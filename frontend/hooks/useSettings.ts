'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

interface ApiKeyStatus {
  configured: boolean;
  masked_key: string | null;
}

interface ConfigStatus {
  openai: ApiKeyStatus;
  anthropic: ApiKeyStatus;
  google: ApiKeyStatus;
  tavily: ApiKeyStatus;
  github: ApiKeyStatus;
  redis_url: string | null;
  ollama_url: string | null;
}

interface GitHubUser {
  login: string;
  name: string | null;
  avatar_url: string;
  html_url: string;
}

interface GitHubUserResponse {
  configured: boolean;
  user: GitHubUser | null;
  error?: string;
}

interface CreateRepoResponse {
  success: boolean;
  repo_url: string;
  clone_url: string;
  full_name: string;
  owner: string;
  name: string;
  private: boolean;
  already_exists?: boolean;
}

interface ValidationResult {
  valid: boolean;
  error: string | null;
}

interface ValidationResults {
  results: Record<string, ValidationResult>;
  all_valid: boolean;
}

// Model types
export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  provider_key: string;
  description: string;
  strengths: string[];
  intended_for: string;
  cost_per_1k_input: number;
  cost_per_1k_output: number;
  max_context: number;
  capabilities: string[];
  is_open_weight: boolean;
  requires_local: boolean;
  is_activated: boolean;
  masked_key?: string | null;
  activated_at?: string;
}

export interface ModelsCatalog {
  providers: Record<string, ModelInfo[]>;
  total_models: number;
  activated_count: number;
}

export interface ActivatedModels {
  models: ModelInfo[];
  count: number;
}

export interface ProviderInfo {
  name: string;
  provider_key: string;
  endpoint: string;
  model_count: number;
  activated_count: number;
  has_api_key: boolean;
  masked_key: string | null;
  // Detailed info
  strengths: string[];
  weaknesses: string[];
  context: string;
  unique: string[];
  best_for: string;
  pricing_philosophy: string;
}

export interface ProvidersResponse {
  providers: ProviderInfo[];
  total_providers: number;
}

// Use relative URLs to go through Next.js proxy (enables ngrok with single tunnel)
const API_BASE = '';

export function useSettings() {
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [validation, setValidation] = useState<ValidationResults | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getToken } = useAuth();

  const fetchConfig = useCallback(async () => {
    try {
      setIsLoading(true);
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/status`);
      if (!response.ok) throw new Error('Failed to fetch config');
      const data = await response.json();
      setConfig(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch configuration');
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const updateApiKey = async (provider: string, apiKey: string) => {
    try {
      setIsSaving(true);
      setError(null);

      const keyMap: Record<string, string> = {
        openai: 'openai_api_key',
        anthropic: 'anthropic_api_key',
        google: 'google_api_key',
        tavily: 'tavily_api_key',
        github: 'github_token',
      };

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/api-keys`, {
        method: 'POST',
        body: JSON.stringify({ [keyMap[provider]]: apiKey }),
      });

      if (!response.ok) throw new Error('Failed to update API key');

      await fetchConfig();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update API key');
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const deleteApiKey = async (provider: string) => {
    try {
      setIsSaving(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/api-keys/${provider}`, {
        method: 'DELETE',
      });

      if (!response.ok) throw new Error('Failed to delete API key');

      await fetchConfig();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete API key');
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const validateKeys = async () => {
    try {
      setIsValidating(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/validate`, {
        method: 'POST',
      });

      if (!response.ok) throw new Error('Failed to validate keys');

      const data = await response.json();
      setValidation(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to validate keys');
      return null;
    } finally {
      setIsValidating(false);
    }
  };

  const getGitHubUser = async (): Promise<GitHubUserResponse | null> => {
    try {
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/github/user`);
      if (!response.ok) throw new Error('Failed to get GitHub user');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to get GitHub user');
      return null;
    }
  };

  const createGitHubRepo = async (
    name: string,
    description?: string,
    isPrivate: boolean = true
  ): Promise<CreateRepoResponse | null> => {
    try {
      setIsSaving(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/github/create-repo`, {
        method: 'POST',
        body: JSON.stringify({ name, description, private: isPrivate }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create repository');
      }

      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create repository');
      return null;
    } finally {
      setIsSaving(false);
    }
  };

  // Model management functions (memoized to prevent infinite re-fetch loops)
  const getModelsCatalog = useCallback(async (): Promise<ModelsCatalog | null> => {
    try {
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/catalog`);
      if (!response.ok) throw new Error('Failed to fetch models catalog');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch models catalog');
      return null;
    }
  }, [getToken]);

  const getActivatedModels = useCallback(async (): Promise<ActivatedModels | null> => {
    try {
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/activated`);
      if (!response.ok) throw new Error('Failed to fetch activated models');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch activated models');
      return null;
    }
  }, [getToken]);

  const activateModel = useCallback(async (modelId: string, apiKey?: string): Promise<boolean> => {
    try {
      setIsSaving(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/activate`, {
        method: 'POST',
        body: JSON.stringify({ model_id: modelId, api_key: apiKey }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to activate model');
      }

      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to activate model');
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [getToken]);

  const deactivateModel = useCallback(async (modelId: string): Promise<boolean> => {
    try {
      setIsSaving(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/deactivate/${modelId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to deactivate model');
      }

      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to deactivate model');
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [getToken]);

  const getModelDetails = useCallback(async (modelId: string): Promise<ModelInfo | null> => {
    try {
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/${modelId}`);
      if (!response.ok) throw new Error('Failed to fetch model details');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch model details');
      return null;
    }
  }, [getToken]);

  const getProviders = useCallback(async (): Promise<ProvidersResponse | null> => {
    try {
      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/providers/list`);
      if (!response.ok) throw new Error('Failed to fetch providers');
      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch providers');
      return null;
    }
  }, [getToken]);

  const setProviderApiKey = useCallback(async (provider: string, apiKey: string): Promise<boolean> => {
    try {
      setIsSaving(true);
      setError(null);

      const response = await apiFetch(getToken, `${API_BASE}/api/settings/models/providers/${provider}/api-key`, {
        method: 'POST',
        body: JSON.stringify({ api_key: apiKey }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to set API key');
      }

      await fetchConfig();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set API key');
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [fetchConfig, getToken]);

  return {
    config,
    validation,
    isLoading,
    isSaving,
    isValidating,
    error,
    updateApiKey,
    deleteApiKey,
    validateKeys,
    getGitHubUser,
    createGitHubRepo,
    refresh: fetchConfig,
    // Model management
    getModelsCatalog,
    getActivatedModels,
    activateModel,
    deactivateModel,
    getModelDetails,
    getProviders,
    setProviderApiKey,
  };
}
