"""
backend/services/app_rpc.py — App-scoped key/value state + RPC bridge.

P4.6 — completes the Sovereign App Runtime with two adjacent layers:

  1. **AppStateStore** — strictly per-app key/value persistence on top
     of the `appState` SQLAlchemy table. The composite PK
     `(appId, key)` is the on-disk isolation boundary; the route
     layer's `_require_matching_app` is the wire boundary; the gate
     check on `app.state.read`/`app.state.write` is the manifest
     boundary. Defense in depth.

  2. **SovereignRPCBridge** — App A invokes App B by spawning B's
     entrypoint inside its own sandbox (via `AppProcessRunner`)
     with the RPC payload injected as env vars + stdin. B's
     stdout is parsed as JSON and returned to A.

Permission grammar (manifest scopes)
------------------------------------
* `app.state.read`  — read OWN state. Cross-app reads are rejected
                      by the route's `_require_matching_app` BEFORE
                      the gate even fires; the scope only controls
                      whether the manifest opted into state reads.
* `app.state.write` — write OWN state. Same isolation as above.

* `rpc.call:<target_app_id>` — invoke a specific target.
* `rpc.call:*`               — wildcard — invoke ANY target.
* `rpc.expose`               — target side: "I accept inbound RPC."

The wildcard `*` is a literal special-case in `_check_rpc_call_scope`
— the existing `PermissionGate._scope_implies` uses dot-prefix
matching, which can't express "any of <set>". We probe wildcard
first (cheap), then the specific target.

Subprocess protocol
-------------------
Target's entrypoint reads:
    VOS3_RPC_METHOD  — caller-supplied method name (str)
    VOS3_RPC_PARAMS  — caller-supplied params (JSON string)
    VOS3_RPC_CALLER  — invoking app's id (str) for audit / quota use

Target MUST print a single JSON object on stdout — the bridge
parses it and returns it verbatim to the caller. A non-JSON stdout
or a non-zero exit is surfaced as an RPC error with the captured
stderr embedded for diagnostics.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from core.database.sqlite_setup import (
    AppState,
    get_session,
    init_db,
)
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppNotFound,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RPCError(RuntimeError):
    """RPC call reached the target subprocess but produced an
    unparsable / non-zero / timed-out result.

    Distinct from `ScopeViolation` / `AppIsolated` / `AppNotFound`,
    which fire BEFORE the spawn. RPCError is the post-spawn surface
    — it carries the captured stderr so the caller can render a
    useful diagnostic without leaking the target's internals."""

    def __init__(self, *, reason: str, returncode: int = 0, stderr: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.returncode = returncode
        self.stderr = stderr

    def to_dict(self) -> dict:
        return {
            "error": "rpc_error",
            "reason": self.reason,
            "returncode": self.returncode,
            "stderr": self.stderr[: 4 * 1024],
        }


# ---------------------------------------------------------------------------
# AppStateStore
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class AppStateEntry:
    app_id: str
    key: str
    value: Any
    updated_at: int

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
        }


class AppStateStore:
    """Per-app key/value persistence.

    The store does NOT consult the PermissionGate — gate enforcement
    is the route handler's job (so we can keep the store usable from
    first-party hooks too). What the store DOES guarantee:
      * Composite-PK isolation: a row is uniquely keyed by (app_id, key).
      * P3.3 sync journal: every write sets `dirty=True`.
      * Atomic upsert semantics inside one transaction.
    """

    # JSON size cap so a runaway app can't fill the state table.
    MAX_VALUE_BYTES = int(64 * 1024)

    def set(self, app_id: str, key: str, value: Any) -> AppStateEntry:
        """Insert or overwrite the (app_id, key) row."""
        self._validate_key(key)
        payload = json.dumps(value, separators=(",", ":"))
        if len(payload.encode("utf-8")) > self.MAX_VALUE_BYTES:
            raise ValueError(f"value JSON exceeds {self.MAX_VALUE_BYTES} bytes")
        init_db()
        now = _now_ms()
        with get_session() as session:
            existing = session.execute(
                select(AppState).where(
                    AppState.appId == app_id,
                    AppState.key == key,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    AppState(
                        appId=app_id,
                        key=key,
                        value_json=payload,
                        updatedAt=now,
                        dirty=True,
                    )
                )
            else:
                existing.value_json = payload
                existing.updatedAt = now
                existing.dirty = True
            session.commit()
        return AppStateEntry(app_id=app_id, key=key, value=value, updated_at=now)

    def get(self, app_id: str, key: str) -> Optional[AppStateEntry]:
        self._validate_key(key)
        with get_session() as session:
            row = session.execute(
                select(AppState).where(
                    AppState.appId == app_id,
                    AppState.key == key,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return AppStateEntry(
                app_id=row.appId,
                key=row.key,
                value=json.loads(row.value_json),
                updated_at=row.updatedAt,
            )

    def delete(self, app_id: str, key: str) -> bool:
        """Return True iff a row was deleted."""
        self._validate_key(key)
        with get_session() as session:
            row = session.execute(
                select(AppState).where(
                    AppState.appId == app_id,
                    AppState.key == key,
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def keys(self, app_id: str) -> list:
        with get_session() as session:
            rows = (
                session.execute(select(AppState.key).where(AppState.appId == app_id))
                .scalars()
                .all()
            )
            return list(rows)

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if "\x00" in key or any(c.isspace() for c in key):
            raise ValueError("key must not contain whitespace or NUL bytes")
        if len(key) > 256:
            raise ValueError("key must be ≤ 256 chars")


APP_STATE_STORE = AppStateStore()


# ---------------------------------------------------------------------------
# SovereignRPCBridge
# ---------------------------------------------------------------------------


def _check_rpc_call_scope(caller_app_id: str, target_app_id: str) -> None:
    """Authorize a caller for invoking `target_app_id`.

    Wildcard first (cheap) then specific. Raises ScopeViolation if
    NEITHER grant is present in the caller's manifest."""
    if PERMISSION_GATE.can(caller_app_id, "rpc.call:*"):
        return
    # `check()` raises ScopeViolation / AppIsolated / AppNotFound on
    # failure — the route handler translates them to HTTP 403.
    PERMISSION_GATE.check(caller_app_id, f"rpc.call:{target_app_id}")


def _check_rpc_expose_scope(target_app_id: str) -> None:
    """Authorize the target for receiving inbound RPC."""
    PERMISSION_GATE.check(target_app_id, "rpc.expose")


@dataclass(frozen=True)
class RPCResult:
    rpc_id: str
    caller_app_id: str
    target_app_id: str
    method: str
    result: Any
    duration_ms: float

    def to_dict(self) -> dict:
        return {
            "rpc_id": self.rpc_id,
            "caller_app_id": self.caller_app_id,
            "target_app_id": self.target_app_id,
            "method": self.method,
            "result": self.result,
            "duration_ms": self.duration_ms,
        }


class SovereignRPCBridge:
    """Request/response bridge between sandboxed apps.

    Stateless except for the injected runner — every `call()`
    re-runs the gate checks, spawns the target's entrypoint, and
    cleans up the ephemeral secret when the subprocess exits.
    """

    def __init__(self, *, runner=None, entrypoint: str = "main.py"):
        self.runner = runner or APP_PROCESS_RUNNER
        self.entrypoint = entrypoint

    async def call(
        self,
        *,
        caller_app_id: str,
        target_app_id: str,
        method: str,
        params: Optional[dict] = None,
        entrypoint: Optional[str] = None,
    ) -> RPCResult:
        """Invoke `method` on `target_app_id` and await JSON result.

        Order of checks (each failure short-circuits the call):
          1. caller_app_id and target_app_id must look like ids
          2. caller's manifest must allow calling target  (rpc.call)
          3. target's manifest must allow being called    (rpc.expose)
          4. target's entrypoint must exist inside its sandbox
          5. subprocess must exit 0 + emit JSON on stdout

        Returns RPCResult on success.

        Raises:
          ScopeViolation / AppIsolated / AppNotFound — pre-spawn auth
            failures, translated to HTTP 403 at the route layer.
          ValueError — malformed `method` / `params`.
          RPCError   — post-spawn failure (non-JSON output, non-zero
            exit, timeout, etc.).
        """
        if not isinstance(method, str) or not method.strip():
            raise ValueError("method must be a non-empty string")
        if any(c.isspace() for c in method):
            raise ValueError("method must not contain whitespace")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if caller_app_id == target_app_id:
            raise ValueError("self-RPC not supported (call your own code directly)")

        # Auth — caller side first (cheaper than the target probe).
        _check_rpc_call_scope(caller_app_id, target_app_id)
        _check_rpc_expose_scope(target_app_id)

        # Ensure the target actually exists in the registry — the
        # gate check above would catch a totally unknown id, but be
        # explicit for the error surface.
        if SANDBOX_MANAGER.get(target_app_id) is None:
            raise AppNotFound(f"target app_id={target_app_id!r}")

        params_json = json.dumps(params, separators=(",", ":"))
        rpc_id = str(uuid.uuid4())
        env_override = {
            "VOS3_RPC_ID": rpc_id,
            "VOS3_RPC_METHOD": method,
            "VOS3_RPC_PARAMS": params_json,
            "VOS3_RPC_CALLER": caller_app_id,
        }

        start = time.monotonic()
        # Spawn the target. Note: this runs INSIDE the target's
        # sandbox (cwd=target's storage, manifest's scopes apply via
        # AppProcessRunner.gate_check_or_raise("process.execute")).
        # If the target doesn't have process.execute, the runner
        # raises ScopeViolation — same 403 surface.
        result = await self.runner.run_entrypoint(
            target_app_id,
            env_override=env_override,
            entrypoint=entrypoint or self.entrypoint,
        )
        duration_ms = (time.monotonic() - start) * 1000.0

        if result.timed_out:
            raise RPCError(
                reason="target subprocess timed out",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        if result.returncode != 0:
            raise RPCError(
                reason=f"target exited with code {result.returncode}",
                returncode=result.returncode,
                stderr=result.stderr,
            )

        stdout_stripped = (result.stdout or "").strip()
        if not stdout_stripped:
            raise RPCError(
                reason="target produced empty stdout",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        try:
            payload = json.loads(stdout_stripped)
        except json.JSONDecodeError as exc:
            raise RPCError(
                reason=f"target stdout is not valid JSON: {exc}",
                returncode=result.returncode,
                stderr=result.stderr,
            ) from exc

        logger.info(
            "[rpc] caller=%s → target=%s method=%s rpc_id=%s duration_ms=%.1f",
            caller_app_id,
            target_app_id,
            method,
            rpc_id,
            duration_ms,
        )
        return RPCResult(
            rpc_id=rpc_id,
            caller_app_id=caller_app_id,
            target_app_id=target_app_id,
            method=method,
            result=payload,
            duration_ms=duration_ms,
        )


RPC_BRIDGE = SovereignRPCBridge()


__all__ = [
    "APP_STATE_STORE",
    "AppStateEntry",
    "AppStateStore",
    "RPC_BRIDGE",
    "RPCError",
    "RPCResult",
    "SovereignRPCBridge",
]
