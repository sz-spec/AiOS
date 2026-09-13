"""
backend/services/attestation_composite_policy.py
==================================================

Sprint 16 / Item D1 — TDX composite-policy attestation per Intel Trust
Authority May 2026 update.

What this is
------------

From the 80-problem agent-era catalog, D1:
  "Intel TDX live-migration bugs (Feb 2026 Google Cloud assessment) —
   Google Cloud researchers disclosed multiple bugs in TDX Live Migration
   support; defense-in-depth complementary controls required."

The Intel Trust Authority May-2026 update introduced **composite
policies** — single attestation requests that evaluate evidence from
multiple TEE technologies at once (TDX + SEV-SNP + TPM). Composite
verification is harder to defeat than single-tech verification because
an attacker who compromises one platform (e.g. TDX live-migration bug)
still has to forge a matching report from a different technology.

This module:

  1. Defines the policy DSL — required evidence kinds + per-kind
     constraints (RTMR values, eat_nonce binding, expected signer).
  2. Evaluates a CompositeReport (collection of per-tech reports)
     against the policy.
  3. Returns CompositeDecision with per-evidence-kind disposition so
     auditors can see WHY a verification passed or failed.

Public surface
--------------

  CompositePolicyBuilder — fluent DSL
  CompositePolicy — frozen policy object
  CompositeEvidence — single per-tech report container
  CompositeReport — bundle of evidence + claimed nonce
  CompositeVerifier — evaluator
  CompositeVerifier.verify(policy, report) -> CompositeDecision

Cross-link
----------

D1 is the COMPOSITE layer; D5 (shipped Wave 1) ensures every individual
quote is nonce-bound. D1 + D5 together: each per-tech report inside the
composite goes through the D5 NonceGate first, then the composite
policy evaluator runs on the verified-nonce bundle.

Honest scope ceiling
--------------------

  - No real TEE crypto here — the per-tech report verification is the
    responsibility of upstream verifiers (D5 NonceGate for nonce
    binding, Intel TA / AMD SEV firmware for signature chain). This
    module is the policy decision layer that says "the bundle of
    reports satisfies the composite policy" or "it doesn't, and here's
    which evidence kind failed".
  - Evidence-kind taxonomy mirrors Intel TA's at May 2026 — extend on
    spec rev.
  - Composite policies are AND across required evidence kinds. OR
    semantics (e.g. "TDX OR SEV-SNP") are expressed via two distinct
    composite policies the relying party tries in sequence.

References:
  - Intel Trust Authority - What's New (May 2026)
    https://docs.trustauthority.intel.com/main/articles/articles/ita/whats-new.html
  - draft-ietf-rats-ar4si-09 (Attestation Results for Secure Interactions)
  - HECKLER paper (arxiv 2404.03387) — motivates composite defense
  - vOS Sprint 16 / D5 (backend/services/attestation_nonce_gate.py)
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Evidence kinds + decision
# ---------------------------------------------------------------------------


class EvidenceKind(str, enum.Enum):
    TDX = "tdx"  # Intel TDX TD quote
    SEV_SNP = "sev_snp"  # AMD SEV-SNP report
    TPM = "tpm"  # TPM 2.0 quote
    OPENSSL_FIPS = "openssl_fips"  # CMVP-validated module attestation


class CompositeDecisionKind(enum.IntEnum):
    PASSED = 0
    FAILED = 1


@dataclass(frozen=True)
class EvidenceConstraint:
    """Per-kind constraints. All MUST hold for the evidence to count
    as satisfying the composite policy."""

    kind: EvidenceKind
    expected_signer: Optional[str] = None  # CN of the signing cert
    required_rtmr_values: tuple[bytes, ...] = field(default_factory=tuple)
    require_nonce_bound: bool = True  # cross-link to D5


@dataclass(frozen=True)
class CompositePolicy:
    name: str
    required_evidence: tuple[EvidenceConstraint, ...]
    description: str = ""


@dataclass(frozen=True)
class CompositeEvidence:
    kind: EvidenceKind
    signer_cn: str
    rtmr_values: tuple[bytes, ...] = field(default_factory=tuple)
    nonce_verified_by_d5: bool = False
    raw_report_sha256: str = ""


@dataclass(frozen=True)
class CompositeReport:
    """Bundle of evidence + the nonce the verifier issued at request-time."""

    claimed_nonce: str
    evidence: tuple[CompositeEvidence, ...]


@dataclass(frozen=True)
class EvidenceDisposition:
    """Per-evidence-kind audit record."""

    kind: EvidenceKind
    satisfied: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CompositeDecision:
    kind: CompositeDecisionKind
    policy_name: str
    dispositions: tuple[EvidenceDisposition, ...]
    overall_reason: str


@dataclass
class CompositeVerifierStats:
    total_verifications: int = 0
    passed: int = 0
    failed: int = 0
    by_failure_reason: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CompositePolicyBuilder — fluent DSL
# ---------------------------------------------------------------------------


class CompositePolicyBuilder:
    def __init__(self, name: str, description: str = ""):
        if not name or not isinstance(name, str):
            raise ValueError("name required (non-empty string)")
        self._name = name
        self._description = description
        self._constraints: list[EvidenceConstraint] = []

    def require(
        self,
        kind: EvidenceKind,
        *,
        expected_signer: Optional[str] = None,
        required_rtmr_values: tuple[bytes, ...] = (),
        require_nonce_bound: bool = True,
    ) -> "CompositePolicyBuilder":
        if not isinstance(kind, EvidenceKind):
            raise TypeError("kind must be EvidenceKind")
        if expected_signer is not None and not isinstance(expected_signer, str):
            raise TypeError("expected_signer must be string")
        for rt in required_rtmr_values:
            if not isinstance(rt, (bytes, bytearray)):
                raise TypeError("required_rtmr_values entries must be bytes")
        self._constraints.append(
            EvidenceConstraint(
                kind=kind,
                expected_signer=expected_signer,
                required_rtmr_values=tuple(required_rtmr_values),
                require_nonce_bound=bool(require_nonce_bound),
            )
        )
        return self

    def build(self) -> CompositePolicy:
        if not self._constraints:
            raise ValueError("policy must require at least one evidence kind")
        return CompositePolicy(
            name=self._name,
            required_evidence=tuple(self._constraints),
            description=self._description,
        )


# ---------------------------------------------------------------------------
# CompositeVerifier
# ---------------------------------------------------------------------------


class CompositeVerifier:
    def __init__(self):
        self._stats = CompositeVerifierStats()
        self._lock = threading.Lock()

    def verify(
        self, policy: CompositePolicy, report: CompositeReport
    ) -> CompositeDecision:
        if not isinstance(policy, CompositePolicy):
            raise TypeError("policy must be CompositePolicy")
        if not isinstance(report, CompositeReport):
            raise TypeError("report must be CompositeReport")
        if not report.claimed_nonce:
            raise ValueError("report.claimed_nonce is required")

        dispositions = []
        all_satisfied = True
        # Index the report's evidence by kind for lookup.
        report_by_kind: dict[EvidenceKind, CompositeEvidence] = {}
        for e in report.evidence:
            report_by_kind[e.kind] = e

        for constraint in policy.required_evidence:
            evidence = report_by_kind.get(constraint.kind)
            if evidence is None:
                dispositions.append(
                    EvidenceDisposition(
                        kind=constraint.kind,
                        satisfied=False,
                        reasons=(f"required_evidence_{constraint.kind.value}_missing",),
                    )
                )
                all_satisfied = False
                continue
            reasons = []
            satisfied = True
            if (
                constraint.expected_signer is not None
                and evidence.signer_cn != constraint.expected_signer
            ):
                reasons.append(
                    f"signer_mismatch: expected={constraint.expected_signer!r} "
                    f"actual={evidence.signer_cn!r}"
                )
                satisfied = False
            if constraint.required_rtmr_values:
                evidence_rtmr_set = set(evidence.rtmr_values)
                missing = [
                    v
                    for v in constraint.required_rtmr_values
                    if v not in evidence_rtmr_set
                ]
                if missing:
                    reasons.append(
                        f"rtmr_values_missing: {len(missing)} of "
                        f"{len(constraint.required_rtmr_values)}"
                    )
                    satisfied = False
            if constraint.require_nonce_bound and not evidence.nonce_verified_by_d5:
                reasons.append("nonce_not_verified_by_d5_gate")
                satisfied = False
            if satisfied and not reasons:
                reasons.append("all_constraints_satisfied")
            dispositions.append(
                EvidenceDisposition(
                    kind=constraint.kind,
                    satisfied=satisfied,
                    reasons=tuple(reasons),
                )
            )
            if not satisfied:
                all_satisfied = False

        kind = (
            CompositeDecisionKind.PASSED
            if all_satisfied
            else CompositeDecisionKind.FAILED
        )
        overall_reason = (
            "all_required_evidence_satisfied"
            if all_satisfied
            else "one_or_more_evidence_kinds_failed"
        )

        with self._lock:
            self._stats.total_verifications += 1
            if all_satisfied:
                self._stats.passed += 1
            else:
                self._stats.failed += 1
                # Tag the first failing reason for stat aggregation.
                for d in dispositions:
                    if not d.satisfied and d.reasons:
                        key = d.reasons[0].split(":")[0]
                        self._stats.by_failure_reason[key] = (
                            self._stats.by_failure_reason.get(key, 0) + 1
                        )
                        break

        return CompositeDecision(
            kind=kind,
            policy_name=policy.name,
            dispositions=tuple(dispositions),
            overall_reason=overall_reason,
        )

    def snapshot_stats(self) -> CompositeVerifierStats:
        with self._lock:
            return CompositeVerifierStats(
                total_verifications=self._stats.total_verifications,
                passed=self._stats.passed,
                failed=self._stats.failed,
                by_failure_reason=dict(self._stats.by_failure_reason),
            )


__all__ = [
    "EvidenceKind",
    "CompositeDecisionKind",
    "EvidenceConstraint",
    "CompositePolicy",
    "CompositeEvidence",
    "CompositeReport",
    "EvidenceDisposition",
    "CompositeDecision",
    "CompositeVerifierStats",
    "CompositePolicyBuilder",
    "CompositeVerifier",
]
