"""
API tests for plugin_routes — plugin CRUD, lifecycle, config, hooks, and marketplace.

The PluginManager is injected via Depends(get_plugin_manager), so we use
app.dependency_overrides to swap it out.

Important design notes:
- Routes call plugin_to_response(instance) which walks into instance.manifest.*
  and instance.status.  MagicMock auto-creates nested attribute access, so all
  attribute reads return MagicMocks (not bare strings), which FastAPI serialises
  to strings via Pydantic coercion.
- install_plugin, uninstall_plugin, enable_plugin, disable_plugin, and
  reload_plugin are all `await`-ed in the handlers, so they must be AsyncMock.
- execute_hook is also `await`-ed and returns a list of HookResult objects;
  we return MagicMock instances whose attributes (plugin_id, success, data,
  error, duration_ms) are auto-set.
- Static-path routes (hooks/execute, hooks/types, types, marketplace) are
  declared before /{plugin_id} in plugin_routes.py, so they match correctly.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from plugins.plugin_system import get_plugin_manager, PluginStatus

# ============================================================================
# Helpers
# ============================================================================


def _make_plugin_instance(
    plugin_id="plugin_test",
    name="Test Plugin",
    version="1.0.0",
    description="A test plugin",
    author="Test Author",
    plugin_type="integration",
    status=PluginStatus.ACTIVE,
    enabled=True,
    loaded_at=None,
    error=None,
):
    """Return a MagicMock that satisfies plugin_to_response()."""
    instance = MagicMock()
    instance.manifest.id = plugin_id
    instance.manifest.name = name
    instance.manifest.version = version
    instance.manifest.description = description
    instance.manifest.author = author
    instance.manifest.type.value = plugin_type
    instance.manifest.permissions = []
    instance.manifest.hooks = []
    instance.manifest.config_schema = {}
    instance.status = status
    instance.loaded_at = loaded_at
    instance.error = error
    instance.config = None
    return instance


def _make_hook_result(
    plugin_id="plugin_test", success=True, data=None, error=None, duration_ms=10.0
):
    """Return a MagicMock that satisfies HookResultResponse construction."""
    r = MagicMock()
    r.plugin_id = plugin_id
    r.success = success
    r.data = data if data is not None else {}
    r.error = error
    r.duration_ms = duration_ms
    return r


MOCK_INSTANCE = _make_plugin_instance()


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


@pytest.fixture
def mock_plugins():
    mgr = MagicMock()

    # list_plugins — returns list of plugin instances
    mgr.list_plugins.return_value = [MOCK_INSTANCE]

    # get_plugin — returns a single instance (or None for 404)
    mgr.get_plugin.return_value = MOCK_INSTANCE

    # install_plugin — awaited
    mgr.install_plugin = AsyncMock(return_value=MOCK_INSTANCE)

    # uninstall_plugin — awaited, returns bool
    mgr.uninstall_plugin = AsyncMock(return_value=True)

    # enable_plugin — awaited, returns instance
    mgr.enable_plugin = AsyncMock(return_value=MOCK_INSTANCE)

    # disable_plugin — awaited, returns bool
    mgr.disable_plugin = AsyncMock(return_value=True)

    # reload_plugin — awaited via manager.loader.reload_plugin
    mgr.loader = MagicMock()
    mgr.loader.reload_plugin = AsyncMock(return_value=MOCK_INSTANCE)

    # get_plugin (used by get_plugin_config handler)
    mgr.get_plugin.return_value = MOCK_INSTANCE

    # update_config — returns bool
    mgr.update_config.return_value = True

    # execute_hook — awaited, returns list of HookResult mocks
    mgr.execute_hook = AsyncMock(return_value=[_make_hook_result()])

    app.dependency_overrides[get_plugin_manager] = lambda: mgr
    yield mgr
    app.dependency_overrides.pop(get_plugin_manager, None)


# ============================================================================
# Plugin CRUD
# ============================================================================


class TestListPlugins:
    def test_list_plugins_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins")
        assert resp.status_code == 200

    def test_list_plugins_has_plugins_and_total(self, client, mock_plugins):
        resp = client.get("/api/plugins")
        body = resp.json()
        assert "plugins" in body
        assert "total" in body

    def test_list_plugins_total_matches_length(self, client, mock_plugins):
        resp = client.get("/api/plugins")
        body = resp.json()
        assert body["total"] == len(body["plugins"])

    def test_list_plugins_empty_when_service_returns_empty(self, client, mock_plugins):
        mock_plugins.list_plugins.return_value = []
        resp = client.get("/api/plugins")
        body = resp.json()
        assert body["plugins"] == []
        assert body["total"] == 0


class TestGetPlugin:
    def test_get_plugin_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test")
        assert resp.status_code == 200

    def test_get_plugin_has_id_field(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test")
        body = resp.json()
        assert "id" in body
        assert body["id"] == "plugin_test"

    def test_get_plugin_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.get_plugin.return_value = None
        resp = client.get("/api/plugins/nonexistent")
        assert resp.status_code == 404

    def test_get_plugin_has_required_fields(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test")
        body = resp.json()
        for field in (
            "id",
            "name",
            "version",
            "description",
            "author",
            "type",
            "status",
            "enabled",
        ):
            assert field in body, f"Missing field: {field}"


_INSTALL_BODY = {
    "request": {
        "id": "plugin_test",
        "name": "Test Plugin",
        "version": "1.0.0",
        "description": "A test plugin",
        "author": "Test Author",
        "type": "integration",
    }
}


class TestInstallPlugin:
    def test_install_plugin_returns_200(self, client, mock_plugins):
        resp = client.post("/api/plugins", json=_INSTALL_BODY)
        assert resp.status_code == 200

    def test_install_plugin_calls_service(self, client, mock_plugins):
        client.post("/api/plugins", json=_INSTALL_BODY)
        assert mock_plugins.install_plugin.called

    def test_install_plugin_returns_plugin_id(self, client, mock_plugins):
        resp = client.post("/api/plugins", json=_INSTALL_BODY)
        assert resp.json()["id"] == "plugin_test"


class TestUninstallPlugin:
    def test_uninstall_plugin_returns_200(self, client, mock_plugins):
        resp = client.delete("/api/plugins/plugin_test")
        assert resp.status_code == 200

    def test_uninstall_plugin_returns_status(self, client, mock_plugins):
        resp = client.delete("/api/plugins/plugin_test")
        body = resp.json()
        assert body["status"] == "uninstalled"
        assert body["plugin_id"] == "plugin_test"

    def test_uninstall_plugin_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.uninstall_plugin = AsyncMock(return_value=False)
        resp = client.delete("/api/plugins/nonexistent")
        assert resp.status_code == 404


# ============================================================================
# Plugin Lifecycle
# ============================================================================


class TestEnableDisable:
    def test_enable_plugin_returns_200(self, client, mock_plugins):
        resp = client.post("/api/plugins/plugin_test/enable")
        assert resp.status_code == 200

    def test_enable_plugin_calls_service(self, client, mock_plugins):
        client.post("/api/plugins/plugin_test/enable")
        assert mock_plugins.enable_plugin.called

    def test_enable_plugin_not_found_raises_404(self, client, mock_plugins):
        mock_plugins.enable_plugin = AsyncMock(side_effect=ValueError("not found"))
        resp = client.post("/api/plugins/nonexistent/enable")
        assert resp.status_code == 404

    def test_disable_plugin_returns_200(self, client, mock_plugins):
        resp = client.post("/api/plugins/plugin_test/disable")
        assert resp.status_code == 200

    def test_disable_plugin_returns_status(self, client, mock_plugins):
        resp = client.post("/api/plugins/plugin_test/disable")
        body = resp.json()
        assert body["status"] == "disabled"
        assert body["plugin_id"] == "plugin_test"

    def test_disable_plugin_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.disable_plugin = AsyncMock(return_value=False)
        resp = client.post("/api/plugins/nonexistent/disable")
        assert resp.status_code == 404


class TestReloadPlugin:
    def test_reload_plugin_returns_200(self, client, mock_plugins):
        resp = client.post("/api/plugins/plugin_test/reload")
        assert resp.status_code == 200

    def test_reload_plugin_calls_loader(self, client, mock_plugins):
        client.post("/api/plugins/plugin_test/reload")
        assert mock_plugins.loader.reload_plugin.called

    def test_reload_plugin_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.loader.reload_plugin = AsyncMock(
            side_effect=ValueError("not found")
        )
        resp = client.post("/api/plugins/nonexistent/reload")
        assert resp.status_code == 404


# ============================================================================
# Plugin Configuration
# ============================================================================


class TestPluginConfig:
    def test_get_config_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test/config")
        assert resp.status_code == 200

    def test_get_config_has_plugin_id(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test/config")
        body = resp.json()
        assert body["plugin_id"] == "plugin_test"

    def test_get_config_has_settings_and_schema(self, client, mock_plugins):
        resp = client.get("/api/plugins/plugin_test/config")
        body = resp.json()
        assert "settings" in body
        assert "schema" in body

    def test_get_config_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.get_plugin.return_value = None
        resp = client.get("/api/plugins/nonexistent/config")
        assert resp.status_code == 404

    def test_update_config_returns_200(self, client, mock_plugins):
        resp = client.put(
            "/api/plugins/plugin_test/config",
            json={"settings": {"api_key": "secret"}, "enabled": True},
        )
        assert resp.status_code == 200

    def test_update_config_returns_status(self, client, mock_plugins):
        resp = client.put(
            "/api/plugins/plugin_test/config",
            json={"settings": {"key": "value"}},
        )
        body = resp.json()
        assert body["status"] == "updated"
        assert body["plugin_id"] == "plugin_test"

    def test_update_config_not_found_returns_404(self, client, mock_plugins):
        mock_plugins.update_config.return_value = False
        resp = client.put(
            "/api/plugins/nonexistent/config",
            json={"settings": {}},
        )
        assert resp.status_code == 404


# ============================================================================
# Hooks (static routes — must match before /{plugin_id})
# ============================================================================


class TestHooks:
    def test_execute_hook_returns_200(self, client, mock_plugins):
        resp = client.post(
            "/api/plugins/hooks/execute",
            json={"hook_type": "on_project_create", "data": {}},
        )
        assert resp.status_code == 200

    def test_execute_hook_returns_hook_type(self, client, mock_plugins):
        resp = client.post(
            "/api/plugins/hooks/execute",
            json={"hook_type": "on_project_create", "data": {}},
        )
        body = resp.json()
        assert body["hook_type"] == "on_project_create"

    def test_execute_hook_returns_results_list(self, client, mock_plugins):
        resp = client.post(
            "/api/plugins/hooks/execute",
            json={"hook_type": "on_project_create", "data": {}},
        )
        body = resp.json()
        assert "results" in body
        assert isinstance(body["results"], list)

    def test_execute_hook_invalid_type_returns_400(self, client, mock_plugins):
        resp = client.post(
            "/api/plugins/hooks/execute",
            json={"hook_type": "invalid_hook_xyz", "data": {}},
        )
        assert resp.status_code == 400

    def test_execute_hook_calls_manager(self, client, mock_plugins):
        client.post(
            "/api/plugins/hooks/execute",
            json={"hook_type": "on_file_create", "data": {"file": "app.py"}},
        )
        assert mock_plugins.execute_hook.called

    def test_list_hook_types_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/hooks/types")
        assert resp.status_code == 200

    def test_list_hook_types_has_hook_types_key(self, client, mock_plugins):
        resp = client.get("/api/plugins/hooks/types")
        body = resp.json()
        assert "hook_types" in body
        assert isinstance(body["hook_types"], list)

    def test_list_hook_types_non_empty(self, client, mock_plugins):
        resp = client.get("/api/plugins/hooks/types")
        hook_types = resp.json()["hook_types"]
        assert len(hook_types) > 0

    def test_list_hook_types_each_has_name(self, client, mock_plugins):
        resp = client.get("/api/plugins/hooks/types")
        for ht in resp.json()["hook_types"]:
            assert "name" in ht


# ============================================================================
# Plugin Types (static route)
# ============================================================================


class TestPluginTypes:
    def test_list_plugin_types_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/types")
        assert resp.status_code == 200

    def test_list_plugin_types_has_types_key(self, client, mock_plugins):
        resp = client.get("/api/plugins/types")
        body = resp.json()
        assert "types" in body

    def test_list_plugin_types_non_empty(self, client, mock_plugins):
        resp = client.get("/api/plugins/types")
        types = resp.json()["types"]
        assert len(types) > 0

    def test_list_plugin_types_each_has_name_and_description(
        self, client, mock_plugins
    ):
        resp = client.get("/api/plugins/types")
        for t in resp.json()["types"]:
            assert "name" in t
            assert "description" in t


# ============================================================================
# Marketplace (static routes)
# ============================================================================


class TestMarketplace:
    def test_marketplace_list_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace")
        assert resp.status_code == 200

    def test_marketplace_list_returns_list(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace")
        assert isinstance(resp.json(), list)

    def test_marketplace_list_non_empty(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace")
        plugins = resp.json()
        assert len(plugins) >= 1

    def test_marketplace_list_has_expected_fields(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace")
        first = resp.json()[0]
        for field in (
            "id",
            "name",
            "version",
            "description",
            "author",
            "type",
            "category",
            "downloads",
            "rating",
            "price",
        ):
            assert field in first, f"Missing field: {field}"

    def test_marketplace_get_slack_returns_200(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace/slack-notifications")
        assert resp.status_code == 200

    def test_marketplace_get_slack_has_correct_id(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace/slack-notifications")
        assert resp.json()["id"] == "slack-notifications"

    def test_marketplace_get_not_found_returns_404(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace/nonexistent-plugin-xyz")
        assert resp.status_code == 404

    def test_marketplace_install_returns_200(self, client, mock_plugins):
        resp = client.post("/api/plugins/marketplace/slack-notifications/install")
        assert resp.status_code == 200

    def test_marketplace_install_calls_manager(self, client, mock_plugins):
        client.post("/api/plugins/marketplace/slack-notifications/install")
        assert mock_plugins.install_plugin.called

    def test_marketplace_install_not_in_marketplace_returns_404(
        self, client, mock_plugins
    ):
        resp = client.post("/api/plugins/marketplace/nonexistent-plugin-xyz/install")
        assert resp.status_code == 404

    def test_marketplace_filter_free_only(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace?free_only=true")
        assert resp.status_code == 200
        plugins = resp.json()
        for p in plugins:
            assert p["price"] == 0

    def test_marketplace_sort_by_rating(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace?sort=rating")
        assert resp.status_code == 200
        plugins = resp.json()
        ratings = [p["rating"] for p in plugins]
        assert ratings == sorted(ratings, reverse=True)

    def test_marketplace_search_by_keyword(self, client, mock_plugins):
        resp = client.get("/api/plugins/marketplace?search=slack")
        assert resp.status_code == 200
        plugins = resp.json()
        assert len(plugins) >= 1
        assert any(
            "slack" in p["name"].lower() or "slack" in p.get("tags", [])
            for p in plugins
        )
