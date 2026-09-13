"""
backend/security/capability_table.py
======================================

Sprint 16 / Item A3 — Python port of the kernel per-byte capability
table (kernel/include/vos/capability.h + kernel/src/sec/capability_table.c).

Why this exists
---------------

The kernel implementation is the production enforcement layer. This
Python module is the algorithm twin used by:

  1. Backend services that need to express + check capabilities BEFORE
     the kernel syscall path is wired in Wave 2 (C7 information-flow
     engine, MCP bridge per-tool scope projection).
  2. Unit tests — exercises the exact same overlap-rejection, perm-
     superset, range-containment logic that lives in kernel C, so a
     regression in the algorithm shows up at Python-test latency
     instead of needing a kernel rebuild + QEMU run.

Wire compatibility
------------------

Permission constants, error codes, and the entry layout match the
kernel header bit-for-bit. A capability granted on the Python side
can be serialized + replayed by the kernel-side `vos3_cap_grant`
verbatim (cap_id allocation is the kernel's responsibility; the
Python side mirrors the same monotonic-counter scheme).

Honest scope ceiling
--------------------

  - Just like the kernel software-fallback, this module does NOT
    defend against malicious userspace code that bypasses the
    cap_check() call (i.e. direct memory access without going through
    the engine). It is the policy-evaluation layer, not the
    enforcement layer. Enforcement requires either CHERI silicon or
    a kernel-side PTE-protection layer.
  - Thread-safety: not lock-protected at this layer. Callers that
    share a table across threads MUST wrap operations in their own
    mutex. The kernel side acquires per-slot spinlocks at the caller
    layer (e.g. ai_slots.c slot->lock) for the same reason.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Permission bits — must match kernel/include/vos/capability.h
# ---------------------------------------------------------------------------


class CapPerm(enum.IntFlag):
    READ = 0x01
    WRITE = 0x02
    EXEC = 0x04
    SHARE = 0x08

    RO = READ
    RW = READ | WRITE
    RX = READ | EXEC
    RWX = READ | WRITE | EXEC


# ---------------------------------------------------------------------------
# Error codes — sign-flipped to match kernel negative-errno convention
# ---------------------------------------------------------------------------


class CapError(enum.IntEnum):
    OK = 0
    INVAL = -1
    FULL = -2
    NOTFOUND = -3
    DENIED = -4
    OVERLAP = -5
    NOMEM = -6


class CapException(Exception):
    def __init__(self, code: CapError, message: str = ""):
        self.code = code
        super().__init__(f"{code.name}: {message}" if message else code.name)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


CAP_ID_INVALID = 0


@dataclass
class CapEntry:
    cap_id: int = CAP_ID_INVALID
    owner: int = 0
    perms: int = 0
    base: int = 0
    length: int = 0
    generation: int = 0


# ---------------------------------------------------------------------------
# CapTable — Python twin of vos3_cap_table_t
# ---------------------------------------------------------------------------


def _ranges_overlap(a_base: int, a_len: int, b_base: int, b_len: int) -> bool:
    a_end = a_base + a_len
    b_end = b_base + b_len
    return (a_base < b_end) and (b_base < a_end)


def _range_contains(cap_base: int, cap_len: int, req_base: int, req_len: int) -> bool:
    if req_base < cap_base:
        return False
    if req_base + req_len > cap_base + cap_len:
        return False
    return True


def _perms_are_superset(cap_perms: int, want_perms: int) -> bool:
    return (cap_perms & want_perms) == want_perms


@dataclass
class CapTable:
    max_caps: int = 1024
    entries: list[CapEntry] = field(default_factory=list)
    used: int = 0
    next_cap_id: int = 1

    def __post_init__(self) -> None:
        if self.max_caps <= 0:
            raise CapException(CapError.INVAL, "max_caps must be > 0")
        if not self.entries:
            self.entries = [CapEntry() for _ in range(self.max_caps)]
        elif len(self.entries) != self.max_caps:
            raise CapException(
                CapError.INVAL,
                f"entries length {len(self.entries)} != " f"max_caps {self.max_caps}",
            )

    def grant(self, *, owner: int, base: int, length: int, perms: int) -> int:
        """Return the new cap_id."""
        if length <= 0:
            raise CapException(CapError.INVAL, "length must be > 0")
        if perms == 0:
            raise CapException(CapError.INVAL, "perms must be non-zero")
        if self.used >= self.max_caps:
            raise CapException(CapError.FULL, f"table at capacity {self.max_caps}")

        # Pass 1: overlap check — reject if grant relaxes perms of a different
        # owner's existing cap over the same bytes.
        for e in self.entries:
            if e.cap_id == CAP_ID_INVALID:
                continue
            if e.owner == owner:
                continue
            if not _ranges_overlap(base, length, e.base, e.length):
                continue
            extra = perms & ~e.perms
            if extra != 0:
                raise CapException(
                    CapError.OVERLAP,
                    f"would grant extra perms 0x{extra:x} over different-owner cap {e.cap_id}",
                )

        # Pass 2: first empty slot.
        for e in self.entries:
            if e.cap_id != CAP_ID_INVALID:
                continue
            e.cap_id = self.next_cap_id
            e.owner = owner
            e.perms = perms
            e.base = base
            e.length = length
            e.generation += 1
            cap_id = e.cap_id
            self.next_cap_id += 1
            if self.next_cap_id == 0:
                self.next_cap_id = 1
            self.used += 1
            return cap_id
        # Unreachable given the FULL check above.
        raise CapException(CapError.FULL, "no free slot (table internal corruption)")

    def revoke(self, cap_id: int) -> None:
        if cap_id == CAP_ID_INVALID:
            raise CapException(CapError.NOTFOUND, "cap_id is INVALID")
        for e in self.entries:
            if e.cap_id != cap_id:
                continue
            e.cap_id = CAP_ID_INVALID
            e.owner = 0
            e.perms = 0
            e.base = 0
            e.length = 0
            e.generation += 1
            self.used -= 1
            return
        raise CapException(CapError.NOTFOUND, f"cap_id {cap_id} not in table")

    def check(self, *, base: int, length: int, want_perms: int) -> bool:
        if length <= 0:
            return False
        if want_perms == 0:
            return True  # vacuously permitted

        for e in self.entries:
            if e.cap_id == CAP_ID_INVALID:
                continue
            if not _range_contains(e.base, e.length, base, length):
                continue
            if not _perms_are_superset(e.perms, want_perms):
                continue
            return True
        return False

    def lookup(self, cap_id: int) -> CapEntry:
        if cap_id == CAP_ID_INVALID:
            raise CapException(CapError.NOTFOUND, "cap_id is INVALID")
        for e in self.entries:
            if e.cap_id == cap_id:
                # Return a copy so callers can't mutate table state.
                return CapEntry(
                    cap_id=e.cap_id,
                    owner=e.owner,
                    perms=e.perms,
                    base=e.base,
                    length=e.length,
                    generation=e.generation,
                )
        raise CapException(CapError.NOTFOUND, f"cap_id {cap_id} not in table")

    def iter_live(self) -> list[CapEntry]:
        return [
            CapEntry(
                cap_id=e.cap_id,
                owner=e.owner,
                perms=e.perms,
                base=e.base,
                length=e.length,
                generation=e.generation,
            )
            for e in self.entries
            if e.cap_id != CAP_ID_INVALID
        ]

    def visit(self, visitor: Callable[[CapEntry], int]) -> int:
        for e in self.entries:
            if e.cap_id == CAP_ID_INVALID:
                continue
            rc = visitor(e)
            if rc != 0:
                return rc
        return 0


__all__ = [
    "CapPerm",
    "CapError",
    "CapException",
    "CapEntry",
    "CapTable",
    "CAP_ID_INVALID",
]
