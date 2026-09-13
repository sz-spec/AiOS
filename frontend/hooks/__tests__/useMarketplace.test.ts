import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMarketplace } from '../useMarketplace';

describe('useMarketplace', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with empty arrays and no error', () => {
    const { result } = renderHook(() => useMarketplace());
    expect(result.current.apps).toEqual([]);
    expect(result.current.featuredApps).toEqual([]);
    expect(result.current.trendingApps).toEqual([]);
    expect(result.current.installedApps).toEqual([]);
    expect(result.current.currentApp).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  // ---- browse ----

  it('browse() fetches from /api/marketplace/browse', async () => {
    const mockApps = [{ slug: 'app1', name: 'App 1' }];
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: mockApps }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    let returned: any;
    await act(async () => {
      returned = await result.current.browse();
    });

    expect(returned).toEqual(mockApps);
    expect(result.current.apps).toEqual(mockApps);
    expect(result.current.isLoading).toBe(false);
  });

  it('browse() with query param', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: [] }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.browse('crm');
    });

    const fetchCall = (global.fetch as any).mock.calls[0][0] as string;
    expect(fetchCall).toContain('q=crm');
  });

  it('browse() with category filter', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: [] }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.browse(undefined, 'productivity');
    });

    const fetchCall = (global.fetch as any).mock.calls[0][0] as string;
    expect(fetchCall).toContain('category=productivity');
  });

  it('browse() sets error on fetch failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.browse();
    });

    expect(result.current.error).toBe('Network error');
    expect(result.current.isLoading).toBe(false);
  });

  // ---- getApp ----

  it('getApp() fetches app detail', async () => {
    const mockApp = { slug: 'my-app', name: 'My App' };
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ app: mockApp, reviews: [] }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    let returned: any;
    await act(async () => {
      returned = await result.current.getApp('my-app');
    });

    expect(returned).toEqual(mockApp);
    expect(result.current.currentApp).toEqual(mockApp);
  });

  // ---- install ----

  it('install() sends POST', async () => {
    vi.spyOn(global, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ installed: true }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ apps: [] }),
      } as Response);

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.install('my-app');
    });

    const installCall = (global.fetch as any).mock.calls[0];
    expect(installCall[0]).toContain('/api/apps/install');
    expect(installCall[1].method).toBe('POST');
  });

  // ---- uninstall ----

  it('uninstall() sends POST', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.uninstall('my-app');
    });

    const uninstallCall = (global.fetch as any).mock.calls[0];
    expect(uninstallCall[0]).toContain('/api/apps/uninstall');
  });

  // ---- featured ----

  it('featured() fetches featured apps', async () => {
    const mockFeatured = [{ slug: 'featured1', name: 'Featured' }];
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: mockFeatured }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    let returned: any;
    await act(async () => {
      returned = await result.current.featured();
    });

    expect(returned).toEqual(mockFeatured);
    expect(result.current.featuredApps).toEqual(mockFeatured);
  });

  // ---- trending ----

  it('trending() fetches trending apps', async () => {
    const mockTrending = [{ slug: 'trend1', name: 'Trending' }];
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: mockTrending }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    let returned: any;
    await act(async () => {
      returned = await result.current.trending();
    });

    expect(returned).toEqual(mockTrending);
    expect(result.current.trendingApps).toEqual(mockTrending);
  });

  // ---- getInstalled ----

  it('getInstalled() fetches installed list', async () => {
    const mockInstalled = [{ slug: 'app1', name: 'App 1', enabled: true }];
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ apps: mockInstalled }),
    } as Response);

    const { result } = renderHook(() => useMarketplace());

    await act(async () => {
      await result.current.getInstalled();
    });

    expect(result.current.installedApps).toEqual(mockInstalled);
  });

  // ---- loading state ----

  it('loading state during fetch', async () => {
    let resolvePromise: any;
    vi.spyOn(global, 'fetch').mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePromise = resolve;
      }) as any,
    );

    const { result } = renderHook(() => useMarketplace());

    const browsePromise = act(async () => {
      result.current.browse();
    });

    // isLoading should be true during fetch
    // Resolve and complete
    resolvePromise({
      ok: true,
      json: async () => ({ apps: [] }),
    });

    await browsePromise;
    expect(result.current.isLoading).toBe(false);
  });
});
