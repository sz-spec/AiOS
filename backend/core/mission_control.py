"""
V Core - Mission Control

AI Activity Monitoring, Approvals, Metrics Dashboard
Based on V PRD Section 4.9

Features:
- Real-time AI agent activity monitoring
- Approval workflows (human-in-the-loop)
- System metrics and KPIs
- Alert management
- Performance analytics
- Cost tracking
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
import random

from core.base_service import PersistentService

logger = logging.getLogger(__name__)

try:
    from core.repositories import get_approval_repository, get_alert_repository
except ImportError:
    pass


# ============================================
# Enums
# ============================================


class ActivityType(str, Enum):
    AGENT_ACTION = "agent_action"
    WORKFLOW_RUN = "workflow_run"
    API_CALL = "api_call"
    USER_ACTION = "user_action"
    SYSTEM_EVENT = "system_event"
    INTEGRATION = "integration"


class ActivityStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUTO_APPROVED = "auto_approved"


class ApprovalPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    RATE = "rate"


# ============================================
# Activity Tracking
# ============================================


@dataclass
class Activity:
    """AI/System activity record."""

    id: str = field(default_factory=lambda: f"act_{uuid4().hex[:12]}")
    organization_id: str = ""

    # Type and source
    type: ActivityType = ActivityType.AGENT_ACTION
    source_type: str = ""  # agent, workflow, user, system
    source_id: str = ""
    source_name: str = ""

    # Action details
    action: str = ""
    description: str = ""

    # Status
    status: ActivityStatus = ActivityStatus.COMPLETED

    # Data
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    # Performance
    duration_ms: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0

    # Error
    error: Optional[str] = None

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "type": self.type.value,
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "sourceName": self.source_name,
            "action": self.action,
            "description": self.description,
            "status": self.status.value,
            "inputData": self.input_data,
            "outputData": self.output_data,
            "metadata": self.metadata,
            "durationMs": self.duration_ms,
            "tokensUsed": self.tokens_used,
            "costUsd": self.cost_usd,
            "error": self.error,
            "startedAt": self.started_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
        }


# ============================================
# Approval System
# ============================================


@dataclass
class ApprovalRequest:
    """Human-in-the-loop approval request."""

    id: str = field(default_factory=lambda: f"apr_{uuid4().hex[:12]}")
    organization_id: str = ""

    # Request details
    title: str = ""
    description: str = ""
    category: str = ""  # communication, financial, data, deployment

    # Source
    requested_by_type: str = ""  # agent, workflow, system
    requested_by_id: str = ""
    requested_by_name: str = ""

    # Priority
    priority: ApprovalPriority = ApprovalPriority.NORMAL

    # Data to approve
    action_type: str = ""
    action_data: dict = field(default_factory=dict)
    preview: Optional[str] = None

    # Context
    context: dict = field(default_factory=dict)

    # Status
    status: ApprovalStatus = ApprovalStatus.PENDING

    # Assignment
    assigned_to: Optional[str] = None  # user_id
    assigned_team: Optional[str] = None

    # Response
    responded_by: Optional[str] = None
    responded_at: Optional[datetime] = None
    response_note: Optional[str] = None

    # Expiry
    expires_at: Optional[datetime] = None
    auto_approve_at: Optional[datetime] = None

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "requestedByType": self.requested_by_type,
            "requestedById": self.requested_by_id,
            "requestedByName": self.requested_by_name,
            "priority": self.priority.value,
            "actionType": self.action_type,
            "actionData": self.action_data,
            "preview": self.preview,
            "context": self.context,
            "status": self.status.value,
            "assignedTo": self.assigned_to,
            "assignedTeam": self.assigned_team,
            "respondedBy": self.responded_by,
            "respondedAt": self.responded_at.isoformat() if self.responded_at else None,
            "responseNote": self.response_note,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "autoApproveAt": (
                self.auto_approve_at.isoformat() if self.auto_approve_at else None
            ),
            "createdAt": self.created_at.isoformat(),
        }


# ============================================
# Alerts
# ============================================


@dataclass
class Alert:
    """System alert."""

    id: str = field(default_factory=lambda: f"alert_{uuid4().hex[:12]}")
    organization_id: str = ""

    # Alert details
    title: str = ""
    message: str = ""
    severity: AlertSeverity = AlertSeverity.INFO
    category: str = ""  # performance, error, security, cost, usage

    # Source
    source_type: str = ""
    source_id: str = ""

    # Data
    data: dict = field(default_factory=dict)

    # Status
    is_read: bool = False
    is_resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None

    # Timing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category,
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "data": self.data,
            "isRead": self.is_read,
            "isResolved": self.is_resolved,
            "resolvedBy": self.resolved_by,
            "resolvedAt": self.resolved_at.isoformat() if self.resolved_at else None,
            "createdAt": self.created_at.isoformat(),
        }


# ============================================
# Metrics
# ============================================


@dataclass
class MetricDefinition:
    """Metric definition."""

    id: str
    name: str
    description: str
    type: MetricType
    unit: str = ""
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
            "unit": self.unit,
            "category": self.category,
        }


@dataclass
class MetricValue:
    """Metric value at a point in time."""

    metric_id: str
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "metricId": self.metric_id,
            "value": self.value,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
        }


# ============================================
# Dashboard Widgets
# ============================================


@dataclass
class DashboardWidget:
    """Dashboard widget configuration."""

    id: str = field(default_factory=lambda: f"widget_{uuid4().hex[:8]}")
    type: str = "metric"  # metric, chart, list, status, gauge
    title: str = ""
    metric_ids: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    position_x: int = 0
    position_y: int = 0
    width: int = 1
    height: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "metricIds": self.metric_ids,
            "config": self.config,
            "positionX": self.position_x,
            "positionY": self.position_y,
            "width": self.width,
            "height": self.height,
        }


# ============================================
# System Metrics Definitions
# ============================================

SYSTEM_METRICS = [
    MetricDefinition(
        "agents_active",
        "Active Agents",
        "Number of currently active AI agents",
        MetricType.GAUGE,
        "",
        "agents",
    ),
    MetricDefinition(
        "agents_actions_total",
        "Agent Actions",
        "Total agent actions performed",
        MetricType.COUNTER,
        "",
        "agents",
    ),
    MetricDefinition(
        "agents_success_rate",
        "Agent Success Rate",
        "Percentage of successful agent actions",
        MetricType.GAUGE,
        "%",
        "agents",
    ),
    MetricDefinition(
        "workflows_active",
        "Active Workflows",
        "Number of active workflows",
        MetricType.GAUGE,
        "",
        "workflows",
    ),
    MetricDefinition(
        "workflows_executions_total",
        "Workflow Executions",
        "Total workflow executions",
        MetricType.COUNTER,
        "",
        "workflows",
    ),
    MetricDefinition(
        "workflows_success_rate",
        "Workflow Success Rate",
        "Percentage of successful workflow runs",
        MetricType.GAUGE,
        "%",
        "workflows",
    ),
    MetricDefinition(
        "api_requests_total",
        "API Requests",
        "Total API requests",
        MetricType.COUNTER,
        "",
        "api",
    ),
    MetricDefinition(
        "api_latency_avg",
        "API Latency",
        "Average API response time",
        MetricType.GAUGE,
        "ms",
        "api",
    ),
    MetricDefinition(
        "api_error_rate",
        "API Error Rate",
        "Percentage of API errors",
        MetricType.GAUGE,
        "%",
        "api",
    ),
    MetricDefinition(
        "tokens_used_total",
        "Tokens Used",
        "Total LLM tokens consumed",
        MetricType.COUNTER,
        "",
        "cost",
    ),
    MetricDefinition(
        "cost_total",
        "Total Cost",
        "Total AI/API costs",
        MetricType.COUNTER,
        "USD",
        "cost",
    ),
    MetricDefinition(
        "cost_daily",
        "Daily Cost",
        "Daily AI/API costs",
        MetricType.GAUGE,
        "USD",
        "cost",
    ),
    MetricDefinition(
        "users_active",
        "Active Users",
        "Daily active users",
        MetricType.GAUGE,
        "",
        "users",
    ),
    MetricDefinition(
        "records_total",
        "Total Records",
        "Total data records",
        MetricType.GAUGE,
        "",
        "data",
    ),
    MetricDefinition(
        "storage_used",
        "Storage Used",
        "Storage space used",
        MetricType.GAUGE,
        "GB",
        "data",
    ),
    MetricDefinition(
        "approvals_pending",
        "Pending Approvals",
        "Number of pending approval requests",
        MetricType.GAUGE,
        "",
        "approvals",
    ),
    MetricDefinition(
        "approvals_avg_time",
        "Avg Approval Time",
        "Average time to approve",
        MetricType.GAUGE,
        "min",
        "approvals",
    ),
]


# ============================================
# Mission Control Service
# ============================================


class MissionControlService(PersistentService):
    """Mission Control - Monitoring, Approvals, Metrics."""

    def __init__(self, approval_repo=None, alert_repo=None, use_persistence=None):
        """
        Initialize MissionControlService.

        Args:
            approval_repo: Optional approval repository
            alert_repo: Optional alert repository
            use_persistence: If True, auto-create repos. If None, check env var.
        """
        self._using_persistence = self._bootstrap_persistence(use_persistence)

        if self._using_persistence:
            self._approval_repo = approval_repo or get_approval_repository()
            self._alert_repo = alert_repo or get_alert_repository()
        else:
            self._approval_repo = None
            self._alert_repo = None

        # In-memory cache (always available)
        self._activities: list[Activity] = []
        self._approvals: dict[str, ApprovalRequest] = {}
        self._alerts: list[Alert] = []
        self._metrics: dict[str, list[MetricValue]] = {}
        self._metric_definitions = {m.id: m for m in SYSTEM_METRICS}

        if not self._using_persistence:
            self._init_sample_data()

    def _init_sample_data(self):
        """Initialize with sample data."""
        org_id = "org_demo"

        # Sample activities
        actions = [
            (
                "agent_action",
                "Sales Assistant",
                "send_follow_up",
                "Sent follow-up email to lead",
            ),
            (
                "agent_action",
                "Support Agent",
                "answer_question",
                "Answered customer FAQ",
            ),
            (
                "workflow_run",
                "Welcome Email",
                "execute",
                "Sent welcome email to new contact",
            ),
            (
                "agent_action",
                "Scheduler",
                "book_appointment",
                "Booked appointment for customer",
            ),
            ("api_call", "OpenAI", "chat_completion", "Generated AI response"),
        ]

        for i, (act_type, source, action, desc) in enumerate(actions):
            activity = Activity(
                organization_id=org_id,
                type=ActivityType(act_type),
                source_type="agent" if "agent" in act_type else "workflow",
                source_name=source,
                action=action,
                description=desc,
                status=ActivityStatus.COMPLETED,
                duration_ms=random.randint(100, 2000),
                tokens_used=random.randint(100, 500),
                cost_usd=round(random.uniform(0.001, 0.05), 4),
                started_at=datetime.now(timezone.utc) - timedelta(hours=i),
                completed_at=datetime.now(timezone.utc)
                - timedelta(hours=i, minutes=-1),
            )
            self._activities.append(activity)
            self._trim_list(self._activities)

        # Sample approvals
        approvals = [
            (
                "Send promotional email to 500 customers",
                "communication",
                ApprovalPriority.HIGH,
            ),
            ("Update customer payment terms", "financial", ApprovalPriority.URGENT),
            ("Deploy new chatbot version", "deployment", ApprovalPriority.NORMAL),
        ]

        for title, category, priority in approvals:
            approval = ApprovalRequest(
                organization_id=org_id,
                title=title,
                description=f"AI agent requests approval to: {title}",
                category=category,
                requested_by_type="agent",
                requested_by_name="Marketing AI",
                priority=priority,
                action_type="execute",
                action_data={"target": "customers", "count": 500},
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            self._approvals[approval.id] = approval

        # Sample alerts
        alerts_data = [
            ("High API latency detected", AlertSeverity.WARNING, "performance"),
            ("Agent success rate dropped below 90%", AlertSeverity.WARNING, "agents"),
            ("Daily cost limit 80% reached", AlertSeverity.INFO, "cost"),
        ]

        for title, severity, category in alerts_data:
            alert = Alert(
                organization_id=org_id,
                title=title,
                message=f"Alert: {title}. Please review and take action if needed.",
                severity=severity,
                category=category,
            )
            self._alerts.append(alert)
            self._trim_list(self._alerts)

        # Initialize metrics with sample values
        self._init_sample_metrics(org_id)

    def _init_sample_metrics(self, org_id: str):
        """Initialize sample metric values."""
        metrics_data = {
            "agents_active": 5,
            "agents_actions_total": 1247,
            "agents_success_rate": 94.5,
            "workflows_active": 8,
            "workflows_executions_total": 3421,
            "workflows_success_rate": 98.2,
            "api_requests_total": 15789,
            "api_latency_avg": 245,
            "api_error_rate": 0.3,
            "tokens_used_total": 2450000,
            "cost_total": 127.45,
            "cost_daily": 12.50,
            "users_active": 23,
            "records_total": 4521,
            "storage_used": 2.4,
            "approvals_pending": 3,
            "approvals_avg_time": 15,
        }

        for metric_id, value in metrics_data.items():
            self._metrics[metric_id] = [MetricValue(metric_id=metric_id, value=value)]

    # ==========================================
    # Activity Tracking
    # ==========================================

    def log_activity(
        self,
        org_id: str,
        type: ActivityType,
        source_type: str,
        source_id: str,
        source_name: str,
        action: str,
        **kwargs,
    ) -> Activity:
        """Log an activity."""
        activity = Activity(
            organization_id=org_id,
            type=type,
            source_type=source_type,
            source_id=source_id,
            source_name=source_name,
            action=action,
            description=kwargs.get("description", ""),
            status=kwargs.get("status", ActivityStatus.COMPLETED),
            input_data=kwargs.get("input_data", {}),
            output_data=kwargs.get("output_data", {}),
            metadata=kwargs.get("metadata", {}),
            duration_ms=kwargs.get("duration_ms", 0),
            tokens_used=kwargs.get("tokens_used", 0),
            cost_usd=kwargs.get("cost_usd", 0.0),
            error=kwargs.get("error"),
        )
        activity.completed_at = datetime.now(timezone.utc)
        self._activities.append(activity)
        self._trim_list(self._activities)

        # Update metrics
        self._increment_metric(
            "agents_actions_total"
            if type == ActivityType.AGENT_ACTION
            else "workflows_executions_total"
        )
        if activity.tokens_used:
            self._increment_metric("tokens_used_total", activity.tokens_used)
        if activity.cost_usd:
            self._increment_metric("cost_total", activity.cost_usd)

        return activity

    def get_activities(
        self,
        org_id: str,
        type: Optional[ActivityType] = None,
        source_id: Optional[str] = None,
        status: Optional[ActivityStatus] = None,
        limit: int = 100,
    ) -> list[Activity]:
        """Get activities with filters."""
        activities = [a for a in self._activities if a.organization_id == org_id]
        if type:
            activities = [a for a in activities if a.type == type]
        if source_id:
            activities = [a for a in activities if a.source_id == source_id]
        if status:
            activities = [a for a in activities if a.status == status]
        return sorted(activities, key=lambda a: a.started_at, reverse=True)[:limit]

    def get_activity_summary(self, org_id: str, hours: int = 24) -> dict:
        """Get activity summary for time period."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        activities = [
            a
            for a in self._activities
            if a.organization_id == org_id and a.started_at >= cutoff
        ]

        total = len(activities)
        successful = len(
            [a for a in activities if a.status == ActivityStatus.COMPLETED]
        )
        failed = len([a for a in activities if a.status == ActivityStatus.FAILED])

        by_type = {}
        for a in activities:
            by_type[a.type.value] = by_type.get(a.type.value, 0) + 1

        total_tokens = sum(a.tokens_used for a in activities)
        total_cost = sum(a.cost_usd for a in activities)
        avg_duration = (
            sum(a.duration_ms for a in activities) / total if total > 0 else 0
        )

        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "successRate": successful / total * 100 if total > 0 else 100,
            "byType": by_type,
            "totalTokens": total_tokens,
            "totalCost": round(total_cost, 4),
            "avgDurationMs": round(avg_duration, 2),
            "periodHours": hours,
        }

    # ==========================================
    # Approvals
    # ==========================================

    def create_approval(
        self,
        org_id: str,
        title: str,
        category: str,
        requested_by_type: str,
        requested_by_id: str,
        requested_by_name: str,
        action_type: str,
        action_data: dict,
        **kwargs,
    ) -> ApprovalRequest:
        """Create an approval request."""
        approval = ApprovalRequest(
            organization_id=org_id,
            title=title,
            category=category,
            requested_by_type=requested_by_type,
            requested_by_id=requested_by_id,
            requested_by_name=requested_by_name,
            priority=kwargs.get("priority", ApprovalPriority.NORMAL),
            action_type=action_type,
            action_data=action_data,
            description=kwargs.get("description", ""),
            preview=kwargs.get("preview"),
            context=kwargs.get("context", {}),
            assigned_to=kwargs.get("assigned_to"),
            assigned_team=kwargs.get("assigned_team"),
            expires_at=kwargs.get("expires_at"),
            auto_approve_at=kwargs.get("auto_approve_at"),
        )
        self._approvals[approval.id] = approval

        if self._using_persistence and self._approval_repo:
            try:
                self._approval_repo.create(
                    {
                        "id": approval.id,
                        "organization_id": org_id,
                        "title": title,
                        "requested_by": requested_by_id,
                        "category": category,
                        "action_type": action_type,
                        "status": "pending",
                        "description": kwargs.get("description", ""),
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist approval %s: %s", approval.id, e)

        # Update pending count
        self._set_metric(
            "approvals_pending",
            len(
                [
                    a
                    for a in self._approvals.values()
                    if a.status == ApprovalStatus.PENDING
                ]
            ),
        )

        return approval

    def get_approval(self, approval_id: str) -> Optional[ApprovalRequest]:
        return self._approvals.get(approval_id)

    def list_approvals(
        self,
        org_id: str,
        status: Optional[ApprovalStatus] = None,
        assigned_to: Optional[str] = None,
        limit: int = 50,
    ) -> list[ApprovalRequest]:
        """List approval requests."""
        approvals = [a for a in self._approvals.values() if a.organization_id == org_id]
        if status:
            approvals = [a for a in approvals if a.status == status]
        if assigned_to:
            approvals = [a for a in approvals if a.assigned_to == assigned_to]
        return sorted(
            approvals, key=lambda a: (a.priority.value, a.created_at), reverse=True
        )[:limit]

    def approve(
        self, approval_id: str, user_id: str, note: str = ""
    ) -> Optional[ApprovalRequest]:
        """Approve a request."""
        approval = self._approvals.get(approval_id)
        if not approval or approval.status != ApprovalStatus.PENDING:
            return None

        approval.status = ApprovalStatus.APPROVED
        approval.responded_by = user_id
        approval.responded_at = datetime.now(timezone.utc)
        approval.response_note = note

        if self._approval_repo:
            try:
                self._approval_repo.update(
                    approval_id, {"status": approval.status.value}
                )
            except Exception as e:
                logger.warning("Approval repo update failed: %s", e)

        self._update_approval_metrics()
        return approval

    def reject(
        self, approval_id: str, user_id: str, note: str = ""
    ) -> Optional[ApprovalRequest]:
        """Reject a request."""
        approval = self._approvals.get(approval_id)
        if not approval or approval.status != ApprovalStatus.PENDING:
            return None

        approval.status = ApprovalStatus.REJECTED
        approval.responded_by = user_id
        approval.responded_at = datetime.now(timezone.utc)
        approval.response_note = note

        if self._approval_repo:
            try:
                self._approval_repo.update(
                    approval_id, {"status": approval.status.value}
                )
            except Exception as e:
                logger.warning("Approval repo update failed: %s", e)

        self._update_approval_metrics()
        return approval

    def _update_approval_metrics(self):
        pending = len(
            [a for a in self._approvals.values() if a.status == ApprovalStatus.PENDING]
        )
        self._set_metric("approvals_pending", pending)

        # Calculate avg approval time
        approved = [
            a
            for a in self._approvals.values()
            if a.status == ApprovalStatus.APPROVED and a.responded_at
        ]
        if approved:
            total_time = sum(
                (a.responded_at - a.created_at).total_seconds() / 60 for a in approved
            )
            avg_time = total_time / len(approved)
            self._set_metric("approvals_avg_time", round(avg_time, 1))

    # ==========================================
    # Alerts
    # ==========================================

    def create_alert(
        self,
        org_id: str,
        title: str,
        message: str,
        severity: AlertSeverity,
        category: str,
        **kwargs,
    ) -> Alert:
        """Create an alert."""
        alert = Alert(
            organization_id=org_id,
            title=title,
            message=message,
            severity=severity,
            category=category,
            source_type=kwargs.get("source_type", ""),
            source_id=kwargs.get("source_id", ""),
            data=kwargs.get("data", {}),
        )
        self._alerts.append(alert)
        self._trim_list(self._alerts)

        if self._using_persistence and self._alert_repo:
            try:
                self._alert_repo.create(
                    {
                        "id": alert.id,
                        "organization_id": org_id,
                        "title": title,
                        "severity": severity.value,
                        "message": message,
                        "category": category,
                        "data": kwargs.get("data", {}),
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist alert %s: %s", alert.id, e)

        return alert

    def get_alerts(
        self,
        org_id: str,
        severity: Optional[AlertSeverity] = None,
        is_read: Optional[bool] = None,
        is_resolved: Optional[bool] = None,
        limit: int = 50,
    ) -> list[Alert]:
        """Get alerts with filters."""
        alerts = [a for a in self._alerts if a.organization_id == org_id]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if is_read is not None:
            alerts = [a for a in alerts if a.is_read == is_read]
        if is_resolved is not None:
            alerts = [a for a in alerts if a.is_resolved == is_resolved]
        return sorted(alerts, key=lambda a: a.created_at, reverse=True)[:limit]

    def mark_alert_read(self, alert_id: str) -> bool:
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.is_read = True
                if self._alert_repo:
                    try:
                        self._alert_repo.update(alert.id, {"isRead": True})
                    except Exception as e:
                        logger.warning("Alert repo update failed: %s", e)
                return True
        return False

    def resolve_alert(self, alert_id: str, user_id: str) -> bool:
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.is_resolved = True
                alert.resolved_by = user_id
                alert.resolved_at = datetime.now(timezone.utc)
                if self._alert_repo:
                    try:
                        self._alert_repo.update(
                            alert.id, {"isResolved": True, "resolvedBy": user_id}
                        )
                    except Exception as e:
                        logger.warning("Alert repo update failed: %s", e)
                return True
        return False

    def get_alert_counts(self, org_id: str) -> dict:
        alerts = [
            a for a in self._alerts if a.organization_id == org_id and not a.is_resolved
        ]
        return {
            "total": len(alerts),
            "unread": len([a for a in alerts if not a.is_read]),
            "critical": len(
                [a for a in alerts if a.severity == AlertSeverity.CRITICAL]
            ),
            "error": len([a for a in alerts if a.severity == AlertSeverity.ERROR]),
            "warning": len([a for a in alerts if a.severity == AlertSeverity.WARNING]),
            "info": len([a for a in alerts if a.severity == AlertSeverity.INFO]),
        }

    # ==========================================
    # Metrics
    # ==========================================

    def get_metric_definitions(
        self, category: Optional[str] = None
    ) -> list[MetricDefinition]:
        """Get metric definitions."""
        metrics = list(self._metric_definitions.values())
        if category:
            metrics = [m for m in metrics if m.category == category]
        return metrics

    def get_metric(self, metric_id: str) -> Optional[float]:
        """Get current metric value."""
        values = self._metrics.get(metric_id, [])
        return values[-1].value if values else None

    def get_metrics(self, metric_ids: list[str] = None) -> dict[str, float]:
        """Get multiple metric values."""
        if metric_ids is None:
            metric_ids = list(self._metric_definitions.keys())
        return {mid: self.get_metric(mid) for mid in metric_ids if mid in self._metrics}

    def get_metric_history(self, metric_id: str, hours: int = 24) -> list[MetricValue]:
        """Get metric history."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        values = self._metrics.get(metric_id, [])
        return [v for v in values if v.timestamp >= cutoff]

    def _set_metric(self, metric_id: str, value: float):
        """Set metric value."""
        if metric_id not in self._metrics:
            self._metrics[metric_id] = []
        self._metrics[metric_id].append(MetricValue(metric_id=metric_id, value=value))

    def _increment_metric(self, metric_id: str, amount: float = 1):
        """Increment counter metric."""
        current = self.get_metric(metric_id) or 0
        self._set_metric(metric_id, current + amount)

    # ==========================================
    # Dashboard
    # ==========================================

    def get_dashboard_data(self, org_id: str) -> dict:
        """Get complete dashboard data."""
        return {
            "metrics": self.get_metrics(),
            "activitySummary": self.get_activity_summary(org_id, hours=24),
            "recentActivities": [
                a.to_dict() for a in self.get_activities(org_id, limit=10)
            ],
            "pendingApprovals": [
                a.to_dict()
                for a in self.list_approvals(
                    org_id, status=ApprovalStatus.PENDING, limit=5
                )
            ],
            "alerts": self.get_alert_counts(org_id),
            "recentAlerts": [
                a.to_dict() for a in self.get_alerts(org_id, is_resolved=False, limit=5)
            ],
        }

    def get_agent_performance(
        self, org_id: str, agent_id: Optional[str] = None
    ) -> dict:
        """Get agent performance metrics."""
        activities = self.get_activities(org_id, type=ActivityType.AGENT_ACTION)
        if agent_id:
            activities = [a for a in activities if a.source_id == agent_id]

        total = len(activities)
        successful = len(
            [a for a in activities if a.status == ActivityStatus.COMPLETED]
        )

        return {
            "totalActions": total,
            "successful": successful,
            "failed": total - successful,
            "successRate": successful / total * 100 if total > 0 else 100,
            "totalTokens": sum(a.tokens_used for a in activities),
            "totalCost": round(sum(a.cost_usd for a in activities), 4),
            "avgDurationMs": (
                round(sum(a.duration_ms for a in activities) / total, 2)
                if total > 0
                else 0
            ),
        }


# Singleton
_mission_control_service: Optional[MissionControlService] = None


def get_mission_control_service() -> MissionControlService:
    global _mission_control_service
    if _mission_control_service is None:
        _mission_control_service = MissionControlService()
    return _mission_control_service


__all__ = [
    "MissionControlService",
    "Activity",
    "ApprovalRequest",
    "Alert",
    "MetricDefinition",
    "MetricValue",
    "DashboardWidget",
    "ActivityType",
    "ActivityStatus",
    "ApprovalStatus",
    "ApprovalPriority",
    "AlertSeverity",
    "MetricType",
    "SYSTEM_METRICS",
    "get_mission_control_service",
]
