"""
backend/tests/security/test_attestation_composite_policy.py

Sprint 16 / Item D1 — composite-policy attestation tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CP_PATH = _REPO_ROOT / "backend" / "services" / "attestation_composite_policy.py"
_spec = importlib.util.spec_from_file_location("vos3_cp_under_test", _CP_PATH)
cp = importlib.util.module_from_spec(_spec)
sys.modules["vos3_cp_under_test"] = cp
_spec.loader.exec_module(cp)


# ---------------------------------------------------------------------------
# Builder validation
# ---------------------------------------------------------------------------


def test_builder_requires_name():
    with pytest.raises(ValueError):
        cp.CompositePolicyBuilder(name="")


def test_builder_require_validates_kind():
    b = cp.CompositePolicyBuilder("policy")
    with pytest.raises(TypeError):
        b.require("not-a-kind")  # type: ignore[arg-type]


def test_builder_require_validates_signer_type():
    b = cp.CompositePolicyBuilder("policy")
    with pytest.raises(TypeError):
        b.require(cp.EvidenceKind.TDX, expected_signer=123)  # type: ignore[arg-type]


def test_builder_require_validates_rtmr_bytes():
    b = cp.CompositePolicyBuilder("policy")
    with pytest.raises(TypeError):
        b.require(cp.EvidenceKind.TDX, required_rtmr_values=("not-bytes",))  # type: ignore[arg-type]


def test_builder_build_rejects_empty_policy():
    with pytest.raises(ValueError):
        cp.CompositePolicyBuilder("empty").build()


def test_builder_build_returns_frozen_policy():
    policy = (
        cp.CompositePolicyBuilder("strict")
        .require(cp.EvidenceKind.TDX, expected_signer="Intel-TDX")
        .require(cp.EvidenceKind.TPM)
        .build()
    )
    assert policy.name == "strict"
    assert len(policy.required_evidence) == 2
    # Frozen — can't mutate.
    with pytest.raises(AttributeError):
        policy.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Verifier basics
# ---------------------------------------------------------------------------


def _policy_tdx_plus_tpm():
    return (
        cp.CompositePolicyBuilder("strict-tdx-tpm")
        .require(
            cp.EvidenceKind.TDX,
            expected_signer="Intel-TDX-Sig",
            require_nonce_bound=True,
        )
        .require(
            cp.EvidenceKind.TPM, expected_signer="TPM-EK-CA", require_nonce_bound=True
        )
        .build()
    )


def _evidence_tdx(*, signer="Intel-TDX-Sig", nonce_ok=True, rtmrs=()):
    return cp.CompositeEvidence(
        kind=cp.EvidenceKind.TDX,
        signer_cn=signer,
        rtmr_values=tuple(rtmrs),
        nonce_verified_by_d5=nonce_ok,
        raw_report_sha256="abc",
    )


def _evidence_tpm(*, signer="TPM-EK-CA", nonce_ok=True, rtmrs=()):
    return cp.CompositeEvidence(
        kind=cp.EvidenceKind.TPM,
        signer_cn=signer,
        rtmr_values=tuple(rtmrs),
        nonce_verified_by_d5=nonce_ok,
        raw_report_sha256="def",
    )


def test_verify_rejects_non_policy():
    v = cp.CompositeVerifier()
    with pytest.raises(TypeError):
        v.verify("not-a-policy", cp.CompositeReport(claimed_nonce="x", evidence=()))  # type: ignore[arg-type]


def test_verify_rejects_non_report():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    with pytest.raises(TypeError):
        v.verify(policy, "not-a-report")  # type: ignore[arg-type]


def test_verify_rejects_empty_nonce():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    with pytest.raises(ValueError):
        v.verify(policy, cp.CompositeReport(claimed_nonce="", evidence=()))


# ---------------------------------------------------------------------------
# Verifier happy path + failures
# ---------------------------------------------------------------------------


def test_verify_passed_when_all_evidence_satisfies():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    report = cp.CompositeReport(
        claimed_nonce="abc123",
        evidence=(_evidence_tdx(), _evidence_tpm()),
    )
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.PASSED
    assert decision.policy_name == "strict-tdx-tpm"
    assert all(d.satisfied for d in decision.dispositions)


def test_verify_fails_when_required_evidence_missing():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    # TPM evidence absent.
    report = cp.CompositeReport(claimed_nonce="x", evidence=(_evidence_tdx(),))
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.FAILED
    # Find TPM disposition.
    tpm_disp = [d for d in decision.dispositions if d.kind == cp.EvidenceKind.TPM][0]
    assert not tpm_disp.satisfied
    assert "missing" in tpm_disp.reasons[0]


def test_verify_fails_on_signer_mismatch():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    report = cp.CompositeReport(
        claimed_nonce="x",
        evidence=(
            _evidence_tdx(signer="ATTACKER-FAKE-CN"),
            _evidence_tpm(),
        ),
    )
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.FAILED
    tdx_disp = [d for d in decision.dispositions if d.kind == cp.EvidenceKind.TDX][0]
    assert not tdx_disp.satisfied
    assert any("signer_mismatch" in r for r in tdx_disp.reasons)


def test_verify_fails_when_nonce_not_verified():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    report = cp.CompositeReport(
        claimed_nonce="x",
        evidence=(_evidence_tdx(nonce_ok=False), _evidence_tpm()),
    )
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.FAILED
    tdx_disp = [d for d in decision.dispositions if d.kind == cp.EvidenceKind.TDX][0]
    assert any("nonce_not_verified" in r for r in tdx_disp.reasons)


def test_verify_passes_when_nonce_not_required():
    v = cp.CompositeVerifier()
    policy = (
        cp.CompositePolicyBuilder("lenient")
        .require(
            cp.EvidenceKind.TDX,
            expected_signer="Intel-TDX-Sig",
            require_nonce_bound=False,
        )
        .build()
    )
    report = cp.CompositeReport(
        claimed_nonce="x",
        evidence=(_evidence_tdx(nonce_ok=False),),
    )
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.PASSED


def test_verify_rtmr_subset_check():
    v = cp.CompositeVerifier()
    expected_rtmr = b"\x01" * 48
    other_rtmr = b"\x02" * 48
    policy = (
        cp.CompositePolicyBuilder("strict-rtmr")
        .require(
            cp.EvidenceKind.TDX,
            expected_signer="Intel-TDX-Sig",
            required_rtmr_values=(expected_rtmr,),
        )
        .build()
    )
    # Evidence has the RTMR — passes.
    r1 = cp.CompositeReport(
        claimed_nonce="x", evidence=(_evidence_tdx(rtmrs=(expected_rtmr,)),)
    )
    assert v.verify(policy, r1).kind == cp.CompositeDecisionKind.PASSED
    # Evidence missing the RTMR — fails.
    r2 = cp.CompositeReport(
        claimed_nonce="x", evidence=(_evidence_tdx(rtmrs=(other_rtmr,)),)
    )
    decision = v.verify(policy, r2)
    assert decision.kind == cp.CompositeDecisionKind.FAILED


# ---------------------------------------------------------------------------
# Multi-failure dispositions
# ---------------------------------------------------------------------------


def test_verify_reports_all_failures_not_just_first():
    """When BOTH TDX (missing) AND TPM (nonce not bound) fail, the
    decision must show dispositions for BOTH."""
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    report = cp.CompositeReport(
        claimed_nonce="x",
        evidence=(_evidence_tpm(nonce_ok=False),),  # TDX missing, TPM bad
    )
    decision = v.verify(policy, report)
    assert decision.kind == cp.CompositeDecisionKind.FAILED
    assert len(decision.dispositions) == 2
    failures = [d for d in decision.dispositions if not d.satisfied]
    assert len(failures) == 2


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    v = cp.CompositeVerifier()
    policy = _policy_tdx_plus_tpm()
    # 2 pass, 1 fail.
    v.verify(
        policy, cp.CompositeReport("x", evidence=(_evidence_tdx(), _evidence_tpm()))
    )
    v.verify(
        policy, cp.CompositeReport("x", evidence=(_evidence_tdx(), _evidence_tpm()))
    )
    v.verify(
        policy,
        cp.CompositeReport(
            "x", evidence=(_evidence_tdx(signer="bad"), _evidence_tpm())
        ),
    )
    s = v.snapshot_stats()
    assert s.total_verifications == 3
    assert s.passed == 2
    assert s.failed == 1
    assert "signer_mismatch" in s.by_failure_reason


def test_snapshot_stats_returns_copy():
    v = cp.CompositeVerifier()
    s1 = v.snapshot_stats()
    v.verify(
        _policy_tdx_plus_tpm(),
        cp.CompositeReport("x", evidence=(_evidence_tdx(), _evidence_tpm())),
    )
    s2 = v.snapshot_stats()
    assert s1.total_verifications == 0
    assert s2.total_verifications == 1
