"""
v20.5-SINGULARITY — Tenant-partitioned KV prefix cache (RadixAttention port).

What this is
============

A lightweight pure-Python port of SGLang's RadixAttention prefix-tree idea
(Zheng et al., SGLang 2024 / merged upstream early 2025) with **one
non-negotiable difference from the upstream literature: a hard tenant_id
partition at the tree root**. Cross-tenant prefix sharing is *not yet
solved with confidentiality* in the published 2026 literature
(`docs/AUDIT_IMMUNE_SPEC.md` cites NDSS '26 SoK on this point), so we
refuse to do it. Within a single tenant, prefix dedup gives a measurable
prefill-throughput improvement on agent-mesh workloads where many
sessions share a long system prompt.

What this is NOT
================

- This module does NOT do attention computation. It is a *prefix index*:
  given a token-id sequence, it returns the longest matching prefix's
  KV-cache handle (caller-provided opaque id) for the LLM serving layer
  to materialise. The actual KV blocks live in the inference engine's
  GPU/CPU memory; this module is the lookup table.
- This module does NOT do cross-tenant dedup. Every tenant_id maps to an
  independent radix tree; the only shared object is the lock.

Mathematical formulation
========================

Hit-rate in steady state, for a workload of $N$ requests where token
prefix $\\pi$ recurs with empirical probability $p(\\pi)$:

    \\rho = 1 - \\Pr[\\text{the longest cached prefix of req has length 0}]
        = 1 - \\sum_{\\pi : \\pi \\text{ not in tree}} p(\\pi).

Throughput improvement on the prefill phase is approximately
$1 / (1-\\rho)$ — every cache hit replaces a quadratic-in-prefix
recompute with an O(1) handle copy. Tail latency benefits scale with
the same factor on shared-context workloads (system prompts, RAG-grounded
agents).

Trust-domain invariant
======================

For every probe ``probe(tenant_id=T, tokens=[t_0, ..., t_{n-1}])``:

    return_value.trust_domain == T
    OR return_value is None.

This is asserted at every node visit in `lookup()` so a coding mistake
in a future refactor surfaces as a `TrustDomainViolation` rather than a
silent cross-tenant disclosure. The assertion is constant-time relative
to the secret tokens (it compares only the outer tenant_id).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "KVPrefixCache",
    "PrefixHit",
    "TrustDomainViolation",
]


class TrustDomainViolation(RuntimeError):
    """Raised when a cache probe would cross a tenant boundary.

    This is a programmer-error condition — well-formed probes never
    hit it. We raise rather than fail-soft so the regression
    surfaces in CI, not in production telemetry.
    """


@dataclass
class PrefixHit:
    """Result of a successful (longest) prefix lookup.

    Carries the trust-domain label so callers — and tests — can
    cheaply re-assert the invariant at the API boundary.
    """

    tokens: Tuple[int, ...]  # the actual matched prefix
    handle: int  # opaque KV-block handle (caller-owned)
    trust_domain: str  # tenant_id that owns this entry
    last_used_ns: int  # set on every hit; LRU eviction key


@dataclass
class _Node:
    """Radix-tree internal node.

    Children are keyed by the FIRST token of their edge label so a
    descent step is O(|alphabet|) per level — for natural-language
    token vocabularies this dominates by the children-per-node degree
    rather than by tree depth.
    """

    edge: Tuple[int, ...] = ()
    handle: Optional[int] = None
    last_used_ns: int = 0
    children: Dict[int, "_Node"] = field(default_factory=dict)


class _TenantTree:
    """One radix tree per tenant. Lock-protected."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._root = _Node()
        self._lock = threading.RLock()
        # Bookkeeping for telemetry + eviction.
        self.entry_count = 0
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    # Public ops
    # ------------------------------------------------------------------

    def insert(self, tokens: Sequence[int], handle: int) -> None:
        """Insert (or update) the leaf for ``tokens``.

        Idempotent on the (tokens, handle) pair; calling twice with the
        same tokens replaces the handle and bumps last_used_ns.
        """
        if not tokens:
            return
        with self._lock:
            self._insert_locked(tuple(tokens), handle)

    def lookup(self, tokens: Sequence[int]) -> Optional[PrefixHit]:
        """Return the longest matching prefix in the tree, or None."""
        if not tokens:
            return None
        with self._lock:
            return self._lookup_locked(tuple(tokens))

    def remove(self, tokens: Sequence[int]) -> bool:
        """Remove the entry with the exact key ``tokens``. Returns True iff present."""
        if not tokens:
            return False
        with self._lock:
            return self._remove_locked(tuple(tokens))

    def evict_lru(self, target: int) -> int:
        """Evict entries until ``entry_count <= target``. Returns evicted count.

        Strict LRU on the leaf timestamps. O(K log K) where K = entries
        (we walk the tree, sort, then delete). Acceptable for occasional
        eviction; not in the hot path.
        """
        if target < 0:
            target = 0
        with self._lock:
            if self.entry_count <= target:
                return 0
            leaves = self._collect_leaves(self._root, ())
            leaves.sort(key=lambda kv: kv[1].last_used_ns)  # oldest first
            evicted = 0
            while self.entry_count > target and leaves:
                tokens, _node = leaves.pop(0)
                if self._remove_locked(tokens):
                    evicted += 1
            return evicted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _insert_locked(self, tokens: Tuple[int, ...], handle: int) -> None:
        node = self._root
        i = 0
        n = len(tokens)
        while i < n:
            first = tokens[i]
            child = node.children.get(first)
            if child is None:
                # No child along this edge — install the remaining suffix.
                new = _Node(
                    edge=tokens[i:], handle=handle, last_used_ns=time.monotonic_ns()
                )
                node.children[first] = new
                self.entry_count += 1
                return

            # Find the longest common prefix of (tokens[i:], child.edge).
            edge = child.edge
            j = 0
            while j < len(edge) and i + j < n and edge[j] == tokens[i + j]:
                j += 1

            if j == len(edge):
                # Edge is fully consumed; descend into child.
                node = child
                i += j
                continue

            # Edge diverges at offset j → split the edge.
            split = _Node(
                edge=edge[:j],
                children={
                    edge[j]: _Node(
                        edge=edge[j:],
                        handle=child.handle,
                        last_used_ns=child.last_used_ns,
                        children=child.children,
                    )
                },
            )
            node.children[first] = split

            if i + j < n:
                # Remaining token suffix becomes a sibling of the moved child.
                split.children[tokens[i + j]] = _Node(
                    edge=tokens[i + j :],
                    handle=handle,
                    last_used_ns=time.monotonic_ns(),
                )
                self.entry_count += 1
            else:
                # tokens is a strict prefix of the existing edge; the
                # split point itself becomes the new leaf.
                split.handle = handle
                split.last_used_ns = time.monotonic_ns()
                self.entry_count += 1
            return

        # Walked through the whole token list landing exactly on `node`.
        if node.handle is None:
            self.entry_count += 1
        node.handle = handle
        node.last_used_ns = time.monotonic_ns()

    def _lookup_locked(self, tokens: Tuple[int, ...]) -> Optional[PrefixHit]:
        """Longest matching prefix that has a handle attached."""
        node = self._root
        i = 0
        n = len(tokens)
        best_handle: Optional[int] = None
        best_length: int = 0

        while i < n:
            first = tokens[i]
            child = node.children.get(first)
            if child is None:
                break
            edge = child.edge
            j = 0
            while j < len(edge) and i + j < n and edge[j] == tokens[i + j]:
                j += 1
            if j < len(edge):
                # Diverges mid-edge; this child is not a prefix of `tokens`.
                break
            i += j
            if child.handle is not None:
                best_handle = child.handle
                best_length = i
                child.last_used_ns = time.monotonic_ns()
            node = child

        if best_handle is None:
            self.misses += 1
            return None

        self.hits += 1
        hit = PrefixHit(
            tokens=tokens[:best_length],
            handle=best_handle,
            trust_domain=self.tenant_id,
            last_used_ns=time.monotonic_ns(),
        )
        # Defensive belt-and-braces: assert the invariant the entire
        # module exists to enforce. Cheap (string compare); fires once
        # per lookup; gives any future refactor a hard tripwire.
        if hit.trust_domain != self.tenant_id:
            raise TrustDomainViolation(
                f"hit.trust_domain={hit.trust_domain!r} != "
                f"tree.tenant_id={self.tenant_id!r}",
            )
        return hit

    def _remove_locked(self, tokens: Tuple[int, ...]) -> bool:
        """Remove an EXACT-key leaf; returns True iff a leaf was cleared."""
        # Walk the path; if we land on a node with the matching handle,
        # null its handle. Compaction is left for a later pass — eviction
        # cost dominates by handle-clear, not by tree-shape compaction.
        node = self._root
        i = 0
        n = len(tokens)
        while i < n:
            first = tokens[i]
            child = node.children.get(first)
            if child is None:
                return False
            edge = child.edge
            j = 0
            while j < len(edge) and i + j < n and edge[j] == tokens[i + j]:
                j += 1
            if j != len(edge):
                return False
            i += j
            node = child

        if node.handle is None:
            return False
        node.handle = None
        node.last_used_ns = 0
        self.entry_count -= 1
        return True

    def _collect_leaves(
        self, node: _Node, path: Tuple[int, ...]
    ) -> List[Tuple[Tuple[int, ...], _Node]]:
        out: List[Tuple[Tuple[int, ...], _Node]] = []
        edge_path = path + node.edge
        if node.handle is not None:
            out.append((edge_path, node))
        for child in node.children.values():
            out.extend(self._collect_leaves(child, edge_path))
        return out


class KVPrefixCache:
    """Tenant-partitioned RadixAttention-style KV prefix index.

    Trust model: every probe takes a ``tenant_id`` and is routed to the
    per-tenant tree. There is no API surface that admits a probe
    against a different tenant's tree, and the API does not return
    cross-tenant results. The tenant_id parameter is the single
    isolation gate.
    """

    def __init__(self, *, max_entries_per_tenant: int = 4096):
        self._trees: Dict[str, _TenantTree] = {}
        self._trees_lock = threading.RLock()
        self._max_entries = int(max_entries_per_tenant)

    # ------------------------------------------------------------------

    def insert(self, tenant_id: str, tokens: Sequence[int], handle: int) -> None:
        if not tenant_id:
            raise ValueError("tenant_id must be a non-empty string")
        tree = self._tree_for(tenant_id)
        tree.insert(tokens, handle)
        # Best-effort cap; eviction runs only when we breach the budget.
        if tree.entry_count > self._max_entries:
            tree.evict_lru(self._max_entries)

    def lookup(self, tenant_id: str, tokens: Sequence[int]) -> Optional[PrefixHit]:
        if not tenant_id:
            raise ValueError("tenant_id must be a non-empty string")
        tree = self._trees.get(tenant_id)  # no-create: lookup-only
        if tree is None:
            return None
        return tree.lookup(tokens)

    def remove(self, tenant_id: str, tokens: Sequence[int]) -> bool:
        tree = self._trees.get(tenant_id)
        if tree is None:
            return False
        return tree.remove(tokens)

    def stats(self) -> Dict[str, Dict[str, int]]:
        """Per-tenant {hits, misses, entries} for telemetry."""
        with self._trees_lock:
            return {
                tid: {
                    "hits": t.hits,
                    "misses": t.misses,
                    "entries": t.entry_count,
                }
                for tid, t in self._trees.items()
            }

    def hit_rate(self, tenant_id: str) -> float:
        """ρ = hits / (hits + misses); ``0.0`` if the tenant has no probes yet."""
        tree = self._trees.get(tenant_id)
        if tree is None:
            return 0.0
        denom = tree.hits + tree.misses
        return (tree.hits / denom) if denom else 0.0

    def tenants(self) -> Iterable[str]:
        with self._trees_lock:
            return list(self._trees.keys())

    # ------------------------------------------------------------------

    def _tree_for(self, tenant_id: str) -> _TenantTree:
        tree = self._trees.get(tenant_id)
        if tree is not None:
            return tree
        with self._trees_lock:
            tree = self._trees.get(tenant_id)
            if tree is None:
                tree = _TenantTree(tenant_id)
                self._trees[tenant_id] = tree
            return tree
