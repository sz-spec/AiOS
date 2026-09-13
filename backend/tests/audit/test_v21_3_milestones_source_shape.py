# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Source-shape tests for v21.3 milestones M1 / M2 / M3.

Per the Blind-Implementation Protocol
(docs/plans/V21_3_TOTAL_SUPREMACY_PLAN.md §0), each HW activation lands
behind a default-OFF compile flag. These tests verify the SHAPE of the
landed code without flipping a flag — they pass on a stock recovery
checkout with no QEMU and no real hardware.

A source-shape test asserts:
  - Public symbols exist with the expected gating discipline.
  - Compile flags are wired up in the Makefile.
  - The activation path follows the documented contract.

It does NOT execute the code. The runnable validation gate is M5/M6.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KERNEL = REPO / "kernel"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ---------------------------------------------------------------------------
# M1 — XSAVE: boot init + per-task wrappers + scheduler wedge
# ---------------------------------------------------------------------------


def test_m1_xsave_header_declares_boot_init():
    src = _read("kernel/include/vos/xsave.h")
    assert "int vos3_xsave_boot_init(void);" in src
    assert "vos3_xsave_save_for_task" in src
    assert "vos3_xsave_restore_for_task" in src


def test_m1_xsave_boot_init_gated_by_hw_flag():
    """The boot-init body must be inside #ifdef VOS3_HW_XSAVE so default
    builds compile to a no-op returning -ENODEV."""
    src = _read("kernel/src/arch/x86_64/xsave.c")
    # Function must exist
    assert re.search(
        r"^int\s+vos3_xsave_boot_init\s*\(", src, re.M
    ), "vos3_xsave_boot_init definition missing"
    # The body must reference VOS3_HW_XSAVE
    body_start = src.index("int vos3_xsave_boot_init")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    assert "VOS3_HW_XSAVE" in body, "boot_init must be gated by VOS3_HW_XSAVE"


def test_m1_xsave_per_task_wrappers_exist_and_are_gated():
    src = _read("kernel/src/sched/xsave_ctx.c")
    assert "vos3_xsave_save_for_task" in src
    assert "vos3_xsave_restore_for_task" in src
    # Both must be gated
    for fn in ("vos3_xsave_save_for_task", "vos3_xsave_restore_for_task"):
        idx = src.index(fn + "(")
        # Look forward up to function close for VOS3_HW_XSAVE
        chunk = src[idx : idx + 600]
        assert "VOS3_HW_XSAVE" in chunk, f"{fn} must be gated by VOS3_HW_XSAVE"


def test_m1_scheduler_wraps_context_switch_with_xsave():
    src = _read("kernel/src/sched/scheduler.c")
    # The wedge must be gated and call the per-task wrappers
    assert "VOS3_HW_XSAVE" in src
    assert "vos3_xsave_save_for_task(prev)" in src
    assert "vos3_xsave_restore_for_task(next)" in src


def test_m1_makefile_gate_implies_xsave_live():
    src = _read("kernel/Makefile")
    assert re.search(r"VOS3_HW_XSAVE.*1", src)
    # The flag must imply VOS3_XSAVE_LIVE so the asm path in xsave_ctx.c
    # actually emits xsave64/xrstor64.
    m = re.search(r"ifeq\s*\(\$\(VOS3_HW_XSAVE\),1\)\s*\n([^\n]*)", src)
    assert m is not None
    assert "VOS3_XSAVE_LIVE" in m.group(1)


# ---------------------------------------------------------------------------
# M2 — LAPIC: MMIO mapping + LVT init + real ICR write
# ---------------------------------------------------------------------------


def test_m2_apic_init_mmio_uses_acpi_madt_address():
    src = _read("kernel/src/arch/x86_64/apic.c")
    body_start = src.index("int vos3_apic_init_mmio(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    assert "VOS3_HW_LAPIC" in body
    assert "lapic_addr" in body, "init_mmio must read lapic_addr from ACPI info"
    assert "vos3_vmm_map_pages" in body
    assert "VOS3_PTE_MMIO" in body


def test_m2_apic_init_lvt_writes_spiv_first():
    src = _read("kernel/src/arch/x86_64/apic.c")
    body_start = src.index("int vos3_apic_init_lvt(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    spiv_idx = body.find("VOS3_APIC_REG_SPIV")
    lvt_idx = body.find("VOS3_APIC_REG_LVT_TIMER")
    assert spiv_idx >= 0 and lvt_idx >= 0
    assert (
        spiv_idx < lvt_idx
    ), "SPIV must be written BEFORE LVT entries (Intel SDM §10.9)"


def test_m2_send_resched_ipi_already_has_real_body():
    """Pre-existing scaffold: the function body is correct, gated only
    by g_apic_base_va == NULL. Adding M2's init_mmio populates the
    pointer; no further change to send_resched_ipi() is needed."""
    src = _read("kernel/src/arch/x86_64/apic.c")
    assert "VOS3_APIC_REG_ICR_LOW" in src
    assert "VOS3_APIC_ICR_DELIV_PEND" in src
    # Drains pending IPI before firing
    assert "while (spin--" in src or "while(spin--" in src


def test_m2_makefile_lapic_flag_wired():
    src = _read("kernel/Makefile")
    assert re.search(r"ifeq\s*\(\$\(VOS3_HW_LAPIC\),1\)", src)
    assert "-DVOS3_HW_LAPIC" in src


# ---------------------------------------------------------------------------
# M3 — IOAPIC: redirection table programming
# ---------------------------------------------------------------------------


def test_m3_ioapic_header_exists():
    p = REPO / "kernel/include/vos/ioapic.h"
    assert p.is_file(), "ioapic.h missing"
    src = p.read_text()
    for sym in (
        "vos3_ioapic_init",
        "vos3_ioapic_program",
        "vos3_ioapic_set_mask",
        "vos3_ioapic_max_redir",
        "vos3_ioapic_self_test",
    ):
        assert sym in src, f"{sym} missing from ioapic.h"


def test_m3_ioapic_c_exists_and_programs_high_then_low():
    p = REPO / "kernel/src/arch/x86_64/ioapic.c"
    assert p.is_file(), "ioapic.c missing"
    src = p.read_text()
    body_start = src.index("int vos3_ioapic_program(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    high_idx = body.find("(uint8_t)(irq * 2u + 1u)")
    low_idx = body.find("(uint8_t)(irq * 2u),")
    assert high_idx >= 0 and low_idx >= 0
    assert high_idx < low_idx, (
        "Must write high dword BEFORE low dword to avoid in-flight "
        "interrupts seeing a half-programmed entry"
    )


def test_m3_ioapic_init_uses_acpi_first_ioapic():
    src = _read("kernel/src/arch/x86_64/ioapic.c")
    body_start = src.index("int vos3_ioapic_init(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    assert "VOS3_HW_IOAPIC" in body
    assert "vos3_acpi_get_info" in body
    assert "ioapics[0]" in body


def test_m3_ioapic_in_makefile_objects():
    src = _read("kernel/Makefile")
    assert "$(SRC_DIR)/arch/x86_64/ioapic.c" in src


def test_m3_makefile_ioapic_flag_wired():
    src = _read("kernel/Makefile")
    assert re.search(r"ifeq\s*\(\$\(VOS3_HW_IOAPIC\),1\)", src)
    assert "-DVOS3_HW_IOAPIC" in src


# ---------------------------------------------------------------------------
# Cross-cutting — Blind-Implementation Protocol invariants
# ---------------------------------------------------------------------------


def test_default_build_does_not_define_any_hw_flag():
    """Stock 'make VOS3_BUILD_TYPE=PRO' must not enable any HW activation
    flag. The flags require explicit env-var opt-in."""
    src = _read("kernel/Makefile")
    # No top-level 'CFLAGS += -DVOS3_HW_*' that fires unconditionally.
    for flag in ("VOS3_HW_XSAVE", "VOS3_HW_LAPIC", "VOS3_HW_IOAPIC"):
        # Grep for unconditional CFLAGS append
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                stripped.startswith("CFLAGS")
                and f"-D{flag}" in stripped
                and not _is_inside_ifeq(src, line, flag)
            ):
                raise AssertionError(
                    f"{flag} is enabled unconditionally — must be inside "
                    f"ifeq(VOS3_HW_*) guard"
                )


def _is_inside_ifeq(src: str, line: str, flag: str) -> bool:
    """Return True iff `line` appears inside an `ifeq ($(<flag>),1) ...
    endif` block in `src`."""
    idx = src.find(line)
    if idx < 0:
        return False
    before = src[:idx]
    open_ifeq = before.rfind(f"ifeq ($({flag}),1)")
    if open_ifeq < 0:
        return False
    closing_endif = before.rfind("endif")
    return open_ifeq > closing_endif
