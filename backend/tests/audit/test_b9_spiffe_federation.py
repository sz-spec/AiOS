"""
B9 — SPIFFE/AIMS cross-domain identity federation tests (TEST_PLAN_300 §B9).

Adversarial sweep over ``services/identity_federation_bridge.py`` — the Wave
B.1 cross-organization bundle-sync layer that fetches a partner trust domain's
SPIFFE bundle, validates rotation, and atomically swaps the verifier's bundle.

Scenarios (fail-closed):
  * fake / spoofed trust-bundle injection (declared trust_domain != registered)
    → PARSE_ERROR, no swap;
  * rolled-back / "expired" anchor (sequence regression) → REGRESSION rejected
    by default (force_rollback override is the only escape);
  * pin mismatch (tampered bundle body under a pinned anchor) → PIN_MISMATCH;
  * unauthorized self-domain / non-HTTPS / under-refresh / unpinned-SPIFFE
    registrations refused at the door;
  * HTTPS_SPIFFE bootstrap requires a pinned trust anchor (fail-closed B.4).

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b9_spiffe_federation.py -v
"""
from __future__ import annotations

import hashlib
import json

import pytest

from services.identity_federation_bridge import (
    BundleProfile,
    IdentityFederationBridge,
    InMemoryBundleTransport,
    RefreshOutcomeKind,
    TransportError,
)

LOCAL_TD = "spiffe://aidg.example"
PARTNER_TD = "spiffe://partner.example"
URL = "https://partner.example/.well-known/spiffe-bundle"


class _FakeVerifier:
    """Minimal F4 SPIFFEFederationVerifier stand-in (_FederationVerifierProtocol)."""

    def __init__(self):
        self.registered: dict = {}
        self.verify_calls: list = []

    def register_federated_domain(self, trust_domain, keys=None, **kw):
        self.registered[trust_domain] = keys

    def unregister_federated_domain(self, trust_domain):
        self.registered.pop(trust_domain, None)

    def list_federated_domains(self):
        return list(self.registered)

    def verify(self, jwt_token):
        self.verify_calls.append(jwt_token)
        return {"ok": True, "token": jwt_token}


def _bundle_bytes(trust_domain: str, seq: int, n_keys: int = 1) -> bytes:
    return json.dumps(
        {
            "trust_domain": trust_domain,
            "spiffe_sequence": seq,
            "spiffe_refresh_hint": 300,
            "keys": [{"kid": f"k{i}", "kty": "RSA", "n": "AA", "e": "AQAB"}
                     for i in range(n_keys)],
        }
    ).encode("utf-8")


def _bridge(transport):
    return IdentityFederationBridge(
        local_trust_domain=LOCAL_TD,
        federation_verifier=_FakeVerifier(),
        transport=transport,
    )


# ===========================================================================
# Registration guards (fail-closed at the door)
# ===========================================================================


def test_b9_register_rejects_local_trust_domain():
    """Cannot register a 'partner' that is actually our own domain
    (self-impersonation / audience confusion)."""
    b = _bridge(InMemoryBundleTransport())
    with pytest.raises(ValueError):
        b.register_partner(trust_domain=LOCAL_TD, bundle_endpoint_url=URL)


def test_b9_register_rejects_non_https_endpoint():
    b = _bridge(InMemoryBundleTransport())
    with pytest.raises(ValueError):
        b.register_partner(trust_domain=PARTNER_TD,
                           bundle_endpoint_url="http://partner.example/bundle")


def test_b9_register_rejects_too_frequent_refresh():
    b = _bridge(InMemoryBundleTransport())
    with pytest.raises(ValueError):
        b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL,
                           refresh_seconds=5)


def test_b9_register_https_spiffe_requires_pinned_anchor():
    """HTTPS_SPIFFE bootstrap is fail-closed without a pinned trust anchor."""
    b = _bridge(InMemoryBundleTransport())
    with pytest.raises(ValueError):
        b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL,
                           profile=BundleProfile.HTTPS_SPIFFE)  # no pinned_sha256


def test_b9_register_rejects_duplicate():
    b = _bridge(InMemoryBundleTransport())
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    with pytest.raises(ValueError):
        b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)


# ===========================================================================
# Refresh — happy path
# ===========================================================================


def test_b9_refresh_success_new_then_unchanged():
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1, n_keys=2))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    o1 = b.refresh_partner(PARTNER_TD)
    assert o1.kind == RefreshOutcomeKind.SUCCESS_NEW and o1.keys_loaded == 2
    # same sequence again → UNCHANGED
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1, n_keys=2))
    o2 = b.refresh_partner(PARTNER_TD)
    assert o2.kind == RefreshOutcomeKind.SUCCESS_UNCHANGED


def test_b9_refresh_rotation_higher_sequence_accepted():
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    b.refresh_partner(PARTNER_TD)
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=2, n_keys=3))
    o = b.refresh_partner(PARTNER_TD)
    assert o.kind == RefreshOutcomeKind.SUCCESS_NEW and o.sequence_number == 2


# ===========================================================================
# Adversarial — fail-closed rejections
# ===========================================================================


def test_b9_fake_bundle_trust_domain_mismatch_rejected():
    """A bundle that DECLARES a different trust_domain than the registered
    partner (spoofed/cross-cluster audience injection) → PARSE_ERROR, no swap."""
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes("spiffe://attacker.example", seq=1))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    o = b.refresh_partner(PARTNER_TD)
    assert o.kind == RefreshOutcomeKind.PARSE_ERROR
    assert b.get_partner_bundle_keys(PARTNER_TD) == ()  # nothing swapped in


def test_b9_malformed_json_bundle_rejected():
    t = InMemoryBundleTransport()
    t.seed(URL, b"{ this is not json ]")
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    assert b.refresh_partner(PARTNER_TD).kind == RefreshOutcomeKind.PARSE_ERROR


def test_b9_sequence_rollback_rejected_by_default():
    """A rolled-back / 'expired' bundle (lower sequence) is rejected unless the
    operator explicitly forces rollback."""
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=5))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    b.refresh_partner(PARTNER_TD)  # seq 5 synced
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=2))  # rollback attempt
    o = b.refresh_partner(PARTNER_TD)
    assert o.kind == RefreshOutcomeKind.REGRESSION
    # explicit operator override is the ONLY escape
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=2))
    o2 = b.refresh_partner(PARTNER_TD, force_rollback=True)
    assert o2.kind == RefreshOutcomeKind.SUCCESS_NEW


def test_b9_pin_mismatch_rejected():
    """A tampered bundle body under a pinned anchor → PIN_MISMATCH."""
    good = _bundle_bytes(PARTNER_TD, seq=1)
    pin = hashlib.sha256(good).hexdigest()
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1, n_keys=9))  # different body
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL,
                       pinned_sha256=pin)
    assert b.refresh_partner(PARTNER_TD).kind == RefreshOutcomeKind.PIN_MISMATCH


def test_b9_transport_error_is_fail_closed():
    t = InMemoryBundleTransport()
    t.seed(URL, TransportError("partner endpoint unreachable"))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    o = b.refresh_partner(PARTNER_TD)
    assert o.kind == RefreshOutcomeKind.TRANSPORT_ERROR
    assert b.get_partner_bundle_keys(PARTNER_TD) == ()


# ===========================================================================
# HTTPS_SPIFFE pinned-anchor bootstrap (B.4)
# ===========================================================================


def test_b9_https_spiffe_bootstrap_then_rotate():
    boot = _bundle_bytes(PARTNER_TD, seq=1)
    pin = hashlib.sha256(boot).hexdigest()
    t = InMemoryBundleTransport()
    t.seed(URL, boot)
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL,
                       profile=BundleProfile.HTTPS_SPIFFE, pinned_sha256=pin)
    o1 = b.refresh_partner(PARTNER_TD)  # bootstrap pinned to anchor
    assert o1.kind == RefreshOutcomeKind.SUCCESS_NEW
    assert b.stats.spiffe_bootstraps == 1
    # post-bootstrap rotation (higher seq, body != pin) validates via synced keys
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=2, n_keys=4))
    o2 = b.refresh_partner(PARTNER_TD)
    assert o2.kind == RefreshOutcomeKind.SUCCESS_NEW and o2.sequence_number == 2


def test_b9_https_spiffe_bootstrap_wrong_anchor_rejected():
    """Bootstrap body not matching the pinned anchor → PIN_MISMATCH (fail-closed)."""
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1, n_keys=7))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL,
                       profile=BundleProfile.HTTPS_SPIFFE,
                       pinned_sha256="deadbeef" * 8)
    assert b.refresh_partner(PARTNER_TD).kind == RefreshOutcomeKind.PIN_MISMATCH


# ===========================================================================
# Accessors + verify pass-through
# ===========================================================================


def test_b9_get_bundle_keys_unknown_partner_raises():
    b = _bridge(InMemoryBundleTransport())
    with pytest.raises(KeyError):
        b.get_partner_bundle_keys("spiffe://never.registered")


def test_b9_get_bundle_keys_empty_before_refresh():
    b = _bridge(InMemoryBundleTransport())
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    assert b.get_partner_bundle_keys(PARTNER_TD) == ()


def test_b9_revoke_partner_removes_state():
    t = InMemoryBundleTransport()
    t.seed(URL, _bundle_bytes(PARTNER_TD, seq=1))
    b = _bridge(t)
    b.register_partner(trust_domain=PARTNER_TD, bundle_endpoint_url=URL)
    b.refresh_partner(PARTNER_TD)
    assert b.revoke_partner(PARTNER_TD) is True
    with pytest.raises(KeyError):
        b.get_partner_bundle_keys(PARTNER_TD)
    assert b.revoke_partner(PARTNER_TD) is False  # already gone
