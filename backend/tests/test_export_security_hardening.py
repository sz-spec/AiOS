"""
Export Security Hardening Tests — SEC-EXP-02
=============================================
Dedicated security audit for the hardened export subsystem.

Verifies:
  A. OIDC & Least Privilege — id-token: write at job level, contents: read retained
  B. Transitive Dependency Warnings — TRANSITIVE-RISK comments on risky actions
  C. Environment Scrubbing Integrity — exact unset count, if: always() placement
  D. Documentation Accuracy — OIDC section in SECRETS_SETUP.md

Covers all four deploy targets (vercel, docker, railway, flyio) to ensure
security properties hold regardless of deployment platform.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.export_service import (
    PINNED_ACTIONS,
    TRANSITIVE_RISK_ACTIONS,
    build_secrets_manifest,
    generate_deploy_workflow,
    generate_secrets_setup_md,
    generate_standard_files,
    resolve_action,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def clerk_pg_stack():
    """Clerk auth + PostgreSQL — produces 2 secrets + 2 variables."""
    return {
        "frontend": "Next.js 14 + Tailwind CSS",
        "backend": "FastAPI",
        "database": "PostgreSQL",
        "node_version": "20",
    }


@pytest.fixture
def clerk_pg_manifest(clerk_pg_stack):
    """Secrets manifest for clerk + PostgreSQL stack."""
    from services.export_service import generate_env_example

    env = generate_env_example(clerk_pg_stack, auth_strategy="clerk")
    return build_secrets_manifest(env)


@pytest.fixture
def three_secret_manifest():
    """
    Simulated .env.example with exactly 3 distinct secrets:
    CLERK_SECRET_KEY, DATABASE_URL, STRIPE_SECRET.
    No NEXT_PUBLIC_* variables — isolates the secret-counting test.
    """
    env_content = (
        'CLERK_SECRET_KEY="sk_test_..."\n'
        'DATABASE_URL="postgresql://..."\n'
        'STRIPE_SECRET="sk_stripe_..."\n'
    )
    return build_secrets_manifest(env_content)


@pytest.fixture
def three_secret_workflow(clerk_pg_stack, three_secret_manifest):
    """Workflow generated with exactly 3 secrets, deploy target docker."""
    return generate_deploy_workflow(
        clerk_pg_stack,
        deploy_target="docker",
        secrets_manifest=three_secret_manifest,
    )


@pytest.fixture(params=["vercel", "docker", "railway", "flyio"])
def workflow_for_target(request, clerk_pg_stack, clerk_pg_manifest):
    """Parametrized: generates workflow for every deploy target."""
    target = request.param
    raw = generate_deploy_workflow(
        clerk_pg_stack,
        deploy_target=target,
        secrets_manifest=clerk_pg_manifest,
    )
    return {"target": target, "raw": raw, "yaml": yaml.safe_load(raw)}


# =============================================================================
# A. OIDC & Least Privilege
# =============================================================================


class TestOIDCLeastPrivilege:
    """
    Verify that OIDC permission (id-token: write) is present at the deploy
    JOB level across all deploy targets, and that contents: read is retained.
    """

    def test_deploy_job_has_oidc_for_all_targets(self, workflow_for_target):
        """id-token: write present in deploy job permissions for every target."""
        wf = workflow_for_target
        deploy_perms = wf["yaml"]["jobs"]["deploy"].get("permissions", {})
        assert deploy_perms.get("id-token") == "write", (
            f"[{wf['target']}] Missing id-token: write in deploy job. "
            f"Got: {deploy_perms}"
        )

    def test_deploy_job_retains_contents_read_for_all_targets(
        self, workflow_for_target
    ):
        """contents: read still present in deploy job for every target."""
        wf = workflow_for_target
        deploy_perms = wf["yaml"]["jobs"]["deploy"].get("permissions", {})
        assert deploy_perms.get("contents") == "read", (
            f"[{wf['target']}] Missing contents: read in deploy job. "
            f"Got: {deploy_perms}"
        )

    def test_top_level_permissions_unchanged(self, workflow_for_target):
        """Top-level permissions block still has contents: read (global minimum)."""
        wf = workflow_for_target
        top_perms = wf["yaml"].get("permissions", {})
        assert (
            top_perms.get("contents") == "read"
        ), f"[{wf['target']}] Top-level permissions missing contents: read"

    def test_oidc_is_job_level_not_top_level(self, workflow_for_target):
        """id-token: write must NOT be at top-level (least-privilege)."""
        wf = workflow_for_target
        top_perms = wf["yaml"].get("permissions", {})
        assert (
            "id-token" not in top_perms
        ), f"[{wf['target']}] id-token should be job-level, not top-level"

    def test_docker_targets_also_have_packages_write(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """Docker-based targets (docker, railway, flyio) need packages: write."""
        for target in ("docker", "railway", "flyio"):
            raw = generate_deploy_workflow(
                clerk_pg_stack, deploy_target=target, secrets_manifest=clerk_pg_manifest
            )
            parsed = yaml.safe_load(raw)
            deploy_perms = parsed["jobs"]["deploy"].get("permissions", {})
            assert (
                deploy_perms.get("packages") == "write"
            ), f"[{target}] Missing packages: write for container-based deploy"

    def test_vercel_target_no_packages_write(self, clerk_pg_stack, clerk_pg_manifest):
        """Vercel target should NOT have packages: write (no container push)."""
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="vercel", secrets_manifest=clerk_pg_manifest
        )
        parsed = yaml.safe_load(raw)
        deploy_perms = parsed["jobs"]["deploy"].get("permissions", {})
        assert (
            "packages" not in deploy_perms
        ), "Vercel deploy should not request packages: write"


# =============================================================================
# B. Transitive Dependency Warnings (SEC-EXP-02)
# =============================================================================


class TestTransitiveDependencyWarnings:
    """
    Verify that actions with known transitive risks emit warning comments
    in the generated YAML, while remaining SHA-pinned.
    """

    SHA_PATTERN = re.compile(r"@([0-9a-f]{40})")

    def test_docker_workflow_has_transitive_risk_comment(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """Docker target uses build-push-action → TRANSITIVE-RISK comment present."""
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="docker", secrets_manifest=clerk_pg_manifest
        )
        assert (
            "TRANSITIVE-RISK" in raw
        ), "Docker workflow should contain TRANSITIVE-RISK warning comment"

    def test_transitive_risk_comment_on_correct_action(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """TRANSITIVE-RISK comment appears on the line with docker/build-push-action."""
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="docker", secrets_manifest=clerk_pg_manifest
        )
        for line in raw.splitlines():
            if "docker/build-push-action" in line:
                assert (
                    "TRANSITIVE-RISK" in line
                ), f"docker/build-push-action line missing TRANSITIVE-RISK: {line}"

    def test_transitive_risk_action_still_sha_pinned(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """Even with TRANSITIVE-RISK comment, action is still 40-char SHA-pinned."""
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="docker", secrets_manifest=clerk_pg_manifest
        )
        for line in raw.splitlines():
            if "TRANSITIVE-RISK" in line:
                assert self.SHA_PATTERN.search(
                    line
                ), f"Transitive-risk action not SHA-pinned: {line.strip()}"

    def test_vercel_workflow_no_transitive_risk(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """Vercel target doesn't use Docker actions → no TRANSITIVE-RISK comments."""
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="vercel", secrets_manifest=clerk_pg_manifest
        )
        # Vercel doesn't use any Docker actions, so no risk warnings
        # (the checkout and setup-node actions are not in the risk registry)
        for line in raw.splitlines():
            if "uses:" in line:
                if "docker/" not in line:
                    assert (
                        "TRANSITIVE-RISK" not in line
                    ), f"Non-Docker action has TRANSITIVE-RISK: {line.strip()}"

    def test_resolve_action_appends_risk_for_known_actions(self):
        """resolve_action() appends risk comment for actions in risk registry."""
        for action_name, risk_msg in TRANSITIVE_RISK_ACTIONS.items():
            ref = resolve_action(action_name)
            assert (
                "TRANSITIVE-RISK" in ref
            ), f"resolve_action('{action_name}') missing TRANSITIVE-RISK"
            assert (
                risk_msg in ref
            ), f"Risk message not in ref: expected '{risk_msg}' in '{ref}'"

    def test_resolve_action_no_risk_for_safe_actions(self):
        """resolve_action() does NOT append risk for safe actions."""
        safe_actions = [a for a in PINNED_ACTIONS if a not in TRANSITIVE_RISK_ACTIONS]
        assert len(safe_actions) >= 1, "Need at least one safe action for test"
        for action_name in safe_actions:
            ref = resolve_action(action_name)
            assert (
                "TRANSITIVE-RISK" not in ref
            ), f"Safe action '{action_name}' incorrectly flagged: {ref}"

    def test_all_transitive_risk_actions_in_pinned_registry(self):
        """Every action in TRANSITIVE_RISK_ACTIONS must exist in PINNED_ACTIONS."""
        for action_name in TRANSITIVE_RISK_ACTIONS:
            assert (
                action_name in PINNED_ACTIONS
            ), f"'{action_name}' in TRANSITIVE_RISK_ACTIONS but not PINNED_ACTIONS"

    def test_risk_registry_has_descriptions(self):
        """Every entry in TRANSITIVE_RISK_ACTIONS has a non-empty description."""
        for action_name, desc in TRANSITIVE_RISK_ACTIONS.items():
            assert isinstance(desc, str), f"'{action_name}' risk is not a string"
            assert (
                len(desc) >= 10
            ), f"'{action_name}' risk description too short: '{desc}'"


# =============================================================================
# C. Environment Scrubbing Integrity
# =============================================================================


class TestEnvironmentScrubbing:
    """
    Verify the Post-Deployment Cleanup step correctly unsets ALL injected
    secrets and variables, runs on failure, and has correct structure.
    """

    def test_scrub_step_has_exactly_3_unset_for_3_secrets(self, three_secret_workflow):
        """With 3 secrets and 0 variables, cleanup has exactly 3 unset commands."""
        unset_count = three_secret_workflow.count("unset ")
        assert (
            unset_count == 3
        ), f"Expected 3 unset commands for 3 secrets, got {unset_count}"

    def test_scrub_step_unsets_correct_keys(self, three_secret_workflow):
        """Each of the 3 secret keys has a corresponding unset command."""
        assert "unset CLERK_SECRET_KEY" in three_secret_workflow
        assert "unset DATABASE_URL" in three_secret_workflow
        assert "unset STRIPE_SECRET" in three_secret_workflow

    def test_scrub_step_if_always_present(self, three_secret_workflow):
        """Cleanup step has 'if: always()' so it runs even on deploy failure."""
        assert "if: always()" in three_secret_workflow

    def test_scrub_step_if_always_before_run(self, three_secret_workflow):
        """'if: always()' appears before 'run: |' within the cleanup step."""
        lines = three_secret_workflow.splitlines()
        if_line = None
        run_line = None
        in_cleanup = False
        for i, line in enumerate(lines):
            if "Post-Deployment Cleanup" in line:
                in_cleanup = True
            if in_cleanup:
                if "if: always()" in line and if_line is None:
                    if_line = i
                if "run: |" in line and run_line is None:
                    run_line = i
        assert if_line is not None, "if: always() not found in cleanup step"
        assert run_line is not None, "run: | not found in cleanup step"
        assert (
            if_line < run_line
        ), f"if: always() (line {if_line}) must come before run: | (line {run_line})"

    def test_scrub_step_echoes_confirmation(self, three_secret_workflow):
        """Cleanup step ends with echo confirmation."""
        assert "Environment scrubbed." in three_secret_workflow

    def test_scrub_step_name_is_descriptive(self, three_secret_workflow):
        """Cleanup step has descriptive name 'Post-Deployment Cleanup'."""
        assert "Post-Deployment Cleanup" in three_secret_workflow

    def test_scrub_step_present_for_all_targets(self, workflow_for_target):
        """Post-Deployment Cleanup step is present regardless of deploy target."""
        assert (
            "Post-Deployment Cleanup" in workflow_for_target["raw"]
        ), f"[{workflow_for_target['target']}] Missing cleanup step"

    def test_scrub_step_if_always_for_all_targets(self, workflow_for_target):
        """if: always() is present in cleanup step for every deploy target."""
        assert (
            "if: always()" in workflow_for_target["raw"]
        ), f"[{workflow_for_target['target']}] Missing if: always()"

    def test_scrub_unsets_both_secrets_and_variables(
        self, clerk_pg_stack, clerk_pg_manifest
    ):
        """
        With clerk+PG manifest (2 secrets + 2 variables),
        cleanup unsets all 4 keys.
        """
        raw = generate_deploy_workflow(
            clerk_pg_stack, deploy_target="docker", secrets_manifest=clerk_pg_manifest
        )
        assert "unset CLERK_SECRET_KEY" in raw
        assert "unset DATABASE_URL" in raw
        assert "unset NEXT_PUBLIC_API_URL" in raw
        assert "unset NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in raw
        # Total: 4 unset commands
        assert (
            raw.count("unset ") == 4
        ), f"Expected 4 unset commands, got {raw.count('unset ')}"

    def test_empty_manifest_still_has_scrub_step(self, clerk_pg_stack):
        """Even with no secrets/variables, the cleanup step exists with echo."""
        raw = generate_deploy_workflow(
            clerk_pg_stack,
            deploy_target="docker",
            secrets_manifest={"secrets": {}, "variables": {}},
        )
        assert "Post-Deployment Cleanup" in raw
        assert "if: always()" in raw
        assert "Environment scrubbed." in raw


# =============================================================================
# D. Documentation Accuracy
# =============================================================================


class TestDocumentationAccuracy:
    """
    Verify SECRETS_SETUP.md contains accurate OIDC migration documentation
    with correct threat model and benefit explanation.
    """

    @pytest.fixture
    def secrets_md(self, clerk_pg_manifest):
        return generate_secrets_setup_md(clerk_pg_manifest)

    def test_has_oidc_section_header(self, secrets_md):
        """OIDC Authentication section header present."""
        assert "## OIDC Authentication" in secrets_md

    def test_has_why_oidc_subsection(self, secrets_md):
        """'Why OIDC?' subsection explains the threat model."""
        assert "### Why OIDC?" in secrets_md

    def test_explains_rotation_burden(self, secrets_md):
        """Mentions rotation burden as a risk of static secrets."""
        assert "Rotation burden" in secrets_md

    def test_explains_blast_radius(self, secrets_md):
        """Mentions blast radius as a risk of static secrets."""
        assert "Blast radius" in secrets_md

    def test_explains_audit_trail(self, secrets_md):
        """Mentions lack of audit trail as a risk of static secrets."""
        assert "audit trail" in secrets_md.lower()

    def test_explains_short_lived_tokens(self, secrets_md):
        """Mentions that OIDC tokens are short-lived."""
        assert "short-lived" in secrets_md

    def test_explains_scoped_to_workflow(self, secrets_md):
        """Mentions that OIDC tokens are scoped to a single workflow run."""
        assert "scoped to a single workflow run" in secrets_md

    def test_references_id_token_permission(self, secrets_md):
        """References the actual permission string 'id-token: write'."""
        assert "id-token: write" in secrets_md

    def test_has_setup_steps(self, secrets_md):
        """OIDC section includes setup instructions."""
        assert "### Setup" in secrets_md
        assert "Configure your deployment platform" in secrets_md

    def test_mentions_fallback_for_unsupported_platforms(self, secrets_md):
        """Documents the fallback to static secrets for unsupported platforms."""
        assert "falls back" in secrets_md.lower()
        assert "DEPLOY_TOKEN" in secrets_md

    def test_full_standard_files_include_oidc_in_secrets_md(self, clerk_pg_stack):
        """generate_standard_files() produces SECRETS_SETUP.md with OIDC section."""
        files = generate_standard_files(
            project_name="audit-app",
            tech_stack=clerk_pg_stack,
            auth_strategy="clerk",
            deploy_target="docker",
        )
        md = files["SECRETS_SETUP.md"]
        assert "OIDC Authentication" in md
        assert "Why OIDC?" in md


# =============================================================================
# E. Cross-Cutting: Security Properties Across Deploy Targets
# =============================================================================


class TestCrossCuttingSecurity:
    """
    End-to-end security invariant checks that must hold for every deploy
    target and every auth strategy.
    """

    @pytest.fixture(params=["clerk", "nextauth", "custom_jwt", None])
    def files_for_auth_strategy(self, request):
        """Parametrized: generate full standard files for each auth strategy."""
        stack = {
            "frontend": "Next.js + Tailwind",
            "database": "PostgreSQL",
            "node_version": "20",
        }
        return generate_standard_files(
            project_name="sec-test",
            tech_stack=stack,
            auth_strategy=request.param,
            deploy_target="docker",
        )

    def test_workflow_always_has_scrub_step(self, files_for_auth_strategy):
        """Every auth strategy produces a workflow with cleanup step."""
        raw = files_for_auth_strategy[".github/workflows/deploy.yml"]
        assert "Post-Deployment Cleanup" in raw
        assert "if: always()" in raw

    def test_workflow_always_has_oidc(self, files_for_auth_strategy):
        """Every auth strategy produces a workflow with OIDC permission."""
        raw = files_for_auth_strategy[".github/workflows/deploy.yml"]
        parsed = yaml.safe_load(raw)
        deploy_perms = parsed["jobs"]["deploy"].get("permissions", {})
        assert deploy_perms.get("id-token") == "write"

    def test_secrets_md_always_has_oidc_docs(self, files_for_auth_strategy):
        """OIDC section present in SECRETS_SETUP.md for every auth strategy."""
        md = files_for_auth_strategy["SECRETS_SETUP.md"]
        assert "OIDC Authentication" in md
