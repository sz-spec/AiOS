import json
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import check_source_dispositions as csd


class SourceDispositionTests(unittest.TestCase):
    def test_every_open_path_has_one_typed_disposition(self):
        result = csd.validate()
        self.assertEqual(result["open_ledger_paths"], 1649)
        self.assertEqual(result["native_paths"], 121)
        self.assertEqual(result["non_native_paths"], 1528)
        self.assertEqual(result["categories"], {
            "documentation-history": 315,
            "hosted": 156,
            "release-operations": 42,
            "tests": 64,
            "vendor": 951,
        })

    def test_ledger_open_count_is_reconstructed_from_rows(self):
        ledger = json.loads(csd.LEDGER.read_text())
        counts = {status: 0 for status in csd.OPEN_STATUSES}
        for row in ledger["files"]:
            if row["status"] in counts:
                counts[row["status"]] += 1
        self.assertEqual(counts, {
            "canonical-diverged-review": 499,
            "missing-from-canonical": 1150,
        })
        self.assertEqual(sum(counts.values()), csd.validate()["open_ledger_paths"])

    def test_scope_digest_ignores_embedded_decisions_but_detects_source_drift(self):
        ledger = json.loads(csd.LEDGER.read_text())
        baseline = csd.ledger_scope_digest(ledger)
        embedded = deepcopy(ledger)
        embedded["files"][0]["decision"] = "review metadata"
        embedded["files"][0]["path_disposition"] = {"state": "test"}
        self.assertEqual(csd.ledger_scope_digest(embedded), baseline)
        drifted = deepcopy(ledger)
        first_source = next(iter(drifted["files"][0]["sources"].values()))
        first_source["sha256"] = "0" * 64
        self.assertNotEqual(csd.ledger_scope_digest(drifted), baseline)

    def _reject_mutation(self, mutate):
        document = json.loads(csd.NON_NATIVE.read_text())
        mutate(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps(document))
            with patch.object(csd, "NON_NATIVE", path):
                with self.assertRaises(csd.DispositionValidationError):
                    csd.validate()

    def test_duplicate_id_and_contradictory_selected_source_are_rejected(self):
        self._reject_mutation(
            lambda document: document["dispositions"][1].update(
                id=document["dispositions"][0]["id"]
            )
        )
        self._reject_mutation(
            lambda document: document["dispositions"][0].update(
                selected_source="VOS3"
            )
        )

    def test_source_fingerprint_and_vos3_legal_gate_are_enforced(self):
        self._reject_mutation(
            lambda document: document["dispositions"][0].update(
                source_fingerprint="0" * 64
            )
        )
        def remove_legal_gate(document):
            group = next(
                group for group in document["dispositions"]
                if group["id"] == "hosted-agent-builder"
            )
            group.update(
                disposition="needs-experiment",
                state="validation-open",
                selected_source="undecided-pending-experiment",
            )
        self._reject_mutation(remove_legal_gate)

    def test_optimized_python_cannot_bypass_validation(self):
        document = json.loads(csd.NON_NATIVE.read_text())
        document["dispositions"][0]["selected_source"] = "VOS3"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps(document))
            code = (
                "from pathlib import Path; "
                "import check_source_dispositions as c; "
                "c.NON_NATIVE=Path(__import__('sys').argv[1]); c.validate()"
            )
            result = subprocess.run(
                [sys.executable, "-O", "-c", code, str(path)],
                cwd=csd.ROOT / "consolidation",
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("selected_source contradicts disposition", result.stderr)

    def test_status_documents_match_authoritative_counts(self):
        ledger = json.loads(csd.LEDGER.read_text())
        variants = json.loads(
            (csd.ROOT / "consolidation/reconciliation/variant-review.json").read_text()
        )
        self.assertEqual(len(ledger["canonical_only_files"]), 1563)
        self.assertEqual(len(variants), 2726)
        self.assertEqual(len({item["path"] for item in variants}), 1542)
        for relative in (
            "consolidation/reconciliation/README.md",
            "docs/design/UNIFICATION_STATUS_2026-09-18_HE.md",
        ):
            text = (csd.ROOT / relative).read_text()
            for value in ("1,649", "1,528", "1,563", "1,566", "2,726"):
                self.assertIn(value, text, (relative, value))
            for value in ("1,510", "2,719"):
                self.assertNotIn(value, text, (relative, value))


if __name__ == "__main__":
    unittest.main()
