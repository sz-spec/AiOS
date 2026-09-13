"""
IE-2 Expert-in-the-Loop — End-to-End Integration Verification
==============================================================

This test simulates the FULL SOS flow:

1. A build hits stuck_count == 3 via _route_after_review
2. expert_sos_suggested is set to True in the state
3. POST /api/v1/expert/request packages context under 900 KB
4. An expert claims and resolves the request with patched files
5. MemoryManager.update_procedure is called with the expert fix

Run: pytest backend/tests/verify_expert_end_to_end.py -v
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


def _make_stuck_build_state():
    """
    Build a ProjectState-like dict that is at the brink of Stage 3.

    stuck_count=2 means _route_after_review will bump to 3 → Stage 3 → SOS.
    iteration=5 (>= 3) means the iteration guard won't short-circuit.
    Critical issues present to trigger the stuck path.
    """
    return {
        "messages": [],
        "requirements": "Build a SaaS dashboard with auth, billing, and team management",
        "project_description": "SaaS dashboard",
        "architecture": {
            "tech_stack": {"frontend": "react", "backend": "fastapi", "db": "postgres"},
            "components": ["AuthModule", "BillingModule", "TeamModule"],
        },
        "frontend_code": {
            "src/App.tsx": "import React from 'react';\nexport default function App() { return <div>SaaS</div>; }",
            "src/components/Dashboard.tsx": "export function Dashboard() { return <div>Dashboard</div>; }",
            "src/components/Auth.tsx": "export function Auth() { return <div>Login</div>; }",
            "src/hooks/useAuth.ts": "export function useAuth() { return { user: null }; }",
            "src/lib/api/billing.ts": "export async function getBilling() { return []; }",
        },
        "backend_code": {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()",
            "api/users.py": "from fastapi import APIRouter\nrouter = APIRouter()",
            "api/billing.py": "from fastapi import APIRouter\nrouter = APIRouter()",
        },
        "tests": {
            "test_auth.py": "def test_auth(): assert True",
            "test_billing.py": "def test_billing(): assert True",
        },
        "review_results": {
            "issues": [
                {
                    "severity": "critical",
                    "title": "Auth bypass",
                    "description": "JWT not validated on /api/billing",
                },
                {
                    "severity": "critical",
                    "title": "SQL injection",
                    "description": "Unparameterized query in users.py",
                },
            ]
        },
        "current_phase": "review",
        "iteration": 5,
        "errors": [
            "TypeError: Cannot read property 'id' of undefined",
            "Auth middleware not applied to billing routes",
            "500 Internal Server Error on /api/users/me",
        ],
        "error": None,
        "final_project": None,
        # stuck_count=2 — will be bumped to 3 by _route_after_review
        "stuck_count": 2,
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
                "file_path": "src/components/Dashboard.tsx",
                "line": 5,
                "message": "fetch() call in component",
                "fix_instruction": "Move to hooks/",
                "severity": "critical",
            },
            {
                "rule_id": "ARCH002",
                "file_path": "src/App.tsx",
                "line": 10,
                "message": "API call outside service layer",
                "fix_instruction": "Move to lib/api/",
                "severity": "critical",
            },
        ],
        "guardrails_iteration": 2,
        "expert_sos_suggested": None,
    }


# =====================================================================
# E2E Test: Full SOS Flow
# =====================================================================


class TestExpertEndToEnd:
    """
    End-to-end integration test for the IE-2 Expert-in-the-Loop system.

    Simulates the complete flow from stuck build → SOS → expert fix → memory storage.
    """

    def test_step1_route_after_review_triggers_stage3(self):
        """
        When stuck_count is already 2 and critical issues exist,
        _route_after_review bumps to 3, sets expert_sos_suggested=True,
        and returns 'finalize'.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_stuck_build_state()

        # Verify preconditions
        assert state["stuck_count"] == 2
        assert state["expert_sos_suggested"] is None

        route = builder._route_after_review(state)

        # Stage 3 triggered
        assert route == "finalize"
        assert state["stuck_count"] == 3
        assert state["expert_sos_suggested"] is True

    def test_step2_context_packaging_under_900kb(self):
        """
        package_context produces an ExpertContext that is under 900 KB,
        contains all relevant build information, and is JSON-serializable.
        """
        from services.expert_pool import package_context, CONTEXT_SIZE_LIMIT

        state = _make_stuck_build_state()
        state["stuck_count"] = 3
        state["expert_sos_suggested"] = True

        ctx = package_context(
            state,
            user_description="Auth is completely broken, billing routes unprotected",
        )

        # Size check
        size = ctx.estimated_size_bytes()
        assert (
            size < CONTEXT_SIZE_LIMIT
        ), f"Context is {size} bytes, limit is {CONTEXT_SIZE_LIMIT}"

        # Content check
        assert ctx.requirements.startswith("Build a SaaS")
        assert ctx.architecture is not None
        assert len(ctx.frontend_files) == 5
        assert len(ctx.backend_files) == 3
        assert len(ctx.test_files) == 2
        assert len(ctx.errors) == 3
        assert len(ctx.review_issues) == 2
        assert len(ctx.guardrails_violations) == 2
        assert ctx.stuck_count == 3
        assert (
            ctx.user_description
            == "Auth is completely broken, billing routes unprotected"
        )

        # Serializable
        serialized = json.dumps(ctx.to_dict(), default=str)
        assert len(serialized) > 0

    @pytest.mark.asyncio
    async def test_step3_create_request_endpoint(self):
        """
        POST /api/v1/expert/request creates a pending request with packaged context.
        """
        from api.expert_routes import create_expert_request, CreateExpertRequestBody
        from middleware.auth import AuthenticatedUser
        import services.expert_pool as ep

        # Reset singleton for clean test
        ep._expert_service = None

        state = _make_stuck_build_state()
        state["stuck_count"] = 3
        state["expert_sos_suggested"] = True

        body = CreateExpertRequestBody(
            project_id="proj_saas_001",
            build_id="build_42",
            description="Auth bypass and SQL injection detected after 3 stuck iterations",
            build_state=state,
        )
        user = AuthenticatedUser(id="user_alice", email="alice@company.com")

        result = await create_expert_request(body, user)

        assert result["status"] == "ok"
        req = result["request"]
        assert req["user_id"] == "user_alice"
        assert req["project_id"] == "proj_saas_001"
        assert req["build_id"] == "build_42"
        assert req["status"] == "pending"
        assert req["has_context"] is True

        # Verify context was packaged under 900 KB
        svc = ep.get_expert_service()
        full_req = svc.get_request(req["id"])
        ctx_json = json.dumps(full_req.context, default=str)
        assert len(ctx_json.encode("utf-8")) < 900 * 1024

    @pytest.mark.asyncio
    async def test_step4_expert_claims_and_resolves(self):
        """
        Full lifecycle: create → claim → resolve with patched file.
        """
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

        # Step 1: User creates request
        user = AuthenticatedUser(id="user_bob", email="bob@company.com")
        body = CreateExpertRequestBody(
            project_id="proj_saas_002",
            description="Stuck on auth",
            build_state=_make_stuck_build_state(),
        )
        create_result = await create_expert_request(body, user)
        request_id = create_result["request"]["id"]

        # Step 2: Expert claims
        expert = AuthenticatedUser(id="expert_charlie", email="charlie@experts.com")
        claim_result = await claim_expert_request(
            request_id, ClaimRequestBody(), expert
        )

        assert claim_result["status"] == "ok"
        assert claim_result["request"]["status"] == "claimed"
        assert claim_result["request"]["claimed_by"] == "expert_charlie"

        # Step 3: Expert resolves with patches
        resolve_body = ResolveRequestBody(
            patches={
                "api/users.py": (
                    "from fastapi import APIRouter, Depends\n"
                    "from middleware.auth import get_current_user\n\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/me')\n"
                    "async def get_me(user=Depends(get_current_user)):\n"
                    "    return {'id': user.id}\n"
                ),
                "api/billing.py": (
                    "from fastapi import APIRouter, Depends\n"
                    "from middleware.auth import get_current_user\n\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/')\n"
                    "async def get_billing(user=Depends(get_current_user)):\n"
                    "    return {'plans': []}\n"
                ),
            },
            summary="Added JWT auth middleware to all billing and user routes, fixed SQL parameterization",
        )

        # Mock MemoryManager to avoid needing real memory backend
        from unittest.mock import patch, MagicMock

        with patch("memory.MemoryManager") as MockMM:
            mock_instance = MagicMock()
            mock_instance.update_procedure.return_value = "mem_expert_fix_001"
            MockMM.return_value = mock_instance

            resolve_result = await resolve_expert_request(
                request_id, resolve_body, expert
            )

        assert resolve_result["status"] == "ok"
        assert resolve_result["request"]["status"] == "resolved"
        assert resolve_result["memory_stored"] is True
        assert resolve_result["memory_id"] == "mem_expert_fix_001"

    @pytest.mark.asyncio
    async def test_step5_memory_manager_called_correctly(self):
        """
        When an expert resolves a request, MemoryManager.update_procedure()
        is called with the fix instruction, agent_id='frontend', and priority=5.
        """
        from api.expert_routes import (
            create_expert_request,
            claim_expert_request,
            resolve_expert_request,
            CreateExpertRequestBody,
            ClaimRequestBody,
            ResolveRequestBody,
        )
        from middleware.auth import AuthenticatedUser
        from unittest.mock import patch, MagicMock
        import services.expert_pool as ep

        ep._expert_service = None

        user = AuthenticatedUser(id="user_d", email="d@t.com")
        body = CreateExpertRequestBody(
            project_id="p1",
            description="Stuck",
            build_state=_make_stuck_build_state(),
        )
        create_result = await create_expert_request(body, user)
        rid = create_result["request"]["id"]

        expert = AuthenticatedUser(id="exp_1", email="e@t.com")
        await claim_expert_request(rid, ClaimRequestBody(), expert)

        resolve_body = ResolveRequestBody(
            patches={"src/App.tsx": "fixed auth code"},
            summary="Fixed JWT validation in auth middleware",
        )

        with patch("memory.MemoryManager") as MockMM:
            mock_instance = MagicMock()
            mock_instance.update_procedure.return_value = "mem_999"
            MockMM.return_value = mock_instance

            await resolve_expert_request(rid, resolve_body, expert)

            # Verify MemoryManager was instantiated and called
            MockMM.assert_called_once()
            mock_instance.update_procedure.assert_called_once()

            call_kwargs = mock_instance.update_procedure.call_args
            # Verify correct parameters
            instruction = (
                call_kwargs.kwargs.get("instruction")
                or call_kwargs[1].get("instruction", "")
                or call_kwargs[0][0]
            )
            agent_id = call_kwargs.kwargs.get("agent_id") or call_kwargs[1].get(
                "agent_id", ""
            )
            priority = call_kwargs.kwargs.get("priority") or call_kwargs[1].get(
                "priority", 0
            )

            assert "EXPERT FIX" in instruction
            assert "Fixed JWT validation" in instruction
            assert "src/App.tsx" in instruction
            assert agent_id == "frontend"
            assert priority == 5

    def test_step6_full_flow_state_consistency(self):
        """
        Verify that the entire flow maintains state consistency:
        - ProjectState has expert_sos_suggested field
        - Stage 3 sets it to True
        - Context packaging preserves stuck_count and iteration
        - Request lifecycle is consistent (pending → claimed → resolved)
        """
        from ai.agents.multi_agent import MultiAgentBuilder, ProjectState
        from services.expert_pool import (
            package_context,
            ExpertPoolService,
            CONTEXT_SIZE_LIMIT,
        )
        import typing

        # 1. Verify field exists in TypedDict
        hints = typing.get_type_hints(ProjectState)
        assert "expert_sos_suggested" in hints

        # 2. Route triggers Stage 3
        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_stuck_build_state()
        route = builder._route_after_review(state)
        assert route == "finalize"
        assert state["expert_sos_suggested"] is True
        assert state["stuck_count"] == 3

        # 3. Context packaging preserves state
        ctx = package_context(state, user_description="E2E test")
        assert ctx.stuck_count == 3
        assert ctx.iteration == 5
        assert ctx.estimated_size_bytes() < CONTEXT_SIZE_LIMIT

        # 4. Request lifecycle
        svc = ExpertPoolService()
        req = svc.create_request("u1", "p1", ctx)
        assert req.status == "pending"

        claimed = svc.claim_request(req.id, "exp1")
        assert claimed.status == "claimed"

        resolved = svc.resolve_request(
            req.id,
            {
                "patches": {"fix.py": "print('fixed')"},
                "summary": "E2E fix",
            },
        )
        assert resolved.status == "resolved"
        assert resolved.resolution["patches"]["fix.py"] == "print('fixed')"

        # 5. Double operations fail
        assert svc.claim_request(req.id, "exp2") is None  # Already resolved
        assert (
            svc.resolve_request(req.id, {"summary": "dup"}) is None
        )  # Already resolved
