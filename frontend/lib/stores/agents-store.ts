import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useAgents.ts)
// ---------------------------------------------------------------------------

export interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  speaker_boost: boolean;
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  model: string;
  model_category?: string | null;
  category?: string | null;
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

export interface AgentLog {
  id: string;
  agent_id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  tokens?: number;
  cost?: number;
  duration_ms?: number;
}

export interface TaskResult {
  success: boolean;
  result: any;
  error?: string;
  tokens_used: number;
  cost: number;
  duration_ms: number;
}

export interface CreateAgentConfig {
  name: string;
  role: string;
  model?: string;
  model_category?: string | null;
  category?: string | null;
  system_prompt?: string;
  services?: string[];
  temperature?: number;
  template_id?: string;
}

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

interface AgentsState {
  // ---- data ----
  agents: Agent[];
  selectedAgent: Agent | null;
  logs: Record<string, AgentLog[]>;
  isLoading: boolean;
  error: string | null;

  // ---- actions ----
  fetchAgents: (getToken: () => Promise<string | null>) => Promise<void>;
  createAgent: (
    config: CreateAgentConfig,
    getToken: () => Promise<string | null>,
  ) => Promise<Agent>;
  updateAgent: (
    agentId: string,
    config: Partial<Agent>,
    getToken: () => Promise<string | null>,
  ) => Promise<Agent>;
  deleteAgent: (
    agentId: string,
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  selectAgent: (agentId: string | null) => void;
  executeTask: (
    agentId: string,
    task: string,
    getToken: () => Promise<string | null>,
    context?: any,
  ) => Promise<TaskResult>;
  incrementRunCount: (
    agentId: string,
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  loadLogs: (
    agentId: string,
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Helper — build auth header
// ---------------------------------------------------------------------------

const authHeader = (token: string | null): Record<string, string> =>
  token ? { Authorization: `Bearer ${token}` } : {};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAgentsStore = create<AgentsState>()(
  immer((set, get) => ({
    // ---- initial state ----
    agents: [],
    selectedAgent: null,
    logs: {},
    isLoading: false,
    error: null,

    // ---- actions ----

    fetchAgents: async (getToken) => {
      try {
        const token = await getToken();
        const response = await fetch('/api/agents', {
          headers: authHeader(token),
        });
        if (response.ok) {
          const data = await response.json();
          set((state) => {
            state.agents = data.agents;
          });
        }
      } catch (err) {
        console.error('Failed to load agents:', err);
      }
    },

    createAgent: async (config, getToken) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const token = await getToken();
        const response = await fetch('/api/agents', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeader(token),
          },
          body: JSON.stringify({
            name: config.name,
            role: config.role,
            model: config.model || 'gpt-4o-mini',
            model_category: config.model_category ?? null,
            category: config.category ?? null,
            system_prompt: config.system_prompt,
            services: config.services || [],
            temperature: config.temperature ?? 0.7,
            template_id: config.template_id || null,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const agent: Agent = await response.json();
        set((state) => {
          state.agents.push(agent);
        });
        return agent;
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to create agent';
        set((state) => {
          state.error = errorMessage;
        });
        throw err;
      } finally {
        set((state) => {
          state.isLoading = false;
        });
      }
    },

    updateAgent: async (agentId, config, getToken) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const token = await getToken();
        const response = await fetch(`/api/agents/${agentId}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...authHeader(token),
          },
          body: JSON.stringify(config),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const agent: Agent = await response.json();
        set((state) => {
          const idx = state.agents.findIndex((a) => a.id === agentId);
          if (idx !== -1) {
            state.agents[idx] = agent;
          }
          if (state.selectedAgent?.id === agentId) {
            state.selectedAgent = agent;
          }
        });
        return agent;
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to update agent';
        set((state) => {
          state.error = errorMessage;
        });
        throw err;
      } finally {
        set((state) => {
          state.isLoading = false;
        });
      }
    },

    deleteAgent: async (agentId, getToken) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
      });

      try {
        const token = await getToken();
        const response = await fetch(`/api/agents/${agentId}`, {
          method: 'DELETE',
          headers: authHeader(token),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        set((state) => {
          state.agents = state.agents.filter((a) => a.id !== agentId);
          if (state.selectedAgent?.id === agentId) {
            state.selectedAgent = null;
          }
        });
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to delete agent';
        set((state) => {
          state.error = errorMessage;
        });
        throw err;
      } finally {
        set((state) => {
          state.isLoading = false;
        });
      }
    },

    selectAgent: (agentId) => {
      set((state) => {
        if (agentId === null) {
          state.selectedAgent = null;
        } else {
          state.selectedAgent =
            state.agents.find((a) => a.id === agentId) ?? null;
        }
      });
    },

    executeTask: async (agentId, task, getToken, context?) => {
      set((state) => {
        state.isLoading = true;
        state.error = null;
        // Optimistically set agent to running
        const agent = state.agents.find((a) => a.id === agentId);
        if (agent) agent.status = 'running';
      });

      try {
        const token = await getToken();
        const response = await fetch(`/api/agents/${agentId}/execute`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeader(token),
          },
          body: JSON.stringify({ task, context }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const result: TaskResult = await response.json();

        set((state) => {
          const agent = state.agents.find((a) => a.id === agentId);
          if (agent) {
            agent.status = 'idle';
            agent.last_run = new Date().toISOString();
          }
        });

        return result;
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to execute task';
        set((state) => {
          state.error = errorMessage;
          const agent = state.agents.find((a) => a.id === agentId);
          if (agent) agent.status = 'error';
        });
        throw err;
      } finally {
        set((state) => {
          state.isLoading = false;
        });
      }
    },

    incrementRunCount: async (agentId, getToken) => {
      // Optimistic update
      set((state) => {
        const agent = state.agents.find((a) => a.id === agentId);
        if (agent) {
          agent.run_count = (agent.run_count || 0) + 1;
          agent.last_run = new Date().toISOString();
        }
      });

      try {
        const token = await getToken();
        await fetch(`/api/agents/${agentId}/run`, {
          method: 'POST',
          headers: authHeader(token),
        });
      } catch (err) {
        console.error('Failed to increment run count:', err);
      }
    },

    loadLogs: async (agentId, getToken) => {
      try {
        const token = await getToken();
        const response = await fetch(`/api/agents/${agentId}/logs`, {
          headers: authHeader(token),
        });
        if (response.ok) {
          const data = await response.json();
          set((state) => {
            state.logs[agentId] = data.logs;
          });
        }
      } catch (err) {
        console.error('Failed to load logs:', err);
      }
    },
  })),
);
