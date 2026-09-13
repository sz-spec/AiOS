# NIST AI 600-1 → VOS-Cyber Control Mapping

**Document:** `docs/NIST_AI_600-1_MAPPING.md`
**Date:** 2026-04-25
**Source:** NIST AI 600-1, *Artificial Intelligence Risk Management Framework: Generative AI Profile* (published July 2024).
**Scope:** map each Action category in NIST AI 600-1 to the VOS-Cyber technical controls that address it. Where a control is partial or proxied through a partner, this is stated explicitly. Where NIST AI 600-1 covers organisational risk that no runtime can satisfy alone, that is also stated.

## Honesty up front

NIST AI 600-1 is a **risk-management profile**, not a checklist of mandatory technical controls. It identifies 12 GAI-specific risk categories and recommends ~200 Actions across the four AI RMF functions (Govern, Map, Measure, Manage). Some actions are organisational ("designate a senior leader…"), some are policy ("document the inputs…"), and some are technical ("validate the integrity of the deployed model"). Only the technical and the policy-evidence actions can be addressed by VOS-Cyber's runtime. We do not claim NIST AI 600-1 *compliance* — that is an organisational outcome the customer's program achieves with VOS-Cyber as a substrate. We claim **substrate-level coverage** for the action categories below.

## Mapping table (technical and policy-evidence actions)

| NIST AI 600-1 Action category | VOS-Cyber control | Evidence (file / dashboard row) |
|---|---|---|
| GV-1.3-001 (governance roles for GAI) | N/A — organisational. | — |
| GV-3.2-002 (org policy for AI risk) | N/A — organisational. | — |
| **GV-1.5-001 (verify inputs/outputs to AI systems)** | IntegrityCertificate binds the exact model + IntentManifest + agent policy at every session finalize | `attestation_service.py::generate_certificate`; OMEGA C1–C16 |
| **MP-2.3-002 (verify the integrity of the deployed model)** | RTMR[1] hardware-rooted SHA-384 of model weights; composed-commitment `C = SHA-384(h_M ‖ h_I ‖ h_P)` extended once per slot activation | `kernel/src/mm/tee.c::vos3_tee_slot_activate_bound`; OMEGA K5 |
| MP-2.3-005 (validate provenance of training data) | Out of runtime scope — provenance is upstream of VOS-Cyber. | — |
| **MP-3.4-001 (track and validate the deployed model lineage)** | model_sha384 + intent_manifest_sha384 + agent_policy_sha384 are all carried on every signed cert; RTMR chain is hardware-rooted | OMEGA K5; `AUDIT_IMMUNE_SPEC.md §"Mapping to EU AI Act Annex IV"` (Annex IV §2(a) and §2(b) overlap with this NIST action) |
| **MP-4.1-002 (document the limitations and capabilities of the AI)** | IntentManifest declares authorised models, authorised tools, authorised roles; the in-kernel validator in v20.5 enforces structural caps | `kernel/src/mm/intent_validator.c`; OMEGA K11 |
| **MS-1.1-001 (test for security vulnerabilities)** | Z3 formal proofs over SCHED_CORE / egress / OOM guard; security test suite `pytest tests/security/` (59/59 PASS as of v20.5) | `backend/tests/benchmarks/*z3*.py`; `tests/security/test_killer_feature_attestation.py`; OMEGA K3, K8, K9 |
| **MS-2.5-002 (continuously verify integrity of deployed AI)** | Auto-attestation at every session finalize; auditor verify endpoint returns verdict in <1 ms | `multi_agent.py::_emit_session_attestation`; `POST /compliance/verify`; OMEGA C4, C10 |
| **MS-2.7-001 (logging of AI events)** | Audit-log binding via `attestation.signed` row carries cert URN + tenant + session + legal_hash; cert vault persists every signed cert | `core/security/cert_vault.py::bind_to_audit_log`; OMEGA C5, C6 |
| **MS-2.10-002 (post-deployment monitoring of model behaviour)** | Bulk export endpoint streams every cert in a date range with hash-of-hashes manifest; hard tenant-scoped IDOR gate | `GET /compliance/export/bulk`; `POST /compliance/verify`; OMEGA C9, C10 |
| **MS-3.3-001 (validate trust boundaries between AI components)** | SCHED_CORE-cookie isolation enforces no cross-trust-domain SMT co-execution; tenant-partitioned KV prefix cache refuses cross-tenant matches | `kernel/src/sched/core_cookie.c`; `backend/ai/llm/kv_prefix_cache.py`; OMEGA K3, C12 |
| **MG-1.3-002 (record decisions about AI controls)** | Z3 invariants are part of the cert's `policy_invariants` field (proof file paths included); auditor sees the proven invariant set on every cert | `attestation_service.py::_POLICY_INVARIANTS`; `_compute_legal_compliance_hash` |
| **MG-2.4-002 (provide redress / recovery)** | v20.5.1 rotation manager: simulated TCB-breach drives a key rotation event with both old and new key fingerprints recorded | `core/security/rotation_manager.py`; OMEGA B20 |
| **MG-3.2-001 (interoperate with existing security infrastructure)** | External AI-SPM AI-BOM export, Runtime-AI-firewall event adapter, EDR / AIDR event relay | `core/security/connectors/{external_spm_connector,runtime_firewall_adapter,edr_event_relay}.py`; OMEGA F53, F54, F55 |
| **MG-4.2-001 (rotate cryptographic keys)** | Rotation manager generates new keypair, emits a transition cert signed by both old and new keys, marks old key revoked | `core/security/rotation_manager.py` (v20.5.1) |

## NIST AI 600-1 risk categories vs VOS-Cyber substrate

NIST AI 600-1 names 12 GAI-specific risk categories. VOS-Cyber's substrate addresses some directly, some by enabling the customer's higher-level program to address them.

| Risk category (NIST AI 600-1) | VOS-Cyber relevance |
|---|---|
| 1. CBRN information | Out of substrate scope — model-content concern, not runtime. |
| 2. Confabulation | Out of substrate scope — model-quality concern. |
| 3. Dangerous, violent, hateful content | Out of substrate scope — content-policy concern. |
| 4. Data privacy | **Substrate-level** — SQLCipher AES-256 vault, tenant-partitioned KV cache, hard IDOR gate. |
| 5. Environmental | Out of substrate scope — energy-accounting concern. |
| 6. Harmful bias / homogenisation | Out of substrate scope — model-training concern. |
| 7. Human-AI configuration | Partial — IntentManifest captures the authorised configuration; runtime enforces it. |
| 8. Information integrity | **Substrate-level** — RTMR-rooted attestation binds the exact model + policy at every session. |
| 9. Information security | **Substrate-level** — kernel hardening (SMEP/SMAP/W^X/SCHED_CORE), Z3-proven egress policy, in-kernel intent validator. |
| 10. Intellectual property | Partial — model-weight binding gives provenance; copyright-cleanliness is upstream. |
| 11. Obscene / degrading content | Out of substrate scope. |
| 12. Value chain / component integration | **Substrate-level** — three industry-segment connectors (External AI-SPM / Runtime AI-Firewall / EDR), bulk-export endpoint, signed AI-BOM. |

**Substrate-level coverage: 4 / 12 risk categories**, plus **partial coverage on 2** (human-AI configuration, IP). The 6 categories not covered are content-policy concerns that no runtime substrate can address — they are the customer's content-policy program's responsibility. NIST AI 600-1 itself states this allocation explicitly.

## What this mapping does NOT claim

- VOS-Cyber does **not** make a customer NIST AI 600-1 compliant. NIST AI 600-1 compliance is an organisational outcome.
- The mapping above identifies actions where the VOS-Cyber substrate does the technical work. Customers still need a governance program, a content-policy program, and an incident-response program.
- We do **not** claim alignment with every NIST AI 600-1 sub-action — only the ones above where the substrate genuinely contributes evidence.

## Reproduction

Every "evidence" cell points to a file path in this repo or a row in the OMEGA Dashboard. A diligence reviewer can:

```bash
git clone <vos-cyber>
cd backend && .venv/bin/python -m pytest tests/security/  # 59/59 PASS
.venv/bin/python tests/benchmarks/sched_core_z3_proof.py    # UNSAT
.venv/bin/python tests/benchmarks/egress_policy_z3_proof.py # UNSAT
.venv/bin/python tests/benchmarks/ai_oom_z3_proof.py        # UNSAT
```

## Sources

- [NIST AI 600-1 — Generative AI Profile (July 2024)](https://nvlpubs.nist.gov/nistpubs/ai/nist.ai.600-1.pdf)
- [NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework)
- [VOS-Cyber `OMEGA_DASHBOARD.md`](./OMEGA_DASHBOARD.md)
- [VOS-Cyber `AUDIT_IMMUNE_SPEC.md`](./AUDIT_IMMUNE_SPEC.md)
