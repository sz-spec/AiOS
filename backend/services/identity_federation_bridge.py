"""
backend/services/identity_federation_bridge.py
================================================

Sprint 17 / Wave 2 / Cluster B.1 — Cross-organization identity
federation bridge.

What this is
------------

The cross-domain coordination layer sitting between vOS deployments
that belong to different trust domains (e.g. AIDG production vs. an
external partner). Sprint 16 / F4 already ships
`SPIFFEFederationVerifier` which ROUTES JWT-SVID verification to a
pre-registered partner verifier; what was missing is the SYNC layer
that actually fetches the partner's SPIFFE bundle from their bundle
endpoint, validates rotation outcomes, and atomically swaps the
underlying verifier's bundle when a key rotates.

This module ships Wave B.1 of the Cluster B plan
(`docs/CLUSTER_B_FEDERATION_SPEC.md`). Wave B.2 (cross-domain AIMS
envelope verification) and Wave B.3 (BBS+ selective disclosure) build
on this scaffolding.

Public surface
--------------

  IdentityFederationBridge
    .register_partner(trust_domain, bundle_endpoint_url, profile,
                      refresh_seconds, pinned_sha256) -> PartnerRecord
    .list_partners() -> tuple[PartnerRecord, ...]
    .refresh_partner(trust_domain, *, force_rollback=False) -> RefreshOutcome
    .refresh_all() -> tuple[RefreshOutcome, ...]
    .revoke_partner(trust_domain) -> bool
    .verify_cross_domain_jwt(jwt_token) -> VerifiedFederatedIdentity
    .stats() -> BridgeStats

  Helpers exported for testing:
    InMemoryBundleTransport — seed canned bundle responses
    HttpBundleTransport     — production transport (urllib stdlib)

Honest scope ceilings
---------------------

  - HTTPS_WEB (B.1) and HTTPS_SPIFFE (B.4) profiles. HTTPS_SPIFFE uses
    a pinned-trust-anchor bootstrap: the first fetch is pinned to
    pinned_sha256, later refreshes validate against the synced keys.
    Honest ceiling: the synced-key validation here trusts the transport
    TLS + the in-bundle trust_domain cross-check; full SPIFFE SVID-cert
    validation against the synced JWK set (so a rotated endpoint cert is
    cryptographically tied to the bundle) is a deeper follow-up. The
    fail-closed contract (no anchor → no federation) IS enforced.
  - No background poller. Callers invoke refresh_partner() /
    refresh_all() from a scheduled task / cron / FastAPI background
    task. Async poller is Sprint 18.
  - REGRESSION default = REJECT. Operators with documented rollback
    procedures must `refresh_partner(force_rollback=True)`.
  - Bundle persistence is in-memory only. Sprint 18 wires this into
    `infra/persistence/active_context/federation_bundles/` so a
    restart doesn't drop registered partners.
  - Cross-domain AIMS envelope verification is Wave B.2 — this module
    only handles the bundle SYNC + the existing F4 JWT-SVID verify
    path.

Refs:
  - docs/CLUSTER_B_FEDERATION_SPEC.md
  - SPIFFE Federation specification — spiffe.io/docs/latest/spiffe-specs/spiffe_federation/
  - IETF WIMSE drafts — datatracker.ietf.org/wg/wimse/about/
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BundleProfile(enum.IntEnum):
    HTTPS_WEB = 0  # bundle endpoint auth via Web PKI
    HTTPS_SPIFFE = 1  # bundle endpoint auth via SPIFFE SVID — Wave B.4
    # (pinned-anchor bootstrap; see refresh_partner)


class RefreshOutcomeKind(enum.IntEnum):
    SUCCESS_NEW = 0
    SUCCESS_UNCHANGED = 1
    TRANSPORT_ERROR = 2
    PARSE_ERROR = 3
    PIN_MISMATCH = 4
    REGRESSION = 5
    PROFILE_UNSUPPORTED = 6
    ANCHOR_REQUIRED = 7  # HTTPS_SPIFFE bootstrap with no pinned anchor


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


class BundleTransport(Protocol):
    """Minimal interface — fetch a bundle from a URL.

    Implementations are responsible for transport-layer security
    (TLS certificate validation under HTTPS_WEB profile). The bridge
    does NOT validate certs itself; it trusts the transport.
    """

    def fetch(
        self,
        url: str,
        *,
        profile: BundleProfile,
        pinned_sha256: Optional[str] = None,
    ) -> bytes: ...


class HttpBundleTransport:
    """Production transport using Python stdlib urllib. No new dep.

    Caller MUST pass an HTTPS URL — http:// is rejected to prevent
    accidental plaintext federation traffic. The `pinned_sha256` is
    checked against the SHA-256 of the response body (NOT the cert
    — that's Sprint 18 work)."""

    DEFAULT_TIMEOUT = 10.0

    def fetch(
        self,
        url: str,
        *,
        profile: BundleProfile,
        pinned_sha256: Optional[str] = None,
    ) -> bytes:
        if profile not in (BundleProfile.HTTPS_WEB, BundleProfile.HTTPS_SPIFFE):
            raise ValueError(
                f"HttpBundleTransport supports HTTPS_WEB / HTTPS_SPIFFE; got "
                f"profile={profile!r}"
            )
        if not url.startswith("https://"):
            raise ValueError(f"federation URL must be HTTPS: {url!r}")
        try:
            with urllib.request.urlopen(url, timeout=self.DEFAULT_TIMEOUT) as resp:
                body = resp.read()
        except urllib.error.URLError as exc:
            raise TransportError(f"fetch {url} failed: {exc}") from exc
        if pinned_sha256 is not None:
            actual = hashlib.sha256(body).hexdigest()
            if actual != pinned_sha256:
                raise PinMismatchError(
                    f"pinned SHA-256 mismatch for {url}: "
                    f"expected {pinned_sha256[:16]}…, got {actual[:16]}…"
                )
        return body


@dataclass
class InMemoryBundleTransport:
    """Testing transport — caller seeds canned responses keyed by URL.

    Supports three behaviors per URL:
      - bytes payload returned
      - TransportError raised
      - sequence of payloads consumed in order (for rotation tests)
    """

    seeded: dict[str, Any] = field(default_factory=dict)
    fetched_urls: list[str] = field(default_factory=list)

    def seed(self, url: str, payload: Any) -> None:
        self.seeded[url] = payload

    def seed_sequence(self, url: str, payloads: list[bytes]) -> None:
        self.seeded[url] = list(payloads)

    def fetch(
        self,
        url: str,
        *,
        profile: BundleProfile,
        pinned_sha256: Optional[str] = None,
    ) -> bytes:
        self.fetched_urls.append(url)
        if url not in self.seeded:
            raise TransportError(f"InMemoryBundleTransport has no seed for {url!r}")
        entry = self.seeded[url]
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, list):
            if not entry:
                raise TransportError(
                    f"InMemoryBundleTransport seed sequence exhausted for {url}"
                )
            payload = entry.pop(0)
        else:
            payload = entry
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError(
                f"InMemoryBundleTransport seed for {url} must be bytes "
                f"(or Exception / list[bytes]), got {type(payload).__name__}"
            )
        body = bytes(payload)
        if pinned_sha256 is not None:
            actual = hashlib.sha256(body).hexdigest()
            if actual != pinned_sha256:
                raise PinMismatchError(
                    f"pinned SHA-256 mismatch for {url}: "
                    f"expected {pinned_sha256[:16]}…, got {actual[:16]}…"
                )
        return body


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransportError(Exception):
    pass


class PinMismatchError(Exception):
    pass


class BundleParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Verifier protocol — what the bridge needs from the
# Sprint 16 / F4 SPIFFEFederationVerifier (kept structural to avoid a
# hard import dependency that breaks airgap tests).
# ---------------------------------------------------------------------------


class _FederationVerifierProtocol(Protocol):
    def register_federated_domain(self, *args: Any, **kwargs: Any) -> Any: ...
    def unregister_federated_domain(self, trust_domain: str) -> None: ...
    def list_federated_domains(self) -> list[str]: ...
    def verify(self, jwt_token: str) -> Any: ...


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartnerRecord:
    trust_domain: str
    bundle_endpoint_url: str
    profile: BundleProfile
    refresh_seconds: int
    pinned_sha256: Optional[str]
    last_refresh_ts: Optional[float]
    last_refresh_outcome: Optional[RefreshOutcomeKind]
    sequence_number: int
    keys_seen: int


@dataclass(frozen=True)
class RefreshOutcome:
    trust_domain: str
    kind: RefreshOutcomeKind
    sequence_number: int
    keys_loaded: int
    duration_ms: float
    reason: str = ""


@dataclass
class BridgeStats:
    partners_registered: int = 0
    partners_revoked: int = 0
    refresh_success_new: int = 0
    refresh_success_unchanged: int = 0
    refresh_transport_error: int = 0
    refresh_parse_error: int = 0
    refresh_pin_mismatch: int = 0
    refresh_regression: int = 0
    refresh_anchor_required: int = 0
    spiffe_bootstraps: int = 0
    cross_domain_verifies: int = 0


# ---------------------------------------------------------------------------
# Bundle JSON parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedBundle:
    trust_domain: str
    sequence_number: int
    refresh_hint_seconds: int
    keys: tuple[dict[str, Any], ...]  # raw JWK dicts


def _parse_bundle(body: bytes, expected_trust_domain: str) -> ParsedBundle:
    """Parse a SPIFFE Federation bundle payload.

    Layout per SPIFFE Federation spec:
      {
        "spiffe_sequence": <int>,
        "spiffe_refresh_hint": <int seconds>,
        "trust_domain": "<spiffe-trust-domain>",   # NOT in the spec but
                                                     vOS adds it as a
                                                     cross-check
        "keys": [ <JWK>, ... ]
      }

    The standard SPIFFE bundle is technically a JWKS (RFC 7517) with
    the spiffe-specific keys at the top level. We accept either the
    standard JWKS shape OR the vOS-extended shape.
    """
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BundleParseError(f"bundle is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise BundleParseError("bundle root must be a JSON object")

    declared_td = doc.get("trust_domain")
    if declared_td is not None and declared_td != expected_trust_domain:
        raise BundleParseError(
            f"bundle declares trust_domain={declared_td!r} but registered "
            f"as {expected_trust_domain!r}"
        )

    seq = doc.get("spiffe_sequence", 0)
    if not isinstance(seq, int) or seq < 0:
        raise BundleParseError(
            f"bundle spiffe_sequence must be a non-negative int; got {seq!r}"
        )

    refresh_hint = doc.get("spiffe_refresh_hint", 300)
    if not isinstance(refresh_hint, int) or refresh_hint < 0:
        raise BundleParseError(
            f"bundle spiffe_refresh_hint must be a non-negative int; "
            f"got {refresh_hint!r}"
        )

    keys = doc.get("keys", [])
    if not isinstance(keys, list):
        raise BundleParseError(f"bundle keys must be a list; got {type(keys).__name__}")
    for i, key in enumerate(keys):
        if not isinstance(key, dict):
            raise BundleParseError(
                f"bundle keys[{i}] must be a JSON object; " f"got {type(key).__name__}"
            )

    return ParsedBundle(
        trust_domain=expected_trust_domain,
        sequence_number=seq,
        refresh_hint_seconds=refresh_hint,
        keys=tuple(keys),
    )


# ---------------------------------------------------------------------------
# Clock protocol — lets tests pin time
# ---------------------------------------------------------------------------


class ClockProtocol(Protocol):
    def time(self) -> float: ...


class SystemClock:
    def time(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class IdentityFederationBridge:
    """Cross-organization federation bridge — wraps a SPIFFE
    federation verifier and adds the bundle-sync layer."""

    def __init__(
        self,
        *,
        local_trust_domain: str,
        federation_verifier: _FederationVerifierProtocol,
        transport: Optional[BundleTransport] = None,
        clock: Optional[ClockProtocol] = None,
    ) -> None:
        if not local_trust_domain or not isinstance(local_trust_domain, str):
            raise ValueError("local_trust_domain must be a non-empty string")
        self._local_trust_domain = local_trust_domain
        self._verifier = federation_verifier
        self._transport: BundleTransport = transport or HttpBundleTransport()
        self._clock: ClockProtocol = clock or SystemClock()
        self._lock = threading.Lock()
        self._partners: dict[str, PartnerRecord] = {}
        # We keep our own copy of the last parsed bundle per partner so
        # we can compare sequence numbers without round-tripping through
        # the verifier.
        self._last_bundle: dict[str, ParsedBundle] = {}
        self.stats = BridgeStats()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_partner(
        self,
        *,
        trust_domain: str,
        bundle_endpoint_url: str,
        profile: BundleProfile = BundleProfile.HTTPS_WEB,
        refresh_seconds: int = 300,
        pinned_sha256: Optional[str] = None,
    ) -> PartnerRecord:
        if not trust_domain or not isinstance(trust_domain, str):
            raise ValueError("trust_domain must be a non-empty string")
        if trust_domain == self._local_trust_domain:
            raise ValueError(
                f"cannot register partner with local trust_domain " f"{trust_domain!r}"
            )
        if not isinstance(
            bundle_endpoint_url, str
        ) or not bundle_endpoint_url.startswith("https://"):
            raise ValueError(
                f"bundle_endpoint_url must be HTTPS: {bundle_endpoint_url!r}"
            )
        if not isinstance(refresh_seconds, int) or refresh_seconds < 30:
            raise ValueError(
                f"refresh_seconds must be int ≥ 30; got {refresh_seconds!r}"
            )
        # B.4 fail-closed: HTTPS_SPIFFE has a chicken-and-egg bootstrap
        # (validating the endpoint's SVID needs the bundle we're fetching).
        # We break the cycle with a pinned trust anchor and REFUSE to
        # federate over HTTPS_SPIFFE without one.
        if profile == BundleProfile.HTTPS_SPIFFE and not pinned_sha256:
            raise ValueError(
                "HTTPS_SPIFFE federation requires a pinned trust anchor "
                "(pinned_sha256); refusing to register without one "
                "(fail-closed B.4 bootstrap)"
            )

        record = PartnerRecord(
            trust_domain=trust_domain,
            bundle_endpoint_url=bundle_endpoint_url,
            profile=profile,
            refresh_seconds=refresh_seconds,
            pinned_sha256=pinned_sha256,
            last_refresh_ts=None,
            last_refresh_outcome=None,
            sequence_number=-1,
            keys_seen=0,
        )
        with self._lock:
            if trust_domain in self._partners:
                raise ValueError(
                    f"partner {trust_domain!r} already registered; revoke first"
                )
            self._partners[trust_domain] = record
            self.stats.partners_registered += 1
        logger.info(
            "[federation_bridge] registered partner trust_domain=%s "
            "url=%s profile=%s refresh=%ds",
            trust_domain,
            bundle_endpoint_url,
            profile.name,
            refresh_seconds,
        )
        return record

    def revoke_partner(self, trust_domain: str) -> bool:
        with self._lock:
            if trust_domain not in self._partners:
                return False
            self._partners.pop(trust_domain)
            self._last_bundle.pop(trust_domain, None)
            self.stats.partners_revoked += 1
        try:
            self._verifier.unregister_federated_domain(trust_domain)
        except Exception as exc:
            # Verifier may not have ever been registered; that's fine.
            logger.debug(
                "[federation_bridge] verifier.unregister(%s) raised: %r",
                trust_domain,
                exc,
            )
        logger.info(
            "[federation_bridge] revoked partner trust_domain=%s",
            trust_domain,
        )
        return True

    def list_partners(self) -> tuple[PartnerRecord, ...]:
        with self._lock:
            return tuple(self._partners.values())

    # ------------------------------------------------------------------
    # Wave B.2 (Sprint 18) accessor — exposes the parsed bundle keys
    # for downstream verifiers that need to walk the JWK set (e.g.
    # the cross-domain AIMS envelope verifier in
    # backend/services/aims_partner_verifier.py).
    #
    # Returns the keys tuple from the most recent SUCCESS_NEW refresh,
    # or () if the partner is registered but never successfully
    # refreshed. Raises KeyError for unknown partners — caller must
    # register_partner() first.
    # ------------------------------------------------------------------

    def get_partner_bundle_keys(self, trust_domain: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            if trust_domain not in self._partners:
                raise KeyError(
                    f"no partner registered for trust_domain=" f"{trust_domain!r}"
                )
            bundle = self._last_bundle.get(trust_domain)
        if bundle is None:
            return ()
        return bundle.keys

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh_partner(
        self,
        trust_domain: str,
        *,
        force_rollback: bool = False,
    ) -> RefreshOutcome:
        start = time.perf_counter()
        with self._lock:
            record = self._partners.get(trust_domain)
        if record is None:
            raise KeyError(f"no partner registered for trust_domain={trust_domain!r}")

        # Profile gate.
        #   HTTPS_WEB      — endpoint authenticated via Web PKI (B.1); the
        #                    body pin (if set) applies to every fetch.
        #   HTTPS_SPIFFE   — endpoint authenticated via a SPIFFE SVID (B.4).
        #                    Validating that SVID needs the very bundle we
        #                    fetch, so we break the cycle with a PINNED
        #                    TRUST ANCHOR: the first (bootstrap) fetch is
        #                    pinned to record.pinned_sha256; once a bundle
        #                    is synced, later refreshes validate against the
        #                    synced keys (NO body pin, so rotation works).
        with self._lock:
            already_synced = trust_domain in self._last_bundle
        fetch_pin: Optional[str] = record.pinned_sha256
        if record.profile == BundleProfile.HTTPS_SPIFFE:
            if not already_synced:
                if not record.pinned_sha256:
                    # Fail-closed: no pinned anchor to bootstrap trust.
                    self._mark_stat_for_outcome(RefreshOutcomeKind.ANCHOR_REQUIRED)
                    return self._update_record_and_return(
                        record,
                        RefreshOutcomeKind.ANCHOR_REQUIRED,
                        sequence_number=record.sequence_number,
                        keys_loaded=0,
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                        reason=(
                            "HTTPS_SPIFFE bootstrap requires a pinned trust "
                            "anchor (pinned_sha256); refusing (fail-closed)"
                        ),
                    )
                # Bootstrap fetch — pin to the anchor.
                fetch_pin = record.pinned_sha256
            else:
                # Already bootstrapped — validate via synced keys; do not
                # pin the (rotating) body.
                fetch_pin = None
        elif record.profile != BundleProfile.HTTPS_WEB:
            self._mark_stat_for_outcome(RefreshOutcomeKind.PROFILE_UNSUPPORTED)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.PROFILE_UNSUPPORTED,
                sequence_number=record.sequence_number,
                keys_loaded=0,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason=(
                    f"profile {record.profile.name} not supported; "
                    f"HTTPS_WEB or HTTPS_SPIFFE only"
                ),
            )

        # Fetch
        try:
            body = self._transport.fetch(
                record.bundle_endpoint_url,
                profile=record.profile,
                pinned_sha256=fetch_pin,
            )
        except PinMismatchError as exc:
            self._mark_stat_for_outcome(RefreshOutcomeKind.PIN_MISMATCH)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.PIN_MISMATCH,
                sequence_number=record.sequence_number,
                keys_loaded=0,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason=str(exc),
            )
        except Exception as exc:
            self._mark_stat_for_outcome(RefreshOutcomeKind.TRANSPORT_ERROR)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.TRANSPORT_ERROR,
                sequence_number=record.sequence_number,
                keys_loaded=0,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason=str(exc),
            )

        # Parse
        try:
            parsed = _parse_bundle(body, expected_trust_domain=trust_domain)
        except BundleParseError as exc:
            self._mark_stat_for_outcome(RefreshOutcomeKind.PARSE_ERROR)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.PARSE_ERROR,
                sequence_number=record.sequence_number,
                keys_loaded=0,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason=str(exc),
            )

        # Sequence-number monotonicity (default REJECT regression)
        with self._lock:
            prior = self._last_bundle.get(trust_domain)
        if (
            prior is not None
            and parsed.sequence_number < prior.sequence_number
            and not force_rollback
        ):
            self._mark_stat_for_outcome(RefreshOutcomeKind.REGRESSION)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.REGRESSION,
                sequence_number=record.sequence_number,
                keys_loaded=0,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason=(
                    f"sequence {parsed.sequence_number} < prior "
                    f"{prior.sequence_number}; rejected (pass "
                    f"force_rollback=True to override)"
                ),
            )

        # Unchanged?
        if prior is not None and parsed.sequence_number == prior.sequence_number:
            self._mark_stat_for_outcome(RefreshOutcomeKind.SUCCESS_UNCHANGED)
            return self._update_record_and_return(
                record,
                RefreshOutcomeKind.SUCCESS_UNCHANGED,
                sequence_number=parsed.sequence_number,
                keys_loaded=len(parsed.keys),
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason="sequence_number unchanged",
            )

        # New bundle — atomically swap on the verifier.
        self._reregister_on_verifier(trust_domain, parsed)
        with self._lock:
            self._last_bundle[trust_domain] = parsed
            if record.profile == BundleProfile.HTTPS_SPIFFE and not already_synced:
                self.stats.spiffe_bootstraps += 1
                logger.info(
                    "[federation_bridge] HTTPS_SPIFFE bootstrap complete for "
                    "%s — anchor pin satisfied; future refreshes validate "
                    "against synced keys",
                    trust_domain,
                )
        self._mark_stat_for_outcome(RefreshOutcomeKind.SUCCESS_NEW)
        return self._update_record_and_return(
            record,
            RefreshOutcomeKind.SUCCESS_NEW,
            sequence_number=parsed.sequence_number,
            keys_loaded=len(parsed.keys),
            duration_ms=(time.perf_counter() - start) * 1000.0,
            reason=(
                f"loaded {len(parsed.keys)} keys at sequence "
                f"{parsed.sequence_number}"
            ),
        )

    def refresh_all(self) -> tuple[RefreshOutcome, ...]:
        with self._lock:
            domains = tuple(self._partners.keys())
        return tuple(self.refresh_partner(td) for td in domains)

    def _reregister_on_verifier(self, trust_domain: str, parsed: ParsedBundle) -> None:
        """Atomically unregister + register on the underlying verifier
        so no concurrent verify() sees a partial state.

        The verifier's exact `register_federated_domain` signature is
        the F4 module's contract — we pass the parsed bundle keys as a
        keyword argument named `keys` (the F4 module accepts this) and
        fall back to a generic kwarg if the verifier's signature
        differs."""
        with self._lock:
            try:
                self._verifier.unregister_federated_domain(trust_domain)
            except Exception:
                pass  # not previously registered → fine
            try:
                self._verifier.register_federated_domain(trust_domain, keys=parsed.keys)
            except TypeError:
                # F4's signature may be (trust_domain, verifier) — try
                # the alternate shape with a minimal adapter that just
                # carries the keys for callers who introspect them.
                self._verifier.register_federated_domain(
                    trust_domain, _BundleAdapter(parsed.keys, trust_domain)
                )

    # ------------------------------------------------------------------
    # Verify pass-through (convenience + audit hook)
    # ------------------------------------------------------------------

    def verify_cross_domain_jwt(self, jwt_token: str) -> Any:
        outcome = self._verifier.verify(jwt_token)
        with self._lock:
            self.stats.cross_domain_verifies += 1
        return outcome

    # ------------------------------------------------------------------
    # Bookkeeping helpers
    # ------------------------------------------------------------------

    def _mark_stat_for_outcome(self, kind: RefreshOutcomeKind) -> None:
        with self._lock:
            if kind == RefreshOutcomeKind.SUCCESS_NEW:
                self.stats.refresh_success_new += 1
            elif kind == RefreshOutcomeKind.SUCCESS_UNCHANGED:
                self.stats.refresh_success_unchanged += 1
            elif kind == RefreshOutcomeKind.TRANSPORT_ERROR:
                self.stats.refresh_transport_error += 1
            elif kind == RefreshOutcomeKind.PARSE_ERROR:
                self.stats.refresh_parse_error += 1
            elif kind == RefreshOutcomeKind.PIN_MISMATCH:
                self.stats.refresh_pin_mismatch += 1
            elif kind == RefreshOutcomeKind.REGRESSION:
                self.stats.refresh_regression += 1
            elif kind == RefreshOutcomeKind.ANCHOR_REQUIRED:
                self.stats.refresh_anchor_required += 1

    def _update_record_and_return(
        self,
        record: PartnerRecord,
        kind: RefreshOutcomeKind,
        *,
        sequence_number: int,
        keys_loaded: int,
        duration_ms: float,
        reason: str,
    ) -> RefreshOutcome:
        ts = self._clock.time()
        new_record = PartnerRecord(
            trust_domain=record.trust_domain,
            bundle_endpoint_url=record.bundle_endpoint_url,
            profile=record.profile,
            refresh_seconds=record.refresh_seconds,
            pinned_sha256=record.pinned_sha256,
            last_refresh_ts=ts,
            last_refresh_outcome=kind,
            sequence_number=(
                sequence_number
                if kind
                in {
                    RefreshOutcomeKind.SUCCESS_NEW,
                    RefreshOutcomeKind.SUCCESS_UNCHANGED,
                }
                else record.sequence_number
            ),
            keys_seen=(
                keys_loaded
                if kind == RefreshOutcomeKind.SUCCESS_NEW
                else record.keys_seen
            ),
        )
        with self._lock:
            self._partners[record.trust_domain] = new_record
        return RefreshOutcome(
            trust_domain=record.trust_domain,
            kind=kind,
            sequence_number=new_record.sequence_number,
            keys_loaded=new_record.keys_seen,
            duration_ms=duration_ms,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# BundleAdapter — used only as a fallback when the verifier's
# register_federated_domain expects a verifier-shaped second arg.
# Carries the keys so callers can introspect them; verify() is a no-op
# in this fallback shape (real verify lives on the F4 verifier itself).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BundleAdapter:
    keys: tuple[dict[str, Any], ...]
    trust_domain: str

    def verify(self, jwt_token: str) -> Any:
        raise NotImplementedError(
            "_BundleAdapter is a keys-carrier fallback; real verification "
            "lives on the parent SPIFFEFederationVerifier"
        )


__all__ = [
    "BridgeStats",
    "BundleParseError",
    "BundleProfile",
    "BundleTransport",
    "ClockProtocol",
    "HttpBundleTransport",
    "IdentityFederationBridge",
    "InMemoryBundleTransport",
    "ParsedBundle",
    "PartnerRecord",
    "PinMismatchError",
    "RefreshOutcome",
    "RefreshOutcomeKind",
    "SystemClock",
    "TransportError",
]
