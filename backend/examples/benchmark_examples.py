"""
RAG Benchmark Examples
======================
Comprehensive benchmarking with:
- Latency measurement (avg, p50, p95, p99)
- Token usage tracking
- RAGAS evaluation (faithfulness, relevancy)
- Comparison tables
- Detailed logging

Based on community best practices from Reddit, HN, X.

Run:
    python examples/benchmark_examples.py
"""

import os
import sys
import time
import logging
from typing import Dict

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Sample Data
# =============================================================================

SAMPLE_DOCS = [
    "LangGraph is a framework for building stateful, multi-actor applications with LLMs.",
    "Command enables dynamic routing without predefined edges in LangGraph workflows.",
    "The interrupt function pauses execution for human approval in HITL patterns.",
    "Semantic memory search uses embeddings to find memories by meaning, not keywords.",
    "LangMem SDK provides complete long-term memory management for AI agents.",
    "RAG combines retrieval from external data with LLM generation for accurate answers.",
    "Vector stores like FAISS and Chroma enable efficient similarity search.",
    "Ollama allows running LLMs locally without cloud API dependencies.",
    "GraphRAG enhances retrieval with knowledge graph relationships.",
    "Caching reduces redundant API calls and improves response times.",
]

BENCHMARK_QUESTIONS = [
    "What is LangGraph?",
    "How does memory search work?",
    "What is RAG?",
    "How do vector stores work?",
    "What is Command used for?",
]


# =============================================================================
# Example 1: Basic Latency Benchmark
# =============================================================================


def example_latency_benchmark():
    """
    Basic latency benchmarking.

    Measures:
    - Average latency
    - P50, P95, P99 percentiles
    - Time to first token
    """
    print("\n" + "=" * 60)
    print("Example 1: Latency Benchmark")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import RAGBenchmark
        from langchain_core.documents import Document

        # Create RAG pipeline
        logger.info("Creating RAG pipeline...")
        rag = RAGPipeline(llm="auto")

        # Add documents
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)
        logger.info(f"Added {len(docs)} documents")

        # Create benchmark
        benchmark = RAGBenchmark(rag, name="Basic RAG")

        # Run benchmark
        logger.info("Running latency benchmark...")
        result = benchmark.run(
            questions=BENCHMARK_QUESTIONS,
            num_runs=3,
            warmup_runs=1,
            include_quality=False,  # Skip RAGAS for speed
        )

        # Print results
        print(benchmark.format_results(result))

        # Manual timing for comparison
        print("\n📊 Manual Timing Verification:")
        latencies = []
        for q in BENCHMARK_QUESTIONS[:3]:
            start = time.time()
            rag.query(q)
            latency = (time.time() - start) * 1000
            latencies.append(latency)
            print(f"  '{q[:20]}...' → {latency:.1f}ms")

        avg = sum(latencies) / len(latencies)
        print(f"\n  Average: {avg:.1f}ms")

        print("\n✅ Latency benchmark completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 2: Token Usage Tracking
# =============================================================================


def example_token_tracking():
    """
    Track token usage across RAG operations.

    Tracks:
    - Input tokens (queries)
    - Output tokens (generations)
    - Embedding tokens
    - Cost estimates
    """
    print("\n" + "=" * 60)
    print("Example 2: Token Usage Tracking")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import TokenCounter
        from langchain_core.documents import Document

        # Create token counter
        counter = TokenCounter(use_tiktoken=True)

        # Create RAG pipeline
        rag = RAGPipeline()
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)

        # Track embedding tokens
        logger.info("Tracking embedding tokens...")
        counter.count_embedding([text for text in SAMPLE_DOCS])

        # Track queries
        logger.info("Tracking query tokens...")
        for question in BENCHMARK_QUESTIONS[:3]:
            # Count input
            counter.count_input(question, "query")

            # Run query
            answer = rag.query(question)

            # Count output
            counter.count_output(answer, "generation")

        # Get stats
        stats = counter.get_stats()

        print("\n📊 Token Usage:")
        print(f"  Input tokens:     {stats['input_tokens']:,}")
        print(f"  Output tokens:    {stats['output_tokens']:,}")
        print(f"  Embedding tokens: {stats['embedding_tokens']:,}")
        print(f"  Total tokens:     {stats['total_tokens']:,}")
        print(f"  Operations:       {stats['operations_count']}")
        print(f"  Cost estimate:    ${stats['cost_estimate_usd']:.4f}")

        # Breakdown by operation
        print("\n📋 Operations:")
        for op in counter.operations[-5:]:
            print(f"  [{op['type']}] {op['operation']}: {op['tokens']} tokens")

        print("\n✅ Token tracking completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


# =============================================================================
# Example 3: RAGAS Evaluation
# =============================================================================


def example_ragas_evaluation():
    """
    RAGAS-based quality evaluation.

    Metrics:
    - Faithfulness (grounded in context)
    - Answer relevancy
    - Context precision/recall
    """
    print("\n" + "=" * 60)
    print("Example 3: RAGAS Evaluation")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import RAGASEvaluator
        from langchain_core.documents import Document

        # Create RAG pipeline
        rag = RAGPipeline()
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)

        # Generate answers
        logger.info("Generating answers for evaluation...")
        questions = BENCHMARK_QUESTIONS[:3]
        answers = []
        contexts = []

        for q in questions:
            result = rag.query_with_sources(q)
            answers.append(result["answer"])

            # Extract context from sources
            ctx = [
                s["content"][:200] if isinstance(s, dict) else str(s)[:200]
                for s in result.get("sources", [])[:2]
            ]
            contexts.append(ctx if ctx else ["No context found"])

        # Run RAGAS evaluation
        logger.info("Running RAGAS evaluation...")
        evaluator = RAGASEvaluator(use_ragas=True)

        result = evaluator.evaluate(
            questions=questions, answers=answers, contexts=contexts
        )

        print("\n📊 RAGAS Scores:")
        for metric, score in result.get("scores", {}).items():
            print(f"  {metric}: {score:.3f}")

        print(f"\n  Samples evaluated: {result.get('sample_count', 0)}")

        if result.get("note"):
            print(f"  Note: {result['note']}")

        # Show sample answers
        print("\n📋 Sample Answers:")
        for i, (q, a) in enumerate(zip(questions, answers)):
            print(f"\n  Q{i+1}: {q}")
            print(f"  A{i+1}: {a[:100]}...")

        print("\n✅ RAGAS evaluation completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 4: Compare Multiple RAG Systems
# =============================================================================


def example_comparison_benchmark():
    """
    Compare multiple RAG systems.

    Compares:
    - Basic RAG
    - Agentic RAG
    - GraphRAG
    - REFRAG (30x faster)
    - CAG (166x cheaper)
    """
    print("\n" + "=" * 60)
    print("Example 4: RAG Systems Comparison")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline, AgenticRAG, GraphRAGPipeline
        from ai.rag.advanced import REFRAGPipeline, CacheAugmentedRAG
        from ai.rag.benchmarks import BenchmarkSuite
        from langchain_core.documents import Document

        # Create benchmark suite
        suite = BenchmarkSuite()

        # Prepare documents
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]

        # 1. Basic RAG
        logger.info("Setting up Basic RAG...")
        basic_rag = RAGPipeline()
        basic_rag.add_documents(docs)
        suite.add("Basic", basic_rag)

        # 2. Agentic RAG
        logger.info("Setting up Agentic RAG...")
        agentic_rag = AgenticRAG(enable_web_search=False)
        agentic_rag.add_documents(docs)
        suite.add(
            "Agentic", agentic_rag, lambda q: agentic_rag.query(q, with_grading=False)
        )

        # 3. GraphRAG
        logger.info("Setting up GraphRAG...")
        graph_rag = GraphRAGPipeline()
        graph_rag.add_documents(docs)
        suite.add("Graph", graph_rag)

        # 4. REFRAG (compression)
        logger.info("Setting up REFRAG...")
        refrag = REFRAGPipeline(compression_ratio=0.1)
        refrag.add_documents(docs)
        suite.add("REFRAG", refrag)

        # 5. CAG (cached)
        logger.info("Setting up CAG...")
        cag = CacheAugmentedRAG()
        cag.add_documents(docs)
        cag.warm_cache(
            [
                ("What is LangGraph?", "LangGraph is a framework for building agents."),
                ("What is RAG?", "RAG combines retrieval with generation."),
            ]
        )
        suite.add("CAG", cag)

        # Run benchmarks
        logger.info("Running comparison benchmark...")
        suite.run_all(
            questions=BENCHMARK_QUESTIONS[:3], num_runs=2, include_quality=False
        )

        # Print comparison table
        print("\n📊 Comparison Table:")
        print(suite.get_comparison_table())

        # Print winners
        print("\n🏆 Winners:")
        print(f"  Fastest:       {suite.get_winner('latency')}")
        print(f"  Highest QPS:   {suite.get_winner('qps')}")
        print(f"  Lowest cost:   {suite.get_winner('cost')}")

        # Export results
        json_results = suite.export_results()
        print(f"\n📁 Results exported ({len(json_results)} bytes)")

        print("\n✅ Comparison benchmark completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 5: Detailed Timing Analysis
# =============================================================================


def example_timing_analysis():
    """
    Detailed timing analysis for RAG components.

    Measures:
    - Retrieval time
    - Generation time
    - Total query time
    - Component breakdown
    """
    print("\n" + "=" * 60)
    print("Example 5: Detailed Timing Analysis")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import TimingTracker
        from langchain_core.documents import Document

        # Create RAG pipeline
        rag = RAGPipeline()
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)

        # Create timing tracker
        tracker = TimingTracker()

        # Run queries with detailed timing
        logger.info("Running queries with timing...")

        for question in BENCHMARK_QUESTIONS[:3]:
            # Time retrieval
            with tracker.measure("retrieval", {"question": question[:20]}):
                docs_retrieved = rag.vector_store.similarity_search(question, k=3)

            # Time context building
            with tracker.measure("context_building"):
                "\n".join(
                    d.page_content if hasattr(d, "page_content") else str(d)
                    for d in docs_retrieved
                )

            # Time generation
            with tracker.measure("generation"):
                rag.query(question)

        # Get summary
        summary = tracker.get_summary()

        print("\n📊 Timing Summary:")
        print(f"  Total time: {summary['total_ms']:.1f}ms")

        print("\n📋 Component Breakdown:")
        for op, stats in summary["operations"].items():
            print(f"\n  {op}:")
            print(f"    Count:   {stats['count']}")
            print(f"    Total:   {stats['total_ms']:.1f}ms")
            print(f"    Average: {stats['avg_ms']:.1f}ms")
            print(f"    Min:     {stats['min_ms']:.1f}ms")
            print(f"    Max:     {stats['max_ms']:.1f}ms")

        # Time to first token
        ttft = tracker.get_time_to_first_token()
        print(f"\n⚡ Time to First Token: {ttft:.1f}ms")

        print("\n✅ Timing analysis completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


# =============================================================================
# Example 6: Advanced RAG Benchmark
# =============================================================================


def example_advanced_rag_benchmark():
    """
    Benchmark advanced RAG techniques.

    Compares:
    - REFRAG (30x efficiency)
    - CLaRa (16x compression)
    - FB-RAG (forward-backward)
    - CAG (166x cheaper)
    """
    print("\n" + "=" * 60)
    print("Example 6: Advanced RAG Techniques Benchmark")
    print("=" * 60)

    try:
        from ai.rag.advanced import (
            REFRAGPipeline,
            CLaRAPipeline,
            FBRAGPipeline,
            CacheAugmentedRAG,
        )
        from langchain_core.documents import Document
        import statistics

        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        questions = BENCHMARK_QUESTIONS[:3]

        results = {}

        # 1. REFRAG
        print("\n📊 Benchmarking REFRAG (30x efficiency)...")
        refrag = REFRAGPipeline(compression_ratio=0.1)
        refrag.add_documents(docs)

        latencies = []
        for q in questions:
            result = refrag.query(q)
            latencies.append(result.get("latency_ms", 0))

        results["REFRAG"] = {
            "avg_latency_ms": statistics.mean(latencies),
            "compression": "10%",
            "expected_speedup": "30x",
        }
        print(f"  Average latency: {results['REFRAG']['avg_latency_ms']:.1f}ms")

        # 2. CLaRa
        print("\n📊 Benchmarking CLaRa (16x compression)...")
        clara = CLaRAPipeline(compression_factor=16)
        clara.add_documents(docs)

        latencies = []
        for q in questions:
            result = clara.query(q)
            latencies.append(result.get("latency_ms", 0))

        results["CLaRa"] = {
            "avg_latency_ms": statistics.mean(latencies),
            "compression": "16x",
            "expected_speedup": "85% latency reduction",
        }
        print(f"  Average latency: {results['CLaRa']['avg_latency_ms']:.1f}ms")

        # 3. FB-RAG
        print("\n📊 Benchmarking FB-RAG (forward-backward)...")
        fbrag = FBRAGPipeline(num_candidates=3)
        fbrag.add_documents(docs)

        latencies = []
        for q in questions:
            result = fbrag.query(q)
            latencies.append(result.get("latency_ms", 0))

        results["FB-RAG"] = {
            "avg_latency_ms": statistics.mean(latencies),
            "candidates": 3,
            "expected_improvement": "48% latency reduction",
        }
        print(f"  Average latency: {results['FB-RAG']['avg_latency_ms']:.1f}ms")

        # 4. CAG
        print("\n📊 Benchmarking CAG (166x cheaper)...")
        cag = CacheAugmentedRAG()
        cag.add_documents(docs)
        cag.warm_cache(
            [
                ("What is LangGraph?", "LangGraph is a framework."),
            ]
        )

        latencies = []
        cache_hits = 0
        for q in questions:
            result = cag.query(q)
            latencies.append(result.get("latency_ms", 0))
            if result.get("cache_hit"):
                cache_hits += 1

        cache_stats = cag.get_cache_stats()
        results["CAG"] = {
            "avg_latency_ms": statistics.mean(latencies),
            "cache_hit_rate": cache_stats["hit_rate"],
            "expected_savings": cache_stats["estimated_savings"],
        }
        print(f"  Average latency: {results['CAG']['avg_latency_ms']:.1f}ms")
        print(f"  Cache hit rate: {cache_stats['hit_rate']}")

        # Summary table
        print("\n" + "=" * 60)
        print("📊 Summary Table")
        print("=" * 60)
        print("\n| Technique | Avg Latency (ms) | Key Benefit |")
        print("|-----------|------------------|-------------|")
        for name, data in results.items():
            benefit = data.get(
                "expected_speedup",
                data.get("expected_improvement", data.get("expected_savings", "N/A")),
            )
            print(f"| {name:9s} | {data['avg_latency_ms']:>16.1f} | {benefit} |")

        print("\n✅ Advanced benchmark completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 7: Benchmark with Decorator
# =============================================================================


def example_decorator_benchmark():
    """
    Use benchmark decorator for easy measurement.
    """
    print("\n" + "=" * 60)
    print("Example 7: Decorator-based Benchmarking")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import benchmark_query, TokenCounter, with_token_tracking
        from langchain_core.documents import Document

        # Create RAG
        rag = RAGPipeline()
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)

        # Create token counter
        counter = TokenCounter()

        # Define decorated query function
        @benchmark_query(name="decorated_query")
        @with_token_tracking(counter)
        def tracked_query(question: str) -> Dict:
            return {"answer": rag.query(question)}

        # Run queries
        print("\n📋 Running decorated queries:")
        for q in BENCHMARK_QUESTIONS[:3]:
            result = tracked_query(q)
            print(f"  Result: {result['answer'][:50]}...")

        # Show token stats
        stats = counter.get_stats()
        print("\n📊 Token Stats:")
        print(f"  Total tokens: {stats['total_tokens']}")
        print(f"  Operations: {stats['operations_count']}")

        print("\n✅ Decorator benchmark completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


# =============================================================================
# Example 8: Full Production Benchmark
# =============================================================================


def example_production_benchmark():
    """
    Full production-style benchmark.

    Includes:
    - Warmup runs
    - Multiple iterations
    - Quality evaluation
    - Export to JSON
    """
    print("\n" + "=" * 60)
    print("Example 8: Production Benchmark")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from ai.rag.benchmarks import RAGBenchmark
        from langchain_core.documents import Document

        # Create RAG
        rag = RAGPipeline()
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        rag.add_documents(docs)

        # Create benchmark
        benchmark = RAGBenchmark(rag, name="Production RAG")

        # Run full benchmark
        logger.info("Running production benchmark...")
        result = benchmark.run(
            questions=BENCHMARK_QUESTIONS,
            num_runs=3,
            warmup_runs=1,
            include_quality=True,
        )

        # Print formatted results
        print(benchmark.format_results(result))

        # Export to dict
        result_dict = result.to_dict()

        print("\n📁 Exported Result Structure:")
        for key in result_dict:
            print(f"  - {key}")

        # Show history
        print(f"\n📊 Benchmark History: {len(benchmark.results_history)} runs")

        print("\n✅ Production benchmark completed!")
        return True

    except Exception as e:
        logger.error(f"Error: {e}")
        return False


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all benchmark examples."""
    print("=" * 60)
    print("  RAG Benchmark Examples")
    print("  Performance Testing & Evaluation")
    print("=" * 60)

    examples = [
        ("Latency Benchmark", example_latency_benchmark),
        ("Token Tracking", example_token_tracking),
        ("RAGAS Evaluation", example_ragas_evaluation),
        ("Systems Comparison", example_comparison_benchmark),
        ("Timing Analysis", example_timing_analysis),
        ("Advanced RAG", example_advanced_rag_benchmark),
        ("Decorator Benchmark", example_decorator_benchmark),
        ("Production Benchmark", example_production_benchmark),
    ]

    results = []

    for name, func in examples:
        try:
            success = func()
            results.append((name, "✅" if success else "⚠️"))
        except Exception as e:
            logger.error(f"{name} failed: {e}")
            results.append((name, "❌"))

    # Summary
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)

    for name, status in results:
        print(f"  {status} {name}")

    passed = sum(1 for _, s in results if s == "✅")
    print(f"\n  Total: {passed}/{len(results)} passed")

    # Benchmark metrics reference
    print("\n📖 Benchmark Metrics Reference:")
    print("  - Latency: Time to complete query (ms)")
    print("  - TTFT: Time to first token (ms)")
    print("  - QPS: Queries per second")
    print("  - Faithfulness: Answer grounded in context (0-1)")
    print("  - Relevancy: Answer addresses question (0-1)")


if __name__ == "__main__":
    main()
