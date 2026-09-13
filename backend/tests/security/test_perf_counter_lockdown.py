"""
backend/tests/security/test_perf_counter_lockdown.py

Sprint 19 / Wave 1 / Item E6 — perf-counter exposure lockdown.

Covers (via injected fake /proc root so tests run on any host):
  - LOCKED: paranoid>=2 + no NVIDIA → safe
  - LOCKED: paranoid>=2 + NVIDIA admin-only → safe
  - CPU_EXPOSED: paranoid<2 → refused
  - GPU_EXPOSED: paranoid ok but NVIDIA profiling open → refused
  - UNKNOWN on non-Linux → fail-closed (multi-tenant)
  - single-tenant mode skips the gate
  - dev override downgrades refusal
  - remediation() surfaces the right commands
  - stats counters
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PATH = _REPO_ROOT / "backend" / "security" / "perf_counter_lockdown.py"
_spec = importlib.util.spec_from_file_location("vos3_perflock_uut", _PATH)
pl = importlib.util.module_from_spec(_spec)
sys.modules["vos3_perflock_uut"] = pl
_spec.loader.exec_module(pl)


def _make_host(tmp_path, *, paranoid, nvidia=None):
    """nvidia: None=no GPU, True=admin-only, False=open."""
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel").mkdir(parents=True, exist_ok=True)
    if paranoid is not None:
        (proc / "sys" / "kernel" / "perf_event_paranoid").write_text(f"{paranoid}\n")
    if nvidia is not None:
        nvdir = proc / "driver" / "nvidia"
        nvdir.mkdir(parents=True, exist_ok=True)
        admin = "1" if nvidia else "0"
        (nvdir / "params").write_text(
            f"ResmanDebugLevel: 0\nRmProfilingAdminOnly: {admin}\n"
        )
    return pl.PerfCounterLockdown(proc_root=str(proc))


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------


def test_locked_no_gpu(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=None)
    r = lock.probe()
    assert r.state == pl.PerfLockState.LOCKED
    assert r.safe_for_multitenant is True
    assert r.nvidia_present is False


def test_locked_paranoid_3_hardened(tmp_path):
    lock = _make_host(tmp_path, paranoid=3, nvidia=None)
    assert lock.probe().state == pl.PerfLockState.LOCKED


def test_locked_with_nvidia_admin_only(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=True)
    r = lock.probe()
    assert r.state == pl.PerfLockState.LOCKED
    assert r.nvidia_present is True
    assert r.nvidia_profiling_admin_only is True


def test_cpu_exposed_low_paranoid(tmp_path):
    lock = _make_host(tmp_path, paranoid=1, nvidia=None)
    r = lock.probe()
    assert r.state == pl.PerfLockState.CPU_EXPOSED
    assert r.safe_for_multitenant is False


def test_cpu_exposed_paranoid_minus_one(tmp_path):
    lock = _make_host(tmp_path, paranoid=-1, nvidia=True)
    assert lock.probe().state == pl.PerfLockState.CPU_EXPOSED


def test_gpu_exposed_when_nvidia_open(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=False)
    r = lock.probe()
    assert r.state == pl.PerfLockState.GPU_EXPOSED
    assert r.gpu_ok is False
    assert r.safe_for_multitenant is False


# ---------------------------------------------------------------------------
# Non-Linux → UNKNOWN
# ---------------------------------------------------------------------------


def test_unknown_on_non_linux(monkeypatch):
    monkeypatch.setattr(pl.platform, "system", lambda: "Darwin")
    lock = pl.PerfCounterLockdown()
    assert lock.probe().state == pl.PerfLockState.UNKNOWN


# ---------------------------------------------------------------------------
# Enforcement gate
# ---------------------------------------------------------------------------


def test_require_allows_when_locked(tmp_path, monkeypatch):
    monkeypatch.delenv(pl.ENV_SINGLE_TENANT, raising=False)
    monkeypatch.delenv(pl.ENV_DEV_OVERRIDE, raising=False)
    lock = _make_host(tmp_path, paranoid=2, nvidia=True)
    report = lock.require_lockdown_for_multitenant_inference()
    assert report.state == pl.PerfLockState.LOCKED
    assert lock.stats.launches_allowed == 1


def test_require_refuses_when_cpu_exposed(tmp_path, monkeypatch):
    monkeypatch.delenv(pl.ENV_SINGLE_TENANT, raising=False)
    monkeypatch.delenv(pl.ENV_DEV_OVERRIDE, raising=False)
    lock = _make_host(tmp_path, paranoid=0, nvidia=None)
    with pytest.raises(pl.PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()
    assert lock.stats.launches_refused == 1


def test_require_refuses_when_gpu_exposed(tmp_path, monkeypatch):
    monkeypatch.delenv(pl.ENV_SINGLE_TENANT, raising=False)
    monkeypatch.delenv(pl.ENV_DEV_OVERRIDE, raising=False)
    lock = _make_host(tmp_path, paranoid=2, nvidia=False)
    with pytest.raises(pl.PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()


def test_require_refuses_on_unknown(monkeypatch):
    monkeypatch.delenv(pl.ENV_SINGLE_TENANT, raising=False)
    monkeypatch.delenv(pl.ENV_DEV_OVERRIDE, raising=False)
    monkeypatch.setattr(pl.platform, "system", lambda: "Darwin")
    lock = pl.PerfCounterLockdown()
    with pytest.raises(pl.PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()


def test_single_tenant_skips_gate(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.ENV_SINGLE_TENANT, "1")
    # Even a wide-open host is allowed in single-tenant mode.
    lock = _make_host(tmp_path, paranoid=-1, nvidia=False)
    report = lock.require_lockdown_for_multitenant_inference()
    assert report.state == pl.PerfLockState.LOCKED  # synthetic
    assert lock.stats.single_tenant_skips == 1


def test_dev_override_downgrades_refusal(tmp_path, monkeypatch):
    monkeypatch.delenv(pl.ENV_SINGLE_TENANT, raising=False)
    monkeypatch.setenv(pl.ENV_DEV_OVERRIDE, "1")
    lock = _make_host(tmp_path, paranoid=0, nvidia=None)
    report = lock.require_lockdown_for_multitenant_inference()
    assert report.state == pl.PerfLockState.CPU_EXPOSED
    assert lock.stats.dev_overrides_used == 1


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------


def test_remediation_for_cpu_exposed(tmp_path):
    lock = _make_host(tmp_path, paranoid=0, nvidia=None)
    cmds = lock.probe().remediation()
    assert any("perf_event_paranoid" in c for c in cmds)


def test_remediation_for_gpu_exposed(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=False)
    cmds = lock.probe().remediation()
    assert any("NVreg_RestrictProfilingToAdminUsers" in c for c in cmds)


def test_remediation_empty_when_locked(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=True)
    assert lock.probe().remediation() == []


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_track_probes(tmp_path):
    lock = _make_host(tmp_path, paranoid=2, nvidia=None)
    lock.probe()
    lock.probe()
    assert lock.stats.probes == 2
