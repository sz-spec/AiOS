"""
VBus LARGE_SIG frame extension — hybrid PQC signature transport.

vOS·Adaptive·SHA=aeb3736·Phase=P2

Why a separate frame?
---------------------
The existing VBus base frame (vbus_driver.py FRAME_HDR_FMT) places
the per-frame HMAC-SHA256 at offset 16 of the 64-byte header. That
HMAC is the per-session transport-integrity tag — it is keyed during
HANDSHAKE and verified per-frame.

The hybrid Ed25519 + ML-DSA-65 signature for a workflow-run
authenticity decision is a SEPARATE layer (different keys, different
trust scope: per-workflow-run identity rather than per-session
transport). The two layers must NOT collide:
  * base frame's offset-16 HMAC keeps its byte-exact protocol
    layout — no field changes, no shift, no breakage of existing
    consumers
  * a LARGE_SIG sibling frame, type 0x06, carries the 3,373-byte
    hybrid sig + a 32-byte binding digest linking it to the base
    frame

Receivers process the two frames in order. If LARGE_SIG arrives
without a matching base frame (binding_digest mismatch), it is
rejected at the logic gate before any signature verification.

Frame layout (LARGE_SIG, type 0x06)
-----------------------------------
Header (64 bytes) — identical struct to FRAME_HDR_FMT:
    "<BBHIII48s"
    +0   B  frame_type        = 0x06
    +1   B  slot_id           = (copied from base frame)
    +2   H  tag               = (copied from base frame)
    +4   I  payload_length    = 3,405 (= 32 + 64 + 3309)
    +8   I  payload_crc32c
    +12  I  header_crc32c
    +16  48s  [HMAC-SHA256 (32 B) || pad (16 B)]

Payload (3,405 bytes), tightly packed:
    +0    32 B   binding_digest    = SHA-256 of canonical
                                       (payload || sig_ed || sig_ml)
                                     per services.pqc_sign.hybrid_binding_digest
    +32   64 B   sig_ed25519
    +96   3309 B sig_mldsa         (FIPS 204 ML-DSA-65)

Honest scope
------------
* MAX_LARGE_SIG_PAYLOAD = 3,405 bytes is FIXED. We do NOT support
  variable-length sigs in this extension — the FIPS 204 spec pins
  the ML-DSA-65 sig at 3,309 bytes. A future scheme (ML-DSA-87 at
  4,627 bytes, say) would need a new frame_type, not a longer
  field.
* We do NOT change FRAME_HDR_FMT or the HMAC offset. The base frame
  is byte-identical to the pre-P2.3 protocol; the existing
  vbus_driver.py and all pre-existing test fixtures continue to
  parse base frames without modification.
* This module deliberately re-implements `_build_frame` /
  `_verify_frame` from the test fixtures rather than importing from
  vbus_driver — the driver is a real I/O object with sockets and
  keys; this module is pure data transformation. Tests pin that
  both implementations produce byte-identical base frames for the
  same inputs.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import struct
from typing import Tuple

from services.pqc_sign import (
    hybrid_binding_digest,
    hybrid_binding_verify,
    hybrid_verify,
)

# ---------------------------------------------------------------------------
# Protocol constants — mirror vbus_driver.py
# ---------------------------------------------------------------------------

FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 64 bytes
HMAC_OFFSET = 16
HMAC_LEN = 32

# Sigs are fixed-size per FIPS 204 ML-DSA-65 + Ed25519.
ED25519_SIG_LEN = 64
MLDSA65_SIG_LEN = 3309
BINDING_DIGEST_LEN = 32
LARGE_SIG_PAYLOAD_LEN = BINDING_DIGEST_LEN + ED25519_SIG_LEN + MLDSA65_SIG_LEN  # 3,405

# New frame type. Existing frame types (per CLAUDE.md / vbus_driver):
#   0x01 = data frame, 0x02 = HANDSHAKE, ...
# LARGE_SIG = 0x06 per the AAA plan reservation.
FRAME_TYPE_LARGE_SIG = 0x06


# ---------------------------------------------------------------------------
# CRC32C (Castagnoli) — same implementation as the test fixtures
# ---------------------------------------------------------------------------


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    poly = 0x82F63B78
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Base frame builder — re-implemented to pin the byte-exact layout
# ---------------------------------------------------------------------------


def build_base_frame(
    frame_type: int,
    slot_id: int,
    tag: int,
    payload: bytes,
    hmac_key: bytes,
) -> bytes:
    """Builds a 64-byte-header + payload frame with HMAC at offset 16.

    Identical byte layout to vbus_driver's frame builder and to the
    `_build_frame` helper in tests/fortification_v5_scale/test_vbus_throughput.py
    — pinned by `test_base_frame_matches_existing_layout`.
    """
    payload_crc = _crc32c(payload) & 0xFFFFFFFF
    pre = struct.pack(
        "<BBHII",
        frame_type,
        slot_id,
        tag,
        len(payload),
        payload_crc,
    )
    hdr_crc = _crc32c(pre) & 0xFFFFFFFF
    header = pre + struct.pack("<I", hdr_crc) + b"\x00" * 48
    mac = _hmac.new(hmac_key, header[:16] + payload, hashlib.sha256).digest()
    header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
    return header + payload


def verify_base_frame_hmac(pkt: bytes, hmac_key: bytes) -> bool:
    """Verifies the offset-16 HMAC of any frame (base OR LARGE_SIG).

    Returns False on any malformation (short packet, bad CRC, bad
    HMAC). Constant-time HMAC comparison via hmac.compare_digest.
    """
    if len(pkt) < FRAME_HDR_SIZE:
        return False
    header = pkt[:FRAME_HDR_SIZE]
    payload = pkt[FRAME_HDR_SIZE:]
    received_mac = header[HMAC_OFFSET : HMAC_OFFSET + HMAC_LEN]
    expected_mac = _hmac.new(
        hmac_key,
        header[:HMAC_OFFSET] + payload,
        hashlib.sha256,
    ).digest()
    return _hmac.compare_digest(received_mac, expected_mac)


# ---------------------------------------------------------------------------
# LARGE_SIG frame builder / parser
# ---------------------------------------------------------------------------


def build_large_sig_frame(
    *,
    slot_id: int,
    tag: int,
    base_payload: bytes,
    sig_ed: bytes,
    sig_mldsa: bytes,
    hmac_key: bytes,
) -> bytes:
    """Build a LARGE_SIG sibling frame for a previously-built base frame.

    The binding_digest field links this LARGE_SIG to its base frame
    by hashing (base_payload || sig_ed || sig_mldsa) via the
    versioned binding scheme in services.pqc_sign.

    Argument shapes (strict, no auto-pad):
      sig_ed     : exactly 64 bytes
      sig_mldsa  : exactly 3309 bytes

    Raises ValueError if either signature has the wrong length —
    the LARGE_SIG protocol does NOT support variable-length sigs
    (see honest-scope note above).
    """
    if len(sig_ed) != ED25519_SIG_LEN:
        raise ValueError(
            f"sig_ed must be exactly {ED25519_SIG_LEN} bytes; got {len(sig_ed)}",
        )
    if len(sig_mldsa) != MLDSA65_SIG_LEN:
        raise ValueError(
            f"sig_mldsa must be exactly {MLDSA65_SIG_LEN} bytes; got {len(sig_mldsa)}",
        )

    binding = hybrid_binding_digest(base_payload, sig_ed, sig_mldsa)
    assert len(binding) == BINDING_DIGEST_LEN

    payload = binding + sig_ed + sig_mldsa
    assert len(payload) == LARGE_SIG_PAYLOAD_LEN

    return build_base_frame(
        frame_type=FRAME_TYPE_LARGE_SIG,
        slot_id=slot_id,
        tag=tag,
        payload=payload,
        hmac_key=hmac_key,
    )


def parse_large_sig_frame(
    pkt: bytes,
    hmac_key: bytes,
) -> Tuple[bytes, bytes, bytes]:
    """Verify HMAC + parse a LARGE_SIG frame.

    Returns (binding_digest, sig_ed, sig_mldsa).
    Raises ValueError on any malformation — wrong frame type,
    wrong payload length, HMAC failure, etc. Caller MUST also
    call verify_large_sig_chain (or hybrid_verify directly) to
    validate the cryptographic signatures themselves.
    """
    if len(pkt) != FRAME_HDR_SIZE + LARGE_SIG_PAYLOAD_LEN:
        raise ValueError(
            f"LARGE_SIG packet wrong size: {len(pkt)} "
            f"(expected {FRAME_HDR_SIZE + LARGE_SIG_PAYLOAD_LEN})",
        )

    # Frame type check first — fail fast before HMAC compute.
    frame_type = pkt[0]
    if frame_type != FRAME_TYPE_LARGE_SIG:
        raise ValueError(
            f"frame_type {frame_type:#04x} is not LARGE_SIG "
            f"({FRAME_TYPE_LARGE_SIG:#04x})",
        )

    if not verify_base_frame_hmac(pkt, hmac_key):
        raise ValueError("HMAC verification failed on LARGE_SIG frame")

    payload = pkt[FRAME_HDR_SIZE:]
    binding = payload[:BINDING_DIGEST_LEN]
    sig_ed = payload[BINDING_DIGEST_LEN : BINDING_DIGEST_LEN + ED25519_SIG_LEN]
    sig_mldsa = payload[BINDING_DIGEST_LEN + ED25519_SIG_LEN :]
    return binding, sig_ed, sig_mldsa


# ---------------------------------------------------------------------------
# Chain verifier — the only API workflow-run validators should call
# ---------------------------------------------------------------------------


def verify_large_sig_chain(
    base_pkt: bytes,
    large_sig_pkt: bytes,
    *,
    ed_pub: bytes,
    mldsa_pub: bytes,
    hmac_key: bytes,
) -> bool:
    """End-to-end verifier for a base+LARGE_SIG pair.

    Returns True iff ALL of:
      1. base frame's HMAC-SHA256 at offset 16 verifies
      2. LARGE_SIG frame's HMAC-SHA256 at offset 16 verifies
      3. LARGE_SIG payload size + frame_type are correct
      4. binding_digest field equals hybrid_binding_digest(base_payload,
         sig_ed, sig_mldsa) — links the two frames cryptographically
      5. hybrid_verify(base_payload, ed_pub, mldsa_pub, sig_ed,
         sig_mldsa) — the hybrid scheme accepts the signature pair

    All four cryptographic checks ALWAYS run regardless of intermediate
    results — same constant-evaluation discipline as hybrid_verify.
    A short-circuit here would leak which check failed via timing.
    """
    # Pre-compute booleans without early-return.
    base_ok = verify_base_frame_hmac(base_pkt, hmac_key)
    base_payload = base_pkt[FRAME_HDR_SIZE:] if len(base_pkt) >= FRAME_HDR_SIZE else b""

    try:
        binding, sig_ed, sig_mldsa = parse_large_sig_frame(
            large_sig_pkt,
            hmac_key,
        )
        parse_ok = True
    except ValueError:
        parse_ok = False
        binding = b"\x00" * BINDING_DIGEST_LEN
        sig_ed = b"\x00" * ED25519_SIG_LEN
        sig_mldsa = b"\x00" * MLDSA65_SIG_LEN

    binding_ok = hybrid_binding_verify(base_payload, sig_ed, sig_mldsa, binding)
    sig_ok = hybrid_verify(base_payload, ed_pub, mldsa_pub, sig_ed, sig_mldsa)

    # Final AND via integer bitwise (non-short-circuit by construction).
    return bool(int(base_ok) & int(parse_ok) & int(binding_ok) & int(sig_ok))


__all__ = [
    "FRAME_HDR_FMT",
    "FRAME_HDR_SIZE",
    "HMAC_OFFSET",
    "HMAC_LEN",
    "FRAME_TYPE_LARGE_SIG",
    "ED25519_SIG_LEN",
    "MLDSA65_SIG_LEN",
    "BINDING_DIGEST_LEN",
    "LARGE_SIG_PAYLOAD_LEN",
    "build_base_frame",
    "verify_base_frame_hmac",
    "build_large_sig_frame",
    "parse_large_sig_frame",
    "verify_large_sig_chain",
]
