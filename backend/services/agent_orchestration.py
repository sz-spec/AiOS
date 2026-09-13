"""
VOS3 Agent Orchestration — TITAN Handshake Layer (v20.5)
=========================================================

Wraps the existing multi-agent system at `backend/ai/agents/multi_agent.py`
with a sovereignty discipline layer. Before any agent in an 11-role pipeline
(Architect / Frontend / Backend / Tester / Reviewer / Researcher / Writer /
Analyst / Developer / Assistant / Custom) runs a task, this module:

  1. Decides whether the task should run on local-titan or cloud, by
     consulting `services.request_manifest.build_request_manifest` plus
     the env flags `VOS3_DEFAULT_LOCAL_FIRST` and `VOS3_LOCAL_ONLY`.
  2. If local-titan is the chosen path, performs a health check on the
     Ollama-TITAN endpoint via the existing `tool_provider._is_ollama_available`.
  3. If TITAN is unresponsive AND the task is LOCAL_ONLY, raises
     `LocalInferenceUnavailableError` (HTTP 503 + Retry-After: 30) —
     never silent cloud fallback.
  4. On TITAN execution success, records a LOCAL_EXECUTION event into
     the kernel MMR audit ledger and returns an `X-VOS3-Attestation`
     header payload describing the routing decision.

This module is the runtime cousin of `services.regional_policy.py`
(EU AI Act enforcement). Where regional_policy gates on user.region_code,
this gates on the explicit local-first preference.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from middleware.auth import AuthenticatedUser

logger = logging.getLogger("vos3.agent_orchestration")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_EXECUTION_LABEL = "OP_LOCAL_EXECUTION"
LOCAL_EXECUTION_LABEL_HASH: str = hashlib.sha256(
    LOCAL_EXECUTION_LABEL.encode("utf-8")
).hexdigest()


# ---------------------------------------------------------------------------
# Decision record (returned to chat / codegen routes for header injection)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AgentRoutingDecision:
    """Per-request routing decision the API layer attaches to the response."""

    role: str
    chosen_model: str
    is_local: bool
    is_titan: bool
    titan_endpoint: Optional[str]
    health_check_passed: bool
    decision_reason: str
    timestamp_ms: int

    def to_attestation_header(self) -> str:
        """Render as a single-line `X-VOS3-Attestation` header value.
        Format: `model=...|is_local=true|reason=...|ts=...`"""
        parts = [
            f"model={self.chosen_model}",
            f"is_local={'true' if self.is_local else 'false'}",
            f"is_titan={'true' if self.is_titan else 'false'}",
            f"reason={self.decision_reason}",
            f"ts={self.timestamp_ms}",
        ]
        if self.is_local and self.is_titan:
            parts.append(f"label_hash={LOCAL_EXECUTION_LABEL_HASH}")
        return "|".join(parts)


# ---------------------------------------------------------------------------
# TITAN handshake
# ---------------------------------------------------------------------------


def _titan_endpoint() -> str:
    """Resolve the TITAN endpoint URL with fallback to OLLAMA_BASE_URL."""
    return os.getenv(
        "OLLAMA_TITAN_ENDPOINT",
        os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


def titan_health_check() -> bool:
    """Return True if the TITAN endpoint responds to /api/tags within 2s.

    Same primitive as `tool_provider._is_ollama_available`, redirected to
    `OLLAMA_TITAN_ENDPOINT` instead of `OLLAMA_BASE_URL`. When TITAN runs
    on the same host as the standard Ollama instance (default case), this
    is functionally identical to the existing health probe.
    """
    base = _titan_endpoint()
    try:
        import httpx

        resp = httpx.get(f"{base}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pre-flight orchestration gate — called from chat / codegen routes
# ---------------------------------------------------------------------------


def orchestrate_pre_flight(
    *,
    role: str,
    complexity: int,
    user: Optional["AuthenticatedUser"] = None,
    body_require_local: Optional[bool] = None,
    body_require_cloud: Optional[bool] = None,
    http_request=None,
) -> AgentRoutingDecision:
    """Pre-flight: build the manifest, decide TITAN vs cloud, perform the
    handshake, raise 503 on LOCAL_ONLY + TITAN-down.

    Returns an `AgentRoutingDecision` the caller stamps into the response
    headers (`X-VOS3-Attestation`) and uses to select the actual LLM
    provider.

    Raises:
        LocalInferenceUnavailableError: if local routing is required
            (privacy mandate or LOCAL_ONLY) AND TITAN is unreachable.
            The HTTPException carries Retry-After: 30 — caller should
            propagate as-is.
    """
    from services.request_manifest import (
        build_request_manifest,
        LocalInferenceUnavailableError,
    )
    from src.efficiency.router import get_optimal_model

    manifest = build_request_manifest(
        body_require_local=body_require_local,
        http_request=http_request,
        user=user,
    )
    is_privacy_mandate = manifest.requires_local_inference
    is_default_local = os.getenv("VOS3_DEFAULT_LOCAL_FIRST", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    chosen = get_optimal_model(
        role=role,
        complexity=complexity,
        local_only=is_privacy_mandate,
        require_cloud=bool(body_require_cloud),
        user=user,
    )

    is_local = chosen.startswith("local-")
    is_titan = chosen == "local-titan"
    health_ok = True
    titan_ep: Optional[str] = None

    if is_local:
        titan_ep = _titan_endpoint()
        health_ok = titan_health_check()
        if not health_ok and is_privacy_mandate:
            # Strict invariant — never silent cloud fallback for privacy mandate.
            logger.warning(
                "TITAN_503_DENY role=%s reason=privacy_mandate_titan_down "
                "endpoint=%s",
                role,
                titan_ep,
            )
            raise LocalInferenceUnavailableError(
                source=manifest.source,
                role=role,
            )
        if not health_ok:
            # Non-mandate caller — TITAN down, fall through to cloud is OK.
            logger.warning(
                "TITAN_DOWN role=%s — falling back to cloud (no privacy mandate)",
                role,
            )
            from src.efficiency.router import assign_model_with_pressure_check

            chosen = assign_model_with_pressure_check(role, complexity, user=user)
            is_local = chosen.startswith("local-")
            is_titan = False

    if is_privacy_mandate:
        reason = f"privacy_mandate_{manifest.source}"
    elif is_titan and is_default_local:
        reason = "default_local_first"
    elif is_titan:
        reason = "titan_explicit"
    elif is_local:
        reason = "ewma_or_regional"
    else:
        reason = "cloud_standard"

    return AgentRoutingDecision(
        role=role,
        chosen_model=chosen,
        is_local=is_local,
        is_titan=is_titan,
        titan_endpoint=titan_ep,
        health_check_passed=health_ok,
        decision_reason=reason,
        timestamp_ms=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# Post-execution audit — record LOCAL_EXECUTION event in MMR
# ---------------------------------------------------------------------------


def record_local_execution(
    decision: AgentRoutingDecision, user_id: Optional[str] = None
) -> bool:
    """Record a successful TITAN-served task into the MMR audit ledger.

    Best-effort: returns False on any failure, never raises. The Python-
    side structured log is the canonical record today; the kernel MMR
    record is supplementary (requires the v20.4 MMR_RECORD_EVENT VBus
    command which is not yet wired — same gap as regional_policy's kernel
    forward).
    """
    if not decision.is_local:
        return False  # Only TITAN/local executions get this event

    # Always log the structured event (canonical audit record)
    logger.info(
        "OP_LOCAL_EXECUTION label=%s role=%s model=%s user=%s endpoint=%s ts=%d",
        LOCAL_EXECUTION_LABEL_HASH,
        decision.role,
        decision.chosen_model,
        user_id or "<anon>",
        decision.titan_endpoint or "(none)",
        decision.timestamp_ms,
    )

    # Best-effort kernel MMR forward (gated by env, same pattern as
    # regional_policy's mmr forward)
    if os.getenv("VOS3_TITAN_MMR_FORWARD_ENABLED", "").lower() in ("1", "true", "yes"):
        try:
            from services.vbus_driver import VBusDriver

            driver = VBusDriver()
            if driver.connect():
                try:
                    driver.send_command(
                        f"MMR_RECORD_EVENT {LOCAL_EXECUTION_LABEL_HASH}"
                    )
                finally:
                    driver.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MMR forward best-effort failure: %s", exc)
    return True


# ---------------------------------------------------------------------------
# v20.5.1 — Phase 5.1 Dynamic Memory Expansion / Graceful Contraction
# ---------------------------------------------------------------------------

# Long-context threshold mirroring kernel/include/ai/kv_cache.h.
LONG_CONTEXT_THRESHOLD_TOKENS = 32768
KV_CACHE_PAGE_SIZE = 2 * 1024 * 1024  # 2 MiB hugepage
KV_CACHE_MAX_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB ceiling

# Bytes per token for KV-cache sizing (8B model at INT8 quant). Used to
# estimate the expansion size required for a long-context request.
_BYTES_PER_TOKEN_KV = 4 * 1024


def _estimate_expansion_bytes(context_length_tokens: int) -> int:
    """Estimate KV-cache expansion needed for a context_length request.
    Bounded by KV_CACHE_MAX_BYTES (10 GiB). Returns 0 for short contexts."""
    if context_length_tokens <= LONG_CONTEXT_THRESHOLD_TOKENS:
        return 0
    extra_tokens = context_length_tokens - LONG_CONTEXT_THRESHOLD_TOKENS
    extra_bytes = extra_tokens * _BYTES_PER_TOKEN_KV
    # Round up to hugepage granularity
    pages = (extra_bytes + KV_CACHE_PAGE_SIZE - 1) // KV_CACHE_PAGE_SIZE
    return min(pages * KV_CACHE_PAGE_SIZE, KV_CACHE_MAX_BYTES)


def request_memory_expansion(slot_id: int, context_length_tokens: int) -> int:
    """Request the kernel to expand `slot_id`'s KV-cache memory if the
    request's context length exceeds the long-context threshold.

    Issues VBus command `MEM_EXPAND <slot_id> <extra_bytes>` which the
    kernel routes to `vos3_vmm_expand_slot_memory`. Returns the new
    total expanded size on success, 0 on failure or no-op (short context).

    Best-effort: never raises. Kernel-offline returns 0 silently — the
    inference proceeds with the slot's static allocation, which may
    produce truncation warnings but never a hard failure.
    """
    extra = _estimate_expansion_bytes(context_length_tokens)
    if extra <= 0:
        return 0

    try:
        from services.vbus_driver import VBusDriver

        driver = VBusDriver()
        if not driver.connect():
            logger.warning(
                "MEM_EXPAND skipped: kernel offline (slot=%d ctx=%d extra=%d)",
                slot_id,
                context_length_tokens,
                extra,
            )
            return 0
        try:
            cmd = f"MEM_EXPAND {slot_id} {extra}"
            resp = driver.send_command(cmd)
            if not resp or not resp.startswith("OK"):
                logger.warning(
                    "MEM_EXPAND rejected: slot=%d resp=%r",
                    slot_id,
                    resp,
                )
                return 0
            # Parse "OK|new_size=N"
            for part in resp.lstrip("OK|").split("|"):
                if part.startswith("new_size="):
                    try:
                        new_size = int(part.split("=", 1)[1])
                        logger.info(
                            "MEM_EXPAND_OK slot=%d ctx=%d extra=%d new_size=%d",
                            slot_id,
                            context_length_tokens,
                            extra,
                            new_size,
                        )
                        return new_size
                    except ValueError:
                        return 0
            return 0
        finally:
            driver.disconnect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MEM_EXPAND best-effort failure: %s", exc)
        return 0


def release_memory_on_idle(slot_id: int) -> int:
    """Graceful Memory Contraction — release all expansion hugepages back
    to the PMM pool when an agent loop goes IDLE.

    Issues VBus command `MEM_CONTRACT <slot_id>` which the kernel routes
    to `vos3_vmm_contract_slot_memory`. Returns the number of bytes
    released, or 0 on no-op / failure.

    Safe to call repeatedly — the kernel-side contraction is idempotent
    (returns 0 if the slot has no expansion footprint).
    """
    try:
        from services.vbus_driver import VBusDriver

        driver = VBusDriver()
        if not driver.connect():
            return 0
        try:
            resp = driver.send_command(f"MEM_CONTRACT {slot_id}")
            if not resp or not resp.startswith("OK"):
                return 0
            for part in resp.lstrip("OK|").split("|"):
                if part.startswith("freed="):
                    try:
                        freed = int(part.split("=", 1)[1])
                        if freed > 0:
                            logger.info(
                                "MEM_CONTRACT_OK slot=%d freed=%d",
                                slot_id,
                                freed,
                            )
                        return freed
                    except ValueError:
                        return 0
            return 0
        finally:
            driver.disconnect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MEM_CONTRACT best-effort failure: %s", exc)
        return 0


__all__ = [
    "LOCAL_EXECUTION_LABEL",
    "LOCAL_EXECUTION_LABEL_HASH",
    "AgentRoutingDecision",
    "titan_health_check",
    "orchestrate_pre_flight",
    "record_local_execution",
    # v20.5.1 — Phase 5.1 dynamic expansion
    "LONG_CONTEXT_THRESHOLD_TOKENS",
    "KV_CACHE_MAX_BYTES",
    "request_memory_expansion",
    "release_memory_on_idle",
]
