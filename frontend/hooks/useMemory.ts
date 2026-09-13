import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

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

// Predefined tag categories
export const TAG_CATEGORIES = {
  priority: ['urgent', 'important', 'low-priority'],
  status: ['active', 'archived', 'pending', 'resolved'],
  scope: ['frontend', 'backend', 'database', 'api', 'ui', 'devops'],
  type: ['bug', 'feature', 'improvement', 'documentation', 'config'],
  custom: [] as string[],
} as const;

export type TagCategory = keyof typeof TAG_CATEGORIES;

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

export function useInsights() {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [stats, setStats] = useState<InsightStats | null>(null);
  const [types, setTypes] = useState<InsightTypes | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const { getToken } = useAuth();

  // Fetch stats
  const fetchStats = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, '/api/memory');
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, [getToken]);

  // Fetch types
  const fetchTypes = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, '/api/memory/types');
      if (response.ok) {
        const data = await response.json();
        setTypes(data);
      }
    } catch (err) {
      console.error('Failed to fetch types:', err);
    }
  }, [getToken]);

  // Fetch recent insights
  const fetchRecent = useCallback(async (limit: number = 50, insightType?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: limit.toString() });
      if (insightType) params.append('memory_type', insightType);

      const response = await apiFetch(getToken, `/api/memory/recent?${params}`);
      if (response.ok) {
        const data = await response.json();
        setInsights(data.memories || []);
      } else {
        throw new Error('Failed to fetch insights');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch insights';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  // Search insights
  const searchInsights = useCallback(async (
    query: string,
    topK: number = 20,
    insightType?: string
  ) => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, '/api/memory/query', {
        method: 'POST',
        body: JSON.stringify({
          query,
          top_k: topK,
          memory_type: insightType || null,
          min_relevance: 0.0,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setInsights(data.results || []);
        return data.results;
      } else {
        throw new Error('Search failed');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Search failed';
      setError(errorMessage);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  // Add insight with auto-tagging
  const addInsight = useCallback(async (
    content: string,
    insightType: string = 'context',
    metadata?: Record<string, string | string[]>,
    tags?: string[],
    autoTag: boolean = true
  ) => {
    setError(null);
    try {
      const response = await apiFetch(getToken, '/api/memory/add', {
        method: 'POST',
        body: JSON.stringify({
          content,
          memory_type: insightType,
          metadata: { ...(metadata || {}), tags: tags || [] },
          auto_tag: autoTag,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        await Promise.all([fetchStats(), fetchRecent()]);
        return {
          success: true,
          autoTags: data.auto_tags || [],
          similarMemories: data.similar_memories || [],
        };
      } else {
        throw new Error('Failed to add insight');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to add insight';
      setError(errorMessage);
      return { success: false, autoTags: [], similarMemories: [] };
    }
  }, [fetchStats, fetchRecent, getToken]);

  // Get suggested tags for content
  const suggestTags = useCallback(async (content: string): Promise<string[]> => {
    try {
      const response = await apiFetch(getToken, `/api/memory/suggest-tags?content=${encodeURIComponent(content)}`);
      if (response.ok) {
        const data = await response.json();
        return data.suggested_tags || [];
      }
      return [];
    } catch {
      return [];
    }
  }, [getToken]);

  // Get similar memories
  const getSimilarMemories = useCallback(async (insightId: string): Promise<Insight[]> => {
    try {
      const response = await apiFetch(getToken, `/api/memory/similar/${insightId}`);
      if (response.ok) {
        const data = await response.json();
        return data.similar || [];
      }
      return [];
    } catch {
      return [];
    }
  }, [getToken]);

  // Get consolidated groups
  const getConsolidatedGroups = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, '/api/memory/consolidate', {
        method: 'POST',
      });
      if (response.ok) {
        const data = await response.json();
        return data.groups || [];
      }
      return [];
    } catch {
      return [];
    }
  }, [getToken]);

  // Update insight
  const updateInsight = useCallback(async (
    id: string,
    content?: string,
    insightType?: string,
    tags?: string[]
  ) => {
    setError(null);
    try {
      const body: Record<string, unknown> = {};
      if (content !== undefined) body.content = content;
      if (insightType !== undefined) body.memory_type = insightType;
      if (tags !== undefined) body.tags = tags;

      const response = await apiFetch(getToken, `/api/memory/${id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });

      if (response.ok) {
        await fetchRecent();
        return true;
      } else {
        throw new Error('Failed to update insight');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to update insight';
      setError(errorMessage);
      return false;
    }
  }, [fetchRecent, getToken]);

  // Update tags only
  const updateTags = useCallback(async (id: string, tags: string[]) => {
    return updateInsight(id, undefined, undefined, tags);
  }, [updateInsight]);

  // Add a tag to an insight
  const addTag = useCallback(async (id: string, tag: string) => {
    const insight = insights.find(i => i.id === id);
    if (!insight) return false;
    const currentTags = insight.metadata.tags || [];
    if (currentTags.includes(tag)) return true;
    return updateTags(id, [...currentTags, tag]);
  }, [insights, updateTags]);

  // Remove a tag from an insight
  const removeTag = useCallback(async (id: string, tag: string) => {
    const insight = insights.find(i => i.id === id);
    if (!insight) return false;
    const currentTags = insight.metadata.tags || [];
    return updateTags(id, currentTags.filter(t => t !== tag));
  }, [insights, updateTags]);

  // Delete single insight
  const deleteInsight = useCallback(async (id: string) => {
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/memory/${id}`, {
        method: 'DELETE',
      });

      if (response.ok) {
        await Promise.all([fetchStats(), fetchRecent()]);
        return true;
      } else {
        throw new Error('Failed to delete insight');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete insight';
      setError(errorMessage);
      return false;
    }
  }, [fetchStats, fetchRecent, getToken]);

  // Bulk delete
  const bulkDelete = useCallback(async (ids: string[]) => {
    setError(null);
    try {
      const response = await apiFetch(getToken, '/api/memory/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ ids }),
      });

      if (response.ok) {
        setSelectedIds(new Set());
        await Promise.all([fetchStats(), fetchRecent()]);
        return true;
      } else {
        throw new Error('Failed to delete insights');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete insights';
      setError(errorMessage);
      return false;
    }
  }, [fetchStats, fetchRecent, getToken]);

  // Export all
  const exportInsights = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, '/api/memory/export/all');
      if (response.ok) {
        const data = await response.json();
        // Create download
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `insights-${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
        return true;
      }
      return false;
    } catch (err) {
      console.error('Export failed:', err);
      return false;
    }
  }, [getToken]);

  // Import
  const importInsights = useCallback(async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const insightsToImport = data.insights || data;

      const response = await apiFetch(getToken, '/api/memory/import', {
        method: 'POST',
        body: JSON.stringify({ insights: insightsToImport }),
      });

      if (response.ok) {
        await Promise.all([fetchStats(), fetchRecent()]);
        return true;
      }
      return false;
    } catch (err) {
      console.error('Import failed:', err);
      return false;
    }
  }, [fetchStats, fetchRecent, getToken]);

  // Clear all
  const clearAll = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, '/api/memory/clear', {
        method: 'DELETE',
      });
      if (response.ok) {
        await Promise.all([fetchStats(), fetchRecent()]);
        return true;
      }
      return false;
    } catch (err) {
      console.error('Clear failed:', err);
      return false;
    }
  }, [fetchStats, fetchRecent, getToken]);

  // Selection helpers
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(insights.map(i => i.id)));
  }, [insights]);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  // Initialize
  useEffect(() => {
    fetchStats();
    fetchTypes();
    fetchRecent();
  }, [fetchStats, fetchTypes, fetchRecent]);

  return {
    insights,
    stats,
    types,
    isLoading,
    error,
    selectedIds,
    fetchStats,
    fetchRecent,
    searchInsights,
    addInsight,
    updateInsight,
    updateTags,
    addTag,
    removeTag,
    suggestTags,
    getSimilarMemories,
    getConsolidatedGroups,
    deleteInsight,
    bulkDelete,
    exportInsights,
    importInsights,
    clearAll,
    toggleSelect,
    selectAll,
    clearSelection,
  };
}

// Chat message for memory discussion
export interface MemoryChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  action?: 'edit' | 'delete' | 'categorize';
  suggestedEdit?: string;
}

// Hook for memory chat/discussion
export function useMemoryChat(insight: Insight | null) {
  const [messages, setMessages] = useState<MemoryChatMessage[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const { getToken } = useAuth();

  const sendMessage = useCallback(async (content: string): Promise<string | null> => {
    if (!insight) return null;

    const userMessage: MemoryChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);
    setIsProcessing(true);

    try {
      const response = await apiFetch(getToken, '/api/chat/completions', {
        method: 'POST',
        body: JSON.stringify({
          messages: [
            {
              role: 'system',
              content: `You are a helpful assistant for discussing and editing memory entries. The user is viewing the following memory:

Memory Content: "${insight.content}"
Memory Type: ${insight.metadata.memory_type}
Created: ${insight.metadata.timestamp}

Help the user understand, edit, or improve this memory. If they ask to edit it, provide a suggested new version.
When suggesting edits, format them clearly with "SUGGESTED EDIT:" prefix.
Be concise and helpful.`
            },
            ...messages.map(m => ({ role: m.role, content: m.content })),
            { role: 'user', content }
          ],
          model: 'gpt-4o-mini',
        }),
      });

      if (response.ok) {
        const data = await response.json();
        const assistantContent = data.message?.content || data.response || data.content || '';

        // Check if there's a suggested edit
        let suggestedEdit: string | undefined;
        const editMatch = assistantContent.match(/SUGGESTED EDIT:\s*(.+?)(?=\n\n|$)/s);
        if (editMatch) {
          suggestedEdit = editMatch[1].trim();
        }

        const assistantMessage: MemoryChatMessage = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: assistantContent,
          timestamp: new Date(),
          suggestedEdit,
          action: suggestedEdit ? 'edit' : undefined,
        };
        setMessages(prev => [...prev, assistantMessage]);
        return suggestedEdit || null;
      }
      return null;
    } catch (err) {
      console.error('Chat error:', err);
      const errorMessage: MemoryChatMessage = {
        id: `error-${Date.now()}`,
        role: 'assistant',
        content: 'Sorry, I encountered an error. Please try again.',
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, errorMessage]);
      return null;
    } finally {
      setIsProcessing(false);
    }
  }, [insight, messages, getToken]);

  const clearChat = useCallback(() => {
    setMessages([]);
  }, []);

  // Reset when insight changes
  useEffect(() => {
    setMessages([]);
  }, [insight?.id]);

  return {
    messages,
    isProcessing,
    sendMessage,
    clearChat,
  };
}

// Keep old export for compatibility
export const useMemory = useInsights;
export type MemoryEntry = Insight;
export type MemoryStats = InsightStats;
export type MemoryTypeInfo = InsightTypes;
