# Phase 29 — Legacy Test Debt Triage (Tier 1 clearance pass)

**Date:** 2026-06-16 · **Baseline:** `20ba153` · **Suite:** `468 audit + 9046 non-audit passed, 47 failed` (pre-triage).

This is the human-review-style triage mandated by Phase 29 Step 2. The strict
rule was applied throughout: **heal ONLY where the test expectation is
objectively outdated vs. a current stable vOS interface; if a failure reflects a
genuine gap, record it as Tier-2 debt and DO NOT delete or weaken the
assertion** (anti-gaming charter, AG-2 / the Phase-23 lesson).

Outcome of this pass: **1 objectively-outdated test healed; 46 left RED on
purpose** and classified below. Forcing the 46 green would require either
gaming (weakening assertions — forbidden) or work outside this phase's scope
(a full kernel rebuild, an INV-1 lift, or live infra). None were weakened.

---

## A. Healed — objectively-outdated expectation (1)

| Test | Why it was outdated | Heal (intent preserved / strengthened) |
|---|---|---|
| `test_kernel_compiler.py::TestCompilerConfig::test_vos3_root_exists` | Asserted `"VOS3" in str(VOS3_ROOT)` — coupled to a checkout dir literally named "VOS3". The canonical repo dir is `vos.v1`, so the substring check was environment-brittle, not a real invariant. | Replaced with the assertion the test name + comment actually describe: `VOS3_ROOT` exists, is a dir, and contains the `kernel/` tree. Strictly stronger. |

No security boundary, status code, or structural symbol assertion was relaxed.

---

## B. Tier-2 — GENUINE GAPS (verified by inspection; MUST NOT be gamed) (14)

These tests assert a desired source/kernel state that **does not exist**. The
tests are correct; the code is incomplete. Several target `kernel/src/mm/`
(INV-1, untouchable here) or require a full kernel rebuild — out of scope for a
test-debt pass. Leaving them RED is the honest signal.

- **Open-core split incomplete** — `test_open_core_split.py` (2): asserts
  `backend/services/finetune_engine.py` was `git mv`'d to `backend/pro/` and that
  `test_finetune_rigor.py` imports from the new path. The old file still exists;
  the relocation never completed. Genuine refactor debt (**Tier-2 / T2-OC1**).
- **Kernel Makefile / source features not wired** — `test_finetune_rigor.py` (5:
  `makefile_compiles_npu_ops`, `mmr_finetune_step_*`, `tpm_seal_adapter_pcr_11`,
  `vos3_pud_size_1gib`), `test_vmm_security.py` (3: `makefile_includes_v205_sources`,
  `wx_violation_calls_quarantine_handler`, `dispatcher_has_task_steering_logic`).
  The Makefile does not list `ai/npu_ops.c` / `sec/slot_state.c`; the asserted
  symbols are not present in the built sources (**Tier-2 / T2-KBUILD**).
- **VMM slot-expansion API behind `VOS3_PRO` / unbuilt** — `test_memory_scaling.py`
  (4: `vmm_expansion_api_exists`, `slot_isolation_via_static_va_base`,
  `expansion_uses_pmm_alloc_huge`, `expansion_caps_at_10gb_ceiling`). The
  `vos3_vmm_expand_slot_memory` signature lives under `#ifdef VOS3_PRO` in
  `kernel/src/mm/vmm.c` — **INV-1 territory**; cannot be remediated here
  (**Tier-2 / T2-VMM**, INV-1-gated).

> Anti-gaming note: none of these grep/structural assertions were edited. Closing
> them requires real engineering (complete the relocation; wire the Makefile
> sources; an INV-1 owner decision for the `mm/` VMM API), not a test edit.

---

## C. Tier-2 — ENVIRONMENT / LIVE-INFRA DEPENDENT (32)

These fail in the hermetic dev sandbox because they need a running backend, a
live LLM/Convex/Stripe endpoint, network egress, or stateful ordering — **not**
because of a vOS code defect. Signature: `httpcore.ConnectError: All connection
attempts failed` / HTTP 500 from an unreachable provider, or shared-limiter
state. They were **left untouched** (adding skip markers would mask, not
resolve, and reads as gaming).

- Chat/codegen request path → live LLM: `test_api_terminal_extended.py` (6),
  `test_api_terminal.py` (1).
- Full-stack lifecycle (need running services): `test_full_stack_integration.py`
  (4), `test_api_billing.py` (4), `owasp/test_owasp_bola.py` (1),
  `test_api_compliance_firewall_integration.py` (1),
  `integration/test_middleware_chain.py` (1).
- Rate-limiter / timing / shared-state: `owasp/test_owasp_rate_limit.py` (4).
- Load + security harness needing infra: `test_load_security.py` (4),
  `red_team/test_hostile_tenant_leak.py` (1), `security/test_dns_pinning.py` (2),
  `test_v32_stability.py` (2), `perf/test_memory_leaks.py` (1).

> Classification basis: clusters B (`test_open_core_split`, `test_finetune_rigor`,
> `test_vmm_security`, `test_memory_scaling`, `test_kernel_compiler`) and the
> chat-path cluster were verified by direct inspection of the failure output;
> the remaining infra entries were classified by failure signature/cluster. None
> were modified. A follow-up pass should run these under a live-infra CI lane.

---

## Disposition

- **Healed:** 1 (objectively outdated).
- **Tier-2 genuine gaps:** ~14 (T2-OC1, T2-KBUILD, T2-VMM) — real work, INV-1/
  build-gated; NOT gamed.
- **Tier-2 environment/infra:** ~32 — need a live-infra CI lane.
- **Moat impact:** none. This pass does not advance the 49/80 tally.
