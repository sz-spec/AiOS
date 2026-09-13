"""Pydantic-based settings for VOS3 backend.

Validates ALL environment variables at import time with proper types.
Usage:
    from config.settings import settings
    print(settings.convex_url)
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Resolve .env path (backend/.env or repo root .env)
_backend_dir = Path(__file__).resolve().parent.parent
_env_file = _backend_dir / ".env"
if not _env_file.exists():
    _env_file = _backend_dir.parent / ".env"


class VOS3Settings(BaseSettings):
    """Typed, validated settings for VOS3.  Loaded from env vars + .env file."""

    # --- General ---
    environment: str = Field(default="development", description="Runtime environment")
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = Field(default=False)

    # --- LLM Provider Keys (all optional in dev) ---
    openai_api_key: str = Field(default="", description="OpenAI API key")
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    google_api_key: str = Field(default="", description="Google Gemini API key")
    xai_api_key: str = Field(default="", description="xAI API key")
    gemini_model: str = Field(default="gemini-3-pro-preview")
    ollama_base_url: str = Field(default="http://localhost:11434")

    # --- Authentication (Clerk) ---
    clerk_secret_key: str = Field(default="", description="Clerk secret key")
    next_public_clerk_publishable_key: str = Field(default="")
    clerk_issuer_url: str = Field(default="")
    clerk_webhook_secret: str = Field(default="")

    # --- Database (Convex) ---
    convex_url: str = Field(default="", description="Convex deployment URL")
    convex_deploy_key: str = Field(default="", description="Convex deploy key")
    vos3_storage_backend: str = Field(
        default="convex", description="Storage backend type"
    )

    # --- Billing (Stripe) ---
    stripe_secret_key: str = Field(default="")
    stripe_webhook_secret: str = Field(default="")

    # --- Infrastructure ---
    redis_url: str = Field(default="", description="Redis URL for caching")
    vos_api_secret: str = Field(default="", description="Backend API secret")
    allowed_origins: str = Field(default="", description="Comma-separated CORS origins")

    # --- Frontend proxy ---
    next_public_api_url: str = Field(default="http://localhost:8000")

    # --- Observability ---
    sentry_dsn: str = Field(default="")
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    sentry_profiles_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    langfuse_secret_key: str = Field(default="")
    langfuse_public_key: str = Field(default="")
    langfuse_host: str = Field(default="https://cloud.langfuse.com")
    langchain_tracing_v2: bool = Field(default=False)
    langchain_project: str = Field(default="default")

    # --- Sandbox Resource Limits ---
    sandbox_memory_mb: int = Field(
        default=512, ge=64, le=8192, description="Sandbox RLIMIT_AS in MB"
    )
    sandbox_cpu_soft: int = Field(default=60, ge=1)
    sandbox_cpu_hard: int = Field(default=120, ge=1)

    @field_validator("convex_url")
    @classmethod
    def _convex_must_be_https_in_prod(cls, v: str) -> str:
        env = os.environ.get("ENVIRONMENT", "development")
        if env == "production" and v and not v.startswith("https://"):
            raise ValueError("CONVEX_URL must use HTTPS in production")
        return v

    model_config = {
        "env_file": str(_env_file) if _env_file.exists() else None,
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Singleton — validated at import time
settings = VOS3Settings()
