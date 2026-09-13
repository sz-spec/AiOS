// src/orchestration/cost-aware-router.ts
// Smart Model Routing for Cost Optimization

import { z } from "zod";

// ============================================================================
// TYPES
// ============================================================================

export interface ModelConfig {
  id: string;
  provider: "openai" | "anthropic" | "google" | "local" | "mistral";
  model: string;
  costPer1MInput: number;
  costPer1MOutput: number;
  maxTokens: number;
  capabilities: string[];
}

export interface RoutingDecision {
  model: ModelConfig;
  tier: "simple" | "medium" | "complex";
  estimatedCost: number;
  reason: string;
  confidence: number;
}

export interface ComplexityAnalysis {
  score: number; // 0-1
  factors: {
    codeComplexity: number;
    reasoningDepth: number;
    domainExpertise: number;
    outputLength: number;
  };
  matchedPatterns: string[];
}

// ============================================================================
// MODEL REGISTRY
// ============================================================================

// ============================================================================
// MODEL REGISTRY - February 2026 Stack (Synced with VOS3 router.yaml)
// ============================================================================
// Models aligned with VOS3 SmartRouter configuration:
//   - Architect: gpt-5.2-pro (Thinking Mode)
//   - Coding: claude-3-opus-20260210 (Opus 4.6)
//   - Reviewer: gpt-5.2 (Cross-Model Verification)
//   - Reviewer Self: claude-opus at temp 0.2
//   - Researcher: gemini-3-pro-latest
//   - Complexity threshold: 7
// ============================================================================

export const MODEL_REGISTRY: Record<string, ModelConfig> = {
  // Anthropic Models - February 2026
  "claude-opus": {
    id: "claude-opus",
    provider: "anthropic",
    model: "claude-3-opus-20260210",  // Opus 4.6
    costPer1MInput: 15.00,
    costPer1MOutput: 75.00,
    maxTokens: 8192,
    capabilities: ["chat", "code", "deep-analysis", "review", "security", "agentic", "200k-context", "large-codebase"],
  },
  "claude-opus-low-temp": {
    id: "claude-opus-low-temp",
    provider: "anthropic",
    model: "claude-3-opus-20260210",  // Opus 4.6 at temp 0.2
    costPer1MInput: 15.00,
    costPer1MOutput: 75.00,
    maxTokens: 8192,
    capabilities: ["self-review", "verification", "deterministic", "security-audit"],
  },

  // OpenAI Models - February 2026
  "gpt-5.2-pro": {
    id: "gpt-5.2-pro",
    provider: "openai",
    model: "gpt-5.2-pro",  // Thinking Mode for architecture
    costPer1MInput: 20.00,
    costPer1MOutput: 80.00,
    maxTokens: 16384,
    capabilities: ["architecture", "system-design", "reasoning", "thinking-mode", "extended-context"],
  },
  "gpt-5.2": {
    id: "gpt-5.2",
    provider: "openai",
    model: "gpt-5.2",  // Standard for cross-model verification
    costPer1MInput: 15.00,
    costPer1MOutput: 60.00,
    maxTokens: 8192,
    capabilities: ["chat", "code-review", "cross-verification", "reasoning", "agentic"],
  },
  // Alias: "gpt" maps to gpt-5.2-pro for SmartRouter compatibility
  "gpt": {
    id: "gpt",
    provider: "openai",
    model: "gpt-5.2-pro",
    costPer1MInput: 20.00,
    costPer1MOutput: 80.00,
    maxTokens: 16384,
    capabilities: ["architecture", "system-design", "reasoning", "thinking-mode"],
  },

  // Google Models - February 2026
  "gemini-3-pro": {
    id: "gemini-3-pro",
    provider: "google",
    model: "gemini-3-pro-latest",  // Massive context for research
    costPer1MInput: 2.00,
    costPer1MOutput: 12.00,
    maxTokens: 2097152,  // 2M context window
    capabilities: ["research", "multimodal", "massive-context", "document-analysis", "agentic"],
  },
  "gemini-3-flash": {
    id: "gemini-3-flash",
    provider: "google",
    model: "gemini-3-flash-latest",
    costPer1MInput: 0.50,
    costPer1MOutput: 3.00,
    maxTokens: 1048576,
    capabilities: ["fast-inference", "testing", "simple-tasks", "multimodal"],
  },

  // Local tier - free (for privacy-sensitive tasks)
  "local-llama": {
    id: "local-llama",
    provider: "local",
    model: "llama-3.1-8b",
    costPer1MInput: 0,
    costPer1MOutput: 0,
    maxTokens: 8192,
    capabilities: ["chat", "code-simple", "private", "offline"],
  },

  // Liquid LFM 2.5 - SOTA SLM for orchestration (February 2026)
  // 359 tokens/sec, extremely efficient for routing tasks
  "liquid-lfm": {
    id: "liquid-lfm",
    provider: "local",
    model: "liquid-lfm-2.5-1.2b",
    costPer1MInput: 0.05,  // Very cheap via OpenRouter
    costPer1MOutput: 0.15,
    maxTokens: 4096,
    capabilities: ["routing", "orchestration", "fast-inference", "lightweight"],
  },

  // Mistral 7B v4 - "The Runner Up" for context escalation
  // Used when Liquid LFM context limit (32K) is exceeded
  "mistral-7b": {
    id: "mistral-7b",
    provider: "mistral",
    model: "mistral-7b-v4-instruct",
    costPer1MInput: 0.10,
    costPer1MOutput: 0.30,
    maxTokens: 32768,
    capabilities: ["routing", "orchestration", "extended-context", "fallback"],
  },
};

// ============================================================================
// ROLE TO MODEL MAPPING (Matches VOS3 router.yaml)
// ============================================================================

export const ROLE_MODEL_MAPPING: Record<string, string> = {
  architect: "gpt-5.2-pro",
  coding: "claude-opus",
  reviewer: "gpt-5.2",
  reviewer_self: "claude-opus-low-temp",
  researcher: "gemini-3-pro",
  tester: "gemini-3-flash",
  frontend: "claude-opus",
  backend: "claude-opus",
};

// Complexity threshold - tasks >= 7 get premium models
export const COMPLEXITY_THRESHOLD = 7;

// ============================================================================
// COMPLEXITY PATTERNS
// ============================================================================

const SIMPLE_PATTERNS = [
  /^(what is|define|explain simply|tell me about)\s/i,
  /^(format|convert|translate|rewrite)\s/i,
  /^(list|enumerate|count|how many)\s/i,
  /^(fix (this )?typo|correct grammar)/i,
  /^(summarize|tldr|brief)/i,
  /hello|hi|hey|thanks|thank you/i,
];

const COMPLEX_PATTERNS = [
  /architect|design.*system|infrastructure/i,
  /debug.*complex|fix.*critical|production.*issue/i,
  /analyze.*trade.?offs|compare.*approaches/i,
  /optimize.*performance|scale.*system/i,
  /security.*vulnerabilit|penetration|exploit/i,
  /implement.*from scratch|build.*complete/i,
  /refactor.*entire|rewrite.*codebase/i,
  /mathematical.*proof|algorithm.*complexity/i,
  /distributed.*system|consensus|raft|paxos/i,
];

const CODE_INDICATORS = [
  /```[\s\S]*```/,
  /function\s+\w+|const\s+\w+\s*=|class\s+\w+/,
  /import\s+.*from|require\s*\(/,
  /\.(ts|js|py|java|go|rs|cpp)$/i,
];

const REASONING_INDICATORS = [
  /why|how come|explain why/i,
  /what.*best.*approach|which.*should.*use/i,
  /pros.*cons|advantages.*disadvantages/i,
  /step.?by.?step|walk.*through/i,
];

// ============================================================================
// COST-AWARE ROUTER
// ============================================================================

export class CostAwareRouter {
  // February 2026 tier defaults (aligned with VOS3)
  private defaultTierModels: Record<string, string> = {
    simple: "liquid-lfm",           // Liquid LFM 2.5 - 359 tok/s, SOTA SLM
    medium: "claude-opus",          // Opus 4.6 for standard coding
    complex: "gpt-5.2-pro",    // Thinking mode for complex architecture
  };

  // Complexity thresholds (aligned with VOS3: threshold = 7 means 0.7 normalized)
  private thresholds = {
    simple: 0.3,
    medium: 0.7,  // >= 0.7 uses complex tier (matches VOS3 complexity >= 7)
  };

  constructor(
    private config?: {
      preferLocal?: boolean;
      maxCostPerRequest?: number;
      defaultModel?: string;
    }
  ) {}

  /**
   * Analyze message complexity and route to appropriate model
   */
  async route(
    message: string,
    context?: {
      conversationHistory?: string[];
      userTier?: "free" | "pro" | "enterprise";
      preferredProvider?: string;
      forceModel?: string;
    }
  ): Promise<RoutingDecision> {
    // Force specific model if requested
    if (context?.forceModel && MODEL_REGISTRY[context.forceModel]) {
      const model = MODEL_REGISTRY[context.forceModel];
      return {
        model,
        tier: this.getTierForModel(model),
        estimatedCost: this.estimateCost(message, model),
        reason: "forced_model",
        confidence: 1.0,
      };
    }

    // Analyze complexity
    const complexity = this.analyzeComplexity(message, context?.conversationHistory);

    // Determine tier
    const tier = this.determineTier(complexity);

    // Select model based on tier and preferences
    const model = this.selectModel(tier, context);

    // Calculate estimated cost
    const estimatedCost = this.estimateCost(message, model);

    // Check cost limits
    if (this.config?.maxCostPerRequest && estimatedCost > this.config.maxCostPerRequest) {
      // Downgrade to cheaper model
      const cheaperModel = this.findCheaperAlternative(model, this.config.maxCostPerRequest, message);
      return {
        model: cheaperModel,
        tier: this.getTierForModel(cheaperModel),
        estimatedCost: this.estimateCost(message, cheaperModel),
        reason: "cost_limit_downgrade",
        confidence: complexity.score,
      };
    }

    return {
      model,
      tier,
      estimatedCost,
      reason: `complexity_${tier}`,
      confidence: complexity.score,
    };
  }

  /**
   * Analyze message complexity
   */
  analyzeComplexity(message: string, history?: string[]): ComplexityAnalysis {
    const factors = {
      codeComplexity: 0,
      reasoningDepth: 0,
      domainExpertise: 0,
      outputLength: 0,
    };
    const matchedPatterns: string[] = [];

    // Check for simple patterns (reduce complexity)
    for (const pattern of SIMPLE_PATTERNS) {
      if (pattern.test(message)) {
        matchedPatterns.push(`simple:${pattern.source.slice(0, 20)}`);
        factors.reasoningDepth -= 0.2;
      }
    }

    // Check for complex patterns (increase complexity)
    for (const pattern of COMPLEX_PATTERNS) {
      if (pattern.test(message)) {
        matchedPatterns.push(`complex:${pattern.source.slice(0, 20)}`);
        factors.reasoningDepth += 0.3;
        factors.domainExpertise += 0.2;
      }
    }

    // Check for code indicators
    for (const pattern of CODE_INDICATORS) {
      if (pattern.test(message)) {
        matchedPatterns.push("code_present");
        factors.codeComplexity += 0.2;
      }
    }

    // Check for reasoning indicators
    for (const pattern of REASONING_INDICATORS) {
      if (pattern.test(message)) {
        matchedPatterns.push("reasoning_required");
        factors.reasoningDepth += 0.15;
      }
    }

    // Message length affects expected output
    const wordCount = message.split(/\s+/).length;
    if (wordCount > 200) {
      factors.outputLength = 0.3;
    } else if (wordCount > 50) {
      factors.outputLength = 0.1;
    }

    // Consider conversation history
    if (history && history.length > 5) {
      factors.reasoningDepth += 0.1; // Longer conversations may need more context
    }

    // Calculate final score (0-1)
    const rawScore =
      factors.codeComplexity * 0.25 +
      factors.reasoningDepth * 0.35 +
      factors.domainExpertise * 0.25 +
      factors.outputLength * 0.15;

    const score = Math.max(0, Math.min(1, rawScore + 0.3)); // Normalize to 0-1

    return {
      score,
      factors,
      matchedPatterns,
    };
  }

  /**
   * Determine tier based on complexity score
   */
  private determineTier(complexity: ComplexityAnalysis): "simple" | "medium" | "complex" {
    if (complexity.score < this.thresholds.simple) {
      return "simple";
    } else if (complexity.score < this.thresholds.medium) {
      return "medium";
    } else {
      return "complex";
    }
  }

  /**
   * Select model based on tier and preferences
   */
  private selectModel(
    tier: "simple" | "medium" | "complex",
    context?: { preferredProvider?: string; userTier?: string }
  ): ModelConfig {
    // Check for local preference
    if (this.config?.preferLocal && tier === "simple") {
      const localModel = MODEL_REGISTRY["local-llama"];
      if (localModel) return localModel;
    }

    // Check for provider preference
    if (context?.preferredProvider) {
      const preferredModels = Object.values(MODEL_REGISTRY).filter(
        (m) => m.provider === context.preferredProvider
      );
      
      const tierModels = preferredModels.filter((m) => {
        if (tier === "simple") return m.costPer1MInput < 1;
        if (tier === "medium") return m.costPer1MInput >= 1 && m.costPer1MInput < 10;
        return m.costPer1MInput >= 10;
      });

      if (tierModels.length > 0) {
        return tierModels[0];
      }
    }

    // Default model for tier
    const modelId = this.defaultTierModels[tier];
    return MODEL_REGISTRY[modelId];
  }

  /**
   * Estimate cost for a request
   */
  private estimateCost(message: string, model: ModelConfig): number {
    // Rough token estimation (4 chars ≈ 1 token)
    const inputTokens = Math.ceil(message.length / 4);
    
    // Estimate output tokens (usually 1-3x input for chat)
    const estimatedOutputTokens = inputTokens * 2;

    const inputCost = (inputTokens / 1_000_000) * model.costPer1MInput;
    const outputCost = (estimatedOutputTokens / 1_000_000) * model.costPer1MOutput;

    return inputCost + outputCost;
  }

  /**
   * Find a cheaper model alternative
   */
  private findCheaperAlternative(
    current: ModelConfig,
    maxCost: number,
    message: string
  ): ModelConfig {
    const sortedModels = Object.values(MODEL_REGISTRY)
      .filter((m) => m.id !== current.id)
      .sort((a, b) => a.costPer1MInput - b.costPer1MInput);

    for (const model of sortedModels) {
      const cost = this.estimateCost(message, model);
      if (cost <= maxCost) {
        return model;
      }
    }

    // Return cheapest if nothing fits
    return sortedModels[0] || current;
  }

  /**
   * Get tier for a model based on cost
   */
  private getTierForModel(model: ModelConfig): "simple" | "medium" | "complex" {
    if (model.costPer1MInput < 1) return "simple";
    if (model.costPer1MInput < 10) return "medium";
    return "complex";
  }
}

// ============================================================================
// EXPORTS
// ============================================================================

export const costAwareRouter = new CostAwareRouter();

// Zod schemas for validation
export const RoutingDecisionSchema = z.object({
  model: z.object({
    id: z.string(),
    provider: z.enum(["openai", "anthropic", "google", "local", "mistral"]),
    model: z.string(),
    costPer1MInput: z.number(),
    costPer1MOutput: z.number(),
  }),
  tier: z.enum(["simple", "medium", "complex"]),
  estimatedCost: z.number(),
  reason: z.string(),
  confidence: z.number(),
});
