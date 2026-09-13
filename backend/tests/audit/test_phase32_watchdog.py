"""
Phase 32 — runtime integrity watchdog (Gap G6).
================================================

Exercises the REAL ``RuntimeIntegrityWatchdog`` + the REAL Phase-24 egress
chokepoint (``dispatch_agent_response`` + ``egress_denied_handler``) + the REAL
Phase-31 attestation binding:

  TC1  baseline integrity            -> check() ok, valid rolling hash.
  TC2  simulated memory alteration   -> drift detected, CRITICAL_INTEGRITY_VIOLATION
       raised, Safe-Lock engaged, and ALL egress blocked (dispatch -> 403).
  TC3  10k sampling iterations       -> no drift, no exception, no panic, bounded
       latency (sampling introduces no stall).
  + periodic attestation binds the latest RuntimeIntegrityHash into the EK quote
    and it verifies.

Anti-gaming (charter §II): fixed-value asserts (== 403 / drift is True/False /
exact markers); drift is induced by a GENUINE byte change in the sampled source,
the quote is a real EK signature, and the egress block runs through the real
fail-closed chokepoint — no mocked verdicts.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase32_watchdog.py -n 0
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import attestation_denied_handler, egress_denied_handler
from services.integrity_watchdog import (
    CriticalIntegrityViolation,
    RuntimeIntegrityWatchdog,
    get_integrity_watchdog,
    _reset_singleton_for_tests,
)


class _MutableSource:
    """A mutable kernel-.text stand-in: tests flip a byte to simulate a hot-patch."""

    def __init__(self, data: bytes):
        self.data = data

    def __call__(self) -> bytes:
        return self.data


# ---------------------------------------------------------------------------
# TC1 — baseline integrity.
# ---------------------------------------------------------------------------


def test_tc1_baseline_integrity_valid_hash():
    src = _MutableSource(b"kernel-text-segment-v1")
    wd = RuntimeIntegrityWatchdog(text_source=src, audit_sink=lambda r: None)
    status = wd.check()
    assert status.ok is True
    assert status.drift is False
    assert status.current_hash == status.baseline_hash
    assert len(wd.runtime_integrity_hash()) == 64  # SHA-256 hex
    assert wd.is_safe_locked() is False


# ---------------------------------------------------------------------------
# TC2 — simulated alteration -> drift -> CRITICAL + Safe-Lock + egress blocked.
# ---------------------------------------------------------------------------


def test_tc2_alteration_detected_and_egress_blocked():
    audit: list = []
    src = _MutableSource(b"kernel-text-segment-v1")
    wd = RuntimeIntegrityWatchdog(text_source=src, audit_sink=audit.append)
    assert wd.check().ok is True  # clean baseline

    # Simulate a hot-patch: flip one byte of the sampled text.
    src.data = b"kernel-text-segment-vX"

    with pytest.raises(CriticalIntegrityViolation):
        wd.check()
    assert wd.is_safe_locked() is True
    assert "CRITICAL_INTEGRITY_VIOLATION" in [a["marker"] for a in audit]

    # Safe-Lock blocks ALL egress through the real Phase-24 chokepoint.
    _reset_singleton_for_tests()
    try:
        get_integrity_watchdog().engage_safe_lock("test-induced lockdown")

        from src.efficiency.router import EgressDenied, dispatch_agent_response

        app = FastAPI()
        app.add_exception_handler(EgressDenied, egress_denied_handler)

        @app.post("/egress")
        async def _route(body: dict):
            return await dispatch_agent_response("s-32", {"d": 1}, {})

        r = TestClient(app, raise_server_exceptions=False).post("/egress", json={})
        assert r.status_code == 403
        assert r.json()["code"] == "egress_denied"
    finally:
        _reset_singleton_for_tests()


def test_tc2b_safe_lock_clear_requires_operator_attestation():
    src = _MutableSource(b"k1")
    wd = RuntimeIntegrityWatchdog(text_source=src, audit_sink=lambda r: None)
    wd.engage_safe_lock("drift")
    assert wd.is_safe_locked() is True
    with pytest.raises(ValueError):
        wd.clear_safe_lock(operator_attestation="")  # empty attestation rejected
    # A genuine operator attestation clears it + re-baselines on the current text.
    wd.clear_safe_lock(operator_attestation="ops:reviewed-and-reflashed")
    assert wd.is_safe_locked() is False
    assert wd.check().ok is True


# ---------------------------------------------------------------------------
# TC3 — 10k sampling iterations: no drift, no panic, bounded latency.
# ---------------------------------------------------------------------------


def test_tc3_stress_10k_iterations_no_drift_no_panic():
    src = _MutableSource(b"stable-ktext")
    wd = RuntimeIntegrityWatchdog(text_source=src, audit_sink=lambda r: None)
    start = time.monotonic()
    for _ in range(10_000):
        st = wd.check()
        assert st.ok is True
    elapsed = time.monotonic() - start
    assert wd.is_safe_locked() is False
    assert elapsed < 5.0  # sampling is cheap; no stall
    assert wd.samples >= 10_000


# ---------------------------------------------------------------------------
# Periodic attestation binding (Phase 31 integration).
# ---------------------------------------------------------------------------


def test_runtime_integrity_hash_bound_into_attestation_quote():
    from services.tpm_attestation import (
        LocalSoftTPM,
        PlatformAttestationService,
        verify_quote,
    )

    gold = {0: "aa" * 32, 1: "bb" * 32, 7: "cc" * 32, 11: "dd" * 32}
    svc = PlatformAttestationService(
        LocalSoftTPM(present=True, pcrs=dict(gold)),
        authorized_pcrs=dict(gold),
        audit_sink=lambda r: None,
    )
    wd = RuntimeIntegrityWatchdog(
        text_source=_MutableSource(b"ktext-attest"), audit_sink=lambda r: None
    )
    nonce = b"\x77" * 32
    result = wd.attest_runtime_integrity(svc, nonce=nonce)

    # The quote BINDS the watchdog's current integrity hash, and it verifies.
    assert result.runtime_integrity_hash == wd.runtime_integrity_hash()
    assert result.runtime_integrity_hash != ""
    assert verify_quote(result, nonce=nonce) is True
    # Tampering the bound integrity hash breaks verification (it is signed over).
    from dataclasses import replace

    other = "11" * 32
    assert (
        verify_quote(replace(result, runtime_integrity_hash=other), nonce=nonce)
        is False
    )


def test_egress_unaffected_when_no_watchdog_initialised():
    # Default (no Safe-Lock, watchdog may be uninitialised): egress is NOT blocked
    # by Phase 32 — proving zero regression to the Phase-24 baseline.
    _reset_singleton_for_tests()
    try:
        from services.integrity_watchdog import egress_safe_locked

        locked, _ = egress_safe_locked()
        assert locked is False
    finally:
        _reset_singleton_for_tests()
