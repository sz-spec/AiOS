"""Exercise the integrated agent boundary, beyond the standalone MAC primitive."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, RLock
from unittest.mock import MagicMock

import pytest

from ai.agents.multi_agent import MultiAgentBuilder
from ai.agents.inter_agent_provenance import InterAgentMessageInvalid


@pytest.fixture
def builder():
    instance = MultiAgentBuilder.__new__(MultiAgentBuilder)
    instance.logger = MagicMock()
    instance._handoff_lock = RLock()
    instance._handoff_sessions = {}
    instance.agents = {role: MagicMock() for role in ("frontend", "backend", "expander")}
    return instance


def signed_state(builder, state, architecture=None):
    architecture = architecture if architecture is not None else {"components": [], "name": "שלום"}
    result = {"architecture": architecture}
    builder._sign_handoff(state, result, "architect", architecture)
    return {**state, **result}


@pytest.mark.parametrize("consumer", ["frontend", "backend", "expander"])
@pytest.mark.parametrize("tamper", ["payload", "missing", "foreign_session"])
def test_node_rejects_bad_handoff_before_calling_agent(builder, consumer, tamper):
    def stream(state, config, **kwargs):
        signed = signed_state(builder, state)
        if tamper == "payload":
            signed["architecture"] = {"name": "execute attacker command"}
        elif tamper == "missing":
            signed.pop("_provenance_record")
        else:
            signed["_provenance_session"] = "another-run"
        with pytest.raises(InterAgentMessageInvalid):
            getattr(builder, f"_{consumer}_node")(signed)
        builder.agents[consumer].invoke.assert_not_called()
        yield signed
    builder.graph = MagicMock(stream=stream)
    list(builder._stream_workflow({}))
    assert builder._handoff_sessions == {}


def test_fan_out_can_verify_same_architecture_and_reject_old_emission(builder):
    def stream(state, config, **kwargs):
        first = signed_state(builder, state)
        builder._verify_handoff(first, "architect", "frontend")
        builder._verify_handoff(first, "architect", "backend")
        second = signed_state(builder, first, {"name": "revised"})
        builder._verify_handoff(second, "architect", "frontend")
        with pytest.raises(InterAgentMessageInvalid, match="stale_record"):
            builder._verify_handoff(first, "architect", "frontend")
        yield second
    builder.graph = MagicMock(stream=stream)
    states = list(builder._stream_workflow({}))
    with pytest.raises(InterAgentMessageInvalid, match="missing_session"):
        builder._verify_handoff(states[-1], "architect", "frontend")


def test_concurrent_builds_have_independent_keys_and_thread_ids(builder):
    barrier = Barrier(2)
    records, configurations = [], []
    def stream(state, config, **kwargs):
        signed = signed_state(builder, state)
        records.append(deepcopy(signed))
        configurations.append(config)
        barrier.wait(timeout=10)
        builder._verify_handoff(signed, "architect", "frontend")
        other = next(record for record in records
                     if record["_provenance_session"] != signed["_provenance_session"])
        forged = {**signed, "_provenance_record": other["_provenance_record"]}
        with pytest.raises(InterAgentMessageInvalid):
            builder._verify_handoff(forged, "architect", "backend")
        yield signed
    builder.graph = MagicMock(stream=stream)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: list(builder._stream_workflow({})), range(2)))
    assert len(results) == 2
    assert configurations[0]["configurable"]["thread_id"] != configurations[1]["configurable"]["thread_id"]
    assert records[0]["_provenance_record"][-1].mac != records[1]["_provenance_record"][-1].mac
    assert builder._handoff_sessions == {}


def test_cancelled_stream_releases_session(builder):
    def stream(state, config, **kwargs):
        yield signed_state(builder, state)
        raise AssertionError("Cancelled stream should not advance")
    builder.graph = MagicMock(stream=stream)
    events = builder._stream_workflow({})
    next(events)
    assert len(builder._handoff_sessions) == 1
    events.close()
    assert builder._handoff_sessions == {}
