// src/observability/cost-tracker.ts
// Real-time Cost Tracking & Monitoring

import { EventEmitter } from "events";

// ============================================================================
// TYPES
// ============================================================================

export interface CostRecord {
  requestId: string;
  userId: string;
  model: string;
  provider: string;
  tokens: {
    input: number;
    output: number;
    total: number;
  };
  cost: number;
  cached: boolean;
  timestamp: number;
  latencyMs: number;
  routingDecision?: string;
}

export interface DailyStats {
  date: string; // YYYY-MM-DD
  totalCost: number;
  totalTokens: number;
  totalRequests: number;
  cachedRequests: number;
  savingsFromCache: number;
  byModel: Record<string, { cost: number; tokens: number; requests: number }>;
  byUser: Record<string, { cost: number; tokens: number; requests: number }>;
  byHour: number[]; // 24 hourly costs
}

export interface AlertConfig {
  threshold: number;
  level: "info" | "warning" | "critical";
  message: string;
  notified: boolean;
}

export interface CostProjection {
  currentDailyRate: number;
  projectedDailyCost: number;
  projectedMonthlyCost: number;
  trend: "increasing" | "stable" | "decreasing";
  trendPercent: number;
}

// ============================================================================
// MODEL PRICING (as of Jan 2026)
// ============================================================================

export const MODEL_PRICING: Record<string, { input: number; output: number }> = {
  // OpenAI
  "gpt-4o": { input: 2.50, output: 10.00 },
  "gpt-4o-mini": { input: 0.15, output: 0.60 },
  "o1-preview": { input: 15.00, output: 60.00 },
  "o1-mini": { input: 3.00, output: 12.00 },
  "gpt-4-turbo": { input: 10.00, output: 30.00 },
  
  // Anthropic
  "claude-opus-4-20250514": { input: 15.00, output: 75.00 },
  "claude-sonnet-4-20250514": { input: 3.00, output: 15.00 },
  "claude-haiku-4-20250514": { input: 0.25, output: 1.25 },
  
  // Embeddings
  "text-embedding-3-small": { input: 0.02, output: 0 },
  "text-embedding-3-large": { input: 0.13, output: 0 },
  
  // Local (free)
  "llama-3.1-8b": { input: 0, output: 0 },
  "mixtral-8x7b": { input: 0, output: 0 },
};

// ============================================================================
// COST TRACKER
// ============================================================================

export class CostTracker extends EventEmitter {
  private records: CostRecord[] = [];
  private dailyStats: Map<string, DailyStats> = new Map();
  private alerts: AlertConfig[] = [
    { threshold: 50, level: "info", message: "Daily spend reached $50", notified: false },
    { threshold: 100, level: "warning", message: "Daily spend reached $100", notified: false },
    { threshold: 250, level: "warning", message: "Daily spend reached $250", notified: false },
    { threshold: 500, level: "critical", message: "Daily spend reached $500", notified: false },
    { threshold: 1000, level: "critical", message: "Daily spend reached $1000 - REVIEW IMMEDIATELY", notified: false },
  ];

  private maxRecordsInMemory = 10000;

  constructor() {
    super();
  }

  /**
   * Calculate cost for a request
   */
  calculateCost(model: string, inputTokens: number, outputTokens: number): number {
    const pricing = MODEL_PRICING[model] || { input: 5.0, output: 15.0 }; // Default to medium cost
    
    const inputCost = (inputTokens / 1_000_000) * pricing.input;
    const outputCost = (outputTokens / 1_000_000) * pricing.output;
    
    return inputCost + outputCost;
  }

  /**
   * Track a completed request
   */
  track(record: Omit<CostRecord, "cost"> & { cost?: number }): CostRecord {
    // Calculate cost if not provided
    const cost = record.cost ?? this.calculateCost(
      record.model,
      record.tokens.input,
      record.tokens.output
    );

    const fullRecord: CostRecord = {
      ...record,
      cost,
      tokens: {
        ...record.tokens,
        total: record.tokens.input + record.tokens.output,
      },
    };

    // Store record
    this.records.push(fullRecord);

    // Trim old records
    if (this.records.length > this.maxRecordsInMemory) {
      this.records = this.records.slice(-this.maxRecordsInMemory);
    }

    // Update daily stats
    this.updateDailyStats(fullRecord);

    // Check alerts
    this.checkAlerts();

    // Emit event
    this.emit("cost", fullRecord);

    return fullRecord;
  }

  /**
   * Update daily statistics
   */
  private updateDailyStats(record: CostRecord): void {
    const date = new Date(record.timestamp).toISOString().split("T")[0];
    const hour = new Date(record.timestamp).getHours();

    let stats = this.dailyStats.get(date);
    if (!stats) {
      stats = {
        date,
        totalCost: 0,
        totalTokens: 0,
        totalRequests: 0,
        cachedRequests: 0,
        savingsFromCache: 0,
        byModel: {},
        byUser: {},
        byHour: new Array(24).fill(0),
      };
      this.dailyStats.set(date, stats);
    }

    // Update totals
    stats.totalCost += record.cost;
    stats.totalTokens += record.tokens.total;
    stats.totalRequests += 1;
    stats.byHour[hour] += record.cost;

    if (record.cached) {
      stats.cachedRequests += 1;
      // Estimate savings (assume cache hit saves equivalent API cost)
      stats.savingsFromCache += record.cost;
    }

    // Update by model
    if (!stats.byModel[record.model]) {
      stats.byModel[record.model] = { cost: 0, tokens: 0, requests: 0 };
    }
    stats.byModel[record.model].cost += record.cost;
    stats.byModel[record.model].tokens += record.tokens.total;
    stats.byModel[record.model].requests += 1;

    // Update by user
    if (!stats.byUser[record.userId]) {
      stats.byUser[record.userId] = { cost: 0, tokens: 0, requests: 0 };
    }
    stats.byUser[record.userId].cost += record.cost;
    stats.byUser[record.userId].tokens += record.tokens.total;
    stats.byUser[record.userId].requests += 1;
  }

  /**
   * Check and trigger alerts
   */
  private checkAlerts(): void {
    const today = new Date().toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    if (!stats) return;

    for (const alert of this.alerts) {
      if (!alert.notified && stats.totalCost >= alert.threshold) {
        alert.notified = true;
        this.emit("alert", {
          level: alert.level,
          message: alert.message,
          currentCost: stats.totalCost,
          threshold: alert.threshold,
          timestamp: Date.now(),
        });
      }
    }
  }

  /**
   * Reset daily alerts (call at midnight)
   */
  resetDailyAlerts(): void {
    for (const alert of this.alerts) {
      alert.notified = false;
    }
  }

  /**
   * Get today's statistics
   */
  getTodayStats(): DailyStats | null {
    const today = new Date().toISOString().split("T")[0];
    return this.dailyStats.get(today) || null;
  }

  /**
   * Get statistics for a date range
   */
  getStatsRange(startDate: string, endDate: string): DailyStats[] {
    const result: DailyStats[] = [];
    
    for (const [date, stats] of this.dailyStats.entries()) {
      if (date >= startDate && date <= endDate) {
        result.push(stats);
      }
    }
    
    return result.sort((a, b) => a.date.localeCompare(b.date));
  }

  /**
   * Get cost projection based on current usage
   */
  getProjection(): CostProjection {
    const now = new Date();
    const today = now.toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    
    if (!stats) {
      return {
        currentDailyRate: 0,
        projectedDailyCost: 0,
        projectedMonthlyCost: 0,
        trend: "stable",
        trendPercent: 0,
      };
    }

    // Calculate hourly rate
    const currentHour = now.getHours();
    const hoursElapsed = currentHour + (now.getMinutes() / 60);
    const hourlyRate = hoursElapsed > 0 ? stats.totalCost / hoursElapsed : 0;

    // Project daily cost
    const projectedDailyCost = hourlyRate * 24;

    // Get yesterday's stats for trend
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split("T")[0];
    const yesterdayStats = this.dailyStats.get(yesterdayStr);

    let trend: "increasing" | "stable" | "decreasing" = "stable";
    let trendPercent = 0;

    if (yesterdayStats && yesterdayStats.totalCost > 0) {
      trendPercent = ((projectedDailyCost - yesterdayStats.totalCost) / yesterdayStats.totalCost) * 100;
      
      if (trendPercent > 10) trend = "increasing";
      else if (trendPercent < -10) trend = "decreasing";
    }

    return {
      currentDailyRate: hourlyRate,
      projectedDailyCost,
      projectedMonthlyCost: projectedDailyCost * 30,
      trend,
      trendPercent,
    };
  }

  /**
   * Get top spenders
   */
  getTopSpenders(limit: number = 10): Array<{ userId: string; cost: number; requests: number }> {
    const today = new Date().toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    
    if (!stats) return [];

    return Object.entries(stats.byUser)
      .map(([userId, data]) => ({ userId, ...data }))
      .sort((a, b) => b.cost - a.cost)
      .slice(0, limit);
  }

  /**
   * Get model cost breakdown
   */
  getModelBreakdown(): Array<{ model: string; cost: number; tokens: number; requests: number; percentOfTotal: number }> {
    const today = new Date().toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    
    if (!stats) return [];

    const totalCost = stats.totalCost || 1; // Avoid division by zero

    return Object.entries(stats.byModel)
      .map(([model, data]) => ({
        model,
        ...data,
        percentOfTotal: (data.cost / totalCost) * 100,
      }))
      .sort((a, b) => b.cost - a.cost);
  }

  /**
   * Export metrics for Prometheus
   */
  exportMetrics(): string {
    const today = new Date().toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    const projection = this.getProjection();

    const lines: string[] = [
      "# HELP vos_llm_cost_dollars_total Total LLM API cost in dollars",
      "# TYPE vos_llm_cost_dollars_total counter",
      `vos_llm_cost_dollars_total{period="daily"} ${stats?.totalCost || 0}`,
      "",
      "# HELP vos_llm_tokens_total Total tokens used",
      "# TYPE vos_llm_tokens_total counter",
      `vos_llm_tokens_total{period="daily"} ${stats?.totalTokens || 0}`,
      "",
      "# HELP vos_llm_requests_total Total requests made",
      "# TYPE vos_llm_requests_total counter",
      `vos_llm_requests_total{period="daily"} ${stats?.totalRequests || 0}`,
      "",
      "# HELP vos_llm_cached_requests_total Requests served from cache",
      "# TYPE vos_llm_cached_requests_total counter",
      `vos_llm_cached_requests_total{period="daily"} ${stats?.cachedRequests || 0}`,
      "",
      "# HELP vos_llm_cache_savings_dollars Money saved by caching",
      "# TYPE vos_llm_cache_savings_dollars counter",
      `vos_llm_cache_savings_dollars{period="daily"} ${stats?.savingsFromCache || 0}`,
      "",
      "# HELP vos_llm_projected_cost_dollars Projected cost based on current rate",
      "# TYPE vos_llm_projected_cost_dollars gauge",
      `vos_llm_projected_cost_dollars{period="daily"} ${projection.projectedDailyCost}`,
      `vos_llm_projected_cost_dollars{period="monthly"} ${projection.projectedMonthlyCost}`,
      "",
      "# HELP vos_llm_cost_by_model_dollars Cost breakdown by model",
      "# TYPE vos_llm_cost_by_model_dollars gauge",
    ];

    if (stats) {
      for (const [model, data] of Object.entries(stats.byModel)) {
        lines.push(`vos_llm_cost_by_model_dollars{model="${model}"} ${data.cost}`);
      }
    }

    return lines.join("\n");
  }

  /**
   * Generate cost report
   */
  generateReport(): string {
    const today = new Date().toISOString().split("T")[0];
    const stats = this.dailyStats.get(today);
    const projection = this.getProjection();

    if (!stats) {
      return "No data available for today.";
    }

    const modelBreakdown = this.getModelBreakdown();
    const topSpenders = this.getTopSpenders(5);

    let report = `
╔════════════════════════════════════════════════════════════╗
║                    💰 DAILY COST REPORT                    ║
║                       ${today}                         ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  Total Spend:        $${stats.totalCost.toFixed(2).padStart(10)}                    ║
║  Total Tokens:       ${stats.totalTokens.toLocaleString().padStart(12)}                    ║
║  Total Requests:     ${stats.totalRequests.toString().padStart(10)}                    ║
║                                                            ║
║  Cached Requests:    ${stats.cachedRequests.toString().padStart(10)} (${((stats.cachedRequests / stats.totalRequests) * 100).toFixed(1)}%)          ║
║  Cache Savings:      $${stats.savingsFromCache.toFixed(2).padStart(10)}                    ║
║                                                            ║
╠════════════════════════════════════════════════════════════╣
║  PROJECTIONS                                               ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  Projected Daily:    $${projection.projectedDailyCost.toFixed(2).padStart(10)}                    ║
║  Projected Monthly:  $${projection.projectedMonthlyCost.toFixed(2).padStart(10)}                    ║
║  Trend:              ${projection.trend.padStart(12)} (${projection.trendPercent > 0 ? '+' : ''}${projection.trendPercent.toFixed(1)}%)       ║
║                                                            ║
╠════════════════════════════════════════════════════════════╣
║  COST BY MODEL                                             ║
╠════════════════════════════════════════════════════════════╣`;

    for (const model of modelBreakdown) {
      report += `
║  ${model.model.padEnd(25)} $${model.cost.toFixed(2).padStart(8)} (${model.percentOfTotal.toFixed(1).padStart(5)}%)  ║`;
    }

    report += `
╠════════════════════════════════════════════════════════════╣
║  TOP SPENDERS                                              ║
╠════════════════════════════════════════════════════════════╣`;

    for (const spender of topSpenders) {
      report += `
║  ${spender.userId.slice(0, 20).padEnd(25)} $${spender.cost.toFixed(2).padStart(8)}        ║`;
    }

    report += `
╚════════════════════════════════════════════════════════════╝
`;

    return report;
  }
}

// ============================================================================
// SINGLETON INSTANCE
// ============================================================================

export const costTracker = new CostTracker();

// Setup default alert handlers
costTracker.on("alert", (alert) => {
  const emoji = alert.level === "critical" ? "🚨" : alert.level === "warning" ? "⚠️" : "ℹ️";
  console.log(`${emoji} Cost Alert: ${alert.message} (Current: $${alert.currentCost.toFixed(2)})`);
});

// Log hourly summaries
setInterval(() => {
  const projection = costTracker.getProjection();
  if (projection.currentDailyRate > 0) {
    console.log(`📊 Hourly Update: Rate $${projection.currentDailyRate.toFixed(2)}/hr, Projected $${projection.projectedDailyCost.toFixed(2)}/day`);
  }
}, 60 * 60 * 1000); // Every hour
