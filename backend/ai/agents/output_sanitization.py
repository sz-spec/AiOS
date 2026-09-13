"""
Agent Output Sanitization (AA9 mitigation — Insecure Output Handling)
=====================================================================

Boundary-layer sanitizer applied to **LLM-emitted text** before it
reaches an HTTP client, a Convex record, or any other downstream
consumer. AA1 (``prompt_sanitization.py``) protects the user→LLM
boundary; AA2 (``tool_output_sanitization.py``) protects the tool→LLM
boundary; THIS module protects the LLM→sink boundary — the third egress
seam in the agent pipeline.

The threat: an LLM under prompt-injection (AA1/AA2 bypassed), a
fine-tuned model with backdoor triggers, or simply a model that
"helpfully" pastes an example API key — emits text that, when rendered
in a browser frontend, persisted in a database, piped into a shell, or
displayed to a user, becomes:

  * **XSS** — emitted ``<script>`` / ``<iframe>`` / ``javascript:`` URL
    surfaces against a frontend renderer that doesn't escape.
  * **Secret exfiltration** — leaked API key, AWS credential, GitHub
    token, Bearer token shipped over HTTP / stored in a DB row.
  * **Prompt echo** — model regurgitates adversarial training data or
    leaks the original system prompt by replaying role markers
    (``Human:`` / ``<|im_start|>`` / ``[INST]``).
  * **Renderer DoS** — same line repeated >20 times, an LLM stuck in a
    decoding loop. The frontend Markdown renderer chokes.

Design (mirrors ``tool_output_sanitization.py`` deliberately, since
this is the closer template — both layers do per-source policy + a
"wrap, don't reject" idiom + truncation + character-class strip):

  * Deterministic regex + character-class checks. No LLM-in-the-loop.
  * Per-sink length cap — TRUNCATE, never reject. A truncated response
    is worse UX than a refused one but better than a renderer crash.
  * Three-stage handling:
      1. **Silent strip** of obfuscation characters (ANSI / zero-width
         / bidi-override) — reused from ``_charclass.py``. These never
         have a legitimate place in agent output the user will see.
      2. **REDACT** secrets in-place. The action is destructive — the
         secret is replaced with ``[REDACTED-<type>]`` and
         ``is_suspicious=True`` is set. Rationale: shipping a leaked
         key is worst-case egress, and we have no use case for
         "showing the user the API key they accidentally pasted into
         the conversation". When in doubt, redact.
      3. **WRAP** prompt-payload emissions in
         ``[AGENT-OUTPUT-FLAGGED] … [END]`` tags so the downstream
         renderer / persistence layer can be taught to render these
         regions inert (gray box, "agent emitted prompt-style payload
         — review before trusting"). Analogous to Day-3's
         ``[UNTRUSTED-DATA]`` wrap, but for OUTPUT we own the wrap
         target (the wrap is here to *inform the user*, not the model).

  * **BLOCK** is reserved for the worst case: secret detected on a
    sink where shipping it externally is unambiguous damage
    (``chat_response`` over HTTP, ``code_block`` returned to a user
    who will paste it somewhere). For ``convex_record`` we redact but
    don't block — Convex sits behind app-level auth, the row is
    auditable, and a blocked agent run loses more than a redacted
    persisted log entry.

Sinks — what they mean and what policy each gets:

  * ``chat_response`` — text streamed back to the HTTP client. Length
    cap 128 KB (chat responses can be long). HTML-vector strip
    applied (the frontend may render Markdown / interpret embedded
    HTML in code-fenced blocks). Block on secrets.
  * ``convex_record`` — text persisted to Convex. Length cap 256 KB
    (persistent records can be larger). NO HTML strip — Convex
    stores raw text and the frontend escapes on render. Redact but
    do not block on secrets — the row is behind app auth.
  * ``code_block`` — text the user explicitly asked for as code
    (e.g. a generated source file). SKIP the HTML strip (legitimate
    code can contain ``<script>`` literals — the file_analyzer case).
    Length cap 1 MB. Block on secrets (paranoid: redact even inside
    code, because shipping a leaked key in a code block is identical
    damage to shipping it in a sentence).
  * ``generic`` — fall-through for any caller that hasn't picked a
    sink. Minimal: char-class strip, secret redact, length cap 64 KB.

Returns an :class:`OutputSanitizationResult` shaped like the AA2 result
type's contract:

  * ``cleaned`` — the text to forward to the sink (possibly redacted,
    HTML-stripped, prompt-wrapped, truncated).
  * ``detected_patterns`` — ordered list of detection names.
  * ``is_suspicious`` — True iff a secret was redacted or a prompt
    payload was detected.
  * ``truncated`` — True iff the per-sink length cap was hit.
  * ``blocked`` — True iff the decision matrix said "do not ship".
  * ``blocked_reason`` — short string identifying *why* if blocked.

OWASP mapping: AA9 (Insecure Output Handling), partial mitigation.

What this catches:
  - ``<script>``, ``<iframe>``, ``<object>``, ``<embed>`` tags in
    non-code sinks (substring strip; not a full HTML parse — same
    trade-off as AA2).
  - ``javascript:`` / ``data:text/html`` / ``vbscript:`` URLs in
    markdown link targets and HTML attributes.
  - API-key-shaped strings: OpenAI ``sk-…``, Anthropic ``sk-ant-…``,
    GitHub ``ghp_…``, AWS ``AKIA…``, generic Bearer tokens.
  - Prompt-payload emission — line-leading ``Human:`` / ``Assistant:``
    / ``<|im_start|>`` / ``[INST]``.
  - Excessive line repetition (same line ×20+).
  - ANSI escapes, zero-width chars, bidi-override chars.
  - Excessive length (per-sink cap, TRUNCATED).

What this does NOT catch (honest limits — see
``docs/OWASP_AGENTIC_MAPPING.md`` AA9 residual gaps):

  - **Novel key formats**. The secret regex set is conservative; an
    attacker who has the model emit a base64-blob of unknown prefix,
    or a JWT, or a private key in PEM form, slips through. We accept
    the trade-off to keep false-positive rate low (we don't want to
    redact every random base64 string a code-explanation agent
    emits).
  - **Encoded XSS**. The HTML strip is substring-based. Creative
    encodings — ``<scr<script>ipt>``, full-width Unicode look-alikes,
    SVG with embedded JS, polyglot payloads — can in principle
    evade. The frontend's CSP and Markdown renderer's own escaping
    are the layered defenses we rely on for the residual.
  - **Paraphrased prompt-payload echoes**. We match on the lexical
    role markers; an attacker who has the model paraphrase the same
    payload in prose ("act as the system administrator and …")
    bypasses the lexical filter. The next layer (semantic firewall,
    Weeks 3–5) catches the semantic form.
  - **DLP / PII regexes** beyond the secrets set. Email addresses,
    phone numbers, SSN-shaped strings are NOT redacted here — they
    are routine in legitimate output. A separate DLP pass (Track 7)
    is the right place for that.
  - **Cross-language injections**. The prompt-payload patterns are
    English / ChatML / Llama / Gemma-leaning.

Tests live at ``backend/tests/test_output_sanitization.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from ai.agents._charclass import strip_obfuscation

# Re-use the LangGraph upgrade logger so detections land in the same audit
# stream as AA1 / AA2 / agent telemetry.
_logger = logging.getLogger("langgraph.upgrade")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Per-sink length caps in characters.
#:
#: Rationale: each sink has a different blast radius for "too large".
#: A 1 MB chat response is a renderer DoS; a 1 MB code block is a normal
#: generated source file. A 256 KB Convex row is fine (the DB takes it);
#: a 256 KB chat response over SSE is borderline. These caps are
#: conservative against renderer behavior and against prompt-stuffing
#: exfiltration patterns (an attacker who's gotten the model to dump
#: secrets in bulk loses the secrets either way, but the truncation
#: cuts the volume).
#:
#: TRUNCATE, never reject — same policy as AA1 / AA2. A truncated
#: response is recorded as ``truncated=True``; the caller can audit-log
#: and surface a "[response truncated]" hint to the user.
MAX_CHARS_BY_SINK = {
    "chat_response": 128 * 1024,  # 128 KB — chat responses can be long
    "convex_record": 256 * 1024,  # 256 KB — persistent records can be larger
    "code_block": 1024 * 1024,  # 1 MB — code can be huge
    "generic": 64 * 1024,  # 64 KB — minimal fall-through cap
}

#: Default cap if an unknown ``sink`` is passed.
_DEFAULT_MAX_CHARS = MAX_CHARS_BY_SINK["generic"]

#: Defensive cap on the detection list size — see AA1 / AA2 for the same
#: pattern. With ~12 detection families this is mostly future-proofing.
_MAX_DETECTIONS = 32

#: Threshold for excessive-repetition detection. Same line repeated more
#: than this many consecutive times triggers truncation. 20 is the
#: smallest threshold large enough to not flag legitimate list output
#: ("- item\n- item\n- item\n…") but small enough to catch a stuck-loop
#: emission (which typically reaches the thousands very fast).
_REPETITION_THRESHOLD = 20

#: Wrap markers for prompt-payload echoes. Chosen to be visually distinct
#: from the AA2 ``[UNTRUSTED-DATA-*]`` markers — these flag content the
#: AGENT emitted, not content fed INTO the agent. The downstream
#: renderer / persistence layer can be taught to render these regions
#: inert (gray box, warning badge).
FLAGGED_START = "[AGENT-OUTPUT-FLAGGED]"
FLAGGED_END = "[END-AGENT-OUTPUT-FLAGGED]"


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# HTML injection vectors. Mirror the AA2 strip set but expanded with
# ``<embed>`` because output may render in a browser frontend and embed
# tags carry the same script-execution surface as object tags.
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
_RE_HTML_EMBED_TAG = re.compile(
    # Self-closing form ``<embed … />`` OR paired ``<embed …>…</embed>``.
    # We match either by greedy-up-to-``>`` for the open tag and then
    # optionally consume a paired close. Empty-element form is the
    # common case (embed is HTML void), so we handle both shapes.
    r"<\s*embed\b[^>]*>(?:.*?<\s*/\s*embed\s*>)?",
    re.IGNORECASE | re.DOTALL,
)
# Orphan-open forms — attacker omits the closer.
_RE_HTML_SCRIPT_OPEN = re.compile(r"<\s*/?\s*script\b[^>]*>", re.IGNORECASE)
_RE_HTML_IFRAME_OPEN = re.compile(r"<\s*/?\s*iframe\b[^>]*>", re.IGNORECASE)
_RE_HTML_OBJECT_OPEN = re.compile(r"<\s*/?\s*object\b[^>]*>", re.IGNORECASE)
_RE_HTML_EMBED_OPEN = re.compile(r"<\s*/?\s*embed\b[^>]*/?\s*>", re.IGNORECASE)

# Dangerous URL schemes in href / src / markdown link targets. We treat
# ``data:text/html`` as dangerous because that's the XSS-relevant form;
# benign ``data:image/png;base64,…`` URLs are NOT flagged here.
#
# Two flavors of pattern:
#   (a) ``javascript:`` / ``vbscript:`` — scheme + ``:`` is enough to
#       flag. Always XSS-relevant.
#   (b) ``data:text/html`` — must include the ``text/html`` MIME because
#       benign data: URIs (images, fonts) are common in legitimate
#       output. The MIME tail is matched WITHOUT requiring a further
#       ``[:;]`` delimiter because attackers use both
#       ``data:text/html,<script>`` (comma) and
#       ``data:text/html;base64,…`` (semicolon).
_RE_DANGEROUS_URL_ATTR = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)"""
    r"""(?P<quote>['"])\s*"""
    r"""(?:(?:javascript|vbscript)\s*:|data\s*:\s*text/html)[^'"]*\2""",
    re.IGNORECASE,
)
_RE_DANGEROUS_URL_ATTR_UNQUOTED = re.compile(
    r"""\b(?:href|src)\s*=\s*""" r"""(?:(?:javascript|vbscript)\s*:|data\s*:\s*text/html)[^\s>]*""",
    re.IGNORECASE,
)
# Markdown link form: ``[text](javascript:…)`` / ``[text](data:text/html…)``.
_RE_MARKDOWN_DANGEROUS_URL = re.compile(
    r"\]\(\s*(?:(?:javascript|vbscript)\s*:|data\s*:\s*text/html)[^)]*\)",
    re.IGNORECASE,
)

# Prompt-payload emission: the LLM emitted role markers that look like
# a chat boundary. Two flavors:
#   - ChatML / Llama / Gemma special tokens: ``<|im_start|>``, ``[INST]`` etc.
#     These have NO legitimate use in agent output (we don't expect the
#     agent to teach the user how to write ChatML; even when it does,
#     the wrap is fine — it's just informational, not destructive).
#   - Line-leading ``Human:`` / ``Assistant:`` / ``System:``. Same as
#     AA1's role-marker-spoof, applied to OUTPUT this time.
_RE_PROMPT_PAYLOAD = re.compile(
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


# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------
# These are deliberately conservative — we want low false-positive rate.
# Each pattern targets a *specific shape* an attacker / model would
# realistically emit. We do NOT try to catch every possible token form;
# DLP is a separate workstream.
#
# Order matters: more specific patterns first so they match before the
# generic ones. For example, OpenAI's "sk-ant-…" Anthropic form must be
# checked before the generic "sk-…" OpenAI form, otherwise the second
# would swallow the first.

# Anthropic API key: sk-ant- prefix, then 30+ url-safe chars.
_RE_ANTHROPIC_KEY = re.compile(
    r"\bsk-ant-[A-Za-z0-9_\-]{30,}\b",
)
# OpenAI API key: sk- prefix, then 20+ alphanumeric. Project keys start
# with sk-proj-; the prefix is still ``sk-`` so this pattern catches both.
# Excluded from the false-positive set: ``sk-ant-…`` (matched above).
_RE_OPENAI_KEY = re.compile(
    r"\bsk-(?!ant-)[A-Za-z0-9_\-]{20,}\b",
)
# GitHub personal access token: ghp_ + 30+ base62.
_RE_GITHUB_TOKEN = re.compile(
    r"\bghp_[A-Za-z0-9]{30,}\b",
)
# GitHub fine-grained PAT: github_pat_ + …
_RE_GITHUB_FINE_TOKEN = re.compile(
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
)
# AWS access key: AKIA + 16 uppercase alphanumerics. Anchored to word
# boundary so a path like ``/var/log/AKIA…`` still matches.
_RE_AWS_ACCESS_KEY = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b",
)
# Stripe live secret key: sk_live_ + 24+ char. Tests use sk_test_ and
# we let those through — leaking a test key in chat is annoying but
# not a credentials-exfil incident.
_RE_STRIPE_LIVE_KEY = re.compile(
    r"\bsk_live_[A-Za-z0-9]{24,}\b",
)
# Generic Bearer token. We require the literal "Bearer " prefix (case
# sensitive on the "B" to avoid matching "bearer in mind") followed by
# a long-enough opaque blob. 20+ base64-ish chars to keep FP rate low.
_RE_BEARER_TOKEN = re.compile(
    r"\bBearer\s+[A-Za-z0-9_\-\.=+/]{20,}\b",
)
# Generic "pk_" Stripe-flavored publishable-key shape (not actually a
# secret in Stripe's threat model, but other vendors use the same
# pattern for things that ARE secrets). Lower priority — redacted but
# does not block.
_RE_GENERIC_PK_KEY = re.compile(
    r"\bpk_(?:live|test)_[A-Za-z0-9]{20,}\b",
)


#: Secret detection table: regex → (redaction label, severity).
#: ``severity="critical"`` means "shipping this externally is worst-case
#: damage" — these trigger ``blocked=True`` on the chat_response and
#: code_block sinks. ``severity="warn"`` means "redact but don't block"
#: — these are flagged but the run proceeds.
_SECRET_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # Anthropic must be first — its prefix is a superset of OpenAI's.
    (_RE_ANTHROPIC_KEY, "anthropic-key", "critical"),
    (_RE_OPENAI_KEY, "openai-key", "critical"),
    (_RE_GITHUB_FINE_TOKEN, "github-token", "critical"),
    (_RE_GITHUB_TOKEN, "github-token", "critical"),
    (_RE_AWS_ACCESS_KEY, "aws-key", "critical"),
    (_RE_STRIPE_LIVE_KEY, "stripe-live-key", "critical"),
    (_RE_BEARER_TOKEN, "bearer-token", "critical"),
    (_RE_GENERIC_PK_KEY, "pk-key", "warn"),
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class OutputSanitizationResult:
    """Outcome of :func:`sanitize_agent_output`.

    Attributes:
        cleaned: The text as it should be forwarded to the sink.
            Obfuscation characters and (for non-code sinks) HTML script
            vectors stripped; detected secrets replaced with
            ``[REDACTED-<type>]``; prompt-payload emissions wrapped in
            ``[AGENT-OUTPUT-FLAGGED]…[END-AGENT-OUTPUT-FLAGGED]`` tags;
            truncated to :data:`MAX_CHARS_BY_SINK` if length exceeded.
            **If ``blocked=True``, ``cleaned`` is the empty string** —
            the caller should NOT ship cleaned to the sink; instead use
            ``blocked_reason`` to produce a refusal response.
        detected_patterns: Ordered list of detection-name strings (see
            module-level pattern constants). Empty when the output
            looks clean. Capped at :data:`_MAX_DETECTIONS`.
        is_suspicious: True when a secret was redacted OR a
            prompt-payload pattern fired. Pure HTML / obfuscation /
            repetition / length flags do NOT set this on their own.
        truncated: True iff the per-sink length cap or the
            excessive-repetition cap was applied.
        blocked: True iff the decision matrix says "do not ship to
            this sink" — currently fires only when a critical-severity
            secret was found on a ``chat_response`` or ``code_block``
            sink.
        blocked_reason: Short identifier for the blocking cause
            (``"secret_emission"`` etc.) — empty when ``blocked=False``.
        sink: The sink identifier the caller passed in. Echoed back
            for telemetry; not used in cleaning logic beyond the policy
            switch.
        original_length: Length of the text as received, before any
            cleaning, redaction, or truncation.
    """

    cleaned: str
    detected_patterns: List[str] = field(default_factory=list)
    is_suspicious: bool = False
    truncated: bool = False
    blocked: bool = False
    blocked_reason: str = ""
    sink: str = "generic"
    original_length: int = 0


# Names that count as "suspicious" — anything that indicates the model
# emitted dangerous content (vs. routine boundary work). HTML strips,
# obfuscation strips, length truncation, repetition truncation are NOT
# in this set on their own.
_SUSPICIOUS_PATTERN_NAMES = frozenset(
    {
        "secret_emission",
        "prompt_payload_emission",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_html_vectors(text: str) -> Tuple[str, bool]:
    """Strip HTML script/iframe/object/embed tags + dangerous URL attrs.

    Mirrors AA2's strip helper, expanded to include ``<embed>`` and
    markdown-link dangerous URLs. Returns ``(cleaned, hit_any)``.

    The detection name returned in the parent function's list is
    ``"html_injection"`` (collapsed — we don't care which sub-vector
    fired for output sanitization; the action is the same: strip).
    """
    cleaned = text
    hit = False

    # Pass 1: paired tags.
    for pattern in (
        _RE_HTML_SCRIPT_TAG,
        _RE_HTML_IFRAME_TAG,
        _RE_HTML_OBJECT_TAG,
        _RE_HTML_EMBED_TAG,
    ):
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            hit = True

    # Pass 2: orphan-open / close tags.
    for pattern in (
        _RE_HTML_SCRIPT_OPEN,
        _RE_HTML_IFRAME_OPEN,
        _RE_HTML_OBJECT_OPEN,
        _RE_HTML_EMBED_OPEN,
    ):
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            hit = True

    return cleaned, hit


def _strip_dangerous_urls(text: str) -> Tuple[str, bool]:
    """Strip ``javascript:`` / ``vbscript:`` / ``data:text/html`` URLs.

    Matches three forms: quoted attr, unquoted attr, and markdown link
    target. Returns ``(cleaned, hit_any)``.
    """
    cleaned = text
    hit = False
    for pattern in (
        _RE_DANGEROUS_URL_ATTR,
        _RE_DANGEROUS_URL_ATTR_UNQUOTED,
        _RE_MARKDOWN_DANGEROUS_URL,
    ):
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            hit = True
    return cleaned, hit


def _redact_secrets(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Redact secret-shaped tokens in-place.

    Returns ``(cleaned, hits)`` where ``hits`` is a list of
    ``(label, severity)`` pairs in detection order. The cleaned text
    has each match replaced with ``[REDACTED-<label>]``.

    Conservative: if a regex matches multiple times in the same string,
    each match is independently redacted (the label is identical so
    only one entry per label appears in ``hits``).
    """
    cleaned = text
    hits: List[Tuple[str, str]] = []
    seen_labels: set = set()
    for pattern, label, severity in _SECRET_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub(f"[REDACTED-{label}]", cleaned)
            if label not in seen_labels:
                hits.append((label, severity))
                seen_labels.add(label)
    return cleaned, hits


def _collapse_excessive_repetition(text: str) -> Tuple[str, bool]:
    """Detect & truncate runs of the same line repeated > threshold.

    Returns ``(cleaned, hit)``. When ``hit=True`` the cleaned text has
    the offending repetition collapsed to ``_REPETITION_THRESHOLD``
    copies followed by a ``[…truncated repetition…]`` marker.

    Implementation: O(n) single-pass over splitlines. We rebuild the
    text rather than re-joining to keep behavior predictable for the
    truncation marker.
    """
    lines = text.split("\n")
    if len(lines) <= _REPETITION_THRESHOLD:
        return text, False

    out: List[str] = []
    i = 0
    hit = False
    n = len(lines)
    while i < n:
        line = lines[i]
        # Count consecutive duplicates.
        j = i + 1
        while j < n and lines[j] == line:
            j += 1
        run = j - i
        if run > _REPETITION_THRESHOLD and line.strip():
            # Keep threshold copies, then collapse the rest.
            out.extend([line] * _REPETITION_THRESHOLD)
            out.append(f"[...truncated {run - _REPETITION_THRESHOLD} more " f"repetitions of the previous line...]")
            hit = True
        else:
            out.extend([line] * run)
        i = j
    return "\n".join(out), hit


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_agent_output(
    text: str,
    sink: str = "generic",
) -> OutputSanitizationResult:
    """Sanitize text emitted by an agent before it reaches a sink.

    This is the AA9 (insecure output handling) boundary layer. It runs
    *between* the LLM's emission and any downstream consumer — HTTP
    client, Convex record, code-block download, etc. See the wire
    points in :mod:`ai.agents.multi_agent` (``build`` / ``build_stream``
    return paths) and :mod:`api.chat_routes` (defense-in-depth at the
    HTTP boundary).

    Args:
        text: Raw text emitted by the agent / LLM. ``None`` and
            non-string inputs are coerced to empty string (defensive
            — the caller should already ensure str, but the agent
            layer occasionally returns ``None`` for skipped phases).
        sink: One of ``"chat_response"``, ``"convex_record"``,
            ``"code_block"``, or ``"generic"``. Controls per-sink
            policy: length cap, HTML-strip on/off, block-on-secret
            on/off. Unknown sinks fall back to the generic policy.

    Returns:
        An :class:`OutputSanitizationResult`. ``cleaned`` is always a
        string. **If ``blocked=True``, ``cleaned`` is empty** — the
        caller must check ``blocked`` before forwarding and produce a
        refusal response from ``blocked_reason`` instead.
    """
    # Type defense — never let a non-string crash the egress path.
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    original_length = len(text)
    detected: List[str] = []

    # 1. Empty / whitespace-only output. Flag and return early. An
    # empty response is not suspicious on its own; we just record it.
    if not text or not text.strip():
        detected.append("empty_output")
        return OutputSanitizationResult(
            cleaned="",
            detected_patterns=detected,
            is_suspicious=False,
            truncated=False,
            blocked=False,
            blocked_reason="",
            sink=sink,
            original_length=original_length,
        )

    cleaned = text

    # 2. Strip pure-obfuscation chars (ANSI / zero-width / bidi).
    # Shared helper with AA1 / AA2. These never have a legitimate
    # place in agent output the user will see.
    cleaned, obfuscation_hits = strip_obfuscation(cleaned)
    detected.extend(obfuscation_hits)

    # 3. Sink-specific HTML / dangerous-URL strip. Skipped for the
    # ``code_block`` sink because legitimate code can contain a
    # ``<script>`` literal (e.g. an agent emitting a Vue component
    # source file). Skipped for ``convex_record`` because Convex
    # stores raw text; the frontend escapes on render.
    if sink in ("chat_response", "generic"):
        new_cleaned, html_hit = _strip_html_vectors(cleaned)
        if html_hit:
            cleaned = new_cleaned
            detected.append("html_injection")
        new_cleaned, url_hit = _strip_dangerous_urls(cleaned)
        if url_hit:
            cleaned = new_cleaned
            detected.append("javascript_url")

    # 4. Secret detection & redaction. Applied to ALL sinks — even
    # ``convex_record`` (the row is auditable but the value should
    # never have been stored in plaintext) and ``code_block``
    # (paranoid: a leaked key in a code block is the same exfil
    # damage as one in a sentence). The DECISION (block vs not) is
    # sink-aware in step 6 below.
    cleaned, secret_hits = _redact_secrets(cleaned)
    secret_severities: List[str] = []
    if secret_hits:
        detected.append("secret_emission")
        secret_severities = [sev for _label, sev in secret_hits]

    # 5. Prompt-payload echo. The LLM emitted role markers
    # (``Human:`` / ``<|im_start|>`` / ``[INST]``) that look like a
    # chat turn boundary. Wrap (don't delete) — the wrap signals to
    # the downstream renderer that this region should be rendered
    # inert (gray box, "agent emitted prompt-style payload"). Mirrors
    # AA2's wrap, but the marker is distinct because we own the
    # downstream side here.
    wrapped = False
    if _RE_PROMPT_PAYLOAD.search(cleaned):
        detected.append("prompt_payload_emission")
        cleaned = f"{FLAGGED_START}\n{cleaned}\n{FLAGGED_END}"
        wrapped = True

    # 6. Excessive repetition. Same line >threshold consecutive copies
    # = LLM stuck in a decoding loop (and a renderer DoS vector).
    # Truncate the run; the truncation marker preserves auditability.
    cleaned, repetition_hit = _collapse_excessive_repetition(cleaned)
    if repetition_hit:
        detected.append("excessive_repetition")

    is_suspicious = any(name in _SUSPICIOUS_PATTERN_NAMES for name in detected)

    # 7. Decision matrix — BLOCK on critical-severity secret in
    # ``chat_response`` / ``code_block`` sinks. The rationale:
    # shipping a leaked credential out over HTTP / into a downloadable
    # code file is worst-case egress. Convex persists behind app-level
    # auth so redaction is enough; generic sinks are a fall-through
    # and we don't know the consumer's blast radius — be permissive
    # there and rely on redaction.
    blocked = False
    blocked_reason = ""
    if "critical" in secret_severities and sink in ("chat_response", "code_block"):
        blocked = True
        blocked_reason = "secret_emission"

    # 8. Per-sink length cap. TRUNCATE not reject. Applied AFTER the
    # wrap so an attacker cannot pad content to push the closing
    # marker out of the window.
    max_chars = MAX_CHARS_BY_SINK.get(sink, _DEFAULT_MAX_CHARS)
    truncated = repetition_hit  # repetition collapse is itself a truncation
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
        detected.append("length_exceeded")
        truncated = True
        # If we truncated a wrapped result, re-append the closing marker
        # so the boundary stays well-formed.
        if wrapped and not cleaned.endswith(FLAGGED_END):
            tail = f"\n{FLAGGED_END}"
            cleaned = cleaned[: max_chars - len(tail)] + tail

    # 9. If blocked, blank out cleaned. The caller MUST use
    # ``blocked_reason`` to construct a refusal — they should not be
    # able to accidentally ship the redacted text either, because
    # even a redacted token reveals "a secret was here" which is a
    # signal we don't want to leak in the blocked-sink case.
    if blocked:
        cleaned = ""

    # 10. Bound detection list defensively.
    if len(detected) > _MAX_DETECTIONS:
        detected = detected[:_MAX_DETECTIONS]

    if detected:
        # WARNING when suspicious or blocked; INFO for routine flags.
        log_method = _logger.warning if (is_suspicious or blocked) else _logger.info
        log_method(
            "[AGENT_OUTPUT_SANITIZE] sink=%s detections=%s suspicious=%s "
            "blocked=%s blocked_reason=%s truncated=%s orig_len=%d cleaned_len=%d",
            sink,
            ",".join(detected),
            is_suspicious,
            blocked,
            blocked_reason or "-",
            truncated,
            original_length,
            len(cleaned),
        )

    return OutputSanitizationResult(
        cleaned=cleaned,
        detected_patterns=detected,
        is_suspicious=is_suspicious,
        truncated=truncated,
        blocked=blocked,
        blocked_reason=blocked_reason,
        sink=sink,
        original_length=original_length,
    )


__all__ = [
    "FLAGGED_END",
    "FLAGGED_START",
    "MAX_CHARS_BY_SINK",
    "OutputSanitizationResult",
    "sanitize_agent_output",
]
