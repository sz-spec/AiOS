import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useSettings } from '../useSettings';

describe('useSettings', () => {
  beforeEach(() => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network unavailable'));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- Initial State ----

  it('starts with null config and null validation', () => {
    const { result } = renderHook(() => useSettings());
    expect(result.current.config).toBeNull();
    expect(result.current.validation).toBeNull();
  });

  it('starts with isLoading true (fetchConfig runs on mount)', () => {
    const { result } = renderHook(() => useSettings());
    // isLoading is initialized to true in the hook
    expect(result.current.isLoading).toBe(true);
  });

  it('starts with isSaving false and isValidating false', () => {
    const { result } = renderHook(() => useSettings());
    expect(result.current.isSaving).toBe(false);
    expect(result.current.isValidating).toBe(false);
  });

  // ---- fetchConfig ----

  it('fetchConfig populates config on success (via useEffect on mount)', async () => {
    const mockConfig = {
      openai: { configured: true, masked_key: 'sk-...abc' },
      anthropic: { configured: false, masked_key: null },
      google: { configured: false, masked_key: null },
      tavily: { configured: false, masked_key: null },
      github: { configured: false, masked_key: null },
      redis_url: null,
      ollama_url: null,
    };

    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => mockConfig,
    } as Response);

    const { result } = renderHook(() => useSettings());

    await waitFor(() => {
      expect(result.current.config).toEqual(mockConfig);
      expect(result.current.isLoading).toBe(false);
      expect(result.current.error).toBeNull();
    });
  });

  it('fetchConfig sets error on failure', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 500,
    } as Response);

    const { result } = renderHook(() => useSettings());

    await waitFor(() => {
      expect(result.current.error).toBe('Failed to fetch config');
      expect(result.current.isLoading).toBe(false);
    });
  });

  // ---- updateApiKey ----

  it('updateApiKey returns true and refreshes config on success', async () => {
    const initialConfig = {
      openai: { configured: false, masked_key: null },
      anthropic: { configured: false, masked_key: null },
      google: { configured: false, masked_key: null },
      tavily: { configured: false, masked_key: null },
      github: { configured: false, masked_key: null },
      redis_url: null,
      ollama_url: null,
    };

    const updatedConfig = {
      ...initialConfig,
      openai: { configured: true, masked_key: 'sk-...xyz' },
    };

    vi.spyOn(global, 'fetch')
      // useEffect fetchConfig
      .mockResolvedValueOnce({
        ok: true,
        json: async () => initialConfig,
      } as Response)
      // updateApiKey POST
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true }),
      } as Response)
      // re-fetchConfig after update
      .mockResolvedValueOnce({
        ok: true,
        json: async () => updatedConfig,
      } as Response);

    const { result } = renderHook(() => useSettings());

    await waitFor(() => {
      expect(result.current.config).toEqual(initialConfig);
    });

    let success: boolean = false;
    await act(async () => {
      success = await result.current.updateApiKey('openai', 'sk-new-key');
    });

    expect(success).toBe(true);
    expect(result.current.config?.openai.configured).toBe(true);
  });

  it('updateApiKey returns false and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable')) // useEffect fetchConfig
      .mockResolvedValueOnce({ ok: false, status: 400 } as Response);

    const { result } = renderHook(() => useSettings());

    let success: boolean = true;
    await act(async () => {
      success = await result.current.updateApiKey('openai', 'bad-key');
    });

    expect(success).toBe(false);
    expect(result.current.error).toBe('Failed to update API key');
  });

  // ---- validateKeys ----

  it('validateKeys populates validation results on success', async () => {
    const mockValidation = {
      results: {
        openai: { valid: true, error: null },
        anthropic: { valid: false, error: 'Invalid key' },
      },
      all_valid: false,
    };

    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable')) // useEffect fetchConfig
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockValidation,
      } as Response);

    const { result } = renderHook(() => useSettings());

    let validationResult: any;
    await act(async () => {
      validationResult = await result.current.validateKeys();
    });

    expect(validationResult).toEqual(mockValidation);
    expect(result.current.validation).toEqual(mockValidation);
  });

  it('validateKeys returns null and sets error on failure', async () => {
    vi.spyOn(global, 'fetch')
      .mockRejectedValueOnce(new Error('Network unavailable'))
      .mockResolvedValueOnce({ ok: false, status: 500 } as Response);

    const { result } = renderHook(() => useSettings());

    let validationResult: any;
    await act(async () => {
      validationResult = await result.current.validateKeys();
    });

    expect(validationResult).toBeNull();
    expect(result.current.error).toBe('Failed to validate keys');
  });

  // ---- Error handling ----

  it('fetchConfig sets error on network exception', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValueOnce(new Error('ECONNREFUSED'));

    const { result } = renderHook(() => useSettings());

    await waitFor(() => {
      expect(result.current.error).toBe('ECONNREFUSED');
    });
  });

  it('refresh function re-fetches config', async () => {
    const config1 = {
      openai: { configured: false, masked_key: null },
      anthropic: { configured: false, masked_key: null },
      google: { configured: false, masked_key: null },
      tavily: { configured: false, masked_key: null },
      github: { configured: false, masked_key: null },
      redis_url: null,
      ollama_url: null,
    };
    const config2 = { ...config1, redis_url: 'redis://localhost:6379' };

    vi.spyOn(global, 'fetch')
      // Initial fetchConfig
      .mockResolvedValueOnce({
        ok: true,
        json: async () => config1,
      } as Response)
      // refresh call
      .mockResolvedValueOnce({
        ok: true,
        json: async () => config2,
      } as Response);

    const { result } = renderHook(() => useSettings());

    await waitFor(() => {
      expect(result.current.config).toEqual(config1);
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.config).toEqual(config2);
  });
});
