"""
P6.2 — Air-gapped P2P Mesh Sync E2E tests.

The harness is fully loopback-bound — the air-gap kill-switch
permits UDP `sendto` to 127.0.0.1 and TCP connect to 127.0.0.1, so
the entire P2P contract can be exercised inside one Python process
without ever touching a real network.

Coverage groups:

  1. Discovery (UDP):
     - Two nodes, same workspace_id → both record the other peer.
     - Beacon signatures verify on receive.
     - Beacon dedup across multiple ticks.
     - `discoveredPeers` SQLite mirror is populated.

  2. Secure handshake + delta exchange (TCP + ECDH):
     - establish_session over loopback derives matching Fernet keys.
     - Encrypted frames round-trip.
     - `state.compare` and `state.delta` return identical summaries on
       both ends when their `apps` tables match.

  3. Rogue rejection — workspace_id divergence:
     - Beacon from a different workspace lands in
       `securityAuditLog` as `local_tampering_blocked`.
     - Handshake from a different workspace tears down BEFORE any
       payload is exchanged AND writes the same audit row.
     - HTTP route gates: a peer asserting a different workspace_id
       on /api/p2p/sync/compare or /sync/delta gets 403 + audit row.
"""

from __future__ import annotations

import json
import socket
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    DiscoveredPeer,
    SecurityAuditLog,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    _reset_gate_for_tests,
)
from services.crypto_keyring import KEYRING
from services.p2p_discovery import (
    BEACON_VERSION,
    LocalPeerDiscovery,
    PeerRecord,
    mark_stale_peers,
)
from services.p2p_sync import (
    HandshakeFailed,
    SovereignSyncEngine,
    SovereignSyncServer,
    compute_table_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env, tmp_path, monkeypatch):
    """Pin keyring to local fallback + a per-test secrets file + a
    per-test machine-id seed so signing keys don't bleed across
    tests."""
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "p6.2-test-seed")
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()


def _signer_factory():
    """Mint a fresh independent identity keypair per node — each
    LocalPeerDiscovery / SovereignSyncEngine instance owns its OWN
    (priv, pub) so signature verification on the receiver tests the
    real crypto path rather than a self-recognition trick."""
    from services.app_crypto import generate_keypair_hex

    priv_hex, pub_hex = generate_keypair_hex()

    def _signer(_):
        return priv_hex, pub_hex

    return _signer, pub_hex


def _free_port() -> int:
    """Return an unused TCP port via the OS ephemeral range."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_app(workspace_id: str, *, name: str = "p62-app") -> str:
    """Install a sandboxed app into a workspace. Returns the app_id.

    Used by the delta tests to give the responder some real rows to
    return.
    """
    res = SANDBOX_MANAGER.install(
        {"name": name, "version": "1.0.0", "scopes": ["filesystem.read"]},
        workspace_id=workspace_id,
    )
    return res["app_id"]


def _make_discovery(
    *,
    workspace_id: str,
    bind_port: int,
    sync_port: int = 0,
    targets=None,
    node_id=None,
):
    signer, _ = _signer_factory()
    return LocalPeerDiscovery(
        node_id=node_id or str(uuid.uuid4()),
        workspace_id=workspace_id,
        discovery_port=bind_port,
        sync_port=sync_port or _free_port(),
        bind_host="127.0.0.1",
        peer_targets=targets,
        broadcast_interval_s=0.2,
        signer=signer,
    )


# ---------------------------------------------------------------------------
# (1) Discovery — UDP beacons + signed envelope verification
# ---------------------------------------------------------------------------


def test_beacon_envelope_is_signed_and_canonical(local_db):
    """beacon JSON includes all required fields; the signature
    verifies against the embedded public key."""
    disc = _make_discovery(
        workspace_id="ws-alpha",
        bind_port=_free_udp_port(),
        targets=[],
    )
    blob = disc.build_beacon()
    envelope = json.loads(blob.decode("utf-8"))
    for field in (
        "v",
        "node_id",
        "workspace_id",
        "sync_port",
        "ts_ms",
        "public_key",
        "signature",
    ):
        assert field in envelope, f"missing {field}"
    assert envelope["v"] == BEACON_VERSION
    assert envelope["workspace_id"] == "ws-alpha"
    assert len(envelope["signature"]) == 128
    assert len(envelope["public_key"]) == 64

    # Re-verify the signature using the same canonical-bytes contract.
    from services.app_crypto import verify_manifest_signature
    from services.p2p_discovery import _beacon_canonical_bytes

    canon = _beacon_canonical_bytes(
        node_id=envelope["node_id"],
        workspace_id=envelope["workspace_id"],
        sync_port=envelope["sync_port"],
        ts_ms=envelope["ts_ms"],
        public_key_hex=envelope["public_key"],
    )
    assert (
        verify_manifest_signature(
            canon,
            envelope["signature"],
            envelope["public_key"],
        )
        is True
    )


def test_two_nodes_discover_each_other_via_udp(local_db):
    """Test 1 of the directive — 2 nodes in the SAME workspace
    auto-discover via UDP broadcast on loopback."""
    port_a = _free_udp_port()
    port_b = _free_udp_port()

    node_a = _make_discovery(
        workspace_id="ws-alpha",
        bind_port=port_a,
        targets=[("127.0.0.1", port_b)],
    )
    node_b = _make_discovery(
        workspace_id="ws-alpha",
        bind_port=port_b,
        targets=[("127.0.0.1", port_a)],
    )
    try:
        node_a.start()
        node_b.start()
        # Each broadcaster ticks every 0.2s — give them ~1.5s of
        # wall time so multiple beacons have round-tripped.
        deadline = time.time() + 3.0
        while time.time() < deadline:
            peers_a = node_a.list_peers()
            peers_b = node_b.list_peers()
            if peers_a and peers_b:
                break
            time.sleep(0.1)
        peers_a = node_a.list_peers()
        peers_b = node_b.list_peers()
        assert len(peers_a) == 1, f"expected node_a to see node_b, got {peers_a}"
        assert len(peers_b) == 1, f"expected node_b to see node_a, got {peers_b}"
        assert peers_a[0].node_id == node_b.node_id
        assert peers_b[0].node_id == node_a.node_id
        assert peers_a[0].workspace_id == "ws-alpha"
        assert peers_a[0].verified is True
        assert peers_a[0].status == "active"
    finally:
        node_a.stop()
        node_b.stop()


def test_discovered_peer_table_mirrors_in_memory_state(local_db):
    """The discoveredPeers SQLite table is the durable mirror of the
    LocalPeerDiscovery._peers in-memory cache."""
    port_a = _free_udp_port()
    port_b = _free_udp_port()
    node_a = _make_discovery(
        workspace_id="ws-mirror",
        bind_port=port_a,
        targets=[],
    )
    node_b = _make_discovery(
        workspace_id="ws-mirror",
        bind_port=port_b,
        targets=[],
    )
    # Manually push one beacon from B → A.
    beacon = node_b.build_beacon()
    rec = node_a.ingest_beacon(beacon, source_host="127.0.0.1")
    assert isinstance(rec, PeerRecord)
    assert rec.status == "active"
    with get_session() as session:
        rows = session.query(DiscoveredPeer).all()
        assert len(rows) == 1
        assert rows[0].nodeId == node_b.node_id
        assert rows[0].workspaceId == "ws-mirror"
        assert rows[0].verified is True
        assert rows[0].status == "active"


def test_repeated_beacons_dedupe_in_table(local_db):
    """A peer that keeps broadcasting only OWNS ONE row — repeated
    beacons update `lastSeenAt` rather than inserting duplicates."""
    node_a = _make_discovery(
        workspace_id="ws-dedupe",
        bind_port=_free_udp_port(),
        targets=[],
    )
    node_b = _make_discovery(
        workspace_id="ws-dedupe",
        bind_port=_free_udp_port(),
        targets=[],
    )
    rec1 = node_a.ingest_beacon(node_b.build_beacon(), source_host="127.0.0.1")
    time.sleep(0.005)
    rec2 = node_a.ingest_beacon(node_b.build_beacon(), source_host="127.0.0.1")
    assert rec1.node_id == rec2.node_id
    with get_session() as session:
        rows = (
            session.query(DiscoveredPeer)
            .filter_by(
                nodeId=node_b.node_id,
            )
            .all()
        )
        assert len(rows) == 1
        # lastSeenAt advanced (or stayed equal due to clock resolution).
        assert rows[0].lastSeenAt >= rec1.last_seen_ms


def test_own_beacon_is_ignored(local_db):
    """Ingesting our OWN beacon (loopback reflection) does not
    create a self-row."""
    node = _make_discovery(
        workspace_id="ws-self",
        bind_port=_free_udp_port(),
        targets=[],
    )
    rec = node.ingest_beacon(node.build_beacon(), source_host="127.0.0.1")
    assert rec is None
    with get_session() as session:
        assert session.query(DiscoveredPeer).count() == 0


def test_unsigned_beacon_is_rejected_and_audited(local_db):
    """A beacon with an empty/missing signature lands in
    securityAuditLog as `malicious_peer_attempt`."""
    node = _make_discovery(
        workspace_id="ws-unsigned",
        bind_port=_free_udp_port(),
        targets=[],
    )
    fake = json.dumps(
        {
            "v": BEACON_VERSION,
            "node_id": str(uuid.uuid4()),
            "workspace_id": "ws-unsigned",
            "sync_port": 9999,
            "ts_ms": int(time.time() * 1000),
            "public_key": "00" * 32,
            "signature": "",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    assert node.ingest_beacon(fake, source_host="127.0.0.1") is None

    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="malicious_peer_attempt",
            )
            .all()
        )
        assert rows, "missing malicious_peer_attempt audit row"
    # And no peer landed in the DB.
    with get_session() as session:
        assert session.query(DiscoveredPeer).count() == 0


def test_mark_stale_peers_demotes_idle_rows(local_db):
    """The stale-eviction sweep flips active rows older than
    threshold_s to status='stale'."""
    node = _make_discovery(
        workspace_id="ws-stale",
        bind_port=_free_udp_port(),
        targets=[],
    )
    other = _make_discovery(
        workspace_id="ws-stale",
        bind_port=_free_udp_port(),
        targets=[],
    )
    node.ingest_beacon(other.build_beacon(), source_host="127.0.0.1")
    # Backdate the row in the DB to look ancient.
    with get_session() as session:
        row = (
            session.query(DiscoveredPeer)
            .filter_by(
                nodeId=other.node_id,
            )
            .one()
        )
        row.lastSeenAt = 0  # epoch
        session.commit()
    n_stale = mark_stale_peers(threshold_s=1.0)
    assert n_stale == 1
    with get_session() as session:
        row = (
            session.query(DiscoveredPeer)
            .filter_by(
                nodeId=other.node_id,
            )
            .one()
        )
        assert row.status == "stale"


# ---------------------------------------------------------------------------
# (2) Secure handshake + delta exchange (TCP + ECDH)
# ---------------------------------------------------------------------------


def _start_tcp_responder(workspace_id: str):
    """Spin a SovereignSyncServer on a free loopback port. Returns
    (server, engine, signer_pub). Caller must call server.shutdown()."""
    signer, pub = _signer_factory()
    engine = SovereignSyncEngine(
        node_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        identity_signer=signer,
    )
    server = SovereignSyncServer(
        engine,
        host="127.0.0.1",
        port=0,
    )
    server.start_in_thread()
    return server, engine, pub


def test_handshake_succeeds_within_same_workspace_and_round_trips_payload(local_db):
    """Test 2 of the directive — handshake + encrypted payload exchange."""
    server, server_engine, server_pub = _start_tcp_responder("ws-handshake")
    try:
        client_signer, client_pub = _signer_factory()
        client_engine = SovereignSyncEngine(
            node_id="client-node",
            workspace_id="ws-handshake",
            identity_signer=client_signer,
        )
        session = client_engine.establish_session(
            "127.0.0.1",
            server.port,
        )
        try:
            assert session.metadata.peer_workspace_id == "ws-handshake"
            assert session.metadata.peer_identity_pk == server_pub
            # Round-trip a compare request.
            response = session.compare_state("apps", fields=[])
            assert response["op"] == "state.compare.ack"
            assert response["table"] == "apps"
            assert "summary" in response
        finally:
            session.close()
    finally:
        server.shutdown()
        server.server_close()


def test_handshake_delta_returns_workspace_scoped_rows(local_db):
    """Both ends should see the SAME `apps` rows for the shared
    workspace — proving the encrypted channel actually delivered
    the responder's local-state read."""
    ws = "ws-delta"
    # Seed the responder's apps table.
    app_id = _seed_app(ws)
    server, server_engine, _ = _start_tcp_responder(ws)
    try:
        client_signer, _ = _signer_factory()
        client_engine = SovereignSyncEngine(
            node_id="client-delta",
            workspace_id=ws,
            identity_signer=client_signer,
        )
        session = client_engine.establish_session("127.0.0.1", server.port)
        try:
            response = session.request_delta("apps", since_ms=0)
            assert response["op"] == "state.delta.ack"
            assert response["table"] == "apps"
            rows = response["rows"]
            assert isinstance(rows, list)
            app_ids = [r.get("id") for r in rows]
            assert (
                app_id in app_ids
            ), f"expected seeded app_id {app_id} in delta rows, got {app_ids}"
        finally:
            session.close()
    finally:
        server.shutdown()
        server.server_close()


def test_compare_summary_changes_when_apps_table_mutates(local_db):
    """`compute_table_summary` is a cheap fingerprint — adding a new
    row in the workspace flips the hash."""
    ws = "ws-fingerprint"
    summary_before = compute_table_summary("apps", workspace_id=ws)
    assert summary_before["row_count"] == 0
    _seed_app(ws)
    summary_after = compute_table_summary("apps", workspace_id=ws)
    assert summary_after["row_count"] == 1
    assert summary_after["hash"] != summary_before["hash"]


def test_session_close_blocks_further_send(local_db):
    """A closed session refuses further send_encrypted calls."""
    server, _, _ = _start_tcp_responder("ws-close")
    try:
        client_signer, _ = _signer_factory()
        client_engine = SovereignSyncEngine(
            node_id="client-close",
            workspace_id="ws-close",
            identity_signer=client_signer,
        )
        session = client_engine.establish_session("127.0.0.1", server.port)
        session.close()
        from services.p2p_sync import SessionClosed

        with pytest.raises(SessionClosed):
            session.send_encrypted({"op": "noop"})
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# (3) Rogue rejection — workspace_id divergence
# ---------------------------------------------------------------------------


def test_rogue_workspace_beacon_blocks_and_audits(local_db):
    """Test 3 of the directive (UDP layer) — a beacon advertising a
    different workspace_id is rejected with the
    `local_tampering_blocked` audit row."""
    node = _make_discovery(
        workspace_id="ws-alpha",
        bind_port=_free_udp_port(),
        targets=[],
    )
    rogue = _make_discovery(
        workspace_id="ws-OMEGA-ATTACKER",
        bind_port=_free_udp_port(),
        targets=[],
    )
    rec = node.ingest_beacon(rogue.build_beacon(), source_host="127.0.0.1")
    # The rogue lands in the table as `rejected`, NOT `active`.
    assert rec is not None
    assert rec.status == "rejected"

    # Active peers list (default filter) excludes the rogue.
    assert node.list_peers() == []

    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert rows, "local_tampering_blocked audit row missing"
        details = json.loads(rows[-1].details_json or "{}")
        assert details["peer_workspace"] == "ws-OMEGA-ATTACKER"
        assert details["local_workspace"] == "ws-alpha"


def test_rogue_workspace_handshake_is_torn_down_with_audit(local_db):
    """Test 3 of the directive (TCP layer) — workspace_id mismatch
    during the handshake raises HandshakeFailed AND lands a
    `local_tampering_blocked` audit row. No payload is exchanged."""
    server, _, _ = _start_tcp_responder("ws-alpha")
    try:
        attacker_signer, _ = _signer_factory()
        attacker_engine = SovereignSyncEngine(
            node_id="attacker-node",
            workspace_id="ws-OMEGA-ATTACKER",
            identity_signer=attacker_signer,
        )
        with pytest.raises(HandshakeFailed) as exc_info:
            attacker_engine.establish_session(
                "127.0.0.1",
                server.port,
            )
        assert "workspace" in str(exc_info.value).lower()

        # Give the server thread a moment to write its audit row.
        time.sleep(0.3)
        with get_session() as session:
            rows = (
                session.query(SecurityAuditLog)
                .filter_by(
                    kind="local_tampering_blocked",
                )
                .all()
            )
            assert rows, "handshake-side audit row missing"
            assert any("workspace_id_mismatch" in (r.reason or "") for r in rows)
    finally:
        server.shutdown()
        server.server_close()


def test_rogue_http_compare_returns_403_and_audits(local_db, monkeypatch):
    """A peer asserting a different workspace_id on /api/p2p/sync/compare
    gets 403 + a `local_tampering_blocked` audit row.

    Uses the offline-auth path so Bearer-auth wiring matches every
    other route in the air-gap suite."""
    from api.p2p_routes import router

    # Pin the local workspace; the route's gate compares against this.
    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-alpha")

    # Auth — use the standard offline-auth bootstrap.
    clerk_id = "user_p62_alice"
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    token = make_offline_token(clerk_id)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(
        "/api/p2p/sync/compare",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "workspace_id": "ws-OMEGA-ATTACKER",
            "table": "apps",
        },
    )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["error"] == "workspace_mismatch"
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert rows
        details = json.loads(rows[-1].details_json or "{}")
        assert details["peer_workspace"] == "ws-OMEGA-ATTACKER"
        assert details["local_workspace"] == "ws-alpha"


def test_rogue_http_delta_returns_403_and_audits(local_db, monkeypatch):
    """Symmetric to the compare test for the /sync/delta route."""
    from api.p2p_routes import router

    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-alpha")

    clerk_id = "user_p62_bob"
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    token = make_offline_token(clerk_id)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(
        "/api/p2p/sync/delta",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "workspace_id": "ws-OMEGA-ATTACKER",
            "table": "apps",
            "since_ms": 0,
        },
    )
    assert r.status_code == 403
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert rows


def test_legitimate_http_compare_succeeds_and_returns_summary(local_db, monkeypatch):
    """A peer in the SAME workspace gets a clean 200 with a
    deterministic summary hash."""
    from api.p2p_routes import router

    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-friendly")
    _seed_app("ws-friendly")

    clerk_id = "user_p62_legit"
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    token = make_offline_token(clerk_id)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(
        "/api/p2p/sync/compare",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "workspace_id": "ws-friendly",
            "table": "apps",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace_id"] == "ws-friendly"
    assert body["summary"]["row_count"] == 1
    assert len(body["summary"]["hash"]) == 64  # SHA-256 hex


def test_p2p_disabled_when_local_workspace_not_configured(local_db, monkeypatch):
    """If the operator hasn't set $VOS3_P2P_WORKSPACE_ID and there
    are no apps in the registry, both routes return 503."""
    from api.p2p_routes import router

    monkeypatch.delenv("VOS3_P2P_WORKSPACE_ID", raising=False)

    clerk_id = "user_p62_disabled"
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    token = make_offline_token(clerk_id)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post(
        "/api/p2p/sync/compare",
        headers={"Authorization": f"Bearer {token}"},
        json={"workspace_id": "ws-anything", "table": "apps"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "p2p_disabled"


def test_peers_route_lists_rows_including_rejected(local_db, monkeypatch):
    """The dashboard route returns every row, status-agnostic — the
    operator wants to see attacks too, not just trusted peers."""
    from api.p2p_routes import router

    # Seed a friendly + a rejected peer.
    node = _make_discovery(
        workspace_id="ws-friendly",
        bind_port=_free_udp_port(),
        targets=[],
    )
    friend = _make_discovery(
        workspace_id="ws-friendly",
        bind_port=_free_udp_port(),
        targets=[],
    )
    rogue = _make_discovery(
        workspace_id="ws-OMEGA",
        bind_port=_free_udp_port(),
        targets=[],
    )
    node.ingest_beacon(friend.build_beacon(), source_host="127.0.0.1")
    node.ingest_beacon(rogue.build_beacon(), source_host="127.0.0.1")

    clerk_id = "user_p62_peers"
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    token = make_offline_token(clerk_id)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.get(
        "/api/p2p/peers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    statuses = {p["status"] for p in body["peers"]}
    assert "active" in statuses
    assert "rejected" in statuses


# ---------------------------------------------------------------------------
# (4) P7.2 — Mesh blob fetch (model distribution over encrypted session)
# ---------------------------------------------------------------------------


def test_blob_request_returns_notfound_when_catalog_has_no_match(local_db):
    """A blob.request with an unknown SHA-256 returns blob.notfound
    over the encrypted session — no path leak, no disk scan."""
    from services.p2p_sync import BlobNotFound

    server, _, _ = _start_tcp_responder("ws-blob-miss")
    try:
        client_signer, _ = _signer_factory()
        client_engine = SovereignSyncEngine(
            node_id="blob-miss-client",
            workspace_id="ws-blob-miss",
            identity_signer=client_signer,
        )
        session = client_engine.establish_session("127.0.0.1", server.port)
        try:
            with pytest.raises(BlobNotFound):
                session.request_blob("0" * 64)
        finally:
            session.close()
    finally:
        server.shutdown()
        server.server_close()


def test_blob_request_streams_catalog_file_end_to_end(local_db, tmp_path, monkeypatch):
    """Server side: catalog entry exists + file on disk with matching
    SHA-256. Client side: request_blob returns the exact bytes."""
    import hashlib
    import json as _json

    # Plant a small "model" file whose SHA-256 we'll add to the catalog.
    model_dir = tmp_path / "vos3_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOS3_MODEL_DIR", str(model_dir))

    payload = b"vOS3-test-gguf-content-" + b"x" * 80_000  # > 1 chunk
    sha = hashlib.sha256(payload).hexdigest()
    blob_path = model_dir / "p7-2-test-model.gguf"
    blob_path.write_bytes(payload)

    # Override the curated catalog with a single test entry that
    # pins this exact file's SHA-256. Done via temp file + env override
    # — the model_manager loader caches per-process so we also flush.
    catalog_path = tmp_path / "curated_models.json"
    catalog_path.write_text(
        _json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "id": "p7-2-test-model",
                        "family": "test",
                        "display_name": "P7.2 test blob",
                        "param_count": "0.001B",
                        "quantization": "Q4_K_M",
                        "context": 512,
                        "format": "gguf",
                        "url": "https://example.invalid/p7-2.gguf",
                        "sha256": sha,
                        "size_bytes": len(payload),
                        "tier": "workstation",
                        "kernel_fit": True,
                        "license": "test",
                    }
                ],
            }
        )
    )

    # Repoint the catalog loader at our temp file.
    from services import model_manager as mm

    monkeypatch.setattr(mm, "CURATED_CATALOG_PATH", catalog_path)
    mm._reset_hw_cache_for_tests()

    # Spin a peer that holds the file.
    server, _, _ = _start_tcp_responder("ws-blob-hit")
    try:
        client_signer, _ = _signer_factory()
        client_engine = SovereignSyncEngine(
            node_id="blob-hit-client",
            workspace_id="ws-blob-hit",
            identity_signer=client_signer,
        )
        session = client_engine.establish_session("127.0.0.1", server.port)
        try:
            received = session.request_blob(sha)
        finally:
            session.close()
    finally:
        server.shutdown()
        server.server_close()

    assert received == payload
    # SHA-256 invariant on the receiver side.
    assert hashlib.sha256(received).hexdigest() == sha


def test_mesh_fetch_falls_back_to_https_when_no_active_peers(
    local_db, tmp_path, monkeypatch
):
    """_try_mesh_blob_fetch returns None when DiscoveredPeers is empty
    — the caller falls back to HTTPS via _download_with_sha256."""
    import asyncio

    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-empty-mesh")
    monkeypatch.setenv("VOS3_MODEL_DIR", str(tmp_path / "models"))

    from services.model_manager import _try_mesh_blob_fetch

    result = asyncio.run(
        _try_mesh_blob_fetch(
            sha256="a" * 64,
            dest=tmp_path / "models" / "x.gguf",
        )
    )
    assert result is None


def test_mesh_fetch_rejects_peer_returning_wrong_sha(local_db, tmp_path, monkeypatch):
    """A peer that holds a file with a NON-matching SHA-256 is treated
    as malicious — the bytes are discarded AND a malicious_peer_attempt
    audit row is written."""
    import asyncio
    import hashlib
    import json as _json

    # Catalog claims sha-A; planted file on the peer hashes to sha-B.
    sha_claimed = "a" * 64
    real_bytes = b"this-is-not-the-promised-blob"
    hashlib.sha256(real_bytes).hexdigest()

    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOS3_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("VOS3_P2P_WORKSPACE_ID", "ws-rogue-blob")
    # The server-side _locate_blob_by_sha256 indexes by the CATALOG'S
    # sha, then verifies the file. We plant the file at the
    # catalog-named ID — server returns it; client SHA check fails.
    catalog_path = tmp_path / "curated_models.json"
    catalog_path.write_text(
        _json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "id": "rogue-test",
                        "family": "test",
                        "tier": "workstation",
                        "sha256": sha_claimed,
                        "url": "https://example.invalid/x.gguf",
                        "size_bytes": len(real_bytes),
                        "format": "gguf",
                    }
                ],
            }
        )
    )
    from services import model_manager as mm

    monkeypatch.setattr(mm, "CURATED_CATALOG_PATH", catalog_path)
    mm._reset_hw_cache_for_tests()
    (model_dir / "rogue-test.gguf").write_bytes(real_bytes)

    # Spin a peer server. The session handshake auto-binds to the
    # workspace from VOS3_P2P_WORKSPACE_ID (passed to the engine).
    server, _, _ = _start_tcp_responder("ws-rogue-blob")
    try:
        # Register a discovered-peer row pointing at the loopback server.
        from core.database.sqlite_setup import (
            DiscoveredPeer,
            get_session as _gs,
            init_db as _idb,
        )

        _idb()
        import uuid as _u
        import time as _t

        with _gs() as s:
            s.add(
                DiscoveredPeer(
                    id=str(_u.uuid4()),
                    nodeId="rogue-peer-node",
                    workspaceId="ws-rogue-blob",
                    host="127.0.0.1",
                    syncPort=server.port,
                    publicKeyHex="00" * 32,
                    status="active",
                    verified=True,
                    firstSeenAt=int(_t.time() * 1000),
                    lastSeenAt=int(_t.time() * 1000),
                )
            )
            s.commit()

        result = asyncio.run(
            mm._try_mesh_blob_fetch(
                sha256=sha_claimed,
                dest=model_dir / "out.gguf",
            )
        )
    finally:
        server.shutdown()
        server.server_close()

    # All peers failed → None returned, caller falls back to HTTPS.
    assert result is None

    # And the malicious-peer audit was recorded.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="malicious_peer_attempt",
            )
            .all()
        )
        assert any("mesh_blob_sha256_mismatch" in (r.reason or "") for r in rows)
