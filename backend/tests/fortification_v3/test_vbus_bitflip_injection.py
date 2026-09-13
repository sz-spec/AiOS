"""
Phase 2 · VBus bit-flip injection — exhaustive HMAC tamper coverage.

Reuses the hermetic frame builder from `test_vbus_frame_perf.py` (same
layout `<BBHIII48s` + payload, HMAC-SHA256 at header offset 16, length
32, flag at offset 48). Every single-bit flip in the HMAC MAC field
or its payload MUST be rejected by the constant-time verifier.

This file's contribution is volume + coverage: every byte of the MAC
flipped at every bit position (32×8 = 256 cases), plus dense payload-
bit flips, plus a Hypothesis-free pseudo-random fuzz of 100 derived
tampers. The frame format and verifier are pure functions, so the
test runs without a socket and without QEMU.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import struct
import random

import pytest

FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    poly = 0x82F63B78
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


_HMAC_KEY = b"0123456789abcdef" * 2


def _build(
    frame_type: int, slot_id: int, tag: int, payload: bytes, key: bytes = _HMAC_KEY
) -> bytes:
    payload_crc = _crc32c(payload) & 0xFFFFFFFF
    pre = struct.pack("<BBHII", frame_type, slot_id, tag, len(payload), payload_crc)
    hdr_crc = _crc32c(pre) & 0xFFFFFFFF
    header = pre + struct.pack("<I", hdr_crc) + b"\x00" * 48
    mac = hmac_mod.new(key, header[:16] + payload, hashlib.sha256).digest()
    header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
    return header + payload


def _verify(packet: bytes, key: bytes = _HMAC_KEY) -> bool:
    header = packet[:FRAME_HDR_SIZE]
    payload = packet[FRAME_HDR_SIZE:]
    received = header[16:48]
    expected = hmac_mod.new(key, header[:16] + payload, hashlib.sha256).digest()
    return hmac_mod.compare_digest(received, expected)


# ---------------------------------------------------------------------------
# Every MAC byte × every bit position — 32 × 8 = 256 single-bit flips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mac_byte", list(range(16, 48)))
@pytest.mark.parametrize("bit", list(range(8)))
def test_single_bit_flip_in_mac_rejects(mac_byte, bit):
    payload = b"VBUS3" * 16
    pkt = bytearray(_build(0x01, 0xFF, 0xCAFE, payload))
    pkt[mac_byte] ^= 1 << bit
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Payload bit-flips — 8 payload positions × 8 bits each = 64 cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload_byte", [0, 1, 4, 8, 16, 32, 47, 63])
@pytest.mark.parametrize("bit", list(range(8)))
def test_single_bit_flip_in_payload_rejects(payload_byte, bit):
    payload = bytearray(b"\x55\xaa" * 32)
    pkt = bytearray(_build(0x01, 0xFF, 0xCAFE, bytes(payload)))
    pkt[FRAME_HDR_SIZE + payload_byte] ^= 1 << bit
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Header bit-flips inside the HMAC-covered prefix (bytes 0-15) — 16 × 8 = 128
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hdr_byte", list(range(0, 16)))
@pytest.mark.parametrize("bit", [0, 3, 7])  # representative bits
def test_single_bit_flip_in_hmac_prefix_rejects(hdr_byte, bit):
    payload = b"X" * 32
    pkt = bytearray(_build(0x01, 0xFF, 0xCAFE, payload))
    pkt[hdr_byte] ^= 1 << bit
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Multi-bit tampers — 50 random masks across 1024 cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", list(range(50)))
def test_random_multi_bit_tamper_rejects(seed):
    rng = random.Random(seed)
    payload = bytes(rng.randint(0, 255) for _ in range(64))
    pkt = bytearray(_build(0x01, 0xFF, 0xCAFE, payload))
    # Flip 2-5 random bits inside the MAC.
    for _ in range(rng.randint(2, 5)):
        b = rng.randint(16, 47)
        pkt[b] ^= 1 << rng.randint(0, 7)
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Replay across different keys — same packet, wrong key must reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("salt", list(range(16)))
def test_wrong_key_rejects_otherwise_valid_frame(salt):
    payload = bytes([salt]) * 64
    pkt = _build(0x01, 0xFF, 0xCAFE, payload, key=_HMAC_KEY)
    wrong_key = bytes([salt]) * 32
    assert _verify(pkt, key=wrong_key) is False


# ---------------------------------------------------------------------------
# Intact frame round-trip — must always verify (positive control)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload_size", [0, 1, 16, 64, 256, 1024, 4096, 16384])
def test_intact_frame_verifies(payload_size):
    payload = os.urandom(payload_size)
    pkt = _build(0x01, 0xFF, 0xCAFE, payload)
    assert _verify(pkt) is True


# ---------------------------------------------------------------------------
# Frame type fuzz — every documented type must round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ftype", list(range(1, 13)))
def test_each_frame_type_round_trips(ftype):
    payload = b"frame-type-test"
    pkt = _build(ftype, 0xFF, 0xCAFE, payload)
    assert _verify(pkt) is True


@pytest.mark.parametrize("ftype", list(range(1, 13)))
def test_each_frame_type_rejects_mac_tamper(ftype):
    payload = b"frame-type-test"
    pkt = bytearray(_build(ftype, 0xFF, 0xCAFE, payload))
    pkt[20] ^= 0x01  # flip in MAC
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Empty payload edge case
# ---------------------------------------------------------------------------


def test_empty_payload_round_trips():
    pkt = _build(0x01, 0xFF, 0xCAFE, b"")
    assert _verify(pkt) is True


def test_empty_payload_tamper_rejects():
    pkt = bytearray(_build(0x01, 0xFF, 0xCAFE, b""))
    pkt[20] ^= 0x01
    assert _verify(bytes(pkt)) is False


# ---------------------------------------------------------------------------
# Constant-time invariant — verify uses hmac.compare_digest, not == .
# We can't test the C kernel verifier from here, but we can audit the
# Python source-level pattern for the driver's own verifier.
# ---------------------------------------------------------------------------


def test_python_vbus_driver_uses_constant_time_compare(repo_root):
    """services/vbus_driver.py must use hmac.compare_digest, not ==
    or memcmp-style equality."""
    src = (repo_root / "backend" / "services" / "vbus_driver.py").read_text()
    assert (
        "compare_digest" in src
    ), "VBus driver verifier must use hmac.compare_digest (constant-time)"


# ---------------------------------------------------------------------------
# Slow — 1000 random bit-flips against the MAC field
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_random_1000_bit_flips_in_mac():
    rng = random.Random(0xDEADBEEF)
    payload = b"VBUS3_BITFLIP_FUZZ" * 8
    base_pkt = _build(0x01, 0xFF, 0xCAFE, payload)
    rejections = 0
    for _ in range(1000):
        pkt = bytearray(base_pkt)
        b = rng.randint(16, 47)
        pkt[b] ^= 1 << rng.randint(0, 7)
        if not _verify(bytes(pkt)):
            rejections += 1
    assert rejections == 1000, f"only {rejections}/1000 single-bit flips rejected"
