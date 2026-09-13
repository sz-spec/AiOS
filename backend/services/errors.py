"""
Error Handling and Logging
==========================
Centralized error handling, custom exceptions, and logging configuration.
"""

import logging
import sys
import traceback
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable
from functools import wraps
from pathlib import Path
import json

# =============================================================================
# Custom Exceptions
# =============================================================================


class AIBuilderError(Exception):
    """Base exception for AI Builder system."""

    def __init__(
        self, message: str, code: str = "UNKNOWN", details: Optional[Dict] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class ModelError(AIBuilderError):
    """Error related to AI model operations."""

    def __init__(
        self, message: str, model_name: str = "", details: Optional[Dict] = None
    ):
        super().__init__(message, "MODEL_ERROR", details)
        self.model_name = model_name


class CodeGenerationError(AIBuilderError):
    """Error during code generation."""

    def __init__(
        self, message: str, generated_code: str = "", details: Optional[Dict] = None
    ):
        super().__init__(message, "CODE_GEN_ERROR", details)
        self.generated_code = generated_code


class ValidationError(AIBuilderError):
    """Error during code/output validation."""

    def __init__(self, message: str, validation_type: str = "", errors: list = None):
        super().__init__(message, "VALIDATION_ERROR", {"errors": errors or []})
        self.validation_type = validation_type
        self.errors = errors or []


class RateLimitError(AIBuilderError):
    """Rate limit exceeded."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message, "RATE_LIMIT", {"retry_after": retry_after})
        self.retry_after = retry_after


class GitError(AIBuilderError):
    """Git operation error."""

    def __init__(
        self, message: str, operation: str = "", details: Optional[Dict] = None
    ):
        super().__init__(message, "GIT_ERROR", details)
        self.operation = operation


class CacheError(AIBuilderError):
    """Cache operation error."""

    def __init__(self, message: str, operation: str = ""):
        super().__init__(message, "CACHE_ERROR", {"operation": operation})


class VCreatorError(AIBuilderError):
    """V-Creator operation error."""

    def __init__(
        self, message: str, operation: str = "", details: Optional[Dict] = None
    ):
        super().__init__(message, "VCREATOR_ERROR", details)
        self.operation = operation


# =============================================================================
# Logging Configuration
# =============================================================================


class LogLevel(Enum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_data["extra"] = record.extra_data

        return json.dumps(log_data)


class ColoredFormatter(logging.Formatter):
    """Colored console formatter."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[41m",  # Red background
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]

        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


def setup_logging(
    log_dir: Path = Path("./logs"),
    level: LogLevel = LogLevel.INFO,
    structured: bool = False,
) -> logging.Logger:
    """Configure logging for the application."""

    log_dir.mkdir(parents=True, exist_ok=True)

    # Create logger
    logger = logging.getLogger("ai_builder")
    logger.setLevel(level.value)
    logger.handlers = []  # Clear existing handlers

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level.value)

    if structured:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(
            ColoredFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )

    logger.addHandler(console_handler)

    # File handler (always structured)
    log_file = log_dir / f"ai_builder_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)  # Always log everything to file
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)

    return logger


# =============================================================================
# Decorators
# =============================================================================


def log_execution(logger: logging.Logger = None):
    """Decorator to log function execution."""

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _logger = logger or logging.getLogger("ai_builder")
            func_name = func.__name__

            _logger.debug(
                f"Executing {func_name}",
                extra={
                    "extra_data": {
                        "args_count": len(args),
                        "kwargs_keys": list(kwargs.keys()),
                    }
                },
            )

            try:
                result = func(*args, **kwargs)
                _logger.debug(f"Completed {func_name}")
                return result
            except Exception as e:
                _logger.error(f"Error in {func_name}: {e}", exc_info=True)
                raise

        return wrapper

    return decorator


def retry_on_error(
    max_attempts: int = 3,
    delay: float = 1.0,
    exponential_backoff: bool = True,
    exceptions: tuple = (Exception,),
):
    """Decorator to retry function on specific exceptions."""

    import time

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger("ai_builder")
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    wait_time = delay * (2**attempt if exponential_backoff else 1)

                    logger.warning(
                        f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {wait_time}s..."
                    )

                    if attempt < max_attempts - 1:
                        time.sleep(wait_time)

            raise last_exception

        return wrapper

    return decorator


def handle_errors(default_return=None, reraise: bool = False):
    """Decorator to handle errors gracefully."""

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger("ai_builder")

            try:
                return func(*args, **kwargs)
            except AIBuilderError as e:
                logger.error(f"AIBuilder error in {func.__name__}: {e.to_dict()}")
                if reraise:
                    raise
                return default_return
            except Exception as e:
                logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
                if reraise:
                    raise
                return default_return

        return wrapper

    return decorator


# =============================================================================
# Error Recovery
# =============================================================================


class ErrorRecovery:
    """Handles error recovery strategies."""

    def __init__(self, logger: logging.Logger = None):
        self.logger = logger or logging.getLogger("ai_builder")
        self.recovery_strategies: Dict[str, Callable] = {}

    def register_strategy(self, error_code: str, strategy: Callable):
        """Register a recovery strategy for an error code."""
        self.recovery_strategies[error_code] = strategy

    def recover(self, error: AIBuilderError) -> Any:
        """Attempt to recover from an error."""
        strategy = self.recovery_strategies.get(error.code)

        if strategy:
            self.logger.info(f"Attempting recovery for {error.code}")
            try:
                return strategy(error)
            except Exception as e:
                self.logger.error(f"Recovery failed: {e}")
                raise
        else:
            self.logger.warning(f"No recovery strategy for {error.code}")
            raise error


# Global logger instance
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Get or create the global logger."""
    global _logger
    if _logger is None:
        _logger = setup_logging()
    return _logger
