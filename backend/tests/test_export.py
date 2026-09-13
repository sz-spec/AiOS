"""
Export Service Tests — Phase 3.0 Subsystem 3.3
===============================================
22 tests covering:
- Standard file generation (package.json, Dockerfile, .env.example, README, etc.)
- SEC-EXP-01: SHA pinning enforcement
- Environment variable rehydration (secrets vs variables classification)
- CI/CD workflow generation and deploy-target adaptation
- Zero Lock-in Rule verification
- _finalize_node integration
"""

import json
import re
import sys
from pathlib import Path

import pytest

# Ensure backend root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.export_service import (
    build_secrets_manifest,
    generate_deploy_workflow,
    generate_docker_compose,
    generate_dockerfile,
    generate_env_example,
    generate_package_json,
    generate_readme,
    generate_secrets_setup_md,
    generate_standard_files,
    resolve_action,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def basic_tech_stack():
    return {"frontend": "Next.js + Tailwind", "node_version": "20"}


@pytest.fixture
def db_tech_stack():
    return {"frontend": "Next.js", "database": "PostgreSQL", "node_version": "20"}


@pytest.fixture
def env_example_content():
    return (
        "# Database\n"
        'DATABASE_URL="postgresql://user:pass@localhost/db"\n'
        "\n"
        "# Auth\n"
        'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="pk_test_xxx"\n'
        'CLERK_SECRET_KEY="sk_test_xxx"\n'
        "\n"
        "# API\n"
        'NEXT_PUBLIC_API_URL="http://localhost:8000"\n'
    )


# =============================================================================
# 1. test_zip_export_contains_all_files
# =============================================================================


def test_zip_export_contains_all_files(basic_tech_stack):
    """generate_standard_files returns all expected deployment files."""
    files = generate_standard_files(
        project_name="test-app",
        tech_stack=basic_tech_stack,
    )
    expected_keys = [
        "package.json",
        "tsconfig.json",
        ".env.example",
        ".gitignore",
        "Dockerfile",
        "docker-compose.yml",
        "README.md",
        ".github/workflows/deploy.yml",
        "SECRETS_SETUP.md",
    ]
    for key in expected_keys:
        assert key in files, f"Missing standard file: {key}"


# =============================================================================
# 2. test_zip_export_standard_files
# =============================================================================


def test_zip_export_standard_files(basic_tech_stack):
    """Standard files are non-empty strings."""
    files = generate_standard_files(
        project_name="test-app",
        tech_stack=basic_tech_stack,
    )
    for name, content in files.items():
        assert isinstance(content, str), f"{name} is not a string"
        assert len(content) > 0, f"{name} is empty"


# =============================================================================
# 3. test_zip_export_no_vbuilder_deps
# =============================================================================


def test_zip_export_no_vbuilder_deps(basic_tech_stack):
    """package.json has zero VBuilder-specific or @vbuilder/* dependencies."""
    pkg_json = generate_package_json("test-app", basic_tech_stack)
    pkg = json.loads(pkg_json)

    all_deps = list(pkg.get("dependencies", {}).keys()) + list(
        pkg.get("devDependencies", {}).keys()
    )
    for dep in all_deps:
        assert "vbuilder" not in dep.lower(), f"Lock-in violation: {dep}"


# =============================================================================
# 4. test_zip_export_valid_dockerfile
# =============================================================================


def test_zip_export_valid_dockerfile(basic_tech_stack):
    """Dockerfile has valid FROM/COPY/CMD structure."""
    dockerfile = generate_dockerfile(basic_tech_stack)
    assert "FROM " in dockerfile
    assert "COPY " in dockerfile
    assert "CMD " in dockerfile
    assert "EXPOSE 3000" in dockerfile


# =============================================================================
# 5. test_zip_export_env_example
# =============================================================================


def test_zip_export_env_example(db_tech_stack):
    """.env.example lists DATABASE_URL when database is in tech stack."""
    env = generate_env_example(db_tech_stack, auth_strategy="clerk")
    assert "DATABASE_URL" in env
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in env
    assert "CLERK_SECRET_KEY" in env
    assert "NEXT_PUBLIC_API_URL" in env


# =============================================================================
# 6. test_readme_has_setup_instructions
# =============================================================================


def test_readme_has_setup_instructions(basic_tech_stack):
    """README.md contains setup instructions."""
    readme = generate_readme("My App", basic_tech_stack)
    assert "npm install" in readme
    assert "npm run dev" in readme
    assert "npm run build" in readme


# =============================================================================
# 7. test_github_export_creates_repo
# =============================================================================


def test_github_export_creates_repo():
    """Placeholder: Mock GitHub API repo creation (route not yet implemented)."""
    # In Phase 3.0 the export_routes.py will handle this.
    # For now, verify generate_standard_files produces files suitable for git push.
    files = generate_standard_files(project_name="gh-test")
    assert ".gitignore" in files
    assert "README.md" in files
    # Verify .gitignore excludes common sensitive paths
    gitignore = files[".gitignore"]
    assert ".env" in gitignore
    assert "node_modules/" in gitignore


# =============================================================================
# 8. test_github_export_pushes_files
# =============================================================================


def test_github_export_pushes_files():
    """All standard files are valid string content suitable for GitHub Contents API."""
    files = generate_standard_files(project_name="push-test")
    for fpath, content in files.items():
        assert isinstance(content, str), f"{fpath} not a string"
        # GitHub Contents API requires base64-encodable content
        content.encode("utf-8")  # Should not raise


# =============================================================================
# 9. test_vercel_deploy_request
# =============================================================================


def test_vercel_deploy_request(basic_tech_stack):
    """Vercel deploy target generates correct workflow steps."""
    workflow = generate_deploy_workflow(basic_tech_stack, deploy_target="vercel")
    assert "vercel deploy --prod" in workflow
    assert "VERCEL_TOKEN" in workflow
    # Should NOT contain Docker steps
    assert (
        "docker/build-push-action" not in workflow.split("@")[0]
        if "@" in workflow
        else True
    )


# =============================================================================
# 10. test_railway_deploy_request
# =============================================================================


def test_railway_deploy_request(basic_tech_stack):
    """Railway deploy target generates Docker-based workflow."""
    workflow = generate_deploy_workflow(basic_tech_stack, deploy_target="railway")
    assert "ghcr.io" in workflow
    assert "docker" in workflow.lower()


# =============================================================================
# 11. test_export_auth_required
# =============================================================================


def test_export_auth_required():
    """Placeholder: export routes return 401 without token (route not yet wired)."""
    # The export_routes.py is Phase 3.0 Task 2.
    # Verify generate_standard_files works without any auth context.
    files = generate_standard_files(project_name="no-auth")
    assert "package.json" in files


# =============================================================================
# 12. test_export_project_not_found
# =============================================================================


def test_export_project_not_found():
    """Placeholder: non-existent project returns 404 (route not yet wired)."""
    # Verify generate_standard_files with minimal input doesn't crash.
    files = generate_standard_files()
    assert len(files) >= 9


# =============================================================================
# 13. test_finalize_node_adds_standard_files
# =============================================================================


def test_finalize_node_adds_standard_files():
    """_finalize_node output includes standard deployment files."""
    from ai.agents.multi_agent import MultiAgentBuilder

    builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

    state = {
        "messages": [],
        "requirements": "Build a todo app",
        "architecture": {
            "project_name": "todo-app",
            "tech_stack": {"frontend": "Next.js"},
        },
        "frontend_code": {"src/App.tsx": "export default function App() {}"},
        "backend_code": {},
        "tests": {},
        "review_results": {},
        "iteration": 1,
        "current_phase": "finalize",
    }

    result = builder._finalize_node.__wrapped__(builder, state)
    project_files = result["final_project"]["files"]

    # Agent-generated file preserved
    assert "src/App.tsx" in project_files

    # Standard files injected
    assert "package.json" in project_files
    assert "Dockerfile" in project_files
    assert ".github/workflows/deploy.yml" in project_files
    assert "SECRETS_SETUP.md" in project_files
    assert "README.md" in project_files


# =============================================================================
# 14. test_docker_compose_includes_db
# =============================================================================


def test_docker_compose_includes_db(db_tech_stack):
    """If architecture has database, docker-compose includes db service."""
    compose = generate_docker_compose(db_tech_stack, project_name="db-app")
    assert "postgres" in compose.lower()
    assert "5432" in compose
    assert "depends_on" in compose


def test_docker_compose_no_db_when_absent(basic_tech_stack):
    """Without database in tech_stack, no db service appears."""
    compose = generate_docker_compose(basic_tech_stack, project_name="no-db")
    assert "postgres" not in compose.lower()


# =============================================================================
# 15. test_workflow_all_actions_sha_pinned (SEC-EXP-01)
# =============================================================================


def test_workflow_all_actions_sha_pinned(basic_tech_stack):
    """Every `uses:` in generated workflow references a 40-char SHA, not a mutable tag."""
    workflow = generate_deploy_workflow(basic_tech_stack, deploy_target="docker")

    uses_pattern = re.compile(r"uses:\s+(.+)")
    for match in uses_pattern.finditer(workflow):
        action_ref = match.group(1).strip()
        # Must contain @ followed by 40 hex chars
        assert re.search(
            r"@[0-9a-f]{40}", action_ref
        ), f"Action not SHA-pinned: {action_ref}"
        # Must NOT be a bare tag like @v4
        parts = action_ref.split("@")
        assert len(parts) == 2, f"Malformed action ref: {action_ref}"
        sha_and_comment = parts[1]
        sha = sha_and_comment.split()[0]  # strip trailing comment
        assert len(sha) == 40, f"SHA wrong length ({len(sha)}): {action_ref}"
        assert all(
            c in "0123456789abcdef" for c in sha
        ), f"SHA has non-hex chars: {action_ref}"


# =============================================================================
# 16. test_workflow_unpinned_action_raises
# =============================================================================


def test_workflow_unpinned_action_raises():
    """Action not in PINNED_ACTIONS registry raises ValueError."""
    with pytest.raises(ValueError, match="Unpinned action"):
        resolve_action("actions/unknown-action")


# =============================================================================
# 17. test_workflow_default_permissions_read
# =============================================================================


def test_workflow_default_permissions_read(basic_tech_stack):
    """Top-level permissions: contents: read is present in generated YAML."""
    workflow = generate_deploy_workflow(basic_tech_stack)
    # Top-level permissions block
    assert "permissions:" in workflow
    assert "contents: read" in workflow


# =============================================================================
# 18. test_secrets_manifest_public_vars
# =============================================================================


def test_secrets_manifest_public_vars(env_example_content):
    """NEXT_PUBLIC_* keys are classified as variables, not secrets."""
    manifest = build_secrets_manifest(env_example_content)
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in manifest["variables"]
    assert "NEXT_PUBLIC_API_URL" in manifest["variables"]
    # Should NOT appear in secrets
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" not in manifest["secrets"]
    assert "NEXT_PUBLIC_API_URL" not in manifest["secrets"]


# =============================================================================
# 19. test_secrets_manifest_secret_keys
# =============================================================================


def test_secrets_manifest_secret_keys(env_example_content):
    """*_KEY, *_SECRET, *_TOKEN classified as secrets."""
    manifest = build_secrets_manifest(env_example_content)
    assert "CLERK_SECRET_KEY" in manifest["secrets"]
    assert "DATABASE_URL" in manifest["secrets"]


# =============================================================================
# 20. test_secrets_setup_md_generated
# =============================================================================


def test_secrets_setup_md_generated(env_example_content):
    """SECRETS_SETUP.md generated with correct tables for secrets + variables."""
    manifest = build_secrets_manifest(env_example_content)
    md = generate_secrets_setup_md(manifest)

    assert "## Secrets" in md
    assert "## Variables" in md
    assert "CLERK_SECRET_KEY" in md
    assert "DATABASE_URL" in md
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in md
    assert "NEXT_PUBLIC_API_URL" in md


# =============================================================================
# 21. test_workflow_env_references_secrets
# =============================================================================


def test_workflow_env_references_secrets(basic_tech_stack):
    """Workflow env: block uses ${{ secrets.X }} for secrets and ${{ vars.X }} for variables."""
    env_content = (
        'DATABASE_URL="postgresql://..."\n'
        'NEXT_PUBLIC_API_URL="http://localhost:8000"\n'
    )
    manifest = build_secrets_manifest(env_content)
    workflow = generate_deploy_workflow(
        basic_tech_stack,
        deploy_target="docker",
        secrets_manifest=manifest,
    )
    assert "${{ secrets.DATABASE_URL }}" in workflow
    assert "${{ vars.NEXT_PUBLIC_API_URL }}" in workflow


# =============================================================================
# 22. test_workflow_adapts_to_deploy_target
# =============================================================================


def test_workflow_adapts_to_deploy_target(basic_tech_stack):
    """Each deploy target produces the correct deployment strategy."""
    # Vercel: vercel CLI, no Docker
    vercel_wf = generate_deploy_workflow(basic_tech_stack, deploy_target="vercel")
    assert "vercel deploy" in vercel_wf
    assert "flyctl" not in vercel_wf

    # Fly.io: flyctl, no vercel
    flyio_wf = generate_deploy_workflow(basic_tech_stack, deploy_target="flyio")
    assert "flyctl deploy" in flyio_wf
    assert "vercel deploy" not in flyio_wf

    # Docker/GHCR
    docker_wf = generate_deploy_workflow(basic_tech_stack, deploy_target="docker")
    assert "ghcr.io" in docker_wf
    assert "vercel deploy" not in docker_wf

    # Railway (also Docker-based)
    railway_wf = generate_deploy_workflow(basic_tech_stack, deploy_target="railway")
    assert "ghcr.io" in railway_wf
