"""
Phase 24 — fail-closed agent-egress gate integration tests (Gap G1).
====================================================================

Exercises the REAL `dispatch_agent_response` chokepoint + the REAL
`egress_denied_handler` (imported from app.py) through a TestClient, end to end:

  1. kernel gate returns DENY            -> HTTP 403 (exact)
  2. incomplete gate context (pid=None)  -> HTTP 403 (exact, INV-5)
  3. clean unmarked transaction          -> HTTP 200 (exact)
  4. gate flag OFF                        -> HTTP 200 (ungated, byte-path unchanged)

Anti-gaming (charter §II): fixed-value status asserts only (== 403 / == 200,
never >=400); the gate decision is driven by REAL color-vs-sink policy through
the REAL connector (MOCK mode on this host) — NOT by mocking the gate function.
The DENY is produced by a genuinely over-ceiling buffer, not a stub.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase24_egress_enforcement.py -n 0
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import egress_denied_handler
from src.efficiency.router import EgressDenied, dispatch_agent_response


# A real TaintedBuffer-shaped object (content + per-byte colors[]).
class _Buf:
    def __init__(self, colors: bytes):
        self.content = b"\x00" * len(colors)
        self.colors = colors

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def max_color(self) -> int:
        return max(self.colors) if self.colors else 0


def _app() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(EgressDenied, egress_denied_handler)

    @app.post("/test-dispatch")
    async def _route(body: dict):
        mode = body.get("mode")
        if mode == "missing":
            ctx = {"runtime_pid": None, "target_fd": None, "tainted_buffer": None}
        else:
            # SECRET(2) byte > NETWORK_EGRESS ceiling UNTRUSTED(1) => real DENY.
            # all-PUBLIC(0) => real ALLOW. Driven by policy, not a stub.
            colors = bytes([0, 0, 2, 0]) if mode == "deny" else bytes([0, 0, 0])
            ctx = {
                "runtime_pid": int(body["pid"]),
                "target_fd": int(body["fd"]),
                "tainted_buffer": _Buf(colors),
            }
        return await dispatch_agent_response("sess-1", {"data": "x"}, ctx)

    return TestClient(app, raise_server_exceptions=False)


def test_case1_kernel_deny_returns_exactly_403(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    r = _app().post("/test-dispatch", json={"mode": "deny", "pid": 90001, "fd": 11})
    assert r.status_code == 403
    assert r.json()["code"] == "egress_denied"


def test_case2_incomplete_context_returns_exactly_403(monkeypatch):
    # INV-5: missing pid/fd/tainted MUST fail closed, never proceed.
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    r = _app().post("/test-dispatch", json={"mode": "missing"})
    assert r.status_code == 403
    assert r.json()["code"] == "egress_denied"


def test_case3_clean_transaction_returns_exactly_200(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_LIVE_LSM_GATE", "1")
    r = _app().post("/test-dispatch", json={"mode": "allow", "pid": 90002, "fd": 12})
    assert r.status_code == 200
    assert r.json()["status"] == "dispatched"


def test_case4_flag_off_skips_gate_returns_200(monkeypatch):
    # Flag OFF (dev/CI default): the gate is not invoked; even a "deny" buffer
    # dispatches normally — proving the gate is opt-in and never on by default.
    monkeypatch.delenv("VOS3_ENABLE_LIVE_LSM_GATE", raising=False)
    r = _app().post("/test-dispatch", json={"mode": "deny", "pid": 90003, "fd": 13})
    assert r.status_code == 200
    assert r.json()["status"] == "dispatched"
