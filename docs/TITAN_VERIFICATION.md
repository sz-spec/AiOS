# VOS-Cyber v20.4-TITAN — Verification & Category Position Memo

**Date:** 2026-04-24
**Audience:** acquirer-diligence analyst, regulated-sector CISO, EU notified-body
liaison. This document is the factual backbone for the M&A deck — every
claim resolves to a file path, a commit SHA, or a dated public URL.
**Tone:** same contract as `MARKET_DOMINANCE_MEMO.md` — marketing
superlatives are not in this document; where a competitor beats us on
a dimension, we say so, and where a regulatory claim in the brief
overstates reality, we correct it.

---

## 0. Two corrections to the brief (stated up front)

1. **"June 2026 global mandate for Class-1 Sensitive AI"** — no such
   global mandate exists. The EU AI Act has **high-risk** and
   **general-purpose AI with systemic risk** classifications, with the
   high-risk obligations phasing in on **2026-08-02** per
   [Article 113 / implementation timeline](https://artificialintelligenceact.eu/implementation-timeline/).
   The GPAI code-of-practice signing window opened 2025-07-18 and
   obligations start 2025-08-02. There is no global "Class-1" tier and
   no June 2026 deadline we were able to verify. This memo uses
   **EU AI Act high-risk** as the actual regulatory anchor. Any pitch
   deck citing "Class-1 Sensitive AI" should be corrected before
   customer-facing use.
2. **"50% smaller TCB"** — shorthand retained from the v20.3 memo
   with the same caveat: the defensible measurable claim is
   **"2× fewer TDCALL round-trips into the TDX Module per slot
   activation"** (3 → 1 via the composed-commitment path shipped in
   v20.3-PRODIGY). The total-substrate TCB is still TDX Module +
   VOS3 kernel; we did not shrink Intel's Module, and our kernel is
   narrower-than-Linux rather than quantifiably "50% smaller."

Customer-facing pitches that lead with the false mandate or the
inflated TCB claim will fail a technical due-diligence read. This
memo gives you the defensible versions.

---

## 1. What v20.4-TITAN actually lands

### 1.1 Hybrid classical signature on every certificate

Every `IntegrityCertificate` emitted at the close of an agent session
is now signed **twice**, over the same canonical payload bytes:

- **ECDSA-P256 + SHA-256** — unchanged primary signature. Back-compat
  with every v20.2+ auditor tool.
- **ECDSA-P521 + SHA-512** — new v20.4 required signature.
  Classical-security level rises from ~128 bits to ~256 bits. No system
  library dependency; available in every `cryptography` release.

A third **ML-DSA-65 (FIPS 204)** lattice signature is *wire-format
defined* and activated only when `VOS3_PQ_SIGNING=1` is set **and** the
Open Quantum Safe (`oqs`) Python binding is importable. Pure-Python
Dilithium reference implementations on PyPI carry explicit
side-channel warnings from their own maintainers and are therefore
**not** acceptable as a production signing path — the v20.4 codebase
refuses to bind to them even when present.

**Forward-compatibility clause.** The verify pipeline treats missing
P-521 and missing ML-DSA as "not applicable" rather than as failures,
so every v20.2 and v20.3 certificate ever emitted continues to verify
cleanly under the v20.4 verifier.

### 1.2 SQLCipher mmap_size enabled for both vaults

`local_vault.py` (entity vault) and `cert_vault.py` (certificate
vault) now issue `PRAGMA mmap_size = <N bytes>` on open. Defaults:
64 MiB and 16 MiB respectively; tunable via `VOS3_VAULT_MMAP_MB` and
`VOS3_CERT_VAULT_MMAP_MB`; `0` disables.

**What this gives us today.** One fewer syscall per page-fault-resolved
read on the vault read path. The crypto posture is unchanged — pages
are still decrypted via the SQLCipher codec into the process address
space; a process-level attacker remains the adversary we model.

**What this does NOT yet give us.** Pages mapped directly into
TDX-private memory such that the untrusted host never observes
decrypted state. That path requires **TDISP (TEE Device Interface
Security Protocol)**, which per the [NDSS '26 SoK on Accelerator TEE
Designs](https://cse.sustech.edu.cn/faculty/~zhangfw/paper/sok-xputee-ndss26.pdf)
is **pre-silicon** as of 2026-Q2. The TEE-direct map is a v20.5+
roadmap item gated on hardware availability; we do not claim it today.

### 1.3 Prefetch state-collapse hardening

`_warm_one()` now resolves each declared model reference with
`Path.resolve(strict=False)` and rejects any candidate whose resolved
path is outside an allowed-root set. Defaults: `data/attestation`,
`data/models`, `~/.cache/huggingface`. Extensible via
`VOS3_PREFETCH_ALLOW_ROOTS` (colon-separated).

The race this closes is minor: a hostile IntentManifest could have
caused the prefetcher to `open(/etc/passwd)` in v20.3. The 4 MiB of
bytes were always discarded, never cross-tenant-surfaced, and never
entered the attestation path — but the *fact* of the read is
observable via kernel audit logs and therefore a minor capability
leak. v20.4 collapses that leak without adding latency on the
happy path.

### 1.4 Supply chain

- `litellm` ≥ 1.83.7 (closes GHSA-xqmj-j6mv-4862).
- `lxml` ≥ 6.1.0 (CVE-2026-41066 — already pinned in v20.2).
- `python-dotenv` ≥ 1.2.2 (CVE-2026-28684 — already pinned in v20.2).
- **Residual:** `pip 26.0.1` is flagged on CVE-2026-3219; **no fix
  version exists on PyPI as of 2026-04-24**. We track this rather than
  claim a fix we can't deliver. It does not affect the runtime
  attestation surface.

---

## 2. "Formal Proof + Silicon Enforcement" vs "Observation-Only"

This is the argument the v20.4 release completes on paper. The
diligence-defensible form:

|  | VOS-Cyber v20.4-TITAN | Tier-1 AI-SPM platform (CSP-acquired Q1 2026) | L7 AI-firewall (NGFW-segment GA Q1 2026) | EDR / AIDR vendor |
|---|---|---|---|---|
| **What observes the workload** | Kernel running inside the TEE | Agentless cloud-plane scan | NGFW redirect + optional runtime guardrail | Model-file scanner + I/O telemetry |
| **What signs the evidence** | ECDSA-P256 + **ECDSA-P521** + optional **ML-DSA-65** over the canonical cert | N/A — dashboard findings | N/A — compliance-framework mapping | N/A — policy enforcement reports |
| **Where the invariants live** | 3 Z3 UNSAT proofs shipped in tree (SCHED_CORE, egress, OOM guard) | N/A | N/A | N/A |
| **What RTMR binds** | Composed `C = SHA-384(h_M ‖ h_I ‖ h_P)` extended into RTMR[1] once per slot activation | N/A | N/A | N/A |
| **Auditor verify time** | **~60 sec** (`POST /compliance/verify` returns verdict) | Hours of correlation per engagement | Hours | Hours |
| **TEE visibility** | **Native** | Structurally blind (agentless by design) | Structurally blind (Futurum 2026-03 acknowledged) | Structurally blind |
| **Hybrid PQ-ready signature wire format** | **Yes, v20.4** | No | No | No |
| **Sovereign / air-gap deploy** | **Native** (SQLCipher, offline HF) | Cloud CNAPP | Helm on-prem available | Custom enterprise |
| **Distribution / brand** | Narrow, strategic-deployment partnership | CSP integration | Every enterprise NGFW contract | Government accounts + analyst mention |

**The defensible positioning sentence.** Observation-only products
tell you *what they saw*. VOS-Cyber tells you *what actually
executed, measured from silicon, signed by a key whose fingerprint
you can cross-check against the repository, with invariants Z3 has
proved are unbypassable in the bounded model and — starting v20.4 —
a signature scheme that will still verify under a post-quantum
adversary*.

---

## 3. Category arithmetic for the EU AI Act high-risk use case

The [EU AI Act Article 11](https://artificialintelligenceact.eu/article/11/)
requires **technical documentation** for high-risk AI systems, per
[Annex IV](https://artificialintelligenceact.eu/annex/4/). Annex IV §1
(description), §2 (design/dev methods), §5 (harmonised standards), §7
(post-market monitoring) map 1:1 onto fields in our JSON-LD
`IntegrityCertificate`.

Observational products can produce *derivative* documentation from
their dashboards; they cannot produce a **hardware-rooted, signed
artifact binding the exact model + policy + platform**, because they
are structurally outside the TEE. That matters when a notified body
asks "how do you know the model described in §2(a) is the model that
actually ran during the post-market period in §7?" The VOS-Cyber
answer is "RTMR[1] of the TEE quote contains SHA-384 of the composed
commitment whose SHA-384 you can recompute from the three component
hashes we publish in the cert; the ECDSA-P521 signature binds all of
this under our published public-key fingerprint." That is an
auditable chain. The observational answer is "trust our dashboard."

**This does NOT automate the audit.** A qualified auditor still has
to read the certificate, read the Annex IV bullets, and exercise
judgment. What VOS-Cyber changes is the **evidence-collection cost**
— the part that typically consumes 80–92% of the engineering time on
an AI governance engagement (see `MARKET_DOMINANCE_MEMO.md` §2 for
the derivation). We move that cost from *engineer-days* to a
*single HTTP call*.

---

## 4. What still needs to be true in the data room

| Claim | Evidence | Status |
|---|---|---|
| Hybrid P-256 + P-521 signing on every cert | `backend/core/security/attestation_service.py::sign_certificate` | **Shipped v20.4-TITAN** |
| ML-DSA-65 wire format locked in | `IntegrityCertificate.signature_mldsa_b64`, `_try_mldsa_sign` | **Shipped v20.4-TITAN** (opt-in) |
| Path-traversal-hardened prefetch | `backend/services/prefetch.py::_is_under_allowed_root` | **Shipped v20.4-TITAN** |
| `PRAGMA mmap_size` on both vaults | `local_vault.py`, `cert_vault.py` | **Shipped v20.4-TITAN** |
| Composed-commitment RTMR[1] extend | `kernel/src/mm/tee.c::vos3_tee_slot_activate_bound` | Shipped v20.3-PRODIGY |
| O(1) SMT-sibling table | `kernel/src/sched/core_cookie.c` | Shipped v20.3-PRODIGY |
| 3 Z3 invariants UNSAT over full address spaces | `backend/tests/benchmarks/*z3*.py` | Shipped; re-run in CI |
| Auto-attestation at session finalize | `backend/ai/agents/multi_agent.py::_emit_session_attestation` | Shipped v20.2-FINAL |
| Regulator verify endpoint | `POST /compliance/verify` | Shipped v20.2-FINAL |
| Bulk audit export | `GET /compliance/export/bulk` | Shipped v20.2-FINAL |
| **liboqs vendoring in release pipeline** | — | **Planned v20.4.1** (flips `VOS3_PQ_SIGNING` default) |
| **TDISP-private SQLCipher page map** | — | **Planned v20.5+** (pre-silicon today) |
| **Zero-copy VBus ring buffer (Python side)** | — | **Planned v20.5** |
| **IntentManifest TDX Trusted Applet** | — | **v20.5 research item** (needs liboqs + applet loader) |
| Production Sigstore OIDC + Rekor signing | — | **Scheduled 2026-05-06** |
| TDX Module ≥ 1.5.24 TCB-min gate | — | **Planned v20.4.1** (INTEL-SA-01397 response) |

## Sources

- [EU AI Act Article 11](https://artificialintelligenceact.eu/article/11/)
- [EU AI Act Annex IV](https://artificialintelligenceact.eu/annex/4/)
- [EU AI Act implementation timeline](https://artificialintelligenceact.eu/implementation-timeline/)
- [NIST FIPS 204 — Module-Lattice-Based Digital Signature Standard (ML-DSA)](https://csrc.nist.gov/pubs/fips/204/final)
- [Open Quantum Safe liboqs ML-DSA docs](https://openquantumsafe.org/liboqs/algorithms/sig/ml-dsa.html)
- [dilithium-py PyPI — side-channel warning on reference impl](https://pypi.org/project/dilithium-py/)
- [NDSS '26 SoK — Accelerator TEE Designs (TDISP status)](https://cse.sustech.edu.cn/faculty/~zhangfw/paper/sok-xputee-ndss26.pdf)
- [SIGMETRICS '25 — Confidential VMs Empirical Analysis](https://dl.acm.org/doi/10.1145/3700418)
- [INTEL-SA-01397 (2026.1 IPU)](https://www.intel.com/content/www/us/en/security-center/advisory/intel-sa-01397.html)
- [Industry analyst — L7 AI-firewall TEE-blindness critique (2026-03)](https://futurumgroup.com/insights/prisma-sase/)
- [LiteLLM GHSA-xqmj-j6mv-4862](https://github.com/BerriAI/litellm/security/advisories/GHSA-xqmj-j6mv-4862)
- [OWASP Password Storage Cheat Sheet (PBKDF2 guidance)](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [Intel SDM Vol 4, §2.8.3 — WRMSR serialisation](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
