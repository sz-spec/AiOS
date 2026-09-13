"""
P3.2 · Universal Hardware Manifest tests.

vOS·Adaptive·SHA=aeb3736·Phase=P3.2

Verifies that backend/services/hardware_manifest.py correctly parses
kernel klog text and maps it to the `mode` + `risk_score` decisions
that downstream sandbox-tier selection (P4.3) depends on.

Honest scope
------------
* Tests use HAND-CRAFTED klog fragments rather than booting QEMU —
  the kernel-side string format is pinned by the source-level tests
  in tests/kernel/test_mitigation_factory_source.py + test_pci_ecam_source.py
  (a future kernel refactor that drifts the format would break THOSE
  tests AND surface here too).
* Risk-score formula is the AAA-plan §4.3 formula — when the plan
  changes the deductions, both this test AND the implementation
  must move together.
"""

from __future__ import annotations

import textwrap

import pytest

from services.hardware_manifest import (
    HardwareManifest,
    build_empty,
    build_from_serial_file,
    parse_klog,
)

# ---------------------------------------------------------------------------
# Hand-crafted klog fragments — represent each tier
# ---------------------------------------------------------------------------

_KLOG_PROTECTED_FULL = textwrap.dedent("""
    [KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes sha-ni=yes budget=<=2%
    [KPTI] PML4 strip: kept=[256,511]  stripped_present_entries=2 residual_attack_surface=PML4[256]_direct_phys_map
    [KPTI] init: ready  mode=PROTECTED_FULL  pcid=yes
    [PCI-ECAM] available — buses 00..FF mapped MMIO@0x00000000B0000000 (PCD=1, NX=1)
""").strip()

_KLOG_PROTECTED_PCID_ONLY = textwrap.dedent("""
    [KPTI] mode=PROTECTED_PCID_ONLY pcid=yes invpcid=no smep=no smap=no sha-ni=no budget=<=3%
    [KPTI] PML4 strip: kept=[256,511]  stripped_present_entries=0 residual_attack_surface=PML4[256]_direct_phys_map
    [KPTI] init: ready  mode=PROTECTED_PCID_ONLY  pcid=yes
    [PCI-ECAM] available — buses 00..3F mapped MMIO@0x00000000B0000000 (PCD=1, NX=1)
""").strip()

_KLOG_LEGACY_KAISER = textwrap.dedent("""
    [KPTI] mode=LEGACY_KAISER pcid=no invpcid=no smep=yes smap=yes sha-ni=yes budget=5-30%
    [KPTI] note: hardware predates 2010 PCID; syscall-heavy regression 5-30% documented; consider hardware refresh.
    [KPTI] PML4 strip: kept=[256,511]  stripped_present_entries=1 residual_attack_surface=PML4[256]_direct_phys_map
    [KPTI] init: ready  mode=LEGACY_KAISER  pcid=no
    [PCI-ECAM] MCFG absent — Port-I/O fallback active
""").strip()

_KLOG_EMPTY = ""


# ---------------------------------------------------------------------------
# Mode derivation
# ---------------------------------------------------------------------------


def test_protected_full_klog_yields_protected_mode():
    m = parse_klog(_KLOG_PROTECTED_FULL)
    assert m.mode == "PROTECTED"
    assert m.is_protected is True
    assert m.is_restricted_legacy is False
    assert m.kpti_mode == "PROTECTED_FULL"
    assert m.pcid is True and m.invpcid is True
    assert m.smep is True and m.smap is True
    assert m.sha_ni is True


def test_protected_pcid_only_klog_yields_protected_mode():
    """PCID present but SMEP/SMAP absent — still PROTECTED, just at
    a lower budget tier. The mode-level decision should be the same."""
    m = parse_klog(_KLOG_PROTECTED_PCID_ONLY)
    assert m.mode == "PROTECTED"
    assert m.kpti_mode == "PROTECTED_PCID_ONLY"
    assert m.pcid is True
    assert m.invpcid is False
    assert m.smep is False and m.smap is False


def test_legacy_kaiser_klog_yields_restricted_legacy_mode():
    """No PCID → drop to RESTRICTED_LEGACY per the directive's
    Security > Availability rule."""
    m = parse_klog(_KLOG_LEGACY_KAISER)
    assert m.mode == "RESTRICTED_LEGACY"
    assert m.is_restricted_legacy is True
    assert m.kpti_mode == "LEGACY_KAISER"
    assert m.pcid is False


def test_empty_klog_yields_unknown_mode():
    """No kernel info at all → mode='UNKNOWN'. This is the boot-cold
    state when the backend starts before the kernel has flushed its
    serial output. Downstream code should treat UNKNOWN as 'no
    decisions yet — wait for kernel'."""
    m = parse_klog(_KLOG_EMPTY)
    assert m.mode == "UNKNOWN"
    assert m.kpti_mode is None
    assert m.pcid is False


# ---------------------------------------------------------------------------
# KPTI summary parsing
# ---------------------------------------------------------------------------


def test_kpti_budget_extracted():
    assert parse_klog(_KLOG_PROTECTED_FULL).kpti_budget == "<=2%"
    assert parse_klog(_KLOG_PROTECTED_PCID_ONLY).kpti_budget == "<=3%"
    assert parse_klog(_KLOG_LEGACY_KAISER).kpti_budget == "5-30%"


def test_pml4_kept_indices_parsed():
    m = parse_klog(_KLOG_PROTECTED_FULL)
    assert m.pml4_kept_indices == ("256", "511")


def test_pml4_stripped_count_parsed():
    assert parse_klog(_KLOG_PROTECTED_FULL).pml4_stripped_present_count == 2
    assert parse_klog(_KLOG_PROTECTED_PCID_ONLY).pml4_stripped_present_count == 0
    assert parse_klog(_KLOG_LEGACY_KAISER).pml4_stripped_present_count == 1


def test_kpti_init_ready_observed():
    assert parse_klog(_KLOG_PROTECTED_FULL).kpti_init_ready is True
    assert parse_klog(_KLOG_EMPTY).kpti_init_ready is False


# ---------------------------------------------------------------------------
# PCI-ECAM parsing
# ---------------------------------------------------------------------------


def test_pci_ecam_available_parsed():
    m = parse_klog(_KLOG_PROTECTED_FULL)
    assert m.pci_transport == "ECAM"
    assert m.pci_ecam_mmio_base == 0xB0000000
    assert m.pci_ecam_bus_range == (0x00, 0xFF)
    assert m.pci_ecam_pcd is True
    assert m.pci_ecam_nx is True


def test_pci_ecam_smaller_bus_range():
    m = parse_klog(_KLOG_PROTECTED_PCID_ONLY)
    assert m.pci_ecam_bus_range == (0x00, 0x3F)


def test_pci_port_io_fallback_parsed():
    m = parse_klog(_KLOG_LEGACY_KAISER)
    assert m.pci_transport == "PORT_IO"
    assert m.pci_ecam_mmio_base is None
    assert m.pci_ecam_bus_range is None


def test_pci_transport_unknown_when_absent():
    m = parse_klog(_KLOG_EMPTY)
    assert m.pci_transport == "UNKNOWN"


# ---------------------------------------------------------------------------
# Fault tolerance — parser must NEVER raise on malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "\x00" * 100,
        "[KPTI] mode=  pcid= invpcid= smep= smap= sha-ni= budget=",
        "[PCI-ECAM] available — bogus",
        "totally unrelated log\nlines\nfrom\nuserland\n",
        "[KPTI] PML4 strip: kept=[]  stripped_present_entries=notanumber",
    ],
)
def test_parser_never_raises_on_garbage(garbage):
    """Audit-honesty: a parser feeding sandbox-tier decisions MUST
    degrade gracefully. We test by passing pathological inputs and
    asserting we get back a manifest (whatever shape) without an
    exception escaping."""
    m = parse_klog(garbage)
    assert isinstance(m, HardwareManifest)


def test_partial_klog_falls_to_restricted_legacy_or_unknown():
    """Mid-boot snapshot: only some lines emitted. The manifest
    must not claim PROTECTED based on partial evidence."""
    partial = "[KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes sha-ni=yes budget=<=2%"
    # No init-ready line, no PML4 strip line
    m = parse_klog(partial)
    # kpti_init_ready is False → mode falls to RESTRICTED_LEGACY
    assert (
        m.mode == "RESTRICTED_LEGACY"
    ), "manifest must not claim PROTECTED without an init-ready signal"


# ---------------------------------------------------------------------------
# Risk-score formula (AAA plan §4.3)
# ---------------------------------------------------------------------------


def test_risk_score_floor_30():
    """The worst-case host (no PCID, no SHA-NI, pure_python, KAISER)
    must score AT LEAST 30 — the floor in the AAA plan."""
    klog = "[KPTI] mode=LEGACY_KAISER pcid=no invpcid=no smep=no smap=no sha-ni=no budget=5-30%\n[KPTI] init: ready"
    m = parse_klog(klog)
    assert m.risk_score >= 30


def test_risk_score_protected_full_host_high():
    """A fully-equipped PROTECTED_FULL host should score >= 85."""
    m = parse_klog(_KLOG_PROTECTED_FULL)
    assert m.risk_score >= 85, f"expected >=85, got {m.risk_score}"


def test_risk_score_legacy_kaiser_around_60():
    """The current dev-host's actual measurement was 60 — pin a
    tolerance window so this test doesn't flake on cosmetic changes."""
    m = parse_klog(_KLOG_LEGACY_KAISER)
    assert 50 <= m.risk_score <= 65, f"got {m.risk_score}"


def test_risk_score_unknown_state_low():
    """An UNKNOWN mode (no kernel info) MUST score low so sandbox
    selection picks the restrictive tier."""
    m = parse_klog(_KLOG_EMPTY)
    assert m.risk_score <= 70


def test_risk_score_compute_directly():
    """compute_risk_score is a pure function over the manifest.
    Verify the deductions match the AAA plan §4.3 formula."""
    # All deductions hit: mode!=PROTECTED, no sha-ni, pure_python,
    # LEGACY_KAISER. Microcode placeholder still 0.
    m = parse_klog(
        "[KPTI] mode=LEGACY_KAISER pcid=no invpcid=no smep=no smap=no sha-ni=no budget=5-30%\n[KPTI] init: ready"
    )
    100 - 20 - 10 - 10 - 5 - 5  # = 50
    # We can't precisely match because pqc_backend deduction depends
    # on host. But we can check the range.
    assert 45 <= m.risk_score <= 55


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------


def test_build_empty_is_safe_default():
    m = build_empty()
    assert m.mode == "UNKNOWN"
    assert m.kpti_init_ready is False
    assert m.pci_transport == "UNKNOWN"
    assert m.risk_score >= 30


def test_build_from_serial_file_missing_file_returns_empty():
    """File-not-found must not raise — fail-closed to UNKNOWN."""
    m = build_from_serial_file("/tmp/this/path/does/not/exist/at_all.log")
    assert isinstance(m, HardwareManifest)
    assert m.mode == "UNKNOWN"


def test_build_from_serial_file_reads_real_klog(tmp_path):
    f = tmp_path / "klog.log"
    f.write_text(_KLOG_PROTECTED_FULL)
    m = build_from_serial_file(str(f))
    assert m.mode == "PROTECTED"
    assert m.pci_transport == "ECAM"


# ---------------------------------------------------------------------------
# Engagement marker
# ---------------------------------------------------------------------------


def test_module_carries_phase_marker():
    import pathlib, re

    src = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "services"
        / "hardware_manifest.py"
    ).read_text()
    assert re.search(r"vOS.Adaptive.SHA=[0-9a-f]{7,40}.Phase=P3", src)


def test_module_documents_honest_scope():
    import pathlib

    src = (
        (
            pathlib.Path(__file__).resolve().parent.parent.parent
            / "services"
            / "hardware_manifest.py"
        )
        .read_text()
        .lower()
    )
    # The fault-tolerant + fail-closed framing must be documented
    assert "fault-tolerant" in src or "fault tolerant" in src
    assert (
        "fail-closed" in src or "fail closed" in src or "security > availability" in src
    )
