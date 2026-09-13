"""
IntentManifest v2 — wire-format integrity.

`services.intent_manifest_builder.build_manifest()` produces a binary
envelope the kernel's INTENT_SUBMIT command consumes. The layout (per
docstring + kernel/include/vos/tee.h) is little-endian, 20-byte header
+ NUL-terminated ASCII strings.

These tests verify:
  * MAGIC bytes are stable
  * header geometry matches the constants
  * count caps are enforced
  * min_confidence_score ranges are checked
  * v1 manifests forbid non-zero score (must be 0)
  * ASCII-clean predicate rejects high-bit / control bytes
  * round-trip parse of the strings preserves order
"""

from __future__ import annotations

import struct

import pytest

from services.intent_manifest_builder import (
    INTENT_HDR_SIZE,
    INTENT_MAX_BYTES,
    INTENT_MAX_CONFIDENCE_SCORE,
    INTENT_MAX_MODELS,
    INTENT_MAX_ROLES,
    INTENT_MAX_TOOLS,
    INTENT_STR_MAX_LEN,
    MAGIC,
    VERSION_V1,
    VERSION_V2,
    build_manifest,
)

# ---------------------------------------------------------------------------
# Header constants — invariants
# ---------------------------------------------------------------------------


def test_magic_is_exactly_8_bytes():
    assert len(MAGIC) == 8
    assert MAGIC == b"VOS3IM01"


def test_header_size_is_20():
    assert INTENT_HDR_SIZE == 20


def test_max_envelope_is_16_kb():
    assert INTENT_MAX_BYTES == 16 * 1024


def test_count_caps_are_documented():
    assert INTENT_MAX_MODELS == 8
    assert INTENT_MAX_TOOLS == 32
    assert INTENT_MAX_ROLES == 8


def test_confidence_score_range():
    assert INTENT_MAX_CONFIDENCE_SCORE == 1000


def test_string_length_cap_includes_nul():
    assert INTENT_STR_MAX_LEN == 256


# ---------------------------------------------------------------------------
# Minimal v2 manifest
# ---------------------------------------------------------------------------


def test_empty_manifest_v2_has_just_header():
    raw = build_manifest(models=[], tools=[], roles=[])
    assert len(raw) == INTENT_HDR_SIZE
    assert raw[:8] == MAGIC
    version = struct.unpack_from("<H", raw, 8)[0]
    assert version == VERSION_V2


def test_default_schema_is_v2():
    raw = build_manifest(models=[], tools=[], roles=[])
    assert struct.unpack_from("<H", raw, 8)[0] == VERSION_V2


def test_explicit_v1_works():
    raw = build_manifest(models=[], tools=[], roles=[], schema_version=VERSION_V1)
    assert struct.unpack_from("<H", raw, 8)[0] == VERSION_V1


# ---------------------------------------------------------------------------
# Body — NUL-terminated ASCII strings preserve order
# ---------------------------------------------------------------------------


def _parse_body(raw: bytes, n_models: int, n_tools: int, n_roles: int):
    body = raw[INTENT_HDR_SIZE:]
    parts = body.split(b"\x00")
    # Last split is the empty trailing chunk because every string ends in NUL.
    if parts and parts[-1] == b"":
        parts = parts[:-1]
    assert len(parts) == n_models + n_tools + n_roles
    return (
        parts[:n_models],
        parts[n_models : n_models + n_tools],
        parts[n_models + n_tools :],
    )


def test_models_order_preserved():
    raw = build_manifest(models=["m_a", "m_b", "m_c"], tools=[], roles=[])
    models, _, _ = _parse_body(raw, 3, 0, 0)
    assert models == [b"m_a", b"m_b", b"m_c"]


def test_tools_order_preserved():
    raw = build_manifest(models=[], tools=["t_1", "t_2"], roles=[])
    _, tools, _ = _parse_body(raw, 0, 2, 0)
    assert tools == [b"t_1", b"t_2"]


def test_roles_order_preserved():
    raw = build_manifest(models=[], tools=[], roles=["r_a", "r_b", "r_c"])
    _, _, roles = _parse_body(raw, 0, 0, 3)
    assert roles == [b"r_a", b"r_b", b"r_c"]


@pytest.mark.parametrize("n_models", [1, 4, 8])
def test_model_count_field_matches(n_models):
    raw = build_manifest(models=[f"m{i}" for i in range(n_models)], tools=[], roles=[])
    count = struct.unpack_from("<H", raw, 12)[0]
    assert count == n_models


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


def test_too_many_models_rejected():
    with pytest.raises(ValueError, match="too many models"):
        build_manifest(
            models=[f"m{i}" for i in range(INTENT_MAX_MODELS + 1)],
            tools=[],
            roles=[],
        )


def test_too_many_tools_rejected():
    with pytest.raises(ValueError, match="too many tools"):
        build_manifest(
            models=[],
            tools=[f"t{i}" for i in range(INTENT_MAX_TOOLS + 1)],
            roles=[],
        )


def test_too_many_roles_rejected():
    with pytest.raises(ValueError, match="too many roles"):
        build_manifest(
            models=[],
            tools=[],
            roles=[f"r{i}" for i in range(INTENT_MAX_ROLES + 1)],
        )


# ---------------------------------------------------------------------------
# min_confidence_score — v2 only, range-checked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score", [0, 1, 250, 500, 999, 1000])
def test_valid_confidence_scores(score):
    raw = build_manifest(
        models=[],
        tools=[],
        roles=[],
        min_confidence_score_override=score,
    )
    on_wire = struct.unpack_from("<H", raw, 18)[0]
    assert on_wire == score


@pytest.mark.parametrize("score", [-1, -1000, 1001, 65535])
def test_out_of_range_confidence_score_rejected(score):
    with pytest.raises(ValueError, match="confidence"):
        build_manifest(
            models=[],
            tools=[],
            roles=[],
            min_confidence_score_override=score,
        )


def test_v1_must_have_zero_score():
    with pytest.raises(ValueError, match="v1"):
        build_manifest(
            models=[],
            tools=[],
            roles=[],
            schema_version=VERSION_V1,
            min_confidence_score_override=500,
        )


# ---------------------------------------------------------------------------
# ASCII-clean predicate — high-bit + control chars are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dirty",
    [
        "café",  # non-ASCII (é = 0xC3 0xA9)
        "M\x00",  # embedded NUL
        "M\x01",  # SOH control
        "M\x07",  # bell
        "M\x1b[Aupx",  # escape sequence
        "M\x7f",  # DEL
        "Mÿ",  # latin-1 supplement
        "M ",  # line separator
    ],
)
def test_non_ascii_clean_strings_rejected(dirty):
    with pytest.raises((ValueError, UnicodeEncodeError)):
        build_manifest(models=[dirty], tools=[], roles=[])


@pytest.mark.parametrize(
    "clean",
    [
        "model.v1",
        "claude-opus-4-7",
        "gpt-4o-mini",
        "tab\there",  # \t is ASCII clean per the kernel predicate
        "newline\nhere",  # \n likewise
        "carriage\rreturn",  # \r likewise
    ],
)
def test_ascii_clean_strings_accepted(clean):
    raw = build_manifest(models=[clean], tools=[], roles=[])
    assert clean.encode("ascii") in raw


def test_invalid_schema_version_rejected():
    with pytest.raises(ValueError, match="schema_version"):
        build_manifest(models=[], tools=[], roles=[], schema_version=99)


def test_invalid_schema_version_negative_rejected():
    with pytest.raises(ValueError):
        build_manifest(models=[], tools=[], roles=[], schema_version=0)
