"""
Error Monitoring Service
=========================
Error tracking with Sentry + Azure Log Analytics integration.
Based on r/SaaS, Stack Overflow monitoring best practices (Dec 2025).

Features:
- Automatic error capture (Sentry)
- Performance monitoring
- User context
- Custom tags/breadcrumbs
- Release tracking
- Azure Log Analytics integration for KQL queries
- Email alerts on critical errors
"""

import os
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timezone
from functools import wraps
from contextlib import contextmanager
import logging

# Sentry SDK
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False


# =============================================================================
# Configuration
# =============================================================================

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
RELEASE = os.getenv("RELEASE", os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"))
APP_NAME = os.getenv("APP_NAME", "ai-app-builder")

# Sample rates
TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_RATE", "0.1"))  # 10%
PROFILES_SAMPLE_RATE = float(os.getenv("SENTRY_PROFILES_RATE", "0.1"))  # 10%

logger = logging.getLogger(__name__)


# =============================================================================
# Sentry Initialization
# =============================================================================


def init_sentry(
    dsn: str = SENTRY_DSN,
    environment: str = ENVIRONMENT,
    release: str = RELEASE,
    traces_sample_rate: float = TRACES_SAMPLE_RATE,
    profiles_sample_rate: float = PROFILES_SAMPLE_RATE,
    debug: bool = False,
) -> bool:
    """
    Initialize Sentry error monitoring.

    Args:
        dsn: Sentry DSN
        environment: Environment name (development, staging, production)
        release: Release version/commit
        traces_sample_rate: Performance monitoring sample rate (0.0-1.0)
        profiles_sample_rate: Profiling sample rate (0.0-1.0)
        debug: Enable debug mode

    Returns:
        True if initialized successfully
    """
    if not SENTRY_AVAILABLE:
        logger.warning("Sentry SDK not installed. Run: pip install sentry-sdk")
        return False

    if not dsn:
        logger.warning("SENTRY_DSN not configured. Error monitoring disabled.")
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=f"{APP_NAME}@{release}",
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            debug=debug,
            # Integrations
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                HttpxIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            # Data scrubbing
            send_default_pii=False,
            # Before send hook for filtering
            before_send=_before_send,
            before_send_transaction=_before_send_transaction,
        )

        logger.info(f"Sentry initialized: environment={environment}, release={release}")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")
        return False


def _before_send(event: Dict, hint: Dict) -> Optional[Dict]:
    """Filter events before sending to Sentry."""
    # Skip certain errors
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]

        # Skip expected errors
        if exc_type.__name__ in ("HTTPException", "ValidationError"):
            return None

        # Skip 404s
        if hasattr(exc_value, "status_code") and exc_value.status_code == 404:
            return None

    return event


def _before_send_transaction(event: Dict, hint: Dict) -> Optional[Dict]:
    """Filter transactions before sending."""
    # Skip health check endpoints
    transaction_name = event.get("transaction", "")
    if any(skip in transaction_name for skip in ["/health", "/metrics", "/favicon"]):
        return None

    return event


# =============================================================================
# Error Capture Functions
# =============================================================================


def capture_exception(
    error: Exception,
    user_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    tags: Optional[Dict[str, str]] = None,
    level: str = "error",
) -> Optional[str]:
    """
    Capture an exception and send to Sentry.

    Args:
        error: The exception to capture
        user_id: User identifier
        extra: Additional context data
        tags: Tags for filtering
        level: Error level (error, warning, info)

    Returns:
        Sentry event ID or None
    """
    if not SENTRY_AVAILABLE or not SENTRY_DSN:
        logger.error(f"Error captured (Sentry disabled): {error}")
        return None

    with sentry_sdk.push_scope() as scope:
        # Set user
        if user_id:
            scope.set_user({"id": user_id})

        # Set extra context
        if extra:
            for key, value in extra.items():
                scope.set_extra(key, value)

        # Set tags
        if tags:
            for key, value in tags.items():
                scope.set_tag(key, value)

        # Set level
        scope.level = level

        # Capture
        event_id = sentry_sdk.capture_exception(error)
        return event_id


def capture_message(
    message: str,
    level: str = "info",
    user_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Capture a message and send to Sentry.

    Args:
        message: The message to capture
        level: Message level (error, warning, info, debug)
        user_id: User identifier
        extra: Additional context
        tags: Tags for filtering

    Returns:
        Sentry event ID or None
    """
    if not SENTRY_AVAILABLE or not SENTRY_DSN:
        logger.log(
            getattr(logging, level.upper(), logging.INFO),
            f"Message captured (Sentry disabled): {message}",
        )
        return None

    with sentry_sdk.push_scope() as scope:
        if user_id:
            scope.set_user({"id": user_id})
        if extra:
            for key, value in extra.items():
                scope.set_extra(key, value)
        if tags:
            for key, value in tags.items():
                scope.set_tag(key, value)

        event_id = sentry_sdk.capture_message(message, level=level)
        return event_id


# =============================================================================
# Context Management
# =============================================================================


def set_user(user_id: str, email: Optional[str] = None, username: Optional[str] = None):
    """Set the current user for error context."""
    if not SENTRY_AVAILABLE or not SENTRY_DSN:
        return

    user_data = {"id": user_id}
    if email:
        user_data["email"] = email
    if username:
        user_data["username"] = username

    sentry_sdk.set_user(user_data)


def set_tag(key: str, value: str):
    """Set a tag for the current scope."""
    if SENTRY_AVAILABLE and SENTRY_DSN:
        sentry_sdk.set_tag(key, value)


def set_context(name: str, data: Dict[str, Any]):
    """Set custom context data."""
    if SENTRY_AVAILABLE and SENTRY_DSN:
        sentry_sdk.set_context(name, data)


def add_breadcrumb(
    message: str,
    category: str = "custom",
    level: str = "info",
    data: Optional[Dict[str, Any]] = None,
):
    """Add a breadcrumb for debugging."""
    if SENTRY_AVAILABLE and SENTRY_DSN:
        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data,
            timestamp=datetime.now(timezone.utc),
        )


# =============================================================================
# Performance Monitoring
# =============================================================================


@contextmanager
def start_transaction(name: str, op: str = "task"):
    """Start a performance transaction."""
    if not SENTRY_AVAILABLE or not SENTRY_DSN:
        yield None
        return

    with sentry_sdk.start_transaction(name=name, op=op) as transaction:
        yield transaction


@contextmanager
def start_span(description: str, op: str = "task"):
    """Start a performance span within a transaction."""
    if not SENTRY_AVAILABLE or not SENTRY_DSN:
        yield None
        return

    with sentry_sdk.start_span(description=description, op=op) as span:
        yield span


# =============================================================================
# Decorators
# =============================================================================


def track_errors(
    operation: str = "function",
    user_id_param: Optional[str] = None,
    capture_args: bool = False,
):
    """
    Decorator to track errors in a function.

    Args:
        operation: Operation name for tagging
        user_id_param: Parameter name containing user ID
        capture_args: Whether to capture function arguments

    Usage:
        @track_errors(operation="generate_code")
        async def generate_code(user_id: str, prompt: str):
            ...
    """

    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                extra = {"operation": operation}
                if capture_args:
                    extra["args"] = str(args)
                    extra["kwargs"] = str(kwargs)

                user_id = None
                if user_id_param and user_id_param in kwargs:
                    user_id = kwargs[user_id_param]

                capture_exception(
                    e, user_id=user_id, extra=extra, tags={"operation": operation}
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                extra = {"operation": operation}
                if capture_args:
                    extra["args"] = str(args)
                    extra["kwargs"] = str(kwargs)

                user_id = None
                if user_id_param and user_id_param in kwargs:
                    user_id = kwargs[user_id_param]

                capture_exception(
                    e, user_id=user_id, extra=extra, tags={"operation": operation}
                )
                raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def track_performance(name: str, op: str = "function"):
    """
    Decorator to track function performance.

    Usage:
        @track_performance("code_generation")
        async def generate_code(prompt: str):
            ...
    """

    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with start_transaction(name=name, op=op):
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with start_transaction(name=name, op=op):
                return func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# =============================================================================
# FastAPI Integration
# =============================================================================


def add_sentry_to_fastapi(app):
    """
    Add Sentry error handling to FastAPI app.

    Usage:
        from fastapi import FastAPI
        from tools.monitoring import add_sentry_to_fastapi, init_sentry

        app = FastAPI()
        init_sentry()
        add_sentry_to_fastapi(app)
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def sentry_context_middleware(request: Request, call_next):
        """Add request context to Sentry."""
        # Set request context
        set_context(
            "request",
            {
                "url": str(request.url),
                "method": request.method,
                "headers": dict(request.headers),
            },
        )

        # Extract user from auth header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # Add breadcrumb for authenticated request
            add_breadcrumb(
                message="Authenticated API request",
                category="http",
                level="info",
                data={"path": request.url.path},
            )

        response = await call_next(request)
        return response

    @app.exception_handler(Exception)
    async def sentry_exception_handler(request: Request, exc: Exception):
        """Catch unhandled exceptions and send to Sentry."""
        # Get user ID from request state if available
        user_id = getattr(request.state, "user_id", None)

        capture_exception(
            exc,
            user_id=user_id,
            extra={
                "url": str(request.url),
                "method": request.method,
            },
            tags={
                "endpoint": request.url.path,
            },
        )

        return JSONResponse(status_code=500, content={"error": "Internal server error"})

    return app


# =============================================================================
# Health Check
# =============================================================================


def sentry_health_check() -> Dict[str, Any]:
    """Check Sentry connectivity."""
    return {
        "sentry": {
            "enabled": SENTRY_AVAILABLE and bool(SENTRY_DSN),
            "environment": ENVIRONMENT,
            "release": RELEASE,
        }
    }


# =============================================================================
# Alert Integration (from Stack Overflow Azure Log Analytics pattern)
# =============================================================================


class AlertIntegration:
    """
    Integrate monitoring with email alerts.
    Based on Stack Overflow Azure Log Analytics email alert pattern.

    Usage:
        from tools.email_service import EmailService, EmailAlertService
        from tools.monitoring import AlertIntegration

        email = EmailService()
        alerts = EmailAlertService(email)
        integration = AlertIntegration(alerts)

        # Register with Sentry
        integration.register_sentry_hooks()
    """

    def __init__(self, alert_service=None, analytics_service=None):
        self.alerts = alert_service
        self.analytics = analytics_service
        self._error_counts: Dict[str, int] = {}
        self._error_threshold = int(os.getenv("ERROR_ALERT_THRESHOLD", "5"))

    def register_sentry_hooks(self):
        """Register hooks with Sentry for automatic alerting."""
        if not SENTRY_AVAILABLE:
            return

        # Add before_send hook
        def before_send_hook(event, hint):
            # Track error
            error_type = (
                event.get("exception", {}).get("values", [{}])[0].get("type", "Unknown")
            )
            self._error_counts[error_type] = self._error_counts.get(error_type, 0) + 1

            # Alert if threshold exceeded
            if self._error_counts[error_type] >= self._error_threshold:
                import asyncio

                if self.alerts:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            _alert_task = asyncio.create_task(
                                self._send_threshold_alert(error_type)
                            )
                            _alert_task.add_done_callback(
                                lambda t: None  # best-effort alert; failures are intentionally silent
                            )
                    except Exception:
                        pass

            return event

        # Note: This would be integrated during sentry_sdk.init()
        # For now, store the hook
        self._before_send_hook = before_send_hook

    async def _send_threshold_alert(self, error_type: str):
        """Send alert when error threshold is exceeded."""
        if not self.alerts:
            return

        count = self._error_counts.get(error_type, 0)
        await self.alerts.alert_on_threshold(
            metric_name=f"Error: {error_type}",
            current_value=count,
            threshold=self._error_threshold,
            unit="occurrences",
        )

        # Reset counter
        self._error_counts[error_type] = 0

    async def track_and_alert(
        self,
        error: Exception,
        user_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        severity: str = "error",
    ):
        """Track error in all systems and send alert if needed."""
        # Capture in Sentry
        event_id = capture_exception(
            error, user_id=user_id, extra=context, level=severity
        )

        # Track in analytics (Azure Log Analytics)
        if self.analytics:
            await self.analytics.track_exception(error, user_id, context)

        # Send email alert for critical errors
        if severity in ("error", "critical", "fatal") and self.alerts:
            await self.alerts.alert_on_error(error, user_id, context)

        return event_id


# =============================================================================
# KQL Query Templates (from Stack Overflow Azure Log Analytics)
# =============================================================================


class KQLQueries:
    """
    Pre-built KQL queries for Azure Log Analytics.
    Based on Stack Overflow recommendations.
    """

    # Error analysis
    ERRORS_BY_TYPE = """
    AIAppBuilder_Exceptions_CL
    | where TimeGenerated > ago(24h)
    | summarize Count=count() by ExceptionType_s
    | order by Count desc
    """

    HIGH_SEVERITY_ERRORS = """
    AIAppBuilder_Exceptions_CL
    | where TimeGenerated > ago(1h)
    | where Severity_s in ("Error", "Critical")
    | project TimeGenerated, ExceptionType_s, ExceptionMessage_s, UserId_s
    | order by TimeGenerated desc
    """

    # AI usage analysis
    AI_USAGE_BY_MODEL = """
    AIAppBuilder_AIUsage_CL
    | where TimeGenerated > ago(7d)
    | summarize 
        TotalTokens=sum(TokensUsed_d),
        AvgLatency=avg(LatencyMs_d),
        RequestCount=count()
      by Model_s
    | order by TotalTokens desc
    """

    AI_USAGE_BY_USER = """
    AIAppBuilder_AIUsage_CL
    | where TimeGenerated > ago(30d)
    | summarize 
        TotalTokens=sum(TokensUsed_d),
        RequestCount=count()
      by UserId_s
    | order by TotalTokens desc
    | take 100
    """

    # Performance analysis
    SLOW_REQUESTS = """
    AIAppBuilder_CL
    | where TimeGenerated > ago(24h)
    | where LatencyMs_d > 5000
    | project TimeGenerated, EventName_s, LatencyMs_d, UserId_s
    | order by LatencyMs_d desc
    """

    # User activity
    DAILY_ACTIVE_USERS = """
    AIAppBuilder_CL
    | where TimeGenerated > ago(30d)
    | summarize DAU=dcount(UserId_s) by bin(TimeGenerated, 1d)
    | order by TimeGenerated asc
    """

    # Cost estimation (tokens * price)
    ESTIMATED_AI_COST = """
    AIAppBuilder_AIUsage_CL
    | where TimeGenerated > ago(30d)
    | extend CostUSD = case(
        Model_s == "gpt-4", TokensUsed_d * 0.00003,
        Model_s == "gpt-3.5-turbo", TokensUsed_d * 0.000002,
        Model_s == "claude-3-opus", TokensUsed_d * 0.000015,
        TokensUsed_d * 0.00001
      )
    | summarize TotalCost=sum(CostUSD) by bin(TimeGenerated, 1d)
    | order by TimeGenerated asc
    """


# =============================================================================
# Export convenience
# =============================================================================

__all__ = [
    "init_sentry",
    "capture_exception",
    "capture_message",
    "set_user",
    "set_tag",
    "set_context",
    "add_breadcrumb",
    "start_transaction",
    "start_span",
    "track_errors",
    "track_performance",
    "add_sentry_to_fastapi",
    "sentry_health_check",
    "AlertIntegration",
    "KQLQueries",
]
