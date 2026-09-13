"""
backend/tests/security/test_policy_compiler.py

Sprint 16 / Item B4 — policy DSL compiler tests.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PC_PATH = _REPO_ROOT / "backend" / "security" / "policy_compiler.py"
_LCG_PATH = _REPO_ROOT / "backend" / "security" / "lsm_cap_gate.py"

_pc_spec = importlib.util.spec_from_file_location("vos3_pc_under_test", _PC_PATH)
pc = importlib.util.module_from_spec(_pc_spec)
sys.modules["vos3_pc_under_test"] = pc
_pc_spec.loader.exec_module(pc)

# IMPORTANT: use the EXACT same lsm_cap_gate module instance the compiler
# uses internally — otherwise CapContext/LinuxCapability identities differ
# and isinstance/enum checks fail. _load_lcg() caches the singleton.
lcg = pc._load_lcg()


def _ctx(**kw):
    base = dict(
        capability=lcg.LinuxCapability.NET_RAW,
        process_id=1234,
        comm="agent-worker",
        exe_path="/opt/vos3/bin",
        tool_call_name=None,
        taint_label=0,
        timestamp_ns=time.time_ns(),
    )
    base.update(kw)
    return lcg.CapContext(**base)


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def test_compile_rejects_non_dict_spec():
    with pytest.raises(pc.PolicyCompileError):
        pc.compile_policy([])  # type: ignore[arg-type]


def test_compile_rejects_missing_policy_key():
    with pytest.raises(pc.PolicyCompileError, match="policy"):
        pc.compile_policy({})


def test_compile_rejects_non_list_policy():
    with pytest.raises(pc.PolicyCompileError):
        pc.compile_policy({"policy": "not-a-list"})


def test_compile_rejects_unknown_entry_key():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "INVALID": "x",
            }
        ]
    }
    with pytest.raises(pc.PolicyCompileError, match="unknown keys"):
        pc.compile_policy(spec)


def test_compile_rejects_missing_required_keys():
    with pytest.raises(pc.PolicyCompileError, match="missing required key"):
        pc.compile_policy({"policy": [{"capability": "NET_RAW"}]})


def test_compile_rejects_unknown_capability():
    spec = {
        "policy": [{"capability": "NOT_A_REAL_CAP", "decision": "ALLOW", "reason": "r"}]
    }
    with pytest.raises(pc.PolicyCompileError, match="unknown capability"):
        pc.compile_policy(spec)


def test_compile_rejects_unknown_decision():
    spec = {"policy": [{"capability": "NET_RAW", "decision": "MAYBE", "reason": "r"}]}
    with pytest.raises(pc.PolicyCompileError, match="unknown decision"):
        pc.compile_policy(spec)


def test_compile_rejects_empty_reason():
    spec = {"policy": [{"capability": "NET_RAW", "decision": "ALLOW", "reason": ""}]}
    with pytest.raises(pc.PolicyCompileError):
        pc.compile_policy(spec)


# ---------------------------------------------------------------------------
# match block validation
# ---------------------------------------------------------------------------


def test_compile_match_unknown_key_rejected():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"weird_key": "x"},
            }
        ]
    }
    with pytest.raises(pc.PolicyCompileError, match="unknown match keys"):
        pc.compile_policy(spec)


def test_compile_match_invalid_taint_label():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"taint_at_most": "NOT_A_LABEL"},
            }
        ]
    }
    with pytest.raises(pc.PolicyCompileError, match="unknown taint label"):
        pc.compile_policy(spec)


def test_compile_rate_limit_missing_fields():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"rate_limit": {"max_calls": 10}},
            }
        ]
    }
    with pytest.raises(pc.PolicyCompileError, match="max_calls"):
        pc.compile_policy(spec)


def test_compile_rate_limit_invalid_values():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"rate_limit": {"max_calls": 0, "window_ms": 100}},
            }
        ]
    }
    with pytest.raises(pc.PolicyCompileError):
        pc.compile_policy(spec)


# ---------------------------------------------------------------------------
# Compile happy path + behavior
# ---------------------------------------------------------------------------


def test_compile_empty_policy_returns_empty_list():
    rules = pc.compile_policy({"policy": []})
    assert rules == []


def test_compile_minimal_entry_uses_always_predicate():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
            }
        ]
    }
    rules = pc.compile_policy(spec)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.capability == lcg.LinuxCapability.NET_RAW
    assert rule.decision == lcg.CapDecision.ALLOW
    # No match block → predicate matches anything.
    assert rule.predicate(_ctx())


def test_compile_with_tool_match_compiles_tool_predicate():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"tool": "fetch_*"},
            }
        ]
    }
    rules = pc.compile_policy(spec)
    assert rules[0].predicate(_ctx(tool_call_name="fetch_url"))
    assert not rules[0].predicate(_ctx(tool_call_name="send_email"))
    assert not rules[0].predicate(_ctx(tool_call_name=None))


def test_compile_with_process_match_compiles_process_predicate():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"process_comm": "agent-*", "exe_prefix": "/opt/vos3/"},
            }
        ]
    }
    rules = pc.compile_policy(spec)
    assert rules[0].predicate(_ctx(comm="agent-worker", exe_path="/opt/vos3/bin"))
    assert not rules[0].predicate(_ctx(comm="other-process", exe_path="/opt/vos3/bin"))


def test_compile_with_taint_match_compiles_taint_predicate():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"taint_at_most": "UNTRUSTED"},
            }
        ]
    }
    rules = pc.compile_policy(spec)
    assert rules[0].predicate(_ctx(taint_label=0))
    assert rules[0].predicate(_ctx(taint_label=1))  # UNTRUSTED
    assert not rules[0].predicate(_ctx(taint_label=2))  # SECRET


def test_compile_composite_match_ands_all_predicates():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {
                    "tool": "fetch_*",
                    "process_comm": "agent-*",
                    "taint_at_most": "UNTRUSTED",
                },
            }
        ]
    }
    rules = pc.compile_policy(spec)
    pred = rules[0].predicate
    # All match → True.
    assert pred(_ctx(tool_call_name="fetch_url", comm="agent-worker", taint_label=1))
    # One fails (tool) → False.
    assert not pred(_ctx(tool_call_name="send_email", comm="agent-worker"))
    # Another fails (taint) → False.
    assert not pred(
        _ctx(tool_call_name="fetch_url", comm="agent-worker", taint_label=3)
    )


def test_compile_with_rate_limit_enforces_budget():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "r",
                "match": {"rate_limit": {"max_calls": 2, "window_ms": 1000}},
            }
        ]
    }
    rules = pc.compile_policy(spec)
    assert rules[0].predicate(_ctx())
    assert rules[0].predicate(_ctx())
    # 3rd within window — budget exhausted.
    assert not rules[0].predicate(_ctx())


def test_compile_multiple_entries_preserve_order():
    spec = {
        "policy": [
            {"capability": "NET_RAW", "decision": "ALLOW", "reason": "first"},
            {"capability": "SYS_ADMIN", "decision": "DENY", "reason": "second"},
        ]
    }
    rules = pc.compile_policy(spec)
    assert len(rules) == 2
    assert rules[0].reason == "first"
    assert rules[1].reason == "second"


# ---------------------------------------------------------------------------
# Integration with B3 CapGate
# ---------------------------------------------------------------------------


def test_compiled_rules_integrate_with_capgate():
    spec = {
        "policy": [
            {
                "capability": "NET_RAW",
                "decision": "ALLOW",
                "reason": "agent_fetch",
                "match": {"tool": "fetch_*", "taint_at_most": "UNTRUSTED"},
            },
            {"capability": "NET_RAW", "decision": "DENY", "reason": "default"},
        ]
    }
    rules = pc.compile_policy(spec)
    gate = lcg.CapGate(default_decision=lcg.CapDecision.DENY)
    gate.add_rules(rules)
    # Agent fetch with PUBLIC taint → first rule allows.
    r1 = gate.check(_ctx(tool_call_name="fetch_url", taint_label=0))
    assert r1.decision == lcg.CapDecision.ALLOW
    assert r1.matched_rule_index == 0
    # Same agent with SECRET taint → first rule doesn't match (taint > UNTRUSTED)
    # → falls through to second (DENY).
    r2 = gate.check(_ctx(tool_call_name="fetch_url", taint_label=2))
    assert r2.decision == lcg.CapDecision.DENY
    assert r2.matched_rule_index == 1
