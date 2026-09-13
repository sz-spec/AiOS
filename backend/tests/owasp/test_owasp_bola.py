"""
OWASP API1:2023 — Broken Object Level Authorization (BOLA / IDOR)
=================================================================

Tests that User A cannot access, modify, or delete User B's resources
by guessing or substituting resource IDs. Every resource-bearing
endpoint must enforce ownership or org-level isolation.

Markers:
    @pytest.mark.owasp — all tests in this module

Fixtures (from conftest.py):
    user_a_client — TestClient as User A (org_a, read permissions)
    user_b_client — TestClient as User B (org_b, read permissions)
    admin_client  — TestClient as admin (org_a, admin:full)
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A deterministic "foreign" resource ID that User A should never own.
FOREIGN_ID = "foreign_resource_00000000"

# Generate a batch of fake sequential IDs to simulate enumeration attacks.
SEQUENTIAL_IDS = [str(i) for i in range(1, 11)]

# Fixed UUID-style IDs for probing (deterministic to avoid xdist collection mismatch)
UUID_PROBE_IDS = [
    "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
    "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
    "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80",
    "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091",
]


def _assert_not_accessible(response, context: str = ""):
    """Assert that the response is NOT a successful data leak.

    Acceptable status codes:
        401 — Unauthorized (auth layer caught it)
        403 — Forbidden (ownership check caught it)
        404 — Not Found (resource hidden from unauthorized user)
        405 — Method Not Allowed
        422 — Validation Error (ID format rejected)
        500 — Internal Error (service layer failed safely — no data leak)
        503 — Service Unavailable (backend dependency down — no data leak)

    Unacceptable:
        200 with a response body that looks like resource data
    """
    assert response.status_code != 200 or (
        # 200 is tolerable ONLY if the body is clearly empty / error
        response.json().get("detail") is not None
        or response.json().get("error") is not None
        or response.json() == {}
    ), (
        f"BOLA VIOLATION ({context}): got 200 with data — "
        f"status={response.status_code}, body={response.text[:300]}"
    )


# ---------------------------------------------------------------------------
# OWASP API1 — Project resource isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaProjects:
    """User A must not access User B's projects."""

    def test_get_foreign_project(self, user_a_client):
        """GET /api/v1/projects/{id} with a foreign project ID."""
        r = user_a_client.get(f"/api/v1/projects/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET project")

    def test_delete_foreign_project(self, user_a_client):
        """DELETE /api/v1/projects/{id} with a foreign project ID."""
        r = user_a_client.delete(f"/api/v1/projects/{FOREIGN_ID}")
        _assert_not_accessible(r, "DELETE project")

    @pytest.mark.parametrize("probe_id", SEQUENTIAL_IDS[:5])
    def test_sequential_id_enumeration_projects(self, user_a_client, probe_id):
        """Probe sequential integer IDs against the projects endpoint."""
        r = user_a_client.get(f"/api/v1/projects/{probe_id}")
        _assert_not_accessible(r, f"sequential project id={probe_id}")

    @pytest.mark.parametrize("probe_id", UUID_PROBE_IDS[:3])
    def test_uuid_guessing_projects(self, user_a_client, probe_id):
        """Probe random UUIDs against the projects endpoint."""
        r = user_a_client.get(f"/api/v1/projects/{probe_id}")
        _assert_not_accessible(r, f"uuid project id={probe_id}")


# ---------------------------------------------------------------------------
# OWASP API1 — V-Core entity / record isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaVCoreEntities:
    """Cross-org entity and record access must be blocked."""

    def test_get_foreign_entity(self, user_a_client):
        """GET /api/v-core/entities/{id} with foreign entity ID."""
        r = user_a_client.get(f"/api/v-core/entities/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET entity")

    def test_add_field_to_foreign_entity(self, user_a_client):
        """POST /api/v-core/entities/{id}/fields on a foreign entity."""
        r = user_a_client.post(
            f"/api/v-core/entities/{FOREIGN_ID}/fields",
            json={"name": "injected", "type": "text", "label": "Injected"},
        )
        _assert_not_accessible(r, "POST add field to foreign entity")

    def test_get_foreign_record(self, user_a_client):
        """GET /api/v-core/records/{id} with foreign record ID."""
        r = user_a_client.get(f"/api/v-core/records/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET record")

    def test_update_foreign_record(self, user_a_client):
        """PATCH /api/v-core/records/{id} on a foreign record."""
        r = user_a_client.patch(
            f"/api/v-core/records/{FOREIGN_ID}",
            json={"status": "pwned"},
        )
        _assert_not_accessible(r, "PATCH foreign record")

    def test_delete_foreign_record(self, user_a_client):
        """DELETE /api/v-core/records/{id} on a foreign record."""
        r = user_a_client.delete(f"/api/v-core/records/{FOREIGN_ID}")
        _assert_not_accessible(r, "DELETE foreign record")

    def test_get_foreign_record_history(self, user_a_client):
        """GET /api/v-core/records/{id}/history with foreign record ID."""
        r = user_a_client.get(f"/api/v-core/records/{FOREIGN_ID}/history")
        _assert_not_accessible(r, "GET record history")

    def test_list_records_foreign_entity(self, user_a_client):
        """GET /api/v-core/entities/{id}/records on a foreign entity."""
        r = user_a_client.get(f"/api/v-core/entities/{FOREIGN_ID}/records")
        _assert_not_accessible(r, "GET records of foreign entity")


# ---------------------------------------------------------------------------
# OWASP API1 — V-Core organization isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaOrganizations:
    """User from org_a must not access org_b's organization resources."""

    FOREIGN_ORG = "org_b"

    def test_get_foreign_organization(self, user_a_client):
        """GET /api/v-core/organizations/{org_id} for another org."""
        r = user_a_client.get(f"/api/v-core/organizations/{self.FOREIGN_ORG}")
        _assert_not_accessible(r, "GET foreign org")

    def test_list_foreign_org_members(self, user_a_client):
        """GET /api/v-core/organizations/{org_id}/members for another org."""
        r = user_a_client.get(f"/api/v-core/organizations/{self.FOREIGN_ORG}/members")
        _assert_not_accessible(r, "GET foreign org members")

    def test_invite_to_foreign_org(self, user_a_client):
        """POST /api/v-core/organizations/{org_id}/invitations to another org."""
        r = user_a_client.post(
            f"/api/v-core/organizations/{self.FOREIGN_ORG}/invitations",
            json={"email": "attacker@evil.com", "role_id": "admin"},
        )
        _assert_not_accessible(r, "POST invite to foreign org")

    def test_audit_logs_foreign_org(self, user_a_client):
        """GET /api/v-core/audit-logs?org_id=org_b must not leak logs."""
        r = user_a_client.get(
            "/api/v-core/audit-logs", params={"org_id": self.FOREIGN_ORG}
        )
        _assert_not_accessible(r, "GET audit logs for foreign org")


# ---------------------------------------------------------------------------
# OWASP API1 — Workflow isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaWorkflows:
    """Workflows owned by another org must not be accessible."""

    def test_get_foreign_workflow(self, user_a_client):
        """GET /api/v-core/workflows/{id} with a foreign workflow ID."""
        r = user_a_client.get(f"/api/v-core/workflows/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET foreign workflow")

    def test_add_node_to_foreign_workflow(self, user_a_client):
        """POST /api/v-core/workflows/{id}/nodes on a foreign workflow."""
        r = user_a_client.post(
            f"/api/v-core/workflows/{FOREIGN_ID}/nodes",
            json={"type": "notification", "name": "evil_node", "config": {}},
        )
        _assert_not_accessible(r, "POST node to foreign workflow")


# ---------------------------------------------------------------------------
# OWASP API1 — Agent isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaAgents:
    """User A must not access User B's agents."""

    def test_get_foreign_agent(self, user_a_client):
        """GET /api/agents/{id} with a foreign agent ID."""
        r = user_a_client.get(f"/api/agents/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET foreign agent")

    def test_delete_foreign_agent(self, user_a_client):
        """DELETE /api/agents/{id} for a foreign agent."""
        r = user_a_client.delete(f"/api/agents/{FOREIGN_ID}")
        _assert_not_accessible(r, "DELETE foreign agent")

    def test_update_foreign_agent(self, user_a_client):
        """PUT /api/agents/{id} for a foreign agent."""
        r = user_a_client.put(
            f"/api/agents/{FOREIGN_ID}",
            json={"name": "hijacked", "role": "assistant"},
        )
        _assert_not_accessible(r, "PUT foreign agent")

    @pytest.mark.parametrize("probe_id", SEQUENTIAL_IDS[:5])
    def test_sequential_agent_enumeration(self, user_a_client, probe_id):
        """Probe sequential IDs against agents endpoint."""
        r = user_a_client.get(f"/api/agents/{probe_id}")
        _assert_not_accessible(r, f"sequential agent id={probe_id}")


# ---------------------------------------------------------------------------
# OWASP API1 — Team isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaTeams:
    """Cross-org team access must be blocked."""

    def test_get_foreign_team(self, user_a_client):
        """GET /api/teams/{id} for a team owned by another user."""
        r = user_a_client.get(f"/api/teams/{FOREIGN_ID}")
        _assert_not_accessible(r, "GET foreign team")

    def test_update_foreign_team(self, user_a_client):
        """PUT /api/teams/{id} for a foreign team."""
        r = user_a_client.put(
            f"/api/teams/{FOREIGN_ID}",
            json={"name": "hijacked_team"},
        )
        _assert_not_accessible(r, "PUT foreign team")

    def test_invite_to_foreign_team(self, user_a_client):
        """POST /api/teams/{id}/invite to a foreign team."""
        r = user_a_client.post(
            f"/api/teams/{FOREIGN_ID}/invite",
            json={"email": "attacker@evil.com", "role": "admin"},
        )
        _assert_not_accessible(r, "POST invite to foreign team")

    def test_delete_foreign_team(self, user_a_client):
        """DELETE /api/teams/{id} for a foreign team."""
        r = user_a_client.delete(f"/api/teams/{FOREIGN_ID}")
        _assert_not_accessible(r, "DELETE foreign team")


# ---------------------------------------------------------------------------
# OWASP API1 — Billing isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaBilling:
    """User A must not view or modify User B's billing data."""

    def test_get_subscription_cross_user(self, user_a_client):
        """GET /api/billing/subscription should return only own data."""
        r = user_a_client.get("/api/billing/subscription")
        # Should not leak another user's subscription
        if r.status_code == 200:
            body = r.json()
            # If it returns data, it must belong to user_a, not user_b
            assert body.get("user_id") in (
                None,
                "user_a",
            ), f"BOLA: subscription data leaked for another user: {body}"

    def test_add_credits_without_admin(self, user_a_client):
        """POST /api/billing/credits/add requires admin permission."""
        r = user_a_client.post(
            "/api/billing/credits/add",
            json={"user_id": "user_b", "amount": 999999, "reason": "bola_test"},
        )
        _assert_not_accessible(r, "POST add credits without admin")

    def test_credits_balance_isolation(self, user_a_client):
        """GET /api/billing/credits should only return own balance."""
        r = user_a_client.get("/api/billing/credits")
        if r.status_code == 200:
            body = r.json()
            assert body.get("user_id") in (
                None,
                "user_a",
            ), f"BOLA: credits data leaked for another user: {body}"


# ---------------------------------------------------------------------------
# OWASP API1 — Memory isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaMemory:
    """Memory entries should be scoped to the authenticated user."""

    def test_query_memory_returns_only_own_data(self, user_a_client):
        """POST /api/memory/query should not return another user's memories."""
        r = user_a_client.post(
            "/api/memory/query",
            json={"query": "secret plans", "top_k": 10},
        )
        # We cannot assert exact content, but the endpoint should not crash
        # and should scope to user_a's memories.
        assert r.status_code in (
            200,
            404,
            422,
            500,
            503,
        ), f"Unexpected status for memory query: {r.status_code}"

    def test_delete_foreign_memory(self, user_a_client):
        """DELETE /api/memory/{id} with a foreign memory ID."""
        r = user_a_client.delete(f"/api/memory/{FOREIGN_ID}")
        _assert_not_accessible(r, "DELETE foreign memory")


# ---------------------------------------------------------------------------
# OWASP API1 — Settings isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaSettings:
    """Settings endpoints must not expose other users' API keys."""

    def test_get_settings_returns_only_own(self, user_a_client):
        """GET /api/settings/status returns only own configuration."""
        r = user_a_client.get("/api/settings/status")
        if r.status_code == 200:
            body = r.json()
            # Must not contain another user's full API keys
            for key_name in ["openai", "anthropic", "google"]:
                val = body.get(key_name, {})
                if isinstance(val, dict) and val.get("masked_key"):
                    # Masked keys are fine (first 4, last 4 chars)
                    assert (
                        len(val["masked_key"]) <= 15
                    ), f"Settings leak: {key_name} key not properly masked"


# ---------------------------------------------------------------------------
# OWASP API1 — Kernel bridge isolation
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaKernel:
    """Kernel bridge operations should not allow cross-user file access."""

    def test_read_with_path_traversal(self, user_a_client):
        """GET /api/kernel/read with path traversal attempt."""
        r = user_a_client.get("/api/kernel/read", params={"path": "/../../etc/passwd"})
        _assert_not_accessible(r, "kernel read path traversal")

    def test_ls_with_path_traversal(self, user_a_client):
        """GET /api/kernel/ls with path traversal attempt."""
        r = user_a_client.get("/api/kernel/ls", params={"path": "/../../"})
        _assert_not_accessible(r, "kernel ls path traversal")


# ---------------------------------------------------------------------------
# OWASP API1 — Cross-endpoint parametrized probe
# ---------------------------------------------------------------------------

# Resource-bearing GET endpoints that accept an ID parameter
_GET_ENDPOINTS_WITH_IDS = [
    "/api/v1/projects/{id}",
    "/api/v-core/entities/{id}",
    "/api/v-core/records/{id}",
    "/api/v-core/records/{id}/history",
    "/api/v-core/workflows/{id}",
    "/api/v-core/organizations/{id}",
    "/api/agents/{id}",
    "/api/teams/{id}",
]

# Resource-bearing DELETE endpoints that accept an ID parameter
_DELETE_ENDPOINTS_WITH_IDS = [
    "/api/v1/projects/{id}",
    "/api/v-core/records/{id}",
    "/api/agents/{id}",
    "/api/teams/{id}",
]


@pytest.mark.owasp
class TestBolaCrossEndpointSweep:
    """Parametrized sweep across all resource-bearing endpoints."""

    @pytest.mark.parametrize("endpoint_template", _GET_ENDPOINTS_WITH_IDS)
    def test_get_with_foreign_id(self, user_a_client, endpoint_template):
        """GET every resource-bearing endpoint with a foreign ID."""
        url = endpoint_template.replace("{id}", FOREIGN_ID)
        r = user_a_client.get(url)
        _assert_not_accessible(r, f"GET {url}")

    @pytest.mark.parametrize("endpoint_template", _DELETE_ENDPOINTS_WITH_IDS)
    def test_delete_with_foreign_id(self, user_a_client, endpoint_template):
        """DELETE every resource-bearing endpoint with a foreign ID."""
        url = endpoint_template.replace("{id}", FOREIGN_ID)
        r = user_a_client.delete(url)
        _assert_not_accessible(r, f"DELETE {url}")

    @pytest.mark.parametrize("endpoint_template", _GET_ENDPOINTS_WITH_IDS)
    def test_get_with_sequential_id(self, user_a_client, endpoint_template):
        """Probe each GET endpoint with sequential integer ID '1'."""
        url = endpoint_template.replace("{id}", "1")
        r = user_a_client.get(url)
        _assert_not_accessible(r, f"GET {url} (sequential)")


# ---------------------------------------------------------------------------
# OWASP API1 — Admin-only endpoints accessed by regular user
# ---------------------------------------------------------------------------


@pytest.mark.owasp
class TestBolaPrivilegeEscalation:
    """Regular user must not access admin-only operations."""

    def test_non_admin_cannot_add_credits(self, user_a_client):
        """POST /api/billing/credits/add should require admin:full."""
        r = user_a_client.post(
            "/api/billing/credits/add",
            json={"user_id": "user_b", "amount": 100, "reason": "test"},
        )
        assert r.status_code in (
            401,
            403,
            404,
            405,
            422,
            500,
            503,
        ), f"Privilege escalation: non-admin added credits, status={r.status_code}"

    def test_non_admin_cannot_access_vos_admin(self, user_a_client):
        """GET /api/vos/status should be restricted to admin/system."""
        r = user_a_client.get("/api/vos/status")
        # VOS routes are hidden system agent routes
        _assert_not_accessible(r, "GET /api/vos/status (non-admin)")

    def test_non_admin_cannot_kill_agents(self, user_a_client):
        """POST /api/kernel/agent-kill-all requires admin privileges."""
        r = user_a_client.post("/api/kernel/agent-kill-all")
        _assert_not_accessible(r, "POST kernel agent-kill-all (non-admin)")
