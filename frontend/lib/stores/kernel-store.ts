// Provenance: 100% original VOS3 code. Zustand persist/immer patterns from
// existing VOS3 stores (settings-store.ts, chat-store.ts). IndexedDB storage
// engine written from scratch. Audited 2026-04-12.

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { persist, createJSONStorage } from 'zustand/middleware';
import {
  isTauri,
  tauriStartKernel,
  tauriStopKernel,
  tauriKernelStatus,
  tauriVbusPing,
  tauriSystemInfo,
  tauriListSlots,
  tauriLoadModel,
  type ModelLoadEvent,
} from '@/lib/tauri-bridge';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SysInfo {
  uptime_ms?: number;
  mem_total_kb?: number;
  mem_free_kb?: number;
  tasks?: number;
  raw?: string;
}

interface KernelState {
  // ---- connection state ----
  connected: boolean;
  qemuAlive: boolean;
  vbusConnected: boolean;
  vbusHmac: boolean;
  warpOpen: boolean;

  // ---- data ----
  sysinfo: SysInfo;
  slots: string | null;
  loading: boolean;
  error: string | null;
  kernelPath: string;
  modelLoadProgress: number | null;
  isDesktop: boolean;

  // ---- actions ----
  initialize: () => void;
  fetchStatus: (getToken: () => Promise<string | null>) => Promise<void>;
  startKernel: (kernelPath: string, qemuPath?: string) => Promise<void>;
  stopKernel: () => Promise<void>;
  ping: (getToken: () => Promise<string | null>) => Promise<string | null>;
  fetchSystemInfo: (getToken: () => Promise<string | null>) => Promise<void>;
  fetchSlots: (getToken: () => Promise<string | null>) => Promise<void>;
  loadModel: (modelPath: string, slotId: number) => Promise<void>;
  setKernelPath: (path: string) => void;
  clearError: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const authHeader = (token: string | null): Record<string, string> =>
  token ? { Authorization: `Bearer ${token}` } : {};

// ---------------------------------------------------------------------------
// IndexedDB storage engine for Zustand persist
// ---------------------------------------------------------------------------

function createIndexedDBStorage() {
  // Only available in browser
  if (typeof window === 'undefined' || typeof indexedDB === 'undefined') {
    return undefined;
  }

  const DB_NAME = 'vos3-kernel-store';
  const STORE_NAME = 'state';

  function openDB(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(STORE_NAME);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  return createJSONStorage(() => ({
    getItem: async (name: string): Promise<string | null> => {
      try {
        const db = await openDB();
        return new Promise((resolve, reject) => {
          const tx = db.transaction(STORE_NAME, 'readonly');
          const store = tx.objectStore(STORE_NAME);
          const req = store.get(name);
          req.onsuccess = () => resolve(req.result ?? null);
          req.onerror = () => reject(req.error);
        });
      } catch {
        return null;
      }
    },
    setItem: async (name: string, value: string): Promise<void> => {
      try {
        const db = await openDB();
        return new Promise((resolve, reject) => {
          const tx = db.transaction(STORE_NAME, 'readwrite');
          const store = tx.objectStore(STORE_NAME);
          const req = store.put(value, name);
          req.onsuccess = () => resolve();
          req.onerror = () => reject(req.error);
        });
      } catch {
        // Best-effort persistence
      }
    },
    removeItem: async (name: string): Promise<void> => {
      try {
        const db = await openDB();
        return new Promise((resolve, reject) => {
          const tx = db.transaction(STORE_NAME, 'readwrite');
          const store = tx.objectStore(STORE_NAME);
          const req = store.delete(name);
          req.onsuccess = () => resolve();
          req.onerror = () => reject(req.error);
        });
      } catch {
        // Best-effort
      }
    },
  }));
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useKernelStore = create<KernelState>()(
  persist(
    immer((set, get) => ({
      // ---- initial state ----
      connected: false,
      qemuAlive: false,
      vbusConnected: false,
      vbusHmac: false,
      warpOpen: false,
      sysinfo: {},
      slots: null,
      loading: false,
      error: null,
      kernelPath: 'kernel/build/vos3.elf',
      modelLoadProgress: null,
      isDesktop: false,

      // ---- actions ----

      initialize: () => {
        set((s) => {
          s.isDesktop = isTauri();
        });
      },

      fetchStatus: async (getToken) => {
        set((s) => {
          s.loading = true;
          s.error = null;
        });

        try {
          if (get().isDesktop) {
            // Desktop mode: Tauri IPC
            const status = await tauriKernelStatus();
            set((s) => {
              s.qemuAlive = status.qemu_alive;
              s.vbusConnected = status.vbus_connected;
              s.vbusHmac = status.vbus_hmac;
              s.warpOpen = status.warp_open;
              s.connected = status.vbus_connected;
            });
          } else {
            // Browser mode: HTTP API
            const token = await getToken();
            const res = await fetch('/api/kernel/status', {
              headers: authHeader(token),
            });
            if (!res.ok) throw new Error(`Status ${res.status}`);
            const data = await res.json();
            set((s) => {
              s.connected = data.connected ?? false;
              s.sysinfo = data.sysinfo ?? {};
              if (data.qemu_alive !== undefined) s.qemuAlive = data.qemu_alive;
              if (data.vbus_connected !== undefined) s.vbusConnected = data.vbus_connected;
              if (data.vbus_hmac !== undefined) s.vbusHmac = data.vbus_hmac;
              if (data.warp_open !== undefined) s.warpOpen = data.warp_open;
            });
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : 'Unknown error';
          set((s) => {
            s.error = msg;
            s.connected = false;
          });
        } finally {
          set((s) => {
            s.loading = false;
          });
        }
      },

      startKernel: async (kernelPath, qemuPath) => {
        if (!get().isDesktop) return;
        set((s) => {
          s.loading = true;
          s.error = null;
        });
        try {
          await tauriStartKernel(kernelPath, qemuPath);
          set((s) => {
            s.qemuAlive = true;
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          set((s) => {
            s.error = msg;
          });
        } finally {
          set((s) => {
            s.loading = false;
          });
        }
      },

      stopKernel: async () => {
        if (!get().isDesktop) return;
        set((s) => {
          s.loading = true;
          s.error = null;
        });
        try {
          await tauriStopKernel();
          set((s) => {
            s.qemuAlive = false;
            s.vbusConnected = false;
            s.vbusHmac = false;
            s.warpOpen = false;
            s.connected = false;
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          set((s) => {
            s.error = msg;
          });
        } finally {
          set((s) => {
            s.loading = false;
          });
        }
      },

      ping: async (getToken) => {
        try {
          if (get().isDesktop) {
            return await tauriVbusPing();
          }
          const token = await getToken();
          const res = await fetch('/api/kernel/ping', {
            headers: authHeader(token),
          });
          if (!res.ok) return null;
          const data = await res.json();
          return data.response ?? null;
        } catch {
          return null;
        }
      },

      fetchSystemInfo: async (getToken) => {
        try {
          if (get().isDesktop) {
            const raw = await tauriSystemInfo();
            // Parse VBus sysinfo response (key=value pairs)
            const info: SysInfo = { raw };
            const uptimeMatch = raw.match(/uptime[=:]?\s*(\d+)/i);
            if (uptimeMatch) info.uptime_ms = parseInt(uptimeMatch[1], 10);
            const memTotalMatch = raw.match(/mem_total[=:]?\s*(\d+)/i);
            if (memTotalMatch) info.mem_total_kb = parseInt(memTotalMatch[1], 10);
            const memFreeMatch = raw.match(/mem_free[=:]?\s*(\d+)/i);
            if (memFreeMatch) info.mem_free_kb = parseInt(memFreeMatch[1], 10);
            const tasksMatch = raw.match(/tasks[=:]?\s*(\d+)/i);
            if (tasksMatch) info.tasks = parseInt(tasksMatch[1], 10);
            set((s) => {
              s.sysinfo = info;
            });
          } else {
            const token = await getToken();
            const res = await fetch('/api/kernel/status', {
              headers: authHeader(token),
            });
            if (!res.ok) return;
            const data = await res.json();
            set((s) => {
              s.sysinfo = data.sysinfo ?? {};
            });
          }
        } catch {
          // Best-effort
        }
      },

      fetchSlots: async (getToken) => {
        try {
          if (get().isDesktop) {
            const raw = await tauriListSlots();
            set((s) => {
              s.slots = raw;
            });
          } else {
            const token = await getToken();
            const res = await fetch('/api/kernel/slots', {
              headers: authHeader(token),
            });
            if (!res.ok) return;
            const data = await res.json();
            set((s) => {
              s.slots = JSON.stringify(data);
            });
          }
        } catch {
          // Best-effort
        }
      },

      loadModel: async (modelPath, slotId) => {
        if (!get().isDesktop) return;
        set((s) => {
          s.loading = true;
          s.error = null;
          s.modelLoadProgress = 0;
        });
        try {
          await tauriLoadModel(modelPath, slotId, (event: ModelLoadEvent) => {
            if (event.event === 'progress') {
              const pct = Math.round(
                (event.data.bytes_written / event.data.total_bytes) * 100,
              );
              set((s) => {
                s.modelLoadProgress = pct;
              });
            } else if (event.event === 'finished') {
              set((s) => {
                s.modelLoadProgress = 100;
              });
            }
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          set((s) => {
            s.error = msg;
          });
        } finally {
          set((s) => {
            s.loading = false;
            s.modelLoadProgress = null;
          });
        }
      },

      setKernelPath: (path) => {
        set((s) => {
          s.kernelPath = path;
        });
      },

      clearError: () => {
        set((s) => {
          s.error = null;
        });
      },
    })),
    {
      name: 'vos3-kernel-store',
      storage: createIndexedDBStorage(),
      partialize: (state) => ({
        kernelPath: state.kernelPath,
      }),
    },
  ),
);
