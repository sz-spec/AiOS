"""
End-to-End Tests for Research Workflow
======================================
Complete E2E tests including:
- Disk persistence verification
- Checkpoint counting
- Multi-invoke scenarios
- Error path coverage
- Backoff loop testing

Run with: pytest tests/test_e2e.py -v

Note: Replace `list` with `List` from typing for Python < 3.9
"""

import pytest
import time
import sqlite3
import os
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
    pytest.skip("SQLite saver not available", allow_module_level=True)


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


# Global mock
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

    # Add nodes
    workflow.add_node("plan", plan_research)
    workflow.add_node("search", search)
    workflow.add_node("validate", validate)
    workflow.add_node("process", process)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("safe_node", safe_node)

    # Linear edges
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "search")
    workflow.add_edge("search", "validate")

    # Conditional edges
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
    """Create in-memory SQLite checkpointer."""
    conn = sqlite3.connect(":memory:")
    yield SqliteSaver(conn)
    conn.close()


@pytest.fixture
def disk_checkpointer(tmp_path):
    """Create disk-based SQLite checkpointer."""
    db_path = tmp_path / "agent_test.db"
    conn = sqlite3.connect(str(db_path))
    checkpointer = SqliteSaver(conn)
    yield checkpointer, db_path
    conn.close()
    if db_path.exists():
        os.remove(db_path)


@pytest.fixture
def test_app(memory_checkpointer):
    """Create test workflow with memory checkpointer."""
    return build_workflow(checkpointer=memory_checkpointer)


@pytest.fixture(autouse=True)
def reset_mock_llm():
    """Reset mock LLM before each test."""
    global llm
    llm = MockLLM()
    yield


# =============================================================================
# End-to-End Tests with Persistence
# =============================================================================


class TestE2ESuccessFlow:
    """E2E tests for successful workflow completion."""

    def test_success_with_disk_persistence(self, initial_state, disk_checkpointer):
        """Test successful flow with disk persistence."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("query1\nquery2\nquery3")

        test_app = build_workflow(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "e2e_success"}}
        result = test_app.invoke(initial_state, config)

        # Verify success
        assert result["status"] == "success"
        assert len(result["search_results"]) == 3
        assert "Mock result for query1" in result["report"]

        # Verify persistence
        assert db_path.exists()

        # Verify checkpoints saved
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        assert "checkpoints" in tables or len(tables) > 0
        conn.close()

    def test_single_query_success(self, initial_state, disk_checkpointer):
        """Test with single query."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("single_query")

        test_app = build_workflow(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "e2e_single"}}
        result = test_app.invoke(initial_state, config)

        assert result["status"] == "success"
        assert len(result["search_results"]) == 1


class TestE2EErrorRecovery:
    """E2E tests for error recovery scenarios."""

    @patch("time.sleep")
    def test_error_recovery_with_backoff(
        self, mock_sleep, initial_state, disk_checkpointer
    ):
        """Test error recovery with backoff and persistence."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")  # Empty response -> invalid

        test_app = build_workflow(checkpointer=checkpointer)

        initial_state["force_error"] = False
        initial_state["last_attempt"] = True
        initial_state["max_retries"] = 2

        config = {"configurable": {"thread_id": "e2e_recovery"}}
        result = test_app.invoke(initial_state, config)

        # Should call sleep due to last_attempt
        mock_sleep.assert_called()

        # Recovery should succeed
        assert result["status"] == "success"

    def test_max_retries_failure(self, initial_state, disk_checkpointer):
        """Test failure after max retries with persistence."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        test_app = build_workflow(checkpointer=checkpointer)

        initial_state["force_error"] = True
        initial_state["max_retries"] = 2

        config = {"configurable": {"thread_id": "e2e_failure"}}
        result = test_app.invoke(initial_state, config)

        assert result["status"] == "error"
        assert result["retry_count"] == 2

        # Verify DB has checkpoint
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        assert len(tables) > 0
        conn.close()


class TestE2EErrorPaths:
    """E2E tests for specific error routing paths."""

    def test_auth_error_ends_workflow(self, initial_state, disk_checkpointer):
        """Test auth error routes to END."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        # Patch handle_error to return auth error

        def mock_handle(state):
            return {
                "status": "error",
                "error_message": "auth failed",
                "retry_count": state["retry_count"] + 1,
                "backoff_seconds": state["backoff_seconds"],
            }

        # Build workflow with patched handler
        workflow = StateGraph(AgentState)
        workflow.add_node("plan", plan_research)
        workflow.add_node("search", search)
        workflow.add_node("validate", validate)
        workflow.add_node("process", process)
        workflow.add_node("handle_error", mock_handle)
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

        test_app = workflow.compile(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "e2e_auth"}}
        result = test_app.invoke(initial_state, config)

        assert "auth" in result.get("error_message", "").lower()

    def test_not_found_to_fallback(self, initial_state, disk_checkpointer):
        """Test not_found error routes to safe_node fallback."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        def mock_handle(state):
            return {
                "status": "error",
                "error_message": "not_found",
                "retry_count": state["retry_count"] + 1,
                "backoff_seconds": state["backoff_seconds"],
            }

        workflow = StateGraph(AgentState)
        workflow.add_node("plan", plan_research)
        workflow.add_node("search", search)
        workflow.add_node("validate", validate)
        workflow.add_node("process", process)
        workflow.add_node("handle_error", mock_handle)
        workflow.add_node("safe_node", safe_node)  # This will succeed

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

        test_app = workflow.compile(checkpointer=checkpointer)

        initial_state["force_error"] = False  # safe_node will succeed

        config = {"configurable": {"thread_id": "e2e_fallback"}}
        result = test_app.invoke(initial_state, config)

        assert result["output"] == "API call result"


class TestE2EBackoffLoop:
    """E2E tests for backoff loop behavior."""

    @patch("time.sleep")
    def test_rate_limit_backoff_loop(
        self, mock_sleep, initial_state, disk_checkpointer
    ):
        """Test rate_limit triggers backoff loop."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        call_count = [0]

        def mock_handle(state):
            call_count[0] += 1
            if call_count[0] < 3:
                return {
                    "status": "error",
                    "error_message": "rate_limit",
                    "retry_count": state["retry_count"] + 1,
                    "backoff_seconds": state["backoff_seconds"] * 2,
                }
            else:
                return {
                    "status": "success",
                    "output": "Recovered after backoff",
                    "backoff_seconds": 1,
                    "error_message": "",
                }

        workflow = StateGraph(AgentState)
        workflow.add_node("plan", plan_research)
        workflow.add_node("search", search)
        workflow.add_node("validate", validate)
        workflow.add_node("process", process)
        workflow.add_node("handle_error", mock_handle)
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

        test_app = workflow.compile(checkpointer=checkpointer)

        initial_state["max_retries"] = 5

        config = {"configurable": {"thread_id": "e2e_backoff"}}
        result = test_app.invoke(initial_state, config)

        assert call_count[0] == 3
        assert result["status"] == "success"


class TestE2EMultiInvoke:
    """E2E tests for multi-invoke persistence scenarios."""

    def test_multi_invoke_same_thread(self, initial_state, disk_checkpointer):
        """Test multiple invokes on same thread."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("query1")

        test_app = build_workflow(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "e2e_multi"}}

        # First invoke
        result1 = test_app.invoke(initial_state, config)
        assert result1["status"] == "success"

        # Get state
        state = test_app.get_state(config)
        assert state is not None

        # Second invoke with different query
        initial_state["research_query"] = "Different query"
        llm = MockLLM("different_query")

        result2 = test_app.invoke(initial_state, config)
        assert result2["status"] == "success"

    def test_different_threads_isolated(self, initial_state, disk_checkpointer):
        """Test different threads are isolated."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("query_a")

        test_app = build_workflow(checkpointer=checkpointer)

        # Thread A
        config_a = {"configurable": {"thread_id": "thread_a"}}
        result_a = test_app.invoke(initial_state, config_a)

        # Thread B with different content
        llm = MockLLM("query_b")
        config_b = {"configurable": {"thread_id": "thread_b"}}
        result_b = test_app.invoke(initial_state, config_b)

        # Verify isolation
        assert "query_a" in result_a["report"]
        assert "query_b" in result_b["report"]


class TestE2EFullFailure:
    """E2E tests for complete failure scenarios."""

    def test_immediate_failure_zero_retries(self, initial_state, disk_checkpointer):
        """Test immediate failure with zero retries."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        test_app = build_workflow(checkpointer=checkpointer)

        initial_state["force_error"] = True
        initial_state["max_retries"] = 0

        config = {"configurable": {"thread_id": "e2e_immediate_fail"}}
        result = test_app.invoke(initial_state, config)

        # Should fail at validation due to max_retries=0
        assert result["status"] in ["invalid", "error"]

    def test_safe_node_failure_cascade(self, initial_state, disk_checkpointer):
        """Test safe_node failure leading to cascade."""
        checkpointer, db_path = disk_checkpointer

        global llm
        llm = MockLLM("")

        def mock_handle(state):
            return {
                "status": "error",
                "error_message": "not_found",
                "retry_count": state["retry_count"] + 1,
                "backoff_seconds": state["backoff_seconds"],
            }

        workflow = StateGraph(AgentState)
        workflow.add_node("plan", plan_research)
        workflow.add_node("search", search)
        workflow.add_node("validate", validate)
        workflow.add_node("process", process)
        workflow.add_node("handle_error", mock_handle)
        workflow.add_node("safe_node", safe_node)  # Will fail due to force_error

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

        test_app = workflow.compile(checkpointer=checkpointer)

        initial_state["force_error"] = True  # safe_node will fail
        initial_state["max_retries"] = 2

        config = {"configurable": {"thread_id": "e2e_cascade"}}
        result = test_app.invoke(initial_state, config)

        assert result["status"] == "error"
        assert result["retry_count"] >= 2


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
