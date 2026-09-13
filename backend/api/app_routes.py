"""
backend/api/app_routes.py — Sovereign App Runtime HTTP surface.

P4.1 — exposes the lifecycle operations of `AppSandboxManager` over
HTTP so the desktop UI can install / inspect / isolate third-party
apps without reaching into the service layer directly.

Endpoints
---------
  POST   /api/apps/install        → validate manifest, persist, return id
  GET    /api/apps                → list installed apps
  GET    /api/apps/{app_id}       → read one app (manifest + status)
  POST   /api/apps/{app_id}/isolate     → freeze the app
  POST   /api/apps/{app_id}/reactivate  → unfreeze (status → 'active')
  DELETE /api/apps/{app_id}       → uninstall (drops the row + gate cache)

All endpoints require Bearer auth (no public install path — operator
intent is mandatory before a third-party manifest takes effect).

Validation contract
-------------------
`POST /api/apps/install` rejects:
  * Missing or non-string `name` / `version`
  * `scopes` or `restrictions` containing non-strings or whitespace
  * Any malformed JSON in the request body

A rejected manifest never touches the `apps` table — the sandbox
manager raises `InvalidManifest` before the INSERT, and the route
translates that into HTTP 400.

What this route deliberately does NOT do
----------------------------------------
* It does NOT execute app code. Execution lives in
  `ProcessSandbox.execute()` (and a future Phase 4.x runtime).
* It does NOT trust the caller's claim about scope grants. The
  manifest's `scopes` list is treated as a *request* — vOS core
  services still consult `PERMISSION_GATE.check()` before honoring
  any app-originated action, so a manifest claiming
  `scopes: ["llm.cloud"]` only unlocks cloud LLM if the LLM
  dispatcher's gate check passes (and is not blocked by a
  `restrictions: ["network.blocked"]`).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import AuthenticatedUser, get_current_user
from middleware.auth import get_app_context
from services.app_filesystem import (
    APP_FILESYSTEM_SERVICE,
    FILESYSTEM_MANAGER,
    PathTraversalAttempt,
)
from services.app_media import MEDIA_SERVICE, MAX_IMAGE_BYTES
from services.app_rpc import (
    APP_STATE_STORE,
    RPC_BRIDGE,
    RPCError,
)
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    AppIsolated,
    AppNotFound,
    InvalidManifest,
    SANDBOX_MANAGER,
    ScopeViolation,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/apps", tags=["Apps"])


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


@router.post("/install")
async def install_app(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Install a third-party app from a manifest.

    Request body:
      {
        "manifest": {
          "name": "...",
          "version": "...",
          "scopes":       ["filesystem.read", "llm.local", ...],
          "restrictions": ["network.blocked"]    # optional
        },
        "app_id": "..."   # optional; defaults to a fresh UUID4
      }

    Response:
      { "app_id": "<uuid>", "status": "active", "name": ..., "version": ... }

    Status codes:
      200 — manifest accepted, app row persisted, gate primed.
      400 — manifest validation failed (see `detail.error`).
      401 — missing / invalid bearer token (enforced by Depends).
    """
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "manifest object required at body.manifest"},
        )
    app_id = payload.get("app_id") if isinstance(payload, dict) else None
    if app_id is not None and not isinstance(app_id, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "app_id must be a string when provided"},
        )

    try:
        result = SANDBOX_MANAGER.install(manifest, app_id=app_id)
    except InvalidManifest as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_manifest", "reason": str(exc)},
        )

    new_id = result["app_id"]
    secret = result.get("secret")  # plaintext, only on first install
    record = SANDBOX_MANAGER.get(new_id) or {}
    logger.info(
        "[apps] %s installed app id=%s name=%s version=%s",
        user.id,
        new_id,
        record.get("name"),
        record.get("version"),
    )
    body: dict[str, Any] = {
        "app_id": new_id,
        "status": record.get("status", "active"),
        "name": record.get("name"),
        "version": record.get("version"),
        "manifest": record.get("manifest"),
    }
    if secret:
        # P4.2 — surfaced exactly once. The app must capture this and
        # send it back as the X-App-Secret header on subsequent calls.
        # Subsequent GET /api/apps/{id} reads NEVER expose the secret.
        body["secret"] = secret
    return body


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("")
async def list_apps(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Return every installed app for the operator's device.

    The list isn't user-scoped — apps are installed at the device
    level (the manifest's `scopes` are the only authorization the
    runtime cares about). Auth on the read endpoint is purely an
    anti-enumeration measure."""
    rows = SANDBOX_MANAGER.list_apps()
    return {"apps": rows, "count": len(rows)}


@router.get("/{app_id}")
async def get_app(
    app_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    record = SANDBOX_MANAGER.get(app_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "app_not_found", "app_id": app_id},
        )
    return record


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


@router.post("/{app_id}/scopes/toggle")
async def toggle_scope(
    app_id: str,
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Operator-driven scope toggle.

    Body: { "scope": "filesystem.read", "granted": false }

    Dashboard-facing. The route is BEARER-auth-gated (operator
    identity, not app identity) because the operator is editing
    the app's manifest. The `granted=False` direction REVOKES a
    capability at runtime; the gate cache is evicted by
    `SANDBOX_MANAGER.toggle_scope()` so the next `check()` reads
    the freshly-edited manifest immediately.
    """
    scope = payload.get("scope") if isinstance(payload, dict) else None
    granted = payload.get("granted") if isinstance(payload, dict) else None
    if not isinstance(scope, str) or not scope.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "scope required"},
        )
    if not isinstance(granted, bool):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "granted must be a boolean"},
        )
    try:
        return SANDBOX_MANAGER.toggle_scope(
            app_id,
            scope=scope.strip(),
            granted=granted,
        )
    except AppNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "app_not_found", "app_id": app_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc


@router.post("/{app_id}/isolate")
async def isolate_app(
    app_id: str,
    payload: dict | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Freeze the app — status → 'isolated', every gate check denies."""
    reason = "operator-initiated"
    if isinstance(payload, dict) and isinstance(payload.get("reason"), str):
        reason = payload["reason"]
    try:
        SANDBOX_MANAGER.isolate(app_id, reason=reason)
    except AppNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "app_not_found", "app_id": app_id},
        ) from exc
    return {"app_id": app_id, "status": "isolated", "reason": reason}


@router.post("/{app_id}/reactivate")
async def reactivate_app(
    app_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        SANDBOX_MANAGER.reactivate(app_id)
    except AppNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "app_not_found", "app_id": app_id},
        ) from exc
    return {"app_id": app_id, "status": "active"}


@router.delete("/{app_id}")
async def uninstall_app(
    app_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    SANDBOX_MANAGER.uninstall(app_id)
    return {"app_id": app_id, "status": "uninstalled"}


# ---------------------------------------------------------------------------
# P4.3 — sandboxed filesystem
# ---------------------------------------------------------------------------


def _require_matching_app(authed_app_id: Optional[str], url_app_id: str) -> str:
    """Reject calls where the URL's app_id doesn't match the
    authenticated `X-App-Id`.

    Without this check, an app A with a valid secret could call
    `/api/apps/B/fs/read` and use A's credentials to operate on
    B's sandbox. The dependency `get_app_context` already verified
    the secret matches the URL's id; this is the cross-check
    that ties the two together.
    """
    if not authed_app_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "app_context_required",
                "reason": "X-App-Id + X-App-Secret headers required",
            },
        )
    if authed_app_id != url_app_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "app_id_mismatch",
                "reason": "URL app_id does not match the authenticated app",
            },
        )
    return authed_app_id


@router.post("/{app_id}/fs/write")
async def fs_write(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Write a single file inside the app's sandbox.

    Body:
      { "path": "<relative path inside the sandbox>",
        "content": "<utf-8 string>" }

    Headers (validated by `get_app_context`):
      X-App-Id      — must match the path's {app_id}.
      X-App-Secret  — plaintext secret minted at install time.

    Status codes:
      200 — write succeeded.
      400 — payload malformed OR path is malformed (e.g. NUL bytes,
            content too large).
      403 — gate denied (`filesystem.write` not granted, app
            isolated, secret/id mismatch, OR the path resolves
            OUTSIDE the sandbox (PathTraversalAttempt)).
    """
    _require_matching_app(authed, app_id)

    relative = payload.get("path") if isinstance(payload, dict) else None
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(relative, str) or not relative:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body.path required"},
        )
    if not isinstance(content, (str, bytes)):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body.content must be a string"},
        )

    try:
        return FILESYSTEM_MANAGER.write_app_file(app_id, relative, content)
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc


@router.get("/{app_id}/fs/list")
async def fs_list(
    app_id: str,
    path: str = "",
    recursive: bool = False,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """List entries inside the app's sandbox directory.

    Query parameters:
      path       — relative path inside the sandbox; "" lists the root.
      recursive  — when true, walks subdirectories (bounded to 1000
                   entries / 8 levels deep).

    Status codes:
      200 — `{"app_id", "path", "recursive", "entries": [...]}`.
      400 — path malformed.
      403 — X-App-Id mismatch / scope missing / traversal (the
            resolver auto-isolates on traversal, see P4.3).
      404 — path is inside sandbox but not a directory / missing.
    """
    _require_matching_app(authed, app_id)
    try:
        entries = APP_FILESYSTEM_SERVICE.list_dir(
            app_id,
            path,
            recursive=bool(recursive),
        )
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "path": path, "reason": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc

    return {
        "app_id": app_id,
        "path": path,
        "recursive": bool(recursive),
        "count": len(entries),
        "entries": entries,
    }


@router.get("/{app_id}/fs/read")
async def fs_read(
    app_id: str,
    path: str,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Read a file from the app's sandbox.

    Query string:
      ?path=<relative path inside the sandbox>

    Status codes:
      200 — `{"app_id", "path", "content"}` with utf-8 decoded content.
      400 — path malformed.
      403 — gate denied OR PathTraversalAttempt.
      404 — file not found inside the sandbox.
    """
    _require_matching_app(authed, app_id)

    try:
        content = FILESYSTEM_MANAGER.read_app_file(app_id, path)
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "path": path},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc

    return {"app_id": app_id, "path": path, "content": content}


# ---------------------------------------------------------------------------
# P4.3 — directive-shaped filesystem endpoints (state/file)
#
# These mirror the existing `/fs/read` + `/fs/write` routes but use
# the literal path shape from the P4.3 directive. Both surfaces share
# the same SovereignFilesystemManager underneath — the directive's
# scope-then-confinement-then-auto-isolate semantics applies
# regardless of which path the caller hits.
# ---------------------------------------------------------------------------


@router.get("/{app_id}/state/file")
async def state_file_read(
    app_id: str,
    path: str,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Read a file from the app's sandbox.

    Query: ?path=<relative path inside the sandbox>
    """
    _require_matching_app(authed, app_id)
    try:
        content = APP_FILESYSTEM_SERVICE.read_file(app_id, path)
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "path": path},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc
    return {"app_id": app_id, "path": path, "content": content}


@router.post("/{app_id}/state/file")
async def state_file_write(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Write a file into the app's sandbox.

    Body: { "path": "<relative>", "content": "<utf-8 string>" }
    """
    _require_matching_app(authed, app_id)
    relative = payload.get("path") if isinstance(payload, dict) else None
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(relative, str) or not relative:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body.path required"},
        )
    if not isinstance(content, (str, bytes)):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body.content must be a string"},
        )
    try:
        return APP_FILESYSTEM_SERVICE.write_file(app_id, relative, content)
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc


# ---------------------------------------------------------------------------
# P5.6 — sandboxed multi-modal pipeline (image gen + vision/OCR)
# ---------------------------------------------------------------------------


@router.post("/{app_id}/media/generate")
async def media_generate(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Generate an image inside the app's sandbox.

    Body:
      {
        "prompt":      "<text>",
        "resolution":  [width, height]   # optional, defaults [256, 256]
      }

    Status codes:
      200 — `ImageGenerationResult.to_dict()` (carries output_path
            inside the sandbox + backend tier used).
      400 — body malformed.
      403 — gate denied (`media.write` missing OR app isolated).
    """
    _require_matching_app(authed, app_id)
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    resolution = payload.get("resolution") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "prompt required"},
        )
    res_tuple: Optional[tuple] = None
    if resolution is not None:
        if (
            not isinstance(resolution, (list, tuple))
            or len(resolution) != 2
            or not all(isinstance(v, int) for v in resolution)
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "reason": "resolution must be [width, height] ints",
                },
            )
        res_tuple = (int(resolution[0]), int(resolution[1]))

    try:
        result = MEDIA_SERVICE.generate_image(
            app_id=app_id,
            prompt=prompt,
            resolution=res_tuple,
        )
    except PathTraversalAttempt as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc

    return result.to_dict()


@router.post("/{app_id}/media/process")
async def media_process(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Run OCR / vision on inline image bytes.

    Body:
      {
        "image_base64": "<base64 PNG/JPEG bytes>",
        "task":         "ocr" | "describe" | "classify"   # default "ocr"
      }

    Image bytes are bounded by VOS3_APP_MEDIA_MAX_BYTES (10 MB by
    default) so a runaway client can't OOM the worker.

    Status codes:
      200 — `VisionResult.to_dict()` with transcript + backend tier.
      400 — body malformed / image too large / unknown task.
      403 — gate denied (`media.read` missing).
    """
    _require_matching_app(authed, app_id)
    b64 = payload.get("image_base64") if isinstance(payload, dict) else None
    task = (payload.get("task") if isinstance(payload, dict) else None) or "ocr"
    if not isinstance(b64, str) or not b64:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "image_base64 required"},
        )
    if not isinstance(task, str):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "task must be a string"},
        )

    # Decode + size-check BEFORE handing to the service so the route
    # layer rejects oversized payloads with a clean 400.
    try:
        import base64 as _b64

        image_bytes = _b64.b64decode(b64, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bad_request",
                "reason": f"image_base64 not valid base64: {exc}",
            },
        ) from exc
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "bad_request",
                "reason": f"image exceeds {MAX_IMAGE_BYTES} bytes",
            },
        )

    try:
        result = MEDIA_SERVICE.process_vision(
            app_id=app_id,
            image_bytes=image_bytes,
            task=task,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc

    return result.to_dict()


# ---------------------------------------------------------------------------
# P4.4 — sandboxed app execution
# ---------------------------------------------------------------------------


@router.post("/{app_id}/execute")
async def execute_app(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Spawn the app's entrypoint inside the hardened subprocess runner.

    Body:
      {
        "args":          ["arg1", "arg2"],          # optional, list of str
        "env_override":  {"VAR": "value"},          # optional, str→str
        "entrypoint":    "main.py"                  # optional, default
      }

    Headers (validated by get_app_context):
      X-App-Id      — must equal the URL's {app_id}.
      X-App-Secret  — install-time secret OR an ephemeral run secret
                      previously minted by a parent execution.

    Status codes:
      200 — process spawned. Returns
            {"app_id", "returncode", "stdout", "stderr",
             "duration_ms", "timed_out", "killed_by_limit"}.
      400 — body args / env_override / entrypoint malformed,
            or no entrypoint exists inside the sandbox.
      403 — gate denied (`process.execute` missing, app isolated,
            secret/id mismatch, OR entrypoint resolves outside
            the sandbox).
      404 — same as 400 when the entrypoint file is missing
            (kept distinct for UX).
    """
    _require_matching_app(authed, app_id)

    args = payload.get("args") if isinstance(payload, dict) else None
    env_override = payload.get("env_override") if isinstance(payload, dict) else None
    entrypoint = payload.get("entrypoint") if isinstance(payload, dict) else None

    if args is not None and not isinstance(args, list):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "args must be a list"},
        )
    if env_override is not None and not isinstance(env_override, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "env_override must be an object"},
        )
    if entrypoint is not None and not isinstance(entrypoint, str):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "entrypoint must be a string"},
        )

    try:
        result = await APP_PROCESS_RUNNER.run_entrypoint(
            app_id,
            args=args,
            env_override=env_override,
            entrypoint=entrypoint,
        )
    except ScopeViolation as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized", "reason": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "entrypoint_missing", "reason": str(exc)},
        ) from exc
    except PathTraversalAttempt as exc:
        # P4.3 — explicit catch since PathTraversalAttempt now
        # inherits from PermissionError, not ValueError. The
        # resolver also auto-isolates the app at this point so
        # subsequent calls get a clean 403 via get_app_context.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "path_traversal_attempt",
                "app_id": exc.app_id,
                "requested_path": exc.requested_path,
                "auto_isolated": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc

    return result.to_dict()


# ---------------------------------------------------------------------------
# P4.6 — App-scoped key/value state
# ---------------------------------------------------------------------------


@router.post("/{app_id}/state/set")
async def state_set(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Write a single (key, value) into the app's isolated state space.

    Body: { "key": "<str>", "value": <JSON-serializable> }

    `value` may be any JSON-serializable type. Strings up to 64 KB
    of serialized JSON are accepted; oversized payloads return 400.

    Status codes:
      200 — wrote successfully. Returns the AppStateEntry.
      400 — key/value malformed.
      403 — X-App-Id mismatch OR `app.state.write` missing.
    """
    caller = _require_matching_app(authed, app_id)
    # Gate check — even though the route's id match already ensures
    # an app can only touch its OWN state, the scope still controls
    # whether the manifest opted into state writes.
    try:
        from services.app_sandbox import PERMISSION_GATE

        PERMISSION_GATE.check(caller, "app.state.write")
    except ScopeViolation as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized", "reason": str(exc)},
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body must be an object"},
        )
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "key required"},
        )
    if "value" not in payload:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "value required"},
        )

    try:
        entry = APP_STATE_STORE.set(caller, key, payload["value"])
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc
    return entry.to_dict()


@router.get("/{app_id}/state/get")
async def state_get(
    app_id: str,
    key: str,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Read a single key from the app's isolated state space.

    Query: ?key=<str>

    Status codes:
      200 — found. Returns the AppStateEntry.
      400 — key malformed.
      403 — X-App-Id mismatch OR `app.state.read` missing.
      404 — key not present in the app's state space.
    """
    caller = _require_matching_app(authed, app_id)
    try:
        from services.app_sandbox import PERMISSION_GATE

        PERMISSION_GATE.check(caller, "app.state.read")
    except ScopeViolation as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized", "reason": str(exc)},
        ) from exc

    try:
        entry = APP_STATE_STORE.get(caller, key)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "key": key},
        )
    return entry.to_dict()


# ---------------------------------------------------------------------------
# P4.6 — Inter-app RPC bridge
# ---------------------------------------------------------------------------


@router.post("/{app_id}/rpc/call")
async def rpc_call(
    app_id: str,
    payload: dict,
    authed: Optional[str] = Depends(get_app_context),
) -> dict[str, Any]:
    """Invoke a method on another app and return its JSON response.

    Body:
      {
        "target_app_id": "<callee uuid>",
        "method":        "<str>",
        "params":        { ... optional JSON object ... }
      }

    Caller scope: `rpc.call:<target_app_id>` OR `rpc.call:*`.
    Callee scope: `rpc.expose` (asserted before spawning).

    Subprocess protocol — the callee's entrypoint reads four env vars:
      VOS3_RPC_ID, VOS3_RPC_METHOD, VOS3_RPC_PARAMS, VOS3_RPC_CALLER
    and prints a single JSON object on stdout. The bridge parses it
    and returns it to the caller verbatim.

    Status codes:
      200 — RPC completed. Returns the bridge's RPCResult dict.
      400 — body malformed.
      403 — auth/scope failure on caller OR callee side.
      404 — target app not installed.
      502 — RPC reached the callee but produced an unparseable /
            non-zero result. Body includes the captured stderr.
    """
    caller = _require_matching_app(authed, app_id)

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "body must be an object"},
        )
    target_app_id = payload.get("target_app_id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if not isinstance(target_app_id, str) or not target_app_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "target_app_id required"},
        )
    if not isinstance(method, str) or not method:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": "method required"},
        )

    try:
        result = await RPC_BRIDGE.call(
            caller_app_id=caller,
            target_app_id=target_app_id,
            method=method,
            params=params,
        )
    except ScopeViolation as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except AppNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "target_not_found", "reason": str(exc)},
        ) from exc
    except AppIsolated as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized", "reason": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc
    except RPCError as exc:
        raise HTTPException(
            status_code=502,
            detail=exc.to_dict(),
        ) from exc

    return result.to_dict()


__all__ = ["router"]
