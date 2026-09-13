"""
AI App Builder - Main Orchestrator
===================================
High-level interface for the complete AI application builder system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from .src.config import Config, get_config
from .src.errors import AIBuilderError, setup_logging
from .src.cache import get_cache
from .ai.llm.providers import (
    LLM,
)  # W2.1a: src/llm.py removed; canonical is ai.llm.providers
from .src.code_generator import (
    CodeGenerator,
    Language,
    GeneratedProject,
    ValidationEngine,
    ValidationResult,
)
from .src.git_integration import GitManager
from .ai.agents.multi_agent import MultiAgentBuilder


class BuildMode(Enum):
    """Build mode options."""

    SIMPLE = "simple"  # Single-shot generation
    ITERATIVE = "iterative"  # With self-correction
    MULTI_AGENT = "multi_agent"  # Full multi-agent pipeline


@dataclass
class BuildRequest:
    """Request for building an application."""

    description: str
    language: Language = Language.HTML
    framework: Optional[str] = None
    mode: BuildMode = BuildMode.ITERATIVE
    include_tests: bool = False
    save_to_git: bool = True
    project_name: Optional[str] = None
    output_dir: Optional[Path] = None


@dataclass
class BuildResult:
    """Result of a build operation."""

    success: bool
    project: Optional[GeneratedProject]
    validation: Optional[ValidationResult]
    git_info: Optional[Dict[str, Any]] = None
    duration_seconds: float = 0
    iterations: int = 1
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "files": list(self.project.files.keys()) if self.project else [],
            "validation": self.validation.to_dict() if self.validation else None,
            "git": self.git_info,
            "duration": self.duration_seconds,
            "iterations": self.iterations,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class AIAppBuilder:
    """
    Main interface for the AI Application Builder.

    This is the primary class you'll use to generate applications from
    natural language descriptions.

    Usage:
        # Simple usage
        builder = AIAppBuilder()
        result = builder.build("Create a todo app with React")

        # With options
        result = builder.build(
            "Build an e-commerce product page",
            language=Language.REACT,
            framework="react",
            include_tests=True
        )

        # Multi-agent mode for complex projects
        result = builder.build(
            "Create a full-stack blog with authentication",
            mode=BuildMode.MULTI_AGENT
        )

        # Interactive/streaming build
        for update in builder.build_stream("Create a dashboard"):
            print(f"Phase: {update['phase']}, Progress: {update['progress']}%")
    """

    def __init__(self, config: Config = None, log_level: str = "INFO"):
        """
        Initialize the AI App Builder.

        Args:
            config: Optional configuration object
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        self.config = config or get_config()
        self.logger = setup_logging(log_dir=self.config.logs_dir, level=log_level)

        # Initialize components
        self.llm = LLM(cache_enabled=self.config.cache.enabled, fallback_enabled=True)

        self.code_generator = CodeGenerator(
            llm=self.llm, max_iterations=self.config.max_iterations
        )

        self.git_manager = GitManager(
            base_dir=self.config.output_dir, config=self.config.git
        )

        self.multi_agent_builder = None  # Lazy init for complex builds

        self.logger.info("AI App Builder initialized")

    def build(
        self,
        description: str,
        language: Language = Language.HTML,
        framework: str = None,
        mode: BuildMode = BuildMode.ITERATIVE,
        include_tests: bool = False,
        save_to_git: bool = True,
        project_name: str = None,
        output_dir: Path = None,
        on_progress: Callable[[Dict], None] = None,
    ) -> BuildResult:
        """
        Build an application from a natural language description.

        Args:
            description: What to build (natural language)
            language: Target language/framework type
            framework: Specific framework (react, vue, express, etc.)
            mode: Build mode (simple, iterative, multi_agent)
            include_tests: Whether to generate tests
            save_to_git: Whether to save with Git versioning
            project_name: Custom project name
            output_dir: Custom output directory
            on_progress: Callback for progress updates

        Returns:
            BuildResult with generated project and metadata

        Examples:
            >>> builder = AIAppBuilder()

            # Simple HTML page
            >>> result = builder.build("Create a landing page for a coffee shop")

            # React app
            >>> result = builder.build(
            ...     "Build a kanban board",
            ...     language=Language.REACT,
            ...     framework="react"
            ... )

            # Full-stack with tests
            >>> result = builder.build(
            ...     "Create a REST API for a blog with user authentication",
            ...     mode=BuildMode.MULTI_AGENT,
            ...     include_tests=True
            ... )
        """

        start_time = datetime.now()
        errors = []
        warnings = []
        iterations = 1

        self.logger.info(f"Starting build: {description[:100]}...")
        self.logger.info(f"Mode: {mode.value}, Language: {language.value}")

        try:
            # Select build strategy
            if mode == BuildMode.MULTI_AGENT:
                project = self._build_multi_agent(
                    description, language, framework, include_tests, on_progress
                )
            elif mode == BuildMode.ITERATIVE:
                project, iterations = self._build_iterative(
                    description, language, framework, include_tests, on_progress
                )
            else:
                project = self._build_simple(
                    description, language, framework, on_progress
                )

            # Validate
            validation = ValidationEngine.validate_project(project)
            warnings.extend(validation.warnings)

            if not validation.valid:
                errors.extend(validation.errors)

            # Save with Git if requested
            git_info = None
            if save_to_git and self.config.git.enabled:
                try:
                    git_info = self.git_manager.save_project(
                        project,
                        message=f"AI generated: {description[:50]}",
                        project_name=project_name,
                    )
                except Exception as e:
                    warnings.append(f"Git save failed: {e}")

            # Save to custom output dir if specified
            if output_dir:
                output_path = Path(output_dir)
                project.save(output_path)

            duration = (datetime.now() - start_time).total_seconds()

            return BuildResult(
                success=validation.valid,
                project=project,
                validation=validation,
                git_info=git_info,
                duration_seconds=duration,
                iterations=iterations,
                errors=errors,
                warnings=warnings,
            )

        except AIBuilderError as e:
            self.logger.error(f"Build failed: {e}")
            errors.append(str(e))

            return BuildResult(
                success=False,
                project=None,
                validation=None,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                errors=errors,
                warnings=warnings,
            )

        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            errors.append(f"Unexpected error: {e}")

            return BuildResult(
                success=False,
                project=None,
                validation=None,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                errors=errors,
                warnings=warnings,
            )

    def _build_simple(
        self,
        description: str,
        language: Language,
        framework: str,
        on_progress: Callable,
    ) -> GeneratedProject:
        """Simple single-shot generation."""

        if on_progress:
            on_progress({"phase": "generating", "progress": 0})

        project = self.code_generator.generate(
            description,
            target_language=language,
            framework=framework,
            include_tests=False,
        )

        if on_progress:
            on_progress({"phase": "complete", "progress": 100})

        return project

    def _build_iterative(
        self,
        description: str,
        language: Language,
        framework: str,
        include_tests: bool,
        on_progress: Callable,
    ) -> tuple[GeneratedProject, int]:
        """Iterative build with self-correction."""

        iterations = 0
        max_iterations = self.config.max_iterations

        for i in range(max_iterations):
            iterations = i + 1

            if on_progress:
                on_progress(
                    {
                        "phase": "generating",
                        "iteration": iterations,
                        "progress": int((i / max_iterations) * 100),
                    }
                )

            project = self.code_generator.generate(
                description,
                target_language=language,
                framework=framework,
                include_tests=include_tests,
            )

            # Validate
            validation = ValidationEngine.validate_project(project)

            if validation.valid:
                break

            self.logger.info(
                f"Iteration {iterations}: {len(validation.errors)} errors, retrying..."
            )

            # Update description with errors for next iteration
            description = f"{description}\n\nFix these errors:\n" + "\n".join(
                validation.errors
            )

        if on_progress:
            on_progress({"phase": "complete", "progress": 100})

        return project, iterations

    def _build_multi_agent(
        self,
        description: str,
        language: Language,
        framework: str,
        include_tests: bool,
        on_progress: Callable,
    ) -> GeneratedProject:
        """Multi-agent collaborative build."""

        # Initialize multi-agent builder if needed
        if self.multi_agent_builder is None:
            self.multi_agent_builder = MultiAgentBuilder(self.llm)

        if on_progress:
            on_progress({"phase": "architect", "progress": 0})

        # Run multi-agent workflow
        project = self.multi_agent_builder.build(
            description,
            config={
                "language": language.value,
                "framework": framework,
                "include_tests": include_tests,
            },
        )

        if on_progress:
            on_progress({"phase": "complete", "progress": 100})

        return project

    def build_stream(
        self, description: str, mode: BuildMode = BuildMode.MULTI_AGENT, **kwargs
    ) -> Generator[Dict[str, Any], None, BuildResult]:
        """
        Stream build progress.

        Yields progress updates during build, returns final result.

        Usage:
            for update in builder.build_stream("Create a dashboard"):
                print(f"Phase: {update['phase']}")
        """

        result_holder = {"result": None}

        def progress_callback(update: Dict):
            pass  # Updates are yielded directly

        if mode == BuildMode.MULTI_AGENT:
            if self.multi_agent_builder is None:
                self.multi_agent_builder = MultiAgentBuilder(self.llm)

            for update in self.multi_agent_builder.build_stream(description):
                yield update

            # Get final result
            result_holder["result"] = self.build(description, mode=mode, **kwargs)
        else:
            result_holder["result"] = self.build(
                description, mode=mode, on_progress=progress_callback, **kwargs
            )
            yield {"phase": "complete", "progress": 100}

        return result_holder["result"]

    # Convenience methods

    def build_html(self, description: str, **kwargs) -> BuildResult:
        """Build a simple HTML page."""
        return self.build(description, language=Language.HTML, **kwargs)

    def build_react(self, description: str, **kwargs) -> BuildResult:
        """Build a React application."""
        return self.build(
            description, language=Language.REACT, framework="react", **kwargs
        )

    def build_api(self, description: str, **kwargs) -> BuildResult:
        """Build a backend API."""
        return self.build(
            description, language=Language.NODEJS, framework="express", **kwargs
        )

    def build_fullstack(self, description: str, **kwargs) -> BuildResult:
        """Build a full-stack application using multi-agent mode."""
        return self.build(
            description, mode=BuildMode.MULTI_AGENT, include_tests=True, **kwargs
        )

    # Project management

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all generated projects."""
        return self.git_manager.list_projects()

    def get_project_history(self, project_name: str) -> List[Dict]:
        """Get commit history for a project."""
        commits = self.git_manager.get_project_history(project_name)
        return [c.__dict__ for c in commits]

    def export_to_github(
        self, project_name: str, repo_name: str, github_token: str, private: bool = True
    ) -> Dict[str, Any]:
        """Export project to GitHub."""
        return self.git_manager.export_to_github(
            project_name, repo_name, github_token, private
        )

    # Statistics and info

    def get_stats(self) -> Dict[str, Any]:
        """Get builder statistics."""
        cache_stats = get_cache().stats()
        projects = self.list_projects()

        return {
            "cache": cache_stats,
            "projects_count": len(projects),
            "llm_info": self.llm.get_info(),
            "config": {
                "max_iterations": self.config.max_iterations,
                "git_enabled": self.config.git.enabled,
                "cache_enabled": self.config.cache.enabled,
            },
        }

    def clear_cache(self) -> bool:
        """Clear the response cache."""
        return get_cache().clear()


# =============================================================================
# CLI Interface
# =============================================================================


def main():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Application Builder - Generate apps from descriptions"
    )

    parser.add_argument(
        "description", help="Natural language description of what to build"
    )

    parser.add_argument(
        "--mode",
        choices=["simple", "iterative", "multi_agent"],
        default="iterative",
        help="Build mode",
    )

    parser.add_argument(
        "--language",
        choices=["html", "react", "nodejs", "python"],
        default="html",
        help="Target language/framework",
    )

    parser.add_argument("--output", "-o", help="Output directory")

    parser.add_argument("--tests", action="store_true", help="Generate tests")

    parser.add_argument(
        "--no-git", action="store_true", help="Don't use Git versioning"
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Map language strings to enums
    language_map = {
        "html": Language.HTML,
        "react": Language.REACT,
        "nodejs": Language.NODEJS,
        "python": Language.PYTHON,
    }

    mode_map = {
        "simple": BuildMode.SIMPLE,
        "iterative": BuildMode.ITERATIVE,
        "multi_agent": BuildMode.MULTI_AGENT,
    }

    # Initialize builder
    builder = AIAppBuilder(log_level="DEBUG" if args.verbose else "INFO")

    # Run build
    print(f"🚀 Building: {args.description}")
    print(f"   Mode: {args.mode}, Language: {args.language}")
    print()

    result = builder.build(
        args.description,
        language=language_map[args.language],
        mode=mode_map[args.mode],
        include_tests=args.tests,
        save_to_git=not args.no_git,
        output_dir=Path(args.output) if args.output else None,
    )

    # Print results
    if result.success:
        print("✅ Build successful!")
        print(f"   Files: {len(result.project.files)}")
        for filename in result.project.files:
            print(f"      - {filename}")
        print(f"   Duration: {result.duration_seconds:.1f}s")
        print(f"   Iterations: {result.iterations}")

        if result.git_info:
            print(f"   Git: {result.git_info.get('path')}")
    else:
        print("❌ Build failed!")
        for error in result.errors:
            print(f"   Error: {error}")

    if result.warnings:
        print("\n⚠️ Warnings:")
        for warning in result.warnings:
            print(f"   - {warning}")

    return 0 if result.success else 1


if __name__ == "__main__":
    exit(main())
