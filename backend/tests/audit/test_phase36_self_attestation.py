"""
Phase 36 — self-attesting compliance gateway (Gap G13).
=======================================================

Exercises the REAL ``/api/v1/compliance/attest`` endpoint + the REAL
``RemoteAttestationProvider`` + offline ``verify_attestation_bundle`` over the
composed Phase-26 ledger / Phase-35 TPM checkpoint / Phase-32 Safe-Lock:

  TC1  valid state -> 200, bundle has a verifying TPM-EK checkpoint signature +
       RFC-6962 inclusion proofs.
  TC2  Safe-Lock state -> 403 + ``X-Compliance-Status: CRITICAL_FAILURE``.
  TC3  external verifier (only the bundle + EK public key) -> all_ok True; and a
       1-byte tamper of the bundle root -> verification fails.

Anti-gaming (charter §II): fixed-value asserts (== 200/403, exact header, all_ok
True/False); the EK signature + Merkle proofs are real, verified by an offline
verifier that has NO internal access (only the bundle).

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase36_self_attestation.py -n 0
"""

from __future__ import annotations

import copy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.audit_anchor import AuditCheckpointAnchor
from services.policy_transparency import PolicyTransparencyLedger
from services.remote_attestation import (
    RemoteAttestationProvider,
    set_remote_attestation_provider,
    verify_attestation_bundle,
)
from services.tpm_attestation import LocalSoftTPM, PlatformAttestationService

_GOLD = {0: "aa" * 32, 1: "bb" * 32, 7: "cc" * 32, 11: "dd" * 32}


def _provider(tmp_path, *, n_events=120, safe_locked=False):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    for i in range(n_events):
        ledger.record(event_type="tx", schema_hash=f"{i:064x}", metadata={"i": i})
    svc = PlatformAttestationService(
        LocalSoftTPM(present=True, pcrs=dict(_GOLD)),
        authorized_pcrs=dict(_GOLD),
        audit_sink=lambda r: None,
    )
    anchor = AuditCheckpointAnchor(
        ledger, svc, interval=100, nvram_path=str(tmp_path / "nv.jsonl")
    )
    lock = (safe_locked, "test lockdown" if safe_locked else "")
    return RemoteAttestationProvider(
        ledger=ledger, anchor_mgr=anchor, safe_lock_fn=lambda: lock
    )


def _client(provider) -> TestClient:
    from api import transparency_routes
    from api.deps import get_current_user

    app = FastAPI()
    app.include_router(transparency_routes.router, prefix="/api")

    class _U:
        id = "auditor"

    app.dependency_overrides[get_current_user] = lambda: _U()
    set_remote_attestation_provider(provider)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TC1 — valid state -> bundle with TPM signature + Merkle proofs.
# ---------------------------------------------------------------------------


def test_tc1_valid_attestation_bundle(tmp_path):
    provider = _provider(tmp_path, n_events=120)
    try:
        r = _client(provider).get("/api/v1/compliance/attest")
        assert r.status_code == 200
        assert r.headers["X-Compliance-Status"] == "OK"
        bundle = r.json()
        assert bundle["schema"] == "vos-attest-v1"
        assert bundle["tree_size"] == 120
        # TPM-EK checkpoint present (a 100-event boundary was crossed).
        assert bundle["tpm_checkpoint"] is not None
        assert bundle["tpm_checkpoint"]["tree_size"] in (100, 120)
        # Inclusion proofs for the last 100 transactions.
        assert len(bundle["inclusion_proofs"]) == 100
        # Ledger STH + taint status present.
        assert bundle["ledger_sth"]["tree_size"] == 120
        assert bundle["taint_status"]["safe_locked"] is False
    finally:
        set_remote_attestation_provider(None)


# ---------------------------------------------------------------------------
# TC2 — Safe-Lock -> 403 + X-Compliance-Status: CRITICAL_FAILURE.
# ---------------------------------------------------------------------------


def test_tc2_safe_lock_returns_403_with_compliance_header(tmp_path):
    provider = _provider(tmp_path, n_events=120, safe_locked=True)
    try:
        r = _client(provider).get("/api/v1/compliance/attest")
        assert r.status_code == 403
        assert r.headers["X-Compliance-Status"] == "CRITICAL_FAILURE"
        assert r.json()["code"] == "compliance_critical_failure"
    finally:
        set_remote_attestation_provider(None)


# ---------------------------------------------------------------------------
# TC3 — external verifier (bundle + EK pub only) verifies; tamper fails.
# ---------------------------------------------------------------------------


def test_tc3_external_verifier_validates_bundle_and_detects_tamper(tmp_path):
    provider = _provider(tmp_path, n_events=120)
    bundle = provider.build_bundle(last_n=100)

    # An external auditor with ONLY the bundle (no internal access) verifies it.
    report = verify_attestation_bundle(bundle)
    assert report["all_ok"] is True
    assert report["tpm_checkpoint_ok"] is True
    assert report["inclusion_proofs_ok"] is True
    assert report["proof_count"] == 100

    # Tamper the TPM checkpoint root -> EK signature no longer covers it -> fail.
    tampered = copy.deepcopy(bundle)
    tampered["tpm_checkpoint"]["root_hex"] = "11" * 32
    assert verify_attestation_bundle(tampered)["tpm_checkpoint_ok"] is False
    assert verify_attestation_bundle(tampered)["all_ok"] is False

    # Tamper a proof's leaf payload -> inclusion verification fails.
    tampered2 = copy.deepcopy(bundle)
    import base64

    raw = bytearray(
        base64.b64decode(tampered2["inclusion_proofs"][0]["leaf_payload_b64"])
    )
    raw[0] ^= 0x01
    tampered2["inclusion_proofs"][0]["leaf_payload_b64"] = base64.b64encode(
        bytes(raw)
    ).decode()
    assert verify_attestation_bundle(tampered2)["inclusion_proofs_ok"] is False


def test_tc3b_no_tpm_bundle_has_null_checkpoint_not_faked(tmp_path):
    # Honest fail-closed: a TPM-less provider yields a null checkpoint (never a
    # faked hardware signature); the bundle still carries ledger STH + proofs.
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "l.jsonl"))
    for i in range(120):
        ledger.record(event_type="tx", schema_hash=f"{i:064x}", metadata={"i": i})
    svc = PlatformAttestationService(
        LocalSoftTPM(present=False, pcrs={}),
        authorized_pcrs={},
        audit_sink=lambda r: None,
    )
    anchor = AuditCheckpointAnchor(ledger, svc, interval=100)
    provider = RemoteAttestationProvider(
        ledger=ledger, anchor_mgr=anchor, safe_lock_fn=lambda: (False, "")
    )
    bundle = provider.build_bundle(last_n=100)
    assert bundle["tpm_checkpoint"] is None
    assert len(bundle["inclusion_proofs"]) == 100
    # Without a hardware anchor the external verifier does NOT report all_ok.
    assert verify_attestation_bundle(bundle)["all_ok"] is False
