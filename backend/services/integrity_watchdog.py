"""
backend/services/integrity_watchdog.py — Phase 32 (Gap G6)
===========================================================

Runtime integrity watchdog: periodically samples the kernel text-segment
measurement, maintains a rolling hash, and FAILS CLOSED into a "Safe-Lock" state
(all egress disabled) on any drift from the boot baseline — detecting
hot-patching / JIT-injected code. The latest verified hash is bound into the
Phase-31 attestation quote so a remote verifier gets proof the kernel has not
been tampered with since boot.

Honest scope (read before citing G6 as closed)
===============================================

The production sampler is a kernel BPF program: a non-blocking ``bpf_timer``
firing every 500 ms that walks the kernel ``.text`` with ``bpf_probe_read_kernel``
and folds a rolling hash. That in-kernel program is NOT shipped here — a brand-new
``bpf_timer`` text-hasher would need its own clang-BPF build + a fresh live
verifier certification, and hashing the full ``.text`` inside a single timer tick
is not feasible without a multi-tick chunked design. So, exactly like the Phase-29
NPU side-channel (perf_event_open producer deferred), this module ships the
**userspace orchestration + drift-detection + Safe-Lock + attestation-binding**
layer with an injectable ``text_source`` (production swaps the BPF reader behind
it). On dev the source is a stable measurement representation — no real kernel
memory is read, and no ``kernel/src/mm/`` mapping is touched.

The watchdog never modifies ``taint_gate.c``: "disable all egress" is expressed by
the Safe-Lock flag that the existing Phase-24 egress chokepoint
(``dispatch_agent_response``) consults — a fail-closed deny, the right layer.

This advances no moat tally (the integrity moat row is INV-6-gated on an external
audit).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("vos3.security.integrity_watchdog")

DEFAULT_INTERVAL_S = 0.5  # 500 ms, matching the bpf_timer cadence


def _default_text_source() -> bytes:
    """Dev representation of the kernel ``.text`` measurement (stable, so the
    baseline matches subsequent samples and there is no false drift). Production
    swaps a ``bpf_probe_read_kernel`` ``.text`` sampler behind this seam."""
    kh = os.environ.get("VOS3_KERNEL_SHA256", "") or "dev-stable-ktext"
    return ("vos-ktext-v1|" + kh).encode("utf-8")


@dataclass(frozen=True)
class IntegrityStatus:
    ok: bool
    current_hash: str
    baseline_hash: Optional[str]
    drift: bool


class CriticalIntegrityViolation(Exception):
    """Raised when a runtime integrity drift is detected. The watchdog engages
    Safe-Lock (all egress denied) and emits a CRITICAL audit before raising.
    ``.reason`` carries the audit cause."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_audit_sink(record: dict) -> None:
    crit = record.get("marker") == "CRITICAL_INTEGRITY_VIOLATION"
    logger.log(
        logging.CRITICAL if crit else logging.INFO,
        "[SECURITY_CRITICAL][integrity-watchdog] %s current=%s baseline=%s",
        record.get("marker"),
        (record.get("current_hash") or "")[:16],
        (record.get("baseline_hash") or "")[:16],
    )


class RuntimeIntegrityWatchdog:
    """Periodic kernel-text integrity watchdog with fail-closed Safe-Lock."""

    def __init__(
        self,
        *,
        text_source: Callable[[], bytes] = _default_text_source,
        interval_s: float = DEFAULT_INTERVAL_S,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._src = text_source
        self._interval = float(interval_s)
        self._sink = audit_sink or _default_audit_sink
        self._lock = threading.RLock()
        self._baseline: Optional[str] = None
        self._current: str = ""
        self._safe_locked = False
        self._lock_reason = ""
        self._samples = 0
        self._timer: Optional[threading.Timer] = None
        self.establish_baseline()

    # -- sampling ----------------------------------------------------------

    def _hash(self) -> str:
        """Hash the current text source. Never raises (a source error keeps the
        last hash — the periodic loop must not crash)."""
        try:
            data = self._src()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[integrity-watchdog] text source raised (kept last): %s", exc
            )
            return self._current
        return hashlib.sha256(data).hexdigest()

    def establish_baseline(self) -> str:
        with self._lock:
            h = self._hash()
            self._baseline = h
            self._current = h
            self._samples += 1
            return h

    def sample(self) -> str:
        with self._lock:
            self._current = self._hash()
            self._samples += 1
            return self._current

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    def runtime_integrity_hash(self) -> str:
        with self._lock:
            return self._current

    @property
    def baseline_hash(self) -> Optional[str]:
        with self._lock:
            return self._baseline

    # -- check + fail-closed ----------------------------------------------

    def check(self, *, raise_on_violation: bool = True) -> IntegrityStatus:
        """Sample + compare to baseline. On drift: engage Safe-Lock, emit a
        CRITICAL audit, and (by default) raise CriticalIntegrityViolation. The
        periodic loop calls with raise_on_violation=False so the thread survives
        while still locking down."""
        with self._lock:
            cur = self._hash()
            self._current = cur
            self._samples += 1
            drift = self._baseline is not None and cur != self._baseline
            status = IntegrityStatus(
                ok=not drift,
                current_hash=cur,
                baseline_hash=self._baseline,
                drift=drift,
            )
            if drift:
                self._engage_safe_lock_locked(
                    f"kernel .text hash drift baseline={self._baseline[:16]} "
                    f"current={cur[:16]}"
                )
        if drift:
            self._audit("CRITICAL_INTEGRITY_VIOLATION", status)
            if raise_on_violation:
                raise CriticalIntegrityViolation(self._lock_reason)
        return status

    def _audit(self, marker: str, status: IntegrityStatus) -> None:
        try:
            self._sink(
                {
                    "marker": marker,
                    "current_hash": status.current_hash,
                    "baseline_hash": status.baseline_hash,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[integrity-watchdog] audit sink raised (dropped): %s", exc)

    # -- Safe-Lock ---------------------------------------------------------

    def is_safe_locked(self) -> bool:
        with self._lock:
            return self._safe_locked

    @property
    def safe_lock_reason(self) -> str:
        with self._lock:
            return self._lock_reason

    def _engage_safe_lock_locked(self, reason: str) -> None:
        # caller holds self._lock
        if not self._safe_locked:
            self._safe_locked = True
            self._lock_reason = reason

    def engage_safe_lock(self, reason: str) -> None:
        with self._lock:
            self._engage_safe_lock_locked(reason)

    def clear_safe_lock(self, *, operator_attestation: str) -> None:
        """Operator override: clear Safe-Lock and re-establish the baseline.
        Requires a non-empty attestation string (an explicit human action), so a
        lockdown can never be cleared accidentally / programmatically by default."""
        if not operator_attestation or not operator_attestation.strip():
            raise ValueError(
                "clear_safe_lock requires a non-empty operator_attestation"
            )
        with self._lock:
            self._safe_locked = False
            self._lock_reason = ""
            h = self._hash()
            self._baseline = h
            self._current = h

    # -- periodic loop -----------------------------------------------------

    def start(self) -> None:
        """Start the non-blocking 500 ms periodic check (background timer)."""
        with self._lock:
            if self._timer is not None:
                return
            self._schedule_locked()

    def _schedule_locked(self) -> None:
        t = threading.Timer(self._interval, self._tick)
        t.daemon = True
        self._timer = t
        t.start()

    def _tick(self) -> None:
        try:
            self.check(raise_on_violation=False)
        finally:
            with self._lock:
                if self._timer is not None:  # reschedule unless stopped
                    self._schedule_locked()

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # -- attestation binding (Phase 31) -----------------------------------

    def attest_runtime_integrity(self, service, *, nonce: bytes):
        """Periodic attestation: produce an EK-signed quote that BINDS the most
        recent RuntimeIntegrityHash, so the remote verifier sees both the boot
        state (G7) and the live integrity hash (G6)."""
        return service.attest(
            nonce=nonce, runtime_integrity_hash=self.runtime_integrity_hash()
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (consulted by the egress chokepoint for Safe-Lock).
# ---------------------------------------------------------------------------

_WATCHDOG_SINGLETON: Optional[RuntimeIntegrityWatchdog] = None
_SINGLETON_LOCK = threading.Lock()


def get_integrity_watchdog() -> RuntimeIntegrityWatchdog:
    global _WATCHDOG_SINGLETON
    if _WATCHDOG_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _WATCHDOG_SINGLETON is None:
                _WATCHDOG_SINGLETON = RuntimeIntegrityWatchdog()
    return _WATCHDOG_SINGLETON


def egress_safe_locked() -> tuple[bool, str]:
    """Fast read used by the egress chokepoint: (locked, reason). Never
    constructs nor mutates the watchdog beyond first-use init; if the watchdog is
    uninitialised it reports unlocked (no false lockdown at import time)."""
    wd = _WATCHDOG_SINGLETON
    if wd is None:
        return (False, "")
    return (wd.is_safe_locked(), wd.safe_lock_reason)


def _reset_singleton_for_tests() -> None:
    global _WATCHDOG_SINGLETON
    with _SINGLETON_LOCK:
        if _WATCHDOG_SINGLETON is not None:
            _WATCHDOG_SINGLETON.stop()
        _WATCHDOG_SINGLETON = None


__all__ = [
    "DEFAULT_INTERVAL_S",
    "IntegrityStatus",
    "CriticalIntegrityViolation",
    "RuntimeIntegrityWatchdog",
    "get_integrity_watchdog",
    "egress_safe_locked",
]
