import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useMemory.ts)
// ---------------------------------------------------------------------------

export interface Insight {
  id: string;
  content: string;
  metadata: {
    memory_type: string;
    timestamp: string;
    tags?: string[];
    [key: string]: string | string[] | undefined;
  };
  relevance?: number;
}

export interface InsightStats {
  initialized: boolean;
  persist_dir: string;
  total_memories: number;
  by_type: Record<string, number>;
  embedding_model: string | null;
}

export interface InsightTypes {
  types: string[];
  descriptions: Record<string, string>;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

interface MemoryState {
  insights: Insight[];
  stats: InsightStats | null;
  types: InsightTypes | null;
  isLoading: boolean;
  error: string | null;

  fetchStats: (getToken: () => Promise<string | null>) => Promise<void>;
  fetchTypes: (getToken: () => Promise<string | null>) => Promise<void>;
  fetchRecent: (
    getToken: () => Promise<string | null>,
    limit?: number,
    insightType?: string,
  ) => Promise<void>;
  searchInsights: (
    query: string,
    getToken: () => Promise<string | null>,
    topK?: number,
    insightType?: string,
  ) => Promise<void>;
  addInsight: (
    content: string,
    getToken: () => Promise<string | null>,
    insightType?: string,
    metadata?: Record<string, string | string[]>,
    tags?: string[],
  ) => Promise<void>;
  deleteInsight: (
    id: string,
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  clearAll: (getToken: () => Promise<string | null>) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const authHeader = async (
  getToken: () => Promise<string | null>,
): Promise<Record<string, string>> => {
  const token = await getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useMemoryStateStore = create<MemoryState>()(
  immer((set) => ({
    insights: [],
    stats: null,
    types: null,
    isLoading: false,
    error: null,

    fetchStats: async (getToken) => {
      try {
        const headers = await authHeader(getToken);
        const res = await fetch('/api/memory/stats', { headers });
        if (res.ok) {
          const data = await res.json();
          set((s) => { s.stats = data; });
        }
      } catch {
        /* silent — stats are optional */
      }
    },

    fetchTypes: async (getToken) => {
      try {
        const headers = await authHeader(getToken);
        const res = await fetch('/api/memory/types', { headers });
        if (res.ok) {
          const data = await res.json();
          set((s) => { s.types = data; });
        }
      } catch {
        /* silent */
      }
    },

    fetchRecent: async (getToken, limit = 50, insightType) => {
      set((s) => { s.isLoading = true; s.error = null; });
      try {
        const headers = await authHeader(getToken);
        const params = new URLSearchParams({ limit: String(limit) });
        if (insightType) params.set('memory_type', insightType);

        const res = await fetch(`/api/memory/recent?${params}`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        set((s) => { s.insights = data.memories || data; });
      } catch (err) {
        set((s) => {
          s.error = err instanceof Error ? err.message : 'Failed to fetch insights';
        });
      } finally {
        set((s) => { s.isLoading = false; });
      }
    },

    searchInsights: async (query, getToken, topK = 20, insightType) => {
      set((s) => { s.isLoading = true; s.error = null; });
      try {
        const headers = await authHeader(getToken);
        const params = new URLSearchParams({ query, top_k: String(topK) });
        if (insightType) params.set('memory_type', insightType);

        const res = await fetch(`/api/memory/search?${params}`, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        set((s) => { s.insights = data.results || data; });
      } catch (err) {
        set((s) => {
          s.error = err instanceof Error ? err.message : 'Search failed';
        });
      } finally {
        set((s) => { s.isLoading = false; });
      }
    },

    addInsight: async (content, getToken, insightType = 'context', metadata, tags) => {
      set((s) => { s.isLoading = true; s.error = null; });
      try {
        const headers = await authHeader(getToken);
        const res = await fetch('/api/memory/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...headers },
          body: JSON.stringify({
            content,
            memory_type: insightType,
            metadata: metadata || {},
            tags: tags || [],
          }),
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch (err) {
        set((s) => {
          s.error = err instanceof Error ? err.message : 'Failed to add insight';
        });
      } finally {
        set((s) => { s.isLoading = false; });
      }
    },

    deleteInsight: async (id, getToken) => {
      try {
        const headers = await authHeader(getToken);
        const res = await fetch(`/api/memory/${id}`, {
          method: 'DELETE',
          headers,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        set((s) => {
          s.insights = s.insights.filter((i) => i.id !== id);
        });
      } catch (err) {
        set((s) => {
          s.error = err instanceof Error ? err.message : 'Failed to delete insight';
        });
      }
    },

    clearAll: async (getToken) => {
      set((s) => { s.isLoading = true; });
      try {
        const headers = await authHeader(getToken);
        const res = await fetch('/api/memory/clear', {
          method: 'POST',
          headers,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        set((s) => {
          s.insights = [];
          if (s.stats) s.stats.total_memories = 0;
        });
      } catch (err) {
        set((s) => {
          s.error = err instanceof Error ? err.message : 'Failed to clear memories';
        });
      } finally {
        set((s) => { s.isLoading = false; });
      }
    },
  })),
);
