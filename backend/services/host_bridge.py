"""
backend/services/host_bridge.py — Legacy Host Bridge (P6.3).

Lets a sandboxed vOS app drive automation against a host-resident
legacy system (SAP GUI, Salesforce Desktop, Oracle Forms, ...) WITHOUT
weakening the P4.x sandbox boundary.

Pipeline
--------
    sandboxed app
         │  (HTTP /api/host_bridge/execute   OR
         │   in-process HostBridgeService.execute_host_automation)
         ▼
    ┌──────────────────────────────────────────────────────────┐
    │  HostBridgeService                                       │
    │  1. PermissionGate.check(app, 'host.automation')         │
    │  2. Workspace tenancy:                                   │
    │       app.workspaceId == host-bound workspace OR raise   │
    │  3. TransactionGuard:                                    │
    │       a. Per-(app,target) rate-limit (default 10/min)    │
    │       b. Monetary cap (default $5_000 per txn)           │
    │       c. Action allow-list (per target_system)           │
    │       Above any threshold → write PendingHostApproval    │
    │       row + return {status:"awaiting_approval", id:...}  │
    │  4. Route by online/offline:                             │
    │       Offline → OfflineSidecarExecutor                   │
    │                  (ephemeral subprocess, JSON over stdin/ │
    │                   stdout)                                │
    │       Online  → OnlineCloudExecutor                      │
    │                  (httpx → host cloud API; app manifest   │
    │                   must hold 'network.egress:<domain>')   │
    │  5. Wrap return as {status:"ok", result:..., executor:..}│
    └──────────────────────────────────────────────────────────┘

Approval flow (HITL)
--------------------
- Operator polls `/api/host_bridge/approvals`.
- Operator signs a {approval_id, decision, ts_ms} canonical blob with
  the P6.1 workflow-signing key, then POSTs to
  `/api/host_bridge/approvals/{id}/approve`. The route verifies the
  Ed25519 signature against the locally-stored keypair's public half.
- On `approved`, the executor runs and the row transitions to
  `executed` (or `failed`). On `rejected`, the row terminates.

Air-gap discipline
------------------
- The offline executor's subprocess inherits a stripped env (PATH +
  VOS3_HOST_BRIDGE=1 + workspace_id) — same allow-list pattern as
  P4.4's AppProcessRunner.
- The online executor refuses to run unless the app's manifest holds
  `network.egress:<allowed.domain>` AND `_is_online()` returns True.
  Under the airgap kill-switch the online path is unreachable (any
  non-loopback connect raises IOError); tests pin offline-mode env.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults / tunables (env-overridable)
# ---------------------------------------------------------------------------


DEFAULT_AMOUNT_LIMIT = 5_000.0
DEFAULT_RATE_LIMIT_PER_MIN = 10
DEFAULT_OFFLINE_TIMEOUT_S = 30.0
DEFAULT_ONLINE_TIMEOUT_S = 15.0
DEFAULT_SUBPROCESS_INTERPRETER = ("python3",)

REQUIRED_SCOPE = "host.automation"


# Maps a logical target system to the helper script + cloud API
# fingerprint. Operators can override via VOS3_HOST_BRIDGE_REGISTRY
# (JSON path). The defaults are intentionally minimal — production
# sites add their own entries.
DEFAULT_TARGET_REGISTRY = {
    "SAP_GUI": {
        "helper_script": "vos3_host_helpers/sap_gui_driver.py",
        "cloud_endpoint": None,  # GUI-only, no public cloud surrogate
        "cloud_egress_scope": None,
        "actions": {
            "fetch_vendor_balance": {"max_amount": None, "writes": False},
            "fetch_invoice": {"max_amount": None, "writes": False},
            "create_purchase_order": {"max_amount": 5_000.0, "writes": True},
            "post_journal_entry": {"max_amount": 5_000.0, "writes": True},
        },
    },
    "SAP_S4HANA_CLOUD": {
        "helper_script": "vos3_host_helpers/sap_s4_cli.py",
        "cloud_endpoint": "https://api.sap.example.com/v1/automation",
        "cloud_egress_scope": "network.egress:api.sap.example.com",
        "actions": {
            "fetch_vendor_balance": {"max_amount": None, "writes": False},
            "create_purchase_order": {"max_amount": 5_000.0, "writes": True},
        },
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HostBridgeError(RuntimeError):
    """Base exception for host bridge failures."""


class HostScopeDenied(HostBridgeError):
    """Sandbox app lacks `host.automation` (or the workspace mismatch)."""


class HostTargetUnknown(HostBridgeError):
    """target_system is not in the registry."""


class HostActionUnknown(HostBridgeError):
    """action is not whitelisted for the target_system."""


class HostExecutorFailed(HostBridgeError):
    """The underlying executor returned non-zero / non-JSON."""


class TransactionPending(HostBridgeError):
    """Internal marker — the Transaction Guard intercepted; caller
    receives a structured `{status: awaiting_approval, ...}` response
    so this is only used in the in-process API for callers that prefer
    raise-based control flow."""

    def __init__(self, approval_id: str, reason: str):
        super().__init__(f"awaiting human approval: {reason} (id={approval_id})")
        self.approval_id = approval_id
        self.reason = reason


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class GuardConfig:
    """Per-(target_system, action) thresholds. Env-overridable."""

    amount_limit: float = DEFAULT_AMOUNT_LIMIT
    rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN
    require_approval_for_writes: bool = False

    @classmethod
    def from_env(cls) -> "GuardConfig":
        amt = float(os.getenv("VOS3_HOST_AMOUNT_LIMIT") or DEFAULT_AMOUNT_LIMIT)
        rl = int(
            os.getenv("VOS3_HOST_RATE_LIMIT_PER_MIN") or DEFAULT_RATE_LIMIT_PER_MIN
        )
        require_writes = (os.getenv("VOS3_HOST_APPROVE_ALL_WRITES") or "0") == "1"
        return cls(
            amount_limit=amt,
            rate_limit_per_min=rl,
            require_approval_for_writes=require_writes,
        )


@dataclass
class ExecuteResult:
    """Returned to callers — either an immediate result OR an
    approval-pending sentinel (status='awaiting_approval')."""

    status: str  # "ok" | "awaiting_approval" | "error"
    executor: Optional[str] = None  # "offline" | "online" | None
    result: Optional[dict] = None
    approval_id: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"status": self.status}
        for k in ("executor", "result", "approval_id", "reason", "error"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


# ---------------------------------------------------------------------------
# Rate limiter — per (app_id, target_system) sliding window
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Simple sliding-window counter. Threadsafe via a single Lock.

    The window is 60 seconds; the bucket holds the timestamps of
    every recent call. The limiter is in-memory only — a process
    restart resets it. Persisting the window across restarts would
    require either a DB write per call (too costly) or a Redis
    dependency (we're sovereign / offline). The reset on restart is
    a documented soft-edge of the contract."""

    def __init__(self):
        self._buckets: dict = {}
        self._lock = threading.Lock()

    def check_and_record(
        self,
        key: tuple,
        *,
        limit: int,
        window_s: float = 60.0,
    ) -> tuple:
        """Return (allowed, count_within_window).

        `allowed` is False once the bucket reaches `limit`."""
        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            stamps = self._buckets.setdefault(key, [])
            # Drop everything older than the window.
            while stamps and stamps[0] < cutoff:
                stamps.pop(0)
            if len(stamps) >= limit:
                return False, len(stamps)
            stamps.append(now)
            return True, len(stamps)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# ---------------------------------------------------------------------------
# Transaction Guard
# ---------------------------------------------------------------------------


class TransactionGuard:
    """Decides whether a host-bridge call may execute immediately, or
    if it must wait for human approval.

    Decisions:
      1. Action allow-list — UNKNOWN action → reject hard.
      2. Rate-limit — over threshold → awaiting_approval row.
      3. Amount cap — payload['amount'] > max_amount → awaiting_approval.
      4. Write-action gate (optional) — action is a 'writes' action
         AND `require_approval_for_writes` is set → awaiting_approval.
    """

    def __init__(
        self,
        *,
        config: Optional[GuardConfig] = None,
        rate_limiter: Optional[_RateLimiter] = None,
        registry: Optional[dict] = None,
    ):
        self.config = config or GuardConfig.from_env()
        self.rate_limiter = rate_limiter or _RateLimiter()
        self.registry = registry or DEFAULT_TARGET_REGISTRY

    # --- public API --------------------------------------------------

    def evaluate(
        self,
        *,
        app_id: str,
        workspace_id: str,
        target_system: str,
        action: str,
        script_payload: dict,
    ) -> dict:
        """Return a decision dict.

        Shape:
          {
            "decision":  "allow" | "hold",
            "reason":    str | None,            # populated on hold
            "amount":    float | None,
            "currency":  str | None,
            "action_meta": {...registry row..} | None
          }
        Raises:
          HostTargetUnknown  — unknown target_system
          HostActionUnknown  — action not in registry for target
        """
        target_meta = self.registry.get(target_system)
        if target_meta is None:
            raise HostTargetUnknown(f"unknown target_system={target_system!r}")
        action_meta = target_meta.get("actions", {}).get(action)
        if action_meta is None:
            raise HostActionUnknown(
                f"action={action!r} not whitelisted on target={target_system!r}"
            )

        amount, currency = self._extract_amount(script_payload)

        # 1. Rate-limit.
        rl_ok, count = self.rate_limiter.check_and_record(
            (app_id, target_system),
            limit=self.config.rate_limit_per_min,
            window_s=60.0,
        )
        if not rl_ok:
            return {
                "decision": "hold",
                "reason": (
                    f"rate_limit_exceeded:{count}/{self.config.rate_limit_per_min}/min"
                ),
                "amount": amount,
                "currency": currency,
                "action_meta": action_meta,
            }

        # 2. Amount cap — per-action override beats the global cap.
        per_action_cap = action_meta.get("max_amount")
        effective_cap = (
            per_action_cap if per_action_cap is not None else self.config.amount_limit
        )
        if amount is not None and effective_cap is not None and amount > effective_cap:
            return {
                "decision": "hold",
                "reason": (f"amount_over_threshold:{amount} > " f"{effective_cap}"),
                "amount": amount,
                "currency": currency,
                "action_meta": action_meta,
            }

        # 3. Write-action approval gate (optional).
        if self.config.require_approval_for_writes and action_meta.get("writes"):
            return {
                "decision": "hold",
                "reason": "writes_require_approval",
                "amount": amount,
                "currency": currency,
                "action_meta": action_meta,
            }

        return {
            "decision": "allow",
            "reason": None,
            "amount": amount,
            "currency": currency,
            "action_meta": action_meta,
        }

    # --- helpers -----------------------------------------------------

    @staticmethod
    def _extract_amount(payload: dict) -> tuple:
        """Best-effort numeric extraction. Falls back to (None, None)
        when the payload doesn't carry a monetary value."""
        if not isinstance(payload, dict):
            return None, None
        amt = payload.get("amount")
        currency = payload.get("currency")
        if isinstance(amt, (int, float)):
            return float(amt), (currency if isinstance(currency, str) else None)
        if isinstance(amt, str):
            try:
                return float(amt.replace(",", "")), (
                    currency if isinstance(currency, str) else None
                )
            except ValueError:
                return None, None
        return None, None


# ---------------------------------------------------------------------------
# Executors — pluggable per network mode
# ---------------------------------------------------------------------------


class HostExecutor:
    """ABC for host-bridge executors."""

    name = "abstract"

    def execute(
        self,
        *,
        target_system: str,
        action: str,
        script_payload: dict,
        timeout_s: float = DEFAULT_OFFLINE_TIMEOUT_S,
    ) -> dict:
        raise NotImplementedError


class OfflineSidecarExecutor(HostExecutor):
    """Spawn the host-side helper script as a subprocess.

    The helper inherits a stripped env and receives the full payload
    on stdin as a JSON document. It must respond with a JSON document
    on stdout (any non-JSON stdout is treated as a hard error). The
    helper script path is resolved relative to either
    `$VOS3_HOST_HELPERS_DIR` or the script name in the registry.

    For tests + headless CI, callers supply an `inline_executor`
    callable instead of a real helper path; see `HostBridgeService(...)`.
    """

    name = "offline"

    def __init__(
        self,
        *,
        registry: dict,
        helpers_dir: Optional[str] = None,
        interpreter: tuple = DEFAULT_SUBPROCESS_INTERPRETER,
    ):
        self.registry = registry
        self.helpers_dir = helpers_dir or os.getenv(
            "VOS3_HOST_HELPERS_DIR",
            "/opt/vos3/host_helpers",
        )
        self.interpreter = interpreter

    def execute(
        self,
        *,
        target_system: str,
        action: str,
        script_payload: dict,
        timeout_s: float = DEFAULT_OFFLINE_TIMEOUT_S,
    ) -> dict:
        target_meta = self.registry.get(target_system) or {}
        helper = target_meta.get("helper_script")
        if not helper:
            raise HostExecutorFailed(
                f"no helper_script configured for {target_system!r}"
            )
        # Resolve script path — absolute paths are accepted verbatim,
        # relative paths are resolved against the helpers_dir.
        path = (
            helper if os.path.isabs(helper) else os.path.join(self.helpers_dir, helper)
        )
        cmd = list(self.interpreter) + [path]
        env = self._sanitized_env()
        body = json.dumps(
            {
                "target_system": target_system,
                "action": action,
                "payload": script_payload,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            proc = subprocess.run(
                cmd,
                input=body,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=timeout_s,
                # shell=False is the default — left explicit for
                # `RCE Defense` audit checks.
                shell=False,
            )
        except FileNotFoundError as exc:
            raise HostExecutorFailed(f"helper not found: {path} ({exc})") from exc
        except subprocess.TimeoutExpired as exc:
            raise HostExecutorFailed(
                f"helper timeout after {timeout_s}s: {shlex.join(cmd)}"
            ) from exc
        if proc.returncode != 0:
            raise HostExecutorFailed(
                f"helper exit={proc.returncode}: "
                f"{proc.stderr.decode('utf-8', errors='replace')[:300]}"
            )
        try:
            return json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostExecutorFailed(f"helper stdout not JSON: {exc}") from exc

    @staticmethod
    def _sanitized_env() -> dict:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/tmp",
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "VOS3_HOST_BRIDGE": "1",
        }


class OnlineCloudExecutor(HostExecutor):
    """POST the payload to the configured cloud endpoint.

    Refuses to dispatch unless `_is_online()` returns True AND the
    app's manifest holds the registry-declared `cloud_egress_scope`
    (e.g. `network.egress:api.sap.example.com`). The pinning happens
    at the HostBridgeService level — this executor only owns the
    network call.
    """

    name = "online"

    def __init__(self, *, registry: dict):
        self.registry = registry

    def execute(
        self,
        *,
        target_system: str,
        action: str,
        script_payload: dict,
        timeout_s: float = DEFAULT_ONLINE_TIMEOUT_S,
    ) -> dict:
        target_meta = self.registry.get(target_system) or {}
        url = target_meta.get("cloud_endpoint")
        if not url:
            raise HostExecutorFailed(
                f"no cloud_endpoint configured for {target_system!r}"
            )
        try:
            import httpx  # late import — keeps offline boots cheap
        except ImportError as exc:
            raise HostExecutorFailed(
                "httpx not installed — online executor unavailable"
            ) from exc
        body = {
            "target_system": target_system,
            "action": action,
            "payload": script_payload,
        }
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(url, json=body)
        except Exception as exc:  # noqa: BLE001 — network is broad
            raise HostExecutorFailed(f"cloud POST failed: {exc!r}") from exc
        if response.status_code >= 400:
            raise HostExecutorFailed(
                f"cloud POST {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001 — httpx JSONDecodeError
            raise HostExecutorFailed(f"cloud response not JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Online / offline detection
# ---------------------------------------------------------------------------


def _is_online_default() -> bool:
    """Detect whether the host has cloud egress.

    Order (first definitive answer wins):
      1. $VOS3_NETWORK_MODE     — "offline" / "online" (explicit)
      2. $VOS3_LOCALITY_PREFERENCE
                                 — "local-first" / "cloud-first"
      3. False (safe default)    — air-gap by default
    """
    explicit = (os.getenv("VOS3_NETWORK_MODE") or "").strip().lower()
    if explicit in ("online", "1", "true", "yes"):
        return True
    if explicit in ("offline", "0", "false", "no"):
        return False
    pref = (os.getenv("VOS3_LOCALITY_PREFERENCE") or "").strip().lower()
    if pref == "cloud-first":
        return True
    return False


# ---------------------------------------------------------------------------
# HostBridgeService — the public entry point
# ---------------------------------------------------------------------------


class HostBridgeService:
    """Routes a sandboxed automation request to the right executor.

    Constructor kwargs are mostly for test injection:
      registry           — override the target/action map
      guard              — TransactionGuard instance (default constructs)
      offline_executor   — HostExecutor subclass / callable
      online_executor    — HostExecutor subclass / callable
      online_probe       — Callable returning bool (default uses env)
      inline_executor    — Callable[(target, action, payload, mode)] -> dict
                           Bypasses both Offline/OnlineCloudExecutor
                           and lets tests deliver a deterministic
                           response without spawning subprocesses.
    """

    def __init__(
        self,
        *,
        registry: Optional[dict] = None,
        guard: Optional[TransactionGuard] = None,
        offline_executor: Optional[HostExecutor] = None,
        online_executor: Optional[HostExecutor] = None,
        online_probe: Optional[Callable[[], bool]] = None,
        inline_executor: Optional[Callable[..., dict]] = None,
    ):
        self.registry = registry or DEFAULT_TARGET_REGISTRY
        self.guard = guard or TransactionGuard(registry=self.registry)
        self.offline_executor = offline_executor or OfflineSidecarExecutor(
            registry=self.registry
        )
        self.online_executor = online_executor or OnlineCloudExecutor(
            registry=self.registry
        )
        self.online_probe = online_probe or _is_online_default
        self.inline_executor = inline_executor

    # --- public API --------------------------------------------------

    def execute_host_automation(
        self,
        *,
        app_id: str,
        target_system: str,
        script_payload: dict,
        operator_user_id: Optional[str] = None,
    ) -> ExecuteResult:
        """Run a sandboxed app's host-automation request.

        Returns an ExecuteResult. Never raises on a security or
        guard decision — those land as `status='awaiting_approval'`
        or `status='error'`. Raises only for caller-side contract
        bugs (e.g. unknown target_system).
        """
        if not isinstance(script_payload, dict):
            raise ValueError("script_payload must be a dict")
        action = script_payload.get("action")
        if not isinstance(action, str) or not action:
            raise ValueError("script_payload['action'] required (str)")

        # 1. Scope + isolation check.
        app_snapshot, workspace_id = self._gate_app(app_id, target_system)

        # 2. Transaction Guard.
        guard_decision = self.guard.evaluate(
            app_id=app_id,
            workspace_id=workspace_id,
            target_system=target_system,
            action=action,
            script_payload=script_payload,
        )
        if guard_decision["decision"] == "hold":
            approval_id = self._enqueue_approval(
                app_id=app_id,
                workspace_id=workspace_id,
                target_system=target_system,
                action=action,
                script_payload=script_payload,
                amount=guard_decision["amount"],
                currency=guard_decision["currency"],
                reason=guard_decision["reason"] or "guard_hold",
            )
            return ExecuteResult(
                status="awaiting_approval",
                approval_id=approval_id,
                reason=guard_decision["reason"],
            )

        # 3. Online / offline routing.
        mode, executor = self._select_executor(
            app_snapshot=app_snapshot,
            target_system=target_system,
        )

        # 4. Dispatch.
        try:
            raw = self._dispatch(
                executor=executor,
                mode=mode,
                target_system=target_system,
                action=action,
                script_payload=script_payload,
            )
            return ExecuteResult(status="ok", executor=mode, result=raw)
        except HostBridgeError as exc:
            return ExecuteResult(
                status="error",
                executor=mode,
                error=str(exc),
            )

    # --- HITL approval surface ---------------------------------------

    def list_pending_approvals(self, *, app_id: Optional[str] = None) -> list:
        """Return all pending approval rows. Optionally narrowed
        to a single app."""
        from core.database.sqlite_setup import (
            PendingHostApproval,
            get_session,
            init_db,
        )

        init_db()
        with get_session() as session:
            q = session.query(PendingHostApproval).filter_by(
                status="awaiting_human_approval",
            )
            if app_id:
                q = q.filter_by(appId=app_id)
            return [
                _approval_to_dict(r)
                for r in q.order_by(
                    PendingHostApproval.createdAt.asc(),
                ).all()
            ]

    def get_approval(self, approval_id: str) -> Optional[dict]:
        from core.database.sqlite_setup import (
            PendingHostApproval,
            get_session,
            init_db,
        )

        init_db()
        with get_session() as session:
            row = (
                session.query(PendingHostApproval)
                .filter_by(
                    id=approval_id,
                )
                .one_or_none()
            )
            return _approval_to_dict(row) if row else None

    def approve_and_execute(
        self,
        *,
        approval_id: str,
        approver_user_id: str,
        signature_hex: str,
        public_key_hex: str,
    ) -> ExecuteResult:
        """Verify the operator's signature, then run the held call.

        Signature MUST be Ed25519 over the canonical bytes of
        `{approval_id, decision:"approve", approver_user_id}`. The
        public_key MUST match the one stored when the operator first
        bootstrapped the keypair (currently re-uses P6.1's workflow
        signing key for the operator identity).
        """
        from core.database.sqlite_setup import (
            PendingHostApproval,
            get_session,
            init_db,
        )

        init_db()
        if not self._verify_operator_signature(
            approval_id=approval_id,
            approver_user_id=approver_user_id,
            decision="approve",
            signature_hex=signature_hex,
            public_key_hex=public_key_hex,
        ):
            _record_security_event(
                kind="host_bridge_approval_rejected",
                reason="signature_invalid",
                details={
                    "approval_id": approval_id,
                    "approver": approver_user_id,
                },
            )
            return ExecuteResult(
                status="error",
                error="approver signature invalid",
            )

        with get_session() as session:
            row = (
                session.query(PendingHostApproval)
                .filter_by(
                    id=approval_id,
                )
                .one_or_none()
            )
            if row is None:
                return ExecuteResult(
                    status="error",
                    error="approval_id not found",
                )
            if row.status != "awaiting_human_approval":
                return ExecuteResult(
                    status="error",
                    error=f"approval already terminal: status={row.status!r}",
                )
            row.status = "approved"
            row.approverId = approver_user_id
            row.signature = signature_hex
            row.signing_public_key = public_key_hex
            row.updatedAt = _now_ms()
            session.commit()
            app_id = row.appId
            target_system = row.targetSystem
            action = row.action
            payload = json.loads(row.payload_json)

        # Get the app's snapshot post-approval — re-check status
        # (operator may have isolated the app in the meantime).
        app_snapshot, _workspace = self._gate_app(app_id, target_system)
        mode, executor = self._select_executor(
            app_snapshot=app_snapshot,
            target_system=target_system,
        )

        # Dispatch WITHOUT re-running the guard — operator has
        # signed off on this exact payload.
        try:
            raw = self._dispatch(
                executor=executor,
                mode=mode,
                target_system=target_system,
                action=action,
                script_payload=payload,
            )
            with get_session() as session:
                row = (
                    session.query(PendingHostApproval)
                    .filter_by(
                        id=approval_id,
                    )
                    .one()
                )
                row.status = "executed"
                row.result_json = json.dumps(raw, separators=(",", ":"))
                row.updatedAt = _now_ms()
                session.commit()
            return ExecuteResult(
                status="ok",
                executor=mode,
                result=raw,
                approval_id=approval_id,
            )
        except HostBridgeError as exc:
            with get_session() as session:
                row = (
                    session.query(PendingHostApproval)
                    .filter_by(
                        id=approval_id,
                    )
                    .one()
                )
                row.status = "failed"
                row.result_json = json.dumps(
                    {"error": str(exc)},
                    separators=(",", ":"),
                )
                row.updatedAt = _now_ms()
                session.commit()
            return ExecuteResult(
                status="error",
                executor=mode,
                error=str(exc),
                approval_id=approval_id,
            )

    def reject_approval(
        self,
        *,
        approval_id: str,
        approver_user_id: str,
        signature_hex: str,
        public_key_hex: str,
    ) -> ExecuteResult:
        """Operator-signed rejection. Flips the row to status='rejected'."""
        from core.database.sqlite_setup import (
            PendingHostApproval,
            get_session,
            init_db,
        )

        init_db()
        if not self._verify_operator_signature(
            approval_id=approval_id,
            approver_user_id=approver_user_id,
            decision="reject",
            signature_hex=signature_hex,
            public_key_hex=public_key_hex,
        ):
            return ExecuteResult(
                status="error",
                error="approver signature invalid",
            )
        with get_session() as session:
            row = (
                session.query(PendingHostApproval)
                .filter_by(
                    id=approval_id,
                )
                .one_or_none()
            )
            if row is None:
                return ExecuteResult(status="error", error="approval_id not found")
            if row.status != "awaiting_human_approval":
                return ExecuteResult(
                    status="error",
                    error=f"already terminal: status={row.status!r}",
                )
            row.status = "rejected"
            row.approverId = approver_user_id
            row.signature = signature_hex
            row.signing_public_key = public_key_hex
            row.updatedAt = _now_ms()
            session.commit()
        return ExecuteResult(
            status="ok",
            approval_id=approval_id,
            reason="rejected",
        )

    # --- internals ---------------------------------------------------

    def _gate_app(self, app_id: str, target_system: str) -> tuple:
        """Authorize the app and return (snapshot_dict, workspace_id).

        Side-effects:
          * Records a `scope_violation` audit row on denial (the gate
            itself owns that emission).
          * Raises HostScopeDenied on any failure.
        """
        from services.app_sandbox import (
            AppIsolated,
            AppNotFound,
            PERMISSION_GATE,
            SANDBOX_MANAGER,
            ScopeViolation,
            _record_security_event,
        )

        try:
            PERMISSION_GATE.check(app_id, REQUIRED_SCOPE)
        except (ScopeViolation, AppIsolated, AppNotFound) as exc:
            raise HostScopeDenied(str(exc)) from exc

        snapshot = SANDBOX_MANAGER.get(app_id)
        if snapshot is None:
            raise HostScopeDenied(f"app={app_id!r} not found post-check")
        workspace_id = snapshot.get("workspace_id")
        if not workspace_id:
            _record_security_event(
                kind="host_bridge_workspace_missing",
                app_id=app_id,
                reason="app has no workspace_id; host bridge refuses",
                details={"target_system": target_system},
            )
            raise HostScopeDenied(
                f"app={app_id!r} has no workspace_id (host bridge "
                f"requires workspace tenancy)"
            )

        # Cross-check the workspace pinning. The Tauri sidecar /
        # operator sets VOS3_HOST_WORKSPACE_ID at boot to pin the
        # backend to ONE workspace's host system. A mismatch is a
        # cross-tenant exploit attempt.
        host_ws = (os.getenv("VOS3_HOST_WORKSPACE_ID") or "").strip()
        if host_ws and host_ws != workspace_id:
            _record_security_event(
                kind="local_tampering_blocked",
                app_id=app_id,
                reason="host_bridge_workspace_mismatch",
                details={
                    "app_workspace": workspace_id,
                    "host_workspace": host_ws,
                    "target_system": target_system,
                },
            )
            raise HostScopeDenied(
                f"app workspace={workspace_id!r} does not match host "
                f"workspace={host_ws!r}"
            )
        return snapshot, workspace_id

    def _select_executor(
        self,
        *,
        app_snapshot: dict,
        target_system: str,
    ) -> tuple:
        """Pick the executor based on online state + manifest scope.

        Returns `(mode, executor)`. The Online path is taken only
        when ALL of:
          (a) `online_probe()` returns True
          (b) the registry entry for target_system has a non-null
              cloud_endpoint
          (c) the app's manifest holds the required `network.egress:*`
              scope (re-checked at runtime, never cached)
        Otherwise the Offline path is taken.
        """
        meta = self.registry.get(target_system) or {}
        cloud_endpoint = meta.get("cloud_endpoint")
        cloud_scope = meta.get("cloud_egress_scope")

        if self.online_probe() and cloud_endpoint and cloud_scope:
            manifest = app_snapshot.get("manifest") or {}
            granted = manifest.get("scopes") or []
            if cloud_scope in granted or any(
                _scope_implies_safe(g, cloud_scope) for g in granted
            ):
                return "online", self.online_executor
        return "offline", self.offline_executor

    def _dispatch(
        self,
        *,
        executor: HostExecutor,
        mode: str,
        target_system: str,
        action: str,
        script_payload: dict,
    ) -> dict:
        """Run the executor. Tests may install an `inline_executor`
        shortcut that skips the real subprocess / network call."""
        if self.inline_executor is not None:
            return self.inline_executor(
                target_system=target_system,
                action=action,
                payload=script_payload,
                mode=mode,
            )
        return executor.execute(
            target_system=target_system,
            action=action,
            script_payload=script_payload,
        )

    def _enqueue_approval(
        self,
        *,
        app_id: str,
        workspace_id: str,
        target_system: str,
        action: str,
        script_payload: dict,
        amount,
        currency,
        reason: str,
    ) -> str:
        """Persist a pending-approval row + emit an audit event."""
        from core.database.sqlite_setup import (
            PendingHostApproval,
            get_session,
            init_db,
        )

        init_db()
        approval_id = str(uuid.uuid4())
        now = _now_ms()
        with get_session() as session:
            session.add(
                PendingHostApproval(
                    id=approval_id,
                    appId=app_id,
                    workspaceId=workspace_id,
                    targetSystem=target_system,
                    action=action,
                    payload_json=json.dumps(
                        script_payload,
                        separators=(",", ":"),
                    ),
                    amount=amount,
                    currency=currency,
                    reason=reason,
                    status="awaiting_human_approval",
                    createdAt=now,
                    updatedAt=now,
                )
            )
            session.commit()
        _record_security_event(
            kind="awaiting_human_approval",
            app_id=app_id,
            scope=REQUIRED_SCOPE,
            reason=reason,
            details={
                "approval_id": approval_id,
                "target_system": target_system,
                "action": action,
                "amount": amount,
                "currency": currency,
            },
        )
        logger.warning(
            "[host_bridge] HOLD app=%s target=%s action=%s reason=%s id=%s",
            app_id,
            target_system,
            action,
            reason,
            approval_id,
        )
        return approval_id

    # --- operator-signature verification -----------------------------

    @staticmethod
    def _verify_operator_signature(
        *,
        approval_id: str,
        approver_user_id: str,
        decision: str,
        signature_hex: str,
        public_key_hex: str,
    ) -> bool:
        try:
            from services.app_crypto import (
                canonical_manifest_bytes,
                verify_manifest_signature,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[host_bridge] crypto import failed: %s", exc)
            return False
        canon = canonical_manifest_bytes(
            {
                "approval_id": approval_id,
                "approver_user_id": approver_user_id,
                "decision": decision,
            }
        )
        if not verify_manifest_signature(canon, signature_hex, public_key_hex):
            return False
        # Pin the public key to the workspace-signing key minted in P6.1.
        try:
            from services.crypto_keyring import get_or_mint_workflow_signing_key

            _, expected_pub = get_or_mint_workflow_signing_key()
            if expected_pub != public_key_hex:
                logger.warning(
                    "[host_bridge] operator key did not match P6.1 keyring",
                )
                return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[host_bridge] keyring lookup failed: %s", exc)
            return False
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_implies_safe(granted: str, requested: str) -> bool:
    """Wrapper around sandbox._scope_implies that tolerates missing
    import (e.g. circular-init paths)."""
    try:
        from services.app_sandbox import _scope_implies

        return _scope_implies(granted, requested)
    except Exception:  # noqa: BLE001
        return granted == requested


def _record_security_event(**kwargs) -> None:
    try:
        from services.app_sandbox import _record_security_event as _impl

        _impl(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[host_bridge] audit failed: %s", exc)


def _approval_to_dict(row) -> dict:
    return {
        "approval_id": row.id,
        "app_id": row.appId,
        "workspace_id": row.workspaceId,
        "target_system": row.targetSystem,
        "action": row.action,
        "amount": row.amount,
        "currency": row.currency,
        "reason": row.reason,
        "status": row.status,
        "approver_id": row.approverId,
        "result": json.loads(row.result_json) if row.result_json else None,
        "created_at": row.createdAt,
        "updated_at": row.updatedAt,
    }


# ---------------------------------------------------------------------------
# Process-wide singleton + reset hook for tests
# ---------------------------------------------------------------------------


HOST_BRIDGE = HostBridgeService()


def _reset_for_tests() -> None:
    """Drop the cached singleton + rate-limiter state."""
    global HOST_BRIDGE
    HOST_BRIDGE = HostBridgeService()


__all__ = [
    "DEFAULT_TARGET_REGISTRY",
    "REQUIRED_SCOPE",
    "ExecuteResult",
    "GuardConfig",
    "HOST_BRIDGE",
    "HostActionUnknown",
    "HostBridgeError",
    "HostBridgeService",
    "HostExecutor",
    "HostExecutorFailed",
    "HostScopeDenied",
    "HostTargetUnknown",
    "OfflineSidecarExecutor",
    "OnlineCloudExecutor",
    "TransactionGuard",
    "TransactionPending",
    "_reset_for_tests",
]
