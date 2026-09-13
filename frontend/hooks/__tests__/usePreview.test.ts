import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePreview, DEVICE_SIZES } from '../usePreview';

describe('usePreview', () => {
  it('defaults to desktop device', () => {
    const { result } = renderHook(() => usePreview({}));
    expect(result.current.device).toBe('desktop');
  });

  it('defaults to 100% zoom', () => {
    const { result } = renderHook(() => usePreview({}));
    expect(result.current.zoom).toBe(100);
  });

  it('defaults visual edit mode off', () => {
    const { result } = renderHook(() => usePreview({}));
    expect(result.current.isVisualEditMode).toBe(false);
  });

  it('setDevice switches to mobile with correct size', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setDevice('mobile'));
    expect(result.current.device).toBe('mobile');
    expect(result.current.deviceSize).toEqual(DEVICE_SIZES.mobile);
    expect(result.current.deviceSize).toEqual({ width: 375, height: 667 });
  });

  it('setDevice switches to tablet with correct size', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setDevice('tablet'));
    expect(result.current.device).toBe('tablet');
    expect(result.current.deviceSize).toEqual({ width: 768, height: 1024 });
  });

  it('setDevice back to desktop restores desktop size', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setDevice('mobile'));
    act(() => result.current.setDevice('desktop'));
    expect(result.current.deviceSize).toEqual({ width: 1280, height: 800 });
  });

  it('setZoom clamps value below 25 to 25', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setZoom(10));
    expect(result.current.zoom).toBe(25);
  });

  it('setZoom clamps value above 200 to 200', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setZoom(500));
    expect(result.current.zoom).toBe(200);
  });

  it('setZoom accepts valid value within range', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setZoom(150));
    expect(result.current.zoom).toBe(150);
  });

  it('setZoom accepts boundary values 25 and 200', () => {
    const { result } = renderHook(() => usePreview({}));
    act(() => result.current.setZoom(25));
    expect(result.current.zoom).toBe(25);
    act(() => result.current.setZoom(200));
    expect(result.current.zoom).toBe(200);
  });

  it('sandpackFiles adds / prefix to paths without it', () => {
    const { result } = renderHook(() =>
      usePreview({ 'index.html': '<html/>', 'app.js': 'code' })
    );
    expect(result.current.sandpackFiles['/index.html']).toBe('<html/>');
    expect(result.current.sandpackFiles['/app.js']).toBe('code');
  });

  it('sandpackFiles preserves existing / prefix', () => {
    const { result } = renderHook(() => usePreview({ '/styles.css': 'body{}' }));
    expect(result.current.sandpackFiles['/styles.css']).toBe('body{}');
    // should not double-prefix
    expect(result.current.sandpackFiles['//styles.css']).toBeUndefined();
  });

  it('sandpackFiles returns empty object for empty input', () => {
    const { result } = renderHook(() => usePreview({}));
    expect(Object.keys(result.current.sandpackFiles)).toHaveLength(0);
  });

  it('refresh increments refreshKey each call', () => {
    const { result } = renderHook(() => usePreview({}));
    const initial = result.current.refreshKey;
    act(() => result.current.refresh());
    expect(result.current.refreshKey).toBe(initial + 1);
    act(() => result.current.refresh());
    expect(result.current.refreshKey).toBe(initial + 2);
  });

  it('toggleVisualEdit flips isVisualEditMode', () => {
    const { result } = renderHook(() => usePreview({}));
    expect(result.current.isVisualEditMode).toBe(false);
    act(() => result.current.toggleVisualEdit());
    expect(result.current.isVisualEditMode).toBe(true);
    act(() => result.current.toggleVisualEdit());
    expect(result.current.isVisualEditMode).toBe(false);
  });
});
