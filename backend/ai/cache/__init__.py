"""
AI Cache Module
================
Semantic caching for LLM responses to reduce costs and latency.
"""

from .semantic_cache import (
    SemanticCache,
    get_semantic_cache,
    CacheEntry,
    CacheStats,
)

__all__ = [
    "SemanticCache",
    "get_semantic_cache",
    "CacheEntry",
    "CacheStats",
]
