"""
backend/services/llm_dispatcher.py — pluggable LLM router abstraction.

W6.25 — decouples the chat-completion handler from the routing decision
so a future external Smart Router can be slotted in WITHOUT touching
api/chat_routes.py. Single chokepoint for "given a request, give me
an LLM I can invoke".

Architecture
------------

::

  ┌──────────────────┐   resolve(ctx)   ┌──────────────────────────┐
  │  chat_routes.py  ├─────────────────►│  BaseLLMRouter (Protocol) │
  └──────────────────┘                  └─────────┬────────────────┘
                                                  │
                ┌─────────────────────────────────┼────────────────────────────┐
                │                                 │                            │
        LocalFirstRouter                 CloudRouter (existing                ExternalSmartRouter
        (W6.1, hard refusal              SmartRouter factory                  (slot reserved
         of cloud egress)                via get_llm_for_chat)                 for CEO's external
                │                                 │                            service; impl pending)
                ▼                                 ▼                            ▼
        Ollama @ localhost            OpenAI / Anthropic /                  ???
                                      Google / etc.

The dispatcher selection lives in :func:`get_dispatcher`; today that
function returns LocalFirstRouter under VOS3_LOCALITY_PREFERENCE=
local-first and CloudRouter otherwise. The future external router
plugs in by adding an `elif` branch — chat_routes.py does not change.

Policy contracts
----------------

Egress barrier — preserved as a POLICY on `LocalFirstRouter.resolve()`,
not as a conditional in the route. The router unconditionally raises
HTTPException(503) when Ollama is unreachable; it ignores
`VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK`. The chat handler simply lets the
exception propagate to FastAPI's error chain.

Observability — each :class:`LLMResolution` carries the chosen
provider + model_id so the cost/observability layer in
chat_routes records the actual upstream service (e.g. "ollama" +
"llama3" vs "openai" + "gpt-4o-mini").

Future external router (sketch)
-------------------------------

When the CEO's external service is ready, plug in like::

    class ExternalSmartRouter(BaseLLMRouter):
        def __init__(self, base_url: str) -> None:
            self._base_url = base_url

        async def resolve_async(self, context: LLMRequestContext) -> LLMResolution:
            # Call the external service. Wire its decision into a
            # LangChain LLM and return it inside LLMResolution.
            ...

and update :func:`get_dispatcher` to branch on the new env flag
(e.g. `VOS3_EXTERNAL_SMART_ROUTER_URL`). chat_routes.py does not
change. The egress-barrier contract for local-first is preserved
because LocalFirstRouter still owns that branch.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response dataclasses — provider-agnostic.
# ---------------------------------------------------------------------------


@dataclass
class LLMRequestContext:
    """What the dispatcher needs to make a routing decision.

    Intentionally minimal — adding fields here is the natural extension
    point as new policies land (region for compliance routing, tenant
    for tier-based selection, etc.). Future fields should default to
    None so older call sites keep working.
    """

    role: str = "coding"
    complexity: int = 5
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    # P4.2 — when present, the request originated from a sandboxed
    # third-party app. Each concrete router calls
    # `PERMISSION_GATE.check(app_id, <scope>)` before returning,
    # converting `ScopeViolation` into `HTTPException(403)`. When
    # None (the default), the call is treated as first-party vOS
    # core and the gate is skipped — the existing W6.* behavior.
    app_id: Optional[str] = None
    # Hint for the dispatcher: "I am a streaming caller" — useful for
    # routers that pick different models for SSE vs unary.
    streaming: bool = False
    # Open extension bag for future per-call hints (region pin, billing
    # tier, sovereignty mode, …) without breaking the dataclass shape.
    extras: dict = field(default_factory=dict)


@dataclass
class LLMResolution:
    """The dispatcher's answer.

    Carries everything the chat handler needs to invoke + bookkeep:
      - ``llm``       — a LangChain Runnable (.ainvoke / .invoke).
      - ``model_id``  — concrete model name being used (for cost,
                        observability, response.model echo).
      - ``provider``  — "ollama" | "openai" | "anthropic" | "google" |
                        "external-smart-router" | ...
      - ``base_url``  — for local / proxied providers; None for cloud
                        SDKs that don't expose one explicitly.
      - ``metadata``  — open bag for router-specific debug info
                        (e.g. routing reason, cost estimate).
    """

    llm: Any
    model_id: str
    provider: str
    base_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BaseLLMRouter — the public interface every concrete router implements.
# ---------------------------------------------------------------------------


def _enforce_app_scope(context: "LLMRequestContext", scope: str) -> None:
    """P4.2 — gate check for app-originated LLM requests.

    No-op when `context.app_id` is None (first-party vOS request).
    Otherwise:
      * On `ScopeViolation`        → HTTPException(403)
      * On `AppIsolated` / unknown → HTTPException(403)
    The two failure modes are conflated to a single 403 with a
    structured detail so the response stays caller-friendly without
    leaking the gate's internal decision tree.
    """
    if not getattr(context, "app_id", None):
        return
    # Imported lazily so plain user-only chat paths don't pay the
    # sandbox-module import on cold dispatch.
    from fastapi import HTTPException as _HTTPException
    from services.app_sandbox import (
        PERMISSION_GATE,
        AppIsolated,
        AppNotFound,
        ScopeViolation,
    )

    try:
        PERMISSION_GATE.check(context.app_id, scope)
    except ScopeViolation as exc:
        raise _HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise _HTTPException(
            status_code=403,
            detail={
                "error": "app_unauthorized",
                "app_id": context.app_id,
                "reason": str(exc),
            },
        ) from exc


class BaseLLMRouter(ABC):
    """Pluggable router interface.

    Concrete subclasses implement :meth:`resolve`. The dispatcher
    selector :func:`get_dispatcher` picks one router per process based
    on environment.
    """

    name: str = "base"

    @abstractmethod
    def resolve(self, context: LLMRequestContext) -> LLMResolution:
        """Return an :class:`LLMResolution` for the given context.

        May raise:
          - ``fastapi.HTTPException`` — typically 503 when the router's
            backend is unavailable AND policy forbids fallback.
          - ``RuntimeError`` — programmer error (the router was called
            outside its configured mode).
        """
        raise NotImplementedError

    # The async variant defaults to running the sync resolver on the
    # event loop's default executor — concrete routers that talk to a
    # remote service (e.g. the future ExternalSmartRouter) should
    # override this with a true `async def`.
    async def resolve_async(self, context: LLMRequestContext) -> LLMResolution:
        import asyncio

        return await asyncio.to_thread(self.resolve, context)


# ---------------------------------------------------------------------------
# LocalFirstRouter — W6.1 with strict egress barrier
# ---------------------------------------------------------------------------


class LocalFirstRouter(BaseLLMRouter):
    """Routes every request to the local Ollama daemon (W6.1).

    Hard refusal of cloud fallback. Ignores
    ``VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK`` — this router is the policy
    boundary for sovereign-strict deployments. If Ollama is
    unreachable or the configured model is missing,
    :class:`fastapi.HTTPException` (503) is raised so the FastAPI
    error chain surfaces it as a clean "Service Unavailable" with
    actionable detail.
    """

    name = "local-first"

    def resolve(self, context: LLMRequestContext) -> LLMResolution:
        # P4.2 — gate check BEFORE we touch Ollama. An app without
        # `llm.local` (or with `network.blocked` restricting all
        # `network.*`) must be rejected up front, not after the
        # daemon probe.
        _enforce_app_scope(context, "llm.local")
        # Imported lazily so the cloud-only path doesn't pay for
        # langchain_ollama loading.
        from services.local_llm import (
            resolve_local_first_llm,
            get_ollama_model,
            get_ollama_base_url,
        )

        llm = resolve_local_first_llm()  # may raise HTTPException(503)
        return LLMResolution(
            llm=llm,
            model_id=get_ollama_model(),
            provider="ollama",
            base_url=get_ollama_base_url(),
            metadata={
                "router": "local-first",
                "role": context.role,
                "complexity": context.complexity,
                "fallback_policy": "strict-refusal",
            },
        )


# ---------------------------------------------------------------------------
# CloudRouter — wraps the existing SmartRouter factory
# ---------------------------------------------------------------------------


class CloudRouter(BaseLLMRouter):
    """Routes via the existing SmartRouter factory (cloud / auto modes).

    Behaviour is intentionally unchanged from the pre-W6.25 path —
    this class is a thin adapter so the dispatcher contract holds.
    Concrete provider (openai / anthropic / google / ollama in auto
    mode) is delegated to the factory and surfaced via metadata.
    """

    name = "cloud"

    def resolve(self, context: LLMRequestContext) -> LLMResolution:
        # P4.2 — cloud routes require BOTH `llm.cloud` AND
        # `network.outbound`. An app with `restrictions: ["network.blocked"]`
        # is killed at the second check; an app without `llm.cloud`
        # is killed at the first. Either way we never hit the
        # SmartRouter factory.
        _enforce_app_scope(context, "llm.cloud")
        _enforce_app_scope(context, "network.outbound")
        from src.efficiency.factory import (
            get_llm_for_task,
            _get_provider_and_model,
        )
        from src.efficiency.router import assign_model

        # First ask the SmartRouter for the alias so we can stamp
        # provider/model into LLMResolution. The same call inside
        # get_llm_for_task is idempotent under the tracking-disabled
        # branch (track=False), so this isn't a double-charge.
        alias = assign_model(context.role, context.complexity, track=False)
        try:
            provider, model_id = _get_provider_and_model(alias)
        except Exception:
            provider, model_id = "unknown", alias

        llm = get_llm_for_task(role=context.role, complexity=context.complexity)
        return LLMResolution(
            llm=llm,
            model_id=model_id,
            provider=provider,
            base_url=None,
            metadata={
                "router": "cloud",
                "router_alias": alias,
                "role": context.role,
                "complexity": context.complexity,
            },
        )


# ---------------------------------------------------------------------------
# Dispatcher selection — module-level singleton, picked once per process.
# ---------------------------------------------------------------------------


_dispatcher: Optional[BaseLLMRouter] = None


def _select_dispatcher() -> BaseLLMRouter:
    """Pick the router that matches the current env.

    Today:
      VOS3_LOCALITY_PREFERENCE=local-first  →  LocalFirstRouter
      anything else                          →  CloudRouter

    Future: insert an `elif VOS3_EXTERNAL_SMART_ROUTER_URL: ...` branch
    that returns the external router, without changing any caller.
    """
    locality = os.environ.get("VOS3_LOCALITY_PREFERENCE", "").lower().strip()
    if locality == "local-first":
        return LocalFirstRouter()
    return CloudRouter()


def get_dispatcher() -> BaseLLMRouter:
    """Return the singleton router selected by env on first call.

    Tests can clear the singleton via :func:`_reset_dispatcher_for_tests`.
    Per-call selection is intentionally avoided — flipping providers
    mid-process would yield inconsistent observability metrics; flip
    the env var and restart the backend instead.
    """
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _select_dispatcher()
        logger.info("LLM dispatcher selected: %s", _dispatcher.name)
    return _dispatcher


def _reset_dispatcher_for_tests() -> None:
    global _dispatcher
    _dispatcher = None


# FastAPI dependency wrapper — `Depends(get_llm_dispatcher_dep)` returns
# the singleton in a way that integrates with FastAPI's DI tree.
def get_llm_dispatcher_dep() -> BaseLLMRouter:
    return get_dispatcher()


__all__ = [
    "LLMRequestContext",
    "LLMResolution",
    "BaseLLMRouter",
    "LocalFirstRouter",
    "CloudRouter",
    "get_dispatcher",
    "get_llm_dispatcher_dep",
]
