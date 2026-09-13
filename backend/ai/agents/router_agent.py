"""
Multi-Agent Router System (2025 Edition)
=========================================
Advanced multi-agent architecture using LangGraph with intelligent routing.
Based on latest LangGraph patterns and best practices.

Uses SmartRouter via LLM Factory for optimal model selection.
"""

from typing import TypedDict, Optional, List, Dict, Any, Annotated
from dataclasses import dataclass
from enum import Enum

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# Command import with fallback for older LangGraph versions
# Can be disabled via environment variable for rollback:
#   export USE_COMMAND_ROUTING=false
import os

try:
    from langgraph.types import Command

    _COMMAND_AVAILABLE = True
except ImportError:
    _COMMAND_AVAILABLE = False
    Command = None

# Feature flag: Check env var override (for rollback), default to Command availability
_env_command = os.environ.get("USE_COMMAND_ROUTING", "").lower()
if _env_command in ("false", "0", "no", "off"):
    USE_COMMAND_ROUTING = False
elif _env_command in ("true", "1", "yes", "on"):
    USE_COMMAND_ROUTING = _COMMAND_AVAILABLE  # Can't enable if Command not available
else:
    USE_COMMAND_ROUTING = _COMMAND_AVAILABLE  # Default: use if available

import logging

# Logger for LangGraph upgrade observability
lg_logger = logging.getLogger("langgraph.upgrade")

# SmartRouter integration via LLM Factory
from src.efficiency.factory import get_llm_for_task

# =============================================================================
# Configuration
# =============================================================================


@dataclass
class AgentConfig:
    """Configuration for multi-agent system.

    Note: LLM provider is determined by SmartRouter based on role and complexity.
    """

    complexity: int = 5  # Task complexity (1-10), affects model selection
    temperature: float = 0.7
    enable_memory: bool = True
    max_iterations: int = 10
    role: str = "coding"  # Default role for SmartRouter


def get_llm(config: AgentConfig):
    """
    Get LLM via SmartRouter Factory.

    DEPRECATED: Use get_llm_for_task() or get_llm_for_agents() directly.

    This function now routes through SmartRouter for optimal model selection
    based on complexity and role rather than hardcoded provider selection.
    """
    return get_llm_for_task(
        role=config.role,
        complexity=config.complexity,
        temperature=config.temperature,
    )


# =============================================================================
# Tools
# =============================================================================


@tool
def web_search(query: str) -> str:
    """
    Perform a real-time web search using Serper API.

    This tool takes a natural language query and returns relevant search results
    from the internet. Use this for current events, facts, news, or any
    information that requires up-to-date data.

    Args:
        query: A natural language search query

    Returns:
        Formatted string of search results
    """
    # Try Serper first (requires SERPER_API_KEY)
    try:
        from crewai_tools import SerperDevTool

        return SerperDevTool().run(query=query)
    except (ImportError, KeyError, Exception):
        # KeyError: missing SERPER_API_KEY
        # ImportError: crewai_tools not installed
        pass

    # Fallback to Tavily (requires TAVILY_API_KEY)
    try:
        from langchain_tavily import TavilySearchResults

        search = TavilySearchResults(max_results=5)
        results = search.invoke(query)
        return "\n".join([r.get("content", "") for r in results])
    except (ImportError, KeyError, Exception):
        pass

    # Final fallback - no search available
    return f"Search unavailable. Query was: {query}"


@tool
def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression safely.

    This tool handles arithmetic calculations, including basic operations
    (+, -, *, /), powers (**), and common math functions.

    Args:
        expression: A mathematical expression to evaluate (e.g., "2 + 2", "sqrt(16)")

    Returns:
        The result of the calculation as a string
    """
    import math
    import ast
    import operator

    # Safe operator mapping for AST-based evaluation
    _safe_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    _safe_functions = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "pow": pow,
        "sqrt": math.sqrt,
        "log": math.log,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "int": int,
        "float": float,
    }

    _safe_constants = {"pi": math.pi, "e": math.e}

    def _safe_eval_node(node):
        """Recursively evaluate an AST node with only safe operations."""
        if isinstance(node, ast.Expression):
            return _safe_eval_node(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, complex)):
                return node.value
            raise ValueError(f"Unsupported constant: {node.value!r}")
        elif isinstance(node, ast.Name):
            if node.id in _safe_constants:
                return _safe_constants[node.id]
            raise ValueError(f"Unknown variable: {node.id}")
        elif isinstance(node, ast.BinOp):
            op_func = _safe_operators.get(type(node.op))
            if not op_func:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op_func(_safe_eval_node(node.left), _safe_eval_node(node.right))
        elif isinstance(node, ast.UnaryOp):
            op_func = _safe_operators.get(type(node.op))
            if not op_func:
                raise ValueError(
                    f"Unsupported unary operator: {type(node.op).__name__}"
                )
            return op_func(_safe_eval_node(node.operand))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _safe_functions:
                args = [_safe_eval_node(arg) for arg in node.args]
                return _safe_functions[node.func.id](*args)
            raise ValueError(f"Unsupported function call: {ast.dump(node.func)}")
        else:
            raise ValueError(f"Unsupported expression: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval_node(tree)
        return str(result)
    except Exception as e:
        return f"Error calculating: {e}"


@tool
def code_executor(code: str, language: str = "python") -> str:
    """
    Execute Python code with access to V-OS tools API (Code Execution with MCP pattern).

    This advanced execution environment provides:
    - Access to tools via API: web_search(), calculator(), file_analyzer()
    - Persistent workspace: save_data(), load_data(), list_files()
    - Context optimization: Process data in code, return only summaries

    Example usage:
        # Search web and process results in code
        results = web_search("Python best practices")
        keywords = results['raw_text'].split()[:10]
        print(f"Top keywords: {', '.join(keywords)}")

        # Save intermediate data to workspace
        save_data("search_results.json", results)

        # Multi-step operations without context overhead
        calc_result = calculator("sqrt(144) * 2")
        print(f"Result: {calc_result['result']}")

    Args:
        code: Python code to execute (with full tools API access)
        language: Programming language (currently only 'python' supported)

    Returns:
        Console output from code execution (print statements)
    """
    if language.lower() != "python":
        return f"Language '{language}' not supported. Only Python is available."

    import asyncio
    from services.app_sandbox import get_sandbox

    # Block dangerous patterns in code before sending to sandbox
    _blocked_patterns = [
        "__import__",
        "importlib",
        "subprocess",
        "os.system",
        "os.popen",
        "os.exec",
        "compile(",
        "__builtins__",
        "__class__",
        "__subclasses__",
        "__bases__",
        "breakpoint(",
    ]
    for pattern in _blocked_patterns:
        if pattern in code:
            return f"Security error: '{pattern}' is not allowed in code execution."

    try:
        sandbox = get_sandbox()

        # Run the async sandbox.execute in sync context
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already inside an async context — use a thread to avoid deadlock
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, sandbox.execute(code, {})).result(
                    timeout=35
                )
        else:
            result = asyncio.run(sandbox.execute(code, {}))

        if result.success:
            output = str(result.output) if result.output is not None else ""
            return output if output else "Code executed successfully (no output)"
        else:
            return f"Execution error: {result.error}"

    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        return f"Execution error: {type(e).__name__}: {e}\n\nTraceback:\n{tb}"


@tool
def file_analyzer(content: str, analysis_type: str = "summary") -> str:
    """
    Analyze text content and provide insights.

    This tool processes text content and can provide summaries,
    extract key points, identify topics, or analyze sentiment.

    Args:
        content: The text content to analyze
        analysis_type: Type of analysis - 'summary', 'keywords', 'topics', 'sentiment'

    Returns:
        Analysis results based on the requested type
    """
    word_count = len(content.split())
    char_count = len(content)
    sentence_count = content.count(".") + content.count("!") + content.count("?")

    base_info = f"Document Stats: {word_count} words, {char_count} characters, ~{sentence_count} sentences"

    if analysis_type == "summary":
        # Simple extractive summary - first and last sentences
        sentences = [
            s.strip() for s in content.replace("\n", " ").split(".") if s.strip()
        ]
        if len(sentences) <= 3:
            summary = ". ".join(sentences)
        else:
            summary = ". ".join(sentences[:2] + sentences[-1:])
        return f"{base_info}\n\nSummary: {summary}."

    elif analysis_type == "keywords":
        # Simple keyword extraction
        import re

        words = re.findall(r"\b[a-zA-Z]{4,}\b", content.lower())
        word_freq = {}
        for word in words:
            word_freq[word] = word_freq.get(word, 0) + 1
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        keywords = [w[0] for w in top_words]
        return f"{base_info}\n\nTop Keywords: {', '.join(keywords)}"

    return base_info


# =============================================================================
# State Definition
# =============================================================================


class AgentState(TypedDict):
    """State for the multi-agent workflow."""

    messages: Annotated[List[BaseMessage], add_messages]
    user_query: str
    selected_agent: Optional[str]
    answer: Optional[str]
    intermediate_steps: List[Dict[str, Any]]
    iteration_count: int


class SimpleState(TypedDict):
    """Simplified state for basic routing."""

    user_query: str
    answer: str
    selected_agent: Optional[str]  # Set by Command routing


# =============================================================================
# Agent Definitions
# =============================================================================


class AgentType(Enum):
    """Available agent types."""

    SEARCH = "search_agent"
    MATH = "math_agent"
    CODE = "code_agent"
    ANALYZER = "analyzer_agent"
    GENERAL = "general_agent"
    KERNEL = "kernel_agent"


# Agent documentation for routing decisions
AGENT_DOCS = {
    AgentType.SEARCH: """
    Search Agent: Handles queries requiring real-time information from the web.
    Best for: Current events, news, facts, product information, research topics,
    'what is', 'who is', 'where is', latest updates, comparisons.
    """,
    AgentType.MATH: """
    Math Agent: Handles mathematical calculations and numeric problems.
    Best for: Arithmetic, algebra, statistics, calculations, 'calculate',
    'what is X + Y', percentages, conversions, formulas.
    """,
    AgentType.CODE: """
    Code Agent: Handles programming tasks and code execution.
    Best for: Writing code, debugging, code explanation, running scripts,
    'write a function', 'fix this code', algorithm implementation.
    """,
    AgentType.ANALYZER: """
    Analyzer Agent: Handles document and text analysis tasks.
    Best for: Summarization, keyword extraction, content analysis,
    'summarize this', 'analyze', 'extract key points', text processing.
    """,
    AgentType.GENERAL: """
    General Agent: Handles general questions and conversation.
    Best for: Open-ended questions, opinions, explanations, creative tasks,
    general knowledge that doesn't require real-time search.
    """,
    AgentType.KERNEL: """
    Kernel Agent: Handles file storage and retrieval on the VOS3 kernel disk.
    Best for: Saving files, reading files, listing disk contents,
    'save to disk', 'store this', 'remember', persistent storage, /disk/ operations.
    """,
}


# =============================================================================
# Node Implementations
# =============================================================================


def create_search_agent(llm):
    """Create a search agent with web search capability."""

    def search_agent(state: SimpleState) -> dict:
        """
        Search agent that uses web search to find information.
        Handles queries about current events, facts, and real-time data.
        """
        agent = create_react_agent(llm, [web_search])
        result = agent.invoke({"messages": [HumanMessage(content=state["user_query"])]})
        return {"answer": result["messages"][-1].content}

    search_agent.__doc__ = AGENT_DOCS[AgentType.SEARCH]
    return search_agent


def create_math_agent(llm):
    """Create a math agent with calculator capability."""

    def math_agent(state: SimpleState) -> dict:
        """
        Math agent that solves mathematical problems and calculations.
        Uses calculator tool for precise numeric computations.
        """
        agent = create_react_agent(llm, [calculator])
        prompt = f"Solve this math problem step by step: {state['user_query']}"
        result = agent.invoke({"messages": [HumanMessage(content=prompt)]})
        return {"answer": result["messages"][-1].content}

    math_agent.__doc__ = AGENT_DOCS[AgentType.MATH]
    return math_agent


def create_code_agent(llm):
    """Create a code agent with code execution capability."""

    def code_agent(state: SimpleState) -> dict:
        """
        Code agent that writes, explains, and executes code.
        Can run Python code and return results.
        """
        agent = create_react_agent(llm, [code_executor])
        result = agent.invoke({"messages": [HumanMessage(content=state["user_query"])]})
        return {"answer": result["messages"][-1].content}

    code_agent.__doc__ = AGENT_DOCS[AgentType.CODE]
    return code_agent


def create_analyzer_agent(llm):
    """Create an analyzer agent for text analysis."""

    def analyzer_agent(state: SimpleState) -> dict:
        """
        Analyzer agent that processes and analyzes text content.
        Can summarize, extract keywords, and identify topics.
        """
        agent = create_react_agent(llm, [file_analyzer])
        result = agent.invoke({"messages": [HumanMessage(content=state["user_query"])]})
        return {"answer": result["messages"][-1].content}

    analyzer_agent.__doc__ = AGENT_DOCS[AgentType.ANALYZER]
    return analyzer_agent


def create_general_agent(llm):
    """Create a general-purpose agent."""

    def general_agent(state: SimpleState) -> dict:
        """
        General agent for open-ended questions and conversation.
        Handles queries that don't require specialized tools.
        """
        response = llm.invoke(state["user_query"])
        return {"answer": response.content}

    general_agent.__doc__ = AGENT_DOCS[AgentType.GENERAL]
    return general_agent


KERNEL_SYSTEM_PROMPT = (
    "You are VOS3, an AI operating within a hardware-enforced microkernel. "
    "You have access to a secure, persistent VirtIO block device at /disk/. "
    "Use your kernel tools to permanently store critical data, preferences, "
    "analysis results, or long-term memories. Files on /disk/ survive cold "
    "reboots — they are hardware-persistent. When saving structured data, "
    "prefer JSON format. Always confirm the result of disk operations."
)


def create_kernel_agent(llm):
    """Create a kernel agent with disk read/write/list capability."""
    from ai.tools.kernel_tools import (
        list_kernel_disk,
        read_kernel_file,
        write_kernel_file,
    )

    kernel_tools = [list_kernel_disk, read_kernel_file, write_kernel_file]
    agent = create_react_agent(llm, kernel_tools, prompt=KERNEL_SYSTEM_PROMPT)

    def kernel_agent(state: SimpleState) -> dict:
        result = agent.invoke({"messages": [HumanMessage(content=state["user_query"])]})
        return {"answer": result["messages"][-1].content}

    kernel_agent.__doc__ = AGENT_DOCS[AgentType.KERNEL]
    return kernel_agent


# =============================================================================
# Router Implementation
# =============================================================================


def create_router(llm, available_agents: List[AgentType]):
    """
    Create an intelligent router that selects the best agent.

    Returns a function that returns Command (LangGraph 1.0+) for edgeless routing,
    or falls back to returning a string for conditional_edges (older versions).
    """
    agent_descriptions = "\n".join(
        [f"- {agent.value}: {AGENT_DOCS[agent].strip()}" for agent in available_agents]
    )

    # Set of valid agent names for runtime validation
    valid_agents = {agent.value for agent in available_agents}

    def _select_agent(state: SimpleState) -> str:
        """
        Core routing logic - uses LLM to select the best agent.
        Returns the agent name as a string.
        """
        prompt = f"""You are an intelligent router agent. Your task is to analyze the user's query
and select the most appropriate specialized agent to handle it.

User Query: {state['user_query']}

Available Agents:
{agent_descriptions}

Instructions:
1. Analyze the intent and content of the user's query
2. Match it to the most suitable agent based on their descriptions
3. Respond with ONLY the agent name (e.g., 'search_agent', 'math_agent', etc.)

Which agent should handle this query? Respond with just the agent name:"""

        response = llm.invoke(prompt)
        decision = response.content.strip().lower()

        # Map response to agent
        for agent in available_agents:
            if agent.value.replace("_agent", "") in decision or agent.value in decision:
                return agent.value

        # Default to general agent
        return AgentType.GENERAL.value

    if USE_COMMAND_ROUTING:
        # LangGraph 1.0+: Return Command for edgeless routing
        def routing_node(state: SimpleState) -> Command:
            """
            Router node using Command for edgeless navigation.
            Runtime validation via assert + LangGraph graph validation.
            """
            selected = _select_agent(state)

            # Runtime validation - clear error message if invalid
            assert (
                selected in valid_agents
            ), f"Unknown agent: {selected}. Valid agents: {valid_agents}"

            query_preview = state.get("user_query", "")[:50]
            lg_logger.info(f"[COMMAND] Routed to: {selected} (query: {query_preview})")

            return Command(goto=selected, update={"selected_agent": selected})

        return routing_node
    else:
        # Legacy fallback: return string for conditional_edges
        def routing_logic(state: SimpleState) -> str:
            """Legacy router returning agent name string."""
            return _select_agent(state)

        return routing_logic


# =============================================================================
# Graph Builder
# =============================================================================


class MultiAgentRouter:
    """
    Multi-Agent Router System with intelligent query routing.

    Usage:
        router = MultiAgentRouter()
        result = router.run("What is the capital of France?")
        print(result["answer"])

        # Or with streaming
        for event in router.stream("Calculate 25 * 4 + 10"):
            print(event)
    """

    def __init__(self, config: AgentConfig = None, agents: List[AgentType] = None):
        self.config = config or AgentConfig()
        self.llm = get_llm(self.config)

        # Default agents if not specified
        self.agents = agents or [
            AgentType.SEARCH,
            AgentType.MATH,
            AgentType.CODE,
            AgentType.KERNEL,
            AgentType.GENERAL,
        ]

        # Build the graph
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""

        workflow = StateGraph(SimpleState)

        # Create agent nodes
        agent_nodes = {
            AgentType.SEARCH: create_search_agent(self.llm),
            AgentType.MATH: create_math_agent(self.llm),
            AgentType.CODE: create_code_agent(self.llm),
            AgentType.ANALYZER: create_analyzer_agent(self.llm),
            AgentType.GENERAL: create_general_agent(self.llm),
            AgentType.KERNEL: create_kernel_agent(self.llm),
        }

        # Add nodes for available agents
        for agent_type in self.agents:
            workflow.add_node(agent_type.value, agent_nodes[agent_type])

        # Create router
        router = create_router(self.llm, self.agents)

        if USE_COMMAND_ROUTING:
            # LangGraph 1.0+: Command-based edgeless routing
            # Router is a node that returns Command with goto
            workflow.add_node("router", router)
            workflow.add_edge(START, "router")
            # No conditional_edges needed - Command.goto handles routing
        else:
            # Legacy fallback: conditional_edges with string routing
            workflow.add_conditional_edges(
                START, router, {agent.value: agent.value for agent in self.agents}
            )

        # All agents end the workflow
        for agent_type in self.agents:
            workflow.add_edge(agent_type.value, END)

        # Compile with optional memory
        if self.config.enable_memory:
            memory = MemorySaver()
            return workflow.compile(checkpointer=memory)

        return workflow.compile()

    def run(self, query: str, thread_id: str = None) -> Dict[str, Any]:
        """
        Run a query through the multi-agent system.

        Args:
            query: The user's question or request
            thread_id: Optional thread ID for conversation memory

        Returns:
            Dict with 'answer' and other state information
        """
        initial_state = {"user_query": query, "answer": ""}

        config = {}
        if thread_id and self.config.enable_memory:
            config["configurable"] = {"thread_id": thread_id}

        return self.graph.invoke(initial_state, config)

    def stream(self, query: str, thread_id: str = None):
        """
        Stream events from the multi-agent system.

        Yields progress updates as the query is processed.
        """
        initial_state = {"user_query": query, "answer": ""}

        config = {}
        if thread_id and self.config.enable_memory:
            config["configurable"] = {"thread_id": thread_id}

        for event in self.graph.stream(initial_state, config, stream_mode="values"):
            yield event

    def interactive(self):
        """Run interactive session with console input."""
        print("\n🤖 Multi-Agent Router System")
        print("=" * 50)
        print("Available agents:", [a.value for a in self.agents])
        print("Type 'quit' to exit\n")

        while True:
            try:
                query = input("You: ").strip()

                if query.lower() in ["quit", "exit", "q"]:
                    print("Goodbye! 👋")
                    break

                if not query:
                    continue

                print("\n🔄 Processing...")
                result = self.run(query)
                print(f"\n🤖 Answer: {result['answer']}\n")

            except KeyboardInterrupt:
                print("\nGoodbye! 👋")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}\n")


# =============================================================================
# Convenience Functions
# =============================================================================


def quick_route(query: str, complexity: int = 5) -> str:
    """Quick one-shot query routing via SmartRouter."""
    config = AgentConfig(complexity=complexity)
    router = MultiAgentRouter(config)
    result = router.run(query)
    return result["answer"]


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Option 1: Quick usage
    # answer = quick_route("What is 25 * 4?")
    # print(answer)

    # Option 2: Full configuration (SmartRouter selects optimal model)
    config = AgentConfig(
        complexity=6,  # Medium complexity task
        role="coding",  # SmartRouter role
        temperature=0.7,
        enable_memory=True,
    )

    router = MultiAgentRouter(
        config=config,
        agents=[AgentType.SEARCH, AgentType.MATH, AgentType.CODE, AgentType.GENERAL],
    )

    # Test queries
    test_queries = [
        "What is 2 + 2 * 10?",
        "What's the latest news about AI?",
        "Write a Python function to calculate fibonacci numbers",
        "Explain quantum computing in simple terms",
    ]

    print("\n🧪 Testing Multi-Agent Router\n")
    print("=" * 60)

    for query in test_queries:
        print(f"\n📝 Query: {query}")
        print("-" * 40)

        result = router.run(query)
        print(f"💬 Answer: {result['answer'][:200]}...")
        print()

    # Option 3: Interactive mode
    # router.interactive()
