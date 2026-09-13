import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDeveloper } from '../useDeveloper';

describe('useDeveloper', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with null profile, empty apps, no error', () => {
    const { result } = renderHook(() => useDeveloper());
    expect(result.current.profile).toBeNull();
    expect(result.current.apps).toEqual([]);
    expect(result.current.earnings).toBeNull();
    expect(result.current.analytics).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  // ---- register ----

  it('register() sends POST to /api/developers/register', async () => {
    const mockProfile = { id: 'dev1', email: 'dev@test.com', name: 'Dev' };
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ profile: mockProfile }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    let returned: any;
    await act(async () => {
      returned = await result.current.register('dev@test.com');
    });

    expect(returned).toEqual(mockProfile);
    expect(result.current.profile).toEqual(mockProfile);

    const fetchCall = (global.fetch as any).mock.calls[0];
    expect(fetchCall[0]).toContain('/api/developers/register');
    expect(fetchCall[1].method).toBe('POST');
  });

  // ---- getProfile ----

  it('getProfile() fetches developer profile', async () => {
    const mockProfile = { id: 'dev1', email: 'dev@test.com' };
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ profile: mockProfile }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    await act(async () => {
      await result.current.getProfile();
    });

    expect(result.current.profile).toEqual(mockProfile);
  });

  // ---- getApps ----

  it('getApps() fetches app list', async () => {
    const mockApps = [{ slug: 'app1', name: 'App 1' }];
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: mockApps }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    let returned: any;
    await act(async () => {
      returned = await result.current.getApps();
    });

    expect(returned).toEqual(mockApps);
    expect(result.current.apps).toEqual(mockApps);
  });

  // ---- submitApp ----

  it('submitApp() sends manifest to /api/developers/apps/submit', async () => {
    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ submission_id: 'sub1' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ apps: [] }),
      } as Response);

    const { result } = renderHook(() => useDeveloper());

    const manifest = {
      name: 'Test App',
      slug: 'test-app',
      description: 'A test app',
      short_description: 'Test',
      category: 'productivity',
      pricing: 'free' as const,
      permissions: ['vos3:entities:read'],
      version: '1.0.0',
    };

    await act(async () => {
      await result.current.submitApp(manifest);
    });

    const submitCall = (global.fetch as any).mock.calls[0];
    expect(submitCall[0]).toContain('/api/developers/apps/submit');
    expect(submitCall[1].method).toBe('POST');
  });

  // ---- getEarnings ----

  it('getEarnings() fetches earnings data', async () => {
    const mockEarnings = { total_earnings: 100, pending_payout: 20 };
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ earnings: mockEarnings }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    await act(async () => {
      await result.current.getEarnings();
    });

    expect(result.current.earnings).toEqual(mockEarnings);
  });

  // ---- getAnalytics ----

  it('getAnalytics() fetches analytics overview', async () => {
    const mockAnalytics = { total_installs: 500, active_users: 100 };
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ analytics: mockAnalytics }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    await act(async () => {
      await result.current.getAnalytics();
    });

    expect(result.current.analytics).toEqual(mockAnalytics);
  });

  // ---- Error handling ----

  it('sets error on fetch failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useDeveloper());

    await act(async () => {
      await result.current.register('dev@test.com');
    });

    expect(result.current.error).toBe('Connection refused');
  });

  // ---- Loading state ----

  it('loading state management', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ profile: {} }),
    } as Response);

    const { result } = renderHook(() => useDeveloper());

    await act(async () => {
      await result.current.getProfile();
    });

    expect(result.current.isLoading).toBe(false);
  });
});
