// types.ts
// VOS3 Multi-Agent Pipeline Types
// ================================

// ============================================================================
// PIPELINE PHASES
// ============================================================================

export type PipelinePhase =
  | 'architect'   // High-level design (GPT-5.2-Pro)
  | 'expander'    // Expand to file specs (Gemini 3 Pro)
  | 'dispatcher'  // Fan-out to parallel branches
  | 'frontend'    // UI/UX implementation (Claude Opus 4.6)
  | 'backend'     // API and logic (Claude Opus 4.6)
  | 'aggregator'  // Merge parallel results
  | 'tester'      // Test generation (GPT-5.2)
  | 'reviewer'    // Cross-model verification (GPT-5.2-Pro)
  | 'finalize';   // Final assembly

// ============================================================================
// PROJECT STATE (LangGraph State)
// ============================================================================

export interface ProjectState {
  /** Message history for context */
  messages: Message[];

  /** Original user requirements */
  requirements: string;

  /** Architect output: high-level design */
  architecture?: ArchitectureBlueprint;

  /** Expander output: file specifications queue */
  execution_queue?: FileSpecification[];

  /** Frontend agent output */
  frontend_code?: Record<string, string>;

  /** Backend agent output */
  backend_code?: Record<string, string>;

  /** Tester agent output */
  tests?: Record<string, string>;

  /** Reviewer agent output */
  review_results?: ReviewResults;

  /** All merged files */
  all_files?: Record<string, string>;

  /** Current pipeline phase */
  current_phase: PipelinePhase;

  /** Retry iteration (0 = first pass) */
  iteration: number;

  /** Error count for escalation */
  error_count: number;

  /** Previous models used (for diversity) */
  previous_models: string[];

  /** Context tags for routing */
  tags: string[];

  /** Metadata */
  metadata?: ProjectMetadata;
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp?: Date;
  model?: string;
}

// ============================================================================
// ARCHITECT OUTPUT
// ============================================================================

export interface ArchitectureBlueprint {
  /** Technology stack */
  tech_stack: string[];

  /** UI components to generate */
  components: ComponentSpec[];

  /** API endpoints */
  api_endpoints: EndpointSpec[];

  /** Data models */
  data_models: DataModelSpec[];

  /** Project structure */
  structure: {
    frontend_dir: string;
    backend_dir: string;
    shared_dir?: string;
  };

  /** Dependencies */
  dependencies: {
    frontend: Record<string, string>;
    backend: Record<string, string>;
  };

  /** Summary */
  summary: string;
}

export interface ComponentSpec {
  name: string;
  path: string;
  type: 'page' | 'component' | 'hook' | 'util' | 'context';
  description: string;
  dependencies: string[];
  props?: Record<string, string>;
}

export interface EndpointSpec {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  path: string;
  description: string;
  request_body?: Record<string, unknown>;
  response: Record<string, unknown>;
  auth_required: boolean;
}

export interface DataModelSpec {
  name: string;
  fields: Record<string, FieldSpec>;
  relations?: RelationSpec[];
}

export interface FieldSpec {
  type: string;
  required: boolean;
  default?: unknown;
  description?: string;
}

export interface RelationSpec {
  type: 'one-to-one' | 'one-to-many' | 'many-to-many';
  target: string;
  field: string;
}

// ============================================================================
// EXPANDER OUTPUT
// ============================================================================

export interface FileSpecification {
  /** File path relative to project root */
  path: string;

  /** Purpose of this file */
  purpose: string;

  /** Which phase generates this file */
  phase: 'frontend' | 'backend' | 'shared';

  /** Files this depends on */
  dependencies: string[];

  /** Interfaces this file exports */
  exports: string[];

  /** Priority (lower = generate first) */
  priority: number;

  /** Estimated complexity 1-10 */
  complexity: number;
}

// ============================================================================
// REVIEWER OUTPUT
// ============================================================================

export interface ReviewResults {
  /** Overall approval */
  approved: boolean;

  /** Review score 0-100 */
  score: number;

  /** Issues found */
  issues: ReviewIssue[];

  /** Suggestions for improvement */
  suggestions: string[];

  /** Security audit results */
  security: SecurityAudit;

  /** Reviewer model */
  reviewer_model: string;
}

export interface ReviewIssue {
  severity: 'critical' | 'error' | 'warning' | 'info';
  category: 'security' | 'quality' | 'performance' | 'style' | 'logic';
  file?: string;
  line?: number;
  message: string;
  suggestion?: string;
  auto_fixable: boolean;
}

export interface SecurityAudit {
  passed: boolean;
  vulnerabilities: Vulnerability[];
}

export interface Vulnerability {
  type: string;  // e.g., "XSS", "SQL Injection", "CSRF"
  severity: 'critical' | 'high' | 'medium' | 'low';
  file: string;
  line?: number;
  description: string;
  remediation: string;
}

// ============================================================================
// FINAL OUTPUT
// ============================================================================

export interface GeneratedProject {
  /** All generated files */
  files: Record<string, string>;

  /** Human-readable summary */
  summary: string;

  /** Project metadata */
  metadata: ProjectMetadata;

  /** Review results */
  review: ReviewResults;
}

export interface ProjectMetadata {
  /** Models used in generation */
  models_used: string[];

  /** Total tokens consumed */
  total_tokens: {
    input: number;
    output: number;
  };

  /** Total cost in USD */
  total_cost: number;

  /** Number of iterations */
  iterations: number;

  /** Generation time in ms */
  duration_ms: number;

  /** Phases executed */
  phases_executed: PipelinePhase[];

  /** Timestamp */
  created_at: Date;
}

// ============================================================================
// MODEL ROUTING
// ============================================================================

export interface RoutingRequest {
  role: string;
  complexity: number;
  state?: Partial<ProjectState>;
}

export interface RoutingDecision {
  modelId: string;
  provider: 'openai' | 'anthropic' | 'google' | 'local';
  thinking: boolean;
  supportsStreaming: boolean;
  source: 'convex' | 'smartrouter' | 'escalation' | 'fallback';

  /** Routing metadata */
  meta?: {
    phase: PipelinePhase;
    iteration: number;
    effectiveComplexity: number;
    escalated: boolean;
  };
}

/** Model assignments per phase */
export const PHASE_MODEL_MAP: Record<PipelinePhase, string> = {
  architect: 'gpt-5.2-pro',
  expander: 'gemini-3-pro-latest',
  dispatcher: 'liquid-lfm-2.5-1.2b',  // Liquid LFM - 359 tok/s, SOTA SLM
  frontend: 'claude-3-opus-20260210',
  backend: 'claude-3-opus-20260210',
  aggregator: 'liquid-lfm-2.5-1.2b',  // Liquid LFM - fast merging
  tester: 'gpt-5.2',
  reviewer: 'gpt-5.2-pro',
  finalize: 'liquid-lfm-2.5-1.2b'     // Liquid LFM - final assembly
};

// ============================================================================
// STREAMING
// ============================================================================

export interface StreamOptions {
  system?: string;
  maxTokens?: number;
  temperature?: number;
  stopSequences?: string[];
  state?: Partial<ProjectState>;
}

export interface StreamChunk {
  chunk: string;
  done: boolean;
  model?: string;
  phase?: PipelinePhase;
}

// ============================================================================
// DISPATCHER (Parallel Execution)
// ============================================================================

export interface SendCommand {
  target: PipelinePhase;
  state: ProjectState;
}

export interface DispatchResult {
  branches: SendCommand[];
  parallel: boolean;
}

// ============================================================================
// COST TRACKING
// ============================================================================

export interface CostRecord {
  timestamp: Date;
  model: string;
  provider: string;
  phase: PipelinePhase;
  iteration: number;
  tokens: {
    input: number;
    output: number;
  };
  cost: number;
  cached: boolean;
}

export interface CostSummary {
  totalCost: number;
  totalTokens: { input: number; output: number };
  byModel: Record<string, number>;
  byPhase: Record<PipelinePhase, number>;
  cacheSavings: number;
  estimatedSavings: number;  // vs. always using premium
}

// ============================================================================
// CROSS-MODEL VERIFICATION
// ============================================================================

export interface CrossModelConfig {
  /** Model that generated the code */
  writer: string;

  /** Model that reviews the code */
  reviewer: string;

  /** Enable cross-model pattern */
  enabled: boolean;
}

export const CROSS_MODEL_PAIRS: CrossModelConfig[] = [
  { writer: 'claude-3-opus-20260210', reviewer: 'gpt-5.2-pro', enabled: true },
  { writer: 'gpt-5.2-pro', reviewer: 'claude-3-opus-20260210', enabled: true },
];
