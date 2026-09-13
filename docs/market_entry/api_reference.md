# VOS3 API Reference
### Version: v2026.04.24-FINAL | Base URL: `https://api.yourdomain.com`

---

## Overview

All VOS3 API endpoints require a Bearer token issued by Clerk (RS256-signed, audience `vos3-api`). In production, HS256 tokens are structurally rejected. Every endpoint resolves the token to an `AuthenticatedUser` dataclass before the route handler is invoked — there is no untyped `user["id"]` access pattern in the codebase.

### Authentication

```http
Authorization: Bearer <clerk_jwt>
```

**Token requirements:**
- Algorithm: `RS256` (HS256 rejected in `ENVIRONMENT=production`)
- Issuer: `https://<your-domain>.clerk.accounts.dev`
- Audience: `vos3-api` (enforced when `CLERK_AUDIENCE` env var is set)
- JWKS cached: 1 hour per `kid`

### Global Response Headers

| Header | Description |
|--------|-------------|
| `Retry-After` | Present on 503 responses — seconds until circuit breaker closes or backpressure clears |
| `X-Request-ID` | Correlates logs to requests (Langfuse trace ID) |

### Error Schema

```json
{
  "detail": "string",
  "feature": "string",          // present on 503 circuit-breaker responses
  "maintenance_mode": true,     // present on 503 backpressure responses
  "retry_after": 60             // present on 503 responses
}
```

### Rate Limiting

All endpoints are subject to the global Redis-backed rate limiter: **100 requests/minute per IP** (sliding window, synchronized across all backend workers). Exceeding this limit returns:

```http
HTTP 429 Too Many Requests
Retry-After: <seconds>
```

---

## Billing & Monetization API

**Router prefix:** `/billing` (built into router, no additional `/api` prefix)

All billing endpoints pass through `BillingGuard` middleware, which performs a pre-flight credit check. The guard is **fail-open** — if the billing service is unavailable, requests proceed rather than blocking all users.

---

### `POST /billing/checkout`

Creates a Stripe Checkout Session for a subscription plan.

**Request:**
```json
{
  "plan_id": "pro_monthly",
  "success_url": "https://app.example.com/billing/success",
  "cancel_url": "https://app.example.com/billing/cancel"
}
```

**Response `200`:**
```json
{
  "checkout_url": "https://checkout.stripe.com/pay/cs_live_...",
  "session_id": "cs_live_..."
}
```

---

### `POST /billing/checkout/tokens`

Creates a Stripe Checkout Session for a token credit pack.

**Request:**
```json
{
  "token_pack": "1000000",
  "success_url": "https://app.example.com/billing/success",
  "cancel_url": "https://app.example.com/billing/cancel"
}
```

**Response `200`:**
```json
{
  "checkout_url": "https://checkout.stripe.com/pay/cs_live_...",
  "session_id": "cs_live_..."
}
```

---

### `POST /billing/portal`

Creates a Stripe Customer Portal session for self-serve subscription management.

**Request:**
```json
{
  "return_url": "https://app.example.com/settings/billing"
}
```

**Response `200`:**
```json
{
  "portal_url": "https://billing.stripe.com/session/..."
}
```

---

### `GET /billing/subscription`

Returns the current user's subscription status.

**Response `200`:**
```json
{
  "plan": "pro",
  "status": "active",
  "current_period_end": "2026-05-24T00:00:00Z",
  "cancel_at_period_end": false,
  "stripe_customer_id": "cus_..."
}
```

---

### `POST /billing/subscription/cancel`

Cancels the subscription at period end.

**Response `200`:**
```json
{
  "status": "cancel_at_period_end",
  "cancels_at": "2026-05-24T00:00:00Z"
}
```

---

### `POST /billing/subscription/resume`

Resumes a subscription scheduled for cancellation.

**Response `200`:**
```json
{
  "status": "active",
  "message": "Subscription cancellation reversed"
}
```

---

### `GET /billing/tokens`

Returns the current user's token balance.

**Response `200`:**
```json
{
  "balance": 847329,
  "unit": "tokens",
  "last_updated": "2026-04-24T15:00:00Z"
}
```

---

### `GET /billing/credits`

Returns detailed credit balance and recent transactions.

**Response `200`:**
```json
{
  "balance": 847329,
  "lifetime_granted": 2000000,
  "lifetime_used": 1152671,
  "plan_allowance": 1000000,
  "plan_reset_at": "2026-05-01T00:00:00Z"
}
```

---

### `POST /billing/credits/use`

Records credit consumption for a completed AI operation.

**Request:**
```json
{
  "amount": 1250,
  "operation": "codegen",
  "model": "claude-sonnet-4-6",
  "metadata": {
    "session_id": "sess_abc123",
    "role": "backend"
  }
}
```

**Response `200`:**
```json
{
  "balance_after": 846079,
  "transaction_id": "txn_..."
}
```

**Response `402`:** Insufficient credits (raised by `BillingGuard` pre-flight check before the route handler runs).

```json
{
  "detail": "Insufficient credits. Balance: 800, required: 1250."
}
```

---

### `GET /billing/credits/transactions`

Returns paginated credit transaction history.

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Max records returned |
| `offset` | integer | 0 | Pagination offset |
| `operation` | string | — | Filter by operation type |

**Response `200`:**
```json
{
  "transactions": [
    {
      "id": "txn_...",
      "amount": -1250,
      "operation": "codegen",
      "model": "claude-sonnet-4-6",
      "created_at": "2026-04-24T14:55:00Z"
    }
  ],
  "total": 247,
  "has_more": true
}
```

---

### `GET /billing/quota`

Returns the current user's usage quota across all dimensions.

**Response `200`:**
```json
{
  "tokens": {"used": 152671, "limit": 1000000, "reset_at": "2026-05-01T00:00:00Z"},
  "api_calls": {"used": 4821, "limit": 100000},
  "agents": {"active": 2, "limit": 10},
  "storage_mb": {"used": 247, "limit": 5000}
}
```

---

### `GET /billing/plans`

Returns available subscription plans (public endpoint, no auth required).

**Response `200`:**
```json
{
  "plans": [
    {
      "id": "starter",
      "name": "Starter",
      "price_monthly": 29,
      "token_allowance": 500000,
      "features": ["Chat", "Codegen", "5 agents"]
    },
    {
      "id": "pro",
      "name": "Pro",
      "price_monthly": 99,
      "token_allowance": 2000000,
      "features": ["All Starter", "Proactive Intelligence", "V-Core", "20 agents"]
    }
  ]
}
```

---

### `POST /billing/webhook`

Stripe webhook endpoint. Validates `Stripe-Signature` header using `STRIPE_WEBHOOK_SECRET`. Deduplicates on Stripe `event_id` — safe to replay.

**Supported events:**
- `checkout.session.completed` — Grant credits, activate subscription
- `customer.subscription.updated` — Update plan tier
- `customer.subscription.deleted` — Downgrade to free tier
- `invoice.payment_failed` — Notify user, grace period logic

> **Not for external callers.** Configure in Stripe Dashboard → Webhooks → `https://api.yourdomain.com/billing/webhook`

---

## Proactive Intelligence API

**Router prefix:** `/api/proactive`

The Proactive Intelligence engine runs background analysis on user data across six categories and surfaces time-sensitive insights. Rollout is controlled by the `proactive_analysis` feature flag (currently 5% of users).

---

### `POST /api/proactive/analyze`

Triggers a full proactive analysis across all six insight categories for the authenticated user.

**Request body:** _(empty)_

**Response `200`:**
```json
{
  "insights_generated": 3,
  "categories_analyzed": ["revenue_protection", "churn_prevention", "opportunity_detection"],
  "analysis_duration_ms": 1840
}
```

---

### `POST /api/proactive/analyze/revenue`

Targeted analysis for revenue protection signals only.

**Response `200`:**
```json
{
  "insight_id": "ins_...",
  "type": "revenue_protection",
  "priority": "urgent",
  "title": "3 invoices overdue > 30 days",
  "body": "Clients A, B, C have unpaid invoices totalling $4,200.",
  "action": {"label": "Send reminders", "route": "/clients/invoices/overdue"},
  "created_at": "2026-04-24T15:00:00Z"
}
```

---

### `POST /api/proactive/analyze/churn`

Targeted analysis for churn prevention signals.

---

### `POST /api/proactive/analyze/opportunities`

Targeted analysis for upsell/cross-sell opportunities.

---

### `GET /api/proactive/insights`

Returns the authenticated user's insight feed, sorted by priority then recency.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Filter: `revenue_protection`, `churn_prevention`, `opportunity_detection`, `schedule_optimization`, `risk_alert`, `weekly_insight` |
| `status` | string | Filter: `new`, `viewed`, `acted`, `dismissed` |
| `priority` | string | Filter: `urgent`, `high`, `medium`, `low` |
| `limit` | integer | Max 50 per page (default: 50) |

**Response `200`:**
```json
{
  "insights": [
    {
      "id": "ins_...",
      "type": "revenue_protection",
      "priority": "urgent",
      "status": "new",
      "title": "3 invoices overdue > 30 days",
      "body": "...",
      "action": {"label": "Send reminders", "route": "/clients/invoices/overdue"},
      "created_at": "2026-04-24T15:00:00Z",
      "viewed_at": null
    }
  ],
  "total": 7,
  "unread": 4
}
```

> **Memory bound:** Each user holds a maximum of **500 insights**. Oldest insights are evicted O(1) via `deque(maxlen=500)` when the limit is reached.

---

### `GET /api/proactive/insights/{insight_id}`

Returns a single insight by ID.

**Response `404`:** Insight not found or does not belong to the authenticated user (IDOR-protected).

---

### `POST /api/proactive/insights/{insight_id}/view`

Marks an insight as viewed. Transitions status from `new` → `viewed`. Sets `viewed_at` timestamp.

**Response `200`:**
```json
{
  "id": "ins_...",
  "status": "viewed",
  "viewed_at": "2026-04-24T15:05:00Z"
}
```

---

### `POST /api/proactive/insights/{insight_id}/dismiss`

Dismisses an insight. Transitions status to `dismissed`.

---

### `POST /api/proactive/insights/{insight_id}/action`

Executes the primary action associated with an insight (e.g., sends invoice reminder emails).

**Request:**
```json
{
  "action_id": "send_reminders",
  "parameters": {}
}
```

**Response `200`:**
```json
{
  "action_id": "send_reminders",
  "result": "3 reminder emails queued",
  "insight_status": "acted"
}
```

---

### `POST /api/proactive/reports/generate`

Triggers generation of a weekly executive insight report.

**Response `202`:** Report generation queued (async).

```json
{
  "report_id": "rep_...",
  "status": "generating",
  "estimated_ready_at": "2026-04-24T15:02:00Z"
}
```

---

### `GET /api/proactive/reports`

Returns the list of generated weekly reports.

---

### `GET /api/proactive/stats`

Returns aggregate statistics about the user's insight history.

**Response `200`:**
```json
{
  "total_insights": 47,
  "acted_count": 12,
  "dismissed_count": 8,
  "action_rate": 0.255,
  "by_category": {
    "revenue_protection": 8,
    "churn_prevention": 5,
    "opportunity_detection": 11
  }
}
```

---

## Feature Flags API

**Router prefix:** `/api/feature-flags`

Feature flags use HMAC-SHA256 deterministic bucket assignment. The same (flag, user_id) pair always produces the same bucket — no server-side session state required.

---

### `GET /api/feature-flags/`

Lists all feature flags. **Admin only.**

**Response `200`:**
```json
{
  "flags": [
    {
      "name": "proactive_analysis",
      "enabled": true,
      "rollout_pct": 5.0,
      "description": "Proactive AI analysis engine — 5% of users",
      "created_at": 1745500000.0,
      "updated_at": 1745500000.0
    },
    {
      "name": "streaming_codegen",
      "enabled": true,
      "rollout_pct": 100.0,
      "description": "Streaming code generation responses"
    },
    {
      "name": "haiku_fallback",
      "enabled": true,
      "rollout_pct": 100.0,
      "description": "Auto-downgrade to Haiku 4.5 under driver pressure"
    },
    {
      "name": "new_billing_portal",
      "enabled": false,
      "rollout_pct": 0.0,
      "description": "New self-serve billing portal (Stripe Customer Portal v2)"
    }
  ]
}
```

---

### `GET /api/feature-flags/{flag_name}`

Returns a single flag definition. **Admin only.**

---

### `PUT /api/feature-flags/{flag_name}`

Creates or updates a feature flag. **Admin only.**

**Request:**
```json
{
  "enabled": true,
  "rollout_pct": 25.0,
  "description": "Rolling out proactive analysis to 25% of users",
  "metadata": {"ticket": "VOS3-421"}
}
```

**Response `200`:** Updated flag object.

> **Warning:** Changing `FEATURE_FLAG_HMAC_KEY` in the environment invalidates all bucket assignments — every user may land in a different bucket.

---

### `DELETE /api/feature-flags/{flag_name}`

Deletes a custom flag. Built-in flags (`proactive_analysis`, `streaming_codegen`, `haiku_fallback`, `new_billing_portal`) cannot be deleted.

**Response `409`:** Attempted deletion of a built-in flag.

---

### `POST /api/feature-flags/evaluate`

Evaluates a flag for the authenticated user. Available to all authenticated users (not admin-only).

**Request:**
```json
{
  "flag_name": "proactive_analysis",
  "default_enabled": false
}
```

**Response `200`:**
```json
{
  "flag_name": "proactive_analysis",
  "enabled": true,
  "user_id": "user_2abc...",
  "bucket": 3.72,
  "rollout_pct": 5.0
}
```

**Evaluation logic:**
1. Flag must exist and have `enabled: true`
2. `rollout_pct >= 100` → always `true`
3. `rollout_pct <= 0` → always `false`
4. Otherwise: `HMAC-SHA256(key, f"{flag}:{user_id}") → bucket [0,100)`. Enabled if `bucket < rollout_pct`.

---

## Observability & Health API

**Router prefix:** `/api/metrics`

---

### `GET /api/metrics/health`

Returns system health across all subsystems. Used as Kubernetes liveness and readiness probe target.

**Response `200` (healthy):**
```json
{
  "status": "healthy",
  "timestamp": "2026-04-24T15:00:00Z",
  "services": {
    "convex": {"status": "ok", "latency_ms": 12},
    "redis": {"status": "ok"},
    "llm_providers": {"anthropic": "ok", "openai": "ok", "google": "ok"},
    "circuit_breaker": {"state": "closed", "failure_count": 0},
    "write_buffer": {"queue_depth": 47, "backpressure_active": false}
  },
  "kernel": {
    "pressure_ratio": 0.41,
    "ewma": 0.38,
    "degraded_mode": false
  }
}
```

**Response `503` (degraded):** One or more services unavailable. Frontend should display per-feature maintenance banners.

---

### `GET /api/metrics/`

Returns all observability metrics: request counts, costs, response times.

---

### `GET /api/metrics/costs`

Returns cost breakdown aggregated by model and role.

**Response `200`:**
```json
{
  "total_cost_usd": 142.87,
  "by_model": {
    "claude-sonnet-4-6": {"requests": 8420, "tokens": 12500000, "cost_usd": 62.50},
    "claude-haiku-4-5": {"requests": 3100, "tokens": 4200000, "cost_usd": 4.20},
    "gpt-4o": {"requests": 420, "tokens": 840000, "cost_usd": 25.20}
  },
  "savings_vs_opus_only_usd": 87.32
}
```

---

### `GET /api/metrics/health/stuck-threads`

Identifies HITL (Human-in-the-Loop) threads awaiting input beyond the threshold.

**Query parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_age_hours` | 24 | Threads older than this are flagged |

---

## Kernel API

**Router prefix:** `/api/kernel`

Direct interface to the VOS3 C kernel via VBus. All paths are validated against `ALLOWED_PATHS` before execution.

---

### `GET /api/kernel/status`

Returns kernel connectivity and VBus state.

**Response `200`:**
```json
{
  "kernel_connected": true,
  "vbus_latency_p99_ms": 7.6,
  "vbus_throughput_cmd_per_s": 228.8,
  "uptime_s": 86400
}
```

---

### `POST /api/kernel/ping`

Sends a VBus PING command. Use for heartbeat / latency measurement.

**Response `200`:**
```json
{
  "pong": true,
  "latency_ms": 3.2
}
```

---

### `GET /api/kernel/sysinfo`

Returns kernel system information: memory, CPU, hugepage usage.

**Response `200`:**
```json
{
  "memory_mb": 4096,
  "hugepages_total": 512,
  "hugepages_used": 214,
  "pressure_ratio": 0.418,
  "congestion": false,
  "ktext_crc32c": "a3f7e291"
}
```

The `ktext_crc32c` field is the live CRC32C of the kernel `.text` section. It should match the value computed at boot for tamper detection.

---

### `GET /api/kernel/processes`

Lists all user-space processes running inside the VOS3 kernel.

---

### `POST /api/kernel/execute`

Executes a whitelisted command inside the kernel sandbox.

**Request:**
```json
{
  "command": "ls",
  "args": ["-la", "/disk"],
  "timeout_s": 30
}
```

**5-layer sanitization applied before execution:**
1. Command allowlist check
2. Global blocked-flag check (`-c`, `-e`, `--eval`)
3. Blocked pattern substring check
4. Per-command safe-flag allowlist
5. Path validation (`ALLOWED_PATHS`)

`shell=False` always. `LD_PRELOAD` stripped from environment.

**Response `400`:** Command not in allowlist or flag rejected.

```json
{"detail": "Command 'bash' is not in the allowed executable list"}
```

---

*VOS3 API Reference — v2026.04.24-FINAL*
*Base URL and authentication must be configured per FINAL_DEPLOY_CHECKLIST.md*
*All endpoints require Bearer RS256 JWT unless marked as public*
