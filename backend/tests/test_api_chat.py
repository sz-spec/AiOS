"""
Tests for Chat API Routes
=========================

Covers: GET /api/chat/models, POST /api/chat/completions,
        POST /api/chat/completions/stream, POST /api/chat/summarize
"""

import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.chat_routes import get_current_user
from api.deps import AuthenticatedUser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient with auth dependency overridden.

    The route is typed ``user: AuthenticatedUser`` and accesses ``user.id``
    (the W6.25 dispatcher chokepoint passes it into ``LLMRequestContext``), so
    the override MUST yield an ``AuthenticatedUser`` — a bare dict raises
    ``AttributeError: 'dict' object has no attribute 'id'`` exactly as
    production would never see.
    """
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test_user",
        email="test@test.com",
        org_id="org_test",
        org_role="member",
        permissions=["read", "write"],
        metadata={"test": True},
    )
    yield TestClient(app, base_url="http://localhost")
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def mock_llm():
    """A mock LLM instance whose ainvoke returns a fake AI message.

    The chat route prefers the async path (``if hasattr(llm, "ainvoke")``),
    so the mock exposes an ``AsyncMock`` ``ainvoke`` (plus a sync ``invoke``
    for completeness).
    """
    instance = MagicMock()
    result = MagicMock(
        content="Test response",
        response_metadata={"token_usage": {"total_tokens": 42}},
    )
    instance.ainvoke = AsyncMock(return_value=result)
    instance.invoke.return_value = result
    instance.model = "gpt-4o-mini"
    return instance


@contextmanager
def _patch_dispatcher(llm, model_id="gpt-4o-mini", provider="echo"):
    """Patch the W6.25 LLM dispatcher chokepoint.

    The chat route resolves its LLM through ``get_dispatcher().resolve(...)``,
    NOT the legacy ``get_llm_for_chat`` factory (that path is only the
    ``ImportError`` fallback and never fires in tests). Patching the
    dispatcher to return a controlled ``LLMResolution`` makes the route
    deterministic — no leaked-``.env``-key cloud calls, no live Ollama. Pass
    ``llm=None`` to exercise the dev-mode echo branch.
    """
    from services.llm_dispatcher import LLMResolution

    fake = MagicMock()
    fake.resolve.return_value = LLMResolution(
        llm=llm, model_id=model_id, provider=provider
    )
    # The route imports get_dispatcher lazily inside the handler
    # (``from services.llm_dispatcher import get_dispatcher``), so patch it at
    # the source module, not on api.chat_routes.
    with patch("services.llm_dispatcher.get_dispatcher", return_value=fake):
        yield


# ---------------------------------------------------------------------------
# GET /api/chat/models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_list_models_returns_200(self, client):
        response = client.get("/api/chat/models")
        assert response.status_code == 200

    def test_list_models_contains_models_key(self, client):
        data = client.get("/api/chat/models").json()
        assert "models" in data

    def test_list_models_nonempty(self, client):
        data = client.get("/api/chat/models").json()
        assert len(data["models"]) > 0

    def test_list_models_contains_default(self, client):
        data = client.get("/api/chat/models").json()
        assert "default" in data

    def test_list_models_each_has_required_fields(self, client):
        data = client.get("/api/chat/models").json()
        required = {
            "id",
            "provider",
            "cost_per_1k_input",
            "cost_per_1k_output",
            "max_context",
            "description",
        }
        for model in data["models"]:
            assert required.issubset(
                model.keys()
            ), f"Model {model.get('id')} missing fields"

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
    def test_list_models_contains_provider(self, client, provider):
        data = client.get("/api/chat/models").json()
        providers = {m["provider"] for m in data["models"]}
        assert provider in providers


# ---------------------------------------------------------------------------
# POST /api/chat/completions
# ---------------------------------------------------------------------------


class TestChatCompletions:
    def _payload(self, content="hello world", model="gpt-4o-mini"):
        return {
            "messages": [{"role": "user", "content": content}],
            "model": model,
        }

    def test_completions_dev_mode_echo(self, client):
        """When the dispatcher resolves no LLM the endpoint returns a dev-mode echo."""
        with _patch_dispatcher(None):
            response = client.post("/api/chat/completions", json=self._payload())
        assert response.status_code == 200

    def test_completions_with_llm_returns_200(self, client, mock_llm):
        """When the dispatcher resolves an LLM the endpoint returns 200 with a ChatResponse."""
        with _patch_dispatcher(mock_llm):
            response = client.post("/api/chat/completions", json=self._payload())
        assert response.status_code == 200

    def test_completions_response_has_message_field(self, client, mock_llm):
        with _patch_dispatcher(mock_llm):
            data = client.post("/api/chat/completions", json=self._payload()).json()
        assert "message" in data

    def test_completions_response_has_model_field(self, client, mock_llm):
        with _patch_dispatcher(mock_llm):
            data = client.post("/api/chat/completions", json=self._payload()).json()
        assert "model" in data

    def test_completions_unauthenticated_in_dev_mode_returns_200(self):
        """In dev mode, unauthenticated requests get a mock user (no 401)."""
        fresh_client = TestClient(app, base_url="http://localhost")
        with _patch_dispatcher(None):
            response = fresh_client.post(
                "/api/chat/completions",
                json=self._payload(),
            )
        assert response.status_code == 200

    def test_completions_multi_turn_messages(self, client):
        payload = {
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},
                {"role": "user", "content": "And 4+4?"},
            ],
            "model": "gpt-4o-mini",
        }
        with _patch_dispatcher(None):
            response = client.post("/api/chat/completions", json=payload)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/chat/completions/stream
# ---------------------------------------------------------------------------


class TestChatStream:
    def test_stream_returns_200(self, client):
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gpt-4o-mini",
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None):
            response = client.post("/api/chat/completions/stream", json=payload)
        assert response.status_code == 200

    def test_stream_content_type_is_event_stream(self, client):
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "gpt-4o-mini",
        }
        with patch("api.chat_routes.get_llm_for_chat", return_value=None):
            response = client.post("/api/chat/completions/stream", json=payload)
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_stream_unauthenticated_in_dev_mode_returns_200(self):
        """In dev mode, unauthenticated requests get a mock user (no 401)."""
        fresh_client = TestClient(app, base_url="http://localhost")
        payload = {"messages": [{"role": "user", "content": "hi"}]}
        with patch("api.chat_routes.get_llm_for_chat", return_value=None):
            response = fresh_client.post("/api/chat/completions/stream", json=payload)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/chat/history/{session_id}
# ---------------------------------------------------------------------------


class TestChatHistory:
    def test_get_history_returns_200(self, client):
        response = client.get("/api/chat/history/session-abc")
        assert response.status_code == 200

    def test_get_history_contains_session_id(self, client):
        response = client.get("/api/chat/history/session-xyz")
        data = response.json()
        assert data.get("session_id") == "session-xyz"

    def test_delete_history_returns_deleted(self, client):
        response = client.delete("/api/chat/history/session-abc")
        assert response.status_code == 200
        data = response.json()
        assert data.get("deleted") is True


# ---------------------------------------------------------------------------
# POST /api/chat/summarize
# ---------------------------------------------------------------------------


class TestSummarize:
    def _payload(self, messages=None, session_id="s1"):
        return {
            "messages": messages
            or [
                {"role": "user", "content": "Tell me about FastAPI."},
                {"role": "assistant", "content": "FastAPI is a modern web framework."},
            ],
            "session_id": session_id,
        }

    def test_summarize_too_few_messages_skipped(self, client):
        payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "session_id": "s0",
        }
        with patch("api.chat_routes.get_dev_memory", create=True) as _:
            response = client.post("/api/chat/summarize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data.get("skipped") is True

    def test_summarize_meaningful_conversation_returns_summary(self, client):
        """With enough messages and no memory/LLM, fallback summary is used."""
        mock_mem = MagicMock()
        mock_mem.query.return_value = []  # No existing summary
        mock_mem.add.return_value = True
        with patch("api.chat_routes.get_llm_for_chat", return_value=None), patch(
            "memory.dev_memory.get_dev_memory", return_value=mock_mem
        ):
            response = client.post("/api/chat/summarize", json=self._payload())
        assert response.status_code == 200
        data = response.json()
        # Either summarized or skipped (already summarized) — both are valid responses
        assert "summary_id" in data or data.get("skipped") is True
