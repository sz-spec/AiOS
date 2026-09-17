// v-os-mcp-agent-server/src/server.ts
// V OS MCP Agent Server - Hybrid Architecture Implementation

import { FastMCP, UserError } from "fastmcp";
import { z } from "zod";

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

interface VSessionData {
  userId: string;
  permissions: string[];
  activeAgentId?: string;
  context: Map<string, unknown>;
  headers: Record<string, string>;
}

interface AgentSpec {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  provider: "openai" | "anthropic" | "azure" | "legoai" | "local";
  model?: string;
  systemPrompt?: string;
}

// ============================================================================
// CONFIGURATION
// ============================================================================

const AGENTS: AgentSpec[] = [
  {
    id: "vos-coder",
    name: "V OS Coder",
    description: "Expert software development agent",
    capabilities: ["code", "debug", "refactor", "test"],
    provider: "anthropic",
    model: "claude-sonnet-4-20250514",
  },
  {
    id: "vos-architect",
    name: "V OS Architect",
    description: "System design and architecture specialist",
    capabilities: ["architecture", "design", "planning"],
    provider: "openai",
    model: "gpt-4o",
  },
  {
    id: "vos-researcher",
    name: "V OS Researcher",
    description: "Research and analysis agent",
    capabilities: ["research", "analysis", "summarize"],
    provider: "anthropic",
    model: "claude-sonnet-4-20250514",
  },
  {
    id: "niseko-concierge",
    name: "Niseko AI Concierge",
    description: "Ski resort guest services specialist",
    capabilities: ["hospitality", "local-info", "bookings"],
    provider: "legoai",
  },
];

// ============================================================================
// LAYER 3: ORCHESTRATION COMPONENTS
// ============================================================================

/**
 * Agent Router - Routes requests to the optimal agent
 */
class AgentRouter {
  private agents: Map<string, AgentSpec>;

  constructor(agents: AgentSpec[]) {
    this.agents = new Map(agents.map((a) => [a.id, a]));
  }

  async route(
    message: string,
    context: { preferredAgent?: string; capabilities?: string[] }
  ): Promise<AgentSpec> {
    // Priority 1: Explicit agent selection
    if (context.preferredAgent && this.agents.has(context.preferredAgent)) {
      return this.agents.get(context.preferredAgent)!;
    }

    // Priority 2: Capability matching
    if (context.capabilities?.length) {
      for (const [_, agent] of this.agents) {
        const matches = context.capabilities.every((cap) =>
          agent.capabilities.includes(cap)
        );
        if (matches) return agent;
      }
    }

    // Priority 3: Intent classification (simplified)
    const intent = this.classifyIntent(message);
    const matched = Array.from(this.agents.values()).find((a) =>
      a.capabilities.includes(intent)
    );

    return matched || this.agents.get("vos-coder")!;
  }

  private classifyIntent(message: string): string {
    const lower = message.toLowerCase();
    if (lower.includes("code") || lower.includes("function") || lower.includes("implement"))
      return "code";
    if (lower.includes("architecture") || lower.includes("design") || lower.includes("system"))
      return "architecture";
    if (lower.includes("research") || lower.includes("find") || lower.includes("search"))
      return "research";
    if (lower.includes("ski") || lower.includes("niseko") || lower.includes("hotel"))
      return "hospitality";
    return "code";
  }

  getAgent(id: string): AgentSpec | undefined {
    return this.agents.get(id);
  }

  listAgents(): AgentSpec[] {
    return Array.from(this.agents.values());
  }
}

/**
 * Multi-Provider Manager - Unified interface for LLM providers
 */
class MultiProviderManager {
  async *chat(
    provider: string,
    model: string,
    _messages: Array<{ role: string; content: string }>,
    _systemPrompt?: string
  ): AsyncGenerator<string> {
    // In production, this would call actual APIs
    // For now, simulating streaming response
    const response = `[${provider}/${model}] Processing your request...`;
    
    for (const char of response) {
      yield char;
      await new Promise((r) => setTimeout(r, 20));
    }
  }
}

/**
 * Context Manager - Manages conversation context
 */
class ContextManager {
  private contexts: Map<string, Map<string, unknown>> = new Map();

  getContext(sessionId: string): Map<string, unknown> {
    if (!this.contexts.has(sessionId)) {
      this.contexts.set(sessionId, new Map());
    }
    return this.contexts.get(sessionId)!;
  }

  addToContext(sessionId: string, key: string, value: unknown): void {
    this.getContext(sessionId).set(key, value);
  }

  clearContext(sessionId: string): void {
    this.contexts.delete(sessionId);
  }
}

// ============================================================================
// LAYER 2: FASTMCP SERVER SETUP
// ============================================================================

// Initialize orchestration components
const router = new AgentRouter(AGENTS);
const providerManager = new MultiProviderManager();
const contextManager = new ContextManager();

// Create FastMCP server
const server = new FastMCP<VSessionData>({
  name: "V OS MCP Agent Server",
  version: "1.0.0",
  instructions: `
V OS MCP Agent Server provides access to multiple AI agents for various tasks.

Available agents:
${AGENTS.map((a) => `- ${a.name}: ${a.description}`).join("\n")}

Use v_agent_chat for conversations, v_agent_select to switch agents,
and v_context_add to provide additional context for better responses.
  `.trim(),

  // Authentication
  authenticate: async (request) => {
    const apiKey = request.headers["x-api-key"] as string;
    const userId = request.headers["x-user-id"] as string;

    // In production: validate against auth service
    if (!apiKey) {
      throw new Response(null, { status: 401, statusText: "Missing API key" });
    }

    return {
      userId: userId || "anonymous",
      permissions: ["chat", "context", "agents"],
      context: new Map(),
      headers: request.headers as Record<string, string>,
    };
  },

  // Health checks
  health: {
    enabled: true,
    path: "/health",
    message: "V OS MCP Agent Server is healthy",
  },

  // Ping configuration
  ping: {
    enabled: true,
    intervalMs: 10000,
    logLevel: "debug",
  },
});

// ============================================================================
// TOOLS REGISTRATION
// ============================================================================

// --- Chat Tool (with streaming) ---
server.addTool({
  name: "v_agent_chat",
  description: "Send a message to a V OS agent and receive a streaming response",
  parameters: z.object({
    message: z.string().describe("The message to send to the agent"),
    agentId: z.string().optional().describe("Specific agent ID (auto-routes if not provided)"),
    streaming: z.boolean().default(true).describe("Enable streaming response"),
  }),
  annotations: {
    title: "V OS Agent Chat",
    streamingHint: true,
    readOnlyHint: false,
  },
  canAccess: (auth) => auth?.permissions.includes("chat") ?? false,
  execute: async (args, { session, streamContent, reportProgress, log }) => {
    const sessionId = session?.userId || "default";

    // Route to appropriate agent
    const agent = await router.route(args.message, {
      preferredAgent: args.agentId,
    });

    log.info(`Routing to agent: ${agent.name}`, { agentId: agent.id });

    await reportProgress({ progress: 0, total: 100 });

    // Get context
    const context = contextManager.getContext(sessionId);
    const contextStr = context.size > 0
      ? `\nContext: ${JSON.stringify(Object.fromEntries(context))}`
      : "";

    // Build messages
    const messages = [
      { role: "user", content: args.message + contextStr },
    ];

    await reportProgress({ progress: 20, total: 100 });

    // Stream response
    if (args.streaming) {
      for await (const chunk of providerManager.chat(
        agent.provider,
        agent.model || "default",
        messages,
        agent.systemPrompt
      )) {
        await streamContent({ type: "text", text: chunk });
      }

      await reportProgress({ progress: 100, total: 100 });

      return {
        content: [{ type: "text", text: `Response from ${agent.name} completed.` }],
      };
    }

    // Non-streaming response
    let fullResponse = "";
    for await (const chunk of providerManager.chat(
      agent.provider,
      agent.model || "default",
      messages,
      agent.systemPrompt
    )) {
      fullResponse += chunk;
    }

    await reportProgress({ progress: 100, total: 100 });

    return fullResponse;
  },
});

// --- Agent Selection Tool ---
server.addTool({
  name: "v_agent_select",
  description: "Select a specific agent for the current session",
  parameters: z.object({
    agentId: z.string().describe("The ID of the agent to select"),
  }),
  annotations: {
    title: "Select V OS Agent",
  },
  canAccess: (auth) => auth?.permissions.includes("agents") ?? false,
  execute: async (args, { log }) => {
    const agent = router.getAgent(args.agentId);

    if (!agent) {
      throw new UserError(
        `Agent "${args.agentId}" not found. Available: ${router
          .listAgents()
          .map((a) => a.id)
          .join(", ")}`
      );
    }

    log.info(`Agent selected: ${agent.name}`);

    return {
      content: [
        {
          type: "text",
          text: `Selected agent: ${agent.name}\n\nCapabilities: ${agent.capabilities.join(", ")}\nProvider: ${agent.provider}`,
        },
      ],
    };
  },
});

// --- List Agents Tool ---
server.addTool({
  name: "v_agent_list",
  description: "List all available V OS agents and their capabilities",
  parameters: z.object({}),
  annotations: {
    title: "List V OS Agents",
    readOnlyHint: true,
  },
  execute: async () => {
    const agents = router.listAgents();

    const list = agents
      .map(
        (a) =>
          `**${a.name}** (${a.id})\n  ${a.description}\n  Capabilities: ${a.capabilities.join(", ")}\n  Provider: ${a.provider}`
      )
      .join("\n\n");

    return `# Available V OS Agents\n\n${list}`;
  },
});

// --- Context Management Tools ---
server.addTool({
  name: "v_context_add",
  description: "Add context information for the current session",
  parameters: z.object({
    key: z.string().describe("Context key (e.g., 'project', 'requirements', 'file')"),
    value: z.string().describe("Context value"),
    type: z.enum(["text", "file", "requirements", "codebase"]).default("text"),
  }),
  annotations: {
    title: "Add Context",
  },
  canAccess: (auth) => auth?.permissions.includes("context") ?? false,
  execute: async (args, { log }) => {
    const sessionId = session?.userId || "default";

    contextManager.addToContext(sessionId, args.key, {
      type: args.type,
      value: args.value,
      addedAt: new Date().toISOString(),
    });

    log.info(`Context added: ${args.key}`, { type: args.type });

    return `Context "${args.key}" added successfully.`;
  },
});

server.addTool({
  name: "v_context_clear",
  description: "Clear all context for the current session",
  parameters: z.object({}),
  annotations: {
    title: "Clear Context",
  },
  canAccess: (auth) => auth?.permissions.includes("context") ?? false,
  execute: async (args, { log }) => {
    const sessionId = session?.userId || "default";
    contextManager.clearContext(sessionId);
    log.info("Context cleared");
    return "Session context cleared.";
  },
});

// --- Health Check Tool ---
server.addTool({
  name: "v_health_check",
  description: "Check the health status of V OS MCP Agent Server",
  parameters: z.object({}),
  annotations: {
    title: "Health Check",
    readOnlyHint: true,
  },
  execute: async () => {
    const agents = router.listAgents();

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              status: "healthy",
              version: "1.0.0",
              agents: {
                total: agents.length,
                available: agents.map((a) => a.id),
              },
              timestamp: new Date().toISOString(),
            },
            null,
            2
          ),
        },
      ],
    };
  },
});

// ============================================================================
// RESOURCES REGISTRATION
// ============================================================================

server.addResource({
  uri: "vos://agents/catalog",
  name: "Agent Catalog",
  description: "Complete catalog of available V OS agents",
  mimeType: "application/json",
  async load() {
    return {
      text: JSON.stringify(router.listAgents(), null, 2),
    };
  },
});

server.addResource({
  uri: "vos://config/capabilities",
  name: "Server Capabilities",
  description: "V OS MCP Agent Server capabilities",
  mimeType: "application/json",
  async load() {
    return {
      text: JSON.stringify(
        {
          streaming: true,
          multiAgent: true,
          contextManagement: true,
          providers: ["openai", "anthropic", "azure", "legoai", "local"],
        },
        null,
        2
      ),
    };
  },
});

// ============================================================================
// PROMPTS REGISTRATION
// ============================================================================

server.addPrompt({
  name: "code-review",
  description: "Review code for best practices, bugs, and improvements",
  arguments: [
    {
      name: "code",
      description: "The code to review",
      required: true,
    },
    {
      name: "language",
      description: "Programming language",
      required: false,
    },
  ],
  load: async ({ code, language }) => ({
    messages: [
      {
        role: "user",
        content: {
          type: "text",
          text: `Please review the following ${language || ""} code for:
1. Best practices
2. Potential bugs
3. Performance issues
4. Security concerns
5. Suggestions for improvement

Code:
\`\`\`${language || ""}
${code}
\`\`\``,
        },
      },
    ],
  }),
});

server.addPrompt({
  name: "architecture-design",
  description: "Design system architecture for a given requirement",
  arguments: [
    {
      name: "requirements",
      description: "System requirements",
      required: true,
    },
    {
      name: "scale",
      description: "Expected scale (small/medium/large/enterprise)",
      required: false,
      enum: ["small", "medium", "large", "enterprise"],
    },
  ],
  load: async ({ requirements, scale }) => ({
    messages: [
      {
        role: "user",
        content: {
          type: "text",
          text: `Design a system architecture for the following requirements:

Requirements:
${requirements}

Scale: ${scale || "medium"}

Please provide:
1. High-level architecture diagram (in text/ASCII)
2. Component breakdown
3. Technology recommendations
4. Data flow description
5. Scalability considerations
6. Security considerations`,
        },
      },
    ],
  }),
});

// ============================================================================
// SERVER STARTUP
// ============================================================================

// Export for different transport modes
export { server };

// HTTP Streaming mode (default)
if (process.env.TRANSPORT !== "stdio") {
  server.start({
    transportType: "httpStream",
    httpStream: {
      port: parseInt(process.env.PORT || "8080"),
    },
  });

  console.log(`
╔═══════════════════════════════════════════════════════════╗
║           V OS MCP Agent Server - Started                 ║
╠═══════════════════════════════════════════════════════════╣
║  Transport: HTTP Stream                                   ║
║  Port: ${process.env.PORT || "8080"}                                              ║
║  Endpoint: http://localhost:${process.env.PORT || "8080"}/mcp                     ║
║  Health: http://localhost:${process.env.PORT || "8080"}/health                    ║
║                                                           ║
║  Agents: ${AGENTS.length} available                                       ║
║  Tools: 6 registered                                      ║
║  Resources: 2 available                                   ║
║  Prompts: 2 defined                                       ║
╚═══════════════════════════════════════════════════════════╝
  `);
} else {
  // stdio mode for CLI integration
  server.start({
    transportType: "stdio",
  });
}
