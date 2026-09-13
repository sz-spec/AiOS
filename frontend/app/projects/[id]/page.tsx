'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { ChevronLeft, Eye, Code, Settings, Rocket, MessageSquare } from 'lucide-react';
import { useProjectStore, Project } from '@/lib/store/projectStore';
import { useVisualEditStore } from '@/lib/store/visualEditStore';
import { BuildProgressPanel } from '@/components/build/BuildProgressPanel';
import { LivePreview } from '@/components/preview/LivePreview';
import { VisualEditMode } from '@/components/visual-edit/VisualEditMode';
import { ProjectChat } from '@/components/chat/ProjectChat';
import { VersionTimeline } from '@/components/history/VersionTimeline';

type Tab = 'preview' | 'code' | 'settings';

export default function ProjectWorkspace() {
  const params = useParams();
  const projectId = params.id as string;
  const { currentProject, setCurrentProject, updateProject } = useProjectStore();
  const { isActive: isVisualEdit, setActive: setVisualEditActive } = useVisualEditStore();
  const [activeTab, setActiveTab] = useState<Tab>('preview');
  const [isLoading, setIsLoading] = useState(true);
  const [showChat, setShowChat] = useState(false);
  const [localFiles, setLocalFiles] = useState<Record<string, string>>({});

  useEffect(() => {
    async function load() {
      setIsLoading(true);
      try {
        const res = await fetch(`/api/v1/projects/${projectId}`);
        if (res.ok) {
          const data = await res.json();
          setCurrentProject(data);
          setLocalFiles(data.files || {});
        }
      } catch {
        // ignore
      }
      setIsLoading(false);
    }
    load();
    return () => setCurrentProject(null);
  }, [projectId, setCurrentProject]);

  const handleBuildComplete = useCallback(() => {
    updateProject(projectId, { status: 'ready' });
    // Reload project to get generated files
    fetch(`/api/v1/projects/${projectId}`)
      .then((r) => r.json())
      .then((data) => {
        setCurrentProject(data);
        setLocalFiles(data.files || {});
      })
      .catch(() => {});
  }, [projectId, updateProject, setCurrentProject]);

  const handleFilesUpdated = useCallback((files: Record<string, string>) => {
    setLocalFiles((prev) => ({ ...prev, ...files }));
    updateProject(projectId, { files: { ...(currentProject?.files || {}), ...files } });
  }, [projectId, updateProject, currentProject]);

  const handleCheckpointRestore = useCallback((_cpId: string) => {
    // Reload project files after restore
    fetch(`/api/v1/projects/${projectId}`)
      .then((r) => r.json())
      .then((data) => {
        setCurrentProject(data);
        setLocalFiles(data.files || {});
      })
      .catch(() => {});
  }, [projectId, setCurrentProject]);

  if (isLoading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
        Loading project...
      </div>
    );
  }

  if (!currentProject) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '16px' }}>
        <div style={{ fontSize: '18px', fontWeight: 600 }}>Project not found</div>
        <Link href="/projects" style={{ color: 'var(--accent)' }}>Back to Projects</Link>
      </div>
    );
  }

  const isBuilding = currentProject.status === 'building';
  const hasFiles = Object.keys(localFiles).length > 0;

  const tabs: { id: Tab; label: string; icon: typeof Eye }[] = [
    { id: 'preview', label: 'Preview', icon: Eye },
    { id: 'code', label: 'Code', icon: Code },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-primary)' }}>
      {/* Top Bar */}
      <div style={{
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid var(--border-light)',
        backgroundColor: 'var(--bg-secondary)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link
            href="/projects"
            style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}
          >
            <ChevronLeft size={16} />
          </Link>
          <h1 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>{currentProject.name}</h1>
          <span style={{
            fontSize: '11px',
            fontWeight: 600,
            padding: '2px 8px',
            borderRadius: 'var(--radius-full)',
            backgroundColor: currentProject.status === 'ready' ? '#d1fae5' : currentProject.status === 'building' ? '#fef3c7' : '#f3f3f3',
            color: currentProject.status === 'ready' ? '#065f46' : currentProject.status === 'building' ? '#92400e' : '#666',
          }}>
            {currentProject.status}
          </span>
        </div>

        <div style={{ display: 'flex', gap: '4px' }}>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  padding: '6px 14px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                  fontWeight: 500,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  backgroundColor: isActive ? 'var(--bg-primary)' : 'transparent',
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                  border: isActive ? '1px solid var(--border-light)' : '1px solid transparent',
                }}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={() => setShowChat(!showChat)}
            style={{
              padding: '8px 14px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: showChat ? 'var(--bg-accent-light)' : 'transparent',
              color: showChat ? 'var(--accent)' : 'var(--text-secondary)',
              border: '1px solid var(--border-light)',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <MessageSquare size={14} />
            Chat
          </button>
          <button
            style={{
              padding: '8px 20px',
              borderRadius: 'var(--radius-sm)',
              backgroundColor: 'var(--accent)',
              color: 'white',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <Rocket size={14} />
            Publish
          </button>
        </div>
      </div>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {isBuilding ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <BuildProgressPanel
              projectId={projectId}
              onComplete={handleBuildComplete}
            />
          </div>
        ) : activeTab === 'preview' ? (
          <VisualEditMode>
            <div style={{ flex: 1, display: 'flex' }}>
              <LivePreview
                files={localFiles}
                onVisualEditToggle={() => setVisualEditActive(!isVisualEdit)}
              />
            </div>
          </VisualEditMode>
        ) : activeTab === 'code' ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)' }}>
            <div style={{ textAlign: 'center' }}>
              <Code size={48} style={{ color: 'var(--text-tertiary)', marginBottom: '16px' }} />
              <div style={{ fontSize: '16px', fontWeight: 500, marginBottom: '8px' }}>Code View</div>
              <div style={{ fontSize: '14px' }}>
                {Object.keys(localFiles).length} files generated
              </div>
              {Object.keys(localFiles).length > 0 && (
                <div style={{ marginTop: '16px', textAlign: 'left', maxWidth: '400px' }}>
                  {Object.keys(localFiles).map((path) => (
                    <div key={path} style={{ fontSize: '13px', padding: '4px 0', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                      {path}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, padding: '32px', maxWidth: '640px' }}>
            <h2 style={{ margin: '0 0 24px', fontSize: '18px', fontWeight: 600 }}>Project Settings</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div>
                <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
                  Project Name
                </label>
                <input
                  value={currentProject.name}
                  readOnly
                  style={{
                    width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-light)', backgroundColor: 'var(--bg-input)', fontSize: '14px',
                  }}
                />
              </div>
              <div>
                <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
                  Category
                </label>
                <div style={{ fontSize: '14px', textTransform: 'capitalize' }}>{currentProject.category}</div>
              </div>
              <div>
                <label style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
                  Description
                </label>
                <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>{currentProject.description}</div>
              </div>
            </div>
          </div>
        )}

        {/* Chat Panel (right side) */}
        {showChat && !isBuilding && (
          <div style={{
            width: '360px',
            borderLeft: '1px solid var(--border-light)',
            flexShrink: 0,
          }}>
            <ProjectChat
              projectId={projectId}
              onFilesUpdated={handleFilesUpdated}
            />
          </div>
        )}
      </div>

      {/* Version Timeline */}
      {hasFiles && !isBuilding && (
        <VersionTimeline
          projectId={projectId}
          onRestore={handleCheckpointRestore}
        />
      )}
    </div>
  );
}
