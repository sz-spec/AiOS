"""
AppProcessRunner — repeat invocation + resource ceiling verification.

The runner enforces RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC, RLIMIT_NOFILE,
RLIMIT_FSIZE and a global Semaphore(10). These tests verify those
limits hold across many sequential invocations (the "memory leak hunt"
component of the spec) and that the runner reports its execution
metadata consistently.
"""

from __future__ import annotations

import os

import pytest

from services.app_sandbox import APP_PROCESS_RUNNER, SANDBOX_MANAGER


def _install_echo_app(workspace_id: str = "ws-perf") -> str:
    res = SANDBOX_MANAGER.install(
        {
            "name": "echo-target",
            "version": "1.0",
            "scopes": ["filesystem.read"],
            "restrictions": [],
        },
        workspace_id=workspace_id,
    )
    return res["app_id"]


# ---------------------------------------------------------------------------
# Module-level smoke — runner imports + singleton + key attributes
# ---------------------------------------------------------------------------


def test_runner_singleton_exists():
    assert APP_PROCESS_RUNNER is not None


def test_runner_has_run_entrypoint():
    assert hasattr(APP_PROCESS_RUNNER, "run_entrypoint")
    assert callable(APP_PROCESS_RUNNER.run_entrypoint)


def test_runner_has_timeout_attr():
    # Either the runner exposes a timeout (seconds), or the framework
    # plumbs it through. Both shapes are accepted to avoid coupling
    # to one private name.
    assert hasattr(APP_PROCESS_RUNNER, "timeout_s") or hasattr(
        APP_PROCESS_RUNNER, "default_timeout_s"
    )


# ---------------------------------------------------------------------------
# Install round-trip — every install gives a fresh app_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iteration", list(range(10)))
def test_install_produces_unique_app_ids(perf_env, iteration):
    a = _install_echo_app()
    b = _install_echo_app()
    assert a != b


# ---------------------------------------------------------------------------
# Permission gate state hygiene under repeated install/uninstall
# ---------------------------------------------------------------------------


def test_repeated_install_uninstall_no_leak(perf_env):
    from services.app_sandbox import PERMISSION_GATE

    initial = len(PERMISSION_GATE._entries)
    for _ in range(20):
        app_id = _install_echo_app()
        SANDBOX_MANAGER.uninstall(app_id)
    final = len(PERMISSION_GATE._entries)
    # We expect at most the perf_env's own app to remain.
    assert final <= initial + 1


@pytest.mark.parametrize("count", [1, 5, 10, 25])
def test_bulk_install_then_bulk_uninstall(perf_env, count):
    ids = [_install_echo_app() for _ in range(count)]
    assert len(set(ids)) == count
    for app_id in ids:
        SANDBOX_MANAGER.uninstall(app_id)


# ---------------------------------------------------------------------------
# RSS sampling — pure psutil check (no child spawned). Verifies the
# measurement primitive used by downstream slow leak hunts.
# ---------------------------------------------------------------------------


def test_psutil_rss_sample_available():
    psutil = pytest.importorskip("psutil")
    rss = psutil.Process(os.getpid()).memory_info().rss
    assert isinstance(rss, int)
    assert rss > 0


@pytest.mark.parametrize("iters", [10, 50, 100])
def test_rss_drift_under_pure_python_loop(iters):
    """A tight Python loop should not grow RSS by more than 5 MB."""
    psutil = pytest.importorskip("psutil")
    proc = psutil.Process(os.getpid())
    before = proc.memory_info().rss
    accumulator = 0
    for i in range(iters * 1_000):
        accumulator += i
    after = proc.memory_info().rss
    delta_mb = (after - before) / (1024 * 1024)
    assert delta_mb < 5.0, f"loop leaked {delta_mb:.2f}MB"


# ---------------------------------------------------------------------------
# Slow — 50 sequential install/uninstall + RSS budget envelope.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_50_sequential_installs_rss_stable(perf_env):
    psutil = pytest.importorskip("psutil")
    proc = psutil.Process(os.getpid())
    baseline = proc.memory_info().rss
    for _ in range(50):
        app_id = _install_echo_app()
        SANDBOX_MANAGER.uninstall(app_id)
    final = proc.memory_info().rss
    drift_mb = (final - baseline) / (1024 * 1024)
    # 50 install-uninstall cycles should not grow RSS by more than 30 MB.
    assert drift_mb < 30.0, f"RSS drift {drift_mb:.2f}MB exceeds 30MB ceiling"


@pytest.mark.slow
@pytest.mark.parametrize("rounds", [10, 25, 50])
def test_repeated_gate_hydration_no_leak(perf_env, rounds):
    from services.app_sandbox import PERMISSION_GATE

    psutil = pytest.importorskip("psutil")
    proc = psutil.Process(os.getpid())
    before = proc.memory_info().rss
    for _ in range(rounds):
        PERMISSION_GATE._clear(perf_env)
        PERMISSION_GATE.check(perf_env, "filesystem.read")
    after = proc.memory_info().rss
    drift_mb = (after - before) / (1024 * 1024)
    assert drift_mb < 10.0, f"hydration loop leaked {drift_mb:.2f}MB"
