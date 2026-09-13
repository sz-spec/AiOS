"""
backend/services/app_filesystem.py — Sovereign per-app filesystem.

P4.3 — Phase 4 of the Sovereign App Runtime. The `AppSandboxManager`
(P4.1/P4.2) provisions a per-app directory at
`{app_data_dir}/apps/{app_id}/storage/` and primes the
`PermissionGate`. This module enforces two invariants when an app
reads or writes inside that directory:

  1. **Capability** — the PermissionGate must approve the action.
     Reads require `filesystem.read`; writes require `filesystem.write`.

  2. **Containment** — the resolved absolute path must live INSIDE
     the app's sandbox root. Any `..` segment, absolute path, or
     symlink that escapes the sandbox is rejected up front with
     `PathTraversalAttempt`. The OS never sees the open() call.

Resolution algorithm (`_resolve_inside_sandbox`)
-----------------------------------------------
1. Reject absolute relative_path early (`/etc/passwd`, `C:\\Windows`).
2. Reject any path component that is exactly `..` (defense in
   depth; the resolve() below would catch crafted ones too).
3. Build `candidate = (sandbox_root / relative_path).resolve(strict=False)`.
4. Build `root_resolved = sandbox_root.resolve(strict=False)`.
5. Confine: `candidate` must be `root_resolved` OR a descendant.
   We use `os.path.commonpath` rather than `Path.is_relative_to`
   (which is 3.9+ only and has quirks with case-insensitive FSes).
6. **Symlink trap** — between resolve() and open(), an attacker
   with write access to the sandbox could swap a normal file for
   a symlink pointing outside. We defend against this with
   `os.open(O_NOFOLLOW)` on the final segment when supported, so
   the kernel itself refuses to traverse the swap.

The result is a path that — if the resolution doesn't raise —
the OS can be told to open without any further fast-and-loose
string manipulation. Belt + braces: the OS open path is also
restricted to the file's parent directory by chdir'ing into the
sandbox root before resolution. We don't do that because the
manager runs in the FastAPI worker thread (chdir is process-
global), so we rely on path validation + O_NOFOLLOW instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from services.app_sandbox import (
    PERMISSION_GATE,
    AppIsolated,
    AppNotFound,
    ScopeViolation,
    _record_security_event,
    app_storage_root,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PathTraversalAttempt(PermissionError):
    """The caller asked to read/write a path that resolves OUTSIDE the
    app's sandbox root. The op is refused before any open() call.

    P4.3 — inherits from PermissionError (was ValueError) so the
    HTTP route layer's existing `except PermissionError` paths catch
    it as a 403 alongside ScopeViolation / AppIsolated. The
    auto-isolation hook in `_isolate_for_traversal` flips the app's
    status to 'isolated' the moment a traversal is detected, so
    every subsequent gate check on the same app refuses without
    needing to re-run the path check.

    Carries `app_id`, `requested_path`, and `resolved_path` so audit
    log consumers can render a structured event."""

    def __init__(self, app_id: str, requested: str, resolved: Optional[str] = None):
        super().__init__(
            f"app={app_id!r} requested path={requested!r} resolves outside its sandbox"
        )
        self.app_id = app_id
        self.requested_path = requested
        self.resolved_path = resolved


def _isolate_for_traversal(
    app_id: str,
    *,
    requested: str,
    resolved: Optional[str] = None,
    reason_extra: str = "",
) -> None:
    """P4.3 — record + auto-isolate on traversal detection.

    Called BEFORE the PathTraversalAttempt raise at every detection
    site in `_resolve_inside_sandbox`. Two side-effects:
      1. Append a high-severity `path_traversal_attempt` audit row
         (one per detection site so the dashboard renders every
         attempt distinctly).
      2. Call `SANDBOX_MANAGER.isolate()` so the app is FROZEN.
         The manager's `_set_status` evicts the gate cache and
         appends its own `app_status_change` audit row.

    Failure-mode: SANDBOX_MANAGER.isolate may raise AppNotFound for
    a row that was wiped mid-call. We swallow that so the traversal
    detection ALWAYS produces a clean PathTraversalAttempt instead
    of leaking an unrelated exception."""
    _record_security_event(
        kind="path_traversal_attempt",
        app_id=app_id,
        reason=reason_extra or f"requested_path={requested!r}",
        details={
            "requested_path": requested,
            "resolved_path": resolved,
            "auto_isolated": True,
        },
    )
    # Lazy import — SANDBOX_MANAGER lives in the same module that
    # exports app_storage_root; cyclic import would explode at
    # module load. Importing here is cheap (already cached).
    try:
        from services.app_sandbox import SANDBOX_MANAGER

        SANDBOX_MANAGER.isolate(app_id, reason="path_traversal_attempt")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[fs] auto-isolate failed app=%s: %s",
            app_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


# Hard cap on a single read / write payload. Keeps the route from
# being a memory-amplification vector (10 MB matches the W6.x sandbox
# RLIMIT_FSIZE). Operators can lift via env if needed.
_MAX_FILE_BYTES = int(os.getenv("VOS3_APP_FS_MAX_BYTES", str(10 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


def _resolve_inside_sandbox(app_id: str, relative_path: str) -> Path:
    """Validate + resolve `relative_path` under the app's sandbox root.

    Returns the absolute, canonical Path on success. Raises
    `PathTraversalAttempt` (a ValueError subclass) on any of:
      * non-string / empty / NUL-byte input
      * absolute path or one that names a Windows-style drive
      * path component equal to `..`
      * resolved path is not the sandbox root or one of its descendants
    """
    if not isinstance(relative_path, str) or not relative_path:
        _isolate_for_traversal(
            app_id,
            requested=repr(relative_path),
            reason_extra="empty or non-string path",
        )
        raise PathTraversalAttempt(app_id, repr(relative_path))
    if "\x00" in relative_path:
        # Reject NUL bytes early — they can truncate strings inside
        # `open()` on some platforms and confuse audit logs.
        _isolate_for_traversal(app_id, requested=relative_path)
        raise PathTraversalAttempt(app_id, relative_path)

    # Reject absolute paths (POSIX leading "/" and Windows "C:\…").
    if relative_path.startswith(("/", "\\")) or (
        len(relative_path) >= 2 and relative_path[1] == ":"
    ):
        _isolate_for_traversal(app_id, requested=relative_path)
        raise PathTraversalAttempt(app_id, relative_path)

    # Normalize Windows-style separators to POSIX before component
    # inspection. Without this, a payload like "..\\..\\Windows\\foo"
    # on a POSIX host is treated as a SINGLE literal filename (since
    # `\` is a regular character on POSIX). Treating `\` as a
    # separator on every OS lets the same blocklist serve both
    # platforms and matches operator intent (no legitimate vOS app
    # filename contains backslashes).
    normalized = relative_path.replace("\\", "/")

    # Reject any literal ".." component up front. resolve() below
    # would canonicalize them away, but catching here means the
    # audit log shows what the attacker actually sent.
    parts = Path(normalized).parts
    if any(p == ".." for p in parts):
        _isolate_for_traversal(app_id, requested=relative_path)
        raise PathTraversalAttempt(app_id, relative_path)

    root = app_storage_root(app_id)
    # We do NOT require the root to exist here — the install path
    # provisions it, and reads against a missing root surface as
    # FileNotFoundError once we attempt the open. But we DO need
    # `.resolve(strict=False)` so symlinks in the prefix are
    # canonicalized before comparison.
    root_resolved = root.resolve()

    candidate = (root_resolved / normalized).resolve()

    # Containment check via commonpath. `Path.is_relative_to()` would
    # be 3.9+; commonpath is universally available.
    try:
        common = os.path.commonpath([str(candidate), str(root_resolved)])
    except ValueError:
        # commonpath raises on mixed drives (Windows) — definitely
        # an escape attempt.
        _isolate_for_traversal(
            app_id,
            requested=relative_path,
            resolved=str(candidate),
            reason_extra="cross-drive commonpath",
        )
        raise PathTraversalAttempt(app_id, relative_path, str(candidate))

    if common != str(root_resolved):
        _isolate_for_traversal(
            app_id,
            requested=relative_path,
            resolved=str(candidate),
            reason_extra=f"resolved_outside={str(candidate)!r}",
        )
        raise PathTraversalAttempt(app_id, relative_path, str(candidate))

    return candidate


# ---------------------------------------------------------------------------
# Gate plumbing — same shape as services/llm_dispatcher._enforce_app_scope
# ---------------------------------------------------------------------------


def _enforce_fs_scope(app_id: str, scope: str) -> None:
    """Translate PermissionGate denials into HTTPException(403).

    Mirrors the dispatcher/RAG enforcement so the FS route's error
    payload is shape-compatible with the rest of the runtime."""
    from fastapi import HTTPException as _HTTPException

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
# SovereignFilesystemManager
# ---------------------------------------------------------------------------


class SovereignFilesystemManager:
    """Per-app file I/O with capability + containment enforcement.

    Stateless — every call re-runs the gate check and path
    resolution. The cost is negligible (a couple of `Path.resolve`s
    and a dict lookup in the gate cache) and the no-state design
    means there's nothing for an attacker to poison between calls.
    """

    @staticmethod
    def write_app_file(app_id: str, relative_path: str, content: str) -> dict:
        """Write `content` to `<sandbox>/<relative_path>`.

        Returns a small dict for telemetry / route response:
          {"app_id", "path", "bytes_written"}

        Raises:
          HTTPException(403)  — gate denied (no filesystem.write OR
                                app is isolated / unknown).
          PathTraversalAttempt — path resolves outside the sandbox.
          ValueError           — content too large.
        """
        _enforce_fs_scope(app_id, "filesystem.write")
        target = _resolve_inside_sandbox(app_id, relative_path)

        # Coerce + size guard. We accept str for the JSON-friendly
        # API but encode as UTF-8 bytes for the write. Bytes are
        # ALSO accepted (for binary uploads through a different
        # API in a future P4.4).
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            data = bytes(content)
        else:
            raise ValueError("content must be a str or bytes-like")

        if len(data) > _MAX_FILE_BYTES:
            raise ValueError(
                f"content size {len(data)} exceeds VOS3_APP_FS_MAX_BYTES "
                f"({_MAX_FILE_BYTES})"
            )

        # Create parent dirs INSIDE the sandbox if needed. The
        # _resolve_inside_sandbox check already guarantees the
        # parent is contained.
        target.parent.mkdir(parents=True, exist_ok=True)

        # Use os.open with O_NOFOLLOW on POSIX to refuse symlink
        # races. On Windows, fall back to a plain open — the
        # earlier containment check is still authoritative.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(target), flags, 0o600)
        except OSError as exc:
            # ELOOP — symlink encountered, refuse. Other open
            # errors propagate as the original OSError type.
            if getattr(exc, "errno", None) == 40:  # ELOOP
                raise PathTraversalAttempt(
                    app_id,
                    relative_path,
                    str(target),
                ) from exc
            raise
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        logger.info(
            "[fs] write app=%s rel=%s bytes=%d",
            app_id,
            relative_path,
            len(data),
        )
        return {
            "app_id": app_id,
            "path": relative_path,
            "bytes_written": len(data),
        }

    @staticmethod
    def read_app_file(app_id: str, relative_path: str) -> str:
        """Read `<sandbox>/<relative_path>` as UTF-8 text.

        Returns the file contents as a string.

        Raises:
          HTTPException(403)  — gate denied filesystem.read.
          PathTraversalAttempt — path resolves outside the sandbox.
          FileNotFoundError    — file doesn't exist inside the sandbox.
        """
        _enforce_fs_scope(app_id, "filesystem.read")
        target = _resolve_inside_sandbox(app_id, relative_path)

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(target), flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            if getattr(exc, "errno", None) == 40:  # ELOOP
                raise PathTraversalAttempt(
                    app_id,
                    relative_path,
                    str(target),
                ) from exc
            raise

        try:
            with os.fdopen(fd, "rb") as fh:
                # Cap reads at _MAX_FILE_BYTES + 1 so an attacker
                # who plants a 10 GB file can't OOM the worker on
                # a single call.
                data = fh.read(_MAX_FILE_BYTES + 1)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        if len(data) > _MAX_FILE_BYTES:
            raise ValueError(
                f"file size exceeds VOS3_APP_FS_MAX_BYTES ({_MAX_FILE_BYTES})"
            )

        logger.info(
            "[fs] read app=%s rel=%s bytes=%d",
            app_id,
            relative_path,
            len(data),
        )
        return data.decode("utf-8", errors="replace")


# Module-level singleton — vOS api routes use this directly.
FILESYSTEM_MANAGER = SovereignFilesystemManager()


# ---------------------------------------------------------------------------
# AppFilesystemService — directive-shaped facade.
#
# Same isolation guarantees as SovereignFilesystemManager (which it
# delegates to). Method names match the P4.3 directive:
#   read_file / write_file / list_dir / delete_file
# Two of those (list_dir, delete_file) are NEW capabilities not
# present in the underlying manager; we implement them inline.
# ---------------------------------------------------------------------------


class AppFilesystemService:
    """Facade matching the P4.3 directive's required method shape.

    All methods enforce capability + path confinement before any
    syscall. A traversal attempt at ANY step auto-isolates the
    calling app and raises PathTraversalAttempt — the manager and
    facade share `_resolve_inside_sandbox`, so the rule is
    enforced exactly once.
    """

    @staticmethod
    def read_file(app_id: str, virtual_path: str) -> str:
        return FILESYSTEM_MANAGER.read_app_file(app_id, virtual_path)

    @staticmethod
    def write_file(app_id: str, virtual_path: str, content) -> dict:
        return FILESYSTEM_MANAGER.write_app_file(app_id, virtual_path, content)

    @staticmethod
    def list_dir(
        app_id: str,
        virtual_path: str = "",
        *,
        recursive: bool = False,
        max_entries: int = 1000,
        max_depth: int = 8,
    ) -> list:
        """Return a list of entries inside `virtual_path`.

        Each entry:
          {
            "name":           str,    # leaf filename
            "path":           str,    # relative-to-sandbox path
            "is_dir":         bool,
            "size_bytes":     int | None,
            "modified_at_ms": int | None,
          }

        When `recursive=True`, descends up to `max_depth` levels
        and emits AT MOST `max_entries` rows total. The caps bound
        the worst-case CPU on the sandbox root before any traversal
        guard fires — large sandboxes don't wedge the API.

        Capability: requires `filesystem.read`.
        Traversal → 403 + auto-isolate (handled inside
        `_resolve_inside_sandbox`)."""
        _enforce_fs_scope(app_id, "filesystem.read")
        # The resolver expects a relative path; "" means "the root".
        # Pass "." so commonpath sees an in-root resolved candidate.
        root = _resolve_inside_sandbox(app_id, virtual_path or ".")
        if not root.exists():
            raise FileNotFoundError(
                f"directory not found: <{app_id}-sandbox>/{virtual_path}"
            )
        if not root.is_dir():
            raise NotADirectoryError(
                f"not a directory: <{app_id}-sandbox>/{virtual_path}"
            )

        sandbox_root = _resolve_inside_sandbox(app_id, ".")
        entries: list = []

        def _enumerate(dir_path, depth: int) -> None:
            if depth > max_depth or len(entries) >= max_entries:
                return
            try:
                children = sorted(dir_path.iterdir())
            except OSError:
                return
            for child in children:
                if len(entries) >= max_entries:
                    return
                try:
                    stat = child.stat()
                    size = stat.st_size if child.is_file() else None
                    mtime_ms = int(stat.st_mtime * 1000)
                except OSError:
                    size = None
                    mtime_ms = None
                rel = child.relative_to(sandbox_root).as_posix()
                entries.append(
                    {
                        "name": child.name,
                        "path": rel,
                        "is_dir": child.is_dir(),
                        "size_bytes": size,
                        "modified_at_ms": mtime_ms,
                    }
                )
                if recursive and child.is_dir():
                    _enumerate(child, depth + 1)

        _enumerate(root, 0)
        return entries

    @staticmethod
    def delete_file(app_id: str, virtual_path: str) -> dict:
        """Unlink a single file inside the sandbox.

        Capability: requires `filesystem.write`. Refuses to delete
        directories (operator can re-install the app to nuke the
        sandbox root). Traversal → 403 + isolate."""
        _enforce_fs_scope(app_id, "filesystem.write")
        target = _resolve_inside_sandbox(app_id, virtual_path)
        if not target.exists():
            raise FileNotFoundError(
                f"file not found: <{app_id}-sandbox>/{virtual_path}"
            )
        if target.is_dir():
            raise IsADirectoryError(f"refusing to delete a directory: {virtual_path}")
        target.unlink()
        logger.info(
            "[fs] delete app=%s rel=%s",
            app_id,
            virtual_path,
        )
        return {"app_id": app_id, "path": virtual_path, "deleted": True}


APP_FILESYSTEM_SERVICE = AppFilesystemService()


__all__ = [
    "FILESYSTEM_MANAGER",
    "APP_FILESYSTEM_SERVICE",
    "SovereignFilesystemManager",
    "AppFilesystemService",
    "PathTraversalAttempt",
]
