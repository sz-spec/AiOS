"""Exercise the actual shell orchestrator with disposable fake test layers."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class UnifiedRunnerTests(unittest.TestCase):
    def run_fixture(self, args=(), backend="exit 0", kernel="exit 0", frontend=0,
                    missing=None):
        with tempfile.TemporaryDirectory(prefix="vos-runner-test-") as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "run_all_tests.sh", root / "run_all_tests.sh")
            for name, body in (("backend", backend), ("kernel", kernel)):
                (root / name).mkdir()
                if missing != name:
                    (root / name / "run_tests.sh").write_text(
                        'echo invoked >> "../' + name + '.count"\n' + body + '\n')
            (root / "frontend").mkdir()
            (root / "bin").mkdir()
            npm = root / "bin/npm"
            npm.write_text('#!/bin/sh\necho invoked >> ../frontend.count\nexit '
                           + str(frontend) + '\n')
            npm.chmod(0o755)
            env = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                       TMPDIR=str(root))
            result = subprocess.run(["bash", "run_all_tests.sh", *args], cwd=root,
                                    env=env, text=True, capture_output=True, timeout=10)
            counts = {name: (root / (name + ".count")).read_text().count("invoked")
                      if (root / (name + ".count")).exists() else 0
                      for name in ("backend", "frontend", "kernel")}
            return result, counts

    def test_each_layer_executes_once(self):
        result, counts = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(counts, dict(backend=1, frontend=1, kernel=1))

    def test_explicit_partial_run_is_reported(self):
        result, counts = self.run_fixture(args=("--no-kernel",))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(counts, dict(backend=1, frontend=1, kernel=0))
        self.assertIn("kernel tests were not run", result.stdout)
        self.assertNotIn("All requested test layers passed", result.stdout)

    def test_unknown_argument_does_not_run_tests(self):
        result, counts = self.run_fixture(args=("--no-kernal",))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(sum(counts.values()), 0)

    def test_failures_do_not_stop_other_layers_or_turn_green(self):
        for kwargs in ({"backend": "exit 7"}, {"frontend": 9}, {"kernel": "exit 3"}):
            with self.subTest(kwargs=kwargs):
                result, counts = self.run_fixture(**kwargs)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(counts, dict(backend=1, frontend=1, kernel=1))

    def test_missing_runner_and_legacy_skip_fail_requested_layer(self):
        for kwargs in ({"missing": "backend"}, {"missing": "kernel"},
                       {"kernel": 'echo "[SKIP] missing QEMU"; exit 0'},
                       {"backend": 'echo "[SKIP] missing Python"; exit 0'}):
            with self.subTest(kwargs=kwargs):
                result, _ = self.run_fixture(**kwargs)
                self.assertEqual(result.returncode, 1, result.stdout)

if __name__ == "__main__":
    unittest.main()
