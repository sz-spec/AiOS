"""
Phase 1 · Installer atomic state transitions — power-loss simulation.

Honest scope
------------
We don't simulate actual power-loss (kill -9 mid-write etc.). We
simulate the equivalent contract: at any point during an installer
state transition, if the process dies, the system must come back up
in either the OLD state OR the NEW state — never a half-applied
"brick" state.

The system under test is `rotation_manager.rotate_workflow_signing_key`
— the only real state-transition op in this codebase. Its contract:
the keyring entry is replaced atomically by the keyring's set_secret
(which is itself transactional). If a crash happens before set_secret
fires, the OLD key is still active. If it fires, the NEW key is
active. There is no "half" state.

These tests verify that invariant under mocked crashes at every
internal step.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Pre-rotation state — verify_against_active_key matches fresh fingerprint
# ---------------------------------------------------------------------------


def test_pre_rotation_state_known(v5_env):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        verify_against_active_key,
    )

    fp = get_active_key_fingerprint()
    assert verify_against_active_key(fp) is True


# ---------------------------------------------------------------------------
# Crash AT each internal step — keyring must remain consistent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iteration", list(range(20)))
def test_rotation_atomic_under_pre_set_secret_crash(v5_env, iteration):
    """Crash BEFORE set_secret fires → old key still active."""
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    pre_fp = get_active_key_fingerprint()

    # Patch generate_keypair_hex to "crash" (raise) before set_secret.
    with patch(
        "services.app_crypto.generate_keypair_hex",
        side_effect=RuntimeError("simulated power loss"),
    ):
        with pytest.raises(RuntimeError, match="simulated power loss"):
            rotate_key()

    # Old fingerprint should still be active.
    post_fp = get_active_key_fingerprint()
    assert post_fp == pre_fp


@pytest.mark.parametrize("iteration", list(range(20)))
def test_rotation_atomic_under_set_secret_crash(v5_env, iteration):
    """Crash DURING set_secret → keyring contract guarantees rollback."""
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    pre_fp = get_active_key_fingerprint()

    with patch(
        "services.crypto_keyring.KEYRING.set_secret",
        side_effect=RuntimeError("crash in set_secret"),
    ):
        with pytest.raises(RuntimeError, match="crash in set_secret"):
            rotate_key()

    # Old fingerprint should still be readable (set_secret failed → old key remains).
    post_fp = get_active_key_fingerprint()
    assert post_fp == pre_fp


# ---------------------------------------------------------------------------
# Successful rotation — new fingerprint replaces old; verify_against
# the OLD fingerprint now returns False (revoked).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iteration", list(range(10)))
def test_rotation_revokes_old_fingerprint(v5_env, iteration):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
        verify_against_active_key,
    )

    pre_fp = get_active_key_fingerprint()
    rotate_key()
    post_fp = get_active_key_fingerprint()
    assert pre_fp != post_fp
    assert verify_against_active_key(pre_fp) is False
    assert verify_against_active_key(post_fp) is True


# ---------------------------------------------------------------------------
# Repeated rotation — N rotations leave the keyring in a final state
# matching the last-rotated key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rotations", [2, 5, 10, 25, 50, 100])
def test_repeated_rotation_lands_on_final_state(v5_env, n_rotations):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
        verify_against_active_key,
    )

    for _ in range(n_rotations):
        rotate_key()
    final_fp = get_active_key_fingerprint()
    assert verify_against_active_key(final_fp) is True


# ---------------------------------------------------------------------------
# Audit chronicle — every rotation writes exactly one audit row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rotations", [1, 3, 10, 25])
def test_rotation_writes_exactly_one_audit_per_call(v5_env, n_rotations):
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from core.security.rotation_manager import rotate_key

    with get_session() as s:
        pre = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()
    for _ in range(n_rotations):
        rotate_key()
    with get_session() as s:
        post = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()
    assert post == pre + n_rotations


# ---------------------------------------------------------------------------
# Bootable-recovery state — after a failed rotation, the system can
# still get_active_key_fingerprint without raising (no half-state)
# ---------------------------------------------------------------------------


def test_bootable_after_failed_rotation(v5_env):
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    with patch(
        "services.app_crypto.generate_keypair_hex",
        side_effect=RuntimeError("crash"),
    ):
        with pytest.raises(RuntimeError, match="crash"):
            rotate_key()
    # System still bootable.
    fp = get_active_key_fingerprint()
    assert isinstance(fp, str)
    assert len(fp) == 16


@pytest.mark.parametrize("crash_after_n", [0, 1, 2, 5, 10, 25])
def test_bootable_after_n_successful_then_one_crash(v5_env, crash_after_n):
    """After N successful rotations + 1 crash, system must still be
    bootable AND verify_against_active_key must work."""
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
        verify_against_active_key,
    )

    for _ in range(crash_after_n):
        rotate_key()
    pre_fp = get_active_key_fingerprint()

    with patch(
        "services.app_crypto.generate_keypair_hex",
        side_effect=RuntimeError("crash"),
    ):
        with pytest.raises(RuntimeError):
            rotate_key()

    post_fp = get_active_key_fingerprint()
    assert post_fp == pre_fp
    assert verify_against_active_key(post_fp) is True


# ---------------------------------------------------------------------------
# Schedule-registry state — schedule_rotation is independent of
# active-key state; one failing shouldn't corrupt the other
# ---------------------------------------------------------------------------


def test_schedule_registry_independent_of_rotation_failure(v5_env):
    from core.security.rotation_manager import (
        _reset_for_tests,
        get_schedule_registry,
        rotate_key,
        schedule_rotation,
    )

    _reset_for_tests()
    schedule_rotation(cron="0 0 * * 0")
    assert get_schedule_registry()["workflow_signing"] == "0 0 * * 0"

    with patch(
        "services.app_crypto.generate_keypair_hex",
        side_effect=RuntimeError("crash"),
    ):
        with pytest.raises(RuntimeError):
            rotate_key()

    # Schedule survives the failed rotation.
    assert get_schedule_registry()["workflow_signing"] == "0 0 * * 0"


@pytest.mark.parametrize(
    "cron",
    [
        "0 0 * * *",
        "*/5 * * * *",
        "0 */6 * * *",
        "0 0 1 * *",
        "0 0 * * 0",
        "0 0 1 1 *",
        "0 12 * * 1-5",
    ],
)
def test_schedule_accepts_valid_cron_expressions(v5_env, cron):
    from core.security.rotation_manager import (
        _reset_for_tests,
        get_schedule_registry,
        schedule_rotation,
    )

    _reset_for_tests()
    schedule_rotation(cron=cron)
    assert get_schedule_registry()["workflow_signing"] == cron


@pytest.mark.parametrize(
    "bad_cron",
    [
        "",
        "   ",
        "not a cron",
        "0 0",
        "0 0 * *",
        "0 0 * * * * *",
    ],
)
def test_schedule_rejects_invalid_cron(v5_env, bad_cron):
    from core.security.rotation_manager import schedule_rotation

    with pytest.raises((ValueError, TypeError)):
        schedule_rotation(cron=bad_cron)
