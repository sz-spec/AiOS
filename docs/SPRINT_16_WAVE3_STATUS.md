# Sprint 16 Wave 3 — Live Status Dashboard

**Branch:** `sprint-16-wave3` (forked from `main@859650d`)
**Window:** 2026-05-24 → 2026-09-01
**Predecessor verification:** Sprint 15+16 W1+W2 cumulative 402/402 tests pass on main

## Live status

| # | Item | Severity | Target | Status | Tests |
|---|---|---|---|---|---|
| 1 | **A4** TLB side-channel mitigation (page coloring) | 🟠 | `backend/services/page_coloring_governor.py` | ✅ **DONE** (25/25) | — |
| 2 | **B3** eBPF LSM per-call capability gating | 🟠 | `backend/security/lsm_cap_gate.py` | ✅ **DONE** (23/23) | — |
| 3 | **B4** Policy DSL compiler | 🟠 | `backend/security/policy_compiler.py` | ✅ **DONE** (21/21) | — |
| 4 | **B5** App-store scope → kernel allowlist | 🟠 | `backend/security/scope_kernel_gate.py` | ✅ **DONE** (18/18) | — |
| 5 | **D1** TDX composite-policy attestation | 🟠 | `backend/services/attestation_composite_policy.py` | ✅ **DONE** (18/18) | — |
| 6 | **E7** NVIDIA MIG QoS via DRA | 🟠 | `infra/k8s/sandboxes/dra-gpu-mig-class.yaml`, `backend/services/dra_mig_validator.py` | ✅ **DONE** (11/11) | — |
| 7 | **H1** Combined egress policy (DNS-pin + taint) | 🟠 | `backend/security/egress_policy_combined.py` | ✅ **DONE** (17/17) | — |
| 8 | **K5** fscrypt-required model weights policy | 🟠 | `backend/security/fscrypt_policy.py`, `kernel/include/vos/fscrypt_required.h` | ✅ **DONE** (22/22) | — |
| F | Final integration + PR | — | repo-wide | ✅ **DONE** (557/557 cumulative) | — |

## Closure tracker

After Wave 3 merge: **37 of 80** catalog problems closed (46%). **155 new tests in Wave 3.**

## Cross-cutting dependencies

- B3 → B4 → B5 (B4 compiles to B3 rules; B5 OAuth scopes → B3 capability checks)
- D1 extends Sprint 15 attestation_service.py + cross-links D5 (Wave 1 nonce gate)
- H1 combines Sprint 15 H4 (DNS pin in runtime_firewall) + Wave 2 C7 (taint engine)
- K5 cross-links A2 (KV-cache OS primitive) for model_loader integration
- E7 cross-links C8 (K8s Sandbox CRD shipped Sprint 15)
