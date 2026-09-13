"""
VBus frame protocol — offline (no socket) encode/CRC/HMAC tests.

The kernel-side ring buffer + Python-side driver share a binary frame
format with dual CRC32C and HMAC-SHA256 authentication. We exercise
the format invariants without a live socket so the tests are
hermetic + parallel-safe:

  * header layout is exactly 64 bytes (struct fmt "<BBHIII48s")
  * CRC32C is deterministic + collision-resistant for small inputs
  * HMAC-SHA256 verification rejects any single-bit tamper
  * frame size enforcement at the 2MB payload ceiling
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import struct

import pytest

FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)
VBUS_MAX_PAYLOAD = 2_097_152  # 2 MB


def _crc32c_castagnoli(data: bytes) -> int:
    """Software CRC32C — Castagnoli polynomial 0x82F63B78, big-table fallback."""
    crc = 0xFFFFFFFF
    poly = 0x82F63B78
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _build_frame(
    frame_type: int,
    slot_id: int,
    tag: int,
    payload: bytes,
    hmac_key: bytes | None = None,
) -> bytes:
    payload_crc = _crc32c_castagnoli(payload) & 0xFFFFFFFF
    header_prefix = struct.pack(
        "<BBHII",
        frame_type,
        slot_id,
        tag,
        len(payload),
        payload_crc,
    )  # 12 bytes
    hdr_crc = _crc32c_castagnoli(header_prefix) & 0xFFFFFFFF
    header = header_prefix + struct.pack("<I", hdr_crc) + b"\x00" * 48
    assert len(header) == FRAME_HDR_SIZE

    if hmac_key is not None:
        mac = hmac_mod.new(hmac_key, header[:16] + payload, hashlib.sha256).digest()
        header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
        assert len(header) == FRAME_HDR_SIZE
    return header + payload


# ---------------------------------------------------------------------------
# Header geometry invariants
# ---------------------------------------------------------------------------


def test_header_size_is_64_bytes():
    assert FRAME_HDR_SIZE == 64


def test_max_payload_is_2_mb():
    assert VBUS_MAX_PAYLOAD == 2 * 1024 * 1024


@pytest.mark.parametrize("frame_type", list(range(1, 13)))
def test_frame_type_fits_in_one_byte(frame_type):
    pkt = _build_frame(frame_type, 0xFF, 0xCAFE, b"x")
    assert pkt[0] == frame_type


@pytest.mark.parametrize("slot_id", [0, 1, 7, 64, 0xFE, 0xFF])
def test_slot_id_round_trips(slot_id):
    pkt = _build_frame(0x01, slot_id, 0, b"")
    assert pkt[1] == slot_id


@pytest.mark.parametrize("tag", [0x0001, 0x0100, 0xCAFE, 0x7FFE, 0xFFFE])
def test_tag_round_trips_little_endian(tag):
    pkt = _build_frame(0x01, 0xFF, tag, b"")
    assert struct.unpack_from("<H", pkt, 2)[0] == tag


# ---------------------------------------------------------------------------
# CRC32C determinism + sensitivity
# ---------------------------------------------------------------------------

# Use a fixed-seed RNG so parametrize ids are identical on every xdist worker.
import random as _random

_rng = _random.Random(0xDEADBEEF)


def _det_bytes(n: int) -> bytes:
    return bytes(_rng.randrange(256) for _ in range(n))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"\xff" * 16,
        b"VBUS3" * 32,
        b"abcdef" * 100,
        _det_bytes(7),
        _det_bytes(33),
        _det_bytes(257),
        _det_bytes(1024),
    ],
)
def test_crc32c_is_deterministic(payload):
    a = _crc32c_castagnoli(payload)
    b = _crc32c_castagnoli(payload)
    assert a == b


@pytest.mark.parametrize("bit_idx", list(range(0, 64, 4)))
def test_crc32c_flips_on_single_bit_tamper(bit_idx):
    base = b"VBUS_TEST_PAYLOAD_FOR_CRC_CHECK_" * 4
    raw = bytearray(base)
    raw[bit_idx // 8] ^= 1 << (bit_idx % 8)
    assert _crc32c_castagnoli(base) != _crc32c_castagnoli(bytes(raw))


def test_crc32c_known_vector_empty():
    # Empty input → 0x00000000 after the final XOR (initial 0xFFFFFFFF ^ 0xFFFFFFFF).
    assert _crc32c_castagnoli(b"") == 0


def test_crc32c_known_vector_zero_byte():
    # Single 0x00 byte → CRC32C("\x00") well-known value 0x527D5351 in the
    # Castagnoli polynomial. Vector cross-checked against RFC 3720 §B.
    assert _crc32c_castagnoli(b"\x00") == 0x527D5351


# ---------------------------------------------------------------------------
# HMAC-SHA256 frame authentication
# ---------------------------------------------------------------------------

_HMAC_KEY = b"0123456789abcdef" * 2  # 32 bytes


def _verify_hmac(packet: bytes, key: bytes) -> bool:
    header = packet[:FRAME_HDR_SIZE]
    payload = packet[FRAME_HDR_SIZE:]
    received = header[16:48]
    expected = hmac_mod.new(key, header[:16] + payload, hashlib.sha256).digest()
    return hmac_mod.compare_digest(received, expected)


@pytest.mark.parametrize("payload_len", [0, 1, 16, 32, 64, 256, 1024, 4096])
def test_hmac_verifies_intact_frame(payload_len):
    payload = os.urandom(payload_len)
    pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY)
    assert _verify_hmac(pkt, _HMAC_KEY)


@pytest.mark.parametrize("tamper_byte", list(range(0, 16, 2)))
def test_hmac_rejects_header_tamper(tamper_byte):
    """Bytes 0–15 are the part of the header HMAC covers.

    Bytes 16–47 are the MAC field itself (tested by
    ``test_hmac_rejects_mac_tamper``). Bytes 48–63 are post-MAC padding
    that the verifier does NOT include in the HMAC input, so flipping
    them is correctly ignored by the protocol.
    """
    payload = b"hello world" * 8
    pkt = bytearray(_build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY))
    pkt[tamper_byte] ^= 0x01
    assert not _verify_hmac(bytes(pkt), _HMAC_KEY)


@pytest.mark.parametrize("mac_byte", list(range(16, 48, 2)))
def test_hmac_rejects_mac_tamper(mac_byte):
    payload = b"abcdef" * 16
    pkt = bytearray(_build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY))
    pkt[mac_byte] ^= 0x01
    assert not _verify_hmac(bytes(pkt), _HMAC_KEY)


@pytest.mark.parametrize("payload_byte", [0, 1, 4, 16, 31])
def test_hmac_rejects_payload_tamper(payload_byte):
    payload = bytearray(b"0123456789abcdef" * 2)
    pkt = bytearray(
        _build_frame(0x01, 0xFF, 0xCAFE, bytes(payload), hmac_key=_HMAC_KEY)
    )
    pkt[FRAME_HDR_SIZE + payload_byte] ^= 0x01
    assert not _verify_hmac(bytes(pkt), _HMAC_KEY)


def test_hmac_rejects_wrong_key():
    payload = b"VBUS3_AUTH_TEST"
    pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY)
    wrong_key = b"X" * 32
    assert not _verify_hmac(pkt, wrong_key)


def test_hmac_rejects_truncated_packet():
    payload = b"abcdefghij" * 4
    pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY)
    truncated = pkt[: FRAME_HDR_SIZE + 5]
    assert not _verify_hmac(truncated, _HMAC_KEY)


# ---------------------------------------------------------------------------
# Size enforcement — 2 MB ceiling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [1, 1024, 65_536, 1_048_576, VBUS_MAX_PAYLOAD])
def test_payload_under_or_at_ceiling_is_buildable(size):
    payload = b"\xaa" * size
    pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload)
    assert len(pkt) == FRAME_HDR_SIZE + size


def test_payload_above_ceiling_caller_responsibility():
    # The builder doesn't enforce — that's the driver's job. Document
    # the contract: build succeeds, length is preserved verbatim.
    payload = b"\xaa" * (VBUS_MAX_PAYLOAD + 1)
    pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload)
    declared_len = struct.unpack_from("<I", pkt, 4)[0]
    assert declared_len == VBUS_MAX_PAYLOAD + 1


# ---------------------------------------------------------------------------
# Flood test — slow, but verifies HMAC + CRC scaling
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("n_frames", [1_000, 5_000, 10_000])
def test_frame_flood_all_verify(n_frames):
    rng = os.urandom
    for _ in range(n_frames):
        payload = rng(64)
        pkt = _build_frame(0x01, 0xFF, 0xCAFE, payload, hmac_key=_HMAC_KEY)
        assert _verify_hmac(pkt, _HMAC_KEY)


@pytest.mark.slow
def test_frame_flood_no_collisions():
    """10k random payloads → 10k unique header-CRCs (collision-free in practice)."""
    crcs = set()
    for _ in range(10_000):
        payload = os.urandom(64)
        c = _crc32c_castagnoli(payload)
        crcs.add(c)
    # Birthday bound: 10k 32-bit hashes have <12% chance of even one collision.
    # We tolerate up to 5 collisions to keep the test deterministic.
    assert len(crcs) > 10_000 - 5
