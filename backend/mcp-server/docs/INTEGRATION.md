# V OS MCP Agent Server - Integration Guide

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Integration Methods](#integration-methods)
  - [HTTP Transport](#http-transport)
  - [stdio Transport](#stdio-transport)
- [Client Integrations](#client-integrations)
  - [Claude Desktop](#claude-desktop)
  - [Cursor](#cursor)
  - [Cline (VS Code)](#cline-vs-code)
  - [Windsurf](#windsurf)
  - [VS Code Copilot](#vs-code-copilot)
  - [Custom Client](#custom-client)
- [Configuration Options](#configuration-options)
- [Authentication](#authentication)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

---

## Overview

V OS MCP Agent Server תומך בשתי שיטות חיבור:

| Method | Use Case | Pros | Cons |
|--------|----------|------|------|
| **HTTP** | Remote/Network | Scalable, shared server | Requires running server |
| **stdio** | Local/CLI | Simple setup, no server needed | One process per client |

### Quick Decision

```
┌─────────────────────────────────────────┐
│ How do you want to run the server?      │
├─────────────────────────────────────────┤
│                                         │
│  "I want a shared server"               │
│  └─→ Use HTTP Transport                 │
│      └─→ Run: npm start                 │
│      └─→ Connect via URL                │
│                                         │
│  "I want per-app instances"             │
│  └─→ Use stdio Transport                │
│      └─→ App spawns the process         │
│      └─→ Configure command in client    │
│                                         │
└─────────────────────────────────────────┘
```

---

## Prerequisites

### For HTTP Transport

```bash
# Server running (pick one method)
npm run dev              # Development
npm start                # Production
docker-compose up -d     # Docker
```

Verify server is running:

```bash
curl http://localhost:8080/health
```

### For stdio Transport

```bash
# Install globally (recommended)
npm install -g v-os-mcp-agent-server

# Or use npx (no install needed)
npx v-os-mcp-agent-server
```

---

## Integration Methods

### HTTP Transport

**Server Configuration:**

```bash
# .env
PORT=8080
TRANSPORT=http
ALLOWED_HOSTS=127.0.0.1,localhost
```

**Client Connection:**

```
URL: http://localhost:8080/mcp
```

**Advantages:**
- Single server for multiple clients
- Shared caching across clients
- Centralized monitoring
- Easy to scale horizontally

### stdio Transport

**How it works:**

```
┌──────────────────┐         ┌────────────────────────┐
│   MCP Client     │ ──────► │  V OS Server Process   │
│   (Cursor, etc)  │ stdin   │  (spawned by client)   │
│                  │ ◄────── │                        │
│                  │ stdout  │                        │
└──────────────────┘         └────────────────────────┘
```

**Advantages:**
- No separate server to manage
- Process isolation per client
- Simple configuration

---

## Client Integrations

### Claude Desktop

Claude Desktop מאנתרופיק הוא ה-client הרשמי הראשון עם תמיכת MCP.

#### Option A: HTTP Transport (Recommended for Teams)

1. Start the server:
   ```bash
   docker-compose up -d
   # or
   npm start
   ```

2. Configure Claude Desktop:

   **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   
   **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

   ```json
   {
     "mcpServers": {
       "v-os": {
         "type": "http",
         "url": "http://localhost:8080/mcp",
         "headers": {
           "x-api-key": "your-api-key"
         }
       }
     }
   }
   ```

3. Restart Claude Desktop

#### Option B: stdio Transport (Recommended for Personal Use)

```json
{
  "mcpServers": {
    "v-os": {
      "command": "npx",
      "args": ["-y", "v-os-mcp-agent-server"],
      "env": {
        "OPENAI_API_KEY": "sk-your-key",
        "ANTHROPIC_API_KEY": "sk-ant-your-key",
        "TRANSPORT": "stdio"
      }
    }
  }
}
```

#### Option C: Using Local Build

```json
{
  "mcpServers": {
    "v-os": {
      "command": "node",
      "args": ["/path/to/v-os-mcp-agent-server/dist/server.js"],
      "env": {
        "OPENAI_API_KEY": "sk-your-key",
        "TRANSPORT": "stdio"
      }
    }
  }
}
```

#### Verify Connection

In Claude Desktop, type:
```
Use v_agent_list to show available agents
```

---

### Cursor

Cursor IDE מגיע עם תמיכת MCP מובנית.

#### Configuration

1. Open Cursor Settings: `Cmd/Ctrl + ,`
2. Search for "MCP"
3. Click "Edit in settings.json"

**Or create:** `.cursor/mcp.json` in your project root:

#### HTTP Transport

```json
{
  "mcpServers": {
    "v-os": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "your-api-key",
        "x-user-id": "cursor-user"
      }
    }
  }
}
```

#### stdio Transport

```json
{
  "mcpServers": {
    "v-os": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "v-os-mcp-agent-server"],
      "env": {
        "OPENAI_API_KEY": "sk-your-key",
        "ANTHROPIC_API_KEY": "sk-ant-your-key"
      }
    }
  }
}
```

#### Global Configuration

For all projects, edit:

**macOS:** `~/.cursor/mcp.json`

**Windows:** `%USERPROFILE%\.cursor\mcp.json`

#### Verify in Cursor

1. Open Command Palette (`Cmd/Ctrl + Shift + P`)
2. Type "MCP: List Tools"
3. You should see v_agent_chat, v_agent_list, etc.

---

### Cline (VS Code)

Cline הוא extension פופולרי ל-VS Code עם תמיכת MCP מלאה.

#### Installation

1. Install Cline from VS Code Marketplace
2. Open Cline Settings

#### Configuration via Settings UI

1. Open VS Code Settings (`Cmd/Ctrl + ,`)
2. Search for "Cline MCP"
3. Add new server

#### Configuration via settings.json

```json
{
  "cline.mcpServers": {
    "v-os": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "your-api-key"
      },
      "timeout": 60000
    }
  }
}
```

#### stdio Configuration

```json
{
  "cline.mcpServers": {
    "v-os": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "v-os-mcp-agent-server"],
      "env": {
        "OPENAI_API_KEY": "${env:OPENAI_API_KEY}",
        "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"
      }
    }
  }
}
```

#### Workspace Configuration

Create `.vscode/settings.json` in your project:

```json
{
  "cline.mcpServers": {
    "v-os": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "project-specific-key"
      }
    }
  }
}
```

#### Verify in Cline

1. Open Cline panel
2. Type: `@v-os list available agents`
3. Or use tool directly: `/tool v_agent_list`

---

### Windsurf

Windsurf IDE (from Codeium) supports MCP servers.

#### Configuration

Edit Windsurf settings or create `windsurf.config.json`:

```json
{
  "mcp": {
    "servers": {
      "v-os": {
        "url": "http://localhost:8080/mcp",
        "auth": {
          "type": "header",
          "key": "x-api-key",
          "value": "your-api-key"
        }
      }
    }
  }
}
```

---

### VS Code Copilot

VS Code Copilot Chat יכול להתחבר ל-MCP servers.

#### Configuration

```json
// settings.json
{
  "github.copilot.chat.mcpServers": {
    "v-os": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "your-api-key"
      }
    }
  }
}
```

---

### Custom Client

#### TypeScript/JavaScript

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

async function main() {
  // Create client
  const client = new Client({
    name: "my-custom-client",
    version: "1.0.0",
  });

  // Create transport
  const transport = new StreamableHTTPClientTransport(
    new URL("http://localhost:8080/mcp"),
    {
      requestInit: {
        headers: {
          "x-api-key": process.env.V_OS_API_KEY || "your-api-key",
          "x-user-id": "custom-client-user",
        },
      },
    }
  );

  // Connect
  await client.connect(transport);

  // List available tools
  const tools = await client.listTools();
  console.log("Available tools:", tools.tools.map(t => t.name));

  // Call a tool
  const result = await client.callTool({
    name: "v_agent_chat",
    arguments: {
      message: "Hello! What can you help me with?",
      streaming: false,
    },
  });

  console.log("Response:", result.content[0].text);

  // Read a resource
  const catalog = await client.readResource({
    uri: "vos://agents/catalog",
  });

  console.log("Agent catalog:", catalog.contents[0].text);

  // Disconnect
  await transport.close();
}

main().catch(console.error);
```

#### Python

```python
import asyncio
from mcp import Client
from mcp.client.streamable_http import StreamableHTTPClientTransport

async def main():
    client = Client("my-python-client", "1.0.0")
    
    transport = StreamableHTTPClientTransport(
        url="http://localhost:8080/mcp",
        headers={
            "x-api-key": "your-api-key",
            "x-user-id": "python-user"
        }
    )
    
    await client.connect(transport)
    
    # List tools
    tools = await client.list_tools()
    print("Tools:", [t.name for t in tools.tools])
    
    # Call tool
    result = await client.call_tool(
        "v_agent_chat",
        {"message": "Hello from Python!", "streaming": False}
    )
    
    print("Response:", result.content[0].text)
    
    await transport.close()

asyncio.run(main())
```

#### curl (For Testing)

```bash
# Initialize session
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "1.0.0"}
    }
  }'

# List tools
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -H "Mcp-Session-Id: <session-id-from-init>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'

# Call tool
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -H "Mcp-Session-Id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "v_agent_list",
      "arguments": {}
    }
  }'
```

---

## Configuration Options

### Server URLs

| Environment | URL |
|-------------|-----|
| Local Dev | `http://localhost:8080/mcp` |
| Docker | `http://localhost:8080/mcp` or `http://mcp-server:8080/mcp` |
| Production | `https://mcp.your-domain.com/mcp` |

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `x-api-key` | Yes | API key for authentication |
| `x-user-id` | No | User identifier for session tracking |
| `Content-Type` | Yes | Must be `application/json` |

### Timeouts

| Operation | Recommended Timeout |
|-----------|---------------------|
| Initialize | 10 seconds |
| Tool call (non-streaming) | 60 seconds |
| Tool call (streaming) | 300 seconds |
| Resource read | 30 seconds |

---

## Authentication

### API Key (Default)

```json
{
  "headers": {
    "x-api-key": "your-api-key"
  }
}
```

### Environment Variable

```json
{
  "headers": {
    "x-api-key": "${env:V_OS_API_KEY}"
  }
}
```

### OAuth (Enterprise)

```json
{
  "auth": {
    "type": "oauth",
    "clientId": "your-client-id",
    "clientSecret": "${env:V_OS_CLIENT_SECRET}",
    "tokenUrl": "https://auth.v-os.ai/token"
  }
}
```

---

## Troubleshooting

### Common Issues

#### "Connection Refused"

```bash
# Check if server is running
curl http://localhost:8080/health

# If not, start it
npm start
# or
docker-compose up -d
```

#### "401 Unauthorized"

```bash
# Verify API key
curl -H "x-api-key: your-key" http://localhost:8080/health

# Check .env configuration
cat .env | grep API_KEY
```

#### "Tool Not Found"

```bash
# List available tools
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-key" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

#### stdio: "Command Not Found"

```bash
# Ensure package is installed
npm install -g v-os-mcp-agent-server

# Or use full path
which v-os-mcp-agent-server

# Or use npx
npx -y v-os-mcp-agent-server
```

#### Slow Responses

```bash
# Check cache stats
curl -X POST http://localhost:8080/mcp \
  -H "x-api-key: your-key" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"v_cache_stats","arguments":{}}}'

# Enable caching if disabled
# In .env: CACHE_ENABLED=true
```

### Debug Mode

```bash
# Enable verbose logging
DEBUG=true LOG_LEVEL=debug npm run dev

# View server logs
docker-compose logs -f mcp-server
```

### Test Connection

```bash
# MCP Inspector
npx @modelcontextprotocol/inspector http://localhost:8080/mcp

# Opens web UI to test tools interactively
```

---

## Best Practices

### 1. Use HTTP Transport for Teams

```
Team benefits:
✅ Shared caching (faster responses)
✅ Centralized monitoring
✅ Single point of configuration
✅ Easier to update
```

### 2. Use stdio Transport for Personal/Offline

```
Personal benefits:
✅ No server to manage
✅ Works offline (with local models)
✅ Process isolation
✅ Simpler setup
```

### 3. Secure Your API Keys

```bash
# ❌ Don't hardcode
"x-api-key": "sk-actual-key"

# ✅ Use environment variables
"x-api-key": "${env:V_OS_API_KEY}"
```

### 4. Configure Appropriate Timeouts

```json
{
  "timeout": 60000,        // 60s for regular calls
  "streamingTimeout": 300000  // 5min for streaming
}
```

### 5. Use Project-Specific Configuration

```
project/
├── .cursor/mcp.json      # Cursor config
├── .vscode/settings.json # VS Code/Cline config
└── .env                  # Environment variables
```

### 6. Monitor Performance

```bash
# View metrics
curl http://localhost:8080/metrics

# Open Grafana dashboard
open http://localhost:3001
```

---

## Support

- 📖 [Full Documentation](https://docs.v-os.ai)
- 🐛 [Issue Tracker](https://github.com/v-os/mcp-agent-server/issues)
- 💬 [Discord Community](https://discord.gg/v-os)
- 📧 [Email Support](mailto:support@v-os.ai)

---

*Last Updated: January 2026*
