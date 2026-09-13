/**
 * V Core Components
 * 
 * Complete UI components for V Core modules
 */

// Control Plane & Business Core
export {
  OrganizationCard,
  MemberList,
  InviteModal,
  EntityCard,
  RecordTable,
  RecordForm,
} from './VCoreComponents';

// Workflow Engine & Mission Control
export {
  WorkflowCard,
  ExecutionList,
  MetricCard,
  ActivityFeed,
  ApprovalCard,
  AlertItem,
  MissionControlDashboard,
} from './VCoreComponents2';

// Re-export types
export type {
  User,
  Organization,
  Role,
  Member,
  EntityDefinition,
  FieldDefinition,
  VCoreRecord,
  VCoreRecord as Record,
  Workflow,
  WorkflowNode,
  WorkflowExecution,
  Activity,
  ApprovalRequest,
  Alert,
  Metric,
  DashboardData,
} from '../../hooks/useVCore';
