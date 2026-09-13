"""
backend/tests/integration/test_aims_envelope.py

Sprint 17 / Prototype 2 — AIMS attestation envelope tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AE_PATH = _REPO_ROOT / "backend" / "services" / "aims_envelope.py"
_spec = importlib.util.spec_from_file_location("vos3_aims_under_test", _AE_PATH)
ae = importlib.util.module_from_spec(_spec)
sys.modules["vos3_aims_under_test"] = ae
_spec.loader.exec_module(ae)


# ---------------------------------------------------------------------------
# Builder validation
# ---------------------------------------------------------------------------


def test_builder_init_rejects_zero_ttl():
    with pytest.raises(ValueError):
        ae.AIMSEnvelopeBuilder(ttl_seconds=0)


def test_build_without_identity_raises():
    b = ae.AIMSEnvelopeBuilder()
    b.with_principal(principal_id="u1", principal_kind="human")
    with pytest.raises(RuntimeError, match="agent identity not set"):
        b.build()


def test_build_without_principal_raises():
    b = ae.AIMSEnvelopeBuilder()
    b.from_spiffe(
        spiffe_id="spiffe://x.com/a",
        trust_domain="x.com",
        actor_type="agent",
        verifier_audience="vos3-platform",
    )
    with pytest.raises(RuntimeError, match="principal not set"):
        b.build()


def test_from_spiffe_rejects_invalid_actor_type():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError, match="actor_type"):
        b.from_spiffe(
            spiffe_id="x",
            trust_domain="x.com",
            actor_type="not-valid",
            verifier_audience="aud",
        )


def test_from_mcp_rejects_empty_scopes():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError, match="mcp_scopes"):
        b.from_mcp(
            mcp_actor_id="u1",
            mcp_actor_type="agent",
            mcp_scopes=(),
            mcp_server_uri="https://mcp",
        )


def test_from_mcp_rejects_invalid_actor_type():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError, match="mcp_actor_type"):
        b.from_mcp(
            mcp_actor_id="u1",
            mcp_actor_type="robot",
            mcp_scopes=("read",),
            mcp_server_uri="https://mcp",
        )


def test_with_principal_rejects_invalid_kind():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError, match="principal_kind"):
        b.with_principal(principal_id="u1", principal_kind="alien")


def test_with_secure_intent_rejects_bad_digest_length():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError, match="SHA-256"):
        b.with_secure_intent(intent_kind="tool_call", target="x", digest="too-short")


def test_with_authorization_grant_rejects_empty_resource():
    b = ae.AIMSEnvelopeBuilder()
    with pytest.raises(ValueError):
        b.with_authorization_grant(resource="", actions=("read",))


# ---------------------------------------------------------------------------
# Build happy paths
# ---------------------------------------------------------------------------


def test_build_from_spiffe_minimal():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://example.com/agent-1",
            trust_domain="example.com",
            actor_type="agent",
            verifier_audience="vos3-platform",
        )
        .with_principal(principal_id="user_abc", principal_kind="human")
        .build()
    )
    assert env.version == ae.AIMS_ENVELOPE_VERSION
    assert env.agent_identity.workload_id == "spiffe://example.com/agent-1"
    assert env.agent_identity.actor_type == "agent"
    assert env.agent_identity.source_kind == ae.IdentitySourceKind.SPIFFE_LOCAL
    assert env.principal.principal_id == "user_abc"
    assert env.principal.principal_kind == "human"
    assert len(env.attestation_chain) == 1
    assert env.attestation_chain[0].verifier_kind == "F1_SPIFFEWITVerifier"
    assert len(env.envelope_sha256) == 64
    int(env.envelope_sha256, 16)  # raises if not hex


def test_build_from_federation_records_correct_source():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_federation(
            spiffe_id="spiffe://partner.com/x",
            trust_domain="partner.com",
            actor_type="agent",
            verifier_audience="vos3-platform",
            is_local=False,
        )
        .with_principal(principal_id="p1", principal_kind="org")
        .build()
    )
    assert env.agent_identity.source_kind == ae.IdentitySourceKind.SPIFFE_FEDERATED
    assert env.attestation_chain[0].verifier_kind == "F4_SPIFFEFederationVerifier"


def test_build_from_mcp_materializes_scopes_as_grants():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_mcp(
            mcp_actor_id="pat:abc",
            mcp_actor_type="agent",
            mcp_scopes=("mcp.tools.read", "mcp.tools.write"),
            mcp_server_uri="https://mcp.vos3.dev/v1",
        )
        .with_principal(principal_id="p1", principal_kind="service")
        .build()
    )
    assert env.agent_identity.source_kind == ae.IdentitySourceKind.MCP_TOKEN
    # 2 scopes ⇒ 2 grants
    assert len(env.authorization_grants) == 2
    actions_seen = {g.actions[0] for g in env.authorization_grants}
    assert actions_seen == {"mcp.tools.read", "mcp.tools.write"}


def test_build_with_user_confirmation():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_mcp(
            mcp_actor_id="pat:abc",
            mcp_actor_type="agent",
            mcp_scopes=("mcp.tools.write",),
            mcp_server_uri="https://mcp.vos3.dev/v1",
            user_confirmation_required=True,
        )
        .with_principal(principal_id="user_abc", principal_kind="human")
        .with_user_confirmation(
            confirmation_token="usr_confirm_xxx",
            confirmed_resource="https://mcp.vos3.dev/v1",
            confirmed_scope=("mcp.tools.write",),
        )
        .build()
    )
    assert env.user_confirmation is not None
    assert env.user_confirmation.confirmation_token == "usr_confirm_xxx"


def test_build_user_confirmation_required_but_missing_raises():
    b = (
        ae.AIMSEnvelopeBuilder()
        .from_mcp(
            mcp_actor_id="pat:abc",
            mcp_actor_type="agent",
            mcp_scopes=("mcp.tools.write",),
            mcp_server_uri="https://mcp.vos3.dev/v1",
            user_confirmation_required=True,
        )
        .with_principal(principal_id="user_abc", principal_kind="human")
    )
    with pytest.raises(RuntimeError, match="user confirmation required"):
        b.build()


def test_build_with_secure_intent():
    digest = "a" * 64
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .with_secure_intent(intent_kind="tool_call", target="fetch_url", digest=digest)
        .build()
    )
    assert env.secure_intent is not None
    assert env.secure_intent.intent_kind == "tool_call"
    assert env.secure_intent.target == "fetch_url"
    assert env.secure_intent.digest == digest


def test_build_with_explicit_authorization_grant():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .with_authorization_grant(
            resource="https://api.example.com/files",
            actions=("read", "write"),
            constraint="owner_only",
        )
        .build()
    )
    assert len(env.authorization_grants) == 1
    g = env.authorization_grants[0]
    assert g.resource == "https://api.example.com/files"
    assert g.actions == ("read", "write")
    assert g.constraint == "owner_only"


# ---------------------------------------------------------------------------
# Canonical serialization + hash stability
# ---------------------------------------------------------------------------


def test_to_canonical_dict_has_all_required_fields():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .build()
    )
    d = env.to_canonical_dict()
    for field in (
        ae.FIELD_AIMS_ID,
        ae.FIELD_AIMS_VERSION,
        ae.FIELD_AGENT_IDENTITY,
        ae.FIELD_PRINCIPAL,
        ae.FIELD_AUTH_GRANTS,
        ae.FIELD_USER_CONFIRMATION,
        ae.FIELD_SECURE_INTENT,
        ae.FIELD_ATTESTATION_CHAIN,
        ae.FIELD_ISSUED_AT,
        ae.FIELD_EXPIRES_AT,
    ):
        assert field in d


def test_to_json_is_sorted_keys_stable():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .build()
    )
    j1 = env.to_json()
    j2 = env.to_json()
    assert j1 == j2
    # Parses cleanly.
    json.loads(j1)


def test_envelope_sha256_matches_recomputed_hash():
    env = (
        ae.AIMSEnvelopeBuilder()
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .build()
    )
    # Recompute the same way the builder did + ensure it matches.
    import dataclasses as dc

    fresh = dc.replace(env, envelope_sha256="").to_canonical_dict()
    import hashlib

    recomp = hashlib.sha256(
        json.dumps(fresh, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert recomp == env.envelope_sha256


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def _good_envelope(ttl=300):
    return (
        ae.AIMSEnvelopeBuilder(ttl_seconds=ttl)
        .from_spiffe(
            spiffe_id="spiffe://x.com/a",
            trust_domain="x.com",
            actor_type="agent",
            verifier_audience="vos3",
        )
        .with_principal(principal_id="p1", principal_kind="human")
        .build()
    )


def test_verifier_accepts_valid_envelope():
    env = _good_envelope()
    out = ae.AIMSEnvelopeVerifier().verify(env)
    assert out.kind == ae.AIMSVerificationOutcomeKind.VALID


def test_verifier_rejects_non_envelope():
    with pytest.raises(TypeError):
        ae.AIMSEnvelopeVerifier().verify("not-an-envelope")  # type: ignore[arg-type]


def test_verifier_rejects_bad_version():
    import dataclasses as dc

    env = _good_envelope()
    tampered = dc.replace(env, version="9.99-fake")
    out = ae.AIMSEnvelopeVerifier().verify(tampered)
    assert out.kind == ae.AIMSVerificationOutcomeKind.BAD_VERSION


def test_verifier_accepts_drift_when_configured():
    import dataclasses as dc

    env = _good_envelope()
    tampered = dc.replace(env, version="9.99-fake")
    out = ae.AIMSEnvelopeVerifier(accept_minor_version_drift=True).verify(tampered)
    # Drift accepted; SHA recompute will still fail because we modified version.
    assert out.kind == ae.AIMSVerificationOutcomeKind.SHA_MISMATCH


def test_verifier_rejects_sha_mismatch():
    import dataclasses as dc

    env = _good_envelope()
    # Tamper the envelope SHA directly.
    tampered = dc.replace(env, envelope_sha256="0" * 64)
    out = ae.AIMSEnvelopeVerifier().verify(tampered)
    assert out.kind == ae.AIMSVerificationOutcomeKind.SHA_MISMATCH


def test_verifier_rejects_expired():
    env = _good_envelope(ttl=1)
    time.sleep(1.1)
    out = ae.AIMSEnvelopeVerifier().verify(env)
    assert out.kind == ae.AIMSVerificationOutcomeKind.EXPIRED


def test_verifier_rejects_missing_workload_id():
    import dataclasses as dc

    env = _good_envelope()
    bad_identity = dc.replace(env.agent_identity, workload_id="")
    tampered = dc.replace(env, agent_identity=bad_identity)
    out = ae.AIMSEnvelopeVerifier().verify(tampered)
    assert out.kind == ae.AIMSVerificationOutcomeKind.MISSING_FIELD


def test_verifier_rejects_missing_principal_id():
    import dataclasses as dc

    env = _good_envelope()
    bad_p = dc.replace(env.principal, principal_id="")
    tampered = dc.replace(env, principal=bad_p)
    out = ae.AIMSEnvelopeVerifier().verify(tampered)
    assert out.kind == ae.AIMSVerificationOutcomeKind.MISSING_FIELD


def test_verifier_rejects_empty_attestation_chain():
    import dataclasses as dc

    env = _good_envelope()
    tampered = dc.replace(env, attestation_chain=())
    out = ae.AIMSEnvelopeVerifier().verify(tampered)
    assert out.kind == ae.AIMSVerificationOutcomeKind.MISSING_FIELD


# ---------------------------------------------------------------------------
# End-to-end: multi-source + secure intent + verification
# ---------------------------------------------------------------------------


def test_end_to_end_federated_with_intent_and_verification():
    digest = "f" * 64
    env = (
        ae.AIMSEnvelopeBuilder(ttl_seconds=900)
        .from_federation(
            spiffe_id="spiffe://partner.example.com/agent-9",
            trust_domain="partner.example.com",
            actor_type="agent_chain",
            verifier_audience="vos3-platform",
        )
        .with_principal(
            principal_id="org:partner",
            principal_kind="org",
            delegated_via="https://idp.partner.example.com",
        )
        .with_authorization_grant(
            resource="https://api.vos3.dev/agents/run", actions=("invoke",)
        )
        .with_secure_intent(
            intent_kind="tool_call", target="execute_workflow", digest=digest
        )
        .build()
    )
    # Wire shape sanity.
    d = env.to_canonical_dict()
    assert d[ae.FIELD_AGENT_IDENTITY]["actor_type"] == "agent_chain"
    assert d[ae.FIELD_AGENT_IDENTITY]["source_kind"] == "spiffe_federated"
    assert d[ae.FIELD_SECURE_INTENT]["digest"] == digest
    # Verifier accepts.
    assert (
        ae.AIMSEnvelopeVerifier().verify(env).kind
        == ae.AIMSVerificationOutcomeKind.VALID
    )
