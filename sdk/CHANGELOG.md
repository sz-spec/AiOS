# VOS3 SDK Changelog

All notable changes to the VOS3 SDK will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-03-08

### Added

#### TypeScript SDK (`@vos3/sdk` v0.1.0)

- **VOS3App** base class for platform applications with one-line initialization:
  `const app = new VOS3App({ appId: "my-app", apiKey: "..." })`
- **VOS3Client** typed HTTP client for all VOS3 platform APIs with built-in
  authentication via `Authorization: Bearer` header and `X-VOS3-App-Id` header
- **VOS3Hook** class for registering frontend UI extensions into named slots,
  each gated by a required OAuth scope
- **VOS3AppConfig** interface for app configuration (appId, apiKey, baseUrl,
  scopes, version)
- **AppManifest** interface defining the `app.json` schema with fields for id,
  name, version, description, author, type (`plugin | fullstack | kernel`),
  main entrypoint, scopes, category, pricing (`free | paid | subscription`),
  price, icon, screenshots, hooks, and config_schema
- **VOS3Scope** type union covering all 9 OAuth 2.0 scopes
- **VOS3Response\<T\>** generic response wrapper with `data`, `ok`, and `error`
  fields
- Built-in typed methods on VOS3Client: `listEntities`, `getEntity`,
  `listRecords`, `createRecord`, `executeWorkflow`, `generateText`, `readFile`,
  `writeFile`
- Hook event system on VOS3App: `on(hookName, handler)` and
  `emit(hookName, ...args)` for lifecycle and custom events
- Scope checking via `app.hasScope(scope)` at runtime
- Compiled to ES2020 with full TypeScript declaration files

#### Python SDK (`vos3-sdk` v0.1.0)

- **VOS3Plugin** base class for backend app extensions following the stateless
  app pattern (Shopify model) with lifecycle hooks:
  - `on_install(context)` -- called when the app is installed in an organization
  - `on_uninstall(context)` -- called when the app is removed
  - `on_enable(context)` / `on_disable(context)` -- toggle hooks
  - `on_hook(hook_name, data)` -- general-purpose event hook with optional
    return data to modify the event
- **VOS3APIClient** HTTP client with dual transport: uses `httpx` when
  available, falls back to `urllib.request` for zero-dependency operation
- Built-in typed methods on VOS3APIClient: `list_entities`, `get_entity`,
  `list_records`, `create_record`, `execute_workflow`, `generate_text`,
  `read_file`, `write_file`
- Lazy-initialized API client on VOS3Plugin via the `client` property
- `get_manifest()` helper to generate an app manifest dict from class attributes
- Published with `extras_require={"httpx": ["httpx>=0.24.0"]}` for optional
  fast transport; Python >= 3.9 required

#### OAuth 2.0 Permissions (9 Scopes)

| Scope | Description |
|-------|-------------|
| `vos3:entities:read` | View business entities and their schemas |
| `vos3:entities:write` | Create and modify business entities |
| `vos3:records:read` | Read data records |
| `vos3:records:write` | Create, update, and delete data records |
| `vos3:workflows:execute` | Execute automated workflows |
| `vos3:ai:generate` | Generate text and code using AI models |
| `vos3:files:read` | Read project files |
| `vos3:files:write` | Create and modify project files |
| `vos3:kernel:execute` | Execute programs on the VOS3 kernel |

Scopes follow incremental authorization -- apps request scopes as needed, not
all upfront. The consent UI groups scopes by category (Data, Automation, AI,
Files, Kernel).

#### Platform APIs

- **App API** (`/api/apps/v1/*`) -- scoped endpoints for entities, records,
  workflows, AI generation, and file operations
- **App Auth** (`/api/apps/authorize`) -- OAuth 2.0 consent flow with
  `authorize`, `authorize/grant`, and `authorize/revoke` endpoints
- **App Submissions** (`/api/apps/submissions/*`) -- submit apps for review,
  check review status, list versions
- **Developer Portal** (`/api/developers/*`) -- developer registration, profile
  management, app listing
- **Developer Analytics** (`/api/developers/analytics/*`) -- download counts,
  earnings, per-app metrics
- **Marketplace** (`/api/v1/marketplace/*`) -- component search, install

#### App Manifest Schema

- Defined `AppManifest` interface (TypeScript) and `get_manifest()` (Python)
  supporting the full app descriptor format including pricing model
  configuration and UI extension slots

---

_Initial release of the VOS3 App Platform SDK._
