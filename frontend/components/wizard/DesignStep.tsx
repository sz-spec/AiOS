'use client';

import { useState, useCallback, useRef } from 'react';
import { Upload, X, Palette, Grid3x3, Type, Check, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useWizardStore } from '@/lib/store/wizardStore';

const STYLE_PRESETS = [
  { id: 'apple-like', name: 'Apple-like', colors: ['#000000', '#F5F5F7', '#0071E3', '#86868B'] },
  { id: 'cyberpunk', name: 'Cyberpunk', colors: ['#0D0D0D', '#00FF41', '#FF00FF', '#00D4FF'] },
  { id: 'minimal', name: 'Minimal', colors: ['#FFFFFF', '#111827', '#3B82F6', '#F3F4F6'] },
  { id: 'corporate', name: 'Corporate', colors: ['#1E3A5F', '#FFFFFF', '#2563EB', '#F1F5F9'] },
  { id: 'glassmorphism', name: 'Glass', colors: ['#FFFFFF', '#1A1A2E', '#6366F1', '#A5B4FC'] },
  { id: 'dark-mode', name: 'Dark Mode', colors: ['#0F172A', '#E2E8F0', '#8B5CF6', '#1E293B'] },
  { id: 'brutalist', name: 'Brutalist', colors: ['#FFFFFF', '#000000', '#FF0000', '#FFFF00'] },
  { id: 'material-design', name: 'Material', colors: ['#FFFFFF', '#212121', '#6200EE', '#03DAC6'] },
];

const PHASE_ICONS: Record<string, typeof Palette> = {
  validate: Check,
  analyze: Grid3x3,
  contract: Palette,
  theme: Type,
  complete: Check,
};

export function DesignStep() {
  const {
    designImage, designStyle, streamedInsights, description,
    setDesignImage, setDesignStyle, addInsight, clearInsights,
  } = useWizardStore();

  const [preview, setPreview] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    if (file.size > 4 * 1024 * 1024) {
      toast.error('Image too large', { description: 'Files must be under 4 MB.' });
      return;
    }
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      toast.error('Unsupported format', { description: 'Use PNG, JPEG, or WebP.' });
      return;
    }
    setDesignImage(file);
    const url = URL.createObjectURL(file);
    setPreview(url);
    fetchInsights(file);
  }, [setDesignImage, description]);

  const handleRemove = useCallback(() => {
    setDesignImage(null);
    setPreview(null);
    clearInsights();
  }, [setDesignImage, clearInsights]);

  const handleStyleSelect = useCallback((styleId: string) => {
    if (designStyle === styleId) {
      setDesignStyle(null);
      clearInsights();
      return;
    }
    setDesignStyle(styleId);
    setPreview(null);
    fetchInsightsForStyle(styleId);
  }, [designStyle, setDesignStyle, clearInsights, description]);

  const fetchInsights = async (file: File) => {
    setAnalyzing(true);
    clearInsights();
    try {
      const form = new FormData();
      form.append('image', file);
      form.append('requirements', description);

      const response = await fetch('/api/v1/vision/analyze', { method: 'POST', body: form });
      if (!response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              addInsight(data.phase, data.message);
            } catch { /* ignore malformed SSE */ }
          }
        }
      }
    } catch (err) {
      addInsight('error', 'Failed to connect to analysis server');
    } finally {
      setAnalyzing(false);
    }
  };

  const fetchInsightsForStyle = async (styleId: string) => {
    setAnalyzing(true);
    clearInsights();
    try {
      const form = new FormData();
      form.append('style', styleId);
      form.append('requirements', description);

      const response = await fetch('/api/v1/vision/analyze', { method: 'POST', body: form });
      if (!response.body) return;

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              addInsight(data.phase, data.message);
            } catch { /* ignore malformed SSE */ }
          }
        }
      }
    } catch {
      addInsight('error', 'Failed to connect to analysis server');
    } finally {
      setAnalyzing(false);
    }
  };

  const isComplete = streamedInsights.some(i => i.phase === 'complete');
  const tokenCount = isComplete
    ? (streamedInsights.find(i => i.phase === 'contract')?.message.match(/(\d+) tokens/)?.[1] || '0')
    : null;
  const componentCount = isComplete
    ? (streamedInsights.find(i => i.phase === 'contract')?.message.match(/(\d+) components/)?.[1] || '0')
    : null;

  return (
    <div style={{ display: 'flex', gap: '32px', alignItems: 'flex-start' }}>
      {/* Left column: Upload + Presets */}
      <div style={{ flex: 1 }}>
        <h2 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
          Design Reference
        </h2>
        <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '24px' }}>
          Upload a screenshot or choose a style preset. This is optional.
        </p>

        {/* Drag-and-drop zone */}
        {!preview ? (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const file = e.dataTransfer.files[0];
              if (file) handleFile(file);
            }}
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: `2px dashed ${dragOver ? 'var(--accent)' : 'var(--border-light)'}`,
              borderRadius: 'var(--radius-lg, 12px)',
              padding: '48px 24px',
              textAlign: 'center',
              cursor: 'pointer',
              backgroundColor: dragOver ? 'rgba(59,130,246,0.04)' : 'var(--bg-secondary)',
              transition: 'all 0.15s',
              marginBottom: '24px',
            }}
          >
            <Upload size={32} style={{ color: 'var(--text-tertiary)', marginBottom: '12px' }} />
            <p style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '4px' }}>
              Drop a design screenshot here
            </p>
            <p style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
              PNG, JPEG, or WebP up to 4MB
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
              style={{ display: 'none' }}
            />
          </div>
        ) : (
          <div style={{
            position: 'relative',
            borderRadius: 'var(--radius-lg, 12px)',
            overflow: 'hidden',
            marginBottom: '24px',
            border: '1px solid var(--border-light)',
          }}>
            <img
              src={preview}
              alt="Design preview"
              style={{ width: '100%', maxHeight: '300px', objectFit: 'contain', display: 'block' }}
            />
            <button
              onClick={handleRemove}
              style={{
                position: 'absolute',
                top: '8px',
                right: '8px',
                width: '28px',
                height: '28px',
                borderRadius: '50%',
                backgroundColor: 'rgba(0,0,0,0.6)',
                color: 'white',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* Style presets */}
        <div style={{ marginBottom: '8px' }}>
          <p style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Or choose a style preset
          </p>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: '10px',
          }}>
            {STYLE_PRESETS.map((preset) => {
              const isActive = designStyle === preset.id;
              return (
                <button
                  key={preset.id}
                  onClick={() => handleStyleSelect(preset.id)}
                  style={{
                    padding: '12px 8px',
                    borderRadius: 'var(--radius-md, 8px)',
                    border: isActive ? '2px solid var(--accent)' : '1px solid var(--border-light)',
                    backgroundColor: isActive ? 'rgba(59,130,246,0.06)' : 'var(--bg-primary)',
                    cursor: 'pointer',
                    textAlign: 'center',
                    transition: 'all 0.15s',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'center', gap: '3px', marginBottom: '6px' }}>
                    {preset.colors.map((color, i) => (
                      <div
                        key={i}
                        style={{
                          width: '14px',
                          height: '14px',
                          borderRadius: '50%',
                          backgroundColor: color,
                          border: '1px solid var(--border-light)',
                        }}
                      />
                    ))}
                  </div>
                  <span style={{ fontSize: '11px', fontWeight: 500, color: 'var(--text-primary)' }}>
                    {preset.name}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Right column: Analysis Insights */}
      {(streamedInsights.length > 0 || analyzing) && (
        <div style={{
          width: '320px',
          flexShrink: 0,
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-lg, 12px)',
          border: '1px solid var(--border-light)',
          padding: '16px',
        }}>
          <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '16px' }}>
            Analysis Insights
          </h3>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {streamedInsights
              .filter(i => i.phase !== 'complete')
              .map((insight, idx) => {
                const Icon = PHASE_ICONS[insight.phase] || Check;
                const isError = insight.phase === 'error';
                return (
                  <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                    <div style={{
                      width: '20px',
                      height: '20px',
                      borderRadius: '50%',
                      backgroundColor: isError ? 'rgba(239,68,68,0.1)' : 'rgba(16,185,129,0.1)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                      marginTop: '1px',
                    }}>
                      <Icon size={11} style={{ color: isError ? '#ef4444' : '#10b981' }} />
                    </div>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                      {insight.message}
                    </span>
                  </div>
                );
              })}

            {analyzing && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Loader2 size={14} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>Analyzing...</span>
              </div>
            )}
          </div>

          {/* Summary card when complete */}
          {isComplete && (
            <div style={{
              marginTop: '16px',
              padding: '12px',
              borderRadius: 'var(--radius-md, 8px)',
              backgroundColor: 'rgba(16,185,129,0.06)',
              border: '1px solid rgba(16,185,129,0.2)',
            }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#10b981', marginBottom: '6px' }}>
                Analysis Complete
              </div>
              <div style={{ display: 'flex', gap: '16px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                {tokenCount && <span>{tokenCount} tokens</span>}
                {componentCount && <span>{componentCount} components</span>}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Spin animation for Loader2 */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
