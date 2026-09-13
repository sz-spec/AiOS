import { useState, useCallback, useMemo } from 'react';

export type DeviceType = 'desktop' | 'tablet' | 'mobile';

export const DEVICE_SIZES: Record<DeviceType, { width: number; height: number }> = {
  desktop: { width: 1280, height: 800 },
  tablet: { width: 768, height: 1024 },
  mobile: { width: 375, height: 667 },
};

interface PreviewState {
  device: DeviceType;
  zoom: number;
  isVisualEditMode: boolean;
  refreshKey: number;
}

export function usePreview(files: Record<string, string>) {
  const [state, setState] = useState<PreviewState>({
    device: 'desktop',
    zoom: 100,
    isVisualEditMode: false,
    refreshKey: 0,
  });

  const setDevice = useCallback((device: DeviceType) => {
    setState((s) => ({ ...s, device }));
  }, []);

  const setZoom = useCallback((zoom: number) => {
    setState((s) => ({ ...s, zoom: Math.max(25, Math.min(200, zoom)) }));
  }, []);

  const toggleVisualEdit = useCallback(() => {
    setState((s) => ({ ...s, isVisualEditMode: !s.isVisualEditMode }));
  }, []);

  const refresh = useCallback(() => {
    setState((s) => ({ ...s, refreshKey: s.refreshKey + 1 }));
  }, []);

  // Convert project files to Sandpack format
  const sandpackFiles = useMemo(() => {
    const result: Record<string, string> = {};
    for (const [path, content] of Object.entries(files)) {
      // Sandpack expects paths starting with /
      const key = path.startsWith('/') ? path : `/${path}`;
      result[key] = content;
    }
    return result;
  }, [files]);

  return {
    ...state,
    setDevice,
    setZoom,
    toggleVisualEdit,
    refresh,
    sandpackFiles,
    deviceSize: DEVICE_SIZES[state.device],
  };
}
