"""
backend/core/repositories/vault_pool.py
========================================

Stage 10.3 (Sprint 14.1) — rotation-aware repository pool.

Problem
-------

When the rotation_manager swaps the workflow signing key (or the SQLCipher
master, or any other live credential), already-open repository connections
hold the OLD credential in memory. The next write through that connection
succeeds (because the DB still has the old MAC key cached too), and only
when the OLD key expires from the DB's cache does the connection start to
fail — at which point the rotation looks half-broken from the operator's
perspective.

Solution
--------

VaultPool wraps repository factories with:
  1. **Generation tracking** — each rotation bumps a generation counter.
  2. **Lazy invalidation** — checked-out repositories that observe a
     stale generation are discarded on return; the next acquire mints
     a fresh one with the current credential.
  3. **Bounded outstanding count** — caller-configurable; if exceeded,
     acquire blocks (with timeout) rather than open ad-hoc connections.

This is the userspace analogue of the kernel-side credential-cache
invalidation in mm/ai_kim.c — same shape, different runtime.

Honest scope ceiling
--------------------

The pool is **process-local**. Multi-process workers (gunicorn with N
workers) each maintain their own pool, and each must be notified of a
rotation independently. The rotation_manager already broadcasts via the
SQLite SecurityAuditLog table, but pools don't subscribe to that today —
they observe the generation bump only via direct `bump_generation()` call.

A cross-worker rotation broadcast (Redis pub-sub or kernel-side VBus
NOTIFY) is the v1.2 swap point. The single-worker case is correct as-is.

API shape
---------

    pool = VaultPool(
        factory=lambda: ComplianceStore(),
        max_outstanding=8,
        acquire_timeout_s=5.0,
    )

    with pool.acquire() as store:
        store.append_events(...)

    # On rotation:
    pool.bump_generation()
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Generic, Iterator, Optional, TypeVar

logger = logging.getLogger(__name__)


T = TypeVar("T")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VaultPoolExhausted(RuntimeError):
    """Raised by acquire() when max_outstanding is hit and timeout expires."""


class VaultPoolClosed(RuntimeError):
    """Raised by acquire() after close() is called."""


# ---------------------------------------------------------------------------
# Internal handle wrapper
# ---------------------------------------------------------------------------


@dataclass
class _PooledHandle(Generic[T]):
    value: T
    generation: int
    acquired_at: float
    in_use: bool = False


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class ConnectionVaultPool(Generic[T]):
    """Rotation-aware repository pool.

    Parameters
    ----------
    factory : callable returning a fresh repository / connection / signer
    max_outstanding : hard ceiling on simultaneously-checked-out handles
    acquire_timeout_s : how long acquire() blocks if pool is at capacity
    close_callback : optional ``(value) -> None`` called when discarding
                     a stale or returned handle. Use to ``.close()`` SQL
                     connections, scrub key material, etc.
    """

    def __init__(
        self,
        factory: Callable[[], T],
        *,
        max_outstanding: int = 8,
        acquire_timeout_s: float = 5.0,
        close_callback: Optional[Callable[[T], None]] = None,
    ) -> None:
        if max_outstanding < 1:
            raise ValueError("max_outstanding must be >= 1")
        self._factory = factory
        self._max = int(max_outstanding)
        self._timeout = float(acquire_timeout_s)
        self._close_cb = close_callback
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._idle: list[_PooledHandle[T]] = []
        self._outstanding: int = 0
        self._generation: int = 0
        self._closed: bool = False
        self._acquires_total: int = 0
        self._invalidated_total: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def bump_generation(self) -> int:
        """Invalidate every currently-idle handle and bump the watermark.

        Outstanding handles are NOT yanked out from under their owners —
        they're discarded when returned. Callers that need *immediate*
        eviction across all workers should call this then send a
        SIGTERM to the gunicorn master (a deliberate rolling restart)."""
        with self._lock:
            self._generation += 1
            stale, self._idle = self._idle, []
        # Run close_callbacks outside the lock to avoid holding it while
        # potentially-slow connection-close I/O happens.
        if self._close_cb is not None:
            for h in stale:
                try:
                    self._close_cb(h.value)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[vault_pool] close_callback failed: %s", exc)
        with self._lock:
            self._invalidated_total += len(stale)
            self._cond.notify_all()
            return self._generation

    def close(self) -> None:
        with self._lock:
            self._closed = True
            stale, self._idle = self._idle, []
            self._cond.notify_all()
        if self._close_cb is not None:
            for h in stale:
                try:
                    self._close_cb(h.value)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[vault_pool] close_callback failed during close(): %s", exc
                    )

    # ------------------------------------------------------------------
    # Checkout
    # ------------------------------------------------------------------

    @contextmanager
    def acquire(self) -> Iterator[T]:
        handle = self._checkout()
        try:
            yield handle.value
        finally:
            self._checkin(handle)

    def _checkout(self) -> _PooledHandle[T]:
        deadline = time.time() + self._timeout
        with self._cond:
            while True:
                if self._closed:
                    raise VaultPoolClosed("VaultPool is closed")

                if self._idle:
                    handle = self._idle.pop()
                    handle.in_use = True
                    handle.acquired_at = time.time()
                    self._outstanding += 1
                    self._acquires_total += 1
                    return handle

                if self._outstanding < self._max:
                    # Mint a fresh handle outside the cond to avoid blocking
                    # other acquires on a slow factory; we re-enter to record.
                    self._outstanding += 1
                    self._acquires_total += 1
                    current_gen = self._generation
                    self._cond.release()
                    try:
                        value = self._factory()
                    except Exception:
                        # Roll back outstanding counter on factory failure.
                        self._cond.acquire()
                        self._outstanding -= 1
                        self._cond.notify_all()
                        raise
                    self._cond.acquire()
                    return _PooledHandle(
                        value=value,
                        generation=current_gen,
                        acquired_at=time.time(),
                        in_use=True,
                    )

                remaining = deadline - time.time()
                if remaining <= 0:
                    raise VaultPoolExhausted(
                        f"VaultPool exhausted: {self._outstanding}/{self._max} "
                        f"outstanding, no handle freed within {self._timeout}s"
                    )
                self._cond.wait(timeout=remaining)

    def _checkin(self, handle: _PooledHandle[T]) -> None:
        stale = False
        with self._cond:
            self._outstanding -= 1
            handle.in_use = False
            if self._closed or handle.generation != self._generation:
                stale = True
            else:
                self._idle.append(handle)
            self._cond.notify_all()
        if stale and self._close_cb is not None:
            try:
                self._close_cb(handle.value)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[vault_pool] close_callback on checkin failed: %s", exc)
        if stale:
            with self._lock:
                self._invalidated_total += 1

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            return {
                "generation": self._generation,
                "idle": len(self._idle),
                "outstanding": self._outstanding,
                "max_outstanding": self._max,
                "acquires_total": self._acquires_total,
                "invalidated_total": self._invalidated_total,
                "closed": self._closed,
            }


# ---------------------------------------------------------------------------
# rotation_manager hook
# ---------------------------------------------------------------------------

_registered_pools: list["ConnectionVaultPool"] = []
_registry_lock = threading.Lock()


def register_pool(pool: "ConnectionVaultPool") -> None:
    """Register a pool for automatic invalidation on rotation events.

    The rotation_manager calls ``invalidate_all_pools()`` after any
    successful rotation; registered pools have their generations bumped."""
    with _registry_lock:
        if pool not in _registered_pools:
            _registered_pools.append(pool)


def unregister_pool(pool: "ConnectionVaultPool") -> None:
    with _registry_lock:
        if pool in _registered_pools:
            _registered_pools.remove(pool)


def invalidate_all_pools() -> int:
    """Bump generation on every registered pool. Returns count bumped."""
    with _registry_lock:
        pools = list(_registered_pools)
    count = 0
    for pool in pools:
        try:
            pool.bump_generation()
            count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[vault_pool] bump_generation failed: %s", exc)
    return count


def _reset_registry_for_tests() -> None:
    with _registry_lock:
        _registered_pools.clear()


# ---------------------------------------------------------------------------
# v20.6-OMNIPRESENCE — certificate VaultPool (SQLite-backed cert store)
#
# A small, pooled SQLite store for IntegrityCertificate payloads, keyed by
# cert_id and indexed by tenant. Distinct from the rotation-aware
# ConnectionVaultPool above (which pools live credentials); this one
# persists attestation payloads with batched insert accounting.
# ---------------------------------------------------------------------------

import json as _json
import sqlite3 as _sqlite3


@dataclass
class VaultStats:
    batches: int = 0  # number of store_many() batch operations
    inserts: int = 0  # total rows inserted (store + store_many)


class VaultPool:
    """Pooled SQLite certificate vault.

    Parameters
    ----------
    db_path : path to the SQLite file backing the vault
    pool_size : number of pooled connections kept open
    """

    def __init__(self, db_path, pool_size: int) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        self.db_path = str(db_path)
        self.pool_size = int(pool_size)
        self._lock = threading.RLock()
        self._stats = VaultStats()
        self._pool: list[_sqlite3.Connection] = []
        self._init_schema()
        for _ in range(self.pool_size):
            self._pool.append(self._new_conn())

    def _new_conn(self) -> "_sqlite3.Connection":
        conn = _sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = _sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._new_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS certs (
                    cert_id   TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    issued_ts INTEGER NOT NULL,
                    payload   TEXT NOT NULL
                )
                """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_certs_tenant " "ON certs(tenant_id)"
            )
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _conn(self) -> Iterator["_sqlite3.Connection"]:
        with self._lock:
            conn = self._pool.pop() if self._pool else self._new_conn()
        try:
            yield conn
        finally:
            with self._lock:
                if len(self._pool) < self.pool_size:
                    self._pool.append(conn)
                else:
                    conn.close()

    def store(
        self, cert_id: str, tenant_id: str, issued_ts: int, payload: dict
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO certs "
                "(cert_id, tenant_id, issued_ts, payload) VALUES (?,?,?,?)",
                (cert_id, tenant_id, int(issued_ts), _json.dumps(payload)),
            )
            conn.commit()
        with self._lock:
            self._stats.inserts += 1

    def store_many(self, rows: list[dict]) -> int:
        params = [
            (
                r["cert_id"],
                r["tenant_id"],
                int(r["issued_ts"]),
                _json.dumps(r["payload"]),
            )
            for r in rows
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO certs "
                "(cert_id, tenant_id, issued_ts, payload) VALUES (?,?,?,?)",
                params,
            )
            conn.commit()
        with self._lock:
            self._stats.batches += 1
            self._stats.inserts += len(params)
        return len(params)

    def get(self, cert_id: str) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT payload FROM certs WHERE cert_id = ?", (cert_id,)
            )
            row = cur.fetchone()
        return _json.loads(row["payload"]) if row is not None else None

    def list_by_tenant(self, tenant_id: str) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT payload FROM certs WHERE tenant_id = ? " "ORDER BY issued_ts",
                (tenant_id,),
            )
            rows = cur.fetchall()
        return [_json.loads(r["payload"]) for r in rows]

    def stats(self) -> VaultStats:
        with self._lock:
            return VaultStats(batches=self._stats.batches, inserts=self._stats.inserts)

    def close_all(self) -> None:
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except _sqlite3.Error:
                    pass
            self._pool.clear()


__all__ = [
    "VaultPool",
    "VaultStats",
    "ConnectionVaultPool",
    "VaultPoolExhausted",
    "VaultPoolClosed",
    "register_pool",
    "unregister_pool",
    "invalidate_all_pools",
]
