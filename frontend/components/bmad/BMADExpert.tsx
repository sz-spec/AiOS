'use client';

import React, { useState, useEffect } from 'react';
import { useBMAD, BMADPhase, BMADAgent, BMADSession, Artifact, Approval } from '@/hooks/useBMAD';
import { PhaseTimeline } from './PhaseTimeline';

interface BMADExpertProps {
  sessionId: string;
}

export function BMADExpert({ sessionId }: BMADExpertProps) {
  const {
    currentSession,
    agents,
    artifacts,
    approvals,
    isLoading,
    error,
    loadSession,
    startWorkflow,
    advancePhase,
    resolveApproval,
    sendAgentMessage,
    createCommit,
    deploy,
    clearError,
    getAgentsForPhase,
    PHASE_ORDER,
  } = useBMAD();

  // UI State
  const [activeTab, setActiveTab] = useState<'overview' | 'agents' | 'artifacts' | 'git' | 'deploy'>('overview');
  const [selectedAgent, setSelectedAgent] = useState<BMADAgent | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [agentMessage, setAgentMessage] = useState('');
  const [commitMessage, setCommitMessage] = useState('');

  // Load session on mount
  useEffect(() => {
    loadSession(sessionId);
  }, [sessionId, loadSession]);

  if (!currentSession) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '256px' }}>
        {isLoading ? (
          <div style={{
            width: '32px', height: '32px',
            border: '4px solid var(--border-light)',
            borderTopColor: 'var(--accent)',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
          }} />
        ) : (
          <div style={{ color: 'var(--text-tertiary)' }}>Session not found</div>
        )}
      </div>
    );
  }

  const phaseAgents = getAgentsForPhase(currentSession.current_phase);
  const phaseArtifacts = artifacts.filter(a => a.phase === currentSession.current_phase);
  const pendingApprovals = approvals.filter(a => a.status === 'awaiting_approval');

  const tabs = ['overview', 'agents', 'artifacts', 'git', 'deploy'] as const;

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 600, color: 'var(--text-primary)' }}>
            {currentSession.project_name}
          </h1>
          <p style={{ margin: '4px 0 0', fontSize: '14px', color: 'var(--text-tertiary)' }}>
            Expert Mode &bull; {currentSession.current_phase}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span style={{
            padding: '6px 12px',
            borderRadius: '20px',
            fontSize: '13px',
            fontWeight: 500,
            backgroundColor: currentSession.git_enabled ? '#dcfce7' : 'var(--bg-tertiary)',
            color: currentSession.git_enabled ? '#15803d' : 'var(--text-tertiary)',
          }}>
            Git: {currentSession.git_enabled ? 'Enabled' : 'Disabled'}
          </span>
          {currentSession.deployment_url && (
            <a
              href={currentSession.deployment_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                padding: '6px 12px',
                borderRadius: '20px',
                fontSize: '13px',
                fontWeight: 500,
                backgroundColor: `var(--accent)15`,
                color: 'var(--accent)',
                textDecoration: 'none',
              }}
            >
              View Deployment
            </a>
          )}
        </div>
      </div>

      {/* Phase Timeline */}
      <div style={{
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: '16px',
        border: '1px solid var(--border-light)',
        padding: '20px 24px',
        marginBottom: '24px',
      }}>
        <PhaseTimeline
          phases={currentSession.phases as Record<BMADPhase, any>}
          currentPhase={currentSession.current_phase}
          onPhaseClick={(phase) => advancePhase(sessionId, phase)}
        />
      </div>

      {/* Pending Approvals Banner */}
      {pendingApprovals.length > 0 && (
        <div style={{
          padding: '16px 20px',
          backgroundColor: '#fef3c7',
          border: '1px solid #fde68a',
          borderRadius: '12px',
          marginBottom: '24px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}>
          <span style={{ fontSize: '16px' }}>⚠️</span>
          <span style={{ fontWeight: 500, color: '#92400e', fontSize: '14px' }}>
            {pendingApprovals.length} pending approval(s)
          </span>
          <button
            onClick={() => setActiveTab('overview')}
            style={{
              background: 'none', border: 'none',
              color: '#92400e', textDecoration: 'underline',
              fontSize: '13px', cursor: 'pointer',
            }}
          >
            Review now
          </button>
        </div>
      )}

      {/* Tab Navigation */}
      <div style={{
        display: 'flex',
        gap: '4px',
        marginBottom: '24px',
        padding: '4px',
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: '10px',
        width: 'fit-content',
      }}>
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              border: 'none',
              backgroundColor: activeTab === tab ? 'var(--bg-primary)' : 'transparent',
              color: activeTab === tab ? 'var(--text-primary)' : 'var(--text-tertiary)',
              fontSize: '14px',
              fontWeight: 500,
              cursor: 'pointer',
              boxShadow: activeTab === tab ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              textTransform: 'capitalize',
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div>
        {/* Overview Tab */}
        {activeTab === 'overview' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            {/* Current Phase Panel */}
            <div style={{
              padding: '24px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '16px',
              border: '1px solid var(--border-light)',
            }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Current Phase
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Phase</span>
                  <p style={{ margin: '4px 0 0', fontWeight: 500, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                    {currentSession.current_phase}
                  </p>
                </div>
                <div>
                  <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Status</span>
                  <p style={{ margin: '4px 0 0', fontWeight: 500, color: 'var(--text-primary)', textTransform: 'capitalize' }}>
                    {currentSession.phases[currentSession.current_phase]?.status || 'pending'}
                  </p>
                </div>
                <div>
                  <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Active Agents</span>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '8px' }}>
                    {phaseAgents.map((agent) => (
                      <span
                        key={agent.role}
                        style={{
                          padding: '4px 10px',
                          borderRadius: '6px',
                          fontSize: '13px',
                          backgroundColor: agent.color + '20',
                          color: 'var(--text-primary)',
                        }}
                      >
                        {agent.avatar} {agent.name}
                      </span>
                    ))}
                  </div>
                </div>
                <div style={{ paddingTop: '8px' }}>
                  <button
                    onClick={() => advancePhase(sessionId)}
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
                    Advance Phase
                  </button>
                </div>
              </div>
            </div>

            {/* Approvals Panel */}
            <div style={{
              padding: '24px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '16px',
              border: '1px solid var(--border-light)',
            }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Approvals
              </h3>
              {pendingApprovals.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>No pending approvals</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {pendingApprovals.map((approval) => (
                    <div key={approval.id} style={{
                      padding: '16px',
                      backgroundColor: '#fef3c7',
                      borderRadius: '10px',
                    }}>
                      <p style={{ margin: '0 0 4px', fontWeight: 500, color: 'var(--text-primary)', fontSize: '14px' }}>
                        {approval.description}
                      </p>
                      <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-tertiary)' }}>
                        {approval.agent} &bull; {approval.phase}
                      </p>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <button
                          onClick={() => resolveApproval(sessionId, approval.id, true)}
                          style={{
                            padding: '6px 14px',
                            backgroundColor: '#22c55e',
                            color: 'white',
                            border: 'none',
                            borderRadius: '6px',
                            fontSize: '13px',
                            fontWeight: 500,
                            cursor: 'pointer',
                          }}
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => resolveApproval(sessionId, approval.id, false)}
                          style={{
                            padding: '6px 14px',
                            backgroundColor: '#fee2e2',
                            color: '#dc2626',
                            border: 'none',
                            borderRadius: '6px',
                            fontSize: '13px',
                            fontWeight: 500,
                            cursor: 'pointer',
                          }}
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Quick Stats */}
            <div style={{
              padding: '24px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '16px',
              border: '1px solid var(--border-light)',
              gridColumn: 'span 2',
            }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Statistics
              </h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
                {[
                  { label: 'Artifacts', value: artifacts.length },
                  { label: 'Approvals', value: approvals.length },
                  { label: 'Phase Progress', value: `${PHASE_ORDER.indexOf(currentSession.current_phase) + 1}/${PHASE_ORDER.length}` },
                  { label: 'Git Sync', value: currentSession.git_enabled ? 'Yes' : 'No' },
                ].map((stat, i) => (
                  <div key={i} style={{
                    textAlign: 'center',
                    padding: '16px',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: '10px',
                  }}>
                    <div style={{ fontSize: '28px', fontWeight: 600, color: 'var(--text-primary)' }}>{stat.value}</div>
                    <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '4px' }}>{stat.label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Agents Tab */}
        {activeTab === 'agents' && (
          <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: '16px' }}>
            {/* Agent List */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {agents.map((agent) => {
                const isActive = agent.phases.includes(currentSession.current_phase);
                const isSelected = selectedAgent?.role === agent.role;
                return (
                  <button
                    key={agent.role}
                    onClick={() => setSelectedAgent(agent)}
                    style={{
                      width: '100%',
                      padding: '16px',
                      borderRadius: '12px',
                      textAlign: 'left',
                      border: isSelected ? '2px solid var(--accent)' : '1px solid var(--border-light)',
                      backgroundColor: isSelected ? `var(--accent)08` : 'var(--bg-secondary)',
                      cursor: 'pointer',
                      opacity: isActive ? 1 : 0.5,
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center' }}>
                      <span style={{ fontSize: '24px', marginRight: '12px' }}>{agent.avatar}</span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 500, color: 'var(--text-primary)', fontSize: '14px' }}>{agent.name}</div>
                        <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{agent.role}</div>
                      </div>
                      {isActive && (
                        <span style={{
                          padding: '3px 8px',
                          backgroundColor: '#dcfce7',
                          color: '#15803d',
                          fontSize: '11px',
                          fontWeight: 500,
                          borderRadius: '10px',
                        }}>
                          Active
                        </span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>

            {/* Agent Details */}
            <div>
              {selectedAgent ? (
                <div style={{
                  padding: '24px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: '1px solid var(--border-light)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '24px' }}>
                    <span style={{ fontSize: '40px', marginRight: '16px' }}>{selectedAgent.avatar}</span>
                    <div>
                      <h3 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {selectedAgent.name}
                      </h3>
                      <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>
                        {selectedAgent.description}
                      </p>
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
                    <div>
                      <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-tertiary)' }}>Phases</span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '8px' }}>
                        {selectedAgent.phases.map((phase) => (
                          <span key={phase} style={{
                            padding: '3px 8px',
                            backgroundColor: 'var(--bg-tertiary)',
                            borderRadius: '4px',
                            fontSize: '12px',
                            textTransform: 'capitalize',
                            color: 'var(--text-secondary)',
                          }}>
                            {phase}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-tertiary)' }}>Artifacts</span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '8px' }}>
                        {selectedAgent.artifacts.map((artifact) => (
                          <span key={artifact} style={{
                            padding: '3px 8px',
                            backgroundColor: `var(--accent)15`,
                            borderRadius: '4px',
                            fontSize: '12px',
                            color: 'var(--accent)',
                          }}>
                            {artifact}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Message Agent */}
                  <div style={{ borderTop: '1px solid var(--border-light)', paddingTop: '20px' }}>
                    <h4 style={{ margin: '0 0 12px', fontWeight: 500, fontSize: '14px', color: 'var(--text-primary)' }}>
                      Send Message
                    </h4>
                    <textarea
                      value={agentMessage}
                      onChange={(e) => setAgentMessage(e.target.value)}
                      placeholder={`Ask ${selectedAgent.name} something...`}
                      rows={3}
                      style={{
                        width: '100%',
                        padding: '12px 14px',
                        border: '1px solid var(--border-light)',
                        borderRadius: '10px',
                        fontSize: '14px',
                        backgroundColor: 'var(--bg-primary)',
                        color: 'var(--text-primary)',
                        outline: 'none',
                        resize: 'none',
                        boxSizing: 'border-box',
                        fontFamily: 'inherit',
                      }}
                    />
                    <button
                      onClick={async () => {
                        if (agentMessage.trim()) {
                          await sendAgentMessage(sessionId, selectedAgent.role, agentMessage);
                          setAgentMessage('');
                        }
                      }}
                      disabled={!agentMessage.trim() || isLoading}
                      style={{
                        marginTop: '8px',
                        padding: '10px 20px',
                        backgroundColor: !agentMessage.trim() || isLoading ? 'var(--bg-tertiary)' : 'var(--accent)',
                        color: !agentMessage.trim() || isLoading ? 'var(--text-tertiary)' : 'white',
                        border: 'none',
                        borderRadius: '8px',
                        fontSize: '14px',
                        fontWeight: 500,
                        cursor: !agentMessage.trim() || isLoading ? 'not-allowed' : 'pointer',
                      }}
                    >
                      Send
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '256px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: '1px solid var(--border-light)',
                }}>
                  <p style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>Select an agent to view details</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Artifacts Tab */}
        {activeTab === 'artifacts' && (
          <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: '16px' }}>
            {/* Artifact List */}
            <div>
              {artifacts.length === 0 ? (
                <p style={{ color: 'var(--text-tertiary)', padding: '16px', fontSize: '14px' }}>No artifacts yet</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {artifacts.map((artifact) => (
                    <button
                      key={artifact.id}
                      onClick={() => setSelectedArtifact(artifact)}
                      style={{
                        width: '100%',
                        padding: '16px',
                        borderRadius: '12px',
                        textAlign: 'left',
                        border: selectedArtifact?.id === artifact.id ? '2px solid var(--accent)' : '1px solid var(--border-light)',
                        backgroundColor: selectedArtifact?.id === artifact.id ? `var(--accent)08` : 'var(--bg-secondary)',
                        cursor: 'pointer',
                        transition: 'all 0.15s',
                      }}
                    >
                      <div style={{ fontWeight: 500, color: 'var(--text-primary)', fontSize: '14px' }}>{artifact.name}</div>
                      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                        {artifact.type} &bull; {artifact.phase}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Artifact Details */}
            <div>
              {selectedArtifact ? (
                <div style={{
                  padding: '24px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: '1px solid var(--border-light)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {selectedArtifact.name}
                    </h3>
                    <span style={{
                      padding: '4px 10px',
                      backgroundColor: 'var(--bg-tertiary)',
                      borderRadius: '6px',
                      fontSize: '12px',
                      color: 'var(--text-secondary)',
                    }}>
                      {selectedArtifact.type}
                    </span>
                  </div>
                  <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '16px' }}>
                    Agent: {selectedArtifact.agent} &bull; Phase: {selectedArtifact.phase}
                  </div>
                  <div style={{
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: '10px',
                    padding: '16px',
                    overflow: 'auto',
                    maxHeight: '384px',
                  }}>
                    <pre style={{
                      margin: 0,
                      fontSize: '13px',
                      color: 'var(--text-primary)',
                      whiteSpace: 'pre-wrap',
                      fontFamily: 'monospace',
                    }}>
                      {typeof selectedArtifact.content === 'string'
                        ? selectedArtifact.content
                        : JSON.stringify(selectedArtifact.content, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: '256px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: '1px solid var(--border-light)',
                }}>
                  <p style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>Select an artifact to view details</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Git Tab */}
        {activeTab === 'git' && (
          <div style={{ maxWidth: '640px' }}>
            <div style={{
              padding: '24px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '16px',
              border: '1px solid var(--border-light)',
            }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Git Operations
              </h3>

              {!currentSession.git_enabled ? (
                <p style={{ color: 'var(--text-tertiary)', fontSize: '14px' }}>Git is not enabled for this session</p>
              ) : (
                <div>
                  <label style={{
                    display: 'block',
                    fontSize: '14px',
                    fontWeight: 500,
                    color: 'var(--text-primary)',
                    marginBottom: '8px',
                  }}>
                    Commit Message
                  </label>
                  <input
                    type="text"
                    value={commitMessage}
                    onChange={(e) => setCommitMessage(e.target.value)}
                    placeholder="feat: add new feature"
                    style={{
                      width: '100%',
                      padding: '12px 14px',
                      border: '1px solid var(--border-light)',
                      borderRadius: '10px',
                      fontSize: '14px',
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-primary)',
                      outline: 'none',
                      boxSizing: 'border-box',
                    }}
                  />
                  <button
                    onClick={async () => {
                      if (commitMessage.trim()) {
                        await createCommit(sessionId, commitMessage);
                        setCommitMessage('');
                      }
                    }}
                    disabled={!commitMessage.trim() || isLoading}
                    style={{
                      marginTop: '12px',
                      padding: '10px 20px',
                      backgroundColor: !commitMessage.trim() || isLoading ? 'var(--bg-tertiary)' : '#1f2937',
                      color: !commitMessage.trim() || isLoading ? 'var(--text-tertiary)' : 'white',
                      border: 'none',
                      borderRadius: '8px',
                      fontSize: '14px',
                      fontWeight: 500,
                      cursor: !commitMessage.trim() || isLoading ? 'not-allowed' : 'pointer',
                    }}
                  >
                    Create Commit
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Deploy Tab */}
        {activeTab === 'deploy' && (
          <div style={{ maxWidth: '640px' }}>
            <div style={{
              padding: '24px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: '16px',
              border: '1px solid var(--border-light)',
            }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Deployment
              </h3>

              {currentSession.deployment_url && (
                <div style={{
                  padding: '16px',
                  backgroundColor: '#dcfce7',
                  borderRadius: '10px',
                  marginBottom: '16px',
                }}>
                  <span style={{ fontSize: '13px', color: '#15803d' }}>Current deployment:</span>
                  <a
                    href={currentSession.deployment_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: 'block',
                      fontWeight: 500,
                      color: '#166534',
                      textDecoration: 'none',
                      marginTop: '4px',
                    }}
                  >
                    {currentSession.deployment_url}
                  </a>
                </div>
              )}

              <button
                onClick={() => deploy(sessionId)}
                disabled={isLoading}
                style={{
                  padding: '12px 24px',
                  backgroundColor: isLoading ? 'var(--bg-tertiary)' : 'var(--accent)',
                  color: isLoading ? 'var(--text-tertiary)' : 'white',
                  border: 'none',
                  borderRadius: '8px',
                  fontSize: '14px',
                  fontWeight: 500,
                  cursor: isLoading ? 'not-allowed' : 'pointer',
                }}
              >
                {isLoading ? 'Deploying...' : 'Deploy to Vercel'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Error Display */}
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
        input::placeholder, textarea::placeholder {
          color: var(--text-tertiary);
        }
      `}</style>
    </div>
  );
}

export default BMADExpert;
