"""Shared API helper functions."""

import logging

logger = logging.getLogger(__name__)


def safe_assign_model(role: str, complexity: int = 5):
    """Safely assign a model with tracking, returning (model, tracker) or (default, None)."""
    try:
        from src.efficiency import assign_model_with_tracking

        return assign_model_with_tracking(role, complexity)
    except Exception as e:
        logger.warning("Model assignment failed for role=%s: %s", role, e)
        return "claude-sonnet", None
