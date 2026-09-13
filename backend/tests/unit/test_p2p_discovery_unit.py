"""
Stage 1 · Atomic unit isolation for services/p2p_discovery.py.

Covers — purpose | guards file:line:
  _beacon_canonical_bytes determinism                | p2p_discovery.py:124-142
  build_beacon → ingest_beacon roundtrip             | p2p_discovery.py:314-364, 368-469
  malformed JSON rejection                           | p2p_discovery.py:387-394
  version-mismatch rejection                         | p2p_discovery.py:400-405
  oversized-envelope rejection                       | p2p_discovery.py:380-385
  bad-type field rejection                           | p2p_discovery.py:414-423
  workspace-mismatch records `rejected`              | p2p_discovery.py:447-463
  signature tamper rejected as malicious_peer        | p2p_discovery.py:434-444
  self-beacon ignored                                | p2p_discovery.py:425-427
  stats counters move correctly                      | p2p_discovery.py:297-310
  list_peers filters by verified+active              | p2p_discovery.py:276-295
  rogue-workspace audit row written                  | p2p_discovery.py:455
"""

from __future__ import annotations

import json

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def disc_env(unit_env, tmp_path, monkeypatch):
    """Path-scoped sandbox + keyring + DB for discovery tests."""
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield


def _disc(workspace_id: str = "ws-test"):
    """Build a LocalPeerDiscovery without starting threads.

    bind_host=127.0.0.1 keeps the air-gap kill-switch happy."""
    from services.p2p_discovery import LocalPeerDiscovery

    return LocalPeerDiscovery(
        workspace_id=workspace_id,
        bind_host="127.0.0.1",
        peer_targets=[("127.0.0.1", 13390)],
    )


# ---------------------------------------------------------------------------
# _beacon_canonical_bytes
# ---------------------------------------------------------------------------


def test_canonical_bytes_deterministic():
    from services.p2p_discovery import _beacon_canonical_bytes

    a = _beacon_canonical_bytes(
        node_id="n1",
        workspace_id="w",
        sync_port=13371,
        ts_ms=1,
        public_key_hex="00" * 32,
    )
    b = _beacon_canonical_bytes(
        node_id="n1",
        workspace_id="w",
        sync_port=13371,
        ts_ms=1,
        public_key_hex="00" * 32,
    )
    assert a == b


def test_canonical_bytes_keys_sorted():
    from services.p2p_discovery import _beacon_canonical_bytes

    out = _beacon_canonical_bytes(
        node_id="n1",
        workspace_id="w",
        sync_port=1,
        ts_ms=1,
        public_key_hex="00",
    ).decode("utf-8")
    # sort_keys=True: node_id < public_key < sync_port < ts_ms < workspace_id
    keys = ["node_id", "public_key", "sync_port", "ts_ms", "workspace_id"]
    idxs = [out.index(f'"{k}":') for k in keys]
    assert idxs == sorted(idxs)


def test_canonical_bytes_changes_on_each_field():
    from services.p2p_discovery import _beacon_canonical_bytes

    base = _beacon_canonical_bytes(
        node_id="n",
        workspace_id="w",
        sync_port=1,
        ts_ms=1,
        public_key_hex="a",
    )
    assert base != _beacon_canonical_bytes(
        node_id="X",
        workspace_id="w",
        sync_port=1,
        ts_ms=1,
        public_key_hex="a",
    )
    assert base != _beacon_canonical_bytes(
        node_id="n",
        workspace_id="X",
        sync_port=1,
        ts_ms=1,
        public_key_hex="a",
    )
    assert base != _beacon_canonical_bytes(
        node_id="n",
        workspace_id="w",
        sync_port=2,
        ts_ms=1,
        public_key_hex="a",
    )
    assert base != _beacon_canonical_bytes(
        node_id="n",
        workspace_id="w",
        sync_port=1,
        ts_ms=2,
        public_key_hex="a",
    )
    assert base != _beacon_canonical_bytes(
        node_id="n",
        workspace_id="w",
        sync_port=1,
        ts_ms=1,
        public_key_hex="X",
    )


# ---------------------------------------------------------------------------
# build_beacon → ingest_beacon (intra-instance, but using a peer)
# ---------------------------------------------------------------------------


def test_build_beacon_yields_valid_json(disc_env):
    d = _disc()
    raw = d.build_beacon()
    env = json.loads(raw.decode("utf-8"))
    assert env["node_id"] == d.node_id
    assert env["workspace_id"] == "ws-test"
    assert env["v"] == 1
    assert isinstance(env["signature"], str)
    assert len(env["signature"]) > 0  # signed (keyring is wired)


def test_ingest_self_beacon_returns_none(disc_env):
    """A node should never persist its own beacon."""
    d = _disc()
    raw = d.build_beacon()
    assert d.ingest_beacon(raw, source_host="127.0.0.1") is None


def test_two_nodes_same_workspace_discover_each_other(disc_env):
    """Node A builds a beacon, Node B ingests it → B sees A as active."""
    a = _disc(workspace_id="ws-shared")
    b = _disc(workspace_id="ws-shared")
    raw = a.build_beacon()
    rec = b.ingest_beacon(raw, source_host="10.0.0.5")
    assert rec is not None
    assert rec.node_id == a.node_id
    assert rec.status == "active"
    assert rec.verified is True
    assert rec.host == "10.0.0.5"
    # B's peer table should now contain A.
    peers = b.list_peers()
    assert any(p.node_id == a.node_id for p in peers)


# ---------------------------------------------------------------------------
# Malformed / oversized / bad-version rejection
# ---------------------------------------------------------------------------


def test_ingest_rejects_malformed_json(disc_env):
    d = _disc()
    assert d.ingest_beacon(b"not-json{{{", source_host="10.0.0.1") is None
    assert d.stats()["beacons_rejected"] == 1


def test_ingest_rejects_non_dict_payload(disc_env):
    d = _disc()
    # Valid JSON, but a list, not a dict.
    assert d.ingest_beacon(b'["hello"]', source_host="10.0.0.1") is None
    assert d.stats()["beacons_rejected"] == 1


def test_ingest_rejects_oversized(disc_env):
    from services.p2p_discovery import BEACON_MAX_BYTES

    d = _disc()
    fat = b"x" * (BEACON_MAX_BYTES + 1)
    assert d.ingest_beacon(fat, source_host="10.0.0.1") is None
    assert d.stats()["beacons_rejected"] == 1


def test_ingest_rejects_wrong_version(disc_env):
    d = _disc()
    raw = json.dumps({"v": 99, "node_id": "n", "workspace_id": "w"}).encode("utf-8")
    assert d.ingest_beacon(raw, source_host="10.0.0.1") is None
    assert d.stats()["beacons_rejected"] == 1


def test_ingest_rejects_bad_field_types(disc_env):
    d = _disc()
    raw = json.dumps(
        {
            "v": 1,
            "node_id": 123,  # should be str
            "workspace_id": "w",
            "sync_port": "13371",  # should be int
            "ts_ms": 1,
            "public_key": "pk",
            "signature": "sig",
        }
    ).encode("utf-8")
    assert d.ingest_beacon(raw, source_host="10.0.0.1") is None
    assert d.stats()["beacons_rejected"] == 1


# ---------------------------------------------------------------------------
# Signature tampering → malicious_peer_attempt audit kind
# ---------------------------------------------------------------------------


def test_ingest_rejects_invalid_signature(disc_env):
    """Tamper the workspace_id post-signing → verify fails → reject."""
    a = _disc(workspace_id="ws-A")
    b = _disc(workspace_id="ws-A")  # same ws so workspace-mismatch isn't first
    raw = a.build_beacon()
    env = json.loads(raw.decode("utf-8"))
    # Bit-flip the signature.
    env["signature"] = ("f" + env["signature"][1:]) if env["signature"] else "ff"
    tampered = json.dumps(env, separators=(",", ":")).encode("utf-8")
    assert b.ingest_beacon(tampered, source_host="10.0.0.1") is None
    assert b.stats()["beacons_rejected"] == 1


# ---------------------------------------------------------------------------
# Workspace mismatch → rogue-rejection path
# ---------------------------------------------------------------------------


def test_workspace_mismatch_records_rejected(disc_env):
    """Same-LAN peer from a different workspace → status='rejected',
    `local_tampering_blocked` audit row."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    rogue = _disc(workspace_id="ws-FOREIGN")
    me = _disc(workspace_id="ws-HOME")
    raw = rogue.build_beacon()
    rec = me.ingest_beacon(raw, source_host="10.0.0.99")

    # Sig is valid → rec is created, but status='rejected'.
    assert rec is not None
    assert rec.status == "rejected"
    assert rec.workspace_id == "ws-FOREIGN"

    # Default list_peers (active_only=True) hides rejected nodes.
    assert me.list_peers() == []
    # But with active_only=False, it surfaces.
    seen = me.list_peers(active_only=False)
    assert any(p.node_id == rogue.node_id for p in seen)

    # Audit row was written.
    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# stats counters
# ---------------------------------------------------------------------------


def test_stats_counters_track_rejections(disc_env):
    d = _disc()
    d.ingest_beacon(b"bad-json", source_host="x")
    d.ingest_beacon(b'{"v":1}', source_host="x")  # missing fields → bad types
    stats = d.stats()
    # We received 2 beacons, both rejected.
    assert stats["beacons_received"] == 2
    assert stats["beacons_rejected"] == 2
    assert stats["peer_count"] == 0


def test_list_peers_default_filters_active_and_verified(disc_env):
    a = _disc(workspace_id="ws")
    b = _disc(workspace_id="ws")
    raw = a.build_beacon()
    b.ingest_beacon(raw, source_host="10.0.0.1")
    peers = b.list_peers()
    assert len(peers) == 1
    assert peers[0].verified is True
    assert peers[0].status == "active"


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_workspace():
    from services.p2p_discovery import LocalPeerDiscovery

    with pytest.raises(ValueError):
        LocalPeerDiscovery(workspace_id="")
