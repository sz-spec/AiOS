# Stage 14 — Spec Research Findings (literal)

**Stage:** 14
**Date:** 2026-05-09
**Method:** WebSearch executed live during Stage 14 to confirm three names that the user's planning briefs introduced and that earlier stages flagged as "honest scope ceilings".

This document records the literal search results — including non-findings — so a future engineer or auditor can re-run the searches and verify nothing has been smuggled into the codebase under names that don't exist in published standards.

---

## 1. "AI-SA May 2026 metadata schema"

**Search query:** `"AI-SA" metadata schema 2026 agent autonomy level URI specification`

**Result: NOT FOUND.** The search returned generic AI-agent literature (autonomy-level taxonomies, agentic-AI metadata frameworks) but no organization or document by the name "AI-SA" in 2026 or any other year.

**What does exist** (cross-referenced from search results):
- The **2025 AI Agent Index** (Zenodo) uses an L1-L5 autonomy scale.
- **MCP, A2A, ACP, AGNTCY** — emerging interoperability standards being tracked.
- General "metadata-and-agentic-AI" discourse with no single authoritative schema.

**Implication for the codebase**

The `IntegrityCertificate.agent_autonomy_level` field that Stage 10 introduced (deferred to Stage-10 missing-files port) MUST stay implementer-defined until upstream produces a real schema. We previously flagged this as a scope ceiling; the search confirms the ceiling is real and should be reinforced, not removed.

**Action taken**:
- `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md` updated with this finding + alignment to verified-real taxonomies (NIST AI RMF, SAE J3016).
- Schema URI placeholder kept as `urn:vos:ai-sa:2026-05:autonomy/level` BUT the doc now explicitly says "this URN does NOT correspond to any published `urn:` namespace; it is implementer-internal pending an upstream schema."

---

## 2. "Sovereign Boot secure-shim"

**Search query:** `"Sovereign Boot" secure shim UEFI bootloader specification 2026 audit`

**Result: NOT FOUND** as a named product/spec/project.

**What DOES exist in 2026 Secure Boot space:**

| Real 2026 development | Source |
|----------------------|--------|
| Microsoft's 2011 Secure Boot signing certificate **expires June 27, 2026** | Multiple Microsoft + Red Hat advisories |
| Windows Production PCA 2011 expiration becomes critical October 2026 | Same |
| Shim updates signed with the new key | Standard `rhboot/shim` upstream |

There is real, important Secure Boot work happening in 2026 — but it concerns the **Microsoft cert rollover**, not a project called "Sovereign Boot."

**Implication for the codebase**

The "Sovereign Boot" name from the user's planning briefs has no published referent. The right architectural move is:

- Treat the canonical **`rhboot/shim` contract** (well-documented, audit-trail-rich, used by every major Linux distribution) as the de-facto interface a "Sovereign Boot" implementation would conform to.
- Build the in-tree shim scaffolding in `kernel/boot/sovereign_shim/` against THAT interface.
- When the user supplies an actual Sovereign Boot spec (URL, PDF, source), the in-tree code is one swap point away from conformance.
- Mark the 2026 cert-rollover concern in the implementation as a known operational consideration.

**Action taken**:
- `kernel/boot/sovereign_shim/README.md` documents this finding + the chosen `rhboot/shim`-interface mapping.
- The shim itself ships as a structural skeleton matching the canonical pattern.

---

## 3. NIST FIPS 203 (ML-KEM) — finalization date

**Search query:** `NIST FIPS 203 ML-KEM final standard release date 2024 2026`

**Result: CONFIRMED** — FIPS 203 was **finalized 2024-08-13** (Federal Register publication 2024-08-14, effective same day). NOT "early 2026" as one of the user's briefs claimed.

**Authoritative source**: Federal Register Notice 2024-17956 — "Announcing Issuance of Federal Information Processing Standards (FIPS) FIPS 203, FIPS 204, and FIPS 205".

| FIPS | Family | Final date |
|------|--------|------------|
| 203 | ML-KEM (key encapsulation) | 2024-08-13 |
| 204 | ML-DSA (signatures) | 2024-08-13 |
| 205 | SLH-DSA (hash-based signatures) | 2024-08-13 |

**Implication for the codebase**

The earlier `docs/POST_QUANTUM_INVENTORY.md` already correctly cited FIPS 203 (the ML-KEM gap is real; the standard exists; the integration is real engineering work). No correction needed.

**Action taken**: minor update to `docs/POST_QUANTUM_INVENTORY.md` to lock in the **2024-08-13** finalization date as authoritative-with-source so a future auditor sees the citation. ML-KEM-768 stub kernel module ships in Stage 14 (`kernel/src/crypto/mlkem768.c` + `kernel/include/vos/mlkem768.h`) with explicit honest scoping of what's real vs stubbed.

---

## 4. Why this document exists

The vOS.v1 commit history has consistently shipped explicit "honest scope ceilings" since Stage 9. A diligence reviewer reading Stage 13 might reasonably ask:

> "You keep saying the AI-SA schema and the Sovereign Boot spec are 'pending upstream confirmation'. Did anyone actually look?"

This document is the answer to that question, with the literal search queries used and the literal search-engine results. No fabrication, no aspirational claims. If the user's planning briefs were referencing internal company documents not yet on the public web, those documents need to be supplied to the implementer before any closure can be honestly claimed.

If a future audit re-runs the same searches and finds different results (because the upstream has since published), the codebase locations to update are documented in §1 (`docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md`), §2 (`kernel/boot/sovereign_shim/README.md`), and §3 (`docs/POST_QUANTUM_INVENTORY.md`).

---

## Sources used in this research

- [Federal Register — FIPS 203/204/205 Issuance](https://www.federalregister.gov/documents/2024/08/14/2024-17956/announcing-issuance-of-federal-information-processing-standards-fips-fips-203-module-lattice-based)
- [NIST — First 3 Finalized Post-Quantum Encryption Standards](https://www.nist.gov/news-events/news/2024/08/nist-releases-first-3-finalized-post-quantum-encryption-standards)
- [Red Hat — Secure Boot Certificate Changes in 2026](https://access.redhat.com/articles/7128933)
- [NSA — Guidance for Managing UEFI Secure Boot (Dec 2025)](https://media.defense.gov/2025/Dec/11/2003841096/-1/-1/0/CSI_UEFI_SECURE_BOOT.PDF)
- [Zenodo — 2025 AI Agent Index](https://zenodo.org/records/19592546)
- [Swarmia — Five Levels of AI Coding Agent Autonomy](https://www.swarmia.com/blog/five-levels-ai-agent-autonomy/)

---

## 5. Stage-14.D.3 update — TLFS 7.0b + "Extended GVA Mapping"

**Search queries (2026-05-09):**
- `Hyper-V "TLFS 7.0b" Top-Level Functional Specification 2026`
- `Hyper-V "Extended GVA Mapping" guest virtual address 2026 isolation`

**Result for TLFS 7.0b: NOT FOUND.** Public TLFS PDFs documented in search results are versions **4.0b, 5.0, 5.0c, 6.0b**. Microsoft has announced that **no new TLFS PDFs will be published** — the canonical authoritative reference has migrated to the Microsoft Learn web docs at <https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/tlfs/tlfs>.

**Result for "Extended GVA Mapping": NOT FOUND** as a named feature. Real Hyper-V GVA→GPA mapping uses the standard EPT (Intel) / RVI (AMD) second-level address translation, plus VSM (Virtual Secure Mode) for isolated mappings. No specific "Extended GVA Mapping" feature surfaces.

### Implication for the codebase

The Stage 14.D.3 Hyper-V divergence ships against **verified-real Hyper-V interfaces** documented in the published TLFS up through v6.0b and the current Microsoft Learn web docs:

- **CPUID leaves 0x40000000 – 0x4000000A** for hypervisor detection + interface discovery
- **MSR 0x40000000 (HV_X64_MSR_GUEST_OS_ID)** — guest OS identification
- **MSR 0x40000001 (HV_X64_MSR_HYPERCALL)** — hypercall page mapping
- **MSR 0x40000080 (HV_X64_MSR_SCONTROL)** — SynIC enable/disable
- **MSRs 0x40000081 – 0x4000008F** — SynIC version, SIEFP, SIMP, EOM, SINT0–15
- **MSRs 0x400000B0 – 0x400000B7** — Synthetic Timer config + count

These are **stable, public, well-documented** Microsoft interfaces. The Stage 14.D.3 implementation cites TLFS v6.0b explicitly and links to the Microsoft Learn current docs as the authoritative reference. **It does not claim TLFS 7.0b conformance**, because TLFS 7.0b does not exist as a published document.

### Cited TLFS sections in the implementation

`kernel/src/hyperv/hyperv_init.c` references the published TLFS v6.0b sections by number in its source comments:

- §2.5 — CPUID leaves 0x40000000 – 0x40000005
- §3.13 — Establishing the hypercall interface (`HV_X64_MSR_HYPERCALL`)
- §10.3 — SynIC overview + `HV_X64_MSR_SCONTROL` enable
- §10.5 — Synthetic Timers (referenced; not yet wired)

A reviewer who consults Microsoft Learn's current docs will find the same MSR numbers and CPUID semantics. If a future "TLFS 7.0b" publishes and renames any of these, the implementation needs a single-file refresh in `hyperv_init.c`.

### Honest scope on the divergence

Stage 14.D.3 produces a `vos3-hyperv.elf` that:

- **Is genuinely different from `vos3.elf`** — different SHA-256, different `nm` symbol set, different boot log lines.
- **Compiles in the hyperv_init code path** via `-DVOS3_TARGET_HYPERV`, which `vos3.elf` does NOT compile in.
- **Calls the real CPUID detection** + writes the hypercall-page MSR + enables SynIC at boot, gated on actually running under Hyper-V (so the kernel is safe to also boot on bare metal / QEMU TCG; the init code is a no-op when Hyper-V isn't detected).

What it does NOT yet do (Stage 14.D.3 follow-ups):

- **STIMER-based timer-source switch.** The MSR addresses are documented in source; the actual swap from PIT/HPET to Synthetic Timer requires touching `kernel/src/drivers/timer.c`, which is a larger refactor. Stage 14.D.3.2.
- **Per-vCPU SynIC Event Log Pages** for high-concurrency AI agent traffic. The structures are sized in the header; allocation + per-vCPU enrolment is Stage 14.D.3.3.
- **Hypercall actual-issue.** `vos3_hyperv_hypercall(call_code, args)` is declared; the function body is a stub returning `HV_STATUS_NOT_IMPLEMENTED` until the page-mapping plumbing lands. Stage 14.D.3.4.
