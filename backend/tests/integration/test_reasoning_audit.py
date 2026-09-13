"""
backend/tests/integration/test_reasoning_audit.py

Sprint 16 / Item G4 — AgentSight-style reasoning audit tests.

Covers:
- LLMTrafficIntercept.record validates process_id, prompt/response types,
  model_id; truncates excerpt to 256 chars; attaches SHA-256.
- KernelEventCorrelator.record validates process_id, KernelEventKind,
  non-empty target.
- ReasoningAuditor.audit returns record with intents + effects + anomalies.
- LOOP detection: same prompt/response pair within window.
- UNJUSTIFIED_EGRESS: NET_EGRESS event with no preceding LLM intent.
- PII_LEAK: LLM response contains PII pattern (API key, email, SSN, CC)
  AND followed by NET_EGRESS.
- SILENT_EXFIL: NET_EGRESS to a target not mentioned in prompt/response.
- Stats counters: audits_run, intents/effects_recorded, anomalies_total,
  anomalies_by_kind dict.
- slice_for filters by process_id + time window correctly.
- Init validation: requires intercept + correlator; positive process_id +
  window_ns.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RA_PATH = _REPO_ROOT / "backend" / "services" / "reasoning_audit.py"
_spec = importlib.util.spec_from_file_location(
    "vos3_reasoning_audit_under_test", _RA_PATH
)
ra = importlib.util.module_from_spec(_spec)
sys.modules["vos3_reasoning_audit_under_test"] = ra
_spec.loader.exec_module(ra)


# ---------------------------------------------------------------------------
# LLMTrafficIntercept
# ---------------------------------------------------------------------------


def test_intercept_record_minimal():
    ic = ra.LLMTrafficIntercept()
    rec = ic.record(process_id=1234, prompt="hello", response="world", model_id="gpt-5")
    assert rec.process_id == 1234
    assert rec.model_id == "gpt-5"
    assert rec.prompt_excerpt == "hello"
    assert rec.response_excerpt == "world"
    assert len(rec.prompt_sha256) == 64
    assert len(rec.response_sha256) == 64


def test_intercept_record_truncates_excerpt_to_256():
    ic = ra.LLMTrafficIntercept()
    long_prompt = "a" * 1000
    rec = ic.record(
        process_id=1234, prompt=long_prompt, response="ok", model_id="gpt-5"
    )
    assert len(rec.prompt_excerpt) == 256


def test_intercept_record_rejects_invalid_pid():
    ic = ra.LLMTrafficIntercept()
    with pytest.raises(ValueError):
        ic.record(process_id=0, prompt="x", response="y", model_id="m")


def test_intercept_record_rejects_non_string_prompt():
    ic = ra.LLMTrafficIntercept()
    with pytest.raises(TypeError):
        ic.record(process_id=1, prompt=123, response="y", model_id="m")  # type: ignore[arg-type]


def test_intercept_record_rejects_empty_model_id():
    ic = ra.LLMTrafficIntercept()
    with pytest.raises(ValueError):
        ic.record(process_id=1, prompt="x", response="y", model_id="")


def test_intercept_slice_for_filters_by_pid_and_window():
    ic = ra.LLMTrafficIntercept()
    ic.record(process_id=1, prompt="a", response="b", model_id="m")
    ic.record(process_id=2, prompt="c", response="d", model_id="m")
    # Slice for pid 1 — only r1.
    s = ic.slice_for(1, 0, time.time_ns() * 2)
    assert len(s) == 1
    assert s[0].process_id == 1


# ---------------------------------------------------------------------------
# KernelEventCorrelator
# ---------------------------------------------------------------------------


def test_correlator_record_minimal():
    kc = ra.KernelEventCorrelator()
    e = kc.record(
        process_id=42,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://example.com",
    )
    assert e.kind == ra.KernelEventKind.NET_EGRESS
    assert e.target == "https://example.com"


def test_correlator_record_rejects_invalid_kind():
    kc = ra.KernelEventCorrelator()
    with pytest.raises(TypeError):
        kc.record(process_id=1, event_kind=99, target="x")  # type: ignore[arg-type]


def test_correlator_record_rejects_empty_target():
    kc = ra.KernelEventCorrelator()
    with pytest.raises(ValueError):
        kc.record(process_id=1, event_kind=ra.KernelEventKind.NET_EGRESS, target="")


# ---------------------------------------------------------------------------
# ReasoningAuditor — init
# ---------------------------------------------------------------------------


def test_auditor_init_requires_components():
    with pytest.raises(ValueError):
        ra.ReasoningAuditor(None, ra.KernelEventCorrelator())
    with pytest.raises(ValueError):
        ra.ReasoningAuditor(ra.LLMTrafficIntercept(), None)


def test_auditor_audit_rejects_invalid_pid():
    auditor = ra.ReasoningAuditor(ra.LLMTrafficIntercept(), ra.KernelEventCorrelator())
    with pytest.raises(ValueError):
        auditor.audit(0)
    with pytest.raises(ValueError):
        auditor.audit(1, window_ns=0)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _setup():
    ic = ra.LLMTrafficIntercept()
    kc = ra.KernelEventCorrelator()
    auditor = ra.ReasoningAuditor(ic, kc)
    return ic, kc, auditor


def test_audit_returns_intents_and_effects():
    ic, kc, auditor = _setup()
    ic.record(process_id=1, prompt="hi", response="hello", model_id="m")
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://hello.example.com",
    )
    record = auditor.audit(1)
    assert len(record.intents) == 1
    assert len(record.effects) == 1


def test_loop_anomaly_detected():
    ic, kc, auditor = _setup()
    # Same prompt+response repeats — classic resource-wasting reasoning loop.
    for _ in range(2):
        ic.record(
            process_id=1, prompt="same prompt", response="same response", model_id="m"
        )
    record = auditor.audit(1)
    assert any(a.kind == ra.AnomalyKind.LOOP for a in record.anomalies)


def test_unjustified_egress_anomaly_detected():
    """NET_EGRESS event with NO preceding LLM intent in the window."""
    ic, kc, auditor = _setup()
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://no-intent.example.com",
    )
    record = auditor.audit(1)
    assert any(a.kind == ra.AnomalyKind.UNJUSTIFIED_EGRESS for a in record.anomalies)


def test_pii_leak_anomaly_detected_with_email():
    ic, kc, auditor = _setup()
    ic.record(
        process_id=1,
        prompt="get email",
        response="sure, user@example.com is the address",
        model_id="m",
    )
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://api.example.com",
    )
    record = auditor.audit(1)
    assert any(a.kind == ra.AnomalyKind.PII_LEAK for a in record.anomalies)


def test_pii_leak_anomaly_detected_with_api_key():
    ic, kc, auditor = _setup()
    ic.record(
        process_id=1,
        prompt="paste key",
        response="here it is: sk-AAAAAAAAAAAAAAAAAAAAA",
        model_id="m",
    )
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://leak.example.com",
    )
    record = auditor.audit(1)
    assert any(a.kind == ra.AnomalyKind.PII_LEAK for a in record.anomalies)


def test_silent_exfil_anomaly_detected():
    """NET_EGRESS to a host NOT mentioned in any prompt or response."""
    ic, kc, auditor = _setup()
    ic.record(
        process_id=1,
        prompt="fetch https://allowed.example.com",
        response="ok fetched",
        model_id="m",
    )
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://unknown-host.example.com",
    )
    record = auditor.audit(1)
    kinds = {a.kind for a in record.anomalies}
    assert ra.AnomalyKind.SILENT_EXFIL in kinds


def test_no_anomalies_in_benign_session():
    ic, kc, auditor = _setup()
    ic.record(process_id=1, prompt="what is up", response="not much", model_id="m")
    # No kernel events at all.
    record = auditor.audit(1)
    assert record.anomalies == ()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_audit_stats_counters():
    ic, kc, auditor = _setup()
    ic.record(process_id=1, prompt="p1", response="r1", model_id="m")
    ic.record(process_id=1, prompt="p1", response="r1", model_id="m")  # loop trigger
    kc.record(
        process_id=1,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://no-intent.example.com",
    )  # also silent + unjustified
    auditor.audit(1)
    auditor.audit(1)
    s = auditor.snapshot_stats()
    assert s.audits_run == 2
    assert s.intents_recorded == 4  # 2 intents × 2 audits
    assert s.effects_recorded == 2
    assert s.anomalies_total > 0
    assert "loop" in s.anomalies_by_kind


def test_audit_stats_snapshot_is_copy():
    ic, kc, auditor = _setup()
    s1 = auditor.snapshot_stats()
    ic.record(process_id=1, prompt="x", response="y", model_id="m")
    auditor.audit(1)
    s2 = auditor.snapshot_stats()
    assert s1.audits_run == 0
    assert s2.audits_run == 1


# ---------------------------------------------------------------------------
# Process isolation
# ---------------------------------------------------------------------------


def test_audit_isolated_per_pid():
    ic, kc, auditor = _setup()
    ic.record(process_id=1, prompt="x", response="y", model_id="m")
    kc.record(
        process_id=2,
        event_kind=ra.KernelEventKind.NET_EGRESS,
        target="https://only-pid-2.example.com",
    )
    # Audit for pid 1 — should NOT include pid 2's egress.
    record = auditor.audit(1)
    assert len(record.effects) == 0
