'use client';

import React, { useRef, useEffect } from 'react';
import { VoiceInput } from '@/components/shared/VoiceInput';
import { ChatMessage, ConfigProposal, InstructionProposal, detectDir } from './types';

interface AgentChatPanelProps {
  messages: ChatMessage[];
  chatInput: string;
  setChatInput: (value: string) => void;
  onSend: (content: string) => void;
  onVoiceTranscript: (transcript: string) => void;
  isLoading: boolean;
  placeholder: string;
  emptyStateTitle: string;
  emptyStateDescription: string;
  quickPrompts?: string[];
  // Config/instruction proposal handlers (optional, only for agent detail chat)
  onApplyConfigProposal?: (messageId: string, proposalId: string) => void;
  onRejectConfigProposal?: (messageId: string, proposalId: string) => void;
  onApplyInstructionProposal?: (messageId: string, proposalId: string) => void;
  onRejectInstructionProposal?: (messageId: string, proposalId: string) => void;
  // TTS (optional)
  playingMessageId?: string | null;
  onSpeakMessage?: (messageId: string, text: string) => void;
  // External refs
  chatEndRef?: React.RefObject<HTMLDivElement | null>;
  chatInputRef?: React.RefObject<HTMLTextAreaElement | null>;
}

export function AgentChatPanel({
  messages,
  chatInput,
  setChatInput,
  onSend,
  onVoiceTranscript,
  isLoading,
  placeholder,
  emptyStateTitle,
  emptyStateDescription,
  quickPrompts,
  onApplyConfigProposal,
  onRejectConfigProposal,
  onApplyInstructionProposal,
  onRejectInstructionProposal,
  playingMessageId,
  onSpeakMessage,
  chatEndRef: externalChatEndRef,
  chatInputRef: externalChatInputRef,
}: AgentChatPanelProps) {
  const internalChatEndRef = useRef<HTMLDivElement | null>(null);
  const internalChatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const chatEndRef = externalChatEndRef || internalChatEndRef;
  const chatInputRef = externalChatInputRef || internalChatInputRef;

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, chatEndRef]);

  const renderProposalCards = (msg: ChatMessage) => {
    return (
      <>
        {/* Config change proposal cards */}
        {msg.configProposals && msg.configProposals.length > 0 && onApplyConfigProposal && onRejectConfigProposal && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px', paddingLeft: '8px' }}>
            {msg.configProposals.map((proposal) => (
              <div key={proposal.id} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${proposal.status === 'applied' ? 'var(--success)' : proposal.status === 'rejected' ? 'var(--error)' : '#6366F1'}30`,
                backgroundColor: proposal.status === 'applied' ? 'rgba(34, 197, 94, 0.06)' : proposal.status === 'rejected' ? 'rgba(239, 68, 68, 0.06)' : 'rgba(99, 102, 241, 0.06)',
                maxWidth: '80%',
              }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={proposal.status === 'applied' ? 'var(--success)' : proposal.status === 'rejected' ? 'var(--error)' : '#6366F1'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
                <span style={{ flex: 1, fontSize: '12px', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                  {proposal.label}
                </span>
                {proposal.status === 'pending' ? (
                  <div style={{ display: 'flex', gap: '4px' }}>
                    <button
                      onClick={() => onApplyConfigProposal(msg.id, proposal.id)}
                      style={{
                        padding: '3px 10px',
                        backgroundColor: 'var(--success)',
                        border: 'none',
                        borderRadius: 'var(--radius-sm)',
                        color: 'white',
                        fontSize: '11px',
                        fontWeight: 600,
                        cursor: 'pointer',
                      }}
                    >
                      Apply
                    </button>
                    <button
                      onClick={() => onRejectConfigProposal(msg.id, proposal.id)}
                      style={{
                        padding: '3px 10px',
                        backgroundColor: 'transparent',
                        border: '1px solid var(--border-medium)',
                        borderRadius: 'var(--radius-sm)',
                        color: 'var(--text-secondary)',
                        fontSize: '11px',
                        fontWeight: 500,
                        cursor: 'pointer',
                      }}
                    >
                      Reject
                    </button>
                  </div>
                ) : (
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    color: proposal.status === 'applied' ? 'var(--success)' : 'var(--error)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}>
                    {proposal.status === 'applied' ? 'Applied' : 'Rejected'}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
        {/* Instruction proposal cards */}
        {msg.instructionProposals && msg.instructionProposals.length > 0 && onApplyInstructionProposal && onRejectInstructionProposal && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '12px', paddingLeft: '8px' }}>
            {msg.instructionProposals.map((proposal) => (
              <div key={proposal.id} style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${proposal.status === 'applied' ? 'var(--success)' : proposal.status === 'rejected' ? 'var(--error)' : '#F59E0B'}30`,
                backgroundColor: proposal.status === 'applied' ? 'rgba(34, 197, 94, 0.06)' : proposal.status === 'rejected' ? 'rgba(239, 68, 68, 0.06)' : 'rgba(245, 158, 11, 0.06)',
                maxWidth: '80%',
              }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={proposal.status === 'applied' ? 'var(--success)' : proposal.status === 'rejected' ? 'var(--error)' : '#F59E0B'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="16" y1="13" x2="8" y2="13" />
                  <line x1="16" y1="17" x2="8" y2="17" />
                </svg>
                <span style={{ flex: 1, fontSize: '12px', color: 'var(--text-primary)' }}>
                  <span style={{ fontSize: '10px', fontWeight: 600, color: proposal.action === 'add' ? '#F59E0B' : 'var(--error)', marginRight: '4px', textTransform: 'uppercase' }}>
                    {proposal.action === 'add' ? '+' : '\u2212'} Instruction
                  </span>
                  {proposal.text}
                </span>
                {proposal.status === 'pending' ? (
                  <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                    <button
                      onClick={() => onApplyInstructionProposal(msg.id, proposal.id)}
                      style={{
                        padding: '3px 10px',
                        backgroundColor: 'var(--success)',
                        border: 'none',
                        borderRadius: 'var(--radius-sm)',
                        color: 'white',
                        fontSize: '11px',
                        fontWeight: 600,
                        cursor: 'pointer',
                      }}
                    >
                      Apply
                    </button>
                    <button
                      onClick={() => onRejectInstructionProposal(msg.id, proposal.id)}
                      style={{
                        padding: '3px 10px',
                        backgroundColor: 'transparent',
                        border: '1px solid var(--border-medium)',
                        borderRadius: 'var(--radius-sm)',
                        color: 'var(--text-secondary)',
                        fontSize: '11px',
                        fontWeight: 500,
                        cursor: 'pointer',
                      }}
                    >
                      Reject
                    </button>
                  </div>
                ) : (
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    color: proposal.status === 'applied' ? 'var(--success)' : 'var(--error)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    flexShrink: 0,
                  }}>
                    {proposal.status === 'applied' ? 'Applied' : 'Rejected'}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border-light)', overflow: 'hidden' }}>
      {/* Chat messages */}
      <div role="log" aria-label="Chat messages" style={{ flex: 1, overflow: 'auto', padding: '16px 20px' }}>
        {messages.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-tertiary)', gap: '8px' }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.4 }}>
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span style={{ fontSize: '14px', fontWeight: 500 }}>{emptyStateTitle}</span>
            <span style={{ fontSize: '12px', maxWidth: '300px', textAlign: 'center', lineHeight: 1.5 }}>
              {emptyStateDescription}
            </span>
            {quickPrompts && quickPrompts.length > 0 && (
              <div style={{ marginTop: '12px', display: 'flex', flexWrap: 'wrap', gap: '6px', justifyContent: 'center', maxWidth: '400px' }}>
                {quickPrompts.slice(0, 3).map((hint, i) => (
                  <button
                    key={i}
                    onClick={() => { setChatInput(hint); chatInputRef.current?.focus(); }}
                    style={{
                      padding: '6px 12px',
                      backgroundColor: 'var(--bg-secondary)',
                      border: '1px solid var(--border-light)',
                      borderRadius: 'var(--radius-full)',
                      fontSize: '11px',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                      transition: 'border-color 0.15s',
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--accent)'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)'; }}
                  >
                    {hint.length > 50 ? hint.slice(0, 50) + '...' : hint}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          messages.filter((m) => m.role !== 'system').map((msg) => {
            const dir = detectDir(msg.content);
            const isUser = msg.role === 'user';
            const hasProposals = (msg.configProposals && msg.configProposals.length > 0) || (msg.instructionProposals && msg.instructionProposals.length > 0);
            return (
              <div key={msg.id}>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: isUser ? 'flex-end' : 'flex-start',
                    marginBottom: hasProposals ? '6px' : '12px',
                  }}
                >
                  <div dir={dir} style={{
                    maxWidth: '80%',
                    padding: '10px 14px',
                    borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                    backgroundColor: isUser ? 'var(--accent)' : 'var(--bg-secondary)',
                    color: isUser ? 'white' : 'var(--text-primary)',
                    border: isUser ? 'none' : '1px solid var(--border-light)',
                    fontSize: '13px',
                    lineHeight: 1.5,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    textAlign: dir === 'rtl' ? 'right' : 'left',
                  }}>
                    {msg.content}
                    <div style={{
                      fontSize: '10px',
                      marginTop: '4px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      opacity: 0.6,
                      color: isUser ? 'rgba(255,255,255,0.7)' : 'var(--text-tertiary)',
                      justifyContent: dir === 'rtl' ? 'flex-end' : 'flex-start',
                    }}>
                      {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      {msg.role === 'assistant' && msg.model && (
                        <span style={{
                          padding: '0 5px',
                          backgroundColor: 'var(--bg-tertiary)',
                          borderRadius: 'var(--radius-full)',
                          fontSize: '9px',
                          fontWeight: 500,
                          opacity: 1,
                          color: 'var(--text-secondary)',
                        }}>
                          {msg.provider ? `${msg.provider} \u00b7 ` : ''}{msg.model}
                        </span>
                      )}
                      {msg.role === 'assistant' && onSpeakMessage && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSpeakMessage(msg.id, msg.content); }}
                          title={playingMessageId === msg.id ? 'Stop' : 'Listen'}
                          style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            padding: '0 2px',
                            display: 'flex',
                            alignItems: 'center',
                            color: playingMessageId === msg.id ? 'var(--accent)' : 'var(--text-tertiary)',
                            opacity: playingMessageId === msg.id ? 1 : 0.7,
                            transition: 'color 0.15s',
                          }}
                        >
                          {playingMessageId === msg.id ? (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                              <rect x="6" y="4" width="4" height="16" rx="1" />
                              <rect x="14" y="4" width="4" height="16" rx="1" />
                            </svg>
                          ) : (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                              <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                              <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                            </svg>
                          )}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
                {renderProposalCards(msg)}
              </div>
            );
          })
        )}
        {isLoading && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: '12px' }}>
            <div style={{
              padding: '10px 14px',
              borderRadius: '16px 16px 16px 4px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              fontSize: '13px',
              color: 'var(--text-tertiary)',
              display: 'flex',
              gap: '4px',
              alignItems: 'center',
            }}>
              <span style={{ animation: 'pulse 1.2s infinite' }}>&#9679;</span>
              <span style={{ animation: 'pulse 1.2s infinite 0.2s' }}>&#9679;</span>
              <span style={{ animation: 'pulse 1.2s infinite 0.4s' }}>&#9679;</span>
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Chat input */}
      <div style={{ padding: '12px 20px 32px', borderTop: '1px solid var(--border-light)', backgroundColor: 'var(--bg-primary)' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
          <textarea
            ref={chatInputRef}
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder={placeholder}
            aria-label="Type a message"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSend(chatInput);
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
            onClick={() => onSend(chatInput)}
            disabled={isLoading || !chatInput.trim()}
            aria-label="Send message"
            style={{
              padding: '10px 16px',
              backgroundColor: 'var(--accent)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              color: 'white',
              fontWeight: 500,
              fontSize: '13px',
              opacity: isLoading || !chatInput.trim() ? 0.5 : 1,
              cursor: isLoading || !chatInput.trim() ? 'not-allowed' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
