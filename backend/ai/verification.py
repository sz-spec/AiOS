"""
Output Verification Module
===========================

Validates AI-generated code, bridge responses, and filters PII from text.

Provides:
- verify_code_output(): Syntax check, size guard, injection scan
- verify_bridge_response(): Validate bridge response format
- filter_pii(): Regex redaction of sensitive data
"""

import ast
import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("vos3.verification")


@dataclass
class VerificationResult:
    """Result of a verification check."""

    valid: bool
    warnings: list[str] = field(default_factory=list)
    confidence: float = 1.0  # 0.0 - 1.0


# ------------------------------------------------------------------
# Code Verification (Phase I4)
# ------------------------------------------------------------------

# Dangerous patterns that suggest injection attempts
_INJECTION_PATTERNS = [
    re.compile(r"__import__\s*\("),  # dynamic import
    re.compile(r"eval\s*\("),  # eval()
    re.compile(r"exec\s*\("),  # exec()
    re.compile(r"os\.system\s*\("),  # os.system()
    re.compile(r"subprocess\.\w+\s*\("),  # subprocess.*()
    re.compile(r"shutil\.rmtree\s*\("),  # shutil.rmtree()
    re.compile(r"open\s*\(.+['\"]w['\"]"),  # file write
    re.compile(r"rm\s+-rf\s+/"),  # shell rm -rf
]

MAX_CODE_SIZE = 50 * 1024  # 50KB


def verify_code_output(code: str, language: str = "python") -> VerificationResult:
    """Verify generated code for syntax, size, and injection.

    Args:
        code: The generated code string.
        language: Programming language ('python', 'javascript', 'c', etc.)

    Returns:
        VerificationResult with valid flag, warnings, and confidence.
    """
    warnings = []
    valid = True
    confidence = 1.0

    # Size guard
    if len(code) > MAX_CODE_SIZE:
        warnings.append(
            f"Code exceeds {MAX_CODE_SIZE // 1024}KB limit ({len(code)} bytes)"
        )
        valid = False
        confidence = 0.0
        return VerificationResult(valid=valid, warnings=warnings, confidence=confidence)

    if not code.strip():
        warnings.append("Empty code output")
        return VerificationResult(valid=False, warnings=warnings, confidence=0.0)

    # Syntax check
    if language == "python":
        try:
            ast.parse(code)
        except SyntaxError as e:
            warnings.append(f"Python syntax error: {e.msg} (line {e.lineno})")
            valid = False
            confidence = 0.3
    elif language in ("javascript", "typescript", "java", "c", "cpp"):
        # Brace matching for C-like languages
        open_braces = code.count("{")
        close_braces = code.count("}")
        if open_braces != close_braces:
            warnings.append(
                f"Unmatched braces: {open_braces} open, {close_braces} close"
            )
            valid = False
            confidence = 0.5

        open_parens = code.count("(")
        close_parens = code.count(")")
        if open_parens != close_parens:
            warnings.append(
                f"Unmatched parentheses: {open_parens} open, {close_parens} close"
            )
            confidence = min(confidence, 0.7)

    # Injection scan
    injection_found = []
    for pattern in _INJECTION_PATTERNS:
        matches = pattern.findall(code)
        if matches:
            injection_found.append(pattern.pattern)

    if injection_found:
        warnings.append(
            f"Potential injection patterns found: {', '.join(injection_found)}"
        )
        confidence = min(confidence, 0.4)
        # Don't set valid=False for injection warnings, just reduce confidence

    return VerificationResult(valid=valid, warnings=warnings, confidence=confidence)


# ------------------------------------------------------------------
# Bridge Response Verification
# ------------------------------------------------------------------


def verify_bridge_response(response) -> VerificationResult:
    """Validate a bridge response object.

    Checks that the response has the expected BridgeResponse structure
    and that data fields are reasonable.

    Args:
        response: A BridgeResponse object or dict-like.

    Returns:
        VerificationResult.
    """
    warnings = []

    if response is None:
        return VerificationResult(
            valid=False, warnings=["Response is None"], confidence=0.0
        )

    # Check required attributes
    if not hasattr(response, "success"):
        warnings.append("Response missing 'success' attribute")
        return VerificationResult(valid=False, warnings=warnings, confidence=0.0)

    if not hasattr(response, "data"):
        warnings.append("Response missing 'data' attribute")
        return VerificationResult(valid=False, warnings=warnings, confidence=0.5)

    # Check data size
    if hasattr(response, "data") and response.data:
        if len(response.data) > 1_000_000:
            warnings.append(
                f"Response data unusually large: {len(response.data)} bytes"
            )

    # Check error fields
    if not response.success:
        if hasattr(response, "error_msg") and response.error_msg:
            if len(response.error_msg) > 1000:
                warnings.append("Error message unusually long")

    return VerificationResult(
        valid=True, warnings=warnings, confidence=1.0 if not warnings else 0.8
    )


# ------------------------------------------------------------------
# PII Filtering (Phase I9)
# ------------------------------------------------------------------

# PII patterns for redaction
_PII_PATTERNS = [
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "[EMAIL_REDACTED]",
    ),
    # Phone numbers (US format)
    (
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[PHONE_REDACTED]",
    ),
    # Credit card numbers (basic 16-digit pattern)
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[CC_REDACTED]"),
    # API keys (common patterns: sk-, pk_, Bearer, token=)
    (
        re.compile(
            r"\b(?:sk-|pk_|api[_-]?key[_=:\s]*)[A-Za-z0-9_-]{16,}\b", re.IGNORECASE
        ),
        "[API_KEY_REDACTED]",
    ),
    # Bearer tokens
    (
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
        "Bearer [TOKEN_REDACTED]",
    ),
    # AWS-style keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[AWS_KEY_REDACTED]"),
    # SSN (US format)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
]


def filter_pii(text: str) -> str:
    """Redact PII (emails, phones, CC numbers, API keys) from text.

    Args:
        text: Input text that may contain sensitive data.

    Returns:
        Text with PII replaced by redaction markers.
    """
    if not text:
        return text

    result = text
    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, result)

    return result
