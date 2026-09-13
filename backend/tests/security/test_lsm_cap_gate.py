"""
backend/tests/security/test_lsm_cap_gate.py

Sprint 16 / Item B3 — eBPF LSM cap-gate model tests.

Covers:
- LinuxCapability enum values match linux/capability.h subset.
- CapDecision enum.
- CapContext + CapGateRule construction.
- Predicate factories:
    process_match (comm glob + exe prefix, single or combined)
    tool_call_match (regex; None tool_call_name → no-match)
    taint_at_most (boundary)
    always
    rate_limit (max_calls per window_ms; per-scope key)
- Rate-limit budget exhaustion + recovery after window.
- Rate-limit init rejects max_calls<=0 + window_ms<=0.
- CapGate.add_rule validates types; .check returns CapCheckResult with
  matched index + reason.
- Rules evaluated in insertion order; first match wins.
- No matching rule → default_decision applied.
- Stats counters: total_checks, allowed, denied, by_capability dict.
- Cross-capability: rule for NET_RAW doesn't fire for NET_ADMIN check.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LCG_PATH = _REPO_ROOT / "backend" / "security" / "lsm_cap_gate.py"
_spec = importlib.util.spec_from_file_location("vos3_lcg_under_test", _LCG_PATH)
lcg = importlib.util.module_from_spec(_spec)
sys.modules["vos3_lcg_under_test"] = lcg
_spec.loader.exec_module(lcg)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_linux_capability_values():
    assert int(lcg.LinuxCapability.CHOWN) == 0
    assert int(lcg.LinuxCapability.NET_BIND_SERVICE) == 10
    assert int(lcg.LinuxCapability.NET_RAW) == 13
    assert int(lcg.LinuxCapability.SYS_ADMIN) == 21
    assert int(lcg.LinuxCapability.BPF) == 39


def test_cap_decision_values():
    assert int(lcg.CapDecision.ALLOW) == 0
    assert int(lcg.CapDecision.DENY) == 1


def _ctx(
    cap=lcg.LinuxCapability.NET_RAW,
    pid=1234,
    comm="agent-worker",
    exe="/usr/bin/agent",
    tool=None,
    taint=0,
):
    return lcg.CapContext(
        capability=cap,
        process_id=pid,
        comm=comm,
        exe_path=exe,
        tool_call_name=tool,
        taint_label=taint,
        timestamp_ns=time.time_ns(),
    )


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def test_predicate_process_match_comm_glob():
    p = lcg.predicate_process_match(comm_glob="agent-*")
    assert p(_ctx(comm="agent-worker"))
    assert not p(_ctx(comm="random-binary"))


def test_predicate_process_match_exe_prefix():
    p = lcg.predicate_process_match(exe_prefix="/opt/vos3/")
    assert p(_ctx(exe="/opt/vos3/agent-binary"))
    assert not p(_ctx(exe="/usr/bin/other"))


def test_predicate_process_match_combined():
    p = lcg.predicate_process_match(comm_glob="agent-*", exe_prefix="/opt/")
    assert p(_ctx(comm="agent-worker", exe="/opt/agent"))
    assert not p(_ctx(comm="agent-worker", exe="/usr/bin"))


def test_predicate_tool_call_match():
    p = lcg.predicate_tool_call_match(tool_regex=r"^fetch_(url|file)$")
    assert p(_ctx(tool="fetch_url"))
    assert p(_ctx(tool="fetch_file"))
    assert not p(_ctx(tool="send_email"))
    assert not p(_ctx(tool=None))


def test_predicate_taint_at_most():
    p = lcg.predicate_taint_at_most(max_label=1)
    assert p(_ctx(taint=0))
    assert p(_ctx(taint=1))
    assert not p(_ctx(taint=2))
    assert not p(_ctx(taint=3))


def test_predicate_always():
    p = lcg.predicate_always()
    assert p(_ctx())


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def test_rate_limit_within_budget():
    p = lcg.predicate_rate_limit(max_calls=3, window_ms=1000)
    for _ in range(3):
        assert p(_ctx())


def test_rate_limit_budget_exhausted():
    p = lcg.predicate_rate_limit(max_calls=2, window_ms=1000)
    assert p(_ctx())
    assert p(_ctx())
    # 3rd call within window — exhausted.
    assert not p(_ctx())


def test_rate_limit_recovers_after_window():
    p = lcg.predicate_rate_limit(max_calls=1, window_ms=50)
    assert p(_ctx())
    assert not p(_ctx())  # exhausted
    time.sleep(0.06)
    assert p(_ctx())  # window elapsed; recovered


def test_rate_limit_init_validates():
    with pytest.raises(ValueError):
        lcg.predicate_rate_limit(max_calls=0, window_ms=100)
    with pytest.raises(ValueError):
        lcg.predicate_rate_limit(max_calls=1, window_ms=0)


def test_rate_limit_per_scope_independent():
    """Different process_id should have independent budgets."""
    p = lcg.predicate_rate_limit(max_calls=1, window_ms=1000)
    assert p(_ctx(pid=1))
    assert not p(_ctx(pid=1))  # pid 1 exhausted
    assert p(_ctx(pid=2))  # pid 2 has its own budget


# ---------------------------------------------------------------------------
# CapGate
# ---------------------------------------------------------------------------


def test_add_rule_validates_types():
    gate = lcg.CapGate()
    with pytest.raises(TypeError):
        gate.add_rule("not-a-rule")  # type: ignore[arg-type]


def test_add_rule_validates_capability_type():
    gate = lcg.CapGate()
    bad_rule = lcg.CapGateRule(
        capability="NET_RAW",  # type: ignore[arg-type]
        predicate=lcg.predicate_always(),
        decision=lcg.CapDecision.ALLOW,
        reason="x",
    )
    with pytest.raises(TypeError):
        gate.add_rule(bad_rule)


def test_check_no_rules_returns_default_deny():
    gate = lcg.CapGate(default_decision=lcg.CapDecision.DENY)
    result = gate.check(_ctx())
    assert result.decision == lcg.CapDecision.DENY
    assert result.matched_rule_index is None
    assert result.reason == "default_deny_no_matching_rule"


def test_check_no_rules_returns_default_allow_when_configured():
    gate = lcg.CapGate(default_decision=lcg.CapDecision.ALLOW)
    result = gate.check(_ctx())
    assert result.decision == lcg.CapDecision.ALLOW
    assert result.reason == "default_allow"


def test_check_first_matching_rule_wins():
    gate = lcg.CapGate()
    gate.add_rule(
        lcg.CapGateRule(
            capability=lcg.LinuxCapability.NET_RAW,
            predicate=lcg.predicate_process_match(comm_glob="agent-*"),
            decision=lcg.CapDecision.ALLOW,
            reason="agent_allowed_net_raw",
        )
    )
    gate.add_rule(
        lcg.CapGateRule(
            capability=lcg.LinuxCapability.NET_RAW,
            predicate=lcg.predicate_always(),
            decision=lcg.CapDecision.DENY,
            reason="default_deny_net_raw",
        )
    )
    result = gate.check(_ctx(comm="agent-worker"))
    assert result.decision == lcg.CapDecision.ALLOW
    assert result.matched_rule_index == 0


def test_check_skips_non_matching_capability():
    """Rule for NET_RAW must not fire for NET_ADMIN check."""
    gate = lcg.CapGate(default_decision=lcg.CapDecision.DENY)
    gate.add_rule(
        lcg.CapGateRule(
            capability=lcg.LinuxCapability.NET_RAW,
            predicate=lcg.predicate_always(),
            decision=lcg.CapDecision.ALLOW,
            reason="net_raw_only",
        )
    )
    result = gate.check(_ctx(cap=lcg.LinuxCapability.NET_ADMIN))
    assert result.decision == lcg.CapDecision.DENY
    assert result.matched_rule_index is None


def test_check_rejects_non_context():
    gate = lcg.CapGate()
    with pytest.raises(TypeError):
        gate.check("not-a-context")  # type: ignore[arg-type]


def test_add_rules_bulk():
    gate = lcg.CapGate()
    indices = gate.add_rules(
        [
            lcg.CapGateRule(
                lcg.LinuxCapability.NET_RAW,
                lcg.predicate_always(),
                lcg.CapDecision.ALLOW,
                "r1",
            ),
            lcg.CapGateRule(
                lcg.LinuxCapability.SYS_ADMIN,
                lcg.predicate_always(),
                lcg.CapDecision.DENY,
                "r2",
            ),
        ]
    )
    assert indices == [0, 1]
    assert gate.rule_count() == 2


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    gate = lcg.CapGate(default_decision=lcg.CapDecision.DENY)
    gate.add_rule(
        lcg.CapGateRule(
            capability=lcg.LinuxCapability.NET_RAW,
            predicate=lcg.predicate_always(),
            decision=lcg.CapDecision.ALLOW,
            reason="allow_net_raw",
        )
    )
    # 3 NET_RAW checks → all ALLOW.
    for _ in range(3):
        gate.check(_ctx(cap=lcg.LinuxCapability.NET_RAW))
    # 2 SYS_ADMIN checks → no matching rule → default DENY.
    for _ in range(2):
        gate.check(_ctx(cap=lcg.LinuxCapability.SYS_ADMIN))
    stats = gate.snapshot_stats()
    assert stats.total_checks == 5
    assert stats.allowed == 3
    assert stats.denied == 2
    assert stats.by_capability[int(lcg.LinuxCapability.NET_RAW)] == 3
    assert stats.by_capability[int(lcg.LinuxCapability.SYS_ADMIN)] == 2


def test_snapshot_stats_returns_copy():
    gate = lcg.CapGate()
    s1 = gate.snapshot_stats()
    gate.check(_ctx())
    s2 = gate.snapshot_stats()
    assert s1.total_checks == 0
    assert s2.total_checks == 1
