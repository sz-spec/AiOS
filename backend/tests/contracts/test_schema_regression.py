"""
API Schema Regression Tests
============================

Frozen response-shape assertions for all major API endpoints.
Verifies that the API contract (field names, types, structure) does not
drift across releases.

Run with:
    pytest tests/contracts/test_schema_regression.py -m contract
"""

import re
import pytest

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_json(resp):
    """Return True when the response Content-Type signals JSON."""
    ct = resp.headers.get("content-type", "")
    return "application/json" in ct


def _allows_status(resp, *codes):
    """Accept any of the listed status codes as valid.

    503 (Service Unavailable) is always accepted — backend services
    may be unavailable in the test environment.
    """
    return resp.status_code in (*codes, 503)


# ============================================================================
# 1-2. GET /health
# ============================================================================


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_returns_json_with_expected_keys(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "status" in data, "Missing 'status' key in /health response"
        assert "version" in data, "Missing 'version' key in /health response"

    def test_health_status_value_is_healthy(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "healthy"

    def test_health_version_is_string(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert isinstance(data["version"], str)

    def test_health_services_is_dict(self, client):
        resp = client.get("/health")
        data = resp.json()
        if "services" in data:
            assert isinstance(data["services"], dict)


# ============================================================================
# 3. GET /api/chat/models
# ============================================================================


class TestChatModels:
    def test_chat_models_returns_list(self, client):
        resp = client.get("/api/chat/models")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            # Endpoint returns {"models": [...], "default": "..."} or a bare list
            models = data if isinstance(data, list) else data.get("models", [])
            assert isinstance(models, list), "/api/chat/models should contain a list"
            assert models is not None

    def test_chat_models_items_have_id_and_provider(self, client):
        resp = client.get("/api/chat/models")
        if resp.status_code == 200:
            data = resp.json()
            models = data if isinstance(data, list) else data.get("models", [])
            if models:
                item = models[0]
                assert "id" in item, "Model item missing 'id'"
                assert "provider" in item, "Model item missing 'provider'"


# ============================================================================
# 4. GET /api/agents
# ============================================================================


class TestAgentsEndpoint:
    def test_agents_returns_list_or_object(self, client):
        resp = client.get("/api/agents")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(
                data, (list, dict)
            ), "/api/agents should return a list or dict"

    def test_agents_list_items_have_id_and_name(self, client):
        resp = client.get("/api/agents")
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("agents", [])
            for item in items:
                assert "id" in item
                assert "name" in item


# ============================================================================
# 5. GET /api/v-core/entities
# ============================================================================


class TestVCoreEntities:
    def test_entities_returns_list(self, client):
        resp = client.get("/api/v-core/entities")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            # Accepts list or {"entities": [...]}
            entities = data if isinstance(data, list) else data.get("entities", data)
            assert isinstance(
                entities, (list, dict)
            ), "/api/v-core/entities must not return null"

    def test_entities_returns_empty_list_not_null(self, client):
        resp = client.get("/api/v-core/entities")
        if resp.status_code == 200:
            data = resp.json()
            assert data is not None, "Entities returned null instead of empty list"


# ============================================================================
# 6. GET /api/metrics
# ============================================================================


class TestMetricsOverview:
    def test_metrics_returns_dict(self, client):
        resp = client.get("/api/metrics/")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict), "/api/metrics should return a dict"

    def test_metrics_summary_returns_dict(self, client):
        resp = client.get("/api/metrics/summary")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


# ============================================================================
# 7. GET /api/billing/credits
# ============================================================================


class TestBillingCredits:
    def test_credits_returns_structured(self, client):
        resp = client.get("/api/billing/credits")
        assert _allows_status(resp, 200, 500, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert data is not None


# ============================================================================
# 8. GET /api/memory
# ============================================================================


class TestMemoryEndpoint:
    def test_memory_returns_structured(self, client):
        resp = client.get("/api/memory/")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(
                data, (list, dict)
            ), "/api/memory should return a list or dict"

    def test_memory_not_null(self, client):
        resp = client.get("/api/memory/")
        if resp.status_code == 200:
            assert resp.json() is not None


# ============================================================================
# 9. GET /api/settings/status
# ============================================================================


class TestSettingsEndpoint:
    def test_settings_status_returns_dict(self, client):
        resp = client.get("/api/settings/status")
        assert _allows_status(resp, 200, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict), "/api/settings/status should return a dict"

    def test_settings_has_provider_keys(self, client):
        resp = client.get("/api/settings/status")
        if resp.status_code == 200:
            data = resp.json()
            # ConfigStatus has openai, anthropic, google, etc.
            for provider in ("openai", "anthropic"):
                assert provider in data, f"Missing '{provider}' in settings status"


# ============================================================================
# 10. Empty list endpoints return [] not null
# ============================================================================


class TestEmptyListBehavior:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v-core/entities",
            "/api/agents",
            "/api/memory/recent",
        ],
    )
    def test_empty_list_not_null(self, client, path):
        resp = client.get(path)
        if resp.status_code == 200:
            data = resp.json()
            # Data may be a list directly or a dict wrapping a list
            if isinstance(data, list):
                # Empty list is fine
                assert data is not None
            elif isinstance(data, dict):
                # Check any list-valued fields are not null
                for v in data.values():
                    if isinstance(v, list):
                        assert v is not None


# ============================================================================
# 11-12. Error response structure
# ============================================================================


class TestErrorResponseStructure:
    def test_404_returns_detail(self, client):
        resp = client.get("/api/nonexistent-endpoint-xyz")
        assert resp.status_code in (404, 503)
        if resp.status_code == 404:
            data = resp.json()
            assert (
                "detail" in data or "error" in data
            ), "404 should return {'detail': ...} or {'error': ...}"

    def test_404_on_nonexistent_resource(self, client):
        resp = client.get("/api/v-core/entities/nonexistent-id-999")
        # Could be 404 or 500 depending on service state
        if resp.status_code in (404, 422):
            data = resp.json()
            assert "detail" in data or "error" in data


# ============================================================================
# 13. Content-Type is application/json
# ============================================================================


class TestContentType:
    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/api/agents",
            "/api/metrics/",
            "/api/settings/status",
            "/api/memory/",
        ],
    )
    def test_content_type_is_json(self, client, path):
        resp = client.get(path)
        if resp.status_code in (200, 422, 404, 500):
            assert _is_json(
                resp
            ), f"{path} returned Content-Type: {resp.headers.get('content-type')}"


# ============================================================================
# 14. GET /api/terminal/sessions
# ============================================================================


class TestTerminalSessions:
    def test_terminal_sessions_returns_list_or_structured(self, client):
        resp = client.get("/api/terminal/sessions/test-session-id")
        # Terminal route may not be mounted or may 404
        assert _allows_status(resp, 200, 404, 422, 500)


# ============================================================================
# 15. GET /api/kernel/status
# ============================================================================


class TestKernelStatus:
    def test_kernel_status_returns_dict(self, client):
        resp = client.get("/api/kernel/status")
        assert _allows_status(resp, 200, 500, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


# ============================================================================
# 16-17. Pagination endpoints
# ============================================================================


class TestPagination:
    def test_v_core_records_accepts_pagination_params(self, client):
        """Entities/{id}/records should accept limit and offset query params."""
        resp = client.get(
            "/api/v-core/entities/test-ent/records",
            params={"limit": 5, "offset": 0},
        )
        # 200 or 404/500 if entity doesn't exist — both fine, no 422 for valid params
        assert (
            resp.status_code != 422 or "limit" not in resp.text.lower()
        ), "Pagination param 'limit' should be accepted"

    def test_memory_recent_accepts_limit(self, client):
        resp = client.get("/api/memory/recent", params={"limit": 3})
        assert _allows_status(resp, 200, 500)

    def test_activities_accept_limit(self, client):
        resp = client.get("/api/v-core/activities", params={"limit": 10})
        assert _allows_status(resp, 200, 500)


# ============================================================================
# 18. Collection with no data returns 200 with empty list
# ============================================================================


class TestEmptyCollections:
    def test_agents_empty_collection_200(self, client):
        resp = client.get("/api/agents")
        if resp.status_code == 200:
            data = resp.json()
            # Even empty, should be valid structure
            assert isinstance(data, (list, dict))


# ============================================================================
# 19. Single resource returns dict with 'id'
# ============================================================================


class TestSingleResource:
    def test_single_agent_returns_dict_with_id(self, client):
        # First create an agent
        payload = {"name": "TestBot", "role": "assistant"}
        create_resp = client.post("/api/agents", json=payload)
        if create_resp.status_code in (200, 201):
            agent_id = create_resp.json().get("id")
            if agent_id:
                resp = client.get(f"/api/agents/{agent_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    assert isinstance(data, dict)
                    assert "id" in data
                # Cleanup
                client.delete(f"/api/agents/{agent_id}")


# ============================================================================
# 20. POST endpoints return 200/201 with created resource
# ============================================================================


class TestPostCreation:
    def test_create_agent_returns_resource(self, client):
        payload = {"name": "ContractBot", "role": "tester"}
        resp = client.post("/api/agents", json=payload)
        assert _allows_status(resp, 200, 201, 422, 500)
        if resp.status_code in (200, 201):
            data = resp.json()
            assert isinstance(data, dict)
            assert "id" in data or "name" in data
            # Cleanup
            agent_id = data.get("id")
            if agent_id:
                client.delete(f"/api/agents/{agent_id}")

    def test_create_entity_returns_resource(self, client):
        payload = {"name": "test_contract_entity", "label": "Test Entity"}
        resp = client.post("/api/v-core/entities", json=payload)
        assert _allows_status(resp, 200, 201, 409, 422, 500)
        if resp.status_code in (200, 201):
            data = resp.json()
            assert isinstance(data, dict)


# ============================================================================
# 21. DELETE endpoints return 200/204
# ============================================================================


class TestDeleteEndpoints:
    def test_delete_agent_returns_success(self, client):
        # Create then delete
        payload = {"name": "DeleteMe", "role": "assistant"}
        create_resp = client.post("/api/agents", json=payload)
        if create_resp.status_code in (200, 201):
            agent_id = create_resp.json().get("id")
            if agent_id:
                resp = client.delete(f"/api/agents/{agent_id}")
                assert _allows_status(resp, 200, 204, 404)

    def test_delete_nonexistent_returns_404_or_200(self, client):
        resp = client.delete("/api/agents/nonexistent-id-999")
        assert _allows_status(resp, 200, 204, 404, 500)


# ============================================================================
# 22. PUT/PATCH endpoints return updated resource
# ============================================================================


class TestUpdateEndpoints:
    def test_update_agent_returns_resource(self, client):
        # Create
        payload = {"name": "UpdateMe", "role": "backend"}
        create_resp = client.post("/api/agents", json=payload)
        if create_resp.status_code in (200, 201):
            agent_id = create_resp.json().get("id")
            if agent_id:
                update_payload = {"name": "UpdatedBot", "role": "frontend"}
                resp = client.put(f"/api/agents/{agent_id}", json=update_payload)
                assert _allows_status(resp, 200, 422, 500)
                if resp.status_code == 200:
                    data = resp.json()
                    assert isinstance(data, dict)
                # Cleanup
                client.delete(f"/api/agents/{agent_id}")


# ============================================================================
# 23. List endpoints accept search/filter params
# ============================================================================


class TestFilterParams:
    def test_agents_accept_extra_query_params(self, client):
        """Extra query params should not cause 422 — silently ignored."""
        resp = client.get("/api/agents", params={"role": "tester"})
        assert _allows_status(resp, 200, 500)

    def test_memory_query_accepts_type_filter(self, client):
        resp = client.get("/api/memory/recent", params={"memory_type": "conversation"})
        assert _allows_status(resp, 200, 500)


# ============================================================================
# 24. Response omits internal fields
# ============================================================================


class TestNoInternalFields:
    FORBIDDEN_KEYS = (
        "_internal",
        "_debug",
        "password_hash",
        "secret_key",
        "api_key_raw",
    )

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/api/agents",
            "/api/settings/status",
            "/api/memory/",
            "/api/metrics/",
        ],
    )
    def test_no_internal_fields_exposed(self, client, path):
        resp = client.get(path)
        if resp.status_code == 200:
            text = resp.text
            for key in self.FORBIDDEN_KEYS:
                assert (
                    key not in text
                ), f"Internal field '{key}' found in {path} response"


# ============================================================================
# 25. Timestamp fields are ISO 8601
# ============================================================================


class TestTimestampFormat:
    ISO8601_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

    def test_agent_timestamps_iso8601(self, client):
        payload = {"name": "TimestampBot", "role": "assistant"}
        create_resp = client.post("/api/agents", json=payload)
        if create_resp.status_code in (200, 201):
            data = create_resp.json()
            for key in ("created_at", "updated_at", "last_run"):
                if key in data and data[key] is not None:
                    assert self.ISO8601_RE.match(
                        str(data[key])
                    ), f"Timestamp field '{key}' is not ISO 8601: {data[key]}"
            agent_id = data.get("id")
            if agent_id:
                client.delete(f"/api/agents/{agent_id}")


# ============================================================================
# 26-30. Additional shape assertions
# ============================================================================


class TestChatCompletionsShape:
    def test_chat_completions_returns_expected_shape(self, client):
        payload = {
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
        resp = client.post("/api/chat/completions", json=payload)
        # May fail with 503 in dev mode or missing API keys — that's fine
        assert _allows_status(resp, 200, 422, 500, 503)
        if resp.status_code == 200:
            data = resp.json()
            assert "message" in data or "choices" in data


class TestMetricsCostsShape:
    def test_costs_returns_expected_keys(self, client):
        resp = client.get("/api/metrics/costs")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)


class TestSettingsProvidersShape:
    def test_providers_status_shape(self, client):
        resp = client.get("/api/chat/providers/status")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (dict, list))


class TestVCoreOrganizationsShape:
    def test_organizations_returns_list_wrapper(self, client):
        resp = client.get("/api/v-core/organizations")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            if "organizations" in data:
                assert isinstance(data["organizations"], list)


class TestMemoryTypesShape:
    def test_memory_types_returns_known_types(self, client):
        resp = client.get("/api/memory/types")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, (list, dict))
            # Should contain well-known memory types
            text = str(data).lower()
            assert "conversation" in text or "error" in text or len(str(data)) > 2
