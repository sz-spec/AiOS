'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useBMAD, BMADPhase, BMADAgent, WorkflowEvent } from '@/hooks/useBMAD';
import { PhaseTimeline } from './PhaseTimeline';
import { CollapsibleMessage } from '@/components/shared/CollapsibleMessage';

interface BMADPartyProps {
  sessionId: string;
}

interface AgentMessage {
  agent: string;
  content: string;
  timestamp: Date;
  type: 'message' | 'thinking' | 'artifact' | 'decision';
}

export function BMADParty({ sessionId }: BMADPartyProps) {
  const {
    currentSession,
    agents,
    events,
    isLoading,
    isStreaming,
    error,
    loadSession,
    startWorkflow,
    connectWebSocket,
    disconnectWebSocket,
    resolveApproval,
    clearError,
    PHASE_ORDER,
  } = useBMAD();

  // UI State
  const [userInput, setUserInput] = useState('');
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Track messages in ref for cleanup
  const messagesRef = useRef<AgentMessage[]>([]);
  messagesRef.current = messages;

  // Load session on mount
  useEffect(() => {
    loadSession(sessionId);
    return () => {
      disconnectWebSocket();

      // Summarize conversation on unmount
      const msgs = messagesRef.current;
      if (msgs.length >= 2) {
        const uniqueAgents = Array.from(new Set(msgs.filter((m) => m.agent !== 'user' && m.agent !== 'system').map((m) => m.agent)));
        const firstUserMsg = msgs.find((m) => m.agent === 'user');
        const payload = JSON.stringify({
          messages: msgs.map((m) => ({
            role: m.agent === 'user' ? 'user' : 'assistant',
            content: m.content,
            agent_name: m.agent !== 'user' ? m.agent : undefined,
          })),
          session_type: 'bmad_party',
          session_id: `bmad-party-${sessionId}`,
          agent_ids: [],
          agent_names: uniqueAgents,
          topic_hint: firstUserMsg?.content?.slice(0, 100) || '',
        });

        if (navigator.sendBeacon) {
          navigator.sendBeacon('/api/chat/summarize', new Blob([payload], { type: 'application/json' }));
        } else {
          fetch('/api/chat/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            keepalive: true,
          }).catch(() => {});
        }
      }
    };
  }, [sessionId, loadSession, disconnectWebSocket]);

  // Convert workflow events to messages
  useEffect(() => {
    events.forEach((event) => {
      if (event.type === 'agent_message' && event.agent && event.data.content) {
        setMessages((prev) => [
          ...prev,
          {
            agent: event.agent!,
            content: event.data.content,
            timestamp: new Date(event.timestamp),
            type: 'message',
          },
        ]);
      } else if (event.type === 'agent_started' && event.agent) {
        setMessages((prev) => [
          ...prev,
          {
            agent: event.agent!,
            content: `Starting work on ${event.phase || 'current'} phase...`,
            timestamp: new Date(event.timestamp),
            type: 'thinking',
          },
        ]);
      } else if (event.type === 'artifact_created' && event.agent) {
        setMessages((prev) => [
          ...prev,
          {
            agent: event.agent!,
            content: `Created artifact: ${event.data.artifact_id || 'new artifact'}`,
            timestamp: new Date(event.timestamp),
            type: 'artifact',
          },
        ]);
      } else if (event.type === 'approval_requested') {
        setMessages((prev) => [
          ...prev,
          {
            agent: 'system',
            content: `Approval needed: ${event.data.message || 'Please review'}`,
            timestamp: new Date(event.timestamp),
            type: 'decision',
          },
        ]);
      }
    });
  }, [events]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleStart = () => {
    if (!userInput.trim() || !currentSession) return;

    // Add user message
    setMessages((prev) => [
      ...prev,
      {
        agent: 'user',
        content: userInput,
        timestamp: new Date(),
        type: 'message',
      },
    ]);

    // Connect WebSocket for streaming
    connectWebSocket(currentSession.id, userInput);
    setUserInput('');
  };

  const getAgentInfo = (role: string): BMADAgent | undefined => {
    return agents.find((a) => a.role === role || a.name.toLowerCase().includes(role.toLowerCase()));
  };

  if (!currentSession) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', backgroundColor: 'var(--bg-primary)' }}>
        {isLoading ? (
          <div style={{
            width: '32px', height: '32px',
            border: '4px solid var(--border-light)',
            borderTopColor: '#a855f7',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
          }} />
        ) : (
          <div style={{ color: 'var(--text-tertiary)' }}>Session not found</div>
        )}
      </div>
    );
  }

  return (
    <div style={{ height: '100vh', backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        borderBottom: '1px solid var(--border-light)',
        padding: '16px 24px',
        backgroundColor: 'var(--bg-secondary)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', maxWidth: '1200px', margin: '0 auto' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
              {currentSession.project_name}
              <span style={{ color: '#a855f7', fontSize: '16px', fontWeight: 500 }}>Party Mode</span>
            </h1>
            <p style={{ margin: '2px 0 0', fontSize: '13px', color: 'var(--text-tertiary)' }}>Watch the agents collaborate in real-time</p>
          </div>

          {isStreaming && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#a855f7' }}>
              <span style={{
                width: '10px', height: '10px',
                borderRadius: '50%',
                backgroundColor: '#a855f7',
                animation: 'pulse 1.5s ease-in-out infinite',
              }} />
              <span style={{ fontSize: '13px', fontWeight: 500 }}>Live</span>
            </div>
          )}
        </div>
      </div>

      {/* Phase Timeline */}
      <div style={{ padding: '12px 24px', backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-light)' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
          <PhaseTimeline
            phases={currentSession.phases as Record<BMADPhase, any>}
            currentPhase={currentSession.current_phase}
          />
        </div>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Agent Sidebar */}
        <div style={{
          width: '240px',
          borderRight: '1px solid var(--border-light)',
          padding: '16px',
          overflowY: 'auto',
          backgroundColor: 'var(--bg-secondary)',
          flexShrink: 0,
        }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Active Agents
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {agents
              .filter((a) => a.phases.includes(currentSession.current_phase))
              .map((agent) => {
                const isTyping = messages.some(
                  (m) =>
                    m.agent === agent.role &&
                    m.type === 'thinking' &&
                    new Date().getTime() - m.timestamp.getTime() < 5000
                );

                return (
                  <div
                    key={agent.role}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      padding: '12px',
                      borderRadius: '10px',
                      backgroundColor: 'var(--bg-primary)',
                      border: `1px solid ${agent.color}30`,
                    }}
                  >
                    <span style={{ fontSize: '24px', marginRight: '12px' }}>{agent.avatar}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500, color: 'var(--text-primary)', fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {agent.name}
                      </div>
                      {isTyping ? (
                        <div style={{ fontSize: '12px', color: '#a855f7' }}>typing...</div>
                      ) : (
                        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {agent.description.slice(0, 30)}...
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
          </div>

          {/* Inactive Agents */}
          <h3 style={{ margin: '24px 0 12px', fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Other Agents
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {agents
              .filter((a) => !a.phases.includes(currentSession.current_phase))
              .slice(0, 5)
              .map((agent) => (
                <div key={agent.role} style={{ display: 'flex', alignItems: 'center', padding: '8px', opacity: 0.4 }}>
                  <span style={{ marginRight: '8px' }}>{agent.avatar}</span>
                  <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {agent.name}
                  </span>
                </div>
              ))}
          </div>
        </div>

        {/* Chat/Activity Feed */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {messages.length === 0 && !isStreaming && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-tertiary)' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '40px', marginBottom: '16px' }}>&#127880;</div>
                  <p style={{ margin: 0, fontSize: '14px' }}>Start the party by describing your project below</p>
                </div>
              </div>
            )}

            {messages.map((msg, index) => {
              const agent = getAgentInfo(msg.agent);
              const isUser = msg.agent === 'user';
              const isSystem = msg.agent === 'system';

              return (
                <div
                  key={index}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '12px',
                    flexDirection: isUser ? 'row-reverse' : 'row',
                  }}
                >
                  {/* Avatar */}
                  <div
                    style={{
                      width: '40px',
                      height: '40px',
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                      fontSize: '18px',
                      backgroundColor: isUser
                        ? 'var(--accent)'
                        : isSystem
                          ? '#fef3c7'
                          : agent ? agent.color + '20' : 'var(--bg-tertiary)',
                    }}
                  >
                    {isUser ? '&#128100;' : isSystem ? '&#9888;&#65039;' : agent?.avatar || '&#129302;'}
                  </div>

                  {/* Message Content */}
                  <div style={{ maxWidth: '70%', textAlign: isUser ? 'right' : 'left' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
                      <span style={{ fontWeight: 500, fontSize: '13px', color: isUser ? 'var(--accent)' : 'var(--text-secondary)' }}>
                        {isUser ? 'You' : isSystem ? 'System' : agent?.name || msg.agent}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                        {msg.timestamp.toLocaleTimeString()}
                      </span>
                    </div>

                    <div
                      style={{
                        borderRadius: '16px',
                        padding: '12px 16px',
                        fontSize: '14px',
                        lineHeight: 1.5,
                        ...(isUser
                          ? { backgroundColor: 'var(--accent)', color: 'white' }
                          : isSystem
                            ? { backgroundColor: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }
                            : msg.type === 'thinking'
                              ? { backgroundColor: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', fontStyle: 'italic' }
                              : msg.type === 'artifact'
                                ? { backgroundColor: '#f3e8ff', color: '#7c3aed', border: '1px solid #e9d5ff' }
                                : { backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border-light)' }
                        ),
                      }}
                    >
                      <CollapsibleMessage
                        content={msg.content}
                        isAssistant={!isUser && !isSystem}
                        agentName={agent?.name || msg.agent}
                      />
                    </div>
                  </div>
                </div>
              );
            })}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Area */}
          <div style={{
            borderTop: '1px solid var(--border-light)',
            padding: '16px 24px',
            backgroundColor: 'var(--bg-secondary)',
          }}>
            <div style={{ maxWidth: '800px', margin: '0 auto', display: 'flex', gap: '12px' }}>
              <input
                type="text"
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleStart()}
                placeholder={isStreaming ? 'Agents are working...' : 'Describe your project or give instructions...'}
                disabled={isStreaming}
                style={{
                  flex: 1,
                  padding: '12px 20px',
                  border: '1px solid var(--border-light)',
                  borderRadius: '24px',
                  fontSize: '14px',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                  outline: 'none',
                  opacity: isStreaming ? 0.5 : 1,
                }}
              />
              <button
                onClick={handleStart}
                disabled={!userInput.trim() || isStreaming}
                style={{
                  padding: '12px 24px',
                  borderRadius: '24px',
                  border: 'none',
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: !userInput.trim() || isStreaming ? 'not-allowed' : 'pointer',
                  backgroundColor: !userInput.trim() || isStreaming ? 'var(--bg-tertiary)' : '#a855f7',
                  color: !userInput.trim() || isStreaming ? 'var(--text-tertiary)' : 'white',
                  transition: 'all 0.15s',
                }}
              >
                {isStreaming ? 'Working...' : 'Start'}
              </button>
            </div>
          </div>
        </div>

        {/* Decision Panel */}
        <div style={{
          width: '240px',
          borderLeft: '1px solid var(--border-light)',
          padding: '16px',
          overflowY: 'auto',
          backgroundColor: 'var(--bg-secondary)',
          flexShrink: 0,
        }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Decision Board
          </h3>

          {events.filter((e) => e.type === 'approval_requested').length === 0 ? (
            <p style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>No pending decisions</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {events
                .filter((e) => e.type === 'approval_requested')
                .map((event, index) => (
                  <div key={index} style={{
                    padding: '12px',
                    backgroundColor: '#fef3c7',
                    border: '1px solid #fde68a',
                    borderRadius: '10px',
                  }}>
                    <p style={{ margin: '0 0 8px', fontSize: '13px', color: '#92400e' }}>{event.data.message}</p>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        onClick={() => resolveApproval(sessionId, event.data.approval_id, true)}
                        style={{
                          flex: 1,
                          padding: '6px',
                          backgroundColor: '#22c55e',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '12px',
                          fontWeight: 500,
                          cursor: 'pointer',
                        }}
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => resolveApproval(sessionId, event.data.approval_id, false)}
                        style={{
                          flex: 1,
                          padding: '6px',
                          backgroundColor: '#fee2e2',
                          color: '#dc2626',
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '12px',
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

          {/* Recent Artifacts */}
          <h3 style={{ margin: '24px 0 12px', fontSize: '12px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Recent Artifacts
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {events
              .filter((e) => e.type === 'artifact_created')
              .slice(-5)
              .map((event, index) => (
                <div key={index} style={{
                  padding: '8px 12px',
                  backgroundColor: '#f3e8ff',
                  border: '1px solid #e9d5ff',
                  borderRadius: '8px',
                  fontSize: '13px',
                  color: '#7c3aed',
                }}>
                  {event.data.artifact_id || 'New Artifact'}
                </div>
              ))}
            {events.filter((e) => e.type === 'artifact_created').length === 0 && (
              <p style={{ color: 'var(--text-tertiary)', fontSize: '13px' }}>No artifacts yet</p>
            )}
          </div>
        </div>
      </div>

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
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        input::placeholder {
          color: var(--text-tertiary);
        }
      `}</style>
    </div>
  );
}

export default BMADParty;
