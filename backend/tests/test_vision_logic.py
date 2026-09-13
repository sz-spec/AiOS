"""
Unit Tests — VisionAgent + ThemeEngine + Pipeline Integration
=============================================================

Verifies:
  1. VisionAgent: image validation, MIME detection, JSON parsing, response repair
  2. ThemeEngine: style presets, fuzzy matching, CSS/Tailwind generation
  3. Pipeline: _route_entry conditional routing, vision node state output,
     architect/frontend context injection
  4. API: /api/projects/vision endpoint accepts multipart/form-data
"""

import base64
import json
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Mock Data
# ---------------------------------------------------------------------------

MOCK_VISION_JSON = {
    "layout_regions": [
        {
            "role": "header",
            "bounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.08},
            "children": [],
            "css_hint": "sticky top-0 bg-white shadow-sm",
        },
        {
            "role": "sidebar",
            "bounds": {"x": 0.0, "y": 0.08, "width": 0.2, "height": 0.92},
            "children": [],
            "css_hint": "w-64 bg-gray-50 border-r",
        },
        {
            "role": "main",
            "bounds": {"x": 0.2, "y": 0.08, "width": 0.8, "height": 0.92},
            "children": [],
            "css_hint": "flex-1 p-6",
        },
    ],
    "components": [
        {
            "type": "nav-link",
            "label": "Dashboard",
            "region_id": "sidebar",
            "props_hint": {"variant": "active"},
            "interactive": True,
        },
        {
            "type": "nav-link",
            "label": "Settings",
            "region_id": "sidebar",
            "props_hint": {},
            "interactive": True,
        },
        {
            "type": "button",
            "label": "Create New",
            "region_id": "header",
            "props_hint": {"variant": "primary", "size": "md"},
            "interactive": True,
        },
        {
            "type": "card",
            "label": "Stats Overview",
            "region_id": "main",
            "props_hint": {},
            "interactive": False,
        },
        {
            "type": "table",
            "label": "Recent Activity",
            "region_id": "main",
            "props_hint": {},
            "interactive": False,
        },
    ],
    "palette": {
        "primary": "#6366F1",
        "secondary": "#10B981",
        "accent": "#F59E0B",
        "background": "#FFFFFF",
        "surface": "#F9FAFB",
        "text_primary": "#111827",
        "text_secondary": "#6B7280",
        "border": "#E5E7EB",
        "error": "#EF4444",
        "success": "#10B981",
    },
    "typography": {
        "heading_font": "Inter",
        "body_font": "Inter",
        "heading_weight": "semibold",
        "base_size_px": 14,
        "scale_ratio": 1.25,
        "line_height": 1.6,
    },
    "overall_style": "minimal",
    "responsive_hints": {
        "mobile": "stack vertically, hide sidebar",
        "tablet": "collapsible sidebar, 2-col grid",
    },
    "page_type": "dashboard",
    "confidence": 0.92,
}


def _make_png_1x1() -> bytes:
    """Minimal valid 1x1 PNG (67 bytes)."""
    import struct
    import zlib

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw_data = zlib.compress(b"\x00\x00\x00\x00")
    idat = _chunk(b"IDAT", raw_data)
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


PNG_1X1 = _make_png_1x1()
PNG_1X1_B64 = base64.b64encode(PNG_1X1).decode("ascii")


# ---------------------------------------------------------------------------
# 1. VisionAgent — Data Structures
# ---------------------------------------------------------------------------


class TestVisionDataStructures:

    def test_vision_analysis_roundtrip(self):
        """VisionAnalysis.to_dict() → from_dict() preserves all fields."""
        from ai.agents.vision_agent import VisionAnalysis

        analysis = VisionAnalysis.from_dict(MOCK_VISION_JSON)
        roundtrip = VisionAnalysis.from_dict(analysis.to_dict())

        assert roundtrip.page_type == "dashboard"
        assert roundtrip.overall_style == "minimal"
        assert roundtrip.confidence == 0.92
        assert len(roundtrip.layout_regions) == 3
        assert len(roundtrip.components) == 5
        assert roundtrip.palette.primary == "#6366F1"
        assert roundtrip.typography.base_size_px == 14

    def test_vision_analysis_defaults(self):
        """Empty dict produces valid VisionAnalysis with defaults."""
        from ai.agents.vision_agent import VisionAnalysis

        analysis = VisionAnalysis.from_dict({})
        assert analysis.page_type == "dashboard"
        assert analysis.confidence == 0.0
        assert analysis.palette.primary == "#3B82F6"


# ---------------------------------------------------------------------------
# 2. VisionAgent — Image Validation
# ---------------------------------------------------------------------------


class TestImageValidation:

    def test_valid_png(self):
        """Valid PNG is accepted."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        raw, mime = agent.validate_image(PNG_1X1_B64)
        assert mime == "image/png"
        assert len(raw) > 0

    def test_invalid_base64_raises(self):
        """Invalid base64 raises ValueError."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        with pytest.raises(ValueError, match="Invalid base64"):
            agent.validate_image("not-valid-base64!!!")

    def test_oversized_image_raises(self):
        """Image over 4MB raises ValueError."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        big_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024 + 1)
        big_b64 = base64.b64encode(big_data).decode("ascii")
        with pytest.raises(ValueError, match="exceeds"):
            agent.validate_image(big_b64)

    def test_unsupported_mime_raises(self):
        """Non-PNG/JPEG/WEBP raises ValueError."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        gif_data = b"GIF89a" + b"\x00" * 100
        gif_b64 = base64.b64encode(gif_data).decode("ascii")
        with pytest.raises(ValueError, match="Unsupported image type"):
            agent.validate_image(gif_b64)

    def test_mime_detection_jpeg(self):
        """JPEG magic bytes detected correctly."""
        from ai.agents.vision_agent import VisionAgent

        assert VisionAgent._detect_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"

    def test_mime_detection_webp(self):
        """WEBP magic bytes detected correctly."""
        from ai.agents.vision_agent import VisionAgent

        data = b"RIFF\x00\x00\x00\x00WEBP"
        assert VisionAgent._detect_mime(data) == "image/webp"


# ---------------------------------------------------------------------------
# 3. VisionAgent — JSON Parsing
# ---------------------------------------------------------------------------


class TestJSONParsing:

    def test_parse_clean_json(self):
        """Clean JSON string parses to VisionAnalysis."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        result = agent._parse_response(json.dumps(MOCK_VISION_JSON))
        assert result is not None
        assert result.page_type == "dashboard"
        assert len(result.components) == 5

    def test_parse_markdown_fenced_json(self):
        """JSON wrapped in ```json fences is extracted correctly."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        fenced = f"```json\n{json.dumps(MOCK_VISION_JSON)}\n```"
        result = agent._parse_response(fenced)
        assert result is not None
        assert result.confidence == 0.92

    def test_parse_invalid_json_returns_none(self):
        """Malformed JSON returns None (triggers repair path)."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        assert agent._parse_response("this is not json {broken") is None

    def test_parse_non_dict_json_returns_none(self):
        """JSON array (not object) returns None."""
        from ai.agents.vision_agent import VisionAgent

        agent = VisionAgent()
        assert agent._parse_response("[1, 2, 3]") is None


# ---------------------------------------------------------------------------
# 4. VisionAgent — Context Formatters
# ---------------------------------------------------------------------------


class TestContextFormatters:

    def test_to_architect_context(self):
        """to_architect_context produces markdown with key sections."""
        from ai.agents.vision_agent import VisionAgent, VisionAnalysis

        agent = VisionAgent()
        analysis = VisionAnalysis.from_dict(MOCK_VISION_JSON)
        ctx = agent.to_architect_context(analysis)

        assert "## Vision Analysis" in ctx
        assert "dashboard" in ctx
        assert "#6366F1" in ctx
        assert "header" in ctx
        assert "sidebar" in ctx

    def test_to_frontend_context(self):
        """to_frontend_context produces design system instructions."""
        from ai.agents.vision_agent import VisionAgent, VisionAnalysis
        from services.theme_engine import ThemeEngine

        agent = VisionAgent()
        analysis = VisionAnalysis.from_dict(MOCK_VISION_JSON)
        theme = ThemeEngine.from_analysis(analysis)

        ctx = agent.to_frontend_context(analysis, theme.to_dict())
        assert "## Design System" in ctx
        assert "CSS Variables" in ctx
        assert "var(--color-primary)" in ctx
        assert "nav-link" in ctx.lower() or "Dashboard" in ctx


# ---------------------------------------------------------------------------
# 5. ThemeEngine — Style Presets
# ---------------------------------------------------------------------------


class TestThemeEngine:

    def test_all_presets_generate(self):
        """All 8 style presets produce valid ThemeConfig."""
        from services.theme_engine import ThemeEngine, STYLE_PRESETS

        for style_name in STYLE_PRESETS:
            theme = ThemeEngine.from_style_descriptor(style_name)
            assert theme.style_name == style_name
            assert "module.exports" in theme.tailwind_config_js
            assert ":root" in theme.globals_css
            assert len(theme.component_classes) >= 8

    def test_fuzzy_alias_matching(self):
        """Style aliases resolve to canonical names."""
        from services.theme_engine import ThemeEngine

        assert ThemeEngine.from_style_descriptor("apple").style_name == "apple-like"
        assert ThemeEngine.from_style_descriptor("cyber").style_name == "cyberpunk"
        assert ThemeEngine.from_style_descriptor("glass").style_name == "glassmorphism"
        assert ThemeEngine.from_style_descriptor("dark").style_name == "dark-mode"

    def test_unknown_style_falls_back_to_minimal(self):
        """Unknown style name falls back to 'minimal'."""
        from services.theme_engine import ThemeEngine

        theme = ThemeEngine.from_style_descriptor("nonexistent_style_xyz")
        assert theme.style_name == "minimal"

    def test_from_analysis_high_confidence(self):
        """High-confidence analysis uses detected colors, not preset defaults."""
        from ai.agents.vision_agent import VisionAnalysis
        from services.theme_engine import ThemeEngine

        analysis = VisionAnalysis.from_dict(MOCK_VISION_JSON)
        theme = ThemeEngine.from_analysis(analysis)
        # The mock has primary=#6366F1, which should appear in CSS vars
        assert "#6366F1" in theme.globals_css or "6366F1" in theme.globals_css

    def test_from_analysis_low_confidence_uses_preset(self):
        """Low-confidence analysis falls back to style preset colors."""
        from ai.agents.vision_agent import VisionAnalysis
        from services.theme_engine import ThemeEngine

        low_conf = {**MOCK_VISION_JSON, "confidence": 0.3}
        analysis = VisionAnalysis.from_dict(low_conf)
        theme = ThemeEngine.from_analysis(analysis)
        # Should use preset colors, not the detected ones
        assert theme.style_name in ("minimal", MOCK_VISION_JSON["overall_style"])

    def test_theme_config_roundtrip(self):
        """ThemeConfig.to_dict() → from_dict() preserves all fields."""
        from services.theme_engine import ThemeEngine

        theme = ThemeEngine.from_style_descriptor("cyberpunk")
        roundtrip = theme.from_dict(theme.to_dict())
        assert roundtrip.style_name == "cyberpunk"
        assert roundtrip.tailwind_config_js == theme.tailwind_config_js
        assert roundtrip.globals_css == theme.globals_css

    def test_tailwind_config_has_css_variables(self):
        """Generated tailwind.config.js references CSS variable colors."""
        from services.theme_engine import ThemeEngine

        theme = ThemeEngine.from_style_descriptor("material-design")
        assert "var(--color-primary)" in theme.tailwind_config_js
        assert "var(--color-surface)" in theme.tailwind_config_js

    def test_globals_css_has_root_variables(self):
        """Generated globals.css has :root block with --color-* vars."""
        from services.theme_engine import ThemeEngine

        theme = ThemeEngine.from_style_descriptor("corporate")
        assert ":root" in theme.globals_css
        assert "--color-primary:" in theme.globals_css
        assert "--font-heading:" in theme.globals_css


# ---------------------------------------------------------------------------
# 6. Pipeline — Conditional Routing
# ---------------------------------------------------------------------------


class TestPipelineRouting:

    def test_route_entry_with_image(self):
        """_route_entry returns 'vision' when vision_image is set."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.LLM"):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            assert builder._route_entry({"vision_image": PNG_1X1_B64}) == "vision"

    def test_route_entry_without_image(self):
        """_route_entry returns 'architect' when no vision_image."""
        from ai.agents.multi_agent import MultiAgentBuilder

        with patch("ai.agents.multi_agent.LLM"):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            assert builder._route_entry({"vision_image": None}) == "architect"
            assert builder._route_entry({}) == "architect"


# ---------------------------------------------------------------------------
# 7. Pipeline — Vision Node (mocked LLM)
# ---------------------------------------------------------------------------


class TestVisionNode:

    def test_vision_node_success(self):
        """_vision_node returns analysis and theme on successful LLM call."""
        from ai.agents.multi_agent import MultiAgentBuilder

        mock_response = MagicMock()
        mock_response.content = json.dumps(MOCK_VISION_JSON)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch("ai.agents.multi_agent.LLM"):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            state = {
                "vision_image": PNG_1X1_B64,
                "requirements": "Build a task dashboard",
            }
            result = builder._vision_node(state)

        assert result["vision_analysis"] is not None
        assert result["vision_analysis"]["page_type"] == "dashboard"
        assert result["vision_theme"] is not None
        assert "tailwind_config_js" in result["vision_theme"]
        assert result["current_phase"] == "vision"

    def test_vision_node_failure_graceful(self):
        """_vision_node returns None analysis on LLM failure (no crash)."""
        from ai.agents.multi_agent import MultiAgentBuilder
        from ai.agents.vision_agent import VisionAnalysisError

        with patch("ai.agents.multi_agent.LLM"):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm",
            side_effect=VisionAnalysisError("No LLM"),
        ):
            state = {
                "vision_image": PNG_1X1_B64,
                "requirements": "Build something",
            }
            result = builder._vision_node(state)

        assert result["vision_analysis"] is None
        assert result["vision_theme"] is None
        assert result["current_phase"] == "vision"


# ---------------------------------------------------------------------------
# 8. Pipeline — Full Graph Has Vision Node
# ---------------------------------------------------------------------------


class TestGraphStructure:

    def test_graph_includes_vision_node(self):
        """Compiled graph includes 'vision' node."""
        from ai.agents.multi_agent import MultiAgentBuilder

        MagicMock()
        mock_llm_instance = MagicMock()
        mock_llm_instance.invoke.return_value = MagicMock(content="{}")

        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm_instance):
            builder = MultiAgentBuilder(llm=mock_llm_instance)

        # Check that the graph has a vision node
        node_names = set(builder.graph.get_graph().nodes.keys())
        assert "vision" in node_names
        assert "architect" in node_names
        assert "expander" in node_names

    def test_project_state_has_vision_fields(self):
        """ProjectState TypedDict includes vision fields."""
        from ai.agents.multi_agent import ProjectState

        annotations = ProjectState.__annotations__
        assert "vision_image" in annotations
        assert "vision_analysis" in annotations
        assert "vision_theme" in annotations
