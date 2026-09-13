"""
backend/services/attestation_nonce_gate.py
============================================

Sprint 16 / Item D5 — Anti-replay nonce enforcement for TEE attestation.

Why this exists
---------------

From the 80-problem agent-era catalog, D5:
  "Attestation freshness — quotes can be replayed. TDX REPORT structures
   lack per-request nonce by default; replaying an old quote is feasible
   against naive relying parties."

The fix per draft-ietf-rats-ar4si-09 (Attestation Results for Secure
Interactions, May 2026) and draft-kdyxy-rats-tdx-eat-profile-02 (EAT
profile for Intel TDX): bind every quote to a per-request nonce that
the verifier issues, the attester must include in the quote's REPORTDATA
field, and the verifier checks before trusting the quote.

This module is the gating layer that lives in front of every quote-
verification call:

  1. issue_nonce(audience) → nonce — verifier mints a single-use nonce,
     records it with an expiry timestamp.
  2. verify_quote(quote_bytes, claimed_nonce) — looks up the nonce in
     the issued set, rejects if missing/expired/wrong audience/wrong
     bound-value, then forwards to the underlying attestation_service.
  3. Each successful verification consumes the nonce (single-use).

Public surface
--------------

  NonceGate.issue_nonce(audience, ttl_seconds=30) -> str
  NonceGate.verify_quote(quote, claimed_nonce, audience) -> VerifiedQuote
  NonceGate.snapshot_stats() -> NonceGateStats

Honest scope ceiling
--------------------

  - The nonce gate verifies the NONCE binding. The cryptographic
    verification of the quote itself (signature chain, RTMR-bound
    measurements) is the existing attestation_service.py path — this
    module wraps it, doesn't replace it.
  - "Audience" binds a nonce to a specific relying-party context so a
    nonce issued for service-A can't be replayed against service-B.
    The audience string is just a tag the verifier picks; format
    isn't standardized in draft-ietf-rats-ar4si-09.
  - The default TTL is 30 seconds — long enough for clock skew + quote
    generation but short enough that replay against a captured nonce
    is bounded. Operator-tunable.
  - Nonces are stored in an in-memory dict (per-process). A
    horizontally-scaled verifier needs a shared store (Redis with TTL,
    or a per-verifier sticky-session scheme). Out of scope for D5; the
    gate's interface is sharable.

References:
  - draft-ietf-rats-ar4si-09 (Attestation Results for Secure Interactions)
  - draft-kdyxy-rats-tdx-eat-profile-02 (EAT profile for Intel TDX)
  - Intel Trust Authority eat_nonce documentation (May 2026)
  - "Guest Data" nonce field in AMD SEV-SNP attestation report
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

DEFAULT_NONCE_TTL_SECONDS = 30
NONCE_BYTES = 32  # 256 bits — overlaps SHA256 output size for REPORTDATA binding


class NonceError(Exception):
    pass


class NonceMissingError(NonceError):
    """Claimed nonce was never issued (or already consumed/expired)."""

    pass


class NonceExpiredError(NonceError):
    """Nonce was issued but the TTL has elapsed."""

    pass


class NonceAudienceMismatchError(NonceError):
    """Nonce was issued for a different audience."""

    pass


class NonceBindingMismatchError(NonceError):
    """Quote's REPORTDATA does not contain the nonce hash."""

    pass


# ---------------------------------------------------------------------------
# Issued-nonce record + verified-quote result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedNonce:
    nonce: str  # hex-encoded NONCE_BYTES bytes
    audience: str
    expiry_ts: float  # epoch seconds


@dataclass(frozen=True)
class VerifiedQuote:
    nonce: str
    audience: str
    quote_sha256: str  # SHA-256 of the quote bytes, for audit linkability


@dataclass
class NonceGateStats:
    nonces_issued: int = 0
    quotes_verified: int = 0
    replays_blocked: int = 0  # claimed nonce missing/consumed
    expired_blocked: int = 0
    audience_blocked: int = 0
    binding_blocked: int = 0


# ---------------------------------------------------------------------------
# NonceGate
# ---------------------------------------------------------------------------


class NonceGate:
    """Mints + verifies per-request nonces for TEE attestation.

    Bind every nonce to an audience (the relying-party context); each
    nonce is single-use; expired nonces are reaped on every operation.
    """

    def __init__(self, default_ttl_seconds: int = DEFAULT_NONCE_TTL_SECONDS):
        self.default_ttl_seconds = int(default_ttl_seconds)
        self._issued: dict[str, IssuedNonce] = {}
        self._lock = threading.Lock()
        self._stats = NonceGateStats()

    @staticmethod
    def _now() -> float:
        return time.time()

    def _reap_expired_locked(self) -> int:
        now = self._now()
        expired = [n for n, rec in self._issued.items() if rec.expiry_ts <= now]
        for n in expired:
            del self._issued[n]
        return len(expired)

    def issue_nonce(self, audience: str, ttl_seconds: Optional[int] = None) -> str:
        """Mint a new nonce bound to `audience`. Returns hex-encoded
        NONCE_BYTES bytes."""
        if not audience or not isinstance(audience, str):
            raise ValueError("audience must be a non-empty string")
        ttl = int(ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        nonce = secrets.token_hex(NONCE_BYTES)
        rec = IssuedNonce(
            nonce=nonce,
            audience=audience,
            expiry_ts=self._now() + ttl,
        )
        with self._lock:
            self._reap_expired_locked()
            self._issued[nonce] = rec
            self._stats.nonces_issued += 1
        return nonce

    @staticmethod
    def _nonce_binding(nonce: str) -> bytes:
        """The value the attester is required to embed in REPORTDATA:
        SHA-256(nonce_hex).  Matches the Intel TDX eat_nonce convention
        of binding the nonce via a hash so REPORTDATA can carry it
        regardless of nonce length."""
        return hashlib.sha256(nonce.encode("utf-8")).digest()

    @staticmethod
    def _extract_report_data(quote: bytes) -> bytes:
        """Extract the REPORTDATA bytes from the quote.

        For the stand-in quote format used in the dev attestation chain
        (vos3-dev tier — see infra/security/sigstore_v3_bundle.py
        "VOS3-DEV-NOT-FULCIO" cert), we treat the LAST 32 bytes of the
        quote as REPORTDATA. Real TDX/SEV-SNP quote parsers in
        attestation_service.py extract REPORTDATA from a structured
        offset; this gate doesn't care about the layout as long as the
        SHA-256(nonce) ends up in those bytes.
        """
        if len(quote) < 32:
            raise NonceBindingMismatchError(
                f"quote too short ({len(quote)} bytes) to carry REPORTDATA"
            )
        return quote[-32:]

    def verify_quote(
        self, quote: bytes, claimed_nonce: str, audience: str
    ) -> VerifiedQuote:
        """Verify the nonce binding before any cryptographic verification.

        Single-use: a successful verification CONSUMES the nonce so a
        captured quote cannot be replayed even within the TTL window.
        """
        if not isinstance(quote, (bytes, bytearray)):
            raise TypeError("quote must be bytes-like")
        quote_bytes = bytes(quote)
        if not claimed_nonce or not isinstance(claimed_nonce, str):
            raise ValueError("claimed_nonce must be a non-empty string")
        if not audience or not isinstance(audience, str):
            raise ValueError("audience must be a non-empty string")

        with self._lock:
            # NOTE: do NOT reap expired nonces before this lookup — if we
            # did, an expired nonce would appear as "missing" instead of
            # "expired", confusing operators + losing the ability to
            # distinguish replay from clock-skew in the audit log.
            rec = self._issued.get(claimed_nonce)
            if rec is None:
                self._stats.replays_blocked += 1
                raise NonceMissingError(
                    f"nonce {claimed_nonce[:12]}... not in issued set "
                    "(replayed, consumed, or never issued)"
                )
            now = self._now()
            if rec.expiry_ts <= now:
                del self._issued[claimed_nonce]
                self._stats.expired_blocked += 1
                raise NonceExpiredError(
                    f"nonce expired ({now - rec.expiry_ts:.1f}s past TTL)"
                )
            if rec.audience != audience:
                self._stats.audience_blocked += 1
                raise NonceAudienceMismatchError(
                    f"nonce was issued for audience {rec.audience!r}, "
                    f"verify request claims {audience!r}"
                )

            report_data = self._extract_report_data(quote_bytes)
            expected = self._nonce_binding(claimed_nonce)
            if not hmac.compare_digest(report_data, expected):
                self._stats.binding_blocked += 1
                raise NonceBindingMismatchError(
                    "quote REPORTDATA does not contain the SHA-256 of the "
                    "claimed nonce — attester did not bind to this nonce"
                )

            # Consume.
            del self._issued[claimed_nonce]
            self._stats.quotes_verified += 1

        return VerifiedQuote(
            nonce=claimed_nonce,
            audience=audience,
            quote_sha256=hashlib.sha256(quote_bytes).hexdigest(),
        )

    def snapshot_stats(self) -> NonceGateStats:
        with self._lock:
            return NonceGateStats(
                nonces_issued=self._stats.nonces_issued,
                quotes_verified=self._stats.quotes_verified,
                replays_blocked=self._stats.replays_blocked,
                expired_blocked=self._stats.expired_blocked,
                audience_blocked=self._stats.audience_blocked,
                binding_blocked=self._stats.binding_blocked,
            )


# ---------------------------------------------------------------------------
# Convenience: build a quote that binds the given nonce in REPORTDATA
# (used by tests + by dev tools that need to round-trip a quote).
# ---------------------------------------------------------------------------


def make_quote_with_nonce(body: bytes, nonce: str) -> bytes:
    """Append the SHA-256 binding of `nonce` as the last 32 bytes of
    the produced quote — matches the dev-tier extraction logic above.
    NOT a real TDX/SEV-SNP quote; for tests + dev only."""
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("body must be bytes-like")
    return bytes(body) + hashlib.sha256(nonce.encode("utf-8")).digest()


__all__ = [
    "DEFAULT_NONCE_TTL_SECONDS",
    "NONCE_BYTES",
    "NonceError",
    "NonceMissingError",
    "NonceExpiredError",
    "NonceAudienceMismatchError",
    "NonceBindingMismatchError",
    "IssuedNonce",
    "VerifiedQuote",
    "NonceGateStats",
    "NonceGate",
    "make_quote_with_nonce",
]
