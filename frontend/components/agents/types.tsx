import { AgentCategory } from './templates';

// Agent type matching useAgents hook
export interface Agent {
  id: string;
  name: string;
  role: string;
  model: string;
  model_category?: string | null;
  category?: string | null;
  system_prompt: string | null;
  services: string[];
  temperature: number;
  status: 'idle' | 'running' | 'paused' | 'error';
  created_at: string;
  last_run: string | null;
  run_count: number;
  template_id?: string | null;
  description?: string | null;
  voice_id?: string | null;
  voice_settings?: {
    stability: number;
    similarity_boost: number;
    style: number;
    speaker_boost: boolean;
  } | null;
}

export interface AgentLog {
  id: string;
  agent_id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  tokens?: number;
  cost?: number;
  duration_ms?: number;
}

// Chat message interface for agent conversations
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  model?: string;
  provider?: string;
  configProposals?: ConfigProposal[];
  instructionProposals?: InstructionProposal[];
}

// Instruction change proposal extracted from AI response
export interface InstructionProposal {
  id: string;
  action: 'add' | 'remove';
  text: string;
  status: 'pending' | 'applied' | 'rejected';
}

// Config change proposal extracted from AI response
export interface ConfigProposal {
  id: string;
  field: 'name' | 'role' | 'category' | 'model' | 'model_category' | 'temperature' | 'system_prompt' | 'services' | 'voice_id';
  value: any;
  oldValue: any;
  label: string;
  status: 'pending' | 'applied' | 'rejected';
}

// Version snapshot for config history
export interface ConfigVersion {
  id: string;
  version: number;
  timestamp: Date;
  changes: string[];
  snapshot: {
    name: string;
    role: string;
    category?: string | null;
    model: string;
    model_category?: string | null;
    temperature: number;
    system_prompt: string | null;
    services: string[];
  };
  source: 'initial' | 'chat';
}

export type MainView = 'detail' | 'template-chat' | 'templates' | 'create' | 'edit' | 'empty' | 'collaborate';

/** Detect text direction based on first meaningful character */
export function detectDir(text: string): 'rtl' | 'ltr' {
  const rtlRange = /[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0780-\u07BF\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
  const stripped = text.replace(/^[\s\d\W]+/, '');
  return rtlRange.test(stripped.charAt(0)) ? 'rtl' : 'ltr';
}

/** SmartRouter role to recommended engine mapping */
export const ROUTER_ENGINE: Record<string, { engine: string; provider: string }> = {
  architect: { engine: 'GPT-4o', provider: 'OpenAI' },
  reviewer: { engine: 'Claude Opus', provider: 'Anthropic' },
  researcher: { engine: 'Gemini Pro', provider: 'Google' },
  coding: { engine: 'Claude Sonnet', provider: 'Anthropic' },
};

// RACI role mapping based on agent role
export const RACI_MAP: Record<string, { letter: string; label: string; description: string }> = {
  architect: { letter: 'A', label: 'Accountable', description: 'Owns decisions and outcomes' },
  developer: { letter: 'R', label: 'Responsible', description: 'Does the work' },
  frontend: { letter: 'R', label: 'Responsible', description: 'Does the work' },
  backend: { letter: 'R', label: 'Responsible', description: 'Does the work' },
  reviewer: { letter: 'C', label: 'Consulted', description: 'Provides expert input' },
  tester: { letter: 'C', label: 'Consulted', description: 'Validates quality' },
  analyst: { letter: 'C', label: 'Consulted', description: 'Provides analysis' },
  researcher: { letter: 'I', label: 'Informed', description: 'Kept in the loop' },
  writer: { letter: 'R', label: 'Responsible', description: 'Does the work' },
  assistant: { letter: 'R', label: 'Responsible', description: 'Does the work' },
  custom: { letter: 'R', label: 'Responsible', description: 'Does the work' },
};

export const RACI_COLORS: Record<string, string> = {
  R: '#10B981',
  A: '#F59E0B',
  C: '#6366F1',
  I: '#8B5CF6',
};

// Role info for display in sidebar and detail header
export const ROLE_INFO: Record<string, { icon: string; label: string; category: AgentCategory }> = {
  assistant: { icon: '\ud83e\udd16', label: 'Assistant', category: 'leadership' },
  architect: { icon: '\ud83c\udfd7\ufe0f', label: 'Architect', category: 'design' },
  analyst: { icon: '\ud83d\udcca', label: 'Analyst', category: 'design' },
  developer: { icon: '\ud83d\udcbb', label: 'Developer', category: 'development' },
  frontend: { icon: '\u269b\ufe0f', label: 'Frontend', category: 'development' },
  backend: { icon: '\ud83d\udd27', label: 'Backend', category: 'development' },
  tester: { icon: '\ud83d\udd0d', label: 'Tester', category: 'quality' },
  reviewer: { icon: '\ud83d\udc40', label: 'Reviewer', category: 'quality' },
  researcher: { icon: '\ud83d\udd2c', label: 'Researcher', category: 'design' },
  writer: { icon: '\u270d\ufe0f', label: 'Writer', category: 'creative' },
  custom: { icon: '\u26a1', label: 'Custom', category: 'operations' },
};

// Get color for an agent: from its template, or from its category/role
export function getAgentColor(agent: { role: string; template_id?: string | null; category?: string | null }, templates: any[], categories: Record<string, { color: string }>): string {
  if (agent.template_id) {
    const tmpl = templates.find((t: any) => t.id === agent.template_id);
    if (tmpl) return tmpl.color;
  }
  if (agent.category && categories[agent.category as AgentCategory]) {
    return categories[agent.category as AgentCategory].color;
  }
  const info = ROLE_INFO[agent.role] || ROLE_INFO.custom;
  return categories[info.category].color;
}

// Shared icon button style
export const iconBtnStyle: React.CSSProperties = {
  padding: '4px',
  backgroundColor: 'transparent',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-tertiary)',
  cursor: 'pointer',
};

// Relative time helper
export function getTimeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 4) return `${weeks}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

// SVG Icon components
export function EditIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

export function DeleteIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

export function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}
