# vOS v1.3-GA — Product Requirements Document & Launch Readiness Guide

**Status:** release-candidate baseline · **Date:** 2026-06-08
**Branch/HEAD:** `feat-m3-ed25519-verify` @ `72ebe6a`
**Document type:** ground-truth product/engineering record (analytical synthesis; no source changes)

> ## Accuracy & Representations (read first)
> This PRD is a record of fact, so it deliberately corrects four framings that
> appeared in the drafting brief and would be inaccurate if published:
> 1. **Moat is `49/80` (≈61%), not "49/49 local."** The "/49" denominator is a
>    re-scoping that overstates completeness; the honest, ledger-backed figure
>    is 49 of 80 catalogued problems.
> 2. **The loopback network policy is a CI/test-harness determinism control, not
>    a production runtime guarantee.** Production sovereignty is enforced by the
>    routing/egress layer (local-titan 503, outbound-PII shield, regional
>    policy), which must be evaluated on its own merits.
> 3. **EU AI Act Article 73:** vOS is *designed toward* the transparency
>    obligations and publishes the artifacts to support an audit; it does not
>    "guarantee full compliance" (a legal determination, not an engineering one).
> 4. **M3 SecureBoot is implemented + booted-QEMU-verified, but NOT a closed
>    moat row.** It stays uncounted (moat 49/80) pending the mandatory external
>    crypto audit (Phase 6). Every metric below is reproducible against `72ebe6a`.

---

## Section 1 — Product Vision & Sovereignty Mandate

**Vision.** vOS is an agent-era operating system whose differentiator is
**auditability + sovereign (local-first) execution** for regulated AI-agent
deployment. The market thesis is transparency, not omnipotence: an 80-problem
agent-OS catalog with an honest, per-row, tier-1-sourced status (solved /
tracked-upstream / hardware-gated / research-open), plus a per-release
attestation chain.

**Core value proposition (accurate form).**
- **Local-first sovereign routing:** non-critical agent work runs on a local
  model lane (Ollama / kernel-resident); a privacy-mandate request that cannot
  run locally **fails closed with HTTP 503** rather than silently egressing to
  non-sovereign cloud.
- **Auditability:** structured decision/audit records, a signed kernel + Sigstore
  bundle, and a published problem catalog + roadmap.

**EU AI Act Article 73 posture (designed-toward, not guaranteed).** vOS provides
the *technical evidence base* an Article-73 filing needs — `docs/EU_AI_ACT_COMPLIANCE.md`,
the 80-problem catalog + roadmap, the per-release attestation chain, and the
regional-policy enforcement (`backend/services/regional_policy.py`, fail-closed
`ComplianceDenied` when no sovereign path exists). Final compliance is an
operator + counsel determination; vOS supplies controls and audit trails, not a
legal guarantee.

---

## Section 2 — Two-Tier Architecture & Feature Matrix

### Tier 1 — Local Edge (shipped)
- **Moat:** **49 of 80** catalogued agent-OS problems have a vOS fail-closed
  enforcement posture (ledger: `SESSION_HANDOVER_LOCK.md §4`). The
  *software-tractable* backlog is exhausted at this figure; the remaining 31 are
  hardware/research/upstream (Tier 2).
- **Core release gate:** `backend/tests/security/` + `backend/tests/services/`
  = **931 passed / 0 failed** (49 test files; auth dataclass, CSRF firewall,
  SSRF/DNS-pinning, compliance-store crypto, and the fail-closed security gates).
  Broad consumer sweep (gate + router/dispatch/chat/titan/convex) = **1,097 passed**.
- **Sovereign Titan routing (realized):** `src.efficiency.router.get_optimal_model`
  routes non-critical roles to `local-titan` (Ollama) under
  `VOS3_DEFAULT_LOCAL_FIRST` when reachable; a privacy mandate forces all roles
  local; `services.agent_orchestration.orchestrate_pre_flight` raises **503 +
  `Retry-After: 30`** (body explicitly forbids cloud fallback) when local is
  required but unreachable. Verified by `test_titan_agent_loop` (8/8).
  *Maturity note: this is the routing/decision + health-gate layer wired to
  Ollama — not a managed model-hosting product.*

### Tier 2 — Enterprise Cloud Enclave (roadmap; OPEN/REQUIRED, not shipped)
Source: `docs/release_notes/vOS_Cloud_Enclave_Sovereign_Spec.md`. These rows
**cannot be closed in software** and are the defined scope of a hardware-backed
tier (procurement Q1–Q2 2027).

| Class | Rows | Hardware/owner dependency |
|---|---|---|
| Silicon / confidential-compute | D3, D7, E4, J2, J4, K1, L2, O2, O4 | Intel TDX 2.0 · NVIDIA Blackwell-CC · TPM 2.0 · CXL 3.0 tiering |
| Upstream-tracked | C3, C5, L1, L3, N2, O1, D2/D6-residual | vendor/distro/standards delivery |
| Research-only | A5, C1, C4, E5, I4, I6, K3, P1 | no production fix exists anywhere |
| Software tail | M3 | in-kernel crypto (see §3) — only software-closable open row |

**Honest valuation note:** Tier 2 is a *defensible, hardware-gated roadmap*, not
inherited working capability. It must be valued as roadmap + documented
architecture, not as completed features.

---

## Section 3 — M3 Model-SecureBoot (cryptographic specification)

**Requirement.** Verify `.gguf`/`.safetensors` model signatures (OpenSSF Model
Signing scheme: **Ed25519 over SHA-256(model)**) at slot ingestion, fail-closed,
before weights are activated.

**Pipeline (implemented).**
1. **Build-time trust anchor** — `kernel/include/vos/vvfs_trusted_key.h`. The
   Ed25519 public key is baked at build (Linux `.builtin_trusted_keys` model);
   **deliberately NOT settable over runtime VBus** (a runtime anchor swap would
   let an attacker self-sign and bypass the gate). Default = unprovisioned →
   fail-closed.
2. **OMS signature transport** — `cmd_model_sig_register` (`MODEL_SIG|slot|digest|sig|signer`)
   in `vbus_ai_cmds.c`, bounded hex parsing; malformed input → fail-closed.
3. **Verify at SLOT_FINISH** — `cmd_slot_finish` computes `SHA-256` over the
   loaded model bytes `[info.base, info.size)` and calls
   `vvfs_verify_model_slot` (`vvfs_model_verify.c`): signature present? signer ==
   build anchor (constant-time)? registered digest == computed digest? Ed25519
   verify over the digest? Verdict cached; the read-path gate
   (`vvfs_transport.c`, gated by `VOS3_VVFS_REQUIRE_MODEL_SIG`, **default-OFF**)
   admits only verified slots.
4. **Crypto core** — freestanding verify-only Ed25519 (RFC 8032, TweetNaCl
   `crypto_sign_open` port) + SHA-512 (`kernel/src/crypto/`). Hardened with the
   canonical **`S < L`** scalar check (**neutralizes CVE-2026-4115 malleability**,
   proven), plus non-canonical / small-order point rejection (CVE-2025-15444
   class), per *Taming the many EdDSAs*.

**Verification state (reproducible).**
- Host KAT `kernel/tests/ed25519_kat.c` — **21/21** (RFC 8032 + malleability +
  OMS interop).
- Host E2E `kernel/tests/m3_e2e_test.c` — **7/7**.
- **Booted-kernel QEMU** assertion run — **67/67 cert points, 0 FAIL**, with the
  7 M3 enforcement certs (120–126) PASS in the actual booted kernel; evidence:
  `kernel/tests/results/m3_phase5_qemu_certs.txt`.

**Product status:** software complete + self-verified; **row M3 remains OPEN
(moat 49/80)** until the Phase-6 external cryptanalysis audit signs off. From-
scratch/ported kernel crypto earns its moat row through independent review, not
self-attestation.

---

## Section 4 — Technical Debt Registry & v1.3.1 trajectory

**Reliable measurement = sequential** (`-p no:xdist`). Last clean baseline:
**~54–61 residual** broad-suite failures (the figure has moved as fixes landed;
the parallel/xdist path retains a separate worker-crash flake — run sequentially
or with reduced `-n`). Ledger of record: `backend/tests/handover_quality_debt.json`.

| Cluster | Category | v1.3.1 resolution path |
|---|---|---|
| `smart_routing`, `efficiency` | **RESOLVED** (665ef16) | fallback raise-on-unknown-role + mock-target correctness; `smart_router` dep still recommended for full SmartRouter path |
| `open_core_split` ×3 (pmm / Makefile / license_check) | **RESOLVED** (665ef16) | pro-gated hugepage pool, CORE build branch, open-core charter |
| `finetune_engine` relocation ×2 | **DEFERRED — product/licensing decision** | tests demand moving the 457-line engine from `services/` (open) to `pro/` (proprietary); the module's own charter says "the training engine itself is CORE" and the header marks the split **PROPOSED, pending legal review**. Requires a human open-vs-proprietary ruling, not an autonomous edit. |
| `memory_scaling`, `vmm_security`, `kim_inference_caps`, `finetune_rigor` | kernel-source asserts | green only by making the kernel source true (real kernel work) or confirming the test is stale |
| `api_billing` | real product behavior (503 needs DB) | finish dev-mode credit support or gate test behind a DB fixture — not a mock-to-green |
| `owasp_rate_limit`, `dns_pinning`, `hostile_tenant_leak`, `owasp_bola` | **security/red-team — diligence-critical** | triage as real (some may be contamination — dns_pinning passes in isolation); **never force-green** |
| `mcp_bridge`, `full_stack_integration`, `terminal*`, `v32_stability` | app/integration/config | per-cluster triage |

**Operating limits (honest):** the shipped local tier is sound (core gate green,
sovereign routing real, M3 verified-but-unaudited). The residual is **mapped,
not zero** — and the security-suite + kernel-source failures are real signal that
must be resolved on their merits before any "fully green" claim.

---

## Section 5 — Launch Readiness Checklist (to lift the GA freeze)

| # | Gate | State |
|---|---|---|
| a | Sequential pre-flight: `pytest backend/tests/ -p no:xdist` (broad) + `kernel/tests/ed25519_kat.c` + `m3_e2e_test.c` (28 host) + QEMU cert run (67) | core gate 931 ✅; broad suite has documented residual (§4); host crypto 28/28 ✅; QEMU 67/67 ✅ |
| b | **PR #17 → Phase-6 independent external cryptanalysis audit** of the ported Ed25519 | **REQUIRED, not done** — blocks M3 → 50/80 |
| c | Loopback/egress: confirm tests are hermetic (CI determinism) | ✅ verified (external connect fast-fails); note: this is a *test* control, not the production egress guarantee |
| d | Resolve/explain failing **security/red-team suites** (owasp, dns-pinning, hostile-tenant, bola) | **REQUIRED** for a security product before GA |
| e | Triage kernel-source assertion failures (memory_scaling/vmm/kim) | confirm the asserted guards exist in the shipped kernel |
| f | Independent `vos3.elf` rebuild + Sigstore/Rekor re-verify | acquirer/operator-side |
| g | finetune open-vs-proprietary **licensing decision** (legal review per the file's PROPOSED marker) | human decision pending |

**Go/No-Go (honest):** the **local edge tier is releasable as a transparent,
auditable RC** — with the explicit caveats that (b) M3 is unaudited, (d) some
security suites are failing pending triage, and (g) the finetune licensing split
is unresolved. A "100% complete / fully green / 80/80" claim is **not** supported
and must not be made. Moat: **49/80**, verified and frozen.

---

*Every figure in this PRD is reproducible against commit `72ebe6a`. Where a
capability is partial, scaffolded, default-off, or unaudited, it is labelled as
such — the absence of overclaim is itself a deliberate quality of this record.*
