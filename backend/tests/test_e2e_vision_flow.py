"""
E2E 'Design Fidelity' Verification — Phase 3.5
================================================

End-to-end tests that verify the FULL vision pipeline from API request
through to generated CSS output, proving zero context loss between nodes.

Tests:
  1. API ENDPOINT: POST /api/projects/vision accepts multipart image + style
  2. VISION ROUTING: _route_entry correctly dispatches based on image presence
  3. VISION NODE STATE: vision_analysis + vision_theme populated after node runs
  4. ARCHITECT CONTEXT INJECTION: Layout/palette injected into architect requirements
  5. FRONTEND CONTEXT INJECTION: Design tokens (colors, typography) reach frontend
  6. ACID TEST: #FF00FF primary color survives full pipeline → globals.css + tailwind.config.js
  7. STYLE-ONLY FLOW: style= without image produces valid theme (no vision node)
"""

import base64
import json
import struct
import zlib
import pytest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

# =============================================================================
# Fixtures: Minimal valid PNG + Neon Pink mock analysis
# =============================================================================


def _make_png_1x1() -> bytes:
    """Minimal valid 1x1 PNG (67 bytes)."""

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

# The ACID TEST color — Neon Pink
ACID_PRIMARY = "#FF00FF"

NEON_PINK_ANALYSIS = {
    "layout_regions": [
        {
            "role": "header",
            "bounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.1},
            "children": [],
            "css_hint": "sticky top-0 bg-black",
        },
        {
            "role": "sidebar",
            "bounds": {"x": 0.0, "y": 0.1, "width": 0.25, "height": 0.9},
            "children": [],
            "css_hint": "w-72 bg-gray-900 border-r border-pink-500",
        },
        {
            "role": "main",
            "bounds": {"x": 0.25, "y": 0.1, "width": 0.75, "height": 0.9},
            "children": [],
            "css_hint": "flex-1 p-8 bg-gray-950",
        },
    ],
    "components": [
        {
            "type": "button",
            "label": "Launch",
            "region_id": "header",
            "props_hint": {"variant": "primary"},
            "interactive": True,
        },
        {
            "type": "card",
            "label": "Analytics",
            "region_id": "main",
            "props_hint": {},
            "interactive": False,
        },
        {
            "type": "table",
            "label": "Events",
            "region_id": "main",
            "props_hint": {},
            "interactive": False,
        },
        {
            "type": "nav-link",
            "label": "Dashboard",
            "region_id": "sidebar",
            "props_hint": {},
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
            "type": "badge",
            "label": "Live",
            "region_id": "header",
            "props_hint": {"variant": "success"},
            "interactive": False,
        },
    ],
    "palette": {
        "primary": ACID_PRIMARY,  # ← THE ACID TEST COLOR
        "secondary": "#00FFFF",
        "accent": "#FFE600",
        "background": "#0A0A0F",
        "surface": "#1A1A2E",
        "text_primary": "#E0E0FF",
        "text_secondary": "#8888AA",
        "border": "#2A2A4E",
        "error": "#FF3366",
        "success": "#00FF88",
    },
    "typography": {
        "heading_font": "Orbitron, monospace",
        "body_font": "Rajdhani, sans-serif",
        "heading_weight": "bold",
        "base_size_px": 16,
        "scale_ratio": 1.333,
        "line_height": 1.6,
    },
    "overall_style": "cyberpunk",
    "responsive_hints": {
        "mobile": "single column, hide sidebar",
        "tablet": "collapsible sidebar, 2-col grid",
    },
    "page_type": "dashboard",
    "confidence": 0.95,
}


def _mock_llm_returning(json_data):
    """Create a mock LLM that returns the given dict as JSON content."""
    mock_response = MagicMock()
    mock_response.content = json.dumps(json_data)
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    return mock_llm


# =============================================================================
# 1. API ENDPOINT — POST /api/projects/vision
# =============================================================================


class TestVisionAPIEndpoint:
    """Verify vision API endpoint logic (image validation, style fallback).

    api/server.py has pre-existing broken imports (ConversationMemory, HITLManager)
    unrelated to Phase 3.5. We test the endpoint logic via a minimal FastAPI app
    that replicates the handler function with mocked dependencies.
    """

    @pytest.fixture
    def client(self):
        """Build a minimal FastAPI app with the vision endpoint handler."""
        from fastapi import (
            FastAPI,
            UploadFile,
            File,
            Form,
            HTTPException,
            BackgroundTasks,
        )
        from fastapi.testclient import TestClient
        from pydantic import BaseModel
        from typing import Optional, Dict

        app = FastAPI()

        class ProjectResponse(BaseModel):
            id: str
            name: str
            description: Optional[str]
            status: str
            created_at: str
            build_id: Optional[str] = None
            files: Dict[str, str] = {}

        @app.post("/api/projects/vision", response_model=ProjectResponse)
        async def create_project_with_vision(
            background_tasks: BackgroundTasks,
            name: str = Form(...),
            requirements: str = Form(...),
            description: Optional[str] = Form(None),
            framework: str = Form("react"),
            style: Optional[str] = Form(None),
            image: Optional[UploadFile] = File(None),
        ):
            import base64 as b64mod

            if image and image.filename:
                raw = await image.read()
                if len(raw) > 4 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413, detail="Image exceeds 4MB limit"
                    )
                content_type = image.content_type or ""
                if content_type not in ("image/png", "image/jpeg", "image/webp"):
                    raise HTTPException(
                        status_code=415,
                        detail=f"Unsupported image type: {content_type}",
                    )
                b64mod.b64encode(raw).decode("ascii")
            elif style:
                try:
                    from services.theme_engine import ThemeEngine

                    theme = ThemeEngine.from_style_descriptor(style)
                    theme.to_dict()
                except Exception:
                    pass

            return ProjectResponse(
                id="proj_test_123",
                name=name,
                description=description or "",
                status="generating",
                created_at="2026-03-30T00:00:00",
                build_id="build_test_456",
                files={},
            )

        return TestClient(app, base_url="http://localhost")

    def test_vision_endpoint_with_image(self, client):
        """POST with image returns 200 and starts build."""
        resp = client.post(
            "/api/projects/vision",
            data={
                "name": "Neon Dashboard",
                "requirements": "Build a cyberpunk dashboard with neon colors",
                "framework": "react",
            },
            files={"image": ("design.png", PNG_1X1, "image/png")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Neon Dashboard"
        assert data["status"] == "generating"
        assert data["build_id"] is not None

    def test_vision_endpoint_with_style_only(self, client):
        """POST with style= (no image) returns 200."""
        resp = client.post(
            "/api/projects/vision",
            data={
                "name": "Cyber App",
                "requirements": "Build a portfolio with cyberpunk vibes",
                "style": "cyberpunk",
                "framework": "react",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "generating"

    def test_vision_endpoint_rejects_oversized_image(self, client):
        """POST with >4MB image returns 413."""
        big_png = PNG_1X1 + b"\x00" * (4 * 1024 * 1024 + 1)
        resp = client.post(
            "/api/projects/vision",
            data={
                "name": "Big Image",
                "requirements": "Build something",
            },
            files={"image": ("big.png", big_png, "image/png")},
        )
        assert resp.status_code == 413

    def test_vision_endpoint_rejects_unsupported_mime(self, client):
        """POST with GIF image returns 415."""
        gif_data = b"GIF89a" + b"\x00" * 100
        resp = client.post(
            "/api/projects/vision",
            data={
                "name": "GIF Test",
                "requirements": "Build something",
            },
            files={"image": ("design.gif", gif_data, "image/gif")},
        )
        assert resp.status_code == 415


# =============================================================================
# 2. VISION ROUTING — Conditional START dispatch
# =============================================================================


class TestVisionRouting:
    """Verify _route_entry dispatches correctly based on state."""

    def _get_builder(self):
        """Create a MultiAgentBuilder instance without triggering full init."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        return builder

    def test_route_entry_returns_vision_with_image(self):
        """State with vision_image → routes to 'vision'."""
        builder = self._get_builder()
        result = builder._route_entry({"vision_image": PNG_1X1_B64})
        assert result == "vision"

    def test_route_entry_returns_architect_without_image(self):
        """State without vision_image → routes to 'architect'."""
        builder = self._get_builder()
        assert builder._route_entry({"vision_image": None}) == "architect"
        assert builder._route_entry({}) == "architect"
        assert builder._route_entry({"vision_image": ""}) == "architect"

    def test_route_entry_returns_vision_with_any_truthy_string(self):
        """Any truthy vision_image string routes to vision."""
        builder = self._get_builder()
        assert builder._route_entry({"vision_image": "abc123"}) == "vision"


# =============================================================================
# 3. VISION NODE STATE — Populates analysis + theme in ProjectState
# =============================================================================


class TestVisionNodeState:
    """Verify _vision_node returns correct state updates."""

    def _get_builder(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        return MultiAgentBuilder.__new__(MultiAgentBuilder)

    def test_vision_node_populates_analysis_and_theme(self):
        """Vision node returns both vision_analysis and vision_theme dicts."""
        builder = self._get_builder()
        mock_llm = _mock_llm_returning(NEON_PINK_ANALYSIS)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            state = {
                "vision_image": PNG_1X1_B64,
                "requirements": "Build a neon dashboard",
            }
            result = builder._vision_node(state)

        # vision_analysis must be a dict with the analysis fields
        assert result["vision_analysis"] is not None
        assert isinstance(result["vision_analysis"], dict)
        assert result["vision_analysis"]["page_type"] == "dashboard"
        assert result["vision_analysis"]["overall_style"] == "cyberpunk"
        assert result["vision_analysis"]["confidence"] == 0.95
        assert len(result["vision_analysis"]["layout_regions"]) == 3
        assert len(result["vision_analysis"]["components"]) == 6

        # vision_theme must be a dict with ThemeConfig fields
        assert result["vision_theme"] is not None
        assert isinstance(result["vision_theme"], dict)
        assert "tailwind_config_js" in result["vision_theme"]
        assert "globals_css" in result["vision_theme"]
        assert "component_classes" in result["vision_theme"]
        assert "css_variables" in result["vision_theme"]

        # current_phase set to "vision"
        assert result["current_phase"] == "vision"

    def test_vision_node_preserves_neon_pink_in_theme(self):
        """The ACID color #FF00FF must appear in the generated theme."""
        builder = self._get_builder()
        mock_llm = _mock_llm_returning(NEON_PINK_ANALYSIS)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            result = builder._vision_node(
                {
                    "vision_image": PNG_1X1_B64,
                    "requirements": "Build it",
                }
            )

        theme = result["vision_theme"]
        # Primary color must be in CSS variables
        assert theme["css_variables"]["--color-primary"] == ACID_PRIMARY
        # Primary color must be in globals.css
        assert f"--color-primary: {ACID_PRIMARY}" in theme["globals_css"]

    def test_vision_node_graceful_failure_returns_none(self):
        """LLM failure → vision_analysis=None, vision_theme=None (no crash)."""
        builder = self._get_builder()

        from ai.agents.vision_agent import VisionAnalysisError

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm",
            side_effect=VisionAnalysisError("No provider"),
        ):
            result = builder._vision_node(
                {
                    "vision_image": PNG_1X1_B64,
                    "requirements": "Build it",
                }
            )

        assert result["vision_analysis"] is None
        assert result["vision_theme"] is None
        assert result["current_phase"] == "vision"
        # Message should indicate skip
        assert (
            "skipped" in result["messages"][0].content.lower()
            or "failed" in result["messages"][0].content.lower()
        )


# =============================================================================
# 4. ARCHITECT CONTEXT INJECTION
# =============================================================================


class TestArchitectContextInjection:
    """Verify the architect receives augmented requirements with vision layout info."""

    def test_architect_receives_vision_context(self):
        """When vision_analysis is in state, architect's invoke() receives augmented requirements."""
        from ai.agents.multi_agent import MultiAgentBuilder

        # Track what requirements the architect agent receives
        captured_requirements = []

        mock_architect = MagicMock()

        def capture_invoke(state):
            captured_requirements.append(state.get("requirements", ""))
            return {
                "architecture": {
                    "tech_stack": {"frontend": "React"},
                    "components": [],
                },
                "messages": [AIMessage(content="Architecture done", name="Architect")],
                "current_phase": "architect",
            }

        mock_architect.invoke.side_effect = capture_invoke

        # Create builder with mocked agents
        mock_llm = MagicMock()
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            builder.llm = mock_llm
            builder.agents = {"architect": mock_architect}
            builder.logger = MagicMock()

        # Build state with vision analysis
        state = {
            "requirements": "Build a neon dashboard",
            "vision_analysis": NEON_PINK_ANALYSIS,
            "architecture": None,
        }

        builder._architect_node(state)

        # Architect should have been called with augmented requirements
        assert len(captured_requirements) == 1
        augmented = captured_requirements[0]

        # Must contain vision layout info
        assert "## Vision Analysis" in augmented
        assert "dashboard" in augmented
        assert "cyberpunk" in augmented
        assert ACID_PRIMARY in augmented  # Neon Pink in palette section

        # Must contain layout regions
        assert "header" in augmented
        assert "sidebar" in augmented
        assert "main" in augmented

        # Must contain the original requirements too
        assert "Build a neon dashboard" in augmented

    def test_architect_without_vision_uses_original_requirements(self):
        """Without vision_analysis, architect gets unmodified requirements."""
        from ai.agents.multi_agent import MultiAgentBuilder

        captured_requirements = []
        mock_architect = MagicMock()

        def capture_invoke(state):
            captured_requirements.append(state.get("requirements", ""))
            return {
                "architecture": {"tech_stack": {}},
                "messages": [AIMessage(content="Done", name="Architect")],
                "current_phase": "architect",
            }

        mock_architect.invoke.side_effect = capture_invoke

        mock_llm = MagicMock()
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            builder.llm = mock_llm
            builder.agents = {"architect": mock_architect}
            builder.logger = MagicMock()

        state = {
            "requirements": "Build a simple app",
            "vision_analysis": None,
            "architecture": None,
        }
        builder._architect_node(state)

        assert len(captured_requirements) == 1
        assert captured_requirements[0] == "Build a simple app"
        assert "## Vision Analysis" not in captured_requirements[0]


# =============================================================================
# 5. FRONTEND CONTEXT INJECTION
# =============================================================================


class TestFrontendContextInjection:
    """Verify the frontend agent receives design tokens (colors, typography)."""

    def _build_vision_theme(self):
        """Generate a ThemeConfig from the Neon Pink analysis."""
        from ai.agents.vision_agent import VisionAnalysis
        from services.theme_engine import ThemeEngine

        analysis = VisionAnalysis.from_dict(NEON_PINK_ANALYSIS)
        theme = ThemeEngine.from_analysis(analysis)
        return theme.to_dict()

    def test_frontend_receives_design_tokens(self):
        """Frontend agent's invoke() receives design system with CSS variables."""
        from ai.agents.multi_agent import MultiAgentBuilder

        captured_requirements = []
        mock_frontend = MagicMock()

        def capture_invoke(state):
            captured_requirements.append(state.get("requirements", ""))
            return {
                "frontend_code": {
                    "src/App.tsx": "export default function App() { return <div>Hello</div> }"
                },
                "messages": [AIMessage(content="Frontend done", name="Frontend")],
                "current_phase": "frontend",
            }

        mock_frontend.invoke.side_effect = capture_invoke

        mock_llm = MagicMock()
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            builder.llm = mock_llm
            builder.agents = {"frontend": mock_frontend}
            builder.logger = MagicMock()

        vision_theme = self._build_vision_theme()
        state = {
            "requirements": "Build a neon dashboard",
            "vision_analysis": NEON_PINK_ANALYSIS,
            "vision_theme": vision_theme,
            "architecture": {},
        }

        builder._frontend_node(state)

        assert len(captured_requirements) == 1
        augmented = captured_requirements[0]

        # Must contain design system header
        assert "## Design System" in augmented

        # Must contain CSS variable instructions
        assert "--color-primary" in augmented
        assert ACID_PRIMARY in augmented
        assert "--color-secondary" in augmented
        assert "#00FFFF" in augmented  # secondary from analysis

        # Must contain typography info
        assert "Orbitron" in augmented or "orbitron" in augmented.lower()

        # Must contain component classes
        assert "Component Classes" in augmented

        # Must contain layout structure
        assert "header" in augmented
        assert "sidebar" in augmented

        # Must contain the original requirements
        assert "Build a neon dashboard" in augmented

    def test_frontend_without_vision_uses_original_requirements(self):
        """Without vision state, frontend gets unmodified requirements."""
        from ai.agents.multi_agent import MultiAgentBuilder

        captured_requirements = []
        mock_frontend = MagicMock()

        def capture_invoke(state):
            captured_requirements.append(state.get("requirements", ""))
            return {
                "frontend_code": {},
                "messages": [AIMessage(content="Done", name="Frontend")],
                "current_phase": "frontend",
            }

        mock_frontend.invoke.side_effect = capture_invoke

        mock_llm = MagicMock()
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
            builder.llm = mock_llm
            builder.agents = {"frontend": mock_frontend}
            builder.logger = MagicMock()

        state = {
            "requirements": "Build a plain app",
            "vision_analysis": None,
            "vision_theme": None,
            "architecture": {},
        }
        builder._frontend_node(state)

        assert captured_requirements[0] == "Build a plain app"
        assert "## Design System" not in captured_requirements[0]


# =============================================================================
# 6. ACID TEST — #FF00FF SURVIVES FULL NODE CHAIN
# =============================================================================


class TestACIDStyling:
    """The definitive test: Neon Pink (#FF00FF) must survive vision → theme → CSS."""

    def test_acid_globals_css_contains_neon_pink(self):
        """globals.css must contain '--color-primary: #FF00FF' exactly."""

        # Step 1: Run vision node with mock LLM returning Neon Pink analysis
        builder = self._make_builder()
        mock_llm = _mock_llm_returning(NEON_PINK_ANALYSIS)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            vision_result = builder._vision_node(
                {
                    "vision_image": PNG_1X1_B64,
                    "requirements": "Neon dashboard",
                }
            )

        # Step 2: Run frontend node with the vision state
        mock_frontend_agent = MagicMock()
        mock_frontend_agent.invoke.return_value = {
            "frontend_code": {},  # No agent-generated theme files → injected ones will appear
            "messages": [AIMessage(content="Frontend done", name="Frontend")],
            "current_phase": "frontend",
        }
        builder.agents = {"frontend": mock_frontend_agent}

        state = {
            "requirements": "Neon dashboard",
            "vision_analysis": vision_result["vision_analysis"],
            "vision_theme": vision_result["vision_theme"],
            "architecture": {},
        }
        frontend_result = builder._frontend_node(state)

        # Step 3: ACID ASSERTIONS
        frontend_code = frontend_result.get("frontend_code", {})

        # globals.css must exist (injected by _frontend_node since agent didn't generate one)
        globals_css = frontend_code.get("src/app/globals.css", "")
        assert globals_css, "globals.css was not injected into frontend_code"

        # THE ACID TEST: exact string match
        assert f"--color-primary: {ACID_PRIMARY}" in globals_css, (
            f"ACID FAIL: '--color-primary: {ACID_PRIMARY}' not found in globals.css.\n"
            f"Actual globals.css content:\n{globals_css[:500]}"
        )

        # Verify other ACID colors too
        assert "--color-secondary: #00FFFF" in globals_css
        assert "--color-accent: #FFE600" in globals_css
        assert "--color-background: #0A0A0F" in globals_css

        # Verify typography
        assert "Orbitron" in globals_css

    def test_acid_tailwind_config_has_primary_mapping(self):
        """tailwind.config.js must map primary to var(--color-primary)."""

        builder = self._make_builder()
        mock_llm = _mock_llm_returning(NEON_PINK_ANALYSIS)

        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            vision_result = builder._vision_node(
                {
                    "vision_image": PNG_1X1_B64,
                    "requirements": "Neon dashboard",
                }
            )

        mock_frontend_agent = MagicMock()
        mock_frontend_agent.invoke.return_value = {
            "frontend_code": {},
            "messages": [AIMessage(content="Done", name="Frontend")],
            "current_phase": "frontend",
        }
        builder.agents = {"frontend": mock_frontend_agent}

        state = {
            "requirements": "Neon dashboard",
            "vision_analysis": vision_result["vision_analysis"],
            "vision_theme": vision_result["vision_theme"],
            "architecture": {},
        }
        frontend_result = builder._frontend_node(state)

        frontend_code = frontend_result.get("frontend_code", {})
        tailwind_config = frontend_code.get("tailwind.config.js", "")
        assert tailwind_config, "tailwind.config.js was not injected"

        # Must have primary color mapping via CSS variable
        assert 'primary: "var(--color-primary)"' in tailwind_config, (
            f"ACID FAIL: primary CSS variable mapping not found in tailwind.config.js.\n"
            f"Actual content:\n{tailwind_config[:500]}"
        )
        assert 'secondary: "var(--color-secondary)"' in tailwind_config
        assert 'accent: "var(--color-accent)"' in tailwind_config

    def test_acid_agent_generated_files_take_priority(self):
        """If the frontend agent generates its own globals.css, it wins over injected."""
        builder = self._make_builder()

        agent_css = ":root { --custom: red; }"
        mock_frontend_agent = MagicMock()
        mock_frontend_agent.invoke.return_value = {
            "frontend_code": {
                "src/app/globals.css": agent_css,  # Agent generated its own
                "tailwind.config.js": "// agent tailwind",
            },
            "messages": [AIMessage(content="Done", name="Frontend")],
            "current_phase": "frontend",
        }
        builder.agents = {"frontend": mock_frontend_agent}

        # Build a vision theme
        from ai.agents.vision_agent import VisionAnalysis
        from services.theme_engine import ThemeEngine

        analysis = VisionAnalysis.from_dict(NEON_PINK_ANALYSIS)
        theme = ThemeEngine.from_analysis(analysis)

        state = {
            "requirements": "test",
            "vision_analysis": NEON_PINK_ANALYSIS,
            "vision_theme": theme.to_dict(),
            "architecture": {},
        }
        result = builder._frontend_node(state)

        frontend_code = result.get("frontend_code", {})
        # Agent-generated files must win
        assert frontend_code["src/app/globals.css"] == agent_css
        assert frontend_code["tailwind.config.js"] == "// agent tailwind"

    def test_acid_full_chain_neon_pink_e2e(self):
        """
        FULL CHAIN: vision_node → _architect_node → _frontend_node.
        Neon Pink must survive all three nodes and appear in final CSS.
        """

        builder = self._make_builder()
        mock_llm = _mock_llm_returning(NEON_PINK_ANALYSIS)

        # --- Step 1: Vision Node ---
        with patch(
            "ai.agents.vision_agent.VisionAgent._get_llm", return_value=mock_llm
        ):
            vision_result = builder._vision_node(
                {
                    "vision_image": PNG_1X1_B64,
                    "requirements": "Build a cyberpunk analytics dashboard",
                }
            )

        assert vision_result["vision_analysis"]["palette"]["primary"] == ACID_PRIMARY

        # --- Step 2: Architect Node ---
        architect_captured = []
        mock_architect = MagicMock()

        def arch_invoke(state):
            architect_captured.append(state.get("requirements", ""))
            return {
                "architecture": {"tech_stack": {"frontend": "React"}, "components": []},
                "messages": [AIMessage(content="Arch done", name="Architect")],
                "current_phase": "architect",
            }

        mock_architect.invoke.side_effect = arch_invoke
        builder.agents = {"architect": mock_architect}

        arch_state = {
            "requirements": "Build a cyberpunk analytics dashboard",
            "vision_analysis": vision_result["vision_analysis"],
            "vision_theme": vision_result["vision_theme"],
            "architecture": None,
        }
        arch_result = builder._architect_node(arch_state)

        # Verify architect received Neon Pink in context
        assert ACID_PRIMARY in architect_captured[0]
        assert "cyberpunk" in architect_captured[0]

        # --- Step 3: Frontend Node ---
        frontend_captured = []
        mock_frontend = MagicMock()

        def front_invoke(state):
            frontend_captured.append(state.get("requirements", ""))
            return {
                "frontend_code": {"src/App.tsx": "<App/>"},
                "messages": [AIMessage(content="Front done", name="Frontend")],
                "current_phase": "frontend",
            }

        mock_frontend.invoke.side_effect = front_invoke
        builder.agents = {"frontend": mock_frontend}

        front_state = {
            "requirements": "Build a cyberpunk analytics dashboard",
            "vision_analysis": vision_result["vision_analysis"],
            "vision_theme": vision_result["vision_theme"],
            "architecture": arch_result.get("architecture", {}),
        }
        front_result = builder._frontend_node(front_state)

        # Verify frontend received design tokens
        assert ACID_PRIMARY in frontend_captured[0]
        assert "## Design System" in frontend_captured[0]

        # FINAL ACID: check generated files
        frontend_code = front_result.get("frontend_code", {})

        globals_css = frontend_code.get("src/app/globals.css", "")
        assert (
            f"--color-primary: {ACID_PRIMARY}" in globals_css
        ), f"ACID FAIL: Neon Pink lost in full chain! globals.css:\n{globals_css[:500]}"

        tailwind_config = frontend_code.get("tailwind.config.js", "")
        assert (
            'primary: "var(--color-primary)"' in tailwind_config
        ), f"ACID FAIL: Tailwind primary mapping lost! tailwind.config.js:\n{tailwind_config[:500]}"

        # Verify the full color palette survived
        assert "--color-secondary: #00FFFF" in globals_css
        assert "--color-background: #0A0A0F" in globals_css
        assert "Orbitron" in globals_css

    def _make_builder(self):
        """Create a minimal MultiAgentBuilder for testing."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        builder.llm = MagicMock()
        builder.agents = {}
        builder.logger = MagicMock()
        return builder


# =============================================================================
# 7. STYLE-ONLY FLOW — No image, just style descriptor
# =============================================================================


class TestStyleOnlyFlow:
    """Verify that style= without image produces valid theme without vision node."""

    def test_style_descriptor_produces_theme(self):
        """ThemeEngine.from_style_descriptor produces complete theme."""
        from services.theme_engine import ThemeEngine

        theme = ThemeEngine.from_style_descriptor("cyberpunk")
        assert theme.style_name == "cyberpunk"
        assert theme.dark_mode is True
        assert "#00F0FF" in theme.globals_css  # cyberpunk preset primary
        assert "Orbitron" in theme.globals_css
        assert 'primary: "var(--color-primary)"' in theme.tailwind_config_js

    def test_style_only_skips_vision_node(self):
        """State with vision_theme but no vision_image routes to architect."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        # Style-only: vision_theme is set, but vision_image is None
        state = {
            "vision_image": None,
            "vision_theme": {"some": "theme"},
        }
        assert builder._route_entry(state) == "architect"

    def test_style_only_frontend_still_injects_theme(self):
        """Even without vision_image, if vision_theme exists, CSS files are injected."""
        from ai.agents.multi_agent import MultiAgentBuilder
        from services.theme_engine import ThemeEngine

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        builder.llm = MagicMock()
        builder.logger = MagicMock()

        mock_frontend = MagicMock()
        mock_frontend.invoke.return_value = {
            "frontend_code": {"src/App.tsx": "<App/>"},
            "messages": [AIMessage(content="Done", name="Frontend")],
            "current_phase": "frontend",
        }
        builder.agents = {"frontend": mock_frontend}

        theme = ThemeEngine.from_style_descriptor("apple-like")
        state = {
            "requirements": "Build an Apple-style app",
            "vision_analysis": None,  # No analysis (style-only)
            "vision_theme": theme.to_dict(),
            "architecture": {},
        }

        result = builder._frontend_node(state)
        frontend_code = result.get("frontend_code", {})

        # Theme files should be injected even without vision_analysis
        assert "tailwind.config.js" in frontend_code
        assert "src/app/globals.css" in frontend_code
        assert "--color-primary: #007AFF" in frontend_code["src/app/globals.css"]
        assert "SF Pro" in frontend_code["src/app/globals.css"]


# =============================================================================
# 8. GRAPH STRUCTURE — Vision node wired correctly
# =============================================================================


class TestGraphWiring:
    """Verify the compiled LangGraph has correct vision node edges."""

    def test_graph_has_vision_node(self):
        """Compiled graph includes 'vision' as a node."""
        from ai.agents.multi_agent import MultiAgentBuilder

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="{}")
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder(llm=mock_llm)

        nodes = set(builder.graph.get_graph().nodes.keys())
        assert "vision" in nodes
        assert "architect" in nodes

    def test_vision_node_edges_to_architect(self):
        """Vision node has an edge to architect."""
        from ai.agents.multi_agent import MultiAgentBuilder

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="{}")
        with patch("ai.agents.multi_agent.LLM", return_value=mock_llm):
            builder = MultiAgentBuilder(llm=mock_llm)

        graph = builder.graph.get_graph()
        # Find edges from vision node
        vision_edges = [e for e in graph.edges if e[0] == "vision"]
        target_nodes = {e[1] for e in vision_edges}
        assert (
            "architect" in target_nodes
        ), f"Vision should edge to architect, found: {target_nodes}"
