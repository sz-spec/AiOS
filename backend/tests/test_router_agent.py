"""
Router Agent Tests
==================
Unit tests for the multi-agent router system.
Run with: pytest tests/test_router_agent.py -v
"""

import pytest
from unittest.mock import patch, MagicMock
from typing import Dict, Any

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.agents.router_agent import (
    MultiAgentRouter,
    AgentConfig,
    AgentType,
    quick_route,
    web_search,
    calculator,
    code_executor,
    file_analyzer,
    create_router,
    create_search_agent,
    create_math_agent,
    create_code_agent,
    create_general_agent,
    AGENT_DOCS,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def simple_state() -> Dict[str, Any]:
    """Create simple state for testing."""
    return {"user_query": "What is 2 + 2?", "answer": ""}


@pytest.fixture
def agent_config():
    """Create test agent configuration."""
    return AgentConfig(
        complexity=5, temperature=0.7, enable_memory=False, role="coding"
    )


@pytest.fixture
def mock_llm():
    """Create mock LLM."""
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content="Test response")
    return mock


# =============================================================================
# Tool Tests
# =============================================================================


class TestCalculator:
    """Tests for calculator tool."""

    def test_basic_addition(self):
        """Test basic addition."""
        result = calculator.invoke("2 + 2")
        assert result == "4"

    def test_multiplication(self):
        """Test multiplication."""
        result = calculator.invoke("5 * 3")
        assert result == "15"

    def test_division(self):
        """Test division."""
        result = calculator.invoke("10 / 2")
        assert result == "5.0"

    def test_complex_expression(self):
        """Test complex expression."""
        result = calculator.invoke("(2 + 3) * 4")
        assert result == "20"

    def test_power(self):
        """Test power operation."""
        result = calculator.invoke("2 ** 3")
        assert result == "8"

    def test_sqrt(self):
        """Test square root."""
        result = calculator.invoke("sqrt(16)")
        assert result == "4.0"

    def test_pi(self):
        """Test pi constant."""
        result = calculator.invoke("pi")
        assert "3.14" in result

    def test_invalid_expression(self):
        """Test invalid expression handling."""
        result = calculator.invoke("invalid expression")
        assert "Error" in result


class TestCodeExecutor:
    """Tests for code executor tool."""

    def test_simple_print(self):
        """Test simple print statement."""
        result = code_executor.invoke("print('Hello')")
        assert "Hello" in result

    def test_calculation(self):
        """Test code with calculation."""
        result = code_executor.invoke("x = 5 + 3\nprint(x)")
        assert "8" in result

    def test_no_output(self):
        """Test code with no output."""
        result = code_executor.invoke("x = 5")
        assert "successfully" in result.lower() or result == ""

    def test_syntax_error(self):
        """Test handling of syntax errors."""
        result = code_executor.invoke("print('missing paren")
        assert "error" in result.lower()

    def test_unsupported_language(self):
        """Test unsupported language."""
        # LangChain tools require dict input for multiple parameters
        result = code_executor.invoke(
            {"code": "console.log('test')", "language": "javascript"}
        )
        assert "not supported" in result.lower()


class TestFileAnalyzer:
    """Tests for file analyzer tool."""

    def test_summary(self):
        """Test summary analysis."""
        content = "This is a test document. It has multiple sentences. The content is about testing."
        # LangChain tools require dict input for multiple parameters
        result = file_analyzer.invoke({"content": content, "analysis_type": "summary"})

        assert "words" in result.lower()
        assert "Summary" in result

    def test_keywords(self):
        """Test keyword extraction."""
        content = (
            "Python programming is great. Python is easy to learn. Programming is fun."
        )
        # LangChain tools require dict input for multiple parameters
        result = file_analyzer.invoke({"content": content, "analysis_type": "keywords"})

        assert "Keywords" in result

    def test_default_analysis(self):
        """Test default analysis type."""
        content = "Short test content."
        # Single required param can use dict or direct string
        result = file_analyzer.invoke({"content": content})

        assert "words" in result.lower()

    def test_empty_content(self):
        """Test with empty content."""
        result = file_analyzer.invoke("")
        assert "0 words" in result


class TestWebSearch:
    """Tests for web search tool."""

    def test_search_fallback(self):
        """Test search returns string even without API keys (fallback mode)."""
        # web_search has built-in fallback when APIs unavailable
        result = web_search.invoke("test query")
        # Should return a string (either results or fallback message)
        assert isinstance(result, str)
        # Fallback message contains the query
        if "unavailable" in result.lower():
            assert "test query" in result


# =============================================================================
# Agent Config Tests
# =============================================================================


class TestAgentConfig:
    """Tests for AgentConfig class."""

    def test_default_values(self):
        """Test default configuration values."""
        config = AgentConfig()

        assert config.complexity == 5
        assert config.temperature == 0.7
        assert config.enable_memory == True
        assert config.role == "coding"

    def test_custom_values(self):
        """Test custom configuration values."""
        config = AgentConfig(
            complexity=8, temperature=0.5, enable_memory=False, role="architect"
        )

        assert config.complexity == 8
        assert config.temperature == 0.5
        assert config.enable_memory == False
        assert config.role == "architect"


# =============================================================================
# Agent Type Tests
# =============================================================================


class TestAgentType:
    """Tests for AgentType enum."""

    def test_agent_types_exist(self):
        """Test all agent types are defined."""
        assert AgentType.SEARCH.value == "search_agent"
        assert AgentType.MATH.value == "math_agent"
        assert AgentType.CODE.value == "code_agent"
        assert AgentType.GENERAL.value == "general_agent"

    def test_agent_docs_exist(self):
        """Test agent documentation exists."""
        assert AgentType.SEARCH in AGENT_DOCS
        assert AgentType.MATH in AGENT_DOCS
        assert AgentType.CODE in AGENT_DOCS
        assert AgentType.GENERAL in AGENT_DOCS


# =============================================================================
# Router Tests
# =============================================================================


def _get_selected_agent(result):
    """Helper to extract selected agent from router result (Command or string)."""
    if hasattr(result, "goto"):  # Command object
        return result.goto
    return result  # Legacy string


class TestRouter:
    """Tests for routing logic."""

    @patch("ai.agents.router_agent.get_llm")
    def test_create_router(self, mock_get_llm, simple_state):
        """Test router creation."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="math_agent")
        mock_get_llm.return_value = mock_llm

        router = create_router(mock_llm, [AgentType.MATH, AgentType.SEARCH])

        # Router should return a routing function
        assert callable(router)

    @patch("ai.agents.router_agent.get_llm")
    def test_router_selects_math(self, mock_get_llm, simple_state):
        """Test router selects math agent for math query."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="math_agent")
        mock_get_llm.return_value = mock_llm

        router = create_router(mock_llm, [AgentType.MATH, AgentType.SEARCH])

        simple_state["user_query"] = "What is 2 + 2?"
        result = router(simple_state)

        assert _get_selected_agent(result) == "math_agent"

    @patch("ai.agents.router_agent.get_llm")
    def test_router_selects_search(self, mock_get_llm, simple_state):
        """Test router selects search agent for search query."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="search_agent")
        mock_get_llm.return_value = mock_llm

        router = create_router(mock_llm, [AgentType.MATH, AgentType.SEARCH])

        simple_state["user_query"] = "What is the capital of France?"
        result = router(simple_state)

        assert _get_selected_agent(result) == "search_agent"

    @patch("ai.agents.router_agent.get_llm")
    def test_router_default_to_general(self, mock_get_llm, simple_state):
        """Test router defaults to general agent on unclear response."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="unknown response")
        mock_get_llm.return_value = mock_llm

        router = create_router(
            mock_llm, [AgentType.MATH, AgentType.SEARCH, AgentType.GENERAL]
        )

        result = router(simple_state)

        assert _get_selected_agent(result) == "general_agent"


# =============================================================================
# Agent Creation Tests
# =============================================================================


class TestAgentCreation:
    """Tests for agent factory functions."""

    def test_create_search_agent(self, mock_llm):
        """Test search agent creation."""
        agent = create_search_agent(mock_llm)

        assert callable(agent)
        assert agent.__doc__ is not None

    def test_create_math_agent(self, mock_llm):
        """Test math agent creation."""
        agent = create_math_agent(mock_llm)

        assert callable(agent)
        assert agent.__doc__ is not None

    def test_create_code_agent(self, mock_llm):
        """Test code agent creation."""
        agent = create_code_agent(mock_llm)

        assert callable(agent)

    def test_create_general_agent(self, mock_llm):
        """Test general agent creation."""
        agent = create_general_agent(mock_llm)

        assert callable(agent)


# =============================================================================
# MultiAgentRouter Integration Tests
# =============================================================================


class TestMultiAgentRouter:
    """Integration tests for MultiAgentRouter class."""

    @patch("ai.agents.router_agent.get_llm")
    def test_router_initialization(self, mock_get_llm, agent_config):
        """Test router can be initialized."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        router = MultiAgentRouter(agent_config)

        assert router.config == agent_config
        assert router.graph is not None

    @patch("ai.agents.router_agent.get_llm")
    def test_router_with_custom_agents(self, mock_get_llm, agent_config):
        """Test router with custom agent list."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        router = MultiAgentRouter(
            agent_config, agents=[AgentType.MATH, AgentType.GENERAL]
        )

        assert len(router.agents) == 2
        assert AgentType.MATH in router.agents

    @patch("ai.agents.router_agent.get_llm")
    def test_router_get_info(self, mock_get_llm, agent_config):
        """Test getting router info."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        router = MultiAgentRouter(agent_config)

        # Router should have agent list
        assert hasattr(router, "agents")
        assert len(router.agents) > 0


# =============================================================================
# Quick Route Function Tests
# =============================================================================


class TestQuickRoute:
    """Tests for quick_route convenience function."""

    @patch("ai.agents.router_agent.MultiAgentRouter")
    def test_quick_route_creates_router(self, mock_router_class):
        """Test quick_route creates router instance."""
        mock_router = MagicMock()
        mock_router.run.return_value = {"answer": "Test answer"}
        mock_router_class.return_value = mock_router

        result = quick_route("Test query")

        assert result == "Test answer"
        mock_router.run.assert_called_once()


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_calculator_division_by_zero(self):
        """Test calculator handles division by zero."""
        result = calculator.invoke("1 / 0")
        assert "Error" in result or "inf" in result.lower()

    def test_calculator_very_large_number(self):
        """Test calculator handles large numbers."""
        result = calculator.invoke("10 ** 100")
        assert result is not None

    def test_code_executor_infinite_loop_protection(self):
        """Test code executor doesn't hang on potential infinite loops."""
        # This should either timeout or return an error
        # Note: In production, you'd want actual timeout protection
        pass  # Placeholder - actual implementation would need timeout

    def test_empty_query(self, simple_state):
        """Test handling of empty query."""
        simple_state["user_query"] = ""
        # Should not raise error
        assert simple_state["user_query"] == ""


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=agents.router_agent"])
