"""
Email Service
==============
Transactional emails with Resend.
Based on r/SaaS email best practices + DEV.to Postmark recommendations.

Features:
- Welcome emails
- Password reset
- Subscription notifications
- Usage alerts
- Team invitations
- Webhook tracking (opens, clicks, bounces)
- Email analytics integration
"""

import os
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
import httpx
import json
import hmac
import hashlib
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "AI App Builder <hello@aiappbuilder.com>")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@aiappbuilder.com")
APP_NAME = os.getenv("APP_NAME", "AI App Builder")
APP_URL = os.getenv("APP_URL", "https://aiappbuilder.com")

# Webhook secret for validating events
EMAIL_WEBHOOK_SECRET = os.getenv("EMAIL_WEBHOOK_SECRET", "")


# =============================================================================
# Email Types & Events
# =============================================================================


class EmailType(str, Enum):
    """Email template types."""

    WELCOME = "welcome"
    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"
    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_CANCELED = "subscription_canceled"
    PAYMENT_FAILED = "payment_failed"
    USAGE_ALERT = "usage_alert"
    TEAM_INVITATION = "team_invitation"
    PROJECT_SHARED = "project_shared"
    WEEKLY_DIGEST = "weekly_digest"


class EmailEvent(str, Enum):
    """Email webhook events (from Resend/Postmark)."""

    SENT = "email.sent"
    DELIVERED = "email.delivered"
    OPENED = "email.opened"
    CLICKED = "email.clicked"
    BOUNCED = "email.bounced"
    COMPLAINED = "email.complained"
    UNSUBSCRIBED = "email.unsubscribed"


@dataclass
class EmailRecipient:
    """Email recipient."""

    email: str
    name: Optional[str] = None


@dataclass
class EmailStats:
    """Email statistics for a message."""

    email_id: str
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None
    opens_count: int = 0
    clicks_count: int = 0
    links_clicked: List[str] = None

    def __post_init__(self):
        if self.links_clicked is None:
            self.links_clicked = []


# =============================================================================
# Email Templates
# =============================================================================


class EmailTemplates:
    """HTML email templates."""

    @staticmethod
    def _base_template(content: str, preheader: str = "") -> str:
        """Base HTML template."""
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{APP_NAME}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; }}
        .header {{ background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 30px; text-align: center; }}
        .header h1 {{ color: white; margin: 0; font-size: 24px; }}
        .content {{ padding: 40px 30px; }}
        .button {{ display: inline-block; background: #6366f1; color: white; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; margin: 20px 0; }}
        .button:hover {{ background: #4f46e5; }}
        .footer {{ padding: 30px; text-align: center; color: #666; font-size: 14px; border-top: 1px solid #eee; }}
        .footer a {{ color: #6366f1; }}
        .alert {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 20px 0; }}
        .success {{ background: #d1fae5; border-left: 4px solid #10b981; padding: 15px; margin: 20px 0; }}
        .preheader {{ display: none; max-height: 0; overflow: hidden; }}
    </style>
</head>
<body>
    <div class="preheader">{preheader}</div>
    <div class="container">
        <div class="header">
            <h1>🚀 {APP_NAME}</h1>
        </div>
        <div class="content">
            {content}
        </div>
        <div class="footer">
            <p>{APP_NAME} • <a href="{APP_URL}">Visit Website</a></p>
            <p>Questions? Contact <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a></p>
        </div>
    </div>
</body>
</html>
"""

    @staticmethod
    def welcome(name: str) -> tuple[str, str, str]:
        """Welcome email template."""
        subject = f"Welcome to {APP_NAME}! 🎉"
        preheader = "Start building amazing apps with AI"
        content = f"""
<h2>Welcome aboard, {name}! 👋</h2>

<p>We're thrilled to have you join {APP_NAME}. You now have access to the most powerful AI-powered app builder on the market.</p>

<div class="success">
    <strong>🎁 Your free credits:</strong> You've received <strong>100 free AI generation credits</strong> to get started!
</div>

<p><strong>Here's what you can do:</strong></p>
<ul>
    <li>✨ Describe your app idea in plain English</li>
    <li>🤖 Watch AI generate production-ready code</li>
    <li>📝 Edit and customize in our powerful editor</li>
    <li>🚀 Export to GitHub or deploy instantly</li>
</ul>

<p style="text-align: center;">
    <a href="{APP_URL}/projects/new" class="button">Create Your First Project →</a>
</p>

<p>Need help getting started? Check out our <a href="{APP_URL}/docs">documentation</a> or reply to this email.</p>

<p>Happy building! 🛠️</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def verify_email(name: str, verify_url: str) -> tuple[str, str, str]:
        """Email verification template."""
        subject = f"Verify your {APP_NAME} email"
        preheader = "Please verify your email address"
        content = f"""
<h2>Verify your email address</h2>

<p>Hi {name},</p>

<p>Thanks for signing up! Please verify your email address by clicking the button below:</p>

<p style="text-align: center;">
    <a href="{verify_url}" class="button">Verify Email →</a>
</p>

<p>This link will expire in 24 hours.</p>

<p>If you didn't create an account, you can safely ignore this email.</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def password_reset(name: str, reset_url: str) -> tuple[str, str, str]:
        """Password reset template."""
        subject = f"Reset your {APP_NAME} password"
        preheader = "Password reset request"
        content = f"""
<h2>Reset your password</h2>

<p>Hi {name},</p>

<p>We received a request to reset your password. Click the button below to create a new password:</p>

<p style="text-align: center;">
    <a href="{reset_url}" class="button">Reset Password →</a>
</p>

<p>This link will expire in 1 hour.</p>

<div class="alert">
    <strong>⚠️ Didn't request this?</strong><br>
    If you didn't request a password reset, please ignore this email or contact support if you're concerned.
</div>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def subscription_created(name: str, plan: str, amount: str) -> tuple[str, str, str]:
        """Subscription confirmation template."""
        subject = f"Welcome to {APP_NAME} {plan}! 🎉"
        preheader = f"Your {plan} subscription is now active"
        content = f"""
<h2>You're now on {plan}! 🚀</h2>

<p>Hi {name},</p>

<div class="success">
    <strong>✅ Subscription confirmed!</strong><br>
    Your {plan} plan ({amount}/month) is now active.
</div>

<p><strong>Your new benefits include:</strong></p>
<ul>
    <li>✨ Unlimited projects</li>
    <li>🤖 10,000+ AI generations per month</li>
    <li>🔗 GitHub integration</li>
    <li>⚡ Priority support</li>
    <li>🎨 All premium templates</li>
</ul>

<p style="text-align: center;">
    <a href="{APP_URL}/dashboard" class="button">Go to Dashboard →</a>
</p>

<p>Thank you for supporting {APP_NAME}!</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def subscription_canceled(name: str, end_date: str) -> tuple[str, str, str]:
        """Subscription cancellation template."""
        subject = f"Your {APP_NAME} subscription has been canceled"
        preheader = "We're sorry to see you go"
        content = f"""
<h2>Subscription canceled</h2>

<p>Hi {name},</p>

<p>Your subscription has been canceled. You'll continue to have access to your current plan until <strong>{end_date}</strong>.</p>

<p>After that date, your account will be downgraded to the Free plan.</p>

<p>We'd love to know why you canceled. Your feedback helps us improve:</p>

<p style="text-align: center;">
    <a href="{APP_URL}/feedback" class="button">Share Feedback →</a>
</p>

<p>Changed your mind? You can resubscribe anytime from your <a href="{APP_URL}/settings/billing">billing settings</a>.</p>

<p>Thank you for being a {APP_NAME} user! 💜</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def payment_failed(name: str, update_url: str) -> tuple[str, str, str]:
        """Payment failed template."""
        subject = f"⚠️ Payment failed for {APP_NAME}"
        preheader = "Please update your payment method"
        content = f"""
<h2>Payment failed</h2>

<p>Hi {name},</p>

<div class="alert">
    <strong>⚠️ We couldn't process your payment</strong><br>
    Please update your payment method to keep your subscription active.
</div>

<p>Your account will be downgraded to Free in 3 days if we can't process payment.</p>

<p style="text-align: center;">
    <a href="{update_url}" class="button">Update Payment Method →</a>
</p>

<p>If you believe this is an error, please contact our <a href="mailto:{SUPPORT_EMAIL}">support team</a>.</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def usage_alert(
        name: str, used: int, limit: int, percent: int
    ) -> tuple[str, str, str]:
        """Usage alert template."""
        subject = f"You've used {percent}% of your credits"
        preheader = f"{used} of {limit} credits used"
        content = f"""
<h2>Usage Alert</h2>

<p>Hi {name},</p>

<p>You've used <strong>{used}</strong> of your <strong>{limit}</strong> monthly AI credits ({percent}%).</p>

<div class="alert">
    <strong>Running low on credits?</strong><br>
    Purchase additional tokens or upgrade your plan for more generations.
</div>

<p style="text-align: center;">
    <a href="{APP_URL}/pricing" class="button">Get More Credits →</a>
</p>

<p>Your credits will reset at the beginning of your next billing cycle.</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)

    @staticmethod
    def team_invitation(
        inviter_name: str, team_name: str, invite_url: str
    ) -> tuple[str, str, str]:
        """Team invitation template."""
        subject = f"{inviter_name} invited you to join {team_name}"
        preheader = f"Join {team_name} on {APP_NAME}"
        content = f"""
<h2>You've been invited!</h2>

<p><strong>{inviter_name}</strong> has invited you to join <strong>{team_name}</strong> on {APP_NAME}.</p>

<p style="text-align: center;">
    <a href="{invite_url}" class="button">Accept Invitation →</a>
</p>

<p>This invitation will expire in 7 days.</p>

<p>Don't know {inviter_name}? You can safely ignore this email.</p>
"""
        return subject, preheader, EmailTemplates._base_template(content, preheader)


# =============================================================================
# Email Service
# =============================================================================


class EmailService:
    """
    Email service using Resend.

    Usage:
        email = EmailService()

        # Send welcome email
        await email.send_welcome("user@example.com", "John")

        # Send custom email
        await email.send(
            to="user@example.com",
            subject="Hello!",
            html="<p>Welcome!</p>"
        )
    """

    def __init__(self, api_key: str = RESEND_API_KEY):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        self.base_url = "https://api.resend.com"

    async def send(
        self,
        to: str | List[str],
        subject: str,
        html: str,
        from_email: str = FROM_EMAIL,
        reply_to: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        tags: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Send an email.

        Args:
            to: Recipient email(s)
            subject: Email subject
            html: HTML content
            from_email: From address
            reply_to: Reply-to address
            cc: CC recipients
            bcc: BCC recipients
            tags: Email tags for tracking

        Returns:
            Response dict with id or error
        """
        if not self.api_key:
            return {"error": "No API key configured"}

        payload = {
            "from": from_email,
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "html": html,
        }

        if reply_to:
            payload["reply_to"] = reply_to
        if cc:
            payload["cc"] = cc
        if bcc:
            payload["bcc"] = bcc
        if tags:
            payload["tags"] = tags

        try:
            response = await self.client.post(f"{self.base_url}/emails", json=payload)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    # -------------------------------------------------------------------------
    # Templated Emails
    # -------------------------------------------------------------------------

    async def send_welcome(self, to: str, name: str) -> Dict[str, Any]:
        """Send welcome email."""
        subject, _, html = EmailTemplates.welcome(name)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "welcome"}],
        )

    async def send_verify_email(
        self, to: str, name: str, verify_url: str
    ) -> Dict[str, Any]:
        """Send email verification."""
        subject, _, html = EmailTemplates.verify_email(name, verify_url)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "verification"}],
        )

    async def send_password_reset(
        self, to: str, name: str, reset_url: str
    ) -> Dict[str, Any]:
        """Send password reset email."""
        subject, _, html = EmailTemplates.password_reset(name, reset_url)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "password_reset"}],
        )

    async def send_subscription_created(
        self, to: str, name: str, plan: str, amount: str
    ) -> Dict[str, Any]:
        """Send subscription confirmation."""
        subject, _, html = EmailTemplates.subscription_created(name, plan, amount)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "billing"}],
        )

    async def send_subscription_canceled(
        self, to: str, name: str, end_date: str
    ) -> Dict[str, Any]:
        """Send subscription cancellation email."""
        subject, _, html = EmailTemplates.subscription_canceled(name, end_date)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "billing"}],
        )

    async def send_payment_failed(
        self, to: str, name: str, update_url: str
    ) -> Dict[str, Any]:
        """Send payment failed notification."""
        subject, _, html = EmailTemplates.payment_failed(name, update_url)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "billing"}],
        )

    async def send_usage_alert(
        self, to: str, name: str, used: int, limit: int
    ) -> Dict[str, Any]:
        """Send usage alert email."""
        percent = int((used / limit) * 100) if limit > 0 else 0
        subject, _, html = EmailTemplates.usage_alert(name, used, limit, percent)
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "usage"}],
        )

    async def send_team_invitation(
        self, to: str, inviter_name: str, team_name: str, invite_url: str
    ) -> Dict[str, Any]:
        """Send team invitation email."""
        subject, _, html = EmailTemplates.team_invitation(
            inviter_name, team_name, invite_url
        )
        return await self.send(
            to=to,
            subject=subject,
            html=html,
            tags=[{"name": "category", "value": "team"}],
        )

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# Email Webhook Handler (from DEV.to/Stack Overflow recommendations)
# =============================================================================


class EmailWebhookHandler:
    """
    Handle email webhooks for tracking opens, clicks, bounces.
    Based on DEV.to/Postmark recommendations.

    Usage:
        handler = EmailWebhookHandler()

        # In your FastAPI route:
        @app.post("/webhooks/email")
        async def handle_email_webhook(request: Request):
            return await handler.process_webhook(request)
    """

    def __init__(
        self,
        webhook_secret: str = EMAIL_WEBHOOK_SECRET,
        analytics_service=None,
        on_bounce: Optional[Callable] = None,
        on_complaint: Optional[Callable] = None,
    ):
        self.webhook_secret = webhook_secret
        self.analytics = analytics_service
        self.on_bounce = on_bounce
        self.on_complaint = on_complaint

        # In-memory stats (replace with database in production)
        self._email_stats: Dict[str, EmailStats] = {}

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature (Resend uses svix)."""
        if not self.webhook_secret:
            return True  # Skip verification if no secret

        try:
            expected = hmac.new(
                self.webhook_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        except Exception:
            return False

    async def process_webhook(self, request) -> Dict[str, Any]:
        """
        Process incoming email webhook.

        Returns event data for further processing.
        """
        from fastapi import HTTPException

        # Get payload
        body = await request.body()

        # Verify signature (if configured)
        signature = request.headers.get("svix-signature", "")
        if self.webhook_secret and not self.verify_signature(body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Parse event
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        event_type = data.get("type", "")
        event_data = data.get("data", {})
        email_id = event_data.get("email_id", "")

        # Update stats
        await self._update_stats(event_type, email_id, event_data)

        # Track in analytics
        if self.analytics:
            await self.analytics.track(
                f"email_{event_type.replace('.', '_')}",
                (
                    event_data.get("to", ["unknown"])[0]
                    if isinstance(event_data.get("to"), list)
                    else "unknown"
                ),
                {
                    "email_id": email_id,
                    "subject": event_data.get("subject", ""),
                    "event_type": event_type,
                },
            )

        # Handle specific events
        if event_type == EmailEvent.BOUNCED.value and self.on_bounce:
            await self.on_bounce(event_data)

        if event_type == EmailEvent.COMPLAINED.value and self.on_complaint:
            await self.on_complaint(event_data)

        return {"status": "processed", "event": event_type}

    async def _update_stats(self, event_type: str, email_id: str, data: Dict):
        """Update email statistics."""
        if not email_id:
            return

        if email_id not in self._email_stats:
            self._email_stats[email_id] = EmailStats(email_id=email_id)

        stats = self._email_stats[email_id]
        now = datetime.now(timezone.utc)

        if event_type == EmailEvent.SENT.value:
            stats.sent_at = now
        elif event_type == EmailEvent.DELIVERED.value:
            stats.delivered_at = now
        elif event_type == EmailEvent.OPENED.value:
            stats.opened_at = stats.opened_at or now
            stats.opens_count += 1
        elif event_type == EmailEvent.CLICKED.value:
            stats.clicked_at = stats.clicked_at or now
            stats.clicks_count += 1
            link = data.get("click", {}).get("link", "")
            if link and link not in stats.links_clicked:
                stats.links_clicked.append(link)

    def get_stats(self, email_id: str) -> Optional[EmailStats]:
        """Get stats for an email."""
        return self._email_stats.get(email_id)

    def get_all_stats(self) -> List[EmailStats]:
        """Get all email stats."""
        return list(self._email_stats.values())

    def get_delivery_rate(self) -> float:
        """Calculate overall delivery rate."""
        total = len(self._email_stats)
        if total == 0:
            return 0.0

        delivered = sum(1 for s in self._email_stats.values() if s.delivered_at)
        return delivered / total

    def get_open_rate(self) -> float:
        """Calculate overall open rate."""
        delivered = sum(1 for s in self._email_stats.values() if s.delivered_at)
        if delivered == 0:
            return 0.0

        opened = sum(1 for s in self._email_stats.values() if s.opened_at)
        return opened / delivered

    def get_click_rate(self) -> float:
        """Calculate overall click rate."""
        opened = sum(1 for s in self._email_stats.values() if s.opened_at)
        if opened == 0:
            return 0.0

        clicked = sum(1 for s in self._email_stats.values() if s.clicked_at)
        return clicked / opened


# =============================================================================
# Email Alert Service (from Stack Overflow - Azure Log Analytics pattern)
# =============================================================================


class EmailAlertService:
    """
    Send email alerts based on monitoring events.
    Based on Stack Overflow Azure Log Analytics email alert pattern.

    Usage:
        alerts = EmailAlertService(email_service)

        # Alert on error
        await alerts.alert_on_error(error, user_id="user_123")

        # Alert on threshold
        await alerts.alert_on_threshold("tokens_used", 9000, 10000)
    """

    def __init__(self, email_service: EmailService, admin_emails: List[str] = None):
        self.email = email_service
        self.admin_emails = admin_emails or [SUPPORT_EMAIL]
        self._alert_cooldowns: Dict[str, datetime] = {}

    def _can_send_alert(self, alert_key: str, cooldown_minutes: int = 15) -> bool:
        """Check if we can send an alert (rate limiting)."""
        last_sent = self._alert_cooldowns.get(alert_key)
        if not last_sent:
            return True

        from datetime import timedelta

        return datetime.now(timezone.utc) - last_sent > timedelta(
            minutes=cooldown_minutes
        )

    async def alert_on_error(
        self,
        error: Exception,
        user_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        cooldown_minutes: int = 15,
    ):
        """Send alert when an error occurs."""
        import traceback

        alert_key = f"error_{type(error).__name__}"
        if not self._can_send_alert(alert_key, cooldown_minutes):
            return

        self._alert_cooldowns[alert_key] = datetime.now(timezone.utc)

        html = f"""
        <div style="font-family: sans-serif; padding: 20px; background: #fee2e2; border-radius: 8px;">
            <h2 style="color: #dc2626;">⚠️ Error Alert</h2>
            <p><strong>Type:</strong> {type(error).__name__}</p>
            <p><strong>Message:</strong> {str(error)}</p>
            <p><strong>User:</strong> {user_id or 'N/A'}</p>
            <p><strong>Time:</strong> {datetime.now(timezone.utc).isoformat()}</p>
            {f'<p><strong>Context:</strong> {json.dumps(context)}</p>' if context else ''}
            <details>
                <summary>Stack Trace</summary>
                <pre style="background: #1f2937; color: #f3f4f6; padding: 10px; border-radius: 4px; overflow-x: auto;">{traceback.format_exc()}</pre>
            </details>
        </div>
        """

        for admin_email in self.admin_emails:
            await self.email.send(
                to=admin_email,
                subject=f"[{APP_NAME}] Error: {type(error).__name__}",
                html=html,
                tags=[{"name": "category", "value": "alert"}],
            )

    async def alert_on_threshold(
        self,
        metric_name: str,
        current_value: float,
        threshold: float,
        unit: str = "",
        cooldown_minutes: int = 60,
    ):
        """Send alert when a threshold is exceeded."""
        alert_key = f"threshold_{metric_name}"
        if not self._can_send_alert(alert_key, cooldown_minutes):
            return

        self._alert_cooldowns[alert_key] = datetime.now(timezone.utc)

        percent = int((current_value / threshold) * 100)

        html = f"""
        <div style="font-family: sans-serif; padding: 20px; background: #fef3c7; border-radius: 8px;">
            <h2 style="color: #d97706;">📊 Threshold Alert</h2>
            <p><strong>Metric:</strong> {metric_name}</p>
            <p><strong>Current:</strong> {current_value:,.0f} {unit}</p>
            <p><strong>Threshold:</strong> {threshold:,.0f} {unit}</p>
            <p><strong>Usage:</strong> {percent}%</p>
            <div style="background: #e5e7eb; border-radius: 4px; height: 20px; overflow: hidden;">
                <div style="background: {'#dc2626' if percent >= 100 else '#f59e0b'}; height: 100%; width: {min(percent, 100)}%;"></div>
            </div>
        </div>
        """

        for admin_email in self.admin_emails:
            await self.email.send(
                to=admin_email,
                subject=f"[{APP_NAME}] {metric_name} at {percent}%",
                html=html,
                tags=[{"name": "category", "value": "alert"}],
            )

    async def alert_payment_issue(
        self,
        user_email: str,
        user_name: str,
        issue: str,
        amount: Optional[float] = None,
    ):
        """Alert admins about payment issues."""
        html = f"""
        <div style="font-family: sans-serif; padding: 20px; background: #fce7f3; border-radius: 8px;">
            <h2 style="color: #be185d;">💳 Payment Issue</h2>
            <p><strong>User:</strong> {user_name} ({user_email})</p>
            <p><strong>Issue:</strong> {issue}</p>
            {f'<p><strong>Amount:</strong> ${amount:.2f}</p>' if amount else ''}
            <p><strong>Time:</strong> {datetime.now(timezone.utc).isoformat()}</p>
        </div>
        """

        for admin_email in self.admin_emails:
            await self.email.send(
                to=admin_email,
                subject=f"[{APP_NAME}] Payment Issue - {user_email}",
                html=html,
                tags=[{"name": "category", "value": "billing_alert"}],
            )


# =============================================================================
# Singleton Instance
# =============================================================================

_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service


async def shutdown_email_service():
    """Shutdown email service."""
    global _email_service
    if _email_service:
        await _email_service.close()
        _email_service = None
