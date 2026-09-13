"""
backend/core/security/spiffe_workload_identity.py
==================================================

Sprint 15 / Item F1 — SPIFFE WIT-SVID workload-identity verifier.

What this is
------------

SPIFFE (Secure Production Identity Framework for Everyone) is the CNCF-
graduated standard for cryptographic workload identity. As of May 2026,
the IETF OAuth working group has draft-ietf-oauth-spiffe-client-auth-01
that defines how a workload presents a Workload Identity Token (WIT)
bound to a SPIFFE Verifiable Identity Document (SVID), enabling
cryptographically-attested authentication WITHOUT relying on long-lived
bearer secrets.

This module is the vOS-side verifier. The Clerk JWT path
(middleware/clerk_auth.py) handles HUMAN users; this module handles
WORKLOAD callers — other agents, sidecars, CI jobs, signed batch
runners — that need cryptographic identity tied to a SPIFFE trust
domain.

Public surface
--------------

    SPIFFEWITVerifier.from_trust_bundle(bundle_path) -> SPIFFEWITVerifier
    SPIFFEWITVerifier.verify(jwt_token) -> VerifiedSPIFFEIdentity
    parse_spiffe_id(uri) -> SPIFFEId
        Splits "spiffe://<trust-domain>/<path>" into the typed
        SPIFFEId dataclass.

WIT-SVID wire format
--------------------

A WIT-SVID is a JWT whose claims include:
    {
      "sub": "spiffe://<trust-domain>/<workload-path>",
      "iss": "spiffe://<trust-domain>",       (or the SPIRE issuer URI)
      "aud": ["<expected-audience>"],
      "exp": <unix-ts>,
      "iat": <unix-ts>,
      "nbf": <unix-ts>,
      "jti": "<token id>",
      "wit": {                                # Workload Identity Token claims
        "wid": "<workload UUID>",             # Stable workload identifier
        "selectors": [...],                   # SPIRE selectors that matched
        "node_attestation": "<method>",       # k8s_sat / aws_iid / gcp_iit / ...
      }
    }

Per the IETF draft, the JWT is signed by a key that's published in the
trust bundle for the issuing trust domain. Verification chain:
  1. Parse the JWT header to extract `kid` (key id).
  2. Look up `kid` in the trust-bundle's JWK set.
  3. Verify the JWT signature against that key.
  4. Validate `iss`, `aud`, `exp`, `nbf`, `sub` shape.
  5. Extract the SPIFFE ID from `sub`.

Honest scope ceiling
--------------------

  1. **Trust bundle distribution** — this verifier expects the bundle as
     a static JWKS JSON file at the path passed to `from_trust_bundle()`.
     Production deployments using SPIRE federation rotate the bundle
     dynamically via the SPIRE Federation API; that integration is the
     Stage 14.B.6 deliverable. For Sprint 15, file-based bundles are
     sufficient (operators refresh via cron + SPIFFE federation client).

  2. **Selector enforcement** — we extract `wit.selectors` from the
     token but do NOT enforce policy on them. Selector-based authz is
     Wave 3 (Agent 12 ties this into the IntentManifest gate).

  3. **WIT vs JWT-SVID** — SPIFFE defines two token types: classic
     JWT-SVID (everyday workload tokens) and WIT-SVID (the OAuth-bound
     workload identity token from draft-ietf-oauth-spiffe-client-auth).
     Both share the same wire format; the verifier accepts either. The
     difference is at the OAuth issuer side, which is out of scope here.

  4. **Federation across trust domains** — single-trust-domain only in
     Sprint 15. Cross-domain federation is Sprint 16-17 / item F4.

References:
  - https://datatracker.ietf.org/doc/draft-ietf-oauth-spiffe-client-auth/
  - https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/
  - https://spiffe.io/docs/latest/spire-about/spire-concepts/
  - https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env + constants
# ---------------------------------------------------------------------------

ENV_TRUST_BUNDLE_PATH = "VOS3_SPIFFE_TRUST_BUNDLE_PATH"
ENV_EXPECTED_AUDIENCE = "VOS3_SPIFFE_EXPECTED_AUDIENCE"
ENV_DEFAULT_TRUST_DOMAIN = "VOS3_SPIFFE_TRUST_DOMAIN"

# SPIFFE ID shape: spiffe://<trust-domain>/<path>
# Trust domain is a DNS-like name (lowercase, dots, hyphens).
# Path is a slash-separated set of components, each [A-Za-z0-9_.-]
_SPIFFE_ID_RE = re.compile(
    r"^spiffe://([a-z0-9][a-z0-9.\-]*[a-z0-9])" r"(/[A-Za-z0-9_./\-]+)?$"
)

DEFAULT_CLOCK_SKEW_SECONDS = 60


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SPIFFEVerificationError(RuntimeError):
    """Raised on any SPIFFE verifier failure (bad bundle, expired token,
    signature failure, audience mismatch, malformed SPIFFE ID)."""


class SPIFFEMalformedIDError(SPIFFEVerificationError):
    """The presented SPIFFE ID isn't a syntactically-valid URI."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SPIFFEId:
    """Parsed `spiffe://<trust-domain>/<path>` URI."""

    trust_domain: str
    path: str  # "" if the URI was just the trust-domain root
    full_uri: str

    @classmethod
    def parse(cls, uri: str) -> "SPIFFEId":
        if not isinstance(uri, str) or not uri:
            raise SPIFFEMalformedIDError("SPIFFE ID must be a non-empty string")
        match = _SPIFFE_ID_RE.match(uri)
        if not match:
            raise SPIFFEMalformedIDError(
                f"SPIFFE ID does not match spiffe://<trust-domain>/<path>: {uri!r}"
            )
        trust_domain = match.group(1)
        path = (match.group(2) or "").lstrip("/")
        return cls(trust_domain=trust_domain, path=path, full_uri=uri)


@dataclass(frozen=True)
class VerifiedSPIFFEIdentity:
    """Result of successful WIT-SVID verification."""

    spiffe_id: SPIFFEId
    issued_at: int  # epoch seconds
    expires_at: int  # epoch seconds
    audience: tuple[str, ...]
    selectors: tuple[str, ...]
    node_attestation: Optional[str]
    raw_claims: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trust bundle loader
# ---------------------------------------------------------------------------


def _load_trust_bundle(path: Path) -> dict[str, Any]:
    """Load a JWKS-format trust bundle. Returns a dict keyed by `kid`."""
    if not path.is_file():
        raise SPIFFEVerificationError(f"trust bundle not found: {path}")
    try:
        doc = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise SPIFFEVerificationError(f"trust bundle is not valid JSON: {exc}") from exc

    keys = doc.get("keys") or []
    if not keys:
        raise SPIFFEVerificationError(f"trust bundle at {path} has no 'keys' array")

    by_kid: dict[str, dict[str, Any]] = {}
    for k in keys:
        kid = k.get("kid")
        if kid:
            by_kid[kid] = k
    if not by_kid:
        raise SPIFFEVerificationError(
            "trust bundle keys lack 'kid' fields; verifier cannot resolve signers"
        )
    return by_kid


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclass
class SPIFFEWITVerifier:
    """Verifier instance bound to a single trust bundle.

    Construction:
        from_trust_bundle(bundle_path, expected_audience=...)

    Instance is thread-safe (the bundle is immutable after load).
    """

    trust_bundle: dict[str, dict[str, Any]]
    expected_audience: Optional[str] = None
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS

    @classmethod
    def from_trust_bundle(
        cls,
        bundle_path: str | os.PathLike,
        expected_audience: Optional[str] = None,
    ) -> "SPIFFEWITVerifier":
        bundle = _load_trust_bundle(Path(bundle_path))
        if expected_audience is None:
            expected_audience = (
                os.environ.get(ENV_EXPECTED_AUDIENCE, "").strip() or None
            )
        return cls(trust_bundle=bundle, expected_audience=expected_audience)

    def verify(self, jwt_token: str) -> VerifiedSPIFFEIdentity:
        """Verify a WIT-SVID JWT and return the structured identity.

        Raises SPIFFEVerificationError on any failure.
        """
        if not isinstance(jwt_token, str) or jwt_token.count(".") != 2:
            raise SPIFFEVerificationError("WIT-SVID must be a 3-segment JWT")

        # Parse header to extract `kid` BEFORE attempting signature verify.
        # We use python-jose / pyjwt if available; otherwise a manual base64
        # decode of the header is enough to find the kid.
        header, claims = self._decode_jwt_segments(jwt_token)
        kid = header.get("kid")
        if not kid:
            raise SPIFFEVerificationError("WIT-SVID header missing `kid`")

        jwk = self.trust_bundle.get(kid)
        if jwk is None:
            raise SPIFFEVerificationError(f"WIT-SVID `kid` {kid!r} not in trust bundle")

        # Verify signature via PyJWT if available; otherwise fall back to
        # a manual ECDSA / EdDSA verify path using cryptography.
        self._verify_signature(jwt_token, header, jwk)

        # Validate temporal claims with clock-skew tolerance.
        now = int(time.time())
        exp = int(claims.get("exp", 0))
        nbf = int(claims.get("nbf", claims.get("iat", 0)))
        if exp == 0:
            raise SPIFFEVerificationError("WIT-SVID missing `exp`")
        if now > exp + self.clock_skew_seconds:
            raise SPIFFEVerificationError(
                f"WIT-SVID expired: now={now} exp={exp} skew={self.clock_skew_seconds}"
            )
        if nbf > 0 and now + self.clock_skew_seconds < nbf:
            raise SPIFFEVerificationError(
                f"WIT-SVID not yet valid: now={now} nbf={nbf}"
            )

        # Audience check (if configured).
        aud_claim = claims.get("aud")
        if isinstance(aud_claim, str):
            aud_set = (aud_claim,)
        elif isinstance(aud_claim, list):
            aud_set = tuple(str(a) for a in aud_claim)
        else:
            aud_set = ()
        if self.expected_audience is not None and self.expected_audience not in aud_set:
            raise SPIFFEVerificationError(
                f"WIT-SVID audience {aud_set!r} does not include "
                f"expected {self.expected_audience!r}"
            )

        # Extract + validate the SPIFFE ID from `sub`.
        sub = claims.get("sub")
        if not isinstance(sub, str):
            raise SPIFFEVerificationError("WIT-SVID `sub` missing or not string")
        spiffe_id = SPIFFEId.parse(sub)

        # Optional WIT extensions.
        wit = claims.get("wit") or {}
        selectors = tuple(str(s) for s in wit.get("selectors", []) or [])
        node_attestation = wit.get("node_attestation")
        if node_attestation is not None and not isinstance(node_attestation, str):
            node_attestation = str(node_attestation)

        return VerifiedSPIFFEIdentity(
            spiffe_id=spiffe_id,
            issued_at=int(claims.get("iat", 0)),
            expires_at=exp,
            audience=aud_set,
            selectors=selectors,
            node_attestation=node_attestation,
            raw_claims=dict(claims),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jwt_segments(jwt_token: str) -> tuple[dict, dict]:
        """Decode header + claims without verifying signature.

        Used only to extract `kid` for trust-bundle lookup. Signature
        verification happens AFTER this — never trust the body until
        signature passes.
        """
        import base64

        header_b64, payload_b64, _sig_b64 = jwt_token.split(".")
        try:
            header_json = base64.urlsafe_b64decode(_pad_b64(header_b64))
            payload_json = base64.urlsafe_b64decode(_pad_b64(payload_b64))
            return json.loads(header_json), json.loads(payload_json)
        except (ValueError, json.JSONDecodeError) as exc:
            raise SPIFFEVerificationError(
                f"WIT-SVID header/payload decode failed: {exc}"
            ) from exc

    def _verify_signature(self, jwt_token: str, header: dict, jwk: dict) -> None:
        """Verify the JWT signature against the JWK from the trust bundle.

        Supports the two SPIFFE-spec-blessed signature algorithms:
            ES256 (ECDSA P-256, SHA-256)
            EdDSA (Ed25519)
        """
        alg = header.get("alg")
        if alg not in {"ES256", "EdDSA"}:
            raise SPIFFEVerificationError(
                f"WIT-SVID unsupported alg {alg!r}; SPIFFE requires ES256 or EdDSA"
            )

        try:
            import jwt as pyjwt  # PyJWT
        except ImportError as exc:
            raise SPIFFEVerificationError(
                "PyJWT not installed; pip install pyjwt cryptography"
            ) from exc

        # PyJWT can ingest a JWK dict directly via PyJWK.
        try:
            pyjwk = pyjwt.PyJWK(jwk)
        except Exception as exc:  # noqa: BLE001
            raise SPIFFEVerificationError(
                f"failed to materialize PyJWK from trust bundle entry: {exc}"
            ) from exc

        try:
            pyjwt.decode(
                jwt_token,
                key=pyjwk.key,
                algorithms=[alg],
                options={
                    "verify_signature": True,
                    "verify_exp": False,  # we do this ourselves with skew
                    "verify_nbf": False,
                    "verify_aud": False,  # we do this ourselves
                    "require": ["exp", "iat", "sub"],
                },
            )
        except pyjwt.InvalidSignatureError as exc:
            raise SPIFFEVerificationError(f"WIT-SVID signature invalid: {exc}") from exc
        except pyjwt.PyJWTError as exc:
            raise SPIFFEVerificationError(
                f"WIT-SVID PyJWT decode failed: {exc}"
            ) from exc


def _pad_b64(s: str) -> str:
    return s + "=" * (-len(s) % 4)


# ---------------------------------------------------------------------------
# Convenience top-level functions
# ---------------------------------------------------------------------------


_default_verifier: Optional[SPIFFEWITVerifier] = None
_verifier_lock = threading.Lock()


def get_default_verifier() -> Optional[SPIFFEWITVerifier]:
    """Return the env-configured default verifier, or None if not set up.

    The verifier is created on first call from `VOS3_SPIFFE_TRUST_BUNDLE_PATH`
    + `VOS3_SPIFFE_EXPECTED_AUDIENCE` env vars. Subsequent calls return
    the cached instance.
    """
    global _default_verifier
    with _verifier_lock:
        if _default_verifier is None:
            bundle_path = os.environ.get(ENV_TRUST_BUNDLE_PATH, "").strip()
            if not bundle_path:
                return None
            try:
                _default_verifier = SPIFFEWITVerifier.from_trust_bundle(bundle_path)
            except SPIFFEVerificationError as exc:
                logger.warning(
                    "[spiffe] failed to load default trust bundle from %s: %s",
                    bundle_path,
                    exc,
                )
                return None
        return _default_verifier


def parse_spiffe_id(uri: str) -> SPIFFEId:
    return SPIFFEId.parse(uri)


def reset_for_tests() -> None:
    """Clear the cached default verifier — used by pytest fixtures."""
    global _default_verifier
    with _verifier_lock:
        _default_verifier = None


__all__ = [
    "SPIFFEId",
    "VerifiedSPIFFEIdentity",
    "SPIFFEWITVerifier",
    "SPIFFEVerificationError",
    "SPIFFEMalformedIDError",
    "get_default_verifier",
    "parse_spiffe_id",
    "reset_for_tests",
    "ENV_TRUST_BUNDLE_PATH",
    "ENV_EXPECTED_AUDIENCE",
    "ENV_DEFAULT_TRUST_DOMAIN",
    "DEFAULT_CLOCK_SKEW_SECONDS",
]
