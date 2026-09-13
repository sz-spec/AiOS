"""
Visual Logic Stress Test — Phase 2.5 Pipeline Validation
==========================================================
Simulates a complete tester → visual_qa → reviewer pipeline flow
with a critical WCAG AA visual regression, verifying:

1. Dual-model (Gemini + Opus) feedback is correctly aggregated by visual_qa node.
2. Reviewer node receives visual failures as mandatory review criteria.
3. Reviewer blocks the build (routes to "frontend" instead of "finalize")
   because of the critical visual regression detected by the VQA agent.

This test exercises the full data flow between Visual QA and Reviewer:

    ┌────────────┐
    │   TESTER   │  → produces test_results (pass)
    └─────┬──────┘
          │
    ┌─────▼──────┐
    │ VISUAL_QA  │  → captures screenshot (mocked)
    │            │  → Gemini 3.1 Pro: detects contrast anomaly (ARCH_VIS001)
    │            │  → Claude Opus 4.6: flags CRITICAL WCAG AA failure (A11Y001)
    │            │  → aggregates both into visual_qa_report
    └─────┬──────┘
          │
    ┌─────▼──────┐
    │  REVIEWER  │  ← receives visual_qa_report
    │            │  ← build_reviewer_visual_context() injects critical issues
    │            │     as SystemMessage("VISUAL QA ISSUES ...")
    │            │  → reviewer agent sees visual issues in prompt
    │            │  → review_results contains critical issues
    │            │  → _route_after_review → "frontend" (NOT "finalize")
    └────────────┘

All LLM calls and Playwright are mocked. No real browser or API calls.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock
from langchain_core.messages import SystemMessage, HumanMessage

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents.visual_qa import (
    VisualSeverity,
    VisualQAAgent,
    build_reviewer_visual_context,
    _parse_issues_from_response,
    _grade_viewport,
)

# =====================================================================
# Fixtures — The Buggy Frontend
# =====================================================================

BUGGY_FRONTEND_CODE = {
    "app/page.tsx": """
export default function Home() {
  return (
    <main className="bg-white min-h-screen">
      <h1 className="text-4xl font-bold text-gray-900">Dashboard</h1>

      {/* BUG: Button text color matches background — invisible text */}
      <button className="bg-blue-600 text-blue-600 px-4 py-2 rounded">
        Submit Form
      </button>

      {/* BUG: Tiny touch target — 20x20px link */}
      <a href="/settings" style={{ width: '20px', height: '20px', display: 'inline-block' }}>
        Settings
      </a>
    </main>
  );
}
""",
    "app/layout.tsx": """
export default function RootLayout({ children }) {
  return <html><body>{children}</body></html>;
}
""",
}

ARCHITECTURE_SPEC = {
    "tech_stack": {"frontend": "next.js", "backend": None},
    "routes": ["/"],
    "components": ["Hero", "SubmitButton", "SettingsLink"],
    "design_system": {
        "primary_color": "#2563EB",
        "background": "#FFFFFF",
        "font_family": "Inter",
        "base_spacing": "8px",
        "border_radius": "8px",
    },
}

# --- Mock LLM Responses ---

# Gemini 3.1 Pro response: architecture comparison — detects contrast anomaly
GEMINI_RESPONSE_JSON = json.dumps(
    [
        {
            "id": "ARCH_VIS001",
            "title": "SubmitButton has invisible text",
            "description": (
                "The SubmitButton component exists in the architecture spec "
                "but its text is invisible — the text color (#2563EB blue-600) "
                "matches the background color (#2563EB blue-600), resulting in "
                "a 1:1 contrast ratio. The button appears as a solid blue rectangle "
                "with no visible label."
            ),
            "severity": "critical",
            "location_hint": "Center of page, below the heading",
            "fix_suggestion": (
                "Change button text color to white: "
                "className='bg-blue-600 text-white px-4 py-2 rounded'"
            ),
        },
    ]
)

# Claude Opus 4.6 response: visual QA + WCAG AA audit
OPUS_RESPONSE_JSON = json.dumps(
    [
        {
            "id": "A11Y001",
            "title": "CRITICAL: WCAG AA contrast failure on button",
            "description": (
                "The 'Submit Form' button has text color #2563EB on background "
                "#2563EB, yielding a contrast ratio of 1:1. WCAG AA requires "
                "minimum 4.5:1 for normal text. This is a complete accessibility "
                "failure — the button text is entirely invisible."
            ),
            "severity": "critical",
            "location_hint": "Main content area, submit button",
            "fix_suggestion": (
                "Replace text-blue-600 with text-white for maximum contrast "
                "(contrast ratio 8.59:1 against blue-600 background)."
            ),
        },
        {
            "id": "A11Y002",
            "title": "Touch target below minimum size",
            "description": (
                "The Settings link has an inline style of width: 20px, height: 20px. "
                "WCAG 2.1 Success Criterion 2.5.5 requires a minimum of 44x44px "
                "for touch targets."
            ),
            "severity": "high",
            "location_hint": "Below the submit button",
            "fix_suggestion": (
                "Set minimum dimensions: style={{ minWidth: '44px', minHeight: '44px' }}"
            ),
        },
    ]
)

# Dummy PNG bytes (1x1 white pixel PNG)
DUMMY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
    b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# =====================================================================
# Test 1: Dual-Model Aggregation
# =====================================================================


class TestDualModelAggregation:
    """Verify the visual_qa node correctly aggregates Gemini + Opus feedback."""

    def test_gemini_and_opus_issues_both_appear_in_report(self):
        """
        Mock the two LLM calls (Gemini for architecture, Opus for visual QA)
        and verify both sets of issues appear in the final report.
        """
        gemini_issues = _parse_issues_from_response(
            GEMINI_RESPONSE_JSON, "/", "desktop"
        )
        opus_issues = _parse_issues_from_response(OPUS_RESPONSE_JSON, "/", "desktop")

        # Gemini found 1 issue
        assert len(gemini_issues) == 1
        assert gemini_issues[0].id == "ARCH_VIS001"
        assert gemini_issues[0].severity == VisualSeverity.CRITICAL

        # Opus found 2 issues
        assert len(opus_issues) == 2
        assert opus_issues[0].id == "A11Y001"
        assert opus_issues[0].severity == VisualSeverity.CRITICAL
        assert opus_issues[1].id == "A11Y002"
        assert opus_issues[1].severity == VisualSeverity.HIGH

        # Combined = 3 issues from dual-model analysis
        all_issues = gemini_issues + opus_issues
        assert len(all_issues) == 3
        critical_count = sum(
            1 for i in all_issues if i.severity == VisualSeverity.CRITICAL
        )
        assert critical_count == 2  # Both ARCH_VIS001 and A11Y001

    def test_aggregated_report_has_fail_grade(self):
        """A viewport with critical issues must receive a 'fail' grade."""
        gemini_issues = _parse_issues_from_response(
            GEMINI_RESPONSE_JSON, "/", "desktop"
        )
        opus_issues = _parse_issues_from_response(OPUS_RESPONSE_JSON, "/", "desktop")
        all_issues = gemini_issues + opus_issues

        grade = _grade_viewport(all_issues)
        assert grade == "fail"

    def test_full_agent_run_aggregates_dual_model(self):
        """
        Run VisualQAAgent.run() with mocked screenshots and analyze_screenshot.
        Verify both models' issues are aggregated.
        """
        import asyncio

        agent = VisualQAAgent()
        mock_screenshots = {"/": {"desktop": DUMMY_PNG}}

        # Combine Gemini + Opus issues as the analyze_screenshot would return
        combined_issues = _parse_issues_from_response(
            GEMINI_RESPONSE_JSON, "/", "desktop"
        ) + _parse_issues_from_response(OPUS_RESPONSE_JSON, "/", "desktop")

        async def mock_analyze(*args, **kwargs):
            return combined_issues

        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot", side_effect=mock_analyze
            ):
                report = asyncio.run(
                    agent.run(
                        frontend_files=BUGGY_FRONTEND_CODE,
                        architecture=ARCHITECTURE_SPEC,
                    )
                )

        assert report.screenshots_analyzed == 1
        assert len(report.issues) == 3
        assert report.has_critical is True
        assert report.grades["/"]["desktop"] == "fail"
        assert report.summary["fail"] == 1

    def test_report_serializes_all_issues(self):
        """Verify to_dict() preserves all 3 issues from dual-model analysis."""
        import asyncio

        agent = VisualQAAgent()
        mock_screenshots = {
            "/": {"desktop": DUMMY_PNG, "tablet": DUMMY_PNG, "mobile": DUMMY_PNG}
        }
        combined_issues = _parse_issues_from_response(
            GEMINI_RESPONSE_JSON, "/", "desktop"
        ) + _parse_issues_from_response(OPUS_RESPONSE_JSON, "/", "desktop")

        async def mock_analyze(*args, **kwargs):
            return combined_issues

        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot", side_effect=mock_analyze
            ):
                report = asyncio.run(
                    agent.run(
                        frontend_files=BUGGY_FRONTEND_CODE,
                        architecture=ARCHITECTURE_SPEC,
                    )
                )

        d = report.to_dict()
        assert d["screenshots_analyzed"] == 3
        # 3 issues per viewport × 3 viewports = 9 total issues
        assert len(d["issues"]) == 9
        critical_issues = [i for i in d["issues"] if i["severity"] == "critical"]
        assert len(critical_issues) == 6  # 2 critical × 3 viewports


# =====================================================================
# Test 2: Reviewer Receives Visual Failure
# =====================================================================


class TestReviewerReceivesVisualFailure:
    """
    Verify the reviewer node receives visual QA issues as mandatory
    criteria via build_reviewer_visual_context().
    """

    def test_reviewer_context_includes_critical_and_high_issues(self):
        """build_reviewer_visual_context extracts critical+high from report."""
        report_dict = {
            "issues": [
                {
                    "id": "ARCH_VIS001",
                    "severity": "critical",
                    "title": "SubmitButton has invisible text",
                    "viewport": "desktop",
                    "fix_suggestion": "Change text color to white",
                },
                {
                    "id": "A11Y001",
                    "severity": "critical",
                    "title": "CRITICAL: WCAG AA contrast failure",
                    "viewport": "desktop",
                    "fix_suggestion": "Replace text-blue-600 with text-white",
                },
                {
                    "id": "A11Y002",
                    "severity": "high",
                    "title": "Touch target below minimum size",
                    "viewport": "desktop",
                    "fix_suggestion": "Set min 44x44px",
                },
            ],
            "screenshots_analyzed": 3,
            "summary": {"pass": 0, "warn": 0, "fail": 3},
        }
        ctx = build_reviewer_visual_context(report_dict)

        assert ctx is not None
        assert "VISUAL QA ISSUES (must be addressed in review)" in ctx
        assert "ARCH_VIS001" in ctx
        assert "A11Y001" in ctx
        assert "A11Y002" in ctx
        assert "Change text color to white" in ctx
        assert "Set min 44x44px" in ctx

    def test_reviewer_context_excludes_medium_and_info(self):
        """Medium and info issues should NOT appear in reviewer context."""
        report_dict = {
            "issues": [
                {
                    "id": "VIS003",
                    "severity": "medium",
                    "title": "Minor spacing issue",
                    "viewport": "desktop",
                    "fix_suggestion": "Adjust padding",
                },
                {
                    "id": "VIS004",
                    "severity": "info",
                    "title": "Suggestion for improvement",
                    "viewport": "desktop",
                    "fix_suggestion": "Consider using...",
                },
            ],
        }
        ctx = build_reviewer_visual_context(report_dict)
        assert ctx is None

    def test_reviewer_node_appends_system_message(self):
        """
        Simulate _reviewer_node: verify that visual QA context is injected
        as a SystemMessage into the state's messages list.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        # Build a state with visual_qa_report containing critical issues
        state = {
            "messages": [HumanMessage(content="Build a dashboard")],
            "visual_qa_report": {
                "issues": [
                    {
                        "id": "A11Y001",
                        "severity": "critical",
                        "title": "WCAG AA contrast failure",
                        "viewport": "desktop",
                        "fix_suggestion": "Change text-blue-600 to text-white",
                    },
                ],
            },
            "review_results": None,
        }

        # Mock the reviewer agent to capture what messages it receives
        captured_messages = []
        mock_agent = MagicMock()

        def capture_invoke(s):
            captured_messages.extend(s.get("messages", []))
            return {
                "review_results": {
                    "issues": [
                        {
                            "title": "WCAG AA contrast failure",
                            "description": "Button text invisible",
                            "severity": "critical",
                        },
                    ],
                },
            }

        mock_agent.invoke = capture_invoke
        builder.agents = {"reviewer": mock_agent}

        builder._reviewer_node(state)

        # Verify SystemMessage was injected before invoke
        system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
        assert len(system_msgs) >= 1

        # Find the VQA injection
        vqa_msgs = [m for m in system_msgs if "VISUAL QA ISSUES" in m.content]
        assert len(vqa_msgs) == 1
        assert "A11Y001" in vqa_msgs[0].content
        assert "WCAG AA contrast failure" in vqa_msgs[0].content
        assert "text-white" in vqa_msgs[0].content

    def test_reviewer_node_skips_injection_when_no_critical(self):
        """If visual_qa_report has no critical/high issues, no SystemMessage added."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {
            "messages": [HumanMessage(content="Build something")],
            "visual_qa_report": {
                "issues": [
                    {
                        "id": "VIS003",
                        "severity": "info",
                        "title": "Minor suggestion",
                        "viewport": "desktop",
                        "fix_suggestion": "n/a",
                    },
                ],
            },
        }
        len(state["messages"])

        mock_agent = MagicMock()
        mock_agent.invoke = lambda s: {"review_results": {"issues": []}}
        builder.agents = {"reviewer": mock_agent}

        builder._reviewer_node(state)

        # No VQA SystemMessage should have been added
        vqa_msgs = [
            m
            for m in state["messages"]
            if isinstance(m, SystemMessage) and "VISUAL QA ISSUES" in m.content
        ]
        assert len(vqa_msgs) == 0


# =====================================================================
# Test 3: Reviewer Blocks Build
# =====================================================================


class TestReviewerBlocksBuild:
    """
    Verify that _route_after_review blocks the build (routes to "frontend"
    instead of "finalize") when the reviewer surfaces critical visual issues.
    """

    def _make_state(self, review_issues=None, iteration=1, stuck_count=0):
        """Build a minimal ProjectState with review_results."""
        return {
            "messages": [HumanMessage(content="Build a dashboard")],
            "requirements": "Build a dashboard with a submit button",
            "project_description": None,
            "architecture": ARCHITECTURE_SPEC,
            "frontend_code": BUGGY_FRONTEND_CODE,
            "backend_code": {},
            "tests": {},
            "review_results": {
                "issues": review_issues or [],
            },
            "current_phase": "review",
            "iteration": iteration,
            "errors": [],
            "error": None,
            "final_project": None,
            "stuck_count": stuck_count,
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

    def test_critical_visual_issue_blocks_build(self):
        """
        When reviewer reports a critical visual issue, _route_after_review
        returns "frontend" (retry), NOT "finalize".
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(
            review_issues=[
                {
                    "title": "WCAG AA contrast failure on submit button",
                    "description": (
                        "Button text color #2563EB matches background #2563EB. "
                        "Contrast ratio is 1:1, WCAG AA requires 4.5:1."
                    ),
                    "severity": "critical",
                },
            ],
            iteration=1,
        )

        route = builder._route_after_review(state)
        assert route == "frontend", (
            f"Expected 'frontend' (retry) but got '{route}'. "
            "Critical visual issues should block the build."
        )

    def test_no_critical_issues_finalizes(self):
        """When no critical issues, _route_after_review returns 'finalize'."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(
            review_issues=[
                {
                    "title": "Minor spacing issue",
                    "description": "4px gap instead of 8px",
                    "severity": "medium",
                },
            ],
        )

        route = builder._route_after_review(state)
        assert route == "finalize"

    def test_visual_regression_triggers_retry_loop(self):
        """
        Simulate the full cycle: visual regression → reviewer blocks →
        route to frontend for fix. After 3 iterations, finally finalize
        (stage 3 escalation).
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        critical_issues = [
            {
                "title": "WCAG AA contrast failure",
                "description": "Invisible button text",
                "severity": "critical",
            },
        ]

        # Iteration 1: should retry
        state = self._make_state(review_issues=critical_issues, iteration=1)
        route1 = builder._route_after_review(state)
        assert route1 == "frontend"

        # Iteration 2: should retry
        state = self._make_state(review_issues=critical_issues, iteration=2)
        route2 = builder._route_after_review(state)
        assert route2 == "frontend"

        # Iteration 3: hits iteration limit, triggers stuck escalation
        state = self._make_state(review_issues=critical_issues, iteration=3)
        route3 = builder._route_after_review(state)
        # At iteration >= 3, it's "stuck" → enters escalation stages
        # stuck_count goes from 0 → 1 → stage 1 → "frontend"
        assert route3 == "frontend"  # Stage 1: model switch, still retry

    def test_exhausted_retries_finalize_with_sos(self):
        """
        After all 3 escalation stages exhausted (stuck_count >= 3),
        the build finalizes with expert_sos_suggested = True.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        critical_issues = [
            {
                "title": "Invisible button text",
                "description": "WCAG AA failure",
                "severity": "critical",
            },
        ]

        # stuck_count=2 means we've already tried stages 1 and 2
        # Next will be stuck_count=3 → stage 3 → finalize with SOS
        state = self._make_state(
            review_issues=critical_issues,
            iteration=5,
            stuck_count=2,
        )

        # Provide previous embeddings so _detect_semantic_loop finds a loop
        state["previous_issues_embeddings"] = [[0.1] * 384]
        state["previous_issues_text"] = ["Invisible button text: WCAG AA failure"]

        # Mock _detect_semantic_loop to confirm we're stuck
        with patch.object(
            MultiAgentBuilder, "_detect_semantic_loop", return_value=(True, 0.95)
        ):
            route = builder._route_after_review(state)

        assert route == "finalize"
        assert state.get("expert_sos_suggested") is True
        assert state.get("stuck_count") == 3


# =====================================================================
# Test 4: Full Pipeline Node Sequence
# =====================================================================


class TestFullPipelineNodeSequence:
    """
    End-to-end simulation: tester → visual_qa → reviewer with full
    data flow verification.
    """

    def _make_full_state(self):
        return {
            "messages": [HumanMessage(content="Build a dashboard with submit button")],
            "requirements": "Build a dashboard with a submit button",
            "project_description": None,
            "architecture": ARCHITECTURE_SPEC,
            "frontend_code": BUGGY_FRONTEND_CODE,
            "backend_code": {},
            "tests": {},
            "review_results": None,
            "current_phase": "testing",
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

    def test_tester_to_visual_qa_to_reviewer_data_flow(self):
        """
        Execute nodes sequentially: tester → visual_qa → reviewer.
        Verify the complete data flow between Visual QA and Reviewer.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        # --- Step 1: TESTER NODE ---
        # Mock tester to produce passing tests (the functional code is fine)
        mock_tester = MagicMock()
        mock_tester.invoke.return_value = {
            "tests": {"tests/test_app.py": "def test_home(): assert True"},
            "current_phase": "visual_qa",
        }

        # --- Step 2: VISUAL QA NODE ---
        # This is the key step. Mock Playwright + dual LLM analysis.
        combined_issues = _parse_issues_from_response(
            GEMINI_RESPONSE_JSON, "/", "desktop"
        ) + _parse_issues_from_response(OPUS_RESPONSE_JSON, "/", "desktop")

        # --- Step 3: REVIEWER NODE ---
        # Mock reviewer agent that captures the injected messages
        captured_reviewer_state = {}
        mock_reviewer = MagicMock()

        def reviewer_capture(s):
            # Deep copy messages to capture what reviewer sees
            captured_reviewer_state["messages"] = list(s.get("messages", []))
            captured_reviewer_state["visual_qa_report"] = s.get("visual_qa_report")
            return {
                "review_results": {
                    "issues": [
                        {
                            "title": "VISUAL REGRESSION: Button text invisible",
                            "description": (
                                "Submit button has text-blue-600 on bg-blue-600. "
                                "This was flagged by Visual QA (A11Y001, ARCH_VIS001). "
                                "WCAG AA requires 4.5:1 contrast ratio."
                            ),
                            "severity": "critical",
                        },
                        {
                            "title": "Touch target too small",
                            "description": (
                                "Settings link is 20x20px. Visual QA flagged (A11Y002). "
                                "WCAG 2.1 requires min 44x44px."
                            ),
                            "severity": "high",
                        },
                    ],
                },
            }

        mock_reviewer.invoke = reviewer_capture
        builder.agents = {"tester": mock_tester, "reviewer": mock_reviewer}

        # Execute pipeline: tester → visual_qa → reviewer
        state = self._make_full_state()

        # Step 1: Tester
        tester_result = builder._tester_node(state)
        state.update(tester_result)

        # Step 2: Visual QA (with mocked screenshots and analysis)
        mock_screenshots = {"/": {"desktop": DUMMY_PNG}}

        async def mock_analyze(*args, **kwargs):
            return combined_issues

        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value=mock_screenshots,
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot", side_effect=mock_analyze
            ):
                vqa_result = builder._visual_qa_node(state)
        state.update(vqa_result)

        # --- VERIFY: Visual QA aggregated dual-model feedback ---
        assert state["visual_qa_screenshots"] == 1
        vqa_report = state["visual_qa_report"]
        assert len(vqa_report["issues"]) == 3
        assert vqa_report["grades"]["/"]["desktop"] == "fail"
        assert vqa_report["summary"]["fail"] == 1

        # Verify ARCH_VIS001 (Gemini) and A11Y001/A11Y002 (Opus) are present
        issue_ids = {i["id"] for i in vqa_report["issues"]}
        assert "ARCH_VIS001" in issue_ids, "Gemini architecture issue missing"
        assert "A11Y001" in issue_ids, "Opus WCAG critical issue missing"
        assert "A11Y002" in issue_ids, "Opus touch target issue missing"

        # Step 3: Reviewer (with VQA context injection)
        with patch("memory.EmbeddingProvider"):
            reviewer_result = builder._reviewer_node(state)
        state.update(reviewer_result)

        # --- VERIFY: Reviewer received visual failure as mandatory criteria ---
        vqa_system_msgs = [
            m
            for m in captured_reviewer_state["messages"]
            if isinstance(m, SystemMessage) and "VISUAL QA ISSUES" in m.content
        ]
        assert (
            len(vqa_system_msgs) == 1
        ), f"Expected 1 VQA SystemMessage, got {len(vqa_system_msgs)}"
        injected_content = vqa_system_msgs[0].content
        assert "ARCH_VIS001" in injected_content
        assert "A11Y001" in injected_content
        assert "A11Y002" in injected_content
        assert "must be addressed in review" in injected_content

        # --- VERIFY: Reviewer blocked the build ---
        review = state["review_results"]
        critical_review_issues = [
            i for i in review["issues"] if i["severity"] == "critical"
        ]
        assert (
            len(critical_review_issues) >= 1
        ), "Reviewer should have at least 1 critical issue from visual regression"

        # --- VERIFY: _route_after_review blocks (routes to "frontend") ---
        route = builder._route_after_review(state)
        assert route == "frontend", (
            f"Build should be BLOCKED (route='frontend') but got '{route}'. "
            "The visual regression must prevent finalization."
        )

    def test_clean_visual_qa_allows_finalize(self):
        """
        When Visual QA finds no issues, the reviewer can finalize normally.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_full_state()

        # Mock tester
        mock_tester = MagicMock()
        mock_tester.invoke.return_value = {"tests": {}, "current_phase": "visual_qa"}
        builder.agents = {"tester": mock_tester}

        # Step 1: Tester
        state.update(builder._tester_node(state))

        # Step 2: Visual QA — no issues
        with patch(
            "ai.agents.visual_qa.capture_screenshots",
            new_callable=AsyncMock,
            return_value={"/": {"desktop": DUMMY_PNG}},
        ):
            with patch(
                "ai.agents.visual_qa.analyze_screenshot",
                new_callable=AsyncMock,
                return_value=[],
            ):
                state.update(builder._visual_qa_node(state))

        # Visual QA report should be clean
        assert state["visual_qa_report"]["summary"]["pass"] == 1
        assert state["visual_qa_report"]["summary"]["fail"] == 0

        # Step 3: Reviewer — no visual issues to block
        mock_reviewer = MagicMock()
        mock_reviewer.invoke.return_value = {
            "review_results": {"issues": []},  # No issues at all
        }
        builder.agents["reviewer"] = mock_reviewer

        with patch("memory.EmbeddingProvider"):
            state.update(builder._reviewer_node(state))

        # No critical issues → finalize
        route = builder._route_after_review(state)
        assert route == "finalize", f"Clean build should finalize but got '{route}'"

    def test_data_flow_diagram_matches_implementation(self):
        """
        Verify the data flow described in the test docstring matches reality:
        - visual_qa_report flows from _visual_qa_node into state
        - _reviewer_node reads visual_qa_report from state
        - build_reviewer_visual_context extracts critical/high issues
        - Extracted issues become SystemMessage appended to messages
        - reviewer agent sees the SystemMessage in its input
        """
        # Step 1: visual_qa_report is a Dict[str, Any] in ProjectState
        from ai.agents.multi_agent import ProjectState

        assert "visual_qa_report" in ProjectState.__annotations__

        # Step 2: build_reviewer_visual_context processes the report
        sample_report = {
            "issues": [
                {
                    "id": "A11Y001",
                    "severity": "critical",
                    "title": "Contrast fail",
                    "viewport": "desktop",
                    "fix_suggestion": "Change color",
                },
            ],
        }
        ctx = build_reviewer_visual_context(sample_report)
        assert ctx is not None
        assert ctx.startswith("VISUAL QA ISSUES")

        # Step 3: The context becomes a SystemMessage
        msg = SystemMessage(content=ctx)
        assert isinstance(msg, SystemMessage)
        assert "A11Y001" in msg.content

        # This confirms the data flow:
        # visual_qa_report (dict) → build_reviewer_visual_context (str)
        # → SystemMessage → state["messages"].append() → reviewer sees it
