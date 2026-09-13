"""
Stage 4 · Signature forgery + Ed25519 malleability fuzz.

Targets the workflow + manifest signing surface for forgery, replay,
and the Ed25519 S>=L non-canonical-signature class (CVE-2026-33895).

Each test asserts that `verify_manifest_signature` returns False
(never True or raises an unhandled exception) for an attacker-
crafted input.
"""

from __future__ import annotations

import pytest

from services.app_crypto import (
    canonical_manifest_bytes,
    generate_keypair_hex,
    sign_manifest,
    verify_manifest_signature,
)


def _kp():
    return generate_keypair_hex()


# ---------------------------------------------------------------------------
# Wrong public key — bog standard
# ---------------------------------------------------------------------------


def test_wrong_public_key_rejected(adv_env):
    priv_a, pub_a = _kp()
    _, pub_b = _kp()  # different keypair
    sig = sign_manifest({"x": 1}, private_key_hex=priv_a)
    msg = canonical_manifest_bytes({"x": 1})
    # Verifying against pub_b should fail.
    assert verify_manifest_signature(msg, sig, pub_b) is False


# ---------------------------------------------------------------------------
# Bit-flips in the signature — 64 byte ed25519 signature → 64 flip points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("byte_idx", list(range(0, 64, 4)))  # every 4th byte
def test_signature_bit_flip_rejected(adv_env, byte_idx):
    priv, pub = _kp()
    sig = sign_manifest({"x": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"x": 1})
    raw = bytearray(bytes.fromhex(sig))
    raw[byte_idx] ^= 0x01  # flip 1 bit
    tampered_sig = raw.hex()
    assert verify_manifest_signature(msg, tampered_sig, pub) is False


# ---------------------------------------------------------------------------
# CVE-2026-33895 — Ed25519 S>=L non-canonical signature
# ---------------------------------------------------------------------------


def test_ed25519_S_ge_L_non_canonical_rejected(adv_env):
    """Forge a signature where S is replaced with S + L (the group order).

    Mathematically S+L is a valid scalar for a multi-of-L multiplication;
    cryptographically RFC 8032 §5.1.7 step 3 mandates rejection. The
    OpenSSL backend in `cryptography` enforces this — if it ever
    regresses we'd accept double-spend / replay attacks."""
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})

    # Ed25519 group order L (little-endian, 32 bytes).
    L_le = bytes.fromhex(
        "edd3f55c1a631258d69cf7a2def9de1400000000000000000000000000000010"
    )
    sig_raw = bytes.fromhex(sig)
    R = sig_raw[:32]
    S = sig_raw[32:]

    # Add L to S (little-endian add, mod 2^256).
    S_int = int.from_bytes(S, "little")
    L_int = int.from_bytes(L_le, "little")
    S_plus_L = (S_int + L_int) % (1 << 256)
    new_S = S_plus_L.to_bytes(32, "little")
    forged = (R + new_S).hex()

    assert verify_manifest_signature(msg, forged, pub) is False


# ---------------------------------------------------------------------------
# Signature swap — sig from message A used on message B
# ---------------------------------------------------------------------------


def test_signature_swap_across_messages_rejected(adv_env):
    priv, pub = _kp()
    sig_a = sign_manifest({"m": "A"}, private_key_hex=priv)
    msg_b = canonical_manifest_bytes({"m": "B"})
    assert verify_manifest_signature(msg_b, sig_a, pub) is False


# ---------------------------------------------------------------------------
# Wrong byte forms
# ---------------------------------------------------------------------------


def test_non_bytes_message_rejected(adv_env):
    priv, pub = _kp()
    sig = sign_manifest({"x": 1}, private_key_hex=priv)
    # Pass a string instead of bytes — must return False, not raise.
    assert verify_manifest_signature("not-bytes", sig, pub) is False  # type: ignore[arg-type]


def test_non_string_signature_rejected(adv_env):
    _, pub = _kp()
    msg = canonical_manifest_bytes({"x": 1})
    assert verify_manifest_signature(msg, 12345, pub) is False  # type: ignore[arg-type]


def test_non_hex_signature_rejected(adv_env):
    _, pub = _kp()
    msg = canonical_manifest_bytes({"x": 1})
    assert verify_manifest_signature(msg, "not-hex-zz!", pub) is False


def test_wrong_length_signature_rejected(adv_env):
    _, pub = _kp()
    msg = canonical_manifest_bytes({"x": 1})
    # 32-byte sig (half) → reject.
    assert verify_manifest_signature(msg, "00" * 32, pub) is False
    # 80-byte sig → reject.
    assert verify_manifest_signature(msg, "ab" * 80, pub) is False


def test_wrong_length_public_key_rejected(adv_env):
    priv, _ = _kp()
    sig = sign_manifest({"x": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"x": 1})
    # 16-byte pub → reject.
    assert verify_manifest_signature(msg, sig, "00" * 16) is False
    # 64-byte pub → reject.
    assert verify_manifest_signature(msg, sig, "ab" * 64) is False


def test_empty_signature_rejected(adv_env):
    _, pub = _kp()
    msg = canonical_manifest_bytes({"x": 1})
    assert verify_manifest_signature(msg, "", pub) is False


def test_empty_public_key_rejected(adv_env):
    priv, _ = _kp()
    sig = sign_manifest({"x": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"x": 1})
    assert verify_manifest_signature(msg, sig, "") is False


# ---------------------------------------------------------------------------
# All-zeros signature / pubkey
# ---------------------------------------------------------------------------


def test_all_zero_signature_rejected(adv_env):
    _, pub = _kp()
    msg = canonical_manifest_bytes({"x": 1})
    assert verify_manifest_signature(msg, "00" * 64, pub) is False


def test_all_zero_pubkey_rejected(adv_env):
    priv, _ = _kp()
    sig = sign_manifest({"x": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"x": 1})
    # Identity element pubkey — Ed25519 verify rejects.
    assert verify_manifest_signature(msg, sig, "00" * 32) is False
