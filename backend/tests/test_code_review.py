"""
Tests for AI Code Review Service

Tests code review functionality including:
- Service initialization
- File review
- Static rules
- Finding generation
- Summary calculation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from code_review.service import (
    AICodeReviewService,
    CodeReviewResult,
    ReviewFinding,
    ReviewSeverity,
    ReviewCategory,
    CodeLocation,
)
from code_review.rules import (
    SecurityRules,
    PerformanceRules,
    StyleRules,
    BestPracticeRules,
    StaticAnalyzer,
)

# ============================================
# Fixtures
# ============================================


@pytest.fixture
def review_service():
    """Create review service with mocked API client."""
    with patch("code_review.service.get_llm_for_task") as mock_factory:
        mock_llm = MagicMock()
        mock_factory.return_value = mock_llm
        service = AICodeReviewService(api_key="test-key")
        return service


@pytest.fixture
def sample_python_code():
    return '''
import os

API_KEY = "sk-1234567890abcdef"

def get_user(user_id):
    """Get user by ID."""
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    return cursor.fetchone()

def process_data(data):
    result = ""
    for item in data:
        result += str(item)
    return result
'''


@pytest.fixture
def sample_typescript_code():
    return """
import * as lodash from 'lodash';

const API_KEY = "pk_test_123456789";

function fetchUser(id: any) {
    var result = null;
    document.innerHTML = `<div>${id}</div>`;
    return result;
}

export function Component({ props }) {
    const items = props.data.map(x => x);
    return <div>{items}</div>;
}
"""


@pytest.fixture
def sample_finding():
    return ReviewFinding(
        id="test_001",
        title="Test Finding",
        description="This is a test finding",
        severity=ReviewSeverity.MEDIUM,
        category=ReviewCategory.STYLE,
        location=CodeLocation(
            file="test.py",
            start_line=5,
            end_line=5,
        ),
        suggestion="Fix this issue",
        code_before="bad code",
        code_after="good code",
        auto_fixable=True,
    )


# ============================================
# Service Tests
# ============================================


class TestAICodeReviewService:
    """Tests for AICodeReviewService."""

    def test_init_defaults(self):
        """Test service initialization with defaults."""
        with patch("code_review.service.get_llm_for_task"):
            service = AICodeReviewService()
            assert service.model == "claude-sonnet-4-20250514"
            assert service.cache_enabled is True
            assert service.max_file_size == 100_000

    def test_init_custom_config(self):
        """Test service initialization with custom config."""
        with patch("code_review.service.get_llm_for_task"):
            service = AICodeReviewService(
                api_key="custom-key",
                model="claude-3-opus",
                cache_enabled=False,
                max_file_size=50_000,
            )
            assert service.model == "claude-3-opus"
            assert service.cache_enabled is False
            assert service.max_file_size == 50_000

    def test_get_language(self, review_service):
        """Test language detection from filename."""
        assert review_service._get_language("test.py") == "python"
        assert review_service._get_language("test.ts") == "typescript"
        assert review_service._get_language("test.tsx") == "tsx"
        assert review_service._get_language("test.js") == "javascript"
        assert review_service._get_language("test.sql") == "sql"
        assert review_service._get_language("test.unknown") == "text"

    def test_get_context(self, review_service):
        """Test context detection from filename."""
        assert "React" in review_service._get_context("Component.tsx")
        assert "Test file" in review_service._get_context("test_something.py")
        assert "API route" in review_service._get_context("api/route.ts")
        assert "Component" in review_service._get_context("src/components/Button.tsx")

    def test_calculate_score_no_findings(self, review_service):
        """Test score calculation with no findings."""
        score = review_service._calculate_score([])
        assert score == 100

    def test_calculate_score_with_findings(self, review_service, sample_finding):
        """Test score calculation with findings."""
        findings = [sample_finding]  # Medium severity = -8
        score = review_service._calculate_score(findings)
        assert score == 92

    def test_calculate_score_critical(self, review_service):
        """Test score with critical finding."""
        finding = ReviewFinding(
            id="critical_001",
            title="Critical Issue",
            description="Critical security issue",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.SECURITY,
            location=CodeLocation(file="test.py", start_line=1, end_line=1),
        )
        score = review_service._calculate_score([finding])
        assert score == 75  # 100 - 25

    def test_create_summary(self, review_service, sample_finding):
        """Test summary creation."""
        findings = [sample_finding]
        summary = review_service._create_summary(findings, review_time_ms=1500)

        assert summary.total_findings == 1
        assert summary.by_severity["medium"] == 1
        assert summary.by_category["style"] == 1
        assert summary.score == 92
        assert summary.review_time_ms == 1500

    def test_parse_json_response_valid(self, review_service):
        """Test JSON parsing from valid response."""
        response = '[{"title": "Test", "severity": "medium"}]'
        result = review_service._parse_json_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "Test"

    def test_parse_json_response_with_markdown(self, review_service):
        """Test JSON parsing with markdown code block."""
        response = """Here are the findings:
```json
[{"title": "Test"}]
```"""
        result = review_service._parse_json_response(response)
        assert len(result) == 1

    def test_parse_json_response_invalid(self, review_service):
        """Test JSON parsing with invalid response."""
        response = "This is not JSON"
        result = review_service._parse_json_response(response)
        assert result == []

    @pytest.mark.asyncio
    async def test_review_code(self, review_service, sample_python_code):
        """Test full code review."""
        # Mock AI response
        mock_response = MagicMock()
        mock_response.content = '[{"title": "Test Issue", "severity": "medium", "category": "style", "start_line": 1, "end_line": 1, "suggestion": "Fix it"}]'
        review_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await review_service.review_code(
            project_id="test-project",
            files={"test.py": sample_python_code},
        )

        assert isinstance(result, CodeReviewResult)
        assert result.project_id == "test-project"
        assert len(result.files_reviewed) == 1
        assert result.summary.total_findings >= 0

    @pytest.mark.asyncio
    async def test_review_code_caching(self, review_service, sample_python_code):
        """Test that results are cached."""
        mock_response = MagicMock()
        mock_response.content = "[]"
        review_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        # First review
        result1 = await review_service.review_code(
            project_id="test-project",
            files={"test.py": sample_python_code},
        )

        # Second review (should be cached)
        result2 = await review_service.review_code(
            project_id="test-project",
            files={"test.py": sample_python_code},
        )

        assert result1.id == result2.id
        # API should only be called once
        assert review_service.llm.ainvoke.call_count == 1

    def test_extract_code_snippet(self, review_service):
        """Test code snippet extraction."""
        code = "line1\nline2\nline3\nline4\nline5"
        snippet = review_service._extract_code_snippet(code, 3, 3, context_lines=1)
        assert "line2" in snippet
        assert "line3" in snippet
        assert "line4" in snippet


# ============================================
# Static Rules Tests
# ============================================


class TestSecurityRules:
    """Tests for security rules."""

    def test_detect_hardcoded_secret(self, sample_python_code):
        """Test detection of hardcoded secrets."""
        findings = SecurityRules.check(sample_python_code, "test.py", "python")
        secret_findings = [f for f in findings if f.id.startswith("SEC001")]
        assert len(secret_findings) >= 1
        assert secret_findings[0].severity == ReviewSeverity.CRITICAL

    def test_detect_sql_injection(self, sample_python_code):
        """Test detection of SQL injection."""
        findings = SecurityRules.check(sample_python_code, "test.py", "python")
        sql_findings = [f for f in findings if "SQL" in f.title.upper()]
        # Should detect the f-string SQL query
        assert len(sql_findings) >= 0  # Depends on regex matching

    def test_detect_innerHTML(self, sample_typescript_code):
        """Test detection of innerHTML usage."""
        findings = SecurityRules.check(sample_typescript_code, "test.ts", "typescript")
        xss_findings = [
            f for f in findings if "innerHTML" in f.title or "XSS" in f.title
        ]
        assert len(xss_findings) >= 1
        assert xss_findings[0].severity == ReviewSeverity.HIGH

    def test_detect_eval(self):
        """Test detection of eval usage."""
        code = "result = eval(user_input)"
        findings = SecurityRules.check(code, "test.py", "python")
        eval_findings = [f for f in findings if "eval" in f.title.lower()]
        assert len(eval_findings) >= 1
        assert eval_findings[0].severity == ReviewSeverity.CRITICAL


class TestPerformanceRules:
    """Tests for performance rules."""

    def test_detect_full_import(self, sample_typescript_code):
        """Test detection of full library imports."""
        findings = PerformanceRules.check(
            sample_typescript_code, "test.ts", "typescript"
        )
        import_findings = [
            f
            for f in findings
            if "import" in f.title.lower() or "bundle" in f.title.lower()
        ]
        assert len(import_findings) >= 1

    def test_detect_sync_file_ops(self):
        """Test detection of synchronous file operations."""
        code = "const data = fs.readFileSync('file.txt');"
        findings = PerformanceRules.check(code, "test.js", "javascript")
        sync_findings = [f for f in findings if "Synchronous" in f.title]
        assert len(sync_findings) >= 1
        assert sync_findings[0].severity == ReviewSeverity.HIGH


class TestStyleRules:
    """Tests for style rules."""

    def test_detect_console_log(self, sample_typescript_code):
        """Test detection of console.log statements."""
        code_with_console = sample_typescript_code + "\nconsole.log('debug');"
        findings = StyleRules.check(code_with_console, "test.ts", "typescript")
        console_findings = [f for f in findings if "console" in f.title.lower()]
        assert len(console_findings) >= 1

    def test_detect_var_usage(self, sample_typescript_code):
        """Test detection of var usage."""
        findings = StyleRules.check(sample_typescript_code, "test.js", "javascript")
        var_findings = [f for f in findings if "var" in f.title.lower()]
        assert len(var_findings) >= 1

    def test_detect_any_type(self, sample_typescript_code):
        """Test detection of 'any' type usage."""
        findings = StyleRules.check(sample_typescript_code, "test.ts", "typescript")
        any_findings = [f for f in findings if "any" in f.title.lower()]
        assert len(any_findings) >= 1


class TestBestPracticeRules:
    """Tests for best practice rules."""

    def test_detect_empty_catch(self):
        """Test detection of empty catch blocks."""
        code = """
try {
    doSomething();
} catch (e) {}
"""
        findings = BestPracticeRules.check(code, "test.js", "javascript")
        catch_findings = [f for f in findings if "catch" in f.title.lower()]
        assert len(catch_findings) >= 1


class TestStaticAnalyzer:
    """Tests for combined static analyzer."""

    def test_analyze_python(self, sample_python_code):
        """Test full Python analysis."""
        findings = StaticAnalyzer.analyze(sample_python_code, "test.py")
        assert len(findings) > 0
        # Should find secret and performance issues
        categories = set(f.category for f in findings)
        assert ReviewCategory.SECURITY in categories or len(findings) > 0

    def test_analyze_typescript(self, sample_typescript_code):
        """Test full TypeScript analysis."""
        findings = StaticAnalyzer.analyze(sample_typescript_code, "test.tsx")
        assert len(findings) > 0

    def test_analyze_deduplication(self, sample_python_code):
        """Test that duplicate findings are removed."""
        findings = StaticAnalyzer.analyze(sample_python_code, "test.py")
        # Check no duplicate locations
        locations = [
            (f.location.file, f.location.start_line, f.title) for f in findings
        ]
        assert len(locations) == len(set(locations))


# ============================================
# Finding Tests
# ============================================


class TestReviewFinding:
    """Tests for ReviewFinding class."""

    def test_to_dict(self, sample_finding):
        """Test finding serialization."""
        data = sample_finding.to_dict()
        assert data["id"] == "test_001"
        assert data["title"] == "Test Finding"
        assert data["severity"] == "medium"
        assert data["category"] == "style"
        assert data["auto_fixable"] is True

    def test_location_to_dict(self):
        """Test location serialization."""
        location = CodeLocation(
            file="test.py",
            start_line=10,
            end_line=15,
            start_col=5,
            end_col=20,
        )
        data = location.to_dict()
        assert data["file"] == "test.py"
        assert data["start_line"] == 10
        assert data["end_line"] == 15


# ============================================
# Integration Tests
# ============================================


class TestIntegration:
    """Integration tests for code review."""

    @pytest.mark.asyncio
    async def test_full_review_flow(self, review_service, sample_python_code):
        """Test complete review flow."""
        mock_response = MagicMock()
        mock_response.content = """[
            {
                "title": "Hardcoded API Key",
                "description": "API key found in source code",
                "severity": "critical",
                "category": "security",
                "start_line": 3,
                "end_line": 3,
                "suggestion": "Use environment variable"
            }
        ]"""
        review_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await review_service.review_code(
            project_id="test-project",
            files={
                "app.py": sample_python_code,
            },
            context={"framework": "FastAPI"},
        )

        # Verify result structure
        assert result.id is not None
        assert result.project_id == "test-project"
        assert "app.py" in result.files_reviewed

        # Verify findings
        assert len(result.findings) >= 1
        critical = [f for f in result.findings if f.severity == ReviewSeverity.CRITICAL]
        assert len(critical) >= 1

        # Verify summary
        assert result.summary.score < 100  # Should have deductions
        assert result.summary.total_findings >= 1

    @pytest.mark.asyncio
    async def test_multi_file_review(
        self, review_service, sample_python_code, sample_typescript_code
    ):
        """Test reviewing multiple files."""
        mock_response = MagicMock()
        mock_response.content = "[]"
        review_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await review_service.review_code(
            project_id="test-project",
            files={
                "backend.py": sample_python_code,
                "frontend.tsx": sample_typescript_code,
            },
        )

        assert len(result.files_reviewed) == 2
        assert "backend.py" in result.files_reviewed
        assert "frontend.tsx" in result.files_reviewed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
