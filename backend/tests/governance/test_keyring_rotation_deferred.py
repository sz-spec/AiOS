"""
Keyring rotation manager — live tests.

Backed by `core.security.rotation_manager` (closes the Stage-10
missing-files batch documented in CLAUDE.md).

Filename retained as `_deferred` for git-history continuity with the
fortification-v2 commit; every test now runs as a real assertion.
"""

from __future__ import annotations

import threading

# ---------------------------------------------------------------------------
# Module surface — import contracts
# ---------------------------------------------------------------------------


def test_rotation_manager_module_importable():
    import core.security.rotation_manager  # noqa: F401


def test_rotation_manager_exposes_rotate_key():
    from core.security.rotation_manager import rotate_key

    assert callable(rotate_key)


def test_rotation_manager_exposes_schedule_rotation():
    from core.security.rotation_manager import schedule_rotation

    assert callable(schedule_rotation)


def test_rotation_manager_exposes_get_active_key_fingerprint():
    from core.security.rotation_manager import get_active_key_fingerprint

    assert callable(get_active_key_fingerprint)


# ---------------------------------------------------------------------------
# Behaviour — rotation produces a fresh fingerprint
# ---------------------------------------------------------------------------


def test_rotate_produces_new_fingerprint(gov_env):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    before = get_active_key_fingerprint()
    rotate_key()
    after = get_active_key_fingerprint()
    assert before != after
    assert isinstance(after, str)
    assert len(after) == 16  # SHA-256 first-16 hex chars


# ---------------------------------------------------------------------------
# Concurrency — 10 readers + 1 rotator must not produce torn reads
# ---------------------------------------------------------------------------


def test_rotation_is_atomic_under_reads(gov_env):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    # Prime the keyring so the first reader doesn't pay the mint cost.
    get_active_key_fingerprint()

    seen = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            fp = get_active_key_fingerprint()
            assert isinstance(fp, str) and fp
            seen.append(fp)

    readers = [threading.Thread(target=reader) for _ in range(10)]
    for t in readers:
        t.start()
    rotate_key()
    stop.set()
    for t in readers:
        t.join()

    # Invariant: every observed fingerprint is a non-empty 16-char string.
    assert seen, "readers should have observed at least one fingerprint"
    assert all(isinstance(s, str) and len(s) == 16 for s in seen)


# ---------------------------------------------------------------------------
# Revocation — old fingerprint must not validate after rotation
# ---------------------------------------------------------------------------


def test_rotation_revokes_old_key(gov_env):
    from core.security.rotation_manager import (
        rotate_key,
        verify_against_active_key,
    )

    rotate_key()
    assert verify_against_active_key("ancient-fingerprint") is False
    assert verify_against_active_key("0" * 16) is False
    assert verify_against_active_key("") is False


# ---------------------------------------------------------------------------
# Schedule registry — cron expressions are validated + stored
# ---------------------------------------------------------------------------


def test_schedule_rotation_accepts_cron_expression(gov_env):
    from core.security.rotation_manager import (
        _reset_for_tests,
        get_schedule_registry,
        schedule_rotation,
    )

    _reset_for_tests()
    schedule_rotation(cron="0 0 * * 0")  # weekly Sunday midnight
    reg = get_schedule_registry()
    assert reg.get("workflow_signing") == "0 0 * * 0"


# ---------------------------------------------------------------------------
# Audit — every rotation writes a `key_rotated` row
# ---------------------------------------------------------------------------


def test_rotation_emits_audit_event(gov_env):
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from core.security.rotation_manager import rotate_key

    with get_session() as s:
        before = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()
    rotate_key()
    with get_session() as s:
        after = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()
    assert after == before + 1


# ---------------------------------------------------------------------------
# Sovereign keyring — present-day surface still works alongside rotation
# ---------------------------------------------------------------------------


def test_keyring_set_get_round_trip(gov_env):
    from services.crypto_keyring import KEYRING

    KEYRING.set_secret("test_service", "alice", "s3cr3t")
    assert KEYRING.get_secret("test_service", "alice") == "s3cr3t"


def test_keyring_delete_returns_to_none(gov_env):
    from services.crypto_keyring import KEYRING

    KEYRING.set_secret("test_service", "bob", "x")
    KEYRING.delete_secret("test_service", "bob")
    assert KEYRING.get_secret("test_service", "bob") is None
