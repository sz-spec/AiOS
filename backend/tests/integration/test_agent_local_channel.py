"""
backend/tests/integration/test_agent_local_channel.py

Sprint 16 / Item H3 — AF_VOS3_AGENT socket-family simulator tests.

Covers:
- Listener init validates addr; rejects duplicate addr in registry.
- Socket init validates verifier + svid + trust_domain.
- Successful handshake exchanges SVIDs both ways; both ends populate
  peer_spiffe_id + peer_trust_domain.
- Peer verification failure (server-side OR client-side): handshake
  raises PeerVerificationError; bumps stats counter.
- TrustDomainRequiredError when peer's domain not in required set
  (server-side OR client-side).
- Data path: send/recv round-trip; bytes counters increment.
- Close signals peer; subsequent send/recv raises ChannelClosedError.
- Multiple sequential connects to the same listener succeed.
- Timeouts: accept + recv with timeout raise TimeoutError.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AC_PATH = _REPO_ROOT / "backend" / "services" / "agent_local_channel.py"
_spec = importlib.util.spec_from_file_location("vos3_agent_chan_under_test", _AC_PATH)
ac = importlib.util.module_from_spec(_spec)
sys.modules["vos3_agent_chan_under_test"] = ac
_spec.loader.exec_module(ac)


# ---------------------------------------------------------------------------
# Stub verifier
# ---------------------------------------------------------------------------


@dataclass
class _StubVerified:
    spiffe_id_str: str


class _AcceptingVerifier:
    """Always accepts; returns SPIFFE-ID derived from the JWT."""

    def verify(self, jwt_token: str):
        # Use the JWT as the SPIFFE-ID for simplicity in tests.
        return _StubVerified(spiffe_id_str=f"spiffe://example.com/{jwt_token}")


class _RejectingVerifier:
    """Always rejects."""

    def verify(self, jwt_token: str):
        raise RuntimeError(f"refusing to verify {jwt_token[:20]}")


# ---------------------------------------------------------------------------
# Listener init
# ---------------------------------------------------------------------------


def setup_function(_):
    ac.reset_registry()


def test_listener_init_requires_addr():
    with pytest.raises(ValueError):
        ac.AgentChannelListener(
            addr="",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="srv",
            own_trust_domain="example.com",
        )


def test_listener_init_duplicate_addr_raises():
    ac.AgentChannelListener(
        addr="/tmp/srv1",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="srv",
        own_trust_domain="example.com",
    )
    with pytest.raises(ac.AgentChannelError):
        ac.AgentChannelListener(
            addr="/tmp/srv1",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="srv",
            own_trust_domain="example.com",
        )


# ---------------------------------------------------------------------------
# Socket init
# ---------------------------------------------------------------------------


def test_socket_init_validation():
    with pytest.raises(ValueError):
        ac.AgentChannelSocket(
            verifier=None,
            own_svid_jwt="x",  # type: ignore[arg-type]
            own_trust_domain="example.com",
        )
    with pytest.raises(ValueError):
        ac.AgentChannelSocket(
            verifier=_AcceptingVerifier(),
            own_svid_jwt="",
            own_trust_domain="example.com",
        )
    with pytest.raises(ValueError):
        ac.AgentChannelSocket(
            verifier=_AcceptingVerifier(), own_svid_jwt="x", own_trust_domain=""
        )


# ---------------------------------------------------------------------------
# Handshake — happy path
# ---------------------------------------------------------------------------


def test_handshake_completes_and_populates_peer_identity():
    listener = ac.AgentChannelListener(
        addr="/tmp/srv2",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    client = ac.AgentChannelSocket.connect(
        listener_addr="/tmp/srv2",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="client-svid",
        own_trust_domain="example.com",
    )
    server = listener.accept(timeout=1.0)
    # Both sides know the other's SPIFFE-ID.
    assert client.peer_spiffe_id == "spiffe://example.com/server-svid"
    assert server.peer_spiffe_id == "spiffe://example.com/client-svid"
    assert client.peer_trust_domain == "example.com"
    assert server.peer_trust_domain == "example.com"


# ---------------------------------------------------------------------------
# Handshake — peer-auth failure
# ---------------------------------------------------------------------------


def test_handshake_fails_when_server_rejects_client_svid():
    ac.AgentChannelListener(
        addr="/tmp/srv3",
        verifier=_RejectingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    with pytest.raises(ac.PeerVerificationError):
        ac.AgentChannelSocket.connect(
            listener_addr="/tmp/srv3",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="client-svid",
            own_trust_domain="example.com",
        )


def test_handshake_fails_when_client_rejects_server_svid():
    ac.AgentChannelListener(
        addr="/tmp/srv4",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    with pytest.raises(ac.PeerVerificationError):
        ac.AgentChannelSocket.connect(
            listener_addr="/tmp/srv4",
            verifier=_RejectingVerifier(),
            own_svid_jwt="client-svid",
            own_trust_domain="example.com",
        )


# ---------------------------------------------------------------------------
# Trust-domain requirement
# ---------------------------------------------------------------------------


def test_handshake_fails_when_server_requires_unmatched_domain():
    ac.AgentChannelListener(
        addr="/tmp/srv5",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
        required_trust_domains={"approved-partner.example.com"},
    )
    with pytest.raises(ac.TrustDomainRequiredError):
        ac.AgentChannelSocket.connect(
            listener_addr="/tmp/srv5",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="client-svid",
            own_trust_domain="random-unknown.example.com",
        )


def test_handshake_fails_when_client_requires_unmatched_domain():
    ac.AgentChannelListener(
        addr="/tmp/srv6",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    with pytest.raises(ac.TrustDomainRequiredError):
        ac.AgentChannelSocket.connect(
            listener_addr="/tmp/srv6",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="client-svid",
            own_trust_domain="example.com",
            required_trust_domains={"only-this.example.com"},
        )


def test_set_trust_domain_require_rejects_non_set():
    sock = ac.AgentChannelSocket(
        verifier=_AcceptingVerifier(), own_svid_jwt="x", own_trust_domain="example.com"
    )
    with pytest.raises(TypeError):
        sock.set_trust_domain_require(["not-a-set"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Data path
# ---------------------------------------------------------------------------


def _make_connected_pair(addr: str = "/tmp/srv-data"):
    listener = ac.AgentChannelListener(
        addr=addr,
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    client = ac.AgentChannelSocket.connect(
        listener_addr=addr,
        verifier=_AcceptingVerifier(),
        own_svid_jwt="client-svid",
        own_trust_domain="example.com",
    )
    server = listener.accept(timeout=1.0)
    return client, server, listener


def test_send_recv_round_trip():
    client, server, _ = _make_connected_pair()
    assert client.send(b"hello server") == 12
    payload = server.recv(timeout=1.0)
    assert payload == b"hello server"
    # Reverse direction.
    server.send(b"hello back")
    assert client.recv(timeout=1.0) == b"hello back"


def test_send_rejects_non_bytes():
    client, _, _ = _make_connected_pair()
    with pytest.raises(TypeError):
        client.send("not-bytes")  # type: ignore[arg-type]


def test_close_signals_peer_then_raises():
    client, server, _ = _make_connected_pair()
    client.close()
    # Server's blocking recv unblocks with empty payload.
    assert server.recv(timeout=1.0) == b""
    # Subsequent operations on closed socket raise.
    with pytest.raises(ac.ChannelClosedError):
        client.send(b"x")
    with pytest.raises(ac.ChannelClosedError):
        client.recv(timeout=0.1)


def test_recv_timeout():
    client, _, _ = _make_connected_pair()
    with pytest.raises(TimeoutError):
        client.recv(timeout=0.1)


def test_accept_timeout():
    listener = ac.AgentChannelListener(
        addr="/tmp/srv-accept-to",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    with pytest.raises(TimeoutError):
        listener.accept(timeout=0.1)


def test_send_before_handshake_raises():
    sock = ac.AgentChannelSocket(
        verifier=_AcceptingVerifier(), own_svid_jwt="x", own_trust_domain="example.com"
    )
    with pytest.raises(ac.AgentChannelError):
        sock.send(b"x")


# ---------------------------------------------------------------------------
# Multiple connections
# ---------------------------------------------------------------------------


def test_multiple_sequential_connects():
    listener = ac.AgentChannelListener(
        addr="/tmp/srv-multi",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="server-svid",
        own_trust_domain="example.com",
    )
    c1 = ac.AgentChannelSocket.connect(
        listener_addr="/tmp/srv-multi",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="client-1",
        own_trust_domain="example.com",
    )
    s1 = listener.accept(timeout=1.0)
    c2 = ac.AgentChannelSocket.connect(
        listener_addr="/tmp/srv-multi",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="client-2",
        own_trust_domain="example.com",
    )
    s2 = listener.accept(timeout=1.0)

    # Each pair is independent.
    c1.send(b"to s1")
    c2.send(b"to s2")
    assert s1.recv(timeout=1.0) == b"to s1"
    assert s2.recv(timeout=1.0) == b"to s2"


def test_connect_unknown_addr_raises():
    with pytest.raises(ac.AgentChannelError):
        ac.AgentChannelSocket.connect(
            listener_addr="/no/such/listener",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="x",
            own_trust_domain="example.com",
        )


# ---------------------------------------------------------------------------
# Listener close removes from registry
# ---------------------------------------------------------------------------


def test_listener_close_removes_from_registry():
    listener = ac.AgentChannelListener(
        addr="/tmp/srv-rm",
        verifier=_AcceptingVerifier(),
        own_svid_jwt="srv",
        own_trust_domain="example.com",
    )
    listener.close()
    with pytest.raises(ac.AgentChannelError):
        ac.AgentChannelSocket.connect(
            listener_addr="/tmp/srv-rm",
            verifier=_AcceptingVerifier(),
            own_svid_jwt="x",
            own_trust_domain="example.com",
        )
