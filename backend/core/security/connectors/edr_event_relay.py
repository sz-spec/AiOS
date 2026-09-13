"""
backend/core/security/connectors/edr_event_relay.py
====================================================

Sprint 14.1 — inbound EDR event relay to the compliance audit ring.

Direction
---------

INBOUND. The EDR (Endpoint Detection & Response) platform — Microsoft
Defender for Endpoint, CrowdStrike Falcon, SentinelOne, Wazuh, generic
syslog — observes signals about the host the vOS backend is running on.
This module receives those signals via a webhook endpoint and correlates
them with vOS internal state (current AI slot bindings, active
intent-manifest grants, audit-ring sequence) so the compliance store gets
the *joined* view.

Why join in vOS rather than in the EDR
--------------------------------------

The EDR knows: "process pid=4221 spawned by python made an outbound
connection to 198.51.100.42:443". It does NOT know "that process is
holding AI slot 2 under IntentManifest grant ``mfg-xyz-2026``". The
operator's incident-response runbook wants both. The EDR can't reach into
vOS to fetch the grant info — vOS can reach into the EDR's event stream
and enrich.

API shape
---------

POST /api/compliance/edr/relay  (mounted by api/compliance_routes.py)

Body:
    {
      "edr_provider": "mde" | "crowdstrike" | "sentinelone" | "wazuh" | "generic",
      "event_id": "<provider-specific id>",
      "severity": "low" | "medium" | "high" | "critical",
      "host_id": "<machine identifier>",
      "process": { "pid": int, "image": str, "command_line": str },
      "network": { "remote_ip": str, "remote_port": int, "direction": "out" | "in" },
      "raw_event": { ... }                  # full original event, opaque
    }

The relay:
  1. Validates signature (HMAC-SHA256 over body, key from env).
  2. Enriches with vOS state (active slot, current intent grant).
  3. Writes a compliance_event row with category=VOS3_AUDIT_CAT_EDR_RELAY.
  4. Returns the enriched event id for the EDR's correlation pipeline.

Honest scope ceiling
--------------------

We ship the relay + four provider parsers. The provider parsers are
**minimum viable** — they extract fields documented in vendor public
docs (CrowdStrike Falcon Streaming API, MDE Common alert schema, etc.).
Production deployments should expect to tune the parsers when their
specific EDR config emits slightly different field names. Each parser is
a small standalone function (``_parse_<provider>``); swap point is
isolated.

The HMAC signature scheme is OUR choice (sha256 over canonical-JSON body
with shared secret). It is NOT the EDR's native signing scheme — most
EDRs offer no outbound webhook signing, so we standardize on one shape
the deployer configures in the EDR's webhook receiver settings.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Shared, canonical kernel-event type (defined in runtime_firewall so the
# RTAF adapter and this EDR relay map the SAME class object).
from .runtime_firewall import VOS3KernelEvent  # noqa: E402,F401

# vOS kernel event code -> (Falcon DetectName, Severity int).
# Severity: 1 = informational, 2 = low, 3 = medium, 4 = high.
_EDR_KERNEL_MAP: dict[str, tuple[str, int]] = {
    "SCHED_CORE_SIBLING_INCOMPATIBLE": ("VOS3.SchedCore.SiblingIncompatible", 4),
    "EGRESS_DENY_PUBLIC_IP": ("VOS3.Network.EgressDenyPublicIp", 4),
    "EGRESS_DENY_BLOCKLIST": ("VOS3.Network.EgressDenyBlocklist", 4),
    "AI_OOM_KILL": ("VOS3.Resource.AiOomKill", 3),
    "INTENT_BIND_DENY": ("VOS3.Intent.BindDeny", 3),
}


def _camel(code: str) -> str:
    """SHOUTY_SNAKE -> CamelCase for a Falcon DetectName suffix."""
    return "".join(part.capitalize() for part in code.split("_") if part)


ENV_HMAC_SECRET = "VOS3_EDR_HMAC_SECRET"
ENV_REQUIRE_SIGNATURE = "VOS3_EDR_REQUIRE_SIGNATURE"


# Audit category — must match kernel/include/vos/audit_ring.h enum
VOS3_AUDIT_CAT_EDR_RELAY = 0x40


# ---------------------------------------------------------------------------
# Provider enum + severity normalization
# ---------------------------------------------------------------------------


class EdrProvider(enum.Enum):
    MDE = "mde"
    CROWDSTRIKE = "crowdstrike"
    SENTINELONE = "sentinelone"
    WAZUH = "wazuh"
    GENERIC = "generic"

    @classmethod
    def from_str(cls, value: str) -> "EdrProvider":
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.GENERIC


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _severity_rc(severity: str) -> int:
    """Map provider severity to the compliance_events ``rc`` column."""
    return _SEVERITY_RANK.get(severity.strip().lower(), 0)


# ---------------------------------------------------------------------------
# Normalized event shape (after parser)
# ---------------------------------------------------------------------------


@dataclass
class NormalizedEdrEvent:
    provider: EdrProvider
    event_id: str
    severity: str
    host_id: Optional[str]
    pid: Optional[int]
    image: Optional[str]
    remote_ip: Optional[str]
    remote_port: Optional[int]
    direction: Optional[str]
    raw: dict = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    def digest_prefix(self) -> str:
        """Stable 16-hex-char id of this event (first 8 bytes of SHA-256
        over the normalized fields). Mirrors the kernel audit ring's
        digest_prefix column shape."""
        canon = json.dumps(
            {
                "p": self.provider.value,
                "i": self.event_id,
                "s": self.severity,
                "h": self.host_id,
                "pid": self.pid,
                "img": self.image,
                "rip": self.remote_ip,
                "rp": self.remote_port,
                "d": self.direction,
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canon).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Provider parsers
# ---------------------------------------------------------------------------


def _parse_mde(body: dict) -> NormalizedEdrEvent:
    """Microsoft Defender for Endpoint Common Alert Schema (extract)."""
    return NormalizedEdrEvent(
        provider=EdrProvider.MDE,
        event_id=str(body.get("AlertId") or body.get("event_id") or ""),
        severity=str(body.get("Severity") or body.get("severity") or "low").lower(),
        host_id=str(
            body.get("MachineId") or body.get("DeviceId") or body.get("host_id") or ""
        ),
        pid=_safe_int(
            body.get("InitiatingProcessId") or (body.get("process") or {}).get("pid")
        ),
        image=str(
            body.get("InitiatingProcessFileName")
            or (body.get("process") or {}).get("image")
            or ""
        ),
        remote_ip=str(
            body.get("RemoteIP") or (body.get("network") or {}).get("remote_ip") or ""
        ),
        remote_port=_safe_int(
            body.get("RemotePort") or (body.get("network") or {}).get("remote_port")
        ),
        direction=str(
            body.get("RemoteNetworkDirection")
            or (body.get("network") or {}).get("direction")
            or ""
        ),
        raw=body,
    )


def _parse_crowdstrike(body: dict) -> NormalizedEdrEvent:
    """CrowdStrike Falcon Streaming API event extract."""
    payload = body.get("event") or body
    return NormalizedEdrEvent(
        provider=EdrProvider.CROWDSTRIKE,
        event_id=str(payload.get("DetectId") or payload.get("event_id") or ""),
        severity=_crowdstrike_severity(
            payload.get("Severity") or payload.get("severity")
        ),
        host_id=str(payload.get("Hostname") or payload.get("host_id") or ""),
        pid=_safe_int(
            payload.get("ProcessId") or (payload.get("process") or {}).get("pid")
        ),
        image=str(
            payload.get("FileName") or (payload.get("process") or {}).get("image") or ""
        ),
        remote_ip=str(
            payload.get("RemoteAddress")
            or (payload.get("network") or {}).get("remote_ip")
            or ""
        ),
        remote_port=_safe_int(
            payload.get("RemotePort")
            or (payload.get("network") or {}).get("remote_port")
        ),
        direction=str(
            payload.get("ConnectionDirection")
            or (payload.get("network") or {}).get("direction")
            or ""
        ),
        raw=body,
    )


def _parse_sentinelone(body: dict) -> NormalizedEdrEvent:
    threat = body.get("data") or body
    return NormalizedEdrEvent(
        provider=EdrProvider.SENTINELONE,
        event_id=str(
            threat.get("threatInfo", {}).get("threatId") or threat.get("event_id") or ""
        ),
        severity=str(
            threat.get("threatInfo", {}).get("confidenceLevel")
            or threat.get("severity")
            or "low"
        ).lower(),
        host_id=str(
            threat.get("agentRealtimeInfo", {}).get("agentId")
            or threat.get("host_id")
            or ""
        ),
        pid=_safe_int(
            threat.get("processInfo", {}).get("pid")
            or (threat.get("process") or {}).get("pid")
        ),
        image=str(
            threat.get("processInfo", {}).get("name")
            or (threat.get("process") or {}).get("image")
            or ""
        ),
        remote_ip=str(
            threat.get("networkInfo", {}).get("remoteIp")
            or (threat.get("network") or {}).get("remote_ip")
            or ""
        ),
        remote_port=_safe_int(
            threat.get("networkInfo", {}).get("remotePort")
            or (threat.get("network") or {}).get("remote_port")
        ),
        direction=str(
            threat.get("networkInfo", {}).get("direction")
            or (threat.get("network") or {}).get("direction")
            or ""
        ),
        raw=body,
    )


def _parse_wazuh(body: dict) -> NormalizedEdrEvent:
    return NormalizedEdrEvent(
        provider=EdrProvider.WAZUH,
        event_id=str(body.get("rule", {}).get("id") or body.get("event_id") or ""),
        severity=_wazuh_severity(body.get("rule", {}).get("level")),
        host_id=str(body.get("agent", {}).get("id") or body.get("host_id") or ""),
        pid=_safe_int((body.get("process") or {}).get("pid")),
        image=str((body.get("process") or {}).get("image") or ""),
        remote_ip=str(
            body.get("data", {}).get("srcip")
            or (body.get("network") or {}).get("remote_ip")
            or ""
        ),
        remote_port=_safe_int(
            body.get("data", {}).get("srcport")
            or (body.get("network") or {}).get("remote_port")
        ),
        direction=str((body.get("network") or {}).get("direction") or ""),
        raw=body,
    )


def _parse_generic(body: dict) -> NormalizedEdrEvent:
    process = body.get("process") or {}
    network = body.get("network") or {}
    return NormalizedEdrEvent(
        provider=EdrProvider.GENERIC,
        event_id=str(body.get("event_id") or ""),
        severity=str(body.get("severity") or "low").lower(),
        host_id=str(body.get("host_id") or ""),
        pid=_safe_int(process.get("pid")),
        image=str(process.get("image") or ""),
        remote_ip=str(network.get("remote_ip") or ""),
        remote_port=_safe_int(network.get("remote_port")),
        direction=str(network.get("direction") or ""),
        raw=body,
    )


_PARSERS = {
    EdrProvider.MDE: _parse_mde,
    EdrProvider.CROWDSTRIKE: _parse_crowdstrike,
    EdrProvider.SENTINELONE: _parse_sentinelone,
    EdrProvider.WAZUH: _parse_wazuh,
    EdrProvider.GENERIC: _parse_generic,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _crowdstrike_severity(value: Any) -> str:
    if value is None:
        return "low"
    try:
        v = int(value)
    except (TypeError, ValueError):
        return str(value).lower()
    if v >= 80:
        return "critical"
    if v >= 60:
        return "high"
    if v >= 40:
        return "medium"
    return "low"


def _wazuh_severity(level: Any) -> str:
    v = _safe_int(level) or 0
    if v >= 13:
        return "critical"
    if v >= 10:
        return "high"
    if v >= 6:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_signature(*, raw_body: bytes, header_signature: Optional[str]) -> bool:
    secret = os.environ.get(ENV_HMAC_SECRET, "").strip()
    required = os.environ.get(ENV_REQUIRE_SIGNATURE, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not secret:
        # No secret configured → signature is optional. If required mode
        # is on and no secret is configured, that's a deployment error;
        # we deny.
        return not required
    if not header_signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # Accept either "sha256=<hex>" or raw "<hex>"
    candidate = header_signature.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256=") :]
    return hmac.compare_digest(expected, candidate)


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------


class EdrEventRelay:
    """Stateful relay — normalizes + persists EDR events into compliance store."""

    def __init__(self, customer_id=None, *, store=None) -> None:
        self.customer_id = customer_id
        self._store = store
        self._lock = threading.RLock()
        self._received: int = 0
        self._offset: int = 0
        self._by_provider: dict[str, int] = {}
        self._by_severity: dict[str, int] = {}

    # ------------------------------------------------------------------
    # v20.7.1-NEUTRAL — outbound kernel-event + attestation relay
    # (CrowdStrike-Falcon-streaming-API-shaped). Maps a vOS kernel event
    # or a finalized IntegrityCertificate into a Falcon "detection" /
    # "attestation" event with a monotonic per-relay offset.
    # ------------------------------------------------------------------

    def _next_offset(self) -> int:
        with self._lock:
            offset = self._offset
            self._offset += 1
            return offset

    def relay_kernel(self, event) -> dict:
        """Map a VOS3KernelEvent → a Falcon-shaped AIDetection event."""
        detect_name, severity = _EDR_KERNEL_MAP.get(
            event.code,
            (f"VOS3.Kernel.{_camel(event.code)}", 1),
        )
        return {
            "metadata": {
                "eventType": "AIDetection",
                "offset": self._next_offset(),
                "customerIDString": self.customer_id,
            },
            "event": {
                "DetectName": detect_name,
                "Severity": severity,
                "VOS3KernelCode": event.code,
                "Subject": {"kind": event.subject_kind, "id": event.subject_id},
                "TenantId": event.tenant_id,
                "Evidence": dict(getattr(event, "evidence", {}) or {}),
            },
        }

    def relay_attestation(self, cert) -> dict:
        """Map a finalized IntegrityCertificate → a Falcon AIAttested event."""
        return {
            "metadata": {
                "eventType": "AIAttested",
                "offset": self._next_offset(),
                "customerIDString": self.customer_id,
            },
            "event": {
                "DetectName": "VOS3.Attestation.SessionFinalized",
                "Severity": 1,  # informational
                "Evidence": {
                    "attestationId": getattr(cert, "id", None),
                    "tenantId": getattr(cert, "tenant_id", None),
                    "sessionId": getattr(cert, "session_id", None),
                },
            },
        }

    def relay(self, body: dict) -> dict:
        """Normalize, enrich, persist, return ack envelope.

        body: parsed JSON from the EDR webhook.
        """
        provider = EdrProvider.from_str(str(body.get("edr_provider") or "generic"))
        parser = _PARSERS[provider]
        event = parser(body)

        # Enrich with vOS state if available — slot bindings, intent grants
        enrichment = self._enrich(event)

        # Persist
        persisted = False
        if self._store is not None:
            row = {
                "seq": int(
                    time.time() * 1_000_000
                ),  # synthetic seq; kernel events use kernel seq
                "tick": int(time.time()),
                "category": VOS3_AUDIT_CAT_EDR_RELAY,
                "rc": _severity_rc(event.severity),
                "slot_id": enrichment.get("slot_id", 255),
                "digest_prefix": event.digest_prefix(),
            }
            try:
                self._store.append_events([row])
                persisted = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("[edr_event_relay] persist failed: %s", exc)

        with self._lock:
            self._received += 1
            self._by_provider[provider.value] = (
                self._by_provider.get(provider.value, 0) + 1
            )
            self._by_severity[event.severity] = (
                self._by_severity.get(event.severity, 0) + 1
            )

        return {
            "ack": True,
            "provider": provider.value,
            "event_id": event.event_id,
            "digest_prefix": event.digest_prefix(),
            "persisted": persisted,
            "enrichment": enrichment,
        }

    def _enrich(self, event: NormalizedEdrEvent) -> dict:
        """Best-effort lookup of vOS state matching the event's pid/image.

        Returns dict of fields: slot_id, intent_grant_id, locality_pref."""
        result: dict[str, Any] = {}
        try:
            # Lookup current slot bindings if the slot service is on path.
            from services.policy_override import get_policy_service  # type: ignore

            svc = get_policy_service()
            snapshot = svc.status()
            # If exactly one slot is "active", attribute the EDR event to it.
            # Otherwise we'd be guessing; leave slot_id absent.
            thresholds = getattr(snapshot, "per_slot_thresholds", {}) or {}
            if len(thresholds) == 1:
                result["slot_id"] = next(iter(thresholds.keys()))
        except Exception:  # noqa: BLE001
            pass
        return result

    def stats(self) -> dict:
        with self._lock:
            return {
                "received_total": self._received,
                "by_provider": dict(self._by_provider),
                "by_severity": dict(self._by_severity),
            }


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_singleton: Optional[EdrEventRelay] = None
_singleton_lock = threading.Lock()


def get_edr_relay(store=None) -> EdrEventRelay:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EdrEventRelay(store=store)
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


# Back-compat alias (the v20.5.1 "Falcon"-specific name).
FalconRelay = EdrEventRelay


__all__ = [
    "EdrProvider",
    "EdrEventRelay",
    "FalconRelay",
    "VOS3KernelEvent",
    "NormalizedEdrEvent",
    "VOS3_AUDIT_CAT_EDR_RELAY",
    "verify_signature",
    "get_edr_relay",
    "reset_for_tests",
    "ENV_HMAC_SECRET",
    "ENV_REQUIRE_SIGNATURE",
]
