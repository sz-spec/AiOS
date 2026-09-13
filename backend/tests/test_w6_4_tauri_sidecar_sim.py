"""
W6.4 — simulated Tauri sidecar boot.

Mirrors the env vars the Rust sidecar (desktop/src-tauri/src/sidecar.rs)
emits when it spawns the FastAPI backend in the desktop shell, then
drives the system through TestClient under a network kill-switch to
verify the WHOLE air-gapped stack initializes correctly without
manual user intervention.

This is the closest thing to "tauri build then run" that we can run
without the Rust toolchain available — the contract between the Rust
spawn() and the Python backend is the env-var set, and we exercise
exactly that set.

What this validates:

  1. The env vars apply_env() sets in sidecar.rs reach the backend
     unchanged.
  2. /api/system/status returns the sovereign posture (locality,
     database, llm_provider, is_airgapped).
  3. The SQLite store lands at the Tauri app_data_dir path, not the
     user's ~/.vos/.
  4. The Chroma store lands at the Tauri app_data_dir path.
  5. The W3.3 handshake endpoint accepts the Rust-side secret.
  6. The W5.3 offline auth gate engages.
  7. Zero non-loopback connect attempts during the full boot dance.

Run:
  python3 backend/tests/test_w6_4_tauri_sidecar_sim.py
"""

from __future__ import annotations

import os
import pathlib
import socket as _socket_module
import sys
import tempfile
import shutil
import importlib.util

# ---------------------------------------------------------------------------
# Simulate the Tauri-canonical app_data_dir + the random handshake secret.
# These are exactly the values desktop/src-tauri/src/sidecar.rs would
# generate and inject into the child env on Linux:
#   apply_env() sets VOS3_LOCAL_DB_PATH = {app_data}/vos3.db
#                    VOS3_LOCAL_CHROMA_PATH = {app_data}/chroma_db
#                    VOS3_TAURI_IPC_SECRET = <64 hex chars>
# ---------------------------------------------------------------------------


_TAURI_APP_DATA = pathlib.Path(tempfile.mkdtemp(prefix="vos3-tauri-app-data-"))
_DB_PATH = _TAURI_APP_DATA / "vos3.db"
_CHROMA_PATH = _TAURI_APP_DATA / "chroma_db"

# 64 hex chars — matches BackendSidecar::generate_handshake_secret().
_HANDSHAKE = "a1b2c3d4e5f6" * 5 + "deadbeefdead0001"[:4]

# Pin the env BEFORE any backend import — sidecar.rs::apply_env() shape:
os.environ["VOS3_LOCALITY_PREFERENCE"] = "local-first"
os.environ["VOS_PROFILE"] = "community"
os.environ["VOS3_LOCAL_DB_PATH"] = str(_DB_PATH)
os.environ["VOS3_LOCAL_CHROMA_PATH"] = str(_CHROMA_PATH)
os.environ["VOS3_TAURI_IPC_SECRET"] = _HANDSHAKE
os.environ["VOS3_OFFLINE_AUTH"] = "true"
os.environ["ENVIRONMENT"] = "development"
# Force the deterministic embedder so the test does not try to download
# the sentence-transformers model on first use (sidecar.rs leaves this
# unset; the backend's auto-detection picks it up when installed).
os.environ["VOS3_EMBEDDING_BACKEND"] = "deterministic"

# Wipe stale cloud creds + Clerk keys.
for k in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "CLERK_SECRET_KEY",
    "CLERK_PUBLISHABLE_KEY",
    "CLERK_ISSUER_URL",
):
    os.environ.pop(k, None)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Network kill-switch — loopback only.
# ---------------------------------------------------------------------------


_network_violations: list[str] = []
_LOOPBACK = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _install_network_guard() -> None:
    real_cls = _socket_module.socket

    class GuardedSocket(real_cls):  # type: ignore[misc, valid-type]
        def connect(self, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) and address else None
            if isinstance(host, str) and host in _LOOPBACK:
                return super().connect(address, *args, **kwargs)
            _network_violations.append(f"connect to {address!r}")
            raise IOError(f"W6.4 air-gap violated: {address!r}")

    _socket_module.socket = GuardedSocket  # type: ignore[assignment]


# IMPORTANT: the guard is installed ONLY when this file runs directly as a
# script (its main() self-check at the bottom), NOT at import time. This module
# is script-style and has no pytest test functions, but pytest still imports it
# during collection on every xdist worker. Installing the guard at import scope
# here permanently replaced socket.socket on every worker and poisoned unrelated
# suites (test_hitl_memory's SentenceTransformer HF-Hub fetch, the Stripe
# dev-mode suite) with spurious air-gap violations. The install now lives in the
# __main__ block so collection never touches the global socket.


# ---------------------------------------------------------------------------
# Imports — safe now.
# ---------------------------------------------------------------------------


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Load api/system_routes.py by file path (PEP 420 namespace shadow —
# see W6.3 test for context).
_system_routes_path = (
    pathlib.Path(__file__).resolve().parent.parent / "api" / "system_routes.py"
)
_spec = importlib.util.spec_from_file_location(
    "_w6_4_system_routes",
    str(_system_routes_path),
)
_system_routes_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_system_routes_mod)
system_router = _system_routes_mod.router

from services.llm_dispatcher import _reset_dispatcher_for_tests  # noqa: E402
from middleware.csrf import (  # noqa: E402
    router as csrf_router,
    csrf_middleware,
    _peek_handshake_secret_for_tests,
)
from core.database.sqlite_setup import (
    resolve_db_path,
    _reset_for_tests as _reset_sqlite,
)  # noqa: E402
from core.database.vector_setup import (
    resolve_chroma_path,
    _reset_chroma_for_tests,
)  # noqa: E402

_reset_dispatcher_for_tests()
_reset_sqlite()
_reset_chroma_for_tests()


def _show(label: str, ok: bool, detail: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {label}{('  → ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


def _build_app() -> FastAPI:
    """Tiny app exposing the routes the Tauri shell needs on boot."""
    app = FastAPI()
    app.middleware("http")(csrf_middleware)
    app.include_router(csrf_router)
    app.include_router(system_router)
    return app


def main() -> None:
    print("=" * 64)
    print("W6.4 — simulated Tauri sidecar boot")
    print("=" * 64)
    print(f"  Tauri app_data_dir       : {_TAURI_APP_DATA}")
    print(f"  Simulated DB path        : {_DB_PATH}")
    print(f"  Simulated Chroma path    : {_CHROMA_PATH}")
    print(f"  Handshake secret (env)   : {_HANDSHAKE[:12]}…")
    print()

    app = _build_app()
    client = TestClient(app)

    # --- 1. SQLite path resolution honors the Tauri-injected env
    print("→ SQLite path resolution")
    resolved = resolve_db_path()
    _show(
        "SQLite path = Tauri app_data_dir/vos3.db",
        os.path.realpath(str(resolved)) == os.path.realpath(str(_DB_PATH)),
        f"resolved={resolved}",
    )

    # --- 2. Chroma path resolution
    print()
    print("→ Chroma path resolution")
    resolved_chroma = resolve_chroma_path()
    _show(
        "Chroma path = Tauri app_data_dir/chroma_db",
        os.path.realpath(str(resolved_chroma)) == os.path.realpath(str(_CHROMA_PATH)),
        f"resolved={resolved_chroma}",
    )

    # --- 3. /api/system/status reports sovereign mode
    print()
    print("→ GET /api/system/status")
    r = client.get("/api/system/status")
    _show("status=200 (public, no auth)", r.status_code == 200)
    body = r.json()
    _show("locality == 'local-first'", body["locality"] == "local-first")
    _show("database == 'SQLite'", body["database"] == "SQLite")
    _show("llm_provider == 'local-first'", body["llm_provider"] == "local-first")
    _show("is_airgapped == True", body["is_airgapped"] is True)

    # --- 4. CSRF handshake — the secret the Rust sidecar set is accepted
    print()
    print("→ GET /api/auth/csrf (W3.3 handshake)")
    # The middleware reads the env at import time; sanity-check that
    # our env value is the one it locked in.
    handshake_loaded = _peek_handshake_secret_for_tests()
    _show(
        "middleware loaded the Tauri-injected secret",
        handshake_loaded == _HANDSHAKE,
        f"loaded={handshake_loaded[:12]}…",
    )
    r = client.get("/api/auth/csrf", headers={"X-Tauri-Handshake": _HANDSHAKE})
    _show("status=200", r.status_code == 200)
    csrf_token = r.json().get("csrf_token", "")
    _show("returns a CSRF token", len(csrf_token) > 0)

    # --- 5. Wrong secret rejected
    r = client.get(
        "/api/auth/csrf", headers={"X-Tauri-Handshake": "not-the-real-secret"}
    )
    _show("wrong handshake → 401", r.status_code == 401)

    # --- 6. The simulated app_data_dir is created on-disk
    print()
    print("→ On-disk app_data_dir + storage init")
    # Trigger SQLite init by reading the resolved path.
    from core.database.sqlite_setup import init_db

    init_db()
    _show("app_data_dir exists", _TAURI_APP_DATA.is_dir())
    _show(
        "SQLite file created",
        _DB_PATH.exists() and _DB_PATH.stat().st_size > 0,
        f"size={_DB_PATH.stat().st_size if _DB_PATH.exists() else 0}",
    )

    # Trigger Chroma init similarly.
    from core.database.vector_setup import init_vector_db

    chroma_ok = init_vector_db()
    _show("Chroma client initialized", chroma_ok)
    chroma_contents = list(_CHROMA_PATH.iterdir()) if _CHROMA_PATH.exists() else []
    _show(
        "Chroma dir non-empty",
        len(chroma_contents) > 0,
        f"entries={[c.name for c in chroma_contents]}",
    )

    # --- 7. Network isolation invariant
    print()
    print("→ Network isolation")
    _show(
        "zero non-loopback connect attempts",
        len(_network_violations) == 0,
        f"violations={_network_violations[:3]}",
    )

    print()
    print("✓ All W6.4 simulated Tauri sidecar assertions passed.")


def cleanup() -> None:
    shutil.rmtree(_TAURI_APP_DATA, ignore_errors=True)


if __name__ == "__main__":
    _install_network_guard()
    try:
        main()
    finally:
        cleanup()
