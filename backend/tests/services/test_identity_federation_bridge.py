"""
backend/tests/services/test_identity_federation_bridge.py

Sprint 17 / Wave 2 / Cluster B.1 — Smoke tests for the cross-org
identity federation bridge.

Covers:
- Partner registration: HTTPS-only URL, refresh_seconds floor,
  duplicate-domain rejection, self-trust-domain rejection.
- Refresh outcomes: SUCCESS_NEW, SUCCESS_UNCHANGED, TRANSPORT_ERROR,
  PARSE_ERROR, PIN_MISMATCH, REGRESSION + force_rollback escape hatch.
- Refresh emits an atomic unregister+register on the underlying
  verifier; the verifier observes only the new keys after a rotation.
- Verifier integration: cross_domain_verifies counter advances when
  verify_cross_domain_jwt is called.
- HTTPS_SPIFFE profile rejected with PROFILE_UNSUPPORTED.
- Revoke partner removes verifier registration + bridge record.
- Sequence-number monotonicity defaults to REJECT; force_rollback
  bypass works.
- InMemoryBundleTransport seed/sequence/exception modes work.
- HttpBundleTransport rejects http:// URLs at the entrypoint.
- PartnerRecord reflects last refresh outcome + ts.
- BridgeStats counters increment for each outcome kind.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
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


bridge_mod = _load(
    "vos3_fed_bridge_under_test",
    _REPO_ROOT / "backend" / "services" / "identity_federation_bridge.py",
)


# ---------------------------------------------------------------------------
# Test fakes — F4 verifier shape
# ---------------------------------------------------------------------------


class FakeVerifier:
    """Records register/unregister calls so tests can assert the
    bridge issues atomic swaps + the right keys land."""

    def __init__(self):
        self.registrations: list[tuple[str, Any]] = []
        self.unregistrations: list[str] = []
        self.verify_calls: list[str] = []
        self._returns: Any = "fake-verified-identity"

    def register_federated_domain(
        self, trust_domain: str, *args: Any, **kwargs: Any
    ) -> None:
        keys = kwargs.get("keys") or (args[0] if args else None)
        self.registrations.append((trust_domain, keys))

    def unregister_federated_domain(self, trust_domain: str) -> None:
        self.unregistrations.append(trust_domain)

    def list_federated_domains(self) -> list[str]:
        return [d for d, _ in self.registrations]

    def verify(self, jwt_token: str) -> Any:
        self.verify_calls.append(jwt_token)
        return self._returns


@pytest.fixture
def verifier() -> FakeVerifier:
    return FakeVerifier()


@pytest.fixture
def transport() -> "bridge_mod.InMemoryBundleTransport":
    return bridge_mod.InMemoryBundleTransport()


@pytest.fixture
def clock_stub():
    class _Clock:
        t: float = 1716700000.0

        def time(self) -> float:
            t = self.t
            self.t += 1.0
            return t

    return _Clock()


@pytest.fixture
def bridge(verifier, transport, clock_stub):
    return bridge_mod.IdentityFederationBridge(
        local_trust_domain="spiffe://aidg.vos3.dev",
        federation_verifier=verifier,
        transport=transport,
        clock=clock_stub,
    )


# ---------------------------------------------------------------------------
# Helpers — synthesize a bundle JSON payload
# ---------------------------------------------------------------------------


def _bundle_json(
    trust_domain: str,
    sequence: int,
    *,
    refresh_hint: int = 300,
    keys: list[dict] | None = None,
) -> bytes:
    return json.dumps(
        {
            "trust_domain": trust_domain,
            "spiffe_sequence": sequence,
            "spiffe_refresh_hint": refresh_hint,
            "keys": keys
            or [
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "kid": f"k{sequence}",
                    "x": "dGVzdC1maW5nZXJwcmludA",
                },
            ],
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Register partner
# ---------------------------------------------------------------------------


def test_register_partner_happy_path(bridge):
    rec = bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url="https://partner.example.com/.well-known/spiffe-bundle",
    )
    assert rec.trust_domain == "spiffe://partner.example.com"
    assert rec.profile == bridge_mod.BundleProfile.HTTPS_WEB
    assert rec.last_refresh_outcome is None
    assert bridge.list_partners() == (rec,)
    assert bridge.stats.partners_registered == 1


def test_register_rejects_http(bridge):
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain="spiffe://partner.example.com",
            bundle_endpoint_url="http://partner.example.com/bundle",
        )


def test_register_rejects_self_trust_domain(bridge):
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain="spiffe://aidg.vos3.dev",  # the bridge's local
            bundle_endpoint_url="https://aidg.vos3.dev/bundle",
        )


def test_register_rejects_short_refresh_seconds(bridge):
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain="spiffe://partner.example.com",
            bundle_endpoint_url="https://partner.example.com/bundle",
            refresh_seconds=10,
        )


def test_register_rejects_duplicate(bridge):
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url="https://partner.example.com/bundle",
    )
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain="spiffe://partner.example.com",
            bundle_endpoint_url="https://partner.example.com/bundle",
        )


# ---------------------------------------------------------------------------
# Refresh SUCCESS_NEW
# ---------------------------------------------------------------------------


def test_refresh_success_new_loads_keys_and_registers_on_verifier(
    bridge, transport, verifier
):
    url = "https://partner.example.com/bundle"
    transport.seed(url, _bundle_json("spiffe://partner.example.com", 1))
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert outcome.sequence_number == 1
    assert outcome.keys_loaded == 1
    # Verifier got the keys.
    assert verifier.registrations[-1][0] == "spiffe://partner.example.com"
    # And the bridge stats track it.
    assert bridge.stats.refresh_success_new == 1


def test_refresh_success_unchanged_no_reregister(bridge, transport, verifier):
    url = "https://partner.example.com/bundle"
    transport.seed_sequence(
        url,
        [
            _bundle_json("spiffe://partner.example.com", 5),
            _bundle_json("spiffe://partner.example.com", 5),  # same seq
        ],
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    o1 = bridge.refresh_partner("spiffe://partner.example.com")
    o2 = bridge.refresh_partner("spiffe://partner.example.com")
    assert o1.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert o2.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_UNCHANGED
    # Only ONE register on the verifier (the SUCCESS_NEW path).
    assert len(verifier.registrations) == 1
    assert bridge.stats.refresh_success_unchanged == 1


# ---------------------------------------------------------------------------
# Refresh SUCCESS_NEW with rotation — atomic unregister+register
# ---------------------------------------------------------------------------


def test_refresh_rotation_unregisters_then_reregisters(bridge, transport, verifier):
    url = "https://partner.example.com/bundle"
    transport.seed_sequence(
        url,
        [
            _bundle_json(
                "spiffe://partner.example.com", 1, keys=[{"kty": "OKP", "kid": "old"}]
            ),
            _bundle_json(
                "spiffe://partner.example.com", 2, keys=[{"kty": "OKP", "kid": "new"}]
            ),
        ],
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    bridge.refresh_partner("spiffe://partner.example.com")
    bridge.refresh_partner("spiffe://partner.example.com")
    # Two register calls (initial + rotation).
    assert len(verifier.registrations) == 2
    # The second register sees the NEW key.
    last_keys = verifier.registrations[-1][1]
    assert last_keys[0]["kid"] == "new"
    # And the rotation issued an unregister BEFORE the second register.
    assert verifier.unregistrations == [
        "spiffe://partner.example.com",  # cleared before initial
        "spiffe://partner.example.com",  # cleared before rotation
    ]


# ---------------------------------------------------------------------------
# Refresh failure modes
# ---------------------------------------------------------------------------


def test_refresh_transport_error_doesnt_reregister(bridge, transport, verifier):
    url = "https://partner.example.com/bundle"
    transport.seed(url, bridge_mod.TransportError("network unreachable"))
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.TRANSPORT_ERROR
    assert verifier.registrations == []
    assert bridge.stats.refresh_transport_error == 1


def test_refresh_parse_error(bridge, transport):
    url = "https://partner.example.com/bundle"
    transport.seed(url, b"this is not json")
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.PARSE_ERROR
    assert bridge.stats.refresh_parse_error == 1


def test_refresh_parse_error_trust_domain_mismatch(bridge, transport):
    url = "https://partner.example.com/bundle"
    transport.seed(
        url,
        _bundle_json("spiffe://impostor.example.com", 1),
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.PARSE_ERROR
    assert "impostor" in outcome.reason


def test_refresh_pin_mismatch(bridge, transport):
    url = "https://partner.example.com/bundle"
    body = _bundle_json("spiffe://partner.example.com", 1)
    transport.seed(url, body)
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
        pinned_sha256="0" * 64,  # wrong
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.PIN_MISMATCH
    assert bridge.stats.refresh_pin_mismatch == 1


def test_refresh_pin_match_succeeds(bridge, transport):
    import hashlib

    url = "https://partner.example.com/bundle"
    body = _bundle_json("spiffe://partner.example.com", 1)
    pin = hashlib.sha256(body).hexdigest()
    transport.seed(url, body)
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
        pinned_sha256=pin,
    )
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW


# ---------------------------------------------------------------------------
# REGRESSION (sequence-number monotonicity)
# ---------------------------------------------------------------------------


def test_refresh_rejects_sequence_regression(bridge, transport):
    url = "https://partner.example.com/bundle"
    transport.seed_sequence(
        url,
        [
            _bundle_json("spiffe://partner.example.com", 5),
            _bundle_json("spiffe://partner.example.com", 3),  # backwards
        ],
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    o1 = bridge.refresh_partner("spiffe://partner.example.com")
    o2 = bridge.refresh_partner("spiffe://partner.example.com")
    assert o1.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert o2.kind == bridge_mod.RefreshOutcomeKind.REGRESSION
    assert "rejected" in o2.reason
    assert bridge.stats.refresh_regression == 1


def test_force_rollback_accepts_regression(bridge, transport, verifier):
    url = "https://partner.example.com/bundle"
    transport.seed_sequence(
        url,
        [
            _bundle_json("spiffe://partner.example.com", 5),
            _bundle_json("spiffe://partner.example.com", 3),
        ],
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    bridge.refresh_partner("spiffe://partner.example.com")
    forced = bridge.refresh_partner("spiffe://partner.example.com", force_rollback=True)
    assert forced.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert forced.sequence_number == 3
    # And the verifier saw the new keys despite the rollback.
    assert len(verifier.registrations) == 2


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoke_partner_removes_record_and_verifier_registration(
    bridge, transport, verifier
):
    url = "https://partner.example.com/bundle"
    transport.seed(url, _bundle_json("spiffe://partner.example.com", 1))
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    bridge.refresh_partner("spiffe://partner.example.com")
    assert bridge.revoke_partner("spiffe://partner.example.com") is True
    assert bridge.list_partners() == ()
    # Verifier got an unregister for the revoke (3rd one — initial,
    # rotation, revoke).
    assert verifier.unregistrations[-1] == "spiffe://partner.example.com"
    assert bridge.stats.partners_revoked == 1


def test_revoke_unknown_partner_returns_false(bridge):
    assert bridge.revoke_partner("spiffe://nobody.example.com") is False


# ---------------------------------------------------------------------------
# refresh_all
# ---------------------------------------------------------------------------


def test_refresh_all_iterates_every_partner(bridge, transport):
    transport.seed(
        "https://a.example.com/b",
        _bundle_json("spiffe://a.example.com", 1),
    )
    transport.seed(
        "https://b.example.com/b",
        _bundle_json("spiffe://b.example.com", 1),
    )
    bridge.register_partner(
        trust_domain="spiffe://a.example.com",
        bundle_endpoint_url="https://a.example.com/b",
    )
    bridge.register_partner(
        trust_domain="spiffe://b.example.com",
        bundle_endpoint_url="https://b.example.com/b",
    )
    outcomes = bridge.refresh_all()
    assert len(outcomes) == 2
    assert all(o.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW for o in outcomes)


# ---------------------------------------------------------------------------
# Profile gating
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B.4 — HTTPS_SPIFFE pinned-anchor bootstrap
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402

_TD = "spiffe://partner.example.com"
_URL = "https://partner.example.com/bundle"


def test_https_spiffe_register_without_anchor_refused(bridge):
    """Fail-closed: no pinned trust anchor → refuse to federate."""
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain=_TD,
            bundle_endpoint_url=_URL,
            profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        )


def test_https_spiffe_bootstrap_with_anchor_succeeds(bridge, transport, verifier):
    body = _bundle_json(_TD, 1)
    pin = hashlib.sha256(body).hexdigest()
    transport.seed(_URL, body)
    bridge.register_partner(
        trust_domain=_TD,
        bundle_endpoint_url=_URL,
        profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        pinned_sha256=pin,
    )
    outcome = bridge.refresh_partner(_TD)
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert bridge.stats.spiffe_bootstraps == 1
    assert verifier.registrations  # keys landed on the verifier


def test_https_spiffe_wrong_anchor_rejected(bridge, transport):
    body = _bundle_json(_TD, 1)
    wrong_pin = hashlib.sha256(b"not-the-bundle").hexdigest()
    transport.seed(_URL, body)
    bridge.register_partner(
        trust_domain=_TD,
        bundle_endpoint_url=_URL,
        profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        pinned_sha256=wrong_pin,
    )
    outcome = bridge.refresh_partner(_TD)
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.PIN_MISMATCH
    assert bridge.stats.spiffe_bootstraps == 0


def test_https_spiffe_post_bootstrap_rotation_no_pin_applied(bridge, transport):
    """After bootstrap, a rotated bundle (different body → different SHA)
    must refresh successfully: the body pin is the bootstrap ANCHOR, not a
    per-fetch lock, so rotation works."""
    body1 = _bundle_json(_TD, 1)
    body2 = _bundle_json(_TD, 2)
    assert hashlib.sha256(body1).hexdigest() != hashlib.sha256(body2).hexdigest()
    transport.seed_sequence(_URL, [body1, body2])
    bridge.register_partner(
        trust_domain=_TD,
        bundle_endpoint_url=_URL,
        profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        pinned_sha256=hashlib.sha256(body1).hexdigest(),
    )
    first = bridge.refresh_partner(_TD)
    assert first.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    # Rotation: body2's SHA != anchor, but post-bootstrap pin is not applied.
    second = bridge.refresh_partner(_TD)
    assert second.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert second.sequence_number == 2


def test_https_spiffe_refresh_anchor_required_defense_in_depth(bridge, transport):
    """Defense-in-depth: if a record somehow reaches refresh with profile
    HTTPS_SPIFFE, not yet synced, and no anchor, refresh fail-closes with
    ANCHOR_REQUIRED (registration normally blocks this earlier)."""
    body = _bundle_json(_TD, 1)
    transport.seed(_URL, body)
    rec = bridge.register_partner(
        trust_domain=_TD,
        bundle_endpoint_url=_URL,
        profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        pinned_sha256=hashlib.sha256(body).hexdigest(),
    )
    # Strip the anchor out-of-band to exercise the refresh-layer guard.
    bridge._partners[_TD] = bridge_mod.PartnerRecord(
        **{**rec.__dict__, "pinned_sha256": None}
    )
    outcome = bridge.refresh_partner(_TD)
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.ANCHOR_REQUIRED
    assert bridge.stats.refresh_anchor_required == 1


# ---------------------------------------------------------------------------
# Verifier pass-through + stats
# ---------------------------------------------------------------------------


def test_verify_cross_domain_jwt_passes_through_to_verifier(bridge, verifier):
    bridge.verify_cross_domain_jwt("eyJhbGciOiJFZERTQSJ9.payload.sig")
    assert verifier.verify_calls == ["eyJhbGciOiJFZERTQSJ9.payload.sig"]
    assert bridge.stats.cross_domain_verifies == 1


# ---------------------------------------------------------------------------
# Transport input validation
# ---------------------------------------------------------------------------


def test_http_bundle_transport_rejects_non_https():
    t = bridge_mod.HttpBundleTransport()
    with pytest.raises(ValueError):
        t.fetch(
            "http://partner.example.com/bundle",
            profile=bridge_mod.BundleProfile.HTTPS_WEB,
        )


def test_http_bundle_transport_accepts_spiffe_profile_but_rejects_http():
    """B.4: the transport now accepts the HTTPS_SPIFFE profile (the pin
    anchor is enforced at the bridge), but still rejects plaintext URLs
    regardless of profile."""
    t = bridge_mod.HttpBundleTransport()
    with pytest.raises(ValueError):
        t.fetch(
            "http://partner.example.com/bundle",
            profile=bridge_mod.BundleProfile.HTTPS_SPIFFE,
        )


# ---------------------------------------------------------------------------
# PartnerRecord lifecycle bookkeeping
# ---------------------------------------------------------------------------


def test_partner_record_reflects_last_refresh_outcome(bridge, transport):
    url = "https://partner.example.com/bundle"
    transport.seed(url, _bundle_json("spiffe://partner.example.com", 1))
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    bridge.refresh_partner("spiffe://partner.example.com")
    rec = bridge.list_partners()[0]
    assert rec.last_refresh_outcome == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    assert rec.sequence_number == 1
    assert rec.keys_seen == 1
    assert rec.last_refresh_ts is not None


# ---------------------------------------------------------------------------
# Refresh of unknown partner raises
# ---------------------------------------------------------------------------


def test_refresh_unknown_partner_raises(bridge):
    with pytest.raises(KeyError):
        bridge.refresh_partner("spiffe://nobody.example.com")


# ---------------------------------------------------------------------------
# In-memory transport sequence exhaustion
# ---------------------------------------------------------------------------


def test_in_memory_transport_sequence_exhaustion_surfaces(bridge, transport):
    url = "https://partner.example.com/bundle"
    transport.seed_sequence(url, [_bundle_json("spiffe://partner.example.com", 1)])
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    bridge.refresh_partner("spiffe://partner.example.com")
    # Second call exhausts the sequence and we get TRANSPORT_ERROR.
    outcome = bridge.refresh_partner("spiffe://partner.example.com")
    assert outcome.kind == bridge_mod.RefreshOutcomeKind.TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# E2E — the documented cross-domain identity acceptance flow
# ---------------------------------------------------------------------------


def test_e2e_cross_domain_acceptance_flow(bridge, transport, verifier):
    """The motivating Wave B.1 flow:
    1. AIDG registers a partner trust domain.
    2. Bridge refreshes the partner's bundle.
    3. AIDG verifies an incoming JWT supposedly issued by the
       partner; the verifier returns the federated identity.
    """
    url = "https://partner.example.com/bundle"
    transport.seed(
        url,
        _bundle_json(
            "spiffe://partner.example.com",
            1,
            keys=[{"kty": "OKP", "crv": "Ed25519", "kid": "partner-2026"}],
        ),
    )
    bridge.register_partner(
        trust_domain="spiffe://partner.example.com",
        bundle_endpoint_url=url,
    )
    o = bridge.refresh_partner("spiffe://partner.example.com")
    assert o.kind == bridge_mod.RefreshOutcomeKind.SUCCESS_NEW
    # Step 3 — verify a token. (FakeVerifier always succeeds; the
    # point of this test is that the bridge's pass-through wires the
    # verifier call correctly.)
    result = bridge.verify_cross_domain_jwt(
        "eyJhbGciOiJFZERTQSJ9.eyJpc3MiOiJzcGlmZmU6Ly9wYXJ0bmVyLmV4YW1wbGUuY29tIn0.sig"
    )
    assert result == "fake-verified-identity"
    assert bridge.stats.partners_registered == 1
    assert bridge.stats.refresh_success_new == 1
    assert bridge.stats.cross_domain_verifies == 1
