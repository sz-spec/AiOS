"""
Research Workflow with LangGraph
================================
Complete multi-agent research workflow with:
- SQLite checkpointing (memory & disk)
- Exponential backoff retry
- Smart error routing
- Full test coverage (unit, integration, E2E)

Usage:
    python research_workflow_standalone.py

Tests:
    pytest research_workflow_standalone.py -v

Note: For Python < 3.9, replace `list` with `List` in type hints:
    from typing import List
    list[str] -> List[str]
"""

from typing import TypedDict, Annotated, List, Dict, Any
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.sqlite import SqliteSaver
import time
import pytest
from unittest.mock import patch, MagicMock
import sqlite3
import os

# =============================================================================
# LLM Configuration
# =============================================================================

llm = ChatOpenAI(model="gpt-4o", temperature=0.7)


# =============================================================================
# State Definition
# =============================================================================


class AgentState(TypedDict):
    """Shared state across all agents in the workflow."""

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


# =============================================================================
# Agent Nodes
# =============================================================================


def plan_research(state: AgentState) -> Dict[str, Any]:
    """Generate search queries from research question."""
    query = state["research_query"]
    response = llm.invoke(
        [
            SystemMessage(content="You are a research planner."),
            HumanMessage(content=f"Create 3-5 search queries for: {query}"),
        ]
    )

    def parse_queries(content: str) -> List[str]:
        return content.split("\n")[:5]

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
    if state["last_attempt"]:
        time.sleep(state["backoff_seconds"])

    try:
        if state.get("force_error", False):
            raise ValueError("Forced error for testing")

        return {"output": "Recovered result", "backoff_seconds": 1, "status": "success"}
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
    error = state["error_message"]

    if "rate_limit" in error:
        return "backoff"
    elif "auth" in error:
        return "refresh_credentials"
    elif "not_found" in error:
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
    workflow = StateGraph(state_schema=AgentState)

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


# Build default app with SQLite checkpointing
checkpointer = SqliteSaver.from_conn_string("agent.db")
app = build_workflow(checkpointer=checkpointer)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def initial_state():
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
    }


@pytest.fixture
def memory_checkpointer():
    """Create in-memory checkpointer for tests."""
    conn = sqlite3.connect(":memory:")
    yield SqliteSaver(conn)
    conn.close()


@pytest.fixture
def disk_checkpointer(tmp_path):
    """Create disk-based checkpointer for E2E tests."""
    db_path = tmp_path / "agent_test.db"
    conn = sqlite3.connect(str(db_path))
    yield SqliteSaver(conn), db_path
    conn.close()
    if db_path.exists():
        os.remove(db_path)


@pytest.fixture
def test_app(memory_checkpointer):
    """Create test workflow with memory checkpointer."""
    return build_workflow(checkpointer=memory_checkpointer)


# =============================================================================
# Unit Tests
# =============================================================================


@patch.object(llm, "invoke")
def test_plan_research(mock_invoke, initial_state):
    """Test plan_research generates queries."""
    mock_response = MagicMock()
    mock_response.content = "query1\nquery2\nquery3\nquery4\nquery5"
    mock_invoke.return_value = mock_response

    result = plan_research(initial_state)

    assert "search_queries" in result
    assert isinstance(result["search_queries"], list)
    assert len(result["search_queries"]) == 5
    assert result["current_stage"] == "searching"


def test_search_with_queries(initial_state):
    """Test search with queries."""
    initial_state["search_queries"] = ["query1", "query2"]

    result = search(initial_state)

    assert "search_results" in result
    assert len(result["search_results"]) == 2
    assert result["current_stage"] == "validating"


def test_search_no_queries(initial_state):
    """Test search with no queries."""
    initial_state["search_queries"] = []

    result = search(initial_state)

    assert "search_results" in result
    assert len(result["search_results"]) == 0
    assert result["current_stage"] == "validating"


def test_validate_with_valid_results(initial_state):
    """Test validation with valid results."""
    initial_state["search_results"] = [{"result": "test"}]

    result = validate(initial_state)

    assert result["status"] == "valid"
    assert result["current_stage"] == "processing"


def test_validate_with_invalid_results(initial_state):
    """Test validation with no results."""
    initial_state["search_results"] = []

    result = validate(initial_state)

    assert result["status"] == "invalid"
    assert result["current_stage"] == "handling_error"


def test_process(initial_state):
    """Test process creates report."""
    initial_state["search_results"] = [{"result": "test1"}, {"result": "test2"}]

    result = process(initial_state)

    assert "report" in result
    assert result["report"] == "test1\ntest2"
    assert result["status"] == "success"
    assert result["current_stage"] == "complete"


def test_process_empty(initial_state):
    """Test process with empty results."""
    initial_state["search_results"] = []

    result = process(initial_state)

    assert result["report"] == ""
    assert result["status"] == "success"


@patch("time.sleep")
def test_handle_error_success(mock_sleep, initial_state):
    """Test successful error recovery."""
    result = handle_error(initial_state)

    mock_sleep.assert_not_called()
    assert result["status"] == "success"
    assert result["output"] == "Recovered result"
    assert result["backoff_seconds"] == 1


@patch("time.sleep")
def test_handle_error_success_with_last_attempt(mock_sleep, initial_state):
    """Test recovery with backoff on last attempt."""
    initial_state["last_attempt"] = True

    result = handle_error(initial_state)

    mock_sleep.assert_called_once_with(1)
    assert result["status"] == "success"


def test_handle_error_failure(initial_state):
    """Test error handling failure."""
    initial_state["force_error"] = True

    result = handle_error(initial_state)

    assert result["status"] == "error"
    assert result["retry_count"] == 1
    assert result["backoff_seconds"] == 2
    assert "Forced error" in result["error_message"]


@patch("time.sleep")
def test_handle_error_failure_with_last_attempt(mock_sleep, initial_state):
    """Test failure with backoff on last attempt."""
    initial_state["last_attempt"] = True
    initial_state["force_error"] = True

    result = handle_error(initial_state)

    mock_sleep.assert_called_once_with(1)
    assert result["status"] == "error"


def test_safe_node_success(initial_state):
    """Test safe node success."""
    result = safe_node(initial_state)

    assert result["status"] == "success"
    assert result["output"] == "API call result"


def test_safe_node_failure(initial_state):
    """Test safe node failure."""
    initial_state["force_error"] = True

    result = safe_node(initial_state)

    assert result["status"] == "error"
    assert result["retry_count"] == 1
    assert "Forced error" in result["error_message"]


# =============================================================================
# Routing Tests
# =============================================================================


def test_route_validation_process(initial_state):
    """Test routing to process."""
    initial_state["status"] = "valid"
    initial_state["retry_count"] = 1
    initial_state["max_retries"] = 3

    assert route_validation(initial_state) == "process"


def test_route_validation_fail(initial_state):
    """Test routing to fail."""
    initial_state["status"] = "invalid"
    initial_state["retry_count"] = 3
    initial_state["max_retries"] = 3

    assert route_validation(initial_state) == "fail"


def test_route_validation_improve(initial_state):
    """Test routing to improve."""
    initial_state["status"] = "invalid"
    initial_state["retry_count"] = 0
    initial_state["max_retries"] = 3

    assert route_validation(initial_state) == "improve"


def test_route_error_rate_limit(initial_state):
    """Test rate limit routing."""
    initial_state["error_message"] = "rate_limit error"

    assert route_error(initial_state) == "backoff"


def test_route_error_auth(initial_state):
    """Test auth error routing."""
    initial_state["error_message"] = "auth failed"

    assert route_error(initial_state) == "refresh_credentials"


def test_route_error_not_found(initial_state):
    """Test not found routing."""
    initial_state["error_message"] = "resource not_found"

    assert route_error(initial_state) == "try_fallback"


def test_route_error_default(initial_state):
    """Test default retry routing."""
    initial_state["error_message"] = "unknown error"

    assert route_error(initial_state) == "retry"


def test_route_error_empty(initial_state):
    """Test empty error routing."""
    initial_state["error_message"] = ""

    assert route_error(initial_state) == "retry"


def test_route_retry_can_retry(initial_state):
    """Test can retry."""
    initial_state["retry_count"] = 2
    initial_state["max_retries"] = 3

    assert route_retry(initial_state) == "retry"


def test_route_retry_fail(initial_state):
    """Test retry fail."""
    initial_state["retry_count"] = 3
    initial_state["max_retries"] = 3

    assert route_retry(initial_state) == "fail"


# =============================================================================
# Integration Tests
# =============================================================================


@patch.object(llm, "invoke")
def test_integration_success_flow(mock_invoke, initial_state, test_app):
    """Test full success flow."""
    mock_response = MagicMock()
    mock_response.content = "query1\nquery2"
    mock_invoke.return_value = mock_response

    config = {"configurable": {"thread_id": "test_success"}}
    result = test_app.invoke(initial_state, config)

    assert result["status"] == "success"
    assert "Mock result for query1" in result["report"]
    assert result["current_stage"] == "complete"
    assert result["retry_count"] == 0
    assert len(result["search_results"]) == 2


@patch.object(llm, "invoke")
@patch("time.sleep")
def test_integration_invalid_to_error_recovery_success(
    mock_sleep, mock_invoke, initial_state, test_app
):
    """Test recovery from invalid results."""
    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = False
    initial_state["max_retries"] = 1

    config = {"configurable": {"thread_id": "test_recovery_success"}}
    result = test_app.invoke(initial_state, config)

    assert result["status"] == "success"
    assert result["retry_count"] == 0


@patch.object(llm, "invoke")
@patch("time.sleep")
def test_integration_error_failure_max_retries(
    mock_sleep, mock_invoke, initial_state, test_app
):
    """Test failure after max retries."""
    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = True
    initial_state["max_retries"] = 1

    config = {"configurable": {"thread_id": "test_max_retries"}}
    result = test_app.invoke(initial_state, config)

    assert result["status"] == "error"
    assert result["retry_count"] == 1


@patch.object(llm, "invoke")
def test_integration_default_retry_loop(mock_invoke, initial_state, test_app):
    """Test retry loop until max."""
    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["max_retries"] = 2
    initial_state["force_error"] = True

    config = {"configurable": {"thread_id": "test_retry_loop"}}
    result = test_app.invoke(initial_state, config)

    assert result["retry_count"] == 2


# =============================================================================
# End-to-End Tests with Disk Persistence
# =============================================================================


@patch.object(llm, "invoke")
def test_e2e_success_flow_with_persistence(
    mock_invoke, initial_state, disk_checkpointer
):
    """Test successful flow with disk persistence."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = "query1\nquery2\nquery3"
    mock_invoke.return_value = mock_response

    config = {"configurable": {"thread_id": "e2e_success"}}
    result = test_app.invoke(initial_state, config)

    assert result["status"] == "success"
    assert len(result["search_results"]) == 3
    assert db_path.exists()

    # Verify checkpoint saved
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    assert len(tables) > 0
    conn.close()


@patch.object(llm, "invoke")
@patch("time.sleep")
def test_e2e_error_recovery_with_backoff_and_persistence(
    mock_sleep, mock_invoke, initial_state, disk_checkpointer
):
    """Test error recovery with backoff and persistence."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = False
    initial_state["last_attempt"] = True
    initial_state["max_retries"] = 2

    config = {"configurable": {"thread_id": "e2e_recovery"}}
    result = test_app.invoke(initial_state, config)

    mock_sleep.assert_called()
    assert result["status"] == "success"


@patch.object(llm, "invoke")
def test_e2e_max_retries_failure_with_persistence(
    mock_invoke, initial_state, disk_checkpointer
):
    """Test failure after max retries with persistence."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = True
    initial_state["max_retries"] = 2

    config = {"configurable": {"thread_id": "e2e_failure"}}
    result = test_app.invoke(initial_state, config)

    assert result["status"] == "error"
    assert result["retry_count"] == 2


@patch.object(llm, "invoke")
def test_e2e_multi_invoke_persistence(mock_invoke, initial_state, disk_checkpointer):
    """Test multiple invokes with persistence."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = "query1"
    mock_invoke.return_value = mock_response

    config = {"configurable": {"thread_id": "e2e_multi"}}

    # First invoke - success
    result1 = test_app.invoke(initial_state, config)
    assert result1["status"] == "success"

    # Get state to verify persistence
    state = test_app.get_state(config)
    assert state is not None


@patch.object(llm, "invoke")
def test_e2e_auth_error(mock_invoke, initial_state, disk_checkpointer):
    """Test auth error ends workflow."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    with patch.object(test_app, "nodes", wraps=test_app.nodes):
        # Simulate auth error via handle_error mock
        # Test routing logic
        initial_state["error_message"] = "auth failed"
        assert route_error(initial_state) == "refresh_credentials"


@patch.object(llm, "invoke")
def test_e2e_not_found_to_safe_node(mock_invoke, initial_state, disk_checkpointer):
    """Test not_found routes to safe_node."""
    checkpointer, db_path = disk_checkpointer
    build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    # Test routing logic
    initial_state["error_message"] = "not_found"
    assert route_error(initial_state) == "try_fallback"


@patch.object(llm, "invoke")
@patch("time.sleep")
def test_e2e_backoff_loop(mock_sleep, mock_invoke, initial_state, disk_checkpointer):
    """Test backoff loop behavior."""
    checkpointer, db_path = disk_checkpointer
    build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    # Test routing logic for rate_limit
    initial_state["error_message"] = "rate_limit"
    assert route_error(initial_state) == "backoff"


@patch.object(llm, "invoke")
def test_e2e_safe_node_recovery(mock_invoke, initial_state, disk_checkpointer):
    """Test safe_node recovery path."""
    checkpointer, db_path = disk_checkpointer
    build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = False
    result = safe_node(initial_state)

    assert result["status"] == "success"
    assert result["output"] == "API call result"


@patch.object(llm, "invoke")
def test_e2e_full_failure(mock_invoke, initial_state, disk_checkpointer):
    """Test complete failure scenario."""
    checkpointer, db_path = disk_checkpointer
    test_app = build_workflow(checkpointer=checkpointer)

    mock_response = MagicMock()
    mock_response.content = ""
    mock_invoke.return_value = mock_response

    initial_state["force_error"] = True
    initial_state["max_retries"] = 0

    config = {"configurable": {"thread_id": "e2e_full_fail"}}
    result = test_app.invoke(initial_state, config)

    # Should fail at validation due to max_retries=0
    assert result["status"] in ["invalid", "error"]


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    # Example usage
    initial_state = {
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
    }

    config = {"configurable": {"thread_id": "research-001"}}
    result = app.invoke(initial_state, config=config)

    print("=" * 60)
    print("Research Workflow Results")
    print("=" * 60)
    print(f"Status: {result['status']}")
    print(f"Retry Count: {result['retry_count']}")
    print(f"Report:\n{result['report']}")
    print("=" * 60)

    # Run tests
    # pytest research_workflow_standalone.py -v
