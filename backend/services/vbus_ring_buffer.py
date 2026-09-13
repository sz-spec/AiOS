"""
backend/services/vbus_ring_buffer.py
=====================================

Stage 10.3 (Sprint 14.1) — userspace drain of the kernel's AUDIT_FAIL_QUOTE ring.

The kernel-side ring (kernel/src/mm/audit_ring.c) is bounded at 64 entries.
Under a heavy compliance burst it can lap before the backend pulls — the
kernel reports ``total_emitted`` alongside the buffered batch so userspace
can detect (and audit) the gap.

Architecture
------------

    VBus driver  ──[AUDIT_FAIL_QUOTE]──►  kernel audit_ring.c
                  ◄─────[batch + total]────

    VBusRingBuffer
      • polls every drain_interval_s seconds
      • tracks highest_seq_seen (monotonic, in-process)
      • inserts NEW events into ComplianceStore (de-dup on seq PK)
      • emits gap warnings when kernel total_emitted advances past
        (highest_seq_seen + len(buffered_batch))

Wire format
-----------

Kernel reply per Stage 10.1 spec (services/policy_override.py mirrors this):

    AUDIT_FAIL|total=<N>|fill=<M>|<seq>:<cat>:<rc>:<slot>:<digest>|...

Where:
    N  = total emitted since boot (monotonic, may exceed 64)
    M  = entries currently buffered (≤64)
    Each event tuple is colon-separated; entries are pipe-separated.
    digest = 16 hex chars (first 8 bytes of SHA-384 over rejected payload).

Honest scope ceiling
--------------------

This module is the **userspace half** of the kernel ring. It does NOT
implement an in-memory ring of its own — the source of truth is the
kernel ring (bounded) plus the ComplianceStore SQLite table (unbounded).
The in-process ``highest_seq_seen`` is purely a watermark to dedupe
overlapping batches across drain cycles.

If the backend crashes between two drain cycles, the next start re-reads
``MAX(seq) FROM compliance_events`` and resumes from there — but anything
that lapped the kernel ring during the downtime is gone, and we log
``compliance_gap`` so the operator sees it explicitly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_DRAIN_INTERVAL_S = 2.0
DEFAULT_MAX_BATCH = 64
AUDIT_FAIL_QUOTE_CMD = "AUDIT_FAIL_QUOTE"


# ---------------------------------------------------------------------------
# Event record (mirrors compliance_store.compliance_events row shape)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    tick: int
    category: int
    rc: int
    slot_id: int
    digest_prefix: str  # 16 hex chars

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "tick": self.tick,
            "category": self.category,
            "rc": self.rc,
            "slot_id": self.slot_id,
            "digest_prefix": self.digest_prefix,
        }


# ---------------------------------------------------------------------------
# Reply parser — single source of truth for AUDIT_FAIL_QUOTE wire format
# ---------------------------------------------------------------------------


def parse_audit_fail_quote(reply: str) -> tuple[int, int, list[AuditEvent]]:
    """Parse the kernel's AUDIT_FAIL_QUOTE reply.

    Returns (total_emitted, fill, events). Raises ValueError on malformed input.
    """
    if not isinstance(reply, str) or not reply:
        raise ValueError("empty audit reply")
    parts = reply.strip().split("|")
    if not parts or parts[0] != "AUDIT_FAIL":
        raise ValueError(f"unexpected header: {parts[0] if parts else None!r}")

    total = 0
    fill = 0
    events: list[AuditEvent] = []

    for token in parts[1:]:
        if not token:
            continue
        if token.startswith("total="):
            total = int(token[len("total=") :])
        elif token.startswith("fill="):
            fill = int(token[len("fill=") :])
        else:
            fields = token.split(":")
            if len(fields) != 5:
                raise ValueError(f"bad event tuple: {token!r}")
            seq, tick, cat, rc_or_slot, *_ = fields
            # Format: seq:cat:rc:slot:digest  (per docstring header)
            seq_i, cat_i, rc_i, slot_i, digest = fields
            events.append(
                AuditEvent(
                    seq=int(seq_i),
                    tick=int(
                        time.time()
                    ),  # kernel "tick" not exposed per-event; use drain wall clock
                    category=int(cat_i),
                    rc=int(rc_i),
                    slot_id=int(slot_i),
                    digest_prefix=str(digest),
                )
            )
    return total, fill, events


# ---------------------------------------------------------------------------
# Drain loop
# ---------------------------------------------------------------------------


class VBusRingBuffer:
    """Polls the kernel audit ring, persists new events, surfaces gaps.

    Construction
    ------------
        VBusRingBuffer(driver, store, *, drain_interval_s=2.0)

        driver: object exposing .send_command(str) -> str
                (services.vbus_driver.VBusDriver or test double)
        store:  object exposing .append_events(iterable[dict]) -> int
                and .highest_seq() -> int
                (services.compliance_store.ComplianceStore)

    Lifecycle
    ---------
        await ring.run_forever()      # cooperative async loop
        ring.stop()                    # signals run_forever to exit

    Or sync drain (for tests / CLI):
        ring.drain_once() -> dict      # {"new": N, "gap": M, "total": K}
    """

    def __init__(
        self,
        driver,
        store,
        *,
        drain_interval_s: float = DEFAULT_DRAIN_INTERVAL_S,
    ) -> None:
        self._driver = driver
        self._store = store
        self._interval = float(drain_interval_s)
        self._lock = threading.RLock()
        self._highest_seq_seen = (
            self._store.highest_seq() if hasattr(self._store, "highest_seq") else 0
        )
        self._stop = False
        self._last_total = 0
        self._last_drain_at: Optional[float] = None

    # ------------------------------------------------------------------
    # One-shot drain
    # ------------------------------------------------------------------

    def drain_once(self) -> dict:
        with self._lock:
            try:
                reply = self._driver.send_command(AUDIT_FAIL_QUOTE_CMD)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[vbus_ring_buffer] drain command failed: %s", exc)
                return {
                    "new": 0,
                    "gap": 0,
                    "total": self._last_total,
                    "error": str(exc),
                }

            try:
                total, fill, events = parse_audit_fail_quote(reply)
            except ValueError as exc:
                logger.warning("[vbus_ring_buffer] malformed reply %r: %s", reply, exc)
                return {
                    "new": 0,
                    "gap": 0,
                    "total": self._last_total,
                    "error": str(exc),
                }

            # Honest gap detection: kernel total advanced past what we can see.
            # Example: kernel total=200, fill=64, highest_seq_seen=100 →
            # we will receive 64 events; if their min seq > 101 we lost
            # (min_seq - 101) events to ring lap.
            new_events = [e for e in events if e.seq > self._highest_seq_seen]
            gap = 0
            if new_events:
                min_seq = min(e.seq for e in new_events)
                if self._highest_seq_seen and min_seq > self._highest_seq_seen + 1:
                    gap = min_seq - self._highest_seq_seen - 1
                self._highest_seq_seen = max(e.seq for e in new_events)

            inserted = 0
            if new_events:
                inserted = self._store.append_events([e.to_dict() for e in new_events])

            if gap > 0:
                logger.warning(
                    "[vbus_ring_buffer] compliance_gap: lost %d events to ring lap "
                    "(highest_seq_seen=%d, min_new_seq=%d, kernel_total=%d, fill=%d)",
                    gap,
                    self._highest_seq_seen,
                    new_events[0].seq if new_events else -1,
                    total,
                    fill,
                )

            self._last_total = total
            self._last_drain_at = time.time()
            return {
                "new": inserted,
                "gap": gap,
                "total": total,
                "fill": fill,
                "highest_seq_seen": self._highest_seq_seen,
            }

    # ------------------------------------------------------------------
    # Async loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Cooperative drain loop. Exits when stop() is called."""
        while not self._stop:
            try:
                self.drain_once()
            except Exception as exc:  # noqa: BLE001 — loop must survive
                logger.exception("[vbus_ring_buffer] unexpected drain error: %s", exc)
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        with self._lock:
            self._stop = True

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return {
                "highest_seq_seen": self._highest_seq_seen,
                "last_total_from_kernel": self._last_total,
                "last_drain_at": self._last_drain_at,
                "interval_s": self._interval,
                "stopped": self._stop,
            }


# ===========================================================================
# v20.5-SINGULARITY — ZeroCopyRingBuffer (POSIX shared-memory SPSC ring)
#
# A single-producer / single-consumer ring over a POSIX shared-memory
# segment. Distinct from VBusRingBuffer above (which polls the kernel audit
# ring); this is a userspace zero-copy transport where a producer writes a
# slot in place via a memoryview and a consumer reads it without an
# intermediate copy.
#
# Layout
# ------
#   header (32 bytes): magic(4s) version(I) slot_count(I) slot_size(I)
#                      head(Q) tail(Q)          [little-endian]
#   slots: slot_count × slot_size bytes, each slot =
#                      length-prefix(Q, 8 bytes) + payload(slot_size - 8)
#
# head/tail are MONOTONIC unsigned counters (not modulo); the live slot
# index is ``counter % slot_count``. count = head - tail; the ring is FULL
# when count == slot_count (capacity == slot_count) and EMPTY when
# count == 0.
# ===========================================================================

import struct as _struct
from multiprocessing import shared_memory as _shared_memory

_ZCRB_MAGIC = b"VZC1"
_ZCRB_VERSION = 1
_ZCRB_HEADER_FMT = "<4sIIIQQ"  # magic, ver, slot_count, slot_size, head, tail
_ZCRB_HEADER_SIZE = _struct.calcsize(_ZCRB_HEADER_FMT)  # 32
_ZCRB_LEN_PREFIX = 8  # per-slot length prefix (struct <Q)


class RingFull(RuntimeError):
    """Raised by emit()/producer_slot() when the ring is at capacity."""


class ZeroCopyRingBuffer:
    """Single-producer/single-consumer zero-copy ring over shared memory."""

    def __init__(self, shm, slot_count: int, slot_size: int, owner: bool):
        self._shm = shm
        self._slot_count = slot_count
        self._slot_size = slot_size
        self._owner = owner
        self._payload_cap = slot_size - _ZCRB_LEN_PREFIX

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, name: str, slot_count: int, slot_size: int) -> "ZeroCopyRingBuffer":
        if slot_count < 1:
            raise ValueError("slot_count must be >= 1")
        if slot_size <= _ZCRB_LEN_PREFIX:
            raise ValueError(f"slot_size must be > {_ZCRB_LEN_PREFIX}")
        total = _ZCRB_HEADER_SIZE + slot_count * slot_size
        # If a stale segment with this name exists, remove it first.
        try:
            stale = _shared_memory.SharedMemory(name=name)
            stale.close()
            stale.unlink()
        except FileNotFoundError:
            pass
        shm = _shared_memory.SharedMemory(name=name, create=True, size=total)
        _struct.pack_into(
            _ZCRB_HEADER_FMT,
            shm.buf,
            0,
            _ZCRB_MAGIC,
            _ZCRB_VERSION,
            slot_count,
            slot_size,
            0,
            0,
        )
        return cls(shm, slot_count, slot_size, owner=True)

    @classmethod
    def attach(cls, name: str) -> "ZeroCopyRingBuffer":
        shm = _shared_memory.SharedMemory(name=name)
        magic, ver, slot_count, slot_size, _head, _tail = _struct.unpack_from(
            _ZCRB_HEADER_FMT, shm.buf, 0
        )
        if magic != _ZCRB_MAGIC:
            shm.close()
            raise ValueError(f"segment {name!r} is not a ZeroCopyRingBuffer")
        return cls(shm, slot_count, slot_size, owner=False)

    # ------------------------------------------------------------------
    # Header accessors
    # ------------------------------------------------------------------

    def _head(self) -> int:
        return _struct.unpack_from("<Q", self._shm.buf, 16)[0]

    def _tail(self) -> int:
        return _struct.unpack_from("<Q", self._shm.buf, 24)[0]

    def _set_head(self, v: int) -> None:
        _struct.pack_into("<Q", self._shm.buf, 16, v)

    def _set_tail(self, v: int) -> None:
        _struct.pack_into("<Q", self._shm.buf, 24, v)

    def _slot_offset(self, counter: int) -> int:
        idx = counter % self._slot_count
        return _ZCRB_HEADER_SIZE + idx * self._slot_size

    # ------------------------------------------------------------------
    # Producer
    # ------------------------------------------------------------------

    def emit(self, data: bytes) -> None:
        if len(data) > self._payload_cap:
            raise ValueError(
                f"payload {len(data)} bytes exceeds slot capacity "
                f"{self._payload_cap} (slot_size {self._slot_size} - "
                f"{_ZCRB_LEN_PREFIX} len prefix)"
            )
        head, tail = self._head(), self._tail()
        if head - tail >= self._slot_count:
            raise RingFull(f"ring full ({self._slot_count} slots)")
        off = self._slot_offset(head)
        _struct.pack_into("<Q", self._shm.buf, off, len(data))
        self._shm.buf[off + _ZCRB_LEN_PREFIX : off + _ZCRB_LEN_PREFIX + len(data)] = (
            data
        )
        self._set_head(head + 1)

    @contextmanager
    def producer_slot(self):
        """Yield a writable memoryview of the next slot's payload region for
        in-place writes. Pair with commit_emit(length) to finalize."""
        head, tail = self._head(), self._tail()
        if head - tail >= self._slot_count:
            raise RingFull(f"ring full ({self._slot_count} slots)")
        off = self._slot_offset(head)
        view = self._shm.buf[off + _ZCRB_LEN_PREFIX : off + self._slot_size]
        try:
            yield view
        finally:
            view.release()

    def commit_emit(self, length: int) -> None:
        if length > self._payload_cap:
            raise ValueError("committed length exceeds slot capacity")
        head = self._head()
        off = self._slot_offset(head)
        _struct.pack_into("<Q", self._shm.buf, off, length)
        self._set_head(head + 1)

    # ------------------------------------------------------------------
    # Consumer
    # ------------------------------------------------------------------

    @contextmanager
    def consumer_slot(self):
        """Yield a readable memoryview of the next unconsumed slot's payload
        (sized to the stored length). Advances the tail on exit."""
        head, tail = self._head(), self._tail()
        if head == tail:
            raise IndexError("ring empty")
        off = self._slot_offset(tail)
        (length,) = _struct.unpack_from("<Q", self._shm.buf, off)
        view = self._shm.buf[off + _ZCRB_LEN_PREFIX : off + _ZCRB_LEN_PREFIX + length]
        try:
            yield view
        finally:
            view.release()
            self._set_tail(tail + 1)

    def consume_iter(self):
        """Yield each currently-available message as bytes, advancing the
        tail. Drains everything queued at call time."""
        while True:
            head, tail = self._head(), self._tail()
            if head == tail:
                return
            off = self._slot_offset(tail)
            (length,) = _struct.unpack_from("<Q", self._shm.buf, off)
            payload = bytes(
                self._shm.buf[off + _ZCRB_LEN_PREFIX : off + _ZCRB_LEN_PREFIX + length]
            )
            self._set_tail(tail + 1)
            yield payload

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._shm.close()
        except Exception:  # noqa: BLE001
            pass

    def unlink(self) -> None:
        try:
            self._shm.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "AuditEvent",
    "VBusRingBuffer",
    "ZeroCopyRingBuffer",
    "RingFull",
    "parse_audit_fail_quote",
    "AUDIT_FAIL_QUOTE_CMD",
    "DEFAULT_DRAIN_INTERVAL_S",
]
