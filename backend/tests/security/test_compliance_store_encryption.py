"""
Verifies that VOS_PROFILE=fortress + VOS3_COMPLIANCE_KEY produces a
SQLCipher-encrypted compliance store. The load-bearing assertion is the
raw-byte read: known plaintext (a deterministic event payload) MUST
NOT appear in the .db file when decryption keys are stripped.

Skipped when sqlcipher3 is not installed (e.g. on a dev workstation
that hasn't run `pip install sqlcipher3-binary==0.5.4` yet).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

sqlcipher3 = pytest.importorskip("sqlcipher3")


@pytest.fixture(autouse=True)
def fortress_env(monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "test-key-do-not-reuse")
    # Reload vos_profile so the fortress check sees the test env.
    import vos_profile

    vos_profile.reload_for_test()


def _known_plaintext_event():
    return {
        "seq": 1,
        "tick": 12345,
        "category": 7,
        "rc": -1,
        "slot_id": 0,
        "digest_prefix": "deadbeefcafebabe",
    }


def test_fortress_db_is_encrypted_at_rest(tmp_path):
    from services.compliance_store import ComplianceStore

    db = tmp_path / "compliance.db"
    store = ComplianceStore(db_path=str(db))
    store.append_events([_known_plaintext_event()])
    # Force flush.
    store._conn.execute("PRAGMA wal_checkpoint(FULL);")  # type: ignore[attr-defined]

    assert db.exists(), "DB file should be created"
    raw = db.read_bytes()

    # Known plaintext from the inserted row must NOT appear in the
    # encrypted file. SQLCipher v4 ciphertext leaks none of these.
    assert (
        b"deadbeefcafebabe" not in raw
    ), "Plain digest_prefix found in encrypted DB — SQLCipher not active"


def test_wrong_key_cannot_open(tmp_path, monkeypatch):
    from services.compliance_store import ComplianceStore

    db = tmp_path / "compliance.db"
    store = ComplianceStore(db_path=str(db))
    store.append_events([_known_plaintext_event()])
    store._conn.close()  # type: ignore[attr-defined]

    # Open with a different key.
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "wrong-key")
    import vos_profile

    vos_profile.reload_for_test()
    from services.compliance_store import _open_connection

    bad = _open_connection(str(db))
    with pytest.raises(sqlcipher3.DatabaseError):
        bad.execute("SELECT count(*) FROM compliance_events;").fetchone()


def test_correct_key_can_open(tmp_path):
    from services.compliance_store import ComplianceStore

    db = tmp_path / "compliance.db"
    store = ComplianceStore(db_path=str(db))
    store.append_events([_known_plaintext_event()])
    n = store._conn.execute(  # type: ignore[attr-defined]
        "SELECT count(*) FROM compliance_events;"
    ).fetchone()[0]
    assert n == 1
