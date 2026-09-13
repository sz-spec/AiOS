"""
Auth Injector — Phase 3.0 Subsystem 3.1
=========================================
Detects auth requirements from natural language, selects a pre-tested template
strategy, and injects template files into the build pipeline.

No LLM calls — pure keyword detection + file loading. Zero hallucination risk.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("auth_injector")

# =============================================================================
# Template paths
# =============================================================================

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "auth"
_MANIFEST_PATH = _TEMPLATES_DIR / "manifest.json"


# =============================================================================
# Keyword Detection
# =============================================================================

AUTH_KEYWORDS: Dict[str, List[str]] = {
    "clerk": ["clerk", "clerk.com", "clerkprovider"],
    "nextauth": ["nextauth", "next-auth", "authjs", "auth.js"],
    "custom_jwt": [
        "login",
        "register",
        "signup",
        "sign up",
        "authentication",
        "password",
        "auth",
        "user account",
        "jwt",
    ],
}


def detect_auth_strategy(
    requirements: str,
    architecture: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Detect which auth strategy to use from requirements text.

    Priority:
    1. Explicit provider mentions (clerk, nextauth) — highest
    2. Implicit auth keywords (login, register, etc.) → custom_jwt
    3. No match → None

    Returns: "clerk" | "nextauth" | "custom_jwt" | None
    """
    req_lower = requirements.lower()

    # Check explicit provider mentions first
    for strategy in ("clerk", "nextauth"):
        if any(kw in req_lower for kw in AUTH_KEYWORDS[strategy]):
            return strategy

    # Implicit auth keywords → default to custom_jwt
    if any(kw in req_lower for kw in AUTH_KEYWORDS["custom_jwt"]):
        return "custom_jwt"

    return None


# =============================================================================
# Manifest Loading
# =============================================================================


def load_manifest() -> Dict[str, Any]:
    """Load the auth template manifest. Returns empty dict if not found."""
    if not _MANIFEST_PATH.exists():
        logger.warning("Auth manifest not found: %s", _MANIFEST_PATH)
        return {}
    return json.loads(_MANIFEST_PATH.read_text())


def get_strategy_manifest(strategy: str) -> Optional[Dict[str, Any]]:
    """Get manifest entry for a specific strategy."""
    manifest = load_manifest()
    strategies = manifest.get("strategies", {})
    return strategies.get(strategy)


# =============================================================================
# File Injection
# =============================================================================


def load_template_files(strategy: str) -> Dict[str, Dict[str, str]]:
    """
    Load all template files for a given strategy.

    Returns:
        {
            "frontend": {"dest/path.tsx": "file content", ...},
            "backend": {"dest/path.py": "file content", ...},
        }
    """
    strategy_manifest = get_strategy_manifest(strategy)
    if not strategy_manifest:
        logger.warning("No manifest entry for strategy: %s", strategy)
        return {"frontend": {}, "backend": {}}

    strategy_dir = _TEMPLATES_DIR / strategy
    result: Dict[str, Dict[str, str]] = {"frontend": {}, "backend": {}}

    for side in ("frontend", "backend"):
        file_entries = strategy_manifest.get("files", {}).get(side, [])
        for entry in file_entries:
            src_path = strategy_dir / side / entry["src"]
            dest_path = entry["dest"]
            if src_path.exists():
                result[side][dest_path] = src_path.read_text()
            else:
                logger.warning(
                    "Template file missing: %s (strategy=%s)", src_path, strategy
                )

    return result


def get_auth_dependencies(strategy: str) -> Dict[str, Dict[str, str]]:
    """Get package dependencies for a strategy."""
    strategy_manifest = get_strategy_manifest(strategy)
    if not strategy_manifest:
        return {"frontend": {}, "backend": {}}
    return strategy_manifest.get("dependencies", {"frontend": {}, "backend": {}})


def get_auth_env_vars(strategy: str) -> List[str]:
    """Get required environment variables for a strategy."""
    strategy_manifest = get_strategy_manifest(strategy)
    if not strategy_manifest:
        return []
    return strategy_manifest.get("env_vars", [])


def get_test_scenarios(strategy: str) -> List[str]:
    """Get auth-specific test scenarios for the tester node."""
    strategy_manifest = get_strategy_manifest(strategy)
    if not strategy_manifest:
        return []
    return strategy_manifest.get("test_scenarios", [])


# =============================================================================
# Pipeline Integration
# =============================================================================


def inject_auth_into_state(
    strategy: str,
    frontend_code: Dict[str, str],
    backend_code: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    """
    Inject auth template files into existing frontend/backend code dicts.

    Template files are added with HIGH priority — they overwrite any
    AI-generated auth files at the same path. This is intentional:
    pre-tested templates are more reliable than generated auth code.

    Returns:
        {"frontend_code": {...}, "backend_code": {...}}
    """
    template_files = load_template_files(strategy)

    updated_frontend = dict(frontend_code)
    updated_backend = dict(backend_code)

    # Inject with HIGH priority (template overwrites generated)
    updated_frontend.update(template_files.get("frontend", {}))
    updated_backend.update(template_files.get("backend", {}))

    injected_count = len(template_files.get("frontend", {})) + len(
        template_files.get("backend", {})
    )
    logger.info(
        "Auth injection complete: strategy=%s, files_injected=%d",
        strategy,
        injected_count,
    )

    return {
        "frontend_code": updated_frontend,
        "backend_code": updated_backend,
    }
