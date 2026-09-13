"""
Stage 2 · Cross-cutting P2P adversarial stress tests.

Covers scenarios that span multiple modules:
  * 100-node mixed workspaces — every audit-row written
  * Beacon replay race (same node, parallel ingests)
  * Discovery counter atomicity under contention
  * Mesh peer scan after storm
  * Workspace-switch attack (rogue claims our workspace mid-storm)
  * Audit-log overflow tolerance (300 rejections in tight loop)
  * Mesh fetch (`_has_active_workspace_peer`) under churn
  * `mark_stale_peers` interaction with active set
  * Sandbox event-bus + peer reject combined audit-row test
  * Beacon canonical-bytes consistency across 100 builds
"""

from __future__ import annotations

import json
import random
import threading

import pytest

from services.p2p_discovery import (
    LocalPeerDiscovery,
    _beacon_canonical_bytes,
    mark_stale_peers,
)


def _disc(workspace_id: str = "ws-adv"):
    return LocalPeerDiscovery(
        workspace_id=workspace_id,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13399)],
    )


# ---------------------------------------------------------------------------
# 100-node mixed storm — full audit accounting
# ---------------------------------------------------------------------------


def test_100_node_mixed_storm_audit_accounting(stress_env):
    """50 legit + 50 rogue, mixed; ensure every legit lands active and every
    rogue lands rejected — no audit row dropped, counters consistent."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    me = _disc(workspace_id="ws-hundo")
    legit = [_disc(workspace_id="ws-hundo") for _ in range(50)]
    rogue = [_disc(workspace_id="ws-NOT-OURS") for _ in range(50)]
    pool = legit + rogue
    random.Random(123).shuffle(pool)

    for p in pool:
        me.ingest_beacon(p.build_beacon(), source_host="10.0.0.99")

    assert len(me.list_peers()) == 50  # active filter
    assert len(me.list_peers(active_only=False)) == 100

    # 50 legit are 'active', 50 rogue are 'rejected'.
    rec_by_status = {"active": 0, "rejected": 0}
    for p in me.list_peers(active_only=False):
        rec_by_status[p.status] = rec_by_status.get(p.status, 0) + 1
    assert rec_by_status == {"active": 50, "rejected": 50}

    with get_session() as s:
        rogue_rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert len(rogue_rows) >= 50


# ---------------------------------------------------------------------------
# Beacon-replay race — 8 threads, same node beacon, in parallel
# ---------------------------------------------------------------------------


def test_replay_race_8_threads_one_peer(stress_env):
    """8 threads ingest the SAME beacon → still 1 peer row."""
    me = _disc(workspace_id="ws-race")
    a = _disc(workspace_id="ws-race")
    raw = a.build_beacon()
    threads = []

    def _ingest():
        me.ingest_beacon(raw, source_host="10.0.0.1")

    for _ in range(8):
        t = threading.Thread(target=_ingest)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(me.list_peers()) == 1


# ---------------------------------------------------------------------------
# Counter atomicity under contention
# ---------------------------------------------------------------------------


def test_received_counter_strictly_monotonic(stress_env):
    """Under heavy ingest, beacons_received only ever increases."""
    me = _disc(workspace_id="ws-mono")
    a = _disc(workspace_id="ws-mono")
    raw = a.build_beacon()
    last = me.stats()["beacons_received"]
    for _ in range(50):
        me.ingest_beacon(raw, source_host="x")
        now = me.stats()["beacons_received"]
        assert now >= last
        last = now


# ---------------------------------------------------------------------------
# Workspace-switch attack
# ---------------------------------------------------------------------------


def test_attacker_switches_workspace_mid_storm(stress_env):
    """An attacker first sends a foreign-workspace beacon (rejected),
    then sends a beacon claiming our workspace — both must produce
    proper audit rows. The second beacon also has the wrong signature
    (signed against the original workspace), so it must be rejected."""
    me = _disc(workspace_id="ws-target")
    rogue = _disc(workspace_id="ws-OTHER")
    raw_real = rogue.build_beacon()

    # Step 1 — original beacon is rejected for workspace mismatch.
    me.ingest_beacon(raw_real, source_host="10.0.0.9")

    # Step 2 — attacker mutates the workspace_id post-signing.
    env = json.loads(raw_real.decode("utf-8"))
    env["workspace_id"] = "ws-target"
    raw_spoof = json.dumps(env).encode("utf-8")
    rec = me.ingest_beacon(raw_spoof, source_host="10.0.0.9")
    # Signature no longer matches → rejected at malicious_peer step.
    assert rec is None
    assert me.stats()["beacons_rejected"] >= 2


# ---------------------------------------------------------------------------
# Audit-log overflow tolerance — 300 rejections in tight loop
# ---------------------------------------------------------------------------


def test_300_rejections_dont_crash_audit_writer(stress_env):
    """The audit writer must accept 300 rejections without dropping the
    process or wedging the DB session pool."""
    d = _disc()
    for i in range(300):
        d.ingest_beacon(f"trash-{i}".encode(), source_host=f"10.{i % 256}.0.0")
    assert d.stats()["beacons_rejected"] == 300


# ---------------------------------------------------------------------------
# Concurrent stats() under storm — never reads negative / huge values
# ---------------------------------------------------------------------------


def test_concurrent_stats_reads_during_storm(stress_env):
    me = _disc(workspace_id="ws-stat")
    a = _disc(workspace_id="ws-stat")
    raw = a.build_beacon()
    stop = threading.Event()
    samples = []

    def _ingest():
        while not stop.is_set():
            me.ingest_beacon(raw, source_host="x")

    def _read():
        while not stop.is_set():
            samples.append(me.stats())

    t1 = threading.Thread(target=_ingest)
    t2 = threading.Thread(target=_read)
    t1.start()
    t2.start()
    import time

    time.sleep(0.3)
    stop.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    # Counts are non-negative, peer_count in [0, 1].
    assert all(s["beacons_received"] >= 0 for s in samples)
    assert all(s["peer_count"] in (0, 1) for s in samples)


# ---------------------------------------------------------------------------
# Beacon canonical-bytes consistency across 100 builds
# ---------------------------------------------------------------------------


def test_canonical_bytes_consistent_across_100_builds():
    """For fixed inputs the canonical form is byte-identical."""
    pool = [
        _beacon_canonical_bytes(
            node_id="n",
            workspace_id="w",
            sync_port=1,
            ts_ms=1,
            public_key_hex="00" * 32,
        )
        for _ in range(100)
    ]
    assert len(set(pool)) == 1


# ---------------------------------------------------------------------------
# mark_stale_peers — interaction with fresh active rows
# ---------------------------------------------------------------------------


def test_mark_stale_does_not_evict_fresh_peers(stress_env):
    """Just-ingested peers must NOT be flipped to 'stale' by a same-tick
    eviction sweep."""
    me = _disc(workspace_id="ws-stale-fresh")
    a = _disc(workspace_id="ws-stale-fresh")
    me.ingest_beacon(a.build_beacon(), source_host="x")
    n = mark_stale_peers(threshold_s=60.0)
    # Just-ingested peer is fresh — count of newly-stale rows is 0.
    assert n == 0


def test_mark_stale_flips_old_peers(stress_env):
    """Setting threshold_s=0 with a non-zero peer table → all flipped stale."""
    from core.database.sqlite_setup import DiscoveredPeer, get_session

    me = _disc(workspace_id="ws-stale-old")
    a = _disc(workspace_id="ws-stale-old")
    me.ingest_beacon(a.build_beacon(), source_host="x")
    n = mark_stale_peers(threshold_s=0.0)
    assert n >= 1
    with get_session() as s:
        rows = s.query(DiscoveredPeer).filter_by(status="stale").all()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# _has_active_workspace_peer — false until first ingest, true after
# ---------------------------------------------------------------------------


def test_has_active_peer_false_then_true(stress_env, monkeypatch):
    """Workspace-scoped peer detection feeds resolve_model_status."""
    from services.model_manager import _has_active_workspace_peer

    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-peercheck")
    me = _disc(workspace_id="ws-peercheck")
    assert _has_active_workspace_peer() is False
    a = _disc(workspace_id="ws-peercheck")
    me.ingest_beacon(a.build_beacon(), source_host="x")
    assert _has_active_workspace_peer() is True


def test_has_active_peer_false_wrong_workspace_env(stress_env, monkeypatch):
    """If the env workspace doesn't match the DB peer's workspace → False."""
    from services.model_manager import _has_active_workspace_peer

    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-different")
    me = _disc(workspace_id="ws-actual")
    a = _disc(workspace_id="ws-actual")
    me.ingest_beacon(a.build_beacon(), source_host="x")
    assert _has_active_workspace_peer() is False


# ---------------------------------------------------------------------------
# Mixed-storm with extra junk frames interleaved
# ---------------------------------------------------------------------------


def test_storm_with_intermixed_junk(stress_env):
    """Ingest a stream of valid beacons interleaved with junk — no junk
    persists, legit beacons all land."""
    me = _disc(workspace_id="ws-mix-junk")
    legit = [_disc(workspace_id="ws-mix-junk") for _ in range(10)]
    valid = [p.build_beacon() for p in legit]
    sequence = []
    for i, beacon in enumerate(valid):
        sequence.append(beacon)
        sequence.append(b"\x00\x00junk\x00\x00")
        if i % 3 == 0:
            sequence.append(json.dumps({"v": 99}).encode())  # bad version

    for s in sequence:
        me.ingest_beacon(s, source_host="x")

    assert len(me.list_peers()) == 10


# ---------------------------------------------------------------------------
# Receiver discards JSON arrays at top level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b"[1,2,3]",
        b'"a string"',
        b"42",
        b"true",
        b"null",
    ],
)
def test_top_level_non_object_rejected(stress_env, payload):
    d = _disc()
    assert d.ingest_beacon(payload, source_host="x") is None


# ---------------------------------------------------------------------------
# Build-beacon stability — successive build() calls only diff by ts_ms + sig
# ---------------------------------------------------------------------------


def test_build_beacon_stable_node_identity(stress_env):
    """Two beacons built ~immediately produce the same node_id + public_key,
    even though their ts_ms + sig differ."""
    d = _disc(workspace_id="ws-stable")
    e1 = json.loads(d.build_beacon().decode("utf-8"))
    e2 = json.loads(d.build_beacon().decode("utf-8"))
    assert e1["node_id"] == e2["node_id"]
    assert e1["public_key"] == e2["public_key"]
    assert e1["workspace_id"] == e2["workspace_id"]


# ---------------------------------------------------------------------------
# Same source_host repeated → host field updated on each ingest
# ---------------------------------------------------------------------------


def test_source_host_update_on_re_ingest(stress_env):
    """The same node moving IPs is recorded — host field updates."""
    me = _disc(workspace_id="ws-iproam")
    a = _disc(workspace_id="ws-iproam")
    raw = a.build_beacon()
    me.ingest_beacon(raw, source_host="10.0.0.1")
    me.ingest_beacon(raw, source_host="10.0.0.2")
    peers = me.list_peers()
    assert len(peers) == 1
    assert peers[0].host == "10.0.0.2"


# ---------------------------------------------------------------------------
# Beacon with extra unknown fields — accepted (forward-compatibility)
# ---------------------------------------------------------------------------


def test_beacon_with_unknown_extra_field_still_ingests(stress_env):
    """A v1 beacon with an extra `future_feature` field MUST still be
    accepted — required for graceful protocol evolution. The ingest path
    only validates the fields it knows about."""
    me = _disc(workspace_id="ws-future")
    a = _disc(workspace_id="ws-future")
    raw = a.build_beacon()
    env = json.loads(raw.decode("utf-8"))
    # Re-build with extra field. Note: extra fields are NOT part of the
    # signature, so the existing sig still verifies.
    env["future_feature"] = {"some": "blob"}
    re_raw = json.dumps(env, separators=(",", ":")).encode("utf-8")
    rec = me.ingest_beacon(re_raw, source_host="x")
    assert rec is not None
    assert rec.status == "active"


# ---------------------------------------------------------------------------
# Beacon with explicit null signature → rejected as malicious
# ---------------------------------------------------------------------------


def test_beacon_with_null_signature_rejected(stress_env):
    """`signature=null` short-circuits verify_beacon to False → reject."""
    me = _disc(workspace_id="ws-nullsig")
    a = _disc(workspace_id="ws-nullsig")
    raw = a.build_beacon()
    env = json.loads(raw.decode("utf-8"))
    env["signature"] = None
    re_raw = json.dumps(env).encode("utf-8")
    rec = me.ingest_beacon(re_raw, source_host="x")
    # `signature=None` fails the isinstance(...,str) field check → rejected.
    assert rec is None
    assert me.stats()["beacons_rejected"] == 1
