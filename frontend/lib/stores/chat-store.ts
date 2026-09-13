import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useChat.ts)
// ---------------------------------------------------------------------------

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
}

export interface Model {
  id: string;
  provider: string;
  cost_per_1k_input: number;
  cost_per_1k_output: number;
}

const DEFAULT_MODELS: Model[] = [
  // OpenAI
  { id: 'gpt-5', provider: 'openai', cost_per_1k_input: 0.005, cost_per_1k_output: 0.02 },
  { id: 'gpt-5.1', provider: 'openai', cost_per_1k_input: 0.005, cost_per_1k_output: 0.02 },
  { id: 'gpt-5.2', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  { id: 'gpt-5-mini', provider: 'openai', cost_per_1k_input: 0.001, cost_per_1k_output: 0.004 },
  { id: 'gpt-4.5-preview', provider: 'openai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.012 },
  { id: 'gpt-4o', provider: 'openai', cost_per_1k_input: 0.0025, cost_per_1k_output: 0.01 },
  { id: 'gpt-4o-mini', provider: 'openai', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'gpt-4', provider: 'openai', cost_per_1k_input: 0.03, cost_per_1k_output: 0.06 },
  { id: 'gpt-4-turbo', provider: 'openai', cost_per_1k_input: 0.01, cost_per_1k_output: 0.03 },
  { id: 'o1', provider: 'openai', cost_per_1k_input: 0.015, cost_per_1k_output: 0.06 },
  { id: 'o1-mini', provider: 'openai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.012 },
  { id: 'o1-pro', provider: 'openai', cost_per_1k_input: 0.06, cost_per_1k_output: 0.24 },
  { id: 'o3', provider: 'openai', cost_per_1k_input: 0.02, cost_per_1k_output: 0.08 },
  { id: 'o3-mini', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  { id: 'o4-mini', provider: 'openai', cost_per_1k_input: 0.004, cost_per_1k_output: 0.016 },
  { id: 'codex', provider: 'openai', cost_per_1k_input: 0.01, cost_per_1k_output: 0.03 },
  // Anthropic
  { id: 'claude-opus-4.6', provider: 'anthropic', cost_per_1k_input: 0.018, cost_per_1k_output: 0.09 },
  { id: 'claude-opus-4.5', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-4.5', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-4.5', provider: 'anthropic', cost_per_1k_input: 0.001, cost_per_1k_output: 0.005 },
  { id: 'claude-opus-4', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-4', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-sonnet-3.7', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-sonnet-3.5-v2', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-sonnet-3.5', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-3.5', provider: 'anthropic', cost_per_1k_input: 0.0008, cost_per_1k_output: 0.004 },
  { id: 'claude-opus-3', provider: 'anthropic', cost_per_1k_input: 0.015, cost_per_1k_output: 0.075 },
  { id: 'claude-sonnet-3', provider: 'anthropic', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'claude-haiku-3', provider: 'anthropic', cost_per_1k_input: 0.00025, cost_per_1k_output: 0.00125 },
  // Google
  { id: 'gemini-3-pro', provider: 'google', cost_per_1k_input: 0.002, cost_per_1k_output: 0.008 },
  { id: 'gemini-3-flash', provider: 'google', cost_per_1k_input: 0.0002, cost_per_1k_output: 0.0008 },
  { id: 'gemini-2.5-pro', provider: 'google', cost_per_1k_input: 0.00175, cost_per_1k_output: 0.007 },
  { id: 'gemini-2.5-flash', provider: 'google', cost_per_1k_input: 0.00015, cost_per_1k_output: 0.0006 },
  { id: 'gemini-2.0-pro', provider: 'google', cost_per_1k_input: 0.0015, cost_per_1k_output: 0.006 },
  { id: 'gemini-2.0-flash', provider: 'google', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  { id: 'gemini-1.5-pro', provider: 'google', cost_per_1k_input: 0.00125, cost_per_1k_output: 0.005 },
  { id: 'gemini-1.5-flash', provider: 'google', cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0004 },
  // xAI
  { id: 'grok-4.1', provider: 'xai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'grok-4', provider: 'xai', cost_per_1k_input: 0.003, cost_per_1k_output: 0.015 },
  { id: 'grok-3', provider: 'xai', cost_per_1k_input: 0.002, cost_per_1k_output: 0.01 },
  { id: 'grok-3-mini', provider: 'xai', cost_per_1k_input: 0.0005, cost_per_1k_output: 0.002 },
  // DeepSeek
  { id: 'deepseek-v3', provider: 'deepseek', cost_per_1k_input: 0.00014, cost_per_1k_output: 0.00028 },
  { id: 'deepseek-r1', provider: 'deepseek', cost_per_1k_input: 0.00055, cost_per_1k_output: 0.00219 },
  // Local
  { id: 'llama-4-maverick', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'llama-4-scout', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
  { id: 'llama-3.3-70b', provider: 'local', cost_per_1k_input: 0, cost_per_1k_output: 0 },
];

// ---------------------------------------------------------------------------
// SendMessage options
// ---------------------------------------------------------------------------

export interface SendMessageOptions {
  /** Override model for this message only */
  model?: string;
}

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

interface ChatState {
  // ---- data ----
  messages: ChatMessage[];
  isStreaming: boolean;
  currentModel: string | null;
  models: Model[];
  error: string | null;
  sessionId: string | null;
  summarized: boolean;
  vosEnabled: boolean;

  // ---- actions ----
  sendMessage: (
    content: string,
    getToken: () => Promise<string | null>,
    options?: SendMessageOptions,
  ) => Promise<void>;
  clearMessages: () => void;
  setModel: (model: string) => void;
  setVosEnabled: (enabled: boolean) => void;
  loadModels: (getToken: () => Promise<string | null>) => Promise<void>;
  endSession: (getToken: () => Promise<string | null>) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

const authHeader = (token: string | null): Record<string, string> =>
  token ? { Authorization: `Bearer ${token}` } : {};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useChatStateStore = create<ChatState>()(
  immer((set, get) => ({
    // ---- initial state ----
    messages: [],
    isStreaming: false,
    currentModel: 'gpt-4o-mini',
    models: DEFAULT_MODELS,
    error: null,
    sessionId: null,
    summarized: false,
    vosEnabled: true,

    // ---- actions ----

    loadModels: async (getToken) => {
      try {
        const token = await getToken();
        const response = await fetch('/api/chat/models', {
          headers: authHeader(token),
        });
        if (response.ok) {
          const data = await response.json();
          set((state) => {
            state.models = data.models;
          });
        }
      } catch (err) {
        console.error('Failed to load models:', err);
      }
    },

    sendMessage: async (content, getToken, options?) => {
      const { messages, currentModel, vosEnabled } = get();

      // Initialize session ID on first message
      if (!get().sessionId) {
        set((state) => {
          state.sessionId = `chat-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
          state.summarized = false;
        });
      }

      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content,
        timestamp: new Date(),
      };

      set((state) => {
        state.messages.push(userMessage);
        state.isStreaming = true;
        state.error = null;
      });

      const modelToUse = options?.model ?? currentModel;

      try {
        const token = await getToken();
        const hdrs = authHeader(token);

        // VOS interception: check if message has system-level intent
        if (vosEnabled) {
          try {
            const contextMsgs = [...messages, userMessage]
              .slice(-4)
              .map((m) => ({ role: m.role, content: m.content }));

            const vosResponse = await fetch('/api/vos/process', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...hdrs },
              body: JSON.stringify({
                message: content,
                context: contextMsgs,
                source: 'chat',
              }),
            });

            if (vosResponse.ok) {
              const vosData = await vosResponse.json();
              if (vosData.had_intent && !vosData.pass_through) {
                const vosMessage: ChatMessage = {
                  id: crypto.randomUUID(),
                  role: 'assistant',
                  content: vosData.summary || 'Action completed.',
                  timestamp: new Date(),
                };
                set((state) => {
                  state.messages.push(vosMessage);
                  state.isStreaming = false;
                });
                return;
              }
            }
          } catch {
            // VOS unavailable -- continue to regular chat
          }
        }

        // Regular chat flow
        const allMessages = get().messages;
        const response = await fetch('/api/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...hdrs },
          body: JSON.stringify({
            messages: allMessages.map((m) => ({
              role: m.role,
              content: m.content,
            })),
            model: modelToUse,
            stream: false,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const data = await response.json();

        const assistantMessage: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: data?.message?.content ?? data?.choices?.[0]?.message?.content ?? 'No response received.',
          timestamp: new Date(),
        };

        set((state) => {
          state.messages.push(assistantMessage);
        });
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to send message';
        set((state) => {
          state.error = errorMessage;
        });
        console.error('Chat error:', err);
      } finally {
        set((state) => {
          state.isStreaming = false;
        });
      }
    },

    clearMessages: () => {
      set((state) => {
        state.messages = [];
        state.error = null;
        state.sessionId = null;
        state.summarized = false;
      });
    },

    setModel: (model) => {
      set((state) => {
        state.currentModel = model;
      });
    },

    setVosEnabled: (enabled) => {
      set((state) => {
        state.vosEnabled = enabled;
      });
    },

    endSession: async (getToken) => {
      const { summarized, sessionId, messages } = get();
      if (summarized || !sessionId || messages.length < 2) return;

      set((state) => {
        state.summarized = true;
      });

      const firstUserMsg = messages.find((m) => m.role === 'user');
      const payload = JSON.stringify({
        messages: messages.map((m) => ({ role: m.role, content: m.content })),
        session_type: 'chat',
        session_id: sessionId,
        agent_ids: [],
        agent_names: [],
        topic_hint: firstUserMsg?.content?.slice(0, 100) || '',
      });

      const token = await getToken();
      const hdrs = authHeader(token);

      // Use fetch with keepalive so auth headers are included (sendBeacon cannot send them).
      fetch('/api/chat/summarize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...hdrs },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    },
  })),
);
