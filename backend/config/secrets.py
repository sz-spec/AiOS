"""Centralized secrets loader with production safety guards.

Reads all sensitive environment variables from a single location.
In production (ENVIRONMENT=production), any value still set to
REPLACE_ME_* will cause an immediate startup crash.
"""

import os
import sys

# Sentinel prefix that must be replaced before production deployment
_REPLACE_PREFIX = "REPLACE_ME"


def _get(key: str, default: str = "") -> str:
    """Read an env var, stripping whitespace."""
    return os.environ.get(key, default).strip()


# --- LLM Provider Keys ---
OPENAI_API_KEY: str = _get("OPENAI_API_KEY")
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")
GOOGLE_API_KEY: str = _get("GOOGLE_API_KEY")
XAI_API_KEY: str = _get("XAI_API_KEY")

# --- Authentication (Clerk) ---
CLERK_SECRET_KEY: str = _get("CLERK_SECRET_KEY")
CLERK_PUBLISHABLE_KEY: str = _get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY")
CLERK_ISSUER_URL: str = _get("CLERK_ISSUER_URL")
CLERK_WEBHOOK_SECRET: str = _get("CLERK_WEBHOOK_SECRET")

# --- Database (Convex) ---
CONVEX_URL: str = _get("CONVEX_URL")
CONVEX_DEPLOY_KEY: str = _get("CONVEX_DEPLOY_KEY")

# --- Billing (Stripe) ---
STRIPE_SECRET_KEY: str = _get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET: str = _get("STRIPE_WEBHOOK_SECRET")

# --- Observability ---
SENTRY_DSN: str = _get("SENTRY_DSN")
LANGFUSE_SECRET_KEY: str = _get("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY: str = _get("LANGFUSE_PUBLIC_KEY")

# --- Infrastructure ---
REDIS_URL: str = _get("REDIS_URL")
VOS_API_SECRET: str = _get("VOS_API_SECRET")

# --- Runtime ---
ENVIRONMENT: str = _get("ENVIRONMENT", "development")


def validate_no_placeholders() -> list[str]:
    """Return a list of keys that still contain REPLACE_ME placeholder values."""
    violations: list[str] = []
    # Only check keys that have a non-empty value
    _secrets_map = {
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GOOGLE_API_KEY": GOOGLE_API_KEY,
        "XAI_API_KEY": XAI_API_KEY,
        "CLERK_SECRET_KEY": CLERK_SECRET_KEY,
        "CONVEX_URL": CONVEX_URL,
        "CONVEX_DEPLOY_KEY": CONVEX_DEPLOY_KEY,
        "STRIPE_SECRET_KEY": STRIPE_SECRET_KEY,
        "STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET,
        "SENTRY_DSN": SENTRY_DSN,
        "VOS_API_SECRET": VOS_API_SECRET,
    }
    for key, value in _secrets_map.items():
        if value and _REPLACE_PREFIX in value:
            violations.append(key)
    return violations


def enforce_production_secrets() -> None:
    """Crash hard if any secret still has a REPLACE_ME placeholder in production.

    Call this during application startup (lifespan) to prevent
    accidental production deployments with placeholder credentials.
    """
    if ENVIRONMENT != "production":
        return

    violations = validate_no_placeholders()
    if violations:
        msg = (
            f"FATAL: {len(violations)} secret(s) still contain "
            f"REPLACE_ME placeholders in production: {', '.join(violations)}. "
            f"Set real values before deploying."
        )
        print(f"[FATAL] {msg}", file=sys.stderr)
        raise RuntimeError(msg)
