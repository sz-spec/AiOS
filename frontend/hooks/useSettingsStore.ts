/**
 * Bridge hook for gradual migration from useSettings (React hooks + fetch)
 * to the Zustand settings store.
 *
 * Usage: Replace `useSettings()` with `useSettingsStoreBridge()` in components.
 * The returned API shape matches the original useSettings hook so call-sites
 * can migrate with minimal diff.
 */
import { useEffect, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { useSettingsStore } from '@/lib/stores/settings-store';

export function useSettingsStoreBridge() {
  const { getToken } = useAuth();

  const config = useSettingsStore((s) => s.config);
  const validation = useSettingsStore((s) => s.validation);
  const isLoading = useSettingsStore((s) => s.isLoading);
  const isSaving = useSettingsStore((s) => s.isSaving);
  const isValidating = useSettingsStore((s) => s.isValidating);
  const error = useSettingsStore((s) => s.error);

  const storeFetchSettings = useSettingsStore((s) => s.fetchSettings);
  const storeUpdateApiKey = useSettingsStore((s) => s.updateApiKey);
  const storeDeleteApiKey = useSettingsStore((s) => s.deleteApiKey);
  const storeValidateKeys = useSettingsStore((s) => s.validateKeys);
  const storeGetGitHubUser = useSettingsStore((s) => s.getGitHubUser);
  const storeCreateGitHubRepo = useSettingsStore((s) => s.createGitHubRepo);
  const storeGetModelsCatalog = useSettingsStore((s) => s.getModelsCatalog);
  const storeGetActivatedModels = useSettingsStore((s) => s.getActivatedModels);
  const storeActivateModel = useSettingsStore((s) => s.activateModel);
  const storeDeactivateModel = useSettingsStore((s) => s.deactivateModel);
  const storeGetModelDetails = useSettingsStore((s) => s.getModelDetails);
  const storeGetProviders = useSettingsStore((s) => s.getProviders);
  const storeSetProviderApiKey = useSettingsStore((s) => s.setProviderApiKey);

  // Auto-fetch config on mount (matches original useSettings behavior)
  useEffect(() => {
    storeFetchSettings(getToken);
  }, [storeFetchSettings, getToken]);

  // Wrap store actions to inject getToken automatically
  const updateApiKey = useCallback(
    (provider: string, apiKey: string) =>
      storeUpdateApiKey(provider, apiKey, getToken),
    [storeUpdateApiKey, getToken],
  );

  const deleteApiKey = useCallback(
    (provider: string) => storeDeleteApiKey(provider, getToken),
    [storeDeleteApiKey, getToken],
  );

  const validateKeys = useCallback(
    () => storeValidateKeys(getToken),
    [storeValidateKeys, getToken],
  );

  const getGitHubUser = useCallback(
    () => storeGetGitHubUser(getToken),
    [storeGetGitHubUser, getToken],
  );

  const createGitHubRepo = useCallback(
    (name: string, description?: string, isPrivate: boolean = true) =>
      storeCreateGitHubRepo(name, getToken, description, isPrivate),
    [storeCreateGitHubRepo, getToken],
  );

  const refresh = useCallback(
    () => storeFetchSettings(getToken),
    [storeFetchSettings, getToken],
  );

  const getModelsCatalog = useCallback(
    () => storeGetModelsCatalog(getToken),
    [storeGetModelsCatalog, getToken],
  );

  const getActivatedModels = useCallback(
    () => storeGetActivatedModels(getToken),
    [storeGetActivatedModels, getToken],
  );

  const activateModel = useCallback(
    (modelId: string, apiKey?: string) =>
      storeActivateModel(modelId, getToken, apiKey),
    [storeActivateModel, getToken],
  );

  const deactivateModel = useCallback(
    (modelId: string) => storeDeactivateModel(modelId, getToken),
    [storeDeactivateModel, getToken],
  );

  const getModelDetails = useCallback(
    (modelId: string) => storeGetModelDetails(modelId, getToken),
    [storeGetModelDetails, getToken],
  );

  const getProviders = useCallback(
    () => storeGetProviders(getToken),
    [storeGetProviders, getToken],
  );

  const setProviderApiKey = useCallback(
    (provider: string, apiKey: string) =>
      storeSetProviderApiKey(provider, apiKey, getToken),
    [storeSetProviderApiKey, getToken],
  );

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
    refresh,
    getModelsCatalog,
    getActivatedModels,
    activateModel,
    deactivateModel,
    getModelDetails,
    getProviders,
    setProviderApiKey,
  };
}
