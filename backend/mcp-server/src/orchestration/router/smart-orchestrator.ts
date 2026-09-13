// src/orchestration/router/smart-orchestrator.ts
// Integration with SmartRouter for intelligent agent selection

import { createSmartRouter, type SmartRouterModel } from "../smart-router-bridge.js";

// Create singleton SmartRouter instance
const smartRouter = createSmartRouter();

// Map SmartRouter models to V OS agents
const MODEL_TO_AGENT: Record<string, string> = {
  "claude-opus": "vos-coder",      // Claude Opus for complex coding
  "claude-sonnet": "vos-coder",    // Claude Sonnet for general coding
  "gpt": "vos-architect",          // GPT for architecture
  "gemini": "vos-researcher",      // Gemini for research
};

// Map roles to agents
const ROLE_TO_AGENT: Record<string, string> = {
  architect: "vos-architect",
  reviewer: "vos-coder",
  researcher: "vos-researcher",
  coding: "vos-coder",
};

export interface RoutingResult {
  agentId: string;
  model: SmartRouterModel;
  role: string;
  complexity: number;
}

/**
 * Determine the optimal agent for a message using SmartRouter
 */
export async function determineOptimalAgent(
  message: string,
  _context?: { preferredAgent?: string; maxComplexity?: number }
): Promise<string> {
  try {
    const result = await smartRouter.routeMessage(message);

    // Map model to agent
    const agentFromModel = MODEL_TO_AGENT[result.model.name];
    const agentFromRole = ROLE_TO_AGENT[result.role];

    // Prefer role-based agent if available
    const agentId = agentFromRole || agentFromModel || "vos-coder";

    console.log(
      `[SmartOrchestrator] ${message.slice(0, 50)}... → ` +
        `role: ${result.role}, complexity: ${result.complexity}/10, ` +
        `model: ${result.model.name} → agent: ${agentId}`
    );

    return agentId;
  } catch (error) {
    console.error("SmartRouter routing failed, falling back to default:", error);
    return "vos-coder"; // Safe fallback
  }
}

/**
 * Get full routing details including model information
 */
export async function getRoutingDetails(message: string): Promise<RoutingResult> {
  try {
    const result = await smartRouter.routeMessage(message);
    const agentId = ROLE_TO_AGENT[result.role] || MODEL_TO_AGENT[result.model.name] || "vos-coder";

    return {
      agentId,
      model: result.model,
      role: result.role,
      complexity: result.complexity,
    };
  } catch (error) {
    console.error("SmartRouter getRoutingDetails failed:", error);
    return {
      agentId: "vos-coder",
      model: {
        name: "claude-sonnet",
        model_id: "claude-3-7-sonnet",
        provider: "anthropic",
        max_tokens: 8192,
        temperature: 0.7,
      },
      role: "coding",
      complexity: 5,
    };
  }
}

/**
 * List all available models from SmartRouter
 */
export function listAvailableModels(): { all: string[]; enabled: string[] } {
  return smartRouter.listModels();
}

/**
 * Get model for specific role and complexity
 */
export function getModelForRole(role: string, complexity: number): SmartRouterModel {
  return smartRouter.getModelForRole(role, complexity);
}
