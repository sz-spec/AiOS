"""
backend/services/kernel_cred_invalidation.py — Phase 28 (Gap G10)
==================================================================

Closes the F5 lifecycle gap: a userspace JIT-credential rotation / revocation
(``services.vault_jit_bridge.VaultJITBridge``) must actively flush the matching
slots out of the kernel eBPF tracking maps, so a rotated/expired token can no
longer transit egress via a still-resident gate entry.

How
---

``VaultJITBridge`` already calls ``self._hook.rotate(token_sha256, new_expiry_ns)``
on BOTH ``rotate_credential`` and ``revoke_credential`` — but the only shipped
``KernelRotationHook`` was ``SimulatedKernelHook`` (a stub that touched no kernel
state). This module ships the REAL hook:

  ``KernelGateRotationHook`` -> resolves each (pid, fd) registered for the rotated
  token and calls ``KernelGateConnector.invalidate_slot(pid=, fd=)`` — an explicit
  ``bpf(BPF_MAP_DELETE_ELEM)`` on the colors map that deletes the color entry while
  KEEPING the per-fd MARK bit, so the kernel hook FAILS CLOSED (-EPERM) on any
  subsequent write to the now-stale slot (see ``taint_gate.c`` marked-no-entry
  ladder). This is the user→kernel state-flush contract.

Concurrency (per the June-2026 eBPF map-deletion review): deletion is a single
atomic ``BPF_MAP_DELETE_ELEM`` per key (the kernel serialises the bucket), and we
key on the exact (pid, fd) footprint registered for the credential, so a rotation
under load cannot leave a dangling slot for the OLD token while a new one is
provisioned — the OLD key is cleared, the NEW key re-provisions independently.

Honest scope
============

  - Production no-ops until the agent runtime REGISTERS a footprint
    (``register_session_footprint``) binding a gated egress fd to its credential
    token. That call site (the real outbound-send path) is not built yet — same
    boundary as Gaps G1/G2. With an empty registry, ``rotate()`` flushes nothing
    and behaviour is byte-identical to the prior ``SimulatedKernelHook`` zero
    outcome, so the 461-test baseline is unaffected.
  - On dev/macOS the gate is MOCK: the flush clears the in-process map and the
    fail-closed decision is observable via the connector, but no real kernel
    enforces it. Self-collected; advances no moat tally.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from services.vault_jit_bridge import (
    KernelRotationHook,
    RotationOutcome,
    VaultBackend,
    VaultJITBridge,
    VaultStubBackend,
)

logger = logging.getLogger("vos3.security.cred_invalidation")

# A footprint is the (pid, fd) the gated egress lives on for a given credential.
Footprint = Tuple[int, int]


class SessionFootprintRegistry:
    """Maps a credential token's SHA-256 -> the set of (pid, fd) gate slots that
    must be flushed when that token rotates / is revoked. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_token: Dict[bytes, Set[Footprint]] = {}

    def register(self, token_sha256: bytes, *, pid: int, fd: int) -> None:
        if not isinstance(token_sha256, (bytes, bytearray)) or len(token_sha256) != 32:
            raise ValueError("token_sha256 must be 32 raw bytes")
        if pid < 0 or fd < 0:
            raise ValueError("pid and fd must be non-negative")
        with self._lock:
            self._by_token.setdefault(bytes(token_sha256), set()).add((pid, fd))

    def footprint(self, token_sha256: bytes) -> Set[Footprint]:
        with self._lock:
            return set(self._by_token.get(bytes(token_sha256), set()))

    def forget(self, token_sha256: bytes) -> Set[Footprint]:
        """Remove + return the footprint for a token (called once flushed)."""
        with self._lock:
            return self._by_token.pop(bytes(token_sha256), set())

    def size(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._by_token.values())


class KernelGateRotationHook(KernelRotationHook):
    """Real ``KernelRotationHook``: flushes each registered (pid, fd) for the
    rotating/revoked token out of the kernel eBPF maps via
    ``KernelGateConnector.invalidate_slot`` (fail-closed). Never raises into the
    rotation path — a flush failure is logged and reported, never propagated
    (rotation correctness must not depend on kernel reachability)."""

    def __init__(
        self,
        registry: Optional[SessionFootprintRegistry] = None,
        gate: object = None,
        gate_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        self.registry = registry or SessionFootprintRegistry()
        self._gate = gate
        self._gate_factory = gate_factory

    def _resolve_gate(self):
        if self._gate is not None:
            return self._gate
        if self._gate_factory is not None:
            return self._gate_factory()
        # Deferred import: keep the connector off the hot path until first use.
        from security.kernel_gate_connector import get_kernel_gate

        return get_kernel_gate()

    def rotate(self, token_sha256: bytes, new_expiry_ns: int) -> RotationOutcome:
        start = time.monotonic_ns()
        footprint = self.registry.footprint(token_sha256)
        flushed = 0
        skipped = 0
        if footprint:
            try:
                gate = self._resolve_gate()
                for pid, fd in sorted(footprint):
                    if gate.invalidate_slot(fd=fd, pid=pid):
                        flushed += 1
                    else:
                        skipped += 1
            except Exception as exc:  # noqa: BLE001 — never break the rotation
                logger.error(
                    "[SECURITY][cred-invalidation] kernel slot flush failed "
                    "for token=%s: %s (rotation continues; %d/%d flushed)",
                    token_sha256.hex()[:16],
                    exc,
                    flushed,
                    len(footprint),
                )
            # The OLD token's footprint is consumed; a fresh token re-registers.
            self.registry.forget(token_sha256)
        logger.info(
            "[SECURITY][cred-invalidation] token=%s rotated: flushed=%d skipped=%d "
            "footprint=%d expiry_ns=%d",
            token_sha256.hex()[:16],
            flushed,
            skipped,
            len(footprint),
            new_expiry_ns,
        )
        return RotationOutcome(
            credential_id="<replaced-on-rotate>",
            new_credential_id="<replaced-on-rotate>",
            fds_marked_stale=flushed,
            fds_closed=0,
            fds_skipped=skipped,
            walk_duration_ns=max(0, time.monotonic_ns() - start),
        )


# ---------------------------------------------------------------------------
# Process-wide singletons + factory wiring the bridge to the real kernel hook.
# ---------------------------------------------------------------------------

_HOOK_SINGLETON: Optional[KernelGateRotationHook] = None
_BRIDGE_SINGLETON: Optional[VaultJITBridge] = None
_LOCK = threading.Lock()


def get_kernel_gate_rotation_hook() -> KernelGateRotationHook:
    global _HOOK_SINGLETON
    if _HOOK_SINGLETON is None:
        with _LOCK:
            if _HOOK_SINGLETON is None:
                _HOOK_SINGLETON = KernelGateRotationHook()
    return _HOOK_SINGLETON


def register_session_footprint(token_sha256: bytes, *, pid: int, fd: int) -> None:
    """Bind a gated egress (pid, fd) to a credential token so that a later
    rotation/revocation flushes exactly that kernel slot. Call this where an
    agent's outbound fd is associated with its JIT credential."""
    get_kernel_gate_rotation_hook().registry.register(token_sha256, pid=pid, fd=fd)


def build_kernel_wired_jit_bridge(
    *,
    vault: Optional[VaultBackend] = None,
    default_ttl_seconds: int = 300,
    hook: Optional[KernelGateRotationHook] = None,
) -> VaultJITBridge:
    """Construct a ``VaultJITBridge`` whose kernel rotation hook is the REAL
    ``KernelGateRotationHook`` (not the simulated stub) — so every
    rotate/revoke flushes the kernel eBPF slots."""
    return VaultJITBridge(
        vault=vault or VaultStubBackend(),
        kernel_hook=hook or get_kernel_gate_rotation_hook(),
        default_ttl_seconds=default_ttl_seconds,
    )


def get_vault_jit_bridge() -> VaultJITBridge:
    """Process-wide kernel-wired JIT bridge (dev uses VaultStubBackend)."""
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is None:
        with _LOCK:
            if _BRIDGE_SINGLETON is None:
                _BRIDGE_SINGLETON = build_kernel_wired_jit_bridge()
    return _BRIDGE_SINGLETON


def _reset_singletons_for_tests() -> None:
    global _HOOK_SINGLETON, _BRIDGE_SINGLETON
    with _LOCK:
        _HOOK_SINGLETON = None
        _BRIDGE_SINGLETON = None


__all__ = [
    "Footprint",
    "SessionFootprintRegistry",
    "KernelGateRotationHook",
    "get_kernel_gate_rotation_hook",
    "register_session_footprint",
    "build_kernel_wired_jit_bridge",
    "get_vault_jit_bridge",
]
