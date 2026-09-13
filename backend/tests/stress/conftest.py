"""
backend/tests/stress/conftest.py — fixtures for the P2P stress suite.

Shadows parent autouse fixtures so the stress suite avoids loading
the full FastAPI factory (same trick as tests/unit/conftest.py).
Adds `stress_env` which adds a tmp-path-isolated keyring + SQLite
init so 50-node beacon storms don't trample the dev workstation DB.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


@pytest.fixture(autouse=True)
def reset_services():
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_dev_memory():
    yield


@pytest.fixture
def stress_env(monkeypatch, tmp_path):
    """Isolated keyring + SQLite + sandbox env for stress runs."""
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "stress-test-seed")
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", str(tmp_path / "vos3.db"))
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    try:
        from services.crypto_keyring import KEYRING

        KEYRING._reset_for_tests()
    except Exception:
        pass
    from core.database.sqlite_setup import _reset_for_tests, init_db

    _reset_for_tests()
    init_db()
    try:
        from services.app_sandbox import _reset_gate_for_tests

        _reset_gate_for_tests()
    except Exception:
        pass
    yield tmp_path
