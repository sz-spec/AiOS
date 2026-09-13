# Getting Started with VOS3 App Development

Build apps for the VOS3 platform using the official TypeScript or Python SDK.
This guide walks through everything from installation to marketplace submission.

## Prerequisites

| Language | Requirement |
|----------|-------------|
| TypeScript | Node.js 18+ and npm 9+ |
| Python | Python 3.10+ and pip |

You will also need:

- A VOS3 developer account (register at the Developer Portal or via the API)
- An API key generated from your developer dashboard

## 1. Install the SDK

### TypeScript

```bash
npm install @vos3/sdk
```

### Python

```bash
pip install vos3-sdk

# For faster HTTP transport (recommended):
pip install vos3-sdk[httpx]
```

## 2. Create Your App Scaffold

### TypeScript

```bash
mkdir my-vos3-app && cd my-vos3-app
npm init -y
npm install @vos3/sdk
```

Create `src/index.ts`:

```typescript
import { VOS3App } from "@vos3/sdk";

const app = new VOS3App({
  appId: "my-vos3-app",
  apiKey: process.env.VOS3_API_KEY!,
  scopes: ["vos3:records:read", "vos3:records:write"],
});

// Your app logic here
const records = await app.client.listRecords("my-entity-id");
console.log(records);
```

### Python

```bash
mkdir my-vos3-app && cd my-vos3-app
pip install vos3-sdk
```

Create `main.py`:

```python
from vos3_sdk import VOS3APIClient

client = VOS3APIClient(
    app_id="my-vos3-app",
    api_key="your-api-key",
)

records = client.list_records(entity_id="my-entity-id")
print(records)
```

## 3. Define Your App Manifest

Every VOS3 app requires a manifest file (`app.json`) that describes the app,
its required permissions, and its type.

Create `app.json` in your project root:

```json
{
  "id": "my-vos3-app",
  "name": "My VOS3 App",
  "version": "1.0.0",
  "description": "A short description of what your app does.",
  "author": "Your Name",
  "type": "plugin",
  "main": "dist/index.js",
  "scopes": [
    "vos3:records:read",
    "vos3:records:write"
  ],
  "category": "productivity",
  "pricing": "free",
  "icon": "icon.png",
  "screenshots": [],
  "hooks": ["on_record_created", "on_record_updated"],
  "config_schema": {
    "notify_on_create": {
      "type": "boolean",
      "default": true,
      "description": "Send a notification when a record is created"
    }
  }
}
```

### Manifest Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique app identifier (lowercase, hyphens allowed) |
| `name` | Yes | Human-readable app name |
| `version` | Yes | Semantic version string (e.g., `1.0.0`) |
| `description` | Yes | Short description (displayed in marketplace) |
| `author` | Yes | Developer or organization name |
| `type` | Yes | One of `plugin`, `fullstack`, or `kernel` |
| `main` | Yes | Entrypoint file path |
| `scopes` | Yes | List of required OAuth 2.0 scopes |
| `category` | Yes | App category (e.g., `productivity`, `analytics`, `general`) |
| `pricing` | Yes | One of `free`, `paid`, or `subscription` |
| `price` | No | Price in USD (required if pricing is `paid` or `subscription`) |
| `icon` | No | Path to app icon image |
| `screenshots` | No | Array of screenshot image paths |
| `hooks` | No | List of event hooks the app listens to |
| `config_schema` | No | JSON schema for user-configurable settings |

### App Types

- **plugin** -- Backend-only extension. Extends VOS3Plugin (Python) or uses
  VOS3App hooks (TypeScript). Runs server-side and responds to lifecycle events.
- **fullstack** -- Combines a backend plugin with a frontend UI extension.
  Uses VOS3Hook to register components into UI slots.
- **kernel** -- Low-level extension that runs on the VOS3 kernel. Requires
  the `vos3:kernel:execute` scope. Reserved for system-level integrations.

## 4. Request Permissions (OAuth 2.0 Scopes)

VOS3 uses OAuth 2.0 scope-based permissions. Apps request only the scopes they
need, and users approve them through a consent screen.

### Available Scopes

| Scope | Description | Category |
|-------|-------------|----------|
| `vos3:entities:read` | View business entities and their schemas | Data |
| `vos3:entities:write` | Create and modify business entities | Data |
| `vos3:records:read` | Read data records | Data |
| `vos3:records:write` | Create, update, and delete data records | Data |
| `vos3:workflows:execute` | Execute automated workflows | Automation |
| `vos3:ai:generate` | Generate text and code using AI models | AI |
| `vos3:files:read` | Read project files | Files |
| `vos3:files:write` | Create and modify project files | Files |
| `vos3:kernel:execute` | Execute programs on the VOS3 kernel | Kernel |

Scopes follow **incremental authorization** -- your app can start with minimal
scopes and request additional ones later as the user accesses features that
require them.

## 5. Build a Backend Plugin (Python)

Create a Python plugin by extending the `VOS3Plugin` base class:

```python
from vos3_sdk import VOS3Plugin


class InventoryTracker(VOS3Plugin):
    name = "inventory-tracker"
    version = "1.0.0"
    description = "Tracks inventory changes and sends alerts"
    scopes = ["vos3:records:read", "vos3:records:write"]
    category = "operations"

    async def on_install(self, context):
        """Called when the app is installed in an organization."""
        print(f"Installed in org: {context.get('organization_id')}")

    async def on_uninstall(self, context):
        """Called when the app is removed."""
        print("Plugin uninstalled, cleaning up...")

    async def on_hook(self, hook_name, data):
        """Handle registered event hooks."""
        if hook_name == "on_record_updated":
            record = data.get("record", {})
            quantity = record.get("quantity", 0)
            if quantity < 10:
                # Use the built-in API client to create an alert record
                self.client.create_record(
                    entity_id="alerts",
                    data={"message": f"Low stock: {record.get('name')}", "level": "warning"},
                )
        return None  # Return data to modify the event, or None to pass through
```

### Plugin Lifecycle Hooks

| Hook | When it fires |
|------|---------------|
| `on_install(context)` | App is installed in an organization |
| `on_uninstall(context)` | App is removed from an organization |
| `on_enable(context)` | App is re-enabled after being disabled |
| `on_disable(context)` | App is temporarily disabled |
| `on_hook(hook_name, data)` | A registered event hook fires |

## 6. Build a Frontend Extension (TypeScript)

Use VOS3Hook to register UI components into named extension slots:

```typescript
import { VOS3App, VOS3Hook } from "@vos3/sdk";

const app = new VOS3App({
  appId: "dashboard-widget",
  apiKey: process.env.VOS3_API_KEY!,
  scopes: ["vos3:records:read"],
});

const hooks = new VOS3Hook();

// Register a React component into the "dashboard-sidebar" slot
hooks.registerExtension(
  "dashboard-sidebar",
  MyWidgetComponent,
  "vos3:records:read"
);

// Listen for app lifecycle events
app.on("activate", async () => {
  const records = await app.client.listRecords("metrics");
  console.log("Widget activated with", records.data?.length, "records");
});
```

## 7. Build a Full-Stack App

Combine a backend plugin with a frontend extension for a complete app:

**Backend** (`plugin.py`):

```python
from vos3_sdk import VOS3Plugin


class ReportGenerator(VOS3Plugin):
    name = "report-generator"
    scopes = ["vos3:records:read", "vos3:ai:generate"]

    async def on_hook(self, hook_name, data):
        if hook_name == "generate_report":
            records = self.client.list_records(data["entity_id"])
            summary = self.client.generate_text(
                f"Summarize this data: {records}"
            )
            return {"report": summary}
        return None
```

**Frontend** (`src/index.ts`):

```typescript
import { VOS3App, VOS3Hook } from "@vos3/sdk";

const app = new VOS3App({
  appId: "report-generator",
  apiKey: process.env.VOS3_API_KEY!,
  scopes: ["vos3:records:read", "vos3:ai:generate"],
});

const hooks = new VOS3Hook();
hooks.registerExtension("tools-panel", ReportPanel, "vos3:records:read");
```

**Manifest** (`app.json`):

```json
{
  "id": "report-generator",
  "name": "Report Generator",
  "version": "1.0.0",
  "description": "AI-powered report generation from your business data",
  "author": "Your Name",
  "type": "fullstack",
  "main": "dist/index.js",
  "scopes": ["vos3:records:read", "vos3:ai:generate"],
  "category": "analytics",
  "pricing": "subscription",
  "price": 9.99
}
```

## 8. Submit to the Marketplace

Once your app is ready, submit it for review:

```bash
curl -X POST https://api.vos3.app/api/apps/submissions/submit \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "my-vos3-app",
    "version": "1.0.0",
    "changelog": "Initial release",
    "manifest": { ...your app.json contents... },
    "developer_id": "your-developer-id"
  }'
```

### Submission Requirements

- The manifest must include `name`, `description`, and `scopes`
- All requested scopes must be valid VOS3 OAuth 2.0 scopes
- The app must pass automated checks (manifest validation, permissions audit,
  sandbox test)

### Review Process

1. **Submit** -- `POST /api/apps/submissions/submit`
2. **Auto-checks** -- manifest validation, scope verification, sandbox testing
3. **Manual review** -- VOS3 team reviews functionality and security
4. **Published** -- app appears in the marketplace

Check your review status at any time:

```bash
curl https://api.vos3.app/api/apps/submissions/MY_APP_ID/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## 9. Revenue Sharing

VOS3 uses a **70/30 revenue split** for paid apps and subscriptions:

| Recipient | Share |
|-----------|-------|
| Developer | 70% |
| VOS3 Platform | 30% |

This split is configurable for enterprise partnerships. Track your earnings
through the Developer Analytics API:

```bash
curl https://api.vos3.app/api/developers/analytics/YOUR_DEV_ID/earnings \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Next Steps

- Read the [API Reference](./api-reference.md) for complete endpoint
  documentation
- Review the [Changelog](../CHANGELOG.md) for the latest SDK updates
- File bugs or feature requests using the GitHub issue templates
