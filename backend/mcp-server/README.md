# V OS MCP Agent Server

<div align="center">

![V OS Logo](https://img.shields.io/badge/V%20OS-MCP%20Agent%20Server-blue?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZmlsbD0id2hpdGUiIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6TTIgMTdsMTAgNSAxMC01TTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+)

**Production-grade Multi-Agent MCP Server with Streaming, Caching & Observability**

[![SDK Version](https://img.shields.io/badge/MCP%20SDK-1.25.2-green)](https://github.com/modelcontextprotocol/typescript-sdk)
[![FastMCP](https://img.shields.io/badge/FastMCP-3.26.8-blue)](https://github.com/punkpeye/fastmcp)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Security](https://img.shields.io/badge/CVE--2025--66414-Patched-success)](SECURITY.md)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue)](https://www.typescriptlang.org/)

[Features](#-features) •
[Quick Start](#-quick-start) •
[Documentation](#-documentation) •
[Integration](#-integration) •
[API Reference](#-api-reference)

</div>

---

## 📖 Overview

V OS MCP Agent Server הוא שרת [Model Context Protocol](https://modelcontextprotocol.io/) מתקדם המספק:

- 🤖 **Multi-Agent Orchestration** - ניתוב חכם בין agents מרובים
- ⚡ **Real-time Streaming** - תשובות streaming עם progress reporting
- 💾 **3-Layer Caching** - ביצועים משופרים עם cache hit rate של 40-60%
- 🔒 **Enterprise Security** - DNS rebinding protection, OAuth, rate limiting
- 📊 **Full Observability** - OpenTelemetry tracing, Prometheus metrics
- 🔌 **Multi-Provider** - OpenAI, Anthropic, Azure, Local models

### ארכיטקטורה

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP CLIENTS                               │
│  (Cline, Cursor, Claude Desktop, VS Code, Custom)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              V OS MCP AGENT SERVER                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Agent     │  │  Streaming  │  │    3-Layer Cache    │  │
│  │   Router    │  │   Engine    │  │  (L1→L2→L3/Redis)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   OpenAI    │  │  Anthropic  │  │   Azure / Local     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

### Multi-Agent System

| Agent | תיאור | Provider | Capabilities |
|-------|--------|----------|--------------|
| **vos-coder** | Software development expert | Anthropic Claude | code, debug, refactor, test |
| **vos-architect** | System design specialist | OpenAI GPT-4o | architecture, design, planning |
| **vos-researcher** | Deep research & analysis | OpenAI o1 | research, analysis, summarize |
| **vos-local** | Privacy-focused local inference | LM Studio | code, chat, translate |

### Intelligent Routing

```typescript
// Automatic intent-based routing
"Write a function..." → vos-coder (Anthropic)
"Design a system..." → vos-architect (OpenAI)
"Research the topic..." → vos-researcher (o1)
```

### Performance

| Metric | Without Cache | With Cache |
|--------|---------------|------------|
| Agent List Latency | ~100ms | ~10ms |
| Cache Hit Rate | 0% | 40-60% |
| Requests/sec | ~50 | ~200+ |

---

## 🚀 Quick Start

### Prerequisites

- Node.js 22+
- npm or pnpm
- (Optional) Docker & Docker Compose
- (Optional) Redis for distributed caching

### Installation

```bash
# Clone the repository
git clone https://github.com/v-os/mcp-agent-server.git
cd mcp-agent-server

# Install dependencies
npm install

# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env

# Start development server
npm run dev
```

### Docker Quick Start

```bash
# Build and run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f mcp-server

# Check health
curl http://localhost:8080/health
```

### Verify Installation

```bash
# Health check
curl http://localhost:8080/health

# Expected response:
{
  "status": "healthy",
  "version": "1.0.1",
  "security": {
    "dnsRebindingProtection": true,
    "cveFixed": ["CVE-2025-66414"]
  },
  "agents": 4
}
```

---

## 📚 Documentation

| Document | תיאור |
|----------|--------|
| [API Reference](docs/API.md) | תיעוד מלא של כל ה-Tools, Resources, Prompts |
| [Integration Guide](docs/INTEGRATION.md) | חיבור מ-Cline, Cursor, Claude Desktop |
| [Architecture](docs/ARCHITECTURE.md) | ארכיטקטורה מפורטת |
| [Security](SECURITY.md) | מדיניות אבטחה ו-CVE handling |
| [Runbook](docs/RUNBOOK.md) | תפעול ו-troubleshooting |
| [Contributing](CONTRIBUTING.md) | הנחיות לתורמים |

---

## 🔌 Integration

### Claude Desktop

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "v-os": {
      "command": "npx",
      "args": ["-y", "v-os-mcp-agent-server"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

### Cursor

```json
// .cursor/mcp.json
{
  "mcpServers": {
    "v-os": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "your-api-key"
      }
    }
  }
}
```

### Cline (VS Code)

```json
// settings.json
{
  "cline.mcpServers": {
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

### Programmatic (TypeScript)

```typescript
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const client = new Client({ name: "my-app", version: "1.0.0" });

const transport = new StreamableHTTPClientTransport(
  new URL("http://localhost:8080/mcp"),
  {
    requestInit: {
      headers: { "x-api-key": "your-api-key" }
    }
  }
);

await client.connect(transport);

// Chat with agent
const result = await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Write a hello world in TypeScript",
    streaming: true
  }
});
```

📖 **Full integration guide:** [docs/INTEGRATION.md](docs/INTEGRATION.md)

---

## 🛠️ API Reference

### Tools

| Tool | Description | Cached |
|------|-------------|--------|
| `v_agent_chat` | Chat with AI agent (streaming) | ❌ |
| `v_agent_list` | List available agents | ✅ 10min |
| `v_agent_select` | Select specific agent | ❌ |
| `v_context_add` | Add context to session | ❌ |
| `v_context_clear` | Clear session context | ❌ |
| `v_health` | Server health & status | ✅ 1min |
| `v_cache_stats` | Cache statistics | ❌ |

### Example: Chat with Agent

```typescript
// Basic chat
await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Explain microservices architecture",
    agentId: "vos-architect", // Optional: auto-routes if omitted
    streaming: true
  }
});

// With context
await client.callTool({
  name: "v_context_add",
  arguments: {
    key: "project",
    value: "E-commerce platform with 1M users",
    type: "requirements"
  }
});

await client.callTool({
  name: "v_agent_chat",
  arguments: {
    message: "Design the database schema"
  }
});
```

📖 **Full API documentation:** [docs/API.md](docs/API.md)

---

## ⚙️ Configuration

### Environment Variables

```bash
# Server
PORT=8080
NODE_ENV=production

# Security
ALLOWED_HOSTS=127.0.0.1,localhost
ALLOWED_ORIGINS=https://your-app.com

# Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
AZURE_OPENAI_ENDPOINT=https://...
LM_STUDIO_ENDPOINT=http://localhost:1234

# Cache
CACHE_ENABLED=true
REDIS_URL=redis://localhost:6379

# Observability
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

📖 **Full configuration:** [.env.example](.env.example)

---

## 🔒 Security

### CVE Status

| CVE | Status | Description |
|-----|--------|-------------|
| CVE-2025-66414 | ✅ Patched | DNS Rebinding Protection |

### Security Features

- ✅ DNS Rebinding Protection (enabled by default)
- ✅ API Key Authentication
- ✅ Per-tool Authorization
- ✅ Rate Limiting
- ✅ Input Validation (Zod v4)
- ✅ Non-root Docker container
- ✅ Read-only filesystem

📖 **Security policy:** [SECURITY.md](SECURITY.md)

---

## 📊 Monitoring

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /metrics` | Prometheus metrics |

### Grafana Dashboards

```bash
# Start full monitoring stack
docker-compose up -d

# Access dashboards
open http://localhost:3001  # Grafana (admin/admin)
open http://localhost:16686 # Jaeger Tracing
open http://localhost:9090  # Prometheus
```

### Key Metrics

```
vos_cache_hits_total        - Total cache hits
vos_cache_misses_total      - Total cache misses
vos_cache_hit_rate          - Cache hit rate (0-1)
vos_request_latency_ms      - Request latency histogram
vos_agent_routes_total      - Agent routing decisions
vos_tokens_used_total       - LLM tokens consumed
```

---

## 🧪 Testing

### Unit Tests

```bash
npm test
npm run test:coverage
```

### Load Testing

```bash
# Run load test
npm run load-test

# With custom parameters
npx tsx scripts/load-test.ts --concurrency=50 --duration=120

# Using Docker
docker-compose --profile test up load-test
```

### Expected Results

```
═══════════════════════════════════════════════════
                 LOAD TEST REPORT
═══════════════════════════════════════════════════

📊 OVERALL METRICS
   Total Requests:     5000
   Successful:         4985 (99.7%)
   Requests/sec:       83.33

⏱️  LATENCY (ms)
   Average:            45.23
   P50 (median):       12
   P95:                89
   P99:                156

💾 CACHING
   Cache Hit Rate:     47.3%
```

---

## 🚢 Deployment

### Docker

```bash
# Build production image
docker build -t v-os-mcp-server:latest .

# Run container
docker run -d \
  -p 8080:8080 \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  v-os-mcp-server:latest
```

### Kubernetes

```bash
# Apply manifests
kubectl apply -f k8s/

# Check status
kubectl get pods -l app=v-os-mcp-server
```

### Cloud Providers

| Provider | Guide |
|----------|-------|
| AWS ECS | [docs/deploy/aws.md](docs/deploy/aws.md) |
| Google Cloud Run | [docs/deploy/gcp.md](docs/deploy/gcp.md) |
| Azure Container Apps | [docs/deploy/azure.md](docs/deploy/azure.md) |

---

## 📦 Project Structure

```
v-os-mcp-agent-server/
├── src/
│   ├── server.ts                 # Main server entry
│   ├── framework/
│   │   ├── tools/                # Tool definitions
│   │   ├── caching/              # 3-layer cache
│   │   └── auth/                 # Authentication
│   ├── orchestration/
│   │   ├── router/               # Agent routing
│   │   ├── providers/            # LLM providers
│   │   └── streaming/            # Stream handling
│   └── observability/            # Metrics & tracing
├── scripts/
│   └── load-test.ts              # Load testing
├── docs/                         # Documentation
├── k8s/                          # Kubernetes manifests
├── monitoring/                   # Prometheus/Grafana
├── Dockerfile                    # Production image
├── docker-compose.yml            # Full stack
└── package.json
```

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Development setup
git clone https://github.com/v-os/mcp-agent-server.git
cd mcp-agent-server
npm install
npm run dev

# Run tests before submitting PR
npm test
npm run lint
npm run typecheck
```

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- [Model Context Protocol](https://modelcontextprotocol.io/) - Anthropic
- [FastMCP](https://github.com/punkpeye/fastmcp) - punkpeye
- [lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent) - Workflow patterns inspiration

---

<div align="center">

**Built with ❤️ by V OS Team**

[Website](https://v-os.ai) •
[Documentation](https://docs.v-os.ai) •
[Discord](https://discord.gg/v-os) •
[Twitter](https://twitter.com/v_os_ai)

</div>
