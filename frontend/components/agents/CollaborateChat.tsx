'use client';

import React, { useRef, useEffect } from 'react';
import { AgentAvatar } from './AgentAvatar';
import { VoiceInput } from '@/components/shared/VoiceInput';
import { CollapsibleMessage } from '@/components/shared/CollapsibleMessage';

/** Detect text direction based on first meaningful character */
function detectDir(text: string): 'rtl' | 'ltr' {
  const rtlRange = /[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0780-\u07BF\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
  const stripped = text.replace(/^[\s\d\W]+/, '');
  return rtlRange.test(stripped.charAt(0)) ? 'rtl' : 'ltr';
}

export interface CollabMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  model?: string;
  provider?: string;
  agentId?: string;
  agentName?: string;
  agentColor?: string;
  isPass?: boolean;
}

export interface CollabDecision {
  id: string;
  text: string;
  agentId: string;
  agentName: string;
  agentColor: string;
  messageId: string;
  timestamp: Date;
  status: 'pending' | 'approved' | 'changed';
  override?: string;
}

interface CollabAgent {
  id: string;
  name: string;
  role: string;
  color: string;
}

interface CollaborateChatProps {
  agents: CollabAgent[];
  collaborateAgents: string[];
  messages: CollabMessage[];
  decisions: CollabDecision[];
  isLoading: boolean;
  respondingAgentId: string | null;
  onSend: (content: string) => void;
  onExit: () => void;
  onApproveDecision: (id: string) => void;
  onChangeDecision: (id: string, newText: string) => void;
  chatInput: string;
  setChatInput: (v: string) => void;
  onVoiceTranscript: (t: string) => void;
}

export function CollaborateChat({
  agents,
  collaborateAgents,
  messages,
  decisions,
  isLoading,
  respondingAgentId,
  onSend,
  onExit,
  onApproveDecision,
  onChangeDecision,
  chatInput,
  setChatInput,
  onVoiceTranscript,
}: CollaborateChatProps) {
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [editingDecision, setEditingDecision] = React.useState<string | null>(null);
  const [editText, setEditText] = React.useState('');

  const participants = agents.filter((a) => collaborateAgents.includes(a.id));
  const respondingAgent = respondingAgentId ? agents.find((a) => a.id === respondingAgentId) : null;

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, respondingAgentId]);

  const visibleMessages = messages.filter((m) => !m.isPass && m.role !== 'system');

  const statusColors = {
    pending: { bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.3)', text: '#F59E0B' },
    approved: { bg: 'rgba(34, 197, 94, 0.1)', border: 'rgba(34, 197, 94, 0.3)', text: '#22C55E' },
    changed: { bg: 'rgba(99, 102, 241, 0.1)', border: 'rgba(99, 102, 241, 0.3)', text: '#6366F1' },
  };

  // Process VOS directives from agent messages — fire-and-forget
  const processVosDirective = React.useCallback((directive: string, agentId: string) => {
    fetch('/api/vos/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: directive,
        source: 'agent',
        source_id: agentId,
      }),
    }).catch(() => {});
  }, []);

  // Render a line of message content, handling [FACT], [DECISION], and [VOS:...] prefixes
  const renderLine = (line: string, idx: number, totalLines: number, msg: CollabMessage) => {
    const trimmed = line.trim();
    const isFact = /^\[FACT\]/i.test(trimmed);
    const isDecision = /^\[DECISION\]/i.test(trimmed);

    // Handle VOS directives — strip from display and fire-and-forget
    const vosMatch = trimmed.match(/^\[VOS:(.+)\]$/i);
    if (vosMatch) {
      if (msg.agentId) {
        processVosDirective(vosMatch[1], msg.agentId);
      }
      // Don't render VOS directives
      return <span key={idx}>{idx < totalLines - 1 && '\n'}</span>;
    }
    const cleanLine = isFact
      ? line.replace(/^\[FACT\]\s*/i, '')
      : isDecision
        ? line.replace(/^\[DECISION\]\s*/i, '')
        : line;

    if (isDecision) {
      const decision = decisions.find((d) => d.messageId === msg.id && d.text === cleanLine.trim());
      return (
        <span key={idx}>
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            padding: '4px 10px',
            backgroundColor: 'rgba(139, 92, 246, 0.08)',
            border: '1px solid rgba(139, 92, 246, 0.2)',
            borderRadius: '6px',
            margin: '4px 0',
          }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
            <span style={{ fontWeight: 500 }}>{cleanLine}</span>
            {decision && decision.status === 'pending' && (
              <>
                <button
                  onClick={() => onApproveDecision(decision.id)}
                  style={{
                    padding: '2px 8px',
                    backgroundColor: 'rgba(34, 197, 94, 0.15)',
                    border: '1px solid rgba(34, 197, 94, 0.3)',
                    borderRadius: '4px',
                    color: '#22C55E',
                    fontSize: '10px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    marginLeft: '4px',
                  }}
                >
                  Approve
                </button>
                <button
                  onClick={() => { setEditingDecision(decision.id); setEditText(decision.text); }}
                  style={{
                    padding: '2px 8px',
                    backgroundColor: 'rgba(99, 102, 241, 0.15)',
                    border: '1px solid rgba(99, 102, 241, 0.3)',
                    borderRadius: '4px',
                    color: '#6366F1',
                    fontSize: '10px',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Change
                </button>
              </>
            )}
            {decision && decision.status === 'approved' && (
              <span style={{ fontSize: '10px', color: '#22C55E', fontWeight: 600 }}>Approved</span>
            )}
            {decision && decision.status === 'changed' && (
              <span style={{ fontSize: '10px', color: '#6366F1', fontWeight: 600 }}>Changed</span>
            )}
          </span>
          {idx < totalLines - 1 && '\n'}
        </span>
      );
    }

    if (isFact) {
      return (
        <span key={idx}>
          <span style={{ borderLeft: '2px solid #8B5CF6', paddingLeft: '6px', display: 'inline-block' }}>
            {cleanLine}
          </span>
          {idx < totalLines - 1 && '\n'}
        </span>
      );
    }

    return (
      <span key={idx}>
        {cleanLine}
        {idx < totalLines - 1 && '\n'}
      </span>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '12px 24px',
        borderBottom: '1px solid var(--border-light)',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}>
        <button
          onClick={onExit}
          style={{
            padding: '6px 10px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back
        </button>

        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Collaboration
          </div>
        </div>

        {/* Participant avatars */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          {participants.map((agent) => (
            <div key={agent.id} title={`${agent.name} (${agent.role})`}>
              <AgentAvatar name={agent.name} color={agent.color} size="sm" />
            </div>
          ))}
        </div>

        <button
          onClick={onExit}
          style={{
            padding: '6px 12px',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.2)',
            borderRadius: 'var(--radius-md)',
            color: '#EF4444',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Exit
        </button>
      </div>

      {/* Two-column: Chat + Decisions */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 280px', overflow: 'hidden' }}>

        {/* Left: Chat messages + input */}
        <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: '1px solid var(--border-light)' }}>
          {/* Messages */}
          <div style={{ flex: 1, overflow: 'auto', padding: '16px 24px' }}>
            {visibleMessages.length === 0 && !isLoading ? (
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: '100%',
                color: 'var(--text-tertiary)',
                gap: '8px',
              }}>
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.4 }}>
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
                <span style={{ fontSize: '14px', fontWeight: 500 }}>Group Chat</span>
                <span style={{ fontSize: '12px', maxWidth: '360px', textAlign: 'center', lineHeight: 1.5 }}>
                  Send a message and {participants.map((p) => p.name).join(', ')} will respond based on their expertise.
                </span>
              </div>
            ) : (
              <>
                {visibleMessages.map((msg) => {
                  return (
                    <div key={msg.id} style={{ marginBottom: '16px', display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                      {/* Avatar */}
                      {msg.role === 'user' ? (
                        <div style={{
                          width: 32,
                          height: 32,
                          borderRadius: '50%',
                          backgroundColor: 'var(--accent)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexShrink: 0,
                          color: 'white',
                          fontSize: 13,
                          fontWeight: 600,
                        }}>
                          U
                        </div>
                      ) : (
                        <AgentAvatar
                          name={msg.agentName || '?'}
                          color={msg.agentColor || '#6B7280'}
                          size="md"
                        />
                      )}

                      {/* Message content */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        {/* Name + role + timestamp row */}
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px', marginBottom: '3px' }}>
                          <span style={{
                            fontSize: '13px',
                            fontWeight: 600,
                            color: msg.role === 'user' ? 'var(--text-primary)' : (msg.agentColor || 'var(--text-primary)'),
                          }}>
                            {msg.role === 'user' ? 'You' : msg.agentName}
                          </span>
                          {msg.role === 'assistant' && (
                            <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                              {(() => {
                                const agent = agents.find((a) => a.id === msg.agentId);
                                return agent?.role || '';
                              })()}
                            </span>
                          )}
                          <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginLeft: 'auto', flexShrink: 0 }}>
                            {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>

                        {/* Content */}
                        <div dir={detectDir(msg.content)}>
                          <CollapsibleMessage
                            content={msg.content}
                            isAssistant={msg.role === 'assistant'}
                            agentName={msg.agentName}
                            renderContent={(text) => {
                              const contentLines = text.split('\n');
                              return contentLines.map((line, i) => renderLine(line, i, contentLines.length, msg));
                            }}
                            style={{ fontSize: '13px', lineHeight: 1.6, color: 'var(--text-primary)' }}
                          />
                        </div>

                        {/* Model/provider tag */}
                        {msg.role === 'assistant' && msg.model && (
                          <div style={{ marginTop: '4px' }}>
                            <span style={{
                              padding: '1px 6px',
                              backgroundColor: 'var(--bg-tertiary)',
                              borderRadius: 'var(--radius-full)',
                              fontSize: '10px',
                              color: 'var(--text-tertiary)',
                              fontWeight: 500,
                            }}>
                              {msg.provider ? `${msg.provider} \u00b7 ` : ''}{msg.model}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}

                {/* Thinking indicator */}
                {isLoading && respondingAgent && (
                  <div style={{
                    marginBottom: '16px',
                    display: 'flex',
                    gap: '10px',
                    alignItems: 'center',
                    padding: '8px 0',
                  }}>
                    <AgentAvatar name={respondingAgent.name} color={respondingAgent.color} size="md" />
                    <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ fontWeight: 500, color: respondingAgent.color }}>
                        {respondingAgent.name}
                      </span>
                      is thinking
                      <span style={{ display: 'flex', gap: '2px' }}>
                        <span style={{ animation: 'pulse 1.2s infinite' }}>{'\u25cf'}</span>
                        <span style={{ animation: 'pulse 1.2s infinite 0.2s' }}>{'\u25cf'}</span>
                        <span style={{ animation: 'pulse 1.2s infinite 0.4s' }}>{'\u25cf'}</span>
                      </span>
                    </span>
                  </div>
                )}
              </>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Input */}
          <div style={{
            padding: '12px 24px 32px',
            borderTop: '1px solid var(--border-light)',
            backgroundColor: 'var(--bg-primary)',
          }}>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
              <textarea
                ref={inputRef}
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Message all agents..."
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (chatInput.trim() && !isLoading) onSend(chatInput);
                  }
                }}
                rows={3}
                style={{
                  flex: 1,
                  padding: '10px 14px',
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  color: 'var(--text-primary)',
                  fontSize: '13px',
                  outline: 'none',
                  resize: 'none',
                  lineHeight: 1.5,
                  maxHeight: '120px',
                  fontFamily: 'inherit',
                }}
              />
              <VoiceInput onTranscript={onVoiceTranscript} disabled={isLoading} size="medium" />
              <button
                onClick={() => { if (chatInput.trim() && !isLoading) onSend(chatInput); }}
                disabled={isLoading || !chatInput.trim()}
                style={{
                  padding: '10px 16px',
                  background: isLoading || !chatInput.trim() ? 'var(--bg-tertiary)' : 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
                  border: 'none',
                  borderRadius: 'var(--radius-md)',
                  color: isLoading || !chatInput.trim() ? 'var(--text-tertiary)' : 'white',
                  fontWeight: 500,
                  fontSize: '13px',
                  opacity: isLoading || !chatInput.trim() ? 0.6 : 1,
                  cursor: isLoading || !chatInput.trim() ? 'not-allowed' : 'pointer',
                  whiteSpace: 'nowrap',
                }}
              >
                Send
              </button>
            </div>
          </div>
        </div>

        {/* Right: Decisions column */}
        <div style={{ overflow: 'auto', padding: '16px', backgroundColor: 'var(--bg-secondary)' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            marginBottom: '12px',
          }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#8B5CF6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
            <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>Decisions</span>
            {decisions.length > 0 && (
              <span style={{
                padding: '1px 6px',
                backgroundColor: 'rgba(139, 92, 246, 0.15)',
                borderRadius: 'var(--radius-full)',
                fontSize: '10px',
                fontWeight: 600,
                color: '#8B5CF6',
              }}>
                {decisions.length}
              </span>
            )}
          </div>

          {decisions.length === 0 ? (
            <div style={{
              padding: '24px 12px',
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: '12px',
              lineHeight: 1.5,
            }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.3, margin: '0 auto 8px' }}>
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
              Decisions proposed by agents will appear here for your approval.
            </div>
          ) : (
            decisions.map((d) => {
              const colors = statusColors[d.status];
              const isEditing = editingDecision === d.id;
              return (
                <div
                  key={d.id}
                  style={{
                    padding: '10px 12px',
                    backgroundColor: colors.bg,
                    border: `1px solid ${colors.border}`,
                    borderRadius: 'var(--radius-md)',
                    marginBottom: '8px',
                  }}
                >
                  {/* Agent + status */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                    <AgentAvatar name={d.agentName} color={d.agentColor} size="sm" />
                    <span style={{ fontSize: '11px', fontWeight: 600, color: d.agentColor }}>{d.agentName}</span>
                    <span style={{
                      marginLeft: 'auto',
                      padding: '1px 6px',
                      borderRadius: 'var(--radius-full)',
                      fontSize: '9px',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '0.5px',
                      backgroundColor: colors.bg,
                      border: `1px solid ${colors.border}`,
                      color: colors.text,
                    }}>
                      {d.status}
                    </span>
                  </div>

                  {/* Decision text */}
                  <div style={{ fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.5, marginBottom: '8px' }}>
                    {d.status === 'changed' && d.override ? (
                      <>
                        <span style={{ textDecoration: 'line-through', opacity: 0.5 }}>{d.text}</span>
                        <br />
                        <span style={{ fontWeight: 500 }}>{d.override}</span>
                      </>
                    ) : d.text}
                  </div>

                  {/* Actions */}
                  {d.status === 'pending' && !isEditing && (
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button
                        onClick={() => onApproveDecision(d.id)}
                        style={{
                          flex: 1,
                          padding: '5px 0',
                          backgroundColor: 'rgba(34, 197, 94, 0.15)',
                          border: '1px solid rgba(34, 197, 94, 0.3)',
                          borderRadius: '4px',
                          color: '#22C55E',
                          fontSize: '11px',
                          fontWeight: 600,
                          cursor: 'pointer',
                        }}
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => { setEditingDecision(d.id); setEditText(d.text); }}
                        style={{
                          flex: 1,
                          padding: '5px 0',
                          backgroundColor: 'rgba(99, 102, 241, 0.15)',
                          border: '1px solid rgba(99, 102, 241, 0.3)',
                          borderRadius: '4px',
                          color: '#6366F1',
                          fontSize: '11px',
                          fontWeight: 600,
                          cursor: 'pointer',
                        }}
                      >
                        Change
                      </button>
                    </div>
                  )}

                  {/* Edit inline */}
                  {isEditing && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      <textarea
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        rows={2}
                        style={{
                          width: '100%',
                          padding: '6px 8px',
                          backgroundColor: 'var(--bg-primary)',
                          border: '1px solid var(--border-light)',
                          borderRadius: '4px',
                          fontSize: '12px',
                          color: 'var(--text-primary)',
                          resize: 'none',
                          outline: 'none',
                          fontFamily: 'inherit',
                        }}
                      />
                      <div style={{ display: 'flex', gap: '6px' }}>
                        <button
                          onClick={() => { onChangeDecision(d.id, editText); setEditingDecision(null); }}
                          style={{
                            flex: 1,
                            padding: '4px 0',
                            backgroundColor: '#6366F1',
                            border: 'none',
                            borderRadius: '4px',
                            color: 'white',
                            fontSize: '11px',
                            fontWeight: 600,
                            cursor: 'pointer',
                          }}
                        >
                          Save
                        </button>
                        <button
                          onClick={() => setEditingDecision(null)}
                          style={{
                            flex: 1,
                            padding: '4px 0',
                            backgroundColor: 'var(--bg-tertiary)',
                            border: '1px solid var(--border-light)',
                            borderRadius: '4px',
                            color: 'var(--text-secondary)',
                            fontSize: '11px',
                            fontWeight: 500,
                            cursor: 'pointer',
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Timestamp */}
                  <div style={{ fontSize: '9px', color: 'var(--text-tertiary)', marginTop: '4px' }}>
                    {d.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
