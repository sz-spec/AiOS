"""
backend/services/lockdown.py — Phase 38 (Gap G15)
==================================================

Audit-readiness lockdown policy: enforce that no non-essential debug / memory-dump
endpoints are exposed.

Honest scope
============

A code audit found NO ``/debug/dump_taints`` / ``/dev/mem`` / dump endpoints in
the FastAPI surface — so "disable debug endpoints" is implemented as an absence
GUARD rather than fabricated removal: ``scan_forbidden_routes`` enumerates the
live app and returns any route matching a forbidden debug pattern, and
``assert_audit_ready`` raises if any are present. This both proves the current
surface is clean and fails CI if a debug/dump endpoint is ever added. When
``VOS3_AUDIT_LOCKDOWN`` is set, the app-factory calls ``assert_audit_ready`` at
startup so a forbidden endpoint hard-fails boot.
"""

from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger("vos3.security.lockdown")

# Path substrings that must NOT appear in a mounted route under audit lockdown.
FORBIDDEN_DEBUG_PATTERNS = (
    "/debug",
    "dump_taints",
    "/dev/mem",
    "/devmem",
    "/dump",
    "/__debug",
)


def audit_lockdown_enabled() -> bool:
    """True iff the operator opted the audit-readiness lockdown ON."""
    return os.environ.get("VOS3_AUDIT_LOCKDOWN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def scan_forbidden_routes(app) -> List[str]:
    """Return the paths of any mounted routes matching a forbidden debug pattern."""
    offenders: List[str] = []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "") or ""
        low = path.lower()
        if any(pat in low for pat in FORBIDDEN_DEBUG_PATTERNS):
            offenders.append(path)
    return offenders


class DebugEndpointExposed(RuntimeError):
    """Raised when a forbidden debug/dump endpoint is mounted under lockdown."""


def assert_audit_ready(app) -> None:
    """Fail-closed audit-readiness check: raise if any forbidden debug/dump
    endpoint is mounted. Safe to call at startup (no-op when the surface is
    clean)."""
    offenders = scan_forbidden_routes(app)
    if offenders:
        logger.critical(
            "[SECURITY_CRITICAL][lockdown] forbidden debug endpoint(s) exposed: %s",
            offenders,
        )
        raise DebugEndpointExposed(f"forbidden debug endpoints mounted: {offenders}")
    logger.info("[lockdown] audit-ready: no forbidden debug/dump endpoints mounted")


__all__ = [
    "FORBIDDEN_DEBUG_PATTERNS",
    "audit_lockdown_enabled",
    "scan_forbidden_routes",
    "assert_audit_ready",
    "DebugEndpointExposed",
]
