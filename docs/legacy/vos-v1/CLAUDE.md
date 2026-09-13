# CLAUDE.md

> **HANDOVER LOCK (Context Valid Until: 2026-05-31):** Before any action, ALWAYS read `infra/persistence/active_context/SESSION_HANDOVER_LOCK.md` to ensure continuity of the Sovereign Agent Edition launch.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status: vOS v1.1-GA — Sovereign Agent Edition (LIVE 2026-05-24)

**vOS v1.1.0-GA — Sovereign Agent Era Release (Sprints 14 + 15 merged to `main` at commit `1051fbe`)**
- 23 launch-blocker gaps closed before EU AI Act Article 73 enforcement (2026-08-02, 74 days out)
- 23 of 80 catalogued agent-era OS problems now have ✅-solved or ⚪-partial vOS posture
- 165/165 Sprint 15 tests pass in 14.86s (cumulative across Wave 1+2+3)
- See `docs/AGENT_ERA_OS_PROBLEMS.md` + `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md`

**Canonical GA kernel binary**
- Path: `kernel/build/vos3.elf`
- SHA-256: `67e8e0c058a1d6706552df3601b0162189a8bba01b52bb6ae4db7f667c73e7f0`
- SHA-384: `8d7d99532eedd0f655c1bd4cb553fb147362314c2060585fa3e2db8edf594e95c7d2b9e75dfb85a4072ae592c0ad34bc`
- Size: 12,884,536 bytes (12.29 MiB), .text=671,799 bytes
- Built: 2026-05-24 from `main@1051fbe` (reproducible build, `SOURCE_DATE_EPOCH=1700000000`)
- Type: ELF 64-bit LSB, x86-64, statically linked, not stripped (debug_info retained for GA-debug builds; `make release-kernel` produces the stripped distribution artifact)
- Sigstore bundle: `kernel/build/vos3.elf.bundle.json` (dev-tier CN=VOS3-DEV-NOT-FULCIO, ECDSA P-256, Rekor v2 inclusion proof verified)

**Prior v1.0.0 developer-preview kernel** (kept for diff lineage; superseded by v1.1-GA)
- SHA-256: `63a9b5405e9f1dcec0950fce16061c71108272495fb8e7c8650197c5bce0f0ba`
  (2026-05-19 release-strip from 12.8 MB unstripped baseline `f584d488c537bd4d7ad701935c12b6a312330a0dd96753cb513143f393e63a1c`)
- Multi-target manifest: `vos3.efi`=`773c58aaedc222277cf8b43d435ac03519251288c847f14cef25d3c6c1a1f623`,
  `vos3-hyperv.elf`=`14f176756028a64d42f50955c1ad8cf8601817302a7ca3010b66f3ffc756ddec`
- Sigstore bundle: `infra/security/release_artifacts/vos3_elf_v1_0_0.bundle.json`
  (ECDSA P-256, RFC-6962 Merkle inclusion proof in `infra/security/rekor_v2.jsonl`)
- Release archive: `dist/release_v1_0.zip` SHA-256 `1a575af891f336cbcb42252d30186bf882149265e18106851ecfd02b8394ef89`
  with bundle at `infra/security/release_artifacts/release_v1_0_zip.bundle.json`
  and external manifest at `dist/RELEASE_HASHES.txt`.
- Verify with: `bash scripts/verify_release.sh`
- **v20.0 Changelog:**
  - Stage 1: API key purge, Pydantic input validation (100KB/500msg), exec env sanitizer (LD_PRELOAD blocked), HTTPS-only Convex, circuit breaker
  - Stage 2: Generic PCI bus scanner (pci.c/pci.h), PCI_LIST VBus command, boot-time device discovery
  - Stage 3: Slab page reclamation (`vos3_heap_shrink()`), CTX_STATS per-slot telemetry, XSTATE_BV=0 guard + MXCSR verification
  - Stage 4: KTEXT_HASH live .text CRC32C integrity, CSP hardened (no unsafe-eval), backend factory decomposition (app.py/startup.py/router_registry.py), dashboard hardware + integrity panels
- VBus HMAC-SHA256: Frame authentication (FIPS 180-4 SHA-256 + RFC 2104 HMAC), 32-byte MAC at header offset 16, constant-time verification, per-session random key exchange during HANDSHAKE
- ivshmem Zone ACL: Per-agent `owner_tid` ownership tracking, gated `zone_base()` with ownership check, Slot 0 Coordinator kernel-only guard, ownership cleared on slot reset
- Pentest: 8/8 PASS — tampering, forgery, downgrade, key-mismatch, empty/large payload, determinism
- W^X Hard Enforcement: mprotect/mmap reject PROT_WRITE|PROT_EXEC (-EINVAL) + PTE sanitizer strips W+X at hardware level
- Stack Canaries: `__stack_chk_fail` (394 call sites)
- SMAP: 11 binary instructions (4 stac + 7 clac, user-copy paths only)
- Serialization Barriers: 73 fence instructions (38 mfence, 24 sfence, 11 lfence) + cpuid barriers
- Crypto Symbols: 8 SHA-256 + 7 HMAC (sha256.o: 30,248 bytes, GPR-only freestanding)
- Zone ACL Symbols: 3 ownership helpers + 2 zone_base variants (gated + unchecked)
- KASLR: Enabled (`limine.conf: kaslr: yes`)
- VMM Boundary: `0xFFFF800000000000` higher-half enforced in all map operations
- Heartbeat VA: `0xFFFFFFFFFFFFF000ULL` — synced between `ai_guard.h:368` and `vos3_sdk.h:31`
- Path Validation: 11/11 disk endpoints protected by `_validate_path`
- Auth: 0 `user: dict`, 0 `user["id"]`, 0 untyped `Depends` — 100% `AuthenticatedUser` dataclass across all route files (458 usages). Bearer token mandatory on 100% of non-public endpoints.
- Convex IDOR: `requireProjectOwnership` on all project/file/chat/memory/builds/yjsUpdates
- Frontend Hooks: 11/11 API-calling hooks send `Authorization: Bearer` headers
- RCE Defense: 5-layer command defense:
  1. Command allowlist (30+ whitelisted executables)
  2. Global blocked flags (`-c`, `-e`, `--eval`, `-exec`, `--exec`)
  3. Blocked pattern substrings (`core.pager`, `alias.`, `core.editor`, `credential.helper`)
  4. Per-executable safe-flag allowlists (git, npm, pip each with explicit flag whitelist)
  5. Path validation (all file-reader commands confined to `ALLOWED_PATHS`)
  All execution uses `shell=False` — zero shell metacharacter injection surface.
- SSRF Lock: DNS pinning + 10 blocked networks + scheme whitelist + pinned-IP httpx
- Sandbox: RLIMIT_AS(configurable) + RLIMIT_CPU(60,120) + RLIMIT_NPROC(4,4) + RLIMIT_NOFILE(64,64) + RLIMIT_FSIZE(10MB,10MB) + Semaphore(10) + 4-var env allowlist (PATH,HOME,VOS3_SANDBOX,LANG)
- VBus Transport: Ring buffer RX (0 memmove), zero-copy CRC, HMAC-SHA256 frame auth, batched mm.flush()
- VBus Performance: 228.8 cmd/s, P99 7.6ms, jitter 0.54ms, 142.2 KB/s writes, 50/50 exhaustion
- Convex Indexes: `by_document_seq` compound, `search_apps`, `by_owner` — all verified in schema
- Connection Pool: Singleton httpx.AsyncClient (10 max, 5 keepalive), JWKS cached 1hr
- Purity: 0 user_demo, 0 shell=True, 0 supabase, 0 pickle, 0 utcnow, 0 breakpoint
- Agent Kill Switch: SYS_AGENT_KILL_ALL (497) — kills all registered agents, scrubs all 8 model slots, privileged
- Certified: 2026-04-14 | Infrastructure v21.0 - Genesis-Gate Hardened (ASSERT count auto-verified: `bash infra/audit/count_asserts.sh` — current floor: 1,300, latest count: 1,518 — see script for per-phase breakdown)

## Project Overview

VOS3 is a unified AI Operating System combining:
- **Multi-Agent System** - LangGraph-based agents for code generation and orchestration
- **V-Core Business OS** - Organizations, entities, workflows, monitoring
- **Cost-Optimized LLM** - Smart routing between providers (40-60% cost savings)

## Development Commands

### Kernel (x86_64 C)

```bash
cd kernel

# Requires x86_64-elf-gcc cross-compiler toolchain
# macOS: brew install x86_64-elf-gcc x86_64-elf-binutils
# Linux: apt install gcc-x86-64-linux-gnu (or build from source)

# Clean build
make clean && make

# Output: build/vos3.elf
# Run with QEMU (see MEMORY.md for full launch config)
qemu-system-x86_64 -kernel build/vos3.elf -m 4096M -smp 2 -cpu max ...
```

### Backend (Python/FastAPI)

```bash
cd backend

# Install dependencies
pip install -r ../requirements.txt

# Run development server (port 8000 default, 8002 if conflicts)
uvicorn main:app --reload --port 8000

# Run all tests
pytest tests/

# Run specific test markers
pytest -m unit              # Unit tests only
pytest -m integration       # Integration tests only
pytest -m "not slow"        # Skip slow tests

# Run single test file
pytest tests/test_module.py

# Coverage report
pytest --cov=src --cov=agents --cov-report=html

# Format and lint
black .
ruff check .
```

### Frontend (Next.js/React)

```bash
cd frontend

# Install dependencies
npm install

# Run development server (default 3000, use -p for other port)
npm run dev
npm run dev -- -p 3001

# Build for production
npm run build

# Lint and type checking
npm run lint
npm run type-check
```

### Desktop (Tauri 2.0 / Rust)

```bash
cd desktop

# Install Tauri CLI (requires Rust toolchain)
cargo install tauri-cli --version "^2"

# Run development mode (requires frontend on :3000)
# Terminal 1: cd frontend && npm run dev
# Terminal 2:
cargo tauri dev

# Build production installer (.dmg / .msi / .AppImage)
cargo tauri build

# Run Rust unit tests
cd src-tauri && cargo test
```

## Architecture

### Backend (`backend/`)

**Entry Point:** `main.py` - FastAPI app with lifespan management, initializes services in global dict

**API Routes** (`api/`) - 26 route files:

*Active (mounted in main.py):*
- `chat_routes.py` - `/api/chat/*` - AI chat with streaming
- `codegen_routes.py` - `/api/codegen/*` - Code generation
- `agents_routes.py` - `/api/agents/*` - Agent management
- `v_core_routes.py` - `/api/v-core/*` - Business OS operations
- `settings_routes.py` - `/api/settings/*` - Runtime configuration
- `metrics_routes.py` - `/api/metrics/*` - Router observability, health, and bug tracking
- `memory_routes.py` - `/api/memory/*` - Development memory CRUD and search
- `voice_routes.py` - `/api/voice/*` - Voice control

*Available (not mounted):*
- `aiva_routes.py` - AIVA assistant
- `analytics_routes.py` - Analytics dashboard
- `billing_routes.py` - Billing and payments
- `blueprints_routes.py` - Project blueprints
- `comment_routes.py` - Code comments
- `design_system_routes.py` - Design system management
- `github_sync_routes.py` - GitHub synchronization
- `inbox_routes.py` - Notifications inbox
- `plugin_routes.py` - Plugin management
- `proactive_routes.py` - Proactive suggestions
- `prompt_routes.py` - Prompt management
- `prompt_history_routes.py` - Prompt history
- `team_routes.py` - Team management
- `template_routes.py` - Code templates
- `theme_routes.py` - Theme management
- `version_control_routes.py` - Version control
- `clerk_webhook.py` - Auth webhooks

**V-Core Business OS** (`core/`):
- `control_plane.py` - Users, orgs, roles, RBAC permissions
- `business_core.py` - Custom entities, fields, records, validation
- `workflow_engine.py` - Workflow automation with triggers/actions
- `mission_control.py` - Monitoring, approvals, metrics, alerts

**AI Engine** (`ai/`):
- `llm/providers.py` - Multi-provider support (OpenAI, Anthropic, Google, Ollama)
- `agents/multi_agent.py` - LangGraph orchestration (Architect, Frontend, Backend, Tester, Reviewer)
- `codegen/generator.py` - Code generation with validation
- `rag/` - RAG implementations (REFRAG, CLaRa, TAO optimizations)

**Smart Router** (`src/efficiency.py`):
- Uses `shared-ai-router` library (installed from `/Users/snirzano/shared-ai-router`)
- Config at `config/router.yaml` - defines models, role mappings, complexity threshold
- `assign_model(role, complexity)` - selects optimal model based on role and task complexity
- High complexity (>=9) routes to claude-opus (or gpt for architect role)

**Observability** (`src/observability.py`):
- Tracks model selections, costs, response times, errors
- `track_request()` context manager for request tracking
- `get_metrics()` / `get_cost_breakdown()` for aggregated stats
- `record_error()` / `get_errors()` for bug tracking
- `get_health()` for system health status
- **Persists to DevMemory** - metrics survive backend restarts

**Development Memory** (`memory/dev_memory.py`):
- ChromaDB-based vector storage for persistent learning
- Stores conversations, decisions, code changes, errors, solutions
- `remember(content, memory_type)` / `recall(query)` for quick access

**Additional Backend Directories:**
- `agents/` - Agent definitions and configs
- `rag/` - RAG implementations
- `integrations/` - External service integrations
- `services/` - Service layer abstractions
- `voice/` - Voice control system
- `analytics/` - Analytics processing
- `plugins/` - Plugin system
- `code_review/` - Code review tools
- `profiling/` - Performance profiling
- `notifications/` - Notification system
- `deployment/` - Deployment utilities
- `blueprints/` - Project blueprints
- `design_system/` - Design system
- `debugging/` - Debug tools
- `hitl/` - Human-in-the-loop
- `proactive/` - Proactive features
- `prompts/` - Prompt templates
- `shared/` - Shared utilities

**Service Pattern:** Services initialized via factory functions (`get_control_plane_service()`, `get_business_core_service()`, etc.) exported from `core/__init__.py`

### Frontend (`frontend/`)

**Path Alias:** `@/*` maps to frontend root (use `@/components/...`, `@/hooks/...`)

**Pages** (`app/`):
- `/` - Dashboard home
- `/chat` - AI chat interface
- `/builder` - Code generation
- `/agents` - Agent management
- `/v-core` - Business OS dashboard
- `/rag` - RAG interface
- `/metrics` - Router observability dashboard
- `/health` - System health and bug tracking
- `/settings` - Configuration

**Key Components** (`components/`):
- `shared/Navigation.tsx` - Sidebar navigation
- `shared/ClientLayout.tsx` - Client-side layout wrapper
- `shared/VoiceInput.tsx` - Voice recognition (11 languages)
- `v-core/VCoreComponents.tsx` - Business OS UI components

**Hooks** (`hooks/`):
- `useChat.ts` - Chat API with model selection
- `useCodegen.ts` - Code generation API
- `useAgents.ts` - Agent management API
- `useVCore.ts` - V-Core state management (entities, records, workflows)
- `useVoiceInput.ts` - Voice recognition

**API Proxy:** `next.config.js` rewrites `/api/*` to backend at `NEXT_PUBLIC_API_URL` (default: `http://localhost:8000`)

## Key Integration Points

1. Root layout (`app/layout.tsx`) is a Server Component, uses `ClientLayout` for client-side interactivity
2. Global styles in `app/globals.css` with CSS variables for theming
3. Backend services stored in `services` dict, accessed via factory functions
4. Vector storage via ChromaDB and FAISS
5. Optional Redis for caching, Langfuse/LangSmith for observability

## Model Routing

The SmartRouter (`backend/config/router.yaml`) determines which LLM handles each request:

| Role | Alias | Model ID | Persona |
|------|-------|----------|---------|
| architect | `gpt` | gpt-4o | Staff Principal Engineer |
| frontend | `claude-sonnet` | claude-sonnet-4-6 | Senior Frontend Developer |
| backend | `claude-sonnet` | claude-sonnet-4-6 | Senior Backend Developer |
| tester | `gemini` | gemini-2.5-flash | Fast QA Automation Engineer |
| reviewer | `claude-opus` | claude-opus-4-6 | Code Quality & Security Auditor |
| coding | `claude-sonnet` | claude-sonnet-4-6 | — |
| coding-complex | `gpt-codex` | o3-mini | Elite Algorithmic & Systems Engineer |
| researcher | `gemini-pro` | gemini-2.5-pro | Massive-Context Data Retriever |
| researcher-deep | `claude-opus` | claude-opus-4-6 | Deep-Tech Technical Investigator |

**Configuration:** `default_model: claude-sonnet`, `complexity_threshold: 9`

Use `assign_model(role, complexity)` from `src.efficiency` to get the optimal model.

**Agent Prompts** are defined in:
- `backend/ai/agents/multi_agent.py` — `AGENT_PROMPTS` dict (full prompts)
- `backend/api/agents_routes.py` — `DEFAULT_PROMPTS` dict (short API-level prompts)

## Environment Variables

API keys can be configured via environment variables OR at runtime through Settings page (`/settings`).

**Required for AI** (optional - falls back to dev mode):
- `OPENAI_API_KEY` - OpenAI GPT models
- `ANTHROPIC_API_KEY` - Anthropic Claude models

**Database (Convex):**
- `CONVEX_URL` - Convex deployment URL (backend)
- `CONVEX_DEPLOY_KEY` - Convex deploy key (backend server mutations)
- `NEXT_PUBLIC_CONVEX_URL` - Convex URL (frontend)

**Authentication (Clerk):**
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` - Clerk publishable key
- `CLERK_SECRET_KEY` - Clerk secret key
- `CLERK_ISSUER_URL` - Clerk JWT issuer URL

**Optional:**
- `NEXT_PUBLIC_API_URL` - Backend URL (default: localhost:8000)
- `GOOGLE_API_KEY` - Google Gemini models
- `OLLAMA_BASE_URL` - Local models
- `REDIS_URL` - Caching
- `LANGFUSE_*` - Observability

## Dev Mode

Backend runs without API keys in "dev mode":
- Echo responses for chat
- Template code for generation
- Mock agent execution

## Testing

```bash
# Backend - all tests with coverage
cd backend && pytest tests/ --cov=src --cov=agents

# Backend - run single test
pytest tests/test_file.py::test_function -v

# Frontend - lint and type check
cd frontend && npm run lint && npm run type-check
```

## VOS3 Architecture (February 2026) — Convex Migration

**Database:** **Convex** (single source of truth). Clerk remains for auth only.

### Architecture

```
Clerk (auth only)
     |
Frontend (Next.js) <──► Convex (ConvexProviderWithClerk + useQuery/useMutation)
Python Backend (FastAPI) ──► Convex HTTP API (POST /api/mutation, /api/query)
```

### Database Layer

| File | Purpose |
|------|---------|
| `frontend/convex/schema.ts` | All 22 tables defined as Convex schema |
| `frontend/convex/auth.config.ts` | Clerk JWT integration |
| `frontend/convex/users.ts` | User sync + CRUD |
| `frontend/convex/organizations.ts` | Org + member management |
| `frontend/convex/billing.ts` | Credits, subscriptions, Stripe customers |
| `frontend/convex/projects.ts` | Projects, files, chat |
| `frontend/convex/vcore.ts` | Entities, records, workflows, audit log |
| `frontend/convex/quota.ts` | Usage quotas + alerts |
| `backend/db/convex.py` | Python HTTP client (`ConvexClient`) |
| `backend/core/repositories/convex.py` | 12 repository implementations |

### Environment Variables (Required for Production)

```env
# Convex (Database)
CONVEX_URL=https://your-deployment.convex.cloud
CONVEX_DEPLOY_KEY=prod:...
NEXT_PUBLIC_CONVEX_URL=https://your-deployment.convex.cloud

# Clerk (Authentication)
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...
CLERK_ISSUER_URL=https://<domain>.clerk.accounts.dev

# Stripe (Billing)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Convex Dev Workflow

```bash
# 1. Install Convex CLI
npm install -g convex

# 2. Start local Convex dev server (generates _generated/ types)
cd frontend && npx convex dev

# 3. Run backend (connects to Convex via HTTP)
cd backend && uvicorn main:app --reload --port 8000
```

### API Routes

- `/api/billing/*` - Subscription and payment management
- `/api/teams/*` - Team collaboration and sharing
- `/api/analytics/*` - Business analytics and tracking
- `/api/github/*` - GitHub sync and version control
- `/api/inbox/*` - Unified communications inbox
- `/api/templates/*` - Code template library
- `/api/plugins/*` - Plugin marketplace and management
- `/api/webhooks/clerk` - Clerk user sync webhook

## VOS3 Kernel Capabilities (feat/phases-g-n — merged to main)

**Test Suite:** 1,340 PASS, 0 FAIL | **Branch:** `feat/10-10-all-capabilities`

### musl libc Integration (v1.2.5)
- Full musl libc dynamically linked via `ld-musl-x86_64.so.1` (618KB stripped)
- **9 musl-linked user programs:** hello, signal, env, fileio, malloc, fork, pipe, stat, pthread
- SSE/SSE2 enabled (CR4.OSFXSR + CR4.OSXMMEXCPT)

### POSIX Threads (pthreads)
- `pthread_create` / `pthread_join` through musl's real implementation (not stubs)
- `CLONE_CHILD_CLEARTID` + futex-wake-on-exit lifecycle for join synchronization
- `CLONE_THREAD` PID inheritance (Linux TGID semantics)
- Mutex, TLS (`__thread`), multi-thread support verified

### Signal Delivery
- Full user-space signal delivery: stack frame + `sa_restorer` → `__restore_rt` → `rt_sigreturn`
- `sigaction`, `tgkill`, `rt_sigreturn` syscalls

### Linux ABI Syscall Coverage
- **File I/O:** open, openat(257), read, write, readv(19), writev(20), lseek, stat, fstat, lstat, newfstatat(262), access(21), ioctl(16)
- **Directory:** mkdir, rmdir, rename, unlink, link, symlink, readlink, getdents64, getcwd, chdir
- **Process:** fork, clone(56), exec, exit, exit_group(231), getpid, getppid, wait4
- **Memory:** mmap(9), munmap(11), mprotect(10), brk(12)
- **Threading:** clone(CLONE_VM|CLONE_THREAD|CLONE_SETTLS), set_tid_address, set_robust_list(273), futex(202)
- **Other:** getrandom(318), epoll(213/232/233), select(23), eventfd2(284), pipe2(293), dup3(292), fcntl(72)

### ProcFS No-Dcache Fix
- `VOS3_FS_NO_DCACHE` flag prevents stale PID caching in `/proc/self` virtual filesystem

### Performance
- `BENCH_MODE=1` suppresses DEBUG serial output (93% reduction: 31K → 2K lines)
- Lock-free kernel ring buffer (klog) for non-blocking logging
- Futex hash table (16 buckets) for O(1) avg-case wake

### Syscall Pointer Validation (Phase 29)
- `sys_puts`, `sys_arch_prctl`, `sys_clone`, `sys_wait4`, `sys_nanosleep`: validated via `access_ok()` / `strncpy_from_user()`
- Break-fix certified: 8/8 exploit attacks → EFAULT/EINVAL

### Vision Pipeline — Contract-First (Phase 3.5) [Operational]
- `DesignContract` (Pydantic): HSL tokens, OCR content, shadcn/ui component mapping
- `VisionAgent` → `DesignContract` → `ThemeEngine` → architect/frontend agents
- SSE streaming: `POST /api/v1/vision/analyze` streams phase insights
- Pixel-Sync overlay: design image at adjustable opacity over live preview
- A11y audit: programmatic WCAG AA contrast + ARIA role verification

### Application Delivery System — VOS3 Native (Phase 4.0) [Operational]
- V-Packer: ZIP-based .vpk with layered manifest (SystemManifest + IntentManifest)
- SystemManifest: kernel-enforced (memory quota, syscall allowlist, inference/scratchpad split)
- IntentManifest: AI Guard-enforced (authorized models, data retention policy, from DesignContract)
- Hardware Root-of-Trust: inference_memory mapped with `VOS3_AI_FLAG_READ_ONLY` → PTE bit 10 without bit 1 → x86_64 MMU enforces immutability
- Data retention: SCRUB (zero-fill on kill), PERSIST (keep RO for restart), SNAPSHOT (Phase 4.1)
- Bridge hardening: `vos3_bridge_bound_window()` (CVE-2026-23086), MAX_CHUNK_SIZE (8KB), congestion control, 22 commands
- APPLOGS: offset-tracked stdout streaming → SSE `/api/v1/deploy/native/{app_id}/logs`
- Native SDK: `VOS3_SDK.h` (syscall stubs) + `Makefile.native` (musl cross-compile) in ZIP download
- Frontend: 6-step wizard (+ Deploy step) + Live Console + dual memory bars + retention badge
- Files: `backend/services/vpacker.py`, `backend/services/native_deploy_service.py`, `backend/api/native_deploy_routes.py`
- Tests: `test_vpacker.py` (34 tests), `test_native_deploy.py` (17 tests), `test_native_sdk.py` (11 tests) — 62 PASS, 0 FAIL

### Phase 7.0 — Genesis-Gate Final Audit (86 GEN_ASSERTs)
- Black-Box Persistence: 1000 VecVFS hard-reset cycles, zero data loss
- Cold-Boot Genesis Flush: AI Guard scrub + cache wipe + DLP scrub, 50 iterations
- Hypervisor Stealth Probe: 100 timing probes, constant-time verification
- Post-Quantum Key Rotation: HKDF-SHA256 under quarantine, 50 rotation cycles
- Genesis Sovereign Certificate: 100 pipeline cycles, 15/10 max scoring

### Phase 8.1 — Velocity-Alpha Performance Benchmark (87 VEL_ASSERTs)

### Phase 8.2 — Sovereign Endurance Audit (86 END_ASSERTs)

## VOS3 Enclave — Desktop Distribution

**Three-Stage Hybrid UI Roadmap:**
- **Stage A (Current)**: Tauri 2.0 wrapper — Next.js 27 routes in native WebView shell, Rust backend manages QEMU child + VBus socket bridge. Produces `.dmg`/`.msi`/`.AppImage`.
- **Stage B (Q3 2026)**: Replace system WebView with Servo (Rust-native web engine) — zero browser dependency.
- **Stage C (Q1 2027)**: Bootable VOS3 ISO with Slint (Rust UI) for critical tasks, Tauri+Servo for non-critical.

### Desktop Architecture (`desktop/`)

```
┌─────────────────────────────────────────────────┐
│  VOS3 Enclave (Tauri 2.0)                       │
│  ┌───────────────────────────────────────────┐   │
│  │  System WebView → Next.js (27 routes)     │   │
│  └──────────────────┬────────────────────────┘   │
│                     │ #[tauri::command] IPC       │
│  ┌──────────────────▼────────────────────────┐   │
│  │  Rust Backend (src-tauri/)                │   │
│  │  QemuManager · VBusClient · WarpDrive     │   │
│  └──────────┬────────────────┬───────────────┘   │
└─────────────┼────────────────┼───────────────────┘
              │ child process  │ Unix socket
        QEMU + VOS3 Kernel (build/vos3.elf)
```

**Key modules** (`desktop/src-tauri/src/`):
- `qemu.rs` — QEMU child process lifecycle (start/stop/health)
- `vbus/` — VBus protocol: CRC32C, HMAC-SHA256, async Unix socket client, 22 commands
- `warp.rs` — Warp Drive mmap (4 zones × 16MB = 64MB shared memory)
- `commands.rs` — `#[tauri::command]` IPC handlers (start_kernel, stop_kernel, load_model, etc.)
- `state.rs` — Shared AppState (QemuManager + VBusClient + WarpDrive)

## Known Technical Debt

### Duplicate Directories — CLOSED (Sprint 14.3, 2026-05-20)

Historical: `backend/rag/` ↔ `backend/ai/rag/` and `backend/agents/` ↔ `backend/ai/agents/`
were tracked here as duplicate-code seams.

Resolution: the root-level `backend/rag/` and `backend/agents/` directories
were removed in an earlier consolidation pass. Sprint 14.3 (2026-05-20)
verified zero remaining orphaned imports (`grep -rnE "(from|import) (rag|agents)\b"`
returns nothing under `backend/`) and bulk-rewrote 27 stale doc references
across `docs/TECHNICAL_IMPLEMENTATION_SPEC.md` (21),
`backend/mcp-server/docs/PIPELINE_ARCHITECTURE.md` (2), and
`COMPETITIVE_GAP_PLAN.md` (4) from `backend/agents/` → `backend/ai/agents/`.

The canonical locations are now:
- `backend/ai/rag/` — RAG pipelines (REFRAG, CLaRa, TAO)
- `backend/ai/agents/` — multi-agent LangGraph orchestration

### V-Core In-Memory Storage
V-Core services (`core/*.py`) currently use in-memory dictionaries. Set `VOS3_STORAGE_BACKEND=convex` (now the default) to use Convex repositories for production persistence.

### Frontend Hooks — Incremental Strategy
Current hooks call FastAPI; the backend calls Convex. Next step: replace hooks with direct `useQuery`/`useMutation` from Convex for real-time reactivity without the backend round-trip.

---

## vOS.v1 Unified Stages (0-13) — Quick Reference

This repo is the **unified merger** of vos4 (Engine) + VOS3-Cyber (Shield) + VOS3 (Product). Full architecture: `ARCHITECTURE.md`. File-level merge log: `docs/CYBER_OVERLAY_INTEGRATION.md`. Build provenance: `docs/PROVENANCE.md`.

### Deterministic kernel
- `kernel/build/vos3.elf` SHA-256: `43bfdd2ac2336d8326209db033c45f414b3fbb2deb3fce904d8aa233b2bc9b9a` (post 2026-05-11 Universal-Model Upgrade — ai_kim.h ceiling lift; reproducibility flags unchanged). Prior baseline `bd0ce974…` was bit-identical from Stage 7 through 14.D.3 (2026-05-09); the 2026-05-11 rebuild changed the SHA because the `ai_kim.h` constants for VOS3_KIM_MAX_VOCAB / DIM / SEQ_LEN / KV_PAGES were lifted to admit the 2026 universal-model family (Gemma 3, Llama 4, DeepSeek R1, Qwen3, Phi-4).
- Reproducibility flags: `SOURCE_DATE_EPOCH=1700000000`, `--build-id=none`, `-ffile-prefix-map`.
- Z3 formal proofs (4/4 UNSAT against the merged kernel, Stage 12): `backend/tests/benchmarks/{sched_core,egress_policy,ai_oom,merkle_inclusion}_z3_proof.py`.

### New VBus commands (added in Stages 4-12)

| Command | Stage | Reply |
|---------|-------|-------|
| `INTENT_SUBMIT\|<hex>[\|<slot>]` | 4-5 | `INTENT_OK\|<96hex>` or `INTENT_BOUND\|<96hex>` (RTMR[1] extend) |
| `TEE_ENV` | 6 | `TEE_ENV\|<env-name>` |
| `TEE_QUOTE` | 6 | `TEE_QUOTE\|<...>` |
| `SCHED_COOKIE_STATS` | 6 | `SCHED_COOKIE\|stats=...` |
| `HCS_FLUSH` | 6 | `HCS_FLUSHED\|src=MANUAL` |
| `SCHED_SIBLING_CHECK\|<cpu>` | 6 | `SCHED_SIBLING_CHECK\|cpu=N\|compat=0\|1` |
| `AUDIT_FAIL_QUOTE` | 10.1 | `AUDIT_FAIL\|total=N\|fill=M\|<seq>:<cat>:<rc>:<slot>:<digest>...` |
| `ACTION_CHECK_CONFIDENCE\|<slot>\|<score>` | 10.2 | `CONF_OK\|...` or `ERR 13 CONF_BLOCK\|...` |
| `POLICY_OVERRIDE\|<slot>\|<score>` | 10.2.2 | `POLICY_OK\|slot=N\|gate=G` |
| `POLICY_FORCE_PERMIT\|<0\|1>` | 10.2.2 | `POLICY_OK\|force_permit=N` |
| `POLICY_STATUS` | 10.2.2 | `POLICY\|force_permit=N\|gates=g0,g1,g2,g3` |

### Stage-10 backend services (`backend/services/`)
- `policy_override.py` — management override + Safe-Rollout + REVIEW_REQUIRED fallback
- `compliance_store.py` — SQLite/SQLCipher persistence for kernel audit events
- `intent_manifest_builder.py` — v2 manifest envelope generator
- `integrity_worker.py` — async SHA-384 worker for streaming-fidelity

### Stage-11 supply chain (`infra/security/`)
- `sigstore_v3_bundle.py` — v3-shaped DSSE+Rekor bundle Signer/Verifier (ECDSA P-256, real cryptography)
- `rekor_v2_log.py` — RFC-6962 Merkle transparency log (75/75 inclusion proofs verified)
- `build_sbom.py` — CycloneDX 1.5 generator with embedded VEX (1374 components)
- `release_artifacts/vos3_elf_stage11.bundle.json` — signed bundle binding `bd0ce974…`

### Stage-13 compliance docs (`docs/`)
- `POST_QUANTUM_INVENTORY.md` — every crypto primitive classified; FIPS 203/204/205 distinguished
- `vOS_System_Context.md` — LLM anti-hallucination prompt (8 sections)
- `AI_SA_AUTONOMY_LEVEL_MAPPING.md` — 0-5 autonomy taxonomy
- `EU_AI_ACT_COMPLIANCE.md` (refreshed) — Article 73 timelines + AI-SA cross-ref
- `POLICY_OVERRIDE.md`, `STREAMING_FIDELITY.md`, `SIGSTORE_V3_GAP.md`, `TEST_SUITE.md` — gap docs / honest-scope ceilings

### What's NOT yet ported (Stage-10 missing-files batch)
40 Stage-12 tests blocked on these — they will port as a batch:
- `backend/core/security/connectors/{external_spm,runtime_firewall,edr_event_relay}.py`
- `backend/api/compliance_routes.py`
- `backend/services/{vbus_ring_buffer,prefetch}.py`
- `backend/core/security/{rotation_manager,cert_vault}.py`
- `backend/core/repositories/vault_pool.py`
- `apex_sim` simulator harness
