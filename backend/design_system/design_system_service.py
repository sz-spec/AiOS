"""
UI/UX Design System Service

Apple-inspired design system with:
- Design tokens (colors, typography, spacing)
- Component definitions
- Animation presets
- Figma integration
- Theme generation

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
import re

# ============================================
# Design Tokens
# ============================================


class ColorMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


@dataclass
class ColorToken:
    """Color design token."""

    name: str
    light: str  # Hex value for light mode
    dark: str  # Hex value for dark mode
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "light": self.light,
            "dark": self.dark,
            "description": self.description,
        }


@dataclass
class TypographyToken:
    """Typography design token."""

    name: str
    font_family: str
    font_size: str  # in rem or pt
    font_weight: int
    line_height: float
    letter_spacing: str = "0"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fontFamily": self.font_family,
            "fontSize": self.font_size,
            "fontWeight": self.font_weight,
            "lineHeight": self.line_height,
            "letterSpacing": self.letter_spacing,
        }


@dataclass
class SpacingToken:
    """Spacing design token."""

    name: str
    value: str  # in rem
    px: int  # pixel equivalent

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "px": self.px}


@dataclass
class ShadowToken:
    """Shadow design token."""

    name: str
    value: str  # CSS box-shadow value

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value}


@dataclass
class RadiusToken:
    """Border radius token."""

    name: str
    value: str

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value}


@dataclass
class AnimationToken:
    """Animation design token."""

    name: str
    duration: str  # e.g., "0.4s"
    easing: str  # e.g., "ease-in-out"
    keyframes: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration": self.duration,
            "easing": self.easing,
            "keyframes": self.keyframes,
        }


# ============================================
# Component Definitions
# ============================================


@dataclass
class ComponentVariant:
    """Component variant definition."""

    name: str
    styles: dict[str, str]

    def to_dict(self) -> dict:
        return {"name": self.name, "styles": self.styles}


@dataclass
class ComponentDefinition:
    """UI Component definition."""

    id: str
    name: str
    description: str
    category: str  # button, input, card, layout, feedback, navigation
    variants: list[ComponentVariant] = field(default_factory=list)
    props: list[dict] = field(default_factory=list)
    figma_node_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "variants": [v.to_dict() for v in self.variants],
            "props": self.props,
            "figmaNodeId": self.figma_node_id,
        }


# ============================================
# Design System
# ============================================


@dataclass
class DesignSystem:
    """Complete design system definition."""

    id: str = field(default_factory=lambda: f"ds_{uuid4().hex[:12]}")
    name: str = "V Design System"
    version: str = "1.0.0"

    # Tokens
    colors: list[ColorToken] = field(default_factory=list)
    typography: list[TypographyToken] = field(default_factory=list)
    spacing: list[SpacingToken] = field(default_factory=list)
    shadows: list[ShadowToken] = field(default_factory=list)
    radii: list[RadiusToken] = field(default_factory=list)
    animations: list[AnimationToken] = field(default_factory=list)

    # Components
    components: list[ComponentDefinition] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "tokens": {
                "colors": [c.to_dict() for c in self.colors],
                "typography": [t.to_dict() for t in self.typography],
                "spacing": [s.to_dict() for s in self.spacing],
                "shadows": [s.to_dict() for s in self.shadows],
                "radii": [r.to_dict() for r in self.radii],
                "animations": [a.to_dict() for a in self.animations],
            },
            "components": [c.to_dict() for c in self.components],
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


# ============================================
# V Design System (Apple-inspired)
# ============================================


def create_v_design_system() -> DesignSystem:
    """Create the V design system based on PRD specs."""
    return DesignSystem(
        id="ds_v_system",
        name="V Design System",
        version="1.0.0",
        # Colors - Apple-inspired palette
        colors=[
            # Backgrounds
            ColorToken("background", "#FFFFFF", "#000000", "Primary background"),
            ColorToken(
                "background-secondary", "#F5F5F7", "#1C1C1E", "Secondary background"
            ),
            ColorToken(
                "background-tertiary", "#E8E8ED", "#2C2C2E", "Tertiary background"
            ),
            # Text
            ColorToken("text-primary", "#1D1D1F", "#F5F5F7", "Primary text"),
            ColorToken("text-secondary", "#6E6E73", "#8E8E93", "Secondary text"),
            ColorToken("text-tertiary", "#86868B", "#636366", "Tertiary text"),
            # Accent - Apple Blue
            ColorToken("accent", "#007AFF", "#0A84FF", "Primary accent (Apple Blue)"),
            ColorToken("accent-hover", "#0056CC", "#409CFF", "Accent hover state"),
            ColorToken("accent-light", "#E5F1FF", "#1C3A5F", "Light accent background"),
            # Semantic colors
            ColorToken("success", "#34C759", "#30D158", "Success state"),
            ColorToken("warning", "#FF9500", "#FF9F0A", "Warning state"),
            ColorToken("error", "#FF3B30", "#FF453A", "Error state"),
            ColorToken("info", "#5856D6", "#5E5CE6", "Info state"),
            # Borders
            ColorToken("border", "#D2D2D7", "#38383A", "Default border"),
            ColorToken("border-light", "#E5E5EA", "#48484A", "Light border"),
            # Overlays
            ColorToken(
                "overlay", "rgba(0,0,0,0.4)", "rgba(0,0,0,0.6)", "Modal overlay"
            ),
        ],
        # Typography - SF Pro inspired
        typography=[
            TypographyToken(
                "display",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
                "3.5rem",
                700,
                1.1,
                "-0.02em",
            ),
            TypographyToken(
                "headline",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
                "2.5rem",
                600,
                1.2,
                "-0.015em",
            ),
            TypographyToken(
                "title-1",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
                "1.75rem",
                600,
                1.3,
                "-0.01em",
            ),
            TypographyToken(
                "title-2",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif",
                "1.375rem",
                600,
                1.35,
                "-0.005em",
            ),
            TypographyToken(
                "title-3",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "1.125rem",
                600,
                1.4,
                "0",
            ),
            TypographyToken(
                "body",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "1rem",
                400,
                1.5,
                "0",
            ),
            TypographyToken(
                "body-bold",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "1rem",
                600,
                1.5,
                "0",
            ),
            TypographyToken(
                "callout",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "0.9375rem",
                400,
                1.45,
                "0",
            ),
            TypographyToken(
                "caption",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "0.8125rem",
                400,
                1.4,
                "0",
            ),
            TypographyToken(
                "footnote",
                "-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
                "0.75rem",
                400,
                1.35,
                "0",
            ),
        ],
        # Spacing - 4px base grid
        spacing=[
            SpacingToken("0", "0", 0),
            SpacingToken("1", "0.25rem", 4),
            SpacingToken("2", "0.5rem", 8),
            SpacingToken("3", "0.75rem", 12),
            SpacingToken("4", "1rem", 16),
            SpacingToken("5", "1.25rem", 20),
            SpacingToken("6", "1.5rem", 24),
            SpacingToken("8", "2rem", 32),
            SpacingToken("10", "2.5rem", 40),
            SpacingToken("12", "3rem", 48),
            SpacingToken("16", "4rem", 64),
            SpacingToken("20", "5rem", 80),
            SpacingToken("24", "6rem", 96),
        ],
        # Shadows - Subtle, Apple-like
        shadows=[
            ShadowToken("none", "none"),
            ShadowToken("xs", "0 1px 2px rgba(0,0,0,0.04)"),
            ShadowToken("sm", "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)"),
            ShadowToken("md", "0 4px 6px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.03)"),
            ShadowToken(
                "lg", "0 10px 15px rgba(0,0,0,0.05), 0 4px 6px rgba(0,0,0,0.03)"
            ),
            ShadowToken(
                "xl", "0 20px 25px rgba(0,0,0,0.06), 0 10px 10px rgba(0,0,0,0.03)"
            ),
            ShadowToken("2xl", "0 25px 50px rgba(0,0,0,0.12)"),
            ShadowToken(
                "card", "0 2px 8px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.04)"
            ),
            ShadowToken(
                "modal", "0 20px 40px rgba(0,0,0,0.15), 0 10px 20px rgba(0,0,0,0.1)"
            ),
        ],
        # Border radii - Generous, friendly
        radii=[
            RadiusToken("none", "0"),
            RadiusToken("sm", "0.375rem"),
            RadiusToken("md", "0.5rem"),
            RadiusToken("lg", "0.75rem"),
            RadiusToken("xl", "1rem"),
            RadiusToken("2xl", "1.25rem"),
            RadiusToken("3xl", "1.5rem"),
            RadiusToken("full", "9999px"),
        ],
        # Animations - Calm, purposeful
        animations=[
            AnimationToken(
                "fade-in",
                "0.4s",
                "ease-out",
                {"0%": {"opacity": "0"}, "100%": {"opacity": "1"}},
            ),
            AnimationToken(
                "fade-out",
                "0.3s",
                "ease-in",
                {"0%": {"opacity": "1"}, "100%": {"opacity": "0"}},
            ),
            AnimationToken(
                "slide-up",
                "0.5s",
                "cubic-bezier(0.16, 1, 0.3, 1)",
                {
                    "0%": {"opacity": "0", "transform": "translateY(16px)"},
                    "100%": {"opacity": "1", "transform": "translateY(0)"},
                },
            ),
            AnimationToken(
                "slide-down",
                "0.5s",
                "cubic-bezier(0.16, 1, 0.3, 1)",
                {
                    "0%": {"opacity": "0", "transform": "translateY(-16px)"},
                    "100%": {"opacity": "1", "transform": "translateY(0)"},
                },
            ),
            AnimationToken(
                "scale-in",
                "0.4s",
                "cubic-bezier(0.16, 1, 0.3, 1)",
                {
                    "0%": {"opacity": "0", "transform": "scale(0.95)"},
                    "100%": {"opacity": "1", "transform": "scale(1)"},
                },
            ),
            AnimationToken(
                "bounce",
                "0.6s",
                "cubic-bezier(0.68, -0.55, 0.265, 1.55)",
                {
                    "0%": {"transform": "scale(1)"},
                    "50%": {"transform": "scale(1.05)"},
                    "100%": {"transform": "scale(1)"},
                },
            ),
            AnimationToken(
                "spin",
                "1s",
                "linear",
                {
                    "0%": {"transform": "rotate(0deg)"},
                    "100%": {"transform": "rotate(360deg)"},
                },
            ),
            AnimationToken(
                "pulse",
                "2s",
                "ease-in-out",
                {"0%, 100%": {"opacity": "1"}, "50%": {"opacity": "0.5"}},
            ),
        ],
        # Components
        components=[
            ComponentDefinition(
                id="button",
                name="Button",
                description="Primary action button",
                category="button",
                variants=[
                    ComponentVariant(
                        "primary",
                        {
                            "background": "var(--accent)",
                            "color": "white",
                            "padding": "0.75rem 1.5rem",
                            "borderRadius": "var(--radius-xl)",
                            "fontWeight": "600",
                        },
                    ),
                    ComponentVariant(
                        "secondary",
                        {
                            "background": "var(--background-secondary)",
                            "color": "var(--text-primary)",
                            "padding": "0.75rem 1.5rem",
                            "borderRadius": "var(--radius-xl)",
                            "fontWeight": "600",
                        },
                    ),
                    ComponentVariant(
                        "ghost",
                        {
                            "background": "transparent",
                            "color": "var(--accent)",
                            "padding": "0.75rem 1.5rem",
                            "borderRadius": "var(--radius-xl)",
                            "fontWeight": "600",
                        },
                    ),
                    ComponentVariant(
                        "danger",
                        {
                            "background": "var(--error)",
                            "color": "white",
                            "padding": "0.75rem 1.5rem",
                            "borderRadius": "var(--radius-xl)",
                            "fontWeight": "600",
                        },
                    ),
                ],
                props=[
                    {"name": "size", "type": "sm | md | lg", "default": "md"},
                    {"name": "disabled", "type": "boolean", "default": "false"},
                    {"name": "loading", "type": "boolean", "default": "false"},
                ],
            ),
            ComponentDefinition(
                id="input",
                name="Input",
                description="Text input field",
                category="input",
                variants=[
                    ComponentVariant(
                        "default",
                        {
                            "background": "var(--background-secondary)",
                            "border": "1px solid var(--border)",
                            "padding": "0.875rem 1rem",
                            "borderRadius": "var(--radius-lg)",
                            "fontSize": "1rem",
                        },
                    ),
                    ComponentVariant(
                        "filled",
                        {
                            "background": "var(--background-tertiary)",
                            "border": "none",
                            "padding": "0.875rem 1rem",
                            "borderRadius": "var(--radius-lg)",
                            "fontSize": "1rem",
                        },
                    ),
                ],
                props=[
                    {"name": "placeholder", "type": "string"},
                    {"name": "error", "type": "string"},
                    {"name": "disabled", "type": "boolean"},
                ],
            ),
            ComponentDefinition(
                id="card",
                name="Card",
                description="Content container",
                category="card",
                variants=[
                    ComponentVariant(
                        "default",
                        {
                            "background": "var(--background)",
                            "borderRadius": "var(--radius-2xl)",
                            "padding": "1.5rem",
                            "boxShadow": "var(--shadow-card)",
                        },
                    ),
                    ComponentVariant(
                        "outlined",
                        {
                            "background": "var(--background)",
                            "borderRadius": "var(--radius-2xl)",
                            "padding": "1.5rem",
                            "border": "1px solid var(--border)",
                        },
                    ),
                    ComponentVariant(
                        "elevated",
                        {
                            "background": "var(--background)",
                            "borderRadius": "var(--radius-2xl)",
                            "padding": "1.5rem",
                            "boxShadow": "var(--shadow-lg)",
                        },
                    ),
                ],
                props=[{"name": "hoverable", "type": "boolean", "default": "false"}],
            ),
            ComponentDefinition(
                id="chat-bubble",
                name="Chat Bubble",
                description="Message bubble for chat interface",
                category="feedback",
                variants=[
                    ComponentVariant(
                        "user",
                        {
                            "background": "var(--accent)",
                            "color": "white",
                            "padding": "0.875rem 1.125rem",
                            "borderRadius": "1.25rem 1.25rem 0.25rem 1.25rem",
                            "maxWidth": "80%",
                        },
                    ),
                    ComponentVariant(
                        "assistant",
                        {
                            "background": "var(--background-secondary)",
                            "color": "var(--text-primary)",
                            "padding": "0.875rem 1.125rem",
                            "borderRadius": "1.25rem 1.25rem 1.25rem 0.25rem",
                            "maxWidth": "80%",
                        },
                    ),
                ],
                props=[
                    {"name": "timestamp", "type": "string"},
                    {"name": "status", "type": "sending | sent | delivered | read"},
                ],
            ),
            ComponentDefinition(
                id="metric-card",
                name="Metric Card",
                description="Dashboard metric display",
                category="card",
                variants=[
                    ComponentVariant(
                        "default",
                        {
                            "background": "var(--background)",
                            "borderRadius": "var(--radius-2xl)",
                            "padding": "1.25rem",
                            "boxShadow": "var(--shadow-card)",
                        },
                    ),
                ],
                props=[
                    {"name": "label", "type": "string"},
                    {"name": "value", "type": "string | number"},
                    {"name": "change", "type": "number"},
                    {"name": "trend", "type": "up | down | neutral"},
                ],
            ),
            ComponentDefinition(
                id="list-item",
                name="List Item",
                description="Interactive list item",
                category="navigation",
                variants=[
                    ComponentVariant(
                        "default",
                        {
                            "padding": "0.875rem 1rem",
                            "borderBottom": "1px solid var(--border-light)",
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "0.75rem",
                        },
                    ),
                    ComponentVariant(
                        "card",
                        {
                            "padding": "1rem",
                            "background": "var(--background)",
                            "borderRadius": "var(--radius-xl)",
                            "marginBottom": "0.5rem",
                            "boxShadow": "var(--shadow-xs)",
                        },
                    ),
                ],
                props=[
                    {"name": "icon", "type": "ReactNode"},
                    {"name": "title", "type": "string"},
                    {"name": "subtitle", "type": "string"},
                    {"name": "chevron", "type": "boolean"},
                ],
            ),
            ComponentDefinition(
                id="avatar",
                name="Avatar",
                description="User avatar",
                category="feedback",
                variants=[
                    ComponentVariant(
                        "circle",
                        {"borderRadius": "var(--radius-full)", "objectFit": "cover"},
                    ),
                    ComponentVariant(
                        "rounded",
                        {"borderRadius": "var(--radius-xl)", "objectFit": "cover"},
                    ),
                ],
                props=[
                    {"name": "size", "type": "xs | sm | md | lg | xl"},
                    {"name": "src", "type": "string"},
                    {"name": "fallback", "type": "string"},
                ],
            ),
            ComponentDefinition(
                id="badge",
                name="Badge",
                description="Status badge",
                category="feedback",
                variants=[
                    ComponentVariant(
                        "default",
                        {
                            "padding": "0.25rem 0.625rem",
                            "borderRadius": "var(--radius-full)",
                            "fontSize": "0.75rem",
                            "fontWeight": "500",
                        },
                    ),
                    ComponentVariant(
                        "dot",
                        {
                            "width": "0.5rem",
                            "height": "0.5rem",
                            "borderRadius": "var(--radius-full)",
                        },
                    ),
                ],
                props=[
                    {
                        "name": "color",
                        "type": "success | warning | error | info | neutral",
                    }
                ],
            ),
        ],
    )


# ============================================
# Design System Service
# ============================================


class DesignSystemService:
    """Design System management service."""

    def __init__(self):
        self._systems: dict[str, DesignSystem] = {}
        self._active_system_id: str = "ds_v_system"

        # Initialize V design system
        v_system = create_v_design_system()
        self._systems[v_system.id] = v_system

    def get_system(self, system_id: Optional[str] = None) -> Optional[DesignSystem]:
        """Get design system by ID or active system."""
        sid = system_id or self._active_system_id
        return self._systems.get(sid)

    def get_active_system(self) -> DesignSystem:
        """Get the active design system."""
        return self._systems[self._active_system_id]

    def list_systems(self) -> list[dict]:
        """List all design systems."""
        return [
            {"id": s.id, "name": s.name, "version": s.version}
            for s in self._systems.values()
        ]

    def generate_css_variables(
        self, system_id: Optional[str] = None, mode: ColorMode = ColorMode.LIGHT
    ) -> str:
        """Generate CSS custom properties from design system."""
        system = self.get_system(system_id)
        if not system:
            return ""

        lines = [":root {"]

        # Colors
        for color in system.colors:
            value = color.light if mode == ColorMode.LIGHT else color.dark
            lines.append(f"  --{color.name}: {value};")

        # Typography
        for typo in system.typography:
            lines.append(f"  --font-{typo.name}: {typo.font_family};")
            lines.append(f"  --text-{typo.name}: {typo.font_size};")
            lines.append(f"  --weight-{typo.name}: {typo.font_weight};")
            lines.append(f"  --leading-{typo.name}: {typo.line_height};")

        # Spacing
        for space in system.spacing:
            lines.append(f"  --space-{space.name}: {space.value};")

        # Shadows
        for shadow in system.shadows:
            lines.append(f"  --shadow-{shadow.name}: {shadow.value};")

        # Radii
        for radius in system.radii:
            lines.append(f"  --radius-{radius.name}: {radius.value};")

        lines.append("}")

        # Dark mode
        if mode == ColorMode.SYSTEM:
            lines.append("\n@media (prefers-color-scheme: dark) {")
            lines.append("  :root {")
            for color in system.colors:
                lines.append(f"    --{color.name}: {color.dark};")
            lines.append("  }")
            lines.append("}")

        # Animations
        lines.append("\n/* Animations */")
        for anim in system.animations:
            if anim.keyframes:
                lines.append(f"@keyframes {anim.name} {{")
                for key, props in anim.keyframes.items():
                    props_str = "; ".join(f"{k}: {v}" for k, v in props.items())
                    lines.append(f"  {key} {{ {props_str}; }}")
                lines.append("}")

        return "\n".join(lines)

    def generate_tailwind_config(self, system_id: Optional[str] = None) -> dict:
        """Generate Tailwind CSS config extension."""
        system = self.get_system(system_id)
        if not system:
            return {}

        config = {
            "theme": {
                "extend": {
                    "colors": {},
                    "fontFamily": {},
                    "fontSize": {},
                    "spacing": {},
                    "boxShadow": {},
                    "borderRadius": {},
                    "animation": {},
                    "keyframes": {},
                }
            }
        }

        # Colors
        for color in system.colors:
            config["theme"]["extend"]["colors"][color.name] = f"var(--{color.name})"

        # Typography
        for typo in system.typography:
            typo.name.replace("-", "_")
            config["theme"]["extend"]["fontSize"][typo.name] = [
                typo.font_size,
                {
                    "lineHeight": str(typo.line_height),
                    "fontWeight": str(typo.font_weight),
                },
            ]

        # Spacing
        for space in system.spacing:
            config["theme"]["extend"]["spacing"][space.name] = space.value

        # Shadows
        for shadow in system.shadows:
            config["theme"]["extend"]["boxShadow"][shadow.name] = shadow.value

        # Radii
        for radius in system.radii:
            config["theme"]["extend"]["borderRadius"][radius.name] = radius.value

        # Animations
        for anim in system.animations:
            config["theme"]["extend"]["animation"][
                anim.name
            ] = f"{anim.name} {anim.duration} {anim.easing}"
            if anim.keyframes:
                config["theme"]["extend"]["keyframes"][anim.name] = anim.keyframes

        return config

    def parse_figma_tokens(self, figma_json: dict) -> dict:
        """Parse Figma tokens export and convert to design system format."""
        tokens = {"colors": [], "typography": [], "spacing": []}

        # Parse colors
        if "colors" in figma_json:
            for name, data in figma_json["colors"].items():
                if isinstance(data, dict) and "value" in data:
                    tokens["colors"].append(
                        {
                            "name": self._to_kebab_case(name),
                            "light": data["value"],
                            "dark": data.get("darkValue", data["value"]),
                        }
                    )

        # Parse text styles
        if "textStyles" in figma_json:
            for name, data in figma_json["textStyles"].items():
                if isinstance(data, dict):
                    tokens["typography"].append(
                        {
                            "name": self._to_kebab_case(name),
                            "fontFamily": data.get("fontFamily", "system-ui"),
                            "fontSize": f"{data.get('fontSize', 16)}px",
                            "fontWeight": data.get("fontWeight", 400),
                            "lineHeight": data.get("lineHeight", 1.5),
                        }
                    )

        # Parse spacing
        if "spacing" in figma_json:
            for name, data in figma_json["spacing"].items():
                if isinstance(data, (int, float)):
                    tokens["spacing"].append(
                        {
                            "name": name,
                            "value": f"{data / 16}rem",
                            "px": int(data),
                        }
                    )

        return tokens

    def _to_kebab_case(self, s: str) -> str:
        """Convert string to kebab-case."""
        s = re.sub(r"([A-Z])", r"-\1", s).lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    def export_to_figma_tokens(self, system_id: Optional[str] = None) -> dict:
        """Export design system to Figma tokens format."""
        system = self.get_system(system_id)
        if not system:
            return {}

        return {
            "colors": {
                c.name: {"value": c.light, "type": "color"} for c in system.colors
            },
            "typography": {
                t.name: {
                    "value": {
                        "fontFamily": t.font_family,
                        "fontSize": t.font_size,
                        "fontWeight": t.font_weight,
                        "lineHeight": t.line_height,
                    },
                    "type": "typography",
                }
                for t in system.typography
            },
            "spacing": {
                s.name: {"value": s.px, "type": "spacing"} for s in system.spacing
            },
            "borderRadius": {
                r.name: {"value": r.value, "type": "borderRadius"} for r in system.radii
            },
            "boxShadow": {
                s.name: {"value": s.value, "type": "boxShadow"} for s in system.shadows
            },
        }


# Singleton
_design_system_service: Optional[DesignSystemService] = None


def get_design_system_service() -> DesignSystemService:
    global _design_system_service
    if _design_system_service is None:
        _design_system_service = DesignSystemService()
    return _design_system_service


__all__ = [
    "DesignSystemService",
    "DesignSystem",
    "ColorToken",
    "TypographyToken",
    "SpacingToken",
    "ShadowToken",
    "RadiusToken",
    "AnimationToken",
    "ComponentDefinition",
    "ComponentVariant",
    "ColorMode",
    "get_design_system_service",
    "create_v_design_system",
]
