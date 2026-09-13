# vOS Enterprise Cloud Enclave — Sovereign Architecture Spec & Open-Row Roadmap

> **Formal architecture roadmap for the upcoming vOS Enterprise Cloud Enclave tier
> (Intel TDX / NVIDIA Blackwell-CC).** This is the document of record for the moat
> rows that the **local GA tier cannot close on commodity/macOS hardware** — they
> are the *defined scope* of the hardware-backed Enclave tier, plus the
> research/upstream rows tracked alongside it.
>
> **Honesty contract (non-negotiable):** every row below is **OPEN / REQUIRED**,
> not shipped. Framing them as the Enclave-tier roadmap does NOT mark them done
> and does NOT change the verified moat. The verified tally is **49/80**, frozen.
> This is a *forward roadmap*, not a completion claim.

**Generated:** 2026-06-06 (reframed for the Enclave tier 2026-06-07) · **Moat frozen at 49/80 (≈61%)**
**Sources of truth:** `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` (May-2026 classification) reconciled against the closure ledger in `infra/persistence/active_context/SESSION_HANDOVER_LOCK.md §4` (Sprint 15 → 23).

## Tier mapping (what ships where)

| Tier | Hardware | Rows it can close |
|------|----------|-------------------|
| **Local GA (today)** | commodity / macOS / Linux | the 49 closed rows; local-first sovereign routing (local-titan); software-tractable backlog **exhausted at 49/80** |
| **Enterprise Cloud Enclave (roadmap)** | Intel TDX 2.0 · NVIDIA Blackwell Confidential-Compute · TPM 2.0 · CXL 3.0 | §2 silicon/hardware-bound rows (D3, D7, E4, J2, J4, K1, L2, O2, O4) — closable **only** with this hardware (Q1–Q2 2027 procurement) |
| **Upstream-tracked** | n/a (vendor/distro/standards owns the fix) | §3 rows — vOS rides along (C3, C5, L1, L3, N2, O1, D2/D6-residual) |
| **Research-only** | none exists anywhere | §1 rows — no production fix on Earth (A5, C1, C4, E5, I4, I6, K3, P1) |

The remaining single software-closable tail row (**M3** model-file SecureBoot) has
a compiled, fail-closed read-path scaffold in `kernel/src/fs/vvfs_transport.c`
(default-off, **not** counted) pending an in-kernel Ed25519+SHA-512 verify
primitive — see the M3 entry.

---

## 0. Important corrections to the requesting brief (read first)

This document deliberately diverges from the task brief on three factual points, because following the brief verbatim would produce a false artifact:

1. **There is no "Row 50–80" numbering.** The 80-problem catalog uses **category-letter IDs** (`A1`–`P6`), not sequential rows. "Row 55 / Row 72" do not exist. All IDs below are the real ones.
2. **Moat rows are NOT the 61 broad-suite test failures.** The moat measures fail-closed *security enforcement* of catalogued problems. Most open rows have **no test surface at all** (they are physics/silicon/upstream). Where the brief asks for a "Live Failure Signature," the honest answer for the large majority is *"none — not test-surfaced."* The handful of test failures that *relate* to a row (e.g. kernel-source greps) are cross-referenced and clearly marked as **test artifacts, not the row's definition**. (Full test matrix lives in `handover_quality_debt.json`.)
3. **Exactness caveat.** The full 80-row catalog (`valiant-cuddling-phoenix.md`, an Anthropic plan file) is **not present in this repo**, and the closed-49 baseline is not enumerated in a single file. The open set below is **reconciled** (roadmap classes − documented Sprint 16→23 closures). It is complete for the categorically-unclosable rows; a few medium-term IDs at the tail are flagged `STATUS-UNCERTAIN` pending the source catalog.

**Closure ledger used (SESSION_HANDOVER_LOCK §4):** S16 closed A2,A3,A4,B3,B4,B5,C7,D1,D5,F4,F5,G2,G4,H1,H3,J1,J3,K5; S19 +E3,E6,O3; S21 +B1,B2,B6,F6,E1,E2; S22 +I2,D2,D6,J5,K2 (+P2,P4 docs). O4 = "enforced-pending-silicon, not counted."

---

## 1. Category: 🔴 RESEARCH-ONLY — no production fix exists anywhere (8 rows)

> No vendor on Earth has a production solution as of mid-2026. These are the investor-facing "TAM" rows. Cannot be "closed" by code — only tracked + attested.

| ID | Name | Blockage root cause (why not closable on macOS — or anywhere) | Live failure signature | v1.3.1 handover action |
|----|------|---------------------------------------------------------------|------------------------|------------------------|
| **A5** | Rowhammer / RowPress on AI-accelerator memory (GPU HBM, NPU SRAM) | Off-CPU memory; no software mitigation exists for HBM/SRAM bit-flips | none (no test surface) | Track DRAM-vendor ECC/TRR roadmaps; document residual risk in COMPLIANCE_REPORT; no code action |
| **C1** | LLMs cannot architecturally distinguish instructions from data | Fundamental ML-architecture limitation; not patchable | none | Compensate at OS layer (taint/IFC — already C7) + attest the gap; no row-close possible |
| **C4** | Adaptive prompt-injection >85% success vs SOTA defenses | Meta-analysis of 78 studies: no defense survives adaptive attacks | none | Defense-in-depth posture only; publish honest efficacy numbers |
| **E5** | Energon — power/thermal side channel recovers transformer weights | Physics-level leak (readable through 100cm glass; 89% family-ID) | none | Physical-security deployment guidance (faraday/PDU isolation); not software |
| **I4** | Training-data poisoning / sleeper agents | Backdoors fire on trigger at inference; detection needs full training-set audit | none | Require signed provenance (I2 done) + upstream detection research; cannot self-close |
| **I6** | Hugging Face config-file deserialization RCE (pre-model-code) | Format-specification problem; runs before any sandbox | partial — `app_sandbox` safetensors-only scanner mitigates, does not close | Push format-spec fix upstream; keep safetensors-only enforcement |
| **K3** | fsync amplification from many small agent artifacts | Filesystem-architecture trade-off (durability vs storm) | none | FS-design research; consider batched-fsync journal in vvfs (research-stage) |
| **P1** | No agreed "Agent-OS" reference architecture (partial) | Standards-consensus problem; multiple competing proposals | none | Drive vOS → CNCF Sandbox proposal; inherently external/consensus-bound |

---

## 2. Category: 🟣 SILICON / HARDWARE-BOUND — needs real chips vOS cannot emulate honestly (≈9 rows)

> These require physical Intel TDX 2.0 registers, NVIDIA Confidential-Compute Blackwell, CXL 3.0 fabric, TPM chips, or vendor NPU driver APIs. **A simulated TPM/enclave/MMU does NOT close these** — it would fabricate a hardware guarantee that does not exist (explicitly refused). Procurement is Q1–Q2 2027.

| ID | Name | Category | Blockage root cause | Live failure signature | v1.3.1 handover action |
|----|------|----------|---------------------|------------------------|------------------------|
| **D3** | Code confidentiality violated via TEE timing | Silicon | Needs real enclave + KingsGuard taint pattern (ACM CCS Nov 2026) | none | Pilot KingsGuard on real TDX once procured |
| **D7** | Intel Platform Security Report — ongoing CVEs | Silicon/Upstream | Continuous; no end-state — depends on Intel microcode | none | Silicon-CI auto-test microcode patches; subscribe to Intel PSR |
| **E4** | SIMT warp-scheduling contention leaks | Silicon | Fix is NVIDIA Confidential-Compute mode on Blackwell B200 — hardware-only | none | Deployment guide pinning CC-mode on real B200; cannot software-close |
| **J2** | GPU/NPU scheduling cannot coordinate with OS CFS | Silicon/Vendor | Depends on NVIDIA Run:AI ↔ kernel CFS integration roadmap | none | Track NVIDIA roadmap; integrate when driver API ships |
| **J4** | SCHED_CORE doesn't extend to GPU SMs | Silicon | Needs GPU SM-partition primitive (VBus PARTITION_GPU + real GPU) | `STATUS-UNCERTAIN` — partial sched/ shim may exist; verify vs live ledger | Wire SM cookies once GPU partitioning HW available |
| **K1** | Model-weights FS tiering (files vs mmap) | Silicon | Requires CXL 3.0 memory-tiering fabric deployment | none | Adopt CXL tiering when hardware deploys |
| **L2** | NPU power islands not exposed to scheduler | Silicon/Vendor | Needs Apple ANE / Qualcomm Hexagon kernel driver APIs | none | Vendor-cooperation track; no software path |
| **O2** | K8s namespace isolation doesn't extend to GPU/NPU | Silicon/Vendor | Needs Multi-Instance NPU upstreamed + vendor support | none | Track K8s + vendor MIG/NPU roadmap |
| **O4** | Cross-tenant cache model-stealing | Silicon | NVIDIA Confidential-Compute cache isolation (hardware) | none — vOS posture is **enforced-pending-silicon** (NOT counted in 49) | Flip to closed only on real CC-enabled silicon validation |

---

## 3. Category: 🟡 UPSTREAM-EXTERNAL — production fix lives outside vOS; we ride along (≈7 rows)

> A real fix exists, owned by a vendor/distro/standards body. vOS's correct posture is dependency-tracking + operator guidance, not a vOS-closed row.

| ID | Name | Owner of the fix | Blockage root cause | Live failure signature | v1.3.1 handover action |
|----|------|------------------|---------------------|------------------------|------------------------|
| **C3** | EchoLeak / ShadowPrompt browser-agent attacks | Anthropic / vendors | Vendor-patched; continuous new variants | none | Continuous CVE tracking; no vOS code |
| **C5** | Frontier models 13% on web-exploit benchmarks | Anthropic ASL3 | Model-safety layer, not OS-layer | none | Track Anthropic ASL3 + classifiers; not vOS-closable |
| **L1** | Sustained inference exceeds thermal budgets | Vendor governors | Hardware power-governor responsibility | none | Operator runbook: NVIDIA SMI / Intel SST policies |
| **L3** | Mobile agent ignores battery health | iOS/Android OEM | OEM responsibility; out of scope | none | Mark out-of-scope; no action |
| **N2** | ML-DSA-65 not in glibc/OpenSSH | OpenSSL/OpenSSH/distros | Hooks exist (OpenSSL 3.5, OpenSSH 10+); distros lag | none | Distro-tracking; vOS userspace hybrid KEX already shipped (N3) |
| **O1** | Spectre/Meltdown fixes don't apply to GPU | NVIDIA CC | Same hardware fix as E4 | none | Same as E4 — CC-mode hardware |
| **D2′/D6′** | (residual) CVM HECKLER / overhead | Intel/AMD microcode + NVIDIA Blackwell | Core D2/D6 closed S22 (launch gate); residual is vendor-continuous | none | Keep CVM launch gate; track microcode |

---

## 4. Category: 🟠 COMPLEX PRODUCT LOGIC — software-possible, not yet shipped (tail; STATUS-UNCERTAIN)

> These *could* be vOS-closed in software but were not in the v1.3 scope. Each needs a product decision, not hardware. Membership here is the least certain (depends on the source catalog + exact baseline-18).

| ID | Name | Blockage root cause | Live failure signature (if any) | v1.3.1 handover action |
|----|------|---------------------|---------------------------------|------------------------|
| **M3** | SecureBoot doesn't extend to model files | `.gguf`/`.safetensors` signature verify at `fs/vvfs.c` read path not yet wired | none (kernel-path feature, no failing test) | Implement read-path sig-verify; ~1wk kernel work |
| **J3′/J-tail** | RT scheduling vs CFS (`J5` shim shipped; full RT_PREEMPT cgroup-v2 inference class) | Upstream Linux RT work + cgroup v2 class | none | Track upstream RT_PREEMPT; J5 shim already counted |
| *(catalog tail)* | Rows requiring `valiant-cuddling-phoenix.md` to enumerate exactly (e.g. any D4/G3/M1-class IDs, residual N/P docs) | Source catalog not in repo | n/a | Obtain the 80-row catalog file to finalize exact membership |

---

## 5. Cross-reference: which *test failures* touch a moat row (and why greening them ≠ closing the row)

The 61 sequential test failures are NOT moat rows, but a few **assert against** kernel/source state related to open rows. Greening them by editing the assertion or simulating hardware would **fabricate** the row's security guarantee — explicitly refused.

| Test cluster (count) | Related row(s) | Why it's a REPORT, not a close |
|----------------------|----------------|--------------------------------|
| `test_memory_scaling` (5), `test_vmm_security` (3), `kernel_guards/test_kim_inference_caps` (1) | A2 / K-mem / VMM guards | Greps live kernel C source for real guards (`VOS3_KV_CACHE_MAX_BYTES`, W^X). Pass only if the **source is actually true** — not by editing the test. |
| `test_finetune_rigor` (5) | I2 (provenance) / NPU ops | Asserts real kernel finetune source (tool-id, PCR-11, struct packing). Same — source-of-record. |
| `test_open_core_split` (5) | enterprise/open-core gating | Asserts real package-relocation + pro-gating. Architecture state, not a test bug. |
| `test_titan_agent_loop` (5) | local-titan routing (unbuilt) | Asserts a routing capability with **no backend** — a real feature gap, not a moat row. |
| `test_api_billing` (4) | — (not a moat row) | Real `503` DB-required behavior; mocking it fakes financial verification. |
| owasp / red_team / dns_pinning (9) | H2/SSRF (shipped), negation suites | Dedicated negation suites — never force-green. |

---

## 6. Bottom line for leadership

- **Software-tractable moat backlog is exhausted at 49/80.** The remaining open rows are **research-only (8), silicon/hardware-bound (≈9), or upstream-external (≈7)**, plus a small software-possible tail (M3 + catalog-uncertain).
- **None of the remaining rows can be honestly closed on a local macOS developer machine.** Closing them requires: real Intel TDX 2.0 / NVIDIA Blackwell CC / TPM / CXL 3.0 hardware (Q1–Q2 2027 procurement), upstream vendor/distro/standards delivery, or open research breakthroughs.
- **The one thing that would forge progress fast is dishonest:** simulating a TPM/enclave/MMU to pass the kernel-source security tests. That fabricates a hardware guarantee and is refused.
- **Genuine next software step:** `M3` (model-file SecureBoot at the vvfs read path) is the only clearly software-closable open row — a real ~1-week kernel task for a future sprint. Everything else is procurement/upstream/research.
