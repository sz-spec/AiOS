"""
Phase 2 · VBus sequence-counter replay.

The kernel-side VBus driver maintains a per-slot 64-bit monotonic
sequence counter. A frame with a sequence number ≤ the last-seen value
must be dropped (replay defence). The chained-MAC TOKEN_STREAM design
additionally rolls the HMAC over the previous chain mac, so an
out-of-order frame both fails the seq check AND fails the chain MAC.

These tests model the **replay-defence state machine** in pure Python
and exercise the contract that kernel code must implement. The model
is documented in `kernel/src/drivers/virtio_vbus.c` (audit ref).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod

import pytest


class _ReplayGuard:
    """Pure-Python model of the kernel's per-slot replay guard."""

    def __init__(self):
        self._last_seen: dict[int, int] = {}

    def accept(self, slot: int, seq: int) -> bool:
        last = self._last_seen.get(slot, -1)
        if seq <= last:
            return False
        self._last_seen[slot] = seq
        return True

    def reset_slot(self, slot: int) -> None:
        """Slot reset must clear the last-seen counter — but the
        contract says ownership is also cleared (per CLAUDE.md). For
        replay defence specifically: a reset means a fresh session,
        seq starts from 0 again."""
        self._last_seen.pop(slot, None)


# ---------------------------------------------------------------------------
# Fresh slot accepts seq 0+, then monotone
# ---------------------------------------------------------------------------


def test_fresh_slot_accepts_seq_0():
    g = _ReplayGuard()
    assert g.accept(0, 0) is True


def test_fresh_slot_accepts_seq_1_to_9_in_order():
    g = _ReplayGuard()
    for i in range(10):
        assert g.accept(0, i) is True


# ---------------------------------------------------------------------------
# Duplicate seq — second submission of same value must reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seq", [0, 1, 5, 100, 1_000_000, (1 << 32), (1 << 63) - 1])
def test_duplicate_seq_rejects(seq):
    g = _ReplayGuard()
    assert g.accept(0, seq) is True
    assert g.accept(0, seq) is False


# ---------------------------------------------------------------------------
# Out-of-order — seq < last must reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "last,older",
    [
        (10, 0),
        (10, 5),
        (10, 9),
        (100, 50),
        (1000, 999),
        (1 << 20, 1 << 19),
        (1 << 32, 1 << 31),
        (1 << 50, 1),
        (1 << 60, 0),
    ],
)
def test_out_of_order_rejects(last, older):
    g = _ReplayGuard()
    g.accept(0, last)
    assert g.accept(0, older) is False


# ---------------------------------------------------------------------------
# Cross-slot independence — slot A's counter must not affect slot B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slot_a,slot_b",
    [
        (0, 1),
        (0, 7),
        (1, 255),
        (0, 0xFE),
    ],
)
def test_slot_isolation(slot_a, slot_b):
    g = _ReplayGuard()
    g.accept(slot_a, 100)
    # Slot B's seq 0 must succeed regardless of slot A's state.
    assert g.accept(slot_b, 0) is True


# ---------------------------------------------------------------------------
# Reset semantics — after reset, seq 0 must succeed again
# ---------------------------------------------------------------------------


def test_reset_clears_replay_state():
    g = _ReplayGuard()
    g.accept(0, 100)
    assert g.accept(0, 50) is False
    g.reset_slot(0)
    assert g.accept(0, 0) is True


# ---------------------------------------------------------------------------
# Spec scenario — replay 50 valid historical frames out of order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("replay_offset", list(range(1, 51)))
def test_replay_historical_frame_rejects(replay_offset):
    """Spec: 'Replay 50 valid historical TOKEN_STREAM frames with
    duplicate or out-of-order 64-bit sequence counters.'"""
    g = _ReplayGuard()
    # Advance the counter to N.
    n = 100
    for i in range(n):
        g.accept(0, i)
    # Now replay a frame at (n - replay_offset).
    assert g.accept(0, n - 1 - replay_offset) is False


# ---------------------------------------------------------------------------
# Chained-MAC simulation — a TOKEN_STREAM frame's MAC is
#   chain_mac[i] = HMAC(key, payload[i] || chain_mac[i-1])
# Replay an out-of-order frame and verify the chain MAC fails.
# ---------------------------------------------------------------------------


def _chain_mac(key: bytes, payloads: list[bytes], iv: bytes) -> bytes:
    state = iv
    for p in payloads:
        state = hmac_mod.new(key, p + state, hashlib.sha256).digest()
    return state


def test_chain_mac_breaks_on_reorder():
    key = b"x" * 32
    iv = b"\x00" * 32
    payloads = [b"alpha", b"beta", b"gamma", b"delta"]
    correct = _chain_mac(key, payloads, iv)
    # Swap any two adjacent frames — chain mac must change.
    reordered = [b"alpha", b"gamma", b"beta", b"delta"]
    different = _chain_mac(key, reordered, iv)
    assert correct != different


@pytest.mark.parametrize(
    "swap_a,swap_b",
    [
        (0, 1),
        (1, 2),
        (2, 3),
        (0, 3),
        (0, 2),
    ],
)
def test_chain_mac_breaks_on_arbitrary_reorder(swap_a, swap_b):
    key = b"x" * 32
    iv = b"\x00" * 32
    payloads = [b"alpha", b"beta", b"gamma", b"delta"]
    correct = _chain_mac(key, payloads, iv)
    reorder = list(payloads)
    reorder[swap_a], reorder[swap_b] = reorder[swap_b], reorder[swap_a]
    if reorder == payloads:
        pytest.skip("swap is a no-op")
    different = _chain_mac(key, reorder, iv)
    assert correct != different


# ---------------------------------------------------------------------------
# 64-bit wraparound — seq must never wrap (kernel must abort the slot).
# ---------------------------------------------------------------------------


def test_seq_at_u64_max():
    g = _ReplayGuard()
    g.accept(0, (1 << 64) - 1)
    # Any subsequent seq <= u64_max must reject.
    assert g.accept(0, (1 << 64) - 1) is False
    assert g.accept(0, 0) is False
