"""
Phase 38 — system lockdown & PKI preparation (Gap G15).
=======================================================

  - HSM anchor: under VOS3_REQUIRE_HSM_EK_CERT the platform anchor is the
    HSMBackedTPM2 (requires a manufacturer EK-cert chain); on a dev host with no
    chain it is NOT present -> attestation fails closed (no software fallback).
  - Debug lockdown: the live app exposes NO forbidden debug/dump endpoints
    (/debug, dump_taints, /dev/mem, ...); assert_audit_ready passes.
  - No regression: with the lockdown flag OFF the dev soft-TPM path is unchanged.

Anti-gaming (charter §II): fixed-value asserts; the HSM stub is genuinely
not-present without a provisioned cert chain (never fakes presence), and the
debug-endpoint check scans the REAL mounted app.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase38_lockdown.py -n 0
"""

from __future__ import annotations

import pytest

from services.lockdown import (
    FORBIDDEN_DEBUG_PATTERNS,
    DebugEndpointExposed,
    assert_audit_ready,
    scan_forbidden_routes,
)
from services.tpm_attestation import (
    AttestationDenied,
    HSMBackedTPM2,
    LocalSoftTPM,
    _resolve_default_tpm,
    require_hsm_ek_cert,
)

# ---------------------------------------------------------------------------
# HSM EK-cert anchor replaces the soft dev anchor in lockdown.
# ---------------------------------------------------------------------------


def test_lockdown_resolves_hsm_anchor_failclosed_without_cert(monkeypatch):
    monkeypatch.setenv("VOS3_REQUIRE_HSM_EK_CERT", "1")
    monkeypatch.delenv("VOS3_HSM_EK_CERT_CHAIN", raising=False)
    monkeypatch.delenv("VOS3_HSM_TRUST_CA", raising=False)
    assert require_hsm_ek_cert() is True
    tpm = _resolve_default_tpm()
    assert isinstance(tpm, HSMBackedTPM2)
    # No provisioned EK-cert chain on dev -> NOT present -> fail-closed.
    assert tpm.present() is False


def test_lockdown_attestation_fails_closed_without_hsm(monkeypatch):
    monkeypatch.setenv("VOS3_REQUIRE_HSM_EK_CERT", "1")
    monkeypatch.delenv("VOS3_HSM_EK_CERT_CHAIN", raising=False)
    from services.tpm_attestation import PlatformAttestationService

    svc = PlatformAttestationService(_resolve_default_tpm(), audit_sink=lambda r: None)
    with pytest.raises(AttestationDenied):
        svc.attest(nonce=b"\x00" * 32)


def test_flag_off_keeps_soft_tpm_dev_anchor(monkeypatch):
    monkeypatch.delenv("VOS3_REQUIRE_HSM_EK_CERT", raising=False)
    assert require_hsm_ek_cert() is False
    # Dev default unchanged: soft TPM (not-present on a TPM-less host).
    assert isinstance(_resolve_default_tpm(), LocalSoftTPM)


def test_hsm_partial_provision_still_failclosed(monkeypatch, tmp_path):
    # A chain path that doesn't exist (or a missing CA) must NOT validate.
    monkeypatch.setenv("VOS3_REQUIRE_HSM_EK_CERT", "1")
    monkeypatch.setenv("VOS3_HSM_EK_CERT_CHAIN", str(tmp_path / "missing.pem"))
    monkeypatch.delenv("VOS3_HSM_TRUST_CA", raising=False)
    assert HSMBackedTPM2().present() is False


# ---------------------------------------------------------------------------
# Debug-endpoint lockdown (absence guard).
# ---------------------------------------------------------------------------


def test_live_app_exposes_no_forbidden_debug_endpoints():
    from app import create_app

    app = create_app()
    offenders = scan_forbidden_routes(app)
    assert offenders == []  # audit-ready: no /debug, dump_taints, /dev/mem, ...
    assert_audit_ready(app)  # must not raise


def test_assert_audit_ready_detects_a_forbidden_endpoint():
    # If a debug/dump endpoint is ever mounted, the guard fails closed.
    from fastapi import FastAPI

    bad = FastAPI()

    @bad.get("/debug/dump_taints")
    async def _dump():
        return {"taints": []}

    assert "/debug/dump_taints" in scan_forbidden_routes(bad)
    with pytest.raises(DebugEndpointExposed):
        assert_audit_ready(bad)


def test_forbidden_patterns_cover_the_brief_examples():
    joined = " ".join(FORBIDDEN_DEBUG_PATTERNS)
    assert "dump_taints" in joined
    assert "/dev/mem" in joined
    assert "/debug" in joined
