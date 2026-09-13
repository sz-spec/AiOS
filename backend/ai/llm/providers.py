"""
LLM Providers - SmartRouter Integrated
======================================
Unified interface for LLM providers via SmartRouter Factory.

All LLM creation is delegated to src.efficiency.factory to ensure:
- Consistent model selection based on role and complexity
- Cost optimization via SmartRouter
- Cross-model verification capabilities

Usage:
    from ai.llm.providers import LLM, Provider, generate

    # Role-based routing (recommended)
    llm = LLM(role="coding", complexity=5)
    response = llm.generate("Write a function")

    # Legacy provider-based (maps to roles)
    llm = LLM(provider=Provider.ANTHROPIC)
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Generator, Union
import logging

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


class Provider(Enum):
    """Supported LLM providers (for backward compatibility)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    LOCAL = "local"


@dataclass
class LLMResponse:
    """Standardized LLM response."""

    content: str
    model: str
    provider: Provider
    tokens_used: int = 0
    latency_ms: float = 0
    cached: bool = False
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "model": self.model,
            "provider": (
                self.provider.value
                if isinstance(self.provider, Provider)
                else self.provider
            ),
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "metadata": self.metadata,
        }


class LLM:
    """
    Unified LLM interface using SmartRouter Factory.

    All LLM creation is delegated to the Factory for consistent routing
    and cost optimization.

    Usage:
        # Role-based routing (recommended)
        llm = LLM(role="coding", complexity=5)
        response = llm.generate("Write a function")

        # Legacy provider-based (maps to roles)
        llm = LLM(provider=Provider.ANTHROPIC)
        response = llm.generate("Tell me a joke")
    """

    def __init__(
        self,
        provider: Provider = None,
        role: str = "coding",
        complexity: int = 5,
        temperature: float = 0.7,
        fallback_enabled: bool = True,
    ):
        self.fallback_enabled = fallback_enabled
        self.role = role
        self.complexity = complexity
        self.temperature = temperature

        # Map legacy provider to role
        if provider:
            self.role = self._provider_to_role(provider)

        # Get LLM from Factory
        self.llm = self._get_llm_from_factory()

    def _provider_to_role(self, provider: Provider) -> str:
        """Map legacy provider enum to SmartRouter role."""
        mapping = {
            Provider.OPENAI: "coding",
            Provider.ANTHROPIC: "coding",
            Provider.GOOGLE: "researcher",
            Provider.LOCAL: "coding",
        }
        return mapping.get(provider, "coding")

    def _get_llm_from_factory(self):
        """Get LLM instance from SmartRouter Factory."""
        try:
            from src.efficiency.factory import get_llm_for_task

            return get_llm_for_task(
                role=self.role, complexity=self.complexity, temperature=self.temperature
            )
        except ImportError as e:
            logger.warning(f"Factory import failed: {e}")
            return None
        except Exception as e:
            logger.warning(f"Factory initialization failed: {e}")
            return None

    def _to_messages(
        self, prompt: Union[str, List[BaseMessage]], system: str = None
    ) -> List[BaseMessage]:
        """Convert input to list of messages."""
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [HumanMessage(content=prompt)]

        if system:
            messages = [SystemMessage(content=system)] + messages

        return messages

    def generate(
        self,
        prompt: Union[str, List[BaseMessage]],
        system: str = None,
        use_cache: bool = True,
        **kwargs,
    ) -> LLMResponse:
        """
        Generate response from prompt via SmartRouter.

        Args:
            prompt: User prompt or list of messages
            system: Optional system message
            use_cache: Whether to use cache (ignored - handled by Factory)
            **kwargs: Additional arguments for the model

        Returns:
            LLMResponse with content and metadata
        """
        messages = self._to_messages(prompt, system)
        start_time = time.time()

        if not self.llm:
            raise RuntimeError("No LLM available - Factory not initialized")

        try:
            response = self.llm.invoke(messages, **kwargs)
            latency = (time.time() - start_time) * 1000

            # Extract token usage if available
            tokens = 0
            if hasattr(response, "response_metadata"):
                usage = response.response_metadata.get("token_usage", {})
                tokens = usage.get("total_tokens", 0)
            elif hasattr(response, "usage_metadata"):
                tokens = response.usage_metadata.get("total_tokens", 0)

            # Determine provider from model
            model_name = getattr(self.llm, "model", "unknown")
            provider = Provider.OPENAI
            if "claude" in str(model_name).lower():
                provider = Provider.ANTHROPIC
            elif "gemini" in str(model_name).lower():
                provider = Provider.GOOGLE

            return LLMResponse(
                content=response.content,
                model=str(model_name),
                provider=provider,
                tokens_used=tokens,
                latency_ms=latency,
            )

        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str:
                raise RuntimeError(f"Rate limit exceeded: {e}")
            raise RuntimeError(f"Generation failed: {e}")

    def stream(
        self, prompt: Union[str, List[BaseMessage]], system: str = None, **kwargs
    ) -> Generator[str, None, None]:
        """Stream response tokens."""
        messages = self._to_messages(prompt, system)

        if not self.llm:
            raise RuntimeError("No LLM available - Factory not initialized")

        try:
            for chunk in self.llm.stream(messages, **kwargs):
                if hasattr(chunk, "content") and chunk.content:
                    yield chunk.content
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise RuntimeError(f"Streaming failed: {e}")

    def get_info(self) -> Dict[str, Any]:
        """Get information about current configuration."""
        model_name = (
            getattr(self.llm, "model", "unknown") if self.llm else "not initialized"
        )
        return {
            "role": self.role,
            "complexity": self.complexity,
            "model": str(model_name),
            "temperature": self.temperature,
            "factory_enabled": self.llm is not None,
        }

    def switch_provider(self, provider: Provider, model_config=None):
        """Switch to a different provider (maps to role change)."""
        self.role = self._provider_to_role(provider)
        self.llm = self._get_llm_from_factory()
        logger.info(f"Switched to role: {self.role}")


# Convenience functions
def generate(
    prompt: str, system: str = None, role: str = "coding", complexity: int = 5, **kwargs
) -> str:
    """Quick generate function via SmartRouter."""
    llm = LLM(role=role, complexity=complexity)
    response = llm.generate(prompt, system=system, **kwargs)
    return response.content


def stream(
    prompt: str, system: str = None, role: str = "coding", complexity: int = 5, **kwargs
) -> Generator[str, None, None]:
    """Quick stream function via SmartRouter."""
    llm = LLM(role=role, complexity=complexity)
    yield from llm.stream(prompt, system=system, **kwargs)
