# vOS Engine Assets — Porting Feasibility Analysis (3 assets)

**Date:** 2026-06-09 · **Source:** `/Users/sz/Desktop/vos` (vos4 Engine + track-1 worktree)
**Target:** `vos.v1` @ `2fc51f1` (`feat/shield-integration`, core gate 931/931) · **Type:** read-only audit (no copies, no code changes)

> **Confidence scope:** verifies size, completeness, import/include resolution,
> overlap signals, and collisions — i.e. whether each asset *builds/imports*
> cleanly into vos.v1. It does NOT prove full semantic correctness, clean QEMU
> boot, or runtime equivalence. "Resolves cleanly" ≠ "verified-correct in vos.v1."

## Summary index
| # | Asset | Lines | Stubs | Internal deps | In vos.v1? | Collision | **Risk** |
|---|-------|------:|------:|---------------|-----------|-----------|----------|
| 1 | `kernel/src/core/cr4_pin.c` (+`.h`) | 86 | **0** | `console.h`, `arch/x86_64/cpu.h` → **both present** | absent (new) | none | **LOW** |
| 2 | `kernel/src/core/smap_smep.c` (+`.h`) | 87 | **0** | `console.h`, `arch/x86_64/cpu.h` → **both present** | absent (new) | **overlap** (see §2) | **LOW–MED** |
| 3 | `backend/services/semantic_firewall.py` (track-1) | 489 | 0 (the "1 marker" is a docstring `...`) | **stdlib only** (logging/dataclass/enum/typing) | absent | none | **LOW** + ships its **393-line test** |

**Universal:** kernel files affect only the kernel build (not the Python 931
gate); semantic_firewall is inert until imported. A pure copy of all 3 is
**931-gate-safe** — risk lives in *wiring* (kmain init / request-path mount).

---

## 1. `cr4_pin.c` — LOW
- **Metrics:** 86 lines, **0 stubs.** Funcs: `vos3_cr4_pin_init()`, `vos3_cr4_pin_verify()`, `vos3_cr4_pinned_write()`, `vos3_cr4_pin_get_mask()` + static `read_cr4`/`write_cr4_raw`.
- **What it does:** reads current CR4, **pins only the already-set candidate bits** (incl. CET) into a mask, so subsequent `pinned_write()` re-ORs them — **anti-rollback hardening** (an attacker can't clear SMEP/SMAP/UMIP/CET via a crafted CR4 write). `verify()` returns missing pinned bits (tamper detection). Defensive: never force-enables an unsupported feature.
- **Deps:** `cr4_pin.h` (port together) + `vos/console.h` ✅ + `arch/x86_64/cpu.h` ✅ — all present in vos.v1.
- **Overlap:** vos.v1 has `cet.c` (sets `CR4.CET`) but **no cr4-pin** — cr4_pin is **complementary** (pins what cet/smap set). No conflict.
- **Modernization delta:** none (freestanding C). Wiring: add to `kernel/Makefile` C_SOURCES + call `vos3_cr4_pin_init()` in `kmain` **after** CET/SMAP/SMEP setup (so it pins the final state).
- **Gate risk:** LOW. Kernel-only; doesn't touch the 931 backend gate. Verify via a `~~CERT~~` boot assertion (`vos3_cr4_pin_verify() == 0` post-init) + QEMU boot.

## 2. `smap_smep.c` — LOW–MEDIUM (overlap to resolve)
- **Metrics:** 87 lines, **0 stubs.** Funcs: `vos3_smap_smep_init()`, `vos3_smap_smep_get_caps()`. Struct `vos3_smap_smep_caps_t`.
- **What it does:** CPUID leaf-7 EBX detect → conditionally **sets `CR4.SMEP`/`CR4.SMAP` enable bits** (skips if unsupported), tracks caps. Defensive.
- **Deps:** `smap_smep.h` + `console.h` ✅ + `arch/x86_64/cpu.h` ✅ — present.
- **⚠️ Overlap (the audit's key finding):** vos.v1 ships **SMAP *usage*** — 11 inline `stac`/`clac` in user-copy paths (per CLAUDE.md) — but the audit found **no explicit `CR4.SMAP`/`CR4.SMEP` *enablement* site** in vos.v1 boot (only `CR4.CET` in `cet.c`; the `multiboot2_entry.S 1<<21` is an EFLAGS.ID CPUID probe, not CR4.SMAP). So smap_smep.c looks **complementary** (it would add the enablement the inline stac/clac assume) — **NOT a duplicate**. **BUT must confirm** there is no other enable site (uefi_boot / arch init) before porting, or risk a **double-init / divergent-logic** merge vector.
- **Modernization delta:** none. Wiring: C_SOURCES + `vos3_smap_smep_init()` in `kmain` (before cr4_pin so the bits are set, then pinned).
- **Gate risk:** LOW–MEDIUM. If vos.v1 already enables CR4.SMAP elsewhere → redundant/conflicting; if not → genuine hardening gap closed. **Action: locate vos.v1's CR4.SMAP enablement (if any) first.** Verify via boot `~~CERT~~` (`caps.smap_enabled`/`smep_enabled`) + ensure user-copy stac/clac still pass.

## 3. `semantic_firewall.py` — LOW (high relevance)
- **Metrics:** **489 lines, complete** (the single "stub marker" is a docstring example `{"source": "web_search", ...}` at L399, not code). Surface: `SemanticFirewallDecision(Enum)`, `SemanticFirewallResult` (dataclass), `SemanticFirewallDenied(Exception)`, `scan(text, context)`, `_corpus_match()`.
- **What it does:** a **semantic / prompt-injection firewall** — scans agent/LLM input text against a corpus and returns an allow/deny decision. Maps directly to the **owasp / C-category (prompt-injection)** gap cluster.
- **Deps:** **stdlib only** (`logging`, `dataclasses`, `enum`, `typing`) — **zero internal deps**, resolves trivially under `.venv_p312` (CPython 3.12). No collision in vos.v1.
- **Bonus:** track-1 ships **`test_semantic_firewall.py` (393 lines)** — port it alongside for ready-made coverage.
- **Modernization delta:** none. Activation: import + call `scan()` at the agent-input boundary (e.g. in the chat/codegen pre-flight, alongside `outbound_pii_shield`). **Do not** force it into a global middleware without review.
- **Gate risk:** LOW. Inert until imported. Wiring it into the request path is the risk surface — gate behind tests (its own 393-line suite + an integration test).

---

## Recommended porting order
1. **`semantic_firewall.py` + its test** (LOW, zero-dep, ships coverage, high security relevance) — cleanest, like the Cyber-Shield ports.
2. **`cr4_pin.c`** (LOW, new, complete, complementary) — anti-rollback hardening; wire `init()` after CET/SMAP in kmain; boot-cert verify.
3. **`smap_smep.c`** (LOW–MED) — **only after** confirming vos.v1 has no existing CR4.SMAP enablement; if none, it closes a real enablement gap.

## Gate-safety statement
Copying all 3 (unwired) is 931-gate-safe (kernel C doesn't touch the backend gate; the firewall is inert until imported; no collisions; deps resolve). Activation requires per-asset tests: **boot `~~CERT~~` assertions** for the two kernel files (verify pinned/enabled bits, clean QEMU boot) and the **393-line firewall test + an integration test** for semantic_firewall. The smap_smep overlap check is a hard pre-req.

## Verification (reproduce)
- `wc -l` + `grep -cE 'TODO|NotImplemented|stub'` on each.
- `grep -nE '#include|^(from|import)'` for deps; cross-check headers in `vos.v1/kernel/include`.
- Overlap: `grep -rn 'CR4.*SMAP|CR4.*SMEP|cr4 |=' vos.v1/kernel/src` (find any existing enablement).
- Collision: `find vos.v1/backend vos.v1/kernel -name '<n>'`.
