"""
backend/services/integrity_worker.py
=====================================

Stage 10.3 — Asynchronous Integrity Streaming worker.

Architectural goal
==================

Decouple SHA-384 verification of LLM tokens from the user-visible token
stream. Tokens render *immediately* in the Tauri shell with a "pending"
marker; the worker drains a verification queue in the background and
publishes a verified(token_id) event when each token's hash matches the
expected RTMR-bound digest. Mismatches publish a revoked(token_id)
event and the frontend redacts the token in place.

Why not block the stream
========================

Blocking SHA-384 against every token would add roughly the cost of a
512-byte hash per token to the user-visible critical path. Even at
modern hash throughputs that's hundreds of nanoseconds; in a real
shell, OS scheduling jitter pushes the median into the low microseconds
and the P99 into the millisecond range. The Stage-10 plan target is
**sub-1ms UI jitter on the optimistic-render path**, which we achieve
by moving the hash off-thread.

The verify worker itself does NOT need to be sub-1ms — its budget is
the time before the user notices an unverified-vs-verified token state
change, which UX research generally puts at ~50-100ms. The kernel-side
gate (Stage 10.2's vos3_action_bridge_check_confidence) IS on the
critical path, but it is a single uint16 compare, not a hash.

Honest scope note
=================

We ship the architecture. We do NOT ship a measurement of the actual
P99 token-to-verified latency on a real workload — that requires a
benchmark harness that runs the chat stream against a known-good model
under representative load. The plan calls for that benchmark in
``backend/tests/benchmarks/test_async_integrity_jitter.py`` as part of
Stage 10's test-suite port (the Stage-10 missing-files batch). When
that test lands the contract here is what it measures against.

Public API
==========

  - IntegrityWorker(publish_verified, publish_revoked, expected_digest)
  - submit(token_id, payload_bytes)         # non-blocking; thread-safe
  - run_forever()                            # asyncio coroutine
  - shutdown()                               # graceful drain

Embedded into the FastAPI lifespan: one worker per chat session
(or one per slot, depending on the chat router's slot-binding policy).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event payloads — match the Tauri event channel shapes (see desktop/)
# ---------------------------------------------------------------------------


@dataclass
class VerifiedEvent:
    token_id: int
    actual_digest_hex: str


@dataclass
class RevokedEvent:
    token_id: int
    actual_digest_hex: str
    expected_digest_hex: str
    reason: str  # "hash_mismatch" | "queue_overflow" | "shutdown"


# Type aliases for the publish callbacks. The worker doesn't care HOW
# they fan out — Tauri channel, websocket, plain async-iterator —
# only that they are async and accept the dataclass payloads above.
PublishVerified = Callable[[VerifiedEvent], Awaitable[None]]
PublishRevoked = Callable[[RevokedEvent], Awaitable[None]]


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class IntegrityWorker:
    """One worker per chat session. Single-consumer, multi-producer queue.

    Producers (the chat router that emits tokens) call ``submit()``
    inline on the optimistic-render path. submit() is non-blocking —
    it appends to a bounded asyncio.Queue and returns. The worker
    coroutine drains the queue, hashes, and fires the publish callback.

    Bounded queue: if the producer outruns the worker, the oldest
    entries are dropped (NOT silently — each drop fires a `revoked`
    event with reason=queue_overflow so the user sees the redaction).
    Drop policy = "fail closed" — under verification overload, prefer
    to redact unverifiable tokens rather than wave them through.
    """

    def __init__(
        self,
        *,
        publish_verified: PublishVerified,
        publish_revoked: PublishRevoked,
        expected_digest_provider: Callable[[int], Optional[bytes]],
        queue_size: int = 1024,
    ) -> None:
        self._publish_verified = publish_verified
        self._publish_revoked = publish_revoked
        self._expected_for = expected_digest_provider
        self._queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(
            maxsize=queue_size
        )
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Producer-side: hot-path submit (non-blocking)
    # ------------------------------------------------------------------

    def submit(self, token_id: int, payload: bytes) -> None:
        """Enqueue a token for background verification.

        Non-blocking. If the queue is full, the OLDEST entry is dropped
        and a queue_overflow revocation is published asynchronously so
        the frontend redacts the corresponding token. We intentionally
        drop oldest-not-newest because the user is more likely to be
        looking at the head of the stream right now.
        """
        try:
            self._queue.put_nowait((token_id, payload))
        except asyncio.QueueFull:
            try:
                old_id, _ = self._queue.get_nowait()
                self._queue.put_nowait((token_id, payload))
                # Schedule a revocation for the dropped token; do NOT
                # await here — submit() is supposed to be hot-path safe.
                asyncio.create_task(
                    self._publish_revoked(
                        RevokedEvent(
                            token_id=old_id,
                            actual_digest_hex="",
                            expected_digest_hex="",
                            reason="queue_overflow",
                        )
                    )
                )
            except asyncio.QueueEmpty:
                # Race against the consumer; fall through and try again.
                try:
                    self._queue.put_nowait((token_id, payload))
                except asyncio.QueueFull:
                    logger.warning(
                        "IntegrityWorker: dropping token %d on tight overflow.",
                        token_id,
                    )

    # ------------------------------------------------------------------
    # Consumer-side: background coroutine
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Main drain loop. Run as ``asyncio.create_task(worker.run_forever())``."""
        while not self._stop.is_set():
            try:
                token_id, payload = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            actual = hashlib.sha384(payload).digest()
            actual_hex = actual.hex()
            expected = self._expected_for(token_id)

            if expected is None:
                # No expected digest registered (e.g. the producer didn't
                # bind one). Treat as verified — we have nothing to
                # compare against. Useful for development/dev-mode
                # streams; production binds via the existing
                # vos3_tee_slot_activate_bound chain.
                await self._publish_verified(
                    VerifiedEvent(token_id=token_id, actual_digest_hex=actual_hex)
                )
                continue

            if actual == expected:
                await self._publish_verified(
                    VerifiedEvent(token_id=token_id, actual_digest_hex=actual_hex)
                )
            else:
                await self._publish_revoked(
                    RevokedEvent(
                        token_id=token_id,
                        actual_digest_hex=actual_hex,
                        expected_digest_hex=expected.hex(),
                        reason="hash_mismatch",
                    )
                )

    async def shutdown(self) -> None:
        """Stop the run_forever loop and revoke any unverified tokens."""
        self._stop.set()
        # Drain whatever's left as revocations so the frontend doesn't
        # leave optimistic tokens hanging in pending state.
        while True:
            try:
                token_id, _payload = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._publish_revoked(
                RevokedEvent(
                    token_id=token_id,
                    actual_digest_hex="",
                    expected_digest_hex="",
                    reason="shutdown",
                )
            )
