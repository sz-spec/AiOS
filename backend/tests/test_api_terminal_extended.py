"""
Extended Chat API Edge-Case Tests
===================================

Covers low-coverage areas and boundary conditions for /api/chat/* endpoints
NOT already exercised by test_api_chat.py:

- ChatRequest validation (empty messages, temperature/max_tokens boundaries, oversized content)
- Provider status endpoint
- History edge cases (nonexistent sessions)
- Streaming format verification (content-type, done event)
- Model selection (explicit valid / invalid model names)
- Summarize edge cases (already_summarized, empty agent_ids, whitespace-only messages)

Uses a locally-created FastAPI app with the chat router mounted directly,
bypassing the production middleware stack (api_key, auth) that requires
env vars only available in deployment.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.chat_routes import router as chat_router
from api.deps import get_current_user, AuthenticatedUser

# ---------------------------------------------------------------------------
# Build a minimal app with just the chat router -- no production middleware.
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(chat_router, prefix="/api/chat")

_DEV_USER = AuthenticatedUser(
    id="test_user",
    email="test@test.com",
    org_id="org_test",
    permissions=["read", "write"],
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def local_chat_dispatcher():
    """Exercise request validation with a deterministic provider, never cloud."""
    response = MagicMock(content="test response", usage_metadata={})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    resolution = MagicMock(llm=llm, model_id="test-model")
    dispatcher = MagicMock()
    dispatcher.resolve.return_value = resolution
    with patch("services.llm_dispatcher.get_dispatcher", return_value=dispatcher):
        yield dispatcher


@pytest.fixture
def client():
    """TestClient with auth dependency overridden -- no middleware."""
    _test_app.dependency_overrides[get_current_user] = lambda: _DEV_USER
    yield TestClient(
        _test_app, base_url="http://localhost", raise_server_exceptions=False
    )
    _test_app.dependency_overrides.pop(get_current_user, None)


def _tracker_ctx():
    """Return a mock (model, tracker) pair for assign_model_with_tracking."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(
        return_value=MagicMock(tokens_in=0, tokens_out=0, success=True)
    )
    ctx.__exit__ = MagicMock(return_value=False)
    return ("gpt-4o-mini", ctx)


# ---------------------------------------------------------------------------
# 1. ChatRequest Validation
# ---------------------------------------------------------------------------


class TestChatRequestValidation:
    """Pydantic field constraints on ChatRequest."""

    def test_empty_messages_array(self, client):
        """messages=[] -- endpoint should handle gracefully (200 echo or 422)."""
        payload = {"messages": [], "model": "gpt-4o-mini"}
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code in (200, 422)

    def test_temperature_zero_accepted(self, client):
        """temperature=0.0 at the lower bound (ge=0.0) -- must succeed."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.0,
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200

    def test_temperature_two_accepted(self, client):
        """temperature=2.0 at the upper bound (le=2.0) -- must succeed."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 2.0,
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200

    def test_temperature_over_two_rejected(self, client):
        """temperature=2.1 exceeds le=2.0 -- Pydantic returns 422."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 2.1,
        }
        resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 422

    def test_max_tokens_one_accepted(self, client):
        """max_tokens=1 at the lower bound (ge=1) -- must succeed."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200

    def test_max_tokens_128000_accepted(self, client):
        """max_tokens=128000 at the upper bound (le=128000) -- must succeed."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 128000,
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200

    def test_max_tokens_zero_rejected(self, client):
        """max_tokens=0 violates ge=1 -- Pydantic returns 422."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 0,
        }
        resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 422

    def test_max_tokens_negative_rejected(self, client):
        """max_tokens=-1 violates ge=1 -- Pydantic returns 422."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": -1,
        }
        resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 422

    def test_content_over_100k_chars_rejected(self, client):
        """content with >100,000 chars exceeds ChatMessage max_length -- 422."""
        long_content = "x" * 100_001
        payload = {
            "messages": [{"role": "user", "content": long_content}],
        }
        resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /api/chat/providers/status
# ---------------------------------------------------------------------------


class TestProviderStatus:
    """Provider health-check endpoint."""

    def test_providers_status_returns_200(self, client):
        resp = client.get("/api/chat/providers/status")
        assert resp.status_code == 200

    def test_providers_status_has_provider_keys(self, client):
        data = client.get("/api/chat/providers/status").json()
        for key in ("anthropic", "openai", "google", "local"):
            assert key in data, f"Missing provider key: {key}"


# ---------------------------------------------------------------------------
# 3. GET / DELETE /api/chat/history -- nonexistent sessions
# ---------------------------------------------------------------------------


class TestHistoryEdgeCases:
    """Nonexistent session handling -- no 404, graceful empty responses."""

    def test_get_nonexistent_session_returns_empty_messages(self, client):
        resp = client.get("/api/chat/history/does-not-exist-9999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["session_id"] == "does-not-exist-9999"

    def test_delete_nonexistent_session_returns_deleted_true(self, client):
        resp = client.delete("/api/chat/history/does-not-exist-9999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["session_id"] == "does-not-exist-9999"


# ---------------------------------------------------------------------------
# 4. POST /api/chat/completions/stream -- format verification
# ---------------------------------------------------------------------------


class TestStreamingFormat:
    """Verify the streaming endpoint returns correct SSE content type and body."""

    def test_stream_content_type_is_event_stream(self, client):
        payload = {"messages": [{"role": "user", "content": "hello"}]}
        with patch("api.chat_routes.get_llm_for_chat", return_value=None):
            resp = client.post("/api/chat/completions/stream", json=payload)
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct

    def test_stream_body_contains_done_event(self, client):
        """The stream must end with a done:true SSE event."""
        payload = {"messages": [{"role": "user", "content": "hello stream"}]}
        with patch("api.chat_routes.get_llm_for_chat", return_value=None):
            resp = client.post("/api/chat/completions/stream", json=payload)
        body = resp.text
        assert '"done": true' in body or '"done":true' in body


# ---------------------------------------------------------------------------
# 5. Model selection on completions
# ---------------------------------------------------------------------------


class TestModelSelection:
    """Explicit model name handling in POST /api/chat/completions."""

    def test_explicit_valid_model_accepted(self, client):
        """When a known model is specified, dev-mode echo returns 200."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gpt-4o-mini",
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200

    def test_invalid_model_falls_back_gracefully(self, client):
        """An unrecognized model name should not 500 -- falls back to MODELS[0]."""
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "nonexistent-model-xyz",
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "api.chat_routes.assign_model_with_tracking", return_value=_tracker_ctx()
        ):
            resp = client.post("/api/chat/completions", json=payload)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. POST /api/chat/summarize -- edge cases
# ---------------------------------------------------------------------------


class TestSummarizeEdgeCases:
    """Edge cases for the summarize endpoint."""

    def test_summarize_already_summarized_skipped(self, client):
        """When DevMemory already has a summary for the session_id, return skipped."""
        mock_mem = MagicMock()
        mock_mem.query.return_value = [
            {
                "content": "Previous summary",
                "metadata": {"session_id": "dup-session"},
            }
        ]
        payload = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
            "session_id": "dup-session",
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "memory.dev_memory.get_dev_memory", return_value=mock_mem
        ):
            resp = client.post("/api/chat/summarize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("skipped") is True
        assert data.get("reason") == "already_summarized"

    def test_summarize_with_empty_agent_ids(self, client):
        """agent_ids=[] should still produce a summary (no per-agent entries)."""
        mock_mem = MagicMock()
        mock_mem.query.return_value = []
        mock_mem.add.return_value = True
        payload = {
            "messages": [
                {"role": "user", "content": "Tell me about testing."},
                {"role": "assistant", "content": "Testing is important."},
            ],
            "session_id": "empty-agents-session",
            "agent_ids": [],
            "agent_names": [],
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "memory.dev_memory.get_dev_memory", return_value=mock_mem
        ):
            resp = client.post("/api/chat/summarize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Should produce a summary, not skip
        assert "summary" in data

    def test_summarize_whitespace_only_messages_skipped(self, client):
        """Messages with only whitespace content are not meaningful -- should skip."""
        payload = {
            "messages": [
                {"role": "user", "content": "   "},
                {"role": "assistant", "content": "  "},
            ],
            "session_id": "ws-session",
        }
        resp = client.post("/api/chat/summarize", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("skipped") is True
        assert data.get("reason") == "too_few_messages"
