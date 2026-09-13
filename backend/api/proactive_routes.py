"""
Proactive AI API Routes

Endpoints for proactive AI insights, analysis, reports, and settings.
Uses ProactiveAIService injected via Depends(get_proactive_service).

SECURITY (April 2026 audit fix):
- Every endpoint is now gated by get_current_user. Previously all 16
  endpoints were fully unauthenticated. Analysis endpoints (/analyze,
  /analyze/revenue, /analyze/churn, /analyze/opportunities,
  /reports/generate, /insights/{id}/action) trigger paid LLM calls, so
  unauthenticated access directly translated to LLM-spend abuse.
- Settings mutation is additionally gated on admin:full permission —
  insights are tenant-global in the current service design and a
  non-admin must not be able to reconfigure the analysis cadence.
- Full per-tenant scoping of insights/reports is a Phase 1 service-layer
  change; the current fix prevents unauthenticated access outright.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from proactive.proactive_service import ProactiveAIService, get_proactive_service

router = APIRouter(prefix="/proactive", tags=["Proactive"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ActionRequest(BaseModel):
    """Request body for executing a suggested action."""

    action_id: str


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------


@router.post("/analyze")
async def run_analysis(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Run all proactive analyses."""
    result = await svc.run_analysis(user.id)
    insights = [obj.to_dict() for obj in result["insights"]]
    return {
        "total": result["total"],
        "counts": result["counts"],
        "insights": insights,
    }


@router.post("/analyze/revenue")
async def analyze_revenue(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Analyze revenue protection."""
    items = await svc.analyze_revenue_protection(user.id)
    return {
        "count": len(items),
        "insights": [obj.to_dict() for obj in items],
    }


@router.post("/analyze/churn")
async def analyze_churn(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Analyze churn risk."""
    items = await svc.analyze_churn_risk(user.id)
    return {
        "count": len(items),
        "insights": [obj.to_dict() for obj in items],
    }


@router.post("/analyze/opportunities")
async def analyze_opportunities(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Detect opportunities."""
    items = await svc.detect_opportunities(user.id)
    return {
        "count": len(items),
        "insights": [obj.to_dict() for obj in items],
    }


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


@router.get("/insights")
async def get_insights(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get all insights."""
    items = svc.get_insights(user_id=user.id)
    insights = [obj.to_dict() for obj in items]
    return {
        "insights": insights,
        "count": len(insights),
    }


@router.get("/insights/{insight_id}")
async def get_insight(
    insight_id: str,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single insight by ID."""
    obj = svc.get_insight(insight_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=404, detail="Insight not found")
    return obj.to_dict()


@router.post("/insights/{insight_id}/view")
async def mark_viewed(
    insight_id: str,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Mark an insight as viewed."""
    obj = svc.get_insight(insight_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=404, detail="Insight not found")
    obj = svc.mark_viewed(insight_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    return {
        "message": "Insight marked as viewed",
        "status": obj.status.value,
    }


@router.post("/insights/{insight_id}/dismiss")
async def dismiss_insight(
    insight_id: str,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Dismiss an insight."""
    obj = svc.get_insight(insight_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=404, detail="Insight not found")
    obj = svc.dismiss_insight(insight_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    return {
        "message": "Insight dismissed",
        "status": obj.status.value,
    }


@router.post("/insights/{insight_id}/action")
async def execute_action(
    insight_id: str,
    body: ActionRequest,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Execute a suggested action on an insight."""
    obj = svc.get_insight(insight_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=404, detail="Insight not found")
    result = await svc.execute_action(insight_id, body.action_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "Action failed")
        )
    return result


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@router.post("/reports/generate")
async def generate_weekly_report(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Generate a weekly report."""
    obj = await svc.generate_weekly_report(user.id)
    return obj.to_dict()


@router.get("/reports")
async def get_reports(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get all reports."""
    items = svc.get_reports(user_id=user.id)
    reports = [obj.to_dict() for obj in items]
    return {
        "reports": reports,
        "count": len(reports),
    }


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single report by ID."""
    obj = svc.get_report(report_id)
    if obj is None or obj.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")
    return obj.to_dict()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_settings(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get proactive AI settings."""
    obj = svc.get_settings(user.id)
    return obj.to_dict()


@router.patch("/settings")
async def update_settings(
    request: Request,
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update proactive AI settings (admin only — tenant-global config)."""
    if not user.has_permission("admin:full"):
        raise HTTPException(status_code=403, detail="Admin access required")
    data = await request.json()
    obj = svc.update_settings(user.id, data)
    return obj.to_dict()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_stats(
    svc: ProactiveAIService = Depends(get_proactive_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get proactive AI statistics."""
    return svc.get_stats(user.id)


# ---------------------------------------------------------------------------
# Types (static)
# ---------------------------------------------------------------------------


@router.get("/types")
async def get_insight_types(user: AuthenticatedUser = Depends(get_current_user)):
    """Get available insight types, priorities, and statuses."""
    return {
        "types": [
            {"id": "revenue_protection", "name": "Revenue Protection"},
            {"id": "churn_risk", "name": "Churn Risk"},
            {"id": "opportunity", "name": "Opportunity Detection"},
            {"id": "schedule_optimization", "name": "Schedule Optimization"},
            {"id": "risk_alert", "name": "Risk Alert"},
        ],
        "priorities": [
            {"id": "critical", "name": "Critical"},
            {"id": "high", "name": "High"},
            {"id": "medium", "name": "Medium"},
            {"id": "low", "name": "Low"},
        ],
        "statuses": [
            {"id": "new", "name": "New"},
            {"id": "pending", "name": "Pending"},
            {"id": "viewed", "name": "Viewed"},
            {"id": "dismissed", "name": "Dismissed"},
            {"id": "actioned", "name": "Actioned"},
        ],
    }
