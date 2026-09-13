# vOS — Missing Source Assets Discovery (7 residual application failures)

**Generated:** 2026-06-08 · **Mode:** read-only investigation (no source/test/config mutated)
**Subject:** the 7 localized failures — `smart_routing` ×1, `efficiency` ×1, `open_core_split` ×5
**Branch context:** `feat-m3-ed25519-verify` (parent `main` @ `fd74753`)
**Sibling repos crawled:** `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3`, `…/VOS3-Cyber`

> **Framing (non-negotiable):** none of these are moat rows; none are
> test-infrastructure bugs. They are real product/config/source state plus one
> missing dev dependency. Two would require editing a **test file** (out of scope
> for this read-only pass) and are documented, not actioned. **Moat stays 49/80.**
> This brief is fact-only: every row was verified with `find`/`grep`/`ls`.

---

## A. Routing failures (2) — single root cause: `smart_router` package absent

**Verified:** `python -c "import smart_router"` → **ModuleNotFoundError: No module
named 'smart_router'**. `CLAUDE.md` sources it from `/Users/snirzano/shared-ai-router`,
which **does not exist on this host** (that path is a different user's home; this
host is `sz`). `backend/src/efficiency/router.py:12` does `try: from smart_router
import SmartRouter / except: SmartRouter = None`, so `SmartRouter` is `None` and
`assign_model` (line 207) takes the **fallback branch** (line 242:
`selected = _FALLBACK_MODELS.get(role, "claude-sonnet")`).

| Failure | Path evaluated | Present/Absent | Concrete delta |
|---|---|---|---|
| `test_smart_routing.py::test_assign_model_unknown_role_raises` | `backend/src/efficiency/router.py::assign_model` | fallback returns a default, **never raises** | Only the real `SmartRouter` raises `KeyError` on an unknown role. **Install `shared-ai-router`** into `.venv_p312`. No source change. |
| `test_efficiency.py::test_with_smart_router` | `backend/src/efficiency/__init__.py` (re-exports `assign_model` from `.router`) | patches `src.efficiency.router`, expects `"claude-opus"`; fallback yields `"claude-sonnet"` | Same root cause — needs `smart_router` importable so the SmartRouter path (not the fallback) runs. |

**Delta (both):** provide the `shared-ai-router` package on this host (vendor into
the repo, or `pip install` from a present path) so `import smart_router` succeeds.
Pure missing dev-dependency; **no application code change required.**

---

## B. `open_core_split` (5) — structural source/config deltas

| Failure | Path(s) evaluated | Present/Absent | Concrete delta to satisfy natively |
|---|---|---|---|
| `test_finetune_engine_relocated_to_backend_pro` | `backend/pro/finetune_engine.py` (**PRESENT**; header has `LicenseRef-VOS3-Pro-Proprietary` + `PROPOSED`), `backend/pro/__init__.py` (**PRESENT**), `backend/services/finetune_engine.py` (**PRESENT — must be absent**) | move was **duplicated, not moved** | `git rm backend/services/finetune_engine.py` (the old location must not exist). |
| `test_finetune_engine_imported_from_new_path` | `backend/tests/test_finetune_rigor.py` (imports `from services.finetune_engine` ×6) | old import path | Change imports → `from pro.finetune_engine`. **TEST-FILE edit — out of scope here; documented only.** Note: this is also the root cause of the 5 `test_finetune_rigor` broad-suite failures. |
| `test_license_check_implementation_is_honest_stub` | `kernel/src/pro/license_check.c` (**PRESENT**, 20 KB; has `vos3_pro_license_check(`, `vos3_pro_build_label(`, `#ifdef VOS3_PRO`) | open-core **charter line MISSING** | Add the charter statement asserting what PRO does NOT disable — the test requires the file to contain `vos3_vmm_cas_pte` **or** the word "infrastructure". Both currently absent. |
| `test_makefile_supports_build_type_flag` | `kernel/Makefile` (has `VOS3_BUILD_TYPE`; `ifeq ($(VOS3_BUILD_TYPE),PRO)` @345; `-DVOS3_PRO` @346; `pro/license_check.c` @169) | **CORE branch MISSING** | Add an explicit `ifeq ($(VOS3_BUILD_TYPE),CORE)` branch (only the PRO branch exists today). |
| `test_pmm_hugepage_pool_size_is_pro_gated` | `kernel/src/mm/pmm.c` (line 49: `#define VOS3_HUGEPAGE_POOL_MAX 128`, flat/unconditional) | **pro-gating ABSENT** | Replace the flat define with: `#ifdef VOS3_PRO` → `#define VOS3_HUGEPAGE_POOL_MAX 5120` / `#else` → smaller ceiling (e.g. `128`) / `#endif`. No `#ifdef VOS3_PRO` or `5120` present today. |

---

## C. Sibling repositories (reference-copy check)

Both `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3` and `…/VOS3-Cyber` **exist**, but:
- **No** `finetune_engine.py` in either `backend/pro/` or `backend/services/`.
- **No** pro-gated `pmm.c` (no `VOS3_HUGEPAGE_POOL_MAX 5120` / `#ifdef VOS3_PRO`).

→ **No reference implementation to import/copy from.** Any deltas above must be
authored in `vos.v1` directly.

---

## D. Summary inventory

| # | Failure | Category | Delta type | Blocked-by-hard-bound? |
|---|---|---|---|---|
| 1 | smart_routing unknown-role-raises | missing dep | install `shared-ai-router` | no (env, not source) |
| 2 | efficiency with_smart_router | missing dep | install `shared-ai-router` | no (env) |
| 3 | finetune relocated | incomplete `git mv` | remove `backend/services/finetune_engine.py` | no (file delete) |
| 4 | finetune import path | stale test import | edit `test_finetune_rigor.py` | **yes — test file** |
| 5 | license_check charter | missing source line | add charter to `kernel/src/pro/license_check.c` | no (source) |
| 6 | Makefile build-type | missing CORE branch | add `ifeq(...,CORE)` to `kernel/Makefile` | no (config) |
| 7 | pmm hugepage pro-gate | missing `#ifdef` block | pro-gate `VOS3_HUGEPAGE_POOL_MAX` in `kernel/src/mm/pmm.c` | no (source) |

**Net:** 2 are a single missing dev-dependency (`smart_router`); 4 are concrete
1-spot source/config/file deltas authorable in `vos.v1`; 1 requires a test-file
edit (out of scope here). None affect the moat (49/80). All deltas are exact and
verified against the live filesystem on 2026-06-08.

## Verification
Re-run the probes used to compile this brief:
- `python -c "import smart_router"` (expect ModuleNotFoundError today)
- `ls backend/pro/finetune_engine.py backend/services/finetune_engine.py`
- `grep -n "VOS3_HUGEPAGE_POOL_MAX" kernel/src/mm/pmm.c`
- `grep -nE "vos3_vmm_cas_pte|infrastructure" kernel/src/pro/license_check.c`
- `grep -nE "ifeq \(\$\(VOS3_BUILD_TYPE\),CORE\)" kernel/Makefile`
- `grep -n "from .*finetune_engine" backend/tests/test_finetune_rigor.py`

After any future fix, confidence check: the 7 tests under `-p no:xdist`.
