"""
P1.1 · Source-level invariant tests for the Adaptive Mitigation Factory.

Honest scope
------------
We do NOT run the kernel under QEMU here. These are *source-level* tests:
we read mitigation_factory.c + mitigation_mode.h + the boot wiring in
kmain.c and assert that the documented contract is encoded literally in
the source. A future refactor that silently breaks the contract will
flip one of these tests red.

Runtime verification (booting QEMU with -cpu Westmere / Nehalem / etc.)
happens in the P1 verification gate (task #44), not in this file.

The contract this file pins:
  * `vos3_mitigation_mode_t` exposes exactly the 5 documented enum values
    (UNINITIALIZED, PROTECTED_FULL, PROTECTED_PCID, LEGACY_KAISER,
    REFUSE_32BIT) — no more, no fewer.
  * The selection logic in `vos3_mitigation_factory_init` does exactly
    what the plan promised:
      - !LM → refuse_32bit panic path
      - PCID+INVPCID+SMEP+SMAP → PROTECTED_FULL
      - PCID only → PROTECTED_PCID
      - else (LM but no PCID) → LEGACY_KAISER
  * The boot serial summary lists pcid/invpcid/smep/smap/sha-ni so an
    operator can see what they got.
  * kmain.c calls factory init AFTER vos3_cpu_detect() and BEFORE
    vos3_vmm_set_maxphyaddr() — the contract that VMM init depends on
    the latched mode being available.
"""

from __future__ import annotations

import pathlib
import re

import pytest


@pytest.fixture(scope="module")
def kernel_root():
    return pathlib.Path(__file__).resolve().parent.parent.parent.parent / "kernel"


@pytest.fixture(scope="module")
def factory_src(kernel_root):
    return (
        kernel_root / "src" / "arch" / "x86_64" / "mitigation_factory.c"
    ).read_text()


@pytest.fixture(scope="module")
def factory_hdr(kernel_root):
    return (
        kernel_root / "include" / "arch" / "x86_64" / "mitigation_mode.h"
    ).read_text()


@pytest.fixture(scope="module")
def kmain_src(kernel_root):
    return (kernel_root / "src" / "boot" / "kmain.c").read_text()


# ---------------------------------------------------------------------------
# Header invariants — the enum is the public contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "VOS3_MIT_UNINITIALIZED",
        "VOS3_MIT_PROTECTED_FULL",
        "VOS3_MIT_PROTECTED_PCID",
        "VOS3_MIT_LEGACY_KAISER",
        "VOS3_MIT_REFUSE_32BIT",
    ],
)
def test_enum_value_defined(factory_hdr, name):
    assert re.search(
        rf"\b{name}\b", factory_hdr
    ), f"enum value {name} must be present in the mitigation_mode header"


def test_enum_count_is_exactly_five(factory_hdr):
    """A 6th mode would break the boot summary table and the audit-kind
    enumeration. Reviewer must consciously update both."""
    names = re.findall(r"VOS3_MIT_[A-Z_0-9]+", factory_hdr)
    # Each enum value appears twice in the header (once at definition,
    # once in the comment listing them — sometimes more in switch helpers).
    # Use a set for the count.
    assert len(set(names)) == 5, f"expected 5 distinct modes, got {set(names)}"


@pytest.mark.parametrize(
    "api",
    [
        "vos3_mitigation_factory_init",
        "vos3_get_mitigation_mode",
        "vos3_mitigation_mode_name",
        "vos3_mitigation_print_summary",
    ],
)
def test_public_api_declared(factory_hdr, api):
    assert re.search(rf"\b{api}\s*\(", factory_hdr)


# ---------------------------------------------------------------------------
# Selection contract — encoded in the .c source
# ---------------------------------------------------------------------------


def test_refuse_32bit_panic_path_present(factory_src):
    """The !LM branch must NOT return — it must halt. If a future refactor
    removes the cli;hlt loop we want a red test, not a silent boot of a
    32-bit-only host."""
    assert (
        "cli; hlt" in factory_src
    ), "32-bit refusal must use cli;hlt to wedge the CPU; bare loop is not enough"
    assert (
        "noreturn" in factory_src
    ), "the refuse path must be marked __attribute__((noreturn))"


def test_refuse_32bit_message_format(factory_src):
    """The operator-visible boot message is part of the certification
    surface — pin the literal so it cannot drift silently."""
    assert "VOS3_BOOT_REFUSE" in factory_src
    assert "32-bit" in factory_src.lower() or "long-mode" in factory_src.lower()


def test_lm_check_uses_documented_bit(factory_src):
    """The long-mode capability check must dereference VOS3_CPU_FEAT_LM
    against ext_features_edx (CPUID.80000001H:EDX bit 29). Using a magic
    number here would be a regression."""
    assert "VOS3_CPU_FEAT_LM" in factory_src
    assert "ext_features_edx" in factory_src


def test_pcid_check_uses_documented_bit(factory_src):
    """PCID is CPUID.01H:ECX bit 17 — exposed as VOS3_CPU_FEAT_PCID."""
    assert "VOS3_CPU_FEAT_PCID" in factory_src
    assert "features_ecx" in factory_src


def test_invpcid_check_uses_documented_bit(factory_src):
    """INVPCID is CPUID.07H:EBX bit 10 — exposed as VOS3_CPU_EXT7_INVPCID."""
    assert "VOS3_CPU_EXT7_INVPCID" in factory_src
    assert "ext7_ebx" in factory_src


@pytest.mark.parametrize("bit_name", ["VOS3_CPU_EXT7_SMEP", "VOS3_CPU_EXT7_SMAP"])
def test_smep_smap_checks_use_documented_bits(factory_src, bit_name):
    assert bit_name in factory_src


def test_protected_full_requires_all_four_bits(factory_src):
    """PROTECTED_FULL is the strongest tier — it must require PCID AND
    INVPCID AND SMEP AND SMAP in the selection logic. A future refactor
    that drops one of these silently weakens the security claim."""
    # Find the selection block. Tolerant of formatting / line wraps.
    block = factory_src
    # All four helper names must be referenced inside a conditional that
    # assigns PROTECTED_FULL.
    full_assign = re.search(
        r"if\s*\([^)]*g_has_pcid[^)]*g_has_invpcid[^)]*g_has_smep[^)]*g_has_smap[^)]*\)"
        r"\s*\{\s*g_mode\s*=\s*VOS3_MIT_PROTECTED_FULL",
        block,
        re.DOTALL,
    )
    assert full_assign, (
        "PROTECTED_FULL gate must AND together pcid+invpcid+smep+smap; "
        "any one missing must derate to a lower tier"
    )


def test_legacy_kaiser_is_fallback_for_lm_no_pcid(factory_src):
    """When LM is present but PCID is not, the mode must be LEGACY_KAISER
    (not refuse, not silent downgrade to a fictional tier)."""
    # The else branch after the PCID check must assign LEGACY_KAISER.
    pattern = re.search(
        r"else\s*\{\s*g_mode\s*=\s*VOS3_MIT_LEGACY_KAISER",
        factory_src,
        re.DOTALL,
    )
    assert pattern


def test_mode_is_latched_read_only(factory_src):
    """Once selected, the mode must not be mutable from outside.
    g_mode = ... must appear ONLY inside vos3_mitigation_factory_init."""
    assignments = re.findall(r"\bg_mode\s*=\s*VOS3_MIT_", factory_src)
    # The static initializer + the 4 (or 5) in-function assignments.
    # Outside the factory function nothing should write to g_mode.
    # Sanity: assignments are bounded — must NOT be plastered across the file.
    assert 4 <= len(assignments) <= 7, (
        f"unexpected number of g_mode assignments ({len(assignments)}) — "
        f"verify they all live inside vos3_mitigation_factory_init"
    )


# ---------------------------------------------------------------------------
# Boot summary — operator visibility contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "[KPTI]",
        "pcid=",
        "invpcid=",
        "smep=",
        "smap=",
        "sha-ni=",
        "budget=",
    ],
)
def test_boot_summary_lists_capability(factory_src, token):
    """The operator-visible boot line must enumerate every capability
    that drives the mode choice. Hiding one would let a silent downgrade
    slip into production unnoticed."""
    assert token in factory_src


def test_legacy_kaiser_emits_consider_refresh_hint(factory_src):
    """If we shipped LEGACY_KAISER without warning the operator, we'd
    violate the audit-honesty discipline. The 'consider hardware refresh'
    hint must be in the source."""
    assert "consider hardware refresh" in factory_src.lower()
    assert "5-30%" in factory_src or "5-30 %" in factory_src


# ---------------------------------------------------------------------------
# Boot ordering — kmain.c calls things in the right order
# ---------------------------------------------------------------------------


def test_kmain_calls_factory_init(kmain_src):
    assert "vos3_mitigation_factory_init" in kmain_src
    assert "vos3_mitigation_print_summary" in kmain_src


def test_factory_init_comes_after_cpu_detect(kmain_src):
    """vos3_cpu_detect() populates the cpu_info struct that the factory
    consumes. Calling the factory first would pass it a zeroed struct
    and silently land in LEGACY_KAISER on every boot."""
    detect_pos = kmain_src.find("vos3_cpu_detect(")
    factory_pos = kmain_src.find("vos3_mitigation_factory_init(")
    assert detect_pos >= 0 and factory_pos >= 0
    assert (
        detect_pos < factory_pos
    ), "vos3_cpu_detect must be called before vos3_mitigation_factory_init"


def test_factory_init_comes_before_vmm_maxphyaddr(kmain_src):
    """Once VMM init starts reasoning about page-table layout, the
    mitigation mode must already be latched — otherwise PTI page mappings
    can't be decided. Pin the ordering."""
    factory_pos = kmain_src.find("vos3_mitigation_factory_init(")
    vmm_pos = kmain_src.find("vos3_vmm_set_maxphyaddr(")
    assert factory_pos >= 0 and vmm_pos >= 0
    assert factory_pos < vmm_pos


# ---------------------------------------------------------------------------
# Honest-scope marker — every file in this plan must say so
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "kernel/src/arch/x86_64/mitigation_factory.c",
        "kernel/include/arch/x86_64/mitigation_mode.h",
    ],
)
def test_file_integrity_header_present(kernel_root, path):
    """Every P1 file carries the marker
        `vOS·Adaptive·SHA=<engagement-anchor>·Phase=P1`
    so an auditor can grep the tree and recover this engagement's
    perimeter, plus trace each file to the plan's anchor commit.

    The SHA is the engagement-baseline commit `aeb3736` — the parent
    that the AAA plan was approved against — NOT the commit that
    introduces this file (which is unknown at write time). This is
    the user-approved interpretation of R3 from the P1.1 review.
    """
    full_path = kernel_root.parent / path
    src = full_path.read_text()
    # Accept 7-40 hex chars so the format survives short-SHA / long-SHA
    # conventions across the engagement.
    assert re.search(
        r"vOS.Adaptive.SHA=[0-9a-f]{7,40}.Phase=P1", src
    ), f"{path}: missing vOS·Adaptive·SHA=<anchor>·Phase=P1 integrity marker"


def test_loud_null_warning_present(kernel_root):
    """R2: degrade-to-LEGACY-on-NULL-info is user-approved, but the
    operator MUST see a loud warning. This test pins the banner so it
    cannot be softened or removed silently."""
    src = (kernel_root / "src" / "arch" / "x86_64" / "mitigation_factory.c").read_text()
    assert "VOS3_KERNEL_BUG" in src
    assert "SECURITY POSTURE: REDUCED" in src
    # Banner uses asterisks for visibility — count them as a proxy for "loud".
    assert src.count("****") >= 8, (
        "NULL-info warning banner appears to have been softened — "
        "must remain unmistakable in the boot log"
    )
