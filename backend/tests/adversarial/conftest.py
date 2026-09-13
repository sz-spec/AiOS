"""
backend/tests/adversarial/conftest.py — fixtures for Stage-4 adversarial tests.

Same shadow-fixture pattern as tests/unit/conftest.py + tests/stress/conftest.py.
The `adv_env` fixture installs a freshly-isolated keyring + SQLite +
sandbox root + a single test app so individual cases can exercise
the gate without setup boilerplate.
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
def adv_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "adv-test-seed")
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
    from services.app_sandbox import _reset_gate_for_tests, SANDBOX_MANAGER

    _reset_gate_for_tests()
    res = SANDBOX_MANAGER.install(
        {
            "name": "adv",
            "version": "1.0",
            "scopes": ["filesystem.read", "filesystem.write"],
        },
        workspace_id="ws-adv",
    )
    yield res["app_id"]
