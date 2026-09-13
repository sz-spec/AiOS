"""
Phase 27 — per-COLOR byte enforcement + per-socket-fd granularity (Gap G4).
===========================================================================

The Phase 27 change is KERNEL-side: `taint_gate.c`'s socket_sendmsg hook now
resolves the real per-socket integer fd via CO-RE (sock->file -> the same
current->files->fdt->fd[] walk the file_permission hook uses), retiring the
legacy `fd=0` blanket stub that collapsed every socket for a pid to one
per-PID key. That kernel enforcement is verified independently on a live
6.12.68 BPF-LSM kernel — see `docs/audit/compliance_payload.json`
(`verifier_loadall_rc == 0`, both LSM programs attach) and the binding test
below. It CANNOT be exercised directly from macOS (no in-kernel LSM; the
connector runs MOCK), so these tests validate the USERSPACE contract the
kernel mirrors byte-for-byte:

  TC1  NETWORK_EGRESS, marked (pid,fd), buffer carrying a BLOCKED color
       (SECRET/TOXIC byte) -> HTTP 403 (and connector EPERM, errno -13).
  TC2  SAME (pid,fd), CLEAR color profile (PUBLIC/UNTRUSTED) -> HTTP 200.
  TC3  same pid, two sockets: a toxic fd DENIES while a clean fd ALLOWS
       (impossible under the old per-PID fd=0 stub) + no cross-namespace
       (cross-pid) leakage of the marked set.

Anti-gaming (charter §II): fixed-value asserts (== 403 / == 200 / == DENY /
errno == -13); decisions are produced by the REAL KernelGateConnector color
policy (NETWORK_EGRESS ceiling = UNTRUSTED), never a mocked verdict; the
per-byte test flips real bytes; the kernel-evidence test reads the real
live-verifier bundle. NOTE (honest scope): the connector is MOCK on this host,
so a 200 here means "userspace would push + the kernel WOULD allow" — the
binding kernel enforcement is the separately-verified live load, not this run.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase27_color_enforcement.py -n 0
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from security.kernel_gate_connector import (
    DecisionKind,
    KernelGateConnector,
    SinkKind,
    TaintLabel,
    TaintMode,
    discover_pidns,
)

PUBLIC = int(TaintLabel.PUBLIC)  # 0
UNTRUSTED = int(TaintLabel.UNTRUSTED)  # 1  — NETWORK_EGRESS ceiling
SECRET = int(TaintLabel.SECRET)  # 2  — blocked on network egress
TOXIC = int(TaintLabel.TOXIC)  # 3  — blocked on network egress


class _Buf:
    """A real TaintedBuffer-shaped object: content + parallel per-byte colors[]."""

    def __init__(self, colors: bytes):
        self.content = b"\x00" * len(colors)
        self.colors = colors

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def max_color(self) -> int:
        return max(self.colors) if self.colors else 0


# ---------------------------------------------------------------------------
# Route-level 403 / 200 through the real dispatch_agent_response chokepoint.
# ---------------------------------------------------------------------------


def _route_client() -> TestClient:
    from app import egress_denied_handler
    from src.efficiency.router import EgressDenied, dispatch_agent_response

    app = FastAPI()
    app.add_exception_handler(EgressDenied, egress_denied_handler)

    @app.post("/egress")
    async def _route(body: dict):
        ctx = {
            "runtime_pid": int(body["pid"]),
            "target_fd": int(body["fd"]),
            "tainted_buffer": _Buf(bytes(body["colors"])),
        }
        return await dispatch_agent_response("sess-27", {"data": "x"}, ctx)

    return TestClient(app, raise_server_exceptions=False)


def test_tc1_network_egress_blocked_color_returns_exactly_403(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    # UNTRUSTED stream with a single SECRET byte -> max_color SECRET > ceiling.
    r = _route_client().post(
        "/egress",
        json={
            "pid": 70101,
            "fd": 11,
            "colors": [UNTRUSTED, UNTRUSTED, SECRET, UNTRUSTED],
        },
    )
    assert r.status_code == 403
    assert r.json()["code"] == "egress_denied"


def test_tc2_network_egress_clear_color_returns_exactly_200(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    # All bytes at/under the NETWORK_EGRESS ceiling (UNTRUSTED) -> permitted.
    r = _route_client().post(
        "/egress",
        json={"pid": 70101, "fd": 11, "colors": [PUBLIC, UNTRUSTED, UNTRUSTED, PUBLIC]},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dispatched"


# ---------------------------------------------------------------------------
# Connector-level: the color policy itself (NETWORK_EGRESS ceiling = UNTRUSTED).
# ---------------------------------------------------------------------------


def test_blocked_color_is_eperm_at_connector():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 70200, 7
    gate.push_tainted_buffer(
        fd=fd,
        pid=pid,
        buffer=_Buf(bytes([UNTRUSTED, TOXIC, UNTRUSTED])),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    d = gate.simulate_write(fd=fd, pid=pid)
    assert d.decision == DecisionKind.DENY
    assert d.errno == -13  # -EPERM


def test_clear_color_allows_at_connector():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 70200, 8
    gate.push_tainted_buffer(
        fd=fd,
        pid=pid,
        buffer=_Buf(bytes([PUBLIC, UNTRUSTED, PUBLIC])),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    d = gate.simulate_write(fd=fd, pid=pid)
    assert d.decision == DecisionKind.ALLOW
    assert d.errno == 0


# ---------------------------------------------------------------------------
# TC3 — per-socket-fd granularity: the CORE consequence of retiring fd=0.
# Same pid, two sockets, OPPOSITE decisions — impossible under the old stub.
# ---------------------------------------------------------------------------


def test_tc3_per_fd_granularity_same_pid_two_sockets():
    gate = KernelGateConnector(force_mock=True)
    pid = 70300
    fd_toxic, fd_clean = 11, 12
    gate.push_tainted_buffer(
        fd=fd_toxic,
        pid=pid,
        buffer=_Buf(bytes([UNTRUSTED, UNTRUSTED, SECRET, UNTRUSTED])),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    gate.push_tainted_buffer(
        fd=fd_clean,
        pid=pid,
        buffer=_Buf(bytes([PUBLIC, UNTRUSTED, UNTRUSTED, PUBLIC])),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    d_toxic = gate.simulate_write(fd=fd_toxic, pid=pid)
    d_clean = gate.simulate_write(fd=fd_clean, pid=pid)
    # Same pid -> a blanket per-PID block could not produce these two verdicts.
    assert d_toxic.decision == DecisionKind.DENY
    assert d_toxic.errno == -13
    assert d_clean.decision == DecisionKind.ALLOW
    assert d_clean.errno == 0


# ---------------------------------------------------------------------------
# Per-byte (per-COLOR) scan bounded by the write span — EchoLeak defense.
# Mirrors the socket hook's vos3_max_color_per_byte(span = iov size).
# ---------------------------------------------------------------------------


def test_per_byte_clean_prefix_egresses_toxic_tail_blocked():
    gate = KernelGateConnector(force_mock=True)
    pid, fd = 70400, 9
    # 200-byte clean UNTRUSTED prefix, then a 56-byte TOXIC tail.
    colors = bytes([UNTRUSTED] * 200 + [TOXIC] * 56)
    gate.push_tainted_buffer(
        fd=fd,
        pid=pid,
        buffer=_Buf(colors),
        sink_kind=SinkKind.NETWORK_EGRESS,
        taint_mode=TaintMode.PER_BYTE,
    )
    # Sending only the clean prefix never reaches the toxic tail -> ALLOW.
    d_prefix = gate.simulate_write(fd=fd, pid=pid, length=200)
    assert d_prefix.decision == DecisionKind.ALLOW
    # Sending the full buffer reaches the toxic bytes -> DENY (fail-closed).
    d_full = gate.simulate_write(fd=fd, pid=pid, length=256)
    assert d_full.decision == DecisionKind.DENY
    assert d_full.errno == -13


# ---------------------------------------------------------------------------
# TC3b — namespace isolation: no cross-pid (cross-namespace) leakage.
# ---------------------------------------------------------------------------


def test_tc3b_no_cross_namespace_marked_set_leakage():
    gate = KernelGateConnector(force_mock=True)
    # The 19.5b drift values: a container-ns pid vs the host-ns pid for the
    # same task. They are DISTINCT map keys; a mark in one must not leak to the
    # other (the kernel keys on the pid AS SEEN IN the bridge's pid-namespace).
    pid_ns_a, pid_ns_b = 19, 73324
    fd = 11
    gate.push_tainted_buffer(
        fd=fd,
        pid=pid_ns_a,
        buffer=_Buf(bytes([SECRET, SECRET, SECRET])),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    # ns A: toxic entry -> DENY.
    assert gate.simulate_write(fd=fd, pid=pid_ns_a).decision == DecisionKind.DENY
    # ns B: SAME fd number, but a different pid key -> no entry, not marked ->
    # default ALLOW. The toxic entry in ns A did not leak across the namespace.
    d_b = gate.simulate_write(fd=fd, pid=pid_ns_b)
    assert d_b.decision == DecisionKind.ALLOW
    assert gate.peek_entry(fd=fd, pid=pid_ns_b) is None


def test_discover_pidns_is_honest_about_platform():
    # Linux -> (dev, ino) 2-tuple; non-Linux dev host -> None (never a guess).
    ns = discover_pidns()
    assert ns is None or (isinstance(ns, tuple) and len(ns) == 2)


# ---------------------------------------------------------------------------
# Kernel-evidence binding: the live 6.12.68 verifier accepted THIS build with
# the new socket_sendmsg CO-RE program (skip if the bundle was not generated).
# ---------------------------------------------------------------------------


def test_live_verifier_accepted_socket_hook_build():
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    bundle = os.path.join(repo_root, "docs", "audit", "compliance_payload.json")
    if not os.path.isfile(bundle):
        pytest.skip(
            "compliance_payload.json not generated (run generate_compliance_proof.sh)"
        )
    with open(bundle, encoding="utf-8") as fh:
        ev = json.load(fh)
    # The live BPF-LSM verifier accepted BOTH programs (loadall rc 0) and the
    # G5 byte-contract held — this is the kernel-side proof of the socket fd
    # resolution, which self-collected evidence does NOT advance the moat.
    assert ev["verifier_loadall_rc"] == 0
    assert ev["bpf_lsm_active"] is True
    assert ev["g5_contract_ok"] is True
    assert ev["btf_struct_sizes"]["vos3_taint_color_entry"] == 65568
    assert ev["btf_struct_sizes"]["vos3_taint_map_key"] == 8
    assert ev["moat_impact"].startswith("none")
    # If the compiled object is present, the recorded sha must match it exactly.
    obj = os.path.join(repo_root, "kernel", "build", "bpf", "taint_gate.o")
    if os.path.isfile(obj):
        with open(obj, "rb") as fh:
            obj_sha = hashlib.sha256(fh.read()).hexdigest()
        assert ev["object_sha256"] == obj_sha
