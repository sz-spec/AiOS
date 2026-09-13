"""
backend/tests/security/test_ifc_engine.py

Sprint 16 / Item C7 — IFC taint-engine tests.

Covers:
- TaintLabel monotonic ordering (PUBLIC < UNTRUSTED < SECRET < TOXIC).
- TaintedBlob.make validates content/label/source_id; SHA-256 attached.
- label_source default UNTRUSTED; explicit higher label accepted.
- propagate: high-watermark — output label = max(input labels).
- propagate with no inputs → PUBLIC (synthetic-output rule).
- propagate provenance chain unions input chains + derivation site
  without duplicating entries.
- declassify: cannot RAISE label; requires reason + authorized_by;
  records marker in provenance chain.
- check_egress: ALLOW iff blob.label <= sink_max_label.
- Default policy: NETWORK denies SECRET+TOXIC; FILE/USER deny TOXIC;
  AUDIT allows everything.
- policy_set overrides per-sink max label.
- Stats counters track sources_labeled / propagations / declassifications
  / egress_allowed / egress_denied.
- Bad-type guards: non-TaintLabel sink/label, non-bytes content.
- End-to-end: tool fetch (UNTRUSTED) → agent derivation (high-watermark
  UNTRUSTED) → NETWORK egress (ALLOW) → SECRET overlay → NETWORK egress (DENY).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_IFC_PATH = _REPO_ROOT / "backend" / "security" / "ifc_engine.py"
_spec = importlib.util.spec_from_file_location("vos3_ifc_engine_under_test", _IFC_PATH)
ifc = importlib.util.module_from_spec(_spec)
sys.modules["vos3_ifc_engine_under_test"] = ifc
_spec.loader.exec_module(ifc)


# ---------------------------------------------------------------------------
# Label model
# ---------------------------------------------------------------------------


def test_taint_label_monotonic_ordering():
    assert ifc.TaintLabel.PUBLIC < ifc.TaintLabel.UNTRUSTED
    assert ifc.TaintLabel.UNTRUSTED < ifc.TaintLabel.SECRET
    assert ifc.TaintLabel.SECRET < ifc.TaintLabel.TOXIC


def test_sink_kinds_distinct():
    assert {
        ifc.SinkKind.NETWORK_EGRESS,
        ifc.SinkKind.FILE_WRITE,
        ifc.SinkKind.USER_STDOUT,
        ifc.SinkKind.AUDIT_LOG,
    } == set(ifc.SinkKind)


# ---------------------------------------------------------------------------
# TaintedBlob construction
# ---------------------------------------------------------------------------


def test_tainted_blob_make_attaches_sha256():
    blob = ifc.TaintedBlob.make(b"hello", ifc.TaintLabel.UNTRUSTED, "src")
    assert blob.content == b"hello"
    assert blob.label == ifc.TaintLabel.UNTRUSTED
    assert blob.source_id == "src"
    assert len(blob.sha256) == 64


def test_tainted_blob_rejects_non_bytes_content():
    with pytest.raises(TypeError):
        ifc.TaintedBlob.make("not-bytes", ifc.TaintLabel.PUBLIC, "src")


def test_tainted_blob_rejects_non_taintlabel():
    with pytest.raises(TypeError):
        ifc.TaintedBlob.make(b"x", 99, "src")  # type: ignore[arg-type]


def test_tainted_blob_rejects_empty_source_id():
    with pytest.raises(ValueError):
        ifc.TaintedBlob.make(b"x", ifc.TaintLabel.PUBLIC, "")


# ---------------------------------------------------------------------------
# label_source
# ---------------------------------------------------------------------------


def test_label_source_default_untrusted():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:fetch_url", b"page content")
    assert blob.label == ifc.TaintLabel.UNTRUSTED
    assert blob.provenance_chain == ("tool:fetch_url",)


def test_label_source_explicit_higher_label():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:web_search", b"<injection>", label=ifc.TaintLabel.TOXIC)
    assert blob.label == ifc.TaintLabel.TOXIC


def test_label_source_increments_stat():
    e = ifc.TaintEngine()
    e.label_source("src1", b"a")
    e.label_source("src2", b"b")
    assert e.snapshot_stats().sources_labeled == 2


# ---------------------------------------------------------------------------
# Propagate — high-watermark
# ---------------------------------------------------------------------------


def test_propagate_picks_max_of_input_labels():
    e = ifc.TaintEngine()
    a = e.label_source("tool:A", b"a", label=ifc.TaintLabel.UNTRUSTED)
    b = e.label_source("tool:B", b"b", label=ifc.TaintLabel.SECRET)
    out = e.propagate([a, b], b"derived")
    assert out.label == ifc.TaintLabel.SECRET  # max(UNTRUSTED, SECRET) = SECRET


def test_propagate_single_input_preserves_label():
    e = ifc.TaintEngine()
    a = e.label_source("tool:A", b"a", label=ifc.TaintLabel.TOXIC)
    out = e.propagate([a], b"derived")
    assert out.label == ifc.TaintLabel.TOXIC


def test_propagate_no_inputs_yields_public():
    """Synthetic output (e.g. the privileged LLM's own reasoning)
    with no tainted inputs gets PUBLIC."""
    e = ifc.TaintEngine()
    out = e.propagate([], b"reasoning")
    assert out.label == ifc.TaintLabel.PUBLIC


def test_propagate_unions_provenance_chains_without_dupes():
    e = ifc.TaintEngine()
    a = e.label_source("tool:A", b"a")
    b = e.label_source("tool:B", b"b")
    # Derive once.
    out1 = e.propagate([a, b], b"out1", derived_source_id="agent:step1")
    # Derive again from out1 + a fresh tool — chain dedup.
    c = e.label_source("tool:C", b"c")
    out2 = e.propagate([out1, c], b"out2", derived_source_id="agent:step2")
    chain = out2.provenance_chain
    assert "tool:A" in chain and "tool:B" in chain and "tool:C" in chain
    assert "agent:step1" in chain and "agent:step2" in chain
    # No duplicates.
    assert len(chain) == len(set(chain))


def test_propagate_increments_stat():
    e = ifc.TaintEngine()
    a = e.label_source("tool:A", b"a")
    e.propagate([a], b"x")
    e.propagate([a], b"y")
    assert e.snapshot_stats().propagations == 2


# ---------------------------------------------------------------------------
# Declassify
# ---------------------------------------------------------------------------


def test_declassify_lowers_label():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.SECRET)
    declassified = e.declassify(
        blob,
        ifc.TaintLabel.PUBLIC,
        reason="reviewed by operator",
        authorized_by="op@example.com",
    )
    assert declassified.label == ifc.TaintLabel.PUBLIC
    # Provenance chain records the declassification.
    assert any("declassify" in p for p in declassified.provenance_chain)


def test_declassify_cannot_raise_label():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.PUBLIC)
    with pytest.raises(ValueError, match="cannot RAISE"):
        e.declassify(
            blob, ifc.TaintLabel.SECRET, reason="upgrade attempt", authorized_by="op"
        )


def test_declassify_requires_reason_and_authorized_by():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.SECRET)
    with pytest.raises(ValueError):
        e.declassify(blob, ifc.TaintLabel.PUBLIC, reason="", authorized_by="op")
    with pytest.raises(ValueError):
        e.declassify(blob, ifc.TaintLabel.PUBLIC, reason="r", authorized_by="")


def test_declassify_increments_stat():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.SECRET)
    e.declassify(blob, ifc.TaintLabel.PUBLIC, reason="r", authorized_by="op")
    assert e.snapshot_stats().declassifications == 1


# ---------------------------------------------------------------------------
# Egress enforcement
# ---------------------------------------------------------------------------


def test_egress_allow_when_label_within_policy():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.UNTRUSTED)
    decision = e.check_egress(blob, ifc.SinkKind.NETWORK_EGRESS)
    assert decision.kind == ifc.EgressDecisionKind.ALLOW
    assert decision.reason == "label_within_policy"


def test_egress_deny_secret_to_network():
    """Default policy: NETWORK_EGRESS max = UNTRUSTED; SECRET denied."""
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.SECRET)
    decision = e.check_egress(blob, ifc.SinkKind.NETWORK_EGRESS)
    assert decision.kind == ifc.EgressDecisionKind.DENY
    assert decision.reason == "label_exceeds_sink_policy"


def test_egress_deny_toxic_to_file_write():
    """Default policy: FILE_WRITE max = SECRET; TOXIC denied."""
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.TOXIC)
    decision = e.check_egress(blob, ifc.SinkKind.FILE_WRITE)
    assert decision.kind == ifc.EgressDecisionKind.DENY


def test_egress_audit_log_accepts_all_labels():
    """AUDIT_LOG must accept TOXIC (compliance requirement)."""
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x", label=ifc.TaintLabel.TOXIC)
    decision = e.check_egress(blob, ifc.SinkKind.AUDIT_LOG)
    assert decision.kind == ifc.EgressDecisionKind.ALLOW


def test_egress_rejects_non_sink_arg():
    e = ifc.TaintEngine()
    blob = e.label_source("tool:A", b"x")
    with pytest.raises(TypeError):
        e.check_egress(blob, 999)  # type: ignore[arg-type]


def test_egress_increments_stats():
    e = ifc.TaintEngine()
    pub = e.label_source("tool:A", b"x", label=ifc.TaintLabel.PUBLIC)
    secret = e.label_source("tool:B", b"y", label=ifc.TaintLabel.SECRET)
    e.check_egress(pub, ifc.SinkKind.NETWORK_EGRESS)  # ALLOW
    e.check_egress(secret, ifc.SinkKind.NETWORK_EGRESS)  # DENY
    s = e.snapshot_stats()
    assert s.egress_checks == 2
    assert s.egress_allowed == 1
    assert s.egress_denied == 1


# ---------------------------------------------------------------------------
# Policy overrides
# ---------------------------------------------------------------------------


def test_policy_set_overrides_default():
    e = ifc.TaintEngine()
    # Tighten NETWORK egress to PUBLIC only.
    e.policy_set(ifc.SinkKind.NETWORK_EGRESS, ifc.TaintLabel.PUBLIC)
    untrusted = e.label_source("tool:A", b"x", label=ifc.TaintLabel.UNTRUSTED)
    decision = e.check_egress(untrusted, ifc.SinkKind.NETWORK_EGRESS)
    assert decision.kind == ifc.EgressDecisionKind.DENY


def test_policy_get_returns_current():
    e = ifc.TaintEngine()
    assert e.policy_get(ifc.SinkKind.NETWORK_EGRESS) == ifc.TaintLabel.UNTRUSTED
    e.policy_set(ifc.SinkKind.NETWORK_EGRESS, ifc.TaintLabel.SECRET)
    assert e.policy_get(ifc.SinkKind.NETWORK_EGRESS) == ifc.TaintLabel.SECRET


def test_policy_set_rejects_bad_types():
    e = ifc.TaintEngine()
    with pytest.raises(TypeError):
        e.policy_set(999, ifc.TaintLabel.UNTRUSTED)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        e.policy_set(ifc.SinkKind.NETWORK_EGRESS, 999)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End-to-end scenario
# ---------------------------------------------------------------------------


def test_end_to_end_tool_fetch_to_egress():
    """Realistic flow: fetch_url returns content → agent derives summary →
    egress check. Default policy permits UNTRUSTED to NETWORK."""
    e = ifc.TaintEngine()
    fetched = e.label_source("tool:fetch_url", b"<html>...</html>")
    summary = e.propagate([fetched], b"Summary of the page")
    decision = e.check_egress(summary, ifc.SinkKind.NETWORK_EGRESS)
    assert decision.kind == ifc.EgressDecisionKind.ALLOW
    # Now overlay a SECRET source — derived output should be SECRET, denied.
    secret_input = e.label_source(
        "user:credentials", b"api-key-xxx", label=ifc.TaintLabel.SECRET
    )
    contaminated = e.propagate([summary, secret_input], b"Summary + key")
    assert contaminated.label == ifc.TaintLabel.SECRET
    decision2 = e.check_egress(contaminated, ifc.SinkKind.NETWORK_EGRESS)
    assert decision2.kind == ifc.EgressDecisionKind.DENY
