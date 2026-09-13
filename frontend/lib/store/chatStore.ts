import { create } from 'zustand';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  isStreaming?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  isLoading: boolean;
  projectId: string | null;

  setProjectId: (id: string | null) => void;
  addMessage: (message: ChatMessage) => void;
  updateLastAssistant: (content: string) => void;
  setIsLoading: (loading: boolean) => void;
  clearMessages: () => void;
}

let msgCounter = 0;

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isLoading: false,
  projectId: null,

  setProjectId: (id) => set({ projectId: id }),
  addMessage: (message) => set((s) => ({
    messages: [...s.messages, { ...message, id: message.id || `msg-${++msgCounter}` }],
  })),
  updateLastAssistant: (content) => set((s) => {
    const msgs = [...s.messages];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') {
        msgs[i] = { ...msgs[i], content, isStreaming: false };
        break;
      }
    }
    return { messages: msgs };
  }),
  setIsLoading: (loading) => set({ isLoading: loading }),
  clearMessages: () => set({ messages: [] }),
}));
