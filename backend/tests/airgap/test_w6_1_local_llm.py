"""
P3.1 / W6.1 — local-LLM E2E migrated to pytest.

Mirrors the W6.1 standalone runner (18 asserts) — see commit 74c0351.
Uses the mock Ollama server fixture so the test isn't dependent on a
real `ollama serve` running on the host.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Locality + env propagation
# ---------------------------------------------------------------------------


def test_locality_pinned_to_local_first(mock_ollama, airgap_env):
    from services.local_llm import is_local_first

    assert is_local_first() is True


def test_ollama_model_honors_env(mock_ollama, airgap_env, monkeypatch):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    from services.local_llm import get_ollama_model, get_ollama_base_url

    assert get_ollama_model() == "llama3"
    assert get_ollama_base_url() == os.environ["OLLAMA_BASE_URL"]


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def test_healthcheck_reports_ready(mock_ollama, airgap_env, monkeypatch):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    from services.local_llm import ollama_healthcheck

    health = ollama_healthcheck()
    assert health.reachable is True, health.detail
    assert health.model_present is True
    assert health.is_ready() is True
    assert "llama3:latest" in health.pulled_models


def test_healthcheck_503_when_daemon_down(airgap_env, monkeypatch):
    """When Ollama is unreachable, the resolver raises 503 with
    error=OFFLINE_LLM_UNAVAILABLE. NO cloud fallback is attempted."""
    # Point at a port that's almost certainly not listening.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
    from services.local_llm import resolve_local_first_llm
    from services.ollama_probe import reset_cache as _reset

    _reset()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        resolve_local_first_llm()
    assert exc.value.status_code == 503
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error") == "OFFLINE_LLM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Resolver returns ChatOllama bound to the mock URL
# ---------------------------------------------------------------------------


def test_resolver_returns_chat_ollama(mock_ollama, airgap_env, monkeypatch):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    from services.local_llm import resolve_local_first_llm

    llm = resolve_local_first_llm()
    assert type(llm).__name__ == "ChatOllama"
    assert getattr(llm, "model", None) == "llama3"
    assert getattr(llm, "base_url", None) == os.environ["OLLAMA_BASE_URL"]


# ---------------------------------------------------------------------------
# Full POST /api/chat/completions through the harness app + mock Ollama
# ---------------------------------------------------------------------------


def test_chat_completion_routes_to_mock_ollama(
    airgap_client,
    mock_ollama,
    bootstrap_user,
    offline_token,
    network_guard,
    monkeypatch,
):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    clerk_id = "user_w61_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)

    r = airgap_client.post(
        "/api/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "test air-gap"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "llama3"
    assert body["provider"] == "ollama"
    assert body["base_url"] == os.environ["OLLAMA_BASE_URL"]
    content = body["choices"][0]["message"]["content"]
    assert "AIR-GAP-OK" in content


def test_mock_ollama_received_chat_post(
    airgap_client,
    mock_ollama,
    bootstrap_user,
    offline_token,
    monkeypatch,
):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    clerk_id = "user_w61_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    r = airgap_client.post(
        "/api/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "probe"}]},
    )
    assert r.status_code == 200
    chat_posts = [
        c
        for c in mock_ollama.calls
        if c["method"] == "POST" and c["path"] in ("/api/chat", "/api/generate")
    ]
    assert len(chat_posts) >= 1
    assert chat_posts[-1].get("payload", {}).get("model") == "llama3"


def test_network_isolation_during_chat(
    airgap_client,
    mock_ollama,
    bootstrap_user,
    offline_token,
    network_guard,
    monkeypatch,
):
    monkeypatch.setenv("VOS3_OLLAMA_MODEL", "llama3")
    clerk_id = "user_w61_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    airgap_client.post(
        "/api/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": "ping"}]},
    )
    # Only loopback connects (TestClient + mock Ollama) — no violations.
    assert network_guard.violations == []
