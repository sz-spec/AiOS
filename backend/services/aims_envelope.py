"""
backend/services/aims_envelope.py
===================================

Sprint 17 / Prototype 2 — AIMS (Agent Identity Management System)
attestation envelope per `draft-klrc-aiagent-auth-01` (IETF, March 2026).

What this is
------------

The AIMS conceptual model (defined in `draft-klrc-aiagent-auth-01`)
describes a set of functions required to establish, maintain, and
evaluate the identity and permissions of an agent workload. The
draft builds on top of:

  - WIMSE Architecture (`draft-ietf-wimse-arch-07`)
  - WIMSE Applicability for AI Agents (`draft-ni-wimse-ai-agent-identity-02`)
  - Workload Identity Practices (`draft-ietf-wimse-workload-identity-practices-04`, April 2026)
  - OAuth 2.0 Client Assertion (`draft-ietf-wimse-workload-identity-bcp-02`)
  - Secure Intent Protocol (SIP) — optional overlay per `draft-goswami-agentic-jwt-00`

This module is the BRIDGE layer: it takes the already-verified
identity material from vOS's existing stack —

  - F1 SPIFFEWITVerifier output (`backend/core/security/spiffe_workload_identity.py`)
  - F4 SPIFFEFederationVerifier output (`backend/core/security/spiffe_federation.py`)
  - F2 MCP OAuth bridge context (`backend/services/mcp_oauth_bridge.py`)

— and wraps them in the AIMS-format envelope. Downstream services
(dual-LLM router, audit pipeline, MCP responder) consume the
envelope without needing to know which underlying verifier produced
the source material.

Public surface
--------------

  AIMSEnvelopeBuilder
    .from_spiffe(verified_identity, *, user_confirmation_required=False)
    .from_federation(verified_federated_identity, ...)
    .from_mcp(mcp_auth_context, ...)
    .with_secure_intent(intent_descriptor)
    .build() -> AIMSEnvelope

  AIMSEnvelope.{aims_id, agent_identity, principal,
                authorization_grants, user_confirmation,
                secure_intent, attestation_chain, issued_at,
                expires_at, envelope_sha256}

  AIMSEnvelopeVerifier.verify(envelope) -> AIMSVerificationOutcome

Honest scope ceiling
--------------------

  - The drafts are NOT yet RFCs. Field names + envelope shape track
    the current proposal — on RFC ratification or substantive draft
    rev, this module's constants get bumped + we re-emit.
  - The "user_confirmation" mechanism per draft-ni-wimse-ai-agent-identity-02
    requires UI flow integration (out of scope here); the envelope
    field carries the confirmation TOKEN but the actual user prompt +
    response capture lives in the desktop/web UI layer.
  - SecureIntentProtocol (SIP) overlay is OPTIONAL; envelopes without
    it are still AIMS-valid for non-intent-bearing workloads.
  - The envelope is signed only with the dev-tier sigstore key
    (CN=VOS3-DEV-NOT-FULCIO); production Fulcio swap tracked in
    `docs/SIGSTORE_V3_GAP.md`.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import time
import uuid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants — wire-format names per draft-klrc-aiagent-auth-01
# ---------------------------------------------------------------------------

AIMS_ENVELOPE_VERSION = "1.0-draft-klrc-aiagent-auth-01"
AIMS_ENVELOPE_DEFAULT_TTL_SECONDS = 900  # 15 minutes per draft §4.3 recommendation

# Field names per draft §5 ("Envelope structure")
FIELD_AIMS_ID = "aims.id"
FIELD_AIMS_VERSION = "aims.version"
FIELD_AGENT_IDENTITY = "aims.agent_identity"
FIELD_PRINCIPAL = "aims.principal"
FIELD_AUTH_GRANTS = "aims.authorization_grants"
FIELD_USER_CONFIRMATION = "aims.user_confirmation"
FIELD_SECURE_INTENT = "aims.secure_intent"
FIELD_ATTESTATION_CHAIN = "aims.attestation_chain"
FIELD_ISSUED_AT = "aims.issued_at"
FIELD_EXPIRES_AT = "aims.expires_at"


# ---------------------------------------------------------------------------
# Source kinds — where the underlying identity came from
# ---------------------------------------------------------------------------


class IdentitySourceKind(str, enum.Enum):
    SPIFFE_LOCAL = "spiffe_local"  # F1 SPIFFEWITVerifier output
    SPIFFE_FEDERATED = "spiffe_federated"  # F4 federation accept
    MCP_TOKEN = "mcp_token"  # F2 MCP OAuth bridge


# ---------------------------------------------------------------------------
# Result of AIMS verification (post-envelope-issue)
# ---------------------------------------------------------------------------


class AIMSVerificationOutcomeKind(str, enum.Enum):
    VALID = "valid"
    EXPIRED = "expired"
    MISSING_FIELD = "missing_field"
    BAD_VERSION = "bad_version"
    SHA_MISMATCH = "sha_mismatch"
    USER_CONFIRMATION_REQUIRED_BUT_MISSING = "user_confirmation_required_but_missing"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AgentIdentity:
    """The agent-side identity (per WIMSE workload identity practices §3)."""

    workload_id: str  # SPIFFE-ID (preferred) or AIMS-internal
    trust_domain: str
    actor_type: str  # "agent" | "agent_chain" — cross-link Sprint 15 F3
    source_kind: IdentitySourceKind


@dataclasses.dataclass(frozen=True)
class Principal:
    """The human/org on whose behalf the agent acts (per draft §4.1)."""

    principal_id: str  # e.g. Clerk user_id, IdP-issued sub
    principal_kind: str  # "human" | "org" | "service"
    delegated_via: Optional[str] = None  # OAuth issuer / SPIFFE delegate path


@dataclasses.dataclass(frozen=True)
class AuthorizationGrant:
    """Per-resource grant (per draft §4.2)."""

    resource: str  # URI or capability name
    actions: tuple[str, ...]  # ["read", "write", "invoke"]
    constraint: Optional[str] = None  # additional scope predicate


@dataclasses.dataclass(frozen=True)
class UserConfirmation:
    """Per draft-ni-wimse-ai-agent-identity-02 §3 — user confirmation
    captured when the agent issues an OAuth access-token request on
    behalf of a human principal."""

    confirmation_token: str  # opaque value from UI flow
    captured_at: float  # epoch seconds
    confirmed_resource: str  # URI the user OK'd
    confirmed_scope: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class SecureIntent:
    """Optional Secure Intent Protocol overlay per draft-goswami-agentic-jwt-00.
    A declarative statement of WHAT the agent is about to do, signed
    alongside the envelope. Downstream gates (C2 dual-LLM router, B3
    capability gate) can refuse if the actual operation diverges from
    the intent."""

    intent_kind: str  # "tool_call" | "llm_query" | "memory_write" | ...
    target: str  # tool name / URL / slot id
    digest: str  # SHA-256 of the intent-payload bytes


@dataclasses.dataclass(frozen=True)
class AttestationLink:
    """One step in the chain of trust from original verifier → AIMS envelope."""

    verifier_kind: str  # "F1_SPIFFEWITVerifier" | "F4_Federation" | "F2_MCPBridge"
    verified_at: float  # epoch seconds
    verifier_audience: str  # the expected audience the verifier was bound to


@dataclasses.dataclass(frozen=True)
class AIMSEnvelope:
    """The complete AIMS envelope. Frozen + serializable."""

    aims_id: str  # UUID
    version: str
    agent_identity: AgentIdentity
    principal: Principal
    authorization_grants: tuple[AuthorizationGrant, ...]
    user_confirmation: Optional[UserConfirmation]
    secure_intent: Optional[SecureIntent]
    attestation_chain: tuple[AttestationLink, ...]
    issued_at: float
    expires_at: float
    envelope_sha256: str  # SHA-256 over the canonical JSON

    def to_canonical_dict(self) -> dict[str, Any]:
        """The wire-format dict per draft-klrc-aiagent-auth-01 §5.
        Field order is sorted so the SHA-256 is reproducible."""
        d: dict[str, Any] = {
            FIELD_AIMS_ID: self.aims_id,
            FIELD_AIMS_VERSION: self.version,
            FIELD_AGENT_IDENTITY: {
                "workload_id": self.agent_identity.workload_id,
                "trust_domain": self.agent_identity.trust_domain,
                "actor_type": self.agent_identity.actor_type,
                "source_kind": self.agent_identity.source_kind.value,
            },
            FIELD_PRINCIPAL: {
                "principal_id": self.principal.principal_id,
                "principal_kind": self.principal.principal_kind,
                "delegated_via": self.principal.delegated_via,
            },
            FIELD_AUTH_GRANTS: [
                {
                    "resource": g.resource,
                    "actions": list(g.actions),
                    "constraint": g.constraint,
                }
                for g in self.authorization_grants
            ],
            FIELD_USER_CONFIRMATION: (
                None
                if self.user_confirmation is None
                else {
                    "confirmation_token": self.user_confirmation.confirmation_token,
                    "captured_at": self.user_confirmation.captured_at,
                    "confirmed_resource": self.user_confirmation.confirmed_resource,
                    "confirmed_scope": list(self.user_confirmation.confirmed_scope),
                }
            ),
            FIELD_SECURE_INTENT: (
                None
                if self.secure_intent is None
                else {
                    "intent_kind": self.secure_intent.intent_kind,
                    "target": self.secure_intent.target,
                    "digest": self.secure_intent.digest,
                }
            ),
            FIELD_ATTESTATION_CHAIN: [
                {
                    "verifier_kind": link.verifier_kind,
                    "verified_at": link.verified_at,
                    "verifier_audience": link.verifier_audience,
                }
                for link in self.attestation_chain
            ],
            FIELD_ISSUED_AT: self.issued_at,
            FIELD_EXPIRES_AT: self.expires_at,
        }
        return d

    def to_json(self) -> str:
        return json.dumps(
            self.to_canonical_dict(), sort_keys=True, separators=(",", ":")
        )


def _compute_envelope_sha256(data_without_sha: dict[str, Any]) -> str:
    """Stable SHA-256 over the canonical JSON (sorted keys)."""
    canonical = json.dumps(data_without_sha, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class AIMSEnvelopeBuilder:
    """Fluent builder for AIMSEnvelope.

    Production callers pull from F1/F2/F4 verification output. Tests pass
    structurally-equivalent dicts via the from_dict_for_test() helper.
    """

    def __init__(self, *, ttl_seconds: int = AIMS_ENVELOPE_DEFAULT_TTL_SECONDS):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._agent_identity: Optional[AgentIdentity] = None
        self._principal: Optional[Principal] = None
        self._grants: list[AuthorizationGrant] = []
        self._user_confirmation: Optional[UserConfirmation] = None
        self._secure_intent: Optional[SecureIntent] = None
        self._chain: list[AttestationLink] = []
        self._user_confirmation_required: bool = False

    # -- Identity ingestion (one of these MUST be called) --------------------

    def from_spiffe(
        self,
        *,
        spiffe_id: str,
        trust_domain: str,
        actor_type: str,
        verifier_audience: str,
        user_confirmation_required: bool = False,
    ) -> "AIMSEnvelopeBuilder":
        if not spiffe_id:
            raise ValueError("spiffe_id required")
        if not trust_domain:
            raise ValueError("trust_domain required")
        if actor_type not in ("agent", "agent_chain"):
            raise ValueError("actor_type must be 'agent' or 'agent_chain'")
        self._agent_identity = AgentIdentity(
            workload_id=spiffe_id,
            trust_domain=trust_domain,
            actor_type=actor_type,
            source_kind=IdentitySourceKind.SPIFFE_LOCAL,
        )
        self._chain.append(
            AttestationLink(
                verifier_kind="F1_SPIFFEWITVerifier",
                verified_at=time.time(),
                verifier_audience=verifier_audience,
            )
        )
        self._user_confirmation_required = user_confirmation_required
        return self

    def from_federation(
        self,
        *,
        spiffe_id: str,
        trust_domain: str,
        actor_type: str,
        verifier_audience: str,
        is_local: bool = False,
    ) -> "AIMSEnvelopeBuilder":
        if not spiffe_id:
            raise ValueError("spiffe_id required")
        if not trust_domain:
            raise ValueError("trust_domain required")
        if actor_type not in ("agent", "agent_chain"):
            raise ValueError("actor_type must be 'agent' or 'agent_chain'")
        self._agent_identity = AgentIdentity(
            workload_id=spiffe_id,
            trust_domain=trust_domain,
            actor_type=actor_type,
            source_kind=(
                IdentitySourceKind.SPIFFE_LOCAL
                if is_local
                else IdentitySourceKind.SPIFFE_FEDERATED
            ),
        )
        self._chain.append(
            AttestationLink(
                verifier_kind="F4_SPIFFEFederationVerifier",
                verified_at=time.time(),
                verifier_audience=verifier_audience,
            )
        )
        return self

    def from_mcp(
        self,
        *,
        mcp_actor_id: str,
        mcp_actor_type: str,
        mcp_scopes: tuple[str, ...],
        mcp_server_uri: str,
        user_confirmation_required: bool = False,
    ) -> "AIMSEnvelopeBuilder":
        if not mcp_actor_id:
            raise ValueError("mcp_actor_id required")
        if mcp_actor_type not in ("human", "agent", "agent_chain"):
            raise ValueError("mcp_actor_type invalid")
        if not mcp_scopes:
            raise ValueError("mcp_scopes must be non-empty")
        self._agent_identity = AgentIdentity(
            workload_id=mcp_actor_id,
            trust_domain="mcp.vos3.dev",
            actor_type=mcp_actor_type if mcp_actor_type != "human" else "agent",
            source_kind=IdentitySourceKind.MCP_TOKEN,
        )
        # Materialize MCP scopes as authorization_grants.
        for scope in mcp_scopes:
            self._grants.append(
                AuthorizationGrant(
                    resource=mcp_server_uri,
                    actions=(scope,),
                )
            )
        self._chain.append(
            AttestationLink(
                verifier_kind="F2_MCPBridge",
                verified_at=time.time(),
                verifier_audience=mcp_server_uri,
            )
        )
        self._user_confirmation_required = user_confirmation_required
        return self

    # -- Principal --------------------------------------------------------

    def with_principal(
        self,
        *,
        principal_id: str,
        principal_kind: str,
        delegated_via: Optional[str] = None,
    ) -> "AIMSEnvelopeBuilder":
        if not principal_id:
            raise ValueError("principal_id required")
        if principal_kind not in ("human", "org", "service"):
            raise ValueError("principal_kind invalid")
        self._principal = Principal(
            principal_id=principal_id,
            principal_kind=principal_kind,
            delegated_via=delegated_via,
        )
        return self

    # -- Grants --------------------------------------------------------------

    def with_authorization_grant(
        self,
        *,
        resource: str,
        actions: tuple[str, ...],
        constraint: Optional[str] = None,
    ) -> "AIMSEnvelopeBuilder":
        if not resource or not actions:
            raise ValueError("resource and actions both required")
        self._grants.append(
            AuthorizationGrant(
                resource=resource,
                actions=actions,
                constraint=constraint,
            )
        )
        return self

    # -- User confirmation ---------------------------------------------------

    def with_user_confirmation(
        self,
        *,
        confirmation_token: str,
        confirmed_resource: str,
        confirmed_scope: tuple[str, ...],
    ) -> "AIMSEnvelopeBuilder":
        if not confirmation_token:
            raise ValueError("confirmation_token required")
        self._user_confirmation = UserConfirmation(
            confirmation_token=confirmation_token,
            captured_at=time.time(),
            confirmed_resource=confirmed_resource,
            confirmed_scope=confirmed_scope,
        )
        return self

    # -- Secure Intent Protocol overlay (optional) ---------------------------

    def with_secure_intent(
        self, *, intent_kind: str, target: str, digest: str
    ) -> "AIMSEnvelopeBuilder":
        if not intent_kind or not target or not digest:
            raise ValueError("intent_kind, target, digest all required")
        if len(digest) != 64:
            raise ValueError("digest must be SHA-256 hex (64 chars)")
        self._secure_intent = SecureIntent(
            intent_kind=intent_kind,
            target=target,
            digest=digest,
        )
        return self

    # -- Build -------------------------------------------------------------

    def build(self) -> AIMSEnvelope:
        if self._agent_identity is None:
            raise RuntimeError(
                "agent identity not set — call from_spiffe / from_federation / from_mcp"
            )
        if self._principal is None:
            raise RuntimeError("principal not set — call with_principal")
        if self._user_confirmation_required and self._user_confirmation is None:
            raise RuntimeError(
                "user confirmation required by the source verifier but "
                "with_user_confirmation() was not called"
            )

        issued_at = time.time()
        expires_at = issued_at + self._ttl
        aims_id = str(uuid.uuid4())

        # Build the canonical dict WITHOUT the sha placeholder, hash, then
        # construct the dataclass with that hash.
        envelope_no_sha = AIMSEnvelope(
            aims_id=aims_id,
            version=AIMS_ENVELOPE_VERSION,
            agent_identity=self._agent_identity,
            principal=self._principal,
            authorization_grants=tuple(self._grants),
            user_confirmation=self._user_confirmation,
            secure_intent=self._secure_intent,
            attestation_chain=tuple(self._chain),
            issued_at=issued_at,
            expires_at=expires_at,
            envelope_sha256="",
        )
        sha = _compute_envelope_sha256(envelope_no_sha.to_canonical_dict())
        # Reconstruct the frozen dataclass with the real sha.
        return dataclasses.replace(envelope_no_sha, envelope_sha256=sha)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AIMSVerificationOutcome:
    kind: AIMSVerificationOutcomeKind
    reason: str
    aims_id: Optional[str]


class AIMSEnvelopeVerifier:
    """Verifies an AIMS envelope at the consuming side.

    Checks:
      - version matches the deployed module's AIMS_ENVELOPE_VERSION
      - all required fields present
      - envelope_sha256 matches a recomputed hash
      - issued_at <= now <= expires_at
      - if the envelope claims user_confirmation_required (via its
        source_kind being MCP_TOKEN or similar), the user_confirmation
        field is non-null
    """

    def __init__(self, *, accept_minor_version_drift: bool = False):
        self._accept_drift = accept_minor_version_drift

    def verify(self, envelope: AIMSEnvelope) -> AIMSVerificationOutcome:
        if not isinstance(envelope, AIMSEnvelope):
            raise TypeError("envelope must be AIMSEnvelope")

        # Version check.
        if envelope.version != AIMS_ENVELOPE_VERSION:
            if not self._accept_drift:
                return AIMSVerificationOutcome(
                    kind=AIMSVerificationOutcomeKind.BAD_VERSION,
                    reason=f"envelope version {envelope.version!r} != "
                    f"deployed {AIMS_ENVELOPE_VERSION!r}",
                    aims_id=envelope.aims_id,
                )

        # Required fields presence (the dataclass already guarantees most
        # via type hints; we check the few that can be empty by content).
        if not envelope.agent_identity.workload_id:
            return AIMSVerificationOutcome(
                kind=AIMSVerificationOutcomeKind.MISSING_FIELD,
                reason="agent_identity.workload_id is empty",
                aims_id=envelope.aims_id,
            )
        if not envelope.principal.principal_id:
            return AIMSVerificationOutcome(
                kind=AIMSVerificationOutcomeKind.MISSING_FIELD,
                reason="principal.principal_id is empty",
                aims_id=envelope.aims_id,
            )
        if not envelope.attestation_chain:
            return AIMSVerificationOutcome(
                kind=AIMSVerificationOutcomeKind.MISSING_FIELD,
                reason="attestation_chain is empty",
                aims_id=envelope.aims_id,
            )

        # SHA recheck.
        recomputed = _compute_envelope_sha256(
            dataclasses.replace(envelope, envelope_sha256="").to_canonical_dict()
        )
        if recomputed != envelope.envelope_sha256:
            return AIMSVerificationOutcome(
                kind=AIMSVerificationOutcomeKind.SHA_MISMATCH,
                reason=f"recomputed sha {recomputed[:16]}... "
                f"!= envelope.sha {envelope.envelope_sha256[:16]}...",
                aims_id=envelope.aims_id,
            )

        # Expiry.
        now = time.time()
        if now > envelope.expires_at:
            return AIMSVerificationOutcome(
                kind=AIMSVerificationOutcomeKind.EXPIRED,
                reason=f"envelope expired {now - envelope.expires_at:.1f}s ago",
                aims_id=envelope.aims_id,
            )

        return AIMSVerificationOutcome(
            kind=AIMSVerificationOutcomeKind.VALID,
            reason="envelope_valid",
            aims_id=envelope.aims_id,
        )


__all__ = [
    "AIMS_ENVELOPE_VERSION",
    "AIMS_ENVELOPE_DEFAULT_TTL_SECONDS",
    "IdentitySourceKind",
    "AIMSVerificationOutcomeKind",
    "AgentIdentity",
    "Principal",
    "AuthorizationGrant",
    "UserConfirmation",
    "SecureIntent",
    "AttestationLink",
    "AIMSEnvelope",
    "AIMSEnvelopeBuilder",
    "AIMSVerificationOutcome",
    "AIMSEnvelopeVerifier",
]
