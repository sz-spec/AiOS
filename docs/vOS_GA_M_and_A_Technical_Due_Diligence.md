# vOS 1.3-GA — Technical Due Diligence & Asset Manifest

**Prepared:** 2026-06-07 · **Subject branch/commit:** `main` @ `fd74753`
**Audience:** acquirer technical diligence team · **Classification:** internal ground-truth

> **Representations & accuracy note (read first).** This is a *due-diligence*
> document, not a sales deck. Every metric below is verifiable against the named
> commit, test path, or file. Where a capability is partial, scaffolded, or
> unverified, it is labeled as such. Claims an acquirer's own diligence would
> contradict (e.g. "100% complete", "zero debt") are deliberately **not** made —
> their absence is itself a signal of the report's reliability. Figures are from
> a real test run; the broad suite is reproducible with the command in §5.

---

## 1. Executive Technology Summary

**What vOS is.** An agent-era operating system whose differentiator is
**auditability and sovereign (local-first) execution** for regulated AI-agent
deployment — positioned against the EU AI Act Article 73 transparency regime
(enforcement 2026-08-02). The thesis is not "we solved every agent-security
problem" — it is **"we have a documented, tier-1-sourced answer for every layer,
and we are transparent about what is solved, tracked, or still open."** That
transparency posture is the asset.

**Verifiable strengths**
- A green **core security/services release gate** (931 passing tests; see §2).
- A genuinely **local-first routing path** (local-titan / Ollama) with a
  *fail-closed* sovereign guarantee: privacy-mandate requests that cannot run
  locally raise HTTP 503 rather than silently falling back to non-sovereign
  cloud (§2).
- An **80-problem agent-OS catalog** with honest per-row status, and a
  **49/80 verified moat** of fail-closed enforcement primitives.

**Honest framing of the "loopback runtime."** The repository's suite-wide
loopback-only network policy is a **test-harness determinism control** (it makes
CI hermetic and prevents accidental external calls during testing). It is *not*
itself a production "zero data-leak guarantee" — the production sovereignty
guarantee is the routing/egress enforcement layer (local-titan 503, outbound-PII
shield, regional policy). An acquirer should evaluate those enforcement modules
on their merits, not conflate them with the CI policy.

---

## 2. Core Technical Asset Manifest (shipped, verified IP)

### 2.1 Core release gate — 931/931 green (verified)
- **Scope:** `backend/tests/security/` + `backend/tests/services/` — **49 test
  files, 931 passed, 8 skipped, 0 failed** at `fd74753`.
- **Covers:** authenticated-user dataclass enforcement, CSRF firewall, SSRF/DNS
  pinning logic, compliance-store crypto, the security-service layer, and the
  fail-closed security gates (IOMMU/DMA, perf-counter lockdown, BBS+ disclosure,
  IBCT, taint engine, model-integrity watchdog, execution-history gate).
- **⚠️ Scope disclosure (material):** "931/931 green" is the **core gate only**.
  Other security-adjacent suites OUTSIDE that gate currently have failing tests —
  `owasp/test_owasp_rate_limit` (4), `owasp/test_owasp_bola` (1),
  `security/test_dns_pinning` (2), `red_team/test_hostile_tenant_leak` (1). These
  are in the §4 risk ledger. An acquirer must not read "931/931" as "all security
  tests pass." (Note: `dns_pinning` passes in isolation — its full-run failure is
  under investigation as possible contamination vs. real; treat as open.)

### 2.2 Local-first moat — 49/80 (verified), software-tractable backlog exhausted
- 49 of the 80 catalogued agent-OS problems have a vOS fail-closed enforcement
  posture, evidenced by tests + source. Ledger: `SESSION_HANDOVER_LOCK.md §4`.
- **Honest denominator:** this is **49/80 (~61%)**, *not* "100%." The accurate
  claim — and the one the engineering ledger supports — is that the
  **software-only** rows are closed; the remaining 31 require hardware, upstream
  delivery, or open research (§3). Re-baselining the denominator to "49/49 local"
  would be a framing an acquirer should discount.

### 2.3 Local-titan sovereign routing — realized (commit `298ff50`)
- **What it does:** `src.efficiency.router.get_optimal_model()` routes
  non-critical agent roles to the `local-titan` lane (Ollama-served) under
  `VOS3_DEFAULT_LOCAL_FIRST` when the daemon is reachable; a privacy mandate
  forces *all* roles local. Critical roles (architect/reviewer) stay on cloud for
  quality unless local-only is asserted.
- **Sovereignty guarantee (the IP):** if local routing is required and TITAN is
  unreachable, `orchestrate_pre_flight` raises a **503 with `Retry-After: 30`
  whose body explicitly forbids cloud fallback** — i.e. it fails closed rather
  than exfiltrating to non-sovereign cloud. Verified by `test_titan_agent_loop`
  (8/8 green).
- **Maturity note:** this is the *routing/decision + health-gate* layer, wired to
  Ollama. It is real and tested; it is not a managed-model-hosting product.

### 2.4 Kernel model-SecureBoot — SCAFFOLD only (commit `1a18cf8`)
- **What exists:** `kernel/src/fs/vvfs_transport.c::vvfs_model_sig_verify()` — a
  fail-closed read-path hook returning `VOS3_FS_ERR_KEYREJECTED (-129)`, wired
  after the ACL gate in `vvfs_read_block`. Compiles into `vos3.elf`; both `#if`
  branches syntax-check.
- **⚠️ Maturity (material):** gated by `VOS3_VVFS_REQUIRE_MODEL_SIG`, **default
  OFF** — a no-op in default builds. It is **not a working signature check**: the
  kernel has SHA-256/384 + HMAC + X25519 + ML-KEM but **no Ed25519/ECDSA verify
  and no SHA-512**, both required by the OMS model-signing scheme. It is honest
  *groundwork* for moat row M3, **not a shipped SecureBoot capability**, and is
  **not counted** in the 49. Runtime fail-closed behavior is **not yet verified**
  (no QEMU harness). Closing M3 is a scoped kernel-crypto task (see §3/§4).

---

## 3. Multi-tier architecture & forward moat (the 31 open rows)

Source: `docs/release_notes/vOS_Cloud_Enclave_Sovereign_Spec.md`. **These are
OPEN/REQUIRED rows — a forward roadmap, not shipped IP the acquirer inherits as
working features.** Their value is as a *defensible, hardware-gated roadmap*, not
as completed assets. (Exact membership is reconciled from the May-2026 roadmap
minus documented closures; the full 80-row source catalog is not in-repo, so a
few tail rows are flagged uncertain in the spec.)

| Tier | Rows | Gating dependency | Realizable when |
|------|------|-------------------|-----------------|
| **(a) Enterprise Cloud Enclave** | D3, D7, E4, J2, J4, K1, L2, O2, O4 (≈9) | Intel TDX 2.0 · NVIDIA Blackwell Confidential-Compute · TPM 2.0 · CXL 3.0 | hardware procurement Q1–Q2 2027 |
| **(b) Upstream OS/vendor integrations** | C3, C5, L1, L3, N2, O1, D2/D6-residual (≈7) | vendor/distro/standards delivery | as upstream ships; vOS rides along |
| **(c) Long-term research assets** | A5, C1, C4, E5, I4, I6, K3, P1 (8) | no production fix exists anywhere | open research (TAM, not roadmap-datable) |
| **(d) Software tail** | M3 (1) | in-kernel Ed25519+SHA-512 verify primitive | next kernel sprint (scaffold exists) |

**Acquirer takeaway:** the barrier-to-entry value is real (these rows demand
confidential-compute silicon and standards work most competitors haven't done
the mapping for), **but they are unbuilt**. They should be valued as roadmap +
documented architecture, not as revenue-ready capability.

---

## 4. Honest quality-debt & risk ledger

**Live broad-suite status (sequential, deterministic):** **54 failed / 9,270
passed / 278 skipped** at `fd74753` (0 worker crashes). Full per-item matrix:
`backend/tests/handover_quality_debt.json` (the JSON predates the local-titan +
Phase-B2 fixes now merged; titan/profile_dispatch/W5 are now green, hence 54 not
61). **This section does NOT claim "zero engineering debt"** — that claim would
be false and would not survive diligence. The accurate statement: the failures
are **mapped and categorized**, with **no evidence of a foundational architecture
defect**, but they include real items an acquirer must price in:

| Cluster (count) | Honest root cause | Risk class |
|---|---|---|
| `test_memory_scaling` (5), `test_vmm_security` (3), `kim_inference_caps` (1) | **Kernel-source assertions** grep live C for security guards (e.g. `VOS3_KV_CACHE_MAX_BYTES`). Failing ⇒ the asserted source state is not present. **Not yet root-caused as benign** — could indicate a genuine missing/renamed kernel guard. **Must be triaged before relying on those guarantees.** | medium (security-relevant; unverified) |
| `test_open_core_split` (5), `test_finetune_rigor` (5) | Source-structure asserts (engine relocation, pro-gating, finetune kernel tool-IDs/PCR/struct). Reflect real architecture state vs. spec. | low–medium |
| `owasp_rate_limit` (4), `owasp_bola` (1), `dns_pinning` (2), `hostile_tenant_leak` (1) | **Security/red-team negation suites failing.** Some may be test contamination (dns_pinning passes in isolation); others may be real gaps. **For a security product this is the highest-attention item** and must be resolved/explained before close. | **high (diligence-critical)** |
| `billing` (4) | Route returns real `503 "cannot verify prior deduction"` — credit refund requires a real DB; dev-mode path unfinished. Product behavior, not infra. | medium (revenue feature incomplete) |
| `full_stack_integration` (4), `mcp_bridge` (4), `terminal_extended`/`terminal` (7), `v32_stability` (2), `smart_routing`/`efficiency` (2), `load_security` (3), `middleware_chain` (1), `perf/memory_leaks` (1) | Application/integration behavior + in-memory-vs-DB dev repos + router-config expectations. | low–medium |

**Correction to a framing to avoid:** there is no evidence of "intentional kernel
configuration drift to 32 GB bounds" as the memory-scaling cause — that root
cause is *not* established; the tests are source-greps and the discrepancy is
**untriaged**. Representing it as a deliberate, benign config choice would be
unsupported.

**Net engineering-debt assessment (honest):** the *infrastructure* layer is
healthy — the broad suite went 273→54 this cycle via root-cause fixes (test
isolation, env-leak neutralization, a deterministic network policy), and the core
gate is green. The residual is **application/product/kernel-source alignment + real
security-suite failures that require triage**. An acquirer faces a **bounded,
mapped remediation backlog**, not a green-field rewrite — but it is **not zero**,
and the failing security suites + unverified kernel-source guards are genuine
pre-close diligence items.

---

## 5. Verification & provenance controls

- **Reproducibility:** broad suite —
  `backend/.venv_p312/bin/python -m pytest backend/tests/ -p no:xdist -q --timeout=60`
  (sequential is the reliable mode; see below). Core gate —
  `... backend/tests/security/ backend/tests/services/ -q`.
- **Why sequential is the trusted number:** under `pytest-xdist` the broad suite
  retains a **parallel-only worker-crash flake** (~1 worker "not properly
  terminated" near completion, occasionally an xdist `INTERNALERROR`) that
  corrupts parallel counts. Root cause is resource/timing contention + a
  thread-method timeout, **not** a product defect; sequential runs complete clean
  (0 crashes). This is a CI-infra item, documented, on the v1.3.1 list.
- **Suite-wide loopback network policy** (`backend/tests/conftest.py`,
  `_VOSLoopbackOnlySocket`): blocks external `connect()` during tests
  (fast-fail), making CI hermetic/deterministic. It is a **test control**, not a
  production guarantee — do not represent it as a runtime data-leak proof.
- **Binary provenance:** `kernel/build/vos3.elf` is **gitignored** (a build
  artifact), so local rebuilds (e.g. the M3 scaffold) do **not** mutate the
  tracked canonical GA SHA recorded in `CLAUDE.md`. An acquirer should
  independently reproduce the kernel build (`x86_64-elf-gcc` toolchain) and
  re-verify the Sigstore bundle rather than trust the recorded SHA at face value.
- **Commit lineage audited:** PR #14 (clerk/contamination), PR #15 (network
  policy + env-leak fixes), PR #16 (profile_dispatch + W5), then direct-to-main
  `1a18cf8`→`fd74753` (M3 scaffold, open-moat spec, local-titan, Enclave spec).
  All test-infra / scaffold / docs; no production-logic change inflates any
  security claim.

---

## 6. Diligence checklist for the acquirer (recommended pre-close)

1. **Resolve/explain the failing security suites** (owasp_rate_limit, owasp_bola,
   dns_pinning, hostile_tenant_leak) — highest priority for a security product.
2. **Triage the kernel-source assertion failures** (memory_scaling, vmm_security,
   kim) to confirm whether the asserted guards exist in the shipped kernel.
3. **Independently rebuild + re-attest** `vos3.elf` and verify the Sigstore/Rekor
   bundle against the recorded SHA.
4. **Validate the M3 SecureBoot claim** is understood as a default-off scaffold,
   and scope the Ed25519+SHA-512 kernel-crypto work to actually close it.
5. **Confirm the 31 open rows** are valued as roadmap (hardware/research/upstream
   gated), not as shipped features.
6. **Confirm billing's** credit/refund path against a real DB before assigning
   revenue-feature value.

---

*This brief is intentionally conservative. Every green number is real and
reproducible; every gap is disclosed. That is the basis on which the
auditability/sovereignty thesis — vOS's core value proposition — should be
evaluated.*
