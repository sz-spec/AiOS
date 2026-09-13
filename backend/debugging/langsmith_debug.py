"""
LangSmith Debugging Integration
===============================
Production debugging tools for LangGraph agents:
- LangSmith tracing and debugging
- Fetch CLI integration for trace analysis
- Polly AI assistant for prompt improvement
- Time-travel debugging
- Claude Code integration

From 2025 debugging best practices:
- LangSmith Fetch CLI: Pull traces to terminal
- Polly AI: Understand traces, improve prompts
- Claude Code: View LLM/tool calls

Installation:
    pip install langsmith langchain

CLI Tools:
    npm install -g langsmith-fetch  # or pip install langsmith
"""

import os
import subprocess
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

# LangSmith
try:
    from langsmith import Client as LangSmithClient
    from langsmith.run_helpers import traceable
    from langchain.callbacks.tracers import LangChainTracer

    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False
    traceable = lambda *args, **kwargs: lambda f: f

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class DebugConfig:
    """Configuration for debugging tools."""

    # LangSmith
    api_key: str = field(default_factory=lambda: os.getenv("LANGSMITH_API_KEY", ""))
    project_name: str = "langgraph-debug"

    # Tracing
    trace_all: bool = True
    sample_rate: float = 1.0

    # Fetch CLI
    fetch_output_format: str = "json"  # json, yaml, text

    # Claude Code
    enable_lsp: bool = True

    def is_configured(self) -> bool:
        return bool(self.api_key)


# =============================================================================
# LangSmith Debug Client
# =============================================================================


class DebugClient:
    """
    Enhanced LangSmith client for debugging.

    Features:
    - Fetch traces from LangSmith
    - Analyze runs and spans
    - Time-travel debugging
    - Integration with Polly AI

    Usage:
        debug = DebugClient()

        # Fetch recent traces
        traces = debug.fetch_traces(limit=10)

        # Analyze specific trace
        analysis = debug.analyze_trace(trace_id)

        # Get debugging suggestions
        suggestions = debug.get_suggestions(trace_id)
    """

    def __init__(self, config: DebugConfig = None):
        self.config = config or DebugConfig()
        self._client = None
        self._tracer = None

    @property
    def client(self) -> Optional[LangSmithClient]:
        """Get or create LangSmith client."""
        if not LANGSMITH_AVAILABLE:
            logger.warning("LangSmith not installed")
            return None

        if not self._client and self.config.is_configured():
            try:
                self._client = LangSmithClient(api_key=self.config.api_key)
            except Exception as e:
                logger.error(f"LangSmith client error: {e}")

        return self._client

    @property
    def tracer(self) -> Optional[LangChainTracer]:
        """Get tracer for callback."""
        if not LANGSMITH_AVAILABLE:
            return None

        if not self._tracer and self.config.is_configured():
            try:
                self._tracer = LangChainTracer(project_name=self.config.project_name)
            except Exception as e:
                logger.error(f"Tracer error: {e}")

        return self._tracer

    # =========================================================================
    # Fetch Traces
    # =========================================================================

    def fetch_traces(
        self,
        limit: int = 10,
        start_time: datetime = None,
        end_time: datetime = None,
        filter_by: Dict[str, Any] = None,
    ) -> List[Dict]:
        """
        Fetch traces from LangSmith.

        Args:
            limit: Maximum traces to fetch
            start_time: Start of time range
            end_time: End of time range
            filter_by: Additional filters

        Returns:
            List of trace dicts
        """
        if not self.client:
            return []

        try:
            # Default to last 24 hours
            if not start_time:
                start_time = datetime.now() - timedelta(hours=24)
            if not end_time:
                end_time = datetime.now()

            runs = self.client.list_runs(
                project_name=self.config.project_name,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            return [self._run_to_dict(run) for run in runs]

        except Exception as e:
            logger.error(f"Fetch traces error: {e}")
            return []

    def fetch_trace_by_id(self, trace_id: str) -> Optional[Dict]:
        """
        Fetch a specific trace by ID.

        Args:
            trace_id: Trace/run ID

        Returns:
            Trace dict or None
        """
        if not self.client:
            return None

        try:
            run = self.client.read_run(trace_id)
            return self._run_to_dict(run)
        except Exception as e:
            logger.error(f"Fetch trace error: {e}")
            return None

    def fetch_thread(self, thread_id: str) -> List[Dict]:
        """
        Fetch all traces for a thread.

        Args:
            thread_id: Thread ID from LangGraph

        Returns:
            List of traces for the thread
        """
        if not self.client:
            return []

        try:
            runs = self.client.list_runs(
                project_name=self.config.project_name,
                filter=f"metadata.thread_id == '{thread_id}'",
            )
            return [self._run_to_dict(run) for run in runs]
        except Exception as e:
            logger.error(f"Fetch thread error: {e}")
            return []

    def _run_to_dict(self, run) -> Dict:
        """Convert LangSmith run to dict."""
        return {
            "id": str(run.id),
            "name": run.name,
            "run_type": run.run_type,
            "status": run.status,
            "start_time": run.start_time.isoformat() if run.start_time else None,
            "end_time": run.end_time.isoformat() if run.end_time else None,
            "latency": run.total_time.total_seconds() if run.total_time else None,
            "total_tokens": run.total_tokens,
            "error": run.error,
            "inputs": run.inputs,
            "outputs": run.outputs,
            "metadata": run.extra.get("metadata", {}) if run.extra else {},
        }

    # =========================================================================
    # Analysis
    # =========================================================================

    def analyze_trace(self, trace_id: str) -> Dict[str, Any]:
        """
        Analyze a trace for debugging.

        Args:
            trace_id: Trace ID

        Returns:
            Analysis dict with:
            - summary: High-level summary
            - bottlenecks: Slow operations
            - errors: Error details
            - suggestions: Improvement suggestions
        """
        trace = self.fetch_trace_by_id(trace_id)

        if not trace:
            return {"error": "Trace not found"}

        analysis = {
            "trace_id": trace_id,
            "summary": self._summarize_trace(trace),
            "bottlenecks": self._find_bottlenecks(trace),
            "errors": self._extract_errors(trace),
            "token_usage": self._analyze_tokens(trace),
            "suggestions": self._generate_suggestions(trace),
        }

        return analysis

    def _summarize_trace(self, trace: Dict) -> Dict:
        """Create trace summary."""
        return {
            "name": trace.get("name"),
            "status": trace.get("status"),
            "latency_seconds": trace.get("latency"),
            "total_tokens": trace.get("total_tokens"),
            "has_error": bool(trace.get("error")),
        }

    def _find_bottlenecks(self, trace: Dict) -> List[Dict]:
        """Identify slow operations."""
        bottlenecks = []

        latency = trace.get("latency", 0)
        if latency > 5:
            bottlenecks.append(
                {
                    "type": "slow_execution",
                    "latency": latency,
                    "suggestion": "Consider optimizing prompts or using faster models",
                }
            )

        tokens = trace.get("total_tokens", 0)
        if tokens > 4000:
            bottlenecks.append(
                {
                    "type": "high_tokens",
                    "tokens": tokens,
                    "suggestion": "Consider using context compression",
                }
            )

        return bottlenecks

    def _extract_errors(self, trace: Dict) -> List[Dict]:
        """Extract error information."""
        errors = []

        if trace.get("error"):
            errors.append(
                {
                    "type": "execution_error",
                    "message": trace["error"],
                    "suggestion": "Check input validation and error handling",
                }
            )

        if trace.get("status") == "error":
            errors.append(
                {
                    "type": "status_error",
                    "status": trace["status"],
                    "suggestion": "Review workflow routing and state management",
                }
            )

        return errors

    def _analyze_tokens(self, trace: Dict) -> Dict:
        """Analyze token usage."""
        total = trace.get("total_tokens", 0)

        return {
            "total": total,
            "estimated_cost": total * 0.00002,  # Rough estimate
            "category": "high" if total > 4000 else "medium" if total > 1000 else "low",
        }

    def _generate_suggestions(self, trace: Dict) -> List[str]:
        """Generate debugging suggestions."""
        suggestions = []

        if trace.get("latency", 0) > 5:
            suggestions.append("Consider using streaming for better UX")

        if trace.get("total_tokens", 0) > 4000:
            suggestions.append("Use hybrid search to reduce context size")

        if trace.get("error"):
            suggestions.append("Add retry logic with exponential backoff")

        if not suggestions:
            suggestions.append("Trace looks healthy!")

        return suggestions

    # =========================================================================
    # CLI Integration
    # =========================================================================

    def fetch_cli(
        self, trace_id: str = None, thread_id: str = None, output_format: str = None
    ) -> str:
        """
        Use LangSmith Fetch CLI to pull traces.

        Args:
            trace_id: Specific trace ID
            thread_id: Thread ID
            output_format: Output format (json, yaml, text)

        Returns:
            CLI output or error message
        """
        format_arg = output_format or self.config.fetch_output_format

        cmd = ["langsmith-fetch"]

        if trace_id:
            cmd.extend(["--trace-id", trace_id])
        elif thread_id:
            cmd.extend(["--thread-id", thread_id])

        cmd.extend(["--output", format_arg])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"

        except FileNotFoundError:
            return (
                "langsmith-fetch CLI not installed. Run: npm install -g langsmith-fetch"
            )
        except subprocess.TimeoutExpired:
            return "CLI timeout"
        except Exception as e:
            return f"CLI error: {e}"

    # =========================================================================
    # Time-Travel Debugging
    # =========================================================================

    def get_trace_timeline(self, trace_id: str) -> List[Dict]:
        """
        Get timeline of events for time-travel debugging.

        Args:
            trace_id: Trace ID

        Returns:
            List of events with timestamps
        """
        trace = self.fetch_trace_by_id(trace_id)

        if not trace:
            return []

        timeline = []

        # Start event
        if trace.get("start_time"):
            timeline.append(
                {
                    "timestamp": trace["start_time"],
                    "event": "trace_start",
                    "name": trace.get("name"),
                    "inputs": trace.get("inputs"),
                }
            )

        # End event
        if trace.get("end_time"):
            timeline.append(
                {
                    "timestamp": trace["end_time"],
                    "event": "trace_end",
                    "status": trace.get("status"),
                    "outputs": trace.get("outputs"),
                    "error": trace.get("error"),
                }
            )

        return sorted(timeline, key=lambda x: x["timestamp"])

    def replay_trace(self, trace_id: str) -> Dict[str, Any]:
        """
        Replay a trace for debugging.

        Args:
            trace_id: Trace ID

        Returns:
            Replay information
        """
        trace = self.fetch_trace_by_id(trace_id)

        if not trace:
            return {"error": "Trace not found"}

        return {
            "original_trace": trace_id,
            "inputs": trace.get("inputs"),
            "expected_outputs": trace.get("outputs"),
            "can_replay": not bool(trace.get("error")),
            "replay_command": f"python -m langgraph.debug replay --trace-id {trace_id}",
        }


# =============================================================================
# Traceable Decorator
# =============================================================================


def debug_trace(
    name: str = None, metadata: Dict[str, Any] = None, run_type: str = "chain"
):
    """
    Decorator to trace a function with LangSmith.

    Usage:
        @debug_trace(name="my_operation")
        def my_function(state):
            return do_work(state)
    """

    def decorator(func: Callable) -> Callable:
        if LANGSMITH_AVAILABLE:
            return traceable(
                name=name or func.__name__, metadata=metadata, run_type=run_type
            )(func)
        return func

    return decorator


# =============================================================================
# Claude Code Integration
# =============================================================================


class ClaudeCodeDebugger:
    """
    Integration with Claude Code for debugging.

    Features:
    - LSP diagnostics for agent code
    - View LLM/tool calls
    - Debug workflows

    Usage:
        debugger = ClaudeCodeDebugger()
        diagnostics = debugger.get_diagnostics("agents/my_agent.py")
    """

    def __init__(self, enable_lsp: bool = True):
        self.enable_lsp = enable_lsp

    def get_diagnostics(self, file_path: str) -> List[Dict]:
        """
        Get LSP diagnostics for a file.

        Args:
            file_path: Path to Python file

        Returns:
            List of diagnostic issues
        """
        if not self.enable_lsp:
            return []

        # Set environment for LSP tools
        env = os.environ.copy()
        env["ENABLE_LSP_TOOLS"] = "1"

        try:
            result = subprocess.run(
                ["claude-code", "query", f"tell me about issues in {file_path}"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            if result.returncode == 0:
                return self._parse_diagnostics(result.stdout)
            return []

        except Exception:
            return []

    def find_definition(self, symbol: str) -> Optional[Dict]:
        """
        Find where a symbol is defined.

        Args:
            symbol: Symbol name (e.g., "MyClass.my_method")

        Returns:
            Location dict or None
        """
        env = os.environ.copy()
        env["ENABLE_LSP_TOOLS"] = "1"

        try:
            result = subprocess.run(
                ["claude-code", "query", f"tell me where {symbol} is defined"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            if result.returncode == 0:
                return {"definition": result.stdout.strip()}
            return None

        except Exception:
            return None

    def _parse_diagnostics(self, output: str) -> List[Dict]:
        """Parse diagnostics from output."""
        # Simplified parsing
        issues = []

        for line in output.split("\n"):
            if "error" in line.lower():
                issues.append({"severity": "error", "message": line})
            elif "warning" in line.lower():
                issues.append({"severity": "warning", "message": line})

        return issues


# =============================================================================
# Debug Report Generator
# =============================================================================


def generate_debug_report(
    trace_id: str = None, thread_id: str = None, output_path: str = None
) -> str:
    """
    Generate a comprehensive debug report.

    Args:
        trace_id: Specific trace ID
        thread_id: Thread ID
        output_path: Optional file path to save report

    Returns:
        Report as string
    """
    debug = DebugClient()

    report = []
    report.append("=" * 60)
    report.append("🔍 LANGSMITH DEBUG REPORT")
    report.append("=" * 60)
    report.append(f"Generated: {datetime.now().isoformat()}")
    report.append("")

    if trace_id:
        analysis = debug.analyze_trace(trace_id)

        report.append("--- Trace Analysis ---")
        report.append(f"Trace ID: {trace_id}")

        if "summary" in analysis:
            summary = analysis["summary"]
            report.append(f"Status: {summary.get('status')}")
            report.append(f"Latency: {summary.get('latency_seconds', 0):.2f}s")
            report.append(f"Tokens: {summary.get('total_tokens', 0)}")

        if analysis.get("errors"):
            report.append("\n--- Errors ---")
            for error in analysis["errors"]:
                report.append(f"  • {error['type']}: {error['message']}")

        if analysis.get("bottlenecks"):
            report.append("\n--- Bottlenecks ---")
            for bottleneck in analysis["bottlenecks"]:
                report.append(f"  • {bottleneck['type']}: {bottleneck['suggestion']}")

        if analysis.get("suggestions"):
            report.append("\n--- Suggestions ---")
            for suggestion in analysis["suggestions"]:
                report.append(f"  • {suggestion}")

    elif thread_id:
        traces = debug.fetch_thread(thread_id)
        report.append(f"--- Thread: {thread_id} ---")
        report.append(f"Total traces: {len(traces)}")

        for trace in traces[:5]:  # First 5
            report.append(f"\n  Trace: {trace.get('id')}")
            report.append(f"    Status: {trace.get('status')}")
            report.append(f"    Latency: {trace.get('latency', 0):.2f}s")

    else:
        # Recent traces
        traces = debug.fetch_traces(limit=5)
        report.append("--- Recent Traces ---")

        for trace in traces:
            report.append(f"\n  {trace.get('name')} ({trace.get('id')[:8]}...)")
            report.append(f"    Status: {trace.get('status')}")
            report.append(f"    Time: {trace.get('start_time')}")

    report.append("\n" + "=" * 60)

    report_str = "\n".join(report)

    if output_path:
        with open(output_path, "w") as f:
            f.write(report_str)

    return report_str


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "DebugConfig",
    "DebugClient",
    "debug_trace",
    "ClaudeCodeDebugger",
    "generate_debug_report",
    "LANGSMITH_AVAILABLE",
]
