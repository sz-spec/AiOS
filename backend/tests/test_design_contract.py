"""
Phase 3.5 QA — DesignContract Schema Validation
=================================================
Tests:
  1. HSL regex validation (valid/invalid patterns)
  2. Minimum required color tokens (primary, background, text_primary)
  3. OCR extraction via from_vision_analysis()
  4. Component mapping via _SHADCN_MAP
  5. _hex_to_hsl correctness
"""

import pytest
from pydantic import ValidationError

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents.vision_agent import (
    DesignContract,
    DesignToken,
    VisionAnalysis,
    DetectedComponent,
    ColorPalette,
    Typography,
    LayoutRegion,
    _hex_to_hsl,
    _SHADCN_MAP,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_minimum_tokens():
    """Return the 3 required tokens for a valid DesignContract."""
    return [
        DesignToken(name="primary", hsl="220 90% 56%", hex_fallback="#3B82F6"),
        DesignToken(name="background", hsl="0 0% 100%", hex_fallback="#FFFFFF"),
        DesignToken(name="text_primary", hsl="220 14% 10%", hex_fallback="#111827"),
    ]


def _make_rich_analysis():
    """Build a VisionAnalysis with components for OCR and mapping tests."""
    return VisionAnalysis(
        layout_regions=[
            LayoutRegion(role="header", bounds={"x": 0, "y": 0, "w": 1, "h": 0.08}),
            LayoutRegion(role="main", bounds={"x": 0, "y": 0.08, "w": 1, "h": 0.84}),
        ],
        components=[
            DetectedComponent(
                type="button", label="Submit", region_id="main", interactive=True
            ),
            DetectedComponent(type="input", label="Email address", region_id="main"),
            DetectedComponent(type="card", label="Dashboard Card", region_id="main"),
            DetectedComponent(type="table", label="Users Table", region_id="main"),
            DetectedComponent(
                type="avatar", label="", region_id="header"
            ),  # empty label → no OCR
        ],
        palette=ColorPalette(
            primary="#3B82F6",
            secondary="#10B981",
            accent="#F59E0B",
            background="#FFFFFF",
            surface="#F3F4F6",
            text_primary="#111827",
            text_secondary="#6B7280",
            border="#E5E7EB",
            error="#EF4444",
            success="#10B981",
        ),
        typography=Typography(
            heading_font="Poppins",
            body_font="Inter",
            heading_weight="bold",
            base_size_px=16,
            scale_ratio=1.333,
            line_height=1.6,
        ),
        overall_style="corporate",
        page_type="dashboard",
        confidence=0.88,
        responsive_hints={"mobile": "stack vertically", "tablet": "2-col grid"},
    )


# ═════════════════════════════════════════════════════════════════════════
# 1. HSL Validation
# ═════════════════════════════════════════════════════════════════════════


class TestHSLValidation:
    def test_valid_hsl_accepted(self):
        """A well-formed HSL string must be accepted."""
        token = DesignToken(name="primary", hsl="220 90% 56%", hex_fallback="#3B82F6")
        assert token.hsl == "220 90% 56%"

    def test_valid_hsl_edge_zeros(self):
        """HSL '0 0% 0%' is valid (pure black)."""
        token = DesignToken(name="black", hsl="0 0% 0%", hex_fallback="#000000")
        assert token.hsl == "0 0% 0%"

    def test_valid_hsl_edge_max(self):
        """HSL '360 100% 100%' is valid (pure white at max hue)."""
        token = DesignToken(name="white", hsl="360 100% 100%", hex_fallback="#FFFFFF")
        assert token.hsl == "360 100% 100%"

    def test_invalid_hsl_word_rejected(self):
        """A color word like 'red' must be rejected by the regex."""
        with pytest.raises(ValidationError) as exc_info:
            DesignToken(name="bad", hsl="red", hex_fallback="#FF0000")
        assert (
            "hsl" in str(exc_info.value).lower()
            or "string_pattern_mismatch" in str(exc_info.value).lower()
        )

    def test_invalid_hsl_missing_percent(self):
        """HSL '220 90' (missing lightness) must be rejected."""
        with pytest.raises(ValidationError):
            DesignToken(name="bad", hsl="220 90", hex_fallback="#3B82F6")

    def test_invalid_hsl_css_function_rejected(self):
        """CSS function syntax 'hsl(220, 90%, 56%)' must be rejected."""
        with pytest.raises(ValidationError):
            DesignToken(name="bad", hsl="hsl(220, 90%, 56%)", hex_fallback="#3B82F6")

    def test_invalid_hex_rejected(self):
        """A 3-digit hex '#F00' must be rejected (only 6-digit allowed)."""
        with pytest.raises(ValidationError):
            DesignToken(name="bad", hsl="0 100% 50%", hex_fallback="#F00")

    def test_invalid_hex_no_hash(self):
        """Hex without '#' prefix must be rejected."""
        with pytest.raises(ValidationError):
            DesignToken(name="bad", hsl="0 100% 50%", hex_fallback="FF0000")


# ═════════════════════════════════════════════════════════════════════════
# 2. Minimum Token Requirements
# ═════════════════════════════════════════════════════════════════════════


class TestMinimumTokens:
    def test_all_required_tokens_pass(self):
        """Contract with primary, background, text_primary should validate."""
        contract = DesignContract(color_tokens=_make_minimum_tokens())
        assert len(contract.color_tokens) == 3

    def test_missing_primary_raises(self):
        """Contract without 'primary' token must raise ValidationError."""
        tokens = [
            DesignToken(name="background", hsl="0 0% 100%", hex_fallback="#FFFFFF"),
            DesignToken(name="text_primary", hsl="220 14% 10%", hex_fallback="#111827"),
        ]
        with pytest.raises(ValidationError) as exc_info:
            DesignContract(color_tokens=tokens)
        assert "primary" in str(exc_info.value)

    def test_missing_background_raises(self):
        """Contract without 'background' token must raise ValidationError."""
        tokens = [
            DesignToken(name="primary", hsl="220 90% 56%", hex_fallback="#3B82F6"),
            DesignToken(name="text_primary", hsl="220 14% 10%", hex_fallback="#111827"),
        ]
        with pytest.raises(ValidationError) as exc_info:
            DesignContract(color_tokens=tokens)
        assert "background" in str(exc_info.value)

    def test_missing_text_primary_raises(self):
        """Contract without 'text_primary' token must raise ValidationError."""
        tokens = [
            DesignToken(name="primary", hsl="220 90% 56%", hex_fallback="#3B82F6"),
            DesignToken(name="background", hsl="0 0% 100%", hex_fallback="#FFFFFF"),
        ]
        with pytest.raises(ValidationError) as exc_info:
            DesignContract(color_tokens=tokens)
        assert "text_primary" in str(exc_info.value)

    def test_extra_tokens_allowed(self):
        """Extra tokens beyond the required 3 must be accepted."""
        tokens = _make_minimum_tokens() + [
            DesignToken(name="accent", hsl="38 92% 50%", hex_fallback="#F59E0B"),
            DesignToken(name="error", hsl="0 84% 60%", hex_fallback="#EF4444"),
        ]
        contract = DesignContract(color_tokens=tokens)
        assert len(contract.color_tokens) == 5


# ═════════════════════════════════════════════════════════════════════════
# 3. OCR Extraction via from_vision_analysis()
# ═════════════════════════════════════════════════════════════════════════


class TestOCRExtraction:
    def test_ocr_preserves_button_label(self):
        """Button component labels should appear as OCR with semantic_role='cta'."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        cta_items = [o for o in contract.ocr_content if o.semantic_role == "cta"]
        assert len(cta_items) == 1
        assert cta_items[0].text == "Submit"
        assert cta_items[0].region_id == "main"

    def test_ocr_preserves_input_label(self):
        """Input component labels should appear as OCR with semantic_role='label'."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        label_items = [o for o in contract.ocr_content if o.text == "Email address"]
        assert len(label_items) == 1
        assert label_items[0].semantic_role == "label"

    def test_ocr_skips_empty_labels(self):
        """Components with empty labels should NOT generate OCR entries."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        # avatar has empty label → should not appear
        all_texts = [o.text for o in contract.ocr_content]
        assert "" not in all_texts
        # 4 components with labels: Submit, Email address, Dashboard Card, Users Table
        assert len(contract.ocr_content) == 4

    def test_ocr_region_id_defaults_to_main(self):
        """Components without region_id should default to 'main'."""
        analysis = VisionAnalysis(
            components=[DetectedComponent(type="badge", label="New", region_id="")],
        )
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.ocr_content[0].region_id == "main"


# ═════════════════════════════════════════════════════════════════════════
# 4. Component Mapping via _SHADCN_MAP
# ═════════════════════════════════════════════════════════════════════════


class TestComponentMapping:
    def test_button_maps_to_shadcn_button(self):
        """'button' type should map to shadcn 'Button' component."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        btn = [m for m in contract.component_map if m.detected_type == "button"]
        assert len(btn) == 1
        assert btn[0].shadcn_component == "Button"
        assert btn[0].variant == "default"

    def test_table_maps_to_shadcn_table(self):
        """'table' type should map to shadcn 'Table'."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        tbl = [m for m in contract.component_map if m.detected_type == "table"]
        assert len(tbl) == 1
        assert tbl[0].shadcn_component == "Table"

    def test_unknown_type_falls_back_to_card(self):
        """Unmapped component types should fall back to 'Card'."""
        analysis = VisionAnalysis(
            components=[DetectedComponent(type="weird-widget", label="???")],
        )
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.component_map[0].shadcn_component == "Card"
        assert contract.component_map[0].variant == "default"

    def test_icon_maps_to_ghost_button(self):
        """'icon' type should map to Button with ghost variant."""
        analysis = VisionAnalysis(
            components=[DetectedComponent(type="icon", label="Settings")],
        )
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.component_map[0].shadcn_component == "Button"
        assert contract.component_map[0].variant == "ghost"

    def test_all_shadcn_map_keys_covered(self):
        """Every key in _SHADCN_MAP should produce a valid ComponentMapping."""
        for ctype, (expected_name, expected_variant) in _SHADCN_MAP.items():
            analysis = VisionAnalysis(
                components=[DetectedComponent(type=ctype, label="test")],
            )
            contract = DesignContract.from_vision_analysis(analysis)
            assert contract.component_map[0].shadcn_component == expected_name
            assert contract.component_map[0].variant == expected_variant


# ═════════════════════════════════════════════════════════════════════════
# 5. _hex_to_hsl Correctness
# ═════════════════════════════════════════════════════════════════════════


class TestHexToHSL:
    def test_pure_white(self):
        assert _hex_to_hsl("#FFFFFF") == "0 0% 100%"

    def test_pure_black(self):
        assert _hex_to_hsl("#000000") == "0 0% 0%"

    def test_blue(self):
        """#3B82F6 should produce an HSL with hue ~217, saturation ~91%, lightness ~60%."""
        hsl = _hex_to_hsl("#3B82F6")
        parts = hsl.split()
        h = int(parts[0])
        s = int(parts[1].rstrip("%"))
        l = int(parts[2].rstrip("%"))
        assert 215 <= h <= 219, f"Hue {h} out of expected range 215-219"
        assert 88 <= s <= 93, f"Saturation {s} out of expected range 88-93"
        assert 58 <= l <= 62, f"Lightness {l} out of expected range 58-62"

    def test_malformed_hex_returns_fallback(self):
        """Non-6-digit hex should return '0 0% 0%'."""
        assert _hex_to_hsl("#FFF") == "0 0% 0%"
        assert _hex_to_hsl("red") == "0 0% 0%"

    def test_strip_hash(self):
        """Should work with or without leading '#'."""
        assert _hex_to_hsl("3B82F6") == _hex_to_hsl("#3B82F6")


# ═════════════════════════════════════════════════════════════════════════
# 6. Full from_vision_analysis() Integration
# ═════════════════════════════════════════════════════════════════════════


class TestFromVisionAnalysis:
    def test_preserves_page_type(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.page_type == "dashboard"

    def test_preserves_overall_style(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.overall_style == "corporate"

    def test_preserves_confidence(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.confidence == pytest.approx(0.88)

    def test_preserves_responsive_hints(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert "mobile" in contract.responsive_breakpoints
        assert "tablet" in contract.responsive_breakpoints

    def test_typography_family_preserved(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.heading_font.family == "Poppins"
        assert contract.body_font.family == "Inter"

    def test_typography_scale_preserved(self):
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert contract.heading_font.scale_ratio == pytest.approx(1.333)

    def test_all_palette_colors_become_tokens(self):
        """All 10 ColorPalette fields should become DesignTokens."""
        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        assert len(contract.color_tokens) == 10

    def test_model_dump_roundtrip(self):
        """model_dump() output should be JSON-serializable."""
        import json

        analysis = _make_rich_analysis()
        contract = DesignContract.from_vision_analysis(analysis)
        dumped = contract.model_dump()
        serialized = json.dumps(dumped)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip["page_type"] == "dashboard"
        assert len(roundtrip["color_tokens"]) == 10
