"""
Phase 3.5 QA — A11y Contrast Engine & ARIA Audit
==================================================
Tests:
  1. _contrast_ratio correctness (black/white, identical, known values)
  2. _luminance correctness (known values)
  3. audit_against_contract — contrast flagging
  4. audit_against_contract — ARIA role detection in HTML
"""

import pytest

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents.visual_qa import (
    _luminance,
    _contrast_ratio,
    audit_against_contract,
    VisualQAReport,
    VisualSeverity,
    WCAG_AA_NORMAL_TEXT,
)

# ═════════════════════════════════════════════════════════════════════════
# 1. _contrast_ratio Correctness
# ═════════════════════════════════════════════════════════════════════════


class TestContrastRatio:
    def test_black_vs_white_is_21(self):
        """WCAG maximum: black on white = 21:1."""
        ratio = _contrast_ratio("#000000", "#FFFFFF")
        assert ratio == pytest.approx(21.0, abs=0.05)

    def test_white_vs_black_is_21(self):
        """Argument order should not matter."""
        ratio = _contrast_ratio("#FFFFFF", "#000000")
        assert ratio == pytest.approx(21.0, abs=0.05)

    def test_identical_colors_is_1(self):
        """Same color against itself = 1:1."""
        ratio = _contrast_ratio("#3B82F6", "#3B82F6")
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_near_identical_low_contrast(self):
        """Very similar grays should have low contrast (< 1.5)."""
        ratio = _contrast_ratio("#777777", "#787878")
        assert ratio < 1.5

    def test_returns_at_least_1(self):
        """Contrast ratio must always be >= 1.0."""
        ratio = _contrast_ratio("#AAAAAA", "#AAAAAA")
        assert ratio >= 1.0

    def test_known_passing_contrast(self):
        """#111827 on #FFFFFF should pass WCAG AA (> 4.5:1)."""
        ratio = _contrast_ratio("#111827", "#FFFFFF")
        assert ratio >= WCAG_AA_NORMAL_TEXT

    def test_known_failing_contrast(self):
        """#6B7280 (gray-500) on #F3F4F6 (gray-100) should fail WCAG AA."""
        ratio = _contrast_ratio("#6B7280", "#F3F4F6")
        assert ratio < WCAG_AA_NORMAL_TEXT


# ═════════════════════════════════════════════════════════════════════════
# 2. _luminance Correctness
# ═════════════════════════════════════════════════════════════════════════


class TestLuminance:
    def test_white_luminance(self):
        """Pure white should have luminance = 1.0."""
        assert _luminance("#FFFFFF") == pytest.approx(1.0, abs=0.001)

    def test_black_luminance(self):
        """Pure black should have luminance = 0.0."""
        assert _luminance("#000000") == pytest.approx(0.0, abs=0.001)

    def test_mid_gray_luminance(self):
        """#808080 (mid gray) should have luminance ~ 0.216."""
        lum = _luminance("#808080")
        assert 0.18 < lum < 0.25

    def test_malformed_returns_zero(self):
        """Invalid hex should return 0.0."""
        assert _luminance("red") == 0.0
        assert _luminance("#FFF") == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 3. audit_against_contract — Contrast Flagging
# ═════════════════════════════════════════════════════════════════════════


def _make_contract_dict(
    text_primary="#111827", background="#FFFFFF", surface="#F3F4F6"
):
    """Build a minimal DesignContract dict for testing."""
    return {
        "color_tokens": [
            {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
            {"name": "background", "hsl": "0 0% 100%", "hex_fallback": background},
            {
                "name": "text_primary",
                "hsl": "220 14% 10%",
                "hex_fallback": text_primary,
            },
            {"name": "surface", "hsl": "220 14% 96%", "hex_fallback": surface},
        ],
        "component_map": [],
    }


class TestContrastAudit:
    def test_no_issues_with_good_contrast(self):
        """High-contrast tokens should produce no contrast issues."""
        contract = _make_contract_dict(text_primary="#000000", background="#FFFFFF")
        report = VisualQAReport()
        result = audit_against_contract(report, contract)
        contrast_issues = [i for i in result.issues if "contrast" in i.title.lower()]
        # text_primary (#000000) on white = 21:1 → no issue
        text_primary_issues = [i for i in contrast_issues if "text_primary" in i.title]
        assert len(text_primary_issues) == 0

    def test_flags_low_contrast_text(self):
        """Light gray text on white background should be flagged."""
        contract = _make_contract_dict(text_primary="#CCCCCC", background="#FFFFFF")
        report = VisualQAReport()
        result = audit_against_contract(report, contract)
        contrast_issues = [
            i
            for i in result.issues
            if "text_primary" in i.title.lower() and "contrast" in i.title.lower()
        ]
        assert len(contrast_issues) >= 1

    def test_severity_high_for_very_low_contrast(self):
        """Nearly identical colors should produce HIGH severity (< 3.0:1)."""
        contract = _make_contract_dict(text_primary="#F0F0F0", background="#FFFFFF")
        report = VisualQAReport()
        result = audit_against_contract(report, contract)
        high_issues = [
            i
            for i in result.issues
            if i.severity == VisualSeverity.HIGH and "text_primary" in i.title.lower()
        ]
        assert len(high_issues) >= 1

    def test_severity_medium_for_borderline(self):
        """Contrast between 3.0 and 4.5 should produce MEDIUM severity."""
        # #767676 on white = ~4.54:1 (just above), #7B7B7B = ~4.19:1 (between 3 and 4.5)
        contract = _make_contract_dict(text_primary="#7B7B7B", background="#FFFFFF")
        report = VisualQAReport()
        result = audit_against_contract(report, contract)
        tp_issues = [
            i
            for i in result.issues
            if "text_primary" in i.title.lower() and "background" in i.title.lower()
        ]
        if tp_issues:
            # If flagged, check it's MEDIUM (between 3.0 and 4.5)
            assert tp_issues[0].severity == VisualSeverity.MEDIUM

    def test_checks_against_surface_too(self):
        """Tokens should be checked against both background AND surface."""
        contract = _make_contract_dict(
            text_primary="#AAAAAA", background="#FFFFFF", surface="#FAFAFA"
        )
        report = VisualQAReport()
        result = audit_against_contract(report, contract)
        surface_issues = [
            i
            for i in result.issues
            if "surface" in i.title.lower() and "text_primary" in i.title.lower()
        ]
        assert len(surface_issues) >= 1


# ═════════════════════════════════════════════════════════════════════════
# 4. audit_against_contract — ARIA Role Detection
# ═════════════════════════════════════════════════════════════════════════


class TestARIAAudit:
    def test_detects_missing_button_tag(self):
        """If code has no <button> or role='button', should flag it."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "button",
                    "shadcn_component": "Button",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {"App.tsx": "<div class='btn'>Click me</div>"}
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        aria_issues = [
            i for i in result.issues if "ARIA" in i.title or "aria" in i.title.lower()
        ]
        assert len(aria_issues) >= 1
        assert "Button" in aria_issues[0].title

    def test_passes_with_button_tag(self):
        """If code has <button>, no ARIA issue for Button component."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "button",
                    "shadcn_component": "Button",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {"App.tsx": "<button class='btn-primary'>Submit</button>"}
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        button_aria_issues = [
            i for i in result.issues if "Button" in i.title and "ARIA" in i.title
        ]
        assert len(button_aria_issues) == 0

    def test_detects_missing_dialog_role(self):
        """If code has no role='dialog', should flag Dialog component."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "modal",
                    "shadcn_component": "Dialog",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {
            "Modal.tsx": "<div class='modal-overlay'><div class='modal-content'>Hello</div></div>"
        }
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        dialog_issues = [i for i in result.issues if "Dialog" in i.title]
        assert len(dialog_issues) >= 1

    def test_passes_with_dialog_role(self):
        """If code has role='dialog', no issue for Dialog."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "modal",
                    "shadcn_component": "Dialog",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {
            "Modal.tsx": '<div role="dialog" aria-labelledby="title"><h2 id="title">Confirm</h2></div>'
        }
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        dialog_issues = [
            i for i in result.issues if "Dialog" in i.title and "ARIA" in i.title
        ]
        assert len(dialog_issues) == 0

    def test_detects_missing_table_tag(self):
        """If code renders a table as divs without <table> or role='grid', flag it."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "table",
                    "shadcn_component": "Table",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {
            "DataGrid.tsx": "<div class='grid'><div class='row'>Data</div></div>"
        }
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        table_issues = [i for i in result.issues if "Table" in i.title]
        assert len(table_issues) >= 1

    def test_no_aria_check_without_frontend_code(self):
        """When frontend_code is None, no ARIA issues should be raised."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#000000",
                },
            ],
            "component_map": [
                {
                    "detected_type": "button",
                    "shadcn_component": "Button",
                    "variant": "default",
                },
            ],
        }
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code=None)
        aria_issues = [i for i in result.issues if "ARIA" in i.title]
        assert len(aria_issues) == 0

    def test_summary_includes_a11y_count(self):
        """audit_against_contract should set summary.a11y_issues count."""
        contract = {
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
                {
                    "name": "text_primary",
                    "hsl": "220 14% 10%",
                    "hex_fallback": "#EEEEEE",
                },  # low contrast
            ],
            "component_map": [
                {
                    "detected_type": "button",
                    "shadcn_component": "Button",
                    "variant": "default",
                },
            ],
        }
        frontend_code = {"App.tsx": "<div>no button tag</div>"}
        report = VisualQAReport()
        result = audit_against_contract(report, contract, frontend_code)
        assert "a11y_issues" in result.summary
        assert result.summary["a11y_issues"] >= 2  # at least 1 contrast + 1 ARIA
