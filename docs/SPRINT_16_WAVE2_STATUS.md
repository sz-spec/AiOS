# Sprint 16 Wave 2 — Live Status Dashboard

**Branch:** `sprint-16-wave2` (forked from `main@fc077f7`)
**Sprint:** Sprint 16, Wave 2 (Taint + Identity + Observability + Local-Channel)
**Window:** 2026-05-24 → 2026-08-15
**Predecessor verification:** Sprint 15+16 Wave 1 cumulative 267/267 tests pass on main

## Live status

| # | Item | Severity | Target paths | Status | Commit |
|---|---|---|---|---|---|
| 1 | **C7** OS-level IFC taint engine | 🟠 | `backend/security/ifc_engine.py` | ✅ **DONE** (28/28) | — |
| 2 | **F4** SPIFFE Federation accept | 🟠 | `backend/core/security/spiffe_federation.py` | ✅ **DONE** (21/21) | — |
| 3 | **F5** Vault JIT bridge + kernel hook header | 🟠 | `backend/services/vault_jit_bridge.py`, `kernel/include/vos/cred_rotate.h` | ✅ **DONE** (22/22) | — |
| 4 | **G2** OTel agent task/action spans | 🟠 | `backend/services/telemetry_genai_agent_spans.py` | ✅ **DONE** (25/25) | — |
| 5 | **G4** AgentSight-style reasoning audit | 🟠 | `backend/services/reasoning_audit.py` | ✅ **DONE** (21/21) | — |
| 6 | **H3** AF_VOS3_AGENT socket family | 🟠 | `kernel/include/vos/af_vos3_agent.h`, `backend/services/agent_local_channel.py` | ✅ **DONE** (18/18) | — |
| F | Final integration + PR | — | repo-wide | ✅ **DONE** (402/402 cumulative) | — |

**Wave 2 complete.** 6/6 items shipped + 135 new tests. Cumulative Sprint 15+16: **402/402 pass in 23.31s**.

## Cross-cutting dependencies

- **C7 ↔ C2 (Sprint 15):** taint engine consumes ToolResultPacket.raw_hash from DualLLMRouter to label tool outputs at the chokepoint.
- **C7 ↔ A3 (Wave 1):** taint labels enforced via capability_table.check() at the egress sink.
- **F4 ↔ F1 (Sprint 15):** federation verifier accepts SPIFFE SVIDs from foreign trust domains using the existing JWT-verification path.
- **F5 ↔ M1+M2 (Sprint 15):** credential rotation invalidates open fds bound to the rotated token; ties into the existing attestation chain.
- **G2 ↔ G1 (Sprint 15):** extends the same OTel emitter with new attribute set; backward-compatible.
- **G4 ↔ G1 (Sprint 15) + G2:** reasoning audit emits its own gen_ai.reasoning.* spans alongside the task/action spans.
- **H3 ↔ F1+F4:** peer-identity-verification at connect() time uses the SPIFFE verifier (F1 native + F4 federated).

## Test-gate progression

| After item | Cumulative test target |
|---|---|
| baseline (sprint-16-wave2 fork) | 267 (Sprint 15+16 Wave 1 floor) — must remain green |
| C7 | 267 + ~20 = ~287 |
| F4 | ~287 + ~15 = ~302 |
| F5 | ~302 + ~15 = ~317 |
| G2 | ~317 + ~12 = ~329 |
| G4 | ~329 + ~15 = ~344 |
| H3 | ~344 + ~12 = ~356 |
