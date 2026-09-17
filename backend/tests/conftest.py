"""
Pytest Configuration and Shared Fixtures
========================================
Common fixtures and configuration for all tests.

Optimized for pytest-xdist parallel execution:
  - Session-scoped fixtures load expensive resources ONCE per worker
  - Each worker has its own in-memory namespace (no cross-worker races)
  - Function-scoped autouse fixtures keep per-test isolation cheap
"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Allocate disposable, process-local storage before application imports create
# keyring/database singletons. Never let tests rotate a developer's real keys.
import tempfile

_TEST_STORAGE = tempfile.TemporaryDirectory(prefix="vos-backend-tests-")
os.environ["VOS3_KEYRING_MODE"] = "local"
os.environ["VOS3_KEYRING_PATH"] = str(Path(_TEST_STORAGE.name) / "secrets.enc")
os.environ["VOS3_KEYRING_SEED_OVERRIDE"] = "disposable-test-keyring-only"
os.environ["VOS3_LOCAL_DB_PATH"] = str(Path(_TEST_STORAGE.name) / "vos.db")
os.environ["VOS3_APP_DATA_DIR"] = str(Path(_TEST_STORAGE.name) / "app-data")

# Set test environment before any test imports main (which creates the app).
# VOS3_ALLOW_DEV_MODE: Auth middleware skips JWT verification in dev mode.
os.environ.setdefault("VOS3_ALLOW_DEV_MODE", "true")

# Stage 12 deep-triage fix (CWE-confused-deputy in test isolation):
#   The root .env contains VOS_API_SECRET=change_me; backend/app.py calls
#   load_dotenv() at import time, populating the env var into the process.
#   The api_key_middleware in app.py then returns 403 ("Forbidden") for
#   every request without a matching x-api-key header — correct production
#   behaviour, but blocks every contract / OWASP / negative test that
#   probes endpoints unauthenticated.
#
#   We clear it here BEFORE main is imported, with an explicit assignment
#   (not setdefault) so we override whatever .env put there. The api_key
#   middleware's "if not api_secret: bypass" check then fires correctly,
#   and tests can probe schema validation / route surface without first
#   having to mint an API secret.
#
#   Production code is unaffected — production never imports conftest.
os.environ["VOS_API_SECRET"] = ""

# Deterministic offline ML policy. Several suites construct
# SentenceTransformer / HuggingFace embedders (e.g. test_hitl_memory,
# dev_memory); by default these issue a HEAD request to huggingface.co to
# check the cached snapshot's freshness. On a developer/CI host that real
# network call is slow and — under pytest-xdist + the thread-method timeout —
# can leave an xdist worker "not properly terminated" (hard crash) when the
# 90s timeout fires mid C-level socket call. Pin the HF stack to its local
# cache so no network is attempted. (Previously this was masked by the W5/W6
# air-gap socket guards, which leaked globally and blocked ALL network; with
# those guards correctly scoped, we need an explicit offline pin instead.)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


# =============================================================================
# Deterministic suite-wide network policy (loopback-only)
# =============================================================================
#
# Legacy air-gap / script test files (W5.*, W6.*) used to install an
# outbound-socket kill-switch at MODULE IMPORT and never restore it. Under
# pytest-xdist that leaked ``socket.socket`` globally and — by accident — kept
# the WHOLE suite air-gapped (diag3: 9212 passed, stable, no crashes). Once
# those guards were correctly scoped per-test, real external connects
# (HuggingFace, Stripe, Convex, …) started happening; under
# ``--timeout-method=thread`` a hanging C-level ``connect()`` can leave an xdist
# worker "not properly terminated" (hard crash) and corrupt the whole run.
#
# Replace that accident with ONE intentional, uniform policy: block all
# NON-loopback TCP connects process-wide — a fast, clean ``OSError`` instead of
# a network hang — while permitting loopback (the p2p e2e suite binds
# 127.0.0.1:0) and AF_UNIX. The test process is ephemeral, so this never needs
# tearing down; the per-test W5/W6 and airgap/ guards (also loopback-permitting)
# simply nest on top, which is a harmless double-check. This does NOT relax any
# security assertion — it only makes the pre-existing "tests must not phone the
# internet" expectation explicit and deterministic.
import socket as _socket_module  # noqa: E402

_VOS_LOOPBACK_OK = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", ""})


def _vos_host_is_loopback(address) -> bool:
    # AF_UNIX (str/bytes path) and unusual address forms: permit.
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if not isinstance(host, str):
        return True
    return host in _VOS_LOOPBACK_OK or host.startswith("127.")


class _VOSLoopbackOnlySocket(_socket_module.socket):
    """socket.socket subclass that fast-fails non-loopback connects."""

    _vos_test_guard = True

    def connect(self, address, *args, **kwargs):
        if _vos_host_is_loopback(address):
            return super().connect(address, *args, **kwargs)
        raise OSError(
            "test network policy (loopback-only): external connect blocked: "
            f"{address!r}"
        )

    def connect_ex(self, address, *args, **kwargs):
        # connect_ex must RETURN an error code, never raise — non-blocking
        # client code depends on that contract. Report connection-refused for
        # external destinations without attempting the (hangable) connect.
        if _vos_host_is_loopback(address):
            return super().connect_ex(address, *args, **kwargs)
        import errno as _errno

        return _errno.ECONNREFUSED


# Idempotent: don't double-wrap if a guard is already installed.
if not getattr(_socket_module.socket, "_vos_test_guard", False):
    _socket_module.socket = _VOSLoopbackOnlySocket


# v20.1.2: Descoped SaaS-layer tests were removed in commit 51… (see
# git log for the deletion). No skip list needed — zero skips in the repo.


# =============================================================================
# Sprint 14.2 (Gap 3) — silicon-marker CLI gate
# =============================================================================
#
# The @pytest.mark.silicon marker tags tests that need REAL hardware
# (Intel TDX, TPM 2.0, NVIDIA NPU, 48-hour wall clock). They are SKIPPED
# by default so the everyday `pytest tests/` run on a developer laptop
# is not 47 tests longer-with-skip-noise. The silicon CI runner
# (.github/workflows/silicon_battery.yml) passes --silicon and they
# all execute.


def pytest_addoption(parser):
    parser.addoption(
        "--silicon",
        action="store_true",
        default=False,
        help="Run silicon-required tests (TDX / TPM / NPU / 48h soak). "
        "Without this flag, @pytest.mark.silicon tests are SKIPPED.",
    )


# =============================================================================
# Session-Scoped Fixtures — loaded ONCE per xdist worker
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def _isolate_dev_memory(tmp_path_factory):
    """Give each xdist worker its own DevMemory directory.

    Without this, multiple workers share ``data/memory/memories.json``
    and corrupt the file with concurrent writes.
    """
    worker_dir = str(tmp_path_factory.mktemp("dev_memory"))
    try:
        from memory import dev_memory as _dm

        _orig_init = _dm.DevMemory.__init__

        def _patched_init(self, persist_dir=None, **kw):
            _orig_init(self, persist_dir=persist_dir if persist_dir is not None else worker_dir, **kw)

        _dm.DevMemory.__init__ = _patched_init
        yield
        _dm.DevMemory.__init__ = _orig_init
    except ImportError:
        yield


@pytest.fixture(scope="session")
def _app():
    """Import the FastAPI app once per worker process."""
    from main import app

    return app


@pytest.fixture(scope="session")
def session_client(_app):
    """
    Session-scoped TestClient.

    Creating TestClient (and importing main.app) is the single most
    expensive setup operation — it imports every route module, middleware,
    and service factory.  Session scope means ONE import per xdist worker
    instead of once-per-test.

    Auto-injects the W3.3 CSRF token (`X-CSRF-Token`) as a default
    header on every request so mutating POST/PUT/PATCH/DELETE calls on
    `/api/*` aren't rejected by `middleware.csrf.csrf_middleware`. Tests
    that specifically need to verify the CSRF gate (e.g. ``test_w3_3_csrf.py``)
    create their own local TestClient and are unaffected. Tests can
    override per-request with ``client.post(..., headers={'X-CSRF-Token': '...'})``.
    """
    from fastapi.testclient import TestClient
    from middleware.csrf import _peek_csrf_token_for_tests

    return TestClient(
        _app,
        base_url="http://localhost",
        headers={"X-CSRF-Token": _peek_csrf_token_for_tests()},
    )


@pytest.fixture(scope="session")
def auth_manifest_cache():
    """
    Cache the auth injection manifest for the entire session.

    load_manifest() reads and parses JSON from disk.  Caching it
    once per worker avoids 30+ redundant disk reads.
    """
    try:
        from ai.agents.auth_injector import load_manifest

        return load_manifest()
    except ImportError:
        return {}


@pytest.fixture(scope="session")
def auth_template_cache():
    """
    Cache template files for all auth strategies.

    load_template_files() reads 7-8 files from disk per strategy.
    """
    try:
        from ai.agents.auth_injector import load_template_files

        return {
            "custom_jwt": load_template_files("custom_jwt"),
            "clerk": load_template_files("clerk"),
            "nextauth": load_template_files("nextauth"),
        }
    except ImportError:
        return {}


# =============================================================================
# Function-Scoped Client — per-test isolation with session-scoped transport
# =============================================================================


@pytest.fixture
def client(session_client):
    """
    Per-test client alias.

    Reuses the session-scoped TestClient instance.  Tests that mutate
    app state are isolated by the reset_services fixture below, NOT
    by recreating the client.
    """
    return session_client


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_llm():
    """
    Create a mock LLM that returns configurable responses.

    Usage:
        def test_something(mock_llm):
            mock_llm.invoke.return_value = MagicMock(content="expected")
    """
    mock = MagicMock()
    mock.invoke.return_value = MagicMock(content="Mock response")
    return mock


@pytest.fixture
def mock_openai():
    """Mock OpenAI ChatOpenAI class."""
    with pytest.MonkeyPatch().context() as m:
        mock_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.invoke.return_value = MagicMock(content="OpenAI response")
        mock_class.return_value = mock_instance

        m.setattr("langchain_openai.ChatOpenAI", mock_class)
        yield mock_instance


@pytest.fixture
def mock_anthropic():
    """Mock Anthropic ChatAnthropic class."""
    with pytest.MonkeyPatch().context() as m:
        mock_class = MagicMock()
        mock_instance = MagicMock()
        mock_instance.invoke.return_value = MagicMock(content="Anthropic response")
        mock_class.return_value = mock_instance

        m.setattr("langchain_anthropic.ChatAnthropic", mock_class)
        yield mock_instance


# =============================================================================
# State Fixtures
# =============================================================================


@pytest.fixture
def base_agent_state():
    """Base state for agent tests."""
    return {
        "messages": [],
        "current_stage": "initial",
        "status": "pending",
        "retry_count": 0,
        "error_message": "",
        "output": "",
    }


@pytest.fixture
def research_state(base_agent_state):
    """State for research workflow tests."""
    base_agent_state.update(
        {
            "research_query": "Test research query",
            "search_queries": [],
            "search_results": [],
            "report": "",
            "summary": "",
            "backoff_seconds": 1,
            "max_retries": 3,
            "last_attempt": False,
            "error_type": "",
            "started_at": "",
            "completed_at": "",
            "total_duration": 0.0,
        }
    )
    return base_agent_state


@pytest.fixture
def router_state():
    """State for router agent tests."""
    return {"user_query": "Test query", "answer": ""}


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def test_config():
    """Test configuration with in-memory/mock settings."""
    try:
        from ai.agents.research_workflow import WorkflowConfig

        return WorkflowConfig(
            llm_model="gpt-4o",
            temperature=0.7,
            max_retries=3,
            initial_backoff=1,
            max_backoff=60,
            checkpoint_db=":memory:",
            enable_persistence=False,
        )
    except ImportError:
        pytest.skip("AI frameworks not installed")


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "owasp: marks tests as OWASP API security tests")
    config.addinivalue_line(
        "markers",
        "noauth: opt OUT of the Sprint-23 default API-auth fixture (test "
        "exercises unauthenticated / permission-denied behaviour itself)",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location AND skip silicon tests unless
    --silicon was passed (Sprint 14.2 Gap 3)."""
    silicon_opted_in = config.getoption("--silicon", default=False)
    skip_silicon = pytest.mark.skip(
        reason="silicon-required test; pass --silicon (see "
        "docs/silicon_ci.md) to run."
    )
    for item in items:
        # Sprint 14.2 — skip @pytest.mark.silicon unless explicitly opted in.
        # Use get_closest_marker (not keyword check) because item.keywords
        # also contains path-component words like "silicon" from the
        # tests/silicon/ directory name, which would over-match.
        if not silicon_opted_in and item.get_closest_marker("silicon") is not None:
            item.add_marker(skip_silicon)
        # Mark tests in test_*_integration.py as integration tests
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _neutralize_offline_auth_leak(request, monkeypatch):
    """Sprint-23 QA: several offline/air-gap test modules set
    ``VOS3_OFFLINE_AUTH=true`` / ``VOS_PROFILE=community`` at IMPORT time
    (module-level ``os.environ[...] = ...``). Because pytest imports every
    test module during collection, that env leaks globally for the whole
    session, and ``reset_environment`` then snapshots the already-polluted
    env — flipping auth into offline mode so unrelated tests get 401 instead
    of their expected 422/200 (the cross-test contamination that broke ~77
    contract/validation tests).

    This forces the default online/dev auth env for every test that is NOT
    itself an offline/air-gap test; those (which need the vars) are excluded
    by path and keep their behaviour. monkeypatch auto-reverts at teardown.
    """
    path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    is_offline_test = (
        "/airgap/" in path
        or "/test_w5_" in path
        or "/test_w6_" in path
        or "test_profile_dispatch" in path
        or "test_open_core_split" in path
    )
    if not is_offline_test:
        monkeypatch.delenv("VOS3_OFFLINE_AUTH", raising=False)
        monkeypatch.delenv("VOS_PROFILE", raising=False)
    yield


@pytest.fixture(autouse=True)
def _neutralize_convex_env_leak(request, monkeypatch):
    """Sprint-23 QA: the root ``.env`` ships placeholder Convex creds
    (``CONVEX_URL=https://your-deployment.convex.cloud`` / ``CONVEX_DEPLOY_KEY=prod:...``).
    ``backend/app.py`` calls ``load_dotenv()`` at import, so those leak into the
    pytest process. ``ConvexDB.__init__`` resolves ``url = (url or os.getenv(
    "CONVEX_URL", ""))`` — meaning ``ConvexDB(url="", deploy_key="")`` does NOT
    take the empty value, it falls through to the leaked placeholder. The
    storage-backend tests then get ``dev_mode=False`` against an unreachable
    placeholder host, so the dev-mode assertions fail and the CRUD calls hit
    the real HTTP path → trip the global ``convex`` circuit breaker → the whole
    file cascades (18 failures).

    Scope: ``test_storage_backend`` only — the file whose contract is "empty
    args ⇒ dev mode". Clearing the leaked vars makes ``url=""`` genuinely mean
    "no URL configured". Tests that pass an explicit URL are unaffected.
    monkeypatch auto-reverts at teardown.
    """
    path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    convex_devmode_files = (
        "/test_storage_backend",
        "/test_convex_routing",
        "/test_api_billing",
        "/test_db_integrity",
        "/test_full_stack_integration",
    )
    if any(f in path for f in convex_devmode_files):
        monkeypatch.delenv("CONVEX_URL", raising=False)
        monkeypatch.delenv("CONVEX_DEPLOY_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _neutralize_stripe_env_leak(request, monkeypatch):
    """The root ``.env`` ships placeholder Stripe creds
    (``STRIPE_SECRET_KEY=sk_test_...``); ``load_dotenv()`` at import leaks them
    into the pytest process. ``StripeConnectService.__init__`` reads
    ``os.environ.get("STRIPE_SECRET_KEY")`` and flips ``available`` to True —
    so ``TestStripeConnectDevMode`` (whose entire contract is "no key ⇒ dev
    mode ⇒ return *_dev_mock payloads") sees a "configured" service and the
    five dev-mode assertions fail (and the mutating calls then attempt real
    api.stripe.com).

    Scope: ``test_stripe_connect`` only — the file whose contract is explicitly
    "no STRIPE_SECRET_KEY". Tests that need a key set it themselves.
    monkeypatch auto-reverts at teardown.
    """
    path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    if "/test_stripe_connect" in path:
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _default_api_auth(request, reset_services, monkeypatch):
    """Sprint-23 QA: the broad ``test_api_*`` suites use a bare
    ``TestClient(app)`` that does neither the Tauri CSRF handshake nor auth,
    so mutating endpoints (POST/PUT/PATCH/DELETE) return 403 (CSRF firewall)
    and permission-gated ones return 403/401. Make those tests behave like a
    legitimate authenticated client:

      1. Bypass the W3.3 CSRF firewall (these tests can't do the handshake).
      2. Authenticate as an admin user (so permission gates pass).

    SCOPED to ``test_api_*`` files ONLY — the dedicated CSRF / auth / IDOR /
    negative / owasp tests keep FULL enforcement (e.g. test_w3_3_csrf.py,
    test_middleware_auth.py, owasp/*). Opt out per-test with
    ``@pytest.mark.noauth``. Depends on ``reset_services`` so it runs AFTER
    that fixture clears ``dependency_overrides``; ``monkeypatch`` auto-reverts
    the CSRF bypass at teardown.
    """
    node_path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    if "/test_api_" not in node_path or request.node.get_closest_marker("noauth"):
        yield
        return
    # 1. CSRF bypass (scoped). The middleware resolves _is_exempt by module
    #    global at call time, so monkeypatching it disables the gate for THIS
    #    test only.
    try:
        monkeypatch.setattr("middleware.csrf._is_exempt", lambda path: True)
    except Exception:
        pass
    # 2. Admin auth override on the canonical dependency object.
    try:
        from main import app as _app
        from api.deps import get_current_user
        from middleware.auth import AuthenticatedUser
    except Exception:
        yield
        return
    _app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="qa_admin_stub",
        email="qa-admin@test.com",
        org_id="org_test",
        org_role="admin",
        permissions=["read", "write", "admin:full"],
        metadata={"qa_stub": True},
    )
    yield
    _app.dependency_overrides.pop(get_current_user, None)


try:
    from langchain_core.embeddings import Embeddings as _LCEmbeddings
except Exception:  # pragma: no cover - langchain_core always present in CI

    class _LCEmbeddings:  # minimal duck-typed base
        pass


class _DeterministicFakeEmbeddings(_LCEmbeddings):
    """Offline stand-in for ``langchain_ollama.OllamaEmbeddings``.

    The real provider POSTs to a local Ollama daemon (``/api/embed``); on a
    CI/dev host with no Ollama running every embed call raises
    ``ConnectionError``. This fake produces stable, content-derived vectors
    (blake2b → floats) so the FAISS/Chroma vector stores index and query
    deterministically without a network round-trip. Subclasses LangChain's
    ``Embeddings`` so vector stores take the ``embed_query`` path (not the
    deprecated "callable" path).
    """

    _DIM = 64

    def __init__(self, *args, **kwargs):  # accept model=, base_url=, etc.
        self.model = kwargs.get("model", "fake-embed")

    def _vector(self, text: str):
        import hashlib

        out = []
        counter = 0
        while len(out) < self._DIM:
            h = hashlib.blake2b(
                f"{counter}:{text}".encode("utf-8"), digest_size=32
            ).digest()
            for i in range(0, len(h), 4):
                if len(out) >= self._DIM:
                    break
                # map 4 bytes → float in [-1, 1) deterministically
                val = int.from_bytes(h[i : i + 4], "big") / 0xFFFFFFFF
                out.append(val * 2.0 - 1.0)
            counter += 1
        return out

    def embed_query(self, text):
        return self._vector(text or "")

    def embed_documents(self, texts):
        return [self._vector(t or "") for t in texts]


@pytest.fixture(autouse=True)
def _offline_embeddings(request, monkeypatch):
    """Sprint-23 QA: RAG/memory pipelines default to local Ollama embeddings
    (``ai.rag.EmbeddingProvider`` → ``OllamaEmbeddings(model="nomic-embed-text")``).
    With no Ollama daemon on the host, every ``embed_documents``/``embed_query``
    raises ``ConnectionError`` and the full-pipeline RAG tests fail at indexing
    time.

    Patch ``OllamaEmbeddings`` (at its ``ai.rag`` import site) to a
    deterministic offline fake for the RAG/memory test modules only. Tests
    that deliberately exercise the degraded path still set
    ``provider._embeddings = None`` themselves, which this leaves intact.

    Also patch the RAG LLM factory (``ai.rag.get_llm_for_rag``): with no
    Ollama daemon AND no cloud API keys, ``get_llm_for_rag`` builds a real
    cloud chat model (e.g. ``ChatGoogleGenerativeAI``) which raises a pydantic
    ``ValidationError`` for the missing key at construction. The offline fake
    is a real ``GenericFakeChatModel`` (a composable LangChain Runnable) that
    returns canned JSON ``{"datasource": "vectorstore", "score": "yes"}`` —
    which satisfies BOTH the plain ``... | llm | StrOutputParser()`` answer
    chains and the AgenticRAG ``... | llm | JsonOutputParser()`` router/grader
    chains (they only read ``datasource``/``score``). GraphRAG entity
    extraction is regex-based and needs no LLM.

    SCOPED by path so nothing else is affected. monkeypatch auto-reverts.
    """
    path = str(getattr(request.node, "fspath", "")).replace("\\", "/")
    needs_offline_embed = (
        "/test_rag" in path
        or "/test_hitl_memory" in path
        or "/test_memory_scaling" in path
    )
    if needs_offline_embed:
        try:
            monkeypatch.setattr("ai.rag.OllamaEmbeddings", _DeterministicFakeEmbeddings)
        except Exception:
            pass
        try:
            from itertools import cycle
            from langchain_core.messages import AIMessage
            from langchain_core.language_models.fake_chat_models import (
                GenericFakeChatModel,
            )

            _canned = '{"datasource": "vectorstore", "score": "yes"}'

            def _fake_rag_llm(*_a, **_k):
                # fresh cycling iterator per call so repeated invokes never
                # exhaust the message stream
                return GenericFakeChatModel(
                    messages=cycle([AIMessage(content=_canned)])
                )

            monkeypatch.setattr("ai.rag.get_llm_for_rag", _fake_rag_llm)
        except Exception:
            pass
    yield


@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file for testing."""
    file_path = tmp_path / "test_file.txt"
    file_path.write_text("Test content")
    return file_path


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for testing."""
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()
    return test_dir


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment before each test."""
    original_env = os.environ.copy()
    yield
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    """Reset rate limiter state between tests to prevent 429 contamination."""
    yield
    try:
        from middleware.rate_limit import _limiter, _auth_limiter

        _limiter._buckets.clear()
        _auth_limiter._buckets.clear()
    except (ImportError, AttributeError):
        pass
    try:
        from middleware.app_rate_limit import _limiter as _app_limiter

        _app_limiter._buckets.clear()
    except (ImportError, AttributeError):
        pass


# =============================================================================
# Assertion Helpers
# =============================================================================

# =============================================================================
# Multi-User Fixtures — for OWASP BOLA / IDOR / role tests
# =============================================================================


@pytest.fixture
def user_a_client(_app):
    """TestClient authenticated as User A (org_a, read permissions)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    _app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_a", email="a@test.com", org_id="org_a", permissions=["read"]
    )
    from fastapi.testclient import TestClient

    from middleware.csrf import _peek_csrf_token_for_tests

    c = TestClient(
        _app, headers={"X-CSRF-Token": _peek_csrf_token_for_tests()}
    )
    yield c
    _app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def user_b_client(_app):
    """TestClient authenticated as User B (different org)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    _app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user_b", email="b@test.com", org_id="org_b", permissions=["read"]
    )
    from fastapi.testclient import TestClient

    from middleware.csrf import _peek_csrf_token_for_tests

    c = TestClient(
        _app, headers={"X-CSRF-Token": _peek_csrf_token_for_tests()}
    )
    yield c
    _app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def admin_client(_app):
    """TestClient authenticated as admin user (full permissions)."""
    from middleware.auth import get_current_user, AuthenticatedUser

    _app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="admin_user",
        email="admin@test.com",
        org_id="org_a",
        permissions=["read", "write", "admin:full"],
    )
    from fastapi.testclient import TestClient

    from middleware.csrf import _peek_csrf_token_for_tests

    c = TestClient(
        _app, headers={"X-CSRF-Token": _peek_csrf_token_for_tests()}
    )
    yield c
    _app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def unauthenticated_client(_app):
    """TestClient with NO auth override — raw unauthenticated requests."""
    _app.dependency_overrides.pop(
        __import__("middleware.auth", fromlist=["get_current_user"]).get_current_user,
        None,
    )
    from fastapi.testclient import TestClient

    return TestClient(_app)


# =============================================================================
# Assertion Helpers
# =============================================================================


@pytest.fixture
def assert_valid_state():
    """Helper to validate state structure."""

    def _assert(state, required_keys):
        for key in required_keys:
            assert key in state, f"Missing required key: {key}"

    return _assert


@pytest.fixture
def assert_workflow_success():
    """Helper to assert workflow completed successfully."""

    def _assert(result):
        assert (
            result.get("status") == "success"
        ), f"Workflow failed: {result.get('error_message', 'Unknown error')}"
        assert result.get("current_stage") == "complete"

    return _assert


# =============================================================================
# Service Reset (per-test isolation for xdist workers)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_services():
    """Reset all singleton services to None before AND after each test."""
    from main import app as _app

    # Clear dependency overrides leaked by prior tests
    _app.dependency_overrides.clear()

    _modules = {}
    _names = [
        "services.project_service",
        "services.checkpoint_service",
        "services.deploy_service",
        "services.marketplace_service",
        "services.collab_service",
        "services.env_manager",
        "services.figma_service",
    ]
    for mod_name in _names:
        try:
            mod = __import__(mod_name, fromlist=["_service"])
            _modules[mod_name] = mod
            mod._service = None
        except ImportError:
            pass

    # Reset Convex singletons so dev-mode in-memory data doesn't leak
    try:
        import core.repositories.convex as _convex_repo

        _convex_repo._convex = None
    except ImportError:
        pass
    try:
        import db.convex as _db_convex

        _db_convex._convex_db = None
    except ImportError:
        pass

    # Reset DevMemory singleton and redirect to temp dir for test isolation
    try:
        from memory import dev_memory as _dm

        _modules["memory.dev_memory"] = _dm
        if _dm._memory_instance is not None:
            _dm._memory_instance = None
    except ImportError:
        pass

    # Restore DEV_MODE to True (default for test suite) in case a test set it to False
    import middleware.auth as _auth_mod

    _auth_mod.DEV_MODE = True

    yield

    # Cleanup after test
    _app.dependency_overrides.clear()
    _auth_mod.DEV_MODE = True
    for mod_name, mod in _modules.items():
        if mod_name == "memory.dev_memory":
            mod._memory_instance = None
        else:
            mod._service = None
