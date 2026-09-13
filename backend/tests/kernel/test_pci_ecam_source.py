"""
P3.1 · Source-level invariants for PCI ECAM transport.

vOS·Adaptive·SHA=aeb3736·Phase=P3

These tests pin the SOURCE contract of pci_ecam.c / pci_ecam.h and
the pci.c dispatcher integration. They do not boot QEMU; runtime
verification is the integrity-gate boot log under tests/kernel
fixtures (or future task #50 for full QEMU automation).
"""

from __future__ import annotations

import pathlib
import re

import pytest


@pytest.fixture(scope="module")
def kernel_root():
    return pathlib.Path(__file__).resolve().parent.parent.parent.parent / "kernel"


@pytest.fixture(scope="module")
def ecam_src(kernel_root):
    return (kernel_root / "src" / "arch" / "x86_64" / "pci_ecam.c").read_text()


@pytest.fixture(scope="module")
def ecam_hdr(kernel_root):
    return (kernel_root / "include" / "arch" / "x86_64" / "pci_ecam.h").read_text()


@pytest.fixture(scope="module")
def pci_src(kernel_root):
    return (kernel_root / "src" / "drivers" / "pci.c").read_text()


@pytest.fixture(scope="module")
def kmain_src(kernel_root):
    return (kernel_root / "src" / "boot" / "kmain.c").read_text()


# ---------------------------------------------------------------------------
# Header contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "api",
    [
        "vos3_pci_ecam_init",
        "vos3_pci_ecam_get_status",
        "vos3_pci_ecam_is_available",
        "vos3_pci_ecam_read32",
        "vos3_pci_ecam_write32",
        "vos3_pci_ecam_get_diagnostic",
    ],
)
def test_public_api_declared(ecam_hdr, api):
    assert re.search(rf"\b{api}\s*\(", ecam_hdr)


@pytest.mark.parametrize(
    "name",
    [
        "VOS3_PCI_ECAM_UNINITIALIZED",
        "VOS3_PCI_ECAM_AVAILABLE",
        "VOS3_PCI_ECAM_NO_MCFG",
        "VOS3_PCI_ECAM_MAP_FAILED",
    ],
)
def test_status_enum_present(ecam_hdr, name):
    assert re.search(rf"\b{name}\b", ecam_hdr)


# ---------------------------------------------------------------------------
# ECAM .c invariants
# ---------------------------------------------------------------------------


def test_scans_acpi_table_inventory_for_mcfg(ecam_src):
    """MCFG discovery MUST iterate vos3_acpi_get_info()->table_sigs.
    The string literal 'MCFG' must be the search key (no magic
    other-strings)."""
    assert "vos3_acpi_get_info" in ecam_src
    # Look for the MCFG signature check
    assert re.search(r'"MCFG"', ecam_src)


def test_uses_vos3_pte_mmio_for_mapping(ecam_src):
    """Directive: non-cacheable attributes for HW-register
    synchronization. VOS3_PTE_MMIO = PRESENT | WRITABLE | PCD | NX
    is the kernel's documented MMIO flag set."""
    assert re.search(r"vos3_vmm_map_pages\([^)]*VOS3_PTE_MMIO", ecam_src)


def test_ecam_address_formula_uses_documented_shifts(ecam_src):
    """Intel SDM Vol 3A §11.11.4 / OSDev wiki:
        PA = base + ((bus - start_bus) << 20)
                  | (dev << 15)
                  | (func << 12)
                  | offset
    The shifts MUST match those exactly — a wrong shift would
    address the wrong function/device + access random hardware."""
    # bus shift = 20
    assert re.search(r"\(bus\s*-\s*g_start_bus\)\)\s*<<\s*20", ecam_src)
    # dev shift = 15
    assert re.search(r"dev\)\s*<<\s*15", ecam_src)
    # func shift = 12
    assert re.search(r"func\)\s*<<\s*12", ecam_src)


def test_reads_use_volatile(ecam_src):
    """MMIO reads MUST use volatile pointer to prevent compiler
    reordering. PCD=1 in the PTE prevents CPU caching but the
    compiler can still hoist or eliminate reads without volatile."""
    assert re.search(r"volatile\s+uint32_t\s*\*", ecam_src)


def test_init_is_idempotent_on_terminal_states_only(ecam_src):
    """AVAILABLE + MAP_FAILED are terminal; NO_MCFG / UNINITIALIZED
    are retriable so the post-ACPI re-init can succeed even if the
    first probe (before ACPI parse) returned NO_MCFG.

    Pinned to prevent a future refactor from collapsing all four
    states into a single one-shot latch (would defeat the late-init
    pattern in kmain.c)."""
    # Idempotence check must exclude NO_MCFG / UNINITIALIZED.
    block = re.search(
        r"VOS3_PCI_ECAM_AVAILABLE\s*\|\|[\s\S]{0,80}" r"VOS3_PCI_ECAM_MAP_FAILED",
        ecam_src,
    )
    assert block, (
        "init() must short-circuit on AVAILABLE+MAP_FAILED only — "
        "NO_MCFG must be retriable for the kmain late-init path"
    )


def test_read32_returns_sentinel_on_out_of_range(ecam_src):
    """Out-of-range bus/dev/func/offset must return 0xFFFFFFFF (the
    PCI "no device" sentinel), NOT cause a fault. Verified by
    ecam_va_for returning 0 → read32 returns the sentinel."""
    # The 0xFFFFFFFFU return after va==0 must be present.
    assert re.search(
        r"if\s*\(\s*va\s*==\s*0\s*\)\s*\{\s*return\s+0xFFFFFFFFU",
        ecam_src,
    )


def test_offset_alignment_enforced(ecam_src):
    """4-byte alignment is required for u32 reads. Unaligned offset
    must be rejected (would split across cache lines / fault on
    some hosts)."""
    assert re.search(r"\(offset\s*&\s*0x3U?\)\s*!=\s*0U?", ecam_src)


def test_offset_bound_check_enforced(ecam_src):
    """Per-function config space is 4 KiB. Offset >= 4096 must be
    rejected."""
    assert re.search(r"offset\s*>=\s*4096U?", ecam_src)


# ---------------------------------------------------------------------------
# pci.c dispatcher integration
# ---------------------------------------------------------------------------


def test_pci_dispatcher_prefers_ecam(pci_src):
    """pci_read32 MUST check is_available() and dispatch to ECAM
    first. Order is non-negotiable: if Port-I/O ran unconditionally,
    we'd miss extended config space (offsets > 0xFF)."""
    block = re.search(
        r"vos3_pci_ecam_is_available\(\)[\s\S]{0,100}" r"vos3_pci_ecam_read32",
        pci_src,
    )
    assert block, (
        "pci_read32 must check is_available() then call ecam_read32 "
        "BEFORE virtio_pci_read32 fallback"
    )


def test_pci_dispatcher_falls_back_to_port_io(pci_src):
    """The Port-I/O fallback path via virtio_pci_read32 MUST remain —
    2010-era boards without MCFG depend on it."""
    assert "virtio_pci_read32" in pci_src


def test_pci_bus_scan_triggers_lazy_ecam_init(pci_src):
    """First call to pci_bus_scan must attempt ECAM init lazily.
    Subsequent calls are idempotent for the terminal states."""
    block = re.search(
        r"vos3_pci_bus_scan[\s\S]{0,400}vos3_pci_ecam_init\s*\(\s*\)",
        pci_src,
    )
    assert block


# ---------------------------------------------------------------------------
# kmain late-init ordering
# ---------------------------------------------------------------------------


def test_kmain_late_inits_ecam_after_boot_drivers(kmain_src):
    """Boot order is: boot_drivers_init runs PCI scan BEFORE ACPI
    parse, so the first ECAM probe (lazy in pci_bus_scan) returns
    NO_MCFG. kmain must call vos3_pci_ecam_init AGAIN after
    boot_drivers_init returns (ACPI is now ready), so MCFG can be
    found for any downstream driver needing extended config space."""
    drivers_pos = kmain_src.find("boot_drivers_init(")
    ecam_late_pos = kmain_src.find("vos3_pci_ecam_init(")
    assert drivers_pos >= 0 and ecam_late_pos >= 0
    assert (
        drivers_pos < ecam_late_pos
    ), "vos3_pci_ecam_init late-call must come AFTER boot_drivers_init"


def test_kmain_includes_pci_ecam_header(kmain_src):
    """The kmain compilation unit must include pci_ecam.h — without
    it the late-init call links against an implicit declaration."""
    assert "pci_ecam.h" in kmain_src


# ---------------------------------------------------------------------------
# Engagement integrity markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "kernel/src/arch/x86_64/pci_ecam.c",
        "kernel/include/arch/x86_64/pci_ecam.h",
    ],
)
def test_file_integrity_header_present(kernel_root, path):
    src = (kernel_root.parent / path).read_text()
    assert re.search(
        r"vOS.Adaptive.SHA=[0-9a-f]{7,40}.Phase=P3", src
    ), f"{path}: missing vOS·Adaptive·SHA=<anchor>·Phase=P3 marker"


def test_ecam_documents_kpti_compatibility(ecam_hdr):
    """The header must explain how the ECAM MMIO mapping interacts
    with the Phase-1.2 Kernel-Silence PML4 strip — i.e., it lives in
    the kernel PML4 only, never propagated to user PML4."""
    src = ecam_hdr.lower()
    assert "kpti" in src
    assert "user pml4" in src or "kernel pml4" in src
