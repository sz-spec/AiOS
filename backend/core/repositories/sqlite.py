"""
backend/core/repositories/sqlite.py — local-first repository implementations.

W5.1 — concrete SQLite-backed repositories that satisfy the SAME
method contracts as the Convex repos:

  Sync (matches service_repos.py — W4.1)
    SQLiteAppInstallationRepository
      install(app_id, organization_id, installed_by, version,
              granted_scopes, config) -> str

  Async (matches async_convex.py — W3.2d)
    SQLiteUserSyncRepository
      sync_from_clerk(clerk_id, email, full_name?, avatar_url?, metadata?) -> str
      soft_delete(clerk_id) -> None
      record_sign_in(clerk_id) -> None
      get_by_clerk_id(clerk_id) -> dict | None

    SQLiteChatSessionRepository
      load(session_id) -> dict | None
        Returns { ..., messages: [...] } — the .load() Convex shape
      remove(session_id) -> None

The async classes wrap sync SQLAlchemy work via `asyncio.to_thread`
rather than pulling in the aiosqlite async driver. Three reasons:

  1. SQLite write paths serialize internally anyway — async only buys
     us non-blocking I/O during reads. The threadpool hop has
     negligible cost on local disk.
  2. Keeps the schema definition in a single sync SQLAlchemy file
     (sqlite_setup.py); no parallel async metadata.
  3. Lets sync and async repos share the same Session factory, so a
     mixed mutation (sync app-installation + async chat-session) hits
     the same SQLite database file with the same connection pool.

JSON-typed Convex fields (`metadata`, `grantedScopes`, `config`) are
stored as serialized JSON strings and reconstructed on read. Schema
columns are intentionally named `metadata_json` etc. in the SQLAlchemy
model to avoid shadowing the `Base.metadata` descriptor, but the
on-disk column is still `metadata` (per the Column(name=...) override).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from sqlalchemy import select

from core.database.sqlite_setup import (
    AppInstallation,
    ChatSession,
    ChatSessionMessage,
    User,
    get_session,
    init_db,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_dump(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, separators=(",", ":"))


def _json_load(s: Optional[str]) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def _ensure_db() -> None:
    """Idempotent schema bootstrap.

    Repos call this on first use so a fresh dev box doesn't need a
    separate boot step. init_db() is itself idempotent (uses
    `create_all` which skips existing tables).
    """
    init_db()


def _enforce_rag_scope(app_id: Optional[str], scope: str) -> None:
    """P4.2 — gate check for app-originated RAG calls.

    No-op when `app_id` is None (first-party vOS request). When set,
    `PERMISSION_GATE.check(app_id, scope)` is consulted; any failure
    surfaces as `fastapi.HTTPException(403)` with a structured
    detail mirroring the LLM dispatcher's enforcement.
    """
    if not app_id:
        return
    # Lazy import — keeps the legacy import graph for callers that
    # don't pass app_id (and therefore don't need the sandbox layer).
    from fastapi import HTTPException as _HTTPException
    from services.app_sandbox import (
        PERMISSION_GATE,
        AppIsolated,
        AppNotFound,
        ScopeViolation,
    )

    try:
        PERMISSION_GATE.check(app_id, scope)
    except ScopeViolation as exc:
        raise _HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise _HTTPException(
            status_code=403,
            detail={
                "error": "app_unauthorized",
                "app_id": app_id,
                "reason": str(exc),
            },
        ) from exc


# ---------------------------------------------------------------------------
# Sync — AppInstallation
# ---------------------------------------------------------------------------


class SQLiteAppInstallationRepository:
    """Mirrors ConvexAppInstallationRepository.install() exactly.

    Drop-in: same kwargs, same return type (a stringified row ID).
    """

    def install(
        self,
        *,
        app_id: str,
        organization_id: str,
        installed_by: str,
        version: str,
        granted_scopes: list[str],
        config: Optional[dict] = None,
    ) -> str:
        _ensure_db()
        row_id = str(uuid.uuid4())
        with get_session() as session:
            row = AppInstallation(
                id=row_id,
                appId=app_id,
                organizationId=organization_id,
                installedBy=installed_by,
                version=version,
                enabled=True,
                grantedScopes=_json_dump(granted_scopes) or "[]",
                config=_json_dump(config),
                installedAt=_now_ms(),
            )
            session.add(row)
            session.commit()
        return row_id


# ---------------------------------------------------------------------------
# Async — UserSync (matches AsyncUserSyncRepository)
# ---------------------------------------------------------------------------


def _sync_from_clerk_sync(
    *,
    clerk_id: str,
    email: str,
    full_name: Optional[str],
    avatar_url: Optional[str],
    metadata: Optional[dict],
) -> str:
    _ensure_db()
    now = _now_ms()
    with get_session() as session:
        existing = session.execute(
            select(User).where(User.clerkId == clerk_id)
        ).scalar_one_or_none()
        if existing is not None:
            existing.email = email
            existing.fullName = full_name
            existing.avatarUrl = avatar_url
            existing.metadata_json = _json_dump(metadata)
            existing.updatedAt = now
            session.commit()
            return existing.id
        row_id = str(uuid.uuid4())
        row = User(
            id=row_id,
            clerkId=clerk_id,
            email=email,
            fullName=full_name,
            avatarUrl=avatar_url,
            lastSignInAt=None,
            metadata_json=_json_dump(metadata),
            createdAt=now,
            updatedAt=now,
        )
        session.add(row)
        session.commit()
        return row_id


def _soft_delete_sync(*, clerk_id: str) -> None:
    _ensure_db()
    with get_session() as session:
        existing = session.execute(
            select(User).where(User.clerkId == clerk_id)
        ).scalar_one_or_none()
        if existing is None:
            return
        # Mirror the Convex softDelete pattern: mark in metadata, don't
        # actually drop the row (so chat history, audit log, etc. still
        # resolve the user reference).
        meta = _json_load(existing.metadata_json) or {}
        meta["deleted"] = True
        meta["deletedAt"] = _now_ms()
        existing.metadata_json = _json_dump(meta)
        existing.updatedAt = _now_ms()
        session.commit()


def _record_sign_in_sync(*, clerk_id: str) -> None:
    _ensure_db()
    with get_session() as session:
        existing = session.execute(
            select(User).where(User.clerkId == clerk_id)
        ).scalar_one_or_none()
        if existing is None:
            return
        existing.lastSignInAt = _now_ms()
        existing.updatedAt = _now_ms()
        session.commit()


def _get_by_clerk_id_sync(*, clerk_id: str) -> Optional[dict]:
    _ensure_db()
    with get_session() as session:
        existing = session.execute(
            select(User).where(User.clerkId == clerk_id)
        ).scalar_one_or_none()
        if existing is None:
            return None
        return {
            "_id": existing.id,
            "clerkId": existing.clerkId,
            "email": existing.email,
            "fullName": existing.fullName,
            "avatarUrl": existing.avatarUrl,
            "lastSignInAt": existing.lastSignInAt,
            "metadata": _json_load(existing.metadata_json),
            "createdAt": existing.createdAt,
            "updatedAt": existing.updatedAt,
        }


class SQLiteUserSyncRepository:
    """Async-native interface that matches AsyncUserSyncRepository."""

    async def sync_from_clerk(
        self,
        *,
        clerk_id: str,
        email: str,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        return await asyncio.to_thread(
            _sync_from_clerk_sync,
            clerk_id=clerk_id,
            email=email,
            full_name=full_name,
            avatar_url=avatar_url,
            metadata=metadata,
        )

    async def soft_delete(self, *, clerk_id: str) -> None:
        await asyncio.to_thread(_soft_delete_sync, clerk_id=clerk_id)

    async def record_sign_in(self, *, clerk_id: str) -> None:
        await asyncio.to_thread(_record_sign_in_sync, clerk_id=clerk_id)

    async def get_by_clerk_id(self, *, clerk_id: str) -> Optional[dict]:
        return await asyncio.to_thread(_get_by_clerk_id_sync, clerk_id=clerk_id)


# ---------------------------------------------------------------------------
# Async — ChatSession (matches AsyncChatSessionRepository)
# ---------------------------------------------------------------------------


def _chat_load_sync(*, session_id: str) -> Optional[dict]:
    _ensure_db()
    with get_session() as session:
        cs = session.execute(
            select(ChatSession).where(ChatSession.sessionId == session_id)
        ).scalar_one_or_none()
        if cs is None:
            return None
        msgs = (
            session.execute(
                select(ChatSessionMessage)
                .where(ChatSessionMessage.sessionId == session_id)
                .order_by(ChatSessionMessage.timestamp.asc())
            )
            .scalars()
            .all()
        )
        return {
            "_id": cs.id,
            "userId": cs.userId,
            "sessionId": cs.sessionId,
            "messageCount": cs.messageCount or len(msgs),
            "createdAt": cs.createdAt,
            "updatedAt": cs.updatedAt,
            "messages": [
                {
                    "_id": m.id,
                    "sessionId": m.sessionId,
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "metadata": _json_load(m.metadata_json),
                }
                for m in msgs
            ],
        }


def _chat_remove_sync(*, session_id: str) -> None:
    _ensure_db()
    with get_session() as session:
        # Drop messages first (no FK enforced — be explicit).
        msgs = (
            session.execute(
                select(ChatSessionMessage).where(
                    ChatSessionMessage.sessionId == session_id
                )
            )
            .scalars()
            .all()
        )
        for m in msgs:
            session.delete(m)
        cs = session.execute(
            select(ChatSession).where(ChatSession.sessionId == session_id)
        ).scalar_one_or_none()
        if cs is not None:
            session.delete(cs)
        session.commit()


def _chat_append_message_sync(
    *,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    metadata: Optional[dict] = None,
) -> str:
    """Append a single message; create the session row on first write.

    Not part of the AsyncChatSessionRepository contract but needed for
    the W5.1 verification test (the Convex side handles this via the
    `chatSessions:save` mutation which we haven't ported yet).

    P3.3 — both the session row and the new message row are written
    with `dirty=True`, so SovereignSyncEngine picks them up on the
    next sweep. An update to an EXISTING session also re-flips its
    dirty flag (Convex has updatedAt + messageCount to re-mirror).

    P4.5 — AFTER the commit lands, publish a `chat.message_sent`
    event so subscribed apps learn about the new message. The
    emit is first-party (origin_app_id=None) — vOS core doesn't
    need a manifest scope to talk to its own bus. Bus failures
    do NOT roll back the SQLite commit; they're logged and the
    repo returns the message id as if nothing happened.
    """
    _ensure_db()
    now = _now_ms()
    msg_id = str(uuid.uuid4())
    with get_session() as session:
        cs = session.execute(
            select(ChatSession).where(ChatSession.sessionId == session_id)
        ).scalar_one_or_none()
        if cs is None:
            cs = ChatSession(
                id=str(uuid.uuid4()),
                userId=user_id,
                sessionId=session_id,
                messageCount=0,
                createdAt=now,
                updatedAt=now,
                dirty=True,
            )
            session.add(cs)
        cs.messageCount = (cs.messageCount or 0) + 1
        cs.updatedAt = now
        cs.dirty = True  # any update re-marks the session for sync
        session.add(
            ChatSessionMessage(
                id=msg_id,
                sessionId=session_id,
                role=role,
                content=content,
                timestamp=now,
                metadata_json=_json_dump(metadata),
                dirty=True,
            )
        )
        session.commit()

    # P4.5 — emit the chat.message_sent event after the commit.
    try:
        from services.event_bus import EVENT_BUS

        EVENT_BUS.publish(
            "chat.message_sent",
            {
                "sessionId": session_id,
                "messageId": msg_id,
                "userId": user_id,
                "role": role,
                "timestamp": now,
            },
        )
    except Exception as exc:  # noqa: BLE001
        # The bus is a non-critical sidecar — a misbehaving
        # subscriber or a disabled bus must not poison chat
        # message persistence.
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "[chat] event-bus publish failed msg_id=%s: %s",
            msg_id,
            exc,
        )

    return msg_id


class SQLiteChatSessionRepository:
    """Async-native interface that matches AsyncChatSessionRepository.

    Adds an `append_message()` helper not present on the Convex side —
    on Convex the full message-list save is done via a separate
    `chatSessions:save` mutation; for local-first we expose a smaller
    incremental write so callers can stream a message in without
    rewriting the whole session row.
    """

    async def load(self, *, session_id: str) -> Optional[dict]:
        return await asyncio.to_thread(_chat_load_sync, session_id=session_id)

    async def remove(self, *, session_id: str) -> None:
        await asyncio.to_thread(_chat_remove_sync, session_id=session_id)

    async def append_message(
        self,
        *,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> str:
        return await asyncio.to_thread(
            _chat_append_message_sync,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# W5.2 — Local Chroma-backed memory repository (project-scoped RAG)
# ---------------------------------------------------------------------------


class LocalChromaMemoryRepository:
    """Per-project semantic memory backed by local ChromaDB + offline embedder.

    Method shapes are intentionally minimal — they're the surface
    requested by W5.2 for the local-first RAG path. The relational
    ProjectMemoryRepository (base.py) still owns structural memory
    (ADRs, decisions, tagged metadata) and is wired separately; this
    class adds semantic recall as a complementary capability.

    Collection layout:
      - One Chroma collection per project, named "vos3_memory_{project_id}".
      - Document ID = uuid4().
      - The query/document text is embedded via the active backend
        (see vector_setup.get_embedder()) — sentence-transformers if
        available, Ollama if configured, deterministic fallback otherwise.
    """

    _COLLECTION_PREFIX = "vos3_memory_"

    def _collection_name(self, project_id: str) -> str:
        # Chroma collection names: ASCII alphanumeric + ._- only, 3-512 chars.
        safe = "".join(c if c.isalnum() else "_" for c in project_id)[:480]
        return f"{self._COLLECTION_PREFIX}{safe}"

    def _client(self):
        from core.database.vector_setup import (
            get_chroma_client,
            chroma_unavailable_reason,
        )

        client = get_chroma_client()
        if client is None:
            reason = chroma_unavailable_reason() or "Chroma unavailable"
            raise RuntimeError(
                f"LocalChromaMemoryRepository unusable: {reason}. "
                f"Install chromadb or run with the cloud profile."
            )
        return client

    def _collection(self, project_id: str):
        return self._client().get_or_create_collection(
            self._collection_name(project_id),
            metadata={"hnsw:space": "cosine"},
        )

    def _embed(self, text: str) -> list[float]:
        from core.database.vector_setup import get_embedder

        return get_embedder().embed(text)

    def add_memory(
        self,
        project_id: str,
        text: str,
        metadata: Optional[dict] = None,
        *,
        app_id: Optional[str] = None,
    ) -> str:
        """Add a single memory entry to the project's Chroma collection.

        Returns the new memory's document ID.

        P4.2 — when called from a sandboxed app (`app_id` set), the
        caller must hold `rag.write`. First-party vOS callers omit
        `app_id` (None) and skip the gate.
        """
        _enforce_rag_scope(app_id, "rag.write")
        if not text or not text.strip():
            raise ValueError("memory text must be non-empty")
        coll = self._collection(project_id)
        doc_id = str(uuid.uuid4())
        # Chroma metadata only accepts str/int/float/bool. Serialize
        # nested values as JSON so the round-trip preserves them.
        flat_meta: dict = {"projectId": project_id}
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)) or v is None:
                    flat_meta[k] = "" if v is None else v
                else:
                    flat_meta[k] = json.dumps(v, separators=(",", ":"))
        embedding = self._embed(text)
        coll.add(
            ids=[doc_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[flat_meta],
        )
        return doc_id

    def query_memory(
        self,
        project_id: str,
        query_text: str,
        limit: int = 5,
        *,
        app_id: Optional[str] = None,
    ) -> list[dict]:
        """Run a similarity search; return up to `limit` matches.

        Result rows:
          { id, text, score (cosine similarity in [0..1]), metadata }

        Lower distance from Chroma => more similar. Convert
        distance → similarity (1 - distance / 2) for cosine,
        with clamping to [0, 1] for the (rare) numeric overshoot.

        P4.2 — `app_id`, when set, requires `rag.read` on the gate.
        """
        _enforce_rag_scope(app_id, "rag.read")
        if not query_text or not query_text.strip():
            return []
        coll = self._collection(project_id)
        embedding = self._embed(query_text)
        result = coll.query(
            query_embeddings=[embedding],
            n_results=max(1, min(limit, 50)),
        )
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        rows: list[dict] = []
        for i, doc_id in enumerate(ids):
            distance = float(dists[i]) if i < len(dists) else 0.0
            # Chroma's cosine "distance" is 1 - cos_sim; clamp to [0, 1].
            similarity = max(0.0, min(1.0, 1.0 - distance))
            rows.append(
                {
                    "id": doc_id,
                    "text": docs[i] if i < len(docs) else "",
                    "score": similarity,
                    "metadata": metas[i] if i < len(metas) else {},
                }
            )
        return rows

    def delete_memory(self, project_id: str, memory_id: str) -> bool:
        """Drop a single memory by ID. Returns True if it existed."""
        coll = self._collection(project_id)
        try:
            existing = coll.get(ids=[memory_id])
            if not existing or not existing.get("ids"):
                return False
            coll.delete(ids=[memory_id])
            return True
        except Exception:
            return False

    def reset_project(self, project_id: str) -> None:
        """Drop the entire project collection. Idempotent."""
        try:
            self._client().delete_collection(self._collection_name(project_id))
        except Exception:
            # delete_collection raises if the collection doesn't exist;
            # that's an OK no-op for reset.
            pass


# ---------------------------------------------------------------------------
# Factory functions — referenced from core.repositories.__init__
# ---------------------------------------------------------------------------


def get_sqlite_app_installation_repository() -> SQLiteAppInstallationRepository:
    return SQLiteAppInstallationRepository()


def get_sqlite_user_sync_repository() -> SQLiteUserSyncRepository:
    return SQLiteUserSyncRepository()


def get_sqlite_chat_session_repository() -> SQLiteChatSessionRepository:
    return SQLiteChatSessionRepository()


def get_local_chroma_memory_repository() -> LocalChromaMemoryRepository:
    return LocalChromaMemoryRepository()


__all__ = [
    "SQLiteAppInstallationRepository",
    "SQLiteUserSyncRepository",
    "SQLiteChatSessionRepository",
    "LocalChromaMemoryRepository",
    "get_sqlite_app_installation_repository",
    "get_sqlite_user_sync_repository",
    "get_sqlite_chat_session_repository",
    "get_local_chroma_memory_repository",
]
