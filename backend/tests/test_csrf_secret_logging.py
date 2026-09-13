"""Independent regression checks for secret resolution, without exposing a secret."""
import logging

import pytest

from middleware import csrf


def test_production_without_explicit_handshake_secret_fails(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("VOS3_TAURI_IPC_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="must be set in production"):
        csrf._resolve_handshake_secret()


def test_generated_development_secret_is_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("VOS3_TAURI_IPC_SECRET", raising=False)
    sentinel = "unit-test-only-generated-secret-sentinel"
    monkeypatch.setattr(csrf.secrets, "token_urlsafe", lambda length: sentinel)
    with caplog.at_level(logging.WARNING, logger="middleware.csrf"):
        assert csrf._resolve_handshake_secret() == sentinel
    assert "ephemeral development secret" in caplog.text
    assert sentinel not in caplog.text


def test_explicit_production_secret_is_preserved_without_logging(monkeypatch, caplog):
    monkeypatch.setenv("ENVIRONMENT", "production")
    sentinel = "unit-test-only-explicit-secret-sentinel"
    monkeypatch.setenv("VOS3_TAURI_IPC_SECRET", sentinel)
    assert csrf._resolve_handshake_secret() == sentinel
    assert sentinel not in caplog.text
