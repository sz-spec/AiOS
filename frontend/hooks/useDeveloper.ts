import { useState, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

// ───────────── Types ─────────────

export interface DeveloperProfile {
  id: string;
  email: string;
  name: string;
  verified: boolean;
  created_at: string;
  app_count: number;
  total_downloads: number;
}

export interface DeveloperApp {
  slug: string;
  name: string;
  icon: string;
  status: 'draft' | 'in_review' | 'approved' | 'rejected' | 'published';
  downloads: number;
  rating: number;
  revenue: number;
  updated_at: string;
}

export interface Earnings {
  total_earnings: number;
  pending_payout: number;
  last_payout: number;
  last_payout_date: string | null;
  monthly: { month: string; amount: number }[];
}

export interface AnalyticsOverview {
  total_installs: number;
  active_users: number;
  uninstalls: number;
  avg_rating: number;
  revenue_30d: number;
  installs_30d: number;
  daily: { date: string; installs: number; revenue: number }[];
}

export interface AppManifest {
  name: string;
  slug: string;
  description: string;
  short_description: string;
  category: string;
  icon?: string;
  screenshots?: string[];
  pricing: 'free' | 'paid' | 'freemium';
  price?: number;
  permissions: string[];
  source_url?: string;
  version: string;
}

// ───────────── Hook ─────────────

export function useDeveloper() {
  const [profile, setProfile] = useState<DeveloperProfile | null>(null);
  const [apps, setApps] = useState<DeveloperApp[]>([]);
  const [earnings, setEarnings] = useState<Earnings | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsOverview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getToken } = useAuth();

  const register = useCallback(async (email: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/register`, {
        method: 'POST',
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProfile(data.profile ?? data);
      return data.profile ?? data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to register';
      setError(msg);
      console.error('Developer register error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const getProfile = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/profile`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProfile(data.profile ?? data);
      return data.profile ?? data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load profile';
      setError(msg);
      console.error('Developer getProfile error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const getApps = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/apps`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setApps(data.apps ?? []);
      return data.apps as DeveloperApp[];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load apps';
      setError(msg);
      console.error('Developer getApps error:', err);
      return [];
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const submitApp = useCallback(async (manifest: AppManifest) => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/apps/submit`, {
        method: 'POST',
        body: JSON.stringify(manifest),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // Refresh app list after submission
      await getApps();
      return data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to submit app';
      setError(msg);
      console.error('Developer submitApp error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getApps, getToken]);

  const getEarnings = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/earnings`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEarnings(data.earnings ?? data);
      return data.earnings ?? data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load earnings';
      setError(msg);
      console.error('Developer getEarnings error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const getAnalytics = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await apiFetch(getToken, `${API_URL}/api/developers/analytics`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAnalytics(data.analytics ?? data);
      return data.analytics ?? data;
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load analytics';
      setError(msg);
      console.error('Developer getAnalytics error:', err);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  return {
    profile,
    apps,
    earnings,
    analytics,
    isLoading,
    error,
    register,
    getProfile,
    getApps,
    submitApp,
    getEarnings,
    getAnalytics,
  };
}
