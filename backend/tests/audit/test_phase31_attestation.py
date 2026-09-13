"""
Phase 31 — TPM 2.0 hardware-rooted platform attestation (Gap G7).
=================================================================

Exercises the REAL ``PlatformAttestationService`` + a real ECDSA-P256 software
EK (``LocalSoftTPM`` — genuine signatures, not mocked verdicts) and the REAL
``attestation_denied_handler`` from app.py:

  TC1  TPM present + authorized PCRs -> attestation SUCCESS; the EK quote
       verifies over (nonce || state_digest || pcr_digest).
  TC2  mismatched PCRs (simulated)   -> AttestationDenied -> HTTP 403 on the
       outbound flow + ATTESTATION_DENIED audit.
  TC3  TPM absent                    -> fail-closed: AttestationDenied (no token
       issuance) + [SECURITY_CRITICAL].

Anti-gaming (charter §II): fixed-value asserts (== 403 / verify is True/False /
exact audit markers); the quote is a genuine EK signature, and TC2 mutates a PCR
so the mismatch is real; replay/forge negative cases use real verification.
HONEST SCOPE: software EK on dev (no /dev/tpmrm0, no manufacturer cert chain);
the attestation moat row is INV-6-gated on an external crypto audit — NOT
advanced here.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase31_attestation.py -n 0
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import attestation_denied_handler
from services.tpm_attestation import (
    AttestationDenied,
    LocalSoftTPM,
    PlatformAttestationService,
    attested_token_guard,
    verify_quote,
    vos_state_digest,
)

# A small authorized golden PCR bank (boot state).
_GOLD = {
    0: "aa" * 32,
    1: "bb" * 32,
    7: "cc" * 32,
    11: "dd" * 32,
}


def _svc(*, present=True, pcrs=None, authorized=None, audit=None):
    tpm = LocalSoftTPM(present=present, pcrs=pcrs if pcrs is not None else dict(_GOLD))
    return PlatformAttestationService(
        tpm,
        authorized_pcrs=authorized if authorized is not None else dict(_GOLD),
        audit_sink=(audit.append if audit is not None else (lambda r: None)),
    )


# ---------------------------------------------------------------------------
# TC1 — present + valid PCRs -> success, EK quote verifies.
# ---------------------------------------------------------------------------


def test_tc1_present_valid_pcrs_attestation_succeeds():
    audit: list = []
    svc = _svc(audit=audit)
    nonce = b"\x11" * 32
    result = svc.attest(nonce=nonce, kernel_hash="kdeadbeef", m3_root_ref="m3:test")

    assert result.success is True
    assert result.state_digest == vos_state_digest(
        kernel_hash="kdeadbeef", m3_root_ref="m3:test"
    )
    assert result.quote is not None
    # The EK quote verifies over (nonce || state || pcr).
    assert verify_quote(result, nonce=nonce) is True
    # A replayed/wrong nonce must NOT verify (anti-replay).
    assert verify_quote(result, nonce=b"\x22" * 32) is False
    assert "ATTESTATION_VERIFIED" in [a["marker"] for a in audit]


def test_tc1b_tampered_quote_signature_fails_verify():
    svc = _svc()
    nonce = b"\x33" * 32
    result = svc.attest(nonce=nonce)
    # Corrupt one byte of the signature -> hard verification failure.
    import base64

    raw = bytearray(base64.b64decode(result.quote.signature_b64))
    raw[0] ^= 0x01
    from dataclasses import replace

    bad_quote = replace(
        result.quote, signature_b64=base64.b64encode(bytes(raw)).decode()
    )
    bad_result = replace(result, quote=bad_quote)
    assert verify_quote(bad_result, nonce=nonce) is False


# ---------------------------------------------------------------------------
# TC2 — mismatched PCRs -> AttestationDenied -> HTTP 403 on outbound flow.
# ---------------------------------------------------------------------------


def test_tc2_mismatched_pcrs_denies_with_403():
    audit: list = []
    # Current PCR[7] drifts from the authorized golden value.
    drifted = dict(_GOLD)
    drifted[7] = "ee" * 32
    svc = _svc(pcrs=drifted, audit=audit)

    with pytest.raises(AttestationDenied):
        svc.attest(nonce=b"\x44" * 32)
    assert "ATTESTATION_DENIED" in [a["marker"] for a in audit]

    # The outbound flow surfaces it as HTTP 403 via the real handler.
    app = FastAPI()
    app.add_exception_handler(AttestationDenied, attestation_denied_handler)

    @app.post("/outbound")
    async def _outbound():
        svc.require_attested(nonce=b"\x44" * 32)
        return {"status": "ok"}

    r = TestClient(app, raise_server_exceptions=False).post("/outbound")
    assert r.status_code == 403
    assert r.json()["code"] == "attestation_denied"


def test_tc2b_unanchored_pcr_is_failclosed():
    # An index with NO authorized value must be treated as a mismatch (never
    # accept an un-anchored PCR).
    svc = _svc(authorized={0: "aa" * 32})  # only PCR0 anchored; 1/7/11 unanchored
    with pytest.raises(AttestationDenied):
        svc.attest(nonce=b"\x55" * 32)


# ---------------------------------------------------------------------------
# TC3 — TPM absent -> fail-closed, no token issuance.
# ---------------------------------------------------------------------------


def test_tc3_tpm_absent_failclosed():
    audit: list = []
    svc = _svc(present=False, audit=audit)
    with pytest.raises(AttestationDenied) as ei:
        svc.attest(nonce=b"\x66" * 32)
    assert "no TPM" in str(ei.value)
    assert "ATTESTATION_DENIED" in [a["marker"] for a in audit]


# ---------------------------------------------------------------------------
# Token-issuance guard (G2/G10 integration) + default-OFF baseline.
# ---------------------------------------------------------------------------


def test_guard_noop_when_flag_off(monkeypatch):
    monkeypatch.delenv("VOS3_ENABLE_TPM_ATTESTATION", raising=False)
    # Default OFF -> guard is a no-op even on a TPM-less host (no regression).
    attested_token_guard()  # must not raise


def test_guard_denies_token_issuance_when_on_and_unattested(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_TPM_ATTESTATION", "1")
    from services import tpm_attestation as mod

    # Force the process service to a TPM-absent platform.
    mod._reset_singleton_for_tests()
    mod._SERVICE_SINGLETON = _svc(present=False)
    try:
        with pytest.raises(AttestationDenied):
            attested_token_guard()
    finally:
        mod._reset_singleton_for_tests()


def test_g2_perimeter_requires_attestation_when_on(monkeypatch):
    # G7->G2: with both the AIMS gate and TPM attestation ON, an unattested host
    # makes the perimeter dependency fail closed (403) before any token resolve.
    monkeypatch.setenv("VOS3_ENABLE_LIVE_AIMS_AUTH", "1")
    monkeypatch.setenv("VOS3_ENABLE_TPM_ATTESTATION", "1")
    from services import tpm_attestation as mod
    from api.aims_dep import aims_auth_dependency

    mod._reset_singleton_for_tests()
    mod._SERVICE_SINGLETON = _svc(present=False)
    try:
        app = FastAPI()
        app.add_exception_handler(AttestationDenied, attestation_denied_handler)

        @app.get("/guarded", dependencies=[Depends(aims_auth_dependency)])
        async def _g():
            return {"ok": True}

        r = TestClient(app, raise_server_exceptions=False).get(
            "/guarded", headers={"Authorization": "Bearer whatever"}
        )
        assert r.status_code == 403
        assert r.json()["code"] == "attestation_denied"
    finally:
        mod._reset_singleton_for_tests()
