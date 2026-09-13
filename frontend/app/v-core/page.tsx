/**
 * V Core Page
 * 
 * Complete V Core dashboard with all modules:
 * - Control Plane
 * - Business Core
 * - Workflow Engine
 * - Mission Control
 */

'use client';

import React, { useState, useEffect } from 'react';
import { useVCore } from '../../hooks/useVCore';
import {
  OrganizationCard,
  MemberList,
  InviteModal,
  EntityCard,
  RecordTable,
  RecordForm,
  WorkflowCard,
  ExecutionList,
  MissionControlDashboard,
} from '../../components/v-core';

type Tab = 'dashboard' | 'control-plane' | 'business-core' | 'workflows' | 'dev-guide';

export default function VCorePage() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [showRecordForm, setShowRecordForm] = useState(false);

  const {
    isLoading,
    error,
    // Control Plane
    organizations,
    currentOrg,
    members,
    roles,
    loadOrganizations,
    loadMembers,
    inviteMember,
    // Business Core
    entities,
    records,
    loadEntities,
    loadRecords,
    createRecord,
    deleteRecord,
    // Workflows
    workflows,
    executions,
    loadWorkflows,
    loadExecutions,
    activateWorkflow,
    pauseWorkflow,
    executeWorkflow,
    // Mission Control
    dashboard,
    loadDashboard,
    approveRequest,
    rejectRequest,
    resolveAlert,
  } = useVCore();

  // Load initial data
  useEffect(() => {
    loadOrganizations();
    loadEntities();
    loadWorkflows();
    loadDashboard();
  }, []);

  useEffect(() => {
    if (currentOrg) {
      loadMembers(currentOrg.id);
    }
  }, [currentOrg]);

  useEffect(() => {
    if (selectedEntity) {
      loadRecords(selectedEntity);
    }
  }, [selectedEntity]);

  const selectedEntityData = entities.find(e => e.id === selectedEntity);

  const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: 'dashboard', label: 'Mission Control', icon: '🎛️' },
    { id: 'control-plane', label: 'Control Plane', icon: '👥' },
    { id: 'business-core', label: 'Business Core', icon: '📊' },
    { id: 'workflows', label: 'Workflows', icon: '⚡' },
    { id: 'dev-guide', label: 'Dev Guide', icon: '📖' },
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-background-secondary, #f5f5f7)' }}>
      {/* Header */}
      <header
        style={{
          background: 'white',
          borderBottom: '1px solid var(--color-border, #e5e5ea)',
          padding: '1rem 2rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <h1 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600 }}>
            <span style={{ background: 'linear-gradient(135deg, #007AFF, #5856D6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              V Core
            </span>
          </h1>
          <span style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            The Brain
          </span>
        </div>

        {/* Tab Navigation */}
        <nav style={{ display: 'flex', gap: '0.5rem' }}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                padding: '0.625rem 1rem',
                background: activeTab === tab.id ? 'var(--color-accent, #007AFF)' : 'transparent',
                color: activeTab === tab.id ? 'white' : 'var(--color-text-secondary, #6e6e73)',
                border: 'none',
                borderRadius: '8px',
                fontSize: '0.875rem',
                fontWeight: 500,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                transition: 'all 0.2s',
              }}
            >
              <span>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>

        {currentOrg && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <div
              style={{
                width: '32px',
                height: '32px',
                borderRadius: '8px',
                background: 'linear-gradient(135deg, #007AFF, #5856D6)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontWeight: 600,
                fontSize: '0.875rem',
              }}
            >
              {currentOrg.name.charAt(0)}
            </div>
            <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>{currentOrg.name}</span>
          </div>
        )}
      </header>

      {/* Main Content */}
      <main style={{ padding: '2rem', maxWidth: '1400px', margin: '0 auto' }}>
        {isLoading && (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            Loading...
          </div>
        )}

        {error && (
          <div style={{ padding: '1rem', background: '#fee2e2', color: '#dc2626', borderRadius: '8px', marginBottom: '1rem' }}>
            {error}
          </div>
        )}

        {/* Mission Control Tab */}
        {activeTab === 'dashboard' && dashboard && (
          <MissionControlDashboard
            data={dashboard}
            onApprove={approveRequest}
            onReject={rejectRequest}
            onResolveAlert={resolveAlert}
          />
        )}

        {/* Control Plane Tab */}
        {activeTab === 'control-plane' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
            {/* Organizations */}
            <div>
              <h2 style={{ margin: '0 0 1rem', fontSize: '1.25rem', fontWeight: 600 }}>🏢 Organizations</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {organizations.map((org) => (
                  <OrganizationCard
                    key={org.id}
                    org={org}
                    isSelected={currentOrg?.id === org.id}
                  />
                ))}
              </div>
            </div>

            {/* Team Members */}
            <div>
              <MemberList members={members} onInvite={() => setShowInviteModal(true)} />
            </div>
          </div>
        )}

        {/* Business Core Tab */}
        {activeTab === 'business-core' && (
          <div style={{ display: 'grid', gridTemplateColumns: selectedEntity ? '300px 1fr' : '1fr', gap: '2rem' }}>
            {/* Entity List */}
            <div>
              <h2 style={{ margin: '0 0 1rem', fontSize: '1.25rem', fontWeight: 600 }}>📋 Entities</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {entities.map((entity) => (
                  <EntityCard
                    key={entity.id}
                    entity={entity}
                    onClick={() => setSelectedEntity(entity.id)}
                  />
                ))}
              </div>
            </div>

            {/* Records View */}
            {selectedEntity && selectedEntityData && (
              <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', overflow: 'hidden' }}>
                <div style={{ padding: '1rem 1.5rem', borderBottom: '1px solid var(--color-border, #e5e5ea)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <span style={{ fontSize: '1.5rem' }}>{selectedEntityData.icon}</span>
                    <div>
                      <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 600 }}>{selectedEntityData.labelPlural}</h3>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
                        {records.length} records
                      </span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      onClick={() => setShowRecordForm(true)}
                      style={{
                        padding: '0.5rem 1rem',
                        background: 'var(--color-accent, #007AFF)',
                        color: 'white',
                        border: 'none',
                        borderRadius: '8px',
                        fontSize: '0.875rem',
                        fontWeight: 500,
                        cursor: 'pointer',
                      }}
                    >
                      + New {selectedEntityData.label}
                    </button>
                    <button
                      onClick={() => setSelectedEntity(null)}
                      style={{
                        padding: '0.5rem 0.75rem',
                        background: 'var(--color-background-secondary, #f5f5f7)',
                        border: 'none',
                        borderRadius: '8px',
                        fontSize: '0.875rem',
                        cursor: 'pointer',
                      }}
                    >
                      ✕
                    </button>
                  </div>
                </div>

                {showRecordForm ? (
                  <div style={{ padding: '1.5rem' }}>
                    <RecordForm
                      entity={selectedEntityData}
                      onSubmit={async (data) => {
                        await createRecord(selectedEntity, data);
                        setShowRecordForm(false);
                        loadRecords(selectedEntity);
                      }}
                      onCancel={() => setShowRecordForm(false)}
                    />
                  </div>
                ) : (
                  <RecordTable
                    entity={selectedEntityData}
                    records={records}
                    onDelete={async (id) => {
                      await deleteRecord(id);
                      loadRecords(selectedEntity);
                    }}
                  />
                )}
              </div>
            )}
          </div>
        )}

        {/* Workflows Tab */}
        {activeTab === 'workflows' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <h2 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 600 }}>⚡ Workflows</h2>
              <div style={{ display: 'flex', gap: '1rem', fontSize: '0.875rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
                <span>📊 {workflows.length} workflows</span>
                <span>✓ {workflows.filter(w => w.status === 'active').length} active</span>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: '1rem' }}>
              {workflows.map((workflow) => (
                <WorkflowCard
                  key={workflow.id}
                  workflow={workflow}
                  onActivate={() => activateWorkflow(workflow.id)}
                  onPause={() => pauseWorkflow(workflow.id)}
                  onRun={async () => {
                    await executeWorkflow(workflow.id, {});
                    loadWorkflows();
                  }}
                />
              ))}
            </div>
          </div>
        )}

        {/* Dev Guide Tab */}
        {activeTab === 'dev-guide' && (
          <div style={{ maxWidth: '1000px' }}>
            {/* Overview */}
            <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '24px', marginBottom: '24px' }}>
              <h2 style={{ margin: '0 0 12px', fontSize: '20px', fontWeight: 600 }}>V-Core: The Business Brain</h2>
              <p style={{ margin: 0, color: 'var(--color-text-secondary, #6e6e73)', lineHeight: 1.6 }}>
                V-Core is the central business operating system. It manages organizations, users, custom data entities,
                automated workflows, and provides a mission control dashboard for monitoring. Connected to the real backend
                at <code>/api/v-core/*</code>.
              </p>
            </div>

            {/* Current Status */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '24px' }}>
              {[
                { module: 'Control Plane', status: organizations.length > 0 ? 'Connected' : error ? 'Error' : 'Loading', color: organizations.length > 0 ? '#059669' : error ? '#dc2626' : '#f59e0b', desc: 'Orgs, Users, Roles' },
                { module: 'Business Core', status: entities.length > 0 ? 'Connected' : error ? 'Error' : 'Loading', color: entities.length > 0 ? '#059669' : error ? '#dc2626' : '#f59e0b', desc: 'Entities, Records' },
                { module: 'Workflows', status: workflows.length > 0 ? 'Connected' : error ? 'Error' : 'Loading', color: workflows.length > 0 ? '#059669' : error ? '#dc2626' : '#f59e0b', desc: 'Automation' },
                { module: 'Mission Control', status: dashboard ? 'Connected' : error ? 'Error' : 'Loading', color: dashboard ? '#059669' : error ? '#dc2626' : '#f59e0b', desc: 'Dashboard' },
              ].map((item) => (
                <div key={item.module} style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '16px' }}>
                  <div style={{ fontSize: '14px', fontWeight: 600, marginBottom: '4px' }}>{item.module}</div>
                  <div style={{ fontSize: '12px', color: 'var(--color-text-secondary)', marginBottom: '8px' }}>{item.desc}</div>
                  <span style={{ padding: '4px 8px', background: item.color + '20', color: item.color, borderRadius: '4px', fontSize: '11px', fontWeight: 500 }}>
                    {item.status}
                  </span>
                </div>
              ))}
            </div>

            {/* Two Column Layout */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginBottom: '24px' }}>
              {/* Backend Files */}
              <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '24px' }}>
                <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: '#059669' }}>Backend (Already Created)</h3>
                <ul style={{ margin: 0, padding: '0 0 0 20px', lineHeight: 2, fontSize: '14px' }}>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>backend/core/control_plane.py</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>backend/core/business_core.py</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>backend/core/workflow_engine.py</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>backend/core/mission_control.py</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>backend/api/v_core_routes.py</code></li>
                </ul>
              </div>

              {/* Frontend Files */}
              <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '24px' }}>
                <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600, color: '#007AFF' }}>Frontend (Already Created)</h3>
                <ul style={{ margin: 0, padding: '0 0 0 20px', lineHeight: 2, fontSize: '14px' }}>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>hooks/useVCore.ts</code> - <span style={{ color: '#059669' }}>Connected to real API</span></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>components/v-core/VCoreComponents.tsx</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>components/v-core/VCoreComponents2.tsx</code></li>
                  <li><code style={{ background: '#f3f3f3', padding: '2px 6px', borderRadius: '4px', fontSize: '12px' }}>app/v-core/page.tsx</code></li>
                </ul>
              </div>
            </div>

            {/* What Needs to be Done */}
            <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '24px', marginBottom: '24px' }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600 }}>Implementation Tasks</h3>
              <ol style={{ margin: 0, padding: '0 0 0 20px', lineHeight: 2.2 }}>
                <li><strong>Connect useVCore to real API</strong> - Replace mock data in <code>hooks/useVCore.ts</code> with actual fetch calls to <code>/api/v-core/*</code></li>
                <li><strong>Add database persistence</strong> - Backend files exist but need database models (SQLAlchemy/PostgreSQL)</li>
                <li><strong>Implement authentication</strong> - Add JWT auth to protect V-Core endpoints</li>
                <li><strong>Build Organization Switcher</strong> - UI to switch between organizations</li>
                <li><strong>Add Entity Builder</strong> - UI to create custom entities with drag-drop field builder</li>
                <li><strong>Implement Workflow Designer</strong> - Visual workflow builder (trigger → conditions → actions)</li>
                <li><strong>Add Real-time Updates</strong> - WebSocket for live mission control dashboard</li>
                <li><strong>Build Approval Flow</strong> - Notification system for pending approvals</li>
              </ol>
            </div>

            {/* API Endpoints */}
            <div style={{ background: 'white', borderRadius: '12px', border: '1px solid var(--color-border, #e5e5ea)', padding: '24px' }}>
              <h3 style={{ margin: '0 0 16px', fontSize: '16px', fontWeight: 600 }}>API Endpoints (in v_core_routes.py)</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div>
                  <h4 style={{ margin: '0 0 8px', fontSize: '14px', fontWeight: 600 }}>Control Plane</h4>
                  <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: '13px', lineHeight: 1.8 }}>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/organizations</li>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/organizations/:id/members</li>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/roles</li>
                  </ul>
                </div>
                <div>
                  <h4 style={{ margin: '0 0 8px', fontSize: '14px', fontWeight: 600 }}>Business Core</h4>
                  <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: '13px', lineHeight: 1.8 }}>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/entities</li>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/entities/:id/records</li>
                    <li><code style={{ color: '#007AFF' }}>DELETE</code> /api/v-core/records/:id</li>
                  </ul>
                </div>
                <div>
                  <h4 style={{ margin: '0 0 8px', fontSize: '14px', fontWeight: 600 }}>Workflows</h4>
                  <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: '13px', lineHeight: 1.8 }}>
                    <li><code style={{ color: '#007AFF' }}>GET/POST</code> /api/v-core/workflows</li>
                    <li><code style={{ color: '#007AFF' }}>POST</code> /api/v-core/workflows/:id/execute</li>
                    <li><code style={{ color: '#007AFF' }}>PUT</code> /api/v-core/workflows/:id/status</li>
                  </ul>
                </div>
                <div>
                  <h4 style={{ margin: '0 0 8px', fontSize: '14px', fontWeight: 600 }}>Mission Control</h4>
                  <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: '13px', lineHeight: 1.8 }}>
                    <li><code style={{ color: '#007AFF' }}>GET</code> /api/v-core/dashboard</li>
                    <li><code style={{ color: '#007AFF' }}>POST</code> /api/v-core/approvals/:id/approve</li>
                    <li><code style={{ color: '#007AFF' }}>POST</code> /api/v-core/alerts/:id/resolve</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Invite Modal */}
      {currentOrg && (
        <InviteModal
          isOpen={showInviteModal}
          onClose={() => setShowInviteModal(false)}
          onInvite={async (email, roleId) => {
            await inviteMember(currentOrg.id, email, roleId);
            loadMembers(currentOrg.id);
          }}
          roles={roles}
        />
      )}
    </div>
  );
}
