import { useState, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

// ───────────── Types ─────────────

export interface MarketplaceApp {
  slug: string;
  name: string;
  icon: string;
  description: string;
  short_description: string;
  developer: string;
  category: string;
  rating: number;
  ratings_count: number;
  downloads: number;
  pricing: 'free' | 'paid' | 'freemium';
  price?: number;
  screenshots: string[];
  permissions: string[];
  version: string;
  updated_at: string;
  installed?: boolean;
  enabled?: boolean;
}

export interface AppReview {
  id: string;
  user: string;
  rating: number;
  comment: string;
  created_at: string;
}

export interface InstalledApp {
  slug: string;
  name: string;
  icon: string;
  version: string;
  enabled: boolean;
  installed_at: string;
}

// ───────────── Hook ─────────────

export function useMarketplace() {
  const [apps, setApps] = useState<MarketplaceApp[]>([]);
  const [featuredApps, setFeaturedApps] = useState<MarketplaceApp[]>([]);
  const [trendingApps, setTrendingApps] = useState<MarketplaceApp[]>([]);
  const [installedApps, setInstalledApps] = useState<InstalledApp[]>([]);
  const [currentApp, setCurrentApp] = useState<MarketplaceApp | null>(null);
  const [reviews, setReviews] = useState<AppReview[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getToken } = useAuth();

  const browse = useCallback(async (query?: string, category?: string, sort?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (query) params.set('q', query);
      if (category) params.set('category', category);
      if (sort) params.set('sort', sort);

      const res = await apiFetch(getToken, `${API_URL}/api/marketplace/browse?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setApps(data.apps ?? []);
      return data.apps as MarketplaceApp[];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to browse marketplace';
      setError(msg);
      console.error('Marketplace browse error:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const getApp = useCallback(async (slug: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/marketplace/apps/${slug}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setCurrentApp(data.app ?? data);
      setReviews(data.reviews ?? []);
      return data.app ?? data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load app';
      setError(msg);
      console.error('Marketplace getApp error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const install = useCallback(async (slug: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/apps/install`, {
        method: 'POST',
        body: JSON.stringify({ slug }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // Refresh installed list
      await getInstalled();
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to install app';
      setError(msg);
      console.error('Marketplace install error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const uninstall = useCallback(async (slug: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/apps/uninstall`, {
        method: 'POST',
        body: JSON.stringify({ slug }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setInstalledApps((prev) => prev.filter((a) => a.slug !== slug));
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to uninstall app';
      setError(msg);
      console.error('Marketplace uninstall error:', err);
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const getInstalled = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/apps/installed`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setInstalledApps(data.apps ?? []);
      return data.apps as InstalledApp[];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load installed apps';
      setError(msg);
      console.error('Marketplace getInstalled error:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const toggleApp = useCallback(async (slug: string, enabled: boolean) => {
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/apps/${slug}/toggle`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setInstalledApps((prev) =>
        prev.map((a) => (a.slug === slug ? { ...a, enabled } : a)),
      );
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to toggle app';
      setError(msg);
      console.error('Marketplace toggleApp error:', err);
      return false;
    }
  }, [getToken]);

  const featured = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/marketplace/featured`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFeaturedApps(data.apps ?? []);
      return data.apps as MarketplaceApp[];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load featured apps';
      setError(msg);
      console.error('Marketplace featured error:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const trending = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/marketplace/trending`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTrendingApps(data.apps ?? []);
      return data.apps as MarketplaceApp[];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load trending apps';
      setError(msg);
      console.error('Marketplace trending error:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  return {
    apps,
    featuredApps,
    trendingApps,
    installedApps,
    currentApp,
    reviews,
    isLoading,
    error,
    browse,
    getApp,
    install,
    uninstall,
    getInstalled,
    toggleApp,
    featured,
    trending,
  };
}
