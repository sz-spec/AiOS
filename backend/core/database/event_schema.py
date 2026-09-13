"""
backend/core/database/event_schema.py — Sovereign Event Bus envelope.

P4.5 — every event flowing through `SovereignEventBus` is wrapped
in an `EventEnvelope` so subscribers (in-process callables, HTTP
webhooks, sandboxed subprocess entrypoints) all see the SAME wire
shape regardless of where they came from.

Shape:

    {
      "event_id":       "<uuid4>",
      "topic":          "chat.message_sent",
      "payload":        { ...arbitrary JSON object... },
      "origin_app_id":  "<uuid> | null",     # null = first-party emitter
      "timestamp":      <ms since epoch>
    }

The envelope is intentionally tiny — no auth tokens, no per-
subscriber metadata, no routing hints. Subscribers MUST treat the
payload as untrusted (it might come from another app); first-
party emitters MUST keep secrets out of `payload`.

The scope grammar uses a literal colon between the action verb
and the topic so the existing `PermissionGate._scope_implies`
prefix logic Just Works:

    events.subscribe:chat.message_sent       — exact-match grant
    events.subscribe:chat                    — implies all chat.*
    events.subscribe                         — implies every topic
                                               (rare; full bus access)

Same shape for `events.publish:<topic>`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class EventEnvelope:
    """Read-only event view passed to subscribers."""

    event_id: str
    topic: str
    payload: dict
    origin_app_id: Optional[str] = None
    timestamp: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "payload": dict(self.payload),
            "origin_app_id": self.origin_app_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def new(
        cls,
        topic: str,
        payload: dict,
        *,
        origin_app_id: Optional[str] = None,
    ) -> "EventEnvelope":
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        return cls(
            event_id=str(uuid.uuid4()),
            topic=topic.strip(),
            payload=payload,
            origin_app_id=origin_app_id,
            timestamp=_now_ms(),
        )


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


def subscribe_scope(topic: str) -> str:
    """Return the manifest scope string an app needs to subscribe."""
    return f"events.subscribe:{topic}"


def publish_scope(topic: str) -> str:
    """Return the manifest scope string an app needs to publish."""
    return f"events.publish:{topic}"


__all__ = [
    "EventEnvelope",
    "subscribe_scope",
    "publish_scope",
]
