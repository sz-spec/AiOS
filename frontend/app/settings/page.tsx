'use client';

import { useState, useEffect, useCallback } from 'react';
import { useSettings, ModelInfo, ModelsCatalog, ProviderInfo } from '@/hooks/useSettings';
import { SUPPORTED_LANGUAGES, LanguageCode } from '@/hooks/useVoiceInput';

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

interface ProviderManagementProps {
  getModelsCatalog: () => Promise<ModelsCatalog | null>;
  getActivatedModels: () => Promise<{ models: ModelInfo[]; count: number } | null>;
  activateModel: (modelId: string, apiKey?: string) => Promise<boolean>;
  deactivateModel: (modelId: string) => Promise<boolean>;
  getProviders: () => Promise<{ providers: ProviderInfo[]; total_providers: number } | null>;
  setProviderApiKey: (provider: string, apiKey: string) => Promise<boolean>;
  isSaving: boolean;
}

function ProviderManagement({ getModelsCatalog, getActivatedModels, activateModel, deactivateModel, getProviders, setProviderApiKey, isSaving }: ProviderManagementProps) {
  const [catalog, setCatalog] = useState<ModelsCatalog | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activatedModels, setActivatedModels] = useState<ModelInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [providerKeyInput, setProviderKeyInput] = useState('');
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [expandedModels, setExpandedModels] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    const [catalogData, activatedData, providersData] = await Promise.all([
      getModelsCatalog(),
      getActivatedModels(),
      getProviders(),
    ]);
    if (catalogData) setCatalog(catalogData);
    if (activatedData) setActivatedModels(activatedData.models);
    if (providersData) setProviders(providersData.providers);
    setIsLoading(false);
  }, [getModelsCatalog, getActivatedModels, getProviders]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSetProviderKey = async (providerKey: string) => {
    if (!providerKeyInput.trim()) return;
    const success = await setProviderApiKey(providerKey, providerKeyInput.trim());
    if (success) {
      setEditingProvider(null);
      setProviderKeyInput('');
      await loadData();
    }
  };

  const formatCost = (cost: number) => {
    if (cost === 0) return 'Free';
    if (cost < 0.001) return `$${(cost * 1000).toFixed(4)}/M`;
    return `$${cost.toFixed(4)}/1K`;
  };

  // Get models for a specific provider from catalog
  const getProviderModels = (providerName: string) => {
    if (!catalog?.providers) return [];
    return catalog.providers[providerName] || [];
  };

  // Check if a model is activated
  const isModelActivated = (modelId: string) => {
    return activatedModels.some(m => m.id === modelId);
  };

  if (isLoading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading providers...
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {providers.map((provider) => {
        const providerModels = getProviderModels(provider.name);
        const activeCount = providerModels.filter(m => isModelActivated(m.id)).length;
        const isExpanded = expandedProvider === provider.name;
        const showModels = expandedModels === provider.name;

        return (
          <div
            key={provider.name}
            style={{
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-lg)',
              border: `1px solid ${provider.has_api_key ? 'var(--success)' : 'var(--border-light)'}`,
              overflow: 'hidden',
            }}
          >
            {/* Provider Header */}
            <div
              style={{
                padding: '16px 20px',
                cursor: 'pointer',
                backgroundColor: isExpanded ? 'var(--bg-secondary)' : 'transparent',
              }}
              onClick={() => setExpandedProvider(isExpanded ? null : provider.name)}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ fontWeight: 600, fontSize: '16px' }}>{provider.name}</span>
                  {provider.has_api_key ? (
                    <span style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      padding: '3px 10px',
                      backgroundColor: 'rgba(5, 150, 105, 0.1)',
                      borderRadius: 'var(--radius-full)',
                      fontSize: '12px',
                      color: 'var(--success)',
                    }}>
                      <CheckIcon /> Connected
                    </span>
                  ) : (
                    <span style={{
                      padding: '3px 10px',
                      backgroundColor: 'rgba(245, 158, 11, 0.1)',
                      borderRadius: 'var(--radius-full)',
                      fontSize: '12px',
                      color: 'var(--warning)',
                    }}>
                      No API Key
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                  <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                    {activeCount}/{provider.model_count} models active
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                    {provider.endpoint}
                  </span>
                  <span style={{
                    transform: isExpanded ? 'rotate(180deg)' : 'rotate(0)',
                    transition: 'transform 0.2s',
                    color: 'var(--text-secondary)',
                  }}>
                    <ChevronDownIcon />
                  </span>
                </div>
              </div>
              <div style={{ marginTop: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                {provider.best_for}
              </div>
            </div>

            {/* Expanded Content */}
            {isExpanded && (
              <div style={{ borderTop: '1px solid var(--border-light)' }}>
                {/* API Key Section */}
                <div style={{ padding: '16px 20px', backgroundColor: 'var(--bg-tertiary)' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                    Provider API Key
                  </div>
                  {editingProvider === provider.provider_key ? (
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <input
                        type="password"
                        value={providerKeyInput}
                        onChange={(e) => setProviderKeyInput(e.target.value)}
                        placeholder={`Enter ${provider.provider_key}...`}
                        style={{
                          flex: 1,
                          padding: '10px 14px',
                          backgroundColor: 'var(--bg-primary)',
                          border: '1px solid var(--border-light)',
                          borderRadius: 'var(--radius-md)',
                          color: 'var(--text-primary)',
                          fontSize: '13px',
                        }}
                        onClick={(e) => e.stopPropagation()}
                        autoFocus
                      />
                      <button
                        onClick={(e) => { e.stopPropagation(); handleSetProviderKey(provider.provider_key); }}
                        disabled={isSaving || !providerKeyInput.trim()}
                        style={{
                          padding: '10px 20px',
                          backgroundColor: 'var(--accent)',
                          borderRadius: 'var(--radius-md)',
                          color: 'white',
                          fontSize: '13px',
                          fontWeight: 500,
                          border: 'none',
                          cursor: isSaving ? 'not-allowed' : 'pointer',
                          opacity: isSaving || !providerKeyInput.trim() ? 0.6 : 1,
                        }}
                      >
                        {isSaving ? 'Saving...' : 'Save Key'}
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setEditingProvider(null); setProviderKeyInput(''); }}
                        style={{
                          padding: '10px 16px',
                          backgroundColor: 'var(--bg-primary)',
                          border: '1px solid var(--border-light)',
                          borderRadius: 'var(--radius-md)',
                          color: 'var(--text-primary)',
                          fontSize: '13px',
                          cursor: 'pointer',
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <code style={{
                          padding: '6px 12px',
                          backgroundColor: 'var(--bg-primary)',
                          borderRadius: 'var(--radius-sm)',
                          fontSize: '12px',
                          color: 'var(--text-tertiary)',
                        }}>
                          {provider.provider_key}
                        </code>
                        {provider.has_api_key ? (
                          <span style={{ fontSize: '13px', fontFamily: 'monospace', color: 'var(--text-secondary)' }}>
                            {provider.masked_key}
                          </span>
                        ) : (
                          <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Not configured</span>
                        )}
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); setEditingProvider(provider.provider_key); setProviderKeyInput(''); }}
                        style={{
                          padding: '8px 16px',
                          backgroundColor: provider.has_api_key ? 'var(--bg-primary)' : 'var(--accent)',
                          border: provider.has_api_key ? '1px solid var(--border-light)' : 'none',
                          borderRadius: 'var(--radius-md)',
                          color: provider.has_api_key ? 'var(--text-primary)' : 'white',
                          fontSize: '13px',
                          fontWeight: 500,
                          cursor: 'pointer',
                        }}
                      >
                        {provider.has_api_key ? 'Update Key' : 'Add API Key'}
                      </button>
                    </div>
                  )}
                </div>

                {/* Provider Info */}
                <div style={{ padding: '16px 20px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '16px' }}>
                    <div>
                      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                        Strengths
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                        {provider.strengths?.map((s, i) => (
                          <span key={i} style={{
                            padding: '4px 10px',
                            backgroundColor: 'rgba(5, 150, 105, 0.1)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '12px',
                            color: 'var(--success)',
                          }}>
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                        Weaknesses
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                        {provider.weaknesses?.map((w, i) => (
                          <span key={i} style={{
                            padding: '4px 10px',
                            backgroundColor: 'rgba(220, 38, 38, 0.1)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '12px',
                            color: 'var(--error)',
                          }}>
                            {w}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '16px' }}>
                    <div>
                      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                        Unique Features
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                        {provider.unique?.map((u, i) => (
                          <span key={i} style={{
                            padding: '4px 10px',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '12px',
                            color: 'var(--accent)',
                          }}>
                            {u}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '8px', textTransform: 'uppercase' }}>
                        Context Window
                      </div>
                      <span style={{ fontSize: '14px', color: 'var(--text-primary)', fontWeight: 500 }}>
                        {provider.context}
                      </span>
                    </div>
                  </div>

                  <div style={{ padding: '12px 16px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', marginBottom: '16px' }}>
                    <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: '4px', textTransform: 'uppercase' }}>
                      Pricing Philosophy
                    </div>
                    <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                      {provider.pricing_philosophy}
                    </span>
                  </div>

                  {/* Models Accordion */}
                  <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: '16px' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        cursor: 'pointer',
                        padding: '8px 0',
                      }}
                      onClick={(e) => { e.stopPropagation(); setExpandedModels(showModels ? null : provider.name); }}
                    >
                      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        Available Models ({providerModels.length})
                      </div>
                      <span style={{
                        transform: showModels ? 'rotate(180deg)' : 'rotate(0)',
                        transition: 'transform 0.2s',
                        color: 'var(--text-secondary)',
                      }}>
                        <ChevronDownIcon />
                      </span>
                    </div>

                    {showModels && (
                      <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {providerModels.map((model) => {
                          const isActive = isModelActivated(model.id);
                          return (
                            <div
                              key={model.id}
                              style={{
                                padding: '12px 16px',
                                backgroundColor: isActive ? 'rgba(5, 150, 105, 0.05)' : 'var(--bg-secondary)',
                                borderRadius: 'var(--radius-md)',
                                border: `1px solid ${isActive ? 'var(--success)' : 'var(--border-light)'}`,
                              }}
                            >
                              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                                <div style={{ flex: 1 }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                    <span style={{ fontWeight: 600, fontSize: '13px' }}>{model.name}</span>
                                    {isActive && (
                                      <span style={{
                                        padding: '2px 6px',
                                        backgroundColor: 'var(--success)',
                                        borderRadius: 'var(--radius-sm)',
                                        fontSize: '10px',
                                        color: 'white',
                                        fontWeight: 600,
                                      }}>
                                        ACTIVE
                                      </span>
                                    )}
                                    {model.is_open_weight && (
                                      <span style={{
                                        padding: '2px 6px',
                                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                                        borderRadius: 'var(--radius-sm)',
                                        fontSize: '10px',
                                        color: 'var(--accent)',
                                      }}>
                                        Open Weight
                                      </span>
                                    )}
                                  </div>
                                  <p style={{ margin: '0 0 6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                    {model.description}
                                  </p>
                                  <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>
                                    <strong>Best for:</strong> {model.intended_for}
                                  </div>
                                  <div style={{ display: 'flex', gap: '12px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                                    <span>Input: <strong>{formatCost(model.cost_per_1k_input)}</strong></span>
                                    <span>Output: <strong>{formatCost(model.cost_per_1k_output)}</strong></span>
                                    <span>Context: <strong>{(model.max_context / 1000).toFixed(0)}K</strong></span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


function VoiceLanguageSettings() {
  const [selectedLanguage, setSelectedLanguage] = useState<LanguageCode>('en-US');
  const [isSupported, setIsSupported] = useState(true);

  useEffect(() => {
    // Check browser support
    const SpeechRecognition = typeof window !== 'undefined' &&
      (window.SpeechRecognition || (window as unknown as { webkitSpeechRecognition: unknown }).webkitSpeechRecognition);
    setIsSupported(!!SpeechRecognition);

    // Load saved preference
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('v-creator-voice-language');
      if (saved && SUPPORTED_LANGUAGES.some(l => l.code === saved)) {
        setSelectedLanguage(saved as LanguageCode);
      }
    }
  }, []);

  const handleLanguageChange = (langCode: LanguageCode) => {
    setSelectedLanguage(langCode);
    if (typeof window !== 'undefined') {
      localStorage.setItem('v-creator-voice-language', langCode);
    }
  };

  return (
    <section style={{ marginBottom: '40px' }}>
      <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, marginBottom: '20px' }}>Voice Input</h2>
      <div
        style={{
          padding: '20px 24px',
          backgroundColor: 'var(--bg-primary)',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-light)',
        }}
      >
        {!isSupported ? (
          <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
            Voice input is not supported in this browser. Please use Chrome for best results.
          </div>
        ) : (
          <>
            <div style={{ marginBottom: '16px' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: 500 }}>
                Default Language
              </label>
              <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                Select your preferred language for voice commands. You can also change this per-input using the language selector.
              </p>
              <select
                value={selectedLanguage}
                onChange={(e) => handleLanguageChange(e.target.value as LanguageCode)}
                style={{
                  width: '100%',
                  maxWidth: '300px',
                  padding: '10px 14px',
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text-primary)',
                  fontSize: '14px',
                  outline: 'none',
                }}
              >
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <option key={lang.code} value={lang.code}>
                    {lang.flag} {lang.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
              Supported languages: English, Spanish, French, German, Italian, Portuguese, Chinese, Japanese, Korean, Arabic
            </div>
          </>
        )}
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const {
    config,
    isLoading,
    isSaving,
    error,
    getModelsCatalog,
    getActivatedModels,
    activateModel,
    deactivateModel,
    getProviders,
    setProviderApiKey,
  } = useSettings();

  if (isLoading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '16px', color: 'var(--text-secondary)' }}>Loading settings...</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-secondary)' }}>
      <div style={{ maxWidth: '900px', margin: '0 auto', padding: '48px 24px' }}>
        {/* Header */}
        <div style={{ marginBottom: '40px' }}>
          <h1 style={{ margin: 0, fontSize: '28px', fontWeight: 600, marginBottom: '8px' }}>Settings</h1>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '15px' }}>
            Configure AI providers and preferences
          </p>
        </div>

        {error && (
          <div
            style={{
              padding: '14px 18px',
              marginBottom: '24px',
              backgroundColor: 'rgba(220, 38, 38, 0.1)',
              border: '1px solid var(--error)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--error)',
              fontSize: '14px',
            }}
          >
            {error}
          </div>
        )}

        {/* AI Providers Section - Unified view with API keys, info, and models */}
        <section style={{ marginBottom: '40px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
            <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>AI Providers</h2>
            <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              Click a provider to configure API key and view models
            </span>
          </div>
          <ProviderManagement
            getModelsCatalog={getModelsCatalog}
            getActivatedModels={getActivatedModels}
            activateModel={activateModel}
            deactivateModel={deactivateModel}
            getProviders={getProviders}
            setProviderApiKey={setProviderApiKey}
            isSaving={isSaving}
          />
        </section>

        {/* Voice Settings Section */}
        <VoiceLanguageSettings />

        {/* Services Section */}
        <section style={{ marginBottom: '40px' }}>
          <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, marginBottom: '20px' }}>Services</h2>
          <div
            style={{
              padding: '20px 24px',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-light)',
            }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
              <div>
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                  Ollama (Local Models)
                </div>
                <div style={{ fontFamily: 'monospace', fontSize: '14px', color: 'var(--text-primary)' }}>
                  {config?.ollama_url || 'http://localhost:11434'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                  Redis (Caching)
                </div>
                <div style={{ fontFamily: 'monospace', fontSize: '14px', color: config?.redis_url ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>
                  {config?.redis_url || 'Not configured'}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* About Section */}
        <section>
          <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, marginBottom: '20px' }}>About</h2>
          <div
            style={{
              padding: '20px 24px',
              backgroundColor: 'var(--bg-primary)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-light)',
            }}
          >
            <div style={{ marginBottom: '12px' }}>
              <span style={{ fontWeight: 600 }}>V-Creator</span>
              <span style={{ marginLeft: '8px', color: 'var(--text-secondary)', fontSize: '14px' }}>v1.0.0</span>
            </div>
            <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              AI-powered platform for code generation and business automation. Combines V-Core Business OS with a
              multi-provider AI engine for chat, code generation, and agent orchestration.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
