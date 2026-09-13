"""
v20.7.1-NEUTRAL — Runtime AI-firewall adapter (L7-gateway interop).

Standardises VOS-Cyber kernel rejection signals to L7-AI-firewall-
compatible Error-IDs so an enterprise NGFW with AI-runtime guardrails
can ingest our policy decisions and surface them in the customer's
security graph alongside its own gateway telemetry.

Honest scope statement
======================

We have NOT signed an interop agreement with any specific NGFW
vendor. This adapter implements our side of the handshake against
the publicly documented AI-runtime-firewall event-ingest schema
patterns observed across Tier-1 enterprise NGFW vendors in 2026 (per
their public OpenAPI specs and accompanying schema docs). If a
vendor's ingest schema evolves we update this file; everything else
is unaffected.

The Error-ID mapping below is **our proposal** for harmonising
VOS-Cyber's kernel rejection codes with industry AI-runtime-event
taxonomy. Until a partner ratifies the mapping, treat the IDs as
canonical inside VOS-Cyber and aliases when shipped to a partner.

Industry-standard event envelope
================================

Per the public AI-runtime-firewall ingest patterns the event
endpoint accepts:

    {
      "vendor": "<source vendor>",
      "vendorVersion": "<source version>",
      "eventId": "<UUID>",
      "eventTime": "<ISO-8601>",
      "tenantId": "<vendor-side tenant>",
      "errorId": "<vendor-namespaced ID>",   # e.g. "RTAF.AGENT.POLICY.DENY"
      "severity": "info|low|medium|high|critical",
      "subject": { "kind": "agent|model|session", "id": "<id>" },
      "context": { ... vendor-specific evidence object ... }
    }

Our adapter preserves the rich kernel-side rejection evidence in the
``context`` field while translating the kernel error code into the
runtime-firewall-namespaced ``errorId``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

__all__ = ["RuntimeFirewallAdapter", "ERROR_ID_MAP", "VOS3KernelEvent"]


# ---------------------------------------------------------------------------
# Error-ID translation table
#
# Left  side: VOS-Cyber kernel rejection codes (positive = accept,
#             negative error codes per `kernel/include/vos/tee.h` etc.).
#             Where multiple kernel codes map to one bucket, the bucket
#             label captures the firewall-side intent.
# Right side: Runtime AI-Firewall (RTAF) namespaced Error-ID. The RTAF
#             prefix is a neutral placeholder for the vendor's own
#             namespace (e.g., a partner-ratified extension would
#             rename it RTAF → <vendor>.PARTNER.VOS3.*) without
#             touching call sites.
# ---------------------------------------------------------------------------

ERROR_ID_MAP: Dict[str, str] = {
    # Intent-manifest schema rejections (kernel/src/mm/intent_validator.c)
    "VOS3_INTENT_E_BAD_MAGIC":           "RTAF.AGENT.MANIFEST.MALFORMED",
    "VOS3_INTENT_E_BAD_VERSION":         "RTAF.AGENT.MANIFEST.UNSUPPORTED",
    "VOS3_INTENT_E_TOO_LONG":            "RTAF.AGENT.MANIFEST.OVERSIZE",
    "VOS3_INTENT_E_TOO_SHORT":           "RTAF.AGENT.MANIFEST.MALFORMED",
    "VOS3_INTENT_E_BAD_UTF8":            "RTAF.AGENT.MANIFEST.MALFORMED",
    "VOS3_INTENT_E_TOO_MANY_MODELS":     "RTAF.AGENT.POLICY.QUOTA",
    "VOS3_INTENT_E_TOO_MANY_TOOLS":      "RTAF.AGENT.POLICY.QUOTA",
    "VOS3_INTENT_E_TOO_MANY_ROLES":      "RTAF.AGENT.POLICY.QUOTA",
    "VOS3_INTENT_E_FIELD_TRUNCATED":    "RTAF.AGENT.MANIFEST.MALFORMED",

    # SCHED_CORE cookie isolation (kernel/src/sched/core_cookie.c)
    "SCHED_CORE_SIBLING_INCOMPATIBLE":   "RTAF.AGENT.ISOLATION.SMT_DENY",

    # TEE / RTMR rejections (kernel/src/mm/tee.c)
    "VOS3_TEE_ENOTSUP":                  "RTAF.PLATFORM.ATTESTATION.UNAVAILABLE",
    "VOS3_TEE_EINVAL":                   "RTAF.PLATFORM.ATTESTATION.INVALID",
    "VOS3_TEE_ETDCALL":                  "RTAF.PLATFORM.ATTESTATION.HARDWARE_ERROR",

    # Egress-policy rejections (kernel/src/net/egress_policy.c)
    "EGRESS_DENY_BLOCKLIST":             "RTAF.NETWORK.EGRESS.BLOCKLIST",
    "EGRESS_DENY_PUBLIC_IP":             "RTAF.NETWORK.EGRESS.PUBLIC_DENY",
    "EGRESS_DENY_NO_RULE":               "RTAF.NETWORK.EGRESS.NO_RULE",

    # OOM guard
    "AI_OOM_GUARD_REJECT":               "RTAF.PLATFORM.RESOURCE.OOM",

    # Catch-all (must be present so the adapter never raises on unmapped)
    "_UNKNOWN":                          "RTAF.GENERIC.POLICY.DENY",
}


# Severity heuristic — leftmost match wins.
_SEVERITY_BY_PREFIX: list[tuple[str, str]] = [
    ("RTAF.PLATFORM.ATTESTATION.HARDWARE_ERROR", "critical"),
    ("RTAF.AGENT.ISOLATION",                     "high"),
    ("RTAF.NETWORK.EGRESS",                      "high"),
    ("RTAF.PLATFORM.RESOURCE.OOM",               "high"),
    ("RTAF.AGENT.MANIFEST",                      "medium"),
    ("RTAF.AGENT.POLICY.QUOTA",                  "medium"),
    ("RTAF.PLATFORM.ATTESTATION",                "medium"),
    ("RTAF.GENERIC",                             "low"),
]


def _severity_for(error_id: str) -> str:
    for prefix, sev in _SEVERITY_BY_PREFIX:
        if error_id.startswith(prefix):
            return sev
    return "low"


@dataclass
class VOS3KernelEvent:
    """Canonical VOS-Cyber rejection event before vendor translation."""
    code:       str                     # one of ERROR_ID_MAP's keys
    subject_kind: str                   # "agent" | "model" | "session"
    subject_id: str                     # opaque id
    tenant_id:  str
    evidence:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeFirewallAdapter:
    """VOS-Cyber → Runtime-AI-Firewall event adapter.

    Stateless transformation; safe to construct per-emit or once
    per process. Caller handles transport (HTTPS POST to the
    firewall vendor's ingest URL with the customer's API key in the
    auth header).
    """
    vendor: str = "VOS-Cyber"
    vendor_version: str = "20.7.1"
    firewall_tenant_id: str = ""

    def to_firewall(self, event: VOS3KernelEvent) -> Dict[str, Any]:
        error_id = ERROR_ID_MAP.get(event.code, ERROR_ID_MAP["_UNKNOWN"])
        return {
            "vendor":         self.vendor,
            "vendorVersion":  self.vendor_version,
            "eventId":        str(uuid.uuid4()),
            "eventTime":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tenantId":       self.firewall_tenant_id or event.tenant_id,
            "errorId":        error_id,
            "severity":       _severity_for(error_id),
            "subject": {
                "kind": event.subject_kind,
                "id":   event.subject_id,
            },
            "context": {
                "vos3KernelCode": event.code,
                "vos3TenantId":   event.tenant_id,
                "evidence":       dict(event.evidence),
            },
        }

    def to_firewall_batch(self, events) -> Dict[str, Any]:
        """Industry pattern: batch posts via {"events": [...]}."""
        return {
            "events": [self.to_firewall(e) for e in events],
        }
