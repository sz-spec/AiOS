"""
Code Generation Tools
=====================
Tools for generating, parsing, and validating code output.
"""

import re
import subprocess
import tempfile
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from abc import ABC, abstractmethod

from langchain_core.tools import tool

from ai.llm.providers import LLM
from shared.errors import get_logger


class Language(Enum):
    """Supported programming languages."""

    HTML = "html"
    CSS = "css"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    REACT = "react"
    NODEJS = "nodejs"
    SQL = "sql"


@dataclass
class CodeBlock:
    """Represents a parsed code block."""

    language: Language
    content: str
    filename: Optional[str] = None
    line_start: int = 0
    line_end: int = 0

    def to_dict(self) -> Dict:
        return {
            "language": self.language.value,
            "content": self.content,
            "filename": self.filename,
            "lines": f"{self.line_start}-{self.line_end}",
        }


@dataclass
class ValidationResult:
    """Result of code validation."""

    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
        }


@dataclass
class GeneratedProject:
    """Represents a complete generated project."""

    files: Dict[str, str]  # filename -> content
    entry_point: str
    language: Language
    framework: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    validation: Optional[ValidationResult] = None

    def save(self, output_dir: Path) -> Path:
        """Save project to disk."""
        output_dir.mkdir(parents=True, exist_ok=True)

        for filename, content in self.files.items():
            file_path = output_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

        return output_dir


# =============================================================================
# Code Parsers
# =============================================================================


class CodeParser:
    """Parses code blocks from LLM output."""

    # Regex patterns for code blocks
    MARKDOWN_PATTERN = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)

    HTML_PATTERN = re.compile(r"<html.*?>(.*?)</html>", re.DOTALL | re.IGNORECASE)

    @classmethod
    def parse(cls, text: str) -> List[CodeBlock]:
        """Parse all code blocks from text."""
        blocks = []

        # Parse markdown code blocks
        for match in cls.MARKDOWN_PATTERN.finditer(text):
            lang_str = match.group(1) or "text"
            content = match.group(2).strip()

            try:
                language = cls._detect_language(lang_str, content)
                blocks.append(
                    CodeBlock(
                        language=language,
                        content=content,
                        line_start=text[: match.start()].count("\n"),
                        line_end=text[: match.end()].count("\n"),
                    )
                )
            except ValueError:
                pass

        # If no markdown blocks, try HTML pattern
        if not blocks:
            for match in cls.HTML_PATTERN.finditer(text):
                blocks.append(
                    CodeBlock(
                        language=Language.HTML, content=f"<html>{match.group(1)}</html>"
                    )
                )

        return blocks

    @classmethod
    def _detect_language(cls, lang_hint: str, content: str) -> Language:
        """Detect language from hint or content."""
        lang_map = {
            "html": Language.HTML,
            "css": Language.CSS,
            "js": Language.JAVASCRIPT,
            "javascript": Language.JAVASCRIPT,
            "ts": Language.TYPESCRIPT,
            "typescript": Language.TYPESCRIPT,
            "python": Language.PYTHON,
            "py": Language.PYTHON,
            "jsx": Language.REACT,
            "tsx": Language.REACT,
            "react": Language.REACT,
            "node": Language.NODEJS,
            "sql": Language.SQL,
        }

        lang = lang_map.get(lang_hint.lower())
        if lang:
            return lang

        # Try to detect from content
        if "<html" in content.lower() or "<!doctype" in content.lower():
            return Language.HTML
        elif "import React" in content or "from 'react'" in content:
            return Language.REACT
        elif content.strip().startswith("def ") or content.strip().startswith(
            "import "
        ):
            return Language.PYTHON

        return Language.JAVASCRIPT  # Default


# =============================================================================
# Validators
# =============================================================================


class CodeValidator(ABC):
    """Abstract base class for code validators."""

    @abstractmethod
    def validate(self, code: str) -> ValidationResult:
        """Validate code and return results."""
        pass


class HTMLValidator(CodeValidator):
    """HTML syntax validator."""

    def validate(self, code: str) -> ValidationResult:
        errors = []
        warnings = []

        # Basic tag matching
        open_tags = re.findall(r"<(\w+)[^>]*(?<!/)>", code)
        close_tags = re.findall(r"</(\w+)>", code)

        # Void elements that don't need closing
        void_elements = {
            "br",
            "hr",
            "img",
            "input",
            "meta",
            "link",
            "area",
            "base",
            "col",
        }

        open_tags = [t.lower() for t in open_tags if t.lower() not in void_elements]
        close_tags = [t.lower() for t in close_tags]

        # Check for mismatched tags
        if len(open_tags) != len(close_tags):
            errors.append(
                f"Mismatched tags: {len(open_tags)} opening, {len(close_tags)} closing"
            )

        # Check for DOCTYPE
        if "<!doctype" not in code.lower():
            warnings.append("Missing DOCTYPE declaration")

        # Check for common issues
        if "<title>" not in code.lower():
            warnings.append("Missing <title> tag")

        return ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings
        )


class JavaScriptValidator(CodeValidator):
    """JavaScript syntax validator using node."""

    def validate(self, code: str) -> ValidationResult:
        errors = []
        warnings = []

        # Try to parse with node
        try:
            with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
                f.write(code)
                f.flush()

                result = subprocess.run(
                    ["node", "--check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode != 0:
                    errors.append(result.stderr.strip())
        except subprocess.TimeoutExpired:
            warnings.append("Validation timed out")
        except FileNotFoundError:
            warnings.append("Node.js not available for validation")
        except Exception as e:
            warnings.append(f"Validation error: {e}")

        # Basic static checks
        if "eval(" in code:
            warnings.append("Usage of eval() is not recommended")
        if "document.write(" in code:
            warnings.append("document.write() is deprecated")

        return ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings
        )


class PythonValidator(CodeValidator):
    """Python syntax validator."""

    def validate(self, code: str) -> ValidationResult:
        errors = []
        warnings = []

        # Try to compile
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            errors.append(f"Syntax error at line {e.lineno}: {e.msg}")

        # Check for common issues
        if "import *" in code:
            warnings.append("Avoid 'import *' - use explicit imports")
        if "except:" in code and "except Exception" not in code:
            warnings.append(
                "Bare 'except:' catches all exceptions including KeyboardInterrupt"
            )

        return ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings
        )


class ValidationEngine:
    """Unified validation engine."""

    VALIDATORS = {
        Language.HTML: HTMLValidator(),
        Language.JAVASCRIPT: JavaScriptValidator(),
        Language.TYPESCRIPT: JavaScriptValidator(),  # Basic check
        Language.PYTHON: PythonValidator(),
    }

    @classmethod
    def validate(cls, code_block: CodeBlock) -> ValidationResult:
        """Validate a code block."""
        validator = cls.VALIDATORS.get(code_block.language)

        if validator:
            return validator.validate(code_block.content)

        # Default: assume valid if no validator
        return ValidationResult(valid=True, warnings=["No validator available"])

    @classmethod
    def validate_project(cls, project: GeneratedProject) -> ValidationResult:
        """Validate entire project."""
        all_errors = []
        all_warnings = []

        for filename, content in project.files.items():
            # Detect language from extension
            ext = Path(filename).suffix.lower()
            lang_map = {
                ".html": Language.HTML,
                ".css": Language.CSS,
                ".js": Language.JAVASCRIPT,
                ".jsx": Language.REACT,
                ".ts": Language.TYPESCRIPT,
                ".tsx": Language.REACT,
                ".py": Language.PYTHON,
            }

            language = lang_map.get(ext)
            if language:
                block = CodeBlock(language=language, content=content, filename=filename)
                result = cls.validate(block)

                for err in result.errors:
                    all_errors.append(f"{filename}: {err}")
                for warn in result.warnings:
                    all_warnings.append(f"{filename}: {warn}")

        return ValidationResult(
            valid=len(all_errors) == 0, errors=all_errors, warnings=all_warnings
        )


# =============================================================================
# Code Generator
# =============================================================================


class CodeGenerator:
    """
    Main code generator using LLM.

    Usage:
        generator = CodeGenerator()
        project = generator.generate("Build a todo app with React")
    """

    SYSTEM_PROMPT = """You are an expert full-stack developer. Generate clean, well-structured, production-ready code.

Rules:
1. Always use modern best practices
2. Include proper error handling
3. Add helpful comments
4. Use semantic HTML
5. Follow accessibility guidelines
6. Structure code for maintainability

Output format:
- Wrap each file in markdown code blocks with the filename as a comment at the top
- Include all necessary files for a complete, working application
- List dependencies at the end"""

    def __init__(self, llm: LLM = None, max_iterations: int = 3):
        self.llm = llm or LLM()
        self.logger = get_logger()
        self.max_iterations = max_iterations

    def generate(
        self,
        description: str,
        target_language: Language = Language.HTML,
        framework: str = None,
        include_tests: bool = False,
    ) -> GeneratedProject:
        """
        Generate a complete project from description.

        Args:
            description: Natural language description of the project
            target_language: Primary language/framework
            framework: Specific framework (e.g., "react", "vue", "express")
            include_tests: Whether to include unit tests

        Returns:
            GeneratedProject with all files
        """

        # Build prompt
        prompt = self._build_prompt(
            description, target_language, framework, include_tests
        )

        # Generate initial code
        response = self.llm.generate(prompt, system=self.SYSTEM_PROMPT)

        # Parse output
        files = self._parse_output(response.content, target_language)

        # Create project
        project = GeneratedProject(
            files=files,
            entry_point=self._detect_entry_point(files),
            language=target_language,
            framework=framework,
            dependencies=self._extract_dependencies(response.content),
        )

        # Validate and iterate if needed
        project = self._validate_and_fix(project)

        return project

    def _build_prompt(
        self, description: str, language: Language, framework: str, include_tests: bool
    ) -> str:
        """Build the generation prompt."""

        prompt_parts = [f"Create: {description}"]

        # Add language/framework requirements
        if framework:
            prompt_parts.append(f"Framework: {framework}")
        else:
            prompt_parts.append(f"Language: {language.value}")

        # Add test requirement
        if include_tests:
            prompt_parts.append("Include unit tests for all components")

        # Add structure requirements based on project type
        if language == Language.REACT:
            prompt_parts.append("""
Structure:
- src/App.jsx - Main component
- src/components/ - Reusable components
- src/index.js - Entry point
- public/index.html - HTML template
- package.json - Dependencies""")

        elif language == Language.NODEJS:
            prompt_parts.append("""
Structure:
- src/index.js - Entry point
- src/routes/ - API routes
- src/middleware/ - Middleware
- src/utils/ - Utilities
- package.json - Dependencies""")

        return "\n".join(prompt_parts)

    def _parse_output(self, output: str, default_language: Language) -> Dict[str, str]:
        """Parse LLM output into file dictionary."""

        files = {}

        # Pattern to match file blocks
        file_pattern = re.compile(
            r"(?:#+\s*)?(?:File:\s*)?`?([^\n`]+\.[a-z]+)`?\n```(?:\w+)?\n(.*?)```",
            re.DOTALL,
        )

        for match in file_pattern.finditer(output):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            files[filename] = content

        # If no files found, try simpler parsing
        if not files:
            blocks = CodeParser.parse(output)
            if blocks:
                # Use first block as index.html or main file
                ext_map = {
                    Language.HTML: "index.html",
                    Language.JAVASCRIPT: "script.js",
                    Language.PYTHON: "main.py",
                    Language.REACT: "App.jsx",
                }
                filename = ext_map.get(default_language, "index.html")
                files[filename] = blocks[0].content

        return files

    def _detect_entry_point(self, files: Dict[str, str]) -> str:
        """Detect the entry point file."""

        priority = [
            "index.html",
            "public/index.html",
            "src/index.js",
            "src/main.js",
            "src/App.jsx",
            "main.py",
            "app.py",
        ]

        for entry in priority:
            if entry in files:
                return entry

        return list(files.keys())[0] if files else "index.html"

    def _extract_dependencies(self, output: str) -> List[str]:
        """Extract dependencies from output."""

        deps = []

        # Look for package.json
        pkg_match = re.search(
            r'```json\n({.*?"dependencies".*?})\n```', output, re.DOTALL
        )
        if pkg_match:
            try:
                pkg = json.loads(pkg_match.group(1))
                deps = list(pkg.get("dependencies", {}).keys())
            except json.JSONDecodeError:
                pass

        # Look for requirements.txt content
        req_match = re.search(
            r"```(?:txt|requirements)?\n([\w\->=<\.\s]+)\n```", output
        )
        if req_match:
            deps.extend(
                [
                    line.split("==")[0].split(">=")[0].strip()
                    for line in req_match.group(1).split("\n")
                    if line.strip()
                ]
            )

        return deps

    def _validate_and_fix(self, project: GeneratedProject) -> GeneratedProject:
        """Validate project and attempt to fix issues."""

        for iteration in range(self.max_iterations):
            # Validate
            result = ValidationEngine.validate_project(project)
            project.validation = result

            if result.valid:
                self.logger.info(
                    f"Project validated successfully (iteration {iteration + 1})"
                )
                return project

            self.logger.warning(
                f"Validation failed, attempting fix (iteration {iteration + 1})"
            )

            # Try to fix errors
            project = self._fix_errors(project, result)

        return project

    def _fix_errors(
        self, project: GeneratedProject, validation: ValidationResult
    ) -> GeneratedProject:
        """Attempt to fix validation errors."""

        if not validation.errors:
            return project

        # Build fix prompt
        error_list = "\n".join(f"- {err}" for err in validation.errors)

        fix_prompt = f"""Fix the following errors in the code:

Errors:
{error_list}

Current files:
"""
        for filename, content in project.files.items():
            fix_prompt += f"\n--- {filename} ---\n```\n{content}\n```\n"

        fix_prompt += "\nProvide the corrected files in the same format."

        # Generate fix
        response = self.llm.generate(fix_prompt, system=self.SYSTEM_PROMPT)

        # Parse fixed output
        fixed_files = self._parse_output(response.content, project.language)

        # Merge with existing (keep files not in fix)
        for filename, content in fixed_files.items():
            project.files[filename] = content

        return project


# =============================================================================
# LangChain Tools
# =============================================================================


@tool
def generate_html_code(description: str) -> str:
    """Generate HTML/CSS/JS code from a description.

    Args:
        description: Natural language description of what to build

    Returns:
        Generated HTML code
    """
    generator = CodeGenerator()
    project = generator.generate(description, target_language=Language.HTML)
    return project.files.get("index.html", "")


@tool
def generate_react_component(description: str) -> str:
    """Generate a React component from a description.

    Args:
        description: Description of the component to build

    Returns:
        Generated React component code
    """
    generator = CodeGenerator()
    project = generator.generate(
        description, target_language=Language.REACT, framework="react"
    )
    return project.files.get("App.jsx", project.files.get("src/App.jsx", ""))


@tool
def generate_api_endpoint(description: str) -> str:
    """Generate Node.js API endpoint from a description.

    Args:
        description: Description of the API to build

    Returns:
        Generated Node.js/Express code
    """
    generator = CodeGenerator()
    project = generator.generate(
        description, target_language=Language.NODEJS, framework="express"
    )
    return project.files.get("src/index.js", "")


@tool
def validate_code(code: str, language: str) -> str:
    """Validate code syntax and best practices.

    Args:
        code: Code to validate
        language: Programming language (html, javascript, python)

    Returns:
        Validation results as JSON string
    """
    lang_map = {
        "html": Language.HTML,
        "javascript": Language.JAVASCRIPT,
        "js": Language.JAVASCRIPT,
        "python": Language.PYTHON,
        "py": Language.PYTHON,
    }

    lang = lang_map.get(language.lower(), Language.JAVASCRIPT)
    block = CodeBlock(language=lang, content=code)
    result = ValidationEngine.validate(block)

    return json.dumps(result.to_dict(), indent=2)
