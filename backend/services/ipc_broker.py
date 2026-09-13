"""
backend/services/ipc_broker.py — Phase 33 (G5 cross-process taint propagation)
==============================================================================

`TaintInheritanceBroker`: when a shared-memory segment is mapped between two
processes, propagate the source process's taint color into the target segment's
``taint_colors`` entry — directionally (source → sink), monotonically (a TOXIC
color is NEVER scrubbed during inheritance), and with a cryptographic provenance
record in the Phase-26 transparency ledger. A re-map that tries to DOWNGRADE a
segment's recorded color triggers a ``PROVENANCE_VIOLATION`` and force-closes the
IPC handle.

Honest scope (read before citing G5-propagation as closed)
==========================================================

Naming note: the project's canonical **G5** is the byte-frozen UAPI contract
(``vos3_taint_color_entry`` == 65568, ``vos3_taint_map_key`` == 8) — UNTOUCHED
here. This module is the *cross-process taint-propagation feature* the Phase-33
brief calls "G5"; it does not alter ``taint_maps.h`` or ``kernel/src/mm/``.

The production interceptor is a kernel BPF ``kprobe``/LSM hook on ``shmat`` /
``mmap`` that fires the broker on a real shared-memory map. That in-kernel probe
is NOT shipped here (a new kprobe BPF program needs its own clang-BPF build + a
fresh live-verifier cert — same boundary as the Phase-29 perf_event_open and
Phase-32 bpf_timer producers). This module ships the **userspace propagation
engine + monotonic join + provenance + force-close** with the kernel side
expressed through the EXISTING ``KernelGateConnector`` ``taint_colors`` writes
(MOCK on dev). It advances no moat tally.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from security.kernel_gate_connector import KernelGateConnector, SinkKind, TaintLabel

logger = logging.getLogger("vos3.security.ipc_broker")

PUBLIC = int(TaintLabel.PUBLIC)
TOXIC = int(TaintLabel.TOXIC)


class ProvenanceViolation(Exception):
    """Raised when an IPC re-map attempts to DOWNGRADE (scrub) a segment's
    recorded taint color. The broker force-closes the handle before raising.
    ``.reason`` / ``.seg_id`` carry the audit cause."""

    def __init__(self, reason: str, *, seg_id: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.seg_id = seg_id


@dataclass(frozen=True)
class InheritanceResult:
    seg_id: int
    source_pid: int
    target_pid: int
    source_color: int
    target_color_before: int
    target_color_after: int
    mutated: bool
    provenance_event_id: Optional[str]


class _ColorBuffer:
    """A TaintedBuffer-shaped all-<color> mask used to write a segment's color
    into the kernel ``taint_colors`` map via the existing connector."""

    def __init__(self, color: int, n: int = 64):
        self.content = b"\x00" * n
        self.colors = bytes([color]) * n
        self._sha = hashlib.sha256(self.content + bytes([color])).hexdigest()

    @property
    def sha256(self) -> str:
        return self._sha

    def max_color(self) -> int:
        return max(self.colors) if self.colors else PUBLIC


class TaintInheritanceBroker:
    """Cross-process taint propagation over shared-memory maps."""

    def __init__(
        self,
        *,
        gate_connector: Optional[KernelGateConnector] = None,
        ledger=None,
    ) -> None:
        self._conn = gate_connector or KernelGateConnector(force_mock=True)
        self._ledger = ledger
        self._lock = threading.RLock()
        # (pid, seg_id) -> recorded color (the broker's provenance registry).
        self._recorded: Dict[Tuple[int, int], int] = {}

    # -- kernel taint_colors I/O (via the existing connector) --------------

    def _color_of(self, pid: int, seg_id: int) -> int:
        """Authoritative current color of (pid, seg_id): the kernel taint_colors
        entry if present, else the broker's recorded value, else PUBLIC."""
        entry = self._conn.peek_entry(fd=seg_id, pid=pid)
        if entry is not None:
            return int(entry["max_color"])
        return self._recorded.get((pid, seg_id), PUBLIC)

    def _push_color(self, pid: int, seg_id: int, color: int) -> None:
        self._conn.push_tainted_buffer(
            fd=seg_id,
            pid=pid,
            buffer=_ColorBuffer(color),
            sink_kind=SinkKind.NETWORK_EGRESS,
        )
        self._recorded[(pid, seg_id)] = color

    def _log(self, event_type: str, metadata: dict) -> Optional[str]:
        """Cryptographic provenance: record into the Phase-26 transparency ledger
        if one is injected, else the best-effort flag-gated singleton path."""
        canonical = "|".join(f"{k}={metadata[k]}" for k in sorted(metadata)).encode(
            "utf-8"
        )
        schema_hash = hashlib.sha256(canonical).hexdigest()
        try:
            if self._ledger is not None:
                rec = self._ledger.record(
                    event_type=event_type, schema_hash=schema_hash, metadata=metadata
                )
                return rec["event_id"]
            from services.policy_transparency import record_policy_event

            return record_policy_event(
                event_type=event_type, schema_hash=schema_hash, metadata=metadata
            )
        except Exception as exc:  # noqa: BLE001 — provenance logging must not break IPC
            logger.warning("[ipc-broker] provenance log failed: %s", exc)
            return None

    # -- public API --------------------------------------------------------

    def register_segment(self, *, seg_id: int, owner_pid: int, color: int) -> None:
        """Establish a segment's SOURCE taint (e.g., the producer process colored
        the buffer). Writes the color into the kernel taint_colors map."""
        with self._lock:
            self._push_color(owner_pid, seg_id, int(color))

    def map_segment(
        self, *, seg_id: int, source_pid: int, target_pid: int
    ) -> InheritanceResult:
        """Propagate the source's taint into the target on a shared-memory map.

        Directional (source → sink) + monotonic: the target's new color is
        ``max(source_color, target_current)``, so a TOXIC inheritance can never be
        scrubbed and a clean→clean map mutates nothing. Records a provenance event."""
        with self._lock:
            src = self._color_of(source_pid, seg_id)
            before = self._color_of(target_pid, seg_id)
            after = max(src, before)  # monotonic join — never scrub TOXIC
            mutated = after != before
            if mutated:
                self._push_color(target_pid, seg_id, after)
            else:
                # Keep the registry consistent even when unchanged.
                self._recorded[(target_pid, seg_id)] = after
            event_id = self._log(
                "ipc_taint_inheritance",
                {
                    "seg_id": seg_id,
                    "source_pid": source_pid,
                    "target_pid": target_pid,
                    "source_color": src,
                    "target_before": before,
                    "target_after": after,
                    "mutated": mutated,
                },
            )
            logger.info(
                "[SECURITY][ipc-broker] inherit seg=%d %d->%d color %d->%d mutated=%s",
                seg_id,
                source_pid,
                target_pid,
                before,
                after,
                mutated,
            )
            return InheritanceResult(
                seg_id=seg_id,
                source_pid=source_pid,
                target_pid=target_pid,
                source_color=src,
                target_color_before=before,
                target_color_after=after,
                mutated=mutated,
                provenance_event_id=event_id,
            )

    def remap_segment(self, *, seg_id: int, pid: int, claimed_color: int) -> int:
        """Re-map an existing segment with a claimed color. A claim LOWER than the
        recorded color is a scrub attempt → PROVENANCE_VIOLATION + force-close.
        A claim >= recorded is accepted (monotonic up). Returns the effective
        color."""
        with self._lock:
            recorded = self._color_of(pid, seg_id)
            if int(claimed_color) < recorded:
                self._log(
                    "ipc_provenance_violation",
                    {
                        "seg_id": seg_id,
                        "pid": pid,
                        "recorded_color": recorded,
                        "claimed_color": int(claimed_color),
                    },
                )
                logger.critical(
                    "[SECURITY_CRITICAL][ipc-broker] PROVENANCE_VIOLATION seg=%d pid=%d "
                    "recorded=%d claimed=%d -> force-close",
                    seg_id,
                    pid,
                    recorded,
                    int(claimed_color),
                )
                # Force-close the IPC handle before raising (fail-closed).
                self._close_locked(seg_id=seg_id, pid=pid)
                raise ProvenanceViolation(
                    f"taint scrub attempt on seg {seg_id} (recorded={recorded} "
                    f"claimed={claimed_color})",
                    seg_id=seg_id,
                )
            effective = max(recorded, int(claimed_color))
            if effective != recorded:
                self._push_color(pid, seg_id, effective)
            return effective

    def close_segment(self, *, seg_id: int, pid: int) -> bool:
        """Prune the segment's taint maps on IPC close. Returns True iff an entry
        was removed."""
        with self._lock:
            return self._close_locked(seg_id=seg_id, pid=pid)

    def _close_locked(self, *, seg_id: int, pid: int) -> bool:
        removed = self._conn.evict(fd=seg_id, pid=pid)
        self._recorded.pop((pid, seg_id), None)
        return removed

    def recorded_color(self, *, seg_id: int, pid: int) -> int:
        with self._lock:
            return self._color_of(pid, seg_id)


__all__ = [
    "ProvenanceViolation",
    "InheritanceResult",
    "TaintInheritanceBroker",
]
