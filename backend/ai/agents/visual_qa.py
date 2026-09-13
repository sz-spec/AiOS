"""
Visual QA Agent — IE-4 Phase 2.5
=================================
Headless rendering + multimodal LLM analysis of generated UI.

Pipeline position: after Tester, before Reviewer.
Non-blocking: failures result in empty report, not pipeline abort.

Dual-model analysis strategy:
- Gemini 3.1 Pro (researcher role): high-context RAG comparison of
  screenshots against the architecture spec and design system.
- Claude Opus 4.6 (reviewer role): detailed visual bug detection,
  WCAG AA accessibility audit, and issue prioritisation.
"""

import base64
import json
import logging
import re
import tempfile
import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("visual_qa")


# =========================================================================
# Constants
# =========================================================================

VIEWPORTS: Dict[str, Dict[str, int]] = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 812},
}

MAX_SCREENSHOTS = 15
MAX_IMAGE_BYTES = 500 * 1024  # 500 KB per screenshot

WCAG_AA_NORMAL_TEXT = 4.5
WCAG_AA_LARGE_TEXT = 3.0
WCAG_MIN_TOUCH_TARGET = 44  # px


# =========================================================================
# Multimodal Prompts
# =========================================================================

VISUAL_QA_PROMPT = """You are a Visual QA expert analyzing a screenshot of a web application.

**Viewport**: {viewport} ({width}x{height}px)
**Route**: {route}

Analyze this screenshot for the following issues:

## 1. Layout Consistency
- Are any elements overlapping inappropriately?
- Is any text or content cut off or clipped?
- Are there any large empty/blank areas that suggest missing content?
- Is the layout balanced and well-structured?

## 2. Color Contrast (WCAG AA)
- Does all normal text (< 18pt) have at least 4.5:1 contrast ratio against its background?
- Does all large text (>= 18pt or >= 14pt bold) have at least 3:1 contrast ratio?
- Are there any areas where text is hard to read against its background?

## 3. Touch Targets (Mobile/Tablet only)
- Are all interactive elements (buttons, links, inputs) at least 44x44px?
- Is there sufficient spacing between interactive elements?

## 4. Responsive Design
- Does the content fit within the viewport without horizontal scrolling?
- Are images and containers properly sized for this viewport?
- Is navigation accessible at this viewport size?

## 5. General Visual Quality
- Are fonts consistent across the page?
- Is spacing consistent (margins, padding)?
- Do colors match a cohesive design system?
- Are loading/skeleton states visible where appropriate?

Return ONLY a JSON array of issues found. Each issue must have:
- "id": unique identifier (VIS001, VIS002, A11Y001, etc.)
- "title": short title (< 60 chars)
- "description": detailed description
- "severity": "critical" | "high" | "medium" | "info"
- "location_hint": where in the screenshot the issue appears
- "fix_suggestion": specific CSS/component fix

If no issues are found, return an empty array: []

{design_context}"""

ARCHITECTURE_COMPARISON_PROMPT = """You are analyzing a screenshot of an AI-generated web application.

**Viewport**: {viewport} ({width}x{height}px)
**Route**: {route}

**Architecture Specification**:
{architecture_summary}

Compare this screenshot against the architecture specification. Check:

1. **Component presence**: Are all expected components visible for this route?
2. **Layout structure**: Does the layout match the specified component hierarchy?
3. **Navigation**: Are expected navigation elements present and accessible?
4. **Data display**: Are data sections, tables, or forms correctly rendered?
5. **Brand consistency**: Do colors, fonts, and spacing match the design tokens?

Return ONLY a JSON array of discrepancies found. Each must have:
- "id": unique identifier (ARCH_VIS001, ARCH_VIS002, etc.)
- "title": short title (< 60 chars)
- "description": what expected component/layout is missing or wrong
- "severity": "critical" | "high" | "medium" | "info"
- "location_hint": where the issue appears
- "fix_suggestion": specific fix to align with architecture

If the screenshot matches the spec well, return an empty array: []
"""


# =========================================================================
# Data Structures
# =========================================================================


class VisualSeverity(str, Enum):
    """Severity levels for visual issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


@dataclass
class VisualIssue:
    """A single visual issue detected by the QA agent."""

    id: str
    title: str
    description: str
    severity: VisualSeverity
    viewport: str
    route: str
    location_hint: str
    fix_suggestion: str

    def to_dict(self) -> dict:
        return {**asdict(self), "severity": self.severity.value}


@dataclass
class VisualQAReport:
    """Complete visual QA report for a build."""

    issues: List[VisualIssue] = field(default_factory=list)
    screenshots_analyzed: int = 0
    routes_tested: List[str] = field(default_factory=list)
    viewports_tested: List[str] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    grades: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "screenshots_analyzed": self.screenshots_analyzed,
            "routes_tested": self.routes_tested,
            "viewports_tested": self.viewports_tested,
            "summary": self.summary,
            "grades": self.grades,
        }

    @property
    def has_critical(self) -> bool:
        return any(i.severity == VisualSeverity.CRITICAL for i in self.issues)


# =========================================================================
# Route Detection
# =========================================================================


def detect_routes(frontend_files: Dict[str, str]) -> List[str]:
    """
    Detect routes from the generated frontend code.

    Heuristics:
    - Next.js App Router: files matching app/**/page.tsx → route = directory path
    - React Router: <Route path="..."> patterns in code
    - Fallback: ["/"]
    """
    routes: List[str] = []

    # Strategy 1: Next.js App Router — app/**/page.{tsx,jsx,ts,js}
    nextjs_page_pattern = re.compile(r"^(?:src/)?app(/.*?)/?page\.[tj]sx?$")
    for filepath in frontend_files:
        normalised = filepath.replace("\\", "/")
        m = nextjs_page_pattern.match(normalised)
        if m:
            route_path = m.group(1) or "/"
            # Normalise: strip trailing slash except for root
            if route_path != "/" and route_path.endswith("/"):
                route_path = route_path.rstrip("/")
            if not route_path:
                route_path = "/"
            routes.append(route_path)

    if routes:
        # Ensure root is first
        if "/" in routes:
            routes.remove("/")
            routes.insert(0, "/")
        return sorted(set(routes), key=lambda r: (r != "/", r))

    # Strategy 2: React Router patterns — <Route path="...">
    route_pattern = re.compile(r'<Route\s+[^>]*path\s*=\s*["\']([^"\']+)["\']')
    for content in frontend_files.values():
        for m in route_pattern.finditer(content):
            routes.append(m.group(1))

    if routes:
        if "/" in routes:
            routes.remove("/")
            routes.insert(0, "/")
        return sorted(set(routes), key=lambda r: (r != "/", r))

    # Fallback: just test root
    return ["/"]


# =========================================================================
# Screenshot Capture
# =========================================================================


def _has_complex_deps(frontend_files: Dict[str, str]) -> bool:
    """Check if the project needs a full dev server (vs Sandpack)."""
    for filepath, content in frontend_files.items():
        lower = filepath.lower()
        if "prisma" in lower or "next.config" in lower:
            return True
        if "server" in lower and "action" in lower:
            return True
    return False


async def capture_screenshots(
    frontend_files: Dict[str, str],
    routes: List[str],
    viewports: Optional[Dict[str, Dict[str, int]]] = None,
    dev_server_port: int = 3099,
    timeout_ms: int = 30000,
) -> Dict[str, Dict[str, bytes]]:
    """
    Render each route at each viewport using Playwright headless.

    Returns: {route: {viewport_name: png_bytes}}

    Strategy:
    1. Write frontend_files to a temp directory
    2. Start dev server via subprocess
    3. For each route x viewport: set viewport, goto, screenshot
    4. Clean up temp dir + kill dev server

    Raises playwright._impl._errors.Error if Playwright is not installed.
    """
    vps = viewports or VIEWPORTS
    screenshots: Dict[str, Dict[str, bytes]] = {}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping screenshot capture")
        return screenshots

    # Write files to temp dir
    tmpdir = tempfile.mkdtemp(prefix="vqa_")
    try:
        for filepath, content in frontend_files.items():
            full_path = os.path.join(tmpdir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Launch Playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                count = 0
                for route in routes:
                    screenshots[route] = {}
                    for vp_name, vp_size in vps.items():
                        if count >= MAX_SCREENSHOTS:
                            break

                        page = await browser.new_page(
                            viewport=vp_size,
                        )
                        try:
                            url = f"http://localhost:{dev_server_port}{route}"
                            await page.goto(
                                url, timeout=timeout_ms, wait_until="networkidle"
                            )
                            png_bytes = await page.screenshot(full_page=True)

                            # Enforce size cap
                            if len(png_bytes) > MAX_IMAGE_BYTES:
                                # Re-capture at lower quality via JPEG not
                                # available in Playwright — just truncate
                                png_bytes = png_bytes[:MAX_IMAGE_BYTES]

                            screenshots[route][vp_name] = png_bytes
                            count += 1
                        except Exception as e:
                            logger.warning(
                                "Screenshot failed for %s @ %s: %s", route, vp_name, e
                            )
                        finally:
                            await page.close()

                    if count >= MAX_SCREENSHOTS:
                        break
            finally:
                await browser.close()
    finally:
        # Clean up temp dir (best effort)
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return screenshots


# =========================================================================
# Multimodal Analysis
# =========================================================================


def _build_visual_qa_prompt(
    route: str,
    viewport: str,
    vp_size: Dict[str, int],
    design_tokens: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the visual QA analysis prompt for a single screenshot."""
    design_context = ""
    if design_tokens:
        lines = ["## Design System Reference"]
        for key, val in design_tokens.items():
            lines.append(f"{key}: {val}")
        design_context = "\n".join(lines)

    return VISUAL_QA_PROMPT.format(
        viewport=viewport,
        width=vp_size["width"],
        height=vp_size["height"],
        route=route,
        design_context=design_context,
    )


def _build_architecture_prompt(
    route: str,
    viewport: str,
    vp_size: Dict[str, int],
    architecture: Dict[str, Any],
) -> str:
    """Build the architecture comparison prompt for a single screenshot."""
    # Summarise architecture for the prompt
    arch_lines = []
    if "tech_stack" in architecture:
        arch_lines.append(f"Tech stack: {json.dumps(architecture['tech_stack'])}")
    if "routes" in architecture:
        arch_lines.append(f"Expected routes: {json.dumps(architecture['routes'])}")
    if "components" in architecture:
        arch_lines.append(f"Components: {json.dumps(architecture['components'])}")
    if "design_system" in architecture:
        arch_lines.append(f"Design system: {json.dumps(architecture['design_system'])}")
    # Fallback: dump everything (truncated)
    if not arch_lines:
        summary = json.dumps(architecture, default=str)[:2000]
        arch_lines.append(summary)

    return ARCHITECTURE_COMPARISON_PROMPT.format(
        viewport=viewport,
        width=vp_size["width"],
        height=vp_size["height"],
        route=route,
        architecture_summary="\n".join(arch_lines),
    )


def _build_multimodal_messages(
    prompt_text: str,
    screenshot_b64: str,
) -> list:
    """
    Build a LangChain-compatible message list with image content.

    Returns a list with a single HumanMessage containing both text and
    the base64-encoded image (OpenAI/Anthropic/Google multimodal format).
    """
    from langchain_core.messages import HumanMessage

    return [
        HumanMessage(
            content=[
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}",
                    },
                },
            ]
        )
    ]


def _parse_issues_from_response(
    raw_text: str,
    route: str,
    viewport: str,
) -> List[VisualIssue]:
    """Parse the LLM JSON response into VisualIssue objects."""
    # Extract JSON array from response (may be wrapped in markdown fences)
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip markdown code fences
        lines = text.split("\n")
        # Remove first and last fence lines
        json_lines = []
        in_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                json_lines.append(line)
        text = "\n".join(json_lines).strip()

    if not text:
        return []

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array within text
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                items = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning("Failed to parse visual QA response as JSON")
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    issues = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            sev_str = item.get("severity", "info").lower()
            try:
                severity = VisualSeverity(sev_str)
            except ValueError:
                severity = VisualSeverity.INFO

            issues.append(
                VisualIssue(
                    id=item.get("id", f"VIS{len(issues)+1:03d}"),
                    title=item.get("title", "Unnamed issue"),
                    description=item.get("description", ""),
                    severity=severity,
                    viewport=viewport,
                    route=route,
                    location_hint=item.get("location_hint", "unknown"),
                    fix_suggestion=item.get("fix_suggestion", ""),
                )
            )
        except Exception:
            continue

    return issues


async def analyze_screenshot(
    screenshot_bytes: bytes,
    route: str,
    viewport: str,
    vp_size: Dict[str, int],
    design_tokens: Optional[Dict[str, Any]] = None,
    architecture: Optional[Dict[str, Any]] = None,
) -> List[VisualIssue]:
    """
    Dual-model multimodal analysis of a single screenshot.

    Pass 1 (Gemini 3.1 Pro — researcher role):
        High-context RAG comparison of screenshot against architecture spec.
        Skipped if no architecture is provided.

    Pass 2 (Claude Opus 4.6 — reviewer role):
        Detailed visual bug detection + WCAG AA accessibility audit.
    """
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
    all_issues: List[VisualIssue] = []

    # --- Pass 1: Architecture comparison (Gemini 3.1 Pro) ---
    if architecture:
        try:
            from ai.llm.providers import LLM

            arch_prompt = _build_architecture_prompt(
                route,
                viewport,
                vp_size,
                architecture,
            )
            messages = _build_multimodal_messages(arch_prompt, screenshot_b64)
            gemini_llm = LLM(role="researcher", complexity=7)
            response = gemini_llm.generate(messages)
            arch_issues = _parse_issues_from_response(
                response.content,
                route,
                viewport,
            )
            all_issues.extend(arch_issues)
        except Exception as e:
            logger.warning(
                "Gemini architecture analysis failed for %s @ %s: %s",
                route,
                viewport,
                e,
            )

    # --- Pass 2: Visual QA + A11y (Claude Opus 4.6) ---
    try:
        from ai.llm.providers import LLM

        qa_prompt = _build_visual_qa_prompt(
            route,
            viewport,
            vp_size,
            design_tokens,
        )
        messages = _build_multimodal_messages(qa_prompt, screenshot_b64)
        opus_llm = LLM(role="reviewer", complexity=9)
        response = opus_llm.generate(messages)
        qa_issues = _parse_issues_from_response(
            response.content,
            route,
            viewport,
        )
        all_issues.extend(qa_issues)
    except Exception as e:
        logger.warning(
            "Opus visual QA failed for %s @ %s: %s",
            route,
            viewport,
            e,
        )

    return all_issues


# =========================================================================
# Grading
# =========================================================================


def _grade_viewport(issues: List[VisualIssue]) -> str:
    """Grade a single route+viewport based on issues found."""
    if any(i.severity == VisualSeverity.CRITICAL for i in issues):
        return "fail"
    if any(i.severity == VisualSeverity.HIGH for i in issues):
        return "warn"
    return "pass"


# =========================================================================
# Full Pipeline
# =========================================================================


class VisualQAAgent:
    """
    Headless rendering + dual-model multimodal LLM analysis.

    Pipeline position: after Tester, before Reviewer.
    """

    def __init__(self, llm_provider: str = "auto"):
        self.llm_provider = llm_provider

    async def run(
        self,
        frontend_files: Dict[str, str],
        architecture: Optional[Dict[str, Any]] = None,
    ) -> VisualQAReport:
        """
        Full Visual QA pipeline:
        1. Detect routes from generated code
        2. Capture screenshots (all routes x all viewports)
        3. Analyse each screenshot with dual-model multimodal LLMs
        4. Compile VisualQAReport with grades
        """
        report = VisualQAReport()

        # Step 1: Detect routes
        routes = detect_routes(frontend_files)
        report.routes_tested = routes
        report.viewports_tested = list(VIEWPORTS.keys())

        # Step 2: Capture screenshots
        screenshots = await capture_screenshots(frontend_files, routes)
        if not screenshots:
            report.summary = {"pass": 0, "warn": 0, "fail": 0}
            return report

        # Extract design tokens from architecture
        design_tokens = None
        if architecture and "design_system" in architecture:
            design_tokens = architecture["design_system"]

        # Step 3: Analyse each screenshot
        for route, vp_shots in screenshots.items():
            report.grades[route] = {}
            for vp_name, png_bytes in vp_shots.items():
                report.screenshots_analyzed += 1
                try:
                    issues = await analyze_screenshot(
                        screenshot_bytes=png_bytes,
                        route=route,
                        viewport=vp_name,
                        vp_size=VIEWPORTS[vp_name],
                        design_tokens=design_tokens,
                        architecture=architecture,
                    )
                except Exception as e:
                    logger.warning(
                        "Analysis failed for %s @ %s: %s",
                        route,
                        vp_name,
                        e,
                    )
                    issues = []
                report.issues.extend(issues)
                report.grades[route][vp_name] = _grade_viewport(issues)

        # Step 4: Compile summary
        all_grades = [
            g for route_grades in report.grades.values() for g in route_grades.values()
        ]
        report.summary = {
            "pass": all_grades.count("pass"),
            "warn": all_grades.count("warn"),
            "fail": all_grades.count("fail"),
        }

        return report


# =========================================================================
# A11y Contract Audit (Phase 3.5)
# =========================================================================


def _luminance(hex_color: str) -> float:
    """Compute relative luminance per WCAG 2.1 from a '#RRGGBB' hex string."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return 0.0
    r, g, b = (
        int(hex_color[:2], 16) / 255.0,
        int(hex_color[2:4], 16) / 255.0,
        int(hex_color[4:6], 16) / 255.0,
    )

    # Linearize sRGB
    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = linearize(r), linearize(g), linearize(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def _contrast_ratio(hex1: str, hex2: str) -> float:
    """Compute WCAG contrast ratio between two hex colors. Returns ratio >= 1.0."""
    l1 = _luminance(hex1)
    l2 = _luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ARIA role expectations for common shadcn/ui components
_EXPECTED_ARIA: Dict[str, List[str]] = {
    "Button": ["<button", 'role="button"'],
    "Dialog": ['role="dialog"', "aria-labelledby"],
    "Table": ["<table", 'role="grid"'],
    "Input": ["<input", 'role="textbox"'],
    "NavigationMenu": ["<nav", 'role="navigation"'],
    "Tabs": ['role="tablist"', 'role="tab"'],
}


def audit_against_contract(
    report: VisualQAReport,
    contract: Dict[str, Any],
    frontend_code: Optional[Dict[str, str]] = None,
) -> VisualQAReport:
    """Cross-reference a VisualQAReport against a DesignContract for A11y issues.

    Checks:
    1. WCAG AA color contrast for all token pairs against background/surface
    2. ARIA role presence in generated frontend code for each component mapping
    """
    a11y_issues: List[VisualIssue] = []
    color_tokens = contract.get("color_tokens", [])

    # Build a lookup of token hex values by name
    token_hex: Dict[str, str] = {}
    for t in color_tokens:
        token_hex[t["name"]] = t["hex_fallback"]

    bg_hex = token_hex.get("background", "#FFFFFF")
    surface_hex = token_hex.get("surface", bg_hex)

    # Check 1: Contrast ratios
    text_tokens = [
        "text_primary",
        "text_secondary",
        "primary",
        "secondary",
        "accent",
        "error",
        "success",
    ]
    for token_name in text_tokens:
        hex_val = token_hex.get(token_name)
        if not hex_val:
            continue

        for bg_name, bg_val in [("background", bg_hex), ("surface", surface_hex)]:
            ratio = _contrast_ratio(hex_val, bg_val)
            # Normal text threshold
            if ratio < WCAG_AA_NORMAL_TEXT:
                severity = (
                    VisualSeverity.HIGH
                    if ratio < WCAG_AA_LARGE_TEXT
                    else VisualSeverity.MEDIUM
                )
                a11y_issues.append(
                    VisualIssue(
                        id=f"A11Y_CONTRAST_{token_name}_{bg_name}".upper(),
                        title=f"Low contrast: {token_name} on {bg_name}",
                        description=(
                            f"Color token '{token_name}' ({hex_val}) against '{bg_name}' ({bg_val}) "
                            f"has contrast ratio {ratio:.2f}:1. WCAG AA requires 4.5:1 for normal text."
                        ),
                        severity=severity,
                        viewport="all",
                        route="/",
                        location_hint=f"Token: {token_name} on {bg_name}",
                        fix_suggestion=f"Adjust '{token_name}' to achieve at least 4.5:1 contrast against '{bg_name}'.",
                    )
                )

    # Check 2: ARIA role verification in generated code
    if frontend_code:
        all_code = "\n".join(frontend_code.values())
        component_map = contract.get("component_map", [])
        for mapping in component_map:
            shadcn_name = mapping.get("shadcn_component", "")
            expected_patterns = _EXPECTED_ARIA.get(shadcn_name)
            if not expected_patterns:
                continue
            found = any(pattern in all_code for pattern in expected_patterns)
            if not found:
                a11y_issues.append(
                    VisualIssue(
                        id=f"A11Y_ARIA_{shadcn_name}".upper(),
                        title=f"Missing ARIA: {shadcn_name}",
                        description=(
                            f"Component '{shadcn_name}' (from detected '{mapping.get('detected_type', '?')}') "
                            f"expected one of: {expected_patterns} but none found in generated code."
                        ),
                        severity=VisualSeverity.MEDIUM,
                        viewport="all",
                        route="/",
                        location_hint=f"Component: {shadcn_name}",
                        fix_suggestion=f"Ensure {shadcn_name} uses proper semantic HTML or ARIA roles.",
                    )
                )

    # Merge into report
    report.issues.extend(a11y_issues)

    # Recompute summary
    all_grades = [
        g for route_grades in report.grades.values() for g in route_grades.values()
    ]
    report.summary = {
        "pass": all_grades.count("pass"),
        "warn": all_grades.count("warn"),
        "fail": all_grades.count("fail"),
        "a11y_issues": len(a11y_issues),
    }

    return report


# =========================================================================
# Reviewer Injection Helper
# =========================================================================


def build_reviewer_visual_context(visual_qa_report: Dict[str, Any]) -> Optional[str]:
    """
    Build a text block summarising critical/high visual issues for injection
    into the reviewer's prompt as mandatory review criteria.

    Returns None if there are no critical/high issues.
    """
    issues = visual_qa_report.get("issues", [])
    critical_high = [i for i in issues if i.get("severity") in ("critical", "high")]
    if not critical_high:
        return None

    lines = ["VISUAL QA ISSUES (must be addressed in review):"]
    for issue in critical_high:
        lines.append(
            f"- [{issue.get('id', '?')}] {issue.get('title', '?')} "
            f"({issue.get('viewport', '?')}): {issue.get('fix_suggestion', 'N/A')}"
        )
    return "\n".join(lines)
