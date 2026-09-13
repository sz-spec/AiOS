"""
backend/tests/airgap_harness.py — shared utilities for P3.1 air-gap tests.

Pure helper module — NOT a pytest conftest. Pytest fixtures that wrap
these utilities live in `tests/airgap/conftest.py`; the helpers are
exposed here so individual test files can import the building blocks
without inheriting the pytest fixture scope.

What this module owns:

  * NetworkGuard         — install / uninstall the loopback-only TCP
                           kill-switch (the same pattern W5.3/W6.1/W6.25
                           used inline). Records every blocked attempt so
                           the suite can assert zero egress.

  * MockOllamaServer     — a ThreadingHTTPServer that responds to
                           /api/tags + /api/chat with Ollama-compatible
                           JSON. Records every received call for
                           assertions.

  * make_offline_token   — produces a JWT the W5.3 offline auth path
                           accepts.

  * bootstrap_sqlite_user — sync a user row into the local SQLite store
                            so the offline-auth gate has someone to
                            resolve.

  * reset_global_singletons — drop the module-level state the W6.25
                              dispatcher / W5.x clients cache between
                              tests.

All helpers are deliberately scope-agnostic so the same code can be
used by pytest fixtures, standalone runners, or ad-hoc REPL sessions.
"""

from __future__ import annotations

import json
import socket as _socket_module
import time
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Network kill-switch
# ---------------------------------------------------------------------------


LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


@dataclass
class NetworkGuard:
    """Loopback-only TCP kill-switch.

    Monkey-patches `socket.socket` so any `connect()` to a non-loopback
    address records a violation and raises `IOError`. Loopback is
    whitelisted so TestClient, the mock Ollama server, and SQLite/
    Chroma on-disk work unaffected.

    The guard is installed once per process; calling install() a
    second time is a no-op. Uninstall is intentionally not exposed —
    pytest tests run inside a single process and any path that legally
    needed to reach the network would simply not use the air-gap
    harness.
    """

    violations: list[str] = field(default_factory=list)
    _installed: bool = field(default=False, init=False)
    _real_socket: type = field(default=_socket_module.socket, init=False)

    def install(self) -> None:
        if self._installed:
            return
        violations = self.violations  # closure capture
        real_cls = self._real_socket

        class GuardedSocket(real_cls):  # type: ignore[misc, valid-type]
            def connect(self, address, *args, **kwargs):  # noqa: D401
                host = address[0] if isinstance(address, tuple) and address else None
                if isinstance(host, str) and host in LOOPBACK_HOSTS:
                    return super().connect(address, *args, **kwargs)
                violations.append(f"connect to {address!r}")
                raise IOError(f"air-gap violated: outbound connect to {address!r}")

        _socket_module.socket = GuardedSocket  # type: ignore[assignment]
        self._installed = True

    def reset(self) -> None:
        """Clear the violations list. Useful between pytest tests so
        a violation from test N doesn't bleed into test N+1's assertion."""
        self.violations.clear()


# Module-level singleton — install once per process.
_NETWORK_GUARD = NetworkGuard()


def get_network_guard() -> NetworkGuard:
    """Return the process-wide NetworkGuard, installing on first call."""
    _NETWORK_GUARD.install()
    return _NETWORK_GUARD


# ---------------------------------------------------------------------------
# Mock Ollama HTTP server
# ---------------------------------------------------------------------------


class _MockOllamaHandler(BaseHTTPRequestHandler):
    """Handles GET /api/tags + POST /api/chat (+ /api/generate).

    All received calls are appended to the parent server's `calls`
    list so tests can assert payload shape.
    """

    server: "MockOllamaServer"  # set by ThreadingHTTPServer subclass

    def log_message(self, *_):  # silence per-request stderr
        return

    def do_GET(self):  # noqa: N802
        self.server.calls.append({"method": "GET", "path": self.path})
        if self.path == "/api/tags":
            body = json.dumps(
                {
                    "models": [
                        {"name": f"{m}:latest", "size": 0}
                        for m in self.server.advertised_models
                    ]
                }
            ).encode("utf-8")
            self._write(200, "application/json", body)
            return
        self._write(404, "text/plain", b"not found")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            payload = {}
        self.server.calls.append(
            {
                "method": "POST",
                "path": self.path,
                "payload": payload,
            }
        )
        if self.path in ("/api/chat", "/api/generate"):
            body = json.dumps(
                {
                    "model": payload.get("model", "llama3"),
                    "created_at": "1970-01-01T00:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": self.server.reply_text,
                    },
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 1,
                    "load_duration": 1,
                    "prompt_eval_count": 1,
                    "prompt_eval_duration": 1,
                    "eval_count": 1,
                    "eval_duration": 1,
                }
            ).encode("utf-8")
            self._write(200, "application/x-ndjson", body)
            return
        self._write(404, "text/plain", b"not found")

    def _write(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockOllamaServer(ThreadingHTTPServer):
    """Drop-in mock Ollama daemon bound to a free loopback port.

    Attributes accessed by the handler:
      advertised_models — list of model names returned by /api/tags
      reply_text        — assistant content returned by /api/chat
      calls             — list of every request seen (mutated by handler)
    """

    def __init__(
        self,
        advertised_models: tuple[str, ...] = ("llama3", "llama3.1:8b"),
        reply_text: str = "AIR-GAP-OK: mock Ollama response.",
    ):
        super().__init__(("127.0.0.1", 0), _MockOllamaHandler)
        self.advertised_models = advertised_models
        self.reply_text = reply_text
        self.calls: list[dict] = []
        self._thread: Optional[threading.Thread] = None

    def start(self) -> str:
        """Spawn the serve_forever thread; return the base URL."""
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        port = self.server_address[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def start_mock_ollama(
    advertised_models: tuple[str, ...] = ("llama3", "llama3.1:8b"),
    reply_text: str = "AIR-GAP-OK: mock Ollama response.",
) -> MockOllamaServer:
    """Convenience: start + return a ready-to-use MockOllamaServer."""
    server = MockOllamaServer(
        advertised_models=advertised_models, reply_text=reply_text
    )
    server.start()
    return server


# ---------------------------------------------------------------------------
# Token + user bootstrap helpers (W5.3 offline auth)
# ---------------------------------------------------------------------------


def make_offline_token(clerk_id: str, email: str = "alice@air.gap") -> str:
    """Produce a JWT the W5.3 offline auth path will accept.

    The signature is intentionally ignored by `_verify_token_offline()`,
    so any HS256 token with a valid 'sub' + non-expired 'exp' works.
    """
    import jwt  # PyJWT — pinned in backend deps

    payload = {
        "sub": clerk_id,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, "ignored-by-offline-path", algorithm="HS256")


def bootstrap_sqlite_user(clerk_id: str, email: str = "alice@air.gap") -> str:
    """Sync a user row into the local SQLite store via the W5.1 repo.

    Runs the async repo on a fresh event loop so callers don't need
    to manage one themselves. Returns the new local user ID.
    """
    import asyncio
    from core.repositories.sqlite import get_sqlite_user_sync_repository

    repo = get_sqlite_user_sync_repository()
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            repo.sync_from_clerk(clerk_id=clerk_id, email=email)
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Singleton reset — between-test isolation
# ---------------------------------------------------------------------------


def reset_global_singletons() -> None:
    """Drop the module-level state the W3-W6 layers cache.

    Each layer caches things like the LLM dispatcher choice, the
    Ollama probe result, the SQLite engine, the Chroma client. Pytest
    runs tests in a single process, so without an explicit reset the
    second test sees the first test's choices.
    """
    # W6.25 dispatcher
    try:
        from services.llm_dispatcher import _reset_dispatcher_for_tests

        _reset_dispatcher_for_tests()
    except Exception:
        pass
    # Ollama probe cache
    try:
        from services.ollama_probe import reset_cache as _reset_probe

        _reset_probe()
    except Exception:
        pass
    # local_llm embedder/healthcheck cache (no explicit reset hook;
    # it re-reads env on every call so this is a no-op safety belt).
    # W5.1 SQLite engine
    try:
        from core.database.sqlite_setup import _reset_for_tests

        _reset_for_tests()
    except Exception:
        pass
    # W5.2 Chroma client + embedder
    try:
        from core.database.vector_setup import (
            _reset_chroma_for_tests,
            _reset_embedder_for_tests,
        )

        _reset_chroma_for_tests()
        _reset_embedder_for_tests()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Minimal FastAPI test app builder
# ---------------------------------------------------------------------------


def build_chat_app() -> Any:
    """Build the small FastAPI app the W5.3 / W6.1 / W6.25 tests drive.

    Contains the routes the air-gap suite exercises:
      POST /api/users/sync
      POST /api/chat/sessions
      POST /api/chat/messages
      GET  /api/chat/history/{session_id}
      POST /api/projects/{id}/memories
      GET  /api/projects/{id}/memories/search
      POST /api/chat/completions          (W6.1 / W6.25 dispatcher target)

    Mirrors what the W5.3 inline build_app() function used to do,
    consolidated here so the harness owns one canonical chat-app shape.
    """
    from fastapi import Depends, FastAPI, HTTPException
    from middleware.auth import AuthenticatedUser, get_current_user
    from core.repositories import (
        get_async_user_sync_repository,
        get_async_chat_session_repository,
        get_memory_repository,
    )
    from services.llm_dispatcher import (
        BaseLLMRouter,
        LLMRequestContext,
        get_llm_dispatcher_dep,
    )

    app = FastAPI()

    @app.post("/api/users/sync")
    async def users_sync(
        payload: dict, user: AuthenticatedUser = Depends(get_current_user)
    ):
        clerk_id = payload.get("clerkId") or user.id
        email = payload.get("email") or user.email or ""
        repo = get_async_user_sync_repository()
        local_id = await repo.sync_from_clerk(
            clerk_id=clerk_id,
            email=email,
            full_name=payload.get("fullName"),
            avatar_url=payload.get("avatarUrl"),
            metadata=payload.get("metadata"),
        )
        return {"localUserId": local_id, "clerkId": clerk_id}

    @app.post("/api/chat/sessions")
    async def chat_session_start(
        payload: dict,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        return {"sessionId": payload["sessionId"], "userId": user.id}

    @app.post("/api/chat/messages")
    async def chat_send_message(
        payload: dict,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        repo = get_async_chat_session_repository()
        msg_id = await repo.append_message(
            session_id=payload["sessionId"],
            user_id=user.id,
            role=payload.get("role", "user"),
            content=payload["content"],
            metadata=payload.get("metadata"),
        )
        return {"messageId": msg_id}

    @app.get("/api/chat/history/{session_id}")
    async def chat_history(
        session_id: str,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        repo = get_async_chat_session_repository()
        result = await repo.load(session_id=session_id)
        if result is None:
            raise HTTPException(404, "session not found")
        return result

    @app.post("/api/projects/{project_id}/memories")
    async def memory_add(
        project_id: str,
        payload: dict,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        repo = get_memory_repository()
        mem_id = repo.add_memory(project_id, payload["text"], payload.get("metadata"))
        return {"memoryId": mem_id}

    @app.get("/api/projects/{project_id}/memories/search")
    async def memory_search(
        project_id: str,
        query: str,
        limit: int = 5,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        repo = get_memory_repository()
        return {"results": repo.query_memory(project_id, query, limit=limit)}

    @app.post("/api/chat/completions")
    async def chat_completion(
        payload: dict,
        user: AuthenticatedUser = Depends(get_current_user),
        dispatcher: BaseLLMRouter = Depends(get_llm_dispatcher_dep),
    ):
        ctx = LLMRequestContext(
            role="coding",
            complexity=5,
            user_id=user.id,
            streaming=False,
        )
        resolution = dispatcher.resolve(ctx)
        msgs = payload.get("messages") or [{"role": "user", "content": "hi"}]
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        converted: list = []
        for m in msgs:
            r = m["role"]
            if r == "user":
                converted.append(HumanMessage(content=m["content"]))
            elif r == "assistant":
                converted.append(AIMessage(content=m["content"]))
            else:
                converted.append(SystemMessage(content=m["content"]))
        result = await resolution.llm.ainvoke(converted)
        content = getattr(result, "content", str(result))
        return {
            "model": resolution.model_id,
            "provider": resolution.provider,
            "router": resolution.metadata.get("router"),
            "base_url": resolution.base_url,
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }

    return app


__all__ = [
    "LOOPBACK_HOSTS",
    "NetworkGuard",
    "get_network_guard",
    "MockOllamaServer",
    "start_mock_ollama",
    "make_offline_token",
    "bootstrap_sqlite_user",
    "reset_global_singletons",
    "build_chat_app",
]
