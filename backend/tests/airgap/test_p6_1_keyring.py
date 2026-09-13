"""
P6.1 — Sovereign Keyring + hardware-backed SQLCipher + signed workflows.

Coverage groups:

  1. SovereignKeyringService roundtrip — set / get / delete on the
     local fallback (always exercised on hosts without `keyring`).

  2. SQLCipher master-key bootstrap — `resolve_compliance_key` pulls
     from the keyring on first call and re-pulls the SAME value on
     subsequent calls (proves persistence).

  3. Resilient fallback path — even when `keyring` is "broken" (we
     pin `VOS3_KEYRING_MODE=local`), the service still works AND a
     high-severity `local_fallback_active` row lands in
     `securityAuditLog`.

  4. Workflow signing & tamper detection — submit-time signature
     covers the immutable run fields; a raw SQL UPDATE on
     `manifest_json` triggers auto-quarantine on the next load.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import update

from core.database.sqlite_setup import (
    SecurityAuditLog,
    WorkflowRun,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    _reset_gate_for_tests,
)
from services.crypto_keyring import (
    KEYRING,
    VOS_DB_MASTER_KEY,
    VOS_KEYRING_SERVICE,
    SovereignKeyringService,
    get_or_mint_db_master_key,
    get_or_mint_workflow_signing_key,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env, tmp_path, monkeypatch):
    # Pin keyring to local fallback + a per-test file so we don't
    # collide with the host's real keychain (or other tests).
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "p6.1-test-seed")
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()


# ---------------------------------------------------------------------------
# (1) Roundtrip — set / get / delete
# ---------------------------------------------------------------------------


def test_set_get_delete_roundtrip(local_db):
    """Test 1 — exact value retention through the keyring API."""
    assert KEYRING.set_secret("svc.test", "alice", "p@ssw0rd!") is True
    assert KEYRING.get_secret("svc.test", "alice") == "p@ssw0rd!"
    assert KEYRING.delete_secret("svc.test", "alice") is True
    assert KEYRING.get_secret("svc.test", "alice") is None


def test_get_unknown_secret_returns_none(local_db):
    assert KEYRING.get_secret("nope.svc", "ghost") is None


def test_overwrite_replaces_value(local_db):
    KEYRING.set_secret("svc.over", "u1", "v1")
    KEYRING.set_secret("svc.over", "u1", "v2")
    assert KEYRING.get_secret("svc.over", "u1") == "v2"


def test_delete_returns_false_for_missing_entry(local_db):
    assert KEYRING.delete_secret("svc.empty", "ghost") is False


def test_validate_rejects_empty_inputs(local_db):
    with pytest.raises(ValueError, match="service_name"):
        KEYRING.set_secret("", "u", "v")
    with pytest.raises(ValueError, match="username"):
        KEYRING.set_secret("svc", "", "v")
    with pytest.raises(ValueError, match="secret"):
        KEYRING.set_secret("svc", "u", 123)  # type: ignore[arg-type]


def test_multiple_services_have_isolated_keyspaces(local_db):
    """Same username under two services must NOT collide."""
    KEYRING.set_secret("svc.a", "shared", "value-a")
    KEYRING.set_secret("svc.b", "shared", "value-b")
    assert KEYRING.get_secret("svc.a", "shared") == "value-a"
    assert KEYRING.get_secret("svc.b", "shared") == "value-b"


def test_backend_label_reflects_local_mode(local_db):
    assert KEYRING.backend_label() == "local_fallback"
    assert KEYRING.is_native() is False


# ---------------------------------------------------------------------------
# (2) SQLCipher bootstrap pulls from the keyring
# ---------------------------------------------------------------------------


def test_db_master_key_is_minted_on_first_call(local_db):
    """Test 2 — keyring read returns None at first; mint + store; the
    second call returns the SAME value."""
    assert KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY) is None
    first = get_or_mint_db_master_key()
    assert isinstance(first, str) and len(first) >= 32
    # Persisted.
    assert KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY) == first
    # Stable across calls.
    again = get_or_mint_db_master_key()
    assert again == first


def test_resolve_compliance_key_uses_keyring(local_db, monkeypatch):
    """`resolve_compliance_key` MUST consult the keyring after the
    env-var path. Strip the env var, then verify it returns the
    same minted key the keyring holds."""
    from core.database.sqlcipher_setup import resolve_compliance_key

    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    KEYRING._reset_for_tests()  # ensure no stale entry

    key1 = resolve_compliance_key()
    assert key1
    # Second call returns the SAME key (proves it's stored, not
    # re-derived each time).
    key2 = resolve_compliance_key()
    assert key1 == key2
    # The keyring now holds it.
    assert KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY) == key1


def test_explicit_env_var_overrides_keyring(local_db, monkeypatch):
    """$VOS3_COMPLIANCE_KEY takes precedence even when the keyring
    has a value (ops escape hatch)."""
    from core.database.sqlcipher_setup import resolve_compliance_key

    KEYRING.set_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY, "from-keyring")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "from-env")
    assert resolve_compliance_key() == "from-env"


# ---------------------------------------------------------------------------
# (3) Resilient fallback path
# ---------------------------------------------------------------------------


def test_local_fallback_emits_high_severity_audit_row(local_db):
    """Test 3 — first operation against a (service, username) tuple
    while in local mode writes a `local_fallback_active` row."""
    KEYRING._reset_for_tests()  # clears the per-process audit memo
    KEYRING.set_secret("svc.fallback", "first-user", "x")

    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_fallback_active",
            )
            .all()
        )
        assert rows, "fallback audit row missing"
        # Details capture the service + username + path.
        details = json.loads(rows[-1].details_json or "{}")
        assert details["service"] == "svc.fallback"
        assert details["username"] == "first-user"
        assert details["op"] == "set"


def test_local_fallback_audit_dedupes_per_key(local_db):
    """Repeated operations against the SAME (service, username)
    don't flood the audit log — only the first one is recorded."""
    KEYRING._reset_for_tests()
    KEYRING.set_secret("svc.dedupe", "u", "v1")
    KEYRING.set_secret("svc.dedupe", "u", "v2")
    KEYRING.get_secret("svc.dedupe", "u")
    KEYRING.delete_secret("svc.dedupe", "u")
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_fallback_active",
            )
            .all()
        )
        # All four ops were against the same key — only ONE row.
        matching = [r for r in rows if "svc.dedupe" in (r.reason or "")]
        assert len(matching) == 1


def test_local_fallback_file_is_encrypted_on_disk(local_db, tmp_path):
    """The fallback file MUST be encrypted — plaintext must not
    appear in the on-disk bytes."""
    secret = "this-is-very-sensitive-payload"
    KEYRING.set_secret("svc.disk", "u1", secret)
    from services.crypto_keyring import _local_storage_path

    blob = _local_storage_path().read_bytes()
    assert (
        secret.encode("utf-8") not in blob
    ), "plaintext secret leaked to disk — encryption broken"


def test_local_fallback_survives_service_recreation(local_db):
    """A new SovereignKeyringService instance can decrypt entries
    written by the singleton — proves the deterministic key
    derivation (no in-memory state required)."""
    KEYRING.set_secret("svc.persist", "u", "value-persisted")
    fresh = SovereignKeyringService()
    assert fresh.get_secret("svc.persist", "u") == "value-persisted"


def test_keyring_seed_isolation(local_db, tmp_path, monkeypatch):
    """Two hosts with different seeds can't read each other's
    files — proves the host-binding (machine-id) guarantee."""
    # Write a secret under seed-A.
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "seed-A")
    monkeypatch.setenv(
        "VOS3_KEYRING_PATH",
        str(tmp_path / "shared.enc"),
    )
    KEYRING._reset_for_tests()
    KEYRING.set_secret("svc.iso", "u", "from-seed-A")

    # Re-open with seed-B against the SAME file → can't decrypt.
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "seed-B")
    KEYRING._reset_for_tests()
    # Decrypt fails → the service treats the file as empty.
    assert KEYRING.get_secret("svc.iso", "u") is None


# ---------------------------------------------------------------------------
# (4) Workflow signing + tamper detection
# ---------------------------------------------------------------------------


def _install_app(scopes, workspace_id):
    res = SANDBOX_MANAGER.install(
        {"name": "p61", "version": "1.0", "scopes": list(scopes)},
        workspace_id=workspace_id,
    )
    return res["app_id"]


def test_workflow_signing_keypair_is_minted_and_stable(local_db):
    """The signing keypair is minted on first call and re-used
    across subsequent calls."""
    priv1, pub1 = get_or_mint_workflow_signing_key()
    assert len(priv1) == 64 and len(pub1) == 64
    priv2, pub2 = get_or_mint_workflow_signing_key()
    assert (priv1, pub1) == (priv2, pub2)


def test_submit_workflow_persists_signature(local_db):
    """Every fresh workflow row carries signature + public_key
    columns populated by the orchestrator."""
    from services.agent_orchestrator import ORCHESTRATOR

    app_id = _install_app(["process.execute"], workspace_id="ws-sign")
    handle = ORCHESTRATOR.submit_workflow(
        {
            "workspace_id": "ws-sign",
            "steps": [{"id": "x", "app_id": app_id, "depends_on": []}],
        }
    )
    with get_session() as session:
        run = session.query(WorkflowRun).filter_by(id=handle.run_id).one()
        assert run.signature, "submit must populate signature"
        assert run.signing_public_key, "submit must populate public key"
        # Signature is 64-byte Ed25519 → 128 hex chars.
        assert len(run.signature) == 128
        assert len(run.signing_public_key) == 64


def test_tampered_workflow_auto_quarantines_on_load(local_db):
    """Directive's Test 3-of-spirit (for workflows): raw SQL UPDATE
    on `manifest_json` breaks the signature → next `run_status` /
    `execute_workflow` quarantines the run + records audit."""
    from services.agent_orchestrator import (
        ORCHESTRATOR,
    )

    app_id = _install_app(["process.execute"], workspace_id="ws-tamper")
    legit_manifest = {
        "workspace_id": "ws-tamper",
        "steps": [{"id": "x", "app_id": app_id, "depends_on": []}],
    }
    handle = ORCHESTRATOR.submit_workflow(legit_manifest)

    # Penetration: raw UPDATE substituting a different manifest.
    tampered_manifest = {
        "workspace_id": "ws-tamper",
        "steps": [
            # Attacker swaps in an extra "exfiltrate" step.
            {"id": "x", "app_id": app_id, "depends_on": []},
            {"id": "exfiltrate", "app_id": app_id, "depends_on": ["x"]},
        ],
    }
    with get_session() as session:
        session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == handle.run_id)
            .values(
                manifest_json=json.dumps(
                    tampered_manifest,
                    separators=(",", ":"),
                )
            )
        )
        session.commit()

    # run_status auto-quarantines on the very next call.
    status = ORCHESTRATOR.run_status(handle.run_id)
    assert status["status"] == "failed"
    assert "signature mismatch" in (status.get("error") or "").lower()

    # An audit row is recorded.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="workflow_tampered",
            )
            .all()
        )
        assert rows


def test_tampered_workflow_execute_short_circuits(local_db):
    """Once tampered, execute_workflow MUST return the failed
    status without spawning any step subprocess."""
    from services.agent_orchestrator import ORCHESTRATOR

    app_id = _install_app(["process.execute"], workspace_id="ws-exec-tamper")
    handle = ORCHESTRATOR.submit_workflow(
        {
            "workspace_id": "ws-exec-tamper",
            "steps": [{"id": "x", "app_id": app_id, "depends_on": []}],
        }
    )
    with get_session() as session:
        session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == handle.run_id)
            .values(workspaceId="ws-OTHER-ATTACKER")
        )
        session.commit()

    final = asyncio.run(ORCHESTRATOR.execute_workflow(handle.run_id))
    assert final["status"] == "failed"
    # No step ever progressed past pending.
    assert all(s["status"] == "pending" for s in final["steps"])


def test_unsigned_legacy_row_still_runs(local_db):
    """Backward compatibility — a row with signature=NULL (legacy
    pre-P6.1 install) verifies as "unsigned" and is allowed to
    execute. New rows are always signed; this branch only matters
    for migration from an older DB."""
    from services.agent_orchestrator import ORCHESTRATOR

    app_id = _install_app(["process.execute"], workspace_id="ws-legacy")
    handle = ORCHESTRATOR.submit_workflow(
        {
            "workspace_id": "ws-legacy",
            "steps": [{"id": "x", "app_id": app_id, "depends_on": []}],
        }
    )
    # Strip the signature to simulate a pre-P6.1 row.
    with get_session() as session:
        session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == handle.run_id)
            .values(signature=None, signing_public_key=None)
        )
        session.commit()

    status = ORCHESTRATOR.run_status(handle.run_id)
    # No auto-quarantine — unsigned rows are permitted.
    assert status["status"] == "pending"
