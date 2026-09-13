'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

export interface TransparencyData {
  fresh: boolean;
  root: string | null;
  leaves: number | null;
  error?: string;
}

export function useTransparency(pollIntervalMs = 3000) {
  const { getToken } = useAuth();
  const [data, setData] = useState<TransparencyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Use ref so the interval closure always sees the latest getToken
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  const poll = useCallback(async () => {
    try {
      const res = await apiFetch(getTokenRef.current, '/api/kernel/transparency');
      if (res.ok) {
        const json = await res.json();
        setData(json as TransparencyData);
        setLastUpdated(new Date());
      } else {
        setData({ fresh: false, root: null, leaves: null, error: `HTTP ${res.status}` });
      }
    } catch {
      setData({ fresh: false, root: null, leaves: null, error: 'network error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, pollIntervalMs);
    return () => clearInterval(id);
  }, [poll, pollIntervalMs]);

  return { data, loading, lastUpdated, refresh: poll };
}
