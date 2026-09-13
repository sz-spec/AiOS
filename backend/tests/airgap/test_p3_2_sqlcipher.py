"""
P3.2 / SQLCipher — encrypted-at-rest tests.

Two tiers of coverage:

  1. Driver-less tests (always run) — exercise the key-resolution
     precedence, the `fortress_active()` env contract, and the
     "no driver installed" failure mode of `make_encrypted_engine()`.
     These do NOT need the SQLCipher binding and pin behavior that
     applies on every host (CI, dev laptops without sqlcipher3).

  2. Driver-gated tests (skipif) — the actual write → close → reopen
     roundtrip. Skipped cleanly when neither `sqlcipher3` nor
     `pysqlcipher3` is importable. CI gets coverage by installing
     `sqlcipher3-binary`; dev hosts without the wheel still get the
     contract-level tests above.

The wrong-key invariant is the load-bearing one: SQLCipher accepts a
wrong PRAGMA key silently at `open()` and only fails on the first
read. `make_encrypted_engine()` probes `sqlite_master` inside the
SQLAlchemy creator to surface that failure as `WrongComplianceKey`
at the connect boundary rather than mid-query.
"""

from __future__ import annotations


import pytest

from core.database import sqlcipher_setup
from core.database.sqlcipher_setup import (
    ComplianceKeyMissing,
    SqlCipherUnavailable,
    WrongComplianceKey,
    detect_driver,
    diagnose_readiness,
    fortress_active,
    make_encrypted_engine,
    resolve_compliance_key,
)

_DRIVER = detect_driver()
_DRIVER_AVAILABLE = _DRIVER is not None
_SKIP_REASON = (
    "SQLCipher Python driver not installed on this host. "
    "Install `sqlcipher3-binary` or `pysqlcipher3` to exercise the "
    "encrypted roundtrip."
)


# ---------------------------------------------------------------------------
# fortress_active() — env-contract tests (driver not required)
# ---------------------------------------------------------------------------


def test_fortress_inactive_when_neither_signal_set(monkeypatch):
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    assert fortress_active() is False


def test_fortress_active_via_profile_env(monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    assert fortress_active() is True


def test_fortress_active_via_compliance_key(monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "community")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "any-non-empty-passphrase")
    assert fortress_active() is True


def test_fortress_inactive_when_profile_is_other_value(monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "enterprise")
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    assert fortress_active() is False


# ---------------------------------------------------------------------------
# resolve_compliance_key() — precedence + production refusal
# ---------------------------------------------------------------------------


def test_resolve_compliance_key_prefers_env_var(monkeypatch):
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "explicit-operator-passphrase")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert resolve_compliance_key() == "explicit-operator-passphrase"


def test_resolve_compliance_key_production_refusal(monkeypatch):
    """P3.2 contract — when the keyring is ALSO unreachable in
    production, the resolver must refuse rather than fall back to
    a machine-id-derived key. P6.1 adds the keyring as a tier-2
    source; we simulate that source being broken so the production
    refusal still fires.

    Without this simulation the keyring would mint a fresh key on
    first boot, which is the new (correct) production happy path —
    but THIS test specifically pins the "everything is broken"
    branch where the operator has to set $VOS3_COMPLIANCE_KEY."""
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    # Force the keyring path to raise so we exercise the legacy
    # machine-id branch, which production refuses.

    def _wedged():
        raise RuntimeError("simulated: keyring unreachable in production")

    monkeypatch.setattr(
        "services.crypto_keyring.get_or_mint_db_master_key",
        _wedged,
    )
    with pytest.raises(ComplianceKeyMissing) as exc:
        resolve_compliance_key()
    assert "VOS3_COMPLIANCE_KEY must be set" in str(exc.value)


def test_resolve_compliance_key_dev_fallback_or_explicit_error(monkeypatch):
    """In development with no env key, ANY of these is acceptable:
      - a keyring-minted key (P6.1, the new primary path)
      - a machine-id-derived 64-hex key (P3.2 legacy, when keyring
        is unreachable)
      - ComplianceKeyMissing (genuinely no way to derive anything)

    What's NOT acceptable is silently returning empty / predictable."""
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    try:
        key = resolve_compliance_key()
    except ComplianceKeyMissing:
        return  # acceptable on locked-down hosts
    # P6.1 keyring keys are 32-byte token_urlsafe (~43 chars); the
    # P3.2 machine-id fallback is exactly 64 hex chars. Either is
    # acceptable; the floor is "non-trivial entropy".
    assert len(key) >= 32, f"key suspiciously short: got {len(key)}"
    assert key != "", "empty key returned — must never happen"


# ---------------------------------------------------------------------------
# detect_driver / make_encrypted_engine — no-driver failure surface
# ---------------------------------------------------------------------------


def test_detect_driver_returns_none_or_module():
    """The probe must never raise — it returns None when no driver
    is installed, or the driver module when one is."""
    drv = detect_driver()
    assert drv is None or hasattr(
        drv, "connect"
    ), f"detect_driver() returned a non-DBAPI object: {drv!r}"


def test_make_encrypted_engine_raises_when_driver_missing(tmp_path, monkeypatch):
    """The factory must refuse explicitly rather than fall back to
    plaintext when no SQLCipher binding is importable. Simulate the
    no-driver case by monkeypatching `detect_driver` to return None
    — this lets the test run on every host (fortress-ready or not)
    rather than skipping when the driver is installed."""
    monkeypatch.setattr(sqlcipher_setup, "detect_driver", lambda: None)
    with pytest.raises(SqlCipherUnavailable) as exc:
        sqlcipher_setup.make_encrypted_engine(tmp_path / "vos3.db", key="ignored")
    msg = str(exc.value)
    assert "sqlcipher3-binary" in msg or "pysqlcipher3" in msg
    assert "Refusing to fall back to plain SQLite" in msg


# ---------------------------------------------------------------------------
# diagnose_readiness() — operator-facing host probe
# ---------------------------------------------------------------------------


def test_diagnose_readiness_reports_not_needed_under_community(monkeypatch):
    monkeypatch.delenv("VOS_PROFILE", raising=False)
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    r = diagnose_readiness(emit=False)
    assert r["profile_requests_fortress"] is False
    assert r["compliance_key_status"] == "not-needed"
    assert r["ready"] is False  # ready only when fortress is requested AND aligned


def test_diagnose_readiness_flags_missing_key_in_production(monkeypatch):
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.delenv("VOS3_COMPLIANCE_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    r = diagnose_readiness(emit=False)
    assert r["profile_requests_fortress"] is True
    assert r["compliance_key_status"] == "missing-prod"
    assert r["ready"] is False
    assert any("VOS3_COMPLIANCE_KEY" in h for h in r["hints"])


def test_diagnose_readiness_flags_missing_driver(monkeypatch):
    """When fortress is requested but no driver is installed, the
    report must surface a platform-specific install hint."""
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "operator-key")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(sqlcipher_setup, "detect_driver", lambda: None)
    r = diagnose_readiness(emit=False)
    assert r["driver_available"] is False
    assert r["ready"] is False
    # At least one hint must reference an install path the operator
    # can actually run on their machine.
    joined = " ".join(r["hints"])
    assert "sqlcipher3" in joined or "pysqlcipher3" in joined or "SQLCipher" in joined


@pytest.mark.skipif(not _DRIVER_AVAILABLE, reason=_SKIP_REASON)
def test_diagnose_readiness_ready_when_fortress_aligned(monkeypatch):
    """Fortress profile + explicit env key + driver installed →
    ready=True with no hints."""
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "operator-key")
    monkeypatch.setenv("ENVIRONMENT", "development")
    r = diagnose_readiness(emit=False)
    assert r["ready"] is True
    assert r["driver_available"] is True
    assert r["compliance_key_status"] == "env"
    assert r["hints"] == []


# ---------------------------------------------------------------------------
# Encrypted roundtrip — driver required
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DRIVER_AVAILABLE, reason=_SKIP_REASON)
def test_encrypted_roundtrip_right_key_succeeds(tmp_path):
    """Write a row with key K, close the engine, reopen with K, read
    the row back. Round-trip must succeed and the value must match."""
    from sqlalchemy import text

    db = tmp_path / "fortress.db"
    key = "round-trip-passphrase-correct-horse-battery-staple"

    engine = make_encrypted_engine(db, key=key)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE secret (k TEXT, v TEXT)"))
            conn.execute(
                text("INSERT INTO secret (k, v) VALUES (:k, :v)"),
                {"k": "alpha", "v": "encrypted-payload"},
            )
            conn.commit()
    finally:
        engine.dispose()

    # Reopen with the SAME key — must succeed and return the row.
    engine2 = make_encrypted_engine(db, key=key)
    try:
        with engine2.connect() as conn:
            row = conn.execute(
                text("SELECT v FROM secret WHERE k = :k"), {"k": "alpha"}
            ).fetchone()
            assert row is not None, "row missing after reopen with correct key"
            assert row[0] == "encrypted-payload"
    finally:
        engine2.dispose()


@pytest.mark.skipif(not _DRIVER_AVAILABLE, reason=_SKIP_REASON)
def test_encrypted_roundtrip_wrong_key_raises(tmp_path):
    """Write a row with key K, close, reopen with key K' (K' != K).
    The wrong-key probe inside the SQLAlchemy creator must trip and
    raise `WrongComplianceKey` at engine.connect() time — not
    silently succeed and fail mid-query later."""
    from sqlalchemy import text

    db = tmp_path / "fortress.db"
    right = "the-correct-passphrase"
    wrong = "the-WRONG-passphrase"

    engine = make_encrypted_engine(db, key=right)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE t (x INTEGER)"))
            conn.execute(text("INSERT INTO t (x) VALUES (42)"))
            conn.commit()
    finally:
        engine.dispose()

    engine_bad = make_encrypted_engine(db, key=wrong)
    with pytest.raises(WrongComplianceKey) as exc:
        with engine_bad.connect():
            pass
    assert "Failed to decrypt" in str(exc.value)
    engine_bad.dispose()


@pytest.mark.skipif(not _DRIVER_AVAILABLE, reason=_SKIP_REASON)
def test_plain_sqlite_cannot_read_encrypted_file(tmp_path):
    """An encrypted DB opened by stdlib sqlite3 (no key) must fail —
    this is the on-disk encryption invariant. If a plain sqlite3
    process can read the file, the encryption is not on."""
    import sqlite3

    from sqlalchemy import text

    db = tmp_path / "fortress.db"
    key = "tamper-evident-passphrase"

    engine = make_encrypted_engine(db, key=key)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE canary (v TEXT)"))
            conn.execute(text("INSERT INTO canary VALUES ('vos3-secret')"))
            conn.commit()
    finally:
        engine.dispose()

    # Now try to open with stdlib sqlite3 — should fail or return zero
    # rows / garbage. SQLCipher-encrypted files have a randomized
    # header so stdlib sqlite3 doesn't even see them as a valid DB.
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    raised = False
    rows = None
    try:
        cur.execute("SELECT v FROM canary")
        rows = cur.fetchall()
    except sqlite3.DatabaseError:
        raised = True
    finally:
        cur.close()
        conn.close()

    assert (
        raised or not rows or "vos3-secret" not in str(rows)
    ), "Plain sqlite3 was able to read the encrypted DB — encryption is OFF."


# ---------------------------------------------------------------------------
# sqlite_setup dispatch — fortress flag flips the engine factory
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DRIVER_AVAILABLE, reason=_SKIP_REASON)
def test_get_engine_uses_encrypted_factory_under_fortress(tmp_path, monkeypatch):
    """When `VOS_PROFILE=fortress`, the singleton engine from
    `sqlite_setup.get_engine()` must come from the encrypted factory.
    Smoke-tested by writing a row through the engine and confirming
    plain sqlite3 can't read it back."""
    import sqlite3

    from sqlalchemy import text

    from core.database import sqlite_setup

    db = tmp_path / "fortress.db"
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", str(db))
    monkeypatch.setenv("VOS_PROFILE", "fortress")
    monkeypatch.setenv("VOS3_COMPLIANCE_KEY", "fortress-dispatch-passphrase")
    monkeypatch.setenv("ENVIRONMENT", "development")

    sqlite_setup._reset_for_tests()
    try:
        engine = sqlite_setup.get_engine()
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE dispatch_canary (v TEXT)"))
            conn.execute(text("INSERT INTO dispatch_canary VALUES ('via-fortress')"))
            conn.commit()
    finally:
        sqlite_setup._reset_for_tests()

    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    leaked = False
    try:
        cur.execute("SELECT v FROM dispatch_canary")
        rows = cur.fetchall()
        if rows and "via-fortress" in str(rows):
            leaked = True
    except sqlite3.DatabaseError:
        pass
    finally:
        cur.close()
        conn.close()

    assert not leaked, "Fortress-mode engine wrote plaintext to disk."
