# EU AI Act — General-Purpose AI (GPAI) Technical Compliance Documentation

**Anchor:** `aeb3736` · **Phase:** P4 (CVE + microcode + adaptive sandbox) · **Document version:** v1.0 (2026-05-17)
**Regulation:** Regulation (EU) 2024/1689 (the "AI Act")
**Effective:** GPAI provisions enter into application **2026-08-02** (T−77 days as of this revision).

## 1. Scope of this document

This document is the **technical-documentation** annex required for vOS
as a General-Purpose AI system platform under the EU AI Act, Article 53
(transparency for GPAI providers) and **Annex IV §V** (technical
documentation for high-risk AI systems). It binds three layers of
evidence to the engagement anchor SHA `aeb3736`:

1. **Architectural mitigations** (Phases P1–P3): KPTI, dual-path PQC,
   universal PCI HAL, universal hardware manifest.
2. **Adaptive risk posture** (Phase P4.1–P4.3): CVE inventory,
   microcode-baseline check, adaptive sandbox rlimits driven by the
   manifest's `.mode` field.
3. **Audit & provenance** (Phases P10–P13): hash-chained audit log,
   Sigstore v3 DSSE bundles, CycloneDX SBOM with VEX, Rekor v2 Merkle
   transparency.

vOS's classification under the Act:

- It is **not** itself a high-risk AI system; it is the **runtime
  platform** on which high-risk AI applications execute.
- It **is** a GPAI provider for the foundation-model layer (Article
  53–55) by virtue of bundling Gemma 3 / Llama 4 / DeepSeek R1 / Qwen 3
  / Phi-4 weights within the certified binary.
- High-risk AI workloads deployed **on top of** vOS inherit the
  platform's documented mitigations as their state-of-the-art robustness
  argument under **Article 15(4)** ("technical robustness and security
  measures appropriate to the state of the art").

## 2. State-of-the-art technical robustness (Article 15)

The Act says a high-risk AI system must reach **"a level of accuracy,
robustness and cybersecurity that, as far as appropriate, is consistent
with the state of the art."** vOS's claim is documented as follows.

### 2.1 Speculative-execution side-channel posture

| Threat class | Mitigation in vOS | Anchor file |
|---|---|---|
| Meltdown (CVE-2017-5754) | KPTI page-table isolation (three paths: PROTECTED_FULL / PROTECTED_PCID_ONLY / LEGACY_KAISER) | `kernel/src/arch/x86_64/kpti.c` · P1.2 |
| Spectre v2 (CVE-2017-5715) | Microcode IBPB + IBRS + retpoline build flags; microcode revision verified at boot | `kernel/src/arch/x86_64/microcode_check.c` · P4.2 |
| L1TF (CVE-2018-3615) | KPTI + microcode requirement (`requires_microcode=True` in compliance table) | `backend/services/compliance_audit.py::_BASELINE` |
| MDS (CVE-2018-1212{6,7,30}) | MDS_CLEAR on context switch + microcode baseline | `compliance_audit.py` · existing tree per CLAUDE.md |
| Retbleed (CVE-2022-29900) | Microcode + IBPB on context switch | baseline-table row in `microcode_check.c` |
| Downfall (CVE-2022-40982) | Microcode baseline check at boot | `microcode_check.c` |
| Inception (CVE-2023-20569) | Microcode baseline check at boot | `microcode_check.c` |

A host below the microcode baseline is **not** refused — instead the
manifest forces `RESTRICTED_LEGACY` mode, the sandbox rlimits halve
automatically (P4.3), and the audit log records the exposure. This is
deliberate: refusing to boot would deny operator visibility on hosts
where the microcode update isn't yet available, while still preventing
GPAI workloads from running with their full resource budget on
known-vulnerable silicon.

### 2.2 Post-quantum cryptography (Article 15(4) + NIS2 alignment)

vOS implements **hybrid Ed25519 + ML-DSA-65** signatures with a
dual-path engine (P2.1):

| Path | Backend | Verify budget |
|---|---|---|
| AVX-512 | `oqs_avx512` (liboqs) | ≤ 150 µs |
| AVX2 | `oqs_avx2` (liboqs) | ≤ 300 µs |
| Scalar (no SIMD) | `oqs_scalar_bitslice` | ≤ 2 ms (P4.3 SLA) |
| Pure-Python | `dilithium-py` | ≤ 12 ms (audit-flagged slow path) |

Verification is **constant-evaluation** (non-short-circuit) per the
P2.3-polish patch, so a malformed signature cannot reveal which
component failed through timing.

### 2.3 Adaptive sandbox tier (Article 9 risk-management requirement)

Every GPAI inference is dispatched through `services.app_sandbox`, which
reads the cached `HardwareManifest` and applies adaptive rlimits:

| Manifest `.mode` | `RLIMIT_AS` | `RLIMIT_CPU` (soft, hard) | `RLIMIT_NPROC` |
|---|---|---|---|
| PROTECTED | base × 1 MB | (60, 120) | (4, 4) |
| RESTRICTED_LEGACY | base ÷ 2 × 1 MB ≥ 32 MB | (30, 60) ≥ 5s | (2, 2) |
| UNKNOWN | matches RESTRICTED_LEGACY (Security > Availability) |

Outdated microcode (P4.2) forces `RESTRICTED_LEGACY` regardless of CPU
class, automatically tightening the runtime resource budget on
known-vulnerable hosts — a Article 15(5) "post-market monitoring"
control wired into the boot path.

## 3. Annex IV §V.1 — Logging, traceability, and the audit trail

vOS retains the following evidence per high-risk inference:

| Item | Mechanism | Retention |
|---|---|---|
| Input manifest hash | `intent_manifest_builder.py` (P10.1) | hash-chained, kept until cert revoked |
| Sandbox tier at execution | logged via `services.app_sandbox._active_manifest()` snapshot | per-request log row |
| PQC verify path used | `services.pqc_sign.verify_path()` cached value | per-request log row |
| Microcode revision | `manifest.microcode_revision` (frozen at boot) | per-boot log row |
| CVE exposure count | `manifest.cve_exposure_count` (frozen at boot) | per-boot log row |
| Compliance inventory | `compliance_inventory_v1.json` (P4.1) | bound to AAA cert, regen on rebuild |
| Risk score | `manifest.risk_score` | per-boot log row |

The hash chain is anchored to a Sigstore v3 DSSE bundle (P11) and
Rekor v2 RFC-6962 transparency log; bundle ID
`release_artifacts/vos3_elf_stage11.bundle.json`.

## 4. Article 53 — Transparency obligations for GPAI

vOS as a GPAI provider publishes:

- **Model card** for each bundled model: `docs/POST_QUANTUM_INVENTORY.md`
  + per-model entries in `kernel/include/ai_kim.h`.
- **Training-data summary** (required by Article 53(1)(d)): published
  with each bundled model; the platform itself does not train but
  passes through provider disclosures unchanged. Pointer:
  `docs/POST_QUANTUM_INVENTORY.md`.
- **Copyright-respecting policy**: vOS does not exfiltrate user code
  for training; bundled models are downstream of their providers'
  copyright postures.

## 5. Article 55 — Systemic-risk threshold

vOS does not currently bundle any GPAI model whose training compute
exceeds the **10^25 FLOP** systemic-risk threshold of Article 51. The
largest bundled model is documented in `kernel/include/ai_kim.h` after
the 2026-05-11 universal-model upgrade. If a future bundled model
crosses the threshold, the additional obligations of Article 55
(adversarial testing, incident reporting to the AI Office) apply; this
document version does not need to satisfy them.

## 6. Article 73 — Serious-incident reporting

vOS audit-log entries flagged `kind="security_serious_incident"` (P10.1
fail-closed events: KPTI init failure, PQC verify-after-rotation
failure, microcode baseline violation when combined with a downstream
exploit attempt) are surfaced to the operator with an explicit
acknowledgement-required dialog. The 72-hour reporting clock under
Article 73 starts when the operator confirms the incident; the audit
log preserves the original timestamp for forensic anchoring.

Reporting destination: the **EU AI Office** Notifications portal +
the relevant national competent authority. vOS does not auto-submit;
operators retain the human-in-the-loop control required by Recital 75.

## 7. Mapping to other frameworks

| Framework | Mapping | Document |
|---|---|---|
| NIST AI RMF (1.0) | "Manage" function ↔ P4.3 adaptive sandbox; "Govern" ↔ Article 73 audit | `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md` |
| ISO/IEC 42001:2023 | AIMS clause 6 ↔ P1–P4 mitigations; clause 8.1 ↔ audit log | (cross-ref via the same audit-log mechanism) |
| SSDF (NIST SP 800-218) | PO.1.1 / PW.4.4 ↔ Sigstore v3 + Rekor v2 bundles | `release_artifacts/vos3_elf_stage11.bundle.json` |
| SOC 2 (TSC CC7.4) | Cybersecurity incident response ↔ Article 73 wiring | (audit log + Sigma rules) |
| EN 18031-2 (Radio Equipment Directive cybersecurity) | not in scope — radio equipment compliance handled by host hardware vendor |

## 8. Honest-scope ceilings

- **Hardware floor is 2010 (Westmere).** Pre-x86_64 silicon is refused.
  Westmere-era hosts ship in LEGACY_KAISER mode with documented 5–30 %
  syscall regression — this is **not** "no performance impact" as the
  original directive's marketing line suggested.
- **Microcode baseline is sampled.** The per-family table covers the
  silicon this engagement actually targets; adding a new CPU model
  means a new row in `microcode_check.c`. We do not pretend universal
  coverage.
- **The compliance audit does not execute exploit POCs.** It
  cross-references manifest claims to documented mitigation
  requirements. POC-driven validation is a separate (and out-of-scope)
  engagement.
- **Article 73 auto-reporting is not implemented.** Operators retain
  human-in-the-loop control over what gets reported to the AI Office.
- **GPAI systemic-risk obligations (Article 55) are dormant.** No
  bundled model currently crosses the 10^25 FLOP threshold.

## 9. References

- **Regulation (EU) 2024/1689** — the EU AI Act (consolidated text).
- **NIST SP 800-218** — Secure Software Development Framework (SSDF).
- **Intel SDM Vol 3A §9.11.7.1** — microcode revision read sequence.
- **FIPS 204** — ML-DSA (Module-Lattice-based Digital Signature Algorithm).
- **Linux kernel `Documentation/x86/pti.rst`** — KPTI performance characteristics.
- **vOS engagement plan** — `quirky-foraging-bachman.md` (AAA plan, anchor `aeb3736`).
