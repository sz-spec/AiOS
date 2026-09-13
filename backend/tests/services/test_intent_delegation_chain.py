"""
Tests for Sprint 21 / Item B2 — IntentManifest v3 delegation chain
(backend/services/intent_manifest_builder.py delegation section).

Pins the fail-closed delegated-authority contract: a chain verifies only
if every link binds to its parent, attenuates (never widens) scope, is
unexpired, and is signed by the right key — back to a trusted root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.intent_manifest_builder import (  # noqa: E402
    DelegationError,
    DelegationToken,
    delegate,
    delegation_keypair,
    new_root_delegation,
    require_delegated_tool,
    verify_delegation_chain,
)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def _root_chain(clock=None):
    root_priv, root_pub = delegation_keypair()
    a_priv, a_pub = delegation_keypair()
    root = new_root_delegation(
        root_private=root_priv,
        root_public=root_pub,
        root_id="root",
        subject_id="agent-A",
        subject_pub=a_pub,
        scope={"web.fetch", "fs.read"},
        clock=clock,
    )
    return root_pub, root, (a_priv, a_pub)


def test_root_only_chain_verifies():
    root_pub, root, _ = _root_chain()
    verify_delegation_chain([root], root_public=root_pub)  # no raise
    leaf = require_delegated_tool([root], "web.fetch", root_public=root_pub)
    assert leaf.subject_id == "agent-A"


def test_two_link_attenuated_chain_verifies():
    root_pub, root, (a_priv, a_pub) = _root_chain()
    b_priv, b_pub = delegation_keypair()
    child = delegate(
        parent=root,
        parent_subject_private=a_priv,
        child_subject_id="agent-B",
        child_subject_pub=b_pub,
        scope={"web.fetch"},
    )
    require_delegated_tool([root, child], "web.fetch", root_public=root_pub)


def test_delegation_cannot_widen_scope():
    root_pub, root, (a_priv, a_pub) = _root_chain()
    b_priv, b_pub = delegation_keypair()
    with pytest.raises(DelegationError):
        delegate(
            parent=root,
            parent_subject_private=a_priv,
            child_subject_id="agent-B",
            child_subject_pub=b_pub,
            scope={"web.fetch", "admin.delete"},
        )  # not held by parent


def test_tool_outside_leaf_scope_refused():
    root_pub, root, (a_priv, a_pub) = _root_chain()
    b_priv, b_pub = delegation_keypair()
    child = delegate(
        parent=root,
        parent_subject_private=a_priv,
        child_subject_id="agent-B",
        child_subject_pub=b_pub,
        scope={"web.fetch"},
    )
    with pytest.raises(DelegationError):
        require_delegated_tool([root, child], "fs.read", root_public=root_pub)


def test_tampered_link_signature_refused():
    root_pub, root, _ = _root_chain()
    forged = DelegationToken(**{**root.__dict__, "signature": b"\x00" * 64})
    with pytest.raises(DelegationError):
        verify_delegation_chain([forged], root_public=root_pub)


def test_wrong_root_key_refused():
    root_pub, root, _ = _root_chain()
    _, other_pub = delegation_keypair()
    with pytest.raises(DelegationError):
        verify_delegation_chain([root], root_public=other_pub)


def test_broken_parent_binding_refused():
    """A child bound to one parent token cannot be re-parented under a
    different root token."""
    root_pub, root, (a_priv, a_pub) = _root_chain()
    b_priv, b_pub = delegation_keypair()
    child = delegate(
        parent=root,
        parent_subject_private=a_priv,
        child_subject_id="agent-B",
        child_subject_pub=b_pub,
        scope={"web.fetch"},
    )
    # Build a different root with the same subject key but different nonce.
    other_root_pub, other_root, _ = _root_chain()
    with pytest.raises(DelegationError):
        verify_delegation_chain([other_root, child], root_public=other_root_pub)


def test_expired_link_refused():
    clock = FakeClock()
    root_pub, root, _ = _root_chain(clock=clock)
    verify_delegation_chain([root], root_public=root_pub, clock=clock)
    clock.now += 10_000  # past ttl
    with pytest.raises(DelegationError):
        verify_delegation_chain([root], root_public=root_pub, clock=clock)


def test_three_link_chain_attenuates_each_hop():
    root_pub, root, (a_priv, a_pub) = _root_chain()
    b_priv, b_pub = delegation_keypair()
    c_priv, c_pub = delegation_keypair()
    b = delegate(
        parent=root,
        parent_subject_private=a_priv,
        child_subject_id="agent-B",
        child_subject_pub=b_pub,
        scope={"web.fetch", "fs.read"},
    )
    c = delegate(
        parent=b,
        parent_subject_private=b_priv,
        child_subject_id="agent-C",
        child_subject_pub=c_pub,
        scope={"fs.read"},
    )
    require_delegated_tool([root, b, c], "fs.read", root_public=root_pub)
    with pytest.raises(DelegationError):
        require_delegated_tool([root, b, c], "web.fetch", root_public=root_pub)
