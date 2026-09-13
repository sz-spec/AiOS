import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChat } from '../useChat';

describe('useChat', () => {
  beforeEach(() => {
    // Default: loadModels fails silently → models stay at DEFAULT_MODELS
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts with DEFAULT_MODELS (89 models)', () => {
    const { result } = renderHook(() => useChat());
    expect(result.current.models).toHaveLength(89);
  });

  it('defaults to gpt-4o-mini as selected model', () => {
    const { result } = renderHook(() => useChat());
    expect(result.current.selectedModel).toBe('gpt-4o-mini');
  });

  it('starts with empty messages and null error', () => {
    const { result } = renderHook(() => useChat());
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.error).toBeNull();
  });

  it('sendMessage adds user message and assistant reply', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('models unavailable')) // loadModels
      .mockRejectedValueOnce(new Error('VOS unavailable'))    // /api/vos/process
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ message: { content: 'Hello back!' } }),
      } as Response);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('Hello');
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].role).toBe('user');
    expect(result.current.messages[0].content).toBe('Hello');
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('Hello back!');
  });

  it('sendMessage sets error string on HTTP failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('models unavailable'))
      .mockRejectedValueOnce(new Error('VOS unavailable'))    // /api/vos/process
      .mockResolvedValueOnce({ ok: false, status: 500 } as Response);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('test');
    });

    expect(result.current.error).toMatch(/HTTP error: 500/);
    // User message still added even on error
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].role).toBe('user');
  });

  it('sendMessage sets error on network failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('models unavailable'))
      .mockRejectedValueOnce(new Error('VOS unavailable'))    // /api/vos/process
      .mockRejectedValueOnce(new Error('Connection refused'));

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('test');
    });

    expect(result.current.error).toBe('Connection refused');
  });

  it('clearHistory empties messages and clears error', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('models unavailable'))
      .mockRejectedValueOnce(new Error('VOS unavailable'))    // /api/vos/process
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ message: { content: 'reply' } }),
      } as Response);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('hi');
    });

    act(() => result.current.clearHistory());

    expect(result.current.messages).toHaveLength(0);
    expect(result.current.error).toBeNull();
  });

  it('setSelectedModel updates the model', () => {
    const { result } = renderHook(() => useChat());
    act(() => result.current.setSelectedModel('claude-sonnet-4'));
    expect(result.current.selectedModel).toBe('claude-sonnet-4');
  });

  it('loadModels updates models when API returns them', async () => {
    const apiModels = [
      { id: 'model-x', provider: 'openai', cost_per_1k_input: 0.001, cost_per_1k_output: 0.002 },
    ];

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ models: apiModels }),
    } as Response);

    const { result } = renderHook(() => useChat());

    await waitFor(() => {
      expect(result.current.models).toEqual(apiModels);
    });
  });

  it('sendMessage messages have unique ids', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('models unavailable'))
      .mockRejectedValueOnce(new Error('VOS unavailable'))    // /api/vos/process
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ message: { content: 'reply' } }),
      } as Response);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      await result.current.sendMessage('hi');
    });

    const [user, assistant] = result.current.messages;
    expect(user.id).toBeTruthy();
    expect(assistant.id).toBeTruthy();
    expect(user.id).not.toBe(assistant.id);
  });
});
