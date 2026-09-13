'use client';

import React, { useEffect, useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useBMAD, BMADSession } from '@/hooks/useBMAD';
import { BMADWizard } from '@/components/bmad';

const MODES: Record<string, { icon: string; label: string; color: string }> = {
  simple: { icon: '⚡', label: 'Quick', color: '#f59e0b' },
  guided: { icon: '🎯', label: 'Guided', color: '#3b82f6' },
  expert: { icon: '🔧', label: 'Expert', color: '#8b5cf6' },
  party: { icon: '🎉', label: 'Party', color: '#ec4899' },
};

const PHASES = [
  'ideation', 'discovery', 'planning', 'design',
  'development', 'testing', 'review', 'deployment', 'operations'
];

// ─── Context Menu ────────────────────────────────────────────────────────────

function ContextMenu({ session, anchorPos, onClose, onEdit, onClone, onDelete }: {
  session: BMADSession;
  anchorPos: { x: number; y: number };
  onClose: () => void;
  onEdit: () => void;
  onClone: () => void;
  onDelete: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onClose]);

  const items = [
    { label: 'Edit Project', icon: 'M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7', action: onEdit },
    { label: 'Clone Project', icon: 'M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2', action: onClone },
    { label: 'Delete Project', icon: 'M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2', action: onDelete, danger: true },
  ];

  return (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        top: anchorPos.y,
        left: anchorPos.x,
        zIndex: 1000,
        minWidth: '180px',
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: '10px',
        boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
        padding: '4px',
        overflow: 'hidden',
      }}
    >
      {items.map((item, i) => (
        <button
          key={i}
          onClick={(e) => { e.stopPropagation(); item.action(); onClose(); }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            width: '100%',
            padding: '10px 12px',
            backgroundColor: 'transparent',
            border: 'none',
            borderRadius: '6px',
            fontSize: '13px',
            fontWeight: 500,
            color: item.danger ? '#ef4444' : 'var(--text-primary)',
            cursor: 'pointer',
            textAlign: 'left',
          }}
          onMouseOver={(e) => e.currentTarget.style.backgroundColor = item.danger ? '#fef2f2' : 'var(--bg-tertiary)'}
          onMouseOut={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d={item.icon} />
          </svg>
          {item.label}
        </button>
      ))}
    </div>
  );
}

// ─── Modal Shell ─────────────────────────────────────────────────────────────

function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0,0,0,0.5)',
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: '16px',
          border: '1px solid var(--border-light)',
          padding: '28px',
          width: '100%',
          maxWidth: '480px',
          boxShadow: '0 16px 48px rgba(0,0,0,0.2)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

// ─── Delete Confirmation Dialog ──────────────────────────────────────────────

function DeleteDialog({ session, onConfirm, onCancel, isLoading }: {
  session: BMADSession;
  onConfirm: () => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  return (
    <Modal onClose={onCancel}>
      <div style={{ textAlign: 'center', marginBottom: '20px' }}>
        <div style={{
          width: '56px',
          height: '56px',
          margin: '0 auto 16px',
          borderRadius: '14px',
          backgroundColor: '#fef2f2',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '28px',
        }}>
          ⚠️
        </div>
        <h3 style={{ margin: '0 0 8px', fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
          Delete &ldquo;{session.project_name}&rdquo;?
        </h3>
        <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
          Everything related to this project will be deleted and is <strong style={{ color: '#ef4444' }}>unrecoverable</strong>.
        </p>
        {/* TODO: discuss when to delete from local filesystem and GitHub to prevent mistakes */}
        <p style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
          Local files and GitHub repo will not be deleted automatically.
        </p>
      </div>
      <div style={{ display: 'flex', gap: '12px' }}>
        <button
          onClick={onCancel}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: '1px solid var(--border-light)',
            backgroundColor: 'var(--bg-primary)',
            color: 'var(--text-secondary)',
            fontSize: '14px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={isLoading}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: 'none',
            backgroundColor: '#ef4444',
            color: 'white',
            fontSize: '14px',
            fontWeight: 600,
            cursor: isLoading ? 'not-allowed' : 'pointer',
            opacity: isLoading ? 0.6 : 1,
          }}
        >
          {isLoading ? 'Deleting...' : 'Delete Project'}
        </button>
      </div>
    </Modal>
  );
}

// ─── Edit Project Dialog ─────────────────────────────────────────────────────

function EditDialog({ session, onSave, onCancel, isLoading }: {
  session: BMADSession;
  onSave: (name: string, desc: string) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [name, setName] = useState(session.project_name);
  const [desc, setDesc] = useState(session.description || '');

  return (
    <Modal onClose={onCancel}>
      <h3 style={{ margin: '0 0 20px', fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
        Edit Project
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '24px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>
            Project Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{
              width: '100%',
              padding: '12px 14px',
              border: '1px solid var(--border-light)',
              borderRadius: '8px',
              fontSize: '14px',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              outline: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>
            Description
          </label>
          <textarea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            rows={4}
            style={{
              width: '100%',
              padding: '12px 14px',
              border: '1px solid var(--border-light)',
              borderRadius: '8px',
              fontSize: '14px',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              outline: 'none',
              resize: 'none',
              boxSizing: 'border-box',
              fontFamily: 'inherit',
            }}
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: '12px' }}>
        <button
          onClick={onCancel}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: '1px solid var(--border-light)',
            backgroundColor: 'var(--bg-primary)',
            color: 'var(--text-secondary)',
            fontSize: '14px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
        <button
          onClick={() => onSave(name, desc)}
          disabled={!name.trim() || isLoading}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: 'none',
            backgroundColor: !name.trim() ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: !name.trim() ? 'var(--text-tertiary)' : 'white',
            fontSize: '14px',
            fontWeight: 600,
            cursor: !name.trim() || isLoading ? 'not-allowed' : 'pointer',
          }}
        >
          {isLoading ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </Modal>
  );
}

// ─── Clone Project Dialog ────────────────────────────────────────────────────

function CloneDialog({ session, onClone, onCancel, isLoading }: {
  session: BMADSession;
  onClone: (name: string, desc: string) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [name, setName] = useState(`${session.project_name} (Copy)`);
  const [desc, setDesc] = useState(session.description || '');

  return (
    <Modal onClose={onCancel}>
      <h3 style={{ margin: '0 0 20px', fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
        Clone Project
      </h3>
      <p style={{ margin: '0 0 16px', fontSize: '13px', color: 'var(--text-tertiary)' }}>
        Creates a copy of &ldquo;{session.project_name}&rdquo; with a new name and folder.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginBottom: '24px' }}>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>
            New Project Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{
              width: '100%',
              padding: '12px 14px',
              border: '1px solid var(--border-light)',
              borderRadius: '8px',
              fontSize: '14px',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              outline: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>
            Description
          </label>
          <textarea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            rows={4}
            style={{
              width: '100%',
              padding: '12px 14px',
              border: '1px solid var(--border-light)',
              borderRadius: '8px',
              fontSize: '14px',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              outline: 'none',
              resize: 'none',
              boxSizing: 'border-box',
              fontFamily: 'inherit',
            }}
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: '12px' }}>
        <button
          onClick={onCancel}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: '1px solid var(--border-light)',
            backgroundColor: 'var(--bg-primary)',
            color: 'var(--text-secondary)',
            fontSize: '14px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
        <button
          onClick={() => onClone(name, desc)}
          disabled={!name.trim() || isLoading}
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '10px',
            border: 'none',
            backgroundColor: !name.trim() ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: !name.trim() ? 'var(--text-tertiary)' : 'white',
            fontSize: '14px',
            fontWeight: 600,
            cursor: !name.trim() || isLoading ? 'not-allowed' : 'pointer',
          }}
        >
          {isLoading ? 'Cloning...' : 'Clone Project'}
        </button>
      </div>
    </Modal>
  );
}

// ─── Main Studio Page ────────────────────────────────────────────────────────

export default function StudioPage() {
  const router = useRouter();
  const {
    sessions,
    isLoading,
    error,
    fetchSessions,
    deleteSession,
    updateSession,
    cloneSession,
    toggleSessionActive,
    clearError,
  } = useBMAD();

  const [showWizard, setShowWizard] = useState(false);
  const [activeTab, setActiveTab] = useState<'projects' | 'templates' | 'activity'>('projects');
  const [contextMenu, setContextMenu] = useState<{ session: BMADSession; pos: { x: number; y: number } } | null>(null);
  const [editTarget, setEditTarget] = useState<BMADSession | null>(null);
  const [cloneTarget, setCloneTarget] = useState<BMADSession | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BMADSession | null>(null);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleSessionCreated = (session: BMADSession) => {
    setShowWizard(false);
    router.push(`/studio/${session.id}`);
  };

  const handleToggleActive = async (session: BMADSession, e: React.MouseEvent) => {
    e.stopPropagation();
    await toggleSessionActive(session.id);
  };

  const handleMenuOpen = (session: BMADSession, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setContextMenu({ session, pos: { x: e.clientX, y: e.clientY } });
  };

  const handleEdit = async (name: string, desc: string) => {
    if (!editTarget) return;
    await updateSession(editTarget.id, { project_name: name, description: desc });
    setEditTarget(null);
  };

  const handleClone = async (name: string, desc: string) => {
    if (!cloneTarget) return;
    await cloneSession(cloneTarget.id, name, desc);
    setCloneTarget(null);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    await deleteSession(deleteTarget.id);
    setDeleteTarget(null);
  };

  const getModeInfo = (mode: string) => MODES[mode] || MODES.guided;
  const getPhaseProgress = (session: BMADSession) => {
    const currentIndex = PHASES.indexOf(session.current_phase);
    return Math.round(((currentIndex + 1) / PHASES.length) * 100);
  };

  const stats = [
    { label: 'Active Projects', value: sessions.filter(s => s.is_active !== false && s.current_phase !== 'operations').length, icon: '📁' },
    { label: 'Deployed', value: sessions.filter(s => s.current_phase === 'operations').length, icon: '🚀' },
    { label: 'AI Agents', value: 15, icon: '🤖' },
    { label: 'Avg. Completion', value: sessions.length > 0 ? Math.round(sessions.reduce((a, s) => a + getPhaseProgress(s), 0) / sessions.length) + '%' : '0%', icon: '📊' },
  ];

  const templates = [
    { name: 'SaaS Starter', desc: 'Full-stack app with auth, billing, dashboard', icon: '💼', tags: ['Next.js', 'Stripe', 'Auth'] },
    { name: 'API Service', desc: 'REST API with documentation and tests', icon: '🔌', tags: ['FastAPI', 'OpenAPI', 'Docker'] },
    { name: 'Landing Page', desc: 'Marketing site with CMS integration', icon: '🎨', tags: ['React', 'MDX', 'Analytics'] },
    { name: 'Mobile App', desc: 'Cross-platform mobile application', icon: '📱', tags: ['React Native', 'Expo'] },
  ];

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', position: 'relative', overflow: 'hidden' }}>
      {/* Decorative animated blurs */}
      <div style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0, overflow: 'hidden' }}>
        <div style={{
          position: 'absolute',
          width: '420px',
          height: '420px',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(34,197,94,0.12) 0%, rgba(34,197,94,0) 70%)',
          filter: 'blur(40px)',
          animation: 'floatGreen 28s ease-in-out infinite',
          top: '10%',
          left: '5%',
        }} />
        <div style={{
          position: 'absolute',
          width: '380px',
          height: '380px',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(234,88,12,0.10) 0%, rgba(234,88,12,0) 70%)',
          filter: 'blur(40px)',
          animation: 'floatOrange 32s ease-in-out infinite',
          bottom: '15%',
          right: '8%',
        }} />
      </div>

      {/* Header */}
      <div style={{
        borderBottom: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-primary)',
        position: 'sticky',
        top: 0,
        zIndex: 10,
        isolation: 'isolate',
      }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '16px 24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{
                width: '42px',
                height: '42px',
                borderRadius: '12px',
                background: 'linear-gradient(135deg, #d97706, #ea580c)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontSize: '20px',
                fontWeight: 700,
              }}>
                V
              </div>
              <div>
                <h1 style={{ margin: 0, fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Project Studio
                </h1>
                <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                  Build full-stack apps with AI agents
                </p>
              </div>
            </div>
            <button
              onClick={() => setShowWizard(true)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
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
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              New Project
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px', position: 'relative', zIndex: 1 }}>
        {/* Stats Grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '16px',
          marginBottom: '32px',
        }}>
          {stats.map((stat, i) => (
            <div
              key={i}
              style={{
                padding: '20px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: '12px',
                border: '1px solid var(--border-light)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', fontWeight: 500 }}>{stat.label}</span>
                <span style={{ fontSize: '20px' }}>{stat.icon}</span>
              </div>
              <div style={{ fontSize: '28px', fontWeight: 600, color: 'var(--text-primary)' }}>{stat.value}</div>
            </div>
          ))}
        </div>

        {/* Tabs */}
        <div style={{
          display: 'flex',
          gap: '4px',
          marginBottom: '24px',
          padding: '4px',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: '10px',
          width: 'fit-content',
        }}>
          {[
            { id: 'projects', label: 'Projects', count: sessions.length },
            { id: 'templates', label: 'Templates', count: templates.length },
            { id: 'activity', label: 'Activity' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as typeof activeTab)}
              style={{
                padding: '8px 16px',
                borderRadius: '8px',
                border: 'none',
                backgroundColor: activeTab === tab.id ? 'var(--bg-primary)' : 'transparent',
                color: activeTab === tab.id ? 'var(--text-primary)' : 'var(--text-tertiary)',
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                boxShadow: activeTab === tab.id ? '0 1px 3px rgba(0,0,0,0.08)' : 'none',
              }}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span style={{
                  padding: '2px 8px',
                  backgroundColor: activeTab === tab.id ? 'var(--bg-tertiary)' : 'transparent',
                  borderRadius: '10px',
                  fontSize: '12px',
                }}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Projects Tab — Card Grid */}
        {activeTab === 'projects' && (
          <>
            {isLoading && sessions.length === 0 ? (
              <div style={{ padding: '64px', textAlign: 'center' }}>
                <div style={{
                  width: '48px',
                  height: '48px',
                  margin: '0 auto 16px',
                  border: '3px solid var(--border-light)',
                  borderTopColor: 'var(--accent)',
                  borderRadius: '50%',
                  animation: 'spin 1s linear infinite',
                }} />
                <p style={{ color: 'var(--text-tertiary)', margin: 0 }}>Loading projects...</p>
              </div>
            ) : sessions.length === 0 ? (
              <div style={{
                padding: '64px',
                textAlign: 'center',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: '16px',
                border: '1px solid var(--border-light)',
              }}>
                <div style={{
                  width: '64px',
                  height: '64px',
                  margin: '0 auto 20px',
                  backgroundColor: 'var(--bg-tertiary)',
                  borderRadius: '16px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '28px',
                }}>
                  📁
                </div>
                <h3 style={{ margin: '0 0 8px', fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  No projects yet
                </h3>
                <p style={{ margin: '0 0 20px', color: 'var(--text-tertiary)', fontSize: '14px', maxWidth: '340px', lineHeight: 1.6 }}>
                  Each project is an AI-driven workspace — from idea to deployed app, with agents handling architecture, code, and review.
                </p>
                <button
                  onClick={() => setShowWizard(true)}
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
                  Create Project
                </button>
              </div>
            ) : (
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
                gap: '16px',
              }}>
                {sessions.map((session) => {
                  const modeInfo = getModeInfo(session.mode);
                  const progress = getPhaseProgress(session);
                  const isActive = session.is_active !== false;

                  return (
                    <div
                      key={session.id}
                      onClick={() => router.push(`/studio/${session.id}`)}
                      style={{
                        backgroundColor: 'var(--bg-secondary)',
                        borderRadius: '14px',
                        border: '1px solid var(--border-light)',
                        padding: '20px',
                        cursor: 'pointer',
                        transition: 'all 0.15s',
                        opacity: isActive ? 1 : 0.55,
                      }}
                      onMouseOver={(e) => {
                        e.currentTarget.style.borderColor = 'var(--text-tertiary)';
                        e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.06)';
                      }}
                      onMouseOut={(e) => {
                        e.currentTarget.style.borderColor = 'var(--border-light)';
                        e.currentTarget.style.boxShadow = 'none';
                      }}
                    >
                      {/* Card Header */}
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '14px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
                          <div style={{
                            width: '40px',
                            height: '40px',
                            borderRadius: '10px',
                            backgroundColor: `${modeInfo.color}15`,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: '18px',
                            flexShrink: 0,
                          }}>
                            {modeInfo.icon}
                          </div>
                          <div style={{ minWidth: 0 }}>
                            <div style={{
                              fontWeight: 600,
                              color: 'var(--text-primary)',
                              fontSize: '15px',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}>
                              {session.project_name}
                            </div>
                            <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                              {session.id.slice(0, 8)}
                            </div>
                          </div>
                        </div>

                        {/* Toggle + Menu */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                          {/* On/Off Toggle */}
                          <button
                            onClick={(e) => handleToggleActive(session, e)}
                            title={isActive ? 'Active — click to deactivate' : 'Inactive — click to activate'}
                            style={{
                              width: '36px',
                              height: '20px',
                              borderRadius: '10px',
                              border: 'none',
                              backgroundColor: isActive ? '#22c55e' : 'var(--bg-tertiary)',
                              cursor: 'pointer',
                              position: 'relative',
                              transition: 'background-color 0.2s',
                              flexShrink: 0,
                            }}
                          >
                            <div style={{
                              width: '16px',
                              height: '16px',
                              borderRadius: '50%',
                              backgroundColor: 'white',
                              position: 'absolute',
                              top: '2px',
                              left: isActive ? '18px' : '2px',
                              transition: 'left 0.2s',
                              boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                            }} />
                          </button>

                          {/* 3-dot menu */}
                          <button
                            onClick={(e) => handleMenuOpen(session, e)}
                            style={{
                              padding: '4px',
                              backgroundColor: 'transparent',
                              border: 'none',
                              borderRadius: '6px',
                              color: 'var(--text-tertiary)',
                              cursor: 'pointer',
                            }}
                            onMouseOver={(e) => e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'}
                            onMouseOut={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                          >
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                              <circle cx="12" cy="5" r="1.5" />
                              <circle cx="12" cy="12" r="1.5" />
                              <circle cx="12" cy="19" r="1.5" />
                            </svg>
                          </button>
                        </div>
                      </div>

                      {/* Description */}
                      {session.description && (
                        <p style={{
                          margin: '0 0 14px',
                          fontSize: '13px',
                          color: 'var(--text-tertiary)',
                          lineHeight: 1.5,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                        }}>
                          {session.description}
                        </p>
                      )}

                      {/* Badges */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
                        <span style={{
                          padding: '3px 8px',
                          backgroundColor: 'var(--bg-tertiary)',
                          borderRadius: '6px',
                          fontSize: '11px',
                          fontWeight: 500,
                          color: 'var(--text-secondary)',
                          textTransform: 'capitalize',
                        }}>
                          {session.current_phase}
                        </span>
                        <span style={{
                          padding: '3px 8px',
                          backgroundColor: `${modeInfo.color}15`,
                          color: modeInfo.color,
                          borderRadius: '6px',
                          fontSize: '11px',
                          fontWeight: 500,
                        }}>
                          {modeInfo.label}
                        </span>
                      </div>

                      {/* Progress */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <div style={{
                          flex: 1,
                          height: '5px',
                          backgroundColor: 'var(--bg-tertiary)',
                          borderRadius: '3px',
                          overflow: 'hidden',
                        }}>
                          <div style={{
                            width: `${progress}%`,
                            height: '100%',
                            backgroundColor: 'var(--accent)',
                            borderRadius: '3px',
                            transition: 'width 0.3s',
                          }} />
                        </div>
                        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontWeight: 500, minWidth: '32px', textAlign: 'right' }}>
                          {progress}%
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* Templates Tab */}
        {activeTab === 'templates' && (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '16px',
          }}>
            {templates.map((template, i) => (
              <div
                key={i}
                onClick={() => setShowWizard(true)}
                style={{
                  padding: '24px',
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '16px',
                  border: '1px solid var(--border-light)',
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                }}
                onMouseOver={(e) => {
                  e.currentTarget.style.borderColor = 'var(--accent)';
                  e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)';
                }}
                onMouseOut={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border-light)';
                  e.currentTarget.style.boxShadow = 'none';
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px' }}>
                  <div style={{
                    width: '48px',
                    height: '48px',
                    borderRadius: '12px',
                    backgroundColor: 'var(--bg-tertiary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '24px',
                    flexShrink: 0,
                  }}>
                    {template.icon}
                  </div>
                  <div style={{ flex: 1 }}>
                    <h3 style={{ margin: '0 0 4px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {template.name}
                    </h3>
                    <p style={{ margin: '0 0 12px', fontSize: '14px', color: 'var(--text-tertiary)' }}>
                      {template.desc}
                    </p>
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                      {template.tags.map((tag, j) => (
                        <span
                          key={j}
                          style={{
                            padding: '3px 8px',
                            backgroundColor: 'var(--bg-tertiary)',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 500,
                            color: 'var(--text-secondary)',
                          }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Activity Tab */}
        {activeTab === 'activity' && (
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '16px',
            border: '1px solid var(--border-light)',
            padding: '24px',
          }}>
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div style={{
                width: '64px',
                height: '64px',
                margin: '0 auto 16px',
                backgroundColor: 'var(--bg-tertiary)',
                borderRadius: '16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '28px',
              }}>
                📋
              </div>
              <h3 style={{ margin: '0 0 8px', fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                No activity yet
              </h3>
              <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-tertiary)' }}>
                Activity from your projects will appear here
              </p>
            </div>
          </div>
        )}

        {/* Quick Actions */}
        <div style={{
          marginTop: '32px',
          padding: '24px',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: '16px',
          border: '1px solid var(--border-light)',
        }}>
          <h3 style={{ margin: '0 0 16px', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
            Quick Actions
          </h3>
          <div style={{ display: 'flex', gap: '12px' }}>
            {[
              { icon: '📝', label: 'Import from GitHub', action: () => {} },
              { icon: '📋', label: 'Clone Template', action: () => setShowWizard(true) },
              { icon: '📤', label: 'Export Project', action: () => {} },
              { icon: '⚙️', label: 'Settings', action: () => router.push('/settings') },
            ].map((action, i) => (
              <button
                key={i}
                onClick={action.action}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '10px 16px',
                  backgroundColor: 'var(--bg-primary)',
                  border: '1px solid var(--border-light)',
                  borderRadius: '8px',
                  fontSize: '13px',
                  fontWeight: 500,
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                }}
                onMouseOver={(e) => {
                  e.currentTarget.style.borderColor = 'var(--text-tertiary)';
                  e.currentTarget.style.color = 'var(--text-primary)';
                }}
                onMouseOut={(e) => {
                  e.currentTarget.style.borderColor = 'var(--border-light)';
                  e.currentTarget.style.color = 'var(--text-secondary)';
                }}
              >
                <span>{action.icon}</span>
                {action.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Wizard Modal Overlay ── */}
      {showWizard && (
        <div style={{
          position: 'fixed',
          inset: 0,
          zIndex: 50,
          backgroundColor: 'var(--bg-primary)',
          overflowY: 'auto',
        }}>
          <BMADWizard
            onSessionCreated={handleSessionCreated}
            onClose={() => setShowWizard(false)}
          />
        </div>
      )}

      {/* ── Context Menu ── */}
      {contextMenu && (
        <ContextMenu
          session={contextMenu.session}
          anchorPos={contextMenu.pos}
          onClose={() => setContextMenu(null)}
          onEdit={() => setEditTarget(contextMenu.session)}
          onClone={() => setCloneTarget(contextMenu.session)}
          onDelete={() => setDeleteTarget(contextMenu.session)}
        />
      )}

      {/* ── Dialogs ── */}
      {editTarget && (
        <EditDialog
          session={editTarget}
          onSave={handleEdit}
          onCancel={() => setEditTarget(null)}
          isLoading={isLoading}
        />
      )}
      {cloneTarget && (
        <CloneDialog
          session={cloneTarget}
          onClone={handleClone}
          onCancel={() => setCloneTarget(null)}
          isLoading={isLoading}
        />
      )}
      {deleteTarget && (
        <DeleteDialog
          session={deleteTarget}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
          isLoading={isLoading}
        />
      )}

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
          zIndex: 200,
        }}>
          <span style={{ fontSize: '20px' }}>⚠️</span>
          <div>
            <div style={{ fontWeight: 500, color: '#dc2626', fontSize: '14px' }}>Error</div>
            <div style={{ fontSize: '13px', color: '#b91c1c' }}>{error}</div>
          </div>
          <button
            onClick={clearError}
            style={{
              padding: '4px',
              backgroundColor: 'transparent',
              border: 'none',
              color: '#dc2626',
              cursor: 'pointer',
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes floatGreen {
          0%   { transform: translate(0, 0); }
          15%  { transform: translate(12vw, 8vh); }
          35%  { transform: translate(5vw, 25vh); }
          55%  { transform: translate(20vw, 15vh); }
          75%  { transform: translate(8vw, 5vh); }
          100% { transform: translate(0, 0); }
        }
        @keyframes floatOrange {
          0%   { transform: translate(0, 0); }
          20%  { transform: translate(-15vw, -10vh); }
          40%  { transform: translate(-8vw, -22vh); }
          60%  { transform: translate(-20vw, -8vh); }
          80%  { transform: translate(-5vw, -18vh); }
          100% { transform: translate(0, 0); }
        }
      `}</style>
    </div>
  );
}
