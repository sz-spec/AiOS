"""
backend/services/agent_orchestrator.py — Sovereign App + Agent Orchestrator.

P6.0 — coordinates multi-step workflows that span the Sovereign App
Runtime's sandboxed apps (and AI agents wrapped as sandboxed apps).
Each step is a task dispatched into ONE app's sandbox; the output of
step A flows into step B via the Custodian (`secure_handoff`).

Architecture
------------

    workflow_manifest                         WorkflowRun (SQLite row)
    ┌─────────────────────┐    submit()      ┌────────────────────────┐
    │ name                │ ────────────────▶│ id, workspaceId,       │
    │ workspace_id        │                  │ manifest_json, status  │
    │ steps[]:            │                  └──────────┬─────────────┘
    │   id, app_id, task, │                             │
    │   depends_on, …     │                             ▼
    │ handoffs[]:         │              WorkflowStep rows (one per step,
    │   from, to, path    │              keyed by runId, ordered by DAG topo)
    └─────────────────────┘
                                                 │
                                  execute_workflow() runs each step
                                  in topological order:
                                                 │
                            ┌────────────────────▼─────────────────────┐
                            │ for step in toposorted_steps:            │
                            │   apply pending handoffs into            │
                            │     <step.app_id>/storage/shared_inputs/ │
                            │   dispatch_task(step.app_id, step.task)  │
                            │     → AppProcessRunner.run_entrypoint     │
                            │       env_override={"VOS3_TASK_JSON": …}  │
                            │   capture stdout JSON → step.output      │
                            │   on failure: mark failed; abort run     │
                            └──────────────────────────────────────────┘

Secure handoff (`secure_handoff`)
---------------------------------
Five Custodian checks before any bytes move:

  1. Both apps exist + status='active'.
  2. Both apps carry the SAME (non-null) workspaceId.
  3. Source app's manifest grants `filesystem.write`.
  4. Target app's manifest grants `filesystem.read`.
  5. `virtual_file_path` passes `_resolve_inside_sandbox` containment.

Any failure → `HandoffViolation` (PermissionError subclass) AND the
offending app (caller-side unless the target lacks `filesystem.read`)
is auto-isolated via `SANDBOX_MANAGER.isolate()` — same pattern as
the P4.3 traversal hook.

The subprocess protocol
-----------------------
Each step's subprocess is spawned by `AppProcessRunner.run_entrypoint`
with these env vars:
  VOS3_WORKFLOW_RUN_ID   — UUID of the run
  VOS3_STEP_ID           — human-readable step id
  VOS3_TASK_JSON         — JSON-encoded `task` payload
Apps read these env vars, do their work in their sandbox, and MUST
print a JSON object on stdout (parsed verbatim into step.output).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from core.database.sqlite_setup import (
    WorkflowRun,
    WorkflowStep,
    get_session,
    init_db,
)
from services.app_filesystem import (
    PathTraversalAttempt,
    _resolve_inside_sandbox,
)
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppIsolated,
    AppNotFound,
    ScopeViolation,
    _record_security_event,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorkflowValidationError(ValueError):
    """The submitted workflow manifest failed structural validation."""


class HandoffViolation(PermissionError):
    """The Custodian refused a `secure_handoff` request.

    Carries `source_app_id` / `target_app_id` / `reason` so the
    audit log + dashboard can render a structured event."""

    def __init__(self, *, source_app_id: str, target_app_id: str, reason: str):
        super().__init__(f"handoff {source_app_id} → {target_app_id} refused: {reason}")
        self.source_app_id = source_app_id
        self.target_app_id = target_app_id
        self.reason = reason


class WorkflowTampered(RuntimeError):
    """P6.1 — the persisted WorkflowRun row's signature does not
    verify against its public key. Raised at load time; the
    orchestrator flips the run to status='failed' before raising
    so subsequent reads see the quarantine."""

    def __init__(self, run_id: str, reason: str = "signature mismatch"):
        super().__init__(f"workflow run={run_id!r} tampered: {reason}")
        self.run_id = run_id
        self.reason = reason


# ---------------------------------------------------------------------------
# Workflow-run signing helpers (P6.1)
# ---------------------------------------------------------------------------


def _workflow_canonical_bytes(
    *,
    run_id: str,
    workspace_id: str,
    manifest_json: str,
    created_at_ms: int,
) -> bytes:
    """Deterministic byte form of a workflow's IMMUTABLE fields.

    Signature covers exactly these fields. Mutating any of them in
    SQLite after submit (e.g. a raw UPDATE that swaps in a different
    manifest) breaks the signature and the orchestrator quarantines
    the run on next load."""
    import json as _json

    payload = {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "manifest_json": manifest_json,
        "created_at_ms": int(created_at_ms),
    }
    return _json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign_workflow_row(
    *,
    run_id: str,
    workspace_id: str,
    manifest_json: str,
    created_at_ms: int,
) -> tuple:
    """Mint signature + public-key hex for a freshly-submitted row.

    Returns (signature_hex, public_key_hex). Falls back to (None, None)
    when the keyring + crypto stack are wedged — the run still runs,
    but the verifier short-circuits to "unsigned" mode (audit row
    notes the downgrade)."""
    try:
        from services.app_crypto import sign_manifest
        from services.crypto_keyring import get_or_mint_workflow_signing_key

        priv_hex, pub_hex = get_or_mint_workflow_signing_key()
        # `sign_manifest` accepts a dict; pass the canonical fields.
        canon = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "manifest_json": manifest_json,
            "created_at_ms": int(created_at_ms),
        }
        sig_hex = sign_manifest(canon, private_key_hex=priv_hex)
        return sig_hex, pub_hex
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[orch] workflow signing skipped (%s) — run will be unsigned",
            exc,
        )
        return None, None


def _verify_workflow_row(row) -> bool:
    """Return True iff the row's signature verifies against its
    stored public key. An unsigned row (signature IS NULL) returns
    True — same dev-mode convention as P5.2 app installs."""
    if not row.signature or not row.signing_public_key:
        return True
    try:
        from services.app_crypto import verify_manifest_signature

        canon = _workflow_canonical_bytes(
            run_id=row.id,
            workspace_id=row.workspaceId,
            manifest_json=row.manifest_json,
            created_at_ms=row.createdAt,
        )
        return verify_manifest_signature(
            canon,
            row.signature,
            row.signing_public_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[orch] workflow signature verify failed (%s)",
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class WorkflowRunHandle:
    """Lightweight return value for `submit_workflow`."""

    run_id: str
    name: Optional[str]
    workspace_id: str
    step_count: int
    status: str

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "name": self.name,
            "workspace_id": self.workspace_id,
            "step_count": self.step_count,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# DAG resolver — pure function, unit-testable
# ---------------------------------------------------------------------------


def _topological_order(steps: list) -> list:
    """Return step ids in execution order.

    Kahn's algorithm with deterministic tie-breaking on `step.id`
    so the same manifest always produces the same order — useful
    for snapshot tests.

    Raises WorkflowValidationError on:
      * duplicate step ids
      * dependency on an unknown step id
      * a cycle anywhere in the DAG
    """
    if not steps:
        raise WorkflowValidationError("manifest.steps must be non-empty")

    by_id: dict = {}
    for s in steps:
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            raise WorkflowValidationError("each step requires a string `id`")
        if sid in by_id:
            raise WorkflowValidationError(f"duplicate step id: {sid!r}")
        by_id[sid] = s

    indegree: dict = {sid: 0 for sid in by_id}
    out_edges: dict = {sid: set() for sid in by_id}
    for sid, s in by_id.items():
        deps = s.get("depends_on") or []
        if not isinstance(deps, list):
            raise WorkflowValidationError(f"step {sid!r}.depends_on must be a list")
        for dep in deps:
            if not isinstance(dep, str):
                raise WorkflowValidationError(
                    f"step {sid!r}.depends_on entries must be strings"
                )
            if dep not in by_id:
                raise WorkflowValidationError(
                    f"step {sid!r} depends on unknown step {dep!r}"
                )
            if dep == sid:
                raise WorkflowValidationError(f"step {sid!r} cannot depend on itself")
            indegree[sid] += 1
            out_edges[dep].add(sid)

    # Kahn's algorithm with sorted seeds for determinism.
    ready = sorted(sid for sid, d in indegree.items() if d == 0)
    order: list = []
    while ready:
        sid = ready.pop(0)
        order.append(sid)
        new_ready: list = []
        for nxt in out_edges[sid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                new_ready.append(nxt)
        if new_ready:
            ready.extend(sorted(new_ready))
            ready.sort()  # keep the heap deterministic

    if len(order) != len(by_id):
        cycle = [sid for sid, d in indegree.items() if d > 0]
        raise WorkflowValidationError(f"cycle detected in DAG; nodes={cycle}")
    return order


# ---------------------------------------------------------------------------
# SovereignOrchestrator
# ---------------------------------------------------------------------------


class SovereignOrchestrator:
    """Multi-app workflow scheduler with secure inter-app handoff.

    Stateless across runs — every call re-reads the SQLite rows so
    a backend restart mid-workflow leaves the on-disk state intact.
    """

    DEFAULT_ENTRYPOINT = "main.py"
    SHARED_INPUTS_DIRNAME = "shared_inputs"

    def __init__(self, *, runner=None):
        self.runner = runner or APP_PROCESS_RUNNER

    # --- submit -------------------------------------------------------

    def submit_workflow(self, workflow_manifest: dict) -> WorkflowRunHandle:
        """Validate the manifest, persist the run + step rows.

        Returns a `WorkflowRunHandle` so the caller can poll status
        via `/api/orchestrator/workflows/{run_id}/status`.
        """
        init_db()
        if not isinstance(workflow_manifest, dict):
            raise WorkflowValidationError("workflow_manifest must be an object")
        workspace_id = workflow_manifest.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise WorkflowValidationError("workflow_manifest.workspace_id required")
        steps = workflow_manifest.get("steps") or []
        if not isinstance(steps, list):
            raise WorkflowValidationError("workflow_manifest.steps must be a list")

        # Validate the DAG up-front so submit() rejects bad shapes
        # before writing ANY row.
        _ = _topological_order(steps)

        # Verify every referenced app exists and lives in this workspace.
        for s in steps:
            app_id = s.get("app_id")
            if not isinstance(app_id, str) or not app_id:
                raise WorkflowValidationError(
                    f"step {s.get('id')!r} requires a string `app_id`"
                )
            record = SANDBOX_MANAGER.get(app_id)
            if record is None:
                raise WorkflowValidationError(
                    f"step {s.get('id')!r} references unknown app {app_id!r}"
                )
            if record.get("workspace_id") != workspace_id:
                raise WorkflowValidationError(
                    f"step {s.get('id')!r} app {app_id!r} not in workspace "
                    f"{workspace_id!r} (app workspace="
                    f"{record.get('workspace_id')!r})"
                )

        # Validate handoffs against the step ids.
        handoffs = workflow_manifest.get("handoffs") or []
        if not isinstance(handoffs, list):
            raise WorkflowValidationError("workflow_manifest.handoffs must be a list")
        step_ids = {s["id"] for s in steps}
        for h in handoffs:
            if not isinstance(h, dict):
                raise WorkflowValidationError("each handoff must be an object")
            fs, ts, p = h.get("from_step"), h.get("to_step"), h.get("path")
            if fs not in step_ids or ts not in step_ids:
                raise WorkflowValidationError(
                    f"handoff references unknown step (from={fs!r} to={ts!r})"
                )
            if not isinstance(p, str) or not p:
                raise WorkflowValidationError(
                    f"handoff {fs}→{ts} requires a string `path`"
                )

        run_id = str(uuid.uuid4())
        now = _now_ms()
        manifest_json = json.dumps(workflow_manifest, separators=(",", ":"))
        # P6.1 — sign the immutable run fields with the keyring-
        # protected workflow signing key. A SQL-level tamper that
        # rewrites manifest_json or workspaceId will fail this
        # signature on the next load.
        sig_hex, pub_hex = _sign_workflow_row(
            run_id=run_id,
            workspace_id=workspace_id,
            manifest_json=manifest_json,
            created_at_ms=now,
        )
        with get_session() as session:
            session.add(
                WorkflowRun(
                    id=run_id,
                    name=workflow_manifest.get("name"),
                    workspaceId=workspace_id,
                    status="pending",
                    manifest_json=manifest_json,
                    signature=sig_hex,
                    signing_public_key=pub_hex,
                    createdAt=now,
                    updatedAt=now,
                )
            )
            for s in steps:
                session.add(
                    WorkflowStep(
                        id=str(uuid.uuid4()),
                        runId=run_id,
                        stepId=s["id"],
                        appId=s["app_id"],
                        dependsOn_json=json.dumps(s.get("depends_on") or []),
                        status="pending",
                        task_json=json.dumps(s.get("task") or {}),
                        createdAt=now,
                        updatedAt=now,
                    )
                )
            session.commit()

        logger.info(
            "[orch] submit run_id=%s workspace=%s steps=%d",
            run_id,
            workspace_id,
            len(steps),
        )
        return WorkflowRunHandle(
            run_id=run_id,
            name=workflow_manifest.get("name"),
            workspace_id=workspace_id,
            step_count=len(steps),
            status="pending",
        )

    # --- execute ------------------------------------------------------

    async def execute_workflow(self, run_id: str) -> dict:
        """Drive a submitted workflow to completion (or first failure).

        Returns the run status dict — same shape as the /status route.

        P6.1 — `_load` verifies the run's Ed25519 signature; if it
        fails, the run is auto-quarantined (status='failed', audit
        event recorded) before this method gets the chance to
        execute anything.
        """
        try:
            manifest, _ = self._load(run_id)
        except WorkflowTampered:
            # `_load` already flipped status='failed' + recorded the
            # tamper audit row. Return the new status verbatim.
            return self.run_status(run_id)
        steps = manifest["steps"]
        handoffs = manifest.get("handoffs") or []
        order = _topological_order(steps)
        step_by_id = {s["id"]: s for s in steps}
        # Group handoffs by destination so we can apply them just
        # before each step runs.
        inbox: dict = {}
        for h in handoffs:
            inbox.setdefault(h["to_step"], []).append(h)

        self._set_run_status(run_id, "running")

        for sid in order:
            step = step_by_id[sid]
            step_app = step["app_id"]
            self._set_step_status(run_id, sid, "running")

            # Apply inbound handoffs FIRST so the target step finds
            # `shared_inputs/<basename>` in its sandbox before its
            # main.py is spawned.
            try:
                for h in inbox.get(sid, []):
                    src_step = step_by_id[h["from_step"]]
                    self.secure_handoff(
                        source_app_id=src_step["app_id"],
                        target_app_id=step_app,
                        virtual_file_path=h["path"],
                    )
            except (HandoffViolation, PathTraversalAttempt) as exc:
                self._set_step_status(
                    run_id,
                    sid,
                    "failed",
                    error=f"handoff failed: {exc}",
                )
                self._set_run_status(
                    run_id,
                    "failed",
                    error=f"step {sid!r} handoff failed: {exc}",
                )
                logger.warning("[orch] run %s failed at handoff: %s", run_id, exc)
                return self.run_status(run_id)

            # Dispatch the step's task into the target sandbox.
            try:
                output = await self.dispatch_task(
                    step_app,
                    task_payload=step.get("task") or {},
                    workflow_run_id=run_id,
                    step_id=sid,
                )
                self._set_step_status(
                    run_id,
                    sid,
                    "completed",
                    output=output,
                )
            except Exception as exc:  # noqa: BLE001
                self._set_step_status(
                    run_id,
                    sid,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._set_run_status(
                    run_id,
                    "failed",
                    error=f"step {sid!r} failed: {exc}",
                )
                logger.warning("[orch] run %s failed at step %s: %s", run_id, sid, exc)
                return self.run_status(run_id)

        self._set_run_status(run_id, "completed")
        return self.run_status(run_id)

    # --- dispatch_task ------------------------------------------------

    async def dispatch_task(
        self,
        app_id: str,
        task_payload: dict,
        *,
        workflow_run_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> dict:
        """Spawn the app's entrypoint with the task payload on env.

        Returns the parsed JSON the subprocess wrote to stdout.

        Raises:
          ScopeViolation / AppIsolated / AppNotFound — pre-spawn
            gate denials (the runner enforces `process.execute`).
          RuntimeError — the child exited non-zero OR produced
            unparseable stdout.
        """
        if not isinstance(task_payload, dict):
            raise TypeError("task_payload must be a dict")
        task_json = json.dumps(task_payload, separators=(",", ":"))
        env: dict = {"VOS3_TASK_JSON": task_json}
        if workflow_run_id:
            env["VOS3_WORKFLOW_RUN_ID"] = workflow_run_id
        if step_id:
            env["VOS3_STEP_ID"] = step_id

        result = await self.runner.run_entrypoint(
            app_id,
            env_override=env,
            entrypoint=self.DEFAULT_ENTRYPOINT,
        )

        if result.timed_out:
            raise RuntimeError(
                f"task subprocess timed out (duration_ms={result.duration_ms})"
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"task subprocess exited rc={result.returncode}: "
                f"{(result.stderr or '').strip()[:512]}"
            )
        stdout = (result.stdout or "").strip()
        if not stdout:
            raise RuntimeError("task subprocess produced empty stdout")
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"task subprocess stdout not JSON: {exc}; head=" f"{stdout[:256]!r}"
            ) from exc

    # --- secure_handoff (Custodian) -----------------------------------

    def secure_handoff(
        self,
        *,
        source_app_id: str,
        target_app_id: str,
        virtual_file_path: str,
    ) -> dict:
        """Copy a file between two app sandboxes after the Custodian
        has cleared all five checks.

        Returns:
          {"source_app_id", "target_app_id",
           "source_path", "target_path", "bytes_copied"}
        """
        source = SANDBOX_MANAGER.get(source_app_id)
        target = SANDBOX_MANAGER.get(target_app_id)

        def _refuse(reason: str, *, isolate: Optional[str] = None) -> None:
            _record_security_event(
                kind="handoff_violation",
                app_id=isolate or source_app_id,
                reason=reason,
                details={
                    "source_app_id": source_app_id,
                    "target_app_id": target_app_id,
                    "virtual_file_path": virtual_file_path,
                },
            )
            if isolate is not None:
                try:
                    SANDBOX_MANAGER.isolate(isolate, reason="handoff_violation")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[orch] auto-isolate after handoff failure failed: %s",
                        exc,
                    )
            raise HandoffViolation(
                source_app_id=source_app_id,
                target_app_id=target_app_id,
                reason=reason,
            )

        # Check 1 — both apps exist + status='active'.
        if source is None or source.get("status") != "active":
            _refuse(
                f"source app missing or not active "
                f"(status={None if source is None else source.get('status')!r})",
                # If source exists but is non-active, it's already
                # isolated — no further action. If source is missing
                # there's nothing to isolate.
                isolate=None,
            )
        if target is None or target.get("status") != "active":
            _refuse(
                f"target app missing or not active "
                f"(status={None if target is None else target.get('status')!r})",
                isolate=None,
            )

        # Check 2 — same non-null workspaceId.
        src_ws = source.get("workspace_id")
        tgt_ws = target.get("workspace_id")
        if not src_ws or not tgt_ws or src_ws != tgt_ws:
            # The SOURCE attempted to reach a foreign workspace — it's
            # the offender. Auto-isolate to freeze it.
            _refuse(
                f"workspace mismatch (source={src_ws!r} target={tgt_ws!r})",
                isolate=source_app_id,
            )

        # Check 3 — source must hold `filesystem.write`.
        try:
            PERMISSION_GATE.check(source_app_id, "filesystem.write")
        except (ScopeViolation, AppIsolated, AppNotFound) as exc:
            _refuse(
                f"source lacks filesystem.write: {exc}",
                isolate=source_app_id,
            )

        # Check 4 — target must hold `filesystem.read`.
        try:
            PERMISSION_GATE.check(target_app_id, "filesystem.read")
        except (ScopeViolation, AppIsolated, AppNotFound) as exc:
            _refuse(
                f"target lacks filesystem.read: {exc}",
                # Target is at fault for not declaring it accepts
                # inbound files; isolate target instead.
                isolate=target_app_id,
            )

        # Check 5 — path containment on the source. _resolve_inside_sandbox
        # already auto-isolates on traversal, so we just let it raise
        # PathTraversalAttempt and the run loop catches.
        source_path = _resolve_inside_sandbox(source_app_id, virtual_file_path)
        if not source_path.is_file():
            _refuse(
                f"source file missing: {virtual_file_path!r}",
                isolate=None,
            )

        # Copy bytes into target/shared_inputs/<basename>.
        import os

        target_rel = (
            f"{self.SHARED_INPUTS_DIRNAME}/{os.path.basename(virtual_file_path)}"
        )
        target_path = _resolve_inside_sandbox(target_app_id, target_rel)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        data = source_path.read_bytes()
        target_path.write_bytes(data)

        logger.info(
            "[orch] handoff %s → %s bytes=%d path=%s",
            source_app_id,
            target_app_id,
            len(data),
            virtual_file_path,
        )
        return {
            "source_app_id": source_app_id,
            "target_app_id": target_app_id,
            "source_path": str(source_path),
            "target_path": str(target_path),
            "bytes_copied": len(data),
        }

    # --- status -------------------------------------------------------

    def run_status(self, run_id: str) -> dict:
        with get_session() as session:
            run = session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id)
            ).scalar_one_or_none()
            if run is None:
                raise KeyError(f"run_id={run_id!r}")
            # P6.1 — verify the signature on every status read so a
            # tamper after submit but BEFORE execute still surfaces.
            # Already-failed runs short-circuit — no need to re-fail.
            if run.status != "failed" and not _verify_workflow_row(run):
                run.status = "failed"
                run.error = "workflow signature mismatch — auto-quarantined"
                run.updatedAt = _now_ms()
                session.commit()
                _record_security_event(
                    kind="workflow_tampered",
                    reason="signature mismatch on run_status",
                    details={
                        "run_id": run.id,
                        "workspace_id": run.workspaceId,
                    },
                )
            steps = (
                session.execute(
                    select(WorkflowStep).where(WorkflowStep.runId == run_id)
                )
                .scalars()
                .all()
            )
            return {
                "run_id": run.id,
                "name": run.name,
                "workspace_id": run.workspaceId,
                "status": run.status,
                "error": run.error,
                "manifest": json.loads(run.manifest_json),
                "createdAt": run.createdAt,
                "updatedAt": run.updatedAt,
                "steps": [
                    {
                        "id": s.id,
                        "step_id": s.stepId,
                        "app_id": s.appId,
                        "depends_on": json.loads(s.dependsOn_json or "[]"),
                        "status": s.status,
                        "task": json.loads(s.task_json or "{}"),
                        "output": (
                            json.loads(s.output_json) if s.output_json else None
                        ),
                        "stderr": s.stderr,
                        "error": s.error,
                        "createdAt": s.createdAt,
                        "updatedAt": s.updatedAt,
                    }
                    for s in steps
                ],
            }

    # --- helpers ------------------------------------------------------

    def _load(self, run_id: str) -> tuple:
        with get_session() as session:
            run = session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id)
            ).scalar_one_or_none()
            if run is None:
                raise KeyError(f"run_id={run_id!r}")
            # P6.1 — verify the signature BEFORE we trust the
            # manifest_json. On mismatch: quarantine the run,
            # record a tamper audit row, raise.
            if not _verify_workflow_row(run):
                run.status = "failed"
                run.error = "workflow signature mismatch — auto-quarantined"
                run.updatedAt = _now_ms()
                session.commit()
                _record_security_event(
                    kind="workflow_tampered",
                    reason="signature mismatch on _load",
                    details={
                        "run_id": run.id,
                        "workspace_id": run.workspaceId,
                    },
                )
                raise WorkflowTampered(run.id)
            return json.loads(run.manifest_json), run

    def _set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> None:
        with get_session() as session:
            run = session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id)
            ).scalar_one_or_none()
            if run is None:
                return
            run.status = status
            if error is not None:
                run.error = error
            run.updatedAt = _now_ms()
            session.commit()

    def _set_step_status(
        self,
        run_id: str,
        step_id: str,
        status: str,
        *,
        output: Optional[dict] = None,
        error: Optional[str] = None,
        stderr: Optional[str] = None,
    ) -> None:
        with get_session() as session:
            row = session.execute(
                select(WorkflowStep).where(
                    WorkflowStep.runId == run_id, WorkflowStep.stepId == step_id
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if output is not None:
                row.output_json = json.dumps(output, separators=(",", ":"))
            if error is not None:
                row.error = error
            if stderr is not None:
                row.stderr = stderr
            row.updatedAt = _now_ms()
            session.commit()


ORCHESTRATOR = SovereignOrchestrator()


__all__ = [
    "HandoffViolation",
    "ORCHESTRATOR",
    "SovereignOrchestrator",
    "WorkflowRunHandle",
    "WorkflowTampered",
    "WorkflowValidationError",
    "_topological_order",
]
