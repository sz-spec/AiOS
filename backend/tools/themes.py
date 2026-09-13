"""
Theme Customization Service

Features:
- Predefined theme presets (Light, Dark, High Contrast, etc.)
- Custom theme creation and editing
- CSS variable generation
- Theme import/export
- User theme preferences
- Organization-wide themes (branding)
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


class ThemeMode(str, Enum):
    """Theme mode."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class ThemeCategory(str, Enum):
    """Theme category."""

    OFFICIAL = "official"  # Built-in themes
    COMMUNITY = "community"  # Shared by users
    CUSTOM = "custom"  # User's private themes
    ORGANIZATION = "organization"  # Company branding


@dataclass
class ColorPalette:
    """Color palette for a theme."""

    # Primary colors
    primary: str = "#6366f1"  # Indigo
    primary_foreground: str = "#ffffff"
    primary_hover: str = "#4f46e5"
    primary_active: str = "#4338ca"

    # Secondary colors
    secondary: str = "#64748b"  # Slate
    secondary_foreground: str = "#ffffff"
    secondary_hover: str = "#475569"

    # Accent colors
    accent: str = "#f59e0b"  # Amber
    accent_foreground: str = "#1f2937"

    # Background colors
    background: str = "#ffffff"
    background_secondary: str = "#f8fafc"
    background_tertiary: str = "#f1f5f9"

    # Foreground/text colors
    foreground: str = "#1f2937"
    foreground_secondary: str = "#64748b"
    foreground_muted: str = "#94a3b8"

    # Border colors
    border: str = "#e2e8f0"
    border_hover: str = "#cbd5e1"
    border_focus: str = "#6366f1"

    # Status colors
    success: str = "#22c55e"
    success_foreground: str = "#ffffff"
    warning: str = "#f59e0b"
    warning_foreground: str = "#1f2937"
    error: str = "#ef4444"
    error_foreground: str = "#ffffff"
    info: str = "#3b82f6"
    info_foreground: str = "#ffffff"

    # Editor colors
    editor_background: str = "#1e1e1e"
    editor_foreground: str = "#d4d4d4"
    editor_line_number: str = "#858585"
    editor_selection: str = "#264f78"
    editor_cursor: str = "#ffffff"

    # Syntax highlighting
    syntax_keyword: str = "#569cd6"
    syntax_string: str = "#ce9178"
    syntax_number: str = "#b5cea8"
    syntax_comment: str = "#6a9955"
    syntax_function: str = "#dcdcaa"
    syntax_variable: str = "#9cdcfe"
    syntax_type: str = "#4ec9b0"
    syntax_operator: str = "#d4d4d4"

    def to_css_variables(self, prefix: str = "") -> dict[str, str]:
        """Convert to CSS custom properties."""
        variables = {}
        for key, value in asdict(self).items():
            css_key = f"--{prefix}{key.replace('_', '-')}"
            variables[css_key] = value
        return variables


@dataclass
class Typography:
    """Typography settings."""

    # Font families
    font_sans: str = (
        "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    )
    font_mono: str = "'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, monospace"
    font_heading: str = "'Inter', -apple-system, BlinkMacSystemFont, sans-serif"

    # Font sizes (rem)
    font_size_xs: str = "0.75rem"
    font_size_sm: str = "0.875rem"
    font_size_base: str = "1rem"
    font_size_lg: str = "1.125rem"
    font_size_xl: str = "1.25rem"
    font_size_2xl: str = "1.5rem"
    font_size_3xl: str = "1.875rem"
    font_size_4xl: str = "2.25rem"

    # Font weights
    font_weight_normal: str = "400"
    font_weight_medium: str = "500"
    font_weight_semibold: str = "600"
    font_weight_bold: str = "700"

    # Line heights
    line_height_tight: str = "1.25"
    line_height_normal: str = "1.5"
    line_height_relaxed: str = "1.75"

    # Letter spacing
    letter_spacing_tight: str = "-0.025em"
    letter_spacing_normal: str = "0"
    letter_spacing_wide: str = "0.025em"

    def to_css_variables(self, prefix: str = "") -> dict[str, str]:
        """Convert to CSS custom properties."""
        variables = {}
        for key, value in asdict(self).items():
            css_key = f"--{prefix}{key.replace('_', '-')}"
            variables[css_key] = value
        return variables


@dataclass
class Spacing:
    """Spacing scale."""

    spacing_0: str = "0"
    spacing_1: str = "0.25rem"
    spacing_2: str = "0.5rem"
    spacing_3: str = "0.75rem"
    spacing_4: str = "1rem"
    spacing_5: str = "1.25rem"
    spacing_6: str = "1.5rem"
    spacing_8: str = "2rem"
    spacing_10: str = "2.5rem"
    spacing_12: str = "3rem"
    spacing_16: str = "4rem"
    spacing_20: str = "5rem"
    spacing_24: str = "6rem"

    def to_css_variables(self, prefix: str = "") -> dict[str, str]:
        """Convert to CSS custom properties."""
        variables = {}
        for key, value in asdict(self).items():
            css_key = f"--{prefix}{key.replace('_', '-')}"
            variables[css_key] = value
        return variables


@dataclass
class BorderRadius:
    """Border radius scale."""

    radius_none: str = "0"
    radius_sm: str = "0.25rem"
    radius_md: str = "0.375rem"
    radius_lg: str = "0.5rem"
    radius_xl: str = "0.75rem"
    radius_2xl: str = "1rem"
    radius_3xl: str = "1.5rem"
    radius_full: str = "9999px"

    def to_css_variables(self, prefix: str = "") -> dict[str, str]:
        """Convert to CSS custom properties."""
        variables = {}
        for key, value in asdict(self).items():
            css_key = f"--{prefix}{key.replace('_', '-')}"
            variables[css_key] = value
        return variables


@dataclass
class Shadows:
    """Shadow definitions."""

    shadow_none: str = "none"
    shadow_sm: str = "0 1px 2px 0 rgb(0 0 0 / 0.05)"
    shadow_md: str = "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)"
    shadow_lg: str = (
        "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)"
    )
    shadow_xl: str = (
        "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)"
    )
    shadow_2xl: str = "0 25px 50px -12px rgb(0 0 0 / 0.25)"
    shadow_inner: str = "inset 0 2px 4px 0 rgb(0 0 0 / 0.05)"

    # Focus rings
    ring_focus: str = "0 0 0 2px var(--border-focus)"
    ring_error: str = "0 0 0 2px var(--error)"

    def to_css_variables(self, prefix: str = "") -> dict[str, str]:
        """Convert to CSS custom properties."""
        variables = {}
        for key, value in asdict(self).items():
            css_key = f"--{prefix}{key.replace('_', '-')}"
            variables[css_key] = value
        return variables


@dataclass
class Theme:
    """Complete theme definition."""

    id: str = field(default_factory=lambda: f"theme_{uuid4().hex[:12]}")
    name: str = "Custom Theme"
    description: str = ""
    mode: ThemeMode = ThemeMode.LIGHT
    category: ThemeCategory = ThemeCategory.CUSTOM

    # Theme components
    colors: ColorPalette = field(default_factory=ColorPalette)
    typography: Typography = field(default_factory=Typography)
    spacing: Spacing = field(default_factory=Spacing)
    border_radius: BorderRadius = field(default_factory=BorderRadius)
    shadows: Shadows = field(default_factory=Shadows)

    # Metadata
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    organization_id: Optional[str] = None
    is_public: bool = False
    downloads: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Preview image URL
    preview_url: Optional[str] = None

    # Tags for discovery
    tags: list[str] = field(default_factory=list)

    def to_css(self) -> str:
        """Generate CSS with all custom properties."""
        variables = {}
        variables.update(self.colors.to_css_variables())
        variables.update(self.typography.to_css_variables())
        variables.update(self.spacing.to_css_variables())
        variables.update(self.border_radius.to_css_variables())
        variables.update(self.shadows.to_css_variables())

        # Generate CSS
        css_vars = "\n  ".join(f"{k}: {v};" for k, v in variables.items())

        return f"""/* Theme: {self.name} */
:root[data-theme="{self.id}"],
[data-theme="{self.id}"] {{
  {css_vars}
}}
"""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode.value,
            "category": self.category.value,
            "colors": asdict(self.colors),
            "typography": asdict(self.typography),
            "spacing": asdict(self.spacing),
            "border_radius": asdict(self.border_radius),
            "shadows": asdict(self.shadows),
            "author_id": self.author_id,
            "author_name": self.author_name,
            "organization_id": self.organization_id,
            "is_public": self.is_public,
            "downloads": self.downloads,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "preview_url": self.preview_url,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Theme":
        """Create theme from dictionary."""
        return cls(
            id=data.get("id", f"theme_{uuid4().hex[:12]}"),
            name=data.get("name", "Custom Theme"),
            description=data.get("description", ""),
            mode=ThemeMode(data.get("mode", "light")),
            category=ThemeCategory(data.get("category", "custom")),
            colors=ColorPalette(**data.get("colors", {})),
            typography=Typography(**data.get("typography", {})),
            spacing=Spacing(**data.get("spacing", {})),
            border_radius=BorderRadius(**data.get("border_radius", {})),
            shadows=Shadows(**data.get("shadows", {})),
            author_id=data.get("author_id"),
            author_name=data.get("author_name"),
            organization_id=data.get("organization_id"),
            is_public=data.get("is_public", False),
            downloads=data.get("downloads", 0),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.now(timezone.utc)
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if "updated_at" in data
                else datetime.now(timezone.utc)
            ),
            preview_url=data.get("preview_url"),
            tags=data.get("tags", []),
        )


# ============================================
# Preset Themes
# ============================================


def create_light_theme() -> Theme:
    """Create default light theme."""
    return Theme(
        id="theme_light",
        name="Light",
        description="Clean and bright default theme",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=ColorPalette(),
        tags=["light", "default", "clean"],
    )


def create_dark_theme() -> Theme:
    """Create dark theme."""
    colors = ColorPalette(
        primary="#818cf8",
        primary_foreground="#1e1b4b",
        primary_hover="#a5b4fc",
        primary_active="#6366f1",
        secondary="#94a3b8",
        secondary_foreground="#0f172a",
        accent="#fbbf24",
        accent_foreground="#1f2937",
        background="#0f172a",
        background_secondary="#1e293b",
        background_tertiary="#334155",
        foreground="#f1f5f9",
        foreground_secondary="#94a3b8",
        foreground_muted="#64748b",
        border="#334155",
        border_hover="#475569",
        border_focus="#818cf8",
        success="#4ade80",
        warning="#fbbf24",
        error="#f87171",
        info="#60a5fa",
        editor_background="#0d1117",
        editor_foreground="#c9d1d9",
        editor_line_number="#6e7681",
        editor_selection="#264f78",
        syntax_keyword="#ff7b72",
        syntax_string="#a5d6ff",
        syntax_number="#79c0ff",
        syntax_comment="#8b949e",
        syntax_function="#d2a8ff",
        syntax_variable="#ffa657",
        syntax_type="#7ee787",
    )

    return Theme(
        id="theme_dark",
        name="Dark",
        description="Easy on the eyes dark theme",
        mode=ThemeMode.DARK,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["dark", "default", "night"],
    )


def create_high_contrast_theme() -> Theme:
    """Create high contrast theme for accessibility."""
    colors = ColorPalette(
        primary="#0000ff",
        primary_foreground="#ffffff",
        primary_hover="#0000cc",
        primary_active="#000099",
        secondary="#000000",
        secondary_foreground="#ffffff",
        accent="#ffff00",
        accent_foreground="#000000",
        background="#ffffff",
        background_secondary="#ffffff",
        background_tertiary="#f0f0f0",
        foreground="#000000",
        foreground_secondary="#000000",
        foreground_muted="#333333",
        border="#000000",
        border_hover="#000000",
        border_focus="#0000ff",
        success="#008000",
        warning="#ff8c00",
        error="#ff0000",
        info="#0000ff",
        editor_background="#000000",
        editor_foreground="#ffffff",
        editor_line_number="#ffff00",
    )

    return Theme(
        id="theme_high_contrast",
        name="High Contrast",
        description="High contrast theme for better accessibility",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["accessibility", "high-contrast", "a11y"],
    )


def create_ocean_theme() -> Theme:
    """Create ocean blue theme."""
    colors = ColorPalette(
        primary="#0ea5e9",
        primary_foreground="#ffffff",
        primary_hover="#0284c7",
        primary_active="#0369a1",
        secondary="#06b6d4",
        secondary_foreground="#ffffff",
        accent="#14b8a6",
        accent_foreground="#ffffff",
        background="#f0f9ff",
        background_secondary="#e0f2fe",
        background_tertiary="#bae6fd",
        foreground="#0c4a6e",
        foreground_secondary="#0369a1",
        foreground_muted="#0ea5e9",
        border="#7dd3fc",
        border_hover="#38bdf8",
        border_focus="#0ea5e9",
        success="#10b981",
        warning="#f59e0b",
        error="#ef4444",
        info="#0ea5e9",
    )

    return Theme(
        id="theme_ocean",
        name="Ocean",
        description="Calm blue ocean-inspired theme",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["blue", "ocean", "calm", "water"],
    )


def create_forest_theme() -> Theme:
    """Create forest green theme."""
    colors = ColorPalette(
        primary="#22c55e",
        primary_foreground="#ffffff",
        primary_hover="#16a34a",
        primary_active="#15803d",
        secondary="#84cc16",
        secondary_foreground="#1f2937",
        accent="#eab308",
        accent_foreground="#1f2937",
        background="#f0fdf4",
        background_secondary="#dcfce7",
        background_tertiary="#bbf7d0",
        foreground="#14532d",
        foreground_secondary="#166534",
        foreground_muted="#22c55e",
        border="#86efac",
        border_hover="#4ade80",
        border_focus="#22c55e",
        success="#22c55e",
        warning="#eab308",
        error="#dc2626",
        info="#0ea5e9",
    )

    return Theme(
        id="theme_forest",
        name="Forest",
        description="Natural green forest theme",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["green", "forest", "nature", "earth"],
    )


def create_sunset_theme() -> Theme:
    """Create warm sunset theme."""
    colors = ColorPalette(
        primary="#f97316",
        primary_foreground="#ffffff",
        primary_hover="#ea580c",
        primary_active="#c2410c",
        secondary="#f59e0b",
        secondary_foreground="#1f2937",
        accent="#ec4899",
        accent_foreground="#ffffff",
        background="#fffbeb",
        background_secondary="#fef3c7",
        background_tertiary="#fde68a",
        foreground="#78350f",
        foreground_secondary="#92400e",
        foreground_muted="#d97706",
        border="#fcd34d",
        border_hover="#fbbf24",
        border_focus="#f97316",
        success="#22c55e",
        warning="#f59e0b",
        error="#dc2626",
        info="#3b82f6",
    )

    return Theme(
        id="theme_sunset",
        name="Sunset",
        description="Warm orange sunset theme",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["orange", "sunset", "warm", "cozy"],
    )


def create_midnight_theme() -> Theme:
    """Create deep purple midnight theme."""
    colors = ColorPalette(
        primary="#a855f7",
        primary_foreground="#ffffff",
        primary_hover="#9333ea",
        primary_active="#7e22ce",
        secondary="#8b5cf6",
        secondary_foreground="#ffffff",
        accent="#ec4899",
        accent_foreground="#ffffff",
        background="#0f0a1e",
        background_secondary="#1a1333",
        background_tertiary="#2d1f4a",
        foreground="#e9d5ff",
        foreground_secondary="#c4b5fd",
        foreground_muted="#a78bfa",
        border="#3b2667",
        border_hover="#4c3080",
        border_focus="#a855f7",
        success="#4ade80",
        warning="#fbbf24",
        error="#f87171",
        info="#60a5fa",
        editor_background="#0a0612",
        editor_foreground="#e9d5ff",
    )

    return Theme(
        id="theme_midnight",
        name="Midnight",
        description="Deep purple midnight theme",
        mode=ThemeMode.DARK,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["purple", "dark", "midnight", "night"],
    )


def create_rose_theme() -> Theme:
    """Create rose/pink theme."""
    colors = ColorPalette(
        primary="#ec4899",
        primary_foreground="#ffffff",
        primary_hover="#db2777",
        primary_active="#be185d",
        secondary="#f472b6",
        secondary_foreground="#1f2937",
        accent="#a855f7",
        accent_foreground="#ffffff",
        background="#fdf2f8",
        background_secondary="#fce7f3",
        background_tertiary="#fbcfe8",
        foreground="#831843",
        foreground_secondary="#9d174d",
        foreground_muted="#db2777",
        border="#f9a8d4",
        border_hover="#f472b6",
        border_focus="#ec4899",
        success="#22c55e",
        warning="#f59e0b",
        error="#dc2626",
        info="#3b82f6",
    )

    return Theme(
        id="theme_rose",
        name="Rose",
        description="Elegant rose pink theme",
        mode=ThemeMode.LIGHT,
        category=ThemeCategory.OFFICIAL,
        colors=colors,
        tags=["pink", "rose", "elegant", "feminine"],
    )


PRESET_THEMES = [
    create_light_theme(),
    create_dark_theme(),
    create_high_contrast_theme(),
    create_ocean_theme(),
    create_forest_theme(),
    create_sunset_theme(),
    create_midnight_theme(),
    create_rose_theme(),
]


# ============================================
# Theme Service
# ============================================


class ThemeService:
    """Theme management service."""

    def __init__(self):
        # In-memory storage (replace with database in production)
        self._themes: dict[str, Theme] = {}
        self._user_preferences: dict[str, str] = {}  # user_id -> theme_id

        # Load preset themes
        for theme in PRESET_THEMES:
            self._themes[theme.id] = theme

    # ==========================================
    # Theme CRUD
    # ==========================================

    def get_theme(self, theme_id: str) -> Optional[Theme]:
        """Get theme by ID."""
        return self._themes.get(theme_id)

    def list_themes(
        self,
        category: Optional[ThemeCategory] = None,
        mode: Optional[ThemeMode] = None,
        search: Optional[str] = None,
        tags: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> list[Theme]:
        """List themes with filters."""
        themes = list(self._themes.values())

        if category:
            themes = [t for t in themes if t.category == category]

        if mode:
            themes = [t for t in themes if t.mode == mode]

        if search:
            search_lower = search.lower()
            themes = [
                t
                for t in themes
                if search_lower in t.name.lower()
                or search_lower in t.description.lower()
            ]

        if tags:
            themes = [t for t in themes if any(tag in t.tags for tag in tags)]

        # Filter by visibility
        themes = [
            t
            for t in themes
            if t.category == ThemeCategory.OFFICIAL
            or t.is_public
            or t.author_id == user_id
            or t.organization_id == organization_id
        ]

        return sorted(
            themes,
            key=lambda t: (t.category != ThemeCategory.OFFICIAL, -t.downloads, t.name),
        )

    def create_theme(
        self,
        name: str,
        colors: Optional[dict] = None,
        typography: Optional[dict] = None,
        base_theme_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        organization_id: Optional[str] = None,
        description: str = "",
        mode: ThemeMode = ThemeMode.LIGHT,
        is_public: bool = False,
        tags: Optional[list[str]] = None,
    ) -> Theme:
        """Create a new custom theme."""
        # Start from base theme or defaults
        if base_theme_id and base_theme_id in self._themes:
            base = self._themes[base_theme_id]
            theme_colors = ColorPalette(**{**asdict(base.colors), **(colors or {})})
            theme_typography = Typography(
                **{**asdict(base.typography), **(typography or {})}
            )
        else:
            theme_colors = ColorPalette(**(colors or {}))
            theme_typography = Typography(**(typography or {}))

        theme = Theme(
            name=name,
            description=description,
            mode=mode,
            category=(
                ThemeCategory.ORGANIZATION if organization_id else ThemeCategory.CUSTOM
            ),
            colors=theme_colors,
            typography=theme_typography,
            author_id=user_id,
            author_name=user_name,
            organization_id=organization_id,
            is_public=is_public,
            tags=tags or [],
        )

        self._themes[theme.id] = theme
        return theme

    def update_theme(
        self,
        theme_id: str,
        user_id: str,
        updates: dict,
    ) -> Optional[Theme]:
        """Update a theme."""
        theme = self._themes.get(theme_id)
        if not theme:
            return None

        # Check ownership
        if theme.category == ThemeCategory.OFFICIAL:
            return None  # Can't edit official themes
        if theme.author_id != user_id:
            return None  # Can't edit others' themes

        # Apply updates
        if "name" in updates:
            theme.name = updates["name"]
        if "description" in updates:
            theme.description = updates["description"]
        if "colors" in updates:
            theme.colors = ColorPalette(**{**asdict(theme.colors), **updates["colors"]})
        if "typography" in updates:
            theme.typography = Typography(
                **{**asdict(theme.typography), **updates["typography"]}
            )
        if "is_public" in updates:
            theme.is_public = updates["is_public"]
        if "tags" in updates:
            theme.tags = updates["tags"]

        theme.updated_at = datetime.now(timezone.utc)
        return theme

    def delete_theme(self, theme_id: str, user_id: str) -> bool:
        """Delete a theme."""
        theme = self._themes.get(theme_id)
        if not theme:
            return False

        # Check ownership
        if theme.category == ThemeCategory.OFFICIAL:
            return False
        if theme.author_id != user_id:
            return False

        del self._themes[theme_id]
        return True

    def duplicate_theme(
        self,
        theme_id: str,
        user_id: str,
        user_name: str,
        new_name: Optional[str] = None,
    ) -> Optional[Theme]:
        """Duplicate an existing theme."""
        original = self._themes.get(theme_id)
        if not original:
            return None

        theme = Theme(
            name=new_name or f"{original.name} (Copy)",
            description=original.description,
            mode=original.mode,
            category=ThemeCategory.CUSTOM,
            colors=ColorPalette(**asdict(original.colors)),
            typography=Typography(**asdict(original.typography)),
            spacing=Spacing(**asdict(original.spacing)),
            border_radius=BorderRadius(**asdict(original.border_radius)),
            shadows=Shadows(**asdict(original.shadows)),
            author_id=user_id,
            author_name=user_name,
            tags=original.tags.copy(),
        )

        self._themes[theme.id] = theme

        # Increment downloads for original
        original.downloads += 1

        return theme

    # ==========================================
    # User Preferences
    # ==========================================

    def get_user_theme(self, user_id: str) -> Theme:
        """Get user's active theme."""
        theme_id = self._user_preferences.get(user_id, "theme_light")
        return self._themes.get(theme_id, self._themes["theme_light"])

    def set_user_theme(self, user_id: str, theme_id: str) -> bool:
        """Set user's active theme."""
        if theme_id not in self._themes:
            return False
        self._user_preferences[user_id] = theme_id
        return True

    # ==========================================
    # Export/Import
    # ==========================================

    def export_theme(self, theme_id: str) -> Optional[str]:
        """Export theme as JSON."""
        theme = self._themes.get(theme_id)
        if not theme:
            return None
        return json.dumps(theme.to_dict(), indent=2)

    def import_theme(
        self,
        json_data: str,
        user_id: str,
        user_name: str,
    ) -> Optional[Theme]:
        """Import theme from JSON."""
        try:
            data = json.loads(json_data)

            # Create new theme with imported data
            theme = Theme.from_dict(data)

            # Override ownership
            theme.id = f"theme_{uuid4().hex[:12]}"
            theme.author_id = user_id
            theme.author_name = user_name
            theme.category = ThemeCategory.CUSTOM
            theme.downloads = 0
            theme.created_at = datetime.now(timezone.utc)
            theme.updated_at = datetime.now(timezone.utc)

            self._themes[theme.id] = theme
            return theme
        except Exception:
            return None

    # ==========================================
    # CSS Generation
    # ==========================================

    def generate_css(self, theme_ids: Optional[list[str]] = None) -> str:
        """Generate CSS for themes."""
        if theme_ids:
            themes = [self._themes[tid] for tid in theme_ids if tid in self._themes]
        else:
            themes = list(self._themes.values())

        return "\n\n".join(theme.to_css() for theme in themes)

    def generate_user_css(self, user_id: str) -> str:
        """Generate CSS for user's active theme."""
        theme = self.get_user_theme(user_id)
        return theme.to_css()


# ============================================
# Singleton instance
# ============================================

_theme_service: Optional[ThemeService] = None


def get_theme_service() -> ThemeService:
    """Get theme service singleton."""
    global _theme_service
    if _theme_service is None:
        _theme_service = ThemeService()
    return _theme_service


__all__ = [
    "Theme",
    "ThemeMode",
    "ThemeCategory",
    "ColorPalette",
    "Typography",
    "Spacing",
    "BorderRadius",
    "Shadows",
    "ThemeService",
    "get_theme_service",
    "PRESET_THEMES",
]
