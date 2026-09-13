"""
Research Workflow Agent System
==============================
Advanced multi-agent workflow with:
- SQLite checkpointing for state persistence
- Exponential backoff retry logic
- Smart error routing
- Validation and recovery patterns

Uses SmartRouter via LLM Factory for optimal model selection.
"""

import time
from typing import TypedDict, Annotated, Literal, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages

# SmartRouter integration via LLM Factory
from src.efficiency.factory import get_llm_for_task

# Try to import SQLite saver, fall back to memory if not available
try:
    from langgraph.checkpoint.sqlite import SqliteSaver

    SQLITE_AVAILABLE = True
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver

    SQLITE_AVAILABLE = False


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class WorkflowConfig:
    """Configuration for the research workflow.

    Note: LLM model is selected by SmartRouter based on complexity.
    """

    complexity: int = 6  # Task complexity for SmartRouter (1-10)
    temperature: float = 0.7
    max_retries: int = 3
    initial_backoff: int = 1
    max_backoff: int = 60
    checkpoint_db: str = "workflow_state.db"
    enable_persistence: bool = True


# =============================================================================
# State Definition
# =============================================================================


class WorkflowStage(Enum):
    """Workflow stages."""

    PLANNING = "planning"
    SEARCHING = "searching"
    VALIDATING = "validating"
    PROCESSING = "processing"
    ERROR_HANDLING = "handling_error"
    COMPLETE = "complete"
    FAILED = "failed"


class WorkflowStatus(Enum):
    """Workflow status values."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALID = "valid"
    INVALID = "invalid"
    SUCCESS = "success"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


class AgentState(TypedDict):
    """
    Shared state across all agents in the workflow.

    This state is persisted via checkpointing and passed between nodes.
    """

    # Message history
    messages: Annotated[List[BaseMessage], add_messages]

    # Workflow control
    current_stage: str
    status: str

    # Retry management
    retry_count: int
    backoff_seconds: int
    max_retries: int
    last_attempt: bool

    # Error handling
    error_message: str
    error_type: str

    # Research specific
    research_query: str
    search_queries: List[str]
    search_results: List[Dict[str, Any]]

    # Output
    output: str
    report: str
    summary: str

    # Metadata
    started_at: str
    completed_at: str
    total_duration: float


# =============================================================================
# Utility Functions
# =============================================================================


def get_llm(config: WorkflowConfig = None):
    """Get LLM via SmartRouter Factory for research tasks."""
    config = config or WorkflowConfig()
    return get_llm_for_task(
        role="researcher",
        complexity=config.complexity,
        temperature=config.temperature,
    )


def parse_queries(content: str) -> List[str]:
    """Parse search queries from LLM response."""
    lines = content.strip().split("\n")
    queries = []
    for line in lines:
        # Remove numbering and clean up
        cleaned = line.strip()
        if cleaned:
            # Remove common prefixes like "1.", "- ", "* "
            for prefix in ["1.", "2.", "3.", "4.", "5.", "-", "*", "•"]:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].strip()
            if cleaned and len(cleaned) > 3:
                queries.append(cleaned)
    return queries[:5]  # Limit to 5 queries


def calculate_backoff(current: int, max_backoff: int = 60) -> int:
    """Calculate exponential backoff with cap."""
    return min(current * 2, max_backoff)


# =============================================================================
# Agent Nodes
# =============================================================================


def plan_research(state: AgentState) -> Dict[str, Any]:
    """
    Planning Agent: Generates search queries from research question.

    Takes the initial research query and breaks it down into
    specific search queries for comprehensive coverage.
    """
    print(f"📋 Planning research for: {state['research_query']}")

    llm = get_llm()

    system_prompt = """You are a research planning specialist. 
Your task is to break down a research question into specific, targeted search queries.
Each query should cover a different aspect of the topic.
Generate 3-5 search queries, one per line."""

    user_prompt = f"""Research Question: {state['research_query']}

Generate specific search queries to thoroughly research this topic.
Consider different angles: definitions, recent developments, key players, statistics, and future trends."""

    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )

    queries = parse_queries(response.content)

    print(f"   Generated {len(queries)} search queries")

    return {
        "messages": [AIMessage(content=f"Generated queries: {queries}")],
        "search_queries": queries,
        "current_stage": WorkflowStage.SEARCHING.value,
        "status": WorkflowStatus.IN_PROGRESS.value,
    }


def search(state: AgentState) -> Dict[str, Any]:
    """
    Search Agent: Performs searches based on generated queries.

    In production, this would integrate with real search APIs
    (Serper, Tavily, Google, etc.)
    """
    print(f"🔍 Searching with {len(state['search_queries'])} queries...")

    results = []

    for i, query in enumerate(state["search_queries"]):
        # TODO: Replace with actual search API call
        # Example with Serper:
        # from crewai_tools import SerperDevTool
        # result = SerperDevTool().run(query=query)

        # Mock results for demonstration
        results.append(
            {
                "query": query,
                "results": [
                    {"title": f"Result 1 for: {query}", "snippet": "Mock content..."},
                    {
                        "title": f"Result 2 for: {query}",
                        "snippet": "More mock content...",
                    },
                ],
                "source": "mock",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

        print(f"   [{i+1}/{len(state['search_queries'])}] Searched: {query[:50]}...")

    return {
        "messages": [AIMessage(content=f"Found {len(results)} result sets")],
        "search_results": results,
        "current_stage": WorkflowStage.VALIDATING.value,
    }


def validate(state: AgentState) -> Dict[str, Any]:
    """
    Validation Agent: Validates search results quality and completeness.

    Checks if results are sufficient and relevant before processing.
    """
    print("✅ Validating search results...")

    results = state["search_results"]

    # Validation criteria
    has_results = len(results) > 0
    has_content = all(len(r.get("results", [])) > 0 for r in results)
    coverage = len(results) / max(len(state["search_queries"]), 1)

    is_valid = has_results and has_content and coverage >= 0.5

    status = WorkflowStatus.VALID.value if is_valid else WorkflowStatus.INVALID.value
    next_stage = (
        WorkflowStage.PROCESSING.value
        if is_valid
        else WorkflowStage.ERROR_HANDLING.value
    )

    print(f"   Valid: {is_valid}, Coverage: {coverage:.0%}")

    return {
        "messages": [AIMessage(content=f"Validation: {status}")],
        "status": status,
        "current_stage": next_stage,
    }


def process(state: AgentState) -> Dict[str, Any]:
    """
    Processing Agent: Synthesizes search results into a coherent report.

    Uses LLM to analyze and summarize all collected information.
    """
    print("📝 Processing results into report...")

    llm = get_llm()

    # Compile all results
    all_content = []
    for result_set in state["search_results"]:
        query = result_set.get("query", "")
        for result in result_set.get("results", []):
            all_content.append(
                f"Query: {query}\nTitle: {result.get('title', '')}\nContent: {result.get('snippet', '')}"
            )

    content_text = "\n\n---\n\n".join(all_content)

    system_prompt = """You are a research analyst. 
Synthesize the provided search results into a comprehensive, well-structured report.
Include key findings, trends, and insights. Cite sources where relevant."""

    user_prompt = f"""Research Question: {state['research_query']}

Search Results:
{content_text}

Please create a comprehensive report summarizing these findings."""

    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )

    report = response.content

    # Generate summary
    summary_response = llm.invoke(
        [
            SystemMessage(content="Summarize the following report in 2-3 sentences."),
            HumanMessage(content=report),
        ]
    )

    print(f"   Report generated: {len(report)} characters")

    return {
        "messages": [AIMessage(content="Report generated successfully")],
        "report": report,
        "summary": summary_response.content,
        "current_stage": WorkflowStage.COMPLETE.value,
        "status": WorkflowStatus.SUCCESS.value,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def handle_error(state: AgentState) -> Dict[str, Any]:
    """
    Error Handler Agent: Manages errors with exponential backoff.

    Implements retry logic with increasing delays to handle
    transient failures gracefully.
    """
    print(
        f"⚠️ Handling error (attempt {state['retry_count'] + 1}/{state['max_retries']})..."
    )

    # Apply backoff if not first attempt
    if state["retry_count"] > 0 and state["last_attempt"]:
        backoff = state["backoff_seconds"]
        print(f"   Waiting {backoff}s before retry...")
        time.sleep(backoff)

    try:
        # Attempt recovery action
        # In production, this might refresh tokens, clear cache, etc.

        print("   Attempting recovery...")

        # Simulated recovery success
        return {
            "messages": [AIMessage(content="Recovery successful")],
            "output": "Recovered",
            "backoff_seconds": 1,  # Reset backoff
            "status": WorkflowStatus.IN_PROGRESS.value,
            "error_message": "",
        }

    except Exception as e:
        new_retry_count = state["retry_count"] + 1
        new_backoff = calculate_backoff(state["backoff_seconds"])

        print(f"   Recovery failed: {e}")

        return {
            "messages": [AIMessage(content=f"Error: {e}")],
            "retry_count": new_retry_count,
            "backoff_seconds": new_backoff,
            "error_message": str(e),
            "status": WorkflowStatus.ERROR.value,
            "last_attempt": new_retry_count >= state["max_retries"],
        }


def safe_operation(state: AgentState) -> Dict[str, Any]:
    """
    Safe Operation Agent: Performs operations with try-except wrapper.

    Used for external API calls or operations that might fail.
    """
    print("🛡️ Executing safe operation...")

    try:
        # Simulated external operation
        # In production: API calls, file operations, etc.

        result = "Operation completed successfully"

        return {
            "messages": [AIMessage(content=result)],
            "output": result,
            "status": WorkflowStatus.SUCCESS.value,
        }

    except Exception as e:
        error_type = type(e).__name__

        return {
            "messages": [AIMessage(content=f"Safe operation failed: {e}")],
            "status": WorkflowStatus.ERROR.value,
            "error_message": str(e),
            "error_type": error_type,
            "retry_count": state["retry_count"] + 1,
        }


def finalize(state: AgentState) -> Dict[str, Any]:
    """
    Finalization Agent: Prepares final output and cleanup.
    """
    print("🏁 Finalizing workflow...")

    return {
        "messages": [AIMessage(content="Workflow complete")],
        "current_stage": WorkflowStage.COMPLETE.value,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fail(state: AgentState) -> Dict[str, Any]:
    """
    Failure Agent: Handles unrecoverable failures.
    """
    print("❌ Workflow failed after max retries")

    return {
        "messages": [AIMessage(content=f"Failed: {state['error_message']}")],
        "current_stage": WorkflowStage.FAILED.value,
        "status": WorkflowStatus.ERROR.value,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# =============================================================================
# Routing Functions
# =============================================================================


def route_validation(state: AgentState) -> Literal["process", "improve", "fail"]:
    """Route based on validation results and retry count."""

    if state["status"] == WorkflowStatus.VALID.value:
        return "process"
    elif state["retry_count"] >= state["max_retries"]:
        return "fail"
    else:
        return "improve"


def route_error(
    state: AgentState,
) -> Literal["backoff", "refresh", "fallback", "retry", "fail"]:
    """Route based on error type."""

    error = state.get("error_message", "").lower()
    state.get("error_type", "").lower()

    # Check retry limit first
    if state["retry_count"] >= state["max_retries"]:
        return "fail"

    # Route based on error type
    if "rate_limit" in error or "429" in error:
        return "backoff"
    elif "auth" in error or "401" in error or "403" in error:
        return "refresh"
    elif "not_found" in error or "404" in error:
        return "fallback"
    else:
        return "retry"


def route_retry(state: AgentState) -> Literal["retry", "fail"]:
    """Simple retry routing."""

    if state["retry_count"] < state["max_retries"]:
        return "retry"
    else:
        return "fail"


# =============================================================================
# Workflow Builder
# =============================================================================


class ResearchWorkflow:
    """
    Research Workflow with checkpointing and error handling.

    Usage:
        workflow = ResearchWorkflow()
        result = workflow.run("AI trends in 2025")
        print(result["report"])

        # Resume from checkpoint
        result = workflow.resume("research-001")
    """

    def __init__(self, config: WorkflowConfig = None):
        self.config = config or WorkflowConfig()
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the workflow graph."""

        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("plan", plan_research)
        workflow.add_node("search", search)
        workflow.add_node("validate", validate)
        workflow.add_node("process", process)
        workflow.add_node("handle_error", handle_error)
        workflow.add_node("safe_operation", safe_operation)
        workflow.add_node("finalize", finalize)
        workflow.add_node("fail", fail)

        # Linear flow
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", "search")
        workflow.add_edge("search", "validate")

        # Conditional: after validation
        workflow.add_conditional_edges(
            "validate",
            route_validation,
            {"process": "process", "improve": "handle_error", "fail": "fail"},
        )

        # Conditional: after error handling
        workflow.add_conditional_edges(
            "handle_error",
            route_error,
            {
                "backoff": "handle_error",  # Loop with backoff
                "refresh": "fail",  # Auth issues -> fail (or add refresh node)
                "fallback": "safe_operation",
                "retry": "search",  # Retry search
                "fail": "fail",
            },
        )

        # Safe operation returns to validation
        workflow.add_edge("safe_operation", "validate")

        # Process leads to finalize
        workflow.add_edge("process", "finalize")

        # End nodes
        workflow.add_edge("finalize", END)
        workflow.add_edge("fail", END)

        # Setup checkpointer
        if self.config.enable_persistence and SQLITE_AVAILABLE:
            checkpointer = SqliteSaver.from_conn_string(self.config.checkpoint_db)
        else:
            checkpointer = MemorySaver()

        return workflow.compile(checkpointer=checkpointer)

    def _get_initial_state(self, query: str) -> AgentState:
        """Create initial state for a new workflow run."""
        return {
            "messages": [],
            "current_stage": WorkflowStage.PLANNING.value,
            "status": WorkflowStatus.PENDING.value,
            "retry_count": 0,
            "backoff_seconds": self.config.initial_backoff,
            "max_retries": self.config.max_retries,
            "last_attempt": False,
            "error_message": "",
            "error_type": "",
            "research_query": query,
            "search_queries": [],
            "search_results": [],
            "output": "",
            "report": "",
            "summary": "",
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "completed_at": "",
            "total_duration": 0.0,
        }

    def run(self, query: str, thread_id: str = None) -> AgentState:
        """
        Run the research workflow.

        Args:
            query: Research question to investigate
            thread_id: Optional thread ID for checkpointing

        Returns:
            Final state with report and status
        """
        start_time = time.time()

        initial_state = self._get_initial_state(query)

        if thread_id is None:
            thread_id = f"research-{int(time.time())}"

        config = {"configurable": {"thread_id": thread_id}}

        print(f"\n{'='*60}")
        print("🚀 Starting Research Workflow")
        print(f"   Query: {query}")
        print(f"   Thread: {thread_id}")
        print(f"{'='*60}\n")

        result = self.graph.invoke(initial_state, config=config)

        # Calculate duration
        result["total_duration"] = time.time() - start_time

        print(f"\n{'='*60}")
        print("✨ Workflow Complete")
        print(f"   Status: {result['status']}")
        print(f"   Duration: {result['total_duration']:.2f}s")
        print(f"{'='*60}\n")

        return result

    def resume(self, thread_id: str) -> AgentState:
        """
        Resume a workflow from checkpoint.

        Args:
            thread_id: Thread ID of the workflow to resume

        Returns:
            Final state after resumption
        """
        config = {"configurable": {"thread_id": thread_id}}

        print(f"🔄 Resuming workflow: {thread_id}")

        # Get current state
        state = self.graph.get_state(config)

        if state.values:
            print(f"   Found checkpoint at stage: {state.values.get('current_stage')}")
            return self.graph.invoke(None, config=config)
        else:
            raise ValueError(f"No checkpoint found for thread: {thread_id}")

    def stream(self, query: str, thread_id: str = None):
        """Stream workflow events."""

        initial_state = self._get_initial_state(query)

        if thread_id is None:
            thread_id = f"research-{int(time.time())}"

        config = {"configurable": {"thread_id": thread_id}}

        for event in self.graph.stream(
            initial_state, config=config, stream_mode="values"
        ):
            yield {
                "stage": event.get("current_stage"),
                "status": event.get("status"),
                "retry_count": event.get("retry_count"),
                "has_report": bool(event.get("report")),
            }

    def get_history(self, thread_id: str) -> List[Dict]:
        """Get execution history for a thread."""
        config = {"configurable": {"thread_id": thread_id}}

        history = []
        for state in self.graph.get_state_history(config):
            history.append(
                {
                    "stage": state.values.get("current_stage"),
                    "status": state.values.get("status"),
                    "timestamp": (
                        state.created_at if hasattr(state, "created_at") else None
                    ),
                }
            )

        return history


# =============================================================================
# CLI Interface
# =============================================================================


def main():
    """Command-line interface for research workflow."""
    import argparse

    parser = argparse.ArgumentParser(description="Research Workflow Agent")
    parser.add_argument("query", nargs="?", help="Research query")
    parser.add_argument("--resume", "-r", help="Resume from thread ID")
    parser.add_argument("--thread", "-t", help="Thread ID for new run")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retries")
    parser.add_argument(
        "--no-persist", action="store_true", help="Disable checkpointing"
    )

    args = parser.parse_args()

    config = WorkflowConfig(
        max_retries=args.max_retries, enable_persistence=not args.no_persist
    )

    workflow = ResearchWorkflow(config)

    if args.resume:
        result = workflow.resume(args.resume)
    elif args.query:
        result = workflow.run(args.query, thread_id=args.thread)
    else:
        # Interactive mode
        print("\n🔬 Research Workflow Agent")
        print("=" * 40)
        query = input("Enter research query: ").strip()
        if query:
            result = workflow.run(query)
        else:
            print("No query provided.")
            return

    # Print results
    print("\n📊 Results")
    print("=" * 40)
    print(f"Status: {result['status']}")
    print(f"Stage: {result['current_stage']}")
    print(f"Retries: {result['retry_count']}")

    if result.get("summary"):
        print(f"\n📝 Summary:\n{result['summary']}")

    if result.get("report"):
        print(f"\n📄 Report Preview:\n{result['report'][:500]}...")


if __name__ == "__main__":
    main()
