# Sprint 18 Plan — Wave 3 follow-ups (Cluster C-2 LIVE + Cluster B.2 cross-domain AIMS)

**Date:** 2026-05-26
**Predecessor:** Sprint 17 Wave 1 (3 prototypes) + Wave 2 (4 prototypes, +101 tests, cumulative 721)
**Window:** 2026-06-01 → 2026-07-31 (8-week sprint; tighter than Sprint 17's 10-month long-term window because every item here has a Wave 2 prototype on disk we're hardening, not researching from scratch)
**Companion documents:**
  - `docs/SPRINT_17_PLAN.md` (the long-term strategic plan that Sprint 18 executes wave-by-wave)
  - `docs/SPRINT_17_STATUS.md` (Wave 1 + Wave 2 dashboard)
  - `docs/CLUSTER_B_FEDERATION_SPEC.md` (Cluster B technical spec — B.1 shipped, B.2-B.5 staged)
  - `docs/CLUSTER_C_BYTE_LEVEL_IFC_SPEC.md` (Cluster C technical spec — C-1/C-2 prototype shipped, LIVE bring-up + per-byte mode staged)
**Sources used to build this plan:** tier-1 only, May 26 2026 horizon. The same source set as the Cluster B + Cluster C-2 spec docs is reused; no new WebSearches required because Sprint 18 is execution of already-researched scaffolding.

---

## 0. Strategic context

Sprint 17 Wave 2 shipped 4 prototypes in a single session: C-1 engine + C-1 router integration + C-2 kernel-gate prototype + B.1 SPIFFE trust-bundle sync. The honest-scope ceilings published at that point were:

1. **C-2 LIVE bring-up not done.** The eBPF LSM C file is a skeleton; the Python bridge runs in MOCK on macOS. Real `libbpf` load on Linux ≥ 5.7 is Sprint 18 work.
2. **Cross-domain AIMS envelope verification not done.** Cluster B.1 closes the SPIFFE bundle SYNC half of the federation problem; B.2 closes the AIMS envelope half.
3. **C-2 per-byte mode not done.** Sprint 17 ships per-fd max-color (verifier-trivial); per-byte requires a bounded inner loop over `colors[]` that the verifier accepts only with explicit bound annotations.

Sprint 18 closes #1 and #2 as primary work; #3 is a stretch (deferred if either primary item slips).

**Strategic moat after Sprint 18 ships:**
- Catalog C7 ✅ extends to full **kernel-enforced** byte-level IFC at the agent tool boundary. The v1.2 launch narrative drops "Python-side enforcement only" from the C-1 honest-scope and replaces it with "kernel write-gate live on Linux 5.7+ hosts; MOCK fallback on macOS dev still documented."
- Catalog F4 ✅ extends to full cross-organization AIMS envelope verification. The v1.2 launch narrative changes to "vOS instances in different administrative domains can verify each other's hardware-integrity attestations end-to-end" — the actual product claim a sub-contractor flow needs.
- 37/80 catalog headline still unchanged — Sprint 18 deepens already-✅ items, doesn't close new ones.

---

## 1. Sprint 18 deliverables — 3 items, 2 waves

### Wave 3.A (primary, mandatory) — Cluster B.2: cross-domain AIMS envelope verification

| | |
|---|---|
| **Owner** | Cluster B (Identity Federation) |
| **Catalog Δ** | extends F4 ✅; closes the AIMS half left open by B.1 |
| **Where** | `backend/services/aims_envelope.py` (add `verify_against_partner_bundle()` path) + new `backend/services/aims_partner_verifier.py` (the cross-domain verifier wrapper) |
| **Tests** | `backend/tests/services/test_aims_partner_verifier.py` — target ~25 tests |
| **Dependency** | Cluster B.1 `IdentityFederationBridge` (shipped commit `e9b2730`) |
| **API shape** | `AIMSPartnerVerifier(bridge)` with `.verify(envelope, expected_trust_domain) → AIMSVerificationOutcome`. The verifier consults the bridge's local copy of the partner's bundle, extracts the partner's signing key, and runs the existing AIMS envelope signature check against it. |
| **Honest scope ceiling** | Sprint 18 ships the signature-verification path only. Hardware attestation chain validation (verifying that the partner's RTMR measurements match a hardware policy table on our side) is Sprint 19+ — requires a hardware-policy registry that doesn't exist yet. |
| **Demo target** | E2E test: AIDG vOS instance verifies a real AIMS envelope from a registered partner trust domain using only the bridge's synced bundle keys (no shared signing infrastructure). |

### Wave 3.B (primary, mandatory) — Cluster C-2: LIVE bring-up on Linux 5.7+

| | |
|---|---|
| **Owner** | Cluster C (Byte-level IFC) |
| **Catalog Δ** | converts C-2 prototype from MOCK to LIVE enforcement |
| **Where** | `backend/security/kernel_gate_connector.py` (add ctypes-bound `bpf()` syscall path) + `kernel/src/sec/taint_gate.c` (un-gate the `__VOS3_TAINT_GATE_REAL_BPF_BUILD` guard once the file is verifier-clean) + new `kernel/build/bpf/Makefile` for the clang -target bpf path |
| **Tests** | `backend/tests/security/test_kernel_write_block_live.py` — Linux-only, marked `pytest.mark.skipif(not has_bpffs())` so macOS CI still passes |
| **Dependency** | Linux ≥ 5.7 + libbpf-dev + clang ≥ 11 + bpffs mounted at /sys/fs/bpf |
| **API shape** | The existing `KernelGateConnector` already auto-detects LIVE vs MOCK; Sprint 18 fills in the LIVE bpf() path. No API change visible to callers. |
| **Honest scope ceiling** | LIVE bring-up is per-fd max-color mode only (matches the skeleton). Per-byte mode is the Wave 3.C stretch. |
| **Demo target** | On a Linux 5.7+ host with bpffs mounted: push a TOXIC TaintedBuffer to a fd; attempt write(2); verify the kernel returns `-EPERM`. Without the kernel module loaded, the same test verifies the bridge correctly reports `pushed=False` instead of silently degrading. |

### Wave 3.C (stretch — deferred if primary slips) — Cluster C-2: per-byte mode

| | |
|---|---|
| **Owner** | Cluster C (Byte-level IFC) |
| **Catalog Δ** | Closes the per-byte vs per-fd ceiling on Cluster C-2 |
| **Where** | `kernel/src/sec/taint_gate.c` — add `VOS3_TAINT_MODE_PER_BYTE` branch with a bounded inner loop over `colors[start:end]` |
| **Tests** | Extends `test_kernel_write_block_live.py` with byte-range egress tests |
| **Dependency** | Wave 3.B completed (LIVE bring-up gives us a verifier-passing baseline to mutate) |
| **Honest scope ceiling** | The per-byte loop bound depends on the write `iov` length — verifier needs the bound as a compile-time constant. We'll cap at 4096 bytes per LSM hook invocation and chunk larger writes at the userspace bridge (matching how Wave B.1 chunks 64 KB bundle pushes). |
| **Drop criterion** | If Wave 3.A or 3.B slips into the second half of the sprint window (after 2026-07-15), 3.C is dropped to Sprint 19. |

---

## 2. Execution order + the 'do B.2 first' decision

Order: **B.2 → C-2 LIVE → C-2 per-byte (if time)**.

Reasoning:
- B.2 is software-only and demonstrably testable on this macOS dev host. It can ship from cold start in a single session with the existing IdentityFederationBridge + AIMSEnvelopeVerifier as the integration anchors.
- C-2 LIVE bring-up requires Linux + libbpf + bpffs mount + root for BPF load — none of which are available on the current macOS dev host. We can WRITE the ctypes binding and the clang Makefile from macOS but can't fully validate end-to-end. CI on the GitHub Actions Linux runner can validate the binding (when Actions billing is restored — see §5 below).
- C-2 per-byte mode is a stretch that depends on C-2 LIVE being merged + reviewed first.

---

## 3. Dependencies and risk

| Risk | Mitigation |
|---|---|
| C-2 LIVE bring-up depends on a Linux host that isn't this dev box | Sprint 18 writes the binding from macOS; a separate `test_kernel_write_block_live.py` runs on the Linux CI runner once Actions billing is back |
| GitHub Actions billing still blocked (see Wave 2 handover note) | Sprint 18 work commits + pushes; CI validates when billing is restored. Local pytest is the hard-gate proxy until then |
| AIMS envelope signature scheme — what does the partner sign with? | `aims_envelope.py` Sprint 17 P2 uses Ed25519 by default; we assume cross-domain partners also use Ed25519 for the B.2 MVP. RSA + ECDSA support is Sprint 19+ |
| Per-byte LSM verifier rejection on edge cases | Wave 3.C runs `bpftool prog load --verifier-log` early to catch reject reasons; if the bound annotation dance is too brittle, defer to Sprint 19 with a fresh kernel-toolchain pass |
| Cross-domain test fixtures (partner Ed25519 keypair) | Generate at test setup time via `cryptography.hazmat.primitives.asymmetric.ed25519` — no real network, no real partner needed for the MVP |

---

## 4. Test budget

| Wave | New tests target | Run time budget |
|---|---|---|
| 3.A (B.2) | ~25 | < 15s |
| 3.B (C-2 LIVE) | ~10 (Linux-only) + ~3 (MOCK fallback assertions on macOS) | < 30s on Linux runner |
| 3.C (C-2 per-byte) | ~6 | < 15s on Linux runner |

Cumulative after Sprint 18 (target): **721 + ~44 = ~765 tests across Sprint 15-18.**

---

## 5. Cross-cutting state

### PR #6 (`ci-cleanup`) — STILL BLOCKED on GitHub Actions billing as of 2026-05-26

Same status as the Sprint 17 W2 handover lock notes: cherry-pick of `constraints.txt` to `ci-cleanup` landed cleanly as commit `02230b8`, but every workflow on the affected branches (including `main`) fails in <3 seconds with `BlobNotFound` on the log endpoint — the signature of exhausted Actions minutes. Operator must top up Actions quota before CI re-runs cleanly.

Implication for Sprint 18: local `backend/.venv_p312` pytest is the hard-gate proxy. Every Sprint 18 commit MUST pass the local regression sweep before pushing.

### Cumulative test sweep proxy

The current full Wave 2 sweep (kernel_gate + byte_taint_intake + dual_llm + taint_engine_v2 + ifc_engine + identity_federation_bridge) is the canonical regression set: **167/167 in 12.99s** at commit `275ded2`. Sprint 18 commits add their new test files to this set so each PR's status is the sweep's count + delta.

---

## 6. CEO sign-off matrix

| Decision | Default recommendation |
|---|---|
| Wave 3.A (B.2 cross-domain AIMS verify) — execute now | ✅ approve — software-only, demonstrable on dev host today |
| Wave 3.B (C-2 LIVE) — scaffold from macOS, validate on Linux CI when Actions billing returns | ✅ approve — scaffolding is reviewable locally; runtime validation deferred to Actions runner |
| Wave 3.C (C-2 per-byte) — stretch, drop if primary slips after 2026-07-15 | ✅ approve drop criterion |
| Defer hardware-attestation policy registry (B.2 honest-scope ceiling) to Sprint 19 | ✅ approve — not a v1.2 blocker |
| RSA + ECDSA support in AIMS partner verify (Sprint 19+) | ✅ approve defer |
| GitHub Actions billing top-up (operator action, not engineering) | 🟡 OPERATOR DECISION — flagged to CEO |

---

## 7. Open questions for CEO

| # | Question | Default |
|---|---|---|
| 1 | Sprint 18 window — 8 weeks acceptable or compress to 4? | 8 weeks; 4-week compression risks dropping Wave 3.C and rushing the C-2 verifier dance |
| 2 | Wave 3.B kernel-side review path — does the operator want a separate kernel-engineer review pass before merging, or is the eBPF verifier passing sufficient gate? | verifier-pass + Linux CI green = merge gate; explicit kernel-eng review is Sprint 19+ |
| 3 | Wave 3.A should the cross-domain AIMS verifier emit a separate audit event class, or share the existing G2 OTel envelope verify span? | share the G2 span with a new `aims.partner_trust_domain` attribute |
| 4 | Sprint 18 cadence — same daily-commit pattern as Sprint 17 Wave 2, or batch into weekly drops? | daily commits; preserves the audit trail + lets the handover lock track day-by-day |

---

**Status:** plan drafted; Wave 3.A B.2 prototype lands in the same commit cluster.
