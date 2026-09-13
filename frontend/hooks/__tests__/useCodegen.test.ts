import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCodegen } from '../useCodegen';

describe('useCodegen', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with empty generatedFiles and null progress/error', () => {
    const { result } = renderHook(() => useCodegen());
    expect(result.current.generatedFiles).toEqual([]);
    expect(result.current.progress).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it('starts with isGenerating false', () => {
    const { result } = renderHook(() => useCodegen());
    expect(result.current.isGenerating).toBe(false);
  });

  // ---- generateCode success ----

  it('generateCode populates generatedFiles on success', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ code: 'console.log("hello")' }),
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateCode({ prompt: 'Write hello world', language: 'typescript' });
    });

    expect(result.current.generatedFiles).toHaveLength(1);
    expect(result.current.generatedFiles[0].path).toBe('generated.typescript');
    expect(result.current.generatedFiles[0].content).toBe('console.log("hello")');
    expect(result.current.generatedFiles[0].language).toBe('typescript');
  });

  it('generateCode sets progress to complete on success', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ code: 'print("hi")' }),
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateCode({ prompt: 'Write hello', language: 'python' });
    });

    expect(result.current.progress).toEqual({
      stage: 'complete',
      percent: 100,
      message: 'Generation complete!',
    });
  });

  // ---- generateCode error ----

  it('generateCode sets error on HTTP failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 500,
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateCode({ prompt: 'Fail', language: 'python' });
    });

    expect(result.current.error).toBe('HTTP error: 500');
    expect(result.current.progress).toBeNull();
    expect(result.current.isGenerating).toBe(false);
  });

  it('generateCode sets error on network failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateCode({ prompt: 'Fail', language: 'python' });
    });

    expect(result.current.error).toBe('Connection refused');
    expect(result.current.progress).toBeNull();
  });

  // ---- generateProject ----

  it('generateProject populates generatedFiles with multiple files', async () => {
    const mockFiles = [
      { path: 'index.ts', content: 'export {}', language: 'typescript' },
      { path: 'package.json', content: '{}', language: 'json' },
    ];

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ files: mockFiles }),
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateProject({ prompt: 'Create a project' });
    });

    expect(result.current.generatedFiles).toEqual(mockFiles);
    expect(result.current.progress?.stage).toBe('complete');
  });

  it('generateProject sets error on failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 422,
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateProject({ prompt: 'Bad project' });
    });

    expect(result.current.error).toBe('HTTP error: 422');
    expect(result.current.progress).toBeNull();
  });

  // ---- clearFiles ----

  it('clearFiles resets generatedFiles, progress, and error', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ code: 'code' }),
    } as Response);

    const { result } = renderHook(() => useCodegen());

    await act(async () => {
      await result.current.generateCode({ prompt: 'test', language: 'js' });
    });

    expect(result.current.generatedFiles).toHaveLength(1);

    act(() => {
      result.current.clearFiles();
    });

    expect(result.current.generatedFiles).toEqual([]);
    expect(result.current.progress).toBeNull();
    expect(result.current.error).toBeNull();
  });
});
