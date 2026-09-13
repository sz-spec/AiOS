"""
services/rotation_manager.py — re-export shim.

The canonical implementation lives at
``core.security.rotation_manager`` (matches CLAUDE.md's documented
Stage-10 path). This module re-exports the public surface so callers
that follow the ``services.*`` convention have a stable import path.
"""

from core.security.rotation_manager import (
    get_active_key_fingerprint,
    get_schedule_registry,
    rotate_db_master_key,
    rotate_key,
    rotate_workflow_signing_key,
    schedule_rotation,
    verify_against_active_key,
)

__all__ = [
    "get_active_key_fingerprint",
    "verify_against_active_key",
    "rotate_key",
    "rotate_workflow_signing_key",
    "rotate_db_master_key",
    "schedule_rotation",
    "get_schedule_registry",
]
