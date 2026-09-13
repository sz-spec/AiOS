"""
E2E Export Flow — Zero Lock-in & CI/CD Verification
=====================================================
Simulates a full project finalization through MultiAgentBuilder._finalize_node
and performs three audits:

1. ACID TEST — Zero Lock-in: No exported file references "vbuilder" anywhere.
2. CI/CD AUDIT — SEC-EXP-01: Every `uses:` is SHA-pinned, top-level permissions enforced.
3. ENVIRONMENT CHECK — SECRETS_SETUP.md tables list all detected secrets/variables.
"""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.agents.multi_agent import MultiAgentBuilder
from services.export_service import PINNED_ACTIONS

# =============================================================================
# Fixtures
# =============================================================================


def _simulate_finalize(
    *,
    project_name: str = "e2e-test-app",
    tech_stack: dict | None = None,
    auth_strategy: str | None = "clerk",
    deploy_target: str = "docker",
    frontend_code: dict | None = None,
    backend_code: dict | None = None,
) -> dict:
    """
    Drive MultiAgentBuilder._finalize_node with a realistic ProjectState
    and return the final_project dict.
    """
    builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

    stack = tech_stack or {
        "frontend": "Next.js 14 + Tailwind CSS",
        "backend": "FastAPI",
        "database": "PostgreSQL",
        "node_version": "20",
    }

    state = {
        "messages": [],
        "requirements": f"Build {project_name} with auth and database",
        "architecture": {
            "project_name": project_name,
            "tech_stack": stack,
            "auth_strategy": auth_strategy,
            "deploy_target": deploy_target,
        },
        "frontend_code": frontend_code
        or {
            "src/app/page.tsx": "export default function Home() { return <h1>Home</h1>; }",
            "src/app/layout.tsx": "export default function Layout({ children }) { return <html><body>{children}</body></html>; }",
            "src/components/Header.tsx": "export function Header() { return <header>Header</header>; }",
            "src/lib/api.ts": "export async function fetchData(url: string) { return fetch(url).then(r => r.json()); }",
        },
        "backend_code": backend_code
        or {
            "api/main.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\ndef health(): return {"ok": True}',
            "api/models.py": "from sqlalchemy import Column, Integer, String\nclass User: pass",
        },
        "tests": {
            "tests/test_health.py": "def test_health(): assert True",
        },
        "review_results": {"score": 85, "issues": []},
        "iteration": 1,
        "current_phase": "finalize",
    }

    result = builder._finalize_node.__wrapped__(builder, state)
    return result["final_project"]


@pytest.fixture(scope="module")
def finalized_project():
    """Module-scoped fixture: run finalize once, share across all tests."""
    return _simulate_finalize()


@pytest.fixture(scope="module")
def project_files(finalized_project):
    """The file dict from finalized_project."""
    return finalized_project["files"]


@pytest.fixture(scope="module")
def workflow_yaml(project_files):
    """Parsed YAML of the generated deploy workflow."""
    raw = project_files[".github/workflows/deploy.yml"]
    return yaml.safe_load(raw)


# =============================================================================
# ACID TEST — Zero Lock-in
# =============================================================================


class TestZeroLockin:
    """Ensure no exported file references VBuilder internals."""

    def test_no_vbuilder_string_in_any_file(self, project_files):
        """
        Acid Test (a): scan ALL exported files for the string "vbuilder"
        (case-insensitive). Zero matches allowed.
        """
        violations = []
        for fpath, content in project_files.items():
            for line_no, line in enumerate(content.splitlines(), 1):
                if "vbuilder" in line.lower():
                    violations.append(f"  {fpath}:{line_no}: {line.strip()}")

        assert (
            violations == []
        ), f"Zero Lock-in VIOLATED — {len(violations)} match(es):\n" + "\n".join(
            violations
        )

    def test_package_json_no_vbuilder_deps(self, project_files):
        """
        Acid Test (b): package.json contains no dependencies starting
        with '@vbuilder/'.
        """
        pkg = json.loads(project_files["package.json"])
        all_deps = list(pkg.get("dependencies", {})) + list(
            pkg.get("devDependencies", {})
        )
        vbuilder_deps = [d for d in all_deps if d.startswith("@vbuilder/")]
        assert vbuilder_deps == [], f"Lock-in deps found: {vbuilder_deps}"

    def test_no_vbuilder_imports_in_code(self, project_files):
        """
        Extended: no import/require referencing 'vbuilder' in any file.
        """
        import_pattern = re.compile(
            r"""(?:from\s+['"]|import\s+['"]|require\s*\(\s*['"]).*vbuilder""",
            re.IGNORECASE,
        )
        violations = []
        for fpath, content in project_files.items():
            for line_no, line in enumerate(content.splitlines(), 1):
                if import_pattern.search(line):
                    violations.append(f"  {fpath}:{line_no}: {line.strip()}")

        assert violations == [], "VBuilder import detected:\n" + "\n".join(violations)


# =============================================================================
# CI/CD AUDIT — SEC-EXP-01
# =============================================================================


class TestCICDAudit:
    """Verify the generated workflow meets SEC-EXP-01 requirements."""

    def test_workflow_is_valid_yaml(self, project_files):
        """
        CI/CD Audit pre-check: the workflow file parses as valid YAML
        with expected top-level keys.
        """
        raw = project_files[".github/workflows/deploy.yml"]
        doc = yaml.safe_load(raw)
        assert isinstance(doc, dict)
        # YAML parses bare `on:` as boolean True (not the string "on").
        # GitHub Actions accepts both forms — check for either.
        assert "on" in doc or True in doc, "Missing 'on' trigger"
        assert "jobs" in doc, "Missing 'jobs' key"
        assert "permissions" in doc, "Missing top-level 'permissions'"

    def test_top_level_permissions_contents_read(self, workflow_yaml):
        """
        CI/CD Audit (c): top-level permissions includes 'contents: read'.
        """
        perms = workflow_yaml.get("permissions", {})
        assert (
            perms.get("contents") == "read"
        ), f"Expected permissions.contents='read', got {perms}"

    def test_every_uses_is_sha_pinned(self, project_files):
        """
        CI/CD Audit (b): every 'uses:' reference in the workflow contains
        a 40-character hexadecimal commit SHA after the '@'.
        """
        raw = project_files[".github/workflows/deploy.yml"]
        sha_pattern = re.compile(r"@([0-9a-f]{40})")
        tag_pattern = re.compile(r"@v[\d.]+")

        uses_re = re.compile(r"uses:\s*(.+)")
        violations = []

        for line_no, line in enumerate(raw.splitlines(), 1):
            m = uses_re.search(line)
            if not m:
                continue
            ref = m.group(1).strip()

            # Must have a 40-char SHA
            if not sha_pattern.search(ref):
                violations.append(f"  line {line_no}: missing SHA — {ref}")
            # Must NOT have a bare tag as the version selector (only in comment)
            at_idx = ref.find("@")
            if at_idx != -1:
                version_part = ref[at_idx + 1 :].split()[0]  # before comment
                if tag_pattern.fullmatch(f"@{version_part}"):
                    violations.append(
                        f"  line {line_no}: mutable tag instead of SHA — {ref}"
                    )

        assert (
            violations == []
        ), "SEC-EXP-01 VIOLATED — unpinned actions:\n" + "\n".join(violations)

    def test_all_pinned_actions_have_comment_tag(self, project_files):
        """
        Every SHA-pinned action should have a trailing '# vX.Y.Z' comment
        for human readability.
        """
        raw = project_files[".github/workflows/deploy.yml"]
        uses_re = re.compile(r"uses:\s*(.+)")
        comment_pattern = re.compile(r"#\s*v[\d.]+")

        missing_comments = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            m = uses_re.search(line)
            if not m:
                continue
            ref = m.group(1).strip()
            if not comment_pattern.search(ref):
                missing_comments.append(f"  line {line_no}: no tag comment — {ref}")

        assert missing_comments == [], "Actions missing tag comment:\n" + "\n".join(
            missing_comments
        )

    def test_shas_match_pinned_actions_registry(self, project_files):
        """
        Every SHA in the workflow must match a SHA in PINNED_ACTIONS,
        ensuring the workflow was generated from the authoritative registry.
        """
        raw = project_files[".github/workflows/deploy.yml"]
        uses_re = re.compile(r"uses:\s*(.+)")
        known_shas = {entry["sha"] for entry in PINNED_ACTIONS.values()}

        for line_no, line in enumerate(raw.splitlines(), 1):
            m = uses_re.search(line)
            if not m:
                continue
            ref = m.group(1).strip()
            sha_match = re.search(r"@([0-9a-f]{40})", ref)
            if sha_match:
                sha = sha_match.group(1)
                assert (
                    sha in known_shas
                ), f"line {line_no}: SHA {sha[:12]}... not in PINNED_ACTIONS registry"

    def test_deploy_job_requires_test(self, workflow_yaml):
        """Deploy job depends on test job passing."""
        deploy = workflow_yaml["jobs"].get("deploy", {})
        needs = deploy.get("needs")
        if isinstance(needs, list):
            assert "test" in needs
        else:
            assert needs == "test"

    def test_deploy_only_on_main_push(self, workflow_yaml):
        """Deploy job only runs on push to main (not PRs)."""
        deploy = workflow_yaml["jobs"].get("deploy", {})
        condition = deploy.get("if", "")
        assert "refs/heads/main" in condition
        assert "push" in condition


# =============================================================================
# ENVIRONMENT CHECK — Secrets & Variables
# =============================================================================


class TestEnvironmentCheck:
    """Verify SECRETS_SETUP.md and env variable classification."""

    def test_secrets_setup_md_exists(self, project_files):
        """SECRETS_SETUP.md is present in the exported files."""
        assert "SECRETS_SETUP.md" in project_files

    def test_secrets_setup_has_tables(self, project_files):
        """SECRETS_SETUP.md contains both Secrets and Variables sections."""
        md = project_files["SECRETS_SETUP.md"]
        assert "## Secrets" in md, "Missing Secrets section"
        assert "## Variables" in md, "Missing Variables section"

    def test_secrets_setup_lists_detected_secrets(self, project_files):
        """
        Environment Check (a): SECRETS_SETUP.md tables list all secrets
        derived from the build context (.env.example with clerk auth + DB).
        """
        md = project_files["SECRETS_SETUP.md"]

        # With clerk auth + PostgreSQL, .env.example produces these keys:
        assert "DATABASE_URL" in md, "DATABASE_URL not in secrets table"
        assert "CLERK_SECRET_KEY" in md, "CLERK_SECRET_KEY not in secrets table"

    def test_secrets_setup_lists_public_variables(self, project_files):
        """NEXT_PUBLIC_* vars appear in the Variables table, not Secrets."""
        md = project_files["SECRETS_SETUP.md"]
        assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in md
        assert "NEXT_PUBLIC_API_URL" in md

    def test_env_example_exists_and_complete(self, project_files):
        """.env.example is generated and has required variables."""
        assert ".env.example" in project_files
        env = project_files[".env.example"]
        assert "DATABASE_URL" in env
        assert "NEXT_PUBLIC_API_URL" in env


# =============================================================================
# STRUCTURAL INTEGRITY
# =============================================================================


class TestStructuralIntegrity:
    """Verify the finalized project has correct structure."""

    def test_agent_generated_files_preserved(self, project_files):
        """Agent-generated source files survive the finalize step."""
        assert "src/app/page.tsx" in project_files
        assert "src/app/layout.tsx" in project_files
        assert "api/main.py" in project_files
        assert "tests/test_health.py" in project_files

    def test_agent_files_take_priority(self):
        """
        If an agent already generated a file (e.g., package.json),
        the standard file does NOT overwrite it.
        """
        custom_pkg = '{"name": "agent-generated", "version": "9.9.9"}'
        project = _simulate_finalize(
            frontend_code={"package.json": custom_pkg},
        )
        files = project["files"]
        pkg = json.loads(files["package.json"])
        assert (
            pkg["name"] == "agent-generated"
        ), "Standard file overwrote agent-generated package.json"
        assert pkg["version"] == "9.9.9"

    def test_summary_includes_all_file_paths(self, finalized_project):
        """Project summary.files lists every file path."""
        summary_files = set(finalized_project["summary"]["files"])
        actual_files = set(finalized_project["files"].keys())
        assert summary_files == actual_files

    def test_dockerfile_uses_correct_node_version(self, project_files):
        """Dockerfile FROM uses the node version from tech_stack."""
        dockerfile = project_files["Dockerfile"]
        assert "node:20" in dockerfile

    def test_docker_compose_has_db_service(self, project_files):
        """docker-compose.yml includes PostgreSQL service from tech_stack."""
        compose = project_files["docker-compose.yml"]
        assert "postgres" in compose.lower()
        assert "5432" in compose


# =============================================================================
# DEPLOY TARGET VARIANT
# =============================================================================


class TestDeployTargetVariant:
    """Verify that different deploy targets produce correct workflows."""

    def test_vercel_target_no_docker(self):
        """Vercel deploy target does not contain Docker steps."""
        project = _simulate_finalize(deploy_target="vercel")
        wf = project["files"][".github/workflows/deploy.yml"]
        assert "vercel deploy" in wf
        # Should not have docker build-push steps
        assert "build-push-action" not in wf.split("#")[0]  # ignore comments

    def test_flyio_target_uses_flyctl(self):
        """Fly.io deploy target uses flyctl."""
        project = _simulate_finalize(deploy_target="flyio")
        wf = project["files"][".github/workflows/deploy.yml"]
        assert "flyctl deploy" in wf
