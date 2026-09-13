"""
backend/services/prefetch.py
=============================

Stage 10.3 (Sprint 14.1) — userspace pairing for the kernel model-prefetch
subsystem (kernel/src/mm/ai_prefetch.c).

Purpose
-------

A multi-agent workload's tail latency is dominated by **cold-load** of the
next model. The kernel exposes a per-slot prefetch queue; this service
populates that queue based on:

  (a) explicit "warm" hints from the orchestrator (predicted next model)
  (b) historical co-occurrence (model B was loaded within N seconds after
      model A in K of the last L sessions)

Both signals turn into PREFETCH VBus commands the kernel uses to opportun-
istically map the weights into the destination AI slot's VOS3_KIM region
without rebooting the slot.

Honest scope ceiling
--------------------

The kernel side ships in tree (mm/ai_prefetch.c) and exposes:

    PREFETCH|<slot>|<model_id>          → schedule a warm-up
    PREFETCH_STATUS|<slot>              → returns "READY" | "STAGED" | "FAULTED"
    PREFETCH_CANCEL|<slot>|<model_id>   → revoke a pending warm-up

The historical co-occurrence side of this service is a **heuristic**. We
ship the data collection (record_load + record_unload) and a simple
last-K-sessions Markov scorer. A proper learned-cache predictor (e.g.
LSTM on per-tenant trace) is out of scope for v1.1; the swap point is
``_score_candidates`` — return any ordering you like.

Concurrency
-----------

A single ``threading.RLock`` guards the cooccurrence table. The hot
``record_load`` path holds the lock for microseconds. Prefetch dispatch is
synchronous from the caller's perspective (returns once the kernel ACKs
the queue insert; the actual page-in happens in background on the kernel
side).
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_COOCCURRENCE_WINDOW_S = 60.0  # only count loads within 60s as related
DEFAULT_HISTORY_SESSIONS = 200  # last K sessions kept in memory
DEFAULT_MAX_CANDIDATES = 4  # don't enqueue more than this per call


# ---------------------------------------------------------------------------
# Session trace
# ---------------------------------------------------------------------------


@dataclass
class ModelLoadEvent:
    model_id: str
    slot_id: int
    loaded_at: float
    duration_s: Optional[float] = None  # filled by record_unload


@dataclass
class _SessionTrace:
    events: list[ModelLoadEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prefetch service
# ---------------------------------------------------------------------------


class PrefetchService:
    """Tracks model load history and dispatches prefetch hints.

    Construction
    ------------
        PrefetchService(driver, *, window_s=60.0, history=200)

        driver: object exposing .send_command(str) -> str
                (services.vbus_driver.VBusDriver or test double).
                Pass ``None`` to operate in "predict only, no dispatch"
                mode useful for unit tests + dry runs.

    Public API
    ----------
        record_load(model_id, slot_id) -> ModelLoadEvent
        record_unload(model_id, slot_id) -> None
        predict_next(slot_id, current_model_id) -> list[str]
        prefetch(slot_id, current_model_id, *, max_candidates=4) -> dict
        status() -> dict
    """

    def __init__(
        self,
        driver=None,
        *,
        window_s: float = DEFAULT_COOCCURRENCE_WINDOW_S,
        history: int = DEFAULT_HISTORY_SESSIONS,
    ) -> None:
        self._driver = driver
        self._window_s = float(window_s)
        self._lock = threading.RLock()
        self._current_session = _SessionTrace()
        self._sessions: collections.deque[_SessionTrace] = collections.deque(
            maxlen=history
        )
        # (model_a, model_b) -> count; b loaded within window after a
        self._cooccurrence: collections.Counter = collections.Counter()
        self._dispatched: int = 0
        self._cancelled: int = 0

    # ------------------------------------------------------------------
    # Trace ingestion
    # ------------------------------------------------------------------

    def record_load(self, model_id: str, slot_id: int) -> ModelLoadEvent:
        ev = ModelLoadEvent(
            model_id=model_id, slot_id=int(slot_id), loaded_at=time.time()
        )
        with self._lock:
            # Update cooccurrence: pair this load with every prior load
            # in the current session that happened within window_s.
            for prior in reversed(self._current_session.events):
                if ev.loaded_at - prior.loaded_at > self._window_s:
                    break
                if prior.model_id != model_id:
                    self._cooccurrence[(prior.model_id, model_id)] += 1
            self._current_session.events.append(ev)
        return ev

    def record_unload(self, model_id: str, slot_id: int) -> None:
        now = time.time()
        with self._lock:
            for ev in reversed(self._current_session.events):
                if (
                    ev.model_id == model_id
                    and ev.slot_id == slot_id
                    and ev.duration_s is None
                ):
                    ev.duration_s = now - ev.loaded_at
                    return

    def cycle_session(self) -> None:
        """Close the current session and start a new one. Call on user logout,
        org switch, or hourly tick — anything that means 'the next load is
        unrelated to the prior trace.'"""
        with self._lock:
            if self._current_session.events:
                self._sessions.append(self._current_session)
            self._current_session = _SessionTrace()

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict_next(self, slot_id: int, current_model_id: str) -> list[str]:
        """Return candidate next-model IDs ranked by cooccurrence frequency."""
        with self._lock:
            return self._score_candidates(current_model_id)

    def _score_candidates(self, current: str) -> list[str]:
        # Pull cooccurrence rows where the antecedent matches current.
        rows = [
            (model_b, count)
            for (model_a, model_b), count in self._cooccurrence.items()
            if model_a == current
        ]
        rows.sort(key=lambda pair: pair[1], reverse=True)
        return [model_b for model_b, _ in rows]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def prefetch(
        self,
        slot_id: int,
        current_model_id: str,
        *,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> dict:
        """Predict next models and dispatch PREFETCH commands to the kernel.

        Returns a dict with:
            candidates:  ordered list of predicted model IDs (may be empty)
            dispatched:  list of model IDs actually sent to the kernel
            errors:      mapping model_id -> error string for failures
        """
        candidates = self.predict_next(slot_id, current_model_id)[:max_candidates]
        if self._driver is None or not candidates:
            return {"candidates": candidates, "dispatched": [], "errors": {}}

        dispatched: list[str] = []
        errors: dict[str, str] = {}
        for model_id in candidates:
            cmd = f"PREFETCH|{slot_id}|{model_id}"
            try:
                reply = self._driver.send_command(cmd)
                if reply and reply.startswith("PREFETCH_OK"):
                    dispatched.append(model_id)
                    with self._lock:
                        self._dispatched += 1
                else:
                    errors[model_id] = f"unexpected_reply:{reply!r}"
            except Exception as exc:  # noqa: BLE001
                errors[model_id] = str(exc)
        return {"candidates": candidates, "dispatched": dispatched, "errors": errors}

    def cancel(self, slot_id: int, model_id: str) -> bool:
        if self._driver is None:
            return False
        try:
            reply = self._driver.send_command(f"PREFETCH_CANCEL|{slot_id}|{model_id}")
            if reply and reply.startswith("PREFETCH_OK"):
                with self._lock:
                    self._cancelled += 1
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[prefetch] cancel failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Observers
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return {
                "current_session_events": len(self._current_session.events),
                "history_sessions": len(self._sessions),
                "cooccurrence_pairs": len(self._cooccurrence),
                "dispatched_total": self._dispatched,
                "cancelled_total": self._cancelled,
                "window_s": self._window_s,
            }


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_singleton: Optional[PrefetchService] = None
_singleton_lock = threading.Lock()


def get_prefetch_service(driver=None) -> PrefetchService:
    """Module-level singleton accessor.

    First call binds the driver. Subsequent calls return the existing
    instance regardless of the driver argument — use ``reset_for_tests()``
    to start fresh."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PrefetchService(driver=driver)
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


# ---------------------------------------------------------------------------
# Path-guarded single-file warm — used by the prefetcher to fault model
# bytes into the page cache. A model ref must resolve to a real file UNDER
# an operator-declared allow-root (VOS3_PREFETCH_ALLOW_ROOTS, os.pathsep-
# separated); anything else is refused fail-closed so a poisoned model ref
# (``/etc/passwd``, ``..`` traversal) can never warm arbitrary host files.
# ---------------------------------------------------------------------------

ENV_PREFETCH_ALLOW_ROOTS = "VOS3_PREFETCH_ALLOW_ROOTS"


def _prefetch_allow_roots() -> list[Path]:
    raw = os.environ.get(ENV_PREFETCH_ALLOW_ROOTS, "")
    roots: list[Path] = []
    for tok in raw.split(os.pathsep):
        tok = tok.strip()
        if tok:
            try:
                roots.append(Path(tok).resolve())
            except (OSError, RuntimeError):
                continue
    return roots


def _is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _warm_one(model_ref: str) -> dict:
    """Warm a single model file into the page cache, fail-closed.

    Returns ``{"warmed": bool, "error": str|None, "bytes": int}``. Refuses
    non-filesystem refs (URLs) and any path that resolves outside the
    VOS3_PREFETCH_ALLOW_ROOTS allowlist (including ``..`` traversal, which
    Path.resolve() normalises before the allowlist check)."""
    if not model_ref or "://" in model_ref:
        return {"warmed": False, "error": "not a filesystem path", "bytes": 0}
    try:
        target = Path(model_ref).resolve()
    except (OSError, RuntimeError):
        return {"warmed": False, "error": "not a filesystem path", "bytes": 0}

    roots = _prefetch_allow_roots()
    if not roots or not any(_is_within(target, r) for r in roots):
        return {"warmed": False, "error": "path_outside_allowlist", "bytes": 0}

    if not target.is_file():
        return {"warmed": False, "error": "not a filesystem path", "bytes": 0}

    try:
        data = target.read_bytes()  # fault the bytes in
    except OSError as exc:
        return {"warmed": False, "error": f"read_failed:{exc.errno}", "bytes": 0}
    return {"warmed": True, "error": None, "bytes": len(data)}


__all__ = [
    "ModelLoadEvent",
    "PrefetchService",
    "get_prefetch_service",
    "reset_for_tests",
    "_warm_one",
    "ENV_PREFETCH_ALLOW_ROOTS",
    "DEFAULT_COOCCURRENCE_WINDOW_S",
    "DEFAULT_HISTORY_SESSIONS",
    "DEFAULT_MAX_CANDIDATES",
]
