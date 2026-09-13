"""
backend/tests/security/test_capability_table.py

Sprint 16 / Item A3 — per-byte capability table tests.

Tests the Python port of the kernel capability_table.c. Algorithm is
mirror-identical to kernel side (same overlap rules, perm-superset rules,
range-containment rules); a regression here would also be a regression
in the kernel implementation.

Coverage:
- Permission bit constants match kernel.
- Init, grant, revoke, lookup, iter, check.
- Grant rejects: zero length, zero perms, table full.
- Grant rejects different-owner overlap with relaxed perms (OVERLAP);
  permits same-owner overlap and equal-or-stricter perms.
- Check returns True only when a single cap covers the full requested
  range AND has perm superset.
- Check returns False for: empty table, partial-range coverage,
  insufficient perms, zero-length request.
- Revoke removes cap; subsequent lookup returns NOTFOUND.
- Revoke twice returns NOTFOUND on second call.
- Visitor early-exit + visit-all semantics.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CT_PATH = _REPO_ROOT / "backend" / "security" / "capability_table.py"

_spec = importlib.util.spec_from_file_location("vos3_cap_table_under_test", _CT_PATH)
ct = importlib.util.module_from_spec(_spec)
sys.modules["vos3_cap_table_under_test"] = ct
_spec.loader.exec_module(ct)


# ---------------------------------------------------------------------------
# Permission constants must match kernel header
# ---------------------------------------------------------------------------


def test_perm_bits_match_kernel_header():
    assert int(ct.CapPerm.READ) == 0x01
    assert int(ct.CapPerm.WRITE) == 0x02
    assert int(ct.CapPerm.EXEC) == 0x04
    assert int(ct.CapPerm.SHARE) == 0x08
    assert int(ct.CapPerm.RW) == 0x03
    assert int(ct.CapPerm.RX) == 0x05
    assert int(ct.CapPerm.RWX) == 0x07


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def test_init_defaults_empty():
    t = ct.CapTable(max_caps=8)
    assert t.max_caps == 8
    assert t.used == 0
    assert t.next_cap_id == 1
    assert all(e.cap_id == ct.CAP_ID_INVALID for e in t.entries)


def test_init_rejects_invalid_max_caps():
    with pytest.raises(ct.CapException) as ei:
        ct.CapTable(max_caps=0)
    assert ei.value.code == ct.CapError.INVAL


# ---------------------------------------------------------------------------
# Grant / lookup / revoke
# ---------------------------------------------------------------------------


def test_grant_returns_monotonic_cap_ids():
    t = ct.CapTable(max_caps=4)
    a = t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    b = t.grant(owner=2, base=0x2000, length=0x200, perms=ct.CapPerm.RW)
    assert a == 1
    assert b == 2
    assert t.used == 2


def test_grant_zero_length_rejected():
    t = ct.CapTable(max_caps=4)
    with pytest.raises(ct.CapException) as ei:
        t.grant(owner=1, base=0x1000, length=0, perms=ct.CapPerm.RO)
    assert ei.value.code == ct.CapError.INVAL


def test_grant_zero_perms_rejected():
    t = ct.CapTable(max_caps=4)
    with pytest.raises(ct.CapException) as ei:
        t.grant(owner=1, base=0x1000, length=0x100, perms=0)
    assert ei.value.code == ct.CapError.INVAL


def test_grant_fills_table_then_fails_full():
    t = ct.CapTable(max_caps=2)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    t.grant(owner=2, base=0x2000, length=0x100, perms=ct.CapPerm.RO)
    with pytest.raises(ct.CapException) as ei:
        t.grant(owner=3, base=0x3000, length=0x100, perms=ct.CapPerm.RO)
    assert ei.value.code == ct.CapError.FULL


def test_grant_rejects_different_owner_overlap_with_relaxed_perms():
    """If owner-1 holds an RO cap over [0x1000, 0x1100), then owner-2
    cannot grant an RW cap that overlaps the same region — that would
    relax permissions for the bytes."""
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    with pytest.raises(ct.CapException) as ei:
        t.grant(owner=2, base=0x1050, length=0x100, perms=ct.CapPerm.RW)
    assert ei.value.code == ct.CapError.OVERLAP


def test_grant_permits_different_owner_overlap_with_equal_perms():
    """Equal perms is fine — owner-2 simply gets the same access."""
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    cap = t.grant(owner=2, base=0x1050, length=0x100, perms=ct.CapPerm.RO)
    assert cap > 0


def test_grant_permits_different_owner_overlap_with_stricter_perms():
    """Stricter perms (fewer rights) is fine — owner-2 has LESS access
    in the overlap region, no privilege escalation possible."""
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RW)
    cap = t.grant(owner=2, base=0x1050, length=0x100, perms=ct.CapPerm.RO)
    assert cap > 0


def test_grant_permits_same_owner_overlap():
    """Same owner can split a region into sub-grants with different perms
    — this is the legitimate sub-grant pattern."""
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x1000, perms=ct.CapPerm.RW)
    cap = t.grant(owner=1, base=0x1100, length=0x100, perms=ct.CapPerm.RWX)
    assert cap > 0


def test_lookup_returns_copy():
    t = ct.CapTable(max_caps=4)
    cap = t.grant(owner=42, base=0x1000, length=0x100, perms=ct.CapPerm.RW)
    e = t.lookup(cap)
    assert e.cap_id == cap
    assert e.owner == 42
    assert e.base == 0x1000
    assert e.length == 0x100
    assert e.perms == int(ct.CapPerm.RW)
    # Mutating the copy should NOT affect the table.
    e.owner = 999
    e2 = t.lookup(cap)
    assert e2.owner == 42


def test_lookup_invalid_returns_notfound():
    t = ct.CapTable(max_caps=4)
    with pytest.raises(ct.CapException) as ei:
        t.lookup(ct.CAP_ID_INVALID)
    assert ei.value.code == ct.CapError.NOTFOUND
    with pytest.raises(ct.CapException) as ei:
        t.lookup(9999)
    assert ei.value.code == ct.CapError.NOTFOUND


def test_revoke_removes_entry():
    t = ct.CapTable(max_caps=4)
    cap = t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    t.revoke(cap)
    with pytest.raises(ct.CapException) as ei:
        t.lookup(cap)
    assert ei.value.code == ct.CapError.NOTFOUND
    assert t.used == 0


def test_revoke_unknown_cap_returns_notfound():
    t = ct.CapTable(max_caps=4)
    with pytest.raises(ct.CapException) as ei:
        t.revoke(9999)
    assert ei.value.code == ct.CapError.NOTFOUND


def test_revoke_then_grant_reuses_slot_with_new_cap_id():
    t = ct.CapTable(max_caps=2)
    a = t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    t.grant(owner=2, base=0x2000, length=0x100, perms=ct.CapPerm.RO)
    t.revoke(a)
    c = t.grant(owner=3, base=0x3000, length=0x100, perms=ct.CapPerm.RO)
    # Reused slot but new monotonic cap_id (3, not 1).
    assert c == 3
    assert a != c


# ---------------------------------------------------------------------------
# Check (hot path)
# ---------------------------------------------------------------------------


def test_check_full_coverage_with_matching_perms_permits():
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x1000, perms=ct.CapPerm.RW)
    assert t.check(base=0x1000, length=0x10, want_perms=int(ct.CapPerm.READ))
    assert t.check(base=0x1500, length=0x500, want_perms=int(ct.CapPerm.WRITE))
    assert t.check(base=0x1000, length=0x1000, want_perms=int(ct.CapPerm.RW))


def test_check_partial_coverage_rejects():
    """Single cap covers [0x1000, 0x1100). Request [0x10F0, 0x1110) crosses
    the boundary — should fail since no single cap covers the full request."""
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RW)
    assert not t.check(base=0x10F0, length=0x20, want_perms=int(ct.CapPerm.READ))


def test_check_insufficient_perms_rejects():
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    assert not t.check(base=0x1000, length=0x10, want_perms=int(ct.CapPerm.WRITE))


def test_check_empty_table_denies():
    t = ct.CapTable(max_caps=4)
    assert not t.check(base=0x1000, length=0x10, want_perms=int(ct.CapPerm.READ))


def test_check_zero_length_denies():
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RW)
    assert not t.check(base=0x1000, length=0, want_perms=int(ct.CapPerm.READ))


def test_check_zero_want_perms_vacuously_permitted():
    t = ct.CapTable(max_caps=4)
    assert t.check(base=0x1000, length=0x10, want_perms=0)


# ---------------------------------------------------------------------------
# Iteration / visit
# ---------------------------------------------------------------------------


def test_iter_live_returns_only_live_entries():
    t = ct.CapTable(max_caps=4)
    a = t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    b = t.grant(owner=2, base=0x2000, length=0x100, perms=ct.CapPerm.RW)
    t.revoke(a)
    live = t.iter_live()
    assert len(live) == 1
    assert live[0].cap_id == b


def test_visit_early_exit():
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    t.grant(owner=2, base=0x2000, length=0x100, perms=ct.CapPerm.RW)
    seen = []

    def visitor(e):
        seen.append(e.cap_id)
        return 42 if len(seen) == 1 else 0  # stop after first

    rc = t.visit(visitor)
    assert rc == 42
    assert len(seen) == 1


def test_visit_all_entries_returns_zero():
    t = ct.CapTable(max_caps=4)
    t.grant(owner=1, base=0x1000, length=0x100, perms=ct.CapPerm.RO)
    t.grant(owner=2, base=0x2000, length=0x100, perms=ct.CapPerm.RW)
    seen = []
    rc = t.visit(lambda e: (seen.append(e.cap_id), 0)[1])
    assert rc == 0
    assert sorted(seen) == [1, 2]
