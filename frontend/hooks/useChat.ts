import { useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

/** Generate a UUID with fallback for environments lacking crypto.randomUUID */
function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Fallback: RFC 4122 v4 UUID from crypto.getRandomValues
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
}

interface Model {
  id: string;
  provider: string;
  cost_per_1k_input: number;
  cost_per_1k_output: number;
  // W7.2 — Hardware-aware quantization metadata. Populated by the
  // /api/models/catalog endpoint for open-weight entries; left
  // undefined for proprietary cloud models served by /api/chat/models.
  family?: string;
  display_name?: string;
  param_count?: string;
  quantization?: string;
  tier?: 'workstation' | 'medium' | 'enterprise';
  status?:
    | 'local_ready'
    | 'available_lan'
    | 'available_https'
    | 'cloud_fallback_only';
  host_can_run?: boolean;
  kernel_fit?: boolean;
  license?: string;
  min_vram_gb?: number;
  min_ram_gb?: number;
}

// W7.2 — Hardware snapshot returned alongside the catalog. Useful for
// the chat UI to render a "running on M4 Pro · medium tier" footer.
export interface HostCapability {
  platform: string;
  cpu_arch: string;
  total_ram_gb: number;
  gpu_name: string | null;
  vram_gb: number;
  unified_memory: boolean;
  tier: 'workstation' | 'medium' | 'enterprise';
  probe_method: string;
}

const DEFAULT_MODELS: Model[] = [
  // ============= OpenAI Models =============
  // GPT-5 Family
  { id: 'gpt-5', provider: 'openai', cost_per_1k_input: 0.005, cost_per_1k_output: 0.02 },
  { id: 'gpt-5.1', provider: 'openai', cost_per_1k_input: 0.005, cost_per_1k_output: 0.02 },
  { id: 'gpt-5.2', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  { id: 'gpt-5-mini', provider: 'openai', cost_per_1k_input: 0.001, cost_per_1k_output: 0.004 },
  // GPT-4.5
  { id: 'gpt-4.5-preview', provider: 'openai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.012 },
  // GPT-4o Family
  { id: 'gpt-4o', provider: 'openai', cost_per_1k_input: 0.0025, cost_per_1k_output: 0.01 },
  { id: 'gpt-4o-mini', provider: 'openai', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  // GPT-4 Family
  { id: 'gpt-4', provider: 'openai', cost_per_1k_input: 0.03, cost_per_1k_output: 0.06 },
  { id: 'gpt-4-turbo', provider: 'openai', cost_per_1k_input: 0.01, cost_per_1k_output: 0.03 },
  // o-series (Reasoning)
  { id: 'o1', provider: 'openai', cost_per_1k_input: 0.015, cost_per_1k_output: 0.06 },
  { id: 'o1-mini', provider: 'openai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.012 },
  { id: 'o1-pro', provider: 'openai', cost_per_1k_input: 0.06, cost_per_1k_output: 0.24 },
  { id: 'o3', provider: 'openai', cost_per_1k_input: 0.02, cost_per_1k_output: 0.08 },
  { id: 'o3-mini', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  { id: 'o4-mini', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  // Codex & Specialized
  { id: 'codex', provider: 'openai', cost_per_1k_input: 0.01, cost_per_1k_output: 0.03 },
  { id: 'gpt-oss', provider: 'openai', cost_per_1k_input: 0.001, cost_per_1k_output: 0.002 },
  // Multimodal
  { id: 'dall-e-3', provider: 'openai', cost_per_1k_input: 0.04, cost_per_1k_output: 0.08 },
  { id: 'whisper', provider: 'openai', cost_per_1k_input: 0.006, cost_per_1k_output: 0 },
  { id: 'tts-1', provider: 'openai', cost_per_1k_input: 0.015, cost_per_1k_output: 0 },
  { id: 'tts-1-hd', provider: 'openai', cost_per_1k_input: 0.03, cost_per_1k_output: 0 },
  // ============= Anthropic Models =============
  // Claude 4.5 Family
  { id: 'claude-opus-4.6', provider: 'anthropic', cost_per_1k_input: 0.018, cost_per_1k_output: 0.09 },
  { id: 'claude-opus-4.5', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-4.5', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-4.5', provider: 'anthropic', cost_per_1k_input: 0.001, cost_per_1k_output: 0.005 },
  // Claude 4 Family
  { id: 'claude-opus-4', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-4', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  // Claude 3.7 Family
  { id: 'claude-sonnet-3.7', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  // Claude 3.5 Family
  { id: 'claude-sonnet-3.5-v2', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-sonnet-3.5', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-3.5', provider: 'anthropic', cost_per_1k_input: 0.0008, cost_per_1k_output: 0.004 },
  // Claude 3 Family
  { id: 'claude-opus-3', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-3', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-3', provider: 'anthropic', cost_per_1k_input: 0.00025, cost_per_1k_output: 0.00125 },
  // ============= Google Models =============
  // Gemini 3
  { id: 'gemini-3-pro', provider: 'google', cost_per_1k_input: 0.002, cost_per_1k_output: 0.008 },
  { id: 'gemini-3-flash', provider: 'google', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0008 },
  { id: 'gemini-3-deep-think', provider: 'google', cost_per_1k_input: 0.005, cost_per_1k_output: 0.02 },
  // Gemini 2.5
  { id: 'gemini-2.5-pro', provider: 'google', cost_per_1k_input: 0.00175, cost_per_1k_output: 0.007 },
  { id: 'gemini-2.5-flash', provider: 'google', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'gemini-2.5-flash-lite', provider: 'google', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  { id: 'gemini-2.5-flash-native-audio', provider: 'google', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0008 },
  // Gemini 2.0
  { id: 'gemini-2.0-pro', provider: 'google', cost_per_1k_input: 0.0015, cost_per_1k_output: 0.006 },
  { id: 'gemini-2.0-flash', provider: 'google', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  { id: 'gemini-2.0-flash-thinking', provider: 'google', cost_per_1k_input: 0.0003, cost_per_1k_output: 0.0012 },
  // Gemini 1.5
  { id: 'gemini-1.5-pro', provider: 'google', cost_per_1k_input: 0.00125, cost_per_1k_output: 0.005 },
  { id: 'gemini-1.5-flash', provider: 'google', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  // Gemini 1.0
  { id: 'gemini-1.0-ultra', provider: 'google', cost_per_1k_input: 0.002, cost_per_1k_output: 0.008 },
  { id: 'gemini-1.0-pro', provider: 'google', cost_per_1k_input: 0.0005, cost_per_1k_output: 0.002 },
  { id: 'gemini-1.0-nano', provider: 'google', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  // Gemma (open-weight)
  { id: 'gemma-3', provider: 'google', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'gemma-2-27b', provider: 'google', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'gemma-2-9b', provider: 'google', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'gemma-2-2b', provider: 'google', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  // Gemini Diffusion / Image
  { id: 'gemini-diffusion-experimental', provider: 'google', cost_per_1k_input: 0.001, cost_per_1k_output: 0.004 },
  { id: 'nano-banana', provider: 'google', cost_per_1k_input: 0.0005, cost_per_1k_output: 0.002 },
  { id: 'imagen-3', provider: 'google', cost_per_1k_input: 0.04, cost_per_1k_output: 0.08 },
  // ============= xAI Grok Models =============
  { id: 'grok-4.1', provider: 'xai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'grok-4', provider: 'xai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'grok-3', provider: 'xai', cost_per_1k_input: 0.002, cost_per_1k_output: 0.01 },
  { id: 'grok-3-mini', provider: 'xai', cost_per_1k_input: 0.0005, cost_per_1k_output: 0.002 },
  { id: 'grok-code-fast-1', provider: 'xai', cost_per_1k_input: 0.002, cost_per_1k_output: 0.01 },
  { id: 'grok-2', provider: 'xai', cost_per_1k_input: 0.002, cost_per_1k_output: 0.01 },
  { id: 'grok-2-mini', provider: 'xai', cost_per_1k_input: 0.0005, cost_per_1k_output: 0.002 },
  // ============= Alibaba Qwen Models (Open-weight) =============
  { id: 'qwen3', provider: 'alibaba', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0006 },
  { id: 'qwen3-thinking', provider: 'alibaba', cost_per_1k_input: 0.0003, cost_per_1k_output: 0.0012 },
  { id: 'qwen2.5', provider: 'alibaba', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'qwen2.5-coder', provider: 'alibaba', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'qwen2.5-math', provider: 'alibaba', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'qwen2.5-vl', provider: 'alibaba', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0008 },
  { id: 'qwen2', provider: 'alibaba', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  { id: 'qwq', provider: 'alibaba', cost_per_1k_input: 0.0003, cost_per_1k_output: 0.0012 },
  // ============= DeepSeek Models (Open-weight) =============
  // DeepSeek-V3
  { id: 'deepseek-v3', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  { id: 'deepseek-v3.1', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  { id: 'deepseek-v3.2', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  { id: 'deepseek-v3.2-exp', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  // DeepSeek-R1 (Reasoning)
  { id: 'deepseek-r1', provider: 'deepseek', cost_per_1k_input: 0.00055, cost_per_1k_output: 0.00219 },
  { id: 'deepseek-r1-0528', provider: 'deepseek', cost_per_1k_input: 0.00055, cost_per_1k_output: 0.00219 },
  { id: 'deepseek-r1-lite', provider: 'deepseek', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0008 },
  { id: 'deepseek-r1-zero', provider: 'deepseek', cost_per_1k_input: 0.00055, cost_per_1k_output: 0.00219 },
  // DeepSeek-V2
  { id: 'deepseek-v2', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  { id: 'deepseek-v2.5', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  // DeepSeek-Coder
  { id: 'deepseek-coder-v2', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  // ============= Meta Llama Models (Local/API) =============
  { id: 'llama-4-maverick', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'llama-4-scout', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'llama-3.3-70b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'llama-3.2-90b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  // ============= Other Open Source (Local) =============
  { id: 'qwen-2.5-72b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'mixtral-8x22b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'codestral-22b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
];

export function useChat() {
  // Phase v17 (F-H4): Clerk auth token for all API requests
  const { getToken } = useAuth();

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [models, setModels] = useState<Model[]>(DEFAULT_MODELS);
  const [selectedModel, setSelectedModel] = useState<string>('gpt-4o-mini');
  const [error, setError] = useState<string | null>(null);
  // W7.2 — Host hardware snapshot (Apple M4 Pro · 48GB · medium tier).
  const [hostCapability, setHostCapability] = useState<HostCapability | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const summarizedRef = useRef(false);
  const [vosEnabled, setVosEnabled] = useState(true);

  const loadModels = useCallback(async () => {
    // Two-source merge:
    //   /api/chat/models       — proprietary providers (OpenAI/Anthropic/…)
    //   /api/models/catalog    — W7.2 open-weight curated catalog with
    //                            hardware-aware status badges
    try {
      const [chatRes, catalogRes] = await Promise.allSettled([
        apiFetch(getToken, '/api/chat/models'),
        apiFetch(getToken, '/api/models/catalog'),
      ]);

      let merged: Model[] = [...DEFAULT_MODELS];

      // Proprietary cloud models.
      if (chatRes.status === 'fulfilled' && chatRes.value.ok) {
        const data = await chatRes.value.json();
        if (Array.isArray(data.models)) merged = data.models;
      }

      // Open-weight curated catalog (badges + hardware-aware status).
      if (catalogRes.status === 'fulfilled' && catalogRes.value.ok) {
        const data = await catalogRes.value.json();
        if (data.host) setHostCapability(data.host as HostCapability);
        if (Array.isArray(data.models)) {
          const curated: Model[] = data.models.map((m: any) => ({
            id: m.id,
            provider: m.family ?? 'local',
            cost_per_1k_input: 0,
            cost_per_1k_output: 0,
            family: m.family,
            display_name: m.display_name,
            param_count: m.param_count,
            quantization: m.quantization,
            tier: m.tier,
            status: m.status,
            host_can_run: m.host_can_run,
            kernel_fit: m.kernel_fit,
            license: m.license,
            min_vram_gb: m.min_vram_gb,
            min_ram_gb: m.min_ram_gb,
          }));
          // De-duplicate by id — catalog wins for entries it owns.
          const byId = new Map<string, Model>();
          for (const m of merged) byId.set(m.id, m);
          for (const m of curated) byId.set(m.id, m);
          merged = Array.from(byId.values());
        }
      }

      setModels(merged);
    } catch (err) {
      console.error('Failed to load models:', err);
    }
  }, [getToken]);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const sendMessage = useCallback(
    async (content: string) => {
      // Initialize session ID on first message
      if (!sessionIdRef.current) {
        sessionIdRef.current = `chat-${Date.now()}-${generateUUID().slice(0, 8)}`;
        summarizedRef.current = false;
      }

      const userMessage: Message = {
        id: generateUUID(),
        role: 'user',
        content,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMessage]);
      setIsLoading(true);
      setError(null);

      try {
        // VOS interception: check if message has system-level intent
        if (vosEnabled) {
          try {
            const contextMsgs = [...messages, userMessage]
              .slice(-4)
              .map((m) => ({ role: m.role, content: m.content }));

            const vosResponse = await apiFetch(getToken, '/api/vos/process', {
              method: 'POST',
              body: JSON.stringify({
                message: content,
                context: contextMsgs,
                source: 'chat',
              }),
            });

            if (vosResponse.ok) {
              const vosData = await vosResponse.json();
              if (vosData.had_intent && !vosData.pass_through) {
                // VOS handled it — show summary as assistant message
                const vosMessage: Message = {
                  id: generateUUID(),
                  role: 'assistant',
                  content: vosData.summary || 'Action completed.',
                  timestamp: new Date(),
                };
                setMessages((prev) => [...prev, vosMessage]);
                setIsLoading(false);
                return;
              }
            }
          } catch {
            // VOS unavailable — continue to regular chat
          }
        }

        // Regular chat flow
        const response = await apiFetch(getToken, `/api/chat/completions`, {
          method: 'POST',
          body: JSON.stringify({
            messages: [...messages, userMessage].map((m) => ({
              role: m.role,
              content: m.content,
            })),
            model: selectedModel,
            stream: false,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const data = await response.json();

        const assistantMessage: Message = {
          id: generateUUID(),
          role: 'assistant',
          content: data?.message?.content ?? data?.choices?.[0]?.message?.content ?? 'No response received.',
          timestamp: new Date(),
        };

        setMessages((prev) => [...prev, assistantMessage]);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Failed to send message';
        setError(errorMessage);
        console.error('Chat error:', err);
      } finally {
        setIsLoading(false);
      }
    },
    [messages, selectedModel, vosEnabled, getToken]
  );

  const clearHistory = useCallback(() => {
    setMessages([]);
    setError(null);
    sessionIdRef.current = null;
    summarizedRef.current = false;
  }, []);

  const endSession = useCallback(async (currentMessages?: Message[]) => {
    const msgs = currentMessages || messages;
    if (summarizedRef.current || !sessionIdRef.current || msgs.length < 2) return;
    summarizedRef.current = true;

    const firstUserMsg = msgs.find((m) => m.role === 'user');
    const payload = JSON.stringify({
      messages: msgs.map((m) => ({ role: m.role, content: m.content })),
      session_type: 'chat',
      session_id: sessionIdRef.current,
      agent_ids: [],
      agent_names: [],
      topic_hint: firstUserMsg?.content?.slice(0, 100) || '',
    });

    // Use apiFetch with keepalive so auth + CSRF headers are included
    // (sendBeacon cannot send custom headers).
    apiFetch(getToken, '/api/chat/summarize', {
      method: 'POST',
      body: payload,
      keepalive: true,
    }).catch(() => {});
  }, [messages, getToken]);

  return {
    messages,
    isLoading,
    models,
    selectedModel,
    setSelectedModel,
    sendMessage,
    clearHistory,
    endSession,
    error,
    vosEnabled,
    setVosEnabled,
    // W7.2 — host hardware snapshot (Apple M4 Pro / NVIDIA RTX / …)
    hostCapability,
  };
}
