import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  speaker_boost: boolean;
}

interface Agent {
  id: string;
  name: string;
  role: string;
  model: string;
  model_category?: string | null;
  category?: string | null;
  /** Active VOS3 vertical the agent was installed under (e.g. 'industrial-automation'). */
  platform_tag?: string | null;
  system_prompt: string | null;
  services: string[];
  temperature: number;
  status: 'idle' | 'running' | 'paused' | 'error';
  created_at: string;
  last_run: string | null;
  run_count: number;
  template_id?: string | null;
  description?: string | null;
  voice_id?: string | null;
  voice_settings?: VoiceSettings | null;
}

interface AgentLog {
  id: string;
  agent_id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  tokens?: number;
  cost?: number;
  duration_ms?: number;
}

interface TaskResult {
  success: boolean;
  result: any;
  error?: string;
  tokens_used: number;
  cost: number;
  duration_ms: number;
}

export function useAgents() {
  // Phase v17 (F-H4): Clerk auth token for all API requests
  const { getToken } = useAuth();

  const [agents, setAgents] = useState<Agent[]>([]);
  const [logs, setLogs] = useState<Record<string, AgentLog[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAgents = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, `/api/agents`);
      if (response.ok) {
        const data = await response.json();
        setAgents(data.agents);
      }
    } catch (err) {
      console.error('Failed to load agents:', err);
    }
  }, [getToken]);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const createAgent = useCallback(
    async (config: { name: string; role: string; model?: string; model_category?: string | null; category?: string | null; platform_tag?: string | null; system_prompt?: string; services?: string[]; temperature?: number; template_id?: string }) => {
      setIsLoading(true);
      setError(null);

      try {
        const response = await apiFetch(getToken, `/api/agents`, {
          method: 'POST',
          body: JSON.stringify({
            name: config.name,
            role: config.role,
            model: config.model || 'gpt-4o-mini',
            model_category: config.model_category ?? null,
            category: config.category ?? null,
            platform_tag: config.platform_tag ?? null,
            system_prompt: config.system_prompt,
            services: config.services || [],
            temperature: config.temperature ?? 0.7,
            template_id: config.template_id || null,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const agent = await response.json();
        setAgents((prev) => [...prev, agent]);
        return agent;
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Failed to create agent';
        setError(errorMessage);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [getToken]
  );

  const updateAgent = useCallback(async (agentId: string, config: Partial<Agent>) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await apiFetch(getToken, `/api/agents/${agentId}`, {
        method: 'PUT',
        body: JSON.stringify(config),
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      const agent = await response.json();
      setAgents((prev) => prev.map((a) => (a.id === agentId ? agent : a)));
      return agent;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to update agent';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const deleteAgent = useCallback(async (agentId: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await apiFetch(getToken, `/api/agents/${agentId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      setAgents((prev) => prev.filter((a) => a.id !== agentId));
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete agent';
      setError(errorMessage);
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const executeTask = useCallback(async (agentId: string, task: string, context?: any): Promise<TaskResult> => {
    setIsLoading(true);
    setError(null);

    // Optimistically update agent status
    setAgents((prev) => prev.map((a) => (a.id === agentId ? { ...a, status: 'running' as const } : a)));

    try {
      const response = await apiFetch(getToken, `/api/agents/${agentId}/execute`, {
        method: 'POST',
        body: JSON.stringify({ task, context }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      const result: TaskResult = await response.json();

      // Update agent status back to idle
      setAgents((prev) =>
        prev.map((a) =>
          a.id === agentId
            ? { ...a, status: 'idle' as const, last_run: new Date().toISOString() }
            : a
        )
      );

      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to execute task';
      setError(errorMessage);

      // Update agent status to error
      setAgents((prev) => prev.map((a) => (a.id === agentId ? { ...a, status: 'error' as const } : a)));

      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const incrementRunCount = useCallback(async (agentId: string) => {
    // Optimistic update
    setAgents((prev) =>
      prev.map((a) =>
        a.id === agentId
          ? { ...a, run_count: (a.run_count || 0) + 1, last_run: new Date().toISOString() }
          : a
      )
    );
    try {
      await apiFetch(getToken, `/api/agents/${agentId}/run`, { method: 'POST' });
    } catch (err) {
      console.error('Failed to increment run count:', err);
    }
  }, [getToken]);

  const loadLogs = useCallback(async (agentId: string) => {
    try {
      const response = await apiFetch(getToken, `/api/agents/${agentId}/logs`);
      if (response.ok) {
        const data = await response.json();
        setLogs((prev) => ({ ...prev, [agentId]: data.logs }));
      }
    } catch (err) {
      console.error('Failed to load logs:', err);
    }
  }, [getToken]);

  return {
    agents,
    logs,
    isLoading,
    error,
    loadAgents,
    createAgent,
    updateAgent,
    deleteAgent,
    executeTask,
    incrementRunCount,
    loadLogs,
  };
}
