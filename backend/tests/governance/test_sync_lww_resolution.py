"""
P2P sync — last-write-wins (LWW) conflict resolution.

`services.sync_engine.resolve_conflict(local_row, cloud_row, *,
updated_at_key)` is a pure function with three outcomes:

  * CONFLICT_CLOUD_WINS  — cloud_ts > local_ts
  * CONFLICT_LOCAL_WINS  — local_ts > cloud_ts
  * CONFLICT_TIED_LOCAL_KEEPS — equal timestamps, local stays dirty

These tests exhaustively exercise the (local_ts, cloud_ts) lattice plus
edge cases for missing/null/zero `updatedAt` values.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.sync_engine import (
    CONFLICT_CLOUD_WINS,
    CONFLICT_LOCAL_WINS,
    CONFLICT_TIED_LOCAL_KEEPS,
    resolve_conflict,
)

# ---------------------------------------------------------------------------
# Deterministic outcome — cardinality matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_ts,cloud_ts,expected",
    [
        (1, 2, CONFLICT_CLOUD_WINS),
        (1, 100, CONFLICT_CLOUD_WINS),
        (1_000, 1_000_000, CONFLICT_CLOUD_WINS),
        (2, 1, CONFLICT_LOCAL_WINS),
        (100, 1, CONFLICT_LOCAL_WINS),
        (1_000_000, 1_000, CONFLICT_LOCAL_WINS),
        (1, 1, CONFLICT_TIED_LOCAL_KEEPS),
        (0, 0, CONFLICT_TIED_LOCAL_KEEPS),
        (999, 999, CONFLICT_TIED_LOCAL_KEEPS),
        (1_700_000_000_000, 1_700_000_000_000, CONFLICT_TIED_LOCAL_KEEPS),
    ],
)
def test_lww_basic_matrix(local_ts, cloud_ts, expected):
    local = SimpleNamespace(updatedAt=local_ts)
    cloud = {"updatedAt": cloud_ts}
    assert resolve_conflict(local, cloud) == expected


# ---------------------------------------------------------------------------
# Boundary — adjacent timestamps differ by 1 ms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", [0, 1, 1000, 1_700_000_000_000])
def test_lww_off_by_one_cloud_wins(base):
    local = SimpleNamespace(updatedAt=base)
    cloud = {"updatedAt": base + 1}
    assert resolve_conflict(local, cloud) == CONFLICT_CLOUD_WINS


@pytest.mark.parametrize("base", [1, 1000, 1_700_000_000_000])
def test_lww_off_by_one_local_wins(base):
    local = SimpleNamespace(updatedAt=base)
    cloud = {"updatedAt": base - 1}
    assert resolve_conflict(local, cloud) == CONFLICT_LOCAL_WINS


# ---------------------------------------------------------------------------
# Missing / null / zero handling — cloud must lose by default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_shape",
    [
        {},  # absent
        {"updatedAt": None},  # explicit null
        {"updatedAt": 0},  # explicit zero
        {"updatedAt": False},  # bool — falsy
        {"other": 99999},  # different key entirely
    ],
)
def test_missing_cloud_updatedat_treated_as_zero(missing_shape):
    local = SimpleNamespace(updatedAt=1)
    assert resolve_conflict(local, missing_shape) == CONFLICT_LOCAL_WINS


@pytest.mark.parametrize(
    "missing_shape",
    [
        {},
        {"updatedAt": None},
        {"updatedAt": 0},
    ],
)
def test_missing_both_yields_tie(missing_shape):
    local = SimpleNamespace(updatedAt=0)
    assert resolve_conflict(local, missing_shape) == CONFLICT_TIED_LOCAL_KEEPS


def test_local_missing_attr_treated_as_zero():
    local = SimpleNamespace()  # no updatedAt at all
    cloud = {"updatedAt": 5}
    assert resolve_conflict(local, cloud) == CONFLICT_CLOUD_WINS


def test_local_missing_attr_and_cloud_zero_yields_tie():
    local = SimpleNamespace()
    cloud = {"updatedAt": 0}
    assert resolve_conflict(local, cloud) == CONFLICT_TIED_LOCAL_KEEPS


# ---------------------------------------------------------------------------
# Custom updated_at_key (chat messages use `timestamp`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_ts,cloud_ts,expected",
    [
        (1, 2, CONFLICT_CLOUD_WINS),
        (2, 1, CONFLICT_LOCAL_WINS),
        (5, 5, CONFLICT_TIED_LOCAL_KEEPS),
    ],
)
def test_custom_key_timestamp(local_ts, cloud_ts, expected):
    local = SimpleNamespace(timestamp=local_ts)
    cloud = {"timestamp": cloud_ts}
    assert resolve_conflict(local, cloud, updated_at_key="timestamp") == expected


@pytest.mark.parametrize("key", ["updatedAt", "timestamp", "lastSyncedAt", "createdAt"])
def test_custom_key_works_for_any_attr_name(key):
    local = SimpleNamespace(**{key: 100})
    cloud = {key: 200}
    assert resolve_conflict(local, cloud, updated_at_key=key) == CONFLICT_CLOUD_WINS


# ---------------------------------------------------------------------------
# Integer coercion — int() of various inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cloud_value,expected_outcome",
    [
        (1, CONFLICT_CLOUD_WINS),  # int 1 > local 0
        ("5", CONFLICT_CLOUD_WINS),  # string "5" coerces to 5
        (True, CONFLICT_CLOUD_WINS),  # True → 1
        ("0", CONFLICT_TIED_LOCAL_KEEPS),
        ("", CONFLICT_TIED_LOCAL_KEEPS),  # empty string → falsy → 0
        (None, CONFLICT_TIED_LOCAL_KEEPS),  # None → 0
    ],
)
def test_cloud_value_coercion(cloud_value, expected_outcome):
    local = SimpleNamespace(updatedAt=0)
    cloud = {"updatedAt": cloud_value}
    assert resolve_conflict(local, cloud) == expected_outcome


# ---------------------------------------------------------------------------
# Idempotence — running resolve_conflict twice yields the same decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "local_ts,cloud_ts",
    [
        (1, 2),
        (2, 1),
        (1, 1),
        (0, 0),
        (100, 50),
        (1, 100),
        (50, 50),
    ],
)
def test_resolve_conflict_is_idempotent(local_ts, cloud_ts):
    local = SimpleNamespace(updatedAt=local_ts)
    cloud = {"updatedAt": cloud_ts}
    first = resolve_conflict(local, cloud)
    second = resolve_conflict(local, cloud)
    third = resolve_conflict(local, cloud)
    assert first == second == third


# ---------------------------------------------------------------------------
# Large ordinal differences — overflow guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("magnitude", [10, 100, 10_000, 1_000_000, 10**12, 10**15])
def test_large_magnitudes_resolve(magnitude):
    local = SimpleNamespace(updatedAt=magnitude)
    cloud = {"updatedAt": magnitude + 1}
    assert resolve_conflict(local, cloud) == CONFLICT_CLOUD_WINS
    cloud2 = {"updatedAt": magnitude - 1}
    assert resolve_conflict(local, cloud2) == CONFLICT_LOCAL_WINS


# ---------------------------------------------------------------------------
# Constants stability — string values shouldn't drift across releases
# ---------------------------------------------------------------------------


def test_conflict_constants_are_distinct_strings():
    assert CONFLICT_CLOUD_WINS != CONFLICT_LOCAL_WINS
    assert CONFLICT_LOCAL_WINS != CONFLICT_TIED_LOCAL_KEEPS
    assert CONFLICT_TIED_LOCAL_KEEPS != CONFLICT_CLOUD_WINS


def test_conflict_constants_are_lowercase_snake():
    for v in (CONFLICT_CLOUD_WINS, CONFLICT_LOCAL_WINS, CONFLICT_TIED_LOCAL_KEEPS):
        assert isinstance(v, str)
        assert v == v.lower()
        assert " " not in v
