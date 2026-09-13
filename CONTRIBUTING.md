# Contributing to VOS3

> **VOS3 Sovereign Infrastructure v19.1 — PRE-CLI GOLD MASTER**

## Getting Started

```bash
# Backend
cd backend
pip install -r ../requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## Agentic Sovereignty

Three non-negotiable rules govern all contributions:

1. **Convex Schema is SSoT.** The single source of truth for all 22 tables lives in `frontend/convex/schema.ts`. Backend Python code reads/writes via the Convex HTTP API (`backend/db/convex.py`). Never define table schemas elsewhere.

2. **Kernel is Protocol Blind.** The VOS3 kernel (`kernel/`) must contain zero references to any external database, cloud service, or network protocol. The kernel communicates exclusively through the VBus binary bridge. If you grep the kernel for "convex", "supabase", "http", or "redis" and find a match, that is a bug.

3. **Supabase is prohibited.** VOS3 migrated fully to Convex + Clerk. No new code may import, reference, or depend on Supabase in any form — client libraries, REST calls, environment variables, or comments. Existing zero-reference count must be maintained.

## Architecture

```
Clerk (auth) ─► Frontend (Next.js) ◄──► Convex (database)
                                         ▲
                  Backend (FastAPI) ──────┘
                      │
                  VBus Bridge (virtio-serial)
                      │
                  VOS3 Kernel (x86_64)
```

## Branch Workflow

- Work on feature branches off `main`
- Push to `vos4` remote: `git push vos4 <branch>`
- Do NOT push to `origin` (legacy VOS3.git)

## Code Standards

- **Python**: Format with `black`, lint with `ruff`, security scan with `bandit`
- **TypeScript**: `npm run lint` and `npm run type-check` must pass
- **Kernel C**: No warnings with `-Wall -Wextra -Werror`, cross-compiled with `x86_64-elf-gcc`
- **Tests**: `pytest tests/` for backend, include unit + integration markers

### Forbidden Patterns (0-Tolerance — Zero-Hit Manifesto)

These patterns must NEVER appear in new code. CI and audits enforce zero occurrences:

| Pattern | Reason | Alternative |
|---------|--------|-------------|
| `shell=True` | RCE vector | `subprocess.run([...], shell=False)` or `ProcessSandbox` |
| `pickle.load` / `pickle.loads` | Arbitrary code execution | `json.loads()`, `orjson`, or Protobuf |
| `datetime.utcnow()` | Deprecated, returns naive datetime | `datetime.now(timezone.utc)` |
| `breakpoint()` | Debug artifact in production | Remove before commit |
| `supabase` (any import/reference) | Migrated to Convex | Use `backend/db/convex.py` |
| `user: dict` / `user["id"]` | Untyped auth bypass | `AuthenticatedUser` dataclass |
| `user_demo` | Legacy test seed | Use `dev_seed_user` |

### Sandbox Resource Limits (OS-Enforced Constraints)

All user-submitted code runs in a restricted `ProcessSandbox` (`backend/services/app_sandbox.py`). These are **kernel-enforced rlimits** — not advisory:

| Resource | Limit | Enforcement |
|----------|-------|-------------|
| `RLIMIT_NPROC` | **(4, 4)** | Hard cap: max 4 child processes, no escalation |
| `RLIMIT_FSIZE` | **(10MB, 10MB)** | Hard cap: 10 MB max file write size |
| `RLIMIT_NOFILE` | (64, 64) | Max open file descriptors |
| `RLIMIT_CPU` | (60, 120) | 60s soft / 120s hard CPU time |
| `RLIMIT_AS` | configurable (default 256MB) | Address space limit |
| Semaphore | 10 concurrent | Max concurrent sandbox executions |
| Environment | PATH, HOME, VOS3_SANDBOX, LANG | 4-variable allowlist only |

All subprocess execution MUST use `ProcessSandbox` or explicit `shell=False` with command allowlist. Direct `os.system()`, `os.popen()`, or `shell=True` are permanently forbidden.

## Pull Requests

- Keep PRs focused on a single concern
- Include test coverage for new functionality
- Ensure CI passes (lint, type-check, bandit, pytest)
