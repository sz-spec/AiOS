"""Host-runner failure controls; no Docker daemon or application probes used."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "backend_runtime_smoke", Path(__file__).with_name("backend_runtime_smoke.py")
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class DockerState:
    """Model server-side creation independently of the client's response."""

    def __init__(self, timeout_at, mismatched=False):
        self.timeout_at = timeout_at
        self.mismatched = mismatched
        self.volumes = {}
        self.containers = {}
        self.calls = []
        self.secrets = []

    def run(self, argv, *, env, text, capture_output, timeout):
        assert argv[0] == "docker"
        self.calls.append(argv[1:])
        self.secrets = [env[key] for key in runner.SECRET_KEYS]
        parts = argv[1:]
        stdout = ""
        if parts[0] == "version":
            stdout = "29.0.0\n"
        elif parts[:2] == ["image", "inspect"]:
            stdout = "sha256:" + "a" * 64 + "\n"
        elif parts[:2] == ["volume", "create"]:
            owner = parts[parts.index("--label") + 1].split("=", 1)[1]
            self.volumes[parts[-1]] = "unrelated-owner" if self.mismatched else owner
            if self.timeout_at == "volume":
                raise subprocess.TimeoutExpired(argv, timeout)
            stdout = parts[-1]
        elif parts[0] == "run":
            name = parts[parts.index("--name") + 1]
            owner = parts[parts.index("--label") + 1].split("=", 1)[1]
            self.containers[name] = "unrelated-owner" if self.mismatched else owner
            raise subprocess.TimeoutExpired(argv, timeout)
        elif parts[0] == "inspect":
            stdout = self.containers[parts[-1]] + "\n"
        elif parts[:2] == ["volume", "inspect"]:
            stdout = self.volumes[parts[-1]] + "\n"
        elif parts[0] == "logs":
            # Exercise the real redactor and its exception inside cleanup.
            stdout = "synthetic log canaries: " + " ".join(self.secrets)
        elif parts[0] == "rm":
            del self.containers[parts[-1]]
        elif parts[:2] == ["volume", "rm"]:
            del self.volumes[parts[-1]]
        else:
            raise AssertionError("Unexpected Docker command: " + repr(parts))
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class HostCleanupTests(unittest.TestCase):
    def execute(self, docker):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence"
            args = SimpleNamespace(output=str(output), image="synthetic:test", timeout=1)
            console = io.StringIO()
            with patch.object(runner.subprocess, "run", side_effect=docker.run), \
                    contextlib.redirect_stdout(console):
                status = runner.outside(args)
            files = {path.name: path.read_text() for path in output.iterdir()}
            result = json.loads(files["result.json"])
            self.assertEqual(status, 1)
            self.assertFalse(result["passed"])
            self.assertTrue(result["error"].startswith("TimeoutExpired:"))
            self.assertEqual(result["checks"], [])
            self.assertFalse(any(call[0] == "exec" for call in docker.calls))
            for secret in docker.secrets:
                self.assertNotIn(secret, console.getvalue())
                for name, content in files.items():
                    self.assertNotIn(secret, content, name)
            return result, files

    def test_volume_created_before_client_timeout_is_removed(self):
        docker = DockerState("volume")
        result, _ = self.execute(docker)
        volume = result["run_id"] + "-data"
        self.assertEqual(docker.volumes, {})
        self.assertIn(["volume", "rm", volume], docker.calls)
        self.assertIn({"volume": volume, "removed": True}, result["cleanup"])
        self.assertFalse(any(call[0] == "run" for call in docker.calls))

    def test_run_timeout_and_log_canaries_do_not_prevent_cleanup(self):
        docker = DockerState("run")
        result, files = self.execute(docker)
        name = result["run_id"] + "-1"
        volume = result["run_id"] + "-data"
        self.assertIn({"container": name, "log_error": "AssertionError"}, result["cleanup"])
        self.assertIn({"container": name, "removed": True}, result["cleanup"])
        self.assertIn({"volume": volume, "removed": True}, result["cleanup"])
        self.assertLess(docker.calls.index(["logs", name]), docker.calls.index(["rm", "-f", name]))
        self.assertEqual(docker.containers, {})
        self.assertEqual(docker.volumes, {})
        self.assertEqual(files[name + "-server.log"].count("[REDACTED]"), len(runner.SECRET_KEYS))

    def test_mismatched_labels_preserve_unrelated_resources(self):
        docker = DockerState("run", mismatched=True)
        result, _ = self.execute(docker)
        name = result["run_id"] + "-1"
        volume = result["run_id"] + "-data"
        self.assertEqual(docker.containers, {name: "unrelated-owner"})
        self.assertEqual(docker.volumes, {volume: "unrelated-owner"})
        self.assertIn({"container": name, "ownership_error": "AssertionError"}, result["cleanup"])
        self.assertIn({"volume": volume, "error": "AssertionError"}, result["cleanup"])
        self.assertFalse(any(call[0] in ("rm", "logs") or call[:2] == ["volume", "rm"] for call in docker.calls))


if __name__ == "__main__":
    unittest.main()
