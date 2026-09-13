'use client';

import { useState } from 'react';
import { Shield, Mail, Globe as Chrome, GitBranch as Github } from 'lucide-react';

interface AuthProvider {
  id: string;
  name: string;
  icon: typeof Mail;
  enabled: boolean;
}

interface AuthSetupPanelProps {
  projectId: string;
  onAuthConfigured?: () => void;
}

export function AuthSetupPanel({ projectId, onAuthConfigured }: AuthSetupPanelProps) {
  const [authEnabled, setAuthEnabled] = useState(false);
  const [providers, setProviders] = useState<AuthProvider[]>([
    { id: 'email', name: 'Email / Password', icon: Mail, enabled: true },
    { id: 'google', name: 'Google', icon: Chrome, enabled: false },
    { id: 'github', name: 'GitHub', icon: Github, enabled: false },
  ]);
  const [isApplying, setIsApplying] = useState(false);

  const toggleProvider = (id: string) => {
    setProviders((prev) =>
      prev.map((p) => p.id === id ? { ...p, enabled: !p.enabled } : p)
    );
  };

  const applyAuth = async () => {
    setIsApplying(true);
    try {
      await fetch('/api/v1/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          enabled: authEnabled,
          providers: providers.filter((p) => p.enabled).map((p) => p.id),
        }),
      });
      onAuthConfigured?.();
    } catch {
      // ignore
    }
    setIsApplying(false);
  };

  return (
    <div style={{ padding: '24px', maxWidth: '480px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
        <Shield size={24} style={{ color: 'var(--accent)' }} />
        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>User Authentication</h3>
      </div>

      {/* Main toggle */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '16px 20px',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-secondary)',
        marginBottom: '20px',
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: '15px' }}>Enable User Accounts</div>
          <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
            Add login, signup, and protected pages
          </div>
        </div>
        <button
          onClick={() => setAuthEnabled(!authEnabled)}
          style={{
            width: '44px',
            height: '24px',
            borderRadius: '12px',
            backgroundColor: authEnabled ? 'var(--accent)' : 'var(--bg-hover)',
            position: 'relative',
            transition: 'background-color 0.2s',
            cursor: 'pointer',
            border: 'none',
          }}
        >
          <div style={{
            width: '20px',
            height: '20px',
            borderRadius: '50%',
            backgroundColor: 'white',
            position: 'absolute',
            top: '2px',
            left: authEnabled ? '22px' : '2px',
            transition: 'left 0.2s',
            boxShadow: 'var(--shadow-sm)',
          }} />
        </button>
      </div>

      {/* Providers */}
      {authEnabled && (
        <>
          <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Sign-in Methods
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '24px' }}>
            {providers.map((provider) => {
              const Icon = provider.icon;
              return (
                <div
                  key={provider.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '12px 16px',
                    borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${provider.enabled ? 'var(--accent)' : 'var(--border-light)'}`,
                    backgroundColor: provider.enabled ? 'var(--bg-accent-light)' : 'var(--bg-primary)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <Icon size={18} style={{ color: provider.enabled ? 'var(--accent)' : 'var(--text-tertiary)' }} />
                    <span style={{ fontSize: '14px', fontWeight: 500 }}>{provider.name}</span>
                  </div>
                  <input
                    type="checkbox"
                    checked={provider.enabled}
                    onChange={() => toggleProvider(provider.id)}
                    style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                  />
                </div>
              );
            })}
          </div>

          <button
            onClick={applyAuth}
            disabled={isApplying}
            style={{
              width: '100%',
              padding: '12px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--accent)',
              color: 'white',
              fontSize: '14px',
              fontWeight: 500,
              cursor: isApplying ? 'not-allowed' : 'pointer',
              opacity: isApplying ? 0.7 : 1,
            }}
          >
            {isApplying ? 'Applying...' : 'Apply Authentication'}
          </button>

          <p style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '12px', textAlign: 'center' }}>
            This generates login/signup pages and protects your app routes automatically.
          </p>
        </>
      )}
    </div>
  );
}
