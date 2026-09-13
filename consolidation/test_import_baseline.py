import tempfile
import unittest
from pathlib import Path

from import_baseline import destination, digest, validate_copy


class ImportSafetyTest(unittest.TestCase):
    def test_secrets_and_legacy_automation_are_not_imported(self):
        for path in (".env", "backend/.env.production", "infra/private.key",
                     ".github/workflows/release.yml", ".git/config"):
            self.assertIsNone(destination(path)[0], path)
        self.assertEqual(destination(".env.example")[0], ".env.example")
        self.assertEqual(destination("backend/app.py")[0], "backend/app.py")

    def test_path_escape_is_rejected(self):
        for path in ("../outside", "/tmp/outside", "backend/../../outside"):
            with self.assertRaises(ValueError):
                destination(path)

    def test_source_drift_and_canonical_edits_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / "source", root / "target"
            source.write_text("baseline")
            expected = digest(source)
            validate_copy(source, target, expected, 0o644)
            target.write_text("integrated changes")
            with self.assertRaisesRegex(ValueError, "overwrite"):
                validate_copy(source, target, expected, 0o644)
            source.write_text("changed after planning")
            with self.assertRaisesRegex(ValueError, "Source changed"):
                validate_copy(source, root / "new", expected, 0o644)

    def test_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, link = root / "source", root / "link"
            source.write_text("baseline")
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                validate_copy(source, link / "new", digest(source), 0o644)


if __name__ == "__main__":
    unittest.main()
