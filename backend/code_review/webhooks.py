"""
GitHub Webhook Handler for Automatic PR Reviews

Based on Forum Recommendations (Dec 2025):
- Auto-trigger AI review on PR open/update
- Support for multiple events
- Queue management for rate limiting

Events Handled:
- pull_request.opened
- pull_request.synchronize
- pull_request.reopened
- pull_request_review.submitted
"""

import hashlib
import hmac
import json
import logging
from enum import Enum
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from .service import AICodeReviewService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])


# ============================================
# Configuration
# ============================================


class WebhookConfig:
    """Webhook configuration from environment."""

    GITHUB_WEBHOOK_SECRET: str = ""
    AUTO_REVIEW_ENABLED: bool = True
    POST_COMMENTS: bool = True
    MIN_FILES_FOR_REVIEW: int = 1
    MAX_FILES_FOR_REVIEW: int = 50
    SKIP_DRAFT_PRS: bool = True
    SKIP_BOT_PRS: bool = True
    REVIEW_LABELS: list[str] = ["needs-review", "ai-review"]


config = WebhookConfig()


# ============================================
# Models
# ============================================


class PREvent(str, Enum):
    OPENED = "opened"
    SYNCHRONIZE = "synchronize"
    REOPENED = "reopened"
    READY_FOR_REVIEW = "ready_for_review"
    LABELED = "labeled"


class WebhookPayload(BaseModel):
    """GitHub webhook payload for PR events."""

    action: str
    number: int
    pull_request: dict
    repository: dict
    sender: dict
    installation: Optional[dict] = None


class ReviewTriggerResult(BaseModel):
    """Result of webhook processing."""

    triggered: bool
    reason: str
    pr_number: Optional[int] = None
    review_id: Optional[str] = None


# ============================================
# Webhook Verification
# ============================================


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not signature or not signature.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    actual = signature.replace("sha256=", "")
    return hmac.compare_digest(expected, actual)


# ============================================
# Review Logic
# ============================================


def should_review_pr(payload: WebhookPayload) -> tuple[bool, str]:
    """Determine if PR should be reviewed."""
    pr = payload.pull_request
    action = payload.action

    # Check if auto-review is enabled
    if not config.AUTO_REVIEW_ENABLED:
        return False, "Auto-review disabled"

    # Check action type
    valid_actions = [
        PREvent.OPENED.value,
        PREvent.SYNCHRONIZE.value,
        PREvent.REOPENED.value,
        PREvent.READY_FOR_REVIEW.value,
    ]
    if action not in valid_actions:
        return False, f"Action '{action}' does not trigger review"

    # Skip draft PRs
    if config.SKIP_DRAFT_PRS and pr.get("draft", False):
        return False, "Draft PR - skipping"

    # Skip bot PRs
    if config.SKIP_BOT_PRS:
        user_type = pr.get("user", {}).get("type", "")
        if user_type == "Bot":
            return False, "Bot PR - skipping"

    # Check changed files count
    changed_files = pr.get("changed_files", 0)
    if changed_files < config.MIN_FILES_FOR_REVIEW:
        return False, f"Too few files ({changed_files})"
    if changed_files > config.MAX_FILES_FOR_REVIEW:
        return False, f"Too many files ({changed_files})"

    # Check for skip labels
    labels = [l.get("name", "") for l in pr.get("labels", [])]
    if "skip-ai-review" in labels:
        return False, "Has 'skip-ai-review' label"

    return True, "PR eligible for review"


async def trigger_review(payload: WebhookPayload, service: AICodeReviewService) -> str:
    """Trigger AI review for a PR."""
    repo = payload.repository

    owner = repo["owner"]["login"]
    repo_name = repo["name"]
    pr_number = payload.number

    logger.info(f"Starting AI review for {owner}/{repo_name}#{pr_number}")

    # Run the review
    result = await service.review_github_pr_full(
        owner=owner,
        repo=repo_name,
        pr_number=pr_number,
        post_comments=config.POST_COMMENTS,
    )

    logger.info(
        f"Review completed for {owner}/{repo_name}#{pr_number}: "
        f"score={result.summary.score}, findings={result.summary.total_findings}"
    )

    return result.id


# ============================================
# API Endpoints
# ============================================


@router.post("/github", response_model=ReviewTriggerResult)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
):
    """
    Handle GitHub webhook events.

    Supported events:
    - pull_request: Triggers AI review on open/update
    """
    # Get raw payload for signature verification
    payload_bytes = await request.body()

    # Verify signature
    if config.GITHUB_WEBHOOK_SECRET:
        if not verify_github_signature(
            payload_bytes, x_hub_signature_256 or "", config.GITHUB_WEBHOOK_SECRET
        ):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        payload_dict = json.loads(payload_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Handle different event types
    if x_github_event == "pull_request":
        return await handle_pull_request_event(
            payload_dict,
            background_tasks,
            x_github_delivery,
        )

    elif x_github_event == "pull_request_review":
        return await handle_review_event(payload_dict)

    elif x_github_event == "ping":
        return ReviewTriggerResult(
            triggered=False,
            reason="Webhook ping received successfully",
        )

    else:
        return ReviewTriggerResult(
            triggered=False,
            reason=f"Event type '{x_github_event}' not handled",
        )


async def handle_pull_request_event(
    payload_dict: dict,
    background_tasks: BackgroundTasks,
    delivery_id: Optional[str],
) -> ReviewTriggerResult:
    """Handle pull_request webhook event."""
    try:
        payload = WebhookPayload(**payload_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Check if we should review
    should_review, reason = should_review_pr(payload)

    if not should_review:
        logger.info(f"Skipping PR #{payload.number}: {reason}")
        return ReviewTriggerResult(
            triggered=False,
            reason=reason,
            pr_number=payload.number,
        )

    # Get review service
    service = AICodeReviewService()

    # Schedule review in background
    background_tasks.add_task(
        trigger_review,
        payload,
        service,
    )

    logger.info(f"Scheduled review for PR #{payload.number}")

    return ReviewTriggerResult(
        triggered=True,
        reason="AI review scheduled",
        pr_number=payload.number,
    )


async def handle_review_event(payload_dict: dict) -> ReviewTriggerResult:
    """Handle pull_request_review event."""
    # Could trigger re-review after human review
    action = payload_dict.get("action")

    if action == "submitted":
        review = payload_dict.get("review", {})
        state = review.get("state")

        if state == "changes_requested":
            # Could trigger additional AI suggestions
            return ReviewTriggerResult(
                triggered=False,
                reason="Changes requested - could trigger AI suggestions",
            )

    return ReviewTriggerResult(
        triggered=False,
        reason=f"Review action '{action}' not handled",
    )


# ============================================
# Webhook Management
# ============================================


@router.get("/github/status")
async def webhook_status():
    """Get webhook configuration status."""
    return {
        "auto_review_enabled": config.AUTO_REVIEW_ENABLED,
        "post_comments": config.POST_COMMENTS,
        "skip_draft_prs": config.SKIP_DRAFT_PRS,
        "skip_bot_prs": config.SKIP_BOT_PRS,
        "min_files": config.MIN_FILES_FOR_REVIEW,
        "max_files": config.MAX_FILES_FOR_REVIEW,
        "secret_configured": bool(config.GITHUB_WEBHOOK_SECRET),
    }


@router.post("/github/test")
async def test_webhook(
    owner: str,
    repo: str,
    pr_number: int,
    background_tasks: BackgroundTasks,
):
    """Manually trigger a review (for testing)."""
    service = AICodeReviewService()

    background_tasks.add_task(
        service.review_github_pr_full,
        owner,
        repo,
        pr_number,
        True,  # post_comments
    )

    return {
        "message": f"Review triggered for {owner}/{repo}#{pr_number}",
        "status": "scheduled",
    }
