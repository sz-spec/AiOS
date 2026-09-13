"""
LLM Factory - February 2026 Stack
=================================
Centralized LLM instantiation that enforces SmartRouter usage.

All LLM creation MUST go through this factory to ensure:
- Consistent model selection based on role and complexity
- Cost optimization via SmartRouter
- Cross-model verification (Claude code -> GPT review)

Model Stack:
- Architect:        gpt-4o              (gpt)
- Frontend/Backend: claude-sonnet-4-6   (claude-sonnet)
- Tester:           gemini-2.5-flash    (gemini)
- Reviewer:         claude-opus-4-6     (claude-opus)
- Coding:           claude-sonnet-4-6   (claude-sonnet)
- Coding-Complex:   o3-mini             (gpt-codex)
- Researcher:       gemini-2.5-pro      (gemini-pro)
- Researcher-Deep:  claude-opus-4-6     (claude-opus)

Usage:
    from src.efficiency.factory import get_llm_for_task

    # Get LLM for a coding task
    llm = get_llm_for_task("coding", complexity=5)

    # Get LLM for architecture
    llm = get_llm_for_task("architect", complexity=9)

    # With tracking context
    llm, tracker = get_llm_for_task("reviewer", complexity=7, with_tracking=True)
"""

import os
import logging
from typing import Tuple, Any, Union, Optional
from dataclasses import dataclass

from .router import assign_model, assign_model_with_tracking

logger = logging.getLogger(__name__)


# Model name to provider mapping
MODEL_PROVIDERS = {
    # OpenAI
    "gpt": "openai",
    "gpt-codex": "openai",
    # Anthropic
    "claude-opus": "anthropic",
    "claude-sonnet": "anthropic",
    # Google
    "gemini": "google",
    "gemini-pro": "google",
    # Local
    "ollama": "ollama",
    "local": "ollama",
    "local-default": "ollama",
    "local-code": "ollama",
    "local-snappy": "ollama",
    "local-light": "ollama",
}

# Default model IDs per provider
DEFAULT_MODEL_IDS = {
    "openai": {
        "gpt": "gpt-4o",
        "gpt-codex": "o3-mini",
    },
    "anthropic": {
        "claude-opus": "claude-opus-4-6",
        "claude-sonnet": "claude-sonnet-4-6",
    },
    "google": {
        "gemini": "gemini-2.5-flash",
        "gemini-pro": "gemini-2.5-pro",
    },
    "ollama": {
        "ollama": "llama3",
        "local": "llama3",
        "local-default": "llama-3.3-70b",
        "local-code": "qwen2.5-coder:72b",
        "local-snappy": "gemma-4-27b",
        "local-light": "codestral:22b",
    },
}

# Models that support thinking mode
THINKING_MODE_MODELS: set = set()

# Models with specific temperature overrides
MODEL_TEMPERATURE_OVERRIDES: dict = {}

# ==============================================
# Langfuse LLM Tracing (Observability)
# ==============================================
# Initialize Langfuse for LLM call tracing when environment variables are set
# Set LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY in .env to enable

_langfuse_handler = None


def _get_langfuse_handler():
    """Get or create Langfuse callback handler for LLM tracing."""
    global _langfuse_handler

    if _langfuse_handler is not None:
        return _langfuse_handler

    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")

    if secret_key and public_key:
        try:
            from langfuse.langchain import CallbackHandler as LangfuseHandler

            _langfuse_handler = LangfuseHandler(
                secret_key=secret_key,
                public_key=public_key,
                host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
            logger.info("Langfuse LLM tracing enabled")
            return _langfuse_handler
        except ImportError:
            logger.warning("langfuse package not installed, LLM tracing disabled")
        except Exception as e:
            logger.warning(f"Langfuse initialization failed: {e}")

    return None


def get_llm_callbacks() -> list:
    """
    Get list of callback handlers for LLM calls.

    Currently includes:
    - Langfuse (if configured via LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY)

    Returns:
        List of callback handlers to pass to LLM.invoke()
    """
    callbacks = []

    langfuse = _get_langfuse_handler()
    if langfuse:
        callbacks.append(langfuse)

    return callbacks


# ==============================================
# Semantic Cache Integration
# ==============================================
# Provides caching for LLM responses to reduce costs and latency.
# Cache is checked before LLM calls; responses are cached after.
# Set SEMANTIC_CACHE_ENABLED=true in .env to enable (default: enabled)

_semantic_cache = None
_cache_import_attempted = False


def _get_semantic_cache():
    """Get semantic cache instance (lazy initialization)."""
    global _semantic_cache, _cache_import_attempted

    if _cache_import_attempted:
        return _semantic_cache

    _cache_import_attempted = True

    try:
        from ai.cache.semantic_cache import get_semantic_cache

        _semantic_cache = get_semantic_cache()
        if _semantic_cache.available:
            logger.info(
                f"Semantic cache enabled: threshold={_semantic_cache.similarity_threshold}, "
                f"ttl={_semantic_cache.ttl_hours}h"
            )
        return _semantic_cache
    except ImportError as e:
        logger.debug(f"Semantic cache not available: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to initialize semantic cache: {e}")
        return None


async def invoke_with_cache(
    llm,
    prompt: str,
    role: str = "general",
    model_name: str = "unknown",
    skip_cache: bool = False,
    **kwargs,
) -> str:
    """
    Invoke LLM with semantic cache support.

    Checks cache before calling LLM; caches response after.
    This is the recommended way to make LLM calls in the SmartRouter pipeline.

    Args:
        llm: LangChain LLM instance
        prompt: The prompt to send
        role: Task role for cache filtering (coding, architect, etc.)
        model_name: Model name for cache filtering
        skip_cache: Set True to bypass cache
        **kwargs: Additional arguments for llm.invoke()

    Returns:
        LLM response string

    Example:
        llm = get_llm_for_task("coding", complexity=5)
        response = await invoke_with_cache(
            llm,
            prompt="Write a function to sort a list",
            role="coding",
            model_name="claude-opus"
        )
    """
    cache = _get_semantic_cache()

    # Check cache first (unless skipped)
    if cache and cache.available and not skip_cache:
        try:
            entry = await cache.get(prompt, role=role, model=model_name)
            if entry:
                logger.debug(
                    f"Cache hit: role={role}, similarity={entry.similarity:.3f}"
                )
                return entry.response
        except Exception as e:
            logger.debug(f"Cache check failed: {e}")

    # Make LLM call
    callbacks = get_llm_callbacks()
    if callbacks:
        kwargs.setdefault("config", {})
        kwargs["config"]["callbacks"] = callbacks

    response = await llm.ainvoke(prompt, **kwargs)

    # Extract text from response
    if hasattr(response, "content"):
        response_text = response.content
    else:
        response_text = str(response)

    # Cache the response
    if cache and cache.available and not skip_cache:
        try:
            await cache.set(
                query=prompt,
                response=response_text,
                role=role,
                model=model_name,
                metadata={"provider": kwargs.get("provider", "unknown")},
            )
        except Exception as e:
            logger.debug(f"Cache set failed: {e}")

    return response_text


def get_cache_stats() -> Optional[dict]:
    """
    Get semantic cache statistics.

    Returns:
        Dict with hits, misses, hit_rate, etc. or None if cache unavailable
    """
    cache = _get_semantic_cache()
    if cache and cache.available:
        return cache.get_stats().to_dict()
    return None


@dataclass
class LLMConfig:
    """Configuration for LLM instantiation."""

    provider: str
    model_id: str
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 60
    thinking_mode: bool = False


def _get_provider_and_model(router_model: str) -> Tuple[str, str]:
    """
    Map SmartRouter model name to provider and model ID.

    Args:
        router_model: Model name from SmartRouter (e.g., 'claude-opus', 'claude-sonnet', 'gpt', 'gemini-pro')

    Returns:
        Tuple of (provider_name, model_id)
    """
    provider = MODEL_PROVIDERS.get(router_model, "openai")
    model_id = DEFAULT_MODEL_IDS.get(provider, {}).get(router_model, router_model)
    return provider, model_id


def _is_thinking_mode_model(router_model: str) -> bool:
    """Check if the model supports thinking mode."""
    return router_model in THINKING_MODE_MODELS


def _get_temperature_override(router_model: str) -> Optional[float]:
    """Get temperature override for specific models."""
    return MODEL_TEMPERATURE_OVERRIDES.get(router_model)


def _create_llm(
    provider: str,
    model_id: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    thinking_mode: bool = False,
    **kwargs,
) -> Any:
    """
    Create an LLM instance for the given provider.

    Args:
        provider: Provider name (anthropic, openai, google, ollama)
        model_id: Specific model ID
        temperature: Generation temperature
        max_tokens: Maximum tokens for response
        thinking_mode: Enable extended reasoning for supported models
        **kwargs: Additional provider-specific arguments

    Returns:
        LangChain-compatible LLM (Runnable)
    """
    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic

            api_key = kwargs.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")

            llm_kwargs = {
                "model": model_id,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "api_key": api_key,
                "timeout": kwargs.get("timeout", 120),  # Longer timeout for Opus
            }

            return ChatAnthropic(**llm_kwargs)
        except ImportError:
            # W2.5 — refuse to silently swap an Anthropic request to an
            # OpenAI cloud call under sovereign-strict offline mode. Honours
            # the same VOS3_LOCALITY_* envelope as src.efficiency.router.
            _locality = (
                os.environ.get("VOS3_LOCALITY_PREFERENCE", "auto").lower().strip()
            )
            _allow = os.environ.get(
                "VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK", "true"
            ).lower().strip() in ("1", "true", "yes")
            if _locality == "local-first" and not _allow:
                try:
                    from src.efficiency.router import LocalInferenceRequiredError
                except ImportError:
                    raise RuntimeError(
                        "factory: langchain_anthropic missing and strict air-gap "
                        "forbids the OpenAI cloud swap."
                    )
                raise LocalInferenceRequiredError(
                    "factory: langchain_anthropic missing and "
                    "VOS3_LOCALITY_ALLOW_CLOUD_FALLBACK=false — refusing to swap "
                    "the Anthropic request to OpenAI cloud."
                )
            logger.warning(
                "langchain_anthropic not installed; under cloud-allowed mode, "
                "falling back to OpenAI provider with model id 'gpt-4o'."
            )
            provider = "openai"
            model_id = "gpt-4o"

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI

            api_key = kwargs.get("api_key") or os.environ.get("OPENAI_API_KEY")

            llm_kwargs = {
                "model": model_id,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "api_key": api_key,
                "request_timeout": kwargs.get("timeout", 120),
            }

            # Enable thinking mode for GPT-5.2-thinking
            if thinking_mode and "thinking" in model_id.lower():
                llm_kwargs["model_kwargs"] = {
                    "reasoning_effort": "high",  # GPT-5.2 thinking mode parameter
                }
                logger.info(f"Thinking mode enabled for {model_id}")

            return ChatOpenAI(**llm_kwargs)
        except ImportError:
            logger.warning("langchain_openai not installed, falling back to Ollama")
            provider = "ollama"
            model_id = "llama3"

    if provider == "google":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            api_key = kwargs.get("api_key") or os.environ.get("GOOGLE_API_KEY")

            llm_kwargs = {
                "model": model_id,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "google_api_key": api_key,
            }

            return ChatGoogleGenerativeAI(**llm_kwargs)
        except ImportError:
            logger.warning(
                "langchain_google_genai not installed, falling back to Ollama"
            )
            provider = "ollama"
            model_id = "llama3"

    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama

            base_url = kwargs.get("base_url") or os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )
            return ChatOllama(
                model=model_id,
                temperature=temperature,
                base_url=base_url,
            )
        except ImportError:
            raise ImportError(
                "No LLM provider available. Install one of: "
                "langchain-anthropic, langchain-openai, langchain-google-genai, langchain-ollama"
            )

    raise ValueError(f"Unknown provider: {provider}")


def get_llm_for_task(
    role: str,
    complexity: int,
    temperature: Optional[float] = None,
    max_tokens: int = 8192,
    with_tracking: bool = False,
    thinking_mode: Optional[bool] = None,
    **kwargs,
) -> Union[Any, Tuple[Any, Any]]:
    """
    Get an LLM instance optimized for a specific task via SmartRouter.

    This is the ONLY approved way to create LLM instances in the codebase.
    All direct instantiation of ChatAnthropic, ChatOpenAI, etc. should be
    replaced with calls to this function.

    Routing:
    - architect:       gpt-4o               (gpt)
    - frontend:        claude-sonnet-4-6    (claude-sonnet)
    - backend:         claude-sonnet-4-6    (claude-sonnet)
    - tester:          gemini-2.5-flash     (gemini)
    - reviewer:        claude-opus-4-6      (claude-opus)
    - coding:          claude-sonnet-4-6    (claude-sonnet)
    - coding-complex:  o3-mini              (gpt-codex)
    - researcher:      gemini-2.5-pro       (gemini-pro)
    - researcher-deep: claude-opus-4-6      (claude-opus)

    Args:
        role: Task role for routing. Options:
            - "architect":       System design, technical specs (gpt-4o)
            - "frontend":        Frontend code generation (claude-sonnet-4-6)
            - "backend":         Backend code generation (claude-sonnet-4-6)
            - "tester":          Test writing (gemini-2.5-flash)
            - "reviewer":        Code review (claude-opus-4-6)
            - "coding":          General coding (claude-sonnet-4-6)
            - "coding-complex":  Complex multi-step coding (o3-mini)
            - "researcher":      Information gathering (gemini-2.5-pro)
            - "researcher-deep": Deep research (claude-opus-4-6)
        complexity: Task complexity score (1-10):
            - 1-8: Standard complexity (uses role mappings)
            - 9-10: High complexity (premium models automatically)
        temperature: Generation temperature (0.0-1.0). If None, uses model defaults
        max_tokens: Maximum response tokens (default: 8192 for large tasks)
        with_tracking: If True, returns (llm, tracker) tuple for observability
        thinking_mode: Enable extended reasoning. Auto-enabled for architect role
        **kwargs: Additional provider-specific arguments

    Returns:
        If with_tracking=False: LLM instance (Runnable)
        If with_tracking=True: Tuple of (LLM instance, tracker context manager)

    Example:
        # Coding task - routes to Claude Sonnet 4.6
        llm = get_llm_for_task("coding", complexity=5)
        response = llm.invoke("Write a function to...")

        # Architecture task - routes to gpt-4o
        llm = get_llm_for_task("architect", complexity=9)
        response = llm.invoke("Design the system architecture for...")

        # Code review - Claude Opus reviews code
        llm = get_llm_for_task("reviewer", complexity=7)
        response = llm.invoke("Review this code...")
    """
    # Validate inputs
    complexity = max(1, min(10, complexity))

    # Get optimal model from SmartRouter
    if with_tracking:
        router_model, tracker = assign_model_with_tracking(role, complexity)
    else:
        router_model = assign_model(role, complexity)

    # Map to provider and model ID
    provider, model_id = _get_provider_and_model(router_model)

    # Determine temperature
    temp_override = _get_temperature_override(router_model)
    if temperature is not None:
        final_temperature = temperature
    elif temp_override is not None:
        final_temperature = temp_override
    else:
        final_temperature = 0.7

    # Determine thinking mode
    if thinking_mode is None:
        # Auto-enable thinking mode for architect role with thinking models
        enable_thinking = _is_thinking_mode_model(router_model)
    else:
        enable_thinking = thinking_mode and _is_thinking_mode_model(router_model)

    logger.info(
        f"LLM Factory: role={role}, complexity={complexity} -> "
        f"provider={provider}, model={model_id}, thinking={enable_thinking}"
    )

    # Create the LLM
    llm = _create_llm(
        provider=provider,
        model_id=model_id,
        temperature=final_temperature,
        max_tokens=max_tokens,
        thinking_mode=enable_thinking,
        **kwargs,
    )

    if with_tracking:
        return llm, tracker
    return llm


def get_llm_for_rag(complexity: int = 4, temperature: float = 0.3, **kwargs) -> Any:
    """
    Convenience function to get an LLM optimized for RAG tasks.

    RAG typically needs lower temperature for factual responses.
    Routes to researcher role which uses Gemini 2.5 Pro for massive context.

    Args:
        complexity: Task complexity (default: 4 for standard RAG)
        temperature: Lower temperature for factual responses (default: 0.3)
        **kwargs: Additional arguments

    Returns:
        LLM instance configured for RAG
    """
    return get_llm_for_task(
        role="researcher", complexity=complexity, temperature=temperature, **kwargs
    )


def get_llm_for_agents(agent_role: str, task_complexity: int = 5, **kwargs) -> Any:
    """
    Convenience function to get an LLM for agent tasks.

    Maps agent roles to SmartRouter roles.

    Args:
        agent_role: Agent role (architect, frontend, backend, tester, reviewer)
        task_complexity: Complexity of the current task
        **kwargs: Additional arguments

    Returns:
        LLM instance configured for the agent
    """
    # Map agent roles to router roles (direct 1:1 where available)
    role_mapping = {
        "architect": "architect",  # -> gpt-4o
        "frontend": "frontend",  # -> claude-sonnet-4-6
        "backend": "backend",  # -> claude-sonnet-4-6
        "tester": "tester",  # -> gemini-2.5-flash
        "reviewer": "reviewer",  # -> claude-opus-4-6
        "project_manager": "architect",  # -> gpt-4o
        "researcher": "researcher",  # -> gemini-2.5-pro
    }

    router_role = role_mapping.get(agent_role.lower(), "coding")
    return get_llm_for_task(role=router_role, complexity=task_complexity, **kwargs)


def get_llm_for_cross_review(
    code_author: str = "claude", complexity: int = 7, **kwargs
) -> Any:
    """
    Get an LLM for cross-model code review.

    Enforces that code is reviewed by a different model family
    to avoid "echo chamber" effects.

    Args:
        code_author: The model that wrote the code ("claude", "gpt", "gemini")
        complexity: Review complexity
        **kwargs: Additional arguments

    Returns:
        LLM instance from a different model family
    """
    # Cross-review mapping: author -> reviewer (different model family)
    cross_review_mapping = {
        "claude": "architect",  # Claude code -> GPT review
        "gpt": "reviewer",  # GPT code -> Claude Opus review
        "gemini": "reviewer",  # Gemini code -> Claude Opus review
    }

    role = cross_review_mapping.get(code_author.lower(), "reviewer")
    return get_llm_for_task(role=role, complexity=complexity, **kwargs)


def calculate_complexity(
    text: str, task_type: str = "general", base_complexity: int = 5
) -> int:
    """
    Calculate complexity score for a task based on text and type.

    Complexity >= 9 triggers premium model routing.

    Args:
        text: The task description or prompt
        task_type: Type of task (affects base complexity):
            - "simple": Base 3 (quick operations)
            - "general": Base 5 (standard tasks)
            - "complex": Base 7 (multi-step tasks)
            - "critical": Base 9 (architecture, security)
        base_complexity: Override base complexity

    Returns:
        Complexity score (1-10)
    """
    # Task type base scores
    type_bases = {
        "simple": 3,
        "general": 5,
        "complex": 7,
        "critical": 9,
    }

    base = type_bases.get(task_type, base_complexity)

    # Adjust based on text length
    text_len = len(text) if text else 0
    length_modifier = min(2, text_len // 500)  # +1 per 500 chars, max +2

    # Adjust based on keywords indicating complexity
    complexity_keywords = [
        "architecture",
        "security",
        "optimize",
        "refactor",
        "integration",
        "migration",
        "scale",
        "performance",
        "distributed",
        "concurrent",
        "async",
        "parallel",
        "codebase",
        "large-scale",
        "multi-file",
        "system-wide",
    ]
    keyword_modifier = sum(1 for kw in complexity_keywords if kw in text.lower())
    keyword_modifier = min(2, keyword_modifier)  # Max +2 for keywords

    # Calculate final complexity
    complexity = base + length_modifier + keyword_modifier
    return max(1, min(10, complexity))


__all__ = [
    "get_llm_for_task",
    "get_llm_for_rag",
    "get_llm_for_agents",
    "get_llm_for_cross_review",
    "get_llm_callbacks",
    "calculate_complexity",
    "LLMConfig",
    # Semantic cache integration
    "invoke_with_cache",
    "get_cache_stats",
]
