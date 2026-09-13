"""
W3.3 — standalone CSRF verification.

Bypasses the full app factory (which has Python 3.10+ syntax in
memory/dev_memory.py preventing 3.9 import) and tests the CSRF
middleware against a minimal FastAPI app that wires only:

  - middleware.csrf.csrf_middleware
  - middleware.csrf.router    (/api/auth/csrf handshake)
  - A bare /api/echo POST endpoint to exercise the gate
  - GET /api/ping for the no-CSRF-required path

Run with:
  VOS3_TAURI_IPC_SECRET=demo-secret python3 backend/tests/manual_csrf_verify.py
"""

import os
import sys
import pathlib

# Pin the secret BEFORE importing csrf so the volatile token + handshake
# secret stabilize to known values.
os.environ.setdefault("VOS3_TAURI_IPC_SECRET", "demo-handshake-secret")
os.environ.setdefault("ENVIRONMENT", "development")

# Make backend/ importable from this script.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from middleware.csrf import (
    csrf_middleware,
    router as csrf_router,
    _peek_csrf_token_for_tests,
    _peek_handshake_secret_for_tests,
)


def build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(csrf_middleware)
    app.include_router(csrf_router)

    # CORS — same Tauri-only allow-list as app.py.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://localhost:1420"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Tauri-Handshake",
        ],
    )

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/echo")
    def echo(body: dict):
        return {"echoed": body}

    @app.post("/api/webhooks/clerk")
    def fake_webhook():
        return {"received": True}

    return app


def show(label: str, passed: bool, details: str = "") -> None:
    icon = "PASS" if passed else "FAIL"
    print(f"  [{icon}] {label}{'  → ' + details if details else ''}")
    if not passed:
        sys.exit(1)


def main() -> None:
    print("=" * 62)
    print("W3.3 — CSRF + handshake verification (standalone)")
    print("=" * 62)
    print(f"  CSRF token (volatile)       : {_peek_csrf_token_for_tests()[:12]}…")
    print(
        f"  Handshake secret (env-pinned): {_peek_handshake_secret_for_tests()[:12]}…"
    )
    print()

    app = build_app()
    client = TestClient(app)

    print("→ Handshake endpoint")
    r = client.get("/api/auth/csrf")
    show(
        "401 when X-Tauri-Handshake missing",
        r.status_code == 401,
        f"status={r.status_code}",
    )

    r = client.get("/api/auth/csrf", headers={"X-Tauri-Handshake": "wrong"})
    show(
        "401 when X-Tauri-Handshake wrong",
        r.status_code == 401,
        f"status={r.status_code}",
    )

    r = client.get(
        "/api/auth/csrf",
        headers={"X-Tauri-Handshake": _peek_handshake_secret_for_tests()},
    )
    show(
        "200 + csrf_token when handshake correct",
        r.status_code == 200 and "csrf_token" in r.json(),
        f"status={r.status_code}",
    )

    # Simulate the CEO test: evil-tracker.com tries to obtain the token.
    r = client.get(
        "/api/auth/csrf",
        headers={"Origin": "http://evil-tracker.com"},
    )
    show(
        "Unauthorized origin (evil-tracker.com) → 401 on handshake",
        r.status_code == 401,
        f"status={r.status_code}",
    )

    print()
    print("→ CSRF middleware gate")
    r = client.post("/api/echo", json={"hello": "world"})
    show(
        "POST without X-CSRF-Token → 403 CSRF_VIOLATION",
        r.status_code == 403
        and r.json().get("error", {}).get("code") == "CSRF_VIOLATION",
        f"status={r.status_code} body={r.json()}",
    )

    r = client.post(
        "/api/echo",
        json={"hello": "world"},
        headers={"X-CSRF-Token": "definitely-wrong"},
    )
    show(
        "POST with wrong X-CSRF-Token → 403 CSRF_VIOLATION",
        r.status_code == 403
        and r.json().get("error", {}).get("code") == "CSRF_VIOLATION",
        f"status={r.status_code}",
    )

    r = client.post(
        "/api/echo",
        json={"hello": "world"},
        headers={"X-CSRF-Token": _peek_csrf_token_for_tests()},
    )
    show(
        "POST with correct X-CSRF-Token → 200 (CSRF passes)",
        r.status_code == 200,
        f"status={r.status_code}",
    )

    print()
    print("→ Method + path exemptions")
    r = client.get("/api/ping")
    show(
        "GET /api/ping requires no CSRF",
        r.status_code == 200,
        f"status={r.status_code}",
    )

    r = client.post("/api/webhooks/clerk", json={"type": "evt"})
    show(
        "POST /api/webhooks/clerk exempt from CSRF",
        r.status_code == 200,
        f"status={r.status_code}",
    )

    print()
    print("✓ All W3.3 CSRF + handshake assertions passed.")


if __name__ == "__main__":
    main()
