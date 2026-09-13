'use client';

import React from 'react';
import { AgentForm, AgentFormData } from './AgentForm';

interface Agent {
  id: string;
  name: string;
  role: string;
  model: string;
  model_category?: string | null;
  category?: string | null;
  system_prompt: string | null;
  services: string[];
  temperature: number;
  status: 'idle' | 'running' | 'paused' | 'error';
  created_at: string;
  last_run: string | null;
  run_count: number;
  template_id?: string | null;
}

interface AgentEditModalProps {
  agent: Agent;
  onClose: () => void;
  onSave: (agentId: string, data: Partial<AgentFormData>) => Promise<void>;
  isLoading?: boolean;
}

export function AgentEditModal({ agent, onClose, onSave, isLoading = false }: AgentEditModalProps) {
  const handleSubmit = async (data: AgentFormData) => {
    // Only send fields the backend AgentConfig accepts
    await onSave(agent.id, {
      name: data.name,
      role: data.role,
      model: data.model,
      model_category: data.model_category,
      category: data.category,
      system_prompt: data.system_prompt,
      services: data.services,
      temperature: data.temperature,
    });
    onClose();
  };

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '20px',
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-primary)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          width: '100%',
          maxWidth: '720px',
          maxHeight: '90vh',
          overflow: 'auto',
          padding: '24px',
        }}
      >
        {/* Close button */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '-12px' }}>
          <button
            onClick={onClose}
            style={{
              padding: '4px',
              backgroundColor: 'transparent',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
            }}
            title="Close"
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <AgentForm
          initialData={{
            name: agent.name,
            role: agent.role,
            model: agent.model,
            model_category: agent.model_category ?? null,
            category: agent.category ?? null,
            system_prompt: agent.system_prompt || '',
            services: agent.services,
            temperature: agent.temperature,
          }}
          onSubmit={handleSubmit}
          onCancel={onClose}
          isLoading={isLoading}
          title="Edit Agent"
          submitLabel="Save"
        />
      </div>
    </div>
  );
}

export default AgentEditModal;
