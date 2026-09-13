"""
Analytics Service
==================
Track user behavior with PostHog, Mixpanel, Google Analytics 4, and Azure Log Analytics.
Based on r/SaaS, r/analytics, Stack Overflow, DEV.to best practices (Dec 2025).

Features:
- Event tracking (multi-provider)
- User identification
- Feature flags (PostHog)
- A/B testing
- Funnel analysis
- Azure Log Analytics for deep insights
- Google Analytics 4 for marketing
"""

import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
import httpx
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# PostHog (Product Analytics)
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY", "")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://app.posthog.com")

# Mixpanel (Alternative)
MIXPANEL_TOKEN = os.getenv("MIXPANEL_TOKEN", "")

# Google Analytics 4 (Marketing Analytics) - from Stack Overflow recommendations
GA4_MEASUREMENT_ID = os.getenv("GA4_MEASUREMENT_ID", "")  # G-XXXXXXXXXX
GA4_API_SECRET = os.getenv("GA4_API_SECRET", "")

# Azure Log Analytics (Deep Insights) - from Stack Overflow recommendations
AZURE_LOG_WORKSPACE_ID = os.getenv("AZURE_LOG_WORKSPACE_ID", "")
AZURE_LOG_SHARED_KEY = os.getenv("AZURE_LOG_SHARED_KEY", "")
AZURE_LOG_TYPE = os.getenv("AZURE_LOG_TYPE", "AIAppBuilder")

# Choose providers: comma-separated list e.g., "posthog,ga4,azure"
ANALYTICS_PROVIDERS = os.getenv("ANALYTICS_PROVIDERS", "posthog")


# =============================================================================
# Event Types
# =============================================================================


class EventCategory(str, Enum):
    """Event categories for organization."""

    AUTH = "auth"
    PROJECT = "project"
    GENERATION = "generation"
    BILLING = "billing"
    ENGAGEMENT = "engagement"
    ERROR = "error"


@dataclass
class AnalyticsEvent:
    """Analytics event structure."""

    name: str
    category: EventCategory
    user_id: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.timestamp:
            data["timestamp"] = self.timestamp.isoformat()
        return data


# =============================================================================
# Predefined Events
# =============================================================================


class Events:
    """Predefined analytics events."""

    # Auth events
    SIGNUP = "user_signed_up"
    LOGIN = "user_logged_in"
    LOGOUT = "user_logged_out"
    PASSWORD_RESET = "password_reset_requested"

    # Project events
    PROJECT_CREATED = "project_created"
    PROJECT_OPENED = "project_opened"
    PROJECT_DELETED = "project_deleted"
    PROJECT_EXPORTED = "project_exported"

    # Generation events
    GENERATION_STARTED = "generation_started"
    GENERATION_COMPLETED = "generation_completed"
    GENERATION_FAILED = "generation_failed"
    CODE_EDITED = "code_edited"

    # Billing events
    CHECKOUT_STARTED = "checkout_started"
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_CANCELED = "subscription_canceled"
    TOKENS_PURCHASED = "tokens_purchased"

    # Engagement events
    FEATURE_USED = "feature_used"
    PAGE_VIEWED = "page_viewed"
    BUTTON_CLICKED = "button_clicked"
    SEARCH_PERFORMED = "search_performed"


# =============================================================================
# PostHog Client
# =============================================================================


class PostHogClient:
    """PostHog analytics client."""

    def __init__(self, api_key: str, host: str = "https://app.posthog.com"):
        self.api_key = api_key
        self.host = host
        self.client = httpx.AsyncClient(timeout=10.0)

    async def capture(
        self, event: str, user_id: str, properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Capture an event."""
        if not self.api_key:
            return False

        payload = {
            "api_key": self.api_key,
            "event": event,
            "distinct_id": user_id,
            "properties": {
                **(properties or {}),
                "$lib": "ai-app-builder",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            response = await self.client.post(f"{self.host}/capture/", json=payload)
            return response.status_code == 200
        except Exception:
            return False

    async def identify(self, user_id: str, properties: Dict[str, Any]) -> bool:
        """Identify a user with properties."""
        if not self.api_key:
            return False

        payload = {
            "api_key": self.api_key,
            "event": "$identify",
            "distinct_id": user_id,
            "$set": properties,
        }

        try:
            response = await self.client.post(f"{self.host}/capture/", json=payload)
            return response.status_code == 200
        except Exception:
            return False

    async def get_feature_flag(
        self, flag_key: str, user_id: str, default: bool = False
    ) -> bool:
        """Check if a feature flag is enabled."""
        if not self.api_key:
            return default

        payload = {
            "api_key": self.api_key,
            "distinct_id": user_id,
        }

        try:
            response = await self.client.post(f"{self.host}/decide/?v=3", json=payload)
            data = response.json()
            return data.get("featureFlags", {}).get(flag_key, default)
        except Exception:
            return default

    async def close(self):
        await self.client.aclose()


# =============================================================================
# Mixpanel Client
# =============================================================================


class MixpanelClient:
    """Mixpanel analytics client."""

    def __init__(self, token: str):
        self.token = token
        self.client = httpx.AsyncClient(timeout=10.0)
        self.base_url = "https://api.mixpanel.com"

    async def track(
        self, event: str, user_id: str, properties: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Track an event."""
        if not self.token:
            return False

        payload = {
            "event": event,
            "properties": {
                "token": self.token,
                "distinct_id": user_id,
                "time": int(datetime.now(timezone.utc).timestamp()),
                **(properties or {}),
            },
        }

        try:
            import base64

            data = base64.b64encode(json.dumps([payload]).encode()).decode()
            response = await self.client.get(
                f"{self.base_url}/track", params={"data": data}
            )
            return response.text == "1"
        except Exception:
            return False

    async def people_set(self, user_id: str, properties: Dict[str, Any]) -> bool:
        """Set user profile properties."""
        if not self.token:
            return False

        payload = {
            "$token": self.token,
            "$distinct_id": user_id,
            "$set": properties,
        }

        try:
            import base64

            data = base64.b64encode(json.dumps([payload]).encode()).decode()
            response = await self.client.get(
                f"{self.base_url}/engage", params={"data": data}
            )
            return response.text == "1"
        except Exception:
            return False

    async def close(self):
        await self.client.aclose()


# =============================================================================
# Google Analytics 4 Client (from Stack Overflow recommendations)
# =============================================================================


class GA4Client:
    """
    Google Analytics 4 Measurement Protocol client.
    Based on Stack Overflow recommendations for marketing analytics.
    """

    def __init__(self, measurement_id: str, api_secret: str):
        self.measurement_id = measurement_id
        self.api_secret = api_secret
        self.client = httpx.AsyncClient(timeout=10.0)
        self.base_url = "https://www.google-analytics.com/mp/collect"

    async def track(
        self,
        event_name: str,
        client_id: str,
        params: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Track event in GA4."""
        if not self.measurement_id or not self.api_secret:
            return False

        payload = {
            "client_id": client_id,
            "events": [
                {
                    "name": event_name,
                    "params": {
                        **(params or {}),
                        "engagement_time_msec": "100",
                    },
                }
            ],
        }

        if user_id:
            payload["user_id"] = user_id

        try:
            response = await self.client.post(
                f"{self.base_url}?measurement_id={self.measurement_id}&api_secret={self.api_secret}",
                json=payload,
            )
            # GA4 returns 204 on success
            return response.status_code in (200, 204)
        except Exception as e:
            logger.error(f"GA4 tracking error: {e}")
            return False

    async def track_page_view(
        self,
        client_id: str,
        page_location: str,
        page_title: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Track page view in GA4."""
        return await self.track(
            "page_view",
            client_id,
            {
                "page_location": page_location,
                "page_title": page_title or "",
            },
            user_id,
        )

    async def track_conversion(
        self,
        client_id: str,
        conversion_name: str,
        value: float = 0,
        currency: str = "USD",
        user_id: Optional[str] = None,
    ) -> bool:
        """Track conversion event (e.g., purchase, signup)."""
        return await self.track(
            conversion_name,
            client_id,
            {
                "value": value,
                "currency": currency,
            },
            user_id,
        )

    async def close(self):
        await self.client.aclose()


# =============================================================================
# Azure Log Analytics Client (from Stack Overflow recommendations)
# =============================================================================


class AzureLogAnalyticsClient:
    """
    Azure Log Analytics Data Collector API client.
    Based on Stack Overflow recommendations for deep insights and alerting.

    Features:
    - Send custom logs to Azure
    - Query with KQL
    - Set up alerts for anomalies
    """

    def __init__(
        self, workspace_id: str, shared_key: str, log_type: str = "AIAppBuilder"
    ):
        self.workspace_id = workspace_id
        self.shared_key = shared_key
        self.log_type = log_type
        self.client = httpx.AsyncClient(timeout=30.0)

    def _build_signature(self, date: str, content_length: int) -> str:
        """Build Azure authorization signature."""
        import base64
        import hmac
        import hashlib

        string_to_hash = (
            f"POST\n{content_length}\napplication/json\nx-ms-date:{date}\n/api/logs"
        )
        bytes_to_hash = string_to_hash.encode("utf-8")
        decoded_key = base64.b64decode(self.shared_key)
        encoded_hash = base64.b64encode(
            hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()
        ).decode("utf-8")

        return f"SharedKey {self.workspace_id}:{encoded_hash}"

    async def send_log(
        self, records: List[Dict[str, Any]], log_type: Optional[str] = None
    ) -> bool:
        """
        Send log records to Azure Log Analytics.

        Args:
            records: List of log records (each is a dict)
            log_type: Custom log type (table name in Azure)
        """
        if not self.workspace_id or not self.shared_key:
            return False

        body = json.dumps(records)
        content_length = len(body)

        # RFC 1123 date format
        from email.utils import formatdate

        rfc1123_date = formatdate(timeval=None, localtime=False, usegmt=True)

        signature = self._build_signature(rfc1123_date, content_length)

        headers = {
            "Content-Type": "application/json",
            "Authorization": signature,
            "Log-Type": log_type or self.log_type,
            "x-ms-date": rfc1123_date,
            "time-generated-field": "TimeGenerated",
        }

        url = f"https://{self.workspace_id}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01"

        try:
            response = await self.client.post(url, content=body, headers=headers)
            return response.status_code in (200, 202)
        except Exception as e:
            logger.error(f"Azure Log Analytics error: {e}")
            return False

    async def track_event(
        self,
        event_name: str,
        user_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        severity: str = "Information",
    ) -> bool:
        """Track a single event."""
        record = {
            "TimeGenerated": datetime.now(timezone.utc).isoformat() + "Z",
            "EventName": event_name,
            "UserId": user_id or "anonymous",
            "Severity": severity,
            "Properties": json.dumps(properties or {}),
        }
        return await self.send_log([record])

    async def track_exception(
        self,
        exception: Exception,
        user_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Track an exception for monitoring."""
        import traceback

        record = {
            "TimeGenerated": datetime.now(timezone.utc).isoformat() + "Z",
            "EventName": "Exception",
            "UserId": user_id or "anonymous",
            "Severity": "Error",
            "ExceptionType": type(exception).__name__,
            "ExceptionMessage": str(exception),
            "StackTrace": traceback.format_exc(),
            "Properties": json.dumps(properties or {}),
        }
        return await self.send_log([record], log_type=f"{self.log_type}_Exceptions")

    async def track_ai_usage(
        self,
        user_id: str,
        model: str,
        tokens_used: int,
        latency_ms: int,
        success: bool,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Track AI model usage for cost analysis."""
        record = {
            "TimeGenerated": datetime.now(timezone.utc).isoformat() + "Z",
            "EventName": "AIUsage",
            "UserId": user_id,
            "Model": model,
            "TokensUsed": tokens_used,
            "LatencyMs": latency_ms,
            "Success": success,
            "Properties": json.dumps(properties or {}),
        }
        return await self.send_log([record], log_type=f"{self.log_type}_AIUsage")

    async def close(self):
        await self.client.aclose()


# =============================================================================
# Unified Analytics Service (Enhanced)
# =============================================================================


class AnalyticsService:
    """
    Unified analytics service supporting multiple providers.

    Providers:
    - PostHog: Product analytics, feature flags
    - Mixpanel: Event analytics
    - GA4: Marketing analytics (from Stack Overflow)
    - Azure Log Analytics: Deep insights, KQL queries (from Stack Overflow)

    Usage:
        analytics = AnalyticsService()

        # Track event (sends to all enabled providers)
        await analytics.track(
            Events.PROJECT_CREATED,
            user_id="user_123",
            properties={"framework": "react"}
        )

        # Track AI usage (Azure Log Analytics)
        await analytics.track_ai_usage(
            user_id="user_123",
            model="gpt-4",
            tokens_used=500,
            latency_ms=1200
        )
    """

    def __init__(
        self,
        providers: str = ANALYTICS_PROVIDERS,
        posthog_key: str = POSTHOG_API_KEY,
        posthog_host: str = POSTHOG_HOST,
        mixpanel_token: str = MIXPANEL_TOKEN,
        ga4_measurement_id: str = GA4_MEASUREMENT_ID,
        ga4_api_secret: str = GA4_API_SECRET,
        azure_workspace_id: str = AZURE_LOG_WORKSPACE_ID,
        azure_shared_key: str = AZURE_LOG_SHARED_KEY,
        azure_log_type: str = AZURE_LOG_TYPE,
    ):
        self.providers = [p.strip().lower() for p in providers.split(",")]

        # Initialize enabled providers
        self.posthog: Optional[PostHogClient] = None
        self.mixpanel: Optional[MixpanelClient] = None
        self.ga4: Optional[GA4Client] = None
        self.azure: Optional[AzureLogAnalyticsClient] = None

        if "posthog" in self.providers and posthog_key:
            self.posthog = PostHogClient(posthog_key, posthog_host)
            logger.info("PostHog analytics enabled")

        if "mixpanel" in self.providers and mixpanel_token:
            self.mixpanel = MixpanelClient(mixpanel_token)
            logger.info("Mixpanel analytics enabled")

        if "ga4" in self.providers and ga4_measurement_id and ga4_api_secret:
            self.ga4 = GA4Client(ga4_measurement_id, ga4_api_secret)
            logger.info("Google Analytics 4 enabled")

        if "azure" in self.providers and azure_workspace_id and azure_shared_key:
            self.azure = AzureLogAnalyticsClient(
                azure_workspace_id, azure_shared_key, azure_log_type
            )
            logger.info("Azure Log Analytics enabled")

    async def track(
        self,
        event: str,
        user_id: str,
        properties: Optional[Dict[str, Any]] = None,
        category: Optional[EventCategory] = None,
    ) -> bool:
        """
        Track an event across all enabled providers.

        Args:
            event: Event name
            user_id: User identifier
            properties: Additional event properties
            category: Event category for organization
        """
        props = properties or {}
        if category:
            props["category"] = category.value

        results = []

        # PostHog
        if self.posthog:
            results.append(await self.posthog.capture(event, user_id, props))

        # Mixpanel
        if self.mixpanel:
            results.append(await self.mixpanel.track(event, user_id, props))

        # Google Analytics 4
        if self.ga4:
            results.append(await self.ga4.track(event, user_id, props, user_id))

        # Azure Log Analytics
        if self.azure:
            results.append(await self.azure.track_event(event, user_id, props))

        return any(results) if results else False

    async def identify(self, user_id: str, properties: Dict[str, Any]) -> bool:
        """
        Identify a user with properties.

        Args:
            user_id: User identifier
            properties: User properties (email, plan, company, etc.)
        """
        results = []

        if self.posthog:
            results.append(await self.posthog.identify(user_id, properties))

        if self.mixpanel:
            results.append(await self.mixpanel.people_set(user_id, properties))

        return any(results) if results else False

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    async def track_signup(
        self, user_id: str, method: str = "email", referrer: Optional[str] = None
    ) -> bool:
        """Track user signup."""
        result = await self.track(
            Events.SIGNUP,
            user_id,
            {"method": method, "referrer": referrer},
            EventCategory.AUTH,
        )

        # Track as conversion in GA4
        if self.ga4:
            await self.ga4.track_conversion(user_id, "sign_up", user_id=user_id)

        return result

    async def track_login(self, user_id: str, method: str = "email") -> bool:
        """Track user login."""
        return await self.track(
            Events.LOGIN, user_id, {"method": method}, EventCategory.AUTH
        )

    async def track_project_created(
        self, user_id: str, project_id: str, framework: str = "react"
    ) -> bool:
        """Track project creation."""
        return await self.track(
            Events.PROJECT_CREATED,
            user_id,
            {"project_id": project_id, "framework": framework},
            EventCategory.PROJECT,
        )

    async def track_generation(
        self,
        user_id: str,
        project_id: str,
        status: str,  # "started", "completed", "failed"
        tokens_used: int = 0,
        duration_ms: int = 0,
        model: str = "gpt-4",
    ) -> bool:
        """Track code generation."""
        event = {
            "started": Events.GENERATION_STARTED,
            "completed": Events.GENERATION_COMPLETED,
            "failed": Events.GENERATION_FAILED,
        }.get(status, Events.GENERATION_STARTED)

        result = await self.track(
            event,
            user_id,
            {
                "project_id": project_id,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "model": model,
            },
            EventCategory.GENERATION,
        )

        # Track AI usage in Azure Log Analytics for cost analysis
        if self.azure and status == "completed":
            await self.azure.track_ai_usage(
                user_id=user_id,
                model=model,
                tokens_used=tokens_used,
                latency_ms=duration_ms,
                success=True,
                properties={"project_id": project_id},
            )

        return result

    async def track_subscription(
        self,
        user_id: str,
        action: str,  # "created", "canceled"
        plan: str,
        interval: str = "monthly",
        value: float = 0,
    ) -> bool:
        """Track subscription events."""
        event = (
            Events.SUBSCRIPTION_CREATED
            if action == "created"
            else Events.SUBSCRIPTION_CANCELED
        )

        result = await self.track(
            event, user_id, {"plan": plan, "interval": interval}, EventCategory.BILLING
        )

        # Track as conversion in GA4
        if self.ga4 and action == "created":
            await self.ga4.track_conversion(user_id, "purchase", value, user_id=user_id)

        return result

    async def track_page_view(
        self, user_id: str, page: str, referrer: Optional[str] = None
    ) -> bool:
        """Track page view."""
        result = await self.track(
            Events.PAGE_VIEWED,
            user_id,
            {"page": page, "referrer": referrer},
            EventCategory.ENGAGEMENT,
        )

        # Track in GA4 specifically for marketing
        if self.ga4:
            await self.ga4.track_page_view(user_id, page, user_id=user_id)

        return result

    async def track_feature_usage(
        self, user_id: str, feature: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Track feature usage."""
        return await self.track(
            Events.FEATURE_USED,
            user_id,
            {"feature": feature, **(metadata or {})},
            EventCategory.ENGAGEMENT,
        )

    # -------------------------------------------------------------------------
    # Azure-specific Methods (Deep Analytics)
    # -------------------------------------------------------------------------

    async def track_ai_usage(
        self,
        user_id: str,
        model: str,
        tokens_used: int,
        latency_ms: int,
        success: bool = True,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Track AI model usage for cost analysis (Azure Log Analytics)."""
        if self.azure:
            return await self.azure.track_ai_usage(
                user_id, model, tokens_used, latency_ms, success, properties
            )
        return False

    async def track_exception(
        self,
        exception: Exception,
        user_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Track exception for monitoring (Azure Log Analytics)."""
        if self.azure:
            return await self.azure.track_exception(exception, user_id, properties)
        return False

    # -------------------------------------------------------------------------
    # Feature Flags (PostHog only)
    # -------------------------------------------------------------------------

    async def get_feature_flag(
        self, flag_key: str, user_id: str, default: bool = False
    ) -> bool:
        """Check if a feature flag is enabled (PostHog only)."""
        if self.posthog:
            return await self.posthog.get_feature_flag(flag_key, user_id, default)
        return default

    async def close(self):
        """Close all client connections."""
        if self.posthog:
            await self.posthog.close()
        if self.mixpanel:
            await self.mixpanel.close()
        if self.ga4:
            await self.ga4.close()
        if self.azure:
            await self.azure.close()


# =============================================================================
# Singleton Instance
# =============================================================================

_analytics: Optional[AnalyticsService] = None


def get_analytics() -> AnalyticsService:
    """Get analytics service singleton."""
    global _analytics
    if _analytics is None:
        _analytics = AnalyticsService()
    return _analytics


async def shutdown_analytics():
    """Shutdown analytics service."""
    global _analytics
    if _analytics:
        await _analytics.close()
        _analytics = None


# =============================================================================
# FastAPI Integration
# =============================================================================


def create_analytics_middleware():
    """Create FastAPI middleware for automatic page view tracking."""
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware

    class AnalyticsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)

            # Track API calls
            user_id = request.headers.get("X-User-ID")
            if user_id and request.url.path.startswith("/api/"):
                analytics = get_analytics()
                await analytics.track(
                    "api_call",
                    user_id,
                    {
                        "path": request.url.path,
                        "method": request.method,
                        "status_code": response.status_code,
                    },
                )

            return response

    return AnalyticsMiddleware
