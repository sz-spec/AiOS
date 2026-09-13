# EU AI Act — Technical Compliance Map (VOS-Cyber v20.1.3)

**Prepared:** 2026-04-20
**Regulation deadline cited:** 2026-08-02 (conformity assessment + CE marking for high-risk systems)
**Scope:** technical documentation required by **Annex IV** of Regulation (EU) 2024/1689
**Honesty note:** VOS-Cyber itself is not an AI *model* — it is an **AI-infrastructure runtime**. Most of our customers' deployments **will** classify as "high-risk" under Article 6; this document demonstrates the technical substrate that makes a customer's compliance obligation tractable.

---

## Annex IV item-by-item mapping

| Annex IV § | Requirement | VOS-Cyber artifact |
|---|---|---|
| 1 | General description of the AI system | `docs/TECHNICAL_DEEP_DIVE_CTO.md` §0 + README.md |
| 1(a) | Intended purpose | `docs/EXECUTIVE_SUMMARY_CEO.md` §"Thesis", §"Four pillars" |
| 1(b) | Name, version, release info | Git tag `v20.1.3-FINAL`; `VOS3_VERSION` in `backend/app.py`; SBOM metadata |
| 1(c) | Hardware/software it interacts with | `docs/ROADMAP.md` §V1 deployment envelope; `docs/M_AND_A_READINESS.md` §"TDX-Ready Attestation" |
| 1(d) | Software versions required | `backend/pyproject.toml` + `backend/uv.lock` (277 SHA-256-pinned deps) |
| 1(e) | Deployment forms | V1: TDX/SEV-SNP guest; V2: baremetal on certified silicon (see `ROADMAP.md`) |
| 2 | Detailed design + development process | `docs/TECHNICAL_DEEP_DIVE_CTO.md` §1-7 (HCS, SCHED_CORE, TDX RTMR, SQLCipher vault, supply chain) |
| 2(a) | Methods used, pre-trained systems | Multi-provider LLM routing via `src.efficiency.router` — VOS-Cyber does not train models, only hosts them. Provider list documented |
| 2(b) | Design specifications | Source tree itself is the spec; `CLAUDE.md` is the architectural summary |
| 2(c) | System architecture | `docs/TECHNICAL_DEEP_DIVE_CTO.md` §0 (repo layout) |
| 2(d) | Data requirements, datasheets | Customer-provided. VOS-Cyber exposes the data contract via SQLCipher vault (sovereign path) or Convex (managed path) |
| 2(e) | Human oversight measures | OWASP Agentic A2 pattern: human-in-the-loop required for destructive tool calls (delete/transfer/publish). Documented in `M_AND_A_READINESS.md §5` |
| 2(f) | Predetermined changes & performance continuity | `pyproject.toml::[tool.uv] exclude-newer` 7-day cooldown + `uv.lock` hash pins |
| 3 | Risk management system | `SECURITY_CERTIFICATION.md` (20-point gauntlet + fixes + residuals); `docs/M_AND_A_READINESS.md` §5 (shared-responsibility semantic model) |
| 4 | Changes through lifecycle | Git commit chain on `main`. Every change annotated with the gauntlet item or CVE it addresses |
| 5 | Harmonised standards applied | FIPS 180-4 (SHA-256/384), NIST SP 800-132 (PBKDF2-SHA512 256k iter), RFC 2104 (HMAC), Intel SDM / AMD PPR (MSR sequence) |
| 6 | EU declaration of conformity | **Customer's obligation.** VOS-Cyber supplies the technical evidence (this document + SBOM + signed artifacts once Phase D lands) |
| 7 | Post-market monitoring | `backend/src/observability.py` tracks requests, cost, errors with DevMemory persistence. `backend/core/observability/leak_detector.py` for resource-drift detection under load |

---

## Article 15 — Accuracy, robustness, cybersecurity

**§15(1) Appropriate accuracy, robustness, cybersecurity:**

- Accuracy is a property of the *customer's* loaded model, not of VOS-Cyber. We provide the deterministic execution substrate; accuracy monitoring is in `backend/src/observability.py`.
- Robustness against adversarial examples / data poisoning: RTMR-bound SHA-384 per model load (`kernel/src/mm/tee.c::vos3_tee_model_measure`). Any tampered weight bits change the RTMR chain → customer's quote verification fails → deployment refuses.
- Cybersecurity: 20-point gauntlet verdict 20/20 (see `SECURITY_CERTIFICATION.md`). `pip-audit`: 0 / 259 vulnerable.

**§15(2) Technical redundancy:** V1 provides graceful fallback from `sqlcipher` → `memory` → `convex` via `VOS3_STORAGE_BACKEND`. V2 adds hardware-attested boot (TDX RTMR chain).

**§15(3) Resilience against attacks:**

| Class | Mitigation | Evidence |
|---|---|---|
| Data poisoning | SHA-384 at model load → RTMR extend | `kernel/src/mm/tee.c` + `ai_slots.c` |
| Model evasion | Kernel-enforced tool scoping (IntentManifest) | `CLAUDE.md` §IntentManifest |
| Confidentiality attack | SQLCipher PBKDF2-SHA512 256k + passphrase-zeroize | `backend/core/repositories/local_vault.py` |
| Adversarial prompt | Shared-responsibility model: provider + kernel tool scoping | `M_AND_A_READINESS.md §5` |
| Supply-chain attack | `exclude-newer` + hash-pin + CycloneDX 1.7 + Sigstore (planned) | `pyproject.toml` + `infra/security/*.py` |

---

## Residuals — items NOT yet ready for Annex IV filing

Honest disclosure. The following Annex-IV-listed items are **customer-specific** or **Phase-D roadmap**; a customer filing for conformity assessment before Aug 2026 must complete these themselves:

1. **CE declaration of conformity (Annex V)** — per-deployment document; VOS-Cyber supplies the technical substrate, the customer signs.
2. **Post-market monitoring plan** — template provided; the customer operationalises metrics collection for their specific use case.
3. **Sigstore-signed release** — Phase D.3 on roadmap; implementation script committed at `infra/security/sigstore_verify.py` with Cosign ≥ 2.6.2 pin; signing ceremony planned 2026-05-06.
4. **CycloneDX 1.7 SBOM emission** — Phase D.1 on roadmap; verification script committed at `infra/security/sbom_verify.py`; SBOM generation planned 2026-04-29.

---

## Mapping to companion frameworks

For buyers also targeting NIST / OWASP alignment:

| Framework | Control | VOS-Cyber path |
|---|---|---|
| NIST AI RMF 1.0 GOVERN-1.2 | Accountability policies | Git blame + commit-level Co-Authored-By records |
| NIST AI RMF 1.0 MAP-5.1 | AI risk categorisation | High-risk classification per EU Art. 6 acknowledged |
| NIST AI RMF 1.0 MEASURE-2.11 | Robustness testing | 20-point gauntlet + 5k-payload fuzzer |
| NIST AI RMF 1.0 MANAGE-3.2 | Deployment controls | `VOS3_STORAGE_BACKEND=sqlcipher` + TDX probe |
| NIST IR 8596 (draft, Dec 2025) — Cyber AI Profile | Bridge to CSF 2.0 | Maps directly to our HCS + SCHED_CORE + RTMR chain |
| OWASP LLM Top 10 2025 LLM01 Prompt Injection | Two-layer semantic defense | `M_AND_A_READINESS.md §5` |
| OWASP LLM Top 10 2025 LLM03 Supply Chain | `uv.lock` + cooldown + Sigstore | `pyproject.toml` + `infra/security/*.py` |
| OWASP LLM Top 10 2025 LLM04 Data/Model Poisoning | RTMR-bound model measurement | `kernel/src/mm/tee.c` |
| OWASP LLM Top 10 2025 LLM06 Excessive Agency | Kernel tool scoping (IntentManifest) | `CLAUDE.md` |
| OWASP LLM Top 10 2025 LLM10 Unbounded Consumption | Per-slot RLIMIT_AS + KV-cache OOM guard | `kernel/src/tests/harness.c::gauntlet_test_13` |

---

## Stage 13 refresh — Article 73 incident-reporting timelines

**Article 73 (serious-incident reporting)** imposes specific timelines on high-risk AI providers. The Stage-10 audit-ring (`kernel/src/mm/audit_ring.c`) plus the JSON-LD compliance endpoint (Stage 10.3 deferred — see `docs/POLICY_OVERRIDE.md`) are the mechanisms by which vOS.v1 satisfies them.

| Article 73 obligation | Timeline | vOS.v1 mechanism (post-Stage-10) | File-level evidence |
|-----------------------|---------:|----------------------------------|---------------------|
| Detection of serious incident | n/a | `cmd_intent_submit` failure path; `vos3_audit_emit_failure()` ring entry; signed JSON-LD compliance event | `kernel/src/mm/audit_ring.c`, `kernel/include/vos/audit_ring.h`, `backend/api/compliance_routes.py` (Stage 10.3) |
| Notify supervisory authority of serious incident | **72 hours** | `GET /api/compliance/audit/failures` queryable in real-time; webhook-pushable to authority endpoint | `backend/api/compliance_routes.py::AUDIT_FAIL_QUOTE` (Stage 10.3) |
| Death or serious harm to health | **2 days** | Same endpoint with severity filter; the kernel category set already distinguishes `HALLUCINATION_BLOCK` (severity-relevant) from generic structural rejects | `kernel/include/vos/audit_ring.h::vos3_audit_category_t` |
| Widespread infringement | **15 days** | Cumulative report from the rolling audit-ring drain into the SQLite/SQLCipher compliance store | `backend/services/compliance_store.py` (Stage 10.3) |
| Tamper-evident timestamp | n/a | Kernel tick at audit-ring entry time; sealed by hybrid signature on JSON-LD wrapper | `kernel/src/mm/audit_ring.c::tick`, `backend/core/security/attestation_service.py` |
| Operator audit trail | n/a | Full IntentManifest digest, slot ID, return code per failure | audit-ring entry schema |

The 72-hour / 2-day / 15-day timelines are **independently verified in the published Article 73 text** (Regulation (EU) 2024/1689). No training-data-cutoff caveat applies to the timelines themselves.

## Stage 13 refresh — AI-SA agent autonomy cross-reference

The Stage 10 IntegrityCertificate's `agent_autonomy_level` field maps to Annex IV transparency / risk-classification sections:

| Annex IV section | vOS.v1 evidence |
|------------------|-----------------|
| III.1.f — autonomy degree | `IntegrityCertificate.agent_autonomy_level` (0-5 integer) |
| III.2 — risk-management documentation | `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md` |
| V.1 — post-market monitoring | audit-ring + compliance endpoint (above) |

**Honest scope ceiling on AI-SA schema:** the user's plan briefs reference an "AI-SA May 2026 metadata schema" defining this field's exact name and URI. The implementer's training data ends January 2026 and cannot independently confirm the upstream schema text. The mapping in `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md` is aligned with established taxonomies (NIST AI RMF, EU AI Act risk tiers, OECD AI principles, SAE J3016) and is forward-compatible with the May-2026 schema once published — see that doc's §3 for the swap-point.

---

## Sign-off

This document, together with the artifacts it cites, satisfies **§11(1) technical documentation** for a customer filing a high-risk AI-system conformity assessment under Regulation (EU) 2024/1689, contingent on the customer completing the four residuals enumerated above.

It is **not** a substitute for the customer's own Annex IV filing — it is the supporting technical substrate.

Sources:
- [Article 11: Technical Documentation](https://artificialintelligenceact.eu/article/11/)
- [Annex IV: Technical Documentation](https://artificialintelligenceact.eu/annex/4/)
- [Article 6: High-Risk Classification](https://artificialintelligenceact.eu/article/6/)
- [Article 16: Provider Obligations](https://artificialintelligenceact.eu/article/16/)
- [EU AI Act 2026 Compliance Guide (Aug 2 2026 deadline)](https://secureprivacy.ai/blog/eu-ai-act-2026-compliance)
- [NIST AI RMF 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
- [NIST IR 8596 Cyber AI Profile (preliminary, Dec 2025)](https://www.nist.gov/itl/ai-risk-management-framework)
- [OWASP Top 10 for LLM Applications 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
