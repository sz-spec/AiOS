"""
backend/tests/integration/test_spiffe_federation.py

Sprint 16 / Item F4 — SPIFFE Federation accept tests.

Uses stub _VerifierProtocol implementations to exercise the routing layer
in isolation from F1's full crypto path (which is already covered by
test_spiffe_wit_svid.py from Sprint 15).

Covers:
- extract_trust_domain_from_issuer: parses spiffe://X/path + https://X/path;
  rejects bare strings, empty, None.
- Verifier registration validation: rejects empty domain, missing verifier,
  trust_domain mismatch between arg + verifier.trust_domain,
  attempt to register the local domain.
- Verifier unregistration removes from federated set.
- list_federated_domains returns sorted lowercase set.
- verify: routes local-issuer tokens to local_verifier; routes federated-
  issuer tokens to the matching registered verifier; raises
  FederationUnknownDomainError for unregistered domains; raises
  FederationMalformedTokenError for non-3-segment / non-JSON-payload / no-iss.
- verify_increments correct stats counters: local_verifications,
  federated_verifications, unknown_domain_blocked, malformed_token_blocked.
- VerifiedFederatedIdentity carries is_local correctly.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FED_PATH = _REPO_ROOT / "backend" / "core" / "security" / "spiffe_federation.py"
_spec = importlib.util.spec_from_file_location("vos3_spiffe_fed_under_test", _FED_PATH)
fed = importlib.util.module_from_spec(_spec)
sys.modules["vos3_spiffe_fed_under_test"] = fed
_spec.loader.exec_module(fed)


# ---------------------------------------------------------------------------
# Stub verifier
# ---------------------------------------------------------------------------


@dataclass
class _StubVerified:
    spiffe_id_str: str


class _StubVerifier:
    def __init__(
        self,
        trust_domain: str,
        will_raise=None,
        return_spiffe_id="spiffe://example.com/agent",
    ):
        self.trust_domain = trust_domain
        self._will_raise = will_raise
        self._return_spiffe = return_spiffe_id
        self.calls: list[str] = []

    def verify(self, jwt_token: str):
        self.calls.append(jwt_token)
        if self._will_raise:
            raise self._will_raise
        return _StubVerified(spiffe_id_str=self._return_spiffe)


def _jwt_with_issuer(issuer: str) -> str:
    """Build a 3-segment JWT-shape with given `iss` claim. NOT cryptographically
    valid; the federation layer only peeks at the payload, then routes to the
    stub verifier which doesn't actually check signatures."""
    header_b64 = (
        base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    )
    payload = json.dumps({"iss": issuer, "sub": "spiffe://example.com/agent"}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.fake-sig"


# ---------------------------------------------------------------------------
# Issuer extraction
# ---------------------------------------------------------------------------


def test_extract_trust_domain_spiffe_uri():
    assert (
        fed.extract_trust_domain_from_issuer("spiffe://example.com/agent/x")
        == "example.com"
    )


def test_extract_trust_domain_https_uri():
    assert (
        fed.extract_trust_domain_from_issuer("https://example.com/issuer")
        == "example.com"
    )


def test_extract_trust_domain_lowercased():
    assert (
        fed.extract_trust_domain_from_issuer("spiffe://EXAMPLE.COM/agent")
        == "example.com"
    )


def test_extract_trust_domain_rejects_bare():
    with pytest.raises(fed.FederationMalformedTokenError):
        fed.extract_trust_domain_from_issuer("just-a-string")


def test_extract_trust_domain_rejects_empty():
    with pytest.raises(fed.FederationMalformedTokenError):
        fed.extract_trust_domain_from_issuer("")
    with pytest.raises(fed.FederationMalformedTokenError):
        fed.extract_trust_domain_from_issuer(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_init_requires_local_verifier():
    with pytest.raises(ValueError):
        fed.SPIFFEFederationVerifier(local_verifier=None)


def test_init_requires_local_verifier_with_trust_domain():
    class _NoDomain:
        pass

    with pytest.raises(ValueError):
        fed.SPIFFEFederationVerifier(local_verifier=_NoDomain())


def test_register_federated_rejects_empty_domain():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    with pytest.raises(ValueError):
        f.register_federated_domain("", _StubVerifier("partner.com"))


def test_register_federated_rejects_missing_verifier():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    with pytest.raises(ValueError):
        f.register_federated_domain("partner.com", None)


def test_register_federated_rejects_trust_domain_mismatch():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    impersonator = _StubVerifier("REAL.example.com")
    with pytest.raises(ValueError, match="trust_domain mismatch"):
        f.register_federated_domain("partner.com", impersonator)


def test_register_federated_rejects_local_domain():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    self_imposter = _StubVerifier("local.example.com")
    with pytest.raises(ValueError, match="cannot register local domain"):
        f.register_federated_domain("local.example.com", self_imposter)


def test_register_then_unregister():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    partner = _StubVerifier("partner.example.com")
    f.register_federated_domain("partner.example.com", partner)
    assert "partner.example.com" in f.list_federated_domains()
    f.unregister_federated_domain("partner.example.com")
    assert f.list_federated_domains() == []


def test_list_federated_domains_sorted_lowercased():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    f.register_federated_domain("zeta.example.com", _StubVerifier("zeta.example.com"))
    f.register_federated_domain("alpha.example.com", _StubVerifier("alpha.example.com"))
    assert f.list_federated_domains() == ["alpha.example.com", "zeta.example.com"]


# ---------------------------------------------------------------------------
# Verification routing
# ---------------------------------------------------------------------------


def test_verify_routes_local_issuer_to_local_verifier():
    local = _StubVerifier(
        "local.example.com", return_spiffe_id="spiffe://local.example.com/agent-1"
    )
    f = fed.SPIFFEFederationVerifier(local)
    tok = _jwt_with_issuer("spiffe://local.example.com/spire-server")
    identity = f.verify(tok)
    assert identity.is_local is True
    assert identity.trust_domain == "local.example.com"
    assert identity.spiffe_id == "spiffe://local.example.com/agent-1"
    assert len(local.calls) == 1


def test_verify_routes_federated_issuer_to_partner_verifier():
    local = _StubVerifier("local.example.com")
    partner = _StubVerifier(
        "partner.example.com", return_spiffe_id="spiffe://partner.example.com/agent-9"
    )
    f = fed.SPIFFEFederationVerifier(local)
    f.register_federated_domain("partner.example.com", partner)
    tok = _jwt_with_issuer("spiffe://partner.example.com/spire-server")
    identity = f.verify(tok)
    assert identity.is_local is False
    assert identity.trust_domain == "partner.example.com"
    assert identity.spiffe_id == "spiffe://partner.example.com/agent-9"
    assert len(local.calls) == 0
    assert len(partner.calls) == 1


def test_verify_unregistered_domain_raises():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    tok = _jwt_with_issuer("spiffe://attacker.example.com/spire-server")
    with pytest.raises(fed.FederationUnknownDomainError):
        f.verify(tok)


def test_verify_malformed_token_raises():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    with pytest.raises(fed.FederationMalformedTokenError):
        f.verify("not-a-jwt")
    with pytest.raises(fed.FederationMalformedTokenError):
        f.verify("")
    with pytest.raises(fed.FederationMalformedTokenError):
        f.verify(None)  # type: ignore[arg-type]


def test_verify_token_with_no_iss_claim_raises():
    header_b64 = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload = json.dumps({"sub": "spiffe://x/y"}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    tok = f"{header_b64}.{payload_b64}.sig"
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    with pytest.raises(fed.FederationMalformedTokenError):
        f.verify(tok)


def test_verify_propagates_underlying_verifier_exception():
    """If F1's verify() raises (bad signature etc.), federation should
    propagate without wrapping so callers can catch the original type."""

    class CustomBoom(RuntimeError):
        pass

    local = _StubVerifier("local.example.com", will_raise=CustomBoom("bad sig"))
    f = fed.SPIFFEFederationVerifier(local)
    tok = _jwt_with_issuer("spiffe://local.example.com/agent")
    with pytest.raises(CustomBoom):
        f.verify(tok)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    local = _StubVerifier("local.example.com")
    partner = _StubVerifier("partner.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    f.register_federated_domain("partner.example.com", partner)

    # 1 local verify.
    f.verify(_jwt_with_issuer("spiffe://local.example.com/x"))
    # 2 federated verifies.
    f.verify(_jwt_with_issuer("spiffe://partner.example.com/y"))
    f.verify(_jwt_with_issuer("spiffe://partner.example.com/z"))
    # 1 unknown-domain blocked.
    with pytest.raises(fed.FederationUnknownDomainError):
        f.verify(_jwt_with_issuer("spiffe://unknown.example.com/w"))
    # 1 malformed.
    with pytest.raises(fed.FederationMalformedTokenError):
        f.verify("not.a.jwt")

    s = f.stats()
    assert s.total_verifications == 5
    assert s.local_verifications == 1
    assert s.federated_verifications == 2
    assert s.unknown_domain_blocked == 1
    assert s.malformed_token_blocked == 1


def test_stats_snapshot_is_copy():
    local = _StubVerifier("local.example.com")
    f = fed.SPIFFEFederationVerifier(local)
    s1 = f.stats()
    f.verify(_jwt_with_issuer("spiffe://local.example.com/x"))
    s2 = f.stats()
    assert s1.total_verifications == 0
    assert s2.total_verifications == 1
