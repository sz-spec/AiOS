'use client';

import React, { useState, useCallback, useEffect } from 'react';
import { AgentFormData, VoiceSettings } from './AgentForm';
import { AgentAvatar } from './AgentAvatar';
import { TemperatureSlider } from './TemperatureSlider';
import { VoiceInput } from '@/components/shared/VoiceInput';

interface CustomAgentBuilderProps {
  onSubmit: (data: AgentFormData) => Promise<void>;
  onCancel: () => void;
  isLoading?: boolean;
}

const STEPS = ['Identity', 'Intelligence', 'Voice', 'Review'] as const;
type Step = 0 | 1 | 2 | 3;

const AVATAR_COLORS = [
  '#6366F1', '#8B5CF6', '#EC4899', '#EF4444',
  '#F59E0B', '#10B981', '#14B8A6', '#06B6D4',
  '#3B82F6', '#6D28D9', '#D97706', '#059669',
];

// Fallback models when API is unavailable
const FALLBACK_MODELS = [
  { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4', provider: 'Anthropic' },
  { value: 'claude-3-5-haiku-20241022', label: 'Claude 3.5 Haiku', provider: 'Anthropic' },
  { value: 'gpt-4o', label: 'GPT-4o', provider: 'OpenAI' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini', provider: 'OpenAI' },
  { value: 'gemini-1.5-pro', label: 'Gemini 1.5 Pro', provider: 'Google' },
  { value: 'gemini-1.5-flash', label: 'Gemini 1.5 Flash', provider: 'Google' },
  { value: 'llama3.1:8b', label: 'LLaMA 3.1 8B', provider: 'Ollama' },
];

interface CatalogModel {
  id: string;
  name: string;
  provider: string;
}

const ELEVENLABS_VOICES = [
  { id: '', label: 'None (No voice)', accent: '' },
  { id: 'pNInz6obpgDQGcFmaJgB', label: 'Adam', accent: 'Deep, warm American male' },
  { id: '21m00Tcm4TlvDq8ikWAM', label: 'Rachel', accent: 'Calm, professional American female' },
  { id: 'EXAVITQu4vr4xnSDxMaL', label: 'Bella', accent: 'Warm, friendly American female' },
  { id: 'ErXwobaYiN019PkySvjV', label: 'Antoni', accent: 'Well-rounded American male' },
  { id: 'MF3mGyEYCl7XYWbV9V6O', label: 'Elli', accent: 'Young, enthusiastic American female' },
  { id: 'TxGEqnHWrfWFTfGW9XjX', label: 'Josh', accent: 'Dynamic young American male' },
  { id: 'VR6AewLTigWG4xSOukaG', label: 'Arnold', accent: 'Confident, deep American male' },
  { id: 'nPczCjzI2devNBz1zQrb', label: 'Brian', accent: 'Deep, narration American male' },
  { id: 'onwK4e9ZLuTAKqWW03F9', label: 'Daniel', accent: 'Authoritative British male' },
  { id: 'XB0fDUnXU5powFXDhCwa', label: 'Charlotte', accent: 'Elegant British female' },
  { id: 'jBpfuIE2acCO8z3wKNLl', label: 'Gigi', accent: 'Energetic American female' },
  { id: 'oWAxZDx7w5VEj9dCyTzz', label: 'Grace', accent: 'Warm Southern American female' },
];

const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  stability: 0.5,
  similarity_boost: 0.75,
  style: 0.0,
  speaker_boost: true,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 14px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-md)',
  color: 'var(--text-primary)',
  fontSize: '14px',
  outline: 'none',
};

const sectionLabel: React.CSSProperties = {
  display: 'block',
  marginBottom: '8px',
  fontSize: '13px',
  fontWeight: 500,
  color: 'var(--text-primary)',
};

const cardStyle: React.CSSProperties = {
  padding: '16px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-md)',
};

const cardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: '12px',
};

const cardTitleStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  color: 'var(--text-tertiary)',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
};

const editLinkStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--accent)',
  fontSize: '12px',
  fontWeight: 500,
  cursor: 'pointer',
  padding: '0',
};

export function CustomAgentBuilder({ onSubmit, onCancel, isLoading = false }: CustomAgentBuilderProps) {
  const [step, setStep] = useState<Step>(0);
  const [formData, setFormData] = useState<AgentFormData>({
    name: '',
    role: '',
    model: 'gpt-4o-mini',
    model_category: 'auto',
    system_prompt: '',
    services: [],
    temperature: 0.7,
    description: '',
    voice_id: '',
    voice_settings: { ...DEFAULT_VOICE_SETTINGS },
  });
  const [avatarColor, setAvatarColor] = useState(AVATAR_COLORS[0]);
  const [modelMode, setModelMode] = useState<'auto' | 'manual'>('auto');
  const [resolvedModel, setResolvedModel] = useState<{ model_name: string; model_id: string; provider: string } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Provider/model catalog state
  const [catalogProviders, setCatalogProviders] = useState<string[]>([]);
  const [catalogModels, setCatalogModels] = useState<CatalogModel[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [catalogLoaded, setCatalogLoaded] = useState(false);

  const updateField = <K extends keyof AgentFormData>(field: K, value: AgentFormData[K]) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors((prev) => { const next = { ...prev }; delete next[field]; return next; });
    }
  };

  useEffect(() => {
    if (modelMode !== 'auto' || !formData.model_category) return;
    let cancelled = false;
    fetch(`/api/agents/resolve-model?role=${formData.model_category}&complexity=5`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (!cancelled && data) setResolvedModel({ model_name: data.model_name, model_id: data.model_id, provider: data.provider }); })
      .catch(() => { if (!cancelled) setResolvedModel(null); });
    return () => { cancelled = true; };
  }, [modelMode, formData.model_category]);

  // Fetch provider/model catalog from settings API
  useEffect(() => {
    if (modelMode !== 'manual') return;
    if (catalogLoaded) return;

    let cancelled = false;
    fetch(`/api/settings/models/catalog`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data?.providers) return;
        const providers: string[] = [];
        const models: CatalogModel[] = [];
        for (const [providerName, providerModels] of Object.entries(data.providers)) {
          providers.push(providerName);
          for (const m of providerModels as any[]) {
            models.push({ id: m.id, name: m.name, provider: providerName });
          }
        }
        setCatalogProviders(providers);
        setCatalogModels(models);
        setCatalogLoaded(true);
        if (!selectedProvider) {
          const currentModel = models.find((m) => m.id === formData.model);
          setSelectedProvider(currentModel?.provider || providers[0] || '');
        }
      })
      .catch(() => {
        if (cancelled) return;
        const providers = Array.from(new Set(FALLBACK_MODELS.map((m) => m.provider)));
        const models = FALLBACK_MODELS.map((m) => ({ id: m.value, name: m.label, provider: m.provider }));
        setCatalogProviders(providers);
        setCatalogModels(models);
        setCatalogLoaded(true);
        if (!selectedProvider) {
          const currentModel = models.find((m) => m.id === formData.model);
          setSelectedProvider(currentModel?.provider || providers[0] || '');
        }
      });
    return () => { cancelled = true; };
  }, [modelMode, catalogLoaded, formData.model, selectedProvider]);

  const handleModelModeChange = (mode: 'auto' | 'manual') => {
    setModelMode(mode);
    updateField('model_category', mode === 'auto' ? 'auto' : null);
  };

  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider);
    const providerModels = catalogModels.filter((m) => m.provider === provider);
    if (providerModels.length > 0 && !providerModels.some((m) => m.id === formData.model)) {
      updateField('model', providerModels[0].id);
    }
  };

  // Models filtered by selected provider
  const filteredModels = selectedProvider
    ? catalogModels.filter((m) => m.provider === selectedProvider)
    : catalogModels;

  const handleVoiceNameTranscript = useCallback((transcript: string) => {
    setFormData((prev) => ({ ...prev, name: prev.name ? `${prev.name} ${transcript}` : transcript }));
  }, []);

  const handleVoicePromptTranscript = useCallback((transcript: string) => {
    setFormData((prev) => ({ ...prev, system_prompt: prev.system_prompt ? `${prev.system_prompt} ${transcript}` : transcript }));
  }, []);


  const validateStep = (s: Step): boolean => {
    const errs: Record<string, string> = {};
    if (s === 0) {
      if (!formData.role.trim()) errs.role = 'Role is required';
      if (!formData.name.trim()) errs.name = 'Name is required';
      else if (formData.name.length > 50) errs.name = 'Name must be under 50 characters';
    }
    if (s === 1 && modelMode === 'manual' && !formData.model) errs.model = 'Model is required';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const goTo = (target: Step) => setStep(target);
  const handleNext = () => { if (validateStep(step) && step < 3) goTo((step + 1) as Step); };
  const handleBack = () => { if (step > 0) goTo((step - 1) as Step); };
  const handleCreate = async () => {
    if (!validateStep(0) || !validateStep(1)) return;
    await onSubmit(formData);
  };

  // Derived values for review step
  const modelLabel = modelMode === 'auto'
    ? 'Auto (SmartRouter)'
    : catalogModels.find((m) => m.id === formData.model)?.name || formData.model;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '520px' }}>
      {/* Header */}
      <div style={{ textAlign: 'center', marginBottom: '28px' }}>
        <h2 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: 600 }}>Create Custom Agent</h2>
        <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
          Build your agent step by step
        </p>
      </div>

      {/* Step indicator */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '32px', padding: '0 40px' }}>
        {STEPS.map((label, i) => {
          const isComplete = i < step;
          const isCurrent = i === step;
          const dotColor = isComplete ? 'var(--success)' : isCurrent ? 'var(--accent)' : 'var(--bg-tertiary)';
          const borderColor = isComplete ? 'var(--success)' : isCurrent ? 'var(--accent)' : 'var(--border-medium)';
          return (
            <React.Fragment key={label}>
              {i > 0 && (
                <div style={{
                  flex: 1,
                  height: '2px',
                  backgroundColor: isComplete ? 'var(--success)' : 'var(--border-light)',
                  transition: 'background-color 0.3s',
                  margin: '0 -4px',
                  marginBottom: '20px',
                }} />
              )}
              <button
                type="button"
                onClick={() => { if (i < step) goTo(i as Step); }}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '6px',
                  background: 'none',
                  border: 'none',
                  cursor: i < step ? 'pointer' : 'default',
                  padding: '0 8px',
                  minWidth: '72px',
                }}
              >
                <div style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  backgroundColor: dotColor,
                  color: isComplete || isCurrent ? 'white' : 'var(--text-tertiary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '13px',
                  fontWeight: 600,
                  border: `2px solid ${borderColor}`,
                  transition: 'all 0.3s',
                }}>
                  {isComplete ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : i + 1}
                </div>
                <span style={{
                  fontSize: '11px',
                  fontWeight: isCurrent ? 600 : 400,
                  color: isCurrent ? 'var(--text-primary)' : 'var(--text-tertiary)',
                  whiteSpace: 'nowrap',
                }}>
                  {label}
                </span>
              </button>
            </React.Fragment>
          );
        })}
      </div>

      {/* Step content — all rendered inline to preserve input focus */}
      <div style={{ flex: 1 }}>

        {/* ─── Step 0: Identity ─── */}
        {step === 0 && (
          <div>
            <div style={{ display: 'flex', gap: '32px', marginBottom: '24px' }}>
              {/* Left: avatar preview */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', minWidth: '120px' }}>
                <AgentAvatar name={formData.name || '?'} color={avatarColor} size="lg" />
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '6px' }}>
                  {AVATAR_COLORS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setAvatarColor(c)}
                      style={{
                        width: '20px',
                        height: '20px',
                        borderRadius: '50%',
                        backgroundColor: c,
                        border: avatarColor === c ? '2px solid white' : '2px solid transparent',
                        outline: avatarColor === c ? `2px solid ${c}` : 'none',
                        cursor: 'pointer',
                        padding: 0,
                      }}
                    />
                  ))}
                </div>
              </div>

              {/* Right: role + name + description */}
              <div style={{ flex: 1 }}>
                {/* Role + Name side by side */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '16px' }}>
                  <div>
                    <label style={sectionLabel}>
                      Role <span style={{ color: 'var(--error)' }}>*</span>
                    </label>
                    <input
                      type="text"
                      value={formData.role}
                      onChange={(e) => updateField('role', e.target.value)}
                      placeholder="Art Director"
                      disabled={isLoading}
                      autoFocus
                      style={{ ...inputStyle, border: `1px solid ${errors.role ? 'var(--error)' : 'var(--border-light)'}` }}
                    />
                    {errors.role && <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--error)' }}>{errors.role}</div>}
                  </div>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <label style={sectionLabel}>
                        Name <span style={{ color: 'var(--error)' }}>*</span>
                      </label>
                      <VoiceInput onTranscript={handleVoiceNameTranscript} disabled={isLoading} size="small" />
                    </div>
                    <input
                      type="text"
                      value={formData.name}
                      onChange={(e) => updateField('name', e.target.value)}
                      placeholder="Sam"
                      disabled={isLoading}
                      style={{ ...inputStyle, border: `1px solid ${errors.name ? 'var(--error)' : 'var(--border-light)'}` }}
                    />
                    {errors.name && <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--error)' }}>{errors.name}</div>}
                  </div>
                </div>

                {/* Short Description */}
                <div>
                  <label style={sectionLabel}>
                    Short Description <span style={{ fontSize: '11px', fontWeight: 400, color: 'var(--text-tertiary)' }}>shown on resource card</span>
                  </label>
                  <input
                    type="text"
                    value={formData.description || ''}
                    onChange={(e) => updateField('description', e.target.value)}
                    placeholder="Oversees visual identity and creative direction..."
                    disabled={isLoading}
                    style={inputStyle}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ─── Step 1: Intelligence ─── */}
        {step === 1 && (
          <div>
            {/* Model — full width */}
            <div style={{ marginBottom: '24px' }}>
              <label style={sectionLabel}>
                Model <span style={{ color: 'var(--error)' }}>*</span>
              </label>
              {/* Auto/Manual toggle — full width 50/50 */}
              <div style={{ display: 'flex', marginBottom: '10px' }}>
                <button
                  type="button"
                  onClick={() => handleModelModeChange('auto')}
                  style={{
                    flex: 1,
                    padding: '10px 16px',
                    fontSize: '13px',
                    fontWeight: 500,
                    backgroundColor: modelMode === 'auto' ? 'var(--accent)' : 'var(--bg-secondary)',
                    color: modelMode === 'auto' ? 'white' : 'var(--text-secondary)',
                    border: `1px solid ${modelMode === 'auto' ? 'var(--accent)' : 'var(--border-light)'}`,
                    borderRadius: 'var(--radius-md) 0 0 var(--radius-md)',
                    cursor: 'pointer',
                  }}
                >
                  Auto (SmartRouter)
                </button>
                <button
                  type="button"
                  onClick={() => handleModelModeChange('manual')}
                  style={{
                    flex: 1,
                    padding: '10px 16px',
                    fontSize: '13px',
                    fontWeight: 500,
                    backgroundColor: modelMode === 'manual' ? 'var(--accent)' : 'var(--bg-secondary)',
                    color: modelMode === 'manual' ? 'white' : 'var(--text-secondary)',
                    border: `1px solid ${modelMode === 'manual' ? 'var(--accent)' : 'var(--border-light)'}`,
                    borderRadius: '0 var(--radius-md) var(--radius-md) 0',
                    cursor: 'pointer',
                  }}
                >
                  Select an LLM
                </button>
              </div>

              {modelMode === 'auto' ? (
                <div>
                  {resolvedModel && (
                    <div style={{
                      marginTop: '2px',
                      padding: '8px 12px',
                      backgroundColor: 'rgba(99, 102, 241, 0.06)',
                      border: '1px solid rgba(99, 102, 241, 0.15)',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '12px',
                      color: 'var(--text-secondary)',
                    }}>
                      Current: <strong>{resolvedModel.model_name}</strong> ({resolvedModel.provider})
                    </div>
                  )}
                  <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                    Model is selected dynamically based on prompt context &mdash; the SmartRouter allocates the best model according to task complexity and cost optimization.
                  </div>
                </div>
              ) : (
                <div>
                  {/* Provider + Model — two dropdowns full width */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                    <div>
                      <label style={{ display: 'block', marginBottom: '4px', fontSize: '11px', fontWeight: 500, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                        Provider
                      </label>
                      <select
                        value={selectedProvider}
                        onChange={(e) => handleProviderChange(e.target.value)}
                        disabled={isLoading}
                        style={inputStyle}
                      >
                        {catalogProviders.length === 0 && (
                          <option value="">Loading...</option>
                        )}
                        {catalogProviders.map((p) => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label style={{ display: 'block', marginBottom: '4px', fontSize: '11px', fontWeight: 500, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                        Model
                      </label>
                      <select
                        value={formData.model}
                        onChange={(e) => updateField('model', e.target.value)}
                        disabled={isLoading || filteredModels.length === 0}
                        style={{ ...inputStyle, border: `1px solid ${errors.model ? 'var(--error)' : 'var(--border-light)'}` }}
                      >
                        {filteredModels.length === 0 && (
                          <option value="">No models available</option>
                        )}
                        {filteredModels.map((m) => (
                          <option key={m.id} value={m.id}>{m.name}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    Showing models from configured providers. Add more on the <strong>Settings</strong> page.
                  </div>
                </div>
              )}
              {errors.model && <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--error)' }}>{errors.model}</div>}
            </div>

            {/* Temperature — full width */}
            <div style={{ marginBottom: '24px' }}>
              <label style={sectionLabel}>Temperature</label>
              <TemperatureSlider
                value={formData.temperature}
                onChange={(temp) => updateField('temperature', temp)}
                disabled={isLoading}
              />
            </div>

            {/* System Prompt — full width */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <label style={sectionLabel}>System Prompt</label>
                <VoiceInput onTranscript={handleVoicePromptTranscript} disabled={isLoading} size="small" />
              </div>
              <textarea
                value={formData.system_prompt}
                onChange={(e) => updateField('system_prompt', e.target.value)}
                placeholder="You are a helpful assistant that..."
                disabled={isLoading}
                rows={4}
                style={{
                  ...inputStyle,
                  lineHeight: 1.5,
                  resize: 'vertical',
                }}
              />
              <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
                Optional instructions that define the agent&apos;s personality and behavior
              </div>
            </div>

          </div>
        )}

        {/* ─── Step 2: Voice ─── */}
        {step === 2 && (
          <div>
            {/* Voice Selection */}
            <div style={{ marginBottom: '24px' }}>
              <label style={sectionLabel}>ElevenLabs Voice</label>
              <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '10px' }}>
                Give your agent a voice. Requires ElevenLabs to be connected in <strong>Tools</strong>.
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                {ELEVENLABS_VOICES.map((voice) => {
                  const isSelected = formData.voice_id === voice.id;
                  return (
                    <button
                      key={voice.id}
                      type="button"
                      onClick={() => updateField('voice_id', voice.id)}
                      disabled={isLoading}
                      style={{
                        padding: '12px 14px',
                        backgroundColor: isSelected ? 'rgba(99, 102, 241, 0.08)' : 'var(--bg-secondary)',
                        border: `1.5px solid ${isSelected ? 'var(--accent)' : 'var(--border-light)'}`,
                        borderRadius: 'var(--radius-md)',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'border-color 0.15s',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {isSelected && (
                          <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--accent)', flexShrink: 0 }} />
                        )}
                        <div>
                          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                            {voice.label}
                          </div>
                          {voice.accent && (
                            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '1px' }}>
                              {voice.accent}
                            </div>
                          )}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Personality Settings — only show when a voice is selected */}
            {formData.voice_id && (
              <div style={{ padding: '20px', backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '16px' }}>
                  Voice Personality
                </div>

                {/* Stability */}
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Stability</label>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{(formData.voice_settings?.stability ?? DEFAULT_VOICE_SETTINGS.stability).toFixed(2)}</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={formData.voice_settings?.stability ?? DEFAULT_VOICE_SETTINGS.stability}
                    onChange={(e) => updateField('voice_settings', { ...formData.voice_settings!, stability: parseFloat(e.target.value) })}
                    style={{ width: '100%', accentColor: 'var(--accent)' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--text-tertiary)' }}>
                    <span>More variable</span><span>More stable</span>
                  </div>
                </div>

                {/* Similarity Boost */}
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Clarity + Similarity</label>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{(formData.voice_settings?.similarity_boost ?? DEFAULT_VOICE_SETTINGS.similarity_boost).toFixed(2)}</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={formData.voice_settings?.similarity_boost ?? DEFAULT_VOICE_SETTINGS.similarity_boost}
                    onChange={(e) => updateField('voice_settings', { ...formData.voice_settings!, similarity_boost: parseFloat(e.target.value) })}
                    style={{ width: '100%', accentColor: 'var(--accent)' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--text-tertiary)' }}>
                    <span>More creative</span><span>More faithful</span>
                  </div>
                </div>

                {/* Style Exaggeration */}
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Style Exaggeration</label>
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{(formData.voice_settings?.style ?? DEFAULT_VOICE_SETTINGS.style).toFixed(2)}</span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={formData.voice_settings?.style ?? DEFAULT_VOICE_SETTINGS.style}
                    onChange={(e) => updateField('voice_settings', { ...formData.voice_settings!, style: parseFloat(e.target.value) })}
                    style={{ width: '100%', accentColor: 'var(--accent)' }}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--text-tertiary)' }}>
                    <span>Neutral</span><span>Exaggerated</span>
                  </div>
                </div>

                {/* Speaker Boost */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div>
                    <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Speaker Boost</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Enhances voice clarity at the cost of latency</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => updateField('voice_settings', { ...formData.voice_settings!, speaker_boost: !formData.voice_settings?.speaker_boost })}
                    style={{
                      width: '40px',
                      height: '22px',
                      borderRadius: '11px',
                      border: 'none',
                      backgroundColor: formData.voice_settings?.speaker_boost ? 'var(--accent)' : 'var(--bg-tertiary)',
                      cursor: 'pointer',
                      position: 'relative',
                      transition: 'background-color 0.2s',
                    }}
                  >
                    <div style={{
                      width: '16px',
                      height: '16px',
                      borderRadius: '50%',
                      backgroundColor: 'white',
                      position: 'absolute',
                      top: '3px',
                      left: formData.voice_settings?.speaker_boost ? '21px' : '3px',
                      transition: 'left 0.2s',
                      boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                    }} />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ─── Step 3: Review ─── */}
        {step === 3 && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            {/* Identity */}
            <div style={cardStyle}>
              <div style={cardHeaderStyle}>
                <span style={cardTitleStyle}>Identity</span>
                <button type="button" onClick={() => goTo(0)} style={editLinkStyle}>Edit</button>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <AgentAvatar name={formData.name || '?'} color={avatarColor} size="md" />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '14px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{formData.name || '(unnamed)'}</div>
                  <div style={{ fontSize: '12px', color: 'var(--accent)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{formData.role || '(no role)'}</div>
                  {formData.description && <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{formData.description}</div>}
                </div>
              </div>
            </div>

            {/* Model */}
            <div style={cardStyle}>
              <div style={cardHeaderStyle}>
                <span style={cardTitleStyle}>Model</span>
                <button type="button" onClick={() => goTo(1)} style={editLinkStyle}>Edit</button>
              </div>
              <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>{modelLabel}</div>
              <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                Temperature: {formData.temperature.toFixed(1)}
              </div>
            </div>

            {/* Voice */}
            <div style={cardStyle}>
              <div style={cardHeaderStyle}>
                <span style={cardTitleStyle}>Voice</span>
                <button type="button" onClick={() => goTo(2)} style={editLinkStyle}>Edit</button>
              </div>
              {formData.voice_id ? (
                <div>
                  <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>
                    {ELEVENLABS_VOICES.find((v) => v.id === formData.voice_id)?.label || 'Custom'}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    {ELEVENLABS_VOICES.find((v) => v.id === formData.voice_id)?.accent}
                  </div>
                </div>
              ) : (
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>No voice assigned</span>
              )}
            </div>

            {/* System Prompt */}
            <div style={cardStyle}>
              <div style={cardHeaderStyle}>
                <span style={cardTitleStyle}>System Prompt</span>
                <button type="button" onClick={() => goTo(1)} style={editLinkStyle}>Edit</button>
              </div>
              {formData.system_prompt ? (
                <div style={{
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                  lineHeight: 1.5,
                  maxHeight: '72px',
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                }}>
                  {formData.system_prompt}
                </div>
              ) : (
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>No system prompt</span>
              )}
            </div>

          </div>
        )}
      </div>

      {/* Navigation */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        paddingTop: '20px',
        marginTop: '24px',
        borderTop: '1px solid var(--border-light)',
      }}>
        <button
          type="button"
          onClick={step === 0 ? onCancel : handleBack}
          disabled={isLoading}
          style={{
            padding: '10px 20px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            fontWeight: 500,
            cursor: 'pointer',
            opacity: isLoading ? 0.5 : 1,
          }}
        >
          {step === 0 ? 'Cancel' : 'Back'}
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {step > 0 && step < 3 && (
            <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
              Step {step + 1} of {STEPS.length}
            </span>
          )}
          {step < 3 ? (
            <button
              type="button"
              onClick={handleNext}
              disabled={isLoading}
              style={{
                padding: '10px 28px',
                backgroundColor: 'var(--accent)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                color: 'white',
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
                opacity: isLoading ? 0.7 : 1,
              }}
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              onClick={handleCreate}
              disabled={isLoading}
              style={{
                padding: '10px 28px',
                backgroundColor: 'var(--success)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                color: 'white',
                fontSize: '14px',
                fontWeight: 600,
                cursor: 'pointer',
                opacity: isLoading ? 0.7 : 1,
              }}
            >
              {isLoading ? 'Creating...' : 'Create Agent'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default CustomAgentBuilder;
