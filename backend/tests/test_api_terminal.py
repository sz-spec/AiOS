"""
Tests for Terminal API Routes
==============================

Covers: GET /api/terminal/models, GET /api/terminal/tools,
        GET /api/terminal/sessions/{id}, DELETE /api/terminal/sessions/{id},
        POST /api/terminal/chat

All endpoints require auth — tests mock get_current_user.
The /tools/execute endpoint and CLI mode have been removed.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

# ---------------------------------------------------------------------------
# Mock auth helper
# ---------------------------------------------------------------------------


def _mock_user():
    """Return a mock AuthenticatedUser for testing."""
    from middleware.auth import AuthenticatedUser

    return AuthenticatedUser(
        id="user_test",
        email="test@example.com",
        permissions=["read", "write"],
    )


@pytest.fixture(autouse=True)
def mock_auth():
    """Mock authentication for all terminal tests."""
    with patch("api.terminal_routes.get_current_user", return_value=_mock_user()):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient for terminal endpoints."""
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear terminal sessions between tests."""
    try:
        from api.terminal_routes import sessions

        sessions.clear()
    except ImportError:
        pass
    yield
    try:
        from api.terminal_routes import sessions

        sessions.clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# GET /api/terminal/models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_list_models_returns_200(self, client):
        response = client.get("/api/terminal/models")
        assert response.status_code == 200

    def test_list_models_contains_models_key(self, client):
        data = client.get("/api/terminal/models").json()
        assert "models" in data

    def test_list_models_contains_default_key(self, client):
        data = client.get("/api/terminal/models").json()
        assert "default" in data

    def test_list_models_nonempty(self, client):
        data = client.get("/api/terminal/models").json()
        assert len(data["models"]) > 0

    def test_list_models_each_has_key_and_id(self, client):
        data = client.get("/api/terminal/models").json()
        for model in data["models"]:
            assert "key" in model
            assert "id" in model
            assert "name" in model


# ---------------------------------------------------------------------------
# GET /api/terminal/tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_list_tools_returns_200(self, client):
        response = client.get("/api/terminal/tools")
        assert response.status_code == 200

    def test_list_tools_contains_tools_key(self, client):
        data = client.get("/api/terminal/tools").json()
        assert "tools" in data

    def test_list_tools_contains_count(self, client):
        data = client.get("/api/terminal/tools").json()
        assert "count" in data
        assert data["count"] == len(data["tools"])


# ---------------------------------------------------------------------------
# GET /api/terminal/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestGetSession:
    def test_get_nonexistent_session_returns_empty(self, client):
        response = client.get("/api/terminal/sessions/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["messages"] == []
        assert data["session_id"] == "nonexistent"


# ---------------------------------------------------------------------------
# DELETE /api/terminal/sessions/{session_id}
# ---------------------------------------------------------------------------


class TestClearSession:
    def test_clear_session_returns_success(self, client):
        response = client.delete("/api/terminal/sessions/test-session")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_clear_nonexistent_session_still_succeeds(self, client):
        response = client.delete("/api/terminal/sessions/does-not-exist")
        assert response.status_code == 200
        assert response.json()["success"] is True


# ---------------------------------------------------------------------------
# POST /api/terminal/tools/execute — REMOVED (should 404/405)
# ---------------------------------------------------------------------------


class TestToolExecuteRemoved:
    def test_tools_execute_endpoint_removed(self, client):
        response = client.post(
            "/api/terminal/tools/execute?tool_name=list_files", json={"path": "."}
        )
        # Should no longer exist — 404 or 405
        assert response.status_code in (404, 405)


# ---------------------------------------------------------------------------
# POST /api/terminal/chat — API mode
# ---------------------------------------------------------------------------


class TestChatAPI:
    def test_api_mode_no_api_key_returns_500(self, client):
        """Without ANTHROPIC_API_KEY set, should get 500."""
        with patch.dict(os.environ, {}, clear=True):
            response = client.post(
                "/api/terminal/chat",
                json={
                    "message": "hello",
                },
            )
        assert response.status_code == 500

    def test_api_mode_with_mock_client_returns_200(self, client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "I can help with that."
        mock_response.content = [text_block]
        mock_client.messages.create.return_value = mock_response

        with patch(
            "api.terminal_routes.get_anthropic_client", return_value=mock_client
        ), patch("api.terminal_routes.get_file_tools") as mock_get_ft:
            mock_ft = MagicMock()
            mock_get_ft.return_value = mock_ft
            response = client.post(
                "/api/terminal/chat",
                json={
                    "message": "hello",
                    "model": "claude-sonnet-4",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert data["mode"] == "api"

    def test_api_mode_session_persistence(self, client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Response 1"
        mock_response.content = [text_block]
        mock_client.messages.create.return_value = mock_response

        with patch(
            "api.terminal_routes.get_anthropic_client", return_value=mock_client
        ), patch("api.terminal_routes.get_file_tools") as mock_get_ft:
            mock_get_ft.return_value = MagicMock()
            # First message
            client.post(
                "/api/terminal/chat",
                json={
                    "message": "first",
                    "session_id": "persist-test",
                },
            )
            # Check session has messages
            resp = client.get("/api/terminal/sessions/persist-test")
        data = resp.json()
        assert len(data["messages"]) == 2  # user + assistant

    def test_api_mode_model_selection(self, client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "ok"
        mock_response.content = [text_block]
        mock_client.messages.create.return_value = mock_response

        with patch(
            "api.terminal_routes.get_anthropic_client", return_value=mock_client
        ), patch("api.terminal_routes.get_file_tools") as mock_get_ft:
            mock_get_ft.return_value = MagicMock()
            response = client.post(
                "/api/terminal/chat",
                json={
                    "message": "hi",
                    "model": "claude-opus-4",
                },
            )
        data = response.json()
        assert data["model"] == "claude-opus-4"
        assert mock_client.messages.create.call_args.kwargs["model"] == data["model"]
