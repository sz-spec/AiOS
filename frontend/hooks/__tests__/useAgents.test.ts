import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useAgents } from '../useAgents';

describe('useAgents', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with empty agents, empty logs, and null error', () => {
    const { result } = renderHook(() => useAgents());
    expect(result.current.agents).toEqual([]);
    expect(result.current.logs).toEqual({});
    expect(result.current.error).toBeNull();
  });

  it('starts with isLoading false', () => {
    const { result } = renderHook(() => useAgents());
    expect(result.current.isLoading).toBe(false);
  });

  // ---- loadAgents ----

  it('loadAgents populates agents on success', async () => {
    const mockAgents = [
      { id: 'a1', name: 'Coder', role: 'backend', status: 'idle', run_count: 0 },
    ];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('useEffect loadAgents')) // useEffect
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ agents: mockAgents }),
      } as Response);

    const { result } = renderHook(() => useAgents());

    await act(async () => {
      await result.current.loadAgents();
    });

    expect(result.current.agents).toEqual(mockAgents);
  });

  it('loadAgents on mount via useEffect when API available', async () => {
    const mockAgents = [{ id: 'a1', name: 'Tester', role: 'tester', status: 'idle' }];

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ agents: mockAgents }),
    } as Response);

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.agents).toEqual(mockAgents);
    });
  });

  // ---- createAgent ----

  it('createAgent appends agent to state on success', async () => {
    const newAgent = { id: 'a2', name: 'Reviewer', role: 'reviewer', status: 'idle' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('useEffect loadAgents'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => newAgent,
      } as Response);

    const { result } = renderHook(() => useAgents());

    let created: any;
    await act(async () => {
      created = await result.current.createAgent({ name: 'Reviewer', role: 'reviewer' });
    });

    expect(created).toEqual(newAgent);
    expect(result.current.agents).toContainEqual(newAgent);
  });

  it('createAgent sets error on HTTP error', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('useEffect loadAgents'))
      .mockResolvedValueOnce({ ok: false, status: 400 } as Response);

    const { result } = renderHook(() => useAgents());

    let caughtError: any;
    await act(async () => {
      try {
        await result.current.createAgent({ name: 'Bad', role: 'test' });
      } catch (err) {
        caughtError = err;
      }
    });

    expect(caughtError).toBeDefined();
    expect(caughtError.message).toBe('HTTP error: 400');
    expect(result.current.error).toBe('HTTP error: 400');
  });

  // ---- executeTask (runAgent) ----

  it('executeTask returns result and resets agent status to idle on success', async () => {
    const mockAgent = { id: 'a1', name: 'Coder', role: 'backend', status: 'idle' as const, run_count: 0 };
    const mockResult = { success: true, result: 'Code output', tokens_used: 100, cost: 0.01, duration_ms: 500 };

    vi.spyOn(global, 'fetch')
      // useEffect loadAgents
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ agents: [mockAgent] }),
      } as Response)
      // executeTask
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockResult,
      } as Response);

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.agents).toHaveLength(1);
    });

    let taskResult: any;
    await act(async () => {
      taskResult = await result.current.executeTask('a1', 'Write a function');
    });

    expect(taskResult).toEqual(mockResult);
    expect(result.current.agents[0].status).toBe('idle');
  });

  it('executeTask sets agent status to error on failure', async () => {
    const mockAgent = { id: 'a1', name: 'Coder', role: 'backend', status: 'idle' as const };

    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ agents: [mockAgent] }),
      } as Response)
      .mockResolvedValueOnce({ ok: false, status: 500 } as Response);

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.agents).toHaveLength(1);
    });

    let caughtError: any;
    await act(async () => {
      try {
        await result.current.executeTask('a1', 'Fail task');
      } catch (err) {
        caughtError = err;
      }
    });

    expect(caughtError).toBeDefined();
    expect(caughtError.message).toBe('HTTP error: 500');
    expect(result.current.agents[0].status).toBe('error');
    expect(result.current.error).toBe('HTTP error: 500');
  });

  // ---- deleteAgent ----

  it('deleteAgent removes agent from state on success', async () => {
    const mockAgent = { id: 'a1', name: 'Old Agent', role: 'backend', status: 'idle' as const };

    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ agents: [mockAgent] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.agents).toHaveLength(1);
    });

    await act(async () => {
      await result.current.deleteAgent('a1');
    });

    expect(result.current.agents).toHaveLength(0);
  });

  it('deleteAgent sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('useEffect loadAgents'))
      .mockResolvedValueOnce({ ok: false, status: 404 } as Response);

    const { result } = renderHook(() => useAgents());

    let caughtError: any;
    await act(async () => {
      try {
        await result.current.deleteAgent('nonexistent');
      } catch (err) {
        caughtError = err;
      }
    });

    expect(caughtError).toBeDefined();
    expect(caughtError.message).toBe('HTTP error: 404');
    expect(result.current.error).toBe('HTTP error: 404');
  });

  // ---- Error handling ----

  it('createAgent sets error on network failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('useEffect loadAgents'))
      .mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useAgents());

    let caughtError: any;
    await act(async () => {
      try {
        await result.current.createAgent({ name: 'Test', role: 'test' });
      } catch (err) {
        caughtError = err;
      }
    });

    expect(caughtError).toBeDefined();
    expect(caughtError.message).toBe('Connection refused');
    expect(result.current.error).toBe('Connection refused');
  });
});
