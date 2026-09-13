"""
backend/security/egress_policy_combined.py
============================================

Sprint 16 / Item H1 — Combined egress policy (DNS-pin + IFC taint).

What this is
------------

From the 80-problem agent-era catalog, H1:
  "Agent legitimate egress breaks traditional firewall rules — agents
   need broad search + API access; allowlists don't scale."

The Sprint 15 runtime_firewall (H4) ships DNS pinning to defeat
DNS-rebinding. The Sprint 16 Wave 2 C7 IFC engine ships taint
propagation + per-sink egress policy. Each is useful alone; the
agent-era threat model needs them combined:

  - Stand-alone DNS pinning permits an attacker-controlled-but-pinned
    host (e.g. paste.example.com) to receive exfiltrated content.
  - Stand-alone taint policy permits clean tainted-content delivery
    to a DNS-rebinding-attacker target.

This module funnels every egress request through BOTH gates and
returns a unified CombinedEgressDecision with the rejecting layer
identified for audit.

Outcome taxonomy
----------------

  ALLOWED                      — both gates permit.
  DENY_DNS_PIN                 — DNS pin rejected (rebinding detected).
  DENY_TAINTED_DESTINATION     — taint engine rejected (label > sink max).
  DENY_BOTH                    — both gates rejected; rare but logged
                                  separately for forensics.

Public surface
--------------

  CombinedEgressGate(taint_engine, dns_pinner)
    .check(blob, target_host, sink) -> CombinedEgressDecision
    .snapshot_stats() -> CombinedEgressStats

Cross-link
----------

  - C7 TaintEngine (backend/security/ifc_engine.py, Sprint 16 Wave 2)
  - H4 runtime_firewall DNS pinning
    (backend/core/security/connectors/runtime_firewall.py, Sprint 15)
  - A3 CapTable (backend/security/capability_table.py, Wave 1)
    is consulted at the lower kernel layer; H1 ops above it on the
    application-egress chokepoint.

Honest scope ceiling
--------------------

  - This is the policy chokepoint. The actual network egress call
    site (httpx/aiohttp client interceptors) must funnel through
    this gate. Existing call sites already integrate H4; the
    integration patch that adds C7 is a tiny follow-up.
  - DNS pinning is plumbed via a simple protocol — production wires
    the H4 runtime_firewall.RuntimeFirewall._check_dns_pin path;
    tests use a stub that exercises both ALLOW and rebinding paths.
  - The gate is STATELESS per call; pin cache + taint provenance
    live in the wrapped components.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Any, Optional, Protocol

# ---------------------------------------------------------------------------
# Outcome taxonomy
# ---------------------------------------------------------------------------


class CombinedEgressOutcome(str, enum.Enum):
    ALLOWED = "allowed"
    DENY_DNS_PIN = "deny_dns_pin"
    DENY_TAINTED_DESTINATION = "deny_tainted_destination"
    DENY_BOTH = "deny_both"


@dataclass(frozen=True)
class CombinedEgressDecision:
    outcome: CombinedEgressOutcome
    target_host: str
    sink: Any  # the C7 SinkKind passed by the caller
    taint_label: Any  # the C7 TaintLabel of the blob
    dns_pin_reason: str = ""  # populated when DNS pin rejected
    taint_reason: str = ""  # populated when taint rejected
    blob_sha256: str = ""  # carries through from TaintedBlob


@dataclass
class CombinedEgressStats:
    total_checks: int = 0
    allowed: int = 0
    deny_dns_pin: int = 0
    deny_tainted: int = 0
    deny_both: int = 0


# ---------------------------------------------------------------------------
# DNS pinner protocol
#
# Production wires this to the runtime_firewall RuntimeFirewall instance
# whose _check_dns_pin returns an internal outcome enum; for the H1
# gate, we expose a tiny structural protocol so test stubs and the
# production firewall both work.
# ---------------------------------------------------------------------------


class DnsPinResult(enum.IntEnum):
    OK = 0
    DENY_REBIND = 1


class DnsPinner(Protocol):
    def check_pin(self, host: str) -> tuple[DnsPinResult, str]:
        """Returns (result, reason)."""
        ...


# Default in-memory pinner — useful for tests and as a documentation example.


class DefaultDnsPinner:
    """Trivial first-resolution-wins pinner. Production deployments wire
    the H4 RuntimeFirewall instead; this default is provided so the H1
    gate is usable in tests without spinning up runtime_firewall."""

    def __init__(self):
        self._pinned: dict[str, str] = {}  # host -> first observed IP
        self._lock = threading.Lock()

    def _resolve(self, host: str) -> str:
        """Stub — production overrides with actual DNS lookup."""
        return f"127.0.0.1#{host}"

    def check_pin(self, host: str) -> tuple[DnsPinResult, str]:
        if not host:
            return DnsPinResult.DENY_REBIND, "empty_host"
        with self._lock:
            current = self._resolve(host)
            pinned = self._pinned.get(host)
            if pinned is None:
                self._pinned[host] = current
                return DnsPinResult.OK, "first_resolution_pinned"
            if pinned != current:
                return DnsPinResult.DENY_REBIND, (
                    f"rebind_detected: pinned={pinned!r} current={current!r}"
                )
            return DnsPinResult.OK, "pinned_match"


# ---------------------------------------------------------------------------
# CombinedEgressGate
# ---------------------------------------------------------------------------


class CombinedEgressGate:
    """Funnels every egress request through DNS pinning + IFC taint
    policy and emits one unified decision."""

    def __init__(self, *, taint_engine: Any, dns_pinner: Optional[DnsPinner] = None):
        if taint_engine is None:
            raise ValueError("taint_engine is required")
        # Light duck-typing: the engine MUST expose .check_egress(blob, sink).
        if not hasattr(taint_engine, "check_egress"):
            raise TypeError("taint_engine must expose .check_egress(blob, sink)")
        self._taint = taint_engine
        self._dns = dns_pinner if dns_pinner is not None else DefaultDnsPinner()
        self._lock = threading.Lock()
        self._stats = CombinedEgressStats()

    def check(
        self, *, blob: Any, target_host: str, sink: Any
    ) -> CombinedEgressDecision:
        if blob is None:
            raise ValueError("blob is required (TaintedBlob from C7)")
        if not isinstance(target_host, str) or not target_host:
            raise ValueError("target_host must be a non-empty string")
        if sink is None:
            raise ValueError("sink is required (C7 SinkKind)")

        # Pull blob attrs that we surface in the decision (provided by C7's
        # TaintedBlob — duck-typed so test fakes are easy).
        blob_label = getattr(blob, "label", None)
        blob_sha256 = getattr(blob, "sha256", "") or ""

        # DNS pin gate.
        dns_result, dns_reason = self._dns.check_pin(target_host)
        # Taint gate.
        taint_decision = self._taint.check_egress(blob, sink)
        taint_kind = getattr(taint_decision, "kind", None)
        taint_kind_value = getattr(
            taint_kind, "value", getattr(taint_kind, "name", str(taint_kind))
        )
        taint_denied = taint_kind_value in (1, "DENY", "deny") or (
            hasattr(taint_kind, "name") and taint_kind.name == "DENY"
        )
        # The C7 EgressDecisionKind enum uses .name == "DENY" / "ALLOW".
        # Fallback: any non-ALLOW value treated as denied.
        if hasattr(taint_kind, "name"):
            taint_denied = taint_kind.name != "ALLOW"
        taint_reason = getattr(taint_decision, "reason", "")

        dns_denied = dns_result == DnsPinResult.DENY_REBIND

        with self._lock:
            self._stats.total_checks += 1
            if dns_denied and taint_denied:
                outcome = CombinedEgressOutcome.DENY_BOTH
                self._stats.deny_both += 1
            elif dns_denied:
                outcome = CombinedEgressOutcome.DENY_DNS_PIN
                self._stats.deny_dns_pin += 1
            elif taint_denied:
                outcome = CombinedEgressOutcome.DENY_TAINTED_DESTINATION
                self._stats.deny_tainted += 1
            else:
                outcome = CombinedEgressOutcome.ALLOWED
                self._stats.allowed += 1

        return CombinedEgressDecision(
            outcome=outcome,
            target_host=target_host,
            sink=sink,
            taint_label=blob_label,
            dns_pin_reason=(dns_reason if dns_denied else ""),
            taint_reason=(taint_reason if taint_denied else ""),
            blob_sha256=blob_sha256,
        )

    def snapshot_stats(self) -> CombinedEgressStats:
        with self._lock:
            return CombinedEgressStats(
                total_checks=self._stats.total_checks,
                allowed=self._stats.allowed,
                deny_dns_pin=self._stats.deny_dns_pin,
                deny_tainted=self._stats.deny_tainted,
                deny_both=self._stats.deny_both,
            )


__all__ = [
    "CombinedEgressOutcome",
    "CombinedEgressDecision",
    "CombinedEgressStats",
    "DnsPinResult",
    "DnsPinner",
    "DefaultDnsPinner",
    "CombinedEgressGate",
]
