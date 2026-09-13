"""
backend/core/database/sqlite_setup.py — SQLite schema + engine.

W5.1 — mirrors the Convex tables declared in
`frontend/convex/schema.ts` for offline-first community profiles. The
six tables ported here are the ones surfaced by the existing
repositories layer (W3.2d/W4.1):

  - users                (mirrors users           in schema.ts:8)
  - projects             (mirrors projects        in schema.ts:235)
  - builds               (mirrors builds          in schema.ts:586)
  - chatSessions         (mirrors chatSessions    in schema.ts:297)
  - chatSessionMessages  (mirrors chatSessionMessages, schema.ts:308)
  - appInstallations     (mirrors appInstallations, schema.ts:422)

Convex idioms mapped to SQLAlchemy:

  v.string()       → String (TEXT)
  v.number()       → Integer for timestamps/counts, Float for sums
  v.boolean()      → Boolean
  v.record(...)    → JSON column (Text serialized as JSON)
  v.array(...)     → JSON column
  v.id("table")    → String (TEXT) — opaque IDs to keep cross-backend
                     parity with Convex's string IDs

Database path resolution:
  $VOS3_LOCAL_DB_PATH         — absolute override (used by tests).
  $HOME/.vos/vos3.db          — default for local-first profile.

The parent directory is created on init if missing. SQLite is opened
with `check_same_thread=False` so threadpool dispatch from async repos
is safe (writes are still serialized by SQLite's per-connection lock).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all VOS3 local tables."""

    pass


# ---------------------------------------------------------------------------
# Tables — names match the Convex schema for cross-backend parity.
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    # Convex auto-generates `_id` strings; we mirror with TEXT primary
    # keys so a future Convex → SQLite sync can preserve IDs verbatim.
    id = Column(String, primary_key=True)
    clerkId = Column(String, nullable=False, unique=True, index=True)
    email = Column(String, nullable=False, index=True)
    fullName = Column(String, nullable=True)
    avatarUrl = Column(String, nullable=True)
    lastSignInAt = Column(Integer, nullable=True)  # ms since epoch
    metadata_json = Column("metadata", Text, nullable=True)  # JSON record
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    ownerId = Column(String, nullable=False, index=True)  # users.id
    organizationId = Column(String, nullable=True, index=True)
    metadata_json = Column("metadata", Text, nullable=True)
    isArchived = Column(Boolean, nullable=False, default=False)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)
    # P3.3 — sync journaling. `dirty` flips to True on every write
    # and back to False once SovereignSyncEngine has pushed the row
    # to Convex. `lastSyncedAt` stores ms since epoch (matching the
    # rest of the schema's timestamp convention — kept as Integer
    # rather than DateTime so cross-row comparisons stay scalar).
    dirty = Column(Boolean, nullable=False, default=True, index=True)
    lastSyncedAt = Column(Integer, nullable=True)


class Build(Base):
    __tablename__ = "builds"

    id = Column(String, primary_key=True)
    projectId = Column(String, nullable=False, index=True)  # projects.id
    userId = Column(String, nullable=False, index=True)  # users.id
    status = Column(String, nullable=False)
    requirements = Column(Text, nullable=False)
    totalCost = Column(Float, nullable=True)
    totalTokens = Column(Integer, nullable=True)
    totalDuration = Column(Integer, nullable=True)
    costBreakdown = Column(Text, nullable=True)  # JSON
    filesGenerated = Column(Integer, nullable=True)
    securityScore = Column(Float, nullable=True)
    completenessScore = Column(Float, nullable=True)
    errorMessage = Column(Text, nullable=True)
    vos3Metadata = Column(Text, nullable=True)  # JSON
    createdAt = Column(Integer, nullable=False)


class ChatSession(Base):
    __tablename__ = "chatSessions"

    id = Column(String, primary_key=True)
    userId = Column(String, nullable=False, index=True)
    sessionId = Column(String, nullable=False, unique=True, index=True)
    messageCount = Column(Integer, nullable=True, default=0)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)
    # P3.3 — sync journaling (see Project.dirty comment).
    dirty = Column(Boolean, nullable=False, default=True, index=True)
    lastSyncedAt = Column(Integer, nullable=True)


class ChatSessionMessage(Base):
    __tablename__ = "chatSessionMessages"

    id = Column(String, primary_key=True)
    sessionId = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    timestamp = Column(Integer, nullable=False, index=True)
    metadata_json = Column("metadata", Text, nullable=True)  # JSON
    # P3.3 — sync journaling.
    dirty = Column(Boolean, nullable=False, default=True, index=True)
    lastSyncedAt = Column(Integer, nullable=True)


class AppInstallation(Base):
    __tablename__ = "appInstallations"

    id = Column(String, primary_key=True)
    appId = Column(String, nullable=False, index=True)
    organizationId = Column(String, nullable=False, index=True)
    installedBy = Column(String, nullable=False)
    version = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    grantedScopes = Column(Text, nullable=False)  # JSON array
    config = Column(Text, nullable=True)  # JSON record
    installedAt = Column(Integer, nullable=False)


class EventSubscription(Base):
    """P4.5 — durable subscription record.

    The in-memory `SovereignEventBus._by_topic` cache is rebuilt
    from this table on backend startup so subscriptions survive
    restarts. Tests can wipe between runs via `_reset_for_tests`.
    """

    __tablename__ = "eventSubscriptions"

    id = Column(String, primary_key=True)
    appId = Column(String, nullable=False, index=True)
    topic = Column(String, nullable=False, index=True)
    # Endpoint scheme:
    #   "inproc:<key>"   - in-process Python callable (first-party / tests)
    #   "http://..."     - POST envelope to URL (warm webhook)
    #   "exec:<path>"    - spawn the entrypoint inside the app sandbox
    #                       with envelope on stdin (cold app)
    callbackEndpoint = Column(String, nullable=False)
    createdAt = Column(Integer, nullable=False)


class EventLog(Base):
    """P4.5 — append-only journal of every event the bus emits.

    Useful for audit, replay (Phase 5), and unit-test introspection.
    The bus persists BEFORE dispatch so a crash mid-fanout doesn't
    silently lose the event from the historical record.
    """

    __tablename__ = "events"

    id = Column(String, primary_key=True)  # event_id (uuid)
    topic = Column(String, nullable=False, index=True)
    payload_json = Column("payload", Text, nullable=False)
    originAppId = Column(String, nullable=True, index=True)
    timestamp = Column(Integer, nullable=False, index=True)


class WorkflowRun(Base):
    """P6.0 — top-level orchestrator run record.

    One row per `submit_workflow()` invocation. Steps live in the
    sibling table `workflowSteps` keyed by `runId`. `status` walks
    `pending → running → completed | failed`.

    P6.1 — `signature` is a hex-encoded Ed25519 signature over the
    canonical IMMUTABLE fields (id, workspaceId, manifest_json,
    createdAt). Verified on every load; tamper → run auto-fails.
    """

    __tablename__ = "workflowRuns"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    workspaceId = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    manifest_json = Column("manifest", Text, nullable=False)
    error = Column(Text, nullable=True)
    # P6.1 — Ed25519 signature; verifier in agent_orchestrator.
    signature = Column(String, nullable=True)
    signing_public_key = Column(String, nullable=True)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)


class WorkflowStep(Base):
    """P6.0 — per-step record under a workflow run.

    `stepId` is the human-readable id from the manifest (e.g.
    "translate"); `id` is a UUID4 row key. Outputs and stderr are
    captured as text so the status route can stream them back to
    the UI without re-reading the subprocess.
    """

    __tablename__ = "workflowSteps"

    id = Column(String, primary_key=True)
    runId = Column(String, nullable=False, index=True)
    stepId = Column(String, nullable=False)
    appId = Column(String, nullable=False, index=True)
    dependsOn_json = Column("dependsOn", Text, nullable=False)  # JSON list
    status = Column(String, nullable=False, default="pending")
    task_json = Column("task", Text, nullable=False)
    output_json = Column("output", Text, nullable=True)
    stderr = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)


class SecurityAuditLog(Base):
    """P5.1 — append-only audit trail of every PermissionGate denial,
    path-traversal attempt, and lifecycle status change.

    The dashboard's `SovereignAuditTrail.tsx` streams from this table.
    The bus / repo / dispatcher don't need to know they're being
    audited — failures bubble through helpers that call
    `_record_security_event()` right before raising.

    The schema is deliberately wide so a single row encodes the full
    "who tried what against whom" context without joining other
    tables.
    """

    __tablename__ = "securityAuditLog"

    id = Column(String, primary_key=True)
    timestamp = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    # Examples: "scope_violation", "path_traversal_attempt",
    #           "app_isolated", "app_not_found",
    #           "scope_toggled", "app_status_change"
    appId = Column(String, nullable=True, index=True)
    scope = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    # Optional structured details — kept JSON so we don't bloat the
    # schema for every future security signal.
    details_json = Column("details", Text, nullable=True)


class AppState(Base):
    """P4.6 — per-app key/value config + lightweight state.

    Composite PK on (appId, key) gives every app its own keyspace
    — even with a SQL injection bug elsewhere, App A can never
    overwrite App B's "theme" without supplying App B's id, which
    the route layer rejects via `_require_matching_app`.

    The on-disk row carries the P3.3 sync columns so app state
    rides the same cloud-sync pipeline as projects / chatSessions.
    """

    __tablename__ = "appState"

    appId = Column(String, primary_key=True, index=True)
    key = Column(String, primary_key=True, index=True)
    value_json = Column("value", Text, nullable=False)
    updatedAt = Column(Integer, nullable=False)
    # P3.3 sync journal — every write re-marks dirty=True.
    dirty = Column(Boolean, nullable=False, default=True, index=True)
    lastSyncedAt = Column(Integer, nullable=True)


class PendingHostApproval(Base):
    """P6.3 — Transaction Guard pending-approval queue.

    Every high-value or rate-limited host-bridge transaction lands
    here BEFORE the executor runs. The HTTP poll endpoint reads
    rows where status='awaiting_human_approval'; the operator
    approval route flips the row to 'approved' (and stores the
    Ed25519 signature over the canonical payload) or 'rejected'.

    Schema fields:
      id              opaque approval id (UUID)
      appId           sandboxed caller (FK to apps.id)
      workspaceId     workspace tenancy — must match the app's
      targetSystem    e.g. "SAP_GUI", "SAP_S4HANA_CLOUD"
      action          e.g. "create_purchase_order"
      payload_json    serialized script_payload (audited verbatim)
      amount          numeric value extracted from payload (optional)
      currency        ISO-4217 (optional)
      reason          why the guard intercepted (threshold/rate-limit)
      status          awaiting_human_approval | approved | rejected
                      | executed | cancelled
      approverId      filled when operator approves/rejects
      signature       hex Ed25519 signature over canonical fields
      signing_public_key
                      operator's identity key (re-uses P6.1 keypair)
      result_json     executor's return value once status='executed'
      createdAt / updatedAt — ms-epoch
    """

    __tablename__ = "pendingHostApprovals"

    id = Column(String, primary_key=True)
    appId = Column(String, nullable=False, index=True)
    workspaceId = Column(String, nullable=False, index=True)
    targetSystem = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    payload_json = Column("payload", Text, nullable=False)
    amount = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    status = Column(
        String, nullable=False, default="awaiting_human_approval", index=True
    )
    approverId = Column(String, nullable=True)
    signature = Column(String, nullable=True)
    signing_public_key = Column(String, nullable=True)
    result_json = Column("result", Text, nullable=True)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)


class DiscoveredPeer(Base):
    """P6.2 — durable record of a peer node observed on the local LAN.

    Populated by `LocalPeerDiscovery` when a beacon arrives and its
    Ed25519 signature verifies. The composite `(workspaceId, nodeId)`
    index lets the sync engine ask "give me every peer in my workspace"
    in O(log n).

    Status semantics:
      * 'active'   — beacon verified, workspaceId matches local, peer
                     is eligible for a sync session.
      * 'rejected' — beacon verified BUT workspaceId differs from the
                     local node's. Kept in the table so the dashboard
                     can render the malicious-attempt history.
      * 'stale'    — last beacon arrived > stale_threshold ms ago.
                     Discovery service flips status on its eviction
                     pass; the row stays so we can show "last seen at
                     ${timestamp}" in the UI.

    `verified` mirrors the on-write signature check — kept as a column
    so a downstream query can filter to "trustworthy peers only"
    without re-running crypto.
    """

    __tablename__ = "discoveredPeers"

    id = Column(String, primary_key=True)
    nodeId = Column(String, nullable=False, index=True)
    workspaceId = Column(String, nullable=False, index=True)
    host = Column(String, nullable=False)
    syncPort = Column(Integer, nullable=False)
    publicKeyHex = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active", index=True)
    verified = Column(Boolean, nullable=False, default=False)
    lastSeenAt = Column(Integer, nullable=False, index=True)
    firstSeenAt = Column(Integer, nullable=False)


class App(Base):
    """P4.1 — third-party app registry for the Sovereign App Runtime.

    Distinct from `AppInstallation` (which is a per-organization
    install record mirroring the Convex `appInstallations` table).
    `apps` is the local registry of "what apps exist on this device
    and what sandbox they run in" — it owns the manifest + lifecycle
    state. `appInstallations` owns per-org tenancy.

    Schema fields (per BEGIN_PHASE_4.1_APP_SANDBOX_MANAGER):
      id            : opaque app identifier (UUID)
      name          : human-readable label
      version       : semver string from the manifest
      manifest_json : the full app manifest as JSON text. Includes
                      `scopes` (positive grants) and `restrictions`
                      (explicit blocks like "network.blocked").
      status        : "active" | "isolated" | "disabled".
                        - active   = manifest accepted, sandbox up,
                                     PermissionGate enforcing.
                        - isolated = manifest rejected / app frozen
                                     after a scope-violation event;
                                     every gate check denies.
                        - disabled = operator-paused but not frozen.
    """

    __tablename__ = "apps"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    version = Column(String, nullable=False)
    manifest_json = Column("manifest", Text, nullable=False)
    status = Column(String, nullable=False, default="active", index=True)
    # P4.2 — hex-encoded SHA-256 of the app's bearer secret. The
    # plaintext is returned exactly once at install time; afterwards
    # this column is the only thing the runtime compares against.
    # Nullable so a manifest installed for in-process use (e.g.
    # internal vOS apps) can opt out of the X-App-Secret check.
    secret_hash = Column(String, nullable=True)
    # P6.0 — workspace tenancy for the orchestrator's `secure_handoff`.
    # Apps in the SAME `workspaceId` can transfer files between
    # their sandboxes via the Custodian; cross-workspace handoff
    # is hard-blocked. Nullable because dev / first-party installs
    # don't need to participate in handoffs.
    workspaceId = Column(String, nullable=True, index=True)
    # P5.2 — cryptographic Chain of Trust.
    # `signature` is the Ed25519 signature (hex-encoded) over the
    # SHA-256 of the canonical manifest bytes (see app_crypto.py).
    # `developer_public_key` is the corresponding hex-encoded Ed25519
    # raw public key (32 bytes). Both are nullable so dev-mode
    # installs (no signing key on the host) still work; production
    # installs MUST provide both, and every `PermissionGate.check`
    # re-verifies the signature against the persisted manifest_json.
    signature = Column(String, nullable=True)
    developer_public_key = Column(String, nullable=True)
    createdAt = Column(Integer, nullable=False)
    updatedAt = Column(Integer, nullable=False)
    # P3.3 sync journal — same convention as projects/chatSessions.
    dirty = Column(Boolean, nullable=False, default=True, index=True)
    lastSyncedAt = Column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Engine / session — singletons so repeated calls don't re-open the file.
# ---------------------------------------------------------------------------


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def resolve_db_path() -> Path:
    """Resolve the SQLite file path.

    Precedence:
      1. $VOS3_LOCAL_DB_PATH (absolute path; used by tests for tmp files)
      2. ~/.vos/vos3.db      (default for local-first profile)
    """
    override = os.getenv("VOS3_LOCAL_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".vos" / "vos3.db"


def resolve_app_data_dir() -> Path:
    """Return the OS-canonical vOS data directory.

    P4.3 — third-party apps get an isolated subtree under this root
    at `apps/<app_id>/storage/`. Resolution order:

      1. `$VOS3_APP_DATA_DIR` — explicit operator override.
      2. Parent of `$VOS3_LOCAL_DB_PATH` — the Tauri sidecar pins
         the SQLite + Chroma paths under the canonical app_data_dir
         (see W6.4 sidecar.rs::apply_env). If those are set, the
         dirname IS the app_data_dir.
      3. `~/.vos/` — default for non-Tauri / non-sidecar deployments.

    The returned path is `.resolve()`-d so subsequent comparisons
    (path confinement, traversal checks) operate on a single
    canonical form regardless of symlinks in the caller's input.
    """
    explicit = os.getenv("VOS3_APP_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    db = os.getenv("VOS3_LOCAL_DB_PATH", "").strip()
    if db:
        return Path(db).expanduser().parent.resolve()

    return (Path.home() / ".vos").resolve()


def get_engine() -> Engine:
    """Return the singleton SQLAlchemy engine, creating it on first call.

    The connection is shared across threads (check_same_thread=False) so
    threadpool-dispatched async repos can use the same engine without
    raising. SQLite serializes writes internally per connection.

    P3.2 — when fortress mode is active (`VOS_PROFILE=fortress` or
    `VOS3_COMPLIANCE_KEY` set), the engine is built by the dedicated
    `sqlcipher_setup.make_encrypted_engine()` factory instead of the
    plain SQLite path. Fortress mode strictly refuses to silently
    write plaintext — a missing driver raises, it does not downgrade.
    """
    global _engine
    if _engine is None:
        path = resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        # Lazy import: don't pay the cost of probing for an SQLCipher
        # driver on plain-SQLite community deployments.
        from core.database.sqlcipher_setup import (
            fortress_active,
            make_encrypted_engine,
        )

        if fortress_active():
            _engine = make_encrypted_engine(path)
        else:
            _engine = create_engine(
                f"sqlite:///{path}",
                connect_args={"check_same_thread": False},
                future=True,
            )
    return _engine


def get_session() -> Session:
    """Return a fresh sync session bound to the singleton engine.

    Callers are responsible for `session.close()` (or use `with` —
    the SQLAlchemy Session is a context manager).
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db() -> None:
    """Create any missing tables. Idempotent.

    Safe to call on every backend boot under VOS3_LOCALITY_PREFERENCE=
    local-first. No-op if all tables already exist.
    """
    Base.metadata.create_all(get_engine())


def _reset_for_tests() -> None:
    """Reset the singleton — used by tests that swap VOS3_LOCAL_DB_PATH."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
