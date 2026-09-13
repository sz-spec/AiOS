"""
backend/core/security/spiffe_federation.py
============================================

Sprint 16 / Item F4 — SPIFFE Federation accept layer.

What this is
------------

From the 80-problem agent-era catalog, F4:
  "Cross-tenant agent identity (sub-contractor flows) — agent moves
   between orgs (consulting workflow); no federation standard."

SPIFFE Federation (https://spiffe.io/docs/latest/spiffe-specs/spiffe_federation/)
solves cross-trust-domain identity exchange: each domain publishes its
own trust bundle (JWKS), and a verifier in domain A can accept SPIFFE
SVIDs issued in domain B by importing B's bundle.

Sprint 15 / F1 (backend/core/security/spiffe_workload_identity.py)
ships a SINGLE-trust-domain verifier. F4 adds the federation layer
that:

  1. Holds the LOCAL trust domain's verifier (the same SPIFFEWITVerifier
     from F1).
  2. Holds a registered set of REMOTE trust-domain verifiers (one per
     federated partner).
  3. Inspects the incoming token's `iss` claim (the SPIFFE issuer URI),
     extracts the trust-domain prefix, and routes verification to the
     matching verifier.
  4. Returns a VerifiedFederatedIdentity that carries which domain the
     SVID was actually verified in (so the caller can apply domain-
     specific authorization policy).

Public surface
--------------

  SPIFFEFederationVerifier
    .__init__(local_verifier)
    .register_federated_domain(trust_domain, verifier)
    .unregister_federated_domain(trust_domain)
    .verify(jwt_token) -> VerifiedFederatedIdentity
    .list_federated_domains() -> list[str]
    .stats() -> FederationStats

Honest scope ceiling
--------------------

  - This module ROUTES verification to the matching domain verifier;
    each verifier still does the full SPIFFE JWT verification (signature,
    expiry, audience, SVID-format) via the existing F1 code path.
  - "Trust domain" is whatever the registered verifier reports as its
    domain — we trust the operator to register only verifiers whose
    bundle is genuinely from the named domain. Cross-domain bundle
    impersonation (claiming to be trust_domain=X while serving a bundle
    from Y) is detected at registration time when the verifier exposes
    its declared domain.
  - Bundle refresh (rotating partner keys) is the operator's job — call
    register_federated_domain again with a fresh verifier instance.
    No background fetch in C7 (avoids a moving-target attack surface);
    a later F5+F4 integration ties bundle refresh to Vault JIT.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Exceptions + dataclasses
# ---------------------------------------------------------------------------


class FederationError(Exception):
    pass


class FederationUnknownDomainError(FederationError):
    """Incoming token claims a trust domain not in our federated set."""

    pass


class FederationMalformedTokenError(FederationError):
    """Token doesn't have a parseable issuer/SPIFFE structure."""

    pass


@dataclass(frozen=True)
class VerifiedFederatedIdentity:
    """Carries the verified SVID + the trust domain it came from."""

    spiffe_id: str
    trust_domain: str
    is_local: bool
    raw_verified: Any  # the F1 VerifiedSPIFFEIdentity, opaque to F4


@dataclass
class FederationStats:
    total_verifications: int = 0
    local_verifications: int = 0
    federated_verifications: int = 0
    unknown_domain_blocked: int = 0
    malformed_token_blocked: int = 0


# ---------------------------------------------------------------------------
# Issuer-prefix parsing
# ---------------------------------------------------------------------------


# Matches `iss` claim shapes the SPIFFE spec accepts:
#   spiffe://<trust_domain>/<path>            — canonical SVID issuer
#   https://<trust_domain>/<path>             — OIDC-bridged spelling
# We only need the trust_domain group.
_ISSUER_RE = re.compile(
    r"^(?:spiffe|https?)://(?P<trust_domain>[a-z0-9][a-z0-9.\-]*[a-z0-9])(?:/|$)",
    re.IGNORECASE,
)


def extract_trust_domain_from_issuer(issuer: str) -> str:
    """Pull the trust_domain out of an `iss` claim string.

    Raises FederationMalformedTokenError if the URI doesn't match.
    """
    if not issuer or not isinstance(issuer, str):
        raise FederationMalformedTokenError("issuer must be a non-empty string")
    m = _ISSUER_RE.match(issuer)
    if not m:
        raise FederationMalformedTokenError(
            f"issuer {issuer!r} doesn't look like a SPIFFE/OIDC issuer URI"
        )
    return m.group("trust_domain").lower()


# ---------------------------------------------------------------------------
# JWT decoding (header + payload extraction without verification)
#
# We need to peek at the issuer BEFORE routing to a verifier. Mirrors the
# helper in spiffe_workload_identity.py but kept private here so F4 has
# no import-cycle with F1.
# ---------------------------------------------------------------------------


def _peek_issuer_from_jwt(token: str) -> str:
    import base64
    import json

    if not isinstance(token, str) or token.count(".") != 2:
        raise FederationMalformedTokenError(
            "token doesn't have the 3-segment JWT shape"
        )
    _hdr, payload_b64, _sig = token.split(".")
    # Pad to multiple of 4 for urlsafe_b64decode.
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise FederationMalformedTokenError(f"payload decode failed: {exc}")
    if not isinstance(payload, dict):
        raise FederationMalformedTokenError("payload is not a JSON object")
    iss = payload.get("iss")
    if not isinstance(iss, str):
        raise FederationMalformedTokenError("payload missing string 'iss' claim")
    return iss


# ---------------------------------------------------------------------------
# Verifier protocol — F1's SPIFFEWITVerifier exposes a verify() method.
# We type the federation layer against a structural protocol to avoid
# import cycles with the F1 module + to keep tests injection-friendly.
# ---------------------------------------------------------------------------


class _VerifierProtocol:
    """Structural type the federation layer expects.

    F1's SPIFFEWITVerifier implements this naturally (.verify returns
    VerifiedSPIFFEIdentity with a .trust_domain + .spiffe_id field).
    """

    trust_domain: str

    def verify(self, jwt_token: str) -> Any: ...


# ---------------------------------------------------------------------------
# SPIFFEFederationVerifier
# ---------------------------------------------------------------------------


class SPIFFEFederationVerifier:
    """Routes incoming SPIFFE JWTs to the matching trust-domain verifier.

    Construction:
        local = SPIFFEWITVerifier.from_trust_bundle(local_bundle_path,
                                                    expected_audience="vos3-platform")
        fed = SPIFFEFederationVerifier(local_verifier=local)
        partner = SPIFFEWITVerifier.from_trust_bundle(partner_bundle_path,
                                                       expected_audience="vos3-platform")
        fed.register_federated_domain("partner.example.com", partner)

    Verification:
        identity = fed.verify(incoming_jwt)
        # identity.spiffe_id, identity.trust_domain, identity.is_local
    """

    def __init__(self, local_verifier: _VerifierProtocol):
        if local_verifier is None:
            raise ValueError("local_verifier is required")
        if not getattr(local_verifier, "trust_domain", None):
            raise ValueError("local_verifier must expose a .trust_domain attribute")
        self._local_verifier = local_verifier
        self._local_domain = local_verifier.trust_domain.lower()
        self._federated: dict[str, _VerifierProtocol] = {}
        self._lock = threading.Lock()
        self._stats = FederationStats()

    # -- Registry mutations --------------------------------------------------

    def register_federated_domain(
        self, trust_domain: str, verifier: _VerifierProtocol
    ) -> None:
        if not trust_domain or not isinstance(trust_domain, str):
            raise ValueError("trust_domain must be non-empty string")
        if verifier is None:
            raise ValueError("verifier is required")
        declared = getattr(verifier, "trust_domain", None)
        if not declared:
            raise ValueError("verifier must expose its .trust_domain for safety check")
        if declared.lower() != trust_domain.lower():
            raise ValueError(
                f"trust_domain mismatch: arg={trust_domain!r} vs "
                f"verifier.trust_domain={declared!r}"
            )
        if trust_domain.lower() == self._local_domain:
            raise ValueError(
                f"cannot register local domain {trust_domain!r} as federated"
            )
        with self._lock:
            self._federated[trust_domain.lower()] = verifier

    def unregister_federated_domain(self, trust_domain: str) -> None:
        if not trust_domain or not isinstance(trust_domain, str):
            raise ValueError("trust_domain must be non-empty string")
        with self._lock:
            self._federated.pop(trust_domain.lower(), None)

    def list_federated_domains(self) -> list[str]:
        with self._lock:
            return sorted(self._federated.keys())

    # -- Verification --------------------------------------------------------

    def verify(self, jwt_token: str) -> VerifiedFederatedIdentity:
        if not isinstance(jwt_token, str) or not jwt_token:
            with self._lock:
                self._stats.malformed_token_blocked += 1
                self._stats.total_verifications += 1
            raise FederationMalformedTokenError("jwt_token must be non-empty string")

        try:
            issuer = _peek_issuer_from_jwt(jwt_token)
            trust_domain = extract_trust_domain_from_issuer(issuer)
        except FederationMalformedTokenError:
            with self._lock:
                self._stats.malformed_token_blocked += 1
                self._stats.total_verifications += 1
            raise

        is_local = trust_domain == self._local_domain
        with self._lock:
            self._stats.total_verifications += 1
            if is_local:
                verifier = self._local_verifier
                self._stats.local_verifications += 1
            else:
                verifier = self._federated.get(trust_domain)
                if verifier is None:
                    self._stats.unknown_domain_blocked += 1
                    raise FederationUnknownDomainError(
                        f"trust_domain {trust_domain!r} not in federated set; "
                        f"known: {sorted(list(self._federated.keys()))!r}"
                    )
                self._stats.federated_verifications += 1

        # The underlying F1 verifier raises on bad signature / expiry /
        # audience / SVID format — propagate without wrapping so callers
        # can catch the specific F1 exception types.
        verified = verifier.verify(jwt_token)
        verified_spiffe = (
            getattr(verified, "spiffe_id_str", None)
            or getattr(verified, "spiffe_id", None)
            or ""
        )
        # If the F1 layer returned a SPIFFEId dataclass, stringify it.
        if hasattr(verified_spiffe, "full_uri"):
            verified_spiffe = verified_spiffe.full_uri
        return VerifiedFederatedIdentity(
            spiffe_id=str(verified_spiffe),
            trust_domain=trust_domain,
            is_local=is_local,
            raw_verified=verified,
        )

    # -- Stats ---------------------------------------------------------------

    def stats(self) -> FederationStats:
        with self._lock:
            return FederationStats(
                total_verifications=self._stats.total_verifications,
                local_verifications=self._stats.local_verifications,
                federated_verifications=self._stats.federated_verifications,
                unknown_domain_blocked=self._stats.unknown_domain_blocked,
                malformed_token_blocked=self._stats.malformed_token_blocked,
            )


__all__ = [
    "FederationError",
    "FederationUnknownDomainError",
    "FederationMalformedTokenError",
    "VerifiedFederatedIdentity",
    "FederationStats",
    "SPIFFEFederationVerifier",
    "extract_trust_domain_from_issuer",
]
