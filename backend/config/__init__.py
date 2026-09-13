"""
Configuration module for VOS3 backend.
"""

from .models_catalog import (
    ALL_MODELS,
    MODELS_BY_ID,
    MODELS_BY_PROVIDER,
    get_model,
    get_models_for_provider,
    get_all_providers,
    ModelInfo,
    ModelCapability,
)

__all__ = [
    "ALL_MODELS",
    "MODELS_BY_ID",
    "MODELS_BY_PROVIDER",
    "get_model",
    "get_models_for_provider",
    "get_all_providers",
    "ModelInfo",
    "ModelCapability",
]
