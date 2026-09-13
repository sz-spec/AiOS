/**
 * GhostStream - Real-time Parallel Pipeline Visualization
 * ========================================================
 * Displays streaming outputs from Frontend and Backend agents
 * running in parallel, with live aggregator preview.
 *
 * February 2026 - VOS3 Multi-Agent Pipeline UI
 */

import React, { useEffect, useState, useRef } from "react";

// ============================================================================
// TYPES
// ============================================================================

export type StreamId = "frontend" | "backend" | "aggregator" | "architect" | "tester" | "reviewer";

export interface StreamData {
  frontend: string;
  backend: string;
  aggregator?: string;
}

export interface StreamStatus {
  frontend: "idle" | "streaming" | "complete" | "error";
  backend: "idle" | "streaming" | "complete" | "error";
  aggregator: "idle" | "streaming" | "complete" | "error";
}

export interface GhostStreamProps {
  /** Current stream contents */
  streams: StreamData;
  /** Optional stream statuses */
  status?: StreamStatus;
  /** Optional model info to display */
  models?: {
    frontend?: string;
    backend?: string;
    aggregator?: string;
  };
  /** Whether to show the aggregator preview panel */
  showAggregator?: boolean;
  /** Optional className for styling */
  className?: string;
}

// ============================================================================
// ANIMATED CURSOR COMPONENT
// ============================================================================

const AnimatedCursor: React.FC<{ isActive: boolean }> = ({ isActive }) => {
  if (!isActive) return null;
  return (
    <span
      className="inline-block w-0.5 h-4 bg-white ml-0.5 animate-pulse"
      style={{ animation: "blink 1s step-end infinite" }}
    />
  );
};

// ============================================================================
// STATUS INDICATOR COMPONENT
// ============================================================================

const StatusIndicator: React.FC<{
  status: "idle" | "streaming" | "complete" | "error";
}> = ({ status }) => {
  const colors = {
    idle: "text-gray-500",
    streaming: "text-green-400 animate-pulse",
    complete: "text-blue-400",
    error: "text-red-400",
  };

  const labels = {
    idle: "Idle",
    streaming: "Streaming",
    complete: "Complete",
    error: "Error",
  };

  return (
    <span className={`text-xs ${colors[status]}`}>
      {status === "streaming" && <span className="mr-1">●</span>}
      {status === "complete" && <span className="mr-1">✓</span>}
      {status === "error" && <span className="mr-1">✗</span>}
      {labels[status]}
    </span>
  );
};

// ============================================================================
// STREAM PANEL COMPONENT
// ============================================================================

interface StreamPanelProps {
  title: string;
  content: string;
  status: "idle" | "streaming" | "complete" | "error";
  model?: string;
  borderColor: string;
  titleColor: string;
}

const StreamPanel: React.FC<StreamPanelProps> = ({
  title,
  content,
  status,
  model,
  borderColor,
  titleColor,
}) => {
  const scrollRef = useRef<HTMLPreElement>(null);

  // Auto-scroll to bottom when content updates
  useEffect(() => {
    if (scrollRef.current && status === "streaming") {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, status]);

  return (
    <div className={`border ${borderColor} p-4 rounded-lg bg-black/50 flex flex-col`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`${titleColor} font-semibold flex items-center`}>
          {status === "streaming" && (
            <span className="animate-pulse mr-2 text-green-400">●</span>
          )}
          {title}
          {model && <span className="text-xs text-gray-500 ml-2">({model})</span>}
        </h3>
        <StatusIndicator status={status} />
      </div>
      <pre
        ref={scrollRef}
        className="text-sm font-mono text-slate-300 overflow-auto flex-1 max-h-80 whitespace-pre-wrap"
      >
        {content || <span className="text-gray-600 italic">Waiting for stream...</span>}
        <AnimatedCursor isActive={status === "streaming"} />
      </pre>
    </div>
  );
};

// ============================================================================
// AGGREGATOR PREVIEW COMPONENT
// ============================================================================

interface AggregatorPreviewProps {
  frontendContent: string;
  backendContent: string;
  aggregatorContent?: string;
  status: "idle" | "streaming" | "complete" | "error";
  model?: string;
}

const AggregatorPreview: React.FC<AggregatorPreviewProps> = ({
  frontendContent,
  backendContent,
  aggregatorContent,
  status,
  model,
}) => {
  const frontendReady = frontendContent.length > 0;
  const backendReady = backendContent.length > 0;

  return (
    <div className="mt-4 border border-purple-500/30 p-4 rounded-lg bg-black/30">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-purple-400 font-semibold flex items-center">
          {status === "streaming" && (
            <span className="animate-pulse mr-2 text-green-400">●</span>
          )}
          Aggregator
          {model && <span className="text-xs text-gray-500 ml-2">({model})</span>}
        </h3>
        <div className="flex items-center gap-3">
          <span className={`text-xs ${frontendReady ? "text-cyan-400" : "text-gray-600"}`}>
            Frontend {frontendReady ? "✓" : "..."}
          </span>
          <span className={`text-xs ${backendReady ? "text-amber-400" : "text-gray-600"}`}>
            Backend {backendReady ? "✓" : "..."}
          </span>
          <StatusIndicator status={status} />
        </div>
      </div>

      {aggregatorContent ? (
        <pre className="text-sm font-mono text-slate-300 overflow-auto max-h-40 whitespace-pre-wrap">
          {aggregatorContent}
          <AnimatedCursor isActive={status === "streaming"} />
        </pre>
      ) : (
        <div className="text-gray-600 text-sm italic">
          {frontendReady && backendReady
            ? "Both streams ready. Aggregator starting..."
            : "Waiting for Frontend and Backend streams to complete..."}
        </div>
      )}
    </div>
  );
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export const GhostStream: React.FC<GhostStreamProps> = ({
  streams,
  status = {
    frontend: streams.frontend ? "streaming" : "idle",
    backend: streams.backend ? "streaming" : "idle",
    aggregator: streams.aggregator ? "streaming" : "idle",
  },
  models = {
    frontend: "Opus 4.6",
    backend: "Opus 4.6",
    aggregator: "Liquid LFM 2.5",
  },
  showAggregator = true,
  className = "",
}) => {
  return (
    <div className={`bg-slate-900 p-6 rounded-xl ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white text-lg font-semibold">Pipeline Streams</h2>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>Parallel Execution</span>
          <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
        </div>
      </div>

      {/* Parallel Stream Panels */}
      <div className="grid grid-cols-2 gap-4">
        {/* Frontend Channel */}
        <StreamPanel
          title="Frontend"
          content={streams.frontend}
          status={status.frontend}
          model={models.frontend}
          borderColor="border-cyan-500/30"
          titleColor="text-cyan-400"
        />

        {/* Backend Channel */}
        <StreamPanel
          title="Backend"
          content={streams.backend}
          status={status.backend}
          model={models.backend}
          borderColor="border-amber-500/30"
          titleColor="text-amber-400"
        />
      </div>

      {/* Aggregator Preview Panel */}
      {showAggregator && (
        <AggregatorPreview
          frontendContent={streams.frontend}
          backendContent={streams.backend}
          aggregatorContent={streams.aggregator}
          status={status.aggregator}
          model={models.aggregator}
        />
      )}
    </div>
  );
};

// ============================================================================
// HOOK: USE GHOST STREAM
// ============================================================================

export interface UseGhostStreamOptions {
  /** WebSocket URL for stream updates */
  wsUrl?: string;
  /** SSE URL for stream updates */
  sseUrl?: string;
}

export interface UseGhostStreamReturn {
  streams: StreamData;
  status: StreamStatus;
  isConnected: boolean;
  reset: () => void;
}

/**
 * Hook to manage GhostStream state with WebSocket/SSE connection.
 */
export function useGhostStream(options: UseGhostStreamOptions = {}): UseGhostStreamReturn {
  const [streams, setStreams] = useState<StreamData>({
    frontend: "",
    backend: "",
    aggregator: "",
  });

  const [status, setStatus] = useState<StreamStatus>({
    frontend: "idle",
    backend: "idle",
    aggregator: "idle",
  });

  const [isConnected, setIsConnected] = useState(false);

  const reset = () => {
    setStreams({ frontend: "", backend: "", aggregator: "" });
    setStatus({ frontend: "idle", backend: "idle", aggregator: "idle" });
  };

  // WebSocket connection
  useEffect(() => {
    if (!options.wsUrl) return;

    const ws = new WebSocket(options.wsUrl);

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "chunk") {
          const { streamId, content } = data;
          setStreams((prev) => ({
            ...prev,
            [streamId]: prev[streamId as keyof StreamData] + content,
          }));
          setStatus((prev) => ({
            ...prev,
            [streamId]: "streaming",
          }));
        } else if (data.type === "complete") {
          const { streamId } = data;
          setStatus((prev) => ({
            ...prev,
            [streamId]: "complete",
          }));
        } else if (data.type === "error") {
          const { streamId } = data;
          setStatus((prev) => ({
            ...prev,
            [streamId]: "error",
          }));
        } else if (data.type === "reset") {
          reset();
        }
      } catch (e) {
        console.error("Failed to parse stream message:", e);
      }
    };

    return () => ws.close();
  }, [options.wsUrl]);

  // SSE connection
  useEffect(() => {
    if (!options.sseUrl) return;

    const eventSource = new EventSource(options.sseUrl);

    eventSource.onopen = () => setIsConnected(true);
    eventSource.onerror = () => setIsConnected(false);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.streamId && data.content !== undefined) {
          setStreams((prev) => ({
            ...prev,
            [data.streamId]: prev[data.streamId as keyof StreamData] + data.content,
          }));
          setStatus((prev) => ({
            ...prev,
            [data.streamId]: data.status || "streaming",
          }));
        }
      } catch (e) {
        console.error("Failed to parse SSE message:", e);
      }
    };

    return () => eventSource.close();
  }, [options.sseUrl]);

  return { streams, status, isConnected, reset };
}

// ============================================================================
// EXPORTS
// ============================================================================

export default GhostStream;
