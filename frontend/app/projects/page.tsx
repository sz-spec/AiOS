'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Plus, Search, Clock, ArrowUpDown, MoreHorizontal, Trash2, FolderOpen } from 'lucide-react';
import { useProjectStore, Project, ProjectStatus } from '@/lib/store/projectStore';
import { useConfirm } from '@/hooks/useConfirm';

const statusConfig: Record<ProjectStatus, { label: string; color: string; bg: string }> = {
  draft: { label: 'Draft', color: '#666', bg: '#f3f3f3' },
  building: { label: 'Building', color: '#d97706', bg: '#fef3c7' },
  ready: { label: 'Ready', color: '#059669', bg: '#d1fae5' },
  deployed: { label: 'Deployed', color: '#2563eb', bg: '#dbeafe' },
  error: { label: 'Error', color: '#dc2626', bg: '#fee2e2' },
};

function ProjectCard({ project }: { project: Project }) {
  const { deleteProject } = useProjectStore();
  const [showMenu, setShowMenu] = useState(false);
  const status = statusConfig[project.status];
  const timeAgo = getTimeAgo(project.updatedAt);
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ position: 'relative' }}
    >
      <Link
        href={`/projects/${project.id}`}
        style={{
          display: 'block',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-secondary)',
          overflow: 'hidden',
          transition: 'all 0.15s',
        }}
        onMouseOver={(e) => {
          e.currentTarget.style.borderColor = 'var(--border-medium)';
          e.currentTarget.style.boxShadow = 'var(--shadow-md)';
        }}
        onMouseOut={(e) => {
          e.currentTarget.style.borderColor = 'var(--border-light)';
          e.currentTarget.style.boxShadow = 'none';
        }}
      >
        {/* Thumbnail */}
        <div style={{
          height: '140px',
          background: 'linear-gradient(135deg, var(--bg-tertiary) 0%, var(--bg-hover) 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-tertiary)',
        }}>
          <FolderOpen size={32} />
        </div>

        {/* Info */}
        <div style={{ padding: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>{project.name}</h3>
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              padding: '3px 10px',
              borderRadius: 'var(--radius-full)',
              backgroundColor: status.bg,
              color: status.color,
            }}>
              {status.label}
            </span>
          </div>
          <p style={{
            margin: 0,
            fontSize: '13px',
            color: 'var(--text-secondary)',
            lineHeight: 1.4,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {project.description || 'No description'}
          </p>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            marginTop: '12px',
            fontSize: '12px',
            color: 'var(--text-tertiary)',
          }}>
            <Clock size={12} />
            {timeAgo}
          </div>
        </div>
      </Link>

      {/* Menu */}
      <div style={{ position: 'absolute', top: '8px', right: '8px' }}>
        <button
          onClick={(e) => { e.preventDefault(); setShowMenu(!showMenu); }}
          style={{
            width: '32px', height: '32px', borderRadius: 'var(--radius-sm)',
            backgroundColor: 'rgba(255,255,255,0.9)', color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <MoreHorizontal size={16} />
        </button>
        {showMenu && (
          <div style={{
            position: 'absolute', top: '36px', right: 0, width: '160px',
            backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-sm)', boxShadow: 'var(--shadow-lg)',
            overflow: 'hidden', zIndex: 10,
          }}>
            <button
              onClick={async (e) => {
                e.preventDefault();
                setShowMenu(false);
                const ok = await confirm({
                  title: `Delete "${project.name || 'this project'}"?`,
                  description: 'The project and its build history will be removed permanently.',
                  confirmLabel: 'Delete',
                  destructive: true,
                });
                if (ok) deleteProject(project.id);
              }}
              style={{
                width: '100%', padding: '10px 14px', fontSize: '13px',
                color: 'var(--error)', display: 'flex', alignItems: 'center', gap: '8px',
                backgroundColor: 'transparent', textAlign: 'left',
              }}
              onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-secondary)')}
              onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <Trash2 size={14} />
              Delete Project
            </button>
          </div>
        )}
      </div>
      <ConfirmMount />
    </motion.div>
  );
}

function getTimeAgo(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return 'Just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

export default function ProjectsPage() {
  const { projects, isLoading, searchQuery, sortBy, setSearchQuery, setSortBy, setProjects, setIsLoading, filteredProjects } = useProjectStore();

  useEffect(() => {
    async function load() {
      setIsLoading(true);
      try {
        const res = await fetch('/api/v1/projects');
        if (res.ok) {
          const data = await res.json();
          setProjects(data.projects || []);
        }
      } catch {
        // ignore
      }
      setIsLoading(false);
    }
    load();
  }, [setProjects, setIsLoading]);

  const displayed = filteredProjects();

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '48px 24px' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '32px' }}>
          <div>
            <h1 style={{ margin: '0 0 4px', fontSize: '28px', fontWeight: 600 }}>My Projects</h1>
            <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>
              {projects.length} project{projects.length !== 1 ? 's' : ''}
            </p>
          </div>
          <Link
            href="/create"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '12px 24px',
              backgroundColor: 'var(--accent)',
              color: 'white',
              borderRadius: 'var(--radius-md)',
              fontSize: '14px',
              fontWeight: 500,
            }}
          >
            <Plus size={18} />
            New Project
          </Link>
        </div>

        {/* Search + Sort */}
        {projects.length > 0 && (
          <div style={{ display: 'flex', gap: '12px', marginBottom: '24px' }}>
            <div style={{ flex: 1, position: 'relative' }}>
              <Search size={16} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
              <input
                type="text"
                placeholder="Search projects..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  width: '100%',
                  padding: '10px 12px 10px 36px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-light)',
                  backgroundColor: 'var(--bg-input)',
                  fontSize: '14px',
                  outline: 'none',
                }}
              />
            </div>
            <button
              onClick={() => {
                const next = sortBy === 'updatedAt' ? 'name' : sortBy === 'name' ? 'createdAt' : 'updatedAt';
                setSortBy(next);
              }}
              style={{
                padding: '10px 16px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-light)',
                backgroundColor: 'var(--bg-secondary)',
                fontSize: '13px',
                color: 'var(--text-secondary)',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <ArrowUpDown size={14} />
              {sortBy === 'updatedAt' ? 'Last Modified' : sortBy === 'name' ? 'Name' : 'Created'}
            </button>
          </div>
        )}

        {/* Grid */}
        {isLoading ? (
          <div style={{ textAlign: 'center', padding: '80px 24px', color: 'var(--text-secondary)' }}>
            Loading projects...
          </div>
        ) : displayed.length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '20px' }}>
            {displayed.map((p) => <ProjectCard key={p.id} project={p} />)}
          </div>
        ) : projects.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '80px 24px',
            borderRadius: 'var(--radius-lg)',
            border: '2px dashed var(--border-light)',
          }}>
            <FolderOpen size={48} style={{ color: 'var(--text-tertiary)', marginBottom: '16px' }} />
            <h3 style={{ margin: '0 0 8px', fontSize: '18px', fontWeight: 600 }}>No projects yet</h3>
            <p style={{ margin: '0 0 24px', fontSize: '14px', color: 'var(--text-secondary)' }}>
              Create your first app in minutes with AI
            </p>
            <Link
              href="/create"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                padding: '14px 28px',
                backgroundColor: 'var(--accent)',
                color: 'white',
                borderRadius: 'var(--radius-md)',
                fontSize: '15px',
                fontWeight: 500,
              }}
            >
              <Plus size={18} />
              Create Your First App
            </Link>
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--text-secondary)' }}>
            No projects match &quot;{searchQuery}&quot;
          </div>
        )}
      </div>
    </div>
  );
}
