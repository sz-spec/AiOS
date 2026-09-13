"""
ComplianceStore — write-path atomicity + read consistency.

The store appends kernel-emitted audit events into a `compliance_events`
table indexed by monotonic seq. The contract:

  * INSERT OR IGNORE — duplicate seq is silently dropped
  * `append_events` is wrapped in BEGIN/COMMIT (rollback on sqlite error)
  * `highest_seq()` reflects committed state
  * `recent_events()` returns most-recent N rows, optionally
    filtered by category
"""

from __future__ import annotations

import threading

import pytest


def _store_at(path):
    from services.compliance_store import ComplianceStore

    return ComplianceStore(db_path=str(path))


def _evt(
    seq: int,
    *,
    category: int = 0,
    rc: int = 0,
    slot_id: int = 0,
    tick: int = 0,
    digest_prefix: str = "0" * 16,
) -> dict:
    return {
        "seq": seq,
        "tick": tick,
        "category": category,
        "rc": rc,
        "slot_id": slot_id,
        "digest_prefix": digest_prefix,
    }


# ---------------------------------------------------------------------------
# Atomicity — single-batch insert
# ---------------------------------------------------------------------------


def test_empty_batch_inserts_zero(gov_env):
    store = _store_at(gov_env / "c.db")
    assert store.append_events([]) == 0
    assert store.highest_seq() == -1


@pytest.mark.parametrize("n", [1, 5, 50, 200])
def test_unique_batch_all_inserted(gov_env, n):
    store = _store_at(gov_env / f"c{n}.db")
    events = [_evt(i + 1) for i in range(n)]
    assert store.append_events(events) == n
    assert store.highest_seq() == n


# ---------------------------------------------------------------------------
# Dedupe on seq — same seq across batches inserts once
# ---------------------------------------------------------------------------


def test_duplicate_seq_dropped(gov_env):
    store = _store_at(gov_env / "c.db")
    assert store.append_events([_evt(1), _evt(1), _evt(1)]) == 1
    assert store.highest_seq() == 1


def test_duplicate_seq_across_batches_dropped(gov_env):
    store = _store_at(gov_env / "c.db")
    assert store.append_events([_evt(1)]) == 1
    assert store.append_events([_evt(1)]) == 0  # second insert: silent drop
    assert store.highest_seq() == 1


@pytest.mark.parametrize(
    "seqs",
    [
        [1, 1, 1, 1, 1],
        [1, 2, 2, 3, 3],
        [10, 10, 11, 12, 12, 13],
    ],
)
def test_mixed_dedupe(gov_env, seqs):
    store = _store_at(gov_env / "c.db")
    inserted = store.append_events([_evt(s) for s in seqs])
    assert inserted == len(set(seqs))
    assert store.highest_seq() == max(seqs)


# ---------------------------------------------------------------------------
# Malformed event is dropped (not a transaction abort)
# ---------------------------------------------------------------------------


def test_malformed_event_does_not_abort_batch(gov_env):
    store = _store_at(gov_env / "c.db")
    inserted = store.append_events(
        [
            _evt(1),
            {"missing_keys": True},  # malformed — dropped, batch survives
            _evt(2),
        ]
    )
    assert inserted == 2
    assert store.highest_seq() == 2


@pytest.mark.parametrize(
    "bad_event",
    [
        {},
        {"seq": "not-an-int"},
        {"seq": 1, "category": None},
        {"seq": 1, "category": "x"},
        {"seq": 1, "rc": "not-int"},
        {"seq": 1, "slot_id": None},
    ],
)
def test_individual_bad_events_dont_crash(gov_env, bad_event):
    store = _store_at(gov_env / "c.db")
    store.append_events([_evt(1), bad_event, _evt(2)])
    # Whatever the bad event was, the valid ones must land.
    assert store.highest_seq() == 2


# ---------------------------------------------------------------------------
# Recent events — filtering + limit
# ---------------------------------------------------------------------------


def test_recent_events_limit_respected(gov_env):
    store = _store_at(gov_env / "c.db")
    store.append_events([_evt(i + 1) for i in range(100)])
    rows = store.recent_events(limit=10)
    assert len(rows) == 10


@pytest.mark.parametrize("cat", [0, 1, 2, 3])
def test_recent_events_category_filter(gov_env, cat):
    store = _store_at(gov_env / "c.db")
    events = []
    for i in range(20):
        events.append(_evt(i + 1, category=i % 4))
    store.append_events(events)
    rows = store.recent_events(limit=100, category=cat)
    assert all(r["category"] == cat for r in rows)


def test_recent_events_limit_validation(gov_env):
    store = _store_at(gov_env / "c.db")
    with pytest.raises(ValueError):
        store.recent_events(limit=0)
    with pytest.raises(ValueError):
        store.recent_events(limit=-1)
    with pytest.raises(ValueError):
        store.recent_events(limit=20_000)


# ---------------------------------------------------------------------------
# Threaded write safety — RLock means concurrent appends are serialized
# ---------------------------------------------------------------------------


def test_threaded_appends_no_lost_writes(gov_env):
    store = _store_at(gov_env / "c.db")
    seqs = list(range(1, 101))

    def worker(start: int):
        store.append_events([_evt(s) for s in range(start, start + 10)])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 101, 10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = store.recent_events(limit=200)
    observed = {r["seq"] for r in rows}
    expected = set(seqs)
    assert observed == expected
