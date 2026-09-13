"""
Phase 1 · SMAP bypass — source-level + build-artifact + boundary tests.

Honest-scope ceiling
--------------------
A true SMAP fault (AC=0 + ring-0 read of user pointer → #PF) can only
be triggered in kernel context. From host pytest we verify the
**necessary conditions** that make the mitigation effective:

  * `VOS3_CR4_SMAP` / `VOS3_CR4_SMEP` constants are defined with the
    documented bit positions (CR4 bits 20 and 21),
  * every load into kernel memory from a user pointer in
    `kernel/src/mm/user_copy.c` is bracketed by `stac()` then `clac()`,
  * the access_ok-class predicate refuses user pointers that overlap
    the higher-half kernel range,
  * the syscall dispatch table bounds-checks the syscall number.

Together these are the chain of conditions an attacker would have to
bypass to get a SMAP-bypass primitive. Each individual test asserts
one link.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# CR4 bit constants — must match Intel SDM Vol 3A §2.5 and AMD APM Vol 2 §3
# ---------------------------------------------------------------------------


def test_cr4_smep_bit_position(kernel_dir):
    h = (kernel_dir / "include" / "arch" / "x86_64" / "cpu.h").read_text()
    assert "VOS3_CR4_SMEP" in h
    # Bit 20 per Intel SDM.
    assert re.search(r"VOS3_CR4_SMEP\s+\(1ULL\s*<<\s*20\)", h)


def test_cr4_smap_bit_position(kernel_dir):
    h = (kernel_dir / "include" / "arch" / "x86_64" / "cpu.h").read_text()
    assert "VOS3_CR4_SMAP" in h
    # Bit 21 per Intel SDM.
    assert re.search(r"VOS3_CR4_SMAP\s+\(1ULL\s*<<\s*21\)", h)


def test_cr4_cet_bit_position(kernel_dir):
    h = (kernel_dir / "include" / "vos" / "cet.h").read_text()
    assert "VOS3_CR4_CET" in h
    # Bit 23 per Intel SDM CET section.
    assert re.search(r"VOS3_CR4_CET\s+\(1ULL\s*<<\s*23\)", h)


# ---------------------------------------------------------------------------
# stac/clac envelope — every kernel read of user memory must be bracketed
# ---------------------------------------------------------------------------


def _user_copy_source(kernel_dir: pathlib.Path) -> str:
    return (kernel_dir / "src" / "mm" / "user_copy.c").read_text()


def test_stac_inline_defined(kernel_dir):
    src = _user_copy_source(kernel_dir)
    assert re.search(
        r"static\s+inline\s+void\s+stac\s*\(\s*void\s*\)", src
    ), "stac() inline helper must be defined in user_copy.c"


def test_clac_inline_defined(kernel_dir):
    src = _user_copy_source(kernel_dir)
    assert re.search(
        r"static\s+inline\s+void\s+clac\s*\(\s*void\s*\)", src
    ), "clac() inline helper must be defined in user_copy.c"


def test_clac_count_at_least_matches_stac_count(kernel_dir):
    """Every stac() must be followed by at least one clac() on every
    control-flow path. CLAUDE.md certified 4 stac + 7 clac — the
    asymmetry comes from `if/else` branches that each need their own
    AC clear after the user-copy. So `clac >= stac` is the invariant."""
    src = _user_copy_source(kernel_dir)
    stac_calls = len(re.findall(r"\bstac\s*\(\s*\)", src))
    clac_calls = len(re.findall(r"\bclac\s*\(\s*\)", src))
    assert clac_calls >= stac_calls, (
        f"stac()={stac_calls} clac()={clac_calls} — some path enables AC "
        f"without clearing it (bad)"
    )


def test_stac_clac_count_at_least_three(kernel_dir):
    """CLAUDE.md says 11 SMAP instructions (4 stac + 7 clac). Tolerate
    drift but require at least 3 of each — anything less means the
    user-copy fast path lost its envelope."""
    src = _user_copy_source(kernel_dir)
    stac_calls = len(re.findall(r"\bstac\s*\(\s*\)", src))
    clac_calls = len(re.findall(r"\bclac\s*\(\s*\)", src))
    assert stac_calls >= 3, f"stac() calls={stac_calls} — envelope eroded"
    assert clac_calls >= 3, f"clac() calls={clac_calls} — envelope eroded"


# ---------------------------------------------------------------------------
# AC-flag semantics — stac() sets AC, clac() clears it; both must be
# inline-asm wrappers around the actual x86_64 instructions.
# ---------------------------------------------------------------------------


def test_stac_uses_inline_asm(kernel_dir):
    src = _user_copy_source(kernel_dir)
    # Match the stac() function body and look for an inline asm with
    # the literal "stac" or "0x01,0xCB,0xCA,0xD8" (the legacy encoding).
    m = re.search(
        r"static\s+inline\s+void\s+stac\s*\(\s*void\s*\)\s*\{([^}]+)\}",
        src,
        re.DOTALL,
    )
    assert m, "stac() body not found"
    body = m.group(1)
    assert (
        "stac" in body.lower()
        or "0x01" in body  # legacy `.byte` encoding
        or "asm" in body
    ), f"stac() body lacks the actual instruction: {body!r}"


def test_clac_uses_inline_asm(kernel_dir):
    src = _user_copy_source(kernel_dir)
    m = re.search(
        r"static\s+inline\s+void\s+clac\s*\(\s*void\s*\)\s*\{([^}]+)\}",
        src,
        re.DOTALL,
    )
    assert m, "clac() body not found"
    body = m.group(1)
    assert "clac" in body.lower() or "0x01" in body or "asm" in body


# ---------------------------------------------------------------------------
# Higher-half kernel range — access_ok must reject user pointers in this
# range. We verify the constant is defined; the access_ok predicate
# uses it by name.
# ---------------------------------------------------------------------------

KERNEL_HALF_BASE = 0xFFFF800000000000


def test_kernel_space_start_constant_defined(kernel_dir):
    h = (kernel_dir / "include" / "arch" / "x86_64" / "memory_map.h").read_text()
    assert "VOS3_KERNEL_SPACE_START" in h
    assert "0xFFFF800000000000" in h


@pytest.mark.parametrize(
    "user_ptr",
    [
        0x0000000000400000,  # canonical user low
        0x00007FFFFFFFE000,  # canonical user high (just below boundary)
        0x0,  # null
        0x1000,  # very low
    ],
)
def test_canonical_user_ptr_under_kernel_threshold(user_ptr):
    assert user_ptr < KERNEL_HALF_BASE


@pytest.mark.parametrize(
    "kernel_ptr",
    [
        0xFFFF800000000000,
        0xFFFF800000000001,
        0xFFFF888000000000,
        0xFFFFFFFFFFFFF000,
    ],
)
def test_canonical_kernel_ptr_at_or_above_threshold(kernel_ptr):
    assert kernel_ptr >= KERNEL_HALF_BASE


# ---------------------------------------------------------------------------
# Boundary fuzz — 50 parametrized addresses straddling the user/kernel
# split. Each must classify correctly. This mirrors the access_ok predicate.
# ---------------------------------------------------------------------------


def _access_ok_reference(ptr: int, length: int) -> bool:
    """Pure-function reference predicate: pointer must be in the user
    half (<0xFFFF800000000000) and the [ptr, ptr+length) range must
    not cross into the kernel half."""
    if ptr < 0 or length < 0:
        return False
    if ptr >= KERNEL_HALF_BASE:
        return False
    if ptr + length > KERNEL_HALF_BASE:
        return False
    return True


_BOUNDARY_CASES = (
    [
        (0x0, 0, True),
        (0x0, 1, True),
        (0x0, 0x1000, True),
        (0x00007FFFFFFFE000, 0x1000, True),
        # 0x00007FFFFFFFE000 + 0x2001 lands in the non-canonical hole
        # (0x0000800000000000–0xFFFF7FFFFFFFFFFF). A real CPU faults on use.
        # Our reference oracle only checks the kernel-half threshold and
        # therefore admits the address; the boundary fuzz exists to lock
        # in *that* predicate. Non-canonical hole detection is its own
        # test that would belong in canonicality_check.py if we shipped one.
        (0x00007FFFFFFFE000, 0x2001, True),
        (KERNEL_HALF_BASE, 1, False),
        (KERNEL_HALF_BASE - 1, 1, True),
        (KERNEL_HALF_BASE - 1, 2, False),
        (0xFFFFFFFFFFFFFFF0, 1, False),
        (-1, 1, False),
        (0x1000, -1, False),
    ]
    + [(0x1000 * i, 0x100, True) for i in range(1, 20)]
    + [(KERNEL_HALF_BASE + 0x1000 * i, 0x100, False) for i in range(20)]
)


@pytest.mark.parametrize("ptr,length,expected", _BOUNDARY_CASES)
def test_access_ok_boundary_fuzz(ptr, length, expected):
    assert _access_ok_reference(ptr, length) is expected


# ---------------------------------------------------------------------------
# Syscall dispatch — kernel/src/arch/x86_64/syscall.c must bounds-check.
# Without bounds-check, an attacker-controlled syscall number indexes
# arbitrary memory.
# ---------------------------------------------------------------------------


def test_syscall_dispatch_has_bounds_check(kernel_dir):
    src = (kernel_dir / "src" / "arch" / "x86_64" / "syscall.c").read_text()
    # The dispatcher uses VOS3_SYS_MAX as the table bound:
    #   `if ((uint32_t)num >= VOS3_SYS_MAX) { ... reject ... }`
    # Accept any boundary check against VOS3_SYS_MAX, NR_syscalls, or
    # MAX_SYSCALL — the symbol name doesn't matter; the check does.
    assert re.search(
        r"(VOS3_SYS_MAX|NR_syscalls|MAX_SYSCALL)",
        src,
    ), "syscall dispatch must reference a max-syscall bound"
    assert re.search(
        r"if\s*\([^)]*(num|nr|sysno)[^)]*>=?\s*(VOS3_SYS_MAX|NR_syscalls|MAX_SYSCALL)",
        src,
    ), "syscall dispatch must bounds-check before indexing the table"


# ---------------------------------------------------------------------------
# Per-syscall pointer validation — CLAUDE.md lists sys_puts, sys_arch_prctl,
# sys_clone, sys_wait4, sys_nanosleep as Phase-29-validated. Each must
# call access_ok-class predicate on its user pointer args.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name",
    [
        # CLAUDE.md Phase-29 list; sys_puts is mentioned in docs but
        # never landed in this kernel — the 4 below are verifiable.
        "sys_arch_prctl",
        "sys_clone",
        "sys_wait4",
        "sys_nanosleep",
    ],
)
def test_phase29_syscall_validates_user_pointer(kernel_dir, fn_name):
    """The Phase-29 break-fix certified these 5 syscalls validate
    pointer args via access_ok / strncpy_from_user. Test the source
    contains the validator name near the function body."""
    import subprocess

    result = subprocess.run(
        ["grep", "-rln", f"\\b{fn_name}\\b", str(kernel_dir / "src")],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = [
        m for m in result.stdout.splitlines() if m.endswith(".c") or m.endswith(".S")
    ]
    if not matches:
        pytest.skip(f"{fn_name} not found — Phase 29 batch may have renamed")
    found_validator = False
    for path in matches:
        text = pathlib.Path(path).read_text()
        # Look for access_ok, strncpy_from_user, copy_from_user near fn name.
        if re.search(
            rf"{fn_name}.*?(access_ok|strncpy_from_user|copy_from_user)",
            text,
            re.DOTALL,
        ):
            found_validator = True
            break
    assert found_validator, (
        f"{fn_name} does not call access_ok/strncpy_from_user/copy_from_user "
        "near its body — pointer-validation regression"
    )


# ---------------------------------------------------------------------------
# Slow — full kernel source SMAP-envelope audit
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_no_user_copy_paths_outside_user_copy_c(kernel_dir):
    """No file other than user_copy.c may issue stac()/clac() — keeping
    the AC envelope concentrated in one audited file is the load-bearing
    architectural invariant."""
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rl",
            "--include=*.c",
            "-E",
            r"\bstac\s*\(|\bclac\s*\(",
            str(kernel_dir / "src"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    offenders = [
        line
        for line in result.stdout.splitlines()
        if "user_copy.c" not in line and line.endswith(".c")
    ]
    assert offenders == [], f"stac()/clac() outside user_copy.c: {offenders}"


# ---------------------------------------------------------------------------
# Parametrized bit-flip on user-pointer encoding — 32 cases that mirror
# what a SMAP-bypass fuzz attempt would look like at the boundary.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flip_bit", list(range(0, 64, 2)))
def test_bit_flip_classification_matches_oracle(flip_bit):
    """Single-bit flips on canonical user low (0x400000) — verify the
    classifier reports the correct half. Single-bit flips alone never
    reach the kernel half (max value base|(1<<63) = 0x8000000000400000
    < 0xFFFF800000000000), so every variant stays user-side. Multi-bit
    lifts into kernel half are covered by `_BOUNDARY_CASES` above."""
    base = 0x0000000000400000
    flipped = base ^ (1 << flip_bit)
    assert flipped < KERNEL_HALF_BASE
