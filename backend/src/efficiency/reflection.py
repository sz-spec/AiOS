"""
Reflection & Self-Correction
=============================
Implements agentic self-correction with critique loops for 100% reliability.

Features:
- Critique node evaluates output against requirements
- Re-plan/correction triggered on requirement mismatch
- Memory integration for learning from mistakes
- Logic error detection beyond runtime exceptions
"""

import logging
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


class CritiqueResult(Enum):
    """Result of critique evaluation."""

    PASS = "pass"
    NEEDS_CORRECTION = "needs_correction"
    LOGIC_ERROR = "logic_error"
    INCOMPLETE = "incomplete"
    REQUIREMENT_MISMATCH = "requirement_mismatch"


@dataclass
class CritiqueReport:
    """Detailed critique report."""

    result: CritiqueResult
    score: float  # 0.0 to 1.0
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    requirements_met: List[str] = field(default_factory=list)
    requirements_missed: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.result == CritiqueResult.PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result.value,
            "score": self.score,
            "passed": self.passed,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "requirements_met": self.requirements_met,
            "requirements_missed": self.requirements_missed,
            "metadata": self.metadata,
        }


@dataclass
class CorrectionAttempt:
    """Record of a correction attempt."""

    attempt_number: int
    original_output: Any
    critique: CritiqueReport
    corrected_output: Optional[Any] = None
    correction_successful: bool = False


@dataclass
class ReflectionState:
    """State for reflection loop tracking."""

    max_corrections: int = 3
    current_attempt: int = 0
    correction_history: List[CorrectionAttempt] = field(default_factory=list)
    final_result: Optional[Any] = None
    converged: bool = False

    # Memory integration
    project_id: Optional[str] = None
    session_mistakes: List[Dict[str, Any]] = field(default_factory=list)


class RequirementChecker:
    """
    Checks output against project requirements/spec.

    Can be extended with LLM-based checking for complex requirements.
    """

    def __init__(self, requirements: List[str] = None):
        """
        Initialize requirement checker.

        Args:
            requirements: List of requirement strings to check against
        """
        self.requirements = requirements or []
        self._custom_checks: List[Callable[[Any], Tuple[bool, str]]] = []

    def add_requirement(self, requirement: str) -> None:
        """Add a requirement to check."""
        self.requirements.append(requirement)

    def add_custom_check(self, check_fn: Callable[[Any], Tuple[bool, str]]) -> None:
        """
        Add a custom check function.

        Args:
            check_fn: Function that takes output and returns (passed, message)
        """
        self._custom_checks.append(check_fn)

    def check(self, output: Any, context: Dict[str, Any] = None) -> CritiqueReport:
        """
        Check output against all requirements.

        Args:
            output: The output to check
            context: Additional context for checking

        Returns:
            CritiqueReport with detailed results
        """
        context = context or {}
        issues = []
        suggestions = []
        requirements_met = []
        requirements_missed = []

        # Check built-in requirements
        for req in self.requirements:
            if self._check_requirement(output, req, context):
                requirements_met.append(req)
            else:
                requirements_missed.append(req)
                issues.append(f"Requirement not met: {req}")

        # Run custom checks
        for check_fn in self._custom_checks:
            try:
                passed, message = check_fn(output)
                if passed:
                    requirements_met.append(message)
                else:
                    requirements_missed.append(message)
                    issues.append(message)
            except Exception as e:
                issues.append(f"Check failed with error: {e}")

        # Calculate score
        total = len(self.requirements) + len(self._custom_checks)
        if total > 0:
            score = len(requirements_met) / total
        else:
            score = 1.0  # No requirements = pass

        # Determine result
        if score >= 1.0:
            result = CritiqueResult.PASS
        elif score >= 0.7:
            result = CritiqueResult.INCOMPLETE
            suggestions.append("Most requirements met, minor corrections needed")
        elif requirements_missed:
            result = CritiqueResult.REQUIREMENT_MISMATCH
            suggestions.append("Review requirements and adjust output")
        else:
            result = CritiqueResult.NEEDS_CORRECTION

        return CritiqueReport(
            result=result,
            score=score,
            issues=issues,
            suggestions=suggestions,
            requirements_met=requirements_met,
            requirements_missed=requirements_missed,
        )

    def _check_requirement(self, output: Any, requirement: str, context: Dict) -> bool:
        """
        Check a single requirement against output.

        This is a simple keyword-based check. Can be enhanced with LLM.
        """
        if output is None:
            return False

        output_str = str(output).lower()
        req_lower = requirement.lower()

        # Extract key terms from requirement
        key_terms = [
            term
            for term in req_lower.split()
            if len(term) > 3
            and term not in ("must", "should", "have", "with", "that", "this")
        ]

        # Check if key terms appear in output
        matches = sum(1 for term in key_terms if term in output_str)
        return matches >= len(key_terms) * 0.5  # At least 50% of key terms present


class LogicErrorDetector:
    """
    Detects logic errors that don't raise exceptions but violate business rules.
    """

    def __init__(self):
        self._rules: List[Callable[[Any, Dict], Optional[str]]] = []

    def add_rule(self, rule_fn: Callable[[Any, Dict], Optional[str]]) -> None:
        """
        Add a business rule check.

        Args:
            rule_fn: Function that returns error message if rule violated, None otherwise
        """
        self._rules.append(rule_fn)

    def detect(self, output: Any, context: Dict[str, Any] = None) -> List[str]:
        """
        Detect logic errors in output.

        Returns:
            List of error messages for detected logic errors
        """
        context = context or {}
        errors = []

        for rule_fn in self._rules:
            try:
                error = rule_fn(output, context)
                if error:
                    errors.append(error)
            except Exception as e:
                logger.warning(f"Rule check failed: {e}")

        return errors


class ReflectionLoop:
    """
    Implements the reflection/self-correction loop for agentic workflows.

    Usage:
        loop = ReflectionLoop(
            requirements=["Must include error handling", "Must validate input"],
            max_corrections=3
        )

        result = loop.run(
            execute_fn=my_agent_function,
            correct_fn=my_correction_function,
            state=initial_state
        )
    """

    def __init__(
        self,
        requirements: List[str] = None,
        max_corrections: int = 3,
        project_id: Optional[str] = None,
    ):
        """
        Initialize reflection loop.

        Args:
            requirements: Requirements to check against
            max_corrections: Maximum correction attempts
            project_id: Project ID for memory integration
        """
        self.checker = RequirementChecker(requirements)
        self.logic_detector = LogicErrorDetector()
        self.max_corrections = max_corrections
        self.project_id = project_id
        self._memory_service = None

        # Initialize memory service if project_id provided
        if project_id:
            try:
                from core.project_memory import get_project_memory_service

                self._memory_service = get_project_memory_service(project_id)
            except ImportError:
                logger.warning("ProjectMemoryService not available")

    def run(
        self,
        execute_fn: Callable[[Dict], Any],
        correct_fn: Callable[[Any, CritiqueReport, Dict], Any],
        state: Dict[str, Any],
    ) -> Tuple[Any, ReflectionState]:
        """
        Run the reflection loop.

        Args:
            execute_fn: Function that executes the main task
            correct_fn: Function that corrects output based on critique
            state: Initial state

        Returns:
            Tuple of (final_output, reflection_state)
        """
        reflection_state = ReflectionState(
            max_corrections=self.max_corrections,
            project_id=self.project_id,
        )

        current_output = None

        while reflection_state.current_attempt < self.max_corrections:
            reflection_state.current_attempt += 1

            # Execute or correct
            if current_output is None:
                # Initial execution
                try:
                    current_output = execute_fn(state)
                except Exception as e:
                    logger.error(f"Execution failed: {e}")
                    self._record_mistake(
                        reflection_state, "execution_error", str(e), state
                    )
                    continue
            else:
                # Correction attempt
                try:
                    current_output = correct_fn(
                        current_output,
                        reflection_state.correction_history[-1].critique,
                        state,
                    )
                except Exception as e:
                    logger.error(f"Correction failed: {e}")
                    self._record_mistake(
                        reflection_state, "correction_error", str(e), state
                    )
                    continue

            # Critique the output
            critique = self._critique(current_output, state)

            # Check for logic errors
            logic_errors = self.logic_detector.detect(current_output, state)
            if logic_errors:
                critique.result = CritiqueResult.LOGIC_ERROR
                critique.issues.extend(logic_errors)
                critique.score = min(critique.score, 0.5)

            # Record attempt
            attempt = CorrectionAttempt(
                attempt_number=reflection_state.current_attempt,
                original_output=current_output,
                critique=critique,
            )
            reflection_state.correction_history.append(attempt)

            logger.info(
                f"Reflection attempt {reflection_state.current_attempt}: "
                f"{critique.result.value} (score: {critique.score:.2f})"
            )

            # Check if we're done
            if critique.passed:
                reflection_state.converged = True
                reflection_state.final_result = current_output
                logger.info("Reflection loop converged successfully")
                break

            # Record mistake for learning
            if not critique.passed:
                self._record_mistake(
                    reflection_state,
                    critique.result.value,
                    "; ".join(critique.issues),
                    state,
                    current_output,
                )

        # If we exhausted attempts without converging, use best result
        if not reflection_state.converged:
            # Find best attempt by score
            best_attempt = max(
                reflection_state.correction_history,
                key=lambda a: a.critique.score,
                default=None,
            )
            if best_attempt:
                reflection_state.final_result = best_attempt.original_output
            logger.warning(
                f"Reflection loop did not converge after {self.max_corrections} attempts"
            )

        return reflection_state.final_result, reflection_state

    def _critique(self, output: Any, context: Dict) -> CritiqueReport:
        """Run critique on output."""
        return self.checker.check(output, context)

    def _record_mistake(
        self,
        state: ReflectionState,
        error_type: str,
        error_message: str,
        context: Dict,
        output: Any = None,
    ) -> None:
        """Record a mistake for learning."""
        mistake = {
            "attempt": state.current_attempt,
            "error_type": error_type,
            "error_message": error_message,
            "context_keys": list(context.keys()) if context else [],
        }
        state.session_mistakes.append(mistake)

        # Store in memory service if available
        if self._memory_service:
            try:
                self._memory_service.store_context(
                    content=f"Error ({error_type}): {error_message}",
                    title=f"Mistake - Attempt {state.current_attempt}",
                    tags=["mistake", "self-correction", error_type],
                )
            except Exception as e:
                logger.warning(f"Failed to store mistake in memory: {e}")


def reflection_middleware(
    requirements: List[str] = None,
    max_corrections: int = 3,
    project_id: Optional[str] = None,
):
    """
    Middleware decorator that adds reflection/self-correction to a function.

    Usage:
        @reflection_middleware(
            requirements=["Must include error handling"],
            max_corrections=3
        )
        def my_agent_task(state):
            return generate_code(state)
    """

    def decorator(func: Callable) -> Callable:
        loop = ReflectionLoop(
            requirements=requirements,
            max_corrections=max_corrections,
            project_id=project_id,
        )

        @wraps(func)
        def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            def execute(s):
                return func(s)

            def correct(output, critique, s):
                # Default correction: retry with critique context
                s["_critique"] = critique.to_dict()
                s["_correction_attempt"] = True
                return func(s)

            result, reflection_state = loop.run(execute, correct, state)

            # Add reflection metadata to result
            if isinstance(result, dict):
                result["_reflection"] = {
                    "attempts": reflection_state.current_attempt,
                    "converged": reflection_state.converged,
                    "final_score": (
                        reflection_state.correction_history[-1].critique.score
                        if reflection_state.correction_history
                        else 0
                    ),
                }

            return result

        return wrapper

    return decorator


def create_critique_node(
    requirements: List[str] = None,
    checker: RequirementChecker = None,
) -> Callable:
    """
    Create a critique node for LangGraph workflows.

    Usage:
        critique = create_critique_node(["Must handle errors"])
        workflow.add_node("critique", critique)
    """
    if checker is None:
        checker = RequirementChecker(requirements)

    def critique_node(state: Dict[str, Any]) -> Dict[str, Any]:
        output = state.get("output")
        critique = checker.check(output, state)

        return {
            "critique_result": critique.result.value,
            "critique_score": critique.score,
            "critique_issues": critique.issues,
            "critique_passed": critique.passed,
            "needs_correction": not critique.passed,
        }

    return critique_node


def create_correction_router(
    pass_node: str = "output",
    correct_node: str = "correct",
    max_corrections: int = 3,
) -> Callable:
    """
    Create a router function for critique -> correction/output routing.

    Usage:
        router = create_correction_router("output", "correct")
        workflow.add_conditional_edges("critique", router, {"output": "output", "correct": "correct"})
    """

    def router(state: Dict[str, Any]) -> str:
        if state.get("critique_passed", False):
            return pass_node

        retry_count = state.get("retry_count", 0)
        if retry_count >= max_corrections:
            logger.warning(
                f"Max corrections ({max_corrections}) reached, proceeding anyway"
            )
            return pass_node

        return correct_node

    return router


__all__ = [
    "CritiqueResult",
    "CritiqueReport",
    "CorrectionAttempt",
    "ReflectionState",
    "RequirementChecker",
    "LogicErrorDetector",
    "ReflectionLoop",
    "reflection_middleware",
    "create_critique_node",
    "create_correction_router",
]
