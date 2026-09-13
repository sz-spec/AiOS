"""
P4.5 — Sovereign Event Bus tests.

Coverage groups:

  1. EventEnvelope shape  — frozen dataclass produces the wire form
     other layers rely on (event_id, topic, payload, origin_app_id,
     timestamp).

  2. Subscribe gate — `events.subscribe:<topic>` is required even
     for an in-process callback. Wildcard / ancestor grant works
     via the existing PermissionGate prefix logic.

  3. Publish gate — app-originated publishes require
     `events.publish:<topic>`. First-party emitters (origin_app_id
     None) skip the gate.

  4. Dispatch — registered in-process subscribers receive the
     envelope. The chat repo's commit path drives a real
     end-to-end `chat.message_sent` round-trip.

  5. Persistence — subscriptions survive via the
     `EventSubscription` table; events land in `EventLog` BEFORE
     dispatch (audit trail intact even if a subscriber crashes).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.database.event_schema import (
    EventEnvelope,
    publish_scope,
    subscribe_scope,
)
from core.database.sqlite_setup import (
    EventLog,
    EventSubscription,
    _reset_for_tests,
    get_session,
    init_db,
)
from core.repositories.sqlite import SQLiteChatSessionRepository
from services.app_sandbox import (
    SANDBOX_MANAGER,
    AppIsolated,
    ScopeViolation,
    _reset_gate_for_tests,
)
from services.event_bus import (
    EVENT_BUS,
    EventBusUnavailable,
    SovereignEventBus,
    _reset_event_bus_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    _reset_event_bus_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()
    _reset_event_bus_for_tests()


def _install(scopes, restrictions=()) -> str:
    res = SANDBOX_MANAGER.install(
        {
            "name": "bus-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"]


# ---------------------------------------------------------------------------
# (1) EventEnvelope shape
# ---------------------------------------------------------------------------


def test_envelope_new_assigns_uuid_and_timestamp():
    e = EventEnvelope.new("topic.x", {"k": "v"})
    assert e.topic == "topic.x"
    assert e.payload == {"k": "v"}
    assert e.origin_app_id is None
    assert e.event_id and len(e.event_id) == 36  # uuid4 canonical len
    assert e.timestamp > 0


def test_envelope_rejects_bad_topic_or_payload():
    with pytest.raises(ValueError, match="topic"):
        EventEnvelope.new("", {})
    with pytest.raises(ValueError, match="payload"):
        EventEnvelope.new("ok", "not-a-dict")  # type: ignore[arg-type]


def test_envelope_to_dict_is_serializable():
    e = EventEnvelope.new("t", {"nested": [1, 2, 3]})
    d = e.to_dict()
    # Round-trip through JSON to prove the dict carries no
    # un-serializable types.
    assert json.loads(json.dumps(d)) == d


def test_scope_helpers():
    assert subscribe_scope("chat.message_sent") == "events.subscribe:chat.message_sent"
    assert publish_scope("chat.message_sent") == "events.publish:chat.message_sent"


# ---------------------------------------------------------------------------
# (2) Subscribe gate
# ---------------------------------------------------------------------------


def test_subscribe_requires_scope(local_db):
    app_id = _install([])  # no events.* scope
    with pytest.raises(ScopeViolation) as exc:
        EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:fn-1")
    assert exc.value.scope == "events.subscribe:chat.message_sent"


def test_subscribe_succeeds_with_exact_scope(local_db):
    app_id = _install(["events.subscribe:chat.message_sent"])
    sub_id = EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:fn-1")
    assert sub_id
    subs = EVENT_BUS.subscribers_for("chat.message_sent")
    assert any(s["id"] == sub_id for s in subs)


def test_subscribe_succeeds_with_ancestor_scope(local_db):
    """`events.subscribe:chat` covers `chat.message_sent` via the
    existing PermissionGate prefix-implication rule."""
    app_id = _install(["events.subscribe:chat"])
    sub_id = EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:fn-2")
    assert sub_id


def test_subscribe_refused_when_app_isolated(local_db):
    app_id = _install(["events.subscribe:chat.message_sent"])
    SANDBOX_MANAGER.isolate(app_id, reason="test")
    with pytest.raises(AppIsolated):
        EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:fn-3")


def test_subscribe_rejects_malformed_topic(local_db):
    app_id = _install(["events.subscribe:foo"])
    with pytest.raises(ValueError, match="topic"):
        EVENT_BUS.subscribe(app_id, "", "inproc:fn")
    with pytest.raises(ValueError, match="callback_endpoint"):
        EVENT_BUS.subscribe(app_id, "foo", "")


def test_unsubscribe_clears_caches_and_row(local_db):
    app_id = _install(["events.subscribe:t"])
    sub_id = EVENT_BUS.subscribe(app_id, "t", "inproc:fn")
    EVENT_BUS.unsubscribe(sub_id)
    assert EVENT_BUS.subscribers_for("t") == []
    with get_session() as session:
        rows = session.query(EventSubscription).filter_by(id=sub_id).all()
        assert rows == []


# ---------------------------------------------------------------------------
# (3) Publish gate
# ---------------------------------------------------------------------------


def test_publish_first_party_skips_gate(local_db):
    """origin_app_id=None → no scope required (vOS core)."""
    count = EVENT_BUS.publish("orphan.topic", {"k": "v"})
    assert count == 0  # no subscribers, but no exception


def test_publish_app_requires_scope(local_db):
    app_id = _install([])  # no publish scope
    with pytest.raises(ScopeViolation) as exc:
        EVENT_BUS.publish("t.x", {"k": "v"}, origin_app_id=app_id)
    assert exc.value.scope == "events.publish:t.x"


def test_publish_app_with_scope_succeeds(local_db):
    app_id = _install(["events.publish:t.x"])
    count = EVENT_BUS.publish("t.x", {"hello": "world"}, origin_app_id=app_id)
    assert count == 0  # no subscribers yet
    # The event must still land in the audit log even with zero subs.
    with get_session() as session:
        rows = session.query(EventLog).filter_by(topic="t.x").all()
        assert len(rows) == 1
        assert json.loads(rows[0].payload_json) == {"hello": "world"}
        assert rows[0].originAppId == app_id


def test_publish_disabled_bus_raises():
    bus = SovereignEventBus()
    bus.disable()
    with pytest.raises(EventBusUnavailable):
        bus.publish("t", {"k": "v"})


# ---------------------------------------------------------------------------
# (4) Dispatch — in-process callback delivery
# ---------------------------------------------------------------------------


def test_dispatch_delivers_to_inproc_callback(local_db):
    app_id = _install(["events.subscribe:bell"])
    sub_id = EVENT_BUS.subscribe(app_id, "bell", "inproc:test-collector")

    received: list = []
    EVENT_BUS.register_inproc_callback(sub_id, lambda env: received.append(env))

    count = EVENT_BUS.publish("bell", {"ring": True})
    assert count == 1
    assert len(received) == 1
    env = received[0]
    assert env.topic == "bell"
    assert env.payload == {"ring": True}
    assert env.origin_app_id is None
    assert env.event_id  # populated by EventEnvelope.new()


def test_dispatch_fans_out_to_multiple_subscribers(local_db):
    a = _install(["events.subscribe:multi"])
    b = _install(["events.subscribe:multi"])
    sub_a = EVENT_BUS.subscribe(a, "multi", "inproc:a")
    sub_b = EVENT_BUS.subscribe(b, "multi", "inproc:b")

    a_seen, b_seen = [], []
    EVENT_BUS.register_inproc_callback(sub_a, a_seen.append)
    EVENT_BUS.register_inproc_callback(sub_b, b_seen.append)

    count = EVENT_BUS.publish("multi", {"n": 1})
    assert count == 2
    assert len(a_seen) == 1 and len(b_seen) == 1


def test_dispatch_isolates_subscriber_errors(local_db):
    """A throwing subscriber must not block downstream delivery."""
    a = _install(["events.subscribe:isolated"])
    b = _install(["events.subscribe:isolated"])
    sub_a = EVENT_BUS.subscribe(a, "isolated", "inproc:bad")
    sub_b = EVENT_BUS.subscribe(b, "isolated", "inproc:good")

    def boom(env):
        raise RuntimeError("subscriber on fire")

    delivered_good = []
    EVENT_BUS.register_inproc_callback(sub_a, boom)
    EVENT_BUS.register_inproc_callback(sub_b, delivered_good.append)

    # The throwing subscriber is NOT counted as delivered; the
    # well-behaved one still gets the event.
    count = EVENT_BUS.publish("isolated", {"ok": True})
    assert count == 1
    assert len(delivered_good) == 1


# ---------------------------------------------------------------------------
# (5) E2E chat.message_sent round-trip
# ---------------------------------------------------------------------------


def test_chat_repo_publishes_message_sent_event(local_db):
    """The directive's golden-path scenario. An app holding the
    subscribe scope receives `chat.message_sent` when the SQLite
    chat repo appends a new message."""
    subscriber_id = _install(["events.subscribe:chat.message_sent"])
    sub_id = EVENT_BUS.subscribe(
        subscriber_id,
        "chat.message_sent",
        "inproc:chat-listener",
    )

    captured: list = []
    EVENT_BUS.register_inproc_callback(sub_id, captured.append)

    repo = SQLiteChatSessionRepository()
    msg_id = asyncio.run(
        repo.append_message(
            session_id="sess-bus-e2e",
            user_id="user-1",
            role="user",
            content="ping from e2e",
        )
    )

    assert len(captured) == 1, "subscribed app didn't receive the event"
    env = captured[0]
    assert env.topic == "chat.message_sent"
    assert env.payload["sessionId"] == "sess-bus-e2e"
    assert env.payload["messageId"] == msg_id
    assert env.payload["userId"] == "user-1"
    assert env.payload["role"] == "user"
    # Audit row landed too.
    with get_session() as session:
        rows = session.query(EventLog).filter_by(topic="chat.message_sent").all()
        assert len(rows) == 1
        # The event is first-party (origin_app_id is None) — the
        # SQLite repo doesn't claim to be a sandboxed app.
        assert rows[0].originAppId is None


def test_unsubscribed_app_does_not_receive_event(local_db):
    """A second app without the scope must NOT see the event, even
    when another app's subscription is active for the same topic."""
    subscribed = _install(["events.subscribe:chat.message_sent"])
    unsubscribed = _install(["filesystem.read"])  # unrelated scope

    sub_id = EVENT_BUS.subscribe(
        subscribed,
        "chat.message_sent",
        "inproc:listener",
    )
    captured: list = []
    EVENT_BUS.register_inproc_callback(sub_id, captured.append)

    # Confirm the unsubscribed app can't subscribe either — that's
    # the directive's "denied 403" surface.
    with pytest.raises(ScopeViolation) as exc:
        EVENT_BUS.subscribe(
            unsubscribed,
            "chat.message_sent",
            "inproc:sneaky",
        )
    assert exc.value.app_id == unsubscribed
    assert exc.value.scope == "events.subscribe:chat.message_sent"

    # The legitimate subscriber still works.
    repo = SQLiteChatSessionRepository()
    asyncio.run(
        repo.append_message(
            session_id="sess-isolation",
            user_id="user-2",
            role="user",
            content="who hears me?",
        )
    )
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# (6) Hydration — subscriptions survive a bus restart
# ---------------------------------------------------------------------------


def test_hydrate_from_db_rebuilds_in_memory_cache(local_db):
    app_id = _install(["events.subscribe:persist.me"])
    sub_id = EVENT_BUS.subscribe(app_id, "persist.me", "inproc:k")

    # Simulate a "restart" — drop the in-memory caches.
    bus = EVENT_BUS
    bus._subs.clear()
    bus._by_topic.clear()
    assert bus.subscribers_for("persist.me") == []

    bus.hydrate_from_db()
    subs = bus.subscribers_for("persist.me")
    assert any(s["id"] == sub_id for s in subs)
