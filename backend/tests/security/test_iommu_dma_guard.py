"""
backend/tests/security/test_iommu_dma_guard.py

Sprint 19 / Wave 1 / Item E3 — IOMMU/DMA-bypass fail-closed guard.

Covers (via injected fake /proc + /sys roots so the tests run on any
host, macOS or Linux):
  - ENFORCING: cmdline on + groups + class devices → safe_for_gpu_bind
  - PASSTHROUGH: iommu=pt → refused (DMA not translated)
  - DISABLED: no cmdline enable + no groups → refused
  - DISABLED: cmdline on but no groups bound (driver didn't attach)
  - UNKNOWN on non-Linux without injected root → fail-closed
  - require_iommu_for_gpu_bind raises DmaBypassRefused when unsafe
  - dev override downgrades refusal to allow + WARNING
  - require_no_ats: ATS-enabled device blocks bind in hardened mode
  - stats counters
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PATH = _REPO_ROOT / "backend" / "security" / "iommu_dma_guard.py"
_spec = importlib.util.spec_from_file_location("vos3_iommu_guard_uut", _PATH)
g = importlib.util.module_from_spec(_spec)
sys.modules["vos3_iommu_guard_uut"] = g
_spec.loader.exec_module(g)


# ---------------------------------------------------------------------------
# Fake-host fixtures — build a /proc + /sys tree on disk
# ---------------------------------------------------------------------------


def _make_host(
    tmp_path, *, cmdline: str, groups: int, class_devs: int, ats_devs: int = 0
):
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "cmdline").write_text(cmdline + "\n")

    groups_dir = sysfs / "kernel" / "iommu_groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    for i in range(groups):
        (groups_dir / str(i)).mkdir()

    class_dir = sysfs / "class" / "iommu"
    class_dir.mkdir(parents=True, exist_ok=True)
    for i in range(class_devs):
        iommu = class_dir / f"dmar{i}"
        devdir = iommu / "devices"
        devdir.mkdir(parents=True, exist_ok=True)
        # Attach ats marker to the first `ats_devs` devices.
        dev = devdir / f"0000:00:0{i}.0"
        dev.mkdir(parents=True, exist_ok=True)
        if i < ats_devs:
            (dev / "ats").mkdir()

    return g.IommuDmaGuard(sysfs_root=str(sysfs), proc_root=str(proc))


# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------


def test_enforcing_state(tmp_path):
    guard = _make_host(
        tmp_path, cmdline="ro quiet intel_iommu=on", groups=12, class_devs=2
    )
    r = guard.probe()
    assert r.state == g.IommuState.ENFORCING
    assert r.safe_for_gpu_bind is True


def test_passthrough_state_refused(tmp_path):
    guard = _make_host(
        tmp_path, cmdline="intel_iommu=on iommu=pt", groups=12, class_devs=2
    )
    r = guard.probe()
    assert r.state == g.IommuState.PASSTHROUGH
    assert r.safe_for_gpu_bind is False


def test_disabled_no_cmdline_no_groups(tmp_path):
    guard = _make_host(tmp_path, cmdline="ro quiet", groups=0, class_devs=0)
    r = guard.probe()
    assert r.state == g.IommuState.DISABLED
    assert r.safe_for_gpu_bind is False


def test_disabled_cmdline_on_but_no_groups_bound(tmp_path):
    guard = _make_host(tmp_path, cmdline="amd_iommu=on", groups=0, class_devs=0)
    r = guard.probe()
    assert r.state == g.IommuState.DISABLED
    assert "driver not attached" in r.reason


def test_amd_iommu_force_enables(tmp_path):
    guard = _make_host(tmp_path, cmdline="amd_iommu=force", groups=5, class_devs=1)
    assert guard.probe().state == g.IommuState.ENFORCING


# ---------------------------------------------------------------------------
# Non-Linux → UNKNOWN → fail-closed
# ---------------------------------------------------------------------------


def test_unknown_on_non_linux_without_roots(monkeypatch):
    monkeypatch.setattr(g.platform, "system", lambda: "Darwin")
    guard = g.IommuDmaGuard()  # no injected roots
    r = guard.probe()
    assert r.state == g.IommuState.UNKNOWN
    assert r.safe_for_gpu_bind is False


# ---------------------------------------------------------------------------
# Enforcement gate
# ---------------------------------------------------------------------------


def test_require_gpu_bind_allows_when_enforcing(tmp_path):
    guard = _make_host(tmp_path, cmdline="intel_iommu=on", groups=8, class_devs=1)
    report = guard.require_iommu_for_gpu_bind()
    assert report.state == g.IommuState.ENFORCING
    assert guard.stats.gpu_bind_allowed == 1
    assert guard.stats.gpu_bind_refused == 0


def test_require_gpu_bind_refuses_when_passthrough(tmp_path):
    guard = _make_host(
        tmp_path, cmdline="intel_iommu=on iommu=pt", groups=8, class_devs=1
    )
    with pytest.raises(g.DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()
    assert guard.stats.gpu_bind_refused == 1


def test_require_gpu_bind_refuses_on_unknown(monkeypatch):
    monkeypatch.setattr(g.platform, "system", lambda: "Darwin")
    guard = g.IommuDmaGuard()
    with pytest.raises(g.DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()


def test_dev_override_downgrades_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv(g.ENV_DEV_OVERRIDE, "1")
    guard = _make_host(tmp_path, cmdline="ro quiet", groups=0, class_devs=0)
    report = guard.require_iommu_for_gpu_bind()  # would refuse, but override
    assert report.state == g.IommuState.DISABLED
    assert guard.stats.dev_overrides_used == 1
    assert guard.stats.gpu_bind_allowed == 1


# ---------------------------------------------------------------------------
# ATS hardened mode
# ---------------------------------------------------------------------------


def test_require_no_ats_blocks_ats_enabled_device(tmp_path, monkeypatch):
    monkeypatch.setenv(g.ENV_REQUIRE_NO_ATS, "1")
    guard = _make_host(
        tmp_path,
        cmdline="intel_iommu=on",
        groups=4,
        class_devs=2,
        ats_devs=1,
    )
    r = guard.probe()
    assert r.state == g.IommuState.ENFORCING
    assert r.ats_enabled_devices == 1
    # ENFORCING but ATS present + hardened mode → not safe.
    assert r.safe_for_gpu_bind is False
    with pytest.raises(g.DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()


def test_ats_present_but_not_hardened_still_safe(tmp_path):
    # No VOS3_IOMMU_REQUIRE_NO_ATS → ATS is advisory only.
    guard = _make_host(
        tmp_path,
        cmdline="intel_iommu=on",
        groups=4,
        class_devs=2,
        ats_devs=1,
    )
    r = guard.probe()
    assert r.ats_enabled_devices == 1
    assert r.safe_for_gpu_bind is True


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_track_probes(tmp_path):
    guard = _make_host(tmp_path, cmdline="intel_iommu=on", groups=2, class_devs=1)
    guard.probe()
    guard.probe()
    assert guard.stats.probes == 2
