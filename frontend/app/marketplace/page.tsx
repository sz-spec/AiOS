'use client';

/**
 * VOS3 Marketplace — Multi-Platform Edition
 * ==========================================
 *
 * VOS3 is the agentic OS underneath several distinct **platforms** (HR &
 * Employees, Industrial Automation, Creative Studio, …). This marketplace
 * is intentionally vertical-first: the active vertical is the primary
 * navigation axis, and every listing declares which verticals it serves.
 *
 * State model:
 *   - URL params drive everything: `?vertical=hr-employees&q=onboard&sort=installs&kind=agent`
 *   - That makes deep-links shareable and lets a platform shell preload
 *     the marketplace already filtered to its own vertical.
 *
 * Scaling assumption:
 *   - Today the listing source is `getAllListings()` (static + seed).
 *   - Tomorrow it can be a Convex query or REST fetch — the UI doesn't
 *     change because it consumes `MarketplaceListing[]`.
 */

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import { motion } from 'framer-motion';
import {
  ArrowRight,
  ArrowUpDown,
  CheckCircle2,
  Download,
  Search as SearchIcon,
  Sparkles,
  Star,
  X,
} from 'lucide-react';
import {
  VERTICALS,
  type MarketplaceListing,
  type ListingKind,
  type VerticalId,
  getVertical,
  matchesVertical,
} from '@/lib/marketplace/verticals';
import { getAllListings } from '@/lib/marketplace/listings';
import {
  useDisplayedInstalls,
  useInstallCountsStore,
} from '@/lib/marketplace/installCountsStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { SkeletonBlock, SkeletonRegion } from '@/components/shared/Skeleton';

type SortKey = 'installs' | 'rating' | 'recent' | 'name';
const SORT_OPTIONS: { id: SortKey; label: string }[] = [
  { id: 'installs', label: 'Most installed' },
  { id: 'rating', label: 'Highest rated' },
  { id: 'recent', label: 'Recently added' },
  { id: 'name', label: 'A–Z' },
];

const KIND_TABS: { id: ListingKind | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'agent', label: 'Agents' },
  { id: 'app', label: 'Apps' },
];

const PAGE_SIZE = 24;

export default function MarketplacePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useMediaQuery('(max-width: 767px)');
  const { getToken } = useAuth();

  // ---- URL-driven state (single source of truth) ----------------------
  const verticalParam = (searchParams.get('vertical') ?? 'all') as VerticalId;
  const vertical = getVertical(verticalParam).id;
  const kindParam = (searchParams.get('kind') ?? 'all') as ListingKind | 'all';
  const sort = (searchParams.get('sort') ?? 'installs') as SortKey;
  const queryParam = searchParams.get('q') ?? '';
  const categoryParam = searchParams.get('category') ?? 'all';

  const [search, setSearch] = useState(queryParam);
  const [page, setPage] = useState(1);

  // Debounce text search → URL.
  useEffect(() => {
    const t = setTimeout(() => updateParam('q', search.trim() || null), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // Sync local search when URL changes via back/forward.
  useEffect(() => {
    setSearch(queryParam);
  }, [queryParam]);

  // Reset pagination on any filter change.
  useEffect(() => {
    setPage(1);
  }, [vertical, kindParam, sort, queryParam, categoryParam]);

  const updateParam = useCallback(
    (key: string, value: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value === null || value === '') params.delete(key);
      else params.set(key, value);
      router.replace(`/marketplace${params.toString() ? `?${params.toString()}` : ''}`, { scroll: false });
    },
    [router, searchParams],
  );

  // ---- Data load ------------------------------------------------------
  const [allListings, setAllListings] = useState<MarketplaceListing[] | null>(null);
  useEffect(() => {
    // Wrapped in Promise.resolve so swapping the source for an async
    // backend call later is a one-line change.
    let cancelled = false;
    Promise.resolve(getAllListings()).then((items) => {
      if (!cancelled) setAllListings(items);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Live install counts. Replaces the seeded `installs` value in the UI
  // with real data from `GET /api/agents/template-stats`. Failure is
  // non-fatal — the listing's seeded baseline keeps rendering.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken().catch(() => null);
        const res = await fetch('/api/agents/template-stats', {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return;
        const body: { counts?: Record<string, number> } = await res.json();
        if (cancelled) return;
        useInstallCountsStore.getState().setReal(body.counts ?? {});
      } catch {
        /* swallow — keep seeded counts */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  // ---- Filter + sort --------------------------------------------------
  const filtered = useMemo(() => {
    if (!allListings) return null;
    const q = queryParam.toLowerCase();
    let out = allListings.filter((l) => {
      if (kindParam !== 'all' && l.kind !== kindParam) return false;
      if (!matchesVertical(l.platform_tags, vertical)) return false;
      if (categoryParam !== 'all' && l.category !== categoryParam) return false;
      if (q) {
        const hay = `${l.name} ${l.description} ${l.tags.join(' ')}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    out = [...out].sort((a, b) => {
      switch (sort) {
        case 'rating':
          return b.rating - a.rating || b.installs - a.installs;
        case 'recent': {
          // Newest first by ISO timestamp. Falls back to id-compare on
          // identical or invalid timestamps so the order stays stable.
          const at = Date.parse(a.created_at);
          const bt = Date.parse(b.created_at);
          if (Number.isFinite(at) && Number.isFinite(bt) && at !== bt) return bt - at;
          return b.id.localeCompare(a.id);
        }
        case 'name':
          return a.name.localeCompare(b.name);
        case 'installs':
        default:
          return b.installs - a.installs;
      }
    });
    return out;
  }, [allListings, kindParam, vertical, categoryParam, queryParam, sort]);

  // Available sub-categories *within the current vertical* — derived,
  // never hardcoded, so adding a new vertical keeps the filter honest.
  const categoriesForVertical = useMemo(() => {
    if (!allListings) return [];
    const counts = new Map<string, number>();
    for (const l of allListings) {
      if (!matchesVertical(l.platform_tags, vertical)) continue;
      counts.set(l.category, (counts.get(l.category) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [allListings, vertical]);

  // Featured strip: top 3 by installs that match the current vertical+kind.
  const featured = useMemo(() => filtered?.slice(0, 3) ?? [], [filtered]);

  // Paginated results below the strip.
  const paginated = useMemo(() => {
    if (!filtered) return [];
    return filtered.slice(0, page * PAGE_SIZE);
  }, [filtered, page]);

  const hasMore = !!filtered && paginated.length < filtered.length;

  const activeVertical = getVertical(vertical);

  // ---- Render ---------------------------------------------------------

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: 'var(--bg-primary)',
        color: 'var(--text-primary)',
        paddingTop: isMobile ? 60 : 0,
      }}
    >
      <div
        style={{
          maxWidth: 1280,
          margin: '0 auto',
          padding: isMobile ? '20px 16px 60px' : '32px 24px 80px',
          display: 'grid',
          gridTemplateColumns: isMobile ? '1fr' : '240px 1fr',
          gap: isMobile ? 16 : 28,
          alignItems: 'start',
        }}
      >
        <VerticalSidebar
          listings={allListings}
          active={vertical}
          isMobile={isMobile}
          onSelect={(v) => updateParam('vertical', v === 'all' ? null : v)}
        />

        <div style={{ minWidth: 0 }}>
          <Header active={activeVertical.id} />

          <FilterBar
            search={search}
            onSearch={setSearch}
            sort={sort}
            onSort={(s) => updateParam('sort', s === 'installs' ? null : s)}
            kind={kindParam}
            onKind={(k) => updateParam('kind', k === 'all' ? null : k)}
          />

          <CategoryChips
            categories={categoriesForVertical}
            active={categoryParam}
            onSelect={(c) => updateParam('category', c === 'all' ? null : c)}
          />

          {filtered === null ? (
            <SkeletonGrid />
          ) : filtered.length === 0 ? (
            <EmptyResults
              hasFilters={!!queryParam || categoryParam !== 'all' || kindParam !== 'all' || vertical !== 'all'}
              onReset={() => {
                router.replace('/marketplace', { scroll: false });
                setSearch('');
              }}
            />
          ) : (
            <>
              {featured.length > 0 && page === 1 && !queryParam && categoryParam === 'all' && (
                <FeaturedStrip listings={featured} accent={activeVertical.accent} currentVertical={vertical} />
              )}

              <ListingGrid listings={paginated} currentVertical={vertical} />

              {hasMore && (
                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 24 }}>
                  <button
                    type="button"
                    onClick={() => setPage((p) => p + 1)}
                    style={{
                      padding: '10px 20px',
                      fontSize: 13,
                      fontWeight: 500,
                      color: 'var(--text-primary)',
                      backgroundColor: 'var(--bg-secondary)',
                      border: '1px solid var(--border-light)',
                      borderRadius: 'var(--radius-full)',
                      cursor: 'pointer',
                    }}
                  >
                    Load more ({(filtered?.length ?? 0) - paginated.length})
                  </button>
                </div>
              )}

              <div
                style={{
                  marginTop: 16,
                  fontSize: 12,
                  color: 'var(--text-tertiary)',
                  textAlign: 'center',
                }}
              >
                Showing {paginated.length} of {filtered.length} listings
                {vertical !== 'all' && ` in ${activeVertical.label}`}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

/**
 * Per-vertical hero copy overrides. The default Header uses each
 * vertical's generic `label` + `tagline`; entries listed here win when
 * present, so high-traffic verticals can carry product-grade titles
 * (e.g. "Industrial Automation Hub") without bloating the taxonomy file.
 */
const HERO_OVERRIDES: Partial<Record<VerticalId, { title: string; subtitle: string }>> = {
  all: {
    title: 'VOS3 Agent Marketplace',
    subtitle: 'Browse every agent and app across the VOS3 ecosystem. Filter by platform on the left.',
  },
  'industrial-automation': {
    title: 'Industrial Automation Hub',
    subtitle:
      'Agents and apps for manufacturing, IoT, OT, and supply-chain orchestration — install and tag for the shop floor.',
  },
  'hr-employees': {
    title: 'Employee Management Suite',
    subtitle:
      'Recruiting, onboarding, performance, and employee-experience automation. One install per workflow you want to delegate.',
  },
};

function Header({ active }: { active: VerticalId }) {
  const v = getVertical(active);
  const override = HERO_OVERRIDES[active];
  const title = override?.title ?? v.label;
  const subtitle = override?.subtitle ?? v.tagline;
  return (
    <motion.div
      key={v.id}
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      style={{ marginBottom: 16 }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--text-tertiary)',
        }}
      >
        VOS3 Marketplace
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
        <span aria-hidden="true" style={{ fontSize: 26 }}>
          {v.icon}
        </span>
        <h1
          style={{
            margin: 0,
            fontSize: 24,
            fontWeight: 700,
            letterSpacing: '-0.02em',
          }}
        >
          {title}
        </h1>
        {v.id !== 'all' && (
          <span
            style={{
              padding: '3px 8px',
              fontSize: 11,
              fontWeight: 600,
              color: v.accent,
              backgroundColor: `${v.accent}1A`,
              border: `1px solid ${v.accent}33`,
              borderRadius: 'var(--radius-full)',
            }}
          >
            Vertical
          </span>
        )}
      </div>
      <p
        style={{
          margin: '6px 0 0',
          fontSize: 14,
          color: 'var(--text-secondary)',
          maxWidth: 640,
          lineHeight: 1.5,
        }}
      >
        {subtitle}
      </p>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Vertical sidebar (becomes a horizontal scrollable bar on mobile)
// ---------------------------------------------------------------------------

function VerticalSidebar({
  listings,
  active,
  isMobile,
  onSelect,
}: {
  listings: MarketplaceListing[] | null;
  active: VerticalId;
  isMobile: boolean;
  onSelect: (v: VerticalId) => void;
}) {
  const counts = useMemo(() => {
    const m = new Map<VerticalId, number>();
    if (!listings) return m;
    for (const v of VERTICALS) m.set(v.id, 0);
    for (const l of listings) {
      m.set('all', (m.get('all') ?? 0) + 1);
      for (const tag of l.platform_tags) m.set(tag, (m.get(tag) ?? 0) + 1);
    }
    return m;
  }, [listings]);

  const containerStyle: React.CSSProperties = isMobile
    ? {
        display: 'flex',
        gap: 8,
        padding: '4px 0',
        overflowX: 'auto',
        WebkitOverflowScrolling: 'touch',
      }
    : {
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: 12,
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-lg)',
        position: 'sticky',
        top: 24,
      };

  return (
    <aside aria-label="Verticals" style={containerStyle}>
      {!isMobile && (
        <div
          style={{
            padding: '4px 8px 8px',
            fontSize: 10,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            color: 'var(--text-tertiary)',
          }}
        >
          Platforms
        </div>
      )}
      {VERTICALS.map((v) => {
        const isActive = v.id === active;
        const count = counts.get(v.id) ?? 0;
        return (
          <button
            key={v.id}
            type="button"
            onClick={() => onSelect(v.id)}
            aria-current={isActive ? 'true' : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: isMobile ? '8px 12px' : '8px 10px',
              flexShrink: 0,
              fontSize: 13,
              fontWeight: isActive ? 600 : 400,
              color: isActive ? v.accent : 'var(--text-secondary)',
              backgroundColor: isActive
                ? `${v.accent}1A`
                : isMobile
                  ? 'var(--bg-secondary)'
                  : 'transparent',
              border: isMobile ? '1px solid var(--border-light)' : 'none',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              transition: 'background-color 120ms ease',
              whiteSpace: 'nowrap',
              textAlign: 'left',
            }}
            onMouseOver={(e) => {
              if (!isActive) e.currentTarget.style.backgroundColor = 'var(--bg-hover)';
            }}
            onMouseOut={(e) => {
              if (!isActive)
                e.currentTarget.style.backgroundColor = isMobile ? 'var(--bg-secondary)' : 'transparent';
            }}
          >
            <span aria-hidden="true" style={{ fontSize: 16 }}>
              {v.icon}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>{v.label}</span>
            {!isMobile && (
              <span
                style={{
                  fontSize: 11,
                  color: isActive ? v.accent : 'var(--text-tertiary)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Filter bar (search + sort + kind tabs)
// ---------------------------------------------------------------------------

function FilterBar({
  search,
  onSearch,
  sort,
  onSort,
  kind,
  onKind,
}: {
  search: string;
  onSearch: (v: string) => void;
  sort: SortKey;
  onSort: (s: SortKey) => void;
  kind: ListingKind | 'all';
  onKind: (k: ListingKind | 'all') => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 10,
        marginTop: 16,
      }}
    >
      <div
        style={{
          flex: '1 1 240px',
          minWidth: 220,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '0 12px',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <SearchIcon size={15} style={{ color: 'var(--text-tertiary)' }} aria-hidden="true" />
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search agents and apps…"
          style={{
            flex: 1,
            padding: '10px 0',
            fontSize: 13,
            color: 'var(--text-primary)',
            backgroundColor: 'transparent',
            border: 'none',
            outline: 'none',
          }}
        />
        {search && (
          <button
            type="button"
            onClick={() => onSearch('')}
            aria-label="Clear search"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 4,
              color: 'var(--text-tertiary)',
              backgroundColor: 'transparent',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            <X size={14} />
          </button>
        )}
      </div>

      <div
        role="tablist"
        aria-label="Listing kind"
        style={{
          display: 'inline-flex',
          padding: 2,
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-full)',
        }}
      >
        {KIND_TABS.map((t) => {
          const active = t.id === kind;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onKind(t.id)}
              style={{
                padding: '6px 14px',
                fontSize: 12,
                fontWeight: active ? 600 : 500,
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                backgroundColor: active ? 'var(--bg-primary)' : 'transparent',
                border: 'none',
                borderRadius: 'var(--radius-full)',
                cursor: 'pointer',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <label
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '8px 12px',
          fontSize: 13,
          color: 'var(--text-secondary)',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-light)',
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <ArrowUpDown size={13} aria-hidden="true" />
        <select
          value={sort}
          onChange={(e) => onSort(e.target.value as SortKey)}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-primary)',
            fontSize: 13,
            outline: 'none',
            cursor: 'pointer',
          }}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Category chip row
// ---------------------------------------------------------------------------

function CategoryChips({
  categories,
  active,
  onSelect,
}: {
  categories: [string, number][];
  active: string;
  onSelect: (c: string) => void;
}) {
  if (categories.length <= 1) return null;
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        marginTop: 12,
      }}
    >
      <Chip label={`All categories`} active={active === 'all'} onClick={() => onSelect('all')} />
      {categories.map(([cat, count]) => (
        <Chip
          key={cat}
          label={`${cat} · ${count}`}
          active={active === cat}
          onClick={() => onSelect(cat)}
        />
      ))}
    </div>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '4px 10px',
        fontSize: 11,
        fontWeight: 500,
        textTransform: 'capitalize',
        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        backgroundColor: active ? 'var(--bg-hover)' : 'transparent',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-full)',
        cursor: 'pointer',
      }}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Featured strip
// ---------------------------------------------------------------------------

function FeaturedStrip({
  listings,
  accent,
  currentVertical,
}: {
  listings: MarketplaceListing[];
  accent: string;
  currentVertical: VerticalId;
}) {
  return (
    <div style={{ marginTop: 24 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 11,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--text-tertiary)',
          marginBottom: 10,
        }}
      >
        <Sparkles size={12} style={{ color: accent }} aria-hidden="true" />
        Featured
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 12,
        }}
      >
        {listings.map((l) => (
          <ListingCard key={l.id} listing={l} featured currentVertical={currentVertical} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main grid
// ---------------------------------------------------------------------------

function ListingGrid({
  listings,
  currentVertical,
}: {
  listings: MarketplaceListing[];
  currentVertical: VerticalId;
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: 12,
        marginTop: 24,
      }}
    >
      {listings.map((l) => (
        <ListingCard key={l.id} listing={l} currentVertical={currentVertical} />
      ))}
    </div>
  );
}

/**
 * Pick the vertical to advertise as "primary" for a listing in the
 * current view.
 *
 * Rule: if the user is filtered to a specific vertical and the listing
 * carries that tag, that's the primary. Otherwise fall back to the
 * listing's first declared tag. The result also becomes the value of
 * `?platform=` on the install link, so the new agent gets persisted with
 * the same vertical the user was browsing in.
 */
function primaryVertical(
  listing: MarketplaceListing,
  current: VerticalId,
): VerticalId {
  if (current !== 'all' && listing.platform_tags.includes(current)) return current;
  return listing.platform_tags[0] ?? 'all';
}

function appendPlatform(href: string, platform: VerticalId): string {
  if (platform === 'all') return href;
  const [path, qs = ''] = href.split('?');
  const params = new URLSearchParams(qs);
  if (!params.has('platform')) params.set('platform', platform);
  const merged = params.toString();
  return merged ? `${path}?${merged}` : path;
}

function ListingCard({
  listing,
  currentVertical,
  featured = false,
}: {
  listing: MarketplaceListing;
  currentVertical: VerticalId;
  featured?: boolean;
}) {
  const primaryId = primaryVertical(listing, currentVertical);
  const primary = getVertical(primaryId);
  const secondaries = listing.platform_tags
    .filter((id) => id !== primaryId)
    .slice(0, 2);
  const installHref = appendPlatform(listing.href, primaryId);
  const installs = useDisplayedInstalls(listing.id, listing.installs);

  return (
    <Link href={installHref} style={{ display: 'block' }} aria-label={`${listing.name} — install`}>
      <motion.div
        whileHover={{ y: -2 }}
        transition={{ duration: 0.15 }}
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-light)',
          borderTop: `3px solid ${primary.accent}`,
          borderRadius: 'var(--radius-lg)',
          overflow: 'hidden',
          cursor: 'pointer',
          transition: 'border-color 120ms ease, transform 120ms ease',
        }}
        onMouseOver={(e) => (e.currentTarget.style.borderColor = 'var(--text-tertiary)')}
        onMouseOut={(e) => (e.currentTarget.style.borderColor = 'var(--border-light)')}
      >
        {/* Vertical-accented header strip — the brand of the platform this
            agent is being installed *into*. Most prominent badge. */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            padding: '6px 12px',
            backgroundColor: `${primary.accent}1A`,
            borderBottom: '1px solid var(--border-light)',
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              color: primary.accent,
            }}
          >
            <span aria-hidden="true">{primary.icon}</span>
            {primary.label}
          </span>
          {featured && (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                color: 'var(--text-tertiary)',
              }}
            >
              Featured
            </span>
          )}
        </div>

        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            padding: 16,
            flex: 1,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
            <div
              style={{
                width: 40,
                height: 40,
                flexShrink: 0,
                borderRadius: 'var(--radius-md)',
                backgroundColor: `${listing.accent}1A`,
                color: listing.accent,
                fontSize: 22,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
              aria-hidden="true"
            >
              {listing.icon}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {listing.name}
                </span>
                {listing.verified && (
                  <CheckCircle2
                    size={13}
                    aria-label="Verified"
                    style={{ color: '#16a34a', flexShrink: 0 }}
                  />
                )}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--text-tertiary)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {listing.author}
              </div>
            </div>
            <PricingBadge pricing={listing.pricing} price={listing.price} />
          </div>

          <p
            style={{
              margin: 0,
              fontSize: 12,
              color: 'var(--text-secondary)',
              lineHeight: 1.5,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {listing.description}
          </p>

          {/* Secondary verticals — quieter chip row, only renders when
              the listing serves multiple verticals. */}
          {secondaries.length > 0 && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 4,
                marginTop: 'auto',
              }}
            >
              {secondaries.map((id) => {
                const sv = getVertical(id);
                return (
                  <span
                    key={id}
                    style={{
                      padding: '2px 8px',
                      fontSize: 10,
                      fontWeight: 500,
                      color: 'var(--text-secondary)',
                      backgroundColor: 'var(--bg-hover)',
                      borderRadius: 'var(--radius-full)',
                    }}
                  >
                    + {sv.label}
                  </span>
                );
              })}
              {listing.platform_tags.length - 1 > secondaries.length && (
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                  +{listing.platform_tags.length - 1 - secondaries.length}
                </span>
              )}
            </div>
          )}

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingTop: 8,
              marginTop: secondaries.length > 0 ? 0 : 'auto',
              borderTop: '1px solid var(--border-light)',
              fontSize: 11,
              color: 'var(--text-tertiary)',
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Star size={11} fill="#f59e0b" stroke="#f59e0b" aria-hidden="true" />
              <span style={{ color: 'var(--text-secondary)' }}>{listing.rating.toFixed(1)}</span>
              <span aria-label={`${installs} installs`} style={{ marginLeft: 4 }}>
                <Download size={11} aria-hidden="true" /> {formatCount(installs)}
              </span>
            </span>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                fontWeight: 600,
                color: primary.accent,
              }}
            >
              Install <ArrowRight size={11} aria-hidden="true" />
            </span>
          </div>
        </div>
      </motion.div>
    </Link>
  );
}

function PricingBadge({ pricing, price }: { pricing: 'free' | 'freemium' | 'paid'; price?: number }) {
  const palette =
    pricing === 'free'
      ? { color: '#16a34a', bg: 'rgba(22,163,74,0.1)' }
      : pricing === 'freemium'
        ? { color: '#d97706', bg: 'rgba(217,119,6,0.1)' }
        : { color: '#2563eb', bg: 'rgba(37,99,235,0.1)' };
  const label = pricing === 'free' ? 'Free' : pricing === 'freemium' ? 'Freemium' : price ? `$${price}` : 'Paid';
  return (
    <span
      style={{
        padding: '2px 8px',
        fontSize: 10,
        fontWeight: 700,
        color: palette.color,
        backgroundColor: palette.bg,
        borderRadius: 'var(--radius-full)',
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// States: loading + empty
// ---------------------------------------------------------------------------

function SkeletonGrid() {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: 12,
        marginTop: 24,
      }}
    >
      <SkeletonRegion label="Loading marketplace">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            style={{
              padding: 16,
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-lg)',
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
            }}
          >
            <div style={{ display: 'flex', gap: 10 }}>
              <SkeletonBlock width={40} height={40} radius={8} delayMs={i * 60} />
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <SkeletonBlock width="70%" height={12} delayMs={i * 60} />
                <SkeletonBlock width="40%" height={10} delayMs={i * 60} />
              </div>
            </div>
            <SkeletonBlock width="100%" height={10} delayMs={i * 60 + 30} />
            <SkeletonBlock width="80%" height={10} delayMs={i * 60 + 60} />
          </div>
        ))}
      </SkeletonRegion>
    </div>
  );
}

function EmptyResults({ hasFilters, onReset }: { hasFilters: boolean; onReset: () => void }) {
  return (
    <div
      style={{
        marginTop: 32,
        padding: '56px 24px',
        textAlign: 'center',
        backgroundColor: 'var(--bg-secondary)',
        border: '1px dashed var(--border-light)',
        borderRadius: 'var(--radius-lg)',
      }}
    >
      <div
        style={{
          width: 56,
          height: 56,
          margin: '0 auto 12px',
          borderRadius: 'var(--radius-full)',
          backgroundColor: 'var(--bg-hover)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-secondary)',
        }}
      >
        <SearchIcon size={26} aria-hidden="true" />
      </div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>No listings match these filters</div>
      <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>
        {hasFilters ? 'Try widening the platform or removing the search.' : 'Nothing to show here yet.'}
      </div>
      {hasFilters && (
        <button
          type="button"
          onClick={onReset}
          style={{
            marginTop: 14,
            padding: '8px 14px',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--text-inverse)',
            backgroundColor: 'var(--accent)',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            cursor: 'pointer',
          }}
        >
          Reset filters
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}
