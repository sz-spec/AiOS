# GitHub Secrets & Variables Setup

Go to your repo **Settings** > **Secrets and variables** > **Actions**.

## Secrets (encrypted, not visible in logs)

| Name | Description |
|------|-------------|
{{SECRETS_TABLE}}

## Variables (non-secret, visible in build logs)

| Name | Description |
|------|-------------|
{{VARIABLES_TABLE}}

## OIDC Authentication (Recommended)

This workflow uses OpenID Connect (OIDC) tokens (`id-token: write`)
instead of static API secrets for deployment authentication.

### Why OIDC?

Static secrets (API keys, tokens) stored in GitHub Secrets are risky:

- **Rotation burden**: Leaked or expired secrets require manual rotation.
- **Blast radius**: A compromised secret grants access until revoked.
- **No audit trail**: Static secrets lack per-request identity binding.

OIDC tokens are **short-lived**, **scoped to a single workflow run**,
and **automatically verified** by the deployment platform — eliminating
the need to store long-lived credentials in your repository.

### Setup

1. Configure your deployment platform to trust GitHub's OIDC provider.
2. The workflow already includes `permissions: id-token: write`.
3. Deployment steps use the platform's OIDC/federated identity flow.

For platforms that do not yet support OIDC (e.g., some Railway setups),
the workflow falls back to `secrets.DEPLOY_TOKEN` — but OIDC is the
target state. Migrate when your platform adds support.
