"""
Vision Agent — Phase 3.5: Creative Power (IE-6)
================================================

Multimodal analysis of design screenshots, wireframes, and Figma exports.
Produces structured VisionAnalysis consumed by downstream pipeline agents.

Usage:
    agent = VisionAgent()
    analysis = await agent.analyze(image_b64, "Build a dashboard")
    context = agent.to_architect_context(analysis)
"""

import base64
import colorsys
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("vos3.vision_agent")


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class LayoutRegion:
    """A rectangular region detected in the image."""

    role: str  # "header", "sidebar", "main", "footer", "card", "hero", "nav", "form"
    bounds: Dict[
        str, float
    ]  # {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.08} — normalized 0-1
    children: List[str] = field(default_factory=list)  # IDs of nested regions
    css_hint: str = ""  # e.g. "sticky top-0", "grid grid-cols-3 gap-4"


@dataclass
class DetectedComponent:
    """A UI component detected in the image."""

    type: str  # "button", "input", "card", "table", "image", "icon", "nav-link", etc.
    label: str  # Visible text or inferred purpose
    region_id: str = ""  # Which LayoutRegion contains this component
    props_hint: Dict[str, str] = field(default_factory=dict)
    interactive: bool = False


@dataclass
class ColorPalette:
    """Extracted color scheme from the image."""

    primary: str = "#3B82F6"
    secondary: str = "#10B981"
    accent: str = "#F59E0B"
    background: str = "#FFFFFF"
    surface: str = "#F3F4F6"
    text_primary: str = "#111827"
    text_secondary: str = "#6B7280"
    border: str = "#E5E7EB"
    error: str = "#EF4444"
    success: str = "#10B981"


@dataclass
class Typography:
    """Detected typography system."""

    heading_font: str = "Inter"
    body_font: str = "Inter"
    heading_weight: str = "bold"
    base_size_px: int = 16
    scale_ratio: float = 1.25
    line_height: float = 1.5


@dataclass
class VisionAnalysis:
    """Complete structured analysis of an uploaded design image."""

    layout_regions: List[LayoutRegion] = field(default_factory=list)
    components: List[DetectedComponent] = field(default_factory=list)
    palette: ColorPalette = field(default_factory=ColorPalette)
    typography: Typography = field(default_factory=Typography)
    overall_style: str = "minimal"
    responsive_hints: Dict[str, str] = field(default_factory=dict)
    page_type: str = "dashboard"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisionAnalysis":
        """Reconstruct VisionAnalysis from a dict (e.g., from ProjectState)."""
        regions = [LayoutRegion(**r) for r in data.get("layout_regions", [])]
        components = [DetectedComponent(**c) for c in data.get("components", [])]
        palette = (
            ColorPalette(**data["palette"]) if "palette" in data else ColorPalette()
        )
        typography = (
            Typography(**data["typography"]) if "typography" in data else Typography()
        )
        return cls(
            layout_regions=regions,
            components=components,
            palette=palette,
            typography=typography,
            overall_style=data.get("overall_style", "minimal"),
            responsive_hints=data.get("responsive_hints", {}),
            page_type=data.get("page_type", "dashboard"),
            confidence=data.get("confidence", 0.0),
        )


class VisionAnalysisError(Exception):
    """Raised when vision analysis fails irrecoverably."""

    pass


# =============================================================================
# DesignContract — Pydantic bridge between vision analysis and code generation
# =============================================================================


def _hex_to_hsl(hex_str: str) -> str:
    """Convert '#RRGGBB' hex to HSL string 'H S% L%' for Tailwind CSS variables."""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) != 6:
        return "0 0% 0%"
    r, g, b = (
        int(hex_str[:2], 16) / 255.0,
        int(hex_str[2:4], 16) / 255.0,
        int(hex_str[4:6], 16) / 255.0,
    )
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return f"{round(h * 360)} {round(s * 100)}% {round(l * 100)}%"


_SHADCN_MAP: Dict[str, tuple] = {
    "button": ("Button", "default"),
    "input": ("Input", "default"),
    "card": ("Card", "default"),
    "table": ("Table", "default"),
    "dropdown": ("DropdownMenu", "default"),
    "modal": ("Dialog", "default"),
    "tabs": ("Tabs", "default"),
    "avatar": ("Avatar", "default"),
    "badge": ("Badge", "default"),
    "nav-link": ("NavigationMenu", "default"),
    "icon": ("Button", "ghost"),
    "image": ("AspectRatio", "default"),
    "chart": ("Card", "default"),
}


class DesignToken(BaseModel):
    """HSL-based design token for Tailwind CSS variable generation."""

    name: str
    hsl: str = Field(pattern=r"^\d{1,3}\s\d{1,3}%\s\d{1,3}%$")
    hex_fallback: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class SpacingScale(BaseModel):
    """rem-based spacing scale for consistent layout."""

    base_rem: float = 1.0
    scale: List[float] = Field(
        default_factory=lambda: [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    )


class FontSpec(BaseModel):
    """Typography specification."""

    family: str = "Inter"
    weights: List[int] = Field(default_factory=lambda: [400, 500, 600, 700])
    base_size_rem: float = 1.0
    line_height: float = 1.5
    scale_ratio: float = 1.25


class OCRContent(BaseModel):
    """Extracted text content from the design image."""

    region_id: str
    text: str
    semantic_role: str  # "heading" | "body" | "label" | "placeholder" | "cta"


class ComponentMapping(BaseModel):
    """Maps a detected visual pattern to a shadcn/ui component."""

    detected_type: str
    shadcn_component: str
    variant: str = "default"
    size: str = "default"
    props: Dict[str, str] = Field(default_factory=dict)


class DesignContract(BaseModel):
    """Validated contract between vision analysis and code generation.

    Single source of truth: every downstream agent (architect,
    frontend, tester) reads from this contract instead of raw vision dicts.
    """

    color_tokens: List[DesignToken]
    spacing: SpacingScale = Field(default_factory=SpacingScale)
    heading_font: FontSpec = Field(default_factory=FontSpec)
    body_font: FontSpec = Field(default_factory=FontSpec)

    ocr_content: List[OCRContent] = Field(default_factory=list)
    component_map: List[ComponentMapping] = Field(default_factory=list)

    page_type: str = "dashboard"
    overall_style: str = "minimal"
    responsive_breakpoints: Dict[str, str] = Field(default_factory=dict)

    confidence: float = 0.0
    source_analysis_id: Optional[str] = None

    @field_validator("color_tokens")
    @classmethod
    def require_minimum_tokens(cls, v):
        names = {t.name for t in v}
        required = {"primary", "background", "text_primary"}
        missing = required - names
        if missing:
            raise ValueError(f"Missing required color tokens: {missing}")
        return v

    @classmethod
    def from_vision_analysis(cls, analysis: "VisionAnalysis") -> "DesignContract":
        """Convert a VisionAnalysis dataclass into a validated DesignContract."""
        palette = analysis.palette
        typo = analysis.typography

        # Build color tokens from palette fields
        palette_dict = asdict(palette)
        color_tokens = []
        for name, hex_val in palette_dict.items():
            if isinstance(hex_val, str) and hex_val.startswith("#"):
                color_tokens.append(
                    DesignToken(
                        name=name,
                        hsl=_hex_to_hsl(hex_val),
                        hex_fallback=hex_val,
                    )
                )

        # Build component mappings
        component_map = []
        for comp in analysis.components:
            ctype = comp.type.lower()
            if ctype in _SHADCN_MAP:
                shadcn_name, variant = _SHADCN_MAP[ctype]
            else:
                shadcn_name, variant = "Card", "default"
            component_map.append(
                ComponentMapping(
                    detected_type=comp.type,
                    shadcn_component=shadcn_name,
                    variant=variant,
                    props=comp.props_hint,
                )
            )

        # Build OCR content from component labels
        ocr_content = []
        for comp in analysis.components:
            if comp.label and comp.label.strip():
                role = "cta" if comp.type == "button" else "label"
                ocr_content.append(
                    OCRContent(
                        region_id=comp.region_id or "main",
                        text=comp.label,
                        semantic_role=role,
                    )
                )

        return cls(
            color_tokens=color_tokens,
            heading_font=FontSpec(
                family=typo.heading_font,
                base_size_rem=typo.base_size_px / 16.0,
                line_height=typo.line_height,
                scale_ratio=typo.scale_ratio,
            ),
            body_font=FontSpec(
                family=typo.body_font,
                base_size_rem=typo.base_size_px / 16.0,
                line_height=typo.line_height,
                scale_ratio=typo.scale_ratio,
            ),
            ocr_content=ocr_content,
            component_map=component_map,
            page_type=analysis.page_type,
            overall_style=analysis.overall_style,
            responsive_breakpoints=analysis.responsive_hints,
            confidence=analysis.confidence,
        )


# =============================================================================
# Prompts
# =============================================================================

VISION_ANALYSIS_PROMPT = """You are a design analysis expert. Analyze this UI screenshot/wireframe and extract
a structured description in JSON format.

{requirements_context}

Return a JSON object with these exact keys:
{{
  "layout_regions": [
    {{
      "role": "header|sidebar|main|footer|card|hero|nav|form",
      "bounds": {{"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.08}},
      "children": [],
      "css_hint": "sticky top-0"
    }}
  ],
  "components": [
    {{
      "type": "button|input|card|table|image|icon|nav-link|dropdown|modal|tabs|avatar|badge|chart",
      "label": "Submit",
      "region_id": "header",
      "props_hint": {{"variant": "primary", "size": "lg"}},
      "interactive": true
    }}
  ],
  "palette": {{
    "primary": "#3B82F6",
    "secondary": "#10B981",
    "accent": "#F59E0B",
    "background": "#FFFFFF",
    "surface": "#F3F4F6",
    "text_primary": "#111827",
    "text_secondary": "#6B7280",
    "border": "#E5E7EB",
    "error": "#EF4444",
    "success": "#10B981"
  }},
  "typography": {{
    "heading_font": "Inter",
    "body_font": "Inter",
    "heading_weight": "bold",
    "base_size_px": 16,
    "scale_ratio": 1.25,
    "line_height": 1.5
  }},
  "overall_style": "minimal|corporate|playful|dark-mode|glassmorphism|brutalist|apple-like|material-design|cyberpunk",
  "responsive_hints": {{
    "mobile": "stack vertically, hide sidebar",
    "tablet": "2-col grid, collapsible nav"
  }},
  "page_type": "landing|dashboard|form|settings|profile|list",
  "confidence": 0.85
}}

Rules:
- Use normalized coordinates (0-1) for layout_regions bounds
- Infer CSS hints as Tailwind utility classes where possible
- For colors, always return 6-digit hex (#RRGGBB)
- For typography, infer from visual appearance (you cannot read font metadata)
- If the image is a wireframe/sketch (grayscale, hand-drawn), set confidence < 0.6
  and use placeholder colors (#3B82F6 for primary, etc.)
- If the user provided requirements, let them override ambiguous visual elements

Return ONLY valid JSON. No markdown. No explanation."""

VISION_REPAIR_PROMPT = """Your previous response was not valid JSON. Here is the raw text:
{raw_response}

Please fix the JSON syntax and return ONLY the corrected JSON object.
Do not include any explanation or markdown formatting — just the raw JSON."""


# =============================================================================
# VisionAgent
# =============================================================================


class VisionAgent:
    """Multimodal analysis of design screenshots/wireframes.

    Routes to Claude Opus 4.6 (reviewer role, complexity=8) for structured extraction.
    Single LLM call per image (two if JSON repair needed).
    """

    MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB limit
    SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp"}

    def __init__(self, llm_provider: str = "auto"):
        self.llm_provider = llm_provider

    def validate_image(self, image_b64: str) -> Tuple[bytes, str]:
        """Decode base64, validate size and MIME type. Returns (raw_bytes, mime_type)."""
        try:
            raw = base64.b64decode(image_b64)
        except Exception as e:
            raise ValueError(f"Invalid base64 image data: {e}")

        if len(raw) > self.MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image size {len(raw)} bytes exceeds {self.MAX_IMAGE_BYTES} byte limit"
            )

        # Detect MIME from magic bytes
        mime = self._detect_mime(raw)
        if mime not in self.SUPPORTED_MIME:
            raise ValueError(
                f"Unsupported image type: {mime}. Supported: {self.SUPPORTED_MIME}"
            )

        return raw, mime

    async def analyze(self, image_b64: str, requirements: str = "") -> VisionAnalysis:
        """Analyze image and return structured VisionAnalysis.

        Steps:
        1. validate_image() — size, MIME check
        2. Build multimodal prompt with image + VISION_ANALYSIS_PROMPT
        3. Call LLM via assign_model("reviewer", complexity=8) → Claude Opus 4.6
        4. Parse JSON response into VisionAnalysis
        5. If parsing fails, retry once with VISION_REPAIR_PROMPT
        """
        raw_bytes, mime_type = self.validate_image(image_b64)

        requirements_context = ""
        if requirements:
            requirements_context = f"User requirements: {requirements}\n\nUse these to guide your analysis — they override ambiguous visual elements."

        prompt = VISION_ANALYSIS_PROMPT.format(
            requirements_context=requirements_context
        )

        # Get LLM via SmartRouter
        llm = self._get_llm()

        # Build multimodal message
        content_blocks = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
            },
            {"type": "text", "text": prompt},
        ]

        try:
            from langchain_core.messages import HumanMessage

            msg = HumanMessage(content=content_blocks)
            response = llm.invoke([msg])
            raw_text = (
                response.content if hasattr(response, "content") else str(response)
            )
        except Exception as e:
            logger.error("[VISION] LLM call failed: %s", e)
            raise VisionAnalysisError(f"LLM analysis failed: {e}") from e

        # Parse response
        analysis = self._parse_response(raw_text)
        if analysis is not None:
            return analysis

        # JSON repair attempt
        logger.warning("[VISION] First parse failed, attempting JSON repair")
        repair_prompt = VISION_REPAIR_PROMPT.format(raw_response=raw_text[:4000])
        try:
            from langchain_core.messages import HumanMessage as HM

            repair_response = llm.invoke([HM(content=repair_prompt)])
            repair_text = (
                repair_response.content
                if hasattr(repair_response, "content")
                else str(repair_response)
            )
            analysis = self._parse_response(repair_text)
            if analysis is not None:
                return analysis
        except Exception as e:
            logger.error("[VISION] Repair call failed: %s", e)

        raise VisionAnalysisError(
            "Failed to parse vision analysis after repair attempt"
        )

    def to_architect_context(self, analysis: VisionAnalysis) -> str:
        """Format VisionAnalysis as context for the Architect agent."""
        regions_desc = "\n".join(
            f"  - {r.role}: {r.css_hint}" for r in analysis.layout_regions
        )
        components_desc = "\n".join(
            f"  - {c.type}: \"{c.label}\" ({'interactive' if c.interactive else 'static'})"
            for c in analysis.components[:20]
        )
        return f"""## Vision Analysis (from uploaded design)
Page Type: {analysis.page_type} | Style: {analysis.overall_style} | Confidence: {analysis.confidence:.0%}

### Layout Regions
{regions_desc or '  (none detected)'}

### Detected Components ({len(analysis.components)} total)
{components_desc or '  (none detected)'}

### Color Palette
Primary: {analysis.palette.primary} | Secondary: {analysis.palette.secondary} | Accent: {analysis.palette.accent}
Background: {analysis.palette.background} | Surface: {analysis.palette.surface}

### Typography
Heading: {analysis.typography.heading_font} ({analysis.typography.heading_weight})
Body: {analysis.typography.body_font} | Base: {analysis.typography.base_size_px}px | Scale: {analysis.typography.scale_ratio}

### Responsive Hints
{json.dumps(analysis.responsive_hints, indent=2) if analysis.responsive_hints else '  (none)'}

Use this visual analysis to inform your architecture decisions. The design shows a {analysis.page_type} with {analysis.overall_style} styling."""

    def to_frontend_context(self, analysis: VisionAnalysis, theme: Any) -> str:
        """Format VisionAnalysis + theme as context for the Frontend agent."""
        # Extract theme info
        if isinstance(theme, dict):
            component_classes = theme.get("component_classes", {})
            css_vars = theme.get("css_variables", {})
        else:
            component_classes = getattr(theme, "component_classes", {})
            css_vars = getattr(theme, "css_variables", {})

        classes_desc = "\n".join(
            f'  {name}: "{classes}"' for name, classes in component_classes.items()
        )
        vars_desc = "\n".join(
            f"  {name}: {value}" for name, value in list(css_vars.items())[:15]
        )
        responsive = "\n".join(
            f"  {bp}: {hint}" for bp, hint in analysis.responsive_hints.items()
        )

        return f"""## Design System (from uploaded design — apply these EXACTLY)
Style: {analysis.overall_style} | Page Type: {analysis.page_type}

### CSS Variables (use these, DO NOT hardcode colors)
{vars_desc or '  (defaults)'}

### Component Classes (apply via className)
{classes_desc or '  (defaults)'}

### Responsive Behavior
{responsive or '  Use standard responsive patterns'}

### Layout Structure
{chr(10).join(f'  - {r.role} ({r.css_hint})' for r in analysis.layout_regions)}

### Components to Implement
{chr(10).join(f'  - {c.type}: "{c.label}" in {c.region_id}' for c in analysis.components[:25])}

IMPORTANT: Use the CSS variables (var(--color-primary), etc.) and component classes above.
Do NOT hardcode hex colors. The tailwind.config.js and globals.css are already generated."""

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_llm(self):
        """Get LLM via SmartRouter — routes to Claude Opus 4.6 for multimodal."""
        try:
            from src.efficiency.factory import get_llm_for_task

            return get_llm_for_task(role="reviewer", complexity=8)
        except ImportError:
            pass

        try:
            from src.efficiency.router import assign_model

            model_id = assign_model("reviewer", complexity=8)
            logger.info("[VISION] SmartRouter selected: %s", model_id)
        except ImportError:
            pass

        # Fallback: direct Anthropic
        try:
            from langchain_anthropic import ChatAnthropic
            import os

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                return ChatAnthropic(model="claude-opus-4-6", api_key=api_key)
        except ImportError:
            pass

        raise VisionAnalysisError(
            "No multimodal LLM available — set ANTHROPIC_API_KEY or configure SmartRouter"
        )

    def _parse_response(self, text: str) -> Optional[VisionAnalysis]:
        """Parse LLM response text into VisionAnalysis. Returns None on failure."""
        # Strip markdown fences
        cleaned = text.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if fence_match:
            cleaned = fence_match.group(1).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        try:
            return VisionAnalysis.from_dict(data)
        except Exception as e:
            logger.warning(
                "[VISION] Failed to construct VisionAnalysis from dict: %s", e
            )
            return None

    @staticmethod
    def _detect_mime(raw: bytes) -> str:
        """Detect image MIME type from magic bytes."""
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if raw[:2] == b"\xff\xd8":
            return "image/jpeg"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"
