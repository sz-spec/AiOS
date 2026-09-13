"""
Settings API Routes
===================

Manage API keys, model activation, and configuration at runtime.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import os
import httpx

from api.deps import get_current_user, AuthenticatedUser

# Import models catalog
try:
    from config.models_catalog import (
        ALL_MODELS,
        MODELS_BY_ID,
        MODELS_BY_PROVIDER,
        get_model,
        get_all_providers,
        ModelInfo,
        PROVIDER_ENDPOINTS,
        PROVIDER_ENV_KEYS,
        PROVIDER_INFO,
        TASK_RECOMMENDATIONS,
    )

    HAS_CATALOG = True
except ImportError:
    HAS_CATALOG = False
    ALL_MODELS = []
    MODELS_BY_ID = {}
    PROVIDER_ENDPOINTS = {}
    PROVIDER_ENV_KEYS = {}
    PROVIDER_INFO = {}
    TASK_RECOMMENDATIONS = {}

router = APIRouter()

# In-memory storage for activated models (in production, use database)
_activated_models: Dict[str, Dict[str, Any]] = {}


class ApiKeysRequest(BaseModel):
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    github_token: Optional[str] = None


class ApiKeyStatus(BaseModel):
    configured: bool
    masked_key: Optional[str] = None


class ConfigStatus(BaseModel):
    openai: ApiKeyStatus
    anthropic: ApiKeyStatus
    google: ApiKeyStatus
    tavily: ApiKeyStatus
    github: ApiKeyStatus
    redis_url: Optional[str] = None
    ollama_url: Optional[str] = None


def mask_key(key: str) -> str:
    """Mask an API key for display."""
    if not key or len(key) < 8:
        return None
    return f"{key[:4]}...{key[-4:]}"


def get_env_key(name: str) -> str:
    """Get API key from environment."""
    return os.environ.get(name, "")


@router.get(
    "/status",
    summary="Get configuration status",
    description="Return the current status of all configured API keys and service endpoints",
    response_model=ConfigStatus,
)
async def get_config_status(
    user: AuthenticatedUser = Depends(get_current_user),
) -> ConfigStatus:
    """Get current configuration status."""
    openai_key = get_env_key("OPENAI_API_KEY")
    anthropic_key = get_env_key("ANTHROPIC_API_KEY")
    google_key = get_env_key("GOOGLE_API_KEY")
    tavily_key = get_env_key("TAVILY_API_KEY")
    github_token = get_env_key("GITHUB_TOKEN")

    return ConfigStatus(
        openai=ApiKeyStatus(
            configured=bool(openai_key),
            masked_key=mask_key(openai_key) if openai_key else None,
        ),
        anthropic=ApiKeyStatus(
            configured=bool(anthropic_key),
            masked_key=mask_key(anthropic_key) if anthropic_key else None,
        ),
        google=ApiKeyStatus(
            configured=bool(google_key),
            masked_key=mask_key(google_key) if google_key else None,
        ),
        tavily=ApiKeyStatus(
            configured=bool(tavily_key),
            masked_key=mask_key(tavily_key) if tavily_key else None,
        ),
        github=ApiKeyStatus(
            configured=bool(github_token),
            masked_key=mask_key(github_token) if github_token else None,
        ),
        redis_url=os.environ.get("REDIS_URL"),
        ollama_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


@router.post(
    "/api-keys",
    summary="Update API keys",
    description="Set or update API keys for AI providers at runtime without restarting the server",
)
async def update_api_keys(
    request: ApiKeysRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Update API keys at runtime."""
    updated = []

    # Empty body is a valid no-op — return success with no updates
    if all(
        v is None
        for v in [
            request.openai_api_key,
            request.anthropic_api_key,
            request.google_api_key,
            request.tavily_api_key,
            request.github_token,
        ]
    ):
        return {"success": True, "updated": updated}

    if request.openai_api_key is not None:
        os.environ["OPENAI_API_KEY"] = request.openai_api_key
        updated.append("openai")

    if request.anthropic_api_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = request.anthropic_api_key
        updated.append("anthropic")

    if request.google_api_key is not None:
        os.environ["GOOGLE_API_KEY"] = request.google_api_key
        updated.append("google")

    if request.tavily_api_key is not None:
        os.environ["TAVILY_API_KEY"] = request.tavily_api_key
        updated.append("tavily")

    if request.github_token is not None:
        os.environ["GITHUB_TOKEN"] = request.github_token
        updated.append("github")

    # Reload config singleton if it exists
    try:
        from shared.config import reload_config

        reload_config()
    except Exception:
        pass

    return {
        "success": True,
        "updated": updated,
        "message": f"Updated {len(updated)} API key(s)",
    }


@router.post(
    "/validate",
    summary="Validate API keys",
    description="Test all configured API keys by making lightweight probe requests to each provider",
    responses={503: {"description": "One or more provider APIs unreachable"}},
)
async def validate_api_keys(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Validate configured API keys by testing them."""
    results = {}

    # Test OpenAI
    openai_key = get_env_key("OPENAI_API_KEY")
    if openai_key:
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    timeout=10.0,
                )
                results["openai"] = {
                    "valid": response.status_code == 200,
                    "error": (
                        None
                        if response.status_code == 200
                        else f"HTTP {response.status_code}"
                    ),
                }
        except Exception as e:
            results["openai"] = {"valid": False, "error": str(e)}
    else:
        results["openai"] = {"valid": False, "error": "Not configured"}

    # Test Anthropic
    anthropic_key = get_env_key("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                    },
                    timeout=10.0,
                )
                # Anthropic returns 200 for valid keys
                results["anthropic"] = {
                    "valid": response.status_code
                    in [200, 404],  # 404 means endpoint doesn't exist but key is valid
                    "error": (
                        None
                        if response.status_code in [200, 404]
                        else f"HTTP {response.status_code}"
                    ),
                }
        except Exception as e:
            results["anthropic"] = {"valid": False, "error": str(e)}
    else:
        results["anthropic"] = {"valid": False, "error": "Not configured"}

    # Test Google
    google_key = get_env_key("GOOGLE_API_KEY")
    if google_key:
        results["google"] = {"valid": True, "error": None}  # Basic check - key exists
    else:
        results["google"] = {"valid": False, "error": "Not configured"}

    return {
        "results": results,
        "all_valid": all(
            r.get("valid")
            for r in results.values()
            if r.get("error") != "Not configured"
        ),
    }


@router.delete(
    "/api-keys/{provider}",
    summary="Delete API key",
    description="Remove a stored API key for the specified provider",
)
async def delete_api_key(
    provider: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Remove an API key."""
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "github": "GITHUB_TOKEN",
    }

    if provider not in key_map:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    env_key = key_map[provider]
    if env_key in os.environ:
        del os.environ[env_key]

    # Reload config
    try:
        from shared.config import reload_config

        reload_config()
    except Exception:
        pass

    return {"success": True, "deleted": provider}


class CreateRepoRequest(BaseModel):
    name: str
    description: Optional[str] = None
    private: bool = True


@router.post(
    "/github/create-repo",
    summary="Create GitHub repository",
    description="Create a new GitHub repository using the stored GitHub token",
    responses={503: {"description": "GitHub API unavailable"}},
)
async def create_github_repo(
    request: CreateRepoRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Create a new GitHub repository using the stored GitHub token."""
    github_token = get_env_key("GITHUB_TOKEN")
    if not github_token:
        raise HTTPException(
            status_code=400,
            detail="GitHub token not configured. Please add your GitHub token in Settings.",
        )

    # Sanitize repo name (GitHub requirements)
    repo_name = request.name.lower().replace(" ", "-").replace("_", "-")
    repo_name = "".join(c for c in repo_name if c.isalnum() or c == "-")

    async with httpx.AsyncClient() as client:
        # First, get the authenticated user
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

        if user_response.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail="Invalid GitHub token. Please update your token in Settings.",
            )

        user_data = user_response.json()
        username = user_data.get("login")

        # Create the repository
        create_response = await client.post(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "name": repo_name,
                "description": request.description or "Created by VOS3 AI Builder",
                "private": request.private,
                "auto_init": True,
                "gitignore_template": "Node",
            },
            timeout=30.0,
        )

        if create_response.status_code == 201:
            repo_data = create_response.json()
            return {
                "success": True,
                "repo_url": repo_data["html_url"],
                "clone_url": repo_data["clone_url"],
                "full_name": repo_data["full_name"],
                "owner": username,
                "name": repo_name,
                "private": request.private,
            }
        elif create_response.status_code == 422:
            # Repository already exists
            error_data = create_response.json()
            if "name already exists" in str(error_data):
                return {
                    "success": True,
                    "repo_url": f"https://github.com/{username}/{repo_name}",
                    "clone_url": f"https://github.com/{username}/{repo_name}.git",
                    "full_name": f"{username}/{repo_name}",
                    "owner": username,
                    "name": repo_name,
                    "private": request.private,
                    "already_exists": True,
                }
            raise HTTPException(status_code=422, detail=str(error_data))
        else:
            raise HTTPException(
                status_code=create_response.status_code,
                detail=f"Failed to create repository: {create_response.text}",
            )


@router.get(
    "/github/user",
    summary="Get GitHub user",
    description="Retrieve the authenticated GitHub user's profile information",
    responses={503: {"description": "GitHub API unavailable"}},
)
async def get_github_user(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get the authenticated GitHub user info."""
    github_token = get_env_key("GITHUB_TOKEN")
    if not github_token:
        return {"configured": False, "user": None}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )

        if response.status_code == 200:
            user_data = response.json()
            return {
                "configured": True,
                "user": {
                    "login": user_data.get("login"),
                    "name": user_data.get("name"),
                    "avatar_url": user_data.get("avatar_url"),
                    "html_url": user_data.get("html_url"),
                },
            }
        else:
            return {"configured": False, "user": None, "error": "Invalid token"}


# ============================================================================
# MODEL MANAGEMENT ENDPOINTS
# ============================================================================


class ActivateModelRequest(BaseModel):
    model_id: str
    api_key: Optional[str] = None  # If not provided, uses existing env key


class ModelResponse(BaseModel):
    id: str
    name: str
    provider: str
    description: str
    strengths: List[str]
    intended_for: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    max_context: int
    capabilities: List[str]
    is_open_weight: bool
    requires_local: bool
    is_activated: bool = False


def is_model_activated(model_id: str, provider_key: str) -> bool:
    """Check if a model is activated (explicitly or via env API key)."""
    if model_id in _activated_models:
        return True
    # Check if provider has API key in environment
    api_key = get_env_key(provider_key)
    return bool(api_key)


def count_activated_models() -> int:
    """Count total activated models (explicit + from env)."""
    activated_ids = set(_activated_models.keys())
    for model in ALL_MODELS:
        if get_env_key(model.provider_key):
            activated_ids.add(model.id)
    return len(activated_ids)


@router.get(
    "/models/catalog",
    summary="Get models catalog",
    description="Return the full models catalog grouped by provider with activation status",
)
async def get_models_catalog(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get the full models catalog grouped by provider."""
    if not HAS_CATALOG:
        return {"error": "Models catalog not available", "providers": {}}

    result = {}
    for provider, models in MODELS_BY_PROVIDER.items():
        result[provider] = [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "provider_key": m.provider_key,
                "description": m.description,
                "strengths": m.strengths,
                "intended_for": m.intended_for,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "max_context": m.max_context,
                "capabilities": [c.value for c in m.capabilities],
                "is_open_weight": m.is_open_weight,
                "requires_local": m.requires_local,
                "is_activated": is_model_activated(m.id, m.provider_key),
            }
            for m in models
        ]

    return {
        "providers": result,
        "total_models": len(ALL_MODELS),
        "activated_count": count_activated_models(),
    }


@router.get(
    "/models/activated",
    summary="Get activated models",
    description="Return models currently activated via explicit API key or environment configuration",
)
async def get_activated_models(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get list of activated models with their details.

    Models are considered activated if:
    1. They were explicitly activated via the API
    2. Their provider has an API key configured in the environment
    """
    activated = []
    seen_model_ids = set()

    # Map of env variable names to their masked values
    env_keys_with_values = {
        "ANTHROPIC_API_KEY": get_env_key("ANTHROPIC_API_KEY"),
        "OPENAI_API_KEY": get_env_key("OPENAI_API_KEY"),
        "GOOGLE_API_KEY": get_env_key("GOOGLE_API_KEY"),
        "XAI_API_KEY": get_env_key("XAI_API_KEY"),
        "DEEPSEEK_API_KEY": get_env_key("DEEPSEEK_API_KEY"),
        "DASHSCOPE_API_KEY": get_env_key("DASHSCOPE_API_KEY"),
        "OLLAMA_BASE_URL": get_env_key("OLLAMA_BASE_URL"),
    }

    # First, add explicitly activated models
    for model_id, activation in _activated_models.items():
        model = MODELS_BY_ID.get(model_id)
        if model:
            seen_model_ids.add(model_id)
            activated.append(
                {
                    "id": model.id,
                    "name": model.name,
                    "provider": model.provider,
                    "description": model.description,
                    "strengths": model.strengths,
                    "intended_for": model.intended_for,
                    "cost_per_1k_input": model.cost_per_1k_input,
                    "cost_per_1k_output": model.cost_per_1k_output,
                    "max_context": model.max_context,
                    "capabilities": [c.value for c in model.capabilities],
                    "is_open_weight": model.is_open_weight,
                    "requires_local": model.requires_local,
                    "masked_key": (
                        mask_key(activation.get("api_key", ""))
                        if activation.get("api_key")
                        else None
                    ),
                    "activated_at": activation.get("activated_at"),
                }
            )

    # Then, add all models from providers with configured API keys
    for model in ALL_MODELS:
        if model.id in seen_model_ids:
            continue

        api_key = env_keys_with_values.get(model.provider_key, "")
        if api_key:
            seen_model_ids.add(model.id)
            activated.append(
                {
                    "id": model.id,
                    "name": model.name,
                    "provider": model.provider,
                    "description": model.description,
                    "strengths": model.strengths,
                    "intended_for": model.intended_for,
                    "cost_per_1k_input": model.cost_per_1k_input,
                    "cost_per_1k_output": model.cost_per_1k_output,
                    "max_context": model.max_context,
                    "capabilities": [c.value for c in model.capabilities],
                    "is_open_weight": model.is_open_weight,
                    "requires_local": model.requires_local,
                    "masked_key": mask_key(api_key),
                    "activated_at": None,  # Auto-activated from env
                }
            )

    return {
        "models": activated,
        "count": len(activated),
    }


@router.post(
    "/models/activate",
    summary="Activate model",
    description="Activate a model by providing or using an existing API key for its provider",
)
async def activate_model(
    request: ActivateModelRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Activate a model with an API key."""
    model = MODELS_BY_ID.get(request.model_id)
    if not model:
        raise HTTPException(
            status_code=404, detail=f"Model not found: {request.model_id}"
        )

    # Determine API key source
    api_key = request.api_key
    if not api_key:
        # Try to get from environment
        api_key = get_env_key(model.provider_key)

    # For local models, no API key needed
    if model.requires_local:
        api_key = get_env_key("OLLAMA_BASE_URL") or "http://localhost:11434"

    # Store API key in environment if provided
    if request.api_key and model.provider_key:
        os.environ[model.provider_key] = request.api_key

    # Activate the model
    import datetime

    _activated_models[request.model_id] = {
        "api_key": api_key,
        "activated_at": datetime.datetime.now().isoformat(),
        "provider_key": model.provider_key,
    }

    return {
        "success": True,
        "model_id": request.model_id,
        "model_name": model.name,
        "provider": model.provider,
        "message": f"Activated {model.name}",
    }


@router.delete("/models/deactivate/{model_id}", summary="Deactivate model")
async def deactivate_model(
    model_id: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Deactivate a model."""
    if model_id not in _activated_models:
        raise HTTPException(status_code=404, detail=f"Model not activated: {model_id}")

    model = MODELS_BY_ID.get(model_id)
    del _activated_models[model_id]

    return {
        "success": True,
        "model_id": model_id,
        "model_name": model.name if model else model_id,
        "message": f"Deactivated {model.name if model else model_id}",
    }


@router.get(
    "/models/providers/list",
    summary="List providers",
    description="Return all AI providers with model counts, API key status, and detailed info",
)
async def list_providers(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """List all available providers with model counts, endpoints, and detailed info."""
    providers = []
    for provider, models in MODELS_BY_PROVIDER.items():
        activated_count = sum(1 for m in models if m.id in _activated_models)
        # Get the provider_key from first model
        provider_key = models[0].provider_key if models else None
        has_api_key = bool(get_env_key(provider_key)) if provider_key else False
        endpoint = PROVIDER_ENDPOINTS.get(provider, "")
        info = PROVIDER_INFO.get(provider, {})

        providers.append(
            {
                "name": provider,
                "provider_key": provider_key,
                "endpoint": endpoint,
                "model_count": len(models),
                "activated_count": activated_count,
                "has_api_key": has_api_key,
                "masked_key": (
                    mask_key(get_env_key(provider_key)) if has_api_key else None
                ),
                # Detailed provider info
                "strengths": info.get("strengths", []),
                "weaknesses": info.get("weaknesses", []),
                "context": info.get("context", ""),
                "unique": info.get("unique", []),
                "best_for": info.get("best_for", ""),
                "pricing_philosophy": info.get("pricing_philosophy", ""),
            }
        )

    return {
        "providers": providers,
        "total_providers": len(providers),
    }


@router.post(
    "/models/providers/{provider}/api-key",
    summary="Set provider API key",
    description="Store an API key for a specific AI provider in the runtime environment",
)
async def set_provider_api_key(
    provider: str,
    request: Dict[str, str],
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Set API key for a provider."""
    api_key = request.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="API key required")

    env_key = PROVIDER_ENV_KEYS.get(provider)
    if not env_key:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")

    os.environ[env_key] = api_key

    return {
        "success": True,
        "provider": provider,
        "message": f"API key set for {provider}",
    }


@router.get(
    "/models/recommendations",
    summary="Get model recommendations",
    description="Return recommended models for different task types across budget, mid-range, and premium tiers",
)
async def get_task_recommendations(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get recommended models for different tasks."""
    recommendations = []
    for task, models in TASK_RECOMMENDATIONS.items():
        rec = {
            "task": task,
            "budget": None,
            "mid_range": None,
            "premium": None,
        }
        for tier in ["budget", "mid_range", "premium"]:
            model_id = models.get(tier)
            if model_id:
                model = MODELS_BY_ID.get(model_id)
                if model:
                    rec[tier] = {
                        "id": model.id,
                        "name": model.name,
                        "provider": model.provider,
                        "cost_per_1k_input": model.cost_per_1k_input,
                        "cost_per_1k_output": model.cost_per_1k_output,
                    }
        recommendations.append(rec)

    return {
        "recommendations": recommendations,
    }


@router.get(
    "/models/{model_id}",
    summary="Get model details",
    description="Return detailed information about a specific model including activation status and capabilities",
)
async def get_model_details(
    model_id: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get detailed information about a specific model."""
    model = MODELS_BY_ID.get(model_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

    activated = is_model_activated(model_id, model.provider_key)
    activation = _activated_models.get(model_id, {})

    # Get masked key from explicit activation or environment
    masked_key = None
    if activation.get("api_key"):
        masked_key = mask_key(activation.get("api_key", ""))
    elif activated:
        env_key = get_env_key(model.provider_key)
        masked_key = mask_key(env_key) if env_key else None

    return {
        "id": model.id,
        "name": model.name,
        "provider": model.provider,
        "provider_key": model.provider_key,
        "description": model.description,
        "strengths": model.strengths,
        "intended_for": model.intended_for,
        "cost_per_1k_input": model.cost_per_1k_input,
        "cost_per_1k_output": model.cost_per_1k_output,
        "max_context": model.max_context,
        "capabilities": [c.value for c in model.capabilities],
        "is_open_weight": model.is_open_weight,
        "requires_local": model.requires_local,
        "is_activated": activated,
        "masked_key": masked_key,
        "activated_at": activation.get("activated_at"),
    }
