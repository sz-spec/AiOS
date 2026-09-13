/**
 * VOS3 Kernel Bridge Hook
 *
 * Provides React state management for kernel operations:
 * system status, process list, filesystem, and execution.
 *
 * Delegates core status/lifecycle to the Zustand kernel store
 * while keeping HTTP-only operations (processes, filesystem, exec)
 * local to preserve backward compatibility.
 *
 * Provenance: 100% original VOS3 code, no external sources.
 * Audited 2026-04-12.
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';
import { useKernelStore } from '@/lib/stores/kernel-store';
import { tauriReadInferenceOutput, tauriWarpReadZone } from '@/lib/tauri-bridge';

const API_BASE = '';

export interface KernelStatus {
  connected: boolean;
  ping: boolean;
  data: string;
  sysinfo: {
    uptime_ms?: number;
    mem_total_kb?: number;
    mem_free_kb?: number;
    tasks?: number;
  };
}

export interface KernelProcess {
  pid: number;
  name: string;
  state: string;
  ppid: number;
}

export interface KernelFile {
  path: string;
  content: string;
  size: number;
}

export interface FilesystemInfo {
  path: string;
  files: string[];
  count: number;
  stat: Record<string, number | string> | null;
}

export interface ExecResult {
  exit_code?: number;
  size?: number;
  error?: string;
  [key: string]: unknown;
}

export function useKernel() {
  const { getToken } = useAuth();

  // ---- Zustand store (dual-mode: Tauri IPC / HTTP) ----
  const store = useKernelStore();

  // Initialize desktop detection on mount
  useEffect(() => {
    store.initialize();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Stable ref for store.fetchStatus to avoid heartbeat interval churn
  const fetchStatusRef = useRef(store.fetchStatus);
  fetchStatusRef.current = store.fetchStatus;

  // Health check heartbeat: poll status every 5s when kernel is running
  useEffect(() => {
    if (!store.isDesktop || !store.qemuAlive) return;
    const interval = setInterval(() => {
      fetchStatusRef.current(getToken);
    }, 5000);
    return () => clearInterval(interval);
  }, [store.isDesktop, store.qemuAlive, getToken]);

  // ---- Local state for HTTP-only operations ----
  const [processes, setProcesses] = useState<KernelProcess[]>([]);
  const [filesystem, setFilesystem] = useState<FilesystemInfo | null>(null);

  // ---- Delegated to store ----

  const fetchStatus = useCallback(async () => {
    await store.fetchStatus(getToken);
  }, [store, getToken]);

  // ---- HTTP-only operations (no Tauri equivalent yet) ----

  const fetchProcesses = useCallback(async () => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/processes`);
      if (!res.ok) throw new Error(`Status ${res.status}`);
      const data = await res.json();
      setProcesses(data.processes || []);
    } catch {
      // Process list unavailable
    }
  }, [getToken]);

  const fetchFilesystem = useCallback(async (path: string = '/disk') => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/filesystem?path=${encodeURIComponent(path)}`);
      if (!res.ok) throw new Error(`Status ${res.status}`);
      const data = await res.json();
      setFilesystem(data);
    } catch {
      // Filesystem browse unavailable
    }
  }, [getToken]);

  const readFile = useCallback(async (path: string): Promise<KernelFile | null> => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/file?path=${encodeURIComponent(path)}`);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }, [getToken]);

  const writeFile = useCallback(async (path: string, content: string): Promise<boolean> => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/file`, {
        method: 'POST',
        body: JSON.stringify({ path, content }),
      });
      return res.ok;
    } catch {
      return false;
    }
  }, [getToken]);

  const deleteFile = useCallback(async (path: string): Promise<boolean> => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/file?path=${encodeURIComponent(path)}`, {
        method: 'DELETE',
      });
      return res.ok;
    } catch {
      return false;
    }
  }, [getToken]);

  const executeProgram = useCallback(async (path: string, args: string = ''): Promise<ExecResult> => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/execute`, {
        method: 'POST',
        body: JSON.stringify({ path, args }),
      });
      if (!res.ok) throw new Error(`Status ${res.status}`);
      return await res.json();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Unknown error';
      return { error: msg, exit_code: -1 };
    }
  }, [getToken]);

  const mkDir = useCallback(async (path: string): Promise<boolean> => {
    try {
      const res = await apiFetch(getToken, `${API_BASE}/api/kernel/mkdir?path=${encodeURIComponent(path)}`, {
        method: 'POST',
      });
      return res.ok;
    } catch {
      return false;
    }
  }, [getToken]);

  // ---- Compose status for backward compat ----
  const status: KernelStatus | null = store.connected || store.qemuAlive
    ? {
        connected: store.connected,
        ping: store.vbusConnected,
        data: '',
        sysinfo: store.sysinfo,
      }
    : null;

  return {
    // Backward-compatible returns
    status,
    processes,
    filesystem,
    loading: store.loading,
    error: store.error,
    fetchStatus,
    fetchProcesses,
    fetchFilesystem,
    readFile,
    writeFile,
    deleteFile,
    executeProgram,
    mkDir,

    // Warp Drive read commands
    readInferenceOutput: useCallback(async (slotId: number) => {
      if (!store.isDesktop) return null;
      return tauriReadInferenceOutput(slotId);
    }, [store.isDesktop]),

    readWarpZone: useCallback(async (slotId: number, offset: number, len: number) => {
      if (!store.isDesktop) return null;
      return tauriWarpReadZone(slotId, offset, len);
    }, [store.isDesktop]),

    // Desktop-only extensions
    isDesktop: store.isDesktop,
    startKernel: store.startKernel,
    stopKernel: store.stopKernel,
    loadModel: store.loadModel,
    kernelPath: store.kernelPath,
    setKernelPath: store.setKernelPath,
    modelLoadProgress: store.modelLoadProgress,
    qemuAlive: store.qemuAlive,
    vbusConnected: store.vbusConnected,
    vbusHmac: store.vbusHmac,
    warpOpen: store.warpOpen,
    slots: store.slots,
    fetchSlots: () => store.fetchSlots(getToken),
    fetchSystemInfo: () => store.fetchSystemInfo(getToken),
  };
}
