import { useState, useCallback, useEffect, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

// =============================================================================
// Types
// =============================================================================

export type BMADMode = 'simple' | 'guided' | 'expert' | 'party';

export type BMADPhase =
  | 'ideation'
  | 'discovery'
  | 'planning'
  | 'design'
  | 'development'
  | 'testing'
  | 'review'
  | 'deployment'
  | 'operations';

export type PhaseStatus =
  | 'pending'
  | 'in_progress'
  | 'awaiting_approval'
  | 'approved'
  | 'rejected'
  | 'completed'
  | 'skipped';

export interface BMADAgent {
  role: string;
  name: string;
  description: string;
  phases: BMADPhase[];
  artifacts: string[];
  avatar: string;
  color: string;
}

export interface Artifact {
  id: string;
  type: string;
  name: string;
  content: any;
  phase: BMADPhase;
  agent: string;
  created_at: string;
}

export interface Approval {
  id: string;
  phase: BMADPhase;
  agent: string;
  description: string;
  artifact_ids: string[];
  status: PhaseStatus;
  created_at: string;
  resolved_at?: string;
  feedback?: string;
}

export interface PhaseState {
  phase: BMADPhase;
  status: PhaseStatus;
  started_at?: string;
  completed_at?: string;
  artifacts: string[];
  approvals: string[];
  git_commit?: string;
}

export interface GitState {
  enabled: boolean;
  repo_url?: string;
  branch: string;
  auto_commit: boolean;
  commits: Array<{
    sha: string;
    message: string;
    phase: BMADPhase;
    timestamp: string;
  }>;
}

export interface DeploymentState {
  provider: string;
  project_id?: string;
  deployment_url?: string;
  status: string;
  last_deployment?: string;
}

export interface BMADSession {
  id: string;
  project_name: string;
  description: string;
  mode: BMADMode;
  current_phase: BMADPhase;
  phases: Record<BMADPhase, PhaseState>;
  artifacts_count: number;
  pending_approvals: number;
  git_enabled: boolean;
  is_active: boolean;
  deployment_url?: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowEvent {
  type: string;
  session_id: string;
  phase?: BMADPhase;
  agent?: string;
  data: Record<string, any>;
  timestamp: string;
}

// =============================================================================
// Constants
// =============================================================================

const PHASE_ORDER: BMADPhase[] = [
  'ideation',
  'discovery',
  'planning',
  'design',
  'development',
  'testing',
  'review',
  'deployment',
  'operations',
];

const PHASE_INFO: Record<BMADPhase, { label: string; description: string }> = {
  ideation: { label: 'Ideation', description: 'Define vision and project scope' },
  discovery: { label: 'Discovery', description: 'Gather requirements and research' },
  planning: { label: 'Planning', description: 'Architecture and sprint planning' },
  design: { label: 'Design', description: 'Detailed specifications' },
  development: { label: 'Development', description: 'Code implementation' },
  testing: { label: 'Testing', description: 'Quality assurance' },
  review: { label: 'Review', description: 'Code review and approval' },
  deployment: { label: 'Deployment', description: 'Release to production' },
  operations: { label: 'Operations', description: 'Monitoring and maintenance' },
};

// =============================================================================
// Hook
// =============================================================================

export function useBMAD() {
  // State
  const [sessions, setSessions] = useState<BMADSession[]>([]);
  const [currentSession, setCurrentSession] = useState<BMADSession | null>(null);
  const [agents, setAgents] = useState<BMADAgent[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getToken } = useAuth();

  // WebSocket ref
  const wsRef = useRef<WebSocket | null>(null);

  // -------------------------------------------------------------------------
  // Session Management
  // -------------------------------------------------------------------------

  const fetchSessions = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions`);
      if (!response.ok) throw new Error('Failed to fetch sessions');
      const data = await response.json();
      setSessions(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch sessions');
    }
  }, [getToken]);

  const createSession = useCallback(async (
    projectName: string,
    description: string = '',
    mode: BMADMode = 'guided',
    gitRepo?: string
  ): Promise<BMADSession | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions`, {
        method: 'POST',
        body: JSON.stringify({
          project_name: projectName,
          description,
          mode,
          git_repo: gitRepo,
        }),
      });
      if (!response.ok) throw new Error('Failed to create session');
      const session = await response.json();
      setCurrentSession(session);
      setSessions(prev => [session, ...prev]);
      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create session');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  // -------------------------------------------------------------------------
  // Artifacts
  // -------------------------------------------------------------------------

  const loadArtifacts = useCallback(async (sessionId: string) => {
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/artifacts`);
      if (!response.ok) throw new Error('Failed to load artifacts');
      const data = await response.json();
      setArtifacts(data.artifacts);
    } catch (err) {
      console.error('Failed to load artifacts:', err);
    }
  }, [getToken]);

  // -------------------------------------------------------------------------
  // Approvals
  // -------------------------------------------------------------------------

  const loadApprovals = useCallback(async (sessionId: string) => {
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/approvals`);
      if (!response.ok) throw new Error('Failed to load approvals');
      const data = await response.json();
      setApprovals(data.approvals);
    } catch (err) {
      console.error('Failed to load approvals:', err);
    }
  }, [getToken]);

  const loadSession = useCallback(async (sessionId: string): Promise<BMADSession | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}`);
      if (!response.ok) throw new Error('Failed to load session');
      const session = await response.json();
      setCurrentSession(session);

      // Load artifacts and approvals
      await Promise.all([
        loadArtifacts(sessionId),
        loadApprovals(sessionId),
      ]);

      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load session');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken, loadArtifacts, loadApprovals]);

  const deleteSession = useCallback(async (sessionId: string): Promise<boolean> => {
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error('Failed to delete session');
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      if (currentSession?.id === sessionId) {
        setCurrentSession(null);
      }
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete session');
      return false;
    }
  }, [currentSession, getToken]);

  const updateSession = useCallback(async (
    sessionId: string,
    updates: { project_name?: string; description?: string }
  ): Promise<BMADSession | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          project_name: updates.project_name,
          description: updates.description,
        }),
      });
      if (!response.ok) throw new Error('Failed to update session');
      const session = await response.json();
      setSessions(prev => prev.map(s => s.id === sessionId ? session : s));
      if (currentSession?.id === sessionId) {
        setCurrentSession(session);
      }
      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update session');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [currentSession, getToken]);

  const cloneSession = useCallback(async (
    sessionId: string,
    projectName: string,
    description?: string
  ): Promise<BMADSession | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/clone`, {
        method: 'POST',
        body: JSON.stringify({
          project_name: projectName,
          description,
        }),
      });
      if (!response.ok) throw new Error('Failed to clone session');
      const session = await response.json();
      setSessions(prev => [session, ...prev]);
      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clone session');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  const toggleSessionActive = useCallback(async (
    sessionId: string
  ): Promise<BMADSession | null> => {
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/toggle-active`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Failed to toggle session');
      const session = await response.json();
      setSessions(prev => prev.map(s => s.id === sessionId ? session : s));
      if (currentSession?.id === sessionId) {
        setCurrentSession(session);
      }
      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle session');
      return null;
    }
  }, [currentSession, getToken]);

  // -------------------------------------------------------------------------
  // Workflow Control
  // -------------------------------------------------------------------------

  const startWorkflow = useCallback(async (
    sessionId: string,
    userInput: string
  ): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/start`, {
        method: 'POST',
        body: JSON.stringify({ user_input: userInput }),
      });
      if (!response.ok) throw new Error('Failed to start workflow');

      // Reload session to get updated state
      await loadSession(sessionId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start workflow');
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  const advancePhase = useCallback(async (
    sessionId: string,
    toPhase?: BMADPhase
  ): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/phase/advance`, {
        method: 'POST',
        body: JSON.stringify({ to_phase: toPhase }),
      });
      if (!response.ok) throw new Error('Failed to advance phase');

      await loadSession(sessionId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to advance phase');
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  // -------------------------------------------------------------------------
  // WebSocket Streaming
  // -------------------------------------------------------------------------

  const connectWebSocket = useCallback((sessionId: string, userInput: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${window.location.host}/api/bmad/sessions/${sessionId}/stream`);
    wsRef.current = ws;
    setIsStreaming(true);
    setEvents([]);

    ws.onopen = () => {
      ws.send(JSON.stringify({ user_input: userInput }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'done') {
          setIsStreaming(false);
          loadSession(sessionId);
        } else if (data.type === 'error') {
          setError(data.error);
          setIsStreaming(false);
        } else {
          setEvents(prev => [...prev, data as WorkflowEvent]);
        }
      } catch (err) {
        console.error('WebSocket parse error:', err);
      }
    };

    ws.onerror = () => {
      setError('WebSocket connection error');
      setIsStreaming(false);
    };

    ws.onclose = () => {
      setIsStreaming(false);
      wsRef.current = null;
    };
  }, [loadSession]);

  const disconnectWebSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const resolveApproval = useCallback(async (
    sessionId: string,
    approvalId: string,
    approved: boolean,
    feedback?: string
  ): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(
        getToken,
        `/api/bmad/sessions/${sessionId}/approvals/${approvalId}/approve`,
        {
          method: 'POST',
          body: JSON.stringify({ approved, feedback }),
        }
      );
      if (!response.ok) throw new Error('Failed to resolve approval');

      await loadSession(sessionId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resolve approval');
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  // -------------------------------------------------------------------------
  // Agents
  // -------------------------------------------------------------------------

  const fetchAgents = useCallback(async () => {
    try {
      const response = await apiFetch(getToken, `/api/bmad/agents`);
      if (!response.ok) throw new Error('Failed to fetch agents');
      const data = await response.json();
      setAgents(data.agents);
    } catch (err) {
      console.error('Failed to fetch agents:', err);
    }
  }, [getToken]);

  const sendAgentMessage = useCallback(async (
    sessionId: string,
    agentRole: string,
    message: string
  ): Promise<string | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(
        getToken,
        `/api/bmad/sessions/${sessionId}/agents/${agentRole}/message`,
        {
          method: 'POST',
          body: JSON.stringify({ user_input: message }),
        }
      );
      if (!response.ok) throw new Error('Failed to send message');
      const data = await response.json();
      return data.response;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [getToken]);

  // -------------------------------------------------------------------------
  // Git Operations
  // -------------------------------------------------------------------------

  const createCommit = useCallback(async (
    sessionId: string,
    message: string,
    files?: string[]
  ): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/git/commit`, {
        method: 'POST',
        body: JSON.stringify({ message, files }),
      });
      if (!response.ok) throw new Error('Failed to create commit');

      await loadSession(sessionId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create commit');
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  // -------------------------------------------------------------------------
  // Deployment
  // -------------------------------------------------------------------------

  const deploy = useCallback(async (
    sessionId: string,
    envVars?: Record<string, string>
  ): Promise<{ success: boolean; url?: string }> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/deploy`, {
        method: 'POST',
        body: JSON.stringify({ env_vars: envVars }),
      });
      if (!response.ok) throw new Error('Failed to deploy');
      const data = await response.json();

      await loadSession(sessionId);
      return { success: data.success, url: data.url };
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to deploy');
      return { success: false };
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  const rollback = useCallback(async (
    sessionId: string,
    deploymentId?: string
  ): Promise<boolean> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/sessions/${sessionId}/rollback`, {
        method: 'POST',
        body: JSON.stringify({ deployment_id: deploymentId }),
      });
      if (!response.ok) throw new Error('Failed to rollback');

      await loadSession(sessionId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rollback');
      return false;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  // -------------------------------------------------------------------------
  // Quick Mode
  // -------------------------------------------------------------------------

  const quickMode = useCallback(async (
    description: string,
    projectName: string = 'Quick Fix'
  ): Promise<BMADSession | null> => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(getToken, `/api/bmad/quick`, {
        method: 'POST',
        body: JSON.stringify({ description, project_name: projectName }),
      });
      if (!response.ok) throw new Error('Failed to run quick mode');
      const data = await response.json();

      // Load the created session
      const session = await loadSession(data.session_id);
      return session;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run quick mode');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [loadSession, getToken]);

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const getPhaseInfo = useCallback((phase: BMADPhase) => {
    return PHASE_INFO[phase];
  }, []);

  const getPhaseIndex = useCallback((phase: BMADPhase) => {
    return PHASE_ORDER.indexOf(phase);
  }, []);

  const getNextPhase = useCallback((phase: BMADPhase): BMADPhase | null => {
    const index = PHASE_ORDER.indexOf(phase);
    if (index < PHASE_ORDER.length - 1) {
      return PHASE_ORDER[index + 1];
    }
    return null;
  }, []);

  const getPrevPhase = useCallback((phase: BMADPhase): BMADPhase | null => {
    const index = PHASE_ORDER.indexOf(phase);
    if (index > 0) {
      return PHASE_ORDER[index - 1];
    }
    return null;
  }, []);

  const getAgentsForPhase = useCallback((phase: BMADPhase): BMADAgent[] => {
    return agents.filter(agent => agent.phases.includes(phase));
  }, [agents]);

  const hasPendingApprovals = useCallback((): boolean => {
    return approvals.some(a => a.status === 'awaiting_approval');
  }, [approvals]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // -------------------------------------------------------------------------
  // Effects
  // -------------------------------------------------------------------------

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  useEffect(() => {
    return () => {
      disconnectWebSocket();
    };
  }, [disconnectWebSocket]);

  // -------------------------------------------------------------------------
  // Return
  // -------------------------------------------------------------------------

  return {
    // State
    sessions,
    currentSession,
    agents,
    artifacts,
    approvals,
    events,
    isLoading,
    isStreaming,
    error,

    // Session management
    fetchSessions,
    createSession,
    loadSession,
    deleteSession,
    updateSession,
    cloneSession,
    toggleSessionActive,

    // Workflow control
    startWorkflow,
    advancePhase,
    connectWebSocket,
    disconnectWebSocket,

    // Approvals
    resolveApproval,

    // Agents
    sendAgentMessage,

    // Git
    createCommit,

    // Deployment
    deploy,
    rollback,

    // Quick mode
    quickMode,

    // Helpers
    getPhaseInfo,
    getPhaseIndex,
    getNextPhase,
    getPrevPhase,
    getAgentsForPhase,
    hasPendingApprovals,
    clearError,

    // Constants
    PHASE_ORDER,
    PHASE_INFO,
  };
}

export default useBMAD;
