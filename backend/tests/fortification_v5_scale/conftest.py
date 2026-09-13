"""
Fortification-v5 (Scalability) — calibrated scale tests.

Honest-scope ceiling
--------------------
The 2026-05-17 brief asked for 10M-node / 200k-QPS verification.
A pytest run on a single host cannot honestly verify that scale.

This suite implements **predictive failure analysis** instead:
  * Measure operation timing at three or four scale points
    (100 / 1,000 / 10,000 / sometimes 100,000).
  * Fit a least-squares regression line to (size, time) data.
  * Assert the SLOPE is below a documented threshold — an O(1) or
    O(log N) op has slope ~0; an O(N) regression shows positive
    slope proportional to the per-item cost.
  * Document the slope-extrapolated 10M-node figure in test output;
    treat it as a HYPOTHESIS, not a proof.

Production scale verification requires actual load testing on
representative infrastructure with infrastructure metrics
(p99 latency, error rates, queue depths). These pytest tests catch
algorithmic regressions; they do not certify deployment readiness.
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
def v5_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "v5-test-seed")
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
        from core.security.rotation_manager import _reset_for_tests as _rot_reset

        _rot_reset()
    except Exception:
        pass
    yield tmp_path


def _linear_regression_slope(xs: list, ys: list) -> float:
    """Least-squares slope of (xs, ys). Used by predictive failure
    analysis to catch O(N) regressions in nominally O(1) / O(log N) ops."""
    if len(xs) < 2:
        return 0.0
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


@pytest.fixture
def regression_slope():
    """Expose the slope-fitter to tests as a fixture."""
    return _linear_regression_slope
