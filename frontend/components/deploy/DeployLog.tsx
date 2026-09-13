"use client";
import { useEffect, useRef, useState } from "react";

interface LogEntry {
  type: string;
  message: string;
  timestamp: number;
}

export default function DeployLog({ projectId }: { projectId: string }) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [status, setStatus] = useState<"idle" | "deploying" | "done" | "error">("idle");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!projectId) return;
    setStatus("deploying");

    const eventSource = new EventSource(`/api/v1/deploy/${projectId}/stream`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLogs((prev) => [
          ...prev,
          { type: data.type, message: data.message || data.label || "", timestamp: Date.now() },
        ]);
        if (data.type === "deploy_complete") setStatus("done");
        if (data.type === "deploy_error") setStatus("error");
      } catch {}
    };

    eventSource.onerror = () => {
      eventSource.close();
      setStatus((s) => (s === "deploying" ? "error" : s));
    };

    return () => eventSource.close();
  }, [projectId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [logs]);

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b">
        <span className="text-sm font-medium">Deploy Log</span>
        <span
          className={`text-xs px-2 py-0.5 rounded-full ${
            status === "done"
              ? "bg-green-100 text-green-700"
              : status === "error"
              ? "bg-red-100 text-red-700"
              : status === "deploying"
              ? "bg-blue-100 text-blue-700"
              : "bg-gray-100 text-gray-600"
          }`}
        >
          {status}
        </span>
      </div>
      <div ref={scrollRef} className="h-64 overflow-y-auto p-4 bg-gray-900 text-gray-100 font-mono text-sm space-y-1">
        {logs.length === 0 && (
          <p className="text-gray-500">Waiting for deployment events...</p>
        )}
        {logs.map((log, i) => (
          <div key={i} className="flex gap-2">
            <span className="text-gray-500">[{new Date(log.timestamp).toLocaleTimeString()}]</span>
            <span className={log.type.includes("error") ? "text-red-400" : "text-green-400"}>
              {log.type}
            </span>
            <span>{log.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
