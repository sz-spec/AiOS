"""
Stage 1 · Atomic unit isolation for services/p2p_sync.py.

Covers — purpose | guards file:line:
  _send_frame/_recv_frame length-prefix roundtrip      | p2p_sync.py:124-155
  _send_frame rejects oversized payload                | p2p_sync.py:127-128
  _recv_frame rejects bad length prefix                | p2p_sync.py:146
  _generate_ephemeral_x25519 yields distinct keys      | p2p_sync.py:163-172
  _derive_shared_secret symmetry                       | p2p_sync.py:175-180
  _derive_fernet_key deterministic + transcript-bound  | p2p_sync.py:183-199
  SyncSession encrypt/decrypt roundtrip                | p2p_sync.py:245-271
  SyncSession.close idempotent                         | p2p_sync.py:374-385
  SyncSession send/recv after close raises             | p2p_sync.py:246, 253
  SovereignSyncEngine validates workspace_id           | p2p_sync.py:408-411
  HandshakeFailed/SessionClosed/BlobNotFound shapes    | p2p_sync.py:98-117
  compute_table_summary scoped to workspace            | p2p_sync.py:860-897
  _locate_blob_by_sha256 returns None on no catalog hit| p2p_sync.py:1010-1039
"""

from __future__ import annotations

import socket
import struct

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def sync_env(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield


# ---------------------------------------------------------------------------
# Frame helpers — pure I/O
# ---------------------------------------------------------------------------


def test_send_recv_frame_roundtrip(sync_env):
    """_send_frame + _recv_frame across a connected socket pair."""
    from services.p2p_sync import _send_frame, _recv_frame

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        _send_frame(a, {"hello": "world", "n": 42})
        env = _recv_frame(b)
        assert env == {"hello": "world", "n": 42}
    finally:
        a.close()
        b.close()


def test_send_frame_rejects_oversized(sync_env):
    """A frame body larger than FRAME_MAX_BYTES raises ValueError."""
    from services.p2p_sync import _send_frame, FRAME_MAX_BYTES

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    huge = {"x": "y" * (FRAME_MAX_BYTES + 100)}
    try:
        with pytest.raises(ValueError):
            _send_frame(a, huge)
    finally:
        a.close()
        b.close()


def test_recv_frame_rejects_zero_length(sync_env):
    from services.p2p_sync import _recv_frame, HandshakeFailed

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        a.sendall(struct.pack(">I", 0))
        with pytest.raises(HandshakeFailed):
            _recv_frame(b)
    finally:
        a.close()
        b.close()


def test_recv_frame_rejects_oversized_length(sync_env):
    from services.p2p_sync import _recv_frame, HandshakeFailed, FRAME_MAX_BYTES

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        a.sendall(struct.pack(">I", FRAME_MAX_BYTES + 1))
        with pytest.raises(HandshakeFailed):
            _recv_frame(b)
    finally:
        a.close()
        b.close()


def test_recv_frame_rejects_non_dict_body(sync_env):
    """A length-prefixed JSON array is not a frame envelope."""
    from services.p2p_sync import _recv_frame, HandshakeFailed

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    body = b'["hello"]'
    try:
        a.sendall(struct.pack(">I", len(body)) + body)
        with pytest.raises(HandshakeFailed):
            _recv_frame(b)
    finally:
        a.close()
        b.close()


def test_recv_frame_rejects_bad_json(sync_env):
    from services.p2p_sync import _recv_frame, HandshakeFailed

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    body = b"\xff\xfe-not-json"
    try:
        a.sendall(struct.pack(">I", len(body)) + body)
        with pytest.raises(HandshakeFailed):
            _recv_frame(b)
    finally:
        a.close()
        b.close()


def test_recv_frame_peer_closed_raises_handshake_failed(sync_env):
    from services.p2p_sync import _recv_frame, HandshakeFailed

    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    a.close()  # peer closes BEFORE sending anything
    try:
        with pytest.raises(HandshakeFailed):
            _recv_frame(b)
    finally:
        b.close()


# ---------------------------------------------------------------------------
# X25519 + HKDF primitives
# ---------------------------------------------------------------------------


def test_generate_ephemeral_distinct():
    from services.p2p_sync import _generate_ephemeral_x25519

    p1, pub1 = _generate_ephemeral_x25519()
    p2, pub2 = _generate_ephemeral_x25519()
    assert pub1 != pub2
    assert len(pub1) == 64  # 32 raw bytes hex
    assert len(pub2) == 64


def test_derive_shared_secret_symmetric():
    """Alice's priv × Bob's pub == Bob's priv × Alice's pub."""
    from services.p2p_sync import _generate_ephemeral_x25519, _derive_shared_secret

    pa, pub_a = _generate_ephemeral_x25519()
    pb, pub_b = _generate_ephemeral_x25519()
    sa = _derive_shared_secret(pa, pub_b)
    sb = _derive_shared_secret(pb, pub_a)
    assert sa == sb


def test_derive_fernet_key_deterministic():
    from services.p2p_sync import _derive_fernet_key

    k1 = _derive_fernet_key(b"shared-x", transcript=b"a|b")
    k2 = _derive_fernet_key(b"shared-x", transcript=b"a|b")
    assert k1 == k2
    # Fernet key is 44 chars (32-byte → urlsafe-b64).
    assert len(k1) == 44


def test_derive_fernet_key_transcript_isolation():
    """Same shared secret, different transcripts → different keys."""
    from services.p2p_sync import _derive_fernet_key

    k1 = _derive_fernet_key(b"shared", transcript=b"a|b")
    k2 = _derive_fernet_key(b"shared", transcript=b"b|a")
    assert k1 != k2


def test_derive_fernet_key_secret_isolation():
    from services.p2p_sync import _derive_fernet_key

    k1 = _derive_fernet_key(b"alpha", transcript=b"t")
    k2 = _derive_fernet_key(b"beta", transcript=b"t")
    assert k1 != k2


# ---------------------------------------------------------------------------
# SyncSession (encryption layer in isolation — no handshake required)
# ---------------------------------------------------------------------------


def _make_session_pair(sync_env):
    """Build two paired SyncSessions over a socketpair with a SHARED Fernet key.

    Skips the handshake entirely — we're unit-testing the message layer."""
    from services.p2p_sync import SyncSession, SessionMetadata
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    a_sock, b_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    meta = SessionMetadata(
        local_node_id="A",
        local_workspace_id="w",
        peer_node_id="B",
        peer_workspace_id="w",
        peer_identity_pk="pk",
        started_at_ms=0,
    )
    a = SyncSession(sock=a_sock, fernet_key=key, metadata=meta)
    b = SyncSession(sock=b_sock, fernet_key=key, metadata=meta)
    return a, b


def test_session_send_recv_roundtrip(sync_env):
    a, b = _make_session_pair(sync_env)
    try:
        a.send_encrypted({"op": "ping", "n": 1})
        got = b.recv_encrypted(timeout_s=2.0)
        assert got == {"op": "ping", "n": 1}
    finally:
        a.close()
        b.close()


def test_session_close_idempotent(sync_env):
    a, b = _make_session_pair(sync_env)
    a.close()
    a.close()  # second close must not raise
    assert a.closed is True
    b.close()


def test_session_send_after_close_raises(sync_env):
    from services.p2p_sync import SessionClosed

    a, b = _make_session_pair(sync_env)
    a.close()
    with pytest.raises(SessionClosed):
        a.send_encrypted({"op": "x"})
    b.close()


def test_session_recv_after_close_raises(sync_env):
    from services.p2p_sync import SessionClosed

    a, b = _make_session_pair(sync_env)
    a.close()
    with pytest.raises(SessionClosed):
        a.recv_encrypted(timeout_s=0.1)
    b.close()


def test_session_corrupt_ciphertext_raises(sync_env):
    """If the inbound `enc` token has been tampered with, Fernet rejects it."""
    from services.p2p_sync import _send_frame

    a, b = _make_session_pair(sync_env)
    try:
        # Hand-craft a frame with a garbage Fernet token.
        _send_frame(a._sock, {"enc": "gAAAAA-this-is-not-a-real-token"})
        with pytest.raises(Exception):  # InvalidToken from cryptography
            b.recv_encrypted(timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# SovereignSyncEngine constructor
# ---------------------------------------------------------------------------


def test_engine_rejects_empty_workspace():
    from services.p2p_sync import SovereignSyncEngine

    with pytest.raises(ValueError):
        SovereignSyncEngine(node_id="n", workspace_id="")


# ---------------------------------------------------------------------------
# Exception shapes
# ---------------------------------------------------------------------------


def test_handshake_failed_is_runtime_error():
    from services.p2p_sync import HandshakeFailed

    exc = HandshakeFailed("oops")
    assert isinstance(exc, RuntimeError)
    assert "oops" in str(exc)


def test_session_closed_is_runtime_error():
    from services.p2p_sync import SessionClosed

    assert isinstance(SessionClosed(), RuntimeError)


def test_blob_not_found_is_runtime_error():
    from services.p2p_sync import BlobNotFound

    assert isinstance(BlobNotFound(), RuntimeError)


# ---------------------------------------------------------------------------
# compute_table_summary / delta — workspace scoping
# ---------------------------------------------------------------------------


def test_compute_table_summary_unknown_table(sync_env):
    from services.p2p_sync import compute_table_summary

    out = compute_table_summary("not_a_table", workspace_id="w")
    assert "error" in out
    assert out["error"].startswith("unknown_table:")


def test_compute_table_summary_empty_workspace(sync_env):
    from services.p2p_sync import compute_table_summary

    out = compute_table_summary("projects", workspace_id="nobody-here")
    assert out["row_count"] == 0
    assert out["latest_updated_at"] == 0
    # Empty input → SHA-256 of "".
    import hashlib

    assert out["hash"] == hashlib.sha256(b"").hexdigest()


def test_compute_table_delta_unknown_table(sync_env):
    from services.p2p_sync import compute_table_delta

    assert compute_table_delta("nope", workspace_id="w") == []


# ---------------------------------------------------------------------------
# _locate_blob_by_sha256 (P7.2)
# ---------------------------------------------------------------------------


def test_locate_blob_returns_none_when_catalog_has_no_match(sync_env):
    """An arbitrary SHA-256 has no curated catalog entry → returns None
    without leaking any path info."""
    from services.p2p_sync import _locate_blob_by_sha256

    fake_sha = "00" * 32
    assert _locate_blob_by_sha256(fake_sha) is None
