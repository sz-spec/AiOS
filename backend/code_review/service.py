"""
AI Code Review Service

Provides automated code review capabilities using AI:
- Security vulnerability detection
- Performance issue identification
- Code style and best practices
- Bug detection
- Complexity analysis
- Documentation suggestions

Based on industry best practices and forum recommendations (Dec 2025).
"""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from src.efficiency.factory import get_llm_for_task


class ReviewSeverity(str, Enum):
    """Severity levels for code review issues."""

    CRITICAL = "critical"  # Security vulnerabilities, data loss risks
    HIGH = "high"  # Bugs, performance issues
    MEDIUM = "medium"  # Code smells, maintainability
    LOW = "low"  # Style, suggestions
    INFO = "info"  # Informational, best practices


class ReviewCategory(str, Enum):
    """Categories for code review findings."""

    SECURITY = "security"
    PERFORMANCE = "performance"
    BUG = "bug"
    STYLE = "style"
    COMPLEXITY = "complexity"
    DOCUMENTATION = "documentation"
    BEST_PRACTICE = "best_practice"
    ACCESSIBILITY = "accessibility"
    TESTING = "testing"
    TYPE_SAFETY = "type_safety"


@dataclass
class CodeLocation:
    """Location of code in a file."""

    file: str
    start_line: int
    end_line: int
    start_col: Optional[int] = None
    end_col: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_col": self.start_col,
            "end_col": self.end_col,
        }


@dataclass
class ReviewFinding:
    """A single finding from code review."""

    id: str
    title: str
    description: str
    severity: ReviewSeverity
    category: ReviewCategory
    location: CodeLocation
    suggestion: Optional[str] = None
    code_before: Optional[str] = None
    code_after: Optional[str] = None
    references: list[str] = field(default_factory=list)
    auto_fixable: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category.value,
            "location": self.location.to_dict(),
            "suggestion": self.suggestion,
            "code_before": self.code_before,
            "code_after": self.code_after,
            "references": self.references,
            "auto_fixable": self.auto_fixable,
        }


@dataclass
class ReviewSummary:
    """Summary statistics for a code review."""

    total_findings: int
    by_severity: dict[str, int]
    by_category: dict[str, int]
    score: int  # 0-100, higher is better
    review_time_ms: int

    def to_dict(self) -> dict:
        return {
            "total_findings": self.total_findings,
            "by_severity": self.by_severity,
            "by_category": self.by_category,
            "score": self.score,
            "review_time_ms": self.review_time_ms,
        }


@dataclass
class CodeReviewResult:
    """Complete result of a code review."""

    id: str
    project_id: str
    files_reviewed: list[str]
    findings: list[ReviewFinding]
    summary: ReviewSummary
    created_at: datetime
    model: str
    context: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "files_reviewed": self.files_reviewed,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary.to_dict(),
            "created_at": self.created_at.isoformat(),
            "model": self.model,
            "context": self.context,
        }


class AICodeReviewService:
    """
    AI-powered code review service.

    Features:
    - Multi-file analysis
    - Security scanning
    - Performance profiling
    - Style checking
    - Auto-fix suggestions
    - Caching for efficiency
    """

    REVIEW_PROMPT = """You are an expert code reviewer. Analyze the following code and provide detailed feedback.

For each issue found, respond with a JSON object containing:
- title: Brief title of the issue
- description: Detailed explanation
- severity: one of [critical, high, medium, low, info]
- category: one of [security, performance, bug, style, complexity, documentation, best_practice, accessibility, testing, type_safety]
- start_line: Line number where issue starts
- end_line: Line number where issue ends
- suggestion: How to fix it
- code_after: Fixed code (if applicable)
- auto_fixable: boolean if this can be automatically fixed
- references: List of relevant documentation URLs

Focus on:
1. SECURITY: SQL injection, XSS, auth issues, secrets exposure, CSRF
2. PERFORMANCE: N+1 queries, memory leaks, unnecessary re-renders, missing indexes
3. BUGS: Null pointer, race conditions, off-by-one errors, unhandled exceptions
4. STYLE: Naming conventions, code organization, consistent formatting
5. COMPLEXITY: Functions too long, deep nesting, cyclomatic complexity
6. DOCUMENTATION: Missing docstrings, unclear comments, outdated docs
7. BEST PRACTICES: Design patterns, SOLID principles, DRY violations
8. ACCESSIBILITY: Missing ARIA labels, color contrast, keyboard navigation
9. TESTING: Missing tests, poor test coverage, flaky tests
10. TYPE SAFETY: Missing types, any usage, incorrect types

Be specific and actionable. Provide code examples for fixes.

Respond ONLY with a JSON array of findings. If no issues, return an empty array [].

Language/Framework context: {context}

File: {filename}
```{language}
{code}
```"""

    SUMMARY_PROMPT = """Based on these code review findings, provide a brief summary:

Findings:
{findings}

Respond with JSON:
{{
  "overall_assessment": "Brief 1-2 sentence summary",
  "top_priorities": ["Priority 1", "Priority 2", "Priority 3"],
  "positive_aspects": ["Good thing 1", "Good thing 2"],
  "score": <0-100 quality score>
}}"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        cache_enabled: bool = True,
        max_file_size: int = 100_000,  # 100KB
        max_files: int = 50,
    ):
        self.llm = get_llm_for_task("reviewer", complexity=7)
        self.model = model
        self.cache_enabled = cache_enabled
        self.max_file_size = max_file_size
        self.max_files = max_files
        self._cache: dict[str, CodeReviewResult] = {}

    def _get_language(self, filename: str) -> str:
        """Detect language from filename."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "jsx",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".rb": "ruby",
            ".php": "php",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".sql": "sql",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".md": "markdown",
            ".sh": "bash",
            ".dockerfile": "dockerfile",
        }
        ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
        return ext_map.get(ext, "text")

    def _get_context(self, filename: str) -> str:
        """Get context hints based on filename/path."""
        contexts = []

        # Framework detection
        if "react" in filename.lower() or filename.endswith((".jsx", ".tsx")):
            contexts.append("React")
        if "next" in filename.lower() or "app/" in filename:
            contexts.append("Next.js")
        if "fastapi" in filename.lower() or "api/" in filename:
            contexts.append("FastAPI")
        if "django" in filename.lower():
            contexts.append("Django")
        if "flask" in filename.lower():
            contexts.append("Flask")

        # File type context
        if "test" in filename.lower() or "spec" in filename.lower():
            contexts.append("Test file")
        if "component" in filename.lower():
            contexts.append("Component")
        if "hook" in filename.lower():
            contexts.append("React Hook")
        if "model" in filename.lower():
            contexts.append("Data model")
        if "route" in filename.lower():
            contexts.append("API route")

        return ", ".join(contexts) if contexts else "General"

    def _generate_finding_id(self, finding: dict, filename: str) -> str:
        """Generate unique ID for a finding."""
        content = f"{filename}:{finding.get('start_line')}:{finding.get('title')}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _cache_key(self, files: dict[str, str]) -> str:
        """Generate cache key from files content."""
        content = json.dumps(files, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    async def _review_file(
        self,
        filename: str,
        code: str,
        context: Optional[str] = None,
    ) -> list[ReviewFinding]:
        """Review a single file."""
        if len(code) > self.max_file_size:
            # For large files, review in chunks
            return await self._review_large_file(filename, code, context)

        language = self._get_language(filename)
        file_context = context or self._get_context(filename)

        prompt = self.REVIEW_PROMPT.format(
            context=file_context,
            filename=filename,
            language=language,
            code=code,
        )

        try:
            from langchain_core.messages import HumanMessage

            response = await self.llm.ainvoke([HumanMessage(content=prompt)])

            content = response.content

            # Parse JSON from response
            findings_data = self._parse_json_response(content)

            findings = []
            for f in findings_data:
                try:
                    finding = ReviewFinding(
                        id=self._generate_finding_id(f, filename),
                        title=f.get("title", "Unnamed Issue"),
                        description=f.get("description", ""),
                        severity=ReviewSeverity(f.get("severity", "info")),
                        category=ReviewCategory(f.get("category", "best_practice")),
                        location=CodeLocation(
                            file=filename,
                            start_line=f.get("start_line", 1),
                            end_line=f.get("end_line", f.get("start_line", 1)),
                        ),
                        suggestion=f.get("suggestion"),
                        code_before=self._extract_code_snippet(
                            code, f.get("start_line", 1), f.get("end_line", 1)
                        ),
                        code_after=f.get("code_after"),
                        references=f.get("references", []),
                        auto_fixable=f.get("auto_fixable", False),
                    )
                    findings.append(finding)
                except (ValueError, KeyError):
                    # Skip malformed findings
                    continue

            return findings

        except Exception as e:
            # Return error finding
            return [
                ReviewFinding(
                    id=self._generate_finding_id({"title": "error"}, filename),
                    title="Review Error",
                    description=f"Failed to review file: {str(e)}",
                    severity=ReviewSeverity.INFO,
                    category=ReviewCategory.BEST_PRACTICE,
                    location=CodeLocation(file=filename, start_line=1, end_line=1),
                )
            ]

    async def _review_large_file(
        self,
        filename: str,
        code: str,
        context: Optional[str] = None,
    ) -> list[ReviewFinding]:
        """Review a large file by chunking."""
        lines = code.split("\n")
        chunk_size = 500  # lines per chunk
        findings = []

        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i : i + chunk_size]
            chunk_code = "\n".join(chunk_lines)

            # Add line offset to findings
            chunk_findings = await self._review_file(filename, chunk_code, context)

            for f in chunk_findings:
                # Adjust line numbers
                f.location.start_line += i
                f.location.end_line += i

            findings.extend(chunk_findings)

        return findings

    def _parse_json_response(self, content: str) -> list[dict]:
        """Parse JSON from AI response."""
        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array
        match = re.search(r"\[[\s\S]*\]", content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Return empty if can't parse
        return []

    def _extract_code_snippet(
        self,
        code: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> str:
        """Extract code snippet with context."""
        lines = code.split("\n")
        start = max(0, start_line - 1 - context_lines)
        end = min(len(lines), end_line + context_lines)
        return "\n".join(lines[start:end])

    def _calculate_score(self, findings: list[ReviewFinding]) -> int:
        """Calculate quality score based on findings."""
        if not findings:
            return 100

        # Severity weights
        weights = {
            ReviewSeverity.CRITICAL: 25,
            ReviewSeverity.HIGH: 15,
            ReviewSeverity.MEDIUM: 8,
            ReviewSeverity.LOW: 3,
            ReviewSeverity.INFO: 1,
        }

        total_deduction = sum(weights[f.severity] for f in findings)
        score = max(0, 100 - total_deduction)
        return score

    def _create_summary(
        self,
        findings: list[ReviewFinding],
        review_time_ms: int,
    ) -> ReviewSummary:
        """Create review summary from findings."""
        by_severity = {}
        by_category = {}

        for f in findings:
            by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
            by_category[f.category.value] = by_category.get(f.category.value, 0) + 1

        return ReviewSummary(
            total_findings=len(findings),
            by_severity=by_severity,
            by_category=by_category,
            score=self._calculate_score(findings),
            review_time_ms=review_time_ms,
        )

    async def review_code(
        self,
        project_id: str,
        files: dict[str, str],
        context: Optional[dict] = None,
    ) -> CodeReviewResult:
        """
        Review multiple files.

        Args:
            project_id: Project identifier
            files: Dict of filename -> code content
            context: Optional context (framework, standards, etc.)

        Returns:
            CodeReviewResult with all findings
        """
        # Check cache
        if self.cache_enabled:
            cache_key = self._cache_key(files)
            if cache_key in self._cache:
                return self._cache[cache_key]

        start_time = datetime.now()

        # Limit files
        if len(files) > self.max_files:
            files = dict(list(files.items())[: self.max_files])

        # Review all files concurrently
        tasks = [
            self._review_file(
                filename, code, context.get("framework") if context else None
            )
            for filename, code in files.items()
        ]

        results = await asyncio.gather(*tasks)

        # Flatten findings
        all_findings = []
        for file_findings in results:
            all_findings.extend(file_findings)

        # Sort by severity
        severity_order = {
            ReviewSeverity.CRITICAL: 0,
            ReviewSeverity.HIGH: 1,
            ReviewSeverity.MEDIUM: 2,
            ReviewSeverity.LOW: 3,
            ReviewSeverity.INFO: 4,
        }
        all_findings.sort(key=lambda f: severity_order[f.severity])

        # Calculate time
        review_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        # Create result
        result = CodeReviewResult(
            id=hashlib.md5(
                f"{project_id}:{datetime.now().isoformat()}".encode()
            ).hexdigest()[:16],
            project_id=project_id,
            files_reviewed=list(files.keys()),
            findings=all_findings,
            summary=self._create_summary(all_findings, review_time_ms),
            created_at=datetime.now(),
            model=self.model,
            context=context,
        )

        # Cache result
        if self.cache_enabled:
            self._cache[cache_key] = result

        return result

    async def review_diff(
        self,
        project_id: str,
        diff: str,
        context: Optional[dict] = None,
    ) -> CodeReviewResult:
        """
        Review a git diff.

        Args:
            project_id: Project identifier
            diff: Git diff content
            context: Optional context

        Returns:
            CodeReviewResult focused on changes
        """
        # Parse diff to extract changed files
        files = self._parse_diff(diff)

        # Add diff context
        review_context = context or {}
        review_context["review_type"] = "diff"

        return await self.review_code(project_id, files, review_context)

    def _parse_diff(self, diff: str) -> dict[str, str]:
        """Parse git diff into files dict."""
        files = {}
        current_file = None
        current_content = []

        for line in diff.split("\n"):
            if line.startswith("+++ b/"):
                if current_file and current_content:
                    files[current_file] = "\n".join(current_content)
                current_file = line[6:]
                current_content = []
            elif current_file and line.startswith("+") and not line.startswith("+++"):
                current_content.append(line[1:])

        if current_file and current_content:
            files[current_file] = "\n".join(current_content)

        return files

    async def get_fix_suggestion(
        self,
        finding: ReviewFinding,
        full_code: str,
    ) -> Optional[str]:
        """Get detailed fix suggestion for a finding."""
        prompt = f"""Given this code issue:

Title: {finding.title}
Description: {finding.description}
Category: {finding.category.value}

Code snippet (lines {finding.location.start_line}-{finding.location.end_line}):
```
{finding.code_before}
```

Full file context:
```
{full_code[:5000]}  # Limit context
```

Provide the corrected code that fixes this issue. Respond with ONLY the fixed code, no explanations."""

        try:
            from langchain_core.messages import HumanMessage

            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            return response.content
        except Exception:
            return None

    def clear_cache(self):
        """Clear the review cache."""
        self._cache.clear()
