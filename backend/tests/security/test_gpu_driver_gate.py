"""
Tests for Sprint 21 / Item E1 — GPU driver-opacity fail-closed gate
(backend/security/gpu_driver_gate.py).

Pins the fail-closed contract: GPU bind is refused unless the loaded
NVIDIA module is provably open + signed (+ matching the operator-pinned
build), with a dev-override escape hatch that logs loud.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.gpu_driver_gate import (  # noqa: E402
    DriverState,
    GpuDriverGate,
    UntrustedGpuDriver,
    ENV_DEV_OVERRIDE,
    ENV_PINNED_VERSION,
)


def _module_tree(tmp_path, *, taint="", version="550.90.07"):
    root = tmp_path / "nvidia"
    root.mkdir()
    (root / "taint").write_text(taint + "\n")
    (root / "version").write_text(version + "\n")
    return str(root)


def test_open_signed_module_allows_bind(tmp_path):
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="O"))
    report = gate.require_trusted_driver_for_gpu_bind()
    assert report.state == DriverState.OPEN_SIGNED
    assert gate.stats.gpu_bind_allowed == 1


def test_proprietary_blob_refused(tmp_path):
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="PO"))
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()
    assert gate.probe().state == DriverState.PROPRIETARY


def test_unsigned_module_refused(tmp_path):
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="E"))
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()
    assert gate.probe().state == DriverState.UNSIGNED


def test_absent_module_refused(tmp_path):
    gate = GpuDriverGate(module_root=str(tmp_path / "does-not-exist"))
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()
    assert gate.probe().state == DriverState.ABSENT


def test_version_pin_mismatch_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PINNED_VERSION, "999.99.99")
    gate = GpuDriverGate(
        module_root=_module_tree(tmp_path, taint="O", version="550.90.07")
    )
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()
    assert gate.probe().state == DriverState.VERSION_MISMATCH


def test_version_pin_match_allows(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PINNED_VERSION, "550.90.07")
    gate = GpuDriverGate(
        module_root=_module_tree(tmp_path, taint="O", version="550.90.07")
    )
    report = gate.require_trusted_driver_for_gpu_bind()
    assert report.state == DriverState.OPEN_SIGNED


def test_dev_override_allows_proprietary(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="P"))
    report = gate.require_trusted_driver_for_gpu_bind()
    assert report.state == DriverState.PROPRIETARY  # state still honest
    assert gate.stats.dev_overrides_used == 1


def test_non_linux_unknown_fail_closed(monkeypatch):
    monkeypatch.delenv(ENV_DEV_OVERRIDE, raising=False)
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    gate = GpuDriverGate(module_root=None)
    assert gate.probe().state == DriverState.UNKNOWN
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()
