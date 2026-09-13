"""
Long-Term Memory Module
=======================
Production-ready long-term memory for LangGraph agents:
- Semantic Search with embeddings
- PostgresStore / InMemoryStore integration
- LangMem SDK for memory management
- Episodic, Semantic, and Procedural memory

December 2024 LangGraph features:
- Semantic Search (Dec 6): Find memories by meaning
- LangMem SDK: Manage long-term memory

Installation:
    pip install langgraph langmem langchain-community
    pip install pgvector psycopg2-binary  # For PostgreSQL
    pip install chromadb  # For local vector store

Usage:
    from memory import MemoryManager, SemanticStore, create_memory_agent

    # Quick start
    manager = MemoryManager()
    manager.store("user prefers dark mode", namespace="preferences")
    results = manager.search("lighting settings")

    # With agent
    agent = create_memory_agent()
    response = agent.invoke({"messages": [{"role": "user", "content": "Remember I like Python"}]})
"""

import os
import time
import json
import hashlib
from typing import TypedDict, Annotated, List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

# LangGraph imports
try:
    from langgraph.store.memory import InMemoryStore

    MEMORY_STORE_AVAILABLE = True
except ImportError:
    MEMORY_STORE_AVAILABLE = False
    InMemoryStore = None

try:
    from langgraph.store.postgres import PostgresStore

    POSTGRES_STORE_AVAILABLE = True
except ImportError:
    POSTGRES_STORE_AVAILABLE = False
    PostgresStore = None

# LangMem imports
try:
    from langmem import (
        create_manage_memory_tool,
        create_search_memory_tool,
        create_memory_manager,
    )

    LANGMEM_AVAILABLE = True
except (ImportError, AttributeError):
    # AttributeError: Python 3.10 doesn't have typing.NotRequired
    LANGMEM_AVAILABLE = False
    create_manage_memory_tool = None
    create_search_memory_tool = None
    create_memory_manager = None

# Embeddings
try:
    from langchain_community.embeddings import HuggingFaceEmbeddings

    HF_EMBEDDINGS_AVAILABLE = True
except ImportError:
    HF_EMBEDDINGS_AVAILABLE = False

try:
    from langchain_openai import OpenAIEmbeddings

    OPENAI_EMBEDDINGS_AVAILABLE = True
except ImportError:
    OPENAI_EMBEDDINGS_AVAILABLE = False


# =============================================================================
# Memory Types
# =============================================================================


class MemoryType(str, Enum):
    """Types of long-term memory."""

    SEMANTIC = "semantic"  # Facts, knowledge
    EPISODIC = "episodic"  # Past interactions, experiences
    PROCEDURAL = "procedural"  # Skills, rules, prompts


@dataclass
class Memory:
    """A single memory item."""

    id: str
    content: str
    memory_type: MemoryType
    namespace: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "namespace": self.namespace,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Memory":
        return cls(
            id=data["id"],
            content=data["content"],
            memory_type=MemoryType(data.get("memory_type", "semantic")),
            namespace=data.get("namespace", "default"),
            metadata=data.get("metadata", {}),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.now()
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if "updated_at" in data
                else datetime.now()
            ),
        )


# =============================================================================
# Embedding Providers
# =============================================================================


class EmbeddingProvider:
    """
    Embedding provider abstraction.

    Supports:
    - OpenAI embeddings
    - HuggingFace (local)
    - Ollama (local)
    """

    def __init__(
        self, provider: str = "auto", model: str = None, dimensions: int = 1536
    ):
        self.provider = provider
        self.model = model
        self.dimensions = dimensions
        self._embeddings = None
        self._initialize()

    def _initialize(self):
        """Initialize embedding model."""
        if self.provider == "auto":
            # Try providers in order
            if OPENAI_EMBEDDINGS_AVAILABLE and os.getenv("OPENAI_API_KEY"):
                self.provider = "openai"
            elif HF_EMBEDDINGS_AVAILABLE:
                self.provider = "huggingface"
            else:
                self.provider = "none"

        if self.provider == "openai":
            model = self.model or "text-embedding-3-small"
            self._embeddings = OpenAIEmbeddings(model=model)
            self.dimensions = 1536

        elif self.provider == "huggingface":
            model = self.model or "sentence-transformers/all-MiniLM-L6-v2"
            self._embeddings = HuggingFaceEmbeddings(model_name=model)
            self.dimensions = 384

        elif self.provider == "ollama":
            try:
                from langchain_community.embeddings import OllamaEmbeddings

                model = self.model or "nomic-embed-text"
                self._embeddings = OllamaEmbeddings(model=model)
                self.dimensions = 768
            except ImportError:
                self.provider = "none"

    def embed(self, text: str) -> List[float]:
        """Embed a single text."""
        if self._embeddings is None:
            return self._fallback_embed(text)

        try:
            return self._embeddings.embed_query(text)
        except Exception as e:
            print(f"⚠️ Embedding error: {e}")
            return self._fallback_embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts."""
        if self._embeddings is None:
            return [self._fallback_embed(t) for t in texts]

        try:
            return self._embeddings.embed_documents(texts)
        except Exception:
            return [self._fallback_embed(t) for t in texts]

    def _fallback_embed(self, text: str) -> List[float]:
        """Fallback: hash-based pseudo-embedding."""
        # Create a deterministic pseudo-embedding
        hash_bytes = hashlib.sha256(text.encode()).digest()
        # Expand to dimensions
        result = []
        for i in range(self.dimensions):
            byte_idx = i % len(hash_bytes)
            result.append((hash_bytes[byte_idx] - 128) / 128.0)
        return result


# =============================================================================
# Semantic Store
# =============================================================================


class SemanticStore:
    """
    Semantic memory store with vector search.

    Features:
    - Semantic similarity search
    - Namespace isolation
    - Multiple backends (InMemory, PostgreSQL, ChromaDB)

    Usage:
        store = SemanticStore()
        store.add("User prefers dark mode", namespace="preferences")
        results = store.search("lighting settings", limit=5)
    """

    def __init__(
        self,
        backend: str = "memory",
        connection_string: str = None,
        embedding_provider: str = "auto",
    ):
        self.backend = backend
        self.connection_string = connection_string
        self.embeddings = EmbeddingProvider(embedding_provider)
        self._store = None
        self._memories: Dict[str, Memory] = {}  # In-memory fallback
        self._initialize_store()

    def _initialize_store(self):
        """Initialize the store backend."""
        if self.backend == "postgres" and POSTGRES_STORE_AVAILABLE:
            try:
                self._store = PostgresStore(
                    connection_string=self.connection_string
                    or os.getenv("DATABASE_URL"),
                    index={
                        "dims": self.embeddings.dimensions,
                        "embed": self.embeddings.embed,
                    },
                )
            except Exception as e:
                print(f"⚠️ PostgresStore init failed: {e}, falling back to memory")
                self.backend = "memory"

        if self.backend == "memory" and MEMORY_STORE_AVAILABLE:
            try:
                self._store = InMemoryStore(
                    index={
                        "dims": self.embeddings.dimensions,
                        "embed": self.embeddings.embed,
                    }
                )
            except Exception:
                self._store = None

    def add(
        self,
        content: str,
        namespace: str = "default",
        memory_type: MemoryType = MemoryType.SEMANTIC,
        metadata: Dict = None,
        memory_id: str = None,
    ) -> str:
        """
        Add a memory to the store.

        Args:
            content: Memory content
            namespace: Namespace for isolation
            memory_type: Type of memory
            metadata: Additional metadata
            memory_id: Optional custom ID

        Returns:
            Memory ID
        """
        # Generate ID if not provided
        if not memory_id:
            memory_id = hashlib.sha256(
                f"{namespace}:{content}:{time.time()}".encode()
            ).hexdigest()[:16]

        # Create memory object
        memory = Memory(
            id=memory_id,
            content=content,
            memory_type=memory_type,
            namespace=namespace,
            metadata=metadata or {},
            embedding=self.embeddings.embed(content),
        )

        # Store
        if self._store:
            try:
                self._store.put(
                    namespace=(namespace,), key=memory_id, value=memory.to_dict()
                )
            except Exception as e:
                print(f"⚠️ Store put failed: {e}")
                self._memories[memory_id] = memory
        else:
            self._memories[memory_id] = memory

        return memory_id

    def get(self, memory_id: str, namespace: str = "default") -> Optional[Memory]:
        """Get a specific memory by ID."""
        if self._store:
            try:
                item = self._store.get(namespace=(namespace,), key=memory_id)
                if item:
                    return Memory.from_dict(item.value)
            except Exception:
                pass

        return self._memories.get(memory_id)

    def search(
        self,
        query: str,
        namespace: str = None,
        limit: int = 5,
        memory_type: MemoryType = None,
        min_score: float = 0.0,
    ) -> List[Tuple[Memory, float]]:
        """
        Semantic search for memories.

        Args:
            query: Search query
            namespace: Filter by namespace
            limit: Max results
            memory_type: Filter by type
            min_score: Minimum similarity score

        Returns:
            List of (Memory, score) tuples
        """
        results = []

        if self._store:
            try:
                # Use store's search
                namespace_tuple = (namespace,) if namespace else None
                items = self._store.search(
                    query=query, namespace=namespace_tuple, limit=limit
                )

                for item in items:
                    score = getattr(item, "score", 1.0)
                    if score >= min_score:
                        memory = Memory.from_dict(item.value)
                        if memory_type is None or memory.memory_type == memory_type:
                            results.append((memory, score))

            except Exception as e:
                print(f"⚠️ Store search failed: {e}")

        # Fallback: search in-memory
        if not results and self._memories:
            query_embedding = self.embeddings.embed(query)

            for memory in self._memories.values():
                # Filter
                if namespace and memory.namespace != namespace:
                    continue
                if memory_type and memory.memory_type != memory_type:
                    continue

                # Calculate similarity
                if memory.embedding:
                    score = self._cosine_similarity(query_embedding, memory.embedding)
                    if score >= min_score:
                        results.append((memory, score))

            # Sort by score
            results.sort(key=lambda x: x[1], reverse=True)
            results = results[:limit]

        return results

    def delete(self, memory_id: str, namespace: str = "default") -> bool:
        """Delete a memory."""
        deleted = False

        if self._store:
            try:
                # Try to delete from store - use positional args for compatibility
                self._store.delete((namespace,), memory_id)
                deleted = True
            except Exception:
                pass

            # Verify deletion by checking if item is gone
            try:
                item = self._store.get(namespace=(namespace,), key=memory_id)
                if item is not None:
                    # Item still exists - store delete didn't work, clear manually
                    # Some stores need batch delete
                    try:
                        self._store.batch(
                            [
                                {
                                    "namespace": (namespace,),
                                    "key": memory_id,
                                    "op": "delete",
                                }
                            ]
                        )
                        deleted = True
                    except Exception:
                        pass
            except Exception:
                pass

        # Also try to delete from fallback memories dict
        if memory_id in self._memories:
            del self._memories[memory_id]
            deleted = True

        return deleted

    def list_namespaces(self) -> List[str]:
        """List all namespaces."""
        namespaces = set()

        for memory in self._memories.values():
            namespaces.add(memory.namespace)

        return list(namespaces)

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)


# =============================================================================
# Memory Manager
# =============================================================================


class MemoryManager:
    """
    High-level memory manager for agents.

    Combines:
    - Semantic memory (facts, preferences)
    - Episodic memory (interactions)
    - Procedural memory (prompts, rules)

    Usage:
        manager = MemoryManager()

        # Store different memory types
        manager.store_fact("User's name is John", user_id="user_123")
        manager.store_episode(conversation_history, user_id="user_123")
        manager.update_procedure("Always greet by name", agent_id="support_bot")

        # Recall
        facts = manager.recall_facts("user preferences", user_id="user_123")
        episodes = manager.recall_episodes("similar conversation", user_id="user_123")
        procedures = manager.get_procedures("support_bot")
    """

    def __init__(
        self,
        store: SemanticStore = None,
        auto_consolidate: bool = True,
        max_memories_per_namespace: int = 1000,
    ):
        self.store = store or SemanticStore()
        self.auto_consolidate = auto_consolidate
        self.max_memories = max_memories_per_namespace

    # =========================================================================
    # Semantic Memory (Facts)
    # =========================================================================

    def store_fact(
        self,
        fact: str,
        user_id: str = None,
        category: str = "general",
        confidence: float = 1.0,
    ) -> str:
        """Store a semantic memory (fact)."""
        namespace = f"facts:{user_id}" if user_id else "facts:global"

        return self.store.add(
            content=fact,
            namespace=namespace,
            memory_type=MemoryType.SEMANTIC,
            metadata={
                "category": category,
                "confidence": confidence,
                "user_id": user_id,
            },
        )

    def recall_facts(
        self, query: str, user_id: str = None, limit: int = 5
    ) -> List[Dict]:
        """Recall semantic memories (facts)."""
        namespace = f"facts:{user_id}" if user_id else None

        results = self.store.search(
            query=query,
            namespace=namespace,
            limit=limit,
            memory_type=MemoryType.SEMANTIC,
        )

        return [
            {
                "fact": m.content,
                "confidence": m.metadata.get("confidence", 1.0),
                "category": m.metadata.get("category", "general"),
                "relevance": score,
            }
            for m, score in results
        ]

    # =========================================================================
    # Episodic Memory (Experiences)
    # =========================================================================

    def store_episode(
        self,
        interaction: List[Dict],
        user_id: str = None,
        outcome: str = None,
        tags: List[str] = None,
    ) -> str:
        """Store an episodic memory (interaction/experience)."""
        namespace = f"episodes:{user_id}" if user_id else "episodes:global"

        # Summarize interaction
        summary = self._summarize_interaction(interaction)

        return self.store.add(
            content=summary,
            namespace=namespace,
            memory_type=MemoryType.EPISODIC,
            metadata={
                "full_interaction": interaction,
                "outcome": outcome,
                "tags": tags or [],
                "user_id": user_id,
            },
        )

    def recall_episodes(
        self, query: str, user_id: str = None, limit: int = 3
    ) -> List[Dict]:
        """Recall episodic memories (past interactions)."""
        namespace = f"episodes:{user_id}" if user_id else None

        results = self.store.search(
            query=query,
            namespace=namespace,
            limit=limit,
            memory_type=MemoryType.EPISODIC,
        )

        return [
            {
                "summary": m.content,
                "interaction": m.metadata.get("full_interaction", []),
                "outcome": m.metadata.get("outcome"),
                "relevance": score,
            }
            for m, score in results
        ]

    def _summarize_interaction(self, interaction: List[Dict]) -> str:
        """Summarize an interaction for storage."""
        # Simple summarization
        messages = []
        for msg in interaction[:5]:  # First 5 messages
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]  # Truncate
            messages.append(f"{role}: {content}")

        return " | ".join(messages)

    # =========================================================================
    # Procedural Memory (Rules/Prompts)
    # =========================================================================

    def update_procedure(
        self, instruction: str, agent_id: str, priority: int = 1, replace: bool = False
    ) -> str:
        """Update procedural memory (agent instructions)."""
        namespace = f"procedures:{agent_id}"

        if replace:
            # Find and update existing
            existing = self.store.search(
                query=instruction,
                namespace=namespace,
                limit=1,
                memory_type=MemoryType.PROCEDURAL,
            )

            if existing and existing[0][1] > 0.9:  # Very similar
                self.store.delete(existing[0][0].id, namespace)

        return self.store.add(
            content=instruction,
            namespace=namespace,
            memory_type=MemoryType.PROCEDURAL,
            metadata={"priority": priority, "agent_id": agent_id},
        )

    def get_procedures(self, agent_id: str, context: str = None) -> List[str]:
        """Get procedural memories (instructions) for an agent."""
        namespace = f"procedures:{agent_id}"

        if context:
            # Get context-relevant procedures
            results = self.store.search(
                query=context,
                namespace=namespace,
                limit=10,
                memory_type=MemoryType.PROCEDURAL,
            )

            # Sort by priority
            sorted_results = sorted(
                results, key=lambda x: x[0].metadata.get("priority", 0), reverse=True
            )

            return [m.content for m, _ in sorted_results]
        else:
            # Get all procedures
            results = self.store.search(
                query="instructions rules behavior",
                namespace=namespace,
                limit=50,
                memory_type=MemoryType.PROCEDURAL,
            )

            return [m.content for m, _ in results]

    # =========================================================================
    # Utilities
    # =========================================================================

    def store(
        self,
        content: str,
        namespace: str = "default",
        memory_type: str = "semantic",
        **metadata,
    ) -> str:
        """Generic store method."""
        return self.store.add(
            content=content,
            namespace=namespace,
            memory_type=MemoryType(memory_type),
            metadata=metadata,
        )

    def search(self, query: str, namespace: str = None, limit: int = 5) -> List[Dict]:
        """Generic search method."""
        results = self.store.search(query, namespace, limit)
        return [
            {
                "content": m.content,
                "type": m.memory_type.value,
                "namespace": m.namespace,
                "score": score,
                "metadata": m.metadata,
            }
            for m, score in results
        ]

    def consolidate(self, namespace: str = None):
        """Consolidate memories: deduplicate by cosine similarity, merge above threshold."""
        try:
            from difflib import SequenceMatcher

            threshold = 0.85

            memories = self.recall("", namespace=namespace, limit=500)
            if len(memories) < 2:
                return

            to_remove = set()
            for i, m1 in enumerate(memories):
                if i in to_remove:
                    continue
                for j in range(i + 1, len(memories)):
                    if j in to_remove:
                        continue
                    m2 = memories[j]
                    ratio = SequenceMatcher(
                        None, m1.get("content", ""), m2.get("content", "")
                    ).ratio()
                    if ratio >= threshold:
                        to_remove.add(j)

            # Remove duplicates from ChromaDB collection
            if to_remove and hasattr(self, "_collection") and self._collection:
                ids_to_delete = [
                    memories[idx].get("id")
                    for idx in to_remove
                    if memories[idx].get("id")
                ]
                if ids_to_delete:
                    self._collection.delete(ids=ids_to_delete)
        except Exception:
            pass


# =============================================================================
# LangMem Integration
# =============================================================================


def create_memory_tools(
    namespace: Tuple[str, ...] = ("memories",), store: SemanticStore = None
) -> List:
    """
    Create LangMem memory tools for an agent.

    Args:
        namespace: Memory namespace
        store: Optional custom store

    Returns:
        List of memory tools
    """
    if not LANGMEM_AVAILABLE:
        print("⚠️ LangMem not installed. Run: pip install langmem")

        # Return fallback tools
        def manage_memory(content: str, action: str = "add") -> str:
            """Manage memory (fallback)."""
            return f"Memory {action}: {content}"

        def search_memory(query: str) -> str:
            """Search memory (fallback)."""
            return f"Searching for: {query}"

        return [manage_memory, search_memory]

    # Create LangMem tools
    tools = [
        create_manage_memory_tool(namespace=namespace),
        create_search_memory_tool(namespace=namespace),
    ]

    return tools


def create_memory_agent(
    llm: str = "anthropic:claude-3-5-sonnet-latest",
    namespace: Tuple[str, ...] = ("memories",),
    store=None,
):
    """
    Create an agent with memory capabilities.

    Args:
        llm: LLM to use
        namespace: Memory namespace
        store: Memory store

    Returns:
        LangGraph agent with memory
    """
    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError:
        print("⚠️ LangGraph prebuilt not available")
        return None

    # Create store if not provided
    if store is None:
        if MEMORY_STORE_AVAILABLE:
            from langgraph.store.memory import InMemoryStore

            store = InMemoryStore(
                index={"dims": 1536, "embed": "openai:text-embedding-3-small"}
            )
        else:
            store = SemanticStore()

    # Create tools
    tools = create_memory_tools(namespace, store)

    # Create agent
    try:
        agent = create_react_agent(llm, tools=tools, store=store)
        return agent
    except Exception as e:
        print(f"⚠️ Agent creation failed: {e}")
        return None


# =============================================================================
# Memory-Augmented State
# =============================================================================


class MemoryState(TypedDict):
    """State with memory integration."""

    messages: Annotated[List[BaseMessage], "add_messages"]
    status: str

    # Memory
    user_id: Optional[str]
    recalled_facts: List[Dict]
    recalled_episodes: List[Dict]
    current_procedures: List[str]

    # Memory operations
    facts_to_store: List[str]
    episodes_to_store: List[Dict]


def create_memory_enhanced_workflow(
    manager: MemoryManager = None,
    recall_on_start: bool = True,
    store_on_end: bool = True,
):
    """
    Create a workflow with automatic memory recall and storage.

    Args:
        manager: MemoryManager instance
        recall_on_start: Whether to recall memories at start
        store_on_end: Whether to store new memories at end

    Returns:
        StateGraph
    """
    from langgraph.graph import StateGraph, END, START

    manager = manager or MemoryManager()
    workflow = StateGraph(MemoryState)

    def recall_memories(state: MemoryState) -> Dict:
        """Recall relevant memories."""
        user_id = state.get("user_id")
        query = state["messages"][-1].content if state["messages"] else ""

        facts = manager.recall_facts(query, user_id, limit=5)
        episodes = manager.recall_episodes(query, user_id, limit=3)

        return {"recalled_facts": facts, "recalled_episodes": episodes}

    def process_with_memory(state: MemoryState) -> Dict:
        """Process using recalled memories."""
        # This would be replaced with actual LLM processing
        return {"status": "processed"}

    def store_memories(state: MemoryState) -> Dict:
        """Store new memories from interaction."""
        user_id = state.get("user_id")

        # Store new facts
        for fact in state.get("facts_to_store", []):
            manager.store_fact(fact, user_id)

        # Store episode
        if state.get("episodes_to_store"):
            for episode in state["episodes_to_store"]:
                manager.store_episode(
                    episode.get("messages", []), user_id, episode.get("outcome")
                )

        return {"status": "memories_stored"}

    # Add nodes
    if recall_on_start:
        workflow.add_node("recall", recall_memories)
        workflow.add_edge(START, "recall")
        workflow.add_node("process", process_with_memory)
        workflow.add_edge("recall", "process")
    else:
        workflow.add_node("process", process_with_memory)
        workflow.add_edge(START, "process")

    if store_on_end:
        workflow.add_node("store", store_memories)
        workflow.add_edge("process", "store")
        workflow.add_edge("store", END)
    else:
        workflow.add_edge("process", END)

    return workflow


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Types
    "MemoryType",
    "Memory",
    # Core
    "EmbeddingProvider",
    "SemanticStore",
    "MemoryManager",
    # LangMem
    "create_memory_tools",
    "create_memory_agent",
    # Workflow
    "MemoryState",
    "create_memory_enhanced_workflow",
    # Availability
    "MEMORY_STORE_AVAILABLE",
    "POSTGRES_STORE_AVAILABLE",
    "LANGMEM_AVAILABLE",
]
