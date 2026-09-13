"""
Langfuse Profiling Example
==========================
Demonstrates how to use Langfuse for profiling LangGraph agents.

Setup:
    1. Install dependencies:
       pip install langfuse langchain langchain_openai langgraph python-dotenv

    2. Create .env file:
       LANGFUSE_SECRET_KEY="sk-lf-..."
       LANGFUSE_PUBLIC_KEY="pk-lf-..."
       LANGFUSE_BASE_URL="https://cloud.langfuse.com"
       OPENAI_API_KEY="sk-proj-..."

    3. Run this example:
       python examples/langfuse_profiling_example.py

Features Demonstrated:
    - Basic profiling with automatic tracing
    - Custom scoring
    - Profiling reports
    - Multiple traces comparison
"""

import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from profiling import (
    ProfiledWorkflow,
    LangfuseManager,
    LangfuseConfig,
    WorkflowConfig,
    LANGFUSE_AVAILABLE,
)


def example_basic_profiling():
    """Example 1: Basic profiling with default configuration."""
    print("\n" + "=" * 60)
    print("📊 Example 1: Basic Profiling")
    print("=" * 60)

    # Create workflow with default config
    workflow = ProfiledWorkflow()

    # Run with auto-generated trace name
    result = workflow.run("AI trends in 2025")

    # Print profiling report
    print(workflow.get_profiling_report(result))

    return result


def example_custom_configuration():
    """Example 2: Custom configuration."""
    print("\n" + "=" * 60)
    print("📊 Example 2: Custom Configuration")
    print("=" * 60)

    # Custom Langfuse config
    langfuse_config = LangfuseConfig(
        # These will be loaded from environment if not specified
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        enabled=True,
        debug=True,  # Print debug messages
    )

    # Custom workflow config
    config = WorkflowConfig(
        llm_model="gpt-4o", temperature=0.5, max_retries=5, langfuse=langfuse_config
    )

    workflow = ProfiledWorkflow(config)

    # Run with custom trace name
    result = workflow.run(
        "Machine learning best practices",
        trace_name="ml-research-001",
        user_id="user-123",
        metadata={"project": "AI Research", "priority": "high"},
    )

    print(workflow.get_profiling_report(result))

    return result


def example_custom_scoring():
    """Example 3: Adding custom scores to traces."""
    print("\n" + "=" * 60)
    print("📊 Example 3: Custom Scoring")
    print("=" * 60)

    workflow = ProfiledWorkflow()

    trace_name = "scored-research-001"
    result = workflow.run("Cloud computing architecture", trace_name=trace_name)

    # Add custom scores
    if result["status"] == "success":
        # Score based on report quality (example metrics)
        report_length = len(result.get("report", ""))
        quality_score = min(1.0, report_length / 1000)  # Normalize

        workflow.score_trace(
            trace_name,
            "report_quality",
            quality_score,
            comment=f"Based on report length: {report_length} chars",
        )

        # Score based on comprehensiveness
        num_queries = len(result.get("search_queries", []))
        comprehensiveness = min(1.0, num_queries / 5)

        workflow.score_trace(
            trace_name,
            "comprehensiveness",
            comprehensiveness,
            comment=f"Based on {num_queries} queries",
        )

        print("✅ Added custom scores:")
        print(f"   - report_quality: {quality_score:.2f}")
        print(f"   - comprehensiveness: {comprehensiveness:.2f}")

    print(workflow.get_profiling_report(result))

    return result


def example_multiple_traces():
    """Example 4: Running multiple traces for comparison."""
    print("\n" + "=" * 60)
    print("📊 Example 4: Multiple Traces for Comparison")
    print("=" * 60)

    workflow = ProfiledWorkflow()

    queries = [
        ("AI in healthcare", "healthcare-001"),
        ("Quantum computing", "quantum-001"),
        ("Sustainable energy", "energy-001"),
    ]

    results = []

    for query, trace_name in queries:
        print(f"\n🔍 Running: {query}")
        result = workflow.run(query, trace_name=trace_name)
        results.append(
            {
                "query": query,
                "trace": trace_name,
                "status": result["status"],
                "time": result.get("_profiling", {}).get("total_time", 0),
            }
        )

    # Summary
    print("\n" + "=" * 60)
    print("📈 COMPARISON SUMMARY")
    print("=" * 60)
    print(f"{'Query':<25} {'Trace':<20} {'Status':<10} {'Time':<10}")
    print("-" * 65)

    for r in results:
        print(f"{r['query']:<25} {r['trace']:<20} {r['status']:<10} {r['time']:.2f}s")

    avg_time = sum(r["time"] for r in results) / len(results)
    success_rate = sum(1 for r in results if r["status"] == "success") / len(results)

    print("-" * 65)
    print(f"{'Average Time:':<47} {avg_time:.2f}s")
    print(f"{'Success Rate:':<47} {success_rate:.0%}")

    return results


def example_langfuse_manager_direct():
    """Example 5: Using LangfuseManager directly for custom tracing."""
    print("\n" + "=" * 60)
    print("📊 Example 5: Direct LangfuseManager Usage")
    print("=" * 60)

    config = LangfuseConfig(debug=True)
    manager = LangfuseManager(config)

    if not LANGFUSE_AVAILABLE:
        print("⚠️ Langfuse not installed. Skipping this example.")
        return

    # Create a trace manually
    trace_id = manager.create_trace(
        name="custom-trace", user_id="user-456", metadata={"custom": True}
    )

    if trace_id:
        print(f"✅ Created trace: {trace_id}")

        # Do some work...
        import time

        time.sleep(0.1)

        # Add scores
        manager.score(trace_id, "custom_metric", 0.85, "Custom score example")
        print("✅ Added custom score")

        # Flush to ensure data is sent
        manager.flush()
        print("✅ Flushed events to Langfuse")
    else:
        print("⚠️ Could not create trace (check configuration)")


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("🚀 LANGFUSE PROFILING EXAMPLES")
    print("=" * 60)

    if not LANGFUSE_AVAILABLE:
        print("\n⚠️ Langfuse is not installed!")
        print("   Run: pip install langfuse")
        print("   Profiling will be disabled but examples will still run.\n")

    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        print("\n⚠️ OPENAI_API_KEY not set!")
        print("   Examples will use mock LLM responses.\n")

    # Run examples
    try:
        example_basic_profiling()
    except Exception as e:
        print(f"❌ Example 1 failed: {e}")

    try:
        example_custom_configuration()
    except Exception as e:
        print(f"❌ Example 2 failed: {e}")

    try:
        example_custom_scoring()
    except Exception as e:
        print(f"❌ Example 3 failed: {e}")

    # Skip multi-trace example by default (takes longer)
    # try:
    #     example_multiple_traces()
    # except Exception as e:
    #     print(f"❌ Example 4 failed: {e}")

    try:
        example_langfuse_manager_direct()
    except Exception as e:
        print(f"❌ Example 5 failed: {e}")

    print("\n" + "=" * 60)
    print("✨ Examples Complete!")
    print("   View traces in Langfuse dashboard:")
    print("   https://cloud.langfuse.com")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
