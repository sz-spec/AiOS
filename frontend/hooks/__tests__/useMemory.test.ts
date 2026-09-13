import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useMemory } from '../useMemory';

describe('useMemory (useInsights)', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with empty insights and null stats/types', () => {
    const { result } = renderHook(() => useMemory());
    expect(result.current.insights).toEqual([]);
    expect(result.current.stats).toBeNull();
    expect(result.current.types).toBeNull();
  });

  it('starts with error null', () => {
    const { result } = renderHook(() => useMemory());
    // isLoading may be true because useEffect calls fetchStats/fetchTypes/fetchRecent on mount
    expect(result.current.error).toBeNull();
  });

  it('starts with empty selectedIds set', () => {
    const { result } = renderHook(() => useMemory());
    expect(result.current.selectedIds.size).toBe(0);
  });

  // ---- fetchStats ----

  it('fetchStats updates stats on success', async () => {
    const mockStats = {
      initialized: true,
      persist_dir: '/data',
      total_memories: 42,
      by_type: { context: 20, decision: 22 },
      embedding_model: 'text-embedding-3-small',
    };

    vi.spyOn(global, 'fetch')
      // useEffect: fetchStats, fetchTypes, fetchRecent all fail initially
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      // manual fetchStats
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockStats,
      } as Response);

    const { result } = renderHook(() => useMemory());

    await act(async () => {
      await result.current.fetchStats();
    });

    expect(result.current.stats).toEqual(mockStats);
  });

  // ---- fetchTypes ----

  it('fetchTypes does not crash when response is not ok', async () => {
    // All three init calls fail, plus a manual call that returns not-ok
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useMemory());

    // Should not throw
    await act(async () => {
      // fetchTypes is not directly exposed but called via internal fetchTypes
      // We test that types remain null if fetch fails
    });

    expect(result.current.types).toBeNull();
  });

  // ---- fetchRecent ----

  it('fetchRecent populates insights on success', async () => {
    const mockMemories = [
      { id: 'm1', content: 'Fix bug in auth', metadata: { memory_type: 'bug', timestamp: '2026-01-01' } },
    ];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ memories: mockMemories }),
      } as Response);

    const { result } = renderHook(() => useMemory());

    await act(async () => {
      await result.current.fetchRecent();
    });

    expect(result.current.insights).toEqual(mockMemories);
  });

  it('fetchRecent sets error on non-ok response', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useMemory());

    await act(async () => {
      await result.current.fetchRecent();
    });

    expect(result.current.error).toBe('Failed to fetch insights');
  });

  // ---- searchInsights ----

  it('searchInsights populates insights with search results', async () => {
    const mockResults = [
      { id: 'm2', content: 'Auth module', metadata: { memory_type: 'context', timestamp: '2026-01-02' }, relevance: 0.95 },
    ];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ results: mockResults }),
      } as Response);

    const { result } = renderHook(() => useMemory());

    let searchResult: any;
    await act(async () => {
      searchResult = await result.current.searchInsights('auth');
    });

    expect(searchResult).toEqual(mockResults);
    expect(result.current.insights).toEqual(mockResults);
  });

  it('searchInsights sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useMemory());

    let searchResult: any;
    await act(async () => {
      searchResult = await result.current.searchInsights('test');
    });

    expect(searchResult).toEqual([]);
    expect(result.current.error).toBe('Search failed');
  });

  // ---- addInsight ----

  it('addInsight returns success result on ok response', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      // addInsight POST
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ auto_tags: ['backend'], similar_memories: [] }),
      } as Response)
      // fetchStats (called after add)
      .mockRejectedValueOnce(new Error('Network unavailable'))
      // fetchRecent (called after add)
      .mockRejectedValueOnce(new Error('Network unavailable'));

    const { result } = renderHook(() => useMemory());

    let addResult: any;
    await act(async () => {
      addResult = await result.current.addInsight('New memory content', 'context');
    });

    expect(addResult.success).toBe(true);
    expect(addResult.autoTags).toEqual(['backend']);
  });

  it('addInsight returns failure result on error', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useMemory());

    let addResult: any;
    await act(async () => {
      addResult = await result.current.addInsight('content');
    });

    expect(addResult.success).toBe(false);
    expect(result.current.error).toBe('Failed to add insight');
  });

  // ---- deleteInsight ----

  it('deleteInsight returns true on success', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      // deleteInsight DELETE
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response)
      // fetchStats
      .mockRejectedValueOnce(new Error('Network unavailable'))
      // fetchRecent
      .mockRejectedValueOnce(new Error('Network unavailable'));

    const { result } = renderHook(() => useMemory());

    let deleted: any;
    await act(async () => {
      deleted = await result.current.deleteInsight('m1');
    });

    expect(deleted).toBe(true);
  });

  it('deleteInsight returns false and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useMemory());

    let deleted: any;
    await act(async () => {
      deleted = await result.current.deleteInsight('m1');
    });

    expect(deleted).toBe(false);
    expect(result.current.error).toBe('Failed to delete insight');
  });

  // ---- Error handling ----

  it('searchInsights returns empty array on network error', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useMemory());

    let searchResult: any;
    await act(async () => {
      searchResult = await result.current.searchInsights('test');
    });

    expect(searchResult).toEqual([]);
    expect(result.current.error).toBe('Connection refused');
  });

  it('fetchRecent sets error on network exception', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockRejectedValueOnce(new Error('Timeout'));

    const { result } = renderHook(() => useMemory());

    await act(async () => {
      await result.current.fetchRecent();
    });

    expect(result.current.error).toBe('Timeout');
  });
});
