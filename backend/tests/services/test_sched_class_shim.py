"""
Tests for Sprint 22 / Item J5 — cgroup v2 inference-class scheduling shim
(backend/services/sched_class_shim.py).

Pins the fail-closed contract: a real-time inference launch is allowed
only when placed in the bounded inference cgroup class; CFS launches are
unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.sched_class_shim import (  # noqa: E402
    InferenceLaunch,
    SchedClassRefused,
    SchedClassShim,
    SchedPolicy,
    ENV_DEV_OVERRIDE,
    DEFAULT_INFERENCE_CLASS,
)


def _cgroup_root(tmp_path, *, has_cpu=True, bounded=True):
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text(
        "cpuset cpu memory pids\n" if has_cpu else "memory pids\n"
    )
    agent = root / DEFAULT_INFERENCE_CLASS / "agent-7"
    agent.mkdir(parents=True)
    (agent / "cpu.max").write_text("200000 100000\n" if bounded else "max 100000\n")
    return str(root)


def test_cfs_launch_always_allowed(tmp_path):
    shim = SchedClassShim(cgroup_root=str(tmp_path))  # no inference class needed
    out = shim.require_inference_launch(
        InferenceLaunch(sched_policy=SchedPolicy.NORMAL, cgroup="anything")
    )
    assert out.sched_policy == SchedPolicy.NORMAL
    assert shim.stats.allowed_cfs == 1


def test_rt_launch_in_bounded_class_allowed(tmp_path):
    shim = SchedClassShim(cgroup_root=_cgroup_root(tmp_path))
    shim.require_inference_launch(
        InferenceLaunch(
            sched_policy=SchedPolicy.RT_FIFO,
            rt_priority=80,
            cgroup=f"{DEFAULT_INFERENCE_CLASS}/agent-7",
        )
    )
    assert shim.stats.allowed == 1


def test_rt_launch_outside_class_refused(tmp_path):
    shim = SchedClassShim(cgroup_root=_cgroup_root(tmp_path))
    with pytest.raises(SchedClassRefused):
        shim.require_inference_launch(
            InferenceLaunch(
                sched_policy=SchedPolicy.RT_FIFO,
                rt_priority=80,
                cgroup="user.slice/rogue",
            )
        )


def test_rt_launch_unbounded_budget_refused(tmp_path):
    shim = SchedClassShim(cgroup_root=_cgroup_root(tmp_path, bounded=False))
    with pytest.raises(SchedClassRefused):
        shim.require_inference_launch(
            InferenceLaunch(
                sched_policy=SchedPolicy.RT_RR,
                rt_priority=50,
                cgroup=f"{DEFAULT_INFERENCE_CLASS}/agent-7",
            )
        )


def test_rt_launch_without_cgroup_v2_cpu_refused(tmp_path):
    shim = SchedClassShim(cgroup_root=_cgroup_root(tmp_path, has_cpu=False))
    with pytest.raises(SchedClassRefused):
        shim.require_inference_launch(
            InferenceLaunch(
                sched_policy=SchedPolicy.RT_FIFO,
                rt_priority=80,
                cgroup=f"{DEFAULT_INFERENCE_CLASS}/agent-7",
            )
        )


def test_dev_override_allows_rt_outside_class(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    shim = SchedClassShim(cgroup_root=_cgroup_root(tmp_path))
    shim.require_inference_launch(
        InferenceLaunch(
            sched_policy=SchedPolicy.RT_FIFO, rt_priority=80, cgroup="user.slice/x"
        )
    )
    assert shim.stats.dev_overrides_used == 1
