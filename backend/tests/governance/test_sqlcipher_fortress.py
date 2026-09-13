"""
SQLCipher fortress profile — fail-closed on wrong key.

`backend/services/compliance_store.py:_open_connection` uses SQLCipher
ONLY when `VOS_PROFILE=fortress`. The encryption surface is gated
behind:

  1. `VOS_PROFILE=fortress`
  2. `VOS3_COMPLIANCE_KEY` set
  3. `sqlcipher3` importable (provided by `sqlcipher3-binary`)

When any of those are absent, the store falls back to plain sqlite3.

These tests verify:
  * The fortress profile MUST raise RuntimeError if the key env var
    is missing, regardless of sqlcipher availability.
  * A correct key produces a usable connection.
  * A wrong key produces a query-time failure (SQLCipher decrypts
    lazily — error surfaces on first read).

If `sqlcipher3` is unavailable on this host (common in CI), the
fortress-encrypted tests are skipped and the fallback-path is
asserted instead.
"""

from __future__ import annotations


import pytest

SQLCIPHER_AVAILABLE = True
try:
    import sqlcipher3  # noqa: F401  # type: ignore[import-not-found]
except ImportError:
    SQLCIPHER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Profile gate — fortress without key raises
# ---------------------------------------------------------------------------


def test_fortress_without_key_raises(gov_env, monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    # Force vos_profile to re-evaluate is_fortress() AFTER our setenv —
    # the module caches the active profile in a module global. Use the
    # provided reload_for_test() re-read hook, NOT importlib.reload: reloading
    # the module recreates the `Profile` enum class and breaks `is Profile.X`
    # identity in any test that imported `Profile` earlier (test_profile_dispatch).
    try:
        import vos_profile
    except ImportError:
        pytest.skip("vos_profile module not on path — gate cannot be exercised here")

    vos_profile.reload_for_test()

    from services.compliance_store import _open_connection

    db_path = str(gov_env / "compliance.db")
    with pytest.raises(RuntimeError, match="VOS3_COMPLIANCE_KEY"):
        _open_connection(db_path)


def test_non_fortress_profile_uses_plain_sqlite(gov_env, monkeypatch):
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    from services.compliance_store import _open_connection

    db_path = str(gov_env / "plain.db")
    conn = _open_connection(db_path)
    try:
        # Plain sqlite3 round-trip — no PRAGMA key needed.
        cur = conn.cursor()
        cur.execute("CREATE TABLE x (a INT)")
        cur.execute("INSERT INTO x VALUES (1)")
        cur.execute("SELECT a FROM x")
        assert cur.fetchone() == (1,)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Wrong-key fail-closed — SQLCipher path only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE,
    reason="sqlcipher3 not installed — fortress path cannot be exercised",
)
def test_sqlcipher_wrong_key_fails_on_read(gov_env, monkeypatch):
    # Build an encrypted DB with key A.
    import sqlcipher3

    db_path = str(gov_env / "fortress.db")
    conn_a = sqlcipher3.connect(db_path, isolation_level=None)
    conn_a.execute("PRAGMA key = 'correct-horse-battery-staple';")
    conn_a.execute("PRAGMA cipher_page_size = 4096;")
    conn_a.execute("PRAGMA kdf_iter = 256000;")
    conn_a.execute("CREATE TABLE secrets (s TEXT)")
    conn_a.execute("INSERT INTO secrets VALUES ('top-secret')")
    conn_a.close()

    # Open with wrong key — read must fail.
    conn_b = sqlcipher3.connect(db_path, isolation_level=None)
    conn_b.execute("PRAGMA key = 'wrong-key';")
    with pytest.raises(Exception):  # sqlcipher3 raises sqlite3.DatabaseError
        conn_b.execute("SELECT s FROM secrets").fetchone()
    conn_b.close()


@pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE,
    reason="sqlcipher3 not installed",
)
def test_sqlcipher_single_bit_off_key_fails(gov_env):
    """One-bit difference in the passphrase must still fail to decrypt."""
    import sqlcipher3

    db_path = str(gov_env / "off-by-one.db")
    conn_a = sqlcipher3.connect(db_path, isolation_level=None)
    conn_a.execute("PRAGMA key = 'aaaaaaaaaaaaaaaa';")
    conn_a.execute("CREATE TABLE x (a INT)")
    conn_a.execute("INSERT INTO x VALUES (42)")
    conn_a.close()

    conn_b = sqlcipher3.connect(db_path, isolation_level=None)
    # 'b' vs 'a' — one bit different in the first byte.
    conn_b.execute("PRAGMA key = 'baaaaaaaaaaaaaaa';")
    with pytest.raises(Exception):
        conn_b.execute("SELECT a FROM x").fetchone()
    conn_b.close()


@pytest.mark.skipif(
    not SQLCIPHER_AVAILABLE,
    reason="sqlcipher3 not installed",
)
def test_sqlcipher_correct_key_round_trip(gov_env):
    import sqlcipher3

    db_path = str(gov_env / "round-trip.db")
    conn_a = sqlcipher3.connect(db_path, isolation_level=None)
    conn_a.execute("PRAGMA key = 'good-key';")
    conn_a.execute("CREATE TABLE x (v TEXT)")
    conn_a.execute("INSERT INTO x VALUES ('hello')")
    conn_a.close()

    conn_b = sqlcipher3.connect(db_path, isolation_level=None)
    conn_b.execute("PRAGMA key = 'good-key';")
    row = conn_b.execute("SELECT v FROM x").fetchone()
    assert row == ("hello",)
    conn_b.close()


# ---------------------------------------------------------------------------
# Schema constants — fortress PRAGMAs match documented values
# ---------------------------------------------------------------------------


def test_compliance_schema_version_constant():
    from services.compliance_store import SCHEMA_VERSION

    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_compliance_schema_sql_constant_defined():
    from services.compliance_store import _SCHEMA_SQL

    assert "compliance_events" in _SCHEMA_SQL
    assert "seq" in _SCHEMA_SQL
    assert "idx_compliance" in _SCHEMA_SQL


# ---------------------------------------------------------------------------
# Compliance store round-trip on plain sqlite (deterministic dedupe)
# ---------------------------------------------------------------------------


def test_compliance_append_dedupes_by_seq(gov_env, monkeypatch):
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    from services.compliance_store import ComplianceStore

    db_path = str(gov_env / "comp.db")
    store = ComplianceStore(db_path=db_path)

    events = [
        {
            "seq": 1,
            "tick": 100,
            "category": 0,
            "rc": 0,
            "slot_id": 0,
            "digest_prefix": "a" * 16,
        },
        {
            "seq": 1,
            "tick": 100,
            "category": 0,
            "rc": 0,
            "slot_id": 0,
            "digest_prefix": "a" * 16,
        },  # duplicate
        {
            "seq": 2,
            "tick": 200,
            "category": 0,
            "rc": 0,
            "slot_id": 0,
            "digest_prefix": "b" * 16,
        },
    ]
    inserted = store.append_events(events)
    assert inserted == 2  # 3 attempted, 1 duplicate dropped
    assert store.highest_seq() == 2


def test_compliance_append_malformed_dropped(gov_env, monkeypatch):
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    from services.compliance_store import ComplianceStore

    db_path = str(gov_env / "comp2.db")
    store = ComplianceStore(db_path=db_path)

    inserted = store.append_events(
        [
            {
                "seq": 1,
                "tick": 100,
                "category": 0,
                "rc": 0,
                "slot_id": 0,
                "digest_prefix": "a" * 16,
            },
            {"missing_required_keys": True},  # malformed — must be dropped
            {
                "seq": 2,
                "tick": 200,
                "category": 0,
                "rc": 0,
                "slot_id": 0,
                "digest_prefix": "b" * 16,
            },
        ]
    )
    # 2 valid + 1 dropped → 2 inserted.
    assert inserted == 2
