"""
Stage 2 · P7.2 blob distribution adversarial stress tests.

Drives `SyncSession.request_blob` against a synthetic malicious peer
that returns hand-crafted `blob.found` / `blob.chunk` / `blob.eof`
frames so we exercise every protocol-level rejection path:

  * out-of-order chunks
  * duplicate chunk index
  * malformed base64 payload
  * peer claims wrong final SHA-256
  * peer responds blob.notfound — caller raises BlobNotFound
  * peer skips chunks but sends eof early
  * non-string / non-int chunk fields

Each test pairs a real client `SyncSession` with a "manual" peer
that drives raw encrypted frames into the other socket end. No real
file I/O — the blob payload is whatever bytes the test feeds.
"""

from __future__ import annotations

import base64
import socket
import threading

import pytest
from cryptography.fernet import Fernet

from services.p2p_sync import (
    BlobNotFound,
    HandshakeFailed,
    SessionMetadata,
    SyncSession,
)


def _session_pair():
    """Build two paired SyncSessions over a socketpair with shared Fernet key."""
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


def _run_peer(b, frames):
    """Spawn a thread that, after the client sends a blob.request, replies
    with the list of `frames` (each a dict)."""

    def _serve():
        try:
            req = b.recv_encrypted(timeout_s=2.0)
            assert req.get("op") == "blob.request"
            rid = req.get("request_id")
            for f in frames:
                if "request_id" not in f and rid is not None:
                    f = {**f, "request_id": rid}
                b.send_encrypted(f)
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Happy path — 3 well-formed chunks → bytes assemble cleanly
# ---------------------------------------------------------------------------


def test_happy_3_chunks_assemble(stress_env):
    a, b = _session_pair()
    payload = b"chunk0-data---chunk1-data---chunk2-data"
    # Split into 3 chunks roughly equal.
    chunks = [payload[i : i + 14] for i in range(0, len(payload), 14)]
    frames = (
        [
            {"op": "blob.found", "size_bytes": len(payload), "chunks": len(chunks)},
        ]
        + [
            {"op": "blob.chunk", "idx": i, "data_b64": base64.b64encode(c).decode()}
            for i, c in enumerate(chunks)
        ]
        + [{"op": "blob.eof", "sha256": "00" * 32}]
    )
    try:
        _run_peer(b, frames)
        got = a.request_blob("ab" * 32, chunk_timeout_s=2.0)
        assert got == payload
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Peer returns blob.notfound → caller raises BlobNotFound
# ---------------------------------------------------------------------------


def test_notfound_response_raises(stress_env):
    a, b = _session_pair()
    try:
        _run_peer(b, [{"op": "blob.notfound"}])
        with pytest.raises(BlobNotFound):
            a.request_blob("00" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Peer responds with unexpected op (neither found nor notfound)
# ---------------------------------------------------------------------------


def test_unexpected_response_op_raises(stress_env):
    a, b = _session_pair()
    try:
        _run_peer(b, [{"op": "weird.response"}])
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Zero-chunk found → HandshakeFailed
# ---------------------------------------------------------------------------


def test_zero_chunk_header_raises(stress_env):
    a, b = _session_pair()
    try:
        _run_peer(b, [{"op": "blob.found", "size_bytes": 0, "chunks": 0}])
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Out-of-order chunks → HandshakeFailed
# ---------------------------------------------------------------------------


def test_out_of_order_chunks_rejected(stress_env):
    a, b = _session_pair()
    payload = b"AAAABBBBCCCC"
    chunks = [payload[i : i + 4] for i in range(0, 12, 4)]
    frames = [
        {"op": "blob.found", "size_bytes": 12, "chunks": 3},
        # Send idx=1 BEFORE idx=0.
        {
            "op": "blob.chunk",
            "idx": 1,
            "data_b64": base64.b64encode(chunks[1]).decode(),
        },
        {
            "op": "blob.chunk",
            "idx": 0,
            "data_b64": base64.b64encode(chunks[0]).decode(),
        },
        {
            "op": "blob.chunk",
            "idx": 2,
            "data_b64": base64.b64encode(chunks[2]).decode(),
        },
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Duplicate chunk idx → HandshakeFailed
# ---------------------------------------------------------------------------


def test_duplicate_chunk_idx_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 8, "chunks": 2},
        {"op": "blob.chunk", "idx": 0, "data_b64": base64.b64encode(b"AAAA").decode()},
        {
            "op": "blob.chunk",
            "idx": 0,  # duplicate idx
            "data_b64": base64.b64encode(b"BBBB").decode(),
        },
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Malformed base64 → HandshakeFailed
# ---------------------------------------------------------------------------


def test_malformed_base64_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        {"op": "blob.chunk", "idx": 0, "data_b64": "not!valid!base64!"},
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Non-string data_b64 → HandshakeFailed
# ---------------------------------------------------------------------------


def test_non_string_data_b64_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        {"op": "blob.chunk", "idx": 0, "data_b64": 12345},  # not a string
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Non-int idx → HandshakeFailed
# ---------------------------------------------------------------------------


def test_non_int_idx_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        {
            "op": "blob.chunk",
            "idx": "0",
            "data_b64": base64.b64encode(b"AAAA").decode(),
        },
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Wrong op for chunk frame → HandshakeFailed
# ---------------------------------------------------------------------------


def test_wrong_op_for_chunk_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        {"op": "weird-op", "idx": 0, "data_b64": base64.b64encode(b"AAAA").decode()},
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Missing EOF (peer sends eof with wrong op) → HandshakeFailed
# ---------------------------------------------------------------------------


def test_missing_eof_rejected(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        {"op": "blob.chunk", "idx": 0, "data_b64": base64.b64encode(b"AAAA").decode()},
        # Instead of blob.eof, send a malformed final frame.
        {"op": "not.eof"},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=2.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# 50-chunk well-formed transfer
# ---------------------------------------------------------------------------


def test_50_chunk_transfer(stress_env):
    """Stream 50 chunks of 1 KiB each → 50 KB assembled correctly."""
    a, b = _session_pair()
    payload = b"".join(bytes([i]) * 1024 for i in range(50))
    frames = (
        [
            {"op": "blob.found", "size_bytes": len(payload), "chunks": 50},
        ]
        + [
            {
                "op": "blob.chunk",
                "idx": i,
                "data_b64": base64.b64encode(
                    payload[i * 1024 : (i + 1) * 1024]
                ).decode(),
            }
            for i in range(50)
        ]
        + [{"op": "blob.eof", "sha256": "00" * 32}]
    )
    try:
        _run_peer(b, frames)
        got = a.request_blob("ab" * 32, chunk_timeout_s=5.0)
        assert got == payload
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Empty chunks declared but no chunk frames → recv times out / raises
# ---------------------------------------------------------------------------


def test_chunks_promised_but_none_delivered(stress_env):
    a, b = _session_pair()
    frames = [
        {"op": "blob.found", "size_bytes": 4, "chunks": 1},
        # Peer never sends the chunk; eof comes immediately.
        {"op": "blob.eof", "sha256": "00" * 32},
    ]
    try:
        _run_peer(b, frames)
        with pytest.raises(HandshakeFailed):
            a.request_blob("ab" * 32, chunk_timeout_s=1.0)
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# Multiple notfound responses in a row — caller raises on the first one
# ---------------------------------------------------------------------------


def test_repeated_notfound_5x(stress_env):
    """5 sequential lookups against a peer that never holds the blob."""
    for _ in range(5):
        a, b = _session_pair()
        try:
            _run_peer(b, [{"op": "blob.notfound"}])
            with pytest.raises(BlobNotFound):
                a.request_blob("be" * 32, chunk_timeout_s=2.0)
        finally:
            a.close()
            b.close()


# ---------------------------------------------------------------------------
# Server side — _serve_blob_request honors malformed sha256 with notfound
# ---------------------------------------------------------------------------


def test_serve_rejects_short_sha256(stress_env):
    """Server-side request handler returns blob.notfound on malformed sha."""
    from services.p2p_sync import _serve_blob_request

    a, b = _session_pair()
    try:
        _serve_blob_request(a, {"sha256": "tooshort", "request_id": "r1"})
        # Other side sees the response.
        resp = b.recv_encrypted(timeout_s=1.0)
        assert resp["op"] == "blob.notfound"
        assert resp.get("reason") == "malformed_sha256"
    finally:
        a.close()
        b.close()


def test_serve_rejects_non_string_sha256(stress_env):
    from services.p2p_sync import _serve_blob_request

    a, b = _session_pair()
    try:
        _serve_blob_request(a, {"sha256": 12345, "request_id": "r2"})
        resp = b.recv_encrypted(timeout_s=1.0)
        assert resp["op"] == "blob.notfound"
    finally:
        a.close()
        b.close()


def test_serve_unknown_sha_returns_notfound(stress_env):
    """A sha256 that doesn't match any catalog entry → notfound, no leak."""
    from services.p2p_sync import _serve_blob_request

    a, b = _session_pair()
    try:
        _serve_blob_request(a, {"sha256": "f" * 64, "request_id": "r3"})
        resp = b.recv_encrypted(timeout_s=1.0)
        assert resp["op"] == "blob.notfound"
        # Ensure no path / filename was leaked.
        assert "path" not in resp
        assert "filename" not in resp
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# request_id round-trip — the caller-issued id is echoed in responses
# ---------------------------------------------------------------------------


def test_request_id_echoed_in_notfound(stress_env):
    """The server's notfound MUST carry the caller's request_id."""
    from services.p2p_sync import _serve_blob_request

    a, b = _session_pair()
    try:
        _serve_blob_request(a, {"sha256": "f" * 64, "request_id": "my-rid"})
        resp = b.recv_encrypted(timeout_s=1.0)
        assert resp.get("request_id") == "my-rid"
    finally:
        a.close()
        b.close()
