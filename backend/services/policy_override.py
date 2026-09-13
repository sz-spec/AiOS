"""
backend/services/policy_override.py
====================================

Stage 10.2.2 — Management Control & Logic Flexibility for the Action
Bridge hallucination guardrail (kernel-side; see
``kernel/include/vos/action_bridge.h`` for the underlying primitives).

This module is the *single backend entry point* the management dashboard,
the CEO override CLI, and the env-bootstrapped startup hook all funnel
through. The four contracts it satisfies match the four Stage 10.2.2
deliverables verbatim:

(1) Per-agent / global Policy Override
    set_agent_threshold(slot_id, score) → POLICY_OVERRIDE VBus frame
    set_global_floor(score)              → applies the same threshold to
                                            every slot in one transaction
    No kernel rebuild required.

(2) Dynamic prompt revision (hot-reload of vOS_System_Context.md)
    register_system_context_reload_hook(callback)
    reload_system_context_now()
    The doc itself ships in Stage 13; this module exposes the contract
    today so the file-watcher can be wired the moment it lands.

(3) VOS_FORCE_PERMIT — Safe-Rollout kill switch
    set_force_permit(enabled) → POLICY_FORCE_PERMIT VBus frame
    Read from env at startup via apply_env_at_startup(); env value is the
    boot default, run-time mutations override it until the next restart.
    When ON: kernel still emits an audit event for every would-block
    (under category VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE) but does NOT
    block. Lets the org collect telemetry on what WOULD be blocked
    before flipping enforcement on.

(4) Availability guarantee (no hard crashes; "Review Required" fallback)
    evaluate_confidence(slot_id, reported_score) → ConfidenceDecision
    If the LLM did NOT report a confidence value (None / NaN / out-of-range):
    the decision is REVIEW_REQUIRED — the action does NOT proceed and is
    NOT crashed; instead the caller routes it to the human-in-the-loop
    queue. This keeps the org's 50 agents operational even when one
    agent's confidence-emission contract is malformed.

Standalone module: pure Python, single dependency on
``backend.services.vbus_driver.VBusDriver``. Importable by routes that
exist today (settings / metrics) and by any future compliance route
without requiring the broader Stage 10.3 backend port to land first.
"""

from __future__ import annotations

import enum
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants — match kernel/include/vos/tee.h + audit_ring.h
# ---------------------------------------------------------------------------

VOS3_INTENT_MAX_CONFIDENCE_SCORE = 1000  # 0..1000 fixed-point, see tee.h
VOS3_MODEL_SLOT_MAX = 4  # see ai_guard.h::VOS3_MODEL_SLOT_MAX
# (the active production slot count;
# the _HP variant is for 256MB models
# and is not addressable from the
# confidence gate path).

ENV_FORCE_PERMIT = "VOS_FORCE_PERMIT"
ENV_OVERRIDE_FILE = "VOS_POLICY_OVERRIDE_FILE"
ENV_SYSTEM_CONTEXT_PATH = "VOS_SYSTEM_CONTEXT_PATH"

DEFAULT_OVERRIDE_FILE = "/tmp/vos_policy_overrides.json"
DEFAULT_SYSTEM_CONTEXT_PATH = "docs/vOS_System_Context.md"


# ---------------------------------------------------------------------------
# Decision type returned by evaluate_confidence
# ---------------------------------------------------------------------------


class ConfidenceDecision(enum.Enum):
    """Outcome of a confidence-gate evaluation.

    PERMIT
        Reported score met-or-exceeded the slot's threshold (or the global
        force-permit was on). Action proceeds.

    BLOCK
        Reported score was below the threshold and force-permit was off.
        The kernel-side audit ring already recorded the block under
        VOS3_AUDIT_CAT_HALLUCINATION_BLOCK; the caller should drop the
        action and surface the block reason to the user.

    REVIEW_REQUIRED
        The LLM did not produce a usable confidence value (missing /
        non-numeric / outside 0..1000). The caller MUST route the action
        to the human-in-the-loop queue rather than executing or
        crashing. This is the availability guarantee — no input from
        the model is ever a fatal error for the agent fleet.
    """

    PERMIT = "permit"
    BLOCK = "block"
    REVIEW_REQUIRED = "review_required"


@dataclass
class PolicySnapshot:
    """Read-only view returned by status() — feeds the management dashboard."""

    force_permit: bool
    per_slot_thresholds: dict[int, int]
    global_floor: Optional[int] = None
    system_context_path: Optional[str] = None
    system_context_mtime: Optional[float] = None


# ---------------------------------------------------------------------------
# Helper — interpret the env var leniently. Treat the typical truthy spellings
# as ON; anything else as OFF. Surprising values are not silently dropped —
# they are logged at WARNING so an ops mistake is visible.
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled", ""}


def _parse_bool_env(value: Optional[str], var_name: str) -> bool:
    if value is None:
        return False
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    logger.warning(
        "%s=%r not in {%s,%s}; treating as OFF.",
        var_name,
        value,
        ",".join(sorted(_TRUTHY)),
        ",".join(sorted(_FALSY)),
    )
    return False


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------


class PolicyOverrideService:
    """Backend-side controller for the kernel hallucination guardrail.

    Wraps the VBus driver to expose a typed, race-safe Python API. State
    held here is small (per-slot thresholds + force_permit flag + a small
    set of reload callbacks); the kernel is the source of truth at the
    moment of any check, but this service caches the most-recent push so
    the dashboard does not have to round-trip POLICY_STATUS for every
    read.

    Thread-safety: an internal lock serialises mutations. Reads of cached
    state are lock-free (a single dict copy under the lock when status()
    is called, so callers cannot observe a torn dict).
    """

    def __init__(
        self,
        vbus_driver,  # type: ignore[no-untyped-def]
        *,
        override_file: Optional[str] = None,
        system_context_path: Optional[str] = None,
        compliance_store=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self._driver = vbus_driver
        self._lock = threading.RLock()

        self._force_permit: bool = False
        self._slot_thresholds: dict[int, int] = {
            i: 0 for i in range(VOS3_MODEL_SLOT_MAX)
        }
        self._global_floor: Optional[int] = None

        self._override_file = Path(
            override_file or os.environ.get(ENV_OVERRIDE_FILE, DEFAULT_OVERRIDE_FILE)
        )
        self._system_context_path = Path(
            system_context_path
            or os.environ.get(ENV_SYSTEM_CONTEXT_PATH, DEFAULT_SYSTEM_CONTEXT_PATH)
        )

        self._reload_hooks: list[Callable[[str], None]] = []

        # Stage 10.3 — long-term compliance persistence.
        # Lazy import so module import remains cheap when only the
        # override APIs (without persistence) are used.
        if compliance_store is None:
            from backend.services.compliance_store import get_compliance_store

            compliance_store = get_compliance_store()
        self._store = compliance_store

        # Highest-seq watermark. Updated each drain so re-drains are idempotent.
        self._last_seen_seq: int = self._store.highest_seq()

    # ------------------------------------------------------------------
    # (3) VOS_FORCE_PERMIT — kill switch
    # ------------------------------------------------------------------

    def apply_env_at_startup(self) -> None:
        """Read VOS_FORCE_PERMIT from the process env and push to kernel.

        Idempotent; safe to call from FastAPI lifespan startup. If the
        VBus driver is not yet connected, the call logs and returns —
        the bootstrap loop should re-call once the driver is up.
        """
        env_force_permit = _parse_bool_env(
            os.environ.get(ENV_FORCE_PERMIT), ENV_FORCE_PERMIT
        )
        try:
            self.set_force_permit(env_force_permit)
            logger.info(
                "PolicyOverride: applied %s=%s at startup.",
                ENV_FORCE_PERMIT,
                env_force_permit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PolicyOverride: could not push %s=%s to kernel at startup "
                "(%s). The setting will retry on next mutation.",
                ENV_FORCE_PERMIT,
                env_force_permit,
                exc,
            )

    def set_force_permit(self, enabled: bool) -> None:
        """Toggle Safe-Rollout. Pushes POLICY_FORCE_PERMIT VBus frame."""
        with self._lock:
            self._driver.send_command(f"POLICY_FORCE_PERMIT|{1 if enabled else 0}")
            self._force_permit = bool(enabled)

    # ------------------------------------------------------------------
    # (1) Per-agent / global Policy Override
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_slot(slot_id: int) -> None:
        if (
            not isinstance(slot_id, int)
            or slot_id < 0
            or slot_id >= VOS3_MODEL_SLOT_MAX
        ):
            raise ValueError(
                f"slot_id {slot_id!r} out of range " f"[0..{VOS3_MODEL_SLOT_MAX - 1}]"
            )

    @staticmethod
    def _validate_score(score: int) -> None:
        if (
            not isinstance(score, int)
            or score < 0
            or score > VOS3_INTENT_MAX_CONFIDENCE_SCORE
        ):
            raise ValueError(
                f"score {score!r} out of range "
                f"[0..{VOS3_INTENT_MAX_CONFIDENCE_SCORE}]"
            )

    def set_agent_threshold(self, slot_id: int, score: int) -> None:
        """Direct per-slot override; does NOT touch the TEE measurement chain."""
        self._validate_slot(slot_id)
        self._validate_score(score)
        with self._lock:
            self._driver.send_command(f"POLICY_OVERRIDE|{slot_id}|{score}")
            self._slot_thresholds[slot_id] = score

    def set_global_floor(self, score: int) -> None:
        """Apply the same threshold to every slot in one transaction."""
        self._validate_score(score)
        with self._lock:
            for slot in range(VOS3_MODEL_SLOT_MAX):
                self._driver.send_command(f"POLICY_OVERRIDE|{slot}|{score}")
                self._slot_thresholds[slot] = score
            self._global_floor = score

    def persist_overrides(self) -> None:
        """Write the current per-slot thresholds + force_permit to disk.

        Used as a fallback so an unexpected backend restart re-applies
        the CEO's last decision. The kernel itself never persists across
        reboots — the file here is purely a backend convenience.
        """
        with self._lock:
            payload = {
                "force_permit": self._force_permit,
                "global_floor": self._global_floor,
                "per_slot": {str(k): v for k, v in self._slot_thresholds.items()},
            }
        try:
            self._override_file.parent.mkdir(parents=True, exist_ok=True)
            self._override_file.write_text(json.dumps(payload, indent=2))
        except OSError as exc:
            logger.warning(
                "PolicyOverride: could not persist overrides to %s (%s).",
                self._override_file,
                exc,
            )

    def restore_overrides(self) -> None:
        """Read the persisted file (if any) and re-push to the kernel.

        Tolerant of missing / malformed files — logs and returns rather
        than raising. The agent fleet must remain operational even if
        the overrides file was wiped or corrupted.
        """
        if not self._override_file.exists():
            return
        try:
            payload = json.loads(self._override_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "PolicyOverride: ignoring malformed overrides file %s (%s).",
                self._override_file,
                exc,
            )
            return

        try:
            self.set_force_permit(bool(payload.get("force_permit", False)))
            for slot_str, score in (payload.get("per_slot") or {}).items():
                slot = int(slot_str)
                if 0 <= slot < VOS3_MODEL_SLOT_MAX and isinstance(score, int):
                    self.set_agent_threshold(slot, score)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PolicyOverride: partial restore from %s failed (%s); "
                "manual re-apply may be needed.",
                self._override_file,
                exc,
            )

    # ------------------------------------------------------------------
    # (2) Dynamic prompt revision — hot-reload contract
    # ------------------------------------------------------------------
    #
    # The vOS_System_Context.md document is built in Stage 13. This module
    # holds the contract today so the file-watcher can be wired the moment
    # the doc lands. Pattern: callers register a callback; an external
    # file-watcher (or the management dashboard's "force reload" button)
    # invokes reload_system_context_now() to fan-out to every callback.

    def register_system_context_reload_hook(
        self, callback: Callable[[str], None]
    ) -> None:
        """Register a callable invoked with the new prompt body on reload."""
        with self._lock:
            self._reload_hooks.append(callback)

    def reload_system_context_now(self) -> bool:
        """Read the current vOS_System_Context.md and dispatch to every hook.

        Returns True on success, False if the file was missing or unreadable.
        Does NOT raise — failure to reload must not crash the agent fleet.
        """
        if not self._system_context_path.exists():
            logger.info(
                "PolicyOverride: vOS_System_Context.md not yet present at %s; "
                "skipping reload (Stage 13 deliverable).",
                self._system_context_path,
            )
            return False

        try:
            body = self._system_context_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "PolicyOverride: could not read %s (%s).",
                self._system_context_path,
                exc,
            )
            return False

        with self._lock:
            hooks = list(self._reload_hooks)
        # Dispatch outside the lock so a slow callback doesn't stall mutations.
        for hook in hooks:
            try:
                hook(body)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PolicyOverride: system-context reload hook %r raised %s; "
                    "continuing with remaining hooks.",
                    hook,
                    exc,
                )
        return True

    # ------------------------------------------------------------------
    # (4) Availability guarantee — evaluate_confidence with REVIEW_REQUIRED
    # ------------------------------------------------------------------

    def evaluate_confidence(
        self, slot_id: int, reported_score: Optional[float]
    ) -> ConfidenceDecision:
        """Local pre-check before issuing ACTION_CHECK_CONFIDENCE.

        This wrapper exists so the backend can implement the four-state
        availability guarantee BEFORE a kernel round-trip:

          - score is None / NaN              → REVIEW_REQUIRED
          - score is not in [0, 1.0] (float) → REVIEW_REQUIRED
          - score numerically valid          → forward to kernel; PERMIT
                                                or BLOCK depending on the
                                                kernel's reply.

        The kernel reply branch follows the Stage-10.2 contract:
          ACTION_CHECK_CONFIDENCE → CONF_OK ⇒ PERMIT
                                    CONF_BLOCK ⇒ BLOCK (or PERMIT if
                                    Safe-Rollout was on; the kernel
                                    returned 0 in that case).

        Note: this is a *local* sanity check. The authoritative decision
        is the kernel's (because the kernel's audit ring is in the trust
        boundary). Backend-side BLOCKs are mirrored to logs for
        ops visibility but do NOT bypass the kernel — the kernel still
        sees every action and enforces.
        """
        # Numeric validity gate — the availability guarantee.
        if reported_score is None:
            self._record_review_required(slot_id, reason="no_score")
            return ConfidenceDecision.REVIEW_REQUIRED

        try:
            f = float(reported_score)
        except (TypeError, ValueError):
            self._record_review_required(slot_id, reason="non_numeric_score")
            return ConfidenceDecision.REVIEW_REQUIRED

        if math.isnan(f) or math.isinf(f) or f < 0.0 or f > 1.0:
            self._record_review_required(slot_id, reason="score_out_of_range")
            return ConfidenceDecision.REVIEW_REQUIRED

        # Convert to the kernel's 0..1000 fixed-point. Round-half-up so a
        # clean 0.500 maps to 500.
        kernel_score = int(f * VOS3_INTENT_MAX_CONFIDENCE_SCORE + 0.5)

        try:
            reply = self._driver.send_command(
                f"ACTION_CHECK_CONFIDENCE|{slot_id}|{kernel_score}"
            )
        except Exception as exc:  # noqa: BLE001
            # Kernel unreachable — availability guarantee says we route to
            # human review, not crash. The auditor will see the gap as
            # "no kernel decision recorded" + a backend WARNING.
            logger.warning(
                "PolicyOverride: ACTION_CHECK_CONFIDENCE failed for "
                "slot=%s score=%s (%s); routing to REVIEW_REQUIRED.",
                slot_id,
                reported_score,
                exc,
            )
            self._record_review_required(slot_id, reason="kernel_unreachable")
            return ConfidenceDecision.REVIEW_REQUIRED

        # Reply format from cmd_action_check_confidence (vbus_ai_cmds.c):
        #   permit  → "CONF_OK|slot=N|gate=G|observed=O"
        #   block   → "ERR 13 CONF_BLOCK|slot=N|gate=G|observed=O"
        if "CONF_OK" in reply:
            return ConfidenceDecision.PERMIT
        if "CONF_BLOCK" in reply:
            return ConfidenceDecision.BLOCK
        # Anything else is unexpected — log and degrade safely.
        logger.warning(
            "PolicyOverride: unexpected ACTION_CHECK_CONFIDENCE reply %r; "
            "routing to REVIEW_REQUIRED.",
            reply,
        )
        self._record_review_required(slot_id, reason="unexpected_reply")
        return ConfidenceDecision.REVIEW_REQUIRED

    @staticmethod
    def _record_review_required(slot_id: int, *, reason: str) -> None:
        # Backend-side logging only. The kernel audit ring is intentionally
        # not touched here — its category set is reserved for kernel
        # decisions, and a backend-detected "no score" is a different
        # class of event (the model didn't speak; the kernel didn't
        # reject anything).
        logger.info(
            "PolicyOverride: REVIEW_REQUIRED slot=%s reason=%s",
            slot_id,
            reason,
        )

    # ------------------------------------------------------------------
    # Stage 10.3 — Audit Ring Drain
    # ------------------------------------------------------------------

    def drain_audit_ring(self) -> dict:
        """Pull the kernel's compliance ring and persist new entries.

        Calls AUDIT_FAIL_QUOTE, parses the wire format, inserts every
        seq we haven't seen before into the compliance store. Returns
        a small report dict the dashboard / cron job can read:

            {
              "kernel_total":   <int>,    # kernel monotonic emit count
              "kernel_fill":    <int>,    # entries currently in the ring
              "new_persisted":  <int>,    # newly written rows this drain
              "compliance_gap": <int>,    # entries lost to ring wraparound
                                          # since the last drain (0 if caught up)
            }

        Tolerant of all wire-format errors: malformed entries are
        logged at WARNING and skipped; the drain itself never raises
        on a bad reply (the agent fleet must keep running even if the
        kernel reply path is briefly garbled).
        """
        try:
            reply = self._driver.send_command("AUDIT_FAIL_QUOTE")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PolicyOverride: AUDIT_FAIL_QUOTE failed (%s); "
                "compliance drain skipped this cycle.",
                exc,
            )
            return {
                "kernel_total": -1,
                "kernel_fill": 0,
                "new_persisted": 0,
                "compliance_gap": 0,
            }

        parsed = self._parse_audit_quote_reply(reply)
        if parsed is None:
            return {
                "kernel_total": -1,
                "kernel_fill": 0,
                "new_persisted": 0,
                "compliance_gap": 0,
            }

        kernel_total, kernel_fill, events = parsed

        # Compliance gap: if the kernel has emitted more events than
        # (last seen + currently in ring), some entries were lost to
        # ring wraparound between drains.
        ring_size_kernel = 64  # VOS3_AUDIT_RING_SIZE; matches audit_ring.h
        emitted_since_last = max(0, kernel_total - max(self._last_seen_seq + 1, 0))
        compliance_gap = max(0, emitted_since_last - kernel_fill)
        if compliance_gap > 0:
            logger.warning(
                "PolicyOverride: compliance gap detected — kernel emitted "
                "%d new events since last drain but ring only held %d "
                "(ring size %d). %d entries were lost to wraparound; "
                "tighten drain cadence.",
                emitted_since_last,
                kernel_fill,
                ring_size_kernel,
                compliance_gap,
            )

        # Persist only events newer than what we have on disk; the
        # store also enforces uniqueness via INSERT OR IGNORE on `seq`.
        new_events = [e for e in events if int(e["seq"]) > self._last_seen_seq]
        new_persisted = self._store.append_events(new_events)
        if new_events:
            self._last_seen_seq = max(int(e["seq"]) for e in new_events)

        return {
            "kernel_total": kernel_total,
            "kernel_fill": kernel_fill,
            "new_persisted": new_persisted,
            "compliance_gap": compliance_gap,
        }

    @staticmethod
    def _parse_audit_quote_reply(reply: str) -> Optional[tuple[int, int, list[dict]]]:
        """Parse AUDIT_FAIL_QUOTE wire format defined in vbus_ai_cmds.c.

        Format:
          AUDIT_FAIL|total=<n>|fill=<m>|<seq>:<cat>:<rc>:<slot>:<digest_hex16>|…

        Returns (kernel_total, kernel_fill, events) or None on parse error.
        """
        if not isinstance(reply, str) or not reply:
            logger.warning("PolicyOverride: empty AUDIT_FAIL_QUOTE reply.")
            return None

        # The driver may prepend a status word (e.g. "OK ") — strip it.
        # Look for the AUDIT_FAIL marker explicitly so we are robust to
        # whatever the driver layer adds.
        marker = "AUDIT_FAIL|"
        idx = reply.find(marker)
        if idx < 0:
            logger.warning(
                "PolicyOverride: AUDIT_FAIL marker not found in reply %r.",
                reply[:80],
            )
            return None
        body = reply[idx:]

        parts = body.split("|")
        # Expect at least: ["AUDIT_FAIL", "total=<n>", "fill=<m>", entries...]
        if len(parts) < 3:
            logger.warning("PolicyOverride: malformed reply %r.", reply[:80])
            return None

        try:
            kernel_total = int(parts[1].split("=", 1)[1])
            kernel_fill = int(parts[2].split("=", 1)[1])
        except (IndexError, ValueError) as exc:
            logger.warning(
                "PolicyOverride: could not parse total/fill from %r (%s).",
                parts[1:3],
                exc,
            )
            return None

        events: list[dict] = []
        for entry in parts[3:]:
            if not entry:
                continue
            fields = entry.split(":")
            if len(fields) != 5:
                logger.warning(
                    "PolicyOverride: skipping malformed entry %r "
                    "(expected 5 colon-separated fields, got %d).",
                    entry,
                    len(fields),
                )
                continue
            try:
                events.append(
                    {
                        "seq": int(fields[0]),
                        "category": int(fields[1]),
                        "rc": int(fields[2]),
                        "slot_id": int(fields[3]),
                        "digest_prefix": fields[4],
                        # tick is not on the wire (kernel reply omits it to keep
                        # the line under LINE_MAX). The store records 0 — the
                        # `drained_at` wall-clock is the auditor-relevant field
                        # for "when did the backend learn about this?".
                        "tick": 0,
                    }
                )
            except ValueError as exc:
                logger.warning(
                    "PolicyOverride: dropping non-numeric entry %r (%s).",
                    entry,
                    exc,
                )

        return kernel_total, kernel_fill, events

    def recent_compliance_events(
        self, *, limit: int = 100, category: Optional[int] = None
    ) -> list[dict]:
        """Read-side proxy used by the Sovereign Control Panel."""
        return self._store.recent_events(limit=limit, category=category)

    def category_counts(self) -> dict[int, int]:
        return self._store.category_counts()

    # ------------------------------------------------------------------
    # Read-back for dashboard
    # ------------------------------------------------------------------

    def status(self) -> PolicySnapshot:
        with self._lock:
            mtime: Optional[float] = None
            try:
                mtime = self._system_context_path.stat().st_mtime
            except OSError:
                mtime = None
            return PolicySnapshot(
                force_permit=self._force_permit,
                per_slot_thresholds=dict(self._slot_thresholds),
                global_floor=self._global_floor,
                system_context_path=str(self._system_context_path),
                system_context_mtime=mtime,
            )


# ---------------------------------------------------------------------------
# Module-level singleton accessor — same shape as the rest of backend/services
# ---------------------------------------------------------------------------

_singleton: Optional[PolicyOverrideService] = None
_singleton_lock = threading.Lock()


def get_policy_override_service(vbus_driver=None) -> PolicyOverrideService:
    """Lazy singleton. First caller MUST pass a vbus_driver instance."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            if vbus_driver is None:
                raise RuntimeError(
                    "PolicyOverrideService not initialised. First "
                    "get_policy_override_service() call must pass vbus_driver."
                )
            _singleton = PolicyOverrideService(vbus_driver)
        return _singleton
