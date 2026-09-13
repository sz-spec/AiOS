'use client';

import { useState } from 'react';
import { Rocket, Globe, Check, Loader2, ExternalLink, AlertCircle } from 'lucide-react';

type DeployStatus = 'idle' | 'deploying' | 'success' | 'error';

interface DeployPanelProps {
  projectId: string;
}

export function DeployPanel({ projectId }: DeployPanelProps) {
  const [subdomain, setSubdomain] = useState('');
  const [status, setStatus] = useState<DeployStatus>('idle');
  const [deployUrl, setDeployUrl] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [isFirstDeploy, setIsFirstDeploy] = useState(true);

  const deploy = async () => {
    if (isFirstDeploy && !subdomain.trim()) return;

    setStatus('deploying');
    setErrorMsg('');

    try {
      const res = await fetch('/api/v1/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          subdomain: subdomain.trim() || undefined,
        }),
      });

      const data = await res.json();

      if (res.ok) {
        setStatus('success');
        setDeployUrl(data.url || `https://${subdomain}.vcreator.app`);
        setIsFirstDeploy(false);
      } else {
        setStatus('error');
        setErrorMsg(data.detail || 'Something went wrong. Try again.');
      }
    } catch {
      setStatus('error');
      setErrorMsg('Connection failed. Please try again.');
    }
  };

  return (
    <div style={{ padding: '32px', maxWidth: '480px', margin: '0 auto' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <Rocket size={40} style={{ color: 'var(--accent)', marginBottom: '12px' }} />
        <h2 style={{ margin: '0 0 8px', fontSize: '22px', fontWeight: 600 }}>Publish Your App</h2>
        <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>
          Deploy your app to the web with one click
        </p>
      </div>

      {/* Subdomain input (first deploy only) */}
      {isFirstDeploy && status !== 'success' && (
        <div style={{ marginBottom: '20px' }}>
          <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
            Choose your URL
          </label>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0' }}>
            <input
              value={subdomain}
              onChange={(e) => setSubdomain(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
              placeholder="my-app"
              style={{
                flex: 1,
                padding: '12px 14px',
                borderRadius: 'var(--radius-sm) 0 0 var(--radius-sm)',
                border: '1px solid var(--border-light)',
                borderRight: 'none',
                fontSize: '14px',
                outline: 'none',
              }}
            />
            <div style={{
              padding: '12px 14px',
              borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
              border: '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-tertiary)',
              fontSize: '14px',
              color: 'var(--text-secondary)',
              whiteSpace: 'nowrap',
            }}>
              .vcreator.app
            </div>
          </div>
        </div>
      )}

      {/* Deploy button */}
      {status !== 'success' && (
        <button
          onClick={deploy}
          disabled={status === 'deploying' || (isFirstDeploy && !subdomain.trim())}
          style={{
            width: '100%',
            padding: '14px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: status === 'deploying' ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: status === 'deploying' ? 'var(--text-secondary)' : 'white',
            fontSize: '15px',
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            cursor: status === 'deploying' ? 'not-allowed' : 'pointer',
          }}
        >
          {status === 'deploying' ? (
            <>
              <Loader2 size={18} style={{ animation: 'spin 1.5s linear infinite' }} />
              Deploying...
            </>
          ) : (
            <>
              <Rocket size={18} />
              {isFirstDeploy ? 'Publish' : 'Re-deploy'}
            </>
          )}
        </button>
      )}

      {/* Progress */}
      {status === 'deploying' && (
        <div style={{ marginTop: '20px' }}>
          {['Packaging files', 'Uploading', 'Building', 'Going live'].map((step, i) => (
            <div key={step} style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '8px 0',
              fontSize: '13px',
              color: 'var(--text-secondary)',
            }}>
              <Loader2 size={14} style={{ animation: 'spin 1.5s linear infinite', animationDelay: `${i * 0.3}s` }} />
              {step}...
            </div>
          ))}
        </div>
      )}

      {/* Success */}
      {status === 'success' && (
        <div style={{
          padding: '20px',
          borderRadius: 'var(--radius-md)',
          backgroundColor: '#d1fae5',
          border: '1px solid #a7f3d0',
          textAlign: 'center',
        }}>
          <Check size={32} style={{ color: '#059669', marginBottom: '8px' }} />
          <div style={{ fontWeight: 600, fontSize: '16px', color: '#065f46', marginBottom: '8px' }}>
            Your app is live!
          </div>
          <a
            href={deployUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              color: '#059669',
              fontWeight: 500,
              fontSize: '14px',
            }}
          >
            <Globe size={14} />
            {deployUrl}
            <ExternalLink size={12} />
          </a>
          <div style={{ marginTop: '16px' }}>
            <button
              onClick={deploy}
              style={{
                padding: '10px 24px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: '#059669',
                color: 'white',
                fontSize: '13px',
                fontWeight: 500,
              }}
            >
              Re-deploy with latest changes
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {status === 'error' && (
        <div style={{
          marginTop: '16px',
          padding: '14px 18px',
          borderRadius: 'var(--radius-md)',
          backgroundColor: '#fee2e2',
          border: '1px solid #fca5a5',
          fontSize: '14px',
          color: '#991b1b',
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
        }}>
          <AlertCircle size={18} />
          {errorMsg}
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
