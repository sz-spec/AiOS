# Sprint 19 Plan — Moat Expansion (post-v1.2-RC)

**Date:** 2026-05-29
**Predecessor:** v1.2-RC sealed (`v1.2.0-rc` @ `main` `d45fca6`); 37/80 catalog closed (46%)
**Window:** 2026-05-29 → ongoing (interleaved with v1.2 GA operator/hardware steps)
**Driver:** CEO directive to expand the moat toward 50%+ — executed under the v1.2 anti-inflation seal (real enforcement, not count-padding).

---

## 0. Context + the honesty constraint

v1.2-RC sealed at **37/80 (46%)** with an explicit principle: *the count is deepened, not inflated.* Sprint 19 expands the moat **only** by shipping genuine fail-closed enforcement that meets the C7 bar (refuse the unsafe operation), never by relabeling operator runbooks as "closed."

The remaining 43 catalog items partition as:
- **8 research-only** (A5 Rowhammer, C1/C4 prompt-injection fundamentals, E5 Energon physics, I4 poisoning, I6 HF config, K3 fsync, P1 standards) — **genuinely uncloseable**; these are the investor-facing TAM. Do NOT attempt.
- **14 hardware/vendor-bound** (A3 CHERI, A4, D1/D3/D7, E4/E7, J2/J3/J5, K1/K2, L2, O2) — need silicon or vendor roadmaps.
- **~18 track-upstream** — production fix exists upstream; vOS can ship *enforcement* that fail-closes when the upstream fix isn't active. **This is the only honest software path to new closures.**

---

## 1. Wave 1 — SHIPPED (2026-05-29)

| Item | Catalog problem | What shipped | Enforcement | Tests |
|---|---|---|---|---|
| **E3** | GPU DMA bypasses page tables (IOMMU+ATS) | `backend/security/iommu_dma_guard.py` | `require_iommu_for_gpu_bind()` **fail-closes** GPU slot binding unless IOMMU provably ENFORCING (cmdline + iommu_groups + no passthrough) | 13 |
| **E6** | Perf counters → co-tenant model fingerprinting | `backend/security/perf_counter_lockdown.py` | `require_lockdown_for_multitenant_inference()` **fail-closes** multi-tenant launch unless `perf_event_paranoid≥2` + NVIDIA `RmProfilingAdminOnly:1` | 17 |

**Moat after Wave 1: 37 → 39/80 (≈49%).** Both are real fail-closed gates (refuse the bind/launch), read-only on host state, with dev-override escape hatches that log loud WARNINGs. 30/30 tests green via injected fake procfs/sysfs (run identically on macOS dev + Linux CI).

### Integration points (callers that must invoke the gates)

- **E3** → the GPU slot-bind path (Sprint 16 E7 DRA+MIG allocator / `mm/ai_slots` bind). Call `require_iommu_for_gpu_bind()` before handing an agent accelerator memory. *(Wiring into the allocator is a small follow-up — the gate is shipped + tested standalone first, mirroring how the dual-LLM router and kernel-gate connector landed.)*
- **E6** → the multi-tenant inference-launch path. Call `require_lockdown_for_multitenant_inference()` before scheduling an agent onto a shared accelerator host.

---

## 2. Wave 2 — candidate (NOT yet built) to reach a clean 50%

To cross 40/80 = 50% honestly, ONE more genuine enforcement item:

| Candidate | Catalog | Honest enforcement shape | Why it qualifies |
|---|---|---|---|
| **E3 hook + E6 hook wiring** | (closes the integration loop on Wave 1) | Wire both gates into the actual GPU-bind + inference-launch code paths so they fire in production, not just unit tests | Converts "gate exists" → "gate enforced in the live path" — the same hello-world → operationalized arc as Cluster C-1 |
| **O3** noisy-neighbor QoS | O3 | A fail-closed admission gate that refuses agent scheduling onto an accelerator unless MIG partitioning + cgroup resource limits are set per the NVIDIA reference | Real admission control, not a checker — refuses the unsafe co-location |

**Decision deferred to CEO:** Wave 1's 39/80 is the honest floor. Whether to pursue O3 for a clean 50% (genuine admission gate) or wire the Wave-1 gates into live paths first is a sequencing call. Recommendation: **wire E3+E6 into the live bind/launch paths first** (closes the operationalization loop, like C-1) — then O3 for the 40th.

---

## 3. Cross-cutting state (unchanged from v1.2-RC seal)

- **PR #6 / CI:** still GitHub-Actions-billing-blocked (re-verified 2026-05-29; latest `main` run `failure` at 14:40). `constraints.txt` fix already on `main`; PR #6 superseded — closable once billing returns. See handover lock §6 rules #7-8.
- **eBPF LIVE activation:** requires `cd kernel/bpf && sudo make load` on a Linux ≥ 5.17 runner — cannot be done from the macOS dev host.
- **Local pytest is the hard-gate proxy** until billing returns.

---

## 4. Honest moat ledger

| Milestone | Closed | % |
|---|---|---|
| v1.1-GA (Sprint 15) | 18 | 23% |
| Sprint 16 (Waves 1-3) | 37 | 46% |
| v1.2-RC (Sprint 17+18) | 37 (deepened C7+F4) | 46% |
| **Sprint 19 Wave 1 (E3+E6)** | **39** | **≈49%** |
| Sprint 19 Wave 2 target (O3 or hook-wiring) | 40 | 50% |

The 8 research-only items remain permanently open by design — physics + ML-architecture limits no vendor solves. That honesty IS the positioning.
