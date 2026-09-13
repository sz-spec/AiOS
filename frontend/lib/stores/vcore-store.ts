import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { persist } from 'zustand/middleware';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useVCore.ts)
// ---------------------------------------------------------------------------

export interface Organization {
  id: string;
  name: string;
  plan: string;
  member_count: number;
  created_at: string;
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  role_id: string;
  role_name: string;
  joined_at: string;
}

export interface Role {
  id: string;
  name: string;
  permissions: string[];
}

export interface FieldDefinition {
  name: string;
  type: string;
  required?: boolean;
  default_value?: unknown;
}

export interface EntityDefinition {
  id: string;
  name: string;
  label: string;
  fields: FieldDefinition[];
  record_count: number;
  created_at: string;
}

export interface VCoreRecord {
  id: string;
  entity_id: string;
  data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  status: 'active' | 'paused' | 'draft';
  trigger_count: number;
  created_at: string;
}

export interface WorkflowExecution {
  id: string;
  workflow_id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  completed_at?: string;
  result?: unknown;
}

export interface DashboardData {
  total_entities: number;
  total_records: number;
  total_workflows: number;
  active_workflows: number;
  recent_activity: number;
}

export interface Activity {
  id: string;
  type: string;
  description: string;
  timestamp: string;
  user_id?: string;
}

export interface ApprovalRequest {
  id: string;
  type: string;
  description: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
}

export interface Alert {
  id: string;
  severity: 'info' | 'warning' | 'error' | 'critical';
  message: string;
  resolved: boolean;
  created_at: string;
}

export interface Metric {
  id: string;
  name: string;
  value: number;
  unit: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

interface VCoreState {
  // Loading / Error
  isLoading: boolean;
  error: string | null;

  // Control Plane
  organizations: Organization[];
  currentOrg: Organization | null;
  members: Member[];
  roles: Role[];

  // Business Core
  entities: EntityDefinition[];
  records: VCoreRecord[];

  // Workflow Engine
  workflows: Workflow[];
  executions: WorkflowExecution[];

  // Mission Control
  dashboard: DashboardData | null;
  activities: Activity[];
  approvals: ApprovalRequest[];
  alerts: Alert[];
  metrics: Metric[];

  // Actions — Control Plane
  loadOrganizations: (getToken: () => Promise<string | null>) => Promise<void>;
  loadMembers: (orgId: string, getToken: () => Promise<string | null>) => Promise<void>;
  createOrganization: (name: string, getToken: () => Promise<string | null>, plan?: string) => Promise<Organization>;
  inviteMember: (orgId: string, email: string, roleId: string, getToken: () => Promise<string | null>) => Promise<void>;

  // Actions — Business Core
  loadEntities: (getToken: () => Promise<string | null>) => Promise<void>;
  loadRecords: (entityId: string, getToken: () => Promise<string | null>) => Promise<void>;
  createEntity: (name: string, label: string, getToken: () => Promise<string | null>, fields?: FieldDefinition[]) => Promise<EntityDefinition>;
  createRecord: (entityId: string, data: Record<string, unknown>, getToken: () => Promise<string | null>) => Promise<VCoreRecord>;
  updateRecord: (recordId: string, data: Record<string, unknown>, getToken: () => Promise<string | null>) => Promise<VCoreRecord>;
  deleteRecord: (recordId: string, getToken: () => Promise<string | null>) => Promise<void>;

  // Actions — Workflow Engine
  loadWorkflows: (getToken: () => Promise<string | null>) => Promise<void>;
  createWorkflow: (name: string, getToken: () => Promise<string | null>, description?: string) => Promise<Workflow>;
  executeWorkflow: (workflowId: string, getToken: () => Promise<string | null>, triggerData?: Record<string, unknown>) => Promise<WorkflowExecution>;

  // Actions — Mission Control
  loadDashboard: (getToken: () => Promise<string | null>) => Promise<void>;
  loadActivities: (getToken: () => Promise<string | null>) => Promise<void>;
  approveRequest: (approvalId: string, getToken: () => Promise<string | null>, note?: string) => Promise<void>;
  rejectRequest: (approvalId: string, getToken: () => Promise<string | null>, note?: string) => Promise<void>;
  resolveAlert: (alertId: string, getToken: () => Promise<string | null>) => Promise<void>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const apiCall = async (
  endpoint: string,
  getToken: () => Promise<string | null>,
  options?: RequestInit,
) => {
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const response = await fetch(endpoint, {
    ...options,
    headers: { ...headers, ...((options?.headers as Record<string, string>) || {}) },
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || 'Request failed');
  }

  return response.json();
};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useVCoreStore = create<VCoreState>()(
  persist(
    immer((set) => ({
      isLoading: false,
      error: null,

      organizations: [],
      currentOrg: null,
      members: [],
      roles: [],

      entities: [],
      records: [],

      workflows: [],
      executions: [],

      dashboard: null,
      activities: [],
      approvals: [],
      alerts: [],
      metrics: [],

      // --- Control Plane ---

      loadOrganizations: async (getToken) => {
        set((s) => { s.isLoading = true; s.error = null; });
        try {
          const data = await apiCall('/api/v-core/organizations', getToken);
          set((s) => {
            s.organizations = data.organizations || data;
            if (s.organizations.length > 0 && !s.currentOrg) {
              s.currentOrg = s.organizations[0];
            }
          });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        } finally {
          set((s) => { s.isLoading = false; });
        }
      },

      loadMembers: async (orgId, getToken) => {
        try {
          const data = await apiCall(`/api/v-core/organizations/${orgId}/members`, getToken);
          set((s) => { s.members = data.members || data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        }
      },

      createOrganization: async (name, getToken, plan = 'free') => {
        const data = await apiCall('/api/v-core/organizations', getToken, {
          method: 'POST',
          body: JSON.stringify({ name, plan }),
        });
        const org = data.organization || data;
        set((s) => { s.organizations.push(org); });
        return org;
      },

      inviteMember: async (orgId, email, roleId, getToken) => {
        await apiCall(`/api/v-core/organizations/${orgId}/members`, getToken, {
          method: 'POST',
          body: JSON.stringify({ email, role_id: roleId }),
        });
      },

      // --- Business Core ---

      loadEntities: async (getToken) => {
        set((s) => { s.isLoading = true; });
        try {
          const data = await apiCall('/api/v-core/entities', getToken);
          set((s) => { s.entities = data.entities || data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        } finally {
          set((s) => { s.isLoading = false; });
        }
      },

      loadRecords: async (entityId, getToken) => {
        set((s) => { s.isLoading = true; });
        try {
          const data = await apiCall(`/api/v-core/entities/${entityId}/records`, getToken);
          set((s) => { s.records = data.records || data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        } finally {
          set((s) => { s.isLoading = false; });
        }
      },

      createEntity: async (name, label, getToken, fields = []) => {
        const data = await apiCall('/api/v-core/entities', getToken, {
          method: 'POST',
          body: JSON.stringify({ name, label, fields }),
        });
        const entity = data.entity || data;
        set((s) => { s.entities.push(entity); });
        return entity;
      },

      createRecord: async (entityId, recordData, getToken) => {
        const data = await apiCall(`/api/v-core/entities/${entityId}/records`, getToken, {
          method: 'POST',
          body: JSON.stringify({ data: recordData }),
        });
        const record = data.record || data;
        set((s) => { s.records.push(record); });
        return record;
      },

      updateRecord: async (recordId, recordData, getToken) => {
        const data = await apiCall(`/api/v-core/records/${recordId}`, getToken, {
          method: 'PUT',
          body: JSON.stringify({ data: recordData }),
        });
        const updated = data.record || data;
        set((s) => {
          const idx = s.records.findIndex((r) => r.id === recordId);
          if (idx >= 0) s.records[idx] = updated;
        });
        return updated;
      },

      deleteRecord: async (recordId, getToken) => {
        await apiCall(`/api/v-core/records/${recordId}`, getToken, { method: 'DELETE' });
        set((s) => { s.records = s.records.filter((r) => r.id !== recordId); });
      },

      // --- Workflow Engine ---

      loadWorkflows: async (getToken) => {
        set((s) => { s.isLoading = true; });
        try {
          const data = await apiCall('/api/v-core/workflows', getToken);
          set((s) => { s.workflows = data.workflows || data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        } finally {
          set((s) => { s.isLoading = false; });
        }
      },

      createWorkflow: async (name, getToken, description = '') => {
        const data = await apiCall('/api/v-core/workflows', getToken, {
          method: 'POST',
          body: JSON.stringify({ name, description }),
        });
        const wf = data.workflow || data;
        set((s) => { s.workflows.push(wf); });
        return wf;
      },

      executeWorkflow: async (workflowId, getToken, triggerData = {}) => {
        const data = await apiCall(`/api/v-core/workflows/${workflowId}/execute`, getToken, {
          method: 'POST',
          body: JSON.stringify({ trigger_data: triggerData }),
        });
        const exec = data.execution || data;
        set((s) => { s.executions.push(exec); });
        return exec;
      },

      // --- Mission Control ---

      loadDashboard: async (getToken) => {
        try {
          const data = await apiCall('/api/v-core/dashboard', getToken);
          set((s) => { s.dashboard = data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        }
      },

      loadActivities: async (getToken) => {
        try {
          const data = await apiCall('/api/v-core/activities', getToken);
          set((s) => { s.activities = data.activities || data; });
        } catch (err) {
          set((s) => { s.error = err instanceof Error ? err.message : 'Failed'; });
        }
      },

      approveRequest: async (approvalId, getToken, note = '') => {
        await apiCall(`/api/v-core/approvals/${approvalId}/approve`, getToken, {
          method: 'POST',
          body: JSON.stringify({ note }),
        });
        set((s) => {
          const a = s.approvals.find((r) => r.id === approvalId);
          if (a) a.status = 'approved';
        });
      },

      rejectRequest: async (approvalId, getToken, note = '') => {
        await apiCall(`/api/v-core/approvals/${approvalId}/reject`, getToken, {
          method: 'POST',
          body: JSON.stringify({ note }),
        });
        set((s) => {
          const a = s.approvals.find((r) => r.id === approvalId);
          if (a) a.status = 'rejected';
        });
      },

      resolveAlert: async (alertId, getToken) => {
        await apiCall(`/api/v-core/alerts/${alertId}/resolve`, getToken, { method: 'POST' });
        set((s) => {
          const a = s.alerts.find((r) => r.id === alertId);
          if (a) a.resolved = true;
        });
      },
    })),
    {
      name: 'vos3-vcore-store',
      partialize: (state) => ({
        entities: state.entities,
        currentOrg: state.currentOrg,
      }),
    },
  ),
);
