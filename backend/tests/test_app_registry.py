"""Tests for services/app_registry.py — App Registry."""

from unittest.mock import patch

from services.app_registry import AppRegistry, AppInstallation, get_app_registry


class TestAppRegistry:
    def setup_method(self):
        self.registry = AppRegistry()

    def test_install_creates_installation(self):
        inst = self.registry.install(
            app_id="app1",
            organization_id="org1",
            installed_by="user1",
            version="1.0.0",
            scopes=["vos3:entities:read"],
        )
        assert isinstance(inst, AppInstallation)
        assert inst.app_id == "app1"
        assert inst.organization_id == "org1"
        assert inst.enabled is True

    @patch("db.convex.get_convex_client", side_effect=Exception("no convex"))
    def test_install_convex_fallback(self, _mock):
        inst = self.registry.install(
            app_id="app1",
            organization_id="org1",
            installed_by="user1",
            version="1.0.0",
            scopes=[],
        )
        assert inst.app_id == "app1"

    def test_uninstall_removes(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        assert self.registry.uninstall("app1", "org1") is True
        assert self.registry.get("app1", "org1") is None

    def test_uninstall_nonexistent_returns_false(self):
        assert self.registry.uninstall("nope", "org1") is False

    def test_enable_disable_toggle(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        assert self.registry.disable("app1", "org1") is True
        inst = self.registry.get("app1", "org1")
        assert inst.enabled is False

        assert self.registry.enable("app1", "org1") is True
        inst = self.registry.get("app1", "org1")
        assert inst.enabled is True

    def test_enable_nonexistent_returns_false(self):
        assert self.registry.enable("nope", "org1") is False

    def test_disable_nonexistent_returns_false(self):
        assert self.registry.disable("nope", "org1") is False

    def test_get_installation_returns_correct(self):
        self.registry.install("app1", "org1", "u1", "1.0", ["s1"])
        inst = self.registry.get("app1", "org1")
        assert inst.version == "1.0"
        assert inst.granted_scopes == ["s1"]

    def test_get_missing_returns_none(self):
        assert self.registry.get("nope", "org1") is None

    def test_list_installed(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        self.registry.install("app2", "org1", "u1", "2.0", [])
        apps = self.registry.list_installed("org1")
        assert len(apps) == 2

    def test_list_installed_empty(self):
        assert self.registry.list_installed("org1") == []

    def test_is_installed(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        assert self.registry.is_installed("app1", "org1") is True

    def test_is_installed_disabled_returns_false(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        self.registry.disable("app1", "org1")
        assert self.registry.is_installed("app1", "org1") is False

    def test_update_version(self):
        self.registry.install("app1", "org1", "u1", "1.0", [])
        updated = self.registry.update("app1", "org1", "2.0")
        assert updated.version == "2.0"

    def test_update_nonexistent_returns_none(self):
        assert self.registry.update("nope", "org1", "2.0") is None


class TestGetAppRegistry:
    def test_singleton(self):
        import services.app_registry as mod

        mod._registry = None
        r1 = get_app_registry()
        r2 = get_app_registry()
        assert r1 is r2
        mod._registry = None
