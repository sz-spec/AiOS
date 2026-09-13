"""
backend/core/security/ibct_engine.py
=====================================

Sprint 21 / Item B6 (also satisfies F6) — Invocation-Bound Capability
Token (IBCT) engine with a Datalog-style delegation-graph policy
(fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, B6:
  "Cross-agent privilege delegation is unmodeled — when agents call
   agents call agents, there is no OS-level account of who is allowed to
   ask whom to do what. Authority leaks transitively and nobody can
   answer 'is agent C allowed to run tool T right now, and on whose
   authority?'."

(F6 — "IBCT proposed in IETF drafts but not adopted" — is satisfied by
shipping a concrete IBCT implementation.)

Two pieces:

  1. A **delegation graph** evaluated by a small Datalog-style fixpoint:
        authorized(A, T) :- root_grant(A, T).
        authorized(B, T) :- authorized(A, T), delegates(A, B, T).
     i.e. authority flows along delegation edges and ATTENUATES (an edge
     can only pass tools the delegator already holds). ``query(agent,
     tool)`` answers "is this derivable?" — the proven delegation graph.

  2. **Invocation-Bound Capability Tokens**: a capability minted for a
     specific invocation context (a task/session id). The token's MAC
     binds (agent, tool, invocation_id), so a token issued for invocation
     X cannot be replayed in invocation Y — closing the token-replay hole
     that plain bearer capabilities leave open.

The engine **refuses any cross-agent invoke that is not derivable in the
graph** (fail-closed): require_cross_agent_invoke(caller, callee, tool)
checks the caller actually held the authority AND delegated it to the
callee before minting an IBCT.

Enforcement contract
--------------------

    eng = IbctEngine()
    eng.grant_root("orchestrator", {"web.fetch", "fs.read"})
    eng.delegate("orchestrator", "researcher", {"web.fetch"})
    token = eng.require_cross_agent_invoke(
        "orchestrator", "researcher", "web.fetch", invocation_id="task-42")
    eng.verify_token(token, invocation_id="task-42")     # ok
    eng.verify_token(token, invocation_id="task-99")     # raises (replay)

Honest scope ceiling
--------------------

  - The fixpoint is the policy-evaluation layer. Enforcement at the
    actual cross-agent call site still depends on every invoke routing
    through require_cross_agent_invoke / verify_token (the same
    bypass-caveat as B1's table). The token MAC uses an in-process
    HMAC secret; binding tokens to an Ed25519 issuer identity for
    cross-process verification reuses the B1/B2 signing path and is a
    follow-up. Attenuation means delegation cycles cannot escalate
    authority (a cycle adds no new tools), so the fixpoint always
    terminates at the least model.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


class IbctError(Exception):
    """Base class."""


class CrossAgentInvokeDenied(IbctError):
    """Raised when a cross-agent invoke is not derivable in the delegation
    graph. Fail-closed."""


class TokenVerifyError(IbctError):
    """Raised when an IBCT fails verification (bad MAC, wrong invocation,
    expired). Fail-closed."""


@dataclass(frozen=True)
class DelegationEdge:
    frm: str
    to: str
    scope: frozenset  # frozenset[str] of tool names


@dataclass(frozen=True)
class Ibct:
    """Invocation-Bound Capability Token."""

    agent_id: str
    tool: str
    invocation_id: str
    issued_seq: int  # monotonic issue counter (audit / ordering)
    mac: bytes

    def binding_message(self) -> bytes:
        h = hashlib.sha256()
        for part in (
            b"vos3-ibct",
            self.agent_id.encode("utf-8"),
            self.tool.encode("utf-8"),
            self.invocation_id.encode("utf-8"),
            self.issued_seq.to_bytes(8, "big"),
        ):
            h.update(len(part).to_bytes(8, "big"))
            h.update(part)
        return h.digest()


@dataclass
class IbctStats:
    root_grants: int = 0
    delegations: int = 0
    queries: int = 0
    invokes_allowed: int = 0
    invokes_denied: int = 0
    tokens_issued: int = 0
    tokens_verified: int = 0
    tokens_rejected: int = 0


@dataclass
class IbctEngine:
    """Delegation-graph policy + invocation-bound capability tokens."""

    secret: bytes = field(default_factory=lambda: os.urandom(32))
    _root_grants: dict = field(default_factory=dict)  # agent -> set[tool]
    _edges: list = field(default_factory=list)  # list[DelegationEdge]
    _seq: int = 0
    stats: IbctStats = field(default_factory=IbctStats)

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def grant_root(self, agent_id: str, tools: Iterable[str]) -> None:
        """Seed a root authority: this agent holds these tools directly."""
        if not agent_id:
            raise IbctError("agent_id required")
        self._root_grants.setdefault(agent_id, set()).update(str(t) for t in tools)
        self.stats.root_grants += 1

    def delegate(self, frm: str, to: str, scope: Iterable[str]) -> DelegationEdge:
        """Record a delegation edge frm→to for the given tool scope. This is
        only a FACT; whether it confers authority depends on frm actually
        being authorized for those tools (checked at query/invoke time)."""
        if not frm or not to:
            raise IbctError("both frm and to are required")
        if frm == to:
            raise IbctError("self-delegation is not meaningful")
        edge = DelegationEdge(frm=frm, to=to, scope=frozenset(str(t) for t in scope))
        self._edges.append(edge)
        self.stats.delegations += 1
        return edge

    # ------------------------------------------------------------------
    # Datalog-style fixpoint: authorized(agent, tool)
    # ------------------------------------------------------------------

    def _authorized_set(self) -> set:
        """Least fixpoint of {(agent, tool)} pairs. Semi-naive evaluation;
        attenuation (edge.scope) means cycles add nothing, so it
        terminates."""
        authorized: set = set()
        for agent, tools in self._root_grants.items():
            for t in tools:
                authorized.add((agent, t))
        changed = True
        while changed:
            changed = False
            for edge in self._edges:
                for t in edge.scope:
                    if (edge.frm, t) in authorized and (edge.to, t) not in authorized:
                        authorized.add((edge.to, t))
                        changed = True
        return authorized

    def query(self, agent_id: str, tool: str) -> bool:
        """Is authorized(agent, tool) derivable in the delegation graph?"""
        self.stats.queries += 1
        return (agent_id, tool) in self._authorized_set()

    def is_authorized(self, agent_id: str, tool: str) -> bool:
        return (agent_id, tool) in self._authorized_set()

    # ------------------------------------------------------------------
    # Cross-agent invoke (fail-closed) + IBCT issuance
    # ------------------------------------------------------------------

    def _delegates_directly(self, frm: str, to: str, tool: str) -> bool:
        return any(e.frm == frm and e.to == to and tool in e.scope for e in self._edges)

    def require_cross_agent_invoke(
        self,
        caller: str,
        callee: str,
        tool: str,
        *,
        invocation_id: str,
    ) -> Ibct:
        """Allow caller→callee invocation of ``tool`` iff (1) the caller is
        itself authorized for the tool AND (2) the caller has a delegation
        edge to the callee covering the tool — i.e. the invoke is inside
        the proven delegation graph. Returns an IBCT bound to
        ``invocation_id``; raises CrossAgentInvokeDenied otherwise."""
        if not invocation_id:
            raise IbctError("invocation_id is required (tokens are invocation-bound)")
        authorized = self._authorized_set()
        if (caller, tool) not in authorized:
            self.stats.invokes_denied += 1
            logger.error(
                "[ibct] cross-agent invoke REFUSED — caller %r not authorized "
                "for tool %r",
                caller,
                tool,
            )
            raise CrossAgentInvokeDenied(
                f"caller {caller!r} is not authorized for tool {tool!r}; "
                f"cannot delegate authority it does not hold"
            )
        if not self._delegates_directly(caller, callee, tool):
            self.stats.invokes_denied += 1
            logger.error(
                "[ibct] cross-agent invoke REFUSED — no delegation edge "
                "%r→%r covering tool %r",
                caller,
                callee,
                tool,
            )
            raise CrossAgentInvokeDenied(
                f"no delegation edge {caller!r}→{callee!r} covering tool "
                f"{tool!r} — invoke is outside the proven delegation graph"
            )
        self.stats.invokes_allowed += 1
        return self._issue_token(callee, tool, invocation_id)

    def _issue_token(self, agent_id: str, tool: str, invocation_id: str) -> Ibct:
        self._seq += 1
        draft = Ibct(
            agent_id=agent_id,
            tool=tool,
            invocation_id=invocation_id,
            issued_seq=self._seq,
            mac=b"",
        )
        mac = hmac.new(self.secret, draft.binding_message(), hashlib.sha256).digest()
        token = Ibct(
            agent_id=agent_id,
            tool=tool,
            invocation_id=invocation_id,
            issued_seq=self._seq,
            mac=mac,
        )
        self.stats.tokens_issued += 1
        return token

    def verify_token(self, token: Ibct, *, invocation_id: str) -> Ibct:
        """Verify an IBCT is authentic AND bound to ``invocation_id``. A
        token minted for a different invocation fails (no replay). Raises
        TokenVerifyError on failure (fail-closed)."""
        if token.invocation_id != invocation_id:
            self.stats.tokens_rejected += 1
            raise TokenVerifyError(
                f"token bound to invocation {token.invocation_id!r}, "
                f"presented for {invocation_id!r} (replay refused)"
            )
        expected = hmac.new(
            self.secret, token.binding_message(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, token.mac):
            self.stats.tokens_rejected += 1
            raise TokenVerifyError("IBCT MAC does not verify (forged/tampered)")
        self.stats.tokens_verified += 1
        return token


__all__ = [
    "IbctError",
    "CrossAgentInvokeDenied",
    "TokenVerifyError",
    "DelegationEdge",
    "Ibct",
    "IbctEngine",
    "IbctStats",
]
