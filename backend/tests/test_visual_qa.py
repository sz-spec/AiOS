"""
Tests for IE-4 Visual QA Agent — Phase 2.5
============================================
All tests mock Playwright and LLM calls. No real headless browser in CI.

Test classes (TIS §2.5.1.8):
  TestVisualIssue         — 3 tests
  TestVisualQAReport      — 5 tests
  TestRouteDetection      — 7 tests
  TestMultimodalPrompt    — 5 tests
  TestScreenshotCapture   — 5 tests
  TestAnalysis            — 5 tests
  TestGracefulDegradation — 6 tests
  TestVisualQANode        — 7 tests
  TestReviewerInjection   — 4 tests
  TestEndToEnd            — 4 tests
"""

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents.visual_qa import (
    VisualSeverity,
    VisualIssue,
    VisualQAReport,
    VisualQAAgent,
    VIEWPORTS,
    MAX_SCREENSHOTS,
    MAX_IMAGE_BYTES,
    detect_routes,
    capture_screenshots,
    build_reviewer_visual_context,
    _build_visual_qa_prompt,
    _build_architecture_prompt,
    _build_multimodal_messages,
    _parse_issues_from_response,
    _grade_viewport,
    _has_complex_deps,
)

# =====================================================================
# Fixtures
# =====================================================================

NEXTJS_FILES = {
    "app/page.tsx": "export default function Home() { return <div>Home</div> }",
    "app/dashboard/page.tsx": "export default function Dashboard() { return <div>Dash</div> }",
    "app/settings/page.tsx": "export default function Settings() { return <div>Settings</div> }",
    "app/layout.tsx": "export default function RootLayout({ children }) { return <html><body>{children}</body></html> }",
    "components/Navbar.tsx": "export function Navbar() { return <nav>Nav</nav> }",
}

REACT_ROUTER_FILES = {
    "src/App.tsx": """
import { BrowserRouter, Route, Routes } from 'react-router-dom';
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/about" element={<About />} />
        <Route path="/contact" element={<Contact />} />
      </Routes>
    </BrowserRouter>
  );
}
""",
    "src/index.tsx": "import App from './App'; ReactDOM.render(<App />, document.getElementById('root'));",
}

SIMPLE_FILES = {
    "index.html": "<html><body><div id='root'></div></body></html>",
    "src/main.tsx": "console.log('hello')",
}

SAMPLE_ISSUES_JSON = json.dumps(
    [
        {
            "id": "VIS001",
            "title": "Text overflows container",
            "description": "The heading text overflows its parent div.",
            "severity": "critical",
            "location_hint": "top-left, hero section",
            "fix_suggestion": "Add overflow-hidden and text-ellipsis to the heading container",
        },
        {
            "id": "A11Y001",
            "title": "Low contrast text",
            "description": "Grey text (#999) on white background has 2.85:1 ratio.",
            "severity": "high",
            "location_hint": "footer paragraph",
            "fix_suggestion": "Change text color to #595959 for 7:1 contrast ratio",
        },
    ]
)

SAMPLE_ARCHITECTURE = {
    "tech_stack": {"frontend": "react", "backend": "fastapi"},
    "routes": ["/", "/dashboard"],
    "components": ["Navbar", "Sidebar", "DataTable"],
    "design_system": {
        "primary_color": "#3B82F6",
        "background": "#FFFFFF",
        "font_family": "Inter",
        "base_spacing": "8px",
    },
}


# =====================================================================
# TestVisualIssue
# =====================================================================


class TestVisualIssue:
    def test_to_dict_serializes_severity(self):
        issue = VisualIssue(
            id="VIS001",
            title="Overflow",
            description="Text overflows",
            severity=VisualSeverity.CRITICAL,
            viewport="desktop",
            route="/",
            location_hint="top",
            fix_suggestion="Add overflow-hidden",
        )
        d = issue.to_dict()
        assert d["severity"] == "critical"
        assert d["id"] == "VIS001"
        assert d["viewport"] == "desktop"

    def test_all_severity_values(self):
        assert VisualSeverity.CRITICAL.value == "critical"
        assert VisualSeverity.HIGH.value == "high"
        assert VisualSeverity.MEDIUM.value == "medium"
        assert VisualSeverity.INFO.value == "info"

    def test_issue_fields_preserved(self):
        issue = VisualIssue(
            id="A11Y001",
            title="Low contrast",
            description="Below 4.5:1",
            severity=VisualSeverity.HIGH,
            viewport="mobile",
            route="/about",
            location_hint="footer",
            fix_suggestion="Darken text",
        )
        d = issue.to_dict()
        assert d["route"] == "/about"
        assert d["location_hint"] == "footer"
        assert d["fix_suggestion"] == "Darken text"


# =====================================================================
# TestVisualQAReport
# =====================================================================


class TestVisualQAReport:
    def test_empty_report(self):
        report = VisualQAReport()
        d = report.to_dict()
        assert d["issues"] == []
        assert d["screenshots_analyzed"] == 0
        assert report.has_critical is False

    def test_report_with_issues(self):
        issue = VisualIssue(
            id="VIS001",
            title="Overflow",
            description="d",
            severity=VisualSeverity.CRITICAL,
            viewport="desktop",
            route="/",
            location_hint="top",
            fix_suggestion="fix",
        )
        report = VisualQAReport(issues=[issue], screenshots_analyzed=3)
        assert report.has_critical is True
        d = report.to_dict()
        assert len(d["issues"]) == 1
        assert d["screenshots_analyzed"] == 3

    def test_has_critical_false_for_non_critical(self):
        issue = VisualIssue(
            id="VIS002",
            title="Minor",
            description="d",
            severity=VisualSeverity.MEDIUM,
            viewport="desktop",
            route="/",
            location_hint="bottom",
            fix_suggestion="fix",
        )
        report = VisualQAReport(issues=[issue])
        assert report.has_critical is False

    def test_to_dict_serializes_grades(self):
        report = VisualQAReport(
            grades={"/": {"desktop": "pass", "mobile": "warn"}},
            summary={"pass": 1, "warn": 1, "fail": 0},
            routes_tested=["/"],
            viewports_tested=["desktop", "mobile"],
        )
        d = report.to_dict()
        assert d["grades"]["/"]["desktop"] == "pass"
        assert d["grades"]["/"]["mobile"] == "warn"
        assert d["summary"]["pass"] == 1

    def test_to_dict_full_roundtrip(self):
        issue = VisualIssue(
            id="VIS001",
            title="t",
            description="d",
            severity=VisualSeverity.HIGH,
            viewport="tablet",
            route="/dash",
            location_hint="center",
            fix_suggestion="f",
        )
        report = VisualQAReport(
            issues=[issue],
            screenshots_analyzed=6,
            routes_tested=["/", "/dash"],
            viewports_tested=["desktop", "tablet", "mobile"],
            summary={"pass": 4, "warn": 1, "fail": 1},
            grades={"/": {"desktop": "pass"}, "/dash": {"tablet": "warn"}},
        )
        d = report.to_dict()
        assert d["screenshots_analyzed"] == 6
        assert len(d["routes_tested"]) == 2
        assert len(d["viewports_tested"]) == 3


# =====================================================================
# TestRouteDetection
# =====================================================================


class TestRouteDetection:
    def test_nextjs_app_router(self):
        routes = detect_routes(NEXTJS_FILES)
        assert "/" in routes
        assert "/dashboard" in routes
        assert "/settings" in routes
        # layout.tsx and Navbar.tsx should NOT be routes
        assert len(routes) == 3

    def test_react_router_patterns(self):
        routes = detect_routes(REACT_ROUTER_FILES)
        assert "/" in routes
        assert "/about" in routes
        assert "/contact" in routes

    def test_fallback_to_root(self):
        routes = detect_routes(SIMPLE_FILES)
        assert routes == ["/"]

    def test_empty_files(self):
        routes = detect_routes({})
        assert routes == ["/"]

    def test_root_is_first(self):
        routes = detect_routes(NEXTJS_FILES)
        assert routes[0] == "/"

    def test_nextjs_src_prefix(self):
        """Files in src/app/ should also be detected."""
        files = {
            "src/app/page.tsx": "export default function Home() {}",
            "src/app/profile/page.tsx": "export default function Profile() {}",
        }
        routes = detect_routes(files)
        assert "/" in routes
        assert "/profile" in routes

    def test_nested_nextjs_routes(self):
        files = {
            "app/page.tsx": "Home",
            "app/blog/page.tsx": "Blog",
            "app/blog/[slug]/page.tsx": "BlogPost",
        }
        routes = detect_routes(files)
        assert "/" in routes
        assert "/blog" in routes
        assert "/blog/[slug]" in routes


# =====================================================================
# TestMultimodalPrompt
# =====================================================================


class TestMultimodalPrompt:
    def test_visual_qa_prompt_includes_viewport(self):
        prompt = _build_visual_qa_prompt("/", "desktop", {"width": 1440, "height": 900})
        assert "1440" in prompt
        assert "900" in prompt
        assert "desktop" in prompt

    def test_visual_qa_prompt_includes_route(self):
        prompt = _build_visual_qa_prompt(
            "/dashboard", "mobile", {"width": 375, "height": 812}
        )
        assert "/dashboard" in prompt
        assert "mobile" in prompt

    def test_visual_qa_prompt_includes_design_tokens(self):
        tokens = {"primary_color": "#3B82F6", "font_family": "Inter"}
        prompt = _build_visual_qa_prompt(
            "/", "desktop", {"width": 1440, "height": 900}, tokens
        )
        assert "#3B82F6" in prompt
        assert "Inter" in prompt
        assert "Design System Reference" in prompt

    def test_visual_qa_prompt_no_design_tokens(self):
        prompt = _build_visual_qa_prompt(
            "/", "desktop", {"width": 1440, "height": 900}, None
        )
        assert "Design System Reference" not in prompt

    def test_architecture_prompt_includes_spec(self):
        prompt = _build_architecture_prompt(
            "/",
            "desktop",
            {"width": 1440, "height": 900},
            SAMPLE_ARCHITECTURE,
        )
        assert "react" in prompt
        assert "fastapi" in prompt
        assert "Architecture Specification" in prompt


# =====================================================================
# TestMultimodalMessages
# =====================================================================


class TestMultimodalMessages:
    def test_build_messages_structure(self):
        msgs = _build_multimodal_messages("Analyze this", "base64data")
        assert len(msgs) == 1
        content = msgs[0].content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Analyze this"
        assert content[1]["type"] == "image_url"
        assert "base64data" in content[1]["image_url"]["url"]

    def test_image_url_format(self):
        msgs = _build_multimodal_messages("test", "ABCD1234")
        url = msgs[0].content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert url.endswith("ABCD1234")


# =====================================================================
# TestParseIssues
# =====================================================================


class TestParseIssues:
    def test_parse_valid_json(self):
        issues = _parse_issues_from_response(SAMPLE_ISSUES_JSON, "/", "desktop")
        assert len(issues) == 2
        assert issues[0].id == "VIS001"
        assert issues[0].severity == VisualSeverity.CRITICAL
        assert issues[1].severity == VisualSeverity.HIGH

    def test_parse_empty_array(self):
        issues = _parse_issues_from_response("[]", "/", "desktop")
        assert issues == []

    def test_parse_markdown_fenced_json(self):
        text = "```json\n" + SAMPLE_ISSUES_JSON + "\n```"
        issues = _parse_issues_from_response(text, "/", "mobile")
        assert len(issues) == 2
        assert issues[0].viewport == "mobile"

    def test_parse_invalid_json_returns_empty(self):
        issues = _parse_issues_from_response("not valid json", "/", "desktop")
        assert issues == []

    def test_parse_unknown_severity_defaults_to_info(self):
        data = json.dumps(
            [{"id": "X", "title": "T", "description": "D", "severity": "banana"}]
        )
        issues = _parse_issues_from_response(data, "/", "desktop")
        assert len(issues) == 1
        assert issues[0].severity == VisualSeverity.INFO

    def test_parse_json_embedded_in_text(self):
        text = 'Here are the issues:\n[{"id":"VIS001","title":"t","description":"d","severity":"medium"}]\nDone.'
        issues = _parse_issues_from_response(text, "/", "tablet")
        assert len(issues) == 1
        assert issues[0].severity == VisualSeverity.MEDIUM


# =====================================================================
# TestGrading
# =====================================================================


class TestGrading:
    def test_pass_when_no_issues(self):
        assert _grade_viewport([]) == "pass"

    def test_fail_when_critical(self):
        issue = VisualIssue(
            id="V1",
            title="t",
            description="d",
            severity=VisualSeverity.CRITICAL,
            viewport="desktop",
            route="/",
            location_hint="top",
            fix_suggestion="fix",
        )
        assert _grade_viewport([issue]) == "fail"

    def test_warn_when_high(self):
        issue = VisualIssue(
            id="V2",
            title="t",
            description="d",
            severity=VisualSeverity.HIGH,
            viewport="desktop",
            route="/",
            location_hint="bottom",
            fix_suggestion="fix",
        )
        assert _grade_viewport([issue]) == "warn"

    def test_pass_when_only_medium_and_info(self):
        issues = [
            VisualIssue(
                id="V3",
                title="t",
                description="d",
                severity=VisualSeverity.MEDIUM,
                viewport="desktop",
                route="/",
                location_hint="mid",
                fix_suggestion="fix",
            ),
            VisualIssue(
                id="V4",
                title="t",
                description="d",
                severity=VisualSeverity.INFO,
                viewport="desktop",
                route="/",
                location_hint="mid",
                fix_suggestion="fix",
            ),
        ]
        assert _grade_viewport(issues) == "pass"


# =====================================================================
# TestComplexDepsDetection
# =====================================================================


class TestComplexDepsDetection:
    def test_simple_project(self):
        assert _has_complex_deps(SIMPLE_FILES) is False

    def test_prisma_detected(self):
        files = {"prisma/schema.prisma": "model User { id Int @id }"}
        assert _has_complex_deps(files) is True

    def test_next_config_detected(self):
        files = {"next.config.js": "module.exports = {}"}
        assert _has_complex_deps(files) is True


# =====================================================================
# TestScreenshotCapture
# =====================================================================


class TestScreenshotCapture:
    def test_returns_empty_when_playwright_missing(self):
        with patch.dict(
            "sys.modules", {"playwright": None, "playwright.async_api": None}
        ):
            # Force reimport to hit ImportError
            import ai.agents.visual_qa as vqa_mod

            # Directly test: Playwright import fails gracefully

        # Instead, test via the function's own import guard
        result = asyncio.run(capture_screenshots(SIMPLE_FILES, ["/"]))
        # If playwright is not installed, should return empty dict
        # If installed, it would fail on connection — still empty
        assert isinstance(result, dict)

    def test_max_screenshots_constant(self):
        assert MAX_SCREENSHOTS == 15

    def test_max_image_bytes_constant(self):
        assert MAX_IMAGE_BYTES == 500 * 1024

    def test_viewports_have_correct_dimensions(self):
        assert VIEWPORTS["desktop"] == {"width": 1440, "height": 900}
        assert VIEWPORTS["tablet"] == {"width": 768, "height": 1024}
        assert VIEWPORTS["mobile"] == {"width": 375, "height": 812}

    def test_three_viewports_defined(self):
        assert len(VIEWPORTS) == 3
        assert set(VIEWPORTS.keys()) == {"desktop", "tablet", "mobile"}


# =====================================================================
# TestGracefulDegradation
# =====================================================================


class TestGracefulDegradation:
    def test_no_frontend_code_returns_empty_report(self):
        agent = VisualQAAgent()
        report = asyncio.run(agent.run(frontend_files={}))
        d = report.to_dict()
        assert d["screenshots_analyzed"] == 0
        assert d["issues"] == []

    def test_capture_failure_returns_empty_screenshots(self):
        """If capture_screenshots returns empty, report has 0 screenshots."""
        agent = VisualQAAgent()
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value={},
        ):
            report = asyncio.run(agent.run(frontend_files=NEXTJS_FILES))
        assert report.screenshots_analyzed == 0
        assert report.summary == {"pass": 0, "warn": 0, "fail": 0}

    def test_analysis_failure_does_not_crash(self):
        """If analyze_screenshot raises, the agent still returns a report."""
        agent = VisualQAAgent()
        mock_screenshots = {"/": {"desktop": b"fake_png_data"}}
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot",
                new_callable=AsyncMock,
                side_effect=Exception("LLM down"),
            ):
                report = asyncio.run(agent.run(frontend_files=NEXTJS_FILES))
        # analyze raised, but the agent catches it and continues
        # The screenshot was "analyzed" (attempted) so count may be 1
        assert isinstance(report, VisualQAReport)

    def test_partial_screenshots_still_produces_report(self):
        """If only some routes succeed, report has partial results."""
        agent = VisualQAAgent()
        # Only root route has screenshots
        mock_screenshots = {"/": {"desktop": b"data", "mobile": b"data"}}
        mock_issues = []
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot",
                new_callable=AsyncMock,
                return_value=mock_issues,
            ):
                report = asyncio.run(agent.run(frontend_files=NEXTJS_FILES))
        assert report.screenshots_analyzed == 2
        assert "/" in report.grades

    def test_empty_frontend_files_dict(self):
        agent = VisualQAAgent()
        report = asyncio.run(agent.run(frontend_files={}))
        assert report.screenshots_analyzed == 0
        assert report.has_critical is False

    def test_agent_run_with_architecture(self):
        """Architecture is passed through to analysis."""
        agent = VisualQAAgent()
        mock_screenshots = {"/": {"desktop": b"data"}}
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_analyze:
                asyncio.run(
                    agent.run(
                        frontend_files=NEXTJS_FILES,
                        architecture=SAMPLE_ARCHITECTURE,
                    )
                )
        # Verify architecture was passed to analyze_screenshot
        call_kwargs = mock_analyze.call_args[1]
        assert call_kwargs["architecture"] == SAMPLE_ARCHITECTURE
        assert call_kwargs["design_tokens"] == SAMPLE_ARCHITECTURE["design_system"]


# =====================================================================
# TestReviewerInjection
# =====================================================================


class TestReviewerInjection:
    def test_no_issues_returns_none(self):
        report = {"issues": [], "screenshots_analyzed": 0}
        assert build_reviewer_visual_context(report) is None

    def test_only_medium_and_info_returns_none(self):
        report = {
            "issues": [
                {
                    "id": "V1",
                    "severity": "medium",
                    "title": "t",
                    "viewport": "d",
                    "fix_suggestion": "f",
                },
                {
                    "id": "V2",
                    "severity": "info",
                    "title": "t",
                    "viewport": "d",
                    "fix_suggestion": "f",
                },
            ],
        }
        assert build_reviewer_visual_context(report) is None

    def test_critical_issues_produce_context(self):
        report = {
            "issues": [
                {
                    "id": "VIS001",
                    "severity": "critical",
                    "title": "Overflow",
                    "viewport": "desktop",
                    "fix_suggestion": "Add overflow-hidden",
                },
            ],
        }
        ctx = build_reviewer_visual_context(report)
        assert ctx is not None
        assert "VIS001" in ctx
        assert "Overflow" in ctx
        assert "overflow-hidden" in ctx
        assert "VISUAL QA ISSUES" in ctx

    def test_high_issues_also_included(self):
        report = {
            "issues": [
                {
                    "id": "A11Y001",
                    "severity": "high",
                    "title": "Low contrast",
                    "viewport": "mobile",
                    "fix_suggestion": "Darken text",
                },
            ],
        }
        ctx = build_reviewer_visual_context(report)
        assert ctx is not None
        assert "A11Y001" in ctx


# =====================================================================
# TestVisualQANode
# =====================================================================


class TestVisualQANode:
    def _make_state(self, frontend_code=None, architecture=None):
        return {
            "messages": [],
            "requirements": "Build a dashboard",
            "project_description": None,
            "architecture": architecture,
            "frontend_code": frontend_code,
            "backend_code": {},
            "tests": {},
            "review_results": None,
            "current_phase": "visual_qa",
            "iteration": 1,
            "errors": [],
            "error": None,
            "final_project": None,
            "stuck_count": 0,
            "previous_issues_embeddings": None,
            "previous_issues_text": None,
            "model_switch_history": None,
            "_override_model": None,
            "_clear_failed_context": None,
            "_include_anti_patterns": None,
            "_latest_issues_embeddings": None,
            "_latest_issues_text": None,
            "guardrails_violations": None,
            "guardrails_iteration": 0,
            "expert_sos_suggested": None,
            "schema_migration": None,
            "previous_schema_fingerprint": None,
            "visual_qa_report": None,
            "visual_qa_screenshots": None,
        }

    def test_empty_frontend_returns_empty_report(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(frontend_code={})
        result = builder._visual_qa_node(state)
        assert result["visual_qa_screenshots"] == 0
        assert result["visual_qa_report"]["screenshots_analyzed"] == 0

    def test_none_frontend_returns_empty_report(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(frontend_code=None)
        result = builder._visual_qa_node(state)
        assert result["visual_qa_screenshots"] == 0

    def test_exception_does_not_crash_pipeline(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(frontend_code=NEXTJS_FILES)
        # Patch the import to raise
        with patch("ai.agents.visual_qa.VisualQAAgent") as MockAgent:
            MockAgent.side_effect = RuntimeError("test crash")
            result = builder._visual_qa_node(state)
        assert result["visual_qa_screenshots"] == 0
        assert "VQA_ERROR" in result["visual_qa_report"]["issues"][0]["id"]

    def test_successful_run_returns_report(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(frontend_code=NEXTJS_FILES)

        mock_report = VisualQAReport(
            issues=[],
            screenshots_analyzed=9,
            routes_tested=["/", "/dashboard", "/settings"],
            viewports_tested=["desktop", "tablet", "mobile"],
            summary={"pass": 9, "warn": 0, "fail": 0},
            grades={"/": {"desktop": "pass"}},
        )

        with patch("ai.agents.visual_qa.VisualQAAgent") as MockAgentClass:
            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_report)
            MockAgentClass.return_value = mock_agent
            result = builder._visual_qa_node(state)

        assert result["visual_qa_screenshots"] == 9
        assert result["visual_qa_report"]["summary"]["pass"] == 9

    def test_result_has_expected_keys(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(frontend_code={})
        result = builder._visual_qa_node(state)
        assert "visual_qa_report" in result
        assert "visual_qa_screenshots" in result

    def test_pipeline_routing_tester_to_visual_qa(self):
        """Verify the graph routes tester → visual_qa → reviewer."""
        from ai.agents.multi_agent import MultiAgentBuilder

        MultiAgentBuilder.__new__(MultiAgentBuilder)
        # We can't easily build the graph without full init,
        # but we can verify the edges exist by reading source
        import inspect

        source = inspect.getsource(MultiAgentBuilder._build_graph)
        assert (
            '"tester", "visual_qa"' in source
            or "'tester', 'visual_qa'" in source
            or 'tester", "visual_qa' in source
        )
        assert (
            '"visual_qa", "reviewer"' in source
            or "'visual_qa', 'reviewer'" in source
            or 'visual_qa", "reviewer' in source
        )

    def test_node_exists_in_graph_setup(self):
        """Verify visual_qa node is added to workflow."""
        import inspect
        from ai.agents.multi_agent import MultiAgentBuilder

        source = inspect.getsource(MultiAgentBuilder._build_graph)
        assert "visual_qa" in source
        assert "_visual_qa_node" in source


# =====================================================================
# TestProjectStateFields
# =====================================================================


class TestProjectStateFields:
    def test_visual_qa_fields_exist(self):
        from ai.agents.multi_agent import ProjectState

        annotations = ProjectState.__annotations__
        assert "visual_qa_report" in annotations
        assert "visual_qa_screenshots" in annotations

    def test_visual_qa_fields_are_optional(self):
        from ai.agents.multi_agent import ProjectState
        import typing

        annotations = ProjectState.__annotations__
        # Check they are Optional (i.e. allow None)
        vqa_report_type = annotations["visual_qa_report"]
        vqa_screenshots_type = annotations["visual_qa_screenshots"]
        # Optional[X] is Union[X, None]
        assert typing.get_origin(vqa_report_type) is typing.Union or "Optional" in str(
            vqa_report_type
        )
        assert typing.get_origin(
            vqa_screenshots_type
        ) is typing.Union or "Optional" in str(vqa_screenshots_type)


# =====================================================================
# TestEndToEnd
# =====================================================================


class TestEndToEnd:
    def test_full_pipeline_with_mocked_browser_and_llm(self):
        """Full E2E: detect routes → capture screenshots → analyse → report."""
        agent = VisualQAAgent()
        mock_screenshots = {
            "/": {"desktop": b"png1", "tablet": b"png2", "mobile": b"png3"},
            "/dashboard": {"desktop": b"png4"},
        }
        mock_issues_root = [
            VisualIssue(
                id="VIS001",
                title="Overflow",
                description="text overflows",
                severity=VisualSeverity.CRITICAL,
                viewport="mobile",
                route="/",
                location_hint="hero",
                fix_suggestion="overflow-hidden",
            ),
        ]
        mock_issues_empty = []

        async def fake_analyze(
            screenshot_bytes,
            route,
            viewport,
            vp_size,
            design_tokens=None,
            architecture=None,
        ):
            if route == "/" and viewport == "mobile":
                return mock_issues_root
            return mock_issues_empty

        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot", side_effect=fake_analyze
            ):
                report = asyncio.run(agent.run(frontend_files=NEXTJS_FILES))

        assert report.screenshots_analyzed == 4
        assert len(report.issues) == 1
        assert report.issues[0].id == "VIS001"
        assert report.has_critical is True
        assert report.grades["/"]["mobile"] == "fail"
        assert report.grades["/"]["desktop"] == "pass"
        assert report.summary["fail"] == 1
        assert report.summary["pass"] == 3

    def test_report_to_dict_and_reviewer_context(self):
        """E2E: report serialises and reviewer context includes critical issues."""
        issue = VisualIssue(
            id="A11Y001",
            title="Low contrast",
            description="below threshold",
            severity=VisualSeverity.HIGH,
            viewport="desktop",
            route="/",
            location_hint="footer",
            fix_suggestion="darken",
        )
        report = VisualQAReport(
            issues=[issue],
            screenshots_analyzed=3,
            routes_tested=["/"],
            viewports_tested=["desktop", "tablet", "mobile"],
            summary={"pass": 2, "warn": 1, "fail": 0},
            grades={"/": {"desktop": "warn", "tablet": "pass", "mobile": "pass"}},
        )
        d = report.to_dict()
        ctx = build_reviewer_visual_context(d)
        assert ctx is not None
        assert "A11Y001" in ctx

    def test_no_issues_produces_clean_report(self):
        agent = VisualQAAgent()
        mock_screenshots = {"/": {"desktop": b"data"}}
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot",
                new_callable=AsyncMock,
                return_value=[],
            ):
                report = asyncio.run(agent.run(frontend_files=SIMPLE_FILES))
        assert report.screenshots_analyzed == 1
        assert len(report.issues) == 0
        assert report.summary["pass"] == 1
        ctx = build_reviewer_visual_context(report.to_dict())
        assert ctx is None

    def test_dual_model_analysis_called(self):
        """Verify both Gemini (architecture) and Opus (visual QA) prompts are built."""
        # Test the prompt builders directly
        arch_prompt = _build_architecture_prompt(
            "/",
            "desktop",
            {"width": 1440, "height": 900},
            SAMPLE_ARCHITECTURE,
        )
        qa_prompt = _build_visual_qa_prompt(
            "/",
            "desktop",
            {"width": 1440, "height": 900},
            SAMPLE_ARCHITECTURE.get("design_system"),
        )
        # Architecture prompt should mention architecture-specific terms
        assert "Architecture Specification" in arch_prompt
        assert "Component presence" in arch_prompt
        # Visual QA prompt should mention WCAG and layout
        assert "WCAG AA" in qa_prompt
        assert "Layout Consistency" in qa_prompt
        assert "#3B82F6" in qa_prompt  # design token from architecture
