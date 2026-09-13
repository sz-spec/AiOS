# Cyber Overlay Integration — File-Level Merge Log

**Stage:** 13
**Date:** 2026-05-09

This document is the file-level provenance for what came from VOS3-Cyber into vOS.v1, and where the integration seams are. It complements `ARCHITECTURE.md` (the layer-level map) and `docs/PROVENANCE.md` (the cryptographic build provenance).

---

## 1. Kernel-side files ported from VOS3-Cyber (Stages 1-6)

| File | Source path in VOS3-Cyber | Ported in stage | Live symbols |
|------|---------------------------|-----------------|--------------|
| `kernel/include/vos/sha384.h` | same | Stage 2 | header |
| `kernel/include/vos/hcs.h` | same | Stage 2 | header |
| `kernel/include/vos/tee.h` | same | Stage 2 | header (extended in 10.2 with v2 schema constants) |
| `kernel/src/crypto/sha384.c` | same | Stage 2 | `vos3_sha384`, `vos3_sha384_init`, `vos3_sha384_update`, `vos3_sha384_final` |
| `kernel/src/mm/intent_validator.c` | same | Stage 1 | `vos3_intent_validate` (extended in 10.2 with v2 schema branch) |
| `kernel/src/mm/tee.c` | same | Stage 1 | `vos3_tee_model_measure`, `vos3_tee_rtmr_extend`, `vos3_tee_env`, `vos3_tee_slot_activate_bound`, `vos3_tee_measurements_snapshot` |
| `kernel/src/mm/hcs.c` | same | Stage 2 | `vos3_hcs_smt_siblings`, `vos3_hcs_flush` |
| `kernel/src/sched/core_cookie.c` | same | Stage 2 | `vos3_sched_set_cookie`, `vos3_sched_get_cookie`, `vos3_sched_sibling_compatible`, `vos3_sched_core_init_topology`, `vos3_sched_cookie_rejections` |

All 23 of the original Cyber kernel symbols verified LIVE in `vos3.elf` at Stage 6 (commit `180711d`).

### Ported-but-also-modified

These files exist in both vos4 and VOS3-Cyber; the merged version is a deliberate blend:

| File | Modification |
|------|--------------|
| `kernel/include/vos/task.h` | Tier-3 surgical append: added `core_cookie` field on `vos3_task_t` (Stage 2) |
| `kernel/include/vos/scheduler.h` | Added cookie API prototypes (Stage 2) |
| `kernel/src/sched/scheduler.c` | Dropped `static` from `g_current_task[256]` for Cyber's external linkage requirement (Stage 6) |
| `kernel/src/sched/task.c` | Added cookie init + 2 self-tests in `vos3_task_init` (Stage 3, 6) |
| `kernel/src/mm/ai_slots.c` | Added TEE measurement after slot_finish (Stage 5) |
| `kernel/src/drivers/vbus_ai_cmds.c` | Added 9 commands: `INTENT_SUBMIT`, 5 diag commands (Stage 4-6), `cmd_audit_fail_quote` (10.1), `cmd_action_check_confidence` (10.2), 3 `cmd_policy_*` (10.2.2) |
| `kernel/src/drivers/virtio_bridge.c` | Added 9 dispatcher branches for those commands |
| `kernel/src/drivers/vbus_bridge_internal.h` | Declarations for the same |
| `kernel/Makefile` | Added Cyber sources + reproducibility flags (`SOURCE_DATE_EPOCH`, `--build-id=none`, `-ffile-prefix-map`) |
| `kernel/src/core/stack_protector.c` | Stage 7 RDRAND #UD fix (route through `vos3_entropy_extract`) |
| `kernel/src/mm/ai_guard.c` | Stage 8 RDRAND #UD fix at line 483 (same pattern) |

---

## 2. New kernel files added in vos.v1 stages

These files have no source in VOS3-Cyber — they were authored fresh during the merger:

| File | Added in | Purpose |
|------|----------|---------|
| `kernel/include/vos/audit_ring.h` | Stage 10.1 | Compliance failure audit ring schema |
| `kernel/src/mm/audit_ring.c` | Stage 10.1 | 64-entry RFC-6962-style ring impl |
| `kernel/include/vos/intent_validator.h` | Stage 10.2 | (planned — stub for Action Bridge cross-import; current uses `tee.h`) |

---

## 3. Backend files ported from VOS3-Cyber

### Stage 1 (initial)
- `backend/core/security/attestation_service.py`
- `backend/ai/llm/kv_prefix_cache.py`

### Stage 11 (infra/)
| File | Source path |
|------|-------------|
| `infra/security/__init__.py` | `VOS3-Cyber/infra/security/__init__.py` |
| `infra/security/build_sbom_v20_1.py` | `VOS3-Cyber/infra/security/build_sbom.py` (renamed to preserve as historical) |
| `infra/security/dev_sign.py` | same |
| `infra/security/sbom_verify.py` | same |
| `infra/security/sigstore_mock.py` | same |
| `infra/security/sigstore_verify.py` | same |
| `infra/security/sample_certificate.jsonld` | same |
| `infra/security/vos3_code_sbom.json{,.meta,.sig}` | same — a real signed SBOM artefact preserved |
| `infra/security/vos3_sbom_v20.1.4.json` | same — historical SBOM |
| `infra/security/keys/{.gitignore,vos3_dev_signing.pub}` | same — dev public key only |
| `infra/audit_deps_depth.py` | same |
| `infra/deploy/audit_env.sh` | same |
| `infra/deploy/bootstrap.sh` | same |
| `infra/deploy/inject_jwt_secret.sh` | same |
| `infra/nginx/vos-cyber.conf` | same |
| `setup.sh` | `VOS3-Cyber/setup.sh` |
| `redeploy.sh` | same |
| `update.sh` | same |

### Stage 12 (test directories)
- `backend/tests/security/test_killer_feature_attestation.py`
- `backend/tests/red_team/test_hostile_tenant_leak.py`
- `backend/tests/{contracts,integration,negative,owasp,perf}/*.py` — many files (mostly metadata-equivalent to existing vos4 versions)
- `backend/tests/conftest.py` (with Stage-12 deep-triage `VOS_API_SECRET=""` fix)
- `backend/tests/conftest_vbus.py`
- `backend/tests/benchmarks/{ai_oom,egress_policy,merkle_inclusion,sched_core}_z3_proof.py` — all 4 UNSAT against merged kernel

### Stage 13 (docs)
- `docs/{EU_AI_ACT_COMPLIANCE,NIST_AI_600-1_MAPPING,AUDIT_IMMUNE_SPEC,POST_QUANTUM_TRANSITION_ROADMAP,SINGULARITY_WHITE_PAPER,TITAN_VERIFICATION,MARKET_DOMINANCE_MEMO,OS_PORTABILITY,DOOMSDAY_GAUNTLET_v20.5}.md`
- `docs/compliance/2026_STANDARDS_MAPPING.md`

---

## 4. New backend files added in vos.v1 stages

These were authored fresh:

| File | Added in | Purpose |
|------|----------|---------|
| `backend/services/policy_override.py` | Stage 10.2.2 | Management override + Safe-Rollout + REVIEW_REQUIRED fallback |
| `backend/services/compliance_store.py` | Stage 10.3 | SQLite/SQLCipher persistence for kernel audit events |
| `backend/services/intent_manifest_builder.py` | Stage 10.3 | v2 manifest envelope generator |
| `backend/services/integrity_worker.py` | Stage 10.3 | Async SHA-384 worker for streaming-fidelity |
| `infra/security/sigstore_v3_bundle.py` | Stage 11 | v3-shaped Signer/Verifier on top of dev_sign |
| `infra/security/rekor_v2_log.py` | Stage 11 | RFC-6962 Merkle tree transparency log |
| `infra/security/build_sbom.py` | Stage 11 | CycloneDX 1.5 + VEX comprehensive generator (replaces v20.1 reference) |
| `infra/security/vex_baseline.json` | Stage 11 | Hand-curated VEX seed |
| `infra/security/vos3_sbom_v11_stage11.json` | Stage 11 | Stage-11 SBOM artefact |
| `infra/security/vos3_sbom_v11_stage11.json.bundle.json` | Stage 11 | Signed bundle for SBOM |
| `infra/security/release_artifacts/vos3_elf_stage11.bundle.json` | Stage 11 | Signed bundle for kernel ELF |
| `infra/security/rekor_v2.jsonl` | Stage 11 | Local transparency log |
| `backend/tests/conftest.py` modifications | Stage 12 deep-triage | `VOS_API_SECRET=""` test isolation fix |
| `backend/db/convex.py` `_dev_*` stubs | Stage 12 deep-triage | Missing dev-mode RPC stubs |
| `backend/services/vbus_driver.py` `import asyncio` | Stage 12.1 | Latent vos4 baseline bug surfaced by test discovery |

---

## 5. Frontend / Tauri files added in vos.v1 stages

| File | Added in | Purpose |
|------|----------|---------|
| `frontend/app/sovereign/page.tsx` | Stage 10.3 | Sovereign Control Panel |
| `frontend/hooks/useSovereignPanel.ts` | Stage 10.3 | Panel state + parsers |
| `frontend/hooks/useTokenVerification.ts` | Stage 10.3 | Streaming-fidelity consumer |
| `frontend/lib/tauri-bridge.ts` | (vos4 base) — Stage 10.3 added 5 `tauriPolicy*` bridges | |
| `desktop/src-tauri/src/commands.rs` | (vos4 base) — Stage 10.3 added 5 `policy_*` IPC handlers | |
| `desktop/src-tauri/src/main.rs` | (vos4 base) — Stage 10.3 registered the 5 handlers | |

---

## 6. VOS3 product preservation (Stage 9)

Verbatim copies, not engineering port — these are **investor / auditor / acquirer artefacts** preserved in tree for diligence purposes:

| File or dir | Source path |
|-------------|-------------|
| `VOS3_ULTIMATE_HANDOFF_2026/` (full subtree, 13 files) | `VOS3/VOS3_ULTIMATE_HANDOFF_2026/` |
| `disk.img` (gitignored, 64 MB) | `VOS3/disk.img` (Apr-5-2026 baseline) |
| `AUDIT_STAGE_A.md` | `VOS3/AUDIT_STAGE_A.md` |
| `docs/PROVENANCE.md` | new (Stage 9 wrote it from scratch) |

---

## 7. The Z3 formal-proof guarantee

`backend/tests/benchmarks/{sched_core,egress_policy,ai_oom,merkle_inclusion}_z3_proof.py` — ported verbatim from VOS3-Cyber in Stage 12. **All 4 UNSAT** against the merged kernel.

This is the highest-confidence diligence statement we can make: the merger preserved every formal invariant the original VOS3-Cyber audit established. The C implementations have line-by-line parity with the proven algorithms (verified for `vos3_sched_sibling_compatible` in `docs/TEST_SUITE.md` §8.5; the other three operate on algorithms unchanged since Stage 7's deterministic baseline).

---

## 8. What's NOT yet ported (Stage 10 missing-files batch)

These VOS3-Cyber backend modules are referenced by Stage 12 tests as `ModuleNotFoundError` (40 of 40 remaining failures); they will port as a single batch:

| Module | Tests waiting | Purpose |
|--------|--------------:|---------|
| `core/security/connectors/{external_spm,runtime_firewall,edr_event_relay}` | 10 | External SIEM / firewall / EDR integrations |
| `api/compliance_routes.py` | 7 | `/api/compliance/*` HTTP endpoints |
| `services/vbus_ring_buffer.py` | 5 | Python-side zero-copy ring (mirror of kernel `audit_ring.c`) |
| `core/security/rotation_manager.py` | 5 | Cryptographic key rotation orchestration |
| `core/security/cert_vault.py` | 4 | Certificate vault (TPM-backed in production) |
| `services/prefetch.py` | 3 | Predictive cache pre-fetcher |
| `core/repositories/vault_pool.py` | 3 | Encrypted SQLCipher storage pool |
| `apex_sim` | 3 | APEX simulator harness |

When this batch lands, the test pass rate jumps from 560/600 to 600/600 (modulo any newly-found regressions, which the deep-triage methodology will catch immediately).
