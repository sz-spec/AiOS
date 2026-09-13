"""
backend/tests/airgap/conftest.py — pytest fixtures for the air-gap suite.

P3.1 — wraps the harness utilities in `tests/airgap_harness.py` as
pytest fixtures so test files stay focused on assertions.

Layout choice:
  * The parent `tests/conftest.py` has session-scoped autouse fixtures
    that import the full FastAPI app (`from main import app`). That
    chain trips Python 3.10+ syntax in `memory/dev_memory.py`, which
    crashes anywhere the system Python is 3.9.
  * The air-gap suite uses minimal inline FastAPI apps that DON'T
    touch the full app factory. Putting these tests in a subdirectory
    with their own conftest lets us shadow the parent's broken
    autouse fixtures with no-op overrides — pytest fixture lookup is
    bottom-up, so the child conftest wins.

Fixtures provided:

  network_guard      — session-scoped; installs the loopback-only TCP
                       kill-switch ONCE per worker. Function-scoped
                       wrapper resets the violations list between
                       tests.
  mock_ollama        — function-scoped; spins up a fresh mock daemon
                       on a free loopback port, sets $OLLAMA_BASE_URL,
                       tears down at teardown.
  airgap_env         — function-scoped; pins the full sovereign env
                       contract (VOS3_LOCALITY_PREFERENCE, paths,
                       VOS3_EMBEDDING_BACKEND=deterministic, …) and
                       restores afterwards via the parent's
                       reset_environment fixture.
  airgap_app         — the harness's build_chat_app() — minimal
                       FastAPI app with the W5/W6 routes.
  airgap_client      — TestClient wrapping airgap_app.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# Ensure backend/ is on sys.path so the imports inside the harness module
# work whether pytest is invoked from the repo root or backend/.
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from tests.airgap_harness import (  # noqa: E402
    NetworkGuard,
    bootstrap_sqlite_user,
    build_chat_app,
    get_network_guard,
    make_offline_token,
    reset_global_singletons,
    start_mock_ollama,
)

# ---------------------------------------------------------------------------
# Shadow the parent conftest's autouse fixtures that import `main.app`.
# Pytest fixture lookup is bottom-up; redefining a fixture name in a
# child conftest shadows the parent's for tests under this directory.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_services():
    """No-op — air-gap tests don't touch the full app's services."""
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """No-op — air-gap tests don't exercise rate-limited routes."""
    yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_dev_memory():
    """No-op — air-gap tests don't import DevMemory."""
    yield


# ---------------------------------------------------------------------------
# Air-gap harness fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _session_network_guard() -> NetworkGuard:
    """Install the loopback kill-switch ONCE per worker.

    `socket.socket` is monkey-patched at module level — uninstalling
    mid-suite would race with any test still in flight. Loopback is
    permitted so the mock Ollama server and SQLite/Chroma on-disk
    paths work; everything else is recorded + raised.
    """
    guard = get_network_guard()
    yield guard


@pytest.fixture
def network_guard(_session_network_guard) -> NetworkGuard:
    """Function-scoped wrapper that resets violations between tests."""
    _session_network_guard.reset()
    yield _session_network_guard


@pytest.fixture
def airgap_env(monkeypatch, tmp_path):
    """Pin the full sovereign env contract for the duration of a test.

    Sets:
      VOS3_LOCALITY_PREFERENCE = "local-first"
      VOS_PROFILE              = "community"
      VOS3_LOCAL_DB_PATH       = <tmp>/vos3.db
      VOS3_LOCAL_CHROMA_PATH   = <tmp>/chroma_db
      VOS3_EMBEDDING_BACKEND   = "deterministic"
      VOS3_OFFLINE_AUTH        = "true"
      VOS3_TAURI_IPC_SECRET    = "test-handshake-secret"
      ENVIRONMENT              = "development"

    Clears any cloud creds + Clerk keys so a developer's shell env
    can't leak in.

    Resets all module-level singletons (dispatcher, ollama probe,
    SQLite engine, Chroma client, embedder) before yielding so each
    test starts from a clean state.
    """
    sqlite_path = tmp_path / "vos3.db"
    chroma_dir = tmp_path / "chroma_db"

    pinned = {
        "VOS3_LOCALITY_PREFERENCE": "local-first",
        "VOS_PROFILE": "community",
        "VOS3_LOCAL_DB_PATH": str(sqlite_path),
        "VOS3_LOCAL_CHROMA_PATH": str(chroma_dir),
        "VOS3_EMBEDDING_BACKEND": "deterministic",
        "VOS3_OFFLINE_AUTH": "true",
        "VOS3_TAURI_IPC_SECRET": "test-handshake-secret-airgap-harness",
        "ENVIRONMENT": "development",
    }
    for k, v in pinned.items():
        monkeypatch.setenv(k, v)

    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "CLERK_SECRET_KEY",
        "CLERK_PUBLISHABLE_KEY",
        "CLERK_ISSUER_URL",
    ):
        monkeypatch.delenv(k, raising=False)

    reset_global_singletons()

    yield {
        "sqlite_path": sqlite_path,
        "chroma_path": chroma_dir,
        "handshake": pinned["VOS3_TAURI_IPC_SECRET"],
    }

    # Reset again on teardown so the NEXT test doesn't inherit cached
    # singletons keyed to this test's tmp paths.
    reset_global_singletons()


@pytest.fixture
def mock_ollama(airgap_env, monkeypatch):
    """Start a mock Ollama HTTP server on a free loopback port.

    Sets $OLLAMA_BASE_URL to the bound URL so the resolver +
    healthcheck pick it up. Tears down at end-of-test.
    """
    server = start_mock_ollama()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("OLLAMA_BASE_URL", base_url)
    # Re-reset singletons so the ollama probe re-evaluates against
    # the new URL (airgap_env reset them before the env var was set).
    reset_global_singletons()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def airgap_app(airgap_env):
    """The harness's minimal FastAPI app — all W5/W6 chat routes."""
    return build_chat_app()


@pytest.fixture
def airgap_client(airgap_app):
    """TestClient against the air-gap chat app."""
    from fastapi.testclient import TestClient

    return TestClient(airgap_app)


@pytest.fixture
def bootstrap_user(airgap_env):
    """Helper factory that provisions a local SQLite user.

    Returns a callable: `bootstrap_user(clerk_id, email)` → local_id.
    """

    def _do(clerk_id: str, email: str = "alice@air.gap") -> str:
        return bootstrap_sqlite_user(clerk_id, email)

    return _do


@pytest.fixture
def offline_token():
    """Factory: `offline_token(clerk_id)` → JWT the W5.3 path accepts."""
    return make_offline_token
