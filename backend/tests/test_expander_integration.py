"""
Integration Tests — Expander Pipeline + Redis Resilience + Performance
======================================================================

Verifies:
  1. PIPELINE: Mocked Gemini → Expander → Frontend, blueprints propagated
  2. RESILIENCE: Missing REDIS_URL → MemorySaver fallback (no crash)
  3. RESILIENCE: Malformed blueprint → routes to "finalize" (graceful degradation)
  4. PERFORMANCE: 5-run latency measurement with overhead delta
"""

import json
import os
import time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, AIMessage

# ---------------------------------------------------------------------------
# Mock LLM fixtures
# ---------------------------------------------------------------------------

MOCK_ARCHITECTURE = {
    "tech_stack": {"frontend": "React", "backend": "Express"},
    "components": [
        {"name": "Dashboard", "type": "frontend", "description": "Main dashboard"},
        {"name": "API", "type": "backend", "description": "REST API"},
    ],
    "api_endpoints": [
        {"method": "GET", "path": "/api/tasks", "description": "List tasks"},
    ],
    "data_models": [
        {"name": "Task", "fields": ["id", "title", "completed"]},
    ],
    "file_structure": {"src": {"App.tsx": "", "api": {"routes.ts": ""}}},
}

MOCK_BLUEPRINTS = {
    "project_metadata": {
        "session_id": "test-session-001",
        "generation_mode": "sequential",
        "total_expected_files": 4,
        "needs_backend": True,
    },
    "architecture_blueprint": {
        "summary": "A task management app with React frontend and Express backend.",
        "shared_dependencies": ["axios", "express"],
    },
    "execution_queue": [
        {
            "task_id": "file_001",
            "file_path": "src/models/Task.ts",
            "role": "model",
            "pruned_context": {
                "description": "Task data model with validation",
                "interfaces": [],
                "required_methods": ["def validate(data: dict) -> bool"],
                "logic_steps": ["1. Check required fields", "2. Validate types"],
            },
            "validation_tests": ["test_task_valid", "test_task_missing_title"],
        },
        {
            "task_id": "file_002",
            "file_path": "src/App.tsx",
            "role": "interface",
            "pruned_context": {
                "description": "Main React component with task list",
                "interfaces": ["Task from models/Task"],
                "required_methods": ["function App(): JSX.Element"],
                "logic_steps": ["1. Fetch tasks", "2. Render list", "3. Handle errors"],
            },
            "validation_tests": ["test_app_renders", "test_app_shows_tasks"],
        },
    ],
}

MOCK_MALFORMED_BLUEPRINT = "This is not JSON at all {{ broken ]] garbage"


@dataclass
class MockLLMResponse:
    """Mimics ai.llm.providers.LLMResponse."""

    content: str
    model: str = "mock-model"
    provider: str = "mock"
    tokens_used: int = 100
    latency_ms: float = 50.0


def make_mock_llm():
    """Create a mock LLM that returns canned responses based on prompt content."""
    mock = MagicMock()

    def generate_side_effect(prompt, system=None, **kwargs):
        prompt_lower = prompt.lower() if isinstance(prompt, str) else ""

        # Architect prompt
        if "design a complete system architecture" in prompt_lower:
            return MockLLMResponse(content=json.dumps(MOCK_ARCHITECTURE))

        # Expander prompt
        if "implementation-ready blueprints" in prompt_lower:
            return MockLLMResponse(content=json.dumps(MOCK_BLUEPRINTS))

        # Frontend prompt
        if "create the frontend" in prompt_lower:
            return MockLLMResponse(
                content="File: `src/App.tsx`\n```tsx\nfunction App() { return <div>Hello</div>; }\n```"
            )

        # Backend prompt
        if "create the backend" in prompt_lower:
            return MockLLMResponse(
                content='File: `src/index.ts`\n```ts\nimport express from "express";\n```'
            )

        # Tester prompt
        if "test" in prompt_lower:
            return MockLLMResponse(
                content='File: `tests/app.test.tsx`\n```tsx\ntest("renders", () => {});\n```'
            )

        # Reviewer prompt
        if "review" in prompt_lower:
            return MockLLMResponse(
                content=json.dumps(
                    {
                        "issues": [],
                        "suggestions": [],
                        "score": {
                            "security": 9,
                            "quality": 9,
                            "performance": 8,
                            "maintainability": 9,
                        },
                        "approved": True,
                    }
                )
            )

        # Guardrails / migrations / visual QA — pass through
        return MockLLMResponse(content=json.dumps({"status": "pass", "violations": []}))

    mock.generate = MagicMock(side_effect=generate_side_effect)
    return mock


# ---------------------------------------------------------------------------
# 1. PIPELINE INTEGRATION: architect → expander → frontend
# ---------------------------------------------------------------------------


class TestExpanderPipeline:

    def test_expander_produces_blueprints(self):
        """ExpanderAgent.invoke() returns parsed blueprints with execution_queue."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = make_mock_llm()
        agent = ExpanderAgent(llm=mock_llm)

        state = {
            "requirements": "Build a task management app",
            "architecture": MOCK_ARCHITECTURE,
        }

        result = agent.invoke(state)

        assert "blueprints" in result
        blueprints = result["blueprints"]
        assert isinstance(blueprints, dict)
        assert "execution_queue" in blueprints
        assert len(blueprints["execution_queue"]) == 2
        assert blueprints["project_metadata"]["needs_backend"] is True

    def test_blueprints_passed_to_frontend_node(self):
        """Full pipeline: architect → expander → frontend. Verify blueprints in state."""
        from ai.agents.multi_agent import MultiAgentBuilder

        mock_llm = make_mock_llm()

        # Patch USE_PARALLEL_EXECUTION to False for deterministic sequential test
        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False):
            builder = MultiAgentBuilder(llm=mock_llm)

            # Intercept the frontend node to capture state
            captured_states = []
            original_frontend = builder._frontend_node

            def intercepting_frontend(state, *args, **kwargs):
                captured_states.append(dict(state))
                return original_frontend(state, *args, **kwargs)

            builder.agents["frontend"].invoke = MagicMock(
                side_effect=lambda state: {
                    "messages": [AIMessage(content="Frontend done", name="Frontend")],
                    "frontend_code": {"src/App.tsx": "function App() {}"},
                    "current_phase": "backend",
                }
            )

            # Run the graph up to frontend by inspecting stream events
            initial_state = {
                "messages": [HumanMessage(content="Build a task app")],
                "requirements": "Build a task app",
                "project_description": "Build a task app",
                "architecture": None,
                "frontend_code": None,
                "backend_code": None,
                "tests": None,
                "review_results": None,
                "current_phase": "architect",
                "iteration": 0,
                "errors": [],
                "error": None,
                "final_project": None,
                "blueprints": None,
                "stuck_count": 0,
                "previous_issues_embeddings": None,
                "previous_issues_text": None,
                "model_switch_history": None,
                "_override_model": None,
                "_clear_failed_context": None,
                "_include_anti_patterns": None,
                "_latest_issues_embeddings": None,
                "_latest_issues_text": None,
                "guardrails_violations": None,
                "guardrails_iteration": 0,
                "expert_sos_suggested": None,
                "schema_migration": None,
                "previous_schema_fingerprint": None,
                "visual_qa_report": None,
                "visual_qa_screenshots": None,
            }

            thread_config = {"configurable": {"thread_id": "test_pipeline_001"}}
            phases = []
            final_state = None

            for event in builder._stream_workflow(initial_state, thread_config):
                final_state = event
                phase = event.get("current_phase", "unknown")
                phases.append(phase)

            # Verify expander ran (blueprints should be populated after expander phase)
            assert final_state is not None
            # The pipeline should have gone through architect and expander at minimum
            assert (
                final_state.get("blueprints") is not None
                or final_state.get("final_project") is not None
            )

    def test_expander_handles_json_in_markdown_fence(self):
        """Expander parses JSON wrapped in ```json ... ``` fences."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = MagicMock()
        fenced_json = f"Here are the blueprints:\n```json\n{json.dumps(MOCK_BLUEPRINTS)}\n```\nDone."
        mock_llm.generate = MagicMock(return_value=MockLLMResponse(content=fenced_json))

        agent = ExpanderAgent(llm=mock_llm)
        result = agent.invoke(
            {
                "requirements": "test",
                "architecture": MOCK_ARCHITECTURE,
            }
        )

        blueprints = result["blueprints"]
        assert isinstance(blueprints, dict)
        assert "execution_queue" in blueprints


# ---------------------------------------------------------------------------
# 2. RESILIENCE: Missing Redis → MemorySaver fallback
# ---------------------------------------------------------------------------


class TestRedisFallback:

    def test_no_redis_url_uses_memory_saver(self):
        """When REDIS_URL is unset, MultiAgentBuilder falls back to MemorySaver."""
        from ai.agents.multi_agent import MultiAgentBuilder

        # Ensure REDIS_URL is NOT set
        env = os.environ.copy()
        env.pop("REDIS_URL", None)

        with patch.dict(os.environ, env, clear=True):
            mock_llm = make_mock_llm()
            builder = MultiAgentBuilder(llm=mock_llm, checkpointer=None)

            # The compiled graph should exist (no crash)
            assert builder.graph is not None
            # _checkpointer was None, so _build_graph used MemorySaver fallback
            assert builder._checkpointer is None

    def test_explicit_memory_saver_works(self):
        """Passing MemorySaver explicitly works without Redis."""
        from ai.agents.multi_agent import MultiAgentBuilder
        from langgraph.checkpoint.memory import MemorySaver

        mock_llm = make_mock_llm()
        saver = MemorySaver()
        builder = MultiAgentBuilder(llm=mock_llm, checkpointer=saver)
        assert builder._checkpointer is saver
        assert builder.graph is not None

    def test_pipeline_runs_without_redis(self):
        """Full pipeline completes with MemorySaver (no Redis)."""
        from ai.agents.multi_agent import MultiAgentBuilder

        env = os.environ.copy()
        env.pop("REDIS_URL", None)

        with patch.dict(os.environ, env, clear=True), patch(
            "ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False
        ):

            mock_llm = make_mock_llm()
            builder = MultiAgentBuilder(llm=mock_llm, checkpointer=None)

            initial_state = {
                "messages": [HumanMessage(content="Build a task app")],
                "requirements": "Build a task app",
                "project_description": "Build a task app",
                "architecture": None,
                "frontend_code": None,
                "backend_code": None,
                "tests": None,
                "review_results": None,
                "current_phase": "architect",
                "iteration": 0,
                "errors": [],
                "error": None,
                "final_project": None,
                "blueprints": None,
                "stuck_count": 0,
                "previous_issues_embeddings": None,
                "previous_issues_text": None,
                "model_switch_history": None,
                "_override_model": None,
                "_clear_failed_context": None,
                "_include_anti_patterns": None,
                "_latest_issues_embeddings": None,
                "_latest_issues_text": None,
                "guardrails_violations": None,
                "guardrails_iteration": 0,
                "expert_sos_suggested": None,
                "schema_migration": None,
                "previous_schema_fingerprint": None,
                "visual_qa_report": None,
                "visual_qa_screenshots": None,
            }

            thread_config = {"configurable": {"thread_id": "test_no_redis"}}
            final_state = None
            for event in builder._stream_workflow(initial_state, thread_config):
                final_state = event

            assert final_state is not None
            # Pipeline should reach completion (finalize sets current_phase to "complete")
            assert (
                final_state.get("current_phase") == "complete"
                or final_state.get("final_project") is not None
            )


# ---------------------------------------------------------------------------
# 3. RESILIENCE: Malformed Blueprint → Graceful Degradation
# ---------------------------------------------------------------------------


class TestMalformedBlueprintDegradation:

    def test_malformed_json_sets_parse_error(self):
        """When Expander returns unparseable JSON, blueprints contain parse_error."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(
            return_value=MockLLMResponse(content=MOCK_MALFORMED_BLUEPRINT)
        )

        agent = ExpanderAgent(llm=mock_llm)
        result = agent.invoke(
            {
                "requirements": "test",
                "architecture": MOCK_ARCHITECTURE,
            }
        )

        blueprints = result["blueprints"]
        assert "parse_error" in blueprints or "error" in blueprints

    def test_expander_error_routes_to_finalize_sequential(self):
        """In sequential mode, expander error routes to finalize (not hang)."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False):
            mock_llm = make_mock_llm()
            builder = MultiAgentBuilder(llm=mock_llm)

            # Test the routing function directly with an error blueprint
            state = {"blueprints": {"error": "LLM crashed", "fallback": True}}
            route = builder._route_after_expander(state)
            assert route == "finalize", f"Expected 'finalize', got '{route}'"

    def test_expander_error_routes_to_finalize_parallel(self):
        """In parallel mode, expander error dispatches Send to finalize."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", True), patch(
            "ai.agents.multi_agent._SEND_AVAILABLE", True
        ):

            mock_llm = make_mock_llm()
            builder = MultiAgentBuilder(llm=mock_llm)

            state = {"blueprints": {"error": "Gemini timeout"}}
            result = builder._dispatch_after_expander(state)

            # Should return a list with Send to finalize
            assert isinstance(result, list)
            assert len(result) == 1
            # Send object should target "finalize"
            assert result[0].node == "finalize"

    def test_llm_exception_produces_error_blueprint(self):
        """When LLM.generate() raises an exception, blueprints contain error key."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(side_effect=RuntimeError("Connection timeout"))

        agent = ExpanderAgent(llm=mock_llm)
        result = agent.invoke(
            {
                "requirements": "test",
                "architecture": MOCK_ARCHITECTURE,
            }
        )

        blueprints = result["blueprints"]
        assert "error" in blueprints
        assert "Connection timeout" in blueprints["error"]
        assert blueprints.get("fallback") is True

    def test_empty_response_produces_error(self):
        """Response shorter than 50 chars triggers ValueError → error blueprint."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(return_value=MockLLMResponse(content="{}"))

        agent = ExpanderAgent(llm=mock_llm)
        result = agent.invoke(
            {
                "requirements": "test",
                "architecture": MOCK_ARCHITECTURE,
            }
        )

        blueprints = result["blueprints"]
        assert "error" in blueprints or "fallback" in blueprints


# ---------------------------------------------------------------------------
# 4. PERFORMANCE: Pipeline latency measurement (5 runs)
# ---------------------------------------------------------------------------


class TestPerformanceDelta:

    def test_pipeline_latency_5_runs(self):
        """Run pipeline 5 times and report avg/min/max latency."""
        from ai.agents.multi_agent import MultiAgentBuilder

        latencies = []
        mock_llm = make_mock_llm()

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False):

            for run in range(5):
                builder = MultiAgentBuilder(llm=mock_llm, checkpointer=None)

                initial_state = {
                    "messages": [HumanMessage(content="Build a task app")],
                    "requirements": "Build a task app",
                    "project_description": "Build a task app",
                    "architecture": None,
                    "frontend_code": None,
                    "backend_code": None,
                    "tests": None,
                    "review_results": None,
                    "current_phase": "architect",
                    "iteration": 0,
                    "errors": [],
                    "error": None,
                    "final_project": None,
                    "blueprints": None,
                    "stuck_count": 0,
                    "previous_issues_embeddings": None,
                    "previous_issues_text": None,
                    "model_switch_history": None,
                    "_override_model": None,
                    "_clear_failed_context": None,
                    "_include_anti_patterns": None,
                    "_latest_issues_embeddings": None,
                    "_latest_issues_text": None,
                    "guardrails_violations": None,
                    "guardrails_iteration": 0,
                    "expert_sos_suggested": None,
                    "schema_migration": None,
                    "previous_schema_fingerprint": None,
                    "visual_qa_report": None,
                    "visual_qa_screenshots": None,
                }

                thread_config = {"configurable": {"thread_id": f"perf_run_{run}"}}

                start = time.perf_counter()
                for event in builder._stream_workflow(initial_state, thread_config):
                    pass
                elapsed = time.perf_counter() - start
                latencies.append(elapsed)

        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        print(f"\n{'='*60}")
        print("PERFORMANCE DELTA REPORT (5 runs, mocked LLM)")
        print(f"{'='*60}")
        print(f"  Average: {avg_latency:.4f}s")
        print(f"  Min:     {min_latency:.4f}s")
        print(f"  Max:     {max_latency:.4f}s")
        print(
            f"  Stddev:  {(sum((x - avg_latency)**2 for x in latencies) / len(latencies))**0.5:.4f}s"
        )
        print(f"{'='*60}")

        # Orchestration overhead should be < 5s per run with mocked LLM
        # (real LLM calls are mocked, so this measures pure graph traversal)
        assert (
            avg_latency < 5.0
        ), f"Pipeline orchestration too slow: {avg_latency:.2f}s avg"

    def test_expander_node_overhead_isolated(self):
        """Measure expander node alone (should be < 100ms with mocked LLM)."""
        from ai.agents.multi_agent import ExpanderAgent

        mock_llm = make_mock_llm()
        agent = ExpanderAgent(llm=mock_llm)

        state = {
            "requirements": "Build a task management app",
            "architecture": MOCK_ARCHITECTURE,
        }

        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            agent.invoke(state)
            latencies.append(time.perf_counter() - start)

        avg = sum(latencies) / len(latencies)
        print(f"\n  Expander node avg: {avg*1000:.2f}ms (10 runs)")

        # Pure overhead (no LLM) should be negligible
        assert avg < 0.1, f"Expander node overhead too high: {avg*1000:.2f}ms"


# ---------------------------------------------------------------------------
# 5. Routing Logic — Direct unit tests
# ---------------------------------------------------------------------------


class TestRoutingLogic:

    def test_route_after_expander_success(self):
        """Successful blueprints route to frontend."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False):
            builder = MultiAgentBuilder(llm=make_mock_llm())
            state = {"blueprints": MOCK_BLUEPRINTS}
            assert builder._route_after_expander(state) == "frontend"

    def test_route_after_expander_none_blueprints(self):
        """None/empty blueprints route to frontend (not error)."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", False):
            builder = MultiAgentBuilder(llm=make_mock_llm())

            # None blueprints
            assert builder._route_after_expander({"blueprints": None}) == "frontend"
            # Empty dict
            assert builder._route_after_expander({"blueprints": {}}) == "frontend"

    def test_dispatch_sends_both_when_backend_needed(self):
        """Parallel dispatch sends to both frontend and backend when tech_stack has backend."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.USE_PARALLEL_EXECUTION", True), patch(
            "ai.agents.multi_agent._SEND_AVAILABLE", True
        ):

            builder = MultiAgentBuilder(llm=make_mock_llm())
            state = {
                "blueprints": MOCK_BLUEPRINTS,
                "architecture": MOCK_ARCHITECTURE,  # has tech_stack.backend = "Express"
                "requirements": "test",
                "project_description": "test",
            }

            result = builder._dispatch_after_expander(state)
            assert isinstance(result, list)
            nodes = [s.node for s in result]
            assert "frontend" in nodes
            assert "backend" in nodes
