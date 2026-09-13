"""
Performance Tests for Research Workflow
=======================================
Performance and scalability tests using:
- pytest-benchmark for micro-benchmarks
- cProfile for detailed profiling
- Scalability testing

Installation:
    pip install pytest-benchmark

Run benchmarks:
    pytest tests/test_performance.py --benchmark-save=results
    pytest tests/test_performance.py --benchmark-compare
    pytest tests/test_performance.py --benchmark-autosave

Note: Replace `list` with `List` from typing for Python < 3.9
"""

import pytest
import time
import sqlite3
import cProfile
import pstats
from io import StringIO
from typing import TypedDict, Annotated, List, Dict, Any
from unittest.mock import patch, MagicMock

from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, END, START

# Try SQLite saver
try:
    from langgraph.checkpoint.sqlite import SqliteSaver

    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False


# =============================================================================
# State Definition
# =============================================================================


class AgentState(TypedDict):
    """Shared state across all agents."""

    messages: Annotated[List[BaseMessage], "add_messages"]
    current_stage: str
    retry_count: int
    search_results: List[Dict[str, Any]]
    status: str
    error_message: str
    backoff_seconds: int
    max_retries: int
    research_query: str
    search_queries: List[str]
    output: str
    report: str
    last_attempt: bool
    force_error: bool


# =============================================================================
# Mock LLM
# =============================================================================


class MockLLM:
    """Mock LLM for testing."""

    def __init__(self, content: str = "query1\nquery2\nquery3"):
        self.content = content
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        response = MagicMock()
        response.content = self.content
        return response


llm = MockLLM()


# =============================================================================
# Agent Nodes
# =============================================================================


def plan_research(state: AgentState) -> Dict[str, Any]:
    """Generate search queries from research question."""
    query = state["research_query"]
    response = llm.invoke(
        [
            {"role": "system", "content": "You are a research planner."},
            {"role": "user", "content": f"Create 3-5 search queries for: {query}"},
        ]
    )

    def parse_queries(content: str) -> List[str]:
        return [q.strip() for q in content.split("\n") if q.strip()][:5]

    queries = parse_queries(response.content)
    return {"search_queries": queries, "current_stage": "searching"}


def search(state: AgentState) -> Dict[str, Any]:
    """Perform search based on queries."""
    results = [{"result": f"Mock result for {q}"} for q in state["search_queries"]]
    return {"search_results": results, "current_stage": "validating"}


def validate(state: AgentState) -> Dict[str, Any]:
    """Validate search results."""
    is_valid = len(state["search_results"]) > 0
    return {
        "status": "valid" if is_valid else "invalid",
        "current_stage": "processing" if is_valid else "handling_error",
    }


def process(state: AgentState) -> Dict[str, Any]:
    """Process results into report."""
    report = "\n".join([r["result"] for r in state["search_results"]])
    return {"report": report, "current_stage": "complete", "status": "success"}


def handle_error(state: AgentState) -> Dict[str, Any]:
    """Handle errors with exponential backoff."""
    if state.get("last_attempt", False):
        time.sleep(state["backoff_seconds"])

    try:
        if state.get("force_error", False):
            raise ValueError("Forced error for testing")

        return {
            "output": "Recovered result",
            "backoff_seconds": 1,
            "status": "success",
            "error_message": "",
        }
    except Exception as e:
        return {
            "retry_count": state["retry_count"] + 1,
            "backoff_seconds": min(state["backoff_seconds"] * 2, 60),
            "error_message": str(e),
            "status": "error",
        }


def safe_node(state: AgentState) -> Dict[str, Any]:
    """Safe operation with try-except wrapper."""
    try:
        if state.get("force_error", False):
            raise ValueError("Forced error for testing")

        return {"output": "API call result", "status": "success"}
    except Exception as e:
        return {
            "status": "error",
            "error_message": str(e),
            "retry_count": state["retry_count"] + 1,
        }


# =============================================================================
# Routing Functions
# =============================================================================


def route_validation(state: AgentState) -> str:
    """Route based on validation results."""
    if state["status"] == "valid" and state["retry_count"] < state["max_retries"]:
        return "process"
    elif state["retry_count"] >= state["max_retries"]:
        return "fail"
    else:
        return "improve"


def route_error(state: AgentState) -> str:
    """Route based on error type."""
    error = state.get("error_message", "")

    if "rate_limit" in error.lower():
        return "backoff"
    elif "auth" in error.lower():
        return "refresh_credentials"
    elif "not_found" in error.lower():
        return "try_fallback"
    else:
        return "retry"


def route_retry(state: AgentState) -> str:
    """Route retry logic."""
    if state["retry_count"] < state["max_retries"]:
        return "retry"
    else:
        return "fail"


# =============================================================================
# Workflow Builder
# =============================================================================


def build_workflow(checkpointer=None):
    """Build the research workflow graph."""
    workflow = StateGraph(AgentState)

    workflow.add_node("plan", plan_research)
    workflow.add_node("search", search)
    workflow.add_node("validate", validate)
    workflow.add_node("process", process)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("safe_node", safe_node)

    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "search")
    workflow.add_edge("search", "validate")

    workflow.add_conditional_edges(
        "validate",
        route_validation,
        {"process": "process", "fail": END, "improve": "handle_error"},
    )

    workflow.add_conditional_edges(
        "handle_error",
        route_error,
        {
            "backoff": "handle_error",
            "refresh_credentials": END,
            "try_fallback": "safe_node",
            "retry": "search",
        },
    )

    workflow.add_edge("process", END)
    workflow.add_edge("safe_node", "validate")

    return workflow.compile(checkpointer=checkpointer)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def initial_state() -> AgentState:
    """Create initial state for tests."""
    return {
        "messages": [],
        "current_stage": "planning",
        "retry_count": 0,
        "search_results": [],
        "status": "pending",
        "error_message": "",
        "backoff_seconds": 1,
        "max_retries": 3,
        "research_query": "AI trends in 2025",
        "search_queries": [],
        "output": "",
        "report": "",
        "last_attempt": False,
        "force_error": False,
    }


@pytest.fixture
def memory_checkpointer():
    """Create in-memory checkpointer."""
    if SQLITE_AVAILABLE:
        conn = sqlite3.connect(":memory:")
        yield SqliteSaver(conn)
        conn.close()
    else:
        from langgraph.checkpoint.memory import MemorySaver

        yield MemorySaver()


@pytest.fixture
def test_app(memory_checkpointer):
    """Create test workflow."""
    return build_workflow(checkpointer=memory_checkpointer)


@pytest.fixture(autouse=True)
def reset_mock_llm():
    """Reset mock LLM before each test."""
    global llm
    llm = MockLLM()
    yield


# =============================================================================
# Agent Node Benchmarks
# =============================================================================


class TestAgentNodePerformance:
    """Benchmark tests for individual agent nodes."""

    @pytest.mark.benchmark(group="agent_nodes")
    def test_benchmark_plan_research(self, benchmark, initial_state):
        """Benchmark plan_research node."""
        global llm
        llm = MockLLM("query1\nquery2\nquery3\nquery4\nquery5" * 10)

        result = benchmark(plan_research, initial_state)
        assert "search_queries" in result

    @pytest.mark.benchmark(group="agent_nodes")
    def test_benchmark_search(self, benchmark, initial_state):
        """Benchmark search node with many queries."""
        initial_state["search_queries"] = ["query"] * 100

        result = benchmark(search, initial_state)
        assert len(result["search_results"]) == 100

    @pytest.mark.benchmark(group="agent_nodes")
    def test_benchmark_validate(self, benchmark, initial_state):
        """Benchmark validate node."""
        initial_state["search_results"] = [{"result": "test"}] * 100

        result = benchmark(validate, initial_state)
        assert result["status"] == "valid"

    @pytest.mark.benchmark(group="agent_nodes")
    def test_benchmark_process(self, benchmark, initial_state):
        """Benchmark process node with large data."""
        initial_state["search_results"] = [{"result": "test" * 100}] * 50

        result = benchmark(process, initial_state)
        assert "report" in result

    @pytest.mark.benchmark(group="agent_nodes")
    @patch("time.sleep")
    def test_benchmark_handle_error(self, mock_sleep, benchmark, initial_state):
        """Benchmark handle_error node."""
        result = benchmark(handle_error, initial_state)
        assert result["status"] == "success"

    @pytest.mark.benchmark(group="agent_nodes")
    def test_benchmark_safe_node(self, benchmark, initial_state):
        """Benchmark safe_node."""
        result = benchmark(safe_node, initial_state)
        assert result["status"] == "success"


# =============================================================================
# Routing Function Benchmarks
# =============================================================================


class TestRoutingPerformance:
    """Benchmark tests for routing functions."""

    @pytest.mark.benchmark(group="routing")
    def test_benchmark_route_validation(self, benchmark, initial_state):
        """Benchmark route_validation."""
        initial_state["status"] = "valid"

        result = benchmark(route_validation, initial_state)
        assert result == "process"

    @pytest.mark.benchmark(group="routing")
    def test_benchmark_route_error(self, benchmark, initial_state):
        """Benchmark route_error."""
        initial_state["error_message"] = "rate_limit exceeded"

        result = benchmark(route_error, initial_state)
        assert result == "backoff"

    @pytest.mark.benchmark(group="routing")
    def test_benchmark_route_retry(self, benchmark, initial_state):
        """Benchmark route_retry."""
        result = benchmark(route_retry, initial_state)
        assert result == "retry"


# =============================================================================
# Full Workflow Benchmarks
# =============================================================================


class TestWorkflowPerformance:
    """Benchmark tests for full workflow execution."""

    @pytest.mark.benchmark(group="workflow")
    def test_benchmark_full_invoke(self, benchmark, initial_state, test_app):
        """Benchmark full workflow invoke."""
        global llm
        llm = MockLLM("query1\nquery2")

        config = {"configurable": {"thread_id": "perf_invoke"}}

        result = benchmark(test_app.invoke, initial_state, config)
        assert result["status"] == "success"

    @pytest.mark.benchmark(group="workflow")
    def test_benchmark_invoke_with_large_queries(
        self, benchmark, initial_state, test_app
    ):
        """Benchmark workflow with many queries."""
        global llm
        llm = MockLLM("\n".join([f"query{i}" for i in range(50)]))

        config = {"configurable": {"thread_id": "perf_large"}}

        result = benchmark(test_app.invoke, initial_state, config)
        assert result["status"] == "success"


# =============================================================================
# Scalability Tests
# =============================================================================


class TestScalability:
    """Scalability and stress tests."""

    @pytest.mark.benchmark(group="scalability")
    def test_benchmark_large_state(self, benchmark, initial_state, test_app):
        """Benchmark with large state data."""
        global llm
        llm = MockLLM("query" * 1000)

        initial_state["search_queries"] = ["large"] * 1000
        config = {"configurable": {"thread_id": "perf_scale"}}

        result = benchmark(test_app.invoke, initial_state, config)
        assert "report" in result

    @pytest.mark.benchmark(group="scalability")
    def test_benchmark_sequential_invocations(self, benchmark, initial_state, test_app):
        """Benchmark multiple sequential invocations."""
        global llm
        llm = MockLLM("query")

        def multi_invoke():
            results = []
            for i in range(10):
                config = {"configurable": {"thread_id": f"perf_conc_{i}"}}
                results.append(test_app.invoke(initial_state, config))
            return results

        results = benchmark(multi_invoke)
        assert len(results) == 10
        assert all(r["status"] == "success" for r in results)

    @pytest.mark.benchmark(group="scalability")
    def test_benchmark_deep_report(self, benchmark, initial_state):
        """Benchmark process with very large report."""
        initial_state["search_results"] = [{"result": "x" * 10000} for _ in range(100)]

        result = benchmark(process, initial_state)
        assert len(result["report"]) > 1000000


# =============================================================================
# Profiling Tests
# =============================================================================


class TestProfiling:
    """Detailed profiling tests using cProfile."""

    def test_profile_full_workflow(self, initial_state, test_app):
        """Profile full workflow execution."""
        global llm
        llm = MockLLM("query1\nquery2\nquery3")

        profiler = cProfile.Profile()
        config = {"configurable": {"thread_id": "prof_flow"}}

        profiler.enable()
        result = test_app.invoke(initial_state, config)
        profiler.disable()

        assert result["status"] == "success"

        # Analyze profile
        s = StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats("cumtime")
        ps.print_stats(10)
        profile_output = s.getvalue()

        # Verify profiling worked
        assert len(profile_output) > 0
        print("\n--- Profile Output (Top 10) ---")
        print(profile_output)

    def test_profile_plan_research(self, initial_state):
        """Profile plan_research node."""
        global llm
        llm = MockLLM("query1\nquery2\nquery3\nquery4\nquery5")

        profiler = cProfile.Profile()

        profiler.enable()
        for _ in range(100):  # Run multiple times for better stats
            plan_research(initial_state)
        profiler.disable()

        s = StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats("cumtime")
        ps.print_stats(5)
        profile_output = s.getvalue()

        print("\n--- Plan Research Profile ---")
        print(profile_output)

    def test_profile_search_scaling(self, initial_state):
        """Profile search with increasing query counts."""
        results = []

        for count in [10, 100, 500, 1000]:
            initial_state["search_queries"] = [f"query{i}" for i in range(count)]

            start = time.perf_counter()
            result = search(initial_state)
            elapsed = time.perf_counter() - start

            results.append(
                {
                    "count": count,
                    "time_ms": elapsed * 1000,
                    "results": len(result["search_results"]),
                }
            )

        print("\n--- Search Scaling ---")
        for r in results:
            print(f"  {r['count']:5d} queries: {r['time_ms']:8.3f}ms")

        # Verify linear scaling (roughly)
        assert results[-1]["time_ms"] < results[0]["time_ms"] * 200


# =============================================================================
# Memory Usage Tests
# =============================================================================


class TestMemoryUsage:
    """Memory usage tests."""

    def test_memory_large_state(self, initial_state):
        """Test memory with large state."""
        import sys

        # Create large state
        initial_state["search_results"] = [{"result": "x" * 10000} for _ in range(100)]

        state_size = sys.getsizeof(initial_state)
        results_size = sum(
            sys.getsizeof(r["result"]) for r in initial_state["search_results"]
        )

        print("\n--- Memory Usage ---")
        print(f"  State dict size: {state_size:,} bytes")
        print(f"  Results content: {results_size:,} bytes")
        print(f"  Total approx: {state_size + results_size:,} bytes")

        # Process should handle this
        result = process(initial_state)
        assert len(result["report"]) > 1000000

    def test_memory_many_invocations(self, initial_state, test_app):
        """Test memory stability across many invocations."""
        import gc

        global llm
        llm = MockLLM("query")

        # Force garbage collection before
        gc.collect()

        for i in range(50):
            config = {"configurable": {"thread_id": f"mem_test_{i}"}}
            test_app.invoke(initial_state, config)

        # Force garbage collection after
        gc.collect()

        # If we got here without memory error, test passes
        assert True


# =============================================================================
# Timing Assertions
# =============================================================================


class TestTimingAssertions:
    """Tests with timing assertions."""

    def test_search_completes_quickly(self, initial_state):
        """Ensure search completes within time limit."""
        initial_state["search_queries"] = ["query"] * 100

        start = time.perf_counter()
        search(initial_state)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"Search took {elapsed:.3f}s, expected < 0.1s"

    def test_validate_completes_quickly(self, initial_state):
        """Ensure validate completes within time limit."""
        initial_state["search_results"] = [{"result": "test"}] * 1000

        start = time.perf_counter()
        validate(initial_state)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.01, f"Validate took {elapsed:.3f}s, expected < 0.01s"

    def test_routing_completes_quickly(self, initial_state):
        """Ensure routing functions are fast."""
        iterations = 10000

        start = time.perf_counter()
        for _ in range(iterations):
            route_validation(initial_state)
            route_error(initial_state)
            route_retry(initial_state)
        elapsed = time.perf_counter() - start

        per_call = (elapsed / iterations / 3) * 1000000  # microseconds

        assert per_call < 10, f"Routing took {per_call:.1f}μs/call, expected < 10μs"


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--benchmark-autosave", "--benchmark-group-by=group"])
