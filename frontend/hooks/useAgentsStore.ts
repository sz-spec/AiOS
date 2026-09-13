/**
 * Bridge hook for gradual migration from useAgents (React hooks + fetch)
 * to the Zustand agents store.
 *
 * Usage: Replace `useAgents()` with `useAgentsStore()` in components.
 * The returned API shape matches the original useAgents hook so call-sites
 * can migrate with minimal diff.
 */
import { useEffect, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  useAgentsStore,
  type Agent,
  type CreateAgentConfig,
  type TaskResult,
} from '@/lib/stores/agents-store';

export function useAgentsStoreBridge() {
  const { getToken } = useAuth();

  const agents = useAgentsStore((s) => s.agents);
  const logs = useAgentsStore((s) => s.logs);
  const isLoading = useAgentsStore((s) => s.isLoading);
  const error = useAgentsStore((s) => s.error);
  const selectedAgent = useAgentsStore((s) => s.selectedAgent);

  const storeFetchAgents = useAgentsStore((s) => s.fetchAgents);
  const storeCreateAgent = useAgentsStore((s) => s.createAgent);
  const storeUpdateAgent = useAgentsStore((s) => s.updateAgent);
  const storeDeleteAgent = useAgentsStore((s) => s.deleteAgent);
  const storeSelectAgent = useAgentsStore((s) => s.selectAgent);
  const storeExecuteTask = useAgentsStore((s) => s.executeTask);
  const storeIncrementRunCount = useAgentsStore((s) => s.incrementRunCount);
  const storeLoadLogs = useAgentsStore((s) => s.loadLogs);

  // Auto-fetch agents on mount (matches original useAgents behavior)
  useEffect(() => {
    storeFetchAgents(getToken);
  }, [storeFetchAgents, getToken]);

  // Wrap store actions to inject getToken automatically (matches original API)
  const loadAgents = useCallback(
    () => storeFetchAgents(getToken),
    [storeFetchAgents, getToken],
  );

  const createAgent = useCallback(
    (config: CreateAgentConfig) => storeCreateAgent(config, getToken),
    [storeCreateAgent, getToken],
  );

  const updateAgent = useCallback(
    (agentId: string, config: Partial<Agent>) =>
      storeUpdateAgent(agentId, config, getToken),
    [storeUpdateAgent, getToken],
  );

  const deleteAgent = useCallback(
    (agentId: string) => storeDeleteAgent(agentId, getToken),
    [storeDeleteAgent, getToken],
  );

  const selectAgent = storeSelectAgent;

  const executeTask = useCallback(
    (agentId: string, task: string, context?: any): Promise<TaskResult> =>
      storeExecuteTask(agentId, task, getToken, context),
    [storeExecuteTask, getToken],
  );

  const incrementRunCount = useCallback(
    (agentId: string) => storeIncrementRunCount(agentId, getToken),
    [storeIncrementRunCount, getToken],
  );

  const loadLogs = useCallback(
    (agentId: string) => storeLoadLogs(agentId, getToken),
    [storeLoadLogs, getToken],
  );

  return {
    agents,
    selectedAgent,
    logs,
    isLoading,
    error,
    loadAgents,
    createAgent,
    updateAgent,
    deleteAgent,
    selectAgent,
    executeTask,
    incrementRunCount,
    loadLogs,
  };
}
