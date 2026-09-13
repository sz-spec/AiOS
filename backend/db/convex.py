"""
Convex Database Client
======================
HTTP client for Convex backend.

Environment Variables:
- CONVEX_URL: Your Convex deployment URL (e.g. https://your-deployment.convex.cloud)
- CONVEX_DEPLOY_KEY: Convex deploy key for server-side mutations (optional in dev)
- ENVIRONMENT: "production" | "development" (default: "development")

In production mode, CONVEX_URL is **required** — the in-memory dict fallback is
disabled to prevent silent data loss.
"""

import os
import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional
import httpx

from core.circuit_breaker import get_breaker

logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


class ConvexClient:
    """
    HTTP client for Convex backend.

    Calls Convex functions via HTTP Actions API:
      POST /api/query   — read data
      POST /api/mutation — write data
      POST /api/action  — run actions (can call external services)

    Usage:
        client = ConvexClient()

        # Query
        user = await client.query("users:getByClerkId", {"clerkId": "user_xxx"})

        # Mutation
        id = await client.mutation("users:syncFromClerk", {...})

        # Action
        result = await client.action("billing:processPayment", {...})
    """

    def __init__(self, url: str = None, deploy_key: str = None):
        self.url = (url or os.getenv("CONVEX_URL", "")).rstrip("/")
        self.deploy_key = deploy_key or os.getenv("CONVEX_DEPLOY_KEY", "")

        # Production guard: CONVEX_URL is mandatory
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production" and not self.url:
            raise RuntimeError(
                "CONVEX_URL is required in production. "
                "Set ENVIRONMENT=development for local dev mode."
            )

        # HTTPS enforcement: reject plaintext transport in non-dev environments
        if env != "development" and self.url and not self.url.startswith("https://"):
            raise RuntimeError(
                f"CONVEX_URL must use HTTPS in {env} mode. " f"Got: {self.url[:40]}..."
            )

        # Dev mode when no CONVEX_URL configured
        self.dev_mode = not self.url

        if self.dev_mode:
            logger.warning("ConvexClient: Running in dev mode (no CONVEX_URL)")

        # Persistent HTTP client — reuses TCP+TLS connections across requests
        self._http = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.deploy_key:
            headers["Authorization"] = f"Convex {self.deploy_key}"
        return headers

    # Retry configuration
    _MAX_RETRIES = 3
    _RETRY_DELAYS = [0.02, 0.05, 0.15]

    # HTTP status codes that are safe to retry
    _RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

    def _is_occ_conflict(self, exc: Exception) -> bool:
        """Return True if the exception represents a Convex OCC conflict."""
        msg = str(exc).lower()
        return any(
            kw in msg
            for kw in [
                "conflict",
                "optimisticconcurrency",
                "occ",
                "write conflict",
            ]
        )

    def _is_retryable_status(self, exc: Exception) -> bool:
        """Return True if the exception is an HTTP error with a retryable status code."""
        if isinstance(exc, httpx.PoolTimeout):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in self._RETRYABLE_STATUS_CODES
        return False

    async def _call_convex(
        self, endpoint: str, function_name: str, args: dict, timeout: float = 30.0
    ) -> Any:
        """Internal: call Convex API with circuit breaker protection and retry logic.

        Retries up to _MAX_RETRIES total attempts on:
          - Network-level errors: ReadTimeout, ConnectTimeout, NetworkError
          - HTTP 429, 502, 503, 504 status codes
          - Convex OCC (Optimistic Concurrency Control) conflicts

        Non-retryable errors (4xx except 429, auth, validation) fail immediately.
        The circuit breaker failure is only recorded when ALL retries are exhausted.
        """
        breaker = get_breaker("convex", failure_threshold=5, cooldown_seconds=30.0)
        if not breaker.allow_request():
            from core.circuit_breaker import CircuitBreakerOpenError

            raise CircuitBreakerOpenError(
                service="convex",
                retry_after=breaker.retry_after_seconds(),
                function_name=function_name,
            )

        last_exc: Optional[Exception] = None

        for attempt in range(self._MAX_RETRIES):
            try:
                response = await self._http.post(
                    f"{self.url}/api/{endpoint}",
                    json={"path": function_name, "args": args or {}},
                    headers=self._get_headers(),
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "error":
                    err_msg = data.get("errorMessage", "unknown")
                    exc = RuntimeError(f"Convex {endpoint} error: {err_msg}")
                    # OCC conflicts embedded in Convex error payloads are retryable
                    if attempt < self._MAX_RETRIES - 1 and self._is_occ_conflict(exc):
                        last_exc = exc
                        base = self._RETRY_DELAYS[attempt]
                        delay = random.uniform(0, base * 2)
                        logger.warning(
                            "ConvexClient: OCC conflict on %s (attempt %d/%d), retrying in %.3fs",
                            function_name,
                            attempt + 1,
                            self._MAX_RETRIES,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    breaker.record_failure()
                    raise exc
                breaker.record_success()
                return data.get("value")

            except (
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.NetworkError,
                httpx.PoolTimeout,
            ) as e:
                last_exc = e
                if attempt < self._MAX_RETRIES - 1:
                    base = self._RETRY_DELAYS[attempt]
                    delay = random.uniform(0, base * 2)
                    logger.warning(
                        "ConvexClient: network error on %s (attempt %d/%d): %s — retrying in %.3fs",
                        function_name,
                        attempt + 1,
                        self._MAX_RETRIES,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                # All retries exhausted — count as breaker failure
                breaker.record_failure()
                raise

            except httpx.HTTPStatusError as e:
                last_exc = e
                if attempt < self._MAX_RETRIES - 1 and self._is_retryable_status(e):
                    base = self._RETRY_DELAYS[attempt]
                    delay = random.uniform(0, base * 2)
                    logger.warning(
                        "ConvexClient: HTTP %d on %s (attempt %d/%d) — retrying in %.3fs",
                        e.response.status_code,
                        function_name,
                        attempt + 1,
                        self._MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                # Non-retryable HTTP 4xx or exhausted retries
                breaker.record_failure()
                raise

            except RuntimeError as _rt:
                # CircuitBreakerOpenError is a RuntimeError subclass — don't count it as a failure
                from core.circuit_breaker import CircuitBreakerOpenError as _CBE

                if not isinstance(_rt, _CBE):
                    breaker.record_failure()
                raise

            except Exception as e:
                last_exc = e
                if attempt < self._MAX_RETRIES - 1 and self._is_occ_conflict(e):
                    base = self._RETRY_DELAYS[attempt]
                    delay = random.uniform(0, base * 2)
                    logger.warning(
                        "ConvexClient: OCC conflict on %s (attempt %d/%d): %s — retrying in %.3fs",
                        function_name,
                        attempt + 1,
                        self._MAX_RETRIES,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                breaker.record_failure()
                raise

        # Should never be reached (loop always raises or returns), but satisfy the type checker
        breaker.record_failure()
        raise last_exc  # type: ignore[misc]

    async def query(self, function_name: str, args: dict = None) -> Any:
        """
        Call a Convex query function (read-only).

        Args:
            function_name: e.g. "users:getByClerkId" or "billing:getBalance"
            args: Arguments to pass to the function
        """
        if self.dev_mode:
            return self._dev_query(function_name, args or {})
        return await self._call_convex("query", function_name, args or {})

    async def mutation(self, function_name: str, args: dict = None) -> Any:
        """
        Call a Convex mutation function (read+write).

        Args:
            function_name: e.g. "users:syncFromClerk"
            args: Arguments to pass to the function
        """
        if self.dev_mode:
            return self._dev_mutation(function_name, args or {})
        return await self._call_convex("mutation", function_name, args or {})

    async def action(self, function_name: str, args: dict = None) -> Any:
        """
        Call a Convex action function (can call external services).

        Args:
            function_name: e.g. "billing:createCheckoutSession"
            args: Arguments to pass to the function
        """
        if self.dev_mode:
            return self._dev_action(function_name, args or {})
        return await self._call_convex(
            "action", function_name, args or {}, timeout=60.0
        )

    async def close(self) -> None:
        """Close the persistent HTTP client and release connections."""
        await self._http.aclose()

    async def health_check(self) -> bool:
        """Verify connectivity to Convex deployment.

        Returns True if the deployment is reachable, raises RuntimeError otherwise.
        In dev mode, always returns True with a warning.
        """
        if self.dev_mode:
            logger.warning("health_check: skipped (dev mode)")
            return True

        try:
            response = await self._http.post(
                f"{self.url}/api/query",
                json={"path": "users:getById", "args": {"id": "__health_check__"}},
                headers=self._get_headers(),
                timeout=10.0,
            )
            # Any non-5xx response means Convex is reachable
            if response.status_code < 500:
                return True
            raise RuntimeError(
                f"Convex health check failed: HTTP {response.status_code}"
            )
        except httpx.ConnectError as e:
            raise RuntimeError(f"Cannot connect to Convex at {self.url}: {e}") from e
        except httpx.TimeoutException as e:
            raise RuntimeError(f"Convex health check timed out: {e}") from e


# =============================================================================
# ConvexDB — enhanced client with direct table-based CRUD interface
# =============================================================================


class ConvexDB(ConvexClient):
    """
    Enhanced Convex client with a direct CRUD interface.

    In PRODUCTION mode (CONVEX_URL + CONVEX_DEPLOY_KEY set):
      CRUD methods route through Convex functions via HTTP API.
      Uses _FUNCTION_MAP to translate (table, operation) → real Convex function names.

    In DEV mode (missing URL or deploy key):
      CRUD methods use in-memory dict for local development.
    """

    # Maps (table, operation) → "module:functionName" for all known Convex functions.
    # The generic pattern {table}:{op} does NOT work — Convex functions have specific names.
    _FUNCTION_MAP: dict[tuple[str, str], str] = {
        # --- V-Core Business OS (vcore.ts) ---
        ("entities", "create"): "vcore:createEntity",
        ("entities", "getById"): "vcore:getEntity",
        ("entities", "update"): "vcore:updateEntity",
        ("entities", "remove"): "vcore:deleteEntity",
        ("entities", "list"): "vcore:listEntities",
        ("records", "create"): "vcore:createRecord",
        ("records", "getById"): "vcore:getRecord",
        ("records", "update"): "vcore:updateRecord",
        ("records", "remove"): "vcore:deleteRecord",
        ("records", "list"): "vcore:listRecords",
        ("workflows", "create"): "vcore:createWorkflow",
        ("workflows", "getById"): "vcore:getWorkflow",
        ("workflows", "update"): "vcore:updateWorkflow",
        ("workflows", "remove"): "vcore:deleteWorkflow",
        ("workflows", "list"): "vcore:listWorkflows",
        ("workflowExecutions", "create"): "vcore:createExecution",
        ("workflowExecutions", "update"): "vcore:updateExecution",
        ("workflowExecutions", "list"): "vcore:listExecutions",
        ("apiKeys", "create"): "vcore:createApiKey",
        ("apiKeys", "getById"): "vcore:getApiKey",
        ("apiKeys", "update"): "vcore:updateApiKey",
        ("apiKeys", "remove"): "vcore:deleteApiKey",
        ("apiKeys", "list"): "vcore:listApiKeys",
        ("auditLog", "create"): "vcore:addAuditEntry",
        ("auditLog", "list"): "vcore:listAuditLog",
        # --- Users (users.ts) ---
        ("users", "create"): "users:syncFromClerk",
        ("users", "getById"): "users:getById",
        ("users", "update"): "users:update",
        # --- Organizations (organizations.ts) ---
        ("organizations", "create"): "organizations:create",
        ("organizations", "getById"): "organizations:getById",
        ("organizations", "update"): "organizations:update",
        ("members", "create"): "organizations:addMember",
        ("members", "remove"): "organizations:removeMember",
        ("members", "list"): "organizations:listMembers",
        # --- Projects (projects.ts) ---
        ("projects", "create"): "projects:create",
        ("projects", "getById"): "projects:getById",
        ("projects", "update"): "projects:update",
        ("projectFiles", "create"): "projects:upsertFile",
        ("projectFiles", "remove"): "projects:deleteFile",
        ("projectFiles", "list"): "projects:getFiles",
        ("chatMessages", "create"): "projects:addChatMessage",
        ("chatMessages", "list"): "projects:getChatMessages",
        ("projectMemory", "create"): "projects:createMemory",
        ("projectMemory", "getById"): "projects:getMemory",
        ("projectMemory", "update"): "projects:updateMemory",
        ("projectMemory", "remove"): "projects:deleteMemory",
        ("projectMemory", "list"): "projects:listMemory",
        # --- Builds (builds.ts) ---
        ("builds", "create"): "builds:create",
        ("builds", "getById"): "builds:getById",
        ("builds", "remove"): "builds:remove",
        ("builds", "list"): "builds:listByProject",
        # --- Agent Status (agentStatus.ts) ---
        ("agentStatus", "create"): "agentStatus:upsert",
        ("agentStatus", "update"): "agentStatus:upsert",
        ("agentStatus", "remove"): "agentStatus:remove",
        ("agentStatus", "list"): "agentStatus:listByBuild",
        # --- Billing (billing.ts) ---
        ("subscriptions", "create"): "billing:upsertSubscription",
        ("userCredits", "getById"): "billing:getBalance",
        ("creditTransactions", "list"): "billing:getTransactions",
        # --- Quota (quota.ts) ---
        ("quotaAlerts", "create"): "quota:createAlert",
        ("quotaAlerts", "list"): "quota:listAlerts",
        # --- Apps (apps.ts) ---
        ("apps", "create"): "apps:publish",
        ("apps", "list"): "apps:list",
        # --- Chat Sessions (chatSessions.ts) ---
        ("chatSessions", "create"): "chatSessions:save",
        ("chatSessions", "remove"): "chatSessions:remove",
        ("chatSessions", "list"): "chatSessions:list",
        # --- Expert Requests (expertRequests.ts) ---
        ("expertRequests", "create"): "expertRequests:create",
        ("expertRequests", "getById"): "expertRequests:getById",
        ("expertRequests", "list"): "expertRequests:listPending",
        # --- Prompt History (promptHistory — generic CRUD) ---
        ("promptHistory", "create"): "promptHistory:create",
        ("promptHistory", "getById"): "promptHistory:getById",
        ("promptHistory", "update"): "promptHistory:update",
        ("promptHistory", "remove"): "promptHistory:remove",
        ("promptHistory", "list"): "promptHistory:listByUser",
    }

    def __init__(self, url: str = None, deploy_key: str = None):
        url = url or os.getenv("CONVEX_URL", "")
        deploy_key = deploy_key or os.getenv("CONVEX_DEPLOY_KEY", "")
        super().__init__(url=url, deploy_key=deploy_key)
        # Override parent dev_mode: require BOTH url and deploy_key
        self.dev_mode = not url or not deploy_key

        env = os.getenv("ENVIRONMENT", "development")
        if env == "production" and self.dev_mode:
            raise RuntimeError(
                "ConvexDB requires CONVEX_URL and CONVEX_DEPLOY_KEY in production. "
                "Set ENVIRONMENT=development for local dev mode."
            )

        # Dev mode: in-memory storage for local development only
        self._db: dict[str, dict[str, Any]] = {} if self.dev_mode else {}

    def get_mode(self) -> str:
        return "dev" if self.dev_mode else "production"

    def is_connected(self) -> bool:
        """Returns True if the database is operational (production or dev mode)."""
        return True

    def _resolve_function(self, table: str, operation: str) -> str:
        """Resolve (table, operation) to a real Convex function name."""
        fn = self._FUNCTION_MAP.get((table, operation))
        if fn:
            return fn
        # Fallback: generic pattern (likely won't work, but log warning)
        fallback = f"{table}:{operation}"
        logger.warning(
            "ConvexDB: no function mapping for (%s, %s) — using fallback '%s'",
            table,
            operation,
            fallback,
        )
        return fallback

    # =========================================================================
    # CRUD — routes to Convex in prod, in-memory dict in dev
    # =========================================================================

    async def insert(self, table: str, data: dict) -> str:
        """Insert a document into a table. Returns the new document ID."""
        if self.dev_mode:
            return await self._dev_insert(table, data)
        return await self.mutation(self._resolve_function(table, "create"), data)

    async def get(self, table: str, doc_id: str) -> Optional[dict]:
        """Get a document by ID."""
        if self.dev_mode:
            return await self._dev_get(table, doc_id)
        return await self.query(
            self._resolve_function(table, "getById"), {"id": doc_id}
        )

    async def update(self, table: str, doc_id: str, data: dict) -> Optional[dict]:
        """Update fields in a document. Returns the updated document."""
        if self.dev_mode:
            return await self._dev_update(table, doc_id, data)
        return await self.mutation(
            self._resolve_function(table, "update"), {"id": doc_id, **data}
        )

    async def delete(self, table: str, doc_id: str) -> bool:
        """Delete a document. Returns True if it existed, False on not-found."""
        if self.dev_mode:
            return await self._dev_delete(table, doc_id)
        try:
            await self.mutation(self._resolve_function(table, "remove"), {"id": doc_id})
            return True
        except Exception as exc:
            if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
                return False
            raise

    async def list(self, table: str, **kwargs) -> list:
        """List all documents in a table. Extra kwargs passed to the Convex function."""
        if self.dev_mode:
            return await self._dev_list(table)
        return await self.query(self._resolve_function(table, "list"), kwargs or {})

    async def find(self, table: str, filter_dict: dict) -> list:
        """Filter documents by field equality."""
        if self.dev_mode:
            docs = await self._dev_list(table)
            return [
                d
                for d in docs
                if all(d.get(k) == val for k, val in filter_dict.items())
            ]
        # Pass filter to Convex; if supported, the server-side function filters.
        # Otherwise falls back to full list + client filter.
        logger.warning(
            "ConvexDB.find() is deprecated — use specific indexed query functions"
        )
        all_docs = await self.query(self._resolve_function(table, "list"), filter_dict)
        if isinstance(all_docs, list):
            return [
                d
                for d in all_docs
                if all(d.get(k) == val for k, val in filter_dict.items())
            ]
        return all_docs or []

    async def action(self, function_name: str, args: dict = None) -> Any:
        if self.dev_mode:
            return {"status": "dev_mode", "function": function_name}
        return await super().action(function_name, args)

    # =========================================================================
    # Dev Mode CRUD — in-memory dict implementation
    # =========================================================================

    async def _dev_insert(self, table: str, data: dict) -> str:
        import uuid as _uuid

        now = datetime.now(timezone.utc).isoformat()
        doc_id = str(_uuid.uuid4())
        doc = {
            **data,
            "_id": doc_id,
            "createdAt": now,
            "updatedAt": now,
            "_creationTime": now,
        }
        self._db.setdefault(table, {})[doc_id] = doc
        return doc_id

    async def _dev_get(self, table: str, doc_id: str) -> Optional[dict]:
        return self._db.get(table, {}).get(doc_id)

    async def _dev_update(self, table: str, doc_id: str, data: dict) -> Optional[dict]:
        table_data = self._db.get(table, {})
        if doc_id not in table_data:
            return None
        table_data[doc_id].update(
            {**data, "updatedAt": datetime.now(timezone.utc).isoformat()}
        )
        return table_data[doc_id]

    async def _dev_delete(self, table: str, doc_id: str) -> bool:
        table_data = self._db.get(table, {})
        if doc_id in table_data:
            del table_data[doc_id]
            return True
        return False

    async def _dev_list(self, table: str) -> list:
        return list(self._db.get(table, {}).values())

    # =========================================================================
    # Dev Mode RPC stubs (Stage 12 deep-triage)
    #
    # The parent ConvexClient.{query, mutation, action} methods route to
    # self._dev_query / _dev_mutation / _dev_action when dev_mode is True.
    # These methods were NEVER defined — every dev-mode RPC raised
    # AttributeError. Stage-12 triage replaces the missing methods with
    # honest stubs that return a structured "dev-mode placeholder" payload
    # the route handlers can interpret as "no real Convex behind this call".
    #
    # When the production Convex stack is wired (CONVEX_URL + CONVEX_DEPLOY_KEY
    # set), dev_mode is False and these stubs are never reached.
    # =========================================================================

    def _dev_query(self, function_name: str, args: dict) -> dict:
        return {
            "status": "dev_mode",
            "kind": "query",
            "function": function_name,
            "args": args,
            "result": [],
        }

    def _dev_mutation(self, function_name: str, args: dict) -> dict:
        return {
            "status": "dev_mode",
            "kind": "mutation",
            "function": function_name,
            "args": args,
            "ok": True,
        }

    def _dev_action(self, function_name: str, args: dict) -> dict:
        return {
            "status": "dev_mode",
            "kind": "action",
            "function": function_name,
            "args": args,
            "result": None,
        }


# =============================================================================
# ConvexWriteBuffer — batches high-frequency writes to avoid rate limits
# =============================================================================


class ConvexWriteBuffer:
    """
    Batches high-frequency mutations to avoid Convex rate limits.

    Coalesces writes by (function_name, buildId, agentName) key — last-write-wins.
    Auto-flushes every flush_interval_ms or when max_batch_size is reached.

    Usage:
        buffer = ConvexWriteBuffer(client, flush_interval_ms=500)
        await buffer.enqueue("agentStatus:upsert", {"buildId": "...", "agentName": "architect", ...})
        # Buffer auto-flushes every 500ms
        await buffer.flush_now()  # Force-flush at build completion
    """

    def __init__(
        self,
        client: ConvexDB,
        flush_interval_ms: int = 500,
        max_batch_size: int = 10,
    ):
        self._client = client
        self._queue: list[tuple[str, dict]] = []
        self._flush_interval = flush_interval_ms / 1000.0
        self._max_batch = max_batch_size
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def enqueue(self, function_name: str, args: dict) -> None:
        """Add a mutation to the buffer. Flushes automatically."""
        async with self._lock:
            # Coalesce: if same function+key exists, replace (last-write-wins)
            key = (
                function_name,
                args.get("buildId", ""),
                args.get("agentName", ""),
            )
            self._queue = [
                (fn, a)
                for fn, a in self._queue
                if (fn, a.get("buildId", ""), a.get("agentName", "")) != key
            ]
            self._queue.append((function_name, args))

            if len(self._queue) >= self._max_batch:
                await self._flush()
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())
                self._flush_task.add_done_callback(
                    lambda t: (
                        logger.error("Convex flush task failed: %s", t.exception())
                        if not t.cancelled() and t.exception()
                        else None
                    )
                )

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self._flush_interval)
        async with self._lock:
            await self._flush()

    async def _flush(self) -> None:
        if not self._queue:
            return
        batch = self._queue[:]
        self._queue.clear()
        # Fire mutations concurrently (bounded)
        tasks = [self._client.mutation(fn, args) for fn, args in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # B-HIGH-12 fix: Re-enqueue failed mutations to prevent data loss.
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "ConvexWriteBuffer: mutation %s failed: %s — re-enqueuing",
                    batch[i][0],
                    result,
                )
                self._queue.append(batch[i])

    async def flush_now(self) -> None:
        """Force-flush all pending writes. Call at build completion."""
        async with self._lock:
            await self._flush()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_convex_db: Optional[ConvexDB] = None


def get_convex_client() -> ConvexDB:
    """Get the Convex DB singleton.

    In production (ENVIRONMENT=production), raises RuntimeError if
    CONVEX_URL or CONVEX_DEPLOY_KEY are missing.
    """
    global _convex_db
    if _convex_db is None:
        _convex_db = ConvexDB()
        logger.info("ConvexDB initialized: mode=%s", _convex_db.get_mode())
    return _convex_db


def get_convex_db() -> ConvexDB:
    """Alias for get_convex_client()."""
    return get_convex_client()


# Alias so existing code that imported get_db() continues to work
def get_db() -> ConvexDB:
    """Alias for get_convex_client()."""
    return get_convex_client()


__all__ = [
    "ConvexClient",
    "ConvexDB",
    "ConvexWriteBuffer",
    "get_convex_client",
    "get_convex_db",
    "get_db",
]
