"""
EU AI Act Article 12 — Local-First Enforcement Policy
======================================================

When an authenticated user is detected as being inside the EU/EEA region
and has not granted explicit consent for "Global Cloud Processing", every
inference request must be routed to a sovereign path:

    1. Local Ollama / NPU (preferred — zero data egress)
    2. EU sovereign cloud endpoint (if `VOS3_EU_SOVEREIGN_CLOUD_URL` set)
    3. HTTP 403 with compliance error — never silently fall back to US cloud

This module is the single source of truth for the policy. The router
(`backend/src/efficiency/router.py`) and chat / codegen route handlers
import from here.

Spec reference:
- EU AI Act Article 12 (automated logging, no operator intervention)
- Annex III enforcement: 2026-08-02
- Penalty: up to €15M or 3% of worldwide annual turnover

Audit trail:
Every enforcement decision is logged with the canonical event label hash
SHA-256("OP_LOCAL_ENFORCEMENT_EU"). The hash is the cryptographic identifier
that the kernel MMR ledger uses when the v20.4 `MMR_RECORD_EVENT` VBus
command lands. Until then, the structured backend log IS the audit record.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from middleware.auth import AuthenticatedUser

logger = logging.getLogger("vos3.eu_compliance")

# ---------------------------------------------------------------------------
# Canonical event identifiers
# ---------------------------------------------------------------------------

#: The canonical event label name. Forms the basis of the cryptographic ID.
EU_ENFORCEMENT_LABEL: str = "OP_LOCAL_ENFORCEMENT_EU"

#: Pre-computed SHA-256 hash of the label, hex-encoded.
#: This is the value the kernel MMR will use as the leaf data once the
#: v20.4 `MMR_RECORD_EVENT` VBus command lands. Pre-computing it here
#: ensures backend and kernel agree without a runtime hash dependency.
EU_ENFORCEMENT_LABEL_HASH: str = hashlib.sha256(
    EU_ENFORCEMENT_LABEL.encode("utf-8")
).hexdigest()


# ---------------------------------------------------------------------------
# EU-eligible local model preference
# ---------------------------------------------------------------------------

#: Local model assigned to EU users by role. Mirrors `router.yaml`.
#: NOTE: All four models have `enabled: false` by default — the SRE must
#: flip the relevant slot to `true` and ensure Ollama is reachable before
#: enabling EU lockdown in production.
_LOCAL_BY_ROLE: dict[str, str] = {
    "architect": "local-default",  # llama-3.3-70b
    "frontend": "local-default",
    "backend": "local-default",
    "tester": "local-snappy",  # gemma-4-27b — fast for tests
    "reviewer": "local-default",
    "coding": "local-code",  # qwen2.5-coder:72b
    "coding-complex": "local-code",
    "researcher": "local-default",
    "researcher-deep": "local-default",
    # Default fallback for unmapped roles
    "_default": "local-default",
}


def _local_model_for_role(role: str) -> str:
    return _LOCAL_BY_ROLE.get(role, _LOCAL_BY_ROLE["_default"])


# ---------------------------------------------------------------------------
# Sovereign cloud endpoint (optional escape hatch for EU enterprise)
# ---------------------------------------------------------------------------


def _sovereign_cloud_url() -> Optional[str]:
    """Return the EU sovereign cloud endpoint URL if configured.

    Read from `VOS3_EU_SOVEREIGN_CLOUD_URL`. Must be an HTTPS endpoint
    hosted in an EU-AI-Act-compliant data residency region. When unset,
    EU users with no local NPU available receive HTTP 403.
    """
    url = os.getenv("VOS3_EU_SOVEREIGN_CLOUD_URL", "").strip()
    if url and not url.startswith("https://"):
        logger.error(
            "VOS3_EU_SOVEREIGN_CLOUD_URL must use HTTPS — got %r. Treating as unset.",
            url,
        )
        return None
    return url or None


# ---------------------------------------------------------------------------
# Audit logging — backend record + best-effort kernel forward
# ---------------------------------------------------------------------------


@dataclass
class EUEnforcementEvent:
    """A single enforcement decision, suitable for structured logging."""

    user_id: str  # opaque Clerk ID — never PII
    region_code: str
    role: str
    chosen_model: str
    decision: str  # "local" | "sovereign_cloud" | "denied"
    timestamp_ms: int
    label_hash: str = EU_ENFORCEMENT_LABEL_HASH


def record_eu_enforcement(
    user: "AuthenticatedUser",
    role: str,
    chosen_model: str,
    decision: str,
) -> EUEnforcementEvent:
    """Record an EU enforcement decision to the audit log.

    Always writes a structured Python log entry (which is the canonical
    audit record today). When the v20.4 `MMR_RECORD_EVENT` VBus command
    is available, this function will additionally forward the event hash
    to the kernel MMR ledger — the call is currently a no-op stub.
    """
    event = EUEnforcementEvent(
        user_id=user.id,
        region_code=user.region_code or "",
        role=role,
        chosen_model=chosen_model,
        decision=decision,
        timestamp_ms=int(time.time() * 1000),
    )
    logger.info(
        "OP_LOCAL_ENFORCEMENT_EU label=%s user=%s region=%s role=%s model=%s decision=%s",
        event.label_hash,
        event.user_id,
        event.region_code,
        event.role,
        event.chosen_model,
        event.decision,
    )
    # Best-effort kernel MMR forward — feature-flagged; safe no-op when off.
    if os.getenv("VOS3_EU_MMR_FORWARD_ENABLED", "").lower() in ("1", "true", "yes"):
        _try_forward_to_kernel_mmr(event)
    # Phase 26 (G3): feed the decision into the LIVE userspace policy
    # transparency Merkle log. Flag-gated (default OFF) + never raises.
    _feed_policy_transparency(event)
    return event


def _feed_policy_transparency(event: "EUEnforcementEvent") -> None:
    """Best-effort append of this enforcement decision to the userspace
    RFC-6962 transparency log (Phase 26, Gap G3). No-op unless
    ``VOS3_POLICY_TRANSPARENCY_ENABLED`` is set; never raises."""
    try:
        from services.policy_transparency import record_policy_event

        record_policy_event(
            event_type="eu_local_enforcement",
            schema_hash=event.label_hash,
            metadata={
                "user_id": event.user_id,
                "region_code": event.region_code,
                "role": event.role,
                "chosen_model": event.chosen_model,
                "decision": event.decision,
                "timestamp_ms": event.timestamp_ms,
            },
        )
    except Exception as exc:  # noqa: BLE001 — must never affect request flow
        logger.debug("policy transparency feed best-effort failure: %s", exc)


def _try_forward_to_kernel_mmr(event: EUEnforcementEvent) -> None:
    """Best-effort forward of the enforcement event to the kernel MMR ledger.

    The kernel must support a VBus command that accepts a 32-byte label
    hash and calls `mmr_record_event()`. Until that command is wired
    (v20.4), this function logs at DEBUG level and returns. No exception
    is ever raised — kernel availability must not affect request flow.
    """
    try:
        from services.vbus_driver import VBusDriver  # noqa: WPS433

        driver = VBusDriver()
        if not driver.connect():
            logger.debug("EU MMR forward skipped: kernel offline")
            return
        try:
            # Reserved for v20.4: `MMR_RECORD_EVENT <hex_label>`
            driver.send_command(f"MMR_RECORD_EVENT {event.label_hash}")
        finally:
            driver.disconnect()
    except Exception as exc:  # noqa: BLE001 — must never raise to caller
        logger.debug("EU MMR forward best-effort failure: %s", exc)


# ---------------------------------------------------------------------------
# Policy gate — the single decision function
# ---------------------------------------------------------------------------


class EUComplianceError(HTTPException):
    """403 raised when EU enforcement cannot route to a compliant model.

    Carries `error_code: "eu_local_inference_unavailable"` so client SDKs
    can surface a helpful upgrade prompt. The detail body is structured
    JSON, never a raw string.
    """

    def __init__(self, region_code: str, reason: str):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "eu_local_inference_unavailable",
                "message": (
                    "Local inference is unavailable and EU AI Act Article 12 "
                    "compliance prevents falling back to a non-sovereign cloud. "
                    "Configure VOS3_EU_SOVEREIGN_CLOUD_URL for an EU-resident "
                    "endpoint, enable Ollama on the local host, or grant "
                    "explicit Global Cloud Processing consent in account settings."
                ),
                "reason": reason,
                "region_code": region_code,
                "compliance_reference": "EU AI Act Article 12 (Annex III, 2026-08-02)",
                "label_hash": EU_ENFORCEMENT_LABEL_HASH,
            },
        )


def assign_eu_local_or_sovereign(
    user: "AuthenticatedUser",
    role: str,
    *,
    is_ollama_available: Optional[bool] = None,
) -> str:
    """The hard gate for EU users.

    Returns the model name that satisfies EU AI Act Article 12 for the
    given role. Never returns a US-cloud model. Always records the
    decision via `record_eu_enforcement()`.

    Raises `EUComplianceError` (HTTP 403) when:
      - Local Ollama is unavailable AND
      - `VOS3_EU_SOVEREIGN_CLOUD_URL` is not configured
    """
    assert (
        user.requires_local_inference()
    ), "assign_eu_local_or_sovereign called on non-EU or opted-in user"

    if is_ollama_available is None:
        is_ollama_available = _check_ollama_available()

    region_code = user.region_code or "EU"

    # Path 1 — local NPU / Ollama (preferred, zero egress)
    if is_ollama_available:
        chosen = _local_model_for_role(role)
        record_eu_enforcement(user, role, chosen, decision="local")
        return chosen

    # Path 2 — EU sovereign cloud (optional escape hatch for enterprise)
    sovereign = _sovereign_cloud_url()
    if sovereign:
        chosen = "eu-sovereign-cloud"  # virtual model name; routed by tool_provider
        record_eu_enforcement(user, role, chosen, decision="sovereign_cloud")
        return chosen

    # Path 3 — DENY. Never silently leak EU data to US cloud.
    record_eu_enforcement(user, role, chosen_model="(none)", decision="denied")
    raise EUComplianceError(
        region_code=region_code,
        reason="ollama_unavailable_and_no_sovereign_cloud_configured",
    )


def _check_ollama_available() -> bool:
    """Check Ollama health by delegating to the existing `tool_provider`
    probe — keeps a single source of truth for "is local up?"."""
    try:
        from ai.llm.tool_provider import _is_ollama_available  # noqa: WPS433

        return bool(_is_ollama_available())
    except Exception:
        return False


__all__ = [
    "EU_ENFORCEMENT_LABEL",
    "EU_ENFORCEMENT_LABEL_HASH",
    "EUComplianceError",
    "EUEnforcementEvent",
    "assign_eu_local_or_sovereign",
    "record_eu_enforcement",
]
