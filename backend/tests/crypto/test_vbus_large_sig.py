"""
P2.3 · VBus LARGE_SIG frame extension tests.

vOS·Adaptive·SHA=aeb3736·Phase=P2

Covers backend/services/vbus_frame.py.

Honest scope
------------
* These tests pin the BYTE layout and CRYPTOGRAPHIC chain. They do
  NOT exercise actual VBus socket I/O — vbus_driver.py owns that
  surface and has its own test suite.
* The "base frame HMAC at offset 16 invariant" is tested by
  comparing vbus_frame.build_base_frame's output byte-for-byte
  against the helper used in tests/fortification_v5_scale (which
  is byte-identical to vbus_driver.py's production builder).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import struct

import pytest

from services.pqc_sign import (
    hybrid_keygen,
    hybrid_sign,
    hybrid_binding_digest,
    verify_path,
)
from services.vbus_frame import (
    FRAME_HDR_SIZE,
    HMAC_OFFSET,
    HMAC_LEN,
    FRAME_TYPE_LARGE_SIG,
    ED25519_SIG_LEN,
    MLDSA65_SIG_LEN,
    BINDING_DIGEST_LEN,
    LARGE_SIG_PAYLOAD_LEN,
    build_base_frame,
    build_large_sig_frame,
    parse_large_sig_frame,
    verify_large_sig_chain,
)

pytestmark = pytest.mark.skipif(
    verify_path() == "unavailable",
    reason="no PQC backend installed",
)


HMAC_KEY = b"vbus_session_key" * 2  # 32 bytes


@pytest.fixture(scope="module")
def keypair():
    return hybrid_keygen()


@pytest.fixture(scope="module")
def signed_base(keypair):
    ed_priv, _ed_pub, ml_priv, _ml_pub = keypair
    payload = b"workflow run #42" * 8
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    base = build_base_frame(0x01, 0xFF, 0xCAFE, payload, HMAC_KEY)
    return payload, sig_ed, sig_ml, base


# ---------------------------------------------------------------------------
# Layout invariants
# ---------------------------------------------------------------------------


def test_constants_match_spec():
    assert FRAME_HDR_SIZE == 64
    assert HMAC_OFFSET == 16
    assert HMAC_LEN == 32
    assert FRAME_TYPE_LARGE_SIG == 0x06
    assert ED25519_SIG_LEN == 64
    assert MLDSA65_SIG_LEN == 3309
    assert BINDING_DIGEST_LEN == 32
    assert LARGE_SIG_PAYLOAD_LEN == 32 + 64 + 3309  # 3,405


def test_base_frame_matches_existing_layout():
    """vbus_frame.build_base_frame must be byte-identical to the
    frame builder used by the production VBus driver (mirrored in
    tests/fortification_v5_scale/test_vbus_throughput.py). A drift
    here would break every existing receiver."""

    # Re-implement the existing builder inline (copy from
    # test_vbus_throughput.py to make the diff explicit).
    def _crc32c(data):
        crc = 0xFFFFFFFF
        poly = 0x82F63B78
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ (poly if crc & 1 else 0)
        return crc ^ 0xFFFFFFFF

    def _existing_build_frame(ftype, slot, tag, payload, key):
        payload_crc = _crc32c(payload) & 0xFFFFFFFF
        pre = struct.pack("<BBHII", ftype, slot, tag, len(payload), payload_crc)
        hdr_crc = _crc32c(pre) & 0xFFFFFFFF
        header = pre + struct.pack("<I", hdr_crc) + b"\x00" * 48
        mac = _hmac.new(key, header[:16] + payload, hashlib.sha256).digest()
        header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
        return header + payload

    msg = b"x" * 100
    new_frame = build_base_frame(0x01, 0xFF, 0xCAFE, msg, HMAC_KEY)
    old_frame = _existing_build_frame(0x01, 0xFF, 0xCAFE, msg, HMAC_KEY)
    assert (
        new_frame == old_frame
    ), "vbus_frame.build_base_frame diverged from existing protocol layout"


def test_hmac_offset_unchanged_when_large_sig_extends_protocol(signed_base):
    """The critical invariant: introducing the LARGE_SIG frame type
    must NOT shift the HMAC away from offset 16 in the BASE frame.
    Verify by hashing the base frame's header[0:16] + payload
    independently and confirming bytes[16:48] match."""
    _payload, _sig_ed, _sig_ml, base = signed_base
    payload = base[FRAME_HDR_SIZE:]
    expected = _hmac.new(
        HMAC_KEY, base[:HMAC_OFFSET] + payload, hashlib.sha256
    ).digest()
    assert base[HMAC_OFFSET : HMAC_OFFSET + HMAC_LEN] == expected


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_chain_round_trip(keypair, signed_base):
    _ed_priv, ed_pub, _ml_priv, ml_pub = keypair
    payload, sig_ed, sig_ml, base = signed_base

    large = build_large_sig_frame(
        slot_id=0xFF,
        tag=0xCAFE,
        base_payload=payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    assert len(large) == FRAME_HDR_SIZE + LARGE_SIG_PAYLOAD_LEN
    assert large[0] == FRAME_TYPE_LARGE_SIG

    ok = verify_large_sig_chain(
        base,
        large,
        ed_pub=ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )
    assert ok is True


def test_large_sig_packet_size_is_3469(signed_base, keypair):
    """Pin the over-the-wire size for capacity planning."""
    _ed_priv, _ed_pub, _ml_priv, _ml_pub = keypair
    payload, sig_ed, sig_ml, _base = signed_base
    large = build_large_sig_frame(
        slot_id=1,
        tag=1,
        base_payload=payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    # 64 (header) + 32 (binding) + 64 (ed sig) + 3309 (ml sig) = 3469
    assert len(large) == 3469


def test_parse_large_sig_extracts_fields(signed_base):
    _payload, sig_ed, sig_ml, _base = signed_base
    payload = b"another payload"
    large = build_large_sig_frame(
        slot_id=2,
        tag=2,
        base_payload=payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    binding, parsed_ed, parsed_ml = parse_large_sig_frame(large, HMAC_KEY)
    assert binding == hybrid_binding_digest(payload, sig_ed, sig_ml)
    assert parsed_ed == sig_ed
    assert parsed_ml == sig_ml


# ---------------------------------------------------------------------------
# Wrong-shape / malformation rejection — fail at the logic gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_sig_ed_len", [0, 32, 63, 65, 128])
def test_build_rejects_wrong_ed_sig_length(signed_base, bad_sig_ed_len):
    _payload, _sig_ed, sig_ml, _base = signed_base
    with pytest.raises(ValueError, match="sig_ed"):
        build_large_sig_frame(
            slot_id=0,
            tag=0,
            base_payload=b"x",
            sig_ed=b"\x00" * bad_sig_ed_len,
            sig_mldsa=sig_ml,
            hmac_key=HMAC_KEY,
        )


@pytest.mark.parametrize("bad_ml_sig_len", [0, 1024, 3308, 3310, 4096])
def test_build_rejects_wrong_ml_sig_length(signed_base, bad_ml_sig_len):
    _payload, sig_ed, _sig_ml, _base = signed_base
    with pytest.raises(ValueError, match="sig_mldsa"):
        build_large_sig_frame(
            slot_id=0,
            tag=0,
            base_payload=b"x",
            sig_ed=sig_ed,
            sig_mldsa=b"\x00" * bad_ml_sig_len,
            hmac_key=HMAC_KEY,
        )


def test_parse_rejects_wrong_frame_type():
    """A frame with the right size but wrong frame_type (e.g., 0x01
    data frame) must be rejected at the logic gate, BEFORE HMAC
    verification — so an attacker can't substitute a LARGE_SIG-shaped
    blob for a different frame type."""
    fake = build_base_frame(
        frame_type=0x01,  # data frame, not LARGE_SIG
        slot_id=0,
        tag=0,
        payload=b"x" * LARGE_SIG_PAYLOAD_LEN,
        hmac_key=HMAC_KEY,
    )
    assert len(fake) == FRAME_HDR_SIZE + LARGE_SIG_PAYLOAD_LEN
    with pytest.raises(ValueError, match="not LARGE_SIG"):
        parse_large_sig_frame(fake, HMAC_KEY)


@pytest.mark.parametrize("drop_bytes", [1, 10, 100, 1000, 3404])
def test_parse_rejects_truncated_frame(signed_base, drop_bytes):
    _payload, sig_ed, sig_ml, _base = signed_base
    large = build_large_sig_frame(
        slot_id=0,
        tag=0,
        base_payload=b"x",
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    truncated = large[:-drop_bytes]
    with pytest.raises(ValueError, match="wrong size"):
        parse_large_sig_frame(truncated, HMAC_KEY)


def test_parse_rejects_wrong_hmac_key(signed_base):
    """A frame built with key A must be rejected when parsed with
    key B — even if frame_type and length are otherwise valid."""
    _payload, sig_ed, sig_ml, _base = signed_base
    large = build_large_sig_frame(
        slot_id=0,
        tag=0,
        base_payload=b"x",
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    wrong_key = b"\x00" * 32
    with pytest.raises(ValueError, match="HMAC"):
        parse_large_sig_frame(large, wrong_key)


# ---------------------------------------------------------------------------
# Tamper rejection — chain verifier catches every cryptographic axis
# ---------------------------------------------------------------------------


def test_chain_rejects_tampered_base_payload(keypair, signed_base):
    _ed_priv, ed_pub, _ml_priv, ml_pub = keypair
    _payload, sig_ed, sig_ml, base = signed_base
    large = build_large_sig_frame(
        slot_id=0xFF,
        tag=0xCAFE,
        base_payload=_payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    tampered_base = bytearray(base)
    tampered_base[-1] ^= 0x01
    assert not verify_large_sig_chain(
        bytes(tampered_base),
        large,
        ed_pub=ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )


def test_chain_rejects_tampered_large_sig_payload(keypair, signed_base):
    _ed_priv, ed_pub, _ml_priv, ml_pub = keypair
    _payload, sig_ed, sig_ml, base = signed_base
    large = build_large_sig_frame(
        slot_id=0xFF,
        tag=0xCAFE,
        base_payload=_payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    # Flip a byte deep in the ML-DSA sig area (well past binding+ed_sig)
    tampered = bytearray(large)
    tampered[FRAME_HDR_SIZE + BINDING_DIGEST_LEN + ED25519_SIG_LEN + 500] ^= 0x01
    assert not verify_large_sig_chain(
        base,
        bytes(tampered),
        ed_pub=ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )


def test_chain_rejects_wrong_ed_pubkey(keypair, signed_base):
    _ed_priv, _ed_pub, _ml_priv, ml_pub = keypair
    _payload, sig_ed, sig_ml, base = signed_base
    large = build_large_sig_frame(
        slot_id=0xFF,
        tag=0xCAFE,
        base_payload=_payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    # Build a different keypair, use its ed_pub.
    other = hybrid_keygen()
    wrong_ed_pub = other[1]
    assert not verify_large_sig_chain(
        base,
        large,
        ed_pub=wrong_ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )


def test_chain_rejects_swapped_base_and_large_sig(keypair, signed_base):
    """An attacker who swaps the two arguments to verify_large_sig_chain
    must be rejected — the chain verifier expects (base, large) in that
    order, and a swap would have the parser fail on the LARGE_SIG
    frame_type check."""
    _ed_priv, ed_pub, _ml_priv, ml_pub = keypair
    _payload, sig_ed, sig_ml, base = signed_base
    large = build_large_sig_frame(
        slot_id=0xFF,
        tag=0xCAFE,
        base_payload=_payload,
        sig_ed=sig_ed,
        sig_mldsa=sig_ml,
        hmac_key=HMAC_KEY,
    )
    # Swap arguments — base passed as large_sig_pkt and vice versa
    assert not verify_large_sig_chain(
        large,
        base,
        ed_pub=ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )


def test_chain_rejects_replay_with_different_base_payload(keypair):
    """The binding_digest is the linkage. A LARGE_SIG built for one
    base frame's payload must NOT validate against a different base
    frame, even if both base frames have valid HMACs."""
    ed_priv, ed_pub, ml_priv, ml_pub = keypair
    payload_a = b"transaction A"
    payload_b = b"transaction B"
    sig_a_ed, sig_a_ml = hybrid_sign(payload_a, ed_priv, ml_priv)
    base_b = build_base_frame(0x01, 0, 0, payload_b, HMAC_KEY)
    # Build LARGE_SIG for payload_a (correct binding), but try to
    # validate it against base_b.
    large_a = build_large_sig_frame(
        slot_id=0,
        tag=0,
        base_payload=payload_a,
        sig_ed=sig_a_ed,
        sig_mldsa=sig_a_ml,
        hmac_key=HMAC_KEY,
    )
    assert not verify_large_sig_chain(
        base_b,
        large_a,
        ed_pub=ed_pub,
        mldsa_pub=ml_pub,
        hmac_key=HMAC_KEY,
    )


# ---------------------------------------------------------------------------
# Engagement marker
# ---------------------------------------------------------------------------


def test_vbus_frame_module_carries_phase_marker():
    import pathlib, re

    src = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "services"
        / "vbus_frame.py"
    ).read_text()
    assert re.search(r"vOS.Adaptive.SHA=[0-9a-f]{7,40}.Phase=P2", src)


def test_vbus_frame_documents_offset_16_invariant():
    """The whole point of the LARGE_SIG extension is to preserve the
    base frame's HMAC at offset 16. The module docstring must say so."""
    import pathlib

    src = (
        (
            pathlib.Path(__file__).resolve().parent.parent.parent
            / "services"
            / "vbus_frame.py"
        )
        .read_text()
        .lower()
    )
    assert "offset 16" in src or "offset-16" in src
    assert "byte-identical" in src or "byte identical" in src
