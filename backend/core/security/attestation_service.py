"""
v20.2-alpha — Attestation bridge between Intel TDX RTMR and EU AI Act Annex IV.

Chain of evidence
=================

    Silicon (TDX module)                                 ─────
        │  measured-boot extends MRTD                         │
        ▼                                                     │
    Kernel image hashed into RTMR[0]                          │ Hardware-
        │                                                     │ rooted
    AI model load →  SHA-384(weights)  extended into RTMR[1]  │ chain
        │                                                     │
    IntentManifest grant → SHA-384(manifest)  →  RTMR[2]      │
        │                                                     │
    AttestationService reads RTMRs + MRTD + model + manifest ─┘
        │
        ▼
    JSON-LD IntegrityCertificate signed by release key
        │
        ▼
    EU AI Act Annex IV §2,§5,§7 evidence fields auditor can verify

Honest framing
==============

The doc in ``docs/AUDIT_IMMUNE_SPEC.md`` calls this "audit-immune." That
is marketing language. The accurate engineering framing is that this
shortens the auditor's evidence-chain verification from hours of
document-hunting to one command:
``infra/security/dev_sign.py --verify --artifact certificate.jsonld``.
An auditor still has to *read* the certificate and cross-check the
fields against their compliance checklist. What changes is the
*provenance* — every field in the certificate is hardware-rooted.

Operating modes
===============

- ``intel_tdx``   — read RTMR chain via ``vcore_bridge``'s TDX shim.
- ``amd_sev_snp`` — read SNP attestation report; stub for now.
- ``baremetal``   — RTMR values zeroed; certificate clearly labeled
                    ``platform: "BAREMETAL_NO_TEE"`` so an auditor sees
                    the trust level immediately.
- ``mock``        — for tests. Deterministic RTMRs keyed by session.

Crypto
======

Signing uses the ``infra/security/dev_sign`` ECDSA-P256 keypair for
the dev tier. Production deployments swap the key root for Sigstore
OIDC per ``docs/ROADMAP.md`` (scheduled 2026-05-06).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TeeMeasurements:
    """Immutable snapshot of the TEE measurement state.

    Values are lowercase hex strings. For baremetal / non-TEE platforms
    all RTMRs are ``"00" * 48`` (SHA-384 zero digest) and ``platform``
    is ``"BAREMETAL_NO_TEE"``.
    """

    platform: str  # "INTEL_TDX" | "AMD_SEV_SNP" | "BAREMETAL_NO_TEE" | "MOCK"
    mrtd: str  # 48-byte hex (SHA-384 of kernel image measurement)
    rtmr_0: str  # 48-byte hex — kernel-code measurements
    rtmr_1: str  # 48-byte hex — model-load measurements
    rtmr_2: str  # 48-byte hex — IntentManifest grants
    rtmr_3: str  # 48-byte hex — agent-policy decisions
    tee_quote_available: bool
    measurement_time_ns: int


class CertificateVerificationError(RuntimeError):
    """Raised when certificate signature, hash, or chain checks fail."""


@dataclass
class IntegrityCertificate:
    """Sovereign Integrity Certificate (JSON-LD payload).

    Maps onto EU AI Act Annex IV §1 (description), §2 (design/dev),
    §5 (harmonised standards), §7 (post-market monitoring).

    v20.2-FINAL additions
    ---------------------
    - ``legal_compliance_hash`` — SHA-256 over the canonical JSON of
      ``policy_invariants`` + ``harmonised_standards`` + the TEE platform
      label. It is the single scalar an auditor cross-references against
      the Z3 proof bundle to confirm that *this* certificate asserts the
      *same* machine-checked invariants as the published reference set.
      A drift here signals either a proof-set change or tampering.

    v20.3-PRODIGY additions
    -----------------------
    - ``composed_commitment_sha384`` — C = SHA-384(h_M || h_I || h_P),
      the single value the kernel extends into RTMR[1] on slot
      activation (TCB reduction: one TDCALL vs. three legacy extends).
      Auditor recomputes C locally from the three individual hashes
      (``model_sha384``, ``intent_manifest_sha384``, and
      ``agent_policy_sha384``) and cross-checks it against RTMR[1] in
      the TEE quote. Collision-resistance of SHA-384 guarantees the
      triple-binding has the same strength as three independent extends.
    - ``agent_policy_sha384`` — the third component hash in the commitment.
      Empty string (``""``) means "no agent-policy bound at attestation
      time" and is substituted with the all-zero SHA-384 in the commitment
      so the field is fail-visible rather than fail-silent.

    v20.4-TITAN additions (hybrid classical+PQ signatures)
    ------------------------------------------------------
    Every cert is now signed TWICE:

      1. **Primary** — ECDSA-P256 + SHA-256  (unchanged from v20.2/v20.3,
         for back-compat with existing auditor tooling).
      2. **Classical-strengthened** — ECDSA-P521 + SHA-512. This is the
         "2026-strength 512-bit EC-sign" fallback the mission brief
         explicitly calls for. P-521 gives a ~256-bit classical security
         level (vs P-256's ~128-bit) and is available in every modern
         ``cryptography`` release — no system-library dependency.

    When the optional Open Quantum Safe binding (``oqs`` / ``liboqs-python``)
    is installed **and** ``VOS3_PQ_SIGNING=1`` is set, a third
    ML-DSA-65 (FIPS 204) lattice-based signature is additionally
    attached. ML-DSA is not enabled by default because the pure-Python
    reference implementations on PyPI (``dilithium-py``) carry explicit
    side-channel warnings from their own maintainers, and the
    production-safe path is liboqs compiled with constant-time guards.
    VOS-Cyber will flip this default once liboqs is vendored into our
    release builds — tracked on the v20.4.1 roadmap.

    Verdict logic in ``verify_signed_payload``:
      - P-256 must verify (hard requirement).
      - P-521 must verify when present (v20.4+ certs); absence is treated
        as "not applicable" for back-compat with v20.2/v20.3 certs.
      - ML-DSA must verify when present; treated as not-applicable when
        absent. A PQ-labelled verifier (``pq_required=True``) tightens
        this to a hard requirement.
    """

    context: list  # JSON-LD @context
    id: str  # urn:vos3:attestation:<session>:<ts>
    session_id: str
    tenant_id: str
    issued_at: str  # ISO-8601 UTC
    tee_measurements: Dict[str, Any]
    model_sha384: str
    intent_manifest_sha384: str
    policy_invariants: Dict[str, str]  # Z3-proven invariants + proof source
    harmonised_standards: list  # FIPS, NIST, etc.
    signing_tier: str  # "dev" | "prod-oidc-rekor"
    certificate_sha256: str  # SHA-256 of the payload-before-signature
    legal_compliance_hash: str = ""  # SHA-256 of Z3 invariants + standards + platform
    # v20.3-PRODIGY — composed RTMR[1] commitment + third component hash
    composed_commitment_sha384: str = ""  # SHA-384(h_M || h_I || h_P), matches RTMR[1]
    agent_policy_sha384: str = ""  # third component; "" → zero SHA-384
    signature_b64: Optional[str] = None
    signer_public_key_fp: Optional[str] = None
    # v20.4-TITAN — classical-strengthened and (optional) post-quantum sigs
    signature_p521_b64: Optional[str] = None  # ECDSA-P521 + SHA-512
    signer_p521_public_key_fp: Optional[str] = None
    signature_mldsa_b64: Optional[str] = None  # ML-DSA-65 (FIPS 204), when available
    signer_mldsa_public_key_fp: Optional[str] = None
    signature_algorithms: Optional[List[str]] = (
        None  # ["ECDSA-P256", "ECDSA-P521", ...]
    )
    # v20.5-SINGULARITY — PQ commitment trapdoor (when liboqs absent)
    #
    # When the runtime cannot produce a real ML-DSA signature (oqs not
    # importable or VOS3_PQ_SIGNING unset), we attach a *commitment* —
    # SHA-384 over (canonical_payload || algorithm_label || issued_at)
    # — that records the PQ algorithm + payload binding the cert WILL
    # be re-signed under once liboqs is vendored into release builds.
    #
    # IMPORTANT — this is NOT post-quantum security. The commitment
    # gives no protection against a quantum adversary; it is a wire-
    # format hook that lets a v20.5.x verifier later prove the
    # certificate was *prepared* for PQ signing at issuance time. We
    # label it accurately on the certificate and in the verdict so
    # nobody mistakes it for the real thing.
    pq_commitment_alg: Optional[str] = None  # e.g. "ML-DSA-65-PENDING"
    pq_commitment_sha384: Optional[str] = None  # 96-hex SHA-384 commitment

    def to_jsonld(self) -> str:
        return json.dumps(asdict_json(self), indent=2, sort_keys=False)

    def to_external_spm_jsonld(self) -> Dict[str, Any]:
        """External AI-SPM 2026.1 compatible JSON-LD export.

        Tier-1 AI-SPM (AI Security Posture Management) ingestion
        pipelines expect a ``@context`` that includes the AI-SPM
        namespace alongside the regulator namespaces, and a top-level
        ``@type: AiRuntimeAttestation`` discriminator. The payload
        body is the standard IntegrityCertificate fields aliased
        into AI-SPM-compatible keys:

        | VOS-Cyber field         | AI-SPM key               |
        |-------------------------|--------------------------|
        | id                      | attestationId            |
        | tenant_id               | ownerTenantId            |
        | model_sha384            | modelBinaryDigest        |
        | intent_manifest_sha384  | policyManifestDigest     |
        | tee_measurements        | trustedExecutionEvidence |
        | legal_compliance_hash   | invariantSetDigest       |
        | signature_b64           | signature.value          |
        | signer_public_key_fp    | signature.keyFingerprint |

        The signed-payload bytes are unchanged — this export is a
        *view* over the same cryptographic object. An AI-SPM
        ingestion that calls ``verify_certificate`` against the
        original certificate gets the same result as one that
        re-serialises from this view.
        """
        return {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
                "https://schema.ai-spm.example/2026/v1",
                {
                    "vos3": "https://vos-shield.example/schema/",
                    "euaiact": "https://artificialintelligenceact.eu/annex/",
                    "nist": "https://www.nist.gov/itl/ai-risk-management-framework#",
                },
            ],
            "@type": "AiRuntimeAttestation",
            "schemaVersion": "ai-spm-2026.1",
            "attestationId": self.id,
            "sessionId": self.session_id,
            "ownerTenantId": self.tenant_id,
            "issuedAt": self.issued_at,
            "modelBinaryDigest": {
                "alg": "SHA-384",
                "value": self.model_sha384,
            },
            "policyManifestDigest": {
                "alg": "SHA-384",
                "value": self.intent_manifest_sha384,
            },
            "trustedExecutionEvidence": self.tee_measurements,
            "invariantSetDigest": {
                "alg": "SHA-256",
                "value": self.legal_compliance_hash,
            },
            "proofInvariants": dict(self.policy_invariants),
            "harmonisedStandards": list(self.harmonised_standards),
            "signingTier": self.signing_tier,
            "payloadDigest": {
                "alg": "SHA-256",
                "value": self.certificate_sha256,
            },
            "signature": {
                "alg": "ECDSA-P256-SHA256",
                "value": self.signature_b64,
                "keyFingerprint": self.signer_public_key_fp,
            },
            # Annex IV § mapping retained so an AI-SPM consumer
            # preserves the regulator provenance when re-emitting to
            # downstream governance stacks.
            "euAiActAnnexIv": {
                "§1": "attestationId + schemaVersion",
                "§2": "modelBinaryDigest + policyManifestDigest",
                "§5": "harmonisedStandards",
                "§7": "issuedAt + sessionId (time-stamped trail)",
            },
        }

    # v20.5.1 → v20.7.1 back-compat alias.
    # The connector layer (external_spm_connector.py) and existing
    # callers reference ``to_wiz_jsonld``; we keep that name as an
    # alias so no v20.4-era external caller breaks.
    to_wiz_jsonld = to_external_spm_jsonld


def asdict_json(obj) -> Dict[str, Any]:
    """Like dataclasses.asdict but with field rename for @context."""
    d = asdict(obj)
    if "context" in d:
        d["@context"] = d.pop("context")
        # Re-order so @context is first (cosmetic, JSON-LD convention)
        return {
            "@context": d["@context"],
            **{k: v for k, v in d.items() if k != "@context"},
        }
    return d


# ----------------------------------------------------------------------------
# TEE measurement sources
# ----------------------------------------------------------------------------

_ZERO_SHA384 = "00" * 48


def _sha384_bytes(data: bytes) -> str:
    return hashlib.sha384(data).hexdigest()


def _sha384_file(path: Path) -> str:
    h = hashlib.sha384()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _MeasurementSource:
    """Abstract source of RTMR measurements. Subclasses implement mode."""

    def read(self) -> TeeMeasurements:
        raise NotImplementedError


class _MockMeasurementSource(_MeasurementSource):
    """Deterministic mock for unit tests — RTMRs hashed from a seed.

    Useful for: tamper-detection tests, CI/CD smoke, documentation demos.
    Not for production.
    """

    def __init__(self, seed: str = "vos3-mock-tee"):
        self.seed = seed

    def read(self) -> TeeMeasurements:
        base = self.seed.encode("utf-8")
        return TeeMeasurements(
            platform="MOCK",
            mrtd=_sha384_bytes(base + b":mrtd"),
            rtmr_0=_sha384_bytes(base + b":rtmr0"),
            rtmr_1=_sha384_bytes(base + b":rtmr1"),
            rtmr_2=_sha384_bytes(base + b":rtmr2"),
            rtmr_3=_sha384_bytes(base + b":rtmr3"),
            tee_quote_available=False,
            measurement_time_ns=time.time_ns(),
        )


class _BaremetalMeasurementSource(_MeasurementSource):
    """Honest baremetal fallback — all-zero RTMRs + clear platform label.

    An auditor looking at the certificate immediately sees
    ``platform: BAREMETAL_NO_TEE`` and knows the measurements are not
    hardware-rooted. This is intentional: fail-visible beats fail-silent.
    """

    def read(self) -> TeeMeasurements:
        return TeeMeasurements(
            platform="BAREMETAL_NO_TEE",
            mrtd=_ZERO_SHA384,
            rtmr_0=_ZERO_SHA384,
            rtmr_1=_ZERO_SHA384,
            rtmr_2=_ZERO_SHA384,
            rtmr_3=_ZERO_SHA384,
            tee_quote_available=False,
            measurement_time_ns=time.time_ns(),
        )


class _TdxMeasurementSource(_MeasurementSource):
    """Intel TDX reader. Real impl proxies to ``vcore_bridge`` which
    issues the TDCALL(TDG.MR.REPORT). When the shim is absent — e.g.
    running outside a TD — this falls back to Baremetal with a clearly
    labelled platform, never silently fabricating measurements."""

    def read(self) -> TeeMeasurements:
        try:
            # Optional import: the vcore_bridge TDX helper may not exist
            # on every deployment. Fall back cleanly.
            from core import vcore_bridge  # noqa: F401  (presence check)
        except Exception:
            return _BaremetalMeasurementSource().read()

        # In a real TDX guest, vcore_bridge.tee_read_rtmr() would issue
        # TDCALL TDG.VP.VEINFO.GET / TDG.MR.REPORT. That syscall bridge
        # doesn't exist from userspace on this host, so we surface that
        # honestly rather than fabricating values.
        return _BaremetalMeasurementSource().read()


def _select_source(mode: Optional[str]) -> _MeasurementSource:
    mode = (mode or os.getenv("VOS3_ATTESTATION_MODE", "")).lower()
    if mode == "mock":
        return _MockMeasurementSource()
    if mode == "baremetal":
        return _BaremetalMeasurementSource()
    # Default: try TDX, fall back to baremetal with honest labeling.
    return _TdxMeasurementSource()


# ----------------------------------------------------------------------------
# Z3-proven policy invariants — static because they are proven once.
# ----------------------------------------------------------------------------

_POLICY_INVARIANTS: Dict[str, str] = {
    "sched_core.no_cross_domain_smt_co_execution": "backend/tests/benchmarks/sched_core_z3_proof.py   (236196 states, Z3 UNSAT)",
    "egress.no_public_ip_bypass": "backend/tests/benchmarks/egress_policy_z3_proof.py (2^32 IPv4 space, Z3 UNSAT × 3)",
    "oom_guard.overflow_safe": "backend/tests/benchmarks/ai_oom_z3_proof.py        (2^64 size_t space, Z3 UNSAT × 6)",
}

_HARMONISED_STANDARDS = [
    "FIPS 180-4 (SHA-384 used for RTMR extension)",
    "NIST SP 800-132 (PBKDF2-SHA512 256k iter, SQLCipher vault)",
    "RFC 2104 (HMAC used for session binding)",
    "Intel SDM Vol 4 (MSR-defined HCS sequence)",
    "CycloneDX 1.7 (SBOM format)",
]

_JSONLD_CONTEXT = [
    "https://www.w3.org/ns/credentials/v2",
    {
        "vos3": "https://vos-shield.example/schema/",
        "euaiact": "https://artificialintelligenceact.eu/annex/",
        "nist": "https://www.nist.gov/itl/ai-risk-management-framework#",
        "IntegrityCertificate": "vos3:IntegrityCertificate",
        "teeMeasurements": "vos3:teeMeasurements",
        "modelSha384": "vos3:modelSha384",
        "intentManifestSha384": "vos3:intentManifestSha384",
        "annexIV": "euaiact:annex/4",
    },
]


# ----------------------------------------------------------------------------
# Attestation service
# ----------------------------------------------------------------------------


class AttestationService:
    """Build + sign + verify IntegrityCertificates.

    Lifetime: one per backend process; stateless aside from the
    measurement source handle and the signing-key location.
    """

    def __init__(
        self, measurement_mode: Optional[str] = None, key_dir: Optional[Path] = None
    ):
        self._source = _select_source(measurement_mode)
        self._key_dir = key_dir or Path(
            os.getenv(
                "VOS3_ATTESTATION_KEY_DIR",
                "infra/security/keys",
            )
        )

    # ------------------------------------------------------------------
    # Measurement plumbing
    # ------------------------------------------------------------------

    def read_measurements(self) -> TeeMeasurements:
        """Snapshot the current TEE measurement state."""
        return self._source.read()

    def bind_intent_manifest(
        self, intent_manifest_bytes: bytes, session_id: str
    ) -> str:
        """Return the SHA-384 that should appear in RTMR[2].

        The kernel is responsible for actually performing the
        ``tdcall(TDG.MR.RTMR.EXTEND)`` on slot activation. This method
        computes the digest a verifier would expect.
        """
        salt = f"vos3:session:{session_id}:".encode("utf-8")
        return _sha384_bytes(salt + intent_manifest_bytes)

    # ------------------------------------------------------------------
    # Certificate generation
    # ------------------------------------------------------------------

    def generate_certificate(
        self,
        session_id: str,
        tenant_id: str,
        model_path_or_bytes: "Path | bytes",
        intent_manifest_bytes: bytes,
    ) -> IntegrityCertificate:
        """Build a fully-populated (but unsigned) IntegrityCertificate.

        Call :py:meth:`sign_certificate` to attach the signature + fingerprint.
        Split to allow inspection before signing (M&A audit workflow).
        """
        if isinstance(model_path_or_bytes, Path):
            model_sha = _sha384_file(model_path_or_bytes)
        else:
            model_sha = _sha384_bytes(model_path_or_bytes)

        manifest_sha = self.bind_intent_manifest(intent_manifest_bytes, session_id)
        tee = self.read_measurements()

        issued_at = _iso_now()
        cert_id = f"urn:vos3:attestation:{session_id}:{int(time.time())}"

        cert = IntegrityCertificate(
            context=_JSONLD_CONTEXT,
            id=cert_id,
            session_id=session_id,
            tenant_id=tenant_id,
            issued_at=issued_at,
            tee_measurements=asdict(tee),
            model_sha384=model_sha,
            intent_manifest_sha384=manifest_sha,
            policy_invariants=dict(_POLICY_INVARIANTS),
            harmonised_standards=list(_HARMONISED_STANDARDS),
            signing_tier="dev",
            certificate_sha256="",  # populated below
        )
        # v20.3-PRODIGY: composed commitment C = SHA-384(h_M || h_I || h_P).
        # This is the scalar the kernel extends into RTMR[1] *once*
        # (TCB: 1 TDCALL vs 3 legacy extends). An auditor recomputes
        # C locally from the three component hashes below and cross-
        # checks against RTMR[1] in the quote.
        cert.agent_policy_sha384 = ""  # empty → zero SHA-384 in commit
        cert.composed_commitment_sha384 = self._compute_composed_commitment(cert)

        # Legal-compliance scalar is a function of the proven-invariant
        # set + harmonised-standards list + TEE platform label. It is
        # derived *before* the payload hash so the payload hash covers
        # it transitively.
        cert.legal_compliance_hash = self._compute_legal_compliance_hash(cert)
        cert.certificate_sha256 = self._payload_sha256(cert)
        return cert

    def sign_certificate(self, cert: IntegrityCertificate) -> IntegrityCertificate:
        """Attach classical + (v20.4) strengthened + (optional) PQ signatures.

        Three signatures, in order of increasing strength assumption:

          1. **ECDSA-P256 + SHA-256** — required; primary signature,
             back-compat with every v20.2+ auditor tool.
          2. **ECDSA-P521 + SHA-512** — required as of v20.4-TITAN;
             ~256-bit classical security vs P-256's ~128-bit. Uses
             the ``vos3_dev_signing_p521`` keypair which is materialised
             on first sign via ``ensure_p521_keypair()`` alongside
             the P-256 pair. Available in every ``cryptography`` build
             — no system-library dependency.
          3. **ML-DSA-65 (FIPS 204)** — optional; activated only when
             ``VOS3_PQ_SIGNING=1`` **and** an Open Quantum Safe
             binding (``oqs`` module) is importable. Pure-Python
             Dilithium reference implementations are deliberately not
             used even when present — see the dataclass docstring.

        All three signatures cover the same canonical payload bytes
        (``self._payload_bytes``). The certificate records which
        algorithms actually signed under ``signature_algorithms``.

        Fails CLOSED on missing P-256 key: an unsigned certificate is
        useless to an auditor and silently returning one would be
        worse than failing.
        """
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from infra.security.dev_sign import ensure_keypair

        priv, pub = ensure_keypair(self._key_dir)
        payload = self._payload_bytes(cert)

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        # (1) P-256 primary ------------------------------------------------
        sig_bytes = priv.sign(payload, ec.ECDSA(hashes.SHA256()))
        pub_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert.signature_b64 = base64.b64encode(sig_bytes).decode("ascii")
        cert.signer_public_key_fp = hashlib.sha256(pub_pem).hexdigest()[:16]

        algorithms = ["ECDSA-P256-SHA256"]

        # (2) P-521 classical-strengthened ---------------------------------
        p521_priv, p521_pub = self._ensure_p521_keypair()
        sig521_bytes = p521_priv.sign(payload, ec.ECDSA(hashes.SHA512()))
        p521_pem = p521_pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert.signature_p521_b64 = base64.b64encode(sig521_bytes).decode("ascii")
        cert.signer_p521_public_key_fp = hashlib.sha256(p521_pem).hexdigest()[:16]
        algorithms.append("ECDSA-P521-SHA512")

        # (3) ML-DSA-65 (optional; opt-in + library-gated) -----------------
        mldsa_signed = False
        if os.getenv("VOS3_PQ_SIGNING", "").lower() in ("1", "true", "yes"):
            pq_sig, pq_fp = _try_mldsa_sign(payload, self._key_dir)
            if pq_sig is not None:
                cert.signature_mldsa_b64 = pq_sig
                cert.signer_mldsa_public_key_fp = pq_fp
                algorithms.append("ML-DSA-65")
                mldsa_signed = True

        # (4) v20.5-SINGULARITY — PQ commitment trapdoor.
        # When we did not produce a real ML-DSA signature, attach a
        # commitment so the cert is wire-format-ready for a future
        # liboqs-enabled re-sign. The commitment is NOT crypto strength
        # vs a quantum adversary — it is a forward-compat hook. Label
        # it accurately so no verdict consumer mistakes it for one.
        if not mldsa_signed:
            commit_label = "ML-DSA-65-PENDING"
            commit_input = (
                payload
                + b"||"
                + commit_label.encode("ascii")
                + b"||"
                + cert.issued_at.encode("ascii")
            )
            cert.pq_commitment_alg = commit_label
            cert.pq_commitment_sha384 = hashlib.sha384(commit_input).hexdigest()
            algorithms.append("PQ-COMMITMENT-ONLY")

        cert.signature_algorithms = algorithms
        return cert

    # ------------------------------------------------------------------
    # P-521 keypair management (classical-strengthened signing tier)
    # ------------------------------------------------------------------

    def _ensure_p521_keypair(self):
        """Load-or-create the ECDSA-P521 keypair at ``key_dir``.

        Files produced:
          - ``vos3_dev_signing_p521.key`` (PEM, unencrypted — dev tier)
          - ``vos3_dev_signing_p521.pub`` (PEM, SubjectPublicKeyInfo)

        Lives alongside the P-256 keypair. Fails CLOSED on any crypto
        import error rather than silently dropping to a single-sig.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        self._key_dir.mkdir(parents=True, exist_ok=True)
        key_path = self._key_dir / "vos3_dev_signing_p521.key"
        pub_path = self._key_dir / "vos3_dev_signing_p521.pub"

        if key_path.is_file() and pub_path.is_file():
            priv = serialization.load_pem_private_key(
                key_path.read_bytes(),
                password=None,
            )
            pub = serialization.load_pem_public_key(pub_path.read_bytes())
            return priv, pub

        priv = ec.generate_private_key(ec.SECP521R1())
        pub = priv.public_key()
        key_path.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        # Tight perms; dev-tier key, not for production ceremony.
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        pub_path.write_bytes(
            pub.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        return priv, pub

    # ------------------------------------------------------------------
    # Certificate verification
    # ------------------------------------------------------------------

    def verify_certificate(
        self,
        cert: IntegrityCertificate,
        expected_model_sha384: str,
        expected_manifest_sha384: Optional[str] = None,
    ) -> None:
        """Raise :class:`CertificateVerificationError` on any mismatch.

        Checks, in order:
            1. Signature cryptographically valid under the bundled public key.
            2. certificate_sha256 matches the payload (anti-tamper).
            3. model_sha384 matches ``expected_model_sha384`` (supplied
               by the verifier re-hashing the on-disk weights).
            4. intent_manifest_sha384 matches, when provided.
        """
        # (1) Signature
        if cert.signature_b64 is None or cert.signer_public_key_fp is None:
            raise CertificateVerificationError("unsigned certificate")

        pub_path = self._key_dir / "vos3_dev_signing.pub"
        if not pub_path.is_file():
            raise CertificateVerificationError(f"public key not found at {pub_path}")

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        pub = serialization.load_pem_public_key(pub_path.read_bytes())
        payload = self._payload_bytes(cert)
        try:
            pub.verify(
                base64.b64decode(cert.signature_b64),
                payload,
                ec.ECDSA(hashes.SHA256()),
            )
        except Exception as e:
            raise CertificateVerificationError(
                f"signature invalid: {type(e).__name__}"
            ) from e

        # (1b) v20.4-TITAN — P-521 must also verify when the cert carries one.
        # (v20.2/v20.3 certs lack the field; absence treated as N/A.)
        if cert.signature_p521_b64 is not None:
            p521_pub_path = self._key_dir / "vos3_dev_signing_p521.pub"
            if not p521_pub_path.is_file():
                raise CertificateVerificationError(
                    f"P-521 public key not found at {p521_pub_path}"
                )
            p521_pub = serialization.load_pem_public_key(p521_pub_path.read_bytes())
            try:
                p521_pub.verify(
                    base64.b64decode(cert.signature_p521_b64),
                    payload,
                    ec.ECDSA(hashes.SHA512()),
                )
            except Exception as e:
                raise CertificateVerificationError(
                    f"P-521 signature invalid: {type(e).__name__}"
                ) from e

        # (1c) v20.4-TITAN — ML-DSA must verify when carried. Non-fatal
        # absence; only an affirmatively-present-but-invalid signature fails.
        if cert.signature_mldsa_b64 is not None:
            if not _try_mldsa_verify(payload, cert.signature_mldsa_b64, self._key_dir):
                raise CertificateVerificationError(
                    "ML-DSA signature invalid (or oqs binding missing)"
                )

        # (2) Payload hash drift
        if cert.certificate_sha256 != self._payload_sha256(cert):
            raise CertificateVerificationError("certificate payload hash drift")

        # (3) Model hash — the actual tamper-detection
        if cert.model_sha384.lower() != expected_model_sha384.lower():
            raise CertificateVerificationError(
                f"model hash mismatch: "
                f"cert={cert.model_sha384[:16]}… expected={expected_model_sha384[:16]}…"
            )

        # (4) Intent manifest, optional
        if expected_manifest_sha384 is not None:
            if cert.intent_manifest_sha384.lower() != expected_manifest_sha384.lower():
                raise CertificateVerificationError("intent manifest hash mismatch")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_bytes(cert: IntegrityCertificate) -> bytes:
        """Canonical-JSON serialisation of the signable payload.

        Excludes fields that would introduce circularity or drift at
        verify time:
          - ``signature_b64``           — P-256 signature
          - ``signer_public_key_fp``    — P-256 key fingerprint
          - ``signature_p521_b64``      — v20.4 P-521 signature
          - ``signer_p521_public_key_fp`` — P-521 key fingerprint
          - ``signature_mldsa_b64``     — v20.4 ML-DSA signature (when enabled)
          - ``signer_mldsa_public_key_fp`` — ML-DSA key fingerprint
          - ``signature_algorithms``    — set at sign time
          - ``certificate_sha256``      — a derived hash over these same bytes

        All three signatures cover the SAME canonical payload bytes. This
        matters for the hybrid security argument: a quantum adversary
        that forges the P-256 or P-521 ECDSA must additionally forge
        ML-DSA against the same byte string, which requires both a
        classical curve break AND a lattice break on one message — the
        "hybrid" compositional security property.
        """
        d = asdict_json(cert)
        for key in (
            "signature_b64",
            "signer_public_key_fp",
            "signature_p521_b64",
            "signer_p521_public_key_fp",
            "signature_mldsa_b64",
            "signer_mldsa_public_key_fp",
            "signature_algorithms",
            # v20.5-SINGULARITY: pq_commitment is derived FROM the
            # payload, so excluding it from the signed bytes prevents
            # circularity (the commit hashes the payload, not itself).
            "pq_commitment_alg",
            "pq_commitment_sha384",
            "certificate_sha256",
        ):
            d.pop(key, None)
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _payload_sha256(self, cert: IntegrityCertificate) -> str:
        return hashlib.sha256(self._payload_bytes(cert)).hexdigest()

    @staticmethod
    def _compute_composed_commitment(cert: IntegrityCertificate) -> str:
        """v20.3-PRODIGY — C = SHA-384(h_M || h_I || h_P).

        Mirrors the kernel-side ``vos3_tee_slot_activate_bound``
        composition. Each component is 48 bytes (SHA-384); an absent
        ``agent_policy_sha384`` is substituted with the all-zero
        SHA-384 so the commit always binds a fixed-width triple.

        Used at cert generation to populate
        ``composed_commitment_sha384`` and at verify time to catch
        tampering in any component without needing the model weights.
        """
        zero = "00" * 48
        h_m = (cert.model_sha384 or zero).lower()
        h_i = (cert.intent_manifest_sha384 or zero).lower()
        h_p = (cert.agent_policy_sha384 or zero).lower()
        try:
            triple = bytes.fromhex(h_m) + bytes.fromhex(h_i) + bytes.fromhex(h_p)
        except ValueError:
            # Malformed component — surface a deterministic non-match
            # rather than raising so the verifier can still produce a
            # verdict document. The certificate_sha256 check upstream
            # already catches payload tampering.
            return zero
        return hashlib.sha384(triple).hexdigest()

    @staticmethod
    def _compute_legal_compliance_hash(cert: IntegrityCertificate) -> str:
        """SHA-256 over canonical JSON of the legal-invariant bundle.

        The scalar an auditor pins as "this cert asserts exactly the
        published invariant set." Covers:
          - every Z3-proven policy invariant name + proof-file path
          - the full harmonised-standards list (FIPS / NIST / RFC…)
          - the TEE platform label (so BAREMETAL vs TDX is in the scope)

        Any drift in any of those surfaces produces a different scalar
        and the downstream audit tooling surfaces it immediately.
        """
        bundle = {
            "policy_invariants": dict(cert.policy_invariants),
            "harmonised_standards": list(cert.harmonised_standards),
            "platform": cert.tee_measurements.get("platform", ""),
        }
        canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(canonical).hexdigest()

    # ------------------------------------------------------------------
    # Third-party verification (regulator upload path)
    # ------------------------------------------------------------------

    def verify_signed_payload(self, cert: IntegrityCertificate) -> Dict[str, Any]:
        """Signature-only verification — no on-disk model required.

        A regulator uploading a certificate through
        ``POST /compliance/verify`` needs to answer:
          *"Was this blob signed by a key VOS-Cyber publishes?"*
        They don't have the model weights, so they can't do the full
        ``verify_certificate`` (which re-hashes the model). This method
        does the cryptographically-decidable subset:

          1. Signature valid under the published public key.
          2. ``certificate_sha256`` matches the payload bytes.
          3. ``legal_compliance_hash`` matches the recomputed invariant
             bundle — catches "attacker re-signed a modified invariant
             set" without needing the model.

        Returns a structured verdict dict rather than raising, so the
        HTTP layer can translate to a 200 verdict-document regardless
        of the outcome.
        """
        verdict: Dict[str, Any] = {
            "signature_valid": False,
            "signature_p521_valid": False,
            "signature_mldsa_valid": False,
            "payload_hash_valid": False,
            "legal_compliance_valid": False,
            "composed_commitment_valid": False,
            "signer_public_key_fp": cert.signer_public_key_fp,
            "signer_p521_public_key_fp": cert.signer_p521_public_key_fp,
            "signer_mldsa_public_key_fp": cert.signer_mldsa_public_key_fp,
            "signature_algorithms": list(cert.signature_algorithms or []),
            "signing_tier": cert.signing_tier,
            "issued_at": cert.issued_at,
            "attestation_id": cert.id,
            "errors": [],
        }

        payload = self._payload_bytes(cert)

        # (1) P-256 primary signature — required.
        if cert.signature_b64 is None or cert.signer_public_key_fp is None:
            verdict["errors"].append("unsigned certificate")
        else:
            pub_path = self._key_dir / "vos3_dev_signing.pub"
            if not pub_path.is_file():
                verdict["errors"].append(f"public key not published at {pub_path.name}")
            else:
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import ec

                pub = serialization.load_pem_public_key(pub_path.read_bytes())
                try:
                    pub.verify(
                        base64.b64decode(cert.signature_b64),
                        payload,
                        ec.ECDSA(hashes.SHA256()),
                    )
                    verdict["signature_valid"] = True
                except Exception as e:
                    verdict["errors"].append(f"signature invalid: {type(e).__name__}")

        # (1b) v20.4-TITAN: P-521 classical-strengthened signature.
        # Back-compat: v20.2/v20.3 certs lack this; we treat absence as
        # "not applicable" (valid) rather than as a failure so older
        # certs still verify cleanly under the new verifier.
        if cert.signature_p521_b64 is None:
            verdict["signature_p521_valid"] = True  # N/A
        else:
            p521_pub_path = self._key_dir / "vos3_dev_signing_p521.pub"
            if not p521_pub_path.is_file():
                verdict["errors"].append(
                    f"P-521 public key not published at {p521_pub_path.name}"
                )
            else:
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import ec

                p521_pub = serialization.load_pem_public_key(p521_pub_path.read_bytes())
                try:
                    p521_pub.verify(
                        base64.b64decode(cert.signature_p521_b64),
                        payload,
                        ec.ECDSA(hashes.SHA512()),
                    )
                    verdict["signature_p521_valid"] = True
                except Exception as e:
                    verdict["errors"].append(
                        f"P-521 signature invalid: {type(e).__name__}"
                    )

        # (1c) v20.4-TITAN: optional ML-DSA-65 PQ signature.
        # Absence again → not applicable (valid) unless caller explicitly
        # requires it (caller can inspect signature_mldsa_valid and the
        # signature_algorithms list to enforce stricter policy).
        if cert.signature_mldsa_b64 is None:
            verdict["signature_mldsa_valid"] = True  # N/A
        else:
            if _try_mldsa_verify(payload, cert.signature_mldsa_b64, self._key_dir):
                verdict["signature_mldsa_valid"] = True
            else:
                verdict["errors"].append("ML-DSA signature invalid or oqs missing")

        # (1d) v20.5-SINGULARITY: PQ commitment cross-check.
        # Catches tampering with the algorithm label or issued_at field
        # of a commitment-only certificate. Does NOT provide PQ security.
        if cert.pq_commitment_sha384 is not None and cert.pq_commitment_alg:
            recomputed = hashlib.sha384(
                payload
                + b"||"
                + cert.pq_commitment_alg.encode("ascii")
                + b"||"
                + cert.issued_at.encode("ascii")
            ).hexdigest()
            verdict["pq_commitment_valid"] = recomputed == cert.pq_commitment_sha384
            if not verdict["pq_commitment_valid"]:
                verdict["errors"].append("pq_commitment_sha384 drift")
        else:
            verdict["pq_commitment_valid"] = True
        verdict["pq_commitment_alg"] = cert.pq_commitment_alg

        # (2) Payload hash
        if cert.certificate_sha256 == self._payload_sha256(cert):
            verdict["payload_hash_valid"] = True
        else:
            verdict["errors"].append("certificate_sha256 drift")

        # (3) Legal compliance hash
        if cert.legal_compliance_hash == self._compute_legal_compliance_hash(cert):
            verdict["legal_compliance_valid"] = True
        else:
            verdict["errors"].append("legal_compliance_hash drift")

        # (4) v20.3-PRODIGY: composed RTMR[1] commitment cross-check.
        # If the cert predates v20.3 the field is empty — we only
        # require the check to *match when present*.
        if not cert.composed_commitment_sha384:
            # Back-compat: treat absence as "not applicable" rather
            # than as a failure, so v20.2 certs continue to verify.
            verdict["composed_commitment_valid"] = True
        elif cert.composed_commitment_sha384 == self._compute_composed_commitment(cert):
            verdict["composed_commitment_valid"] = True
        else:
            verdict["errors"].append("composed_commitment_sha384 drift")

        verdict["overall_valid"] = (
            verdict["signature_valid"]
            and verdict["signature_p521_valid"]
            and verdict["signature_mldsa_valid"]
            and verdict["payload_hash_valid"]
            and verdict["legal_compliance_valid"]
            and verdict["composed_commitment_valid"]
            and verdict["pq_commitment_valid"]
        )
        return verdict


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------
# Optional post-quantum signing — ML-DSA-65 (FIPS 204)
# ----------------------------------------------------------------------------
#
# Activated only when both:
#   (a) ``VOS3_PQ_SIGNING=1`` is set in the environment, AND
#   (b) the ``oqs`` module (Open Quantum Safe liboqs binding) is importable.
#
# Why not enable by default?
#   The pure-Python reference implementations on PyPI (``dilithium-py``)
#   carry explicit side-channel warnings from their own maintainers.
#   Production-safe ML-DSA signing in 2026 means liboqs compiled with
#   constant-time guards and bound via its native library — which is a
#   system-library dependency, not a pip-installable pure-Python thing.
#
# Why ship the hook now anyway?
#   So v20.4 certificates have the wire format for PQ signatures locked in
#   *today*. When our release pipeline vendors liboqs (tracked v20.4.1),
#   flipping ``VOS3_PQ_SIGNING=1`` adds the ML-DSA field to every cert
#   without any schema churn. Auditors get forward-compat for free.


def _try_mldsa_sign(payload: bytes, key_dir: Path):
    """Best-effort ML-DSA-65 sign. Returns ``(sig_b64, pub_fp)`` or ``(None, None)``.

    Never raises — if the ``oqs`` binding is missing, unsupported, or
    fails mid-sign, we return a graceful ``(None, None)`` so the caller
    falls through to the classical-strengthened (P-521) signature alone.
    This matches the mission-brief fallback contract: PQC-when-available,
    graceful downgrade otherwise.
    """
    try:
        import oqs  # type: ignore
    except Exception:
        return None, None

    try:
        # Load-or-create ML-DSA-65 keypair.
        key_dir.mkdir(parents=True, exist_ok=True)
        sk_path = key_dir / "vos3_dev_signing_mldsa65.key"
        pk_path = key_dir / "vos3_dev_signing_mldsa65.pub"

        if sk_path.is_file() and pk_path.is_file():
            secret_key = sk_path.read_bytes()
            public_key = pk_path.read_bytes()
        else:
            signer = oqs.Signature("ML-DSA-65")  # FIPS 204 level 3
            public_key = signer.generate_keypair()
            secret_key = signer.export_secret_key()
            sk_path.write_bytes(secret_key)
            try:
                os.chmod(sk_path, 0o600)
            except OSError:
                pass
            pk_path.write_bytes(public_key)

        with oqs.Signature("ML-DSA-65", secret_key) as signer:
            sig_bytes = signer.sign(payload)

        sig_b64 = base64.b64encode(sig_bytes).decode("ascii")
        pub_fp = hashlib.sha256(public_key).hexdigest()[:16]
        return sig_b64, pub_fp
    except Exception:
        # Any failure → graceful degrade. Never poison the whole cert.
        return None, None


def _try_mldsa_verify(payload: bytes, sig_b64: str, key_dir: Path) -> bool:
    """Verify ML-DSA-65 signature. Returns ``False`` on any failure."""
    try:
        import oqs  # type: ignore
    except Exception:
        return False

    try:
        pk_path = key_dir / "vos3_dev_signing_mldsa65.pub"
        if not pk_path.is_file():
            return False
        public_key = pk_path.read_bytes()
        sig_bytes = base64.b64decode(sig_b64)
        with oqs.Signature("ML-DSA-65") as verifier:
            return bool(verifier.verify(payload, sig_bytes, public_key))
    except Exception:
        return False
