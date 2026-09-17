import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useKernel } from '../useKernel';
import { useKernelStore } from '@/lib/stores/kernel-store';

describe('useKernel', () => {
  beforeEach(() => {
    useKernelStore.setState(useKernelStore.getInitialState(), true);
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with null status and empty processes', () => {
    const { result } = renderHook(() => useKernel());
    expect(result.current.status).toBeNull();
    expect(result.current.processes).toEqual([]);
  });

  it('starts with null filesystem, loading false, and error null', () => {
    const { result } = renderHook(() => useKernel());
    expect(result.current.filesystem).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('heartbeat uses the latest committed status action and stops when the kernel stops', async () => {
    vi.useFakeTimers();
    const initial = vi.fn(async () => {});
    const replacement = vi.fn(async () => {});
    useKernelStore.setState({ initialize: vi.fn(), isDesktop: true, qemuAlive: true, fetchStatus: initial });
    const { unmount } = renderHook(() => useKernel());
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(initial).toHaveBeenCalledTimes(1);
      await act(async () => { useKernelStore.setState({ fetchStatus: replacement }); });
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(initial).toHaveBeenCalledTimes(1);
      expect(replacement).toHaveBeenCalledTimes(1);
      await act(async () => { useKernelStore.setState({ qemuAlive: false }); });
      await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
      expect(replacement).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      vi.useRealTimers();
    }
  });

  // ---- fetchStatus ----

  it('fetchStatus populates status on success', async () => {
    const mockStatus = {
      connected: true,
      ping: true,
      data: 'pong',
      sysinfo: { uptime_ms: 5000, mem_total_kb: 524288, mem_free_kb: 262144, tasks: 3 },
    };

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatus,
    } as Response);

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchStatus();
    });

    expect(result.current.status).toEqual(mockStatus);
    // A successful HTTP ping must not manufacture desktop vbus state.
    expect(useKernelStore.getState().vbusConnected).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
    useKernelStore.setState({ qemuAlive: true });
    vi.mocked(fetch).mockRejectedValueOnce(new Error('refresh failed'));
    await act(async () => { await result.current.fetchStatus(); });
    expect(result.current.status?.ping).toBe(false);
    expect(result.current.status?.data).toBe('');
  });

  it('fetchStatus sets error and clears status on HTTP failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 503,
    } as Response);

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchStatus();
    });

    expect(result.current.error).toBe('Status 503');
    expect(result.current.status).toBeNull();
  });

  it('fetchStatus sets error on network failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('ECONNREFUSED'));

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchStatus();
    });

    expect(result.current.error).toBe('ECONNREFUSED');
    expect(result.current.status).toBeNull();
  });

  // ---- fetchProcesses ----

  it('fetchProcesses populates process list on success', async () => {
    const mockProcesses = [
      { pid: 1, name: 'init', state: 'running', ppid: 0 },
      { pid: 2, name: 'shell', state: 'running', ppid: 1 },
    ];

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ processes: mockProcesses }),
    } as Response);

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchProcesses();
    });

    expect(result.current.processes).toEqual(mockProcesses);
  });

  it('fetchProcesses sets error on failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 500,
    } as Response);

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchProcesses();
    });

    expect(result.current.error).toBe('Status 500');
    vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ processes: [] }) } as Response);
    await act(async () => { await result.current.fetchProcesses(); });
    expect(result.current.error).toBeNull();
    expect(result.current.processes).toEqual([]);
  });

  // ---- fetchFilesystem ----

  it('fetchFilesystem populates filesystem info on success', async () => {
    const mockFs = { path: '/disk', files: ['hello.txt', 'data.bin'], count: 2, stat: null };

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockFs,
    } as Response);

    const { result } = renderHook(() => useKernel());

    await act(async () => {
      await result.current.fetchFilesystem('/disk');
    });

    expect(result.current.filesystem).toEqual(mockFs);
  });

  // ---- readFile ----

  it('readFile returns file data on success', async () => {
    const mockFile = { path: '/disk/hello.txt', content: 'Hello world', size: 11 };

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockFile,
    } as Response);

    const { result } = renderHook(() => useKernel());

    let file: any;
    await act(async () => {
      file = await result.current.readFile('/disk/hello.txt');
    });

    expect(file).toEqual(mockFile);
  });

  it('readFile returns null on failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 404,
    } as Response);

    const { result } = renderHook(() => useKernel());

    let file: any;
    await act(async () => {
      file = await result.current.readFile('/disk/missing.txt');
    });

    expect(file).toBeNull();
  });

  // ---- writeFile ----

  it('writeFile returns true on success', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);

    const { result } = renderHook(() => useKernel());

    let success: boolean = false;
    await act(async () => {
      success = await result.current.writeFile('/disk/test.txt', 'content');
    });

    expect(success).toBe(true);
  });

  it('writeFile returns false on network error', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useKernel());

    let success: boolean = true;
    await act(async () => {
      success = await result.current.writeFile('/disk/test.txt', 'content');
    });

    expect(success).toBe(false);
  });

  // ---- deleteFile ----

  it('deleteFile returns true on success', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);

    const { result } = renderHook(() => useKernel());

    let success: boolean = false;
    await act(async () => {
      success = await result.current.deleteFile('/disk/test.txt');
    });

    expect(success).toBe(true);
  });

  // ---- executeProgram ----

  it('executeProgram returns result on success', async () => {
    const mockResult = { exit_code: 0, size: 100 };

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockResult,
    } as Response);

    const { result } = renderHook(() => useKernel());

    let execResult: any;
    await act(async () => {
      execResult = await result.current.executeProgram('/disk/hello.elf');
    });

    expect(execResult).toEqual(mockResult);
  });

  it('executeProgram returns error result on failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Execution failed'));

    const { result } = renderHook(() => useKernel());

    let execResult: any;
    await act(async () => {
      execResult = await result.current.executeProgram('/disk/bad.elf');
    });

    expect(execResult.error).toBe('Execution failed');
    expect(execResult.exit_code).toBe(-1);
  });

  // ---- mkDir ----

  it('mkDir returns true on success', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    } as Response);

    const { result } = renderHook(() => useKernel());

    let success: boolean = false;
    await act(async () => {
      success = await result.current.mkDir('/disk/newdir');
    });

    expect(success).toBe(true);
  });

  it('mkDir returns false on network error', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useKernel());

    let success: boolean = true;
    await act(async () => {
      success = await result.current.mkDir('/disk/newdir');
    });

    expect(success).toBe(false);
  });
});
