"""
Efficiency Middleware
=====================
Error handling, retry, and logic error detection middleware
for efficient and reliable state management.
"""

import time
import logging
from typing import Callable, Dict, Any, List, Tuple
from functools import wraps
from dataclasses import dataclass

from .config import EfficientState

logger = logging.getLogger(__name__)


# =============================================================================
# Error Types
# =============================================================================


class LogicError(Exception):
    """
    Exception for logic errors that don't raise runtime exceptions
    but violate business rules.
    """

    def __init__(self, message: str, rule: str = None, context: Dict = None):
        super().__init__(message)
        self.rule = rule
        self.context = context or {}


@dataclass
class BusinessRule:
    """A business rule that can be checked against output."""

    name: str
    description: str
    check_fn: Callable[[Any, Dict], Tuple[bool, str]]
    severity: str = "error"  # "error", "warning", "info"


class BusinessRuleRegistry:
    """Registry for business rules that detect logic errors."""

    def __init__(self):
        self._rules: List[BusinessRule] = []

    def register(
        self,
        name: str,
        check_fn: Callable[[Any, Dict], Tuple[bool, str]],
        description: str = "",
        severity: str = "error",
    ) -> None:
        """Register a business rule."""
        self._rules.append(
            BusinessRule(
                name=name,
                description=description,
                check_fn=check_fn,
                severity=severity,
            )
        )

    def check_all(self, output: Any, context: Dict = None) -> List[Dict[str, Any]]:
        """
        Check all rules and return violations.

        Returns:
            List of violation dicts with rule name, message, and severity
        """
        context = context or {}
        violations = []

        for rule in self._rules:
            try:
                passed, message = rule.check_fn(output, context)
                if not passed:
                    violations.append(
                        {
                            "rule": rule.name,
                            "message": message,
                            "severity": rule.severity,
                            "description": rule.description,
                        }
                    )
            except Exception as e:
                logger.warning(f"Rule check '{rule.name}' failed: {e}")
                violations.append(
                    {
                        "rule": rule.name,
                        "message": f"Rule check failed: {e}",
                        "severity": "error",
                    }
                )

        return violations


# Global rule registry
_rule_registry = BusinessRuleRegistry()


def register_business_rule(
    name: str,
    description: str = "",
    severity: str = "error",
):
    """
    Decorator to register a business rule function.

    Usage:
        @register_business_rule("no_empty_output", "Output must not be empty")
        def check_not_empty(output, context) -> Tuple[bool, str]:
            if not output:
                return False, "Output is empty"
            return True, ""
    """

    def decorator(fn: Callable[[Any, Dict], Tuple[bool, str]]):
        _rule_registry.register(name, fn, description, severity)
        return fn

    return decorator


# =============================================================================
# Core Middleware
# =============================================================================


def error_middleware(func: Callable) -> Callable:
    """
    Automatic error handling middleware.

    Reduces code from:
        try:
            result = process(state)
        except Exception as e:
            state["retry_count"] += 1
            state["status"] = "error"

    To:
        @error_middleware
        def process(state):
            return do_work()
    """

    @wraps(func)
    def wrapper(state: EfficientState) -> Dict[str, Any]:
        try:
            result = func(state)
            result["status"] = "success"
            return result
        except LogicError as e:
            # Logic errors are business rule violations
            return {
                "retry_count": state["retry_count"] + 1,
                "status": "logic_error",
                "error_message": str(e),
                "logic_errors": state.get("logic_errors", []) + [str(e)],
                "business_rule_violations": state.get("business_rule_violations", [])
                + [{"rule": e.rule, "message": str(e), "context": e.context}],
            }
        except Exception as e:
            return {
                "retry_count": state["retry_count"] + 1,
                "status": "error",
                "error_message": str(e),
            }

    return wrapper


def retry_middleware(max_retries: int = 3):
    """
    Retry middleware with exponential backoff.

    Usage:
        @retry_middleware(max_retries=3)
        def flaky_operation(state):
            return call_external_api()
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(state: EfficientState) -> Dict[str, Any]:
            retries = 0
            last_error = None

            while retries < max_retries:
                try:
                    result = func(state)
                    result["status"] = "success"
                    return result
                except Exception as e:
                    last_error = e
                    retries += 1
                    time.sleep(2**retries)  # Exponential backoff

            return {
                "retry_count": state["retry_count"] + retries,
                "status": "error",
                "error_message": f"Failed after {retries} retries: {last_error}",
            }

        return wrapper

    return decorator


def logic_error_middleware(
    rules: List[BusinessRule] = None,
    use_global_registry: bool = True,
):
    """
    Middleware that checks for logic errors (business rule violations).

    This catches errors that don't raise exceptions but violate
    business rules (e.g., code that runs but produces invalid output).

    Usage:
        @logic_error_middleware(rules=[my_rule])
        def generate_code(state):
            return {"output": generated_code}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            # Execute the function
            try:
                result = func(state)
            except Exception as e:
                return {
                    "retry_count": state.get("retry_count", 0) + 1,
                    "status": "error",
                    "error_message": str(e),
                }

            # Check business rules
            output = result.get("output") if isinstance(result, dict) else result
            violations = []

            # Check provided rules
            if rules:
                for rule in rules:
                    try:
                        passed, message = rule.check_fn(output, state)
                        if not passed:
                            violations.append(
                                {
                                    "rule": rule.name,
                                    "message": message,
                                    "severity": rule.severity,
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Rule check failed: {e}")

            # Check global registry
            if use_global_registry:
                violations.extend(_rule_registry.check_all(output, state))

            # If violations found, mark as logic error
            if violations:
                error_violations = [v for v in violations if v["severity"] == "error"]
                if error_violations:
                    if isinstance(result, dict):
                        result["status"] = "logic_error"
                        result["logic_errors"] = [
                            v["message"] for v in error_violations
                        ]
                        result["business_rule_violations"] = violations
                    else:
                        result = {
                            "output": result,
                            "status": "logic_error",
                            "logic_errors": [v["message"] for v in error_violations],
                            "business_rule_violations": violations,
                        }
                else:
                    # Only warnings
                    if isinstance(result, dict):
                        result["warnings"] = [v["message"] for v in violations]
                    else:
                        result = {
                            "output": result,
                            "warnings": [v["message"] for v in violations],
                        }

            # Success if no errors
            if isinstance(result, dict) and "status" not in result:
                result["status"] = "success"

            return result

        return wrapper

    return decorator


def combined_middleware(
    max_retries: int = 3,
    check_logic_errors: bool = True,
    rules: List[BusinessRule] = None,
):
    """
    Combined middleware with error handling, retry, and logic error checking.

    Usage:
        @combined_middleware(max_retries=3, check_logic_errors=True)
        def my_task(state):
            return {"output": result}
    """

    def decorator(func: Callable) -> Callable:
        # Apply middlewares in order
        wrapped = func

        if check_logic_errors:
            wrapped = logic_error_middleware(rules=rules)(wrapped)

        wrapped = retry_middleware(max_retries=max_retries)(wrapped)
        wrapped = error_middleware(wrapped)

        return wrapped

    return decorator


# =============================================================================
# Common Business Rules
# =============================================================================


@register_business_rule(
    "output_not_empty", "Output must not be empty or None", severity="error"
)
def check_output_not_empty(output: Any, context: Dict) -> Tuple[bool, str]:
    """Check that output is not empty."""
    if output is None:
        return False, "Output is None"
    if isinstance(output, str) and not output.strip():
        return False, "Output is empty string"
    if isinstance(output, (list, dict)) and len(output) == 0:
        return False, "Output is empty collection"
    return True, ""


@register_business_rule(
    "no_placeholder_code",
    "Generated code must not contain TODO or placeholder markers",
    severity="warning",
)
def check_no_placeholders(output: Any, context: Dict) -> Tuple[bool, str]:
    """Check for placeholder markers in code output."""
    if not isinstance(output, str):
        return True, ""

    placeholders = ["TODO", "FIXME", "XXX", "PLACEHOLDER", "NOT_IMPLEMENTED"]
    found = [p for p in placeholders if p in output.upper()]

    if found:
        return False, f"Output contains placeholders: {', '.join(found)}"
    return True, ""


@register_business_rule(
    "valid_json_if_expected",
    "Output must be valid JSON if JSON is expected",
    severity="error",
)
def check_valid_json(output: Any, context: Dict) -> Tuple[bool, str]:
    """Check JSON validity if context indicates JSON is expected."""
    if not context.get("expect_json", False):
        return True, ""

    if isinstance(output, (dict, list)):
        return True, ""

    if isinstance(output, str):
        import json

        try:
            json.loads(output)
            return True, ""
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

    return False, f"Expected JSON but got {type(output).__name__}"


__all__ = [
    "error_middleware",
    "retry_middleware",
    "logic_error_middleware",
    "combined_middleware",
    "LogicError",
    "BusinessRule",
    "BusinessRuleRegistry",
    "register_business_rule",
]
