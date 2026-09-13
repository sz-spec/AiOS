"""
backend/core/security/connectors
================================

Sprint 14.1 — outbound bridges from vOS security state to external
posture-management / detection / enforcement systems.

Modules
-------

  external_spm.py     — AI-SPM / CSPM platform adapter (Wiz, Prisma, Sysdig)
  runtime_firewall.py — egress allowlist enforcement (Z3-proven policy)
  edr_event_relay.py  — MDE / CrowdStrike / SentinelOne signal correlation

Why connectors and not a single "integrations" module
-----------------------------------------------------

The three sinks share zero wire format and zero credential model. Wrapping
them in a single facade would force every site to think about every
platform — i.e., the dependency surface explodes. Each connector ships
standalone, imports nothing from its siblings, and degrades gracefully
when its target endpoint is not configured (no env var → no-op).
"""

from __future__ import annotations

# Re-export the public connector surface so callers can do
# `from core.security.connectors import build_external_spm_aibom` etc.
# VOS3KernelEvent is sourced from runtime_firewall (its canonical home)
# so the RTAF adapter and the EDR relay share the SAME class object.
from .external_spm import (
    build_external_spm_aibom,
    build_wiz_aibom,
    SpmConnector,
    WizConnector,
    get_spm_sink,
    publish_posture,
)
from .runtime_firewall import (
    VOS3KernelEvent,
    RuntimeFirewallAdapter,
    PrismaAdapter,
    RuntimeFirewall,
    get_runtime_firewall,
)
from .edr_event_relay import (
    EdrEventRelay,
    FalconRelay,
    get_edr_relay,
)

__all__: list[str] = [
    # external_spm
    "build_external_spm_aibom",
    "build_wiz_aibom",
    "SpmConnector",
    "WizConnector",
    "get_spm_sink",
    "publish_posture",
    # runtime_firewall
    "VOS3KernelEvent",
    "RuntimeFirewallAdapter",
    "PrismaAdapter",
    "RuntimeFirewall",
    "get_runtime_firewall",
    # edr_event_relay
    "EdrEventRelay",
    "FalconRelay",
    "get_edr_relay",
]
