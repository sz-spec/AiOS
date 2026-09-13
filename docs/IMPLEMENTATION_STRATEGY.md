# VOS3 Patch-Only Implementation Strategy (Atomic, Reversible, Backward-Compatible)

## Global Patch Rules (Enforced)
1. **Edits are old_string → new_string replacements only** (or *additive insertions* at clearly identified anchors).
2. **Each patch is independent**, testable, and **reversible** via the inverse replacement.
3. **No full-file rewrites**; only targeted block replacements.
4. **Imports only added inside existing import blocks**.
5. **No code changes outside the specified regions** *except*:
   - Adding **new files** (explicitly allowed in your plan)
   - Adding **one dependency line** to requirements (explicitly required by bcrypt/passlib)

---

## Phase 0: Safety Net (Prep, 30–60 min)
### Patch 0.1: Add environment-driven configuration placeholders (no behavior change)
- **Goal:** Prepare for secure CORS and optional persistence without breaking local dev.
- **Action:** Only add env reads where patches already touch code (CORS block + ControlPlane init).

**Test after Patch 0.1**
- `python -c "from backend.main import app"`

Rollback: inverse the insertion.

---

## Phase 1: Security Patches (Day 1)

## Patch 1.1: CORS Configuration (backend/main.py, lines 194–201)
### Problem
- `allow_origins=["*"]` with `allow_credentials=True` is unsafe and also violates the intent of credentialed CORS.

### Strategy
- Replace wildcard origins with **env-driven allowlist**.
- Maintain backward compatibility with an explicit “allow all” escape hatch via env var.

### Patch (old_string → new_string)
**File:** `backend/main.py`  
**Lines:** 194–201 (exact block provided)

**OLD**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # PROBLEM: Allows any origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**NEW**
```python
app.add_middleware(
    CORSMiddleware,
    # Security default: only local dev + explicit allowlist.
    # Backward-compat escape hatch:
    #   VOS3_CORS_ALLOW_ALL=1  -> allow any origin (still not using "*" to keep credentials behavior correct)
    allow_origins=(
        ["http://localhost:3000", "http://localhost:5173"]
        if not os.getenv("VOS3_CORS_ALLOW_ALL")
        else []
    ),
    allow_origin_regex=(
        None
        if not os.getenv("VOS3_CORS_ALLOW_ALL")
        else r"^https?://.*$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Required companion import patch (same file, existing import block)
If `os` isn’t already imported, add it to the **existing import section** (no other location).

**Insert into existing imports** (old_string → new_string example; adjust to your import block)
- If the top already has something like `import logging`, patch it as:

**OLD**
```python
import logging
```

**NEW**
```python
import logging
import os
```

### Test after Patch 1.1
- `python -c "from backend.main import app"`

### Rollback (inverse)
- Replace NEW CORS block back to OLD
- Remove `import os` if it was added

---

## Patch 1.2: Password Hashing Dependency (requirements)
### Goal
Introduce bcrypt hashing via passlib.

### Patch
**File:** `requirements.txt` (or the project’s equivalent dependency file)
- Add one line (atomic & reversible):

**ADD**
```text
passlib[bcrypt]>=1.7.4
```

### Test
- `pip install -r requirements.txt`

### Rollback
- Remove the added line

---

## Patch 1.3: Password Hashing Import + Context (backend/core/control_plane.py, lines 1–25 import section)
### Goal
Add `passlib` context without altering other behavior.

### Patch (add to existing import block)
**File:** `backend/core/control_plane.py`  
**Lines:** 1–25 (import section)

Add these imports inside the import section (exact placement within the import block is fine):
```python
from passlib.context import CryptContext
```

Add a module-level context **near other module-level constants** (if none exist, place immediately after imports—still within the “top-of-file” region you allowed):
```python
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
```

**Atomicity note:** This patch is purely additive; no behavioral change yet.

### Test after Patch 1.3
- `python -c "from backend.core.control_plane import ControlPlaneService"`

### Rollback
- Remove the import and `pwd_context = ...` line

---

## Patch 1.4: Password Hashing Implementation (backend/core/control_plane.py, lines 405–410)
### Problem
Uses `hashlib.sha256` (fast/weak; no salt/work factor).

### Strategy
- Replace sha256 with bcrypt hash from passlib.

### Patch (old_string → new_string)
**File:** `backend/core/control_plane.py`  
**Lines:** 405–410 (exact block provided)

**OLD**
```python
def create_user(self, email: str, name: str, password: Optional[str] = None, ...):
    user = User(email=email, name=name, sso_provider=sso_provider, sso_id=sso_id)
    if password:
        user.password_hash = hashlib.sha256(password.encode()).hexdigest()  # PROBLEM: Weak hashing
    self._users[user.id] = user  # PROBLEM: In-memory storage
    return user
```

**NEW**
```python
def create_user(self, email: str, name: str, password: Optional[str] = None, ...):
    user = User(email=email, name=name, sso_provider=sso_provider, sso_id=sso_id)
    if password:
        # bcrypt via passlib (salted + work factor)
        user.password_hash = pwd_context.hash(password)
    self._users[user.id] = user  # NOTE: persistence addressed in Phase 2 via repository injection
    return user
```

### Backward compatibility note (important)
This patch changes **stored format** for new passwords. To preserve login compatibility for existing sha256 users, you will need a follow-up patch to the password verification path to:
- detect bcrypt hash (`$2b$...` etc) and verify via `pwd_context.verify`
- otherwise verify legacy sha256

Because your “allowed patch regions” didn’t include the auth/verify method snippet, that follow-up is planned in **Phase 1.5** below as a *string-anchored patch* (still patch-only).

### Test after Patch 1.4
- Create a user and confirm `password_hash` starts with `$2` (bcrypt):
  - minimal: run unit/integration tests if present

### Rollback
- Replace back to sha256 line

---

## Patch 1.5 (Required for Backward Compatibility): Dual-Mode Password Verification (string-anchored patch)
### Why this is necessary
If any existing code compares `hashlib.sha256(password).hexdigest()` to `user.password_hash`, bcrypt users will no longer be able to log in.

### Patch method
Because you did not provide the verification snippet/lines, this patch is defined as **exact old_string → new_string** using a search anchor.

#### Find (exact old_string)
Search in `backend/core/control_plane.py` for a block that looks like this pattern:
```python
hashlib.sha256(password.encode()).hexdigest()
```

#### Replace with (new_string)
Replace the verification logic to:

```python
def _verify_password(self, password: str, stored_hash: str) -> bool:
    # bcrypt hashes typically start with "$2"
    if stored_hash.startswith("$2"):
        return pwd_context.verify(password, stored_hash)
    # legacy sha256 fallback
    return hashlib.sha256(password.encode()).hexdigest() == stored_hash
```

Then update the original compare site from:
```python
hashlib.sha256(password.encode()).hexdigest() == user.password_hash
```
to:
```python
self._verify_password(password, user.password_hash)
```

**Atomicity:** Do as two micro-patches:
1) Add `_verify_password` helper (additive insertion near other helpers in same class)
2) Replace the compare expression

**Test**
- Existing sha256 user can still authenticate
- New bcrypt user authenticates

**Rollback**
- Restore original compare logic and remove helper

> If you want, paste the auth/verify snippet and I’ll convert Patch 1.5 into an exact line-numbered replacement like the others.

---

## Phase 2: Repository Pattern (Days 2–3) — Patch Only, Backward Compatible

### Goal
Stop losing users/orgs/etc on restart by introducing an optional persistence layer while keeping existing in-memory dictionaries as the default fallback.

**Key compatibility rule:** If Convex is not configured or unavailable, behavior remains **exactly** as today.

---

## Patch 2.1: Add Repository Interfaces (NEW FILE)
**File:** `backend/core/repositories/__init__.py` (new file)

Contents (kept minimal; no changes to existing code paths until Phase 2.3+):
- Define protocol-like interfaces (or simple ABCs) for:
  - `UserRepository`
  - later: orgs, memberships, invitations, api keys

Rollback: delete the file.

---

## Patch 2.2: Add Convex Repository Implementation (NEW FILE)
**File:** `backend/core/repositories/convex.py` (new file)

Implementation notes:
- Use existing `backend/db/convex.py` client
- Map `User` fields to your schema in `frontend/convex/schema.ts`
- Provide methods:
  - `create_user(user: User) -> User`
  - `get_user_by_id(id: str) -> Optional[User]`
  - `get_user_by_email(email: str) -> Optional[User]`
  - `update_user(user: User) -> User`
  - `delete_user(id: str) -> None`

Rollback: delete the file.

---

## Patch 2.3: Inject Repository into ControlPlaneService (backend/core/control_plane.py, lines 366–373)
### Problem
Hard-coded in-memory dicts only.

### Strategy
- Add optional `user_repo` parameter (default `None`)
- When provided, write-through to repo; otherwise use in-memory dict
- Keep `_users` dict for backward compatibility and for local/dev mode

### Patch (old_string → new_string)
**File:** `backend/core/control_plane.py`  
**Lines:** 366–373 (exact block provided)

**OLD**
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

**NEW**
```python
class ControlPlaneService:
    def __init__(self, user_repo=None):
        # Backward-compatible in-memory defaults
        self._users: dict[str, User] = {}  # fallback cache (also used when no repo configured)
        self._organizations: dict[str, Organization] = {}
        self._memberships: dict[str, OrganizationMember] = {}
        self._roles: dict[str, Role] = {r.id: r for r in SYSTEM_ROLES.values()}
        self._invitations: dict[str, Invitation] = {}
        self._audit_logs: list[AuditLog] = []
        self._api_keys: dict[str, ApiKey] = {}

        # Optional persistence (Convex or other)
        self._user_repo = user_repo
```

### Test after Patch 2.3
- `python -c "from backend.core.control_plane import ControlPlaneService; ControlPlaneService()"`

Rollback: revert block to OLD.

---

## Patch 2.4: Write-through User Create (backend/core/control_plane.py, lines 405–410)
### Strategy
Preserve existing `_users` behavior, but also persist if repo is configured.

### Patch (incremental from Patch 1.4 NEW block)
**Find current NEW block** and replace only the storage line:

**OLD (post-1.4)**
```python
self._users[user.id] = user  # NOTE: persistence addressed in Phase 2 via repository injection
return user
```

**NEW**
```python
self._users[user.id] = user  # always keep in-memory cache for backward compatibility
if getattr(self, "_user_repo", None):
    self._user_repo.create_user(user)
return user
```

### Test
- Without repo: behavior unchanged
- With repo stub: `create_user` calls repo method

Rollback: revert to previous storage-only lines.

---

## Patch 2.5: Read-through for User Retrieval (string-anchored micro-patches)
Because the exact code blocks for user lookup methods were not provided, do these as *anchored replace* patches:
- Wherever code does:
  - `return self._users.get(user_id)`
  - iterate over `self._users.values()` to find by email
Replace with:
- Check repo first if configured, else fallback to in-memory
- Always populate `_users` cache when loading from repo (keeps existing code behavior consistent)

**Example replacement pattern**
- **OLD**
  ```python
  return self._users.get(user_id)
  ```
- **NEW**
  ```python
  if getattr(self, "_user_repo", None):
      user = self._user_repo.get_user_by_id(user_id)
      if user:
          self._users[user.id] = user
      return user
  return self._users.get(user_id)
  ```

**Test**
- With no repo: unchanged
- With repo: retrieval works after restart

Rollback: restore original lines.

---

## Phase 3: Efficiency Module Decomposition (Day 4) — Patch-Only via Facade Exports

### Goal
Reduce `backend/src/efficiency.py` blast radius without breaking imports.

### Rule
- **Do not remove public symbols** currently imported elsewhere.
- Convert `efficiency.py` into a **facade** that imports implementations from new submodules.

---

## Patch 3.1: Create New Module Structure (NEW FILES)
Create new files (no modifications to existing behavior yet):

- `backend/src/efficiency/router.py`
  - Smart Router initialization (currently lines 44–51)
  - `assign_model()`, `assign_model_with_tracking()`

- `backend/src/efficiency/config.py`
  - `EfficiencyConfig` dataclass
  - `EfficientState` TypedDict

- `backend/src/efficiency/middleware.py`
  - `error_middleware`
  - `retry_middleware`

- `backend/src/efficiency/llm.py`
  - `create_quantized_llm`
  - `LLMCallReducer`

- `backend/src/efficiency/retrieval.py`
  - `HybridRetriever`
  - `ContextManager`

- `backend/src/efficiency/workflow.py`
  - `build_efficient_workflow`

Rollback: delete the new files.

---

## Patch 3.2: Convert efficiency.py Sections into Imports (targeted block replacements)
**File:** `backend/src/efficiency.py`

For each named section, replace the **entire definition block** with an import+reexport.
You already have the exact function/class names and approximate line ranges; implement as **one patch per section**.

### Example patch pattern (repeat per symbol)
#### Patch 3.2.a: assign_model
**OLD (example anchor)**
```python
def assign_model(...):
    ...
```

**NEW**
```python
from .efficiency.router import assign_model  # re-export
```

Do the same for:
- `assign_model_with_tracking`
- `EfficiencyConfig`
- `EfficientState`
- `error_middleware`
- `retry_middleware`
- `create_quantized_llm`
- `HybridRetriever`
- `ContextManager`
- `build_efficient_workflow`
- `LLMCallReducer`

**Important:** Keep any existing `__all__` updated *only if it already exists*; otherwise do not add new export machinery (avoid scope creep).

### Tests after Patch 3.2 (critical)
- `python -c "from backend.src.efficiency import EfficiencyConfig, assign_model, HybridRetriever"`
- Run existing test suite:
  - `pytest -x --tb=short`

Rollback: replace imports back to original definitions for the specific symbol.

---

# Verification Checklist (Run after *each* patch)
1. **Import sanity**
   - `python -c "from backend.main import app"`
2. **Core service import**
   - `python -c "from backend.core.control_plane import ControlPlaneService"`
3. **Tests**
   - `pytest -x --tb=short`
4. **Targeted checks**
   - After CORS patch: confirm browser preflight works from allowed origin
   - After hashing patch: confirm new hashes are bcrypt; legacy users still log in (after Patch 1.5)
   - After repo patch: confirm no functional change when repo is not configured

---

# Rollback Procedures (Per Patch)
Each patch is a single old_string → new_string replacement; rollback is the **inverse replacement**:
- Patch 1.1 rollback: restore original CORS block + remove `os` import if added
- Patch 1.2 rollback: remove passlib dependency line
- Patch 1.3 rollback: remove `passlib` import and `pwd_context` line
- Patch 1.4 rollback: restore sha256 hashing line
- Patch 1.5 rollback: restore original compare logic and remove `_verify_password`
- Phase 2 rollback: revert `__init__` signature/body and remove write-through lines; delete new repo files
- Phase 3 rollback: revert each replaced symbol import back to its original block; delete new modules

---

## What I Need From You to Make This 100% Line-Exact (No TBD Anchors)
To convert **Patch 1.5** and **Patch 2.5** from “string-anchored” into **precise line-numbered patches**, paste:
1. The login/authentication method(s) that currently verify passwords (snippet around the sha256 compare).
2. Any user retrieval methods (`get_user_by_id`, `get_user_by_email`, etc.) currently using `self._users`.

I’ll then rewrite those two patches as exact “lines X–Y” replacements like the others.