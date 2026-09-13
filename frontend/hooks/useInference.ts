import { useState, useCallback, useRef } from 'react';
import { isTauri, tauriSendChat, tauriListSlots } from '@/lib/tauri-bridge';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface InferenceMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  slotId: number;
  timestamp: Date;
}

export type SlotStatus = 'empty' | 'loaded' | 'streaming';

export interface SlotInfo {
  id: number;
  status: SlotStatus;
  label: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NUM_SLOTS = 8;

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function defaultSlots(): SlotInfo[] {
  return Array.from({ length: NUM_SLOTS }, (_, i) => ({
    id: i,
    status: 'empty' as SlotStatus,
    label: `Slot ${i}`,
  }));
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useInference() {
  const [messages, setMessages] = useState<InferenceMessage[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<number>(0);
  const [isStreaming, setIsStreaming] = useState(false);
  const [slotStatuses, setSlotStatuses] = useState<SlotInfo[]>(defaultSlots);
  const streamingRef = useRef(false);

  /**
   * Detect whether we are running inside the Tauri WebView shell.
   * Returns false during SSR or when accessed from a regular browser.
   */
  const isDesktop = useCallback((): boolean => {
    if (typeof window === 'undefined') return false;
    return isTauri();
  }, []);

  /**
   * Refresh slot statuses by querying the kernel via VBus LIST_SLOTS.
   * Best-effort: on failure the existing state is kept.
   */
  const refreshSlots = useCallback(async () => {
    if (!isDesktop()) return;
    try {
      const raw = await tauriListSlots();
      // The Rust side returns a human-readable string.  We attempt a simple
      // parse: look for lines like  "Slot 3: loaded" / "Slot 0: empty".
      const lines = raw.split('\n');
      setSlotStatuses((prev) => {
        const next = [...prev];
        for (const line of lines) {
          const match = line.match(/Slot\s+(\d+)\s*:\s*(\w+)/i);
          if (match) {
            const idx = parseInt(match[1], 10);
            const statusWord = match[2].toLowerCase();
            if (idx >= 0 && idx < NUM_SLOTS) {
              let status: SlotStatus = 'empty';
              if (statusWord === 'loaded' || statusWord === 'ready') status = 'loaded';
              else if (statusWord === 'streaming' || statusWord === 'busy') status = 'streaming';
              next[idx] = { ...next[idx], status };
            }
          }
        }
        return next;
      });
    } catch {
      // Kernel may not be running yet — keep existing state.
    }
  }, [isDesktop]);

  /**
   * Send a user message to the kernel for inference on the selected slot.
   *
   * @param text        The user prompt.
   * @param slotId      Target inference slot (0-7).
   * @param maxTokens   Maximum tokens to generate (default 128).
   * @param temperature Temperature * 100 (e.g. 100 = 1.0). Passed as
   *                    metadata but actual enforcement depends on the kernel.
   */
  const sendMessage = useCallback(
    async (
      text: string,
      slotId: number,
      maxTokens: number = 128,
      temperature: number = 100,
    ) => {
      if (!text.trim()) return;

      // Append user message immediately.
      const userMsg: InferenceMessage = {
        id: generateId(),
        role: 'user',
        content: text.trim(),
        slotId,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);

      // Mark streaming state.
      setIsStreaming(true);
      streamingRef.current = true;

      // Update the slot status to streaming.
      setSlotStatuses((prev) =>
        prev.map((s) => (s.id === slotId ? { ...s, status: 'streaming' as SlotStatus } : s)),
      );

      try {
        if (!isDesktop()) {
          throw new Error('Kernel inference is only available in VOS3 Desktop (Tauri) mode.');
        }

        // Call the Tauri IPC command.  The Rust backend sends the message
        // over VBus to the kernel and returns the generated text.
        const response = await tauriSendChat(text.trim(), slotId);

        const assistantMsg: InferenceMessage = {
          id: generateId(),
          role: 'assistant',
          content: response || '(empty response)',
          slotId,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, assistantMsg]);

        // Mark slot as loaded (no longer streaming).
        setSlotStatuses((prev) =>
          prev.map((s) => (s.id === slotId ? { ...s, status: 'loaded' as SlotStatus } : s)),
        );
      } catch (err) {
        const errorText =
          err instanceof Error ? err.message : 'Unknown inference error';
        const errMsg: InferenceMessage = {
          id: generateId(),
          role: 'assistant',
          content: `Error: ${errorText}`,
          slotId,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errMsg]);

        // Revert slot status back to whatever it was before (assume loaded
        // if we hit an error during inference, empty otherwise).
        setSlotStatuses((prev) =>
          prev.map((s) => {
            if (s.id !== slotId) return s;
            return { ...s, status: s.status === 'streaming' ? 'loaded' : s.status };
          }),
        );
      } finally {
        setIsStreaming(false);
        streamingRef.current = false;
      }
    },
    [isDesktop],
  );

  /**
   * Clear the message history.
   */
  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  return {
    messages,
    selectedSlot,
    setSelectedSlot,
    sendMessage,
    isStreaming,
    slotStatuses,
    isDesktop,
    refreshSlots,
    clearMessages,
  };
}
