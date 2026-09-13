# Sprint 17 Plan — Intelligence & Long-Term Sovereign Moat

**Date:** 2026-05-24
**Predecessor:** Sprint 16 (W1+W2+W3) — 37/80 catalog problems closed (46%)
**Window:** 2026-09-01 → 2027-06-30 (10-month horizon; deliberately wider than Sprint 16's 4-month cadence because the items here are hardware/vendor/research-bound)
**Companion documents:**
  - `docs/AGENT_ERA_OS_PROBLEMS.md` (80-problem catalog)
  - `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` (solutions classification)
  - `docs/SPRINT_16_PLAN.md` (the previous plan; this one extends its 14 medium-term → long-term boundary)
**Sources used to build this plan:** tier-1 only, **May 24, 2026 horizon** — arXiv, IETF datatracker, NIST CSRC + NCCoE, IACR ePrint, USENIX/IEEE/ACM, vendor research (Intel, NVIDIA, Anthropic, Google Project Zero).

---

## 0. Strategic context

Sprint 16 closed all 19 medium-term items. The remaining catalog splits into:

| Class | Count | Plan |
|---|---|---|
| 🟣 Long-term (Sprint 17, this plan) | 14 | hardware/vendor-bound; some need new silicon |
| 🟡 Track upstream | 18 | monitoring only — fixes exist outside vOS |
| 🔴 Open research no fix anywhere | 8 | TAM for investors; published as honest moat |
| (cross-link overlap already shipped) | 3 | implicitly covered |

After Sprint 17 ships, vOS will have **51 of 80** problems closed (64%) — a unique posture in the industry. The 8 open-research items become the published "industry-wide unsolved" list for investor + counsel + regulator briefings.

Three strategic clusters drive Sprint 17:

| Cluster | Items | Posture |
|---|---|---|
| **Hardware-dependent security** (TEE + GPU isolation) | A5, D2, D3, D6, D7, E1-E6, L2 | Requires Morello + Blackwell-class GPU + Intel TDX server access |
| **Cross-organization agent identity** (Federation) | (extends Sprint 16 F4) | Aligned with IETF WIMSE + AIMS draft work |
| **Advanced information flow control** (byte-level taint tracking) | (extends Sprint 16 C7) | Pure software; refines the high-watermark Bell-LaPadula discipline |

---

## 1. The 14 long-term items, mapped to deliverables

### Cluster A — Hardware-dependent security (10 items, gate: silicon procurement)

| ID | Title | Severity | Approach (May 2026 best-known) | Hardware dep |
|---|---|---|---|---|
| **A5** | Rowhammer / RowPress on GPU HBM + NPU SRAM | 🔴 | Pin model-weight pages to non-attacker-controlled rows + RAPL-bound refresh-rate watchdog. No production defense exists; vOS-side is a hint to the kernel mm layer that lifts refresh on `MODEL_WEIGHTS`-hinted pages (cross-link Sprint 16 A1). | Blackwell HBM3e access |
| **D2** | HECKLER malicious-interrupt attack on SEV-SNP + TDX | 🔴 | TDX module update from Intel (announced May 2026); vOS-side: detect-and-deny patch installed via attestation_service composite-policy (cross-link D1). | Intel TDX server |
| **D3** | SEV-SNP WASM code recovery via timing | 🟠 | Code-path-randomization layer at WASM runtime entry. Mitigation tracked in NDSS-26 follow-up. | AMD SEV-SNP server |
| **D6** | CVM 10-30% throughput tax | 🟡 | TDX module 1.5 (Intel, Q3 2026 ETA) reportedly cuts overhead to 5-12%. Track + re-benchmark when available. | Intel TDX server |
| **D7** | Intel quarterly CVE flow tracking | 🟠 | Automated CVE-scoring pipeline that ingests Intel Platform Security quarterly reports + fans out to attestation policy updates. Backend service. | None — automation work |
| **E1** | NVIDIA out-of-tree driver opacity | 🔴 | Kernel-side fuzzer harness against the IOCTL surface + bug-bounty incentive doc. Requires NVIDIA OSS-driver migration timeline (in progress at NVIDIA). | NVIDIA H100/B200 host |
| **E2** | CUDA/ROCm IOCTL fuzzing | 🔴 | syzkaller-style fuzzer against the CUDA IOCTL — published by Google Project Zero April 2026 (precedent). | NVIDIA/AMD GPU host |
| **E3** | GPU DMA bypass via IOMMU mis-config | 🔴 | IOMMU + ATS validation harness invoked at kernel boot; fail-closed on suspect topology. | IOMMU-capable host |
| **E4** | SIMT warp-scheduler contention | 🟠 | Per-tenant warp-isolation via NVIDIA MIG profile enforcement (cross-link Sprint 16 E7). | NVIDIA MIG-capable GPU |
| **E5** | Energon power+thermal weight extraction (arxiv 2508.01768) | 🔴 | **Two-layer defense per the paper:** (a) hardware-side — restrict GPU sensor access to admin only (NVIDIA driver patch — vendor-bound); (b) software-side — model pruning + knowledge distillation to emit obfuscated variants. vOS ships the software-side as a model-loading policy in Sprint 17. | NVIDIA driver vendor coordination for (a) |
| **E6** | Performance-counter model fingerprinting | 🟠 | Kernel-side restriction of `perf_event_open` on AI-workload PIDs via the Sprint 16 B3 LSM cap-gate. | None — software |
| **L2** | NPU power-island scheduler visibility | 🟡 | Apple ANE / Qualcomm Hexagon expose-power-state via vendor SDK; vOS wraps the SDK calls into a kernel scheduler hook. | Apple/Qualcomm hardware |

### Cluster B — Cross-org identity (1 conceptual ID, ladders on Sprint 16 F4)

Sprint 16 / F4 shipped SPIFFE Federation accept. The Sprint 17 follow-up aligns the implementation with the May-2026 IETF draft set:

| Concept | What | IETF reference |
|---|---|---|
| **AIMS conceptual model** (Agent Identity Management System) | Backend service `backend/services/aims_envelope.py` that wraps an F1/F4 SPIFFE verification result in the AIMS-format envelope per draft-klrc-aiagent-auth-01 (March 2026) | draft-klrc-aiagent-auth-01 |
| **WIMSE-applied agent identity** | Extends F4 federation verifier with the WIMSE-applicability validation rules (control mechanisms requiring user confirmation on OAuth access token issuance) | draft-ni-wimse-ai-agent-identity-02 (Feb 2026) |
| **Federated workload identity practices** | Lift Sprint 16 F4 to support the April-2026 federation-based credential exchange pattern | draft-ietf-wimse-workload-identity-practices-04 |
| **Agentic JWT (Secure Intent Protocol)** | Add Secure Intent Protocol envelope around the existing Sprint 15 / F2 MCP OAuth bridge | draft-goswami-agentic-jwt-00 |

### Cluster C — Advanced IFC (byte-level taint tracking, ladders on Sprint 16 C7)

Sprint 16 / C7 ships per-blob TaintLabel (PUBLIC / UNTRUSTED / SECRET / TOXIC) with high-watermark propagation. Sprint 17 refinement:

| Concept | Why | Status |
|---|---|---|
| **Byte-level taint** | High-watermark over a whole blob falsely contaminates clean fields with a single UNTRUSTED byte. Byte-level tracking solves the false-positive class. Per HiStar/Asbestos-era research + the May 2026 GAAP/Fides papers, this is implementable as a span-tracking layer on top of the blob model. | Pure software — extend C7 |
| **Declassification with cryptographic evidence** | The current `declassify()` records a marker; Sprint 17 binds declassification to a signed attestation (cross-link D5 NonceGate) so the audit trail is cryptographically verifiable, not just textual. | Pure software |
| **Multi-source byte coloring** | Track WHICH source contributed each byte, not just the max-label. Useful for cross-org workflows where some bytes come from a federated partner with a different trust profile (cross-link F4). | Pure software |

---

## 2. Open-research backlog (8 items — TAM for investors, no production fix anywhere)

These are NOT in Sprint 17's delivery scope. They are the published "industry-wide unsolved" list:

| ID | Title | Why no fix exists |
|---|---|---|
| **A5** (also in long-term) | Rowhammer on accelerator memory | No production-grade mitigation on any vendor |
| **B1** | POSIX permission model is 50 years too old | Architectural rebuild needed industry-wide |
| **C1** | LLMs cannot reliably distinguish instructions from data | Architectural property of transformer training |
| **C3** | EchoLeak-class zero-click data exfil | Patches break specific attacks; class remains |
| **C4** | Adaptive attacks beat all in-band defenses at >85% | Meta-analysis of 78 studies (arxiv 2604.23887) |
| **I4** | Training-data poisoning (sleeper agents) | Detectable only at training time; deployment-time defense doesn't exist |
| **O1** | Spectre/Meltdown on GPU | CPU mitigations don't extend to GPU; vendor research only |
| **O4** | Cross-tenant cache-based model stealing | L1/L2/L3 cache leakage; partitioning research-only |

vOS posture: PUBLISHED in `docs/AGENT_ERA_OS_PROBLEMS.md` with tier-1 citations. Operators deploying vOS get an honest scope ceiling for each. Counsel/investors get the "no other OS solves these either" framing.

---

## 3. Step 3 — Immediate Execution: 3 low-hanging fruit prototypes

These can be prototyped NOW on the M-series Mac dev host without buying new silicon. Each follows the Sprint 15/16 ritual (header + Python service + tests + commit).

### Prototype #1 — Energon software-side mitigation policy

**Catalog ID:** E5 (software half)
**Effort:** ~250 lines + ~20 tests
**Hardware dep:** NONE (vendor coordination is for the kernel-driver half, deferred)
**File targets:**
  - `backend/services/energon_obfuscation_policy.py` — model-loading hook that consumes a `MODEL_WEIGHTS` slot's architecture spec and emits a (pruning_recipe, distillation_recipe) pair per the arxiv 2508.01768 mitigation section.
  - `backend/tests/security/test_energon_obfuscation_policy.py` — 20 tests covering recipe-generation, per-attention-head obfuscation, deterministic seed reproducibility, refusal on unsupported architectures.
**Source:** arxiv 2508.01768 §6 "Mitigation strategies".
**Honest scope ceiling:** This is the POLICY DECISION layer. Actually applying the obfuscation to weights at load time requires a model-runtime hook (not in scope for the policy prototype). The Python twin lets backend services program against the API surface; the runtime integration happens when the GPU-host environment is provisioned.

### Prototype #2 — AIMS-formatted attestation envelope

**Catalog ID:** AIMS extension to Sprint 16 / F2 + F4
**Effort:** ~300 lines + ~25 tests
**Hardware dep:** NONE
**File targets:**
  - `backend/services/aims_envelope.py` — wraps an F1/F4 `VerifiedSPIFFEIdentity` / `VerifiedFederatedIdentity` + the F2 `MCPAuthContext` into an AIMS-format envelope per `draft-klrc-aiagent-auth-01`.
  - `backend/tests/integration/test_aims_envelope.py` — tests covering envelope shape, user-confirmation gate (`draft-ni-wimse-ai-agent-identity-02`'s OAuth token requirement), Secure Intent Protocol (SIP) optional overlay.
**Source:** `draft-klrc-aiagent-auth-01` (IETF, March 2026); `draft-ni-wimse-ai-agent-identity-02` (Feb 2026); `draft-ietf-wimse-workload-identity-practices-04` (April 2026).
**Honest scope ceiling:** Drafts are not RFCs yet. Format may rev. This prototype tracks the current draft + exposes the envelope shape so downstream services (MCP bridge, dual-LLM router) can program against it. On RFC ratification we re-emit.

### Prototype #3 — ML-KEM-768 microkernel benchmark suite

**Catalog ID:** N1 follow-up (Sprint 15 / N1 shipped userspace hybrid KEX; Sprint 17 measures it)
**Effort:** ~200 lines + measurement harness + ~10 tests
**Hardware dep:** NONE (runs on Mac; produces a baseline number)
**File targets:**
  - `infra/benchmarks/pq_tls_bench.py` — measures handshake + decap latency on the existing Sprint 15 ML-KEM-768 hybrid TLS path. Compares against pure X25519. Reports P50/P95/P99.
  - `infra/benchmarks/results/pq_tls_baseline_2026-05-24.json` — baseline numbers committed to repo so future regressions are caught.
  - `backend/tests/benchmarks/test_pq_tls_bench_smoke.py` — non-performance test confirming the benchmark harness runs end-to-end without crashing.
**Source:** `draft-ietf-tls-mlkem-07` (current at May 2026); arxiv 2404.13544 "Faster Post-Quantum TLS 1.3 Based on ML-KEM"; IACR ePrint 2026/959 "Operationalising Post-Quantum TLS".
**Honest scope ceiling:** Microkernel-specific PQ-TLS perf data is NOT in the public literature (the WebSearch found "no specific microkernel-perf data" — that's the gap vOS can fill). This benchmark establishes the baseline; comparison against AVX-512 (1.64× speedup reported) + batch keygen (3.5-4.9× speedup) is a Sprint 18 follow-up requiring an AVX-512 host.

---

## 4. Timeline + go/no-go gates

| Window | Items | Gate |
|---|---|---|
| 2026-06 → 2026-08 (now → enforcement) | 3 low-hanging prototypes (this plan §3) | Code lands; tests pass; documentation published |
| 2026-09 → 2026-12 | Cluster B (Identity Federation alignment with IETF drafts) + Cluster C (byte-level IFC) | IETF draft ratification timeline → docs/SPRINT_17_PLAN.md updated; vOS impl re-emits envelope on RFC publication |
| 2027-01 → 2027-06 | Cluster A (hardware-dependent) | Procurement complete: Morello board, Blackwell host, Intel TDX server. Each item delivered as a per-host integration |

### Procurement gate (Cluster A blocker)
- **Morello board** — ARM Morello dev kit; ~$5-10k; for A3 (Sprint 16) hardware CHERI validation + E2 IOCTL fuzzer
- **Blackwell-class GPU host** — NVIDIA H200 or B200; for E1-E6 GPU-side work
- **Intel TDX-capable server** — Xeon 4th-gen or newer with TDX enabled; for D2, D6, D7

CEO sign-off required.

---

## 5. Sources (May 24, 2026 horizon)

### IETF drafts
- [draft-klrc-aiagent-auth-01](https://datatracker.ietf.org/doc/draft-klrc-aiagent-auth/) — AI Agent Authentication and Authorization, March 2026
- [draft-ni-wimse-ai-agent-identity-02](https://datatracker.ietf.org/doc/draft-ni-wimse-ai-agent-identity/) — WIMSE Applicability for AI Agents, Feb 2026
- [draft-ietf-wimse-arch-07](https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/) — WIMSE Architecture
- [draft-ietf-wimse-workload-identity-practices-04](https://datatracker.ietf.org/doc/draft-ietf-wimse-workload-identity-practices/) — Workload Identity Practices, April 2026
- [draft-ietf-wimse-workload-identity-bcp-02](https://datatracker.ietf.org/doc/draft-ietf-wimse-workload-identity-bcp/) — OAuth 2.0 Client Assertion in Workload Environments
- [draft-goswami-agentic-jwt-00](https://datatracker.ietf.org/doc/draft-goswami-agentic-jwt/) — Secure Intent Protocol: JWT Compatible Agentic Identity
- [draft-ietf-tls-mlkem-07](https://datatracker.ietf.org/doc/draft-ietf-tls-mlkem/) — ML-KEM Post-Quantum Key Agreement for TLS 1.3

### Government / Standards
- [NIST AI Agent Standards Initiative (CAISI, Feb 2026)](https://www.nist.gov/caisi/ai-agent-standards-initiative)
- [NIST NCCoE: Accelerating Adoption of Software and AI Agent Identity and Authorization](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf)
- [NIST AI 100-5 — Plan for Global Engagement on AI Standards](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-5.pdf)
- [Draft NIST Guidelines: Rethink Cybersecurity for the AI Era](https://www.nist.gov/news-events/news/2025/12/draft-nist-guidelines-rethink-cybersecurity-ai-era)

### arXiv (May-2026 horizon papers)
- [arxiv 2508.01768 — Energon: GPU Power + Thermal Side Channels](https://arxiv.org/abs/2508.01768)
- [arxiv 2603.02891 — Kraken: Higher-order EM Side-Channel on DNNs](https://arxiv.org/pdf/2603.02891)
- [arxiv 2509.00300 — ShadowScope GPU Monitoring](https://arxiv.org/pdf/2509.00300)
- [arxiv 2404.03387 — Heckler: Breaking Confidential VMs with Malicious Interrupts](https://arxiv.org/pdf/2404.03387)
- [arxiv 2602.11434 — Intel TDX Live Migration Security Assessment](https://arxiv.org/pdf/2602.11434)
- [arxiv 2404.13544 — Faster Post-Quantum TLS 1.3 Based on ML-KEM](https://arxiv.org/pdf/2404.13544)

### Cryptography
- [IACR ePrint 2026/959 — Operationalising Post-Quantum TLS](https://eprint.iacr.org/2026/959)

### Open issues + community signals
- [github.com/langchain-ai/langgraph issues/6363 — langgraph-prebuilt breaking change](https://github.com/langchain-ai/langgraph/issues/6363)

---

## 6. CEO sign-off matrix

| Decision | Default recommendation | Reasoning |
|---|---|---|
| Begin §3 prototypes (#1 + #2 + #3) immediately | ✅ approve | Pure software; no procurement; ~2-3 days each; unlocks Sprint 17 cluster work without waiting for silicon |
| Sprint 17 Cluster A procurement | 🟡 hold until 2026-08 enforcement clears | Morello + Blackwell + TDX server = ~$25k. Sequenced so v1.1-GA stabilization comes first. |
| Sprint 17 Cluster B (Identity Federation) | ✅ approve in Sep | IETF drafts firm up in Q3-Q4 2026; align then |
| Sprint 17 Cluster C (byte-level IFC) | ✅ approve immediately after §3 | Pure software extension of Sprint 16 C7 |
| Publish open-research backlog on vos3.dev/security | ✅ approve | Honesty-as-moat positioning works; same approach used for the 23/80 statement at v1.1-GA |

---

**Status of this document:** Drafted 2026-05-24 by Claude Opus 4.7 (1M context) acting for sz@aidg.com. Awaiting CEO sign-off before §3 execution begins.
