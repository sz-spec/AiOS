'use client';

import React from 'react';
import { AgentAvatar } from './AgentAvatar';

interface AgentPickerAgent {
  id: string;
  name: string;
  role: string;
  color: string;
}

interface AgentPickerProps {
  agents: AgentPickerAgent[];
  selected: string[];
  onToggle: (id: string) => void;
  onStart: () => void;
  onClose: () => void;
}

export function AgentPicker({ agents, selected, onToggle, onStart, onClose }: AgentPickerProps) {
  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      backgroundColor: 'rgba(0,0,0,0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        width: '420px',
        maxHeight: '70vh',
        backgroundColor: 'var(--bg-primary)',
        borderRadius: 'var(--radius-lg, 12px)',
        border: '1px solid var(--border-light)',
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '20px 20px 12px',
          borderBottom: '1px solid var(--border-light)',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
        }}>
          <div>
            <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
              Select Agents
            </h3>
            <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-tertiary)' }}>
              Choose 2+ agents for group chat
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              padding: '4px',
              backgroundColor: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-tertiary)',
              fontSize: '18px',
              lineHeight: 1,
            }}
          >
            {'\u2715'}
          </button>
        </div>

        {/* Agent list */}
        <div style={{ flex: 1, overflow: 'auto', padding: '8px 12px' }}>
          {agents.map((agent) => {
            const isChecked = selected.includes(agent.id);
            return (
              <button
                key={agent.id}
                onClick={() => onToggle(agent.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  width: '100%',
                  padding: '10px 12px',
                  backgroundColor: isChecked ? 'rgba(139, 92, 246, 0.08)' : 'transparent',
                  border: `1px solid ${isChecked ? 'rgba(139, 92, 246, 0.3)' : 'transparent'}`,
                  borderRadius: 'var(--radius-md)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.15s ease',
                  marginBottom: '2px',
                }}
              >
                {/* Checkbox */}
                <div style={{
                  width: '18px',
                  height: '18px',
                  borderRadius: '4px',
                  border: `2px solid ${isChecked ? '#8B5CF6' : 'var(--border-medium)'}`,
                  backgroundColor: isChecked ? '#8B5CF6' : 'transparent',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  transition: 'all 0.15s ease',
                }}>
                  {isChecked && (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </div>

                <AgentAvatar name={agent.name} color={agent.color} size="sm" />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {agent.name}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
                    {agent.role}
                  </div>
                </div>

                <div style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: agent.color,
                  flexShrink: 0,
                }} />
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 20px 16px',
          borderTop: '1px solid var(--border-light)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            Selected: {selected.length}
          </span>
          <button
            onClick={onStart}
            disabled={selected.length < 2}
            style={{
              padding: '8px 20px',
              background: selected.length >= 2 ? 'linear-gradient(135deg, #8B5CF6, #6D28D9)' : 'var(--bg-tertiary)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              color: selected.length >= 2 ? 'white' : 'var(--text-tertiary)',
              fontWeight: 600,
              fontSize: '13px',
              cursor: selected.length >= 2 ? 'pointer' : 'not-allowed',
              boxShadow: selected.length >= 2 ? '0 2px 8px rgba(139, 92, 246, 0.3)' : 'none',
              transition: 'all 0.15s ease',
            }}
          >
            Start Collaboration
          </button>
        </div>
      </div>
    </div>
  );
}
