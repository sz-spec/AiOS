"""
Static Code Review Rules

Rule-based code analysis that complements AI review.
These rules catch common issues without needing AI.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

from .service import CodeLocation, ReviewCategory, ReviewFinding, ReviewSeverity


@dataclass
class Rule:
    """A static analysis rule."""

    id: str
    title: str
    description: str
    severity: ReviewSeverity
    category: ReviewCategory
    pattern: re.Pattern
    suggestion: str
    languages: list[str]  # ["python", "javascript", etc.]
    code_fix: Optional[Callable[[str], str]] = None


class SecurityRules:
    """Security-focused static analysis rules."""

    RULES = [
        Rule(
            id="SEC001",
            title="Hardcoded Secret Detected",
            description="API keys, passwords, or secrets should not be hardcoded in source code. Use environment variables instead.",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(
                r'(?:api[_-]?key|secret|password|token|auth)\s*[=:]\s*["\'][\w\-]{16,}["\']',
                re.IGNORECASE,
            ),
            suggestion="Move secrets to environment variables: process.env.API_KEY or os.environ['API_KEY']",
            languages=["python", "javascript", "typescript"],
        ),
        Rule(
            id="SEC002",
            title="SQL Injection Vulnerability",
            description="String concatenation in SQL queries can lead to SQL injection attacks.",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(
                r'(?:execute|query|raw)\s*\(\s*[f"\'].*?\{.*?\}.*?["\']|'
                r'(?:execute|query)\s*\(\s*["\'].*?\+.*?["\']'
            ),
            suggestion="Use parameterized queries instead of string concatenation.",
            languages=["python", "javascript"],
        ),
        Rule(
            id="SEC003",
            title="XSS Vulnerability - innerHTML",
            description="Using innerHTML with user input can lead to Cross-Site Scripting attacks.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(r"\.innerHTML\s*="),
            suggestion="Use textContent instead, or sanitize input with DOMPurify.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="SEC004",
            title="Insecure Random Number",
            description="Math.random() is not cryptographically secure.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(r"Math\.random\(\)"),
            suggestion="Use crypto.getRandomValues() or crypto.randomUUID() for security-sensitive operations.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="SEC005",
            title="Eval Usage Detected",
            description="eval() executes arbitrary code and is a security risk.",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(r"\beval\s*\("),
            suggestion="Avoid eval(). Use JSON.parse() for JSON data or safer alternatives.",
            languages=["python", "javascript"],
        ),
        Rule(
            id="SEC006",
            title="Unsafe Pickle Usage",
            description="pickle.load() on untrusted data can execute arbitrary code.",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(r"pickle\.loads?\s*\("),
            suggestion="Use JSON for data serialization, or validate source of pickled data.",
            languages=["python"],
        ),
        Rule(
            id="SEC007",
            title="Missing CSRF Protection",
            description="Form submission without CSRF token protection.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(
                r'<form[^>]*method\s*=\s*["\']post["\'][^>]*>(?:(?!csrf).)*</form>',
                re.IGNORECASE | re.DOTALL,
            ),
            suggestion="Add CSRF token to forms: {% csrf_token %} or use a CSRF library.",
            languages=["html", "python", "javascript"],
        ),
        Rule(
            id="SEC008",
            title="Insecure Cookie Settings",
            description="Cookies should have secure, httpOnly, and sameSite attributes.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.SECURITY,
            pattern=re.compile(r"document\.cookie\s*="),
            suggestion="Set cookies with secure flags: httpOnly, secure, sameSite='strict'",
            languages=["javascript", "typescript"],
        ),
    ]

    @classmethod
    def check(cls, code: str, filename: str, language: str) -> list[ReviewFinding]:
        """Check code against security rules."""
        findings = []
        lines = code.split("\n")

        for rule in cls.RULES:
            if language not in rule.languages:
                continue

            for i, line in enumerate(lines, 1):
                if rule.pattern.search(line):
                    findings.append(
                        ReviewFinding(
                            id=f"{rule.id}_{i}",
                            title=rule.title,
                            description=rule.description,
                            severity=rule.severity,
                            category=rule.category,
                            location=CodeLocation(
                                file=filename,
                                start_line=i,
                                end_line=i,
                            ),
                            suggestion=rule.suggestion,
                            code_before=line.strip(),
                            auto_fixable=rule.code_fix is not None,
                        )
                    )

        return findings


class PerformanceRules:
    """Performance-focused static analysis rules."""

    RULES = [
        Rule(
            id="PERF001",
            title="N+1 Query Pattern",
            description="Querying inside a loop can cause N+1 query problems.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(
                r"for\s+.*?:\s*\n.*?(?:\.query|\.find|\.get|\.fetch)\s*\("
            ),
            suggestion="Use batch queries or eager loading (prefetch_related, include, etc.)",
            languages=["python", "javascript", "typescript"],
        ),
        Rule(
            id="PERF002",
            title="Array Method in Render",
            description="Creating new arrays with map/filter in render causes unnecessary re-renders.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(r"return\s*\([^)]*\.(?:map|filter|reduce)\s*\("),
            suggestion="Use useMemo() to memoize array transformations.",
            languages=["javascript", "typescript", "jsx", "tsx"],
        ),
        Rule(
            id="PERF003",
            title="Missing React.memo",
            description="Component might benefit from React.memo for optimization.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(
                r"export\s+(?:default\s+)?function\s+\w+\s*\([^)]*props"
            ),
            suggestion="Consider wrapping with React.memo() if props don't change often.",
            languages=["jsx", "tsx"],
        ),
        Rule(
            id="PERF004",
            title="Synchronous File Operation",
            description="Synchronous file operations block the event loop.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(r"(?:readFileSync|writeFileSync|appendFileSync)\s*\("),
            suggestion="Use async versions: readFile, writeFile, appendFile",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="PERF005",
            title="Missing Database Index Hint",
            description="Query on large table without apparent index usage.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(
                r"SELECT\s+.*FROM\s+\w+\s+WHERE\s+(?!.*(?:id|_id)\s*=)", re.IGNORECASE
            ),
            suggestion="Ensure indexed columns are used in WHERE clause.",
            languages=["sql"],
        ),
        Rule(
            id="PERF006",
            title="Large Bundle Import",
            description="Importing entire library when only part is needed.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(
                r'import\s+\*\s+as\s+\w+\s+from\s+["\'](?:lodash|moment|date-fns)["\']'
            ),
            suggestion="Import only needed functions: import { debounce } from 'lodash'",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="PERF007",
            title="Inefficient String Concatenation",
            description="String concatenation in loop is inefficient in Python.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(r'for\s+.*?:\s*\n\s*\w+\s*\+=\s*["\']'),
            suggestion="Use list.append() and ''.join() for building strings.",
            languages=["python"],
        ),
    ]

    @classmethod
    def check(cls, code: str, filename: str, language: str) -> list[ReviewFinding]:
        """Check code against performance rules."""
        findings = []

        for rule in cls.RULES:
            if language not in rule.languages:
                continue

            matches = list(rule.pattern.finditer(code))
            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                findings.append(
                    ReviewFinding(
                        id=f"{rule.id}_{line_num}",
                        title=rule.title,
                        description=rule.description,
                        severity=rule.severity,
                        category=rule.category,
                        location=CodeLocation(
                            file=filename,
                            start_line=line_num,
                            end_line=line_num,
                        ),
                        suggestion=rule.suggestion,
                        code_before=match.group()[:100],
                        auto_fixable=False,
                    )
                )

        return findings


class StyleRules:
    """Code style and formatting rules."""

    RULES = [
        Rule(
            id="STYLE001",
            title="Console Log in Production",
            description="console.log statements should be removed in production code.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.STYLE,
            pattern=re.compile(r"console\.(?:log|debug|info)\s*\("),
            suggestion="Remove console.log or use a proper logging library.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="STYLE002",
            title="TODO/FIXME Comment",
            description="TODO or FIXME comment found - consider addressing before merge.",
            severity=ReviewSeverity.INFO,
            category=ReviewCategory.STYLE,
            pattern=re.compile(
                r"(?://|#|/\*)\s*(?:TODO|FIXME|HACK|XXX)", re.IGNORECASE
            ),
            suggestion="Address the TODO/FIXME or create an issue to track it.",
            languages=["python", "javascript", "typescript"],
        ),
        Rule(
            id="STYLE003",
            title="Magic Number",
            description="Magic numbers should be named constants.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.STYLE,
            pattern=re.compile(r'(?<!["\'\w])\d{3,}(?!["\'\w])'),
            suggestion="Extract to a named constant: const MAX_RETRIES = 1000",
            languages=["python", "javascript", "typescript"],
        ),
        Rule(
            id="STYLE004",
            title="Long Line",
            description="Line exceeds recommended length (120 characters).",
            severity=ReviewSeverity.INFO,
            category=ReviewCategory.STYLE,
            pattern=re.compile(r"^.{121,}$", re.MULTILINE),
            suggestion="Break long lines for better readability.",
            languages=["python", "javascript", "typescript"],
        ),
        Rule(
            id="STYLE005",
            title="Var Declaration",
            description="Use const or let instead of var.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.STYLE,
            pattern=re.compile(r"\bvar\s+\w+\s*="),
            suggestion="Use 'const' for constants, 'let' for variables.",
            languages=["javascript"],
        ),
        Rule(
            id="STYLE006",
            title="Any Type Usage",
            description="Using 'any' type defeats TypeScript's type safety.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.TYPE_SAFETY,
            pattern=re.compile(r":\s*any\b"),
            suggestion="Define a proper type or use 'unknown' with type guards.",
            languages=["typescript"],
        ),
    ]

    @classmethod
    def check(cls, code: str, filename: str, language: str) -> list[ReviewFinding]:
        """Check code against style rules."""
        findings = []
        lines = code.split("\n")

        for rule in cls.RULES:
            if language not in rule.languages:
                continue

            for i, line in enumerate(lines, 1):
                if rule.pattern.search(line):
                    findings.append(
                        ReviewFinding(
                            id=f"{rule.id}_{i}",
                            title=rule.title,
                            description=rule.description,
                            severity=rule.severity,
                            category=rule.category,
                            location=CodeLocation(
                                file=filename,
                                start_line=i,
                                end_line=i,
                            ),
                            suggestion=rule.suggestion,
                            code_before=line.strip()[:80],
                            auto_fixable=False,
                        )
                    )

        return findings


class BestPracticeRules:
    """Best practice rules."""

    RULES = [
        Rule(
            id="BP001",
            title="Missing Error Handling",
            description="Async operation without try-catch or .catch().",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(
                r"await\s+\w+\([^)]*\)\s*;"
            ),  # Simplified: matches await calls without try-catch context
            suggestion="Wrap await in try-catch or add .catch() handler.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="BP002",
            title="Empty Catch Block",
            description="Empty catch blocks hide errors silently.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"),
            suggestion="Log the error or handle it appropriately.",
            languages=["javascript", "typescript", "python"],
        ),
        Rule(
            id="BP003",
            title="Missing Return Type",
            description="Function is missing explicit return type.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.TYPE_SAFETY,
            pattern=re.compile(
                r"(?:async\s+)?function\s+\w+\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{"
            ),
            suggestion="Add explicit return type annotation.",
            languages=["typescript"],
        ),
        Rule(
            id="BP004",
            title="Unused Import",
            description="Imported module appears to be unused.",
            severity=ReviewSeverity.LOW,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(r"^import\s+(?:\{[^}]+\}|\w+)\s+from"),
            suggestion="Remove unused imports to reduce bundle size.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="BP005",
            title="Direct State Mutation",
            description="Directly mutating state instead of using setState.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.BUG,
            pattern=re.compile(r"(?:this\.)?state\.\w+\s*="),
            suggestion="Use setState() or dispatch actions to update state.",
            languages=["javascript", "typescript"],
        ),
        Rule(
            id="BP006",
            title="Missing Dependency Array",
            description="useEffect without dependency array runs on every render.",
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.PERFORMANCE,
            pattern=re.compile(r"useEffect\s*\(\s*\(\)\s*=>\s*\{[^}]*\}\s*\)(?!\s*,)"),
            suggestion="Add dependency array: useEffect(() => {}, [deps])",
            languages=["javascript", "typescript"],
        ),
    ]

    @classmethod
    def check(cls, code: str, filename: str, language: str) -> list[ReviewFinding]:
        """Check code against best practice rules."""
        findings = []

        for rule in cls.RULES:
            if language not in rule.languages:
                continue

            matches = list(rule.pattern.finditer(code))
            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                findings.append(
                    ReviewFinding(
                        id=f"{rule.id}_{line_num}",
                        title=rule.title,
                        description=rule.description,
                        severity=rule.severity,
                        category=rule.category,
                        location=CodeLocation(
                            file=filename,
                            start_line=line_num,
                            end_line=line_num,
                        ),
                        suggestion=rule.suggestion,
                        code_before=match.group()[:100],
                        auto_fixable=False,
                    )
                )

        return findings


class ArchitecturalRules:
    """
    Architectural rules (ARCH001-ARCH010) — regex-based quick checks.

    These complement the full AST-based analysis in agents/guardrails.py.
    The guardrails engine does deep analysis; these catch surface patterns
    during normal StaticAnalyzer runs.
    """

    RULES = [
        Rule(
            id="ARCH001",
            title="Business Logic in UI Component",
            description="fetch/axios/ORM calls should not appear in components/ directory.",
            severity=ReviewSeverity.CRITICAL,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(r'\bfetch\s*\(\s*["\']|axios\.\w+\s*\('),
            suggestion="Move API calls to hooks/ or lib/api/ directory.",
            languages=["javascript", "typescript", "jsx", "tsx"],
        ),
        Rule(
            id="ARCH003",
            title="File Exceeds 300 Lines",
            description="Large files are harder to maintain. Split into smaller modules.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(r"^.+$"),  # Placeholder — checked via line count
            suggestion="Split into smaller modules and re-export.",
            languages=["python", "javascript", "typescript", "jsx", "tsx"],
        ),
        Rule(
            id="ARCH005",
            title="Direct DOM Manipulation",
            description="document.getElementById/innerHTML should not be used in React.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.BEST_PRACTICE,
            pattern=re.compile(
                r"document\.(?:getElementById|querySelector|createElement)\s*\(|\.innerHTML\s*="
            ),
            suggestion="Use React refs (useRef) instead of direct DOM access.",
            languages=["javascript", "typescript", "jsx", "tsx"],
        ),
        Rule(
            id="ARCH007",
            title="Excessive Any Type Usage",
            description="Using 'any' type defeats TypeScript's type safety.",
            severity=ReviewSeverity.HIGH,
            category=ReviewCategory.TYPE_SAFETY,
            pattern=re.compile(r":\s*any\b"),
            suggestion="Define a proper interface/type or use 'unknown' with type guards.",
            languages=["typescript", "tsx"],
        ),
    ]

    @classmethod
    def check(cls, code: str, filename: str, language: str) -> list[ReviewFinding]:
        """Check code against architectural rules."""
        findings = []

        # ARCH003 — file length check (special case: not per-line pattern match)
        if language in ("python", "javascript", "typescript", "jsx", "tsx"):
            line_count = code.count("\n") + 1
            if line_count > 300:
                findings.append(
                    ReviewFinding(
                        id=f"ARCH003_{line_count}",
                        title="File Exceeds 300 Lines",
                        description=f"File has {line_count} lines (limit: 300). Split into smaller modules.",
                        severity=ReviewSeverity.HIGH,
                        category=ReviewCategory.BEST_PRACTICE,
                        location=CodeLocation(
                            file=filename, start_line=1, end_line=line_count
                        ),
                        suggestion="Split into smaller modules and re-export.",
                        auto_fixable=False,
                    )
                )

        # Standard per-line checks (skip ARCH003 placeholder)
        lines = code.split("\n")
        for rule in cls.RULES:
            if rule.id == "ARCH003":
                continue  # Handled above
            if language not in rule.languages:
                continue

            # ARCH001 — only flag if file is in components/ directory
            if rule.id == "ARCH001":
                filepath_lower = filename.replace("\\", "/").lower()
                # Match both "/components/" (mid-path) and "components/" (start of path)
                is_ui_path = any(
                    seg in filepath_lower
                    for seg in ("/components/", "/pages/", "/app/")
                ) or any(
                    filepath_lower.startswith(seg)
                    for seg in ("components/", "pages/", "app/")
                )
                if not is_ui_path:
                    continue

            for i, line in enumerate(lines, 1):
                if rule.pattern.search(line):
                    findings.append(
                        ReviewFinding(
                            id=f"{rule.id}_{i}",
                            title=rule.title,
                            description=rule.description,
                            severity=rule.severity,
                            category=rule.category,
                            location=CodeLocation(
                                file=filename,
                                start_line=i,
                                end_line=i,
                            ),
                            suggestion=rule.suggestion,
                            code_before=line.strip()[:80],
                            auto_fixable=False,
                        )
                    )

        return findings


class StaticAnalyzer:
    """
    Combines all static analysis rules.

    Run before AI review for quick feedback.
    """

    RULE_SETS = [
        SecurityRules,
        PerformanceRules,
        StyleRules,
        BestPracticeRules,
        ArchitecturalRules,
    ]

    @classmethod
    def analyze(cls, code: str, filename: str) -> list[ReviewFinding]:
        """Run all static analysis rules on code."""
        # Detect language
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "jsx",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".sql": "sql",
            ".html": "html",
        }
        ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
        language = ext_map.get(ext, "text")

        findings = []
        for rule_set in cls.RULE_SETS:
            findings.extend(rule_set.check(code, filename, language))

        # Deduplicate by location
        seen = set()
        unique_findings = []
        for f in findings:
            key = (f.location.file, f.location.start_line, f.title)
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        return unique_findings
