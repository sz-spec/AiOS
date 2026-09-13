"""
Analytics API Routes

Endpoints for:
- Dashboard metrics
- Event tracking
- Time series data
- User analytics
- AI usage stats
- Funnel analysis
"""

import os as _os
import socket as _socket
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Query, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from analytics.analytics_service import (
    AnalyticsService,
    get_analytics_service,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ============================================
# Request/Response Models
# ============================================


class TrackEventRequest(BaseModel):
    """Track event request."""

    event_type: str
    properties: dict = {}
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None


class TrackPageViewRequest(BaseModel):
    """Track page view request."""

    page_url: str
    referrer: Optional[str] = None
    session_id: Optional[str] = None


class TrackAIGenerationRequest(BaseModel):
    """Track AI generation request."""

    project_id: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    duration_ms: int = 0
    success: bool = True


class FunnelRequest(BaseModel):
    """Funnel analysis request."""

    steps: list[str]
    days: int = 30


class DashboardResponse(BaseModel):
    """Dashboard metrics response."""

    # Users
    total_users: int
    active_users_today: int
    active_users_week: int
    active_users_month: int
    new_users_today: int
    new_users_week: int

    # Projects
    total_projects: int
    projects_created_today: int
    projects_created_week: int

    # AI
    total_ai_generations: int
    ai_generations_today: int
    total_tokens_used: int
    tokens_used_today: int

    # Deployments
    total_deployments: int
    deployments_today: int
    deployment_success_rate: float

    # Errors
    errors_today: int
    error_rate: float

    # Performance
    avg_response_time: float
    p99_response_time: float


class TimeSeriesPoint(BaseModel):
    """Time series data point."""

    date: str
    count: int


class TopPage(BaseModel):
    """Top page data."""

    page: str
    views: int


class AIUsageStats(BaseModel):
    """AI usage statistics."""

    total_generations: int
    successful: int
    failed: int
    success_rate: float
    total_tokens: int
    avg_tokens_per_generation: float
    total_duration_ms: int
    avg_duration_ms: float
    by_model: dict


class FunnelStep(BaseModel):
    """Funnel step data."""

    step: str
    step_number: int
    users: int
    conversion_rate: float


class UserAnalytics(BaseModel):
    """User analytics data."""

    user_id: str
    total_sessions: int
    total_page_views: int
    total_actions: int
    total_prompts: int
    total_tokens_used: int
    total_projects: int
    total_deployments: int
    first_seen: Optional[str]
    last_seen: Optional[str]


# ============================================
# Event Tracking Endpoints
# ============================================


@router.post("/track/event")
async def track_event(
    request: TrackEventRequest,
    req: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Track a custom event."""
    event = service.track_event(
        event_type=request.event_type,
        user_id=user.id,
        properties=request.properties,
        page_url=request.page_url,
        referrer=request.referrer,
        session_id=request.session_id,
        project_id=request.project_id,
        client_ip=req.client.host if req.client else None,
        user_agent=req.headers.get("user-agent"),
    )

    return {"status": "tracked", "event_id": event.id}


@router.post("/track/pageview")
async def track_page_view(
    request: TrackPageViewRequest,
    req: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Track a page view."""
    event = service.track_page_view(
        page_url=request.page_url,
        user_id=user.id,
        referrer=request.referrer,
        session_id=request.session_id,
        client_ip=req.client.host if req.client else None,
        user_agent=req.headers.get("user-agent"),
    )

    return {"status": "tracked", "event_id": event.id}


@router.post("/track/ai-generation")
async def track_ai_generation(
    request: TrackAIGenerationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Track an AI generation event."""
    event = service.track_ai_generation(
        user_id=user.id,
        project_id=request.project_id,
        prompt_tokens=request.prompt_tokens,
        completion_tokens=request.completion_tokens,
        model=request.model,
        duration_ms=request.duration_ms,
        success=request.success,
    )

    return {"status": "tracked", "event_id": event.id}


# ============================================
# Dashboard Endpoints
# ============================================


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    organization_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get dashboard metrics."""
    org_id = organization_id or user.org_id
    metrics = service.get_dashboard_metrics(organization_id=org_id)

    return DashboardResponse(
        total_users=metrics.total_users,
        active_users_today=metrics.active_users_today,
        active_users_week=metrics.active_users_week,
        active_users_month=metrics.active_users_month,
        new_users_today=metrics.new_users_today,
        new_users_week=metrics.new_users_week,
        total_projects=metrics.total_projects,
        projects_created_today=metrics.projects_created_today,
        projects_created_week=metrics.projects_created_week,
        total_ai_generations=metrics.total_ai_generations,
        ai_generations_today=metrics.ai_generations_today,
        total_tokens_used=metrics.total_tokens_used,
        tokens_used_today=metrics.tokens_used_today,
        total_deployments=metrics.total_deployments,
        deployments_today=metrics.deployments_today,
        deployment_success_rate=metrics.deployment_success_rate,
        errors_today=metrics.errors_today,
        error_rate=metrics.error_rate,
        avg_response_time=metrics.avg_response_time,
        p99_response_time=metrics.p99_response_time,
    )


@router.get("/events/timeseries", response_model=list[TimeSeriesPoint])
async def get_events_timeseries(
    event_type: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    interval: str = Query("day", pattern="^(hour|day|week)$"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get events over time."""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    data = service.get_events_over_time(
        event_type=event_type,
        start_time=start_time,
        end_time=end_time,
        interval=interval,
    )

    return [TimeSeriesPoint(**point) for point in data]


@router.get("/pages/top", response_model=list[TopPage])
async def get_top_pages(
    limit: int = Query(10, ge=1, le=100),
    days: int = Query(7, ge=1, le=365),
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get top pages by view count."""
    start_time = datetime.now(timezone.utc) - timedelta(days=days)
    data = service.get_top_pages(limit=limit, start_time=start_time)

    return [TopPage(**page) for page in data]


@router.get("/ai/usage", response_model=AIUsageStats)
async def get_ai_usage(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get AI usage statistics."""
    start_time = datetime.now(timezone.utc) - timedelta(days=days)

    # Use current user if no user_id specified
    target_user = user_id or user.id

    data = service.get_ai_usage_stats(
        user_id=target_user if user_id else None,
        start_time=start_time,
    )

    return AIUsageStats(**data)


@router.post("/funnel", response_model=list[FunnelStep])
async def get_funnel(
    request: FunnelRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get funnel conversion data."""
    start_time = datetime.now(timezone.utc) - timedelta(days=request.days)
    data = service.get_funnel_data(steps=request.steps, start_time=start_time)

    return [FunnelStep(**step) for step in data]


@router.get("/users/{user_id}", response_model=UserAnalytics)
async def get_user_analytics(
    user_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get analytics for a specific user."""
    metrics = service.get_user_metrics(user_id)

    if not metrics:
        raise HTTPException(status_code=404, detail="User not found")

    return UserAnalytics(
        user_id=metrics.user_id,
        total_sessions=metrics.total_sessions,
        total_page_views=metrics.total_page_views,
        total_actions=metrics.total_actions,
        total_prompts=metrics.total_prompts,
        total_tokens_used=metrics.total_tokens_used,
        total_projects=metrics.total_projects,
        total_deployments=metrics.total_deployments,
        first_seen=metrics.first_seen.isoformat() if metrics.first_seen else None,
        last_seen=metrics.last_seen.isoformat() if metrics.last_seen else None,
    )


# ============================================
# Real-time Metrics
# ============================================


@router.get("/realtime/active-users")
async def get_realtime_active_users(
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get real-time active user count."""
    # In production, use Redis or similar for real-time tracking
    metrics = service.get_dashboard_metrics()

    return {
        "active_now": metrics.active_users_today,
        "active_5min": metrics.active_users_today,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/realtime/events")
async def get_realtime_events(
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Get recent events in real-time."""
    # Get most recent events
    events = sorted(
        service._events,
        key=lambda e: e.timestamp,
        reverse=True,
    )[:limit]

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================
# Export Endpoints
# ============================================


@router.get("/export/csv")
async def export_csv(
    event_type: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    user: AuthenticatedUser = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """Export analytics data as CSV."""
    start_time = datetime.now(timezone.utc) - timedelta(days=days)

    events = [
        e
        for e in service._events
        if e.timestamp >= start_time and (not event_type or e.event_type == event_type)
    ]

    # Build CSV
    headers = ["timestamp", "event_type", "user_id", "page_url", "properties"]
    rows = []

    for event in events:
        rows.append(
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "user_id": event.user_id or "",
                "page_url": event.page_url or "",
                "properties": str(event.properties),
            }
        )

    return {
        "headers": headers,
        "rows": rows,
        "count": len(rows),
    }


__all__ = ["router", "swarm_router", "_query_kernel_efficiency"]


# ============================================================================
# v21.3.1 — VBus efficiency-stats query helper + /api/v1/swarm/health
# ============================================================================
#
# Restored 2026-05-02 from TRUTH-BRIDGE forensic protocol per
# OMEGA_RESUMPTION_PROTOCOL.md §2.2. The disaster-directory inline edits
# never transferred to recovery; the test bodies in
# backend/tests/audit/test_stress_75_rounds.py:b01-b15 document the
# exact expected behavior, which this section implements.
#
# _query_kernel_efficiency() connects to the VBus Unix socket (path from
# VOS3_BRIDGE_SOCKET env var or default), sends the EFFICIENCY_STATS
# ASCII command, and parses the OK|key=val|... response. When the bridge
# is unavailable (no socket, dev mode), returns kernel_online=False with
# zero-valued fields — matches B03's "graceful offline" contract.
#
# /api/v1/swarm/health computes savings_bytes / dedup_ratio / savings_pct
# from the kernel's raw counters per b13/b14/b15 math:
#   savings_bytes = max(0, virtual_bytes - physical_bytes)
#   dedup_ratio   = virtual_bytes / physical_bytes  (1.0 if denom == 0)
#   savings_pct   = (savings_bytes / virtual_bytes) * 100  (0 if denom == 0)

_KERNEL_EFFICIENCY_KEYSET = {
    "hits",
    "misses",
    "cow_breaks",
    "virtual_bytes",
    "physical_bytes",
    "dedup_ratio_x1000",
    "kernel_online",
}


def _query_kernel_efficiency() -> dict:
    """Query the VOS3 kernel's KV-cache efficiency counters via VBus.

    Returns a dict with the canonical keyset. Offline-graceful: if the
    bridge socket is missing or any I/O fails, returns kernel_online=False
    with zero/default values instead of raising.

    Wire format (kernel side, in cmd_efficiency_stats):
        OK|hits=N|misses=N|cow_breaks=N|virtual_bytes=N|
           physical_bytes=N|dedup_ratio_x1000=N
    """
    out = {
        "hits": 0,
        "misses": 0,
        "cow_breaks": 0,
        "virtual_bytes": 0,
        "physical_bytes": 0,
        "dedup_ratio_x1000": 1000,
        "kernel_online": False,
    }

    sock_path = _os.environ.get("VOS3_BRIDGE_SOCKET", "/run/vos3/bridge.sock")
    if not sock_path or not _os.path.exists(sock_path):
        return out

    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(0.25)
        s.connect(sock_path)
        s.sendall(b"EFFICIENCY_STATS\n")
        chunks: list[bytes] = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        s.close()
        payload = b"".join(chunks).decode("ascii", errors="replace").strip()
    except (OSError, _socket.timeout):
        return out

    if not payload.startswith("OK|"):
        return out

    body = payload[3:]
    for part in body.split("|"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k in out and k != "kernel_online":
            try:
                out[k] = int(v.strip())
            except ValueError:
                pass

    out["kernel_online"] = True
    return out


swarm_router = APIRouter(tags=["Swarm"])


@swarm_router.get("/v1/swarm/health")
async def swarm_health() -> dict:
    """KV-cache deduplication efficiency snapshot.

    Public endpoint — no auth (operationally observable; reveals only
    aggregate counters, no per-user data). Always returns 200 even when
    the kernel bridge is down (kernel_online=False in that case).

    Response shape (10 fields, all required by b02):
        kernel_online   : bool
        hits            : int  (KV-cache hit count)
        misses          : int
        cow_breaks      : int  (copy-on-write breaks, dedup invalidations)
        virtual_bytes   : int
        physical_bytes  : int
        dedup_ratio     : float  (virtual / physical, 1.0 on div-by-zero)
        savings_bytes   : int    (max(0, virtual - physical))
        savings_pct     : float  (savings / virtual * 100, 0 if no virt)
        honest_caveat   : str
    """
    raw = _query_kernel_efficiency()

    virtual = int(raw.get("virtual_bytes", 0))
    physical = int(raw.get("physical_bytes", 0))

    if physical > 0:
        dedup_ratio = float(virtual) / float(physical)
    else:
        dedup_ratio = 1.0

    if virtual > physical:
        savings_bytes = virtual - physical
    else:
        savings_bytes = 0

    if virtual > 0:
        savings_pct = (savings_bytes / virtual) * 100.0
    else:
        savings_pct = 0.0

    return {
        "kernel_online": bool(raw.get("kernel_online", False)),
        "hits": int(raw.get("hits", 0)),
        "misses": int(raw.get("misses", 0)),
        "cow_breaks": int(raw.get("cow_breaks", 0)),
        "virtual_bytes": virtual,
        "physical_bytes": physical,
        "dedup_ratio": dedup_ratio,
        "savings_bytes": savings_bytes,
        "savings_pct": savings_pct,
        "honest_caveat": (
            "Counters reflect KV-cache state since last kernel boot. "
            "kernel_online=False means the VBus bridge socket is "
            "unavailable; zero values are placeholders, not measurements."
        ),
    }
