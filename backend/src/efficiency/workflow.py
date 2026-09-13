"""
Workflow Builder
================
Efficient LangGraph workflow construction with automatic optimizations
and self-correction capabilities.

Features:
- Auto error handling middleware
- Retry logic built-in
- Efficient state management
- Reflection/critique loops for 100% reliability
- Memory integration for learning from mistakes
"""

from typing import Dict, Callable, List, Optional, Any
import logging

from langgraph.graph import StateGraph, END

from .config import EfficiencyConfig, EfficientState, ReflectiveState
from .middleware import error_middleware
from .reflection import (
    create_correction_router,
    RequirementChecker,
    ReflectionLoop,
)

logger = logging.getLogger(__name__)


def build_efficient_workflow(
    nodes: Dict[str, Callable],
    edges: List[tuple],
    conditional_edges: Dict[str, tuple] = None,
    config: EfficiencyConfig = None,
) -> StateGraph:
    """
    Build an efficient workflow with automatic optimizations.

    Features:
    - Auto error handling middleware
    - Retry logic built-in
    - Efficient state management

    Usage:
        workflow = build_efficient_workflow(
            nodes={"plan": plan_fn, "search": search_fn},
            edges=[("plan", "search"), ("search", END)],
            config=EfficiencyConfig(auto_error_handling=True)
        )
    """
    config = config or EfficiencyConfig()
    workflow = StateGraph(EfficientState)

    # Add nodes with middleware
    for name, func in nodes.items():
        if config.auto_error_handling:
            wrapped = error_middleware(func)
        else:
            wrapped = func
        workflow.add_node(name, wrapped)

    # Add edges
    for edge in edges:
        if len(edge) == 2:
            workflow.add_edge(edge[0], edge[1])
        elif len(edge) == 3:
            # With condition
            workflow.add_edge(edge[0], edge[1])

    # Add conditional edges
    if conditional_edges:
        for source, (router, mapping) in conditional_edges.items():
            workflow.add_conditional_edges(source, router, mapping)

    return workflow


def build_reflective_workflow(
    nodes: Dict[str, Callable],
    edges: List[tuple],
    requirements: List[str] = None,
    config: EfficiencyConfig = None,
    project_id: Optional[str] = None,
    critique_after: Optional[List[str]] = None,
) -> StateGraph:
    """
    Build a workflow with automatic reflection/self-correction.

    This adds critique nodes after specified nodes to evaluate output
    against requirements and trigger corrections if needed.

    Args:
        nodes: Dict of node_name -> node_function
        edges: List of (source, target) edges
        requirements: List of requirements to check against
        config: Efficiency configuration
        project_id: Project ID for memory integration
        critique_after: List of node names after which to add critique
                       (defaults to all nodes except END)

    Usage:
        workflow = build_reflective_workflow(
            nodes={"generate": generate_fn, "validate": validate_fn},
            edges=[("generate", "validate"), ("validate", END)],
            requirements=["Must include error handling", "Must validate input"],
            critique_after=["generate"],  # Add critique after generate node
        )

    The workflow will:
    1. Execute the generate node
    2. Run critique to check requirements
    3. If requirements not met, route to correction
    4. Correction modifies state and re-runs generate
    5. Repeat until requirements met or max corrections reached
    """
    config = config or EfficiencyConfig()
    requirements = requirements or []
    critique_after = critique_after or list(nodes.keys())

    # Use reflective state
    workflow = StateGraph(ReflectiveState)

    # Create requirement checker
    checker = RequirementChecker(requirements)

    # Add original nodes with middleware
    for name, func in nodes.items():
        if config.auto_error_handling:
            wrapped = error_middleware(func)
        else:
            wrapped = func
        workflow.add_node(name, wrapped)

    # Add critique and correction nodes for specified nodes
    for node_name in critique_after:
        if node_name not in nodes:
            continue

        critique_name = f"{node_name}_critique"
        correct_name = f"{node_name}_correct"

        # Create and add critique node
        critique_fn = _create_critique_node_with_memory(
            checker=checker,
            project_id=project_id,
            source_node=node_name,
        )
        workflow.add_node(critique_name, critique_fn)

        # Create and add correction node
        correct_fn = _create_correction_node(
            original_node=nodes[node_name],
            config=config,
        )
        workflow.add_node(correct_name, correct_fn)

    # Build modified edges with critique routing
    processed_edges = set()

    for edge in edges:
        source, target = edge[0], edge[1]

        if source in critique_after and source not in processed_edges:
            critique_name = f"{source}_critique"
            correct_name = f"{source}_correct"

            # source -> critique
            workflow.add_edge(source, critique_name)

            # critique -> (target OR correct) based on result
            router = create_correction_router(
                pass_node=target if target != END else "end",
                correct_node=correct_name,
                max_corrections=config.max_retries,
            )

            mapping = {
                target if target != END else "end": target,
                correct_name: correct_name,
            }
            workflow.add_conditional_edges(critique_name, router, mapping)

            # correct -> source (retry loop)
            workflow.add_edge(correct_name, source)

            processed_edges.add(source)
        else:
            # Normal edge
            workflow.add_edge(source, target)

    return workflow


def _create_critique_node_with_memory(
    checker: RequirementChecker,
    project_id: Optional[str],
    source_node: str,
) -> Callable:
    """Create a critique node with memory integration."""
    memory_service = None
    if project_id:
        try:
            from core.project_memory import get_project_memory_service

            memory_service = get_project_memory_service(project_id)
        except ImportError:
            pass

    def critique_node(state: Dict[str, Any]) -> Dict[str, Any]:
        output = state.get("output")
        critique = checker.check(output, state)

        # Log critique result
        logger.info(
            f"Critique [{source_node}]: {critique.result.value} "
            f"(score: {critique.score:.2f}, issues: {len(critique.issues)})"
        )

        # Store in memory if critique failed
        if not critique.passed and memory_service:
            try:
                memory_service.store_context(
                    content=f"Critique failed for {source_node}: {'; '.join(critique.issues)}",
                    title=f"Critique Failure - {source_node}",
                    tags=["critique", "correction-needed", source_node],
                )
            except Exception as e:
                logger.warning(f"Failed to store critique in memory: {e}")

        return {
            "critique_result": critique.result.value,
            "critique_score": critique.score,
            "critique_issues": critique.issues,
            "critique_suggestions": critique.suggestions,
            "critique_passed": critique.passed,
            "needs_correction": not critique.passed,
            "requirements_met": critique.requirements_met,
            "requirements_missed": critique.requirements_missed,
        }

    return critique_node


def _create_correction_node(
    original_node: Callable,
    config: EfficiencyConfig,
) -> Callable:
    """Create a correction node that adjusts state and prepares for retry."""

    def correction_node(state: Dict[str, Any]) -> Dict[str, Any]:
        # Increment retry count
        retry_count = state.get("retry_count", 0) + 1

        # Add critique context for the retry
        correction_context = {
            "previous_issues": state.get("critique_issues", []),
            "previous_suggestions": state.get("critique_suggestions", []),
            "requirements_missed": state.get("requirements_missed", []),
            "attempt_number": retry_count,
        }

        logger.info(
            f"Correction attempt {retry_count}: addressing {len(correction_context['previous_issues'])} issues"
        )

        return {
            "retry_count": retry_count,
            "correction_context": correction_context,
            "status": "correcting",
            # Clear previous critique results
            "critique_passed": False,
            "needs_correction": False,
        }

    return correction_node


def build_self_correcting_agent(
    task_fn: Callable,
    requirements: List[str],
    project_id: Optional[str] = None,
    max_corrections: int = 3,
    config: EfficiencyConfig = None,
) -> Callable:
    """
    Build a self-correcting agent wrapper.

    This is a simpler alternative to build_reflective_workflow for
    single-task agents that need self-correction.

    Args:
        task_fn: The main task function
        requirements: Requirements to check
        project_id: Project ID for memory
        max_corrections: Maximum correction attempts
        config: Configuration

    Returns:
        A wrapped function with self-correction

    Usage:
        @build_self_correcting_agent(
            task_fn=None,  # Will be the decorated function
            requirements=["Must include tests"],
            max_corrections=3
        )
        def generate_code(state):
            return {"output": code}
    """
    config = config or EfficiencyConfig()

    def decorator(fn: Callable) -> Callable:
        actual_fn = task_fn or fn
        loop = ReflectionLoop(
            requirements=requirements,
            max_corrections=max_corrections,
            project_id=project_id,
        )

        def wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
            def execute(s):
                result = actual_fn(s)
                if isinstance(result, dict):
                    return result
                return {"output": result}

            def correct(output, critique, s):
                # Add critique to state for correction
                s["_critique"] = critique.to_dict()
                s["_correction_attempt"] = True
                s["_previous_output"] = output
                return execute(s)

            result, reflection_state = loop.run(execute, correct, state)

            # Merge reflection metadata
            if isinstance(result, dict):
                result["_reflection"] = {
                    "attempts": reflection_state.current_attempt,
                    "converged": reflection_state.converged,
                    "mistakes": len(reflection_state.session_mistakes),
                }
            else:
                result = {
                    "output": result,
                    "_reflection": {
                        "attempts": reflection_state.current_attempt,
                        "converged": reflection_state.converged,
                    },
                }

            return result

        return wrapped

    # Support both @decorator and @decorator() syntax
    if callable(task_fn):
        return decorator(task_fn)
    return decorator


__all__ = [
    "build_efficient_workflow",
    "build_reflective_workflow",
    "build_self_correcting_agent",
]
