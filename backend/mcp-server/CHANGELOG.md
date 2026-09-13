# Changelog

All notable changes to V OS MCP Agent Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- WebSocket transport support
- Agent memory/conversation history
- Custom agent creation API
- Plugin system for tools
- Multi-tenant support

---

## [1.0.1] - 2026-01-12

### Security
- **CRITICAL**: Fixed CVE-2025-66414 (DNS Rebinding vulnerability)
  - Enabled `dnsRebindingProtection` by default
  - Added `allowedHosts` configuration
  - Updated to @modelcontextprotocol/sdk 1.25.2

### Added
- 3-layer caching system (Request → Session → Global/Redis)
  - L1: Request-scoped cache
  - L2: Session-scoped LRU cache with TTL
  - L3: Global cache with Redis support
- Stale-while-revalidate caching pattern
- `v_cache_stats` tool for monitoring cache performance
- Cache hit rate metrics for Prometheus
- `reasoning_effort` parameter support for OpenAI o1 models (PR #617 inspired)
- LM Studio / local model support via AugmentedLLM pattern (PR #622 inspired)
- Comprehensive security documentation (SECURITY.md)
- Operations runbook (docs/RUNBOOK.md)
- Kubernetes deployment manifests

### Changed
- Updated FastMCP to v3.26.8
  - List handler caching improvements
  - MCP spec 2025-11-25 sub-path discovery support
- Updated Zod to v4.0.0 for improved validation
- Improved error messages for agent selection
- Enhanced logging with structured JSON output

### Fixed
- Memory leak in streaming responses (inspired by fastmcp-python #2639)
- SSE priming events disabled for better client compatibility
- Body cancel handling in HTTP streaming (SDK #1372)

### Performance
- 40-60% cache hit rate on repeated queries
- 10x faster agent list responses (cached)
- Reduced memory footprint with LRU eviction

---

## [1.0.0] - 2026-01-01

### Added
- Initial release of V OS MCP Agent Server
- Multi-agent orchestration system
  - `vos-coder` - Software development agent (Anthropic Claude)
  - `vos-architect` - System design agent (OpenAI GPT-4o)
  - `vos-researcher` - Research agent (OpenAI o1)
  - `vos-local` - Local inference agent (LM Studio)
- Intelligent agent routing based on intent classification
- Real-time streaming responses with progress reporting
- Context management system
  - `v_context_add` - Add context to session
  - `v_context_clear` - Clear session context
- Core tools:
  - `v_agent_chat` - Chat with agents
  - `v_agent_list` - List available agents
  - `v_agent_select` - Select specific agent
  - `v_health` - Health check
- Resources:
  - `vos://agents/catalog` - Agent catalog
  - `vos://config/capabilities` - Server capabilities
- Prompts:
  - `code-review` - Code review template
  - `architecture-design` - Architecture design template
- HTTP streaming transport (Streamable HTTP)
- stdio transport for CLI integration
- API key authentication
- Per-tool authorization with `canAccess`
- Health check endpoint
- Prometheus metrics endpoint
- OpenTelemetry tracing support
- Docker support with security hardening
- Docker Compose for full stack deployment

### Multi-Provider Support
- OpenAI (GPT-4o, GPT-4o-mini, o1-preview)
- Anthropic (Claude Sonnet 4)
- Azure OpenAI
- Local models (LM Studio, Ollama)

### Documentation
- README.md with quick start guide
- API documentation
- Integration guide for Claude Desktop, Cursor, Cline
- Architecture documentation

---

## [0.1.0] - 2025-12-15 (Beta)

### Added
- Beta release for internal testing
- Basic MCP server implementation
- Single agent support
- HTTP transport only

### Known Issues
- No caching (fixed in 1.0.1)
- DNS rebinding vulnerability (fixed in 1.0.1)
- Limited error handling

---

## Version History

| Version | Release Date | Status |
|---------|--------------|--------|
| 1.0.1 | 2026-01-12 | Current |
| 1.0.0 | 2026-01-01 | Supported |
| 0.1.0 | 2025-12-15 | Deprecated |

---

## Migration Guides

### Migrating from 1.0.0 to 1.0.1

#### Required Changes

1. **Update dependencies:**
   ```bash
   npm update @modelcontextprotocol/sdk fastmcp zod
   ```

2. **Enable DNS protection (if not using defaults):**
   ```typescript
   // Before (vulnerable)
   const transport = new StreamableHTTPServerTransport({
     sessionIdGenerator: () => randomUUID(),
   });

   // After (secure)
   const transport = new StreamableHTTPServerTransport({
     sessionIdGenerator: () => randomUUID(),
     enableDnsRebindingProtection: true,  // Now default
     allowedHosts: ['127.0.0.1', 'localhost', 'your-domain.com'],
   });
   ```

3. **Update environment variables:**
   ```bash
   # New required variable
   ALLOWED_HOSTS=127.0.0.1,localhost,your-domain.com
   ```

#### Optional Changes

1. **Enable caching:**
   ```bash
   CACHE_ENABLED=true
   REDIS_URL=redis://localhost:6379  # Optional for distributed cache
   ```

2. **Enable reasoning_effort for o1 models:**
   ```typescript
   // In agent configuration
   providerOptions: {
     reasoningEffort: "high",  // New option
   }
   ```

### Migrating from 0.1.0 to 1.0.0

Complete rewrite - fresh installation recommended.

---

## Deprecation Notices

### Deprecated in 1.0.1
- None

### Removed in 1.0.1
- None

### Planned Deprecations
- SSE transport will be removed in 2.0.0 (use Streamable HTTP instead)

---

## Release Process

1. Update version in `package.json`
2. Update this CHANGELOG
3. Create git tag: `git tag -a v1.0.1 -m "Release 1.0.1"`
4. Push tag: `git push origin v1.0.1`
5. GitHub Actions builds and publishes:
   - npm package
   - Docker image to ghcr.io
6. Update documentation

---

[Unreleased]: https://github.com/v-os/mcp-agent-server/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/v-os/mcp-agent-server/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/v-os/mcp-agent-server/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/v-os/mcp-agent-server/releases/tag/v0.1.0
