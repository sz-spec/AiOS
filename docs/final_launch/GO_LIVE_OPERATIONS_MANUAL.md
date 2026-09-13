# VOS3 Go-Live Operations Manual
## SRE Runbook — 1M User Scale-Out
**v20.3.0 | April 2026 | FOR: Site Reliability Engineers, On-Call**

---

## Overview

This manual covers day-to-day operation of VOS3 at production scale. It assumes Version A (Linux/Docker + QEMU kernel) is deployed. Every threshold, command, and alert in this document maps to a specific code artifact — no tribal knowledge required.

**System envelope at steady state (1M users):**
- 10,000 requests/second sustained
- Convex write queue ≤ 5,000 items
- Circuit breaker: ≤ 5 failures / 30s window
- EWMA pressure: < 0.85 (normal), ≥ 0.85 (degraded/cost-saving mode)
- LLM timeout: 45 seconds hard ceiling
- Memory recall timeout: 5 seconds

---

## 1. Architecture Map for SREs

```
                    ┌──────────────────────────────────────┐
                    │  QEMU (kernel/build/vos3.elf)        │
                    │  VBus socket: /tmp/vos3_bridge.sock  │
                    │  VBus: 228.8 cmd/s | P99 7.6ms       │
                    └─────────────┬────────────────────────┘
                                  │ VBus HMAC-SHA256 ASCII
                    ┌─────────────▼────────────────────────┐
                    │  FastAPI (uvicorn, port 8000)         │
                    │  kernel_routes: /api/kernel/*         │
                    │  EWMA PID router (router.py)          │
                    │  Circuit breaker (circuit_breaker.py) │
                    │  Backpressure (startup.py)            │
                    └──────┬──────────────┬────────────────┘
                           │ HTTP         │ Convex mutations
              ┌────────────▼──┐    ┌──────▼──────────────┐
              │  LLM APIs     │    │  Convex Database     │
              │  OpenAI/Anth/ │    │  billing:useCredits  │
              │  Google/Haiku │    │  OCC serializable    │
              └───────────────┘    └─────────────────────┘
```

---

## 2. Health Check — First 30 Seconds on Call

Run in order. Each command is self-contained.

### 2.1 Kernel Status
```bash
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/kernel/status | jq .

# Healthy: {"connected": true, "ping": true, ...}
# Degraded: {"connected": false} → kernel offline, see Section 5
```

### 2.2 MMR Transparency (Audit Chain Live?)
```bash
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/kernel/transparency | jq .

# Healthy:
# {"fresh": true, "root": "5a47e556...", "leaves": 48291}
#
# Degraded:
# {"fresh": false, "error": "kernel offline"}
# → kernel not recording — EU AI Act compliance gap, P0 alert
```

### 2.3 Hardware Pressure
```bash
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/kernel/hardware/pressure | jq .

# Healthy:   {"pressure": 0.32, "congested": 0, "fresh": true}
# Warning:   {"pressure": 0.78} → approaching degraded threshold (0.85)
# Degraded:  {"pressure": 0.91} → Haiku fallback active, check LLM costs
# Critical:  {"congested": 1}   → token-bucket ban active
```

### 2.4 General System Health
```bash
curl -s http://localhost:8000/api/metrics/health | jq .

# Check: status, services.*, errors_last_hour
```

### 2.5 Circuit Breaker State (Convex)
The circuit breaker does not have a dedicated HTTP endpoint. Infer its state from:
```bash
# OPEN circuit → all /api/* calls return 503 with Retry-After header
# Check recent 503 rate:
curl -s http://localhost:8000/api/metrics/errors \
  | jq '.errors[] | select(.error_type == "CircuitBreakerOpenError")'
```

---

## 3. Normal Operating Procedures

### 3.1 Kernel Management (QEMU)

**Start kernel:**
```bash
cd kernel
qemu-system-x86_64 \
  -kernel build/vos3.elf \
  -m 4096M \
  -smp 2 \
  -cpu max \
  -serial unix:/tmp/vos3_bridge.sock,server,nowait \
  -nographic \
  -no-reboot
```

**Verify kernel started:**
```bash
# Wait for VBus handshake (< 3s)
python3 backend/scripts/bench_vbus.py --ping-only

# Expected: PONG received in < 10ms
```

**Graceful kernel restart:**
```bash
# Kill QEMU
pkill -f "qemu-system-x86_64.*vos3.elf"
sleep 2
# Restart (kernel reloads clean MMR state — boot measurement is re-recorded)
```

**Note:** MMR state is in-kernel SRAM. It does not persist across reboots — this is by design. The MMR root at shutdown is pinned to Convex via `/api/kernel/transparency` before termination. Pin it manually before planned maintenance:
```bash
curl -s -X POST \
  -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/kernel/transparency/pin
```

### 3.2 Backend Management

**Start backend:**
```bash
cd backend
uvicorn main:app --reload --port 8000 --workers 4
```

**Start with gunicorn for production:**
```bash
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 60 \
  --graceful-timeout 30
```

**Health after restart:**
```bash
# Wait for LLM factory pre-warm (defined in startup.py lifespan)
# Expected: "LLM factory warmed" in logs within 10s
curl -s http://localhost:8000/api/metrics/health | jq '.status'
# Expected: "healthy"
```

### 3.3 Frontend Management

**Start Next.js:**
```bash
cd frontend
npm run build && npm start
# Production port: 3000
```

**Check transparency widget:**
```
Browse to http://localhost:3000/health
→ "MMR Audit Ledger" section should show LIVE (green dot)
→ Leaf count should increment every few seconds
```

---

## 4. Feature Flags — Live Traffic Control

Feature flags are the primary tool for traffic management without deployment. All flags support instant rollback.

**File:** `backend/services/feature_flags.py`

### 4.1 View Current Flags
```bash
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/settings/feature-flags | jq .
```

### 4.2 Key Flags for SREs

| Flag | Default | Effect | When to Toggle |
|------|---------|--------|----------------|
| `haiku_fallback` | ON, 100% | EWMA pressure → Haiku downgrade | Turn OFF to force full-quality models regardless of pressure |
| `streaming_codegen` | ON, 100% | SSE streaming for code gen | Turn OFF if client disconnect issues arise |
| `proactive_analysis` | ON, 5% rollout | Background proactive insights | Reduce % or turn OFF to cut Convex write volume |
| `new_billing_portal` | OFF, 0% | New billing UI | Only enable for controlled rollout |

### 4.3 Update a Flag
```bash
curl -s -X PATCH \
  -H "Authorization: Bearer $VOS3_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"haiku_fallback": {"enabled": false, "rollout_pct": 0}}' \
  http://localhost:8000/api/settings/feature-flags
```

**Impact note:** Flag evaluations are cached per-user via `@functools.lru_cache(maxsize=2048)`. Cache entries persist for the process lifetime. A flag change takes effect for new user sessions; existing cached evaluations are unaffected until process restart. For immediate effect: restart the FastAPI process.

---

## 5. Incident Response Playbooks

### P0 — MMR Audit Chain Offline

**Symptom:** `GET /api/kernel/transparency` returns `{"fresh": false}` for > 60 seconds.

**Risk:** EU AI Act Article 12 compliance gap — the automated audit trail is interrupted. Log the gap duration for compliance documentation.

**Diagnosis:**
```bash
# Step 1: Is the kernel process running?
pgrep -a qemu-system-x86_64

# Step 2: Can we reach VBus?
python3 backend/scripts/bench_vbus.py --ping-only

# Step 3: VBus socket present?
ls -la /tmp/vos3_bridge.sock

# Step 4: Check kernel serial output
# (if running with -serial file:/tmp/kernel.log)
tail -50 /tmp/kernel.log | grep -E "MMR|PANIC|ASSERT"
```

**Resolution:**
1. If kernel process dead → restart QEMU (Section 3.1). MMR re-initializes. Root hash resets.
2. If VBus socket present but no response → check for kernel deadlock: `VBus PING` should respond in < 10ms. If timeout: `kill -9 $(pgrep qemu-system)` then restart.
3. After restart: verify `leaves` counter increments within 30 seconds.
4. Document gap start/end in incident report for compliance record.

---

### P0 — Billing Overdraft Alert

**Symptom:** `ConvexError("insufficient_credits")` rate > 0 in `/api/metrics/errors` with `severity: critical`.

**This should never happen** — OCC prevents it mathematically. If observed:

**Diagnosis:**
```bash
# Check if Convex OCC retry is firing
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/metrics/errors \
  | jq '.errors[] | select(.message | contains("occ") or contains("conflict"))'

# Check current credit balance for affected user (requires admin)
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8000/api/billing/credits?user_id=user_2abc..."
```

**Resolution:**
1. If OCC conflicts are retrying normally → Convex is absorbing contention, no action needed. Monitor retry rate.
2. If `insufficient_credits` at balance > 0 → likely a mutation serialization bug. Escalate immediately. Do NOT issue manual credit refunds until root cause is confirmed.
3. If Convex is reporting OCC timeout (> 3 retries) → Convex may be under extreme write contention. Activate backpressure manually: `_backpressure_active = True` via hot-reload or feature flag.

---

### P1 — Circuit Breaker OPEN (Convex Outage)

**Symptom:** All `POST /api/*` returning 503 with `Retry-After: 30` header. Error logs show `CircuitBreakerOpenError`.

**Diagnosis:**
```bash
# Check Convex connectivity directly
curl -s "$CONVEX_URL/api/query" \
  -H "Content-Type: application/json" \
  -d '{"path": "users:ping", "args": {}}' | jq .

# Check error rate
curl -s http://localhost:8000/api/metrics/errors \
  | jq '[.errors[] | select(.error_type == "CircuitBreakerOpenError")] | length'
```

**State machine:**
- OPEN → HALF_OPEN automatically after 30 seconds
- HALF_OPEN: first request is a probe. If probe succeeds → CLOSED (normal).
- If Convex is still down: probe fails → back to OPEN for another 30 seconds.

**Resolution:**
1. Wait 30 seconds — circuit auto-transitions to HALF_OPEN.
2. If Convex recovers during HALF_OPEN: circuit closes automatically.
3. If sustained Convex outage (> 5 minutes): activate maintenance mode:
   ```bash
   # Returns 503 "scheduled maintenance" to all write endpoints
   # Prevents thundering herd on Convex recovery
   curl -X POST http://localhost:8000/api/admin/maintenance/activate
   ```
4. On Convex recovery: deactivate maintenance, verify circuit reaches CLOSED.

**Frontend impact:** The `CircuitBreaker` → `HTTP 503` → frontend maintenance banner flow is automatic. Users see "Service temporarily unavailable" — no manual banner needed.

---

### P1 — Backpressure Active (Write Queue Spike)

**Symptom:** New write requests returning 503. `_backpressure_active = True` in logs. `len(ConvexWriteBuffer._queue) > 5,000`.

**Diagnosis:**
```bash
# Monitor queue depth (exposed via metrics)
curl -s http://localhost:8000/api/metrics/health \
  | jq '.services.convex_write_buffer'

# Check write error rate
curl -s http://localhost:8000/api/metrics/errors \
  | jq '[.errors[] | select(.source | contains("convex"))] | length'
```

**Resolution:**
1. Backpressure is self-healing: the write buffer flushes every 500ms. Queue will drain as Convex processes mutations.
2. If queue is growing (not draining): Convex is not accepting writes → see P1 (Circuit Breaker) playbook above.
3. To reduce write volume immediately: set `proactive_analysis` rollout to 0% (it generates ~6 background Convex writes per user session).
4. If queue drain takes > 5 minutes: the ConvexWriteBuffer's failed-mutation re-enqueue (B-HIGH-12 fix) may be creating a retry storm. Check for repeated OCC failures in the error log and escalate.

---

### P2 — EWMA Pressure Sustained > 0.85 (Degraded Mode)

**Symptom:** `GET /api/kernel/hardware/pressure` returns `pressure > 0.85`. LLM costs are minimized (Haiku is active for all non-critical roles), but quality is reduced.

**Note:** This is a **normal operating mode**, not an outage. The PID router is working as designed. Alert only if sustained > 4 hours (may indicate a kernel memory leak).

**Diagnosis:**
```bash
# Check kernel hugepage stats
curl -s -H "Authorization: Bearer $VOS3_TOKEN" \
  http://localhost:8000/api/kernel/hardware/pressure | jq '{
    pressure, congested, hp_used, hp_total, timeouts
  }'

# If timeouts > 0: VBus command dispatch is timing out → kernel under CPU pressure
# If congested = 1: token-bucket rate limiter hit → too many VBus requests
```

**Resolution:**
1. `pressure` > 0.85 but `congested` = 0 and `timeouts` = 0: kernel is just using memory normally. Monitor but no action needed unless sustained.
2. `congested` = 1: reduce VBus polling frequency. Increase poll interval in `kernel_routes.py` pressure endpoint from on-demand to a cached response (30s TTL).
3. `timeouts` increasing: kernel CPU saturated. Check QEMU `-smp` flag — increase vCPU count if possible.
4. Genuine memory pressure sustained: restart QEMU to reclaim slab pages. `vos3_heap_shrink()` is called automatically, but a restart resets all slab caches.

---

### P2 — LLM Timeout Cascade

**Symptom:** `/api/metrics/errors` shows `asyncio.TimeoutError` with `source: chat_routes`. P99 response time > 45 seconds.

**Timeout locations:**
- `chat_routes.py:920, 1144` — `asyncio.wait_for(llm_call, timeout=45.0)`
- `memory_routes.py` — `asyncio.wait_for(memory.query, timeout=5.0)`

**Resolution:**
1. Check LLM provider status pages.
2. If provider degraded: the EWMA router does not automatically switch providers on LLM timeout (it switches on kernel pressure). Manual override:
   ```bash
   # Force haiku_fallback ON for all users (immediate for new sessions)
   curl -X PATCH \
     -H "Authorization: Bearer $VOS3_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"haiku_fallback": {"enabled": true, "rollout_pct": 100}}' \
     http://localhost:8000/api/settings/feature-flags
   ```
3. If Haiku is also degraded: the fallback chain in `router.py` is `["claude-opus", "gpt", "gemini"]`. Configure the provider priority in `config/router.yaml` and restart.

---

## 6. Scale-Out Procedures (1M User Target)

### 6.1 Horizontal Backend Scaling

The FastAPI backend is stateless (all state in Convex). Scale by adding replicas behind a load balancer:

```bash
# Docker Compose scale
docker-compose up --scale backend=8

# Kubernetes HPA
kubectl autoscale deployment vos3-backend \
  --min=4 --max=32 \
  --cpu-percent=70
```

**Caution:** Each replica instantiates its own `ConvexWriteBuffer` and `CircuitBreaker`. The write buffer state is not shared between replicas — monitor per-replica queue depth separately.

**Feature flag cache:** Each replica has its own `lru_cache(maxsize=2048)`. Flag changes require restarting all replicas for immediate effect. This is acceptable — flags are for gradual rollouts, not instant enforcement.

### 6.2 QEMU / Kernel Scaling

The kernel is single-instance per deployment today (VBus is a Unix socket). For horizontal kernel scaling:

1. Run multiple QEMU instances on separate ports: `/tmp/vos3_bridge_0.sock`, `/tmp/vos3_bridge_1.sock`, etc.
2. Configure `kernel_bridge/service.py` with a pool of VBus connections (round-robin).
3. Each kernel instance has an independent MMR ledger. The backend must fan-out `MMR_ROOT` queries and aggregate (XOR or concatenate) for the transparency endpoint.

**Note:** Multi-kernel MMR federation is a v20.4 roadmap item. For 1M users on Version A, a single QEMU instance with 4 vCPUs and 8GB RAM handles 228.8 cmd/s sustained — scale the FastAPI fleet first.

### 6.3 Convex Write Pressure Management

At 1M users, sustained write pressure requires:

1. **Reduce proactive analysis rollout:** `proactive_analysis` → 5% (default). At 6 writes/session, 1M users × 5% = 50,000 active proactive sessions × 6 writes = 300,000 writes/session-cycle. Stagger this.

2. **Tune write buffer flush interval:** Increase `flush_interval_ms` from 500ms to 1,000ms for lower write frequency at higher coalescing ratio.

3. **Monitor Convex dashboard:** Watch the `billing:useCredits` mutation P99. Convex OCC retry storms appear as spikes in mutation duration.

4. **Backpressure activation point:** 5,000 items in `ConvexWriteBuffer._queue`. At 1M users, if write throughput exceeds Convex ingest rate, backpressure will activate. This is correct behavior — users see 503 + Retry-After and retry automatically.

---

## 7. The 30/30 Security Test Suite — Gold Standard QA

**File:** `backend/tests/test_load_security.py`
**Result:** `30 passed, 57 warnings in 6.44s` (certified 2026-04-14)

Run after any deployment:
```bash
cd backend
pytest tests/test_load_security.py -v --tb=short
```

### Section 1 — Security (Tests 1–10): Hard PASS Required

| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_prompt_injection_blocked` | System prompt not overridable via user input |
| 2 | `test_concurrent_credit_deduction_no_overdraft` | OCC prevents billing race condition |
| 3 | `test_500_error_no_pii_leak` | Stack traces never expose user data |
| 4 | `test_dependency_cve_pins` | FastAPI ≥ 0.115, stripe ≥ 11.0 |
| 5 | `test_idor_proactive_insights` | Users cannot read other users' insights |
| 6 | `test_kernel_exec_pipe_injection` | Shell metacharacter injection blocked |
| 7 | `test_jwt_hs256_excluded_in_prod` | HS256 algorithm absent from production JWT validation |
| 8 | `test_xff_rate_limit_bypass_blocked` | X-Forwarded-For header cannot spoof rate limits |
| 9 | `test_convex_parameterized_queries` | No string interpolation in Convex queries |
| 10 | `test_no_traceback_in_500` | Generic error messages on 5xx |

**If any of tests 1–10 FAIL:** Do not deploy. Escalate immediately.

### Section 2 — Performance (Tests 11–20): Audit Findings

| # | Test | Threshold |
|---|------|-----------|
| 11 | N+1 query check | `ConvexDB.find()` must not be called in loops |
| 12 | Proactive insights bounded | ≤ 500 items per user (`deque(maxlen=500)`) |
| 13 | Async LLM calls | `ainvoke` or `run_in_executor` — no sync blocking |
| 14 | OCC retry present | `_is_occ_conflict` handler exists |
| 15 | P99 response time | < 2 seconds for standard endpoints |
| 16 | 50MB payload rejected | HTTP 413 |
| 17 | Kernel ISR no blocking sleep | `sleep()` not in interrupt context |
| 18 | LLM factory pre-warm | Warmed in startup lifespan |
| 19 | Redis rate limit backed | Rate limit state in Redis, not in-memory |
| 20 | Convex projection | `listSummary` not `listAll` for large datasets |

### Section 3 — Reliability (Tests 21–30): Chaos Scenarios

| # | Test | What It Verifies |
|---|------|-----------------|
| 21–23 | Transient retry | httpx.ReadTimeout → auto-retry with Full Jitter |
| 24–26 | Structured logging | All log entries have `level`, `ts`, `user_id` (opaque) |
| 27–30 | Schema state machines | Convex schema guards prevent invalid state transitions |

---

## 8. Monitoring Checklist — Daily

| Check | Command / Location | Threshold |
|-------|--------------------|-----------|
| MMR fresh | `GET /api/kernel/transparency` → `fresh: true` | Alert if false > 60s |
| Leaf count growing | Compare two readings 60s apart | Alert if static |
| Pressure ratio | `GET /api/kernel/hardware/pressure` → `pressure` | Warning > 0.78, Alert > 0.90 |
| Congestion | Same endpoint → `congested` | Alert if 1 |
| Circuit breaker | Error log — `CircuitBreakerOpenError` | Alert on first occurrence |
| Write queue | `health` endpoint → `convex_write_buffer` | Warning > 3,000, Alert > 5,000 |
| 503 rate | `metrics/errors` → HTTP 503 count | Alert > 1%/min |
| LLM timeout rate | `metrics/errors` → `asyncio.TimeoutError` | Alert > 5/min |
| Security suite | `pytest test_load_security.py` | Alert if any of tests 1–10 FAIL |
| VBus P99 | `bench_vbus.py` weekly | Alert if > 15ms |

---

## 9. Environment Variables Reference

```bash
# Convex (Database)
CONVEX_URL=https://your-deployment.convex.cloud
CONVEX_DEPLOY_KEY=prod:...
NEXT_PUBLIC_CONVEX_URL=https://your-deployment.convex.cloud

# Clerk (Authentication)
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...
CLERK_ISSUER_URL=https://<domain>.clerk.accounts.dev

# Stripe (Billing)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# LLM Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...

# Feature Flags
FEATURE_FLAG_HMAC_KEY=vos3-feature-flag-salt-2026   # change in production

# Optional
REDIS_URL=redis://localhost:6379
NEXT_PUBLIC_API_URL=http://localhost:8000
RLIMIT_AS_MB=512                                    # sandbox address space limit
```

---

## 10. Quick Reference Card

```
┌─────────────────────────────────────────────────────────────────┐
│  VOS3 PRODUCTION QUICK REFERENCE — v20.3.0                      │
├──────────────────┬──────────────────────────────────────────────┤
│  KERNEL UP?      │  GET /api/kernel/status → connected: true    │
│  AUDIT LIVE?     │  GET /api/kernel/transparency → fresh: true  │
│  PRESSURE?       │  GET /api/kernel/hardware/pressure → < 0.85  │
│  BILLING OK?     │  No CircuitBreakerOpenError in /metrics/errors│
│  BACKPRESSURE?   │  503 rate < 1% — queue < 5,000              │
├──────────────────┼──────────────────────────────────────────────┤
│  SECURITY SUITE  │  pytest tests/test_load_security.py → 30/30  │
│  KERNEL ASSERTS  │  1,340 PASS, 0 FAIL (build-time verified)    │
│  VBUS PERF       │  228.8 cmd/s | P99 7.6ms | jitter 0.54ms    │
│  OVERDRAFT?      │  Impossible — Convex OCC (algebraic proof)   │
├──────────────────┼──────────────────────────────────────────────┤
│  KILL SWITCH     │  VBus: AGENT_KILL_ALL (SYS nr 497)          │
│  MAINTENANCE     │  POST /api/admin/maintenance/activate         │
│  FLAG OVERRIDE   │  PATCH /api/settings/feature-flags           │
└──────────────────┴──────────────────────────────────────────────┘
```

---

*VOS3 Go-Live Operations Manual — v20.3.0 — April 25, 2026*
*"Security by physics, not by policy."*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
