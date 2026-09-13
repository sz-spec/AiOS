# Contributing to V OS MCP Agent Server

First off, thank you for considering contributing to V OS MCP Agent Server! 🎉

This document provides guidelines and information for contributors. Following these guidelines helps maintainers and the community understand your contribution and makes the review process smoother.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Issue Guidelines](#issue-guidelines)
- [Community](#community)

---

## Code of Conduct

This project and everyone participating in it is governed by our Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to conduct@v-os.ai.

### Our Standards

- Be respectful and inclusive
- Accept constructive criticism gracefully
- Focus on what's best for the community
- Show empathy towards other community members

---

## Getting Started

### Prerequisites

- Node.js 22 or higher
- npm or pnpm
- Git
- Docker (optional, for integration testing)
- An IDE with TypeScript support (VS Code recommended)

### Quick Setup

```bash
# 1. Fork the repository on GitHub

# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/mcp-agent-server.git
cd mcp-agent-server

# 3. Add upstream remote
git remote add upstream https://github.com/v-os/mcp-agent-server.git

# 4. Install dependencies
npm install

# 5. Copy environment file
cp .env.example .env.local

# 6. Start development server
npm run dev
```

---

## How to Contribute

### Types of Contributions

We welcome many types of contributions:

| Type | Description | Label |
|------|-------------|-------|
| 🐛 Bug fixes | Fix something that's broken | `bug` |
| ✨ Features | Add new functionality | `enhancement` |
| 📚 Documentation | Improve docs, examples | `documentation` |
| 🧪 Tests | Add or improve tests | `testing` |
| ⚡ Performance | Improve speed/efficiency | `performance` |
| 🔒 Security | Fix vulnerabilities | `security` |
| ♻️ Refactoring | Code improvements | `refactor` |

### Good First Issues

New to the project? Look for issues labeled:
- `good first issue` - Simple, well-defined tasks
- `help wanted` - We need community help
- `documentation` - Often easier to start with

### Feature Requests

Before working on a large feature:

1. Check [existing issues](https://github.com/v-os/mcp-agent-server/issues) to avoid duplicates
2. Open a [feature request issue](https://github.com/v-os/mcp-agent-server/issues/new?template=feature_request.md)
3. Wait for discussion and approval before starting work

---

## Development Setup

### Project Structure

```
v-os-mcp-agent-server/
├── src/
│   ├── server.ts              # Main entry point
│   ├── framework/
│   │   ├── tools/             # Tool implementations
│   │   ├── caching/           # Caching system
│   │   └── auth/              # Authentication
│   ├── orchestration/
│   │   ├── router/            # Agent routing
│   │   ├── providers/         # LLM providers
│   │   └── streaming/         # Stream handling
│   └── observability/         # Metrics & tracing
├── tests/
│   ├── unit/                  # Unit tests
│   └── integration/           # Integration tests
├── docs/                      # Documentation
├── scripts/                   # Build/deploy scripts
└── k8s/                       # Kubernetes manifests
```

### Available Scripts

```bash
# Development
npm run dev          # Start dev server with hot reload
npm run build        # Build for production
npm run start        # Start production server

# Testing
npm test             # Run all tests
npm run test:unit    # Run unit tests only
npm run test:int     # Run integration tests
npm run test:cov     # Run with coverage

# Code Quality
npm run lint         # Run ESLint
npm run lint:fix     # Fix linting issues
npm run typecheck    # TypeScript type checking
npm run format       # Format with Prettier

# Other
npm run load-test    # Run load tests
npm run inspect      # Open MCP Inspector
```

### Environment Configuration

Create `.env.local` for development:

```bash
# Required for testing LLM features
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Development settings
NODE_ENV=development
LOG_LEVEL=debug
MOCK_LLM=true  # Use mock responses (no API key needed)
```

---

## Coding Standards

### TypeScript Guidelines

```typescript
// ✅ DO: Use explicit types
function processMessage(message: string): Promise<AgentResponse> {
  // ...
}

// ❌ DON'T: Use `any`
function processMessage(message: any): any {
  // ...
}

// ✅ DO: Use interfaces for objects
interface AgentConfig {
  id: string;
  name: string;
  capabilities: string[];
}

// ✅ DO: Use enums for fixed values
enum AgentProvider {
  OpenAI = "openai",
  Anthropic = "anthropic",
  Local = "local",
}

// ✅ DO: Use async/await
async function fetchData(): Promise<Data> {
  const response = await fetch(url);
  return response.json();
}

// ❌ DON'T: Use callbacks
function fetchData(callback: (data: Data) => void) {
  fetch(url).then(r => r.json()).then(callback);
}
```

### File Naming

```
# Files
my-component.ts        # kebab-case for files
MyClass.ts             # PascalCase for classes (optional)
index.ts               # Barrel exports

# Tests
my-component.test.ts   # Test files
my-component.spec.ts   # Alternative test naming
```

### Code Organization

```typescript
// 1. Imports (grouped)
import { FastMCP } from "fastmcp";              // External packages
import { z } from "zod";

import { AgentRouter } from "./orchestration";  // Internal imports
import { CacheManager } from "./caching";

import type { AgentConfig } from "./types";     // Type imports

// 2. Constants
const DEFAULT_TIMEOUT = 30000;

// 3. Types/Interfaces
interface ToolResult {
  content: Content[];
  isError?: boolean;
}

// 4. Main implementation
export class MyClass {
  // ...
}

// 5. Helper functions (private)
function helper() {
  // ...
}
```

### Documentation

```typescript
/**
 * Routes messages to the appropriate agent based on intent.
 * 
 * @param message - The user message to route
 * @param context - Optional routing context
 * @returns The selected agent specification
 * 
 * @example
 * ```typescript
 * const agent = await router.route("Write a function", {
 *   preferredAgent: "vos-coder"
 * });
 * ```
 */
async function route(
  message: string,
  context?: RoutingContext
): Promise<AgentSpec> {
  // ...
}
```

### Error Handling

```typescript
// ✅ DO: Use custom error classes
class AgentNotFoundError extends Error {
  constructor(agentId: string) {
    super(`Agent "${agentId}" not found`);
    this.name = "AgentNotFoundError";
  }
}

// ✅ DO: Provide helpful error messages
if (!agent) {
  throw new AgentNotFoundError(agentId);
}

// ✅ DO: Handle errors gracefully
try {
  const result = await executeTask();
} catch (error) {
  if (error instanceof AgentNotFoundError) {
    return { error: error.message, suggestions: listAgents() };
  }
  throw error; // Re-throw unknown errors
}
```

---

## Testing

### Test Structure

```typescript
// tests/unit/router.test.ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { AgentRouter } from "../../src/orchestration/router";

describe("AgentRouter", () => {
  let router: AgentRouter;

  beforeEach(() => {
    router = new AgentRouter(mockAgents);
  });

  describe("route()", () => {
    it("should route coding questions to vos-coder", async () => {
      const agent = await router.route("Write a function to sort an array");
      expect(agent.id).toBe("vos-coder");
    });

    it("should respect explicit agent selection", async () => {
      const agent = await router.route("Hello", {
        preferredAgent: "vos-architect"
      });
      expect(agent.id).toBe("vos-architect");
    });

    it("should throw for invalid agent ID", async () => {
      await expect(
        router.route("Hello", { preferredAgent: "invalid" })
      ).rejects.toThrow(AgentNotFoundError);
    });
  });
});
```

### Test Categories

| Category | Location | Command | Purpose |
|----------|----------|---------|---------|
| Unit | `tests/unit/` | `npm run test:unit` | Test isolated functions |
| Integration | `tests/integration/` | `npm run test:int` | Test component interaction |
| E2E | `tests/e2e/` | `npm run test:e2e` | Test full workflows |

### Coverage Requirements

- Minimum coverage: 80%
- New features must include tests
- Bug fixes should include regression tests

```bash
# Check coverage
npm run test:cov

# Coverage report in coverage/index.html
```

### Mocking

```typescript
// Mock LLM provider
vi.mock("../../src/orchestration/providers", () => ({
  MultiProviderManager: vi.fn().mockImplementation(() => ({
    chat: vi.fn().mockReturnValue(
      (async function* () {
        yield "Mock response";
      })()
    ),
  })),
}));
```

---

## Pull Request Process

### Before Submitting

1. **Update your fork:**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Create a feature branch:**
   ```bash
   git checkout -b feature/my-feature
   # or
   git checkout -b fix/bug-description
   ```

3. **Make your changes** following coding standards

4. **Run checks:**
   ```bash
   npm run lint
   npm run typecheck
   npm test
   ```

5. **Commit with conventional commits:**
   ```bash
   git commit -m "feat: add new caching layer"
   git commit -m "fix: resolve memory leak in streaming"
   git commit -m "docs: update API documentation"
   ```

### Commit Message Format

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat` - New feature
- `fix` - Bug fix
- `docs` - Documentation
- `style` - Formatting (no code change)
- `refactor` - Code restructuring
- `perf` - Performance improvement
- `test` - Adding tests
- `chore` - Maintenance

**Examples:**
```
feat(router): add capability-based routing

Add new routing strategy that matches message intent
to agent capabilities.

Closes #123
```

```
fix(cache): prevent memory leak in session cache

Clear expired entries on session close to prevent
memory accumulation.

Fixes #456
```

### PR Template

When you open a PR, please fill out the template:

```markdown
## Description
[What does this PR do?]

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## How Has This Been Tested?
[Describe testing approach]

## Checklist
- [ ] My code follows the project's coding standards
- [ ] I have added tests that prove my fix/feature works
- [ ] All new and existing tests pass
- [ ] I have updated the documentation
- [ ] I have updated the CHANGELOG.md
```

### Review Process

1. **Automated checks** must pass (CI/CD)
2. **Code review** by at least one maintainer
3. **Approval** from a maintainer
4. **Merge** via squash and merge

### After Merge

- Delete your feature branch
- Update your local main:
  ```bash
  git checkout main
  git pull upstream main
  ```

---

## Issue Guidelines

### Bug Reports

Use the bug report template and include:

- **Description:** Clear description of the bug
- **Steps to reproduce:** Detailed steps
- **Expected behavior:** What should happen
- **Actual behavior:** What actually happens
- **Environment:** OS, Node version, etc.
- **Logs/Screenshots:** If applicable

### Feature Requests

Use the feature request template and include:

- **Problem:** What problem does this solve?
- **Solution:** Your proposed solution
- **Alternatives:** Other solutions considered
- **Additional context:** Any other information

### Security Issues

**Do NOT open public issues for security vulnerabilities!**

Email security@v-os.ai with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

See [SECURITY.md](SECURITY.md) for our security policy.

---

## Community

### Getting Help

- 💬 [Discord](https://discord.gg/v-os) - Chat with the community
- 📧 [Email](mailto:support@v-os.ai) - For private inquiries
- 🐦 [Twitter](https://twitter.com/v_os_ai) - Updates and announcements

### Recognition

Contributors are recognized in:
- [CONTRIBUTORS.md](CONTRIBUTORS.md)
- Release notes
- Our website's contributors page

### Maintainers

| Name | Role | GitHub |
|------|------|--------|
| V OS Team | Core | @v-os |

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing! 🚀
