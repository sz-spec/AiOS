"""
backend/tests/crypto/test_hybrid_kex.py
========================================

Sprint 14.1 / Gap 2 — hybrid X25519 + ML-KEM-768 KEX correctness tests.

The tests fall into three groups:

  1. Size + wire-format invariants — always run. Verify the constants and
     serialization roundtrips that mirror the IETF draft. These run
     without any PQ library and catch the cheap mistakes.

  2. PQ round-trip tests — gated on ``hybrid_kex.is_available()``. If the
     ``oqs`` Python binding is not installed (CI without liboqs, dev
     laptops without it), these are SKIPPED with a clear marker. The
     skip is honest: the test cannot run, not "passed."

  3. Negative tests — always run. Confirm that mis-sized inputs raise
     HybridKexDecodeError and that the unavailable backend raises
     HybridKexUnavailable.
"""

from __future__ import annotations

import pytest

from services import hybrid_kex as hk

# ---------------------------------------------------------------------------
# 1. Size invariants — never skipped
# ---------------------------------------------------------------------------


def test_size_constants_match_ietf_draft():
    # Per draft-ietf-tls-ecdhe-mlkem-04, ML-KEM-768 numbers from FIPS 203.
    assert hk.X25519_PUB_BYTES == 32
    assert hk.MLKEM768_PK_BYTES == 1184
    assert hk.MLKEM768_SK_BYTES == 2400
    assert hk.MLKEM768_CT_BYTES == 1088
    assert hk.MLKEM768_SS_BYTES == 32

    assert hk.HYBRID_ENCAP_KEY_BYTES == 32 + 1184  # 1216
    assert hk.HYBRID_CIPHER_BYTES == 32 + 1088  # 1120
    assert hk.HYBRID_SS_BYTES == 32 + 32  # 64


def test_status_dict_shape():
    s = hk.status()
    assert "backend" in s
    assert "available" in s
    assert "sizes" in s
    assert s["ietf_codepoint"] == 0x11EC
    assert s["spec"] == "draft-ietf-tls-ecdhe-mlkem-04"


def test_cipher_wire_roundtrip_size_check():
    blob = b"\x00" * hk.HYBRID_CIPHER_BYTES
    parsed = hk.HybridCipher.from_wire(blob)
    assert len(parsed.x25519_peer_pub) == 32
    assert len(parsed.mlkem_ct) == 1088
    assert parsed.to_wire() == blob


def test_cipher_wire_roundtrip_rejects_bad_size():
    with pytest.raises(hk.HybridKexDecodeError):
        hk.HybridCipher.from_wire(b"\x00" * 1119)


# ---------------------------------------------------------------------------
# 2. PQ round-trip — skipped if oqs unavailable
# ---------------------------------------------------------------------------

requires_oqs = pytest.mark.skipif(
    not hk.is_available(),
    reason="liboqs Python binding not installed; install with `pip install oqs`",
)


@requires_oqs
def test_hybrid_keygen_sizes():
    kp = hk.hybrid_keygen()
    assert len(kp.x25519_priv) == 32
    assert len(kp.x25519_pub) == 32
    assert len(kp.mlkem_pk) == 1184
    assert len(kp.mlkem_sk) == 2400
    assert len(kp.encap_key()) == 1216


@requires_oqs
def test_hybrid_kex_roundtrip_64_byte_secret():
    """End-to-end: initiator + responder derive the SAME 64-byte secret."""
    alice = hk.hybrid_keygen()
    bob_shared, cipher = hk.hybrid_encap(alice.encap_key())
    alice_shared = hk.hybrid_decap(alice, cipher)

    assert alice_shared == bob_shared
    assert len(alice_shared) == 64
    # The first 32 bytes are the ML-KEM secret, the last 32 are X25519.
    # Confirm both halves are non-zero (i.e., the KEX actually ran).
    assert alice_shared[:32] != b"\x00" * 32
    assert alice_shared[32:] != b"\x00" * 32


@requires_oqs
def test_hybrid_kex_independence():
    """Two independent runs produce independent secrets — sanity check."""
    a1 = hk.hybrid_keygen()
    a2 = hk.hybrid_keygen()
    ss1, _ = hk.hybrid_encap(a1.encap_key())
    ss2, _ = hk.hybrid_encap(a2.encap_key())
    assert ss1 != ss2


@requires_oqs
def test_hybrid_kex_decap_rejects_mismatched_cipher():
    """A cipher generated for Alice cannot be decapped by Carol."""
    alice = hk.hybrid_keygen()
    carol = hk.hybrid_keygen()
    _, cipher_for_alice = hk.hybrid_encap(alice.encap_key())

    # Decapping with Carol's keys should produce a DIFFERENT secret
    # (ML-KEM doesn't raise on a wrong-key decap; it returns a
    # deterministic pseudo-random secret per the FIPS 203 spec — a
    # property called "implicit rejection". But the secret differs
    # from the one Alice would derive, which is what matters.)
    alice_secret = hk.hybrid_decap(alice, cipher_for_alice)
    carol_secret = hk.hybrid_decap(carol, cipher_for_alice)
    assert alice_secret != carol_secret


# ---------------------------------------------------------------------------
# 3. Negative tests — always run
# ---------------------------------------------------------------------------


def test_hybrid_encap_rejects_bad_encap_key_size():
    with pytest.raises(hk.HybridKexDecodeError):
        hk.hybrid_encap(b"\x00" * 1215)
    with pytest.raises(hk.HybridKexDecodeError):
        hk.hybrid_encap(b"\x00" * 1217)


def test_classical_x25519_fallback_always_works():
    """When PQ is unavailable, callers can use classical KEX."""
    a_priv, a_pub, ss = hk.x25519_only_kex()
    assert len(a_priv) == 32
    assert len(a_pub) == 32
    assert len(ss) == 32
    assert ss != b"\x00" * 32


def test_pq_unavailable_raises_clear_error(monkeypatch):
    """When oqs is unavailable, hybrid_keygen raises HybridKexUnavailable
    with an actionable message."""
    monkeypatch.setattr(
        hk, "_backend_cache", hk._BackendInfo(name="unavailable", available=False)
    )
    try:
        with pytest.raises(hk.HybridKexUnavailable, match=r"pip install oqs"):
            hk.hybrid_keygen()
    finally:
        hk._reset_probe_for_tests()


def test_backend_name_is_one_of_three():
    name = hk.backend_name()
    assert name in {"oqs_avx2", "oqs_scalar", "unavailable"}
