"""
Tool Output Sanitization (AA2 mitigation — Indirect Prompt Injection)
======================================================================

Boundary-layer sanitizer applied to **tool output** before it is appended
to LangGraph state (where the next model turn will read it as context).
AA1 (``prompt_sanitization.py``) protects the user→LLM boundary; this
module protects the tool→LLM boundary.

The threat: any agent tool that returns text the model will read
(``web_search`` results, scraped pages, file contents, ``file_analyzer``
output) is an injection vector. An attacker controls a page the agent
fetches → the fetched HTML contains ``"Ignore previous instructions and
…"`` → the model treats that as instructions because LLMs do not
distinguish data-channel from instruction-channel.

Design (mirrors ``prompt_sanitization.py`` deliberately):
  * Deterministic regex + character-class checks. No LLM-in-the-loop.
  * Per-source length cap — TRUNCATE, never reject. A failed search is
    worse than a truncated one.
  * Two-stage handling:
      1. **Silent strip** of obfuscation characters and HTML-script
         vectors (web_search only). These never have a legitimate place
         in tool output the model needs to read.
      2. **Wrap, do not reject** content that triggers a prompt-injection
         pattern: the suspect text is enclosed in
         ``[UNTRUSTED-DATA-START] … [UNTRUSTED-DATA-END]`` tags so the
         downstream system prompt can be taught to treat it as data, not
         instructions.
  * The wrap is **informational only** until the system prompt is updated
    to honor it. That work is owned by the next deliverable; see the
    ``docs/OWASP_AGENTIC_MAPPING.md`` AA2 residual-gap note.

Why "wrap, don't reject" — tool output is unbounded in form. A web page
about "prompt injection research" legitimately contains the phrase
"ignore previous instructions". Rejecting on detection would break that
use case. Wrapping it as data preserves the content, signals to the model
it should not be treated as control, and gives forensics a flag.

Returns a :class:`ToolOutputSanitizationResult` mirroring the AA1 result
type's contract:
  * ``cleaned``: the text to forward downstream (possibly truncated,
    obfuscation-stripped, HTML-script-stripped, and wrapped).
  * ``detected_patterns``: ordered list of detection-name strings.
  * ``is_suspicious``: True iff a content-level injection pattern fired.
  * ``truncated``: True iff the per-source length cap was hit.
  * ``wrapped``: True iff the cleaned text is enclosed in
    ``[UNTRUSTED-DATA-*]`` tags.

OWASP mapping: AA2 (Indirect Prompt Injection), partial mitigation.

What this catches:
  - Same five content-level injection families as AA1
    (``ignore_previous_instructions``, ``dan_jailbreak``,
    ``system_prompt_leak``, ``role_marker_spoof``,
    ``markdown_link_smuggling``).
  - HTML ``<script>``, ``<iframe>``, ``<object>`` tags inside web search
    results (substring strip; not a full HTML parse — that would expand
    the dependency footprint for marginal benefit).
  - ``javascript:`` / ``vbscript:`` / ``data:`` URLs in ``href=`` / ``src=``
    attributes inside web search results.
  - ANSI escapes, zero-width chars, bidi-override chars (shared with AA1).
  - Excessive length (truncated per source).

What this does NOT catch (honest limits):
  - The wrap is only as good as the system prompt that honors it.
    Until the system prompt is taught to treat ``[UNTRUSTED-DATA]``
    regions as data-channel only, the wrap is forensic-only.
  - Semantic injections that don't trip any of the five lexical
    patterns (e.g. an attacker who rewrites their payload in benign
    phrasing). A model-based screen (semantic firewall, Weeks 3-4) is
    the next layer.
  - Multi-modal injections (images with embedded text, OCR-able PDF
    pages). This sanitizer is text-only.
  - The HTML strip is substring-based; an attacker can construct
    pathological tag syntax that we miss. We are conservative on
    purpose — the layered defenses (CSP at the browser, sandbox at
    execution, bitmask gate at tool dispatch) bound the blast radius
    of a successful bypass.

Tests live at ``backend/tests/test_tool_output_sanitization.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from ai.agents._charclass import strip_obfuscation
from services.semantic_firewall import (
    SemanticFirewallDecision,
    scan as semantic_firewall_scan,
)

# Re-use the LangGraph upgrade logger so detections land in the same audit
# stream as AA1 and the rest of the agent pipeline.
_logger = logging.getLogger("langgraph.upgrade")


# Marker emitted into ``cleaned`` when the firewall DENIES a tool output.
# Chosen to be visually distinctive, not collide with the AA2 wrap
# markers, and parseable in audit logs.
_FIREWALL_BLOCKED_MARKER = "[TOOL-OUTPUT-BLOCKED-BY-FIREWALL: {reason}]"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Per-source length caps in characters.
#:
#: Rationale: tool outputs have legitimately different sizes. A web search
#: returns a few pages of snippets; a file analyzer can return a much
#: larger summary; a generic tool should be smaller because we have no
#: reason to whitelist size. Caps are conservative against the model's
#: context window (every provider we route to supports >= 200K tokens,
#: i.e. roughly 600K chars — these caps are well below that) and against
#: prompt-stuffing exfiltration patterns.
#:
#: As with the AA1 sanitizer: TRUNCATE, do not reject. A truncated tool
#: result is better than a failed tool call from the caller's perspective;
#: the flag is recorded for audit.
MAX_CHARS_BY_SOURCE = {
    "web_search": 64 * 1024,  # 64 KB — search results can legitimately be large
    "file_analyzer": 256 * 1024,  # 256 KB — file content can be substantially larger
    "generic": 32 * 1024,  # 32 KB — same cap as the AA1 user-prompt limit
}

#: Default cap if an unknown ``source`` is passed.
_DEFAULT_MAX_CHARS = MAX_CHARS_BY_SOURCE["generic"]

#: Defensive cap on the detection list size — see the AA1 module for the
#: same pattern. With ~10 detection families this is mostly future-proofing.
_MAX_DETECTIONS = 32

#: Wrap markers. Chosen to be visually distinctive and not collide with
#: any markdown / ChatML / Llama / Gemma markers the AA1 sanitizer flags
#: as ``role_marker_spoof``. The pair MUST be kept in sync with whatever
#: system-prompt instruction teaches the model to honor them.
UNTRUSTED_START = "[UNTRUSTED-DATA-START]"
UNTRUSTED_END = "[UNTRUSTED-DATA-END]"


# ---------------------------------------------------------------------------
# Detection patterns — same 5 families as AA1, applied to tool output
# ---------------------------------------------------------------------------
# We re-compile the patterns here rather than importing from
# ``prompt_sanitization.py`` because:
#   (a) the AA1 module's patterns are module-private (``_RE_*``);
#   (b) future tuning of either layer should be independent — AA2 may
#       want to widen patterns (tool output is rarely a benign academic
#       discussion of jailbreaks), AA1 must keep false positives low.
# If the two pattern sets diverge, that's a feature, not a bug.

_RE_IGNORE_INSTRUCTIONS = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+"
    r"(?:all\s+(?:of\s+)?(?:your\s+)?|the\s+|your\s+|previous\s+|prior\s+|above\s+|earlier\s+)+"
    r"(?:previous\s+|prior\s+|earlier\s+|above\s+)?"
    r"(?:instructions?|rules?|prompts?|directives?|system\s+(?:prompt|message))",
    re.IGNORECASE,
)

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

_RE_SYSTEM_PROMPT_LEAK = re.compile(
    r"(?:"
    r"\b(?:reveal|show|print|output|repeat|display|expose|leak|give)\b"
    r"\s+(?:me\s+)?(?:your\s+|the\s+)?"
    r"(?:system\s+(?:prompt|message|instructions)|initial\s+instructions|original\s+(?:prompt|instructions)|hidden\s+instructions)"
    r"|\bwhat\s+(?:is|are)\s+your\s+(?:system\s+prompt|initial\s+instructions|original\s+instructions)"
    r")",
    re.IGNORECASE,
)

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

_RE_MARKDOWN_LINK_SMUGGLING = re.compile(
    r"\]\(\s*(?:javascript|data|vbscript|file)\s*:",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Source-specific HTML strip (web_search)
# ---------------------------------------------------------------------------
# Substring removal — NOT a full HTML parse. We accept that pathological
# tag syntax can evade this; the goal is to neutralize the common case
# (scraped pages containing ``<script>alert(1)</script>``) without pulling
# in a parser dependency. The layered defenses (CSP at the browser, the
# bitmask tool gate at dispatch, the sandbox at execution) bound the
# blast radius of anything that slips through.
#
# ``re.DOTALL`` lets ``.`` match newlines — important because real HTML
# from the web is multi-line.
_RE_HTML_SCRIPT_TAG = re.compile(
    r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_RE_HTML_IFRAME_TAG = re.compile(
    r"<\s*iframe\b[^>]*>.*?<\s*/\s*iframe\s*>",
    re.IGNORECASE | re.DOTALL,
)
_RE_HTML_OBJECT_TAG = re.compile(
    r"<\s*object\b[^>]*>.*?<\s*/\s*object\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Also catch self-closing / unterminated script & iframe / object opens —
# an attacker might omit the closing tag to bypass the greedy strip above.
# The looser "open tag only" form runs after the paired-tag strip.
_RE_HTML_SCRIPT_OPEN = re.compile(r"<\s*/?\s*script\b[^>]*>", re.IGNORECASE)
_RE_HTML_IFRAME_OPEN = re.compile(r"<\s*/?\s*iframe\b[^>]*>", re.IGNORECASE)
_RE_HTML_OBJECT_OPEN = re.compile(r"<\s*/?\s*object\b[^>]*>", re.IGNORECASE)

# ``href="javascript:..."`` / ``src="javascript:..."`` / equivalents.
# Strips the ``attr="..."`` pair entirely; the surrounding tag is left
# intact (defensive — we don't want to accidentally corrupt an otherwise
# benign anchor).
_RE_DANGEROUS_URL_ATTR = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)""" r"""(?P<quote>['"])\s*(?:javascript|vbscript|data)\s*:[^'"]*\2""",
    re.IGNORECASE,
)
# Also catch unquoted forms: ``href=javascript:foo()`` .
_RE_DANGEROUS_URL_ATTR_UNQUOTED = re.compile(
    r"""\b(?:href|src)\s*=\s*(?:javascript|vbscript|data)\s*:[^\s>]*""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ToolOutputSanitizationResult:
    """Outcome of :func:`sanitize_tool_output`.

    Attributes:
        cleaned: The text as it should be forwarded into LangGraph state.
            Obfuscation characters and (for ``web_search``) HTML script
            vectors are stripped; suspicious content is wrapped in
            ``[UNTRUSTED-DATA-START] … [UNTRUSTED-DATA-END]`` tags;
            truncated to :data:`MAX_CHARS_BY_SOURCE` if length exceeded.
        detected_patterns: Ordered list of detection-name strings (see
            module-level pattern constants). Empty when the output looks
            clean. Capped at :data:`_MAX_DETECTIONS`.
        is_suspicious: True when at least one *content-level* injection
            pattern fired. Pure-obfuscation strips, HTML-script strips,
            and length truncation do NOT set this on their own.
        truncated: True iff the per-source length cap was applied.
        wrapped: True iff the cleaned text is enclosed in
            ``[UNTRUSTED-DATA-*]`` tags (i.e. iff ``is_suspicious`` is
            True and the input was non-empty).
        source: The originating tool name passed in by the caller. Echoed
            back for telemetry convenience; not used in cleaning logic.
        original_length: Length of the text as received, before any
            cleaning or truncation.
    """

    cleaned: str
    detected_patterns: List[str] = field(default_factory=list)
    is_suspicious: bool = False
    truncated: bool = False
    wrapped: bool = False
    source: str = "generic"
    original_length: int = 0


# Names that count as "suspicious" — content-level injection patterns.
# Obfuscation / HTML / length flags are excluded; those are routine.
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


def _strip_html_vectors(text: str) -> Tuple[str, List[str]]:
    """Strip HTML script/iframe/object tags + dangerous URL attrs.

    Only invoked for ``source="web_search"`` because that's the surface
    that fetches arbitrary scraped HTML. Other sources should never have
    HTML in them; flagging it there would be a false positive risk.

    Returns ``(cleaned, detections)`` where ``detections`` is a list of
    fired-detection names (subset of ``["html_script", "html_iframe",
    "html_object", "dangerous_url"]``).
    """
    detections: List[str] = []
    cleaned = text

    # Pass 1: paired tags (handles the typical ``<script>...</script>`` case
    # and removes the body too — important so the script body doesn't leak
    # into the model context as plain text).
    if _RE_HTML_SCRIPT_TAG.search(cleaned):
        cleaned = _RE_HTML_SCRIPT_TAG.sub("", cleaned)
        detections.append("html_script")
    if _RE_HTML_IFRAME_TAG.search(cleaned):
        cleaned = _RE_HTML_IFRAME_TAG.sub("", cleaned)
        detections.append("html_iframe")
    if _RE_HTML_OBJECT_TAG.search(cleaned):
        cleaned = _RE_HTML_OBJECT_TAG.sub("", cleaned)
        detections.append("html_object")

    # Pass 2: orphan open / close tags (attacker omits the closer to evade
    # pass 1). Only flag if we didn't already flag from pass 1, to avoid
    # double-counting.
    if _RE_HTML_SCRIPT_OPEN.search(cleaned):
        cleaned = _RE_HTML_SCRIPT_OPEN.sub("", cleaned)
        if "html_script" not in detections:
            detections.append("html_script")
    if _RE_HTML_IFRAME_OPEN.search(cleaned):
        cleaned = _RE_HTML_IFRAME_OPEN.sub("", cleaned)
        if "html_iframe" not in detections:
            detections.append("html_iframe")
    if _RE_HTML_OBJECT_OPEN.search(cleaned):
        cleaned = _RE_HTML_OBJECT_OPEN.sub("", cleaned)
        if "html_object" not in detections:
            detections.append("html_object")

    # Pass 3: javascript:/vbscript:/data: URLs in href/src.
    flagged_url = False
    if _RE_DANGEROUS_URL_ATTR.search(cleaned):
        cleaned = _RE_DANGEROUS_URL_ATTR.sub("", cleaned)
        flagged_url = True
    if _RE_DANGEROUS_URL_ATTR_UNQUOTED.search(cleaned):
        cleaned = _RE_DANGEROUS_URL_ATTR_UNQUOTED.sub("", cleaned)
        flagged_url = True
    if flagged_url:
        detections.append("dangerous_url")

    return cleaned, detections


def sanitize_tool_output(text: str, source: str = "generic") -> ToolOutputSanitizationResult:
    """Sanitize text returned by a tool before it enters LangGraph state.

    This is the AA2 (indirect prompt injection) boundary layer. It runs
    *between* tool execution and state append; see the wire points in
    :mod:`ai.agents.tools_api` (``web_search``, ``file_analyzer``).

    Args:
        text: Raw text returned by the tool. ``None`` and non-string
            inputs are coerced to empty string (defensive — the tool
            wrapper should already ensure str).
        source: Tool identifier. One of ``"web_search"``,
            ``"file_analyzer"``, or ``"generic"``. Controls the per-source
            length cap and whether HTML-vector stripping runs. Unknown
            sources fall back to the generic 32 KB cap.

    Returns:
        A :class:`ToolOutputSanitizationResult`. ``cleaned`` is always a
        string (possibly empty) and is safe to feed downstream.
    """
    # Type defense — never let a non-string crash the agent loop.
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    original_length = len(text)
    detected: List[str] = []

    # 0. Semantic firewall (Stage 1, Day 6) — banned-substring check that
    # catches paraphrased / role-reset / prompt-extraction phrases the
    # five lexical regexes downstream do not.
    #
    # Wired BEFORE the existing detection pipeline so a firewall DENY
    # short-circuits with a clear marker; a firewall TRANSFORM
    # contributes its reason to ``detected_patterns`` and falls through
    # to the lexical layer (which will wrap in [UNTRUSTED-DATA] as
    # before).
    #
    # Defensive: any internal firewall failure is swallowed; the
    # downstream sanitizer remains active (defense-in-depth — we never
    # want the firewall itself to become a denial-of-service surface).
    if text and text.strip():
        try:
            firewall_verdict = semantic_firewall_scan(text, context={"source": source, "layer": "tool_output"})
        except Exception as exc:  # noqa: BLE001 — intentional broad catch
            _logger.warning(
                "[TOOL_OUTPUT_SANITIZE] firewall scan raised %s — "
                "proceeding (defense-in-depth: lexical layer remains active).",
                exc,
            )
            firewall_verdict = None

        if firewall_verdict is not None:
            if firewall_verdict.decision is SemanticFirewallDecision.DENY:
                # Replace the tool output with a marker; the downstream
                # model never sees the attacker text. ``is_suspicious``
                # and ``wrapped`` are both True so the caller treats this
                # as a content-level block.
                blocked_marker = _FIREWALL_BLOCKED_MARKER.format(reason=firewall_verdict.reason)
                _logger.warning(
                    "[TOOL_OUTPUT_SANITIZE] source=%s firewall DENY "
                    "reason=%s confidence=%.2f orig_len=%d — output "
                    "blocked.",
                    source,
                    firewall_verdict.reason,
                    firewall_verdict.confidence,
                    original_length,
                )
                return ToolOutputSanitizationResult(
                    cleaned=blocked_marker,
                    detected_patterns=["firewall_blocked", firewall_verdict.reason],
                    is_suspicious=True,
                    truncated=False,
                    wrapped=True,
                    source=source,
                    original_length=original_length,
                )
            if firewall_verdict.decision is SemanticFirewallDecision.TRANSFORM:
                # TRANSFORM = log the verdict, contribute the reason to
                # the detection list, and fall through. The AA2 lexical
                # layer below will wrap suspect content in
                # [UNTRUSTED-DATA] markers as part of its normal flow.
                detected.append(f"firewall_transform:{firewall_verdict.reason}")

    # 1. Empty / whitespace-only output. Flag and return early — there's
    # nothing else to do, and an empty result is not suspicious on its own.
    if not text or not text.strip():
        detected.append("empty_output")
        result = ToolOutputSanitizationResult(
            cleaned="",
            detected_patterns=detected,
            is_suspicious=False,
            truncated=False,
            wrapped=False,
            source=source,
            original_length=original_length,
        )
        return result

    cleaned = text

    # 2. Strip pure-obfuscation chars (ANSI, zero-width, bidi). Shared
    # helper with the AA1 sanitizer — see ``_charclass.py``.
    cleaned, obfuscation_hits = strip_obfuscation(cleaned)
    detected.extend(obfuscation_hits)

    # 3. Source-specific HTML strip. ``web_search`` is the only source
    # that legitimately returns scraped HTML; running this on
    # ``file_analyzer`` output could clobber a code sample containing a
    # ``<script>`` literal the user explicitly wants analyzed.
    if source == "web_search":
        cleaned, html_hits = _strip_html_vectors(cleaned)
        detected.extend(html_hits)

    # 4. Content-level injection patterns. *Flag*, then *wrap* — we do
    # not reject. See module docstring for rationale.
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

    is_suspicious = any(name in _SUSPICIOUS_PATTERN_NAMES for name in detected)

    # 5. Wrap suspect content in [UNTRUSTED-DATA] tags. This is the
    # forward-looking hook: the wrap is informational until the system
    # prompt is taught to treat wrapped regions as data-channel only.
    # Done BEFORE the length cap so the cap applies to the wrapped form
    # (an attacker shouldn't be able to push the closing tag out of the
    # window by padding the content).
    wrapped = False
    if is_suspicious:
        cleaned = f"{UNTRUSTED_START}\n{cleaned}\n{UNTRUSTED_END}"
        wrapped = True

    # 6. Per-source length cap. TRUNCATE (do not reject) and flag.
    max_chars = MAX_CHARS_BY_SOURCE.get(source, _DEFAULT_MAX_CHARS)
    truncated = False
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
        detected.append("length_exceeded")
        truncated = True
        # If we truncated a wrapped result, append the end marker so the
        # data boundary is still well-formed. Worth a small overhead
        # because a truncated wrapper without its closing tag is worse
        # than a slightly-over-cap wrapped result.
        if wrapped and not cleaned.endswith(UNTRUSTED_END):
            # Reserve room for the end marker by trimming a bit more.
            tail = f"\n{UNTRUSTED_END}"
            cleaned = cleaned[: max_chars - len(tail)] + tail

    # 7. Bound detection list defensively.
    if len(detected) > _MAX_DETECTIONS:
        detected = detected[:_MAX_DETECTIONS]

    if detected:
        # WARNING when content-level injection detected; INFO for routine
        # obfuscation / HTML / length flags only.
        log_method = _logger.warning if is_suspicious else _logger.info
        log_method(
            "[TOOL_OUTPUT_SANITIZE] source=%s detections=%s suspicious=%s "
            "wrapped=%s truncated=%s orig_len=%d cleaned_len=%d",
            source,
            ",".join(detected),
            is_suspicious,
            wrapped,
            truncated,
            original_length,
            len(cleaned),
        )

    return ToolOutputSanitizationResult(
        cleaned=cleaned,
        detected_patterns=detected,
        is_suspicious=is_suspicious,
        truncated=truncated,
        wrapped=wrapped,
        source=source,
        original_length=original_length,
    )


__all__ = [
    "MAX_CHARS_BY_SOURCE",
    "ToolOutputSanitizationResult",
    "UNTRUSTED_END",
    "UNTRUSTED_START",
    "sanitize_tool_output",
]
