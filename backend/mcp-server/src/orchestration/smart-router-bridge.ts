// smart-router-bridge.ts
// TypeScript Bridge to VOS3 SmartRouter (Python)
// ================================================
// Uses an interactive Python subprocess for high-performance routing.
// Supports streaming responses via AsyncIterable.

import { spawn, execSync, ChildProcess } from "child_process";
import { EventEmitter } from "events";
import path from "path";
import { fileURLToPath } from "url";
import { MODEL_REGISTRY, getModelForRole } from "./model-registry.js";

// ============================================================================
// PATH CONFIGURATION
// ============================================================================

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const MCP_SERVER_ROOT = path.resolve(__dirname, "../..");
const VOS3_BACKEND_ROOT = path.resolve(MCP_SERVER_ROOT, "..");
const ROUTER_BRIDGE_PATH = path.join(VOS3_BACKEND_ROOT, "scripts", "router_bridge.py");

// ============================================================================
// SECURITY: FILE PATH ALLOWLIST (February 2026)
// ============================================================================
// Agents can only write to these directories. All other paths are blocked.
// This prevents malicious or misconfigured agents from writing to sensitive areas.

const ALLOWED_WRITE_PATHS = [
  "backend/convex/",
  "frontend/src/components/",
  "frontend/src/hooks/",
  "frontend/src/app/",
  "frontend/src/types/",
  "frontend/src/lib/",
  "frontend/src/utils/",
  "backend/api/",
  "backend/src/",
  "backend/tests/",
];

// ============================================================================
// MODEL DISPLAY NAMES (February 2026)
// ============================================================================
// Human-readable names for UI display

export const MODEL_DISPLAY_NAMES: Record<string, string> = {
  "liquid-lfm-2.5-1.2b": "Liquid LFM 2.5 (Turbo)",
  "mistral-7b-v4-instruct": "Mistral 7B v4 (Long Context)",
  "claude-3-opus-20260210": "Claude Opus 4.6 (Premium Coding)",
  "gpt-5.2-pro": "GPT-5.2 (Reasoning)"
};

// ============================================================================
// TYPES
// ============================================================================

export interface SmartRouterResult {
  name: string;
  model_id: string;
  provider: "openai" | "anthropic" | "google" | "local";
  max_tokens: number;
  temperature: number;
  thinking_mode: boolean;
  supports_streaming: boolean;
  complexity_used: number;
  role_used: string;
  source: string;
}

export interface RoutingDecision {
  modelId: string;
  provider: string;
  thinking: boolean;
  supportsStreaming: boolean;
  source:
    | "smartrouter"
    | "fallback"
    | "context_escalation"
    | "context_escalation_32k_rule"
    | "low_confidence_escalation"
    | "speculative";
  /** Confidence score from speculative routing (0-1) */
  confidence?: number;
  /** Context size in tokens when routing was decided */
  contextTokens?: number;
}

export interface SmartRouterModel {
  name: string;
  model_id: string;
  provider: "anthropic" | "openai" | "google" | "local";
  max_tokens: number;
  temperature: number;
}

interface StreamChunk {
  chunk: string;
  done: boolean;
}

interface PythonResponse {
  status: "ready" | "ok" | "error" | "stream" | "exit";
  data?: Partial<Omit<SmartRouterResult, "source"> & StreamChunk> & { confidence?: number; source?: RoutingDecision["source"] };
  message?: string;
}

interface PendingRequest {
  resolve: (value: PythonResponse) => void;
  reject: (error: Error) => void;
  timeout: NodeJS.Timeout;
}

// ============================================================================
// SMART ROUTER BRIDGE (with Streaming Support)
// ============================================================================

export class SmartRouterBridge extends EventEmitter {
  private pythonPath: string;
  private bridgePath: string;
  private backendRoot: string;

  // Interactive mode state
  private process: ChildProcess | null = null;
  private isInteractiveMode: boolean = false;
  private isReady: boolean = false;
  private pendingRequests: PendingRequest[] = [];
  private buffer: string = "";

  constructor(pythonPath: string = "python3") {
    super();
    this.pythonPath = pythonPath;
    this.bridgePath = ROUTER_BRIDGE_PATH;
    this.backendRoot = VOS3_BACKEND_ROOT;

    console.log(`[SmartRouterBridge] Bridge path: ${this.bridgePath}`);
    console.log(`[SmartRouterBridge] Backend root: ${this.backendRoot}`);
  }

  // =========================================================================
  // INTERACTIVE MODE MANAGEMENT
  // =========================================================================

  async startInteractive(): Promise<boolean> {
    if (this.isInteractiveMode && this.isReady) {
      return true;
    }

    return new Promise((resolve) => {
      try {
        this.process = spawn(this.pythonPath, [this.bridgePath], {
          cwd: this.backendRoot,
          env: {
            ...process.env,
            PYTHONPATH: this.backendRoot
          },
          stdio: ["pipe", "pipe", "pipe"]
        });

        this.buffer = "";

        // Handle stdout data
        this.process.stdout?.on("data", (data: Buffer) => {
          this.buffer += data.toString();
          this.handleStdout();
        });

        // Handle stderr
        this.process.stderr?.on("data", (data: Buffer) => {
          console.error(`[SmartRouterBridge] stderr: ${data.toString()}`);
        });

        // Handle process exit
        this.process.on("close", (code) => {
          console.log(`[SmartRouterBridge] Process exited with code ${code}`);
          this.cleanup();
        });

        // Handle errors
        this.process.on("error", (err) => {
          console.error(`[SmartRouterBridge] Process error:`, err);
          this.cleanup();
          resolve(false);
        });

        // Wait for ready signal
        const readyTimeout = setTimeout(() => {
          if (!this.isReady) {
            console.warn("[SmartRouterBridge] Timeout waiting for ready signal");
            this.cleanup();
            resolve(false);
          }
        }, 5000);

        this.once("ready", () => {
          clearTimeout(readyTimeout);
          this.isInteractiveMode = true;
          this.isReady = true;
          console.log("[SmartRouterBridge] Interactive mode ready");
          resolve(true);
        });

      } catch (error) {
        console.error("[SmartRouterBridge] Failed to start interactive mode:", error);
        resolve(false);
      }
    });
  }

  private handleStdout(): void {
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.trim()) continue;

      try {
        const response: PythonResponse = JSON.parse(line);

        if (response.status === "ready") {
          this.emit("ready");
        } else if (response.status === "stream") {
          // Emit chunk for active stream
          this.emit("chunk", response.data?.chunk || "");
        } else if (response.status === "ok") {
          // Check if this is stream completion
          if (response.data?.done) {
            this.emit("stream_done", response.data);
          }
          // Also resolve pending request
          const pending = this.pendingRequests.shift();
          if (pending) {
            clearTimeout(pending.timeout);
            pending.resolve(response);
          }
        } else if (response.status === "error") {
          this.emit("stream_error", response.message);
          const pending = this.pendingRequests.shift();
          if (pending) {
            clearTimeout(pending.timeout);
            pending.reject(new Error(response.message || "Unknown error"));
          }
        } else if (response.status === "exit") {
          this.cleanup();
        }
      } catch (_e) {
        console.warn("[SmartRouterBridge] Failed to parse response:", line);
      }
    }
  }

  private async sendCommand(command: string, params: Record<string, unknown> = {}): Promise<PythonResponse> {
    if (!this.isInteractiveMode || !this.process?.stdin) {
      throw new Error("Interactive mode not available");
    }

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Command timeout"));
      }, 30000); // 30 second timeout for streaming

      this.pendingRequests.push({ resolve, reject, timeout });

      const json = JSON.stringify({ command, params }) + "\n";
      this.process!.stdin!.write(json);
    });
  }

  async stopInteractive(): Promise<void> {
    if (this.process && this.isInteractiveMode) {
      try {
        await this.sendCommand("exit");
      } catch {
        // Ignore errors during shutdown
      }
      this.cleanup();
    }
  }

  private cleanup(): void {
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
    this.isInteractiveMode = false;
    this.isReady = false;
    this.pendingRequests.forEach((req) => {
      clearTimeout(req.timeout);
      req.reject(new Error("Process terminated"));
    });
    this.pendingRequests = [];
    this.buffer = "";
    this.removeAllListeners();
  }

  // =========================================================================
  // STREAMING RESPONSE
  // =========================================================================

  /**
   * Stream LLM response via Python bridge.
   * Uses Claude 3 Opus 4.6 by default.
   */
  async *streamResponse(
    prompt: string,
    role: string = "coding",
    complexity: number = 5,
    options: {
      system?: string;
      maxTokens?: number;
      temperature?: number;
    } = {}
  ): AsyncIterable<string> {
    if (!this.isReady) {
      await this.startInteractive();
    }

    // Get routing decision
    const decision = await this.getModelForTaskAsync(role, complexity);

    console.log(
      `[SmartRouter] Streaming ${role}@${complexity} -> ${decision.modelId}` +
      (decision.thinking ? " [THINKING]" : "")
    );

    // Send stream command
    const command = {
      command: "route_stream",
      params: {
        prompt,
        modelId: decision.modelId,
        provider: decision.provider,
        system: options.system,
        maxTokens: options.maxTokens || 16384,
        temperature: options.temperature || 0.7
      }
    };

    this.process!.stdin!.write(JSON.stringify(command) + "\n");

    // Create async generator that listens for chunks
    let streamEnded = false;
    let streamError: Error | null = null;

    const errorHandler = (error: string) => {
      streamError = new Error(error);
      streamEnded = true;
    };
    this.on("stream_error", errorHandler);

    try {
      while (!streamEnded) {
        const chunk = await new Promise<string | null>((resolve) => {
          const onChunk = (c: string) => {
            this.removeListener("stream_done", onDone);
            resolve(c);
          };

          const onDone = () => {
            this.removeListener("chunk", onChunk);
            streamEnded = true;
            resolve(null);
          };

          this.once("chunk", onChunk);
          this.once("stream_done", onDone);

          // Timeout for individual chunks
          setTimeout(() => {
            this.removeListener("chunk", onChunk);
            this.removeListener("stream_done", onDone);
            resolve(null);
          }, 60000); // 60 second chunk timeout
        });

        if (streamError) {
          throw streamError;
        }

        if (chunk !== null) {
          yield chunk;
        } else {
          break;
        }
      }
    } finally {
      this.removeListener("stream_error", errorHandler);
    }
  }

  // =========================================================================
  // MODEL ROUTING
  // =========================================================================

  getModelForTask(role: string, complexity: number): RoutingDecision {
    return this.getModelForTaskCLI(role, complexity);
  }

  async getModelForTaskAsync(
    role: string,
    complexity: number,
    contextLength?: number
  ): Promise<RoutingDecision> {
    // Context escalation for Liquid LFM limits (February 2026)
    // Liquid LFM 2.5 has 32K context limit - escalate to Mistral 7B for larger contexts
    const LIQUID_LFM_CONTEXT_LIMIT = 32000;

    if (
      role === "aggregator" &&
      contextLength &&
      contextLength > LIQUID_LFM_CONTEXT_LIMIT
    ) {
      console.log(
        `[SmartRouter] Context (${contextLength}) exceeds Liquid LFM limit (${LIQUID_LFM_CONTEXT_LIMIT}). ` +
        `Routing to Mistral 7B (The Runner Up).`
      );

      return {
        modelId: "mistral-7b-v4-instruct", // "The Runner Up" - handles larger contexts
        provider: "mistral",
        thinking: false,
        supportsStreaming: true,
        source: "context_escalation"
      };
    }

    // Similar escalation for dispatcher/finalize with large contexts
    if (
      (role === "dispatcher" || role === "finalize") &&
      contextLength &&
      contextLength > LIQUID_LFM_CONTEXT_LIMIT
    ) {
      console.log(
        `[SmartRouter] Context (${contextLength}) exceeds Liquid LFM limit. ` +
        `Routing ${role} to Mistral 7B.`
      );

      return {
        modelId: "mistral-7b-v4-instruct",
        provider: "mistral",
        thinking: false,
        supportsStreaming: true,
        source: "context_escalation"
      };
    }

    if (this.isInteractiveMode && this.isReady) {
      try {
        const response = await this.sendCommand("get_model", { role, complexity });

        if (response.status === "ok" && response.data) {
          const result = response.data as SmartRouterResult;
          if (result.name && result.model_id) {
            return {
              modelId: result.model_id,
              provider: result.provider,
              thinking: result.thinking_mode,
              supportsStreaming: result.supports_streaming ?? true,
              source: "smartrouter"
            };
          }
        }
      } catch (error) {
        console.warn("[SmartRouterBridge] Interactive mode failed:", error);
      }
    }

    return this.getModelForTaskCLI(role, complexity);
  }

  private getModelForTaskCLI(role: string, complexity: number): RoutingDecision {
    try {
      const cmd = `${this.pythonPath} "${this.bridgePath}" get_model ${role} ${complexity}`;

      const output = execSync(cmd, {
        cwd: this.backendRoot,
        encoding: "utf-8",
        env: {
          ...process.env,
          PYTHONPATH: this.backendRoot
        },
        timeout: 10000,
        stdio: ["pipe", "pipe", "pipe"]
      });

      const lines = output.trim().split("\n");
      const jsonLine = lines[lines.length - 1];
      const result: SmartRouterResult = JSON.parse(jsonLine);

      if (result.name && result.model_id) {
        console.log(
          `[SmartRouter] ${role}@${complexity} -> ${result.name} (${result.model_id})` +
          (result.thinking_mode ? " [THINKING]" : "")
        );

        return {
          modelId: result.model_id,
          provider: result.provider,
          thinking: result.thinking_mode,
          supportsStreaming: result.supports_streaming ?? true,
          source: "smartrouter"
        };
      }

      throw new Error("Invalid response from SmartRouter");

    } catch (error) {
      console.warn(`[SmartRouterBridge] Python bridge failed, using fallback:`, error);
      return this.getFallbackModel(role);
    }
  }

  private getFallbackModel(role: string): RoutingDecision {
    const model = getModelForRole(role);

    console.log(`[SmartRouterBridge] Fallback: ${role} -> ${model.id}`);

    return {
      modelId: model.id,
      provider: model.provider,
      thinking: model.thinking,
      supportsStreaming: true,
      source: "fallback"
    };
  }

  // =========================================================================
  // SECURITY: FILE PATH SANDBOXING (February 2026)
  // =========================================================================

  /**
   * Validates that a file path is within the allowed directories.
   * Prevents path traversal attacks and unauthorized file access.
   *
   * @param targetPath - The path to validate (relative to project root)
   * @returns true if path is allowed, false otherwise
   */
  private validatePath(targetPath: string): boolean {
    // Normalize the path to prevent bypass attempts
    const normalizedPath = path.normalize(targetPath).replace(/\\/g, "/");

    // Block path traversal attempts (../)
    if (normalizedPath.includes("..")) {
      console.error(
        `[Security Alert] Blocked path traversal attempt: ${targetPath}`
      );
      return false;
    }

    // Block absolute paths
    if (path.isAbsolute(targetPath)) {
      console.error(
        `[Security Alert] Blocked absolute path: ${targetPath}`
      );
      return false;
    }

    // Check if path starts with an allowed directory
    const isAllowed = ALLOWED_WRITE_PATHS.some((allowed) =>
      normalizedPath.startsWith(allowed)
    );

    if (!isAllowed) {
      console.error(
        `[Security Violation] Agent tried to access unauthorized path: ${targetPath}\n` +
        `Allowed paths: ${ALLOWED_WRITE_PATHS.join(", ")}`
      );
    }

    return isAllowed;
  }

  /**
   * Secure file write for agents with path validation.
   * Only allows writing to pre-approved directories.
   *
   * @param filePath - Relative path to write (must be in allowlist)
   * @param content - File content to write
   * @throws Error if path is not in allowlist
   */
  async safeWriteFile(filePath: string, content: string): Promise<boolean> {
    if (!this.validatePath(filePath)) {
      throw new Error(
        `Access Denied: Path "${filePath}" is not in the allowlist. ` +
        `Agents can only write to: ${ALLOWED_WRITE_PATHS.join(", ")}`
      );
    }

    // Path is allowed - send to Python bridge for physical write
    const response = await this.sendCommand("write_file", {
      path: filePath,
      content
    });

    if (response.status !== "ok") {
      throw new Error(response.message || "Failed to write file");
    }

    console.log(`[SmartRouterBridge] Wrote file: ${filePath}`);
    return true;
  }

  /**
   * Secure batch file write for generated project files.
   * Validates all paths before writing any files.
   *
   * @param files - Map of file paths to contents
   * @returns Map of paths to write success/failure
   */
  async safeWriteFiles(
    files: Record<string, string>
  ): Promise<Record<string, boolean>> {
    const results: Record<string, boolean> = {};

    // First, validate ALL paths before writing anything
    const invalidPaths: string[] = [];
    for (const filePath of Object.keys(files)) {
      if (!this.validatePath(filePath)) {
        invalidPaths.push(filePath);
      }
    }

    if (invalidPaths.length > 0) {
      throw new Error(
        `Access Denied: The following paths are not allowed:\n` +
        invalidPaths.map((p) => `  - ${p}`).join("\n") +
        `\n\nAllowed directories: ${ALLOWED_WRITE_PATHS.join(", ")}`
      );
    }

    // All paths valid - write files
    for (const [filePath, content] of Object.entries(files)) {
      try {
        await this.safeWriteFile(filePath, content);
        results[filePath] = true;
      } catch (error) {
        console.error(`[SmartRouterBridge] Failed to write ${filePath}:`, error);
        results[filePath] = false;
      }
    }

    return results;
  }

  /**
   * Check if a path would be allowed without actually writing.
   * Useful for pre-validation in pipeline planning phases.
   */
  isPathAllowed(targetPath: string): boolean {
    return this.validatePath(targetPath);
  }

  // =========================================================================
  // ESCALATION HELPERS (February 2026)
  // =========================================================================

  /**
   * Returns Mistral 7B v4 fallback for context escalation (32K Rule).
   * "The Runner Up" - handles larger contexts than Liquid LFM.
   */
  private getMistralFallback(contextTokens: number): RoutingDecision {
    return {
      modelId: "mistral-7b-v4-instruct",
      provider: "mistral",
      thinking: false,
      supportsStreaming: true,
      source: "context_escalation_32k_rule",
      confidence: 1.0,
      contextTokens
    };
  }

  /**
   * Returns Claude Opus 4.6 escalation for low confidence scenarios.
   * Enables thinking mode to improve reasoning quality.
   */
  private getOpusEscalation(contextTokens: number, originalConfidence: number): RoutingDecision {
    return {
      modelId: "claude-3-opus-20260210",
      provider: "anthropic",
      thinking: true, // Enable thinking mode for low confidence scenarios
      supportsStreaming: true,
      source: "low_confidence_escalation",
      confidence: originalConfidence,
      contextTokens
    };
  }

  /**
   * Returns GPT-5.2 Thinking for architecture escalation.
   * Used for complex system design tasks.
   */
  private getArchitectEscalation(contextTokens: number): RoutingDecision {
    return {
      modelId: "gpt-5.2-pro",
      provider: "openai",
      thinking: true,
      supportsStreaming: true,
      source: "low_confidence_escalation",
      confidence: 1.0,
      contextTokens
    };
  }

  // =========================================================================
  // TOKEN ESTIMATION (February 2026)
  // =========================================================================

  /**
   * Fast token estimation for pipeline context.
   * Average for TypeScript/Python code is 3.8 chars per token.
   */
  private estimateTokens(text: string | object): number {
    const str = typeof text === "string" ? text : JSON.stringify(text);
    // February 2026: accurate average for TypeScript/Python is 3.8 chars/token
    return Math.ceil(str.length / 3.8);
  }

  // =========================================================================
  // JIT SCHEMA FILTERING (February 2026)
  // =========================================================================

  /**
   * Just-In-Time schema filtering for context optimization.
   *
   * When passing Convex schema to agents, we don't need the full schema.
   * This method extracts only the table definitions relevant to the current task,
   * significantly reducing token usage.
   *
   * @param fullSchema - Complete Convex schema.ts content
   * @param requiredModels - List of model/table names needed (e.g., ["users", "todos"])
   * @returns Filtered schema containing only relevant table definitions
   *
   * @example
   * // Full schema has 50 tables, but we only need 2
   * const filtered = filterRelevantSchema(fullSchema, ["users", "sessions"]);
   * // Result: ~200 tokens instead of ~5000 tokens
   */
  filterRelevantSchema(fullSchema: string, requiredModels: string[]): string {
    // If no specific models requested, return full schema
    if (requiredModels.length === 0) {
      return fullSchema;
    }

    const lines = fullSchema.split("\n");
    let filteredSchema = "// JIT Filtered Schema (Optimized for Agent Context)\n";
    filteredSchema += "// Only includes tables: " + requiredModels.join(", ") + "\n\n";

    let isExtracting = false;
    let braceCount = 0;
    let currentTableName = "";

    // Normalize model names for case-insensitive matching
    const normalizedModels = requiredModels.map((m) => m.toLowerCase());

    for (const line of lines) {
      // Detect table definition start: tableName: defineTable({
      const tableMatch = line.match(/^\s*(\w+)\s*:\s*defineTable\s*\(/);

      if (tableMatch) {
        currentTableName = tableMatch[1].toLowerCase();
        const isRelevant = normalizedModels.some(
          (model) =>
            currentTableName.includes(model) || model.includes(currentTableName)
        );

        if (isRelevant) {
          isExtracting = true;
          braceCount = 0;
        }
      }

      // Extract relevant table definitions
      if (isExtracting) {
        filteredSchema += line + "\n";

        // Track brace depth to find end of table definition
        braceCount += (line.match(/{/g) || []).length;
        braceCount -= (line.match(/}/g) || []).length;

        // End of table definition (back to zero braces, line contains closing)
        if (braceCount <= 0 && line.includes(")")) {
          isExtracting = false;
          filteredSchema += "\n"; // Add spacing between tables
        }
      }

      // Also extract imports and schema setup lines
      if (
        !isExtracting &&
        (line.includes("import ") ||
          line.includes("export default") ||
          line.includes("defineSchema"))
      ) {
        filteredSchema += line + "\n";
      }
    }

    // Log optimization stats
    const originalTokens = this.estimateTokens(fullSchema);
    const filteredTokens = this.estimateTokens(filteredSchema);
    const savings = Math.round((1 - filteredTokens / originalTokens) * 100);

    console.log(
      `[JIT Schema] Filtered ${requiredModels.length} tables: ` +
        `${originalTokens} → ${filteredTokens} tokens (${savings}% reduction)`
    );

    return filteredSchema;
  }

  /**
   * Extract model names from architecture or requirements.
   * Useful for automatically determining which schema tables are needed.
   *
   * @param text - Requirements or architecture text to analyze
   * @returns Array of likely model/table names
   */
  extractRequiredModels(text: string): string[] {
    const models: Set<string> = new Set();

    // Common patterns that indicate model/table references
    const patterns = [
      /(?:table|model|entity|schema)\s+["']?(\w+)["']?/gi,
      /(?:create|read|update|delete|query)\s+(\w+)/gi,
      /(\w+)(?:Table|Model|Entity|Record)/g,
      /db\.(\w+)/g,
      /ctx\.db\.(\w+)/g,
      // Common data entity patterns
      /(?:with|and|manage|handle|store)\s+(\w+s?)\b/gi,
      /(\w+)\s+(?:management|system|data|records|entries)/gi,
    ];

    // Common words to exclude (not model names)
    const excludeWords = new Set([
      "the", "a", "an", "this", "that", "from", "to", "with", "and", "or",
      "for", "in", "on", "at", "by", "is", "are", "was", "were", "be",
      "have", "has", "had", "do", "does", "did", "will", "would", "could",
      "should", "may", "might", "must", "shall", "can", "need", "dare",
      "create", "read", "update", "delete", "query", "manage", "handle",
      "store", "fetch", "get", "set", "add", "remove", "build", "make",
      "system", "data", "records", "entries", "management", "app", "application",
    ]);

    for (const pattern of patterns) {
      let match;
      // Reset lastIndex for global patterns
      pattern.lastIndex = 0;
      while ((match = pattern.exec(text)) !== null) {
        const modelName = match[1].toLowerCase();
        // Filter out excluded words and very short words
        if (!excludeWords.has(modelName) && modelName.length > 2) {
          // Remove trailing 's' to normalize singular/plural
          const normalized = modelName.endsWith("s") && modelName.length > 4
            ? modelName.slice(0, -1)
            : modelName;
          models.add(normalized);
          // Also add the original if different
          if (normalized !== modelName) {
            models.add(modelName);
          }
        }
      }
    }

    return Array.from(models);
  }

  /**
   * Prepare optimized context for backend agent.
   * Combines schema filtering with other context optimizations.
   */
  prepareBackendContext(
    fullSchema: string,
    requirements: string,
    existingCode?: Record<string, string>
  ): {
    schema: string;
    detectedModels: string[];
    tokensBefore: number;
    tokensAfter: number;
  } {
    // Auto-detect required models from requirements
    const detectedModels = this.extractRequiredModels(requirements);

    // Also check existing code for model references
    if (existingCode) {
      const codeText = Object.values(existingCode).join("\n");
      const codeModels = this.extractRequiredModels(codeText);
      codeModels.forEach((m) => detectedModels.push(m));
    }

    // Remove duplicates
    const uniqueModels = [...new Set(detectedModels)];

    // Filter schema
    const tokensBefore = this.estimateTokens(fullSchema);
    const filteredSchema = this.filterRelevantSchema(fullSchema, uniqueModels);
    const tokensAfter = this.estimateTokens(filteredSchema);

    return {
      schema: filteredSchema,
      detectedModels: uniqueModels,
      tokensBefore,
      tokensAfter,
    };
  }

  // =========================================================================
  // CONTEXT SIZE CALCULATION
  // =========================================================================

  /**
   * Calculate total context size from ProjectState.
   * Used to determine if context escalation is needed (Liquid LFM → Mistral).
   */
  calculateStateContext(state: {
    frontend_code?: Record<string, string>;
    backend_code?: Record<string, string>;
    requirements?: string;
    architecture?: unknown;
    tests?: Record<string, string>;
    messages?: unknown[];
  }): number {
    let totalChars = 0;

    // Sum all generated code from Frontend and Backend
    if (state.frontend_code) {
      totalChars += JSON.stringify(state.frontend_code).length;
    }
    if (state.backend_code) {
      totalChars += JSON.stringify(state.backend_code).length;
    }
    if (state.tests) {
      totalChars += JSON.stringify(state.tests).length;
    }

    // Add requirements and architecture
    if (state.requirements) {
      totalChars += state.requirements.length;
    }
    if (state.architecture) {
      totalChars += JSON.stringify(state.architecture).length;
    }

    // Add message history (if present)
    if (state.messages) {
      totalChars += JSON.stringify(state.messages).length;
    }

    // Convert characters to tokens (3.8 chars per token for code)
    return Math.ceil(totalChars / 3.8);
  }

  /**
   * Get model for pipeline phase with automatic context-aware routing.
   * Automatically calculates context size and escalates if needed.
   */
  async getModelForPipelinePhase(
    phase: string,
    complexity: number,
    state?: {
      frontend_code?: Record<string, string>;
      backend_code?: Record<string, string>;
      requirements?: string;
      architecture?: unknown;
      tests?: Record<string, string>;
      messages?: unknown[];
    }
  ): Promise<RoutingDecision> {
    // Calculate context length from state if provided
    const contextLength = state ? this.calculateStateContext(state) : 0;

    console.log(
      `[SmartRouter] Phase: ${phase}, Complexity: ${complexity}, ` +
      `Context: ${contextLength} tokens`
    );

    return this.getModelForTaskAsync(phase, complexity, contextLength);
  }

  /**
   * Main routing method for LangGraph pipeline steps (February 2026).
   *
   * Features:
   * 1. The 32K Rule - auto-escalates to Mistral 7B when context exceeds Liquid LFM capacity
   * 2. Speculative Execution - enables predictive model selection
   * 3. Confidence Escalation - escalates to Opus 4.6 when confidence < 85%
   *
   * @param state - Full ProjectState from LangGraph
   * @returns RoutingDecision with selected model and confidence score
   */
  async getModelForPipelineStep(state: {
    current_phase: string;
    complexity?: number;
    iteration?: number;
    error_count?: number;
    frontend_code?: Record<string, string>;
    backend_code?: Record<string, string>;
    requirements?: string;
    architecture?: unknown;
    tests?: Record<string, string>;
    messages?: unknown[];
    tags?: string[];
    /** For testing only: override calculated confidence (0.0-1.0) */
    force_confidence?: number;
  }): Promise<RoutingDecision> {
    const contextSize = this.calculateStateContext(state);
    const phase = state.current_phase;
    const complexity = state.complexity ?? 5;
    const iteration = state.iteration ?? 0;

    console.log(
      `[SmartRouter] Pipeline step: phase=${phase}, complexity=${complexity}, ` +
      `context=${contextSize} tokens, iteration=${iteration}`
    );

    // Constants
    const LIQUID_LFM_CONTEXT_LIMIT = 32000;
    const CONFIDENCE_THRESHOLD = 0.85;
    const ORCHESTRATION_PHASES = ["dispatcher", "aggregator", "finalize"];

    // =========================================================================
    // RULE 1: The 32K Rule (Context Escalation)
    // =========================================================================
    // Orchestration phases use Liquid LFM by default, but auto-escalate to
    // Mistral 7B v4 when context exceeds 32K tokens
    if (ORCHESTRATION_PHASES.includes(phase) && contextSize > LIQUID_LFM_CONTEXT_LIMIT) {
      console.warn(
        `[SmartRouter] Context size (${contextSize}) exceeds Liquid LFM limit (${LIQUID_LFM_CONTEXT_LIMIT}). ` +
        `Auto-switching to Mistral 7B v4.`
      );

      return this.getMistralFallback(contextSize);
    }

    // =========================================================================
    // RULE 2: Speculative Execution via Python/Convex
    // =========================================================================
    if (this.isInteractiveMode && this.isReady) {
      try {
        const params: Record<string, unknown> = {
          current_phase: phase,
          complexity,
          iteration,
          error_count: state.error_count ?? 0,
          context_length: contextSize,
          tags: state.tags ?? [],
          enable_speculation: true
        };

        // Add force_confidence for testing (if provided)
        if (state.force_confidence !== undefined) {
          params.force_confidence = state.force_confidence;
        }

        const response = await this.sendCommand("get_pipeline_model", params);

        if (response.status === "ok" && response.data) {
          const decision = response.data;
          const confidence = decision.confidence ?? 1.0;

          // =================================================================
          // RULE 3: Confidence Escalation (February 2026)
          // =================================================================
          // If confidence is below threshold and not already using premium model,
          // escalate to Claude Opus 4.6 with thinking mode enabled
          if (
            confidence < CONFIDENCE_THRESHOLD &&
            decision.model_id !== "claude-3-opus-20260210"
          ) {
            console.warn(
              `[SmartRouter] Low confidence (${(confidence * 100).toFixed(1)}%) ` +
              `for ${decision.model_id}. Escalating to Opus 4.6 with thinking mode.`
            );

            return this.getOpusEscalation(contextSize, confidence);
          }

          return {
            modelId: decision.model_id,
            provider: decision.provider,
            thinking: decision.thinking_mode ?? false,
            supportsStreaming: decision.supports_streaming ?? true,
            source: decision.source ?? "speculative",
            confidence,
            contextTokens: contextSize
          };
        }
      } catch (error) {
        console.warn("[SmartRouterBridge] Pipeline routing failed:", error);
      }
    }

    // Fallback to standard routing with context
    return this.getModelForTaskAsync(phase, complexity, contextSize);
  }

  // =========================================================================
  // UTILITY METHODS
  // =========================================================================

  listModels(): { all: string[]; enabled: string[] } {
    try {
      const cmd = `${this.pythonPath} "${this.bridgePath}" list_models`;
      const output = execSync(cmd, {
        cwd: this.backendRoot,
        encoding: "utf-8",
        env: { ...process.env, PYTHONPATH: this.backendRoot },
        timeout: 10000
      });

      const lines = output.trim().split("\n");
      const jsonLine = lines[lines.length - 1];
      const result = JSON.parse(jsonLine);

      return {
        all: result.all || [],
        enabled: result.enabled || result.all || []
      };

    } catch (error) {
      console.warn(`[SmartRouterBridge] list_models failed:`, error);
      const models = Object.keys(MODEL_REGISTRY);
      return { all: models, enabled: models };
    }
  }

  getConfig(): Record<string, unknown> {
    try {
      const cmd = `${this.pythonPath} "${this.bridgePath}" config`;
      const output = execSync(cmd, {
        cwd: this.backendRoot,
        encoding: "utf-8",
        env: { ...process.env, PYTHONPATH: this.backendRoot },
        timeout: 10000
      });

      const lines = output.trim().split("\n");
      const jsonLine = lines[lines.length - 1];
      return JSON.parse(jsonLine);

    } catch (error) {
      console.warn(`[SmartRouterBridge] config failed:`, error);
      return {
        error: "Bridge unavailable",
        fallback_registry: MODEL_REGISTRY
      };
    }
  }

  isAvailable(): boolean {
    try {
      const cmd = `${this.pythonPath} "${this.bridgePath}" list_models`;
      execSync(cmd, {
        cwd: this.backendRoot,
        encoding: "utf-8",
        env: { ...process.env, PYTHONPATH: this.backendRoot },
        timeout: 5000,
        stdio: ["pipe", "pipe", "pipe"]
      });
      return true;
    } catch {
      return false;
    }
  }

  isInteractive(): boolean {
    return this.isInteractiveMode && this.isReady;
  }

  // =========================================================================
  // LEGACY COMPATIBILITY METHODS
  // =========================================================================

  async routeMessage(message: string): Promise<{
    model: SmartRouterModel;
    role: string;
    complexity: number;
  }> {
    const { role, complexity } = this.analyzeMessage(message);
    const decision = await this.getModelForTaskAsync(role, complexity);

    const model: SmartRouterModel = {
      name: role === "architect" ? "gpt" : "claude-opus",
      model_id: decision.modelId,
      provider: decision.provider as "anthropic" | "openai" | "google" | "local",
      max_tokens: 16384,
      temperature: 0.7
    };

    return { model, role, complexity };
  }

  getModelForRole(role: string, complexity: number): SmartRouterModel {
    const decision = this.getModelForTask(role, complexity);

    return {
      name: role,
      model_id: decision.modelId,
      provider: decision.provider as "anthropic" | "openai" | "google" | "local",
      max_tokens: 16384,
      temperature: 0.7
    };
  }

  private analyzeMessage(message: string): { role: string; complexity: number } {
    let role = "coding";
    let complexity = 5;

    const patterns: Record<string, RegExp[]> = {
      architect: [/architect|design.*system|infrastructure|scale/i],
      reviewer: [/review|audit|check.*code|security/i],
      researcher: [/research|investigate|analyze|explain/i],
      coding: [/write.*code|implement|fix|debug|refactor/i]
    };

    for (const [r, regexes] of Object.entries(patterns)) {
      if (regexes.some(p => p.test(message))) {
        role = r;
        break;
      }
    }

    complexity = Math.min(10, Math.max(1, Math.floor(message.length / 200) + 3));

    return { role, complexity };
  }
}

// ============================================================================
// EXPORTS
// ============================================================================

export const smartRouter = new SmartRouterBridge();

export function getModelForTask(role: string, complexity: number): RoutingDecision {
  return smartRouter.getModelForTask(role, complexity);
}

export async function getModelForTaskAsync(
  role: string,
  complexity: number,
  contextLength?: number
): Promise<RoutingDecision> {
  return smartRouter.getModelForTaskAsync(role, complexity, contextLength);
}

/**
 * Get model for pipeline phase with automatic context-aware routing.
 * Calculates context size from state and escalates to larger models if needed.
 */
export async function getModelForPipelinePhase(
  phase: string,
  complexity: number,
  state?: {
    frontend_code?: Record<string, string>;
    backend_code?: Record<string, string>;
    requirements?: string;
    architecture?: unknown;
    tests?: Record<string, string>;
    messages?: unknown[];
  }
): Promise<RoutingDecision> {
  return smartRouter.getModelForPipelinePhase(phase, complexity, state);
}

/**
 * Calculate estimated token count for a ProjectState.
 */
export function calculateStateContext(state: {
  frontend_code?: Record<string, string>;
  backend_code?: Record<string, string>;
  requirements?: string;
  architecture?: unknown;
  tests?: Record<string, string>;
  messages?: unknown[];
}): number {
  return smartRouter.calculateStateContext(state);
}

// ============================================================================
// JIT SCHEMA FILTERING EXPORTS (February 2026)
// ============================================================================

/**
 * Filter Convex schema to include only relevant table definitions.
 * Reduces token usage by 80-95% for large schemas.
 *
 * @example
 * const filtered = filterRelevantSchema(fullSchema, ["users", "todos"]);
 */
export function filterRelevantSchema(
  fullSchema: string,
  requiredModels: string[]
): string {
  return smartRouter.filterRelevantSchema(fullSchema, requiredModels);
}

/**
 * Auto-detect model/table names from requirements or code text.
 */
export function extractRequiredModels(text: string): string[] {
  return smartRouter.extractRequiredModels(text);
}

/**
 * Prepare optimized context for backend agent with JIT schema filtering.
 * Automatically detects required models and filters schema.
 */
export function prepareBackendContext(
  fullSchema: string,
  requirements: string,
  existingCode?: Record<string, string>
): {
  schema: string;
  detectedModels: string[];
  tokensBefore: number;
  tokensAfter: number;
} {
  return smartRouter.prepareBackendContext(fullSchema, requirements, existingCode);
}

/**
 * Main routing function for LangGraph pipeline steps.
 * Implements the 32K Rule for automatic context-based model escalation.
 *
 * @example
 * const decision = await getModelForPipelineStep({
 *   current_phase: "aggregator",
 *   complexity: 5,
 *   frontend_code: { "App.tsx": "..." },
 *   backend_code: { "api.py": "..." }
 * });
 */
export async function getModelForPipelineStep(state: {
  current_phase: string;
  complexity?: number;
  iteration?: number;
  error_count?: number;
  frontend_code?: Record<string, string>;
  backend_code?: Record<string, string>;
  requirements?: string;
  architecture?: unknown;
  tests?: Record<string, string>;
  messages?: unknown[];
  tags?: string[];
  /** For testing only: override calculated confidence (0.0-1.0) */
  force_confidence?: number;
}): Promise<RoutingDecision> {
  return smartRouter.getModelForPipelineStep(state);
}

export function createSmartRouter(
  config?: string | { bridgePath?: string; pythonPath?: string; configPath?: string }
): SmartRouterBridge {
  if (typeof config === "string") {
    return new SmartRouterBridge(config);
  }
  return new SmartRouterBridge(config?.pythonPath);
}

// ============================================================================
// SECURITY EXPORTS (February 2026)
// ============================================================================

/**
 * Securely write a file with path validation.
 * Only allows writing to pre-approved directories (ALLOWED_WRITE_PATHS).
 *
 * @throws Error if path is not in the allowlist
 */
export async function safeWriteFile(
  filePath: string,
  content: string
): Promise<boolean> {
  return smartRouter.safeWriteFile(filePath, content);
}

/**
 * Securely write multiple files with path validation.
 * Validates ALL paths before writing ANY files.
 *
 * @throws Error if any path is not in the allowlist
 */
export async function safeWriteFiles(
  files: Record<string, string>
): Promise<Record<string, boolean>> {
  return smartRouter.safeWriteFiles(files);
}

/**
 * Check if a path is allowed for writing without actually writing.
 * Useful for pre-validation in pipeline planning phases.
 */
export function isPathAllowed(targetPath: string): boolean {
  return smartRouter.isPathAllowed(targetPath);
}

/**
 * The list of directories agents are allowed to write to.
 */
export { ALLOWED_WRITE_PATHS };

export { VOS3_BACKEND_ROOT, ROUTER_BRIDGE_PATH, MCP_SERVER_ROOT };
