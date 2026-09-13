# EU AI Act Annex IV — OS-Attestation Interpretation (vOS)

**Item:** P4 (Sprint 22, V1.3) — *doc-only; NOT a counted moat row.*
**Saved:** 2026-06-04 (Opus 4.8)
**Catalog row:** P4 — "EU AI Act Annex IV technical-documentation
requirements don't clearly say what OS-level attestation a deployer must
provide; interpretation needed."
**Status:** **DRAFT for counsel review — NOT legal advice.** Every claim
here must be confirmed by qualified EU AI Act counsel before it is relied
on. Article 73 enforcement date: **2026-08-02**.

> **Honest scope.** This document interprets which Annex IV technical-
> documentation points vOS's *enforcing* controls can supply evidence
> for, and which they cannot. It is an engineering-to-legal translation
> aid, not a compliance certification. Cells marked ⚠️ are open questions
> for counsel.

---

## 1. Purpose

Annex IV lists the technical documentation a provider of a high-risk AI
system must maintain. Several points imply *runtime* and *platform*
guarantees (logging, robustness, cybersecurity, accuracy) but Annex IV
does not enumerate an "operating-system attestation" artifact. This
document maps Annex IV points to the concrete, fail-closed evidence vOS
can produce, so a deployer's Annex IV file can cite real enforcement
rather than prose.

## 2. Annex IV point → vOS evidence

| Annex IV point (paraphrased) | vOS evidence artifact | Enforcing module | Confidence |
|---|---|---|---|
| §2(b)/(c) — system design, architecture, computational resources | Intent manifests + GPU/CVM admission records | `intent_manifest_builder.py`, `gpu_alloc_validator.py`, `cvm_launch_gate.py` | High (records exist) |
| §2(d) — data provenance / training data governance | MAIF v2 hash-chained model provenance + SLOT_START gate decisions | `maif_v2.py` | High for *lineage*; ⚠️ does NOT attest data *cleanliness* (I4 is research-only) |
| §2(e)/(g) — accuracy, robustness, cybersecurity measures | Byte-level IFC kernel gate, capability/delegation gates, isolation gates | `taint_engine_v2.py` + `kernel/src/sec/taint_gate.c`, `agent_capability_table.py`, the GPU/CVM gates | High for the *mechanisms*; ⚠️ effectiveness metrics separate |
| §3 — monitoring / logging capabilities (Art. 12 link) | Fail-closed decision logs (every gate logs ALLOW/REFUSE with reason) + Annex IV "zero PII in logs" gate | all gates; `tools/log_pii_scan.py`, `outbound_pii_shield.py` | High |
| §5 — cybersecurity / resilience (Art. 15 link) | IOMMU/DMA, perf-counter, driver, ioctl, CVM-interrupt gates | `iommu_dma_guard.py`, `perf_counter_lockdown.py`, `gpu_driver_gate.py`, `accel_ioctl_filter.py`, `cvm_launch_gate.py` | Mixed — several `enforced-pending-silicon` |
| §2(f) — human oversight measures (Art. 14 link) | Policy-override / REVIEW_REQUIRED fallback; cryptographic declassification evidence | `services/policy_override.py`, C-3 `DeclassEvidence`, BBS release | Medium ⚠️ — oversight *workflow* is operator-owned |

## 3. What vOS does NOT attest (be explicit for counsel)

- **Training-data cleanliness / poisoning absence** — I2 attests lineage,
  not that the data was free of sleeper-agent triggers (I4 is an open
  research problem; do not claim otherwise in an Annex IV file).
- **Hardware-bound isolation on un-piloted silicon** — O4 and the
  Partition-2 items are `enforced-pending-silicon`; their evidence is a
  software gate + a modeled stub, not on-silicon validation, until a
  customer pilot. State this honestly.
- **Model accuracy / fairness metrics** — out of scope for the OS layer
  entirely; supplied by the model provider's own Annex IV materials.

## 4. Open questions for counsel (⚠️)

1. Does Annex IV §2(d) accept a *hash-chained provenance record* as
   sufficient "data governance" evidence, or does it require dataset-
   content disclosure?
2. Is a *fail-closed enforcement gate with logged refusals* recognized as
   an Art. 15 cybersecurity measure, or must there be an external
   certification?
3. For `enforced-pending-silicon` controls, what disclosure language is
   required so the Annex IV file is not misleading?
4. Does the cryptographic declassification record (C-3 / BBS) satisfy any
   Art. 14 human-oversight documentation requirement?

## 5. References

- EU AI Act, Annex IV; Articles 12, 14, 15, 73.
- `docs/EU_AI_ACT_COMPLIANCE.md` (existing timeline + AI-SA cross-ref).
- `docs/V1.3_BACKLOG_MASTER_PLAN.md` (control inventory + honesty labels).
