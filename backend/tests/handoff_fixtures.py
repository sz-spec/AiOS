"""Node fixtures using real per-run handoff creation, signing and verification."""
from unittest.mock import MagicMock, patch
import pytest

_active = {}


@pytest.fixture(autouse=True)
def handoff_sessions():
    yield
    for _, generator in _active.values():
        generator.close()
    _active.clear()


def make_builder():
    from ai.agents.multi_agent import MultiAgentBuilder
    return MultiAgentBuilder(llm=MagicMock())


def invoke_node(builder, role, state):
    key = id(builder)
    if key not in _active:
        # Only graph transport is replaced. The real workflow owns fresh keys,
        # session identity, signature state and final cleanup.
        with patch.object(builder.graph, 'stream', side_effect=lambda initial, *a, **kw: iter([initial])):
            generator = builder._stream_workflow(state)
            live = next(generator)
        _active[key] = (live, generator)
    live, _ = _active[key]
    supplied = {**live, **state}
    if role in ('frontend', 'backend') and not supplied.get('_provenance_record'):
        # A node-only fixture has a trusted upstream architecture. Sign through
        # the production API; full-chain tests retain the architect's record.
        supplied.update(builder._sign_handoff(supplied, {}, 'architect', supplied.get('architecture')))
    result = getattr(builder, '_' + role + '_node')(supplied)
    live.update(supplied)
    live.update(result)
    return result
