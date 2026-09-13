'use client';

import Link from 'next/link';
import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  ArrowRight,
  CircleAlert,
  CircleCheck,
  File as FileIcon,
  FolderTree,
  MessageSquare,
  Plus,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { SkeletonBlock, SkeletonRegion } from '@/components/shared/Skeleton';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { usePlatform } from '@/lib/hooks/usePlatform';
import { VERTICALS, type VerticalId } from '@/lib/marketplace/verticals';
import { workflowsDirFor } from '@/lib/marketplace/paths';

const API_BASE = '';
const ROOT_DIR = '/disk';

type KernelStatus = 'unknown' | 'online' | 'offline';

interface FileEntry {
  name: string;
  size: number;
  type: string;
  /** Kernel inode mtime (uint64 seconds). 0 = unknown / not tracked. */
  mtime: number;
}

interface DashboardData {
  loading: boolean;
  kernel: KernelStatus;
  workflowCount: number | null;
  recentFiles: FileEntry[];
}

const initialState: DashboardData = {
  loading: true,
  kernel: 'unknown',
  workflowCount: null,
  recentFiles: [],
};

export default function HomePage() {
  const { getToken } = useAuth();
  const { platform, setPlatform, vertical } = usePlatform();
  const [data, setData] = useState<DashboardData>(initialState);
  const isMobile = useMediaQuery('(max-width: 767px)');

  useEffect(() => {
    let cancelled = false;
    setData(initialState);

    async function load() {
      const token = await getToken().catch(() => null);
      const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
      const workflowsDir = workflowsDirFor(platform);

      const [workflowsRes, rootRes] = await Promise.allSettled([
        fetch(`${API_BASE}/api/v1/files/?path=${encodeURIComponent(workflowsDir)}`, { headers }),
        fetch(`${API_BASE}/api/v1/files/?path=${encodeURIComponent(ROOT_DIR)}`, { headers }),
      ]);

      if (cancelled) return;

      // Kernel status: 503 anywhere means offline; any 2xx means online; all-failed = unknown.
      let kernel: KernelStatus = 'unknown';
      const responses = [workflowsRes, rootRes];
      for (const r of responses) {
        if (r.status === 'fulfilled') {
          if (r.value.status === 503) kernel = 'offline';
          else if (r.value.ok && kernel !== 'offline') kernel = 'online';
        }
      }

      // Workflow count: filter .json entries; missing dir (404) counts as 0.
      let workflowCount = 0;
      if (workflowsRes.status === 'fulfilled' && workflowsRes.value.ok) {
        const body = await workflowsRes.value.json().catch(() => null);
        if (body && Array.isArray(body.entries)) {
          workflowCount = body.entries.filter(
            (e: FileEntry) => e.type !== 'dir' && e.name.endsWith('.json'),
          ).length;
        }
      }

      // Recent files: kernel LSM now surfaces mtime per entry (D-01).
      // Sort by mtime DESC so the user actually sees their most recent
      // edits. Entries with mtime=0 (untracked filesystems / older
      // kernels) sink to the bottom — they're tied with each other and
      // come after any real timestamp.
      let recentFiles: FileEntry[] = [];
      if (rootRes.status === 'fulfilled' && rootRes.value.ok) {
        const body = await rootRes.value.json().catch(() => null);
        if (body && Array.isArray(body.entries)) {
          recentFiles = (body.entries as FileEntry[])
            .filter((e) => e.type !== 'dir')
            .sort((a, b) => (b.mtime ?? 0) - (a.mtime ?? 0))
            .slice(0, 3);
        }
      }

      setData({ loading: false, kernel, workflowCount, recentFiles });
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [getToken, platform]);

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: 'var(--bg-primary)',
        paddingTop: isMobile ? 60 : 0,
      }}
    >
      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '40px 24px' }}>
        <Hero />

        <PlatformSwitcher
          platform={platform}
          onChange={setPlatform}
          isMobile={isMobile}
        />

        <section style={{ marginTop: 28 }}>
          <SectionTitle>
            {platform === 'all' ? 'Overview' : `Overview · ${vertical.label}`}
          </SectionTitle>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)',
              gap: 16,
              marginTop: 12,
            }}
          >
            <ActiveWorkflowsCard
              loading={data.loading}
              count={data.workflowCount}
              kernel={data.kernel}
              platform={platform}
            />
            <RecentFilesCard loading={data.loading} files={data.recentFiles} kernel={data.kernel} />
            <SystemHealthCard loading={data.loading} kernel={data.kernel} />
          </div>
        </section>

        <section style={{ marginTop: 40 }}>
          <SectionTitle>Quick Actions</SectionTitle>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)',
              gap: 16,
              marginTop: 12,
            }}
          >
            <QuickAction
              href="/chat"
              icon={<MessageSquare size={22} />}
              label="New Chat"
              description="Multi-model AI conversation"
              accent="#2563eb"
            />
            <QuickAction
              href="/workflows"
              icon={<WorkflowIcon size={22} />}
              label="New Workflow"
              description="Visual workflow builder"
              accent="#a855f7"
            />
            <QuickAction
              href="/files"
              icon={<FolderTree size={22} />}
              label="File Manager"
              description="Browse the kernel VFS"
              accent="#16a34a"
            />
          </div>
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function Hero() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
      <div style={{ minWidth: 0 }}>
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 12,
              background: 'linear-gradient(135deg, #d97706, #ea580c)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: 20,
              fontWeight: 700,
            }}
          >
            V
          </div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em' }}>
            VOS3 Dashboard
          </h1>
        </motion.div>
        <p style={{ margin: 0, fontSize: 14, color: 'var(--text-secondary)', maxWidth: 540, lineHeight: 1.5 }}>
          A live snapshot of your kernel, workflows, and files. Jump back in below.
        </p>
      </div>
      <Link
        href="/create"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 18px',
          backgroundColor: 'var(--accent)',
          color: 'white',
          borderRadius: 'var(--radius-full)',
          fontSize: 14,
          fontWeight: 600,
        }}
      >
        Start building
        <ArrowRight size={16} />
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Platform switcher
// ---------------------------------------------------------------------------

/**
 * Quick toggle for the active VOS3 vertical. Drives the entire OS context:
 * the marketplace, dashboard, workflow save path, and any future
 * per-platform surface read from the same `usePlatform()` source.
 *
 * Renders as scroll-x chips on mobile and a wrap-flow on desktop. The
 * "All Platforms" entry stays first so users always have an exit hatch.
 */
function PlatformSwitcher({
  platform,
  onChange,
  isMobile,
}: {
  platform: VerticalId;
  onChange: (next: VerticalId) => void;
  isMobile: boolean;
}) {
  return (
    <div style={{ marginTop: 24 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}
      >
        Active Platform
      </div>
      <div
        role="tablist"
        aria-label="Active platform"
        style={{
          display: 'flex',
          gap: 6,
          flexWrap: isMobile ? 'nowrap' : 'wrap',
          overflowX: isMobile ? 'auto' : 'visible',
          paddingBottom: isMobile ? 6 : 0,
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {VERTICALS.map((v) => {
          const active = v.id === platform;
          return (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(v.id)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '6px 12px',
                fontSize: 12,
                fontWeight: active ? 600 : 500,
                color: active ? v.accent : 'var(--text-secondary)',
                backgroundColor: active ? `${v.accent}1A` : 'var(--bg-secondary)',
                border: `1px solid ${active ? `${v.accent}66` : 'var(--border-light)'}`,
                borderRadius: 'var(--radius-full)',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                transition: 'background-color 120ms ease, border-color 120ms ease',
              }}
              onMouseOver={(e) => {
                if (!active) e.currentTarget.style.backgroundColor = 'var(--bg-hover)';
              }}
              onMouseOut={(e) => {
                if (!active) e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
              }}
            >
              <span aria-hidden="true">{v.icon}</span>
              {v.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section helpers
// ---------------------------------------------------------------------------

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        color: 'var(--text-tertiary)',
      }}
    >
      {children}
    </div>
  );
}

function Card({
  children,
  href,
}: {
  children: React.ReactNode;
  href?: string;
}) {
  const inner = (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ duration: 0.15 }}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        padding: 20,
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-lg)',
        minHeight: 160,
        cursor: href ? 'pointer' : 'default',
        transition: 'border-color 120ms ease',
      }}
      onMouseOver={(e) => {
        if (href) e.currentTarget.style.borderColor = 'var(--text-tertiary)';
      }}
      onMouseOut={(e) => {
        if (href) e.currentTarget.style.borderColor = 'var(--border-light)';
      }}
    >
      {children}
    </motion.div>
  );
  return href ? <Link href={href} style={{ display: 'block' }}>{inner}</Link> : inner;
}

function CardHeader({
  icon,
  title,
  accent,
}: {
  icon: React.ReactNode;
  title: string;
  accent: string;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: 8,
          backgroundColor: `${accent}1A`,
          color: accent,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {icon}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

function ActiveWorkflowsCard({
  loading,
  count,
  kernel,
  platform,
}: {
  loading: boolean;
  count: number | null;
  kernel: KernelStatus;
  platform: VerticalId;
}) {
  const dir = workflowsDirFor(platform);
  return (
    <Card href={`/workflows${platform === 'all' ? '' : `?platform=${platform}`}`}>
      <CardHeader icon={<WorkflowIcon size={16} />} title="Active Workflows" accent="#a855f7" />
      {loading ? (
        <SkeletonRegion label="Loading workflow count">
          <SkeletonBlock width={80} height={32} radius={6} />
        </SkeletonRegion>
      ) : kernel === 'offline' ? (
        <CardOffline />
      ) : (
        <>
          <div
            style={{
              fontSize: 32,
              fontWeight: 700,
              letterSpacing: '-0.02em',
              color: 'var(--text-primary)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {count ?? 0}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            {count === 1 ? 'JSON file' : 'JSON files'} in {dir}
          </div>
        </>
      )}
    </Card>
  );
}

function RecentFilesCard({
  loading,
  files,
  kernel,
}: {
  loading: boolean;
  files: FileEntry[];
  kernel: KernelStatus;
}) {
  return (
    <Card href="/files">
      <CardHeader icon={<FileIcon size={16} />} title="Recent Files" accent="#16a34a" />
      {loading ? (
        <SkeletonRegion label="Loading recent files">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[0, 1, 2].map((i) => (
              <SkeletonBlock key={i} width="80%" height={14} delayMs={i * 80} />
            ))}
          </div>
        </SkeletonRegion>
      ) : kernel === 'offline' ? (
        <CardOffline />
      ) : files.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>No files yet in {ROOT_DIR}.</div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {files.map((f) => (
            <li
              key={f.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                color: 'var(--text-primary)',
              }}
            >
              <FileIcon size={14} aria-hidden="true" style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
              <span
                style={{
                  flex: 1,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {f.name}
              </span>
              <span
                style={{ fontSize: 11, color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }}
                title={f.mtime > 0 ? new Date(f.mtime * 1000).toISOString() : undefined}
              >
                {f.mtime > 0 ? formatRelativeMtime(f.mtime) : formatSize(f.size)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function SystemHealthCard({ loading, kernel }: { loading: boolean; kernel: KernelStatus }) {
  const config = HEALTH_CONFIG[kernel];
  return (
    <Card>
      <CardHeader icon={<CircleCheck size={16} />} title="System Health" accent={config.accent} />
      {loading ? (
        <SkeletonRegion label="Checking kernel">
          <SkeletonBlock width={120} height={20} radius={6} />
        </SkeletonRegion>
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              aria-hidden="true"
              style={{
                width: 10,
                height: 10,
                borderRadius: 9999,
                backgroundColor: config.accent,
                boxShadow: `0 0 0 4px ${config.accent}1F`,
              }}
            />
            <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
              {config.label}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{config.detail}</div>
        </>
      )}
    </Card>
  );
}

const HEALTH_CONFIG: Record<
  KernelStatus,
  { label: string; detail: string; accent: string }
> = {
  online: {
    label: 'Kernel Online',
    detail: 'VBus reachable, files API responding.',
    accent: '#16a34a',
  },
  offline: {
    label: 'Kernel Offline',
    detail: 'VBus returned 503. Start the kernel to restore service.',
    accent: '#dc2626',
  },
  unknown: {
    label: 'Status Unknown',
    detail: 'Could not reach the backend. Check network connectivity.',
    accent: '#d97706',
  },
};

function CardOffline() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 13,
        color: 'var(--text-tertiary)',
      }}
    >
      <CircleAlert size={14} aria-hidden="true" />
      Kernel offline — data unavailable.
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quick action
// ---------------------------------------------------------------------------

function QuickAction({
  href,
  icon,
  label,
  description,
  accent,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  description: string;
  accent: string;
}) {
  return (
    <Link href={href} style={{ display: 'block' }}>
      <motion.div
        whileHover={{ y: -2 }}
        whileTap={{ scale: 0.98 }}
        transition={{ duration: 0.15 }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: 20,
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-light)',
          borderLeft: `4px solid ${accent}`,
          borderRadius: 'var(--radius-lg)',
          minHeight: 88,
          cursor: 'pointer',
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            flexShrink: 0,
            borderRadius: 'var(--radius-md)',
            backgroundColor: `${accent}1A`,
            color: accent,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
            {label}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{description}</div>
        </div>
        <Plus size={18} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} aria-hidden="true" />
      </motion.div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log10(bytes) / 3), units.length - 1);
  const v = bytes / Math.pow(1000, i);
  return `${v >= 10 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

/**
 * Compact relative formatter for the dashboard's Recent Files row.
 * `seconds` is a kernel mtime (uint64 seconds since epoch). Caller is
 * expected to pre-check `seconds > 0` — this helper is unconditional.
 *
 * Heuristic: values < 1e9 are RAMFS-uptime-relative; we render those
 * as "uptime+Ns" so a vos3fs+RAMFS mixed listing is readable.
 */
function formatRelativeMtime(seconds: number): string {
  if (seconds < 1_000_000_000) return `+${Math.floor(seconds)}s`;
  const diff = Date.now() - seconds * 1000;
  if (diff < 0) return new Date(seconds * 1000).toLocaleDateString();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
}
