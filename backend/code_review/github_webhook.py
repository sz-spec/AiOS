"""
GitHub Webhook Handler for Automatic Code Reviews

Based on forum recommendations (Shakudo, Reddit - Dec 2025):
- Webhook-triggered automatic reviews on PR events
- Posts inline comments and summary
- Supports GitHub Actions integration
- Logs all reviews

Events handled:
- pull_request.opened
- pull_request.synchronize
- pull_request.reopened
- push (for branch protection)
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from .multi_provider import (
    MultiProviderReviewService,
    ReviewContext,
    ReviewProvider,
    ReviewStorage,
    StoredReview,
)

router = APIRouter(prefix="/webhooks", tags=["GitHub Webhooks"])

# Configuration
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY")

# Service instances
review_service = MultiProviderReviewService()
storage: Optional[ReviewStorage] = None


# ============================================
# Models
# ============================================


class WebhookPayload(BaseModel):
    """GitHub webhook payload."""

    action: Optional[str] = None
    pull_request: Optional[dict] = None
    repository: dict
    sender: dict
    installation: Optional[dict] = None


class ReviewConfig(BaseModel):
    """Configuration for automatic review."""

    enabled: bool = True
    provider: ReviewProvider = ReviewProvider.CLAUDE
    auto_comment: bool = True
    auto_approve: bool = False  # Auto-approve if no critical issues
    min_score_for_approve: int = 80
    review_on_draft: bool = False
    excluded_paths: list[str] = []
    custom_rules: list[str] = []


# Default config per repo (can be stored in DB)
DEFAULT_CONFIG = ReviewConfig()


# ============================================
# Webhook Verification
# ============================================


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature."""
    if not GITHUB_WEBHOOK_SECRET:
        return True  # Skip verification if no secret set

    expected = (
        "sha256="
        + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature)


async def get_github_token(installation_id: Optional[int] = None) -> str:
    """Get GitHub token (either PAT or GitHub App installation token)."""
    if GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY and installation_id:
        # Generate installation access token for GitHub App
        return await _get_installation_token(installation_id)
    return GITHUB_TOKEN


async def _get_installation_token(installation_id: int) -> str:
    """Get installation access token for GitHub App."""
    import jwt
    import time

    # Generate JWT
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": GITHUB_APP_ID,
    }

    jwt_token = jwt.encode(
        payload,
        GITHUB_APP_PRIVATE_KEY,
        algorithm="RS256",
    )

    # Exchange for installation token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
        )

        if response.status_code == 201:
            return response.json()["token"]
        raise HTTPException(500, "Failed to get installation token")


# ============================================
# GitHub API Helpers
# ============================================


class GitHubClient:
    """GitHub API client for posting reviews."""

    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get PR diff content."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={**self.headers, "Accept": "application/vnd.github.v3.diff"},
            )
            return response.text if response.status_code == 200 else ""

    async def get_pr_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict]:
        """Get changed files in PR."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self.headers,
            )
            return response.json() if response.status_code == 200 else []

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
    ) -> str:
        """Get file content from repository."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref},
                headers=self.headers,
            )

            if response.status_code == 200:
                import base64

                data = response.json()
                return base64.b64decode(data["content"]).decode("utf-8")
            return ""

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ):
        """Post comment on PR."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers=self.headers,
                json={"body": body},
            )

    async def create_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        event: str,  # APPROVE, REQUEST_CHANGES, COMMENT
        comments: list[dict],
    ):
        """Create PR review with inline comments."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers=self.headers,
                json={
                    "commit_id": commit_sha,
                    "body": body,
                    "event": event,
                    "comments": comments[:50],  # GitHub limit
                },
            )

    async def post_check_run(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        name: str,
        status: str,
        conclusion: Optional[str],
        output: dict,
    ):
        """Post check run status."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/repos/{owner}/{repo}/check-runs",
                headers=self.headers,
                json={
                    "name": name,
                    "head_sha": head_sha,
                    "status": status,
                    "conclusion": conclusion,
                    "output": output,
                },
            )


# ============================================
# Review Formatting
# ============================================


def format_review_summary(
    findings: list[dict],
    score: int,
    files_count: int,
    provider: str,
) -> str:
    """Format review summary as markdown."""
    # Severity icons
    icons = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🔵",
        "info": "⚪",
    }

    # Count by severity
    by_severity = {}
    for f in findings:
        sev = f.get("severity", "info")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    # Build summary
    body = "## 🤖 AI Code Review\n\n"
    body += f"**Score:** {score}/100 "
    if score >= 80:
        body += "✅\n"
    elif score >= 60:
        body += "⚠️\n"
    else:
        body += "❌\n"

    body += f"**Provider:** {provider}\n"
    body += f"**Files Reviewed:** {files_count}\n"
    body += f"**Issues Found:** {len(findings)}\n\n"

    # Severity table
    if by_severity:
        body += "### Issues by Severity\n\n"
        body += "| Severity | Count |\n|----------|-------|\n"
        for sev in ["critical", "high", "medium", "low", "info"]:
            count = by_severity.get(sev, 0)
            if count > 0:
                body += f"| {icons.get(sev, '⚪')} {sev.title()} | {count} |\n"
        body += "\n"

    # Top findings
    critical_high = [f for f in findings if f.get("severity") in ["critical", "high"]]
    if critical_high:
        body += "### Critical/High Priority Issues\n\n"
        for f in critical_high[:5]:
            body += f"#### {icons.get(f.get('severity', 'info'), '⚪')} {f.get('title', 'Issue')}\n"
            body += f"📁 `{f.get('file', 'unknown')}:{f.get('line', '?')}`\n\n"
            body += f"{f.get('description', '')}\n\n"
            if f.get("suggestion"):
                body += f"**💡 Suggestion:** {f.get('suggestion')}\n\n"

    # Footer
    body += "---\n"
    body += "*Powered by AI App Builder Code Review*\n"

    return body


def format_inline_comment(finding: dict) -> dict:
    """Format finding as inline PR comment."""
    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
    sev = finding.get("severity", "info")

    body = (
        f"**{icons.get(sev, '⚪')} {sev.upper()}**: {finding.get('title', 'Issue')}\n\n"
    )
    body += f"{finding.get('description', '')}\n"

    if finding.get("suggestion"):
        body += f"\n**💡 Suggestion:** {finding.get('suggestion')}\n"

    if finding.get("code_fix"):
        body += f"\n```suggestion\n{finding.get('code_fix')}\n```"

    return {
        "path": finding.get("file", ""),
        "line": finding.get("line") or finding.get("end_line") or 1,
        "body": body,
    }


def calculate_score(findings: list[dict]) -> int:
    """Calculate review score from findings."""
    if not findings:
        return 100

    weights = {"critical": 25, "high": 15, "medium": 8, "low": 3, "info": 1}
    deduction = sum(weights.get(f.get("severity", "info"), 1) for f in findings)
    return max(0, 100 - deduction)


# ============================================
# Webhook Handlers
# ============================================


@router.post("/github")
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None),
):
    """
    Handle GitHub webhook events.

    Supported events:
    - pull_request: Trigger code review on PR
    - push: Optional review on push to protected branches
    """
    # Verify signature
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256 or ""):
        raise HTTPException(401, "Invalid signature")

    payload = json.loads(body)

    # Handle different event types
    if x_github_event == "pull_request":
        action = payload.get("action")
        if action in ["opened", "synchronize", "reopened"]:
            # Schedule background review
            background_tasks.add_task(
                handle_pr_review,
                payload,
            )
            return {"status": "review_scheduled"}
        return {"status": "action_ignored", "action": action}

    elif x_github_event == "push":
        # Optional: Review pushes to main/develop
        ref = payload.get("ref", "")
        if ref in ["refs/heads/main", "refs/heads/develop"]:
            background_tasks.add_task(
                handle_push_review,
                payload,
            )
            return {"status": "push_review_scheduled"}
        return {"status": "branch_ignored"}

    elif x_github_event == "ping":
        return {"status": "pong"}

    return {"status": "event_ignored", "event": x_github_event}


async def handle_pr_review(payload: dict):
    """Handle pull request review."""
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    installation = payload.get("installation", {})

    # Get configuration
    config = DEFAULT_CONFIG  # Could load from DB per repo

    # Skip draft PRs if configured
    if pr.get("draft") and not config.review_on_draft:
        return

    # Get GitHub token
    token = await get_github_token(installation.get("id"))
    github = GitHubClient(token)

    # Extract info
    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    pr_number = pr.get("number")
    head_sha = pr.get("head", {}).get("sha", "")
    head_ref = pr.get("head", {}).get("ref", "")
    base_ref = pr.get("base", {}).get("ref", "main")

    try:
        # Post "in progress" status
        await github.post_check_run(
            owner=owner,
            repo=repo_name,
            head_sha=head_sha,
            name="AI Code Review",
            status="in_progress",
            conclusion=None,
            output={
                "title": "AI Code Review",
                "summary": "Analyzing code changes...",
            },
        )

        # Get PR files
        pr_files = await github.get_pr_files(owner, repo_name, pr_number)

        # Filter files
        files_to_review = {}
        for file in pr_files:
            path = file.get("filename", "")

            # Skip excluded paths
            if any(path.startswith(ex) for ex in config.excluded_paths):
                continue

            # Only review code files
            if not any(
                path.endswith(ext)
                for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"]
            ):
                continue

            # Get file content
            content = await github.get_file_content(owner, repo_name, path, head_ref)
            if content:
                files_to_review[path] = content

        if not files_to_review:
            await github.post_check_run(
                owner=owner,
                repo=repo_name,
                head_sha=head_sha,
                name="AI Code Review",
                status="completed",
                conclusion="neutral",
                output={
                    "title": "AI Code Review",
                    "summary": "No reviewable files found.",
                },
            )
            return

        # Get diff for context
        diff = await github.get_pr_diff(owner, repo_name, pr_number)

        # Create review context
        context = ReviewContext(
            project_id=f"github:{owner}/{repo_name}",
            files=files_to_review,
            diff=diff,
            pr_number=pr_number,
            pr_url=pr.get("html_url"),
            base_branch=base_ref,
            head_branch=head_ref,
            custom_rules=config.custom_rules,
        )

        # Run review
        result = await review_service.review(context, config.provider)
        findings = result.get("findings", [])
        score = calculate_score(findings)

        # Store review
        if storage:
            await storage.store_review(
                StoredReview(
                    id=hashlib.md5(
                        f"{owner}/{repo_name}:{pr_number}:{head_sha}".encode()
                    ).hexdigest()[:16],
                    project_id=context.project_id,
                    provider=config.provider.value,
                    files_reviewed=list(files_to_review.keys()),
                    findings=findings,
                    score=score,
                    created_at=datetime.now(timezone.utc),
                    pr_number=pr_number,
                    pr_url=pr.get("html_url"),
                    metadata={"head_sha": head_sha},
                )
            )

        # Format and post review
        if config.auto_comment:
            summary = format_review_summary(
                findings=findings,
                score=score,
                files_count=len(files_to_review),
                provider=result.get("provider", config.provider.value),
            )

            # Prepare inline comments for critical/high issues
            inline_comments = []
            for f in findings:
                if (
                    f.get("severity") in ["critical", "high"]
                    and f.get("file")
                    and f.get("line")
                ):
                    inline_comments.append(format_inline_comment(f))

            # Determine review event
            event = "COMMENT"
            if config.auto_approve and score >= config.min_score_for_approve:
                event = "APPROVE"
            elif any(f.get("severity") == "critical" for f in findings):
                event = "REQUEST_CHANGES"

            # Create review
            await github.create_pr_review(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                commit_sha=head_sha,
                body=summary,
                event=event,
                comments=inline_comments,
            )

        # Update check run
        conclusion = "success" if score >= 60 else "failure"
        critical_count = sum(1 for f in findings if f.get("severity") == "critical")

        await github.post_check_run(
            owner=owner,
            repo=repo_name,
            head_sha=head_sha,
            name="AI Code Review",
            status="completed",
            conclusion=conclusion,
            output={
                "title": f"AI Code Review - Score: {score}/100",
                "summary": f"Found {len(findings)} issues ({critical_count} critical)",
                "text": format_review_summary(
                    findings, score, len(files_to_review), config.provider.value
                ),
            },
        )

    except Exception as e:
        # Post failure status
        await github.post_check_run(
            owner=owner,
            repo=repo_name,
            head_sha=head_sha,
            name="AI Code Review",
            status="completed",
            conclusion="failure",
            output={
                "title": "AI Code Review Failed",
                "summary": f"Error: {str(e)}",
            },
        )


async def handle_push_review(payload: dict):
    """Handle push event review (optional)."""
    # Similar to PR review but for direct pushes
    pass


# ============================================
# Manual Trigger Endpoint
# ============================================


class ManualReviewRequest(BaseModel):
    """Request for manual PR review."""

    owner: str
    repo: str
    pr_number: int
    provider: ReviewProvider = ReviewProvider.CLAUDE


@router.post("/github/review")
async def trigger_manual_review(
    request: ManualReviewRequest,
    background_tasks: BackgroundTasks,
):
    """Manually trigger a PR review."""
    # Create mock payload
    payload = {
        "action": "manual",
        "pull_request": {"number": request.pr_number},
        "repository": {
            "name": request.repo,
            "owner": {"login": request.owner},
        },
    }

    background_tasks.add_task(handle_pr_review, payload)
    return {"status": "review_triggered"}


# ============================================
# Status Endpoint
# ============================================


@router.get("/github/status")
async def webhook_status():
    """Get webhook handler status."""
    providers = await review_service.get_available_providers()

    return {
        "status": "healthy",
        "webhook_secret_configured": bool(GITHUB_WEBHOOK_SECRET),
        "github_token_configured": bool(GITHUB_TOKEN),
        "github_app_configured": bool(GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY),
        "storage_configured": storage is not None,
        "available_providers": [p.value for p in providers],
    }
