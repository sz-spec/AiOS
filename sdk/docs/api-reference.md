# VOS3 API Reference

Complete reference for the VOS3 platform APIs available to third-party apps.

All endpoints require authentication via the `Authorization: Bearer <token>`
header and the `X-VOS3-App-Id` header.

Base URL: `https://api.vos3.app`

---

## OAuth 2.0 Scopes

VOS3 uses OAuth 2.0 scope-based permissions. Each API endpoint requires one or
more scopes. Scopes are grouped into categories for the consent UI.

### Data Scopes

| Scope | Description |
|-------|-------------|
| `vos3:entities:read` | View business entities and their schemas. Required by entity listing and detail endpoints. |
| `vos3:entities:write` | Create and modify business entities. Required for entity creation and schema updates. |
| `vos3:records:read` | Read data records belonging to entities. Required by record listing endpoints. |
| `vos3:records:write` | Create, update, and delete data records. Required for any record mutation. |

### Automation Scopes

| Scope | Description |
|-------|-------------|
| `vos3:workflows:execute` | Execute automated workflows. Required to trigger workflow runs via the API. |

### AI Scopes

| Scope | Description |
|-------|-------------|
| `vos3:ai:generate` | Generate text and code using AI models. Required to call the AI generation endpoint. Requests are routed through the VOS3 Smart Router to the optimal model. |

### File Scopes

| Scope | Description |
|-------|-------------|
| `vos3:files:read` | Read project files. Required for file content retrieval. |
| `vos3:files:write` | Create and modify project files. Required for file creation and updates. |

### Kernel Scopes

| Scope | Description |
|-------|-------------|
| `vos3:kernel:execute` | Execute programs on the VOS3 kernel. Reserved for system-level integrations. Requires additional review during app submission. |

---

## App API Endpoints (`/api/apps/v1/*`)

Scoped endpoints for third-party app data access. Each endpoint enforces the
required OAuth scope via the `@requires_scope` decorator.

### Entities

#### `GET /api/apps/v1/entities`

List entities for an organization.

**Scope:** `vos3:entities:read`

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `org` | string | query | Organization ID (required) |

**Response:**

```json
{
  "data": [
    {
      "id": "entity-123",
      "name": "Products",
      "fields": [...]
    }
  ]
}
```

#### `GET /api/apps/v1/entities/{entity_id}`

Get a single entity by ID.

**Scope:** `vos3:entities:read`

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `entity_id` | string | path | Entity ID (required) |

**Response:** Entity object with id, name, fields, and schema.

**Errors:**
- `404` -- Entity not found

---

### Records

#### `GET /api/apps/v1/records`

List records for an entity.

**Scope:** `vos3:records:read`

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `entity` | string | query | Entity ID (required) |

**Response:**

```json
{
  "data": [
    {
      "id": "record-456",
      "entityId": "entity-123",
      "data": { "name": "Widget", "quantity": 42 }
    }
  ]
}
```

#### `POST /api/apps/v1/records`

Create a new record.

**Scope:** `vos3:records:write`

**Request Body:**

```json
{
  "entityId": "entity-123",
  "data": {
    "name": "New Widget",
    "quantity": 100
  }
}
```

**Response:** The created record object.

**Errors:**
- `400` -- Validation error

---

### Workflows

#### `POST /api/apps/v1/workflows/{workflow_id}/execute`

Execute a workflow.

**Scope:** `vos3:workflows:execute`

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `workflow_id` | string | path | Workflow ID (required) |

**Request Body:**

```json
{
  "input": {
    "key": "value"
  }
}
```

**Response:** Workflow execution result.

**Errors:**
- `400` -- Execution error

---

### AI

#### `POST /api/apps/v1/ai/generate`

Generate text using AI models.

**Scope:** `vos3:ai:generate`

**Request Body:**

```json
{
  "prompt": "Summarize the following data...",
  "model": "claude-sonnet",
  "maxTokens": 1024
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | The prompt to send to the AI model |
| `model` | string | No | Model alias (routed by Smart Router if omitted) |
| `maxTokens` | integer | No | Maximum tokens in the response |

**Response:**

```json
{
  "content": "Here is a summary of the data..."
}
```

---

### Files

#### `GET /api/apps/v1/files`

Read a file.

**Scope:** `vos3:files:read`

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `path` | string | query | File path, URL-encoded (required) |

**Response:**

```json
{
  "content": "file contents here...",
  "path": "/src/main.ts"
}
```

#### `PUT /api/apps/v1/files`

Write a file.

**Scope:** `vos3:files:write`

**Request Body:**

```json
{
  "path": "/src/main.ts",
  "content": "// new file contents"
}
```

**Response:**

```json
{
  "ok": true,
  "path": "/src/main.ts"
}
```

---

## App Auth Endpoints (`/api/apps/*`)

OAuth 2.0 consent flow for app authorization.

### `POST /api/apps/authorize`

Initiate the OAuth consent flow. Returns the requested scopes with descriptions
for rendering the consent UI.

**Request Body:**

```json
{
  "app_id": "my-app",
  "redirect_uri": "https://myapp.com/callback",
  "scopes": ["vos3:records:read", "vos3:records:write"],
  "state": "optional-csrf-token"
}
```

**Response:**

```json
{
  "app_id": "my-app",
  "consent_url": null,
  "scopes": [
    {
      "scope": "vos3:records:read",
      "description": "Read data records"
    },
    {
      "scope": "vos3:records:write",
      "description": "Create, update, and delete data records"
    }
  ]
}
```

**Errors:**
- `400` -- Invalid scope requested

### `POST /api/apps/authorize/grant`

User grants consent. Issues an access token with the granted scopes.

**Request Body:**

```json
{
  "app_id": "my-app",
  "granted_scopes": ["vos3:records:read", "vos3:records:write"],
  "user_id": "user-123",
  "organization_id": "org-456"
}
```

**Response:**

```json
{
  "access_token": "vos3_at_...",
  "token_type": "Bearer",
  "scopes": ["vos3:records:read", "vos3:records:write"],
  "expires_in": 3600
}
```

### `POST /api/apps/authorize/revoke`

Revoke all permissions for an app in an organization.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `app_id` | string | query | App ID (required) |
| `organization_id` | string | query | Organization ID (required) |

**Response:**

```json
{
  "revoked": true,
  "app_id": "my-app",
  "organization_id": "org-456"
}
```

---

## Developer API Endpoints (`/api/developers/*`)

Developer registration, profile management, and app listing.

### `POST /api/developers/register`

Register as a developer on the VOS3 platform.

**Request Body:**

```json
{
  "display_name": "Jane Developer",
  "email": "jane@example.com",
  "website": "https://janedev.com",
  "bio": "Building tools for the VOS3 ecosystem"
}
```

**Response:**

```json
{
  "id": "dev-001",
  "display_name": "Jane Developer",
  "email": "jane@example.com",
  "website": "https://janedev.com",
  "bio": "Building tools for the VOS3 ecosystem",
  "verified": false,
  "total_apps": 0,
  "total_earnings": 0.0
}
```

### `GET /api/developers/profile/{user_id}`

Get a developer profile.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `user_id` | string | path | User ID (required) |

**Response:** Developer profile object (same shape as registration response).

**Errors:**
- `404` -- Developer not found

### `GET /api/developers/{user_id}/apps`

List all apps published by a developer.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `user_id` | string | path | User/Developer ID (required) |

**Response:**

```json
{
  "apps": [
    {
      "id": "my-app",
      "name": "My App",
      "version": "1.0.0",
      "status": "published",
      "downloads": 150,
      "avgRating": 4.5
    }
  ]
}
```

---

## Developer Analytics Endpoints (`/api/developers/analytics/*`)

Download counts, revenue, ratings, and usage metrics for developers.

### `GET /api/developers/analytics/{developer_id}/overview`

Get analytics overview for a developer across all apps.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `developer_id` | string | path | Developer ID (required) |

**Response:**

```json
{
  "developer_id": "dev-001",
  "total_apps": 3,
  "total_downloads": 1250,
  "average_rating": 4.3
}
```

### `GET /api/developers/analytics/{developer_id}/earnings`

Get earnings data for a developer.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `developer_id` | string | path | Developer ID (required) |

**Response:**

```json
{
  "total_earnings": 450.00,
  "pending_payout": 75.00,
  "last_payout": "2026-02-28T00:00:00Z"
}
```

### `GET /api/developers/analytics/{developer_id}/apps/{app_id}/metrics`

Get detailed metrics for a specific app.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `developer_id` | string | path | Developer ID (required) |
| `app_id` | string | path | App ID (required) |
| `period` | string | query | Time period (default: `30d`). Options: `7d`, `30d`, `90d`, `1y` |

**Response:**

```json
{
  "app_id": "my-app",
  "period": "30d",
  "downloads": 42,
  "active_installations": 38,
  "api_calls": 12500,
  "avg_rating": 4.7,
  "revenue": 120.00
}
```

---

## Marketplace API Endpoints (`/api/v1/marketplace/*`)

Browse and install components from the VOS3 marketplace.

### `GET /api/v1/marketplace/components`

List marketplace components with optional search and filtering.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `search` | string | query | Search query (optional) |
| `category` | string | query | Filter by category (optional) |

**Response:**

```json
{
  "components": [
    {
      "id": "comp-001",
      "name": "Data Table Widget",
      "description": "A configurable data table for entity records",
      "category": "ui",
      "rating": 4.5,
      "downloads": 320
    }
  ]
}
```

### `POST /api/v1/marketplace/install`

Install a marketplace component into a project.

**Request Body:**

```json
{
  "project_id": "proj-123",
  "component_id": "comp-001"
}
```

**Response:**

```json
{
  "installed": true,
  "files": ["components/DataTable.tsx", "hooks/useDataTable.ts"]
}
```

**Errors:**
- `404` -- Project not found
- `404` -- Component not found

---

## Submission API Endpoints (`/api/apps/submissions/*`)

Submit apps for marketplace review, check review status, and manage versions.

### `POST /api/apps/submissions/submit`

Submit an app for review.

**Request Body:**

```json
{
  "app_id": "my-app",
  "version": "1.0.0",
  "changelog": "Initial release with record tracking",
  "manifest": {
    "id": "my-app",
    "name": "My App",
    "version": "1.0.0",
    "description": "Description of the app",
    "scopes": ["vos3:records:read"],
    "type": "plugin",
    "main": "dist/index.js",
    "category": "productivity",
    "pricing": "free"
  },
  "developer_id": "dev-001"
}
```

**Response (201 Created):**

```json
{
  "submission_id": "sub-789",
  "status": "review",
  "message": "App submitted for review. Auto-checks passed."
}
```

**Errors:**
- `400` -- Manifest validation failed (missing `name`, `scopes`, or
  `description`)

### `GET /api/apps/submissions/{app_id}/status`

Check the review status for an app.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `app_id` | string | path | App ID (required) |

**Response:**

```json
{
  "app_id": "my-app",
  "status": "review",
  "checks": {
    "manifest_valid": true,
    "permissions_audit": true,
    "sandbox_test": true
  }
}
```

### `GET /api/apps/submissions/{app_id}/versions`

List all submitted versions of an app.

| Parameter | Type | In | Description |
|-----------|------|-----|-------------|
| `app_id` | string | path | App ID (required) |

**Response:**

```json
{
  "versions": [
    {
      "version": "1.0.0",
      "status": "published",
      "changelog": "Initial release",
      "submittedAt": "2026-03-08T12:00:00Z"
    }
  ]
}
```

---

## Error Responses

All API errors follow a consistent format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

For validation errors with multiple issues:

```json
{
  "detail": {
    "errors": [
      "Manifest missing 'name'",
      "Manifest missing 'scopes'"
    ]
  }
}
```

### Common HTTP Status Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `201` | Created |
| `400` | Bad request (validation error, invalid scopes) |
| `403` | Forbidden (missing required OAuth scope) |
| `404` | Resource not found |
| `500` | Internal server error |

### Scope Enforcement Errors

When a request is missing a required scope, the API returns:

```json
{
  "detail": "Missing required scopes: vos3:records:write"
}
```

Status code: `403 Forbidden`

---

## Authentication Headers

All API requests must include:

| Header | Value | Description |
|--------|-------|-------------|
| `Authorization` | `Bearer <access_token>` | OAuth access token from the consent flow |
| `X-VOS3-App-Id` | `<app_id>` | Your application's unique identifier |
| `Content-Type` | `application/json` | Required for POST/PUT requests |
| `X-VOS3-App-Version` | `<version>` | Optional. App version for request tracking |

---

## Rate Limits

API requests are subject to rate limiting. Current limits are returned in
response headers:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Maximum requests per window |
| `X-RateLimit-Remaining` | Requests remaining in current window |
| `X-RateLimit-Reset` | Unix timestamp when the window resets |
