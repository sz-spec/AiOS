'use client';

import React, { useState, useEffect } from 'react';
import { useBMAD, BMADPhase, BMADMode, BMADSession, Approval } from '@/hooks/useBMAD';
import { useSettings } from '@/hooks/useSettings';
import { PhaseTimeline } from './PhaseTimeline';

interface BMADWizardProps {
  sessionId?: string;
  onSessionCreated?: (session: BMADSession) => void;
  onClose?: () => void;
}

const MODE_INFO: Record<BMADMode, { label: string; description: string; icon: string; color: string; features: string[] }> = {
  simple: {
    label: 'Quick Fix',
    description: 'Bug fixes and small changes',
    icon: '⚡',
    color: '#f59e0b',
    features: ['Skip ideation phase', 'Fast iteration', 'Direct to development'],
  },
  guided: {
    label: 'Guided',
    description: 'Step-by-step wizard through all phases',
    icon: '🎯',
    color: '#3b82f6',
    features: ['Full lifecycle', 'AI recommendations', 'Best for new projects'],
  },
  expert: {
    label: 'Expert',
    description: 'Full control over all phases',
    icon: '🔧',
    color: '#8b5cf6',
    features: ['Direct agent access', 'Custom workflows', 'Advanced controls'],
  },
  party: {
    label: 'Party Mode',
    description: 'Watch agents collaborate live',
    icon: '🎉',
    color: '#ec4899',
    features: ['Real-time updates', 'Agent chat view', 'Fun to watch'],
  },
};

export function BMADWizard({ sessionId, onSessionCreated, onClose }: BMADWizardProps) {
  const {
    currentSession,
    isLoading,
    isStreaming,
    error,
    events,
    approvals,
    createSession,
    loadSession,
    startWorkflow,
    advancePhase,
    resolveApproval,
    connectWebSocket,
    clearError,
    getPhaseInfo,
    hasPendingApprovals,
    PHASE_ORDER,
  } = useBMAD();

  const { config, getGitHubUser, createGitHubRepo, isSaving: isCreatingRepo } = useSettings();

  const [step, setStep] = useState<'mode' | 'details' | 'running' | 'approval'>('mode');
  const [selectedMode, setSelectedMode] = useState<BMADMode>('guided');
  const [projectName, setProjectName] = useState('');
  const [description, setDescription] = useState('');
  const [userInput, setUserInput] = useState('');
  const [gitRepo, setGitRepo] = useState('');
  const [showGitConfig, setShowGitConfig] = useState(false);
  const [hoveredMode, setHoveredMode] = useState<BMADMode | null>(null);
  const [githubUser, setGithubUser] = useState<{ login: string; avatar_url: string; name: string | null } | null>(null);
  const [isPrivateRepo, setIsPrivateRepo] = useState(true);
  const [repoCreationStatus, setRepoCreationStatus] = useState<'idle' | 'creating' | 'success' | 'error'>('idle');
  const [repoError, setRepoError] = useState<string | null>(null);

  // Load existing session if sessionId is provided
  useEffect(() => {
    if (sessionId) {
      loadSession(sessionId);
    }
  }, [sessionId, loadSession]);

  // Auto-skip to running step when session is loaded from sessionId prop
  useEffect(() => {
    if (currentSession && sessionId) {
      setSelectedMode(currentSession.mode as BMADMode);
      if (hasPendingApprovals()) {
        setStep('approval');
      } else {
        setStep('running');
      }
    }
  }, [currentSession, sessionId, hasPendingApprovals]);

  useEffect(() => {
    if (currentSession && hasPendingApprovals() && !sessionId) {
      setStep('approval');
    }
  }, [currentSession, hasPendingApprovals, sessionId]);

  // Fetch GitHub user when Git Sync is enabled
  useEffect(() => {
    if (showGitConfig && config?.github?.configured) {
      getGitHubUser().then((response) => {
        if (response?.user) {
          setGithubUser(response.user);
        }
      });
    }
  }, [showGitConfig, config?.github?.configured]);

  const handleModeSelect = (mode: BMADMode) => {
    setSelectedMode(mode);
    setStep('details');
  };

  const handleCreateSession = async () => {
    if (!projectName.trim()) return;

    let repoUrl = gitRepo;

    // Auto-create GitHub repo if Git Sync is enabled and GitHub is configured
    if (showGitConfig && config?.github?.configured && !gitRepo) {
      setRepoCreationStatus('creating');
      setRepoError(null);

      const result = await createGitHubRepo(projectName, description, isPrivateRepo);

      if (result?.success) {
        repoUrl = result.repo_url;
        setGitRepo(repoUrl);
        setRepoCreationStatus('success');
      } else {
        setRepoCreationStatus('error');
        setRepoError('Failed to create repository');
        return; // Don't proceed if repo creation failed
      }
    }

    const session = await createSession(
      projectName,
      description,
      selectedMode,
      repoUrl || undefined
    );

    if (session) {
      onSessionCreated?.(session);
      setStep('running');
    }
  };

  const handleStartWorkflow = async () => {
    if (!currentSession || !userInput.trim()) return;

    if (selectedMode === 'party') {
      connectWebSocket(currentSession.id, userInput);
    } else {
      await startWorkflow(currentSession.id, userInput);
    }
  };

  const handleApproval = async (approval: Approval, approved: boolean, feedback?: string) => {
    if (!currentSession) return;
    await resolveApproval(currentSession.id, approval.id, approved, feedback);
  };

  const handleAdvancePhase = async () => {
    if (!currentSession) return;
    await advancePhase(currentSession.id);
  };

  // Mode Selection Step
  if (step === 'mode') {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
        {/* Header */}
        <div style={{
          borderBottom: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-primary)',
          position: 'sticky',
          top: 0,
          zIndex: 10,
        }}>
          <div style={{ maxWidth: '900px', margin: '0 auto', padding: '16px 24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                <div style={{
                  width: '42px',
                  height: '42px',
                  borderRadius: '12px',
                  background: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '20px',
                }}>
                  🚀
                </div>
                <div>
                  <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    New Project
                  </h1>
                  <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                    Choose your development style
                  </p>
                </div>
              </div>
              {onClose && (
                <button
                  onClick={onClose}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--bg-secondary)',
                    border: '1px solid var(--border-light)',
                    borderRadius: '8px',
                    fontSize: '14px',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Content */}
        <div style={{ maxWidth: '900px', margin: '0 auto', padding: '32px 24px' }}>
          {/* Mode Cards Grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '16px',
          }}>
            {(Object.entries(MODE_INFO) as [BMADMode, typeof MODE_INFO[BMADMode]][]).map(([mode, info]) => (
              <button
                key={mode}
                onClick={() => handleModeSelect(mode)}
                onMouseEnter={() => setHoveredMode(mode)}
                onMouseLeave={() => setHoveredMode(null)}
                style={{
                  padding: '24px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: `2px solid ${hoveredMode === mode ? info.color : 'var(--border-light)'}`,
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                  boxShadow: hoveredMode === mode ? `0 4px 12px ${info.color}20` : 'none',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
                  <div style={{
                    width: '56px',
                    height: '56px',
                    borderRadius: '14px',
                    backgroundColor: `${info.color}15`,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '28px',
                    flexShrink: 0,
                    transition: 'transform 0.15s',
                    transform: hoveredMode === mode ? 'scale(1.05)' : 'scale(1)',
                  }}>
                    {info.icon}
                  </div>
                  <div style={{ flex: 1 }}>
                    <h3 style={{
                      margin: '0 0 4px',
                      fontSize: '18px',
                      fontWeight: 600,
                      color: hoveredMode === mode ? info.color : 'var(--text-primary)',
                      transition: 'color 0.15s',
                    }}>
                      {info.label}
                    </h3>
                    <p style={{
                      margin: '0 0 16px',
                      fontSize: '14px',
                      color: 'var(--text-tertiary)',
                    }}>
                      {info.description}
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      {info.features.map((feature, i) => (
                        <div key={i} style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          fontSize: '13px',
                          color: 'var(--text-secondary)',
                        }}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={info.color} strokeWidth="2.5">
                            <path d="M5 13l4 4L19 7" />
                          </svg>
                          {feature}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '8px',
                    backgroundColor: hoveredMode === mode ? `${info.color}15` : 'var(--bg-tertiary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 0.15s',
                  }}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={hoveredMode === mode ? info.color : 'var(--text-tertiary)'} strokeWidth="2">
                      <path d="M9 5l7 7-7 7" />
                    </svg>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {error && (
            <div style={{
              marginTop: '24px',
              padding: '16px 20px',
              backgroundColor: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
            }}>
              <span style={{ fontSize: '20px' }}>⚠️</span>
              <span style={{ flex: 1, color: '#dc2626', fontSize: '14px' }}>{error}</span>
              <button
                onClick={clearError}
                style={{
                  padding: '4px',
                  backgroundColor: 'transparent',
                  border: 'none',
                  color: '#dc2626',
                  cursor: 'pointer',
                }}
              >
                ×
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Project Details Step
  if (step === 'details') {
    const modeInfo = MODE_INFO[selectedMode];

    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
        {/* Header */}
        <div style={{
          borderBottom: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-primary)',
          position: 'sticky',
          top: 0,
          zIndex: 10,
        }}>
          <div style={{ maxWidth: '680px', margin: '0 auto', padding: '16px 24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <button
                onClick={() => setStep('mode')}
                style={{
                  padding: '8px 12px',
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: '8px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontSize: '14px',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M15 19l-7-7 7-7" />
                </svg>
                Back
              </button>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <div style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '10px',
                  backgroundColor: `${modeInfo.color}15`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '18px',
                }}>
                  {modeInfo.icon}
                </div>
                <div>
                  <h1 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {modeInfo.label} Mode
                  </h1>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Form */}
        <div style={{ maxWidth: '680px', margin: '0 auto', padding: '32px 24px' }}>
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '16px',
            border: '1px solid var(--border-light)',
            padding: '32px',
          }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              {/* Project Name */}
              <div>
                <label style={{
                  display: 'block',
                  fontSize: '14px',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  marginBottom: '8px',
                }}>
                  Project Name <span style={{ color: '#ef4444' }}>*</span>
                </label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="My Awesome App"
                  style={{
                    width: '100%',
                    padding: '14px 16px',
                    border: '1px solid var(--border-light)',
                    borderRadius: '10px',
                    fontSize: '15px',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              {/* Description */}
              <div>
                <label style={{
                  display: 'block',
                  fontSize: '14px',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                  marginBottom: '8px',
                }}>
                  Description <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional)</span>
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Brief description of what you're building..."
                  rows={4}
                  style={{
                    width: '100%',
                    padding: '14px 16px',
                    border: '1px solid var(--border-light)',
                    borderRadius: '10px',
                    fontSize: '15px',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    outline: 'none',
                    resize: 'none',
                    boxSizing: 'border-box',
                    fontFamily: 'inherit',
                  }}
                />
              </div>

              {/* Git Config */}
              <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: '24px' }}>
                <button
                  onClick={() => setShowGitConfig(!showGitConfig)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: 0,
                    color: 'var(--text-secondary)',
                  }}
                >
                  <div style={{
                    width: '20px',
                    height: '20px',
                    borderRadius: '4px',
                    border: `2px solid ${showGitConfig ? modeInfo.color : 'var(--border-light)'}`,
                    backgroundColor: showGitConfig ? modeInfo.color : 'transparent',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 0.15s',
                  }}>
                    {showGitConfig && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3">
                        <path d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>
                  <span style={{ fontWeight: 500, fontSize: '14px' }}>Enable Git Sync</span>
                </button>

                {showGitConfig && (
                  <div style={{
                    marginTop: '16px',
                    padding: '16px',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: '10px',
                  }}>
                    {config?.github?.configured && githubUser ? (
                      <>
                        {/* GitHub User Info */}
                        <div style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '12px',
                          marginBottom: '16px',
                          padding: '12px',
                          backgroundColor: 'var(--bg-primary)',
                          borderRadius: '8px',
                          border: '1px solid #22c55e40',
                        }}>
                          <img
                            src={githubUser.avatar_url}
                            alt={githubUser.login}
                            style={{
                              width: '40px',
                              height: '40px',
                              borderRadius: '50%',
                              border: '2px solid #22c55e',
                            }}
                          />
                          <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 600, fontSize: '14px', color: 'var(--text-primary)' }}>
                              {githubUser.name || githubUser.login}
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                              @{githubUser.login}
                            </div>
                          </div>
                          <div style={{
                            padding: '4px 8px',
                            backgroundColor: '#22c55e20',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            color: '#22c55e',
                          }}>
                            Connected
                          </div>
                        </div>

                        {/* Auto-create repo info */}
                        <div style={{
                          padding: '12px',
                          backgroundColor: `${modeInfo.color}10`,
                          borderRadius: '8px',
                          marginBottom: '12px',
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={modeInfo.color} strokeWidth="2">
                              <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                            </svg>
                            <span style={{ fontWeight: 600, fontSize: '13px', color: 'var(--text-primary)' }}>
                              Auto-create repository
                            </span>
                          </div>
                          <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                            A new repo <strong>&ldquo;{projectName.toLowerCase().replace(/\s+/g, '-') || 'project-name'}&rdquo;</strong> will be created on your GitHub account
                          </p>
                        </div>

                        {/* Privacy toggle */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
                          <button
                            onClick={() => setIsPrivateRepo(true)}
                            style={{
                              flex: 1,
                              padding: '10px',
                              borderRadius: '8px',
                              border: `2px solid ${isPrivateRepo ? modeInfo.color : 'var(--border-light)'}`,
                              backgroundColor: isPrivateRepo ? `${modeInfo.color}10` : 'var(--bg-primary)',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              gap: '6px',
                              fontSize: '13px',
                              fontWeight: 500,
                              color: isPrivateRepo ? modeInfo.color : 'var(--text-secondary)',
                              transition: 'all 0.15s',
                            }}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                            </svg>
                            Private
                          </button>
                          <button
                            onClick={() => setIsPrivateRepo(false)}
                            style={{
                              flex: 1,
                              padding: '10px',
                              borderRadius: '8px',
                              border: `2px solid ${!isPrivateRepo ? modeInfo.color : 'var(--border-light)'}`,
                              backgroundColor: !isPrivateRepo ? `${modeInfo.color}10` : 'var(--bg-primary)',
                              cursor: 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              gap: '6px',
                              fontSize: '13px',
                              fontWeight: 500,
                              color: !isPrivateRepo ? modeInfo.color : 'var(--text-secondary)',
                              transition: 'all 0.15s',
                            }}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="12" cy="12" r="10" />
                              <line x1="2" y1="12" x2="22" y2="12" />
                              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                            </svg>
                            Public
                          </button>
                        </div>

                        <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-tertiary)' }}>
                          Code changes will auto-commit at each phase completion
                        </p>

                        {repoError && (
                          <div style={{
                            marginTop: '12px',
                            padding: '10px 12px',
                            backgroundColor: '#fef2f2',
                            border: '1px solid #fecaca',
                            borderRadius: '8px',
                            fontSize: '13px',
                            color: '#dc2626',
                          }}>
                            {repoError}
                          </div>
                        )}
                      </>
                    ) : (
                      <>
                        {/* GitHub not configured */}
                        <div style={{
                          padding: '16px',
                          backgroundColor: '#fef3c7',
                          borderRadius: '8px',
                          marginBottom: '12px',
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                            <span style={{ fontSize: '16px' }}>⚠️</span>
                            <span style={{ fontWeight: 600, fontSize: '13px', color: '#92400e' }}>
                              GitHub not connected
                            </span>
                          </div>
                          <p style={{ margin: '0 0 12px', fontSize: '12px', color: '#92400e', lineHeight: 1.5 }}>
                            Add your GitHub token in Settings to auto-create repositories
                          </p>
                          <a
                            href="/settings"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '6px',
                              padding: '8px 12px',
                              backgroundColor: '#f59e0b',
                              color: 'white',
                              borderRadius: '6px',
                              fontSize: '12px',
                              fontWeight: 600,
                              textDecoration: 'none',
                            }}
                          >
                            Go to Settings
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M5 12h14M12 5l7 7-7 7" />
                            </svg>
                          </a>
                        </div>

                        {/* Manual URL input fallback */}
                        <label style={{
                          display: 'block',
                          fontSize: '13px',
                          fontWeight: 500,
                          color: 'var(--text-secondary)',
                          marginBottom: '8px',
                        }}>
                          Or enter existing repository URL
                        </label>
                        <input
                          type="text"
                          value={gitRepo}
                          onChange={(e) => setGitRepo(e.target.value)}
                          placeholder="https://github.com/owner/repo"
                          style={{
                            width: '100%',
                            padding: '10px 12px',
                            border: '1px solid var(--border-light)',
                            borderRadius: '8px',
                            fontSize: '14px',
                            backgroundColor: 'var(--bg-primary)',
                            color: 'var(--text-primary)',
                            outline: 'none',
                            boxSizing: 'border-box',
                          }}
                        />
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* Submit Button */}
              <button
                onClick={handleCreateSession}
                disabled={!projectName.trim() || isLoading || isCreatingRepo}
                style={{
                  width: '100%',
                  padding: '16px',
                  borderRadius: '10px',
                  border: 'none',
                  backgroundColor: !projectName.trim() || isLoading || isCreatingRepo ? 'var(--bg-tertiary)' : modeInfo.color,
                  color: !projectName.trim() || isLoading || isCreatingRepo ? 'var(--text-tertiary)' : 'white',
                  fontSize: '15px',
                  fontWeight: 600,
                  cursor: !projectName.trim() || isLoading || isCreatingRepo ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                  transition: 'all 0.15s',
                }}
              >
                {isLoading || repoCreationStatus === 'creating' ? (
                  <>
                    <div style={{
                      width: '18px',
                      height: '18px',
                      border: '2px solid rgba(255,255,255,0.3)',
                      borderTopColor: 'white',
                      borderRadius: '50%',
                      animation: 'spin 1s linear infinite',
                    }} />
                    {repoCreationStatus === 'creating' ? 'Creating Repository...' : 'Creating Project...'}
                  </>
                ) : (
                  <>
                    {showGitConfig && config?.github?.configured && !gitRepo ? (
                      <>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                        </svg>
                        Create Repo & Project
                      </>
                    ) : (
                      <>
                        Create Project
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M13 7l5 5m0 0l-5 5m5-5H6" />
                        </svg>
                      </>
                    )}
                  </>
                )}
              </button>
            </div>
          </div>

          {error && (
            <div style={{
              marginTop: '24px',
              padding: '16px 20px',
              backgroundColor: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
            }}>
              <span style={{ flex: 1, color: '#dc2626', fontSize: '14px' }}>{error}</span>
              <button onClick={clearError} style={{ background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer' }}>×</button>
            </div>
          )}
        </div>

        <style>{`
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
          input::placeholder, textarea::placeholder {
            color: var(--text-tertiary);
          }
        `}</style>
      </div>
    );
  }

  // Running Step - keep similar but with inline styles
  if (step === 'running' && currentSession) {
    const currentPhaseInfo = getPhaseInfo(currentSession.current_phase);
    const modeInfo = MODE_INFO[selectedMode];

    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', padding: '24px' }}>
        <div style={{ maxWidth: '900px', margin: '0 auto' }}>
          {/* Header */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '16px',
            marginBottom: '24px',
          }}>
            <div style={{
              width: '56px',
              height: '56px',
              borderRadius: '14px',
              backgroundColor: `${modeInfo.color}15`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '28px',
            }}>
              {modeInfo.icon}
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {currentSession.project_name}
              </h1>
              <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-tertiary)' }}>
                {currentSession.description || 'No description'}
              </p>
            </div>
          </div>

          {/* Phase Timeline */}
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '16px',
            border: '1px solid var(--border-light)',
            padding: '24px',
            marginBottom: '24px',
          }}>
            <PhaseTimeline
              phases={currentSession.phases as Record<BMADPhase, any>}
              currentPhase={currentSession.current_phase}
            />
          </div>

          {/* Current Phase Card */}
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '16px',
            border: '1px solid var(--border-light)',
            overflow: 'hidden',
          }}>
            <div style={{
              padding: '20px 24px',
              borderBottom: '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-tertiary)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                <div style={{
                  width: '44px',
                  height: '44px',
                  borderRadius: '12px',
                  backgroundColor: 'var(--accent)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'white',
                  fontWeight: 700,
                  fontSize: '18px',
                }}>
                  {(PHASE_ORDER.indexOf(currentSession.current_phase) + 1)}
                </div>
                <div>
                  <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {currentPhaseInfo?.label || currentSession.current_phase}
                  </h2>
                  <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-tertiary)' }}>
                    {currentPhaseInfo?.description}
                  </p>
                </div>
              </div>
            </div>

            <div style={{ padding: '24px' }}>
              {!isStreaming ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  <div>
                    <label style={{
                      display: 'block',
                      fontSize: '14px',
                      fontWeight: 600,
                      color: 'var(--text-primary)',
                      marginBottom: '8px',
                    }}>
                      What would you like to build?
                    </label>
                    <textarea
                      value={userInput}
                      onChange={(e) => setUserInput(e.target.value)}
                      placeholder="Describe your project, feature, or what you want to accomplish..."
                      rows={5}
                      style={{
                        width: '100%',
                        padding: '14px 16px',
                        border: '1px solid var(--border-light)',
                        borderRadius: '10px',
                        fontSize: '15px',
                        backgroundColor: 'var(--bg-primary)',
                        color: 'var(--text-primary)',
                        outline: 'none',
                        resize: 'none',
                        boxSizing: 'border-box',
                        fontFamily: 'inherit',
                      }}
                    />
                  </div>

                  <div style={{ display: 'flex', gap: '12px' }}>
                    <button
                      onClick={handleStartWorkflow}
                      disabled={!userInput.trim() || isLoading}
                      style={{
                        flex: 1,
                        padding: '14px',
                        borderRadius: '10px',
                        border: 'none',
                        backgroundColor: !userInput.trim() || isLoading ? 'var(--bg-tertiary)' : 'var(--accent)',
                        color: !userInput.trim() || isLoading ? 'var(--text-tertiary)' : 'white',
                        fontSize: '15px',
                        fontWeight: 600,
                        cursor: !userInput.trim() || isLoading ? 'not-allowed' : 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '8px',
                      }}
                    >
                      {isLoading ? 'Processing...' : '🚀 Start Building'}
                    </button>
                    <button
                      onClick={handleAdvancePhase}
                      style={{
                        padding: '14px 24px',
                        borderRadius: '10px',
                        border: '1px solid var(--border-light)',
                        backgroundColor: 'var(--bg-primary)',
                        color: 'var(--text-secondary)',
                        fontSize: '15px',
                        fontWeight: 500,
                        cursor: 'pointer',
                      }}
                    >
                      Skip Phase
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '16px',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: '10px',
                    marginBottom: '16px',
                  }}>
                    <div style={{
                      width: '10px',
                      height: '10px',
                      borderRadius: '50%',
                      backgroundColor: 'var(--accent)',
                      animation: 'pulse 1.5s ease-in-out infinite',
                    }} />
                    <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>
                      AI agents are working on your project...
                    </span>
                  </div>

                  <div style={{ maxHeight: '320px', overflowY: 'auto' }}>
                    {events.map((event, index) => (
                      <div key={index} style={{
                        padding: '12px 16px',
                        backgroundColor: 'var(--bg-tertiary)',
                        borderRadius: '8px',
                        marginBottom: '8px',
                        fontSize: '13px',
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                          <span style={{
                            padding: '2px 8px',
                            backgroundColor: 'var(--bg-secondary)',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 600,
                            color: 'var(--text-tertiary)',
                          }}>
                            {event.type}
                          </span>
                          {event.agent && (
                            <span style={{
                              padding: '2px 8px',
                              backgroundColor: `${modeInfo.color}15`,
                              borderRadius: '4px',
                              fontSize: '11px',
                              fontWeight: 600,
                              color: modeInfo.color,
                            }}>
                              {event.agent}
                            </span>
                          )}
                        </div>
                        <p style={{ margin: 0, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                          {JSON.stringify(event.data).slice(0, 150)}...
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {error && (
            <div style={{
              marginTop: '24px',
              padding: '16px 20px',
              backgroundColor: '#fef2f2',
              border: '1px solid #fecaca',
              borderRadius: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
            }}>
              <span style={{ flex: 1, color: '#dc2626', fontSize: '14px' }}>{error}</span>
              <button onClick={clearError} style={{ background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer' }}>×</button>
            </div>
          )}
        </div>

        <style>{`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
          }
          textarea::placeholder {
            color: var(--text-tertiary);
          }
        `}</style>
      </div>
    );
  }

  // Approval Step
  if (step === 'approval' && currentSession) {
    const pendingApprovals = approvals.filter(a => a.status === 'awaiting_approval');

    return (
      <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', padding: '48px 24px' }}>
        <div style={{ maxWidth: '680px', margin: '0 auto' }}>
          <div style={{ textAlign: 'center', marginBottom: '32px' }}>
            <div style={{
              width: '80px',
              height: '80px',
              margin: '0 auto 20px',
              borderRadius: '20px',
              backgroundColor: '#fef3c7',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '40px',
            }}>
              ✋
            </div>
            <h1 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Approval Required
            </h1>
            <p style={{ margin: 0, fontSize: '16px', color: 'var(--text-tertiary)' }}>
              Review the following items before continuing
            </p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {pendingApprovals.map((approval) => (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                onApprove={(feedback) => handleApproval(approval, true, feedback)}
                onReject={(feedback) => handleApproval(approval, false, feedback)}
                isLoading={isLoading}
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return null;
}

// Approval Card Component
interface ApprovalCardProps {
  approval: Approval;
  onApprove: (feedback?: string) => void;
  onReject: (feedback?: string) => void;
  isLoading: boolean;
}

function ApprovalCard({ approval, onApprove, onReject, isLoading }: ApprovalCardProps) {
  const [feedback, setFeedback] = useState('');
  const [showFeedback, setShowFeedback] = useState(false);

  return (
    <div style={{
      backgroundColor: 'var(--bg-secondary)',
      borderRadius: '16px',
      border: '2px solid #fde68a',
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '20px 24px',
        borderBottom: '1px solid var(--border-light)',
        backgroundColor: '#fefce8',
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
          <div style={{
            width: '48px',
            height: '48px',
            borderRadius: '12px',
            backgroundColor: '#fef3c7',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            flexShrink: 0,
          }}>
            ⚠️
          </div>
          <div>
            <h3 style={{ margin: '0 0 4px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
              {approval.description}
            </h3>
            <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-tertiary)' }}>
              Phase: <span style={{ fontWeight: 500, textTransform: 'capitalize' }}>{approval.phase}</span> •
              Agent: <span style={{ fontWeight: 500 }}>{approval.agent}</span>
            </p>
          </div>
        </div>
      </div>

      <div style={{ padding: '20px 24px' }}>
        {showFeedback && (
          <div style={{ marginBottom: '16px' }}>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Add feedback or instructions for the agent..."
              rows={3}
              style={{
                width: '100%',
                padding: '12px 14px',
                border: '1px solid var(--border-light)',
                borderRadius: '8px',
                fontSize: '14px',
                backgroundColor: 'var(--bg-primary)',
                color: 'var(--text-primary)',
                outline: 'none',
                resize: 'none',
                boxSizing: 'border-box',
                fontFamily: 'inherit',
              }}
            />
          </div>
        )}

        <div style={{ display: 'flex', gap: '12px' }}>
          <button
            onClick={() => onApprove(feedback)}
            disabled={isLoading}
            style={{
              flex: 1,
              padding: '12px',
              borderRadius: '8px',
              border: 'none',
              backgroundColor: '#22c55e',
              color: 'white',
              fontSize: '14px',
              fontWeight: 600,
              cursor: isLoading ? 'not-allowed' : 'pointer',
              opacity: isLoading ? 0.5 : 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M5 13l4 4L19 7" />
            </svg>
            Approve
          </button>
          <button
            onClick={() => setShowFeedback(!showFeedback)}
            style={{
              padding: '12px 16px',
              borderRadius: '8px',
              border: '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-secondary)',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            💬
          </button>
          <button
            onClick={() => onReject(feedback)}
            disabled={isLoading}
            style={{
              padding: '12px 16px',
              borderRadius: '8px',
              border: 'none',
              backgroundColor: '#fee2e2',
              color: '#dc2626',
              fontSize: '14px',
              fontWeight: 600,
              cursor: isLoading ? 'not-allowed' : 'pointer',
              opacity: isLoading ? 0.5 : 1,
            }}
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

export default BMADWizard;
