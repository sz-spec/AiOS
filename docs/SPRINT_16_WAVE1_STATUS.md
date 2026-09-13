# Sprint 16 Wave 1 — Live Status Dashboard

**Branch:** `sprint-16` (forked from `main@19133c8`)
**Sprint:** Sprint 16, Wave 1 (Kernel/Memory/Scheduling)
**Window:** 2026-05-24 → 2026-07-15
**Execution mode:** Single-agent sequential, 5 role-buckets
**Predecessor verification:** Sprint 15 cumulative 165/165 tests pass; v1.1-GA kernel SHA-256 `67e8e0c0…7e7f0` verified

---

## Live status

| # | Agent (role) | Item | Severity | Target paths | Status | Commit |
|---|---|---|---|---|---|---|
| 1 | Swarm Lead | Cross-cutting integration + final verify | — | repo-wide | ✅ **DONE** (267/267 cumulative) | (next push) |
| 2 | Kernel Architect | **A2** KV-cache OS primitive (checkpoint/restore/fork) | 🟠 | `kernel/include/vos/kvcache.h`, `kernel/src/mm/ai_slots.c`, `backend/services/kvcache_state.py` | ✅ **DONE** (28/28 tests) | (next commit) |
| 3 | Security Hardening | **A3** Per-byte CHERI-style capability table | 🟠 | `kernel/include/vos/capability.h`, `kernel/src/sec/capability_table.c`, `backend/security/capability_table.py` | ✅ **DONE** (25/25 tests) | (next commit) |
| 4 | Scheduler Specialist | **J1** `SCHED_INFERENCE` class (TBT/TTFT) | 🟠 | `kernel/include/vos/sched_inference.h`, `backend/services/sched_inference.py` | ✅ **DONE** (18/18 tests) | (next commit) |
| 5a | Integrity & Power | **J3** RAPL batch-aware power capping | 🟠 | `backend/services/rapl_batch_bridge.py` | ✅ **DONE** (13/13 tests) | (next commit) |
| 5b | Integrity & Power | **D5** Anti-replay nonce enforcement (TEE) | 🟠 | `backend/services/attestation_nonce_gate.py` | ✅ **DONE** (18/18 tests) | (next commit) |

---

## Shared-context registry (cross-agent dependencies)

- **A2 ↔ J1:** SCHED_INFERENCE needs to know about KV-cache slot ownership for fair preemption — J1 reads `g_model_slots[]` populated by A2.
- **A3 ↔ A2:** Per-byte capability table is the enforcement layer for KV-cache cross-slot reads — A3 lookup tables consume KV-cache region descriptors from A2.
- **D5 ↔ Sprint 15 / M2:** Nonce enforcement uses the existing `attestation_service.py` measurement chain; M2 wrote model-weight SHA-384 to RTMR[2] — D5 adds nonce binding to that quote.
- **J3 ↔ G1 (shipped Sprint 15):** Batch-aware RAPL reads telemetry from `gen_ai.request.batch_size` attribute set by G1 emitter — extending the existing emitter, not redefining.

---

## Honest scope ceilings carried from prior sessions

- Primary CWD `/Users/sz/Desktop/ultra` is not a git repo → cannot use `isolation: "worktree"` for parallel agent dispatch. All 5 "agents" are logical task buckets executed sequentially.
- Sigstore tier remains dev (`CN=VOS3-DEV-NOT-FULCIO`) per the SIGSTORE_V3_GAP.md ceiling.
- A3 CHERI tag emission path is software-fallback only on this Mac M-series host; CHERI hardware (Morello / CHERIoT) required for byte-granularity capability enforcement in hardware.
- D5 nonce verification path will be exercised against the dev attestation chain; real Fulcio integration still pending.

---

## Test-gate progression

After each agent's commit, the cumulative-sweep target grows:

| After agent | Cumulative test target | Notes |
|---|---|---|
| baseline (sprint-16 fork) | 165 (Sprint 15 floor) | must remain green |
| Agent 2 (A2) | 165 + ~15 = ~180 | KV-cache checkpoint/restore/fork |
| Agent 3 (A3) | ~180 + ~15 = ~195 | capability table + denial paths |
| Agent 4 (J1) | ~195 + ~10 = ~205 | scheduler-class fairness |
| Agent 5a (J3) | ~205 + ~10 = ~215 | RAPL batch-aware policy |
| Agent 5b (D5) | ~215 + ~10 = ~225 | nonce-enforcement + replay |
| Agent 1 final | **267 actual** (165 + 102 new) | full sweep + verify_release.sh ✅ |

**Wave 1 complete:** 5/5 items shipped + final integration. **267/267 cumulative pass in 22.14s.** Kernel rebuilds clean from sprint-16 (text=671799 — new functions linker-GC'd pending Wave 2 callers).

**Commits on `sprint-16`:**
- `88e298a` A2 KV-cache OS primitive (28 tests)
- `c09086c` A3 per-byte capability table (25 tests)
- `1f14a50` J1 SCHED_INFERENCE class (18 tests)
- `5899060` J3 RAPL batch-aware power-cap bridge (13 tests)
- `267e6d4` D5 anti-replay nonce gate for TEE (18 tests)

---

## Update protocol

- After each agent commits, I update the **Status** column + **Commit** column in the table.
- After each agent's cumulative sweep passes, I update the test-gate row.
- If any agent hits a blocker, status → `BLOCKED` with one-line reason, and Agent 1 (Swarm Lead) is notified to resolve cross-layer dependency.
