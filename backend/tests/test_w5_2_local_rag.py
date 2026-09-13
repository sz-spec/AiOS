"""
W5.2 — local Chroma RAG verification (100% offline).

Forces the deterministic embedder backend and a tmp Chroma path so
the test is fast, reproducible, and free of any ML library dependency.
Installs a network kill-switch so any accidental HTTP egress (Convex,
HuggingFace Hub, Ollama, …) surfaces as a failure rather than a
silent slow path.

Flow:
  1. Set $VOS3_LOCALITY_PREFERENCE=local-first
     Set $VOS3_LOCAL_CHROMA_PATH=<tmp>
     Set $VOS3_EMBEDDING_BACKEND=deterministic
  2. Install network kill-switch (socket.connect → raise).
  3. Through the public factory get_memory_repository(), insert 3
     developer-log memories into project "vos3-w52".
  4. Query "relational database" and assert the SQLite-setup memory
     ranks #1 with a strictly higher score than the others.
  5. Confirm the persistent Chroma collection exists on disk.
  6. Verify zero outbound network attempts.

Run:
  python3 backend/tests/test_w5_2_local_rag.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import pathlib
import shutil

# Pin env BEFORE any backend import so module-scope state stabilizes
# to the expected values on first access.
_TMP_CHROMA = tempfile.mkdtemp(prefix="vos3-w52-chroma-")
os.environ["VOS3_LOCALITY_PREFERENCE"] = "local-first"
os.environ["VOS3_LOCAL_CHROMA_PATH"] = _TMP_CHROMA
os.environ["VOS3_EMBEDDING_BACKEND"] = "deterministic"
os.environ["VOS_PROFILE"] = "community"
os.environ.setdefault("ENVIRONMENT", "development")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Network kill-switch
# ---------------------------------------------------------------------------


_network_violations: list[str] = []


import pytest  # noqa: E402


def _make_guarded_socket(real_socket):
    """Build a socket subclass whose every outbound TCP connect raises.

    SQLite + Chroma + the deterministic embedder are all on-disk; any
    socket attempt during this run is a regression that landed on a
    cloud code path.
    """

    class GuardedSocket(real_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args, **kwargs):
            _network_violations.append(f"socket.connect({args!r}, {kwargs!r})")
            raise RuntimeError(
                "W5.2 test invariant violated: outbound socket attempted"
            )

    return GuardedSocket


@pytest.fixture(autouse=True)
def _network_guard():
    """Air-gap socket kill-switch — scoped to each W5.2 test.

    Previously the guard was installed at MODULE IMPORT time and never
    restored. Under pytest-xdist EVERY worker imports this module during
    collection, so ``socket.socket`` stayed globally replaced on every
    worker for the whole session — poisoning unrelated tests (the Ollama
    cloud-probe → cloud fallback, the p2p loopback e2e suite) with spurious
    ``connect()`` failures and deadlocks. Scoping the guard to each test
    (install on setup, restore on teardown) keeps the air-gap invariant
    fully intact — every test still runs under the kill-switch and
    ``test_no_network_attempted`` still asserts zero violations — while
    eliminating the cross-test/cross-worker leak.
    """
    import socket as _socket

    real_socket = _socket.socket
    _socket.socket = _make_guarded_socket(real_socket)  # type: ignore[assignment]
    try:
        yield
    finally:
        _socket.socket = real_socket  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _isolate_local_chroma(monkeypatch):
    """Re-pin THIS module's tmp Chroma path / deterministic embedder / community
    profile and rebuild the cached Chroma client + embedder for every test.

    These vars are set at MODULE IMPORT, but the Chroma client and embedder are
    cached singletons: a prior test in the same xdist worker (e.g. the
    governance fortress suite) can leave them pointing elsewhere, so W5.2's
    path-resolution / persistence assertions fail. Re-pinning the env
    (monkeypatch auto-reverts) and resetting via the provided
    ``vector_setup._reset_chroma_for_tests`` / ``_reset_embedder_for_tests``
    hooks restores isolation without weakening anything.
    """
    monkeypatch.setenv("VOS3_LOCAL_CHROMA_PATH", _TMP_CHROMA)
    monkeypatch.setenv("VOS3_LOCALITY_PREFERENCE", "local-first")
    monkeypatch.setenv("VOS3_EMBEDDING_BACKEND", "deterministic")
    monkeypatch.setenv("VOS_PROFILE", "community")
    try:
        from core.database import vector_setup

        vector_setup._reset_chroma_for_tests()
        vector_setup._reset_embedder_for_tests()
        yield
        vector_setup._reset_chroma_for_tests()
        vector_setup._reset_embedder_for_tests()
    except Exception:
        yield


# ---------------------------------------------------------------------------
# Imports — must come AFTER env pinning + kill-switch installation
# ---------------------------------------------------------------------------


from core.repositories import (  # noqa: E402
    get_memory_repository,
    LocalChromaMemoryRepository,
)
from core.database.vector_setup import (  # noqa: E402
    get_chroma_client,
    get_embedder,
    resolve_chroma_path,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


PROJECT_ID = "vos3-w52"

DEMO_MEMORIES = [
    {
        "text": "Wrote memory setup on SQLite",
        "metadata": {"type": "dev-log", "label": "sqlite-setup", "phase": "W5.1"},
    },
    {
        "text": "Secured API from IDOR",
        "metadata": {"type": "dev-log", "label": "idor-fuzz", "phase": "W3.1"},
    },
    {
        "text": "Added confetti to billing UI",
        "metadata": {"type": "dev-log", "label": "billing-ui", "phase": "design"},
    },
]


def _show(label: str, ok: bool, detail: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {label}{('  → ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_factory_dispatch() -> None:
    print("→ Factory dispatch")
    repo = get_memory_repository()
    _show(
        "get_memory_repository() → LocalChromaMemoryRepository under local-first",
        isinstance(repo, LocalChromaMemoryRepository),
        f"got {type(repo).__name__}",
    )


def test_chroma_path_resolution() -> None:
    print("→ Chroma path resolution")
    resolved = str(resolve_chroma_path())
    expected = os.path.realpath(_TMP_CHROMA)
    _show(
        "VOS3_LOCAL_CHROMA_PATH override honored",
        os.path.realpath(resolved) == expected,
        f"resolved={resolved}",
    )


def test_embedder_pinned_to_deterministic() -> None:
    print("→ Embedder selection")
    embedder = get_embedder()
    _show(
        "VOS3_EMBEDDING_BACKEND=deterministic honored",
        getattr(embedder, "backend", "") == "deterministic",
        f"backend={getattr(embedder, 'backend', None)}",
    )

    # Sanity — dimension is 384 (matches all-MiniLM-L6-v2).
    vec = embedder.embed("relational database")
    _show(
        "embedder output dim == 384 (matches sentence-transformers)",
        len(vec) == 384,
        f"len={len(vec)}",
    )


def test_chroma_client_is_persistent() -> None:
    print("→ Chroma client")
    client = get_chroma_client()
    _show(
        "PersistentClient initialized",
        client is not None,
        f"type={type(client).__name__ if client else None}",
    )


def test_add_and_query_memories() -> None:
    print("→ Add 3 memories + semantic query")
    repo = get_memory_repository()

    # Idempotency: reset the project collection so a re-run from the
    # same tmp dir gives a deterministic answer.
    repo.reset_project(PROJECT_ID)

    ids: list[str] = []
    for m in DEMO_MEMORIES:
        new_id = repo.add_memory(PROJECT_ID, m["text"], m["metadata"])
        ids.append(new_id)
    _show(
        "add_memory returned 3 distinct ids",
        len(ids) == 3 and len(set(ids)) == 3,
        f"ids={[i[:8] + '…' for i in ids]}",
    )

    # The headline assertion: query "relational database" → SQLite wins.
    results = repo.query_memory(PROJECT_ID, "relational database", limit=5)
    _show(
        "query_memory returned >= 3 results",
        len(results) >= 3,
        f"count={len(results)}",
    )

    print("    Ranked results:")
    for i, r in enumerate(results):
        print(
            f"      #{i + 1}  score={r['score']:.4f}  "
            f"text={r['text']!r}  "
            f"label={r['metadata'].get('label')}"
        )

    top = results[0]
    _show(
        "SQLite-setup memory ranks #1 for 'relational database'",
        top["metadata"].get("label") == "sqlite-setup",
        f"top.label={top['metadata'].get('label')}",
    )

    # The score for #1 must be strictly higher than the others.
    if len(results) >= 2:
        _show(
            "#1 score strictly > #2 score",
            results[0]["score"] > results[1]["score"],
            f"top={results[0]['score']:.4f}, runner-up={results[1]['score']:.4f}",
        )


def test_chroma_persisted_to_disk() -> None:
    print("→ On-disk persistence")
    root = pathlib.Path(_TMP_CHROMA)
    _show(
        f"chroma dir exists at {root}",
        root.is_dir(),
    )
    # The persistent client lays down a SQLite-backed chroma.sqlite3
    # plus optional collection subdirs. Either is enough proof of
    # on-disk persistence.
    contents = list(root.iterdir())
    _show(
        "chroma dir is non-empty",
        len(contents) > 0,
        f"entries={[c.name for c in contents]}",
    )


def test_no_network_attempted() -> None:
    print("→ Network isolation")
    _show(
        "no outbound socket attempted during the W5.2 flow",
        len(_network_violations) == 0,
        f"violations={_network_violations[:3]}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 62)
    print("W5.2 — local Chroma RAG verification (100% offline)")
    print("=" * 62)
    print(f"  Chroma path             : {_TMP_CHROMA}")
    print(f"  VOS3_LOCALITY_PREFERENCE: {os.environ['VOS3_LOCALITY_PREFERENCE']}")
    print(f"  VOS3_EMBEDDING_BACKEND  : {os.environ['VOS3_EMBEDDING_BACKEND']}")
    print()

    test_factory_dispatch()
    print()
    test_chroma_path_resolution()
    print()
    test_embedder_pinned_to_deterministic()
    print()
    test_chroma_client_is_persistent()
    print()
    test_add_and_query_memories()
    print()
    test_chroma_persisted_to_disk()
    print()
    test_no_network_attempted()

    print()
    print("✓ All W5.2 local-RAG assertions passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Cleanup the tmp Chroma dir; leave it on failure so a dev can
        # inspect the persisted store.
        try:
            shutil.rmtree(_TMP_CHROMA, ignore_errors=True)
        except OSError:
            pass
