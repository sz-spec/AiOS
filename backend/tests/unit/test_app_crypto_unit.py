"""
Stage 1 · Atomic unit isolation for services/app_crypto.py.

Covers — purpose | guards file:line:
  test_canonical_bytes_sort_keys                  | app_crypto.py:115-134
  test_canonical_bytes_separator_compactness      | app_crypto.py:115-134
  test_canonical_bytes_unicode_passthrough        | app_crypto.py:115-134
  test_canonical_bytes_rejects_non_dict           | app_crypto.py:127
  test_manifest_digest_is_sha256_hex              | app_crypto.py:137-141
  test_generate_keypair_hex_lengths               | app_crypto.py:170-189
  test_generate_keypair_unique_per_call           | app_crypto.py:170-189
  test_sign_manifest_rejects_non_hex_priv         | app_crypto.py:192-208
  test_sign_manifest_rejects_wrong_length_priv    | app_crypto.py:203
  test_verify_manifest_signature_rejects_non_bytes| app_crypto.py:231
  test_verify_manifest_signature_rejects_empty_sig| app_crypto.py:233-236
  test_verify_manifest_signature_rejects_bad_hex  | app_crypto.py:240-245
  test_verify_manifest_signature_rejects_wrong_pub_length | app_crypto.py:244-245
  test_verify_manifest_signature_rejects_wrong_sig_length | app_crypto.py:246-248
  test_verify_manifest_signature_happy_path       | app_crypto.py:250-263
  test_verify_manifest_signature_invalid_returns_false | app_crypto.py:264-265
  test_verify_manifest_dict_rejects_missing_sig_or_pub | app_crypto.py:284
  test_ed25519_S_ge_L_non_canonical_rejected      | CVE-2026-33895 class

Maps to spring-2026 disclosure: CVE-2026-33895 (Ed25519 signature
malleability — node-forge accepted S >= L variants; OpenSSL backend
rejects them). We rely on the cryptography lib's OpenSSL backend
through pyca, so the test asserts the rejection happens.
"""

from __future__ import annotations


import pytest

# ---------------------------------------------------------------------------
# canonical_manifest_bytes
# ---------------------------------------------------------------------------


def test_canonical_bytes_sort_keys():
    from services.app_crypto import canonical_manifest_bytes

    a = canonical_manifest_bytes({"b": 1, "a": 2})
    b = canonical_manifest_bytes({"a": 2, "b": 1})
    assert a == b
    # Deterministic shape — keys sorted alphabetically.
    assert b.decode("utf-8").startswith('{"a":2,"b":1}')


def test_canonical_bytes_separator_compactness():
    from services.app_crypto import canonical_manifest_bytes

    out = canonical_manifest_bytes({"x": 1, "y": 2})
    # No spaces — the (",", ":") separator contract is what makes
    # the bytes byte-stable across Python/Node/Go JSON emitters.
    assert b": " not in out
    assert b", " not in out


def test_canonical_bytes_unicode_passthrough():
    from services.app_crypto import canonical_manifest_bytes

    out = canonical_manifest_bytes({"name": "vOS — café"})
    # `ensure_ascii=False` — characters preserved literally.
    assert "café".encode("utf-8") in out
    assert "—".encode("utf-8") in out


def test_canonical_bytes_rejects_non_dict():
    from services.app_crypto import canonical_manifest_bytes

    with pytest.raises(ValueError):
        canonical_manifest_bytes([1, 2, 3])  # type: ignore[arg-type]


def test_canonical_bytes_byte_stability_across_runs():
    """Same dict, multiple calls — bytes identical."""
    from services.app_crypto import canonical_manifest_bytes

    m = {"workspace_id": "ws", "manifest_json": '{"v":1}', "run_id": "r"}
    assert canonical_manifest_bytes(m) == canonical_manifest_bytes(m)


# ---------------------------------------------------------------------------
# manifest_digest_sha256
# ---------------------------------------------------------------------------


def test_manifest_digest_is_sha256_hex():
    from services.app_crypto import manifest_digest_sha256

    d = manifest_digest_sha256({"x": 1})
    assert len(d) == 64
    assert all(c in "0123456789abcdef" for c in d)


# ---------------------------------------------------------------------------
# generate_keypair_hex
# ---------------------------------------------------------------------------


def test_generate_keypair_hex_lengths():
    from services.app_crypto import generate_keypair_hex

    priv_hex, pub_hex = generate_keypair_hex()
    assert len(priv_hex) == 64
    assert len(pub_hex) == 64


def test_generate_keypair_unique_per_call():
    from services.app_crypto import generate_keypair_hex

    kp1 = generate_keypair_hex()
    kp2 = generate_keypair_hex()
    # Random keys — collision is astronomically unlikely.
    assert kp1 != kp2


# ---------------------------------------------------------------------------
# sign_manifest
# ---------------------------------------------------------------------------


def test_sign_manifest_rejects_non_hex_priv():
    from services.app_crypto import sign_manifest

    with pytest.raises(ValueError):
        sign_manifest({"a": 1}, private_key_hex="not-hex")


def test_sign_manifest_rejects_wrong_length_priv():
    from services.app_crypto import sign_manifest

    with pytest.raises(ValueError):
        sign_manifest({"a": 1}, private_key_hex="ab" * 16)  # 32 chars = 16 bytes


def test_sign_manifest_signature_length_64_bytes():
    """Ed25519 sigs are always exactly 64 bytes (128 hex chars)."""
    from services.app_crypto import generate_keypair_hex, sign_manifest

    priv_hex, _ = generate_keypair_hex()
    sig = sign_manifest({"a": 1}, private_key_hex=priv_hex)
    assert len(sig) == 128
    assert all(c in "0123456789abcdef" for c in sig)


# ---------------------------------------------------------------------------
# verify_manifest_signature
# ---------------------------------------------------------------------------


def test_verify_manifest_signature_rejects_non_bytes():
    from services.app_crypto import verify_manifest_signature

    assert verify_manifest_signature("not-bytes", "ab" * 64, "ab" * 32) is False  # type: ignore[arg-type]


def test_verify_manifest_signature_rejects_empty_sig():
    from services.app_crypto import verify_manifest_signature

    assert verify_manifest_signature(b"payload", "", "ab" * 32) is False


def test_verify_manifest_signature_rejects_empty_pub():
    from services.app_crypto import verify_manifest_signature

    assert verify_manifest_signature(b"payload", "ab" * 64, "") is False


def test_verify_manifest_signature_rejects_bad_hex():
    from services.app_crypto import verify_manifest_signature

    assert verify_manifest_signature(b"payload", "zz" * 64, "ab" * 32) is False


def test_verify_manifest_signature_rejects_wrong_pub_length():
    from services.app_crypto import verify_manifest_signature

    # 33 bytes — Ed25519 demands exactly 32.
    assert verify_manifest_signature(b"payload", "ab" * 64, "ab" * 33) is False


def test_verify_manifest_signature_rejects_wrong_sig_length():
    from services.app_crypto import verify_manifest_signature

    # 63 bytes — Ed25519 demands exactly 64.
    assert verify_manifest_signature(b"payload", "ab" * 63, "ab" * 32) is False


def test_verify_manifest_signature_happy_path():
    from services.app_crypto import (
        canonical_manifest_bytes,
        generate_keypair_hex,
        sign_manifest,
        verify_manifest_signature,
    )

    priv_hex, pub_hex = generate_keypair_hex()
    manifest = {"x": 42}
    sig = sign_manifest(manifest, private_key_hex=priv_hex)
    assert (
        verify_manifest_signature(
            canonical_manifest_bytes(manifest),
            sig,
            pub_hex,
        )
        is True
    )


def test_verify_manifest_signature_invalid_returns_false():
    """A valid-shape but wrong signature returns False, doesn't raise."""
    from services.app_crypto import (
        canonical_manifest_bytes,
        generate_keypair_hex,
        verify_manifest_signature,
    )

    _, pub_hex = generate_keypair_hex()
    # Random-but-shape-correct signature → InvalidSignature → False.
    fake_sig = "00" * 64
    assert (
        verify_manifest_signature(
            canonical_manifest_bytes({"a": 1}),
            fake_sig,
            pub_hex,
        )
        is False
    )


# ---------------------------------------------------------------------------
# verify_manifest_dict
# ---------------------------------------------------------------------------


def test_verify_manifest_dict_rejects_missing_sig_or_pub():
    from services.app_crypto import verify_manifest_dict

    assert (
        verify_manifest_dict({"a": 1}, signature_hex=None, public_key_hex="ab" * 32)
        is False
    )
    assert (
        verify_manifest_dict({"a": 1}, signature_hex="ab" * 64, public_key_hex=None)
        is False
    )
    assert (
        verify_manifest_dict({"a": 1}, signature_hex=None, public_key_hex=None) is False
    )


def test_verify_manifest_dict_round_trip():
    from services.app_crypto import (
        generate_keypair_hex,
        sign_manifest,
        verify_manifest_dict,
    )

    priv, pub = generate_keypair_hex()
    m = {"name": "x", "v": 1}
    sig = sign_manifest(m, private_key_hex=priv)
    assert verify_manifest_dict(m, signature_hex=sig, public_key_hex=pub) is True


def test_verify_manifest_dict_tampered_returns_false():
    from services.app_crypto import (
        generate_keypair_hex,
        sign_manifest,
        verify_manifest_dict,
    )

    priv, pub = generate_keypair_hex()
    sig = sign_manifest({"v": 1}, private_key_hex=priv)
    # Same key, different manifest → should fail.
    assert (
        verify_manifest_dict(
            {"v": 2},
            signature_hex=sig,
            public_key_hex=pub,
        )
        is False
    )


# ---------------------------------------------------------------------------
# CVE-2026-33895 class — Ed25519 signature malleability (S >= L)
# ---------------------------------------------------------------------------


def test_ed25519_S_ge_L_non_canonical_rejected():
    """node-forge accepted non-canonical signatures where S >= L (the
    Ed25519 group order). pyca/cryptography uses the OpenSSL backend
    which enforces S < L per RFC 8032. We assert that adding L to S
    produces an INVALID signature.

    L (Ed25519 group order) = 2^252 + 27742317777372353535851937790883648493
    For a valid sig (R, S), the variant (R, S + L mod 2^256) is
    cryptographically distinct and OpenSSL must reject it."""
    from services.app_crypto import (
        canonical_manifest_bytes,
        generate_keypair_hex,
        sign_manifest,
        verify_manifest_signature,
    )

    priv, pub = generate_keypair_hex()
    sig_hex = sign_manifest({"v": 1}, private_key_hex=priv)
    # Sig is 64 bytes: R (first 32) || S (last 32), little-endian.
    sig_bytes = bytes.fromhex(sig_hex)
    R, S = sig_bytes[:32], sig_bytes[32:]
    S_int = int.from_bytes(S, "little")
    L = (1 << 252) + 27742317777372353535851937790883648493
    S_plus_L = (S_int + L) & ((1 << 256) - 1)  # 32-byte truncation
    forged_S = S_plus_L.to_bytes(32, "little")
    forged_sig_hex = (R + forged_S).hex()
    canon = canonical_manifest_bytes({"v": 1})
    # The original sig MUST verify (sanity check).
    assert verify_manifest_signature(canon, sig_hex, pub) is True
    # The S+L variant MUST NOT verify on OpenSSL backend.
    assert verify_manifest_signature(canon, forged_sig_hex, pub) is False
