'use client';

import { useState, useEffect } from 'react';
import { Clock, RotateCcw, ChevronDown, ChevronUp } from 'lucide-react';

interface Checkpoint {
  id: string;
  description: string;
  created_at: string;
}

interface VersionTimelineProps {
  projectId: string;
  onRestore: (checkpointId: string) => void;
}

export function VersionTimeline({ projectId, onRestore }: VersionTimelineProps) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`/api/v1/projects/${projectId}/checkpoints`);
        if (res.ok) {
          const data = await res.json();
          setCheckpoints(data.checkpoints || []);
        }
      } catch {
        // ignore
      }
    }
    load();
  }, [projectId]);

  if (checkpoints.length === 0) return null;

  const handleRestore = async (cpId: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/${projectId}/checkpoints/${cpId}/restore`, {
        method: 'POST',
      });
      if (res.ok) {
        onRestore(cpId);
      }
    } catch {
      // ignore
    }
    setIsLoading(false);
  };

  return (
    <div style={{
      position: 'fixed',
      bottom: 0,
      left: '50%',
      transform: 'translateX(-50%)',
      backgroundColor: 'var(--bg-primary)',
      border: '1px solid var(--border-light)',
      borderBottom: 'none',
      borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
      boxShadow: 'var(--shadow-lg)',
      width: '500px',
      maxWidth: '90vw',
      zIndex: 50,
    }}>
      {/* Toggle bar */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        style={{
          width: '100%',
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'transparent',
          fontSize: '13px',
          fontWeight: 500,
          color: 'var(--text-secondary)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Clock size={14} />
          {checkpoints.length} checkpoint{checkpoints.length !== 1 ? 's' : ''}
        </div>
        {isExpanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      {/* Expanded timeline */}
      {isExpanded && (
        <div style={{
          maxHeight: '240px',
          overflow: 'auto',
          borderTop: '1px solid var(--border-light)',
          padding: '8px',
        }}>
          {[...checkpoints].reverse().map((cp, i) => (
            <div
              key={cp.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 12px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: i === 0 ? 'var(--bg-accent-light)' : 'transparent',
              }}
            >
              <div>
                <div style={{ fontSize: '13px', fontWeight: 500 }}>
                  {cp.description || `Checkpoint ${checkpoints.length - i}`}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                  {new Date(cp.created_at).toLocaleTimeString()}
                </div>
              </div>
              {i > 0 && (
                <button
                  onClick={() => handleRestore(cp.id)}
                  disabled={isLoading}
                  style={{
                    padding: '4px 12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)',
                    backgroundColor: 'var(--bg-primary)',
                    fontSize: '12px',
                    fontWeight: 500,
                    color: 'var(--text-secondary)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}
                >
                  <RotateCcw size={12} />
                  Restore
                </button>
              )}
              {i === 0 && (
                <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--accent)' }}>Current</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
