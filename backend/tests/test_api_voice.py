"""
API tests for voice_routes — voice control and hands-free interaction.

Router:  api/voice_routes.py  (prefix "/voice", mounted at "/api")
Service: VoiceService (voice.voice_service)
         injected via Depends(get_voice_service).

The dependency is overridden via app.dependency_overrides.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from voice.voice_service import get_voice_service

# =============================================================================
# Helpers
# =============================================================================


def _make_command_obj(**overrides):
    """Build a MagicMock that looks like a VoiceCommand domain object."""
    cmd = MagicMock()
    cmd.id = overrides.get("id", "cmd_1")
    cmd.transcript = overrides.get("transcript", "go to dashboard")
    cmd.confidence = overrides.get("confidence", 0.95)
    # language and category need .value (they're enums in the real service)
    lang = MagicMock()
    lang.value = overrides.get("language", "en")
    cmd.language = lang
    cat = MagicMock()
    cat.value = overrides.get("category", "navigation")
    cmd.category = cat
    cmd.intent = overrides.get("intent", "navigate")
    cmd.entities = overrides.get("entities", {})
    cmd.action = overrides.get("action", "/dashboard")
    cmd.response_text = overrides.get("response_text", "Navigating to dashboard")
    cmd.processing_time_ms = overrides.get("processing_time_ms", 42)
    cmd.to_dict = MagicMock(
        return_value={
            "id": cmd.id,
            "transcript": cmd.transcript,
            "confidence": cmd.confidence,
            "intent": cmd.intent,
        }
    )
    return cmd


def _make_settings_obj(**overrides):
    """Build a MagicMock that looks like a VoiceSettings domain object."""
    settings = MagicMock()
    data = {
        "user_id": "user_123",
        "input_language": "en-US",
        "output_language": "en-US",
        "wake_word_enabled": False,
        "wake_word": "hey vos",
        "continuous_listening": False,
        "voice_gender": "neutral",
        "speech_rate": 1.0,
        "pitch": 1.0,
        "volume": 1.0,
        "read_notifications": True,
        "morning_briefing": True,
        "voice_confirmations": True,
    }
    data.update(overrides)
    settings.to_dict.return_value = data
    for k, v in data.items():
        setattr(settings, k, v)
    return settings


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _override_user():
    """Override get_current_user so user.id == 'user_123' (not dev_seed_user)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_123", email="test@test.com"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_voice():
    svc = MagicMock()
    settings_obj = _make_settings_obj()
    command_obj = _make_command_obj()

    svc.process_voice_input = AsyncMock(return_value=command_obj)
    svc.get_settings = MagicMock(return_value=settings_obj)
    svc.update_settings = MagicMock(return_value=settings_obj)
    svc.generate_morning_briefing = AsyncMock(
        return_value="Good morning! Here is your briefing."
    )
    svc.get_tts_config = MagicMock(
        return_value={
            "provider": "browser",
            "voice": "en-US-default",
            "rate": 1.0,
        }
    )
    svc.get_available_commands = MagicMock(
        return_value=[
            {
                "intent": "navigate",
                "description": "Navigate to a page",
                "examples": ["go to dashboard"],
            },
        ]
    )
    svc.get_command_history = MagicMock(return_value=[])
    svc.get_popular_commands = MagicMock(return_value=[])

    app.dependency_overrides[get_voice_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_voice_service, None)


# =============================================================================
# Voice processing
# =============================================================================


class TestProcessVoice:
    def test_process_command_success(self, client, mock_voice):
        resp = client.post(
            "/api/voice/process",
            json={
                "transcript": "go to dashboard",
                "confidence": 0.9,
                "language": "en-US",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "cmd_1"
        assert body["transcript"] == "go to dashboard"
        assert body["confidence"] == 0.95
        assert body["intent"] == "navigate"
        assert body["response_text"] == "Navigating to dashboard"

    def test_process_command_calls_service(self, client, mock_voice):
        client.post(
            "/api/voice/process",
            json={"transcript": "open settings", "confidence": 0.8, "language": "en"},
        )
        mock_voice.process_voice_input.assert_called_once()
        kwargs = mock_voice.process_voice_input.call_args[1]
        assert kwargs["transcript"] == "open settings"
        assert kwargs["confidence"] == 0.8
        assert kwargs["user_id"] == "user_123"

    def test_process_command_missing_transcript(self, client, mock_voice):
        resp = client.post(
            "/api/voice/process",
            json={"confidence": 0.9},
        )
        assert resp.status_code == 422

    def test_process_command_default_confidence(self, client, mock_voice):
        """Confidence is optional; route should use default 1.0."""
        resp = client.post(
            "/api/voice/process",
            json={"transcript": "go home"},
        )
        assert resp.status_code == 200

    def test_process_command_response_shape(self, client, mock_voice):
        resp = client.post(
            "/api/voice/process",
            json={"transcript": "anything"},
        )
        body = resp.json()
        for field in (
            "id",
            "transcript",
            "confidence",
            "language",
            "category",
            "intent",
            "entities",
            "action",
            "response_text",
            "processing_time_ms",
        ):
            assert field in body, f"Missing field: {field}"


# =============================================================================
# Settings
# =============================================================================


class TestVoiceSettings:
    def test_get_settings(self, client, mock_voice):
        resp = client.get("/api/voice/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "user_123"
        assert body["input_language"] == "en-US"

    def test_get_settings_calls_service(self, client, mock_voice):
        client.get("/api/voice/settings")
        mock_voice.get_settings.assert_called_once_with("user_123")

    def test_update_settings(self, client, mock_voice):
        resp = client.patch(
            "/api/voice/settings",
            json={"input_language": "es-ES"},
        )
        assert resp.status_code == 200

    def test_update_settings_calls_service_with_partial(self, client, mock_voice):
        client.patch(
            "/api/voice/settings",
            json={"speech_rate": 1.5, "volume": 0.8},
        )
        mock_voice.update_settings.assert_called_once()
        _, updates = mock_voice.update_settings.call_args[0]
        assert "speech_rate" in updates
        assert "volume" in updates

    def test_update_settings_empty_body(self, client, mock_voice):
        """Empty patch is valid — no fields to update."""
        resp = client.patch("/api/voice/settings", json={})
        assert resp.status_code == 200


# =============================================================================
# Morning briefing
# =============================================================================


class TestMorningBriefing:
    def test_get_briefing(self, client, mock_voice):
        resp = client.get("/api/voice/briefing")
        assert resp.status_code == 200
        body = resp.json()
        assert "briefing" in body
        assert "tts_config" in body

    def test_briefing_calls_service(self, client, mock_voice):
        client.get("/api/voice/briefing")
        mock_voice.generate_morning_briefing.assert_called_once_with("user_123")
        mock_voice.get_tts_config.assert_called_with("user_123")

    def test_briefing_returns_tts_config(self, client, mock_voice):
        resp = client.get("/api/voice/briefing")
        tts = resp.json()["tts_config"]
        assert tts["provider"] == "browser"
        assert tts["rate"] == 1.0


# =============================================================================
# TTS config
# =============================================================================


class TestTTSConfig:
    def test_get_tts_config(self, client, mock_voice):
        resp = client.get("/api/voice/tts-config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "browser"
        assert body["voice"] == "en-US-default"
        assert body["rate"] == 1.0

    def test_tts_config_calls_service(self, client, mock_voice):
        client.get("/api/voice/tts-config")
        mock_voice.get_tts_config.assert_called_once_with("user_123")


# =============================================================================
# Commands
# =============================================================================


class TestVoiceCommands:
    def test_get_available_commands(self, client, mock_voice):
        resp = client.get("/api/voice/commands")
        assert resp.status_code == 200
        body = resp.json()
        assert "commands" in body
        assert isinstance(body["commands"], list)
        assert body["commands"][0]["intent"] == "navigate"

    def test_get_command_history(self, client, mock_voice):
        resp = client.get("/api/voice/history?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert "history" in body
        assert isinstance(body["history"], list)

    def test_get_command_history_calls_limit(self, client, mock_voice):
        client.get("/api/voice/history?limit=25")
        mock_voice.get_command_history.assert_called_once_with("user_123", 25)

    def test_get_popular_commands(self, client, mock_voice):
        resp = client.get("/api/voice/popular")
        assert resp.status_code == 200
        body = resp.json()
        assert "popular" in body
        assert isinstance(body["popular"], list)

    def test_get_command_history_default_limit(self, client, mock_voice):
        client.get("/api/voice/history")
        mock_voice.get_command_history.assert_called_once_with("user_123", 50)


# =============================================================================
# Languages
# =============================================================================


class TestVoiceLanguages:
    def test_get_supported_languages(self, client, mock_voice):
        resp = client.get("/api/voice/languages")
        assert resp.status_code == 200
        body = resp.json()
        assert "languages" in body
        assert isinstance(body["languages"], list)


# =============================================================================
# Parametrized: all voice GET endpoints are reachable
# =============================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/api/voice/settings",
        "/api/voice/briefing",
        "/api/voice/tts-config",
        "/api/voice/commands",
        "/api/voice/history",
        "/api/voice/popular",
        "/api/voice/languages",
    ],
)
def test_get_endpoints_reachable(client, mock_voice, path):
    resp = client.get(path)
    assert (
        resp.status_code == 200
    ), f"GET {path} returned {resp.status_code}: {resp.text}"
