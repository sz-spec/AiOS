"""
Phase 3 — Data Governance & Integrity.

Shared fixtures for SQLCipher / sync conflict / atomic uninstall /
intent-manifest tests. The `gov_env` fixture mirrors the adv_env /
perms_env pattern from sibling phases.
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
def gov_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "gov-test-seed")
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", str(tmp_path / "vos3.db"))
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Defensive: ensure non-fortress unless a test explicitly opts in.
    # `vos_profile` caches the active profile in a module global; re-read it
    # from env so monkeypatch.setenv from a prior test in the same xdist
    # worker is invalidated. Use vos_profile.reload_for_test() (the provided
    # re-read hook) NOT importlib.reload — reloading the module recreates the
    # `Profile` enum class, which breaks `is Profile.X` identity checks in any
    # test that imported `Profile` earlier (e.g. test_profile_dispatch).
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    import sys as _sys

    if "vos_profile" in _sys.modules:
        _sys.modules["vos_profile"].reload_for_test()
    try:
        from services.crypto_keyring import KEYRING

        KEYRING._reset_for_tests()
    except Exception:
        pass
    from core.database.sqlite_setup import _reset_for_tests, init_db

    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield tmp_path
