// backend/mcp-server/src/orchestration/pipeline-runner.ts

import { SmartRouterBridge } from './smart-router-bridge.js';
import { EventEmitter } from 'events';

// ============================================================================
// TYPES
// ============================================================================

interface ProjectBlueprint {
  name: string;
  architecture: string;
  frontend: TaskDefinition;
  backend: TaskDefinition;
  database?: TaskDefinition;
}

interface TaskDefinition {
  complexity: number;
  force_confidence?: number;
  modules: string[];
  dependencies: string[];
}

interface DispatchedTasks {
  frontend: TaskDefinition;
  backend: TaskDefinition;
}

// ============================================================================
// MULTI-STREAM MANAGER (Placeholder until ui/streaming-manager exists)
// ============================================================================

class MultiStreamManager extends EventEmitter {
  private streams: Map<string, { status: string; chunks: string[] }> = new Map();

  createStream(id: string): void {
    this.streams.set(id, { status: 'pending', chunks: [] });
    console.log(`[StreamManager] Created stream: ${id}`);
  }

  appendChunk(id: string, chunk: string): void {
    const stream = this.streams.get(id);
    if (stream) {
      stream.chunks.push(chunk);
      this.emit('chunk', { id, chunk });
    }
  }

  completeStream(id: string): void {
    const stream = this.streams.get(id);
    if (stream) {
      stream.status = 'completed';
      this.emit('complete', { id });
      console.log(`[StreamManager] Completed stream: ${id}`);
    }
  }

  getStreamContent(id: string): string {
    return this.streams.get(id)?.chunks.join('') || '';
  }
}

// ============================================================================
// PIPELINE RUNNER
// ============================================================================

export class PipelineRunner extends EventEmitter {
  private router: SmartRouterBridge;
  private streamManager: MultiStreamManager;
  private projectName: string = '';
  private mode: 'sequential' | 'parallel' = 'parallel';
  private uiMode: 'ghost-stream' | 'standard' = 'ghost-stream';

  constructor(options?: { mode?: 'sequential' | 'parallel'; ui?: 'ghost-stream' | 'standard' }) {
    super();
    this.router = new SmartRouterBridge();
    this.streamManager = new MultiStreamManager();
    this.mode = options?.mode || 'parallel';
    this.uiMode = options?.ui || 'ghost-stream';
  }

  async runProject(projectName: string): Promise<void> {
    this.projectName = projectName;
    console.log(`\n${'='.repeat(60)}`);
    console.log(`[VOS3] Starting Project: ${projectName}`);
    console.log(`[VOS3] Mode: ${this.mode} | UI: ${this.uiMode}`);
    console.log(`${'='.repeat(60)}\n`);

    // Initialize SmartRouter interactive mode
    const routerReady = await this.router.startInteractive();
    if (!routerReady) {
      console.warn('[VOS3] SmartRouter interactive mode unavailable, using CLI fallback');
    }

    try {
      // 1. Architect Phase (GPT-5.2 Thinking)
      const blueprint = await this.generateBlueprint(projectName);

      // 2. Dispatch Phase (Liquid LFM - fast)
      const tasks = this.dispatchTasks(blueprint);

      // 3. Parallel Execution with Ghost Streaming
      if (this.mode === 'parallel') {
        await Promise.all([
          this.executeStream('frontend', tasks.frontend),
          this.executeStream('backend', tasks.backend)
        ]);
      } else {
        await this.executeStream('frontend', tasks.frontend);
        await this.executeStream('backend', tasks.backend);
      }

      // 4. Final Aggregation
      await this.finalizeProject();

      console.log(`\n${'='.repeat(60)}`);
      console.log(`[VOS3] Project "${projectName}" completed successfully!`);
      console.log(`${'='.repeat(60)}\n`);

    } finally {
      await this.router.stopInteractive();
    }
  }

  // =========================================================================
  // PHASE 1: ARCHITECT (GPT-5.2 Thinking)
  // =========================================================================

  private async generateBlueprint(projectName: string): Promise<ProjectBlueprint> {
    console.log('\n[Phase 1] Generating Architecture Blueprint...');

    const decision = await this.router.getModelForPipelineStep({
      current_phase: 'architect',
      complexity: 9, // High complexity for architecture
      tags: ['architecture', 'system-design']
    });

    console.log(`[Architect] Using ${decision.modelId} (source: ${decision.source})`);

    // Simulate blueprint generation
    const blueprint: ProjectBlueprint = {
      name: projectName,
      architecture: 'microservices',
      frontend: {
        complexity: 7,
        modules: ['components', 'hooks', 'pages', 'utils'],
        dependencies: ['react', 'next.js', 'tailwind']
      },
      backend: {
        complexity: 8,
        modules: ['api', 'services', 'models', 'middleware'],
        dependencies: ['fastapi', 'convex', 'langchain']
      }
    };

    console.log(`[Architect] Blueprint generated: ${blueprint.architecture} architecture`);
    console.log(`[Architect] Frontend modules: ${blueprint.frontend.modules.join(', ')}`);
    console.log(`[Architect] Backend modules: ${blueprint.backend.modules.join(', ')}`);

    this.emit('blueprint', blueprint);
    return blueprint;
  }

  // =========================================================================
  // PHASE 2: DISPATCH (Liquid LFM - Fast)
  // =========================================================================

  private dispatchTasks(blueprint: ProjectBlueprint): DispatchedTasks {
    console.log('\n[Phase 2] Dispatching Tasks...');

    const tasks: DispatchedTasks = {
      frontend: blueprint.frontend,
      backend: blueprint.backend
    };

    console.log(`[Dispatch] Frontend task: complexity=${tasks.frontend.complexity}`);
    console.log(`[Dispatch] Backend task: complexity=${tasks.backend.complexity}`);

    this.emit('dispatch', tasks);
    return tasks;
  }

  // =========================================================================
  // PHASE 3: PARALLEL EXECUTION (Ghost Streaming)
  // =========================================================================

  private async executeStream(id: string, task: TaskDefinition): Promise<void> {
    console.log(`\n[Phase 3] Executing ${id} stream...`);

    // SmartRouter selects optimal model with Confidence Gate
    const decision = await this.router.getModelForPipelineStep({
      current_phase: id,
      complexity: task.complexity,
      force_confidence: task.force_confidence // Support for test overrides
    });

    const confidenceStr = decision.confidence
      ? `${(decision.confidence * 100).toFixed(1)}%`
      : 'N/A';

    console.log(`[${id}] Routing to ${decision.modelId}`);
    console.log(`[${id}] Provider: ${decision.provider} | Thinking: ${decision.thinking}`);
    console.log(`[${id}] Confidence: ${confidenceStr} | Source: ${decision.source}`);

    // Create stream for this task
    this.streamManager.createStream(id);

    // Simulate code generation streaming
    const modules = task.modules || ['module1', 'module2'];
    for (const module of modules) {
      await this.simulateModuleGeneration(id, module);
    }

    this.streamManager.completeStream(id);
    this.emit('stream-complete', { id, decision });
  }

  private async simulateModuleGeneration(streamId: string, moduleName: string): Promise<void> {
    // Simulate streaming chunks
    const chunks = [
      `// ${moduleName}.ts\n`,
      `export function ${moduleName}() {\n`,
      `  // Generated by VOS3 Pipeline\n`,
      `  return { status: 'ok' };\n`,
      `}\n\n`
    ];

    for (const chunk of chunks) {
      this.streamManager.appendChunk(streamId, chunk);
      // Small delay to simulate streaming
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  }

  // =========================================================================
  // PHASE 4: AGGREGATION
  // =========================================================================

  private async finalizeProject(): Promise<void> {
    console.log('\n[Phase 4] Finalizing Project...');

    const decision = await this.router.getModelForPipelineStep({
      current_phase: 'aggregator',
      complexity: 6
    });

    console.log(`[Aggregator] Using ${decision.modelId}`);

    // Collect all generated code
    const frontendCode = this.streamManager.getStreamContent('frontend');
    const backendCode = this.streamManager.getStreamContent('backend');

    console.log(`[Aggregator] Frontend code: ${frontendCode.length} chars`);
    console.log(`[Aggregator] Backend code: ${backendCode.length} chars`);

    this.emit('finalize', {
      frontend: frontendCode,
      backend: backendCode
    });
  }
}

// ============================================================================
// CLI ENTRY POINT
// ============================================================================

async function main() {
  const args = process.argv.slice(2);

  // Parse CLI arguments
  let projectName = 'Demo Project';
  let mode: 'sequential' | 'parallel' = 'parallel';
  let ui: 'ghost-stream' | 'standard' = 'ghost-stream';

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--project' && args[i + 1]) {
      projectName = args[i + 1];
      i++;
    } else if (args[i] === '--mode' && args[i + 1]) {
      mode = args[i + 1] as 'sequential' | 'parallel';
      i++;
    } else if (args[i] === '--ui' && args[i + 1]) {
      ui = args[i + 1] as 'ghost-stream' | 'standard';
      i++;
    }
  }

  const runner = new PipelineRunner({ mode, ui });

  // Subscribe to events
  runner.on('blueprint', (bp) => {
    console.log(`\n📐 Blueprint Event: ${bp.name}`);
  });

  runner.on('stream-complete', ({ id, decision }) => {
    console.log(`\n✅ Stream Complete: ${id} -> ${decision.modelId}`);
  });

  runner.on('finalize', (result) => {
    console.log(`\n🎉 Project Finalized!`);
  });

  // Run the pipeline
  await runner.runProject(projectName);
}

// Run if executed directly
main().catch(console.error);

export { MultiStreamManager };
