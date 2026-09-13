"""
RAG Examples - Advanced Patterns
================================
Production examples for Retrieval-Augmented Generation.

Examples:
1. Basic RAG with local vector store
2. Agentic RAG with grading
3. GraphRAG with knowledge graphs
4. Multi-source RAG (docs + web)
5. Cached RAG for efficiency

Run:
    python examples/rag_examples.py
"""

import os
import sys
import time

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Example 1: Basic RAG with Local Vector Store
# =============================================================================


def example_basic_rag():
    """
    Basic RAG with FAISS/Chroma and local/cloud LLM.

    Pattern: Load docs → Split → Embed → Store → Retrieve → Generate
    """
    print("\n" + "=" * 60)
    print("Example 1: Basic RAG with Local Vector Store")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline, FAISS_AVAILABLE, OLLAMA_AVAILABLE

        print(f"  FAISS Available: {FAISS_AVAILABLE}")
        print(f"  Ollama Available: {OLLAMA_AVAILABLE}")

        # Create RAG pipeline
        rag = RAGPipeline(
            llm="auto",  # Will use Ollama if available
            embeddings="auto",
            vector_store="auto",
        )

        # Add sample documents
        sample_docs = [
            "LangGraph is a framework for building stateful, multi-actor applications with LLMs.",
            "Agents in LangGraph can have memory that persists across conversations.",
            "LangGraph supports checkpointing for fault-tolerant workflows.",
            "The Command primitive allows dynamic routing between nodes.",
            "Human-in-the-loop patterns are supported via the interrupt function.",
            "LangGraph integrates with LangSmith for observability and debugging.",
        ]

        print("\n📚 Adding documents...")
        rag.add_texts(sample_docs)
        print(f"  ✓ Added {len(sample_docs)} documents")

        # Query
        questions = [
            "What is LangGraph?",
            "How does memory work in LangGraph?",
            "What is Command used for?",
        ]

        print("\n🔍 Querying RAG...")
        for question in questions:
            print(f"\n  Q: {question}")
            answer = rag.query(question, k=2)
            print(f"  A: {answer[:200]}...")

        print("\n✅ Basic RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 2: Agentic RAG with Grading
# =============================================================================


def example_agentic_rag():
    """
    Agentic RAG with document grading and hallucination detection.

    Pattern: Route → Retrieve → Grade → Generate → Validate → Retry/End
    """
    print("\n" + "=" * 60)
    print("Example 2: Agentic RAG with Grading")
    print("=" * 60)

    try:
        from ai.rag import AgenticRAG, TAVILY_AVAILABLE

        print(f"  Web Search (Tavily) Available: {TAVILY_AVAILABLE}")

        # Create Agentic RAG
        rag = AgenticRAG(
            llm="auto",
            max_retries=2,
            enable_web_search=False,  # Disable for demo without API key
        )

        # Add documents
        docs = [
            "Python is a programming language known for its simplicity and readability.",
            "Python was created by Guido van Rossum and released in 1991.",
            "Python supports multiple programming paradigms including procedural and OOP.",
            "Popular Python frameworks include Django, Flask, and FastAPI.",
            "Machine learning libraries like TensorFlow and PyTorch are written in Python.",
        ]

        print("\n📚 Adding documents...")
        rag.add_texts(docs)

        # Query with grading
        print("\n🔍 Querying with grading...")

        question = "What is Python and who created it?"
        print(f"\n  Q: {question}")

        result = rag.query(question, with_grading=True)

        print(f"\n  Answer: {result.get('answer', 'N/A')[:200]}...")

        if "grading" in result:
            grading = result["grading"]
            print("\n  📊 Grading Results:")
            print(
                f"     - Relevance scores: {len(grading.get('relevance_scores', []))} docs graded"
            )
            print(
                f"     - Hallucination check: {grading.get('hallucination_score', 'N/A')}"
            )
            print(f"     - Answer quality: {grading.get('answer_quality', 'N/A')}")
            print(f"     - Used web search: {grading.get('used_web_search', False)}")

        print(f"\n  Sources used: {result.get('sources', 0)}")

        print("\n✅ Agentic RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 3: GraphRAG with Knowledge Graphs
# =============================================================================


def example_graph_rag():
    """
    GraphRAG: RAG enhanced with knowledge graphs.

    Pattern: Extract entities → Build graph → Graph-enhanced retrieval
    """
    print("\n" + "=" * 60)
    print("Example 3: GraphRAG with Knowledge Graphs")
    print("=" * 60)

    try:
        from ai.rag import GraphRAGPipeline, NETWORKX_AVAILABLE

        print(f"  NetworkX Available: {NETWORKX_AVAILABLE}")

        # Create GraphRAG
        graph_rag = GraphRAGPipeline(llm="auto")

        # Add documents with entity relationships
        docs = [
            "LangChain is a framework for building LLM applications. Harrison Chase created LangChain.",
            "LangGraph is built on top of LangChain. LangGraph uses StateGraph for workflows.",
            "Anthropic created Claude, an AI assistant. Claude uses Constitutional AI.",
            "OpenAI created ChatGPT and GPT-4. GPT-4 uses transformer architecture.",
            "Hugging Face provides transformers library. Transformers library supports BERT and GPT models.",
        ]

        print("\n📚 Adding documents and building knowledge graph...")
        graph_rag.add_texts(docs)

        if graph_rag.graph:
            print(f"  ✓ Graph has {graph_rag.graph.number_of_nodes()} nodes")
            print(f"  ✓ Graph has {graph_rag.graph.number_of_edges()} edges")

        # Query with graph enhancement
        print("\n🔍 Graph-enhanced queries...")

        questions = [
            "What is the relationship between LangChain and LangGraph?",
            "Who created Claude?",
            "What models does Hugging Face support?",
        ]

        for question in questions:
            print(f"\n  Q: {question}")
            result = graph_rag.query(question)

            print(f"  A: {result.get('answer', 'N/A')[:150]}...")

            if result.get("graph_entities"):
                print(
                    f"  🔗 Related entities: {', '.join(result['graph_entities'][:5])}"
                )

        # Get entity subgraph
        if graph_rag.graph and "LangChain" in graph_rag.entities:
            print("\n📊 Entity subgraph for 'LangChain':")
            subgraph = graph_rag.get_entity_subgraph("LangChain", depth=2)
            print(f"  Nodes: {subgraph.get('nodes', [])}")
            print(f"  Edges: {len(subgraph.get('edges', []))} relationships")

        print("\n✅ GraphRAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 4: RAG with Web Sources
# =============================================================================


def example_web_rag():
    """
    RAG with web page loading.

    Pattern: Load web pages → Split → Embed → Query
    """
    print("\n" + "=" * 60)
    print("Example 4: RAG with Web Sources")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline, DocumentLoader, LOADERS_AVAILABLE

        print(f"  Web Loader Available: {LOADERS_AVAILABLE}")

        if not LOADERS_AVAILABLE:
            print("  ⚠️ Web loading requires: pip install beautifulsoup4 lxml")
            print("  Using sample texts instead...")

            # Fallback to sample texts
            rag = RAGPipeline()
            rag.add_texts(
                [
                    "This is a sample document about AI agents.",
                    "Agents can use tools and have memory.",
                ]
            )

            result = rag.query("What are agents?")
            print(f"\n  Sample result: {result[:100]}...")
            return True

        # Create loader
        loader = DocumentLoader(chunk_size=500, chunk_overlap=50)

        # Load from web (example URL)
        print("\n🌐 Loading web pages...")

        # Note: In production, use actual URLs
        # For demo, we'll use sample text
        sample_web_content = """
        LangGraph is a library for building stateful, multi-actor applications with LLMs.
        It extends the LangChain Expression Language with the ability to coordinate 
        multiple chains (or actors) across multiple steps of computation.
        
        Key features include:
        - Cycles and branching in workflows
        - Persistence for long-running conversations
        - Human-in-the-loop interactions
        - Streaming support for real-time updates
        """

        docs = loader.load_texts([sample_web_content])
        print(f"  ✓ Loaded {len(docs)} document chunks")

        # Create RAG
        rag = RAGPipeline()
        rag.add_documents(docs)

        # Query
        question = "What are the key features of LangGraph?"
        print(f"\n  Q: {question}")

        result = rag.query_with_sources(question)
        print(f"  A: {result['answer'][:200]}...")
        print(f"  Sources: {len(result['sources'])} documents")

        print("\n✅ Web RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 5: Cached RAG for Efficiency
# =============================================================================


def example_cached_rag():
    """
    RAG with caching for improved efficiency.

    Pattern: Cache embeddings → Cache retrievals → Cache generations
    """
    print("\n" + "=" * 60)
    print("Example 5: Cached RAG for Efficiency")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        import hashlib

        # Simple cache implementation
        _cache = {}

        def cached_query(rag, question, cache_ttl=3600):
            """Query with caching."""
            cache_key = hashlib.md5(question.encode()).hexdigest()

            if cache_key in _cache:
                print("    ⚡ Cache HIT")
                return _cache[cache_key]

            print("    💾 Cache MISS - querying...")
            result = rag.query(question)
            _cache[cache_key] = result
            return result

        # Create RAG
        rag = RAGPipeline()
        rag.add_texts(
            [
                "Caching improves performance by storing computed results.",
                "Embedding caching reduces redundant API calls.",
                "Query caching helps with repeated questions.",
            ]
        )

        # Test caching
        print("\n🔄 Testing caching...")

        question = "How does caching help performance?"

        # First query (cache miss)
        print(f"\n  Query 1: {question}")
        start = time.time()
        result1 = cached_query(rag, question)
        time1 = time.time() - start
        print(f"    Time: {time1:.3f}s")
        print(f"    Result: {result1[:100]}...")

        # Second query (cache hit)
        print(f"\n  Query 2: {question}")
        start = time.time()
        result2 = cached_query(rag, question)
        time2 = time.time() - start
        print(f"    Time: {time2:.3f}s")
        print(f"    Result: {result2[:100]}...")

        # Stats
        print("\n📊 Cache Stats:")
        print(f"    Speed improvement: {((time1 - time2) / time1 * 100):.1f}%")
        print(f"    Cache entries: {len(_cache)}")

        print("\n✅ Cached RAG example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 6: RAG with HITL Integration
# =============================================================================


def example_rag_with_hitl():
    """
    RAG integrated with Human-in-the-Loop.

    Pattern: Retrieve → Generate → Human Review → Approve/Edit
    """
    print("\n" + "=" * 60)
    print("Example 6: RAG with HITL Integration")
    print("=" * 60)

    try:
        from ai.rag import RAGPipeline
        from hitl import HITLWorkflow, HITL_AVAILABLE

        print(f"  HITL Available: {HITL_AVAILABLE}")

        # Create RAG
        rag = RAGPipeline()
        rag.add_texts(
            [
                "Customer refunds require manager approval for amounts over $100.",
                "Premium customers get priority support within 1 hour.",
                "All data changes must be logged and reviewed.",
            ]
        )

        # Simulate RAG + HITL workflow
        print("\n📋 RAG + HITL Workflow:")

        # Step 1: User question
        question = "Should I approve a $500 refund for customer John?"
        print(f"\n  1️⃣ User Question: {question}")

        # Step 2: RAG retrieval
        print("\n  2️⃣ RAG Retrieval...")
        result = rag.query_with_sources(question)
        print(f"     Retrieved: {len(result['sources'])} relevant policies")

        # Step 3: Generate recommendation
        print("\n  3️⃣ AI Recommendation:")
        print(f"     {result['answer'][:150]}...")

        # Step 4: HITL approval
        print("\n  4️⃣ Human Review Required:")
        print("     Action: Approve $500 refund")
        print("     Policies: Manager approval required for >$100")

        # Simulate workflow
        workflow = HITLWorkflow(db_path=":memory:")
        hitl_result = workflow.run_with_approval(
            action="Approve $500 refund based on RAG recommendation",
            thread_id="rag-hitl-demo",
        )

        print("\n  5️⃣ Workflow Result:")
        print(f"     Status: {hitl_result.get('status')}")
        print(f"     Approval: {hitl_result.get('approval_status')}")

        print("\n✅ RAG + HITL example completed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all RAG examples."""
    print("=" * 60)
    print("  Advanced RAG Examples")
    print("  Retrieval-Augmented Generation Patterns")
    print("=" * 60)

    examples = [
        ("Basic RAG", example_basic_rag),
        ("Agentic RAG with Grading", example_agentic_rag),
        ("GraphRAG", example_graph_rag),
        ("Web RAG", example_web_rag),
        ("Cached RAG", example_cached_rag),
        ("RAG + HITL", example_rag_with_hitl),
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


if __name__ == "__main__":
    main()
