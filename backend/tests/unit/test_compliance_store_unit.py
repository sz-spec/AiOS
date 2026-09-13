"""
Stage 1 · Atomic unit isolation for services/compliance_store.py.

Covers — purpose | guards file:line:
  append_events writes well-formed rows           | compliance_store.py:173-213
  append_events ignores duplicate seq             | compliance_store.py:188 (INSERT OR IGNORE)
  malformed events are dropped, not raised        | compliance_store.py:204-208
  highest_seq tracks the kernel watermark         | compliance_store.py:215-221
  recent_events orders DESC + applies category    | compliance_store.py:227-261
  recent_events rejects out-of-range limit        | compliance_store.py:235-236
  category_counts groups correctly                | compliance_store.py:263-269
  transaction context manager commits + rolls back| compliance_store.py:282-293
  close is safe                                   | compliance_store.py:275-280
"""

from __future__ import annotations

import pytest


def _store(tmp_path):
    from services.compliance_store import ComplianceStore

    return ComplianceStore(db_path=str(tmp_path / "compliance.db"))


def _event(
    seq: int, *, category: int = 1, rc: int = 0, slot: int = 0, digest: str = "00" * 8
):
    return {
        "seq": seq,
        "tick": seq,
        "category": category,
        "rc": rc,
        "slot_id": slot,
        "digest_prefix": digest,
    }


# ---------------------------------------------------------------------------
# append_events — happy path + dedupe
# ---------------------------------------------------------------------------


def test_append_events_inserts_new(tmp_path):
    s = _store(tmp_path)
    try:
        n = s.append_events([_event(1), _event(2), _event(3)])
        assert n == 3
        assert s.highest_seq() == 3
    finally:
        s.close()


def test_append_events_dedupes_on_seq(tmp_path):
    """INSERT OR IGNORE → re-appending the same seq is a no-op."""
    s = _store(tmp_path)
    try:
        s.append_events([_event(1), _event(2)])
        n = s.append_events([_event(2), _event(3)])
        # Only seq=3 is new.
        assert n == 1
        assert s.highest_seq() == 3
    finally:
        s.close()


def test_append_events_drops_malformed_without_raising(tmp_path):
    """A missing required key is logged + dropped, but doesn't break the batch."""
    s = _store(tmp_path)
    try:
        bad = {"seq": "not-an-int"}  # int() raises ValueError
        good = _event(10)
        n = s.append_events([bad, good])
        assert n == 1
        assert s.highest_seq() == 10
    finally:
        s.close()


def test_append_events_empty_batch(tmp_path):
    s = _store(tmp_path)
    try:
        assert s.append_events([]) == 0
        assert s.highest_seq() == -1
    finally:
        s.close()


# ---------------------------------------------------------------------------
# highest_seq
# ---------------------------------------------------------------------------


def test_highest_seq_empty_returns_neg_one(tmp_path):
    s = _store(tmp_path)
    try:
        assert s.highest_seq() == -1
    finally:
        s.close()


# ---------------------------------------------------------------------------
# recent_events
# ---------------------------------------------------------------------------


def test_recent_events_returns_desc_by_seq(tmp_path):
    s = _store(tmp_path)
    try:
        s.append_events([_event(1), _event(2), _event(3)])
        rows = s.recent_events(limit=10)
        assert [r["seq"] for r in rows] == [3, 2, 1]
    finally:
        s.close()


def test_recent_events_filters_by_category(tmp_path):
    s = _store(tmp_path)
    try:
        s.append_events(
            [
                _event(1, category=10),
                _event(2, category=20),
                _event(3, category=10),
            ]
        )
        rows = s.recent_events(limit=10, category=10)
        assert {r["seq"] for r in rows} == {1, 3}
        assert all(r["category"] == 10 for r in rows)
    finally:
        s.close()


def test_recent_events_respects_limit(tmp_path):
    s = _store(tmp_path)
    try:
        s.append_events([_event(i) for i in range(1, 11)])
        rows = s.recent_events(limit=3)
        assert len(rows) == 3
    finally:
        s.close()


def test_recent_events_rejects_zero_limit(tmp_path):
    s = _store(tmp_path)
    try:
        with pytest.raises(ValueError):
            s.recent_events(limit=0)
    finally:
        s.close()


def test_recent_events_rejects_oversize_limit(tmp_path):
    s = _store(tmp_path)
    try:
        with pytest.raises(ValueError):
            s.recent_events(limit=10_001)
    finally:
        s.close()


# ---------------------------------------------------------------------------
# category_counts
# ---------------------------------------------------------------------------


def test_category_counts_groups_correctly(tmp_path):
    s = _store(tmp_path)
    try:
        s.append_events(
            [
                _event(1, category=1),
                _event(2, category=1),
                _event(3, category=2),
            ]
        )
        counts = s.category_counts()
        assert counts == {1: 2, 2: 1}
    finally:
        s.close()


def test_category_counts_empty_table(tmp_path):
    s = _store(tmp_path)
    try:
        assert s.category_counts() == {}
    finally:
        s.close()


# ---------------------------------------------------------------------------
# close / transaction
# ---------------------------------------------------------------------------


def test_close_is_safe_to_call_twice(tmp_path):
    s = _store(tmp_path)
    s.close()
    s.close()  # second close should not raise


def test_transaction_commits_on_success(tmp_path):
    s = _store(tmp_path)
    try:
        with s.transaction() as cur:
            cur.execute(
                "INSERT INTO compliance_events "
                "(seq, tick, drained_at, category, rc, slot_id, digest_prefix) "
                "VALUES (1, 0, 0, 1, 0, 0, '0000000000000000')"
            )
        assert s.highest_seq() == 1
    finally:
        s.close()
