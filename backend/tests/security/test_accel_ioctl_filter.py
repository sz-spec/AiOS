"""
Tests for Sprint 21 / Item E2 — accelerator ioctl allowlist
(backend/security/accel_ioctl_filter.py).

Pins the default-deny, fail-closed contract: an allowlisted (type, nr)
passes; everything else is refused, with a dev-override that logs loud.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.accel_ioctl_filter import (  # noqa: E402
    AccelDevice,
    AccelIoctlFilter,
    IoctlBlocked,
    decode_ioctl,
    ENV_DEV_OVERRIDE,
    ENV_EXTRA_ALLOW,
)

_NV = ord("F")


def _ioc(type_: int, nr: int, size: int = 0, dir_: int = 0) -> int:
    return (dir_ << 30) | (size << 16) | (type_ << 8) | nr


def test_decode_roundtrip():
    req = _ioc(_NV, 0x2A, size=32, dir_=3)
    d = decode_ioctl(req)
    assert d.type == _NV
    assert d.nr == 0x2A
    assert d.size == 32
    assert d.dir == 3
    assert d.key == (_NV, 0x2A)


def test_default_allowlist_permits_known_nv_ioctl():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    # NV_ESC_CARD_INFO is in the default allowlist.
    assert filt.is_allowed(_ioc(_NV, 0x2A, size=128))
    d = filt.require_ioctl(_ioc(_NV, 0x2A, size=128))
    assert d.key == (_NV, 0x2A)
    assert filt.stats.allowed == 1


def test_non_allowlisted_ioctl_refused():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_ioc(_NV, 0xDE))  # not in the allowlist
    assert filt.stats.blocked == 1


def test_size_independent_match():
    """A driver may bump the arg struct size across versions; identity is
    (type, nr), so a known command with a different size still passes."""
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    assert filt.is_allowed(_ioc(_NV, 0x2A, size=64))
    assert filt.is_allowed(_ioc(_NV, 0x2A, size=4096))


def test_dev_override_allows_blocked(monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    d = filt.require_ioctl(_ioc(_NV, 0xDE))
    assert d.nr == 0xDE
    assert filt.stats.dev_overrides_used == 1


def test_env_extra_allow_extends(monkeypatch):
    monkeypatch.setenv(ENV_EXTRA_ALLOW, "F:0xDE,F:0xDF")
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    assert filt.is_allowed(_ioc(_NV, 0xDE))
    assert filt.is_allowed(_ioc(_NV, 0xDF))


def test_explicit_empty_allowlist_denies_everything():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL, allowlist=frozenset())
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_ioc(_NV, 0x2A))


def test_runtime_allow_after_audit():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_GPU)
    req = _ioc(_NV, 0x55)
    assert not filt.is_allowed(req)
    filt.allow(_NV, 0x55)
    assert filt.is_allowed(req)


def test_uvm_device_has_distinct_default_allowlist():
    uvm = AccelIoctlFilter(device=AccelDevice.NVIDIA_UVM)
    # UVM_INITIALIZE (type 0, nr 1) is allowed for UVM...
    assert uvm.is_allowed(_ioc(0x0, 0x1))
    # ...but the NV control magic command is NOT on the UVM list.
    assert not uvm.is_allowed(_ioc(_NV, 0x2A))
