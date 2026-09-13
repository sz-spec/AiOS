"""
Tests for Sprint 21 / Primitive (c) / Item O4 — GPU allocation validator
(backend/security/gpu_alloc_validator.py).

Pins the fail-closed admission contract: a shared-host GPU allocation is
refused unless MIG partition + Confidential Compute + fresh bound
attestation + cgroup caps ALL hold. Uses injected fake procfs/sysfs +
an injected attestation provider + a pinned clock (the mig_isolation_sim
stub) so the four-way gate is exercised on a macOS dev host.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.gpu_alloc_validator import (  # noqa: E402
    AttestationQuote,
    GpuAllocRefused,
    GpuAllocRequest,
    GpuAllocValidator,
    ENV_DEV_OVERRIDE,
)

MIG_UUID = "MIG-12345678-1234-1234-1234-1234567890ab"


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def _host(tmp_path, *, mig="1", cc="on"):
    """Build a fake procfs/sysfs tree. Returns (proc_root, sysfs_root)."""
    proc = tmp_path / "proc"
    gpus = proc / "driver" / "nvidia" / "gpus" / "0000:01:00.0"
    gpus.mkdir(parents=True)
    (gpus / "mig_mode").write_text(mig + "\n")

    sysfs = tmp_path / "sys"
    dev = sysfs / "class" / "nvidia" / "nvidia0"
    dev.mkdir(parents=True)
    (dev / "cc_mode").write_text(cc + "\n")
    return str(proc), str(sysfs)


def _fresh_provider(clock, uuid=MIG_UUID, verified=True, age=10.0):
    def provider(req_uuid):
        if req_uuid != uuid:
            return None
        return AttestationQuote(
            mig_uuid=uuid, issued_at=clock.now - age, verified=verified
        )

    return provider


def _good_request(shared=True):
    return GpuAllocRequest(
        gpu_index=0,
        mig_uuid=MIG_UUID,
        shared_host=shared,
        cgroup_mem_limit_bytes=8 << 30,
        cgroup_sm_limit=14,
    )


def test_dedicated_gpu_allowed_without_isolation(tmp_path):
    v = GpuAllocValidator()
    report = v.require_isolated_gpu_alloc(_good_request(shared=False))
    assert report.safe_for_alloc
    assert v.stats.allowed_dedicated == 1


def test_all_conditions_met_allows(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    report = v.require_isolated_gpu_alloc(_good_request())
    assert report.safe_for_alloc
    assert report.mig_enabled and report.cc_enabled
    assert report.attestation_fresh and report.cgroup_capped


def test_mig_off_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path, mig="0")
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())


def test_cc_off_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path, cc="off")
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())


def test_stale_attestation_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock, age=10_000.0),  # > TTL
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())


def test_unverified_attestation_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock, verified=False),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())


def test_attestation_uuid_mismatch_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock, uuid="MIG-other"),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())


def test_overcommit_no_cgroup_caps_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_fresh_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    req = GpuAllocRequest(
        gpu_index=0,
        mig_uuid=MIG_UUID,
        shared_host=True,
        cgroup_mem_limit_bytes=None,
        cgroup_sm_limit=None,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(req)


def test_dev_override_allows_unverifiable(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    v = GpuAllocValidator()  # no host probe, no attestation → unverifiable
    report = v.require_isolated_gpu_alloc(_good_request())
    assert not report.safe_for_alloc  # state stays honest
    assert v.stats.dev_overrides_used == 1


def test_unprobeable_shared_host_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_DEV_OVERRIDE, raising=False)
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    v = GpuAllocValidator()  # no injected roots, non-Linux → unprobeable
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_request())
