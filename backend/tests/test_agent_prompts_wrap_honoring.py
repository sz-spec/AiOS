"""
Verifies every system prompt teaches the model to honor the
``[UNTRUSTED-DATA-START] … [UNTRUSTED-DATA-END]`` wrap.

This is the test side of the Day-4 AA2 follow-on: the wrap-emitter
landed Day 3 in ``backend/ai/agents/tool_output_sanitization.py``;
Day 4 makes prompts honor it. If a new role is added to
``AGENT_PROMPTS`` or ``DEFAULT_PROMPTS`` without the directive, this
test fails and pins the regression.

What is intentionally NOT tested here:
  * The model actually obeying the directive at runtime — that is a
    probabilistic behavior, not a hard guarantee (see the AA2 honest
    expectations note).
  * The exact wording — only the load-bearing parts (the marker strings
    and a refusal cue) are pinned, so future tone polish does not
    spuriously break the test.

Cross-references:
  * ``backend/ai/agents/tool_output_sanitization.py`` defines
    ``UNTRUSTED_START`` / ``UNTRUSTED_END`` — imported here verbatim
    rather than reproduced as string literals.
  * ``docs/OWASP_AGENTIC_MAPPING.md`` AA2 — residual-gap accounting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from ai.agents.multi_agent import AGENT_PROMPTS, AgentRole  # noqa: E402
from ai.agents.tool_output_sanitization import (  # noqa: E402
    UNTRUSTED_END,
    UNTRUSTED_START,
)
from api.agents_routes import DEFAULT_PROMPTS  # noqa: E402

# ---------------------------------------------------------------------------
# AGENT_PROMPTS (multi_agent.py) — full-length per-role prompts
# ---------------------------------------------------------------------------


class TestAgentPromptsHonorWrap:
    """Every role in AGENT_PROMPTS must include the wrap directive."""

    @pytest.mark.parametrize("role", list(AgentRole))
    def test_role_prompt_mentions_markers(self, role: AgentRole) -> None:
        prompt = AGENT_PROMPTS.get(role)
        assert prompt is not None, f"AGENT_PROMPTS missing entry for {role}"
        # The exact marker strings — must match the sanitizer module so
        # the wrap-emitter and the wrap-honorer can never drift.
        assert UNTRUSTED_START in prompt, (
            f"Role {role} prompt is missing the {UNTRUSTED_START} marker. " "Add the 'Untrusted Data Handling' section."
        )
        assert UNTRUSTED_END in prompt, f"Role {role} prompt is missing the {UNTRUSTED_END} marker."

    @pytest.mark.parametrize("role", list(AgentRole))
    def test_role_prompt_has_refusal_cue(self, role: AgentRole) -> None:
        prompt = AGENT_PROMPTS.get(role) or ""
        lower = prompt.lower()
        # Load-bearing cue: the model must be told these markers are
        # data, not instructions, and that wrapped instructions are
        # to be refused. Pin both halves to catch accidental softening.
        assert "data" in lower and "instructions" in lower, f"Role {role} prompt does not contrast data vs instructions"
        assert "refuse" in lower or "report" in lower, f"Role {role} prompt has no refusal / report cue"


# ---------------------------------------------------------------------------
# DEFAULT_PROMPTS (agents_routes.py) — short API-level prompts
# ---------------------------------------------------------------------------


class TestDefaultPromptsHonorWrap:
    """Every role in DEFAULT_PROMPTS must include the compressed wrap
    directive. Strings are intentionally short here, so we pin both
    marker presence and the refuse/report cue."""

    @pytest.mark.parametrize("role", sorted(DEFAULT_PROMPTS.keys()))
    def test_role_prompt_mentions_markers(self, role: str) -> None:
        prompt = DEFAULT_PROMPTS[role]
        assert UNTRUSTED_START in prompt, f"DEFAULT_PROMPTS[{role!r}] missing {UNTRUSTED_START}"
        assert UNTRUSTED_END in prompt, f"DEFAULT_PROMPTS[{role!r}] missing {UNTRUSTED_END}"

    @pytest.mark.parametrize("role", sorted(DEFAULT_PROMPTS.keys()))
    def test_role_prompt_has_refusal_cue(self, role: str) -> None:
        prompt = DEFAULT_PROMPTS[role].lower()
        assert "data" in prompt and "instructions" in prompt
        assert "refuse" in prompt or "report" in prompt
