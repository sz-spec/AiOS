/**
 * Bridge hook for gradual migration from useVCore (React hooks + fetch)
 * to the Zustand vcore store.
 *
 * Usage: Replace `useVCore()` with `useVCoreStoreBridge()` in components.
 * The returned API shape matches the original useVCore hook.
 */
import { useEffect, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  useVCoreStore,
  type Organization,
  type EntityDefinition,
  type VCoreRecord,
  type Workflow,
  type WorkflowExecution,
  type FieldDefinition,
} from '@/lib/stores/vcore-store';

export function useVCoreStoreBridge() {
  const { getToken } = useAuth();

  // State selectors
  const isLoading = useVCoreStore((s) => s.isLoading);
  const error = useVCoreStore((s) => s.error);
  const organizations = useVCoreStore((s) => s.organizations);
  const currentOrg = useVCoreStore((s) => s.currentOrg);
  const members = useVCoreStore((s) => s.members);
  const roles = useVCoreStore((s) => s.roles);
  const entities = useVCoreStore((s) => s.entities);
  const records = useVCoreStore((s) => s.records);
  const workflows = useVCoreStore((s) => s.workflows);
  const executions = useVCoreStore((s) => s.executions);
  const dashboard = useVCoreStore((s) => s.dashboard);
  const activities = useVCoreStore((s) => s.activities);
  const approvals = useVCoreStore((s) => s.approvals);
  const alerts = useVCoreStore((s) => s.alerts);
  const metrics = useVCoreStore((s) => s.metrics);

  // Action selectors
  const storeLoadOrganizations = useVCoreStore((s) => s.loadOrganizations);
  const storeLoadMembers = useVCoreStore((s) => s.loadMembers);
  const storeCreateOrganization = useVCoreStore((s) => s.createOrganization);
  const storeInviteMember = useVCoreStore((s) => s.inviteMember);
  const storeLoadEntities = useVCoreStore((s) => s.loadEntities);
  const storeLoadRecords = useVCoreStore((s) => s.loadRecords);
  const storeCreateEntity = useVCoreStore((s) => s.createEntity);
  const storeCreateRecord = useVCoreStore((s) => s.createRecord);
  const storeUpdateRecord = useVCoreStore((s) => s.updateRecord);
  const storeDeleteRecord = useVCoreStore((s) => s.deleteRecord);
  const storeLoadWorkflows = useVCoreStore((s) => s.loadWorkflows);
  const storeCreateWorkflow = useVCoreStore((s) => s.createWorkflow);
  const storeExecuteWorkflow = useVCoreStore((s) => s.executeWorkflow);
  const storeLoadDashboard = useVCoreStore((s) => s.loadDashboard);
  const storeLoadActivities = useVCoreStore((s) => s.loadActivities);
  const storeApproveRequest = useVCoreStore((s) => s.approveRequest);
  const storeRejectRequest = useVCoreStore((s) => s.rejectRequest);
  const storeResolveAlert = useVCoreStore((s) => s.resolveAlert);

  // Auto-load roles on mount
  useEffect(() => {
    storeLoadEntities(getToken);
  }, [storeLoadEntities, getToken]);

  // Wrapped actions — Control Plane
  const loadOrganizations = useCallback(
    () => storeLoadOrganizations(getToken),
    [storeLoadOrganizations, getToken],
  );
  const loadMembers = useCallback(
    (orgId: string) => storeLoadMembers(orgId, getToken),
    [storeLoadMembers, getToken],
  );
  const createOrganization = useCallback(
    (name: string, plan?: string) => storeCreateOrganization(name, getToken, plan),
    [storeCreateOrganization, getToken],
  );
  const inviteMember = useCallback(
    (orgId: string, email: string, roleId: string) =>
      storeInviteMember(orgId, email, roleId, getToken),
    [storeInviteMember, getToken],
  );

  // Wrapped actions — Business Core
  const loadEntities = useCallback(
    () => storeLoadEntities(getToken),
    [storeLoadEntities, getToken],
  );
  const loadRecords = useCallback(
    (entityId: string) => storeLoadRecords(entityId, getToken),
    [storeLoadRecords, getToken],
  );
  const createEntity = useCallback(
    (name: string, label: string, fields?: FieldDefinition[]) =>
      storeCreateEntity(name, label, getToken, fields),
    [storeCreateEntity, getToken],
  );
  const createRecord = useCallback(
    (entityId: string, data: Record<string, unknown>) =>
      storeCreateRecord(entityId, data, getToken),
    [storeCreateRecord, getToken],
  );
  const updateRecord = useCallback(
    (recordId: string, data: Record<string, unknown>) =>
      storeUpdateRecord(recordId, data, getToken),
    [storeUpdateRecord, getToken],
  );
  const deleteRecord = useCallback(
    (recordId: string) => storeDeleteRecord(recordId, getToken),
    [storeDeleteRecord, getToken],
  );

  // Wrapped actions — Workflow Engine
  const loadWorkflows = useCallback(
    () => storeLoadWorkflows(getToken),
    [storeLoadWorkflows, getToken],
  );
  const createWorkflow = useCallback(
    (name: string, description?: string) =>
      storeCreateWorkflow(name, getToken, description),
    [storeCreateWorkflow, getToken],
  );
  const executeWorkflow = useCallback(
    (workflowId: string, triggerData?: Record<string, unknown>) =>
      storeExecuteWorkflow(workflowId, getToken, triggerData),
    [storeExecuteWorkflow, getToken],
  );

  // Wrapped actions — Mission Control
  const loadDashboard = useCallback(
    () => storeLoadDashboard(getToken),
    [storeLoadDashboard, getToken],
  );
  const loadActivities = useCallback(
    () => storeLoadActivities(getToken),
    [storeLoadActivities, getToken],
  );
  const approveRequest = useCallback(
    (approvalId: string, note?: string) =>
      storeApproveRequest(approvalId, getToken, note),
    [storeApproveRequest, getToken],
  );
  const rejectRequest = useCallback(
    (approvalId: string, note?: string) =>
      storeRejectRequest(approvalId, getToken, note),
    [storeRejectRequest, getToken],
  );
  const resolveAlert = useCallback(
    (alertId: string) => storeResolveAlert(alertId, getToken),
    [storeResolveAlert, getToken],
  );

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
    createWorkflow,
    executeWorkflow,
    // Mission Control
    dashboard,
    activities,
    approvals,
    alerts,
    metrics,
    loadDashboard,
    loadActivities,
    approveRequest,
    rejectRequest,
    resolveAlert,
  };
}

export type {
  Organization,
  EntityDefinition,
  VCoreRecord,
  Workflow,
  WorkflowExecution,
  FieldDefinition,
};
