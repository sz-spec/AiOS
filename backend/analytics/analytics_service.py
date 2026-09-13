"""
Analytics Service

Comprehensive analytics system for tracking:
- User events (page views, actions, conversions)
- Project metrics (builds, deployments, errors)
- AI usage (prompts, tokens, generations)
- Performance metrics (latency, response times)
- Business metrics (signups, subscriptions, churn)

Storage: In-memory with time-series aggregation
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
from collections import defaultdict

# ============================================
# Event Types
# ============================================


class EventCategory(str, Enum):
    """Event categories."""

    PAGE_VIEW = "page_view"
    USER_ACTION = "user_action"
    AI_GENERATION = "ai_generation"
    PROJECT = "project"
    DEPLOYMENT = "deployment"
    ERROR = "error"
    CONVERSION = "conversion"
    SUBSCRIPTION = "subscription"
    PERFORMANCE = "performance"


class EventType(str, Enum):
    """Specific event types."""

    # Page views
    PAGE_VIEW = "page_view"

    # User actions
    SIGN_UP = "sign_up"
    SIGN_IN = "sign_in"
    SIGN_OUT = "sign_out"
    PROFILE_UPDATE = "profile_update"

    # Project events
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_DELETE = "project_delete"
    PROJECT_CLONE = "project_clone"

    # AI events
    AI_PROMPT = "ai_prompt"
    AI_GENERATION_START = "ai_generation_start"
    AI_GENERATION_COMPLETE = "ai_generation_complete"
    AI_GENERATION_ERROR = "ai_generation_error"

    # File events
    FILE_CREATE = "file_create"
    FILE_UPDATE = "file_update"
    FILE_DELETE = "file_delete"

    # Deployment events
    DEPLOY_START = "deploy_start"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILURE = "deploy_failure"

    # Subscription events
    SUBSCRIPTION_START = "subscription_start"
    SUBSCRIPTION_UPGRADE = "subscription_upgrade"
    SUBSCRIPTION_DOWNGRADE = "subscription_downgrade"
    SUBSCRIPTION_CANCEL = "subscription_cancel"
    SUBSCRIPTION_RENEW = "subscription_renew"

    # Errors
    ERROR_CLIENT = "error_client"
    ERROR_SERVER = "error_server"
    ERROR_AI = "error_ai"


# ============================================
# Data Models
# ============================================


@dataclass
class AnalyticsEvent:
    """Single analytics event."""

    id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")

    # Event info
    event_type: str = ""
    category: str = ""

    # Context
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    organization_id: Optional[str] = None

    # Data
    properties: dict = field(default_factory=dict)

    # Metadata
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    referrer: Optional[str] = None
    page_url: Optional[str] = None

    # Device info
    device_type: Optional[str] = None  # desktop, mobile, tablet
    browser: Optional[str] = None
    os: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MetricValue:
    """A single metric value."""

    name: str
    value: float
    unit: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: dict = field(default_factory=dict)


@dataclass
class AggregatedMetric:
    """Aggregated metric over time period."""

    name: str
    period: str  # hour, day, week, month
    start_time: datetime
    end_time: datetime

    count: int = 0
    sum: float = 0.0
    avg: float = 0.0
    min: float = 0.0
    max: float = 0.0

    # Percentiles
    p50: float = 0.0
    p90: float = 0.0
    p99: float = 0.0

    dimensions: dict = field(default_factory=dict)


@dataclass
class UserMetrics:
    """User-level metrics."""

    user_id: str

    # Engagement
    total_sessions: int = 0
    total_page_views: int = 0
    total_actions: int = 0
    avg_session_duration: float = 0.0  # seconds

    # AI Usage
    total_prompts: int = 0
    total_tokens_used: int = 0
    total_generations: int = 0

    # Projects
    total_projects: int = 0
    total_deployments: int = 0

    # Time
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    # Subscription
    subscription_tier: str = "free"
    lifetime_value: float = 0.0


@dataclass
class DashboardMetrics:
    """Metrics for analytics dashboard."""

    # Overview
    total_users: int = 0
    active_users_today: int = 0
    active_users_week: int = 0
    active_users_month: int = 0

    new_users_today: int = 0
    new_users_week: int = 0
    new_users_month: int = 0

    # Projects
    total_projects: int = 0
    projects_created_today: int = 0
    projects_created_week: int = 0

    # AI Usage
    total_ai_generations: int = 0
    ai_generations_today: int = 0
    total_tokens_used: int = 0
    tokens_used_today: int = 0

    # Deployments
    total_deployments: int = 0
    deployments_today: int = 0
    deployment_success_rate: float = 0.0

    # Revenue (for business dashboards)
    mrr: float = 0.0
    arr: float = 0.0
    paying_customers: int = 0
    churn_rate: float = 0.0

    # Errors
    errors_today: int = 0
    error_rate: float = 0.0

    # Performance
    avg_response_time: float = 0.0
    p99_response_time: float = 0.0


# ============================================
# Analytics Service
# ============================================


class AnalyticsService:
    """Analytics tracking and aggregation service."""

    def __init__(self):
        # In-memory storage (replace with persistent backend in production)
        self._events: list[AnalyticsEvent] = []
        self._metrics: list[MetricValue] = []
        self._user_metrics: dict[str, UserMetrics] = {}

        # Real-time counters
        self._counters: dict[str, int] = defaultdict(int)

    # ==========================================
    # Event Tracking
    # ==========================================

    def track_event(
        self,
        event_type: str,
        user_id: Optional[str] = None,
        properties: Optional[dict] = None,
        **kwargs,
    ) -> AnalyticsEvent:
        """Track an analytics event."""
        # Determine category
        category = self._get_category(event_type)

        event = AnalyticsEvent(
            event_type=event_type,
            category=category,
            user_id=user_id,
            properties=properties or {},
            **kwargs,
        )

        self._events.append(event)

        # Update counters
        self._counters[f"events:{event_type}"] += 1
        self._counters["events:total"] += 1

        if user_id:
            self._counters[f"users:{user_id}:events"] += 1
            self._update_user_metrics(user_id, event)

        return event

    def track_page_view(
        self,
        page_url: str,
        user_id: Optional[str] = None,
        referrer: Optional[str] = None,
        **kwargs,
    ) -> AnalyticsEvent:
        """Track a page view."""
        return self.track_event(
            event_type=EventType.PAGE_VIEW,
            user_id=user_id,
            page_url=page_url,
            referrer=referrer,
            properties={
                "page_url": page_url,
                "referrer": referrer,
            },
            **kwargs,
        )

    def track_ai_generation(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "",
        duration_ms: int = 0,
        success: bool = True,
        **kwargs,
    ) -> AnalyticsEvent:
        """Track an AI generation event."""
        event_type = (
            EventType.AI_GENERATION_COMPLETE
            if success
            else EventType.AI_GENERATION_ERROR
        )

        return self.track_event(
            event_type=event_type,
            user_id=user_id,
            project_id=project_id,
            properties={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "model": model,
                "duration_ms": duration_ms,
                "success": success,
            },
            **kwargs,
        )

    def track_error(
        self,
        error_type: str,
        error_message: str,
        user_id: Optional[str] = None,
        stack_trace: Optional[str] = None,
        **kwargs,
    ) -> AnalyticsEvent:
        """Track an error event."""
        return self.track_event(
            event_type=EventType.ERROR_SERVER,
            user_id=user_id,
            properties={
                "error_type": error_type,
                "error_message": error_message,
                "stack_trace": stack_trace,
            },
            **kwargs,
        )

    # ==========================================
    # Metrics Recording
    # ==========================================

    def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        tags: Optional[dict] = None,
    ) -> MetricValue:
        """Record a metric value."""
        metric = MetricValue(
            name=name,
            value=value,
            unit=unit,
            tags=tags or {},
        )

        self._metrics.append(metric)
        return metric

    def record_response_time(
        self,
        endpoint: str,
        duration_ms: float,
        status_code: int,
    ):
        """Record API response time."""
        self.record_metric(
            name="response_time",
            value=duration_ms,
            unit="ms",
            tags={
                "endpoint": endpoint,
                "status_code": str(status_code),
            },
        )

    # ==========================================
    # User Metrics
    # ==========================================

    def _update_user_metrics(self, user_id: str, event: AnalyticsEvent):
        """Update user-level metrics."""
        if user_id not in self._user_metrics:
            self._user_metrics[user_id] = UserMetrics(
                user_id=user_id,
                first_seen=event.timestamp,
            )

        metrics = self._user_metrics[user_id]
        metrics.last_seen = event.timestamp

        # Update based on event type
        if event.event_type == EventType.PAGE_VIEW:
            metrics.total_page_views += 1
        elif event.event_type in [
            EventType.AI_GENERATION_COMPLETE,
            EventType.AI_GENERATION_ERROR,
        ]:
            metrics.total_prompts += 1
            metrics.total_generations += 1
            tokens = event.properties.get("total_tokens", 0)
            metrics.total_tokens_used += tokens
        elif event.event_type == EventType.PROJECT_CREATE:
            metrics.total_projects += 1
        elif event.event_type == EventType.DEPLOY_SUCCESS:
            metrics.total_deployments += 1

    def get_user_metrics(self, user_id: str) -> Optional[UserMetrics]:
        """Get metrics for a specific user."""
        return self._user_metrics.get(user_id)

    # ==========================================
    # Dashboard Queries
    # ==========================================

    def get_dashboard_metrics(
        self,
        organization_id: Optional[str] = None,
    ) -> DashboardMetrics:
        """Get aggregated metrics for dashboard."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)
        month_start = today_start - timedelta(days=30)

        metrics = DashboardMetrics()

        # Filter events
        events = self._events
        if organization_id:
            events = [e for e in events if e.organization_id == organization_id]

        # Calculate user metrics
        all_users = set(e.user_id for e in events if e.user_id)
        today_users = set(
            e.user_id for e in events if e.user_id and e.timestamp >= today_start
        )
        week_users = set(
            e.user_id for e in events if e.user_id and e.timestamp >= week_start
        )
        month_users = set(
            e.user_id for e in events if e.user_id and e.timestamp >= month_start
        )

        metrics.total_users = len(all_users)
        metrics.active_users_today = len(today_users)
        metrics.active_users_week = len(week_users)
        metrics.active_users_month = len(month_users)

        # New users (first sign_up event)
        signups = [e for e in events if e.event_type == EventType.SIGN_UP]
        metrics.new_users_today = len(
            [e for e in signups if e.timestamp >= today_start]
        )
        metrics.new_users_week = len([e for e in signups if e.timestamp >= week_start])
        metrics.new_users_month = len(
            [e for e in signups if e.timestamp >= month_start]
        )

        # Project metrics
        project_creates = [
            e for e in events if e.event_type == EventType.PROJECT_CREATE
        ]
        metrics.total_projects = len(project_creates)
        metrics.projects_created_today = len(
            [e for e in project_creates if e.timestamp >= today_start]
        )
        metrics.projects_created_week = len(
            [e for e in project_creates if e.timestamp >= week_start]
        )

        # AI metrics
        ai_events = [
            e
            for e in events
            if e.event_type
            in [
                EventType.AI_GENERATION_COMPLETE,
                EventType.AI_GENERATION_ERROR,
            ]
        ]
        metrics.total_ai_generations = len(ai_events)
        metrics.ai_generations_today = len(
            [e for e in ai_events if e.timestamp >= today_start]
        )

        metrics.total_tokens_used = sum(
            e.properties.get("total_tokens", 0) for e in ai_events
        )
        metrics.tokens_used_today = sum(
            e.properties.get("total_tokens", 0)
            for e in ai_events
            if e.timestamp >= today_start
        )

        # Deployment metrics
        deploy_success = [e for e in events if e.event_type == EventType.DEPLOY_SUCCESS]
        deploy_failure = [e for e in events if e.event_type == EventType.DEPLOY_FAILURE]
        total_deploys = len(deploy_success) + len(deploy_failure)

        metrics.total_deployments = total_deploys
        metrics.deployments_today = len(
            [e for e in deploy_success + deploy_failure if e.timestamp >= today_start]
        )
        metrics.deployment_success_rate = (
            len(deploy_success) / total_deploys * 100 if total_deploys > 0 else 0
        )

        # Error metrics
        errors = [e for e in events if e.category == EventCategory.ERROR]
        metrics.errors_today = len([e for e in errors if e.timestamp >= today_start])
        total_events_today = len([e for e in events if e.timestamp >= today_start])
        metrics.error_rate = (
            metrics.errors_today / total_events_today * 100
            if total_events_today > 0
            else 0
        )

        # Performance metrics
        response_times = [m.value for m in self._metrics if m.name == "response_time"]
        if response_times:
            metrics.avg_response_time = sum(response_times) / len(response_times)
            sorted_times = sorted(response_times)
            p99_index = int(len(sorted_times) * 0.99)
            metrics.p99_response_time = sorted_times[
                min(p99_index, len(sorted_times) - 1)
            ]

        return metrics

    def get_events_over_time(
        self,
        event_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        interval: str = "day",  # hour, day, week
        user_id: Optional[str] = None,
    ) -> list[dict]:
        """Get event counts grouped by time interval."""
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(days=30)

        # Filter events
        events = self._events
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if user_id:
            events = [e for e in events if e.user_id == user_id]

        events = [e for e in events if start_time <= e.timestamp <= end_time]

        # Group by interval
        groups: dict[str, int] = defaultdict(int)

        for event in events:
            if interval == "hour":
                key = event.timestamp.strftime("%Y-%m-%d %H:00")
            elif interval == "day":
                key = event.timestamp.strftime("%Y-%m-%d")
            elif interval == "week":
                # Get Monday of the week
                monday = event.timestamp - timedelta(days=event.timestamp.weekday())
                key = monday.strftime("%Y-%m-%d")
            else:
                key = event.timestamp.strftime("%Y-%m-%d")

            groups[key] += 1

        # Convert to list
        return [{"date": k, "count": v} for k, v in sorted(groups.items())]

    def get_top_pages(
        self,
        limit: int = 10,
        start_time: Optional[datetime] = None,
    ) -> list[dict]:
        """Get top pages by view count."""
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=7)

        page_views = [
            e
            for e in self._events
            if e.event_type == EventType.PAGE_VIEW and e.timestamp >= start_time
        ]

        page_counts: dict[str, int] = defaultdict(int)
        for event in page_views:
            page_url = event.properties.get("page_url", event.page_url or "unknown")
            page_counts[page_url] += 1

        sorted_pages = sorted(
            page_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:limit]

        return [{"page": page, "views": count} for page, count in sorted_pages]

    def get_ai_usage_stats(
        self,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
    ) -> dict:
        """Get AI usage statistics."""
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)

        ai_events = [
            e
            for e in self._events
            if e.event_type
            in [
                EventType.AI_GENERATION_COMPLETE,
                EventType.AI_GENERATION_ERROR,
            ]
            and e.timestamp >= start_time
        ]

        if user_id:
            ai_events = [e for e in ai_events if e.user_id == user_id]

        successful = [
            e for e in ai_events if e.event_type == EventType.AI_GENERATION_COMPLETE
        ]
        failed = [e for e in ai_events if e.event_type == EventType.AI_GENERATION_ERROR]

        total_tokens = sum(e.properties.get("total_tokens", 0) for e in ai_events)
        total_duration = sum(e.properties.get("duration_ms", 0) for e in ai_events)

        # Group by model
        by_model: dict[str, dict] = defaultdict(lambda: {"count": 0, "tokens": 0})
        for event in ai_events:
            model = event.properties.get("model", "unknown")
            by_model[model]["count"] += 1
            by_model[model]["tokens"] += event.properties.get("total_tokens", 0)

        return {
            "total_generations": len(ai_events),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(ai_events) * 100 if ai_events else 0,
            "total_tokens": total_tokens,
            "avg_tokens_per_generation": (
                total_tokens / len(ai_events) if ai_events else 0
            ),
            "total_duration_ms": total_duration,
            "avg_duration_ms": total_duration / len(ai_events) if ai_events else 0,
            "by_model": dict(by_model),
        }

    def get_funnel_data(
        self,
        steps: list[str],
        start_time: Optional[datetime] = None,
    ) -> list[dict]:
        """Get funnel conversion data."""
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)

        events = [e for e in self._events if e.timestamp >= start_time]

        # Track users who completed each step
        user_steps: dict[str, set] = {step: set() for step in steps}

        for event in events:
            if event.user_id and event.event_type in steps:
                user_steps[event.event_type].add(event.user_id)

        # Build funnel
        funnel = []
        prev_count = 0

        for i, step in enumerate(steps):
            count = len(user_steps[step])
            conversion = count / prev_count * 100 if prev_count > 0 else 100

            funnel.append(
                {
                    "step": step,
                    "step_number": i + 1,
                    "users": count,
                    "conversion_rate": round(conversion, 1),
                }
            )

            prev_count = count if count > 0 else prev_count

        return funnel

    # ==========================================
    # Utility Methods
    # ==========================================

    def _get_category(self, event_type: str) -> str:
        """Get category for event type."""
        type_lower = event_type.lower()

        if "page" in type_lower:
            return EventCategory.PAGE_VIEW
        elif "ai" in type_lower or "generation" in type_lower:
            return EventCategory.AI_GENERATION
        elif "project" in type_lower:
            return EventCategory.PROJECT
        elif "deploy" in type_lower:
            return EventCategory.DEPLOYMENT
        elif "error" in type_lower:
            return EventCategory.ERROR
        elif "subscription" in type_lower:
            return EventCategory.SUBSCRIPTION
        elif "conversion" in type_lower:
            return EventCategory.CONVERSION
        else:
            return EventCategory.USER_ACTION


# ============================================
# Singleton
# ============================================

_analytics_service: Optional[AnalyticsService] = None


def get_analytics_service() -> AnalyticsService:
    """Get analytics service singleton."""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService()
    return _analytics_service


__all__ = [
    "AnalyticsService",
    "AnalyticsEvent",
    "EventType",
    "EventCategory",
    "MetricValue",
    "UserMetrics",
    "DashboardMetrics",
    "get_analytics_service",
]
