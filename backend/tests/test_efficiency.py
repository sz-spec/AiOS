"""
Tests for Efficiency Module
============================

Covers: assign_model fallback, error_middleware, retry_middleware,
        ContextManager, LLMCallReducer
"""

from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# assign_model (fallback when SmartRouter unavailable)
# ---------------------------------------------------------------------------


class TestAssignModel:
    def test_fallback_architect(self):
        from src.efficiency import assign_model

        with patch("src.efficiency.router", None):
            model = assign_model("architect", 5)
        assert "gpt" in model.lower()

    def test_fallback_coding(self):
        from src.efficiency import assign_model

        with patch("src.efficiency.router", None):
            model = assign_model("coding", 3)
        assert (
            "claude" in model.lower()
            or "sonnet" in model.lower()
            or "gpt" in model.lower()
        )

    def test_fallback_returns_string(self):
        from src.efficiency import assign_model

        with patch("src.efficiency.router", None):
            model = assign_model("reviewer", 5)
        assert isinstance(model, str)
        assert len(model) > 0

    def test_with_smart_router(self):
        from src.efficiency import assign_model

        mock_router = MagicMock()
        mock_router.get_model_for_role.return_value = "claude-opus"
        mock_config = MagicMock()
        mock_config.name = "Claude Opus"
        mock_config.model_id = "claude-opus-4"
        mock_router.get_model.return_value = mock_config
        mock_router.config.complexity_threshold = 9

        # Patch the module global that assign_model actually reads
        # (src.efficiency.router.router), not the re-exported name in the
        # package __init__ — those are distinct bindings.
        with patch("src.efficiency.router.router", mock_router):
            model = assign_model("coding", 10)
        assert model == "claude-opus"

    def test_assign_model_returns_model_name(self):
        """assign_model always returns a non-empty string."""
        from src.efficiency import assign_model

        model = assign_model("coding", 5)
        assert isinstance(model, str)
        assert len(model) > 0


# ---------------------------------------------------------------------------
# error_middleware
# ---------------------------------------------------------------------------


class TestErrorMiddleware:
    def test_success_sets_status(self):
        from src.efficiency import error_middleware

        @error_middleware
        def my_func(state):
            return {"output": "done"}

        state = {"retry_count": 0}
        result = my_func(state)
        assert result["status"] == "success"
        assert result["output"] == "done"

    def test_exception_sets_error_status(self):
        from src.efficiency import error_middleware

        @error_middleware
        def failing_func(state):
            raise ValueError("boom")

        state = {"retry_count": 0}
        result = failing_func(state)
        assert result["status"] == "error"
        assert result["retry_count"] == 1
        assert "boom" in result["error_message"]

    def test_exception_increments_retry(self):
        from src.efficiency import error_middleware

        @error_middleware
        def failing_func(state):
            raise RuntimeError("fail")

        state = {"retry_count": 2}
        result = failing_func(state)
        assert result["retry_count"] == 3


# ---------------------------------------------------------------------------
# retry_middleware
# ---------------------------------------------------------------------------


class TestRetryMiddleware:
    def test_first_try_success(self):
        from src.efficiency import retry_middleware

        @retry_middleware(max_retries=3)
        def my_func(state):
            return {"output": "ok"}

        state = {"retry_count": 0}
        result = my_func(state)
        assert result["status"] == "success"

    def test_retry_then_success(self):
        from src.efficiency import retry_middleware

        call_count = {"n": 0}

        @retry_middleware(max_retries=3)
        def flaky_func(state):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise ValueError("temporary failure")
            return {"output": "recovered"}

        with patch("time.sleep"):  # Skip actual sleep
            state = {"retry_count": 0}
            result = flaky_func(state)
        assert result["status"] == "success"

    def test_max_retries_exhausted(self):
        from src.efficiency import retry_middleware

        @retry_middleware(max_retries=2)
        def always_fails(state):
            raise ValueError("permanent failure")

        with patch("time.sleep"):
            state = {"retry_count": 0}
            result = always_fails(state)
        assert result["status"] == "error"
        assert "2 retries" in result["error_message"]


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_short_context_unchanged(self):
        from src.efficiency import ContextManager

        cm = ContextManager(max_tokens=10000)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = cm.optimize_context(messages)
        assert result == messages

    def test_empty_messages(self):
        from src.efficiency import ContextManager

        cm = ContextManager()
        result = cm.optimize_context([])
        assert result == []

    def test_long_context_summarized(self):
        from src.efficiency import ContextManager

        cm = ContextManager(max_tokens=50, window_size=2)
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "A" * 200},
            {"role": "assistant", "content": "B" * 200},
            {"role": "user", "content": "C" * 200},
            {"role": "assistant", "content": "D" * 200},
            {"role": "user", "content": "recent question"},
            {"role": "assistant", "content": "recent answer"},
        ]
        result = cm.optimize_context(messages)
        # Should keep system + summary + recent window
        assert len(result) < len(messages)
        # System message preserved
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"

    def test_system_messages_preserved(self):
        from src.efficiency import ContextManager

        cm = ContextManager(max_tokens=20, window_size=1)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "X" * 200},
            {"role": "user", "content": "recent"},
        ]
        result = cm.optimize_context(messages)
        roles = [m["role"] for m in result]
        assert "system" in roles

    def test_summarize_messages(self):
        from src.efficiency import ContextManager

        cm = ContextManager()
        messages = [
            {"role": "user", "content": "Tell me about Python programming"},
            {"role": "assistant", "content": "Python is a language for..."},
        ]
        summary = cm._summarize_messages(messages)
        assert "2 messages" in summary

    def test_summarize_empty(self):
        from src.efficiency import ContextManager

        cm = ContextManager()
        assert cm._summarize_messages([]) == ""


# ---------------------------------------------------------------------------
# LLMCallReducer
# ---------------------------------------------------------------------------


class TestLLMCallReducer:
    def test_cache_hit(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "cached response"
        reducer = LLMCallReducer(mock_llm, local_fallback=False)

        # First call — cache miss
        result1 = reducer.invoke("test prompt")
        assert result1 == "cached response"
        assert mock_llm.invoke.call_count == 1

        # Second call — cache hit
        result2 = reducer.invoke("test prompt")
        assert result2 == "cached response"
        assert mock_llm.invoke.call_count == 1  # No additional call

    def test_cache_miss(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "response"
        reducer = LLMCallReducer(mock_llm, local_fallback=False)

        reducer.invoke("prompt A")
        reducer.invoke("prompt B")
        assert mock_llm.invoke.call_count == 2

    def test_cache_disabled(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "response"
        reducer = LLMCallReducer(mock_llm, cache_enabled=False, local_fallback=False)

        reducer.invoke("same prompt")
        reducer.invoke("same prompt")
        assert mock_llm.invoke.call_count == 2

    def test_local_fallback_hello(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        reducer = LLMCallReducer(mock_llm, local_fallback=True)

        result = reducer.invoke("hello there")
        assert "Hello" in result
        assert mock_llm.invoke.call_count == 0

    def test_local_fallback_thanks(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        reducer = LLMCallReducer(mock_llm, local_fallback=True)

        result = reducer.invoke("thanks for the help")
        assert "welcome" in result.lower()

    def test_get_stats(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = "resp"
        reducer = LLMCallReducer(mock_llm, local_fallback=False)

        reducer.invoke("complex question about architecture")  # llm call
        reducer.invoke("complex question about architecture")  # cache hit

        stats = reducer.get_stats()
        assert stats["calls"] == 1
        assert stats["cache_hits"] == 1

    def test_get_stats_with_local(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        reducer = LLMCallReducer(mock_llm, local_fallback=True)

        reducer.invoke("hello")  # local hit

        stats = reducer.get_stats()
        assert stats["local_hits"] == 1
        assert stats["calls"] == 0

    def test_stats_copy(self):
        from src.efficiency import LLMCallReducer

        mock_llm = MagicMock()
        reducer = LLMCallReducer(mock_llm)
        stats = reducer.get_stats()
        stats["calls"] = 999
        assert reducer.get_stats()["calls"] != 999


# ---------------------------------------------------------------------------
# create_initial_state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_create_initial_state(self):
        from src.efficiency import create_initial_state

        state = create_initial_state()
        assert state["status"] == "pending"
        assert state["retry_count"] == 0
        assert state["messages"] == []
        assert state["llm_calls"] == 0
        assert state["tokens_used"] == 0
        assert state["cache_hits"] == 0
