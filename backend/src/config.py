"""
Configuration Management for AI App Builder
============================================
Handles environment variables, API keys, and system settings.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from pathlib import Path
import json


@dataclass
class ModelConfig:
    """Configuration for AI models."""

    name: str
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 120
    retry_attempts: int = 3
    retry_delay: float = 1.0


@dataclass
class CacheConfig:
    """Configuration for caching system."""

    enabled: bool = True
    backend: str = "redis"  # redis, memory, file
    ttl: int = 3600  # Time to live in seconds
    max_size: int = 1000  # Max cached items
    redis_url: str = "redis://localhost:6379/0"
    file_path: str = ".cache/ai_cache"


@dataclass
class GitConfig:
    """Configuration for Git integration."""

    enabled: bool = True
    auto_commit: bool = True
    branch_prefix: str = "ai-generated"
    remote_url: Optional[str] = None
    username: Optional[str] = None
    token: Optional[str] = None


@dataclass
class MCPConfig:
    """Configuration for MCP (Model Context Protocol) server integration.

    When enabled, routes LLM requests through V-OS-MCP-Agent-Server for:
    - Semantic caching (save 40-60% on repeated queries)
    - User quotas (per-user spending limits)
    - Smart routing (automatic model selection)
    - Cost tracking (real-time analytics)
    """

    enabled: bool = field(
        default_factory=lambda: os.getenv("MCP_ENABLED", "false").lower() == "true"
    )
    base_url: str = field(
        default_factory=lambda: os.getenv("MCP_SERVER_URL", "http://localhost:8080")
    )
    timeout: float = 120.0
    retry_attempts: int = 3


@dataclass
class Config:
    """Main configuration class."""

    # API Keys
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "")
    )
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    # Model configurations
    primary_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            name="gpt-4o", temperature=0.7, max_tokens=4096
        )
    )

    fallback_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            name="gpt-3.5-turbo", temperature=0.7, max_tokens=4096
        )
    )

    code_model: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            name="gpt-4o",
            temperature=0.2,  # Lower temperature for more consistent code
            max_tokens=8192,
        )
    )

    # Cache configuration
    cache: CacheConfig = field(default_factory=CacheConfig)

    # Git configuration
    git: GitConfig = field(default_factory=GitConfig)

    # MCP (V-OS-MCP-Agent-Server) configuration
    mcp: MCPConfig = field(default_factory=MCPConfig)

    # Paths
    output_dir: Path = field(default_factory=lambda: Path("./generated"))
    templates_dir: Path = field(default_factory=lambda: Path("./templates"))
    logs_dir: Path = field(default_factory=lambda: Path("./logs"))

    # System settings
    debug: bool = field(
        default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true"
    )
    max_iterations: int = 10  # Max iterations for self-correction loops
    validation_enabled: bool = True

    def __post_init__(self):
        """Create necessary directories."""
        for dir_path in [self.output_dir, self.templates_dir, self.logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load configuration from JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls()

    def validate(self) -> Dict[str, Any]:
        """Validate configuration and return status."""
        issues = []
        warnings = []

        # Check API keys
        if not self.openai_api_key and not self.anthropic_api_key:
            issues.append("No AI API key configured (OpenAI or Anthropic required)")

        if not self.tavily_api_key:
            warnings.append("Tavily API key not set - web search will be unavailable")

        # Check Git config
        if self.git.enabled and not self.git.remote_url:
            warnings.append("Git enabled but no remote URL configured")

        return {"valid": len(issues) == 0, "issues": issues, "warnings": warnings}

    def get_active_model(self) -> ModelConfig:
        """Get the currently active model based on availability."""
        if self.openai_api_key:
            return self.primary_model
        elif self.anthropic_api_key:
            return ModelConfig(name="claude-sonnet-4-20250514", temperature=0.7)
        elif os.getenv("OLLAMA_BASE_URL"):
            return ModelConfig(name="llama-3.3-70b", temperature=0.7)
        else:
            raise ValueError(
                "No API key configured. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or OLLAMA_BASE_URL."
            )


# Singleton instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create configuration singleton."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def reload_config() -> Config:
    """Force reload configuration."""
    global _config
    _config = Config.from_env()
    return _config


def validate_config() -> Dict[str, Any]:
    """Validate environment-level configuration values.

    Only checks variables that are actually set. Returns a dict with:
        - valid (bool): True if all present values pass validation.
        - errors (list[str]): Human-readable descriptions of any failures.
    """
    errors: list[str] = []

    convex_url = os.getenv("CONVEX_URL")
    if convex_url is not None and not convex_url.startswith("https://"):
        errors.append("CONVEX_URL must start with 'https://'")

    clerk_key = os.getenv("CLERK_SECRET_KEY")
    if clerk_key is not None and not clerk_key.startswith("sk_"):
        errors.append("CLERK_SECRET_KEY must start with 'sk_'")

    redis_url = os.getenv("REDIS_URL")
    if redis_url is not None and not (
        redis_url.startswith("redis://") or redis_url.startswith("rediss://")
    ):
        errors.append("REDIS_URL must start with 'redis://' or 'rediss://'")

    sentry_dsn = os.getenv("SENTRY_DSN")
    if sentry_dsn is not None and not sentry_dsn.startswith("https://"):
        errors.append("SENTRY_DSN must start with 'https://'")

    return {"valid": len(errors) == 0, "errors": errors}
