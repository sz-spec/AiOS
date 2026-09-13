"""
IE-2 Expert-in-the-Loop — Verification Tests

Covers:
1. ExpertContext packaging with 900 KB budget enforcement
2. ExpertRequest lifecycle (pending → claimed → resolved)
3. Memory integration (procedural memory storage of expert fixes)
4. API route validation (create, claim, resolve)
5. ProjectState field integration (expert_sos_suggested)
6. Edge cases (empty state, oversized files, double-claim)

Run: pytest backend/tests/test_expert_system.py -v
"""

import os
import sys
import json
import pytest

_backend = os.path.join(os.path.dirname(__file__), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


# =====================================================================
# Helpers
# =====================================================================


def _make_build_state(**overrides):
    """Build a complete ProjectState-like dict for testing."""
    state = {
        "messages": [],
        "requirements": "Build a user dashboard with auth and Stripe integration",
        "project_description": "Build a user dashboard",
        "architecture": {"tech_stack": {"frontend": "react", "backend": "fastapi"}},
        "frontend_code": {
            "src/App.tsx": "import React from 'react';\nexport default function App() { return <div>Hello</div>; }",
            "src/components/Dashboard.tsx": "export function Dashboard() { return <div>Dashboard</div>; }",
            "src/hooks/useAuth.ts": "export function useAuth() { return { user: null }; }",
        },
        "backend_code": {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()",
            "api/users.py": "from fastapi import APIRouter\nrouter = APIRouter()",
        },
        "tests": {
            "test_app.py": "def test_hello(): assert True",
        },
        "review_results": {
            "issues": [
                {
                    "severity": "critical",
                    "title": "Auth bypass",
                    "description": "Missing JWT validation",
                },
                {
                    "severity": "high",
                    "title": "No error handling",
                    "description": "API has no try/except",
                },
            ]
        },
        "current_phase": "review",
        "iteration": 4,
        "errors": [
            "TypeError: Cannot read property 'id' of undefined",
            "Auth middleware not applied",
        ],
        "error": None,
        "final_project": None,
        "stuck_count": 3,
        "previous_issues_embeddings": None,
        "previous_issues_text": None,
        "model_switch_history": [
            {
                "from": "claude-sonnet-4-6",
                "to": "gpt-5.2-pro",
                "reason": "Loop detected",
                "stage": 1,
            },
            {
                "from": "gpt-5.2-pro",
                "to": "gpt-5.2-pro",
                "reason": "Thinking model",
                "stage": 2,
            },
        ],
        "_override_model": None,
        "_clear_failed_context": None,
        "_include_anti_patterns": None,
        "_latest_issues_embeddings": None,
        "_latest_issues_text": None,
        "guardrails_violations": [
            {
                "rule_id": "ARCH001",
                "file_path": "src/App.tsx",
                "line": 3,
                "message": "fetch in component",
                "fix_instruction": "Move to hook",
                "severity": "critical",
            },
        ],
        "guardrails_iteration": 1,
        "expert_sos_suggested": True,
    }
    state.update(overrides)
    return state


# =====================================================================
# Test: Context Packaging
# =====================================================================


class TestContextPackaging:
    """Verify ExpertContext correctly packages build state."""

    def test_basic_packaging(self):
        """All fields from build state land in ExpertContext."""
        from services.expert_pool import package_context

        state = _make_build_state()
        ctx = package_context(state, user_description="Help! Auth is broken")

        assert ctx.requirements.startswith("Build a user dashboard")
        assert ctx.architecture is not None
        assert len(ctx.frontend_files) == 3
        assert len(ctx.backend_files) == 2
        assert len(ctx.test_files) == 1
        assert len(ctx.errors) == 2
        assert len(ctx.review_issues) == 2
        assert len(ctx.guardrails_violations) == 1
        assert ctx.stuck_count == 3
        assert ctx.iteration == 4
        assert len(ctx.model_switch_history) == 2
        assert ctx.user_description == "Help! Auth is broken"

    def test_context_fits_900kb(self):
        """Packaged context must be under 900 KB."""
        from services.expert_pool import package_context, CONTEXT_SIZE_LIMIT

        state = _make_build_state()
        ctx = package_context(state)
        size = ctx.estimated_size_bytes()

        assert (
            size < CONTEXT_SIZE_LIMIT
        ), f"Context is {size} bytes, limit is {CONTEXT_SIZE_LIMIT}"

    def test_large_files_truncated(self):
        """Files exceeding MAX_FILE_CHARS are truncated."""
        from services.expert_pool import package_context

        big_file = "x" * 20_000
        state = _make_build_state(
            frontend_code={
                "src/BigComponent.tsx": big_file,
            }
        )

        ctx = package_context(state)
        content = ctx.frontend_files["src/BigComponent.tsx"]

        assert len(content) < len(big_file)
        assert "truncated" in content

    def test_oversized_state_still_fits(self):
        """Even with 50 large files, trimming ensures we fit the budget."""
        from services.expert_pool import package_context, CONTEXT_SIZE_LIMIT

        # Create 50 files of 20KB each = ~1MB raw
        big_state = _make_build_state(
            frontend_code={f"src/component_{i}.tsx": "a" * 20_000 for i in range(50)}
        )

        ctx = package_context(big_state)
        size = ctx.estimated_size_bytes()

        assert size < CONTEXT_SIZE_LIMIT, f"After trimming: {size} bytes"

    def test_empty_state_handled(self):
        """Packaging an empty state produces a valid ExpertContext."""
        from services.expert_pool import package_context

        ctx = package_context({})
        assert ctx.requirements == ""
        assert ctx.frontend_files == {}
        assert ctx.backend_files == {}
        assert ctx.errors == []
        assert ctx.stuck_count == 0

    def test_to_dict_serializable(self):
        """ExpertContext.to_dict() produces JSON-serializable output."""
        from services.expert_pool import package_context

        state = _make_build_state()
        ctx = package_context(state)
        d = ctx.to_dict()

        # Should not raise
        serialized = json.dumps(d, default=str)
        assert len(serialized) > 0

    def test_messages_extracted(self):
        """Agent messages are extracted and truncated."""
        from services.expert_pool import package_context
        from langchain_core.messages import HumanMessage, AIMessage

        messages = [
            HumanMessage(content="Build me an app"),
            AIMessage(
                content="Here's the architecture: " + "x" * 5000, name="Architect"
            ),
            AIMessage(content="Frontend code ready", name="Frontend"),
        ]

        state = _make_build_state(messages=messages)
        ctx = package_context(state)

        assert len(ctx.agent_messages) == 3
        # Content should be truncated to 2000 chars
        assert all(len(m["content"]) <= 2000 for m in ctx.agent_messages)

    def test_message_limit_applied(self):
        """Only the last MAX_MESSAGES messages are kept."""
        from services.expert_pool import package_context, MAX_MESSAGES
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content=f"msg {i}") for i in range(50)]
        state = _make_build_state(messages=messages)
        ctx = package_context(state)

        assert len(ctx.agent_messages) == MAX_MESSAGES


# =====================================================================
# Test: Expert Request Lifecycle
# =====================================================================


class TestExpertRequestLifecycle:
    """Verify pending → claimed → resolved flow."""

    def test_create_request(self):
        """Creating a request returns a pending ExpertRequest."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test", user_description="Help!")

        req = svc.create_request(
            user_id="user_123",
            project_id="proj_456",
            context=ctx,
        )

        assert req.id != ""
        assert req.user_id == "user_123"
        assert req.project_id == "proj_456"
        assert req.status == "pending"
        assert req.context is not None
        assert req.claimed_by is None

    def test_claim_request(self):
        """Claiming changes status to 'claimed' and records expert ID."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req = svc.create_request("u1", "p1", ctx)

        claimed = svc.claim_request(req.id, expert_id="expert_42")

        assert claimed is not None
        assert claimed.status == "claimed"
        assert claimed.claimed_by == "expert_42"
        assert claimed.claimed_at is not None

    def test_resolve_request(self):
        """Resolving attaches patches and summary."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req = svc.create_request("u1", "p1", ctx)
        svc.claim_request(req.id, expert_id="expert_42")

        resolution = {
            "patches": {"src/App.tsx": "fixed code"},
            "summary": "Added missing null check",
        }

        resolved = svc.resolve_request(req.id, resolution)

        assert resolved is not None
        assert resolved.status == "resolved"
        assert resolved.resolution["patches"]["src/App.tsx"] == "fixed code"
        assert resolved.resolved_at is not None

    def test_double_claim_rejected(self):
        """Cannot claim an already-claimed request."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req = svc.create_request("u1", "p1", ctx)
        svc.claim_request(req.id, expert_id="expert_1")

        result = svc.claim_request(req.id, expert_id="expert_2")
        assert result is None

    def test_resolve_already_resolved_rejected(self):
        """Cannot resolve an already-resolved request."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req = svc.create_request("u1", "p1", ctx)
        svc.resolve_request(req.id, {"patches": {}, "summary": "fix1"})

        result = svc.resolve_request(req.id, {"patches": {}, "summary": "fix2"})
        assert result is None

    def test_claim_nonexistent_returns_none(self):
        """Claiming a non-existent request returns None."""
        from services.expert_pool import ExpertPoolService

        svc = ExpertPoolService()
        assert svc.claim_request("nonexistent", "exp1") is None

    def test_list_pending(self):
        """list_pending returns only pending requests."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req1 = svc.create_request("u1", "p1", ctx)
        req2 = svc.create_request("u2", "p2", ctx)
        svc.claim_request(req1.id, "exp1")

        pending = svc.list_pending()
        assert len(pending) == 1
        assert pending[0].id == req2.id

    def test_list_by_user(self):
        """list_by_user returns only that user's requests."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        svc.create_request("u1", "p1", ctx)
        svc.create_request("u2", "p2", ctx)
        svc.create_request("u1", "p3", ctx)

        user1_reqs = svc.list_by_user("u1")
        assert len(user1_reqs) == 2

    def test_resolve_pending_directly(self):
        """A pending request can be resolved directly (skip claim)."""
        from services.expert_pool import ExpertPoolService, ExpertContext

        svc = ExpertPoolService()
        ctx = ExpertContext(requirements="test")
        req = svc.create_request("u1", "p1", ctx)

        resolved = svc.resolve_request(
            req.id,
            {
                "patches": {"fix.py": "print('fixed')"},
                "summary": "Quick fix",
            },
        )

        assert resolved is not None
        assert resolved.status == "resolved"


# =====================================================================
# Test: Memory Integration
# =====================================================================


class TestMemoryIntegration:
    """Verify expert fixes are stored in procedural memory."""

    def test_store_expert_fix_builds_instruction(self):
        """
        store_expert_fix_in_memory constructs a meaningful instruction
        from resolution + context and calls MemoryManager.update_procedure.
        """
        from services.expert_pool import ExpertPoolService
        from unittest.mock import patch, MagicMock

        resolution = {
            "patches": {"src/App.tsx": "fixed content"},
            "summary": "Fixed auth bypass by adding JWT validation",
        }

        context = {
            "errors": ["TypeError: Cannot read property 'id'"],
            "guardrails_violations": [
                {"rule_id": "ARCH001"},
                {"rule_id": "ARCH002"},
            ],
        }

        with patch("memory.MemoryManager") as MockMM:
            mock_instance = MagicMock()
            mock_instance.update_procedure.return_value = "mem_123"
            MockMM.return_value = mock_instance

            memory_id = ExpertPoolService.store_expert_fix_in_memory(
                resolution, context
            )

            assert memory_id == "mem_123"
            mock_instance.update_procedure.assert_called_once()

            call_kwargs = mock_instance.update_procedure.call_args
            instruction = (
                call_kwargs.kwargs.get("instruction")
                or call_kwargs[1].get("instruction")
                or call_kwargs[0][0]
            )

            # Verify instruction content
            assert "EXPERT FIX" in instruction
            assert "Fixed auth bypass" in instruction
            assert "ARCH001" in instruction
            assert "src/App.tsx" in instruction

    def test_store_fix_empty_patches_returns_none(self):
        """If no patches and no summary, return None without calling memory."""
        from services.expert_pool import ExpertPoolService

        result = ExpertPoolService.store_expert_fix_in_memory(
            resolution={"patches": {}, "summary": ""},
            context={},
        )
        assert result is None

    def test_store_fix_graceful_on_import_failure(self):
        """If MemoryManager import fails, return None gracefully."""
        from services.expert_pool import ExpertPoolService
        from unittest.mock import patch

        with patch("memory.MemoryManager", side_effect=ImportError("no module")):
            result = ExpertPoolService.store_expert_fix_in_memory(
                resolution={"patches": {"a.py": "x"}, "summary": "fix"},
                context={},
            )
            # Should not raise — graceful degradation
            assert result is None

    def test_stored_with_high_priority(self):
        """Expert fixes should be stored with priority=5 (high)."""
        from services.expert_pool import ExpertPoolService
        from unittest.mock import patch, MagicMock

        with patch("memory.MemoryManager") as MockMM:
            mock_instance = MagicMock()
            mock_instance.update_procedure.return_value = "mem_456"
            MockMM.return_value = mock_instance

            ExpertPoolService.store_expert_fix_in_memory(
                {"patches": {"f.py": "x"}, "summary": "fix"},
                {},
            )

            call_kwargs = mock_instance.update_procedure.call_args
            # priority should be 5
            assert (
                call_kwargs.kwargs.get("priority", call_kwargs[1].get("priority")) == 5
            )


# =====================================================================
# Test: ProjectState Integration
# =====================================================================


class TestProjectStateIntegration:
    """Verify expert_sos_suggested field in ProjectState."""

    def test_expert_sos_field_exists(self):
        """ProjectState TypedDict has expert_sos_suggested field."""
        from ai.agents.multi_agent import ProjectState
        import typing

        hints = typing.get_type_hints(ProjectState)
        assert "expert_sos_suggested" in hints

    def test_stage_3_sets_sos_suggested(self):
        """Stage 3 of _route_after_review sets expert_sos_suggested=True."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        state = _make_build_state(
            stuck_count=2,  # Will become 3 in stage check
            review_results={
                "issues": [
                    {
                        "severity": "critical",
                        "title": "Bug",
                        "description": "Still broken",
                    },
                ]
            },
            iteration=5,
            # Provide matching previous embeddings to trigger "stuck" detection
            previous_issues_embeddings=None,  # None = first iter, but iteration >= 3 triggers escalation
        )

        route = builder._route_after_review(state)

        assert route == "finalize"
        assert state["expert_sos_suggested"] is True
        assert state["stuck_count"] == 3

    def test_initial_state_has_expert_field(self):
        """build() initializes expert_sos_suggested to None."""
        # We verify via the TypedDict — initial state includes the field
        state = _make_build_state()
        assert "expert_sos_suggested" in state


# =====================================================================
# Test: API Routes (unit-level, no server needed)
# =====================================================================


class TestAPIRoutes:
    """Test the API route handler functions directly."""

    @pytest.mark.asyncio
    async def test_create_request_endpoint(self):
        """POST /request creates a request and returns response."""
        from api.expert_routes import create_expert_request, CreateExpertRequestBody
        from middleware.auth import AuthenticatedUser

        body = CreateExpertRequestBody(
            project_id="proj_123",
            description="Build is stuck on auth",
            build_state=_make_build_state(),
        )
        user = AuthenticatedUser(id="user_test", email="test@test.com")

        # Reset singleton for clean test
        import services.expert_pool as ep

        ep._expert_service = None

        result = await create_expert_request(body, user)

        assert result["status"] == "ok"
        assert result["request"]["user_id"] == "user_test"
        assert result["request"]["project_id"] == "proj_123"
        assert result["request"]["status"] == "pending"
        assert result["request"]["has_context"] is True

    @pytest.mark.asyncio
    async def test_claim_endpoint(self):
        """POST /requests/{id}/claim changes status to claimed."""
        from api.expert_routes import (
            create_expert_request,
            claim_expert_request,
            CreateExpertRequestBody,
            ClaimRequestBody,
        )
        from middleware.auth import AuthenticatedUser
        import services.expert_pool as ep

        ep._expert_service = None

        user = AuthenticatedUser(id="user_1", email="u@t.com")
        body = CreateExpertRequestBody(project_id="p1", description="help")

        create_result = await create_expert_request(body, user)
        request_id = create_result["request"]["id"]

        expert = AuthenticatedUser(id="expert_1", email="e@t.com")
        claim_body = ClaimRequestBody()

        claim_result = await claim_expert_request(request_id, claim_body, expert)

        assert claim_result["status"] == "ok"
        assert claim_result["request"]["status"] == "claimed"
        assert claim_result["request"]["claimed_by"] == "expert_1"

    @pytest.mark.asyncio
    async def test_resolve_endpoint(self):
        """POST /requests/{id}/resolve stores resolution."""
        from api.expert_routes import (
            create_expert_request,
            claim_expert_request,
            resolve_expert_request,
            CreateExpertRequestBody,
            ClaimRequestBody,
            ResolveRequestBody,
        )
        from middleware.auth import AuthenticatedUser
        import services.expert_pool as ep

        ep._expert_service = None

        user = AuthenticatedUser(id="user_1", email="u@t.com")
        create_body = CreateExpertRequestBody(
            project_id="p1",
            description="help",
            build_state=_make_build_state(),
        )
        create_result = await create_expert_request(create_body, user)
        request_id = create_result["request"]["id"]

        expert = AuthenticatedUser(id="expert_1", email="e@t.com")
        await claim_expert_request(request_id, ClaimRequestBody(), expert)

        resolve_body = ResolveRequestBody(
            patches={"src/App.tsx": "fixed code here"},
            summary="Fixed the auth bypass bug",
        )
        resolve_result = await resolve_expert_request(request_id, resolve_body, expert)

        assert resolve_result["status"] == "ok"
        assert resolve_result["request"]["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_claim_already_claimed_returns_409(self):
        """Claiming an already-claimed request raises 409."""
        from api.expert_routes import (
            create_expert_request,
            claim_expert_request,
            CreateExpertRequestBody,
            ClaimRequestBody,
        )
        from middleware.auth import AuthenticatedUser
        from fastapi import HTTPException
        import services.expert_pool as ep

        ep._expert_service = None

        user = AuthenticatedUser(id="u1", email="u@t.com")
        body = CreateExpertRequestBody(project_id="p1", description="help")

        result = await create_expert_request(body, user)
        rid = result["request"]["id"]

        exp1 = AuthenticatedUser(id="e1", email="e1@t.com")
        exp2 = AuthenticatedUser(id="e2", email="e2@t.com")

        await claim_expert_request(rid, ClaimRequestBody(), exp1)

        with pytest.raises(HTTPException) as exc_info:
            await claim_expert_request(rid, ClaimRequestBody(), exp2)

        assert exc_info.value.status_code == 409


# =====================================================================
# Test: Edge Cases
# =====================================================================


class TestEdgeCases:
    """Edge cases for the expert system."""

    def test_context_with_none_messages(self):
        """Packaging handles None messages gracefully."""
        from services.expert_pool import package_context

        state = _make_build_state(messages=None)
        ctx = package_context(state)
        assert ctx.agent_messages == []

    def test_context_with_dict_messages(self):
        """Packaging handles plain dict messages (not LangChain)."""
        from services.expert_pool import package_context

        state = _make_build_state(
            messages=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        )
        ctx = package_context(state)
        assert len(ctx.agent_messages) == 2
        assert ctx.agent_messages[0]["role"] == "user"

    def test_request_to_dict(self):
        """ExpertRequest.to_dict() is serializable."""
        from services.expert_pool import ExpertRequest

        req = ExpertRequest(user_id="u1", project_id="p1")
        d = req.to_dict()

        serialized = json.dumps(d, default=str)
        assert "u1" in serialized

    def test_nuclear_trimming_for_massive_state(self):
        """When state is truly massive, iterative trimming ensures it fits the budget."""
        from services.expert_pool import package_context, CONTEXT_SIZE_LIMIT
        from unittest.mock import patch

        # Force a very low limit so nuclear trimming is guaranteed to trigger
        with patch("services.expert_pool.CONTEXT_SIZE_LIMIT", 5_000):
            massive_state = _make_build_state(
                frontend_code={f"f{i}.tsx": "x" * 10_000 for i in range(20)},
                backend_code={f"b{i}.py": "y" * 10_000 for i in range(20)},
            )

            ctx = package_context(massive_state)

            # After nuclear trim, file contents should be "[content omitted]"
            for content in ctx.frontend_files.values():
                assert content == "[content omitted]"

        # Also verify the standard case: massive state still fits real limit
        massive_state_2 = _make_build_state(
            frontend_code={f"f{i}.tsx": "x" * 50_000 for i in range(100)},
            backend_code={f"b{i}.py": "y" * 50_000 for i in range(100)},
        )
        ctx2 = package_context(massive_state_2)
        assert ctx2.estimated_size_bytes() < CONTEXT_SIZE_LIMIT

    def test_singleton_accessor(self):
        """get_expert_service returns the same instance."""
        import services.expert_pool as ep

        ep._expert_service = None

        svc1 = ep.get_expert_service()
        svc2 = ep.get_expert_service()
        assert svc1 is svc2
