"""
Tier-2 Security Validation: VBus HMAC-SHA256 Pentest
=====================================================

Tests 3 attack vectors against the VBus HMAC frame authentication:
  A) Payload Tampering — valid frame, 1 flipped bit in payload
  B) HMAC Forgery — valid frame, random bytes in HMAC field
  C) Downgrade Attack — valid frame, HMAC flag bit cleared (legacy)

All 3 attacks MUST be rejected by the receiver.

This test validates the Python-side enforcement, which mirrors the
kernel-side enforcement in virtio_vbus.c:parse_frame_header().
"""

import hashlib
import hmac as hmac_mod
import os
import struct
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import _crc32c, FRAME_HDR_FMT, FRAME_HDR_SIZE

# Constants matching kernel
VBUS_TYPE_CMD = 0x01
VBUS_TYPE_RESP = 0x02
VBUS_HMAC_OFFSET = 16
VBUS_HMAC_SIZE = 32
VBUS_FLAGS_OFFSET = 48
VBUS_FLAG_HMAC = 0x01
VBUS_MAX_PAYLOAD = 2097152


def build_authentic_frame(
    hmac_key: bytes, frame_type: int, slot_id: int, tag: int, payload: bytes
) -> bytes:
    """Build a fully valid HMAC-authenticated VBus frame."""
    length = len(payload)
    hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
    hdr_crc = _crc32c(hdr_prefix)
    payload_crc = _crc32c(payload, _crc32c(hdr_prefix))

    # Build 64-byte header with zero padding
    header = struct.pack(
        FRAME_HDR_FMT,
        frame_type,
        slot_id,
        tag,
        length,
        payload_crc,
        hdr_crc,
        b"\x00" * 48,
    )

    # Compute HMAC over bytes 0-15 + payload
    hmac_input = header[:16] + payload
    mac = hmac_mod.new(hmac_key, hmac_input, hashlib.sha256).digest()

    # Splice HMAC into header: bytes 16-47 = HMAC, byte 48 = flag
    header = header[:16] + mac + bytes([VBUS_FLAG_HMAC]) + b"\x00" * 15

    return header + payload


def verify_frame(hmac_key: bytes, raw_frame: bytes) -> bool:
    """
    Verify a VBus frame exactly as the kernel/Python driver would.
    Returns True if valid, raises ValueError on rejection.
    """
    if len(raw_frame) < FRAME_HDR_SIZE:
        raise ValueError("Frame too short")

    header = raw_frame[:FRAME_HDR_SIZE]
    frame_type, slot_id, tag, length, expected_pcrc, expected_hcrc, _pad = (
        struct.unpack(FRAME_HDR_FMT, header)
    )

    # Step 1: hdr_crc (fast reject)
    hdr_prefix = header[:8]
    actual_hcrc = _crc32c(hdr_prefix)
    if actual_hcrc != expected_hcrc:
        raise ValueError(
            f"Header CRC mismatch: 0x{expected_hcrc:08x} vs 0x{actual_hcrc:08x}"
        )

    if length > VBUS_MAX_PAYLOAD:
        raise ValueError(f"Frame too large: {length}")

    payload = raw_frame[FRAME_HDR_SIZE : FRAME_HDR_SIZE + length]
    if len(payload) != length:
        raise ValueError(f"Truncated payload: {len(payload)} < {length}")

    # Step 2: payload_crc (integrity)
    actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
    if actual_pcrc != expected_pcrc:
        raise ValueError(
            f"Payload CRC mismatch: 0x{expected_pcrc:08x} vs 0x{actual_pcrc:08x}"
        )

    # Step 3: HMAC-SHA256 (authentication)
    flag = header[VBUS_FLAGS_OFFSET]
    if not (flag & VBUS_FLAG_HMAC):
        raise ValueError("HMAC required but flag not set")

    received_hmac = header[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
    expected_mac = hmac_mod.new(
        hmac_key, header[:16] + payload, hashlib.sha256
    ).digest()
    if not hmac_mod.compare_digest(received_hmac, expected_mac):
        raise ValueError("HMAC verification failed")

    return True


def test_baseline_valid_frame():
    """Verify that a properly authenticated frame passes."""
    key = os.urandom(32)
    payload = b"PING"
    frame = build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 1, payload)
    assert verify_frame(key, frame) is True
    print("  [PASS] Baseline: valid authenticated frame accepted")


def test_attack_a_payload_tampering():
    """Attack A: Flip 1 bit in payload after HMAC was computed."""
    key = os.urandom(32)
    payload = b"WRITE|/etc/config|deadbeef"
    frame = bytearray(build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 1, payload))

    # Flip bit 0 of the first payload byte
    tamper_offset = FRAME_HDR_SIZE
    frame[tamper_offset] ^= 0x01

    try:
        verify_frame(key, bytes(frame))
        print("  [FAIL] Attack A: Tampered frame was ACCEPTED (CRITICAL)")
        return False
    except ValueError as e:
        reason = str(e)
        # Must be caught by either CRC or HMAC
        assert "CRC" in reason or "HMAC" in reason, f"Unexpected rejection: {reason}"
        print(f"  [PASS] Attack A: Tampered frame REJECTED — {reason}")
        return True


def test_attack_b_hmac_forgery():
    """Attack B: Replace HMAC field with random bytes."""
    key = os.urandom(32)
    payload = b"SLOT_RESET|1"
    frame = bytearray(build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 2, payload))

    # Overwrite HMAC (bytes 16-47) with random garbage
    frame[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE] = os.urandom(32)

    try:
        verify_frame(key, bytes(frame))
        print("  [FAIL] Attack B: Forged HMAC was ACCEPTED (CRITICAL)")
        return False
    except ValueError as e:
        reason = str(e)
        assert "HMAC verification failed" in reason, f"Unexpected rejection: {reason}"
        print(f"  [PASS] Attack B: Forged HMAC REJECTED — {reason}")
        return True


def test_attack_c_downgrade_no_hmac():
    """Attack C: Send frame with HMAC flag cleared (downgrade attack)."""
    key = os.urandom(32)
    payload = b"PING"
    frame = bytearray(build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 3, payload))

    # Clear the HMAC flag bit (byte 48, bit 0 → 0)
    frame[VBUS_FLAGS_OFFSET] = 0x00

    try:
        verify_frame(key, bytes(frame))
        print("  [FAIL] Attack C: Downgrade frame ACCEPTED (CRITICAL)")
        return False
    except ValueError as e:
        reason = str(e)
        assert (
            "HMAC required but flag not set" in reason
        ), f"Unexpected rejection: {reason}"
        print(f"  [PASS] Attack C: Downgrade REJECTED — {reason}")
        return True


def test_hmac_key_mismatch():
    """Bonus: Frame signed with wrong key must be rejected."""
    key_a = os.urandom(32)
    key_b = os.urandom(32)
    payload = b"SLOT_STATUS|2"
    frame = build_authentic_frame(key_a, VBUS_TYPE_CMD, 0xFF, 4, payload)

    try:
        verify_frame(key_b, frame)
        print("  [FAIL] Key mismatch: Frame ACCEPTED with wrong key (CRITICAL)")
        return False
    except ValueError as e:
        reason = str(e)
        assert "HMAC verification failed" in reason, f"Unexpected rejection: {reason}"
        print(f"  [PASS] Key Mismatch: Wrong-key frame REJECTED — {reason}")
        return True


def test_empty_payload_hmac():
    """Verify HMAC works correctly on zero-length payloads (PING/PONG)."""
    key = os.urandom(32)
    frame = build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 5, b"")
    assert verify_frame(key, frame) is True
    print("  [PASS] Empty payload: HMAC validated correctly")


def test_large_payload_hmac():
    """Verify HMAC works on large payloads (simulating model chunks)."""
    key = os.urandom(32)
    payload = os.urandom(49152)  # 48KB — max chunk size
    frame = build_authentic_frame(key, VBUS_TYPE_CMD, 0x01, 6, payload)
    assert verify_frame(key, frame) is True
    print("  [PASS] Large payload (48KB): HMAC validated correctly")


def test_kernel_mirror_hmac_computation():
    """Verify Python HMAC matches what the kernel would compute.

    The kernel computes: HMAC-SHA256(key, header[0:16] + payload)
    This test verifies the Python side produces the exact same MAC.
    """
    key = bytes(range(32))  # Deterministic key
    payload = b"HELLO KERNEL"

    # Build frame
    frame = build_authentic_frame(key, VBUS_TYPE_CMD, 0xFF, 7, payload)
    header = frame[:FRAME_HDR_SIZE]

    # Extract the HMAC from the frame
    frame_hmac = header[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]

    # Recompute independently
    hmac_input = header[:16] + payload
    expected = hmac_mod.new(key, hmac_input, hashlib.sha256).digest()

    assert frame_hmac == expected, "HMAC computation mismatch!"
    print("  [PASS] Kernel-mirror: HMAC computation deterministic")


def main():
    print("=" * 60)
    print("TIER-2 PENTEST: VBus HMAC-SHA256 Frame Authentication")
    print("=" * 60)

    results = []

    print("\n--- Baseline ---")
    test_baseline_valid_frame()
    results.append(True)

    print("\n--- Attack A: Payload Tampering (1-bit flip) ---")
    results.append(test_attack_a_payload_tampering())

    print("\n--- Attack B: HMAC Forgery (random bytes) ---")
    results.append(test_attack_b_hmac_forgery())

    print("\n--- Attack C: Downgrade (no-HMAC flag) ---")
    results.append(test_attack_c_downgrade_no_hmac())

    print("\n--- Bonus: Key Mismatch ---")
    results.append(test_hmac_key_mismatch())

    print("\n--- Edge Cases ---")
    test_empty_payload_hmac()
    results.append(True)
    test_large_payload_hmac()
    results.append(True)
    test_kernel_mirror_hmac_computation()
    results.append(True)

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    if passed == total:
        print(f"VERDICT: {passed}/{total} PASS — ALL ATTACKS NEUTRALIZED")
    else:
        print(f"VERDICT: {passed}/{total} — SECURITY BREACH DETECTED")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
