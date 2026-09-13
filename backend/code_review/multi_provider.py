"""
Multi-Provider AI Code Review Service

Based on Shakudo blog, Reddit (r/ChatGPTCoding, r/nocode) - Dec 2025:
- GitHub Copilot SDK integration (90% bug detection accuracy)
- Claude/Sonnet for deep analysis
- Ollama for local/offline reviews
- 40% time reduction vs manual review

Supports:
- Multiple AI providers (Copilot, Claude, Ollama, OpenAI)
- Automatic provider fallback
- Parallel reviews from multiple providers
- Consensus-based findings
"""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import httpx

from src.efficiency.factory import get_llm_for_task


class ReviewProvider(str, Enum):
    """Available review providers."""

    COPILOT = "copilot"  # GitHub Copilot SDK - best for bugs
    CLAUDE = "claude"  # Claude - best for security/deep analysis
    OLLAMA = "ollama"  # Local LLM - offline/privacy
    OPENAI = "openai"  # GPT-4 - general purpose
    CONSENSUS = "consensus"  # Aggregate from multiple providers


@dataclass
class ProviderConfig:
    """Configuration for a review provider."""

    name: ReviewProvider
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    enabled: bool = True
    timeout: int = 60
    priority: int = 1  # Lower = higher priority


@dataclass
class ReviewContext:
    """Context for code review."""

    project_id: str
    files: dict[str, str]
    diff: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    base_branch: str = "main"
    head_branch: Optional[str] = None
    framework: Optional[str] = None
    language: Optional[str] = None
    custom_rules: list[str] = field(default_factory=list)


# ============================================
# Provider Interfaces
# ============================================


class BaseReviewProvider(ABC):
    """Base class for review providers."""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.name = config.name

    @abstractmethod
    async def review(self, context: ReviewContext) -> dict:
        """Perform code review."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if provider is available."""
        pass


class CopilotReviewProvider(BaseReviewProvider):
    """
    GitHub Copilot SDK Integration.

    Based on forum recommendations:
    - 90% accuracy for bug detection
    - Best integration with Git workflows
    - Uses Copilot API for code analysis
    """

    REVIEW_PROMPT = """You are a code reviewer using GitHub Copilot. Analyze this code for:

1. BUGS: Logic errors, null references, off-by-one, race conditions
2. SECURITY: SQL injection, XSS, authentication issues, secrets
3. PERFORMANCE: N+1 queries, memory leaks, inefficient algorithms
4. BEST PRACTICES: Code style, naming, documentation

Respond with JSON array of findings:
[{
  "title": "Issue title",
  "description": "Detailed description",
  "severity": "critical|high|medium|low|info",
  "category": "bug|security|performance|style|documentation",
  "file": "filename",
  "line": line_number,
  "suggestion": "How to fix",
  "code_fix": "Fixed code if applicable"
}]

Code to review:
{code}"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key or os.getenv("GITHUB_TOKEN")
        self.base_url = config.base_url or "https://api.github.com"
        # Copilot uses GitHub's API with special headers
        self.copilot_url = "https://api.githubcopilot.com"

    async def review(self, context: ReviewContext) -> dict:
        """Review code using GitHub Copilot."""
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            # Prepare code content
            code_content = self._prepare_code(context)

            try:
                # Use Copilot's completion API for review
                response = await client.post(
                    f"{self.copilot_url}/v1/engines/copilot-codex/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Editor-Version": "Neovim/0.9.0",
                        "Editor-Plugin-Version": "copilot.vim/1.0.0",
                    },
                    json={
                        "prompt": self.REVIEW_PROMPT.format(code=code_content),
                        "max_tokens": 4096,
                        "temperature": 0.1,
                        "top_p": 0.95,
                        "n": 1,
                        "stream": False,
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data, context)
                else:
                    # Fallback to standard GitHub API review
                    return await self._github_review_fallback(context, client)

            except Exception as e:
                return {
                    "provider": self.name.value,
                    "error": str(e),
                    "findings": [],
                }

    async def _github_review_fallback(
        self,
        context: ReviewContext,
        client: httpx.AsyncClient,
    ) -> dict:
        """Fallback using GitHub's code scanning suggestions."""
        # Use GitHub's built-in code analysis if Copilot API unavailable
        findings = []

        # Check for common patterns using regex-based analysis
        for filename, code in context.files.items():
            findings.extend(self._analyze_code_patterns(filename, code))

        return {
            "provider": self.name.value,
            "findings": findings,
            "fallback": True,
        }

    def _analyze_code_patterns(self, filename: str, code: str) -> list[dict]:
        """Analyze code for common issues using patterns."""
        import re

        findings = []
        lines = code.split("\n")

        patterns = [
            # Security patterns
            (
                r"eval\s*\(",
                "Eval Usage",
                "critical",
                "security",
                "Avoid eval() - security risk",
            ),
            (
                r"innerHTML\s*=",
                "XSS Risk",
                "high",
                "security",
                "Use textContent or sanitize input",
            ),
            (
                r'password\s*=\s*["\'][^"\']+["\']',
                "Hardcoded Password",
                "critical",
                "security",
                "Use environment variables",
            ),
            (
                r'api[_-]?key\s*=\s*["\'][^"\']+["\']',
                "Hardcoded API Key",
                "critical",
                "security",
                "Use environment variables",
            ),
            # Bug patterns
            (
                r"==\s*null(?!\s*\|\|)",
                "Null Check Issue",
                "medium",
                "bug",
                "Use === null or optional chaining",
            ),
            (
                r"catch\s*\([^)]*\)\s*\{\s*\}",
                "Empty Catch Block",
                "medium",
                "bug",
                "Handle or log the error",
            ),
            # Performance patterns
            (
                r"for\s*\([^)]+\)\s*\{[^}]*await",
                "Await in Loop",
                "high",
                "performance",
                "Use Promise.all for parallel execution",
            ),
        ]

        for i, line in enumerate(lines, 1):
            for pattern, title, severity, category, suggestion in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(
                        {
                            "title": title,
                            "description": f"Found potential issue at line {i}",
                            "severity": severity,
                            "category": category,
                            "file": filename,
                            "line": i,
                            "suggestion": suggestion,
                        }
                    )

        return findings

    def _prepare_code(self, context: ReviewContext) -> str:
        """Prepare code content for review."""
        if context.diff:
            return f"Git Diff:\n{context.diff}"

        parts = []
        for filename, code in context.files.items():
            parts.append(f"// File: {filename}\n{code}")
        return "\n\n".join(parts)

    def _parse_response(self, data: dict, context: ReviewContext) -> dict:
        """Parse Copilot response."""
        try:
            text = data.get("choices", [{}])[0].get("text", "[]")
            findings = json.loads(text)
            return {
                "provider": self.name.value,
                "findings": findings,
            }
        except json.JSONDecodeError:
            return {
                "provider": self.name.value,
                "findings": [],
                "raw_response": data,
            }

    async def health_check(self) -> bool:
        """Check if Copilot is available."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/user",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return response.status_code == 200
        except Exception:
            return False


class ClaudeReviewProvider(BaseReviewProvider):
    """
    Claude/Anthropic Integration.

    Based on forum recommendations:
    - Best for deep security analysis
    - Excellent at understanding complex code patterns
    - Good for explaining issues
    """

    REVIEW_PROMPT = """You are an expert code reviewer. Perform a thorough review of this code.

Focus areas:
1. SECURITY (CRITICAL): Vulnerabilities, injection attacks, authentication issues
2. BUGS: Logic errors, edge cases, error handling
3. PERFORMANCE: Inefficiencies, memory issues, scalability
4. CODE QUALITY: Maintainability, readability, best practices
5. DOCUMENTATION: Missing or unclear documentation

Context:
- Framework: {framework}
- Language: {language}
{custom_rules}

Respond ONLY with a JSON array of findings:
[{{
  "title": "Brief issue title",
  "description": "Detailed explanation of the issue",
  "severity": "critical|high|medium|low|info",
  "category": "security|bug|performance|style|documentation|best_practice",
  "file": "filename.ext",
  "line": line_number,
  "end_line": end_line_number,
  "suggestion": "How to fix this issue",
  "code_fix": "The corrected code",
  "references": ["optional URLs to documentation"],
  "confidence": 0.95
}}]

If no issues found, return empty array: []

Code to review:
{code}"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.model = config.model or "claude-sonnet-4-20250514"
        self.llm = get_llm_for_task("reviewer", complexity=7)

    async def review(self, context: ReviewContext) -> dict:
        """Review code using Factory-selected LLM (model-agnostic)."""
        try:
            from langchain_core.messages import HumanMessage

            # Prepare code and context
            code_content = self._prepare_code(context)
            custom_rules = ""
            if context.custom_rules:
                custom_rules = "Custom rules:\n" + "\n".join(
                    f"- {r}" for r in context.custom_rules
                )

            prompt = self.REVIEW_PROMPT.format(
                framework=context.framework or "Not specified",
                language=context.language or "Auto-detect",
                custom_rules=custom_rules,
                code=code_content,
            )

            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            return self._parse_response(response.content)

        except Exception as e:
            return {
                "provider": self.name.value,
                "error": str(e),
                "findings": [],
            }

    def _prepare_code(self, context: ReviewContext) -> str:
        """Prepare code for review."""
        if context.diff:
            return f"Git Diff:\n```diff\n{context.diff}\n```"

        parts = []
        for filename, code in context.files.items():
            lang = self._detect_language(filename)
            parts.append(f"File: {filename}\n```{lang}\n{code}\n```")
        return "\n\n".join(parts)

    def _detect_language(self, filename: str) -> str:
        """Detect language from filename."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".jsx": "jsx",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
            ".php": "php",
        }
        ext = "." + filename.split(".")[-1] if "." in filename else ""
        return ext_map.get(ext, "")

    def _parse_response(self, text: str) -> dict:
        """Parse Claude response."""
        try:
            # Try to extract JSON from response
            import re

            match = re.search(r"\[[\s\S]*\]", text)
            if match:
                findings = json.loads(match.group())
                return {
                    "provider": self.name.value,
                    "findings": findings,
                }
        except json.JSONDecodeError:
            pass

        return {
            "provider": self.name.value,
            "findings": [],
            "raw_response": text,
        }

    async def health_check(self) -> bool:
        """Check if the review LLM is available."""
        try:
            from langchain_core.messages import HumanMessage

            response = await self.llm.ainvoke([HumanMessage(content="ping")])
            return bool(response.content)
        except Exception:
            return False


class OllamaReviewProvider(BaseReviewProvider):
    """
    Ollama Local LLM Integration.

    Based on forum recommendations:
    - Best for privacy/offline usage
    - Good for quick reviews
    - Free and self-hosted
    """

    REVIEW_PROMPT = """Review this code for bugs, security issues, and improvements.

Respond with JSON array:
[{{"title": "issue", "description": "details", "severity": "high|medium|low", "category": "bug|security|style", "file": "name", "line": 1, "suggestion": "fix"}}]

Code:
{code}"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.base_url = config.base_url or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
        self.model = config.model or "llama3"

    async def review(self, context: ReviewContext) -> dict:
        """Review code using Ollama."""
        try:
            code_content = self._prepare_code(context)

            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": self.REVIEW_PROMPT.format(code=code_content),
                        "stream": False,
                        "format": "json",
                    },
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data.get("response", "[]"))
                else:
                    return {
                        "provider": self.name.value,
                        "error": f"Ollama returned {response.status_code}",
                        "findings": [],
                    }

        except Exception as e:
            return {
                "provider": self.name.value,
                "error": str(e),
                "findings": [],
            }

    def _prepare_code(self, context: ReviewContext) -> str:
        """Prepare code for review."""
        parts = []
        for filename, code in list(context.files.items())[:3]:  # Limit for local LLM
            parts.append(f"// {filename}\n{code[:2000]}")  # Truncate large files
        return "\n".join(parts)

    def _parse_response(self, text: str) -> dict:
        """Parse Ollama response."""
        try:
            findings = json.loads(text) if isinstance(text, str) else text
            if isinstance(findings, list):
                return {
                    "provider": self.name.value,
                    "findings": findings,
                }
        except json.JSONDecodeError:
            pass

        return {
            "provider": self.name.value,
            "findings": [],
            "raw_response": text,
        }

    async def health_check(self) -> bool:
        """Check if Ollama is available."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False


# ============================================
# Multi-Provider Review Service
# ============================================


class MultiProviderReviewService:
    """
    Multi-provider code review service.

    Features:
    - Automatic provider selection
    - Parallel reviews from multiple providers
    - Consensus-based findings
    - Provider fallback
    """

    def __init__(
        self,
        providers: Optional[list[ProviderConfig]] = None,
        consensus_threshold: float = 0.5,
    ):
        self.consensus_threshold = consensus_threshold
        self.providers: dict[ReviewProvider, BaseReviewProvider] = {}

        # Initialize default providers
        default_providers = providers or self._get_default_providers()
        for config in default_providers:
            if config.enabled:
                self._add_provider(config)

    def _get_default_providers(self) -> list[ProviderConfig]:
        """Get default provider configurations.

        Auto-detects available providers. If no cloud API keys are set,
        Ollama is promoted to priority 1 for local-only operation.
        """
        providers = []
        has_cloud = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("GITHUB_TOKEN"))

        if os.getenv("ANTHROPIC_API_KEY"):
            providers.append(
                ProviderConfig(
                    name=ReviewProvider.CLAUDE,
                    api_key=os.getenv("ANTHROPIC_API_KEY"),
                    model="claude-sonnet-4-20250514",
                    priority=1,
                )
            )
        if os.getenv("GITHUB_TOKEN"):
            providers.append(
                ProviderConfig(
                    name=ReviewProvider.COPILOT,
                    api_key=os.getenv("GITHUB_TOKEN"),
                    priority=2 if has_cloud else 2,
                )
            )

        # Ollama: priority 1 when no cloud keys, otherwise priority 3
        ollama_priority = 1 if not has_cloud else 3
        providers.append(
            ProviderConfig(
                name=ReviewProvider.OLLAMA,
                base_url=os.getenv(
                    "OLLAMA_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
                ),
                model="llama-3.3-70b",
                priority=ollama_priority,
            )
        )

        return providers

    def _add_provider(self, config: ProviderConfig):
        """Add a provider to the service."""
        provider_classes = {
            ReviewProvider.COPILOT: CopilotReviewProvider,
            ReviewProvider.CLAUDE: ClaudeReviewProvider,
            ReviewProvider.OLLAMA: OllamaReviewProvider,
        }

        provider_class = provider_classes.get(config.name)
        if provider_class:
            self.providers[config.name] = provider_class(config)

    async def review(
        self,
        context: ReviewContext,
        provider: ReviewProvider = ReviewProvider.CLAUDE,
    ) -> dict:
        """
        Review code using specified provider.

        Args:
            context: Review context with files/diff
            provider: Which provider to use

        Returns:
            Review result with findings
        """
        if provider == ReviewProvider.CONSENSUS:
            return await self.consensus_review(context)

        if provider not in self.providers:
            # Fallback to first available provider
            available = await self.get_available_providers()
            if not available:
                return {"error": "No providers available", "findings": []}
            provider = available[0]

        return await self.providers[provider].review(context)

    async def consensus_review(self, context: ReviewContext) -> dict:
        """
        Get consensus review from multiple providers.

        Runs reviews in parallel and aggregates findings.
        """
        available = await self.get_available_providers()
        if not available:
            return {"error": "No providers available", "findings": []}

        # Run reviews in parallel
        tasks = [
            self.providers[provider].review(context)
            for provider in available[:3]  # Max 3 providers
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate findings
        all_findings = []
        provider_results = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                continue
            provider_results.append(
                {
                    "provider": available[i].value,
                    "findings_count": len(result.get("findings", [])),
                }
            )
            all_findings.extend(result.get("findings", []))

        # Deduplicate and score findings
        consensus_findings = self._build_consensus(all_findings)

        return {
            "provider": "consensus",
            "providers_used": provider_results,
            "findings": consensus_findings,
        }

    def _build_consensus(self, findings: list[dict]) -> list[dict]:
        """Build consensus from multiple provider findings."""
        # Group similar findings
        finding_groups: dict[str, list[dict]] = {}

        for finding in findings:
            # Create key based on file + line + category
            key = f"{finding.get('file', '')}:{finding.get('line', 0)}:{finding.get('category', '')}"
            if key not in finding_groups:
                finding_groups[key] = []
            finding_groups[key].append(finding)

        # Build consensus findings
        consensus = []
        for key, group in finding_groups.items():
            if len(group) >= 2 or len(findings) < 3:
                # Multiple providers agree, or not enough providers
                # Use the most detailed finding
                best = max(group, key=lambda f: len(f.get("description", "")))
                best["consensus_count"] = len(group)
                best["confidence"] = len(group) / max(len(self.providers), 1)
                consensus.append(best)

        # Sort by severity and confidence
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        consensus.sort(
            key=lambda f: (
                severity_order.get(f.get("severity", "info"), 5),
                -f.get("confidence", 0),
            )
        )

        return consensus

    async def get_available_providers(self) -> list[ReviewProvider]:
        """Get list of available providers."""
        available = []

        health_checks = await asyncio.gather(
            *[provider.health_check() for provider in self.providers.values()],
            return_exceptions=True,
        )

        for (name, provider), is_healthy in zip(self.providers.items(), health_checks):
            if is_healthy is True:
                available.append(name)

        return available

    def add_custom_provider(self, provider: BaseReviewProvider):
        """Add a custom review provider."""
        self.providers[provider.name] = provider


# ============================================
# Review Result Storage
# ============================================


@dataclass
class StoredReview:
    """A stored review result."""

    id: str
    project_id: str
    provider: str
    files_reviewed: list[str]
    findings: list[dict]
    score: int
    created_at: datetime
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class ReviewStorage:
    """No-op review storage (not configured)."""

    def __init__(self, **kwargs):
        pass

    async def store_review(self, review: StoredReview) -> str:
        """No-op: review storage not configured."""
        return review.id

    async def get_reviews(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[StoredReview]:
        """No-op: review storage not configured."""
        return []


# Export main classes
__all__ = [
    "MultiProviderReviewService",
    "ReviewProvider",
    "ReviewContext",
    "ProviderConfig",
    "CopilotReviewProvider",
    "ClaudeReviewProvider",
    "OllamaReviewProvider",
    "ReviewStorage",
    "StoredReview",
]
