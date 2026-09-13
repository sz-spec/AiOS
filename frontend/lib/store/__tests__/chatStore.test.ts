import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';

describe('useChatStore', () => {
  beforeEach(() => {
    // Merge reset — preserves action functions
    useChatStore.setState({ messages: [], isLoading: false, projectId: null });
  });

  it('addMessage with explicit id preserves it', () => {
    const { addMessage } = useChatStore.getState();
    addMessage({ id: 'explicit-1', role: 'user', content: 'hello', timestamp: '2026-01-01T00:00:00Z' });
    const msgs = useChatStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe('explicit-1');
    expect(msgs[0].content).toBe('hello');
  });

  it('addMessage auto-generates id when id is empty string', () => {
    const { addMessage } = useChatStore.getState();
    addMessage({ id: '', role: 'user', content: 'auto id test', timestamp: '2026-01-01T00:00:00Z' });
    const msgs = useChatStore.getState().messages;
    expect(msgs[0].id).toMatch(/^msg-\d+$/);
  });

  it('addMessage appends messages in order', () => {
    const { addMessage } = useChatStore.getState();
    addMessage({ id: 'a', role: 'user', content: 'first', timestamp: '2026-01-01T00:00:00Z' });
    addMessage({ id: 'b', role: 'assistant', content: 'second', timestamp: '2026-01-01T00:00:01Z' });
    const msgs = useChatStore.getState().messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[0].id).toBe('a');
    expect(msgs[1].id).toBe('b');
  });

  it('updateLastAssistant updates most recent assistant message', () => {
    const { addMessage, updateLastAssistant } = useChatStore.getState();
    addMessage({ id: 'u1', role: 'user', content: 'question', timestamp: '2026-01-01T00:00:00Z' });
    addMessage({ id: 'a1', role: 'assistant', content: 'old content', timestamp: '2026-01-01T00:00:01Z' });
    updateLastAssistant('updated content');
    const msgs = useChatStore.getState().messages;
    expect(msgs[1].content).toBe('updated content');
    expect(msgs[1].isStreaming).toBe(false);
  });

  it('updateLastAssistant skips user messages to find last assistant', () => {
    const { addMessage, updateLastAssistant } = useChatStore.getState();
    addMessage({ id: 'a1', role: 'assistant', content: 'first reply', timestamp: '2026-01-01T00:00:00Z' });
    addMessage({ id: 'u1', role: 'user', content: 'followup', timestamp: '2026-01-01T00:00:01Z' });
    addMessage({ id: 'a2', role: 'assistant', content: 'second reply', timestamp: '2026-01-01T00:00:02Z' });
    updateLastAssistant('patched');
    const msgs = useChatStore.getState().messages;
    expect(msgs[0].content).toBe('first reply');
    expect(msgs[2].content).toBe('patched');
  });

  it('clearMessages empties the messages array', () => {
    const { addMessage, clearMessages } = useChatStore.getState();
    addMessage({ id: 'x', role: 'user', content: 'test', timestamp: '2026-01-01T00:00:00Z' });
    clearMessages();
    expect(useChatStore.getState().messages).toHaveLength(0);
  });

  it('setProjectId stores project id', () => {
    useChatStore.getState().setProjectId('proj-abc');
    expect(useChatStore.getState().projectId).toBe('proj-abc');
  });

  it('setProjectId accepts null to clear project', () => {
    useChatStore.getState().setProjectId('proj-abc');
    useChatStore.getState().setProjectId(null);
    expect(useChatStore.getState().projectId).toBeNull();
  });
});
