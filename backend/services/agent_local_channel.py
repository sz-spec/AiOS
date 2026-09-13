"""
backend/services/agent_local_channel.py
=========================================

Sprint 16 / Item H3 — AF_VOS3_AGENT socket-family simulator.

What this is
------------

From the 80-problem agent-era catalog, H3:
  "Agent-to-agent comm: no 'trusted local channel' OS primitive —
   sibling agents on same host should be able to verify each other;
   only userspace mTLS today."

The kernel-side header in kernel/include/vos/af_vos3_agent.h declares
the new socket family. The kernel module that registers it (net/...c)
is a Wave 3 follow-up. This module ships the **Python simulator** of
the connect-time SPIFFE-SVID exchange + peer-identity verification so
backend services can be coded against the API today.

Public surface
--------------

  AgentChannelSocket — connect-time SPIFFE handshake; bidirectional
    send/recv; SO_VOS3_PEER_SPIFFE / SO_VOS3_TRUST_DOMAIN_REQUIRE
    helpers.

  AgentChannelListener — accept() returns a fully-handshaked socket.

  PeerVerificationError, TrustDomainRequiredError — handshake failures.

Handshake protocol
------------------

  client                              server
  ─────                              ──────
  ── SVID_OFFER(client_svid_jwt) ──>
                                      validate via SPIFFE verifier
                                      check against required trust domains
  <── SVID_OFFER(server_svid_jwt) ──
  validate via SPIFFE verifier
  check against required trust domains
  ── HANDSHAKE_ACK ──>
  <── HANDSHAKE_ACK ──
  (channel ready for application data)

If either side rejects the other's SVID, connect()/accept() raises;
no application data is exchanged. After successful handshake,
.peer_spiffe_id is populated on both ends.

Honest scope ceiling
--------------------

  - The simulator runs entirely in-process — there is no actual socket
    pair. Production deployment uses the kernel-registered AF_VOS3_AGENT
    family + AF_UNIX backing. The API surface (connect, listen, accept,
    send, recv, getsockopt) is shaped to match what the kernel module
    will provide.
  - The handshake exchanges SVIDs in plaintext; transport encryption
    is the kernel-side concern (AF_UNIX is already local-only +
    process-isolated by namespace; we don't re-encrypt).
  - SVID rotation during a long-lived connection: out of scope for H3.
    F5 vos3_cred_rotate will invalidate the fd if the underlying
    credential expires.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentChannelError(Exception):
    pass


class PeerVerificationError(AgentChannelError):
    """Peer's SVID did not verify against our trust bundle."""

    pass


class TrustDomainRequiredError(AgentChannelError):
    """Peer's trust domain is not in our setsockopt-required set."""

    pass


class ChannelClosedError(AgentChannelError):
    pass


# ---------------------------------------------------------------------------
# Verifier protocol (structural — F1's SPIFFEWITVerifier + F4's federation
# verifier both satisfy this)
# ---------------------------------------------------------------------------


class _VerifierProtocol:
    def verify(self, jwt_token: str) -> Any: ...


# ---------------------------------------------------------------------------
# Verified-peer + handshake frames (in-process simulator)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SVIDOffer:
    svid_jwt: str
    presented_trust_domain: str


@dataclass(frozen=True)
class _HandshakeAck:
    pass


@dataclass
class AgentChannelStats:
    handshakes_completed: int = 0
    handshakes_rejected_peer_auth: int = 0
    handshakes_rejected_trust_required: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0


# Shared registry of listening sockets so a "connect" can find the
# corresponding "listener" by name.  In-process only.
_listener_registry: dict[str, "AgentChannelListener"] = {}
_registry_lock = threading.Lock()


# ---------------------------------------------------------------------------
# AgentChannelSocket — both ends of a connected channel
# ---------------------------------------------------------------------------


class AgentChannelSocket:
    """A connected socket between two agents.

    Instances are produced by AgentChannelListener.accept() (server side)
    or by AgentChannelSocket.connect(addr, ...) (client side).
    """

    def __init__(
        self,
        *,
        verifier: _VerifierProtocol,
        own_svid_jwt: str,
        own_trust_domain: str,
        stats: Optional[AgentChannelStats] = None,
    ):
        if verifier is None:
            raise ValueError("verifier required")
        if not own_svid_jwt:
            raise ValueError("own_svid_jwt required")
        if not own_trust_domain:
            raise ValueError("own_trust_domain required")
        self._verifier = verifier
        self._own_svid_jwt = own_svid_jwt
        self._own_trust_domain = own_trust_domain
        self.peer_spiffe_id: Optional[str] = None
        self.peer_trust_domain: Optional[str] = None
        self._required_trust_domains: Optional[set[str]] = None  # None = any
        self._inbox: queue.Queue = queue.Queue()
        self._other_end: Optional["AgentChannelSocket"] = None
        self._closed = False
        self._stats = stats if stats is not None else AgentChannelStats()
        self._lock = threading.Lock()

    # -- setsockopt helpers --------------------------------------------------

    def set_trust_domain_require(self, domains: set[str]) -> None:
        if not isinstance(domains, set) or not all(isinstance(d, str) for d in domains):
            raise TypeError("domains must be set[str]")
        self._required_trust_domains = {d.lower() for d in domains}

    # -- Client-side connect -------------------------------------------------

    @classmethod
    def connect(
        cls,
        *,
        listener_addr: str,
        verifier: _VerifierProtocol,
        own_svid_jwt: str,
        own_trust_domain: str,
        required_trust_domains: Optional[set[str]] = None,
        stats: Optional[AgentChannelStats] = None,
    ) -> "AgentChannelSocket":
        """Client-side handshake. Returns a fully-handshaked socket."""
        with _registry_lock:
            listener = _listener_registry.get(listener_addr)
        if listener is None:
            raise AgentChannelError(f"no listener at {listener_addr!r}")
        client = cls(
            verifier=verifier,
            own_svid_jwt=own_svid_jwt,
            own_trust_domain=own_trust_domain,
            stats=stats,
        )
        if required_trust_domains is not None:
            client.set_trust_domain_require(required_trust_domains)
        listener._handshake_with_client(client)
        return client

    # -- Server-side accept (called by listener) -----------------------------

    def _accept_connection(self, client: "AgentChannelSocket") -> None:
        # Both ends offer SVIDs + verify.
        client_offer = _SVIDOffer(
            svid_jwt=client._own_svid_jwt,
            presented_trust_domain=client._own_trust_domain,
        )
        server_offer = _SVIDOffer(
            svid_jwt=self._own_svid_jwt, presented_trust_domain=self._own_trust_domain
        )

        # Server verifies client's SVID.
        try:
            client_verified = self._verifier.verify(client_offer.svid_jwt)
        except Exception as exc:
            with self._lock:
                self._stats.handshakes_rejected_peer_auth += 1
            raise PeerVerificationError(f"server failed to verify client SVID: {exc}")

        if (
            self._required_trust_domains is not None
            and client_offer.presented_trust_domain.lower()
            not in self._required_trust_domains
        ):
            with self._lock:
                self._stats.handshakes_rejected_trust_required += 1
            raise TrustDomainRequiredError(
                f"client trust domain {client_offer.presented_trust_domain!r} "
                f"not in required set {sorted(self._required_trust_domains)!r}"
            )

        # Client verifies server's SVID.
        try:
            server_verified = client._verifier.verify(server_offer.svid_jwt)
        except Exception as exc:
            with client._lock:
                client._stats.handshakes_rejected_peer_auth += 1
            raise PeerVerificationError(f"client failed to verify server SVID: {exc}")

        if (
            client._required_trust_domains is not None
            and server_offer.presented_trust_domain.lower()
            not in client._required_trust_domains
        ):
            with client._lock:
                client._stats.handshakes_rejected_trust_required += 1
            raise TrustDomainRequiredError(
                f"server trust domain {server_offer.presented_trust_domain!r} "
                f"not in client's required set {sorted(client._required_trust_domains)!r}"
            )

        # Both sides verified — pin peer identity + wire up the in-process pipe.
        self.peer_spiffe_id = self._extract_spiffe_id(client_verified)
        self.peer_trust_domain = client_offer.presented_trust_domain
        client.peer_spiffe_id = self._extract_spiffe_id(server_verified)
        client.peer_trust_domain = server_offer.presented_trust_domain
        self._other_end = client
        client._other_end = self

        with self._lock:
            self._stats.handshakes_completed += 1
        with client._lock:
            client._stats.handshakes_completed += 1

    @staticmethod
    def _extract_spiffe_id(verified: Any) -> str:
        """Pull the SPIFFE-ID string out of whatever the F1/F4 verifier
        returned. The two layers expose slightly different fields:
        F1 returns .spiffe_id_str, F4 returns .spiffe_id."""
        candidate = getattr(verified, "spiffe_id_str", None) or getattr(
            verified, "spiffe_id", None
        )
        if hasattr(candidate, "full_uri"):
            candidate = candidate.full_uri
        return str(candidate or "")

    # -- Data path -----------------------------------------------------------

    def send(self, data: bytes) -> int:
        if self._closed:
            raise ChannelClosedError("send on closed socket")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes-like")
        if self._other_end is None:
            raise AgentChannelError("send before handshake completed")
        bs = bytes(data)
        self._other_end._inbox.put(bs)
        with self._lock:
            self._stats.bytes_sent += len(bs)
        return len(bs)

    def recv(self, timeout: Optional[float] = None) -> bytes:
        if self._closed:
            raise ChannelClosedError("recv on closed socket")
        try:
            payload = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("recv timed out")
        with self._lock:
            self._stats.bytes_received += len(payload)
        return payload

    def close(self) -> None:
        self._closed = True
        # Signal the peer with an empty payload so a blocking recv unblocks.
        try:
            if self._other_end is not None:
                self._other_end._inbox.put(b"")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AgentChannelListener
# ---------------------------------------------------------------------------


class AgentChannelListener:
    """Server-side listener. Bind to an `addr` string, call .accept() to
    receive a fully-handshaked AgentChannelSocket."""

    def __init__(
        self,
        *,
        addr: str,
        verifier: _VerifierProtocol,
        own_svid_jwt: str,
        own_trust_domain: str,
        required_trust_domains: Optional[set[str]] = None,
        stats: Optional[AgentChannelStats] = None,
    ):
        if not addr:
            raise ValueError("addr required")
        self._addr = addr
        self._verifier = verifier
        self._own_svid_jwt = own_svid_jwt
        self._own_trust_domain = own_trust_domain
        self._required = (
            {d.lower() for d in required_trust_domains}
            if required_trust_domains
            else None
        )
        self._stats = stats if stats is not None else AgentChannelStats()
        self._accepted: queue.Queue = queue.Queue()
        with _registry_lock:
            if addr in _listener_registry:
                raise AgentChannelError(f"addr {addr!r} already in use")
            _listener_registry[addr] = self

    def _handshake_with_client(self, client: AgentChannelSocket) -> None:
        # Server constructs a fresh server-side socket.
        server = AgentChannelSocket(
            verifier=self._verifier,
            own_svid_jwt=self._own_svid_jwt,
            own_trust_domain=self._own_trust_domain,
            stats=self._stats,
        )
        if self._required is not None:
            server._required_trust_domains = set(self._required)
        # Perform the handshake (both sides verify each other's SVID).
        server._accept_connection(client)
        # Make available to the next .accept() call.
        self._accepted.put(server)

    def accept(self, timeout: Optional[float] = None) -> AgentChannelSocket:
        try:
            return self._accepted.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("accept timed out")

    def close(self) -> None:
        with _registry_lock:
            _listener_registry.pop(self._addr, None)


def reset_registry() -> None:
    """Test helper — clears the in-process listener registry."""
    with _registry_lock:
        _listener_registry.clear()


__all__ = [
    "AgentChannelError",
    "PeerVerificationError",
    "TrustDomainRequiredError",
    "ChannelClosedError",
    "AgentChannelSocket",
    "AgentChannelListener",
    "AgentChannelStats",
    "reset_registry",
]
