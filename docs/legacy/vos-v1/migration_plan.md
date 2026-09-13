# VOS4 Migration Plan — Q2 2026 Dependency Refresh + WASM Agent Isolation

**Authored:** 2026-05-03
**Scope:** Frontend (Next.js, Monaco), Backend (FastAPI, LangChain stack, CrewAI), and WASM-based isolation for the AI agent runtime currently sandboxed by Linux rlimits.
**Audience:** A VOS4 engineer with the repo checked out at `/Users/sz/Desktop/vos/vos4` and the existing `.venv/` present.

---

## 1. Current state snapshot

### 1.1 Versions

| Package                  | Currently installed | Target stable (released ≤ 2026-05-03) | Released    | Source |
|--------------------------|---------------------|----------------------------------------|-------------|--------|
| `next`                   | 16.2.4              | **16.2.4** (HOLD — see notes)          | —           | [vercel/next.js releases](https://github.com/vercel/next.js/releases) |
| `eslint-config-next`     | 15.0.0              | **16.2.4** (align with `next`)         | —           | [npm: eslint-config-next](https://www.npmjs.com/package/eslint-config-next) |
| `monaco-editor`          | 0.55.1              | **0.55.1** (HOLD — no stable since)    | —           | [microsoft/monaco-editor releases](https://github.com/microsoft/monaco-editor/releases) |
| `fastapi`                | 0.136.1             | **0.136.1** (already current)          | 2026-04-23  | [PyPI: fastapi](https://pypi.org/project/fastapi/) |
| `langchain`              | 1.2.17              | **1.2.17** (already current; 2.x not GA)| 2026-04-30 | [langchain-ai/langchain releases](https://github.com/langchain-ai/langchain/releases) |
| `langchain-core`         | 1.3.2               | **1.3.2** (already current)            | 2026-04-30  | [docs.langchain.com release policy](https://docs.langchain.com/oss/python/release-policy) |
| `langgraph`              | 1.1.10              | **1.1.10** (1.2.x still alpha)         | —           | [langchain-ai/langgraph releases](https://github.com/langchain-ai/langgraph/releases) |
| `crewai`                 | 1.6.1               | **1.14.4**                             | 2026-04-30  | [crewAIInc/crewAI releases](https://github.com/crewAIInc/crewAI/releases) |
| `anthropic`              | 0.97.0              | (not in scope — no upgrade required)   | —           | — |
| `openai`                 | 2.33.0              | (not in scope — no upgrade required)   | —           | — |

### 1.2 Unresolved transitive vulnerabilities

| Advisory                 | Package (path)                                | Severity | Status |
|--------------------------|-----------------------------------------------|----------|--------|
| `GHSA-crv5-9vww-q3g8`, `GHSA-v9jr-rg53-9pgp` | `dompurify@3.2.7` (bundled in `monaco-editor@0.55.1`) | Moderate | Unresolved — no monaco stable carries `dompurify >= 3.3.4` yet |
| `GHSA-qx2v-qp2m-jg93`    | `postcss@8.4.31` (bundled in `next@16.2.4`)   | Moderate | Unresolved — `postcss 8.5.10` lands in `next@16.3.0` (still canary as of 2026-05-03) |

`npm overrides` in `frontend/package.json` cannot rewrite *bundled* (vendored) postcss inside `next/dist/...`. The fix has to ride an upstream release.

---

## 2. NPM upgrades (frontend)

> Run from `/Users/sz/Desktop/vos/vos4/frontend`.

### 2.1 No-op confirmation for already-current packages

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm ls next monaco-editor eslint eslint-config-next
# Expected: next@16.2.4, monaco-editor@0.55.1, eslint@9.39.4, eslint-config-next@15.0.0
```

### 2.2 Align `eslint-config-next` to the `next` major

`eslint-config-next@15` against `next@16` produces lint config drift; bump:

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm install --save-dev eslint-config-next@16.2.4
```

If npm complains about peer-deps (eslint 9.x vs config expecting eslint 8.x):

```bash
npm install --save-dev eslint-config-next@16.2.4 --legacy-peer-deps
```

### 2.3 Hold `next` at 16.2.4 — DO NOT upgrade to canary

As of 2026-05-03 the only published builds beyond `16.2.4` are pre-release `16.3.0-canary.0` through `16.3.0-canary.9`. Source: [vercel/next.js releases](https://github.com/vercel/next.js/releases). `canary.6` (2026-04-30) carries the [`chore: bump postcss to 8.5.10`](https://github.com/vercel/next.js/releases) commit, but a canary build is not acceptable for production. **Wait for the `16.3.0` GA tag.** Re-check this command weekly:

```bash
npm view next dist-tags  # look for "latest" advancing past 16.2.4
```

When `16.3.0` ships stable:

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm install next@16.3.0 eslint-config-next@16.3.0
npm audit                 # postcss advisory should disappear
```

### 2.4 Hold `monaco-editor` at 0.55.1 — DO NOT upgrade

`0.56.0` is dev-only (last dev: `0.56.0-dev-20260211`). Source: [microsoft/monaco-editor releases](https://github.com/microsoft/monaco-editor/releases). No published changelog references a `dompurify@3.3.4+` bump. Track issue [microsoft/monaco-editor#5248](https://github.com/microsoft/monaco-editor/issues/5248).

### 2.5 Fallback if upstream stalls past 2026-05-31 — `patch-package`

If `next@16.3.0` GA has not shipped by 2026-05-31, patch the bundled postcss in place:

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm install --save-dev patch-package postinstall-postinstall

# Replace the vendored postcss with a known-good copy
node -e "const fs=require('fs');const p=require.resolve('next/dist/compiled/postcss/package.json');console.log(p)"
# Then download postcss 8.5.10 source and overwrite next/dist/compiled/postcss/*

npx patch-package next
# Commits the diff to ./patches/next+16.2.4.patch
```

Add to `frontend/package.json`:

```json
"scripts": {
  "postinstall": "patch-package"
}
```

For `monaco-editor`, the equivalent patch flow is to overwrite `node_modules/monaco-editor/esm/vs/base/browser/dompurify/dompurify.js` with [DOMPurify 3.3.4](https://github.com/cure53/DOMPurify/releases) and run `npx patch-package monaco-editor`. **Test the editor's HTML preview rendering thoroughly** before shipping a DOMPurify patch — the API surface is stable across 3.x but Monaco bundles a custom subset.

### 2.6 Verification

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm install
npm audit
npm run lint
npm run type-check
npm run build
```

---

## 3. PIP upgrades (backend, Python 3.12 venv)

> The venv lives at `/Users/sz/Desktop/vos/vos4/.venv`. All commands use the venv's `pip` directly so they are independent of which shell is active.

### 3.1 Snapshot before changes

```bash
cd /Users/sz/Desktop/vos/vos4
.venv/bin/pip freeze > /tmp/vos4-pip-pre-upgrade.txt
```

### 3.2 FastAPI — already at latest

`fastapi@0.136.1` (released 2026-04-23) is the latest stable. Source: [PyPI: fastapi](https://pypi.org/project/fastapi/). No action required. If you want to be explicit:

```bash
.venv/bin/pip install --upgrade "fastapi==0.136.1"
```

Note: `0.136.x` enabled strict `Content-Type` checking by default for JSON requests. VOS4 already sets `application/json` on all internal callers, but if external clients break, opt out per-route with `strict_content_type=False`.

### 3.3 LangChain ecosystem — stay on 1.x; do NOT chase `1.3.0a1`/`1.4.0a2`

The 2.x line is **not GA** as of 2026-05-03. The stable head is `langchain==1.2.17` (2026-04-30) with `langchain-core==1.3.2` and `langgraph==1.1.10`. Source: [LangChain release policy](https://docs.langchain.com/oss/python/release-policy). VOS4 is already on these. Sanity-pin them:

```bash
.venv/bin/pip install --upgrade \
  "langchain==1.2.17" \
  "langchain-core==1.3.2" \
  "langgraph==1.1.10"
```

If the resolver complains about `langchain-community`, also bump:

```bash
.venv/bin/pip install --upgrade "langchain-community>=1.0.0,<2.0.0"
```

### 3.4 CrewAI — upgrade 1.6.1 → 1.14.4

`crewai@1.14.4` shipped 2026-04-30. Source: [crewAIInc/crewAI releases](https://github.com/crewAIInc/crewAI/releases).

```bash
.venv/bin/pip install --upgrade "crewai==1.14.4"
```

**Breaking changes from 1.6.1 → 1.14.4** (per [docs.crewai.com/en/changelog](https://docs.crewai.com/en/changelog)) that affect VOS4:

1. **`CodeInterpreterTool` removed** (1.14.0a4). VOS4's `backend/ai/agents/` must not import it. Search before upgrading:

   ```bash
   grep -rn "CodeInterpreterTool\|code_interpreter" /Users/sz/Desktop/vos/vos4/backend
   ```

   If hits exist, replace with the E2B/Daytona sandbox integration that 1.14.3 introduced — or, better, route those calls through the new WASM sandbox in §5.

2. **`UserMemory` system removed.** Search:

   ```bash
   grep -rn "UserMemory\|user_memory" /Users/sz/Desktop/vos/vos4/backend
   ```

   Migrate to the hierarchical memory introduced in 1.12.0 (`root_scope` parameter).

3. **Delegation disabled by default** (1.14.x). If any agent in `backend/ai/agents/multi_agent.py` relies on auto-delegation, set `allow_delegation=True` explicitly.

4. **`force_delegation` renamed** in 1.14.4 to avoid self-routing. Audit any explicit references.

5. **LangChain dependency removed from CrewAI core** (since 0.60.0). VOS4 still uses LangChain directly so this is a non-issue, but be aware that `crewai.agents.parser` no longer re-exports langchain types.

### 3.5 Verification

```bash
cd /Users/sz/Desktop/vos/vos4
.venv/bin/pip install pip-audit
.venv/bin/pip-audit --requirement requirements.txt --strict
.venv/bin/pip check
cd backend && ../.venv/bin/pytest tests/ -x --maxfail=3
```

---

## 4. Vulnerability resolution checklist

| # | Advisory | Affected pkg | Action | Verification command | Status after plan |
|---|----------|--------------|--------|----------------------|-------------------|
| 1 | `GHSA-crv5-9vww-q3g8` (DOMPurify XSS) | `dompurify@3.2.7` bundled in `monaco-editor@0.55.1` | Wait for monaco stable carrying `dompurify ≥ 3.3.4`; if not by 2026-05-31, apply `patch-package` per §2.5 | `cd frontend && npm audit \| grep -i dompurify` (expect 0 lines) | **OPEN** until upstream — no stable monaco release with the fix exists yet |
| 2 | `GHSA-qx2v-qp2m-jg93` (postcss `</style>` XSS) | `postcss@8.4.31` bundled in `next@16.2.4` | Upgrade to `next@16.3.0` GA when published; otherwise `patch-package` per §2.5 | `cd frontend && npm audit \| grep -i postcss` (expect 0 lines) | **OPEN** until 16.3.0 GA — only canary builds carry the fix as of 2026-05-03 |
| 3 | n/a — preventative | All Python deps | Run pip-audit on the upgraded venv | `.venv/bin/pip-audit` (expect 0 vulns) | CLOSED if 0 findings |
| 4 | RCE / agent escape (architectural risk, not a CVE) | `backend/ai/agents/` reliance on rlimit-only sandbox | Implement WASM isolation per §5 | `pytest backend/tests/test_wasm_sandbox.py` | TRACKED in §5 phased plan |

---

## 5. WASM agent-isolation migration

### 5.1 Recommended runtime: **Wasmtime (Bytecode Alliance)** — pinned at v44.0.2 *(Stage 9 vos.v1 update, May 2026)*

> **Stage 9 (vos.v1) version pin update — May 2026:** the target was bumped from `wasmtime==44.0.0` (April 2026) to **`wasmtime==44.0.2`** to pick up the user-specified May-2026 Spectre-V4 (Speculative Store Bypass variant) mitigation. The patch-level pin is non-optional for production deployments. **Verify against `pypi.org/project/wasmtime/#history` and the `bytecodealliance/wasmtime` security-advisories page at port time** in case upstream has moved further; the principle (latest patch-level on the v44.0.x line) is what matters, the digit is just a snapshot.

**Justification (one paragraph).** Wasmtime is the most defensible choice for VOS4 in May 2026 on four axes simultaneously: **(1) security model** — Wasmtime explicitly hardens against Spectre/Meltdown and ships a `fuel` mechanism for instruction-count CPU bounds, exactly the primitive VOS4 needs to replace `RLIMIT_CPU`; **(2) ecosystem maturity** — it is the reference WASI Preview 2 implementation, has the most active CVE response process, and is the runtime backing Microsoft's Wassette agent-tool sandbox and Phoenix's evaluation that "CPython WASM via wasmtime is the only option that passes all six security vectors" ([Arize Phoenix issue #11756](https://github.com/Arize-ai/phoenix/issues/11756)); **(3) Python integration cost** — `wasmtime-py@44.0.2` ([PyPI: wasmtime](https://pypi.org/project/wasmtime/)) embeds the runtime in-process so VOS4 keeps its existing FastAPI process model with no IPC layer, and `componentize-py` lets us compile agent code itself to a Component if/when we need that level of isolation; **(4) perf overhead** — projects like Eryx report 41× faster sandbox startup with AOT compilation and pooling, putting the per-agent cold-start budget under what VOS4's current `subprocess` fork costs.

Why not the alternatives:

- **Pyodide**: explicitly *not designed* as a security boundary by upstream. CVE-2026-5752 (CVSS 9.3) is unpatched and the project is unmaintained as of 2026. Multiple downstream sandbox escapes in 2025–2026 (n8n, Grist).
- **Wasmer**: Python SDK has lagged behind wasmtime-py; weaker WASI Preview 2 support; smaller security-disclosure track record.
- **WasmEdge**: strong for edge AI inference (LlamaEdge), but the Python embedding story is less mature than wasmtime-py and the runtime is C++ which expands the trusted computing base versus Wasmtime's Rust.
- **componentize-py**: complementary, not a replacement — used to compile *agent code* to a Component, then loaded by Wasmtime. Phase 2/3 candidate.

### 5.2 Phase 1 — Install + hello-world POC (1–2 days)

> **Status (Zero-Gap Task 6, 2026-05-09): ✅ Phase 1 LANDED.**
> Implementation lives in `backend/sandbox/wasm/`:
> - `engine.py` — process-wide Wasmtime engine singleton (fuel +
>   epoch + component-model enabled).
> - `policy.py` — `Policy(fuel, memory_pages, wall_clock_ms)` dataclass.
> - `runner.py` — `run_module(wasm_bytes, policy, ...)` with capability
>   denylist for `wasi:filesystem`, `wasi:sockets`, `wasi:http`.
> - `poc/hello.wat`, `poc/runaway.wat`, `poc/filesystem_import.wat` —
>   three test fixtures.
> - `backend/tests/security/test_wasm_sandbox.py` — three assertions:
>   clean run, fuel-exhaustion kill, denied-import refusal.
>
> `requirements.txt` now pins `wasmtime==44.0.2` (matches §5.1 above).
> Phase 2 (call-site identification + hot-path migration) is the next
> stage; it is NOT yet started.

```bash
cd /Users/sz/Desktop/vos/vos4
.venv/bin/pip install "wasmtime==44.0.2"
mkdir -p backend/sandbox/wasm
```

Create `/Users/sz/Desktop/vos/vos4/backend/sandbox/wasm/poc.py`:

```python
"""Hello-world POC: load a precompiled CPython.wasm and run agent code in it."""
from wasmtime import Config, Engine, Linker, Module, Store, WasiConfig

CPYTHON_WASM = "vendor/python-3.12.wasm"  # download from python.org/wasm builds

def run_agent(source: str, *, fuel: int = 100_000_000, mem_pages: int = 256) -> str:
    cfg = Config()
    cfg.consume_fuel = True            # CPU bound — replaces RLIMIT_CPU
    engine = Engine(cfg)
    store = Store(engine)
    store.set_fuel(fuel)
    store.set_limits(memory_size=mem_pages * 65536)  # mem bound — replaces RLIMIT_AS

    wasi = WasiConfig()
    wasi.argv = ["python", "-c", source]
    wasi.inherit_stdout()              # in production: redirect to a buffer
    store.set_wasi(wasi)

    linker = Linker(engine)
    linker.define_wasi()
    module = Module.from_file(engine, CPYTHON_WASM)
    instance = linker.instantiate(store, module)
    instance.exports(store)["_start"](store)
    return "ok"

if __name__ == "__main__":
    print(run_agent("print(2 + 2)"))
```

Run:

```bash
cd /Users/sz/Desktop/vos/vos4
mkdir -p backend/sandbox/wasm/vendor
# Fetch a CPython 3.12 WASI build (e.g. from VMware Wasm Labs or python.org WASI artifacts)
.venv/bin/python backend/sandbox/wasm/poc.py
# Expected: 4 \n ok
```

### 5.3 Phase 2 — Identify call sites needing wrapping

You don't need to read every file — these greps locate the entire surface area:

```bash
# Subprocess / shell exec sites in backend agents
grep -rln --include="*.py" "subprocess\|os\.system\|os\.popen\|asyncio\.create_subprocess" \
  /Users/sz/Desktop/vos/vos4/backend/agents \
  /Users/sz/Desktop/vos/vos4/backend/ai/agents

# Python eval/exec — highest-risk untrusted-code path
grep -rln --include="*.py" "\beval(\|\bexec(\|compile(" \
  /Users/sz/Desktop/vos/vos4/backend

# Existing rlimit-based sandbox (the thing being replaced/coexisting)
grep -rln --include="*.py" "RLIMIT_\|setrlimit\|resource\.setrlimit" \
  /Users/sz/Desktop/vos/vos4/backend

# CrewAI tool definitions that may execute generated code
grep -rln --include="*.py" "BaseTool\|@tool\b\|CodeInterpreter" \
  /Users/sz/Desktop/vos/vos4/backend
```

Each hit becomes a candidate for routing through `backend/sandbox/wasm/runner.py` (the production version of `poc.py` from §5.2).

### 5.4 Phase 3 — Coexistence with the existing rlimit sandbox

**Recommendation: defense-in-depth, do not deprecate.** The Linux rlimit + command allowlist + `shell=False` layer protects the *Python interpreter that hosts wasmtime-py itself*. Removing it would mean an exploit in wasmtime-py's host bindings escapes straight to the FastAPI process. Keep both.

Layering (outer → inner):

1. FastAPI worker process — runs under existing rlimits (RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC, RLIMIT_NOFILE, RLIMIT_FSIZE) — unchanged.
2. Command allowlist + `shell=False` — unchanged.
3. **NEW**: `wasmtime.Store` per agent invocation, with `consume_fuel=True` and `set_limits()`.
4. **NEW**: WASI capability gating — only mount the project's writable scratch dir via `wasi.preopen_dir`.

Add a feature flag (existing pattern in VOS4 — see `VOS3_STORAGE_BACKEND` env var):

```bash
# .env / settings page
VOS4_AGENT_SANDBOX=wasm   # values: rlimit (legacy), wasm (new), both (parallel run for diff testing)
```

Wire it in `backend/sandbox/__init__.py` (new file) so callers do `from backend.sandbox import run_agent` and the dispatch is one place.

### 5.5 Phase 4 — Rollout with feature flag

| Stage | Population | Flag value | Rollback |
|-------|-----------|------------|----------|
| 0 | Internal devs only | `VOS4_AGENT_SANDBOX=wasm` set per-developer in `.env.local` | unset env var |
| 1 | 5% of agent invocations | `VOS4_AGENT_SANDBOX=both` + sampling in `agents_routes.py` (compare outputs, log diffs) | flip sampling to 0% |
| 2 | 50% | same code path, raise sample to 0.5 | lower sample |
| 3 | 100% | `VOS4_AGENT_SANDBOX=wasm` default | env override per-tenant |
| 4 | Remove `rlimit` path | delete legacy code in `backend/sandbox/rlimit.py` | git revert |

Telemetry to watch during stages 1–3 (already exists in `backend/src/observability.py`):
- `agent_sandbox_p99_ms` — wasm should be within 1.5× of rlimit cold-start
- `agent_sandbox_oom_count` — wasm OOM should fail fast with `Trap` rather than killing the worker
- `agent_sandbox_timeout_count` — fuel exhaustion should trip before wall clock
- diff rate between `wasm` and `rlimit` outputs in stage 1 (target < 0.1%)

---

## 6. Verification & rollback

### 6.1 Green-build verification (run in order)

```bash
# Backend
cd /Users/sz/Desktop/vos/vos4
.venv/bin/pip check
.venv/bin/pip-audit --strict
cd backend && ../.venv/bin/pytest tests/ --cov=src --cov=agents

# Frontend
cd /Users/sz/Desktop/vos/vos4/frontend
npm install
npm audit
npm run lint
npm run type-check
npm run build

# E2E (Playwright, already wired)
cd /Users/sz/Desktop/vos/vos4/frontend
npx playwright test

# WASM sandbox (after Phase 1)
cd /Users/sz/Desktop/vos/vos4
.venv/bin/python backend/sandbox/wasm/poc.py
```

### 6.2 Rollback

This plan does not modify Python source files outside `backend/sandbox/wasm/` (which is new). All upgrades are atomic at the dependency-manifest level.

```bash
cd /Users/sz/Desktop/vos/vos4

# Stage everything first so the rollback is one ref
git status
git add -A
git commit -m "wip: Q2 2026 dependency refresh + wasm sandbox POC"

# To roll back to pre-upgrade state:
git revert HEAD
.venv/bin/pip install -r /tmp/vos4-pip-pre-upgrade.txt
cd frontend && rm -rf node_modules .next && npm ci
```

If only the backend upgrade needs reverting:

```bash
.venv/bin/pip install -r /tmp/vos4-pip-pre-upgrade.txt
```

If only the frontend upgrade needs reverting:

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
git checkout HEAD~1 -- package.json package-lock.json
rm -rf node_modules .next
npm ci
```

---

## 7. Sources

- [Next.js releases (vercel/next.js)](https://github.com/vercel/next.js/releases)
- [Next.js 16.2 announcement](https://nextjs.org/blog/next-16-2)
- [Monaco-editor releases](https://github.com/microsoft/monaco-editor/releases)
- [Monaco DOMPurify advisory thread #5248](https://github.com/microsoft/monaco-editor/issues/5248)
- [PyPI: fastapi](https://pypi.org/project/fastapi/)
- [LangChain releases (langchain-ai/langchain)](https://github.com/langchain-ai/langchain/releases)
- [LangChain release policy](https://docs.langchain.com/oss/python/release-policy)
- [LangGraph releases](https://github.com/langchain-ai/langgraph/releases)
- [CrewAI releases](https://github.com/crewAIInc/crewAI/releases)
- [CrewAI changelog](https://docs.crewai.com/en/changelog)
- [PyPI: wasmtime](https://pypi.org/project/wasmtime/)
- [bytecodealliance/wasmtime-py](https://github.com/bytecodealliance/wasmtime-py)
- [bytecodealliance/componentize-py](https://github.com/bytecodealliance/componentize-py)
- [Arize Phoenix sandbox-evaluation issue #11756](https://github.com/Arize-ai/phoenix/issues/11756)
- [CVE-2026-5752 (Pyodide sandbox escape)](https://undercodetesting.com/cve-2026-5752-unpatched-pyodide-sandbox-escape-allows-root-command-execution-act-now-video/)
- [Wasmer vs WasmEdge 2026 comparison](https://wasmruntime.com/en/compare/wasmer-vs-wasmedge)
