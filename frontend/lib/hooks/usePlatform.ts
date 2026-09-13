'use client';

/**
 * Active-platform global state.
 *
 * VOS3 is the agentic OS; specific platforms (HR, Industrial, Creative, …)
 * are verticals on top of it. The "active platform" is a piece of UI
 * context that filters the marketplace, dashboard, and any future
 * per-vertical surfaces.
 *
 * Source of truth: the URL search param `?platform=<vertical-id>`.
 * Persistence: reflected to localStorage so users don't lose context when
 * they navigate to a page that didn't carry the param. The URL still wins
 * on every render.
 *
 *   const { platform, setPlatform, vertical, hrefWithPlatform } = usePlatform();
 */

import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { useCallback, useEffect, useMemo } from 'react';
import {
  VERTICALS,
  getVertical,
  type Vertical,
  type VerticalId,
} from '@/lib/marketplace/verticals';

const STORAGE_KEY = 'vos3.platform';
const PARAM_KEY = 'platform';

function isVerticalId(value: string | null): value is VerticalId {
  if (!value) return false;
  return VERTICALS.some((v) => v.id === value);
}

function readStored(): VerticalId | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isVerticalId(raw) ? raw : null;
  } catch {
    return null;
  }
}

function writeStored(value: VerticalId | null) {
  if (typeof window === 'undefined') return;
  try {
    if (value && value !== 'all') window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* swallow — best-effort persistence */
  }
}

export interface UsePlatformResult {
  /** Current vertical id (defaults to 'all'). */
  platform: VerticalId;
  /** Full vertical record for the current platform. */
  vertical: Vertical;
  /** Switch to a new platform; reflects to URL + localStorage. */
  setPlatform: (next: VerticalId) => void;
  /**
   * Append `?platform=<v>` (or merge into an existing query string) so
   * outbound links inside the dashboard / marketplace carry context to
   * the next page. Skips the param when the active platform is 'all'.
   */
  hrefWithPlatform: (href: string) => string;
}

export function usePlatform(): UsePlatformResult {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlValue = searchParams.get(PARAM_KEY);
  const platform: VerticalId = isVerticalId(urlValue) ? urlValue : 'all';

  // Sync URL → storage so a deep-link sets the user's "sticky" context.
  useEffect(() => {
    if (platform === 'all') return;
    writeStored(platform);
  }, [platform]);

  // First-load: if URL has no value but storage does, hydrate the URL.
  useEffect(() => {
    if (urlValue) return;
    const stored = readStored();
    if (!stored) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set(PARAM_KEY, stored);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    // Run once per pathname.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  const setPlatform = useCallback(
    (next: VerticalId) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === 'all') params.delete(PARAM_KEY);
      else params.set(PARAM_KEY, next);
      writeStored(next);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const hrefWithPlatform = useCallback(
    (href: string) => {
      if (platform === 'all') return href;
      const [path, qs = ''] = href.split('?');
      const params = new URLSearchParams(qs);
      // Don't override if the link already carries an explicit platform.
      if (!params.has(PARAM_KEY)) params.set(PARAM_KEY, platform);
      const merged = params.toString();
      return merged ? `${path}?${merged}` : path;
    },
    [platform],
  );

  return useMemo(
    () => ({
      platform,
      vertical: getVertical(platform),
      setPlatform,
      hrefWithPlatform,
    }),
    [platform, setPlatform, hrefWithPlatform],
  );
}
