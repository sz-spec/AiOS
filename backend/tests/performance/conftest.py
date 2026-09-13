"""
Phase 1 — Performance & Scalability Stress.

Heavy load tests (≥1k iterations, multi-thread, large rowsets) carry
`@pytest.mark.slow` so the default `pytest -m "not slow"` invocation
stays interactive. The shared `perf_env` fixture mirrors
tests/adversarial/conftest.py: a tmp-scoped keyring + SQLite + sandbox
root so every test starts from a clean slate.
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
def perf_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "perf-test-seed")
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
            "name": "perf-app",
            "version": "1.0",
            "scopes": [
                "filesystem.read",
                "filesystem.write",
                "network.outbound",
                "llm.local",
                "llm.cloud",
            ],
            "restrictions": [],
        },
        workspace_id="ws-perf",
    )
    yield res["app_id"]
