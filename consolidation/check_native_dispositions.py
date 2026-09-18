#!/usr/bin/env python3
"""Validate complete, unique dispositions for the independent-OS review wave."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "consolidation/reconciliation/file-ledger.json"
DISPOSITIONS = ROOT / "consolidation/native-source-dispositions.json"

COMPONENTS = {
    "native.boot", "native.architecture", "native.memory", "native.scheduler",
    "native.execution", "native.ipc", "native.filesystems", "native.abi",
    "native.build-and-support", "native.crypto", "native.drivers",
    "native.network", "native.tests", "user.programs",
}
OPEN_STATUSES = {"missing-from-canonical", "canonical-diverged-review"}
ALLOWED = {
    "retain-canonical", "integrate-semantics", "superseded", "reject-stub",
    "needs-experiment",
}


def validate():
    ledger = json.loads(LEDGER.read_text())
    decisions = json.loads(DISPOSITIONS.read_text())
    expected = {
        row["path"]: row
        for row in ledger["files"]
        if row["component"] in COMPONENTS and row["status"] in OPEN_STATUSES
    }
    seen = {}
    for group in decisions["dispositions"]:
        assert group["component"] in COMPONENTS, group["id"]
        assert group["source_status"] in OPEN_STATUSES, group["id"]
        assert group["disposition"] in ALLOWED, group["id"]
        assert group["state"] in {
            "resolved-source-choice", "implementation-open",
            "integrated-validation-open",
        }, group["id"]
        assert group["paths"], group["id"]
        assert group["rationale"] and group["validation"], group["id"]
        if group["disposition"] == "needs-experiment":
            assert group["state"] == "implementation-open", group["id"]
        if group["disposition"] == "integrate-semantics":
            assert group["state"] in {
                "implementation-open", "integrated-validation-open",
            }, group["id"]
        for path in group["paths"]:
            assert path not in seen, f"duplicate disposition: {path}"
            assert path in expected, f"out-of-scope disposition: {path}"
            row = expected[path]
            assert row["component"] == group["component"], path
            assert row["status"] == group["source_status"], path
            if row["status"] == "missing-from-canonical":
                assert group["disposition"] != "retain-canonical", path
            seen[path] = group
    assert set(seen) == set(expected), {
        "missing": sorted(set(expected) - set(seen)),
        "extra": sorted(set(seen) - set(expected)),
    }
    return {
        "paths": len(seen),
        "dispositions": dict(sorted(Counter(
            group["disposition"] for group in seen.values()
        ).items())),
        "states": dict(sorted(Counter(
            group["state"] for group in seen.values()
        ).items())),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
