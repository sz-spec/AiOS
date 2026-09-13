"""
B6 — GPU / accelerator isolation boundary tests (TEST_PLAN_300 §B6)
===================================================================

Adversarial sweep over the four fail-closed hardware-isolation gates that
guard the accelerator bind path (bind order: E1 driver → E3 IOMMU →
O4 alloc → E6 perf → warp):

  * ``security/iommu_dma_guard.py``       (E3) — DMA-bypass guard
  * ``security/perf_counter_lockdown.py`` (E6) — side-channel lockdown
  * ``security/accel_ioctl_filter.py``    (E2) — default-deny ioctl allowlist
  * ``security/gpu_alloc_validator.py``   (O4) — shared-host MIG/CC admission

Goal: prove a GPU context cannot be bound / allocated unless the host can
PROVE isolation — an unverifiable host fail-closes, and the only escape is a
process-env dev override (never request-controlled). Telemetry side-channels
(perf counters) and the ioctl escalation surface are gated likewise.

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b6_gpu_isolation.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from security.accel_ioctl_filter import (
    AccelDevice,
    AccelIoctlFilter,
    IoctlBlocked,
    decode_ioctl,
)
from security.gpu_alloc_validator import (
    AttestationQuote,
    GpuAllocRefused,
    GpuAllocRequest,
    GpuAllocValidator,
)
from security.iommu_dma_guard import DmaBypassRefused, IommuDmaGuard, IommuState
from security.perf_counter_lockdown import (
    PerfCounterExposed,
    PerfCounterLockdown,
    PerfLockState,
)


class FakeClock:
    def __init__(self, t: float = 1_000.0):
        self._t = t

    def time(self) -> float:
        return self._t


# ---------------------------------------------------------------------------
# Fake /proc + /sys builders
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _make_enforcing_iommu(root: Path) -> tuple[str, str]:
    proc = root / "proc"
    sysfs = root / "sys"
    _write(proc / "cmdline", "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=force\n")
    (sysfs / "kernel" / "iommu_groups" / "0").mkdir(parents=True)
    (sysfs / "kernel" / "iommu_groups" / "1").mkdir(parents=True)
    (sysfs / "class" / "iommu" / "dmar0").mkdir(parents=True)
    return str(sysfs), str(proc)


# ===========================================================================
# E3 — IOMMU / DMA-bypass guard
# ===========================================================================


def test_b6_iommu_non_linux_unknown_fails_closed():
    """On an unverifiable host (no injected roots, non-Linux) → refuse."""
    guard = IommuDmaGuard()  # no roots; on macOS dev this probes UNKNOWN
    rep = guard.probe()
    if rep.state == IommuState.UNKNOWN:
        with pytest.raises(DmaBypassRefused):
            guard.require_iommu_for_gpu_bind()
    else:  # on a real Linux CI host the probe reflects the host — don't assert
        pytest.skip("running on Linux; host IOMMU state is real, not UNKNOWN")


def test_b6_iommu_enforcing_allows_bind(tmp_path):
    sysfs, proc = _make_enforcing_iommu(tmp_path)
    guard = IommuDmaGuard(sysfs_root=sysfs, proc_root=proc)
    rep = guard.require_iommu_for_gpu_bind()
    assert rep.state == IommuState.ENFORCING


def test_b6_iommu_passthrough_refuses_bind(tmp_path):
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    _write(proc / "cmdline", "intel_iommu=on iommu=pt\n")
    (sysfs / "kernel" / "iommu_groups" / "0").mkdir(parents=True)
    (sysfs / "class" / "iommu" / "dmar0").mkdir(parents=True)
    guard = IommuDmaGuard(sysfs_root=str(sysfs), proc_root=str(proc))
    with pytest.raises(DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()


def test_b6_iommu_disabled_refuses_bind(tmp_path):
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    _write(proc / "cmdline", "BOOT_IMAGE=/vmlinuz quiet\n")  # no iommu enable
    (sysfs / "kernel" / "iommu_groups").mkdir(parents=True)  # empty → 0 groups
    (sysfs / "class" / "iommu").mkdir(parents=True)
    guard = IommuDmaGuard(sysfs_root=str(sysfs), proc_root=str(proc))
    with pytest.raises(DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()


def test_b6_iommu_dev_override_is_process_env_only(tmp_path, monkeypatch):
    """The override comes from the PROCESS env (monkeypatch), never from a
    request. With it set, an otherwise-refused host is allowed (loud warn)."""
    proc = tmp_path / "proc"
    sysfs = tmp_path / "sys"
    _write(proc / "cmdline", "quiet\n")
    (sysfs / "kernel" / "iommu_groups").mkdir(parents=True)
    (sysfs / "class" / "iommu").mkdir(parents=True)
    guard = IommuDmaGuard(sysfs_root=str(sysfs), proc_root=str(proc))
    with pytest.raises(DmaBypassRefused):
        guard.require_iommu_for_gpu_bind()
    monkeypatch.setenv("VOS3_IOMMU_DEV_OVERRIDE", "1")
    rep = guard.require_iommu_for_gpu_bind()  # now downgraded to allow
    assert guard.stats.dev_overrides_used == 1
    assert rep.state != IommuState.ENFORCING  # allowed *despite* not enforcing


# ===========================================================================
# E6 — perf-counter lockdown (side-channel / model-fingerprinting)
# ===========================================================================


def _perf_proc(root: Path, paranoid: str, nvidia_params: str | None) -> str:
    proc = root / "proc"
    _write(proc / "sys" / "kernel" / "perf_event_paranoid", paranoid)
    if nvidia_params is not None:
        _write(proc / "driver" / "nvidia" / "params", nvidia_params)
    return str(proc)


def test_b6_perf_cpu_exposed_refuses(tmp_path):
    proc = _perf_proc(tmp_path, "1", None)  # below floor 2
    lock = PerfCounterLockdown(proc_root=proc)
    with pytest.raises(PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()


def test_b6_perf_gpu_exposed_refuses(tmp_path):
    proc = _perf_proc(tmp_path, "2", "RmProfilingAdminOnly: 0\n")
    lock = PerfCounterLockdown(proc_root=proc)
    with pytest.raises(PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()


def test_b6_perf_locked_no_gpu_allows(tmp_path):
    proc = _perf_proc(tmp_path, "2", None)  # paranoid ok, no nvidia
    lock = PerfCounterLockdown(proc_root=proc)
    rep = lock.require_lockdown_for_multitenant_inference()
    assert rep.state == PerfLockState.LOCKED


def test_b6_perf_locked_admin_only_gpu_allows(tmp_path):
    proc = _perf_proc(tmp_path, "3", "RmProfilingAdminOnly: 1\n")
    lock = PerfCounterLockdown(proc_root=proc)
    rep = lock.require_lockdown_for_multitenant_inference()
    assert rep.state == PerfLockState.LOCKED


def test_b6_perf_single_tenant_skips_gate(tmp_path, monkeypatch):
    proc = _perf_proc(tmp_path, "0", "RmProfilingAdminOnly: 0\n")  # wide open
    lock = PerfCounterLockdown(proc_root=proc)
    monkeypatch.setenv("VOS3_PERF_SINGLE_TENANT", "1")
    rep = lock.require_lockdown_for_multitenant_inference()  # skipped → allowed
    assert rep.safe_for_multitenant is True
    assert lock.stats.single_tenant_skips == 1


def test_b6_perf_dev_override_allows_exposed(tmp_path, monkeypatch):
    proc = _perf_proc(tmp_path, "1", None)
    lock = PerfCounterLockdown(proc_root=proc)
    with pytest.raises(PerfCounterExposed):
        lock.require_lockdown_for_multitenant_inference()
    monkeypatch.setenv("VOS3_PERF_DEV_OVERRIDE", "1")
    lock.require_lockdown_for_multitenant_inference()
    assert lock.stats.dev_overrides_used == 1


# ===========================================================================
# E2 — accelerator ioctl allowlist (driver escalation surface)
# ===========================================================================


def _nv_ioctl(nr: int, *, dir_: int = 0, size: int = 0) -> int:
    NV_MAGIC = ord("F")  # 0x46
    return (dir_ << 30) | (size << 16) | (NV_MAGIC << 8) | nr


def test_b6_ioctl_decode_roundtrip():
    code = _nv_ioctl(0x2A, dir_=1, size=4)
    d = decode_ioctl(code)
    assert d.type == ord("F") and d.nr == 0x2A and d.size == 4 and d.dir == 1
    assert d.key == (ord("F"), 0x2A)


def test_b6_ioctl_allowlisted_is_allowed():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    d = filt.require_ioctl(_nv_ioctl(0x2A))  # NV_ESC_CARD_INFO
    assert d.key == (ord("F"), 0x2A)
    assert filt.is_allowed(_nv_ioctl(0x2A)) is True


def test_b6_ioctl_unlisted_is_default_denied():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    # nr=0x99 is a fuzzer-chosen command not on the conservative allowlist.
    assert filt.is_allowed(_nv_ioctl(0x99)) is False
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_nv_ioctl(0x99))


def test_b6_ioctl_empty_allowlist_blocks_everything():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL, allowlist=frozenset())
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_nv_ioctl(0x2A))


def test_b6_ioctl_extra_allow_from_env(monkeypatch):
    monkeypatch.setenv("VOS3_ACCEL_IOCTL_EXTRA_ALLOW", "F:0x99")
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    assert filt.is_allowed(_nv_ioctl(0x99)) is True


def test_b6_ioctl_dev_override_allows_blocked(monkeypatch):
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    monkeypatch.setenv("VOS3_ACCEL_IOCTL_DEV_OVERRIDE", "1")
    filt.require_ioctl(_nv_ioctl(0x99))  # downgraded to allow
    assert filt.stats.dev_overrides_used == 1


# ===========================================================================
# O4 — shared-host GPU allocation (cross-tenant model-stealing admission)
# ===========================================================================


def _make_isolated_gpu(root: Path) -> tuple[str, str]:
    proc = root / "proc"
    sysfs = root / "sys"
    _write(proc / "driver" / "nvidia" / "gpus" / "0000:01:00.0" / "mig_mode", "1\n")
    _write(sysfs / "class" / "nvidia" / "nvidia0" / "cc_mode", "on\n")
    return str(sysfs), str(proc)


def _fresh_provider(clock):
    def provider(uuid):
        return AttestationQuote(mig_uuid=uuid, issued_at=clock.time(), verified=True)
    return provider


def test_b6_alloc_dedicated_host_allowed():
    v = GpuAllocValidator()
    rep = v.require_isolated_gpu_alloc(
        GpuAllocRequest(gpu_index=0, mig_uuid=None, shared_host=False))
    assert rep.safe_for_alloc is True
    assert v.stats.allowed_dedicated == 1


def test_b6_alloc_shared_all_conditions_met_allowed(tmp_path):
    clk = FakeClock()
    sysfs, proc = _make_isolated_gpu(tmp_path)
    v = GpuAllocValidator(attestation_provider=_fresh_provider(clk),
                          sysfs_root=sysfs, proc_root=proc, clock=clk)
    rep = v.require_isolated_gpu_alloc(GpuAllocRequest(
        gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
        cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))
    assert rep.safe_for_alloc is True


def test_b6_alloc_shared_missing_mig_refused(tmp_path):
    clk = FakeClock()
    # CC + attestation + cgroup ok, but MIG mode off (no mig_mode file).
    sysfs = tmp_path / "sys"
    proc = tmp_path / "proc"
    _write(sysfs / "class" / "nvidia" / "nvidia0" / "cc_mode", "on\n")
    (proc / "driver" / "nvidia" / "gpus").mkdir(parents=True)
    v = GpuAllocValidator(attestation_provider=_fresh_provider(clk),
                          sysfs_root=str(sysfs), proc_root=str(proc), clock=clk)
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(GpuAllocRequest(
            gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
            cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))


def test_b6_alloc_stale_attestation_refused(tmp_path):
    clk = FakeClock()
    sysfs, proc = _make_isolated_gpu(tmp_path)

    def stale_provider(uuid):
        return AttestationQuote(uuid, issued_at=clk.time() - 400, verified=True)

    v = GpuAllocValidator(attestation_provider=stale_provider,
                          sysfs_root=sysfs, proc_root=proc, clock=clk)
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(GpuAllocRequest(
            gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
            cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))


def test_b6_alloc_attestation_uuid_mismatch_refused(tmp_path):
    clk = FakeClock()
    sysfs, proc = _make_isolated_gpu(tmp_path)

    def wrong_uuid_provider(uuid):
        return AttestationQuote("MIG-OTHER", issued_at=clk.time(), verified=True)

    v = GpuAllocValidator(attestation_provider=wrong_uuid_provider,
                          sysfs_root=sysfs, proc_root=proc, clock=clk)
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(GpuAllocRequest(
            gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
            cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))


def test_b6_alloc_uncapped_cgroup_refused(tmp_path):
    clk = FakeClock()
    sysfs, proc = _make_isolated_gpu(tmp_path)
    v = GpuAllocValidator(attestation_provider=_fresh_provider(clk),
                          sysfs_root=sysfs, proc_root=proc, clock=clk)
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(GpuAllocRequest(
            gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
            cgroup_mem_limit_bytes=None, cgroup_sm_limit=None))  # overcommit


def test_b6_alloc_attestation_provider_raises_fails_closed(tmp_path):
    """A provider that throws must be treated as NO proof → refuse."""
    clk = FakeClock()
    sysfs, proc = _make_isolated_gpu(tmp_path)

    def boom(uuid):
        raise RuntimeError("attestation backend down")

    v = GpuAllocValidator(attestation_provider=boom,
                          sysfs_root=sysfs, proc_root=proc, clock=clk)
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(GpuAllocRequest(
            gpu_index=0, mig_uuid="MIG-abc", shared_host=True,
            cgroup_mem_limit_bytes=8 << 30, cgroup_sm_limit=14))


# ===========================================================================
# B6.08 — systemic: dev-overrides are process-env only, never request-fed
# ===========================================================================


def test_b6_08_overrides_never_read_from_request_input():
    """Static guard: no gate module sets its DEV_OVERRIDE/SINGLE_TENANT env
    from a request/header/json body. Overrides MUST be operator process-env
    only — otherwise an attacker could disable a gate per-request."""
    import re

    backend = Path(__file__).resolve().parents[2]
    suspicious = re.compile(
        r"environ\[\s*['\"][A-Z0-9_]*(DEV_OVERRIDE|SINGLE_TENANT|EXTRA_ALLOW)"
        r"['\"]\s*\]\s*="  # assignment INTO os.environ[...]
    )
    offenders = []
    for mod in [
        "security/iommu_dma_guard.py",
        "security/perf_counter_lockdown.py",
        "security/accel_ioctl_filter.py",
        "security/gpu_alloc_validator.py",
    ]:
        text = (backend / mod).read_text(encoding="utf-8")
        if suspicious.search(text):
            offenders.append(mod)
    assert not offenders, (
        f"override env written (not just read) in {offenders} — a request "
        f"could flip a fail-closed gate open"
    )
