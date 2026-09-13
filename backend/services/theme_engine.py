"""
Theme Engine — Phase 3.5: Creative Power (IE-6)
================================================

Converts VisionAnalysis (ColorPalette + Typography) or style descriptors
into consistent ThemeConfig: tailwind.config.js, globals.css, component classes.

Usage:
    from services.theme_engine import ThemeEngine
    engine = ThemeEngine()
    theme = engine.from_style_descriptor("apple-like")
    # or: theme = engine.from_analysis(vision_analysis)
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict

from ai.agents.vision_agent import ColorPalette, Typography, VisionAnalysis

# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class ThemeConfig:
    """Complete theme configuration for code generation."""

    tailwind_extend: Dict[str, Any] = field(default_factory=dict)
    css_variables: Dict[str, str] = field(default_factory=dict)
    component_classes: Dict[str, str] = field(default_factory=dict)
    globals_css: str = ""
    tailwind_config_js: str = ""
    dark_mode: bool = False
    style_name: str = "minimal"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ThemeConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Style Aliases
# =============================================================================

STYLE_ALIASES: Dict[str, str] = {
    "apple": "apple-like",
    "ios": "apple-like",
    "macos": "apple-like",
    "cyber": "cyberpunk",
    "neon": "cyberpunk",
    "sci-fi": "cyberpunk",
    "clean": "minimal",
    "simple": "minimal",
    "modern": "minimal",
    "business": "corporate",
    "enterprise": "corporate",
    "professional": "corporate",
    "glass": "glassmorphism",
    "frosted": "glassmorphism",
    "blur": "glassmorphism",
    "brutal": "brutalist",
    "raw": "brutalist",
    "google": "material-design",
    "material": "material-design",
    "md": "material-design",
    "dark": "dark-mode",
    "night": "dark-mode",
}


# =============================================================================
# Style Presets
# =============================================================================

STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "apple-like": {
        "palette": ColorPalette(
            primary="#007AFF",
            secondary="#5856D6",
            accent="#FF9500",
            background="#FFFFFF",
            surface="#F2F2F7",
            text_primary="#000000",
            text_secondary="#8E8E93",
            border="#C6C6C8",
            error="#FF3B30",
            success="#34C759",
        ),
        "typography": Typography(
            heading_font="SF Pro Display, system-ui, sans-serif",
            body_font="SF Pro Text, system-ui, sans-serif",
            heading_weight="semibold",
            base_size_px=17,
            scale_ratio=1.25,
            line_height=1.47,
        ),
        "component_classes": {
            "button_primary": "bg-primary text-white rounded-xl px-6 py-3 font-semibold shadow-sm hover:bg-primary/90 transition-colors",
            "button_secondary": "bg-surface text-primary rounded-xl px-6 py-3 font-semibold hover:bg-surface/80 transition-colors",
            "card": "bg-white rounded-2xl shadow-sm border border-border p-6",
            "input": "bg-surface border border-border rounded-xl px-4 py-3 text-base focus:ring-2 focus:ring-primary/30 focus:border-primary outline-none",
            "nav": "bg-white/80 backdrop-blur-xl border-b border-border sticky top-0 z-50",
            "heading": "font-heading font-semibold text-text-primary",
            "badge": "bg-primary/10 text-primary rounded-full px-3 py-1 text-sm font-medium",
            "avatar": "rounded-full bg-surface border-2 border-border",
            "table_row": "border-b border-border hover:bg-surface/50 transition-colors",
            "modal": "bg-white rounded-2xl shadow-xl p-8 max-w-lg mx-auto",
        },
    },
    "cyberpunk": {
        "palette": ColorPalette(
            primary="#00F0FF",
            secondary="#FF00FF",
            accent="#FFE600",
            background="#0A0A0F",
            surface="#1A1A2E",
            text_primary="#E0E0FF",
            text_secondary="#8888AA",
            border="#2A2A4E",
            error="#FF3366",
            success="#00FF88",
        ),
        "typography": Typography(
            heading_font="Orbitron, monospace",
            body_font="Rajdhani, sans-serif",
            heading_weight="bold",
            base_size_px=16,
            scale_ratio=1.333,
            line_height=1.6,
        ),
        "component_classes": {
            "button_primary": "bg-primary/20 text-primary border border-primary rounded px-6 py-3 font-bold uppercase tracking-wider hover:bg-primary/30 hover:shadow-[0_0_20px_rgba(0,240,255,0.3)] transition-all",
            "button_secondary": "bg-surface text-secondary border border-secondary rounded px-6 py-3 font-bold uppercase tracking-wider hover:text-primary transition-colors",
            "card": "bg-surface/80 border border-border rounded-lg p-6 backdrop-blur-sm shadow-[0_0_15px_rgba(0,240,255,0.1)]",
            "input": "bg-surface border border-border rounded px-4 py-3 text-text-primary focus:border-primary focus:shadow-[0_0_10px_rgba(0,240,255,0.2)] outline-none",
            "nav": "bg-background/90 backdrop-blur border-b border-border sticky top-0 z-50",
            "heading": "font-heading font-bold text-primary uppercase tracking-wider",
            "badge": "bg-primary/20 text-primary border border-primary/50 rounded px-3 py-1 text-sm font-bold uppercase",
            "avatar": "rounded border-2 border-primary/50 shadow-[0_0_10px_rgba(0,240,255,0.3)]",
            "table_row": "border-b border-border hover:bg-primary/5 transition-colors",
            "modal": "bg-surface border border-primary/30 rounded-lg shadow-[0_0_30px_rgba(0,240,255,0.2)] p-8 max-w-lg mx-auto",
        },
    },
    "minimal": {
        "palette": ColorPalette(
            primary="#111827",
            secondary="#6B7280",
            accent="#3B82F6",
            background="#FFFFFF",
            surface="#F9FAFB",
            text_primary="#111827",
            text_secondary="#6B7280",
            border="#E5E7EB",
            error="#EF4444",
            success="#10B981",
        ),
        "typography": Typography(
            heading_font="Inter, system-ui, sans-serif",
            body_font="Inter, system-ui, sans-serif",
            heading_weight="semibold",
            base_size_px=16,
            scale_ratio=1.25,
            line_height=1.5,
        ),
        "component_classes": {
            "button_primary": "bg-primary text-white rounded-lg px-5 py-2.5 font-medium hover:bg-primary/90 transition-colors",
            "button_secondary": "bg-white text-primary border border-border rounded-lg px-5 py-2.5 font-medium hover:bg-surface transition-colors",
            "card": "bg-white rounded-lg border border-border p-6",
            "input": "border border-border rounded-lg px-4 py-2.5 text-base focus:ring-2 focus:ring-accent/20 focus:border-accent outline-none",
            "nav": "bg-white border-b border-border sticky top-0 z-50",
            "heading": "font-heading font-semibold text-text-primary",
            "badge": "bg-surface text-secondary rounded-full px-3 py-1 text-sm font-medium",
            "avatar": "rounded-full bg-surface border border-border",
            "table_row": "border-b border-border hover:bg-surface transition-colors",
            "modal": "bg-white rounded-xl shadow-lg border border-border p-6 max-w-lg mx-auto",
        },
    },
    "corporate": {
        "palette": ColorPalette(
            primary="#1E40AF",
            secondary="#047857",
            accent="#D97706",
            background="#FFFFFF",
            surface="#F8FAFC",
            text_primary="#0F172A",
            text_secondary="#64748B",
            border="#CBD5E1",
            error="#DC2626",
            success="#059669",
        ),
        "typography": Typography(
            heading_font="Plus Jakarta Sans, system-ui, sans-serif",
            body_font="Inter, system-ui, sans-serif",
            heading_weight="bold",
            base_size_px=16,
            scale_ratio=1.25,
            line_height=1.6,
        ),
        "component_classes": {
            "button_primary": "bg-primary text-white rounded-md px-5 py-2.5 font-semibold shadow-sm hover:bg-primary/90 transition-colors",
            "button_secondary": "bg-white text-primary border border-primary rounded-md px-5 py-2.5 font-semibold hover:bg-primary/5 transition-colors",
            "card": "bg-white rounded-lg shadow-sm border border-border p-6",
            "input": "border border-border rounded-md px-4 py-2.5 text-base focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none",
            "nav": "bg-white shadow-sm border-b border-border sticky top-0 z-50",
            "heading": "font-heading font-bold text-text-primary",
            "badge": "bg-primary/10 text-primary rounded-md px-3 py-1 text-sm font-semibold",
            "avatar": "rounded-full bg-surface border-2 border-border",
            "table_row": "border-b border-border hover:bg-surface transition-colors",
            "modal": "bg-white rounded-lg shadow-xl p-8 max-w-lg mx-auto",
        },
    },
    "glassmorphism": {
        "palette": ColorPalette(
            primary="#8B5CF6",
            secondary="#EC4899",
            accent="#06B6D4",
            background="#0F172A",
            surface="rgba(255,255,255,0.1)",
            text_primary="#F1F5F9",
            text_secondary="#94A3B8",
            border="rgba(255,255,255,0.15)",
            error="#F43F5E",
            success="#34D399",
        ),
        "typography": Typography(
            heading_font="Inter, system-ui, sans-serif",
            body_font="Inter, system-ui, sans-serif",
            heading_weight="semibold",
            base_size_px=16,
            scale_ratio=1.25,
            line_height=1.5,
        ),
        "component_classes": {
            "button_primary": "bg-primary/80 text-white rounded-xl px-6 py-3 font-semibold backdrop-blur-md hover:bg-primary/90 transition-all",
            "button_secondary": "bg-white/10 text-text-primary border border-white/20 rounded-xl px-6 py-3 font-semibold backdrop-blur-md hover:bg-white/20 transition-all",
            "card": "bg-white/10 backdrop-blur-xl border border-white/20 rounded-2xl p-6 shadow-xl",
            "input": "bg-white/10 backdrop-blur border border-white/20 rounded-xl px-4 py-3 text-text-primary focus:border-primary outline-none",
            "nav": "bg-white/5 backdrop-blur-xl border-b border-white/10 sticky top-0 z-50",
            "heading": "font-heading font-semibold text-text-primary",
            "badge": "bg-primary/20 text-primary rounded-full px-3 py-1 text-sm font-medium backdrop-blur",
            "avatar": "rounded-full border-2 border-white/20 backdrop-blur",
            "table_row": "border-b border-white/10 hover:bg-white/5 transition-colors",
            "modal": "bg-white/10 backdrop-blur-2xl border border-white/20 rounded-2xl shadow-2xl p-8 max-w-lg mx-auto",
        },
    },
    "dark-mode": {
        "palette": ColorPalette(
            primary="#3B82F6",
            secondary="#8B5CF6",
            accent="#F59E0B",
            background="#111827",
            surface="#1F2937",
            text_primary="#F9FAFB",
            text_secondary="#9CA3AF",
            border="#374151",
            error="#EF4444",
            success="#10B981",
        ),
        "typography": Typography(
            heading_font="Inter, system-ui, sans-serif",
            body_font="Inter, system-ui, sans-serif",
            heading_weight="semibold",
            base_size_px=16,
            scale_ratio=1.25,
            line_height=1.5,
        ),
        "component_classes": {
            "button_primary": "bg-primary text-white rounded-lg px-5 py-2.5 font-medium hover:bg-primary/80 transition-colors",
            "button_secondary": "bg-surface text-text-primary border border-border rounded-lg px-5 py-2.5 font-medium hover:bg-border/30 transition-colors",
            "card": "bg-surface rounded-lg border border-border p-6",
            "input": "bg-surface border border-border rounded-lg px-4 py-2.5 text-text-primary focus:ring-2 focus:ring-primary/30 focus:border-primary outline-none",
            "nav": "bg-background border-b border-border sticky top-0 z-50",
            "heading": "font-heading font-semibold text-text-primary",
            "badge": "bg-primary/20 text-primary rounded-full px-3 py-1 text-sm font-medium",
            "avatar": "rounded-full bg-border border-2 border-surface",
            "table_row": "border-b border-border hover:bg-border/20 transition-colors",
            "modal": "bg-surface border border-border rounded-xl shadow-xl p-6 max-w-lg mx-auto",
        },
    },
    "brutalist": {
        "palette": ColorPalette(
            primary="#000000",
            secondary="#FF0000",
            accent="#FFFF00",
            background="#FFFFFF",
            surface="#F0F0F0",
            text_primary="#000000",
            text_secondary="#333333",
            border="#000000",
            error="#FF0000",
            success="#00FF00",
        ),
        "typography": Typography(
            heading_font="Courier New, monospace",
            body_font="Arial, sans-serif",
            heading_weight="bold",
            base_size_px=18,
            scale_ratio=1.5,
            line_height=1.4,
        ),
        "component_classes": {
            "button_primary": "bg-black text-white border-2 border-black px-6 py-3 font-bold uppercase hover:bg-white hover:text-black transition-colors",
            "button_secondary": "bg-white text-black border-2 border-black px-6 py-3 font-bold uppercase hover:bg-black hover:text-white transition-colors",
            "card": "bg-white border-2 border-black p-6",
            "input": "border-2 border-black px-4 py-3 text-base focus:outline-4 focus:outline-black outline-offset-2",
            "nav": "bg-white border-b-4 border-black sticky top-0 z-50",
            "heading": "font-heading font-bold text-black uppercase",
            "badge": "bg-black text-white px-3 py-1 text-sm font-bold uppercase",
            "avatar": "border-2 border-black",
            "table_row": "border-b-2 border-black hover:bg-yellow-100 transition-colors",
            "modal": "bg-white border-4 border-black p-8 max-w-lg mx-auto shadow-[8px_8px_0_0_#000]",
        },
    },
    "material-design": {
        "palette": ColorPalette(
            primary="#1976D2",
            secondary="#9C27B0",
            accent="#FF5722",
            background="#FAFAFA",
            surface="#FFFFFF",
            text_primary="#212121",
            text_secondary="#757575",
            border="#E0E0E0",
            error="#D32F2F",
            success="#388E3C",
        ),
        "typography": Typography(
            heading_font="Roboto, sans-serif",
            body_font="Roboto, sans-serif",
            heading_weight="bold",
            base_size_px=16,
            scale_ratio=1.25,
            line_height=1.5,
        ),
        "component_classes": {
            "button_primary": "bg-primary text-white rounded px-6 py-2.5 font-medium uppercase text-sm tracking-wider shadow hover:shadow-md hover:bg-primary/90 transition-all",
            "button_secondary": "text-primary bg-transparent rounded px-6 py-2.5 font-medium uppercase text-sm tracking-wider hover:bg-primary/5 transition-colors",
            "card": "bg-white rounded shadow p-6 hover:shadow-md transition-shadow",
            "input": "border-b-2 border-border px-1 py-2 text-base focus:border-primary outline-none transition-colors",
            "nav": "bg-primary text-white shadow-md sticky top-0 z-50",
            "heading": "font-heading font-bold text-text-primary",
            "badge": "bg-secondary text-white rounded-full px-3 py-1 text-xs font-medium uppercase",
            "avatar": "rounded-full bg-primary/10",
            "table_row": "border-b border-border hover:bg-surface transition-colors",
            "modal": "bg-white rounded shadow-xl p-8 max-w-lg mx-auto",
        },
    },
}


# =============================================================================
# ThemeEngine
# =============================================================================


class ThemeEngine:
    """Produces consistent Tailwind config and CSS variables from design analysis."""

    @classmethod
    def from_analysis(cls, analysis: VisionAnalysis) -> ThemeConfig:
        """Build ThemeConfig from VisionAnalysis."""
        if analysis.confidence < 0.5:
            # Low confidence — fall back to closest style preset
            style = cls._resolve_style(analysis.overall_style)
            return cls.from_style_descriptor(style)

        palette = analysis.palette
        typography = analysis.typography
        style = cls._resolve_style(analysis.overall_style)
        dark_mode = style == "dark-mode" or analysis.palette.background.lower() in (
            "#000000",
            "#111827",
            "#0a0a0f",
            "#0f172a",
        )

        preset = STYLE_PRESETS.get(style, STYLE_PRESETS["minimal"])
        component_classes = preset["component_classes"]

        return ThemeConfig(
            tailwind_extend=cls._build_tailwind_extend(palette, typography),
            css_variables=cls._build_css_variables(palette, typography),
            component_classes=component_classes,
            globals_css=cls.generate_globals_css(palette, typography, dark_mode),
            tailwind_config_js=cls.generate_tailwind_config(palette, typography),
            dark_mode=dark_mode,
            style_name=style,
        )

    @classmethod
    def from_style_descriptor(cls, style: str) -> ThemeConfig:
        """Build ThemeConfig from a style name string."""
        resolved = cls._resolve_style(style)
        preset = STYLE_PRESETS.get(resolved, STYLE_PRESETS["minimal"])
        palette = preset["palette"]
        typography = preset["typography"]
        component_classes = preset["component_classes"]
        dark_mode = resolved in ("dark-mode", "cyberpunk", "glassmorphism")

        return ThemeConfig(
            tailwind_extend=cls._build_tailwind_extend(palette, typography),
            css_variables=cls._build_css_variables(palette, typography),
            component_classes=component_classes,
            globals_css=cls.generate_globals_css(palette, typography, dark_mode),
            tailwind_config_js=cls.generate_tailwind_config(palette, typography),
            dark_mode=dark_mode,
            style_name=resolved,
        )

    @classmethod
    def generate_tailwind_config(
        cls, palette: ColorPalette, typography: Typography
    ) -> str:
        """Generate a complete tailwind.config.js file."""
        return f"""/** @type {{import('tailwindcss').Config}} */
module.exports = {{
  content: ["./app/**/*.{{js,ts,jsx,tsx}}", "./components/**/*.{{js,ts,jsx,tsx}}"],
  theme: {{
    extend: {{
      colors: {{
        primary: "var(--color-primary)",
        secondary: "var(--color-secondary)",
        accent: "var(--color-accent)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        error: "var(--color-error)",
        success: "var(--color-success)",
        "text-primary": "var(--color-text-primary)",
        "text-secondary": "var(--color-text-secondary)",
      }},
      fontFamily: {{
        heading: ["{typography.heading_font.split(',')[0].strip()}", "system-ui", "sans-serif"],
        body: ["{typography.body_font.split(',')[0].strip()}", "system-ui", "sans-serif"],
      }},
    }},
  }},
  plugins: [],
}};"""

    @classmethod
    def generate_globals_css(
        cls, palette: ColorPalette, typography: Typography, dark_mode: bool
    ) -> str:
        """Generate globals.css with CSS variables and base styles."""
        css = f"""@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --color-primary: {palette.primary};
  --color-secondary: {palette.secondary};
  --color-accent: {palette.accent};
  --color-background: {palette.background};
  --color-surface: {palette.surface};
  --color-text-primary: {palette.text_primary};
  --color-text-secondary: {palette.text_secondary};
  --color-border: {palette.border};
  --color-error: {palette.error};
  --color-success: {palette.success};
  --font-heading: '{typography.heading_font.split(",")[0].strip()}', system-ui, sans-serif;
  --font-body: '{typography.body_font.split(",")[0].strip()}', system-ui, sans-serif;
}}

html {{
  font-family: var(--font-body);
  font-size: {typography.base_size_px}px;
  line-height: {typography.line_height};
  color: var(--color-text-primary);
  background-color: var(--color-background);
}}"""

        if dark_mode:
            css += """

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
  }
}"""

        return css

    @classmethod
    def generate_component_classes(
        cls, style: str, palette: ColorPalette
    ) -> Dict[str, str]:
        """Return Tailwind class strings for common component types."""
        resolved = cls._resolve_style(style)
        preset = STYLE_PRESETS.get(resolved, STYLE_PRESETS["minimal"])
        return preset["component_classes"]

    @classmethod
    def _resolve_style(cls, style: str) -> str:
        """Fuzzy-match a style descriptor to a STYLE_PRESETS key."""
        if not style:
            return "minimal"
        normalized = style.lower().strip().replace("_", "-")
        if normalized in STYLE_PRESETS:
            return normalized
        if normalized in STYLE_ALIASES:
            return STYLE_ALIASES[normalized]
        return "minimal"

    @classmethod
    def _build_tailwind_extend(
        cls, palette: ColorPalette, typography: Typography
    ) -> Dict[str, Any]:
        return {
            "colors": {
                "primary": palette.primary,
                "secondary": palette.secondary,
                "accent": palette.accent,
                "surface": palette.surface,
                "border": palette.border,
                "error": palette.error,
                "success": palette.success,
            },
            "fontFamily": {
                "heading": [typography.heading_font],
                "body": [typography.body_font],
            },
        }

    @classmethod
    def _build_css_variables(
        cls, palette: ColorPalette, typography: Typography
    ) -> Dict[str, str]:
        return {
            "--color-primary": palette.primary,
            "--color-secondary": palette.secondary,
            "--color-accent": palette.accent,
            "--color-background": palette.background,
            "--color-surface": palette.surface,
            "--color-text-primary": palette.text_primary,
            "--color-text-secondary": palette.text_secondary,
            "--color-border": palette.border,
            "--color-error": palette.error,
            "--color-success": palette.success,
            "--font-heading": typography.heading_font,
            "--font-body": typography.body_font,
        }
