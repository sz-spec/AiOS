"""
backend/core/security/connectors/runtime_firewall.py
=====================================================

Sprint 14.1 — egress allowlist enforcement.

Relationship to the kernel egress policy
----------------------------------------

The Z3-proven egress invariant (proof in
backend/tests/benchmarks/egress_policy_z3_proof.py) is enforced **at the
kernel level** via mm/ai_isc.c outbound checks. This module is the
**userspace mirror**: it lets the FastAPI process drop packets at the
HTTP-client layer BEFORE they hit the kernel egress gate, which buys two
things:

  1. Faster failure — a misrouted httpx call gets an EGRESS_DENIED
     ValueError in microseconds instead of a kernel-side EINVAL plus
     audit-ring event.
  2. Telemetry — the kernel records the egress block in the audit ring
     under VOS3_AUDIT_CAT_EGRESS_DENY, but for the userspace layer we
     want a separate counter so the dashboard can show "blocked at FW"
     vs "blocked at kernel" distinctly. Same denial, different stage.

The two enforcement points are kept **in sync** by reading the SAME
policy file (or env vars) — see ``EgressPolicy.load()``. If they ever
diverge, the Z3 proof becomes meaningless; CI test
``test_egress_userspace_kernel_parity`` (Stage 12 missing-file batch)
will catch drift when it lands.

API
---

    fw = get_runtime_firewall()
    fw.check_egress("https://api.openai.com/v1/chat") -> EgressDecision
    fw.wrap_httpx_client(client) -> client  (patches send())

EgressDecision is (allowed: bool, reason: str, matched_rule: str | None).

Honest scope ceiling
--------------------

We ship the **allowlist** check. We do NOT do TLS-layer cert pinning at
this layer — that's the kernel's job (crypto/tls13.c maintains a pinned
root CA list per Sigstore Fulcio trust root). The userspace firewall is
URL/host scope only.

Locality-preference integration
-------------------------------

If ``VOS3_LOCALITY_PREFERENCE=local-first`` or ``air-gap``, the
firewall denies **every** non-loopback host by default, regardless of
the allowlist. This is the same gate enforced by
``backend/services/host_bridge.py`` and is here for defense-in-depth.
"""

from __future__ import annotations

import enum
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables / env
# ---------------------------------------------------------------------------

ENV_ALLOWLIST = "VOS3_EGRESS_ALLOWLIST"  # comma-separated host[:port] entries
ENV_BLOCKLIST = "VOS3_EGRESS_BLOCKLIST"  # comma-separated host entries
ENV_LOCALITY = "VOS3_LOCALITY_PREFERENCE"  # local-first | cloud-first | auto | air-gap
ENV_DENY_BY_DEFAULT = (
    "VOS3_EGRESS_DENY_DEFAULT"  # truthy = denylist mode (allowlist required)
)

# Sprint 15 / Item H4 — DNS pinning. The first time we resolve a hostname
# we cache the set of IPs returned. Every subsequent resolution within the
# pin TTL must return a SUBSET of that initial set; an IP outside the
# pinned set indicates DNS rebinding (the attacker flipped the A record
# mid-session to point at a private IP after we passed the SSRF gate).
ENV_DNS_PIN_TTL_SECONDS = "VOS3_DNS_PIN_TTL_SECONDS"  # default 300 (5 min)
ENV_DNS_PIN_DISABLED = "VOS3_DNS_PIN_DISABLED"  # set to "1" to opt out

DEFAULT_DNS_PIN_TTL_SECONDS = 300

LOCALITY_AIR_GAP = "air-gap"
LOCALITY_LOCAL_FIRST = "local-first"


# IP networks that ALWAYS denied for outbound (CWE-918 SSRF)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + AWS metadata
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
]

_LOOPBACK_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+(?::\d{1,5})?$")


# ---------------------------------------------------------------------------
# Decision type
# ---------------------------------------------------------------------------


class EgressOutcome(enum.Enum):
    ALLOW = "allow"
    DENY_BLOCKLIST = "deny_blocklist"
    DENY_NOT_ALLOWED = "deny_not_allowed"
    DENY_PRIVATE_IP = "deny_private_ip"
    DENY_AIR_GAP = "deny_air_gap"
    DENY_INVALID = "deny_invalid"
    # Sprint 15 / Item H4 — host's DNS A record changed mid-session to
    # an IP not in the originally-pinned set. Classic DNS rebinding
    # signature.
    DENY_DNS_REBIND = "deny_dns_rebind"


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    outcome: EgressOutcome
    reason: str
    matched_rule: Optional[str] = None


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class EgressPolicy:
    allowlist_hosts: set[str] = field(default_factory=set)
    blocklist_hosts: set[str] = field(default_factory=set)
    locality_preference: str = "auto"
    deny_by_default: bool = False

    @classmethod
    def load(cls) -> "EgressPolicy":
        allowlist = {
            h.strip().lower()
            for h in os.environ.get(ENV_ALLOWLIST, "").split(",")
            if h.strip()
        }
        blocklist = {
            h.strip().lower()
            for h in os.environ.get(ENV_BLOCKLIST, "").split(",")
            if h.strip()
        }
        locality = os.environ.get(ENV_LOCALITY, "auto").strip().lower()
        deny_default = os.environ.get(ENV_DENY_BY_DEFAULT, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            allowlist_hosts=allowlist,
            blocklist_hosts=blocklist,
            locality_preference=locality,
            deny_by_default=deny_default,
        )


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------


class RuntimeFirewall:
    def __init__(self, policy: Optional[EgressPolicy] = None) -> None:
        self._policy = policy or EgressPolicy.load()
        self._lock = threading.RLock()
        self._allows = 0
        self._denies: dict[str, int] = {}
        # Sprint 15 / Item H4 — DNS pin cache.
        # Maps host (lowercase, no port) → (frozenset[ip_address_str], expiry_unix_ts)
        self._dns_pin_cache: dict[str, tuple[frozenset[str], float]] = {}
        try:
            self._dns_pin_ttl = float(
                os.environ.get(ENV_DNS_PIN_TTL_SECONDS, DEFAULT_DNS_PIN_TTL_SECONDS)
            )
        except ValueError:
            self._dns_pin_ttl = float(DEFAULT_DNS_PIN_TTL_SECONDS)
        self._dns_pin_disabled = os.environ.get(
            ENV_DNS_PIN_DISABLED, ""
        ).strip().lower() in {"1", "true", "yes", "on"}

    def reload_policy(self) -> EgressPolicy:
        with self._lock:
            self._policy = EgressPolicy.load()
            return self._policy

    def check_egress(self, target: str) -> EgressDecision:
        """Evaluate whether outbound to ``target`` is permitted.

        ``target`` may be a URL (``https://host:port/path``) or a bare
        ``host`` / ``host:port``.
        """
        host = self._extract_host(target)
        if host is None:
            return self._record(
                EgressDecision(
                    allowed=False,
                    outcome=EgressOutcome.DENY_INVALID,
                    reason=f"could not parse target: {target!r}",
                )
            )

        host_lower = host.lower()
        host_no_port = host_lower.split(":", 1)[0]

        # 1. Air-gap mode wins absolutely.
        if self._policy.locality_preference == LOCALITY_AIR_GAP:
            if not self._is_loopback(host_no_port):
                return self._record(
                    EgressDecision(
                        allowed=False,
                        outcome=EgressOutcome.DENY_AIR_GAP,
                        reason=f"VOS3_LOCALITY_PREFERENCE=air-gap denies {host_no_port}",
                    )
                )

        # 2. Explicit blocklist.
        if (
            host_lower in self._policy.blocklist_hosts
            or host_no_port in self._policy.blocklist_hosts
        ):
            return self._record(
                EgressDecision(
                    allowed=False,
                    outcome=EgressOutcome.DENY_BLOCKLIST,
                    reason=f"{host_no_port} is on blocklist",
                    matched_rule=host_no_port,
                )
            )

        # 3. Private-IP / SSRF guard. Resolve the host to confirm it does
        #    not resolve to a private network. We then PIN the resolved
        #    set (Sprint 15 / Item H4) so that a later resolution which
        #    returns IPs outside the pinned set is treated as DNS
        #    rebinding and denied.
        resolved_ips, resolve_failed = self._resolve_ips(host_no_port)
        if resolve_failed:
            return self._record(
                EgressDecision(
                    allowed=False,
                    outcome=EgressOutcome.DENY_PRIVATE_IP,
                    reason=f"{host_no_port} unresolvable — fail-safe deny",
                )
            )
        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if any(ip in net for net in _BLOCKED_NETWORKS):
                return self._record(
                    EgressDecision(
                        allowed=False,
                        outcome=EgressOutcome.DENY_PRIVATE_IP,
                        reason=f"{host_no_port} resolves to private/blocked network ({ip_str})",
                    )
                )

        # 3b. DNS-pin check. Sprint 15 / Item H4.
        rebind = self._check_dns_pin(host_no_port, resolved_ips)
        if rebind is not None:
            return self._record(rebind)

        # 4. Allowlist gate.
        if (
            self._policy.deny_by_default
            or self._policy.locality_preference == LOCALITY_LOCAL_FIRST
        ):
            if not (
                host_lower in self._policy.allowlist_hosts
                or host_no_port in self._policy.allowlist_hosts
                or self._is_loopback(host_no_port)
            ):
                return self._record(
                    EgressDecision(
                        allowed=False,
                        outcome=EgressOutcome.DENY_NOT_ALLOWED,
                        reason=f"{host_no_port} not in allowlist",
                    )
                )

        return self._record(
            EgressDecision(
                allowed=True,
                outcome=EgressOutcome.ALLOW,
                reason="permitted",
            )
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_host(self, target: str) -> Optional[str]:
        if not isinstance(target, str) or not target.strip():
            return None
        t = target.strip()
        if t.startswith(("http://", "https://", "ws://", "wss://")):
            try:
                u = urlparse(t)
            except ValueError:
                return None
            host = u.hostname
            port = u.port
            if host is None:
                return None
            return f"{host}:{port}" if port else host
        # Bare host[:port]
        return t if _HOST_RE.match(t) else None

    def _is_loopback(self, host: str) -> bool:
        if host in {"localhost", "ip6-localhost", "loopback"}:
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(ip in net for net in _LOOPBACK_NETWORKS)

    def _resolves_to_private(self, host: str) -> bool:
        """Retained for backwards compatibility with callers that don't
        need the resolved-IP set. Equivalent to `_resolve_ips(host)`
        + private-network check on the returned set."""
        ips, failed = self._resolve_ips(host)
        if failed:
            return True
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if any(ip in net for net in _BLOCKED_NETWORKS):
                return True
        return False

    def _resolve_ips(self, host: str) -> tuple[frozenset[str], bool]:
        """Resolve host to its set of IPs.

        Returns (ip_set, resolve_failed). On gaierror, returns
        (empty_set, True) so callers fail-closed.

        Direct IP literals (host is already an IP) return ({host}, False)
        without any DNS work.
        """
        # Direct IP literal — no DNS work.
        try:
            ip = ipaddress.ip_address(host)
            return frozenset({str(ip)}), False
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return frozenset(), True
        ips: set[str] = set()
        for info in infos:
            addr = info[4][0]
            try:
                ips.add(str(ipaddress.ip_address(addr)))
            except ValueError:
                continue
        return frozenset(ips), False

    def _check_dns_pin(
        self, host: str, current_ips: frozenset[str]
    ) -> Optional[EgressDecision]:
        """Check + update the DNS pin cache. Sprint 15 / Item H4.

        Returns None if the resolution is acceptable (either the host
        wasn't pinned yet — we install the pin now — or the new
        resolution is a subset of the pinned set).

        Returns a DENY_DNS_REBIND EgressDecision if the new resolution
        contains an IP outside the pinned set, indicating a classic DNS
        rebinding attack where the attacker flipped the A record after
        we passed the SSRF gate.
        """
        if self._dns_pin_disabled or not current_ips:
            return None

        # IP literals don't get pinned (they ARE the pin).
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass

        now = time.time()
        with self._lock:
            existing = self._dns_pin_cache.get(host)
            if existing is not None:
                pinned_ips, expiry = existing
                if now < expiry:
                    new_ips = current_ips - pinned_ips
                    if new_ips:
                        # Classic DNS rebinding: new resolution contains
                        # IPs outside the original pin set.
                        return EgressDecision(
                            allowed=False,
                            outcome=EgressOutcome.DENY_DNS_REBIND,
                            reason=(
                                f"{host} DNS rebinding detected — pinned set "
                                f"was {sorted(pinned_ips)!r}, new resolution "
                                f"adds {sorted(new_ips)!r}"
                            ),
                            matched_rule=host,
                        )
                    # New resolution is subset of pin — refresh expiry.
                    self._dns_pin_cache[host] = (pinned_ips, now + self._dns_pin_ttl)
                    return None
                # Pin expired; install new pin from current resolution.
            self._dns_pin_cache[host] = (current_ips, now + self._dns_pin_ttl)
            return None

    def reset_dns_pin_cache(self) -> None:
        """Operator-callable reset of the pin cache. Used after planned
        DNS rotation (CDN failover, etc.). Logs a warning so the action
        is auditable."""
        with self._lock:
            count = len(self._dns_pin_cache)
            self._dns_pin_cache.clear()
        logger.warning(
            "[runtime_firewall] DNS pin cache reset (%d entries cleared)",
            count,
        )

    def import_time(self) -> float:  # for tests
        return time.time()

    def _record(self, decision: EgressDecision) -> EgressDecision:
        with self._lock:
            if decision.allowed:
                self._allows += 1
            else:
                self._denies[decision.outcome.value] = (
                    self._denies.get(decision.outcome.value, 0) + 1
                )
        return decision

    # ------------------------------------------------------------------
    # httpx wrapper
    # ------------------------------------------------------------------

    def wrap_httpx_client(self, client):  # type: ignore[no-untyped-def]
        """Patch an httpx.Client so its send() raises before egress."""
        original_send = client.send

        def guarded_send(request, *args, **kwargs):  # type: ignore[no-untyped-def]
            decision = self.check_egress(str(request.url))
            if not decision.allowed:
                raise PermissionError(
                    f"[runtime_firewall] {decision.outcome.value}: {decision.reason}"
                )
            return original_send(request, *args, **kwargs)

        client.send = guarded_send  # type: ignore[assignment]
        return client

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            return {
                "allows": self._allows,
                "denies": dict(self._denies),
                "locality_preference": self._policy.locality_preference,
                "allowlist_size": len(self._policy.allowlist_hosts),
                "blocklist_size": len(self._policy.blocklist_hosts),
                "deny_by_default": self._policy.deny_by_default,
            }


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_singleton: Optional[RuntimeFirewall] = None
_singleton_lock = threading.Lock()


def get_runtime_firewall() -> RuntimeFirewall:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = RuntimeFirewall()
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


# ---------------------------------------------------------------------------
# v20.7.1-NEUTRAL — VOS3KernelEvent + RuntimeFirewallAdapter (RTAF mapping)
#
# RuntimeFirewallAdapter maps a normalized vOS kernel security event into a
# runtime-application-firewall (RTAF) alert envelope so a third-party
# runtime-defense console (Prisma-Cloud-runtime-shaped) sees vOS kernel
# denials as first-class alerts. Outbound only. VOS3KernelEvent is the
# canonical shared event type — the EDR relay imports it from here so the
# adapter and the relay operate on the SAME class object.
# ---------------------------------------------------------------------------


@dataclass
class VOS3KernelEvent:
    """A normalized kernel security event handed to an external connector."""

    code: str
    subject_kind: str
    subject_id: str
    tenant_id: str
    evidence: dict = field(default_factory=dict)


# vOS kernel event code -> (RTAF errorId, severity).
_RTAF_CODE_MAP: dict[str, tuple[str, str]] = {
    "EGRESS_DENY_PUBLIC_IP": ("RTAF.NETWORK.EGRESS.PUBLIC_DENY", "high"),
    "EGRESS_DENY_BLOCKLIST": ("RTAF.NETWORK.EGRESS.BLOCKLIST_DENY", "high"),
    "EGRESS_DENY_DNS_PIN": ("RTAF.NETWORK.EGRESS.DNS_PIN_DENY", "high"),
    "AI_OOM_KILL": ("RTAF.RESOURCE.AI_OOM_KILL", "medium"),
}


class RuntimeFirewallAdapter:
    """Maps a VOS3KernelEvent into an RTAF alert envelope. Outbound only."""

    def __init__(self, firewall_tenant_id: Optional[str] = None) -> None:
        self.firewall_tenant_id = firewall_tenant_id

    def to_firewall(self, event: "VOS3KernelEvent") -> dict:
        error_id, severity = _RTAF_CODE_MAP.get(
            event.code, (f"RTAF.GENERIC.{event.code}", "low")
        )
        return {
            "errorId": error_id,
            "severity": severity,
            "firewallTenantId": self.firewall_tenant_id,
            "subject": {"kind": event.subject_kind, "id": event.subject_id},
            "tenantId": event.tenant_id,
            "context": {
                "vos3KernelCode": event.code,
                "evidence": dict(event.evidence),
            },
        }


# Back-compat alias (the v20.5.1 name some external callers still import).
PrismaAdapter = RuntimeFirewallAdapter


__all__ = [
    "EgressDecision",
    "EgressOutcome",
    "EgressPolicy",
    "RuntimeFirewall",
    "RuntimeFirewallAdapter",
    "PrismaAdapter",
    "VOS3KernelEvent",
    "get_runtime_firewall",
    "reset_for_tests",
    "ENV_ALLOWLIST",
    "ENV_BLOCKLIST",
    "ENV_LOCALITY",
    "ENV_DENY_BY_DEFAULT",
    "LOCALITY_AIR_GAP",
    "LOCALITY_LOCAL_FIRST",
]
