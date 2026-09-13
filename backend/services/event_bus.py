"""
backend/services/event_bus.py — Sovereign Event Bus (P4.5).

Architecture
------------

    ┌─────────────────────────────────────────────────────────────────┐
    │  Emitter (first-party repo OR sandboxed app)                    │
    │   - `EVENT_BUS.publish(topic, payload, origin_app_id=...)`      │
    └────────────────┬────────────────────────────────────────────────┘
                     │
                     ▼  (gate-check `events.publish:<topic>` when origin_app_id set)
    ┌─────────────────────────────────────────────────────────────────┐
    │  SovereignEventBus                                              │
    │   - persists EventLog row                                       │
    │   - looks up `_by_topic[topic]` → sub_ids                       │
    │   - dispatches per-subscription based on callback_endpoint:     │
    │       inproc:<key>  → call the registered Python callable      │
    │       http://...    → fire-and-forget POST of the envelope     │
    │       exec:<path>   → AppProcessRunner.run_entrypoint with     │
    │                       VOS3_EVENT_JSON on stdin                  │
    └─────────────────────────────────────────────────────────────────┘
                     │
                     ▼
    ┌──────────────────┐  ┌────────────────┐  ┌──────────────────────┐
    │ in-proc callable │  │ HTTP webhook   │  │ cold app subprocess  │
    │ (vOS core hooks  │  │ (warm sidecar  │  │ (entrypoint sees     │
    │  + tests)        │  │  receiver)     │  │  envelope on stdin)  │
    └──────────────────┘  └────────────────┘  └──────────────────────┘

Permission semantics
--------------------
* `subscribe(app_id, topic, callback)` — REQUIRES the app to hold
  `events.subscribe:<topic>` in its manifest.
* `publish(topic, payload, origin_app_id=app_id)` — REQUIRES
  `events.publish:<topic>` when origin_app_id is set.
* `publish(topic, payload)` with `origin_app_id=None` is first-
  party (vOS core hooks, repos). The bus does NOT gate first-
  party publishes — that's how core code emits "chat.message_sent"
  without holding a scope it doesn't conceptually need.

Thread safety
-------------
A single `RLock` guards the in-memory subscription cache and the
inproc-callback registry. SQLite writes happen inside the lock so
the durable journal stays consistent with the cache. Dispatch
fanout uses a snapshot of subscriber ids taken under the lock,
then runs callbacks WITHOUT the lock held — a slow webhook can't
stall an unrelated publish.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Callable, Optional

from sqlalchemy import select

from core.database.event_schema import (
    EventEnvelope,
    publish_scope,
    subscribe_scope,
)
from core.database.sqlite_setup import (
    EventLog,
    EventSubscription,
    get_session,
    init_db,
)
from services.app_sandbox import PERMISSION_GATE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions / value types
# ---------------------------------------------------------------------------


class EventBusUnavailable(RuntimeError):
    """Raised when the bus has been explicitly disabled (e.g. test
    teardown) but a caller still tried to publish."""


class Subscription:
    """In-memory view of an EventSubscription row."""

    __slots__ = ("id", "app_id", "topic", "callback_endpoint")

    def __init__(self, *, id: str, app_id: str, topic: str, callback_endpoint: str):
        self.id = id
        self.app_id = app_id
        self.topic = topic
        self.callback_endpoint = callback_endpoint

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "app_id": self.app_id,
            "topic": self.topic,
            "callback_endpoint": self.callback_endpoint,
        }


# ---------------------------------------------------------------------------
# SovereignEventBus
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


class SovereignEventBus:
    """Thread-safe singleton implementing pub/sub for vOS apps + core."""

    def __init__(self):
        # topic -> set(sub_id)
        self._by_topic: dict = {}
        # sub_id -> Subscription
        self._subs: dict = {}
        # sub_id -> callable (envelope) for inproc dispatch
        self._inproc_callbacks: dict = {}
        # Re-entrant: subscribe() and publish() may call into helpers
        # that acquire the same lock.
        self._lock = threading.RLock()
        # Set False during shutdown to short-circuit publishes.
        self._enabled = True

    # --- lifecycle -----------------------------------------------------

    def hydrate_from_db(self) -> None:
        """Rebuild the in-memory caches from the EventSubscription
        table. Called on backend startup; idempotent."""
        init_db()
        with self._lock:
            self._by_topic.clear()
            self._subs.clear()
            with get_session() as session:
                for row in session.execute(select(EventSubscription)).scalars():
                    sub = Subscription(
                        id=row.id,
                        app_id=row.appId,
                        topic=row.topic,
                        callback_endpoint=row.callbackEndpoint,
                    )
                    self._subs[sub.id] = sub
                    self._by_topic.setdefault(sub.topic, set()).add(sub.id)
        logger.info(
            "[bus] hydrated %d subscriptions across %d topics",
            len(self._subs),
            len(self._by_topic),
        )

    def disable(self) -> None:
        """Test-only — block further publishes. Outstanding async
        dispatches finish on their own."""
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._by_topic.clear()
            self._subs.clear()
            self._inproc_callbacks.clear()
            self._enabled = True

    # --- subscribe -----------------------------------------------------

    def subscribe(
        self,
        app_id: str,
        topic: str,
        callback_endpoint: str,
        *,
        sub_id: Optional[str] = None,
    ) -> str:
        """Register an app to receive events on `topic`.

        Raises:
          ScopeViolation / AppIsolated / AppNotFound — gate denied.
          ValueError — malformed input.

        Returns the new subscription id (UUID4 unless `sub_id` is
        supplied for deterministic re-binding in tests).
        """
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be a non-empty string")
        if not isinstance(callback_endpoint, str) or not callback_endpoint.strip():
            raise ValueError("callback_endpoint must be a non-empty string")

        topic = topic.strip()
        callback_endpoint = callback_endpoint.strip()

        # Gate check — even an `inproc:` callback requires the scope,
        # because the dispatch path doesn't distinguish endpoints
        # from the manifest's point of view.
        PERMISSION_GATE.check(app_id, subscribe_scope(topic))

        new_id = sub_id or str(uuid.uuid4())
        now = _now_ms()
        with self._lock:
            init_db()
            with get_session() as session:
                session.add(
                    EventSubscription(
                        id=new_id,
                        appId=app_id,
                        topic=topic,
                        callbackEndpoint=callback_endpoint,
                        createdAt=now,
                    )
                )
                session.commit()
            sub = Subscription(
                id=new_id,
                app_id=app_id,
                topic=topic,
                callback_endpoint=callback_endpoint,
            )
            self._subs[new_id] = sub
            self._by_topic.setdefault(topic, set()).add(new_id)

        logger.info(
            "[bus] subscribed app=%s topic=%s endpoint=%s sub_id=%s",
            app_id,
            topic,
            callback_endpoint,
            new_id,
        )
        return new_id

    def unsubscribe(self, sub_id: str) -> None:
        with self._lock:
            sub = self._subs.pop(sub_id, None)
            self._inproc_callbacks.pop(sub_id, None)
            if sub is None:
                return
            bucket = self._by_topic.get(sub.topic)
            if bucket:
                bucket.discard(sub_id)
                if not bucket:
                    self._by_topic.pop(sub.topic, None)
            with get_session() as session:
                row = session.execute(
                    select(EventSubscription).where(EventSubscription.id == sub_id)
                ).scalar_one_or_none()
                if row is not None:
                    session.delete(row)
                    session.commit()
        logger.info("[bus] unsubscribed sub_id=%s", sub_id)

    # --- inproc binding ------------------------------------------------

    def register_inproc_callback(
        self,
        sub_id: str,
        fn: Callable[[EventEnvelope], None],
    ) -> None:
        """Bind a Python callable to an existing subscription.

        Used by first-party services (and tests) to receive events
        in-process. The dispatch path prefers this over the
        callback_endpoint string when both are present — keeps
        `inproc:<key>` endpoints from needing a global registry.
        """
        with self._lock:
            if sub_id not in self._subs:
                raise KeyError(f"sub_id={sub_id!r} not registered")
            self._inproc_callbacks[sub_id] = fn

    # --- publish -------------------------------------------------------

    def publish(
        self,
        topic: str,
        payload: dict,
        *,
        origin_app_id: Optional[str] = None,
    ) -> int:
        """Fanout `payload` to every subscriber of `topic`.

        Returns the number of subscribers the bus attempted delivery
        to (NOT the number that succeeded — failed in-proc callbacks
        / HTTP errors are logged but don't fail the publish).
        """
        if not self._enabled:
            raise EventBusUnavailable("event bus disabled")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        topic = topic.strip()

        if origin_app_id:
            # App-originated publishes are gated. First-party calls
            # (origin_app_id=None) skip the gate — that's how vOS
            # core repos emit events without holding a scope they
            # don't conceptually need.
            PERMISSION_GATE.check(origin_app_id, publish_scope(topic))

        envelope = EventEnvelope.new(topic, payload, origin_app_id=origin_app_id)

        # Persist to the EventLog BEFORE dispatch — a crash mid-
        # fanout still leaves an audit trail.
        try:
            init_db()
            with get_session() as session:
                session.add(
                    EventLog(
                        id=envelope.event_id,
                        topic=envelope.topic,
                        payload_json=json.dumps(
                            envelope.payload, separators=(",", ":")
                        ),
                        originAppId=envelope.origin_app_id,
                        timestamp=envelope.timestamp,
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001
            # Persistence failure must NOT block fanout — we still
            # want subscribers to learn about the event.
            logger.warning(
                "[bus] event-log persist failed event_id=%s err=%s",
                envelope.event_id,
                exc,
            )

        # Snapshot subscribers under the lock, dispatch without it.
        with self._lock:
            sub_ids = list(self._by_topic.get(topic, ()))
            cb_snapshot = {sid: self._inproc_callbacks.get(sid) for sid in sub_ids}
            sub_snapshot = {sid: self._subs.get(sid) for sid in sub_ids}

        delivered = 0
        for sid in sub_ids:
            sub = sub_snapshot.get(sid)
            if sub is None:
                continue
            cb = cb_snapshot.get(sid)
            try:
                if cb is not None:
                    # Prefer the in-process callable when bound.
                    cb(envelope)
                elif sub.callback_endpoint.startswith("inproc:"):
                    # Endpoint claims inproc but no callable bound —
                    # log + skip rather than swallow the event.
                    logger.warning(
                        "[bus] sub_id=%s endpoint=inproc but no callable "
                        "registered; event_id=%s dropped",
                        sid,
                        envelope.event_id,
                    )
                    continue
                elif sub.callback_endpoint.startswith(("http://", "https://")):
                    self._dispatch_http(sub, envelope)
                elif sub.callback_endpoint.startswith("exec:"):
                    self._dispatch_exec(sub, envelope)
                else:
                    logger.warning(
                        "[bus] sub_id=%s unknown endpoint scheme=%r; "
                        "event_id=%s dropped",
                        sid,
                        sub.callback_endpoint,
                        envelope.event_id,
                    )
                    continue
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[bus] dispatch failed sub_id=%s topic=%s err=%s",
                    sid,
                    topic,
                    exc,
                )

        logger.info(
            "[bus] published topic=%s event_id=%s subs=%d delivered=%d",
            topic,
            envelope.event_id,
            len(sub_ids),
            delivered,
        )
        return delivered

    # --- dispatch helpers ----------------------------------------------

    def _dispatch_http(self, sub: Subscription, envelope: EventEnvelope) -> None:
        """Fire-and-forget POST of the envelope to the subscriber URL.

        Kept synchronous + bounded-timeout so a slow webhook can't
        wedge the bus. The HTTP call MUST go through the same
        loopback kill-switch the rest of vOS uses; we don't
        re-check egress policy here because the network layer is
        already the choke point.
        """
        try:
            import urllib.request

            req = urllib.request.Request(
                sub.callback_endpoint,
                data=json.dumps(envelope.to_dict()).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-VOS-Event-Id": envelope.event_id,
                    "X-VOS-Event-Topic": envelope.topic,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2.0).close()  # nosec
        except Exception as exc:  # noqa: BLE001
            # Webhook errors are logged at the call site; re-raise
            # so the publish() loop counts this as a failed dispatch.
            raise RuntimeError(f"http dispatch failed: {exc}") from exc

    def _dispatch_exec(self, sub: Subscription, envelope: EventEnvelope) -> None:
        """Spawn the subscriber app's entrypoint with the envelope
        on stdin. The entrypoint reads stdin to learn what fired.

        Imported lazily so the bus module doesn't pull the runner
        and its asyncio machinery into every backend boot.
        """
        from services.app_sandbox import APP_PROCESS_RUNNER

        entrypoint = sub.callback_endpoint[len("exec:") :]
        envelope_json = json.dumps(envelope.to_dict())
        # The runner is async — we schedule it on the running loop
        # if one is alive, else do a blocking run.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                APP_PROCESS_RUNNER.run_entrypoint(
                    sub.app_id,
                    entrypoint=entrypoint,
                    env_override={"VOS3_EVENT_JSON": envelope_json},
                )
            )
        except RuntimeError:
            asyncio.run(
                APP_PROCESS_RUNNER.run_entrypoint(
                    sub.app_id,
                    entrypoint=entrypoint,
                    env_override={"VOS3_EVENT_JSON": envelope_json},
                )
            )

    # --- read-only views ----------------------------------------------

    def subscribers_for(self, topic: str) -> list:
        """Return a list[dict] of current subscriptions for a topic.
        Used by `/api/system/events/status` and tests."""
        with self._lock:
            sub_ids = list(self._by_topic.get(topic, ()))
            return [self._subs[s].to_dict() for s in sub_ids if s in self._subs]


# Module-level singleton — core repos + api routes import this.
EVENT_BUS = SovereignEventBus()


def _reset_event_bus_for_tests() -> None:
    EVENT_BUS._reset_for_tests()


__all__ = [
    "EVENT_BUS",
    "EventBusUnavailable",
    "SovereignEventBus",
    "Subscription",
]
