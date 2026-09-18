"""Compile and execute the production AI quota primitive with host sanitizers."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AIOOMHostTests(unittest.TestCase):
    def test_production_quota_state_machine(self):
        configurations = (
            ("sanitized", "-O1", "-fno-omit-frame-pointer",
             "-fsanitize=address,undefined"),
            ("optimized", "-O3", "-flto"),
            ("release", "-O3", "-flto", "-DNDEBUG"),
        )
        with tempfile.TemporaryDirectory(prefix="vos-ai-oom-") as directory:
            for configuration in configurations:
                name, *extra_flags = configuration
                with self.subTest(configuration=name):
                    binary = Path(directory) / f"ai_oom_host-{name}"
                    build = subprocess.run(
                        [
                            "cc",
                            "-std=c11",
                            "-Wall",
                            "-Wextra",
                            "-Werror",
                            *extra_flags,
                            "-I",
                            str(ROOT / "kernel/include"),
                            str(ROOT / "kernel/tests/ai_oom_host.c"),
                            str(ROOT / "kernel/src/mm/ai_oom.c"),
                            "-o",
                            str(binary),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(build.returncode, 0, build.stderr)
                    run = subprocess.run(
                        [str(binary)], capture_output=True, text=True, timeout=10
                    )
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
