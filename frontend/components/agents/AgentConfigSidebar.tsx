'use client';

import { AGENT_TEMPLATES, AGENT_CATEGORIES } from './templates';
import {
  Agent,
  AgentLog,
  ConfigVersion,
  RACI_MAP,
  RACI_COLORS,
  ROUTER_ENGINE,
  getTimeAgo,
} from './types';

interface AgentConfigSidebarProps {
  agent: Agent;
  instructions: string[];
  configVersions: ConfigVersion[];
  logs: AgentLog[];
  showLogs: boolean;
  onRemoveInstruction: (agentId: string, index: number) => void;
  onRestoreVersion: (version: ConfigVersion) => void;
  onLoadLogs: (agentId: string) => void;
}

export function AgentConfigSidebar({
  agent,
  instructions,
  configVersions,
  logs,
  showLogs,
  onRemoveInstruction,
  onRestoreVersion,
  onLoadLogs,
}: AgentConfigSidebarProps) {
  const raci = RACI_MAP[agent.role] || RACI_MAP.custom;
  const tmpl = agent.template_id ? AGENT_TEMPLATES.find((t) => t.id === agent.template_id) : null;
  const routerRole = tmpl?.defaults.router_role;
  const recommended = routerRole ? ROUTER_ENGINE[routerRole] : null;
  const isAuto = !!agent.model_category;

  return (
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

      {/* SmartRouter Routing Card */}
      {recommended && (
        <div style={{
          backgroundColor: 'rgba(99, 102, 241, 0.06)',
          borderRadius: 'var(--radius-md)',
          padding: '12px',
          border: '1px solid rgba(99, 102, 241, 0.15)',
          marginBottom: '16px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6366F1" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#6366F1' }}>SmartRouter</span>
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
              <span>Router Role</span>
              <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{routerRole}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
              <span>Recommended</span>
              <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{recommended.engine}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Provider</span>
              <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{recommended.provider}</span>
            </div>
          </div>
          {isAuto ? (
            <div style={{ marginTop: '8px', padding: '4px 8px', backgroundColor: 'rgba(34, 197, 94, 0.1)', borderRadius: 'var(--radius-sm)', fontSize: '11px', color: 'var(--success)' }}>
              Auto mode -- SmartRouter selects the optimal model
            </div>
          ) : (
            <div style={{ marginTop: '8px', padding: '4px 8px', backgroundColor: 'rgba(245, 158, 11, 0.1)', borderRadius: 'var(--radius-sm)', fontSize: '11px', color: 'var(--warning)' }}>
              Manual override -- recommended: {recommended.engine} ({recommended.provider})
            </div>
          )}
        </div>
      )}

      {/* Configuration Card */}
      <div style={{ marginBottom: '16px' }}>
        <h4 style={{ margin: '0 0 10px', fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Configuration</h4>
        <div style={{
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '12px',
          border: '1px solid var(--border-light)',
        }}>
          <div style={{ marginBottom: '10px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Model</div>
            <div style={{ fontSize: '13px' }}>
              {agent.model_category ? (
                <span>
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
                    Auto ({agent.model_category})
                  </span>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{agent.model}</span>
                </span>
              ) : agent.model}
            </div>
          </div>
          <div style={{ marginBottom: '10px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Temperature</div>
            <div style={{ fontSize: '13px' }}>{agent.temperature}</div>
          </div>
          <div style={{ marginBottom: agent.system_prompt ? '10px' : 0 }}>
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '3px' }}>Services</div>
            <div style={{ fontSize: '13px' }}>
              {(agent.services || []).length > 0 ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                  {(agent.services || []).map((svc) => (
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
          {agent.system_prompt && (
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
                {agent.system_prompt}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Instructions (override system prompt) */}
      {instructions.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <h4 style={{ margin: '0 0 10px', fontSize: '13px', fontWeight: 600, color: '#F59E0B', textTransform: 'uppercase', letterSpacing: '0.5px', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            Instructions
            <span style={{ fontSize: '10px', fontWeight: 400, color: 'var(--text-tertiary)' }}>overrides prompt</span>
          </h4>
          <div style={{
            backgroundColor: 'rgba(245, 158, 11, 0.04)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(245, 158, 11, 0.15)',
            overflow: 'hidden',
          }}>
            {instructions.map((inst, i) => (
              <div key={i} style={{
                padding: '8px 10px',
                borderBottom: i < instructions.length - 1 ? '1px solid rgba(245, 158, 11, 0.1)' : 'none',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '6px',
                fontSize: '12px',
                color: 'var(--text-primary)',
                lineHeight: 1.4,
              }}>
                <span style={{ color: '#F59E0B', fontWeight: 700, fontSize: '11px', flexShrink: 0, marginTop: '1px' }}>{i + 1}.</span>
                <span style={{ flex: 1 }}>{inst}</span>
                <button
                  onClick={() => onRemoveInstruction(agent.id, i)}
                  title="Remove instruction"
                  style={{
                    padding: '0 3px',
                    backgroundColor: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-tertiary)',
                    fontSize: '12px',
                    flexShrink: 0,
                    lineHeight: 1,
                  }}
                >
                  {'\u2715'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Version History */}
      {configVersions.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <h4 style={{ margin: '0 0 10px', fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Version History</h4>
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
            overflow: 'hidden',
          }}>
            {[...configVersions].reverse().map((v, i) => {
              const isLatest = i === 0;
              const timeAgo = getTimeAgo(v.timestamp.toISOString());
              return (
                <div key={v.id} style={{
                  padding: '10px 12px',
                  borderBottom: i < configVersions.length - 1 ? '1px solid var(--border-light)' : 'none',
                  position: 'relative',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                    <span style={{
                      fontSize: '11px',
                      fontWeight: 700,
                      color: isLatest ? '#6366F1' : 'var(--text-secondary)',
                    }}>
                      v{v.version}
                    </span>
                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)' }}>
                      {v.source === 'initial' ? 'Original' : timeAgo}
                    </span>
                    {isLatest && (
                      <span style={{
                        padding: '0px 5px',
                        backgroundColor: 'rgba(99, 102, 241, 0.1)',
                        borderRadius: 'var(--radius-full)',
                        fontSize: '9px',
                        fontWeight: 600,
                        color: '#6366F1',
                      }}>
                        Current
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {v.changes.map((c, ci) => (
                      <div key={ci} style={{ fontFamily: v.source === 'chat' ? 'monospace' : 'inherit', fontSize: '10px' }}>{c}</div>
                    ))}
                  </div>
                  {!isLatest && v.source !== 'initial' && (
                    <button
                      onClick={() => onRestoreVersion(v)}
                      style={{
                        marginTop: '6px',
                        padding: '2px 8px',
                        backgroundColor: 'transparent',
                        border: '1px solid var(--border-medium)',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '10px',
                        fontWeight: 500,
                        color: 'var(--text-secondary)',
                        cursor: 'pointer',
                      }}
                    >
                      Restore
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent Runs (collapsible) */}
      <div>
        <button
          onClick={() => onLoadLogs(agent.id)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '0',
            backgroundColor: 'transparent',
            border: 'none',
            cursor: 'pointer',
            fontSize: '13px',
            fontWeight: 600,
            color: 'var(--text-secondary)',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
            marginBottom: '8px',
          }}
        >
          <span style={{ transform: showLogs ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', display: 'inline-block', fontSize: '10px' }}>
            {'\u25b8'}
          </span>
          Recent Runs
          <span style={{ fontSize: '11px', fontWeight: 400, color: 'var(--text-tertiary)' }}>
            ({agent.run_count})
          </span>
        </button>
        {showLogs && logs && (
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-light)',
            overflow: 'hidden',
          }}>
            {logs.length === 0 ? (
              <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-tertiary)' }}>
                No logs yet
              </div>
            ) : (
              logs.slice(-5).reverse().map((log) => (
                <div key={log.id} style={{
                  padding: '8px 10px',
                  borderBottom: '1px solid var(--border-light)',
                  fontSize: '11px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}>
                  <span style={{
                    width: '5px',
                    height: '5px',
                    borderRadius: '50%',
                    flexShrink: 0,
                    backgroundColor: log.level === 'error' ? 'var(--error)' : log.level === 'warning' ? 'var(--warning)' : 'var(--success)',
                  }} />
                  <span style={{ flex: 1, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{log.message}</span>
                  {log.duration_ms != null && (
                    <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', flexShrink: 0 }}>{log.duration_ms}ms</span>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
