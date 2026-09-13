"""
Biometric / Ed25519 signature forgery — host-bridge approval surface.

The host-bridge approve path verifies an Ed25519 signature over a
canonical manifest of the held call. These tests extend the
adversarial signature-forgery suite by focusing on the **key-not-in-
keyring** axis the spec calls "Biometric Forge":

  * a valid Ed25519 signature signed by a key the keyring has never
    seen must not verify against any keyring-stored public key,
  * the canonical-bytes function must be deterministic so verifiers
    don't accept stale signatures over malleable JSON,
  * pubkey/signature/message tampers must all yield False (never True
    or unhandled exception).
"""

from __future__ import annotations


import pytest

from services.app_crypto import (
    canonical_manifest_bytes,
    generate_keypair_hex,
    sign_manifest,
    verify_manifest_signature,
)

# ---------------------------------------------------------------------------
# Fresh keypair never in keyring — every forged signature rejects
# ---------------------------------------------------------------------------


def _kp():
    return generate_keypair_hex()


@pytest.mark.parametrize("iteration", list(range(15)))
def test_fresh_keypair_not_in_keyring_rejects(perms_env, iteration):
    """Sign with key A, verify against key B (both fresh). Must fail."""
    priv_a, _pub_a = _kp()
    _, pub_b = _kp()
    payload = {"op": "deploy", "iter": iteration}
    sig = sign_manifest(payload, private_key_hex=priv_a)
    msg = canonical_manifest_bytes(payload)
    assert verify_manifest_signature(msg, sig, pub_b) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"approval_id": "a1"},
        {"approval_id": "a1", "decision": "approve"},
        {"approval_id": "a2", "decision": "approve", "user_id": "u_42"},
        {"op": "spawn", "model": "claude-opus", "memory_mb": 4096},
        {"sensitive": True, "tier": "platinum"},
    ],
)
def test_unknown_signer_rejects_for_each_shape(perms_env, payload):
    priv_unknown, _ = _kp()
    _, pub_keyring = _kp()
    sig = sign_manifest(payload, private_key_hex=priv_unknown)
    msg = canonical_manifest_bytes(payload)
    assert verify_manifest_signature(msg, sig, pub_keyring) is False


# ---------------------------------------------------------------------------
# Canonical-bytes determinism — same dict, same bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"a": 1, "b": 2},
        {"b": 2, "a": 1},
        {"x": {"y": {"z": 1}}},
        {"list": [1, 2, 3]},
        {"approval_id": "k1", "ts": 1700000000},
    ],
)
def test_canonical_bytes_deterministic(payload):
    assert canonical_manifest_bytes(payload) == canonical_manifest_bytes(payload)


@pytest.mark.parametrize(
    "a,b",
    [
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
        ({"x": 1, "y": 2, "z": 3}, {"z": 3, "y": 2, "x": 1}),
    ],
)
def test_canonical_bytes_independent_of_key_order(a, b):
    assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)


# ---------------------------------------------------------------------------
# Bit-flips in signature, pubkey, message — all must fail verify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("byte_idx", list(range(0, 64, 2)))  # 32 cases
def test_signature_bit_flip_rejected(byte_idx):
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})
    raw = bytearray(bytes.fromhex(sig))
    raw[byte_idx] ^= 0x01
    assert verify_manifest_signature(msg, raw.hex(), pub) is False


@pytest.mark.parametrize("byte_idx", list(range(0, 32, 2)))  # 16 cases
def test_pubkey_bit_flip_rejected(byte_idx):
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})
    raw = bytearray(bytes.fromhex(pub))
    raw[byte_idx] ^= 0x01
    assert verify_manifest_signature(msg, sig, raw.hex()) is False


def test_truncated_signature_rejected():
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})
    truncated = sig[:-2]  # drop last hex pair
    assert verify_manifest_signature(msg, truncated, pub) is False


def test_truncated_pubkey_rejected():
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})
    assert verify_manifest_signature(msg, sig, pub[:-2]) is False


def test_extra_garbage_appended_to_signature_rejected():
    priv, pub = _kp()
    sig = sign_manifest({"k": 1}, private_key_hex=priv)
    msg = canonical_manifest_bytes({"k": 1})
    assert verify_manifest_signature(msg, sig + "ff", pub) is False


def test_message_replacement_rejected():
    """Sig over A, verify against canonical bytes of B — must fail."""
    priv, pub = _kp()
    sig = sign_manifest({"m": "alpha"}, private_key_hex=priv)
    msg_b = canonical_manifest_bytes({"m": "beta"})
    assert verify_manifest_signature(msg_b, sig, pub) is False


@pytest.mark.parametrize("mutation_field", ["op", "user_id", "ts", "decision"])
def test_partial_payload_mutation_rejected(mutation_field):
    priv, pub = _kp()
    original = {"op": "approve", "user_id": "u1", "ts": 1, "decision": "yes"}
    sig = sign_manifest(original, private_key_hex=priv)
    mutated = dict(original)
    mutated[mutation_field] = "mutated"
    msg_mutated = canonical_manifest_bytes(mutated)
    assert verify_manifest_signature(msg_mutated, sig, pub) is False


# ---------------------------------------------------------------------------
# Cross-key forge — sig with key A under message from key B's signer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iteration", list(range(5)))
def test_cross_signer_signatures_dont_interchange(iteration):
    priv_a, pub_a = _kp()
    priv_b, pub_b = _kp()
    payload = {"k": iteration}
    sig_a = sign_manifest(payload, private_key_hex=priv_a)
    msg = canonical_manifest_bytes(payload)
    # sig_a verifies under pub_a but not pub_b.
    assert verify_manifest_signature(msg, sig_a, pub_a) is True
    assert verify_manifest_signature(msg, sig_a, pub_b) is False
