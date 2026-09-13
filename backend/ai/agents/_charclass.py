"""
Shared character-class strip helpers (AA1 + AA2 boundary layers)
=================================================================

This module factors the "obfuscation character" regex set out of
``prompt_sanitization.py`` (Day 2, AA1) so it can be reused by
``tool_output_sanitization.py`` (Day 3, AA2). Both layers share the
same notion of "characters that should never appear in untrusted text
because they exist to hide content from the reader" — ANSI escapes,
zero-width characters, and Unicode bidi-override characters.

Design:
  * Regexes only — deterministic, fast, no LLM-in-the-loop.
  * Each class is exposed both as a compiled pattern (for ``.search``
    branchless checks) and via a ``strip_*`` convenience function that
    returns ``(cleaned, was_present)``.
  * Module-private, prefixed with ``_`` — re-exported by name from
    callers so the public surface stays in the AA1/AA2 modules.

These three classes are stripped silently from BOTH user prompts and
tool output. Their presence is logged as an audit signal but does NOT
mark the input as ``is_suspicious`` on its own — for that, the caller
needs a content-level injection-pattern hit. Rationale: a stray BOM in
a copy-pasted prompt or a fetched HTML page is routine; treating it as
malicious would generate noise without security value.

OWASP mapping:
  * AA1 (Direct Prompt Injection) — user→LLM boundary
  * AA2 (Indirect Prompt Injection) — tool→LLM boundary
"""

from __future__ import annotations

import re
from typing import Tuple

# ---------------------------------------------------------------------------
# ANSI escape sequences (CSI etc.)
# ---------------------------------------------------------------------------
# The set is small and well-defined (ECMA-48). We match both the CSI
# (``ESC [ ... letter``) and the single-character C1 control forms.
RE_ANSI_ESCAPE = re.compile(r"\x1B(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


# ---------------------------------------------------------------------------
# Zero-width characters
# ---------------------------------------------------------------------------
# ZWSP (U+200B), ZWNJ (U+200C), ZWJ (U+200D), WJ (U+2060), BOM (U+FEFF).
# Treating BOM as zero-width here is intentional: it is a Unicode invisible
# char and we have no legitimate use for it in either user prompts or tool
# output reaching the LLM.
ZERO_WIDTH_CHARS = "​‌‍⁠﻿"
RE_ZERO_WIDTH = re.compile(f"[{ZERO_WIDTH_CHARS}]")


# ---------------------------------------------------------------------------
# Bidi-override characters
# ---------------------------------------------------------------------------
# RLO (U+202E), LRO (U+202D), RLE (U+202B), LRE (U+202A), PDF (U+202C),
# RLI (U+2067), LRI (U+2066), FSI (U+2068), PDI (U+2069). These cause
# display-vs-content mismatches and have no legitimate place in untrusted
# strings reaching the LLM.
BIDI_OVERRIDE_CHARS = "‪‫‬‭‮⁦⁧⁨⁩"
RE_BIDI_OVERRIDE = re.compile(f"[{BIDI_OVERRIDE_CHARS}]")


# ---------------------------------------------------------------------------
# Public strip helpers
# ---------------------------------------------------------------------------
# Each returns ``(cleaned, was_present)``. The boolean lets the caller log
# the detection without a second ``.search`` pass.


def strip_ansi(text: str) -> Tuple[str, bool]:
    """Strip ANSI escape sequences. Returns ``(cleaned, was_present)``."""
    if not RE_ANSI_ESCAPE.search(text):
        return text, False
    return RE_ANSI_ESCAPE.sub("", text), True


def strip_zero_width(text: str) -> Tuple[str, bool]:
    """Strip zero-width chars (ZWSP/ZWNJ/ZWJ/WJ/BOM). Returns ``(cleaned, was_present)``."""
    if not RE_ZERO_WIDTH.search(text):
        return text, False
    return RE_ZERO_WIDTH.sub("", text), True


def strip_bidi_override(text: str) -> Tuple[str, bool]:
    """Strip Unicode bidi-override chars. Returns ``(cleaned, was_present)``."""
    if not RE_BIDI_OVERRIDE.search(text):
        return text, False
    return RE_BIDI_OVERRIDE.sub("", text), True


def strip_obfuscation(text: str) -> Tuple[str, list]:
    """Strip all three obfuscation char classes in one pass.

    Convenience wrapper for callers that want a single call site. Returns
    ``(cleaned, detections)`` where ``detections`` is a list of detection
    name strings (subset of ``["ansi_escape", "zero_width", "bidi_override"]``)
    in the order the classes were checked.
    """
    detections: list = []
    cleaned, hit = strip_ansi(text)
    if hit:
        detections.append("ansi_escape")
    cleaned, hit = strip_zero_width(cleaned)
    if hit:
        detections.append("zero_width")
    cleaned, hit = strip_bidi_override(cleaned)
    if hit:
        detections.append("bidi_override")
    return cleaned, detections


__all__ = [
    "BIDI_OVERRIDE_CHARS",
    "RE_ANSI_ESCAPE",
    "RE_BIDI_OVERRIDE",
    "RE_ZERO_WIDTH",
    "ZERO_WIDTH_CHARS",
    "strip_ansi",
    "strip_bidi_override",
    "strip_obfuscation",
    "strip_zero_width",
]
