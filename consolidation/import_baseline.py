"""Import the tracked vos.v1 working tree into the canonical repository.

No Git history, credentials, ignored files or release automation are imported.
The complete plan is checked before copying; existing destination files must
match, so rerunning can never overwrite integrated work. The manifest records
the actual working-tree content, including tracked local modifications.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT.parent
SOURCE = "vos.v1"
CODE_DIRS = {"backend", "frontend", "kernel", "desktop", "sdk", "user",
             "cli_system", "tools", "scripts", "tests", "infra", "docs",
             "LICENSES", ".reuse", "bin"}
ROOT_FILES = {"LICENSE", "SPDX.md", "CONTRIBUTING.md", "Dockerfile.kernel",
              "docker-compose.yml", "nginx.conf", "nginx.conf.template",
              "requirements.txt", "requirements.lock", "requirements_win.txt",
              "ruff.toml", ".python-version", ".env.example", "vercel.json",
              "deploy.sh", "redeploy.sh", "run.ps1", "run_all_tests.sh",
              "setup.ps1", "setup.sh", "update.sh", "test_convex_connection.py"}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def destination(path: str) -> tuple[str | None, str]:
    parts = Path(path).parts
    if not parts or Path(path).is_absolute() or ".." in parts:
        raise ValueError(f"Unsafe source path: {path}")
    if any(p == ".git" or p in {"node_modules", "__pycache__", ".next"}
           or p.startswith(".venv") for p in parts):
        return None, "repository metadata or generated dependency/cache"
    if (any(p.startswith(".env") and not p.endswith((".example", ".template"))
            for p in parts) or Path(path).suffix in {".pem", ".key", ".p12", ".pfx"}):
        return None, "local configuration or key material; review separately"
    if path.startswith("infra/persistence/"):
        return None, "historical agent session state; not runtime code"
    if parts[0] in CODE_DIRS or path in ROOT_FILES:
        return path, "canonical baseline"
    if len(parts) == 1 and path.endswith(".md"):
        return f"docs/legacy/vos-v1/{path}", "historical source documentation"
    return None, "historical automation, agent tooling or release evidence; deferred"


def validate_copy(source: Path, target: Path, expected: str, mode: int) -> None:
    if source.is_symlink() or not source.is_file() or digest(source) != expected:
        raise ValueError(f"Source changed or is not a regular file: {source}")
    if any(parent.is_symlink() for parent in (target, *target.parents)):
        raise ValueError(f"Destination traverses a symlink: {target}")
    if target.exists() and (not target.is_file() or digest(target) != expected
                            or (target.stat().st_mode & 0o777) != mode):
        raise ValueError(f"Refusing to overwrite canonical work: {target}")


def main() -> None:
    source_root = SOURCE_ROOT / SOURCE
    head = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True).strip()
    entries = subprocess.check_output(
        ["git", "-C", str(source_root), "ls-files", "--stage", "-z"]).decode().split("\0")
    imported, deferred, plan = [], [], []
    for entry in filter(None, entries):
        metadata, path = entry.split("\t", 1)
        git_mode, blob, stage = metadata.split()
        if stage != "0":
            raise ValueError(f"Unresolved source merge: {path}")
        target, reason = destination(path)
        if git_mode not in {"100644", "100755"}:
            target, reason = None, "non-regular Git entry; needs explicit integration"
        if target is None:
            deferred.append({"path": path, "reason": reason})
            continue
        source_path, target_path = source_root / path, ROOT / target
        expected = digest(source_path)
        mode = 0o755 if git_mode == "100755" else 0o644
        validate_copy(source_path, target_path, expected, mode)
        plan.append((source_path, target_path, expected, mode))
        imported.append({"source_path": path, "path": target,
                         "sha256": expected, "git_blob": blob, "mode": git_mode})
    for source_path, target_path, expected, mode in plan:
        validate_copy(source_path, target_path, expected, mode)
        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
            target_path.chmod(mode)
        if digest(target_path) != expected:
            raise ValueError(f"Copy verification failed: {target_path}")
    manifest = {"source": SOURCE, "head": head,
                "content": "tracked working-tree files (not git show HEAD)",
                "imported": imported, "deferred": deferred}
    (ROOT / "consolidation" / "baseline.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    print(f"Imported and verified {len(imported)} files; deferred {len(deferred)} entries.")


if __name__ == "__main__":
    main()
