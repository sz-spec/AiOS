'use client';

import React from 'react';
import { AGENT_TEMPLATES, AGENT_CATEGORIES } from './templates';
import { AgentAvatar } from './AgentAvatar';
import { AgentChatPanel } from './AgentChatPanel';
import { AgentConfigSidebar } from './AgentConfigSidebar';
import {
  Agent,
  AgentLog,
  ChatMessage,
  ConfigVersion,
  RACI_MAP,
  RACI_COLORS,
  EditIcon,
  getAgentColor,
} from './types';

interface AgentDetailViewProps {
  agent: Agent;
  messages: ChatMessage[];
  chatInput: string;
  setChatInput: (value: string) => void;
  onSendMessage: (content: string) => void;
  onVoiceTranscript: (transcript: string) => void;
  isChatLoading: boolean;
  // Config/instruction proposal handlers
  onApplyConfigProposal: (messageId: string, proposalId: string) => void;
  onRejectConfigProposal: (messageId: string, proposalId: string) => void;
  onApplyInstructionProposal: (messageId: string, proposalId: string) => void;
  onRejectInstructionProposal: (messageId: string, proposalId: string) => void;
  // TTS
  playingMessageId: string | null;
  onSpeakMessage: (messageId: string, text: string) => void;
  // Config sidebar
  instructions: string[];
  configVersions: ConfigVersion[];
  logs: AgentLog[];
  showLogs: boolean;
  onRemoveInstruction: (agentId: string, index: number) => void;
  onRestoreVersion: (version: ConfigVersion) => void;
  onLoadLogs: (agentId: string) => void;
  // Header actions
  onShowTemplates: () => void;
  onStartCollaborate: () => void;
  onEdit: () => void;
  // Refs
  chatEndRef: React.RefObject<HTMLDivElement | null>;
  chatInputRef: React.RefObject<HTMLTextAreaElement | null>;
}

export function AgentDetailView({
  agent,
  messages,
  chatInput,
  setChatInput,
  onSendMessage,
  onVoiceTranscript,
  isChatLoading,
  onApplyConfigProposal,
  onRejectConfigProposal,
  onApplyInstructionProposal,
  onRejectInstructionProposal,
  playingMessageId,
  onSpeakMessage,
  instructions,
  configVersions,
  logs,
  showLogs,
  onRemoveInstruction,
  onRestoreVersion,
  onLoadLogs,
  onShowTemplates,
  onStartCollaborate,
  onEdit,
  chatEndRef,
  chatInputRef,
}: AgentDetailViewProps) {
  const raci = RACI_MAP[agent.role] || RACI_MAP.custom;
  const getColor = (a: Agent) => getAgentColor(a, AGENT_TEMPLATES, AGENT_CATEGORIES);

  return (
    <>
      {/* Header with RACI badge */}
      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-light)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <AgentAvatar name={agent.name} color={getColor(agent)} size="lg" />
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 600 }}>{agent.name}</h2>
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
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)', fontSize: '13px', marginTop: '4px', flexWrap: 'wrap' }}>
              {/* Category badge from template */}
              {(() => {
                const tmpl = agent.template_id ? AGENT_TEMPLATES.find((t) => t.id === agent.template_id) : null;
                const catInfo = tmpl ? AGENT_CATEGORIES[tmpl.category] : null;
                return catInfo ? (
                  <span style={{
                    padding: '1px 8px',
                    borderRadius: 'var(--radius-full)',
                    fontSize: '11px',
                    fontWeight: 500,
                    backgroundColor: `${catInfo.color}15`,
                    color: catInfo.color,
                    border: `1px solid ${catInfo.color}30`,
                  }}>
                    {catInfo.label}
                  </span>
                ) : null;
              })()}
              <span>{agent.role}</span>
              <span>&bull;</span>
              <span>{agent.model_category ? `Auto (${agent.model_category})` : agent.model}</span>
              <span>&bull;</span>
              <span>{agent.run_count} run{agent.run_count !== 1 ? 's' : ''}</span>
            </div>
          </div>
          <button
            onClick={onShowTemplates}
            style={{
              padding: '8px 14px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7" />
              <rect x="14" y="3" width="7" height="7" />
              <rect x="3" y="14" width="7" height="7" />
              <rect x="14" y="14" width="7" height="7" />
            </svg>
            Agent Resources
          </button>
          <button
            onClick={onStartCollaborate}
            style={{
              padding: '8px 14px',
              background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              color: 'white',
              fontSize: '13px',
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer',
              boxShadow: '0 2px 8px rgba(139, 92, 246, 0.3)',
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            Collaborate
          </button>
          <button
            onClick={onEdit}
            style={{
              padding: '8px 14px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: 'pointer',
            }}
          >
            <EditIcon />
            Edit
          </button>
        </div>
      </div>

      {/* Chat + Config/RACI two-column layout */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 320px', overflow: 'hidden' }}>
        {/* Left: Chat area */}
        <AgentChatPanel
          messages={messages}
          chatInput={chatInput}
          setChatInput={setChatInput}
          onSend={onSendMessage}
          onVoiceTranscript={onVoiceTranscript}
          isLoading={isChatLoading}
          placeholder={`Ask about ${agent.name}...`}
          emptyStateTitle={`Chat with ${agent.name}`}
          emptyStateDescription="Ask about capabilities, RACI role, or change configuration through conversation."
          quickPrompts={['Always respond in Hebrew', 'Change the temperature to 0.8', 'Focus on React code only']}
          onApplyConfigProposal={onApplyConfigProposal}
          onRejectConfigProposal={onRejectConfigProposal}
          onApplyInstructionProposal={onApplyInstructionProposal}
          onRejectInstructionProposal={onRejectInstructionProposal}
          playingMessageId={playingMessageId}
          onSpeakMessage={onSpeakMessage}
          chatEndRef={chatEndRef}
          chatInputRef={chatInputRef}
        />

        {/* Right: Config + RACI sidebar */}
        <AgentConfigSidebar
          agent={agent}
          instructions={instructions}
          configVersions={configVersions}
          logs={logs}
          showLogs={showLogs}
          onRemoveInstruction={onRemoveInstruction}
          onRestoreVersion={onRestoreVersion}
          onLoadLogs={onLoadLogs}
        />
      </div>
    </>
  );
}
