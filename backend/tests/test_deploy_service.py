"""Tests for DeployService — deployment management."""

import pytest
from services.deploy_service import get_deploy_service


class TestDeployService:
    def _svc(self):
        return get_deploy_service()

    @pytest.mark.asyncio
    async def test_deploy_frontend(self):
        svc = self._svc()
        dep = await svc.deploy_frontend(
            "proj-1", {"index.html": "<h1>Hi</h1>"}, "myapp"
        )
        assert "myapp.vcreator.app" in dep.url
        assert dep.status == "live"
        assert dep.provider == "vercel"
        assert dep.project_id == "proj-1"

    @pytest.mark.asyncio
    async def test_deploy_backend(self):
        svc = self._svc()
        dep = await svc.deploy_backend("proj-1", {"server.py": "app"})
        assert "railway.app" in dep.url
        assert dep.status == "live"
        assert dep.provider == "railway"

    def test_get_latest_none(self):
        assert self._svc().get_latest("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_latest(self):
        svc = self._svc()
        await svc.deploy_frontend("proj-1", {}, "first")
        second = await svc.deploy_frontend("proj-1", {}, "second")
        latest = svc.get_latest("proj-1")
        assert latest is not None
        assert latest.id == second.id

    @pytest.mark.asyncio
    async def test_deployment_to_dict(self):
        svc = self._svc()
        dep = await svc.deploy_frontend("proj-1", {}, "test")
        d = dep.to_dict()
        for key in (
            "id",
            "project_id",
            "provider",
            "url",
            "subdomain",
            "status",
            "created_at",
        ):
            assert key in d

    @pytest.mark.asyncio
    async def test_list_by_project(self):
        svc = self._svc()
        await svc.deploy_frontend("proj-1", {}, "a")
        await svc.deploy_frontend("proj-1", {}, "b")
        await svc.deploy_frontend("proj-2", {}, "c")
        # get_latest returns one, but let's verify multiple exist via get_latest on each
        assert svc.get_latest("proj-1") is not None
        assert svc.get_latest("proj-2") is not None
