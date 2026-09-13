// model-registry.ts
// February 2026 Model Stack - Static Registry
// ============================================
// This registry provides fallback model definitions when the
// SmartRouter Python bridge is unavailable.
//
// For dynamic routing, use SmartRouterBridge which queries
// VOS3's central config/router.yaml via router_bridge.py

export interface ModelConfig {
  id: string;
  provider: "openai" | "anthropic" | "google" | "local" | "mistral";
  thinking: boolean;
  maxTokens?: number;
  temperature?: number;
}

export const MODEL_REGISTRY: Record<string, ModelConfig> = {
  // Manager model for lightweight orchestration (dispatcher, aggregator, finalize)
  // Liquid LFM 2.5 - 359 tokens/sec, SOTA SLM February 2026
  manager: {
    id: "liquid-lfm-2.5-1.2b",
    provider: "local", // Via OpenRouter or local Ollama
    thinking: false,
    maxTokens: 4096,
    temperature: 0.3 // Low temp for deterministic routing
  },
  architect: {
    id: "gpt-5.2-pro",
    provider: "openai",
    thinking: true,
    maxTokens: 16384,
    temperature: 0.7
  },
  coding: {
    id: "claude-3-opus-20260210", // Opus 4.6 - large codebase handling
    provider: "anthropic",
    thinking: false,
    maxTokens: 16384, // Extended output support
    temperature: 0.7
  },
  researcher: {
    id: "gemini-3-pro-latest",
    provider: "google",
    thinking: false,
    maxTokens: 32768,
    temperature: 0.7
  },
  reviewer: {
    id: "gpt-5.2", // Cross-model verification (reviews Claude's code)
    provider: "openai",
    thinking: false,
    maxTokens: 8192,
    temperature: 0.7
  },
  reviewer_self: {
    id: "claude-3-opus-20260210", // Opus 4.6 - Low-temp for self-correction
    provider: "anthropic",
    thinking: false,
    maxTokens: 16384,
    temperature: 0.2
  },
  tester: {
    id: "gemini-3-flash-latest",
    provider: "google",
    thinking: false,
    maxTokens: 8192,
    temperature: 0.5
  },
  // Mistral 7B v4 - "The Runner Up" for context escalation
  // Used when Liquid LFM context limit (32K) is exceeded
  mistral: {
    id: "mistral-7b-v4-instruct",
    provider: "mistral",
    thinking: false,
    maxTokens: 32768,
    temperature: 0.3
  }
};

// Complexity threshold - tasks >= 7 get premium models
export const COMPLEXITY_THRESHOLD = 7;

// Helper function to get model by role
export function getModelForRole(role: string): ModelConfig {
  return MODEL_REGISTRY[role] || MODEL_REGISTRY.coding;
}
