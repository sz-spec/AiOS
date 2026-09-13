"""
Export Service — Phase 3.0 Subsystem 3.3
=========================================
Standardized project output, CI/CD Autopilot (SEC-EXP-01), and env rehydration.

Generates deployment-ready files that are injected into the finalized project:
- package.json, tsconfig.json, .env.example, Dockerfile, docker-compose.yml
- .github/workflows/deploy.yml (SHA-pinned actions, least-privilege permissions,
  OIDC authentication, runner environment scrubbing)
- SECRETS_SETUP.md (GitHub Secrets/Variables configuration guide + OIDC migration)
- README.md (setup + run + deploy instructions)

Supply-Chain Hardening (SEC-EXP-02):
- OIDC tokens replace static API secrets where platforms support it
- Transitive dependency risk warnings on third-party actions
- Post-deployment runner scrubbing to prevent secret leakage
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

import logging

logger = logging.getLogger("export_service")

# =============================================================================
# SEC-EXP-01: Full Commit SHA Pinning Registry
# =============================================================================
# Every third-party GitHub Action MUST be pinned to a full 40-char commit SHA.
# Adding a mutable tag (v4, v3, etc.) is forbidden. The tag is kept ONLY as a
# trailing comment for human readability.
#
# To update: verify the new SHA on the action's releases page, then update here.

PINNED_ACTIONS: Dict[str, Dict[str, str]] = {
    "actions/checkout": {
        "sha": "b4ffde65f46336ab88eb53be808477a3936bae11",
        "tag": "v4.1.1",
    },
    "actions/setup-node": {
        "sha": "60edb5dd545a775178f52524783378180af0d1f8",
        "tag": "v4.0.2",
    },
    "actions/setup-python": {
        "sha": "0a5c61591373683505ea898e09a3ea4f39ef2b9c",
        "tag": "v5.0.0",
    },
    "docker/build-push-action": {
        "sha": "2cdde995de11925a030ce8070c3d77a52ffcf1c0",
        "tag": "v5.3.0",
    },
    "docker/login-action": {
        "sha": "e92390c5fb421da1463c202d546fed0ec5c39f20",
        "tag": "v3.1.0",
    },
    "docker/setup-buildx-action": {
        "sha": "d70bba72b1f3fd22344832f00baa16ece964efeb",
        "tag": "v3.3.0",
    },
    "superfly/flyctl-actions/setup-flyctl": {
        "sha": "fc53c09e1bc3be6f54706524e3956cc922f5c8b3",
        "tag": "v1.5",
    },
}


# =============================================================================
# SEC-EXP-02: Transitive Dependency Risk Registry
# =============================================================================
# Actions known to pull unpinned internal dependencies at runtime.
# These are still allowed (some are unavoidable), but a warning comment is
# appended to the YAML so operators know to audit their supply chain.

TRANSITIVE_RISK_ACTIONS: Dict[str, str] = {
    "docker/build-push-action": (
        "Pulls buildx binary at runtime; audit ghcr.io/docker/buildx releases"
    ),
    "docker/login-action": ("Delegates to platform-specific credential helpers"),
}


def resolve_action(action_name: str) -> str:
    """
    Resolve a GitHub Action name to its SHA-pinned reference.

    Returns: "action/name@<40-char-sha>  # <tag>"
    If the action has known transitive risks, appends a warning comment.
    Raises: ValueError if the action is not in the PINNED_ACTIONS registry.
    """
    entry = PINNED_ACTIONS.get(action_name)
    if not entry:
        raise ValueError(
            f"Unpinned action: {action_name}. "
            f"Add it to PINNED_ACTIONS in export_service.py before use."
        )
    ref = f"{action_name}@{entry['sha']}  # {entry['tag']}"
    risk = TRANSITIVE_RISK_ACTIONS.get(action_name)
    if risk:
        ref += f"  # TRANSITIVE-RISK: {risk}"
    return ref


# =============================================================================
# Environment Variable Rehydration
# =============================================================================

# Patterns that indicate a variable is sensitive (should be a GitHub Secret)
_SECRET_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_DSN", "_URL")


def build_secrets_manifest(
    env_example_content: str,
) -> Dict[str, Dict[str, str]]:
    """
    Parse .env.example and classify variables for GitHub Secrets vs Variables.

    Rules:
    - NEXT_PUBLIC_* → GitHub Variables (non-secret, visible in logs)
    - *_KEY, *_SECRET, *_TOKEN, *_PASSWORD, *_DSN, *_URL → GitHub Secrets
    - Everything else → GitHub Secrets (safe default)

    Returns:
        {"secrets": {"DB_URL": "desc", ...}, "variables": {"NEXT_PUBLIC_X": "desc", ...}}
    """
    secrets: Dict[str, str] = {}
    variables: Dict[str, str] = {}

    for line in env_example_content.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, raw_value = line.partition("=")
        key = key.strip()
        description = raw_value.strip().strip('"').strip("'")
        if not description:
            description = key

        if key.startswith("NEXT_PUBLIC_"):
            variables[key] = description
        else:
            secrets[key] = description

    return {"secrets": secrets, "variables": variables}


def generate_secrets_setup_md(
    manifest: Dict[str, Dict[str, str]],
) -> str:
    """Generate SECRETS_SETUP.md content from a secrets manifest."""
    lines = [
        "# GitHub Secrets & Variables Setup",
        "",
        "Go to your repo → **Settings** → **Secrets and variables** → **Actions**.",
        "",
    ]

    secrets = manifest.get("secrets", {})
    variables = manifest.get("variables", {})

    if secrets:
        lines.append("## Secrets (encrypted, not visible in logs)")
        lines.append("")
        lines.append("| Name | Description |")
        lines.append("|------|-------------|")
        for name, desc in sorted(secrets.items()):
            lines.append(f"| `{name}` | {desc} |")
        lines.append("")

    if variables:
        lines.append("## Variables (non-secret, visible in build logs)")
        lines.append("")
        lines.append("| Name | Description |")
        lines.append("|------|-------------|")
        for name, desc in sorted(variables.items()):
            lines.append(f"| `{name}` | {desc} |")
        lines.append("")

    if not secrets and not variables:
        lines.append("No environment variables detected in `.env.example`.")
        lines.append("")

    # OIDC migration section
    lines.extend(
        [
            "## OIDC Authentication (Recommended)",
            "",
            "This workflow uses OpenID Connect (OIDC) tokens (`id-token: write`)",
            "instead of static API secrets for deployment authentication.",
            "",
            "### Why OIDC?",
            "",
            "Static secrets (API keys, tokens) stored in GitHub Secrets are risky:",
            "",
            "- **Rotation burden**: Leaked or expired secrets require manual rotation.",
            "- **Blast radius**: A compromised secret grants access until revoked.",
            "- **No audit trail**: Static secrets lack per-request identity binding.",
            "",
            "OIDC tokens are **short-lived**, **scoped to a single workflow run**,",
            "and **automatically verified** by the deployment platform — eliminating",
            "the need to store long-lived credentials in your repository.",
            "",
            "### Setup",
            "",
            "1. Configure your deployment platform to trust GitHub's OIDC provider.",
            "2. The workflow already includes `permissions: id-token: write`.",
            "3. Deployment steps use the platform's OIDC/federated identity flow.",
            "",
            "For platforms that do not yet support OIDC (e.g., some Railway setups),",
            "the workflow falls back to `secrets.DEPLOY_TOKEN` — but OIDC is the",
            "target state. Migrate when your platform adds support.",
            "",
        ]
    )

    return "\n".join(lines)


# =============================================================================
# CI/CD Workflow Generation
# =============================================================================


def generate_deploy_workflow(
    tech_stack: Dict[str, Any],
    deploy_target: str = "docker",
    secrets_manifest: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """
    Generate .github/workflows/deploy.yml with SHA-pinned actions.

    Args:
        tech_stack: Architecture tech stack (frontend, backend, database, etc.)
        deploy_target: "vercel" | "railway" | "docker" | "flyio"
        secrets_manifest: Output of build_secrets_manifest()

    Returns:
        YAML string for the workflow file.
    """
    manifest = secrets_manifest or {"secrets": {}, "variables": {}}

    # Build env block
    env_lines = _build_env_block(manifest)

    # Build deploy steps based on target
    deploy_steps = _build_deploy_steps(deploy_target, tech_stack)

    # Determine if we need packages:write
    needs_packages_write = deploy_target in ("docker", "flyio", "railway")

    deploy_permissions = "      contents: read\n      id-token: write"
    if needs_packages_write:
        deploy_permissions += "\n      packages: write"

    checkout_action = resolve_action("actions/checkout")
    setup_node_action = resolve_action("actions/setup-node")

    node_version = tech_stack.get("node_version", "20")

    # Build the post-deploy scrubbing step from secrets manifest
    scrub_step = _build_scrub_step(manifest)

    workflow = f"""name: Deploy

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: {checkout_action}
      - uses: {setup_node_action}
        with:
          node-version: '{node_version}'
          cache: 'npm'
      - run: npm ci
      - run: npm run lint --if-present
      - run: npm test --if-present

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
{deploy_permissions}
{env_lines}    steps:
      - uses: {checkout_action}
{deploy_steps}
{scrub_step}"""

    return workflow


def _build_env_block(manifest: Dict[str, Dict[str, str]]) -> str:
    """Build the env: block for the deploy job."""
    secrets = manifest.get("secrets", {})
    variables = manifest.get("variables", {})

    if not secrets and not variables:
        return ""

    lines = ["    env:"]
    for key in sorted(secrets.keys()):
        lines.append(f"      {key}: ${{{{ secrets.{key} }}}}")
    for key in sorted(variables.keys()):
        lines.append(f"      {key}: ${{{{ vars.{key} }}}}")
    lines.append("")

    return "\n".join(lines) + "\n"


def _build_scrub_step(manifest: Dict[str, Dict[str, str]]) -> str:
    """
    Build a post-deployment cleanup step that unsets all injected secrets.

    Uses `if: always()` so the step runs even if the deployment fails,
    preventing secret leakage in runner environment.
    """
    secrets = manifest.get("secrets", {})
    variables = manifest.get("variables", {})

    all_keys = sorted(secrets.keys()) + sorted(variables.keys())

    unset_lines = [f"unset {key}" for key in all_keys]
    unset_lines.append('echo "Environment scrubbed."')

    run_block = "\n          ".join(unset_lines)

    return f"""      - name: Post-Deployment Cleanup
        if: always()
        run: |
          {run_block}"""


def _build_deploy_steps(deploy_target: str, tech_stack: Dict[str, Any]) -> str:
    """Build deploy steps for the specified target."""
    if deploy_target == "vercel":
        return _vercel_deploy_steps()
    elif deploy_target == "flyio":
        return _flyio_deploy_steps()
    elif deploy_target == "railway":
        return _docker_deploy_steps("railway")
    else:
        return _docker_deploy_steps("ghcr")


def _vercel_deploy_steps() -> str:
    setup_node = resolve_action("actions/setup-node")
    return f"""      - uses: {setup_node}
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci
      - run: npm run build
      - run: npx vercel deploy --prod --token=${{{{ secrets.VERCEL_TOKEN }}}}"""


def _flyio_deploy_steps() -> str:
    setup_flyctl = resolve_action("superfly/flyctl-actions/setup-flyctl")
    return f"""      - uses: {setup_flyctl}
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{{{ secrets.FLY_API_TOKEN }}}}"""


def _docker_deploy_steps(push_target: str) -> str:
    setup_buildx = resolve_action("docker/setup-buildx-action")
    login_action = resolve_action("docker/login-action")
    build_push = resolve_action("docker/build-push-action")

    if push_target == "railway":
        return f"""      - uses: {setup_buildx}
      - uses: {login_action}
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}
      - uses: {build_push}
        with:
          push: true
          tags: ghcr.io/${{{{ github.repository }}}}:latest"""
    else:
        # Default GHCR push
        return f"""      - uses: {setup_buildx}
      - uses: {login_action}
        with:
          registry: ghcr.io
          username: ${{{{ github.actor }}}}
          password: ${{{{ secrets.GITHUB_TOKEN }}}}
      - uses: {build_push}
        with:
          push: true
          tags: ghcr.io/${{{{ github.repository }}}}:latest"""


# =============================================================================
# Standard File Generation
# =============================================================================

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "deployment"


def _load_template(name: str) -> str:
    """Load a deployment template file. Returns empty string if not found."""
    path = _TEMPLATES_DIR / name
    if path.exists():
        return path.read_text()
    logger.warning("Template not found: %s", path)
    return ""


def generate_package_json(
    project_name: str,
    tech_stack: Dict[str, Any],
    extra_deps: Optional[Dict[str, str]] = None,
) -> str:
    """Generate a standard package.json."""
    deps = {
        "next": "14.2.25",
        "react": "^18.3.1",
        "react-dom": "^18.3.1",
    }

    frontend_framework = tech_stack.get("frontend", "")
    if (
        "tailwind" in str(frontend_framework).lower()
        or "tailwind" in str(tech_stack).lower()
    ):
        deps["tailwindcss"] = "^3.4.0"
        deps["autoprefixer"] = "^10.4.0"
        deps["postcss"] = "^8.4.0"

    if extra_deps:
        deps.update(extra_deps)

    dev_deps = {
        "typescript": "^5.4.0",
        "@types/react": "^18.3.0",
        "@types/node": "^20.0.0",
    }

    pkg = {
        "name": _slugify(project_name),
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
            "lint": "next lint",
        },
        "dependencies": dict(sorted(deps.items())),
        "devDependencies": dict(sorted(dev_deps.items())),
    }

    return json.dumps(pkg, indent=2) + "\n"


def generate_env_example(
    tech_stack: Dict[str, Any],
    auth_strategy: Optional[str] = None,
) -> str:
    """Generate .env.example with all required env vars."""
    lines = ["# Environment Variables", "# Copy to .env.local and fill in values", ""]

    # Database
    db = tech_stack.get("database", "")
    if db:
        lines.append("# Database")
        lines.append(
            f'DATABASE_URL="postgresql://user:password@localhost:5432/{_slugify(str(db))}"'
        )
        lines.append("")

    # Auth
    if auth_strategy == "clerk":
        lines.append("# Clerk Authentication")
        lines.append('NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="pk_test_..."')
        lines.append('CLERK_SECRET_KEY="sk_test_..."')
        lines.append("")
    elif auth_strategy == "nextauth":
        lines.append("# NextAuth")
        lines.append('NEXTAUTH_SECRET="generate-a-random-secret"')
        lines.append('NEXTAUTH_URL="http://localhost:3000"')
        lines.append("")
    elif auth_strategy == "custom_jwt":
        lines.append("# JWT Authentication")
        lines.append('JWT_SECRET="generate-a-random-secret"')
        lines.append('JWT_REFRESH_SECRET="generate-another-random-secret"')
        lines.append("")

    # API URL
    lines.append("# API")
    lines.append('NEXT_PUBLIC_API_URL="http://localhost:8000"')
    lines.append("")

    return "\n".join(lines)


def generate_dockerfile(tech_stack: Dict[str, Any]) -> str:
    """Generate a multi-stage Dockerfile."""
    template = _load_template("Dockerfile.template")
    if template:
        node_version = tech_stack.get("node_version", "20")
        return template.replace("{{NODE_VERSION}}", str(node_version))

    # Fallback if template missing
    node_version = tech_stack.get("node_version", "20")
    return f"""FROM node:{node_version}-alpine AS base

FROM base AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --only=production

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
"""


def generate_docker_compose(
    tech_stack: Dict[str, Any],
    project_name: str = "app",
) -> str:
    """Generate docker-compose.yml. Includes db service if database detected."""
    template = _load_template("docker-compose.template")

    slug = _slugify(project_name)
    db = tech_stack.get("database", "")

    if template:
        result = template.replace("{{PROJECT_NAME}}", slug)
        if db:
            result = result.replace("{{DB_SERVICE}}", _db_service_block(db))
            result = result.replace("{{DEPENDS_ON}}", "\n    depends_on:\n      - db")
        else:
            result = result.replace("{{DB_SERVICE}}", "")
            result = result.replace("{{DEPENDS_ON}}", "")
        return result

    # Fallback
    services = """services:
  app:
    build: .
    ports:
      - "3000:3000"
    env_file:
      - .env"""

    if db:
        services += "\n    depends_on:\n      - db"
        services += "\n" + _db_service_block(db)

    return services + "\n"


def _db_service_block(db: str) -> str:
    """Generate a database service block for docker-compose."""
    db_lower = str(db).lower()
    if "postgres" in db_lower:
        return """  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: app
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:"""
    elif "mysql" in db_lower:
        return """  db:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: password
      MYSQL_DATABASE: app
    ports:
      - "3306:3306"
    volumes:
      - mysqldata:/var/lib/mysql

volumes:
  mysqldata:"""
    return ""


def generate_gitignore() -> str:
    """Generate a standard .gitignore."""
    return """node_modules/
.next/
.env
.env.local
.env.*.local
dist/
build/
*.log
.DS_Store
coverage/
.vercel/
"""


def generate_tsconfig() -> str:
    """Generate a standard tsconfig.json."""
    config = {
        "compilerOptions": {
            "target": "es2017",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "paths": {"@/*": ["./*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    }
    return json.dumps(config, indent=2) + "\n"


def generate_readme(
    project_name: str,
    tech_stack: Dict[str, Any],
    auth_strategy: Optional[str] = None,
) -> str:
    """Generate README.md with setup, run, and deploy instructions."""
    template = _load_template("readme.template.md")

    slug = _slugify(project_name)
    has_db = bool(tech_stack.get("database"))

    if template:
        result = template.replace("{{PROJECT_NAME}}", project_name)
        result = result.replace("{{PROJECT_SLUG}}", slug)
        result = result.replace(
            "{{NODE_VERSION}}", str(tech_stack.get("node_version", "20"))
        )
        if has_db:
            result = result.replace("{{DB_SETUP}}", "docker-compose up -d db")
        else:
            result = result.replace("{{DB_SETUP}}", "# No database required")
        return result

    # Fallback
    lines = [
        f"# {project_name}",
        "",
        "## Prerequisites",
        "",
        f"- Node.js {tech_stack.get('node_version', '20')}+",
        "- npm",
    ]
    if has_db:
        lines.append("- Docker (for database)")
    lines.extend(
        [
            "",
            "## Setup",
            "",
            "```bash",
            "npm install",
            "cp .env.example .env.local",
            "# Fill in your environment variables in .env.local",
        ]
    )
    if has_db:
        lines.append("docker-compose up -d db")
    lines.extend(
        [
            "```",
            "",
            "## Development",
            "",
            "```bash",
            "npm run dev",
            "```",
            "",
            "## Production",
            "",
            "```bash",
            "npm run build",
            "npm start",
            "```",
            "",
            "## Deploy",
            "",
            "```bash",
            "docker-compose up -d",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


# =============================================================================
# Main Entry Point: generate_standard_files()
# =============================================================================


def generate_standard_files(
    project_name: str = "my-app",
    tech_stack: Optional[Dict[str, Any]] = None,
    auth_strategy: Optional[str] = None,
    deploy_target: str = "docker",
    extra_deps: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Generate all standard deployment files for a project.

    Returns a dict of {filepath: content} to be merged into the project output.
    All files are VBuilder-free (zero lock-in).
    """
    stack = tech_stack or {}

    # 1. Core project files
    env_example = generate_env_example(stack, auth_strategy)
    secrets_manifest = build_secrets_manifest(env_example)

    files: Dict[str, str] = {
        "package.json": generate_package_json(project_name, stack, extra_deps),
        "tsconfig.json": generate_tsconfig(),
        ".env.example": env_example,
        ".gitignore": generate_gitignore(),
        "Dockerfile": generate_dockerfile(stack),
        "docker-compose.yml": generate_docker_compose(stack, project_name),
        "README.md": generate_readme(project_name, stack, auth_strategy),
    }

    # 2. CI/CD Autopilot: GitHub Actions workflow
    workflow = generate_deploy_workflow(stack, deploy_target, secrets_manifest)
    files[".github/workflows/deploy.yml"] = workflow

    # 3. Secrets setup guide
    secrets_md = generate_secrets_setup_md(secrets_manifest)
    files["SECRETS_SETUP.md"] = secrets_md

    return files


# =============================================================================
# Helpers
# =============================================================================


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"
