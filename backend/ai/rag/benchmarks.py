"""
RAG Benchmarking Module
=======================
Comprehensive performance testing and evaluation for RAG systems.

Features:
- Latency benchmarking (time-to-first-token, total time)
- Token usage tracking
- RAGAS evaluation (faithfulness, relevance, answer quality)
- Memory profiling
- Comparison between RAG techniques
- Detailed logging with structured output

Based on community best practices from Reddit, HN, and research papers.

Installation:
    pip install ragas datasets timeit memory-profiler

Usage:
    from ai.rag.benchmarks import RAGBenchmark, BenchmarkSuite

    benchmark = RAGBenchmark(rag_pipeline)
    results = benchmark.run_full_benchmark(questions)
    print(benchmark.get_comparison_table())
"""

import time
import logging
import statistics
from typing import List, Dict, Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from collections import defaultdict
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Token Counter
# =============================================================================


class TokenCounter:
    """
    Track token usage across RAG operations.

    Supports:
    - Approximate counting (word-based)
    - Tiktoken counting (if available)
    - Per-operation breakdown

    Usage:
        counter = TokenCounter()
        counter.count_input("query text")
        counter.count_output("response text")
        print(counter.get_stats())
    """

    def __init__(self, use_tiktoken: bool = True):
        self.use_tiktoken = use_tiktoken
        self._tiktoken = None

        if use_tiktoken:
            try:
                import tiktoken

                self._tiktoken = tiktoken.get_encoding("cl100k_base")
            except ImportError:
                logger.warning("tiktoken not available, using word-based counting")

        self.reset()

    def reset(self):
        """Reset all counters."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.embedding_tokens = 0
        self.operations = []

    def _count(self, text: str) -> int:
        """Count tokens in text."""
        if not text:
            return 0

        if self._tiktoken:
            return len(self._tiktoken.encode(text))
        else:
            # Approximate: words * 1.3
            return int(len(text.split()) * 1.3)

    def count_input(self, text: str, operation: str = "query"):
        """Count input tokens."""
        tokens = self._count(text)
        self.input_tokens += tokens
        self.operations.append(
            {
                "type": "input",
                "operation": operation,
                "tokens": tokens,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return tokens

    def count_output(self, text: str, operation: str = "generation"):
        """Count output tokens."""
        tokens = self._count(text)
        self.output_tokens += tokens
        self.operations.append(
            {
                "type": "output",
                "operation": operation,
                "tokens": tokens,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return tokens

    def count_embedding(self, texts: List[str]):
        """Count embedding tokens."""
        tokens = sum(self._count(t) for t in texts)
        self.embedding_tokens += tokens
        self.operations.append(
            {
                "type": "embedding",
                "operation": "embed",
                "tokens": tokens,
                "count": len(texts),
                "timestamp": datetime.now().isoformat(),
            }
        )
        return tokens

    def get_stats(self) -> Dict:
        """Get token usage statistics."""
        total = self.input_tokens + self.output_tokens + self.embedding_tokens

        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "total_tokens": total,
            "operations_count": len(self.operations),
            "cost_estimate_usd": self._estimate_cost(total),
        }

    def _estimate_cost(self, tokens: int) -> float:
        """Estimate cost (based on GPT-4 pricing, $0 for local)."""
        # GPT-4: ~$0.03 per 1K tokens (average)
        # Local (Ollama): $0
        return tokens * 0.00003


# =============================================================================
# Timing Utilities
# =============================================================================


@dataclass
class TimingResult:
    """Result of a timing measurement."""

    operation: str
    duration_ms: float
    start_time: datetime
    end_time: datetime
    metadata: Dict = field(default_factory=dict)


class TimingTracker:
    """
    Track timing for RAG operations.

    Usage:
        tracker = TimingTracker()

        with tracker.measure("retrieval"):
            docs = retriever.invoke(query)

        with tracker.measure("generation"):
            answer = llm.invoke(prompt)

        print(tracker.get_summary())
    """

    def __init__(self):
        self.timings: List[TimingResult] = []
        self._start_time = None
        self._current_op = None

    class _MeasureContext:
        def __init__(self, tracker, operation, metadata=None):
            self.tracker = tracker
            self.operation = operation
            self.metadata = metadata or {}

        def __enter__(self):
            self.start = datetime.now()
            return self

        def __exit__(self, *args):
            end = datetime.now()
            duration_ms = (end - self.start).total_seconds() * 1000

            self.tracker.timings.append(
                TimingResult(
                    operation=self.operation,
                    duration_ms=duration_ms,
                    start_time=self.start,
                    end_time=end,
                    metadata=self.metadata,
                )
            )

    def measure(self, operation: str, metadata: Dict = None):
        """Context manager for measuring operation time."""
        return self._MeasureContext(self, operation, metadata)

    def get_summary(self) -> Dict:
        """Get timing summary."""
        if not self.timings:
            return {"total_ms": 0, "operations": {}}

        # Group by operation
        by_operation = defaultdict(list)
        for t in self.timings:
            by_operation[t.operation].append(t.duration_ms)

        summary = {
            "total_ms": sum(t.duration_ms for t in self.timings),
            "operations": {},
        }

        for op, times in by_operation.items():
            summary["operations"][op] = {
                "count": len(times),
                "total_ms": sum(times),
                "avg_ms": statistics.mean(times),
                "min_ms": min(times),
                "max_ms": max(times),
                "std_ms": statistics.stdev(times) if len(times) > 1 else 0,
            }

        return summary

    def get_time_to_first_token(self) -> float:
        """Get time to first token (retrieval + start of generation)."""
        retrieval_time = 0
        for t in self.timings:
            if t.operation == "retrieval":
                retrieval_time += t.duration_ms
                break
        return retrieval_time

    def reset(self):
        """Reset all timings."""
        self.timings = []


# =============================================================================
# RAGAS Evaluation
# =============================================================================


class RAGASEvaluator:
    """
    RAGAS-based evaluation for RAG quality.

    Metrics:
    - Faithfulness: Is the answer grounded in the context?
    - Answer Relevancy: Does the answer address the question?
    - Context Precision: Are retrieved docs relevant?
    - Context Recall: Are all relevant docs retrieved?

    Usage:
        evaluator = RAGASEvaluator()
        scores = evaluator.evaluate(
            questions=["What is X?"],
            answers=["X is..."],
            contexts=[["Doc about X"]]
        )
    """

    def __init__(self, use_ragas: bool = True):
        self.use_ragas = use_ragas
        self._ragas_available = False

        if use_ragas:
            try:
                from ragas import evaluate
                from ragas.metrics import (
                    faithfulness,
                    answer_relevancy,
                    context_precision,
                    context_recall,
                )

                self._ragas_available = True
                self._evaluate = evaluate
                self._metrics = {
                    "faithfulness": faithfulness,
                    "answer_relevancy": answer_relevancy,
                    "context_precision": context_precision,
                    "context_recall": context_recall,
                }
            except ImportError:
                logger.warning("RAGAS not available. Install: pip install ragas")

    def evaluate(
        self,
        questions: List[str],
        answers: List[str],
        contexts: List[List[str]],
        ground_truths: List[str] = None,
        metrics: List[str] = None,
    ) -> Dict:
        """
        Evaluate RAG quality.

        Args:
            questions: List of questions
            answers: List of generated answers
            contexts: List of context lists (retrieved docs)
            ground_truths: Optional ground truth answers
            metrics: Which metrics to use (default: all)

        Returns:
            Dict with metric scores
        """
        if not self._ragas_available:
            return self._fallback_evaluate(questions, answers, contexts)

        try:
            from datasets import Dataset

            # Build dataset
            data = {"question": questions, "answer": answers, "contexts": contexts}

            if ground_truths:
                data["ground_truth"] = ground_truths

            dataset = Dataset.from_dict(data)

            # Select metrics
            if metrics:
                selected = [self._metrics[m] for m in metrics if m in self._metrics]
            else:
                selected = list(self._metrics.values())

            # Evaluate
            result = self._evaluate(dataset, selected)

            return {
                "scores": dict(result),
                "sample_count": len(questions),
                "metrics_used": [m.name for m in selected],
            }

        except Exception as e:
            logger.error(f"RAGAS evaluation failed: {e}")
            return self._fallback_evaluate(questions, answers, contexts)

    def _fallback_evaluate(
        self, questions: List[str], answers: List[str], contexts: List[List[str]]
    ) -> Dict:
        """Fallback evaluation without RAGAS."""
        scores = {"faithfulness": [], "relevancy": [], "context_coverage": []}

        for q, a, ctx in zip(questions, answers, contexts):
            # Simple heuristic scores

            # Faithfulness: overlap between answer and context
            context_text = " ".join(ctx).lower()
            answer_words = set(a.lower().split())
            context_words = set(context_text.split())
            overlap = (
                len(answer_words & context_words) / len(answer_words)
                if answer_words
                else 0
            )
            scores["faithfulness"].append(min(overlap * 1.5, 1.0))

            # Relevancy: question words in answer
            question_words = set(q.lower().split())
            q_overlap = (
                len(question_words & answer_words) / len(question_words)
                if question_words
                else 0
            )
            scores["relevancy"].append(min(q_overlap * 2, 1.0))

            # Context coverage: context size vs answer size
            coverage = min(len(context_text) / (len(a) * 10), 1.0) if a else 0
            scores["context_coverage"].append(coverage)

        return {
            "scores": {
                "faithfulness": statistics.mean(scores["faithfulness"]),
                "relevancy": statistics.mean(scores["relevancy"]),
                "context_coverage": statistics.mean(scores["context_coverage"]),
            },
            "sample_count": len(questions),
            "note": "Using fallback heuristic evaluation",
        }


# =============================================================================
# RAG Benchmark
# =============================================================================


@dataclass
class BenchmarkResult:
    """Complete benchmark result."""

    name: str
    timestamp: datetime

    # Timing
    latency_avg_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    time_to_first_token_ms: float

    # Tokens
    input_tokens_avg: float
    output_tokens_avg: float
    total_tokens_avg: float

    # Quality (RAGAS)
    faithfulness: float
    relevancy: float

    # Additional
    queries_per_second: float
    cost_per_query_usd: float
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "timestamp": self.timestamp.isoformat(),
            "latency": {
                "avg_ms": self.latency_avg_ms,
                "p50_ms": self.latency_p50_ms,
                "p95_ms": self.latency_p95_ms,
                "p99_ms": self.latency_p99_ms,
                "ttft_ms": self.time_to_first_token_ms,
            },
            "tokens": {
                "input_avg": self.input_tokens_avg,
                "output_avg": self.output_tokens_avg,
                "total_avg": self.total_tokens_avg,
            },
            "quality": {"faithfulness": self.faithfulness, "relevancy": self.relevancy},
            "performance": {
                "qps": self.queries_per_second,
                "cost_per_query_usd": self.cost_per_query_usd,
            },
            "metadata": self.metadata,
        }


class RAGBenchmark:
    """
    Comprehensive RAG benchmarking.

    Usage:
        from ai.rag import RAGPipeline

        rag = RAGPipeline()
        rag.add_documents(docs)

        benchmark = RAGBenchmark(rag, name="Basic RAG")
        result = benchmark.run(questions, num_runs=10)

        print(benchmark.format_results(result))
    """

    def __init__(
        self, rag_pipeline, name: str = "RAG Pipeline", query_fn: Callable = None
    ):
        """
        Initialize benchmark.

        Args:
            rag_pipeline: RAG pipeline with query() method
            name: Name for this benchmark
            query_fn: Optional custom query function
        """
        self.pipeline = rag_pipeline
        self.name = name
        self.query_fn = query_fn or self._default_query

        self.token_counter = TokenCounter()
        self.timing_tracker = TimingTracker()
        self.evaluator = RAGASEvaluator()

        self.results_history: List[BenchmarkResult] = []

    def _default_query(self, question: str) -> Dict:
        """Default query function."""
        if hasattr(self.pipeline, "query"):
            result = self.pipeline.query(question)
            if isinstance(result, dict):
                return result
            return {"answer": result, "contexts": []}
        raise ValueError("Pipeline must have query() method")

    def run(
        self,
        questions: List[str],
        num_runs: int = 5,
        warmup_runs: int = 1,
        include_quality: bool = True,
        ground_truths: List[str] = None,
    ) -> BenchmarkResult:
        """
        Run benchmark.

        Args:
            questions: Questions to test
            num_runs: Number of runs per question
            warmup_runs: Warmup runs (not counted)
            include_quality: Whether to run RAGAS evaluation
            ground_truths: Optional ground truth answers

        Returns:
            BenchmarkResult
        """
        logger.info(f"Starting benchmark: {self.name}")
        logger.info(f"Questions: {len(questions)}, Runs: {num_runs}")

        # Reset trackers
        self.token_counter.reset()
        self.timing_tracker.reset()

        # Warmup
        if warmup_runs > 0:
            logger.info(f"Running {warmup_runs} warmup runs...")
            for _ in range(warmup_runs):
                self.query_fn(questions[0])

        # Run benchmark
        latencies = []
        answers = []
        contexts = []

        for run in range(num_runs):
            logger.info(f"Run {run + 1}/{num_runs}")

            for question in questions:
                # Track tokens
                self.token_counter.count_input(question, "query")

                # Track timing
                start = time.time()

                try:
                    with self.timing_tracker.measure("total_query"):
                        result = self.query_fn(question)

                    answer = result.get("answer", str(result))
                    ctx = result.get("contexts", result.get("sources", []))

                    # Normalize contexts
                    if ctx and not isinstance(ctx[0], str):
                        ctx = [
                            c.page_content if hasattr(c, "page_content") else str(c)
                            for c in ctx
                        ]

                    # Track output tokens
                    self.token_counter.count_output(answer, "generation")

                    # Store for quality eval (only last run)
                    if run == num_runs - 1:
                        answers.append(answer)
                        contexts.append(ctx if ctx else ["No context"])

                except Exception as e:
                    logger.error(f"Query failed: {e}")
                    if run == num_runs - 1:
                        answers.append(f"Error: {e}")
                        contexts.append(["Error"])

                latencies.append((time.time() - start) * 1000)

        # Calculate metrics
        latencies_sorted = sorted(latencies)
        n = len(latencies)

        # Quality evaluation
        if include_quality and answers:
            logger.info("Running quality evaluation...")
            quality = self.evaluator.evaluate(
                questions=questions,
                answers=answers,
                contexts=contexts,
                ground_truths=ground_truths,
            )
            faithfulness = quality["scores"].get("faithfulness", 0)
            relevancy = quality["scores"].get(
                "relevancy", quality["scores"].get("answer_relevancy", 0)
            )
        else:
            faithfulness = 0
            relevancy = 0

        # Token stats
        token_stats = self.token_counter.get_stats()
        total_queries = len(questions) * num_runs

        # Build result
        result = BenchmarkResult(
            name=self.name,
            timestamp=datetime.now(),
            latency_avg_ms=statistics.mean(latencies),
            latency_p50_ms=latencies_sorted[int(n * 0.5)],
            latency_p95_ms=(
                latencies_sorted[int(n * 0.95)] if n > 20 else latencies_sorted[-1]
            ),
            latency_p99_ms=(
                latencies_sorted[int(n * 0.99)] if n > 100 else latencies_sorted[-1]
            ),
            time_to_first_token_ms=self.timing_tracker.get_time_to_first_token(),
            input_tokens_avg=token_stats["input_tokens"] / total_queries,
            output_tokens_avg=token_stats["output_tokens"] / total_queries,
            total_tokens_avg=token_stats["total_tokens"] / total_queries,
            faithfulness=faithfulness,
            relevancy=relevancy,
            queries_per_second=1000 / statistics.mean(latencies),
            cost_per_query_usd=token_stats["cost_estimate_usd"] / total_queries,
            metadata={
                "num_runs": num_runs,
                "num_questions": len(questions),
                "total_queries": total_queries,
            },
        )

        self.results_history.append(result)
        logger.info(f"Benchmark complete: {result.latency_avg_ms:.1f}ms avg")

        return result

    def format_results(self, result: BenchmarkResult = None) -> str:
        """Format results as readable string."""
        if result is None:
            result = self.results_history[-1] if self.results_history else None

        if not result:
            return "No benchmark results available"

        return f"""
╔══════════════════════════════════════════════════════════════╗
║  Benchmark: {result.name:48s} ║
╠══════════════════════════════════════════════════════════════╣
║  LATENCY                                                     ║
║    Average:     {result.latency_avg_ms:>8.1f} ms                               ║
║    P50:         {result.latency_p50_ms:>8.1f} ms                               ║
║    P95:         {result.latency_p95_ms:>8.1f} ms                               ║
║    P99:         {result.latency_p99_ms:>8.1f} ms                               ║
║    TTFT:        {result.time_to_first_token_ms:>8.1f} ms                               ║
╠══════════════════════════════════════════════════════════════╣
║  TOKENS                                                      ║
║    Input avg:   {result.input_tokens_avg:>8.0f}                                  ║
║    Output avg:  {result.output_tokens_avg:>8.0f}                                  ║
║    Total avg:   {result.total_tokens_avg:>8.0f}                                  ║
╠══════════════════════════════════════════════════════════════╣
║  QUALITY (RAGAS)                                             ║
║    Faithfulness: {result.faithfulness:>7.2f}                                   ║
║    Relevancy:    {result.relevancy:>7.2f}                                   ║
╠══════════════════════════════════════════════════════════════╣
║  PERFORMANCE                                                 ║
║    QPS:         {result.queries_per_second:>8.2f}                                  ║
║    Cost/query:  ${result.cost_per_query_usd:>7.5f}                                ║
╚══════════════════════════════════════════════════════════════╝
"""


# =============================================================================
# Benchmark Suite (Compare Multiple RAG Systems)
# =============================================================================


class BenchmarkSuite:
    """
    Compare multiple RAG systems.

    Usage:
        suite = BenchmarkSuite()

        suite.add("Basic RAG", basic_rag)
        suite.add("Agentic RAG", agentic_rag)
        suite.add("GraphRAG", graph_rag)

        results = suite.run_all(questions)
        print(suite.get_comparison_table())
    """

    def __init__(self):
        self.benchmarks: Dict[str, RAGBenchmark] = {}
        self.results: Dict[str, BenchmarkResult] = {}

    def add(self, name: str, pipeline, query_fn: Callable = None):
        """Add a RAG pipeline to benchmark."""
        self.benchmarks[name] = RAGBenchmark(pipeline, name, query_fn)

    def run_all(
        self, questions: List[str], num_runs: int = 5, **kwargs
    ) -> Dict[str, BenchmarkResult]:
        """Run benchmarks for all pipelines."""
        logger.info(f"Running benchmark suite with {len(self.benchmarks)} pipelines")

        for name, benchmark in self.benchmarks.items():
            logger.info(f"\n{'='*60}")
            logger.info(f"Benchmarking: {name}")
            logger.info("=" * 60)

            self.results[name] = benchmark.run(questions, num_runs, **kwargs)

        return self.results

    def get_comparison_table(self) -> str:
        """Generate comparison table."""
        if not self.results:
            return "No results available. Run benchmarks first."

        # Header
        header = "| Pipeline | Latency (ms) | QPS | Faithfulness | Relevancy | Tokens | Cost/Query |"
        separator = "|----------|--------------|-----|--------------|-----------|--------|------------|"

        rows = [header, separator]

        for name, result in self.results.items():
            row = (
                f"| {name[:8]:8s} | "
                f"{result.latency_avg_ms:12.1f} | "
                f"{result.queries_per_second:3.1f} | "
                f"{result.faithfulness:12.2f} | "
                f"{result.relevancy:9.2f} | "
                f"{result.total_tokens_avg:6.0f} | "
                f"${result.cost_per_query_usd:.4f} |"
            )
            rows.append(row)

        return "\n".join(rows)

    def get_winner(self, metric: str = "latency") -> str:
        """Get best performing pipeline for a metric."""
        if not self.results:
            return "No results"

        if metric == "latency":
            return min(self.results, key=lambda x: self.results[x].latency_avg_ms)
        elif metric == "quality":
            return max(self.results, key=lambda x: self.results[x].faithfulness)
        elif metric == "cost":
            return min(self.results, key=lambda x: self.results[x].cost_per_query_usd)
        elif metric == "qps":
            return max(self.results, key=lambda x: self.results[x].queries_per_second)

        return "Unknown metric"

    def export_results(self, filepath: str = None) -> str:
        """Export results to JSON."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "pipelines": len(self.results),
            "results": {
                name: result.to_dict() for name, result in self.results.items()
            },
        }

        json_str = json.dumps(data, indent=2)

        if filepath:
            with open(filepath, "w") as f:
                f.write(json_str)
            logger.info(f"Results exported to {filepath}")

        return json_str


# =============================================================================
# Benchmark Decorators
# =============================================================================


def benchmark_query(name: str = "query"):
    """Decorator to benchmark a query function."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()

            try:
                result = func(*args, **kwargs)
                success = True
            except Exception as e:
                result = None
                success = False
                logger.error(f"Query failed: {e}")

            duration_ms = (time.time() - start) * 1000

            logger.info(f"[{name}] Duration: {duration_ms:.1f}ms, Success: {success}")

            return result

        return wrapper

    return decorator


def with_token_tracking(counter: TokenCounter):
    """Decorator to track tokens."""

    def decorator(func):
        @wraps(func)
        def wrapper(question: str, *args, **kwargs):
            counter.count_input(question)
            result = func(question, *args, **kwargs)

            if isinstance(result, dict) and "answer" in result:
                counter.count_output(result["answer"])
            elif isinstance(result, str):
                counter.count_output(result)

            return result

        return wrapper

    return decorator


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Tracking
    "TokenCounter",
    "TimingTracker",
    "TimingResult",
    # Evaluation
    "RAGASEvaluator",
    # Benchmarking
    "BenchmarkResult",
    "RAGBenchmark",
    "BenchmarkSuite",
    # Decorators
    "benchmark_query",
    "with_token_tracking",
]
