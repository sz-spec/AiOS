"""
Code Review API Routes

REST endpoints for AI-powered code review:
- POST /review - Review code files
- POST /review/diff - Review git diff
- GET /review/:id - Get review result
- POST /review/:id/fix - Apply auto-fix
- GET /review/history - Review history
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .service import AICodeReviewService, CodeReviewResult, ReviewSeverity

router = APIRouter(prefix="/review", tags=["Code Review"])

# Service instance (singleton)
_review_service: Optional[AICodeReviewService] = None

# In-memory storage for demo (use Redis/DB in production)
_review_history: dict[str, CodeReviewResult] = {}


def get_review_service() -> AICodeReviewService:
    """Get or create review service."""
    global _review_service
    if _review_service is None:
        _review_service = AICodeReviewService()
    return _review_service


# ============================================
# Request/Response Models
# ============================================


class ReviewRequest(BaseModel):
    """Request to review code files."""

    project_id: str = Field(..., description="Project identifier")
    files: dict[str, str] = Field(..., description="Map of filename to code content")
    context: Optional[dict] = Field(
        None, description="Optional context (framework, etc.)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "proj_123",
                "files": {
                    "src/components/Button.tsx": "export function Button() { ... }",
                    "src/hooks/useAuth.ts": "export function useAuth() { ... }",
                },
                "context": {"framework": "React", "standards": ["airbnb"]},
            }
        }


class DiffReviewRequest(BaseModel):
    """Request to review a git diff."""

    project_id: str
    diff: str = Field(..., description="Git diff content")
    base_branch: Optional[str] = Field("main", description="Base branch name")
    context: Optional[dict] = None


class FixRequest(BaseModel):
    """Request to apply a fix."""

    finding_id: str
    file_content: str = Field(..., description="Current file content")


class ReviewResponse(BaseModel):
    """Code review response."""

    id: str
    project_id: str
    files_reviewed: list[str]
    findings: list[dict]
    summary: dict
    created_at: str
    model: str


class ReviewHistoryItem(BaseModel):
    """Item in review history."""

    id: str
    project_id: str
    files_count: int
    findings_count: int
    score: int
    created_at: str


class ReviewStats(BaseModel):
    """Review statistics."""

    total_reviews: int
    total_findings: int
    avg_score: float
    findings_by_severity: dict[str, int]
    findings_by_category: dict[str, int]
    top_issues: list[dict]


# ============================================
# API Endpoints
# ============================================


@router.post("", response_model=ReviewResponse)
async def review_code(
    request: ReviewRequest,
    background_tasks: BackgroundTasks,
    service: AICodeReviewService = Depends(get_review_service),
):
    """
    Review code files for issues.

    Analyzes provided code for:
    - Security vulnerabilities
    - Performance issues
    - Bugs and errors
    - Code style
    - Best practices

    Returns detailed findings with suggestions.
    """
    if not request.files:
        raise HTTPException(status_code=400, detail="No files provided")

    if len(request.files) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per review")

    # Run review
    result = await service.review_code(
        project_id=request.project_id,
        files=request.files,
        context=request.context,
    )

    # Store in history
    _review_history[result.id] = result

    # Background: store in database
    background_tasks.add_task(_store_review, result)

    return ReviewResponse(
        id=result.id,
        project_id=result.project_id,
        files_reviewed=result.files_reviewed,
        findings=[f.to_dict() for f in result.findings],
        summary=result.summary.to_dict(),
        created_at=result.created_at.isoformat(),
        model=result.model,
    )


@router.post("/diff", response_model=ReviewResponse)
async def review_diff(
    request: DiffReviewRequest,
    background_tasks: BackgroundTasks,
    service: AICodeReviewService = Depends(get_review_service),
):
    """
    Review a git diff.

    Focused review on changed lines only.
    Useful for PR reviews.
    """
    if not request.diff:
        raise HTTPException(status_code=400, detail="No diff provided")

    # Run diff review
    result = await service.review_diff(
        project_id=request.project_id,
        diff=request.diff,
        context=request.context,
    )

    # Store in history
    _review_history[result.id] = result
    background_tasks.add_task(_store_review, result)

    return ReviewResponse(
        id=result.id,
        project_id=result.project_id,
        files_reviewed=result.files_reviewed,
        findings=[f.to_dict() for f in result.findings],
        summary=result.summary.to_dict(),
        created_at=result.created_at.isoformat(),
        model=result.model,
    )


@router.get("/{review_id}", response_model=ReviewResponse)
async def get_review(review_id: str):
    """Get a specific review result."""
    if review_id not in _review_history:
        raise HTTPException(status_code=404, detail="Review not found")

    result = _review_history[review_id]

    return ReviewResponse(
        id=result.id,
        project_id=result.project_id,
        files_reviewed=result.files_reviewed,
        findings=[f.to_dict() for f in result.findings],
        summary=result.summary.to_dict(),
        created_at=result.created_at.isoformat(),
        model=result.model,
    )


@router.post("/{review_id}/fix")
async def apply_fix(
    review_id: str,
    request: FixRequest,
    service: AICodeReviewService = Depends(get_review_service),
):
    """
    Get auto-fix for a specific finding.

    Returns corrected code.
    """
    if review_id not in _review_history:
        raise HTTPException(status_code=404, detail="Review not found")

    result = _review_history[review_id]

    # Find the finding
    finding = next((f for f in result.findings if f.id == request.finding_id), None)

    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    if not finding.auto_fixable:
        raise HTTPException(status_code=400, detail="This finding is not auto-fixable")

    # Get fix suggestion
    fixed_code = await service.get_fix_suggestion(finding, request.file_content)

    if not fixed_code:
        raise HTTPException(status_code=500, detail="Could not generate fix")

    return {
        "finding_id": finding.id,
        "original_code": finding.code_before,
        "fixed_code": fixed_code,
        "file": finding.location.file,
    }


@router.get("/project/{project_id}/history")
async def get_review_history(
    project_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[ReviewHistoryItem]:
    """Get review history for a project."""
    # Filter by project
    project_reviews = [
        r for r in _review_history.values() if r.project_id == project_id
    ]

    # Sort by date descending
    project_reviews.sort(key=lambda r: r.created_at, reverse=True)

    # Paginate
    paginated = project_reviews[offset : offset + limit]

    return [
        ReviewHistoryItem(
            id=r.id,
            project_id=r.project_id,
            files_count=len(r.files_reviewed),
            findings_count=len(r.findings),
            score=r.summary.score,
            created_at=r.created_at.isoformat(),
        )
        for r in paginated
    ]


@router.get("/project/{project_id}/stats", response_model=ReviewStats)
async def get_review_stats(project_id: str):
    """Get aggregated review statistics for a project."""
    project_reviews = [
        r for r in _review_history.values() if r.project_id == project_id
    ]

    if not project_reviews:
        return ReviewStats(
            total_reviews=0,
            total_findings=0,
            avg_score=100.0,
            findings_by_severity={},
            findings_by_category={},
            top_issues=[],
        )

    # Aggregate stats
    total_findings = sum(len(r.findings) for r in project_reviews)
    avg_score = sum(r.summary.score for r in project_reviews) / len(project_reviews)

    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    issue_counts: dict[str, int] = {}

    for review in project_reviews:
        for s, count in review.summary.by_severity.items():
            by_severity[s] = by_severity.get(s, 0) + count
        for c, count in review.summary.by_category.items():
            by_category[c] = by_category.get(c, 0) + count
        for finding in review.findings:
            issue_counts[finding.title] = issue_counts.get(finding.title, 0) + 1

    # Top issues
    top_issues = sorted(
        [{"title": k, "count": v} for k, v in issue_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return ReviewStats(
        total_reviews=len(project_reviews),
        total_findings=total_findings,
        avg_score=round(avg_score, 1),
        findings_by_severity=by_severity,
        findings_by_category=by_category,
        top_issues=top_issues,
    )


@router.delete("/{review_id}")
async def delete_review(review_id: str):
    """Delete a review from history."""
    if review_id not in _review_history:
        raise HTTPException(status_code=404, detail="Review not found")

    del _review_history[review_id]
    return {"deleted": review_id}


# ============================================
# WebSocket for Streaming Reviews
# ============================================

from fastapi import WebSocket, WebSocketDisconnect


@router.websocket("/ws/{project_id}")
async def review_websocket(
    websocket: WebSocket,
    project_id: str,
    service: AICodeReviewService = Depends(get_review_service),
):
    """
    WebSocket endpoint for streaming review results.

    Send files to review, receive findings as they're discovered.
    """
    await websocket.accept()

    try:
        while True:
            # Receive files to review
            data = await websocket.receive_json()

            if data.get("type") == "review":
                files = data.get("files", {})

                # Send start message
                await websocket.send_json(
                    {
                        "type": "start",
                        "files_count": len(files),
                    }
                )

                # Review each file and stream findings
                for filename, code in files.items():
                    await websocket.send_json(
                        {
                            "type": "reviewing",
                            "file": filename,
                        }
                    )

                    findings = await service._review_file(filename, code)

                    await websocket.send_json(
                        {
                            "type": "findings",
                            "file": filename,
                            "findings": [f.to_dict() for f in findings],
                        }
                    )

                # Send completion
                await websocket.send_json(
                    {
                        "type": "complete",
                        "project_id": project_id,
                    }
                )

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass


# ============================================
# Background Tasks
# ============================================


async def _store_review(result: CodeReviewResult):
    """Store review in database (placeholder)."""
    # In production, store in database
    pass


# ============================================
# GitHub Integration
# ============================================


class GitHubPRReviewRequest(BaseModel):
    """Request to review a GitHub PR."""

    owner: str
    repo: str
    pr_number: int
    github_token: Optional[str] = None


@router.post("/github/pr")
async def review_github_pr(
    request: GitHubPRReviewRequest,
    background_tasks: BackgroundTasks,
    service: AICodeReviewService = Depends(get_review_service),
):
    """
    Review a GitHub Pull Request.

    Fetches PR diff and reviews changed files.
    Optionally posts comments to the PR.
    """
    import httpx

    headers = {}
    if request.github_token:
        headers["Authorization"] = f"token {request.github_token}"

    # Fetch PR diff
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/pulls/{request.pr_number}",
            headers={**headers, "Accept": "application/vnd.github.v3.diff"},
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to fetch PR: {response.text}",
            )

        diff = response.text

    # Review the diff
    result = await service.review_diff(
        project_id=f"github:{request.owner}/{request.repo}",
        diff=diff,
        context={"source": "github_pr", "pr_number": request.pr_number},
    )

    # Store in history
    _review_history[result.id] = result

    # Optionally post comments (background task)
    if request.github_token:
        background_tasks.add_task(
            _post_github_comments,
            request.owner,
            request.repo,
            request.pr_number,
            request.github_token,
            result,
        )

    return ReviewResponse(
        id=result.id,
        project_id=result.project_id,
        files_reviewed=result.files_reviewed,
        findings=[f.to_dict() for f in result.findings],
        summary=result.summary.to_dict(),
        created_at=result.created_at.isoformat(),
        model=result.model,
    )


async def _post_github_comments(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    result: CodeReviewResult,
):
    """Post review comments to GitHub PR."""
    import httpx

    async with httpx.AsyncClient() as client:
        # Post summary comment
        summary_body = f"""## 🤖 AI Code Review

**Score:** {result.summary.score}/100
**Files Reviewed:** {len(result.files_reviewed)}
**Issues Found:** {result.summary.total_findings}

### Summary by Severity
| Severity | Count |
|----------|-------|
"""
        for severity, count in result.summary.by_severity.items():
            emoji = {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🔵",
                "info": "⚪",
            }.get(severity, "⚪")
            summary_body += f"| {emoji} {severity.title()} | {count} |\n"

        await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers={"Authorization": f"token {token}"},
            json={"body": summary_body},
        )

        # Post inline comments for critical/high findings
        for finding in result.findings:
            if finding.severity in [ReviewSeverity.CRITICAL, ReviewSeverity.HIGH]:
                comment_body = f"""**{finding.severity.value.upper()}**: {finding.title}

{finding.description}

**Suggestion:** {finding.suggestion or 'See below'}
"""
                if finding.code_after:
                    comment_body += f"\n```suggestion\n{finding.code_after}\n```"

                await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                    headers={"Authorization": f"token {token}"},
                    json={
                        "body": comment_body,
                        "path": finding.location.file,
                        "line": finding.location.end_line,
                    },
                )
