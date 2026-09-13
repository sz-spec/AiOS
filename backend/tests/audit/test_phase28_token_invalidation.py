"""
Phase 28 — F5 token rotation -> kernel eBPF slot invalidation (Gap G10).
========================================================================

Wires the userspace JIT-credential lifecycle (`services.vault_jit_bridge`) to
active kernel-side slot invalidation: on rotate/revoke, the new
`KernelGateRotationHook` flushes each registered (pid, fd) footprint out of the
kernel maps via `KernelGateConnector.invalidate_slot` — an explicit
`bpf(BPF_MAP_DELETE_ELEM)` on the colors map that KEEPS the MARK, so the kernel
hook fails closed (-EPERM) on the now-stale slot.

  TC1  rotate -> userspace credential ROTATED AND the eBPF color slot ERASED
       (mark retained -> fail-closed), exactly one footprint flushed.
  TC2  egress on the rotated footprint -> kernel gate DENY (hard EPERM, errno
       -13) AND the route surfaces HTTP 403.
  TC3  no rotation -> credential ACTIVE, slot intact, egress 200 (zero regress);
       and an empty-registry rotation flushes nothing (baseline preserved).

Anti-gaming (charter §II): fixed-value asserts (== 403 / == 200 / == DENY /
errno == -13 / == ROTATED / == ACTIVE); the flush runs through the REAL
KernelGateConnector (MOCK on this host) and the REAL VaultJITBridge rotate/
revoke path — the hook is not stubbed. HONEST SCOPE: MOCK means the flush
clears the in-process map and the fail-closed decision is observable via the
connector; the binding kernel enforcement is the separately-verified live load
(docs/audit/compliance_payload.json, verifier_loadall_rc==0). Production no-ops
until the agent runtime registers a footprint.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase28_token_invalidation.py -n 0
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from security.kernel_gate_connector import (
    DecisionKind,
    KernelGateConnector,
    SinkKind,
    TaintLabel,
    TaintMode,
    _pack_mark_push_arg,
)
from services.kernel_cred_invalidation import (
    KernelGateRotationHook,
    SessionFootprintRegistry,
    build_kernel_wired_jit_bridge,
)
from services.vault_jit_bridge import CredentialStatus

UNTRUSTED = int(TaintLabel.UNTRUSTED)  # 1 — clean for NETWORK_EGRESS (ceiling=1)


def _mark_clean_egress_slot(gate: KernelGateConnector, *, pid: int, fd: int) -> None:
    """Atomically mark+push a gated NETWORK_EGRESS slot with clean (UNTRUSTED)
    colors so it ALLOWS until invalidated."""
    arg = _pack_mark_push_arg(
        fd=fd,
        colors=bytes([UNTRUSTED, UNTRUSTED, UNTRUSTED, UNTRUSTED]),
        sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        mode=TaintMode.PER_FD,
        claimed_max_color=UNTRUSTED,
    )
    gate.mark_push(arg, pid=pid)


def _stale_route_client(gate: KernelGateConnector, *, pid: int, fd: int) -> TestClient:
    """A route that consults the kernel gate for the (pid, fd) slot and maps a
    DENY (the stale-slot fail-closed) to HTTP 403."""
    app = FastAPI()

    @app.post("/egress")
    async def _route():
        d = gate.simulate_write(fd=fd, pid=pid)
        if d.decision == DecisionKind.DENY:
            raise HTTPException(
                status_code=403, detail="kernel egress gate: stale credential slot"
            )
        return {"status": "dispatched"}

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TC1 — rotate flushes the eBPF slot; userspace credential transitions ROTATED.
# ---------------------------------------------------------------------------


def test_tc1_rotation_erases_ebpf_slot_and_updates_token_service():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 80001, 14
    _mark_clean_egress_slot(gate, pid=pid, fd=fd)

    # Baseline: slot present, marked, ALLOWs (clean colors at the egress ceiling).
    assert gate.peek_entry(fd=fd, pid=pid) is not None
    assert gate.is_marked(fd=fd, pid=pid) is True
    assert gate.simulate_write(fd=fd, pid=pid).decision == DecisionKind.ALLOW

    hook = KernelGateRotationHook(gate=gate)
    bridge = build_kernel_wired_jit_bridge(hook=hook)
    cred = bridge.issue_credential("agent://payments", ttl_seconds=300)
    hook.registry.register(cred.token_sha256, pid=pid, fd=fd)

    outcome = bridge.rotate_credential(cred.credential_id)

    # Userspace token service updated.
    assert bridge.status(cred.credential_id) == CredentialStatus.ROTATED
    # Exactly the one registered footprint was flushed from the kernel map.
    assert outcome.fds_marked_stale == 1
    assert outcome.fds_skipped == 0
    # The eBPF color slot is completely erased ...
    assert gate.peek_entry(fd=fd, pid=pid) is None
    # ... but the MARK is retained, so the slot now fails closed (not allow).
    assert gate.is_marked(fd=fd, pid=pid) is True


# ---------------------------------------------------------------------------
# TC2 — egress on the rotated footprint is blocked: hard EPERM + HTTP 403.
# ---------------------------------------------------------------------------


def test_tc2_egress_on_rotated_footprint_blocked_403():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 80002, 21
    _mark_clean_egress_slot(gate, pid=pid, fd=fd)

    hook = KernelGateRotationHook(gate=gate)
    bridge = build_kernel_wired_jit_bridge(hook=hook)
    cred = bridge.issue_credential("agent://email", ttl_seconds=300)
    hook.registry.register(cred.token_sha256, pid=pid, fd=fd)
    bridge.rotate_credential(cred.credential_id)

    # Hard kernel EPERM on the stale slot.
    d = gate.simulate_write(fd=fd, pid=pid)
    assert d.decision == DecisionKind.DENY
    assert d.errno == -13  # -EPERM

    # Route surfaces it as HTTP 403.
    r = _stale_route_client(gate, pid=pid, fd=fd).post("/egress")
    assert r.status_code == 403


def test_tc2b_revoke_also_flushes_slot_fail_closed():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 80003, 22
    _mark_clean_egress_slot(gate, pid=pid, fd=fd)

    hook = KernelGateRotationHook(gate=gate)
    bridge = build_kernel_wired_jit_bridge(hook=hook)
    cred = bridge.issue_credential("agent://files", ttl_seconds=300)
    hook.registry.register(cred.token_sha256, pid=pid, fd=fd)

    bridge.revoke_credential(cred.credential_id)
    assert bridge.status(cred.credential_id) == CredentialStatus.REVOKED
    assert gate.peek_entry(fd=fd, pid=pid) is None
    d = gate.simulate_write(fd=fd, pid=pid)
    assert d.decision == DecisionKind.DENY
    assert d.errno == -13


# ---------------------------------------------------------------------------
# TC3 — no rotation: clean lifecycle, zero functional regression.
# ---------------------------------------------------------------------------


def test_tc3_unrotated_token_transits_clean_200():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 80004, 30
    _mark_clean_egress_slot(gate, pid=pid, fd=fd)

    hook = KernelGateRotationHook(gate=gate)
    bridge = build_kernel_wired_jit_bridge(hook=hook)
    cred = bridge.issue_credential("agent://search", ttl_seconds=300)
    hook.registry.register(cred.token_sha256, pid=pid, fd=fd)

    # No rotation -> credential ACTIVE, slot intact, egress allowed.
    assert bridge.status(cred.credential_id) == CredentialStatus.ACTIVE
    assert gate.peek_entry(fd=fd, pid=pid) is not None
    assert gate.simulate_write(fd=fd, pid=pid).decision == DecisionKind.ALLOW
    r = _stale_route_client(gate, pid=pid, fd=fd).post("/egress")
    assert r.status_code == 200
    assert r.json()["status"] == "dispatched"


def test_tc3b_empty_registry_rotation_is_a_noop_baseline_preserved():
    # A rotation with NO registered footprint must flush nothing and leave an
    # unrelated marked slot completely intact (default-OFF / no-coupling promise).
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 80005, 31
    _mark_clean_egress_slot(gate, pid=pid, fd=fd)

    hook = KernelGateRotationHook(gate=gate)  # empty registry
    bridge = build_kernel_wired_jit_bridge(hook=hook)
    cred = bridge.issue_credential("agent://noop", ttl_seconds=300)
    # deliberately DO NOT register a footprint
    outcome = bridge.rotate_credential(cred.credential_id)

    assert outcome.fds_marked_stale == 0
    assert outcome.fds_skipped == 0
    # The unrelated slot is untouched -> still allows.
    assert gate.peek_entry(fd=fd, pid=pid) is not None
    assert gate.simulate_write(fd=fd, pid=pid).decision == DecisionKind.ALLOW


# ---------------------------------------------------------------------------
# Registry unit invariants (anti-gaming: the footprint is exact, not blanket).
# ---------------------------------------------------------------------------


def test_registry_keys_exactly_and_forgets_on_flush():
    reg = SessionFootprintRegistry()
    tok_a = b"\xaa" * 32
    tok_b = b"\xbb" * 32
    reg.register(tok_a, pid=1, fd=2)
    reg.register(tok_a, pid=1, fd=3)
    reg.register(tok_b, pid=9, fd=9)
    assert reg.footprint(tok_a) == {(1, 2), (1, 3)}
    assert reg.footprint(tok_b) == {(9, 9)}
    # Rotating tok_a must not disturb tok_b (no cross-token leakage).
    assert reg.forget(tok_a) == {(1, 2), (1, 3)}
    assert reg.footprint(tok_a) == set()
    assert reg.footprint(tok_b) == {(9, 9)}


def test_registry_rejects_malformed_token_hash():
    reg = SessionFootprintRegistry()
    import pytest

    with pytest.raises(ValueError):
        reg.register(b"too-short", pid=1, fd=1)
