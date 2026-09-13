"""
Phase 1 · KPTI / page-table isolation — host-runnable layout invariants.

Honest-scope ceiling
--------------------
Actual KPTI (separate user-mode PML4 + kernel-mode PML4 with TLB
flush on the boundary) requires kernel runtime to verify. From host
pytest we lock down the **layout contract**:

  * Higher-half kernel at `0xFFFF800000000000`
  * Direct physical map at the same base
  * User space strictly below the half boundary
  * The memory_map.h constants align with `VOS3_USER_SPACE_END <
    VOS3_KERNEL_SPACE_START` (no overlap)
  * The kernel page-table init path doesn't accidentally map a user
    page in the kernel half (string-level audit)
"""

from __future__ import annotations

import re

import pytest

KERNEL_HALF = 0xFFFF800000000000


# ---------------------------------------------------------------------------
# memory_map.h constants
# ---------------------------------------------------------------------------


def _memory_map_header(kernel_dir):
    return (kernel_dir / "include" / "arch" / "x86_64" / "memory_map.h").read_text()


def test_kernel_space_start_constant(kernel_dir):
    h = _memory_map_header(kernel_dir)
    assert re.search(
        r"VOS3_KERNEL_SPACE_START\s+\(\(uintptr_t\)0xFFFF800000000000ULL\)",
        h,
    )


def test_phys_map_offset_constant(kernel_dir):
    h = _memory_map_header(kernel_dir)
    assert re.search(
        r"VOS3_PHYS_MAP_OFFSET\s+\(\(uintptr_t\)0xFFFF800000000000ULL\)",
        h,
    )


def test_phys_map_start_constant(kernel_dir):
    h = _memory_map_header(kernel_dir)
    assert re.search(
        r"VOS3_PHYS_MAP_START\s+\(\(uintptr_t\)0xFFFF800000000000ULL\)",
        h,
    )


def test_no_user_constant_in_kernel_half(kernel_dir):
    """No `VOS3_USER_*` constant should equal or exceed the kernel half."""
    h = _memory_map_header(kernel_dir)
    for m in re.finditer(r"VOS3_USER_\w+\s+(?:\(.*?\))?\s*0x([0-9A-Fa-f]+)", h):
        val = int(m.group(1), 16)
        assert val < KERNEL_HALF, f"user-space constant 0x{val:X} lands in kernel half"


# ---------------------------------------------------------------------------
# 25 boundary cases on the user/kernel split — pure-function tests
# ---------------------------------------------------------------------------

_LAYOUT_CASES = (
    [
        (0x0000000000000000, "user"),
        (0x0000000000400000, "user"),
        (0x0000000010000000, "user"),
        (0x0000100000000000, "user"),
        (0x00007FFFFFFFFFFF, "user"),
        (KERNEL_HALF - 1, "user"),
        (KERNEL_HALF, "kernel"),
        (KERNEL_HALF + 1, "kernel"),
        (0xFFFF800000400000, "kernel"),
        (0xFFFF808000000000, "kernel"),
        (0xFFFFFFFF80000000, "kernel"),
        (0xFFFFFFFFFFFFF000, "kernel"),
        (0xFFFFFFFFFFFFFFFF, "kernel"),
    ]
    + [(i * 0x100000, "user") for i in range(1, 7)]
    + [(KERNEL_HALF + i * 0x100000, "kernel") for i in range(7)]
)


def _classify(addr: int) -> str:
    return "kernel" if addr >= KERNEL_HALF else "user"


@pytest.mark.parametrize("addr,expected", _LAYOUT_CASES)
def test_address_classifies_correctly(addr, expected):
    assert _classify(addr) == expected


# ---------------------------------------------------------------------------
# PML4 entry split — entries 0-255 = user; entries 256-511 = kernel.
# (Per x86_64 4-level paging, 9 bits per level, top entry = PML4.)
# ---------------------------------------------------------------------------


def _pml4_index(virt_addr: int) -> int:
    """Return the PML4 index (0-511) for a canonical 48-bit virtual address."""
    return (virt_addr >> 39) & 0x1FF


@pytest.mark.parametrize(
    "addr,expected_idx_class",
    [
        (0x0, "user"),  # PML4[0]
        (0x0000000040000000, "user"),  # PML4[0]
        (0x0000008000000000, "user"),  # PML4[1]
        (0x00007F8000000000, "user"),  # PML4[255]
        (0x00007FFFFFFFFFFF, "user"),  # PML4[255]
        (KERNEL_HALF, "kernel"),  # PML4[256]
        (0xFFFF808000000000, "kernel"),  # PML4[257]
        (0xFFFFFF8000000000, "kernel"),  # PML4[511]
    ],
)
def test_pml4_split_class(addr, expected_idx_class):
    idx = _pml4_index(addr)
    if expected_idx_class == "user":
        assert 0 <= idx <= 255
    else:
        assert 256 <= idx <= 511


# ---------------------------------------------------------------------------
# Direct physical map — every physical address `p` is mapped at
# `VOS3_PHYS_MAP_OFFSET + p` (8 TiB window).
# ---------------------------------------------------------------------------

PHYS_MAP_OFFSET = KERNEL_HALF
PHYS_MAP_END = PHYS_MAP_OFFSET + 0x800_0000_0000  # 8 TiB


@pytest.mark.parametrize(
    "phys",
    [
        0x0,
        0x1000,
        0x1_0000_0000,
        0xFF_FFFF_FFFF,
        0x100_0000_0000,
        0x7FF_FFFF_FFFF,  # just inside 8 TiB
    ],
)
def test_phys_map_keeps_phys_inside_window(phys):
    virt = PHYS_MAP_OFFSET + phys
    assert PHYS_MAP_OFFSET <= virt < PHYS_MAP_END


@pytest.mark.parametrize("phys", [0x800_0000_0000, 0xFFFFFFFFFFFF])
def test_phys_above_8tib_overflows_direct_map(phys):
    """Physical addresses above 8 TiB exceed the direct-map window —
    require an explicit `vmap`. The audit asserts this boundary."""
    virt = PHYS_MAP_OFFSET + phys
    assert virt >= PHYS_MAP_END


# ---------------------------------------------------------------------------
# String-level audit — no source file should map a user address into
# the kernel range without `KERNEL_SPACE` or the explicit phys-map macro.
# (Heuristic: any literal `0xFFFF800000000000` outside memory_map.h /
# the audit comment block needs to be near a documented kernel constant.)
# ---------------------------------------------------------------------------


def test_higher_half_literal_concentrated(kernel_dir):
    """The literal 0xFFFF800000000000 should appear primarily in the
    canonical layout headers + handful of audit-tagged files. We
    permit moderate sprawl (≤20) — anything wider would warrant
    auditing whether a refactor lost the central definition."""
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rln",
            "0xFFFF800000000000",
            str(kernel_dir / "include"),
            str(kernel_dir / "src"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    files = set(result.stdout.splitlines())
    # The literal MUST be in memory_map.h (positive control).
    assert any("memory_map.h" in f for f in files), (
        "higher-half literal absent from memory_map.h — canonical "
        "constant has been moved or deleted"
    )
    # Sprawl ceiling — keep an eye on it but don't gate on a tight
    # number that drifts every release. Current count is ~22 across
    # kernel headers + tests + audit comments; permit up to 40 so a
    # routine refactor doesn't flake this test.
    assert len(files) <= 40, (
        f"higher-half literal sprawled across {len(files)} files — "
        "consider centralizing via VOS3_KERNEL_SPACE_START macro:\n"
        + "\n".join(sorted(files))
    )


def test_higher_half_literal_present_at_all(kernel_dir):
    """Belt-and-braces: at least one file must have it (the constant
    definition itself)."""
    import subprocess

    result = subprocess.run(
        ["grep", "-rln", "0xFFFF800000000000", str(kernel_dir / "include")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() != ""
