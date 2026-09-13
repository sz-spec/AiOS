"""
Advanced Langfuse Profiling Examples
====================================
Demonstrates advanced profiling patterns for LangGraph agents:

1. Distributed Tracing with Custom Trace IDs
2. Scoring Traces for Performance Evaluation
3. Context Propagation (user_id, session_id, tags)
4. Custom Spans and Decorators
5. Flushing for Serverless Environments
6. Agent Graph Visualization

Setup:
    pip install langfuse langchain langchain_openai langgraph python-dotenv

Environment (.env):
    LANGFUSE_SECRET_KEY="sk-lf-..."
    LANGFUSE_PUBLIC_KEY="pk-lf-..."
    LANGFUSE_BASE_URL="https://cloud.langfuse.com"
    OPENAI_API_KEY="sk-proj-..."

Run:
    python examples/advanced_profiling_examples.py
"""

import os
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from profiling import (
    AdvancedProfiler,
    AdvancedLangfuseConfig,
    profiled,
    profiled_observe,
    quick_profile,
    ProfiledWorkflow,
    LANGFUSE_AVAILABLE,
)

# =============================================================================
# Example 1: Distributed Tracing with Custom Trace IDs
# =============================================================================


def example_distributed_tracing():
    """
    Distributed tracing allows tracking operations across multiple
    services/agents using a custom trace ID.
    """
    print("\n" + "=" * 60)
    print("📊 Example 1: Distributed Tracing")
    print("=" * 60)

    profiler = AdvancedProfiler()

    # Create deterministic trace ID from external request
    external_request_id = "agent_run_12345"
    trace_id = profiler.create_trace_id(seed=external_request_id)
    print(f"Generated trace ID: {trace_id}")

    # Use the trace ID across operations
    with profiler.trace(
        name="distributed_agent_execution",
        trace_id=trace_id,
        user_id="user_456",
        metadata={"source": "external_api"},
    ) as tid:
        print(f"Active trace: {tid}")

        # Simulate distributed work
        time.sleep(0.1)

        # Create child span for sub-operation
        with profiler.span("sub_operation_1"):
            time.sleep(0.05)

        with profiler.span("sub_operation_2"):
            time.sleep(0.03)

    # Get summary
    profiler.print_summary(trace_id)

    return trace_id


# =============================================================================
# Example 2: Scoring Traces for Performance Evaluation
# =============================================================================


def example_scoring():
    """
    Add scores to traces for quality measurement and evaluation.
    """
    print("\n" + "=" * 60)
    print("📊 Example 2: Trace Scoring")
    print("=" * 60)

    profiler = AdvancedProfiler()

    with profiler.trace("scored_operation") as trace_id:
        # Simulate work
        result = {"status": "success", "accuracy": 0.95, "latency_ms": 150}

        # Manual scores
        profiler.score("accuracy", result["accuracy"])
        profiler.score("latency", max(0, 1 - result["latency_ms"] / 1000))

        # Score with comment
        profiler.score(
            name="quality",
            value=0.92,
            comment="High quality response with minor issues",
        )

        print(f"✅ Added scores to trace: {trace_id}")

    # Auto-score from result dict
    mock_result = {
        "status": "success",
        "retry_count": 1,
        "search_queries": ["q1", "q2", "q3"],
        "search_results": [{"r": 1}, {"r": 2}, {"r": 3}],
        "_profiling": {"total_time": 2.5},
    }

    with profiler.trace("auto_scored_operation") as trace_id:
        scores = profiler.auto_score(mock_result)
        print(f"🤖 Auto-generated scores: {scores}")

    profiler.print_summary()


# =============================================================================
# Example 3: Context Propagation
# =============================================================================


def example_context_propagation():
    """
    Pass user_id, session_id, and tags for filtering in dashboard.
    """
    print("\n" + "=" * 60)
    print("📊 Example 3: Context Propagation")
    print("=" * 60)

    profiler = AdvancedProfiler()

    # Full context
    with profiler.trace(
        name="contextualized_operation",
        user_id="user_789",
        session_id="session_abc123",
        tags=["production", "v2", "high_priority"],
        metadata={
            "environment": "production",
            "version": "2.1.0",
            "region": "us-east-1",
        },
        input_data={"query": "What are AI trends?"},
    ) as trace_id:

        # Simulate work
        time.sleep(0.1)

        # Update trace with output
        profiler.update_current_trace(
            output_data={"answer": "AI trends include...", "tokens": 150}
        )

        print(f"✅ Trace with full context: {trace_id}")

    # Get LangGraph config with context
    config = profiler.get_config(
        thread_id="thread-001",
        user_id="user_789",
        session_id="session_abc123",
        tags=["agent_v2"],
        metadata={"priority": "high"},
    )

    print(f"📋 LangGraph config: {list(config.keys())}")
    print(f"   callbacks: {len(config.get('callbacks', []))} handler(s)")

    profiler.print_summary()


# =============================================================================
# Example 4: Custom Spans and Decorators
# =============================================================================


def example_spans_and_decorators():
    """
    Use custom spans for fine-grained profiling and decorators for functions.
    """
    print("\n" + "=" * 60)
    print("📊 Example 4: Custom Spans & Decorators")
    print("=" * 60)

    profiler = AdvancedProfiler()

    # Using @profiled decorator
    @profiled(name="decorated_function", score_on_success=1.0, score_on_error=0.0)
    def process_data(data):
        time.sleep(0.05)
        return {"processed": len(data)}

    # Using @profiled_observe (Langfuse native)
    @profiled_observe(name="observed_function")
    def analyze_results(results):
        time.sleep(0.03)
        return {"analysis": "complete"}

    with profiler.trace("decorated_operations"):
        # Call decorated functions
        result1 = process_data([1, 2, 3, 4, 5])
        print(f"Process result: {result1}")

        result2 = analyze_results(result1)
        print(f"Analysis result: {result2}")

        # Nested spans
        with profiler.span("manual_span_1"):
            time.sleep(0.02)

            with profiler.span("nested_span"):
                time.sleep(0.01)

        with profiler.span("manual_span_2", metadata={"type": "validation"}):
            time.sleep(0.015)

    profiler.print_summary()


# =============================================================================
# Example 5: Flushing for Serverless
# =============================================================================


def example_serverless_flushing():
    """
    Proper flushing for serverless environments (Lambda, Cloud Functions).
    """
    print("\n" + "=" * 60)
    print("📊 Example 5: Serverless Flushing")
    print("=" * 60)

    # Configure for serverless
    config = AdvancedLangfuseConfig(
        auto_flush=True,  # Flush after each trace
        flush_at_exit=True,  # Register atexit handler
        debug=True,
    )

    profiler = AdvancedProfiler(config)

    # Simulate Lambda-style execution
    def lambda_handler(event, context):
        """Simulated Lambda handler."""
        try:
            with profiler.trace(
                "lambda_execution", metadata={"event_type": event.get("type")}
            ):
                # Process event
                time.sleep(0.05)
                result = {"statusCode": 200, "body": "OK"}
                profiler.score("success", 1.0)
                return result
        finally:
            # Explicit flush for Lambda (critical!)
            profiler.flush()

    # Simulate invocation
    event = {"type": "api_request", "path": "/search"}
    result = lambda_handler(event, None)
    print(f"Lambda result: {result}")

    # Manual shutdown (call at end of process)
    # profiler.shutdown()  # Uncomment in real Lambda

    print("✅ Flushing complete")


# =============================================================================
# Example 6: Full Agent Workflow with Profiling
# =============================================================================


def example_agent_workflow():
    """
    Complete example with ProfiledWorkflow showing graph visualization.
    """
    print("\n" + "=" * 60)
    print("📊 Example 6: Full Agent Workflow")
    print("=" * 60)

    # Use the full ProfiledWorkflow
    workflow = ProfiledWorkflow()

    # Run with all profiling features
    result = workflow.run(
        query="Best practices for AI agent development",
        trace_name="agent-workflow-demo",
        user_id="demo-user",
        metadata={"example": True, "version": "advanced"},
    )

    # Get profiling report
    print(workflow.get_profiling_report(result))

    # Additional scoring
    workflow.score_trace("agent-workflow-demo", "demo_score", 0.95)

    return result


# =============================================================================
# Example 7: Quick Profile Helper
# =============================================================================


def example_quick_profile():
    """
    Use quick_profile for one-off function profiling.
    """
    print("\n" + "=" * 60)
    print("📊 Example 7: Quick Profile Helper")
    print("=" * 60)

    def expensive_operation(n):
        """Simulate expensive computation."""
        time.sleep(0.1)
        return {"status": "success", "result": n * 2, "_profiling": {"total_time": 0.1}}

    # Quick profile with auto-scoring
    result = quick_profile(
        expensive_operation, 42, trace_name="quick-operation", user_id="quick-user"
    )

    print(f"Result: {result}")


# =============================================================================
# Example 8: Comparing Multiple Traces
# =============================================================================


def example_trace_comparison():
    """
    Run multiple traces for A/B testing or comparison.
    """
    print("\n" + "=" * 60)
    print("📊 Example 8: Trace Comparison")
    print("=" * 60)

    profiler = AdvancedProfiler()

    experiments = [
        {"name": "experiment_a", "delay": 0.1},
        {"name": "experiment_b", "delay": 0.05},
        {"name": "experiment_c", "delay": 0.15},
    ]

    results = []

    for exp in experiments:
        with profiler.trace(
            name=exp["name"], tags=["a_b_test"], metadata={"experiment": True}
        ) as trace_id:

            start = time.time()
            time.sleep(exp["delay"])
            elapsed = time.time() - start

            profiler.score("latency", max(0, 1 - elapsed / 0.2))

            results.append(
                {"name": exp["name"], "trace_id": trace_id, "latency": elapsed}
            )

    # Summary
    print("\n📈 Experiment Comparison:")
    print("-" * 50)
    for r in sorted(results, key=lambda x: x["latency"]):
        print(f"  {r['name']}: {r['latency']*1000:.1f}ms")

    best = min(results, key=lambda x: x["latency"])
    print(f"\n🏆 Best: {best['name']}")


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all advanced profiling examples."""
    print("\n" + "=" * 60)
    print("🚀 ADVANCED LANGFUSE PROFILING EXAMPLES")
    print("=" * 60)

    if not LANGFUSE_AVAILABLE:
        print("\n⚠️ Langfuse not installed!")
        print("   Run: pip install langfuse")
        print("   Examples will run with profiling disabled.\n")

    examples = [
        ("Distributed Tracing", example_distributed_tracing),
        ("Scoring", example_scoring),
        ("Context Propagation", example_context_propagation),
        ("Spans & Decorators", example_spans_and_decorators),
        ("Serverless Flushing", example_serverless_flushing),
        ("Quick Profile", example_quick_profile),
        ("Trace Comparison", example_trace_comparison),
    ]

    for name, func in examples:
        try:
            func()
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")

    # Full workflow example (requires LLM)
    if os.getenv("OPENAI_API_KEY"):
        try:
            example_agent_workflow()
        except Exception as e:
            print(f"\n❌ Agent Workflow failed: {e}")
    else:
        print("\n⚠️ Skipping Agent Workflow (OPENAI_API_KEY not set)")

    print("\n" + "=" * 60)
    print("✨ All examples complete!")
    print("   View traces at: https://cloud.langfuse.com")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
