"""
DB Integrity Tests — Blocker #1 (DB Layer) and #2 (Schema Drift)

Verifies:
  1. Production mode without CONVEX_URL raises RuntimeError
  2. Production mode without CONVEX_DEPLOY_KEY raises RuntimeError
  3. Dev mode works without credentials (ENVIRONMENT != production)
  4. health_check() validates connectivity
  5. Schema SSoT: backend/convex/schema.ts is deleted, frontend/convex/schema.ts exists
  6. is_connected() returns correct state

Run: pytest tests/test_db_integrity.py -v
"""

import os
import sys
import asyncio
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.convex import ConvexClient, ConvexDB

# ============================================================================
# 1. Production Mode Guards
# ============================================================================


class TestProductionGuards:
    """ENVIRONMENT=production must require CONVEX_URL and CONVEX_DEPLOY_KEY."""

    def test_production_no_convex_url_raises(self):
        """ConvexClient in production without CONVEX_URL must raise RuntimeError."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("CONVEX_URL", None)
            with pytest.raises(RuntimeError, match="CONVEX_URL.*required.*production"):
                ConvexClient(url="", deploy_key="")

    def test_production_no_deploy_key_raises(self):
        """ConvexDB in production without CONVEX_DEPLOY_KEY must raise RuntimeError."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("CONVEX_DEPLOY_KEY", None)
            with pytest.raises(
                RuntimeError, match="CONVEX_URL.*CONVEX_DEPLOY_KEY.*production"
            ):
                ConvexDB(url="https://test.convex.cloud", deploy_key="")

    def test_dev_mode_works_without_credentials(self):
        """Dev mode (ENVIRONMENT=development) should NOT raise even without credentials."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            # Should not raise
            client = ConvexDB(url="", deploy_key="")
            assert client.dev_mode is True
            assert client.get_mode() == "dev"
            # Dev mode is considered "connected" because it's operational via in-memory storage
            assert client.is_connected() is True


# ============================================================================
# 2. Health Check
# ============================================================================


class TestHealthCheck:
    """health_check() validates actual Convex connectivity."""

    def test_health_check_dev_mode_returns_true(self):
        """In dev mode, health_check returns True without network call."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            client = ConvexClient(url="", deploy_key="")
            assert client.dev_mode is True
            result = asyncio.run(client.health_check())
            assert result is True

    def test_health_check_bad_url_raises(self):
        """health_check with unreachable URL must raise RuntimeError."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            # RFC 5737: 192.0.2.0/24 is reserved for documentation, guaranteed non-routable
            client = ConvexClient(
                url="http://192.0.2.1:1",
                deploy_key="test-key",
            )
            client.dev_mode = False  # Force production path
            with pytest.raises(RuntimeError):
                asyncio.run(client.health_check())


# ============================================================================
# 3. Connection State
# ============================================================================


class TestConnectionState:
    """is_connected() must reflect actual state."""

    def test_is_connected_true_in_dev(self):
        """Dev mode: is_connected() returns True (operational via in-memory storage)."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            db = ConvexDB(url="", deploy_key="")
            assert db.is_connected() is True

    def test_is_connected_true_with_credentials(self):
        """With credentials: is_connected() returns True."""
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            db = ConvexDB(url="https://test.convex.cloud", deploy_key="prod:key123")
            assert db.is_connected() is True
            assert db.get_mode() == "production"


# ============================================================================
# 4. Schema Single Source of Truth
# ============================================================================


class TestSchemaSSOT:
    """frontend/convex/schema.ts is the sole schema definition."""

    def test_backend_schema_deleted(self):
        """backend/convex/schema.ts must NOT exist (deleted for SSoT)."""
        backend_schema = (
            Path(__file__).parent.parent.parent / "backend" / "convex" / "schema.ts"
        )
        assert (
            not backend_schema.exists()
        ), "backend/convex/schema.ts still exists — delete it. SSoT is frontend/convex/schema.ts"

    def test_frontend_schema_exists(self):
        """frontend/convex/schema.ts must exist as the Single Source of Truth."""
        frontend_schema = (
            Path(__file__).parent.parent.parent / "frontend" / "convex" / "schema.ts"
        )
        assert (
            frontend_schema.exists()
        ), f"frontend/convex/schema.ts not found at {frontend_schema}"

    def test_frontend_schema_has_required_tables(self):
        """SSoT schema must define all required tables."""
        frontend_schema = (
            Path(__file__).parent.parent.parent / "frontend" / "convex" / "schema.ts"
        )
        content = frontend_schema.read_text()
        required_tables = [
            "users",
            "organizations",
            "projects",
            "builds",
            "agentStatus",
            "entities",
            "records",
            "workflows",
            "subscriptions",
            "apps",
        ]
        for table in required_tables:
            assert (
                f"{table}:" in content or f"{table} :" in content
            ), f"Table '{table}' not found in frontend/convex/schema.ts"


# ============================================================================
# 5. Singleton Reset Safety
# ============================================================================


class TestSingletonSafety:
    """get_convex_client() singleton respects environment."""

    def test_singleton_dev_mode(self):
        """Singleton in dev mode returns a working ConvexDB."""
        import db.convex as convex_module

        # Reset singleton
        convex_module._convex_db = None
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            env = os.environ.copy()
            env.pop("CONVEX_URL", None)
            env.pop("CONVEX_DEPLOY_KEY", None)
            env["ENVIRONMENT"] = "development"
            with mock.patch.dict(os.environ, env, clear=True):
                db = convex_module.get_convex_client()
                assert db.dev_mode is True
                # Reset for other tests
                convex_module._convex_db = None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
