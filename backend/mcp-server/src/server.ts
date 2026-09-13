// src/server-with-cost-optimization.ts
// V OS MCP Agent Server with Full Cost Optimization

import { FastMCP } from "fastmcp";
import { z } from "zod";
import { randomUUID } from "crypto";
import fs from "node:fs";
import path from "node:path";

// Cost optimization imports
import { CostAwareRouter, MODEL_REGISTRY, type RoutingDecision } from "./orchestration/cost-aware-router";
import { SemanticCache, createSemanticCache } from "./framework/caching/semantic-cache";
import { QuotaManager, quotaManager, type QuotaCheckResult } from "./framework/quotas/quota-manager";
import { CostTracker, costTracker } from "./observability/cost-tracker";

// SmartRouter integration
import { SmartRouterBridge, createSmartRouter, type SmartRouterModel } from "./orchestration/smart-router-bridge";

// ============================================================================
// CONFIGURATION
// ============================================================================

const config = {
  port: parseInt(process.env.PORT || "8080"),
  
  // API Keys
  openaiApiKey: process.env.OPENAI_API_KEY || "",
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || "",
  
  // Cost Optimization
  costOptimization: {
    enabled: process.env.COST_OPTIMIZATION_ENABLED !== "false",
    smartRouting: process.env.SMART_ROUTING_ENABLED !== "false",
    semanticCache: process.env.SEMANTIC_CACHE_ENABLED !== "false",
    quotasEnabled: process.env.QUOTAS_ENABLED !== "false",
    maxCostPerRequest: parseFloat(process.env.MAX_COST_PER_REQUEST || "0.50"),
    preferLocal: process.env.PREFER_LOCAL === "true",
  },
  
  // Security
  allowedHosts: (process.env.ALLOWED_HOSTS || "127.0.0.1,localhost").split(","),
};

// ============================================================================
// INITIALIZE COMPONENTS
// ============================================================================

// Cost-aware router (legacy, kept for fallback)
const costRouter = new CostAwareRouter({
  preferLocal: config.costOptimization.preferLocal,
  maxCostPerRequest: config.costOptimization.maxCostPerRequest,
});

// SmartRouter - primary router for all LLM calls
const smartRouter = createSmartRouter({
  bridgePath: process.env.SMART_ROUTER_BRIDGE_PATH,
  pythonPath: process.env.PYTHON_PATH || "python3",
});

// Semantic cache (requires OpenAI API key for embeddings)
const semanticCache = config.openaiApiKey
  ? createSemanticCache(config.openaiApiKey, {
      similarityThreshold: 0.92,
      ttlMs: 24 * 60 * 60 * 1000, // 24 hours
      maxCacheSize: 10000,
      enabled: config.costOptimization.semanticCache,
    })
  : null;

// Simple hash-based cache (works without API key)
const simpleCache = new Map<string, { response: string; model: string; timestamp: number }>();
const SIMPLE_CACHE_TTL = 60 * 60 * 1000; // 1 hour

function getSimpleCacheKey(message: string): string {
  // Simple hash for exact match caching
  return message.trim().toLowerCase();
}

// ============================================================================
// PERSISTENT CACHE
// ============================================================================

const CACHE_FILE = path.join(process.cwd(), "cache", "simple-cache.json");

function loadCache(): void {
  try {
    if (fs.existsSync(CACHE_FILE)) {
      const data = fs.readFileSync(CACHE_FILE, "utf8");
      const parsed = JSON.parse(data) as Record<string, { response: string; model: string; timestamp: number }>;

      // Filter out expired entries while loading
      const now = Date.now();
      let loaded = 0;
      let expired = 0;

      for (const [key, value] of Object.entries(parsed)) {
        if (now - value.timestamp < SIMPLE_CACHE_TTL) {
          simpleCache.set(key, value);
          loaded++;
        } else {
          expired++;
        }
      }

      console.log(`📂 Loaded ${loaded} cache entries (${expired} expired entries skipped)`);
    }
  } catch (err) {
    console.error("❌ Failed to load cache file:", err);
  }
}

function saveCache(): void {
  try {
    // Ensure cache directory exists
    const cacheDir = path.dirname(CACHE_FILE);
    if (!fs.existsSync(cacheDir)) {
      fs.mkdirSync(cacheDir, { recursive: true });
    }

    const data = JSON.stringify(Object.fromEntries(simpleCache), null, 2);
    fs.writeFileSync(CACHE_FILE, data, "utf8");
  } catch (err) {
    console.error("❌ Failed to save cache file:", err);
  }
}

// Load cache on startup
loadCache();

// ============================================================================
// MCP SERVER
// ============================================================================

const mcp = new FastMCP({
  name: "v-os-mcp-cost-optimized",
  version: "1.1.0",
});

// ============================================================================
// TOOL: v_agent_chat (with cost optimization)
// ============================================================================

mcp.addTool({
  name: "v_agent_chat",
  description: "Chat with V OS AI agent - cost-optimized with smart routing and caching",
  parameters: z.object({
    message: z.string().min(1).max(10000).describe("The message to send"),
    agentId: z.string().optional().describe("Specific agent ID (auto-routes if omitted)"),
    forceModel: z.string().optional().describe("Force specific model (bypasses cost optimization)"),
    streaming: z.boolean().default(true).describe("Enable streaming response"),
  }),
  annotations: {
    title: "V OS Agent Chat (Cost Optimized)",
    streamingHint: true,
    readOnlyHint: false,
  },
  execute: async (args, { reportProgress }) => {
    const startTime = Date.now();
    const requestId = randomUUID();
    const userId = "default-user"; // In production, get from session
    
    try {
      // ========================================
      // Step 1: Check user quota
      // ========================================
      if (config.costOptimization.quotasEnabled) {
        const estimatedTokens = Math.ceil(args.message.length / 4) * 3; // Rough estimate
        const estimatedCost = 0.01; // Rough estimate for quota check
        
        const quotaCheck = await quotaManager.checkQuota(userId, {
          tokens: estimatedTokens,
          cost: estimatedCost,
        });
        
        if (!quotaCheck.allowed) {
          return {
            content: [
              {
                type: "text" as const,
                text: `⚠️ Quota exceeded: ${quotaCheck.reason}\n\nYour quota will reset at ${new Date(quotaCheck.resetAt?.daily || 0).toLocaleString()}`,
              },
            ],
            isError: true,
          };
        }
        
        // Warn if approaching limit
        if (quotaCheck.percentUsed && quotaCheck.percentUsed.daily > 0.8) {
          reportProgress({ progress: 0, total: 100 });
          console.log(`⚠️ User ${userId} at ${(quotaCheck.percentUsed.daily * 100).toFixed(1)}% of daily quota`);
        }
      }
      
      // ========================================
      // Step 2: Check semantic cache
      // ========================================
      if (semanticCache && config.costOptimization.semanticCache) {
        reportProgress({ progress: 10, total: 100 });
        
        const cacheResult = await semanticCache.get(args.message);
        
        if (cacheResult.hit && cacheResult.entry) {
          // Cache HIT - return cached response
          const latency = Date.now() - startTime;
          
          // Track the cached request
          costTracker.track({
            requestId,
            userId,
            model: cacheResult.entry.model,
            provider: "cache",
            tokens: { ...cacheResult.entry.tokens, total: (cacheResult.entry.tokens?.input || 0) + (cacheResult.entry.tokens?.output || 0) },
            cost: 0, // Free!
            cached: true,
            timestamp: Date.now(),
            latencyMs: latency,
          });
          
          console.log(`💾 Cache HIT (${(cacheResult.similarity! * 100).toFixed(1)}% similar) - Saved $${cacheResult.savedCost?.toFixed(4)}`);
          
          const cacheMetaInfo = `\n\n---\n💾 Cache HIT | Similarity: ${(cacheResult.similarity! * 100).toFixed(1)}% | Saved: $${cacheResult.savedCost?.toFixed(4)} | Latency: ${latency}ms`;

          return {
            content: [
              {
                type: "text" as const,
                text: cacheResult.entry.response + cacheMetaInfo,
              },
            ],
          };
        }
      }
      
      // ========================================
      // Step 2b: Check simple cache (fallback when no semantic cache)
      // ========================================
      if (!semanticCache) {
        const cacheKey = getSimpleCacheKey(args.message);
        const cached = simpleCache.get(cacheKey);

        if (cached && (Date.now() - cached.timestamp) < SIMPLE_CACHE_TTL) {
          const latency = Date.now() - startTime;
          console.log(`💾 Simple Cache HIT - Latency: ${latency}ms`);

          return {
            content: [
              {
                type: "text" as const,
                text: cached.response + `\n\n---\n💾 Simple Cache HIT | Model: ${cached.model} | Latency: ${latency}ms`,
              },
            ],
          };
        }
      }

      // ========================================
      // Step 3: Smart model routing via SmartRouter
      // ========================================
      reportProgress({ progress: 20, total: 100 });

      let routingDecision: RoutingDecision;
      let smartRouterResult: { model: SmartRouterModel; role: string; complexity: number } | null = null;

      if (config.costOptimization.smartRouting && !args.forceModel) {
        // Use SmartRouter for intelligent routing
        try {
          smartRouterResult = await smartRouter.routeMessage(args.message);

          // Map SmartRouter result to RoutingDecision format
          const modelId = smartRouterResult.model.name;
          const tier = smartRouterResult.complexity >= 9 ? "complex" :
                       smartRouterResult.complexity >= 5 ? "medium" : "simple";

          // Use MODEL_REGISTRY if available, otherwise create from SmartRouter
          const modelConfig = MODEL_REGISTRY[modelId] || {
            id: smartRouterResult.model.name,
            provider: smartRouterResult.model.provider as "openai" | "anthropic" | "local",
            model: smartRouterResult.model.model_id,
            costPer1MInput: 3.0, // Default estimate
            costPer1MOutput: 15.0,
            maxTokens: smartRouterResult.model.max_tokens,
            capabilities: ["chat", "code"],
          };

          routingDecision = {
            model: modelConfig,
            tier,
            estimatedCost: 0.01, // Will be calculated later
            reason: `smartrouter_${smartRouterResult.role}_c${smartRouterResult.complexity}`,
            confidence: 0.9,
          };

          console.log(`🎯 SmartRouter: role=${smartRouterResult.role}, complexity=${smartRouterResult.complexity}/10 → ${modelConfig.model}`);
        } catch (error) {
          console.error("SmartRouter failed, falling back to CostAwareRouter:", error);
          routingDecision = await costRouter.route(args.message, {
            forceModel: args.forceModel,
          });
          console.log(`🎯 Fallback Routing: ${routingDecision.tier} → ${routingDecision.model.id}`);
        }
      } else {
        // Default to GPT-4o if routing disabled
        routingDecision = {
          model: MODEL_REGISTRY["gpt-4o"] || MODEL_REGISTRY["gpt-4o-mini"],
          tier: "medium",
          estimatedCost: 0.01,
          reason: "routing_disabled",
          confidence: 1.0,
        };
      }
      
      // ========================================
      // Step 4: Call LLM
      // ========================================
      reportProgress({ progress: 30, total: 100 });
      
      // Call the actual LLM API based on SmartRouter decision
      const actualModelId = smartRouterResult?.model.model_id || routingDecision.model.model;
      const actualProvider = smartRouterResult?.model.provider || routingDecision.model.provider;

      console.log(`🤖 MCP following Router: Using ${actualModelId} (${actualProvider})`);

      const response = await callLLM(
        actualModelId,
        args.message,
        actualProvider
      );
      
      reportProgress({ progress: 90, total: 100 });
      
      // ========================================
      // Step 5: Calculate actual cost & track
      // ========================================
      const actualCost = costTracker.calculateCost(
        routingDecision.model.id,
        response.inputTokens,
        response.outputTokens
      );
      
      costTracker.track({
        requestId,
        userId,
        model: routingDecision.model.id,
        provider: routingDecision.model.provider,
        tokens: {
          input: response.inputTokens,
          output: response.outputTokens,
          total: response.inputTokens + response.outputTokens,
        },
        cost: actualCost,
        cached: false,
        timestamp: Date.now(),
        latencyMs: Date.now() - startTime,
        routingDecision: routingDecision.reason,
      });
      
      // Record quota usage
      if (config.costOptimization.quotasEnabled) {
        quotaManager.recordUsage(userId, {
          tokens: response.inputTokens + response.outputTokens,
          cost: actualCost,
          model: routingDecision.model.id,
          timestamp: Date.now(),
        });
      }
      
      // ========================================
      // Step 6: Cache for future use
      // ========================================
      if (semanticCache && config.costOptimization.semanticCache) {
        await semanticCache.set(args.message, response.text, {
          model: routingDecision.model.id,
          inputTokens: response.inputTokens,
          outputTokens: response.outputTokens,
          cost: actualCost,
        });
      } else {
        // Use simple cache when semantic cache is not available
        const cacheKey = getSimpleCacheKey(args.message);
        simpleCache.set(cacheKey, {
          response: response.text,
          model: routingDecision.model.id,
          timestamp: Date.now(),
        });
        saveCache(); // Persist to disk
        console.log(`💾 Saved to simple cache (key: ${cacheKey.substring(0, 30)}...)`);
      }
      
      reportProgress({ progress: 100, total: 100 });
      
      console.log(`✅ Request completed: ${routingDecision.model.id}, $${actualCost.toFixed(4)}, ${Date.now() - startTime}ms`);
      
      // Include metadata in response text for visibility
      const metaInfo = `\n\n---\n📊 Model: ${routingDecision.model.id} | Cost: $${actualCost.toFixed(4)} | Tokens: ${response.inputTokens + response.outputTokens} | Latency: ${Date.now() - startTime}ms`;

      return {
        content: [
          {
            type: "text" as const,
            text: response.text + metaInfo,
          },
        ],
      };
      
    } catch (error) {
      console.error("Chat error:", error);
      return {
        content: [
          {
            type: "text" as const,
            text: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
          },
        ],
        isError: true,
      };
    }
  },
});

// ============================================================================
// TOOL: v_cost_report
// ============================================================================

mcp.addTool({
  name: "v_cost_report",
  description: "Get cost report and optimization statistics",
  parameters: z.object({
    format: z.enum(["summary", "detailed", "json"]).default("summary"),
  }),
  execute: async (args) => {
    const todayStats = costTracker.getTodayStats();
    const projection = costTracker.getProjection();
    const cacheStats = semanticCache?.getStats();
    
    if (args.format === "json") {
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({
              today: todayStats,
              projection,
              cache: cacheStats,
              topSpenders: costTracker.getTopSpenders(5),
              modelBreakdown: costTracker.getModelBreakdown(),
            }, null, 2),
          },
        ],
      };
    }
    
    if (args.format === "detailed") {
      return {
        content: [
          {
            type: "text" as const,
            text: costTracker.generateReport(),
          },
        ],
      };
    }
    
    // Summary format
    const cacheHitRate = cacheStats ? (cacheStats.hitRate * 100).toFixed(1) : "N/A";
    const cacheSavings = cacheStats ? cacheStats.totalSavedCost.toFixed(2) : "0.00";
    
    return {
      content: [
        {
          type: "text" as const,
          text: `📊 **Cost Summary**

**Today's Spend:** $${todayStats?.totalCost.toFixed(2) || "0.00"}
**Projected Daily:** $${projection.projectedDailyCost.toFixed(2)}
**Projected Monthly:** $${projection.projectedMonthlyCost.toFixed(2)}

**Optimization Stats:**
- Cache Hit Rate: ${cacheHitRate}%
- Cache Savings: $${cacheSavings}
- Smart Routing: ${config.costOptimization.smartRouting ? "✅ Enabled" : "❌ Disabled"}

**Trend:** ${projection.trend} (${projection.trendPercent > 0 ? "+" : ""}${projection.trendPercent.toFixed(1)}%)`,
        },
      ],
    };
  },
});

// ============================================================================
// TOOL: v_quota_status
// ============================================================================

mcp.addTool({
  name: "v_quota_status",
  description: "Check your quota usage and limits",
  parameters: z.object({
    userId: z.string().optional().describe("User ID to check (admin only)"),
  }),
  execute: async (args) => {
    const userId = args.userId || "default-user";
    const usage = quotaManager.getUsage(userId);
    const quota = quotaManager.getQuota(userId);
    
    const dailyPercent = (usage.daily.cost / quota.daily.cost * 100).toFixed(1);
    const monthlyPercent = (usage.monthly.cost / quota.monthly.cost * 100).toFixed(1);
    
    return {
      content: [
        {
          type: "text" as const,
          text: `📈 **Quota Status for ${userId}**

**Daily Usage:**
- Tokens: ${usage.daily.tokens.toLocaleString()} / ${quota.daily.tokens.toLocaleString()}
- Requests: ${usage.daily.requests} / ${quota.daily.requests}
- Cost: $${usage.daily.cost.toFixed(2)} / $${quota.daily.cost.toFixed(2)} (${dailyPercent}%)

**Monthly Usage:**
- Tokens: ${usage.monthly.tokens.toLocaleString()} / ${quota.monthly.tokens.toLocaleString()}
- Requests: ${usage.monthly.requests} / ${quota.monthly.requests}
- Cost: $${usage.monthly.cost.toFixed(2)} / $${quota.monthly.cost.toFixed(2)} (${monthlyPercent}%)

**All Time:**
- Total Tokens: ${usage.allTime.tokens.toLocaleString()}
- Total Requests: ${usage.allTime.requests.toLocaleString()}
- Total Spend: $${usage.allTime.cost.toFixed(2)}`,
        },
      ],
    };
  },
});

// ============================================================================
// TOOL: v_set_quota
// ============================================================================

mcp.addTool({
  name: "v_set_quota",
  description: "Set quota tier for a user (admin only)",
  parameters: z.object({
    userId: z.string().describe("User ID"),
    tier: z.enum(["free", "pro", "enterprise"]).describe("Quota tier"),
  }),
  execute: async (args) => {
    quotaManager.setQuota(args.userId, args.tier);
    
    return {
      content: [
        {
          type: "text" as const,
          text: `✅ Quota set to **${args.tier}** for user ${args.userId}`,
        },
      ],
    };
  },
});

// ============================================================================
// TOOL: v_cache_stats
// ============================================================================

mcp.addTool({
  name: "v_cache_stats",
  description: "Get semantic cache statistics",
  parameters: z.object({}),
  execute: async () => {
    if (!semanticCache) {
      return {
        content: [
          {
            type: "text" as const,
            text: "⚠️ Semantic cache is not enabled (missing OPENAI_API_KEY)",
          },
        ],
      };
    }
    
    const stats = semanticCache.getStats();
    
    return {
      content: [
        {
          type: "text" as const,
          text: `💾 **Semantic Cache Stats**

**Entries:** ${stats.totalEntries.toLocaleString()}
**Hit Rate:** ${(stats.hitRate * 100).toFixed(1)}%
**Total Hits:** ${stats.hits.toLocaleString()}
**Total Misses:** ${stats.misses.toLocaleString()}

**Savings:**
- Total Saved: $${stats.totalSavedCost.toFixed(2)}
- Avg Similarity: ${(stats.avgSimilarity * 100).toFixed(1)}%

**Cache Age:**
- Oldest: ${stats.oldestEntry ? new Date(stats.oldestEntry).toLocaleString() : "N/A"}
- Newest: ${stats.newestEntry ? new Date(stats.newestEntry).toLocaleString() : "N/A"}`,
        },
      ],
    };
  },
});

// ============================================================================
// TOOL: v_health (updated with cost info)
// ============================================================================

mcp.addTool({
  name: "v_health",
  description: "Server health and cost optimization status",
  parameters: z.object({}),
  execute: async () => {
    const projection = costTracker.getProjection();
    const cacheStats = semanticCache?.getStats();
    
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({
            status: "healthy",
            version: "1.1.0",
            timestamp: new Date().toISOString(),
            costOptimization: {
              enabled: config.costOptimization.enabled,
              smartRouting: config.costOptimization.smartRouting,
              semanticCache: config.costOptimization.semanticCache,
              quotas: config.costOptimization.quotasEnabled,
            },
            metrics: {
              projectedDailyCost: projection.projectedDailyCost,
              projectedMonthlyCost: projection.projectedMonthlyCost,
              cacheHitRate: cacheStats?.hitRate || 0,
              cacheSavings: cacheStats?.totalSavedCost || 0,
            },
          }, null, 2),
        },
      ],
    };
  },
});

// ============================================================================
// MOCK LLM CALL (replace with actual implementation)
// ============================================================================

async function callLLM(
  model: string,
  message: string,
  provider: string
): Promise<{ text: string; inputTokens: number; outputTokens: number }> {
  const startTime = Date.now();

  try {
    if (provider === "anthropic") {
      // Claude API call
      const response = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": config.anthropicApiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: model, // e.g., "claude-5-sonnet-20260115" or "claude-4-1-opus-20251022"
          max_tokens: 4096,
          messages: [{ role: "user", content: message }],
        }),
      });

      const data = await response.json() as {
        content?: Array<{ text: string }>;
        usage?: { input_tokens: number; output_tokens: number };
        error?: { message: string };
      };

      if (data.error) {
        throw new Error(`Anthropic API error: ${data.error.message}`);
      }

      return {
        text: data.content?.[0]?.text || "",
        inputTokens: data.usage?.input_tokens || Math.ceil(message.length / 4),
        outputTokens: data.usage?.output_tokens || 0,
      };

    } else if (provider === "openai") {
      // OpenAI API call - use Responses API for pro models
      const isProModel = model.includes("-pro");
      const endpoint = isProModel
        ? "https://api.openai.com/v1/responses"
        : "https://api.openai.com/v1/chat/completions";

      const body = isProModel
        ? {
            model: model,
            input: message,
            max_output_tokens: 4096,
          }
        : {
            model: model,
            max_completion_tokens: 4096,
            messages: [{ role: "user", content: message }],
          };

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${config.openaiApiKey}`,
        },
        body: JSON.stringify(body),
      });

      const data = await response.json() as {
        // Chat completions format
        choices?: Array<{ message: { content: string } }>;
        // Responses API format
        output?: Array<{ content: Array<{ text: string }> }>;
        output_text?: string;
        usage?: { prompt_tokens?: number; completion_tokens?: number; input_tokens?: number; output_tokens?: number };
        error?: { message: string };
      };

      if (data.error) {
        throw new Error(`OpenAI API error: ${data.error.message}`);
      }

      // Handle both response formats
      const text = isProModel
        ? (data.output_text || data.output?.[0]?.content?.[0]?.text || "")
        : (data.choices?.[0]?.message?.content || "");

      return {
        text,
        inputTokens: data.usage?.prompt_tokens || data.usage?.input_tokens || Math.ceil(message.length / 4),
        outputTokens: data.usage?.completion_tokens || data.usage?.output_tokens || 0,
      };

    } else if (provider === "google") {
      // Google Gemini API call
      const googleApiKey = process.env.GOOGLE_API_KEY || "";
      const response = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${googleApiKey}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contents: [{ parts: [{ text: message }] }],
          }),
        }
      );

      const data = await response.json() as {
        candidates?: Array<{ content: { parts: Array<{ text: string }> } }>;
        usageMetadata?: { promptTokenCount: number; candidatesTokenCount: number };
        error?: { message: string };
      };

      if (data.error) {
        throw new Error(`Google API error: ${data.error.message}`);
      }

      return {
        text: data.candidates?.[0]?.content?.parts?.[0]?.text || "",
        inputTokens: data.usageMetadata?.promptTokenCount || Math.ceil(message.length / 4),
        outputTokens: data.usageMetadata?.candidatesTokenCount || 0,
      };

    } else {
      // Fallback for unknown providers
      throw new Error(`Unknown provider: ${provider}`);
    }
  } catch (error) {
    console.error(`LLM call failed (${provider}/${model}):`, error);
    throw error;
  }
}

// ============================================================================
// START SERVER
// ============================================================================

async function main() {
  console.log(`
╔════════════════════════════════════════════════════════════╗
║       V OS MCP Agent Server (Cost Optimized)               ║
╠════════════════════════════════════════════════════════════╣
║  Version:        1.1.0                                     ║
║  Port:           ${config.port.toString().padEnd(43)}║
║                                                            ║
║  Cost Optimization:                                        ║
║  • Smart Routing:   ${(config.costOptimization.smartRouting ? "✅ Enabled" : "❌ Disabled").padEnd(37)}║
║  • Semantic Cache:  ${(config.costOptimization.semanticCache ? "✅ Enabled" : "❌ Disabled").padEnd(37)}║
║  • User Quotas:     ${(config.costOptimization.quotasEnabled ? "✅ Enabled" : "❌ Disabled").padEnd(37)}║
║  • Max Cost/Req:    $${config.costOptimization.maxCostPerRequest.toFixed(2).padEnd(35)}║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
`);

  // Setup quota alert handler
  quotaManager.on("alert", (alert) => {
    console.log(`🚨 Quota Alert: ${alert.userId} - ${alert.type} (${alert.percentUsed.toFixed(1)}%)`);
  });

  // Setup cost alert handler
  costTracker.on("alert", (alert) => {
    console.log(`💰 Cost Alert: ${alert.message} - Current: $${alert.currentCost.toFixed(2)}`);
  });

  // Start server
  await mcp.start({
    transportType: "httpStream",
    httpStream: {
      port: config.port,
      // Note: DNS rebinding protection handled at reverse proxy level
      // allowedHosts: config.allowedHosts,
    },
  });

  console.log(`\n🚀 Server running at http://localhost:${config.port}/mcp\n`);

  // Log hourly cost summary
  setInterval(() => {
    const report = costTracker.generateReport();
    console.log(report);
  }, 60 * 60 * 1000); // Every hour
}

main().catch(console.error);
