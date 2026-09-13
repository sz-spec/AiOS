"""
backend/tests/security/test_taint_engine_v2.py

Sprint 17 / Cluster C-1 — byte-level taint engine tests.

Covers:
- TaintLabel + SinkKind enums match C7 values exactly (interop)
- TaintedBuffer.make invariants (len match, color range, source_id non-empty)
- label_source: uniform per-byte color; no-color globs respected;
  filename hint falls through if not matching
- label_range: lifts per-byte color in [start:end), never downgrades
- slice: preserves per-byte color in the sliced range
- concat: per-byte color preserved across boundaries
- max_color_in_range over arbitrary ranges
- max_color() over whole buffer; no-color buffer returns TOXIC (safe-fail)
- check_egress respects max_color vs sink policy (UNTRUSTED clean to
  NETWORK; SECRET denied; AUDIT accepts TOXIC)
- declassify_range requires Ed25519-signed evidence with matching
  SHA-256 binding + time window + target label
- declassify_range cannot RAISE label (ValueError)
- C7 interop: from_blob_label / to_blob_max round-trip works
- Stats counters track operations
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TE_PATH = _REPO_ROOT / "backend" / "security" / "taint_engine_v2.py"
_spec = importlib.util.spec_from_file_location("vos3_tev2_under_test", _TE_PATH)
te = importlib.util.module_from_spec(_spec)
sys.modules["vos3_tev2_under_test"] = te
_spec.loader.exec_module(te)


# ---------------------------------------------------------------------------
# Enum parity with C7
# ---------------------------------------------------------------------------


def test_taint_label_values_match_c7():
    assert int(te.TaintLabel.PUBLIC) == 0
    assert int(te.TaintLabel.UNTRUSTED) == 1
    assert int(te.TaintLabel.SECRET) == 2
    assert int(te.TaintLabel.TOXIC) == 3


def test_sink_kind_values_match_c7():
    assert int(te.SinkKind.NETWORK_EGRESS) == 0
    assert int(te.SinkKind.FILE_WRITE) == 1
    assert int(te.SinkKind.USER_STDOUT) == 2
    assert int(te.SinkKind.AUDIT_LOG) == 3


# ---------------------------------------------------------------------------
# TaintedBuffer construction
# ---------------------------------------------------------------------------


def test_make_validates_length_match():
    with pytest.raises(ValueError, match="length"):
        te.TaintedBuffer.make(content=b"hello", colors=b"\x00\x00", source_id="src")


def test_make_validates_color_range():
    with pytest.raises(ValueError, match="invalid color"):
        te.TaintedBuffer.make(content=b"x", colors=b"\x05", source_id="src")


def test_make_validates_source_id_nonempty():
    with pytest.raises(ValueError):
        te.TaintedBuffer.make(content=b"x", colors=b"\x00", source_id="")


def test_make_attaches_sha256_and_creation_ts():
    buf = te.TaintedBuffer.make(b"hello", b"\x01" * 5, "src")
    assert len(buf.sha256) == 64
    assert buf.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert buf.creation_ts > 0


# ---------------------------------------------------------------------------
# label_source — uniform per-byte color
# ---------------------------------------------------------------------------


def test_label_source_uniform_per_byte_color():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("tool:fetch", b"abcdef", te.TaintLabel.UNTRUSTED)
    assert buf.colors == bytes([1] * 6)
    assert buf.content == b"abcdef"


def test_label_source_default_label_untrusted():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("tool:fetch", b"x")
    assert buf.colors == b"\x01"


def test_label_source_no_color_glob_matches_gguf():
    eng = te.ByteTaintEngine()
    buf = eng.label_source(
        "file:llama",
        b"\x00" * 100,
        te.TaintLabel.UNTRUSTED,
        filename_hint="llama-3-70b.gguf",
    )
    assert buf.no_color_reason is not None
    assert "*.gguf" in buf.no_color_reason
    assert buf.colors == b""  # no color array stored
    # Safe-fail: no-color buffer returns TOXIC.
    assert buf.max_color() == te.TaintLabel.TOXIC


def test_label_source_no_color_glob_does_not_match_text():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("file:doc", b"hello", filename_hint="readme.txt")
    assert buf.no_color_reason is None
    assert len(buf.colors) == 5


def test_label_source_rejects_non_bytes():
    eng = te.ByteTaintEngine()
    with pytest.raises(TypeError):
        eng.label_source("src", "not-bytes")  # type: ignore[arg-type]


def test_label_source_rejects_non_label():
    eng = te.ByteTaintEngine()
    with pytest.raises(TypeError):
        eng.label_source("src", b"x", label=99)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# label_range — high-watermark lifting
# ---------------------------------------------------------------------------


def test_label_range_lifts_bytes_in_range():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hello world", te.TaintLabel.PUBLIC)
    # Mark bytes [6:11) "world" as TOXIC.
    upgraded = eng.label_range(buf, 6, 11, te.TaintLabel.TOXIC)
    assert upgraded.colors[:6] == bytes([0, 0, 0, 0, 0, 0])
    assert upgraded.colors[6:11] == bytes([3, 3, 3, 3, 3])


def test_label_range_never_downgrades():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"abcde", te.TaintLabel.TOXIC)
    # Try to "lower" range [1:4) to PUBLIC via label_range (should NOT downgrade).
    result = eng.label_range(buf, 1, 4, te.TaintLabel.PUBLIC)
    # All bytes stay TOXIC.
    assert result.colors == bytes([3, 3, 3, 3, 3])


def test_label_range_rejects_no_color_buffer():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("file", b"x" * 10, filename_hint="x.gguf")
    with pytest.raises(ValueError, match="no-color buffer"):
        eng.label_range(buf, 0, 5, te.TaintLabel.TOXIC)


def test_label_range_rejects_invalid_range():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"abc")
    with pytest.raises(ValueError):
        eng.label_range(buf, 5, 10, te.TaintLabel.SECRET)
    with pytest.raises(ValueError):
        eng.label_range(buf, 2, 1, te.TaintLabel.SECRET)


# ---------------------------------------------------------------------------
# slice + concat
# ---------------------------------------------------------------------------


def test_slice_preserves_per_byte_color():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hello world", te.TaintLabel.UNTRUSTED)
    buf = eng.label_range(buf, 6, 11, te.TaintLabel.TOXIC)
    sliced = eng.slice(buf, 3, 9)
    assert sliced.content == b"lo wor"
    # Colors at original positions 3..5 = UNTRUSTED (1); 6..8 = TOXIC (3).
    assert sliced.colors == bytes([1, 1, 1, 3, 3, 3])


def test_concat_preserves_per_byte_color_across_boundaries():
    eng = te.ByteTaintEngine()
    a = eng.label_source("a", b"AAA", te.TaintLabel.PUBLIC)
    b = eng.label_source("b", b"BBB", te.TaintLabel.SECRET)
    c = eng.label_source("c", b"CCC", te.TaintLabel.TOXIC)
    out = eng.concat([a, b, c])
    assert out.content == b"AAABBBCCC"
    assert out.colors == bytes([0, 0, 0, 2, 2, 2, 3, 3, 3])
    # max_color over the whole buffer = TOXIC.
    assert out.max_color() == te.TaintLabel.TOXIC


def test_concat_unions_provenance_chains():
    eng = te.ByteTaintEngine()
    a = eng.label_source("src-a", b"x")
    b = eng.label_source("src-b", b"y")
    out = eng.concat([a, b])
    assert "src-a" in out.provenance_chain
    assert "src-b" in out.provenance_chain
    assert any("concat" in p for p in out.provenance_chain)


def test_concat_empty_raises():
    eng = te.ByteTaintEngine()
    with pytest.raises(ValueError):
        eng.concat([])


def test_concat_no_color_buffer_raises():
    eng = te.ByteTaintEngine()
    a = eng.label_source("a", b"hi")
    b = eng.label_source("b", b"\x00", filename_hint="b.gguf")
    with pytest.raises(ValueError, match="no-color"):
        eng.concat([a, b])


# ---------------------------------------------------------------------------
# max_color_in_range
# ---------------------------------------------------------------------------


def test_max_color_in_range_picks_max():
    eng = te.ByteTaintEngine()
    a = eng.label_source("a", b"AAA", te.TaintLabel.PUBLIC)
    b = eng.label_source("b", b"BBB", te.TaintLabel.UNTRUSTED)
    c = eng.label_source("c", b"CCC", te.TaintLabel.TOXIC)
    buf = eng.concat([a, b, c])
    # First 3 bytes (a): PUBLIC.
    assert eng.max_color_in_range(buf, 0, 3) == te.TaintLabel.PUBLIC
    # Middle bytes 3..6: UNTRUSTED.
    assert eng.max_color_in_range(buf, 3, 6) == te.TaintLabel.UNTRUSTED
    # Last 3 bytes (c): TOXIC.
    assert eng.max_color_in_range(buf, 6, 9) == te.TaintLabel.TOXIC
    # Span crossing all three: TOXIC.
    assert eng.max_color_in_range(buf, 0, 9) == te.TaintLabel.TOXIC


# ---------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------


def test_check_egress_allows_untrusted_to_network():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"x", te.TaintLabel.UNTRUSTED)
    d = eng.check_egress(buf, te.SinkKind.NETWORK_EGRESS)
    assert d.kind == te.EgressDecisionKind.ALLOW


def test_check_egress_denies_secret_to_network():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"x", te.TaintLabel.SECRET)
    d = eng.check_egress(buf, te.SinkKind.NETWORK_EGRESS)
    assert d.kind == te.EgressDecisionKind.DENY


def test_check_egress_audit_log_accepts_toxic():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"x", te.TaintLabel.TOXIC)
    d = eng.check_egress(buf, te.SinkKind.AUDIT_LOG)
    assert d.kind == te.EgressDecisionKind.ALLOW


def test_check_egress_no_color_buffer_defaults_toxic_and_denied():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("file", b"weights", filename_hint="m.gguf")
    d = eng.check_egress(buf, te.SinkKind.NETWORK_EGRESS)
    assert d.kind == te.EgressDecisionKind.DENY
    assert d.max_color_in_buffer == te.TaintLabel.TOXIC


# ---------------------------------------------------------------------------
# Declassification — Ed25519 evidence
# ---------------------------------------------------------------------------


def _make_signed_evidence(
    reviewer_id: str,
    reviewed_bytes: bytes,
    target_label: te.TaintLabel,
    reason: str = "reviewed by operator",
    reviewed_at: float = None,
) -> te.DeclassEvidence:
    """Construct a real Ed25519-signed DeclassEvidence."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    pk_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if reviewed_at is None:
        reviewed_at = time.time()
    payload = {
        "reviewer_id": reviewer_id,
        "reviewed_bytes_sha256": hashlib.sha256(reviewed_bytes).hexdigest(),
        "reviewed_at": reviewed_at,
        "target_label": int(target_label),
        "reason": reason,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    sig = sk.sign(canonical)
    return te.DeclassEvidence(
        reviewer_id=reviewer_id,
        reviewed_bytes_sha256=payload["reviewed_bytes_sha256"],
        reviewed_at=reviewed_at,
        target_label=int(target_label),
        reason=reason,
        signature=sig,
        public_key=pk_bytes,
    )


def test_declassify_range_happy_path():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hello world", te.TaintLabel.TOXIC)
    # Operator reviews bytes [0:5) "hello" and declares them PUBLIC.
    target_bytes = buf.content[0:5]
    evidence = _make_signed_evidence(
        reviewer_id="op@example.com",
        reviewed_bytes=target_bytes,
        target_label=te.TaintLabel.PUBLIC,
    )
    declassified = eng.declassify_range(
        buf,
        0,
        5,
        te.TaintLabel.PUBLIC,
        evidence=evidence,
    )
    # Bytes 0..4 are now PUBLIC; bytes 5..10 still TOXIC.
    assert declassified.colors[:5] == bytes([0, 0, 0, 0, 0])
    assert declassified.colors[5:] == bytes([3, 3, 3, 3, 3, 3])


def test_declassify_range_refuses_to_raise_label():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"x", te.TaintLabel.PUBLIC)
    evidence = _make_signed_evidence(
        reviewer_id="op",
        reviewed_bytes=buf.content,
        target_label=te.TaintLabel.SECRET,
    )
    with pytest.raises(ValueError, match="cannot RAISE"):
        eng.declassify_range(buf, 0, 1, te.TaintLabel.SECRET, evidence=evidence)


def test_declassify_range_rejects_sha_mismatch():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hello world", te.TaintLabel.TOXIC)
    # Evidence signed for DIFFERENT bytes.
    evidence = _make_signed_evidence(
        reviewer_id="op",
        reviewed_bytes=b"DIFFERENT",
        target_label=te.TaintLabel.PUBLIC,
    )
    with pytest.raises(te.DeclassError, match="SHA-256"):
        eng.declassify_range(buf, 0, 5, te.TaintLabel.PUBLIC, evidence=evidence)


def test_declassify_range_rejects_stale_evidence():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hi", te.TaintLabel.TOXIC)
    # Evidence with reviewed_at one hour ago.
    evidence = _make_signed_evidence(
        reviewer_id="op",
        reviewed_bytes=buf.content[0:2],
        target_label=te.TaintLabel.PUBLIC,
        reviewed_at=time.time() - 3600,
    )
    with pytest.raises(te.DeclassError, match="time window"):
        eng.declassify_range(buf, 0, 2, te.TaintLabel.PUBLIC, evidence=evidence)


def test_declassify_range_rejects_target_label_mismatch():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hi", te.TaintLabel.TOXIC)
    # Evidence claims target=PUBLIC but call asks for UNTRUSTED.
    evidence = _make_signed_evidence(
        reviewer_id="op",
        reviewed_bytes=buf.content[0:2],
        target_label=te.TaintLabel.PUBLIC,
    )
    with pytest.raises(te.DeclassError, match="target_label"):
        eng.declassify_range(buf, 0, 2, te.TaintLabel.UNTRUSTED, evidence=evidence)


def test_declassify_range_rejects_invalid_signature():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"hi", te.TaintLabel.TOXIC)
    evidence = _make_signed_evidence(
        reviewer_id="op",
        reviewed_bytes=buf.content[0:2],
        target_label=te.TaintLabel.PUBLIC,
    )
    # Tamper the signature.
    bad_evidence = te.DeclassEvidence(
        reviewer_id=evidence.reviewer_id,
        reviewed_bytes_sha256=evidence.reviewed_bytes_sha256,
        reviewed_at=evidence.reviewed_at,
        target_label=evidence.target_label,
        reason=evidence.reason,
        signature=b"\x00" * 64,  # zeroed signature
        public_key=evidence.public_key,
    )
    with pytest.raises(te.DeclassError):
        eng.declassify_range(buf, 0, 2, te.TaintLabel.PUBLIC, evidence=bad_evidence)


# ---------------------------------------------------------------------------
# C7 interop
# ---------------------------------------------------------------------------


def test_from_blob_label_creates_uniform_buffer():
    eng = te.ByteTaintEngine()
    buf = eng.from_blob_label(b"abc", "tool:fetch", te.TaintLabel.UNTRUSTED)
    assert buf.colors == bytes([1, 1, 1])


def test_to_blob_max_returns_content_and_max():
    eng = te.ByteTaintEngine()
    buf = eng.label_source("src", b"AAABBB", te.TaintLabel.PUBLIC)
    buf = eng.label_range(buf, 3, 6, te.TaintLabel.SECRET)
    content, label = eng.to_blob_max(buf)
    assert content == b"AAABBB"
    assert label == te.TaintLabel.SECRET


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    eng = te.ByteTaintEngine()
    eng.label_source("a", b"x", te.TaintLabel.UNTRUSTED)
    eng.label_source("b", b"y", te.TaintLabel.SECRET)
    buf = eng.label_source("c", b"hello", te.TaintLabel.PUBLIC)
    eng.label_range(buf, 0, 3, te.TaintLabel.TOXIC)
    eng.slice(buf, 1, 3)
    eng.concat([buf, buf])
    eng.check_egress(buf, te.SinkKind.NETWORK_EGRESS)
    stats = eng.snapshot_stats()
    assert stats.sources_labeled == 3
    assert stats.ranges_relabeled == 1
    assert stats.slices == 1
    assert stats.concats == 1
    assert stats.egress_checks == 1
    assert stats.egress_allowed == 1


def test_snapshot_returns_copy():
    eng = te.ByteTaintEngine()
    s1 = eng.snapshot_stats()
    eng.label_source("x", b"y")
    s2 = eng.snapshot_stats()
    assert s1.sources_labeled == 0
    assert s2.sources_labeled == 1


# ---------------------------------------------------------------------------
# End-to-end: real-world EchoLeak class scenario
# ---------------------------------------------------------------------------


def test_e2e_partial_taint_does_not_contaminate_clean_summary():
    """The motivating scenario: an agent fetches a web page with both
    public content AND an attacker-injected exfil payload. Cluster C-1
    lets the clean summary inherit PUBLIC, NOT TOXIC."""
    eng = te.ByteTaintEngine()
    # Page = 50 bytes public + 30 bytes attacker-injected.
    fetched = eng.label_source(
        "tool:fetch_url", b"A" * 50 + b"B" * 30, te.TaintLabel.UNTRUSTED
    )
    # The attacker's injection is marked TOXIC by the C2 dual-LLM router.
    fetched = eng.label_range(fetched, 50, 80, te.TaintLabel.TOXIC)
    # The privileged LLM extracts the clean section (bytes 0..50)
    # as the summary.
    summary = eng.slice(fetched, 0, 50)
    # Summary max-color is UNTRUSTED (not TOXIC), so egress to network is allowed.
    decision = eng.check_egress(summary, te.SinkKind.NETWORK_EGRESS)
    assert decision.kind == te.EgressDecisionKind.ALLOW
    # If the same engine were C7-style blob-level, the entire blob (incl
    # bytes 0..50) would be TOXIC and egress would be DENIED.
