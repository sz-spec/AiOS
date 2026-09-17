'use client';

import { useState, useEffect } from 'react';
import { Zap, Brain, Sliders, Wifi, WifiOff } from 'lucide-react';

const StatusDot = ({ active }: { active: boolean }) => (
  <span
    style={{
      display: 'inline-block',
      width: 8,
      height: 8,
      borderRadius: '50%',
      backgroundColor: active ? '#22c55e' : '#ef4444',
      marginRight: 6,
    }}
  />
);

interface ModelSelectorProps {
  onModelChange?: (config: ModelConfig) => void;
}

interface ModelConfig {
  mode: 'auto' | 'manual';
  speed: number; // 0 = quality, 100 = speed
  manualModels?: Record<string, string>;
}

interface ProviderStatus {
  anthropic: boolean;
  openai: boolean;
  google: boolean;
  local: boolean;
  local_models: string[];
}

const agentRoles = [
  { id: 'architect', label: 'Architect' },
  { id: 'frontend', label: 'Frontend' },
  { id: 'backend', label: 'Backend' },
  { id: 'tester', label: 'Tester' },
  { id: 'reviewer', label: 'Reviewer' },
];

const cloudModels = [
  { id: 'claude-opus', label: 'Claude Opus (Best Quality)', provider: 'anthropic' },
  { id: 'claude-sonnet', label: 'Claude Sonnet (Balanced)', provider: 'anthropic' },
  { id: 'gpt-4o', label: 'GPT-4o (Fast)', provider: 'openai' },
  { id: 'gemini-pro', label: 'Gemini Pro (Efficient)', provider: 'google' },
];

export function ModelSelector({ onModelChange }: ModelSelectorProps) {
  const [mode, setMode] = useState<'auto' | 'manual'>('auto');
  const [speed, setSpeed] = useState(50);
  const [manualModels, setManualModels] = useState<Record<string, string>>({});
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  // Fetch provider status on mount
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const resp = await fetch('/api/chat/providers/status');
        if (resp.ok) {
          const data = await resp.json();
          setProviderStatus(data);
        }
      } catch {
        // Silently fail — status will show as unavailable
      } finally {
        setStatusLoading(false);
      }
    };
    fetchStatus();
  }, []);

  // Build available models list based on provider status
  const availableModels = [
    ...cloudModels.filter((m) => {
      if (!providerStatus) return true; // Show all while loading
      return providerStatus[m.provider as keyof ProviderStatus];
    }),
    // Append local models if Ollama is available
    ...(providerStatus?.local
      ? providerStatus.local_models.map((name) => ({
          id: `local:${name}`,
          label: `${name} (Local)`,
          provider: 'local' as const,
        }))
      : []),
  ];

  const handleModeChange = (newMode: 'auto' | 'manual') => {
    setMode(newMode);
    onModelChange?.({ mode: newMode, speed, manualModels });
  };

  const handleSpeedChange = (newSpeed: number) => {
    setSpeed(newSpeed);
    onModelChange?.({ mode, speed: newSpeed, manualModels });
  };

  const handleModelSelect = (role: string, model: string) => {
    const updated = { ...manualModels, [role]: model };
    setManualModels(updated);
    onModelChange?.({ mode, speed, manualModels: updated });
  };



  return (
    <div style={{ padding: '24px', maxWidth: '480px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '24px' }}>
        <Brain size={20} style={{ color: 'var(--accent)' }} />
        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>AI Model Settings</h3>
      </div>

      {/* Provider Status */}
      {!statusLoading && providerStatus && (
        <div style={{
          display: 'flex',
          gap: '12px',
          padding: '10px 14px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-tertiary)',
          marginBottom: '16px',
          fontSize: '12px',
          flexWrap: 'wrap',
        }}>
          <span><StatusDot active={providerStatus.anthropic} />Anthropic</span>
          <span><StatusDot active={providerStatus.openai} />OpenAI</span>
          <span><StatusDot active={providerStatus.google} />Google</span>
          <span>
            <StatusDot active={providerStatus.local} />
            Local {providerStatus.local ? `(${providerStatus.local_models.length})` : ''}
          </span>
        </div>
      )}

      {/* Mode Toggle */}
      <div style={{
        display: 'flex',
        gap: '8px',
        padding: '16px',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-secondary)',
        marginBottom: '20px',
      }}>
        <button
          onClick={() => handleModeChange('auto')}
          style={{
            flex: 1,
            padding: '10px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: mode === 'auto' ? 'var(--accent)' : 'transparent',
            color: mode === 'auto' ? 'white' : 'var(--text-secondary)',
            fontWeight: 500,
            fontSize: '13px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
          }}
        >
          <Zap size={14} />
          Let AI Choose
        </button>
        <button
          onClick={() => handleModeChange('manual')}
          style={{
            flex: 1,
            padding: '10px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: mode === 'manual' ? 'var(--accent)' : 'transparent',
            color: mode === 'manual' ? 'white' : 'var(--text-secondary)',
            fontWeight: 500,
            fontSize: '13px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '6px',
          }}
        >
          <Sliders size={14} />
          Manual
        </button>
      </div>

      {/* Auto mode: Speed/Quality slider */}
      {mode === 'auto' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', marginBottom: '8px' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Quality</span>
            <span style={{ color: 'var(--text-secondary)' }}>Speed</span>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={speed}
            onChange={(e) => handleSpeedChange(Number(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent)' }}
          />
          <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center', marginTop: '8px' }}>
            {speed < 33 ? 'Premium models for best results' :
             speed < 66 ? 'Balanced cost and quality' :
             'Fastest and most cost-effective'}
          </p>
        </div>
      )}

      {/* Manual mode: Per-agent model selection */}
      {mode === 'manual' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {agentRoles.map((role) => (
            <div key={role.id}>
              <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '4px', display: 'block' }}>
                {role.label}
              </label>
              <select
                value={manualModels[role.id] || ''}
                onChange={(e) => handleModelSelect(role.id, e.target.value)}
                style={{
                  width: '100%', padding: '8px 10px',
                  borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)',
                  fontSize: '13px', backgroundColor: 'var(--bg-input)',
                }}
              >
                <option value="">Auto</option>
                {availableModels.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
