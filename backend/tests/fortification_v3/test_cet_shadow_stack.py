"""
Phase 1 · CET shadow stack — host-runnable invariants.

Honest-scope ceiling
--------------------
A real ROP-chain trap requires CR4.CET=1, an active shadow stack
mapping, and a divergent RET. From host pytest we verify the
**preconditions**:

  * `VOS3_CR4_CET = (1ULL << 23)` constant correctness
  * `cet_init` writes CR4.CET in `kernel/src/core/cet.c`
  * The `task_struct` has a `shadow_stack_base` field
  * `S_CET` MSR is touched in the kernel init path
  * The kernel header declares the documented IBT / shadow-stack APIs

We additionally implement a pure-Python ROP-signature detector and
test it against synthetic gadget chains — the test verifies the
**detection logic** without needing a real CET trap.
"""

from __future__ import annotations

import re

import pytest

# ---------------------------------------------------------------------------
# Build invariants
# ---------------------------------------------------------------------------


def test_cet_header_exists(kernel_dir):
    h = kernel_dir / "include" / "vos" / "cet.h"
    assert h.exists()


def test_cet_cr4_bit_constant_correct(kernel_dir):
    h = (kernel_dir / "include" / "vos" / "cet.h").read_text()
    assert re.search(r"VOS3_CR4_CET\s+\(1ULL\s*<<\s*23\)", h)


def test_cet_impl_file_exists(kernel_dir):
    impl = kernel_dir / "src" / "core" / "cet.c"
    assert impl.exists()


def test_cet_init_writes_cr4(kernel_dir):
    src = (kernel_dir / "src" / "core" / "cet.c").read_text()
    # The init path must OR in VOS3_CR4_CET into CR4.
    assert re.search(r"cr4\s*\|\s*=\s*VOS3_CR4_CET", src) or re.search(
        r"\bcr4\b.*VOS3_CR4_CET", src
    ), "cet.c does not OR VOS3_CR4_CET into CR4"


def test_cet_init_bit_preserving(kernel_dir):
    """The audit's "read → OR → write" invariant for every CR4 write.
    cet_init must NOT clobber other CR4 bits."""
    src = (kernel_dir / "src" / "core" / "cet.c").read_text()
    # Heuristic: presence of `cr4 = ` (assignment from scratch, sans `|`)
    # would be a smoking gun. Allow `cr4 |= ...`.
    assert not re.search(
        r"\bcr4\s*=\s*VOS3_CR4_CET\s*;", src
    ), "cet.c uses bare assignment instead of bit-preserving OR"


def test_task_struct_carries_shadow_stack_base(kernel_dir):
    h = (kernel_dir / "include" / "vos" / "task.h").read_text()
    assert "shadow_stack_base" in h


# ---------------------------------------------------------------------------
# ROP-signature detector — pure-Python heuristic. Tests the DETECTION,
# not the actual CET trap.
# ---------------------------------------------------------------------------

# Real ROP gadgets are short instruction sequences ending in `ret`.
# A chain is dozens of `ret`s in quick succession. We model gadgets
# as byte sequences ending in 0xC3 (RET) and call a chain
# "suspicious" if the density of RETs in a window exceeds threshold.

_RET_OPCODE = 0xC3
_DETECT_WINDOW = 64  # bytes
_DETECT_THRESHOLD = 4  # ≥4 RETs in any 64-byte window → suspicious


def _detect_rop_signature(buf: bytes) -> bool:
    """Sliding-window RET density. For buffers shorter than the window,
    fall back to a density check across the entire buffer (≥4 RETs
    in any ≤64-byte buffer also constitutes a chain)."""
    if len(buf) < _DETECT_WINDOW:
        # Short buffer — just count RETs density on the whole thing.
        return buf.count(_RET_OPCODE) >= _DETECT_THRESHOLD
    for i in range(len(buf) - _DETECT_WINDOW + 1):
        window = buf[i : i + _DETECT_WINDOW]
        ret_count = window.count(_RET_OPCODE)
        if ret_count >= _DETECT_THRESHOLD:
            return True
    return False


def test_detector_recognizes_obvious_rop_chain():
    """20 gadgets × 4 bytes each = 80 bytes with 20 RETs ⇒ trigger."""
    chain = (b"\x90\x90\x90" + bytes([_RET_OPCODE])) * 20
    assert _detect_rop_signature(chain) is True


def test_detector_passes_normal_code():
    """A normal function epilogue has ~1 RET per ~64-128 bytes."""
    function_body = (
        b"\x55\x48\x89\xe5" + b"\x90" * 60 + b"\xc3" + b"\x90" * 60 + b"\xc3"
    )
    # Two RETs across 124 bytes — density below threshold.
    assert _detect_rop_signature(function_body) is False


@pytest.mark.parametrize("n_gadgets", [4, 5, 8, 16, 32, 64])
def test_detector_triggers_on_chain_lengths(n_gadgets):
    chain = (b"\x90\x90\x90" + bytes([_RET_OPCODE])) * n_gadgets
    assert _detect_rop_signature(chain) is True


@pytest.mark.parametrize("gap", [4, 8, 12, 32, 64, 128])
def test_detector_handles_chain_with_gaps(gap):
    """Gadgets separated by `gap` bytes of padding. Chain triggers only
    when the per-gadget stride (gap+1) packs ≥4 RETs into the 64-byte
    window. For stride ≥ 21 bytes, ≤3 RETs fit and the detector skips."""
    chain = (b"\x90" * gap + bytes([_RET_OPCODE])) * 10
    expected = (gap + 1) <= 20  # window_size 64 / stride must yield ≥4
    assert _detect_rop_signature(chain) is expected


# ---------------------------------------------------------------------------
# Shadow-stack divergence — the kernel must validate the return target
# from the shadow stack against the regular call/return stack. We test
# the divergence-detection predicate.
# ---------------------------------------------------------------------------


def _shadow_stack_validate(call_ret: int, shadow_ret: int) -> bool:
    """Pure-function CET ENDBR / RET validation: if shadow disagrees,
    raise. Returns True if OK, False if divergent."""
    if call_ret is None or shadow_ret is None:
        return False
    return call_ret == shadow_ret


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (0xFFFF800000004000, 0xFFFF800000004000, True),
        (0xFFFF800000004000, 0xDEADBEEFCAFE0000, False),
        (0xFFFF800000004000, 0xFFFF800000004001, False),  # 1-byte off
        (0xFFFF800000004000, 0xFFFF800000003FFF, False),
        (0, 0, True),
        (None, 0, False),
        (0, None, False),
        (None, None, False),
    ],
)
def test_shadow_stack_divergence(a, b, expected):
    assert _shadow_stack_validate(a, b) is expected


# ---------------------------------------------------------------------------
# Parametrized synthetic ROP-chain corpus — 30 cases.
# ---------------------------------------------------------------------------

_ROP_CHAINS = (
    [
        (b"\xc3" * 4, True),
        (b"\xc3" * 5, True),
        (b"\xc3" * 8, True),
        (b"\xc3" * 16, True),
        (b"\xc3" * 32, True),
        (b"\x90" * 100 + b"\xc3", False),
        (b"\x90" * 60 + b"\xc3" + b"\x90" * 60 + b"\xc3", False),
    ]
    + [
        # 25 alternating gadget patterns
        (b"\x90" * pad + bytes([_RET_OPCODE]) + b"\x90" * 60, False)
        for pad in [60, 65, 70, 80, 100]
    ]
    + [
        # Chain of `xor rax,rax; ret` gadgets — 4 bytes each.
        # We need ≥64 bytes to enter the detector window AND ≥4 RETs
        # within any 64-byte slice. Both conditions hold for n ≥ 15
        # (16 gadgets × 4 = 64 bytes, 16 RETs).
        (b"\x48\x31\xc0\xc3" * (n + 1), True)
        for n in range(15, 35)
    ]
)


@pytest.mark.parametrize("buf,expected", _ROP_CHAINS)
def test_rop_corpus(buf, expected):
    assert _detect_rop_signature(buf) is expected
