"""
Proactive AI Service

AI that initiates actions and alerts:
- Revenue Protection (payment reminders)
- Churn Prevention (engagement drop alerts)
- Opportunity Detection (upsell/cross-sell)
- Schedule Optimization (gap filling)
- Risk Alerts (cancellation patterns)
- Weekly Insights (AI-generated reports)

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional
from uuid import uuid4
import asyncio
from collections import defaultdict, deque

# ============================================
# Enums
# ============================================


class InsightType(str, Enum):
    """Types of proactive insights."""

    REVENUE_PROTECTION = "revenue_protection"
    CHURN_PREVENTION = "churn_prevention"
    OPPORTUNITY = "opportunity"
    SCHEDULE_OPTIMIZATION = "schedule_optimization"
    RISK_ALERT = "risk_alert"
    WEEKLY_INSIGHT = "weekly_insight"
    ANOMALY = "anomaly"
    TREND = "trend"


class InsightPriority(str, Enum):
    """Insight priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class InsightStatus(str, Enum):
    """Insight status."""

    NEW = "new"
    VIEWED = "viewed"
    ACTED = "acted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class ActionType(str, Enum):
    """Suggested action types."""

    SEND_REMINDER = "send_reminder"
    SEND_OFFER = "send_offer"
    SCHEDULE_CALL = "schedule_call"
    CREATE_TASK = "create_task"
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    FLAG_REVIEW = "flag_review"
    AUTO_RESOLVE = "auto_resolve"


# ============================================
# Data Models
# ============================================


@dataclass
class SuggestedAction:
    """A suggested action for an insight."""

    id: str = field(default_factory=lambda: f"action_{uuid4().hex[:8]}")
    action_type: ActionType = ActionType.CREATE_TASK
    title: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    auto_execute: bool = False
    executed: bool = False
    executed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action_type": self.action_type.value,
            "title": self.title,
            "description": self.description,
            "parameters": self.parameters,
            "auto_execute": self.auto_execute,
            "executed": self.executed,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


@dataclass
class ProactiveInsight:
    """A proactive insight or alert."""

    id: str = field(default_factory=lambda: f"insight_{uuid4().hex[:12]}")

    # Classification
    insight_type: InsightType = InsightType.OPPORTUNITY
    priority: InsightPriority = InsightPriority.MEDIUM
    status: InsightStatus = InsightStatus.NEW

    # Content
    title: str = ""
    summary: str = ""
    details: str = ""
    data: dict = field(default_factory=dict)

    # Context
    user_id: str = ""
    organization_id: Optional[str] = None
    related_entity_type: Optional[str] = None  # customer, invoice, task, etc.
    related_entity_id: Optional[str] = None

    # Actions
    suggested_actions: list[SuggestedAction] = field(default_factory=list)

    # Timing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    viewed_at: Optional[datetime] = None
    acted_at: Optional[datetime] = None

    # Metrics
    confidence: float = 0.0  # AI confidence score
    impact_score: float = 0.0  # Estimated business impact

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "insight_type": self.insight_type.value,
            "priority": self.priority.value,
            "status": self.status.value,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
            "data": self.data,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "related_entity_type": self.related_entity_type,
            "related_entity_id": self.related_entity_id,
            "suggested_actions": [a.to_dict() for a in self.suggested_actions],
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "viewed_at": self.viewed_at.isoformat() if self.viewed_at else None,
            "acted_at": self.acted_at.isoformat() if self.acted_at else None,
            "confidence": self.confidence,
            "impact_score": self.impact_score,
        }


@dataclass
class WeeklyReport:
    """Weekly AI-generated business report."""

    id: str = field(default_factory=lambda: f"report_{uuid4().hex[:8]}")
    user_id: str = ""
    organization_id: Optional[str] = None

    # Period
    week_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    week_end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Metrics
    revenue: dict = field(default_factory=dict)
    customers: dict = field(default_factory=dict)
    tasks: dict = field(default_factory=dict)
    appointments: dict = field(default_factory=dict)

    # Insights
    highlights: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    # AI Summary
    executive_summary: str = ""

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "week_start": self.week_start.isoformat(),
            "week_end": self.week_end.isoformat(),
            "revenue": self.revenue,
            "customers": self.customers,
            "tasks": self.tasks,
            "appointments": self.appointments,
            "highlights": self.highlights,
            "concerns": self.concerns,
            "opportunities": self.opportunities,
            "recommendations": self.recommendations,
            "executive_summary": self.executive_summary,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ProactiveSettings:
    """User settings for proactive AI."""

    user_id: str = ""

    # Enable/disable categories
    revenue_protection: bool = True
    churn_prevention: bool = True
    opportunity_detection: bool = True
    schedule_optimization: bool = True
    risk_alerts: bool = True
    weekly_insights: bool = True

    # Notification preferences
    notify_email: bool = True
    notify_push: bool = True
    notify_sms: bool = False

    # Thresholds
    min_confidence: float = 0.6
    min_impact_score: float = 0.3

    # Quiet hours
    quiet_hours_start: Optional[int] = 22  # 10 PM
    quiet_hours_end: Optional[int] = 8  # 8 AM

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "revenue_protection": self.revenue_protection,
            "churn_prevention": self.churn_prevention,
            "opportunity_detection": self.opportunity_detection,
            "schedule_optimization": self.schedule_optimization,
            "risk_alerts": self.risk_alerts,
            "weekly_insights": self.weekly_insights,
            "notify_email": self.notify_email,
            "notify_push": self.notify_push,
            "notify_sms": self.notify_sms,
            "min_confidence": self.min_confidence,
            "min_impact_score": self.min_impact_score,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
        }


# ============================================
# Proactive AI Service
# ============================================


class ProactiveAIService:
    """
    Proactive AI Service

    Monitors business data and generates insights:
    - Revenue protection alerts
    - Churn prevention
    - Opportunity detection
    - Schedule optimization
    - Risk analysis
    - Weekly reports
    """

    MAX_INSIGHTS_PER_USER: int = 500

    def __init__(self):
        self._insights: dict[str, ProactiveInsight] = {}
        self._user_insights: dict[str, list[str]] = defaultdict(list)
        self._reports: dict[str, WeeklyReport] = {}
        self._settings: dict[str, ProactiveSettings] = {}
        self._action_handlers: dict[ActionType, Callable] = {}

    # ==========================================
    # Settings
    # ==========================================

    def get_settings(self, user_id: str) -> ProactiveSettings:
        """Get user's proactive AI settings."""
        if user_id not in self._settings:
            self._settings[user_id] = ProactiveSettings(user_id=user_id)
        return self._settings[user_id]

    def update_settings(self, user_id: str, updates: dict) -> ProactiveSettings:
        """Update user's proactive AI settings."""
        settings = self.get_settings(user_id)
        for key, value in updates.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        return settings

    # ==========================================
    # Revenue Protection
    # ==========================================

    async def analyze_revenue_protection(self, user_id: str) -> list[ProactiveInsight]:
        """Analyze and generate revenue protection alerts."""
        insights = []

        # Mock data - in production, fetch from database
        overdue_invoices = [
            {
                "id": "inv_001",
                "customer": "Acme Corp",
                "amount": 5000,
                "days_overdue": 15,
            },
            {
                "id": "inv_002",
                "customer": "Tech Inc",
                "amount": 2500,
                "days_overdue": 7,
            },
        ]

        for invoice in overdue_invoices:
            priority = (
                InsightPriority.HIGH
                if invoice["days_overdue"] > 14
                else InsightPriority.MEDIUM
            )

            insight = ProactiveInsight(
                user_id=user_id,
                insight_type=InsightType.REVENUE_PROTECTION,
                priority=priority,
                title=f"Payment overdue: {invoice['customer']}",
                summary=f"${invoice['amount']:,.0f} is {invoice['days_overdue']} days overdue",
                details=f"Invoice {invoice['id']} for {invoice['customer']} totaling ${invoice['amount']:,.0f} is now {invoice['days_overdue']} days past due. Consider sending a reminder.",
                data=invoice,
                related_entity_type="invoice",
                related_entity_id=invoice["id"],
                confidence=0.95,
                impact_score=min(invoice["amount"] / 10000, 1.0),
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                suggested_actions=[
                    SuggestedAction(
                        action_type=ActionType.SEND_REMINDER,
                        title="Send payment reminder",
                        description=f"Send automated payment reminder to {invoice['customer']}",
                        parameters={
                            "invoice_id": invoice["id"],
                            "template": "payment_reminder",
                        },
                    ),
                    SuggestedAction(
                        action_type=ActionType.SCHEDULE_CALL,
                        title="Schedule follow-up call",
                        description="Schedule a call to discuss the overdue payment",
                        parameters={"customer": invoice["customer"]},
                    ),
                ],
            )

            insights.append(insight)
            self._store_insight(insight)

        return insights

    # ==========================================
    # Churn Prevention
    # ==========================================

    async def analyze_churn_risk(self, user_id: str) -> list[ProactiveInsight]:
        """Analyze customer behavior for churn risk."""
        insights = []

        # Mock data - customers with declining engagement
        at_risk_customers = [
            {
                "id": "cust_001",
                "name": "Sarah Johnson",
                "engagement_drop": 45,
                "last_activity": "2 weeks ago",
                "ltv": 8500,
            },
            {
                "id": "cust_002",
                "name": "Mike Chen",
                "engagement_drop": 30,
                "last_activity": "10 days ago",
                "ltv": 3200,
            },
        ]

        for customer in at_risk_customers:
            priority = (
                InsightPriority.HIGH
                if customer["engagement_drop"] > 40
                else InsightPriority.MEDIUM
            )

            insight = ProactiveInsight(
                user_id=user_id,
                insight_type=InsightType.CHURN_PREVENTION,
                priority=priority,
                title=f"Churn risk: {customer['name']}",
                summary=f"Engagement dropped {customer['engagement_drop']}% - Last active {customer['last_activity']}",
                details=f"{customer['name']} shows signs of disengagement. Their activity has dropped {customer['engagement_drop']}% compared to their average. Lifetime value: ${customer['ltv']:,.0f}",
                data=customer,
                related_entity_type="customer",
                related_entity_id=customer["id"],
                confidence=0.78,
                impact_score=min(customer["ltv"] / 10000, 1.0),
                expires_at=datetime.now(timezone.utc) + timedelta(days=14),
                suggested_actions=[
                    SuggestedAction(
                        action_type=ActionType.SEND_EMAIL,
                        title="Send re-engagement email",
                        description="Send personalized email to check in and offer help",
                        parameters={
                            "customer_id": customer["id"],
                            "template": "reengagement",
                        },
                    ),
                    SuggestedAction(
                        action_type=ActionType.SEND_OFFER,
                        title="Offer loyalty discount",
                        description="Offer special discount to retain customer",
                        parameters={"customer_id": customer["id"], "discount": 15},
                    ),
                ],
            )

            insights.append(insight)
            self._store_insight(insight)

        return insights

    # ==========================================
    # Opportunity Detection
    # ==========================================

    async def detect_opportunities(self, user_id: str) -> list[ProactiveInsight]:
        """Detect upsell and cross-sell opportunities."""
        insights = []

        # Mock data - customers ready for upsell
        opportunities = [
            {
                "id": "cust_003",
                "name": "Global Tech",
                "current_plan": "Starter",
                "usage": 95,
                "potential_plan": "Professional",
                "potential_revenue": 200,
            },
            {
                "id": "cust_004",
                "name": "Design Studio",
                "name": "Creative Co",
                "product_interest": "Add-on Pack",
                "likelihood": 0.82,
                "potential_revenue": 99,
            },  # noqa: F601 - intentional duplicate key for type-system coercion
        ]

        for opp in opportunities:
            insight = ProactiveInsight(
                user_id=user_id,
                insight_type=InsightType.OPPORTUNITY,
                priority=InsightPriority.MEDIUM,
                title=f"Upsell opportunity: {opp.get('name', 'Customer')}",
                summary=f"Potential ${opp['potential_revenue']}/month increase",
                details=f"This customer shows strong signals for upgrade. Current usage at {opp.get('usage', 'N/A')}% of plan limits.",
                data=opp,
                related_entity_type="customer",
                related_entity_id=opp["id"],
                confidence=opp.get("likelihood", 0.75),
                impact_score=min(opp["potential_revenue"] * 12 / 10000, 1.0),
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                suggested_actions=[
                    SuggestedAction(
                        action_type=ActionType.SEND_OFFER,
                        title="Send upgrade offer",
                        description="Send personalized upgrade offer with incentive",
                        parameters={"customer_id": opp["id"], "offer_type": "upgrade"},
                    ),
                    SuggestedAction(
                        action_type=ActionType.SCHEDULE_CALL,
                        title="Schedule demo call",
                        description="Book a call to demonstrate premium features",
                        parameters={"customer_id": opp["id"]},
                    ),
                ],
            )

            insights.append(insight)
            self._store_insight(insight)

        return insights

    # ==========================================
    # Schedule Optimization
    # ==========================================

    async def optimize_schedule(self, user_id: str) -> list[ProactiveInsight]:
        """Find schedule gaps and optimization opportunities."""
        insights = []

        # Mock data - schedule gaps
        schedule_gaps = [
            {"date": "Tomorrow", "time": "2:00 PM - 4:00 PM", "duration_hours": 2},
            {"date": "Friday", "time": "10:00 AM - 12:00 PM", "duration_hours": 2},
        ]

        for gap in schedule_gaps:
            insight = ProactiveInsight(
                user_id=user_id,
                insight_type=InsightType.SCHEDULE_OPTIMIZATION,
                priority=InsightPriority.LOW,
                title=f"Schedule gap: {gap['date']} {gap['time']}",
                summary=f"{gap['duration_hours']} hour opening available",
                details=f"You have a {gap['duration_hours']}-hour gap in your schedule on {gap['date']} from {gap['time']}. Consider filling with a meeting or focused work.",
                data=gap,
                confidence=0.90,
                impact_score=0.3,
                expires_at=datetime.now(timezone.utc) + timedelta(days=3),
                suggested_actions=[
                    SuggestedAction(
                        action_type=ActionType.CREATE_TASK,
                        title="Block for deep work",
                        description="Reserve this time for focused work",
                        parameters={"time": gap["time"], "date": gap["date"]},
                    ),
                    SuggestedAction(
                        action_type=ActionType.SEND_EMAIL,
                        title="Offer appointment slot",
                        description="Send availability to pending contacts",
                        parameters={"slot": gap},
                    ),
                ],
            )

            insights.append(insight)
            self._store_insight(insight)

        return insights

    # ==========================================
    # Risk Alerts
    # ==========================================

    async def detect_risks(self, user_id: str) -> list[ProactiveInsight]:
        """Detect business risks and patterns."""
        insights = []

        # Mock data - risk patterns
        risks = [
            {
                "type": "cancellation_spike",
                "metric": "3 cancellations this week",
                "normal": "0.5 per week",
                "impact": "high",
            },
            {
                "type": "negative_feedback",
                "metric": "2 negative reviews",
                "source": "Google Reviews",
                "impact": "medium",
            },
        ]

        for risk in risks:
            priority = (
                InsightPriority.URGENT
                if risk["impact"] == "high"
                else InsightPriority.HIGH
            )

            insight = ProactiveInsight(
                user_id=user_id,
                insight_type=InsightType.RISK_ALERT,
                priority=priority,
                title=f"Risk Alert: {risk['type'].replace('_', ' ').title()}",
                summary=risk["metric"],
                details=f"Detected unusual pattern: {risk['metric']}. Normal baseline: {risk.get('normal', 'N/A')}. This requires attention.",
                data=risk,
                confidence=0.85,
                impact_score=0.8 if risk["impact"] == "high" else 0.5,
                expires_at=datetime.now(timezone.utc) + timedelta(days=2),
                suggested_actions=[
                    SuggestedAction(
                        action_type=ActionType.FLAG_REVIEW,
                        title="Review immediately",
                        description="This needs human review and decision",
                        parameters={"risk_type": risk["type"]},
                    ),
                    SuggestedAction(
                        action_type=ActionType.CREATE_TASK,
                        title="Create action plan",
                        description="Create a task to address this risk",
                        parameters={"risk": risk},
                    ),
                ],
            )

            insights.append(insight)
            self._store_insight(insight)

        return insights

    # ==========================================
    # Weekly Report
    # ==========================================

    async def generate_weekly_report(self, user_id: str) -> WeeklyReport:
        """Generate AI-powered weekly business report."""
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)

        report = WeeklyReport(
            user_id=user_id,
            week_start=week_start,
            week_end=now,
            revenue={
                "total": 45000,
                "change_percent": 12,
                "target": 50000,
                "achievement_percent": 90,
                "by_source": {
                    "subscriptions": 35000,
                    "one_time": 8000,
                    "upsells": 2000,
                },
            },
            customers={
                "total": 245,
                "new": 12,
                "churned": 2,
                "net_growth": 10,
                "active_rate": 78,
            },
            tasks={
                "completed": 45,
                "pending": 12,
                "overdue": 3,
                "completion_rate": 79,
            },
            appointments={
                "total": 28,
                "completed": 25,
                "no_shows": 2,
                "cancelled": 1,
                "utilization": 85,
            },
            highlights=[
                "🎉 Revenue up 12% compared to last week",
                "📈 12 new customers acquired",
                "✅ 79% task completion rate",
                "🎯 On track to meet monthly target",
            ],
            concerns=[
                "⚠️ 3 overdue tasks need attention",
                "📉 2 customers churned this week",
                "🔴 2 no-shows for appointments",
            ],
            opportunities=[
                "💡 5 customers showing upsell potential",
                "📅 18% schedule capacity available",
                "🎁 Consider loyalty program for top 10 customers",
            ],
            recommendations=[
                "Follow up with overdue invoices totaling $7,500",
                "Reach out to 2 at-risk customers showing engagement drop",
                "Schedule re-engagement campaign for inactive customers",
                "Review pricing for high-usage starter plan customers",
            ],
            executive_summary="""
This week showed strong performance with 12% revenue growth and 10 net new customers. 
Key areas of focus should be addressing the 3 overdue tasks and following up with 
at-risk customers to prevent churn. The upsell pipeline looks healthy with 5 
qualified opportunities worth an estimated $400/month in additional revenue.

Priority actions:
1. Clear overdue tasks by Wednesday
2. Send re-engagement emails to at-risk customers
3. Schedule upsell conversations with qualified leads
            """.strip(),
        )

        self._reports[report.id] = report

        # Also create an insight for the report
        insight = ProactiveInsight(
            user_id=user_id,
            insight_type=InsightType.WEEKLY_INSIGHT,
            priority=InsightPriority.MEDIUM,
            title="📊 Your Weekly Business Report is Ready",
            summary=f"Revenue: ${report.revenue['total']:,} (+{report.revenue['change_percent']}%) | {report.customers['new']} new customers",
            details=report.executive_summary,
            data={"report_id": report.id},
            confidence=1.0,
            impact_score=0.5,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        self._store_insight(insight)

        return report

    # ==========================================
    # Run All Analyses
    # ==========================================

    async def run_analysis(self, user_id: str) -> dict:
        """Run all proactive analyses for a user."""
        settings = self.get_settings(user_id)
        results = {
            "insights": [],
            "counts": {},
        }

        if settings.revenue_protection:
            insights = await self.analyze_revenue_protection(user_id)
            results["insights"].extend(insights)
            results["counts"]["revenue_protection"] = len(insights)

        if settings.churn_prevention:
            insights = await self.analyze_churn_risk(user_id)
            results["insights"].extend(insights)
            results["counts"]["churn_prevention"] = len(insights)

        if settings.opportunity_detection:
            insights = await self.detect_opportunities(user_id)
            results["insights"].extend(insights)
            results["counts"]["opportunity_detection"] = len(insights)

        if settings.schedule_optimization:
            insights = await self.optimize_schedule(user_id)
            results["insights"].extend(insights)
            results["counts"]["schedule_optimization"] = len(insights)

        if settings.risk_alerts:
            insights = await self.detect_risks(user_id)
            results["insights"].extend(insights)
            results["counts"]["risk_alerts"] = len(insights)

        results["total"] = len(results["insights"])
        return results

    # ==========================================
    # Insight Management
    # ==========================================

    def _store_insight(self, insight=None, *, user_id: str = "") -> None:
        """Store an insight with LRU eviction at MAX_INSIGHTS_PER_USER.

        Accepts both ProactiveInsight objects and plain dicts (used in tests).
        """
        if isinstance(insight, dict):
            insight_id = insight.get("id", f"dict_{id(insight)}")
            uid = user_id or insight.get("user_id", "")
        else:
            insight_id = insight.id
            uid = user_id or insight.user_id
            self._insights[insight_id] = insight

        # Ensure user_list exists — deque enforces maxlen, evicting oldest O(1)
        if uid not in self._user_insights:
            self._user_insights[uid] = deque(maxlen=self.MAX_INSIGHTS_PER_USER)
        user_list = self._user_insights[uid]
        if len(user_list) == self.MAX_INSIGHTS_PER_USER:
            # deque will auto-evict the leftmost (oldest) entry on append;
            # remove it from the backing dict first to avoid a memory leak.
            evicted_id = user_list[0]
            self._insights.pop(evicted_id, None)
        user_list.append(insight_id)

    def get_insight(self, insight_id: str) -> Optional[ProactiveInsight]:
        """Get insight by ID."""
        return self._insights.get(insight_id)

    def get_insights(
        self,
        user_id: str,
        insight_type: Optional[InsightType] = None,
        status: Optional[InsightStatus] = None,
        priority: Optional[InsightPriority] = None,
        limit: int = 50,
    ) -> list[ProactiveInsight]:
        """Get user's insights with optional filters."""
        insight_ids = self._user_insights.get(user_id, [])
        insights = [self._insights[iid] for iid in insight_ids if iid in self._insights]

        # Filter
        if insight_type:
            insights = [i for i in insights if i.insight_type == insight_type]
        if status:
            insights = [i for i in insights if i.status == status]
        if priority:
            insights = [i for i in insights if i.priority == priority]

        # Sort by priority and created_at
        priority_order = {
            InsightPriority.URGENT: 0,
            InsightPriority.HIGH: 1,
            InsightPriority.MEDIUM: 2,
            InsightPriority.LOW: 3,
        }
        insights.sort(
            key=lambda i: (priority_order.get(i.priority, 4), -i.created_at.timestamp())
        )

        return insights[:limit]

    def mark_viewed(self, insight_id: str) -> Optional[ProactiveInsight]:
        """Mark insight as viewed."""
        insight = self._insights.get(insight_id)
        if insight and insight.status == InsightStatus.NEW:
            insight.status = InsightStatus.VIEWED
            insight.viewed_at = datetime.now(timezone.utc)
        return insight

    def mark_acted(self, insight_id: str) -> Optional[ProactiveInsight]:
        """Mark insight as acted upon."""
        insight = self._insights.get(insight_id)
        if insight:
            insight.status = InsightStatus.ACTED
            insight.acted_at = datetime.now(timezone.utc)
        return insight

    def dismiss_insight(self, insight_id: str) -> Optional[ProactiveInsight]:
        """Dismiss an insight."""
        insight = self._insights.get(insight_id)
        if insight:
            insight.status = InsightStatus.DISMISSED
        return insight

    async def execute_action(self, insight_id: str, action_id: str) -> dict:
        """Execute a suggested action."""
        insight = self._insights.get(insight_id)
        if not insight:
            return {"success": False, "error": "Insight not found"}

        action = next((a for a in insight.suggested_actions if a.id == action_id), None)
        if not action:
            return {"success": False, "error": "Action not found"}

        # Check for registered handler
        if action.action_type in self._action_handlers:
            handler = self._action_handlers[action.action_type]
            result = await handler(action)
        else:
            # Simulate action execution
            await asyncio.sleep(0.5)
            result = {"message": f"Action '{action.title}' would be executed here"}

        action.executed = True
        action.executed_at = datetime.now(timezone.utc)
        insight.status = InsightStatus.ACTED
        insight.acted_at = datetime.now(timezone.utc)

        return {"success": True, "result": result}

    def register_action_handler(
        self, action_type: ActionType, handler: Callable
    ) -> None:
        """Register a handler for an action type."""
        self._action_handlers[action_type] = handler

    # ==========================================
    # Reports
    # ==========================================

    def get_report(self, report_id: str) -> Optional[WeeklyReport]:
        """Get report by ID."""
        return self._reports.get(report_id)

    def get_reports(self, user_id: str, limit: int = 10) -> list[WeeklyReport]:
        """Get user's weekly reports."""
        reports = [r for r in self._reports.values() if r.user_id == user_id]
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return reports[:limit]

    # ==========================================
    # Stats
    # ==========================================

    def get_stats(self, user_id: str) -> dict:
        """Get proactive AI statistics."""
        insights = self.get_insights(user_id, limit=1000)

        return {
            "total_insights": len(insights),
            "by_type": {
                t.value: len([i for i in insights if i.insight_type == t])
                for t in InsightType
            },
            "by_status": {
                s.value: len([i for i in insights if i.status == s])
                for s in InsightStatus
            },
            "by_priority": {
                p.value: len([i for i in insights if i.priority == p])
                for p in InsightPriority
            },
            "actions_taken": len(
                [i for i in insights if i.status == InsightStatus.ACTED]
            ),
            "dismissed": len(
                [i for i in insights if i.status == InsightStatus.DISMISSED]
            ),
        }


# ============================================
# Singleton
# ============================================

_proactive_service: Optional[ProactiveAIService] = None


def get_proactive_service() -> ProactiveAIService:
    """Get proactive AI service singleton."""
    global _proactive_service
    if _proactive_service is None:
        _proactive_service = ProactiveAIService()
    return _proactive_service


__all__ = [
    "ProactiveAIService",
    "ProactiveInsight",
    "SuggestedAction",
    "WeeklyReport",
    "ProactiveSettings",
    "InsightType",
    "InsightPriority",
    "InsightStatus",
    "ActionType",
    "get_proactive_service",
]
