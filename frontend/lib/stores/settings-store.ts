import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { persist } from 'zustand/middleware';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useSettings.ts)
// ---------------------------------------------------------------------------

export interface ApiKeyStatus {
  configured: boolean;
  masked_key: string | null;
}

export interface ConfigStatus {
  openai: ApiKeyStatus;
  anthropic: ApiKeyStatus;
  google: ApiKeyStatus;
  tavily: ApiKeyStatus;
  github: ApiKeyStatus;
  redis_url: string | null;
  ollama_url: string | null;
}

export interface GitHubUser {
  login: string;
  name: string | null;
  avatar_url: string;
  html_url: string;
}

export interface GitHubUserResponse {
  configured: boolean;
  user: GitHubUser | null;
  error?: string;
}

export interface CreateRepoResponse {
  success: boolean;
  repo_url: string;
  clone_url: string;
  full_name: string;
  owner: string;
  name: string;
  private: boolean;
  already_exists?: boolean;
}

export interface ValidationResult {
  valid: boolean;
  error: string | null;
}

export interface ValidationResults {
  results: Record<string, ValidationResult>;
  all_valid: boolean;
}

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

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

interface SettingsState {
  // ---- data ----
  config: ConfigStatus | null;
  validation: ValidationResults | null;
  settings: Record<string, any>;
  isLoading: boolean;
  isSaving: boolean;
  isValidating: boolean;
  error: string | null;

  // ---- actions ----
  fetchSettings: (getToken: () => Promise<string | null>) => Promise<void>;
  updateSetting: (
    key: string,
    value: any,
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  resetSettings: () => void;

  // API key management
  updateApiKey: (
    provider: string,
    apiKey: string,
    getToken: () => Promise<string | null>,
  ) => Promise<boolean>;
  deleteApiKey: (
    provider: string,
    getToken: () => Promise<string | null>,
  ) => Promise<boolean>;
  validateKeys: (
    getToken: () => Promise<string | null>,
  ) => Promise<ValidationResults | null>;

  // GitHub
  getGitHubUser: (
    getToken: () => Promise<string | null>,
  ) => Promise<GitHubUserResponse | null>;
  createGitHubRepo: (
    name: string,
    getToken: () => Promise<string | null>,
    description?: string,
    isPrivate?: boolean,
  ) => Promise<CreateRepoResponse | null>;

  // Model management
  getModelsCatalog: (
    getToken: () => Promise<string | null>,
  ) => Promise<ModelsCatalog | null>;
  getActivatedModels: (
    getToken: () => Promise<string | null>,
  ) => Promise<ActivatedModels | null>;
  activateModel: (
    modelId: string,
    getToken: () => Promise<string | null>,
    apiKey?: string,
  ) => Promise<boolean>;
  deactivateModel: (
    modelId: string,
    getToken: () => Promise<string | null>,
  ) => Promise<boolean>;
  getModelDetails: (
    modelId: string,
    getToken: () => Promise<string | null>,
  ) => Promise<ModelInfo | null>;
  getProviders: (
    getToken: () => Promise<string | null>,
  ) => Promise<ProvidersResponse | null>;
  setProviderApiKey: (
    provider: string,
    apiKey: string,
    getToken: () => Promise<string | null>,
  ) => Promise<boolean>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const API_BASE = '';

const authHeader = async (
  getToken: () => Promise<string | null>,
): Promise<Record<string, string>> => {
  const token = await getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

// ---------------------------------------------------------------------------
// Store (with persist middleware for localStorage)
// ---------------------------------------------------------------------------

export const useSettingsStore = create<SettingsState>()(
  persist(
    immer((set, get) => ({
      // ---- initial state ----
      config: null,
      validation: null,
      settings: {},
      isLoading: true,
      isSaving: false,
      isValidating: false,
      error: null,

      // ---- actions ----

      fetchSettings: async (getToken) => {
        try {
          set((state) => {
            state.isLoading = true;
          });
          const hdrs = await authHeader(getToken);
          const response = await fetch(`${API_BASE}/api/settings/status`, {
            headers: hdrs,
          });
          if (!response.ok) throw new Error('Failed to fetch config');
          const data = await response.json();
          set((state) => {
            state.config = data;
            state.error = null;
          });
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to fetch configuration';
          });
        } finally {
          set((state) => {
            state.isLoading = false;
          });
        }
      },

      updateSetting: async (key, value, getToken) => {
        set((state) => {
          state.settings[key] = value;
        });
        // Persist to server if there is an endpoint for generic settings
        // Currently the hook uses specific endpoints (api-keys, models, etc.)
        // This is a local-first setter; specific endpoints are called directly.
      },

      resetSettings: () => {
        set((state) => {
          state.settings = {};
          state.config = null;
          state.validation = null;
          state.error = null;
        });
      },

      updateApiKey: async (provider, apiKey, getToken) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const keyMap: Record<string, string> = {
            openai: 'openai_api_key',
            anthropic: 'anthropic_api_key',
            google: 'google_api_key',
            tavily: 'tavily_api_key',
            github: 'github_token',
          };

          const hdrs = await authHeader(getToken);
          const response = await fetch(`${API_BASE}/api/settings/api-keys`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...hdrs },
            body: JSON.stringify({ [keyMap[provider]]: apiKey }),
          });

          if (!response.ok) throw new Error('Failed to update API key');

          // Refresh config
          await get().fetchSettings(getToken);
          return true;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to update API key';
          });
          return false;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },

      deleteApiKey: async (provider, getToken) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/api-keys/${provider}`,
            {
              method: 'DELETE',
              headers: hdrs,
            },
          );

          if (!response.ok) throw new Error('Failed to delete API key');

          await get().fetchSettings(getToken);
          return true;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to delete API key';
          });
          return false;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },

      validateKeys: async (getToken) => {
        try {
          set((state) => {
            state.isValidating = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(`${API_BASE}/api/settings/validate`, {
            method: 'POST',
            headers: hdrs,
          });

          if (!response.ok) throw new Error('Failed to validate keys');

          const data: ValidationResults = await response.json();
          set((state) => {
            state.validation = data;
          });
          return data;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to validate keys';
          });
          return null;
        } finally {
          set((state) => {
            state.isValidating = false;
          });
        }
      },

      getGitHubUser: async (getToken) => {
        try {
          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/github/user`,
            { headers: hdrs },
          );
          if (!response.ok) throw new Error('Failed to get GitHub user');
          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to get GitHub user';
          });
          return null;
        }
      },

      createGitHubRepo: async (name, getToken, description?, isPrivate = true) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/github/create-repo`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...hdrs },
              body: JSON.stringify({
                name,
                description,
                private: isPrivate,
              }),
            },
          );

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(
              errorData.detail || 'Failed to create repository',
            );
          }

          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to create repository';
          });
          return null;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },

      getModelsCatalog: async (getToken) => {
        try {
          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/catalog`,
            { headers: hdrs },
          );
          if (!response.ok)
            throw new Error('Failed to fetch models catalog');
          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to fetch models catalog';
          });
          return null;
        }
      },

      getActivatedModels: async (getToken) => {
        try {
          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/activated`,
            { headers: hdrs },
          );
          if (!response.ok)
            throw new Error('Failed to fetch activated models');
          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to fetch activated models';
          });
          return null;
        }
      },

      activateModel: async (modelId, getToken, apiKey?) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/activate`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...hdrs },
              body: JSON.stringify({ model_id: modelId, api_key: apiKey }),
            },
          );

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(
              errorData.detail || 'Failed to activate model',
            );
          }

          return true;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to activate model';
          });
          return false;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },

      deactivateModel: async (modelId, getToken) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/deactivate/${modelId}`,
            {
              method: 'DELETE',
              headers: hdrs,
            },
          );

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(
              errorData.detail || 'Failed to deactivate model',
            );
          }

          return true;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to deactivate model';
          });
          return false;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },

      getModelDetails: async (modelId, getToken) => {
        try {
          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/${modelId}`,
            { headers: hdrs },
          );
          if (!response.ok)
            throw new Error('Failed to fetch model details');
          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to fetch model details';
          });
          return null;
        }
      },

      getProviders: async (getToken) => {
        try {
          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/providers/list`,
            { headers: hdrs },
          );
          if (!response.ok) throw new Error('Failed to fetch providers');
          return await response.json();
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to fetch providers';
          });
          return null;
        }
      },

      setProviderApiKey: async (provider, apiKey, getToken) => {
        try {
          set((state) => {
            state.isSaving = true;
            state.error = null;
          });

          const hdrs = await authHeader(getToken);
          const response = await fetch(
            `${API_BASE}/api/settings/models/providers/${provider}/api-key`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...hdrs },
              body: JSON.stringify({ api_key: apiKey }),
            },
          );

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(
              errorData.detail || 'Failed to set API key',
            );
          }

          await get().fetchSettings(getToken);
          return true;
        } catch (err) {
          set((state) => {
            state.error =
              err instanceof Error
                ? err.message
                : 'Failed to set API key';
          });
          return false;
        } finally {
          set((state) => {
            state.isSaving = false;
          });
        }
      },
    })),
    {
      name: 'vos3-settings-store',
      // Only persist the local settings map, not server-fetched config
      partialize: (state) => ({
        settings: state.settings,
      }),
    },
  ),
);
