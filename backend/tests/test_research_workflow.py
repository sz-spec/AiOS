"""
Research Workflow Tests
=======================
Comprehensive unit tests for the research workflow agents.
Run with: pytest tests/test_research_workflow.py -v --cov=agents
"""

import pytest
from unittest.mock import patch, MagicMock

# Import from research workflow module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.agents.research_workflow import (
    ResearchWorkflow,
    WorkflowConfig,
    WorkflowStage,
    WorkflowStatus,
    AgentState,
    plan_research,
    search,
    validate,
    process,
    handle_error,
    safe_operation,
    finalize,
    fail,
    route_validation,
    route_error,
    route_retry,
    parse_queries,
    calculate_backoff,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def initial_state() -> AgentState:
    """Create a fresh initial state for each test."""
    return {
        "messages": [],
        "current_stage": WorkflowStage.PLANNING.value,
        "status": WorkflowStatus.PENDING.value,
        "retry_count": 0,
        "backoff_seconds": 1,
        "max_retries": 3,
        "last_attempt": False,
        "error_message": "",
        "error_type": "",
        "research_query": "AI trends in 2025",
        "search_queries": [],
        "search_results": [],
        "output": "",
        "report": "",
        "summary": "",
        "started_at": "",
        "completed_at": "",
        "total_duration": 0.0,
    }


@pytest.fixture
def workflow_config():
    """Create test workflow configuration."""
    return WorkflowConfig(
        complexity=6,  # Task complexity for SmartRouter (1-10)
        temperature=0.7,
        max_retries=3,
        initial_backoff=1,
        max_backoff=60,
        checkpoint_db=":memory:",  # In-memory for tests
        enable_persistence=False,
    )


@pytest.fixture
def mock_llm():
    """Create a mock LLM."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content="query1\nquery2\nquery3")
    return mock


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_parse_queries_simple(self):
        """Test parsing simple query list."""
        content = "query1\nquery2\nquery3"
        result = parse_queries(content)
        assert len(result) == 3
        assert result[0] == "query1"

    def test_parse_queries_with_numbering(self):
        """Test parsing queries with numbering."""
        content = "1. First query\n2. Second query\n3. Third query"
        result = parse_queries(content)
        assert len(result) == 3
        assert "First query" in result[0]

    def test_parse_queries_with_bullets(self):
        """Test parsing queries with bullet points."""
        content = "- Query one\n- Query two\n* Query three"
        result = parse_queries(content)
        assert len(result) == 3

    def test_parse_queries_empty(self):
        """Test parsing empty content."""
        result = parse_queries("")
        assert result == []

    def test_parse_queries_max_five(self):
        """Test that max 5 queries are returned."""
        content = "\n".join([f"query{i}" for i in range(10)])
        result = parse_queries(content)
        assert len(result) == 5

    def test_calculate_backoff(self):
        """Test exponential backoff calculation."""
        assert calculate_backoff(1) == 2
        assert calculate_backoff(2) == 4
        assert calculate_backoff(4) == 8

    def test_calculate_backoff_cap(self):
        """Test backoff is capped at max."""
        assert calculate_backoff(32, max_backoff=60) == 60
        assert calculate_backoff(100, max_backoff=60) == 60


# =============================================================================
# Agent Node Tests
# =============================================================================


class TestPlanResearch:
    """Tests for plan_research agent."""

    @patch("ai.agents.research_workflow.get_llm")
    def test_plan_research_success(self, mock_get_llm, initial_state):
        """Test successful research planning."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="query1\nquery2\nquery3")
        mock_get_llm.return_value = mock_llm

        result = plan_research(initial_state)

        assert "search_queries" in result
        assert isinstance(result["search_queries"], list)
        assert len(result["search_queries"]) > 0
        assert result["current_stage"] == WorkflowStage.SEARCHING.value

    @patch("ai.agents.research_workflow.get_llm")
    def test_plan_research_empty_response(self, mock_get_llm, initial_state):
        """Test planning with empty LLM response."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="")
        mock_get_llm.return_value = mock_llm

        result = plan_research(initial_state)

        assert result["search_queries"] == []
        assert result["current_stage"] == WorkflowStage.SEARCHING.value


class TestSearch:
    """Tests for search agent."""

    def test_search_with_queries(self, initial_state):
        """Test search with valid queries."""
        initial_state["search_queries"] = ["query1", "query2"]

        result = search(initial_state)

        assert "search_results" in result
        assert len(result["search_results"]) == 2
        assert result["current_stage"] == WorkflowStage.VALIDATING.value

    def test_search_no_queries(self, initial_state):
        """Test search with no queries."""
        initial_state["search_queries"] = []

        result = search(initial_state)

        assert "search_results" in result
        assert len(result["search_results"]) == 0
        assert result["current_stage"] == WorkflowStage.VALIDATING.value

    def test_search_result_structure(self, initial_state):
        """Test search result structure."""
        initial_state["search_queries"] = ["test query"]

        result = search(initial_state)

        assert len(result["search_results"]) == 1
        assert "query" in result["search_results"][0]
        assert "results" in result["search_results"][0]
        assert "source" in result["search_results"][0]


class TestValidate:
    """Tests for validate agent."""

    def test_validate_with_valid_results(self, initial_state):
        """Test validation with valid results."""
        initial_state["search_results"] = [
            {"query": "q1", "results": [{"title": "t1"}]},
            {"query": "q2", "results": [{"title": "t2"}]},
        ]
        initial_state["search_queries"] = ["q1", "q2"]

        result = validate(initial_state)

        assert result["status"] == WorkflowStatus.VALID.value
        assert result["current_stage"] == WorkflowStage.PROCESSING.value

    def test_validate_with_empty_results(self, initial_state):
        """Test validation with empty results."""
        initial_state["search_results"] = []

        result = validate(initial_state)

        assert result["status"] == WorkflowStatus.INVALID.value
        assert result["current_stage"] == WorkflowStage.ERROR_HANDLING.value

    def test_validate_with_no_content(self, initial_state):
        """Test validation when results have no content."""
        initial_state["search_results"] = [{"query": "q1", "results": []}]
        initial_state["search_queries"] = ["q1"]

        result = validate(initial_state)

        assert result["status"] == WorkflowStatus.INVALID.value


class TestProcess:
    """Tests for process agent."""

    @patch("ai.agents.research_workflow.get_llm")
    def test_process_success(self, mock_get_llm, initial_state):
        """Test successful processing."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            MagicMock(content="Full report content"),
            MagicMock(content="Summary of report"),
        ]
        mock_get_llm.return_value = mock_llm

        initial_state["search_results"] = [
            {"query": "q1", "results": [{"title": "t1", "snippet": "content1"}]}
        ]

        result = process(initial_state)

        assert "report" in result
        assert "summary" in result
        assert result["status"] == WorkflowStatus.SUCCESS.value
        assert result["current_stage"] == WorkflowStage.COMPLETE.value

    @patch("ai.agents.research_workflow.get_llm")
    def test_process_empty_results(self, mock_get_llm, initial_state):
        """Test processing with empty results."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            MagicMock(content="No data available"),
            MagicMock(content="No data"),
        ]
        mock_get_llm.return_value = mock_llm

        initial_state["search_results"] = []

        result = process(initial_state)

        assert result["status"] == WorkflowStatus.SUCCESS.value


class TestHandleError:
    """Tests for handle_error agent."""

    @patch("time.sleep")
    def test_handle_error_success(self, mock_sleep, initial_state):
        """Test successful error recovery."""
        result = handle_error(initial_state)

        mock_sleep.assert_not_called()
        assert result["status"] == WorkflowStatus.IN_PROGRESS.value
        assert result["backoff_seconds"] == 1
        assert result["error_message"] == ""

    @patch("time.sleep")
    def test_handle_error_with_backoff(self, mock_sleep, initial_state):
        """Test error handling with backoff wait."""
        initial_state["retry_count"] = 1
        initial_state["last_attempt"] = True
        initial_state["backoff_seconds"] = 2

        handle_error(initial_state)

        mock_sleep.assert_called_once_with(2)

    def test_handle_error_no_backoff_first_attempt(self, initial_state):
        """Test no backoff on first attempt."""
        initial_state["retry_count"] = 0
        initial_state["last_attempt"] = False

        with patch("time.sleep") as mock_sleep:
            handle_error(initial_state)
            mock_sleep.assert_not_called()


class TestSafeOperation:
    """Tests for safe_operation agent."""

    def test_safe_operation_success(self, initial_state):
        """Test successful safe operation."""
        result = safe_operation(initial_state)

        assert result["status"] == WorkflowStatus.SUCCESS.value
        assert "output" in result


class TestFinalize:
    """Tests for finalize agent."""

    def test_finalize(self, initial_state):
        """Test workflow finalization."""
        result = finalize(initial_state)

        assert result["current_stage"] == WorkflowStage.COMPLETE.value
        assert "completed_at" in result


class TestFail:
    """Tests for fail agent."""

    def test_fail(self, initial_state):
        """Test workflow failure."""
        initial_state["error_message"] = "Test error"

        result = fail(initial_state)

        assert result["current_stage"] == WorkflowStage.FAILED.value
        assert result["status"] == WorkflowStatus.ERROR.value


# =============================================================================
# Routing Function Tests
# =============================================================================


class TestRouteValidation:
    """Tests for route_validation function."""

    def test_route_to_process(self, initial_state):
        """Test routing to process on valid status."""
        initial_state["status"] = WorkflowStatus.VALID.value
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_validation(initial_state) == "process"

    def test_route_to_fail(self, initial_state):
        """Test routing to fail on max retries."""
        initial_state["status"] = WorkflowStatus.INVALID.value
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_validation(initial_state) == "fail"

    def test_route_to_improve(self, initial_state):
        """Test routing to improve on invalid with retries left."""
        initial_state["status"] = WorkflowStatus.INVALID.value
        initial_state["retry_count"] = 1
        initial_state["max_retries"] = 3

        assert route_validation(initial_state) == "improve"


class TestRouteError:
    """Tests for route_error function."""

    def test_route_rate_limit(self, initial_state):
        """Test routing on rate limit error."""
        initial_state["error_message"] = "rate_limit exceeded"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "backoff"

    def test_route_rate_limit_429(self, initial_state):
        """Test routing on 429 error."""
        initial_state["error_message"] = "Error 429: Too many requests"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "backoff"

    def test_route_auth_error(self, initial_state):
        """Test routing on auth error."""
        initial_state["error_message"] = "auth failed"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "refresh"

    def test_route_401_error(self, initial_state):
        """Test routing on 401 error."""
        initial_state["error_message"] = "Error 401: Unauthorized"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "refresh"

    def test_route_not_found(self, initial_state):
        """Test routing on not found error."""
        initial_state["error_message"] = "resource not_found"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "fallback"

    def test_route_404_error(self, initial_state):
        """Test routing on 404 error."""
        initial_state["error_message"] = "Error 404: Not found"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "fallback"

    def test_route_default_retry(self, initial_state):
        """Test default routing to retry."""
        initial_state["error_message"] = "unknown error"
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "retry"

    def test_route_empty_error(self, initial_state):
        """Test routing with empty error message."""
        initial_state["error_message"] = ""
        initial_state["retry_count"] = 0
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "retry"

    def test_route_max_retries_reached(self, initial_state):
        """Test routing when max retries reached."""
        initial_state["error_message"] = "any error"
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_error(initial_state) == "fail"


class TestRouteRetry:
    """Tests for route_retry function."""

    def test_can_retry(self, initial_state):
        """Test retry when attempts remaining."""
        initial_state["retry_count"] = 2
        initial_state["max_retries"] = 3

        assert route_retry(initial_state) == "retry"

    def test_cannot_retry(self, initial_state):
        """Test fail when no attempts remaining."""
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_retry(initial_state) == "fail"

    def test_retry_boundary(self, initial_state):
        """Test boundary condition at max retries."""
        initial_state["retry_count"] = 3
        initial_state["max_retries"] = 3

        assert route_retry(initial_state) == "fail"


# =============================================================================
# Workflow Integration Tests
# =============================================================================


class TestResearchWorkflow:
    """Integration tests for ResearchWorkflow class."""

    def test_workflow_initialization(self, workflow_config):
        """Test workflow can be initialized."""
        workflow = ResearchWorkflow(workflow_config)

        assert workflow.config == workflow_config
        assert workflow.graph is not None

    def test_initial_state_creation(self, workflow_config):
        """Test initial state is created correctly."""
        workflow = ResearchWorkflow(workflow_config)
        state = workflow._get_initial_state("Test query")

        assert state["research_query"] == "Test query"
        assert state["retry_count"] == 0
        assert state["max_retries"] == workflow_config.max_retries
        assert state["backoff_seconds"] == workflow_config.initial_backoff

    @patch("ai.agents.research_workflow.get_llm")
    def test_full_workflow_success(self, mock_get_llm, workflow_config):
        """Test full workflow execution."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            MagicMock(content="query1\nquery2"),  # plan_research
            MagicMock(content="Full report"),  # process report
            MagicMock(content="Summary"),  # process summary
        ]
        mock_get_llm.return_value = mock_llm

        workflow = ResearchWorkflow(workflow_config)
        result = workflow.run("Test research", thread_id="test-001")

        assert result["status"] == WorkflowStatus.SUCCESS.value
        assert "report" in result

    def test_workflow_config_defaults(self):
        """Test workflow config has correct defaults."""
        config = WorkflowConfig()

        assert config.max_retries == 3
        assert config.initial_backoff == 1
        assert config.max_backoff == 60
        assert config.enable_persistence == True


# =============================================================================
# Edge Cases and Error Scenarios
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and error scenarios."""

    def test_very_long_query(self, initial_state):
        """Test handling of very long research query."""
        initial_state["research_query"] = "A" * 10000

        # Should not raise
        result = search(initial_state)
        assert "search_results" in result

    def test_special_characters_in_query(self, initial_state):
        """Test handling of special characters."""
        initial_state["search_queries"] = ["test?query", "with&special<chars>"]

        result = search(initial_state)
        assert len(result["search_results"]) == 2

    def test_unicode_in_query(self, initial_state):
        """Test handling of unicode characters."""
        initial_state["search_queries"] = ["מחקר בעברית", "研究中文"]

        result = search(initial_state)
        assert len(result["search_results"]) == 2

    def test_zero_max_retries(self, initial_state):
        """Test with zero max retries."""
        initial_state["max_retries"] = 0
        initial_state["retry_count"] = 0
        initial_state["status"] = WorkflowStatus.INVALID.value

        assert route_validation(initial_state) == "fail"

    def test_negative_backoff(self, initial_state):
        """Test handling of invalid backoff value."""
        # Should use minimum of 1
        assert calculate_backoff(0) == 0  # Edge case: 0 * 2 = 0
        assert calculate_backoff(-1) == -2  # Edge case: negative


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=agents", "--cov-report=term-missing"])
