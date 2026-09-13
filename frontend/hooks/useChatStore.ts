/**
 * Bridge hook for gradual migration from useChat (React hooks + fetch)
 * to the Zustand chat store.
 *
 * Usage: Replace `useChat()` with `useChatStoreBridge()` in components.
 * The returned API shape matches the original useChat hook so call-sites
 * can migrate with minimal diff.
 */
import { useEffect, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useChatStateStore } from '@/lib/stores/chat-store';

export function useChatStoreBridge() {
  const { getToken } = useAuth();

  const messages = useChatStateStore((s) => s.messages);
  const isLoading = useChatStateStore((s) => s.isStreaming);
  const models = useChatStateStore((s) => s.models);
  const selectedModel = useChatStateStore((s) => s.currentModel);
  const error = useChatStateStore((s) => s.error);
  const vosEnabled = useChatStateStore((s) => s.vosEnabled);

  const storeLoadModels = useChatStateStore((s) => s.loadModels);
  const storeSendMessage = useChatStateStore((s) => s.sendMessage);
  const storeClearMessages = useChatStateStore((s) => s.clearMessages);
  const storeSetModel = useChatStateStore((s) => s.setModel);
  const storeSetVosEnabled = useChatStateStore((s) => s.setVosEnabled);
  const storeEndSession = useChatStateStore((s) => s.endSession);

  // Auto-load models on mount (matches original useChat behavior)
  useEffect(() => {
    storeLoadModels(getToken);
  }, [storeLoadModels, getToken]);

  const sendMessage = useCallback(
    (content: string) => storeSendMessage(content, getToken),
    [storeSendMessage, getToken],
  );

  const clearHistory = storeClearMessages;

  const setSelectedModel = storeSetModel;

  const setVosEnabled = storeSetVosEnabled;

  const endSession = useCallback(
    () => storeEndSession(getToken),
    [storeEndSession, getToken],
  );

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
  };
}
