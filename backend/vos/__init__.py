"""
VOS - Hidden System Agent
=========================
Central intelligence layer for VOS3. Intercepts system-level commands
from users and agents, executing them via internal API tools.

Invisible to users, observable by admins.
"""

from vos.engine import VosEngine, VosResult, get_vos_engine
from vos.tool_registry import ToolRegistry, VosTool, build_default_registry
from vos.intent_classifier import IntentClassifier

__all__ = [
    "VosEngine",
    "VosResult",
    "get_vos_engine",
    "ToolRegistry",
    "VosTool",
    "build_default_registry",
    "IntentClassifier",
]
