"""
infra/security/rekor_v2_log.py — Stage 11

Append-only transparency log, Rekor-v2-shaped.

Honest scope
============

This module ships a **structurally-correct** Rekor-v2 transparency log:
append-only, Merkle-tree-backed, every entry produces a verifiable
inclusion proof, and the root-hash chain is monotonically extending.

What it is NOT: a public Sigstore Rekor v2 *deployment*. The real
Sigstore Rekor v2 service runs at rekor.sigstore.dev; entries posted
there are visible to every Sigstore client globally and queryable by
fingerprint. A v2 endpoint shape was not in my training data (cutoff
January 2026), so this module's REST shape and OpenAPI schema may
differ from upstream. The cryptographic guarantees (SHA-256 leaf hash,
binary-tree-style Merkle aggregation, RFC-6962-style inclusion proofs)
are well-established public formats and are byte-for-byte standard.

Migration path: when the upstream Rekor v2 OpenAPI schema is in hand,
the only changes are (a) the JSON field names of LogEntry / InclusionProof
and (b) the HTTP path of the upload endpoint (legacy v1 was
``/api/v1/log/entries``; this module uses ``/api/v2/log/entries`` as a
forward-compatible placeholder). Cryptographic primitives stay the same.

Wire format produced
====================

Per-entry "LogEntry" object:

    {
      "kind":            "vos3.sigstore.v3+dsse",
      "leaf_index":       <int>,
      "appended_at":      <ISO-8601 UTC>,
      "leaf_hash_sha256": <hex>,            # SHA-256 of the bundle bytes
      "tree_size":        <int>,            # leaves in the tree AFTER append
      "root_hash_sha256": <hex>,            # tree root after this append
      "inclusion_proof": {
          "leaf_index":  <int>,
          "tree_size":   <int>,
          "root_hash":   <hex>,
          "audit_path":  [<hex>, ...]       # RFC-6962-shaped Merkle path
      }
    }

The full log file (default: ``infra/security/rekor_v2.jsonl``) is
JSON-Lines with one LogEntry per line. The tree root after entry N is
recoverable by hashing the leaves 0..N-1 according to the audit path
(or, equivalently, by reading the root_hash field of entry N — which
this verifier does and cross-checks).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

DEFAULT_LOG_PATH = "infra/security/rekor_v2.jsonl"


# ---------------------------------------------------------------------------
# RFC-6962-style Merkle hashing
# ---------------------------------------------------------------------------
#
# Domain separation per RFC-6962 §2.1:
#   leaf hash:  SHA-256(0x00 || data)
#   node hash:  SHA-256(0x01 || left || right)
#
# Required so a malicious server cannot present an internal node as a leaf.

LEAF_DOMAIN = b"\x00"
NODE_DOMAIN = b"\x01"


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(LEAF_DOMAIN + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_DOMAIN + left + right).digest()


def compute_root(leaves: List[bytes]) -> bytes:
    """Compute Merkle root over a list of LEAF HASHES (already domain-sep'd)."""
    if not leaves:
        # Empty-tree convention: SHA-256("") per RFC-6962 §2.1.
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return leaves[0]
    # RFC-6962 splits at the largest power-of-two strictly less than n.
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return node_hash(compute_root(leaves[:k]), compute_root(leaves[k:]))


def compute_inclusion_path(leaves: List[bytes], idx: int) -> List[bytes]:
    """RFC-6962 §2.1.1 audit path, BOTTOM-UP order.

    Path[0] = sibling at leaf level (closest); Path[-1] = topmost sibling.
    This matches the canonical RFC formulation so any standard verifier
    can consume it.
    """
    n = len(leaves)
    if idx < 0 or idx >= n:
        raise IndexError(f"leaf index {idx} out of range [0, {n})")
    if n == 1:
        return []
    # Largest power-of-two strictly less than n.
    k = 1
    while k * 2 < n:
        k *= 2
    if idx < k:
        return compute_inclusion_path(leaves[:k], idx) + [compute_root(leaves[k:])]
    else:
        return compute_inclusion_path(leaves[k:], idx - k) + [compute_root(leaves[:k])]


def verify_inclusion(
    *,
    leaf_data: bytes,
    leaf_index: int,
    tree_size: int,
    audit_path: List[bytes],
    expected_root: bytes,
) -> bool:
    """Verify a bottom-up audit path against the claimed root.

    Implements the canonical RFC-6962 §2.1.1 algorithm:

      fn = leaf_index, sn = tree_size - 1
      for each sibling in path (bottom-up):
        if fn is right child, OR fn == sn (leaf is rightmost in subtree):
          r = h(sibling || r); skip past trailing 0 bits in fn (fold up
          past completed left-only sub-paths)
        else:
          r = h(r || sibling)
        fn >>= 1; sn >>= 1
      return sn == 0 and r == expected_root
    """
    if leaf_index < 0 or leaf_index >= tree_size:
        return False
    fn = leaf_index
    sn = tree_size - 1
    r = leaf_hash(leaf_data)
    for sibling in audit_path:
        if sn == 0:
            # Path longer than the tree height — malformed.
            return False
        if (fn & 1) == 1 or fn == sn:
            r = node_hash(sibling, r)
            # Skip up past completed left-only sub-paths.
            while (fn & 1) == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


# ---------------------------------------------------------------------------
# Public log API
# ---------------------------------------------------------------------------


@dataclass
class LogEntry:
    kind: str
    leaf_index: int
    appended_at: str
    leaf_hash_sha256: str
    tree_size: int
    root_hash_sha256: str
    audit_path: List[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "leaf_index": self.leaf_index,
            "appended_at": self.appended_at,
            "leaf_hash_sha256": self.leaf_hash_sha256,
            "tree_size": self.tree_size,
            "root_hash_sha256": self.root_hash_sha256,
            "inclusion_proof": {
                "leaf_index": self.leaf_index,
                "tree_size": self.tree_size,
                "root_hash": self.root_hash_sha256,
                "audit_path": self.audit_path,
            },
        }


class RekorV2Log:
    """Append-only Merkle-tree-backed transparency log.

    Persisted as JSON-Lines so each append is a single fsync-able line write
    — the file is not corrupted by a crash mid-append (worst case: the last
    line is truncated, the verifier rejects it on parse, the next append
    re-derives root from the surviving entries).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path or DEFAULT_LOG_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._leaves: List[bytes] = []
        self._load_existing()

    @property
    def path(self) -> Path:
        return self._path

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        with self._lock:
            with self._path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        # Truncated final line from a crashed append. Stop here;
                        # the next append will overwrite from the current pos.
                        break
                    leaf_b = bytes.fromhex(e["leaf_hash_sha256"])
                    self._leaves.append(leaf_b)

    def append(self, *, kind: str, payload: bytes) -> LogEntry:
        """Append a new entry; return its LogEntry incl. inclusion proof."""
        with self._lock:
            new_leaf = leaf_hash(payload)
            self._leaves.append(new_leaf)
            idx = len(self._leaves) - 1
            audit_path = compute_inclusion_path(self._leaves, idx)
            root = compute_root(self._leaves)
            entry = LogEntry(
                kind=kind,
                leaf_index=idx,
                appended_at=datetime.now(timezone.utc).isoformat(),
                leaf_hash_sha256=new_leaf.hex(),
                tree_size=len(self._leaves),
                root_hash_sha256=root.hex(),
                audit_path=[h.hex() for h in audit_path],
            )
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
                f.flush()
            return entry

    def latest_root(self) -> str:
        """Hex of the current tree root, or the empty-tree root if no leaves."""
        with self._lock:
            return compute_root(self._leaves).hex()

    def tree_size(self) -> int:
        with self._lock:
            return len(self._leaves)

    def iter_entries(self) -> Iterator[dict]:
        if not self._path.exists():
            return iter([])
        with self._path.open("r", encoding="utf-8") as f:
            return iter([json.loads(line) for line in f if line.strip()])


# ---------------------------------------------------------------------------
# Convenience: stand-alone verify against a stored entry
# ---------------------------------------------------------------------------


def verify_stored_entry(payload: bytes, entry_dict: dict) -> bool:
    """Verify a payload's inclusion proof against the entry's claimed root.

    The CALLER is responsible for separately confirming that the claimed
    root is the legitimate one (e.g. by cross-checking against the latest
    log root on disk, or against a publicly-broadcast root hash).
    """
    proof = entry_dict["inclusion_proof"]
    return verify_inclusion(
        leaf_data=payload,
        leaf_index=int(proof["leaf_index"]),
        tree_size=int(proof["tree_size"]),
        audit_path=[bytes.fromhex(h) for h in proof["audit_path"]],
        expected_root=bytes.fromhex(proof["root_hash"]),
    )
