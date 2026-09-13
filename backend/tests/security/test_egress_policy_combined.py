"""
backend/tests/security/test_egress_policy_combined.py

Sprint 16 / Item H1 — combined egress policy tests.

Uses stub DNS pinner + stub taint engine to exercise the four-outcome
matrix (ALLOWED / DENY_DNS_PIN / DENY_TAINTED / DENY_BOTH) without
pulling in the full runtime_firewall + IFC engine.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EGP_PATH = _REPO_ROOT / "backend" / "security" / "egress_policy_combined.py"
_spec = importlib.util.spec_from_file_location("vos3_egp_under_test", _EGP_PATH)
egp = importlib.util.module_from_spec(_spec)
sys.modules["vos3_egp_under_test"] = egp
_spec.loader.exec_module(egp)


# ---------------------------------------------------------------------------
# Stubs that mimic the C7 + H4 interfaces
# ---------------------------------------------------------------------------


class _StubKind(IntEnum):
    ALLOW = 0
    DENY = 1


@dataclass(frozen=True)
class _StubDecision:
    kind: _StubKind
    reason: str = "stub"


@dataclass
class _StubTaintEngine:
    next_kind: _StubKind = _StubKind.ALLOW
    next_reason: str = "label_within_policy"

    def check_egress(self, blob, sink) -> _StubDecision:
        return _StubDecision(kind=self.next_kind, reason=self.next_reason)


class _StubDnsPinner:
    def __init__(self, result: egp.DnsPinResult, reason: str = "stub"):
        self._result = result
        self._reason = reason

    def check_pin(self, host: str):
        return self._result, self._reason


@dataclass(frozen=True)
class _StubBlob:
    sha256: str = "deadbeef" * 8
    label: int = 1


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------


def test_init_requires_taint_engine():
    with pytest.raises(ValueError):
        egp.CombinedEgressGate(taint_engine=None)


def test_init_validates_taint_engine_protocol():
    class _MissingMethod:
        pass

    with pytest.raises(TypeError, match="check_egress"):
        egp.CombinedEgressGate(taint_engine=_MissingMethod())


def test_init_default_dns_pinner_constructed():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    # Default pinner is the in-memory first-resolution stub.
    assert gate._dns is not None


# ---------------------------------------------------------------------------
# Check input validation
# ---------------------------------------------------------------------------


def test_check_rejects_none_blob():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    with pytest.raises(ValueError, match="blob"):
        gate.check(blob=None, target_host="example.com", sink="NET")


def test_check_rejects_empty_host():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    with pytest.raises(ValueError, match="target_host"):
        gate.check(blob=_StubBlob(), target_host="", sink="NET")


def test_check_rejects_non_str_host():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    with pytest.raises(ValueError):
        gate.check(blob=_StubBlob(), target_host=123, sink="NET")  # type: ignore[arg-type]


def test_check_rejects_none_sink():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    with pytest.raises(ValueError, match="sink"):
        gate.check(blob=_StubBlob(), target_host="x.com", sink=None)


# ---------------------------------------------------------------------------
# Four-outcome matrix
# ---------------------------------------------------------------------------


def test_outcome_allowed_when_both_pass():
    gate = egp.CombinedEgressGate(
        taint_engine=_StubTaintEngine(next_kind=_StubKind.ALLOW),
        dns_pinner=_StubDnsPinner(result=egp.DnsPinResult.OK, reason="ok"),
    )
    d = gate.check(blob=_StubBlob(), target_host="api.example.com", sink="NET")
    assert d.outcome == egp.CombinedEgressOutcome.ALLOWED
    assert d.dns_pin_reason == ""
    assert d.taint_reason == ""


def test_outcome_deny_dns_pin_when_only_dns_rejects():
    gate = egp.CombinedEgressGate(
        taint_engine=_StubTaintEngine(next_kind=_StubKind.ALLOW),
        dns_pinner=_StubDnsPinner(
            result=egp.DnsPinResult.DENY_REBIND, reason="rebind_seen"
        ),
    )
    d = gate.check(blob=_StubBlob(), target_host="evil.example.com", sink="NET")
    assert d.outcome == egp.CombinedEgressOutcome.DENY_DNS_PIN
    assert d.dns_pin_reason == "rebind_seen"


def test_outcome_deny_tainted_destination_when_only_taint_rejects():
    gate = egp.CombinedEgressGate(
        taint_engine=_StubTaintEngine(
            next_kind=_StubKind.DENY, next_reason="label_exceeds_sink_policy"
        ),
        dns_pinner=_StubDnsPinner(result=egp.DnsPinResult.OK),
    )
    d = gate.check(blob=_StubBlob(), target_host="api.example.com", sink="NET")
    assert d.outcome == egp.CombinedEgressOutcome.DENY_TAINTED_DESTINATION
    assert d.taint_reason == "label_exceeds_sink_policy"


def test_outcome_deny_both_when_both_reject():
    gate = egp.CombinedEgressGate(
        taint_engine=_StubTaintEngine(
            next_kind=_StubKind.DENY, next_reason="label_exceeds_sink_policy"
        ),
        dns_pinner=_StubDnsPinner(
            result=egp.DnsPinResult.DENY_REBIND, reason="rebind_seen"
        ),
    )
    d = gate.check(blob=_StubBlob(), target_host="evil.example.com", sink="NET")
    assert d.outcome == egp.CombinedEgressOutcome.DENY_BOTH
    assert d.dns_pin_reason == "rebind_seen"
    assert d.taint_reason == "label_exceeds_sink_policy"


# ---------------------------------------------------------------------------
# Decision content
# ---------------------------------------------------------------------------


def test_decision_carries_blob_sha_and_label():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    blob = _StubBlob(sha256="abc" * 21 + "x", label=3)
    d = gate.check(blob=blob, target_host="x.com", sink="NET")
    assert d.blob_sha256 == blob.sha256
    assert d.taint_label == 3


def test_decision_carries_target_host_and_sink():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    d = gate.check(blob=_StubBlob(), target_host="api.example.com", sink="USER_STDOUT")
    assert d.target_host == "api.example.com"
    assert d.sink == "USER_STDOUT"


# ---------------------------------------------------------------------------
# DefaultDnsPinner
# ---------------------------------------------------------------------------


def test_default_dns_pinner_first_resolution_pins():
    pinner = egp.DefaultDnsPinner()
    result, _reason = pinner.check_pin("example.com")
    assert result == egp.DnsPinResult.OK
    # Second call to same host — still pinned (resolves same value).
    result2, _ = pinner.check_pin("example.com")
    assert result2 == egp.DnsPinResult.OK


def test_default_dns_pinner_empty_host_denied():
    pinner = egp.DefaultDnsPinner()
    result, reason = pinner.check_pin("")
    assert result == egp.DnsPinResult.DENY_REBIND


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    # ALLOWED case.
    gate.check(blob=_StubBlob(), target_host="ok.example.com", sink="NET")
    # DENY_DNS_PIN case.
    gate._dns = _StubDnsPinner(result=egp.DnsPinResult.DENY_REBIND)
    gate.check(blob=_StubBlob(), target_host="evil.example.com", sink="NET")
    # DENY_TAINTED case.
    gate._dns = _StubDnsPinner(result=egp.DnsPinResult.OK)
    gate._taint = _StubTaintEngine(next_kind=_StubKind.DENY)
    gate.check(blob=_StubBlob(), target_host="api.example.com", sink="NET")
    # DENY_BOTH case.
    gate._dns = _StubDnsPinner(result=egp.DnsPinResult.DENY_REBIND)
    gate._taint = _StubTaintEngine(next_kind=_StubKind.DENY)
    gate.check(blob=_StubBlob(), target_host="evil.example.com", sink="NET")

    s = gate.snapshot_stats()
    assert s.total_checks == 4
    assert s.allowed == 1
    assert s.deny_dns_pin == 1
    assert s.deny_tainted == 1
    assert s.deny_both == 1


def test_snapshot_stats_returns_copy():
    gate = egp.CombinedEgressGate(taint_engine=_StubTaintEngine())
    s1 = gate.snapshot_stats()
    gate.check(blob=_StubBlob(), target_host="ok.example.com", sink="NET")
    s2 = gate.snapshot_stats()
    assert s1.total_checks == 0
    assert s2.total_checks == 1
