"""
IE-1 Architectural Guardrails — Phase 2.0 Subsystem #3

AST-based analysis engine enforcing 10 Iron Rules on generated code.
Pipeline position: between Aggregator and Tester, max 2 fix cycles.

Classes:
    Violation       — A single rule violation with file, line, and fix instruction
    IronRule        — A rule definition: id, title, severity, detect callable
    ArchitecturalGuardrails — Main engine: validate_project(), build import graph,
                              detect circular deps, classify file layers
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ViolationSeverity(str, Enum):
    """Severity of an architectural violation."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


@dataclass
class Violation:
    """A single architectural violation."""

    rule_id: str
    file_path: str
    line: int
    message: str
    fix_instruction: str
    severity: ViolationSeverity

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "file_path": self.file_path,
            "line": self.line,
            "message": self.message,
            "fix_instruction": self.fix_instruction,
            "severity": self.severity.value,
        }


@dataclass
class IronRule:
    """An architectural Iron Rule definition."""

    id: str
    title: str
    description: str
    severity: ViolationSeverity
    detect: Callable[[str, str, Dict[str, str]], List[Violation]]
    fix_instruction: str
    target_languages: List[str] = field(
        default_factory=lambda: ["javascript", "typescript", "jsx", "tsx"]
    )


# ======================================================================
# File layer classification
# ======================================================================

# Directories that are considered UI component directories
UI_DIRS = {"components", "pages", "app", "views", "screens"}

# Directories where business logic / API calls belong
SERVICE_DIRS = {
    "lib/api",
    "services",
    "hooks",
    "lib",
    "api",
    "utils",
    "store",
    "stores",
}

# Patterns that indicate business logic (should NOT be in UI components)
BUSINESS_LOGIC_PATTERNS = [
    re.compile(r"\bfetch\s*\("),
    re.compile(r"\baxios\b"),
    re.compile(r"\b(?:prisma|convex|drizzle|mongoose|sequelize)\b", re.IGNORECASE),
    re.compile(r"\.(?:query|mutation|mutate|subscribe)\s*\("),
    re.compile(r'(?:GET|POST|PUT|DELETE|PATCH)\s*["\']https?://'),
]

# Patterns for direct DOM manipulation
DOM_MANIPULATION_PATTERNS = [
    re.compile(r"document\.getElementById\s*\("),
    re.compile(r"document\.querySelector(?:All)?\s*\("),
    re.compile(r"document\.createElement\s*\("),
    re.compile(r"\.innerHTML\s*="),
    re.compile(r"\.outerHTML\s*="),
    re.compile(r"document\.write\s*\("),
]


def _get_language(filename: str) -> str:
    """Detect language from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
    }
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext_map.get(ext, "unknown")


def _classify_file_layer(filepath: str) -> str:
    """
    Classify a file into an architectural layer.

    Returns one of: "ui", "service", "hook", "page", "test", "config", "unknown"
    """
    parts = filepath.replace("\\", "/").lower().split("/")
    # Keep original-case parts for hook filename detection
    # (avoids false positives: "UserCard.tsx" lowered → "usercard" starts with "use")
    original_parts = filepath.replace("\\", "/").split("/")

    if any(
        p in ("__tests__", "tests", "test", "spec") or p.startswith("test_")
        for p in parts
    ):
        return "test"
    if any(p in ("config", "configs", ".config") for p in parts):
        return "config"
    # Hook detection: directory named "hooks" OR filename starts with lowercase "use"
    # MUST use original case to avoid false positives (UserCard ≠ useCard)
    if any(p == "hooks" for p in parts):
        return "hook"
    orig_filename = original_parts[-1]
    if (
        orig_filename.startswith("use")
        and len(orig_filename) > 3
        and orig_filename[3].isupper()
    ):
        return "hook"
    if any(p == "components" or p == "views" or p == "screens" for p in parts):
        return "ui"
    if any(p in ("pages", "app") for p in parts):
        return "page"
    if any(p in ("services", "lib", "api", "utils", "store", "stores") for p in parts):
        return "service"

    return "unknown"


def _is_ui_file(filepath: str) -> bool:
    """Check if file lives under a UI directory."""
    return _classify_file_layer(filepath) in ("ui", "page")


def _extract_imports_js(content: str) -> List[str]:
    """Extract import sources from JS/TS code."""
    pattern = re.compile(
        r'(?:import\s+.*?\s+from\s+["\']([^"\']+)["\']|'
        r'require\s*\(\s*["\']([^"\']+)["\']\s*\))',
        re.MULTILINE,
    )
    return [m.group(1) or m.group(2) for m in pattern.finditer(content)]


def _extract_imports_py(content: str) -> List[str]:
    """Extract import sources from Python code."""
    pattern = re.compile(
        r"(?:from\s+(\S+)\s+import|import\s+(\S+))",
        re.MULTILINE,
    )
    return [m.group(1) or m.group(2) for m in pattern.finditer(content)]


# ======================================================================
# ARCH001 — Business logic in UI component
# ======================================================================


def _detect_arch001(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect fetch/axios/ORM calls in components/ directory."""
    if not _is_ui_file(filepath):
        return []

    violations = []
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        for pattern in BUSINESS_LOGIC_PATTERNS:
            if pattern.search(line):
                violations.append(
                    Violation(
                        rule_id="ARCH001",
                        file_path=filepath,
                        line=i,
                        message=f"Business logic detected in UI component: {line.strip()[:80]}",
                        fix_instruction=(
                            "Move this API/data call to a custom hook in hooks/ or "
                            "a service in lib/api/. The component should only call the hook."
                        ),
                        severity=ViolationSeverity.CRITICAL,
                    )
                )
                break  # One violation per line is enough

    return violations


# ======================================================================
# ARCH002 — API call outside service layer
# ======================================================================


def _detect_arch002(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect HTTP calls outside lib/api/, services/, hooks/."""
    layer = _classify_file_layer(filepath)
    if layer in ("service", "hook", "test", "config"):
        return []  # Allowed locations

    violations = []
    api_call_patterns = [
        re.compile(r'\bfetch\s*\(\s*["\']'),
        re.compile(r"\baxios\.(?:get|post|put|delete|patch)\s*\("),
        re.compile(r"\bhttp(?:Client|Service)\.\w+\s*\("),
    ]

    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        for pattern in api_call_patterns:
            if pattern.search(line):
                violations.append(
                    Violation(
                        rule_id="ARCH002",
                        file_path=filepath,
                        line=i,
                        message=f"API call outside service layer: {line.strip()[:80]}",
                        fix_instruction=(
                            "Move HTTP calls to lib/api/ or services/ directory. "
                            "Import and call from there."
                        ),
                        severity=ViolationSeverity.CRITICAL,
                    )
                )
                break

    return violations


# ======================================================================
# ARCH003 — File exceeds 300 lines
# ======================================================================


def _detect_arch003(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect files exceeding 300 lines."""
    line_count = content.count("\n") + 1
    if line_count <= 300:
        return []

    return [
        Violation(
            rule_id="ARCH003",
            file_path=filepath,
            line=1,
            message=f"File has {line_count} lines (limit: 300). Split into smaller modules.",
            fix_instruction=(
                f"Split this {line_count}-line file into smaller modules. "
                "Extract logical groups into separate files and re-export."
            ),
            severity=ViolationSeverity.HIGH,
        )
    ]


# ======================================================================
# ARCH004 — Prop drilling > 2 levels
# ======================================================================


def _detect_arch004(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """
    Detect potential prop drilling by finding props passed through >2 component levels.
    Heuristic: count how many times the same prop name appears as both received and passed.
    """
    if not _is_ui_file(filepath):
        return []

    lang = _get_language(filepath)
    if lang not in ("jsx", "tsx", "javascript", "typescript"):
        return []

    # Find props that are both destructured from function params AND spread/passed to children
    # Heuristic: look for pattern like ({propName, ...}) and <Child propName={propName} />
    prop_receive = re.findall(
        r"(?:function\s+\w+|const\s+\w+\s*=\s*)\s*\(\s*\{([^}]+)\}",
        content,
    )
    if not prop_receive:
        return []

    received_props: Set[str] = set()
    for match in prop_receive:
        for prop in match.split(","):
            prop = prop.strip().split("=")[0].strip().split(":")[0].strip()
            if prop and prop != "...props" and not prop.startswith("..."):
                received_props.add(prop)

    # Now check how many of those same props are passed down to child components
    pass_pattern = re.compile(r"(\w+)\s*=\s*\{(\w+)\}")
    passed_down: Set[str] = set()
    for m in pass_pattern.finditer(content):
        if m.group(1) == m.group(2) and m.group(1) in received_props:
            passed_down.add(m.group(1))

    if len(passed_down) > 2:
        return [
            Violation(
                rule_id="ARCH004",
                file_path=filepath,
                line=1,
                message=f"Potential prop drilling: {len(passed_down)} props passed through ({', '.join(sorted(passed_down)[:5])})",
                fix_instruction=(
                    "Use React Context or a state management library (Zustand, Jotai) "
                    "instead of passing props through multiple component levels."
                ),
                severity=ViolationSeverity.HIGH,
            )
        ]

    return []


# ======================================================================
# ARCH005 — Direct DOM manipulation
# ======================================================================


def _detect_arch005(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect direct DOM manipulation in React components."""
    lang = _get_language(filepath)
    if lang not in ("jsx", "tsx", "javascript", "typescript"):
        return []

    violations = []
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        for pattern in DOM_MANIPULATION_PATTERNS:
            if pattern.search(line):
                violations.append(
                    Violation(
                        rule_id="ARCH005",
                        file_path=filepath,
                        line=i,
                        message=f"Direct DOM manipulation in React code: {line.strip()[:80]}",
                        fix_instruction=(
                            "Use React refs (useRef) instead of document.getElementById. "
                            "Use state/props instead of innerHTML."
                        ),
                        severity=ViolationSeverity.HIGH,
                    )
                )
                break

    return violations


# ======================================================================
# ARCH006 — Inconsistent naming
# ======================================================================


def _detect_arch006(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect naming convention violations."""
    violations = []
    filename = os.path.basename(filepath)
    lang = _get_language(filepath)

    if lang in ("jsx", "tsx"):
        # React components should be PascalCase
        name_no_ext = filename.rsplit(".", 1)[0]
        if (
            name_no_ext
            and not name_no_ext[0].isupper()
            and name_no_ext
            not in ("index", "layout", "page", "loading", "error", "not-found")
        ):
            violations.append(
                Violation(
                    rule_id="ARCH006",
                    file_path=filepath,
                    line=1,
                    message=f"Component file '{filename}' should use PascalCase naming.",
                    fix_instruction=f"Rename to '{name_no_ext[0].upper() + name_no_ext[1:]}.{filename.rsplit('.', 1)[-1]}'",
                    severity=ViolationSeverity.HIGH,
                )
            )

    # Hooks must start with "use"
    if lang in ("javascript", "typescript", "jsx", "tsx"):
        re.findall(r"(?:export\s+)?(?:function|const)\s+(use\w+)", content)
        layer = _classify_file_layer(filepath)
        if layer == "hook":
            name_no_ext = filename.rsplit(".", 1)[0]
            if not name_no_ext.startswith("use"):
                violations.append(
                    Violation(
                        rule_id="ARCH006",
                        file_path=filepath,
                        line=1,
                        message=f"Hook file '{filename}' should start with 'use' prefix.",
                        fix_instruction=f"Rename to 'use{name_no_ext[0].upper() + name_no_ext[1:]}.{filename.rsplit('.', 1)[-1]}'",
                        severity=ViolationSeverity.HIGH,
                    )
                )

    return violations


# ======================================================================
# ARCH007 — Missing TypeScript types
# ======================================================================


def _detect_arch007(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect excessive 'any' type usage in TypeScript."""
    lang = _get_language(filepath)
    if lang not in ("typescript", "tsx"):
        return []

    any_pattern = re.compile(r":\s*any\b")
    matches = list(any_pattern.finditer(content))

    if len(matches) <= 2:
        return []  # Allow a couple of `any` for pragmatic code

    violations = []
    for m in matches[:5]:  # Report max 5
        line_num = content[: m.start()].count("\n") + 1
        violations.append(
            Violation(
                rule_id="ARCH007",
                file_path=filepath,
                line=line_num,
                message="Using 'any' type defeats TypeScript's type safety.",
                fix_instruction=(
                    "Define a proper interface/type or use 'unknown' with type guards."
                ),
                severity=ViolationSeverity.HIGH,
            )
        )

    return violations


# ======================================================================
# ARCH008 — Circular dependency detection
# ======================================================================


def _build_import_graph(all_files: Dict[str, str]) -> Dict[str, Set[str]]:
    """
    Build a directed import graph from all project files.
    Keys are file paths, values are sets of imported file paths.
    """
    graph: Dict[str, Set[str]] = defaultdict(set)
    file_lookup: Dict[str, str] = {}

    # Build lookup: basename without ext → full path
    for fp in all_files:
        base = os.path.basename(fp).rsplit(".", 1)[0]
        file_lookup[base] = fp
        # Also store directory-relative paths
        parts = fp.replace("\\", "/").split("/")
        for i in range(len(parts)):
            key = "/".join(parts[i:]).rsplit(".", 1)[0]
            file_lookup[key] = fp

    for fp, content in all_files.items():
        lang = _get_language(fp)
        if lang in ("javascript", "typescript", "jsx", "tsx"):
            imports = _extract_imports_js(content)
        elif lang == "python":
            imports = _extract_imports_py(content)
        else:
            continue

        for imp in imports:
            # Resolve relative imports
            imp_clean = imp.lstrip("./").replace("@/", "")
            # Try to find the imported file in our project
            resolved = file_lookup.get(imp_clean)
            if not resolved:
                # Try basename
                resolved = file_lookup.get(os.path.basename(imp_clean))
            if resolved and resolved != fp:
                graph[fp].add(resolved)

    return graph


def _detect_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Find all cycles in the import graph using iterative DFS."""
    visited: Set[str] = set()
    cycles: List[List[str]] = []
    for start in graph:
        if start in visited:
            continue
        stack = [(start, iter(graph.get(start, set())), [start])]
        on_stack = {start}
        while stack:
            node, neighbors, path = stack[-1]
            try:
                neighbor = next(neighbors)
                if neighbor in on_stack:
                    cycles.append(path[path.index(neighbor) :] + [neighbor])
                elif neighbor not in visited:
                    on_stack.add(neighbor)
                    stack.append(
                        (neighbor, iter(graph.get(neighbor, set())), path + [neighbor])
                    )
            except StopIteration:
                on_stack.discard(node)
                visited.add(node)
                stack.pop()
    return cycles


def _detect_arch008(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect circular dependencies in the import graph."""
    # This rule operates on the full project, not a single file.
    # We detect cycles in the import graph and report on all participating files.
    graph = _build_import_graph(all_files)
    cycles = _detect_cycles(graph)

    violations = []
    for cycle in cycles:
        if filepath in cycle:
            cycle_str = " → ".join(os.path.basename(f) for f in cycle)
            violations.append(
                Violation(
                    rule_id="ARCH008",
                    file_path=filepath,
                    line=1,
                    message=f"Circular dependency: {cycle_str}",
                    fix_instruction=(
                        "Break the cycle by extracting shared code into a separate module, "
                        "or invert the dependency using dependency injection."
                    ),
                    severity=ViolationSeverity.CRITICAL,
                )
            )

    return violations


# ======================================================================
# ARCH009 — Unused imports
# ======================================================================


def _detect_arch009(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect imports whose identifiers are not used elsewhere in the file."""
    lang = _get_language(filepath)
    if lang not in ("javascript", "typescript", "jsx", "tsx"):
        return []

    violations = []
    lines = content.split("\n")

    import_pattern = re.compile(r'import\s+\{([^}]+)\}\s+from\s+["\'][^"\']+["\']')

    for i, line in enumerate(lines, 1):
        m = import_pattern.match(line.strip())
        if not m:
            continue

        identifiers = [
            s.strip().split(" as ")[-1].strip() for s in m.group(1).split(",")
        ]
        rest_of_file = "\n".join(lines[i:])  # Everything after the import line

        for ident in identifiers:
            if not ident:
                continue
            # Check if identifier appears anywhere else in the file (not just in imports)
            # Use word boundary to avoid partial matches
            usage = re.search(r"\b" + re.escape(ident) + r"\b", rest_of_file)
            if not usage:
                violations.append(
                    Violation(
                        rule_id="ARCH009",
                        file_path=filepath,
                        line=i,
                        message=f"Unused import: '{ident}'",
                        fix_instruction=f"Remove unused import '{ident}' to reduce bundle size.",
                        severity=ViolationSeverity.HIGH,
                    )
                )

    return violations


# ======================================================================
# ARCH010 — Missing error boundary at route level
# ======================================================================


def _detect_arch010(
    filepath: str, content: str, all_files: Dict[str, str]
) -> List[Violation]:
    """Detect route-level components missing ErrorBoundary wrapping."""
    layer = _classify_file_layer(filepath)
    if layer != "page":
        return []

    lang = _get_language(filepath)
    if lang not in ("jsx", "tsx", "javascript", "typescript"):
        return []

    # Check if any ErrorBoundary is present in the file
    has_error_boundary = (
        "ErrorBoundary" in content
        or "error.tsx" in filepath  # Next.js error boundary convention
        or "error.jsx" in filepath
        or "error.js" in filepath
    )

    if has_error_boundary:
        return []

    # Check if there's a sibling error.tsx in the same directory (Next.js convention)
    dir_path = os.path.dirname(filepath)
    for sibling in all_files:
        if os.path.dirname(sibling) == dir_path and os.path.basename(
            sibling
        ).startswith("error."):
            return []  # Next.js error boundary exists as sibling

    return [
        Violation(
            rule_id="ARCH010",
            file_path=filepath,
            line=1,
            message="Route-level component missing ErrorBoundary. Uncaught errors will crash the page.",
            fix_instruction=(
                "Add an ErrorBoundary component wrapping the route content, "
                "or create an error.tsx file in the same directory (Next.js convention)."
            ),
            severity=ViolationSeverity.HIGH,
        )
    ]


# ======================================================================
# Main Engine
# ======================================================================

# All 10 Iron Rules
IRON_RULES: List[IronRule] = [
    IronRule(
        id="ARCH001",
        title="Business logic in UI component",
        description="fetch/axios/ORM calls detected in components/ directory",
        severity=ViolationSeverity.CRITICAL,
        detect=_detect_arch001,
        fix_instruction="Move API/data calls to hooks/ or lib/api/",
    ),
    IronRule(
        id="ARCH002",
        title="API call outside service layer",
        description="HTTP calls found outside lib/api/, services/, hooks/",
        severity=ViolationSeverity.CRITICAL,
        detect=_detect_arch002,
        fix_instruction="Move HTTP calls to lib/api/ or services/",
    ),
    IronRule(
        id="ARCH003",
        title="File exceeds 300 lines",
        description="Large file makes code harder to maintain",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch003,
        fix_instruction="Split into smaller modules",
        target_languages=["python", "javascript", "typescript", "jsx", "tsx"],
    ),
    IronRule(
        id="ARCH004",
        title="Prop drilling > 2 levels",
        description="Props passed through too many component levels",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch004,
        fix_instruction="Use React Context or state management",
    ),
    IronRule(
        id="ARCH005",
        title="Direct DOM manipulation",
        description="document.getElementById/innerHTML in React code",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch005,
        fix_instruction="Use React refs and state instead",
    ),
    IronRule(
        id="ARCH006",
        title="Inconsistent naming",
        description="PascalCase for components, 'use' prefix for hooks, kebab-case for files",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch006,
        fix_instruction="Follow naming conventions",
    ),
    IronRule(
        id="ARCH007",
        title="Missing TypeScript types",
        description="Excessive 'any' annotations defeat type safety",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch007,
        fix_instruction="Define proper types or use 'unknown'",
    ),
    IronRule(
        id="ARCH008",
        title="Circular dependency",
        description="Import graph contains cycles",
        severity=ViolationSeverity.CRITICAL,
        detect=_detect_arch008,
        fix_instruction="Break cycle by extracting shared module",
        target_languages=["python", "javascript", "typescript", "jsx", "tsx"],
    ),
    IronRule(
        id="ARCH009",
        title="Unused imports",
        description="Imported identifiers not used in file",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch009,
        fix_instruction="Remove unused imports",
    ),
    IronRule(
        id="ARCH010",
        title="Missing error boundary",
        description="Route-level component lacks ErrorBoundary",
        severity=ViolationSeverity.HIGH,
        detect=_detect_arch010,
        fix_instruction="Add ErrorBoundary or error.tsx sibling",
    ),
]


class ArchitecturalGuardrails:
    """
    Main guardrails engine that validates a full project against Iron Rules.

    Usage:
        guardrails = ArchitecturalGuardrails()
        violations = guardrails.validate_project(project_files)
        critical = guardrails.get_critical_violations(violations)
    """

    def __init__(self, rules: Optional[List[IronRule]] = None):
        self.rules = rules or IRON_RULES

    def validate_project(self, project_files: Dict[str, str]) -> List[Violation]:
        """
        Run all Iron Rules against all project files.

        Args:
            project_files: Dict of filepath → file content

        Returns:
            List of Violation objects, sorted by severity (CRITICAL first)
        """
        all_violations: List[Violation] = []

        for filepath, content in project_files.items():
            lang = _get_language(filepath)
            for rule in self.rules:
                if lang in rule.target_languages or lang == "unknown":
                    try:
                        violations = rule.detect(filepath, content, project_files)
                        all_violations.extend(violations)
                    except Exception:
                        # Don't let a single rule crash the whole analysis, but
                        # NEVER swallow silently: a guardrail that crashes is a
                        # safety check that did not run. Surface it loudly so a
                        # toxic rule cannot hide a missed violation (H.17).
                        logger.warning(
                            "guardrail rule %s crashed on %s; treating as "
                            "DID-NOT-EVALUATE (no silent pass)",
                            rule.id,
                            filepath,
                            exc_info=True,
                        )

        # Deduplicate by (rule_id, file_path, line)
        seen: Set[Tuple[str, str, int]] = set()
        unique: List[Violation] = []
        for v in all_violations:
            key = (v.rule_id, v.file_path, v.line)
            if key not in seen:
                seen.add(key)
                unique.append(v)

        # Sort: CRITICAL first, then HIGH, then MEDIUM
        severity_order = {
            ViolationSeverity.CRITICAL: 0,
            ViolationSeverity.HIGH: 1,
            ViolationSeverity.MEDIUM: 2,
        }
        unique.sort(key=lambda v: severity_order.get(v.severity, 99))

        return unique

    def get_critical_violations(self, violations: List[Violation]) -> List[Violation]:
        """Filter only CRITICAL violations."""
        return [v for v in violations if v.severity == ViolationSeverity.CRITICAL]

    def get_violations_by_file(
        self, violations: List[Violation]
    ) -> Dict[str, List[Violation]]:
        """Group violations by file path."""
        by_file: Dict[str, List[Violation]] = defaultdict(list)
        for v in violations:
            by_file[v.file_path].append(v)
        return dict(by_file)

    def build_fix_prompt(self, violations: List[Violation], target_node: str) -> str:
        """
        Build a targeted fix prompt for the frontend or backend agent.

        This is NOT a full regeneration prompt — it tells the agent exactly
        which files to fix and how.

        Args:
            violations: List of violations to fix
            target_node: "frontend" or "backend"

        Returns:
            Fix prompt string
        """
        by_file = self.get_violations_by_file(violations)

        lines = [
            "## ARCHITECTURAL GUARDRAILS FIX REQUIRED",
            "",
            f"The following {len(violations)} violation(s) were detected. "
            "Fix ONLY these issues. Do NOT regenerate unaffected files.",
            "",
        ]

        for filepath, file_violations in by_file.items():
            lines.append(f"### {filepath}")
            for v in file_violations:
                lines.append(f"- **[{v.rule_id}]** Line {v.line}: {v.message}")
                lines.append(f"  Fix: {v.fix_instruction}")
            lines.append("")

        lines.append("Return ONLY the modified files with the fixes applied.")

        return "\n".join(lines)
