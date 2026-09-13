# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Regional Policy — Sovereign Zone Routing
=========================================

Generalized regional routing policy. Today this module enforces exactly one
sovereign zone — the EU/EEA — to satisfy EU AI Act Article 12. The
architecture is designed so that additional sovereign zones (e.g. IL, CH,
UK post-DPDI, JP under APPI) can be added later by extending the zone
table without touching the router or the auth middleware.

**Active zones (v20.3):**
- EU/EEA — mandatory local-first inference, deny-on-failure

**Reserved (NOT activated — exclusion-tested):**
IL, CH, UK, JP currently route via the standard EWMA PID controller. These
regions are explicitly excluded from the EU set in `middleware/auth.py`
(`_EU_COUNTRY_CODES`) and are covered by a regression guard in
`tests/test_load_security.py::test_israel_user_unaffected` — any future
change that accidentally extends the EU lockdown to them will break that
test.

----------------------------------------------------------------------
EU/EEA enforcement (the only active zone)
----------------------------------------------------------------------
When an authenticated user is detected as being inside the EU/EEA region
and has not granted explicit consent for "Global Cloud Processing", every
inference request must be routed to a sovereign path:

    1. Local Ollama / NPU (preferred — zero data egress)
    2. EU sovereign cloud endpoint (if `VOS3_EU_SOVEREIGN_CLOUD_URL` set)
    3. HTTP 403 with compliance error — never silently fall back to US cloud

Spec reference:
- EU AI Act Article 12 (automated logging, no operator intervention)
- Annex III enforcement: 2026-08-02
- Penalty: up to €15M or 3% of worldwide annual turnover

Audit trail:
Every enforcement decision is logged with the canonical event label hash
SHA-256("OP_LOCAL_ENFORCEMENT_EU"). The hash is the cryptographic identifier
that the kernel MMR ledger uses when the v20.4 `MMR_RECORD_EVENT` VBus
command lands. Until then, the structured backend log IS the audit record.

==============================================================================
SCAFFOLD: Reconstructed on 2026-05-01 due to data loss. Integrity vs
original v20.6 ELF not guaranteed.

Recovered: docstring + module preamble (lines 1..47, byte-faithful).
Scaffolded (no transcript coverage): everything below — zone table,
detection, enforcement entrypoints, audit logging.

The enforcement contract is preserved (deny-on-failure, never silent
fallback); the implementation choices (function signatures, exception
classes) are SCAFFOLDED to fit how `middleware/auth.py` and the request
router consume this module today.
==============================================================================
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Zone table — extend here when adding new sovereign zones.
# ---------------------------------------------------------------------------

# Canonical EU/EEA two-letter country codes (ISO 3166-1 alpha-2).
# Mirrors `middleware/auth.py::_EU_COUNTRY_CODES` — KEEP IN LOCKSTEP.
EU_COUNTRY_CODES: frozenset[str] = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
        # EEA additions (Art. 12 applies via EEA agreement):
        "IS",
        "LI",
        "NO",
    }
)

# Audit event label — SHA-256 fixes the identifier across kernel + backend.
# Recompute via `python3 -c 'import hashlib; print(hashlib.sha256(b"OP_LOCAL_ENFORCEMENT_EU").hexdigest())'`
EVENT_LABEL_LOCAL_ENFORCEMENT_EU: str = hashlib.sha256(
    b"OP_LOCAL_ENFORCEMENT_EU"
).hexdigest()

# Public aliases consumed by the MCP bridge (tools/vos3_mcp_bridge.py) and the
# load-security suite. These were always referenced but never defined here,
# which broke the EU-compliance MCP tool (ImportError -> error payload) and
# skipped the load-security EU tests. EU_ENFORCEMENT_LABEL is the plaintext
# identifier; EU_ENFORCEMENT_LABEL_HASH is its canonical SHA-256.
EU_ENFORCEMENT_LABEL: str = "OP_LOCAL_ENFORCEMENT_EU"
EU_ENFORCEMENT_LABEL_HASH: str = EVENT_LABEL_LOCAL_ENFORCEMENT_EU


class SovereignZone(str, Enum):
    """Explicit zone tag. NONE means user is outside any active zone."""

    NONE = "none"
    EU_EEA = "eu_eea"


@dataclass(frozen=True)
class ZoneDecision:
    """Structured decision returned by `classify_zone`."""

    zone: SovereignZone
    country_code: Optional[str]
    consent_to_global: bool

    @property
    def requires_local_first(self) -> bool:
        """True iff this request must be routed to a sovereign path."""
        return self.zone is SovereignZone.EU_EEA and not self.consent_to_global


class ComplianceDenied(Exception):
    """Raised by `enforce_routing` when no sovereign route is available
    AND the user has not consented to global cloud processing.

    Caller (FastAPI route) MUST translate to HTTP 403 with the
    `X-VOS3-Compliance-Reason` header set to `event_label_sha256`.
    """

    def __init__(self, *, event_label_sha256: str, reason: str) -> None:
        super().__init__(reason)
        self.event_label_sha256 = event_label_sha256
        self.reason = reason


class EUComplianceError(ComplianceDenied):
    """EU AI Act Art. 12 hard stop: an EU/EEA user without global-cloud consent
    has no compliant (local / sovereign) inference route available.

    Subclasses `ComplianceDenied` so existing `except ComplianceDenied` handlers
    still catch it. FastAPI routes MUST translate to HTTP 403 with
    `X-VOS3-Compliance-Reason` = `event_label_sha256`. This is the symbol the
    router's EU gate imports; its prior absence made the gate silently no-op to
    cloud (the bare-except bug)."""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def classify_zone(country_code: Optional[str], consent_to_global: bool) -> ZoneDecision:
    """Classify a user's sovereign-zone membership.

    Args:
        country_code: ISO 3166-1 alpha-2, uppercased. May be None if the
            auth middleware could not resolve the user's region.
        consent_to_global: True if the user has explicitly opted in to
            global cloud processing (overrides the EU lockdown). The
            consent flag is set in the user's profile and surfaced via
            `AuthenticatedUser.consent_to_global`.

    Returns:
        ZoneDecision describing which zone (if any) applies.
    """
    if country_code is None:
        # Unknown region: do NOT default to EU — that would break IL/US/JP
        # users for whom geo-IP failed. The auth middleware already logs
        # geo-IP failures; downstream router uses standard PID.
        return ZoneDecision(SovereignZone.NONE, None, consent_to_global)
    cc = country_code.upper()
    if cc in EU_COUNTRY_CODES:
        return ZoneDecision(SovereignZone.EU_EEA, cc, consent_to_global)
    return ZoneDecision(SovereignZone.NONE, cc, consent_to_global)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def _local_inference_available() -> bool:
    """Probe for a usable local inference path (Ollama / NPU).

    Returns True if EITHER:
      - `VOS3_OLLAMA_BASE_URL` is set (Ollama daemon is configured), OR
      - `VOS3_NPU_DEVICE` is set (an NPU is bound to this backend instance).
    """
    return bool(
        os.environ.get("VOS3_OLLAMA_BASE_URL") or os.environ.get("VOS3_NPU_DEVICE")
    )


def _check_ollama_available() -> bool:
    """Reachability probe for the local Ollama (TITAN) lane.

    Companion to :func:`_local_inference_available`, which only checks whether a
    local lane is *configured* (env set). This one asks whether the Ollama
    daemon actually *answers*, via the shared exception-safe probe — so the
    sovereign-routing gate can distinguish "configured" from "reachable" and
    never silently fall back to non-sovereign cloud for a privacy-mandate
    request. Returns False (never raises) if the probe is unavailable/unreachable.
    """
    try:
        from services.ollama_probe import probe_ollama

        return bool(probe_ollama())
    except Exception:
        return False


def _eu_sovereign_endpoint() -> Optional[str]:
    """Return the configured EU sovereign cloud endpoint, if any."""
    url = os.environ.get("VOS3_EU_SOVEREIGN_CLOUD_URL")
    return url or None


def enforce_routing(decision: ZoneDecision, *, user_id_for_log: str) -> str:
    """Enforce the sovereign-zone routing policy for one request.

    Args:
        decision: result of `classify_zone(...)`.
        user_id_for_log: opaque user identifier to record in the audit log.
            Pass the Clerk user id, NOT email or PII.

    Returns:
        One of:
          - "local"             — caller should dispatch to local Ollama / NPU.
          - "eu_sovereign"      — caller should dispatch to the EU cloud URL.
          - "global"            — caller may use the standard PID router.

    Raises:
        ComplianceDenied — if EU enforcement applies and no sovereign path
            is available. Caller translates to HTTP 403.
    """
    if not decision.requires_local_first:
        # Not in an active sovereign zone, OR user has explicitly consented
        # to global processing. Standard router applies.
        # OLYMPUS Tier-A G3: log this path too — Art. 12 requires
        # automated logging of EVERY enforcement decision, not just
        # the EU-locked ones. Without this call, audit trails were
        # silently incomplete on the most common request shape.
        _log_enforcement(user_id_for_log, decision, "global")
        return "global"

    # EU/EEA branch — must route to a sovereign path.
    if _local_inference_available():
        _log_enforcement(user_id_for_log, decision, "local")
        return "local"

    eu_url = _eu_sovereign_endpoint()
    if eu_url:
        _log_enforcement(user_id_for_log, decision, "eu_sovereign")
        return "eu_sovereign"

    # No compliant route available. Fail closed.
    _log_enforcement(user_id_for_log, decision, "denied")
    raise ComplianceDenied(
        event_label_sha256=EVENT_LABEL_LOCAL_ENFORCEMENT_EU,
        reason=(
            "EU AI Act Art. 12 enforcement: user is in EU/EEA region "
            f"({decision.country_code}) and no sovereign inference path "
            "is configured (VOS3_OLLAMA_BASE_URL, VOS3_NPU_DEVICE, "
            "VOS3_EU_SOVEREIGN_CLOUD_URL all unset). Refusing to fall "
            "back to non-compliant cloud."
        ),
    )


# Compliant non-cloud model alias returned for EU-locked requests. Matches the
# router's VOS3_DEFAULT_LOCAL_FIRST local lane ("local-titan").
_EU_COMPLIANT_LOCAL_MODEL = "local-titan"


def assign_eu_local_or_sovereign(user, role: str) -> str:
    """EU AI Act Art. 12 model selection for a user that requires local inference.

    The router's EU gate calls this BEFORE any cloud routing whenever
    ``user.requires_local_inference()`` is True. It returns a NON-cloud model
    alias for EU/EEA users without global-cloud consent, or raises
    :class:`EUComplianceError` (fail-closed, HTTP 403) when no sovereign route
    is configured. It MUST NEVER return a cloud model on this path — that is the
    whole point of the Art. 12 gate.

    Args:
        user: the authenticated user (duck-typed: ``country_code``/``region``,
            ``consent_to_global``, ``id``).
        role: the agent role (carried for symmetry with ``assign_model``; the
            compliant lane is local-first regardless of role).

    Returns:
        A compliant local model alias (``"local-titan"``).

    Raises:
        EUComplianceError: if EU enforcement applies and no sovereign path
            (local Ollama/NPU or EU sovereign cloud) is configured.
    """
    # Resolve from any of the known user shapes. CRITICAL: middleware.auth's
    # AuthenticatedUser exposes `region_code` / `global_cloud_consent` — reading
    # only `country_code`/`consent_to_global` made this EU Art.12 gate DEAD CODE
    # for real requests (country_code=None -> zone NONE -> never fail-closed).
    country_code = (
        getattr(user, "country_code", None)
        or getattr(user, "region", None)
        or getattr(user, "region_code", None)
    )
    consent = bool(
        getattr(
            user,
            "consent_to_global",
            getattr(user, "global_cloud_consent", False),
        )
    )
    decision = classify_zone(country_code, consent)
    user_id = str(getattr(user, "id", "<unknown>"))
    try:
        # Returns "local" | "eu_sovereign" | "global", or raises ComplianceDenied.
        enforce_routing(decision, user_id_for_log=user_id)
    except ComplianceDenied as exc:
        # Re-raise as the EU-specific type the router + route layer expect (403).
        raise EUComplianceError(
            event_label_sha256=exc.event_label_sha256, reason=exc.reason
        ) from exc
    # A compliant route exists (local or EU-sovereign endpoint). The concrete
    # endpoint dispatch is handled downstream; the MODEL is always the local
    # lane — never cloud — for an Art. 12-locked request.
    return _EU_COMPLIANT_LOCAL_MODEL


# ---------------------------------------------------------------------------
# Audit logging — structured. Acts as the cryptographic-identifier audit
# record until the v20.4 MMR_RECORD_EVENT VBus command lands.
# ---------------------------------------------------------------------------


def _log_enforcement(user_id: str, decision: ZoneDecision, action: str) -> None:
    """Emit the structured audit record for one routing decision.

    Schema (stable — downstream log shippers depend on this):
        event:         "regional_policy.enforcement"
        event_label:   SHA-256("OP_LOCAL_ENFORCEMENT_EU") (canonical id)
        zone:          "eu_eea" | "none"
        country_code:  ISO alpha-2 or null
        action:        "local" | "eu_sovereign" | "global" | "denied"
        user_id:       opaque id (Clerk sub) — never PII
        consent:       bool
    """
    logger.info(
        "regional_policy.enforcement",
        extra={
            "event_label": EVENT_LABEL_LOCAL_ENFORCEMENT_EU,
            "zone": decision.zone.value,
            "country_code": decision.country_code,
            "action": action,
            "user_id": user_id,
            "consent_to_global": decision.consent_to_global,
        },
    )
    # Phase 26 (G3): feed the routing decision into the LIVE userspace policy
    # transparency Merkle log. Flag-gated (default OFF) + never raises.
    try:
        from services.policy_transparency import record_policy_event

        record_policy_event(
            event_type="regional_policy_enforcement",
            schema_hash=EVENT_LABEL_LOCAL_ENFORCEMENT_EU,
            metadata={
                "zone": decision.zone.value,
                "country_code": decision.country_code,
                "action": action,
                "user_id": user_id,
                "consent_to_global": decision.consent_to_global,
            },
        )
    except Exception as exc:  # noqa: BLE001 — must never affect request flow
        logger.debug("policy transparency feed best-effort failure: %s", exc)
