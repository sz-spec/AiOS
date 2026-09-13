#!/usr/bin/env python3
"""Bounded production-container qualification, with disposable synthetic data.

Runs the image's real CMD twice using the same owned volume. No host ports or
external services are used. The persistence probe supplies deterministic test
embeddings; it qualifies storage, not downloaded model quality or Clerk/Convex.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

PREFIX = "VOS_RUNTIME_RESULT "
SECRET_KEYS = (
    "JWT_SECRET", "VOS3_TAURI_IPC_SECRET", "VOS_API_SECRET", "CLERK_SECRET_KEY",
    "CLERK_WEBHOOK_SECRET", "CONVEX_DEPLOY_KEY", "VOS_SMOKE_MARKER",
)
BACKEND_INPUTS = ("Dockerfile", "requirements.lock", "app.py", "startup.py", "router_registry.py",
                  "memory/dev_memory.py", "middleware/auth.py", "middleware/csrf.py", "tools/auth.py",
                  "api/memory_routes.py", "core/database/sqlite_setup.py", "config/secrets.py", "db/convex.py")


def source_hashes(root):
    return {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in BACKEND_INPUTS}


def emit(value):
    print(PREFIX + json.dumps(value, sort_keys=True), flush=True)


def request(path, headers=None):
    req = urllib.request.Request("http://127.0.0.1:8000" + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


def inside(phase):
    sys.path.insert(0, "/app")
    assert os.getuid() == 1000
    assert os.environ["ENVIRONMENT"] == "production"
    assert os.environ.get("VOS3_ALLOW_DEV_MODE", "").lower() not in {"1", "yes", "true"}
    if phase == "http":
        status, health = request("/health")
        assert status == 200 and health["status"] == "healthy"
        required = {"control_plane", "business_core", "workflow_engine", "mission_control", "agent_registry"}
        assert all(health["services"].get(key) is True for key in required)
        denied = []
        for headers in ({}, {"x-api-key": os.environ["VOS_API_SECRET"]},
                        {"x-api-key": os.environ["VOS_API_SECRET"], "Authorization": "Bearer malformed"}):
            code, _ = request("/api/memory/recent", headers)
            assert code in (401, 403), "protected endpoint did not reject credentials"
            denied.append(code)
        emit({"phase": phase, "health": health, "protected_denials": denied})
        return
    if phase == "inventory":
        try:
            Path("/app/.runtime-root-write").write_text("must fail")
        except OSError as error:
            assert error.errno == errno.EROFS, "root was not verified read-only"
        else:
            raise AssertionError("image root unexpectedly writable")
        for key in ("VOS_DEV_MEMORY_DIR", "VOS_MEMORY_TENANT_ROOT", "VOS3_LOCAL_DB_PATH",
                    "VOS3_LOCAL_CHROMA_PATH", "VOS3_APP_DATA_DIR", "HF_HOME"):
            assert Path(os.environ[key]).is_relative_to("/app/data"), key
        for directory in ("/app/data", "/app/.cache", "/tmp"):
            p = Path(directory) / ".runtime-writable"
            p.write_text("owned smoke probe")
            p.unlink()
        from middleware.auth import DEV_MODE
        assert DEV_MODE is False
        from router_registry import discover_routers
        routers = discover_routers()
        assert len(routers) == 40 and all(value is not None for value in routers.values())
        assert {"billing", "team", "github", "template", "terminal", "version_control", "theme", "memory"} <= routers.keys()
        from app import create_app
        app = create_app()
        paths = app.openapi()["paths"]
        assert "/api/memory/recent" in paths and "/api/billing/plans" in paths
        emit({"phase": phase, "router_groups": sorted(routers), "openapi_paths": len(paths),
              "uid": os.getuid(), "read_only_root": True, "dev_auth": DEV_MODE,
              "source_sha256": source_hashes("/app")})
        return
    if phase == "negative":
        cases = {
            "JWT_SECRET": ("from tools.auth import JWT_SECRET", "JWT_SECRET is required in production"),
            "VOS3_TAURI_IPC_SECRET": ("import middleware.csrf", "VOS3_TAURI_IPC_SECRET must be set in production"),
            "CLERK_SECRET_KEY": ("import middleware.auth", "CLERK_SECRET_KEY is required"),
            "VOS_API_SECRET": ("from app import create_app; create_app()", "VOS_API_SECRET must be set"),
        }
        for key, (code, expected) in cases.items():
            env = dict(os.environ, PYTHONPATH="/app")
            env.pop(key, None)
            result = subprocess.run([sys.executable, "-c", code], env=env, cwd="/app",
                                    capture_output=True, text=True, timeout=90)
            assert result.returncode != 0 and expected in result.stderr, key
            assert all(os.environ[value] not in result.stdout + result.stderr for value in SECRET_KEYS)
        emit({"phase": phase, "missing_secret_refusals": list(cases)})
        return
    assert phase in {"write", "read"}
    from core.database.sqlite_setup import User, get_session, init_db
    marker = os.environ["VOS_SMOKE_MARKER"]
    record = Path("/app/data/runtime-smoke-record.json")
    if phase == "read":
        assert Path(os.environ["VOS3_LOCAL_DB_PATH"]).is_file()
        expected = json.loads(record.read_text())
    init_db()
    with get_session() as session:
        if phase == "write":
            assert session.get(User, "runtime-smoke-user") is None
            session.add(User(id="runtime-smoke-user", clerkId="runtime-smoke-user",
                             email="runtime@example.invalid", fullName=marker,
                             createdAt=1, updatedAt=1))
            session.commit()
        row = session.get(User, "runtime-smoke-user")
        assert row is not None and row.fullName == marker
    # Only model inference is replaced in this probe process. The actual
    # DevMemory code, principal namespaces and persistent Chroma stay active.
    import numpy as np
    import memory.dev_memory as memory

    class TestEmbedding:
        def encode(self, text):
            return np.array([1.0, 0.5, 0.25], dtype=np.float32)

    memory._shared_embedding_model = lambda name: TestEmbedding()
    a = memory.get_user_dev_memory("runtime-principal-a")
    b = memory.get_user_dev_memory("runtime-principal-b")
    assert a._initialized and b._initialized, "Chroma fallback would invalidate this probe"
    assert a._collection is not None and b._collection is not None
    assert isinstance(a._embedding_model, TestEmbedding) and isinstance(b._embedding_model, TestEmbedding)
    assert a.persist_dir != b.persist_dir
    if phase == "write":
        assert a.get_stats()["total_memories"] == 0 and b.get_stats()["total_memories"] == 0
        entry = a.add(marker, metadata={"actor": "runtime-principal-a"})
        assert entry is not None
        expected = {"memory_id": entry.id, "marker_sha256": hashlib.sha256(marker.encode()).hexdigest()}
        record.write_text(json.dumps(expected))
    memory_id = expected["memory_id"]
    assert expected["marker_sha256"] == hashlib.sha256(marker.encode()).hexdigest()
    assert a.get_by_id(memory_id)["content"] == marker
    assert b.get_by_id(memory_id) is None
    assert not b.update(memory_id, content="unauthorized change")
    assert not b.delete(memory_id)
    assert all(item.get("content") != marker for item in b.get_recent() + b.export_all() + b.query(marker))
    assert a.get_by_id(memory_id)["content"] == marker
    emit({"phase": phase, "sqlite_row_verified": True, "chroma_persistence": True,
          "store_principal_noninterference": True, "memory_id": memory_id,
          "marker_sha256": expected["marker_sha256"], "embedding": "deterministic test adapter"})


def outside(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_id = "vos5-runtime-" + secrets.token_hex(6)
    volume = run_id + "-data"
    env = dict(os.environ)
    for key in SECRET_KEYS:
        env[key] = secrets.token_urlsafe(40)
    env["CLERK_SECRET_KEY"] = "sk_test_" + env["CLERK_SECRET_KEY"]
    protected = [env[key] for key in SECRET_KEYS]
    result = {"run_id": run_id, "requested_image": args.image, "passed": False,
              "checks": [], "cleanup": [], "scope": "offline production startup and local persistence; no live cloud/model qualification"}
    result["source_sha256"] = source_hashes(Path(__file__).resolve().parents[1] / "backend")
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["phase_timeout_seconds"] = args.timeout
    started = time.monotonic()
    owned = []
    volume_created = False

    def command(parts, *, timeout=30, check=True, label=None):
        completed = subprocess.run(["docker", *parts], env=env, text=True, capture_output=True, timeout=timeout)
        raw = completed.stdout + completed.stderr
        leaked = any(value in raw for value in protected)
        safe = raw
        for value in protected:
            safe = safe.replace(value, "[REDACTED]")
        if label:
            (output / (label + ".log")).write_text(safe)
        if leaked:
            raise AssertionError("synthetic secret/content appeared in " + str(label))
        if check and completed.returncode:
            raise RuntimeError(f"Docker {parts[0]} failed ({completed.returncode}); see {label}")
        return completed

    def probe(name, phase):
        response = command(["exec", name, "python", "/runtime-probe/backend_runtime_smoke.py", "--inside", phase],
                           timeout=args.timeout, label=name + "-" + phase)
        lines = [line for line in response.stdout.splitlines() if line.startswith(PREFIX)]
        assert len(lines) == 1
        value = json.loads(lines[0][len(PREFIX):])
        if phase == "inventory":
            assert value["source_sha256"] == result["source_sha256"], "image/source mismatch"
        result["checks"].append(value)
        print(json.dumps({"completed": phase, "container": name}), flush=True)

    def capture(name):
        response = command(["logs", name], label=name + "-server", check=False)
        return response.stdout + response.stderr

    try:
        version = command(["version", "--format", "{{.Server.Version}}"], label="docker-version")
        result["docker_version"] = version.stdout.strip()
        identity = command(["image", "inspect", args.image, "--format", "{{.Id}}"], label="image")
        pinned = identity.stdout.strip()
        assert pinned.startswith("sha256:")
        result["image_id"] = pinned
        volume_created = True
        command(["volume", "create", "--label", "vos.runtime-smoke=" + run_id, volume], label="volume")
        ids = []
        for number in (1, 2):
            name = run_id + "-" + str(number)
            owned.append(name)
            options = ["run", "-d", "--name", name, "--label", "vos.runtime-smoke=" + run_id,
                       "--platform", "linux/amd64", "--network", "none", "--read-only", "--user", "1000:1000",
                       "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--memory", "2g", "--cpus", "2",
                       "--mount", "type=volume,src=" + volume + ",dst=/app/data",
                       "--mount", "type=bind,src=" + str(Path(__file__).resolve().parent) + ",dst=/runtime-probe,readonly",
                       "--tmpfs", "/tmp:rw,size=256m", "--tmpfs", "/app/.cache:rw,size=256m,uid=1000,gid=1000,mode=0700"]
            for value in ("ENVIRONMENT=production", "VOS_PROFILE=community", "VOS3_ALLOW_DEV_MODE=false",
                          "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "CONVEX_URL=https://runtime.invalid",
                          "OMP_NUM_THREADS=1", "TOKENIZERS_PARALLELISM=false"):
                options += ["-e", value]
            for key in SECRET_KEYS:
                options += ["-e", key]
            response = command(options + [pinned], label=name + "-create")
            ids.append(response.stdout.strip())
            assert len(ids[-1]) == 64 and len(set(ids)) == len(ids)
            deadline = time.monotonic() + args.timeout
            while True:
                logs = capture(name)
                if "Application startup complete." in logs:
                    break
                state = command(["inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}", name]).stdout.strip()
                if not state.startswith("true "):
                    raise RuntimeError("server exited before startup: " + state)
                if time.monotonic() >= deadline:
                    raise TimeoutError("server startup deadline")
                time.sleep(2)
            probe(name, "http")
            if number == 1:
                probe(name, "inventory")
                probe(name, "negative")
            probe(name, "write" if number == 1 else "read")
            command(["stop", "--time", "15", name], label=name + "-stop")
            logs = capture(name)
            assert "Application shutdown complete." in logs
            command(["rm", name], label=name + "-remove")
            result["cleanup"].append({"container": name, "removed": True})
            owned.remove(name)
        result["distinct_container_ids"] = ids
        result["passed"] = True
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    finally:
        for name in list(owned):
            try:
                label = command(["inspect", "--format", '{{index .Config.Labels "vos.runtime-smoke"}}', name], check=False)
                if label.returncode and "No such" in label.stderr:
                    result["cleanup"].append({"container": name, "absent": True})
                    continue
                assert label.returncode == 0 and label.stdout.strip() == run_id, "resource ownership unconfirmed"
            except Exception as error:
                result["cleanup"].append({"container": name, "ownership_error": type(error).__name__})
                result["passed"] = False
                continue
            try:
                capture(name)
            except Exception as error:
                result["cleanup"].append({"container": name, "log_error": type(error).__name__})
                result["passed"] = False
            try:
                response = command(["rm", "-f", name], label=name + "-cleanup", check=False)
                result["cleanup"].append({"container": name, "removed": response.returncode == 0})
                if response.returncode:
                    result["passed"] = False
            except Exception as error:
                result["cleanup"].append({"container": name, "error": type(error).__name__})
                result["passed"] = False
        if volume_created:
            try:
                label = command(["volume", "inspect", "--format", '{{index .Labels "vos.runtime-smoke"}}', volume], check=False)
                if label.returncode and "No such volume" in label.stderr:
                    result["cleanup"].append({"volume": volume, "absent": True})
                else:
                    assert label.returncode == 0 and label.stdout.strip() == run_id, "resource ownership unconfirmed"
                    response = command(["volume", "rm", volume], label="volume-cleanup", check=False)
                    result["cleanup"].append({"volume": volume, "removed": response.returncode == 0})
                    if response.returncode:
                        result["passed"] = False
            except Exception as error:
                result["cleanup"].append({"volume": volume, "error": type(error).__name__})
                result["passed"] = False
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"passed": result["passed"], "result": str(output / "result.json"), "error": result.get("error")}), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="vos5-backend:security-final")
    parser.add_argument("--output")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--inside", choices=("inventory", "http", "negative", "write", "read"))
    args = parser.parse_args()
    if args.inside:
        inside(args.inside)
    else:
        if not args.output:
            parser.error("--output must name a new directory")
        sys.exit(outside(args))
