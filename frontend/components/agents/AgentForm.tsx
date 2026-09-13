'use client';

import React, { useState, useCallback, useEffect } from 'react';
import { toast } from 'sonner';
import { VoiceInput } from '@/components/shared/VoiceInput';
import { useConfirm } from '@/hooks/useConfirm';

const ELEVENLABS_VOICES: { id: string; label: string; accent: string; personality: VoiceSettings }[] = [
  { id: '', label: 'None (No voice)', accent: '', personality: { stability: 0.5, similarity_boost: 0.75, style: 0.0, speaker_boost: true } },
  { id: 'pNInz6obpgDQGcFmaJgB', label: 'Adam', accent: 'Deep, warm American male', personality: { stability: 0.7, similarity_boost: 0.8, style: 0.1, speaker_boost: true } },
  { id: '21m00Tcm4TlvDq8ikWAM', label: 'Rachel', accent: 'Calm, professional American female', personality: { stability: 0.75, similarity_boost: 0.85, style: 0.05, speaker_boost: true } },
  { id: 'EXAVITQu4vr4xnSDxMaL', label: 'Bella', accent: 'Warm, friendly American female', personality: { stability: 0.55, similarity_boost: 0.75, style: 0.2, speaker_boost: true } },
  { id: 'ErXwobaYiN019PkySvjV', label: 'Antoni', accent: 'Well-rounded American male', personality: { stability: 0.6, similarity_boost: 0.75, style: 0.15, speaker_boost: true } },
  { id: 'MF3mGyEYCl7XYWbV9V6O', label: 'Elli', accent: 'Young, enthusiastic American female', personality: { stability: 0.4, similarity_boost: 0.7, style: 0.35, speaker_boost: true } },
  { id: 'TxGEqnHWrfWFTfGW9XjX', label: 'Josh', accent: 'Dynamic young American male', personality: { stability: 0.45, similarity_boost: 0.75, style: 0.3, speaker_boost: true } },
  { id: 'VR6AewLTigWG4xSOukaG', label: 'Arnold', accent: 'Confident, deep American male', personality: { stability: 0.65, similarity_boost: 0.8, style: 0.25, speaker_boost: true } },
  { id: 'nPczCjzI2devNBz1zQrb', label: 'Brian', accent: 'Deep, narration American male', personality: { stability: 0.8, similarity_boost: 0.85, style: 0.1, speaker_boost: true } },
  { id: 'onwK4e9ZLuTAKqWW03F9', label: 'Daniel', accent: 'Authoritative British male', personality: { stability: 0.75, similarity_boost: 0.9, style: 0.15, speaker_boost: true } },
  { id: 'XB0fDUnXU5powFXDhCwa', label: 'Charlotte', accent: 'Elegant British female', personality: { stability: 0.7, similarity_boost: 0.85, style: 0.2, speaker_boost: true } },
  { id: 'jBpfuIE2acCO8z3wKNLl', label: 'Gigi', accent: 'Energetic American female', personality: { stability: 0.35, similarity_boost: 0.7, style: 0.45, speaker_boost: true } },
  { id: 'oWAxZDx7w5VEj9dCyTzz', label: 'Grace', accent: 'Warm Southern American female', personality: { stability: 0.6, similarity_boost: 0.8, style: 0.2, speaker_boost: true } },
];

const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  stability: 0.5,
  similarity_boost: 0.75,
  style: 0.0,
  speaker_boost: true,
};

export interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  speaker_boost: boolean;
}

export interface AgentFormData {
  name: string;
  role: string;
  model: string;
  model_category: string | null; // "architect"|"reviewer"|"researcher"|"coding" or null for manual
  category?: string | null; // template category: leadership, creative, design, development, quality, operations
  platform_tag?: string | null; // VOS3 vertical (e.g. "industrial-automation") set on install from a marketplace vertical
  system_prompt: string;
  services: string[];
  temperature: number;
  template_id?: string;
  description?: string;
  voice_id?: string;
  voice_settings?: VoiceSettings;
}

interface AgentFormProps {
  initialData?: Partial<AgentFormData>;
  onSubmit: (data: AgentFormData) => Promise<void>;
  onSave?: (data: AgentFormData) => Promise<void>;
  onCancel: () => void;
  isLoading?: boolean;
  title?: string;
  templateName?: string;
  submitLabel?: string;
}

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

const selectStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 14px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-light)',
  borderRadius: 'var(--radius-md)',
  color: 'var(--text-primary)',
  fontSize: '14px',
  outline: 'none',
};

/** Derive provider name from a model ID string */
function deriveProvider(model: string): string {
  if (model.startsWith('gpt') || model.startsWith('o1') || model.startsWith('o3')) return 'OpenAI';
  if (model.startsWith('claude')) return 'Anthropic';
  if (model.startsWith('gemini')) return 'Google';
  if (model.includes('llama') || model.includes(':')) return 'Ollama';
  return 'Auto';
}

const DEFAULT_FORM_DATA: AgentFormData = {
  name: '',
  role: 'assistant',
  model: 'gpt-4o-mini',
  model_category: 'auto',
  system_prompt: '',
  services: [],
  temperature: 0.7,
};

/** Reusable slider styled like TemperatureSlider — colored progress fill + category badge */
function PersonalitySlider({
  label,
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.05,
  disabled = false,
  leftLabel,
  rightLabel,
  getCategory,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  leftLabel: string;
  rightLabel: string;
  getCategory: (v: number) => { label: string; color: string };
}) {
  const pct = ((value - min) / (max - min)) * 100;
  const cat = getCategory(value);

  return (
    <div style={{ marginBottom: '20px' }}>
      {/* Header: label + category badge + value */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>{label}</span>
          <span
            style={{
              fontSize: '11px',
              fontWeight: 500,
              color: cat.color,
              padding: '1px 7px',
              backgroundColor: `${cat.color}15`,
              borderRadius: 'var(--radius-full)',
            }}
          >
            {cat.label}
          </span>
        </div>
        <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
          {value.toFixed(max >= 2 ? 1 : 2)}
        </span>
      </div>

      {/* Slider with progress fill */}
      <div style={{ position: 'relative', marginBottom: '6px' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          disabled={disabled}
          style={{
            width: '100%',
            height: '6px',
            appearance: 'none',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: '3px',
            outline: 'none',
            cursor: disabled ? 'not-allowed' : 'pointer',
            opacity: disabled ? 0.5 : 1,
          }}
        />
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '6px',
            width: `${pct}%`,
            background: `linear-gradient(90deg, var(--success) 0%, var(--accent) 50%, var(--warning) 100%)`,
            borderRadius: '3px',
            pointerEvents: 'none',
          }}
        />
      </div>

      {/* Left / Right labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-tertiary)' }}>
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  );
}

export function AgentForm({
  initialData,
  onSubmit,
  onCancel,
  isLoading = false,
  title = 'Create Agent',
  templateName,
  submitLabel,
  onSave,
}: AgentFormProps) {
  const [formData, setFormData] = useState<AgentFormData>({
    ...DEFAULT_FORM_DATA,
    ...initialData,
  });
  const [errors, setErrors] = useState<Partial<Record<keyof AgentFormData, string>>>({});
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();
  const [modelMode, setModelMode] = useState<'auto' | 'manual'>(
    initialData?.model_category !== undefined
      ? (initialData.model_category ? 'auto' : 'manual')
      : 'auto'
  );
  const [resolvedModel, setResolvedModel] = useState<{ model_name: string; model_id: string; provider: string } | null>(null);
  const [showAdvancedPersonality, setShowAdvancedPersonality] = useState(false);

  // Provider/model catalog state
  const [catalogProviders, setCatalogProviders] = useState<string[]>([]);
  const [catalogModels, setCatalogModels] = useState<CatalogModel[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [catalogLoaded, setCatalogLoaded] = useState(false);

  const updateField = <K extends keyof AgentFormData>(field: K, value: AgentFormData[K]) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors((prev) => {
        const next = { ...prev };
        delete next[field];
        return next;
      });
    }
  };

  const handleVoiceNameTranscript = useCallback((transcript: string) => {
    setFormData((prev) => ({ ...prev, name: prev.name ? `${prev.name} ${transcript}` : transcript }));
  }, []);

  const handleVoicePromptTranscript = useCallback((transcript: string) => {
    setFormData((prev) => ({
      ...prev,
      system_prompt: prev.system_prompt ? `${prev.system_prompt} ${transcript}` : transcript,
    }));
  }, []);


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
        // Auto-select provider from current model
        if (!selectedProvider) {
          const currentModel = models.find((m) => m.id === formData.model);
          setSelectedProvider(currentModel?.provider || providers[0] || '');
        }
      })
      .catch(() => {
        if (cancelled) return;
        // Fallback: derive providers from hardcoded list
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

  // Resolve model from SmartRouter when in auto mode
  useEffect(() => {
    if (modelMode !== 'auto' || !formData.model_category) return;
    let cancelled = false;
    fetch(`/api/agents/resolve-model?role=${formData.model_category}&complexity=5`)
      .then((res) => res.ok ? res.json() : null)
      .then((data) => {
        if (!cancelled && data) {
          setResolvedModel({ model_name: data.model_name, model_id: data.model_id, provider: data.provider });
        }
      })
      .catch(() => {
        if (!cancelled) setResolvedModel(null);
      });
    return () => { cancelled = true; };
  }, [modelMode, formData.model_category]);

  const handleModelModeChange = (mode: 'auto' | 'manual') => {
    setModelMode(mode);
    updateField('model_category', mode === 'auto' ? 'auto' : null);
  };

  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider);
    // Auto-select first model of the new provider
    const providerModels = catalogModels.filter((m) => m.provider === provider);
    if (providerModels.length > 0 && !providerModels.some((m) => m.id === formData.model)) {
      updateField('model', providerModels[0].id);
    }
  };

  // Models filtered by selected provider
  const filteredModels = selectedProvider
    ? catalogModels.filter((m) => m.provider === selectedProvider)
    : catalogModels;

  const validate = (): boolean => {
    const newErrors: Partial<Record<keyof AgentFormData, string>> = {};

    if (!formData.name.trim()) {
      newErrors.name = 'Name is required';
    } else if (formData.name.length > 50) {
      newErrors.name = 'Name must be less than 50 characters';
    }

    if (modelMode === 'manual' && !formData.model) {
      newErrors.model = 'Model is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validate()) return;

    try {
      await onSubmit(formData);
    } catch {
      // Error handling is done by the parent
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: 600 }}>{title}</h2>
        <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
          Configure your AI agent&apos;s behavior and capabilities
        </p>
      </div>

      {/* Name Field */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <label style={{ fontSize: '13px', fontWeight: 500 }}>
            Name <span style={{ color: 'var(--error)' }}>*</span>
          </label>
          <VoiceInput onTranscript={handleVoiceNameTranscript} disabled={isLoading} size="small" />
        </div>
        <input
          type="text"
          value={formData.name}
          onChange={(e) => updateField('name', e.target.value)}
          placeholder="My Custom Agent"
          disabled={isLoading}
          style={{
            width: '100%',
            padding: '10px 14px',
            backgroundColor: 'var(--bg-secondary)',
            border: `1px solid ${errors.name ? 'var(--error)' : 'var(--border-light)'}`,
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            outline: 'none',
          }}
        />
        {errors.name && (
          <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--error)' }}>{errors.name}</div>
        )}
      </div>

      {/* Role Field */}
      <div style={{ marginBottom: '20px' }}>
        <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: 500 }}>
          Role <span style={{ color: 'var(--error)' }}>*</span>
        </label>
        <input
          type="text"
          value={formData.role}
          onChange={(e) => updateField('role', e.target.value)}
          placeholder="assistant"
          disabled={isLoading}
          style={{
            width: '100%',
            padding: '10px 14px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            outline: 'none',
          }}
        />
        {templateName && (
          <div style={{ marginTop: '6px', fontSize: '12px', color: '#6366F1' }}>
            Based on: {templateName}
          </div>
        )}
      </div>

      {/* Model — full width */}
      <div style={{ marginBottom: '20px' }}>
        <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: 500 }}>
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
            Auto (Smart Router)
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

        {/* Provider + Model — two-column grid, same layout for both modes */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
          {/* Provider */}
          {modelMode === 'auto' ? (
            <div style={{
              ...selectStyle,
              backgroundColor: 'rgba(99, 102, 241, 0.06)',
              border: '1px solid rgba(99, 102, 241, 0.15)',
            }}>
              <span style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>Provider: </span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{resolvedModel?.provider || deriveProvider(formData.model)}</span>
            </div>
          ) : (
            <select
              value={selectedProvider}
              onChange={(e) => handleProviderChange(e.target.value)}
              disabled={isLoading}
              style={selectStyle}
            >
              {catalogProviders.length === 0 && (
                <option value="">Loading...</option>
              )}
              {catalogProviders.map((p) => (
                <option key={p} value={p}>Provider: {p}</option>
              ))}
            </select>
          )}
          {/* Current Model */}
          {modelMode === 'auto' ? (
            <div style={{
              ...selectStyle,
              backgroundColor: 'rgba(99, 102, 241, 0.06)',
              border: '1px solid rgba(99, 102, 241, 0.15)',
            }}>
              <span style={{ color: 'var(--text-tertiary)', fontSize: '12px' }}>Current: </span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{resolvedModel?.model_name || formData.model}</span>
            </div>
          ) : (
            <select
              value={formData.model}
              onChange={(e) => updateField('model', e.target.value)}
              disabled={isLoading || filteredModels.length === 0}
              style={{ ...selectStyle, border: `1px solid ${errors.model ? 'var(--error)' : 'var(--border-light)'}` }}
            >
              {filteredModels.length === 0 && (
                <option value="">No models available</option>
              )}
              {filteredModels.map((m) => (
                <option key={m.id} value={m.id}>Current: {m.name}</option>
              ))}
            </select>
          )}
        </div>
        {errors.model && (
          <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--error)' }}>{errors.model}</div>
        )}
      </div>

      {/* System Prompt */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <label style={{ fontSize: '13px', fontWeight: 500 }}>System Prompt</label>
          <VoiceInput onTranscript={handleVoicePromptTranscript} disabled={isLoading} size="small" />
        </div>
        <textarea
          value={formData.system_prompt}
          onChange={(e) => updateField('system_prompt', e.target.value)}
          placeholder="You are a helpful assistant that..."
          disabled={isLoading}
          rows={templateName ? 8 : 4}
          style={{
            width: '100%',
            padding: '10px 14px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            lineHeight: 1.5,
            resize: 'vertical',
            outline: 'none',
          }}
        />
        <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
          Optional instructions that define the agent&apos;s personality and behavior
        </div>
      </div>


      {/* Personality */}
      <div style={{ marginBottom: '24px' }}>
        <label style={{ display: 'block', marginBottom: '4px', fontSize: '13px', fontWeight: 500 }}>Personality</label>
        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginBottom: '12px' }}>
          Tune how your agent thinks and speaks.
        </div>

        {/* Voice Dropdown */}
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', marginBottom: '6px', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Voice</label>
          <select
            value={formData.voice_id || ''}
            onChange={(e) => {
              const voiceId = e.target.value;
              updateField('voice_id', voiceId);
              const voice = ELEVENLABS_VOICES.find((v) => v.id === voiceId);
              if (voice) {
                updateField('voice_settings', voice.personality);
              }
            }}
            disabled={isLoading}
            style={selectStyle}
          >
            {ELEVENLABS_VOICES.map((voice) => (
              <option key={voice.id || '_none'} value={voice.id}>
                {voice.label}{voice.accent ? ` — ${voice.accent}` : ''}
              </option>
            ))}
          </select>
          <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
            Requires ElevenLabs to be connected in Tools
          </div>
        </div>

        {/* Advanced Personality Settings — collapsible */}
        <button
          type="button"
          onClick={() => setShowAdvancedPersonality((prev) => !prev)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '0',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 500,
            color: 'var(--text-tertiary)',
            marginBottom: showAdvancedPersonality ? '16px' : '0',
          }}
        >
          <svg
            width="12" height="12" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ transform: showAdvancedPersonality ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
          Advanced Personality Settings
        </button>

        {showAdvancedPersonality && (
          <div>
            {/* Temperature */}
            <PersonalitySlider
              label="Temperature"
              value={formData.temperature}
              onChange={(v) => updateField('temperature', v)}
              min={0} max={2} step={0.1}
              disabled={isLoading}
              leftLabel="Precise" rightLabel="Creative"
              getCategory={(v) => {
                if (v <= 0.3) return { label: 'Precise', color: 'var(--success)' };
                if (v <= 0.8) return { label: 'Balanced', color: 'var(--accent)' };
                if (v <= 1.2) return { label: 'Creative', color: 'var(--warning)' };
                return { label: 'Wild', color: 'var(--error)' };
              }}
            />

            {/* Voice personality sliders */}
            <PersonalitySlider
              label="Stability"
              value={formData.voice_settings?.stability ?? DEFAULT_VOICE_SETTINGS.stability}
              onChange={(v) => updateField('voice_settings', { ...(formData.voice_settings ?? DEFAULT_VOICE_SETTINGS), stability: v })}
              min={0} max={1} step={0.05}
              disabled={isLoading}
              leftLabel="Variable" rightLabel="Stable"
              getCategory={(v) => {
                if (v <= 0.3) return { label: 'Variable', color: 'var(--warning)' };
                if (v <= 0.7) return { label: 'Balanced', color: 'var(--accent)' };
                return { label: 'Stable', color: 'var(--success)' };
              }}
            />
            <PersonalitySlider
              label="Clarity + Similarity"
              value={formData.voice_settings?.similarity_boost ?? DEFAULT_VOICE_SETTINGS.similarity_boost}
              onChange={(v) => updateField('voice_settings', { ...(formData.voice_settings ?? DEFAULT_VOICE_SETTINGS), similarity_boost: v })}
              min={0} max={1} step={0.05}
              disabled={isLoading}
              leftLabel="Creative" rightLabel="Faithful"
              getCategory={(v) => {
                if (v <= 0.3) return { label: 'Creative', color: 'var(--warning)' };
                if (v <= 0.7) return { label: 'Balanced', color: 'var(--accent)' };
                return { label: 'Faithful', color: 'var(--success)' };
              }}
            />
            <PersonalitySlider
              label="Style Exaggeration"
              value={formData.voice_settings?.style ?? DEFAULT_VOICE_SETTINGS.style}
              onChange={(v) => updateField('voice_settings', { ...(formData.voice_settings ?? DEFAULT_VOICE_SETTINGS), style: v })}
              min={0} max={1} step={0.05}
              disabled={isLoading}
              leftLabel="Neutral" rightLabel="Exaggerated"
              getCategory={(v) => {
                if (v <= 0.3) return { label: 'Neutral', color: 'var(--success)' };
                if (v <= 0.7) return { label: 'Moderate', color: 'var(--accent)' };
                return { label: 'Exaggerated', color: 'var(--warning)' };
              }}
            />

            {/* Speaker Boost */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
              <div>
                <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>Speaker Boost</div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>Enhances voice clarity at the cost of latency</div>
              </div>
              <button
                type="button"
                onClick={() => updateField('voice_settings', { ...(formData.voice_settings ?? DEFAULT_VOICE_SETTINGS), speaker_boost: !(formData.voice_settings?.speaker_boost ?? DEFAULT_VOICE_SETTINGS.speaker_boost) })}
                style={{
                  width: '40px',
                  height: '22px',
                  borderRadius: '11px',
                  border: 'none',
                  backgroundColor: (formData.voice_settings?.speaker_boost ?? DEFAULT_VOICE_SETTINGS.speaker_boost) ? 'var(--accent)' : 'var(--bg-tertiary)',
                  cursor: 'pointer',
                  position: 'relative' as const,
                  transition: 'background-color 0.2s',
                }}
              >
                <div style={{
                  width: '16px',
                  height: '16px',
                  borderRadius: '50%',
                  backgroundColor: 'white',
                  position: 'absolute' as const,
                  top: '3px',
                  left: (formData.voice_settings?.speaker_boost ?? DEFAULT_VOICE_SETTINGS.speaker_boost) ? '21px' : '3px',
                  transition: 'left 0.2s',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                }} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', paddingTop: '16px', borderTop: '1px solid var(--border-light)' }}>
        {/* Import / Export — left aligned */}
        <div style={{ display: 'flex', gap: '8px', marginRight: 'auto' }}>
          <button
            type="button"
            onClick={() => {
              const input = document.createElement('input');
              input.type = 'file';
              input.accept = '.json,application/json';
              input.onchange = (e) => {
                const file = (e.target as HTMLInputElement).files?.[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = (ev) => {
                  try {
                    const data = JSON.parse(ev.target?.result as string);
                    // Apply all recognized fields from the strict format
                    if (data.name) updateField('name', data.name);
                    if (data.role) updateField('role', data.role);
                    if (data.model) updateField('model', data.model);
                    if (data.model_category !== undefined) updateField('model_category', data.model_category);
                    if (data.system_prompt !== undefined) updateField('system_prompt', data.system_prompt);
                    if (typeof data.temperature === 'number') updateField('temperature', data.temperature);
                    if (Array.isArray(data.services)) updateField('services', data.services);
                    if (data.template_id !== undefined) updateField('template_id', data.template_id);
                    if (data.description !== undefined) updateField('description', data.description);
                    if (data.voice_id !== undefined) updateField('voice_id', data.voice_id);
                    if (data.voice_settings) updateField('voice_settings', data.voice_settings);
                    // Switch model mode based on imported data
                    if (data.model_category) {
                      setModelMode('auto');
                    } else if (data.model_category === null) {
                      setModelMode('manual');
                    }
                  } catch {
                    toast.error('Invalid JSON file', {
                      description: 'Please use a file exported from this form.',
                    });
                  }
                };
                reader.readAsText(file);
              };
              input.click();
            }}
            style={{
              padding: '10px 14px',
              backgroundColor: 'transparent',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-secondary)',
              fontSize: '13px',
              fontWeight: 500,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            Import
          </button>
          <button
            type="button"
            onClick={() => {
              const exportData = {
                name: formData.name,
                role: formData.role,
                model: formData.model,
                model_category: formData.model_category,
                system_prompt: formData.system_prompt,
                temperature: formData.temperature,
                services: formData.services,
                template_id: formData.template_id || null,
                description: formData.description || null,
                voice_id: formData.voice_id || null,
                voice_settings: formData.voice_settings || null,
              };
              const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${(formData.name || 'agent').replace(/\s+/g, '-').toLowerCase()}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
            style={{
              padding: '10px 14px',
              backgroundColor: 'transparent',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-secondary)',
              fontSize: '13px',
              fontWeight: 500,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            Export
          </button>
        </div>
        {/* Cancel / Save / Hire — right aligned */}
        <button
          type="button"
          onClick={onCancel}
          disabled={isLoading}
          style={{
            padding: '10px 20px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            fontSize: '14px',
            fontWeight: 500,
            opacity: isLoading ? 0.5 : 1,
          }}
        >
          Cancel
        </button>
        {onSave && (
          <button
            type="button"
            disabled={isLoading}
            onClick={async () => {
              if (!validate()) return;
              const ok = await confirm({
                title: 'Save changes to this resource template?',
                description: 'Updates will be applied to the underlying template configuration.',
                confirmLabel: 'Save changes',
              });
              if (ok) onSave(formData);
            }}
            style={{
              padding: '10px 20px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '14px',
              fontWeight: 500,
              opacity: isLoading ? 0.7 : 1,
            }}
          >
            {isLoading ? 'Saving...' : (submitLabel || 'Save')}
          </button>
        )}
        <button
          type="submit"
          disabled={isLoading}
          style={{
            padding: '10px 16px',
            backgroundColor: 'var(--accent)',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            color: 'white',
            fontSize: '14px',
            fontWeight: 500,
            opacity: isLoading ? 0.7 : 1,
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
        >
          {submitLabel ? (
            isLoading ? 'Saving...' : submitLabel
          ) : (
            <>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              {isLoading ? 'Hiring...' : 'Hire Agent'}
            </>
          )}
        </button>
      </div>
      <ConfirmMount />
    </form>
  );
}

export default AgentForm;
