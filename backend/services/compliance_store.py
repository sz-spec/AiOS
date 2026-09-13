"""
backend/services/compliance_store.py
=====================================

Stage 10.3 — long-term persistence for kernel-emitted compliance events
(IntentManifest rejections, TEE bind failures, hallucination blocks,
force-permit overrides).

Schema choice — pragmatic honesty
=================================

The Stage-10 plan specifies SQLCipher persistence (encrypted-at-rest
SQLite). The vos.v1 dependency surface today does NOT carry a SQLCipher
binding — `vault_pool.py` is one of the missing files in the gap plan
(stage-10 backend port, not yet executed). Rather than block on that
port, this module:

  - Uses Python stdlib `sqlite3` so it works on every dev workstation,
    in CI, and inside the Docker image without a new wheel.
  - Exposes the storage backend as a single function call
    (`_open_connection`) so the SQLCipher migration is a one-line change
    once `pysqlcipher3` (or whichever wrapper the gap plan elects)
    lands.
  - Documents the encryption-at-rest gap in the table-creation SQL so
    a security auditor reading the database file sees the in-tree
    intent.

Concretely: when `vos.v1/backend/core/repositories/vault_pool.py` is
ported in Stage 10's missing-files batch, the `_open_connection`
function below is the single swap point. No call site changes.

Schema
======

Single table `compliance_events`:

    seq             INTEGER PRIMARY KEY      -- monotonic from kernel ring
    tick            INTEGER NOT NULL         -- kernel boot tick
    drained_at      INTEGER NOT NULL         -- backend wall-clock at drain
    category        INTEGER NOT NULL         -- VOS3_AUDIT_CAT_* enum value
    rc              INTEGER NOT NULL         -- kernel rejection code
    slot_id         INTEGER NOT NULL         -- 0..VOS3_MODEL_SLOT_MAX-1, or 255
    digest_prefix   TEXT NOT NULL            -- 16 hex chars, first 8 bytes
                                             --   of SHA-384 over the rejected
                                             --   payload

The kernel ring (Stage 10.1) is bounded — 64 entries — so a heavy
attack burst can lap it before the backend drains. The `seq` field is
the kernel's monotonic emit counter; the backend tracks "highest seq
seen" and skips duplicates on re-drain. If the kernel reports a
`total_emitted` greater than `seq + count(buffered)`, the backend logs
a "compliance gap" warning so the auditor sees the lost head.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/tmp/vos_compliance.db"

_SCHEMA_SQL = """
-- Stage 10.3 — kernel compliance event store
-- Encryption-at-rest gap: this database is currently stdlib sqlite3.
-- The Stage-10 plan calls for SQLCipher; swap point is in
-- backend/services/compliance_store.py::_open_connection. Do NOT add
-- columns here without bumping the SCHEMA_VERSION constant in this
-- module so a future migration path is possible.
CREATE TABLE IF NOT EXISTS compliance_events (
    seq            INTEGER PRIMARY KEY,
    tick           INTEGER NOT NULL,
    drained_at     INTEGER NOT NULL,
    category       INTEGER NOT NULL,
    rc             INTEGER NOT NULL,
    slot_id        INTEGER NOT NULL,
    digest_prefix  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compliance_category
    ON compliance_events(category);
CREATE INDEX IF NOT EXISTS idx_compliance_drained_at
    ON compliance_events(drained_at);
"""

SCHEMA_VERSION = 1


def _open_connection(db_path: str) -> sqlite3.Connection:
    """Open a sqlite3 (or SQLCipher) connection.

    Single migration point for the Stage-10 SQLCipher upgrade. Behaviour:

    - Under VOS_PROFILE=fortress, SQLCipher is REQUIRED. The connection
      uses sqlcipher3 with the passphrase from VOS3_COMPLIANCE_KEY.
      Missing key → RuntimeError (fail-closed per docs/PROFILES.md).
    - Under any other profile, plain stdlib sqlite3 is used (back-compat
      with all existing dev workstations + CI). The fortress gate is the
      only path that mandates encryption-at-rest.
    """
    try:
        from vos_profile import is_fortress  # local import — avoid boot-cycle
    except ImportError:
        is_fortress = lambda: False  # noqa: E731 — backend not on path

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    if is_fortress():
        key = os.environ.get("VOS3_COMPLIANCE_KEY")
        if not key:
            raise RuntimeError(
                "VOS_PROFILE=fortress requires VOS3_COMPLIANCE_KEY for SQLCipher. "
                "Set the env var or use a non-fortress profile."
            )
        try:
            import sqlcipher3 as cipher_sqlite  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "VOS_PROFILE=fortress requires sqlcipher3-binary. "
                "Run: pip install sqlcipher3-binary==0.5.4"
            ) from e
        conn = cipher_sqlite.connect(
            db_path, isolation_level=None, check_same_thread=False
        )
        # PRAGMA key MUST be the first executed statement on a fresh
        # connection — anything before it sees an unencrypted page.
        conn.execute(f"PRAGMA key = '{key}';")
        conn.execute("PRAGMA cipher_page_size = 4096;")
        conn.execute("PRAGMA kdf_iter = 256000;")
        conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
        conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


class ComplianceStore:
    """Thread-safe persistence layer for kernel compliance events.

    Concurrency model: one connection, one lock. Append rates are bounded
    by the kernel's emit rate (one frame per VBus tick), so contention
    is not a real concern. For the dashboard's read-side, a separate
    short-lived connection is opened per query — sqlite3 handles
    multi-reader fine.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._conn = _open_connection(self._db_path)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)

    @property
    def db_path(self) -> str:
        return self._db_path

    # ------------------------------------------------------------------
    # Write path — drain helpers
    # ------------------------------------------------------------------

    def append_events(self, events: Iterable[dict]) -> int:
        """Insert events; ignore duplicates on the kernel monotonic seq.

        Returns the number of NEW rows inserted (excludes duplicates).
        """
        now = int(time.time())
        new_count = 0
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN")
                for e in events:
                    try:
                        cur.execute(
                            """
                            INSERT OR IGNORE INTO compliance_events
                              (seq, tick, drained_at, category, rc, slot_id,
                               digest_prefix)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                int(e["seq"]),
                                int(e.get("tick", 0)),
                                now,
                                int(e["category"]),
                                int(e["rc"]),
                                int(e["slot_id"]),
                                str(e.get("digest_prefix", "0" * 16)),
                            ),
                        )
                        new_count += cur.rowcount or 0
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.warning(
                            "ComplianceStore: dropped malformed event %r (%s).",
                            e,
                            exc,
                        )
                cur.execute("COMMIT")
            except sqlite3.Error:
                cur.execute("ROLLBACK")
                raise
        return new_count

    def highest_seq(self) -> int:
        """Highest kernel seq observed so far. -1 if table is empty."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM compliance_events"
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Read path — for the Sovereign Control Panel
    # ------------------------------------------------------------------

    def recent_events(
        self, *, limit: int = 100, category: Optional[int] = None
    ) -> list[dict]:
        """Return the N most-recent events, optionally filtered by category.

        Used by the management dashboard's live feed. Default 100 rows
        is enough to fill a page without a scroll-bomb.
        """
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be in [1, 10000]")
        sql = (
            "SELECT seq, tick, drained_at, category, rc, slot_id, digest_prefix "
            "FROM compliance_events "
        )
        params: tuple = ()
        if category is not None:
            sql += "WHERE category = ? "
            params = (int(category),)
        sql += "ORDER BY seq DESC LIMIT ?"
        params = params + (int(limit),)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "seq": r[0],
                "tick": r[1],
                "drained_at": r[2],
                "category": r[3],
                "rc": r[4],
                "slot_id": r[5],
                "digest_prefix": r[6],
            }
            for r in rows
        ]

    def category_counts(self) -> dict[int, int]:
        """Aggregate count per category — feeds the dashboard's category bar."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, COUNT(*) FROM compliance_events GROUP BY category"
            ).fetchall()
        return {int(r[0]): int(r[1]) for r in rows}

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    @contextmanager
    def transaction(self):
        """Context-managed multi-write transaction (used by tests)."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN")
                yield cur
                cur.execute("COMMIT")
            except sqlite3.Error:
                cur.execute("ROLLBACK")
                raise


# ---------------------------------------------------------------------------
# Module-level singleton — same shape as policy_override.py
# ---------------------------------------------------------------------------

_singleton: Optional[ComplianceStore] = None
_singleton_lock = threading.Lock()


def get_compliance_store(db_path: Optional[str] = None) -> ComplianceStore:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ComplianceStore(db_path)
        return _singleton
