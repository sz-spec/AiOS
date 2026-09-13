'use client';

import React, { Suspense, useEffect, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useBMAD, BMADMode } from '@/hooks/useBMAD';
import { BMADWizard, BMADExpert, BMADParty } from '@/components/bmad';

export default function BMADSessionPage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{
          width: '40px', height: '40px',
          border: '4px solid var(--border-light)',
          borderTopColor: 'var(--accent)',
          borderRadius: '50%',
          animation: 'spin 1s linear infinite',
        }} />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    }>
      <BMADSessionContent />
    </Suspense>
  );
}

function BMADSessionContent() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = params.sessionId as string;

  const {
    currentSession,
    isLoading,
    error,
    loadSession,
    clearError,
  } = useBMAD();

  const [viewMode, setViewMode] = useState<'auto' | 'wizard' | 'expert' | 'party'>('auto');

  useEffect(() => {
    if (sessionId) {
      loadSession(sessionId);
    }
  }, [sessionId, loadSession]);

  // Set view mode from URL or session mode
  useEffect(() => {
    const urlMode = searchParams.get('view');
    if (urlMode && ['wizard', 'expert', 'party'].includes(urlMode)) {
      setViewMode(urlMode as 'wizard' | 'expert' | 'party');
    } else if (currentSession) {
      // Auto-select based on session mode
      const modeMap: Record<BMADMode, 'wizard' | 'expert' | 'party'> = {
        simple: 'wizard',
        guided: 'wizard',
        expert: 'expert',
        party: 'party',
      };
      setViewMode(modeMap[currentSession.mode] || 'wizard');
    }
  }, [currentSession, searchParams]);

  if (isLoading && !currentSession) {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{
            width: '40px', height: '40px', margin: '0 auto',
            border: '4px solid var(--border-light)',
            borderTopColor: 'var(--accent)',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
          }} />
          <p style={{ color: 'var(--text-tertiary)', marginTop: '16px' }}>Loading session...</p>
        </div>
      </div>
    );
  }

  if (!currentSession && !isLoading) {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '48px', marginBottom: '16px' }}>&#128269;</div>
          <h2 style={{ margin: '0 0 8px', fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>Session Not Found</h2>
          <p style={{ margin: '0 0 16px', color: 'var(--text-tertiary)', fontSize: '14px' }}>The session you&apos;re looking for doesn&apos;t exist.</p>
          <button
            onClick={() => router.push('/studio')}
            style={{
              padding: '10px 20px',
              backgroundColor: 'var(--accent)',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Back to Projects
          </button>
        </div>
      </div>
    );
  }

  // View mode selector
  const ViewModeSelector = () => (
    <div style={{
      position: 'fixed',
      top: '16px',
      right: '16px',
      zIndex: 50,
      display: 'flex',
      backgroundColor: 'var(--bg-primary)',
      borderRadius: '10px',
      boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
      border: '1px solid var(--border-light)',
      overflow: 'hidden',
    }}>
      {[
        { mode: 'wizard' as const, label: 'Guided', icon: '&#127919;', color: 'var(--accent)' },
        { mode: 'expert' as const, label: 'Expert', icon: '&#128295;', color: '#8b5cf6' },
        { mode: 'party' as const, label: 'Party', icon: '&#127881;', color: '#a855f7' },
      ].map((item) => (
        <button
          key={item.mode}
          onClick={() => {
            setViewMode(item.mode);
            router.replace(`/studio/${sessionId}?view=${item.mode}`);
          }}
          style={{
            padding: '8px 16px',
            fontSize: '13px',
            fontWeight: 500,
            border: 'none',
            cursor: 'pointer',
            transition: 'all 0.15s',
            backgroundColor: viewMode === item.mode ? item.color : 'transparent',
            color: viewMode === item.mode ? 'white' : 'var(--text-secondary)',
          }}
        >
          <span dangerouslySetInnerHTML={{ __html: item.icon }} /> {item.label}
        </button>
      ))}
      <button
        onClick={() => router.push('/studio')}
        style={{
          padding: '8px 12px',
          fontSize: '13px',
          fontWeight: 500,
          border: 'none',
          borderLeft: '1px solid var(--border-light)',
          backgroundColor: 'transparent',
          color: 'var(--text-tertiary)',
          cursor: 'pointer',
        }}
        title="Back to projects"
      >
        &#8592;
      </button>
    </div>
  );

  // Render based on view mode
  if (viewMode === 'party') {
    return (
      <>
        <ViewModeSelector />
        <BMADParty sessionId={sessionId} />
      </>
    );
  }

  if (viewMode === 'expert') {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
        <ViewModeSelector />
        <BMADExpert sessionId={sessionId} />
      </div>
    );
  }

  // Default to wizard view
  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      <ViewModeSelector />
      <BMADWizard sessionId={sessionId} onSessionCreated={() => {}} />

      {/* Error Toast */}
      {error && (
        <div style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          padding: '16px 20px',
          backgroundColor: '#fef2f2',
          border: '1px solid #fecaca',
          borderRadius: '12px',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
        }}>
          <span style={{ flex: 1, color: '#dc2626', fontSize: '14px' }}>{error}</span>
          <button
            onClick={clearError}
            style={{
              background: 'none', border: 'none',
              color: '#dc2626', cursor: 'pointer',
              textDecoration: 'underline', fontSize: '13px',
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
