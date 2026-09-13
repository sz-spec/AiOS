"""
backend/tests/integration/test_spiffe_wit_svid.py

Sprint 15 / Item F1 — SPIFFE WIT-SVID verifier tests.

Coverage:
  - SPIFFE ID parser: valid + malformed + edge cases
  - Trust bundle loader: valid JWKS, missing file, missing keys, missing kid
  - WIT-SVID verify: happy-path roundtrip with Ed25519 key, ES256 key
  - Failures: wrong audience, expired token, kid not in bundle, bad alg
  - Default verifier env fallback
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

# Load the SPIFFE module via explicit file path to avoid colliding with
# the backend/security/ package created in Sprint 15 I6.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPIFFE_PATH = (
    _REPO_ROOT / "backend" / "core" / "security" / "spiffe_workload_identity.py"
)
_spec = importlib.util.spec_from_file_location("vos3_spiffe_under_test", _SPIFFE_PATH)
spiffe = importlib.util.module_from_spec(_spec)
sys.modules["vos3_spiffe_under_test"] = spiffe
_spec.loader.exec_module(spiffe)


# ---------------------------------------------------------------------------
# Helpers — mint trust bundles + sign tokens
# ---------------------------------------------------------------------------


def _make_ed25519_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


def _make_es256_keypair():
    from cryptography.hazmat.primitives.asymmetric.ec import (
        SECP256R1,
        generate_private_key,
    )

    priv = generate_private_key(SECP256R1())
    return priv, priv.public_key()


def _jwk_from_ed25519_pub(pub, kid: str) -> dict:
    from cryptography.hazmat.primitives import serialization

    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "kid": kid,
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
    }


def _jwk_from_es256_pub(pub, kid: str) -> dict:
    nums = pub.public_numbers()
    x = nums.x.to_bytes(32, "big")
    y = nums.y.to_bytes(32, "big")
    return {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "kid": kid,
        "x": base64.urlsafe_b64encode(x).rstrip(b"=").decode("ascii"),
        "y": base64.urlsafe_b64encode(y).rstrip(b"=").decode("ascii"),
    }


def _sign_jwt(priv_key, header: dict, claims: dict) -> str:
    import jwt as pyjwt

    return pyjwt.encode(claims, priv_key, algorithm=header["alg"], headers=header)


# ---------------------------------------------------------------------------
# SPIFFE ID parser
# ---------------------------------------------------------------------------


def test_parse_simple_spiffe_id():
    sid = spiffe.parse_spiffe_id("spiffe://example.org/ns/default/sa/agent-1")
    assert sid.trust_domain == "example.org"
    assert sid.path == "ns/default/sa/agent-1"
    assert sid.full_uri == "spiffe://example.org/ns/default/sa/agent-1"


def test_parse_trust_domain_only():
    sid = spiffe.parse_spiffe_id("spiffe://vos.dev")
    assert sid.trust_domain == "vos.dev"
    assert sid.path == ""


def test_parse_rejects_non_spiffe_scheme():
    with pytest.raises(spiffe.SPIFFEMalformedIDError):
        spiffe.parse_spiffe_id("https://example.org/x")


def test_parse_rejects_empty_string():
    with pytest.raises(spiffe.SPIFFEMalformedIDError):
        spiffe.parse_spiffe_id("")


def test_parse_rejects_uppercase_trust_domain():
    """SPIFFE IDs are case-sensitive; trust-domain must be lowercase."""
    with pytest.raises(spiffe.SPIFFEMalformedIDError):
        spiffe.parse_spiffe_id("spiffe://Example.org/x")


# ---------------------------------------------------------------------------
# Trust bundle loader
# ---------------------------------------------------------------------------


def test_load_bundle_missing_file(tmp_path):
    with pytest.raises(spiffe.SPIFFEVerificationError, match="not found"):
        spiffe.SPIFFEWITVerifier.from_trust_bundle(tmp_path / "no-bundle.json")


def test_load_bundle_invalid_json(tmp_path):
    p = tmp_path / "bundle.json"
    p.write_text("{not valid", encoding="utf-8")
    with pytest.raises(spiffe.SPIFFEVerificationError, match="not valid JSON"):
        spiffe.SPIFFEWITVerifier.from_trust_bundle(p)


def test_load_bundle_no_keys_array(tmp_path):
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps({"keys": []}), encoding="utf-8")
    with pytest.raises(spiffe.SPIFFEVerificationError, match="no 'keys'"):
        spiffe.SPIFFEWITVerifier.from_trust_bundle(p)


def test_load_bundle_keys_missing_kid(tmp_path):
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps({"keys": [{"kty": "OKP"}]}), encoding="utf-8")
    with pytest.raises(spiffe.SPIFFEVerificationError, match="kid"):
        spiffe.SPIFFEWITVerifier.from_trust_bundle(p)


# ---------------------------------------------------------------------------
# Happy-path WIT-SVID verify
# ---------------------------------------------------------------------------


@pytest.fixture
def ed25519_bundle(tmp_path):
    priv, pub = _make_ed25519_keypair()
    kid = "vos3-test-ed25519-001"
    jwk = _jwk_from_ed25519_pub(pub, kid)
    p = tmp_path / "ed25519_bundle.json"
    p.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
    return priv, pub, kid, p


@pytest.fixture
def es256_bundle(tmp_path):
    priv, pub = _make_es256_keypair()
    kid = "vos3-test-es256-001"
    jwk = _jwk_from_es256_pub(pub, kid)
    p = tmp_path / "es256_bundle.json"
    p.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
    return priv, pub, kid, p


def test_ed25519_wit_svid_happy_path(ed25519_bundle):
    priv, _pub, kid, bundle_path = ed25519_bundle
    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/ns/agents/sa/sprint15-worker",
        "iss": "spiffe://vos.dev",
        "aud": ["vos3://api/agents"],
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "wit": {
            "selectors": ["k8s:ns:agents", "k8s:sa:sprint15-worker"],
            "node_attestation": "k8s_sat",
        },
    }
    token = _sign_jwt(priv, {"alg": "EdDSA", "kid": kid}, claims)

    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(
        bundle_path, expected_audience="vos3://api/agents"
    )
    identity = verifier.verify(token)

    assert identity.spiffe_id.trust_domain == "vos.dev"
    assert identity.spiffe_id.path == "ns/agents/sa/sprint15-worker"
    assert identity.expires_at == now + 3600
    assert "vos3://api/agents" in identity.audience
    assert identity.node_attestation == "k8s_sat"
    assert "k8s:ns:agents" in identity.selectors


def test_es256_wit_svid_happy_path(es256_bundle):
    priv, _pub, kid, bundle_path = es256_bundle
    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/ns/agents/sa/sprint15-worker",
        "aud": "vos3://api/agents",  # single string variant
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    token = _sign_jwt(priv, {"alg": "ES256", "kid": kid}, claims)

    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(
        bundle_path, expected_audience="vos3://api/agents"
    )
    identity = verifier.verify(token)
    assert (
        identity.spiffe_id.full_uri == "spiffe://vos.dev/ns/agents/sa/sprint15-worker"
    )


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_expired_token_rejected(ed25519_bundle):
    priv, _pub, kid, bundle_path = ed25519_bundle
    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/ns/agents/sa/expired",
        "iat": now - 7200,
        "nbf": now - 7200,
        "exp": now - 3600,
    }
    token = _sign_jwt(priv, {"alg": "EdDSA", "kid": kid}, claims)
    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(bundle_path)
    with pytest.raises(spiffe.SPIFFEVerificationError, match="expired"):
        verifier.verify(token)


def test_wrong_audience_rejected(ed25519_bundle):
    priv, _pub, kid, bundle_path = ed25519_bundle
    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/ns/agents/sa/wrong-aud",
        "aud": "vos3://api/wrong-target",
        "iat": now,
        "exp": now + 600,
    }
    token = _sign_jwt(priv, {"alg": "EdDSA", "kid": kid}, claims)
    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(
        bundle_path, expected_audience="vos3://api/agents"
    )
    with pytest.raises(spiffe.SPIFFEVerificationError, match="audience"):
        verifier.verify(token)


def test_kid_not_in_bundle_rejected(ed25519_bundle):
    priv, _pub, _kid, bundle_path = ed25519_bundle
    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/ns/agents/sa/unknown-kid",
        "iat": now,
        "exp": now + 600,
    }
    # Sign with the real key but a fake kid in the header — the bundle
    # lookup will miss.
    token = _sign_jwt(priv, {"alg": "EdDSA", "kid": "totally-fake-kid"}, claims)
    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(bundle_path)
    with pytest.raises(spiffe.SPIFFEVerificationError, match="not in trust bundle"):
        verifier.verify(token)


def test_unsupported_alg_rejected(ed25519_bundle):
    """SPIFFE allows only ES256 and EdDSA. HS256 (symmetric) must be refused."""
    _priv, _pub, kid, bundle_path = ed25519_bundle
    import jwt as pyjwt

    now = int(time.time())
    claims = {
        "sub": "spiffe://vos.dev/agent",
        "iat": now,
        "exp": now + 600,
    }
    token = pyjwt.encode(
        claims, "shared-secret", algorithm="HS256", headers={"kid": kid}
    )
    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(bundle_path)
    with pytest.raises(spiffe.SPIFFEVerificationError, match="unsupported alg"):
        verifier.verify(token)


def test_malformed_jwt_rejected(ed25519_bundle):
    _priv, _pub, _kid, bundle_path = ed25519_bundle
    verifier = spiffe.SPIFFEWITVerifier.from_trust_bundle(bundle_path)
    with pytest.raises(spiffe.SPIFFEVerificationError, match="3-segment"):
        verifier.verify("not.a.jwt.with.too.many.dots")
    with pytest.raises(spiffe.SPIFFEVerificationError, match="3-segment"):
        verifier.verify("only-one-segment")


def test_default_verifier_unset_returns_none(monkeypatch):
    monkeypatch.delenv("VOS3_SPIFFE_TRUST_BUNDLE_PATH", raising=False)
    spiffe.reset_for_tests()
    assert spiffe.get_default_verifier() is None


def test_default_verifier_from_env(monkeypatch, ed25519_bundle):
    _priv, _pub, _kid, bundle_path = ed25519_bundle
    monkeypatch.setenv("VOS3_SPIFFE_TRUST_BUNDLE_PATH", str(bundle_path))
    monkeypatch.setenv("VOS3_SPIFFE_EXPECTED_AUDIENCE", "vos3://api/agents")
    spiffe.reset_for_tests()
    v = spiffe.get_default_verifier()
    assert v is not None
    assert v.expected_audience == "vos3://api/agents"
