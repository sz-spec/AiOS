# vOS Master Development Plan — Sovereign Agent Edition

**Saved:** 2026-05-29 (session pause + handover to next holder)
**Authoritative continuity doc:** `infra/persistence/active_context/SESSION_HANDOVER_LOCK.md` (read FIRST; this plan is the strategic companion)
**Current version state:** `v1.2.0-rc` tagged at `main` `d45fca6`; `main` HEAD now `2502fd5` (3 commits ahead of the RC tag: E3+E6 moat expansion + Sprint 19 plan/ledger)
**Branch:** `main` (clean + synced with `origin/main`); `sprint-17-wave1` fully merged
**Holder transitioning out:** Claude Opus 4.7 (1M context) for sz@aidg.com
**Next holder:** Opus 4.8

> This document is the SINGLE forward-looking plan. It consolidates
> everything completed, paused, and pending so the next session resumes
> with zero re-derivation. It does not replace the handover lock (the
> hash/TTL/operational source of truth) — it sits beside it.

---

## 1. Status snapshot

| Axis | State |
|---|---|
| Release | **v1.2 Release Candidate SEALED** (`v1.2.0-rc` @ `d45fca6`) |
| GA target | 2026-07-15 (ahead of EU AI Act Art. 73 enforcement 2026-08-02) |
| Strategic moat | **40 / 80 catalog problems closed (50%)** — honest, no padding (Sprint 19 W2: E3/E6 operationalized + O3 shipped) |
| Cumulative tests | **817 declared (788 + O3 20 + E3/E6 wiring 9)**; local hard-gate `security/`+`services/` = **694 passed / 0 failed / 8 skipped** (was 611/43/1-error pre-Wave-2 — baseline debt cleared) |
| Active dev | Sprint 19 Wave 2 **SHIPPED locally (uncommitted)** — see handover lock ⚠️ |
| Dev host | macOS (Darwin arm64) — **cannot** run the Linux/eBPF GA steps |

---

## 2. Completed work (Sprints 15 → 19 Wave 1)

### v1.1-GA (Sprint 15) — 18/80 (23%)
Baseline Sovereign Agent Edition. Tagged `v1.1.0-ga` @ `7f403c3`.

### Sprint 16 (Waves 1-3) — → 37/80 (46%)
19 medium-term items: A2/A3/A4 memory, B3/B4/B5 capability/LSM, C7 IFC
engine, D1/D5 TEE, E7 DRA+MIG, F4/F5 identity, G2/G4 audit, H1/H3
egress, J1/J3 scheduler, K5 fscrypt.

### Sprint 17 + 18 (v1.2-RC) — 37/80 (46%, DEEPENED not widened)
The **v1.2 Triple-Threat** — existing C7 + F4 promoted to full-stack:

- **[B] Identity Federation (SPIFFE/AIMS)** — F1+F4+F5
  - `backend/services/identity_federation_bridge.py` (B.1: trust-bundle sync, atomic rotation, sequence monotonicity, pin-SHA256)
  - `backend/services/aims_partner_verifier.py` (B.2: cross-domain Ed25519 AIMS envelope verify)
- **[C] Byte-Level IFC (eBPF Kernel Gate)** — C7
  - `backend/security/taint_engine_v2.py` (C-1 per-byte engine)
  - `backend/ai/agents/dual_llm_router.py` (C-1 chokepoint integration)
  - `kernel/src/sec/taint_gate.c` + `kernel/include/vos/taint_maps.h` + `backend/security/kernel_gate_connector.py` (C-2 LSM write-gate: prototype → LIVE bpf() binding → per-byte bpf_loop EchoLeak defense)
  - `kernel/bpf/Makefile` (clang -target bpf build/load)
- **[C-3] Cryptographic Declassification (Ed25519)** — C7 ext
  - `DeclassEvidence` in `taint_engine_v2.py` (signed downgrade, SHA-256 byte-binding, 5-min window)

Sprint plans: `docs/SPRINT_17_PLAN.md`, `docs/SPRINT_18_PLAN.md`. Status: `docs/SPRINT_17_STATUS.md`. Specs: `docs/CLUSTER_B_FEDERATION_SPEC.md`, `docs/CLUSTER_C_BYTE_LEVEL_IFC_SPEC.md`. Notes: `docs/V1.2_RELEASE_NOTES.md`.

### Sprint 19 Wave 1 (moat expansion) — → 39/80 (≈49%)
Two **NEW** closures via genuine fail-closed enforcement (commit `4f5aaa0`):
- **E3** `backend/security/iommu_dma_guard.py` — refuses GPU slot bind unless IOMMU provably ENFORCING.
- **E6** `backend/security/perf_counter_lockdown.py` — refuses multi-tenant inference unless perf counters locked (`perf_event_paranoid≥2` + NVIDIA admin-only profiling).
Plan: `docs/SPRINT_19_PLAN.md`.

---

## 3. Pending — v1.2 GA gate (operator/hardware; NOT doable from macOS)

These are the **only** things between RC and GA. None can be executed
or faked from the current macOS dev host — they require a Linux ≥ 5.17
runner and/or operator billing action.

1. **Activate the eBPF LSM gate LIVE** — on a Linux ≥ 5.17 host with `clang`/`libbpf`/`bpftool` + bpffs mounted:
   ```
   cd kernel/bpf && make && sudo make load
   sudo bpftool map show pinned /sys/fs/bpf/vos3/taint_colors   # verify
   ```
   The Python `KernelGateConnector` auto-detects the pinned map and flips MOCK → LIVE (`-EPERM` enforced). The 6 skipped LIVE eBPF tests then execute.
2. **Sigstore re-seal of the kernel ELF** — current `kernel/build/vos3.elf` SHA diverges from the `v1.1.0-ga` bundle (expected, from Sprint 16+17 kernel-source additions). Rebuild from `main` + re-sign so the published bundle matches. (Handover lock §1.2 + rule #1.)
3. **Restore GitHub Actions billing** — org `sz-spec` Actions minutes exhausted (all CI jobs fail in <3s, `BlobNotFound`). The `constraints.txt` fix is already on `main`, so once billing returns, `main` CI should green WITHOUT PR #6. PR #6 is then closable as **superseded** (verify `git log main..ci-cleanup` first — only `02230b8`, already on main). (Handover lock §6 rules #7-8.)

---

## 4. Sprint 19 Wave 2 (software; doable from macOS) — ✅ SHIPPED 2026-06-01

Honest **40/80 = 50%** reached + operationalization loop closed (Opus 4.8):

1. ✅ **E3 wired into the GPU slot-bind path** — `model_manager.load_to_kernel()` now calls `IommuDmaGuard.require_iommu_for_gpu_bind()` after the VBus connect and BEFORE the DMA warp transfer. Fail-closed (refuses the bind on an unverifiable host); gated by `VOS3_DISABLE_IOMMU_GUARD` for mock-driver environments. (`backend/services/model_manager.py`)
2. ✅ **E6 wired into the inference-launch path** — `kernel_provider.KernelProvider.astream()` now calls `PerfCounterLockdown.require_lockdown_for_multitenant_inference()` before `KIM_GENERATE`. Fail-closed; `VOS3_PERF_SINGLE_TENANT` / `VOS3_DISABLE_PERF_GUARD` escape hatches. (`backend/ai/llm/kernel_provider.py`)
   *(Items 1-2 are the hello-world→operationalized arc, exactly like C-1's DualLLMRouter wiring. Proven by `backend/tests/security/test_e3_e6_wiring.py` — 9 tests showing the gates fire through the real call paths.)*
3. ✅ **O3 — Context-Aware Outbound-PII Shield** (the honest 40th item; the operator redefined O3 from the earlier noisy-neighbor-admission framing to a fail-closed *outbound-PII* gate). `backend/security/outbound_pii_shield.py` refuses an outbound send carrying structured PII (email/phone/SSN/credit-card/national-ID) to an untrusted destination, but ALLOWS the same payload to an operator-declared trusted destination or under an explicit kind-scoped `ReleaseContext`. Reuses the canonical `tools/log_pii_scan.py` patterns (single source of truth with the Annex IV log gate). 20 tests in `backend/tests/security/test_outbound_pii_shield.py`. Honest scope: structured identifiers only — free-text NER is an O3 follow-up; cryptographic release-binding is the C-3 Ed25519 path (Sprint 20).

**Moat: 39 → 40/80 (50%).** Catalog `docs/AGENT_ERA_OS_PROBLEMS.md` O3 row flips to ✅.

### Baseline test-debt cleared the same turn (operator-approved)
The local hard-gate's broad selector (`pytest backend/tests/security/ backend/tests/services/`) carried **44 pre-existing failures unrelated to the moat work** — cleared so the "800+ green" milestone is real, not asterisked:
- `test_killer_feature_attestation.py` (43) — TDD stubs whose implementations had drifted/were absent. Implemented to contract: `RotationManager` (rotation_manager.py), cert-vault `VaultPool` + `VaultStats` (vault_pool.py; generic pool renamed `ConnectionVaultPool`), `ZeroCopyRingBuffer` (vbus_ring_buffer.py), AI-BOM builder + `SpmConnector`/`WizConnector` aliases (external_spm.py), `RuntimeFirewallAdapter` + `VOS3KernelEvent` + `PrismaAdapter` (runtime_firewall.py), `relay_kernel`/`relay_attestation` + `customer_id` + `FalconRelay` (edr_event_relay.py), connectors `__init__` re-exports, `CertificateVault` singleton (cert_vault.py), `_enforce_session_ownership` IDOR gate (compliance_routes.py), `_warm_one` path guard (prefetch.py), `backend/scripts/apex_sim.py`. z3-solver added to requirements for the merkle UNSAT proof.
- `test_log_pii_scan.py` (1 error) — `backend/tools/` regular-package shadowed the repo-root namespace `tools`; fixed with a re-export shim `backend/tools/log_pii_scan.py`.
- `test_wasm_sandbox.py` (2) — wasmtime 44 API drift (`add_fuel`→`set_fuel`, `fuel_consumed()`→`get_fuel()`, `StoreLimitsBuilder`→`set_limits(memory_size=…)`); made version-robust in `backend/sandbox/wasm/runner.py`.

---

## 5. Backlog — catalog partition (the remaining 41 of 80)

| Bucket | Count | Disposition |
|---|---|---|
| **Research-only (uncloseable)** | 8 | A5 Rowhammer, C1/C4 prompt-injection fundamentals, E5 Energon (physics), I4 poisoning, I6 HF config, K3 fsync, P1 standards. **Do NOT attempt** — physics/ML-architecture limits no vendor solves. This IS the investor-facing TAM + the "we're honest about what's unfixable" positioning. |
| **Hardware/vendor-bound** | 14 | A3 CHERI, A4, D1/D3/D7, E4/E7, J2/J3/J5, K1/K2, L2, O2. Need silicon (Morello/Blackwell/TDX) or vendor roadmaps. Cluster A — Q1-Q2 2027 procurement (~$25k gate), post EU AI Act 73. |
| **Track-upstream (enforceable)** | ~18 | The only honest software path to NEW closures: ship fail-closed enforcement that refuses the unsafe op when the upstream fix isn't active (the E3/E6 pattern). Remaining candidates: O3, and a few others — pick by genuine-enforcement value, never by count. |

---

## 6. Next-session action list for Opus 4.8 (ordered)

**If a Linux ≥ 5.17 runner is available** → execute §3 GA steps (eBPF load → re-sign → cut `v1.2.0-ga` tag). This is the highest-value path; it converts RC → GA.

**If still macOS-only** → execute §4 Sprint 19 Wave 2 (wire E3/E6 into live paths → O3 admission gate → honest 40/80 = 50%). Pure software, fully doable.

**Either way, on resume:**
1. Read `infra/persistence/active_context/SESSION_HANDOVER_LOCK.md` (TTL 2026-07-15 — re-verify claims if elapsed).
2. Run the hard-gate proxy: `backend/.venv_p312/bin/python -m pytest backend/tests/security/ backend/tests/services/ -q` (last green: 161 passed + 7 skipped on the Cluster B/C + E3/E6 subset).
3. Check if billing returned: `gh run list --branch main --limit 1`.

---

## 7. Honesty constraints carried forward (DO NOT violate)

These were sealed in v1.2 and re-affirmed in Sprint 19. The next holder
inherits them:

1. **Do NOT inflate the moat count.** 39/80 is honest. Only NEW closures via genuine fail-closed enforcement (refuse the unsafe op) count — never runbooks-relabeled-as-closed. The v1.2 seal explicitly chose depth over breadth.
2. **Do NOT fake the Linux/eBPF GA steps.** They are impossible from macOS. Report blocked; never simulate a load/re-sign that didn't happen.
3. **Local pytest is the release hard-gate** until Actions billing returns. Black/bandit/pip-audit are continue-on-error.
4. **Don't cut a GA tag without rebuilding the kernel + re-signing** (handover lock rule #1).
5. **Shared-state actions** (merging/closing PRs, force-push, cutting tags) — confirm with the operator first unless pre-authorized.
6. The **8 research-only items stay open by design.** Transparency about the unfixable is the positioning, not a gap to paper over.

---

*Session paused 2026-05-29 by operator request. State is fully durable
on `origin/main` @ `2502fd5`. Resume via §6.*
