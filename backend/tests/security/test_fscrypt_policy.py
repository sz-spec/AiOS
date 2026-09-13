"""
backend/tests/security/test_fscrypt_policy.py

Sprint 16 / Item K5 — fscrypt-required policy enforcement tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FP_PATH = _REPO_ROOT / "backend" / "security" / "fscrypt_policy.py"
_spec = importlib.util.spec_from_file_location("vos3_fp_under_test", _FP_PATH)
fp = importlib.util.module_from_spec(_spec)
sys.modules["vos3_fp_under_test"] = fp
_spec.loader.exec_module(fp)


# Stub is_encrypted_fn that maps a known set of paths to "encrypted=true".
def _make_is_encrypted_fn(encrypted_paths):
    encrypted_set = {os.path.abspath(p) for p in encrypted_paths}
    return lambda path: os.path.abspath(path) in encrypted_set


# ---------------------------------------------------------------------------
# Init validation
# ---------------------------------------------------------------------------


def test_init_rejects_zero_max_entries():
    with pytest.raises(ValueError):
        fp.FscryptPolicyStore(max_entries=0)


# ---------------------------------------------------------------------------
# mark_dir validation
# ---------------------------------------------------------------------------


def test_mark_dir_rejects_non_kind():
    s = fp.FscryptPolicyStore()
    with pytest.raises(TypeError):
        s.mark_dir("/some/dir", "REQUIRED")  # type: ignore[arg-type]


def test_mark_dir_rejects_none_kind_use_unmark():
    s = fp.FscryptPolicyStore()
    with pytest.raises(ValueError, match="unmark_dir"):
        s.mark_dir("/some/dir", fp.FscryptPolicyKind.NONE)


def test_mark_dir_rejects_empty_prefix():
    s = fp.FscryptPolicyStore()
    with pytest.raises(ValueError):
        s.mark_dir("", fp.FscryptPolicyKind.REQUIRED)


def test_mark_dir_returns_marker_id():
    s = fp.FscryptPolicyStore()
    mid1 = s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    mid2 = s.mark_dir("/opt/vos3/secrets", fp.FscryptPolicyKind.REQUIRED)
    assert mid1 != mid2
    assert mid1 == 1
    assert mid2 == 2


def test_mark_dir_full_table_raises():
    s = fp.FscryptPolicyStore(max_entries=2)
    s.mark_dir("/a", fp.FscryptPolicyKind.REQUIRED)
    s.mark_dir("/b", fp.FscryptPolicyKind.REQUIRED)
    with pytest.raises(RuntimeError, match="full"):
        s.mark_dir("/c", fp.FscryptPolicyKind.REQUIRED)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_policy_for_returns_none_when_no_match():
    s = fp.FscryptPolicyStore()
    assert s.policy_for("/random/path") == fp.FscryptPolicyKind.NONE


def test_policy_for_returns_required_when_under_marked_dir():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    assert (
        s.policy_for("/opt/vos3/models/llama-3.gguf") == fp.FscryptPolicyKind.REQUIRED
    )


def test_policy_for_prefix_must_match_dir_boundary():
    """/opt/vos3/models marked — /opt/vos3/models2 should NOT match."""
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    assert s.policy_for("/opt/vos3/models2/foo") == fp.FscryptPolicyKind.NONE


def test_policy_for_most_recent_match_wins():
    """Later mark on a narrower subtree overrides earlier broader mark."""
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3", fp.FscryptPolicyKind.REQUIRED)
    s.mark_dir("/opt/vos3/dev-only", fp.FscryptPolicyKind.PREFER)
    assert s.policy_for("/opt/vos3/models/x") == fp.FscryptPolicyKind.REQUIRED
    assert s.policy_for("/opt/vos3/dev-only/y") == fp.FscryptPolicyKind.PREFER


def test_matching_prefix_returns_correct_subtree():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    prefix = s.matching_prefix("/opt/vos3/models/llama-3.gguf")
    assert prefix == "/opt/vos3/models/"


# ---------------------------------------------------------------------------
# unmark_dir
# ---------------------------------------------------------------------------


def test_unmark_dir_removes_entry():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    removed = s.unmark_dir("/opt/vos3/models")
    assert removed == 1
    assert s.policy_for("/opt/vos3/models/x") == fp.FscryptPolicyKind.NONE


def test_unmark_unknown_dir_returns_zero():
    s = fp.FscryptPolicyStore()
    assert s.unmark_dir("/never/marked") == 0


# ---------------------------------------------------------------------------
# check_required — enforcement
# ---------------------------------------------------------------------------


def test_check_no_policy_outcome_no_policy():
    s = fp.FscryptPolicyStore()
    is_enc = _make_is_encrypted_fn([])
    r = s.check_required("/random/path", is_enc)
    assert r.outcome == fp.FscryptCheckOutcome.NO_POLICY
    assert r.matched_prefix is None


def test_check_required_blocked_when_plaintext():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    is_enc = _make_is_encrypted_fn([])  # nothing encrypted
    r = s.check_required("/opt/vos3/models/llama-3.gguf", is_enc)
    assert r.outcome == fp.FscryptCheckOutcome.BLOCKED
    assert "plaintext" in r.reason


def test_check_required_passed_when_encrypted():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/models", fp.FscryptPolicyKind.REQUIRED)
    is_enc = _make_is_encrypted_fn(["/opt/vos3/models/llama-3.gguf"])
    r = s.check_required("/opt/vos3/models/llama-3.gguf", is_enc)
    assert r.outcome == fp.FscryptCheckOutcome.PASSED


def test_check_prefer_warns_when_plaintext():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/optional", fp.FscryptPolicyKind.PREFER)
    is_enc = _make_is_encrypted_fn([])
    r = s.check_required("/opt/vos3/optional/x", is_enc)
    assert r.outcome == fp.FscryptCheckOutcome.WARNED


def test_check_prefer_passed_when_encrypted():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/opt/vos3/optional", fp.FscryptPolicyKind.PREFER)
    is_enc = _make_is_encrypted_fn(["/opt/vos3/optional/x"])
    r = s.check_required("/opt/vos3/optional/x", is_enc)
    assert r.outcome == fp.FscryptCheckOutcome.PASSED


def test_check_rejects_non_callable_is_encrypted_fn():
    s = fp.FscryptPolicyStore()
    with pytest.raises(TypeError):
        s.check_required("/p", is_encrypted_fn="not-callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/req", fp.FscryptPolicyKind.REQUIRED)
    s.mark_dir("/pref", fp.FscryptPolicyKind.PREFER)
    is_enc = _make_is_encrypted_fn(["/req/good"])
    s.check_required("/req/good", is_enc)  # passed
    s.check_required("/req/bad", is_enc)  # blocked
    s.check_required("/pref/x", is_enc)  # warned
    s.check_required("/random", is_enc)  # no_policy

    stats = s.snapshot_stats()
    assert stats.policies_marked == 2
    assert stats.checks_run == 4
    assert stats.passed == 1
    assert stats.blocked == 1
    assert stats.warned == 1
    assert stats.no_policy == 1


def test_snapshot_stats_returns_copy():
    s = fp.FscryptPolicyStore()
    s1 = s.snapshot_stats()
    s.mark_dir("/x", fp.FscryptPolicyKind.REQUIRED)
    s2 = s.snapshot_stats()
    assert s1.policies_marked == 0
    assert s2.policies_marked == 1


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_entries_returns_all():
    s = fp.FscryptPolicyStore()
    s.mark_dir("/a", fp.FscryptPolicyKind.REQUIRED)
    s.mark_dir("/b", fp.FscryptPolicyKind.PREFER)
    entries = s.list_entries()
    assert len(entries) == 2
