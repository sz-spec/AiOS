/**
 * Multi-Stream Manager for VOS3 Pipeline UI
 * ==========================================
 * Manages parallel streaming outputs from Frontend, Backend, and Aggregator agents.
 * Enables real-time UI updates via WebSocket/SSE.
 *
 * February 2026 - Supports parallel pipeline execution visualization.
 */

import { EventEmitter } from "events";

// ============================================================================
// TYPES
// ============================================================================

export type StreamId = "frontend" | "backend" | "aggregator" | "architect" | "tester" | "reviewer";

export interface StreamChunk {
  /** Which agent/phase is streaming */
  streamId: StreamId;
  /** The content chunk */
  content: string;
  /** Timestamp when chunk was received */
  timestamp: number;
  /** Optional metadata */
  metadata?: {
    model?: string;
    tokens?: number;
    phase?: string;
  };
}

export interface UIUpdate {
  /** Stream identifier */
  id: StreamId;
  /** Full accumulated text so far */
  fullText: string;
  /** Just the new content (delta) */
  delta: string;
  /** Total characters received */
  totalChars: number;
  /** Timestamp of last update */
  lastUpdate: number;
  /** Stream status */
  status: "streaming" | "complete" | "error";
}

export interface StreamStats {
  streamId: StreamId;
  startTime: number;
  endTime?: number;
  totalChunks: number;
  totalChars: number;
  averageChunkSize: number;
  tokensPerSecond?: number;
}

// ============================================================================
// MULTI-STREAM MANAGER
// ============================================================================

export class MultiStreamManager extends EventEmitter {
  /** Accumulated content for each stream */
  private activeStreams: Map<StreamId, string> = new Map();

  /** Statistics for each stream */
  private streamStats: Map<StreamId, StreamStats> = new Map();

  /** Stream status tracking */
  private streamStatus: Map<StreamId, "streaming" | "complete" | "error"> = new Map();

  constructor() {
    super();
  }

  // =========================================================================
  // STREAM HANDLING
  // =========================================================================

  /**
   * Handle incoming chunk from any stream.
   * Accumulates content and emits UI update events.
   */
  handleChunk(chunk: StreamChunk): void {
    const { streamId, content, timestamp } = chunk;

    // Initialize stream if first chunk
    if (!this.activeStreams.has(streamId)) {
      this.activeStreams.set(streamId, "");
      this.streamStatus.set(streamId, "streaming");
      this.streamStats.set(streamId, {
        streamId,
        startTime: timestamp,
        totalChunks: 0,
        totalChars: 0,
        averageChunkSize: 0,
      });

      this.emit("stream-start", { streamId, timestamp });
    }

    // Accumulate content
    const current = this.activeStreams.get(streamId) || "";
    const updated = current + content;
    this.activeStreams.set(streamId, updated);

    // Update statistics
    const stats = this.streamStats.get(streamId)!;
    stats.totalChunks++;
    stats.totalChars += content.length;
    stats.averageChunkSize = stats.totalChars / stats.totalChunks;

    // Emit UI update event
    const update: UIUpdate = {
      id: streamId,
      fullText: updated,
      delta: content,
      totalChars: updated.length,
      lastUpdate: timestamp,
      status: "streaming",
    };

    this.emit("ui-update", update);
  }

  /**
   * Mark a stream as complete.
   */
  completeStream(streamId: StreamId, metadata?: Record<string, unknown>): void {
    const endTime = Date.now();
    this.streamStatus.set(streamId, "complete");

    // Update stats
    const stats = this.streamStats.get(streamId);
    if (stats) {
      stats.endTime = endTime;
      const duration = (endTime - stats.startTime) / 1000;
      if (duration > 0 && metadata?.tokens) {
        stats.tokensPerSecond = (metadata.tokens as number) / duration;
      }
    }

    // Emit completion event
    this.emit("stream-complete", {
      streamId,
      fullText: this.activeStreams.get(streamId) || "",
      stats: stats,
      metadata,
    });

    // Emit final UI update
    this.emit("ui-update", {
      id: streamId,
      fullText: this.activeStreams.get(streamId) || "",
      delta: "",
      totalChars: this.activeStreams.get(streamId)?.length || 0,
      lastUpdate: endTime,
      status: "complete",
    });
  }

  /**
   * Mark a stream as errored.
   */
  errorStream(streamId: StreamId, error: Error | string): void {
    this.streamStatus.set(streamId, "error");

    this.emit("stream-error", {
      streamId,
      error: typeof error === "string" ? error : error.message,
      partialContent: this.activeStreams.get(streamId) || "",
    });

    this.emit("ui-update", {
      id: streamId,
      fullText: this.activeStreams.get(streamId) || "",
      delta: "",
      totalChars: this.activeStreams.get(streamId)?.length || 0,
      lastUpdate: Date.now(),
      status: "error",
    });
  }

  // =========================================================================
  // STREAM ACCESS
  // =========================================================================

  /**
   * Get current content for a stream.
   */
  getStreamContent(streamId: StreamId): string {
    return this.activeStreams.get(streamId) || "";
  }

  /**
   * Get all active stream contents.
   */
  getAllStreams(): Record<StreamId, string> {
    const result: Partial<Record<StreamId, string>> = {};
    for (const [id, content] of this.activeStreams) {
      result[id] = content;
    }
    return result as Record<StreamId, string>;
  }

  /**
   * Get statistics for a stream.
   */
  getStreamStats(streamId: StreamId): StreamStats | undefined {
    return this.streamStats.get(streamId);
  }

  /**
   * Get status of a stream.
   */
  getStreamStatus(streamId: StreamId): "streaming" | "complete" | "error" | "idle" {
    return this.streamStatus.get(streamId) || "idle";
  }

  /**
   * Check if all specified streams are complete.
   */
  areStreamsComplete(streamIds: StreamId[]): boolean {
    return streamIds.every((id) => this.streamStatus.get(id) === "complete");
  }

  // =========================================================================
  // PARALLEL STREAM MANAGEMENT
  // =========================================================================

  /**
   * Wait for multiple streams to complete.
   * Used for parallel frontend/backend execution.
   */
  async waitForStreams(streamIds: StreamId[], timeout: number = 300000): Promise<{
    success: boolean;
    results: Record<StreamId, string>;
    errors: Record<StreamId, string>;
  }> {
    return new Promise((resolve) => {
      const results: Partial<Record<StreamId, string>> = {};
      const errors: Partial<Record<StreamId, string>> = {};
      const pending = new Set(streamIds);

      const checkComplete = () => {
        if (pending.size === 0) {
          cleanup();
          resolve({
            success: Object.keys(errors).length === 0,
            results: results as Record<StreamId, string>,
            errors: errors as Record<StreamId, string>,
          });
        }
      };

      const onComplete = (data: { streamId: StreamId; fullText: string }) => {
        if (pending.has(data.streamId)) {
          results[data.streamId] = data.fullText;
          pending.delete(data.streamId);
          checkComplete();
        }
      };

      const onError = (data: { streamId: StreamId; error: string }) => {
        if (pending.has(data.streamId)) {
          errors[data.streamId] = data.error;
          pending.delete(data.streamId);
          checkComplete();
        }
      };

      const timeoutId = setTimeout(() => {
        cleanup();
        // Add remaining as timeout errors
        for (const id of pending) {
          errors[id] = "Stream timeout";
        }
        resolve({
          success: false,
          results: results as Record<StreamId, string>,
          errors: errors as Record<StreamId, string>,
        });
      }, timeout);

      const cleanup = () => {
        clearTimeout(timeoutId);
        this.off("stream-complete", onComplete);
        this.off("stream-error", onError);
      };

      this.on("stream-complete", onComplete);
      this.on("stream-error", onError);

      // Check if already complete
      for (const id of streamIds) {
        if (this.streamStatus.get(id) === "complete") {
          results[id] = this.activeStreams.get(id) || "";
          pending.delete(id);
        } else if (this.streamStatus.get(id) === "error") {
          errors[id] = "Stream error";
          pending.delete(id);
        }
      }
      checkComplete();
    });
  }

  // =========================================================================
  // RESET & CLEANUP
  // =========================================================================

  /**
   * Reset a specific stream.
   */
  resetStream(streamId: StreamId): void {
    this.activeStreams.delete(streamId);
    this.streamStats.delete(streamId);
    this.streamStatus.delete(streamId);
  }

  /**
   * Reset all streams (for new pipeline run).
   */
  resetAll(): void {
    this.activeStreams.clear();
    this.streamStats.clear();
    this.streamStatus.clear();
    this.emit("reset");
  }

  /**
   * Get summary of all streams for logging.
   */
  getSummary(): {
    activeCount: number;
    completeCount: number;
    errorCount: number;
    totalChars: number;
  } {
    let activeCount = 0;
    let completeCount = 0;
    let errorCount = 0;
    let totalChars = 0;

    for (const [id, status] of this.streamStatus) {
      if (status === "streaming") activeCount++;
      else if (status === "complete") completeCount++;
      else if (status === "error") errorCount++;

      totalChars += this.activeStreams.get(id)?.length || 0;
    }

    return { activeCount, completeCount, errorCount, totalChars };
  }
}

// ============================================================================
// EXPORTS
// ============================================================================

export const streamManager = new MultiStreamManager();

/**
 * Create a new stream manager instance.
 */
export function createStreamManager(): MultiStreamManager {
  return new MultiStreamManager();
}
