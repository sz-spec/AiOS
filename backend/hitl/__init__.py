"""
Human-in-the-Loop (HITL) Module
================================
Production-ready patterns for:
- Command: Dynamic edgeless flows with goto
- interrupt: Pause for human approval/input
- Tools that update state directly
- Multi-agent handoffs

December 2024 LangGraph features:
- Command (Dec 10): Edgeless graphs, dynamic routing
- interrupt (Dec 16): Human approval, review, edit
- Tools update state (Dec 18): Tools return Command to update state

Installation:
    pip install langgraph>=0.2.60 langchain>=0.3.0

Usage:
    from hitl import HITLWorkflow, create_approval_node, create_stateful_tool

    workflow = HITLWorkflow()
    result = workflow.run_with_approval("critical action", thread_id="hitl-001")
"""

import os
import time
from typing import (
    TypedDict,
    Annotated,
    List,
    Dict,
    Any,
    Optional,
    Literal,
    Union,
    Callable,
)
from dataclasses import dataclass, field
from functools import wraps
from enum import Enum

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END, START

# Import Command and interrupt
try:
    from langgraph.types import Command, interrupt

    HITL_AVAILABLE = True
except ImportError:
    HITL_AVAILABLE = False

    # Fallback implementations
    class Command:
        def __init__(self, goto=None, update=None, resume=None):
            self.goto = goto
            self.update = update or {}
            self.resume = resume

    def interrupt(message: str) -> Any:
        """Fallback interrupt - just returns None."""
        print(f"⚠️ INTERRUPT (fallback): {message}")
        return None


# Checkpointer
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3

    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False
    SqliteSaver = None


# =============================================================================
# State Definitions
# =============================================================================


class ApprovalStatus(str, Enum):
    """Status for approval workflows."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    EDITED = "edited"


class HITLState(TypedDict):
    """
    State for Human-in-the-Loop workflows.

    Supports:
    - Message history
    - Approval status tracking
    - User info from tools
    - Dynamic routing
    """

    messages: Annotated[List[BaseMessage], "add_messages"]
    status: str
    approval_status: str

    # User/customer info (updated by tools)
    user_id: Optional[str]
    user_info: Optional[Dict[str, Any]]

    # Action tracking
    pending_action: Optional[str]
    action_result: Optional[Any]

    # Routing
    next_node: Optional[str]

    # Metadata
    retry_count: int
    error_message: Optional[str]


def create_initial_hitl_state(query: str = "", user_id: str = None) -> HITLState:
    """Create initial HITL state."""
    return {
        "messages": [HumanMessage(content=query)] if query else [],
        "status": "pending",
        "approval_status": ApprovalStatus.PENDING.value,
        "user_id": user_id,
        "user_info": None,
        "pending_action": None,
        "action_result": None,
        "next_node": None,
        "retry_count": 0,
        "error_message": None,
    }


# =============================================================================
# Command Patterns - Dynamic Edgeless Flows
# =============================================================================


def create_dynamic_router(
    routes: Dict[str, str],
    default: str = END,
    condition_fn: Callable[[HITLState], str] = None,
) -> Callable:
    """
    Create a dynamic router using Command.

    Args:
        routes: Mapping of condition -> node name
        default: Default node if no condition matches
        condition_fn: Custom function to determine route

    Returns:
        Node function that returns Command with goto

    Usage:
        router = create_dynamic_router({
            "search_needed": "search_node",
            "memory_recall": "memory_node",
            "approval_required": "approval_node"
        })
    """

    def router_node(state: HITLState) -> Command:
        if condition_fn:
            next_node = condition_fn(state)
        else:
            # Check messages for routing hints
            last_message = state["messages"][-1].content if state["messages"] else ""

            next_node = default
            for condition, node in routes.items():
                if condition.lower() in last_message.lower():
                    next_node = node
                    break

        return Command(goto=next_node, update={"next_node": next_node})

    return router_node


def create_handoff_node(
    target_agent: str, handoff_message: str = None, include_context: bool = True
) -> Callable:
    """
    Create a handoff node for multi-agent systems.

    Args:
        target_agent: Name of the agent/node to hand off to
        handoff_message: Optional message to include
        include_context: Whether to pass current context

    Returns:
        Node function that performs handoff via Command

    Usage:
        handoff_to_support = create_handoff_node("support_agent")
        workflow.add_node("handoff", handoff_to_support)
    """

    def handoff_node(state: HITLState) -> Command:
        update = {}

        if handoff_message:
            update["messages"] = [AIMessage(content=handoff_message)]

        if include_context:
            update["handoff_context"] = {
                "from_agent": state.get("next_node", "unknown"),
                "user_info": state.get("user_info"),
                "pending_action": state.get("pending_action"),
            }

        return Command(goto=target_agent, update=update)

    return handoff_node


class DynamicFlowBuilder:
    """
    Builder for dynamic edgeless workflows using Command.

    Usage:
        builder = DynamicFlowBuilder()
        builder.add_dynamic_node("router", router_fn)
        builder.add_agent("search", search_agent)
        builder.add_agent("memory", memory_agent)
        workflow = builder.build()
    """

    def __init__(self, state_schema=HITLState):
        self.workflow = StateGraph(state_schema)
        self.nodes = {}
        self.entry_point = None

    def add_dynamic_node(
        self, name: str, node_fn: Callable, is_entry: bool = False
    ) -> "DynamicFlowBuilder":
        """Add a node that uses Command for routing."""
        self.workflow.add_node(name, node_fn)
        self.nodes[name] = {"type": "dynamic", "fn": node_fn}

        if is_entry:
            self.entry_point = name
            self.workflow.add_edge(START, name)

        return self

    def add_agent(
        self, name: str, agent_fn: Callable, next_node: str = None
    ) -> "DynamicFlowBuilder":
        """Add an agent node."""
        self.workflow.add_node(name, agent_fn)
        self.nodes[name] = {"type": "agent", "fn": agent_fn}

        if next_node:
            self.workflow.add_edge(name, next_node)

        return self

    def add_conditional_route(
        self, source: str, router_fn: Callable, routes: Dict[str, str]
    ) -> "DynamicFlowBuilder":
        """Add conditional routing from a node."""
        self.workflow.add_conditional_edges(source, router_fn, routes)
        return self

    def build(self, checkpointer=None):
        """Compile the workflow."""
        return self.workflow.compile(checkpointer=checkpointer)


# =============================================================================
# Interrupt Patterns - Human Approval
# =============================================================================


def create_approval_node(
    approval_message: str = "Approve this action?",
    timeout_seconds: int = None,
    on_approve: str = "execute",
    on_reject: str = "cancel",
    on_timeout: str = "timeout_handler",
) -> Callable:
    """
    Create an approval node using interrupt.

    Args:
        approval_message: Message shown to human
        timeout_seconds: Optional timeout
        on_approve: Node to go to on approval
        on_reject: Node to go to on rejection
        on_timeout: Node to go to on timeout

    Returns:
        Node function that pauses for approval

    Usage:
        approval = create_approval_node(
            "Approve sending email?",
            on_approve="send_email",
            on_reject="cancel"
        )
    """

    def approval_node(state: HITLState) -> Command:
        if not HITL_AVAILABLE:
            # Fallback: auto-approve
            print(f"⚠️ HITL not available, auto-approving: {approval_message}")
            return Command(
                goto=on_approve,
                update={"approval_status": ApprovalStatus.APPROVED.value},
            )

        # Build approval prompt
        action = state.get("pending_action", "unknown action")
        full_message = f"{approval_message}\n\nAction: {action}"

        # Interrupt for human input
        try:
            response = interrupt(full_message)

            if response is None and timeout_seconds:
                return Command(
                    goto=on_timeout,
                    update={"approval_status": ApprovalStatus.TIMEOUT.value},
                )

            # Parse response
            response_lower = str(response).lower().strip()

            if response_lower in ["yes", "y", "approve", "ok", "1", "true"]:
                return Command(
                    goto=on_approve,
                    update={"approval_status": ApprovalStatus.APPROVED.value},
                )
            elif response_lower in ["no", "n", "reject", "cancel", "0", "false"]:
                return Command(
                    goto=on_reject,
                    update={"approval_status": ApprovalStatus.REJECTED.value},
                )
            else:
                # Treat as edited input
                return Command(
                    goto=on_approve,
                    update={
                        "approval_status": ApprovalStatus.EDITED.value,
                        "pending_action": response,
                    },
                )

        except Exception as e:
            return Command(
                goto=on_reject,
                update={
                    "approval_status": ApprovalStatus.REJECTED.value,
                    "error_message": str(e),
                },
            )

    return approval_node


def create_review_node(
    review_prompt: str = "Review and edit if needed:", allow_edit: bool = True
) -> Callable:
    """
    Create a review node for human editing.

    Args:
        review_prompt: Prompt for review
        allow_edit: Whether to allow editing

    Returns:
        Node function that allows review/edit
    """

    def review_node(state: HITLState) -> Command:
        if not HITL_AVAILABLE:
            return Command(goto=END, update={"status": "reviewed"})

        # Show current content for review
        content = state.get("action_result", state.get("pending_action", ""))
        full_prompt = f"{review_prompt}\n\nCurrent: {content}"

        response = interrupt(full_prompt)

        if response and allow_edit:
            # User provided edited version
            return Command(
                goto=END, update={"action_result": response, "status": "edited"}
            )
        else:
            # User accepted as-is
            return Command(goto=END, update={"status": "reviewed"})

    return review_node


def create_tool_review_node(tool_calls_key: str = "tool_calls") -> Callable:
    """
    Create a node to review tool calls before execution.

    Usage:
        review = create_tool_review_node()
        workflow.add_node("review_tools", review)
    """

    def tool_review_node(state: HITLState) -> Command:
        if not HITL_AVAILABLE:
            return Command(goto="execute_tools")

        tool_calls = state.get(tool_calls_key, [])

        if not tool_calls:
            return Command(goto=END)

        # Format tool calls for review
        review_msg = "Review tool calls:\n"
        for i, call in enumerate(tool_calls):
            review_msg += f"\n{i+1}. {call.get('name', 'unknown')}"
            review_msg += f"\n   Args: {call.get('args', {})}"

        response = interrupt(review_msg)

        if response in ["yes", "y", "approve"]:
            return Command(goto="execute_tools")
        elif response in ["no", "n", "reject"]:
            return Command(goto=END, update={"status": "tools_rejected"})
        else:
            # Allow editing specific tool
            return Command(goto="edit_tools", update={"tool_edit_request": response})

    return tool_review_node


# =============================================================================
# Tools That Update State
# =============================================================================


def create_stateful_tool(
    name: str,
    description: str,
    state_updates: Dict[str, Any] = None,
    lookup_fn: Callable = None,
) -> Callable:
    """
    Create a tool that updates graph state via Command.

    Args:
        name: Tool name
        description: Tool description
        state_updates: Static state updates
        lookup_fn: Function to lookup and return updates

    Returns:
        Tool function that returns Command

    Usage:
        lookup_user = create_stateful_tool(
            "lookup_user",
            "Look up user info",
            lookup_fn=lambda user_id: {"user_info": db.get(user_id)}
        )
    """

    def tool_fn(**kwargs) -> Command:
        updates = {}

        # Static updates
        if state_updates:
            updates.update(state_updates)

        # Dynamic updates from lookup
        if lookup_fn:
            try:
                result = lookup_fn(**kwargs)
                if isinstance(result, dict):
                    updates.update(result)
                else:
                    updates["tool_result"] = result
            except Exception as e:
                updates["error_message"] = str(e)

        # Add tool message
        tool_msg = ToolMessage(
            content=f"Tool {name} executed: {updates}",
            tool_call_id=kwargs.get("tool_call_id", "unknown"),
        )
        updates.setdefault("messages", []).append(tool_msg)

        return Command(update=updates)

    tool_fn.__name__ = name
    tool_fn.__doc__ = description

    return tool_fn


class StatefulToolNode:
    """
    Custom tool node that handles Command returns from tools.

    Unlike the prebuilt ToolNode, this properly processes
    tools that return Command objects for state updates.

    Usage:
        tool_node = StatefulToolNode([lookup_user, update_customer])
        workflow.add_node("tools", tool_node)
    """

    def __init__(self, tools: List[Callable]):
        self.tools = {t.__name__: t for t in tools}

    def __call__(self, state: HITLState) -> Union[Dict, Command]:
        """Execute tools and aggregate state updates."""
        messages = state.get("messages", [])

        # Find tool calls in messages
        tool_calls = []
        for msg in reversed(messages):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = msg.tool_calls
                break

        if not tool_calls:
            return {"status": "no_tools_to_execute"}

        # Execute tools and collect updates
        all_updates = {}
        tool_messages = []

        for call in tool_calls:
            tool_name = call.get("name", call.get("function", {}).get("name"))
            tool_args = call.get("args", call.get("function", {}).get("arguments", {}))
            tool_id = call.get("id", "unknown")

            if tool_name in self.tools:
                tool_fn = self.tools[tool_name]

                try:
                    result = tool_fn(**tool_args, tool_call_id=tool_id)

                    if isinstance(result, Command):
                        # Merge Command updates
                        all_updates.update(result.update or {})

                        # If Command has goto, use it
                        if result.goto:
                            return result
                    else:
                        # Regular tool result
                        tool_messages.append(
                            ToolMessage(content=str(result), tool_call_id=tool_id)
                        )

                except Exception as e:
                    tool_messages.append(
                        ToolMessage(content=f"Error: {e}", tool_call_id=tool_id)
                    )
            else:
                tool_messages.append(
                    ToolMessage(
                        content=f"Unknown tool: {tool_name}", tool_call_id=tool_id
                    )
                )

        # Add tool messages to updates
        if tool_messages:
            all_updates.setdefault("messages", []).extend(tool_messages)

        return all_updates


# Customer support tools (example)
def create_customer_support_tools():
    """
    Create a set of customer support tools that update state.

    Returns:
        List of tools for customer support workflows
    """
    # Mock database
    _db = {
        "user_123": {
            "name": "John Doe",
            "email": "john@example.com",
            "tier": "premium",
        },
        "user_456": {
            "name": "Jane Smith",
            "email": "jane@example.com",
            "tier": "basic",
        },
    }

    def lookup_customer(customer_id: str, **kwargs) -> Command:
        """Look up customer information."""
        data = _db.get(customer_id, {"error": "Customer not found"})
        return Command(
            update={
                "user_id": customer_id,
                "user_info": data,
                "messages": [
                    ToolMessage(
                        content=f"Found customer: {data}",
                        tool_call_id=kwargs.get("tool_call_id", ""),
                    )
                ],
            }
        )

    def update_customer(
        customer_id: str, field: str, value: Any, **kwargs
    ) -> Command:  # noqa: F811 - parameter name intentionally shadows dataclasses.field
        """Update customer information."""
        if customer_id in _db:
            _db[customer_id][field] = value
            return Command(
                update={
                    "user_info": _db[customer_id],
                    "action_result": f"Updated {field} to {value}",
                    "messages": [
                        ToolMessage(
                            content=f"Updated {field}",
                            tool_call_id=kwargs.get("tool_call_id", ""),
                        )
                    ],
                }
            )
        return Command(update={"error_message": "Customer not found"})

    def escalate_to_human(**kwargs) -> Command:
        """Escalate to human agent."""
        return Command(
            goto="human_agent",
            update={
                "status": "escalated",
                "messages": [
                    ToolMessage(
                        content="Escalating to human agent",
                        tool_call_id=kwargs.get("tool_call_id", ""),
                    )
                ],
            },
        )

    return [lookup_customer, update_customer, escalate_to_human]


# =============================================================================
# HITL Workflow Builder
# =============================================================================


class HITLWorkflow:
    """
    Complete Human-in-the-Loop workflow with all patterns.

    Features:
    - Dynamic routing with Command
    - Human approval with interrupt
    - Tools that update state
    - Multi-agent handoffs

    Usage:
        workflow = HITLWorkflow()

        # Run with approval
        result = workflow.run_with_approval(
            action="Send marketing email to 10,000 users",
            thread_id="campaign-001"
        )

        # Resume after interrupt
        result = workflow.resume("campaign-001", "yes")
    """

    def __init__(self, db_path: str = "hitl_checkpoints.db"):
        self.db_path = db_path
        self._workflow = None
        self._checkpointer = None
        self._build_workflow()

    def _build_workflow(self):
        """Build the HITL workflow."""
        workflow = StateGraph(HITLState)

        # Add nodes
        workflow.add_node("router", self._router_node)
        workflow.add_node("approval", self._approval_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("cancel", self._cancel_node)
        workflow.add_node("review", self._review_node)

        # Add edges
        workflow.add_edge(START, "router")

        # Conditional routing from router
        workflow.add_conditional_edges(
            "router",
            lambda state: state.get("next_node", END),
            {
                "approval": "approval",
                "execute": "execute",
                "review": "review",
                END: END,
            },
        )

        # From approval
        workflow.add_conditional_edges(
            "approval",
            lambda state: state.get("next_node", END),
            {"execute": "execute", "cancel": "cancel", END: END},
        )

        workflow.add_edge("execute", "review")
        workflow.add_edge("review", END)
        workflow.add_edge("cancel", END)

        # Compile with checkpointer
        if SQLITE_AVAILABLE:
            conn = sqlite3.connect(self.db_path)
            self._checkpointer = SqliteSaver(conn)
            self._workflow = workflow.compile(checkpointer=self._checkpointer)
        else:
            self._workflow = workflow.compile()

    def _router_node(self, state: HITLState) -> Dict:
        """Route based on action type."""
        action = state.get("pending_action", "")

        # Determine if approval needed
        critical_keywords = ["delete", "send", "publish", "deploy", "payment"]
        needs_approval = any(kw in action.lower() for kw in critical_keywords)

        if needs_approval:
            return {"next_node": "approval", "status": "awaiting_approval"}
        else:
            return {"next_node": "execute", "status": "auto_approved"}

    def _approval_node(self, state: HITLState) -> Dict:
        """Request human approval."""
        if not HITL_AVAILABLE:
            return {
                "approval_status": ApprovalStatus.APPROVED.value,
                "next_node": "execute",
            }

        action = state.get("pending_action", "unknown action")
        response = interrupt(f"Approve action: {action}? (yes/no)")

        if response in ["yes", "y", "approve"]:
            return {
                "approval_status": ApprovalStatus.APPROVED.value,
                "next_node": "execute",
            }
        else:
            return {
                "approval_status": ApprovalStatus.REJECTED.value,
                "next_node": "cancel",
            }

    def _execute_node(self, state: HITLState) -> Dict:
        """Execute the approved action."""
        action = state.get("pending_action", "")

        # Simulate execution
        time.sleep(0.1)

        return {"action_result": f"Executed: {action}", "status": "executed"}

    def _cancel_node(self, state: HITLState) -> Dict:
        """Handle cancelled action."""
        return {"status": "cancelled", "action_result": None}

    def _review_node(self, state: HITLState) -> Dict:
        """Review executed action."""
        return {"status": "completed"}

    def run_with_approval(
        self, action: str, thread_id: str = None, user_id: str = None
    ) -> Dict:
        """
        Run workflow with approval step.

        Args:
            action: Action to approve
            thread_id: Thread ID for persistence
            user_id: Optional user ID

        Returns:
            Workflow result
        """
        thread_id = thread_id or f"hitl-{int(time.time())}"

        state = create_initial_hitl_state(user_id=user_id)
        state["pending_action"] = action

        config = {"configurable": {"thread_id": thread_id}}

        result = self._workflow.invoke(state, config)

        return {
            "thread_id": thread_id,
            "status": result.get("status"),
            "approval_status": result.get("approval_status"),
            "action_result": result.get("action_result"),
        }

    def resume(self, thread_id: str, response: str) -> Dict:
        """
        Resume an interrupted workflow.

        Args:
            thread_id: Thread ID to resume
            response: Human response

        Returns:
            Workflow result
        """
        if not HITL_AVAILABLE:
            return {"error": "HITL not available"}

        config = {"configurable": {"thread_id": thread_id}}

        result = self._workflow.invoke(Command(resume=response), config)

        return {
            "thread_id": thread_id,
            "status": result.get("status"),
            "approval_status": result.get("approval_status"),
            "action_result": result.get("action_result"),
        }

    def get_pending_approvals(self) -> List[Dict]:
        """Get list of pending approvals."""
        # This would query the checkpointer for interrupted threads
        # Simplified implementation
        return []


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # State
    "HITLState",
    "ApprovalStatus",
    "create_initial_hitl_state",
    # Command patterns
    "create_dynamic_router",
    "create_handoff_node",
    "DynamicFlowBuilder",
    # Interrupt patterns
    "create_approval_node",
    "create_review_node",
    "create_tool_review_node",
    # Stateful tools
    "create_stateful_tool",
    "StatefulToolNode",
    "create_customer_support_tools",
    # Workflow
    "HITLWorkflow",
    # Availability
    "HITL_AVAILABLE",
]
