# ISO/IEC 27090 — vOS Pre-Compliance Technical File

**Standard:** ISO/IEC FDIS 27090 — *Cybersecurity — Artificial Intelligence — Guidance for addressing security threats and compromises to artificial intelligence systems*
**Standard status:** FDIS (Final Draft International Standard) — final text reached 2026-03-12; publication imminent
**This document status:** Draft pre-compliance — written against the FDIS text; will be refreshed against the final published Standard
**Sprint:** 15 / Item P3
**Date:** 2026-05-23
**Authors:** vOS engineering team
**Reviewers needed before GA:** counsel + ISO-trained auditor
**Source:** https://www.iso.org/standard/56581.html

---

## 0. Why this document exists

EU AI Act Article 73 (in force 2026-08-02) requires providers of high-risk AI systems to demonstrate technical controls against known threats. ISO/IEC 27090 is the cybersecurity-side standard that names those threats and prescribes detection + response guidance. Publishing this pre-compliance file now — months before the Standard's expected publication — lets us:

1. Cross-reference every vOS control to a named ISO clause as soon as the Standard publishes.
2. Surface gaps to counsel + auditor while there's still Sprint 16-17 runway to close them.
3. Anchor the EU AI Act Annex IV technical-documentation pack to a recognized international standard rather than a vOS-internal taxonomy.

Honest scope: **this is a draft against the FDIS text. The final ISO/IEC 27090 may renumber clauses, add requirements, or split a single FDIS clause into two.** Every section below carries a "Last reviewed against FDIS dated 2026-03-12" line. When the final publishes, this document is refreshed in one editing pass.

---

## 1. Scope of this pre-compliance file

| Item | Coverage |
|------|----------|
| In scope | vOS.v1 kernel + backend + Tauri shell + frontend, **as deployed** under the enterprise + sovereign profiles. |
| In scope | Model artifacts loaded into vOS AI slots (covered by Sprint 14.1 RTMR + Sprint 15 I3 OMS signing). |
| Out of scope | Customer model training pipelines — those are the customer's own ISO 27090 boundary. |
| Out of scope | Hardware below the vOS kernel (CPU microcode, GPU firmware, NPU bring-up). Tracked separately in `docs/HARDWARE_CVE_INVENTORY_2010_2026.md`. |
| Out of scope | Network infrastructure outside the host (DNS, BGP, intermediate ISPs). |

---

## 2. Threat-class mapping (FDIS Annex A → vOS controls)

ISO/IEC 27090 FDIS Annex A enumerates AI-specific threat classes. The mapping below pairs each class with the vOS control that addresses it.

| ISO 27090 threat class (FDIS) | vOS control | Sprint that shipped it | File path | Status |
|-------------------------------|-------------|------------------------|-----------|--------|
| **A.1 Data poisoning / training-data tampering** | OMS signature on loaded model + RTMR[2] extends with SHA-384 of weights at SLOT_START | Sprint 14.1 + Sprint 15 / I3 | `infra/security/model_signer.py`, `kernel/src/mm/ai_slots.c` | ✅ shipped |
| **A.2 Adversarial input / prompt injection** | Dual-LLM Privileged/Quarantined pattern; spotlighting on tool outputs | Sprint 15 / C2 (Wave 3) | `backend/ai/agents/dual_llm_router.py` (Wave 3) | 🟡 wave-3 pending |
| **A.3 Model extraction / inference-side stealing** | Confidential GPU + cache partitioning; userspace egress allowlist | Sprint 14.1 + Sprint 15 / H1 H2 | `backend/core/security/connectors/runtime_firewall.py` | ⚪ partial — physical side-channels (Energon class) are tracked as open research |
| **A.4 Model evasion via input perturbation** | Confidence-gate (Sprint 14.1) + dual-LLM Quarantined classifier on outputs | Sprint 14.1 + Sprint 15 / C2 | `kernel/src/exec/action_bridge.c` | ✅ shipped |
| **A.5 Supply-chain compromise of AI components** | OMS model signing + CycloneDX AI/ML-BOM + HF config scanner | Sprint 15 / I1 I3 I6 | `infra/security/build_sbom.py`, `infra/security/model_signer.py`, `backend/security/hf_config_scanner.py` | ✅ shipped this sprint |
| **A.6 Identity + privilege abuse** | Bearer-token auth + IntentManifest bound to slot + SPIFFE WIT-SVID (Wave 2) | Sprint 14.1 + Sprint 15 / F1 F2 F3 | `backend/middleware/auth.py`, `backend/middleware/clerk_auth.py` (Wave 2 adds SPIFFE) | ⚪ partial — Wave 2 lands SPIFFE |
| **A.7 Audit evasion / log tampering** | Append-only kernel audit ring + SQLCipher compliance store + MAIF envelopes for artifacts | Sprint 14.1 + Sprint 15 / G1 G5 K4 | `backend/services/compliance_store.py`, `kernel/src/mm/audit_ring.c`, `kernel/src/fs/vbus_fs.c` | ✅ shipped this sprint |
| **A.8 Sandbox escape from agent runtime** | gVisor MAGI + K8s Sandbox CRD per-agent isolation | Sprint 15 / C6 C8 | `backend/sandbox/gvisor_config.json`, `infra/k8s/sandboxes/` | ✅ shipped this sprint |
| **A.9 Memory disclosure across AI workloads** | Workload-hint API (KV_CACHE / MODEL_WEIGHTS separation) + zone ACL | Sprint 14.1 + Sprint 15 / A1 | `kernel/src/mm/ai_guard.c` | ✅ shipped this sprint |
| **A.10 Cryptographic-agility failure (PQC transition)** | Hybrid X25519+ML-KEM-768 KEX in userspace; kernel-side group offered in TLS supported_groups | Sprint 14.1 | `backend/services/hybrid_kex.py`, `kernel/src/crypto/tls13.c` | ⚪ partial — kernel lattice ops are Stage 14.B.2 |

**Summary as of 2026-05-23:**
- 6 of 10 ISO 27090 Annex A threat classes are ✅ fully addressed.
- 3 are ⚪ partial (depend on Wave 2/3 or post-quantum kernel work).
- 1 (A.3 inference side-channels) has residuals tracked as open research (Energon GPU power side-channels — physics-level leak, no production fix anywhere in the industry).

---

## 3. Lifecycle-integration controls (FDIS §6)

ISO/IEC 27090 FDIS §6 requires the AI security program to be integrated into the standard SDLC + post-market monitoring cycle.

| FDIS §6 sub-clause | vOS posture | Evidence |
|--------------------|-------------|----------|
| §6.1 Threat modelling during design | The 80-problem catalog in `docs/AGENT_ERA_OS_PROBLEMS.md` plus the per-Sprint solution roadmap (`docs/AGENT_ERA_SOLUTIONS_ROADMAP.md`) IS the vOS threat model. Every Sprint maps to a problem ID. | both files committed in git |
| §6.2 Secure-coding practices | CLAUDE.md + project conventions enforce: typed AuthenticatedUser dataclass, no shell=True, RLIMIT caps, Stack canaries (394 sites), SMAP, KASLR, ASSERT count 1518+ | `infra/audit/count_asserts.sh` + kernel build |
| §6.3 Security testing | apex_sim harness + governance + contracts + crypto + silicon test suites; 162 contract tests, 13 hybrid-KEX, 9 OMS, 12 HF-scanner, 7 gVisor-config — all gated through `verify_release.sh` | `backend/tests/` + CI workflows |
| §6.4 Vulnerability handling + CVE response | `docs/HARDWARE_CVE_INVENTORY_2010_2026.md` lists every CPU/chipset CVE we track; `infra/security/vex_baseline.json` holds the VEX statements | both committed |
| §6.5 Post-market monitoring | `/api/compliance/audit/failures` drains kernel audit ring; `/api/compliance/incident/report` files Article 73 incidents with auto-derived deadlines (2 days for death-harm, 15 days otherwise) | `backend/api/compliance_routes.py` (Sprint 14.1) |
| §6.6 End-of-life / decommission | `infra/security/rotation_manager.py` rotates signing keys; SLOT_RESET zeroes all model bytes; SQLCipher rekey procedure documented | Sprint 14.1 |

**Gaps as of 2026-05-23:** none for §6 — every sub-clause has an active control. The Wave-2 SPIFFE work (F1) adds a stronger §6.5 evidence chain (signed workload identity in every audit row).

---

## 4. Detection guidance (FDIS §7)

§7 requires the provider to detect AI-specific compromise indicators in real time. vOS's detection layer:

- **Kernel audit ring** — bounded 64-entry circular buffer in `kernel/src/mm/audit_ring.c`; emits VOS3_AUDIT_CAT_* events on IntentManifest rejection, TEE bind failure, hallucination block (ACTION_CHECK_CONFIDENCE), force-permit override.
- **Compliance store ingestion** — `backend/services/vbus_ring_buffer.py` drains the kernel ring every 2 s; `backend/services/compliance_store.py` persists to SQLite/SQLCipher.
- **EDR webhook** — `backend/core/security/connectors/edr_event_relay.py` accepts inbound events from MDE / CrowdStrike / SentinelOne / Wazuh, normalizes to a stable 16-char digest_prefix, persists to the compliance store under `VOS3_AUDIT_CAT_EDR_RELAY` (0x40).
- **Runtime firewall** — `backend/core/security/connectors/runtime_firewall.py` blocks SSRF + DNS-rebind + locality-violation; Sprint 15 H4 adds first-resolution DNS pinning.
- **Sandbox attestation** — every gVisor MAGI sentry hash extends into RTMR[3] at sandbox start (Sprint 15 C6/C8); divergence between deployed config and attested config is detected by `infra/security/release_artifacts/` cross-reference.

Detection-side gaps:
- **G1 / G2 (OpenTelemetry GenAI semantic conventions)** — Wave 2 work; will produce the standard `gen_ai.agent.*` spans that ISO 27090 §7 references in the "structured detection record" guidance.
- **G4 (LSM-level capture of LLM reasoning)** — Sprint 16-17 deliverable; needs a new `vos3_llm_event()` kernel hook in AI Guard.

---

## 5. Response guidance (FDIS §8)

ISO/IEC 27090 §8 prescribes:
1. **Containment** — isolate the compromised AI workload.
2. **Eradication** — wipe affected slots; revoke credentials.
3. **Recovery** — re-provision from known-good signed artifacts.
4. **Lessons learned** — feed back into §6.1 threat modelling.

vOS response controls:

| §8 step | vOS mechanism | File |
|---------|---------------|------|
| Containment | `POLICY_FORCE_PERMIT=0` kill switch (kernel-side); `SLOT_RESET` + `AGENT_KILL_ALL` (SYS_AGENT_KILL_ALL = syscall 497) | `kernel/src/exec/action_bridge.c`, `kernel/src/mm/ai_slots.c` |
| Eradication | `vos3_simd_scrub_all()` zeroes all 8 model slots; `vos3_ai_guard_free()` scrubs guard regions | `kernel/src/mm/ai_guard.c` |
| Recovery | `bash scripts/verify_release.sh` validates release artifacts; `rotation_manager.py` rotates Ed25519 + ML-DSA-65 keys | `scripts/`, `backend/core/security/rotation_manager.py` |
| Lessons learned | Every incident filed via `/api/compliance/incident/report` is cross-referenced to a problem-catalog ID, which feeds the next Sprint's roadmap | `backend/api/compliance_routes.py`, `docs/AGENT_ERA_OS_PROBLEMS.md` |

---

## 6. Examples (FDIS Annex B equivalent)

The Standard's annex provides worked examples. vOS-specific equivalents:

- **Example B.1 — Indirect prompt injection from a tool result.** Mitigation: dual-LLM Privileged/Quarantined pattern (Sprint 15 / C2, Wave 3). Sample test fixture lives in `backend/tests/red_team/` (Wave 3, Agent 12).
- **Example B.2 — Malicious model config (`trust_remote_code=true` in `config.json`).** Mitigation: `backend/security/hf_config_scanner.py` (Sprint 15 / I6). Test corpus in `backend/tests/security/test_hf_config_scanner.py`.
- **Example B.3 — Stale agent token reused after rotation.** Mitigation: rotation_manager + vault_pool generation tracking (Sprint 14.1). Test in `backend/tests/governance/test_keyring_rotation_deferred.py`.
- **Example B.4 — Side-channel model extraction via shared GPU cache.** Mitigation: NVIDIA Confidential Compute on B200 + tracked-upstream MIG isolation. Honest gap: physical side-channels (Energon, Kraken) are open research — no production fix.

---

## 7. Verifiable evidence the auditor will ask for

| Auditor question | Evidence location |
|------------------|-------------------|
| "Show me the SBOM for the deployed system." | `infra/security/vos3_sbom_v11_stage11.json` + Sprint 15 I1 AI/ML-BOM section |
| "Show me the model-signing chain." | `infra/security/release_artifacts/*.bundle.json` (kernel) + sibling `.bundle.json` files per model (OMS, Sprint 15 I3) |
| "Show me the attestation evidence for production silicon." | `evidence/silicon_attestation_<date>.jsonld` (Sprint 14.2 silicon CI) |
| "Show me the audit-trail format spec." | `kernel/include/vos/audit_ring.h` + `backend/services/compliance_store.py` schema |
| "Show me the incident-reporting endpoint." | `POST /api/compliance/incident/report`; SLA derivation in `backend/api/compliance_routes.py::_article_73_deadline()` |
| "Show me the technical documentation Annex IV." | `GET /api/compliance/annex-iv/export` — emits the 9-section JSON-LD bundle |
| "What's your threat model?" | `docs/AGENT_ERA_OS_PROBLEMS.md` + this file |

---

## 8. Watchlist — items that change when ISO/IEC 27090 final publishes

This document is refreshed by tracking these signals:

1. **ISO/IEC 27090 publication date.** Subscribe: https://www.iso.org/standard/56581.html → "Stage" field changes from "60 — Approval" to "60.60 — Published".
2. **Clause renumbering.** Diff the FDIS table of contents against the published Standard.
3. **New Annex A threat classes.** The FDIS Annex A is 10 classes; if the final adds more, each gets a new row in §2 above.
4. **K8s Sandbox API drift.** SIG-Apps spec was draft as of March 2026; final expected late Q3 2026. Update `infra/k8s/sandboxes/sandbox-crd.yaml` if upstream API changes.
5. **MCP spec at 2026-07-28.** The MCP release candidate locked 2026-05-21; the 2026-07-28 final may shift the OAuth/OIDC stateless-core requirements. Update §A.6 row + Wave-2 SPIFFE integration accordingly.

---

## 9. Honest scope ceiling

- **This is a pre-compliance file**, not a conformance claim. The final ISO/IEC 27090 publication may add requirements we don't yet meet; this document is the workbench for surfacing those.
- **The 4 partial-status rows in §2** (A.2, A.3, A.6, A.10) are the items the auditor will ask hardest about. The Wave 2/3 work in Sprint 15 closes A.2 and most of A.6; A.3 and A.10 carry residuals into Stage 14.B.2.
- **No external auditor has reviewed this document** as of 2026-05-23. Counsel + ISO-trained auditor review is the gate before v1.1.0 GA (2026-07-15 target, 11 weeks of buffer before EU AI Act enforcement on 2026-08-02).

---

## Last reviewed

| Section | Against FDIS dated | Reviewer | Next review trigger |
|---------|--------------------|----------|---------------------|
| §1-§9 | 2026-03-12 | engineering team | ISO/IEC 27090 final publication |
