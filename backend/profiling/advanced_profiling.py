"""
Advanced Langfuse Profiling
===========================
Production-grade observability for LangGraph agents with:
- Distributed Tracing with custom trace IDs
- Automatic and manual scoring
- Context propagation (user_id, session_id, tags)
- Custom spans and decorators
- Proper flushing for serverless environments
- Agent graph visualization support

Installation:
    pip install langfuse langchain langchain_openai langgraph python-dotenv

Environment Variables:
    LANGFUSE_SECRET_KEY="sk-lf-..."
    LANGFUSE_PUBLIC_KEY="pk-lf-..."
    LANGFUSE_BASE_URL="https://cloud.langfuse.com"

Usage:
    from profiling import AdvancedProfiler, ProfiledWorkflow

    # Quick usage
    profiler = AdvancedProfiler()
    with profiler.trace("my-operation", user_id="user-123"):
        result = app.invoke(state, profiler.get_config("thread-1"))
        profiler.score("accuracy", 0.95)
"""

import os
import time
import uuid
import hashlib
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from functools import wraps
from contextlib import contextmanager
import atexit

# Langfuse imports with fallback
try:
    from langfuse import Langfuse
    from langfuse.callback import CallbackHandler
    from langfuse.decorators import observe, langfuse_context

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    Langfuse = None
    CallbackHandler = None
    observe = lambda *args, **kwargs: lambda f: f
    langfuse_context = None

# Load environment
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class AdvancedLangfuseConfig:
    """Advanced configuration for Langfuse profiling."""

    secret_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    public_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    base_url: str = field(
        default_factory=lambda: os.getenv(
            "LANGFUSE_BASE_URL", "https://cloud.langfuse.com"
        )
    )
    enabled: bool = True
    debug: bool = False
    auto_flush: bool = True
    flush_at_exit: bool = True
    sample_rate: float = 1.0  # 1.0 = 100% of traces

    def is_configured(self) -> bool:
        return bool(self.secret_key and self.public_key)


# =============================================================================
# Advanced Profiler
# =============================================================================


class AdvancedProfiler:
    """
    Advanced Langfuse profiler with full feature support.

    Features:
    - Distributed tracing with custom trace IDs
    - Automatic scoring based on results
    - Context propagation (user, session, tags)
    - Custom spans for fine-grained profiling
    - Proper flushing for serverless

    Usage:
        profiler = AdvancedProfiler()

        # Method 1: Context manager
        with profiler.trace("operation", user_id="user-123"):
            result = do_something()
            profiler.score("quality", 0.95)

        # Method 2: Manual
        trace_id = profiler.start_trace("operation")
        result = do_something()
        profiler.end_trace(trace_id)

        # Method 3: With LangGraph
        config = profiler.get_config("thread-1", user_id="user-123")
        result = app.invoke(state, config)
    """

    _instance: Optional["AdvancedProfiler"] = None

    def __new__(cls, config: AdvancedLangfuseConfig = None):
        """Singleton pattern for shared profiler instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: AdvancedLangfuseConfig = None):
        if self._initialized:
            return

        self.config = config or AdvancedLangfuseConfig()
        self._client: Optional[Langfuse] = None
        self._handler: Optional[CallbackHandler] = None
        self._current_trace_id: Optional[str] = None
        self._current_span_stack: List[Any] = []
        self._traces: Dict[str, Dict[str, Any]] = {}

        self._initialize_client()
        self._initialized = True

    def _initialize_client(self):
        """Initialize Langfuse client."""
        if not LANGFUSE_AVAILABLE:
            if self.config.debug:
                print("⚠️ Langfuse not installed")
            return

        if not self.config.is_configured():
            if self.config.debug:
                print("⚠️ Langfuse not configured (missing keys)")
            return

        try:
            self._client = Langfuse(
                secret_key=self.config.secret_key,
                public_key=self.config.public_key,
                host=self.config.base_url,
            )

            if self.config.flush_at_exit:
                atexit.register(self.shutdown)

            if self.config.debug:
                print(f"✅ Langfuse initialized: {self.config.base_url}")

        except Exception as e:
            if self.config.debug:
                print(f"❌ Langfuse init failed: {e}")

    @property
    def client(self) -> Optional[Langfuse]:
        """Get Langfuse client."""
        return self._client

    @property
    def is_enabled(self) -> bool:
        """Check if profiling is enabled and configured."""
        return LANGFUSE_AVAILABLE and self.config.enabled and self._client is not None

    # =========================================================================
    # Trace ID Generation
    # =========================================================================

    def create_trace_id(self, seed: str = None) -> str:
        """
        Create a deterministic or random trace ID.

        Args:
            seed: Optional seed for deterministic ID (useful for distributed tracing)

        Returns:
            Trace ID string
        """
        if seed:
            # Deterministic ID based on seed
            hash_input = f"langfuse-{seed}".encode()
            return hashlib.sha256(hash_input).hexdigest()[:32]
        else:
            # Random UUID
            return str(uuid.uuid4())

    # =========================================================================
    # Distributed Tracing
    # =========================================================================

    @contextmanager
    def trace(
        self,
        name: str,
        trace_id: str = None,
        user_id: str = None,
        session_id: str = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        input_data: Any = None,
    ):
        """
        Context manager for distributed tracing.

        Args:
            name: Trace name
            trace_id: Custom trace ID (for distributed tracing)
            user_id: User identifier
            session_id: Session identifier
            tags: List of tags
            metadata: Additional metadata
            input_data: Input data to record

        Usage:
            with profiler.trace("my-operation", user_id="user-123"):
                result = do_work()
                profiler.score("quality", 0.95)
        """
        if not self.is_enabled:
            yield None
            return

        # Generate or use provided trace ID
        actual_trace_id = trace_id or self.create_trace_id()
        self._current_trace_id = actual_trace_id

        # Store trace info
        self._traces[actual_trace_id] = {
            "name": name,
            "start_time": time.time(),
            "user_id": user_id,
            "session_id": session_id,
            "tags": tags or [],
            "metadata": metadata or {},
            "scores": [],
            "spans": [],
        }

        try:
            # Start observation span
            with self._client.start_as_current_observation(
                as_type="span", name=name, trace_context={"trace_id": actual_trace_id}
            ) as span:
                # Update with metadata
                if user_id:
                    span.update(user_id=user_id)
                if session_id:
                    span.update(session_id=session_id)
                if tags:
                    span.update(tags=tags)
                if metadata:
                    span.update(metadata=metadata)
                if input_data:
                    span.update(input=input_data)

                self._current_span_stack.append(span)

                yield actual_trace_id

        finally:
            # Record end time
            self._traces[actual_trace_id]["end_time"] = time.time()
            self._traces[actual_trace_id]["duration"] = (
                self._traces[actual_trace_id]["end_time"]
                - self._traces[actual_trace_id]["start_time"]
            )

            if self._current_span_stack:
                self._current_span_stack.pop()

            self._current_trace_id = None

            if self.config.auto_flush:
                self.flush()

    def start_trace(
        self,
        name: str,
        seed: str = None,
        user_id: str = None,
        session_id: str = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """
        Manually start a trace.

        Args:
            name: Trace name
            seed: Seed for deterministic trace ID
            user_id: User identifier
            session_id: Session identifier
            tags: List of tags
            metadata: Additional metadata

        Returns:
            Trace ID
        """
        if not self.is_enabled:
            return self.create_trace_id(seed)

        trace_id = self.create_trace_id(seed)

        trace = self._client.trace(
            id=trace_id,
            name=name,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            metadata=metadata or {},
        )

        self._current_trace_id = trace_id
        self._traces[trace_id] = {
            "name": name,
            "start_time": time.time(),
            "trace_obj": trace,
        }

        return trace_id

    def end_trace(
        self, trace_id: str = None, output_data: Any = None, status: str = None
    ):
        """
        Manually end a trace.

        Args:
            trace_id: Trace ID (defaults to current)
            output_data: Output data to record
            status: Final status
        """
        trace_id = trace_id or self._current_trace_id

        if not trace_id or trace_id not in self._traces:
            return

        if self.is_enabled and "trace_obj" in self._traces[trace_id]:
            trace = self._traces[trace_id]["trace_obj"]
            if output_data:
                trace.update(output=output_data)
            if status:
                trace.update(metadata={"status": status})

        self._traces[trace_id]["end_time"] = time.time()

        if self._current_trace_id == trace_id:
            self._current_trace_id = None

        if self.config.auto_flush:
            self.flush()

    # =========================================================================
    # Custom Spans
    # =========================================================================

    @contextmanager
    def span(
        self,
        name: str,
        span_type: str = "span",
        input_data: Any = None,
        metadata: Dict[str, Any] = None,
    ):
        """
        Create a custom span within the current trace.

        Args:
            name: Span name
            span_type: Type of span ("span", "generation", "event")
            input_data: Input data
            metadata: Additional metadata

        Usage:
            with profiler.trace("main"):
                with profiler.span("sub-operation"):
                    result = do_work()
        """
        if not self.is_enabled:
            yield None
            return

        start_time = time.time()

        try:
            with self._client.start_as_current_observation(
                as_type=span_type, name=name, input=input_data, metadata=metadata or {}
            ) as span_obj:
                yield span_obj

        finally:
            elapsed = time.time() - start_time

            if self._current_trace_id and self._current_trace_id in self._traces:
                self._traces[self._current_trace_id]["spans"].append(
                    {"name": name, "duration": elapsed}
                )

    # =========================================================================
    # Scoring
    # =========================================================================

    def score(
        self,
        name: str,
        value: float,
        trace_id: str = None,
        comment: str = None,
        data_type: str = "NUMERIC",
    ) -> bool:
        """
        Add a score to a trace.

        Args:
            name: Score name (e.g., "accuracy", "latency", "quality")
            value: Score value (0-1 for normalized, any for numeric)
            trace_id: Trace ID (defaults to current)
            comment: Optional comment
            data_type: "NUMERIC" or "CATEGORICAL"

        Returns:
            True if score was recorded
        """
        if not self.is_enabled:
            return False

        trace_id = trace_id or self._current_trace_id

        if not trace_id:
            if self.config.debug:
                print("⚠️ No active trace for scoring")
            return False

        try:
            self._client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
                data_type=data_type,
            )

            # Track locally
            if trace_id in self._traces:
                self._traces[trace_id].setdefault("scores", []).append(
                    {"name": name, "value": value, "comment": comment}
                )

            return True

        except Exception as e:
            if self.config.debug:
                print(f"⚠️ Score failed: {e}")
            return False

    def score_current_trace(self, name: str, value: float, comment: str = None) -> bool:
        """Convenience method to score the current trace."""
        return self.score(name, value, comment=comment)

    def auto_score(
        self, result: Dict[str, Any], trace_id: str = None
    ) -> Dict[str, float]:
        """
        Automatically generate scores based on result.

        Args:
            result: Workflow result with status, timing, etc.
            trace_id: Trace ID (defaults to current)

        Returns:
            Dictionary of scores generated
        """
        scores = {}
        trace_id = trace_id or self._current_trace_id

        # Success score
        if "status" in result:
            success = 1.0 if result["status"] == "success" else 0.0
            self.score("success", success, trace_id)
            scores["success"] = success

        # Latency score (lower is better)
        if "_profiling" in result and "total_time" in result["_profiling"]:
            total_time = result["_profiling"]["total_time"]
            latency_score = max(0, 1 - (total_time / 30))  # 30s = 0 score
            self.score("latency", latency_score, trace_id)
            scores["latency"] = latency_score

        # Retry score (fewer is better)
        if "retry_count" in result:
            retry_score = max(0, 1 - (result["retry_count"] / 5))
            self.score("retry_efficiency", retry_score, trace_id)
            scores["retry_efficiency"] = retry_score

        # Completeness score
        if "search_results" in result and "search_queries" in result:
            queries = len(result.get("search_queries", []))
            results = len(result.get("search_results", []))
            completeness = results / queries if queries > 0 else 0
            self.score("completeness", completeness, trace_id)
            scores["completeness"] = completeness

        return scores

    # =========================================================================
    # LangGraph Integration
    # =========================================================================

    def get_handler(
        self,
        trace_name: str = None,
        user_id: str = None,
        session_id: str = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[CallbackHandler]:
        """
        Get a CallbackHandler for LangChain/LangGraph.

        Args:
            trace_name: Name for this trace
            user_id: User identifier
            session_id: Session identifier
            tags: List of tags
            metadata: Additional metadata

        Returns:
            CallbackHandler or None
        """
        if not self.is_enabled:
            return None

        try:
            handler = CallbackHandler(
                trace_name=trace_name,
                user_id=user_id,
                session_id=session_id,
                tags=tags,
                metadata=metadata or {},
            )
            self._handler = handler
            return handler

        except Exception as e:
            if self.config.debug:
                print(f"⚠️ Handler creation failed: {e}")
            return None

    def get_config(
        self,
        thread_id: str,
        user_id: str = None,
        session_id: str = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Get a complete config dict for LangGraph invoke.

        Args:
            thread_id: Thread ID for checkpointing
            user_id: User identifier (for Langfuse)
            session_id: Session identifier (for Langfuse)
            tags: Tags (for Langfuse)
            metadata: Additional metadata

        Returns:
            Config dict ready for app.invoke()

        Usage:
            config = profiler.get_config("thread-1", user_id="user-123")
            result = app.invoke(state, config)
        """
        config = {"configurable": {"thread_id": thread_id}}

        # Add Langfuse handler
        handler = self.get_handler(
            trace_name=thread_id,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            metadata=metadata,
        )

        if handler:
            config["callbacks"] = [handler]

        # Add Langfuse metadata to config
        if metadata:
            config["metadata"] = {
                "langfuse_user_id": user_id,
                "langfuse_session_id": session_id,
                "langfuse_tags": tags or [],
                **metadata,
            }

        return config

    @property
    def last_trace_id(self) -> Optional[str]:
        """Get the last trace ID from the handler."""
        if self._handler and hasattr(self._handler, "last_trace_id"):
            return self._handler.last_trace_id
        return self._current_trace_id

    # =========================================================================
    # Trace Updates
    # =========================================================================

    def update_current_trace(
        self,
        name: str = None,
        input_data: Any = None,
        output_data: Any = None,
        metadata: Dict[str, Any] = None,
    ):
        """
        Update the current trace with additional data.

        Args:
            name: New name
            input_data: Input data
            output_data: Output data
            metadata: Additional metadata
        """
        if not self.is_enabled or not self._current_trace_id:
            return

        try:
            if langfuse_context:
                if name:
                    langfuse_context.update_current_trace(name=name)
                if input_data:
                    langfuse_context.update_current_trace(input=input_data)
                if output_data:
                    langfuse_context.update_current_trace(output=output_data)
                if metadata:
                    langfuse_context.update_current_trace(metadata=metadata)

        except Exception as e:
            if self.config.debug:
                print(f"⚠️ Trace update failed: {e}")

    # =========================================================================
    # Flushing & Cleanup
    # =========================================================================

    def flush(self):
        """Flush all pending events to Langfuse."""
        if self._client:
            try:
                self._client.flush()
            except Exception as e:
                if self.config.debug:
                    print(f"⚠️ Flush failed: {e}")

    def shutdown(self):
        """Shutdown and flush all pending events."""
        if self._client:
            try:
                self._client.shutdown()
                if self.config.debug:
                    print("✅ Langfuse shutdown complete")
            except Exception:
                pass

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_trace_summary(self, trace_id: str = None) -> Dict[str, Any]:
        """
        Get summary of a trace.

        Args:
            trace_id: Trace ID (defaults to current)

        Returns:
            Summary dictionary
        """
        trace_id = trace_id or self._current_trace_id

        if not trace_id or trace_id not in self._traces:
            return {}

        trace_info = self._traces[trace_id]

        return {
            "trace_id": trace_id,
            "name": trace_info.get("name"),
            "duration": trace_info.get("duration"),
            "user_id": trace_info.get("user_id"),
            "session_id": trace_info.get("session_id"),
            "tags": trace_info.get("tags", []),
            "scores": trace_info.get("scores", []),
            "spans": trace_info.get("spans", []),
            "langfuse_enabled": self.is_enabled,
        }

    def print_summary(self, trace_id: str = None):
        """Print trace summary to console."""
        summary = self.get_trace_summary(trace_id)

        if not summary:
            print("No trace data available")
            return

        print("\n" + "=" * 60)
        print("📊 TRACE SUMMARY")
        print("=" * 60)
        print(f"Trace ID: {summary['trace_id']}")
        print(f"Name: {summary['name']}")
        print(f"Duration: {summary.get('duration', 0):.3f}s")
        print(f"User: {summary.get('user_id', 'N/A')}")
        print(f"Session: {summary.get('session_id', 'N/A')}")
        print(f"Tags: {', '.join(summary.get('tags', [])) or 'None'}")
        print(f"Langfuse: {'✅' if summary['langfuse_enabled'] else '❌'}")

        if summary.get("scores"):
            print("\n--- Scores ---")
            for s in summary["scores"]:
                print(f"  {s['name']}: {s['value']:.3f}")

        if summary.get("spans"):
            print("\n--- Spans ---")
            for span in summary["spans"]:
                print(f"  {span['name']}: {span['duration']:.3f}s")

        print("=" * 60 + "\n")


# =============================================================================
# Decorator for Custom Functions
# =============================================================================


def profiled(
    name: str = None, score_on_success: float = None, score_on_error: float = None
):
    """
    Decorator to profile custom functions.

    Args:
        name: Custom span name (defaults to function name)
        score_on_success: Score to record on success
        score_on_error: Score to record on error

    Usage:
        @profiled(name="my-function", score_on_success=1.0)
        def my_function(state):
            return {"result": "ok"}
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            span_name = name or func.__name__
            profiler = AdvancedProfiler()

            start_time = time.time()
            error = None
            result = None

            try:
                with profiler.span(span_name):
                    result = func(*args, **kwargs)

                if score_on_success is not None:
                    profiler.score(f"{span_name}_success", score_on_success)

                return result

            except Exception as e:
                error = e
                if score_on_error is not None:
                    profiler.score(f"{span_name}_error", score_on_error)
                raise

            finally:
                elapsed = time.time() - start_time
                if profiler.config.debug:
                    status = "✅" if error is None else "❌"
                    print(f"{status} {span_name}: {elapsed:.3f}s")

        return wrapper

    return decorator


# =============================================================================
# Observe Wrapper (for non-LangChain functions)
# =============================================================================

if LANGFUSE_AVAILABLE:
    # Re-export the observe decorator
    profiled_observe = observe
else:
    # Fallback no-op decorator
    def profiled_observe(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


# =============================================================================
# Convenience Functions
# =============================================================================


def get_profiler() -> AdvancedProfiler:
    """Get the singleton profiler instance."""
    return AdvancedProfiler()


def quick_profile(
    func: Callable, *args, trace_name: str = None, user_id: str = None, **kwargs
) -> Any:
    """
    Quick profile a function call.

    Usage:
        result = quick_profile(my_func, arg1, arg2, trace_name="my-trace")
    """
    profiler = get_profiler()

    with profiler.trace(trace_name or func.__name__, user_id=user_id):
        result = func(*args, **kwargs)

        # Auto-score if result is a dict
        if isinstance(result, dict):
            profiler.auto_score(result)

        return result


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "AdvancedProfiler",
    "AdvancedLangfuseConfig",
    "profiled",
    "profiled_observe",
    "get_profiler",
    "quick_profile",
    "LANGFUSE_AVAILABLE",
]
