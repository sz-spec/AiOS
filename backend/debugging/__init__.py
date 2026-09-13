"""
Debugging Module
================
Production debugging tools for LangGraph agents.

Available Tools:
- LangSmith tracing and analysis
- Fetch CLI integration
- Time-travel debugging
- Claude Code LSP integration

Usage:
    from debugging import DebugClient, debug_trace

    # Analyze traces
    debug = DebugClient()
    traces = debug.fetch_traces(limit=10)
    analysis = debug.analyze_trace(trace_id)

    # Decorate functions for tracing
    @debug_trace(name="my_operation")
    def my_function(state):
        return do_work(state)

CLI Tools:
    langsmith-fetch --trace-id <id> --output json

    ENABLE_LSP_TOOLS=1 claude-code query "tell me about issues"
"""

from .langsmith_debug import (
    DebugConfig,
    DebugClient,
    debug_trace,
    ClaudeCodeDebugger,
    generate_debug_report,
    LANGSMITH_AVAILABLE,
)

__all__ = [
    "DebugConfig",
    "DebugClient",
    "debug_trace",
    "ClaudeCodeDebugger",
    "generate_debug_report",
    "LANGSMITH_AVAILABLE",
]
