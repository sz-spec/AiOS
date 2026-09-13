"""
Prompt Sanitization (AA1 mitigation — Direct Prompt Injection)
==============================================================

Boundary-layer sanitizer applied to user-supplied prompts before they reach
the LangGraph multi-agent pipeline. This is one defense layer; it is **not**
a substitute for the layered defenses (tool permission bitmask, sandbox,
guardrails AST gate, rate limit, etc.) that bound the blast radius of any
injection that does slip past.

Design:
  * Deterministic regex + character-class checks. No LLM-in-the-loop.
  * Conservative — designed to keep false positives low. The only content
    actually *removed* from the prompt is obfuscation characters that have
    no legitimate place in a user prompt (ANSI escapes, zero-width chars,
    bidi-override chars). Suspected jailbreak text is *flagged* but not
    rewritten — the LLM still sees it. The flag lets the caller audit-log
    and (in future) refuse the request.
  * Length policy: TRUNCATE (with flag), not reject. Rationale at
    ``MAX_PROMPT_CHARS`` below.

Returns a :class:`SanitizationResult` so the caller can:
  * use ``cleaned`` as the prompt to feed forward;
  * log ``detected_patterns`` for forensics;
  * branch on ``is_suspicious`` for stricter modes (refuse, ask-for-confirm).

OWASP mapping: AA1 (Direct Prompt Injection), partial mitigation.
What this catches:
  - "ignore previous instructions" family
  - DAN / "you are now ..." jailbreak preambles
  - "reveal your system prompt" family
  - leading role-marker spoofs (Human:, Assistant:, <|im_start|>, ...)
  - markdown links with javascript:/data: URIs (link smuggling)
  - ANSI escape sequences
  - zero-width characters (ZWSP, ZWNJ, ZWJ, BOM)
  - Unicode bidi-override characters (RLO etc.)
  - excessive length (truncated)
  - empty prompt

What this does NOT catch (honest limits):
  - Indirect prompt injection (text smuggled in via tool output / fetched
    content / files). That is AA2 and is owned (as of Day 3) by
    :mod:`ai.agents.tool_output_sanitization` — the tool→LLM boundary
    layer. Semantic firewall (Weeks 3-4) will add a stronger second stage
    on top.
  - Paraphrased / obfuscated jailbreaks ("disregard the above rules" etc.
    are caught by some patterns but the search space is unbounded).
  - Multi-step social-engineering injections that look benign per message.
  - Cross-language injections (patterns are English-leaning).

Tests live at ``backend/tests/test_prompt_sanitization.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

from ai.agents._charclass import (
    BIDI_OVERRIDE_CHARS,
    RE_ANSI_ESCAPE,
    RE_BIDI_OVERRIDE,
    RE_ZERO_WIDTH,
    ZERO_WIDTH_CHARS,
)

# Re-use the LangGraph upgrade logger so detections land in the same audit
# stream as the rest of the agent pipeline. The caller may also log via its
# own logger; this is just a default.
_logger = logging.getLogger("langgraph.upgrade")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Hard cap on prompt size in characters.
#:
#: Rationale: VOS3 v20.0 Stage 1 already enforces a 100 KB Pydantic body cap
#: at the HTTP boundary (see CLAUDE.md). 32_000 characters is a *conservative*
#: secondary cap applied closer to the LLM: well over a realistic single-turn
#: prompt (a wall of text from a serious user is typically <8 K chars), well
#: under the model context window of every provider we route to, and below
#: the threshold where prompt-stuffing attacks become economical.
#:
#: We TRUNCATE rather than REJECT. Rejecting an over-long benign prompt
#: produces a worse user experience than silently capping; the truncation is
#: recorded as a detection flag so the caller can audit-log it.
MAX_PROMPT_CHARS = 32_000

#: Maximum number of detected patterns to retain. Defensive cap so a
#: pathological input cannot make this function allocate unboundedly.
_MAX_DETECTIONS = 32


# ---------------------------------------------------------------------------
# Detection pattern constants (exported for tests / future tuning)
# ---------------------------------------------------------------------------

# All regexes are compiled once at module load.
# Conventions: case-insensitive where text is English; anchored where the
# pattern is only meaningful at line start; non-greedy where the pattern
# spans across content.

# "Ignore previous instructions" family. Anchored loosely; we accept a few
# common rephrasings but do NOT try to enumerate every paraphrase — the
# expectation is that this catches the lazy attempts, not the determined
# adversary.
_RE_IGNORE_INSTRUCTIONS = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+"
    r"(?:all\s+(?:of\s+)?(?:your\s+)?|the\s+|your\s+|previous\s+|prior\s+|above\s+|earlier\s+)+"
    r"(?:previous\s+|prior\s+|earlier\s+|above\s+)?"
    r"(?:instructions?|rules?|prompts?|directives?|system\s+(?:prompt|message))",
    re.IGNORECASE,
)

# DAN / "you are now ..." family. The "DAN" jailbreak is the canonical
# example; "do anything now" and "you are now <X>" are the structural
# variants we explicitly call out.
_RE_DAN_JAILBREAK = re.compile(
    r"(?:"
    r"\bDAN\b\s*(?:mode|prompt|jailbreak)?"
    r"|\bdo\s+anything\s+now\b"
    r"|\byou\s+are\s+now\s+(?:DAN|in\s+DAN\s+mode|jailbroken|unrestricted|an?\s+unrestricted)"
    r"|\bpretend\s+(?:to\s+be|you\s+are)\s+(?:DAN|jailbroken|an?\s+unrestricted)"
    r"|\bdeveloper\s+mode\s+enabled\b"
    r")",
    re.IGNORECASE,
)

# "Reveal your system prompt" family.
#
# Structure: <verb> [me] [your|the] <target>. The "me" and "your|the"
# fillers are independently optional so we match "show me your", "show
# the", "show me the", "reveal your", "print your", etc.
_RE_SYSTEM_PROMPT_LEAK = re.compile(
    r"(?:"
    r"\b(?:reveal|show|print|output|repeat|display|expose|leak|give)\b"
    r"\s+(?:me\s+)?(?:your\s+|the\s+)?"
    r"(?:system\s+(?:prompt|message|instructions)|initial\s+instructions|original\s+(?:prompt|instructions)|hidden\s+instructions)"
    r"|\bwhat\s+(?:is|are)\s+your\s+(?:system\s+prompt|initial\s+instructions|original\s+instructions)"
    r")",
    re.IGNORECASE,
)

# Role-marker spoofs. We look for these in two contexts:
#   (a) leading on a fresh line — strong signal of a spoofed turn boundary;
#   (b) anywhere — weaker signal, still worth flagging.
# To keep false positives low (e.g. someone writing "Human: a person" in a
# legitimate context about UX writing), we only flag (a) — the line-leading
# form. The anywhere check is bypassed here intentionally.
_RE_ROLE_MARKER_SPOOF = re.compile(
    r"(?:^|\n)\s*"
    r"(?:"
    r"(?:Human|Assistant|System|User|AI|Model)\s*:"
    r"|<\|(?:im_start|im_end|start_header_id|end_header_id|system|user|assistant)\|>"
    r"|<\|(?:start|end)_of_turn\|>"
    r"|\[INST\]|\[/INST\]"
    r"|###\s*(?:System|Instruction|Human|Assistant)"
    r")",
    re.IGNORECASE,
)

# Markdown link smuggling: [text](javascript:...) or [text](data:...) or
# [text](vbscript:...). These should never appear in a benign user prompt.
_RE_MARKDOWN_LINK_SMUGGLING = re.compile(
    r"\]\(\s*(?:javascript|data|vbscript|file)\s*:",
    re.IGNORECASE,
)

# Obfuscation character classes (ANSI escapes, zero-width chars, bidi-override).
# Definitions live in :mod:`ai.agents._charclass` and are shared with the AA2
# tool-output sanitizer. Module-level aliases preserved with the historical
# underscore-prefixed names so existing readers / tests don't need updates.
_RE_ANSI_ESCAPE = RE_ANSI_ESCAPE
_ZERO_WIDTH_CHARS = ZERO_WIDTH_CHARS
_RE_ZERO_WIDTH = RE_ZERO_WIDTH
_BIDI_OVERRIDE_CHARS = BIDI_OVERRIDE_CHARS
_RE_BIDI_OVERRIDE = RE_BIDI_OVERRIDE


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SanitizationResult:
    """Outcome of :func:`sanitize_user_prompt`.

    Attributes:
        cleaned: The prompt as it should be forwarded downstream. Obfuscation
            characters (zero-width, bidi, ANSI) are stripped; jailbreak-style
            phrases are left intact so the downstream model still sees them
            and so legitimate phrasings (e.g. a user asking about jailbreaks
            in an academic context) are not rewritten under us. Truncated to
            ``MAX_PROMPT_CHARS`` if length exceeded.
        detected_patterns: List of pattern-name strings (see constants in
            this module). Empty when the prompt looks clean. Capped at
            :data:`_MAX_DETECTIONS` to bound memory.
        is_suspicious: True when at least one *content-level* injection
            pattern fired. Pure-obfuscation strips (zero-width / bidi /
            ANSI) and length truncation alone do NOT set this — those are
            handled silently — but they do appear in ``detected_patterns``
            so the caller can still audit them.
        original_length: Length of the prompt as received, before any
            cleaning or truncation. Useful for telemetry.
    """

    cleaned: str
    detected_patterns: List[str] = field(default_factory=list)
    is_suspicious: bool = False
    original_length: int = 0


# Patterns that count as "suspicious" (set is_suspicious=True). Length and
# pure-obfuscation strips are excluded — they are routine boundary work.
_SUSPICIOUS_PATTERN_NAMES = frozenset(
    {
        "ignore_previous_instructions",
        "dan_jailbreak",
        "system_prompt_leak",
        "role_marker_spoof",
        "markdown_link_smuggling",
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_user_prompt(prompt: str) -> SanitizationResult:
    """Sanitize a single user-supplied prompt.

    This is a deterministic, regex-only pre-check. It is the AA1 boundary
    layer; it does *not* replace per-tool permission gates, sandbox
    enforcement, or the guardrails AST gate downstream.

    Args:
        prompt: The raw user prompt as received from the API surface.
            ``None`` and non-string inputs are coerced to empty string
            for safety; the caller should still validate type upstream.

    Returns:
        A :class:`SanitizationResult`. The ``cleaned`` field is always a
        string (possibly empty) and is safe to feed downstream.
    """
    # Type safety: do not let a non-string crash the whole pipeline. The
    # API layer should already enforce ``str`` via Pydantic; this is belt
    # and braces.
    if not isinstance(prompt, str):
        prompt = "" if prompt is None else str(prompt)

    original_length = len(prompt)
    detected: List[str] = []

    # 1. Empty / whitespace-only prompt. Flag and return early — there's
    # nothing else to scan.
    if not prompt or not prompt.strip():
        detected.append("empty_prompt")
        return SanitizationResult(
            cleaned="",
            detected_patterns=detected,
            is_suspicious=False,
            original_length=original_length,
        )

    cleaned = prompt

    # 2. Strip pure-obfuscation chars (ANSI, zero-width, bidi). These have
    # no legitimate place in a user prompt and removing them is safe.
    if _RE_ANSI_ESCAPE.search(cleaned):
        cleaned = _RE_ANSI_ESCAPE.sub("", cleaned)
        detected.append("ansi_escape")
    if _RE_ZERO_WIDTH.search(cleaned):
        cleaned = _RE_ZERO_WIDTH.sub("", cleaned)
        detected.append("zero_width")
    if _RE_BIDI_OVERRIDE.search(cleaned):
        cleaned = _RE_BIDI_OVERRIDE.sub("", cleaned)
        detected.append("bidi_override")

    # 3. Content-level injection patterns. We *flag* but do not rewrite —
    # benign uses (e.g. asking about prompt-injection in an academic
    # context) exist and we'd rather get a flagged-but-passed result than
    # mangle the user's text.
    if _RE_IGNORE_INSTRUCTIONS.search(cleaned):
        detected.append("ignore_previous_instructions")
    if _RE_DAN_JAILBREAK.search(cleaned):
        detected.append("dan_jailbreak")
    if _RE_SYSTEM_PROMPT_LEAK.search(cleaned):
        detected.append("system_prompt_leak")
    if _RE_ROLE_MARKER_SPOOF.search(cleaned):
        detected.append("role_marker_spoof")
    if _RE_MARKDOWN_LINK_SMUGGLING.search(cleaned):
        detected.append("markdown_link_smuggling")

    # 4. Length cap. Truncate (do not reject) and flag. We truncate AFTER
    # the obfuscation strips so the user's "real" content gets the full
    # budget; we cap BEFORE returning so downstream sees a bounded string.
    if len(cleaned) > MAX_PROMPT_CHARS:
        cleaned = cleaned[:MAX_PROMPT_CHARS]
        detected.append("length_exceeded")

    # 5. Bound detection list (defense against a pathological input
    # generating wild numbers of detections — currently 9 unique slots, so
    # mostly a future-proofing belt).
    if len(detected) > _MAX_DETECTIONS:
        detected = detected[:_MAX_DETECTIONS]

    is_suspicious = any(name in _SUSPICIOUS_PATTERN_NAMES for name in detected)

    if detected:
        # Log at WARNING when suspicious, INFO when only obfuscation/length.
        # This way ops can alert on WARNING-level prompt-sanitization
        # signals without drowning in routine truncation events.
        log_method = _logger.warning if is_suspicious else _logger.info
        log_method(
            "[PROMPT_SANITIZE] detections=%s suspicious=%s orig_len=%d cleaned_len=%d",
            ",".join(detected),
            is_suspicious,
            original_length,
            len(cleaned),
        )

    return SanitizationResult(
        cleaned=cleaned,
        detected_patterns=detected,
        is_suspicious=is_suspicious,
        original_length=original_length,
    )


__all__ = [
    "MAX_PROMPT_CHARS",
    "SanitizationResult",
    "sanitize_user_prompt",
]
