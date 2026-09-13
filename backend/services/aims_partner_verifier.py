"""
backend/services/aims_partner_verifier.py
==========================================

Sprint 18 / Wave 3.A / Cluster B.2 — Cross-domain AIMS envelope
verification.

What this is
------------

The other half of Cluster B's federation problem. Cluster B.1
(Sprint 17 W2) ships `IdentityFederationBridge` which synchronizes
SPIFFE trust bundles between vOS instances in different
administrative domains. That's the SYNC half.

B.2 (this module) is the VERIFY half: an AIMS envelope produced by
a partner trust domain, signed with the partner's Ed25519 key, can
now be verified end-to-end on the receiving side by:

  1. Looking up the partner's bundle keys via the bridge
  2. Finding the JWK whose `kid` matches the envelope's signing-key id
  3. Decoding the Ed25519 public key bytes (RFC 8037)
  4. Verifying the signature over the envelope's canonical JSON
  5. Running the existing self-consistency checks (version, SHA,
     expiry, presence) via the base AIMSEnvelopeVerifier
  6. Returning an outcome that records WHICH trust_domain the
     envelope was actually verified in, so the caller can apply
     domain-scoped authorization policy

Public surface
--------------

  AIMSPartnerVerifier(bridge, base_verifier=None)
    .verify(envelope, signature_bytes, signing_kid,
             expected_trust_domain) -> PartnerVerificationOutcome

  PartnerVerificationOutcome (frozen dataclass)
    .kind: PartnerVerificationOutcomeKind
    .trust_domain: Optional[str]  — populated for VALID and most
                                     soft-fail outcomes
    .reason: str
    .aims_id: Optional[str]
    .base_outcome: Optional[AIMSVerificationOutcome]  — surfaces the
                   self-consistency outcome for callers that care

Honest scope ceilings
---------------------

  - **Ed25519 only.** RSA + ECDSA support is Sprint 19+. The MVP
    targets `kty=OKP, crv=Ed25519` JWKs — the default AIMS envelope
    signing scheme in Sprint 17 W1 P2.
  - **No hardware-attestation policy check.** The B.2 MVP verifies
    the partner's signature on the envelope but does NOT verify
    that the partner's RTMR measurements (in the envelope's
    AttestationChain) match a hardware-policy registry on our side.
    The registry doesn't exist yet — that's Sprint 19+ work.
  - **Bundle must be SYNCED first.** The verifier consults the
    bridge's cache of the partner's last SUCCESS_NEW refresh; if
    the partner is registered but has no successful refresh in its
    history, verification returns PARTNER_NOT_SYNCED. Operators
    must call `bridge.refresh_partner()` before trying to verify
    envelopes from that partner.
  - **Signature is over the envelope's canonical JSON** (the same
    bytes that produce `envelope.envelope_sha256`). The partner is
    responsible for using the same canonical-JSON encoding; the
    Sprint 17 W1 P2 builder always produces canonical JSON via
    `AIMSEnvelope.to_json()`. If a partner uses a different
    encoder, the signature will fail to verify even if the data is
    semantically equivalent.

Refs:
  - docs/SPRINT_18_PLAN.md §1 Wave 3.A
  - docs/CLUSTER_B_FEDERATION_SPEC.md §3 (Wave B.2)
  - backend/services/identity_federation_bridge.py (B.1)
  - backend/services/aims_envelope.py (Sprint 17 W1 P2)
  - RFC 8037 — CFRG ECDH/EdDSA keys for JOSE (Ed25519 JWK encoding)
"""

from __future__ import annotations

import base64
import enum
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome enum + dataclass
# ---------------------------------------------------------------------------


class PartnerVerificationOutcomeKind(enum.IntEnum):
    VALID = 0
    PARTNER_UNKNOWN = 1  # trust_domain not registered with bridge
    PARTNER_NOT_SYNCED = 2  # registered but bundle never refreshed
    KID_NOT_FOUND = 3  # no JWK with matching kid in bundle
    UNSUPPORTED_KEY_TYPE = 4  # JWK kty/crv not Ed25519
    MALFORMED_JWK = 5  # JWK missing required fields
    SIGNATURE_INVALID = 6  # Ed25519 verify failed
    TRUST_DOMAIN_MISMATCH = 7  # envelope.agent_identity.trust_domain
    # != expected_trust_domain
    BASE_VERIFY_FAILED = 8  # the AIMSEnvelopeVerifier rejected
    # the envelope itself (version,
    # SHA, expiry, presence)


@dataclass(frozen=True)
class PartnerVerificationOutcome:
    kind: PartnerVerificationOutcomeKind
    trust_domain: Optional[str]
    aims_id: Optional[str]
    reason: str
    base_outcome: Optional[Any] = None  # AIMSVerificationOutcome (avoided
    # as a hard type to keep this
    # module decoupled)


# ---------------------------------------------------------------------------
# Protocols — kept structural so this module doesn't hard-import the
# F4 bridge or the AIMS envelope module (lets tests stub freely).
# ---------------------------------------------------------------------------


class _BridgeProtocol(Protocol):
    def get_partner_bundle_keys(
        self, trust_domain: str
    ) -> tuple[dict[str, Any], ...]: ...
    def list_partners(self) -> tuple[Any, ...]: ...


class _BaseVerifierProtocol(Protocol):
    def verify(self, envelope: Any) -> Any: ...


# ---------------------------------------------------------------------------
# JWK Ed25519 pubkey extraction
# ---------------------------------------------------------------------------


class _JWKDecodeError(Exception):
    pass


def _b64url_decode(s: str) -> bytes:
    """RFC 7515 base64url decode (no padding)."""
    if not isinstance(s, str):
        raise _JWKDecodeError(f"base64url value must be str; got {type(s).__name__}")
    padded = s + "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise _JWKDecodeError(f"base64url decode failed: {exc}") from exc


def _extract_ed25519_pubkey(jwk: dict[str, Any]) -> bytes:
    """Return the raw 32-byte Ed25519 public key from an RFC 8037 JWK."""
    kty = jwk.get("kty")
    if kty != "OKP":
        raise _JWKDecodeError(f"JWK kty must be 'OKP' for Ed25519; got {kty!r}")
    crv = jwk.get("crv")
    if crv != "Ed25519":
        raise _JWKDecodeError(f"JWK crv must be 'Ed25519'; got {crv!r}")
    x = jwk.get("x")
    if not x:
        raise _JWKDecodeError("JWK missing 'x' field (public key)")
    raw = _b64url_decode(x)
    if len(raw) != 32:
        raise _JWKDecodeError(
            f"Ed25519 public key must be exactly 32 bytes; " f"got {len(raw)} bytes"
        )
    return raw


# ---------------------------------------------------------------------------
# Ed25519 verify — uses `cryptography` (already a project dep)
# ---------------------------------------------------------------------------


def _ed25519_verify(pubkey_bytes: bytes, signature: bytes, message: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise RuntimeError(
            f"cryptography library is required for B.2 verification: {exc}"
        ) from exc
    try:
        key = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
    except Exception as exc:
        raise _JWKDecodeError(f"Ed25519 pubkey load failed: {exc}") from exc
    try:
        key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class AIMSPartnerVerifier:
    """Cross-domain AIMS envelope verifier — couples B.1's bridge to
    the existing AIMS self-consistency checks plus an Ed25519
    signature check using the partner's published JWK."""

    def __init__(
        self,
        *,
        bridge: _BridgeProtocol,
        base_verifier: Optional[_BaseVerifierProtocol] = None,
    ) -> None:
        if bridge is None:
            raise ValueError("bridge is required")
        self._bridge = bridge
        self._base = base_verifier
        if self._base is None:
            # Lazy-load to keep the module importable in environments
            # where backend.services.aims_envelope isn't available (e.g.
            # the airgap harness). Tests can inject a stub via
            # base_verifier=.
            try:
                from backend.services.aims_envelope import (
                    AIMSEnvelopeVerifier,
                )

                self._base = AIMSEnvelopeVerifier()
            except ImportError:
                try:
                    from services.aims_envelope import AIMSEnvelopeVerifier

                    self._base = AIMSEnvelopeVerifier()
                except ImportError as exc:
                    raise RuntimeError(
                        f"AIMSEnvelopeVerifier unavailable; pass "
                        f"base_verifier= explicitly: {exc}"
                    ) from exc

    def verify(
        self,
        *,
        envelope: Any,
        signature_bytes: bytes,
        signing_kid: str,
        expected_trust_domain: str,
    ) -> PartnerVerificationOutcome:
        """Verify an AIMS envelope signed by a partner trust domain.

        envelope                — an AIMSEnvelope (duck-typed: must
                                   expose .agent_identity.trust_domain,
                                   .aims_id, .to_json() returning the
                                   canonical-JSON string)
        signature_bytes         — Ed25519 signature over the canonical
                                   JSON (the same bytes that produce
                                   envelope.envelope_sha256)
        signing_kid             — the JWK 'kid' the partner used to
                                   sign; must match a key in the
                                   partner's most-recently synced bundle
        expected_trust_domain   — the trust_domain the CALLER expects
                                   the envelope to have come from;
                                   prevents bundle confusion (we look
                                   up THIS trust_domain's bundle, then
                                   cross-check the envelope's claimed
                                   trust_domain against it)
        """
        if envelope is None:
            raise ValueError("envelope is required")
        if not isinstance(signature_bytes, (bytes, bytearray)):
            raise TypeError(
                f"signature_bytes must be bytes; got "
                f"{type(signature_bytes).__name__}"
            )
        if not signing_kid or not isinstance(signing_kid, str):
            raise ValueError("signing_kid must be a non-empty string")
        if not expected_trust_domain or not isinstance(expected_trust_domain, str):
            raise ValueError("expected_trust_domain must be a non-empty string")

        aims_id = getattr(envelope, "aims_id", None)
        envelope_td = getattr(
            getattr(envelope, "agent_identity", None),
            "trust_domain",
            None,
        )

        # Step 1 — cross-check envelope's claimed trust_domain against
        # the caller's expectation. Catches bundle confusion where
        # caller looks up bundle X but the envelope is actually from Y.
        if envelope_td and envelope_td != expected_trust_domain:
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.TRUST_DOMAIN_MISMATCH,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"envelope.agent_identity.trust_domain="
                    f"{envelope_td!r} != expected_trust_domain="
                    f"{expected_trust_domain!r}"
                ),
            )

        # Step 2 — look up the partner's bundle keys via the bridge.
        try:
            keys = self._bridge.get_partner_bundle_keys(expected_trust_domain)
        except KeyError:
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.PARTNER_UNKNOWN,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"trust_domain {expected_trust_domain!r} not "
                    f"registered with bridge — call register_partner() "
                    f"first"
                ),
            )
        if not keys:
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.PARTNER_NOT_SYNCED,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"trust_domain {expected_trust_domain!r} registered "
                    f"but no successful bundle refresh — call "
                    f"bridge.refresh_partner() first"
                ),
            )

        # Step 3 — find the JWK with matching kid.
        matched = next((k for k in keys if k.get("kid") == signing_kid), None)
        if matched is None:
            available = [k.get("kid") for k in keys]
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.KID_NOT_FOUND,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"no JWK with kid={signing_kid!r} in partner "
                    f"bundle; available kids: {available}"
                ),
            )

        # Step 4 — decode Ed25519 pubkey.
        try:
            pubkey_bytes = _extract_ed25519_pubkey(matched)
        except _JWKDecodeError as exc:
            # Distinguish unsupported-key-type from malformed.
            kty = matched.get("kty")
            crv = matched.get("crv")
            if kty != "OKP" or crv != "Ed25519":
                kind = PartnerVerificationOutcomeKind.UNSUPPORTED_KEY_TYPE
            else:
                kind = PartnerVerificationOutcomeKind.MALFORMED_JWK
            return PartnerVerificationOutcome(
                kind=kind,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=str(exc),
            )

        # Step 5 — verify signature over canonical JSON.
        try:
            canonical = envelope.to_json().encode("utf-8")
        except AttributeError:
            raise TypeError(
                "envelope must expose to_json() returning canonical JSON; "
                "is this really an AIMSEnvelope?"
            )
        if not _ed25519_verify(pubkey_bytes, bytes(signature_bytes), canonical):
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.SIGNATURE_INVALID,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"Ed25519 verify failed for envelope sig under "
                    f"kid={signing_kid!r}"
                ),
            )

        # Step 6 — base self-consistency checks.
        base_outcome = self._base.verify(envelope)
        base_kind_name = getattr(
            getattr(base_outcome, "kind", None), "name", str(base_outcome)
        )
        if base_kind_name != "VALID":
            return PartnerVerificationOutcome(
                kind=PartnerVerificationOutcomeKind.BASE_VERIFY_FAILED,
                trust_domain=expected_trust_domain,
                aims_id=aims_id,
                reason=(
                    f"base AIMS verify rejected envelope: "
                    f"{base_kind_name} — "
                    f"{getattr(base_outcome, 'reason', '?')}"
                ),
                base_outcome=base_outcome,
            )

        return PartnerVerificationOutcome(
            kind=PartnerVerificationOutcomeKind.VALID,
            trust_domain=expected_trust_domain,
            aims_id=aims_id,
            reason="cross-domain AIMS envelope verified",
            base_outcome=base_outcome,
        )


__all__ = [
    "AIMSPartnerVerifier",
    "PartnerVerificationOutcome",
    "PartnerVerificationOutcomeKind",
]
