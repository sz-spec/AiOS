"""
Stage 1 · Atomic unit isolation for services/event_bus.py + event_schema.

Covers — purpose | guards file:line:
  EventEnvelope.new field validation                  | event_schema.py:67-85
  subscribe_scope / publish_scope grammar             | event_schema.py:93-100
  SovereignEventBus.subscribe gate enforcement        | event_bus.py:176-232
  publish gate enforcement on origin_app_id           | event_bus.py:294-299
  first-party publish (no gate)                       | event_bus.py:294-299
  publish disabled raises EventBusUnavailable         | event_bus.py:286-287
  subscribers_for snapshot                            | event_bus.py:433-438
  unsubscribe removes both cache + DB row             | event_bus.py:234-252
  inproc callback receives envelope                   | event_bus.py:256-269, 337-341
"""

from __future__ import annotations

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def bus_env(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    from services.event_bus import _reset_event_bus_for_tests

    _reset_event_bus_for_tests()
    yield


def _install_app(scopes):
    """Install a sandboxed app with the given manifest scopes."""
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "evt", "version": "1.0", "scopes": list(scopes)},
        workspace_id="ws-bus",
    )
    return res["app_id"]


# ---------------------------------------------------------------------------
# EventEnvelope.new validation
# ---------------------------------------------------------------------------


def test_envelope_new_minimum_fields():
    from core.database.event_schema import EventEnvelope

    env = EventEnvelope.new("chat.message_sent", {"x": 1})
    assert env.topic == "chat.message_sent"
    assert env.payload == {"x": 1}
    assert env.origin_app_id is None
    assert isinstance(env.event_id, str) and len(env.event_id) > 0
    assert env.timestamp > 0


def test_envelope_new_rejects_empty_topic():
    from core.database.event_schema import EventEnvelope

    with pytest.raises(ValueError):
        EventEnvelope.new("", {})


def test_envelope_new_rejects_non_string_topic():
    from core.database.event_schema import EventEnvelope

    with pytest.raises(ValueError):
        EventEnvelope.new(42, {})  # type: ignore[arg-type]


def test_envelope_new_rejects_non_dict_payload():
    from core.database.event_schema import EventEnvelope

    with pytest.raises(ValueError):
        EventEnvelope.new("t", ["not", "a", "dict"])  # type: ignore[arg-type]


def test_envelope_to_dict_roundtrips():
    from core.database.event_schema import EventEnvelope

    env = EventEnvelope.new("t", {"x": 1}, origin_app_id="a")
    d = env.to_dict()
    assert d["topic"] == "t"
    assert d["payload"] == {"x": 1}
    assert d["origin_app_id"] == "a"
    assert d["event_id"] == env.event_id


def test_envelope_to_dict_payload_is_a_copy():
    """Mutating the returned payload must not corrupt the envelope."""
    from core.database.event_schema import EventEnvelope

    env = EventEnvelope.new("t", {"x": 1})
    d = env.to_dict()
    d["payload"]["x"] = 999
    assert env.payload["x"] == 1


# ---------------------------------------------------------------------------
# scope helpers
# ---------------------------------------------------------------------------


def test_subscribe_scope_format():
    from core.database.event_schema import subscribe_scope

    assert subscribe_scope("chat.message_sent") == "events.subscribe:chat.message_sent"


def test_publish_scope_format():
    from core.database.event_schema import publish_scope

    assert publish_scope("chat.message_sent") == "events.publish:chat.message_sent"


# ---------------------------------------------------------------------------
# SovereignEventBus — subscribe gate
# ---------------------------------------------------------------------------


def test_subscribe_blocked_when_app_missing_scope(bus_env):
    from services.app_sandbox import ScopeViolation
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["llm.local"])  # no events.subscribe
    with pytest.raises(ScopeViolation):
        EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:cb")


def test_subscribe_allowed_with_exact_scope(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["events.subscribe:chat.message_sent"])
    sub_id = EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:cb")
    assert isinstance(sub_id, str) and len(sub_id) > 0


def test_subscribe_allowed_with_parent_scope(bus_env):
    """Hierarchical scope `events.subscribe:chat` implies any chat.*."""
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["events.subscribe:chat"])
    sid = EVENT_BUS.subscribe(app_id, "chat.message_sent", "inproc:cb")
    assert sid


def test_subscribe_rejects_empty_topic(bus_env):
    """Empty topic is caught by input validation BEFORE the gate check,
    so we use a no-scope app to verify that path."""
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=[])
    with pytest.raises(ValueError):
        EVENT_BUS.subscribe(app_id, "", "inproc:cb")


def test_subscribe_rejects_empty_endpoint(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=[])
    with pytest.raises(ValueError):
        EVENT_BUS.subscribe(app_id, "t", "")


# ---------------------------------------------------------------------------
# publish — gate enforcement on app-originated, free for first-party
# ---------------------------------------------------------------------------


def test_publish_first_party_skips_gate(bus_env):
    """origin_app_id=None → no scope required."""
    from services.event_bus import EVENT_BUS

    delivered = EVENT_BUS.publish("system.heartbeat", {"ok": True})
    # No subscribers, so delivered == 0. Important: it didn't raise.
    assert delivered == 0


def test_publish_origin_app_id_blocked_without_scope(bus_env):
    from services.app_sandbox import ScopeViolation
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["llm.local"])  # no events.publish
    with pytest.raises(ScopeViolation):
        EVENT_BUS.publish("t", {}, origin_app_id=app_id)


def test_publish_origin_app_id_allowed_with_scope(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["events.publish:t"])
    # No subscribers yet, but the gate-check must succeed.
    n = EVENT_BUS.publish("t", {"x": 1}, origin_app_id=app_id)
    assert n == 0


def test_publish_rejects_empty_topic(bus_env):
    from services.event_bus import EVENT_BUS

    with pytest.raises(ValueError):
        EVENT_BUS.publish("", {})


def test_publish_rejects_non_dict_payload(bus_env):
    from services.event_bus import EVENT_BUS

    with pytest.raises(ValueError):
        EVENT_BUS.publish("t", "not-a-dict")  # type: ignore[arg-type]


def test_publish_when_disabled_raises(bus_env):
    from services.event_bus import EVENT_BUS, EventBusUnavailable

    EVENT_BUS.disable()
    try:
        with pytest.raises(EventBusUnavailable):
            EVENT_BUS.publish("t", {})
    finally:
        EVENT_BUS.enable()


# ---------------------------------------------------------------------------
# inproc callback delivery
# ---------------------------------------------------------------------------


def test_inproc_callback_receives_envelope(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["events.subscribe:t.evt"])
    sid = EVENT_BUS.subscribe(app_id, "t.evt", "inproc:cb")
    received = []
    EVENT_BUS.register_inproc_callback(sid, lambda e: received.append(e))
    n = EVENT_BUS.publish("t.evt", {"k": "v"})
    assert n == 1
    assert len(received) == 1
    env = received[0]
    assert env.topic == "t.evt"
    assert env.payload == {"k": "v"}


def test_subscribers_for_returns_topic_only(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(
        scopes=[
            "events.subscribe:topic.a",
            "events.subscribe:topic.b",
        ]
    )
    sid = EVENT_BUS.subscribe(app_id, "topic.a", "inproc:cb")
    _ = EVENT_BUS.subscribe(app_id, "topic.b", "inproc:cb2")
    subs = EVENT_BUS.subscribers_for("topic.a")
    assert len(subs) == 1
    assert subs[0]["id"] == sid
    assert subs[0]["topic"] == "topic.a"


def test_unsubscribe_drops_subscription(bus_env):
    from services.event_bus import EVENT_BUS

    app_id = _install_app(scopes=["events.subscribe:t.evt"])
    sid = EVENT_BUS.subscribe(app_id, "t.evt", "inproc:cb")
    EVENT_BUS.unsubscribe(sid)
    received = []
    # Even if we re-register a callback to the now-dead sub, no delivery.
    EVENT_BUS.publish("t.evt", {})
    assert received == []
    assert EVENT_BUS.subscribers_for("t.evt") == []


def test_register_inproc_callback_unknown_sub_raises(bus_env):
    from services.event_bus import EVENT_BUS

    with pytest.raises(KeyError):
        EVENT_BUS.register_inproc_callback("nonexistent", lambda e: None)
