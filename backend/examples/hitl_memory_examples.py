"""
Complete Examples: HITL + Long-Term Memory
==========================================
Production examples based on December 2024 LangGraph features
and community best practices from forums/X.

Examples:
1. Dynamic Agent with Command (edgeless flows)
2. Customer Support with HITL approval
3. Semantic Memory Agent
4. Multi-agent handoffs
5. Memory-enhanced chatbot

Run:
    python examples/hitl_memory_examples.py
"""

import os
import sys
import time
from typing import Dict, List

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =============================================================================
# Example 1: Dynamic Agent with Command (Edgeless Flows)
# =============================================================================


def example_dynamic_agent():
    """
    Dynamic routing with Command - no predefined edges.

    Based on X post (Dec 6, 2025): Architecture with dynamic orchestration
    that decides when to search, use memory, or combine - 57% efficiency gain.
    """
    print("\n" + "=" * 60)
    print("Example 1: Dynamic Agent with Command (Edgeless Flows)")
    print("=" * 60)

    try:
        from langgraph.types import Command
        from langgraph.graph import StateGraph, END, START
        from typing import TypedDict, Annotated

        class AgentState(TypedDict):
            messages: List[str]
            query: str
            result: str
            route_taken: str

        def dynamic_router(state: AgentState) -> Command:
            """
            Dynamic routing based on message content.
            No edges needed - Command.goto handles routing.
            """
            last_message = state["messages"][-1] if state["messages"] else ""

            # Intelligent routing decision
            if "search" in last_message.lower() or "find" in last_message.lower():
                print("  → Routing to: search_node")
                return Command(
                    goto="search_node",
                    update={"query": last_message, "route_taken": "search"},
                )
            elif "remember" in last_message.lower() or "recall" in last_message.lower():
                print("  → Routing to: memory_node")
                return Command(goto="memory_node", update={"route_taken": "memory"})
            elif "analyze" in last_message.lower():
                print("  → Routing to: analysis_node")
                return Command(goto="analysis_node", update={"route_taken": "analysis"})
            else:
                print("  → Routing to: END (direct response)")
                return Command(
                    goto=END,
                    update={
                        "result": f"Processed: {last_message}",
                        "route_taken": "direct",
                    },
                )

        def search_node(state: AgentState) -> Dict:
            """Simulated search."""
            print(f"  🔍 Searching for: {state.get('query', 'unknown')}")
            time.sleep(0.1)  # Simulate search
            return {"result": f"Search results for: {state.get('query')}"}

        def memory_node(state: AgentState) -> Dict:
            """Simulated memory recall."""
            print("  🧠 Recalling from memory...")
            return {"result": "Memory: User prefers dark mode, Python expert"}

        def analysis_node(state: AgentState) -> Dict:
            """Simulated analysis."""
            print("  📊 Analyzing...")
            return {"result": "Analysis complete"}

        # Build workflow
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("router", dynamic_router)
        workflow.add_node("search_node", search_node)
        workflow.add_node("memory_node", memory_node)
        workflow.add_node("analysis_node", analysis_node)

        # Only need entry edge - Command handles the rest!
        workflow.add_edge(START, "router")
        workflow.add_edge("search_node", END)
        workflow.add_edge("memory_node", END)
        workflow.add_edge("analysis_node", END)

        app = workflow.compile()

        # Test different queries
        test_queries = [
            "Search for Python tutorials",
            "Remember my preferences",
            "Analyze this data",
            "Hello, how are you?",
        ]

        for query in test_queries:
            print(f"\n📝 Query: '{query}'")
            result = app.invoke({"messages": [query]})
            print(f"  ✅ Result: {result.get('result', 'N/A')}")
            print(f"  📍 Route: {result.get('route_taken', 'N/A')}")

        print("\n✅ Dynamic Agent example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        print("   Install: pip install langgraph>=0.2.60")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 2: Customer Support with HITL Approval
# =============================================================================


def example_customer_support_hitl():
    """
    Customer support agent with human approval for critical actions.

    Based on awesome-claude-code patterns (Dec 12, 2025):
    - interrupt for approval
    - Tools that update state
    - Timeout handling
    """
    print("\n" + "=" * 60)
    print("Example 2: Customer Support with HITL Approval")
    print("=" * 60)

    try:
        from hitl import (
            HITLWorkflow,
            create_approval_node,
            create_stateful_tool,
            StatefulToolNode,
            HITL_AVAILABLE,
        )

        print(f"  HITL Available: {HITL_AVAILABLE}")

        # Create workflow
        workflow = HITLWorkflow(db_path=":memory:")

        # Test cases
        test_actions = [
            ("Update customer email", "routine"),  # Auto-approved
            ("Send promotional email to all users", "critical"),  # Needs approval
            ("Delete customer account", "critical"),  # Needs approval
            ("View customer info", "routine"),  # Auto-approved
        ]

        for action, action_type in test_actions:
            print(f"\n📋 Action: '{action}' ({action_type})")

            result = workflow.run_with_approval(
                action=action,
                thread_id=f"test-{int(time.time()*1000)}",
                user_id="user_123",
            )

            print(f"  Status: {result.get('status')}")
            print(f"  Approval: {result.get('approval_status')}")

            if result.get("action_result"):
                print(f"  Result: {result.get('action_result')}")

        print("\n✅ Customer Support HITL example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 3: Semantic Memory Agent
# =============================================================================


def example_semantic_memory():
    """
    Agent with semantic (meaning-based) memory search.

    Based on X post (Dec 6, 2025): Supermemory with Chroma/LanceDB
    for semantic recall - improves long-term memory efficiency.
    """
    print("\n" + "=" * 60)
    print("Example 3: Semantic Memory Agent")
    print("=" * 60)

    try:
        from memory import (
            MemoryManager,
            SemanticStore,
            MemoryType,
            MEMORY_STORE_AVAILABLE,
        )

        print(f"  Memory Store Available: {MEMORY_STORE_AVAILABLE}")

        # Create memory manager
        store = SemanticStore(backend="memory", embedding_provider="auto")
        manager = MemoryManager(store=store)

        # Store some facts
        print("\n📝 Storing facts...")
        facts = [
            ("User's name is John Doe", "profile"),
            ("User prefers dark mode", "preferences"),
            ("User is a Python expert", "skills"),
            ("User works at TechCorp", "work"),
            ("User's birthday is March 15", "profile"),
            ("User prefers morning meetings", "preferences"),
            ("User is learning Rust", "skills"),
            ("User's favorite framework is LangGraph", "preferences"),
        ]

        for fact, category in facts:
            memory_id = manager.store_fact(fact, user_id="user_123", category=category)
            print(f"  ✓ Stored: '{fact[:40]}...' (ID: {memory_id[:8]})")

        # Semantic search
        print("\n🔍 Semantic search tests:")

        queries = [
            "What is the user's name?",
            "What UI theme does the user like?",
            "What programming languages does the user know?",
            "When should I schedule meetings?",
        ]

        for query in queries:
            print(f"\n  Q: '{query}'")
            results = manager.recall_facts(query, user_id="user_123", limit=2)

            for i, result in enumerate(results):
                print(
                    f"     {i+1}. {result['fact']} (relevance: {result['relevance']:.2f})"
                )

        # Store an episode
        print("\n📖 Storing episode...")
        episode = [
            {"role": "user", "content": "How do I use LangGraph?"},
            {
                "role": "assistant",
                "content": "LangGraph is great for building agents...",
            },
            {"role": "user", "content": "Thanks, that was helpful!"},
        ]

        episode_id = manager.store_episode(
            episode,
            user_id="user_123",
            outcome="successful",
            tags=["langgraph", "tutorial"],
        )
        print(f"  ✓ Episode stored (ID: {episode_id[:8]})")

        # Recall episode
        print("\n🔍 Recalling similar episodes:")
        episodes = manager.recall_episodes("LangGraph help", user_id="user_123")
        for ep in episodes:
            print(f"  - {ep['summary'][:60]}... (relevance: {ep['relevance']:.2f})")

        print("\n✅ Semantic Memory example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 4: Multi-Agent Handoffs
# =============================================================================


def example_multi_agent_handoffs():
    """
    Multi-agent system with handoffs using Command.

    Pattern: Supervisor delegates to specialist agents.
    """
    print("\n" + "=" * 60)
    print("Example 4: Multi-Agent Handoffs")
    print("=" * 60)

    try:
        from langgraph.types import Command
        from langgraph.graph import StateGraph, END, START
        from typing import TypedDict, List

        class MultiAgentState(TypedDict):
            messages: List[str]
            current_agent: str
            handoff_context: dict
            result: str

        def supervisor(state: MultiAgentState) -> Command:
            """Supervisor agent that routes to specialists."""
            query = state["messages"][-1] if state["messages"] else ""
            print(f"  👔 Supervisor analyzing: '{query[:50]}...'")

            # Route based on content
            if any(
                word in query.lower() for word in ["code", "python", "bug", "function"]
            ):
                print("  → Handoff to: code_expert")
                return Command(
                    goto="code_expert",
                    update={
                        "current_agent": "code_expert",
                        "handoff_context": {"query": query, "from": "supervisor"},
                    },
                )
            elif any(
                word in query.lower()
                for word in ["data", "analyze", "chart", "statistics"]
            ):
                print("  → Handoff to: data_analyst")
                return Command(
                    goto="data_analyst",
                    update={
                        "current_agent": "data_analyst",
                        "handoff_context": {"query": query, "from": "supervisor"},
                    },
                )
            else:
                print("  → Handling directly")
                return Command(
                    goto=END, update={"result": f"Supervisor response: {query}"}
                )

        def code_expert(state: MultiAgentState) -> Command:
            """Specialist for coding tasks."""
            context = state.get("handoff_context", {})
            print(
                f"  💻 Code Expert handling task from {context.get('from', 'unknown')}"
            )

            # Can hand back or to another agent
            return Command(
                goto=END,
                update={
                    "result": "Code solution: Use async/await for better performance"
                },
            )

        def data_analyst(state: MultiAgentState) -> Command:
            """Specialist for data analysis."""
            context = state.get("handoff_context", {})
            print(
                f"  📊 Data Analyst handling task from {context.get('from', 'unknown')}"
            )

            return Command(
                goto=END, update={"result": "Analysis: Data shows 23% increase in Q4"}
            )

        # Build workflow
        workflow = StateGraph(MultiAgentState)

        workflow.add_node("supervisor", supervisor)
        workflow.add_node("code_expert", code_expert)
        workflow.add_node("data_analyst", data_analyst)

        workflow.add_edge(START, "supervisor")

        app = workflow.compile()

        # Test handoffs
        test_queries = [
            "Help me fix this Python bug",
            "Analyze last month's sales data",
            "What's the weather like?",
        ]

        for query in test_queries:
            print(f"\n📝 Query: '{query}'")
            result = app.invoke({"messages": [query]})
            print(f"  ✅ Result: {result.get('result', 'N/A')}")

        print("\n✅ Multi-Agent Handoffs example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Example 5: Memory-Enhanced Chatbot
# =============================================================================


def example_memory_chatbot():
    """
    Chatbot that remembers user context across conversations.

    Combines:
    - Semantic memory for facts
    - Episodic memory for interactions
    - Procedural memory for behavior
    """
    print("\n" + "=" * 60)
    print("Example 5: Memory-Enhanced Chatbot")
    print("=" * 60)

    try:
        from memory import MemoryManager, SemanticStore
        from langgraph.graph import StateGraph, END, START
        from typing import TypedDict, List, Optional

        class ChatState(TypedDict):
            messages: List[dict]
            user_id: str
            context: dict
            response: str

        # Initialize memory
        store = SemanticStore(backend="memory")
        memory = MemoryManager(store=store)

        # Pre-populate some memories
        memory.store_fact("User's name is Alice", user_id="alice_123")
        memory.store_fact("User is a data scientist", user_id="alice_123")
        memory.store_fact("User prefers concise answers", user_id="alice_123")

        def recall_context(state: ChatState) -> dict:
            """Recall relevant memories before responding."""
            user_id = state.get("user_id", "unknown")
            last_msg = state["messages"][-1]["content"] if state["messages"] else ""

            print(f"  🧠 Recalling context for user: {user_id}")

            # Get relevant facts
            facts = memory.recall_facts(last_msg, user_id=user_id, limit=3)

            # Build context
            context = {
                "user_facts": [f["fact"] for f in facts],
                "preferences": [
                    f["fact"] for f in facts if "prefer" in f["fact"].lower()
                ],
            }

            print(f"     Found {len(facts)} relevant facts")

            return {"context": context}

        def generate_response(state: ChatState) -> dict:
            """Generate response using context."""
            context = state.get("context", {})
            last_msg = state["messages"][-1]["content"] if state["messages"] else ""

            # Simulated response generation
            user_facts = context.get("user_facts", [])

            if user_facts:
                fact_summary = ", ".join(user_facts[:2])
                response = f"Based on what I know about you ({fact_summary}), here's my answer to '{last_msg[:30]}...'"
            else:
                response = f"Response to: {last_msg}"

            print(f"  💬 Generated response using {len(user_facts)} facts")

            return {"response": response}

        def store_interaction(state: ChatState) -> dict:
            """Store interaction for future reference."""
            user_id = state.get("user_id", "unknown")

            # Extract any new facts from the conversation
            for msg in state["messages"]:
                content = msg.get("content", "").lower()

                # Simple fact extraction (in real app, use LLM)
                if "my name is" in content:
                    name = content.split("my name is")[-1].strip().split()[0]
                    memory.store_fact(f"User's name is {name}", user_id=user_id)
                    print(f"  📝 Stored new fact: User's name is {name}")

                elif "i work" in content or "i am a" in content:
                    memory.store_fact(
                        f"User mentioned: {content[:50]}", user_id=user_id
                    )
                    print("  📝 Stored new fact about user")

            return {}

        # Build workflow
        workflow = StateGraph(ChatState)

        workflow.add_node("recall", recall_context)
        workflow.add_node("respond", generate_response)
        workflow.add_node("store", store_interaction)

        workflow.add_edge(START, "recall")
        workflow.add_edge("recall", "respond")
        workflow.add_edge("respond", "store")
        workflow.add_edge("store", END)

        app = workflow.compile()

        # Simulate conversation
        conversations = [
            {"role": "user", "content": "Hi, what do you know about me?"},
            {"role": "user", "content": "I'm working on a machine learning project"},
            {"role": "user", "content": "Can you help me analyze some data?"},
        ]

        for msg in conversations:
            print(f"\n👤 User: {msg['content']}")
            result = app.invoke({"messages": [msg], "user_id": "alice_123"})
            print(f"🤖 Bot: {result.get('response', 'No response')}")

        print("\n✅ Memory-Enhanced Chatbot example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Example 6: Tools with State Updates + Redis Caching
# =============================================================================


def example_stateful_tools_with_caching():
    """
    Tools that update state with caching for efficiency.

    Based on forum discussions: Use Redis + SQLite for caching
    to reduce DB calls and improve loop execution time.
    """
    print("\n" + "=" * 60)
    print("Example 6: Stateful Tools with Caching")
    print("=" * 60)

    try:
        from hitl import create_stateful_tool, StatefulToolNode
        from langgraph.types import Command

        # Simple in-memory cache (use Redis in production)
        _cache = {}
        _db = {
            "cust_001": {
                "name": "John",
                "email": "john@example.com",
                "tier": "premium",
            },
            "cust_002": {"name": "Jane", "email": "jane@example.com", "tier": "basic"},
        }

        def cached_lookup(customer_id: str, **kwargs) -> Command:
            """Look up customer with caching."""
            # Check cache first
            cache_key = f"customer:{customer_id}"

            if cache_key in _cache:
                print(f"    ⚡ Cache HIT for {customer_id}")
                data = _cache[cache_key]
            else:
                print("    💾 Cache MISS - fetching from DB")
                data = _db.get(customer_id, {"error": "Not found"})
                _cache[cache_key] = data  # Store in cache

            return Command(
                update={
                    "customer_data": data,
                    "cache_status": "hit" if cache_key in _cache else "miss",
                }
            )

        def update_customer(
            customer_id: str, field: str, value: str, **kwargs
        ) -> Command:
            """Update customer and invalidate cache."""
            if customer_id in _db:
                _db[customer_id][field] = value

                # Invalidate cache
                cache_key = f"customer:{customer_id}"
                if cache_key in _cache:
                    del _cache[cache_key]
                    print(f"    🗑️ Cache invalidated for {customer_id}")

                return Command(
                    update={
                        "customer_data": _db[customer_id],
                        "update_status": "success",
                    }
                )

            return Command(update={"error": "Customer not found"})

        # Create tool node
        StatefulToolNode([cached_lookup, update_customer])

        # Simulate tool calls
        print("\n📞 Testing cached lookups:")

        # First lookup - cache miss
        print("\n  Call 1: lookup cust_001")
        result1 = cached_lookup(customer_id="cust_001")
        print(f"    Result: {result1.update}")

        # Second lookup - cache hit
        print("\n  Call 2: lookup cust_001 (should be cached)")
        result2 = cached_lookup(customer_id="cust_001")
        print(f"    Result: {result2.update}")

        # Update - invalidates cache
        print("\n  Call 3: update cust_001")
        result3 = update_customer(
            customer_id="cust_001", field="tier", value="enterprise"
        )
        print(f"    Result: {result3.update}")

        # Lookup after update - cache miss
        print("\n  Call 4: lookup cust_001 (cache invalidated)")
        result4 = cached_lookup(customer_id="cust_001")
        print(f"    Result: {result4.update}")

        print("\n✅ Stateful Tools with Caching example completed!")
        return True

    except ImportError as e:
        print(f"⚠️ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# =============================================================================
# Main
# =============================================================================


def main():
    """Run all examples."""
    print("=" * 60)
    print("  HITL + Long-Term Memory Examples")
    print("  December 2024 LangGraph Features")
    print("=" * 60)

    examples = [
        ("Dynamic Agent (Command)", example_dynamic_agent),
        ("Customer Support HITL", example_customer_support_hitl),
        ("Semantic Memory", example_semantic_memory),
        ("Multi-Agent Handoffs", example_multi_agent_handoffs),
        ("Memory-Enhanced Chatbot", example_memory_chatbot),
        ("Stateful Tools + Caching", example_stateful_tools_with_caching),
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
