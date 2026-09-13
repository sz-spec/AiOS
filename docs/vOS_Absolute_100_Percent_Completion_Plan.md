# vOS 1.3 — Completion & Hardening Roadmap (honest scope)

**Prepared:** 2026-06-07 · **Branch:** `feat-m3-ed25519-verify` @ `3d0f4ef`
**Threat-intel window:** May–June 2026 (live web search; sources in §6)

> ## ⚠️ Reality check (read before quoting any number)
> The requesting brief asked for a "100/100 / 100%-complete / 0-failed /
> valuation-maximizing" manifest. **That target is not achievable and this
> document does not claim it.** Specifically:
> - **A literal "100/80 moat" is impossible.** 31 of the 80 catalog rows are
>   gated on hardware (Intel TDX 2.0 / NVIDIA Blackwell-CC / TPM / CXL),
>   upstream vendors, or open research — they **cannot** be closed in software
>   on any host. "100%" here means *100% of the software-tractable scope*, with
>   an honest, hardware/research-gated roadmap for the rest.
> - **"0 failed application tests" will not be engineered by fiat.** Several of
>   the 54 residual failures are real product/kernel/security state (e.g.
>   failing OWASP/red-team suites, billing's real DB requirement). They are
>   fixed where the cause is genuine and **reported, not hidden**, where it is
>   not.
> - **Honest milestone targets:** core gate stays **931/931**; broad sequential
>   suite driven down by *root-cause* fixes (currently 54); **M3 → 50/80 only
>   after external crypto audit** (§2). The moat is **49/80 today**.

---

## 1. May–June 2026 threat intel → Phase 2 strict-verify criteria

The web sweep surfaced findings that **directly implicate the Ed25519 verifier
shipped in Phase 1** (commit `3d0f4ef`). This is the single most important
output of this document.

### 1.1 CONFIRMED gap in our own code — signature malleability (CVE-2026-4115 class)
- **Finding:** PuTTY **CVE-2026-4115** — `eddsa_verify` accepted `S ≥ L`
  (over-large scalar), enabling RFC-8032 signature **malleability** (alternative
  valid signatures for the same message).
- **Our exposure (proven, not theoretical):** the Phase-1 verifier is a faithful
  TweetNaCl `crypto_sign_open` port, and **TweetNaCl does not perform the
  canonical `S < L` check**. We demonstrated locally that
  `vos3_ed25519_verify` **accepts `S' = S + L`** for RFC-8032 TEST 2 — i.e. our
  code is currently malleable. It is RFC-8032-KAT-correct for *honest* inputs
  but not yet malleability-hardened.
- **Phase 2 fix (mandatory before M3 closes):** before using `S`, reject any
  signature whose `S` is not canonical (`S < L`). Add a constant-time
  `s_lt_L(sig+32)` gate at the top of `vos3_ed25519_verify`. Add a KAT that the
  `S+L` malleated signature is **rejected**.

### 1.2 Elliptic-curve point validation (CVE-2025-15444 class)
- **Finding:** libsodium **CVE-2025-15444** — improper EC point validation
  (checked `X==0` after order-mul but not `Y==Z`) admitted small-subgroup points
  (Ed25519 cofactor = 8).
- **Phase 2 fix:** add explicit **non-canonical encoding rejection** for `R` and
  `A` (point coords ≥ p) and **small-order point rejection**, per
  *"Taming the many EdDSAs"* (eprint 2020/1244). Decide and document
  cofactored vs cofactorless verification (our port is cofactorless-style; the
  strict criterion set must be chosen deliberately and KAT'd against the
  paper's edge-case vectors).

### 1.3 Kernel-crypto cautionary context (not vOS-exploitable, but informs review)
- **CVE-2026-31431 "Copy Fail"** (exploited in the wild): logic bug in the Linux
  `algif_aead`/`authencesn` **in-place** crypto path → controlled 4-byte write.
  vOS does not use `algif_aead`, but it is a direct warning for our review gate:
  **audit every in-place buffer reuse in the crypto + vvfs paths.**
- **CVE-2026-43493** (pcrypt `MAY_BACKLOG` mishandling) and **CVE-2026-46333**
  (`__ptrace_may_access` local root) — general May-2026 kernel hardening context
  for the platform's CVE-watch.

### 1.4 TDX 2.0 / Blackwell-CC
- No specific public confidential-compute leakage advisory surfaced in this
  sweep (results were NVIDIA financial filings). **Action:** subscribe to Intel
  PSIRT + NVIDIA PSIRT feeds; do not assume "clean" — track continuously. This
  gates the §3 hardware tier, not the local GA.

**Phase-2 acceptance:** the verifier rejects `S ≥ L`, non-canonical `R`/`A`, and
small-order points; KATs include the malleability + "Taming" edge vectors; all
prior 13 KATs still pass.

---

## 2. M3 — Phase 4/5/6 execution (path to 50/80, honestly gated)

### Phase 4 — wire into the vvfs ingestion path
- Replace the per-read scaffold call with a **once-at-SLOT_START** verification
  in the ingestion path (not per `vvfs_read_block`).
- Flow: read full model bytes for the slot → `SHA-256(model)` (kernel has it) →
  compare to OMS bundle `digest.sha256` → `vos3_ed25519_verify(sig, digest, 32,
  trusted_pubkey)` → on any failure return `VOS3_FS_ERR_KEYREJECTED` (fail
  closed). Parse the OMS `.sig`/`.bundle.json` from the slot.
- Gate with `VOS3_VVFS_REQUIRE_MODEL_SIG`: default-ON for **fortress** profile,
  default-off community (staged), documented dev-override.
- Trusted pubkey provisioning (Phase 3 of the original plan): embed at build
  (Linux `.builtin_trusted_keys` model) + rotation via `cert_vault`/
  `rotation_manager`; support key-set + blacklist/revocation.

### Phase 5 — verification matrix (local, no inflation)
Build a deterministic harness exercising the wired path:

| Case | Expected |
|------|----------|
| valid OMS sig + matching digest | accept (load) |
| forged signature | `-EKEYREJECTED` |
| `S+L` malleated signature | `-EKEYREJECTED` (Phase-2 gate) |
| tampered model bytes (digest mismatch) | `-EKEYREJECTED` |
| missing `.sig`/bundle | `-EKEYREJECTED` |
| wrong / revoked trusted key | `-EKEYREJECTED` |

Run host-KAT for the primitives (done for Phase 1) **and** a QEMU boot that
ingests signed/forged/missing models and asserts the matrix. **Only when this
matrix is green in QEMU is M3 a real runtime capability.**

### Phase 6 — external audit before claiming 50/80
- From-scratch/ported crypto **must** get independent review (the §1 findings
  prove why). Provide: the diff vs TweetNaCl, the KAT suite (RFC 8032 + Taming +
  malleability), the threat model, and the provisioning design.
- **Moat ledger:** M3 advances **49 → 50/80 only after** Phase 5 (QEMU matrix
  green) **and** Phase 6 (external sign-off). Until then it remains a
  scaffold/implementation, **not counted**. No data inflation.

---

## 3. The 31 open rows → Premium Cloud Enclave / Research tiers

Transposed from `docs/release_notes/vOS_Cloud_Enclave_Sovereign_Spec.md` (the
document of record). These are **OPEN/REQUIRED roadmap**, not shipped IP.

| Tier | Rows | Integration vector | Realizable |
|------|------|--------------------|-----------|
| **Premium Cloud Enclave** | D3, D7, E4, J2, J4, K1, L2, O2, O4 | deploy vOS guest inside a **TDX 2.0** CVM with **Blackwell-CC** GPUs; bind RTMR/attestation to the existing TEE_QUOTE path; CXL 3.0 tiering for K1 | hardware procurement Q1–Q2 2027 |
| **Upstream-tracked** | C3, C5, L1, L3, N2, O1, D2/D6-residual | ride vendor/distro/standards delivery | as upstream ships |
| **Research** | A5, C1, C4, E5, I4, I6, K3, P1 | no production fix exists | open problem (TAM, not datable) |

**Integration vectors (TDX 2.0 / Blackwell-CC), honest status:** the *design*
(map RTMRs to attestation, run inference in the CC GPU, verify quotes per
request — D5 already shipped) is specifiable now; **execution requires the
physical silicon** and is therefore roadmap, not deliverable. Live vendor
whitepaper specs were not retrievable in this sweep — to be pinned against
Intel/NVIDIA PSIRT + spec docs at procurement.

---

## 4. The 54 residual failures — honest triage (not "zero by fiat")

Source: `backend/tests/handover_quality_debt.json`. Categorized by *real cause*;
"fix" only where the cause is a genuine test/config bug.

| Cluster | Honest disposition |
|---|---|
| `mcp_bridge` (4), `full_stack_integration` (4), `terminal*` (7), `middleware_chain` (1) | **App/integration** — fixable: align to dev-mode repos / loopback policy; triage each. |
| `v32_stability` (2) | **Config**: `router.yaml` lacks a top-level `fallback_chain` ending in `local-default`. *If* that key is genuinely intended, add it (real config) and verify the router consumes it — **not** a decoy. |
| `billing` (4) | **Real product behavior**: 503 "cannot verify prior deduction" needs a real DB. Either finish dev-mode credit support (real feature work) or mark the test as requiring a DB fixture. **Not** a mock-to-green. |
| `owasp_rate_limit` (4), `owasp_bola` (1), `dns_pinning` (2), `hostile_tenant_leak` (1) | **Security/red-team — diligence-critical.** Triage as real first; some may be contamination (dns_pinning passes in isolation). **Never force-green.** |
| `memory_scaling` (5), `vmm_security` (3), `kim_inference_caps` (1), `finetune_rigor` (5), `open_core_split` (5) | **Kernel-source / architecture asserts.** Green only by making the **source true** (real kernel/arch work) or a product owner confirming the test is stale. |
| `smart_routing` (1), `efficiency` (1), `load_security` (3), `perf/memory_leaks` (1) | router-config / perf — triage individually. |

**Honest end-state:** the app/integration + config clusters are reducible to
near-zero with genuine fixes; the **security suites and kernel-source asserts
are real signal** and must be resolved on their merits, not suppressed. "Local
release debt to absolute zero" is achievable **only** if every one of these is
*genuinely* fixed — which for the security/kernel rows means real engineering,
not config alignment.

---

## 5. Honest certification matrix (what "done" actually means)

| Gate | Target | Method |
|------|--------|--------|
| Core release gate | **931/931 green** (hold) | `pytest backend/tests/security/ backend/tests/services/ -q` |
| Broad suite | reduce 54 → lower via **root-cause** fixes; disclose irreducible real-state failures | sequential `pytest backend/tests/ -p no:xdist -q` (reliable mode) |
| M3 | **50/80 only** after Phase 5 QEMU matrix + Phase 6 audit | KAT + QEMU + external review |
| Crypto | RFC 8032 + malleability + Taming edge KATs green | `kernel/tests/ed25519_kat.c` (extended in Phase 2) |
| Provenance | independent `vos3.elf` rebuild + Sigstore re-verify | acquirer-side |

**Framing for M&A:** the credible manifest is *"core security gate green;
49/80 honest moat; a KAT-correct (audit-pending) SecureBoot primitive; a
mapped, hardware-gated roadmap for the rest; and a fully disclosed quality-debt
ledger."* A "100% complete" claim would not survive technical diligence and is
not made here. Transparency **is** the valuation thesis — auditability is the
product.

---

## 6. Sources (May–June 2026 sweep)
- [CVE-2026-4115 — PuTTY Ed25519 over-large S / malleability](https://www.sentinelone.com/vulnerability-database/cve-2026-4115/)
- [CVE-2025-15444 — libsodium Ed25519 point validation](https://www.sentinelone.com/vulnerability-database/cve-2025-15444/)
- [Taming the many EdDSAs (eprint 2020/1244)](https://eprint.iacr.org/2020/1244.pdf) · [The Provable Security of Ed25519 (eprint 2020/823)](https://eprint.iacr.org/2020/823.pdf) · [RFC 8032](https://datatracker.ietf.org/doc/html/rfc8032)
- [CVE-2026-31431 "Copy Fail" (algif_aead, exploited)](https://www.microsoft.com/en-us/security/blog/2026/05/01/cve-2026-31431-copy-fail-vulnerability-enables-linux-root-privilege-escalation/) · [NVD CVE-2026-31431](https://nvd.nist.gov/vuln/detail/CVE-2026-31431)
- [CVE-2026-43493 — pcrypt MAY_BACKLOG](https://www.sentinelone.com/vulnerability-database/cve-2026-43493/) · [CVE-2026-46333 — ptrace local root (Qualys)](https://blog.qualys.com/vulnerabilities-threat-research/2026/05/20/cve-2026-46333-local-root-privilege-escalation-and-credential-disclosure-in-the-linux-kernel-ptrace-path)
