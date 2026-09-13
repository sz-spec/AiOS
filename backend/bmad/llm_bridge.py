"""
BMAD LLM Bridge
================

Routes BMAD agent calls to the optimal LLM via Smart Router.

Mapping:
    BMAD Agent Role → Smart Router Role → Model

    product_manager, market_analyst, ux_designer, scrum_master → researcher (gemini)
    architect → architect (gpt)
    frontend_dev, backend_dev, dev_lead, qa_engineer → coding (claude-sonnet)
    security_reviewer, code_reviewer → reviewer (claude-opus)

Complexity per phase:
    ideation=7, discovery=8, planning=9, design=8,
    development=9, testing=7, review=9, deployment=6, operations=5
"""

import logging
from dataclasses import dataclass
from typing import Optional, AsyncGenerator, Dict

logger = logging.getLogger(__name__)

# Smart Router (optional)
try:
    from src.efficiency import assign_model, HAS_SMART_ROUTER, router as smart_router
except ImportError:
    HAS_SMART_ROUTER = False
    smart_router = None

    def assign_model(role: str, complexity: int, track: bool = True) -> str:
        return "default"


# LLM providers
try:
    from ai.llm.providers import LLM, LLMResponse, Provider, ModelConfig
    from shared.config import ModelConfig  # noqa: F811 - intentional re-export shadow

    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    LLM = None
    LLMResponse = None
    Provider = None
    ModelConfig = None


# BMAD agent role → Smart Router role
ROLE_MAPPING: Dict[str, str] = {
    "product_manager": "researcher",
    "market_analyst": "researcher",
    "ux_designer": "researcher",
    "scrum_master": "researcher",
    "architect": "architect",
    "dev_lead": "coding",
    "frontend_dev": "coding",
    "backend_dev": "coding",
    "devops_engineer": "coding",
    "qa_engineer": "coding",
    "security_reviewer": "reviewer",
    "code_reviewer": "reviewer",
    "deployment_manager": "coding",
    "release_manager": "coding",
    "monitoring_agent": "coding",
    "maintenance_agent": "coding",
}

# Phase → complexity score (0-10)
PHASE_COMPLEXITY: Dict[str, int] = {
    "ideation": 7,
    "discovery": 8,
    "planning": 9,
    "design": 8,
    "development": 9,
    "testing": 7,
    "review": 9,
    "deployment": 6,
    "operations": 5,
}


@dataclass
class BridgeResponse:
    """Response from the LLM bridge."""

    content: str
    model: str = "unknown"
    tokens_used: int = 0
    latency_ms: float = 0
    cached: bool = False


class BMADLLMBridge:
    """
    Routes BMAD agent LLM calls through the Smart Router.

    Falls back to default LLM() if Smart Router is unavailable.
    """

    def __init__(self):
        self._llm_cache: Dict[str, LLM] = {}

    def _get_llm(self, router_role: str, complexity: int) -> Optional["LLM"]:
        """Get or create an LLM instance for the given role/complexity."""
        if not LLM_AVAILABLE:
            return None

        # Use Smart Router to pick model if available
        if HAS_SMART_ROUTER and smart_router:
            try:
                model_name = assign_model(router_role, complexity, track=True)
                logger.info(
                    f"Smart Router selected: {model_name} for {router_role} (complexity={complexity})"
                )
            except Exception as e:
                logger.warning(f"Smart Router failed, using default: {e}")
                model_name = "default"
        else:
            model_name = "default"

        # Cache LLM instances by model name
        if model_name not in self._llm_cache:
            try:
                self._llm_cache[model_name] = LLM()
            except Exception as e:
                logger.error(f"Failed to create LLM: {e}")
                return None

        return self._llm_cache[model_name]

    def generate(
        self,
        agent_role: str,
        phase: str,
        prompt: str,
        system_prompt: str = "",
    ) -> BridgeResponse:
        """
        Generate a response for a BMAD agent.

        Args:
            agent_role: BMAD agent role (e.g. "product_manager")
            phase: Current phase (e.g. "ideation")
            prompt: User/context prompt
            system_prompt: System prompt (persona + instructions)

        Returns:
            BridgeResponse with content and metadata
        """
        router_role = ROLE_MAPPING.get(agent_role, "coding")
        complexity = PHASE_COMPLEXITY.get(phase, 7)

        llm = self._get_llm(router_role, complexity)

        if not llm:
            logger.warning("No LLM available, returning dev mode response")
            return BridgeResponse(
                content=f"[Dev Mode] {agent_role} processing {phase} phase...\n\nInput: {prompt[:200]}",
                model="dev-mode",
            )

        try:
            response = llm.generate(
                prompt,
                system=system_prompt or None,
                use_cache=True,
            )

            return BridgeResponse(
                content=response.content,
                model=response.model,
                tokens_used=response.tokens_used,
                latency_ms=response.latency_ms,
                cached=response.cached,
            )

        except Exception as e:
            logger.error(f"LLM generation failed for {agent_role}/{phase}: {e}")
            return BridgeResponse(
                content=f"[Error] LLM generation failed: {e}",
                model="error",
            )

    async def stream(
        self,
        agent_role: str,
        phase: str,
        prompt: str,
        system_prompt: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        Stream a response for a BMAD agent.

        Args:
            agent_role: BMAD agent role
            phase: Current phase
            prompt: User/context prompt
            system_prompt: System prompt

        Yields:
            Response content chunks
        """
        router_role = ROLE_MAPPING.get(agent_role, "coding")
        complexity = PHASE_COMPLEXITY.get(phase, 7)

        llm = self._get_llm(router_role, complexity)

        if not llm:
            yield f"[Dev Mode] {agent_role} processing {phase} phase..."
            return

        try:
            for chunk in llm.stream(prompt, system=system_prompt or None):
                yield chunk
        except Exception as e:
            logger.error(f"LLM streaming failed for {agent_role}/{phase}: {e}")
            yield f"[Error] Streaming failed: {e}"
