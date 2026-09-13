"""
backend/services/sync_engine.py — local-first → Convex sync bridge.

P3.3 — Sovereign sync engine for the local-first profile.

Lifecycle of a record:

  ┌──────────┐   local       ┌──────────┐   sync     ┌──────────┐
  │  client  │──────────────▶│  SQLite  │───────────▶│  Convex  │
  └──────────┘    write      │ dirty=T  │   push     │ remote   │
                             └──────────┘            └──────────┘
                                  ▲                       │
                                  └───────────────────────┘
                                       dirty=F + lastSyncedAt = now

Design points
-------------
1. **Pusher injection.** The engine doesn't import ConvexClient at
   module import — it accepts a `pusher` callable so unit tests can
   substitute an in-memory mock without touching the network. The
   default `_make_default_pusher()` wires the ConvexClient lazily and
   falls back to a noop when Convex is not configured (community
   profile / air-gapped run / dev mode without credentials).

2. **Best-effort per row.** A single bad row should not stop the
   whole sweep. Each push is wrapped in try/except; failures are
   counted and surfaced via `SyncResult.errors`, the row stays
   dirty, and the next sweep retries it.

3. **No tombstones yet.** Deletions are out of scope for P3.3 — the
   sync surface is INSERT/UPDATE only. A future P3.4 will add a
   `deletedAt` soft-delete column and teach the engine to push
   tombstones.

4. **Order matters.** ChatSession rows are pushed BEFORE
   ChatSessionMessage rows so the message rows always land into an
   existing parent on the Convex side. Within a sweep we follow the
   topological order: projects → chatSessions → chatSessionMessages.

5. **Locality gate.** `perform_sync()` is a no-op (returns SKIPPED)
   when `VOS3_LOCALITY_PREFERENCE != "local-first"`. That keeps the
   engine inert on cloud-first deployments where Convex is the
   primary store and SQLite is only an offline cache (no rows are
   ever marked dirty there anyway, but belt-and-braces).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

from sqlalchemy import select

from core.database.sqlite_setup import (
    ChatSession,
    ChatSessionMessage,
    Project,
    get_session,
)

logger = logging.getLogger(__name__)


# `Pusher` takes (table_name, payload_dict) and returns a coroutine
# that resolves once the row has been written remotely. Errors must
# be raised — the engine catches and counts.
Pusher = Callable[[str, dict], Awaitable[Any]]

# `Puller` takes (table_name, since_ms_or_None) and returns a coroutine
# that resolves to a list of row dicts updated AT OR AFTER `since_ms`.
# `since_ms=None` means "fetch the full table" (cold-start). Each
# returned dict must carry `_id` and `updatedAt` so the local merge
# can decide INSERT vs UPDATE vs CONFLICT.
Puller = Callable[[str, Optional[int]], Awaitable[list]]


# Conflict-resolution outcomes — small string set so tests and the
# `/api/system/sync/status` endpoint can pattern-match without
# parsing free-form text.
CONFLICT_CLOUD_WINS = "cloud_wins"
CONFLICT_LOCAL_WINS = "local_wins"
CONFLICT_TIED_LOCAL_KEEPS = "tied_local_keeps"


@dataclass
class SyncResult:
    """Summary of a single perform_sync() / perform_pull() pass."""

    started_at_ms: int
    finished_at_ms: int
    pushed: dict = field(
        default_factory=lambda: {
            "projects": 0,
            "chatSessions": 0,
            "chatSessionMessages": 0,
        }
    )
    pulled: dict = field(
        default_factory=lambda: {
            "projects": {"inserted": 0, "updated": 0},
            "chatSessions": {"inserted": 0, "updated": 0},
            "chatSessionMessages": {"inserted": 0, "updated": 0},
        }
    )
    # P3.4 — when both sides have diverged, the engine doesn't auto-
    # merge fields. It picks a winner by updatedAt (last-write-wins),
    # records the decision here, and leaves any other reconciliation
    # to a future schema-aware merge layer.
    conflicts: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped_reason: Optional[str] = None

    @property
    def total_pushed(self) -> int:
        return sum(self.pushed.values())

    @property
    def total_pulled(self) -> int:
        return sum(v["inserted"] + v["updated"] for v in self.pulled.values())

    @property
    def ok(self) -> bool:
        return self.skipped_reason is None and not self.errors

    def to_dict(self) -> dict:
        return {
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "pushed": dict(self.pushed),
            "pulled": {k: dict(v) for k, v in self.pulled.items()},
            "conflicts": list(self.conflicts),
            "errors": list(self.errors),
            "skipped_reason": self.skipped_reason,
            "total_pushed": self.total_pushed,
            "total_pulled": self.total_pulled,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Row → payload serializers. Kept narrow so the engine doesn't smuggle
# SQLAlchemy internals across the boundary; the pusher only sees JSON.
# ---------------------------------------------------------------------------


def _project_to_payload(p: Project) -> dict:
    return {
        "_id": p.id,
        "name": p.name,
        "description": p.description,
        "ownerId": p.ownerId,
        "organizationId": p.organizationId,
        "metadata": p.metadata_json,
        "isArchived": bool(p.isArchived),
        "createdAt": p.createdAt,
        "updatedAt": p.updatedAt,
    }


def _chat_session_to_payload(cs: ChatSession) -> dict:
    return {
        "_id": cs.id,
        "userId": cs.userId,
        "sessionId": cs.sessionId,
        "messageCount": cs.messageCount,
        "createdAt": cs.createdAt,
        "updatedAt": cs.updatedAt,
    }


def _chat_message_to_payload(m: ChatSessionMessage) -> dict:
    return {
        "_id": m.id,
        "sessionId": m.sessionId,
        "role": m.role,
        "content": m.content,
        "timestamp": m.timestamp,
        "metadata": m.metadata_json,
    }


# ---------------------------------------------------------------------------
# Cloud → local writers. These take a dict from the puller and either
# INSERT a new SQLAlchemy row or overwrite an existing one. Each
# writer ALSO sets dirty=False + lastSyncedAt=now because the row's
# canonical state has just been mirrored from the remote authority.
# ---------------------------------------------------------------------------


def _project_from_cloud(session, cloud: dict, *, now_ms: int) -> tuple:
    """Apply a cloud Project row to local SQLite.

    Returns (action, local_was_dirty) where action ∈ {"inserted","updated","conflict"}.
    On `"conflict"`, the caller is responsible for invoking the
    conflict resolver — this writer is just the mechanical apply.
    """
    existing = session.execute(
        select(Project).where(Project.id == cloud["_id"])
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Project(
                id=cloud["_id"],
                name=cloud.get("name") or "",
                description=cloud.get("description"),
                ownerId=cloud.get("ownerId") or "",
                organizationId=cloud.get("organizationId"),
                metadata_json=cloud.get("metadata"),
                isArchived=bool(cloud.get("isArchived", False)),
                createdAt=int(cloud.get("createdAt") or now_ms),
                updatedAt=int(cloud.get("updatedAt") or now_ms),
                dirty=False,
                lastSyncedAt=now_ms,
            )
        )
        return ("inserted", False)
    if existing.dirty:
        return ("conflict", True)
    existing.name = cloud.get("name") or existing.name
    existing.description = cloud.get("description")
    existing.ownerId = cloud.get("ownerId") or existing.ownerId
    existing.organizationId = cloud.get("organizationId")
    existing.metadata_json = cloud.get("metadata")
    existing.isArchived = bool(cloud.get("isArchived", existing.isArchived))
    existing.updatedAt = int(cloud.get("updatedAt") or now_ms)
    existing.dirty = False
    existing.lastSyncedAt = now_ms
    return ("updated", False)


def _chat_session_from_cloud(session, cloud: dict, *, now_ms: int) -> tuple:
    existing = session.execute(
        select(ChatSession).where(ChatSession.id == cloud["_id"])
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ChatSession(
                id=cloud["_id"],
                userId=cloud.get("userId") or "",
                sessionId=cloud.get("sessionId") or cloud["_id"],
                messageCount=int(cloud.get("messageCount") or 0),
                createdAt=int(cloud.get("createdAt") or now_ms),
                updatedAt=int(cloud.get("updatedAt") or now_ms),
                dirty=False,
                lastSyncedAt=now_ms,
            )
        )
        return ("inserted", False)
    if existing.dirty:
        return ("conflict", True)
    existing.userId = cloud.get("userId") or existing.userId
    existing.sessionId = cloud.get("sessionId") or existing.sessionId
    existing.messageCount = int(cloud.get("messageCount") or existing.messageCount or 0)
    existing.updatedAt = int(cloud.get("updatedAt") or now_ms)
    existing.dirty = False
    existing.lastSyncedAt = now_ms
    return ("updated", False)


def _chat_message_from_cloud(session, cloud: dict, *, now_ms: int) -> tuple:
    existing = session.execute(
        select(ChatSessionMessage).where(ChatSessionMessage.id == cloud["_id"])
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ChatSessionMessage(
                id=cloud["_id"],
                sessionId=cloud.get("sessionId") or "",
                role=cloud.get("role") or "user",
                content=cloud.get("content") or "",
                timestamp=int(cloud.get("timestamp") or now_ms),
                metadata_json=cloud.get("metadata"),
                dirty=False,
                lastSyncedAt=now_ms,
            )
        )
        return ("inserted", False)
    if existing.dirty:
        return ("conflict", True)
    existing.sessionId = cloud.get("sessionId") or existing.sessionId
    existing.role = cloud.get("role") or existing.role
    existing.content = cloud.get("content") or existing.content
    existing.timestamp = int(cloud.get("timestamp") or existing.timestamp)
    existing.metadata_json = cloud.get("metadata")
    existing.dirty = False
    existing.lastSyncedAt = now_ms
    return ("updated", False)


_TABLE_PLAN = {
    "projects": {
        "model": Project,
        "to_payload": _project_to_payload,
        "from_cloud": _project_from_cloud,
        "updated_at_key": "updatedAt",
    },
    "chatSessions": {
        "model": ChatSession,
        "to_payload": _chat_session_to_payload,
        "from_cloud": _chat_session_from_cloud,
        "updated_at_key": "updatedAt",
    },
    "chatSessionMessages": {
        "model": ChatSessionMessage,
        # Messages don't carry a separate updatedAt — `timestamp` IS
        # the write time. Treat it as the LWW key.
        "to_payload": _chat_message_to_payload,
        "from_cloud": _chat_message_from_cloud,
        "updated_at_key": "timestamp",
    },
}


# ---------------------------------------------------------------------------
# Conflict resolver — last-write-wins by updatedAt (or `timestamp` for
# rows that don't have a separate updatedAt). Pure function so it's
# trivially unit-testable.
# ---------------------------------------------------------------------------


def resolve_conflict(
    local_row,
    cloud_row: dict,
    *,
    updated_at_key: str = "updatedAt",
) -> str:
    """Decide which side wins on a row that is BOTH dirty locally and
    newer-than-watermark on the cloud.

    Strategy
    --------
    last-write-wins, with two extra rules:
      * a tie keeps the local row dirty (the local writer hasn't
        observed the cloud value yet; let the next push reconcile).
      * a missing cloud `updatedAt` is treated as 0 so cloud loses by
        default — we never overwrite local edits with a row that
        can't prove it's newer.

    Returns one of:
      "cloud_wins"        — caller should overwrite local with cloud
      "local_wins"        — caller should keep local, dirty stays True
      "tied_local_keeps"  — caller should keep local, dirty stays True
    """
    cloud_ts = int(cloud_row.get(updated_at_key) or 0)
    # `local_row` is the SQLAlchemy ORM row; pull the matching attr.
    local_ts = int(getattr(local_row, updated_at_key, 0) or 0)

    if cloud_ts > local_ts:
        return CONFLICT_CLOUD_WINS
    if local_ts > cloud_ts:
        return CONFLICT_LOCAL_WINS
    return CONFLICT_TIED_LOCAL_KEEPS


def _apply_cloud_overwrite(session, model, cloud_row: dict, *, now_ms: int) -> None:
    """Overwrite an EXISTING local row with cloud data, clearing dirty.

    Called only after `resolve_conflict()` returned CONFLICT_CLOUD_WINS.
    Mirrors the field-by-field copy in `_*_from_cloud()` but assumes
    the row exists (no INSERT branch).
    """
    if model is Project:
        existing = session.execute(
            select(Project).where(Project.id == cloud_row["_id"])
        ).scalar_one_or_none()
        if existing is None:
            return
        existing.name = cloud_row.get("name") or existing.name
        existing.description = cloud_row.get("description")
        existing.ownerId = cloud_row.get("ownerId") or existing.ownerId
        existing.organizationId = cloud_row.get("organizationId")
        existing.metadata_json = cloud_row.get("metadata")
        existing.isArchived = bool(cloud_row.get("isArchived", existing.isArchived))
        existing.updatedAt = int(cloud_row.get("updatedAt") or now_ms)
        existing.dirty = False
        existing.lastSyncedAt = now_ms
    elif model is ChatSession:
        existing = session.execute(
            select(ChatSession).where(ChatSession.id == cloud_row["_id"])
        ).scalar_one_or_none()
        if existing is None:
            return
        existing.userId = cloud_row.get("userId") or existing.userId
        existing.sessionId = cloud_row.get("sessionId") or existing.sessionId
        existing.messageCount = int(
            cloud_row.get("messageCount") or existing.messageCount or 0
        )
        existing.updatedAt = int(cloud_row.get("updatedAt") or now_ms)
        existing.dirty = False
        existing.lastSyncedAt = now_ms
    elif model is ChatSessionMessage:
        existing = session.execute(
            select(ChatSessionMessage).where(ChatSessionMessage.id == cloud_row["_id"])
        ).scalar_one_or_none()
        if existing is None:
            return
        existing.sessionId = cloud_row.get("sessionId") or existing.sessionId
        existing.role = cloud_row.get("role") or existing.role
        existing.content = cloud_row.get("content") or existing.content
        existing.timestamp = int(cloud_row.get("timestamp") or existing.timestamp)
        existing.metadata_json = cloud_row.get("metadata")
        existing.dirty = False
        existing.lastSyncedAt = now_ms


# ---------------------------------------------------------------------------
# Default pusher — lazy ConvexClient wiring; noop when unavailable.
# ---------------------------------------------------------------------------


async def _noop_pusher(table: str, payload: dict) -> None:
    """No-op pusher. Used when Convex credentials are unset; lets
    `perform_sync()` still advance the dirty flag in dev mode so the
    flow is testable without a live Convex deployment."""
    logger.debug("[sync] noop_pusher table=%s id=%s", table, payload.get("_id"))


async def _noop_puller(table: str, since_ms: Optional[int]) -> list:
    """No-op puller — returns empty list so perform_pull() is a no-op
    when no remote is configured."""
    logger.debug("[sync] noop_puller table=%s since=%s", table, since_ms)
    return []


def _make_default_pusher() -> Pusher:
    """Build the production pusher.

    Tries to import ConvexClient and bind it to a singleton. If the
    client raises at import or instantiation (no CONVEX_URL set,
    missing deps, etc.) we fall back to the noop pusher with a single
    warning — the engine still advances dirty bits locally so a later
    "real" sync can be authoritative when credentials are wired up.
    """
    try:
        from db.convex import ConvexClient
    except ImportError as exc:
        logger.warning(
            "[sync] ConvexClient unavailable (%s) — using noop pusher",
            exc,
        )
        return _noop_pusher

    try:
        client = ConvexClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sync] ConvexClient construction failed (%s) — using noop pusher",
            exc,
        )
        return _noop_pusher

    table_to_mutation = {
        "projects": "projects:upsert",
        "chatSessions": "chatSessions:upsert",
        "chatSessionMessages": "chatSessionMessages:upsert",
    }

    async def _push(table: str, payload: dict) -> Any:
        fn = table_to_mutation.get(table)
        if fn is None:
            raise ValueError(f"no Convex mutation mapped for table {table!r}")
        return await client.mutation(fn, payload)

    return _push


def _make_default_puller() -> Puller:
    """Build the production puller against ConvexClient.

    Falls back to `_noop_puller` (empty list) when Convex isn't
    importable or constructable — same posture as the default pusher.
    """
    try:
        from db.convex import ConvexClient
    except ImportError as exc:
        logger.warning(
            "[sync] ConvexClient unavailable (%s) — using noop puller",
            exc,
        )
        return _noop_puller

    try:
        client = ConvexClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sync] ConvexClient construction failed (%s) — using noop puller",
            exc,
        )
        return _noop_puller

    table_to_query = {
        "projects": "projects:listUpdatedSince",
        "chatSessions": "chatSessions:listUpdatedSince",
        "chatSessionMessages": "chatSessionMessages:listUpdatedSince",
    }

    async def _pull(table: str, since_ms: Optional[int]) -> list:
        fn = table_to_query.get(table)
        if fn is None:
            raise ValueError(f"no Convex query mapped for table {table!r}")
        result = await client.query(fn, {"sinceMs": since_ms or 0})
        # Convex queries normally return a list directly; defend against
        # an envelope shape so we don't surface KeyError on a quirky
        # mutation response.
        if isinstance(result, dict) and "rows" in result:
            return list(result["rows"])
        return list(result or [])

    return _pull


# ---------------------------------------------------------------------------
# SovereignSyncEngine
# ---------------------------------------------------------------------------


class SovereignSyncEngine:
    """Reads dirty rows from SQLite and pushes them to Convex.

    Stateless aside from the injected pusher — each `perform_sync()`
    call opens a fresh SQLAlchemy session and commits dirty-clear
    transitions in small batches.
    """

    def __init__(
        self,
        *,
        pusher: Optional[Pusher] = None,
        puller: Optional[Puller] = None,
        batch_size: int = 100,
    ):
        self.pusher: Pusher = pusher or _make_default_pusher()
        self.puller: Puller = puller or _make_default_puller()
        self.batch_size = batch_size

    # --- Public entry ----------------------------------------------------

    async def perform_sync(self) -> SyncResult:
        """Run a single end-to-end sync pass.

        Returns a SyncResult. Never raises for a single bad row —
        per-row errors are recorded under `result.errors`.
        """
        start = int(time.time() * 1000)
        result = SyncResult(started_at_ms=start, finished_at_ms=start)

        if os.getenv("VOS3_LOCALITY_PREFERENCE", "").strip().lower() != "local-first":
            result.skipped_reason = "locality_not_local_first"
            result.finished_at_ms = int(time.time() * 1000)
            return result

        # Order matters: parent rows before child rows.
        await self._sync_table(
            table="projects",
            model=Project,
            to_payload=_project_to_payload,
            result=result,
        )
        await self._sync_table(
            table="chatSessions",
            model=ChatSession,
            to_payload=_chat_session_to_payload,
            result=result,
        )
        await self._sync_table(
            table="chatSessionMessages",
            model=ChatSessionMessage,
            to_payload=_chat_message_to_payload,
            result=result,
        )

        result.finished_at_ms = int(time.time() * 1000)
        return result

    # --- Pull (Convex → SQLite) -----------------------------------------

    async def perform_pull(self, *, since_ms: Optional[int] = None) -> SyncResult:
        """Pull cloud-side updates into the local SQLite mirror.

        Parameters
        ----------
        since_ms
            High-water mark to pass to the puller. When None, the
            engine computes the local watermark per table via
            `_table_watermark()` (MAX(lastSyncedAt)).

        For each remote row:
          - absent locally        → INSERT (dirty=False, lastSyncedAt=now)
          - present and clean     → UPDATE overwrite + clear flag
          - present and dirty     → resolve_conflict():
              * cloud newer       → overwrite local, clear dirty
              * local newer       → keep local, dirty stays True
              * tied              → keep local (same as local-newer)

        Conflicts are recorded under `SyncResult.conflicts` regardless
        of the resolution so the UI can surface them.
        """
        start = int(time.time() * 1000)
        result = SyncResult(started_at_ms=start, finished_at_ms=start)

        if os.getenv("VOS3_LOCALITY_PREFERENCE", "").strip().lower() != "local-first":
            result.skipped_reason = "locality_not_local_first"
            result.finished_at_ms = int(time.time() * 1000)
            return result

        # Topological order — same as push (parents before children).
        for table in ("projects", "chatSessions", "chatSessionMessages"):
            plan = _TABLE_PLAN[table]
            try:
                watermark = (
                    since_ms
                    if since_ms is not None
                    else await asyncio.to_thread(_table_watermark, plan["model"])
                )
                rows = await self.puller(table, watermark)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "table": table,
                        "id": None,
                        "error": f"pull failed: {type(exc).__name__}: {exc}",
                    }
                )
                logger.warning(
                    "[sync] pull failed table=%s err=%s",
                    table,
                    exc,
                )
                continue

            if not rows:
                continue

            await asyncio.to_thread(
                self._merge_pulled_rows,
                table=table,
                rows=rows,
                result=result,
            )

        result.finished_at_ms = int(time.time() * 1000)
        return result

    def _merge_pulled_rows(
        self,
        *,
        table: str,
        rows: list,
        result: SyncResult,
    ) -> None:
        """Apply a batch of cloud rows to local SQLite.

        Runs synchronously inside a single SQLAlchemy session — keeps
        each table's merge atomic. Updates `result.pulled[table]` and
        `result.conflicts` in place.
        """
        plan = _TABLE_PLAN[table]
        model = plan["model"]
        from_cloud = plan["from_cloud"]
        updated_at_key = plan["updated_at_key"]
        now_ms = int(time.time() * 1000)

        with get_session() as session:
            for cloud_row in rows:
                row_id = cloud_row.get("_id")
                if not row_id:
                    result.errors.append(
                        {
                            "table": table,
                            "id": None,
                            "error": "puller returned row without _id",
                        }
                    )
                    continue
                try:
                    action, _ = from_cloud(session, cloud_row, now_ms=now_ms)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {
                            "table": table,
                            "id": row_id,
                            "error": f"merge failed: {type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                if action == "inserted":
                    result.pulled[table]["inserted"] += 1
                elif action == "updated":
                    result.pulled[table]["updated"] += 1
                elif action == "conflict":
                    # Re-fetch the row for the LWW decision — the
                    # `_*_from_cloud` writer already short-circuited
                    # on `dirty=True` without applying anything.
                    local = session.execute(
                        select(model).where(model.id == row_id)
                    ).scalar_one_or_none()
                    if local is None:  # pragma: no cover — defensive
                        continue
                    decision = resolve_conflict(
                        local,
                        cloud_row,
                        updated_at_key=updated_at_key,
                    )
                    result.conflicts.append(
                        {
                            "table": table,
                            "id": row_id,
                            "decision": decision,
                            "local_updated_at": getattr(local, updated_at_key, None),
                            "cloud_updated_at": cloud_row.get(updated_at_key),
                        }
                    )
                    if decision == CONFLICT_CLOUD_WINS:
                        _apply_cloud_overwrite(session, model, cloud_row, now_ms=now_ms)
                        result.pulled[table]["updated"] += 1
                    # local_wins / tied_local_keeps → no local change;
                    # the row will be pushed on the next perform_sync().
            session.commit()

    # --- Internals -------------------------------------------------------

    async def _sync_table(
        self,
        *,
        table: str,
        model,
        to_payload: Callable[[Any], dict],
        result: SyncResult,
    ) -> None:
        """Read dirty rows for one table, push each, clear dirty on success."""
        rows = await asyncio.to_thread(_fetch_dirty_rows, model, self.batch_size)
        if not rows:
            return

        now = int(time.time() * 1000)
        successful_ids: list = []

        for row_payload, row_id in rows:
            try:
                await self.pusher(table, row_payload)
                successful_ids.append(row_id)
            except Exception as exc:  # noqa: BLE001
                # Record but keep going — next sweep retries.
                result.errors.append(
                    {
                        "table": table,
                        "id": row_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                logger.warning(
                    "[sync] push failed table=%s id=%s err=%s",
                    table,
                    row_id,
                    exc,
                )

        if successful_ids:
            await asyncio.to_thread(_clear_dirty_flags, model, successful_ids, now)
            result.pushed[table] = result.pushed.get(table, 0) + len(successful_ids)


# ---------------------------------------------------------------------------
# Sync helpers (run in the threadpool to avoid blocking the event loop
# on SQLite I/O — the engine is async even though SQLAlchemy is sync).
# ---------------------------------------------------------------------------


def _fetch_dirty_rows(model, limit: int) -> list:
    """Return [(payload_dict, id), …] for up to `limit` dirty rows."""
    with get_session() as session:
        rows = (
            session.execute(select(model).where(model.dirty.is_(True)).limit(limit))
            .scalars()
            .all()
        )

        # Serialize while still inside the session — once the session
        # closes, lazy attributes become detached.
        if model is Project:
            return [(_project_to_payload(r), r.id) for r in rows]
        if model is ChatSession:
            return [(_chat_session_to_payload(r), r.id) for r in rows]
        if model is ChatSessionMessage:
            return [(_chat_message_to_payload(r), r.id) for r in rows]
        return []


def _clear_dirty_flags(model, ids: Iterable[str], now_ms: int) -> None:
    """Flip dirty=False and stamp lastSyncedAt for the given IDs."""
    id_list = list(ids)
    if not id_list:
        return
    with get_session() as session:
        rows = (
            session.execute(select(model).where(model.id.in_(id_list))).scalars().all()
        )
        for r in rows:
            r.dirty = False
            r.lastSyncedAt = now_ms
        session.commit()


def _table_watermark(model) -> int:
    """Return MAX(lastSyncedAt) for `model`, or 0 when empty."""
    from sqlalchemy import func

    with get_session() as session:
        val = session.execute(select(func.max(model.lastSyncedAt))).scalar()
        return int(val or 0)


def _table_dirty_count(model) -> int:
    """Return the count of rows where dirty=True for `model`."""
    from sqlalchemy import func

    with get_session() as session:
        val = session.execute(
            select(func.count()).select_from(model).where(model.dirty.is_(True))
        ).scalar()
        return int(val or 0)


def sync_status_snapshot() -> dict:
    """Read-only summary used by `GET /api/system/sync/status`.

    Shape:
      {
        "last_synced_at_ms": int | None,    # max watermark across all tables
        "dirty_count":       int,           # total dirty rows
        "tables": {
          "projects":            {"dirty": int, "last_synced_at_ms": int|None},
          "chatSessions":        {"dirty": int, "last_synced_at_ms": int|None},
          "chatSessionMessages": {"dirty": int, "last_synced_at_ms": int|None},
        },
      }

    Never raises — if the schema isn't initialized yet (fresh boot)
    the helper bootstraps it via `init_db()`. The endpoint is meant
    to be safe to poll from the UI.
    """
    # Lazy import so the API layer doesn't bring sync_engine in until
    # it's actually used.
    from core.database.sqlite_setup import init_db

    try:
        init_db()
    except Exception as exc:  # pragma: no cover - fresh-DB defensive
        logger.debug("[sync] sync_status_snapshot init_db skipped: %s", exc)

    tables_out: dict = {}
    total_dirty = 0
    max_watermark: Optional[int] = None
    for table, plan in _TABLE_PLAN.items():
        model = plan["model"]
        try:
            wm = _table_watermark(model)
            dc = _table_dirty_count(model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[sync] watermark/dirty count failed for %s: %s", table, exc)
            wm, dc = 0, 0
        tables_out[table] = {
            "dirty": dc,
            "last_synced_at_ms": wm or None,
        }
        total_dirty += dc
        if wm:
            max_watermark = max(max_watermark or 0, wm)

    return {
        "last_synced_at_ms": max_watermark,
        "dirty_count": total_dirty,
        "tables": tables_out,
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "Pusher",
    "Puller",
    "SovereignSyncEngine",
    "SyncResult",
    "resolve_conflict",
    "sync_status_snapshot",
    "CONFLICT_CLOUD_WINS",
    "CONFLICT_LOCAL_WINS",
    "CONFLICT_TIED_LOCAL_KEEPS",
]
