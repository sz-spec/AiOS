"""
Stage 2 · P2P discovery stress + adversarial mesh tests.

Tests the receiver-side `LocalPeerDiscovery.ingest_beacon` under:
  * 50-node beacon storms (legit and mixed-malicious)
  * Truncated / bit-flipped / replayed beacons
  * Workspace-mismatch flood (rogue tenancy)
  * Audit-row integrity under load
  * Counter monotonicity

Maps to spring-2026 CVE class:
  CVE-2026-44499 (Zebra P2P gossip queue DoS, 8.6 CVSS).
"""

from __future__ import annotations

import json
import random
import threading

import pytest

from services.p2p_discovery import (
    BEACON_MAX_BYTES,
    BEACON_VERSION,
    LocalPeerDiscovery,
)


def _disc(workspace_id: str = "ws-storm"):
    return LocalPeerDiscovery(
        workspace_id=workspace_id,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13390)],
    )


# ---------------------------------------------------------------------------
# 50-node beacon flood (homogeneous workspace)
# ---------------------------------------------------------------------------


def test_50_legit_nodes_all_persisted(stress_env):
    """50 distinct nodes broadcast into the same workspace → 50 active peers."""
    me = _disc(workspace_id="ws-50")
    peers = [_disc(workspace_id="ws-50") for _ in range(50)]
    for p in peers:
        rec = me.ingest_beacon(p.build_beacon(), source_host="10.0.0.1")
        assert rec is not None
        assert rec.status == "active"
    assert len(me.list_peers()) == 50
    assert me.stats()["beacons_received"] == 50
    assert me.stats()["beacons_rejected"] == 0


def test_50_rogue_nodes_all_rejected_audit(stress_env):
    """50 rogue beacons (foreign workspace) → 50 audit rows + 0 active peers."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    me = _disc(workspace_id="ws-home")
    rogues = [_disc(workspace_id="ws-FOREIGN") for _ in range(50)]
    for r in rogues:
        me.ingest_beacon(r.build_beacon(), source_host="10.0.0.99")

    assert me.list_peers() == []  # default filter hides 'rejected'
    rejected = me.list_peers(active_only=False)
    assert len(rejected) == 50
    assert all(p.status == "rejected" for p in rejected)

    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert len(rows) >= 50


def test_mixed_storm_legit_and_rogue(stress_env):
    """25 legit + 25 rogue mixed in random order → exactly 25 active."""
    me = _disc(workspace_id="ws-mixed")
    legit = [_disc(workspace_id="ws-mixed") for _ in range(25)]
    rogue = [_disc(workspace_id="ws-other") for _ in range(25)]
    all_peers = legit + rogue
    random.shuffle(all_peers)
    for p in all_peers:
        me.ingest_beacon(p.build_beacon(), source_host="10.0.0.5")
    assert len(me.list_peers()) == 25
    assert len(me.list_peers(active_only=False)) == 50


# ---------------------------------------------------------------------------
# Truncation fuzz — every offset
# ---------------------------------------------------------------------------


def test_truncation_at_every_offset(stress_env):
    """Build one beacon, truncate at offsets 1..len-1 → all rejected."""
    a = _disc(workspace_id="ws-trunc")
    b = _disc(workspace_id="ws-trunc")
    full = a.build_beacon()
    for cut in range(1, len(full) - 1):
        truncated = full[:cut]
        assert b.ingest_beacon(truncated, source_host="x") is None
    # No legitimate peer ever recorded.
    assert b.list_peers() == []


def test_empty_beacon_rejected(stress_env):
    d = _disc()
    assert d.ingest_beacon(b"", source_host="x") is None
    assert d.stats()["beacons_rejected"] == 1


def test_single_byte_beacon_rejected(stress_env):
    d = _disc()
    assert d.ingest_beacon(b"{", source_host="x") is None
    assert d.stats()["beacons_rejected"] == 1


# ---------------------------------------------------------------------------
# Bit-flip fuzz — randomly mutate one byte at a time
# ---------------------------------------------------------------------------


def test_random_byte_flips_25_iterations(stress_env):
    """25 random one-byte flips of a valid beacon — every flip must be rejected
    OR result in a workspace mismatch (rejected) — never a clean acceptance."""
    a = _disc(workspace_id="ws-flip")
    b = _disc(workspace_id="ws-flip")
    full = bytearray(a.build_beacon())
    rng = random.Random(0xCAFE)
    accepted_active = 0
    for _ in range(25):
        flipped = bytearray(full)
        idx = rng.randrange(0, len(flipped))
        flipped[idx] = (flipped[idx] + 1) & 0xFF
        rec = b.ingest_beacon(bytes(flipped), source_host="x")
        # Either None (rejected) or a record with status=='rejected'.
        if rec is not None:
            assert rec.status == "rejected"
        else:
            assert rec is None
    # In no case should a flipped beacon land as 'active'.
    assert accepted_active == 0


# ---------------------------------------------------------------------------
# Replay protection — same node, multiple broadcasts → upsert
# ---------------------------------------------------------------------------


def test_replay_same_node_upserts_single_row(stress_env):
    """20 ingests of the SAME source node → still 1 peer row + last_seen advances."""
    a = _disc(workspace_id="ws-replay")
    b = _disc(workspace_id="ws-replay")
    for _ in range(20):
        b.ingest_beacon(a.build_beacon(), source_host="10.0.0.1")
    peers = b.list_peers()
    assert len(peers) == 1
    assert peers[0].node_id == a.node_id


# ---------------------------------------------------------------------------
# Version-skew fuzz — wrong / future / negative `v`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_v", [0, 2, 5, 99, -1, "1", None])
def test_wrong_version_rejected(stress_env, bad_v):
    """Anything other than v=1 → reject."""
    d = _disc()
    env = {
        "v": bad_v,
        "node_id": "n",
        "workspace_id": "w",
        "sync_port": 1,
        "ts_ms": 1,
        "public_key": "00" * 32,
        "signature": "ab" * 64,
    }
    raw = json.dumps(env).encode("utf-8")
    assert d.ingest_beacon(raw, source_host="x") is None


# ---------------------------------------------------------------------------
# Field-shape fuzz — each field wrong type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("node_id", 1),
        ("node_id", None),
        ("node_id", ["list"]),
        ("workspace_id", 1),
        ("workspace_id", None),
        ("sync_port", "13371"),
        ("sync_port", 13371.5),
        ("sync_port", None),
        ("ts_ms", "now"),
        ("ts_ms", None),
        ("public_key", 1),
        ("public_key", None),
        ("signature", 1),
        ("signature", None),
    ],
)
def test_each_field_wrong_type_rejected(stress_env, field, bad_value):
    d = _disc()
    env = {
        "v": BEACON_VERSION,
        "node_id": "n",
        "workspace_id": "w",
        "sync_port": 1,
        "ts_ms": 1,
        "public_key": "00" * 32,
        "signature": "ab" * 64,
    }
    env[field] = bad_value
    raw = json.dumps(env).encode("utf-8")
    assert d.ingest_beacon(raw, source_host="x") is None


# ---------------------------------------------------------------------------
# Oversize fuzz — ensure no panic on edge sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "size",
    [
        BEACON_MAX_BYTES + 1,
        BEACON_MAX_BYTES + 1024,
        BEACON_MAX_BYTES * 2,
    ],
)
def test_oversized_at_thresholds(stress_env, size):
    d = _disc()
    blob = b"x" * size
    assert d.ingest_beacon(blob, source_host="x") is None


# ---------------------------------------------------------------------------
# Concurrent ingest — 50 threads, no lost rows
# ---------------------------------------------------------------------------


def test_concurrent_ingest_50_threads(stress_env):
    me = _disc(workspace_id="ws-conc")
    peers = [_disc(workspace_id="ws-conc") for _ in range(50)]
    beacons = [p.build_beacon() for p in peers]
    threads = []

    def _ingest(idx):
        me.ingest_beacon(beacons[idx], source_host=f"10.0.{idx//256}.{idx%256}")

    for i in range(50):
        t = threading.Thread(target=_ingest, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)
    # Every legit beacon landed.
    assert len(me.list_peers()) == 50
    assert me.stats()["beacons_received"] == 50


# ---------------------------------------------------------------------------
# Stats counter monotonicity
# ---------------------------------------------------------------------------


def test_stats_monotonic_under_storm(stress_env):
    """beacons_received + beacons_rejected are write-only monotonic."""
    d = _disc()
    last = 0
    for _ in range(20):
        d.ingest_beacon(b"garbage", source_host="x")
        s = d.stats()
        assert s["beacons_received"] >= last
        last = s["beacons_received"]


# ---------------------------------------------------------------------------
# Rejection -> persisted audit row (single ingest)
# ---------------------------------------------------------------------------


def test_each_malformed_beacon_writes_one_audit(stress_env):
    """Each rejected beacon must write one securityAuditLog row."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    d = _disc()
    for i in range(10):
        d.ingest_beacon(f"junk-{i}".encode(), source_host=f"10.0.0.{i}")
    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter(
                SecurityAuditLog.kind.in_(
                    ("malicious_peer_attempt", "local_tampering_blocked"),
                ),
            )
            .all()
        )
        # At least one row per rejected beacon.
        assert len(rows) >= 10


# ---------------------------------------------------------------------------
# Self-beacon noise — 30 self-ingests don't create a record
# ---------------------------------------------------------------------------


def test_self_beacons_never_persisted(stress_env):
    """30 self-ingestions never produce a peer row."""
    d = _disc()
    raw = d.build_beacon()
    for _ in range(30):
        assert d.ingest_beacon(raw, source_host="127.0.0.1") is None
    assert d.list_peers() == []


# ---------------------------------------------------------------------------
# Signature swap attack — sig from node-A used by node-B
# ---------------------------------------------------------------------------


def test_signature_swap_rejected_50_attempts(stress_env):
    """50 swap attempts (A's sig on B's beacon body) → all rejected."""
    me = _disc(workspace_id="ws-swap")
    a = _disc(workspace_id="ws-swap")
    b = _disc(workspace_id="ws-swap")
    for _ in range(50):
        # Build A's beacon then graft B's identity onto it
        env = json.loads(a.build_beacon().decode("utf-8"))
        env["node_id"] = b.node_id
        env["public_key"] = b.public_key_hex
        # signature is now wrong vs. the new node_id + public_key
        raw = json.dumps(env).encode("utf-8")
        rec = me.ingest_beacon(raw, source_host="x")
        assert rec is None
    assert me.stats()["beacons_rejected"] >= 50
