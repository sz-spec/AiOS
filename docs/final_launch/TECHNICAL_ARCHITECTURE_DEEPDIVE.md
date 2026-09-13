# VOS3 Technical Architecture Deep-Dive
## Layer-by-Layer: UEFI Boot → Next.js Frontend
**v20.3.0 | April 2026 | CONFIDENTIAL — ENGINEERING REVIEW**

---

## Overview

VOS3 is a seven-layer stack. Each layer has a precisely defined contract with the layers above and below it. The contracts are enforced by hardware (IOMMU, MMU, PCID, TPM), mathematics (SHA-256 hash chain), and formally specified concurrency primitives (Convex OCC, EWMA hysteresis). This document traces every load-bearing decision from the first instruction after UEFI to the React component that displays the live audit root hash.

```
Layer 7: Next.js Frontend        — real-time transparency, BillingGuard UI
Layer 6: Convex Database          — OCC serialization, billing atomicity
Layer 5: FastAPI Backend          — EWMA PID router, circuit breaker, backpressure
Layer 4: VBus Protocol            — HMAC-SHA256 authenticated ASCII command bus
Layer 3: Kernel Core              — MMR audit, slab allocator, PCID agent isolation
Layer 2: UEFI Physical Handover   — Limine → VOS3 MMU state, PCID init, boot measurement
Layer 1: Hardware                 — TPM 2.0 PCR, VT-d IOMMU, RDSEED, Intel PCID
```

---

## Layer 1 — Hardware Substrate

### TPM 2.0 — Arrow of Time

**File:** `kernel/src/sec/tpm2.c`

VOS3 uses TPM 2.0 Command Response Buffer (CRB) interface, located via the ACPI `TPM2` table. At boot:

1. `tpm2_init()` — parses ACPI table inventory for signature "TPM2", verifies CRB locality 0 ready, sends `TPM2_CC_Startup(TPM_SU_CLEAR)`.
2. `tpm2_boot_measurement()` — `tpm2_extend_pcr(PCR_KTEXT=0, sha256(ktext_hash ‖ kaslr_slide))`.

PCR[0] is written exactly once at boot, before the scheduler starts. The TPM's PCR extend operation is one-way (SHA-256 based): `PCR[i] := SHA-256(PCR[i] ‖ measurement)`. A rebooted kernel with a different KASLR slide, a patched `.text` section, or a different kernel binary produces a different PCR[0]. This is the "Arrow of Time": the hardware proves when this exact kernel binary started.

PCR[8] is extended with the MMR root after the first 1,000 syscalls, binding the audit ledger to the hardware trust anchor.

**Static buffers:** `g_cmd_buf[256]`, `g_rsp_buf[256]` — no heap allocation. Safe no-op if QEMU is launched without `-tpmdev`.

### VT-d IOMMU — Memory Sovereignty

**Relevant MSR:** ACPI DMAR (DMA Remapping) table, enumerated by `vos3_acpi_init()`.

The IOMMU sits on the PCIe root complex and translates every DMA address issued by any PCI device. VOS3 registers AI inference memory pages (ivshmem zones) as "deny-all" in the IOMMU second-level page tables. This is enforced before any VMBus channel offer is exposed to the Windows guest partition.

**Formal invariant:** `∀ PCI transaction T from Windows partition: DMA_target(T) ∩ AI_pages = ∅`

This is hardware enforcement: software running at any ring level in the Windows partition cannot override it. Physical IOMMU register access requires kernel control — which VOS3 holds.

### Intel PCID — Per-Agent TLB Isolation

CR4.PCIDE (bit 17) enables Process Context Identifiers. Each PCID is a 12-bit tag on every TLB entry. When a CR3 load includes bit 63 set, the CPU retains TLB entries for the current PCID — zero invalidation cost.

PCID assignments:

| PCID | Owner |
|------|-------|
| 0 | Kernel (always cached) |
| 1 | Shared AI model weights |
| 2–9 | Agent slots 0–7 (per-agent inference context) |
| 10–4095 | Future: user processes, sandboxed apps |

### RDSEED Entropy — Hardware Unpredictability

Each MMR leaf XORs 8 bytes of `RDSEED`/`RDRAND` output into the leaf hash before tree insertion. RDSEED taps the on-die hardware entropy source (thermal noise). An attacker who captures and replays a known syscall sequence cannot reproduce the same MMR root because the RDSEED output at each call is physically unpredictable.

---

## Layer 2 — UEFI Physical Handover

**File:** `kernel/src/boot/uefi_bridge.c`

`vos3_uefi_bridge_handover()` is called once from `boot_drivers_init()`, after `mmr_init()` and before the scheduler starts. It executes in three sub-phases:

### Sub-phase 2A — CPU State Validation

`bridge_validate_cpu_state()` verifies the CPU is in the expected post-UEFI state:
- CR0: PE (protected mode) + PG (paging) + WP (write-protect) must be set
- Logs CR4 flags: SMEP (bit 20), SMAP (bit 21), UMIP (bit 11)

W^X is enforced at the MMU PTE level: a PTE sanitizer strips any entry with both W (bit 1) and X (bit 63 clear in NX mode) simultaneously. `mprotect`/`mmap` calls that request `PROT_WRITE | PROT_EXEC` receive `-EINVAL`.

### Sub-phase 2B — PCID Activation

`bridge_pcid_setup()`:

1. **Errata detection:** `detect_invlpg_pcid_bug()` checks CPUID family 6, models `{0x97, 0x9A, 0xB7, 0xBA, 0xBE, 0xBF}` — Alder Lake and Raptor Lake chips with INVLPG+PCID interaction bug (Intel microcode MC0x012E / MC0x0122). If detected, sets `g_invpcid_type2_required = 1`.
2. **PCIDE enable:** `CR4 |= (1 << 17)`.
3. **No-flush CR3:** `cr3 |= (1ULL << 63); write_cr3(cr3)`. Bit 63 of CR3 instructs the CPU not to flush TLB entries for the current PCID on subsequent CR3 reloads. All agent context switches use this mode.

**Quantified benefit:** At 10,000 agent switches/second, PCID eliminates 200 cycles × 10,000 = 2,000,000 cycles/second = ~0.67ms/second per agent; at 8 agents: 16ms/second saved — measurable in P99 latency.

### Sub-phase 2C — Boot Measurement

`bridge_boot_measurement()` records MMR leaf 0: `mmr_record_event(k_handover_label)` where `k_handover_label[32]` is the pre-computed `SHA-256("physical_handover")`. This seals the handover event into the immutable audit chain before any agent runs.

---

## Layer 3 — Kernel Core

### MMR Audit Ledger

**File:** `kernel/src/sec/mmr_audit.c` (227 lines) + `kernel/src/sec/mmr_audit.h`

The Merkle Mountain Range is a forest of perfect binary trees. New leaves are appended via O(log N) amortized peak merging. The root is computed via "Bagging-the-Peaks."

**Data layout (BSS):**
```c
static uint8_t  g_peaks[64][32];      /* 2,048 bytes — supports 2^64 leaves */
static uint8_t  g_peak_valid[64];     /* 64 bytes */
static uint64_t g_leaf_count;         /* 8 bytes */
static uint8_t  g_merge_buf[72];      /* scratch — no heap */
```

**Leaf structure:**
```c
typedef struct mmr_leaf {
    uint64_t timestamp;    /* rdtsc() at event time */
    uint64_t syscall_nr;   /* 0 for non-syscall events */
    uint8_t  entropy[8];   /* RDSEED bytes */
    uint8_t  data[32];     /* caller-supplied context */
} mmr_leaf_t;
```

**Append algorithm (`mmr_append`):**
```
1. leaf_hash = SHA-256(timestamp ‖ syscall_nr ‖ entropy ‖ data)
2. XOR leaf_hash[0..7] with entropy[]   ← RDSEED binding
3. carry = leaf_hash
4. for h = 0 to 63:
     if g_peak_valid[h]:
       carry = SHA-256(g_peaks[h] ‖ carry ‖ h_byte)  ← domain separation
       g_peak_valid[h] = 0
     else:
       g_peaks[h] = carry; g_peak_valid[h] = 1; break
5. g_leaf_count++
```

The height byte in step 4 domain-separates merges at different tree levels — preventing second-preimage attacks where two different merge sequences produce the same node hash.

**Root computation (`mmr_root`):**
```
running = 0
for h = 63 downto 0:
  if g_peak_valid[h]:
    running = SHA-256(running ‖ g_peaks[h])
final_root = SHA-256(running ‖ leaf_count[8])
```

Including `leaf_count` in the final hash defeats length-extension attacks.

**Integration into syscall path:**
```c
/* In syscall_dispatch() — every syscall */
mmr_record_syscall(syscall_nr, args[0]);
```

`mmr_record_syscall()` calls `vos3_entropy_get_u64()` (RDSEED), encodes `arg0` into `data[0..7]`, and calls `mmr_append()`.

**VBus surface:** `cmd_mmr_root()` in `vbus_ai_cmds.c` — returns `root=<64hex>|leaves=<decimal>`.

### KTEXT Integrity — Live .text Verification

**File:** `kernel/src/drivers/vbus_diag_cmds.c:444–514`

At boot, `vos3_ktext_hash_init()` computes `g_ktext_boot_crc = crc32c_sw(_text_start, _text_end - _text_start)` using the Castagnoli polynomial (0x82F63B78). Every VBus `KTEXT_HASH` command recomputes the live CRC and returns `{"boot_crc": "X", "live_crc": "Y", "match": true|false}`. Any in-memory `.text` tampering is detectable per heartbeat cycle.

The three signals — KTEXT CRC (per-heartbeat), MMR root (per-syscall), TPM PCR[0] (per-boot) — form the Arrow of Time: an attacker must simultaneously forge a SHA-256 CRC collision, defeat the SHA-256 MMR chain, and rewrite the TPM's non-volatile PCR store.

### Slab Memory Allocator — O(1) Per-Allocation

**File:** `kernel/src/mm/heap.c`

Eight size classes: `{16, 32, 64, 128, 256, 512, 1024, 2048}` bytes. Each class has a free-list of pre-allocated slab pages. Allocations from the free-list are O(1). Free-list pointers are XOR-obfuscated with `g_slab_freelist_cookie` (random at boot) — defeating use-after-free and freelist corruption exploits. `vos3_heap_shrink()` returns reclaimable empty slab pages to the page allocator.

### VBus Dispatcher — 142 Commands, HMAC-SHA256 Authenticated

**File:** `kernel/src/drivers/virtio_bridge.c`

VBus uses a ring buffer transport (zero memmove, zero-copy CRC) over a Unix domain socket. Each frame carries a 32-byte HMAC-SHA256 MAC computed with a per-session key exchanged during HANDSHAKE. Constant-time verification prevents timing side-channels.

The dispatcher handles 142 commands organized into functional groups:
- **Legacy COM2 surface (22 commands):** PING, STAT, WRITE, READ, LS, READC, APPEND, LSM, RMDIR, EXEC, MKDIR, UNLINK, RENAME, SYSINFO, BUILD_UUID, PROCS, HTTPGET, APPLOAD, APPSTAT, APPKILL, APPLIST, AGENT_KILL_ALL
- **v20.3 Transparency:** MMR_ROOT, KTEXT_HASH, DRIVER_PRESSURE, HP_STATS, PCI_LIST
- **AI model lifecycle:** MODEL_START/SYNC/REWIND/DONE/CHECK/TAMPER/LOAD_UPDATE
- **Agent slot management:** SLOT_START/FINISH/SYNC/SUSPEND/RESUME/STATUS/SNAPSHOT (and 20+ more)
- **Orchestration:** ORCH_START/DISPATCH/STOP/STATS/GHOST
- **Diagnostics:** HEARTBEAT_STATUS, QUERY_CR4, KASLR_BASE, CPU_PATH, TLB_AUDIT, SMP_STATUS

**Watchdog:** Every command is wrapped with a cycle counter (`lfence; rdtsc`). If a command exceeds `VBUS_CMD_CYCLE_LIMIT = 1,000,000,000` cycles, `g_dispatch_timeouts` increments — published via DRIVER_PRESSURE.

---

## Layer 4 — VBus Protocol

### DRIVER_PRESSURE — The Kernel → Cloud Telemetry Channel

**Kernel side:** `vbus_ai_cmds.c:3089–3139`

```c
void cmd_driver_pressure(void) {
    uint32_t hp_used, hp_total;
    vos3_pmm_hugepage_stats(&hp_used, &hp_total);
    send_ok_fmt("congested=%u|hp_used=%u|hp_total=%u|timeouts=%llu",
                g_congestion, hp_used, hp_total, g_dispatch_timeouts);
}
```

Three signals:
- `g_congestion` — set by the token-bucket rate limiter and CRC-ban system
- `hp_used / hp_total` — hugepage pressure ratio (direct memory availability)
- `g_dispatch_timeouts` — cumulative watchdog timeout counter (latency health)

**FastAPI ingestion:** `GET /api/kernel/hardware/pressure` (`kernel_routes.py:480–542`) — runs `VBusDriver.send_command("DRIVER_PRESSURE")` in a thread executor, parses the ASCII pairs, computes `pressure = round(hp_used / hp_total, 4)`, and returns a JSON object including the `pressure` ratio.

---

## Layer 5 — FastAPI Backend

### EWMA PID Router — The Closed-Loop Cost Controller

**File:** `backend/src/efficiency/router.py:104–167`

```python
_PRESSURE_ALPHA          = 0.30    # EWMA smoothing factor
_PRESSURE_ENTER_THRESHOLD = 0.85   # degraded mode trigger
_PRESSURE_EXIT_THRESHOLD  = 0.75   # degraded mode release
_CRITICAL_ROLES           = {"architect", "reviewer", "researcher-deep"}
_HAIKU_MODEL              = "claude-haiku-4-5-20251001"
```

The EWMA update and hysteresis:
```python
def _update_pressure_ewma(raw_ratio: float) -> bool:
    global _pressure_ewma, _in_degraded_mode
    _pressure_ewma = _PRESSURE_ALPHA * raw_ratio + (1.0 - _PRESSURE_ALPHA) * _pressure_ewma
    if not _in_degraded_mode and _pressure_ewma > _PRESSURE_ENTER_THRESHOLD:
        _in_degraded_mode = True          # enter degraded — downgrade non-critical
    elif _in_degraded_mode and _pressure_ewma < _PRESSURE_EXIT_THRESHOLD:
        _in_degraded_mode = False         # exit degraded — restore full quality
    return _in_degraded_mode
```

Model assignment:
```python
def assign_model_with_pressure_check(role, complexity, *, driver_pressure):
    degraded = _update_pressure_ewma(driver_pressure["pressure"])
    congested = driver_pressure.get("congested", 0)
    if (degraded or congested) and role not in _CRITICAL_ROLES:
        return _HAIKU_MODEL           # $0.80/MTok input vs ~$15 for Opus
    return base_router.assign_model(role, complexity)
```

**Why α = 0.30?** A higher α (e.g., 0.50) makes the EWMA react faster but oscillates under bursty load. A lower α (e.g., 0.10) is too sluggish to protect against sustained pressure. α = 0.30 with the [0.75, 0.85] hysteresis band was empirically validated against the Locust load profile (`backend/tests/perf/locustfile.py`). The band prevents "chatter" — rapid oscillation between degraded and full quality modes.

**Model cost tiers (router.yaml):**

| Role | Normal Model | Degraded Model | $/MTok (in) |
|------|-------------|----------------|-------------|
| architect | gpt-4o | claude-haiku-4-5 | $2.50 → $0.80 |
| frontend | claude-sonnet-4-6 | claude-haiku-4-5 | $3.00 → $0.80 |
| backend | claude-sonnet-4-6 | claude-haiku-4-5 | $3.00 → $0.80 |
| tester | gemini-2.5-flash | claude-haiku-4-5 | $0.15 → $0.80 |
| reviewer | claude-opus-4-7 | **never degraded** | $15.00 |
| researcher-deep | claude-opus-4-7 | **never degraded** | $15.00 |
| coding-complex | o3-mini | claude-haiku-4-5 | variable → $0.80 |

The haiku_fallback feature flag (`services/feature_flags.py`) gates this behavior globally with deterministic per-user HMAC bucketing — allowing gradual rollout or instant rollback.

### BillingGuard Middleware

**File:** `backend/middleware/billing_guard.py`

Mounted before every `POST`/`PUT` to `/api/chat`, `/api/agents`, `/api/codegen`. Reads estimated cost (`ESTIMATED_CREDITS = {"chat": 10, "agents": 50, "codegen": 20}`), calls `stripe_svc.get_token_balance(user_id)`, returns HTTP 402 with `{"error": "insufficient_credits", "balance": N, "required": M}` if balance < estimated.

**Fail-open design:** If the Stripe service is unreachable, BillingGuard logs a warning and allows the request. This is a deliberate availability tradeoff — a billing check failure should not prevent legitimate users from working.

### Circuit Breaker

**File:** `backend/core/circuit_breaker.py`

Three-state automaton: `CLOSED → OPEN → HALF_OPEN → CLOSED`.

| State | Behavior |
|-------|----------|
| CLOSED | Normal operation; counts consecutive failures |
| OPEN | Rejects all calls; returns 503 + Retry-After header |
| HALF_OPEN | Allows one probe; success → CLOSED, failure → OPEN |

Thresholds: `failure_threshold=5`, `cooldown_seconds=30.0`. Named global registry: `get_breaker("convex")`. The Convex client wraps every mutation and query with the breaker — Convex outages produce immediate 503 responses rather than 30-second timeout cascades.

### Backpressure

**Files:** `backend/startup.py:19–110, 311`

```python
_WRITE_BUFFER_QUEUE_ALERT_THRESHOLD = 5_000
_backpressure_active: bool = False
```

A background coroutine (`_convex_health_monitor`, 30-second interval) checks `len(ConvexWriteBuffer._queue)`. When the queue exceeds 5,000 items, `_backpressure_active = True`. The `require_capacity()` FastAPI dependency (attached at router level via `Depends`) returns HTTP 503 + Retry-After for all new write requests until the queue drains.

**Combined reliability model:**
- P(Convex outage) ≈ 10⁻³/hour (three-nines Convex SLA)
- P(write queue spike) ≈ 10⁻³/hour (empirical from load testing)
- P(both simultaneously) ≈ 10⁻⁶/hour → 99.9999% uptime

### Convex Write Buffer and Full Jitter Retry

**File:** `backend/db/convex.py`

`ConvexWriteBuffer` coalesces writes by `(function_name, buildId, agentName)` with last-write-wins semantics. Flushes every 500ms in batches of 10 via `asyncio.gather`. Failed mutations are re-enqueued (B-HIGH-12 fix) to prevent silent data loss.

**Full Jitter retry** (AWS formula):
```python
_RETRY_DELAYS = [0.02, 0.05, 0.15]   # base delays (seconds)

for attempt in range(_MAX_RETRIES):
    base = _RETRY_DELAYS[attempt]
    delay = random.uniform(0, base * 2)   # Full Jitter
    await asyncio.sleep(delay)
```

Applied to: transient HTTP errors (429, 502, 503, 504), OCC conflicts (Convex `_is_occ_conflict`), and network timeouts (ReadTimeout, ConnectTimeout, PoolTimeout).

### Feature Flags

**File:** `backend/services/feature_flags.py`

HMAC-SHA256 per-user bucketing: `bucket = int.from_bytes(HMAC-SHA256(user_id, key)[:4]) % 100`. Decorated with `@functools.lru_cache(maxsize=2048)` — evaluated once per user per process, then cached. This reduced CPU in the flag hot-path from O(n) HMAC per request to O(1) cache lookup.

Default flag set:
- `haiku_fallback` — enabled, 100% — hardware-pressure LLM downgrade
- `streaming_codegen` — enabled, 100%
- `proactive_analysis` — enabled, 5% rollout
- `new_billing_portal` — disabled, 0%

Redis-backed persistence (`vos3:feature_flags`), in-memory fallback, fail-open evaluation.

---

## Layer 6 — Convex Database (OCC Billing Engine)

**File:** `frontend/convex/billing.ts`

The `useCredits` mutation enforces billing atomicity at the database level:

```typescript
export const useCredits = mutation({
  handler: async (ctx, { userId, amount, reason }) => {
    await requireOwnership(ctx, userId);           // IDOR guard
    const existing = await ctx.db.query("userCredits")
      .withIndex("by_user", q => q.eq("userId", userId)).unique();
    const currentBalance = existing?.balance ?? 0;
    if (currentBalance < amount)
      throw new ConvexError("insufficient_credits");
    const newBalance = currentBalance - amount;
    if (newBalance < 0)                            // double-guard
      throw new ConvexError("insufficient_credits");
    await ctx.db.patch(existing._id, { balance: newBalance });
    await ctx.db.insert("creditTransactions", { ... });
  },
});
```

Convex OCC semantics: the `ctx.db.query(...).unique()` call binds the document version number. If two concurrent mutations read the same version, the second commit triggers a version conflict, Convex auto-retries with the post-deduction balance, and the `currentBalance < amount` guard fires. No overdraft is possible.

**Abuse guard:** `addCredits` bounds `amount ∈ [1, 10,000]` — prevents administrative credit-stuffing attacks.

**Webhook idempotency (`tools/stripe_service.py:350–400`):**
```python
event_id = event.id
result = await convex.mutation("billing:recordWebhookEvent", {
    "eventId": event_id, "type": event_type
})
if result.get("duplicate"):
    return   # already processed — skip
```

Every Stripe webhook is deduplicated by `event.id` stored in Convex before processing. Stripe retries (for unacknowledged webhooks) are idempotent.

---

## Layer 7 — Next.js Frontend

### Live MMR Transparency Widget

**File:** `frontend/hooks/useTransparency.ts`

```typescript
export function useTransparency(pollIntervalMs = 3000) {
    const { getToken } = useAuth();
    // Polls GET /api/kernel/transparency with Clerk Bearer token
    // Returns: { data: { fresh, root, leaves }, loading, lastUpdated }
}
```

The `/health` page renders `KernelTransparencyWidget`:
- SHA-256 root hash (truncated display, full 64-char on hover)
- Syscall leaf count (append-only counter)
- EU AI Act Article 12 compliance checklist (4 items)
- LIVE/OFFLINE pill — green pulsing dot when kernel is online

The 3-second poll interval gives real-time visibility into the audit chain. Each refresh confirms the MMR root has advanced — proving the kernel is still recording events.

### Auth Architecture

All 11 API-calling hooks send `Authorization: Bearer <clerk_jwt>` headers. The backend verifies each token against the Clerk JWKS endpoint (cached 1 hour). JWT algorithm: RS256 only — HS256 is structurally absent from all production routes.

---

## Hyper-V SynIC Bridge — Windows Coexistence

**File:** `kernel/src/hyperv/hyperv_bridge.c`

VOS3 detects Hyper-V presence via `CPUID[0x40000000].EBX == "Micr"`. If running as a Hyper-V Gen-2 guest, `hyperv_synic_init()` configures the Synthetic Interrupt Controller:

```c
#define VOS3_GUEST_OS_ID    0x000056534F330003ULL   /* "VSO3" v20.3 */
#define VOS3_SYNIC_SINT_VBUS  7U                    /* SINT7 for VBus */
#define VOS3_SYNIC_VECTOR     0x50U                 /* IDT vector */
```

MSR write sequence:
1. `GUEST_OS_ID` ← `VOS3_GUEST_OS_ID` (Hyper-V requires guest OS identification)
2. `SIMP` ← physical address of `g_simp_page` | 1 (enable message page)
3. `SIEFP` ← physical address of `g_siefp_page` | 1 (enable event flags page)
4. `SINT7` ← vector 0x50 | `HV_SINT_AUTO_EOI` | NOT MASKED
5. `SCONTROL` ← `HV_SCONTROL_ENABLE`

`hyperv_synic_signal()` writes 0 to `HV_X64_MSR_EOM` — the End Of Message MSR — which triggers Hyper-V to deliver the pending SynIC interrupt to the Windows service listening on the VMBus channel.

**IOMMU isolation checkpoint:** `hyperv_assert_iommu_isolation()` verifies `DMAR DRHD count > 0` before exposing any VMBus channel offer. This ensures the VT-d hardware is active before any Windows-observable surface is created.

---

## ACPI DSAR — NPU Cluster Topology

**File:** `kernel/src/drivers/acpi.c`

VOS3 parses the non-standard ACPI `DSAR` (Device-Specific ACPI Resources) table (signature `"DSAR"`). Each 16-byte entry describes one NPU cluster: `{device_id, cluster_id, compute_capacity, memory_bandwidth}`. The parsed topology is stored in `g_acpi_info.dsar_clusters[8]` and exposed via:
- VBus `PCI_LIST` command — for backend hardware topology queries
- `GET /api/kernel/hardware/npu-topology` — JSON cluster array for dashboard display

This enables the backend to make inference routing decisions based on actual hardware heterogeneity — e.g., routing high-memory-bandwidth tasks to the cluster with the highest `memory_bandwidth` score.

---

## Security Properties Summary

| Property | Mechanism | Verification |
|----------|-----------|--------------|
| Audit tamper-resistance | MMR SHA-256 chain | `GET /api/kernel/transparency` |
| Kernel integrity | KTEXT CRC32C + TPM PCR[0] | `VBus KTEXT_HASH` |
| Agent memory isolation | PCID + MMU PTE | CR4.PCIDE, no-flush CR3 |
| DMA isolation (Hyper-V) | VT-d IOMMU | ACPI DMAR DRHD count |
| Stack safety | Canaries (394 sites) + SMAP | Binary analysis |
| W^X enforcement | PTE sanitizer | `mprotect` EINVAL test |
| JWT algorithm | RS256 only | Grep: 0 HS256 in prod |
| Path traversal | 11 endpoints `_validate_path` | Unit tests |
| RCE surface | 5-layer command defense | 30/30 security suite |
| SSRF | DNS pinning + 10 blocked networks | Integration tests |
| Billing race | Convex OCC single mutation | Chaos concurrency tests |
| Stripe replay | `event_id` deduplication | Webhook idempotency tests |

---

## Performance Baseline

| Metric | Value | Source |
|--------|-------|--------|
| VBus throughput | 228.8 cmd/s | `bench_vbus.py` |
| VBus P99 latency | 7.6ms (7.695ms precise) | `bench_vbus.py` |
| VBus P99.9 latency | 7.85ms | `FINAL_CERTIFICATION_REPORT.md` |
| VBus jitter (P99−P50) | 0.54ms | `bench_vbus.py` |
| Kernel asserts | 1,340 PASS, 0 FAIL | `Makefile:751` string audit |
| MMR BSS footprint | 2,048 bytes | Static peak array `g_peaks[64][32]` |
| Feature flag eval | O(1) — LRU cache 2,048 | `feature_flags.py` |
| Write queue threshold | 5,000 items | `startup.py` |
| Circuit breaker | 5 failures / 30s | `circuit_breaker.py` |
| LLM timeout | 45 seconds | `chat_routes.py:920,1144` |
| Memory recall timeout | 5 seconds | `memory.query` |
| HMAC cache | 2,048 entries (LRU) | `feature_flags.py` |

---

*VOS3 Technical Architecture Deep-Dive — v20.3.0 — April 25, 2026*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
