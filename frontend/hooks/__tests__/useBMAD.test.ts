import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useBMAD } from '../useBMAD';

describe('useBMAD', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with empty sessions and null currentSession', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.sessions).toEqual([]);
    expect(result.current.currentSession).toBeNull();
  });

  it('starts with isLoading false and error null', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('starts with empty artifacts, approvals, and events', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.artifacts).toEqual([]);
    expect(result.current.approvals).toEqual([]);
    expect(result.current.events).toEqual([]);
  });

  it('starts with isStreaming false', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.isStreaming).toBe(false);
  });

  it('exposes PHASE_ORDER and PHASE_INFO constants', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.PHASE_ORDER).toHaveLength(9);
    expect(result.current.PHASE_ORDER[0]).toBe('ideation');
    expect(result.current.PHASE_INFO.ideation.label).toBe('Ideation');
  });

  // ---- createSession ----

  it('createSession sets currentSession and prepends to sessions on success', async () => {
    const mockSession = {
      id: 's1',
      project_name: 'Test Project',
      mode: 'guided',
      current_phase: 'ideation',
    };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable')) // useEffect fetchAgents
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSession,
      } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.createSession('Test Project', 'A description', 'guided');
    });

    expect(session).toEqual(mockSession);
    expect(result.current.currentSession).toEqual(mockSession);
    expect(result.current.sessions).toContainEqual(mockSession);
  });

  it('createSession returns null and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.createSession('Test');
    });

    expect(session).toBeNull();
    expect(result.current.error).toBe('Failed to create session');
  });

  // ---- fetchSessions (loadSessions) ----

  it('fetchSessions populates sessions on success', async () => {
    const mockSessions = [
      { id: 's1', project_name: 'Project A' },
      { id: 's2', project_name: 'Project B' },
    ];

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSessions,
      } as Response);

    const { result } = renderHook(() => useBMAD());

    await act(async () => {
      await result.current.fetchSessions();
    });

    expect(result.current.sessions).toEqual(mockSessions);
  });

  it('fetchSessions sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    await act(async () => {
      await result.current.fetchSessions();
    });

    expect(result.current.error).toBe('Failed to fetch sessions');
  });

  // ---- loadSession (selectSession) ----

  it('loadSession sets currentSession on success', async () => {
    const mockSession = { id: 's1', project_name: 'Loaded' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      // loadSession
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSession,
      } as Response)
      // loadArtifacts (called inside loadSession)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ artifacts: [] }),
      } as Response)
      // loadApprovals (called inside loadSession)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ approvals: [] }),
      } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.loadSession('s1');
    });

    expect(session).toEqual(mockSession);
    expect(result.current.currentSession).toEqual(mockSession);
  });

  it('loadSession returns null and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.loadSession('s1');
    });

    expect(session).toBeNull();
    expect(result.current.error).toBe('Failed to load session');
  });

  // ---- deleteSession ----

  it('deleteSession removes session from state on success', async () => {
    const mockSession = { id: 's1', project_name: 'To Delete' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      // createSession
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSession,
      } as Response)
      // deleteSession
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response);

    const { result } = renderHook(() => useBMAD());

    await act(async () => {
      await result.current.createSession('To Delete');
    });
    expect(result.current.sessions).toHaveLength(1);

    let deleted: any;
    await act(async () => {
      deleted = await result.current.deleteSession('s1');
    });

    expect(deleted).toBe(true);
    expect(result.current.sessions).toHaveLength(0);
    expect(result.current.currentSession).toBeNull();
  });

  it('deleteSession returns false and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let deleted: any;
    await act(async () => {
      deleted = await result.current.deleteSession('s1');
    });

    expect(deleted).toBe(false);
    expect(result.current.error).toBe('Failed to delete session');
  });

  // ---- cloneSession ----

  it('cloneSession prepends cloned session to state on success', async () => {
    const clonedSession = { id: 's-clone', project_name: 'Cloned Project' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => clonedSession,
      } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.cloneSession('s1', 'Cloned Project');
    });

    expect(session).toEqual(clonedSession);
    expect(result.current.sessions).toContainEqual(clonedSession);
  });

  it('cloneSession returns null on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let session: any;
    await act(async () => {
      session = await result.current.cloneSession('s1', 'Clone');
    });

    expect(session).toBeNull();
    expect(result.current.error).toBe('Failed to clone session');
  });

  // ---- sendAgentMessage ----

  it('sendAgentMessage returns agent response on success', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ response: 'Agent says hello' }),
      } as Response);

    const { result } = renderHook(() => useBMAD());

    let response: any;
    await act(async () => {
      response = await result.current.sendAgentMessage('s1', 'architect', 'Plan the app');
    });

    expect(response).toBe('Agent says hello');
  });

  it('sendAgentMessage returns null and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let response: any;
    await act(async () => {
      response = await result.current.sendAgentMessage('s1', 'architect', 'Plan');
    });

    expect(response).toBeNull();
    expect(result.current.error).toBe('Failed to send message');
  });

  // ---- advancePhase ----

  it('advancePhase returns true on success', async () => {
    const mockSession = { id: 's1', current_phase: 'discovery' };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      // advancePhase POST
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({}),
      } as Response)
      // loadSession (called inside advancePhase)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSession,
      } as Response)
      // loadArtifacts
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ artifacts: [] }),
      } as Response)
      // loadApprovals
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ approvals: [] }),
      } as Response);

    const { result } = renderHook(() => useBMAD());

    let advanced: any;
    await act(async () => {
      advanced = await result.current.advancePhase('s1');
    });

    expect(advanced).toBe(true);
  });

  it('advancePhase returns false and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    let advanced: any;
    await act(async () => {
      advanced = await result.current.advancePhase('s1');
    });

    expect(advanced).toBe(false);
    expect(result.current.error).toBe('Failed to advance phase');
  });

  // ---- Helper functions ----

  it('getPhaseInfo returns correct info for a phase', () => {
    const { result } = renderHook(() => useBMAD());
    const info = result.current.getPhaseInfo('development');
    expect(info.label).toBe('Development');
    expect(info.description).toBe('Code implementation');
  });

  it('getNextPhase and getPrevPhase navigate phases correctly', () => {
    const { result } = renderHook(() => useBMAD());
    expect(result.current.getNextPhase('ideation')).toBe('discovery');
    expect(result.current.getPrevPhase('discovery')).toBe('ideation');
    expect(result.current.getNextPhase('operations')).toBeNull();
    expect(result.current.getPrevPhase('ideation')).toBeNull();
  });

  it('clearError resets the error to null', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('agents unavailable'))
      .mockResolvedValueOnce({ ok: false } as Response);

    const { result } = renderHook(() => useBMAD());

    await act(async () => {
      await result.current.fetchSessions();
    });
    expect(result.current.error).not.toBeNull();

    act(() => {
      result.current.clearError();
    });

    expect(result.current.error).toBeNull();
  });
});
