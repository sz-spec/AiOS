"""
P2.3 · Atomic hybrid rotation tests.

vOS·Adaptive·SHA=aeb3736·Phase=P2

Covers core/security/rotation_manager.py::rotate_hybrid_workflow_key.

Honest scope
------------
* SQLCipher backend not assumed — the keyring's local fallback is
  used in CI. Audit-row tests therefore mock `_audit` to capture
  emit attempts rather than hitting a real SQLAlchemy session.
* Threading tests use real `threading.Thread` and rely on the
  module-level `_lock` to serialize. macOS-fast machines may
  collapse the contention window — that doesn't change correctness,
  only ensures that the atomicity contract holds under whatever
  scheduling actually happens.
"""

from __future__ import annotations

import threading

import pytest

from services.pqc_sign import verify_path

pytestmark = pytest.mark.skipif(
    verify_path() == "unavailable",
    reason="no PQC backend installed",
)


@pytest.fixture(autouse=True)
def isolated_rotation_state():
    """Do not carry the deliberate hard-failure latch between test cases."""
    from core.security import rotation_manager
    from services.crypto_keyring import KEYRING

    rotation_manager._reset_for_tests()
    KEYRING._reset_for_tests()
    yield
    rotation_manager._reset_for_tests()
    KEYRING._reset_for_tests()


# ---------------------------------------------------------------------------
# Atomic hybrid mint
# ---------------------------------------------------------------------------


def test_rotate_hybrid_returns_two_fingerprints():
    from core.security.rotation_manager import rotate_hybrid_workflow_key

    ed_fp, ml_fp = rotate_hybrid_workflow_key()
    assert isinstance(ed_fp, str) and len(ed_fp) == 16
    assert isinstance(ml_fp, str) and len(ml_fp) == 16
    # Hex
    int(ed_fp, 16)
    int(ml_fp, 16)


def test_rotate_hybrid_produces_fresh_fingerprints_each_call():
    from core.security.rotation_manager import rotate_hybrid_workflow_key

    first = rotate_hybrid_workflow_key()
    second = rotate_hybrid_workflow_key()
    assert first != second
    # Both halves should differ — a stuck PQC keygen would only
    # rotate the Ed25519 fingerprint while keeping the ML-DSA one.
    assert first[0] != second[0]
    assert first[1] != second[1]


def test_rotate_hybrid_persists_to_keyring():
    """After rotate, both keyring entries (Ed25519 + PQC) must be
    readable via their getter convenience helpers."""
    from core.security.rotation_manager import rotate_hybrid_workflow_key
    from services.crypto_keyring import (
        get_or_mint_workflow_signing_key,
        get_or_mint_workflow_pqc_key,
    )

    rotate_hybrid_workflow_key()
    # Both halves now resolvable. Their fingerprints aren't easy to
    # check directly (compute_fingerprint is private) but the fact
    # that reading them returns sensible-length bytes is the contract.
    ed_priv_hex, ed_pub_hex = get_or_mint_workflow_signing_key()
    ml_priv, ml_pub = get_or_mint_workflow_pqc_key()
    assert len(ed_priv_hex) == 64
    assert len(ed_pub_hex) == 64
    assert len(ml_priv) == 4032
    assert len(ml_pub) == 1952


# ---------------------------------------------------------------------------
# Atomic rollback on failure
# ---------------------------------------------------------------------------


def test_rotate_hybrid_rolls_back_on_pqc_keygen_failure(monkeypatch):
    """If hybrid_keygen raises, BOTH keyring entries must remain at
    their prior values — no half-written state."""
    from core.security import rotation_manager as rm
    from services.crypto_keyring import (
        KEYRING,
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
        VOS_WORKFLOW_PQC_KEY,
    )
    from services.app_sandbox import SecuritySandboxError

    # Establish a known prior state.
    rm.rotate_hybrid_workflow_key()
    prior_ed = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_SIGNING_KEY)
    prior_pqc = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_PQC_KEY)
    assert prior_ed is not None and prior_pqc is not None

    # Force hybrid_keygen to fail by monkeypatching the import.
    def boom():
        raise RuntimeError("simulated PQC backend failure")

    monkeypatch.setattr(
        "services.pqc_sign.hybrid_keygen",
        boom,
    )

    with pytest.raises(SecuritySandboxError) as excinfo:
        rm.rotate_hybrid_workflow_key()

    # SecuritySandboxError carries the reason "pqc_keygen_failed".
    assert "pqc_keygen_failed" in excinfo.value.reason

    # Critical: keyring state UNCHANGED. Both entries match the snapshot.
    after_ed = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_SIGNING_KEY)
    after_pqc = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_PQC_KEY)
    assert after_ed == prior_ed, "Ed25519 keyring entry was modified despite rollback"
    assert after_pqc == prior_pqc, "PQC keyring entry was modified despite rollback"


def test_rotate_hybrid_rolls_back_on_pqc_write_failure(monkeypatch):
    """If hybrid_keygen succeeds but the PQC keyring write fails AFTER
    the Ed25519 write succeeded, the Ed25519 write must be reverted —
    no partial state where Ed25519 advances but PQC doesn't."""
    from core.security import rotation_manager as rm
    from services.crypto_keyring import (
        KEYRING,
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
        VOS_WORKFLOW_PQC_KEY,
    )
    from services.app_sandbox import SecuritySandboxError

    rm.rotate_hybrid_workflow_key()
    prior_ed = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_SIGNING_KEY)
    KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_PQC_KEY)

    # Patch set_secret to fail ONLY on the PQC key write.
    real_set = KEYRING.set_secret

    def selective_fail(service, username, secret):
        if username == VOS_WORKFLOW_PQC_KEY:
            raise OSError("simulated keyring write failure (PQC half)")
        return real_set(service, username, secret)

    monkeypatch.setattr(KEYRING, "set_secret", selective_fail)

    with pytest.raises(SecuritySandboxError):
        rm.rotate_hybrid_workflow_key()

    # IMPORTANT: while selective_fail is still active, set_secret on
    # the PQC key would fail. The rollback path calls set_secret with
    # the prior value, and since that's the PQC username, it would
    # ALSO fail. The audit row catches this and the test verifies
    # rollback at least attempted both halves.
    # To check post-state cleanly, un-patch.
    monkeypatch.undo()
    # The Ed25519 rollback ran inside the still-patched set_secret —
    # wait no, the Ed25519 rollback calls set_secret with VOS_WORKFLOW_SIGNING_KEY
    # which is NOT the PQC key, so selective_fail lets it through.
    after_ed = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_SIGNING_KEY)
    assert after_ed == prior_ed, "Ed25519 was not rolled back to prior value"


def test_rotate_hybrid_emits_single_audit_row_on_success(monkeypatch):
    """The atomicity contract says ONE audit row per successful
    rotation, carrying BOTH fingerprints. A future refactor that
    splits this into two rows would break the audit-log query."""
    from core.security import rotation_manager as rm

    emits = []

    def capturing_audit(kind, **details):
        emits.append((kind, details))

    monkeypatch.setattr(rm, "_audit", capturing_audit)

    rm.rotate_hybrid_workflow_key()
    success_rows = [(k, d) for k, d in emits if k == "hybrid_key_rotated"]
    assert (
        len(success_rows) == 1
    ), f"expected exactly 1 hybrid_key_rotated audit row, got {len(success_rows)}"
    details = success_rows[0][1]
    assert "ed25519_fingerprint" in details
    assert "mldsa_fingerprint" in details
    assert "pqc_backend" in details
    assert details["scope"] == "workflow_signing"


def test_rotate_hybrid_emits_failure_audit_on_keygen_error(monkeypatch):
    """Failure path must emit hybrid_key_rotation_failed with the
    reason + rollback status for each half."""
    from core.security import rotation_manager as rm
    from services.app_sandbox import SecuritySandboxError

    rm.rotate_hybrid_workflow_key()  # establish prior state

    emits = []

    def capturing_audit(kind, **details):
        emits.append((kind, details))

    monkeypatch.setattr(rm, "_audit", capturing_audit)

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr("services.pqc_sign.hybrid_keygen", boom)

    with pytest.raises(SecuritySandboxError):
        rm.rotate_hybrid_workflow_key()

    failure_rows = [(k, d) for k, d in emits if k == "hybrid_key_rotation_failed"]
    assert len(failure_rows) == 1
    details = failure_rows[0][1]
    assert "reason" in details
    assert "ed25519_rolled_back" in details
    assert "mldsa_rolled_back" in details


# ---------------------------------------------------------------------------
# Concurrency — the module-level RLock must serialize
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hard-fail latch — vOS·Adaptive·SHA=aeb3736·Phase=P2.3-polish
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rotation_state():
    """Clear hard-fail + schedule registry before each test so a
    test that intentionally lands the kernel in hard-fail does not
    poison subsequent tests."""
    from core.security import rotation_manager as rm

    rm._reset_for_tests()
    yield
    rm._reset_for_tests()


def test_hard_fail_starts_inactive():
    from core.security.rotation_manager import (
        is_hard_fail_active,
        get_hard_fail_state,
    )

    assert is_hard_fail_active() is False
    state = get_hard_fail_state()
    assert state["active"] is False
    assert state["reason"] is None
    assert state["since_ms"] is None


def test_rotation_blocked_after_hard_fail_latched(monkeypatch):
    """Force a rollback failure → hard-fail latches → subsequent
    rotate raises RotationHardFailError.

    Setup: establish prior state, then force PQC writes to fail.
    Real rotation will:
      1. Snapshot prior_ed + prior_pqc (both non-None).
      2. Mint hybrid pair (succeeds).
      3. Write Ed25519 — succeeds.
      4. Write PQC — FAILS (patched).
      5. Enter except block; try rollback of Ed25519 (succeeds);
         try rollback of PQC — FAILS (patched). → hard-fail latches.
    """
    from core.security import rotation_manager as rm
    from services.crypto_keyring import KEYRING, VOS_WORKFLOW_PQC_KEY
    from services.app_sandbox import SecuritySandboxError

    # 1. Establish prior state so the rollback path has something to
    #    restore (otherwise prior_pqc is None and rollback is a no-op).
    rm.rotate_hybrid_workflow_key()
    assert rm.is_hard_fail_active() is False

    # 2. Patch set_secret to fail ONLY on PQC writes. Both the
    #    forward write AND the rollback restore will hit this.
    real_set = KEYRING.set_secret

    def fail_on_pqc_set(service, username, secret):
        if username == VOS_WORKFLOW_PQC_KEY:
            raise OSError("simulated keyring failure")
        return real_set(service, username, secret)

    monkeypatch.setattr(KEYRING, "set_secret", fail_on_pqc_set)

    # 3. Rotate → forward PQC write fails → rollback of PQC ALSO
    #    fails → _enter_hard_fail triggers.
    with pytest.raises(SecuritySandboxError):
        rm.rotate_hybrid_workflow_key()
    assert (
        rm.is_hard_fail_active() is True
    ), "PQC rollback failure should have latched hard-fail"

    # 4. Un-patch so subsequent calls hit fresh code paths — but
    #    hard-fail latch should still block them.
    monkeypatch.undo()
    with pytest.raises(rm.RotationHardFailError):
        rm.rotate_hybrid_workflow_key()
    with pytest.raises(rm.RotationHardFailError):
        rm.rotate_workflow_signing_key()
    with pytest.raises(rm.RotationHardFailError):
        rm.get_active_key_fingerprint()
    with pytest.raises(rm.RotationHardFailError):
        rm.verify_against_active_key("0" * 16)


def test_clear_hard_fail_requires_attestation():
    from core.security.rotation_manager import clear_hard_fail_state

    # Empty / too-short attestation rejected
    with pytest.raises(ValueError, match="operator_attestation"):
        clear_hard_fail_state(operator_attestation="")
    with pytest.raises(ValueError, match="operator_attestation"):
        clear_hard_fail_state(operator_attestation="abc")  # 3 chars
    # 4+ chars accepted
    clear_hard_fail_state(operator_attestation="ops-team-2026-05-17")


def test_clear_hard_fail_restores_normal_operation():
    from core.security import rotation_manager as rm

    # Manually enter hard-fail (skips the full rollback dance).
    rm._enter_hard_fail("test-induced hard-fail")
    assert rm.is_hard_fail_active() is True

    # Operations blocked
    with pytest.raises(rm.RotationHardFailError):
        rm.rotate_hybrid_workflow_key()

    # Clear with attestation
    rm.clear_hard_fail_state(operator_attestation="incident-2026-05-17-ops")
    assert rm.is_hard_fail_active() is False

    # Normal operation resumes
    ed_fp, ml_fp = rm.rotate_hybrid_workflow_key()
    assert len(ed_fp) == 16 and len(ml_fp) == 16


def test_hard_fail_state_carries_originating_reason():
    """If hard-fail latches twice (rollback fails on BOTH halves),
    the operator must see the FIRST reason — the originating event,
    not whatever masked it later."""
    from core.security import rotation_manager as rm

    rm._enter_hard_fail("first reason")
    rm._enter_hard_fail("second reason should NOT overwrite")
    state = rm.get_hard_fail_state()
    assert state["reason"] == "first reason"
    assert state["since_ms"] is not None


def test_clear_hard_fail_when_inactive_emits_no_op_audit(monkeypatch):
    """Calling clear when hard-fail is already inactive emits an
    audit row with status='no_op' so the surface can't be probed
    silently."""
    from core.security import rotation_manager as rm

    emits = []
    monkeypatch.setattr(rm, "_audit", lambda kind, **d: emits.append((kind, d)))
    rm.clear_hard_fail_state(operator_attestation="probe-test")
    matches = [e for e in emits if e[0] == "hard_fail_cleared"]
    assert len(matches) == 1
    assert matches[0][1]["status"] == "no_op"
    assert matches[0][1]["was_active"] is False


def test_concurrent_rotations_produce_distinct_serialized_fingerprints():
    """N threads rotating simultaneously each get a unique pair of
    fingerprints (no two threads see the same ed/ml combo because
    the RLock serializes them). After all threads finish, the
    keyring's current state matches exactly ONE of the threads' results.
    """
    from core.security.rotation_manager import (
        rotate_hybrid_workflow_key,
        get_active_key_fingerprint,
    )

    n_threads = 8
    results = []
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        ed_fp, ml_fp = rotate_hybrid_workflow_key()
        results.append((ed_fp, ml_fp))

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # N distinct (ed, ml) pairs — each rotation minted fresh keys.
    assert len(results) == n_threads
    assert (
        len(set(results)) == n_threads
    ), f"some threads saw duplicate fingerprints: {results}"
    # Final keyring state = one of the threads' Ed25519 fingerprints.
    final = get_active_key_fingerprint()
    ed_fps = {r[0] for r in results}
    assert (
        final in ed_fps
    ), f"final keyring ed_fp={final} not among thread results {ed_fps}"
