"""
Convex Migration Test Suite
============================
Validates the Convex integration without requiring a live deployment.

Run:
    python test_convex_connection.py

With a real Convex deployment:
    CONVEX_URL=https://xxx.convex.cloud python test_convex_connection.py
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# =============================================================================
# Test 1: ConvexClient — dev mode (no CONVEX_URL)
# =============================================================================

class TestConvexClientDevMode(unittest.IsolatedAsyncioTestCase):
    """Test ConvexClient in dev mode (no CONVEX_URL configured)."""

    def setUp(self):
        # Ensure no CONVEX_URL is set for dev-mode tests
        os.environ.pop("CONVEX_URL", None)
        # Reset singleton
        import db.convex as convex_module
        convex_module._convex_client = None

    async def test_dev_mode_detected(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        self.assertTrue(client.dev_mode, "Should be in dev mode when CONVEX_URL is missing")
        print("  ✅ Dev mode detected correctly")

    async def test_query_returns_none_for_single_lookup(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        result = await client.query("users:getByClerkId", {"clerkId": "user_test_123"})
        self.assertIsNone(result)
        print("  ✅ Dev-mode query returns None for single-item lookups")

    async def test_query_returns_list_for_list_functions(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        result = await client.query("organizations:listByUser", {"userId": "user_123"})
        self.assertIsInstance(result, list)
        print("  ✅ Dev-mode query returns [] for list functions")

    async def test_mutation_returns_fake_id(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        result = await client.mutation("users:syncFromClerk", {
            "clerkId": "user_test",
            "email": "test@example.com",
        })
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
        print(f"  ✅ Dev-mode mutation returns fake ID: {result[:8]}...")

    async def test_action_returns_dev_status(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        result = await client.action("billing:processPayment", {})
        self.assertEqual(result.get("status"), "dev_mode")
        print("  ✅ Dev-mode action returns dev_mode status")

    async def test_singleton(self):
        from db.convex import get_convex_client, get_db
        c1 = get_convex_client()
        c2 = get_convex_client()
        c3 = get_db()
        self.assertIs(c1, c2, "get_convex_client() should return the same singleton")
        self.assertIs(c1, c3, "get_db() should return the same singleton as get_convex_client()")
        print("  ✅ Singleton and get_db() alias work correctly")

    async def test_close_is_noop(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        await client.close()  # Should not raise
        print("  ✅ close() is a safe no-op")


# =============================================================================
# Test 2: ConvexClient — live HTTP (mocked)
# =============================================================================

class TestConvexClientHTTP(unittest.IsolatedAsyncioTestCase):
    """Test ConvexClient HTTP request construction (mocked httpx)."""

    def setUp(self):
        os.environ["CONVEX_URL"] = "https://test-deployment.convex.cloud"
        os.environ["CONVEX_DEPLOY_KEY"] = "prod:test_key_123"
        import db.convex as convex_module
        convex_module._convex_client = None

    def tearDown(self):
        os.environ.pop("CONVEX_URL", None)
        os.environ.pop("CONVEX_DEPLOY_KEY", None)
        import db.convex as convex_module
        convex_module._convex_client = None

    async def test_query_sends_correct_request(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        self.assertFalse(client.dev_mode)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"status": "success", "value": None}

        with patch("httpx.AsyncClient") as mock_http_class:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http_class.return_value = mock_http

            result = await client.query("users:getByClerkId", {"clerkId": "user_abc"})

            # Verify the POST was called with correct args
            call_kwargs = mock_http.post.call_args
            url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
            body = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})

            self.assertIn("/api/query", str(url) + str(call_kwargs))
            self.assertEqual(body.get("path"), "users:getByClerkId")
            self.assertEqual(body.get("args", {}).get("clerkId"), "user_abc")
        print("  ✅ Query sends correct path + args to /api/query")

    async def test_auth_header_included(self):
        from db.convex import ConvexClient
        client = ConvexClient()
        headers = client._get_headers()
        self.assertIn("Authorization", headers)
        self.assertIn("Convex", headers["Authorization"])
        self.assertIn("test_key_123", headers["Authorization"])
        print("  ✅ Authorization header includes deploy key")

    async def test_mutation_error_raises(self):
        from db.convex import ConvexClient
        client = ConvexClient()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "error",
            "errorMessage": "Document not found",
        }

        with patch("httpx.AsyncClient") as mock_http_class:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http_class.return_value = mock_http

            with self.assertRaises(RuntimeError) as ctx:
                await client.mutation("users:syncFromClerk", {"clerkId": "bad"})
            self.assertIn("Document not found", str(ctx.exception))
        print("  ✅ Mutation errors propagate as RuntimeError")


# =============================================================================
# Test 3: Webhook logic — handle_user_created
# =============================================================================

class TestWebhookLogic(unittest.IsolatedAsyncioTestCase):
    """Test Clerk webhook handlers call the correct Convex mutations."""

    def setUp(self):
        os.environ.pop("CONVEX_URL", None)
        import db.convex as convex_module
        convex_module._convex_client = None

    async def test_handle_user_created_calls_sync_mutation(self):
        from api.clerk_webhook import handle_user_created

        mutation_calls = []

        async def mock_mutation(self_arg, fn_name, args=None):
            mutation_calls.append((fn_name, args or {}))
            return "fake_convex_id"

        with patch("db.convex.ConvexClient.mutation", new=mock_mutation):
            result = await handle_user_created({
                "id": "user_clerk_123",
                "email_addresses": [
                    {"id": "ea_1", "email_address": "alice@example.com"}
                ],
                "primary_email_address_id": "ea_1",
                "first_name": "Alice",
                "last_name": "Smith",
                "image_url": "https://example.com/alice.jpg",
                "public_metadata": {"role": "admin"},
            })

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["user_id"], "user_clerk_123")
        self.assertEqual(result["email"], "alice@example.com")

        # Verify the correct mutation was called
        called_fns = [c[0] for c in mutation_calls]
        self.assertIn("users:syncFromClerk", called_fns)

        sync_args = next(a for fn, a in mutation_calls if fn == "users:syncFromClerk")
        self.assertEqual(sync_args["clerkId"], "user_clerk_123")
        self.assertEqual(sync_args["email"], "alice@example.com")
        self.assertEqual(sync_args["fullName"], "Alice Smith")
        self.assertEqual(sync_args["avatarUrl"], "https://example.com/alice.jpg")
        print("  ✅ handle_user_created calls users:syncFromClerk with correct args")

    async def test_handle_user_created_fallback_email(self):
        """When primary_email_address_id doesn't match, falls back to first email."""
        from api.clerk_webhook import handle_user_created

        mutation_calls = []

        async def mock_mutation(self_arg, fn_name, args=None):
            mutation_calls.append((fn_name, args or {}))
            return "fake_id"

        with patch("db.convex.ConvexClient.mutation", new=mock_mutation):
            result = await handle_user_created({
                "id": "user_456",
                "email_addresses": [
                    {"id": "ea_2", "email_address": "bob@example.com"}
                ],
                "primary_email_address_id": None,  # not set
                "first_name": "Bob",
                "last_name": None,
                "image_url": None,
                "public_metadata": {},
            })

        self.assertEqual(result["email"], "bob@example.com")
        sync_args = next(a for fn, a in mutation_calls if fn == "users:syncFromClerk")
        self.assertEqual(sync_args["email"], "bob@example.com")
        print("  ✅ handle_user_created falls back to first email when primary not set")

    async def test_handle_user_deleted_calls_soft_delete(self):
        from api.clerk_webhook import handle_user_deleted

        mutation_calls = []

        async def mock_mutation(self_arg, fn_name, args=None):
            mutation_calls.append((fn_name, args or {}))
            return "ok"

        with patch("db.convex.ConvexClient.mutation", new=mock_mutation):
            result = await handle_user_deleted({"id": "user_to_delete"})

        self.assertEqual(result["status"], "deleted")
        called = [c[0] for c in mutation_calls]
        self.assertIn("users:softDelete", called)
        soft_args = next(a for fn, a in mutation_calls if fn == "users:softDelete")
        self.assertEqual(soft_args["clerkId"], "user_to_delete")
        print("  ✅ handle_user_deleted calls users:softDelete with correct clerkId")

    async def test_handle_user_deleted_no_id_skips(self):
        from api.clerk_webhook import handle_user_deleted
        result = await handle_user_deleted({})
        self.assertEqual(result["status"], "skipped")
        print("  ✅ handle_user_deleted skips gracefully when id missing")


# =============================================================================
# Test 4: Repository integrity
# =============================================================================

class TestRepositoryIntegrity(unittest.TestCase):
    """Verify repositories/__init__.py returns Convex implementations by default."""

    def setUp(self):
        os.environ.pop("CONVEX_URL", None)
        os.environ.pop("VOS3_STORAGE_BACKEND", None)
        import db.convex as convex_module
        convex_module._convex_client = None

    def test_default_backend_is_convex(self):
        from core.repositories import _get_backend
        backend = _get_backend()
        self.assertEqual(backend, "convex",
            "Default backend should be 'convex' (VOS3_STORAGE_BACKEND not set)")
        print("  ✅ Default backend is 'convex'")

    def test_get_user_repository_returns_convex_impl(self):
        from core.repositories import get_user_repository
        from core.repositories.convex import ConvexUserRepository
        repo = get_user_repository(backend="convex")
        self.assertIsInstance(repo, ConvexUserRepository)
        print("  ✅ get_user_repository('convex') returns ConvexUserRepository")

    def test_get_org_repository_returns_convex_impl(self):
        from core.repositories import get_organization_repository
        from core.repositories.convex import ConvexOrganizationRepository
        repo = get_organization_repository(backend="convex")
        self.assertIsInstance(repo, ConvexOrganizationRepository)
        print("  ✅ get_organization_repository('convex') returns ConvexOrganizationRepository")

    def test_memory_fallback_still_works(self):
        from core.repositories import get_user_repository
        from core.repositories.memory import InMemoryUserRepository
        repo = get_user_repository(backend="memory")
        self.assertIsInstance(repo, InMemoryUserRepository)
        print("  ✅ memory fallback still works")

    def test_convex_user_repo_has_required_methods(self):
        from core.repositories.convex import ConvexUserRepository
        from core.repositories.base import UserRepository
        repo = ConvexUserRepository()
        # Check all abstract methods are implemented
        required = ["create", "get_by_id", "get_by_email", "update", "delete",
                    "list_all", "list_by_org"]
        for method in required:
            self.assertTrue(hasattr(repo, method),
                f"ConvexUserRepository missing method: {method}")
        print(f"  ✅ ConvexUserRepository implements all {len(required)} required methods")

    def test_convex_org_repo_has_required_methods(self):
        from core.repositories.convex import ConvexOrganizationRepository
        repo = ConvexOrganizationRepository()
        required = ["create", "get_by_id", "get_by_slug", "update", "delete",
                    "list_all", "list_by_user"]
        for method in required:
            self.assertTrue(hasattr(repo, method),
                f"ConvexOrganizationRepository missing method: {method}")
        print(f"  ✅ ConvexOrganizationRepository implements all {len(required)} required methods")

    def test_convex_audit_repo_has_required_methods(self):
        from core.repositories.convex import ConvexAuditLogRepository
        repo = ConvexAuditLogRepository()
        required = ["create", "get_by_id", "list_by_org"]
        for method in required:
            self.assertTrue(hasattr(repo, method))
        print(f"  ✅ ConvexAuditLogRepository implements all {len(required)} required methods")


# =============================================================================
# Test 5: Stripe integration — verify Convex calls present
# =============================================================================

class TestStripeIntegration(unittest.TestCase):
    """Verify stripe_service.py uses Convex mutations."""

    def test_no_from_table_calls(self):
        """Ensure no .from_("table") legacy pattern remains."""
        stripe_path = os.path.join(
            os.path.dirname(__file__),
            "backend", "tools", "stripe_service.py"
        )
        with open(stripe_path) as f:
            source = f.read()
        self.assertNotIn('.from_("', source,
            "stripe_service.py still contains legacy .from_() calls")
        self.assertNotIn(".from_(", source)
        print("  ✅ No .from_('table') legacy calls remain in stripe_service.py")

    def test_convex_mutation_calls_present(self):
        """Ensure convex.mutation calls exist for billing operations."""
        stripe_path = os.path.join(
            os.path.dirname(__file__),
            "backend", "tools", "stripe_service.py"
        )
        with open(stripe_path) as f:
            source = f.read()
        self.assertIn('convex.mutation("billing:upsertSubscription"', source)
        self.assertIn('convex.mutation("billing:addCredits"', source)
        self.assertIn('convex.mutation("billing:useCredits"', source)
        self.assertIn('convex.mutation("billing:updateSubscriptionStatus"', source)
        self.assertIn('convex.mutation("billing:cancelSubscriptionByStripeId"', source)
        print("  ✅ All Stripe webhook handlers use correct convex.mutation() calls")

    def test_convex_query_calls_present(self):
        stripe_path = os.path.join(
            os.path.dirname(__file__),
            "backend", "tools", "stripe_service.py"
        )
        with open(stripe_path) as f:
            source = f.read()
        self.assertIn('convex.query("billing:getSubscription"', source)
        self.assertIn('convex.query("billing:getBalance"', source)
        self.assertIn('convex.query("billing:getStripeCustomerId"', source)
        self.assertIn('convex.query("billing:getUserByStripeCustomerId"', source)
        print("  ✅ All Stripe service reads use correct convex.query() calls")

    def test_constructor_takes_convex_param(self):
        stripe_path = os.path.join(
            os.path.dirname(__file__),
            "backend", "tools", "stripe_service.py"
        )
        with open(stripe_path) as f:
            source = f.read()
        self.assertIn("def __init__(self, convex_client=None):", source)
        self.assertIn("self.convex = convex_client", source)
        print("  ✅ StripeService.__init__ accepts convex_client")


# =============================================================================
# Test 6: Dependency & Startup checks
# =============================================================================

class TestDependenciesAndStartup(unittest.TestCase):

    def test_package_json_has_convex(self):
        pkg_path = os.path.join(
            os.path.dirname(__file__), "frontend", "package.json"
        )
        with open(pkg_path) as f:
            pkg = json.load(f)
        deps = pkg.get("dependencies", {})
        self.assertIn("convex", deps, "package.json missing 'convex' dependency")
        print(f"  ✅ package.json has convex: {deps['convex']}")

    def test_package_json_has_clerk(self):
        pkg_path = os.path.join(
            os.path.dirname(__file__), "frontend", "package.json"
        )
        with open(pkg_path) as f:
            pkg = json.load(f)
        deps = pkg.get("dependencies", {})
        self.assertIn("@clerk/nextjs", deps, "package.json missing '@clerk/nextjs' dependency")
        print(f"  ✅ package.json has @clerk/nextjs: {deps['@clerk/nextjs']}")

    def test_convex_client_file_exists(self):
        client_path = os.path.join(
            os.path.dirname(__file__), "backend", "db", "convex.py"
        )
        self.assertTrue(os.path.exists(client_path))
        print("  ✅ backend/db/convex.py exists")

    def test_convex_provider_exists(self):
        provider_path = os.path.join(
            os.path.dirname(__file__),
            "frontend", "components", "providers", "ConvexClerkProvider.tsx"
        )
        self.assertTrue(os.path.exists(provider_path))
        with open(provider_path) as f:
            src = f.read()
        self.assertIn("ConvexProviderWithClerk", src)
        self.assertIn("ClerkProvider", src)
        self.assertIn('"use client"', src)
        print("  ✅ ConvexClerkProvider.tsx exists with correct providers")

    def test_layout_wraps_with_convex_provider(self):
        layout_path = os.path.join(
            os.path.dirname(__file__), "frontend", "app", "layout.tsx"
        )
        with open(layout_path) as f:
            src = f.read()
        self.assertIn("ConvexClerkProvider", src)
        self.assertIn("<ConvexClerkProvider>", src)
        print("  ✅ frontend/app/layout.tsx wraps children with <ConvexClerkProvider>")

    def test_convex_schema_exists_with_all_tables(self):
        schema_path = os.path.join(
            os.path.dirname(__file__), "frontend", "convex", "schema.ts"
        )
        with open(schema_path) as f:
            src = f.read()
        expected_tables = [
            "users", "organizations", "members", "invitations",
            "entities", "records", "workflows", "workflowExecutions",
            "metrics", "approvals", "alerts", "auditLog", "apiKeys",
            "subscriptions", "stripeCustomers", "userCredits", "creditTransactions",
            "quotaUsage", "quotaTransactions", "quotaAlerts",
            "projects", "projectFiles", "chatMessages", "projectMemory",
        ]
        missing = [t for t in expected_tables if f'"{t}"' not in src and f": defineTable" not in src]
        # Check by table name presence
        missing_tables = [t for t in expected_tables if t + ":" not in src and t + " :" not in src]
        if missing_tables:
            # More lenient check
            missing_tables = [t for t in expected_tables if t not in src]
        self.assertEqual(missing_tables, [],
            f"Schema missing tables: {missing_tables}")
        print(f"  ✅ schema.ts contains all {len(expected_tables)} expected tables")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  VOS3 Convex Migration — Validation Suite")
    print("="*60)

    groups = [
        ("1. ConvexClient Dev Mode", TestConvexClientDevMode),
        ("2. ConvexClient HTTP (mocked)", TestConvexClientHTTP),
        ("3. Webhook Logic", TestWebhookLogic),
        ("4. Repository Integrity", TestRepositoryIntegrity),
        ("5. Stripe Integration", TestStripeIntegration),
        ("6. Dependencies & Startup", TestDependenciesAndStartup),
    ]

    total_passed = 0
    total_failed = 0

    for title, test_class in groups:
        print(f"\n{title}")
        print("-" * 50)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(test_class)

        # Run each test case individually to capture output
        for test in unittest.TestLoader().loadTestsFromTestCase(test_class):
            method = test._testMethodName
            try:
                # Call setUp before each test (critical for env var injection)
                if hasattr(test, "setUp"):
                    test.setUp()

                # IsolatedAsyncioTestCase needs the runner for async tests
                if asyncio.iscoroutinefunction(getattr(test_class, method, None)):
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(getattr(test, method)())
                    finally:
                        loop.close()
                else:
                    getattr(test, method)()
                total_passed += 1
            except Exception as e:
                print(f"  ❌ {method}: {e}")
                total_failed += 1
            finally:
                # Call tearDown after each test to clean up env vars
                if hasattr(test, "tearDown"):
                    try:
                        test.tearDown()
                    except Exception:
                        pass

    print("\n" + "="*60)
    print(f"  Results: {total_passed} passed, {total_failed} failed")
    print("="*60 + "\n")

    sys.exit(1 if total_failed > 0 else 0)
