'use client';

import { useEffect, useState } from 'react';
import { useTheme } from 'next-themes';
import { Moon, Sun } from 'lucide-react';
import { SkeletonBlock } from '@/components/shared/Skeleton';

interface ThemeToggleProps {
  collapsed?: boolean;
}

/**
 * Width reserved for the post-mount label, big enough for both
 * "Light mode" and "Dark mode" without wrapping. This eliminates the
 * pre-mount → mounted text swap that previously caused a 1-frame
 * layout shift inside the sidebar footer.
 */
const LABEL_RESERVED_PX = 72;

/**
 * Icon footprint. The lucide icons render at 18×18; the skeleton
 * placeholder shown until hydration completes uses the same dimensions
 * so the icon column does not change size.
 */
const ICON_PX = 18;

export function ThemeToggle({ collapsed = false }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = mounted && resolvedTheme === 'dark';
  const nextTheme = isDark ? 'light' : 'dark';
  const label = isDark ? 'Switch to light theme' : 'Switch to dark theme';

  return (
    <button
      type="button"
      onClick={() => setTheme(nextTheme)}
      title={label}
      aria-label={label}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
        width: '100%',
        padding: collapsed ? '10px' : '10px 12px',
        borderRadius: 'var(--radius-sm)',
        color: 'var(--text-secondary)',
        backgroundColor: 'transparent',
        fontSize: '14px',
        fontWeight: 400,
        justifyContent: collapsed ? 'center' : 'flex-start',
        transition: 'background-color 0.15s ease',
      }}
      onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
      onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      {/* Icon column: skeleton placeholder until mount, then Sun/Moon.
          Both occupy the same 18×18 box, so no horizontal shift. */}
      <span
        aria-hidden="true"
        style={{
          width: ICON_PX,
          height: ICON_PX,
          flexShrink: 0,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {mounted ? (
          isDark ? <Sun size={ICON_PX} /> : <Moon size={ICON_PX} />
        ) : (
          <SkeletonBlock width={ICON_PX} height={ICON_PX} radius={4} />
        )}
      </span>
      {/* Label column: reserved width so the pre-mount placeholder and
          the mounted label ("Light mode" / "Dark mode") leave the
          surrounding flex layout dimensions unchanged. */}
      {!collapsed && (
        <span
          style={{
            minWidth: LABEL_RESERVED_PX,
            display: 'inline-block',
            textAlign: 'left',
          }}
        >
          {mounted ? (
            isDark ? 'Light mode' : 'Dark mode'
          ) : (
            <SkeletonBlock width={LABEL_RESERVED_PX} height={12} radius={4} />
          )}
        </span>
      )}
    </button>
  );
}
