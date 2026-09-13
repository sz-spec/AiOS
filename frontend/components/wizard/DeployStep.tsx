'use client';

import { useState } from 'react';
import { Download, Server, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import { useWizardStore } from '@/lib/store/wizardStore';
import { AppStatusCard } from '@/components/deploy/AppStatusCard';

interface DeployStepProps {
  projectId?: string;
}

export function DeployStep({ projectId }: DeployStepProps) {
  const {
    deployTarget, setDeployTarget, deployStatus, setDeployStatus,
    deployAppId, setDeployAppId, addDeployProgress, deployProgress,
    clearDeployProgress,
  } = useWizardStore();

  const [error, setError] = useState<string | null>(null);

  const handleDownloadZip = async () => {
    if (!projectId) return;
    setDeployTarget('download');
    setDeployStatus('packaging');
    try {
      const res = await fetch(`/api/v1/build/${projectId}/download`);
      if (!res.ok) throw new Error('Download failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${projectId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setDeployStatus('idle');
    } catch (e: any) {
      setError(e.message);
      setDeployStatus('error');
    }
  };

  const handleDeployNative = async () => {
    if (!projectId) return;
    setDeployTarget('vos3-native');
    setDeployStatus('packaging');
    clearDeployProgress();
    setError(null);

    try {
      const res = await fetch(`/api/v1/deploy/native/${projectId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId }),
      });
      if (!res.ok) throw new Error('Deploy request failed');

      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response stream');
      const decoder = new TextDecoder();

      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            const data = event.data || event;

            if (event.event === 'progress' || data.phase) {
              const phase = data.phase as string;
              if (phase === 'packaging') setDeployStatus('packaging');
              else if (phase === 'transferring') setDeployStatus('transferring');
              else if (phase === 'launching') setDeployStatus('launching');
              addDeployProgress(data.phase, data.message, data.progress);
            } else if (event.event === 'complete') {
              setDeployStatus('running');
              setDeployAppId(data.app_id);
              addDeployProgress('running', data.message);
            } else if (event.event === 'error') {
              setDeployStatus('error');
              setError(data.message);
            }
          } catch {
            // Skip malformed SSE lines
          }
        }
      }
    } catch (e: any) {
      setError(e.message);
      setDeployStatus('error');
    }
  };

  const isDeploying = deployStatus !== 'idle' && deployStatus !== 'error';
  const isRunning = deployStatus === 'running';

  return (
    <div style={{ maxWidth: '720px', margin: '0 auto' }}>
      <h2 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '8px', color: 'var(--text-primary)' }}>
        Deploy Your Project
      </h2>
      <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '32px' }}>
        Choose how to deliver your application.
      </p>

      {/* Deploy Options */}
      {!isDeploying && !isRunning && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
          {/* Download ZIP */}
          <button
            onClick={handleDownloadZip}
            disabled={!projectId}
            style={{
              padding: '24px',
              borderRadius: 'var(--radius-lg, 12px)',
              border: deployTarget === 'download'
                ? '2px solid var(--accent)'
                : '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-secondary)',
              cursor: projectId ? 'pointer' : 'not-allowed',
              textAlign: 'left',
            }}
          >
            <Download size={28} style={{ color: 'var(--accent)', marginBottom: '12px' }} />
            <div style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
              Download ZIP
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Includes VOS3_SDK.h + Makefile.native for local cross-compilation with musl.
            </div>
          </button>

          {/* Deploy to VOS3 */}
          <button
            onClick={handleDeployNative}
            disabled={!projectId}
            style={{
              padding: '24px',
              borderRadius: 'var(--radius-lg, 12px)',
              border: deployTarget === 'vos3-native'
                ? '2px solid var(--success, #10b981)'
                : '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-secondary)',
              cursor: projectId ? 'pointer' : 'not-allowed',
              textAlign: 'left',
            }}
          >
            <Server size={28} style={{ color: 'var(--success, #10b981)', marginBottom: '12px' }} />
            <div style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
              Deploy to VOS3
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Native deployment to VOS3 kernel with AI Guard sandboxing and hardware-protected memory.
            </div>
          </button>
        </div>
      )}

      {/* Deploy Progress */}
      {isDeploying && !isRunning && (
        <div style={{
          padding: '24px',
          borderRadius: 'var(--radius-lg, 12px)',
          border: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-secondary)',
          marginBottom: '24px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
            <Loader2 size={20} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {deployStatus === 'packaging' && 'Packaging...'}
              {deployStatus === 'transferring' && 'Transferring to VOS3...'}
              {deployStatus === 'launching' && 'Launching on kernel...'}
            </span>
          </div>
          <div style={{ maxHeight: '200px', overflow: 'auto' }}>
            {deployProgress.map((p, i) => (
              <div key={i} style={{
                fontSize: '12px',
                color: 'var(--text-secondary)',
                padding: '4px 0',
                borderBottom: '1px solid var(--border-light)',
              }}>
                <span style={{ color: 'var(--text-tertiary)', marginRight: '8px' }}>
                  [{p.phase}]
                </span>
                {p.message}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Running App Status */}
      {isRunning && deployAppId && (
        <AppStatusCard appId={deployAppId} />
      )}

      {/* Error */}
      {error && (
        <div style={{
          padding: '12px 16px',
          borderRadius: 'var(--radius-md, 8px)',
          backgroundColor: 'rgba(239,68,68,0.08)',
          border: '1px solid rgba(239,68,68,0.2)',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginTop: '16px',
        }}>
          <AlertCircle size={16} style={{ color: '#ef4444' }} />
          <span style={{ fontSize: '13px', color: '#ef4444' }}>{error}</span>
        </div>
      )}

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
