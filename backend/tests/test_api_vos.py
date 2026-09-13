"""
Tests for VOS API routes (/api/vos/*).

Covers all endpoints in api/vos_routes.py. The router has no external service
class; each handler calls _get_engine() directly. We patch that function at the
module level using monkeypatch so no real VOS engine is instantiated.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def mock_engine(monkeypatch):
    """Patch _get_engine in vos_routes so no real engine is created."""
    engine = MagicMock()

    # Async methods
    engine.classify_intent = AsyncMock(return_value="navigation")
    engine.execute = AsyncMock(
        return_value=MagicMock(
            intent="navigation",
            tool_calls=[{"tool": "navigate", "args": {"target": "/dashboard"}}],
            results=[{"success": True, "output": "Navigated"}],
            summary="Navigated to dashboard",
            source="chat",
        )
    )

    # Sync methods
    engine.get_recent_actions.return_value = [
        {"id": "act1", "intent": "navigation", "timestamp": "2024-01-01T00:00:00"}
    ]
    engine.get_action_stats.return_value = {
        "total": 50,
        "by_intent": {"navigation": 30, "code_generation": 20},
    }
    engine.get_available_tools.return_value = [
        {"name": "navigate", "description": "Navigate to a page", "category": "ui"}
    ]
    engine.registry = MagicMock()
    engine.registry.get_categories.return_value = ["ui", "code", "data"]

    monkeypatch.setattr("api.vos_routes._get_engine", lambda: engine)
    return engine


# =============================================================================
# POST /api/vos/classify
# =============================================================================


def test_classify_with_intent(client, mock_engine):
    resp = client.post("/api/vos/classify", json={"message": "go to dashboard"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "navigation"
    assert data["has_intent"] is True
    mock_engine.classify_intent.assert_called_once()


def test_classify_no_intent_returns_had_intent_false(client, mock_engine):
    mock_engine.classify_intent = AsyncMock(return_value=None)
    resp = client.post("/api/vos/classify", json={"message": "random noise"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] is None
    assert data["has_intent"] is False


def test_classify_missing_message_returns_422(client, mock_engine):
    resp = client.post("/api/vos/classify", json={})
    assert resp.status_code == 422


def test_classify_passes_message_to_engine(client, mock_engine):
    client.post("/api/vos/classify", json={"message": "open settings"})
    call_args = mock_engine.classify_intent.call_args
    assert call_args[0][0] == "open settings"


# =============================================================================
# POST /api/vos/execute
# =============================================================================


def test_execute_action_success(client, mock_engine):
    resp = client.post(
        "/api/vos/execute",
        json={"message": "go to dashboard", "intent": "navigation"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "navigation"
    assert isinstance(data["tool_calls"], list)
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["tool"] == "navigate"
    assert data["summary"] == "Navigated to dashboard"
    assert data["source"] == "chat"


def test_execute_action_results_present(client, mock_engine):
    resp = client.post(
        "/api/vos/execute",
        json={"message": "go to dashboard", "intent": "navigation"},
    )
    data = resp.json()
    assert isinstance(data["results"], list)
    assert data["results"][0]["success"] is True


def test_execute_action_missing_intent_returns_422(client, mock_engine):
    resp = client.post("/api/vos/execute", json={"message": "do something"})
    assert resp.status_code == 422


def test_execute_passes_intent_to_engine(client, mock_engine):
    client.post(
        "/api/vos/execute",
        json={"message": "go to dashboard", "intent": "navigation"},
    )
    call_kwargs = mock_engine.execute.call_args.kwargs
    assert call_kwargs["intent_category"] == "navigation"
    assert call_kwargs["message"] == "go to dashboard"


# =============================================================================
# POST /api/vos/process
# =============================================================================


def test_process_with_intent_found(client, mock_engine):
    resp = client.post("/api/vos/process", json={"message": "go to settings"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["had_intent"] is True
    assert data["pass_through"] is False
    assert data["intent"] == "navigation"
    assert "tool_calls" in data
    assert "results" in data
    assert "summary" in data


def test_process_no_intent_returns_pass_through(client, mock_engine):
    mock_engine.classify_intent = AsyncMock(return_value=None)
    resp = client.post("/api/vos/process", json={"message": "random noise"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pass_through"] is True
    assert data["had_intent"] is False
    assert data["intent"] is None
    assert data["results"] is None
    assert data["summary"] is None


def test_process_no_intent_does_not_call_execute(client, mock_engine):
    mock_engine.classify_intent = AsyncMock(return_value=None)
    client.post("/api/vos/process", json={"message": "gibberish"})
    mock_engine.execute.assert_not_called()


def test_process_with_intent_calls_execute(client, mock_engine):
    client.post("/api/vos/process", json={"message": "navigate to home"})
    mock_engine.execute.assert_called_once()


# =============================================================================
# GET /api/vos/actions
# =============================================================================


def test_get_recent_actions_default_limit(client, mock_engine):
    resp = client.get("/api/vos/actions")
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert "total" in data
    assert isinstance(data["actions"], list)
    assert data["total"] == 1


def test_get_recent_actions_custom_limit(client, mock_engine):
    resp = client.get("/api/vos/actions?limit=10")
    assert resp.status_code == 200
    # Verify limit was forwarded to engine
    mock_engine.get_recent_actions.assert_called_with(10)


def test_get_recent_actions_returns_action_fields(client, mock_engine):
    resp = client.get("/api/vos/actions")
    action = resp.json()["actions"][0]
    assert action["id"] == "act1"
    assert action["intent"] == "navigation"


# =============================================================================
# GET /api/vos/actions/stats
# =============================================================================


def test_get_action_stats(client, mock_engine):
    resp = client.get("/api/vos/actions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 50
    assert "by_intent" in data
    assert data["by_intent"]["navigation"] == 30
    mock_engine.get_action_stats.assert_called_once()


# =============================================================================
# GET /api/vos/tools
# =============================================================================


def test_list_tools(client, mock_engine):
    resp = client.get("/api/vos/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert "categories" in data
    assert "total" in data
    assert data["total"] == 1
    assert data["tools"][0]["name"] == "navigate"
    assert data["categories"] == ["ui", "code", "data"]
    mock_engine.get_available_tools.assert_called_once()
    mock_engine.registry.get_categories.assert_called_once()
