'use client';

import React from 'react';
import { AgentTemplate, AGENT_CATEGORIES } from './templates';
import { AgentAvatar } from './AgentAvatar';
import { AgentChatPanel } from './AgentChatPanel';
import { ChatMessage, RACI_MAP, RACI_COLORS } from './types';

interface TemplateChatViewProps {
  template: AgentTemplate;
  messages: ChatMessage[];
  chatInput: string;
  setChatInput: (value: string) => void;
  onSendMessage: (content: string) => void;
  onVoiceTranscript: (transcript: string) => void;
  isChatLoading: boolean;
  onCreateAgent: (template: AgentTemplate) => void;
  chatEndRef: React.RefObject<HTMLDivElement | null>;
  chatInputRef: React.RefObject<HTMLTextAreaElement | null>;
}

export function TemplateChatView({
  template,
  messages,
  chatInput,
  setChatInput,
  onSendMessage,
  onVoiceTranscript,
  isChatLoading,
  onCreateAgent,
  chatEndRef,
  chatInputRef,
}: TemplateChatViewProps) {
  const raci = RACI_MAP[template.defaults.role] || RACI_MAP.custom;
  const catInfo = AGENT_CATEGORIES[template.category];

  return (
    <>
      {/* Header */}
      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-light)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <AgentAvatar name={template.name} color={template.color} size="lg" />
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 600 }}>{template.name}</h2>
              <span style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                padding: '2px 10px',
                backgroundColor: `${RACI_COLORS[raci.letter]}15`,
                border: `1px solid ${RACI_COLORS[raci.letter]}30`,
                borderRadius: 'var(--radius-full)',
                fontSize: '11px',
                fontWeight: 600,
                color: RACI_COLORS[raci.letter],
              }}>
                {raci.letter} &middot; {raci.label}
              </span>
              <span style={{
                padding: '2px 8px',
                backgroundColor: `${catInfo.color}15`,
                borderRadius: 'var(--radius-full)',
                fontSize: '11px',
                fontWeight: 500,
                color: catInfo.color,
              }}>
                {catInfo.label}
              </span>
            </div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '2px' }}>
              {template.description}
            </div>
          </div>
          <button
            onClick={() => onCreateAgent(template)}
            style={{
              padding: '8px 16px',
              background: 'linear-gradient(135deg, #d97706, #b45309)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              color: 'white',
              fontWeight: 600,
              fontSize: '13px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer',
              boxShadow: '0 2px 8px rgba(217, 119, 6, 0.3)',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Create Agent
          </button>
        </div>
      </div>

      {/* Chat + Config two-column layout */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 320px', overflow: 'hidden' }}>
        {/* Left: Chat area */}
        <AgentChatPanel
          messages={messages}
          chatInput={chatInput}
          setChatInput={setChatInput}
          onSend={onSendMessage}
          onVoiceTranscript={onVoiceTranscript}
          isLoading={isChatLoading}
          placeholder={`Ask ${template.name}...`}
          emptyStateTitle={`Chat with ${template.name}`}
          emptyStateDescription="Talk to this resource template. Ask about its capabilities, or give it a task to try."
          quickPrompts={template.exampleTasks}
          chatEndRef={chatEndRef}
          chatInputRef={chatInputRef}
        />

        {/* Right: Template Config sidebar */}
        <div style={{ overflow: 'auto', padding: '16px' }}>
          {/* RACI Card */}
          <div style={{
            backgroundColor: `${RACI_COLORS[raci.letter]}08`,
            borderRadius: 'var(--radius-md)',
            padding: '14px',
            border: `1px solid ${RACI_COLORS[raci.letter]}20`,
            marginBottom: '16px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
              <div style={{
                width: '36px',
                height: '36px',
                borderRadius: 'var(--radius-md)',
                backgroundColor: RACI_COLORS[raci.letter],
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontWeight: 700,
                fontSize: '16px',
              }}>
                {raci.letter}
              </div>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{raci.label}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>RACI Role</div>
              </div>
            </div>
            <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {raci.description}
            </p>
          </div>

          {/* Template Defaults Card */}
          <div style={{ marginBottom: '16px' }}>
            <h4 style={{ margin: '0 0 10px', fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Template Defaults</h4>
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              padding: '12px',
              border: '1px solid var(--border-light)',
            }}>
              <div style={{ marginBottom: '10px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Role</div>
                <div style={{ fontSize: '13px' }}>{template.defaults.role}</div>
              </div>
              <div style={{ marginBottom: '10px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Model</div>
                <div style={{ fontSize: '13px' }}>
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '3px',
                    padding: '1px 6px',
                    backgroundColor: 'rgba(99, 102, 241, 0.1)',
                    borderRadius: 'var(--radius-full)',
                    fontSize: '11px',
                    color: '#6366F1',
                    marginRight: '4px',
                  }}>
                    Router ({template.defaults.router_role})
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{template.defaults.model}</span>
                </div>
              </div>
              <div style={{ marginBottom: '10px' }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Temperature</div>
                <div style={{ fontSize: '13px' }}>{template.defaults.temperature}</div>
              </div>
              <div style={{ marginBottom: template.defaults.system_prompt ? '10px' : 0 }}>
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Services</div>
                <div style={{ fontSize: '13px' }}>
                  {template.defaults.services.length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                      {template.defaults.services.map((svc) => (
                        <span key={svc} style={{
                          padding: '1px 6px',
                          backgroundColor: 'var(--bg-tertiary)',
                          borderRadius: 'var(--radius-full)',
                          fontSize: '11px',
                        }}>
                          {svc}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span style={{ color: 'var(--text-tertiary)' }}>Defaults only</span>
                  )}
                </div>
              </div>
              {template.defaults.system_prompt && (
                <div>
                  <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>System Prompt</div>
                  <div style={{
                    fontSize: '12px',
                    padding: '6px 8px',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: 'var(--radius-sm)',
                    whiteSpace: 'pre-wrap',
                    maxHeight: '100px',
                    overflow: 'auto',
                    lineHeight: 1.4,
                    color: 'var(--text-secondary)',
                  }}>
                    {template.defaults.system_prompt}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Tags */}
          {template.tags.length > 0 && (
            <div>
              <h4 style={{ margin: '0 0 8px', fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Tags</h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                {template.tags.map((tag) => (
                  <span key={tag} style={{
                    padding: '2px 8px',
                    backgroundColor: 'var(--bg-tertiary)',
                    borderRadius: 'var(--radius-full)',
                    fontSize: '11px',
                    color: 'var(--text-secondary)',
                  }}>
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
