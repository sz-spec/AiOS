"""
Phase 3 Integration Tests — Auth (3.1) + Export (3.3) End-to-End
================================================================
Cross-subsystem integration test that simulates a full VOS3-mode build
of "social feed app with Clerk authentication and a PostgreSQL database".

Verifies the entire chain:
  1. Auth Detection:   detect_auth_strategy → "clerk"
  2. Template Injection: frontend_code + backend_code contain Clerk templates
  3. Tester Augmentation: TesterAgent prompt includes Clerk's MANDATORY scenarios
  4. Finalization:      _finalize_node merges agent files + export files
  5. SEC-EXP-01:        deploy.yml uses 40-char SHA for every action
  6. Env Rehydration:   SECRETS_SETUP.md lists CLERK_SECRET_KEY + DATABASE_URL

This is the cross-cutting integration that ensures Subsystems 3.1 and 3.3
work together end-to-end inside a single simulated pipeline run.
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# Ensure backend root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.agents.auth_injector import (
    detect_auth_strategy,
    get_test_scenarios,
    inject_auth_into_state,
)
from ai.agents.multi_agent import MultiAgentBuilder, TesterAgent
from services.export_service import PINNED_ACTIONS, TRANSITIVE_RISK_ACTIONS

# =============================================================================
# Constants
# =============================================================================

REQUIREMENTS = (
    "Build a social feed app with Clerk authentication and a PostgreSQL database."
)

TECH_STACK = {
    "frontend": "Next.js 14 + Tailwind CSS",
    "backend": "FastAPI",
    "database": "PostgreSQL",
    "node_version": "20",
}

# Agent-generated files that simulate a social feed app
AGENT_FRONTEND_CODE = {
    "src/app/page.tsx": (
        'export default function Home() { return <div className="feed">Social Feed</div>; }'
    ),
    "src/app/layout.tsx": (
        "export default function Layout({ children }: { children: React.ReactNode }) "
        "{ return <html><body>{children}</body></html>; }"
    ),
    "src/components/PostCard.tsx": (
        "export function PostCard({ post }: { post: Post }) "
        "{ return <div className='card'>{post.content}</div>; }"
    ),
    "src/components/FeedList.tsx": (
        "export function FeedList() { return <div>Feed list</div>; }"
    ),
    "src/lib/api.ts": (
        "export async function fetchPosts() { return fetch('/api/posts').then(r => r.json()); }"
    ),
}

AGENT_BACKEND_CODE = {
    "api/main.py": (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.get("/health")\n'
        "def health(): return {'ok': True}"
    ),
    "api/routes/posts.py": (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        '@router.get("/posts")\n'
        "def get_posts(): return []"
    ),
    "api/models.py": (
        "from sqlalchemy import Column, Integer, String, Text, DateTime\n"
        "class Post: pass\n"
        "class User: pass"
    ),
}

AGENT_TESTS = {
    "tests/test_posts.py": "def test_get_posts(): assert True",
}


# =============================================================================
# Helpers
# =============================================================================


def _build_integrated_state() -> dict:
    """
    Simulate the full pipeline state as it would exist at the finalize gate,
    AFTER auth injection in frontend_node and backend_node.
    """
    # Step 1: Detect auth strategy (architect_node)
    auth_strategy = detect_auth_strategy(REQUIREMENTS)

    # Step 2: Inject auth into frontend code (frontend_node)
    frontend_injected = inject_auth_into_state(
        auth_strategy,
        frontend_code=dict(AGENT_FRONTEND_CODE),
        backend_code={},
    )

    # Step 3: Inject auth into backend code (backend_node)
    backend_injected = inject_auth_into_state(
        auth_strategy,
        frontend_code={},
        backend_code=dict(AGENT_BACKEND_CODE),
    )

    return {
        "messages": [],
        "requirements": REQUIREMENTS,
        "architecture": {
            "project_name": "social-feed-app",
            "tech_stack": TECH_STACK,
            "auth_strategy": auth_strategy,
            "deploy_target": "docker",
        },
        "frontend_code": frontend_injected["frontend_code"],
        "backend_code": backend_injected["backend_code"],
        "tests": dict(AGENT_TESTS),
        "review_results": {"score": 88, "issues": []},
        "iteration": 1,
        "current_phase": "finalize",
    }


def _run_finalize(state: dict) -> dict:
    """Call _finalize_node on the given state and return final_project."""
    builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
    result = builder._finalize_node.__wrapped__(builder, state)
    return result["final_project"]


# =============================================================================
# Module-scoped fixtures
# =============================================================================


@pytest.fixture(scope="module")
def integrated_state():
    """The fully-assembled state with auth injection applied."""
    return _build_integrated_state()


@pytest.fixture(scope="module")
def final_project(integrated_state):
    """The finalized project after _finalize_node runs."""
    return _run_finalize(dict(integrated_state))


@pytest.fixture(scope="module")
def project_files(final_project):
    """The file dict from the finalized project."""
    return final_project["files"]


@pytest.fixture(scope="module")
def workflow_yaml(project_files):
    """Parsed YAML of the generated deploy workflow."""
    raw = project_files[".github/workflows/deploy.yml"]
    return yaml.safe_load(raw)


@pytest.fixture(scope="module")
def secrets_setup_md(project_files):
    """Raw SECRETS_SETUP.md content."""
    return project_files["SECRETS_SETUP.md"]


# =============================================================================
# 1. Auth Detection (Subsystem 3.1)
# =============================================================================


class TestAuthDetection:
    """Verify detect_auth_strategy picks 'clerk' from the requirements."""

    def test_requirements_trigger_clerk(self):
        """'Clerk authentication' in requirements → strategy = 'clerk'."""
        strategy = detect_auth_strategy(REQUIREMENTS)
        assert strategy == "clerk"

    def test_clerk_not_custom_jwt(self):
        """
        Even though the word 'authentication' (a custom_jwt keyword) appears,
        'Clerk' (explicit) has higher priority.
        """
        strategy = detect_auth_strategy(REQUIREMENTS)
        assert strategy != "custom_jwt"


# =============================================================================
# 2. Template Injection (Subsystem 3.1)
# =============================================================================


class TestTemplateInjection:
    """Verify Clerk template files are injected into code dicts."""

    def test_frontend_has_clerk_signin(self, integrated_state):
        """Frontend code includes Clerk SignIn component."""
        fc = integrated_state["frontend_code"]
        assert "src/components/auth/SignIn.tsx" in fc
        assert "SignIn" in fc["src/components/auth/SignIn.tsx"]

    def test_frontend_has_clerk_signup(self, integrated_state):
        """Frontend code includes Clerk SignUp component."""
        fc = integrated_state["frontend_code"]
        assert "src/components/auth/SignUp.tsx" in fc

    def test_frontend_has_clerk_userbutton(self, integrated_state):
        """Frontend code includes Clerk UserButton component."""
        fc = integrated_state["frontend_code"]
        assert "src/components/auth/UserButton.tsx" in fc

    def test_frontend_has_clerk_middleware(self, integrated_state):
        """Frontend code includes Clerk middleware.ts."""
        fc = integrated_state["frontend_code"]
        assert "middleware.ts" in fc
        assert "clerkMiddleware" in fc["middleware.ts"]

    def test_frontend_preserves_agent_files(self, integrated_state):
        """Agent-generated UI files are preserved alongside auth templates."""
        fc = integrated_state["frontend_code"]
        assert "src/app/page.tsx" in fc
        assert "src/components/PostCard.tsx" in fc
        assert "src/components/FeedList.tsx" in fc

    def test_backend_has_clerk_auth_middleware(self, integrated_state):
        """Backend code includes Clerk JWT verification middleware."""
        bc = integrated_state["backend_code"]
        assert "middleware/auth.py" in bc
        content = bc["middleware/auth.py"]
        # Clerk middleware should verify JWT
        assert "jwt" in content.lower() or "clerk" in content.lower()

    def test_backend_has_auth_routes(self, integrated_state):
        """Backend code includes auth route endpoints."""
        bc = integrated_state["backend_code"]
        assert "routes/auth.py" in bc

    def test_backend_preserves_agent_files(self, integrated_state):
        """Agent-generated backend files are preserved alongside auth templates."""
        bc = integrated_state["backend_code"]
        assert "api/main.py" in bc
        assert "api/routes/posts.py" in bc
        assert "api/models.py" in bc


# =============================================================================
# 3. Tester Augmentation (Subsystem 3.1)
# =============================================================================


class TestTesterAugmentation:
    """Verify TesterAgent prompt includes Clerk's MANDATORY auth scenarios."""

    def test_clerk_scenarios_exist(self):
        """Clerk manifest has ≥ 4 test scenarios."""
        scenarios = get_test_scenarios("clerk")
        assert len(scenarios) >= 4

    def test_tester_prompt_includes_clerk_scenarios(self, integrated_state):
        """
        TesterAgent.invoke() produces a prompt containing MANDATORY Auth Test
        Scenarios from the Clerk manifest when auth_test_scenarios are injected.
        """
        agent = TesterAgent.__new__(TesterAgent)
        agent.system_prompt = "You are a QA engineer."

        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(content="No files.")
        agent.llm = mock_llm

        # Simulate what _tester_node does: inject auth_test_scenarios
        tester_state = dict(integrated_state)
        tester_state["auth_test_scenarios"] = get_test_scenarios("clerk")

        agent.invoke(tester_state)

        prompt = mock_llm.generate.call_args[0][0]
        assert "MANDATORY Auth Test Scenarios" in prompt
        assert "ClerkProvider wraps the app in layout.tsx" in prompt
        assert "Middleware protects non-public routes" in prompt

    def test_tester_node_injects_scenarios(self, integrated_state):
        """_tester_node auto-injects auth_test_scenarios for Clerk strategy."""
        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        builder.agents = {}
        builder.config = {}
        builder.memory_saver = None

        captured = {}

        def capture(state):
            captured.update(state)
            return {"messages": [], "tests": {}, "current_phase": "reviewer"}

        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = capture
        builder.agents["tester"] = mock_agent

        builder._tester_node.__wrapped__(builder, dict(integrated_state))

        assert "auth_test_scenarios" in captured
        assert len(captured["auth_test_scenarios"]) >= 4
        assert any("ClerkProvider" in s for s in captured["auth_test_scenarios"])


# =============================================================================
# 4. Finalization & Export (Subsystem 3.3)
# =============================================================================


class TestFinalizationExport:
    """Verify _finalize_node merges agent + auth + export files correctly."""

    # ---- 4a. Agent + auth UI files survive finalization ----

    def test_final_has_agent_ui_files(self, project_files):
        """Agent-generated UI files are present in final_project."""
        assert "src/app/page.tsx" in project_files
        assert "src/components/PostCard.tsx" in project_files
        assert "src/components/FeedList.tsx" in project_files

    def test_final_has_auth_template_files(self, project_files):
        """Clerk auth template files are present in final_project."""
        assert "src/components/auth/SignIn.tsx" in project_files
        assert "middleware.ts" in project_files
        assert "middleware/auth.py" in project_files
        assert "routes/auth.py" in project_files

    # ---- 4b. Standard export files are present ----

    def test_final_has_dockerfile(self, project_files):
        """Dockerfile generated by export service."""
        assert "Dockerfile" in project_files
        assert "FROM node:" in project_files["Dockerfile"]

    def test_final_has_docker_compose(self, project_files):
        """docker-compose.yml generated with PostgreSQL service."""
        assert "docker-compose.yml" in project_files
        content = project_files["docker-compose.yml"]
        assert "postgres" in content.lower()

    def test_final_has_deploy_workflow(self, project_files):
        """CI/CD workflow at .github/workflows/deploy.yml."""
        assert ".github/workflows/deploy.yml" in project_files

    def test_final_has_package_json(self, project_files):
        """package.json generated."""
        assert "package.json" in project_files
        pkg = json.loads(project_files["package.json"])
        assert "name" in pkg
        assert "scripts" in pkg

    def test_final_has_env_example(self, project_files):
        """.env.example generated."""
        assert ".env.example" in project_files

    def test_final_has_readme(self, project_files):
        """README.md generated."""
        assert "README.md" in project_files

    def test_final_has_secrets_setup(self, project_files):
        """SECRETS_SETUP.md generated."""
        assert "SECRETS_SETUP.md" in project_files

    def test_final_has_gitignore(self, project_files):
        """.gitignore generated."""
        assert ".gitignore" in project_files


# =============================================================================
# 5. SEC-EXP-01: SHA Pinning (Subsystem 3.3)
# =============================================================================


class TestSECEXP01:
    """Every `uses:` in deploy.yml must reference a 40-char commit SHA."""

    SHA_PATTERN = re.compile(r"@([0-9a-f]{40})")

    def _extract_uses(self, workflow: dict) -> list[str]:
        """Recursively extract all `uses:` values from parsed YAML."""
        uses_values = []

        def _walk(obj):
            if isinstance(obj, dict):
                for key, val in obj.items():
                    if key == "uses" and isinstance(val, str):
                        uses_values.append(val)
                    else:
                        _walk(val)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(workflow)
        return uses_values

    def test_workflow_has_uses_steps(self, workflow_yaml):
        """deploy.yml contains at least one `uses:` step."""
        uses = self._extract_uses(workflow_yaml)
        assert len(uses) >= 1, "No `uses:` steps found in deploy.yml"

    def test_every_uses_is_sha_pinned(self, workflow_yaml):
        """Every `uses:` reference contains a 40-char hex SHA after '@'."""
        uses = self._extract_uses(workflow_yaml)
        violations = []
        for ref in uses:
            if not self.SHA_PATTERN.search(ref):
                violations.append(ref)
        assert violations == [], (
            f"SEC-EXP-01 violation: {len(violations)} action(s) not SHA-pinned:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_no_mutable_tags_without_sha(self, workflow_yaml):
        """No action uses a mutable tag (e.g., @v4) without SHA."""
        uses = self._extract_uses(workflow_yaml)
        mutable = re.compile(r"@v\d")
        for ref in uses:
            if mutable.search(ref) and not self.SHA_PATTERN.search(ref):
                pytest.fail(f"Mutable tag without SHA: {ref}")

    def test_shas_match_pinned_registry(self, workflow_yaml):
        """All SHAs used in deploy.yml match PINNED_ACTIONS registry."""
        uses = self._extract_uses(workflow_yaml)
        for ref in uses:
            action_name = ref.split("@")[0].strip()
            sha_match = self.SHA_PATTERN.search(ref)
            if sha_match:
                actual_sha = sha_match.group(1)
                if action_name in PINNED_ACTIONS:
                    expected_sha = PINNED_ACTIONS[action_name]["sha"]
                    assert actual_sha == expected_sha, (
                        f"{action_name}: SHA mismatch\n"
                        f"  expected: {expected_sha}\n"
                        f"  actual:   {actual_sha}"
                    )


# =============================================================================
# 6. Environment Rehydration (Subsystem 3.3)
# =============================================================================


class TestEnvRehydration:
    """SECRETS_SETUP.md lists expected secrets for Clerk + PostgreSQL stack."""

    def test_secrets_md_lists_clerk_secret_key(self, secrets_setup_md):
        """CLERK_SECRET_KEY must appear in SECRETS_SETUP.md."""
        assert "CLERK_SECRET_KEY" in secrets_setup_md

    def test_secrets_md_lists_database_url(self, secrets_setup_md):
        """DATABASE_URL must appear in SECRETS_SETUP.md."""
        assert "DATABASE_URL" in secrets_setup_md

    def test_secrets_md_lists_clerk_publishable_as_variable(self, secrets_setup_md):
        """
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY should appear in the Variables
        section (not Secrets), since NEXT_PUBLIC_* are non-sensitive.
        """
        assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in secrets_setup_md

    def test_env_example_has_clerk_vars(self, project_files):
        """.env.example includes Clerk auth variables."""
        env = project_files[".env.example"]
        assert "CLERK_SECRET_KEY" in env
        assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in env

    def test_env_example_has_database_url(self, project_files):
        """.env.example includes DATABASE_URL for PostgreSQL."""
        env = project_files[".env.example"]
        assert "DATABASE_URL" in env


# =============================================================================
# 7. Cross-Cutting Integrity
# =============================================================================


class TestCrossCuttingIntegrity:
    """End-to-end integrity checks across both subsystems."""

    def test_zero_lockin_no_vbuilder_refs(self, project_files):
        """
        Acid Test: no file in the final project references 'vbuilder'
        (case-insensitive). Auth templates + export files must all be clean.
        """
        violations = []
        for fpath, content in project_files.items():
            for line_no, line in enumerate(content.splitlines(), 1):
                if "vbuilder" in line.lower():
                    violations.append(f"  {fpath}:{line_no}: {line.strip()}")
        assert violations == [], (
            f"Zero Lock-in violation: {len(violations)} reference(s) to 'vbuilder':\n"
            + "\n".join(violations)
        )

    def test_final_file_count(self, project_files):
        """
        Final project should have:
        - 5 agent UI files + 5 Clerk frontend templates + 1 middleware.ts
        - 3 agent backend files + 2 Clerk backend templates
        - 1 test file
        - 8+ export files (Dockerfile, docker-compose, deploy.yml, package.json,
          .env.example, README, SECRETS_SETUP, .gitignore, tsconfig)
        Total: ≥ 25 files
        """
        assert (
            len(project_files) >= 25
        ), f"Expected ≥ 25 files, got {len(project_files)}:\n" + "\n".join(
            f"  - {f}" for f in sorted(project_files)
        )

    def test_auth_strategy_in_summary(self, final_project):
        """Build summary includes the detected auth_strategy."""
        arch = final_project["summary"]["architecture"]
        assert arch.get("auth_strategy") == "clerk"

    def test_package_json_has_clerk_dep(self, project_files):
        """
        package.json should include @clerk/nextjs dependency since
        auth_strategy is 'clerk'.
        """
        pkg = json.loads(project_files["package.json"])
        deps = pkg.get("dependencies", {})
        assert (
            "@clerk/nextjs" in deps
        ), f"@clerk/nextjs not found in package.json dependencies: {list(deps.keys())}"

    def test_workflow_has_permissions(self, workflow_yaml):
        """deploy.yml has top-level permissions (least privilege)."""
        # YAML `on:` is parsed as boolean True, so check both
        assert "permissions" in workflow_yaml, "Missing top-level permissions"
        perms = workflow_yaml["permissions"]
        assert "contents" in perms, "Missing contents permission"


# =============================================================================
# 8. SEC-EXP-02: Supply Chain Hardening
# =============================================================================


class TestSupplyChainHardening:
    """
    Verify OIDC auth, transitive risk warnings, and runner scrubbing
    are present in the generated deploy.yml workflow.
    """

    def test_deploy_job_has_oidc_permission(self, workflow_yaml):
        """Deploy job must include 'id-token: write' for OIDC authentication."""
        deploy_job = workflow_yaml["jobs"]["deploy"]
        perms = deploy_job.get("permissions", {})
        assert (
            perms.get("id-token") == "write"
        ), f"Missing id-token: write in deploy job permissions. Got: {perms}"

    def test_deploy_job_retains_contents_read(self, workflow_yaml):
        """Deploy job must still have 'contents: read' alongside OIDC."""
        deploy_job = workflow_yaml["jobs"]["deploy"]
        perms = deploy_job.get("permissions", {})
        assert (
            perms.get("contents") == "read"
        ), f"Missing contents: read in deploy job permissions. Got: {perms}"

    def test_scrub_step_present(self, project_files):
        """deploy.yml must contain a Post-Deployment Cleanup step."""
        raw = project_files[".github/workflows/deploy.yml"]
        assert (
            "Post-Deployment Cleanup" in raw
        ), "Missing 'Post-Deployment Cleanup' step in deploy.yml"

    def test_scrub_step_runs_always(self, project_files):
        """Cleanup step must use 'if: always()' to run even on failure."""
        raw = project_files[".github/workflows/deploy.yml"]
        assert "if: always()" in raw, "Missing 'if: always()' on cleanup step"

    def test_scrub_step_unsets_secrets(self, project_files):
        """Cleanup step must unset injected secret environment variables."""
        raw = project_files[".github/workflows/deploy.yml"]
        # Clerk + PostgreSQL stack should unset these secrets
        assert (
            "unset CLERK_SECRET_KEY" in raw
        ), "Missing 'unset CLERK_SECRET_KEY' in cleanup step"
        assert (
            "unset DATABASE_URL" in raw
        ), "Missing 'unset DATABASE_URL' in cleanup step"

    def test_scrub_step_echoes_confirmation(self, project_files):
        """Cleanup step must echo confirmation message."""
        raw = project_files[".github/workflows/deploy.yml"]
        assert "Environment scrubbed." in raw

    def test_transitive_risk_registry_exists(self):
        """TRANSITIVE_RISK_ACTIONS registry must be defined and non-empty."""
        assert isinstance(TRANSITIVE_RISK_ACTIONS, dict)
        assert len(TRANSITIVE_RISK_ACTIONS) >= 1

    def test_transitive_risk_actions_are_pinned(self):
        """Every action in TRANSITIVE_RISK_ACTIONS must also be in PINNED_ACTIONS."""
        for action_name in TRANSITIVE_RISK_ACTIONS:
            assert (
                action_name in PINNED_ACTIONS
            ), f"Transitive-risk action '{action_name}' is not in PINNED_ACTIONS"

    def test_transitive_risk_comment_in_workflow(self, project_files):
        """
        Actions with transitive risks must have a TRANSITIVE-RISK comment
        in the generated YAML for operator awareness.
        """
        raw = project_files[".github/workflows/deploy.yml"]
        # Docker deploy steps use build-push-action which is in the risk registry
        if "docker/build-push-action" in raw:
            assert (
                "TRANSITIVE-RISK" in raw
            ), "docker/build-push-action is used but TRANSITIVE-RISK comment is missing"

    def test_secrets_setup_has_oidc_section(self, project_files):
        """SECRETS_SETUP.md must include OIDC migration documentation."""
        md = project_files["SECRETS_SETUP.md"]
        assert "OIDC Authentication" in md, "Missing OIDC section header"
        assert "id-token: write" in md, "Missing id-token reference in OIDC docs"
        assert "short-lived" in md, "Missing OIDC benefit explanation"
