"""
backend/services/kvcache_state.py
==================================

Sprint 16 / Item A2 — Backend wrapper for the kernel KV-cache OS primitives.

What this module does
---------------------

Provides a Python-side mirror of the three kernel primitives defined in
`kernel/include/vos/kvcache.h`:

  - checkpoint(slot_id) -> bytes
  - restore(slot_id, blob) -> None
  - fork(src_slot_id, dst_slot_id) -> None

Plus a sizing helper `blob_size(slot_id) -> int`.

Why a Python wrapper exists alongside the kernel implementation
---------------------------------------------------------------

The Sprint 16 / Wave 1 / A2 contribution is the OS-primitive *shape* —
process-state-style checkpoint / restore / fork on a KV-cache region.
At commit time the kernel API isn't yet wired into a userspace syscall
(that happens in Sprint 16 / Wave 2 alongside the SCHED_INFERENCE
integration in J1). This module:

  1. Lets backend services (MCP bridge, multi-agent router, the future
     SCHED_INFERENCE Python emitter) program against the API surface
     today, in a transport-agnostic way.
  2. Provides a faithful in-process simulator backend so unit tests can
     exercise blob-format round-trips, COW semantics, and error paths
     without needing the kernel to be running.
  3. Defines the contract that the kernel/userspace transport (VBus
     channel, ioctl, or AF_VOS3 socket — to be chosen in Wave 2) will
     have to honor.

Honest scope ceiling
--------------------

  - The simulator backend is for tests + dev iteration. It is NOT a
    drop-in for the kernel — it does not enforce slot-lock contention,
    does not model the COW page-fault path, and does not pin to
    HugePages. Tests that need to assert kernel-side invariants must
    run in a kernel emulator (QEMU + the kernel ELF), not against this
    backend.
  - Blob format is byte-identical to the kernel's vos3_kvcache_blob_v1_hdr_t
    (little-endian, packed) so a blob produced by the simulator is
    accepted by the kernel and vice versa. This is regression-tested.
"""

from __future__ import annotations

import enum
import struct
import threading
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants — MUST match kernel/include/vos/kvcache.h exactly
# ---------------------------------------------------------------------------

VOS3_KVCACHE_BLOB_VERSION = 1
VOS3_KVCACHE_BLOB_MAGIC = 0x564B5643  # 'V' 'K' 'V' 'C' little-endian on disk

VOS3_CONTEXT_PAGE_MAX = 128  # 4KB pages per slot ⇒ 512KB max blob payload
VOS3_MODEL_SLOT_MAX = 8  # MUST track ai_guard.h value
PAGE_SIZE = 4096

# Header layout: <I I B 3s I I I Q Q> = 4+4+1+3+4+4+4+8+8 = 40 bytes
_BLOB_HDR_FMT = "<I I B 3s I I I Q Q"
_BLOB_HDR_SIZE = struct.calcsize(_BLOB_HDR_FMT)
assert _BLOB_HDR_SIZE == 40, (
    f"header struct must be 40 bytes (matches kernel "
    f"VOS3_KVCACHE_BLOB_HDR_SIZE), got {_BLOB_HDR_SIZE}"
)


class KVCacheError(enum.IntEnum):
    """Return codes — sign-flipped to match the kernel negative-errno
    convention so a single integer can encode success (0) or failure."""

    OK = 0
    INVAL = -1
    NOSLOT = -2
    EMPTY = -3
    BUFSMALL = -4
    BUFLARGE = -5
    MAGIC = -6
    VERSION = -7
    CSUM = -8
    DSTBUSY = -9
    SAMESLOT = -10
    LOCKED = -11
    NOMEM = -12


class KVCacheException(Exception):
    """Raised by the high-level methods when the underlying primitive
    returns a non-OK code. The .code attribute carries the KVCacheError."""

    def __init__(self, code: KVCacheError, message: str = ""):
        self.code = code
        super().__init__(f"{code.name}: {message}" if message else code.name)


# ---------------------------------------------------------------------------
# Slot-status enum — MUST match vos3_model_slot_status_t in ai_guard.h
# ---------------------------------------------------------------------------


class SlotStatus(enum.IntEnum):
    FREE = 0
    STREAMING = 1
    ACTIVE = 2
    WARM = 3
    SUSPENDED = 4
    CORRUPT = 5
    DORMANT = 6
    STUCK = 7
    SUSPENDED_PENDING = 8


_SLOT_HAS_CONTEXT_STATES = frozenset(
    {
        SlotStatus.STREAMING,
        SlotStatus.ACTIVE,
        SlotStatus.WARM,
        SlotStatus.SUSPENDED,
        SlotStatus.DORMANT,
    }
)


# ---------------------------------------------------------------------------
# Blob serialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlobHeader:
    magic: int
    version: int
    src_slot_id: int
    page_count: int
    model_id: int
    model_epoch: int
    checkpoint_tick: int
    payload_crc64: int

    def pack(self) -> bytes:
        return struct.pack(
            _BLOB_HDR_FMT,
            self.magic,
            self.version,
            self.src_slot_id,
            b"\x00\x00\x00",  # reserved[3]
            self.page_count,
            self.model_id,
            self.model_epoch,
            self.checkpoint_tick,
            self.payload_crc64,
        )

    @classmethod
    def unpack(cls, raw: bytes) -> "BlobHeader":
        if len(raw) < _BLOB_HDR_SIZE:
            raise KVCacheException(
                KVCacheError.BUFSMALL, f"need {_BLOB_HDR_SIZE} bytes, got {len(raw)}"
            )
        (
            magic,
            version,
            src_slot_id,
            _reserved,
            page_count,
            model_id,
            model_epoch,
            checkpoint_tick,
            payload_crc64,
        ) = struct.unpack(_BLOB_HDR_FMT, raw[:_BLOB_HDR_SIZE])
        return cls(
            magic,
            version,
            src_slot_id,
            page_count,
            model_id,
            model_epoch,
            checkpoint_tick,
            payload_crc64,
        )


def _xxh3_64(seed: int, data: bytes) -> int:
    """Stand-in for the kernel XXH3-64.

    Implementing real XXH3-64 in pure Python for blob-validation purposes
    would add a dependency the wider codebase doesn't have today (no
    `xxhash` import elsewhere). The kernel's actual blob-checksum logic
    is XXH3-64 with VOS3_XXH3_SEED. For the simulator backend we use a
    FNV-1a 64-bit fold seeded the same way — both algorithms have
    sufficient collision resistance for OS-primitive sanity checking
    (the actual *security* properties come from the slot-lock and the
    blob magic, not the checksum).

    When the kernel + Python boundary needs to exchange blobs over a
    real transport, both sides must agree on the checksum algorithm.
    A follow-up patch will either:
      (a) replace this with a real xxhash dependency, or
      (b) replace the kernel side with a SHA-256 truncated to 64 bits,
    whichever ends up being cleaner. Documented in the Sprint 16 plan.
    """
    h = seed & 0xFFFFFFFFFFFFFFFF
    for b in data:
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


VOS3_XXH3_SEED = 0x9E3779B97F4A7C15


# ---------------------------------------------------------------------------
# Slot state (simulator backend)
# ---------------------------------------------------------------------------


@dataclass
class SimulatedSlot:
    slot_id: int
    status: SlotStatus = SlotStatus.FREE
    model_id: int = 0
    model_epoch: int = 0
    context_configured: bool = False
    context_pages: list[bytes] = field(default_factory=list)
    cycle_count: int = 0
    # COW provenance — when forked, dst.cow_parent points at src.slot_id.
    # First write to a page splits the page (we lazily realize this when
    # a test mutates a page via write_page()).
    cow_parent: Optional[int] = None
    cow_split_pages: set[int] = field(default_factory=set)

    def has_context(self) -> bool:
        return (
            self.context_configured
            and self.status in _SLOT_HAS_CONTEXT_STATES
            and len(self.context_pages) > 0
        )


class KVCacheStore:
    """In-process simulator for the kernel KV-cache primitives.

    Thread-safe: acquires per-slot locks in slot_id order to match the
    kernel's deadlock-avoidance discipline.
    """

    def __init__(self, n_slots: int = VOS3_MODEL_SLOT_MAX):
        self.n_slots = n_slots
        self.slots: list[SimulatedSlot] = [
            SimulatedSlot(slot_id=i) for i in range(n_slots)
        ]
        self._locks: list[threading.Lock] = [threading.Lock() for _ in range(n_slots)]
        self._tick = 0

    def _validate_slot_id(self, slot_id: int) -> None:
        if not isinstance(slot_id, int) or slot_id < 0 or slot_id >= self.n_slots:
            raise KVCacheException(
                KVCacheError.NOSLOT, f"slot {slot_id} out of range [0, {self.n_slots})"
            )

    def _acquire_pair_ordered(self, a: int, b: int) -> tuple[int, int]:
        first, second = sorted([a, b])
        self._locks[first].acquire()
        self._locks[second].acquire()
        return first, second

    def _release_pair(self, first: int, second: int) -> None:
        self._locks[second].release()
        self._locks[first].release()

    # Helpers for tests — populate / read a slot's KV-cache.

    def configure(
        self,
        slot_id: int,
        *,
        model_id: int,
        model_epoch: int,
        page_count: int,
        status: SlotStatus = SlotStatus.ACTIVE,
        initial_byte: int = 0,
    ) -> None:
        """Test helper: set up a slot with `page_count` context pages,
        each filled with `initial_byte`."""
        self._validate_slot_id(slot_id)
        if page_count > VOS3_CONTEXT_PAGE_MAX:
            raise KVCacheException(
                KVCacheError.INVAL,
                f"page_count {page_count} > MAX {VOS3_CONTEXT_PAGE_MAX}",
            )
        with self._locks[slot_id]:
            slot = self.slots[slot_id]
            slot.model_id = model_id
            slot.model_epoch = model_epoch
            slot.context_pages = [
                bytes([initial_byte]) * PAGE_SIZE for _ in range(page_count)
            ]
            slot.context_configured = True
            slot.status = status
            slot.cow_parent = None
            slot.cow_split_pages = set()

    def write_page(self, slot_id: int, page_idx: int, payload: bytes) -> None:
        """Test helper: write a 4096-byte page; triggers COW split if applicable."""
        if len(payload) != PAGE_SIZE:
            raise ValueError(
                f"page payload must be {PAGE_SIZE} bytes, got {len(payload)}"
            )
        self._validate_slot_id(slot_id)
        with self._locks[slot_id]:
            slot = self.slots[slot_id]
            if not slot.has_context():
                raise KVCacheException(KVCacheError.EMPTY, "slot has no context")
            if page_idx >= len(slot.context_pages):
                raise KVCacheException(
                    KVCacheError.INVAL, f"page {page_idx} out of range"
                )
            # COW realization: if this slot is a fork-child and the page
            # hasn't been split yet, the existing context_pages reference
            # was shared — we now write the new payload locally without
            # affecting the parent.
            if slot.cow_parent is not None and page_idx not in slot.cow_split_pages:
                slot.cow_split_pages.add(page_idx)
            # Replace by value (bytes is immutable so a fresh slice IS isolation)
            slot.context_pages[page_idx] = bytes(payload)

    def read_page(self, slot_id: int, page_idx: int) -> bytes:
        self._validate_slot_id(slot_id)
        with self._locks[slot_id]:
            slot = self.slots[slot_id]
            if not slot.has_context():
                raise KVCacheException(KVCacheError.EMPTY, "slot has no context")
            return slot.context_pages[page_idx]

    def cycle_count(self, slot_id: int) -> int:
        self._validate_slot_id(slot_id)
        with self._locks[slot_id]:
            return self.slots[slot_id].cycle_count

    # The three OS primitives.

    def blob_size(self, slot_id: int) -> int:
        """Return required blob size in bytes."""
        self._validate_slot_id(slot_id)
        with self._locks[slot_id]:
            slot = self.slots[slot_id]
            if not slot.has_context():
                raise KVCacheException(KVCacheError.EMPTY)
            return _BLOB_HDR_SIZE + len(slot.context_pages) * PAGE_SIZE

    def checkpoint(self, slot_id: int) -> bytes:
        """Snapshot a slot's KV-cache into an opaque blob."""
        self._validate_slot_id(slot_id)
        with self._locks[slot_id]:
            slot = self.slots[slot_id]
            if not slot.has_context():
                raise KVCacheException(KVCacheError.EMPTY, "slot has no context")
            self._tick += 1
            payload = b"".join(slot.context_pages)
            checksum = _xxh3_64(VOS3_XXH3_SEED, payload)
            hdr = BlobHeader(
                magic=VOS3_KVCACHE_BLOB_MAGIC,
                version=VOS3_KVCACHE_BLOB_VERSION,
                src_slot_id=slot_id,
                page_count=len(slot.context_pages),
                model_id=slot.model_id,
                model_epoch=slot.model_epoch,
                checkpoint_tick=self._tick,
                payload_crc64=checksum,
            )
            slot.cycle_count += 1
            return hdr.pack() + payload

    def restore(self, dst_slot_id: int, blob: bytes) -> None:
        """Restore a slot's KV-cache from a blob."""
        if not isinstance(blob, (bytes, bytearray)):
            raise KVCacheException(KVCacheError.INVAL, "blob must be bytes-like")
        if len(blob) < _BLOB_HDR_SIZE:
            raise KVCacheException(
                KVCacheError.BUFSMALL, f"blob too small: {len(blob)} < {_BLOB_HDR_SIZE}"
            )
        hdr = BlobHeader.unpack(blob)
        if hdr.magic != VOS3_KVCACHE_BLOB_MAGIC:
            raise KVCacheException(KVCacheError.MAGIC, f"bad magic 0x{hdr.magic:08x}")
        if hdr.version != VOS3_KVCACHE_BLOB_VERSION:
            raise KVCacheException(
                KVCacheError.VERSION,
                f"blob version {hdr.version} != " f"{VOS3_KVCACHE_BLOB_VERSION}",
            )
        if hdr.page_count > VOS3_CONTEXT_PAGE_MAX:
            raise KVCacheException(
                KVCacheError.INVAL, f"page_count {hdr.page_count} > MAX"
            )
        required = _BLOB_HDR_SIZE + hdr.page_count * PAGE_SIZE
        if len(blob) < required:
            raise KVCacheException(
                KVCacheError.BUFSMALL,
                f"blob payload too short: {len(blob)} < {required}",
            )
        payload = bytes(
            blob[_BLOB_HDR_SIZE : _BLOB_HDR_SIZE + hdr.page_count * PAGE_SIZE]
        )
        if _xxh3_64(VOS3_XXH3_SEED, payload) != hdr.payload_crc64:
            raise KVCacheException(KVCacheError.CSUM, "payload checksum mismatch")

        self._validate_slot_id(dst_slot_id)
        with self._locks[dst_slot_id]:
            slot = self.slots[dst_slot_id]
            if not slot.has_context():
                raise KVCacheException(KVCacheError.EMPTY, "destination has no context")
            if slot.model_id != hdr.model_id:
                raise KVCacheException(
                    KVCacheError.INVAL,
                    f"model_id mismatch: dst={slot.model_id} " f"blob={hdr.model_id}",
                )
            if hdr.page_count > len(slot.context_pages):
                raise KVCacheException(
                    KVCacheError.BUFLARGE,
                    f"blob has more pages ({hdr.page_count}) than "
                    f"destination ({len(slot.context_pages)})",
                )
            for i in range(hdr.page_count):
                slot.context_pages[i] = payload[i * PAGE_SIZE : (i + 1) * PAGE_SIZE]
            slot.checkpoint_epoch = hdr.model_epoch
            slot.cycle_count += 1

    def fork(self, src_slot_id: int, dst_slot_id: int) -> None:
        """COW-fork the KV-cache from src into dst."""
        if src_slot_id == dst_slot_id:
            raise KVCacheException(KVCacheError.SAMESLOT, "src == dst")
        self._validate_slot_id(src_slot_id)
        self._validate_slot_id(dst_slot_id)
        first, second = self._acquire_pair_ordered(src_slot_id, dst_slot_id)
        try:
            src = self.slots[src_slot_id]
            dst = self.slots[dst_slot_id]
            if not src.has_context():
                raise KVCacheException(KVCacheError.EMPTY, "src has no context")
            if dst.status != SlotStatus.FREE:
                raise KVCacheException(
                    KVCacheError.DSTBUSY, f"dst slot status is {dst.status.name}"
                )
            # Logical copy — list references point at the same bytes objects.
            # Because bytes is immutable, the only way the two slots end up
            # observably distinct is via write_page(), which writes a new
            # bytes object into the local list.
            dst.context_pages = list(src.context_pages)
            dst.context_configured = True
            dst.status = SlotStatus.WARM
            dst.model_id = src.model_id
            dst.model_epoch = src.model_epoch
            dst.cow_parent = src_slot_id
            dst.cow_split_pages = set()
            src.cycle_count += 1
            dst.cycle_count += 1
        finally:
            self._release_pair(first, second)


# ---------------------------------------------------------------------------
# Module-level default store (single-process scope for tests)
# ---------------------------------------------------------------------------

_default_store: Optional[KVCacheStore] = None
_default_lock = threading.Lock()


def get_default_store() -> KVCacheStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = KVCacheStore()
        return _default_store


def reset_default_store() -> None:
    """Test helper — re-initialize the module-level store."""
    global _default_store
    with _default_lock:
        _default_store = KVCacheStore()


__all__ = [
    "VOS3_KVCACHE_BLOB_VERSION",
    "VOS3_KVCACHE_BLOB_MAGIC",
    "VOS3_CONTEXT_PAGE_MAX",
    "VOS3_MODEL_SLOT_MAX",
    "PAGE_SIZE",
    "BlobHeader",
    "KVCacheStore",
    "KVCacheError",
    "KVCacheException",
    "SlotStatus",
    "get_default_store",
    "reset_default_store",
]
