'use client';

import { Monitor, Tablet, Smartphone, RefreshCw, ExternalLink, ZoomIn, ZoomOut, MousePointer, Layers } from 'lucide-react';
import { DeviceType } from '@/hooks/usePreview';

interface PreviewToolbarProps {
  device: DeviceType;
  zoom: number;
  isVisualEditMode: boolean;
  onDeviceChange: (device: DeviceType) => void;
  onZoomChange: (zoom: number) => void;
  onToggleVisualEdit: () => void;
  onRefresh: () => void;
  onOpenExternal?: () => void;
  // Phase 3.5: Pixel Sync overlay
  isOverlayActive?: boolean;
  onToggleOverlay?: () => void;
  overlayOpacity?: number;
  onOverlayOpacityChange?: (n: number) => void;
}

const devices: { id: DeviceType; icon: typeof Monitor; label: string }[] = [
  { id: 'desktop', icon: Monitor, label: 'Desktop' },
  { id: 'tablet', icon: Tablet, label: 'Tablet' },
  { id: 'mobile', icon: Smartphone, label: 'Mobile' },
];

export function PreviewToolbar({
  device,
  zoom,
  isVisualEditMode,
  onDeviceChange,
  onZoomChange,
  onToggleVisualEdit,
  onRefresh,
  onOpenExternal,
  isOverlayActive = false,
  onToggleOverlay,
  overlayOpacity = 50,
  onOverlayOpacityChange,
}: PreviewToolbarProps) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '8px 12px',
      borderBottom: '1px solid var(--border-light)',
      backgroundColor: 'var(--bg-secondary)',
      fontSize: '13px',
    }}>
      {/* Left: Device selectors */}
      <div style={{ display: 'flex', gap: '2px' }}>
        {devices.map((d) => {
          const Icon = d.icon;
          const isActive = device === d.id;
          return (
            <button
              key={d.id}
              onClick={() => onDeviceChange(d.id)}
              title={d.label}
              style={{
                padding: '6px 10px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: isActive ? 'var(--bg-primary)' : 'transparent',
                color: isActive ? 'var(--text-primary)' : 'var(--text-tertiary)',
                border: isActive ? '1px solid var(--border-light)' : '1px solid transparent',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
              }}
            >
              <Icon size={14} />
            </button>
          );
        })}
      </div>

      {/* Center: Zoom */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button
          onClick={() => onZoomChange(zoom - 25)}
          style={{ padding: '4px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
        >
          <ZoomOut size={14} />
        </button>
        <span style={{ minWidth: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>{zoom}%</span>
        <button
          onClick={() => onZoomChange(zoom + 25)}
          style={{ padding: '4px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
        >
          <ZoomIn size={14} />
        </button>
      </div>

      {/* Right: Actions */}
      <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
        {/* Pixel Sync toggle */}
        {onToggleOverlay && (
          <>
            <button
              onClick={onToggleOverlay}
              title={isOverlayActive ? 'Hide Pixel Sync' : 'Pixel Sync'}
              style={{
                padding: '6px 12px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: isOverlayActive ? 'var(--accent)' : 'transparent',
                color: isOverlayActive ? 'white' : 'var(--text-secondary)',
                border: isOverlayActive ? 'none' : '1px solid var(--border-light)',
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '12px',
                fontWeight: 500,
              }}
            >
              <Layers size={13} />
              Pixel Sync
            </button>
            {isOverlayActive && onOverlayOpacityChange && (
              <input
                type="range"
                min="0"
                max="100"
                value={overlayOpacity}
                onChange={(e) => onOverlayOpacityChange(Number(e.target.value))}
                title={`Opacity: ${overlayOpacity}%`}
                style={{ width: '60px', accentColor: 'var(--accent)' }}
              />
            )}
          </>
        )}
        <button
          onClick={onToggleVisualEdit}
          title={isVisualEditMode ? 'Exit Visual Edit' : 'Visual Edit'}
          style={{
            padding: '6px 12px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: isVisualEditMode ? 'var(--accent)' : 'transparent',
            color: isVisualEditMode ? 'white' : 'var(--text-secondary)',
            border: isVisualEditMode ? 'none' : '1px solid var(--border-light)',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            fontSize: '12px',
            fontWeight: 500,
          }}
        >
          <MousePointer size={13} />
          Visual Edit
        </button>
        <button
          onClick={onRefresh}
          title="Refresh"
          style={{ padding: '6px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
        >
          <RefreshCw size={14} />
        </button>
        {onOpenExternal && (
          <button
            onClick={onOpenExternal}
            title="Open in new tab"
            style={{ padding: '6px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
          >
            <ExternalLink size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
