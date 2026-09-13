# vOS.v1 — Architecture (Engine + Shield + Product)

**Stage:** Sprint 15 (Sovereign Agent Edition GA)
**Date:** 2026-05-24
**Repository:** `https://github.com/sz-spec/vOS.1.git` branch `main`
**Release:** **vOS v1.1.0-GA — Sovereign Agent Edition** (commit `1051fbe`)
**Canonical kernel SHA-256:** `67e8e0c058a1d6706552df3601b0162189a8bba01b52bb6ae4db7f667c73e7f0`
**Canonical kernel SHA-384:** `8d7d99532eedd0f655c1bd4cb553fb147362314c2060585fa3e2db8edf594e95c7d2b9e75dfb85a4072ae592c0ad34bc`

This document is the canonical map of how vOS.v1 came together: which subsystem came from which source tree, how they fit, and where the seams are. It complements `docs/PROVENANCE.md` (the cryptographic / build-determinism provenance) and `docs/CYBER_OVERLAY_INTEGRATION.md` (file-level merge log).

v1.1-GA additions on top of the Engine+Shield+Product baseline: Sprint 14 closed 5 v1.1 launch-blocker gaps (Stage-10 file completion, ML-KEM-768 hybrid KEX, silicon CI, Authenticode MSI, duplicate-dirs cleanup); Sprint 15 closed 18 agent-era gaps from the 80-problem catalog at `docs/AGENT_ERA_OS_PROBLEMS.md` (model-security I1+I3+I6, sandbox C6+C8, kernel-link A1+K4, compliance P3+N1+N4, identity F1+F2, observability G1+G5, net-sec H4+H5, docs P5+P6, hardening C2, QA F3). See `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` for per-item closure status.

---

## 1. Three sources, one tree

vOS.v1 is the unified successor to three previously-parallel forks:

| Source | Local path | Role | Ported in |
|--------|-----------|------|-----------|
| **vos4** (the Engine) | `/Users/sz/Desktop/vos/vos4` | High-performance microkernel baseline (musl libc, POSIX threads, signal delivery, full Linux ABI syscall coverage, deterministic builds) | Stage 0 — full clone as base |
| **VOS3-Cyber** (the Shield) | `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3-Cyber` | Security overlay: SCHED_CORE cookies, TEE attestation, IntentManifest validator, Sigstore pipeline, OWASP test suite | Stages 1-11 (selective port) |
| **VOS3** (the Product) | `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3` | Investor-grade handoff: disk.img bare-metal testbed, FINAL_SHA_MANIFEST, GLOBAL_SIGNATURE, audit reports | Stage 9 (preservation only) |

The naming convention "Engine + Shield + Product" reflects the conceptual roles: vos4 IS the engine that runs; VOS3-Cyber IS the shield that protects it; VOS3 IS the product wrapper investors saw at v20.0 Genesis Master.

---

## 2. Layer map

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Tauri 2.0 desktop shell (frontend/ + desktop/src-tauri/)                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Next.js 27 frontend                                              │  │
│  │   • /sovereign — Stage 10.3 control panel                         │  │
│  │   • /chat, /builder, /agents — vos4 baseline                      │  │
│  │   • useTokenVerification — Stage 10.3 streaming-fidelity          │  │
│  └──────────────────────────────────┬─────────────────────────────────┘  │
│                                     │ #[tauri::command] IPC              │
│  ┌──────────────────────────────────▼─────────────────────────────────┐  │
│  │  Rust commands.rs (Stage 10.3 added 5 policy_* commands)          │  │
│  │  VBusClient (HMAC-SHA256, async unix-socket)                      │  │
│  └──────────────────────────────────┬─────────────────────────────────┘  │
└─────────────────────────────────────┼────────────────────────────────────┘
                                      │ Unix socket (virtio-serial chardev)
┌─────────────────────────────────────▼────────────────────────────────────┐
│  FastAPI backend (backend/)                                              │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Stage 10.x services:                                             │  │
│  │   • policy_override.py — kill switch + per-agent override         │  │
│  │   • compliance_store.py — SQLite/SQLCipher VEX archive            │  │
│  │   • intent_manifest_builder.py — schema-v2 envelope generator     │  │
│  │   • integrity_worker.py — async SHA-384 worker (sub-1ms target)   │  │
│  │  Stage 11 supply chain:                                           │  │
│  │   • infra/security/sigstore_v3_bundle.py                          │  │
│  │   • infra/security/rekor_v2_log.py                                │  │
│  │   • infra/security/build_sbom.py (CycloneDX 1.5 + VEX)            │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────┬────────────────────────────────────┘
                                      │ VBusDriver.send_command(...)
┌─────────────────────────────────────▼────────────────────────────────────┐
│  vOS kernel (vos4 base + VOS3-Cyber overlay) — kernel/                  │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Cyber overlay (Stages 1-6):                                      │  │
│  │   • mm/intent_validator.c — schema-v2 envelope validator          │  │
│  │   • mm/tee.c — RTMR-extending composed-commitment activate        │  │
│  │   • mm/hcs.c — Hardware Context Switch L1D flush                  │  │
│  │   • mm/audit_ring.c (Stage 10.1) — bounded compliance ring        │  │
│  │   • sched/core_cookie.c — SMT isolation (Z3-proven)               │  │
│  │   • crypto/sha384.c — IntentManifest measurement                  │  │
│  │   • exec/action_bridge.c — confidence gate (Stage 10.2)           │  │
│  │  vos4 baseline:                                                   │  │
│  │   • drivers/virtio_bridge.c — VBus dispatcher (~160 commands)     │  │
│  │   • mm/{ai_guard.c, ai_slots.c, ...} — AI Guard subsystem         │  │
│  │   • sched/scheduler.c — base scheduler                            │  │
│  │   • boot/* — Limine multiboot2 entry                              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                          QEMU x86_64 (or bare metal Stage 14)
                          + virtio-serial chardev for VBus
```

---

## 3. Stage timeline (commits on `unified-master-v1`)

| Stage | Commit | What landed |
|-------|--------|-------------|
| 0 | `48fb084` | vos4 clone as unified base |
| 1 | `d5bfdb0` | Cyber kernel modules ported (intent_validator, tee, core_cookie, sha384, hcs) |
| 2 | `6f182e9` | Cyber sources compile clean |
| 3 | `acbaa13` | SCHED_CORE cookie init + IntentManifest self-test wired |
| 4 | `a237997` | Userspace bridge: `INTENT_SUBMIT` VBus command + hex decoder |
| 5 | `f1f2ad8` | Hardware-rooted enforcement: TEE slot-binding + RTMR composed-commitment |
| 6 | `180711d` | All 23/23 Cyber kernel symbols LIVE in vos3.elf |
| 7 | `36bebf0` | Reproducibility: deterministic SHA-256 + RDRAND #UD fixes |
| 8 | `1f0bbcf` | ai_guard.c RDRAND #UD on qemu64 fixed |
| 9 | `a5c26db` | VOS3 preservation overlay (handoff dir, disk.img, AUDIT_STAGE_A.md) |
| 10.1 | `76f0ebc` | Kernel audit ring + AUDIT_FAIL_QUOTE |
| 10.2 | `94387cb` | IntentManifest schema v2 + hallucination guardrail |
| 10.2.2 | `5c8a797` | VOS_FORCE_PERMIT + per-agent override + REVIEW_REQUIRED fallback |
| 10.3 | `4eee6a2` | Audit ring drain + manifest builder + Sovereign Control Panel + integrity worker |
| 11 | `59e1247` | Supply chain: Sigstore v3-shaped + Rekor v2 + CycloneDX 1.5 + VEX |
| 12 | `6484823` | Test suite + Z3 re-validation (4/4 UNSAT) |
| 12 deep-triage | `8b7c633` | True Pass Rate 100% (excl. Stage-10 missing-files) |
| 13 | (this commit) | Compliance docs + post-quantum inventory + System-Context prompt |

---

## 4. Where the seams are (and why they're not bugs)

A diligence reviewer will notice the following "seams" — places where vos4 and VOS3-Cyber meet. They are documented design boundaries, not technical debt:

### 4.1 — Two backend test directories

`backend/tests/` has BOTH the vos4-era test files (chaos, fuzz, stress at top level — 138 files) AND the VOS3-Cyber-era structured subdirs (security, negative, owasp, red_team, contracts, integration, perf, benchmarks). The two sets cover overlapping but distinct surface area.

**Why:** the vos4 tests target raw kernel + low-level infra; the Cyber tests target the security overlay. Merging them would force one set to import the other's fixtures, which would create a cycle. They run independently and report independently.

### 4.2 — Three middleware files

`backend/middleware/` has `auth.py` + `app_auth.py` + `clerk_auth.py` + `team_auth.py`. These look duplicative but cover different identity types: human users (auth.py), apps (app_auth.py), Clerk JWT verification (clerk_auth.py), team-membership (team_auth.py). Each runs only on its specific URL prefix.

### 4.3 — Two config systems for crypto

The kernel has its own `kernel/include/vos/crypto.h` constants; the backend has its own dispatch in `attestation_service.py`. The two are kept in sync manually via `docs/POST_QUANTUM_INVENTORY.md` (Stage 13). A future Stage adds an automated cross-check.

### 4.4 — Redundant SBOM generators

`infra/security/build_sbom.py` (Stage 11 comprehensive walker) and `infra/security/build_sbom_v20_1.py` (historical v20.1 explicit-list reference). The historical version is preserved for audit traceability — a regulator who saw the v20.1 SBOM in the original VOS3 release should be able to compare it against the current SBOM. Not a duplication; a deliberate historical artefact.

### 4.5 — Resolved seams (closed by Sprint 14.3)

Earlier revisions of this document called out **`backend/rag/` ↔ `backend/ai/rag/`** and **`backend/agents/` ↔ `backend/ai/agents/`** as duplicate-code seams that needed consolidation. Sprint 14.3 (2026-05-20) closed that loop:

- The root-level `backend/rag/` and `backend/agents/` directories were removed in an earlier consolidation pass.
- Sprint 14.3 verified zero remaining orphaned imports (`grep -rnE "(from|import) (rag|agents)\b"` returns nothing in `backend/`).
- 27 stale documentation references were rewritten from `backend/agents/` → `backend/ai/agents/` across `docs/TECHNICAL_IMPLEMENTATION_SPEC.md` (21), `backend/mcp-server/docs/PIPELINE_ARCHITECTURE.md` (2), and `COMPETITIVE_GAP_PLAN.md` (4). `migration_plan.md` had 2 more refs cleaned up in the same sweep.

Canonical locations: **`backend/ai/rag/`** for RAG pipelines (REFRAG, CLaRa, TAO) and **`backend/ai/agents/`** for the multi-agent LangGraph orchestration. Left in the seam list as a documented closure for reviewers cross-referencing older audit reports.

---

## 5. The "Honest Scope Ceiling" tradition

Every stage from 9 onwards has shipped with an explicit "honest scope ceiling" section in its commit body and (for non-trivial stages) a dedicated gap doc:

| Stage | Gap doc |
|-------|---------|
| 9 | `migration_plan.md` (Wasmtime v44.0.2 reasoning) |
| 10.2.2 | `docs/POLICY_OVERRIDE.md` |
| 10.3 | `docs/STREAMING_FIDELITY.md` |
| 11 | `docs/SIGSTORE_V3_GAP.md` |
| 12 | `docs/TEST_SUITE.md` (post-triage report) |
| 13 | `docs/POST_QUANTUM_INVENTORY.md`, `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md`, `docs/vOS_System_Context.md` |

This is by design. The implementer's training data ends January 2026; honest scope ceilings document where the cryptographic / specification reality may have moved beyond what we shipped, with explicit swap points so a fresh-eyes engineer can close the gap when the upstream spec is in hand.

---

## 6. What's NOT in this architecture (yet)

For completeness:

- **Stage 14 deliverables:** multi-target reproducible builds (Linux ELF — done; Bare-metal UEFI — pending; Windows Hyper-V — pending) plus Sovereign Boot shim. Not in this commit.
- **Stage 10's missing-files batch:** `core/security/connectors/`, `api/compliance_routes.py`, `services/vbus_ring_buffer.py`, `services/prefetch.py`, `core/repositories/vault_pool.py`, `core/security/cert_vault.py`, `core/security/rotation_manager.py`, `apex_sim`. These are the 40 ModuleNotFoundError tests in Stage 12's run; will port together as a batch.
- **Live HTTP routes for `/api/policy/*` and `/api/compliance/*`:** the kernel surface and backend services exist; the FastAPI route wiring is part of the same Stage-10 missing-files batch.
- **Production Sigstore push:** `infra/security/rekor_v2.jsonl` is the local transparency log; pushing to public `rekor.sigstore.dev` is a CI step deferred until the actual upstream v3 API surface is stable.
- **macOS/Windows installer signing:** `cargo tauri build` produces the installer artefacts; signing them with a real Apple Developer / Microsoft Authenticode certificate is operator-side work, not in-tree code.
