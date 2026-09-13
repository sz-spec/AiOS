'use client';

import { useMemo, useState } from 'react';
import {
  SandpackProvider,
  SandpackPreview,
  SandpackLayout,
} from '@codesandbox/sandpack-react';
import { DeviceFrame } from './DeviceFrame';
import { PreviewToolbar } from './PreviewToolbar';
import { usePreview, DeviceType } from '@/hooks/usePreview';
import { useConsoleCapture, ConsoleEntry } from '@/hooks/useConsoleCapture';
import { ConsolePanel } from '@/components/debug/ConsolePanel';
import { AIDebugPanel } from '@/components/debug/AIDebugPanel';

interface LivePreviewProps {
  files: Record<string, string>;
  onVisualEditToggle?: () => void;
  onErrorClick?: (entry: ConsoleEntry) => void;
  onSendToChat?: (error: ConsoleEntry, analysis: any) => void;
  /** Phase 3.5: Design image URL for Pixel Sync overlay */
  overlayImage?: string | null;
}

export function LivePreview({ files, onVisualEditToggle, onErrorClick, onSendToChat, overlayImage }: LivePreviewProps) {
  const preview = usePreview(files);
  const console = useConsoleCapture();
  const [debugError, setDebugError] = useState<ConsoleEntry | null>(null);
  const [isOverlayActive, setIsOverlayActive] = useState(false);
  const [overlayOpacity, setOverlayOpacity] = useState(50);

  // Determine entry file and setup
  const hasFiles = Object.keys(files).length > 0;

  // Build sandpack files object
  const sandpackFiles = useMemo(() => {
    if (!hasFiles) return {};
    const result: Record<string, { code: string }> = {};
    for (const [path, content] of Object.entries(files)) {
      const key = path.startsWith('/') ? path : `/${path}`;
      result[key] = { code: content };
    }
    return result;
  }, [files, hasFiles]);

  const showOverlay = isOverlayActive && overlayImage;

  if (!hasFiles) {
    return (
      <div style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
      }}>
        <PreviewToolbar
          device={preview.device}
          zoom={preview.zoom}
          isVisualEditMode={preview.isVisualEditMode}
          onDeviceChange={preview.setDevice}
          onZoomChange={preview.setZoom}
          onToggleVisualEdit={onVisualEditToggle || preview.toggleVisualEdit}
          onRefresh={preview.refresh}
        />
        <div style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: 'var(--bg-tertiary)',
          color: 'var(--text-secondary)',
          fontSize: '14px',
        }}>
          No preview available — build your project first
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
      <PreviewToolbar
        device={preview.device}
        zoom={preview.zoom}
        isVisualEditMode={preview.isVisualEditMode}
        onDeviceChange={preview.setDevice}
        onZoomChange={preview.setZoom}
        onToggleVisualEdit={onVisualEditToggle || preview.toggleVisualEdit}
        onRefresh={preview.refresh}
        isOverlayActive={isOverlayActive}
        onToggleOverlay={overlayImage ? () => setIsOverlayActive(!isOverlayActive) : undefined}
        overlayOpacity={overlayOpacity}
        onOverlayOpacityChange={setOverlayOpacity}
      />
      <DeviceFrame device={preview.device} zoom={preview.zoom}>
        <div style={{ position: 'relative', width: '100%', height: '100%' }}>
          <SandpackProvider
            key={preview.refreshKey}
            template="react-ts"
            files={sandpackFiles}
            options={{
              externalResources: ['https://cdn.tailwindcss.com'],
            }}
            theme="light"
          >
            <SandpackLayout style={{ height: '100%', border: 'none' }}>
              <SandpackPreview
                showNavigator={false}
                showOpenInCodeSandbox={false}
                showRefreshButton={false}
                style={{ height: '100%' }}
              />
            </SandpackLayout>
          </SandpackProvider>
          {/* Pixel Sync overlay */}
          {showOverlay && (
            <img
              src={overlayImage}
              alt="Design overlay"
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                opacity: overlayOpacity / 100,
                pointerEvents: 'none',
                zIndex: 10,
              }}
            />
          )}
        </div>
      </DeviceFrame>
      <ConsolePanel
        entries={console.logs}
        onClear={console.clear}
        onEntryClick={onErrorClick}
        onSendToAI={(entry) => setDebugError(entry)}
        maxHeight={200}
      />
      <AIDebugPanel
        error={debugError}
        projectFiles={files}
        onApplyFix={undefined}
        onSendToChat={onSendToChat ? (err, analysis) => onSendToChat(err, analysis) : undefined}
      />
    </div>
  );
}
