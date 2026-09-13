'use client';

import { useMemo, useState } from 'react';
import { AGENT_TEMPLATES, AGENT_CATEGORIES, AgentCategory } from './templates';
import { AgentAvatar } from './AgentAvatar';
import {
  Agent,
  MainView,
  ROLE_INFO,
  EditIcon,
  DeleteIcon,
  CopyIcon,
  iconBtnStyle,
  getTimeAgo,
  getAgentColor,
} from './types';

interface AgentSidebarProps {
  agents: Agent[];
  selectedAgent: string | null;
  mainView: MainView;
  collaborateAgents: string[];
  onSelectAgent: (id: string) => void;
  onDuplicate: (agent: Agent) => void;
  onEdit: (agentId: string) => void;
  onDelete: (agentId: string) => void;
  onToggleStatus: (agentId: string, newStatus: 'idle' | 'paused') => void;
  onShowTemplates: () => void;
}

const categoryOrder: AgentCategory[] = ['leadership', 'creative', 'design', 'development', 'quality', 'operations'];

export function AgentSidebar({
  agents,
  selectedAgent,
  mainView,
  collaborateAgents,
  onSelectAgent,
  onDuplicate,
  onEdit,
  onDelete,
  onToggleStatus,
  onShowTemplates,
}: AgentSidebarProps) {
  const [sidebarFilter, setSidebarFilter] = useState('');
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});

  const filteredAgents = useMemo(() => {
    if (!sidebarFilter.trim()) return agents;
    const q = sidebarFilter.toLowerCase();
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.role.toLowerCase().includes(q)
    );
  }, [agents, sidebarFilter]);

  const customAgents = useMemo(() => filteredAgents.filter((a) => !a.template_id), [filteredAgents]);
  const templateAgents = useMemo(() => filteredAgents.filter((a) => !!a.template_id), [filteredAgents]);

  const agentsByCategory = useMemo(() => {
    const groups: Record<AgentCategory, Agent[]> = {
      leadership: [],
      creative: [],
      design: [],
      development: [],
      quality: [],
      operations: [],
    };
    for (const agent of templateAgents) {
      const tmplCat = agent.template_id ? AGENT_TEMPLATES.find((t) => t.id === agent.template_id)?.category : undefined;
      const cat = (agent.category as AgentCategory) || tmplCat || (ROLE_INFO[agent.role] || ROLE_INFO.custom).category;
      groups[cat].push(agent);
    }
    return groups;
  }, [templateAgents]);

  const getColor = (agent: Agent) => getAgentColor(agent, AGENT_TEMPLATES, AGENT_CATEGORIES);

  const renderAgentItem = (agent: Agent, showTemplate: boolean) => {
    const roleInfo = ROLE_INFO[agent.role] || ROLE_INFO.custom;
    const tmplCat = agent.template_id ? AGENT_TEMPLATES.find((t) => t.id === agent.template_id)?.category : undefined;
    const agentCat = (agent.category as AgentCategory) || tmplCat || roleInfo.category;
    const catInfo = AGENT_CATEGORIES[agentCat] || AGENT_CATEGORIES.development;
    const isSelected = selectedAgent === agent.id && mainView === 'detail';
    const isInCollab = mainView === 'collaborate' && collaborateAgents.includes(agent.id);
    const isHighlighted = isSelected || isInCollab;
    const tmpl = showTemplate && agent.template_id ? AGENT_TEMPLATES.find((t) => t.id === agent.template_id) : null;
    const shortModel = agent.model ? agent.model.replace(/^(gpt-|claude-|gemini-)/, '').split('-').slice(0, 2).join('-') : 'No model';
    const lastRunAgo = agent.last_run ? getTimeAgo(agent.last_run) : null;

    return (
      <div
        key={agent.id}
        onClick={() => onSelectAgent(agent.id)}
        aria-selected={isSelected}
        style={{
          padding: '10px 10px 8px',
          marginBottom: '2px',
          borderRadius: 'var(--radius-md)',
          backgroundColor: isHighlighted ? 'var(--bg-primary)' : 'transparent',
          border: `1px solid ${isInCollab ? '#8B5CF6' : isSelected ? 'var(--accent)' : 'transparent'}`,
          cursor: 'pointer',
          transition: 'all 0.15s ease',
        }}
      >
        {/* Row 1: Avatar + Name + actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <AgentAvatar name={agent.name} color={getColor(agent)} size="sm" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)' }}>
              {agent.name}
            </div>
            {tmpl && (
              <div style={{ fontSize: '10px', color: tmpl.color, fontWeight: 500, marginTop: '1px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {tmpl.name}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: '1px', flexShrink: 0, alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => onToggleStatus(agent.id, agent.status === 'paused' ? 'idle' : 'paused')}
              title={agent.status === 'paused' ? 'Activate' : 'Deactivate'}
              style={{
                width: '28px', height: '16px', borderRadius: '8px', border: 'none',
                backgroundColor: agent.status === 'paused' ? 'var(--bg-tertiary)' : 'var(--success)',
                cursor: 'pointer', position: 'relative', transition: 'background-color 0.2s',
                flexShrink: 0, marginRight: '4px',
              }}
            >
              <div style={{
                width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'white',
                position: 'absolute', top: '2px',
                left: agent.status === 'paused' ? '2px' : '14px',
                transition: 'left 0.2s', boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
              }} />
            </button>
            <button onClick={() => onDuplicate(agent)} style={iconBtnStyle} title="Clone">
              <CopyIcon />
            </button>
            <button onClick={() => onEdit(agent.id)} style={iconBtnStyle} title="Edit">
              <EditIcon />
            </button>
            <button onClick={() => onDelete(agent.id)} style={iconBtnStyle} title="Delete">
              <DeleteIcon />
            </button>
          </div>
        </div>

        {/* Row 2: Category + Role + Model */}
        <div style={{ marginTop: '5px', marginLeft: '28px', display: 'flex', flexWrap: 'wrap', gap: '4px', alignItems: 'center' }}>
          <span style={{
            padding: '1px 6px',
            backgroundColor: `${catInfo.color}15`,
            borderRadius: 'var(--radius-full)',
            fontSize: '10px',
            fontWeight: 500,
            color: catInfo.color,
            whiteSpace: 'nowrap',
          }}>
            {catInfo.label}
          </span>
          <span style={{
            padding: '1px 6px',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-full)',
            fontSize: '10px',
            color: 'var(--text-secondary)',
            whiteSpace: 'nowrap',
          }}>
            {agent.role}
          </span>
          <span style={{
            padding: '1px 6px',
            backgroundColor: agent.model_category ? 'rgba(99, 102, 241, 0.08)' : 'var(--bg-tertiary)',
            borderRadius: 'var(--radius-full)',
            fontSize: '10px',
            color: agent.model_category ? '#6366F1' : 'var(--text-secondary)',
            whiteSpace: 'nowrap',
            maxWidth: '130px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }} title={agent.model_category ? `SmartRouter (${agent.model_category})` : agent.model}>
            {agent.model_category ? (
              <>
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{ display: 'inline', verticalAlign: '-1px', marginRight: '2px' }}>
                  <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                </svg>
                Smart
              </>
            ) : shortModel}
          </span>
        </div>

        {/* Row 3: Activity summary */}
        <div style={{ marginTop: '4px', marginLeft: '28px', fontSize: '10px', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>{agent.run_count} run{agent.run_count !== 1 ? 's' : ''}</span>
          {lastRunAgo && (
            <>
              <span style={{ opacity: 0.4 }}>&bull;</span>
              <span>Last {lastRunAgo}</span>
            </>
          )}
          {(agent.services || []).length > 0 && (
            <>
              <span style={{ opacity: 0.4 }}>&bull;</span>
              <span>{(agent.services || []).length} service{(agent.services || []).length !== 1 ? 's' : ''}</span>
            </>
          )}
        </div>
      </div>
    );
  };

  return (
    <aside style={{ borderRight: '1px solid var(--border-light)', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-secondary)' }}>
      {/* Sidebar header */}
      <div style={{ padding: '16px 12px 14px', borderBottom: '1px solid var(--border-light)' }}>
        <div style={{ position: 'relative' }} role="search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={sidebarFilter ? 'var(--accent)' : 'var(--text-tertiary)'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', transition: 'stroke 0.15s' }}>
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            value={sidebarFilter}
            onChange={(e) => setSidebarFilter(e.target.value)}
            placeholder="Filter agents..."
            aria-label="Filter agents"
            style={{
              width: '100%',
              padding: '10px 30px 10px 34px',
              backgroundColor: 'var(--bg-primary)',
              border: `1.5px solid ${sidebarFilter ? 'var(--accent)' : 'var(--border-medium)'}`,
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              outline: 'none',
              transition: 'border-color 0.15s',
            }}
          />
          {sidebarFilter ? (
            <button
              onClick={() => setSidebarFilter('')}
              style={{
                position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)',
                background: 'var(--bg-tertiary)', border: 'none', cursor: 'pointer',
                padding: '2px 5px', borderRadius: 'var(--radius-sm)',
                color: 'var(--text-secondary)', fontSize: '11px', lineHeight: 1,
                fontWeight: 500,
              }}
            >
              {'\u2715'}
            </button>
          ) : (
            <span style={{
              position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)',
              fontSize: '10px', color: 'var(--text-tertiary)', pointerEvents: 'none',
              padding: '2px 5px', backgroundColor: 'var(--bg-tertiary)', borderRadius: '3px',
              fontWeight: 500,
            }}>
              {agents.length}
            </span>
          )}
        </div>
      </div>

      {/* Agent list */}
      <div style={{ flex: 1, overflow: 'auto', padding: '8px' }}>
        {filteredAgents.length === 0 && !sidebarFilter ? (
          <div style={{ textAlign: 'center', marginTop: '32px', color: 'var(--text-secondary)' }}>
            <div style={{ fontSize: '32px', marginBottom: '10px', opacity: 0.6 }}>{'\ud83e\udd16'}</div>
            <p style={{ margin: '0 0 4px', fontWeight: 500, fontSize: '14px' }}>No agents yet</p>
            <p style={{ margin: '0 0 16px', fontSize: '12px', color: 'var(--text-tertiary)' }}>Create your first agent or browse templates</p>
            <button
              onClick={onShowTemplates}
              style={{
                padding: '8px 16px',
                backgroundColor: 'transparent',
                border: '1px solid var(--accent)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--accent)',
                fontSize: '12px',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Get started
            </button>
          </div>
        ) : filteredAgents.length === 0 && sidebarFilter ? (
          <div style={{ textAlign: 'center', marginTop: '24px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
            No agents match &ldquo;{sidebarFilter}&rdquo;
          </div>
        ) : (
          <>
            {/* Hired Agents -- custom-built agents (no template) */}
            {customAgents.length > 0 && (
              <div style={{ marginBottom: '8px' }}>
                <div
                  onClick={() => setCollapsedSections((prev) => ({ ...prev, hired: !prev.hired }))}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px 4px',
                    fontSize: '10px',
                    fontWeight: 600,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.6px',
                    cursor: 'pointer',
                    userSelect: 'none',
                  }}>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform 0.2s', transform: collapsedSections.hired ? 'rotate(-90deg)' : 'rotate(0deg)', flexShrink: 0 }}>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                  <div style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: 'var(--accent)' }} />
                  Hired Agents
                  <span style={{ fontWeight: 400, opacity: 0.7 }}>({customAgents.length})</span>
                </div>
                {!collapsedSections.hired && customAgents.map((agent) => renderAgentItem(agent, false))}
              </div>
            )}
            {categoryOrder.map((cat) => {
              const catAgents = agentsByCategory[cat];
              if (catAgents.length === 0) return null;
              const catInfo = AGENT_CATEGORIES[cat];
              return (
                <div key={cat} style={{ marginBottom: '8px' }}>
                  <div
                    onClick={() => setCollapsedSections((prev) => ({ ...prev, [cat]: !prev[cat] }))}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '6px 8px 4px',
                      fontSize: '10px',
                      fontWeight: 600,
                      color: 'var(--text-tertiary)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.6px',
                      cursor: 'pointer',
                      userSelect: 'none',
                    }}>
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ transition: 'transform 0.2s', transform: collapsedSections[cat] ? 'rotate(-90deg)' : 'rotate(0deg)', flexShrink: 0 }}>
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                    <div style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: catInfo.color }} />
                    {catInfo.label}
                    <span style={{ fontWeight: 400, opacity: 0.7 }}>({catAgents.length})</span>
                  </div>
                  {!collapsedSections[cat] && catAgents.map((agent) => renderAgentItem(agent, true))}
                </div>
              );
            })}
          </>
        )}
      </div>
    </aside>
  );
}
