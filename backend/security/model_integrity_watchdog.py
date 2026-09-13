"""
backend/security/model_integrity_watchdog.py
=============================================

Sprint 23 (DEPTH) — Runtime Model-Integrity Watchdog.

Accounting note (READ FIRST — no count inflation)
-------------------------------------------------

This module is **DEPTH**, not a new catalog row. It does NOT increment the
moat (which stays 49/80). It is the *runtime* counterpart to I2
(``backend/services/maif_v2.py``), which proves a model's provenance at
SLOT_START (load time). Nothing in the existing stack re-verifies the
weights once they are resident in memory — that gap is what this closes,
as a strengthening of the existing I-category integrity posture.

What this is
------------

After a model is loaded into a (shared) inference region, its bytes can
drift: a co-tenant out-of-bounds write, a DMA corruption that slips past
the IOMMU (E3 is the *prevention* layer; this is *detection*), a
rowhammer-class bit flip (A5 is research-only and unfixable — this only
*detects* the effect), or a malicious patch. The watchdog periodically
re-checksums each registered model-memory region against a baseline taken
at registration, and **fail-closes** the moment any region drifts: it
evicts the region and raises ``ModelIntegrityCompromised``. A gate that
refuses to keep serving tampered weights is enforcement; a log line is
not.

Enforcement contract
--------------------

    wd = ModelIntegrityWatchdog(memory_source=src)   # src: region_key -> bytes
    wd.register_region(model_id="llama-4", layer_id="blk.0.attn",
                       baseline=weights_bytes)
    ...
    wd.scan(model_id="llama-4")          # re-checksum every region of the model;
                                         # raises ModelIntegrityCompromised +
                                         # evicts the drifted region(s) on mismatch
    wd.require_integrity("llama-4")      # gate to call BEFORE an inference step

``memory_source`` is a callable ``(model_id, layer_id) -> bytes`` (or a
mapping keyed by ``(model_id, layer_id)``) that returns the CURRENT bytes
of a region. In production it wraps the mmap'd / shared-memory view of the
weights; in tests (and on this macOS dev host) it is an in-memory dict the
test mutates to simulate drift — so the whole gate is exercised with zero
Linux/GPU/shared-memory dependency.

Honest scope ceilings
--------------------

  - This DETECTS + EVICTS; it does not PREVENT the write (that is the E3
    IOMMU/DMA guard + hardware). It is the integrity-monitoring layer.
  - Checksums use ``hashlib.blake2b`` (fast, in-tree, no new dep). The
    production swap to ``xxhash`` (faster, non-cryptographic — adequate for
    drift detection) and binding the baseline into the TEE RTMR are
    follow-ups; the algorithm twin here is what those will measure against.
  - ``scan`` is synchronous + caller-driven via ``tick()`` (a scheduler /
    asyncio loop / cron invokes it). No background thread lives in the
    module, which keeps the gate deterministic and unit-testable.
  - The dev escape hatch ``VOS3_MODEL_INTEGRITY_DEV_OVERRIDE=1`` downgrades
    a detected drift to a loud WARNING (region NOT evicted, no raise) — for
    local dev where a region source is intentionally volatile. Never set it
    in production.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)

ENV_DEV_OVERRIDE = "VOS3_MODEL_INTEGRITY_DEV_OVERRIDE"

# memory_source may be either a callable or a plain mapping keyed by
# (model_id, layer_id).
MemorySource = Union[
    Callable[[str, str], Optional[bytes]],
    dict,
]


def _checksum(data: bytes) -> str:
    """Region checksum. blake2b stands in for the production xxhash; both
    serve drift detection (collision-resistance is a bonus, not required)."""
    return hashlib.blake2b(data, digest_size=32).hexdigest()


class RegionState(enum.IntEnum):
    OK = 0
    EVICTED = 1  # drift detected → region dropped from active serving


@dataclass(frozen=True)
class RegionReport:
    model_id: str
    layer_id: str
    state: RegionState
    baseline_checksum: str
    current_checksum: str
    reason: str

    @property
    def intact(self) -> bool:
        return (
            self.state == RegionState.OK
            and self.baseline_checksum == self.current_checksum
        )


class ModelIntegrityCompromised(Exception):
    """Raised by scan()/require_integrity() when a registered model-memory
    region no longer matches its baseline checksum. Fail-closed."""

    def __init__(self, reports: tuple):
        self.reports = reports  # tuple[RegionReport, ...] of the drifted regions
        drifted = ", ".join(f"{r.model_id}/{r.layer_id}" for r in reports)
        super().__init__(f"model integrity compromised: bit-drift in {drifted}")


@dataclass
class WatchdogStats:
    regions_registered: int = 0
    scans: int = 0
    verified: int = 0  # region checks that matched baseline
    drift_detected: int = 0  # region checks that mismatched
    evictions: int = 0
    dev_overrides_used: int = 0


@dataclass
class _Region:
    model_id: str
    layer_id: str
    baseline_checksum: str
    state: RegionState = RegionState.OK


@dataclass
class ModelIntegrityWatchdog:
    """Fail-closed runtime weight-integrity monitor."""

    memory_source: MemorySource
    stats: WatchdogStats = field(default_factory=WatchdogStats)
    _regions: dict = field(default_factory=dict)  # (model_id, layer_id) -> _Region
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_region(
        self, *, model_id: str, layer_id: str, baseline: bytes
    ) -> RegionReport:
        """Record the baseline checksum for a model-memory region. The
        baseline is the trusted state captured right after I2/MAIF v2
        cleared the load."""
        if not model_id or not layer_id:
            raise ValueError("model_id and layer_id are required")
        if not isinstance(baseline, (bytes, bytearray)):
            raise TypeError("baseline must be bytes-like")
        chk = _checksum(bytes(baseline))
        with self._lock:
            self._regions[(model_id, layer_id)] = _Region(
                model_id=model_id,
                layer_id=layer_id,
                baseline_checksum=chk,
            )
            self.stats.regions_registered += 1
        return RegionReport(
            model_id=model_id,
            layer_id=layer_id,
            state=RegionState.OK,
            baseline_checksum=chk,
            current_checksum=chk,
            reason="baseline registered",
        )

    # ------------------------------------------------------------------
    # Current-bytes lookup via the injected source
    # ------------------------------------------------------------------

    def _read_current(self, model_id: str, layer_id: str) -> Optional[bytes]:
        src = self.memory_source
        try:
            if callable(src):
                return src(model_id, layer_id)
            return src.get((model_id, layer_id))
        except Exception as exc:  # noqa: BLE001 - a source failure = no proof
            logger.warning(
                "[model_integrity] memory_source raised for %s/%s: %r",
                model_id,
                layer_id,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Scan / verify (fail-closed)
    # ------------------------------------------------------------------

    def _check_region(self, region: _Region) -> RegionReport:
        current = self._read_current(region.model_id, region.layer_id)
        if current is None:
            # Source can't produce the bytes → treat as drift (fail-closed:
            # an unverifiable region is not a trusted region).
            cur_chk = ""
            reason = "region bytes unavailable from memory_source"
            matched = False
        else:
            cur_chk = _checksum(bytes(current))
            matched = cur_chk == region.baseline_checksum
            reason = "checksum match" if matched else "checksum MISMATCH (bit-drift)"
        state = RegionState.OK if matched else RegionState.EVICTED
        return RegionReport(
            model_id=region.model_id,
            layer_id=region.layer_id,
            state=state,
            baseline_checksum=region.baseline_checksum,
            current_checksum=cur_chk,
            reason=reason,
        )

    def scan(self, model_id: Optional[str] = None) -> tuple:
        """Re-checksum every registered region (optionally filtered to one
        ``model_id``). Returns a tuple of RegionReport for the regions
        checked. On ANY drift: evict the drifted region(s) and raise
        ModelIntegrityCompromised — UNLESS the dev override is set, in which
        case the drift is logged loudly and the region is left in place."""
        with self._lock:
            self.stats.scans += 1
            targets = [
                r
                for r in self._regions.values()
                if model_id is None or r.model_id == model_id
            ]

        reports: list = []
        drifted: list = []
        for region in targets:
            rep = self._check_region(region)
            reports.append(rep)
            with self._lock:
                if rep.intact:
                    self.stats.verified += 1
                else:
                    self.stats.drift_detected += 1
                    drifted.append(rep)

        if not drifted:
            return tuple(reports)

        if self._env_true(ENV_DEV_OVERRIDE):
            with self._lock:
                self.stats.dev_overrides_used += 1
            logger.warning(
                "[model_integrity] DRIFT DETECTED in %d region(s) but allowed "
                "by DEV OVERRIDE (%s). NEVER set this in production — tampered "
                "weights would keep serving. Regions: %s",
                len(drifted),
                ENV_DEV_OVERRIDE,
                ", ".join(f"{r.model_id}/{r.layer_id}" for r in drifted),
            )
            return tuple(reports)

        # Fail-closed: evict the drifted regions, then raise.
        with self._lock:
            for rep in drifted:
                region = self._regions.get((rep.model_id, rep.layer_id))
                if region is not None:
                    region.state = RegionState.EVICTED
                    self._regions.pop((rep.model_id, rep.layer_id), None)
                    self.stats.evictions += 1
        logger.error(
            "[model_integrity] EVICTED %d region(s) on bit-drift (fail-closed): %s",
            len(drifted),
            ", ".join(f"{r.model_id}/{r.layer_id}" for r in drifted),
        )
        raise ModelIntegrityCompromised(tuple(drifted))

    def require_integrity(self, model_id: str) -> tuple:
        """Gate to call before an inference step. Returns the scan reports
        if all of ``model_id``'s regions are intact; raises
        ModelIntegrityCompromised otherwise (fail-closed). If the model has
        no registered regions, raises ValueError — you must register before
        gating (refusing silently would be a false sense of safety)."""
        with self._lock:
            has_any = any(r.model_id == model_id for r in self._regions.values())
        if not has_any:
            raise ValueError(
                f"no registered regions for model {model_id!r}; register before "
                f"gating integrity"
            )
        return self.scan(model_id)

    def tick(self) -> tuple:
        """Scheduler entry point — scan ALL registered regions. Drives the
        periodic watchdog from an asyncio loop / cron. Propagates
        ModelIntegrityCompromised so the caller can react (alert, kill slot)."""
        return self.scan(None)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def is_registered(self, model_id: str, layer_id: str) -> bool:
        with self._lock:
            return (model_id, layer_id) in self._regions

    def active_regions(self) -> tuple:
        with self._lock:
            return tuple((r.model_id, r.layer_id) for r in self._regions.values())

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "MemorySource",
    "RegionState",
    "RegionReport",
    "ModelIntegrityCompromised",
    "WatchdogStats",
    "ModelIntegrityWatchdog",
    "ENV_DEV_OVERRIDE",
]
