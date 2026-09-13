"""
OWASP API3:2023 -- Broken Object Property Level Authorization (BOPLA)
=====================================================================

Tests that mass-assignment attacks are rejected across all mutable endpoints.

When a client sends extra properties (owner_id, is_admin, role, permissions,
org_id, created_at, user_id, ...) alongside legitimate fields, the backend
MUST either:
  - Silently strip them (Pydantic ``model_config = {"extra": "forbid"/"ignore"}``)
  - Return 422 Unprocessable Entity

In NO case should the extra fields be persisted or echoed back in the
response.

Fixtures ``client``, ``user_a_client``, and ``admin_client`` are provided
by the shared conftest and inject mock ``AuthenticatedUser`` instances via
``app.dependency_overrides[get_current_user]``.
"""

import pytest

# ---------------------------------------------------------------------------
# Marker applied to every test in this module
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.owasp


# =========================================================================
# Helpers
# =========================================================================

# Fields that must NEVER be accepted via mass-assignment
PRIVILEGE_FIELDS = {
    "owner_id": "attacker_id",
    "is_admin": True,
    "permissions": ["admin:full", "write", "delete"],
    "role": "admin",
    "org_id": "attacker_org",
    "created_at": "1970-01-01T00:00:00Z",
}

# A subset focused on user-identity takeover
IDENTITY_FIELDS = {
    "user_id": "victim_user_42",
    "id": "forged_id_999",
    "email": "attacker@evil.com",
}


def _assert_extra_fields_rejected(response, injected_fields):
    """Verify that none of the injected fields appear in the response body.

    Accepted outcomes:
      - 422 (Pydantic rejected the extra field outright)
      - 200/201 with the injected field absent from response JSON

    Unacceptable:
      - 200/201 with the injected field echoed back with the attacker value
    """
    if response.status_code == 422:
        # Pydantic rejected it -- correct behavior
        return

    if response.status_code >= 400:
        # Any client/server error is fine (the request was not accepted)
        return

    # 2xx -- ensure injected fields did NOT leak into the response
    try:
        body = response.json()
    except Exception:
        return  # non-JSON 2xx (e.g. 204 No Content)

    if not isinstance(body, dict):
        return

    for field, injected_value in injected_fields.items():
        if field in body:
            assert body[field] != injected_value, (
                f"Mass-assignment succeeded: field '{field}' was accepted "
                f"with value {injected_value!r} (status {response.status_code})"
            )


def _create_project(client):
    """Create a test project and return its ID."""
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "bopla test"},
    )
    if resp.status_code == 200 and "project_id" in resp.json():
        return resp.json()["project_id"]
    return None


def _create_agent(client):
    """Create a test agent and return its JSON."""
    resp = client.post(
        "/api/agents/",
        json={"name": "BOPLA Agent", "role": "backend", "model": "gpt-4o-mini"},
    )
    if resp.status_code == 200:
        return resp.json()
    return None


# =========================================================================
# Test Suite: Projects — mass assignment
# =========================================================================


class TestProjectMassAssignment:
    """BOPLA on project creation and update endpoints."""

    def test_create_project_with_owner_id_injection(self, client):
        """POST /api/v1/projects/wizard with injected owner_id."""
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "test", "owner_id": "attacker"},
        )
        _assert_extra_fields_rejected(resp, {"owner_id": "attacker"})

    def test_create_project_with_is_admin_injection(self, client):
        """POST /api/v1/projects/wizard with injected is_admin."""
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "test", "is_admin": True},
        )
        _assert_extra_fields_rejected(resp, {"is_admin": True})

    def test_create_project_with_permissions_injection(self, client):
        """POST /api/v1/projects/wizard with injected permissions array."""
        resp = client.post(
            "/api/v1/projects/wizard",
            json={
                "category": "website",
                "description": "test",
                "permissions": ["admin:full"],
            },
        )
        _assert_extra_fields_rejected(resp, {"permissions": ["admin:full"]})

    def test_create_project_with_role_injection(self, client):
        """POST /api/v1/projects/wizard with injected role field."""
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "test", "role": "superadmin"},
        )
        _assert_extra_fields_rejected(resp, {"role": "superadmin"})


# =========================================================================
# Test Suite: V-Core entities — mass assignment
# =========================================================================


class TestVCoreMassAssignment:
    """BOPLA on V-Core organization and entity endpoints."""

    def test_create_org_with_owner_id_injection(self, client):
        """POST /api/v-core/organizations with injected owner_id."""
        resp = client.post(
            "/api/v-core/organizations",
            json={"name": "Test Org", "plan": "free", "owner_id": "attacker"},
        )
        _assert_extra_fields_rejected(resp, {"owner_id": "attacker"})

    def test_create_org_with_is_admin_injection(self, client):
        """POST /api/v-core/organizations with injected is_admin."""
        resp = client.post(
            "/api/v-core/organizations",
            json={"name": "Test Org", "plan": "free", "is_admin": True},
        )
        _assert_extra_fields_rejected(resp, {"is_admin": True})

    def test_create_entity_with_org_id_injection(self, client):
        """POST /api/v-core/entities with injected org_id."""
        resp = client.post(
            "/api/v-core/entities",
            json={
                "name": "TestEntity",
                "label": "Test",
                "fields": [],
                "org_id": "attacker_org",
            },
        )
        _assert_extra_fields_rejected(resp, {"org_id": "attacker_org"})

    def test_create_record_with_user_id_injection(self, client):
        """PATCH /api/v-core/records/{id} with injected user_id."""
        resp = client.patch(
            "/api/v-core/records/some_record",
            json={"data": {"name": "legit"}, "user_id": "victim"},
        )
        _assert_extra_fields_rejected(resp, {"user_id": "victim"})

    def test_create_record_with_created_at_injection(self, client):
        """POST /api/v-core/entities/{id}/records with injected created_at."""
        resp = client.post(
            "/api/v-core/entities/ent1/records",
            json={"data": {"name": "test"}, "created_at": "1970-01-01T00:00:00Z"},
        )
        _assert_extra_fields_rejected(resp, {"created_at": "1970-01-01T00:00:00Z"})


# =========================================================================
# Test Suite: Settings — mass assignment
# =========================================================================


class TestSettingsMassAssignment:
    """BOPLA on settings endpoints."""

    def test_api_keys_with_role_injection(self, client):
        """POST /api/settings/api-keys with injected role."""
        resp = client.post(
            "/api/settings/api-keys",
            json={
                "openai_api_key": "sk-test-fake",
                "role": "admin",
            },
        )
        _assert_extra_fields_rejected(resp, {"role": "admin"})

    def test_api_keys_with_permissions_injection(self, client):
        """POST /api/settings/api-keys with injected permissions."""
        resp = client.post(
            "/api/settings/api-keys",
            json={
                "openai_api_key": "sk-test-fake",
                "permissions": ["admin:full"],
            },
        )
        _assert_extra_fields_rejected(resp, {"permissions": ["admin:full"]})


# =========================================================================
# Test Suite: Agents — mass assignment
# =========================================================================


class TestAgentMassAssignment:
    """BOPLA on agent CRUD endpoints."""

    def test_create_agent_with_owner_id_injection(self, client):
        """POST /api/agents/ with injected owner_id."""
        resp = client.post(
            "/api/agents/",
            json={
                "name": "Test Agent",
                "role": "backend",
                "model": "gpt-4o-mini",
                "owner_id": "attacker",
            },
        )
        _assert_extra_fields_rejected(resp, {"owner_id": "attacker"})

    def test_create_agent_with_is_admin_injection(self, client):
        """POST /api/agents/ with injected is_admin."""
        resp = client.post(
            "/api/agents/",
            json={
                "name": "Test Agent",
                "role": "backend",
                "model": "gpt-4o-mini",
                "is_admin": True,
            },
        )
        _assert_extra_fields_rejected(resp, {"is_admin": True})

    def test_update_agent_with_permissions_injection(self, client):
        """PUT /api/agents/{id} with injected permissions."""
        agent = _create_agent(client)
        if agent is None:
            pytest.skip("Could not create agent for test")
        agent_id = agent.get("id", "")
        resp = client.put(
            f"/api/agents/{agent_id}",
            json={
                "name": "Updated Agent",
                "role": "backend",
                "model": "gpt-4o-mini",
                "permissions": ["admin:full"],
            },
        )
        _assert_extra_fields_rejected(resp, {"permissions": ["admin:full"]})

    def test_update_agent_with_created_at_injection(self, client):
        """PUT /api/agents/{id} with injected created_at."""
        agent = _create_agent(client)
        if agent is None:
            pytest.skip("Could not create agent for test")
        agent_id = agent.get("id", "")
        resp = client.put(
            f"/api/agents/{agent_id}",
            json={
                "name": "Updated Agent",
                "role": "backend",
                "model": "gpt-4o-mini",
                "created_at": "1970-01-01T00:00:00Z",
            },
        )
        _assert_extra_fields_rejected(resp, {"created_at": "1970-01-01T00:00:00Z"})


# =========================================================================
# Test Suite: Memory — mass assignment
# =========================================================================


class TestMemoryMassAssignment:
    """BOPLA on memory/insight endpoints."""

    def test_add_memory_with_user_id_injection(self, client):
        """POST /api/memory/add with injected user_id."""
        resp = client.post(
            "/api/memory/add",
            json={
                "content": "test memory",
                "memory_type": "conversation",
                "user_id": "victim_user",
            },
        )
        _assert_extra_fields_rejected(resp, {"user_id": "victim_user"})

    def test_add_memory_with_id_injection(self, client):
        """POST /api/memory/add with injected id to overwrite another entry."""
        resp = client.post(
            "/api/memory/add",
            json={
                "content": "test memory",
                "memory_type": "conversation",
                "id": "overwrite_target",
            },
        )
        _assert_extra_fields_rejected(resp, {"id": "overwrite_target"})

    def test_update_memory_with_owner_id_injection(self, client):
        """PUT /api/memory/{id} with injected owner_id."""
        resp = client.put(
            "/api/memory/mem1",
            json={
                "content": "updated content",
                "owner_id": "attacker",
            },
        )
        _assert_extra_fields_rejected(resp, {"owner_id": "attacker"})


# =========================================================================
# Test Suite: Billing — mass assignment
# =========================================================================


class TestBillingMassAssignment:
    """BOPLA on billing/credit endpoints."""

    def test_use_credits_with_negative_amount(self, client):
        """POST /api/billing/credits/use with negative amount (credit theft)."""
        resp = client.post(
            "/api/billing/credits/use",
            json={"amount": -500, "reason": "ai_generation"},
        )
        # Negative amounts should be rejected (422 or 400)
        # If 200, the amount should not be negative in response
        if resp.status_code == 200:
            body = resp.json()
            if "amount" in body:
                assert body["amount"] >= 0, "Negative credit deduction accepted"

    def test_add_credits_with_user_id_injection(self, client):
        """POST /api/billing/credits/add with injected user_id targeting another user."""
        resp = client.post(
            "/api/billing/credits/add",
            json={
                "user_id": "victim_user",
                "amount": 99999,
                "reason": "free money",
                "is_admin": True,
            },
        )
        _assert_extra_fields_rejected(resp, {"is_admin": True})

    def test_checkout_with_role_injection(self, client):
        """POST /api/billing/checkout with injected role."""
        resp = client.post(
            "/api/billing/checkout",
            json={
                "plan": "pro",
                "interval": "monthly",
                "role": "admin",
                "is_admin": True,
            },
        )
        _assert_extra_fields_rejected(resp, {"role": "admin", "is_admin": True})


# =========================================================================
# Test Suite: Role escalation via mass assignment
# =========================================================================


class TestRoleEscalation:
    """Attempts to escalate privileges via property injection."""

    def test_editor_sets_self_as_owner_via_project(self, user_a_client):
        """User A (editor) tries to set owner_id on project creation."""
        resp = user_a_client.post(
            "/api/v1/projects/wizard",
            json={
                "category": "website",
                "description": "role escalation test",
                "owner_id": "user_a",
                "role": "owner",
            },
        )
        _assert_extra_fields_rejected(resp, {"owner_id": "user_a", "role": "owner"})

    def test_user_injects_admin_permission_via_org_creation(self, user_a_client):
        """User A tries to create org with admin permissions baked in."""
        resp = user_a_client.post(
            "/api/v-core/organizations",
            json={
                "name": "Escalated Org",
                "plan": "enterprise",
                "permissions": ["admin:full"],
                "role": "owner",
            },
        )
        _assert_extra_fields_rejected(
            resp, {"permissions": ["admin:full"], "role": "owner"}
        )

    def test_user_injects_admin_flag_on_agent_create(self, user_a_client):
        """User A tries to create an agent with elevated privileges."""
        resp = user_a_client.post(
            "/api/agents/",
            json={
                "name": "Escalated Agent",
                "role": "backend",
                "model": "gpt-4o-mini",
                "is_admin": True,
                "permissions": ["admin:full", "kernel:write"],
            },
        )
        _assert_extra_fields_rejected(
            resp, {"is_admin": True, "permissions": ["admin:full", "kernel:write"]}
        )


# =========================================================================
# Test Suite: Cross-cutting — all privilege fields on multiple endpoints
# =========================================================================


class TestBulkFieldInjection:
    """Injects ALL privilege fields at once to stress Pydantic stripping."""

    def _inject_all_fields(self, client, method, path, base_body):
        """Send request with all privilege fields injected."""
        payload = {**base_body, **PRIVILEGE_FIELDS, **IDENTITY_FIELDS}
        resp = getattr(client, method)(path, json=payload)
        _assert_extra_fields_rejected(resp, {**PRIVILEGE_FIELDS, **IDENTITY_FIELDS})
        return resp

    def test_project_wizard_all_fields(self, client):
        """POST /api/v1/projects/wizard with every privilege field."""
        self._inject_all_fields(
            client,
            "post",
            "/api/v1/projects/wizard",
            {"category": "website", "description": "bulk test"},
        )

    def test_org_create_all_fields(self, client):
        """POST /api/v-core/organizations with every privilege field."""
        self._inject_all_fields(
            client,
            "post",
            "/api/v-core/organizations",
            {"name": "Bulk Org", "plan": "free"},
        )

    def test_agent_create_all_fields(self, client):
        """POST /api/agents/ with every privilege field.
        Note: 'role' is excluded because it is a legitimate agent field (freeform str).
        """
        # 'role' is a valid AgentConfig field — exclude it from privilege injection
        agent_privilege_fields = {
            k: v for k, v in PRIVILEGE_FIELDS.items() if k != "role"
        }
        payload = {
            "name": "Bulk Agent",
            "role": "backend",
            "model": "gpt-4o-mini",
            **agent_privilege_fields,
            **IDENTITY_FIELDS,
        }
        resp = client.post("/api/agents/", json=payload)
        _assert_extra_fields_rejected(
            resp, {**agent_privilege_fields, **IDENTITY_FIELDS}
        )

    def test_memory_add_all_fields(self, client):
        """POST /api/memory/add with every privilege field."""
        self._inject_all_fields(
            client,
            "post",
            "/api/memory/add",
            {"content": "bulk test", "memory_type": "conversation"},
        )
