"""W1.5 — SSE error-leak redaction regression suite.

The streaming chat endpoint must never let provider keys, JWTs, absolute
filesystem paths, or dated model IDs reach the client through the
`data: {"error": ...}` SSE frame. Each planted secret below is treated as
a regression marker — if any of them ever appears verbatim in the redacted
payload, the test fails and a real leak has shipped.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from api.chat_routes import _redact_for_sse  # noqa: E402

# ---------------------------------------------------------------------------
# Planted secrets — each must NOT survive _redact_for_sse(...).
# ---------------------------------------------------------------------------

PLANTED_SECRETS = [
    # Provider API keys
    "sk-proj-AAAA1111BBBB2222CCCC3333DDDD4444EEEE",
    "sk-ant-api03-ZZZZYYYYXXXXWWWWVVVVUUUUTTTTSSSSRRRRQQQQ",
    "sk-FFFFGGGGHHHHIIIIJJJJKKKKLLLLMMMMNNNNOOOO",
    # Stripe / Clerk
    "pk_live_AAAABBBBCCCCDDDDEEEEFFFFGGGG",
    "_".join(("sk", "live", "TEST" * 7)),  # Generated synthetic Stripe fixture
    "clerk_AAAABBBBCCCCDDDDEEEEFFFFGGGG",
    # Webhook secret
    "whsec_AAAABBBBCCCCDDDDEEEEFFFFGGGG",
    # JWTs
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    # Bearer header
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3IifQ.k1tDoxzMOMqI4-ZyIBQzPv0VXhOCt9V8hl5ZqfxgKYg",
    # Filesystem paths
    "/Users/sz/.aws/credentials",
    "/home/runner/work/vos.v1/secrets",
    "C:\\Users\\sz\\AppData\\Roaming\\secret.json",
    # Dated model IDs
    "claude-3-5-sonnet-20240620",
    "gpt-4o-2024-08-06",
    "gemini-2.5-flash-002",
]


@pytest.mark.parametrize("secret", PLANTED_SECRETS)
def test_redactor_strips_planted_secret(secret):
    """Every planted secret must be replaced by a placeholder before reaching the client."""
    msg = f"some upstream chatter mentioning {secret} that should not leak"
    try:
        raise RuntimeError(msg)
    except RuntimeError as exc:
        out = _redact_for_sse(exc)
    assert secret not in out, f"PLANTED SECRET LEAKED: {secret!r} survived in: {out!r}"
    # The redactor swaps the secret for a `<REDACTED:...>` marker
    assert "<REDACTED:" in out, f"No redaction marker in output: {out!r}"


def test_redactor_keeps_class_and_ref():
    """A support-ref token + the exception class are always present."""
    try:
        raise PermissionError("denied")
    except PermissionError as exc:
        out = _redact_for_sse(exc)
    assert out.startswith("PermissionError"), out
    assert "ref=" in out, out


def test_redactor_caps_length():
    """A 100KB exception message must not blow up the SSE channel."""
    payload = "a" * 100_000
    try:
        raise RuntimeError(payload)
    except RuntimeError as exc:
        out = _redact_for_sse(exc)
    # 240 char body cap + class prefix + ref suffix < 400 chars
    assert len(out) < 400, f"Redacted output too long: {len(out)} chars"


def test_redactor_handles_empty_message():
    """An exception with no message string still produces a useful frame."""
    try:
        raise ValueError()
    except ValueError as exc:
        out = _redact_for_sse(exc)
    assert out.startswith("ValueError"), out
    assert "ref=" in out, out


def test_redactor_does_not_redact_innocent_text():
    """A normal upstream error message stays human-readable after redaction."""
    try:
        raise ValueError("model temperature must be between 0 and 2")
    except ValueError as exc:
        out = _redact_for_sse(exc)
    # The substantive message survives (no over-redaction)
    assert "temperature" in out, out
    assert "<REDACTED:" not in out, out
