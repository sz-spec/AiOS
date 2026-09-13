# ADR-001: Decomposition of `efficiency.py` God Module

## Status
Proposed

## Context
The file `backend/src/efficiency.py` has grown to ~760 lines and currently mixes multiple concerns in a single module:

- Smart Router initialization and model assignment (`assign_model*`)
- Configuration and state types (`EfficiencyConfig`, `EfficientState`)
- Cross-cutting middleware (`error_middleware`, `retry_middleware`)
- LLM utilities (quantization/embeddings setup)
- Retrieval (`HybridRetriever`)
- Context orchestration (`ContextManager`)
- Workflow building (`build_efficient_workflow`)
- Call reduction/caching (`LLMCallReducer`)

This creates the following architectural and engineering issues:

1. **High coupling / low cohesion**: unrelated concepts change together and share internal helpers.
2. **Testing difficulty**: unit tests must import a heavyweight module and mock many unrelated dependencies.
3. **Change risk**: small edits can introduce regressions in unrelated areas due to shared state/import ordering.
4. **Cognitive load**: onboarding and maintenance require understanding a large, multi-domain file.
5. **Import hygiene problems**: single-file “utility” modules often become dependency magnets and create circular import pressure over time.

This ADR focuses only on decomposing `efficiency.py` into cohesive modules while preserving behavior and public API, using patch-only changes (no functional rewrite).

## Decision
Decompose `backend/src/efficiency.py` into a package `backend/src/efficiency/` organized by responsibility, and preserve backward compatibility by re-exporting the legacy public surface from `backend/src/efficiency/__init__.py`.

Key decision points:

- **Package-based decomposition**: Convert from a single module to a package to support submodules and clear boundaries.
- **Cohesion-first grouping**: Split along stable responsibilities (router/state/middleware/llm/retrieval/context/workflow).
- **Backward compatibility guarantee**: Existing imports (`from backend.src.efficiency import X`) must continue to work unchanged during migration.
- **Patch-only**: Move code with minimal edits, keeping names and semantics stable; avoid opportunistic refactors.

## Proposed Module Structure
```
backend/src/
├── efficiency/
│   ├── __init__.py           # Re-exports for backward compatibility
│   ├── router.py             # SmartRouter setup, assign_model, assign_model_with_tracking
│   ├── state.py              # EfficientState, state helpers (e.g., create_initial_state if applicable)
│   ├── middleware.py         # error_middleware, retry_middleware
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── quantized.py      # create_quantized_llm (+ embeddings factory if present)
│   │   └── reducer.py        # LLMCallReducer
│   ├── retrieval/
│   │   ├── __init__.py
│   │   └── hybrid.py         # HybridRetriever
│   ├── context.py            # ContextManager
│   └── workflow.py           # EfficiencyConfig, build_efficient_workflow
```

### Boundary Guidelines (to prevent a “distributed god module”)
- `router.py` must not import `retrieval` or `context` (router concerns only).
- `workflow.py` may import `router`, `retrieval`, `context`, `llm.*`, and `state` (composition root).
- `state.py` should contain types and small pure helpers only (no heavy imports).
- `middleware.py` should remain framework-agnostic unless it is explicitly tied to one framework already.
- `llm/quantized.py` should not depend on `workflow.py` (avoid upward dependency).
- `retrieval/hybrid.py` should not import router/workflow (retrieval is a service component).

## Alternatives Considered
1. **Keep as-is**  
   - Rejected: does not address coupling, testability, or long-term maintainability.

2. **Split into two files only (e.g., `efficiency_core.py` + `efficiency_utils.py`)**  
   - Rejected: still too coarse; tends to re-grow into a second god module.

3. **Rewrite into a new API and deprecate old names immediately**  
   - Rejected: higher risk and violates “patch-only changes” requirement; would break external consumers.

4. **Introduce a service layer (DI container) first, then split**  
   - Deferred: potentially valuable, but adds complexity and is not required for initial decomposition.

## Consequences

### Positive
- **Improved cohesion and clarity**: each module has a single responsibility.
- **Better testability**: unit tests can target `router`, `retrieval`, `llm`, etc. without importing everything.
- **Reduced change risk**: edits become localized; fewer accidental side effects.
- **Easier code ownership**: teams can own subpackages (e.g., retrieval vs. LLM utilities).
- **Cleaner dependency graph**: enables future improvements (e.g., interface abstractions, DI) with fewer circular imports.

### Negative / Tradeoffs
- **Short-term churn**: many moved symbols and import paths internally; requires careful review.
- **Potential circular import exposure**: decomposition can reveal hidden coupling that was previously masked.
- **More files to navigate**: slightly higher overhead for small changes (mitigated by clear structure).
- **Need for compatibility layer**: `__init__.py` re-exports can obscure where definitions live (acceptable as a transition tool).

## Migration Path (Backward-Compatible, Patch-Only)
1. **Create package skeleton**
   - Add `backend/src/efficiency/` directory with required `__init__.py` files.
   - Ensure `backend/src/efficiency.py` is either removed or replaced safely (see step 4).  
     *Note: in Python, a package and module with the same name conflict; the migration must end with one canonical location.*

2. **Move code blocks verbatim**
   - Copy/paste sections from `efficiency.py` into the appropriate new modules:
     - router → `router.py`
     - config/workflow → `workflow.py`
     - middleware → `middleware.py`
     - state types → `state.py`
     - retriever → `retrieval/hybrid.py`
     - context manager → `context.py`
     - quantization/embeddings → `llm/quantized.py`
     - call reducer → `llm/reducer.py`
   - Keep names, signatures, and runtime behavior unchanged.

3. **Fix internal imports**
   - Update imports inside the moved code to use relative imports within the package (e.g., `from .state import EfficientState`).
   - Keep heavyweight imports (LLM libs, retriever deps) inside the modules that need them to reduce import-time cost.

4. **Preserve external imports via re-exports**
   - Implement `backend/src/efficiency/__init__.py` to re-export the public surface area currently relied upon:
     - `assign_model`, `assign_model_with_tracking`
     - `EfficiencyConfig`, `EfficientState`
     - `error_middleware`, `retry_middleware`
     - `create_quantized_llm`
     - `HybridRetriever`, `ContextManager`
     - `build_efficient_workflow`
     - `LLMCallReducer`
   - If the codebase currently imports `backend.src.efficiency` as a module, finalize the migration by:
     - **Option A (preferred)**: delete/rename the original `backend/src/efficiency.py` so the package is authoritative.
     - **Option B (transition-only)**: keep `backend/src/efficiency.py` as a thin shim that imports and re-exports from the package, *but only if naming conflicts are handled*. In practice, you cannot have both `efficiency.py` and `efficiency/` as peers in the same package without ambiguity—so the shim should be placed under a different name (e.g., `efficiency_legacy.py`) or the package should become canonical.

5. **Update internal imports incrementally**
   - Within the repository, update imports to prefer the new package submodules (e.g., `from backend.src.efficiency.router import assign_model`).
   - Keep external compatibility via `from backend.src.efficiency import assign_model` working.

6. **Add module-level tests**
   - Add focused tests for each module:
     - `test_router.py`, `test_middleware.py`, `test_retrieval_hybrid.py`, etc.
   - Ensure imports work without initializing unrelated components.

7. **Deprecation plan (optional, later ADR)**
   - After internal adoption, consider deprecating star re-exports in `__init__.py` in favor of explicit submodule imports.

## Implementation Notes
- **Re-export strategy**
  - `backend/src/efficiency/__init__.py` should explicitly import and expose the known public API to avoid accidental exports.
  - Use `__all__` to define the supported surface area.

- **Avoid import-time side effects**
  - Smart Router initialization currently present in `efficiency.py` should be reviewed:
    - Prefer lazy initialization or factory functions in `router.py` to prevent importing `backend.src.efficiency` from triggering network/config side effects.
    - If side effects are required today, preserve behavior in the first patch and schedule a follow-up hardening.

- **Type ownership**
  - Put `EfficiencyConfig` and `EfficientState` in stable locations (`workflow.py` and `state.py` respectively), and have other modules import them rather than re-defining.

- **No functional changes**
  - This ADR explicitly does *not* address:
    - permissive CORS in `backend/main.py`
    - weak password hashing and in-memory user storage in `backend/core/control_plane.py`
    - unused legacy database integration
  Those are separate concerns and should be tracked via separate ADRs/issues.

## Acceptance Criteria
- The application runs with no behavior changes attributable to the refactor.
- All existing imports of symbols from `backend.src.efficiency` continue to work.
- Each decomposed module can be imported independently without pulling in unrelated heavy dependencies.
- Unit tests can target router/middleware/retrieval/llm independently.
- `efficiency.py` no longer exists as a 760-line god module; responsibility is distributed across the package.

--- 

If you want, I can also provide a concrete patch plan (file-by-file move order + exact `__init__.py` re-export list) tailored to the current symbol names found in your `efficiency.py`.