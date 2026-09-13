# Asynchronous Integrity Streaming — Architecture & Honest Scope

**Stage:** 10.3 (Streaming Fidelity scaffold)
**Date:** 2026-05-09
**Status:** architecture live; end-to-end latency not yet measured.

---

## Goal

Decouple SHA-384 verification of LLM tokens from the user-visible token stream so the Tauri shell renders **optimistically** (immediate, with a "pending" marker) while the backend integrity worker hashes off-thread. When the worker finishes, the badge promotes to "verified" — or redacts the token if the hash mismatched.

The Stage-10 plan asks for **≤ 1 ms UI jitter** on the optimistic-render path. This document describes how the architecture meets that constraint, and is **honest about which parts have been measured and which have not**.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  LLM token emitted                                                 │
│      │                                                             │
│      ├──► Optimistic render path                                   │
│      │     • backend appends to chat stream                        │
│      │     • frontend useChat hook setState                        │
│      │     • token shows in UI with "pending" marker               │
│      │     ⊳ NO hash on this path. NO kernel round-trip.           │
│      │                                                             │
│      └──► IntegrityWorker.submit(token_id, payload)                │
│              │ (non-blocking enqueue)                              │
│              ▼                                                     │
│            asyncio.Queue (bounded, 1024)                           │
│              │                                                     │
│              ▼  ── background coroutine ──                         │
│            sha384(payload) → compare to expected                   │
│              │                                                     │
│              ├──► publish_verified(VerifiedEvent)                  │
│              │     fastapi → Tauri channel → frontend event        │
│              │     useTokenVerification hook setState              │
│              │     UI promotes "pending" → "verified" badge        │
│              │                                                     │
│              └──► publish_revoked(RevokedEvent)                    │
│                    UI redacts the token + surfaces banner          │
└────────────────────────────────────────────────────────────────────┘
```

Components and their files in this repo:

| Component | File | Status |
|-----------|------|--------|
| Backend integrity worker | `backend/services/integrity_worker.py` | ✅ live |
| Frontend verification hook | `frontend/hooks/useTokenVerification.ts` | ✅ live |
| Tauri event channel | uses existing `onKernelEvent` in `frontend/lib/tauri-bridge.ts` | ✅ live (already shipped Stage 0) |
| Producer integration (chat router → submit) | `backend/api/chat_routes.py` | ❌ Stage 10's missing-files port |
| FastAPI → Tauri publisher (verified/revoked over websocket) | new file `backend/services/integrity_publisher.py` | ❌ Stage 10's missing-files port |
| Optimistic-render integration in useChat | `frontend/hooks/useChat.ts` | ❌ deferred — current useChat does not yet emit `tokenId`s |
| End-to-end jitter benchmark | `backend/tests/benchmarks/test_async_integrity_jitter.py` | ❌ Stage 12 test-suite port |

---

## Performance contract — what's claimed vs. measured

### Claimed (architectural)

The optimistic-render path has **zero hashing** between LLM emit and user-visible render. The producer's hot path is:

```
chat router emit  →  asyncio.Queue.put_nowait()  →  setState  →  React render
```

`put_nowait` is a single dictionary `append` + bounded-queue check; it does not call SHA-384 and never blocks on the worker. The worker runs on a separate coroutine and produces verified/revoked events asynchronously.

This is the same pattern browsers use for SRI + preload, Discord/Slack for message delivery, and most modern real-time UIs. It is well-understood; the tradeoff is "tokens may be redacted shortly after appearing" — acceptable for chat UX.

### Measured

**Nothing yet.** The end-to-end "token-emit → verified-badge-shown" latency is a system property. Measuring it requires:

1. A representative chat workload (model, prompt, tokens-per-second profile).
2. An instrumentation harness that timestamps each token at emit, at queue submit, at worker hash completion, and at frontend render.
3. A statistically-meaningful run length.

We have NOT run that benchmark. The plan calls for it in Stage 12's test-suite port (`backend/tests/benchmarks/test_async_integrity_jitter.py`); when that file lands, the assertion it checks is **P99 inter-token wall-clock jitter ≤ 1 ms on the UI side; verification completes within 50 ms median**.

Until the benchmark exists, "sub-1 ms UI jitter" is the **architectural target** of Stage 10.3, not a measured property of the running system. We have shipped what the target requires (off-thread hashing + non-blocking queue + event-based promotion); we have not yet proven the target is met in production. **A diligence-grade audit document would not call this "measured" — it would call it "architecturally consistent with the target, pending end-to-end benchmark in Stage 12."**

---

## Failure modes the architecture handles

| Failure | Behaviour |
|---------|-----------|
| Producer outruns worker | Bounded queue drops oldest entries and fires `revoked(reason=queue_overflow)` for each — UI redacts those tokens. Fail-closed. |
| Hash mismatch | Worker fires `revoked(reason=hash_mismatch)` with both digests in the payload; UI redacts and surfaces a banner. |
| Worker crash / shutdown | `IntegrityWorker.shutdown()` drains the queue and fires `revoked(reason=shutdown)` for every pending token. UI never leaves a token in "pending" state across a worker restart. |
| Backend → Tauri channel disconnect | Tauri's `onKernelEvent` returns no-op; UI keeps tokens in pending state until reconnect. Frontend SHOULD surface a "verification offline" banner — not yet wired. |
| Browser mode (no Tauri) | Hook returns no-op listeners; tokens display with the pending marker indefinitely. Acceptable for frontend dev without a kernel. |

---

## Integration checklist for Stage 10's missing-files port

When the Stage-10 backend port lands, wire these:

1. `backend/api/chat_routes.py` — at every `yield {"token": ...}` point, also call `integrity_worker.submit(token_id, token_bytes)`.
2. `backend/services/integrity_publisher.py` — pass-through that receives `VerifiedEvent` / `RevokedEvent` from the worker and forwards over the existing chat websocket as `chat:token-verified` / `chat:token-revoked` events.
3. `frontend/hooks/useChat.ts` — assign each emitted token an integer `tokenId` (already present in some implementations as a sequence counter); call `markPending(tokenId)` at emit; consume `states[tokenId]` in the rendering layer to switch the badge.
4. `frontend/components/chat/ChatStream.tsx` — render the badge:
    ```tsx
    {state === 'pending'  && <span className="opacity-50">⋯</span>}
    {state === 'verified' && <span className="text-emerald-400">✓</span>}
    {state === 'revoked'  && <span className="text-red-400">redacted</span>}
    ```

None of those four changes require kernel modifications — they are all userspace.
