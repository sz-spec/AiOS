import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useBMAD } from './useBMAD';
import { apiFetch } from '@/lib/api-client';

const auth = vi.hoisted(() => ({ getToken: vi.fn(async () => 'initial-token') }));
vi.mock('@clerk/nextjs', () => ({ useAuth: () => auth }));
vi.mock('@/lib/api-client', () => ({ apiFetch: vi.fn() }));
const reply = (data: unknown, ok = true) => ({ ok, json: async () => data }) as Response;

describe('BMAD session dependent loading', () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it('loads both artifacts and approvals with current authentication after rerender', async () => {
    vi.mocked(apiFetch).mockResolvedValue(reply([]));
    const { result, rerender } = renderHook(() => useBMAD());
    const oldLoad = result.current.loadSession;
    auth.getToken = vi.fn(async () => 'updated-token');
    await act(async () => { rerender(); });
    expect(result.current.loadSession).not.toBe(oldLoad);
    const session = { id: 'current', project_name: 'Current project' };
    const artifacts = [{ id: 'artifact-current', content: 'current content' }];
    const approvals = [{ id: 'approval-current', status: 'pending' }];
    vi.mocked(apiFetch).mockClear().mockImplementation(async (_token, url) => {
      if (url === '/api/bmad/sessions/current') return reply(session);
      if (url === '/api/bmad/sessions/current/artifacts') return reply({ artifacts });
      if (url === '/api/bmad/sessions/current/approvals') return reply({ approvals });
      throw new Error(`Unexpected URL: ${url}`);
    });
    await act(async () => { expect(await result.current.loadSession('current')).toEqual(session); });
    expect(apiFetch).toHaveBeenCalledTimes(3);
    for (const [getToken] of vi.mocked(apiFetch).mock.calls) expect(getToken).toBe(auth.getToken);
    expect(result.current.artifacts).toEqual(artifacts);
    expect(result.current.approvals).toEqual(approvals);
    expect(result.current.isLoading).toBe(false);
  });

  it('does not request dependent records when session loading is rejected', async () => {
    vi.mocked(apiFetch).mockResolvedValue(reply([]));
    const { result } = renderHook(() => useBMAD());
    await act(async () => {});
    vi.mocked(apiFetch).mockClear().mockResolvedValue(reply({}, false));
    await act(async () => { expect(await result.current.loadSession('denied')).toBeNull(); });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(result.current.currentSession).toBeNull();
    expect(result.current.artifacts).toEqual([]);
    expect(result.current.approvals).toEqual([]);
    expect(result.current.error).toBe('Failed to load session');
    expect(result.current.isLoading).toBe(false);
  });
});
