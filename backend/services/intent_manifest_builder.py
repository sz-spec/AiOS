"""
backend/services/intent_manifest_builder.py
============================================

Stage 10.3 — Pythonic builder for the binary IntentManifest envelope
that ``cmd_intent_submit`` (kernel/src/drivers/vbus_ai_cmds.c) and
``vos3_intent_validate`` (kernel/src/mm/intent_validator.c) consume.

Schema reference: ``kernel/include/vos/tee.h`` — defaults to **v2**
because v2 is the schema that carries ``min_confidence_score`` and any
manifest produced by this module is going through Stage-10.2's
hallucination guardrail. Producing v1 manifests is still possible for
back-compat tests (``schema_version=1``) but is not the default.

Wire layout (little-endian; bytes 0..19 fixed, body variable):

    offset  size   field
    ------  ----   -----
       0     8     MAGIC ("VOS3IM01")
       8     2     version            (1 or 2)
      10     2     flags              (must be 0)
      12     2     model_count        (≤ 8)
      14     2     tool_count         (≤ 32)
      16     2     role_count         (≤ 8)
      18     2     v1: reserved=0
                   v2: min_confidence_score (0..1000)
      20    var    body — concatenated NUL-terminated ASCII strings,
                          model_count + tool_count + role_count of them
                          in that order. Each string ≤ 256 bytes incl. NUL.

Caller responsibilities:

  - Strings must be ASCII-clean (printable + tab/LF/CR). The kernel
    validator rejects high-bit bytes as VOS3_INTENT_E_BAD_UTF8; this
    builder preflight-rejects them as ValueError so the caller sees
    the issue at build time, not after a kernel round-trip.

  - The ``user_settings`` argument is the bridge to the org / user
    configuration layer. It is a small protocol (a typed dict or an
    object exposing ``min_confidence_score``); see
    ``OrgManifestSettings`` below for the shape.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — match kernel/include/vos/tee.h and the v2 schema doc above
# ---------------------------------------------------------------------------

MAGIC = b"VOS3IM01"
VERSION_V1 = 1
VERSION_V2 = 2

INTENT_HDR_SIZE = 20
INTENT_STR_MAX_LEN = 256
INTENT_MAX_BYTES = 16 * 1024
INTENT_MAX_MODELS = 8
INTENT_MAX_TOOLS = 32
INTENT_MAX_ROLES = 8
INTENT_MAX_CONFIDENCE_SCORE = 1000


# ---------------------------------------------------------------------------
# User / Org settings protocol — a single field today, additive in future
# ---------------------------------------------------------------------------


class OrgManifestSettings(Protocol):
    """Protocol the org-wide configuration object must satisfy.

    The default implementation is a plain dataclass below; teams using
    Pydantic / Convex / etc. only need a class that exposes the same
    attribute name.
    """

    @property
    def min_confidence_score(self) -> int: ...


@dataclass
class DefaultOrgSettings:
    """Stand-in implementation when the broader org-config layer is offline.

    Honest scope note: in production the values come from the User/Org
    record in Convex (see ``backend/convex/organizations.ts``). This
    dataclass is the offline-dev stand-in and the default the builder
    falls back to when no settings object is passed.
    """

    min_confidence_score: int = 0


# ---------------------------------------------------------------------------
# ASCII helpers — match the kernel's is_ascii_clean() in intent_validator.c
# ---------------------------------------------------------------------------


def _is_ascii_clean_byte(b: int) -> bool:
    if b in (0x09, 0x0A, 0x0D):
        return True
    return 0x20 <= b <= 0x7E


def _validate_ascii_string(s: str) -> bytes:
    """Encode a string for the manifest body and verify ASCII-cleanliness.

    Returns the raw bytes WITHOUT the NUL terminator; the builder appends
    the NUL itself so a caller cannot accidentally double-NUL.
    """
    if not isinstance(s, str):
        raise TypeError(f"manifest string must be str, got {type(s).__name__}")
    raw = s.encode("ascii", errors="strict")  # raises if non-ASCII
    for i, b in enumerate(raw):
        if not _is_ascii_clean_byte(b):
            raise ValueError(
                f"manifest string {s!r} byte {i} (0x{b:02x}) "
                "is a control character — kernel validator would reject"
            )
    if len(raw) + 1 > INTENT_STR_MAX_LEN:
        raise ValueError(
            f"manifest string {s!r} exceeds {INTENT_STR_MAX_LEN - 1} chars "
            "(kernel cap)"
        )
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    models: Iterable[str],
    tools: Iterable[str],
    roles: Iterable[str],
    user_settings: Optional[OrgManifestSettings] = None,
    min_confidence_score_override: Optional[int] = None,
    schema_version: int = VERSION_V2,
) -> bytes:
    """Produce a binary IntentManifest envelope ready for INTENT_SUBMIT.

    Argument resolution order for ``min_confidence_score``:
      1. ``min_confidence_score_override`` if provided (caller wins —
         used by tests and one-off CEO overrides issued without
         touching the org config).
      2. ``user_settings.min_confidence_score`` if a settings object
         was passed.
      3. Zero (no guardrail) — preserves Stage 10.1 behaviour for any
         caller that doesn't know about the score yet.

    The default ``schema_version=VERSION_V2`` matches the Stage-10.3
    contract: every freshly-built manifest is v2 unless a back-compat
    test explicitly asks for v1.
    """
    if schema_version not in (VERSION_V1, VERSION_V2):
        raise ValueError(f"schema_version must be 1 or 2; got {schema_version}")

    models = list(models)
    tools = list(tools)
    roles = list(roles)

    if len(models) > INTENT_MAX_MODELS:
        raise ValueError(f"too many models ({len(models)} > {INTENT_MAX_MODELS})")
    if len(tools) > INTENT_MAX_TOOLS:
        raise ValueError(f"too many tools ({len(tools)} > {INTENT_MAX_TOOLS})")
    if len(roles) > INTENT_MAX_ROLES:
        raise ValueError(f"too many roles ({len(roles)} > {INTENT_MAX_ROLES})")

    # Resolve min_confidence_score per the documented order above.
    if min_confidence_score_override is not None:
        score = int(min_confidence_score_override)
    elif user_settings is not None:
        score = int(user_settings.min_confidence_score)
    else:
        score = 0

    if score < 0 or score > INTENT_MAX_CONFIDENCE_SCORE:
        raise ValueError(
            f"min_confidence_score {score} outside [0, {INTENT_MAX_CONFIDENCE_SCORE}]"
        )

    # v1 must keep the reserved word == 0; the validator rejects non-zero
    # at offset 18 for v1.
    if schema_version == VERSION_V1 and score != 0:
        raise ValueError(
            "v1 manifests do not carry min_confidence_score; either set "
            "schema_version=2 or pass score=0"
        )

    # Pre-encode body strings so we can fail fast on bad ASCII.
    encoded_models = [_validate_ascii_string(s) for s in models]
    encoded_tools = [_validate_ascii_string(s) for s in tools]
    encoded_roles = [_validate_ascii_string(s) for s in roles]

    # Header — 20 bytes little-endian per the schema.
    out = bytearray()
    out.extend(MAGIC)
    out.extend(int(schema_version).to_bytes(2, "little"))  # version
    out.extend((0).to_bytes(2, "little"))  # flags
    out.extend(len(models).to_bytes(2, "little"))  # n_models
    out.extend(len(tools).to_bytes(2, "little"))  # n_tools
    out.extend(len(roles).to_bytes(2, "little"))  # n_roles
    out.extend(score.to_bytes(2, "little"))  # offset 18

    # Body — NUL-terminated ASCII strings in (models, tools, roles) order.
    for s in encoded_models:
        out.extend(s)
        out.append(0x00)
    for s in encoded_tools:
        out.extend(s)
        out.append(0x00)
    for s in encoded_roles:
        out.extend(s)
        out.append(0x00)

    if len(out) > INTENT_MAX_BYTES:
        raise ValueError(
            f"manifest size {len(out)} exceeds kernel cap {INTENT_MAX_BYTES}"
        )

    return bytes(out)


def to_hex_for_vbus(manifest_bytes: bytes) -> str:
    """Convert a manifest envelope to the lowercase-hex form INTENT_SUBMIT expects.

    Wire usage:
        INTENT_SUBMIT|<hex>|<slot_id>
    """
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        raise TypeError("manifest_bytes must be bytes")
    return manifest_bytes.hex()


def build_v2_for_org(
    *,
    org_settings: OrgManifestSettings,
    models: Iterable[str],
    tools: Iterable[str],
    roles: Iterable[str],
) -> bytes:
    """Convenience wrapper — produce a v2 manifest using ONLY the org settings.

    The shape every "create new agent for org X" call site uses:
    no override, no schema-version juggling. Just the org's
    ``min_confidence_score`` baked into a fresh v2 envelope.
    """
    return build_manifest(
        models=models,
        tools=tools,
        roles=roles,
        user_settings=org_settings,
        schema_version=VERSION_V2,
    )


# ===========================================================================
# Sprint 21 / Item B2 — IntentManifest v3 delegation chain (NEW row)
# ===========================================================================
#
# From the 80-problem agent-era catalog, B2:
#   "No OS delegated-authority primitive — when agent A spawns sub-agent B
#    to do part of a task, the OS has no way to express 'B acts on A's
#    behalf, with NO MORE authority than A, and only for this task'. Unix
#    setuid is all-or-nothing and points the wrong way."
#
# The v3 delegation chain is that primitive: a linear chain of Ed25519-
# signed tokens, each bound to its parent by the parent token's hash, each
# ATTENUATING (never widening) the parent's tool scope. A child operation
# is refused unless the whole chain verifies back to a trusted root AND the
# requested tool is in the leaf's scope. Refuse a child op whose parent-
# token binding doesn't verify = the fail-closed contract.
#
# This is the cross-process / parent->child sibling of B1's
# agent_capability_table (a single agent's tool caps) and the linear
# special case of B6's IBCT delegation GRAPH.

import os as _os  # noqa: E402  (local to the delegation section)


class DelegationError(Exception):
    """Raised when a delegation chain fails to verify or a requested op is
    outside the proven, attenuated authority. Fail-closed."""


class _DelegClock(Protocol):
    def time(self) -> float: ...


class _DelegSystemClock:
    def time(self) -> float:
        return time.time()


def _dsha(*chunks: bytes) -> bytes:
    h = hashlib.sha256()
    for c in chunks:
        h.update(len(c).to_bytes(8, "big"))
        h.update(c)
    return h.digest()


def delegation_keypair() -> "tuple[bytes, bytes]":
    """(private_bytes, public_bytes) Ed25519 — for roots and subjects."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    return (
        sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )


def _deleg_sign(private_bytes: bytes, message: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.from_private_bytes(private_bytes).sign(message)


def _deleg_verify(public_bytes: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class DelegationToken:
    """One link in a delegation chain.

    ``issuer_id`` is the delegator (the root authority for the root link,
    or the parent's subject for child links). ``subject_id``/``subject_pub``
    is the delegatee — its key signs the NEXT link. ``signer_pub`` is the
    key that signed THIS token (root pubkey for the root, parent's
    subject_pub for children). ``parent_hash`` binds the link to its
    parent token (b"" for the root)."""

    issuer_id: str
    subject_id: str
    subject_pub: bytes
    scope: frozenset  # frozenset[str] of allowed tool names
    parent_hash: bytes
    issued_at: float
    expires_at: float
    nonce: bytes
    signer_pub: bytes
    signature: bytes

    def canonical_payload(self) -> bytes:
        return _dsha(
            b"vos3-deleg-v3",
            self.issuer_id.encode("utf-8"),
            self.subject_id.encode("utf-8"),
            self.subject_pub,
            _dsha(*[s.encode("utf-8") for s in sorted(self.scope)]),
            self.parent_hash,
            repr(self.issued_at).encode("utf-8"),
            repr(self.expires_at).encode("utf-8"),
            self.nonce,
            self.signer_pub,
        )

    def token_hash(self) -> bytes:
        return _dsha(b"vos3-deleg-id", self.canonical_payload(), self.signature)


def new_root_delegation(
    *,
    root_private: bytes,
    root_public: bytes,
    root_id: str,
    subject_id: str,
    subject_pub: bytes,
    scope: Iterable[str],
    ttl_seconds: int = 300,
    clock: Optional[_DelegClock] = None,
    nonce: Optional[bytes] = None,
) -> DelegationToken:
    """Mint the root of a delegation chain, signed by the trusted root
    authority key."""
    clk = clock or _DelegSystemClock()
    now = clk.time()
    n = nonce if nonce is not None else _os.urandom(16)
    draft = DelegationToken(
        issuer_id=root_id,
        subject_id=subject_id,
        subject_pub=subject_pub,
        scope=frozenset(scope),
        parent_hash=b"",
        issued_at=now,
        expires_at=now + max(1, int(ttl_seconds)),
        nonce=n,
        signer_pub=root_public,
        signature=b"",
    )
    sig = _deleg_sign(root_private, draft.canonical_payload())
    return DelegationToken(**{**draft.__dict__, "signature": sig})


def delegate(
    *,
    parent: DelegationToken,
    parent_subject_private: bytes,
    child_subject_id: str,
    child_subject_pub: bytes,
    scope: Iterable[str],
    ttl_seconds: int = 300,
    clock: Optional[_DelegClock] = None,
    nonce: Optional[bytes] = None,
) -> DelegationToken:
    """Mint a child token under ``parent``. The child scope MUST be a
    subset of the parent scope (attenuation — delegation never widens
    authority); the child is signed by the parent's subject key and bound
    to the parent token's hash."""
    child_scope = frozenset(scope)
    if not child_scope.issubset(parent.scope):
        widened = sorted(child_scope - parent.scope)
        raise DelegationError(
            f"delegation cannot WIDEN authority — child scope adds "
            f"{widened} not held by parent {parent.subject_id!r}"
        )
    clk = clock or _DelegSystemClock()
    now = clk.time()
    n = nonce if nonce is not None else _os.urandom(16)
    draft = DelegationToken(
        issuer_id=parent.subject_id,
        subject_id=child_subject_id,
        subject_pub=child_subject_pub,
        scope=child_scope,
        parent_hash=parent.token_hash(),
        issued_at=now,
        expires_at=now + max(1, int(ttl_seconds)),
        nonce=n,
        signer_pub=parent.subject_pub,
        signature=b"",
    )
    sig = _deleg_sign(parent_subject_private, draft.canonical_payload())
    return DelegationToken(**{**draft.__dict__, "signature": sig})


def verify_delegation_chain(
    chain: "list[DelegationToken]",
    *,
    root_public: bytes,
    clock: Optional[_DelegClock] = None,
) -> None:
    """Verify a chain root→leaf. Raises DelegationError on the first
    failure (empty chain, bad root key, broken parent binding, widened
    scope, expired link, or bad signature). Returns None on success."""
    if not chain:
        raise DelegationError("empty delegation chain")
    clk = clock or _DelegSystemClock()
    now = clk.time()

    root = chain[0]
    if root.parent_hash != b"":
        raise DelegationError("root link must have empty parent_hash")
    if root.signer_pub != root_public:
        raise DelegationError("root link not signed by the trusted root key")

    prev: Optional[DelegationToken] = None
    for i, tok in enumerate(chain):
        if now > tok.expires_at:
            raise DelegationError(f"chain link #{i} ({tok.subject_id!r}) expired")
        if not _deleg_verify(tok.signer_pub, tok.signature, tok.canonical_payload()):
            raise DelegationError(f"chain link #{i} signature does not verify")
        if i == 0:
            prev = tok
            continue
        # Child binding checks.
        if tok.parent_hash != prev.token_hash():
            raise DelegationError(
                f"chain link #{i} parent_hash does not bind to link #{i-1}"
            )
        if tok.signer_pub != prev.subject_pub:
            raise DelegationError(
                f"chain link #{i} not signed by parent #{i-1}'s subject key"
            )
        if tok.issuer_id != prev.subject_id:
            raise DelegationError(
                f"chain link #{i} issuer {tok.issuer_id!r} != parent subject "
                f"{prev.subject_id!r}"
            )
        if not tok.scope.issubset(prev.scope):
            raise DelegationError(
                f"chain link #{i} widens scope beyond parent (attenuation " f"violated)"
            )
        prev = tok


def require_delegated_tool(
    chain: "list[DelegationToken]",
    tool: str,
    *,
    root_public: bytes,
    clock: Optional[_DelegClock] = None,
) -> DelegationToken:
    """Verify the chain AND that the leaf's attenuated scope authorizes
    ``tool``. Returns the leaf token on success; raises DelegationError
    (fail-closed) otherwise."""
    verify_delegation_chain(chain, root_public=root_public, clock=clock)
    leaf = chain[-1]
    if tool not in leaf.scope:
        raise DelegationError(
            f"delegated agent {leaf.subject_id!r} is not authorized for tool "
            f"{tool!r} (leaf scope={sorted(leaf.scope)})"
        )
    return leaf
