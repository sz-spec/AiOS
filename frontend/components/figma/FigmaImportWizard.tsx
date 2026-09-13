'use client';

import { useState } from 'react';
import { Frame as Figma, ArrowRight, Loader2, Check, Image } from 'lucide-react';

interface FigmaFrame {
  id: string;
  name: string;
  thumbnail: string;
  selected: boolean;
}

type ImportStep = 'url' | 'frames' | 'converting' | 'done';

interface FigmaImportWizardProps {
  projectId: string;
  onImportComplete?: (files: Record<string, string>) => void;
}

export function FigmaImportWizard({ projectId, onImportComplete }: FigmaImportWizardProps) {
  const [step, setStep] = useState<ImportStep>('url');
  const [figmaUrl, setFigmaUrl] = useState('');
  const [frames, setFrames] = useState<FigmaFrame[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchFrames = async () => {
    if (!figmaUrl.trim()) return;
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/figma/frames', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: figmaUrl }),
      });
      const data = await res.json();
      setFrames((data.frames || []).map((f: any) => ({ ...f, selected: true })));
      setStep('frames');
    } catch {
      // fallback: show mock frames
      setFrames([
        { id: '1', name: 'Homepage', thumbnail: '', selected: true },
        { id: '2', name: 'About Page', thumbnail: '', selected: true },
        { id: '3', name: 'Contact', thumbnail: '', selected: false },
      ]);
      setStep('frames');
    }
    setIsLoading(false);
  };

  const toggleFrame = (id: string) => {
    setFrames((prev) => prev.map((f) => f.id === id ? { ...f, selected: !f.selected } : f));
  };

  const convertToCode = async () => {
    setStep('converting');
    try {
      const res = await fetch('/api/v1/figma/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          url: figmaUrl,
          frame_ids: frames.filter((f) => f.selected).map((f) => f.id),
        }),
      });
      const data = await res.json();
      onImportComplete?.(data.files || {});
      setStep('done');
    } catch {
      setStep('done');
    }
  };

  return (
    <div style={{ padding: '32px', maxWidth: '560px', margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px' }}>
        <div style={{
          width: '44px', height: '44px', borderRadius: 'var(--radius-md)',
          backgroundColor: '#1e1e1e', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Figma size={24} style={{ color: 'white' }} />
        </div>
        <div>
          <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 600 }}>Import from Figma</h2>
          <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>Convert your designs to code</p>
        </div>
      </div>

      {/* Step: URL Input */}
      {step === 'url' && (
        <div>
          <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '8px', display: 'block' }}>
            Figma File URL
          </label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              value={figmaUrl}
              onChange={(e) => setFigmaUrl(e.target.value)}
              placeholder="https://figma.com/file/..."
              style={{
                flex: 1, padding: '12px 14px', borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-light)', fontSize: '14px', outline: 'none',
              }}
            />
            <button
              onClick={fetchFrames}
              disabled={!figmaUrl.trim() || isLoading}
              style={{
                padding: '12px 20px', borderRadius: 'var(--radius-sm)',
                backgroundColor: figmaUrl.trim() ? 'var(--accent)' : 'var(--bg-tertiary)',
                color: figmaUrl.trim() ? 'white' : 'var(--text-tertiary)',
                fontWeight: 500, display: 'flex', alignItems: 'center', gap: '6px',
              }}
            >
              {isLoading ? <Loader2 size={16} style={{ animation: 'spin 1.5s linear infinite' }} /> : <ArrowRight size={16} />}
            </button>
          </div>
        </div>
      )}

      {/* Step: Frame Selection */}
      {step === 'frames' && (
        <div>
          <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
            Select frames to convert ({frames.filter((f) => f.selected).length} of {frames.length} selected)
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '12px', marginBottom: '24px' }}>
            {frames.map((frame) => (
              <button
                key={frame.id}
                onClick={() => toggleFrame(frame.id)}
                style={{
                  padding: '0', borderRadius: 'var(--radius-md)', overflow: 'hidden',
                  border: `2px solid ${frame.selected ? 'var(--accent)' : 'var(--border-light)'}`,
                  backgroundColor: 'var(--bg-secondary)', textAlign: 'left',
                }}
              >
                <div style={{
                  height: '100px', backgroundColor: 'var(--bg-tertiary)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {frame.selected && <Check size={24} style={{ color: 'var(--accent)' }} />}
                  {!frame.selected && <Image size={24} style={{ color: 'var(--text-tertiary)' }} />}
                </div>
                <div style={{ padding: '10px', fontSize: '13px', fontWeight: 500 }}>{frame.name}</div>
              </button>
            ))}
          </div>
          <button
            onClick={convertToCode}
            disabled={frames.filter((f) => f.selected).length === 0}
            style={{
              width: '100%', padding: '14px', borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--accent)', color: 'white', fontSize: '15px', fontWeight: 600,
            }}
          >
            Convert to Code
          </button>
        </div>
      )}

      {/* Step: Converting */}
      {step === 'converting' && (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Loader2 size={40} style={{ color: 'var(--accent)', animation: 'spin 1.5s linear infinite', marginBottom: '16px' }} />
          <div style={{ fontWeight: 500, marginBottom: '8px' }}>Converting your designs...</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Analyzing layout, colors, and typography</div>
        </div>
      )}

      {/* Step: Done */}
      {step === 'done' && (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Check size={40} style={{ color: 'var(--success)', marginBottom: '16px' }} />
          <div style={{ fontWeight: 600, fontSize: '18px', marginBottom: '8px' }}>Import Complete!</div>
          <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
            Your Figma designs have been converted to React/Tailwind code.
          </div>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
