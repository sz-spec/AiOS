/**
 * Workflow node taxonomy. Each kind has a label, description, accent color,
 * and emoji icon. The icon is intentionally an emoji rather than a lucide
 * import — this matches the existing `/workflows` page aesthetic and keeps
 * the node component dependency-free.
 */

export type WorkflowNodeKind =
  | 'trigger.event'
  | 'trigger.schedule'
  | 'trigger.webhook'
  | 'action.http'
  | 'action.transform'
  | 'action.notify'
  | 'condition.branch';

export interface NodeKindMeta {
  label: string;
  description: string;
  accent: string;
  icon: string;
  category: 'Triggers' | 'Actions' | 'Logic';
}

export const NODE_KINDS: Record<WorkflowNodeKind, NodeKindMeta> = {
  'trigger.event': {
    label: 'Event Trigger',
    description: 'Run when an event fires',
    accent: '#22c55e',
    icon: '⚡',
    category: 'Triggers',
  },
  'trigger.schedule': {
    label: 'Schedule',
    description: 'Run on a cron schedule',
    accent: '#22c55e',
    icon: '🕐',
    category: 'Triggers',
  },
  'trigger.webhook': {
    label: 'Webhook',
    description: 'Run on incoming webhook',
    accent: '#22c55e',
    icon: '🔗',
    category: 'Triggers',
  },
  'action.http': {
    label: 'HTTP Request',
    description: 'Call an external API',
    accent: '#3b82f6',
    icon: '🌐',
    category: 'Actions',
  },
  'action.transform': {
    label: 'Transform',
    description: 'Reshape the payload',
    accent: '#3b82f6',
    icon: '⚙️',
    category: 'Actions',
  },
  'action.notify': {
    label: 'Notify',
    description: 'Send a message',
    accent: '#3b82f6',
    icon: '📨',
    category: 'Actions',
  },
  'condition.branch': {
    label: 'Branch',
    description: 'Split based on a condition',
    accent: '#a855f7',
    icon: '🔀',
    category: 'Logic',
  },
};

export interface WorkflowNodeData extends Record<string, unknown> {
  kind: WorkflowNodeKind;
  label: string;
}
