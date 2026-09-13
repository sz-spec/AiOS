"""
Smart Router Integration
========================
Model routing via shared-ai-router SmartRouter.
"""

import logging
import os
from pathlib import Path

try:
    from smart_router import SmartRouter

    HAS_SMART_ROUTER = True
except ImportError:
    SmartRouter = None
    HAS_SMART_ROUTER = False

# Observability imports (relative to src package)
from ..observability import track_request, get_metrics, get_cost_breakdown

# Set up logging
logger = logging.getLogger(__name__)


# =============================================================================
# W2.1d — Sovereign locality preference
# =============================================================================
# `VOS3_LOCALITY_PREFERENCE`:
#   - "local-first" (sovereign default): a kernel-attested or Ollama-resident
#     model is selected ahead of any cloud route. Cloud is only used as a last
#     resort when the requested complexity exceeds what any local model in
#     tree can serve and `VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK` is true.
#   - "cloud-first": existing pre-W2.1 behaviour. The SmartRouter's role/
#     complexity matrix is used as-is.
#   - "auto" (default): per-call decision. If the kernel slot is bound or
#     Ollama is reachable, prefer local for complexity < threshold; otherwise
#     follow the standard chain.
#
# `VOS3_LOCAL_MIN_PARAMS_B` (default 70) is the parameter-count floor that
# `local-default` is assumed to meet. When the requested complexity demands
# something heavier and no kernel route is available, we either degrade
# gracefully (return local-default + log) or escalate to cloud.
_LOCALITY_PREFERENCE = (
    os.environ.get("VOS3_LOCALITY_PREFERENCE", "auto").lower().strip()
)
_LOCAL_MIN_PARAMS_B = int(os.environ.get("VOS3_LOCAL_MIN_PARAMS_B", "70"))
_ALLOW_CLOUD_FALLBACK = os.environ.get(
    "VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK", "true"
).lower().strip() in ("1", "true", "yes")
_HIGH_COMPLEXITY_THRESHOLD = (
    9  # complexity at/above this prefers cloud unless kernel-attested
)


class LocalInferenceRequiredError(RuntimeError):
    """Raised when VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK=false and no local
    inference route is available.

    Callers that catch this should either degrade gracefully (return a
    placeholder, fall back to a heuristic) or surface a clear "air-gap
    sovereign mode requires a local model" message — never silently
    initiate an external HTTP request.
    """


# Role → local route under local-first.
_LOCAL_FIRST_ROLE_MAP = {
    "architect": "local-default",
    "frontend": "local-default",
    "backend": "local-default",
    "tester": "local-snappy",
    "reviewer": "local-default",
    "coding": "local-code",
    "coding-complex": "local-code",
    "researcher": "local-default",
    "researcher-deep": "local-default",
}
_LOCAL_FIRST_DEFAULT = "local-default"


def _ollama_available() -> bool:
    """Cached probe result. False (silently) if probe module unavailable."""
    try:
        from services.ollama_probe import probe_ollama

        return probe_ollama()
    except Exception:
        return False


def _ollama_up_safe() -> bool:
    """Exception-safe view of local-Ollama (TITAN) reachability.

    Thin guard around :func:`_ollama_available` so a probe failure can never
    propagate into the routing decision — an unreachable / erroring probe is
    treated as "TITAN down" (False). This is the seam the local-titan routing
    policy (:func:`get_optimal_model`) consults.
    """
    try:
        return bool(_ollama_available())
    except Exception:
        return False


def _kernel_slot_available() -> bool:
    """Whether a kernel-resident slot is bound to the kernel-default lane.

    Filled by W2.1e (ai/llm/kernel_provider.py). Returns False until then.
    """
    try:
        from ai.llm.kernel_provider import is_kernel_default_ready

        return bool(is_kernel_default_ready())
    except Exception:
        return False


def _resolve_local_first(role: str, complexity: int) -> str | None:
    """Return the local model name to use, or ``None`` if cloud must take over.

    Decision matrix (local-first preference):

    1. kernel-default (priority 0)        → if a kernel slot is bound.
    2. role-mapped local-* (priorities 1–5) → if Ollama is reachable.
    3. None                                → caller will fall back to cloud.

    Complexity above the high-complexity threshold escalates to cloud only
    when ``_ALLOW_CLOUD_FALLBACK`` is true (sovereign-strict deployments
    keep it false to forbid silent egress).
    """
    if _kernel_slot_available():
        logger.info(
            "locality=local-first: routing role=%r complexity=%d to kernel-default "
            "(kernel slot bound)",
            role,
            complexity,
        )
        return "kernel-default"

    if _ollama_available():
        local_route = _LOCAL_FIRST_ROLE_MAP.get(role, _LOCAL_FIRST_DEFAULT)
        if complexity >= _HIGH_COMPLEXITY_THRESHOLD:
            logger.warning(
                "locality=local-first: complexity=%d >= %d but no kernel slot — "
                "using %r (~%dB params) instead of escalating to cloud.",
                complexity,
                _HIGH_COMPLEXITY_THRESHOLD,
                local_route,
                _LOCAL_MIN_PARAMS_B,
            )
        else:
            logger.info(
                "locality=local-first: routing role=%r complexity=%d to %r (Ollama lane)",
                role,
                complexity,
                local_route,
            )
        return local_route

    # Neither kernel nor Ollama available
    if _ALLOW_CLOUD_FALLBACK:
        logger.warning(
            "locality=local-first: no local route available (no kernel slot, "
            "no Ollama) — falling through to cloud routing for role=%r.",
            role,
        )
        return None
    # Sovereign-strict: refuse to leak to cloud.
    raise LocalInferenceRequiredError(
        f"VOS3_LOCALITY_PREFERENCE=local-first + "
        f"VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK=false: no kernel slot bound and "
        f"Ollama daemon unreachable — cannot serve role={role!r} without leaking "
        f"to cloud. Bind a kernel slot via POST /api/models/{{id}}/activate or "
        f"start Ollama on $OLLAMA_BASE_URL, then retry."
    )


# =============================================================================
# Smart Router Initialization
# =============================================================================

# Fallback model assignments when SmartRouter is not available
_FALLBACK_MODELS = {
    "architect": "gpt",
    "frontend": "claude-sonnet",
    "backend": "claude-sonnet",
    "developer": "claude-sonnet",  # multi-agent pipeline role (Architect→Developer→Reviewer)
    "tester": "gemini",
    "reviewer": "claude-opus",
    "coding": "claude-sonnet",
    "coding-complex": "gpt-codex",
    "researcher": "gemini-pro",
    "researcher-deep": "claude-opus",
}

# Initialize the router with configuration
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "router.yaml"
if HAS_SMART_ROUTER:
    router = SmartRouter(config_path=_CONFIG_PATH)
else:
    logger.warning(
        "smart_router package not installed — using fallback model assignments"
    )
    router = None


def assign_model(role: str, complexity: int, track: bool = True) -> str:
    """
    Assign the optimal model based on role and complexity.

    Uses the shared-ai-router SmartRouter to select the best model
    based on predefined role mappings and complexity thresholds.

    Args:
        role: The agent role (e.g., 'architect', 'frontend', 'backend', 'tester',
              'reviewer', 'coding', 'coding-complex', 'researcher', 'researcher-deep')
        complexity: Complexity score (0-10, higher = more complex)
        track: Whether to record this selection for observability (default: True)

    Returns:
        The name of the selected model (e.g., 'claude-opus', 'claude-sonnet', 'gpt', 'gemini', 'gemini-pro', 'gpt-codex')
    """
    # W2.1d — Sovereign locality preference takes precedence over the
    # SmartRouter's complexity matrix when set. Cloud-first preserves the
    # legacy behaviour; auto defers to the matrix unless local is reachable.
    if _LOCALITY_PREFERENCE == "local-first":
        local_choice = _resolve_local_first(role, complexity)
        if local_choice is not None:
            return local_choice
        # local_choice is None → fall through to the existing chain
    elif _LOCALITY_PREFERENCE == "auto":
        # Quietly prefer local for low/medium complexity when reachable; the
        # cloud chain still serves anything the local lane can't.
        if complexity < _HIGH_COMPLEXITY_THRESHOLD and (
            _kernel_slot_available() or _ollama_available()
        ):
            local_choice = _resolve_local_first(role, complexity)
            if local_choice is not None:
                return local_choice

    if router is None:
        # Mirror the SmartRouter contract: an unrecognised role is a caller
        # error and must fail loud (a typo'd role silently resolving to a
        # default is a latent bug). The canonical role set is _FALLBACK_MODELS.
        if role not in _FALLBACK_MODELS:
            raise KeyError(role)
        selected = _FALLBACK_MODELS[role]
        logger.info(f"Fallback routing: role='{role}' -> {selected}")
        return selected

    selected_model = router.get_model_for_role(role, complexity)

    # Get model details for logging
    model_config = router.get_model(selected_model)
    threshold = router.config.complexity_threshold

    # Log the selection decision
    if complexity >= threshold:
        if role == "architect":
            reason = (
                f"high complexity ({complexity} >= {threshold}) with architect role"
            )
        else:
            reason = f"high complexity ({complexity} >= {threshold})"
    else:
        reason = f"role mapping for '{role}' (complexity {complexity} < {threshold})"

    logger.info(
        f"Selected {model_config.name} ({model_config.model_id}) due to {reason}"
    )

    return selected_model


# Fallback chain (from router.yaml) — used when primary model fails
_FALLBACK_CHAIN = ["claude-opus", "gpt", "gemini"]

# Template fallback for when ALL models fail
_TEMPLATE_FALLBACK = {
    "python": "# Template: API endpoint\nfrom fastapi import APIRouter\n\nrouter = APIRouter()\n\n@router.get('/')\nasync def root():\n    return {'status': 'ok'}\n",
    "javascript": "// Template: Express server\nconst express = require('express');\nconst app = express();\n\napp.get('/', (req, res) => res.json({ status: 'ok' }));\n\nmodule.exports = app;\n",
    "react": "// Template: React component\nimport React from 'react';\n\nexport default function App() {\n  return <div>Loading...</div>;\n}\n",
}


# ---------------------------------------------------------------------------
# EWMA pressure tracker — prevents rapid oscillation when pressure_ratio
# hovers near the 0.85 threshold (PID-like hysteresis).
#
# Enter degraded mode: smoothed ratio > 0.85
# Exit degraded mode:  smoothed ratio < 0.75  (10% hysteresis band)
# EWMA alpha = 0.30 — responsive but not jittery
# ---------------------------------------------------------------------------
_CRITICAL_ROLES = {"architect", "reviewer", "researcher-deep"}


def get_optimal_model(
    role: str,
    complexity: int,
    local_only: bool = False,
    require_cloud: bool = False,
    user: object = None,
) -> str:
    """Resolve the model name for a role, honouring sovereign local-first policy.

    Decision order (v20.5 local-titan routing):

    1. ``local_only`` (privacy mandate / LOCAL_ONLY) → always ``"local-titan"``.
       Sovereignty wins even for critical roles; the orchestrator
       (:func:`services.agent_orchestration.orchestrate_pre_flight`) performs the
       TITAN health-check and raises 503 rather than ever falling back to cloud.
    2. ``require_cloud`` → defer to the cloud assignment (:func:`assign_model`).
    3. ``VOS3_DEFAULT_LOCAL_FIRST`` set AND ``role`` is non-critical AND TITAN is
       reachable (:func:`_ollama_up_safe`) → ``"local-titan"``. Critical roles
       (architect / reviewer / researcher-deep) stay on cloud for quality unless
       ``local_only`` forbids it.
    4. Otherwise → the normal cloud/complexity assignment (:func:`assign_model`).

    Args:
        role: agent role (e.g. ``"developer"``, ``"architect"``).
        complexity: task complexity 1..10 (passed through to cloud assignment).
        local_only: force sovereign local execution for every role.
        require_cloud: caller explicitly opts out of local-first for this call.
        user: optional AuthenticatedUser (reserved for per-user policy; unused
            here so the routing stays deterministic for the OS-level decision).

    Returns:
        A model alias from ``router.yaml`` — ``"local-titan"`` for the local
        sovereign lane, or a cloud alias from :func:`assign_model`.
    """
    # ``user`` is accepted for call-site compatibility / future per-user policy
    # but intentionally not consulted: the local-vs-cloud lane is an OS-level
    # decision that must stay deterministic per (role, env, TITAN health).
    if local_only:
        return "local-titan"

    if require_cloud:
        return assign_model(role, complexity, track=False)

    default_local_first = os.getenv("VOS3_DEFAULT_LOCAL_FIRST", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if default_local_first and role not in _CRITICAL_ROLES and _ollama_up_safe():
        return "local-titan"

    return assign_model(role, complexity, track=False)


_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_PRESSURE_ALPHA = 0.30
_PRESSURE_ENTER_THRESHOLD = 0.85
_PRESSURE_EXIT_THRESHOLD = 0.75

_pressure_ewma: float = 0.0
_in_degraded_mode: bool = False


def _update_pressure_ewma(raw_ratio: float) -> bool:
    """Update the EWMA and return True when model downgrade is warranted."""
    global _pressure_ewma, _in_degraded_mode
    _pressure_ewma = (
        _PRESSURE_ALPHA * raw_ratio + (1.0 - _PRESSURE_ALPHA) * _pressure_ewma
    )
    if not _in_degraded_mode and _pressure_ewma > _PRESSURE_ENTER_THRESHOLD:
        _in_degraded_mode = True
        logger.warning(
            "pressure_ewma: entering degraded mode (ewma=%.3f > %.2f)",
            _pressure_ewma,
            _PRESSURE_ENTER_THRESHOLD,
        )
    elif _in_degraded_mode and _pressure_ewma < _PRESSURE_EXIT_THRESHOLD:
        _in_degraded_mode = False
        logger.info(
            "pressure_ewma: exiting degraded mode (ewma=%.3f < %.2f)",
            _pressure_ewma,
            _PRESSURE_EXIT_THRESHOLD,
        )
    return _in_degraded_mode


def assign_model_with_pressure_check(
    role: str,
    complexity: int,
    *,
    driver_pressure: dict | None = None,
    user: object | None = None,
) -> str:
    """Like assign_model() but auto-downgrades to Haiku 4.5 under driver pressure.

    Uses an EWMA-smoothed pressure ratio with a 10% hysteresis band to prevent
    rapid model oscillation when the kernel pressure hovers near the threshold.

    Degraded mode: enters at ewma > 0.85, exits at ewma < 0.75.
    Critical roles (architect, reviewer, researcher-deep) are never downgraded.

    [RESTORED-FROM-LOGS] EU AI Act Article 12: if `user.requires_local_inference()`
    is True (i.e. user is in EU/EEA and has not granted Global Cloud Processing
    consent), the cloud paths are bypassed entirely — `assign_eu_local_or_sovereign`
    is consulted instead. May raise `EUComplianceError` (HTTP 403) when no
    compliant route is available.
    """
    # ---- EU AI Act Article 12 gate (highest priority; FAIL-CLOSED) ----
    # Runs BEFORE pressure / role mapping. `assign_eu_local_or_sovereign` returns
    # a compliant local model or raises `EUComplianceError` (HTTP 403) when no
    # sovereign route exists. We DELIBERATELY do not wrap this in a broad
    # try/except: silently falling through to cloud routing for an EU-locked
    # request is exactly the Art. 12 violation the prior bare-except introduced
    # (it swallowed the ImportError from the then-missing symbols and no-opped
    # the gate to cloud). EUComplianceError MUST propagate to the route layer.
    if user is not None and getattr(user, "requires_local_inference", None):
        if user.requires_local_inference():
            from services.regional_policy import assign_eu_local_or_sovereign

            return assign_eu_local_or_sovereign(user, role)

    # ---- DRIVER_PRESSURE EWMA → Haiku downgrade (cloud → cloud) ----
    if driver_pressure and role not in _CRITICAL_ROLES:
        raw_ratio = driver_pressure.get("pressure_ratio", 0.0)
        congested = driver_pressure.get("congested", False)
        degraded = _update_pressure_ewma(raw_ratio) or congested
        if degraded:
            logger.warning(
                "assign_model_with_pressure_check: downgrading role=%r to Haiku "
                "(ewma=%.3f, congested=%s)",
                role,
                _pressure_ewma,
                congested,
            )
            return _HAIKU_MODEL

    return assign_model(role, complexity)


def assign_model_with_fallback(
    role: str, complexity: int, failed_models: list | None = None
) -> str:
    """Assign model with fallback chain when primary model fails.

    Tries the primary model for the role first, then falls through
    the fallback chain (claude-opus -> gpt -> gemini) skipping any
    models that have already failed.

    Args:
        role: Agent role.
        complexity: Complexity score (0-10).
        failed_models: List of model names that have already failed.

    Returns:
        Model name to try next, or 'template' if all models exhausted.
    """
    failed = set(failed_models or [])

    # Try primary model first
    primary = assign_model(role, complexity, track=False)
    if primary not in failed:
        return primary

    # Try fallback chain
    for model in _FALLBACK_CHAIN:
        if model not in failed:
            logger.warning(
                f"Fallback: role='{role}' primary='{primary}' failed, "
                f"trying '{model}'"
            )
            return model

    # All models exhausted
    logger.error(
        f"All models exhausted for role='{role}': {failed}. "
        f"Returning template fallback."
    )
    return "template"


def get_template_fallback(language: str = "python") -> str:
    """Get template code when all models fail.

    Args:
        language: Target language for the template.

    Returns:
        Pre-built template code string.
    """
    return _TEMPLATE_FALLBACK.get(language, _TEMPLATE_FALLBACK["python"])


def assign_model_with_tracking(role: str, complexity: int):
    """
    Assign model and return a context manager for tracking the request.

    Usage:
        model, tracker = assign_model_with_tracking("coding", 5)
        with tracker as req:
            response = call_llm(model, prompt)
            req.tokens_in = count_tokens(prompt)
            req.tokens_out = count_tokens(response)

    Returns:
        Tuple of (model_name, request_tracker_context_manager)
    """
    model = assign_model(role, complexity, track=False)
    return model, track_request(role, complexity, model)


def resolve_model_id(role: str, complexity: int) -> str:
    """W2.5 — return the concrete provider model_id for a (role, complexity).

    This is the canonical replacement for hardcoded fallback strings like
    ``"gpt-4o-mini"`` scattered across the codebase. Callers that need a
    string to embed in an HTTP payload (e.g. raw OpenAI/Anthropic requests
    in ``backend/vos/``) should call this rather than hardcoding a model.

    The function:
      1. Calls :func:`assign_model` to pick an alias honouring
         ``VOS3_LOCALITY_PREFERENCE``.
      2. If the alias resolves to a kernel- or local-bound entry, returns
         that alias verbatim — the caller is expected to route through
         :class:`ai.llm.kernel_provider.KernelProvider` or the Ollama
         OpenAI-compatible endpoint, NOT through api.openai.com.
      3. Otherwise translates the alias to the concrete provider model_id
         via the SmartRouter's loaded config (or :data:`_FALLBACK_DEFAULT_IDS`
         if the router isn't initialised).

    Air-gap strict mode (``VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK=false`` with
    no local lane available) propagates :class:`LocalInferenceRequiredError`
    from :func:`assign_model` — callers MUST handle this or surface it to
    the user. Returning a placeholder cloud string would silently leak the
    air-gap.
    """
    alias = assign_model(role, complexity, track=False)
    if alias is None or alias.startswith(("local-", "kernel-")):
        # Caller is responsible for dispatching through the local provider.
        return alias or _LOCAL_FIRST_DEFAULT

    if router is not None:
        try:
            cfg = router.get_model(alias)
            return cfg.model_id
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "resolve_model_id: router lookup failed for alias=%r (%s); "
                "using built-in fallback table.",
                alias,
                exc,
            )

    # Router unavailable — best-effort static map.
    return _FALLBACK_DEFAULT_IDS.get(alias, _FALLBACK_DEFAULT_IDS["claude-sonnet"])


# Concrete model_id mapping used when the SmartRouter config can't be
# consulted (e.g. import-time tests or air-gap probes). Source of truth
# remains ``backend/config/router.yaml``.
_FALLBACK_DEFAULT_IDS = {
    "claude-opus": "claude-opus-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
    "gpt": "gpt-4o",
    "gpt-codex": "o3-mini",
    "gemini": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
    "local-default": "llama-3.3-70b",
    "local-snappy": "gemma-4-27b",
    "local-code": "qwen2.5-coder:72b",
    "local-light": "codestral:22b",
    "kernel-default": "kernel-resident-gguf",
}


# =============================================================================
# OS Development Pipeline (6-Stage Industrial Grade)
# =============================================================================

# Stage mappings for VOS3 OS development
_OS_PIPELINE_STAGES = {
    "security_standards": "claude-opus",  # Stage 0: POSIX requirements
    "architect": "gpt",  # Stage 1: Preemptive Scheduling, SMP (gpt-4o)
    "expander": "gemini-pro",  # Stage 2: ABI verification (gemini-2.5-pro)
    "execution": "claude-opus",  # Stage 3: MISRA C code
    "reviewer": "gpt",  # Stage 4: Load simulation (gpt-4o)
    "fuzzer": "claude-sonnet",  # Stage 4.5: Fuzz testing (small model OK)
    "formal_verifier": "gpt",  # Stage 5: TLA+/Coq proofs (gpt-4o)
    "unit_tests": "claude-sonnet",  # Tests only (small model OK)
    # System Audit stages
    "scanner": "gemini-pro",  # Audit Stage 1: Full codebase scan (1M context, gemini-2.5-pro)
    "analyzer": "gpt",  # Audit Stage 2: Architecture analysis (gpt-4o)
}


def get_os_pipeline_model(stage: str, complexity: int = 9) -> str:
    """
    Get model for OS development pipeline.

    6-Stage Pipeline for Industrial-Grade OS:
    - Stage 0: security_standards (injected as system prompt)
    - Stage 1: architect - Plans kernel structures with Preemptive Scheduling + SMP
    - Stage 2: expander - Verifies ABI and Context Switch alignment
    - Stage 3: execution - Writes MISRA C compliant code
    - Stage 4: reviewer - Static analysis + load simulation
    - Stage 5: formal_verifier - TLA+/Coq proofs for critical sections

    Args:
        stage: The pipeline stage name
        complexity: Complexity score (default 9 for OS development)

    Returns:
        The name of the selected model for this stage
    """
    model = _OS_PIPELINE_STAGES.get(stage)

    if model is None:
        logger.warning(
            f"Unknown OS pipeline stage '{stage}', falling back to claude-opus"
        )
        return "claude-opus"

    # Verify model exists in router config (if router available)
    if router is not None:
        try:
            model_config = router.get_model(model)
            logger.info(
                f"OS Pipeline Stage '{stage}' -> {model_config.name} "
                f"({model_config.model_id})"
            )
        except Exception as e:
            logger.warning(
                f"Model '{model}' not found in config, falling back to claude-opus: {e}"
            )
            return "claude-opus"
    else:
        logger.info(f"OS Pipeline Stage '{stage}' -> {model} (no router)")

    return model


def get_os_pipeline_stages() -> dict:
    """
    Get all OS pipeline stage mappings.

    Returns:
        Dictionary mapping stage names to model names
    """
    return _OS_PIPELINE_STAGES.copy()


# ===========================================================================
# Phase 24 — fail-closed agent-egress chokepoint (Gap G1).
#
# NOTE (honest scope): there is no live production caller of this chokepoint
# yet — the real outbound-send path and the per-byte TaintedBuffer/fd plumbing
# do not exist. This implements the GATE WRAPPER + a placeholder sender so the
# fail-closed contract is real and testable; production must (1) route its
# actual agent egress through dispatch_agent_response, and (2) thread the real
# TaintedBuffer + socket fd + runtime pid into `context`.
# ===========================================================================


class EgressDenied(Exception):
    """The kernel egress gate denied (or could not evaluate) an outbound agent
    dispatch. The route layer maps this to HTTP 403 + a [SECURITY] audit record
    (see app.py `egress_denied_handler`). FAIL-CLOSED: never swallowed, never
    downgraded. `.reason` carries the precise cause for the audit log (not the
    client body)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def execute_raw_egress_dispatch(session_id: str, payload: dict) -> dict:
    """PLACEHOLDER for the real outbound send (socket / token dispatch). The
    actual send path + TaintedBuffer plumbing are not built; this echoes so the
    gate wrapper is end-to-end testable. Replace at integration."""
    return {"session_id": session_id, "status": "dispatched", "payload": payload}


async def dispatch_agent_response(
    session_id: str, payload: dict, context: dict
) -> dict:
    """Central egress chokepoint. When ``VOS3_ENABLE_LIVE_LSM_GATE`` is set, every
    outbound agent dispatch is gated by the kernel LSM egress gate.

    FAIL-CLOSED (Gap G1, INV-5): a ``DecisionKind.DENY`` OR an incomplete gate
    context (missing pid / fd / tainted buffer) raises ``EgressDenied`` -> 403.
    The gate's decision is EVALUATED, never discarded (the prior draft dropped
    the return value, which is fail-OPEN). Flag off (dev/CI default) -> the gate
    is skipped and this is byte-identical to the ungated dispatch."""
    # Phase 32 (Gap G6): Safe-Lock — a runtime-integrity violation disables ALL
    # egress unconditionally (independent of the LSM-gate flag). egress_safe_locked
    # reads the watchdog without constructing it, so the common path is a single
    # bool check and an uninitialised watchdog never causes a false lockdown.
    from services.integrity_watchdog import egress_safe_locked

    locked, lock_reason = egress_safe_locked()
    if locked:
        raise EgressDenied(f"runtime integrity Safe-Lock engaged: {lock_reason}")

    # Deferred import: avoids an import cycle + the connector's module-load cost
    # when the gate is disabled (the common path).
    from security.kernel_gate_connector import (
        DecisionKind,
        SinkKind,
        egress_gate_enabled,
        get_kernel_gate,
    )

    if egress_gate_enabled():
        gate = get_kernel_gate()
        pid = context.get("runtime_pid")
        fd = context.get("target_fd")
        tainted = context.get("tainted_buffer")
        # INV-5: cannot gate an unknown egress -> REFUSE. No fd=0 default, no
        # proceed-on-missing-context. An unknown egress is a denied egress.
        if pid is None or fd is None or tainted is None:
            raise EgressDenied(
                "egress gate active but context incomplete "
                f"(pid={pid}, fd={fd}, "
                f"tainted={'set' if tainted is not None else 'missing'})"
            )
        decision = gate.enforce_egress(
            pid=pid, fd=fd, buffer=tainted, sink_kind=SinkKind.NETWORK_EGRESS
        )
        if decision.decision == DecisionKind.DENY:
            raise EgressDenied(
                f"kernel egress gate DENY session={session_id} "
                f"pid={pid} fd={fd}: {decision.reason}"
            )
        # LIVE note: userspace returns ALLOW here; the kernel still EPERMs the
        # actual write() if policy denies — that failure surfaces at the send.

    return await execute_raw_egress_dispatch(session_id, payload)


__all__ = [
    "EgressDenied",
    "dispatch_agent_response",
    "execute_raw_egress_dispatch",
    "router",
    "HAS_SMART_ROUTER",
    "assign_model",
    "assign_model_with_pressure_check",
    "assign_model_with_fallback",
    "assign_model_with_tracking",
    "resolve_model_id",
    "LocalInferenceRequiredError",
    "get_template_fallback",
    "track_request",
    "get_metrics",
    "get_cost_breakdown",
    "get_os_pipeline_model",
    "get_os_pipeline_stages",
]
