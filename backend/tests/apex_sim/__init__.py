"""
backend/tests/apex_sim
======================

Sprint 14.1 — Apex Simulator harness.

What this is
------------

A pure-Python simulator that **stands in for the kernel** so the 40
Stage-10 tests that were blocked on "missing files / no kernel
attached" can run in CI. The harness exposes the VBus driver interface
(``send_command(line) -> reply``), backed by an in-memory state machine
that mirrors the kernel-side behavior described in
``docs/CYBER_OVERLAY_INTEGRATION.md`` §2.

Scope of the simulation
-----------------------

  • AUDIT_FAIL_QUOTE   — emits synthetic events from a configurable queue
  • POLICY_OVERRIDE    — stores threshold, returns POLICY_OK
  • POLICY_FORCE_PERMIT— toggles flag, returns POLICY_OK
  • POLICY_STATUS      — returns the simulated policy state
  • ACTION_CHECK_CONFIDENCE — applies the threshold from POLICY_OVERRIDE
  • INTENT_SUBMIT      — returns INTENT_OK with a deterministic hex digest
  • TEE_ENV / TEE_QUOTE — returns a fixed dev-cert envelope
  • TPM_SEAL / TPM_UNSEAL — XOR with a fixed wrap key (NOT real TPM)
  • PREFETCH / PREFETCH_CANCEL / PREFETCH_STATUS — counter only

What this does NOT simulate
---------------------------

  • Hardware-rooted attestation (no RTMR extends — the digest is just
    SHA-384 of the inputs)
  • Hyperthread-sibling scheduling (no SCHED_CORE; tests that need it
    are tagged @pytest.mark.requires_silicon and SKIP in CI)
  • Memory-mapped warp zones (the kernel maps 4×16MB zones; the sim
    returns synthetic bytes via the shared-memory stub in this module)

The honest-scope ceiling is: tests that pass under apex_sim verify the
**control flow + wire format + service composition**. They do NOT
verify hardware semantics. Per Sprint 14.2 (Gap 3), the same 40 tests
will be promoted to the silicon CI runner on Azure DCedsv6, where real
hardware confirms the kernel-side enforcement.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simulated kernel state
# ---------------------------------------------------------------------------


@dataclass
class _SimState:
    force_permit: bool = False
    per_slot_thresholds: dict[int, int] = field(default_factory=dict)
    global_floor: Optional[int] = None
    audit_ring_total: int = 0
    audit_events: list[dict] = field(default_factory=list)  # FIFO; bounded at 64
    audit_ring_max: int = 64
    audit_seq_next: int = 1
    prefetch_dispatched: int = 0
    intent_count: int = 0
    tpm_wrap_key: bytes = b"\x42" * 32


class ApexSimDriver:
    """In-memory VBus driver replacement.

    Use anywhere a real ``VBusDriver`` is expected. Conforms to the same
    ``.send_command(line) -> reply`` contract. Thread-safe.

    Test usage::

        from tests.apex_sim import ApexSimDriver

        driver = ApexSimDriver()
        driver.inject_audit_event(category=0x10, rc=2, slot_id=0, digest="ab"*8)

        reply = driver.send_command("AUDIT_FAIL_QUOTE")
        assert reply.startswith("AUDIT_FAIL|")
    """

    def __init__(self) -> None:
        self._state = _SimState()
        self._lock = threading.RLock()
        self._handlers: dict[str, callable] = {
            "AUDIT_FAIL_QUOTE": self._h_audit_quote,
            "POLICY_OVERRIDE": self._h_policy_override,
            "POLICY_FORCE_PERMIT": self._h_policy_force_permit,
            "POLICY_STATUS": self._h_policy_status,
            "ACTION_CHECK_CONFIDENCE": self._h_action_check,
            "INTENT_SUBMIT": self._h_intent_submit,
            "TEE_ENV": self._h_tee_env,
            "TEE_QUOTE": self._h_tee_quote,
            "TPM_SEAL": self._h_tpm_seal,
            "TPM_UNSEAL": self._h_tpm_unseal,
            "PREFETCH": self._h_prefetch,
            "PREFETCH_CANCEL": self._h_prefetch_cancel,
            "PREFETCH_STATUS": self._h_prefetch_status,
            "PING": lambda _args: "PONG",
            "SYSTEM_INFO": lambda _args: "SYSTEM|apex_sim|v1",
        }

    # ------------------------------------------------------------------
    # Driver interface
    # ------------------------------------------------------------------

    def send_command(self, line: str) -> str:
        if not isinstance(line, str) or not line:
            return "ERR 1 EMPTY"
        cmd, _, rest = line.partition("|")
        args = rest.split("|") if rest else []
        handler = self._handlers.get(cmd)
        if handler is None:
            return f"ERR 1 UNKNOWN_CMD {cmd}"
        try:
            return handler(args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[apex_sim] handler %s raised: %s", cmd, exc)
            return f"ERR 2 HANDLER_FAILURE {exc}"

    # ------------------------------------------------------------------
    # Test injection — for setting up state before send_command
    # ------------------------------------------------------------------

    def inject_audit_event(
        self, *, category: int, rc: int, slot_id: int, digest: str
    ) -> int:
        """Synthesize an audit event. Returns its seq number."""
        with self._lock:
            seq = self._state.audit_seq_next
            self._state.audit_seq_next += 1
            self._state.audit_ring_total += 1
            self._state.audit_events.append(
                {
                    "seq": seq,
                    "cat": category,
                    "rc": rc,
                    "slot_id": slot_id,
                    "digest": digest,
                }
            )
            # Bounded ring: drop oldest if over capacity
            if len(self._state.audit_events) > self._state.audit_ring_max:
                self._state.audit_events.pop(0)
            return seq

    def set_audit_ring_max(self, max_entries: int) -> None:
        with self._lock:
            self._state.audit_ring_max = int(max_entries)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "force_permit": self._state.force_permit,
                "per_slot_thresholds": dict(self._state.per_slot_thresholds),
                "global_floor": self._state.global_floor,
                "audit_ring_total": self._state.audit_ring_total,
                "audit_fill": len(self._state.audit_events),
                "intent_count": self._state.intent_count,
                "prefetch_dispatched": self._state.prefetch_dispatched,
            }

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _h_audit_quote(self, _args: list[str]) -> str:
        with self._lock:
            total = self._state.audit_ring_total
            events = list(self._state.audit_events)
            # Drain semantic: AUDIT_FAIL_QUOTE empties the ring per Stage 10.1
            self._state.audit_events.clear()
        parts = [f"AUDIT_FAIL|total={total}|fill={len(events)}"]
        for ev in events:
            parts.append(
                f"{ev['seq']}:{ev['cat']}:{ev['rc']}:{ev['slot_id']}:{ev['digest']}"
            )
        return "|".join(parts)

    def _h_policy_override(self, args: list[str]) -> str:
        if len(args) != 2:
            return "ERR 3 BAD_ARGS POLICY_OVERRIDE"
        try:
            slot = int(args[0])
            score = int(args[1])
        except ValueError:
            return "ERR 4 BAD_INT"
        if not (0 <= score <= 1000):
            return "ERR 5 OUT_OF_RANGE"
        with self._lock:
            self._state.per_slot_thresholds[slot] = score
        return f"POLICY_OK|slot={slot}|gate={score}"

    def _h_policy_force_permit(self, args: list[str]) -> str:
        if len(args) != 1:
            return "ERR 3 BAD_ARGS POLICY_FORCE_PERMIT"
        enabled = args[0].strip() in {"1", "true", "on", "yes"}
        with self._lock:
            self._state.force_permit = enabled
        return f"POLICY_OK|force_permit={1 if enabled else 0}"

    def _h_policy_status(self, _args: list[str]) -> str:
        with self._lock:
            gates = ",".join(
                f"{slot}:{score}"
                for slot, score in sorted(self._state.per_slot_thresholds.items())
            )
        return (
            f"POLICY|force_permit={1 if self._state.force_permit else 0}|gates={gates}"
        )

    def _h_action_check(self, args: list[str]) -> str:
        if len(args) != 2:
            return "ERR 3 BAD_ARGS ACTION_CHECK_CONFIDENCE"
        try:
            slot = int(args[0])
            score = int(args[1])
        except ValueError:
            return "ERR 4 BAD_INT"
        with self._lock:
            if self._state.force_permit:
                return f"CONF_OK|slot={slot}|score={score}|forced=1"
            threshold = self._state.per_slot_thresholds.get(
                slot, self._state.global_floor or 0
            )
            if score >= threshold:
                return f"CONF_OK|slot={slot}|score={score}|threshold={threshold}"
            return f"ERR 13 CONF_BLOCK|slot={slot}|score={score}|threshold={threshold}"

    def _h_intent_submit(self, args: list[str]) -> str:
        if not args:
            return "ERR 3 BAD_ARGS INTENT_SUBMIT"
        hex_payload = args[0]
        try:
            payload = bytes.fromhex(hex_payload)
        except ValueError:
            return "ERR 4 BAD_HEX"
        digest = hashlib.sha384(payload).hexdigest()  # 96 hex chars
        with self._lock:
            self._state.intent_count += 1
            bound = len(args) > 1
        prefix = "INTENT_BOUND" if bound else "INTENT_OK"
        return f"{prefix}|{digest}"

    def _h_tee_env(self, _args: list[str]) -> str:
        return "TEE_ENV|APEX_SIM"

    def _h_tee_quote(self, _args: list[str]) -> str:
        return "TEE_QUOTE|apex-sim-not-real-attestation"

    def _h_tpm_seal(self, args: list[str]) -> str:
        if not args:
            return "ERR 3 BAD_ARGS TPM_SEAL"
        try:
            payload = bytes.fromhex(args[0])
        except ValueError:
            return "ERR 4 BAD_HEX"
        key = self._state.tpm_wrap_key
        wrapped = bytes(b ^ key[i % len(key)] for i, b in enumerate(payload))
        return f"TPM_SEALED|{wrapped.hex()}"

    def _h_tpm_unseal(self, args: list[str]) -> str:
        if not args:
            return "ERR 3 BAD_ARGS TPM_UNSEAL"
        try:
            wrapped = bytes.fromhex(args[0])
        except ValueError:
            return "ERR 4 BAD_HEX"
        key = self._state.tpm_wrap_key
        payload = bytes(b ^ key[i % len(key)] for i, b in enumerate(wrapped))
        return f"TPM_UNSEALED|{payload.hex()}"

    def _h_prefetch(self, args: list[str]) -> str:
        if len(args) != 2:
            return "ERR 3 BAD_ARGS PREFETCH"
        with self._lock:
            self._state.prefetch_dispatched += 1
        return f"PREFETCH_OK|slot={args[0]}|model={args[1]}"

    def _h_prefetch_cancel(self, args: list[str]) -> str:
        if len(args) != 2:
            return "ERR 3 BAD_ARGS PREFETCH_CANCEL"
        return f"PREFETCH_OK|slot={args[0]}|cancelled={args[1]}"

    def _h_prefetch_status(self, args: list[str]) -> str:
        if len(args) != 1:
            return "ERR 3 BAD_ARGS PREFETCH_STATUS"
        return f"PREFETCH_STATUS|slot={args[0]}|state=READY"


# ---------------------------------------------------------------------------
# Pytest fixture (importable from tests/conftest.py if desired)
# ---------------------------------------------------------------------------


def make_driver() -> ApexSimDriver:
    """Helper for tests: returns a fresh ApexSimDriver."""
    return ApexSimDriver()


__all__ = ["ApexSimDriver", "make_driver"]
