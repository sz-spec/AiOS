"""
Advanced RAG Examples - December 2025 Techniques
================================================
State-of-the-art RAG optimizations from research papers and community discussions.

Examples:
1. REFRAG - 30x efficiency (Meta)
2. CLaRa - 16x-128x compression (Apple)
3. FB-RAG - Forward-Backward for multi-hop
4. Weak-to-Strong GraphRAG
5. Agentic RAG (7 patterns)
6. EntropyGuard - Deduplication
7. CAG - Cache-Augmented (166x cheaper)
8. RAGFlow - Template chunking

Run:
    python examples/advanced_rag_examples.py
"""

import os
import sys

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Sample Documents
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


# =============================================================================
# Example 1: REFRAG - 30x Efficiency (Meta)
# =============================================================================


def example_refrag():
    """
    REFRAG: Retrieval-Embedded Forward RAG.

    From Meta research - 30x efficiency improvement:
    - Compresses 16K tokens to ~1K
    - Solves "lost in the middle" problem
    - RL-based compression policy
    """
    print("\n" + "=" * 60)
    print("Example 1: REFRAG - 30x Efficiency (Meta)")
    print("=" * 60)

    try:
        from ai.rag.advanced import REFRAGPipeline
        from langchain_core.documents import Document

        # Create REFRAG pipeline
        refrag = REFRAGPipeline(llm="auto", compression_ratio=0.1)  # Compress to 10%

        print("\n📚 Adding documents with compression...")
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        refrag.add_documents(docs)

        print(f"  ✓ Compressed {len(docs)} documents")
        print(f"  ✓ Compression ratio: {refrag.compression_ratio}")

        # Query
        print("\n🔍 Querying with compressed retrieval...")

        questions = [
            "What is LangGraph?",
            "How does memory search work?",
        ]

        for q in questions:
            print(f"\n  Q: {q}")
            result = refrag.query(q)
            print(f"  A: {result['answer'][:150]}...")
            print(f"  ⚡ Latency: {result['latency_ms']:.1f}ms")

        print("\n✅ REFRAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 2: CLaRa - Apple Compression
# =============================================================================


def example_clara():
    """
    CLaRa: Compressed Latent RAG from Apple.

    16x-128x compression:
    - 97% accuracy with 10x fewer tokens
    - 85% latency reduction
    - Supports multimodal (text + images)
    """
    print("\n" + "=" * 60)
    print("Example 2: CLaRa - 16x-128x Compression (Apple)")
    print("=" * 60)

    try:
        from ai.rag.advanced import CLaRAPipeline
        from langchain_core.documents import Document

        # Create CLaRa pipeline
        clara = CLaRAPipeline(compression_factor=16, latent_dim=256)

        print("\n📚 Adding documents with latent compression...")
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        clara.add_documents(docs)

        print(f"  ✓ Latent dimension: {clara.latent_dim}")
        print(f"  ✓ Compression: {clara.compression_factor}x")

        # Query in latent space
        print("\n🔍 Querying in latent space...")

        result = clara.query("What is RAG?")
        print("\n  Q: What is RAG?")
        print(f"  A: {result['answer'][:150]}...")
        print(f"  ⚡ Latency: {result['latency_ms']:.1f}ms")

        print("\n✅ CLaRa example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 3: FB-RAG - Forward-Backward
# =============================================================================


def example_fbrag():
    """
    FB-RAG: Forward-Backward RAG.

    8%-48% accuracy improvement:
    - Small LLM generates candidates
    - Chunks re-ranked by relevance to candidates
    - 48% latency reduction
    """
    print("\n" + "=" * 60)
    print("Example 3: FB-RAG - Forward-Backward")
    print("=" * 60)

    try:
        from ai.rag.advanced import FBRAGPipeline
        from langchain_core.documents import Document

        # Create FB-RAG pipeline
        fbrag = FBRAGPipeline(
            small_model="llama3:8b", large_model="llama3", num_candidates=3
        )

        print("\n📚 Adding documents...")
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        fbrag.add_documents(docs)

        # Query with forward-backward
        print("\n🔍 Forward-Backward query...")
        print("  1️⃣ Forward: Generate candidates with small LLM")
        print("  2️⃣ Backward: Re-rank chunks by candidates")
        print("  3️⃣ Generate: Final answer with large LLM")

        result = fbrag.query("How do agents use memory?")

        print("\n  Q: How do agents use memory?")
        print(f"  Candidates generated: {len(result.get('candidates', []))}")
        print(f"  A: {result['answer'][:150]}...")
        print(f"  ⚡ Latency: {result['latency_ms']:.1f}ms")

        print("\n✅ FB-RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 4: Weak-to-Strong GraphRAG
# =============================================================================


def example_weak_to_strong():
    """
    Weak-to-Strong GraphRAG.

    80% accuracy with 5% data:
    - Weak retriever extracts paths
    - Strong LLM aligns evidence
    - Structure-aware reorganization
    """
    print("\n" + "=" * 60)
    print("Example 4: Weak-to-Strong GraphRAG")
    print("=" * 60)

    try:
        from ai.rag.advanced import WeakToStrongGraphRAG
        from langchain_core.documents import Document

        # Create Weak-to-Strong pipeline
        ws_rag = WeakToStrongGraphRAG(weak_model="llama3:8b", strong_model="llama3")

        print("\n📚 Adding documents and building knowledge graph...")
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        ws_rag.add_documents(docs)

        if ws_rag.graph:
            print(f"  ✓ Graph nodes: {ws_rag.graph.number_of_nodes()}")
            print(f"  ✓ Graph edges: {ws_rag.graph.number_of_edges()}")

        # Query with weak-to-strong
        print("\n🔍 Weak-to-Strong query...")
        print("  1️⃣ Weak: Extract graph paths")
        print("  2️⃣ Strong: Align evidence chains")

        result = ws_rag.query("How is LangGraph related to memory?")

        print("\n  Q: How is LangGraph related to memory?")
        print(f"  Graph paths found: {len(result.get('graph_paths', []))}")
        print(f"  A: {result['answer'][:150]}...")

        print("\n✅ Weak-to-Strong example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 5: Agentic RAG (7 Patterns)
# =============================================================================


def example_agentic_patterns():
    """
    Agentic RAG: 7 architecture patterns.

    35% error reduction, 50% less misinformation:
    1. Router - Route to datasource
    2. Query Planner - Multi-step
    3. Adaptive - Dynamic strategy
    4. Corrective - Self-correct
    5. Self-Reflective - Reflect
    6. Speculative - Generate & verify
    7. Self-Route - Fetch vs tools
    """
    print("\n" + "=" * 60)
    print("Example 5: Agentic RAG (7 Patterns)")
    print("=" * 60)

    try:
        from ai.rag.advanced import AgenticRAGRouter, AgenticRAGType
        from langchain_core.documents import Document

        # Test different patterns
        patterns = ["router", "corrective", "adaptive"]

        for pattern in patterns:
            print(f"\n📋 Pattern: {pattern.upper()}")

            agentic = AgenticRAGRouter(rag_type=pattern, llm="llama3")

            docs = [Document(page_content=text) for text in SAMPLE_DOCS[:5]]
            agentic.add_documents(docs)

            result = agentic.query("What is vector search?")
            print(f"  A: {result['answer'][:100]}...")
            print(f"  ⚡ Latency: {result['latency_ms']:.1f}ms")

        print("\n📊 All 7 patterns available:")
        for t in AgenticRAGType:
            print(f"  - {t.value}")

        print("\n✅ Agentic RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 6: EntropyGuard - Deduplication
# =============================================================================


def example_entropy_guard():
    """
    EntropyGuard: 40% storage reduction.

    - Hash-based exact duplicate removal
    - Embedding-based near-duplicate detection
    - Entropy filtering for low-quality content
    """
    print("\n" + "=" * 60)
    print("Example 6: EntropyGuard - Deduplication")
    print("=" * 60)

    try:
        from ai.rag.advanced import EntropyGuard
        from langchain_core.documents import Document

        # Create EntropyGuard
        guard = EntropyGuard(similarity_threshold=0.95, min_entropy=0.1)

        # Documents with duplicates
        docs_with_dupes = [
            Document(page_content="LangGraph is a framework."),
            Document(page_content="LangGraph is a framework."),  # Exact dupe
            Document(page_content="LangGraph is a framework!"),  # Near dupe
            Document(page_content="Vector stores enable search."),
            Document(page_content="aaaaaaaaaaaaaaaa"),  # Low entropy
            Document(page_content="RAG combines retrieval and generation."),
        ]

        print(f"\n📚 Input documents: {len(docs_with_dupes)}")

        # Deduplicate
        unique_docs = guard.deduplicate(docs_with_dupes)

        print(f"✓ After deduplication: {len(unique_docs)}")
        print(f"✓ Removed: {len(docs_with_dupes) - len(unique_docs)} documents")

        # Stats
        stats = guard.get_stats()
        print("\n📊 Stats:")
        print(f"  - Unique hashes: {stats['unique_hashes']}")
        print(f"  - Similarity threshold: {stats['threshold']}")

        print("\n✅ EntropyGuard example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 7: CAG - Cache-Augmented Generation
# =============================================================================


def example_cag():
    """
    CAG: 166x cost reduction through caching.

    - Cache static Q&A pairs
    - RAG fallback for dynamic queries
    - 85% latency reduction for cache hits
    """
    print("\n" + "=" * 60)
    print("Example 7: CAG - Cache-Augmented (166x Cheaper)")
    print("=" * 60)

    try:
        from ai.rag.advanced import CacheAugmentedRAG
        from langchain_core.documents import Document

        # Create CAG
        cag = CacheAugmentedRAG(cache_ttl=3600, max_cache_size=10000)

        # Warm cache with FAQs
        faqs = [
            (
                "What is LangGraph?",
                "LangGraph is a framework for building stateful AI applications.",
            ),
            (
                "What is RAG?",
                "RAG combines retrieval with generation for accurate answers.",
            ),
            (
                "How does caching help?",
                "Caching reduces latency and API costs by 166x for repeated queries.",
            ),
        ]

        print("\n🔥 Warming cache with FAQs...")
        cag.warm_cache(faqs)
        print(f"  ✓ Cached {len(faqs)} Q&A pairs")

        # Add documents for RAG fallback
        docs = [Document(page_content=text) for text in SAMPLE_DOCS]
        cag.add_documents(docs)

        # Test queries
        print("\n🔍 Testing queries...")

        test_queries = [
            "What is LangGraph?",  # Cache hit
            "What is RAG?",  # Cache hit
            "How do vector stores work?",  # Cache miss → RAG
            "What is LangGraph?",  # Cache hit again
        ]

        for q in test_queries:
            result = cag.query(q)
            status = "⚡ CACHE HIT" if result["cache_hit"] else "📦 RAG"
            print(f"\n  Q: {q}")
            print(f"  {status} ({result['latency_ms']:.1f}ms)")
            print(f"  A: {result['answer'][:80]}...")

        # Stats
        stats = cag.get_cache_stats()
        print("\n📊 Cache Stats:")
        print(f"  - Size: {stats['cache_size']}")
        print(f"  - Hits: {stats['cache_hits']}")
        print(f"  - Misses: {stats['cache_misses']}")
        print(f"  - Hit rate: {stats['hit_rate']}")
        print(f"  - Estimated savings: {stats['estimated_savings']}")

        print("\n✅ CAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 8: RAGFlow - Template Chunking
# =============================================================================


def example_ragflow():
    """
    RAGFlow: Template-based chunking.

    - Structure-aware chunking (headings, tables, lists)
    - Citation-backed answers
    - 100% open-source
    """
    print("\n" + "=" * 60)
    print("Example 8: RAGFlow - Template Chunking")
    print("=" * 60)

    try:
        from ai.rag.advanced import RAGFlowPipeline
        from langchain_core.documents import Document

        # Create RAGFlow
        ragflow = RAGFlowPipeline(chunk_by="structure", chunk_size=500)

        # Document with structure
        structured_doc = """# LangGraph Overview

LangGraph is a framework for building stateful applications.

## Key Features

- Dynamic routing with Command
- Human-in-the-loop with interrupt
- Checkpointing for persistence

## Architecture

| Component | Purpose |
|-----------|---------|
| StateGraph | Define workflow |
| Nodes | Processing steps |
| Edges | Flow control |

## Example Code

```python
from langgraph.graph import StateGraph
workflow = StateGraph(AgentState)
```
"""

        print("\n📚 Adding structured document...")
        doc = Document(page_content=structured_doc)
        ragflow.add_documents([doc])

        print(f"  ✓ Created {len(ragflow.chunks)} chunks")
        print(f"  ✓ Chunk types: {set(m['type'] for m in ragflow.chunk_metadata)}")

        # Query with citations
        print("\n🔍 Querying with citations...")

        result = ragflow.query("What are LangGraph features?")

        print("\n  Q: What are LangGraph features?")
        print(f"  A: {result['answer'][:200]}...")

        print("\n  📎 Citations:")
        for cite in result.get("citations", [])[:3]:
            print(f"     [{cite['id']}] ({cite['type']}): {cite['preview']}...")

        print("\n✅ RAGFlow example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all advanced RAG examples."""
    print("=" * 60)
    print("  Advanced RAG Examples")
    print("  December 2025 Research Techniques")
    print("=" * 60)

    print("\n📖 Techniques from forums/research:")
    print("  - REFRAG (Meta): 30x efficiency")
    print("  - CLaRa (Apple): 16x-128x compression")
    print("  - FB-RAG: Forward-Backward")
    print("  - Weak-to-Strong GraphRAG")
    print("  - Agentic RAG: 7 patterns")
    print("  - EntropyGuard: 40% dedup")
    print("  - CAG: 166x cheaper")
    print("  - RAGFlow: Template chunking")

    examples = [
        ("REFRAG (30x)", example_refrag),
        ("CLaRa (Apple)", example_clara),
        ("FB-RAG", example_fbrag),
        ("Weak-to-Strong", example_weak_to_strong),
        ("Agentic (7 patterns)", example_agentic_patterns),
        ("EntropyGuard", example_entropy_guard),
        ("CAG (166x)", example_cag),
        ("RAGFlow", example_ragflow),
    ]

    results = []

    for name, func in examples:
        try:
            success = func()
            results.append((name, "✅" if success else "⚠️"))
        except Exception as e:
            print(f"❌ {name} failed: {e}")
            results.append((name, "❌"))

    # Summary
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)

    for name, status in results:
        print(f"  {status} {name}")

    passed = sum(1 for _, s in results if s == "✅")
    print(f"\n  Total: {passed}/{len(results)} passed")

    # Efficiency summary
    print("\n📊 Efficiency Gains (from research):")
    print("  - REFRAG: 30x faster time-to-first-token")
    print("  - CLaRa: 85% latency reduction")
    print("  - FB-RAG: 48% latency reduction")
    print("  - CAG: 166x cost reduction")
    print("  - EntropyGuard: 40% storage reduction")


if __name__ == "__main__":
    main()
