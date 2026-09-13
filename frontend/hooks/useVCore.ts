/**
 * V Core React Hook
 * 
 * Complete hook for V Core modules:
 * - Control Plane (users, orgs, roles)
 * - Business Core (entities, records)
 * - Workflow Engine (workflows, executions)
 * - Mission Control (dashboard, approvals, alerts)
 */

import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

// Use relative URLs so requests go through Next.js proxy (avoids CORS)

// ============================================
// Types
// ============================================

// Control Plane
export interface User {
  id: string;
  email: string;
  name: string;
  avatarUrl?: string;
  status: 'active' | 'invited' | 'suspended' | 'deleted';
  mfaEnabled: boolean;
  lastLogin?: string;
  createdAt: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  logoUrl?: string;
  plan: 'free' | 'starter' | 'professional' | 'enterprise';
  maxUsers: number;
  maxAgents: number;
  maxWorkflows: number;
  isActive: boolean;
  createdAt: string;
}

export interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  isSystem: boolean;
}

export interface Member {
  id: string;
  userId: string;
  roleId: string;
  isOwner: boolean;
  user: User;
  role: Role;
}

// Business Core
export interface FieldDefinition {
  id: string;
  name: string;
  label: string;
  type: string;
  options?: { value: string; label: string; color?: string }[];
  isRequired: boolean;
  isUnique: boolean;
  defaultValue?: any;
  order: number;
}

export interface EntityDefinition {
  id: string;
  name: string;
  label: string;
  labelPlural: string;
  description: string;
  icon: string;
  color: string;
  fields: FieldDefinition[];
  primaryFieldId?: string;
  defaultView: string;
  createdAt: string;
}

export interface VCoreRecord {
  id: string;
  entityId: string;
  data: Record<string, any>;
  createdBy: string;
  updatedBy: string;
  createdAt: string;
  updatedAt: string;
}

// Workflow Engine
export interface WorkflowNode {
  id: string;
  type: string;
  name: string;
  config: Record<string, any>;
  nextNodes: string[];
  positionX: number;
  positionY: number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  status: 'draft' | 'active' | 'paused' | 'archived';
  nodes: WorkflowNode[];
  startNodeId?: string;
  runCount: number;
  successCount: number;
  failureCount: number;
  lastRunAt?: string;
  createdAt: string;
}

export interface WorkflowExecution {
  id: string;
  workflowId: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  triggerData: Record<string, any>;
  startedAt?: string;
  completedAt?: string;
  error?: string;
}

// Mission Control
export interface Activity {
  id: string;
  type: string;
  sourceType: string;
  sourceName: string;
  action: string;
  description: string;
  status: string;
  durationMs: number;
  tokensUsed: number;
  costUsd: number;
  startedAt: string;
}

export interface ApprovalRequest {
  id: string;
  title: string;
  description: string;
  category: string;
  requestedByName: string;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  actionType: string;
  actionData: Record<string, any>;
  createdAt: string;
}

export interface Alert {
  id: string;
  title: string;
  message: string;
  severity: 'info' | 'warning' | 'error' | 'critical';
  category: string;
  isRead: boolean;
  isResolved: boolean;
  createdAt: string;
}

export interface Metric {
  id: string;
  name: string;
  description: string;
  type: string;
  unit: string;
  category: string;
  value: number;
}

export interface DashboardData {
  metrics: Record<string, number>;
  activitySummary: {
    total: number;
    successful: number;
    failed: number;
    successRate: number;
    totalTokens: number;
    totalCost: number;
  };
  recentActivities: Activity[];
  pendingApprovals: ApprovalRequest[];
  alerts: {
    total: number;
    unread: number;
    critical: number;
    error: number;
    warning: number;
  };
  recentAlerts: Alert[];
}

// ============================================
// Hook
// ============================================

export interface UseVCoreReturn {
  // State
  isLoading: boolean;
  error: string | null;
  
  // Control Plane
  organizations: Organization[];
  currentOrg: Organization | null;
  members: Member[];
  roles: Role[];
  loadOrganizations: () => Promise<void>;
  loadMembers: (orgId: string) => Promise<void>;
  createOrganization: (name: string, plan?: string) => Promise<Organization>;
  inviteMember: (orgId: string, email: string, roleId: string) => Promise<void>;
  
  // Business Core
  entities: EntityDefinition[];
  records: VCoreRecord[];
  loadEntities: () => Promise<void>;
  loadRecords: (entityId: string) => Promise<void>;
  createEntity: (name: string, label: string, fields?: any[]) => Promise<EntityDefinition>;
  createRecord: (entityId: string, data: Record<string, any>) => Promise<VCoreRecord>;
  updateRecord: (recordId: string, data: Record<string, any>) => Promise<VCoreRecord>;
  deleteRecord: (recordId: string) => Promise<void>;
  
  // Workflow Engine
  workflows: Workflow[];
  executions: WorkflowExecution[];
  loadWorkflows: () => Promise<void>;
  loadExecutions: (workflowId: string) => Promise<void>;
  createWorkflow: (name: string, description?: string) => Promise<Workflow>;
  activateWorkflow: (workflowId: string) => Promise<void>;
  pauseWorkflow: (workflowId: string) => Promise<void>;
  executeWorkflow: (workflowId: string, triggerData?: Record<string, any>) => Promise<WorkflowExecution>;
  
  // Mission Control
  dashboard: DashboardData | null;
  activities: Activity[];
  approvals: ApprovalRequest[];
  alerts: Alert[];
  metrics: Metric[];
  loadDashboard: () => Promise<void>;
  loadActivities: () => Promise<void>;
  loadApprovals: () => Promise<void>;
  loadAlerts: () => Promise<void>;
  loadMetrics: () => Promise<void>;
  approveRequest: (approvalId: string, note?: string) => Promise<void>;
  rejectRequest: (approvalId: string, note?: string) => Promise<void>;
  resolveAlert: (alertId: string) => Promise<void>;
}

export function useVCore(): UseVCoreReturn {
  // Phase v17 (F-H4): Clerk auth token for all API requests
  const { getToken } = useAuth();

  // State
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Control Plane State
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [currentOrg, setCurrentOrg] = useState<Organization | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  
  // Business Core State
  const [entities, setEntities] = useState<EntityDefinition[]>([]);
  const [records, setRecords] = useState<VCoreRecord[]>([]);
  
  // Workflow Engine State
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [executions, setExecutions] = useState<WorkflowExecution[]>([]);
  
  // Mission Control State
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);

  // API Helper — W3.3: apiFetch handles Authorization + X-CSRF-Token + 401 retry.
  const apiCall = useCallback(async (endpoint: string, options?: RequestInit) => {
    const response = await apiFetch(getToken, endpoint, options);
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(err.detail || 'Request failed');
    }
    return response.json();
  }, [getToken]);

  // ==========================================
  // Control Plane
  // ==========================================

  const loadOrganizations = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiCall('/api/v-core/organizations');
      setOrganizations(data.organizations);
      if (data.organizations.length > 0 && !currentOrg) {
        setCurrentOrg(data.organizations[0]);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall, currentOrg]);

  const loadMembers = useCallback(async (orgId: string) => {
    setIsLoading(true);
    try {
      const data = await apiCall(`/api/v-core/organizations/${orgId}/members`);
      setMembers(data.members);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const createOrganization = useCallback(async (name: string, plan: string = 'free') => {
    setIsLoading(true);
    try {
      const data = await apiCall('/api/v-core/organizations', {
        method: 'POST',
        body: JSON.stringify({ name, plan }),
      });
      setOrganizations(prev => [...prev, data.organization]);
      return data.organization;
    } catch (e: any) {
      setError(e.message);
      throw e;
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const inviteMember = useCallback(async (orgId: string, email: string, roleId: string) => {
    await apiCall(`/api/v-core/organizations/${orgId}/invitations`, {
      method: 'POST',
      body: JSON.stringify({ email, role_id: roleId }),
    });
  }, [apiCall]);

  // ==========================================
  // Business Core
  // ==========================================

  const loadEntities = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiCall('/api/v-core/entities');
      setEntities(data.entities);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const loadRecords = useCallback(async (entityId: string) => {
    setIsLoading(true);
    try {
      const data = await apiCall(`/api/v-core/entities/${entityId}/records`);
      setRecords(data.records);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const createEntity = useCallback(async (name: string, label: string, fields: any[] = []) => {
    const data = await apiCall('/api/v-core/entities', {
      method: 'POST',
      body: JSON.stringify({ name, label, fields }),
    });
    setEntities(prev => [...prev, data.entity]);
    return data.entity;
  }, [apiCall]);

  const createRecord = useCallback(async (entityId: string, recordData: Record<string, any>) => {
    const data = await apiCall(`/api/v-core/entities/${entityId}/records`, {
      method: 'POST',
      body: JSON.stringify({ data: recordData }),
    });
    setRecords(prev => [...prev, data.record]);
    return data.record;
  }, [apiCall]);

  const updateRecord = useCallback(async (recordId: string, recordData: Record<string, any>) => {
    const data = await apiCall(`/api/v-core/records/${recordId}`, {
      method: 'PATCH',
      body: JSON.stringify(recordData),
    });
    setRecords(prev => prev.map(r => r.id === recordId ? data.record : r));
    return data.record;
  }, [apiCall]);

  const deleteRecord = useCallback(async (recordId: string) => {
    await apiCall(`/api/v-core/records/${recordId}`, { method: 'DELETE' });
    setRecords(prev => prev.filter(r => r.id !== recordId));
  }, [apiCall]);

  // ==========================================
  // Workflow Engine
  // ==========================================

  const loadWorkflows = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiCall('/api/v-core/workflows');
      setWorkflows(data.workflows);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const loadExecutions = useCallback(async (workflowId: string) => {
    const data = await apiCall(`/api/v-core/workflows/${workflowId}/executions`);
    setExecutions(data.executions);
  }, [apiCall]);

  const createWorkflow = useCallback(async (name: string, description: string = '') => {
    const data = await apiCall('/api/v-core/workflows', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    });
    setWorkflows(prev => [...prev, data.workflow]);
    return data.workflow;
  }, [apiCall]);

  const activateWorkflow = useCallback(async (workflowId: string) => {
    const data = await apiCall(`/api/v-core/workflows/${workflowId}/activate`, { method: 'POST' });
    setWorkflows(prev => prev.map(w => w.id === workflowId ? data.workflow : w));
  }, [apiCall]);

  const pauseWorkflow = useCallback(async (workflowId: string) => {
    const data = await apiCall(`/api/v-core/workflows/${workflowId}/pause`, { method: 'POST' });
    setWorkflows(prev => prev.map(w => w.id === workflowId ? data.workflow : w));
  }, [apiCall]);

  const executeWorkflow = useCallback(async (workflowId: string, triggerData: Record<string, any> = {}) => {
    const data = await apiCall(`/api/v-core/workflows/${workflowId}/execute`, {
      method: 'POST',
      body: JSON.stringify(triggerData),
    });
    return data.execution;
  }, [apiCall]);

  // ==========================================
  // Mission Control
  // ==========================================

  const loadDashboard = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await apiCall('/api/v-core/dashboard');
      setDashboard(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  }, [apiCall]);

  const loadActivities = useCallback(async () => {
    const data = await apiCall('/api/v-core/activities');
    setActivities(data.activities);
  }, [apiCall]);

  const loadApprovals = useCallback(async () => {
    const data = await apiCall('/api/v-core/approvals');
    setApprovals(data.approvals);
  }, [apiCall]);

  const loadAlerts = useCallback(async () => {
    const data = await apiCall('/api/v-core/alerts');
    setAlerts(data.alerts);
  }, [apiCall]);

  const loadMetrics = useCallback(async () => {
    const data = await apiCall('/api/v-core/metrics');
    setMetrics(data.metrics);
  }, [apiCall]);

  const approveRequest = useCallback(async (approvalId: string, note: string = '') => {
    await apiCall(`/api/v-core/approvals/${approvalId}/approve?note=${encodeURIComponent(note)}`, {
      method: 'POST',
    });
    setApprovals(prev => prev.map(a => 
      a.id === approvalId ? { ...a, status: 'approved' as const } : a
    ));
  }, [apiCall]);

  const rejectRequest = useCallback(async (approvalId: string, note: string = '') => {
    await apiCall(`/api/v-core/approvals/${approvalId}/reject?note=${encodeURIComponent(note)}`, {
      method: 'POST',
    });
    setApprovals(prev => prev.map(a => 
      a.id === approvalId ? { ...a, status: 'rejected' as const } : a
    ));
  }, [apiCall]);

  const resolveAlert = useCallback(async (alertId: string) => {
    await apiCall(`/api/v-core/alerts/${alertId}/resolve`, { method: 'POST' });
    setAlerts(prev => prev.map(a => 
      a.id === alertId ? { ...a, isResolved: true } : a
    ));
  }, [apiCall]);

  // Load roles on mount
  useEffect(() => {
    apiCall('/api/v-core/roles').then(data => setRoles(data.roles)).catch(() => {});
  }, [apiCall]);

  return {
    isLoading,
    error,
    
    // Control Plane
    organizations,
    currentOrg,
    members,
    roles,
    loadOrganizations,
    loadMembers,
    createOrganization,
    inviteMember,
    
    // Business Core
    entities,
    records,
    loadEntities,
    loadRecords,
    createEntity,
    createRecord,
    updateRecord,
    deleteRecord,
    
    // Workflow Engine
    workflows,
    executions,
    loadWorkflows,
    loadExecutions,
    createWorkflow,
    activateWorkflow,
    pauseWorkflow,
    executeWorkflow,
    
    // Mission Control
    dashboard,
    activities,
    approvals,
    alerts,
    metrics,
    loadDashboard,
    loadActivities,
    loadApprovals,
    loadAlerts,
    loadMetrics,
    approveRequest,
    rejectRequest,
    resolveAlert,
  };
}

export default useVCore;
