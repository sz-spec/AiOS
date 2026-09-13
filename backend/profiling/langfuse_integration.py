"""
Langfuse Profiling Integration
==============================
Complete observability and profiling for LangGraph agents using Langfuse.

Features:
- Automatic tracing of all LLM calls
- Performance metrics (latency, tokens, costs)
- Custom scoring for quality evaluation
- Distributed tracing support
- Integration with LangGraph workflows

Installation:
    pip install langfuse langchain langchain_openai langgraph

Environment Variables (.env):
    LANGFUSE_SECRET_KEY="sk-lf-..."
    LANGFUSE_PUBLIC_KEY="pk-lf-..."
    LANGFUSE_BASE_URL="https://cloud.langfuse.com"
    OPENAI_API_KEY="sk-proj-..."

Usage:
    from profiling.langfuse_integration import ProfiledWorkflow

    workflow = ProfiledWorkflow()
    result = workflow.run("AI trends 2025", trace_name="research-001")
"""

import os
import time
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.sqlite import SqliteSaver

# Langfuse imports
try:
    from langfuse import Langfuse
    from langfuse.callback import CallbackHandler
    from langfuse.decorators import observe, langfuse_context

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    print("Warning: Langfuse not installed. Run: pip install langfuse")

# Load environment variables
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LangfuseConfig:
    """Configuration for Langfuse profiling."""

    secret_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    public_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    base_url: str = field(
        default_factory=lambda: os.getenv(
            "LANGFUSE_BASE_URL", "https://cloud.langfuse.com"
        )
    )
    enabled: bool = True
    debug: bool = False

    def is_configured(self) -> bool:
        """Check if Langfuse is properly configured."""
        return bool(self.secret_key and self.public_key)


@dataclass
class WorkflowConfig:
    """Configuration for the profiled workflow."""

    llm_model: str = "gpt-4o"
    temperature: float = 0.7
    max_retries: int = 3
    checkpoint_db: str = "agent.db"
    langfuse: LangfuseConfig = field(default_factory=LangfuseConfig)


# =============================================================================
# State Definition
# =============================================================================


class AgentState(TypedDict):
    """Shared state across all agents."""

    messages: Annotated[List[BaseMessage], "add_messages"]
    current_stage: str
    retry_count: int
    search_results: List[Dict[str, Any]]
    status: str
    error_message: str
    backoff_seconds: int
    max_retries: int
    research_query: str
    search_queries: List[str]
    output: str
    report: str
    last_attempt: bool
    # Profiling metadata
    trace_id: str
    start_time: float
    stage_times: Dict[str, float]


# =============================================================================
# Langfuse Manager
# =============================================================================


class LangfuseManager:
    """
    Manages Langfuse client and tracing operations.

    Usage:
        manager = LangfuseManager()
        handler = manager.get_handler(trace_name="my-trace")

        # After execution
        manager.score("my-trace", "accuracy", 0.95)
    """

    def __init__(self, config: LangfuseConfig = None):
        self.config = config or LangfuseConfig()
        self._client: Optional[Langfuse] = None
        self._initialized = False

    @property
    def client(self) -> Optional[Langfuse]:
        """Get or create Langfuse client."""
        if not LANGFUSE_AVAILABLE:
            return None

        if not self._initialized and self.config.is_configured():
            try:
                self._client = Langfuse(
                    secret_key=self.config.secret_key,
                    public_key=self.config.public_key,
                    host=self.config.base_url,
                )
                self._initialized = True
                if self.config.debug:
                    print(f"✅ Langfuse initialized: {self.config.base_url}")
            except Exception as e:
                print(f"❌ Langfuse initialization failed: {e}")
                self._client = None

        return self._client

    def get_handler(
        self,
        trace_name: str = None,
        user_id: str = None,
        session_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Optional[CallbackHandler]:
        """
        Create a CallbackHandler for LangChain/LangGraph tracing.

        Args:
            trace_name: Name/ID for this trace
            user_id: Optional user identifier
            session_id: Optional session identifier
            metadata: Additional metadata to attach

        Returns:
            CallbackHandler or None if Langfuse not available
        """
        if not LANGFUSE_AVAILABLE or not self.config.enabled:
            return None

        try:
            handler = CallbackHandler(
                trace_name=trace_name,
                user_id=user_id,
                session_id=session_id,
                metadata=metadata or {},
            )
            return handler
        except Exception as e:
            if self.config.debug:
                print(f"Warning: Could not create handler: {e}")
            return None

    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str = None,
        data_type: str = "NUMERIC",
    ) -> bool:
        """
        Add a score to a trace.

        Args:
            trace_id: ID of the trace to score
            name: Score name (e.g., "accuracy", "latency")
            value: Score value (0-1 for normalized, any for numeric)
            comment: Optional comment
            data_type: "NUMERIC" or "CATEGORICAL"

        Returns:
            True if score was recorded successfully
        """
        if not self.client:
            return False

        try:
            self.client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
                data_type=data_type,
            )
            return True
        except Exception as e:
            if self.config.debug:
                print(f"Warning: Could not record score: {e}")
            return False

    def create_trace(
        self, name: str, user_id: str = None, metadata: Dict[str, Any] = None
    ) -> Optional[str]:
        """
        Create a new trace and return its ID.

        Args:
            name: Trace name
            user_id: Optional user identifier
            metadata: Additional metadata

        Returns:
            Trace ID or None
        """
        if not self.client:
            return None

        try:
            trace = self.client.trace(
                name=name, user_id=user_id, metadata=metadata or {}
            )
            return trace.id
        except Exception as e:
            if self.config.debug:
                print(f"Warning: Could not create trace: {e}")
            return None

    def flush(self):
        """Flush pending events to Langfuse."""
        if self.client:
            try:
                self.client.flush()
            except Exception:
                pass


# =============================================================================
# Profiled Agent Nodes
# =============================================================================


def create_profiled_llm(config: WorkflowConfig) -> ChatOpenAI:
    """Create LLM instance with profiling support."""
    return ChatOpenAI(model=config.llm_model, temperature=config.temperature)


# Use @observe decorator for automatic tracing
if LANGFUSE_AVAILABLE:

    @observe(name="plan_research")
    def plan_research_profiled(state: AgentState, llm: ChatOpenAI) -> Dict[str, Any]:
        """Generate search queries with profiling."""
        start = time.perf_counter()

        query = state["research_query"]
        response = llm.invoke(
            [
                SystemMessage(content="You are a research planner."),
                HumanMessage(content=f"Create 3-5 search queries for: {query}"),
            ]
        )

        def parse_queries(content: str) -> List[str]:
            return [q.strip() for q in content.split("\n") if q.strip()][:5]

        queries = parse_queries(response.content)
        elapsed = time.perf_counter() - start

        # Update stage times
        stage_times = state.get("stage_times", {})
        stage_times["plan_research"] = elapsed

        return {
            "search_queries": queries,
            "current_stage": "searching",
            "stage_times": stage_times,
        }

    @observe(name="search")
    def search_profiled(state: AgentState) -> Dict[str, Any]:
        """Perform search with profiling."""
        start = time.perf_counter()

        results = [{"result": f"Mock result for {q}"} for q in state["search_queries"]]
        elapsed = time.perf_counter() - start

        stage_times = state.get("stage_times", {})
        stage_times["search"] = elapsed

        return {
            "search_results": results,
            "current_stage": "validating",
            "stage_times": stage_times,
        }

    @observe(name="validate")
    def validate_profiled(state: AgentState) -> Dict[str, Any]:
        """Validate results with profiling."""
        start = time.perf_counter()

        is_valid = len(state["search_results"]) > 0
        elapsed = time.perf_counter() - start

        stage_times = state.get("stage_times", {})
        stage_times["validate"] = elapsed

        return {
            "status": "valid" if is_valid else "invalid",
            "current_stage": "processing" if is_valid else "handling_error",
            "stage_times": stage_times,
        }

    @observe(name="process")
    def process_profiled(state: AgentState) -> Dict[str, Any]:
        """Process results with profiling."""
        start = time.perf_counter()

        report = "\n".join([r["result"] for r in state["search_results"]])
        elapsed = time.perf_counter() - start

        stage_times = state.get("stage_times", {})
        stage_times["process"] = elapsed

        return {
            "report": report,
            "current_stage": "complete",
            "status": "success",
            "stage_times": stage_times,
        }

    @observe(name="handle_error")
    def handle_error_profiled(state: AgentState) -> Dict[str, Any]:
        """Handle errors with profiling."""
        start = time.perf_counter()

        if state.get("last_attempt", False):
            time.sleep(state["backoff_seconds"])

        try:
            if state.get("force_error", False):
                raise ValueError("Forced error for testing")

            elapsed = time.perf_counter() - start
            stage_times = state.get("stage_times", {})
            stage_times["handle_error"] = elapsed

            return {
                "output": "Recovered result",
                "backoff_seconds": 1,
                "status": "success",
                "stage_times": stage_times,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start
            stage_times = state.get("stage_times", {})
            stage_times["handle_error"] = elapsed

            return {
                "retry_count": state["retry_count"] + 1,
                "backoff_seconds": min(state["backoff_seconds"] * 2, 60),
                "error_message": str(e),
                "status": "error",
                "stage_times": stage_times,
            }

    @observe(name="safe_node")
    def safe_node_profiled(state: AgentState) -> Dict[str, Any]:
        """Safe operation with profiling."""
        start = time.perf_counter()

        try:
            if state.get("force_error", False):
                raise ValueError("Forced error for testing")

            elapsed = time.perf_counter() - start
            stage_times = state.get("stage_times", {})
            stage_times["safe_node"] = elapsed

            return {
                "output": "API call result",
                "status": "success",
                "stage_times": stage_times,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start
            stage_times = state.get("stage_times", {})
            stage_times["safe_node"] = elapsed

            return {
                "status": "error",
                "error_message": str(e),
                "retry_count": state["retry_count"] + 1,
                "stage_times": stage_times,
            }

else:
    # Fallback without profiling decorators
    def plan_research_profiled(state, llm):
        return {"search_queries": [], "current_stage": "searching"}

    def search_profiled(state):
        return {"search_results": [], "current_stage": "validating"}

    def validate_profiled(state):
        return {"status": "invalid", "current_stage": "handling_error"}

    def process_profiled(state):
        return {"report": "", "status": "success", "current_stage": "complete"}

    def handle_error_profiled(state):
        return {"status": "success", "backoff_seconds": 1}

    def safe_node_profiled(state):
        return {"status": "success", "output": ""}


# =============================================================================
# Routing Functions
# =============================================================================


def route_validation(state: AgentState) -> str:
    """Route based on validation results."""
    if state["status"] == "valid" and state["retry_count"] < state["max_retries"]:
        return "process"
    elif state["retry_count"] >= state["max_retries"]:
        return "fail"
    else:
        return "improve"


def route_error(state: AgentState) -> str:
    """Route based on error type."""
    error = state.get("error_message", "")

    if "rate_limit" in error.lower():
        return "backoff"
    elif "auth" in error.lower():
        return "refresh_credentials"
    elif "not_found" in error.lower():
        return "try_fallback"
    else:
        return "retry"


# =============================================================================
# Profiled Workflow
# =============================================================================


class ProfiledWorkflow:
    """
    Research workflow with Langfuse profiling integration.

    Usage:
        # Basic usage
        workflow = ProfiledWorkflow()
        result = workflow.run("AI trends 2025")

        # With custom trace name
        result = workflow.run("AI trends", trace_name="research-001")

        # With scoring
        result = workflow.run("AI trends", trace_name="research-001")
        workflow.score_trace("research-001", "quality", 0.9)

        # Get profiling report
        report = workflow.get_profiling_report(result)
        print(report)
    """

    def __init__(self, config: WorkflowConfig = None):
        self.config = config or WorkflowConfig()
        self.langfuse = LangfuseManager(self.config.langfuse)
        self.llm = create_profiled_llm(self.config)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the workflow graph."""
        workflow = StateGraph(AgentState)

        # Create node wrappers that include LLM
        def plan_node(state):
            return plan_research_profiled(state, self.llm)

        workflow.add_node("plan", plan_node)
        workflow.add_node("search", search_profiled)
        workflow.add_node("validate", validate_profiled)
        workflow.add_node("process", process_profiled)
        workflow.add_node("handle_error", handle_error_profiled)
        workflow.add_node("safe_node", safe_node_profiled)

        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", "search")
        workflow.add_edge("search", "validate")

        workflow.add_conditional_edges(
            "validate",
            route_validation,
            {"process": "process", "fail": END, "improve": "handle_error"},
        )

        workflow.add_conditional_edges(
            "handle_error",
            route_error,
            {
                "backoff": "handle_error",
                "refresh_credentials": END,
                "try_fallback": "safe_node",
                "retry": "search",
            },
        )

        workflow.add_edge("process", END)
        workflow.add_edge("safe_node", "validate")

        # Setup checkpointer
        try:
            checkpointer = SqliteSaver.from_conn_string(self.config.checkpoint_db)
        except Exception:
            checkpointer = None

        return workflow.compile(checkpointer=checkpointer)

    def _get_initial_state(self, query: str, trace_id: str = None) -> AgentState:
        """Create initial state."""
        return {
            "messages": [],
            "current_stage": "planning",
            "retry_count": 0,
            "search_results": [],
            "status": "pending",
            "error_message": "",
            "backoff_seconds": 1,
            "max_retries": self.config.max_retries,
            "research_query": query,
            "search_queries": [],
            "output": "",
            "report": "",
            "last_attempt": False,
            "trace_id": trace_id or "",
            "start_time": time.time(),
            "stage_times": {},
        }

    def run(
        self,
        query: str,
        trace_name: str = None,
        user_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> AgentState:
        """
        Run the workflow with Langfuse profiling.

        Args:
            query: Research query
            trace_name: Name for this trace (default: auto-generated)
            user_id: Optional user identifier
            metadata: Additional metadata for the trace

        Returns:
            Final state with results and profiling data
        """
        start_time = time.time()

        # Generate trace name if not provided
        if not trace_name:
            trace_name = f"research-{int(start_time)}"

        # Get Langfuse handler
        handler = self.langfuse.get_handler(
            trace_name=trace_name, user_id=user_id, metadata=metadata
        )

        # Prepare config with callbacks
        config = {"configurable": {"thread_id": trace_name}}

        if handler:
            config["callbacks"] = [handler]

        # Create initial state
        initial_state = self._get_initial_state(query, trace_name)

        print(f"\n{'='*60}")
        print("🚀 Starting Profiled Research Workflow")
        print(f"   Query: {query}")
        print(f"   Trace: {trace_name}")
        print(f"   Profiling: {'Enabled' if handler else 'Disabled'}")
        print(f"{'='*60}\n")

        # Run workflow
        result = self.graph.invoke(initial_state, config=config)

        # Calculate total time
        total_time = time.time() - start_time

        # Add profiling summary to result
        result["_profiling"] = {
            "trace_name": trace_name,
            "total_time": total_time,
            "stage_times": result.get("stage_times", {}),
            "langfuse_enabled": handler is not None,
        }

        # Auto-score based on result
        if handler and self.langfuse.client:
            # Score based on success
            success_score = 1.0 if result["status"] == "success" else 0.0
            self.langfuse.score(trace_name, "success", success_score)

            # Score based on latency (lower is better, normalize to 0-1)
            latency_score = max(0, 1 - (total_time / 10))  # 10s = 0 score
            self.langfuse.score(trace_name, "latency", latency_score)

            # Flush events
            self.langfuse.flush()

        print(f"\n{'='*60}")
        print("✨ Workflow Complete")
        print(f"   Status: {result['status']}")
        print(f"   Total Time: {total_time:.2f}s")
        print(f"{'='*60}\n")

        return result

    def score_trace(
        self, trace_name: str, name: str, value: float, comment: str = None
    ) -> bool:
        """
        Add a custom score to a trace.

        Args:
            trace_name: Name of the trace
            name: Score name
            value: Score value
            comment: Optional comment

        Returns:
            True if successful
        """
        return self.langfuse.score(trace_name, name, value, comment)

    def get_profiling_report(self, result: AgentState) -> str:
        """
        Generate a human-readable profiling report.

        Args:
            result: Result from run()

        Returns:
            Formatted profiling report
        """
        profiling = result.get("_profiling", {})
        stage_times = profiling.get("stage_times", {})

        report = []
        report.append("\n" + "=" * 60)
        report.append("📊 PROFILING REPORT")
        report.append("=" * 60)

        report.append(f"\nTrace: {profiling.get('trace_name', 'N/A')}")
        report.append(f"Status: {result.get('status', 'N/A')}")
        report.append(f"Total Time: {profiling.get('total_time', 0):.3f}s")
        report.append(
            f"Langfuse: {'✅ Enabled' if profiling.get('langfuse_enabled') else '❌ Disabled'}"
        )

        if stage_times:
            report.append("\n--- Stage Breakdown ---")
            total_stage_time = sum(stage_times.values())

            for stage, time_s in sorted(
                stage_times.items(), key=lambda x: x[1], reverse=True
            ):
                pct = (time_s / total_stage_time * 100) if total_stage_time > 0 else 0
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                report.append(f"  {stage:20s}: {time_s:8.3f}s ({pct:5.1f}%) {bar}")

        report.append("\n--- Result Summary ---")
        report.append(f"  Queries: {len(result.get('search_queries', []))}")
        report.append(f"  Results: {len(result.get('search_results', []))}")
        report.append(f"  Retries: {result.get('retry_count', 0)}")

        if result.get("error_message"):
            report.append(f"  Error: {result['error_message']}")

        report.append("\n" + "=" * 60)

        return "\n".join(report)


# =============================================================================
# CLI Interface
# =============================================================================


def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(description="Profiled Research Workflow")
    parser.add_argument("query", nargs="?", help="Research query")
    parser.add_argument("--trace", "-t", help="Trace name")
    parser.add_argument("--user", "-u", help="User ID")
    parser.add_argument("--no-profile", action="store_true", help="Disable profiling")

    args = parser.parse_args()

    # Configure
    langfuse_config = LangfuseConfig(enabled=not args.no_profile)
    config = WorkflowConfig(langfuse=langfuse_config)

    workflow = ProfiledWorkflow(config)

    if args.query:
        result = workflow.run(args.query, trace_name=args.trace, user_id=args.user)
    else:
        # Interactive mode
        print("\n🔬 Profiled Research Workflow")
        print("=" * 40)
        query = input("Enter research query: ").strip()
        if query:
            result = workflow.run(query)
        else:
            print("No query provided.")
            return

    # Print profiling report
    print(workflow.get_profiling_report(result))

    # Print report preview
    if result.get("report"):
        print(f"\n📄 Report Preview:\n{result['report'][:500]}...")


if __name__ == "__main__":
    main()
