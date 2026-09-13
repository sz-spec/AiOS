"""
backend/core/security/agent_capability_table.py
================================================

Sprint 21 / Item B1 — agent tool-call capability table (unforgeable,
fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, B1:
  "POSIX permissions can't express scoped agent rights — a process uid
   either can or can't open a file; there is no OS primitive for 'this
   agent may call the `web.fetch` tool, but only against *.corp.example,
   and only for the next 5 minutes'. Agent frameworks bolt scoping on in
   userspace where a prompt-injected agent can talk its way around it."

This module is the OS-level capability primitive that POSIX lacks,
scoped to *agent tool calls*. A capability is an **unforgeable,
Ed25519-signed token** that grants one agent the right to invoke one
tool within one scope (a resource prefix) with a set of permissions,
until it expires. The gate **refuses any tool call not covered by a
valid capability** (fail-closed: refuse the uncapped call).

Relationship to the A3 capability table:
``backend/security/capability_table.py`` is the per-byte MEMORY
capability table (range + perms over a buffer). This is its sibling for
the *tool-call* axis: instead of "may write bytes [base, base+len)", it
expresses "may invoke tool T against resource R". Same fail-closed
philosophy, different resource.

Why signed (unforgeable)?
-------------------------

The token is signed by the issuer's Ed25519 key. The gate verifies the
signature against the issuer public key before honouring a capability,
so a compromised/prompt-injected agent cannot fabricate a token that
widens its own scope — a forged or tampered token fails the signature
check and the call is refused. This is the "unforgeable" the catalog
asks for, achieved in software (CHERI sealed capabilities are the
silicon analogue, tracked under A3).

Enforcement contract
--------------------

    gate = AgentCapabilityGate.generate()
    cap = gate.grant(agent_id="agent-7", tool="web.fetch",
                     scope_prefix="https://corp.example/", ttl_seconds=300)
    gate.require_capability("agent-7", "web.fetch",
                            resource="https://corp.example/data")   # ok
    gate.require_capability("agent-7", "web.fetch",
                            resource="https://evil.example/")       # raises
                                                                    # CapabilityDenied

Honest scope ceiling
--------------------

  - This is the policy + cryptographic-verification layer. Like the A3
    table, it does NOT stop code that bypasses the ``require_capability``
    call entirely; that requires the kernel syscall gate (mm/ai_cap.c,
    the kernel twin) or CHERI silicon. It makes the *token* unforgeable,
    not the *call site* unbypassable.
  - Scope is a resource PREFIX (attenuation by prefix). Glob/regex
    scopes are a follow-up; prefix covers the dominant
    host/path-scoping case without a regex-DoS surface.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class ToolPerm(enum.IntFlag):
    INVOKE = 0x01  # may call the tool at all
    READ = 0x02  # read-class operation
    WRITE = 0x04  # mutating operation
    DELEGATE = 0x08  # may delegate this capability to a sub-agent (see B2/B6)

    RO = INVOKE | READ
    RW = INVOKE | READ | WRITE


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CapabilityError(Exception):
    """Base class."""


class CapabilityDenied(CapabilityError):
    """Raised by require_capability() when no valid capability covers the
    requested (agent, tool, resource, perm). Fail-closed."""


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class ClockProtocol(Protocol):
    def time(self) -> float: ...


class SystemClock:
    def time(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Crypto helpers (Ed25519, via in-tree `cryptography`)
# ---------------------------------------------------------------------------


def _sha256(*chunks: bytes) -> bytes:
    h = hashlib.sha256()
    for c in chunks:
        h.update(len(c).to_bytes(8, "big"))
        h.update(c)
    return h.digest()


def generate_keypair() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub


def _sign(private_bytes: bytes, message: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.from_private_bytes(private_bytes).sign(message)


def _verify(public_bytes: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Scope-prefix matcher (B5-1 fix)
# ---------------------------------------------------------------------------

# Path/host segment delimiters. A prefix grant only covers a resource when the
# match ends at one of these boundaries (or the prefix already ends in one, or
# it is an exact match) — so "https://corp.example" does NOT cover the rogue
# look-alike "https://corp.example.evil.com".
_SCOPE_DELIMITERS = "/:"


def _scope_prefix_covers(scope_prefix: str, resource: str) -> bool:
    """Strict, delimiter-aware prefix containment.

    - "" (empty prefix) = whole-tool scope → covers everything.
    - exact match → covered.
    - otherwise the resource must start with the prefix AND the junction must
      fall on a delimiter boundary: either the prefix itself ends in a
      delimiter, or the first resource character past the prefix is a
      delimiter. This blocks the B5-1 prefix-confusion escape where a
      delimiter-less prefix would `str.startswith`-match a sibling host/path.
    """
    if scope_prefix == "":
        return True
    if not resource.startswith(scope_prefix):
        return False
    if len(resource) == len(scope_prefix):
        return True  # exact match
    if scope_prefix[-1] in _SCOPE_DELIMITERS:
        return True  # prefix already terminates at a boundary
    return resource[len(scope_prefix)] in _SCOPE_DELIMITERS


# ---------------------------------------------------------------------------
# Capability token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCapability:
    agent_id: str
    tool: str
    scope_prefix: str  # "" = whole-tool scope
    perms: int  # ToolPerm bits
    issued_at: float
    expires_at: float
    nonce: bytes
    issuer_pub: bytes
    signature: bytes

    def canonical_payload(self) -> bytes:
        return _sha256(
            b"vos3-agent-cap",
            self.agent_id.encode("utf-8"),
            self.tool.encode("utf-8"),
            self.scope_prefix.encode("utf-8"),
            int(self.perms).to_bytes(4, "big"),
            repr(self.issued_at).encode("utf-8"),
            repr(self.expires_at).encode("utf-8"),
            self.nonce,
            self.issuer_pub,
        )

    @property
    def cap_id(self) -> str:
        return self.canonical_payload().hex()[:16]

    def covers(self, agent_id: str, tool: str, resource: str, perm: int) -> bool:
        if self.agent_id != agent_id or self.tool != tool:
            return False
        if not _scope_prefix_covers(self.scope_prefix, resource):
            return False
        return (self.perms & perm) == perm


# ---------------------------------------------------------------------------
# Gate / table
# ---------------------------------------------------------------------------


@dataclass
class CapabilityGateStats:
    granted: int = 0
    revoked: int = 0
    checks: int = 0
    allowed: int = 0
    denied: int = 0
    bad_signature: int = 0


@dataclass
class AgentCapabilityGate:
    """Holds the issuer keypair + the live capability table. Grants signed
    tokens and refuses any uncapped/over-scoped/expired/forged tool call."""

    issuer_private: bytes
    issuer_public: bytes
    clock: ClockProtocol = field(default_factory=SystemClock)
    _table: list[AgentCapability] = field(default_factory=list)
    stats: CapabilityGateStats = field(default_factory=CapabilityGateStats)

    @classmethod
    def generate(cls, clock: Optional[ClockProtocol] = None) -> "AgentCapabilityGate":
        priv, pub = generate_keypair()
        return cls(issuer_private=priv, issuer_public=pub, clock=clock or SystemClock())

    # ------------------------------------------------------------------
    # Grant / revoke
    # ------------------------------------------------------------------

    def grant(
        self,
        *,
        agent_id: str,
        tool: str,
        scope_prefix: str = "",
        perms: int = ToolPerm.RO,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        nonce: Optional[bytes] = None,
    ) -> AgentCapability:
        if not agent_id or not tool:
            raise CapabilityError("agent_id and tool are required")
        if int(perms) == 0:
            raise CapabilityError("perms must be non-zero")
        now = self.clock.time()
        n = (
            nonce
            if nonce is not None
            else _sha256(
                b"nonce",
                agent_id.encode(),
                tool.encode(),
                repr(now).encode(),
            )[:16]
        )
        draft = AgentCapability(
            agent_id=agent_id,
            tool=tool,
            scope_prefix=scope_prefix,
            perms=int(perms),
            issued_at=now,
            expires_at=now + max(1, int(ttl_seconds)),
            nonce=n,
            issuer_pub=self.issuer_public,
            signature=b"",
        )
        sig = _sign(self.issuer_private, draft.canonical_payload())
        cap = AgentCapability(
            agent_id=draft.agent_id,
            tool=draft.tool,
            scope_prefix=draft.scope_prefix,
            perms=draft.perms,
            issued_at=draft.issued_at,
            expires_at=draft.expires_at,
            nonce=draft.nonce,
            issuer_pub=draft.issuer_pub,
            signature=sig,
        )
        self._table.append(cap)
        self.stats.granted += 1
        logger.info(
            "[agent_cap] granted cap %s agent=%s tool=%s scope=%r perms=0x%x",
            cap.cap_id,
            agent_id,
            tool,
            scope_prefix,
            int(perms),
        )
        return cap

    def install(self, cap: AgentCapability) -> None:
        """Install an externally-issued capability token into the table.
        Its signature is verified at check time, not here — an installed
        forged token simply never passes require_capability()."""
        self._table.append(cap)

    def revoke(self, cap_id: str) -> bool:
        before = len(self._table)
        self._table = [c for c in self._table if c.cap_id != cap_id]
        removed = len(self._table) != before
        if removed:
            self.stats.revoked += 1
        return removed

    def live_capabilities(self) -> tuple[AgentCapability, ...]:
        return tuple(self._table)

    # ------------------------------------------------------------------
    # Enforcement (fail-closed)
    # ------------------------------------------------------------------

    def _valid(self, cap: AgentCapability, now: float) -> bool:
        # Signature must verify against the cap's own issuer_pub AND that
        # issuer_pub must be the one this gate trusts.
        if not hmac.compare_digest(cap.issuer_pub, self.issuer_public):
            return False
        if not _verify(cap.issuer_pub, cap.signature, cap.canonical_payload()):
            self.stats.bad_signature += 1
            return False
        if now > cap.expires_at:
            return False
        return True

    def find_capability(
        self,
        agent_id: str,
        tool: str,
        resource: str = "",
        perm: int = ToolPerm.INVOKE,
    ) -> Optional[AgentCapability]:
        now = self.clock.time()
        for cap in self._table:
            if cap.covers(agent_id, tool, resource, int(perm)) and self._valid(
                cap, now
            ):
                return cap
        return None

    def require_capability(
        self,
        agent_id: str,
        tool: str,
        resource: str = "",
        perm: int = ToolPerm.INVOKE,
    ) -> AgentCapability:
        """Allow the tool call iff a valid, signed, unexpired capability in
        the table covers (agent, tool, resource, perm). Otherwise raise
        CapabilityDenied (fail-closed — refuse the uncapped call)."""
        self.stats.checks += 1
        cap = self.find_capability(agent_id, tool, resource, perm)
        if cap is not None:
            self.stats.allowed += 1
            return cap
        self.stats.denied += 1
        logger.error(
            "[agent_cap] REFUSED (fail-closed) agent=%s tool=%s resource=%r "
            "perm=0x%x — no valid capability",
            agent_id,
            tool,
            resource,
            int(perm),
        )
        raise CapabilityDenied(
            f"agent {agent_id!r} has no valid capability to invoke "
            f"{tool!r} on {resource!r} (perm=0x{int(perm):x})"
        )


__all__ = [
    "ToolPerm",
    "CapabilityError",
    "CapabilityDenied",
    "ClockProtocol",
    "SystemClock",
    "AgentCapability",
    "AgentCapabilityGate",
    "CapabilityGateStats",
    "generate_keypair",
    "DEFAULT_TTL_SECONDS",
]
