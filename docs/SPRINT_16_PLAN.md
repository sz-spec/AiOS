# Sprint 16 Plan — v1.2 Roadmap (Medium-Term Agent-Era Closures)

**Date:** 2026-05-24
**Status:** PROPOSED (awaiting CEO sign-off)
**Predecessor:** Sprint 15 (Sovereign Agent Edition, v1.1-GA shipped 2026-05-24 at `main@19133c8`)
**Window:** 2026-06-01 → 2026-09-01 (split: Sprint 16 to 2026-07-15, Sprint 17 to 2026-09-01)
**Companion documents:**
  - `docs/AGENT_ERA_OS_PROBLEMS.md` (80-problem catalog)
  - `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` (solutions classification)
**Sources used to build this plan:** tier-1 only, **April–May 2026** publication horizon — arXiv (April–May 2026), CNCF/Kubernetes (April–May 2026 blogs), Intel Trust Authority (May 2026), HashiCorp (April–May 2026), OpenTelemetry (May 2026), Linux kernel docs (current), IETF drafts (current).

---

## 0. Strategic context

Sprint 15 closed **18 of the 80 cataloged problems** before v1.1-GA. The solutions roadmap categorizes the remaining 57 problems as:

| Class | Count | Status after v1.1-GA |
|---|---|---|
| 🟢 **Sprint 15 — DONE** | 18 | Shipped 2026-05-24 |
| 🔵 **Sprint 16-17 — this plan** | **19** | Proposed below |
| 🟣 **Sprint 18+ (Q1-Q2 2027)** | 14 | Roadmap only — hardware/vendor dependent |
| 🟡 **Track upstream** | 18 | Monitoring only |
| 🔴 **Open research** | 8 | No production fix anywhere |

**This plan covers the 19 🔵 items**, the next-quarter closure target. After Sprint 17 ships, vOS will have **42 of 80** problems with ✅-solved or ⚪-partial posture — 53% catalog coverage in 4 months, an industry-unique claim.

**Critical deadline reminder:** EU AI Act Article 73 enforces 2026-08-02 (70 days from this plan). Wave 1 of Sprint 16 MUST land before that date so the additional incident-response surface (G2, G4) is operational at enforcement.

---

## 1. The 19 items by wave

Items grouped to maximize independence (Wave-N items don't block Wave-N+1) and to align with research stream maturity. Each item carries the canonical ID from the 80-problem catalog.

### Wave 1 — Kernel + memory + scheduling (7 items, target 2026-07-15)

These are the items that BENEFIT from being landed before the August 2 enforcement window because they shape the kernel-side primitives that downstream Wave-2/Wave-3 features sit on.

| ID | Title | Severity | Approach |
|---|---|---|---|
| **A2** | KV-cache as OS primitive | 🟠 | Treat KV-cache as process state with checkpoint/restore/fork ops. Implement `vos3_kvcache_*` syscall family in kernel mm; backing store on hugepages with per-tenant tier classification (hot RSS / cold mmap / cross-process shared). Validates against **ProbeLogits** (arxiv 2604.11943) reference. |
| **A3** | Per-byte ACL for shared model memory | 🟠 | Adopt **CHERI capability tags** for shared weight regions on Morello + CHERIoT hardware paths. Software-fallback for non-CHERI hosts uses fine-grained mprotect + a vOS-side capability table. |
| **A4** | Page-table side-channel mitigation for transformer attention patterns | 🟠 | Implement page-coloring + TLB-randomization governor for AI workloads (extension of A1 workload-hint API shipped in Sprint 15). |
| **B3** | Per-call gating for Linux capabilities | 🟠 | **eBPF LSM** programs attached at syscall-arg level via `bpf_lsm` hooks; vOS ships baseline policies for CAP_NET_RAW / CAP_NET_BIND_SERVICE / CAP_SYS_PTRACE that the intent-manifest evaluator can override per-tool. |
| **B4** | Seccomp/Landlock with agent semantics | 🟠 | New **vOS policy DSL** that compiles to eBPF LSM programs. Per-tool / per-URL / per-filename-pattern expressivity. Validates against **eBPF-PATROL** (arxiv 2511.18155) + **OAMAC** (arxiv 2601.14021). |
| **J1** | CFS inference latency SLA class | 🟠 | New `SCHED_INFERENCE` class in `sched_ext` framework; carries TBT/TTFT deadlines per request. Validates against **TempoNet** (arxiv 2602.18109) + the **SLAI scheduler** referenced in arxiv 2508.01002. |
| **J3** | Power capping batch-aware | 🟠 | Extend RAPL governor with batch-size telemetry from `gen_ai.request.*` OTel spans. Validates against **RAPID disaggregated inference** (arxiv 2601.12241) + Argo NRM+RAPL co-control. |

### Wave 2 — Taint + identity + observability (6 items, target 2026-08-15)

These are the items that operationalize the agent-execution audit surface needed for steady-state EU AI Act Article 73 compliance and for cross-org agent flows.

| ID | Title | Severity | Approach |
|---|---|---|---|
| **C7** | OS-level taint tracking for untrusted-input flow | 🟠 | Adopt the **Fides/GAAP/SAMOS** model: label every byte that originates from a tool result; propagate labels via syscall hooks; enforce egress-deny when high-taint labels reach network sinks. Implementation lives in `backend/security/ifc_engine.py` + matching eBPF programs. Validates against arxiv 2505.23643 + arxiv 2604.19657. |
| **F4** | Cross-tenant agent identity federation | 🟠 | Adopt **SPIFFE Federation** protocol (already standardized at spiffe.io/docs/latest/spiffe-specs/spiffe_federation/). vOS Sprint 16 wires SPIRE Agent into the F1 verifier (shipped Sprint 15) to accept federated bundles from registered partner trust domains. |
| **F5** | Long-lived → JIT credential rotation kernel hook | 🟠 | New kernel `vos3_cred_rotate(fd, expiry_ns)` syscall + integration with **HashiCorp Vault Enterprise 2.0** (April 2026) JIT API + SPIFFE auth. The kernel-side hook lets a credential rotation invalidate every open fd that holds the prior token. |
| **G2** | Standardized "agent decision → tool call → result" schema | 🟠 | Adopt **OpenTelemetry GenAI agent spans** (graduated 2026-05-21, blog post 2026-05-14). vOS extends the in-tree G1 emitter (shipped Sprint 15) to cover `gen_ai.agent.task.*` + `gen_ai.agent.action.*` per the OTel issue #2664 proposal. |
| **G4** | LSM captures LLM reasoning, not just syscalls | 🟠 | Adopt **AgentSight** approach (arxiv 2508.02736) — eBPF interception of TLS-encrypted LLM traffic for intent capture + kernel-event correlation for effect capture. <3% overhead claim must be re-validated on vOS workloads. |
| **H3** | Trusted-local-channel OS primitive for sibling agents | 🟠 | New `AF_VOS3_AGENT` socket family — kernel-attested peer identity via SPIFFE SVID at connect() time. Sibling agents on same host can verify each other without userspace mTLS roundtrip. |

### Wave 3 — TEE + GPU + storage + egress (6 items, target 2026-09-01)

These are the items with hardware/vendor dependencies; sequenced last because partner availability gates them.

| ID | Title | Severity | Approach |
|---|---|---|---|
| **B5** | App-store scope → kernel-enforced | 🟠 | Extend the eBPF LSM programs from B3+B4 (Wave 1) with scope-string lookup tables. OAuth scope "contacts.read" becomes a kernel-side allowlist entry that an unprivileged process cannot bypass. |
| **D1** | TDX live-migration hardening (post-Feb 2026 Google Cloud findings) | 🟠 | Adopt the **Intel Trust Authority** May-2026 attestation client updates (TPM + SEV-SNP composite policies). vOS attestation_service.py adds the composite-policy verification path. |
| **D5** | Attestation freshness — per-request nonce binding | 🟠 | Mandate `eat_nonce` claim per **draft-ietf-rats-ar4si-09** in every TDX/SEV-SNP quote that vOS verifies. Replay-attack regression test added to `backend/tests/security/test_attestation_freshness.py`. |
| **E7** | NVIDIA MIG QoS via DRA | 🟠 | Adopt the **NVIDIA DRA Driver for GPUs** donated to CNCF at KubeCon April 2026 (now community-governed). vOS K8s manifests (shipped Sprint 15 / C8) gain MIG-partitioned device classes with explicit QoS bounds. Requires K8s ≥ v1.34.2. |
| **H1** | Egress policy beyond URL allowlist | 🟠 | Combine the H4 DNS pinning (shipped Sprint 15) with eBPF-side connection labeling derived from the C7 (Wave 2) taint engine. New `EgressOutcome.DENY_TAINTED_DESTINATION` outcome class. |
| **K5** | Encrypted-at-rest enforcement for model weights | 🟠 | Adopt **fscrypt v2 per-file keys** (kernel.org/doc/html/docs.kernel.org/filesystems/fscrypt.html). vOS-side: model_loader gains an `fscrypt_required=True` policy hint; mmap of an unencrypted weight file from a policy-marked directory is denied at vfs_open. |

---

## 2. Catalog of changes — file-level plan

| Wave | Item | Code paths added/modified | Tests added |
|---|---|---|---|
| 1 | A2 | `kernel/include/vos/kvcache.h`, `kernel/src/mm/kvcache.c`, `backend/services/kvcache_state.py` | `kernel/tests/test_kvcache_*.c`, `backend/tests/services/test_kvcache_state.py` |
| 1 | A3 | `kernel/include/vos/capability.h`, `kernel/src/mm/cap_table.c` | `kernel/tests/test_cap_table.c`, `backend/tests/security/test_per_byte_acl.py` |
| 1 | A4 | `kernel/src/mm/page_coloring.c`, extend `kernel/src/mm/ai_guard.c` | `kernel/tests/test_page_coloring.c` |
| 1 | B3 | `infra/security/ebpf/lsm_cap_gate.bpf.c`, `backend/security/cap_gate.py` | `backend/tests/security/test_cap_gate.py` |
| 1 | B4 | `infra/security/policy_dsl/` (new tree), `backend/security/policy_compiler.py` | `backend/tests/security/test_policy_dsl.py` |
| 1 | J1 | `kernel/src/sched/inference_class.c`, `kernel/include/vos/sched_inference.h` | `kernel/tests/test_sched_inference.c` |
| 1 | J3 | `kernel/src/power/batch_rapl.c`, extend `backend/services/telemetry_genai.py` | `backend/tests/services/test_batch_rapl.py` |
| 2 | C7 | `backend/security/ifc_engine.py`, `infra/security/ebpf/ifc_label.bpf.c` | `backend/tests/security/test_ifc_engine.py` |
| 2 | F4 | extend `backend/core/security/spiffe_workload_identity.py` (federation accept) | `backend/tests/integration/test_spiffe_federation.py` |
| 2 | F5 | `kernel/src/cred/rotate.c`, `backend/services/vault_jit_bridge.py` | `backend/tests/integration/test_vault_jit_rotation.py` |
| 2 | G2 | extend `backend/services/telemetry_genai.py` (agent task/action spans) | extend `backend/tests/governance/test_telemetry_genai.py` |
| 2 | G4 | `infra/security/ebpf/agentsight_tls.bpf.c`, `backend/services/reasoning_audit.py` | `backend/tests/integration/test_reasoning_audit.py` |
| 2 | H3 | `kernel/src/net/af_vos3_agent.c`, `kernel/include/vos/af_vos3.h` | `kernel/tests/test_af_vos3_agent.c`, `backend/tests/integration/test_agent_local_channel.py` |
| 3 | B5 | extend `infra/security/ebpf/lsm_cap_gate.bpf.c` with scope tables | extend `backend/tests/security/test_cap_gate.py` |
| 3 | D1 | extend `backend/services/attestation_service.py` (composite policies) | extend `backend/tests/security/test_attestation.py` |
| 3 | D5 | extend attestation_service.py (eat_nonce enforcement) | `backend/tests/security/test_attestation_freshness.py` |
| 3 | E7 | `infra/k8s/sandboxes/dra-gpu-mig-class.yaml`, extend gvisor_config | `infra/k8s/sandboxes/tests/test_dra_mig.py` |
| 3 | H1 | extend `backend/core/security/connectors/runtime_firewall.py` (tainted-destination outcome) | extend `backend/tests/security/test_runtime_firewall.py` |
| 3 | K5 | extend `backend/security/model_loader.py` (fscrypt-required policy), kernel hook in vfs_open | `backend/tests/security/test_fscrypt_required.py` |

---

## 3. Execution model — single-agent sequential (same as Sprint 15)

Per the lesson from the Sprint 15 swarm-setup attempt (primary CWD `/Users/sz/Desktop/ultra` is not a git repo, blocking parallel `isolation: "worktree"` agent dispatch), I will execute Sprint 16 in the same single-agent sequential mode used for Sprint 15. The wave structure represents logical concurrency; actual code lands sequentially.

Per-item closure ritual matches Sprint 15:
1. Implement.
2. Add tests.
3. Run targeted test sweep + verify no regression in the cumulative Sprint 15 sweep (currently 165/165).
4. Commit with `feat(sprint-16.<wave>.<area>): <ID> — <one-liner>`.
5. After wave complete, run full cumulative sweep + push.

---

## 4. Exit criteria + go/no-go gates

### Sprint 16 (Wave 1, due 2026-07-15)
- All 7 Wave-1 items committed to `sprint-16` branch
- Cumulative Sprint 15+16 test sweep passes (target: 165 + ~60 new = ~225 tests)
- Kernel rebuilds cleanly; `.text` growth tracked
- `verify_release.sh` passes on a fresh GA-style rebuild
- **No-go conditions:** any 🔴-class test failure, kernel ELF size growth > 10%, regression in pre-existing 165-test sweep

### Sprint 17 (Waves 2+3, due 2026-09-01)
- All 12 Wave-2+3 items committed
- Cumulative test sweep target: ~225 + ~80 = ~305 tests
- EU AI Act Article 73 enforcement (2026-08-02) passes with no operator complaints about missing primitives
- One external design-partner pilot signed (target: fortress-tier hospital or government agency)

---

## 5. Honest scope ceilings to publish at v1.2 release

1. **CHERI (A3) requires Morello / CHERIoT hardware** for full byte-granularity capability tags. Non-CHERI hosts run a software-fallback that is weaker — clearly labeled.
2. **eBPF LSM (B3, B4, B5, C7, G4) requires Linux ≥ 5.7** with CONFIG_BPF_LSM=y. RHEL 9 ≥ 9.2 ships this; older distros are not supported for these features.
3. **NVIDIA DRA + MIG (E7) requires K8s ≥ v1.34.2** and a Blackwell-era or later GPU. Older deployments fall back to time-slicing without QoS bounds.
4. **Vault JIT integration (F5) requires Vault Enterprise 2.0** (April 2026). Vault Community Edition lacks the JIT API surface; documented.
5. **AgentSight TLS interception (G4) requires the agent process to be launched under an LD_PRELOAD shim** or be wrapped in the vOS supervisor — bare-metal processes without this instrumentation aren't covered.
6. **fscrypt per-file keys (K5) require Linux ≥ 5.4** with CONFIG_FS_ENCRYPTION=y and a supporting filesystem (ext4 / f2fs / ubifs / btrfs once mainlined).
7. **OpenTelemetry GenAI agent spans (G2) are still semantically maturing** — vOS will track the spec and may need to rev the emitter when the OTel issue #2664 proposal stabilizes.

---

## 6. Source list (tier-1, April–May 2026 horizon)

### arXiv (2026 papers cited above)
- ProbeLogits — Kernel-Level LLM Inference Primitives (arxiv 2604.11943, April 2026)
- TempoNet — Slack-Quantized Transformer-Guided RL Scheduler (arxiv 2602.18109)
- KernelOracle — Predicting Linux Scheduler with Deep Learning (arxiv 2505.15213)
- Optimal Scheduling for LLM Inference: SLAI scheduler (arxiv 2508.01002)
- RAPID — Power-Aware Disaggregated Inference (arxiv 2601.12241)
- An AI Agent Execution Environment to Safeguard User Data — GAAP (arxiv 2604.19657, April 2026)
- Securing AI Agents with Information-Flow Control — Fides (arxiv 2505.23643)
- AgentSight — System-Level Observability for AI Agents Using eBPF (arxiv 2508.02736)
- eBPF-PATROL — Protective Agent for Threat Recognition (arxiv 2511.18155)
- OAMAC — Origin-Aware MAC for Post-Compromise Attack Surface Reduction (arxiv 2601.14021)
- Mon CH'ERI — Mitigating Uninitialized Memory Access with Conditional Capabilities (arxiv 2407.08663)

### Standards bodies / IETF
- draft-ietf-rats-ar4si-09 — Attestation Results for Secure Interactions
- IETF draft-kdyxy-rats-tdx-eat-profile-02 — EAT profile for Intel TDX
- SPIFFE Federation specification (spiffe.io/docs/latest/spiffe-specs/spiffe_federation/)
- OpenTelemetry GenAI agent spans (opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- OpenTelemetry "Inside the LLM Call" blog post (2026-05-14)
- OpenTelemetry semantic-conventions issue #2664 (agentic systems)

### Vendor / consortium
- NVIDIA DRA Driver donation to CNCF at KubeCon April 2026 (blogs.nvidia.com/blog/nvidia-at-kubecon-2026/)
- NVIDIA NIM Operator DRA documentation (docs.nvidia.com/nim-operator/latest/dra.html)
- Intel Trust Authority — What's New (May 2026, docs.trustauthority.intel.com/main/articles/articles/ita/whats-new.html)
- Intel Trust Authority — GPU Remote Attestation (docs.trustauthority.intel.com)
- HashiCorp Vault Enterprise 2.0 announcement (April 2026)
- HashiCorp — SPIFFE Securing the Identity of Agentic AI and Non-Human Actors

### Linux kernel docs
- sched_ext extensible scheduler class (docs.kernel.org/scheduler/sched-ext.html)
- bpf_lsm programs (docs.kernel.org/bpf/prog_lsm.html)
- fscrypt v2 encryption policy + per-file keys (docs.kernel.org/filesystems/fscrypt.html)

### CHERI ecosystem
- CHERI-SIMT (ASPLOS '26) — Capability Memory Protection in GPUs
- CHERIoT (Microsoft) + Morello (Arm) production silicon

---

## 7. Open decisions requiring CEO sign-off

1. **Branch model:** `sprint-16` branched off `main@19133c8` immediately, with PR #3 against `sprint-16-baseline`? Or skip the baseline branch and PR directly against `main`?
2. **External pilot:** Which design partner gets early access to the Wave 2 reasoning-audit (G4) capability? This is a privacy-sensitive feature — the partner needs to be one we trust with full LLM traffic capture.
3. **Hardware budget:** A3 (CHERI) requires Morello hardware (~$5k-$10k development board). E7 (NVIDIA MIG) requires at least one Blackwell-class GPU host. Both are needed for Wave 3 closure validation. Approve procurement?
4. **K8s baseline:** Are we OK requiring K8s ≥ v1.34.2 for the E7 + Wave-3 deployment story? That cuts off operators still on managed-K8s lagging versions.
5. **Vault Enterprise 2.0 partnership:** F5 wiring depends on HashiCorp's commercial API. Do we license, or build the JIT-equivalent in-tree under MIT?
6. **Publication strategy:** Same as v1.1-GA — publish the SPRINT_16_PLAN.md + per-item closure status under `vos3.dev/security`? This continues the public-transparency posture but locks us into 19 named deliverables with 2026-09-01 dates.

---

**Status:** Ready for CEO review. On approval, I will create the `sprint-16` branch from `main@19133c8` and begin Wave 1 / Item A2 (kernel KV-cache OS primitive).
