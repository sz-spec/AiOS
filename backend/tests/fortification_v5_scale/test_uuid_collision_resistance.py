"""
Phase 2 · UUID / SID collision resistance.

Honest scope
------------
UUID4 has 122 bits of entropy. The probability of collision in N
samples is ~N²/2^123 (birthday bound). For N=10M, that's ~10^14/10^37
= 10^-23 — vanishingly small.

We don't actually generate 10M UUIDs (slow + RAM-intensive). We
generate 1k / 10k / 100k samples and verify ZERO collisions. We
also exercise the AppContainer SID generator and Windows SID format
validator.

The "10M" framing maps to: at 100k samples we see ~5×10^-26 collision
probability; extrapolating to 10M is a million-fold increase, still
2×10^-20 — far below any operationally significant bound.
"""

from __future__ import annotations

import uuid

import pytest

# ---------------------------------------------------------------------------
# UUID4 collision at various N
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [100, 1_000, 10_000, 100_000])
def test_uuid4_zero_collisions_at_scale(n):
    seen = set()
    for _ in range(n):
        u = uuid.uuid4().hex
        assert u not in seen
        seen.add(u)
    assert len(seen) == n


@pytest.mark.parametrize("seed", list(range(20)))
def test_uuid4_independent_runs_dont_overlap(seed):
    """20 independent runs of 1k UUIDs each. Across the union of
    20×1k = 20k UUIDs, no collision."""
    all_seen = set()
    for _ in range(20):
        for _ in range(1_000):
            u = uuid.uuid4().hex
            assert u not in all_seen
            all_seen.add(u)
    assert len(all_seen) == 20_000


# ---------------------------------------------------------------------------
# AppContainer container-name generator collision (uses uuid4 internally)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [100, 1_000, 10_000])
def test_appcontainer_name_zero_collision(n):
    from services.app_sandbox import WindowsAppContainerProvider

    seen = set()
    for _ in range(n):
        name = WindowsAppContainerProvider._generate_container_name()
        assert name not in seen
        seen.add(name)
    assert len(seen) == n


@pytest.mark.parametrize("n", [100, 1_000, 5_000])
def test_appcontainer_name_passes_validator_at_scale(n):
    from services.app_sandbox import WindowsAppContainerProvider

    for _ in range(n):
        name = WindowsAppContainerProvider._generate_container_name()
        assert WindowsAppContainerProvider._validate_container_name(name)


# ---------------------------------------------------------------------------
# Windows SID format validator
# ---------------------------------------------------------------------------

import re

_SID_RE = re.compile(r"^S-\d+(-\d+)+$")


def _is_valid_sid(sid: str) -> bool:
    return bool(_SID_RE.match(sid))


@pytest.mark.parametrize(
    "good_sid",
    [
        "S-1-15-2-1234567890-1234567890-1234567890-1234567890",
        "S-1-15-2-0-0-0-0",
        "S-1-5-21-1004336348-1177238915-682003330-512",
        "S-1-15-3-1024-3424233489-972189580-2057154623-747635277-1604371224-316187531-3786945614-3776428294",
    ],
)
def test_valid_sids_accepted(good_sid):
    assert _is_valid_sid(good_sid) is True


@pytest.mark.parametrize(
    "bad_sid",
    [
        "",
        "S-",
        "S-1",
        "1-15-2",
        "S 1 15 2",
        "S-1-15-2-abc",
        "S-1-15-2-1234567890-",
        "S-1-15-2--1234",
        "SID-1-15-2",
    ],
)
def test_invalid_sids_rejected(bad_sid):
    assert _is_valid_sid(bad_sid) is False


# ---------------------------------------------------------------------------
# O(1) set membership — lookup time invariant under cache growth
# ---------------------------------------------------------------------------


def test_set_membership_is_constant_time(regression_slope):
    """Predictive failure analysis: a Python set's `in` operator is
    O(1) amortized. Verify the slope of (lookup_time vs set_size) is
    ~0 across sizes [1k, 10k, 100k]."""
    import time

    sizes = [1_000, 10_000, 100_000]
    times = []
    for n in sizes:
        s = {f"key_{i}" for i in range(n)}
        target = f"key_{n // 2}"
        # Measure 10k lookups; report per-lookup time.
        t0 = time.perf_counter()
        hits = 0
        for _ in range(10_000):
            if target in s:
                hits += 1
        elapsed = time.perf_counter() - t0
        times.append(elapsed / 10_000)
    slope = regression_slope(sizes, times)
    # 1ns per added element of growth = O(N); we want O(1) → ~0 slope.
    assert slope < 1e-9, (
        f"set lookup slope {slope*1e9:.3f}ns/element — non-O(1) " f"regression detected"
    )


# ---------------------------------------------------------------------------
# Dict lookup — same invariant for the gate cache pattern
# ---------------------------------------------------------------------------


def test_dict_lookup_is_constant_time(regression_slope):
    import time

    sizes = [1_000, 10_000, 100_000]
    times = []
    for n in sizes:
        d = {f"k{i}": i for i in range(n)}
        target = f"k{n // 2}"
        t0 = time.perf_counter()
        for _ in range(10_000):
            _ = d.get(target)
        elapsed = time.perf_counter() - t0
        times.append(elapsed / 10_000)
    slope = regression_slope(sizes, times)
    assert slope < 1e-9


# ---------------------------------------------------------------------------
# PermissionGate cache lookup — verify O(1) at scale
# ---------------------------------------------------------------------------


def test_permission_gate_cache_lookup_constant_time(v5_env, regression_slope):
    """The PermissionGate's _entries dict is the production hot-path.
    Verify lookup time doesn't grow with cache size."""
    from services.app_sandbox import PERMISSION_GATE
    import time

    PERMISSION_GATE._entries.clear()

    # Populate with synthetic entries.
    from services.app_sandbox import _GateEntry

    cache_sizes = [100, 1_000, 10_000]
    times = []
    for n in cache_sizes:
        for i in range(n):
            PERMISSION_GATE._entries[f"app_{i}"] = _GateEntry(
                status="active",
                scopes=("filesystem.read",),
                restrictions=(),
            )
        target = f"app_{n // 2}"
        t0 = time.perf_counter()
        for _ in range(10_000):
            _ = PERMISSION_GATE._entries.get(target)
        elapsed = time.perf_counter() - t0
        times.append(elapsed / 10_000)

    slope = regression_slope(cache_sizes, times)
    PERMISSION_GATE._entries.clear()
    assert slope < 1e-9, (
        f"gate cache slope {slope*1e9:.3f}ns/entry — would O(N) " f"at production scale"
    )


# ---------------------------------------------------------------------------
# UUID format validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iteration", list(range(20)))
def test_uuid4_hex_format_invariant(iteration):
    u = uuid.uuid4()
    assert len(u.hex) == 32
    int(u.hex, 16)  # must parse as hex


@pytest.mark.parametrize("iteration", list(range(20)))
def test_uuid4_version_bits(iteration):
    """UUID4 must have version=4 (top nibble of byte 6 = 0x4) and
    variant=10 (top 2 bits of byte 8)."""
    u = uuid.uuid4()
    assert u.version == 4
    assert u.variant == uuid.RFC_4122
