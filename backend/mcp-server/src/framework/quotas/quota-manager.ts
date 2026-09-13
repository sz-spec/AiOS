// src/framework/quotas/quota-manager.ts
// User Quotas & Budget Management

import { EventEmitter } from "events";

// ============================================================================
// TYPES
// ============================================================================

export interface QuotaLimits {
  daily: {
    tokens: number;
    requests: number;
    cost: number;
  };
  monthly: {
    tokens: number;
    requests: number;
    cost: number;
  };
  perRequest: {
    maxTokens: number;
    maxCost: number;
  };
}

export interface UsageRecord {
  tokens: number;
  cost: number;
  model: string;
  timestamp: number;
}

export interface UserUsage {
  userId: string;
  daily: {
    tokens: number;
    requests: number;
    cost: number;
    date: string; // YYYY-MM-DD
  };
  monthly: {
    tokens: number;
    requests: number;
    cost: number;
    month: string; // YYYY-MM
  };
  allTime: {
    tokens: number;
    requests: number;
    cost: number;
    firstRequest: number;
  };
}

export interface QuotaCheckResult {
  allowed: boolean;
  reason?: string;
  remaining?: {
    daily: { tokens: number; requests: number; cost: number };
    monthly: { tokens: number; requests: number; cost: number };
  };
  resetAt?: {
    daily: number;
    monthly: number;
  };
  percentUsed?: {
    daily: number;
    monthly: number;
  };
}

export interface QuotaAlert {
  userId: string;
  type: "warning" | "critical" | "exceeded";
  resource: "tokens" | "requests" | "cost";
  period: "daily" | "monthly";
  percentUsed: number;
  timestamp: number;
}

// ============================================================================
// DEFAULT QUOTAS BY TIER
// ============================================================================

export const DEFAULT_QUOTAS: Record<string, QuotaLimits> = {
  free: {
    daily: {
      tokens: 50_000,     // ~$0.50/day
      requests: 100,
      cost: 0.50,
    },
    monthly: {
      tokens: 500_000,    // ~$5/month
      requests: 1_000,
      cost: 5.00,
    },
    perRequest: {
      maxTokens: 4_096,
      maxCost: 0.05,
    },
  },
  pro: {
    daily: {
      tokens: 500_000,    // ~$5/day
      requests: 1_000,
      cost: 5.00,
    },
    monthly: {
      tokens: 10_000_000, // ~$100/month
      requests: 20_000,
      cost: 100.00,
    },
    perRequest: {
      maxTokens: 16_384,
      maxCost: 0.50,
    },
  },
  enterprise: {
    daily: {
      tokens: 5_000_000,  // ~$50/day
      requests: 10_000,
      cost: 50.00,
    },
    monthly: {
      tokens: 100_000_000, // ~$1000/month
      requests: 200_000,
      cost: 1000.00,
    },
    perRequest: {
      maxTokens: 32_768,
      maxCost: 5.00,
    },
  },
};

// ============================================================================
// QUOTA MANAGER
// ============================================================================

export class QuotaManager extends EventEmitter {
  private usage: Map<string, UserUsage> = new Map();
  private quotas: Map<string, QuotaLimits> = new Map();
  private alertThresholds = [0.8, 0.9, 1.0]; // 80%, 90%, 100%

  constructor() {
    super();
  }

  /**
   * Set quota limits for a user
   */
  setQuota(userId: string, tier: string | QuotaLimits): void {
    const limits = typeof tier === "string" 
      ? DEFAULT_QUOTAS[tier] || DEFAULT_QUOTAS.free
      : tier;
    this.quotas.set(userId, limits);
  }

  /**
   * Get quota limits for a user
   */
  getQuota(userId: string): QuotaLimits {
    return this.quotas.get(userId) || DEFAULT_QUOTAS.free;
  }

  /**
   * Check if a request is allowed within quota
   */
  async checkQuota(
    userId: string,
    estimatedUsage: { tokens: number; cost: number }
  ): Promise<QuotaCheckResult> {
    const quota = this.getQuota(userId);
    const usage = this.getUsage(userId);
    const now = new Date();
    const today = now.toISOString().split("T")[0];
    const thisMonth = today.slice(0, 7);

    // Reset daily usage if new day
    if (usage.daily.date !== today) {
      usage.daily = { tokens: 0, requests: 0, cost: 0, date: today };
    }

    // Reset monthly usage if new month
    if (usage.monthly.month !== thisMonth) {
      usage.monthly = { tokens: 0, requests: 0, cost: 0, month: thisMonth };
    }

    // Check per-request limits
    if (estimatedUsage.tokens > quota.perRequest.maxTokens) {
      return {
        allowed: false,
        reason: `Request exceeds max tokens (${estimatedUsage.tokens} > ${quota.perRequest.maxTokens})`,
      };
    }

    if (estimatedUsage.cost > quota.perRequest.maxCost) {
      return {
        allowed: false,
        reason: `Request exceeds max cost ($${estimatedUsage.cost.toFixed(4)} > $${quota.perRequest.maxCost})`,
      };
    }

    // Check daily limits
    if (usage.daily.tokens + estimatedUsage.tokens > quota.daily.tokens) {
      return {
        allowed: false,
        reason: "Daily token limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    if (usage.daily.requests + 1 > quota.daily.requests) {
      return {
        allowed: false,
        reason: "Daily request limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    if (usage.daily.cost + estimatedUsage.cost > quota.daily.cost) {
      return {
        allowed: false,
        reason: "Daily cost limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    // Check monthly limits
    if (usage.monthly.tokens + estimatedUsage.tokens > quota.monthly.tokens) {
      return {
        allowed: false,
        reason: "Monthly token limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    if (usage.monthly.requests + 1 > quota.monthly.requests) {
      return {
        allowed: false,
        reason: "Monthly request limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    if (usage.monthly.cost + estimatedUsage.cost > quota.monthly.cost) {
      return {
        allowed: false,
        reason: "Monthly cost limit exceeded",
        resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
      };
    }

    // Calculate remaining and percent used
    const remaining = {
      daily: {
        tokens: quota.daily.tokens - usage.daily.tokens - estimatedUsage.tokens,
        requests: quota.daily.requests - usage.daily.requests - 1,
        cost: quota.daily.cost - usage.daily.cost - estimatedUsage.cost,
      },
      monthly: {
        tokens: quota.monthly.tokens - usage.monthly.tokens - estimatedUsage.tokens,
        requests: quota.monthly.requests - usage.monthly.requests - 1,
        cost: quota.monthly.cost - usage.monthly.cost - estimatedUsage.cost,
      },
    };

    const percentUsed = {
      daily: Math.max(
        (usage.daily.tokens + estimatedUsage.tokens) / quota.daily.tokens,
        (usage.daily.requests + 1) / quota.daily.requests,
        (usage.daily.cost + estimatedUsage.cost) / quota.daily.cost
      ),
      monthly: Math.max(
        (usage.monthly.tokens + estimatedUsage.tokens) / quota.monthly.tokens,
        (usage.monthly.requests + 1) / quota.monthly.requests,
        (usage.monthly.cost + estimatedUsage.cost) / quota.monthly.cost
      ),
    };

    return {
      allowed: true,
      remaining,
      percentUsed,
      resetAt: { daily: this.getNextDayReset(), monthly: this.getNextMonthReset() },
    };
  }

  /**
   * Record usage after a successful request
   */
  recordUsage(userId: string, record: UsageRecord): void {
    const usage = this.getUsage(userId);
    const quota = this.getQuota(userId);
    const today = new Date().toISOString().split("T")[0];
    const thisMonth = today.slice(0, 7);

    // Ensure we're on the current period
    if (usage.daily.date !== today) {
      usage.daily = { tokens: 0, requests: 0, cost: 0, date: today };
    }
    if (usage.monthly.month !== thisMonth) {
      usage.monthly = { tokens: 0, requests: 0, cost: 0, month: thisMonth };
    }

    // Update usage
    usage.daily.tokens += record.tokens;
    usage.daily.requests += 1;
    usage.daily.cost += record.cost;

    usage.monthly.tokens += record.tokens;
    usage.monthly.requests += 1;
    usage.monthly.cost += record.cost;

    usage.allTime.tokens += record.tokens;
    usage.allTime.requests += 1;
    usage.allTime.cost += record.cost;

    this.usage.set(userId, usage);

    // Check for alerts
    this.checkAlerts(userId, usage, quota);
  }

  /**
   * Get current usage for a user
   */
  getUsage(userId: string): UserUsage {
    const existing = this.usage.get(userId);
    if (existing) return existing;

    const now = new Date();
    const today = now.toISOString().split("T")[0];
    const thisMonth = today.slice(0, 7);

    const newUsage: UserUsage = {
      userId,
      daily: { tokens: 0, requests: 0, cost: 0, date: today },
      monthly: { tokens: 0, requests: 0, cost: 0, month: thisMonth },
      allTime: { tokens: 0, requests: 0, cost: 0, firstRequest: Date.now() },
    };

    this.usage.set(userId, newUsage);
    return newUsage;
  }

  /**
   * Check and emit alerts for quota thresholds
   */
  private checkAlerts(userId: string, usage: UserUsage, quota: QuotaLimits): void {
    const checks = [
      { resource: "tokens" as const, period: "daily" as const, used: usage.daily.tokens, limit: quota.daily.tokens },
      { resource: "requests" as const, period: "daily" as const, used: usage.daily.requests, limit: quota.daily.requests },
      { resource: "cost" as const, period: "daily" as const, used: usage.daily.cost, limit: quota.daily.cost },
      { resource: "tokens" as const, period: "monthly" as const, used: usage.monthly.tokens, limit: quota.monthly.tokens },
      { resource: "requests" as const, period: "monthly" as const, used: usage.monthly.requests, limit: quota.monthly.requests },
      { resource: "cost" as const, period: "monthly" as const, used: usage.monthly.cost, limit: quota.monthly.cost },
    ];

    for (const check of checks) {
      const percentUsed = check.used / check.limit;

      for (const threshold of this.alertThresholds) {
        if (percentUsed >= threshold) {
          const type = threshold >= 1.0 ? "exceeded" : threshold >= 0.9 ? "critical" : "warning";
          
          const alert: QuotaAlert = {
            userId,
            type,
            resource: check.resource,
            period: check.period,
            percentUsed: percentUsed * 100,
            timestamp: Date.now(),
          };

          this.emit("alert", alert);
          break; // Only emit highest alert
        }
      }
    }
  }

  /**
   * Get next daily reset time
   */
  private getNextDayReset(): number {
    const now = new Date();
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(0, 0, 0, 0);
    return tomorrow.getTime();
  }

  /**
   * Get next monthly reset time
   */
  private getNextMonthReset(): number {
    const now = new Date();
    const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    return nextMonth.getTime();
  }

  /**
   * Get all users with high usage
   */
  getHighUsageUsers(thresholdPercent: number = 80): Array<{ userId: string; usage: UserUsage; percentUsed: number }> {
    const result: Array<{ userId: string; usage: UserUsage; percentUsed: number }> = [];

    for (const [userId, usage] of this.usage.entries()) {
      const quota = this.getQuota(userId);
      const percentUsed = Math.max(
        usage.daily.cost / quota.daily.cost,
        usage.monthly.cost / quota.monthly.cost
      ) * 100;

      if (percentUsed >= thresholdPercent) {
        result.push({ userId, usage, percentUsed });
      }
    }

    return result.sort((a, b) => b.percentUsed - a.percentUsed);
  }

  /**
   * Export metrics for Prometheus
   */
  exportMetrics(): string {
    const lines: string[] = [
      "# HELP vos_user_tokens_used Total tokens used by user",
      "# TYPE vos_user_tokens_used gauge",
      "# HELP vos_user_cost_used Total cost by user",
      "# TYPE vos_user_cost_used gauge",
      "# HELP vos_user_quota_percent Quota usage percentage",
      "# TYPE vos_user_quota_percent gauge",
    ];

    for (const [userId, usage] of this.usage.entries()) {
      const quota = this.getQuota(userId);
      
      lines.push(`vos_user_tokens_used{user="${userId}",period="daily"} ${usage.daily.tokens}`);
      lines.push(`vos_user_tokens_used{user="${userId}",period="monthly"} ${usage.monthly.tokens}`);
      lines.push(`vos_user_cost_used{user="${userId}",period="daily"} ${usage.daily.cost}`);
      lines.push(`vos_user_cost_used{user="${userId}",period="monthly"} ${usage.monthly.cost}`);
      lines.push(`vos_user_quota_percent{user="${userId}",period="daily"} ${(usage.daily.cost / quota.daily.cost) * 100}`);
      lines.push(`vos_user_quota_percent{user="${userId}",period="monthly"} ${(usage.monthly.cost / quota.monthly.cost) * 100}`);
    }

    return lines.join("\n");
  }
}

// ============================================================================
// SINGLETON INSTANCE
// ============================================================================

export const quotaManager = new QuotaManager();

// Setup default alert handlers
quotaManager.on("alert", (alert: QuotaAlert) => {
  const emoji = alert.type === "exceeded" ? "🚫" : alert.type === "critical" ? "🔴" : "⚠️";
  console.log(
    `${emoji} Quota Alert: User ${alert.userId} - ${alert.period} ${alert.resource} at ${alert.percentUsed.toFixed(1)}%`
  );
});
