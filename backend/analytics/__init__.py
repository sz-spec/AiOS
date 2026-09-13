"""
Analytics Module

Comprehensive analytics system for tracking user behavior,
AI usage, and business metrics.
"""

from analytics.analytics_service import (
    AnalyticsService,
    AnalyticsEvent,
    EventType,
    EventCategory,
    MetricValue,
    AggregatedMetric,
    UserMetrics,
    DashboardMetrics,
    get_analytics_service,
)

__all__ = [
    "AnalyticsService",
    "AnalyticsEvent",
    "EventType",
    "EventCategory",
    "MetricValue",
    "AggregatedMetric",
    "UserMetrics",
    "DashboardMetrics",
    "get_analytics_service",
]
