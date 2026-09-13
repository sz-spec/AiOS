#!/usr/bin/env node
// v-os-mcp-agent-server/scripts/load-test.ts
// Load Testing Script for V OS MCP Agent Server Caching Validation

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

// ============================================================================
// CONFIGURATION
// ============================================================================

interface TestConfig {
  serverUrl: string;
  apiKey: string;
  userId: string;
  // Test parameters
  concurrency: number;
  duration: number; // seconds
  rampUpTime: number; // seconds
  // Scenarios
  scenarios: TestScenario[];
}

interface TestScenario {
  name: string;
  weight: number; // probability weight
  tool: string;
  args: Record<string, any>;
  expectedCached: boolean;
}

interface TestResult {
  scenario: string;
  success: boolean;
  latency: number;
  cached: boolean;
  error?: string;
  timestamp: number;
}

interface TestSummary {
  totalRequests: number;
  successfulRequests: number;
  failedRequests: number;
  avgLatency: number;
  p50Latency: number;
  p95Latency: number;
  p99Latency: number;
  minLatency: number;
  maxLatency: number;
  requestsPerSecond: number;
  cacheHitRate: number;
  byScenario: Record<string, ScenarioStats>;
}

interface ScenarioStats {
  count: number;
  avgLatency: number;
  cacheHits: number;
  errors: number;
}

// ============================================================================
// DEFAULT CONFIGURATION
// ============================================================================

const DEFAULT_CONFIG: TestConfig = {
  serverUrl: process.env.MCP_SERVER_URL || "http://localhost:8080/mcp",
  apiKey: process.env.MCP_API_KEY || "test-api-key",
  userId: process.env.MCP_USER_ID || "load-test-user",
  concurrency: parseInt(process.env.CONCURRENCY || "10"),
  duration: parseInt(process.env.DURATION || "60"),
  rampUpTime: parseInt(process.env.RAMP_UP || "10"),
  scenarios: [
    {
      name: "agent_list_cached",
      weight: 40,
      tool: "v_agent_list",
      args: {},
      expectedCached: true,
    },
    {
      name: "health_check",
      weight: 20,
      tool: "v_health",
      args: {},
      expectedCached: true,
    },
    {
      name: "cache_stats",
      weight: 10,
      tool: "v_cache_stats",
      args: {},
      expectedCached: false,
    },
    {
      name: "agent_chat_short",
      weight: 20,
      tool: "v_agent_chat",
      args: { message: "Hello, what can you do?", streaming: false },
      expectedCached: false,
    },
    {
      name: "agent_chat_code",
      weight: 10,
      tool: "v_agent_chat",
      args: { message: "Write a hello world function", streaming: false },
      expectedCached: false,
    },
  ],
};

// ============================================================================
// MCP CLIENT POOL
// ============================================================================

class MCPClientPool {
  private clients: Client[] = [];
  private transports: StreamableHTTPClientTransport[] = [];
  private available: Client[] = [];
  private waiting: Array<(client: Client) => void> = [];

  constructor(
    private config: TestConfig,
    private poolSize: number
  ) {}

  async initialize(): Promise<void> {
    console.log(`🔌 Initializing ${this.poolSize} MCP clients...`);

    for (let i = 0; i < this.poolSize; i++) {
      const transport = new StreamableHTTPClientTransport(
        new URL(this.config.serverUrl),
        {
          requestInit: {
            headers: {
              "x-api-key": this.config.apiKey,
              "x-user-id": `${this.config.userId}-${i}`,
            },
          },
        }
      );

      const client = new Client({
        name: `load-test-client-${i}`,
        version: "1.0.0",
      });

      await client.connect(transport);

      this.clients.push(client);
      this.transports.push(transport);
      this.available.push(client);
    }

    console.log(`✅ ${this.poolSize} clients connected`);
  }

  async acquire(): Promise<Client> {
    if (this.available.length > 0) {
      return this.available.pop()!;
    }

    return new Promise((resolve) => {
      this.waiting.push(resolve);
    });
  }

  release(client: Client): void {
    if (this.waiting.length > 0) {
      const resolve = this.waiting.shift()!;
      resolve(client);
    } else {
      this.available.push(client);
    }
  }

  async close(): Promise<void> {
    console.log("🔌 Closing client connections...");
    for (const transport of this.transports) {
      await transport.close();
    }
  }
}

// ============================================================================
// LOAD TEST RUNNER
// ============================================================================

class LoadTestRunner {
  private results: TestResult[] = [];
  private startTime: number = 0;
  private running: boolean = false;
  private activeRequests: number = 0;

  constructor(
    private config: TestConfig,
    private clientPool: MCPClientPool
  ) {}

  private selectScenario(): TestScenario {
    const totalWeight = this.config.scenarios.reduce((sum, s) => sum + s.weight, 0);
    let random = Math.random() * totalWeight;

    for (const scenario of this.config.scenarios) {
      random -= scenario.weight;
      if (random <= 0) {
        return scenario;
      }
    }

    return this.config.scenarios[0];
  }

  private async executeScenario(scenario: TestScenario): Promise<TestResult> {
    const client = await this.clientPool.acquire();
    const startTime = Date.now();

    try {
      const result = await client.callTool({
        name: scenario.tool,
        arguments: scenario.args,
      });

      const latency = Date.now() - startTime;

      // Check if response indicates cache hit (from cache stats or latency)
      const cached = latency < 20 && scenario.expectedCached;

      return {
        scenario: scenario.name,
        success: true,
        latency,
        cached,
        timestamp: startTime,
      };
    } catch (error) {
      return {
        scenario: scenario.name,
        success: false,
        latency: Date.now() - startTime,
        cached: false,
        error: error instanceof Error ? error.message : String(error),
        timestamp: startTime,
      };
    } finally {
      this.clientPool.release(client);
    }
  }

  private async runWorker(): Promise<void> {
    const endTime = this.startTime + this.config.duration * 1000;

    while (this.running && Date.now() < endTime) {
      // Ramp-up: gradually increase load
      const elapsed = Date.now() - this.startTime;
      const rampProgress = Math.min(1, elapsed / (this.config.rampUpTime * 1000));
      const targetConcurrency = Math.ceil(this.config.concurrency * rampProgress);

      if (this.activeRequests < targetConcurrency) {
        this.activeRequests++;

        const scenario = this.selectScenario();
        const result = await this.executeScenario(scenario);
        this.results.push(result);

        this.activeRequests--;
      } else {
        // Wait a bit if at capacity
        await new Promise((r) => setTimeout(r, 10));
      }
    }
  }

  async run(): Promise<TestSummary> {
    console.log("\n🚀 Starting load test...");
    console.log(`   Concurrency: ${this.config.concurrency}`);
    console.log(`   Duration: ${this.config.duration}s`);
    console.log(`   Ramp-up: ${this.config.rampUpTime}s`);
    console.log(`   Scenarios: ${this.config.scenarios.length}`);
    console.log("");

    this.running = true;
    this.startTime = Date.now();
    this.results = [];

    // Progress reporter
    const progressInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
      const rps = this.results.length / Math.max(1, elapsed);
      const errors = this.results.filter((r) => !r.success).length;
      const cacheHits = this.results.filter((r) => r.cached).length;

      process.stdout.write(
        `\r⏱️  ${elapsed}s | 📊 ${this.results.length} reqs | ⚡ ${rps.toFixed(1)} rps | ` +
        `✅ ${this.results.length - errors} ok | ❌ ${errors} err | 💾 ${cacheHits} cached`
      );
    }, 1000);

    // Run workers
    const workers = Array(this.config.concurrency)
      .fill(null)
      .map(() => this.runWorker());

    await Promise.all(workers);

    this.running = false;
    clearInterval(progressInterval);

    console.log("\n\n✅ Load test completed\n");

    return this.calculateSummary();
  }

  private calculateSummary(): TestSummary {
    const latencies = this.results
      .filter((r) => r.success)
      .map((r) => r.latency)
      .sort((a, b) => a - b);

    const percentile = (p: number) => {
      if (latencies.length === 0) return 0;
      const index = Math.ceil((p / 100) * latencies.length) - 1;
      return latencies[Math.max(0, index)];
    };

    const byScenario: Record<string, ScenarioStats> = {};
    for (const scenario of this.config.scenarios) {
      const scenarioResults = this.results.filter((r) => r.scenario === scenario.name);
      byScenario[scenario.name] = {
        count: scenarioResults.length,
        avgLatency:
          scenarioResults.length > 0
            ? scenarioResults.reduce((sum, r) => sum + r.latency, 0) / scenarioResults.length
            : 0,
        cacheHits: scenarioResults.filter((r) => r.cached).length,
        errors: scenarioResults.filter((r) => !r.success).length,
      };
    }

    const duration = (Date.now() - this.startTime) / 1000;

    return {
      totalRequests: this.results.length,
      successfulRequests: this.results.filter((r) => r.success).length,
      failedRequests: this.results.filter((r) => !r.success).length,
      avgLatency: latencies.length > 0 ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0,
      p50Latency: percentile(50),
      p95Latency: percentile(95),
      p99Latency: percentile(99),
      minLatency: latencies[0] || 0,
      maxLatency: latencies[latencies.length - 1] || 0,
      requestsPerSecond: this.results.length / duration,
      cacheHitRate:
        this.results.length > 0
          ? this.results.filter((r) => r.cached).length / this.results.length
          : 0,
      byScenario,
    };
  }
}

// ============================================================================
// REPORT GENERATOR
// ============================================================================

function printReport(summary: TestSummary): void {
  console.log("═".repeat(60));
  console.log("                    LOAD TEST REPORT");
  console.log("═".repeat(60));

  console.log("\n📊 OVERALL METRICS\n");
  console.log(`   Total Requests:     ${summary.totalRequests}`);
  console.log(`   Successful:         ${summary.successfulRequests} (${((summary.successfulRequests / summary.totalRequests) * 100).toFixed(1)}%)`);
  console.log(`   Failed:             ${summary.failedRequests} (${((summary.failedRequests / summary.totalRequests) * 100).toFixed(1)}%)`);
  console.log(`   Requests/sec:       ${summary.requestsPerSecond.toFixed(2)}`);

  console.log("\n⏱️  LATENCY (ms)\n");
  console.log(`   Average:            ${summary.avgLatency.toFixed(2)}`);
  console.log(`   Minimum:            ${summary.minLatency}`);
  console.log(`   Maximum:            ${summary.maxLatency}`);
  console.log(`   P50 (median):       ${summary.p50Latency}`);
  console.log(`   P95:                ${summary.p95Latency}`);
  console.log(`   P99:                ${summary.p99Latency}`);

  console.log("\n💾 CACHING\n");
  console.log(`   Cache Hit Rate:     ${(summary.cacheHitRate * 100).toFixed(1)}%`);

  console.log("\n📋 BY SCENARIO\n");
  console.log("   Scenario                      Count    Avg(ms)  Cache%   Errors");
  console.log("   " + "-".repeat(70));

  for (const [name, stats] of Object.entries(summary.byScenario)) {
    const cacheRate = stats.count > 0 ? ((stats.cacheHits / stats.count) * 100).toFixed(0) : "0";
    console.log(
      `   ${name.padEnd(30)} ${String(stats.count).padStart(6)}  ` +
      `${stats.avgLatency.toFixed(1).padStart(8)}  ${cacheRate.padStart(5)}%  ` +
      `${String(stats.errors).padStart(6)}`
    );
  }

  console.log("\n" + "═".repeat(60));

  // Cache effectiveness analysis
  console.log("\n🔍 CACHE EFFECTIVENESS ANALYSIS\n");

  const cachedScenarios = Object.entries(summary.byScenario).filter(
    ([name, stats]) => stats.cacheHits > 0
  );

  if (cachedScenarios.length > 0) {
    const avgCachedLatency =
      cachedScenarios.reduce((sum, [, s]) => sum + s.avgLatency * s.cacheHits, 0) /
      cachedScenarios.reduce((sum, [, s]) => sum + s.cacheHits, 0);

    const nonCachedResults = Object.values(summary.byScenario).filter(
      (s) => s.cacheHits === 0 && s.count > 0
    );
    const avgNonCachedLatency =
      nonCachedResults.length > 0
        ? nonCachedResults.reduce((sum, s) => sum + s.avgLatency * s.count, 0) /
          nonCachedResults.reduce((sum, s) => sum + s.count, 0)
        : 0;

    if (avgNonCachedLatency > 0) {
      const improvement = ((avgNonCachedLatency - avgCachedLatency) / avgNonCachedLatency) * 100;
      console.log(`   Cached avg latency:      ${avgCachedLatency.toFixed(2)}ms`);
      console.log(`   Non-cached avg latency:  ${avgNonCachedLatency.toFixed(2)}ms`);
      console.log(`   Improvement:             ${improvement.toFixed(1)}%`);
    }
  } else {
    console.log("   ⚠️  No cache hits detected - verify caching is enabled");
  }

  // Recommendations
  console.log("\n💡 RECOMMENDATIONS\n");

  if (summary.cacheHitRate < 0.3) {
    console.log("   ⚠️  Cache hit rate is low (<30%) - consider:");
    console.log("      • Increasing cache TTL");
    console.log("      • Reviewing cache key strategy");
    console.log("      • Enabling caching on more tools");
  }

  if (summary.p99Latency > 1000) {
    console.log("   ⚠️  P99 latency is high (>1s) - consider:");
    console.log("      • Optimizing slow tools");
    console.log("      • Increasing concurrency limits");
    console.log("      • Adding more caching layers");
  }

  if (summary.failedRequests > summary.totalRequests * 0.01) {
    console.log("   ❌ Error rate is high (>1%) - investigate:");
    console.log("      • Server logs for errors");
    console.log("      • Connection pool exhaustion");
    console.log("      • Rate limiting");
  }

  console.log("\n" + "═".repeat(60) + "\n");
}

function exportJson(summary: TestSummary, filename: string): void {
  const fs = require("fs");
  fs.writeFileSync(filename, JSON.stringify(summary, null, 2));
  console.log(`📄 Results exported to ${filename}`);
}

// ============================================================================
// MAIN
// ============================================================================

async function main(): Promise<void> {
  console.log("╔════════════════════════════════════════════════════════╗");
  console.log("║     V OS MCP Agent Server - Load Testing Suite         ║");
  console.log("╚════════════════════════════════════════════════════════╝\n");

  // Parse command line arguments
  const args = process.argv.slice(2);
  const config: TestConfig = { ...DEFAULT_CONFIG };

  for (const arg of args) {
    const [key, value] = arg.replace("--", "").split("=");
    switch (key) {
      case "url":
        config.serverUrl = value;
        break;
      case "concurrency":
      case "c":
        config.concurrency = parseInt(value);
        break;
      case "duration":
      case "d":
        config.duration = parseInt(value);
        break;
      case "ramp-up":
        config.rampUpTime = parseInt(value);
        break;
    }
  }

  // Initialize client pool
  const clientPool = new MCPClientPool(config, config.concurrency);

  try {
    await clientPool.initialize();

    // Run load test
    const runner = new LoadTestRunner(config, clientPool);
    const summary = await runner.run();

    // Print report
    printReport(summary);

    // Export results
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    exportJson(summary, `load-test-results-${timestamp}.json`);

    // Exit with error code if too many failures
    if (summary.failedRequests > summary.totalRequests * 0.05) {
      console.error("❌ Test failed: Error rate > 5%");
      process.exit(1);
    }
  } catch (error) {
    console.error("❌ Load test failed:", error);
    process.exit(1);
  } finally {
    await clientPool.close();
  }
}

// Usage instructions
if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`
Usage: npx tsx scripts/load-test.ts [options]

Options:
  --url=<url>           MCP server URL (default: http://localhost:8080/mcp)
  --concurrency=<n>     Number of concurrent clients (default: 10)
  --duration=<seconds>  Test duration in seconds (default: 60)
  --ramp-up=<seconds>   Ramp-up time in seconds (default: 10)

Environment Variables:
  MCP_SERVER_URL        Server URL
  MCP_API_KEY           API key for authentication
  MCP_USER_ID           User ID prefix
  CONCURRENCY           Number of concurrent clients
  DURATION              Test duration

Examples:
  npx tsx scripts/load-test.ts
  npx tsx scripts/load-test.ts --concurrency=50 --duration=120
  MCP_SERVER_URL=http://prod:8080/mcp npx tsx scripts/load-test.ts
`);
  process.exit(0);
}

main().catch(console.error);
