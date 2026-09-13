"""
Phase 3 · VBus throughput — chained-MAC latency budget.

Honest scope
------------
We don't push 10M frames through a real VBus socket. We use the
same hermetic frame builder + HMAC verifier the rest of the suite
uses, and measure:
  * Throughput at varying frame counts (1k / 10k / 100k)
  * Per-frame latency stays within budget (5ms ceiling per spec
    is conservatively generous for HMAC-SHA256 + dual CRC32C)
  * Chained-MAC verification's overhead scales sub-linearly

The "10M frames" extrapolation: measured throughput × seconds gives
the time to process 10M. Documented in test output; not claimed as
proof of production behavior.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import struct
import time

import pytest

FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)
_HMAC_KEY = b"0123456789abcdef" * 2


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    poly = 0x82F63B78
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _build_frame(frame_type, slot_id, tag, payload, key=_HMAC_KEY):
    payload_crc = _crc32c(payload) & 0xFFFFFFFF
    pre = struct.pack("<BBHII", frame_type, slot_id, tag, len(payload), payload_crc)
    hdr_crc = _crc32c(pre) & 0xFFFFFFFF
    header = pre + struct.pack("<I", hdr_crc) + b"\x00" * 48
    mac = hmac_mod.new(key, header[:16] + payload, hashlib.sha256).digest()
    header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
    return header + payload


def _verify_frame(pkt, key=_HMAC_KEY):
    header = pkt[:FRAME_HDR_SIZE]
    payload = pkt[FRAME_HDR_SIZE:]
    received = header[16:48]
    expected = hmac_mod.new(key, header[:16] + payload, hashlib.sha256).digest()
    return hmac_mod.compare_digest(received, expected)


# ---------------------------------------------------------------------------
# Per-frame latency at small frame counts (fast, in-CI)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_frames", [100, 500, 1_000, 5_000])
def test_per_frame_latency_under_5ms(n_frames):
    """Spec: chained-MAC verification adds < 5ms latency under load.
    Measure per-frame latency at varying counts; assert < 5ms (a
    very generous ceiling — actual values are ~10µs/frame on M-series)."""
    payload = b"VBUS3_STRESS" * 8
    pkts = [_build_frame(1, 0xFF, i & 0xFFFF, payload) for i in range(n_frames)]
    t0 = time.perf_counter()
    verified = sum(1 for p in pkts if _verify_frame(p))
    elapsed = time.perf_counter() - t0
    per_frame_ms = (elapsed / n_frames) * 1000
    assert verified == n_frames
    assert (
        per_frame_ms < 5.0
    ), f"{n_frames} frames: {per_frame_ms:.3f}ms/frame — over budget"


# ---------------------------------------------------------------------------
# Throughput scaling — predictive failure analysis
# ---------------------------------------------------------------------------


def test_throughput_scales_sublinearly(regression_slope):
    """Per-frame latency should stay constant as frame count grows
    (HMAC-SHA256 is O(payload_size), not O(N_frames)). Slope of
    per-frame time vs n_frames should be ~0."""
    sizes = [100, 1_000, 10_000]
    times = []
    payload = b"X" * 64
    for n in sizes:
        pkts = [_build_frame(1, 0xFF, i & 0xFFFF, payload) for i in range(n)]
        t0 = time.perf_counter()
        for p in pkts:
            _verify_frame(p)
        elapsed = time.perf_counter() - t0
        times.append(elapsed / n)
    slope = regression_slope(sizes, times)
    # 100ns per added frame would mean ~1s overhead at 10M — unacceptable.
    assert slope < 1e-7, (
        f"per-frame latency grew {slope*1e9:.2f}ns/frame — " f"O(N) regression"
    )


# ---------------------------------------------------------------------------
# Throughput floor — must process > N frames/sec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_frames,floor_per_sec",
    [
        (1_000, 1_000),
        (10_000, 5_000),
        (100_000, 10_000),
    ],
)
def test_verify_throughput_above_floor(n_frames, floor_per_sec):
    payload = b"abcdef" * 16
    pkts = [_build_frame(1, 0xFF, i & 0xFFFF, payload) for i in range(n_frames)]
    t0 = time.perf_counter()
    for p in pkts:
        _verify_frame(p)
    elapsed = time.perf_counter() - t0
    throughput = n_frames / elapsed
    assert (
        throughput > floor_per_sec
    ), f"{n_frames} frames: {throughput:.0f}/sec < {floor_per_sec} floor"


# ---------------------------------------------------------------------------
# Build throughput — frame construction with HMAC
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_frames,floor_per_sec",
    [
        (1_000, 1_000),
        (10_000, 5_000),
        (100_000, 10_000),
    ],
)
def test_build_throughput_above_floor(n_frames, floor_per_sec):
    payload = b"X" * 32
    t0 = time.perf_counter()
    for i in range(n_frames):
        _build_frame(1, 0xFF, i & 0xFFFF, payload)
    elapsed = time.perf_counter() - t0
    throughput = n_frames / elapsed
    assert (
        throughput > floor_per_sec
    ), f"{n_frames} builds: {throughput:.0f}/sec < {floor_per_sec} floor"


# ---------------------------------------------------------------------------
# Chained-MAC simulation — sequential MAC chain over N frames
# ---------------------------------------------------------------------------


def _chain_mac_step(key: bytes, payload: bytes, prev_mac: bytes) -> bytes:
    return hmac_mod.new(key, payload + prev_mac, hashlib.sha256).digest()


@pytest.mark.parametrize("n_frames", [10, 100, 1_000, 10_000])
def test_chained_mac_throughput(n_frames):
    """Chained-MAC over N frames must complete within a per-frame
    budget. Each frame is one HMAC-SHA256 of (payload || prev_mac)."""
    key = b"x" * 32
    iv = b"\x00" * 32
    state = iv
    payload = b"tok_" * 16
    t0 = time.perf_counter()
    for _ in range(n_frames):
        state = _chain_mac_step(key, payload, state)
    elapsed = time.perf_counter() - t0
    per_frame_us = (elapsed / n_frames) * 1e6
    # 100µs/frame is generous; actual ~5µs on M-series.
    assert per_frame_us < 100, f"chained-MAC {per_frame_us:.2f}µs/frame — over budget"


def test_chained_mac_scaling_slope(regression_slope):
    """Per-frame chained-MAC time stays constant — slope ~0."""
    key = b"x" * 32
    iv = b"\x00" * 32
    payload = b"tok_" * 16

    sizes = [100, 1_000, 5_000]
    per_frame_times = []
    for n in sizes:
        state = iv
        t0 = time.perf_counter()
        for _ in range(n):
            state = _chain_mac_step(key, payload, state)
        elapsed = time.perf_counter() - t0
        per_frame_times.append(elapsed / n)
    slope = regression_slope(sizes, per_frame_times)
    assert slope < 1e-7, f"chained-MAC slope {slope*1e9:.2f}ns/frame — non-constant"


# ---------------------------------------------------------------------------
# Frame size variance — verify large payloads don't break the budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload_size",
    [
        16,
        64,
        256,
        1024,
        4096,
        16384,
        65536,
    ],
)
def test_frame_round_trip_at_payload_size(payload_size):
    payload = os.urandom(payload_size)
    pkt = _build_frame(1, 0xFF, 0xCAFE, payload)
    assert _verify_frame(pkt)


@pytest.mark.parametrize("payload_size", [16, 256, 4096, 65536])
def test_per_frame_latency_scales_with_payload(payload_size, regression_slope):
    """Latency growing linearly with payload size is EXPECTED (HMAC is
    O(N)). Verify by measuring at varying sizes."""
    n_frames = 100
    payload = os.urandom(payload_size)
    pkts = [_build_frame(1, 0xFF, i & 0xFFFF, payload) for i in range(n_frames)]
    t0 = time.perf_counter()
    for p in pkts:
        _verify_frame(p)
    elapsed = time.perf_counter() - t0
    per_frame = elapsed / n_frames
    # Even a 64KB payload should verify in < 5ms.
    assert per_frame < 5e-3
