"""
Efficiency Configuration
========================
Configuration dataclasses and type definitions for efficiency optimizations.
"""

from typing import TypedDict, Annotated, List, Any, Dict
from dataclasses import dataclass

from langchain_core.messages import BaseMessage


@dataclass
class EfficiencyConfig:
    """Configuration for efficiency optimizations."""

    # LLM settings
    use_quantization: bool = True
    quantization_bits: int = 4  # 4-bit for max reduction
    local_model: str = "llama3:4bit"

    # Retrieval settings
    use_hybrid_search: bool = True
    retrieval_k: int = 5

    # State management
    auto_error_handling: bool = True
    max_retries: int = 3

    # Resource limits
    max_context_tokens: int = 4096
    batch_size: int = 10

    # Reflection settings
    enable_reflection: bool = True
    reflection_threshold: float = 0.8  # Score threshold for passing critique


class EfficientState(TypedDict):
    """
    Optimized state definition.

    Before (28 lines, 3 useState):
        state = {"retry_count": 0, "status": "pending"}
        try:
            result = process(state)
        except Exception as e:
            state["retry_count"] += 1
            state["status"] = "error"

    After (12 lines, automatic):
        Uses middleware for auto error handling
    """

    messages: Annotated[List[BaseMessage], "add_messages"]
    status: str
    retry_count: int
    error_message: str
    output: Any
    # Efficiency metadata
    llm_calls: int
    tokens_used: int
    cache_hits: int


class ReflectiveState(TypedDict):
    """
    Extended state for self-correcting workflows.

    Includes all EfficientState fields plus reflection/critique fields.
    """

    # Base fields from EfficientState
    messages: Annotated[List[BaseMessage], "add_messages"]
    status: str
    retry_count: int
    error_message: str
    output: Any
    llm_calls: int
    tokens_used: int
    cache_hits: int

    # Critique fields
    critique_result: str
    critique_score: float
    critique_issues: List[str]
    critique_suggestions: List[str]
    critique_passed: bool
    needs_correction: bool
    requirements_met: List[str]
    requirements_missed: List[str]

    # Correction fields
    correction_context: Dict[str, Any]
    correction_history: List[Dict[str, Any]]

    # Logic error tracking
    logic_errors: List[str]
    business_rule_violations: List[str]


def create_initial_state() -> EfficientState:
    """Create optimized initial state."""
    return {
        "messages": [],
        "status": "pending",
        "retry_count": 0,
        "error_message": "",
        "output": None,
        "llm_calls": 0,
        "tokens_used": 0,
        "cache_hits": 0,
    }


def create_reflective_state() -> ReflectiveState:
    """Create initial state for reflective workflows."""
    return {
        # Base fields
        "messages": [],
        "status": "pending",
        "retry_count": 0,
        "error_message": "",
        "output": None,
        "llm_calls": 0,
        "tokens_used": 0,
        "cache_hits": 0,
        # Critique fields
        "critique_result": "",
        "critique_score": 0.0,
        "critique_issues": [],
        "critique_suggestions": [],
        "critique_passed": False,
        "needs_correction": False,
        "requirements_met": [],
        "requirements_missed": [],
        # Correction fields
        "correction_context": {},
        "correction_history": [],
        # Logic error tracking
        "logic_errors": [],
        "business_rule_violations": [],
    }


__all__ = [
    "EfficiencyConfig",
    "EfficientState",
    "ReflectiveState",
    "create_initial_state",
    "create_reflective_state",
]
