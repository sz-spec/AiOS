#!/usr/bin/env python3
"""Validate that every unresolved source variant has one explicit disposition.

The byte ledger deliberately does not treat content identity as semantic
equivalence.  This verifier joins the independent-OS decisions with the
remaining product, test, release, vendor and historical decisions and rejects
gaps, duplicates, category drift and unsupported state/disposition pairs.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "consolidation/reconciliation/file-ledger.json"
NATIVE = ROOT / "consolidation/native-source-dispositions.json"
NON_NATIVE = ROOT / "consolidation/non-native-source-dispositions.json"

OPEN_STATUSES = {"missing-from-canonical", "canonical-diverged-review"}
EXPECTED_CATEGORIES = {
    "vendor": 951,
    "documentation-history": 315,
    "hosted": 156,
    "tests": 63,
    "release-operations": 42,
}
HOSTED_COMPONENTS = {
    "backend.agents", "backend.api", "backend.convex", "backend.core",
    "backend.extensions", "backend.mcp", "backend.middleware",
    "backend.services", "desktop.host", "frontend.data",
    "frontend.product", "sdk.clients",
}
TEST_COMPONENTS = {"backend.tests", "system.tests"}
RELEASE_COMPONENTS = {"ci.workflows", "infra.operations", "tooling"}
HISTORY_COMPONENTS = {
    "documentation", "backend.example-projects", "root.configuration",
}

ALLOWED_DISPOSITIONS = {
    "retain-canonical", "integrate-semantics", "superseded", "reject-stub",
    "needs-experiment", "vendor-review", "archive-provenance", "legal-review",
}
STATE_DISPOSITIONS = {
    "resolved-source-choice": {
        "retain-canonical", "superseded", "reject-stub", "archive-provenance",
    },
    "implementation-open": {"integrate-semantics"},
    "integrated-validation-open": {"integrate-semantics"},
    "validation-open": {"needs-experiment"},
    "upstream-review-open": {"vendor-review"},
    "legal-review-open": {"legal-review"},
}
REQUIRED_TEXT = {
    "rationale", "license", "security", "validation", "classification_basis",
}
SELECTED_SOURCE = {
    "retain-canonical": "canonical",
    "superseded": "canonical",
    "reject-stub": "canonical",
    "archive-provenance": "canonical",
    "integrate-semantics": "canonical-reimplementation",
    "needs-experiment": "undecided-pending-experiment",
    "vendor-review": "undecided-pending-upstream-review",
    "legal-review": "none-pending-legal-review",
}


class DispositionValidationError(ValueError):
    """A disposition file contradicts the source ledger or its schema."""


def _require(condition: bool, message) -> None:
    if not condition:
        raise DispositionValidationError(str(message))


def _load(path: Path):
    return json.loads(path.read_text())


def _paths(document: dict):
    for group in document["dispositions"]:
        for path in group["paths"]:
            yield path, group


def ledger_scope_digest(ledger: dict) -> str:
    """Hash source/canonical facts while excluding embedded decisions.

    `reconcile_sources.py` writes dispositions back into the ledger, so a hash
    of the complete JSON would be circular.  This projection changes whenever
    the source bytes, selected canonical bytes, scope, component or status
    changes, but remains stable when review metadata is embedded.
    """
    projection = [
        {
            "path": row["path"],
            "component": row["component"],
            "canonical_path": row["canonical_path"],
            "canonical_sha256": row["canonical_sha256"],
            "status": row["status"],
            "sources": {
                source: facts["sha256"]
                for source, facts in sorted(row["sources"].items())
            },
        }
        for row in ledger["files"]
    ]
    encoded = json.dumps(
        {"scope": ledger["scope"], "files": projection},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_fingerprint(paths: list[str], rows: dict[str, dict]) -> str:
    """Bind a review group to the exact canonical and donor bytes reviewed."""
    facts = []
    for path in sorted(paths):
        row = rows[path]
        facts.append({
            "path": path,
            "canonical_sha256": row["canonical_sha256"],
            "sources": {
                source: values["sha256"]
                for source, values in sorted(row["sources"].items())
            },
        })
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _expected_category(row: dict, release_additional: set[str]) -> str:
    path = row["path"]
    component = row["component"]
    if path.startswith(("user/musl/", "kernel/boot/limine/")):
        return "vendor"
    if component in HOSTED_COMPONENTS:
        return "hosted"
    if component in TEST_COMPONENTS:
        return "tests"
    if component in RELEASE_COMPONENTS or path in release_additional:
        return "release-operations"
    if component in HISTORY_COMPONENTS:
        return "documentation-history"
    raise DispositionValidationError(
        f"unclassified non-native path: {path} ({component})"
    )


def validate() -> dict:
    ledger = _load(LEDGER)
    native = _load(NATIVE)
    decisions = _load(NON_NATIVE)

    _require(decisions.get("schema_version") == 1, "unsupported schema version")
    _require(
        decisions.get("ledger_scope_sha256") == ledger_scope_digest(ledger),
        "source ledger scope digest mismatch",
    )
    release_additional = set(decisions["release_operations_additional_paths"])
    _require(len(release_additional) == 11, "release additional set must contain 11 paths")

    rows = {
        row["path"]: row for row in ledger["files"]
        if row["status"] in OPEN_STATUSES
    }
    native_paths = {path for path, _ in _paths(native)}
    _require(native_paths <= set(rows), "native dispositions contain non-open paths")
    expected = set(rows) - native_paths

    seen: dict[str, dict] = {}
    seen_ids = set()
    for group in decisions["dispositions"]:
        for key in ("id", "category", "component", "source_status",
                    "disposition", "state", "paths", "selected_source",
                    "source_fingerprint", *REQUIRED_TEXT):
            _require(key in group, f"{group.get('id', '<unknown>')}: missing {key}")
        _require(group["id"] not in seen_ids, f"duplicate disposition id: {group['id']}")
        seen_ids.add(group["id"])
        _require(group["category"] in EXPECTED_CATEGORIES, group["id"])
        _require(group["disposition"] in ALLOWED_DISPOSITIONS, group["id"])
        _require(group["state"] in STATE_DISPOSITIONS, group["id"])
        _require(
            group["disposition"] in STATE_DISPOSITIONS[group["state"]],
            f"{group['id']}: state/disposition mismatch",
        )
        _require(
            group["selected_source"] == SELECTED_SOURCE[group["disposition"]],
            f"{group['id']}: selected_source contradicts disposition",
        )
        _require(bool(group["paths"]), f"{group['id']}: empty paths")
        for key in REQUIRED_TEXT:
            _require(
                isinstance(group[key], str) and group[key].strip(),
                f"{group['id']}: empty {key}",
            )
        _require(
            group["source_fingerprint"] == source_fingerprint(group["paths"], rows),
            f"{group['id']}: source fingerprint mismatch",
        )
        donor_sources = set().union(*(set(rows[path]["sources"]) for path in group["paths"]))
        _require(
            not (
                donor_sources == {"VOS3"}
                and group["disposition"] in {"integrate-semantics", "needs-experiment"}
            ),
            f"{group['id']}: VOS3-only reuse requires legal-review-open",
        )
        for path in group["paths"]:
            _require(path not in seen, f"duplicate disposition: {path}")
            _require(path in expected, f"out-of-scope disposition: {path}")
            row = rows[path]
            _require(
                group["category"] == _expected_category(row, release_additional),
                f"category mismatch: {path}",
            )
            if group["component"] != "mixed":
                _require(group["component"] == row["component"], path)
            if group["source_status"] != "mixed":
                _require(group["source_status"] == row["status"], path)
            seen[path] = group

    _require(
        set(seen) == expected,
        {"missing": sorted(expected - set(seen)),
         "unexpected": sorted(set(seen) - expected)},
    )
    _require(release_additional <= set(seen), "release additional path is not decided")
    _require(
        all(
            rows[path]["component"] in {"root.configuration", "licensing"}
            for path in release_additional
        ),
        "release additional path has invalid component",
    )
    counts = Counter(group["category"] for group in seen.values())
    _require(dict(counts) == EXPECTED_CATEGORIES, dict(counts))

    states = Counter(group["state"] for group in seen.values())
    dispositions = Counter(group["disposition"] for group in seen.values())
    return {
        "open_ledger_paths": len(rows),
        "native_paths": len(native_paths),
        "non_native_paths": len(seen),
        "categories": dict(sorted(counts.items())),
        "dispositions": dict(sorted(dispositions.items())),
        "states": dict(sorted(states.items())),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2, sort_keys=True))
