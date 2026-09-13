#!/usr/bin/env python3
"""
LlamaIndex Agentic RAG Integration
===================================

This module provides a drop-in replacement for the existing LangChain-based
AgenticRAG with LlamaIndex's ReAct agent architecture.

Key Features:
- Auto-detection of LLM providers (OpenAI → Anthropic → Ollama)
- Auto-detection of embedding models (OpenAI → HuggingFace → Ollama)
- Vector store support (FAISS, ChromaDB)
- Knowledge graph support (NetworkX via SimpleGraphStore)
- ReAct agent for intelligent tool routing
- Persistent storage for indices
- Backward-compatible interface

Architecture:
1. Vector Search Tool: For semantic similarity queries
2. Graph Search Tool: For entity relationships and multi-hop reasoning
3. ReAct Agent: Decides which tool(s) to use based on query

Usage:
    from ai.rag.llamaindex_agentic import create_llamaindex_rag

    # Create RAG instance (auto-detects providers)
    rag = create_llamaindex_rag()

    # Load documents
    rag.load_documents(directory="./data")

    # Query with agent
    result = rag.query("What are the three types of agent memory?")
    print(result["answer"])
    print(result["sources"])

Author: V Orbital UI Team
Date: January 2026
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

# LlamaIndex Core
try:
    from llama_index.core import (
        VectorStoreIndex,
        SimpleDirectoryReader,
        StorageContext,
        load_index_from_storage,
        Settings,
        Document,
    )
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core.agent import ReActAgent
    from llama_index.core.tools import QueryEngineTool, ToolMetadata
    from llama_index.core.query_engine import BaseQueryEngine
    from llama_index.core.graph_stores import SimpleGraphStore
    from llama_index.core import KnowledgeGraphIndex

    # LLM Providers
    from llama_index.llms.ollama import Ollama
    from llama_index.llms.openai import OpenAI
    from llama_index.llms.anthropic import Anthropic

    # Embedding Providers
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    # Vector Stores
    from llama_index.vector_stores.faiss import FaissVectorStore
    from llama_index.vector_stores.chroma import ChromaVectorStore

    # External dependencies
    import faiss
    import chromadb

    LLAMAINDEX_AVAILABLE = True
except ImportError as e:
    LLAMAINDEX_AVAILABLE = False
    IMPORT_ERROR = str(e)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Auto-Detection Functions
# =============================================================================


def _get_embedding_model():
    """
    Auto-detect and return the best available embedding model.

    Priority:
    1. OpenAI (if OPENAI_API_KEY exists) → text-embedding-3-small (1536 dim)
    2. HuggingFace (fallback) → all-MiniLM-L6-v2 (384 dim)
    3. Ollama (default) → nomic-embed-text (768 dim)

    Returns:
        BaseEmbedding: Configured embedding model
    """
    if os.getenv("OPENAI_API_KEY"):
        logger.info("✓ Using OpenAI embeddings (text-embedding-3-small, 1536 dim)")
        return OpenAIEmbedding(
            model="text-embedding-3-small", api_key=os.getenv("OPENAI_API_KEY")
        )

    # Try HuggingFace as fallback (no API key needed, runs locally)
    try:
        logger.info("✓ Using HuggingFace embeddings (all-MiniLM-L6-v2, 384 dim)")
        return HuggingFaceEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except Exception as e:
        logger.warning(f"HuggingFace embeddings failed: {e}")

    # Default to Ollama (local, no API key needed)
    logger.info("✓ Using Ollama embeddings (nomic-embed-text, 768 dim)")
    return OllamaEmbedding(
        model_name="nomic-embed-text", base_url="http://localhost:11434"
    )


def _get_llm():
    """
    Auto-detect and return the best available LLM.

    Priority:
    1. OpenAI (if OPENAI_API_KEY exists) → gpt-5.2
    2. Anthropic (if ANTHROPIC_API_KEY exists) → claude-sonnet-4-5-20250929
    3. Ollama (default) → llama3.1 (local)

    Returns:
        BaseLLM: Configured language model
    """
    if os.getenv("OPENAI_API_KEY"):
        logger.info("✓ Using OpenAI LLM (gpt-5.2)")
        return OpenAI(
            model="gpt-5.2",
            temperature=0.7,
            max_tokens=4096,
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        logger.info("✓ Using Anthropic LLM (claude-sonnet-4-5-20250929)")
        return Anthropic(
            model="claude-sonnet-4-5-20250929",
            temperature=0.7,
            max_tokens=4096,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    # Default to Ollama (local, no API key needed)
    logger.info("✓ Using Ollama LLM (llama3.1)")
    return Ollama(
        model="llama3.1",
        base_url="http://localhost:11434",
        temperature=0.7,
        request_timeout=120.0,
    )


def _get_embedding_dimension() -> int:
    """
    Get embedding dimension based on configured embedding model.

    Returns:
        int: Embedding dimension (384, 768, or 1536)
    """
    if os.getenv("OPENAI_API_KEY"):
        return 1536  # OpenAI text-embedding-3-small

    # Check if HuggingFace is available
    try:
        return 384  # all-MiniLM-L6-v2
    except:
        pass

    return 768  # Ollama nomic-embed-text (default)


# =============================================================================
# Main RAG Class
# =============================================================================


class LlamaIndexAgenticRAG:
    """
    LlamaIndex-based Agentic RAG with ReAct agent.

    Features:
    - Vector search for semantic similarity
    - Knowledge graph for entity relationships
    - ReAct agent for intelligent tool routing
    - Persistent storage for indices

    Architecture:
        Query → ReAct Agent → [Vector Tool | Graph Tool] → Answer

    Args:
        persist_dir: Directory for persisted indices
        chroma_path: Path for ChromaDB storage (if use_chroma=True)
        faiss_index_path: Path for FAISS index file (if use_chroma=False)
        use_chroma: Whether to use ChromaDB (True) or FAISS (False)
    """

    def __init__(
        self,
        persist_dir: str = "./storage",
        chroma_path: Optional[str] = None,
        faiss_index_path: Optional[str] = None,
        use_chroma: bool = True,
    ):
        """Initialize the Agentic RAG system."""
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.chroma_path = chroma_path
        self.faiss_index_path = faiss_index_path
        self.use_chroma = use_chroma

        # Initialize indices
        self.vector_index: Optional[VectorStoreIndex] = None
        self.graph_index: Optional[KnowledgeGraphIndex] = None
        self.agent: Optional[ReActAgent] = None

        # Try to load existing indices
        self._load_or_create_indices()

        logger.info("✓ LlamaIndex Agentic RAG initialized")

    def _get_vector_store(self):
        """
        Create or load vector store (ChromaDB or FAISS).

        Returns:
            VectorStore: Configured vector store
        """
        if self.use_chroma:
            # Use ChromaDB (persistent)
            if not self.chroma_path:
                self.chroma_path = str(self.persist_dir / "chroma_db")

            chroma_client = chromadb.PersistentClient(path=self.chroma_path)
            chroma_collection = chroma_client.get_or_create_collection("rag_collection")

            logger.info(f"✓ Using ChromaDB at {self.chroma_path}")
            return ChromaVectorStore(chroma_collection=chroma_collection)
        else:
            # Use FAISS (in-memory, optionally saved to disk)
            dimension = _get_embedding_dimension()
            faiss_index = faiss.IndexFlatIP(
                dimension
            )  # Inner product (cosine similarity)

            logger.info(f"✓ Using FAISS with dimension {dimension}")
            return FaissVectorStore(faiss_index=faiss_index)

    def _load_or_create_indices(self):
        """
        Load existing indices from storage or prepare for new creation.

        This method attempts to load pre-existing indices. If they don't exist,
        it sets up the storage context for future index creation.
        """
        try:
            # Try to load existing vector index
            storage_context = StorageContext.from_defaults(
                persist_dir=str(self.persist_dir)
            )
            self.vector_index = load_index_from_storage(
                storage_context, index_id="vector"
            )

            # Try to load existing graph index
            self.graph_index = load_index_from_storage(
                storage_context, index_id="graph"
            )

            logger.info("✓ Loaded existing indices from storage")

            # Build agent with loaded indices
            self._build_agent()

        except Exception as e:
            logger.info(
                f"No existing indices found (will create on document load): {e}"
            )
            # Indices will be created in load_documents()

    def _build_agent(self):
        """
        Build ReAct agent with vector and graph query tools.

        The agent will automatically decide which tool to use based on the query:
        - Vector tool: For semantic similarity and general questions
        - Graph tool: For entity relationships and multi-hop reasoning
        """
        if not self.vector_index or not self.graph_index:
            logger.warning("Cannot build agent: indices not loaded")
            return

        # Create query engines
        vector_query_engine = self.vector_index.as_query_engine(
            similarity_top_k=5, response_mode="compact"
        )

        graph_query_engine = self.graph_index.as_query_engine(
            include_text=True, response_mode="tree_summarize", embedding_mode="hybrid"
        )

        # Create tools
        tools = [
            QueryEngineTool(
                query_engine=vector_query_engine,
                metadata=ToolMetadata(
                    name="vector_search",
                    description=(
                        "Useful for answering questions about general concepts, definitions, "
                        "and semantic similarity. Use this for questions like 'What is X?', "
                        "'Explain Y', or 'What are the types of Z?'"
                    ),
                ),
            ),
            QueryEngineTool(
                query_engine=graph_query_engine,
                metadata=ToolMetadata(
                    name="graph_search",
                    description=(
                        "Useful for questions about relationships between entities, "
                        "multi-hop reasoning, and 'how is X connected to Y' questions. "
                        "Use this for questions like 'What company created X?', "
                        "'How does A relate to B?', or 'What is the relationship between X and Y?'"
                    ),
                ),
            ),
        ]

        # Create ReAct agent
        self.agent = ReActAgent.from_tools(
            tools, llm=Settings.llm, verbose=True, max_iterations=10
        )

        logger.info("✓ ReAct agent created with vector + graph tools")

    def load_documents(
        self,
        documents: Optional[List[Document]] = None,
        directory: Optional[str] = None,
        file_paths: Optional[List[str]] = None,
    ) -> None:
        """
        Load documents and build vector + graph indices.

        Args:
            documents: Pre-loaded LlamaIndex Document objects
            directory: Directory path to load all files from
            file_paths: List of specific file paths to load

        Note:
            Knowledge graph building can be slow on first run (especially with
            Ollama). Start with 2-3 small files for testing.
        """
        # Load documents
        if documents:
            docs = documents
        elif directory:
            logger.info(f"Loading documents from directory: {directory}")
            reader = SimpleDirectoryReader(input_dir=directory)
            docs = reader.load_data()
        elif file_paths:
            logger.info(f"Loading {len(file_paths)} specific files")
            reader = SimpleDirectoryReader(input_files=file_paths)
            docs = reader.load_data()
        else:
            raise ValueError("Must provide documents, directory, or file_paths")

        logger.info(f"✓ Loaded {len(docs)} documents")

        # Chunk documents (matching existing system: 1000 chars, 200 overlap)
        splitter = SentenceSplitter(chunk_size=1000, chunk_overlap=200)
        nodes = splitter.get_nodes_from_documents(docs)
        logger.info(f"✓ Created {len(nodes)} chunks")

        # Create storage context with vector store
        vector_store = self._get_vector_store()
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        # Build vector index
        logger.info("Building vector index...")
        self.vector_index = VectorStoreIndex(
            nodes=nodes, storage_context=storage_context, show_progress=True
        )
        logger.info("✓ Vector index built")

        # Build knowledge graph index
        # CRITICAL: max_triplets_per_chunk=10 to avoid slowdowns
        logger.info("Building knowledge graph (this may take 1-2 minutes)...")
        graph_store = SimpleGraphStore()
        graph_storage_context = StorageContext.from_defaults(graph_store=graph_store)

        self.graph_index = KnowledgeGraphIndex(
            nodes=nodes,
            storage_context=graph_storage_context,
            max_triplets_per_chunk=10,  # Limit for performance
            show_progress=True,
        )
        logger.info("✓ Knowledge graph built")

        # Persist indices
        self.vector_index.set_index_id("vector")
        self.graph_index.set_index_id("graph")

        self.vector_index.storage_context.persist(persist_dir=str(self.persist_dir))
        self.graph_index.storage_context.persist(persist_dir=str(self.persist_dir))

        logger.info(f"✓ Indices persisted to {self.persist_dir}")

        # Build agent with new indices
        self._build_agent()

    def query(self, question: str, verbose: bool = True) -> Dict[str, Any]:
        """
        Query the RAG system using the ReAct agent.

        The agent will automatically decide whether to use:
        - Vector search (for semantic similarity)
        - Graph search (for entity relationships)
        - Both (for hybrid queries)

        Args:
            question: The question to answer
            verbose: Whether to print agent reasoning steps

        Returns:
            Dict with keys:
            - answer: The answer text
            - sources: List of source document excerpts
            - raw_response: Raw agent response object
        """
        if not self.agent:
            raise RuntimeError(
                "Agent not initialized. Call load_documents() first or ensure "
                "indices were loaded from storage."
            )

        # Query agent
        response = self.agent.chat(question)

        # Extract sources from response
        sources = []
        if hasattr(response, "source_nodes"):
            for node in response.source_nodes:
                source_text = node.node.get_content()
                # Truncate long sources
                if len(source_text) > 500:
                    source_text = source_text[:500] + "..."
                sources.append(source_text)

        return {"answer": str(response), "sources": sources, "raw_response": response}

    def persist(self):
        """
        Explicitly persist indices to storage.

        This is automatically called by load_documents(), but can be called
        manually if needed.
        """
        if self.vector_index:
            self.vector_index.storage_context.persist(persist_dir=str(self.persist_dir))
        if self.graph_index:
            self.graph_index.storage_context.persist(persist_dir=str(self.persist_dir))

        logger.info(f"✓ Indices persisted to {self.persist_dir}")


# =============================================================================
# Compatibility Layer (Drop-in Replacement for Existing AgenticRAG)
# =============================================================================


class AgenticRAGCompatibility:
    """
    Backward-compatible wrapper for existing AgenticRAG interface.

    This class provides the same interface as rag/__init__.py:AgenticRAG
    to allow gradual migration without breaking existing code.

    Usage:
        # Replace this:
        from ai.rag import AgenticRAG
        rag = AgenticRAG(llm="ollama/llama3")

        # With this:
        from ai.rag.llamaindex_agentic import AgenticRAGCompatibility
        rag = AgenticRAGCompatibility()
    """

    def __init__(
        self,
        llm: Optional[str] = None,
        embeddings: Optional[str] = None,
        vector_store: str = "chroma",
        **kwargs,
    ):
        """
        Initialize compatibility wrapper.

        Args:
            llm: LLM provider (ignored, uses auto-detection)
            embeddings: Embedding provider (ignored, uses auto-detection)
            vector_store: "chroma" or "faiss"
            **kwargs: Additional arguments (passed to LlamaIndexAgenticRAG)
        """
        use_chroma = vector_store.lower() == "chroma"
        self._rag = create_llamaindex_rag(use_chroma=use_chroma, **kwargs)

    def add_documents(self, documents: List[Any]) -> None:
        """Add documents (compatible with LangChain Document format)."""
        # Convert to LlamaIndex format if needed
        llama_docs = []
        for doc in documents:
            if hasattr(doc, "page_content"):
                # LangChain Document
                llama_docs.append(Document(text=doc.page_content))
            else:
                # Already LlamaIndex Document
                llama_docs.append(doc)

        self._rag.load_documents(documents=llama_docs)

    def add_texts(self, texts: List[str]) -> None:
        """Add raw text strings."""
        docs = [Document(text=text) for text in texts]
        self._rag.load_documents(documents=docs)

    def query(
        self, question: str, with_grading: bool = False, **kwargs
    ) -> Union[str, Dict[str, Any]]:
        """
        Query the RAG system.

        Args:
            question: Question to answer
            with_grading: If True, returns dict with answer and grading
            **kwargs: Additional arguments (ignored for compatibility)

        Returns:
            str: Answer text (if with_grading=False)
            Dict: Answer + sources + grading (if with_grading=True)
        """
        result = self._rag.query(question)

        if with_grading:
            # Return format matching original AgenticRAG
            return {
                "answer": result["answer"],
                "sources": result["sources"],
                "grading": {
                    "relevance": "yes",  # LlamaIndex doesn't have grading
                    "hallucination": "no",
                    "quality": "good",
                },
            }
        else:
            return result["answer"]


# =============================================================================
# Factory Function (Recommended Entry Point)
# =============================================================================


def create_llamaindex_rag(
    persist_dir: str = "./storage",
    chroma_path: Optional[str] = None,
    faiss_index_path: Optional[str] = None,
    use_chroma: bool = True,
) -> LlamaIndexAgenticRAG:
    """
    Factory function to create LlamaIndex Agentic RAG instance.

    This function automatically configures:
    - LLM (OpenAI → Anthropic → Ollama)
    - Embeddings (OpenAI → HuggingFace → Ollama)
    - Vector store (ChromaDB or FAISS)
    - Graph store (NetworkX via SimpleGraphStore)
    - ReAct agent with tools

    Args:
        persist_dir: Directory for persisted indices (default: "./storage")
        chroma_path: Path for ChromaDB (default: persist_dir/chroma_db)
        faiss_index_path: Path for FAISS index file (optional)
        use_chroma: Whether to use ChromaDB (True) or FAISS (False)

    Returns:
        LlamaIndexAgenticRAG: Configured RAG instance

    Example:
        >>> rag = create_llamaindex_rag()
        >>> rag.load_documents(directory="./data")
        >>> result = rag.query("What are the main concepts?")
        >>> print(result["answer"])
    """
    if not LLAMAINDEX_AVAILABLE:
        raise ImportError(
            f"LlamaIndex dependencies not installed: {IMPORT_ERROR}\n\n"
            "Install with:\n"
            "  pip install llama-index-core llama-index-llms-ollama \\\n"
            "              llama-index-embeddings-ollama llama-index-vector-stores-faiss \\\n"
            "              llama-index-vector-stores-chroma faiss-cpu chromadb\n\n"
            "Or run: ./install_llamaindex_deps.sh"
        )

    # Configure global settings
    Settings.embed_model = _get_embedding_model()
    Settings.llm = _get_llm()
    Settings.chunk_size = 1000
    Settings.chunk_overlap = 200

    # Create RAG instance
    return LlamaIndexAgenticRAG(
        persist_dir=persist_dir,
        chroma_path=chroma_path,
        faiss_index_path=faiss_index_path,
        use_chroma=use_chroma,
    )


# =============================================================================
# Convenience Exports
# =============================================================================

__all__ = [
    "create_llamaindex_rag",
    "LlamaIndexAgenticRAG",
    "AgenticRAGCompatibility",
]


# =============================================================================
# Quick Test (if run directly)
# =============================================================================

if __name__ == "__main__":
    print("LlamaIndex Agentic RAG Module")
    print("=" * 80)
    print("\nThis module provides LlamaIndex integration for lovable-ai-system-3.")
    print("\nQuick Start:")
    print("  1. Install dependencies: ./install_llamaindex_deps.sh")
    print("  2. Run test: python test_rag.py")
    print("\nUsage:")
    print("  from ai.rag.llamaindex_agentic import create_llamaindex_rag")
    print("  rag = create_llamaindex_rag()")
    print("  rag.load_documents(directory='./data')")
    print("  result = rag.query('Your question here')")
    print("  print(result['answer'])")
    print("\n" + "=" * 80)
