'use client';

import React, { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  isWebpackError: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, isWebpackError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    const isWebpackError =
      error.message?.includes('call') ||
      error.message?.includes('webpack') ||
      error.message?.includes('hydrat') ||
      error.message?.includes('chunk');

    return { hasError: true, error, isWebpackError };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  handleReload = () => {
    // Clear localStorage cache markers
    try {
      localStorage.removeItem('next-cache');
    } catch {}

    // Hard reload
    window.location.reload();
  };

  handleHardReload = () => {
    // Force bypass cache
    window.location.href = window.location.href + '?cache=' + Date.now();
  };

  render() {
    if (this.state.hasError) {
      const { isWebpackError, error } = this.state;

      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#0a0a0f',
          color: '#e0e0e0',
          padding: '20px',
        }}>
          <div style={{
            maxWidth: '500px',
            textAlign: 'center',
          }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>
              {isWebpackError ? '🔄' : '⚠️'}
            </div>

            <h1 style={{
              fontSize: '24px',
              fontWeight: 600,
              marginBottom: '12px',
            }}>
              {isWebpackError ? 'Cache Error' : 'Something went wrong'}
            </h1>

            <p style={{
              color: '#888',
              marginBottom: '24px',
              lineHeight: 1.6,
            }}>
              {isWebpackError
                ? 'The build cache got corrupted. This happens sometimes during development. Click below to fix it.'
                : error?.message || 'An unexpected error occurred.'
              }
            </p>

            <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
              <button
                onClick={this.handleReload}
                style={{
                  padding: '12px 24px',
                  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                  border: 'none',
                  borderRadius: '8px',
                  color: '#fff',
                  fontSize: '15px',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {isWebpackError ? 'Reload Page' : 'Try Again'}
              </button>

              {isWebpackError && (
                <button
                  onClick={this.handleHardReload}
                  style={{
                    padding: '12px 24px',
                    background: 'transparent',
                    border: '1px solid #333',
                    borderRadius: '8px',
                    color: '#888',
                    fontSize: '15px',
                    cursor: 'pointer',
                  }}
                >
                  Hard Reload
                </button>
              )}
            </div>

            {isWebpackError && (
              <p style={{
                marginTop: '24px',
                fontSize: '13px',
                color: '#666',
              }}>
                If this keeps happening, run: <code style={{
                  backgroundColor: '#1a1a2e',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  fontFamily: 'monospace',
                }}>npm run dev:clean</code>
              </p>
            )}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
