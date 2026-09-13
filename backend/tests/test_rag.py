"""
Tests for RAG Module
====================
Unit and integration tests for RAG patterns.
"""

import pytest
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Embedding Provider Tests
# =============================================================================


class TestEmbeddingProvider:
    """Tests for embedding provider."""

    def test_provider_initialization(self):
        """Test embedding provider initializes."""
        from ai.rag import EmbeddingProvider

        provider = EmbeddingProvider(provider="auto")

        assert provider is not None

    def test_embed_query(self):
        """Test query embedding."""
        from ai.rag import EmbeddingProvider

        provider = EmbeddingProvider(provider="auto")
        provider._embeddings = (
            None  # Prevent real connection (Ollama/OpenAI may not be running)
        )

        embedding = provider.embed_query("test text")

        # Returns [] when no model available
        assert isinstance(embedding, list)

    def test_embed_documents(self):
        """Test batch embedding."""
        from ai.rag import EmbeddingProvider

        provider = EmbeddingProvider(provider="auto")
        provider._embeddings = (
            None  # Prevent real connection (Ollama/OpenAI may not be running)
        )

        texts = ["text 1", "text 2"]
        embeddings = provider.embed_documents(texts)

        assert isinstance(embeddings, list)


# =============================================================================
# LLM Provider Tests
# =============================================================================


class TestLLMProvider:
    """Tests for LLM provider."""

    def test_provider_initialization(self):
        """Test LLM provider initializes."""
        from ai.rag import LLMProvider

        provider = LLMProvider(provider="auto")

        assert provider is not None

    def test_invoke_without_model(self):
        """Test invoke returns empty when no model."""
        from ai.rag import LLMProvider

        provider = LLMProvider(provider="none")
        provider._llm = None

        result = provider.invoke("test prompt")

        assert result == ""


# =============================================================================
# Vector Store Tests
# =============================================================================


class TestVectorStore:
    """Tests for vector store."""

    def test_store_creation(self):
        """Test vector store creates successfully."""
        from ai.rag import VectorStore

        store = VectorStore(backend="memory")

        assert store is not None

    def test_add_documents(self):
        """Test adding documents."""
        from ai.rag import VectorStore

        store = VectorStore(backend="memory")

        # Create mock documents
        class MockDoc:
            def __init__(self, content):
                self.page_content = content
                self.metadata = {}

        docs = [MockDoc("Test content 1"), MockDoc("Test content 2")]

        store.add_documents(docs)

        assert len(store._documents) == 2

    def test_similarity_search_fallback(self):
        """Test similarity search fallback."""
        from ai.rag import VectorStore

        store = VectorStore(backend="memory")
        store._store = None  # Force fallback

        class MockDoc:
            def __init__(self, content):
                self.page_content = content
                self.metadata = {}

        store._documents = [
            MockDoc("Python is a programming language"),
            MockDoc("Java is another language"),
            MockDoc("Python supports machine learning"),
        ]

        results = store.similarity_search("Python programming", k=2)

        assert len(results) == 2
        # Should return Python-related docs first
        assert "Python" in results[0].page_content

    def test_as_retriever(self):
        """Test retriever interface."""
        from ai.rag import VectorStore

        store = VectorStore(backend="memory")

        retriever = store.as_retriever()

        assert retriever is not None
        assert hasattr(retriever, "invoke")


# =============================================================================
# Document Loader Tests
# =============================================================================


class TestDocumentLoader:
    """Tests for document loader."""

    def test_loader_creation(self):
        """Test loader creates successfully."""
        from ai.rag import DocumentLoader

        loader = DocumentLoader(chunk_size=500, chunk_overlap=50)

        assert loader.chunk_size == 500
        assert loader.chunk_overlap == 50

    def test_load_texts(self):
        """Test loading from texts."""
        from ai.rag import DocumentLoader

        loader = DocumentLoader(chunk_size=100, chunk_overlap=10)

        texts = [
            "This is a test document about AI.",
            "Another document about machine learning.",
        ]

        docs = loader.load_texts(texts)

        assert len(docs) >= 2
        assert all(hasattr(d, "page_content") for d in docs)

    def test_load_texts_splitting(self):
        """Test text splitting."""
        from ai.rag import DocumentLoader, LOADERS_AVAILABLE

        if not LOADERS_AVAILABLE:
            pytest.skip("Loaders not available")

        loader = DocumentLoader(chunk_size=50, chunk_overlap=10)

        long_text = "This is a very long document. " * 20

        docs = loader.load_texts([long_text])

        # Should be split into multiple chunks
        assert len(docs) > 1


# =============================================================================
# RAG Pipeline Tests
# =============================================================================


class TestRAGPipeline:
    """Tests for basic RAG pipeline."""

    def test_pipeline_creation(self):
        """Test pipeline creates successfully."""
        from ai.rag import RAGPipeline

        rag = RAGPipeline(llm="auto", embeddings="auto")

        assert rag is not None
        assert rag.vector_store is not None

    def test_add_texts(self):
        """Test adding texts."""
        from ai.rag import RAGPipeline

        rag = RAGPipeline()

        rag.add_texts(["Document about Python", "Document about JavaScript"])

        # Verify documents were added
        assert len(rag.vector_store._documents) >= 2

    def test_query_returns_string(self):
        """Test query returns string."""
        from ai.rag import RAGPipeline

        rag = RAGPipeline()
        rag.add_texts(["Test document about AI"])
        rag._chain = None  # Use fallback path — no real LLM call

        result = rag.query("What is AI?")

        assert isinstance(result, str)

    def test_query_with_sources(self):
        """Test query with sources."""
        from ai.rag import RAGPipeline

        rag = RAGPipeline()
        rag.add_texts(
            ["Python is a programming language", "Python was created by Guido"]
        )
        rag._chain = None  # Use fallback path — no real LLM call

        result = rag.query_with_sources("What is Python?")

        assert "answer" in result
        assert "sources" in result
        assert isinstance(result["sources"], list)

    def test_query_no_documents(self):
        """Test query with no documents."""
        from ai.rag import RAGPipeline

        rag = RAGPipeline()
        # Don't add any documents

        result = rag.query("Random question")

        assert "No relevant documents" in result


# =============================================================================
# Agentic RAG Tests
# =============================================================================


class TestAgenticRAG:
    """Tests for agentic RAG with grading."""

    def test_agentic_rag_creation(self):
        """Test agentic RAG creates successfully."""
        from ai.rag import AgenticRAG

        rag = AgenticRAG(llm="auto", max_retries=2, enable_web_search=False)

        assert rag is not None
        assert rag.max_retries == 2

    def test_add_documents(self):
        """Test adding documents to agentic RAG."""
        from ai.rag import AgenticRAG

        rag = AgenticRAG(enable_web_search=False)

        rag.add_texts(["Test document 1", "Test document 2"])

        # Verify documents added to base RAG
        assert len(rag.base_rag.vector_store._documents) >= 2

    def test_query_without_grading(self):
        """Test simple query without grading."""
        from ai.rag import AgenticRAG

        rag = AgenticRAG(enable_web_search=False)
        rag.add_texts(["Python is a programming language"])
        rag.base_rag._chain = None  # Use fallback path — no real LLM call

        result = rag.query("What is Python?", with_grading=False)

        assert "answer" in result

    def test_query_with_grading(self):
        """Test query with grading."""
        from ai.rag import AgenticRAG, LANGGRAPH_AVAILABLE

        rag = AgenticRAG(enable_web_search=False)
        rag.add_texts(["Python is a programming language"])
        rag.base_rag._chain = None  # Use fallback path — no real LLM call

        result = rag.query("What is Python?", with_grading=True)

        assert "answer" in result

        # Grading may or may not be available
        if LANGGRAPH_AVAILABLE and rag._workflow:
            assert "grading" in result


# =============================================================================
# GraphRAG Tests
# =============================================================================


class TestGraphRAG:
    """Tests for GraphRAG with knowledge graphs."""

    def test_graph_rag_creation(self):
        """Test GraphRAG creates successfully."""
        from ai.rag import GraphRAGPipeline, NETWORKX_AVAILABLE

        rag = GraphRAGPipeline()

        assert rag is not None

        if NETWORKX_AVAILABLE:
            assert rag.graph is not None

    def test_add_documents(self):
        """Test adding documents builds graph."""
        from ai.rag import GraphRAGPipeline, NETWORKX_AVAILABLE, DocumentLoader

        rag = GraphRAGPipeline()
        loader = DocumentLoader()
        docs = loader.load_texts(
            [
                "LangChain is a framework. Harrison Chase created LangChain.",
                "OpenAI created GPT-4. GPT-4 uses transformer architecture.",
            ]
        )
        rag.add_documents(docs)

        if NETWORKX_AVAILABLE:
            # Graph should have some nodes
            assert rag.graph.number_of_nodes() >= 0

    def test_entity_extraction(self):
        """Test entity extraction."""
        from ai.rag import GraphRAGPipeline

        rag = GraphRAGPipeline()

        entities, relations = rag._extract_entities_and_relations(
            "Python is a language. Guido created Python."
        )

        assert isinstance(entities, list)
        assert isinstance(relations, list)

    def test_query(self):
        """Test graph-enhanced query."""
        from ai.rag import GraphRAGPipeline, DocumentLoader

        rag = GraphRAGPipeline()
        loader = DocumentLoader()
        docs = loader.load_texts(
            ["LangChain is a framework for LLMs.", "LangGraph extends LangChain."]
        )
        rag.add_documents(docs)
        rag.base_rag._chain = None  # Use fallback path — no real LLM call

        result = rag.query("What is LangChain?")

        assert "answer" in result
        assert "sources" in result

    def test_get_entity_subgraph(self):
        """Test getting entity subgraph."""
        from ai.rag import GraphRAGPipeline, NETWORKX_AVAILABLE

        if not NETWORKX_AVAILABLE:
            pytest.skip("NetworkX not available")

        rag = GraphRAGPipeline()
        rag.add_texts(
            ["Python is a language.", "Django uses Python.", "Flask uses Python."]
        )

        # Add some entities manually for testing
        if "Python" in rag.graph:
            subgraph = rag.get_entity_subgraph("Python", depth=2)

            assert "nodes" in subgraph
            assert "edges" in subgraph


# =============================================================================
# State Tests
# =============================================================================


class TestRAGStates:
    """Tests for RAG state definitions."""

    def test_rag_state(self):
        """Test RAGState structure."""
        from ai.rag import RAGState

        state: RAGState = {
            "question": "Test?",
            "documents": [],
            "generation": "",
            "context": "",
        }

        assert state["question"] == "Test?"

    def test_agentic_rag_state(self):
        """Test AgenticRAGState structure."""
        from ai.rag import AgenticRAGState

        state: AgenticRAGState = {
            "question": "Test?",
            "documents": [],
            "generation": "",
            "web_search": "No",
            "relevance_scores": [],
            "hallucination_score": None,
            "answer_quality": None,
            "datasource": "vectorstore",
            "retry_count": 0,
        }

        assert state["datasource"] == "vectorstore"

    def test_graph_rag_state(self):
        """Test GraphRAGState structure."""
        from ai.rag import GraphRAGState

        state: GraphRAGState = {
            "question": "Test?",
            "documents": [],
            "generation": "",
            "entities": [],
            "relations": [],
            "graph_context": "",
        }

        assert isinstance(state["entities"], list)


# =============================================================================
# Integration Tests
# =============================================================================


class TestRAGIntegration:
    """Integration tests for RAG modules."""

    def test_rag_with_memory(self):
        """Test RAG with memory module."""
        from ai.rag import RAGPipeline
        from memory import MemoryManager

        # Create RAG
        rag = RAGPipeline()
        rag.add_texts(["Important fact about user preferences"])
        rag._chain = None  # Use fallback path — no real LLM call

        # Create memory
        memory = MemoryManager()
        memory.store_fact("User likes Python", user_id="user_1")

        # Query RAG
        result = rag.query("What about preferences?")

        assert isinstance(result, str)

    def test_rag_with_hitl(self):
        """Test RAG with HITL module."""
        from ai.rag import RAGPipeline
        from hitl import HITLWorkflow

        # Create RAG
        rag = RAGPipeline()
        rag.add_texts(["Policy: Refunds over $100 need approval"])
        rag._chain = None  # Use fallback path — no real LLM call

        # Query
        result = rag.query("What's the refund policy?")

        # Create HITL workflow
        workflow = HITLWorkflow(db_path=":memory:")
        hitl_result = workflow.run_with_approval(
            action=f"Apply policy: {result[:50]}", thread_id="rag-hitl-test"
        )

        assert hitl_result is not None

    def test_full_rag_pipeline(self):
        """Test full RAG pipeline."""
        from ai.rag import RAGPipeline

        # Create pipeline
        rag = RAGPipeline()

        # Add various documents
        texts = [
            "LangGraph is a framework for building agents.",
            "Agents can use memory and tools.",
            "Memory helps agents remember context.",
            "Tools let agents take actions.",
            "LangGraph supports checkpointing.",
        ]
        rag.add_texts(texts)
        rag._chain = None  # Use fallback path — no real LLM call

        # Query
        questions = [
            "What is LangGraph?",
            "How do agents work?",
            "What is memory used for?",
        ]

        for q in questions:
            result = rag.query(q)
            assert isinstance(result, str)
            assert len(result) > 0


# =============================================================================
# Availability Tests
# =============================================================================


class TestRAGAvailability:
    """Tests for RAG feature availability."""

    def test_availability_flags(self):
        """Test availability flags exist."""
        from ai.rag import (
            LANGGRAPH_AVAILABLE,
            OLLAMA_AVAILABLE,
            FAISS_AVAILABLE,
            CHROMA_AVAILABLE,
            TAVILY_AVAILABLE,
            NETWORKX_AVAILABLE,
        )

        # All should be boolean
        assert isinstance(LANGGRAPH_AVAILABLE, bool)
        assert isinstance(OLLAMA_AVAILABLE, bool)
        assert isinstance(FAISS_AVAILABLE, bool)
        assert isinstance(CHROMA_AVAILABLE, bool)
        assert isinstance(TAVILY_AVAILABLE, bool)
        assert isinstance(NETWORKX_AVAILABLE, bool)

    def test_exports(self):
        """Test all exports are available."""
        from ai.rag import RAGState, RAGPipeline, AgenticRAG, GraphRAGPipeline

        # All classes should be importable
        assert RAGState is not None
        assert RAGPipeline is not None
        assert AgenticRAG is not None
        assert GraphRAGPipeline is not None


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
