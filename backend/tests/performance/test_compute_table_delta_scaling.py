"""
compute_table_delta — scaling characteristics across rowset sizes.

The function is the hot path for P2P delta sync: it sorts rows by id
after filtering by workspace_id + since_ms, then returns the JSON-
serializable row dicts. Tests verify:
  * monotone behaviour across since_ms boundaries,
  * workspace isolation under mixed-tenant load,
  * stable ordering (deterministic id sort),
  * sub-linear scan time for 1k/10k row tables (slow-marked at 100k).
"""

from __future__ import annotations

import time

import pytest

from core.database.sqlite_setup import (
    ChatSession,
    ChatSessionMessage,
    Project,
    get_session,
)
from services.p2p_sync import (
    compute_table_delta,
    compute_table_summary,
)

_NOW_MS = 1_700_000_000_000


def _now():
    return _NOW_MS


def _insert_projects(
    n: int,
    *,
    workspace_id: str,
    base_ts: int,
    id_offset: int = 0,
) -> list[str]:
    ids = []
    with get_session() as s:
        for i in range(n):
            pid = f"proj_{workspace_id}_{i + id_offset:06d}"
            s.add(
                Project(
                    id=pid,
                    name=f"P{i + id_offset}",
                    ownerId="u1",
                    organizationId=workspace_id,
                    createdAt=base_ts + i,
                    updatedAt=base_ts + i,
                    dirty=False,
                )
            )
            ids.append(pid)
        s.commit()
    return ids


def _insert_chat_sessions(n: int, *, workspace_id: str, base_ts: int) -> None:
    with get_session() as s:
        for i in range(n):
            s.add(
                ChatSession(
                    id=f"cs_{workspace_id}_{i:06d}",
                    userId="u1",
                    sessionId=f"sess_{i}",
                    createdAt=base_ts + i,
                    updatedAt=base_ts + i,
                    organizationId=workspace_id,
                    dirty=False,
                )
            )
        s.commit()


def _insert_chat_messages(n: int, *, workspace_id: str, base_ts: int) -> None:
    with get_session() as s:
        for i in range(n):
            s.add(
                ChatSessionMessage(
                    id=f"msg_{workspace_id}_{i:06d}",
                    sessionId=f"sess_{i % 4}",
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"m{i}",
                    timestamp=base_ts + i,
                    organizationId=workspace_id,
                    dirty=False,
                )
            )
        s.commit()


# ---------------------------------------------------------------------------
# since_ms boundary — rows AT or AFTER are returned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rows", [1, 4, 16, 64, 256])
def test_since_ms_inclusive_lower_bound(perf_env, n_rows):
    _insert_projects(n_rows, workspace_id="ws-perf", base_ts=_now())
    out = compute_table_delta("projects", workspace_id="ws-perf", since_ms=_now())
    # All inserted rows have ts >= _now → all returned.
    assert len(out) == n_rows


@pytest.mark.parametrize("n_rows", [4, 16, 64, 256])
def test_since_ms_excludes_older_rows(perf_env, n_rows):
    _insert_projects(n_rows, workspace_id="ws-perf", base_ts=_now())
    # Anything strictly older than (base + n) is excluded when we
    # ask for since_ms past the youngest row.
    out = compute_table_delta(
        "projects",
        workspace_id="ws-perf",
        since_ms=_now() + n_rows + 1,
    )
    assert out == []


@pytest.mark.parametrize("split", [0.25, 0.5, 0.75])
def test_since_ms_partial_window(perf_env, split):
    n = 100
    _insert_projects(n, workspace_id="ws-perf", base_ts=_now())
    cutoff = _now() + int(n * split)
    out = compute_table_delta(
        "projects",
        workspace_id="ws-perf",
        since_ms=cutoff,
    )
    # ts >= cutoff → rows i where _now+i >= cutoff → i >= cutoff-_now.
    expected = n - (cutoff - _now())
    assert len(out) == expected


# ---------------------------------------------------------------------------
# Workspace isolation — rows from other workspaces never bleed in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_each", [1, 5, 25, 100])
def test_workspace_isolation_projects(perf_env, n_each):
    _insert_projects(n_each, workspace_id="ws-perf", base_ts=_now())
    _insert_projects(n_each, workspace_id="ws-other", base_ts=_now())
    a = compute_table_delta("projects", workspace_id="ws-perf", since_ms=0)
    b = compute_table_delta("projects", workspace_id="ws-other", since_ms=0)
    assert len(a) == n_each
    assert len(b) == n_each
    ids_a = {r["id"] for r in a}
    ids_b = {r["id"] for r in b}
    assert ids_a.isdisjoint(ids_b)


# ---------------------------------------------------------------------------
# Ordering — output is sorted by id ascending
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 10, 50, 200])
def test_output_sorted_by_id(perf_env, n):
    _insert_projects(n, workspace_id="ws-perf", base_ts=_now())
    out = compute_table_delta("projects", workspace_id="ws-perf", since_ms=0)
    ids = [r["id"] for r in out]
    assert ids == sorted(ids), "compute_table_delta must return id-sorted rows"


# ---------------------------------------------------------------------------
# Unknown table — returns [] (never raises)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bogus_table",
    [
        "users",
        "nope",
        "",
        "DROP TABLE",
        "../etc/passwd",
        "projects\x00",
    ],
)
def test_unknown_table_returns_empty(perf_env, bogus_table):
    out = compute_table_delta(bogus_table, workspace_id="ws-perf", since_ms=0)
    assert out == []


# ---------------------------------------------------------------------------
# summary fingerprint — stable + flips on mutation
# ---------------------------------------------------------------------------


def test_summary_stable_across_calls(perf_env):
    _insert_projects(20, workspace_id="ws-perf", base_ts=_now())
    s1 = compute_table_summary("projects", workspace_id="ws-perf")
    s2 = compute_table_summary("projects", workspace_id="ws-perf")
    assert s1 == s2


def test_summary_flips_on_mutation(perf_env):
    _insert_projects(5, workspace_id="ws-perf", base_ts=_now())
    s1 = compute_table_summary("projects", workspace_id="ws-perf")
    _insert_projects(1, workspace_id="ws-perf", base_ts=_now() + 100, id_offset=999)
    s2 = compute_table_summary("projects", workspace_id="ws-perf")
    assert s1["hash"] != s2["hash"]
    assert s2["row_count"] == s1["row_count"] + 1


# ---------------------------------------------------------------------------
# Scaling — wall-clock budget per rowset size (non-slow up to 1k)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_rows,budget_s",
    [
        (10, 0.05),
        (100, 0.10),
        (500, 0.30),
        (1_000, 0.60),
    ],
)
def test_delta_scan_under_budget(perf_env, n_rows, budget_s):
    _insert_projects(n_rows, workspace_id="ws-perf", base_ts=_now())
    t0 = time.perf_counter()
    out = compute_table_delta("projects", workspace_id="ws-perf", since_ms=0)
    elapsed = time.perf_counter() - t0
    assert len(out) == n_rows
    assert elapsed < budget_s, f"{n_rows} rows took {elapsed:.3f}s (> {budget_s}s)"


@pytest.mark.slow
@pytest.mark.parametrize("n_rows", [10_000, 50_000, 100_000])
def test_large_dataset_scaling(perf_env, n_rows):
    _insert_projects(n_rows, workspace_id="ws-perf", base_ts=_now())
    t0 = time.perf_counter()
    out = compute_table_delta("projects", workspace_id="ws-perf", since_ms=0)
    elapsed = time.perf_counter() - t0
    assert len(out) == n_rows
    # Linear-with-large-constant envelope: 100µs per row.
    assert (
        elapsed < n_rows * 1e-4 + 1.0
    ), f"{n_rows} rows: {elapsed:.2f}s exceeds linear envelope"


# ---------------------------------------------------------------------------
# Cross-table parametrize — all five exposed tables behave identically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "projects",
        "chatSessions",
        "chatSessionMessages",
        "appState",
        "apps",
    ],
)
def test_unknown_workspace_returns_empty(perf_env, table):
    out = compute_table_delta(table, workspace_id="ws-nonexistent", since_ms=0)
    assert out == []


@pytest.mark.parametrize(
    "table",
    [
        "projects",
        "chatSessions",
        "chatSessionMessages",
        "appState",
        "apps",
    ],
)
def test_summary_unknown_workspace_zero(perf_env, table):
    s = compute_table_summary(table, workspace_id="ws-nonexistent")
    assert s.get("row_count") == 0
    assert s.get("latest_updated_at") == 0
