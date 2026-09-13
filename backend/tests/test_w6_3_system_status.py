"""
W6.3 — backend /api/system/status verification.

Exercises the new public status endpoint through TestClient:

  - Reachable WITHOUT auth (locality is pre-login data).
  - Locality field reflects $VOS3_LOCALITY_PREFERENCE.
  - Database label flips SQLite/Convex with the env.
  - is_airgapped is False when cloud creds exist, True otherwise.
  - Reachable WITHOUT a CSRF token (GET).

Runs under the same loopback kill-switch as W5.3 / W6.1 so it stays
honest about not initiating outbound calls during a status read.

Run:
  python3 backend/tests/test_w6_3_system_status.py
"""

from __future__ import annotations

import os
import sys
import socket as _socket_module
import pathlib
import tempfile

_TMP_SQLITE = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_SQLITE.close()

os.environ["VOS_PROFILE"] = "community"
os.environ["VOS3_LOCALITY_PREFERENCE"] = "local-first"
os.environ["VOS3_LOCAL_DB_PATH"] = _TMP_SQLITE.name
os.environ["ENVIRONMENT"] = "development"
# Belt-and-braces: clear any cloud creds + Clerk keys.
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

_network_violations: list[str] = []
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _install_network_guard() -> None:
    real_cls = _socket_module.socket

    class GuardedSocket(real_cls):  # type: ignore[misc, valid-type]
        def connect(self, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) and address else None
            if isinstance(host, str) and host in _LOOPBACK_HOSTS:
                return super().connect(address, *args, **kwargs)
            _network_violations.append(f"connect to {address!r}")
            raise IOError(f"W6.3 air-gap violated: {address!r}")

    _socket_module.socket = GuardedSocket  # type: ignore[assignment]


# IMPORTANT: the guard is installed ONLY when this file runs directly as a
# script (its main() self-check at the bottom), NOT at import time. This module
# is script-style and has no pytest test functions, but pytest still imports it
# during collection on every xdist worker. Installing the guard at import scope
# here permanently replaced socket.socket on every worker and poisoned unrelated
# suites (test_hitl_memory's SentenceTransformer HF-Hub fetch, the Stripe
# dev-mode suite) with spurious air-gap violations. The install now lives in the
# __main__ block so collection never touches the global socket.


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# `tests/api/__init__.py` is a regular package that shadows
# `backend/api/` (which has no __init__.py — namespace package).
# Under PEP 420 the regular package wins regardless of sys.path
# order, so a plain `from api.system_routes import ...` resolves
# against tests/api/ and 404s. Load the W6.3 module by file path
# instead — bypasses the package-shadow entirely without touching
# the rest of the tests/ tree.
import importlib.util  # noqa: E402

_system_routes_path = (
    pathlib.Path(__file__).resolve().parent.parent / "api" / "system_routes.py"
)
_spec = importlib.util.spec_from_file_location(
    "_w6_3_system_routes",
    str(_system_routes_path),
)
_system_routes_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_system_routes_mod)
system_router = _system_routes_mod.router
from services.llm_dispatcher import _reset_dispatcher_for_tests  # noqa: E402


def _show(label: str, ok: bool, detail: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {label}{('  → ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


def _client_with(env_overrides: dict[str, str]) -> TestClient:
    for k, v in env_overrides.items():
        os.environ[k] = v
    _reset_dispatcher_for_tests()
    app = FastAPI()
    app.include_router(system_router)
    return TestClient(app)


def main() -> None:
    print("=" * 60)
    print("W6.3 — /api/system/status verification")
    print("=" * 60)

    # Scenario A: local-first + no cloud creds → sovereign
    print("→ Scenario A: local-first + no cloud creds (sovereign)")
    client = _client_with({"VOS3_LOCALITY_PREFERENCE": "local-first"})
    r = client.get("/api/system/status")
    _show("status=200 (no auth required)", r.status_code == 200)
    body = r.json()
    _show("locality == 'local-first'", body["locality"] == "local-first")
    _show("database == 'SQLite'", body["database"] == "SQLite")
    _show("llm_provider == 'local-first'", body["llm_provider"] == "local-first")
    _show("is_airgapped == True", body["is_airgapped"] is True)
    _show("version is non-empty", bool(body.get("version")))

    # Scenario B: local-first BUT cloud creds present
    print()
    print("→ Scenario B: local-first + OPENAI_API_KEY set")
    os.environ["OPENAI_API_KEY"] = "sk-fake-not-real-do-not-use"
    client = _client_with({"VOS3_LOCALITY_PREFERENCE": "local-first"})
    r = client.get("/api/system/status")
    body = r.json()
    _show("locality still 'local-first'", body["locality"] == "local-first")
    _show(
        "is_airgapped flips to False (cloud creds present)",
        body["is_airgapped"] is False,
    )

    # Scenario C: auto mode → cloud surface
    print()
    print("→ Scenario C: auto mode")
    client = _client_with({"VOS3_LOCALITY_PREFERENCE": "auto"})
    r = client.get("/api/system/status")
    body = r.json()
    _show("locality == 'auto'", body["locality"] == "auto")
    _show("database == 'Convex'", body["database"] == "Convex")
    _show("llm_provider == 'cloud'", body["llm_provider"] == "cloud")
    _show("is_airgapped == False", body["is_airgapped"] is False)

    # Scenario D: malformed env still normalizes
    print()
    print("→ Scenario D: malformed env value → 'auto' fallback")
    client = _client_with({"VOS3_LOCALITY_PREFERENCE": "bogus-value-zzz"})
    r = client.get("/api/system/status")
    body = r.json()
    _show("locality normalized to 'auto'", body["locality"] == "auto")

    # Network isolation
    print()
    print("→ Network isolation")
    _show(
        "zero non-loopback connect attempts",
        len(_network_violations) == 0,
        f"violations={_network_violations[:3]}",
    )

    print()
    print("✓ All W6.3 /api/system/status assertions passed.")


def cleanup() -> None:
    try:
        os.unlink(_TMP_SQLITE.name)
    except OSError:
        pass


if __name__ == "__main__":
    _install_network_guard()
    try:
        main()
    finally:
        cleanup()
