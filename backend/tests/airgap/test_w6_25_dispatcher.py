"""
P3.1 / W6.25 — dispatcher abstraction migrated to pytest.

Mirrors the W6.25 standalone runner (20 asserts) — see commit a72afd6.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.airgap_harness import reset_global_singletons

# ---------------------------------------------------------------------------
# Dispatcher selection by env
# ---------------------------------------------------------------------------


def test_local_first_env_selects_local_first_router(mock_ollama, airgap_env):
    from services.llm_dispatcher import (
        BaseLLMRouter,
        LocalFirstRouter,
        get_dispatcher,
    )

    d = get_dispatcher()
    assert isinstance(d, LocalFirstRouter), type(d).__name__
    assert isinstance(d, BaseLLMRouter)
    assert d.name == "local-first"


def test_non_local_first_env_selects_cloud_router(airgap_env, monkeypatch):
    """Flip locality + reset singleton → CloudRouter."""
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "cloud-first")
    reset_global_singletons()
    from services.llm_dispatcher import CloudRouter, get_dispatcher

    d = get_dispatcher()
    assert isinstance(d, CloudRouter)
    assert d.name == "cloud"


# ---------------------------------------------------------------------------
# LocalFirstRouter.resolve() returns ChatOllama with rich metadata
# ---------------------------------------------------------------------------


def test_local_first_resolution_metadata(mock_ollama, airgap_env, monkeypatch):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    router = LocalFirstRouter()
    resolution = router.resolve(LLMRequestContext(role="coding", complexity=5))

    assert resolution.provider == "ollama"
    assert resolution.model_id == "llama3"
    import os

    assert resolution.base_url == os.environ["OLLAMA_BASE_URL"]
    assert type(resolution.llm).__name__ == "ChatOllama"
    assert resolution.metadata.get("router") == "local-first"
    assert resolution.metadata.get("fallback_policy") == "strict-refusal"


# ---------------------------------------------------------------------------
# Egress barrier: Ollama down → HTTPException(503)
# ---------------------------------------------------------------------------


def test_resolver_503_when_ollama_unreachable(airgap_env, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    reset_global_singletons()
    from fastapi import HTTPException
    from services.llm_dispatcher import LocalFirstRouter, LLMRequestContext

    router = LocalFirstRouter()
    with pytest.raises(HTTPException) as exc:
        router.resolve(LLMRequestContext())
    assert exc.value.status_code == 503
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error") == "OFFLINE_LLM_UNAVAILABLE"
    # The 503 must include a 'fix' message — no cloud LLM was reached.
    assert "fix" in exc.value.detail


# ---------------------------------------------------------------------------
# POST /api/chat/completions routes through the dispatcher
# ---------------------------------------------------------------------------


def test_chat_route_uses_dispatcher(
    airgap_client,
    mock_ollama,
    bootstrap_user,
    offline_token,
    network_guard,
    airgap_env,
    monkeypatch,
):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    clerk_id = "user_w625_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    r = airgap_client.post(
        "/api/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "dispatch test"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "ollama"
    assert body["model"] == "llama3"
    assert body["router"] == "local-first"
    assert "AIR-GAP-OK" in body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Static guard: chat_routes.py is dispatcher-only (no is_local_first conditional)
# ---------------------------------------------------------------------------


def test_chat_routes_has_no_legacy_local_first_conditional():
    """W6.25 removed the W6.1 inline branch. This guard catches a
    future re-introduction during refactoring."""
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "api" / "chat_routes.py"
    ).read_text()
    legacy_marker = (
        "from services.local_llm import is_local_first, resolve_local_first_llm"
    )
    assert legacy_marker not in src, (
        "chat_routes.py is back to importing is_local_first inline. "
        "Restore the W6.25 dispatcher chokepoint."
    )
    # And the new path must be present.
    new_marker = "from services.llm_dispatcher import get_dispatcher, LLMRequestContext"
    assert new_marker in src
