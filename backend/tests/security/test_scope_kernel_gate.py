"""
backend/tests/security/test_scope_kernel_gate.py

Sprint 16 / Item B5 — OAuth scope → kernel allowlist tests.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SKG_PATH = _REPO_ROOT / "backend" / "security" / "scope_kernel_gate.py"
_spec = importlib.util.spec_from_file_location("vos3_skg_under_test", _SKG_PATH)
skg = importlib.util.module_from_spec(_spec)
sys.modules["vos3_skg_under_test"] = skg
_spec.loader.exec_module(skg)

# Use the SAME lcg the gate uses internally — identical to B4 test pattern.
lcg = skg._load_lcg()


def _ctx(**kw):
    base = dict(
        capability=lcg.LinuxCapability.NET_RAW,
        process_id=1,
        comm="agent",
        exe_path="/opt/x",
        tool_call_name=None,
        taint_label=0,
        timestamp_ns=time.time_ns(),
    )
    base.update(kw)
    return lcg.CapContext(**base)


# ---------------------------------------------------------------------------
# Mapping validation
# ---------------------------------------------------------------------------


def test_add_mapping_rejects_non_mapping_type():
    g = skg.ScopeKernelGate()
    with pytest.raises(TypeError):
        g.add_mapping("not-a-mapping")  # type: ignore[arg-type]


def test_add_mapping_rejects_empty_scope():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="",
                capability_name="NET_RAW",
                reason="r",
            )
        )


def test_add_mapping_rejects_empty_capability():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="net.raw",
                capability_name="",
                reason="r",
            )
        )


def test_add_mapping_rejects_unknown_capability():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError, match="unknown capability"):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="net.raw",
                capability_name="NOT_A_REAL_CAP",
                reason="r",
            )
        )


def test_add_mapping_rejects_invalid_decision():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError, match="decision_name"):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="net.raw",
                capability_name="NET_RAW",
                decision_name="MAYBE",
                reason="r",
            )
        )


def test_add_mapping_rejects_empty_reason():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="net.raw",
                capability_name="NET_RAW",
                reason="",
            )
        )


def test_add_mapping_rejects_unknown_taint_label():
    g = skg.ScopeKernelGate()
    with pytest.raises(skg.ScopeMappingError, match="unknown taint label"):
        g.add_mapping(
            skg.ScopeMapping(
                scope_name="net.raw",
                capability_name="NET_RAW",
                reason="r",
                max_taint_label="NOT_A_LABEL",
            )
        )


def test_list_mappings_returns_all_registered():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("net.raw", "NET_RAW", reason="a"))
    g.add_mapping(skg.ScopeMapping("sys.admin", "SYS_ADMIN", reason="b"))
    assert len(g.list_mappings()) == 2


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def test_compile_for_empty_scopes_returns_empty():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("net.raw", "NET_RAW", reason="r"))
    rules = g.compile_rules_for(set())
    assert rules == []


def test_compile_rejects_non_set():
    g = skg.ScopeKernelGate()
    with pytest.raises(TypeError):
        g.compile_rules_for(["net.raw"])  # type: ignore[arg-type]


def test_compile_for_matching_scope_returns_rule():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("net.raw", "NET_RAW", reason="agent_net_raw"))
    rules = g.compile_rules_for({"net.raw"})
    assert len(rules) == 1
    assert rules[0].capability == lcg.LinuxCapability.NET_RAW
    assert rules[0].decision == lcg.CapDecision.ALLOW


def test_compile_unknown_scope_yields_no_rule():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("net.raw", "NET_RAW", reason="r"))
    rules = g.compile_rules_for({"completely.unknown.scope"})
    assert rules == []


def test_compile_with_tool_glob_filters_predicate():
    g = skg.ScopeKernelGate()
    g.add_mapping(
        skg.ScopeMapping(
            "net.raw",
            "NET_RAW",
            reason="r",
            tool_glob="fetch_*",
        )
    )
    rules = g.compile_rules_for({"net.raw"})
    assert rules[0].predicate(_ctx(tool_call_name="fetch_url"))
    assert not rules[0].predicate(_ctx(tool_call_name="send_email"))


def test_compile_with_taint_ceiling_filters_predicate():
    g = skg.ScopeKernelGate()
    g.add_mapping(
        skg.ScopeMapping(
            "net.raw",
            "NET_RAW",
            reason="r",
            max_taint_label="UNTRUSTED",
        )
    )
    rules = g.compile_rules_for({"net.raw"})
    assert rules[0].predicate(_ctx(taint_label=0))
    assert rules[0].predicate(_ctx(taint_label=1))  # UNTRUSTED
    assert not rules[0].predicate(_ctx(taint_label=2))  # SECRET


def test_compile_multiple_mappings_per_scope():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("priv.elevated", "NET_RAW", reason="a"))
    g.add_mapping(skg.ScopeMapping("priv.elevated", "SYS_ADMIN", reason="b"))
    rules = g.compile_rules_for({"priv.elevated"})
    assert len(rules) == 2


# ---------------------------------------------------------------------------
# Install + integration with CapGate
# ---------------------------------------------------------------------------


def test_install_into_capgate_end_to_end():
    g = skg.ScopeKernelGate()
    g.add_mapping(
        skg.ScopeMapping(
            "net.fetch",
            "NET_RAW",
            reason="agent_fetch",
            tool_glob="fetch_*",
            max_taint_label="UNTRUSTED",
        )
    )
    gate = lcg.CapGate(default_decision=lcg.CapDecision.DENY)
    installed = g.install_into(gate, granted_scopes={"net.fetch"})
    assert installed == 1
    # Agent fetch with PUBLIC taint allowed.
    r1 = gate.check(_ctx(tool_call_name="fetch_url", taint_label=0))
    assert r1.decision == lcg.CapDecision.ALLOW
    # Agent fetch with SECRET taint → rule predicate fails → default DENY.
    r2 = gate.check(_ctx(tool_call_name="fetch_url", taint_label=2))
    assert r2.decision == lcg.CapDecision.DENY
    # Non-fetch tool → rule predicate fails → default DENY.
    r3 = gate.check(_ctx(tool_call_name="send_email", taint_label=0))
    assert r3.decision == lcg.CapDecision.DENY


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    g = skg.ScopeKernelGate()
    g.add_mapping(skg.ScopeMapping("a", "NET_RAW", reason="r"))
    g.add_mapping(skg.ScopeMapping("b", "SYS_ADMIN", reason="r"))
    gate1 = lcg.CapGate()
    gate2 = lcg.CapGate()
    g.install_into(gate1, {"a"})
    g.install_into(gate2, {"a", "b"})
    g.install_into(gate2, {"nonexistent"})

    stats = g.snapshot_stats()
    assert stats.mappings_registered == 2
    assert stats.install_calls == 3
    assert stats.rules_compiled == 1 + 2 + 0
    assert stats.install_no_matching_scopes == 1


def test_snapshot_stats_returns_copy():
    g = skg.ScopeKernelGate()
    s1 = g.snapshot_stats()
    g.add_mapping(skg.ScopeMapping("x", "NET_RAW", reason="r"))
    s2 = g.snapshot_stats()
    assert s1.mappings_registered == 0
    assert s2.mappings_registered == 1
