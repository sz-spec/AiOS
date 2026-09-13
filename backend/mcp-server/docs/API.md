# V OS MCP Agent Server - API Documentation

## Table of Contents

- [Overview](#overview)
- [Authentication](#authentication)
- [Tools](#tools)
  - [v_agent_chat](#v_agent_chat)
  - [v_agent_list](#v_agent_list)
  - [v_agent_select](#v_agent_select)
  - [v_context_add](#v_context_add)
  - [v_context_clear](#v_context_clear)
  - [v_health](#v_health)
  - [v_cache_stats](#v_cache_stats)
- [Resources](#resources)
- [Prompts](#prompts)
- [Error Handling](#error-handling)
- [Rate Limiting](#rate-limiting)

---

## Overview

V OS MCP Agent Server exposes a Model Context Protocol (MCP) interface with:

- **7 Tools** - Agent chat, management, context, and monitoring
- **2 Resources** - Agent catalog and server capabilities
- **2 Prompts** - Code review and architecture design templates

### Base URLs

| Environment | URL |
|-------------|-----|
| Local Development | `http://localhost:8080/mcp` |
| Docker | `http://mcp-server:8080/mcp` |
| Production | `https://mcp.your-domain.com/mcp` |

### Protocol Version

```
MCP Specification: 2025-03-26
SDK Version: 1.25.2
```

---

## Authentication

All requests require authentication via HTTP headers:

```http
POST /mcp HTTP/1.1
Host: localhost:8080
Content-Type: application/json
x-api-key: your-api-key
x-user-id: optional-user-id
```

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `x-api-key` | ✅ Yes | API key for authentication |
| `x-user-id` | ❌ No | User identifier for session management |
| `Mcp-Session-Id` | ❌ Auto | Session ID (managed by transport) |

### Response Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 401 | Missing or invalid API key |
| 403 | Insufficient permissions |
| 429 | Rate limit exceeded |
| 500 | Internal server error |

---

## Tools

### v_agent_chat

Chat with a V OS AI agent with optional streaming response.

#### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `message` | string | ✅ | - | The message to send (1-10,000 chars) |
| `agentId` | string | ❌ | auto | Specific agent ID (auto-routes if omitted) |
| `streaming` | boolean | ❌ | true | Enable streaming response |

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_agent_chat",
    "arguments": {
      "message": "Write a TypeScript function to validate email addresses",
      "agentId": "vos-coder",
      "streaming": true
    }
  }
}
```

#### Response (Streaming)

When `streaming: true`, responses are sent as incremental content:

```json
{
  "content": [
    { "type": "text", "text": "Here's a TypeScript function..." }
  ]
}
```

Progress notifications are also sent:

```json
{
  "method": "notifications/progress",
  "params": {
    "progress": 50,
    "total": 100
  }
}
```

#### Response (Non-Streaming)

```json
{
  "content": [
    {
      "type": "text",
      "text": "Here's a TypeScript function to validate email addresses:\n\n```typescript\nfunction validateEmail(email: string): boolean {\n  const regex = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;\n  return regex.test(email);\n}\n```"
    }
  ]
}
```

#### Agent Routing

When `agentId` is omitted, the server automatically routes based on message intent:

| Keywords | Routed Agent |
|----------|--------------|
| code, function, implement, fix, bug | vos-coder |
| architecture, design, system, scale | vos-architect |
| research, find, explain, what is | vos-researcher |
| (default) | vos-coder |

#### Example: TypeScript Client

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";

// Basic chat
const result = await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Explain the SOLID principles",
    streaming: false
  }
});

console.log(result.content[0].text);
```

#### Example: With Specific Agent

```typescript
// Use architect agent
const result = await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Design a microservices architecture for e-commerce",
    agentId: "vos-architect",
    streaming: true
  }
});
```

#### Annotations

```json
{
  "title": "V OS Agent Chat",
  "streamingHint": true,
  "readOnlyHint": false
}
```

#### Caching

❌ **Not Cached** - Chat responses are never cached to ensure fresh responses.

#### Required Permission

`chat`

---

### v_agent_list

List all available V OS agents and their capabilities.

#### Parameters

None

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_agent_list",
    "arguments": {}
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "**V OS Coder** (vos-coder)\n  Expert software development agent\n  Provider: anthropic/claude-sonnet-4-20250514\n\n**V OS Architect** (vos-architect)\n  System design and architecture specialist\n  Provider: openai/gpt-4o\n\n**V OS Researcher** (vos-researcher)\n  Research and analysis agent with deep reasoning\n  Provider: openai/o1-preview\n\n**V OS Local Agent** (vos-local)\n  Privacy-focused local inference agent\n  Provider: local/lm-studio"
    }
  ]
}
```

#### Caching

✅ **Cached** - TTL: 10 minutes (agent list rarely changes)

---

### v_agent_select

Select a specific agent for the current session.

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `agentId` | string | ✅ | Agent ID to select |

#### Valid Agent IDs

- `vos-coder`
- `vos-architect`
- `vos-researcher`
- `vos-local`

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_agent_select",
    "arguments": {
      "agentId": "vos-architect"
    }
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "Selected agent: V OS Architect\n\nCapabilities: architecture, design, planning\nProvider: openai"
    }
  ]
}
```

#### Error Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "Agent \"invalid-agent\" not found. Available: vos-coder, vos-architect, vos-researcher, vos-local"
    }
  ],
  "isError": true
}
```

#### Required Permission

`agents`

---

### v_context_add

Add context information to the current session. Context is used to enhance agent responses.

#### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `key` | string | ✅ | - | Context key (e.g., 'project', 'requirements') |
| `value` | string | ✅ | - | Context value |
| `type` | enum | ❌ | "text" | Context type |

#### Context Types

| Type | Description |
|------|-------------|
| `text` | General text context |
| `file` | File content |
| `requirements` | Project requirements |
| `codebase` | Codebase context |

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_context_add",
    "arguments": {
      "key": "project",
      "value": "E-commerce platform with React frontend, Node.js backend, PostgreSQL database. Expected scale: 100K daily users.",
      "type": "requirements"
    }
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "Context \"project\" added successfully."
    }
  ]
}
```

#### Example: Multiple Contexts

```typescript
// Add project context
await client.callTool({
  name: "v_context_add",
  arguments: {
    key: "project",
    value: "E-commerce platform",
    type: "requirements"
  }
});

// Add file context
await client.callTool({
  name: "v_context_add",
  arguments: {
    key: "schema",
    value: "CREATE TABLE users (id SERIAL PRIMARY KEY, email VARCHAR(255));",
    type: "file"
  }
});

// Now chat with full context
await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Add a shopping cart table that references users"
  }
});
```

#### Required Permission

`context`

---

### v_context_clear

Clear all context for the current session.

#### Parameters

None

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_context_clear",
    "arguments": {}
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "Session context cleared."
    }
  ]
}
```

#### Required Permission

`context`

---

### v_health

Get server health status and security information.

#### Parameters

None

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_health",
    "arguments": {}
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"status\": \"healthy\",\n  \"version\": \"1.0.1\",\n  \"security\": {\n    \"dnsRebindingProtection\": true,\n    \"cveFixed\": [\"CVE-2025-66414\"],\n    \"sdkVersion\": \"1.25.2\",\n    \"fastmcpVersion\": \"3.26.8\"\n  },\n  \"cache\": {\n    \"totalHits\": 1523,\n    \"totalMisses\": 892,\n    \"avgHitRate\": 0.63\n  },\n  \"agents\": 4,\n  \"timestamp\": \"2026-01-12T15:30:00.000Z\"\n}"
    }
  ]
}
```

#### Caching

✅ **Cached** - TTL: 1 minute

---

### v_cache_stats

Get detailed cache statistics for monitoring and debugging.

#### Parameters

None

#### Request

```json
{
  "method": "tools/call",
  "params": {
    "name": "v_cache_stats",
    "arguments": {}
  }
}
```

#### Response

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"stats\": {\n    \"sessions\": 5,\n    \"aggregateStats\": {\n      \"totalHits\": 2341,\n      \"totalMisses\": 1122,\n      \"avgHitRate\": 0.68\n    }\n  },\n  \"metrics\": {\n    \"vos_cache_sessions_total\": 5,\n    \"vos_cache_hits_total\": 2341,\n    \"vos_cache_misses_total\": 1122,\n    \"vos_cache_hit_rate\": 0.68\n  },\n  \"config\": {\n    \"enabled\": true,\n    \"ttl\": 300000,\n    \"maxSize\": 1000\n  }\n}"
    }
  ]
}
```

#### Caching

❌ **Not Cached** - Always returns fresh statistics.

---

## Resources

### vos://agents/catalog

Complete catalog of available V OS agents.

#### URI

```
vos://agents/catalog
```

#### Read Request

```json
{
  "method": "resources/read",
  "params": {
    "uri": "vos://agents/catalog"
  }
}
```

#### Response

```json
{
  "contents": [
    {
      "uri": "vos://agents/catalog",
      "mimeType": "application/json",
      "text": "[{\"id\":\"vos-coder\",\"name\":\"V OS Coder\",\"description\":\"Expert software development agent\",\"capabilities\":[\"code\",\"debug\",\"refactor\",\"test\"],\"provider\":\"anthropic\",\"model\":\"claude-sonnet-4-20250514\"}]"
    }
  ]
}
```

---

### vos://config/capabilities

Server capabilities and feature flags.

#### URI

```
vos://config/capabilities
```

#### Response

```json
{
  "contents": [
    {
      "uri": "vos://config/capabilities",
      "mimeType": "application/json",
      "text": "{\"streaming\":true,\"multiAgent\":true,\"contextManagement\":true,\"providers\":[\"openai\",\"anthropic\",\"azure\",\"legoai\",\"local\"]}"
    }
  ]
}
```

---

## Prompts

### code-review

Generate a prompt for code review.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `code` | ✅ | The code to review |
| `language` | ❌ | Programming language |

#### Request

```json
{
  "method": "prompts/get",
  "params": {
    "name": "code-review",
    "arguments": {
      "code": "function add(a, b) { return a + b; }",
      "language": "javascript"
    }
  }
}
```

#### Response

```json
{
  "messages": [
    {
      "role": "user",
      "content": {
        "type": "text",
        "text": "Please review the following javascript code for:\n1. Best practices\n2. Potential bugs\n3. Performance issues\n4. Security concerns\n5. Suggestions for improvement\n\nCode:\n```javascript\nfunction add(a, b) { return a + b; }\n```"
      }
    }
  ]
}
```

---

### architecture-design

Generate a prompt for system architecture design.

#### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `requirements` | ✅ | System requirements |
| `scale` | ❌ | Expected scale (small/medium/large/enterprise) |

#### Request

```json
{
  "method": "prompts/get",
  "params": {
    "name": "architecture-design",
    "arguments": {
      "requirements": "Real-time chat application with 10K concurrent users",
      "scale": "large"
    }
  }
}
```

---

## Error Handling

### Error Response Format

```json
{
  "content": [
    {
      "type": "text",
      "text": "Error message description"
    }
  ],
  "isError": true
}
```

### Error Types

| Error | Description | Resolution |
|-------|-------------|------------|
| `UserError` | Invalid input or user error | Check parameters |
| `AuthError` | Authentication failed | Verify API key |
| `PermissionError` | Insufficient permissions | Check user permissions |
| `RateLimitError` | Rate limit exceeded | Wait and retry |
| `ProviderError` | LLM provider error | Check provider status |

### Example Error

```json
{
  "content": [
    {
      "type": "text",
      "text": "Agent \"invalid-id\" not found. Available: vos-coder, vos-architect, vos-researcher, vos-local"
    }
  ],
  "isError": true
}
```

---

## Rate Limiting

### Default Limits

| Endpoint | Limit | Window |
|----------|-------|--------|
| All Tools | 100 requests | 15 minutes |
| v_agent_chat | 20 requests | 1 minute |

### Rate Limit Headers

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1704067200
```

### Rate Limit Response

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json

{
  "error": "Too many requests",
  "retryAfter": 60
}
```

---

## SDK Examples

### TypeScript

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const client = new Client({ name: "my-app", version: "1.0.0" });

const transport = new StreamableHTTPClientTransport(
  new URL("http://localhost:8080/mcp"),
  { requestInit: { headers: { "x-api-key": "your-key" } } }
);

await client.connect(transport);

// List tools
const tools = await client.listTools();

// Call tool
const result = await client.callTool({
  name: "v_agent_chat",
  arguments: { message: "Hello!" }
});

// Read resource
const resource = await client.readResource({
  uri: "vos://agents/catalog"
});

// Get prompt
const prompt = await client.getPrompt({
  name: "code-review",
  arguments: { code: "...", language: "typescript" }
});
```

### Python

```python
from mcp import Client
from mcp.client.streamable_http import StreamableHTTPClientTransport

async with Client("my-app", "1.0.0") as client:
    transport = StreamableHTTPClientTransport(
        "http://localhost:8080/mcp",
        headers={"x-api-key": "your-key"}
    )
    await client.connect(transport)
    
    result = await client.call_tool(
        "v_agent_chat",
        {"message": "Hello!"}
    )
```

---

## Changelog

### v1.0.1 (2026-01-12)

- 🔒 Fixed CVE-2025-66414 (DNS Rebinding)
- ⚡ Added 3-layer caching
- 🤖 Added reasoning_effort support for OpenAI
- 📊 Added cache statistics tool

### v1.0.0 (2026-01-01)

- Initial release
- Multi-agent support
- Streaming responses
- Basic authentication

---

*Last Updated: January 2026*
