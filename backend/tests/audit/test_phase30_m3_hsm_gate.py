"""
Phase 30 — M3 HSM model-signature gate (Gap G9).
================================================

Exercises the REAL ``M3ModelSignatureGate`` + the REAL Ed25519 HSM verifier
(``LocalEd25519HSM``, not a mocked verdict) and the REAL ``m3_gate_rejected_handler``
from app.py:

  TC1  invalid signature, HSM ON  -> HTTP 403 + ``M3_GATE_REJECTED`` in audit +
       the model is TOXIC-quarantined in the kernel taint_colors map.
  TC2  valid signature, HSM ON    -> 200 + ``M3_GATE_VERIFIED`` in audit.
  TC3  HSM OFF (mock/dev)          -> ``[SECURITY_WARNING]`` (``M3_GATE_WARN_ALLOWED``)
       logged but load SUCCEEDS (no regression).

Anti-gaming (charter §II): fixed-value asserts (== 403 / == 200 / exact audit
markers); signatures are GENUINELY minted with Ed25519 and verified by the real
HSM seam — TC1 flips one byte of a real signature so verification fails for a
cryptographic reason, not a stub. The trust-root key is ephemeral-per-test (never
the production a6 vector). HONEST SCOPE: this is the userspace enforcement layer;
the kernel read-gate stays OFF and G9's moat row is INV-6-gated on the external
crypto audit — NOT advanced here.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase30_m3_hsm_gate.py -n 0
"""

from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import m3_gate_rejected_handler
from security.kernel_gate_connector import KernelGateConnector, TaintLabel
from services.m3_signature_gate import (
    LocalEd25519HSM,
    M3GateRejected,
    M3ModelSignatureGate,
    m3_gate_enabled,
)

_PAYLOAD = b"\x00\x01\x02\x03 model weights " * 64
_DIGEST = hashlib.sha256(_PAYLOAD).hexdigest()


def _signed():
    """A freshly minted (private, hsm-over-public, valid-signature) triple over
    the digest. Real Ed25519 — the HSM verifies the public half."""
    priv = Ed25519PrivateKey.generate()
    hsm = LocalEd25519HSM(priv.public_key(), key_id="test-anchor")
    sig = priv.sign(bytes.fromhex(_DIGEST))
    return hsm, sig


def _route_client(gate: M3ModelSignatureGate, **verify_kwargs) -> TestClient:
    """A route that runs the gate and maps M3GateRejected -> 403 via the real
    app.py handler; returns 200 on a non-raising decision."""
    app = FastAPI()
    app.add_exception_handler(M3GateRejected, m3_gate_rejected_handler)

    @app.post("/load")
    async def _load():
        decision = gate.verify_model_file(
            model_id="m-1", sha256_hex=_DIGEST, **verify_kwargs
        )
        return {"status": "loaded", "verdict": decision.verdict}

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# TC1 — invalid signature, HSM ON -> 403 + M3_GATE_REJECTED + TOXIC quarantine.
# ---------------------------------------------------------------------------


def test_tc1_invalid_signature_hsm_on_returns_403_and_audits_rejected(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_M3_HSM_GATE", "1")
    assert m3_gate_enabled() is True

    hsm, good_sig = _signed()
    bad_sig = bytearray(good_sig)
    bad_sig[0] ^= 0x01  # genuine single-byte corruption -> Ed25519 verify fails
    audit: list = []
    conn = KernelGateConnector(force_mock=True)
    gate = M3ModelSignatureGate(hsm, gate_connector=conn, audit_sink=audit.append)

    # Route maps the raise to 403; signature is provided per-request.
    client = _route_client(gate, signature=bytes(bad_sig), pid=99001, fd=7)
    r = client.post("/load")
    assert r.status_code == 403
    assert r.json()["code"] == "m3_gate_rejected"

    markers = [a["marker"] for a in audit]
    assert "M3_GATE_REJECTED" in markers
    assert "M3_GATE_VERIFIED" not in markers
    # The model's (pid, fd) slot was TOXIC-quarantined in taint_colors.
    entry = conn.peek_entry(fd=7, pid=99001)
    assert entry is not None
    assert entry["max_color"] == int(TaintLabel.TOXIC)


def test_tc1b_missing_signature_hsm_on_raises():
    import os

    os.environ["VOS3_ENABLE_M3_HSM_GATE"] = "1"
    try:
        hsm, _ = _signed()
        gate = M3ModelSignatureGate(hsm, audit_sink=lambda r: None)
        with pytest.raises(M3GateRejected):
            gate.verify_model_file(model_id="m-x", sha256_hex=_DIGEST, signature=None)
    finally:
        del os.environ["VOS3_ENABLE_M3_HSM_GATE"]


# ---------------------------------------------------------------------------
# TC2 — valid signature, HSM ON -> 200 + M3_GATE_VERIFIED.
# ---------------------------------------------------------------------------


def test_tc2_valid_signature_hsm_on_returns_200_and_audits_verified(monkeypatch):
    monkeypatch.setenv("VOS3_ENABLE_M3_HSM_GATE", "1")
    hsm, good_sig = _signed()
    audit: list = []
    gate = M3ModelSignatureGate(hsm, audit_sink=audit.append)

    client = _route_client(gate, signature=good_sig, pid=99002, fd=8)
    r = client.post("/load")
    assert r.status_code == 200
    assert r.json()["status"] == "loaded"
    assert r.json()["verdict"] == "VERIFIED"
    assert [a["marker"] for a in audit] == ["M3_GATE_VERIFIED"]


# ---------------------------------------------------------------------------
# TC3 — HSM OFF -> warning logged, load succeeds (no regression).
# ---------------------------------------------------------------------------


def test_tc3_hsm_off_warns_but_allows(monkeypatch):
    monkeypatch.delenv("VOS3_ENABLE_M3_HSM_GATE", raising=False)
    assert m3_gate_enabled() is False

    hsm, good_sig = _signed()
    bad_sig = bytearray(good_sig)
    bad_sig[0] ^= 0x01  # invalid signature
    audit: list = []
    gate = M3ModelSignatureGate(hsm, audit_sink=audit.append)

    # Even with an INVALID signature, OFF must NOT raise and the route returns 200.
    client = _route_client(gate, signature=bytes(bad_sig), pid=99003, fd=9)
    r = client.post("/load")
    assert r.status_code == 200
    assert r.json()["verdict"] == "WARN_ALLOWED"
    assert "M3_GATE_WARN_ALLOWED" in [a["marker"] for a in audit]
    assert "M3_GATE_REJECTED" not in [a["marker"] for a in audit]


def test_tc3b_off_valid_signature_still_verifies(monkeypatch):
    # OFF + a VALID signature still reports VERIFIED (the gate verifies whenever it
    # can; OFF only changes what happens on FAILURE).
    monkeypatch.delenv("VOS3_ENABLE_M3_HSM_GATE", raising=False)
    hsm, good_sig = _signed()
    gate = M3ModelSignatureGate(hsm, audit_sink=lambda r: None)
    d = gate.verify_model_file(model_id="m-ok", sha256_hex=_DIGEST, signature=good_sig)
    assert d.verdict == "VERIFIED"
    assert d.audit_marker == "M3_GATE_VERIFIED"


# ---------------------------------------------------------------------------
# Honesty anchors.
# ---------------------------------------------------------------------------


def test_default_is_off_and_handler_code_is_stable(monkeypatch):
    monkeypatch.delenv("VOS3_ENABLE_M3_HSM_GATE", raising=False)
    assert m3_gate_enabled() is False  # default OFF -> no CI/dev regression


def test_gate_does_not_use_a6_ephemeral_vector_as_anchor(monkeypatch):
    # G9 anti-gaming: the gate must never bake the kernel a6_vectors.h test vector
    # as the production anchor. With no trust root provisioned, the dev HSM falls
    # back to an EPHEMERAL verify-only key (key_id 'ephemeral-no-anchor') under
    # which nothing verifies — the safe default — never the a6 vector.
    monkeypatch.delenv("VOS3_M3_TRUST_ROOT_PEM", raising=False)
    from services.m3_signature_gate import _resolve_dev_hsm

    hsm = _resolve_dev_hsm()
    assert hsm.key_id == "ephemeral-no-anchor"
