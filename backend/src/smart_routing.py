"""
Smart Routing — Model Switcher for Loop Breaking (Phase 2.0, Subsystem #2)

When the pipeline detects a semantic loop (same errors repeating), this module
provides alternate model selection. The key insight: switching PROVIDER (not just
model size) gives the best results — different training data leads to different
problem-solving approaches.

See: docs/TECHNICAL_IMPLEMENTATION_SPEC.md Section 2.3.4
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Pre-computed alternate model map.
# Key rule: switch PROVIDER, not just model.
# If Claude is stuck, switch to GPT (different training data = different perspective).
# If GPT is stuck, switch to Claude.
_ALTERNATE_MODELS: Dict[str, str] = {
    # Current model → Alternate model (different provider)
    # Model IDs MUST match router.yaml definitions exactly.
    "claude-sonnet-4-6": "gpt-4o",
    "claude-opus-4-6": "gpt-4o",
    "gpt-4o": "claude-sonnet-4-6",
    "o3-mini": "claude-sonnet-4-6",
    "gemini-2.5-flash": "claude-sonnet-4-6",
    "gemini-2.5-pro": "claude-opus-4-6",
}

# Thinking / high-reasoning model for Stage 2 escalation
THINKING_MODEL = "o3-mini"


def get_alternate_model(current_model_id: str) -> str:
    """
    Return a model from a DIFFERENT provider for loop-breaking.

    The insight: when Claude is stuck on a problem, GPT often has a different
    internal representation that avoids the same dead-end. And vice versa.
    Switching within the same provider (e.g., Sonnet → Opus) is less effective.

    Args:
        current_model_id: The model ID currently being used (e.g. "claude-sonnet-4-6")

    Returns:
        An alternate model ID from a different provider.
    """
    alternate = _ALTERNATE_MODELS.get(current_model_id, "gpt-4o")
    logger.info(
        "[ROUTING] Alternate model for loop-break: %s → %s",
        current_model_id,
        alternate,
    )
    return alternate


def get_thinking_model() -> str:
    """
    Return the high-reasoning "thinking" model for Stage 2 escalation.

    Stage 2 uses a model with strong chain-of-thought capabilities to
    reason through problems that simpler models couldn't solve.
    """
    return THINKING_MODEL
