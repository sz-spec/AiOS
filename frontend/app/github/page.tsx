'use client';

import { useState } from 'react';

interface Repository {
  id: string;
  name: string;
  fullName: string;
  description: string;
  lastSync: string | null;
  status: 'connected' | 'pending' | 'error';
}

export default function GitHubPage() {
  const [connected, setConnected] = useState(false);
  const [repos, setRepos] = useState<Repository[]>([]);

  const mockRepos: Repository[] = [
    {
      id: '1',
      name: 'vos3-frontend',
      fullName: 'myorg/vos3-frontend',
      description: 'Frontend application',
      lastSync: '2 hours ago',
      status: 'connected',
    },
    {
      id: '2',
      name: 'vos3-backend',
      fullName: 'myorg/vos3-backend',
      description: 'Backend API service',
      lastSync: '1 hour ago',
      status: 'connected',
    },
    {
      id: '3',
      name: 'shared-libs',
      fullName: 'myorg/shared-libs',
      description: 'Shared libraries',
      lastSync: null,
      status: 'pending',
    },
  ];

  const handleConnect = () => {
    // In production, this would redirect to GitHub OAuth
    setConnected(true);
    setRepos(mockRepos);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'connected':
        return '#22c55e';
      case 'pending':
        return '#f59e0b';
      case 'error':
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1000px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px' }}>GitHub Integration</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Sync your repositories and enable AI-powered code assistance</p>
      </div>

      {!connected ? (
        /* Connect GitHub */
        <div
          style={{
            padding: '48px',
            textAlign: 'center',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
          }}
        >
          <div style={{ fontSize: '64px', marginBottom: '16px' }}>
            <svg
              width="64"
              height="64"
              viewBox="0 0 24 24"
              fill="currentColor"
              style={{ color: 'var(--text-primary)' }}
            >
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
          </div>
          <h2 style={{ marginBottom: '8px', fontSize: '20px', fontWeight: 600 }}>Connect GitHub</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', maxWidth: '400px', margin: '0 auto 24px' }}>
            Connect your GitHub account to sync repositories, enable code context for AI, and automate workflows.
          </p>
          <button
            onClick={handleConnect}
            style={{
              padding: '12px 24px',
              backgroundColor: '#24292e',
              color: 'white',
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
            Connect with GitHub
          </button>
          <div style={{ marginTop: '16px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
            We only request read access to your repositories
          </div>
        </div>
      ) : (
        /* Repository List */
        <>
          <div
            style={{
              padding: '16px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
              marginBottom: '24px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div
                style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: '#22c55e',
                }}
              />
              <span>Connected as @myorg</span>
            </div>
            <button
              style={{
                padding: '8px 16px',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
              }}
            >
              Add Repository
            </button>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {repos.map((repo) => (
              <div
                key={repo.id}
                style={{
                  padding: '16px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-light)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '16px',
                }}
              >
                <div style={{ fontSize: '24px' }}>📁</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500 }}>{repo.fullName}</div>
                  <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>{repo.description}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div
                    style={{
                      padding: '4px 8px',
                      backgroundColor: `${getStatusColor(repo.status)}20`,
                      color: getStatusColor(repo.status),
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '12px',
                      fontWeight: 500,
                      marginBottom: '4px',
                    }}
                  >
                    {repo.status}
                  </div>
                  {repo.lastSync && (
                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>Synced {repo.lastSync}</div>
                  )}
                </div>
                <button
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--bg-primary)',
                    border: '1px solid var(--border-light)',
                    borderRadius: 'var(--radius-md)',
                    color: 'var(--text-primary)',
                    fontSize: '14px',
                  }}
                >
                  Sync
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
