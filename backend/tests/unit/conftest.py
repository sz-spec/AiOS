"""
backend/tests/unit/conftest.py — pytest fixtures for the unit suite.

The unit suite tests services + core modules in isolation — no
FastAPI app, no full app factory, no service-dict bootstrap. The
parent `tests/conftest.py` defines session-scoped autouse fixtures
that pull in `main.py` (and through it, Python 3.10+ syntax that
trips on the 3.9 system Python). We shadow those autouse fixtures
with no-ops so the unit suite stays focused on pure-function /
isolated-class testing.

Convention:
  - One test file per service / core module being audited.
  - Each test asserts a single behavior in 1-5 lines.
  - No network, no subprocess, no real DB unless the test owns its
    setUp/tearDown for an in-memory or tmp_path SQLite.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# Ensure `backend/` is importable.
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# Shadow parent autouse fixtures that import `main.app`.
@pytest.fixture(autouse=True)
def reset_services():
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_dev_memory():
    yield


# Generic unit-test sandbox env. Pins keyring to local fallback +
# tmp paths so a test that touches the keyring never reaches the
# real macOS Keychain.
@pytest.fixture
def unit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv(
        "VOS3_KEYRING_PATH",
        str(tmp_path / "secrets.enc"),
    )
    monkeypatch.setenv(
        "VOS3_KEYRING_SEED_OVERRIDE",
        "unit-test-seed",
    )
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", str(tmp_path / "vos3.db"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Reset module-level singletons that other suites may have warmed.
    try:
        from services.crypto_keyring import KEYRING

        KEYRING._reset_for_tests()
    except Exception:
        pass
    try:
        from core.database.sqlite_setup import _reset_for_tests as _rs

        _rs()
    except Exception:
        pass
    yield tmp_path
