"""
VOS Intent Classifier
=====================
Lightweight classifier that determines if a user message contains
a system-level intent that VOS should handle.

Uses rule-based pre-filter + LLM classification (gpt-4o-mini).
"""

import os
import re
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Intent categories that VOS can handle
INTENT_CATEGORIES = [
    "agent_management",
    "memory_management",
    "vcore_management",
    "system_query",
    "settings_management",
    "codegen",
]

# Rule-based keyword patterns for fast pre-filter
_KEYWORD_PATTERNS: Dict[str, List[str]] = {
    "agent_management": [
        r"\b(create|make|add|build|spawn|new)\b.*\bagent\b",
        r"\bagent\b.*\b(create|make|add|build|spawn|new)\b",
        r"\b(delete|remove|destroy|kill)\b.*\bagent\b",
        r"\b(list|show|get|display)\b.*\bagents?\b",
        r"\b(update|change|modify|edit)\b.*\bagent\b",
        r"\bagent\b.*\b(update|change|modify|edit)\b",
        r"\brun\b.*\bagent\b.*\btask\b",
    ],
    "memory_management": [
        r"\b(remember|store|save|memorize|keep)\b.*\b(this|that|memory|note)\b",
        r"\b(recall|retrieve|find|search)\b.*\b(memory|memories|remember)\b",
        r"\b(what do you|do you)\b.*\bremember\b",
        r"\bstore\b.*\bas\b.*\bmemory\b",
        r"\brecent\b.*\bmemories\b",
    ],
    "vcore_management": [
        r"\b(list|show|get)\b.*\bentit(y|ies)\b",
        r"\b(create|add|new)\b.*\brecord\b",
        r"\b(list|show|get)\b.*\bworkflows?\b",
        r"\b(trigger|run|start|execute)\b.*\bworkflow\b",
        r"\bv-?core\b",
    ],
    "system_query": [
        r"\b(system|server)\b.*\b(health|status)\b",
        r"\b(health|status)\b.*\b(system|server|check)\b",
        r"\b(get|show|check)\b.*\bmetrics\b",
        r"\bcost\b.*\bbreakdown\b",
        r"\bhow much\b.*\b(cost|spent|spend)\b",
        r"\b(what|how)\b.*\b(running|status|health)\b.*\b(system|server|service)\b",
    ],
    "settings_management": [
        r"\b(check|show|get|what)\b.*\b(settings|api\s*keys?|configuration)\b",
        r"\bapi\s*keys?\b.*\b(configured|set|status)\b",
        r"\bdev\s*mode\b",
    ],
    "codegen": [
        r"\b(generate|create|write|build)\b.*\b(code|function|class|component|module)\b",
        r"\bcode\s*gen\b",
    ],
}

# Patterns that indicate a NON-system message (skip classification entirely)
_SKIP_PATTERNS = [
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|bye|goodbye)\s*[.!?]*$",
    r"^.{0,15}$",  # Very short messages (under 15 chars)
    r"^\?$",  # Just a question mark
]


class IntentClassifier:
    """Classifies messages into VOS intent categories."""

    def __init__(self):
        self._skip_re = [re.compile(p, re.IGNORECASE) for p in _SKIP_PATTERNS]
        self._keyword_re: Dict[str, List[re.Pattern]] = {
            cat: [re.compile(p, re.IGNORECASE) for p in patterns]
            for cat, patterns in _KEYWORD_PATTERNS.items()
        }

    def _should_skip(self, message: str) -> bool:
        """Check if message is obviously not a system command."""
        stripped = message.strip()
        return any(r.match(stripped) for r in self._skip_re)

    def _rule_based_classify(self, message: str) -> Optional[str]:
        """Try to classify using keyword patterns alone."""
        for category, patterns in self._keyword_re.items():
            for pattern in patterns:
                if pattern.search(message):
                    return category
        return None

    async def classify(
        self, message: str, context: Optional[List[Dict[str, str]]] = None
    ) -> Optional[str]:
        """
        Classify message intent.
        Returns intent category string or None if no system intent detected.
        """
        if self._should_skip(message):
            return None

        # Try rule-based first (fast path)
        rule_result = self._rule_based_classify(message)
        if rule_result:
            logger.info(f"VOS intent (rule-based): {rule_result}")
            return rule_result

        # Fall through to LLM classification
        return await self._llm_classify(message, context)

    async def _llm_classify(
        self, message: str, context: Optional[List[Dict[str, str]]] = None
    ) -> Optional[str]:
        """Use LLM to classify intent."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        # W2.5 — refuse to leak to api.openai.com under sovereign-strict
        # offline mode. The rule-based path above already returned for
        # high-confidence cases; here we degrade gracefully to None and
        # let the caller treat the message as ordinary conversation.
        try:
            from src.efficiency.router import (
                resolve_model_id,
                LocalInferenceRequiredError,
                _LOCALITY_PREFERENCE,
                _ALLOW_CLOUD_FALLBACK,
            )

            if _LOCALITY_PREFERENCE == "local-first" and not _ALLOW_CLOUD_FALLBACK:
                logger.info(
                    "intent_classifier: skipping cloud LLM classification "
                    "(local-first + cloud-fallback disabled). Returning None "
                    "so the message is treated as plain conversation."
                )
                return None
            try:
                model_id = resolve_model_id("intent_classifier", 2)
            except LocalInferenceRequiredError:
                logger.info("intent_classifier: strict air-gap; skipping LLM step.")
                return None
        except ImportError:
            # Router unavailable (test contexts) — fall back to the historical
            # default but log loudly so the audit trail is honest.
            logger.warning(
                "intent_classifier: src.efficiency.router unavailable; "
                "falling back to gpt-4o-mini default."
            )
            model_id = "gpt-4o-mini"

        try:
            import httpx

            categories_str = ", ".join(INTENT_CATEGORIES)
            system_prompt = (
                "You are an intent classifier for a system called VOS. "
                "Given a user message, determine if it contains a system-level action intent.\n\n"
                f"Categories: {categories_str}\n\n"
                "Rules:\n"
                "- agent_management: creating, listing, deleting, updating, or running agents\n"
                "- memory_management: storing, recalling, or browsing memories/knowledge\n"
                "- vcore_management: business entities, records, workflows\n"
                "- system_query: system health, metrics, costs, status checks\n"
                "- settings_management: API keys, configuration status\n"
                "- codegen: generating code from descriptions\n"
                "- null: normal conversation, questions, or anything that doesn't match above\n\n"
                "Respond with ONLY the category name or 'null'. Nothing else."
            )

            messages = [{"role": "system", "content": system_prompt}]
            if context:
                # Include last few messages for context
                for msg in context[-4:]:
                    messages.append(
                        {
                            "role": msg.get("role", "user"),
                            "content": msg.get("content", ""),
                        }
                    )
            messages.append({"role": "user", "content": message})

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model_id,
                        "messages": messages,
                        "max_tokens": 20,
                        "temperature": 0,
                    },
                    timeout=5.0,
                )

                if resp.status_code != 200:
                    logger.warning(f"VOS classifier LLM error: {resp.status_code}")
                    return None

                data = resp.json()
                result = data["choices"][0]["message"]["content"].strip().lower()

                if result in INTENT_CATEGORIES:
                    logger.info(f"VOS intent (LLM): {result}")
                    return result

                return None

        except Exception as e:
            logger.warning(f"VOS classifier error: {e}")
            return None
