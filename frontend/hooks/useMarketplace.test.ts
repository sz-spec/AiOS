import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMarketplace } from './useMarketplace';
import { apiFetch } from '@/lib/api-client';

const auth = vi.hoisted(() => ({ getToken: vi.fn(async () => 'initial-token') }));
vi.mock('@clerk/nextjs', () => ({ useAuth: () => auth }));
vi.mock('@/lib/api-client', () => ({ apiFetch: vi.fn() }));

const reply = (data: unknown, status = 200) => ({
  ok: status < 400, status, json: async () => data,
}) as Response;

describe('marketplace installation refresh', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('refreshes installed apps after a successful install using current authentication', async () => {
    const { result, rerender } = renderHook(() => useMarketplace());
    const previousInstall = result.current.install;
    auth.getToken = vi.fn(async () => 'updated-token');
    rerender();
    expect(result.current.install).not.toBe(previousInstall);
    const installed = [{ slug: 'notes', name: 'Notes', enabled: true }];
    vi.mocked(apiFetch).mockResolvedValueOnce(reply({ installed: true }))
      .mockResolvedValueOnce(reply({ apps: installed }));
    await act(async () => { expect(await result.current.install('notes')).toEqual({ installed: true }); });
    expect(apiFetch).toHaveBeenNthCalledWith(1, auth.getToken, expect.stringContaining('/api/apps/install'),
      { method: 'POST', body: JSON.stringify({ slug: 'notes' }) });
    expect(apiFetch).toHaveBeenNthCalledWith(2, auth.getToken, expect.stringContaining('/api/apps/installed'));
    expect(result.current.installedApps).toEqual(installed);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('rejects a failed install without refreshing or replacing the installed list', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const { result } = renderHook(() => useMarketplace());
      const installed = [{ slug: 'existing', name: 'Existing', enabled: true }];
      vi.mocked(apiFetch).mockResolvedValueOnce(reply({ apps: installed }));
      await act(async () => { await result.current.getInstalled(); });
      vi.mocked(apiFetch).mockClear().mockResolvedValueOnce(reply({}, 403));
      await act(async () => { expect(await result.current.install('denied')).toBeNull(); });
      expect(apiFetch).toHaveBeenCalledTimes(1);
      expect(result.current.installedApps).toEqual(installed);
      expect(result.current.error).toBe('HTTP 403');
      expect(result.current.isLoading).toBe(false);
    } finally { log.mockRestore(); }
  });
});
