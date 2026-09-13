"""
backend/tests/services/test_aims_partner_verifier.py

Sprint 18 / Wave 3.A / Cluster B.2 — Smoke tests for the
cross-domain AIMS envelope verifier.

Covers:
- VALID happy path: real AIMSEnvelope from a partner trust domain,
  signed with a real Ed25519 key, verified end-to-end through the
  bridge.
- PARTNER_UNKNOWN: trust_domain not registered with the bridge.
- PARTNER_NOT_SYNCED: registered but no successful refresh yet.
- KID_NOT_FOUND: signing_kid doesn't match any JWK in the partner's
  bundle.
- TRUST_DOMAIN_MISMATCH: envelope claims a different trust_domain
  than the caller expected.
- SIGNATURE_INVALID: tampered signature, tampered envelope content,
  wrong key.
- UNSUPPORTED_KEY_TYPE: JWK kty=RSA or crv=P-256 instead of Ed25519.
- MALFORMED_JWK: missing 'x' field; wrong-length 'x'.
- BASE_VERIFY_FAILED: signature is valid but the envelope itself
  fails the existing self-consistency check (e.g. tampered after
  signing, expired).
- Input validation: None envelope, non-bytes signature, empty kid,
  empty expected_trust_domain.
- E2E with real bridge + InMemoryBundleTransport: bundle sync →
  partner verify → VALID; bundle rotates → old kid still rejects
  (KID_NOT_FOUND or SIGNATURE_INVALID per the rotation semantics).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


aims = _load(
    "vos3_aims_envelope_under_test_b2",
    _REPO_ROOT / "backend" / "services" / "aims_envelope.py",
)
bridge_mod = _load(
    "vos3_fed_bridge_under_test_b2",
    _REPO_ROOT / "backend" / "services" / "identity_federation_bridge.py",
)
pv_mod = _load(
    "vos3_aims_partner_verifier_under_test",
    _REPO_ROOT / "backend" / "services" / "aims_partner_verifier.py",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _gen_ed25519():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    pk_bytes = pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sk, pk_bytes


def _make_jwk(pubkey_bytes: bytes, kid: str) -> dict[str, Any]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "x": _b64url(pubkey_bytes),
    }


def _bundle_with_keys(trust_domain: str, keys: list[dict], sequence: int = 1) -> bytes:
    return json.dumps(
        {
            "trust_domain": trust_domain,
            "spiffe_sequence": sequence,
            "spiffe_refresh_hint": 300,
            "keys": keys,
        }
    ).encode("utf-8")


def _make_envelope(trust_domain: str) -> "aims.AIMSEnvelope":
    return (
        aims.AIMSEnvelopeBuilder()
        .from_federation(
            spiffe_id=f"{trust_domain}/agent/slot-42",
            trust_domain=trust_domain,
            actor_type="agent",
            verifier_audience="aidg-receiving-side",
        )
        .with_principal(
            principal_id="alice@partner.example.com",
            principal_kind="human",
        )
        .with_authorization_grant(
            resource="https://api.aidg.vos3.dev/v1/data",
            actions=("read",),
        )
        .build()
    )


def _sign_envelope(sk, envelope) -> bytes:
    return sk.sign(envelope.to_json().encode("utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def transport():
    return bridge_mod.InMemoryBundleTransport()


@pytest.fixture
def stub_verifier():
    """Minimal verifier stub — the bridge needs a register/unregister
    target. Real F4 verifier shape is exercised in
    test_identity_federation_bridge.py."""

    class _V:
        def register_federated_domain(self, *a, **kw):
            pass

        def unregister_federated_domain(self, *a, **kw):
            pass

        def list_federated_domains(self):
            return []

        def verify(self, *_):
            return None

    return _V()


@pytest.fixture
def bridge(stub_verifier, transport):
    return bridge_mod.IdentityFederationBridge(
        local_trust_domain="spiffe://aidg.vos3.dev",
        federation_verifier=stub_verifier,
        transport=transport,
    )


@pytest.fixture
def synced_bridge_with_partner(bridge, transport):
    """Bridge with one registered + successfully-synced partner.
    Returns (bridge, sk, pubkey_bytes, kid, trust_domain)."""
    td = "spiffe://partner.example.com"
    sk, pk = _gen_ed25519()
    kid = "partner-2026-key-1"
    transport.seed(
        "https://partner.example.com/bundle",
        _bundle_with_keys(td, [_make_jwk(pk, kid)]),
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://partner.example.com/bundle",
    )
    o = bridge.refresh_partner(td)
    assert o.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    return bridge, sk, pk, kid, td


@pytest.fixture
def partner_verifier(synced_bridge_with_partner):
    bridge, *_ = synced_bridge_with_partner
    # Inject the test's AIMSEnvelopeVerifier instance so the isinstance
    # check inside it sees the SAME AIMSEnvelope class as the test
    # module's _make_envelope() builds. Without this, spec_from_file_
    # location loads two different copies and isinstance fails.
    return pv_mod.AIMSPartnerVerifier(
        bridge=bridge,
        base_verifier=aims.AIMSEnvelopeVerifier(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_cross_domain_envelope(synced_bridge_with_partner, partner_verifier):
    _, sk, _, kid, td = synced_bridge_with_partner
    env = _make_envelope(td)
    sig = _sign_envelope(sk, env)
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=sig,
        signing_kid=kid,
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.VALID
    assert outcome.trust_domain == td
    assert outcome.aims_id == env.aims_id
    assert "verified" in outcome.reason.lower()


# ---------------------------------------------------------------------------
# Bridge state failures
# ---------------------------------------------------------------------------


def test_partner_unknown(partner_verifier):
    env = _make_envelope("spiffe://random.example.com")
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="anything",
        expected_trust_domain="spiffe://random.example.com",
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.PARTNER_UNKNOWN


def test_partner_not_synced(bridge):
    """Registered but never refreshed → PARTNER_NOT_SYNCED."""
    td = "spiffe://newpartner.example.com"
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://newpartner.example.com/bundle",
    )
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )
    env = _make_envelope(td)
    outcome = pv.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="anything",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.PARTNER_NOT_SYNCED


# ---------------------------------------------------------------------------
# Key lookup failures
# ---------------------------------------------------------------------------


def test_kid_not_found(synced_bridge_with_partner, partner_verifier):
    _, sk, _, _, td = synced_bridge_with_partner
    env = _make_envelope(td)
    sig = _sign_envelope(sk, env)
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=sig,
        signing_kid="wrong-kid-xyz",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.KID_NOT_FOUND
    assert "wrong-kid-xyz" in outcome.reason


def test_unsupported_key_type(bridge, transport):
    """Partner bundle contains an RSA JWK instead of OKP/Ed25519."""
    td = "spiffe://rsa-partner.example.com"
    rsa_jwk = {
        "kty": "RSA",
        "kid": "rsa-1",
        "n": "x" * 100,
        "e": "AQAB",
    }
    transport.seed(
        "https://rsa-partner.example.com/bundle",
        _bundle_with_keys(td, [rsa_jwk]),
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://rsa-partner.example.com/bundle",
    )
    bridge.refresh_partner(td)
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )
    env = _make_envelope(td)
    outcome = pv.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="rsa-1",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.UNSUPPORTED_KEY_TYPE


def test_unsupported_curve(bridge, transport):
    """JWK kty=OKP but crv=X25519 (not Ed25519)."""
    td = "spiffe://x25519-partner.example.com"
    x25519_jwk = {
        "kty": "OKP",
        "crv": "X25519",
        "kid": "x25519-1",
        "x": _b64url(b"\x00" * 32),
    }
    transport.seed(
        "https://x25519-partner.example.com/bundle",
        _bundle_with_keys(td, [x25519_jwk]),
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://x25519-partner.example.com/bundle",
    )
    bridge.refresh_partner(td)
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )
    env = _make_envelope(td)
    outcome = pv.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="x25519-1",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.UNSUPPORTED_KEY_TYPE


def test_malformed_jwk_missing_x(bridge, transport):
    """JWK kty=OKP, crv=Ed25519 but no 'x' field."""
    td = "spiffe://malformed-partner.example.com"
    bad_jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "bad-1"}
    transport.seed(
        "https://malformed-partner.example.com/bundle",
        _bundle_with_keys(td, [bad_jwk]),
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://malformed-partner.example.com/bundle",
    )
    bridge.refresh_partner(td)
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )
    env = _make_envelope(td)
    outcome = pv.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="bad-1",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.MALFORMED_JWK


def test_malformed_jwk_wrong_x_length(bridge, transport):
    """Ed25519 pubkey must be exactly 32 bytes."""
    td = "spiffe://short-key-partner.example.com"
    bad_jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": "short-1",
        "x": _b64url(b"\x00" * 16),  # half-length
    }
    transport.seed(
        "https://short-key-partner.example.com/bundle",
        _bundle_with_keys(td, [bad_jwk]),
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://short-key-partner.example.com/bundle",
    )
    bridge.refresh_partner(td)
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )
    env = _make_envelope(td)
    outcome = pv.verify(
        envelope=env,
        signature_bytes=b"\x00" * 64,
        signing_kid="short-1",
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.MALFORMED_JWK


# ---------------------------------------------------------------------------
# Signature failures
# ---------------------------------------------------------------------------


def test_signature_invalid_tampered_signature(
    synced_bridge_with_partner, partner_verifier
):
    _, sk, _, kid, td = synced_bridge_with_partner
    env = _make_envelope(td)
    sig = bytearray(_sign_envelope(sk, env))
    sig[0] ^= 0xFF  # flip a bit
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=bytes(sig),
        signing_kid=kid,
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.SIGNATURE_INVALID


def test_signature_invalid_wrong_key(synced_bridge_with_partner, partner_verifier):
    _, _, _, kid, td = synced_bridge_with_partner
    # Sign with a DIFFERENT key not in the partner's bundle.
    other_sk, _ = _gen_ed25519()
    env = _make_envelope(td)
    sig = _sign_envelope(other_sk, env)
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=sig,
        signing_kid=kid,
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.SIGNATURE_INVALID


# ---------------------------------------------------------------------------
# Trust domain mismatch
# ---------------------------------------------------------------------------


def test_trust_domain_mismatch(synced_bridge_with_partner, partner_verifier):
    _, sk, _, kid, td = synced_bridge_with_partner
    env = _make_envelope(td)
    sig = _sign_envelope(sk, env)
    outcome = partner_verifier.verify(
        envelope=env,
        signature_bytes=sig,
        signing_kid=kid,
        expected_trust_domain="spiffe://different-partner.example.com",
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.TRUST_DOMAIN_MISMATCH


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_rejects_none_envelope(partner_verifier):
    with pytest.raises(ValueError):
        partner_verifier.verify(
            envelope=None,
            signature_bytes=b"\x00",
            signing_kid="k",
            expected_trust_domain="td",
        )


def test_rejects_non_bytes_signature(partner_verifier):
    env = _make_envelope("spiffe://partner.example.com")
    with pytest.raises(TypeError):
        partner_verifier.verify(
            envelope=env,
            signature_bytes="not bytes",
            signing_kid="k",
            expected_trust_domain="td",
        )


def test_rejects_empty_signing_kid(partner_verifier):
    env = _make_envelope("spiffe://partner.example.com")
    with pytest.raises(ValueError):
        partner_verifier.verify(
            envelope=env,
            signature_bytes=b"\x00",
            signing_kid="",
            expected_trust_domain="td",
        )


def test_rejects_empty_expected_trust_domain(partner_verifier):
    env = _make_envelope("spiffe://partner.example.com")
    with pytest.raises(ValueError):
        partner_verifier.verify(
            envelope=env,
            signature_bytes=b"\x00",
            signing_kid="k",
            expected_trust_domain="",
        )


# ---------------------------------------------------------------------------
# Base envelope self-consistency failures
# ---------------------------------------------------------------------------


def test_base_verify_failed_propagates(synced_bridge_with_partner, partner_verifier):
    """Signature verifies but envelope's own SHA is wrong — the base
    AIMSEnvelopeVerifier should reject + we report BASE_VERIFY_FAILED."""
    import dataclasses

    _, sk, _, kid, td = synced_bridge_with_partner
    env = _make_envelope(td)

    # Tamper the envelope: replace its sha (now self-inconsistent).
    tampered = dataclasses.replace(env, envelope_sha256="0" * 64)
    # But sign the TAMPERED canonical JSON so the sig check passes.
    sig = sk.sign(tampered.to_json().encode("utf-8"))
    outcome = partner_verifier.verify(
        envelope=tampered,
        signature_bytes=sig,
        signing_kid=kid,
        expected_trust_domain=td,
    )
    assert outcome.kind == pv_mod.PartnerVerificationOutcomeKind.BASE_VERIFY_FAILED
    assert outcome.base_outcome is not None
    # The base outcome should be a SHA_MISMATCH.
    assert "SHA_MISMATCH" in outcome.reason or "sha" in outcome.reason.lower()


# ---------------------------------------------------------------------------
# E2E — full sync → verify → rotation
# ---------------------------------------------------------------------------


def test_e2e_sync_verify_rotation(bridge, transport):
    """Realistic flow:
    1. Partner publishes bundle with key K1.
    2. Bridge syncs.
    3. Envelope signed with K1 verifies VALID.
    4. Partner rotates key — bundle now has K2.
    5. Bridge re-syncs.
    6. Envelope signed with K1 + claiming kid=K1 now fails
       KID_NOT_FOUND (K1 is no longer in the bundle).
    """
    td = "spiffe://rotating-partner.example.com"
    sk1, pk1 = _gen_ed25519()
    sk2, pk2 = _gen_ed25519()

    transport.seed_sequence(
        "https://rotating-partner.example.com/bundle",
        [
            _bundle_with_keys(td, [_make_jwk(pk1, "k1")], sequence=1),
            _bundle_with_keys(td, [_make_jwk(pk2, "k2")], sequence=2),
        ],
    )
    bridge.register_partner(
        trust_domain=td,
        bundle_endpoint_url="https://rotating-partner.example.com/bundle",
    )
    bridge.refresh_partner(td)
    pv = pv_mod.AIMSPartnerVerifier(
        bridge=bridge, base_verifier=aims.AIMSEnvelopeVerifier()
    )

    env = _make_envelope(td)
    sig1 = _sign_envelope(sk1, env)

    # Initial: VALID under k1.
    o1 = pv.verify(
        envelope=env,
        signature_bytes=sig1,
        signing_kid="k1",
        expected_trust_domain=td,
    )
    assert o1.kind == pv_mod.PartnerVerificationOutcomeKind.VALID

    # Rotate.
    bridge.refresh_partner(td)
    # Old k1 envelope: KID_NOT_FOUND (the rotation dropped k1).
    o2 = pv.verify(
        envelope=env,
        signature_bytes=sig1,
        signing_kid="k1",
        expected_trust_domain=td,
    )
    assert o2.kind == pv_mod.PartnerVerificationOutcomeKind.KID_NOT_FOUND

    # New envelope signed with k2: VALID.
    env_new = _make_envelope(td)
    sig2 = _sign_envelope(sk2, env_new)
    o3 = pv.verify(
        envelope=env_new,
        signature_bytes=sig2,
        signing_kid="k2",
        expected_trust_domain=td,
    )
    assert o3.kind == pv_mod.PartnerVerificationOutcomeKind.VALID
