"""
Integration Tests for Research Workflow
=======================================
Full integration tests with memory checkpointing, error flows, and edge cases.
Run with: pytest tests/test_integration.py -v

Note: Import issue fix - use `List` from typing, not `list` directly in annotations
for Python < 3.9 compatibility.
"""

import pytest
import time
import sqlite3
from typing import TypedDict, Annotated, List, Dict, Any
from unittest.mock import patch, MagicMock

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END, START

# Try SQLite saver, fallback to memory
try:
    from langgraph.checkpoint.sqlite import SqliteSaver

    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False


# =============================================================================
# State Definition (Fixed: List instead of list for compatibility)
# =============================================================================


class AgentState(TypedDict):
    """Agent state - shared between all nodes."""

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
    force_error: bool  # For testing


# =============================================================================
# Mock LLM for Testing
# =============================================================================


class MockLLM:
    """Mock LLM for testing without API calls."""

    def __init__(self, response_content: str = "query1\nquery2\nquery3"):
        self.response_content = response_content
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        mock_response = MagicMock()
        mock_response.content = self.response_content
        return mock_response


# Global mock LLM for tests
mock_llm = MockLLM()


# =============================================================================
# Agent Nodes
# =============================================================================


def plan_research(state: AgentState) -> Dict[str, Any]:
    """Generate search queries from research question."""
    query = state["research_query"]

    response = mock_llm.invoke(
        [
            SystemMessage(content="You are a research planner."),
            HumanMessage(content=f"Create 3-5 search queries for: {query}"),
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
    # If handle_error succeeded (status=success), route to process/end
    if state.get("status") == "success":
        return "success_end"

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
# Fixtures
# =============================================================================


@pytest.fixture
def initial_state() -> AgentState:
    """Create fresh initial state for each test."""
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
    """Create in-memory checkpointer for testing."""
    if SQLITE_AVAILABLE:
        conn = sqlite3.connect(":memory:")
        return SqliteSaver(conn)
    else:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


@pytest.fixture
def test_app(memory_checkpointer):
    """Create test workflow with memory checkpointer."""
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
            "success_end": END,
            "backoff": "handle_error",
            "refresh_credentials": END,
            "try_fallback": "safe_node",
            "retry": "search",
        },
    )

    workflow.add_edge("process", END)
    workflow.add_edge("safe_node", "validate")

    return workflow.compile(checkpointer=memory_checkpointer)


@pytest.fixture(autouse=True)
def reset_mock_llm():
    """Reset mock LLM before each test."""
    global mock_llm
    mock_llm = MockLLM()
    yield


# =============================================================================
# Unit Tests
# =============================================================================


class TestPlanResearch:
    """Unit tests for plan_research node."""

    def test_generates_queries(self, initial_state):
        """Test query generation."""
        global mock_llm
        mock_llm = MockLLM("query1\nquery2\nquery3\nquery4\nquery5")

        result = plan_research(initial_state)

        assert "search_queries" in result
        assert isinstance(result["search_queries"], list)
        assert len(result["search_queries"]) == 5
        assert result["current_stage"] == "searching"

    def test_empty_response(self, initial_state):
        """Test with empty LLM response."""
        global mock_llm
        mock_llm = MockLLM("")

        result = plan_research(initial_state)

        assert result["search_queries"] == []
        assert result["current_stage"] == "searching"

    def test_max_five_queries(self, initial_state):
        """Test that max 5 queries are returned."""
        global mock_llm
        mock_llm = MockLLM("\n".join([f"query{i}" for i in range(10)]))

        result = plan_research(initial_state)

        assert len(result["search_queries"]) == 5


class TestSearch:
    """Unit tests for search node."""

    def test_with_queries(self, initial_state):
        """Test search with queries."""
        initial_state["search_queries"] = ["query1", "query2"]

        result = search(initial_state)

        assert len(result["search_results"]) == 2
        assert result["current_stage"] == "validating"

    def test_no_queries(self, initial_state):
        """Test search with no queries."""
        initial_state["search_queries"] = []

        result = search(initial_state)

        assert len(result["search_results"]) == 0
        assert result["current_stage"] == "validating"


class TestValidate:
    """Unit tests for validate node."""

    def test_valid_results(self, initial_state):
        """Test validation with valid results."""
        initial_state["search_results"] = [{"result": "test"}]

        result = validate(initial_state)

        assert result["status"] == "valid"
        assert result["current_stage"] == "processing"

    def test_invalid_results(self, initial_state):
        """Test validation with no results."""
        initial_state["search_results"] = []

        result = validate(initial_state)

        assert result["status"] == "invalid"
        assert result["current_stage"] == "handling_error"


class TestProcess:
    """Unit tests for process node."""

    def test_creates_report(self, initial_state):
        """Test report creation."""
        initial_state["search_results"] = [{"result": "test1"}, {"result": "test2"}]

        result = process(initial_state)

        assert result["report"] == "test1\ntest2"
        assert result["status"] == "success"
        assert result["current_stage"] == "complete"

    def test_empty_results(self, initial_state):
        """Test with empty results."""
        initial_state["search_results"] = []

        result = process(initial_state)

        assert result["report"] == ""
        assert result["status"] == "success"


class TestHandleError:
    """Unit tests for handle_error node."""

    @patch("time.sleep")
    def test_success_recovery(self, mock_sleep, initial_state):
        """Test successful error recovery."""
        result = handle_error(initial_state)

        mock_sleep.assert_not_called()
        assert result["status"] == "success"
        assert result["output"] == "Recovered result"
        assert result["backoff_seconds"] == 1

    @patch("time.sleep")
    def test_backoff_on_last_attempt(self, mock_sleep, initial_state):
        """Test backoff is applied on last attempt."""
        initial_state["last_attempt"] = True
        initial_state["backoff_seconds"] = 2

        result = handle_error(initial_state)

        mock_sleep.assert_called_once_with(2)
        assert result["status"] == "success"

    def test_failure_increments_retry(self, initial_state):
        """Test failure increments retry count."""
        initial_state["force_error"] = True

        result = handle_error(initial_state)

        assert result["status"] == "error"
        assert result["retry_count"] == 1
        assert result["backoff_seconds"] == 2
        assert "Forced error" in result["error_message"]

    @patch("time.sleep")
    def test_failure_with_last_attempt(self, mock_sleep, initial_state):
        """Test failure on last attempt still waits."""
        initial_state["last_attempt"] = True
        initial_state["force_error"] = True

        result = handle_error(initial_state)

        mock_sleep.assert_called_once()
        assert result["status"] == "error"

    def test_backoff_doubles(self, initial_state):
        """Test backoff doubles on each failure."""
        initial_state["force_error"] = True
        initial_state["backoff_seconds"] = 4

        result = handle_error(initial_state)

        assert result["backoff_seconds"] == 8

    def test_backoff_capped_at_60(self, initial_state):
        """Test backoff is capped at 60 seconds."""
        initial_state["force_error"] = True
        initial_state["backoff_seconds"] = 32

        result = handle_error(initial_state)

        assert result["backoff_seconds"] == 60


class TestSafeNode:
    """Unit tests for safe_node."""

    def test_success(self, initial_state):
        """Test successful operation."""
        result = safe_node(initial_state)

        assert result["status"] == "success"
        assert result["output"] == "API call result"

    def test_failure(self, initial_state):
        """Test failure handling."""
        initial_state["force_error"] = True

        result = safe_node(initial_state)

        assert result["status"] == "error"
        assert result["retry_count"] == 1
        assert "Forced error" in result["error_message"]


# =============================================================================
# Routing Tests
# =============================================================================


class TestRouteValidation:
    """Tests for route_validation function."""

    def test_routes_to_process(self, initial_state):
        """Test routing to process on valid."""
        initial_state["status"] = "valid"
        initial_state["retry_count"] = 0

        assert route_validation(initial_state) == "process"

    def test_routes_to_fail_on_max_retries(self, initial_state):
        """Test routing to fail on max retries."""
        initial_state["status"] = "invalid"
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_validation(initial_state) == "fail"

    def test_routes_to_improve(self, initial_state):
        """Test routing to improve on invalid with retries left."""
        initial_state["status"] = "invalid"
        initial_state["retry_count"] = 1
        initial_state["max_retries"] = 3

        assert route_validation(initial_state) == "improve"


class TestRouteError:
    """Tests for route_error function."""

    def test_rate_limit(self, initial_state):
        """Test rate limit routing."""
        initial_state["error_message"] = "rate_limit exceeded"
        assert route_error(initial_state) == "backoff"

    def test_auth_error(self, initial_state):
        """Test auth error routing."""
        initial_state["error_message"] = "auth failed"
        assert route_error(initial_state) == "refresh_credentials"

    def test_not_found(self, initial_state):
        """Test not found routing."""
        initial_state["error_message"] = "resource not_found"
        assert route_error(initial_state) == "try_fallback"

    def test_default_retry(self, initial_state):
        """Test default retry routing."""
        initial_state["error_message"] = "unknown error"
        assert route_error(initial_state) == "retry"

    def test_empty_error(self, initial_state):
        """Test empty error message."""
        initial_state["error_message"] = ""
        assert route_error(initial_state) == "retry"

    def test_case_insensitive(self, initial_state):
        """Test case insensitive matching."""
        initial_state["error_message"] = "RATE_LIMIT error"
        assert route_error(initial_state) == "backoff"


class TestRouteRetry:
    """Tests for route_retry function."""

    def test_can_retry(self, initial_state):
        """Test retry when retries remaining."""
        initial_state["retry_count"] = 2
        initial_state["max_retries"] = 3

        assert route_retry(initial_state) == "retry"

    def test_fail_on_max(self, initial_state):
        """Test fail when max reached."""
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_retry(initial_state) == "fail"


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegrationSuccessFlow:
    """Integration tests for successful workflow."""

    def test_full_success_flow(self, initial_state, test_app):
        """Test complete successful workflow."""
        global mock_llm
        mock_llm = MockLLM("query1\nquery2")

        config = {"configurable": {"thread_id": "test_success"}}
        result = test_app.invoke(initial_state, config)

        assert result["status"] == "success"
        assert "Mock result for query1" in result["report"]
        assert result["current_stage"] == "complete"
        assert result["retry_count"] == 0
        assert len(result["search_results"]) == 2

    def test_single_query_success(self, initial_state, test_app):
        """Test with single query."""
        global mock_llm
        mock_llm = MockLLM("single_query")

        config = {"configurable": {"thread_id": "test_single"}}
        result = test_app.invoke(initial_state, config)

        assert result["status"] == "success"
        assert len(result["search_results"]) == 1


class TestIntegrationErrorRecovery:
    """Integration tests for error recovery flows."""

    @patch("time.sleep")
    def test_invalid_to_recovery_success(self, mock_sleep, initial_state, test_app):
        """Test recovery from invalid results."""
        global mock_llm
        mock_llm = MockLLM("")  # Empty -> invalid

        initial_state["force_error"] = False
        initial_state["max_retries"] = 2

        config = {"configurable": {"thread_id": "test_recovery"}}
        result = test_app.invoke(initial_state, config)

        # handle_error succeeds, routes to retry -> search -> still empty -> loop
        # But since force_error=False, handle_error succeeds each time
        assert result["status"] == "success"

    @patch("time.sleep")
    def test_max_retries_reached(self, mock_sleep, initial_state, test_app):
        """Test failure after max retries."""
        global mock_llm
        mock_llm = MockLLM("")

        initial_state["force_error"] = True
        initial_state["max_retries"] = 1

        config = {"configurable": {"thread_id": "test_max_retries"}}
        result = test_app.invoke(initial_state, config)

        # Flow: plan empty -> search empty -> validate invalid -> handle_error fail
        # -> route_error "retry" -> search -> validate invalid (retry_count=1 >= max)
        # -> route_validation "fail" -> END
        assert result["retry_count"] >= 1


class TestIntegrationErrorTypes:
    """Integration tests for different error types."""

    def test_rate_limit_backoff(self, initial_state, test_app):
        """Test rate limit triggers backoff."""
        global mock_llm
        mock_llm = MockLLM("")

        # Patch handle_error to return rate_limit error

        def mock_handle(state):
            return {
                "status": "error",
                "error_message": "rate_limit exceeded",
                "retry_count": state["retry_count"] + 1,
                "backoff_seconds": state["backoff_seconds"] * 2,
            }

        # Note: For full mocking, would need to rebuild workflow
        # This test verifies the routing logic
        initial_state["error_message"] = "rate_limit exceeded"
        assert route_error(initial_state) == "backoff"

    def test_auth_error_ends(self, initial_state):
        """Test auth error routes to end."""
        initial_state["error_message"] = "auth failed"
        assert route_error(initial_state) == "refresh_credentials"

    def test_not_found_fallback(self, initial_state):
        """Test not found routes to fallback."""
        initial_state["error_message"] = "not_found"
        assert route_error(initial_state) == "try_fallback"


class TestIntegrationCheckpointing:
    """Integration tests for checkpointing."""

    def test_different_threads_isolated(self, initial_state, test_app):
        """Test different threads are isolated."""
        global mock_llm
        mock_llm = MockLLM("query1")

        config1 = {"configurable": {"thread_id": "thread1"}}
        config2 = {"configurable": {"thread_id": "thread2"}}

        result1 = test_app.invoke(initial_state, config1)

        # Modify state
        initial_state["research_query"] = "Different query"
        mock_llm = MockLLM("different")

        result2 = test_app.invoke(initial_state, config2)

        # Results should be different
        assert result1["report"] != result2["report"]


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_zero_max_retries(self, initial_state):
        """Test with zero max retries."""
        initial_state["max_retries"] = 0
        initial_state["retry_count"] = 0
        initial_state["status"] = "invalid"

        assert route_validation(initial_state) == "fail"

    def test_very_long_query(self, initial_state):
        """Test with very long query."""
        initial_state["research_query"] = "A" * 10000
        result = plan_research(initial_state)

        assert "search_queries" in result

    def test_special_characters(self, initial_state):
        """Test with special characters in query."""
        initial_state["search_queries"] = ["test?query", "with&special<chars>"]

        result = search(initial_state)
        assert len(result["search_results"]) == 2

    def test_unicode_queries(self, initial_state):
        """Test with unicode queries."""
        initial_state["search_queries"] = ["מחקר בעברית", "研究中文", "исследование"]

        result = search(initial_state)
        assert len(result["search_results"]) == 3


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
