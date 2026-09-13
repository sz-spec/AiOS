"""Tests for core/app_scopes.py — OAuth 2.0 scope model."""

import pytest
from unittest.mock import MagicMock

from fastapi import HTTPException

from core.app_scopes import (
    AppScope,
    SCOPE_DESCRIPTIONS,
    SCOPE_CATEGORIES,
    validate_scopes,
    check_scopes,
    requires_scope,
)


class TestAppScope:
    def test_all_9_scopes_defined(self):
        assert len(AppScope) == 9

    def test_scope_values(self):
        assert AppScope.ENTITIES_READ == "vos3:entities:read"
        assert AppScope.ENTITIES_WRITE == "vos3:entities:write"
        assert AppScope.RECORDS_READ == "vos3:records:read"
        assert AppScope.RECORDS_WRITE == "vos3:records:write"
        assert AppScope.WORKFLOWS_EXECUTE == "vos3:workflows:execute"
        assert AppScope.AI_GENERATE == "vos3:ai:generate"
        assert AppScope.FILES_READ == "vos3:files:read"
        assert AppScope.FILES_WRITE == "vos3:files:write"
        assert AppScope.KERNEL_EXECUTE == "vos3:kernel:execute"


class TestValidateScopes:
    def test_accepts_valid_scopes(self):
        result = validate_scopes(["vos3:entities:read", "vos3:records:write"])
        assert len(result) == 2
        assert result[0] == AppScope.ENTITIES_READ
        assert result[1] == AppScope.RECORDS_WRITE

    def test_rejects_unknown_scope(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            validate_scopes(["vos3:entities:read", "vos3:bogus:scope"])

    def test_empty_list(self):
        result = validate_scopes([])
        assert result == []


class TestCheckScopes:
    def test_all_required_granted(self):
        granted = {"vos3:entities:read", "vos3:records:write"}
        assert check_scopes(granted, ["vos3:entities:read"]) is True

    def test_missing_required(self):
        granted = {"vos3:entities:read"}
        assert check_scopes(granted, ["vos3:records:write"]) is False


class TestScopeDescriptions:
    def test_all_scopes_have_descriptions(self):
        for scope in AppScope:
            assert scope in SCOPE_DESCRIPTIONS, f"Missing description for {scope}"
            assert len(SCOPE_DESCRIPTIONS[scope]) > 0


class TestScopeCategories:
    def test_all_scopes_categorized(self):
        categorized = set()
        for scopes in SCOPE_CATEGORIES.values():
            categorized.update(scopes)
        for scope in AppScope:
            assert scope in categorized, f"Scope {scope} not in any category"


class TestRequiresScope:
    def _make_request(self, scopes):
        """Create a mock Request with explicit app_scopes on state."""
        request = MagicMock()
        state = MagicMock()
        state.app_scopes = scopes
        request.state = state
        return request

    @pytest.mark.asyncio
    async def test_allows_matching_scope(self):
        @requires_scope("vos3:entities:read")
        async def handler(request):
            return {"ok": True}

        request = self._make_request({"vos3:entities:read", "vos3:records:read"})
        result = await handler(request=request)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_denies_missing_scope(self):
        @requires_scope("vos3:kernel:execute")
        async def handler(request):
            return {"ok": True}

        request = self._make_request({"vos3:entities:read"})
        with pytest.raises(HTTPException) as exc_info:
            await handler(request=request)
        assert exc_info.value.status_code == 403
        assert "vos3:kernel:execute" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_multiple_scopes_required(self):
        @requires_scope("vos3:entities:read", "vos3:entities:write")
        async def handler(request):
            return {"ok": True}

        request = self._make_request({"vos3:entities:read"})
        with pytest.raises(HTTPException) as exc_info:
            await handler(request=request)
        assert exc_info.value.status_code == 403
