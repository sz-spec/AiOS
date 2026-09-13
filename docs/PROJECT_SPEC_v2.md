# VOS3 Project Specification v2.0

This specification updates VOS3 to remediate identified **security** (CORS + password hashing + auth hardening) and **persistence** (in-memory state loss) issues while respecting **patch-only** constraints and preserving current behavior.

---

## 1. Security Requirements

### 1.1 CORS Configuration

**Objective:** Replace permissive `allow_origins=["*"]` with an explicit, environment-driven whitelist.

#### Requirements
1. **Origin Whitelist (No `"*"` in production)**
   - CORS origins must be explicitly listed.
   - Support multiple origins via a comma-separated environment variable.

2. **Environment Variables**
   - `VOS3_CORS_ORIGINS` (string)
     - Example: `https://app.example.com,https://admin.example.com`
     - In development only, may default to `http://localhost:3000,http://localhost:5173`
   - `VOS3_ENV` (enum string): `development | staging | production`
     - In `production`, startup must **fail fast** if `VOS3_CORS_ORIGINS` is empty or contains `"*"`.

3. **Explicit Methods and Headers**
   - `allow_methods` must be explicitly enumerated (no `"*"`).
   - `allow_headers` must be explicitly enumerated (no `"*"`).

#### Allowed Methods (explicit)
- `GET, POST, PUT, PATCH, DELETE, OPTIONS`

#### Allowed Headers (explicit)
- `Authorization`
- `Content-Type`
- `Accept`
- `Origin`
- `X-Requested-With`
- `X-Request-ID`
- `X-API-Key`

---

### 1.2 Password Security

**Objective:** Replace SHA256 password hashing with a modern password hashing scheme and define password policy.

#### Requirements
1. **Password Hashing Algorithm**
   - Must use **Argon2id** (preferred) or **bcrypt** (acceptable fallback).
   - **Must NOT** use raw SHA256 for passwords.

2. **Work Factor / Cost Parameters**
   - **Argon2id (preferred)**:
     - `time_cost`: 2–4 (default 3)
     - `memory_cost`: 64–256 MB (default 128 MB)
     - `parallelism`: 1–4 (default 2)
   - **bcrypt (fallback)**:
     - cost/rounds: 12–14 (default 12)

3. **Password Policy**
   - Minimum length: **12**
   - Must include at least **3 of 4** categories:
     - lowercase letter
     - uppercase letter
     - digit
     - symbol
   - Reject passwords found in a short denylist (configurable) if available; otherwise enforce complexity only.

4. **Verification**
   - Password verification must use constant-time comparison provided by the hashing library.

5. **API Key Hashing (Allowed Use of SHA256)**
   - API keys are **high entropy**, so SHA256 is acceptable.
   - Store only a hash in persistence:
     - `api_key_hash = sha256(pepper + raw_key)` where:
       - `pepper` comes from `VOS3_API_KEY_PEPPER` (required in staging/production)
   - Never log raw API keys.

#### Environment Variables
- `VOS3_PASSWORD_HASHER`: `argon2id | bcrypt` (default `argon2id`)
- `VOS3_API_KEY_PEPPER`: required in `staging|production`

---

### 1.3 Authentication Hardening

**Objective:** Ensure development shortcuts cannot be enabled in production and define a public endpoint policy.

#### Requirements
1. **DEV_MODE Handling**
   - Any `DEV_MODE` / debug bypass features must be **disabled in production**.
   - If `VOS3_ENV=production` and `DEV_MODE=true`, the application must **refuse to start**.

2. **Public Endpoint Policy**
   - Public endpoints must be explicitly enumerated (deny-by-default).
   - At minimum:
     - `GET /health` is public
     - `/docs` and `/openapi.json` public only in `development`
   - All other endpoints require authentication unless explicitly listed as public.

3. **Audit Logging**
   - Authentication and key issuance events must write to `audit_logs` with:
     - `actor_id` (nullable for anonymous)
     - action type (e.g., `user.create`, `auth.login`, `api_key.create`)
     - timestamp and metadata

---

## 2. Data Persistence Requirements

### 2.1 Storage Migration

**Objective:** Replace ControlPlaneService’s in-memory dictionaries with a repository-backed persistence layer (Convex in production), while retaining in-memory storage for tests.

#### Requirements
1. **Repository Pattern**
   - Introduce repository interfaces for:
     - Users
     - Organizations
     - Memberships
     - API Keys
     - Audit Logs
     - Invitations (if required by current flows)
   - ControlPlaneService must depend on interfaces, not concrete storage.

2. **Production Backend**
   - Production persistence must be **Convex** (existing `backend/db/convex.py` + schema).
   - Storage backend selection via environment variable:
     - `VOS3_STORAGE_BACKEND=memory|convex`
     - Default: `memory` in tests, `convex` in staging/production.

3. **In-Memory Backend**
   - Allowed **only** for:
     - Unit tests
     - Local development
   - Must not be the default in production deployments.

---

### 2.2 Critical Tables

**Objective:** Ensure the following entities persist across restarts and are queryable for auth/security.

#### Must Persist (minimum)
- `users`
- `organizations`
- `memberships`
- `api_keys`
- `audit_logs`

#### Additionally Required if currently used in flows
- `roles` (or keep system roles in code but persist assignments if applicable)
- `invitations`

#### Business Entities & Records
- Any “business entity” objects currently held in memory must be mapped to tables (or explicitly documented as ephemeral).
- If the system currently creates domain records via ControlPlaneService, those records must be persisted or the endpoint behavior must be changed to clearly indicate non-persistence (not recommended).

---

### 2.3 Migration Strategy

**Objective:** Zero-downtime migration from in-memory to Convex persistence.

#### Phases
1. **Phase 0 – Prepare**
   - Ensure schema is applied to the target database.
   - Add missing columns if needed (see below).

2. **Phase 1 – Dual-Write, Read-From-Memory (safe rollout)**
   - Writes go to both:
     - in-memory store (existing dicts) AND
     - Convex repositories
   - Reads default to in-memory, with optional Convex fallback for cache misses.

3. **Phase 2 – Read-From-Convex**
   - Flip default reads to Convex.
   - Keep dual-write briefly to support rollback.

4. **Phase 3 – Remove Dual-Write**
   - Disable in-memory persistence in non-test environments.
   - Keep in-memory repositories only for unit tests.

#### Data Validation Requirements
- On startup (staging/production), validate:
  - required tables exist
  - required columns exist
  - `VOS3_API_KEY_PEPPER` set
- During migration, run reconciliation checks:
  - counts per table
  - spot-check user IDs and membership relations
  - verify API key hashes are populated (never raw keys)

#### Schema Adjustments (if not already present)
- `users.password_hash` must support modern hash strings (TEXT recommended).
- `api_keys.api_key_hash` (TEXT) and **never store raw key**.
- `audit_logs` indexed by `created_at` and optionally `actor_id`.

---

## 3. Implementation Constraints

### 3.1 Patch-Only Changes

**Objective:** Implement changes via targeted edits only.

#### Constraints
- **NO full file rewrites**.
- Use **targeted edits to specific line ranges**.
- Preserve existing behavior and public APIs wherever possible.
- Maintain backward compatibility:
  - Existing endpoints should keep request/response shapes.
  - Existing user creation flows should still work (but now hash securely and persist).

> Note: `backend/src/efficiency.py` is acknowledged as a “God Module” but is **out of scope** for this security/persistence patch unless a direct dependency is required for persistence/auth changes.

---

### 3.2 Testing Requirements

#### Unit Tests (required)
- CORS origin parsing/validation:
  - rejects `"*"` in production
  - accepts explicit allowed origins
- Password hashing:
  - hash + verify works
  - policy enforcement rejects weak passwords
- API key hashing:
  - peppered SHA256 stable for same inputs
  - raw keys never stored/logged (test via repository calls)

#### Integration Tests (required)
- Convex persistence:
  - create user -> restart simulation -> user still exists
  - create org/membership -> persists
  - create API key -> stored as hash, lookup/verify works
- Auth hardening:
  - production startup fails with DEV_MODE enabled
  - public endpoint access rules enforced

#### Rollback Procedures
- Toggle `VOS3_STORAGE_BACKEND=memory` to revert to prior behavior temporarily.
- During dual-write phases, rollback must not lose writes (because memory+Convex both updated).

---

## 4. File-Specific Changes

All changes below must be implemented as **small patches** at the specified locations.

---

### 4.1 `backend/main.py` — CORS Hardening

#### Lines to modify
- **Current**: lines **194–201** (CORS middleware configuration)

#### Before
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # PROBLEM: Allows any origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### After (patched)
```python
# NEW: load allowed origins from env and validate based on environment
from backend.core.settings import get_settings  # add import near other imports

settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Request-ID",
        "X-API-Key",
    ],
)
```

#### New dependency / file
- Add **new**: `backend/core/settings.py` (small new module; no rewrites elsewhere)

**`backend/core/settings.py` (new file) requirements**
- Reads:
  - `VOS3_ENV`
  - `VOS3_CORS_ORIGINS`
  - `DEV_MODE`
  - `VOS3_STORAGE_BACKEND`
  - `VOS3_PASSWORD_HASHER`
  - `VOS3_API_KEY_PEPPER`
- Provides `get_settings()` singleton.
- Validates:
  - In production: `cors_origins` must be non-empty and must not contain `"*"`
  - In production: `DEV_MODE` must be false
  - In staging/production: `VOS3_API_KEY_PEPPER` required

#### Dependencies to add (Python)
- `pydantic-settings` (or `pydantic` Settings if already used)
  - If minimizing dependencies is preferred: implement with `os.getenv` + manual validation.

---

### 4.2 `backend/core/control_plane.py` — Password Hashing + Persistence

#### A) Replace in-memory-only stores with repository-backed access (incremental)

##### Lines to modify
- **Current**: lines **366–373** (`ControlPlaneService.__init__`)

##### Before
```python
class ControlPlaneService:
    def __init__(self):
        self._users: dict[str, User] = {}  # PROBLEM: In-memory, lost on restart
        self._organizations: dict[str, Organization] = {}
        self._memberships: dict[str, OrganizationMember] = {}
        self._roles: dict[str, Role] = {r.id: r for r in SYSTEM_ROLES.values()}
        self._invitations: dict[str, Invitation] = {}
        self._audit_logs: list[AuditLog] = []
        self._api_keys: dict[str, ApiKey] = {}
```

##### After (patched; keep memory for tests + migration)
```python
from backend.core.settings import get_settings
from backend.db.repositories import (
    UserRepository,
    OrganizationRepository,
    MembershipRepository,
    ApiKeyRepository,
    AuditLogRepository,
    get_repositories,
)

class ControlPlaneService:
    def __init__(self):
        self._roles: dict[str, Role] = {r.id: r for r in SYSTEM_ROLES.values()}

        # Keep in-memory structures for tests and/or dual-write migration window
        self._users: dict[str, User] = {}
        self._organizations: dict[str, Organization] = {}
        self._memberships: dict[str, OrganizationMember] = {}
        self._invitations: dict[str, Invitation] = {}
        self._audit_logs: list[AuditLog] = []
        self._api_keys: dict[str, ApiKey] = {}

        settings = get_settings()
        self._repos = get_repositories(settings)
        self._storage_backend = settings.storage_backend
```

##### New files required
- `backend/db/repositories/__init__.py`
- `backend/db/repositories/interfaces.py`
- `backend/db/repositories/memory.py`
- `backend/db/repositories/convex.py`

**Repository interface requirements (minimum methods)**
- Users:
  - `create(user: User) -> User`
  - `get_by_id(user_id: str) -> Optional[User]`
  - `get_by_email(email: str) -> Optional[User]`
- Organizations / Memberships:
  - `create`, `get_by_id`, `list_by_user`, etc. (as required by current ControlPlane methods)
- API keys:
  - `create(api_key: ApiKey) -> ApiKey` (hash-only)
  - `get_by_hash(hash: str) -> Optional[ApiKey]`
- Audit logs:
  - `append(log: AuditLog) -> AuditLog`
  - `list(...)` if currently used

**Convex repository requirements**
- Use existing `backend/db/convex.py` client.
- Implement CRUD via Convex HTTP API calls, consistent with existing schema.

---

#### B) Fix password hashing in user creation

##### Lines to modify
- **Current**: lines **405–410** (`create_user`)

##### Before
```python
def create_user(self, email: str, name: str, password: Optional[str] = None, ...):
    user = User(email=email, name=name, sso_provider=sso_provider, sso_id=sso_id)
    if password:
        user.password_hash = hashlib.sha256(password.encode()).hexdigest()  # PROBLEM: Weak hashing
    self._users[user.id] = user  # PROBLEM: In-memory storage
    return user
```

##### After (patched; secure hashing + persistence + optional dual-write)
```python
from backend.core.security import hash_password, validate_password_policy

def create_user(self, email: str, name: str, password: Optional[str] = None, ...):
    user = User(email=email, name=name, sso_provider=sso_provider, sso_id=sso_id)

    if password:
        validate_password_policy(password)
        user.password_hash = hash_password(password)

    # Dual-write during migration (memory + repo). In memory-only mode, repo is memory repo.
    self._users[user.id] = user
    self._repos.users.create(user)

    return user
```

##### New file required
- `backend/core/security.py`

**`backend/core/security.py` requirements**
- `hash_password(password: str) -> str`
  - Uses Argon2id (preferred) or bcrypt per `VOS3_PASSWORD_HASHER`
- `verify_password(password: str, stored_hash: str) -> bool`
- `validate_password_policy(password: str) -> None`
  - Raises a specific exception consumed by API layer (keep existing exception style)

##### Dependencies to add (Python)
- Preferred:
  - `argon2-cffi` (Argon2id)
- Acceptable alternative:
  - `passlib[bcrypt]` (bcrypt)
- Choose one primary to minimize footprint; spec preference is Argon2id.

---

### 4.3 `backend/db/convex.py` — Activate and Standardize Convex Client Usage

#### Change scope
- No rewrite; only ensure the module exports a reusable client used by `repositories/convex.py`.

#### Required behaviors
- Validate required env vars in staging/production:
  - `CONVEX_URL`
  - `CONVEX_DEPLOY_KEY`
- Provide a function:
  - `get_convex_client()`

#### Dependencies
- If not already included:
  - `httpx` (for Convex HTTP API)

---

### 4.4 `frontend/convex/schema.ts` — Confirm/Adjust Fields for Secure Hash Storage

#### Change scope
- Patch only if schema lacks these fields or types.

#### Required schema capabilities
- `users.passwordHash` as `v.optional(v.string())` (stores Argon2/bcrypt encoded hash strings)
- `apiKeys.apiKeyHash` as `v.string()` (stores SHA256 hex)
- `auditLog` table with `_creationTime` indexed (automatic in Convex)

---

### 4.5 `backend/src/efficiency.py` — No Changes Required (Explicitly Out of Scope)

#### Current note
- This module is a “God Module” and should be refactored later, but **must not be modified** as part of this security/persistence patch unless a direct compilation/import break occurs due to new settings modules.

---

## Dependency Summary (Additions)

Add to backend requirements (exact package depends on chosen approach):

1. Settings (choose one):
   - `pydantic-settings` (recommended), or implement manual `os.getenv` parsing with no dependency.

2. Password hashing (choose one primary):
   - Preferred: `argon2-cffi`
   - Or: `passlib[bcrypt]`

3. Convex client (if not already installed/consistent):
   - `httpx`

---

## Acceptance Criteria

1. **CORS**
   - Production refuses to start with `VOS3_CORS_ORIGINS` unset/empty or containing `"*"`.
   - CORS allows only configured origins, methods, headers.

2. **Password security**
   - Passwords stored as Argon2id/bcrypt encoded hashes.
   - Password policy enforced at user creation/reset.
   - No SHA256 password hashing remains.

3. **Auth hardening**
   - Production refuses to start if `DEV_MODE=true`.
   - Public endpoints are explicit and minimal.

4. **Persistence**
   - Creating users/orgs/memberships/api_keys/audit_logs persists in Convex when `VOS3_STORAGE_BACKEND=convex`.
   - Restart does not lose state.
   - In-memory backend remains available for unit tests.

5. **Tests**
   - Unit tests cover CORS validation, password hashing/policy, API key hashing.
   - Integration tests confirm Convex persistence.

---