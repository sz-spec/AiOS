"""
backend/services/local_llm.py — W6.1 local LLM resolver + healthcheck.

Single chokepoint for the local-first chat completion path. When
`VOS3_LOCALITY_PREFERENCE=local-first` is set the chat handler calls
`resolve_local_first_llm()`, which either:

  - returns a langchain-compatible Ollama chat model bound to
    `$OLLAMA_BASE_URL` (default http://localhost:11434), targeting
    `$VOS3_OLLAMA_MODEL` (default "llama3"), OR

  - raises HTTPException(503) with a clear "offline LLM unavailable"
    message. No cloud fallback is ever attempted from this path,
    REGARDLESS of `VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK`. The whole
    point of the W6.1 barrier is that local-first preference is the
    contract — silent egress to OpenAI/Anthropic under air-gap mode
    is a privacy regression we explicitly forbid.

`ollama_healthcheck()` is the boot-time companion: it probes the
daemon, lists pulled models, and emits an actionable warning if the
configured target model is missing. Returns a structured dict so the
caller can also publish the status to /health / dashboards later.

This module is independent of the SmartRouter logic in
`src/efficiency/router.py`. The router still handles cloud-first
and "auto" modes; W6.1 just inserts a strict local-first gate
ahead of it inside the chat-completion handler.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration accessors
# ---------------------------------------------------------------------------


def _locality_preference() -> str:
    return os.environ.get("VOS3_LOCALITY_PREFERENCE", "auto").lower().strip()


def is_local_first() -> bool:
    """True iff the operator has pinned the local-first locality."""
    return _locality_preference() == "local-first"


def get_ollama_base_url() -> str:
    """Return the Ollama daemon base URL.

    Mirrors `services.ollama_probe.get_base_url()` so the two stay in
    sync but does not import it to avoid a probe-cache side effect at
    every call.
    """
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def get_ollama_model() -> str:
    """Return the Ollama model name the chat handler should target.

    Precedence:
      1. $VOS3_OLLAMA_MODEL — explicit per-deployment override
      2. "llama3"           — sane default (small, widely-pulled)
    """
    return os.environ.get("VOS3_OLLAMA_MODEL", "llama3").strip() or "llama3"


# ---------------------------------------------------------------------------
# Healthcheck — structured result for startup + /health surfaces
# ---------------------------------------------------------------------------


@dataclass
class OllamaHealth:
    """Snapshot of the local Ollama daemon at startup or healthcheck time."""

    reachable: bool
    base_url: str
    target_model: str
    model_present: bool
    pulled_models: tuple[str, ...]
    detail: str

    def is_ready(self) -> bool:
        """True iff the daemon is up AND the configured target model is pulled."""
        return self.reachable and self.model_present


def _normalize_model_name(name: str) -> str:
    """Compare model names case-insensitively, ignoring tag suffixes.

    Ollama lists models as "llama3:latest", "llama3.1:8b", etc.
    Operators set $VOS3_OLLAMA_MODEL to "llama3" most of the time;
    treat "llama3" as matching "llama3:latest" / "llama3:8b" / etc.
    """
    return name.split(":", 1)[0].strip().lower()


def ollama_healthcheck(*, force_probe: bool = True) -> OllamaHealth:
    """Run a healthcheck against the local Ollama daemon.

    `force_probe` triggers a fresh probe; pass False to reuse the
    boot-time cached result.

    Never raises — every failure mode is folded into the returned
    OllamaHealth so the caller can shape the next action (warn,
    refuse boot, run anyway).
    """
    base_url = get_ollama_base_url()
    target = get_ollama_model()
    try:
        from services.ollama_probe import probe_ollama, get_local_models
    except Exception as exc:
        return OllamaHealth(
            reachable=False,
            base_url=base_url,
            target_model=target,
            model_present=False,
            pulled_models=(),
            detail=f"ollama_probe import failed: {exc}",
        )
    reachable = probe_ollama(force=force_probe)
    pulled = get_local_models()
    if not reachable:
        return OllamaHealth(
            reachable=False,
            base_url=base_url,
            target_model=target,
            model_present=False,
            pulled_models=pulled,
            detail=(
                f"Ollama daemon not reachable at {base_url}. "
                f"Start the daemon (e.g. `ollama serve`) to enable the "
                f"offline chat lane."
            ),
        )
    target_norm = _normalize_model_name(target)
    model_present = any(_normalize_model_name(m) == target_norm for m in pulled)
    if not model_present:
        return OllamaHealth(
            reachable=True,
            base_url=base_url,
            target_model=target,
            model_present=False,
            pulled_models=pulled,
            detail=(
                f"Ollama is active but '{target}' is not pulled. "
                f"Run `ollama pull {target}` to enable offline chat. "
                f"Pulled models: {', '.join(pulled) if pulled else '(none)'}"
            ),
        )
    return OllamaHealth(
        reachable=True,
        base_url=base_url,
        target_model=target,
        model_present=True,
        pulled_models=pulled,
        detail=(f"Ollama OK at {base_url}; target model '{target}' is pulled."),
    )


def log_ollama_healthcheck() -> OllamaHealth:
    """Run the healthcheck + emit boot-time log lines.

    Invoked from `startup.py` under the community profile. Loud
    warnings are intentional — a misconfigured offline build should
    be visible in the very first startup output, not just at the
    first chat request.
    """
    health = ollama_healthcheck()
    if health.is_ready():
        logger.info("[W6.1] %s", health.detail)
    elif health.reachable and not health.model_present:
        logger.warning("[W6.1] %s", health.detail)
    else:
        logger.warning("[W6.1] %s", health.detail)
    return health


# ---------------------------------------------------------------------------
# Strict local-first resolver — hard refusal, no cloud fallback
# ---------------------------------------------------------------------------


def resolve_local_first_llm() -> Any:
    """Return a langchain Ollama chat model, or raise HTTPException(503).

    Strict-refusal contract:
      - Local-first is the only mode this function serves. If
        $VOS3_LOCALITY_PREFERENCE != "local-first" the call is
        misrouted and we raise RuntimeError (a programmer error).
      - If Ollama is unreachable OR the target model is missing,
        raise HTTPException(503) IMMEDIATELY. Never instantiate any
        cloud provider, never honor $VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK.
      - On success returns a ChatOllama instance ready to ainvoke.

    Errors are HTTPException so a FastAPI handler can surface them
    verbatim without an extra try/except layer.
    """
    if not is_local_first():
        raise RuntimeError(
            "resolve_local_first_llm() called without "
            "VOS3_LOCALITY_PREFERENCE=local-first set. This is a "
            "programmer error — the cloud path uses get_llm_for_chat()."
        )

    health = ollama_healthcheck(force_probe=False)
    if not health.reachable:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "OFFLINE_LLM_UNAVAILABLE",
                "message": health.detail,
                "base_url": health.base_url,
                "target_model": health.target_model,
                "fix": (
                    "Start the local Ollama daemon. Under local-first "
                    "preference the backend refuses to fall back to "
                    "cloud providers."
                ),
            },
        )
    if not health.model_present:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "OFFLINE_LLM_MODEL_MISSING",
                "message": health.detail,
                "base_url": health.base_url,
                "target_model": health.target_model,
                "pulled_models": list(health.pulled_models),
                "fix": f"Run `ollama pull {health.target_model}`.",
            },
        )

    # Daemon up + model pulled. Build the langchain client. ImportError
    # here is still a 503 — without langchain_ollama we cannot serve
    # offline at all, and the user asked for hard refusal of cloud.
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "OFFLINE_LLM_CLIENT_MISSING",
                "message": (
                    "langchain-ollama is not installed. The local-first "
                    "preference requires this package; cloud fallback "
                    "is intentionally refused."
                ),
                "import_error": str(exc),
                "fix": "pip install langchain-ollama",
            },
        )

    return ChatOllama(
        model=health.target_model,
        base_url=health.base_url,
        # Temperature is intentionally left at the langchain default
        # here; chat_routes layers a per-request override on top.
    )


__all__ = [
    "OllamaHealth",
    "is_local_first",
    "get_ollama_base_url",
    "get_ollama_model",
    "ollama_healthcheck",
    "log_ollama_healthcheck",
    "resolve_local_first_llm",
]
