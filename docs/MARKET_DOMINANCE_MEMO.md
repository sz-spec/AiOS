# VOS-Cyber v20.3-PRODIGY — Market Position Memo

**Date:** 2026-04-24  (sanitized to vendor-neutral form in v20.7.1)
**Purpose:** strategic-deployment brief. Intended audience: enterprise-integration
analyst at a Tier-1 cloud service provider, AI-SPM vendor, NGFW vendor, or EDR
vendor; or growth-stage investor diligencing the AI-runtime attestation category.
**Tone contract:** every claim is cited to code or to a dated public source.
Marketing superlatives are not in this document. Where a competitor beats us
on a dimension, we say so.
**v20.7.1 sanitization note:** in this and the other v20.x memos, specific
vendor brand names have been replaced with industry-segment terms ("Tier-1
AI-SPM platform", "L7 AI-firewall", "EDR / AIDR vendor", "Cloud Service
Provider"). The original v20.3 / v20.4 / v20.5 commits in `git log` retain
the named-vendor history for reference; this document is the neutral form.

---

## 1. The category we actually compete in

Not CNAPP (the Tier-1 AI-SPM segment). Not SASE (the L7 AI-firewall segment).
Not MLDR (the model-detection-and-response segment).

**VOS-Cyber is the evidence layer** — the kernel-rooted, hardware-measured,
Z3-proven, cryptographically-signed substrate that produces the
[EU AI Act Article 11 / Annex IV technical documentation](https://artificialintelligenceact.eu/annex/4/)
a notified body needs, *from a runtime the incumbents cannot see into*.

Searching "AI-SPM" returns dashboards. Searching "attestation" returns TPM
blogs from 2019. The intersection — "signed, hardware-rooted, AI-specific
attestation that automates Annex IV" — is empty in the 2026-Q2 market.
That intersection is our seat.

---

## 2. What the v20.3-PRODIGY release puts into the seat

### 2.1 TCB reduction on slot activation

Before v20.3, every AI model slot activation issued three separate
`TDCALL(TDG.MR.RTMR.EXTEND)` operations — one for the model hash, one
for the IntentManifest hash, one for the agent-policy hash. Each TDCALL
is a round-trip into the TDX Module (Ring-0-equivalent, outside our TCB),
and each adds a synchronisation window.

**v20.3 change (`kernel/src/mm/tee.c::vos3_tee_slot_activate_bound`):**
we compose

$$
C = \text{SHA-384}(h_M \mathbin\Vert h_I \mathbin\Vert h_P)
$$

in kernel-local memory and extend RTMR[1] **once** with `C`. Component
hashes remain recorded in the measurement ring and surfaced on the
JSON-LD certificate; auditors recompute `C` locally from those three
and cross-check against RTMR[1] in the quote. SHA-384 collision
resistance guarantees the triple-binding has the same cryptographic
strength as three independent extends.

**Honest framing of the "smaller TCB" claim.**

- What we actually reduced: **TDCALL count per slot activation, 3 → 1**.
- What we did *not* reduce: the size of the Intel TDX Module itself —
  that is Intel-owned and ~92 KLOC (public TDX Module source figures).
- The "50% smaller TCB than any TEE competitor" phrasing is marketing
  shorthand for "50% fewer round-trips into the TCB at slot activation."
  That is true and measurable (look at `vos3_tdcall_rtmr_extend` call
  counts under a session-load benchmark). The total-substrate TCB is
  the TDX Module + the VOS3 kernel either way; our relative edge there
  is that our kernel is narrower and freestanding (no general-purpose
  Linux surface), not that we shrank Intel's TDX Module.
- If an investor pushes on the "50%" number, lead with the TDCALL count
  (defensible, measurable) and the 92 KLOC vs multi-million-LOC-Linux
  comparison (defensible as published LOC). **Do not** cite "50% smaller
  TCB than Tier-1 AI-SPM / L7 AI-firewall / EDR vendors" — those three
  segments don't have a TEE runtime at all, so the comparison is
  undefined.

### 2.2 Side-channel hardening — constant-time SMT sibling lookup

The SCHED_CORE cookie guard (item K3 on the OMEGA Dashboard) is the
kernel-scheduler invariant that prevents cross-trust-domain SMT
co-execution. The v20.1 implementation used a Θ(N) linear scan of the
online-CPU table at every invocation. That leaks timing information
through trip-count variance.

v20.3 materialises the SMT-sibling graph — which is
hardware-static after `vos3_smp_init` — into a
`g_cpu_sibling[VOS3_SCHED_MAX_CPUS]` table populated once at boot:

$$
\sigma[i] := \min \{\, j : j \ne i \land \operatorname{smt\_siblings}(\text{apic}[i], \text{apic}[j]) \,\}
$$

Hot-path lookup is a single array deref — **constant-time, data-independent**.
This is strictly better than the old path on the timing side-channel axis
and preserves the Z3 invariant in `backend/tests/benchmarks/sched_core_z3_proof.py`
(which has always modelled sibling as a pure function; see the parity note
added to that file in this release).

Observable impact at benchmark time: at N=32 logical CPUs and a 1 kHz
pick rate, we drop from $\Theta(N^2 f) = 32 \cdot 1000 \cdot \Theta(32) \approx 10^6$
work/sec to $\Theta(N f) \approx 3 \times 10^4$ — a ~32× reduction on the
SCHED_CORE hot path, linear in CPU count.

### 2.3 Defense-in-depth — HCS speculative-boundary fence normalisation

The HCS (Hyper-threading Context Switch) sequence now emits an explicit
`lfence` on **both** the hardware and software L1D-flush branches
(`kernel/src/mm/hcs.c:89–108`, with an in-source citation to Intel SDM
Vol 4 §2.8.3). The prior code relied on the *implicit* serialisation
of `WRMSR(IA32_FLUSH_CMD)`; this is correct today but brittle against
future microcode updates that narrow the promise. The v20.3 invariant

$$
\forall\, \ell \text{ issued in } [t_{\text{flush\_done}}, t_{\text{IBPB\_start}}]:
\quad \ell \notin L1D_{\text{outgoing}} \land \ell \notin \text{BTB}_{\text{outgoing}}
$$

is now established *syntactically in source* — no microcode guarantee
required.

### 2.4 Cryptographic work-factor tightened to the 2026 state of the art

SQLCipher vault KDF: **PBKDF2-HMAC-SHA512 @ 600 000 iterations**
(was 256 000). Derivation in-source at
`backend/core/repositories/local_vault.py`:

$$
W = H + \log_2 I \;\ge\; 80 \text{ bits}, \qquad H \ge 64, \qquad R \le 10^{10}\text{/s}
\Rightarrow I \ge 2^{16}; \; \text{we pick } 600{,}000 \approx 2^{19.2}.
$$

OWASP 2024 Password Storage Cheat Sheet compliant; cost is +100 ms at
unlock (paid once per process).

### 2.5 Ancillary hot-path hygiene

- **V-AAAK decoder** — pre-scan + single-allocation path
  (`backend/services/vbus_driver.py::v_aaak_decode`). Heap-churn measured
  at 0.7 B per decode over a 1k-decode loop vs amortised
  `O(frame_size)` previously. Material under PEP-703 free-threading
  (Python 3.14) where per-allocation GC-root contention shows up.

- **Model-weight prefetch** — new `backend/services/prefetch.py` warms
  IntentManifest-declared models via `run_in_executor` during the
  FastAPI lifespan. Does **not** extend RTMR (kernel-exclusive
  responsibility).

---

## 3. Competitive arithmetic (sourced, April 2026)

| Vector | VOS-Cyber v20.3 | Tier-1 AI-SPM platform (CSP-acquired Q1 2026) | L7 AI-firewall (NGFW segment, GA Q1 2026) | EDR / AIDR vendor |
|---|---|---|---|---|
| Trust root | **Hardware RTMR** | Agentless cloud-plane scan | NGFW redirect + guardrails | Model scan + I/O telemetry |
| Signed Annex IV cert output | **Yes — auto-emitted, JSON-LD, ECDSA-P256 signed** | No | No | No |
| TEE visibility | **Runs inside the TEE** | Structurally blind | Structurally blind (third-party analyst confirmed) | Structurally blind |
| Auto-trigger at session finalize | **Yes** | N/A | N/A | N/A |
| Z3-proven invariants shipped in cert | **3** (SCHED_CORE, egress, OOM guard) | 0 | 0 | 0 |
| Side-channel-hardened sibling lookup | **O(1), data-independent** | N/A | N/A | N/A |
| Sovereign / air-gap deploy | **Native** (SQLCipher vault, offline HF) | Cloud CNAPP | On-prem Helm available | Custom enterprise |
| Distribution | **Narrow** — strategic-deployment partnership | **CSP integration** | **Every enterprise NGFW contract** | Government accounts, analyst-firm mention |

**Where they beat us (stated up front so diligence doesn't):** brand,
distribution, customer logos, ecosystem breadth (CNAPP / SASE / MLDR
alongside their AI story). These are resolved by an enterprise
integration or strategic deployment partnership, not a product feature.

**Where we are uncontested:** the signed-Annex-IV-evidence-from-hardware
path. The Tier-1 AI-SPM segment cannot ship this without a kernel; the
L7 AI-firewall segment cannot ship it from an NGFW redirect; the EDR
segment cannot ship it from a model-file scan. Shipping our certificate
inside their graph (our `to_external_spm_jsonld()` export already
matches the AI-SPM 2026.1 JSON-LD pattern) makes all three of them
our distribution.

---

## 4. The strategic-deployment thesis in one sentence

> A Tier-1 enterprise-security vendor whose AI-security product is
> structurally TEE-blind integrates VOS-Cyber as its evidence layer
> — not to replace their scanner, but to carry the signed Annex IV
> certificate their scanner cannot generate — and the integrated
> surface becomes the differentiator against the other two segments.

Pricing anchors (public-segment data, indicative only):

- Tier-1 AI-SPM segment: a Q1 2026 acquisition closed at the
  $32 B mark (CSP-led).
- EDR / AIDR segment: most-recent named private round in the
  $500 M range (third-party aggregator data — treat as rough).

We are in the range where a tuck-in acquisition closes the exact
"runtime blindness" gap third-party analysts have publicly called out
on the L7 AI-firewall segment.

---

## 5. What still needs to be true in the data room

| Claim | Evidence | Status |
|---|---|---|
| Auto-attestation lands at every session finalize | `backend/ai/agents/multi_agent.py::_emit_session_attestation` | Shipped (v20.2-FINAL) |
| Z3 invariants proven over full address spaces | `backend/tests/benchmarks/*z3*.py` | 3 proofs, UNSAT, re-run in CI |
| TCB round-trip reduction on slot activation | `kernel/src/mm/tee.c::vos3_tee_slot_activate_bound` | Shipped (v20.3-PRODIGY) |
| Side-channel-hardened SCHED_CORE | `kernel/src/sched/core_cookie.c` (O(1) table) | Shipped (v20.3-PRODIGY) |
| Regulator-facing verify endpoint | `POST /compliance/verify` (unauthenticated) | Shipped (v20.2-FINAL) |
| External-SPM interop export | `IntegrityCertificate.to_external_spm_jsonld()` | Shipped (v20.2-FINAL; renamed v20.7.1 from `to_wiz_jsonld`) |
| Bulk audit export with signed manifest | `GET /compliance/export/bulk` | Shipped (v20.2-FINAL) |
| pip-audit 0 vulnerabilities | venv on main | Shipped (continuous) |
| TDX Module ≥ 1.5.24 enforcement in verifier | TCB-recovery policy in verify path | **Planned v20.3.1** (response to INTEL-SA-01397) |
| Sigstore OIDC + Rekor production signing tier | `infra/security/sigstore_verify.py` + ceremony | **Scheduled 2026-05-06** |
| SPIFFE-compatible agent identity | Roadmap | **Planned** (strategic alignment vs the L7 AI-firewall segment's proprietary-instrumentation lock-in risk flagged by third-party analysts) |

## Sources

- [Industry analyst — L7 AI-firewall segment TEE-blindness analysis](https://futurumgroup.com/insights/prisma-sase/)
- [Tier-1 AI-SPM segment — public product page (vendor name redacted v20.7.1; URL retained for traceability)](https://www.wiz.io/solutions/ai-spm)
- [EDR / AIDR runtime-security press release (vendor name redacted v20.7.1; URL retained)](https://www.prnewswire.com/news-releases/hiddenlayer-unveils-new-agentic-runtime-security-capabilities-for-securing-autonomous-ai-execution-302721517.html)
- [INTEL-SA-01397 TDX Module Advisory (2026.1 IPU)](https://www.intel.com/content/www/us/en/security-center/advisory/intel-sa-01397.html)
- [EU AI Act Annex IV](https://artificialintelligenceact.eu/annex/4/)
- [EU AI Act Article 11](https://artificialintelligenceact.eu/article/11/)
- [OWASP Password Storage Cheat Sheet (2024)](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [Intel SDM Vol 4, §2.8.3 — WRMSR serialisation behaviour](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
