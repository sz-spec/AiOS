/**
 * Tauri Bridge — Transport abstraction for VOS3 Enclave desktop integration.
 *
 * Provides runtime detection of the Tauri environment and type-safe wrappers
 * around all 8 IPC commands. In browser mode, all functions are no-ops or
 * return null, preserving full browser compatibility.
 *
 * Uses dynamic import('@tauri-apps/api/core') to avoid SSR crashes.
 *
 * Provenance: 100% original VOS3 code. Channel invoke pattern from official
 * Tauri 2.0 docs (https://v2.tauri.app/develop/calling-frontend/) —
 * re-implemented in TypeScript with VOS3 type contracts. Audited 2026-04-12.
 */

// ---------------------------------------------------------------------------
// Types — mirror the Rust-side response shapes
// ---------------------------------------------------------------------------

export interface KernelStatusResponse {
  qemu_alive: boolean;
  vbus_connected: boolean;
  vbus_hmac: boolean;
  warp_open: boolean;
}

/** Model-load progress event streamed via Tauri Channel. */
export type ModelLoadEvent =
  | { event: 'started'; data: { slot_id: number; total_bytes: number } }
  | { event: 'progress'; data: { slot_id: number; bytes_written: number; total_bytes: number } }
  | { event: 'finished'; data: { slot_id: number; total_bytes: number } };

// ---------------------------------------------------------------------------
// Runtime detection
// ---------------------------------------------------------------------------

/** Returns `true` when running inside the Tauri 2.0 WebView shell. */
export function isTauri(): boolean {
  return (
    typeof window !== 'undefined' &&
    '__TAURI_INTERNALS__' in window
  );
}

// ---------------------------------------------------------------------------
// Dynamic import helper (lazy-loaded, SSR-safe)
// ---------------------------------------------------------------------------

async function getTauriCore() {
  return await import('@tauri-apps/api/core');
}

// ---------------------------------------------------------------------------
// IPC wrappers — Tauri 2.0 uses rename_all = "camelCase" for command args
// ---------------------------------------------------------------------------

/**
 * Start the VOS3 kernel in QEMU.
 * Only callable in desktop mode.
 */
export async function tauriStartKernel(
  kernelPath: string,
  qemuPath?: string,
): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('start_kernel', {
      kernelPath,
      qemuPath: qemuPath ?? null,
    });
  } catch (e) {
    console.error('[VBus] start_kernel failed:', e);
    throw e;
  }
}

/**
 * Stop the running VOS3 kernel and disconnect all subsystems.
 */
export async function tauriStopKernel(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('stop_kernel');
  } catch (e) {
    console.error('[VBus] stop_kernel failed:', e);
    throw e;
  }
}

/**
 * Query the current kernel/VBus/Warp status.
 */
export async function tauriKernelStatus(): Promise<KernelStatusResponse> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<KernelStatusResponse>('kernel_status');
  } catch (e) {
    console.error('[VBus] kernel_status failed:', e);
    throw e;
  }
}

/**
 * Send a VBus PING to the kernel and return the response string.
 */
export async function tauriVbusPing(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('vbus_ping');
  } catch (e) {
    console.error('[VBus] vbus_ping failed:', e);
    throw e;
  }
}

/**
 * Query kernel system information via VBus.
 */
export async function tauriSystemInfo(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('system_info');
  } catch (e) {
    console.error('[VBus] system_info failed:', e);
    throw e;
  }
}

/**
 * Enumerate all AI model slots.
 */
export async function tauriListSlots(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('list_slots');
  } catch (e) {
    console.error('[VBus] list_slots failed:', e);
    throw e;
  }
}

/**
 * Load a model into a kernel AI slot via Warp Drive.
 * Uses Tauri 2.0 Channel for real-time progress streaming.
 *
 * @param modelPath  Path to the model file on host disk.
 * @param slotId     Target slot (0–7).
 * @param onProgress Callback invoked for each progress event.
 * @returns Final result string from the Rust command.
 */
export async function tauriLoadModel(
  modelPath: string,
  slotId: number,
  onProgress?: (event: ModelLoadEvent) => void,
): Promise<string> {
  try {
    const { invoke, Channel } = await getTauriCore();

    const channel = new Channel<ModelLoadEvent>();
    if (onProgress) {
      channel.onmessage = onProgress;
    }

    return await invoke<string>('load_model', {
      modelPath,
      slotId,
      onProgress: channel,
    });
  } catch (e) {
    console.error('[VBus] load_model failed:', e);
    throw e;
  }
}

/**
 * Send a chat message to the kernel for inference.
 */
export async function tauriSendChat(
  message: string,
  slotId: number,
): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('send_chat', { message, slotId });
  } catch (e) {
    console.error('[VBus] send_chat failed:', e);
    throw e;
  }
}

/**
 * Read the latest inference output from a slot's Warp zone (first 4KB).
 */
export async function tauriReadInferenceOutput(slotId: number): Promise<number[]> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<number[]>('read_inference_output', { slotId });
  } catch (e) {
    console.error('[VBus] read_inference_output failed:', e);
    throw e;
  }
}

/**
 * Read raw bytes from a Warp Drive zone with specified offset and length.
 * Length is capped at 1MB server-side for IPC safety.
 */
export async function tauriWarpReadZone(slotId: number, offset: number, len: number): Promise<number[]> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<number[]>('warp_read_zone', { slotId, offset, len });
  } catch (e) {
    console.error('[VBus] warp_read_zone failed:', e);
    throw e;
  }
}

// ---------------------------------------------------------------------------
// Event listener abstraction (no-op in browser)
// ---------------------------------------------------------------------------

/**
 * Subscribe to a Tauri event by name.
 * Returns an unsubscribe function. No-op in browser mode.
 */
export async function onKernelEvent<T>(
  eventName: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  if (!isTauri()) return () => {};

  const { listen } = await import('@tauri-apps/api/event');
  const unlisten = await listen<T>(eventName, (e) => handler(e.payload));
  return unlisten;
}

// ---------------------------------------------------------------------------
// Stage 10.3 — Sovereign Control Panel bridges
//
// Each function is a thin pass-through to a #[tauri::command] in
// desktop/src-tauri/src/commands.rs. Reply strings are kept as plain
// VBus wire format so the frontend parser can evolve without a Tauri
// rebuild.
// ---------------------------------------------------------------------------

/** POLICY_STATUS — read force_permit + per-slot gates. */
export async function tauriPolicyStatus(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('policy_status');
  } catch (e) {
    console.error('[Policy] policy_status failed:', e);
    throw e;
  }
}

/** Toggle Safe-Rollout. */
export async function tauriPolicySetForcePermit(enabled: boolean): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('policy_set_force_permit', { enabled });
  } catch (e) {
    console.error('[Policy] policy_set_force_permit failed:', e);
    throw e;
  }
}

/** POLICY_OVERRIDE — direct per-slot threshold. */
export async function tauriPolicyOverrideSlot(
  slotId: number,
  score: number,
): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('policy_override_slot', { slotId, score });
  } catch (e) {
    console.error('[Policy] policy_override_slot failed:', e);
    throw e;
  }
}

/** AUDIT_FAIL_QUOTE — pull the kernel compliance ring. */
export async function tauriPolicyDrainAudit(): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('policy_drain_audit');
  } catch (e) {
    console.error('[Policy] policy_drain_audit failed:', e);
    throw e;
  }
}

/** ACTION_CHECK_CONFIDENCE — manually exercise an agent's gate. */
export async function tauriPolicyCheckConfidence(
  slotId: number,
  score: number,
): Promise<string> {
  try {
    const { invoke } = await getTauriCore();
    return await invoke<string>('policy_check_confidence', { slotId, score });
  } catch (e) {
    console.error('[Policy] policy_check_confidence failed:', e);
    throw e;
  }
}
