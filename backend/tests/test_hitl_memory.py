"""
Tests for HITL and Memory Modules
=================================
Unit and integration tests for December 2024 LangGraph features.
"""

import pytest
import sys
import os
from unittest.mock import patch

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# HITL Module Tests
# =============================================================================


class TestHITLState:
    """Tests for HITL state management."""

    def test_create_initial_state(self):
        """Test initial state creation."""
        from hitl import create_initial_hitl_state, ApprovalStatus

        state = create_initial_hitl_state(query="test query", user_id="user_123")

        assert state["status"] == "pending"
        assert state["approval_status"] == ApprovalStatus.PENDING.value
        assert state["user_id"] == "user_123"
        assert len(state["messages"]) == 1
        assert "test query" in state["messages"][0].content

    def test_initial_state_empty_query(self):
        """Test initial state with no query."""
        from hitl import create_initial_hitl_state

        state = create_initial_hitl_state()

        assert state["messages"] == []
        assert state["user_id"] is None


class TestDynamicRouter:
    """Tests for Command-based dynamic routing."""

    def test_create_dynamic_router(self):
        """Test dynamic router creation."""
        from hitl import create_dynamic_router

        routes = {"search": "search_node", "memory": "memory_node"}

        router = create_dynamic_router(routes, default="default_node")

        assert callable(router)

    def test_router_matches_condition(self):
        """Test router matches conditions in messages."""
        from hitl import create_dynamic_router, HITLState
        from langchain_core.messages import HumanMessage

        routes = {"search": "search_node", "memory": "memory_node"}

        router = create_dynamic_router(routes, default="default_node")

        state: HITLState = {
            "messages": [HumanMessage(content="Please search for documents")],
            "status": "pending",
            "approval_status": "pending",
            "user_id": None,
            "user_info": None,
            "pending_action": None,
            "action_result": None,
            "next_node": None,
            "retry_count": 0,
            "error_message": None,
        }

        result = router(state)

        assert result.goto == "search_node"

    def test_router_uses_default(self):
        """Test router falls back to default."""
        from hitl import create_dynamic_router, HITLState
        from langchain_core.messages import HumanMessage

        routes = {"search": "search_node"}
        router = create_dynamic_router(routes, default="default_node")

        state: HITLState = {
            "messages": [HumanMessage(content="Hello world")],
            "status": "pending",
            "approval_status": "pending",
            "user_id": None,
            "user_info": None,
            "pending_action": None,
            "action_result": None,
            "next_node": None,
            "retry_count": 0,
            "error_message": None,
        }

        result = router(state)

        assert result.goto == "default_node"


class TestHandoffNode:
    """Tests for multi-agent handoffs."""

    def test_create_handoff_node(self):
        """Test handoff node creation."""
        from hitl import create_handoff_node

        handoff = create_handoff_node(
            target_agent="support_agent", handoff_message="Transferring to support"
        )

        assert callable(handoff)

    def test_handoff_returns_command(self):
        """Test handoff returns Command with goto."""
        from hitl import create_handoff_node, HITLState

        handoff = create_handoff_node("support_agent")

        state: HITLState = {
            "messages": [],
            "status": "pending",
            "approval_status": "pending",
            "user_id": "user_123",
            "user_info": {"name": "John"},
            "pending_action": "help request",
            "action_result": None,
            "next_node": None,
            "retry_count": 0,
            "error_message": None,
        }

        result = handoff(state)

        assert result.goto == "support_agent"
        assert "handoff_context" in result.update


class TestApprovalNode:
    """Tests for interrupt-based approval."""

    def test_create_approval_node(self):
        """Test approval node creation."""
        from hitl import create_approval_node

        approval = create_approval_node(
            approval_message="Approve?", on_approve="execute", on_reject="cancel"
        )

        assert callable(approval)

    @patch("hitl.HITL_AVAILABLE", False)
    def test_approval_fallback_auto_approves(self):
        """Test approval auto-approves when HITL unavailable."""
        from hitl import create_approval_node, HITLState, ApprovalStatus

        approval = create_approval_node(on_approve="execute")

        state: HITLState = {
            "messages": [],
            "status": "pending",
            "approval_status": "pending",
            "user_id": None,
            "user_info": None,
            "pending_action": "test action",
            "action_result": None,
            "next_node": None,
            "retry_count": 0,
            "error_message": None,
        }

        result = approval(state)

        assert result.goto == "execute"
        assert result.update["approval_status"] == ApprovalStatus.APPROVED.value


class TestStatefulTools:
    """Tests for tools that update state."""

    def test_create_stateful_tool(self):
        """Test stateful tool creation."""
        from hitl import create_stateful_tool

        tool = create_stateful_tool(
            name="lookup_user",
            description="Look up user info",
            lookup_fn=lambda user_id: {"name": "John"},
        )

        assert callable(tool)
        assert tool.__name__ == "lookup_user"

    def test_stateful_tool_returns_command(self):
        """Test stateful tool returns Command with updates."""
        from hitl import create_stateful_tool

        tool = create_stateful_tool(
            name="test_tool", description="Test", lookup_fn=lambda x: {"result": x}
        )

        result = tool(x="test_value")

        assert hasattr(result, "update")
        assert "result" in result.update

    def test_stateful_tool_node(self):
        """Test StatefulToolNode processes tools."""
        from hitl import StatefulToolNode, create_stateful_tool

        tool = create_stateful_tool(
            name="test_tool",
            description="Test",
            state_updates={"status": "tool_executed"},
        )

        node = StatefulToolNode([tool])

        assert "test_tool" in node.tools


class TestHITLWorkflow:
    """Tests for complete HITL workflow."""

    def test_workflow_creation(self):
        """Test workflow creates successfully."""
        from hitl import HITLWorkflow

        workflow = HITLWorkflow(db_path=":memory:")

        assert workflow._workflow is not None

    def test_workflow_run_with_approval(self):
        """Test workflow execution with approval."""
        from hitl import HITLWorkflow

        workflow = HITLWorkflow(db_path=":memory:")

        result = workflow.run_with_approval(action="Test action", thread_id="test-001")

        assert "thread_id" in result
        assert "status" in result


# =============================================================================
# Memory Module Tests
# =============================================================================


class TestMemoryTypes:
    """Tests for memory type definitions."""

    def test_memory_type_enum(self):
        """Test MemoryType enum values."""
        from memory import MemoryType

        assert MemoryType.SEMANTIC.value == "semantic"
        assert MemoryType.EPISODIC.value == "episodic"
        assert MemoryType.PROCEDURAL.value == "procedural"

    def test_memory_dataclass(self):
        """Test Memory dataclass."""
        from memory import Memory, MemoryType

        memory = Memory(
            id="mem_001",
            content="User prefers dark mode",
            memory_type=MemoryType.SEMANTIC,
            namespace="preferences",
        )

        assert memory.id == "mem_001"
        assert memory.content == "User prefers dark mode"

    def test_memory_to_dict(self):
        """Test Memory serialization."""
        from memory import Memory, MemoryType

        memory = Memory(
            id="mem_001",
            content="Test content",
            memory_type=MemoryType.SEMANTIC,
            namespace="default",
        )

        data = memory.to_dict()

        assert data["id"] == "mem_001"
        assert data["memory_type"] == "semantic"
        assert "created_at" in data


class TestEmbeddingProvider:
    """Tests for embedding provider."""

    def test_provider_initialization(self):
        """Test embedding provider initializes."""
        from memory import EmbeddingProvider

        provider = EmbeddingProvider(provider="auto")

        assert provider.dimensions > 0

    def test_fallback_embedding(self):
        """Test fallback embedding works."""
        from memory import EmbeddingProvider

        provider = EmbeddingProvider(provider="none")

        embedding = provider.embed("test text")

        assert len(embedding) == provider.dimensions
        assert all(isinstance(v, float) for v in embedding)

    def test_embedding_deterministic(self):
        """Test fallback embedding is deterministic."""
        from memory import EmbeddingProvider

        provider = EmbeddingProvider(provider="none")

        emb1 = provider.embed("same text")
        emb2 = provider.embed("same text")

        assert emb1 == emb2

    def test_batch_embedding(self):
        """Test batch embedding."""
        from memory import EmbeddingProvider

        provider = EmbeddingProvider(provider="none")

        texts = ["text 1", "text 2", "text 3"]
        embeddings = provider.embed_batch(texts)

        assert len(embeddings) == 3


class TestSemanticStore:
    """Tests for semantic memory store."""

    def test_store_creation(self):
        """Test store creates successfully."""
        from memory import SemanticStore

        store = SemanticStore(backend="memory")

        assert store is not None

    def test_add_memory(self):
        """Test adding memory to store."""
        from memory import SemanticStore

        store = SemanticStore(backend="memory")

        memory_id = store.add(content="User prefers Python", namespace="preferences")

        assert memory_id is not None
        assert len(memory_id) > 0

    def test_get_memory(self):
        """Test retrieving memory by ID."""
        from memory import SemanticStore

        store = SemanticStore(backend="memory")

        memory_id = store.add(content="Test memory", namespace="test")

        memory = store.get(memory_id, namespace="test")

        assert memory is not None
        assert memory.content == "Test memory"

    def test_search_memories(self):
        """Test semantic search."""
        from memory import SemanticStore

        store = SemanticStore(backend="memory")

        # Add memories
        store.add("User prefers dark mode", namespace="prefs")
        store.add("User likes Python programming", namespace="prefs")
        store.add("User works at TechCorp", namespace="prefs")

        # Search
        results = store.search("coding language", namespace="prefs", limit=2)

        assert len(results) <= 2
        assert all(isinstance(r, tuple) for r in results)

    def test_delete_memory(self):
        """Test deleting memory."""
        from memory import SemanticStore

        store = SemanticStore(backend="memory")

        memory_id = store.add("To delete", namespace="test")

        success = store.delete(memory_id, namespace="test")

        assert success is True

        memory = store.get(memory_id, namespace="test")
        assert memory is None


class TestMemoryManager:
    """Tests for high-level memory manager."""

    def test_manager_creation(self):
        """Test manager creates successfully."""
        from memory import MemoryManager

        manager = MemoryManager()

        assert manager is not None

    def test_store_fact(self):
        """Test storing semantic memory (fact)."""
        from memory import MemoryManager

        manager = MemoryManager()

        memory_id = manager.store_fact(
            fact="User's name is John", user_id="user_123", category="profile"
        )

        assert memory_id is not None

    def test_recall_facts(self):
        """Test recalling facts."""
        from memory import MemoryManager

        manager = MemoryManager()

        manager.store_fact("User prefers dark mode", user_id="user_123")
        manager.store_fact("User likes Python", user_id="user_123")

        facts = manager.recall_facts("UI theme", user_id="user_123", limit=2)

        assert isinstance(facts, list)

    def test_store_episode(self):
        """Test storing episodic memory."""
        from memory import MemoryManager

        manager = MemoryManager()

        interaction = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        memory_id = manager.store_episode(
            interaction=interaction, user_id="user_123", outcome="successful"
        )

        assert memory_id is not None

    def test_recall_episodes(self):
        """Test recalling episodes."""
        from memory import MemoryManager

        manager = MemoryManager()

        interaction = [
            {"role": "user", "content": "Help with Python"},
            {"role": "assistant", "content": "Sure, what do you need?"},
        ]

        manager.store_episode(interaction, user_id="user_123")

        episodes = manager.recall_episodes("Python help", user_id="user_123")

        assert isinstance(episodes, list)

    def test_update_procedure(self):
        """Test updating procedural memory."""
        from memory import MemoryManager

        manager = MemoryManager()

        memory_id = manager.update_procedure(
            instruction="Always greet the user by name",
            agent_id="support_bot",
            priority=1,
        )

        assert memory_id is not None

    def test_get_procedures(self):
        """Test getting procedural memories."""
        from memory import MemoryManager

        manager = MemoryManager()

        manager.update_procedure("Rule 1", agent_id="bot_1")
        manager.update_procedure("Rule 2", agent_id="bot_1")

        procedures = manager.get_procedures("bot_1")

        assert isinstance(procedures, list)


class TestMemoryTools:
    """Tests for LangMem tool integration."""

    def test_create_memory_tools(self):
        """Test memory tools creation."""
        from memory import create_memory_tools

        tools = create_memory_tools(namespace=("test",))

        assert len(tools) == 2
        assert all(callable(t) for t in tools)


# =============================================================================
# Integration Tests
# =============================================================================


class TestHITLMemoryIntegration:
    """Integration tests for HITL + Memory."""

    def test_workflow_with_memory(self):
        """Test HITL workflow with memory recall."""
        from hitl import HITLWorkflow
        from memory import MemoryManager

        # Create memory manager
        memory = MemoryManager()
        memory.store_fact("User is VIP", user_id="user_123")

        # Create workflow
        workflow = HITLWorkflow(db_path=":memory:")

        result = workflow.run_with_approval(
            action="Priority support request",
            thread_id="integration-test",
            user_id="user_123",
        )

        assert result is not None
        assert "status" in result

    def test_stateful_tool_with_memory(self):
        """Test stateful tool that uses memory."""
        from hitl import create_stateful_tool
        from memory import MemoryManager

        memory = MemoryManager()

        def lookup_with_memory(user_id: str):
            facts = memory.recall_facts("user info", user_id=user_id)
            return {"facts": facts}

        tool = create_stateful_tool(
            name="memory_lookup",
            description="Look up user from memory",
            lookup_fn=lookup_with_memory,
        )

        result = tool(user_id="user_123")

        assert hasattr(result, "update")


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
