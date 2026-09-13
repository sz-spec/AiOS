'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { useAgents } from '@/hooks/useAgents';
import { SEED_LISTINGS } from '@/lib/marketplace/listings';
import type { MarketplaceListing } from '@/lib/marketplace/verticals';
import { useInstallCountsStore } from '@/lib/marketplace/installCountsStore';
import { AgentForm, AgentFormData } from '@/components/agents';
import { TemplateGallery } from '@/components/agents/TemplateGallery';
import { AgentTemplate, AGENT_TEMPLATES, AGENT_CATEGORIES } from '@/components/agents/templates';
import { CollabMessage, CollabDecision } from '@/components/agents/CollaborateChat';
import { CollaborateChat } from '@/components/agents/CollaborateChat';
import { AgentPicker } from '@/components/agents/AgentPicker';
import { AgentSidebar } from '@/components/agents/AgentSidebar';
import { AgentDetailView } from '@/components/agents/AgentDetailView';
import { TemplateChatView } from '@/components/agents/TemplateChatView';
import {
  ChatMessage,
  ConfigProposal,
  InstructionProposal,
  ConfigVersion,
  MainView,
  RACI_MAP,
  getAgentColor,
} from '@/components/agents/types';
import { useConfirm } from '@/hooks/useConfirm';

// Extract persona name from template system_prompt (pattern: "I'm [Name], your...")
function extractPersonaName(systemPrompt: string): string {
  const match = systemPrompt.match(/I'm\s+(\w+)/);
  return match ? match[1] : '';
}

export default function AgentsPage() {
  const { getToken } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { agents, createAgent, updateAgent, deleteAgent, incrementRunCount, loadLogs, logs, isLoading } = useAgents();
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [mainView, setMainView] = useState<MainView>('templates');
  const [showLogs, setShowLogs] = useState(false);
  // Vertical pre-selected by the marketplace install flow (?platform=<vertical>).
  // Used as the value for the new agent's platform_tag.
  const [installPlatform, setInstallPlatform] = useState<string | null>(null);
  // Seed listing pre-selected by the marketplace install flow (?seed=<id>).
  // Source for the create form when there's no static AGENT_TEMPLATE.
  const [installSeed, setInstallSeed] = useState<MarketplaceListing | null>(null);

  // Agent chat state
  const [agentMessages, setAgentMessages] = useState<Record<string, ChatMessage[]>>({});
  const [chatInput, setChatInput] = useState('');
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [playingMessageId, setPlayingMessageId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);

  // Collaborate state
  const [collaborateAgents, setCollaborateAgents] = useState<string[]>([]);
  const [collaborateMessages, setCollaborateMessages] = useState<CollabMessage[]>([]);
  const [showAgentPicker, setShowAgentPicker] = useState(false);
  const [isCollabLoading, setIsCollabLoading] = useState(false);
  const [respondingAgentId, setRespondingAgentId] = useState<string | null>(null);
  const [collabChatInput, setCollabChatInput] = useState('');
  const [collabDecisions, setCollabDecisions] = useState<CollabDecision[]>([]);

  // Config version history (per agent)
  const [configVersions, setConfigVersions] = useState<Record<string, ConfigVersion[]>>({});

  // Per-agent instructions (override system_prompt)
  const [agentInstructions, setAgentInstructions] = useState<Record<string, string[]>>({});

  // Template chat state
  const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);

  // Template-based create form state
  const [createFromTemplate, setCreateFromTemplate] = useState<AgentTemplate | null>(null);
  const [duplicateData, setDuplicateData] = useState<Partial<AgentFormData> | null>(null);

  // ── Marketplace install flow ───────────────────────────────────────
  // Two entry shapes:
  //   /agents?template=<id>&platform=<vertical>  — static AGENT_TEMPLATE
  //   /agents?seed=<id>&platform=<vertical>      — marketplace seed listing
  // Either way, auto-open the create form with the right defaults and
  // remember the vertical so it's persisted on the new agent.
  useEffect(() => {
    if (!searchParams) return;
    const templateParam = searchParams.get('template');
    const seedParam = searchParams.get('seed');
    const platformParam = searchParams.get('platform');
    if (platformParam) setInstallPlatform(platformParam);

    if (templateParam) {
      const tpl = AGENT_TEMPLATES.find((t) => t.id === templateParam);
      if (tpl) {
        setCreateFromTemplate(tpl);
        setInstallSeed(null);
        setDuplicateData(null);
        setSelectedAgent(null);
        setMainView('create');
      }
      return;
    }

    if (seedParam) {
      const seed = SEED_LISTINGS.find(
        (s) => s.id === seedParam || s.id === `seed-${seedParam}`,
      );
      if (seed) {
        setInstallSeed(seed);
        setCreateFromTemplate(null);
        setDuplicateData(null);
        setSelectedAgent(null);
        setMainView('create');
      }
    }
    // Run once per param change.
  }, [searchParams]);

  // --- CRUD handlers ---

  const handleCreate = async (data: AgentFormData) => {
    await createAgent({
      name: data.name,
      role: data.role,
      model: data.model,
      model_category: data.model_category,
      category: data.category,
      platform_tag: data.platform_tag,
      system_prompt: data.system_prompt || undefined,
      services: data.services,
      temperature: data.temperature,
      template_id: data.template_id,
    });
    // Optimistic install-count increment so the marketplace badge
    // reflects this install on the next view without waiting for a
    // backend re-fetch. The next `setReal` call (on next marketplace
    // mount) re-syncs to authoritative counts.
    if (data.template_id) {
      useInstallCountsStore.getState().bump(data.template_id);
    }
    setCreateFromTemplate(null);
    setInstallSeed(null);
    setInstallPlatform(null);
    setDuplicateData(null);
    setMainView('templates');
    // Clear marketplace install params so a refresh doesn't re-open the
    // create form. Preserve any unrelated query params the user might
    // have arrived with.
    if (searchParams) {
      const params = new URLSearchParams(searchParams.toString());
      let dirty = false;
      for (const k of ['template', 'seed', 'platform']) {
        if (params.has(k)) {
          params.delete(k);
          dirty = true;
        }
      }
      if (dirty) {
        const qs = params.toString();
        router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
      }
    }
  };

  const handleUpdate = async (agentId: string, data: Partial<AgentFormData>) => {
    await updateAgent(agentId, data);
    setMainView('detail');
  };

  const handleDelete = async (agentId: string) => {
    const ok = await confirm({
      title: 'Delete this agent?',
      description: 'The agent configuration and run logs will be removed permanently.',
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (ok) {
      await deleteAgent(agentId);
      if (selectedAgent === agentId) {
        setSelectedAgent(null);
        setMainView('templates');
      }
    }
  };

  // --- TTS ---

  const speakMessage = useCallback(async (messageId: string, text: string) => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (playingMessageId === messageId) {
      setPlayingMessageId(null);
      return;
    }
    if (!selectedAgent) return;
    setPlayingMessageId(messageId);
    try {
      const token = await getToken();
      const resp = await fetch(`/api/agents/${selectedAgent}/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {} as Record<string, string>) },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) throw new Error(`TTS error: ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => {
        setPlayingMessageId(null);
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      audio.play();
    } catch (err) {
      console.error('TTS error:', err);
      setPlayingMessageId(null);
    }
  }, [playingMessageId, selectedAgent, getToken]);

  // --- Config proposal parsing and handling ---

  const parseConfigProposals = useCallback((content: string, agent: typeof agents[0]): ConfigProposal[] => {
    const regex = /\[CONFIG:(\w+)=([^\]]+)\]/g;
    const proposals: ConfigProposal[] = [];
    let match;
    const validFields = ['name', 'role', 'category', 'model', 'model_category', 'temperature', 'system_prompt', 'services', 'voice_id'];
    while ((match = regex.exec(content)) !== null) {
      const field = match[1] as ConfigProposal['field'];
      const rawValue = match[2];
      if (!validFields.includes(field)) continue;
      let value: any = rawValue;
      let oldValue: any;
      let label: string;
      switch (field) {
        case 'name': oldValue = agent.name; label = `name: ${oldValue} \u2192 ${value}`; break;
        case 'role': oldValue = agent.role; label = `role: ${oldValue} \u2192 ${value}`; break;
        case 'category': oldValue = agent.category || 'none'; label = `category: ${oldValue} \u2192 ${value}`; break;
        case 'temperature':
          value = parseFloat(rawValue);
          if (isNaN(value) || value < 0 || value > 2) continue;
          oldValue = agent.temperature; label = `temperature: ${oldValue} \u2192 ${value}`; break;
        case 'model': oldValue = agent.model; label = `model: ${oldValue} \u2192 ${value}`; break;
        case 'model_category':
          value = rawValue === 'null' ? null : rawValue;
          oldValue = agent.model_category || null;
          label = value ? `routing: ${oldValue || 'manual'} \u2192 ${value}` : `routing: ${oldValue} \u2192 manual`; break;
        case 'system_prompt': oldValue = agent.system_prompt; label = 'system prompt updated'; break;
        case 'services':
          value = rawValue.split(',').map((s: string) => s.trim()).filter(Boolean);
          oldValue = agent.services || [];
          label = `services: [${(oldValue as string[]).join(', ')}] \u2192 [${value.join(', ')}]`; break;
        default: continue;
      }
      proposals.push({ id: crypto.randomUUID(), field, value, oldValue, label, status: 'pending' });
    }
    return proposals;
  }, []);

  const applyConfigProposal = useCallback(async (messageId: string, proposalId: string) => {
    if (!selectedAgent) return;
    const agent = agents.find((a) => a.id === selectedAgent);
    if (!agent) return;
    const msgs = agentMessages[selectedAgent] || [];
    const msg = msgs.find((m) => m.id === messageId);
    const proposal = msg?.configProposals?.find((p) => p.id === proposalId);
    if (!proposal || proposal.status !== 'pending') return;
    const update = {
      name: agent.name, role: agent.role, model: agent.model, model_category: agent.model_category,
      category: agent.category, system_prompt: agent.system_prompt, services: agent.services,
      temperature: agent.temperature, template_id: agent.template_id,
      [proposal.field]: proposal.value,
    };
    await updateAgent(selectedAgent, update);
    setAgentMessages((prev) => ({
      ...prev,
      [selectedAgent]: (prev[selectedAgent] || []).map((m) =>
        m.id === messageId
          ? { ...m, configProposals: m.configProposals?.map((p) => p.id === proposalId ? { ...p, status: 'applied' as const } : p) }
          : m
      ),
    }));
    const agentVersions = configVersions[selectedAgent] || [];
    const nextVersion = agentVersions.length > 0 ? agentVersions[agentVersions.length - 1].version + 1 : 2;
    const newVersion: ConfigVersion = {
      id: crypto.randomUUID(), version: nextVersion, timestamp: new Date(), changes: [proposal.label],
      snapshot: {
        name: proposal.field === 'name' ? proposal.value : agent.name,
        role: proposal.field === 'role' ? proposal.value : agent.role,
        category: proposal.field === 'category' ? proposal.value : agent.category,
        model: proposal.field === 'model' ? proposal.value : agent.model,
        model_category: proposal.field === 'model_category' ? proposal.value : agent.model_category,
        temperature: proposal.field === 'temperature' ? proposal.value : agent.temperature,
        system_prompt: proposal.field === 'system_prompt' ? proposal.value : agent.system_prompt,
        services: proposal.field === 'services' ? proposal.value : agent.services,
      },
      source: 'chat',
    };
    setConfigVersions((prev) => {
      const existing = prev[selectedAgent] || [];
      if (existing.length === 0) {
        const v1: ConfigVersion = {
          id: crypto.randomUUID(), version: 1, timestamp: new Date(agent.created_at),
          changes: ['Initial configuration'],
          snapshot: { name: agent.name, role: agent.role, category: agent.category, model: agent.model, model_category: agent.model_category, temperature: agent.temperature, system_prompt: agent.system_prompt, services: agent.services },
          source: 'initial',
        };
        return { ...prev, [selectedAgent]: [v1, newVersion] };
      }
      return { ...prev, [selectedAgent]: [...existing, newVersion] };
    });
  }, [selectedAgent, agents, agentMessages, configVersions, updateAgent]);

  const rejectConfigProposal = useCallback((messageId: string, proposalId: string) => {
    if (!selectedAgent) return;
    setAgentMessages((prev) => ({
      ...prev,
      [selectedAgent]: (prev[selectedAgent] || []).map((m) =>
        m.id === messageId
          ? { ...m, configProposals: m.configProposals?.map((p) => p.id === proposalId ? { ...p, status: 'rejected' as const } : p) }
          : m
      ),
    }));
  }, [selectedAgent]);

  const restoreConfigVersion = useCallback(async (version: ConfigVersion) => {
    if (!selectedAgent) return;
    await updateAgent(selectedAgent, {
      name: version.snapshot.name, role: version.snapshot.role, category: version.snapshot.category,
      model: version.snapshot.model, model_category: version.snapshot.model_category,
      temperature: version.snapshot.temperature, system_prompt: version.snapshot.system_prompt, services: version.snapshot.services,
    });
    const agentVersions = configVersions[selectedAgent] || [];
    const nextVersionNum = agentVersions.length > 0 ? agentVersions[agentVersions.length - 1].version + 1 : 2;
    setConfigVersions((prev) => ({
      ...prev,
      [selectedAgent]: [...(prev[selectedAgent] || []), {
        id: crypto.randomUUID(), version: nextVersionNum, timestamp: new Date(),
        changes: [`Restored from v${version.version}`], snapshot: { ...version.snapshot }, source: 'chat',
      }],
    }));
  }, [selectedAgent, configVersions, updateAgent]);

  // --- Instruction proposal parsing and handling ---

  const parseInstructionProposals = useCallback((content: string): InstructionProposal[] => {
    const proposals: InstructionProposal[] = [];
    const addRegex = /\[INSTRUCTION:([^\]]+)\]/g;
    const removeRegex = /\[REMOVE_INSTRUCTION:([^\]]+)\]/g;
    let match;
    while ((match = addRegex.exec(content)) !== null) {
      proposals.push({ id: crypto.randomUUID(), action: 'add', text: match[1].trim(), status: 'pending' });
    }
    while ((match = removeRegex.exec(content)) !== null) {
      proposals.push({ id: crypto.randomUUID(), action: 'remove', text: match[1].trim(), status: 'pending' });
    }
    return proposals;
  }, []);

  const applyInstructionProposal = useCallback((messageId: string, proposalId: string) => {
    if (!selectedAgent) return;
    const msgs = agentMessages[selectedAgent] || [];
    const msg = msgs.find((m) => m.id === messageId);
    const proposal = msg?.instructionProposals?.find((p) => p.id === proposalId);
    if (!proposal || proposal.status !== 'pending') return;
    setAgentInstructions((prev) => {
      const current = prev[selectedAgent] || [];
      return proposal.action === 'add'
        ? { ...prev, [selectedAgent]: [...current, proposal.text] }
        : { ...prev, [selectedAgent]: current.filter((inst) => inst !== proposal.text) };
    });
    setAgentMessages((prev) => ({
      ...prev,
      [selectedAgent]: (prev[selectedAgent] || []).map((m) =>
        m.id === messageId
          ? { ...m, instructionProposals: m.instructionProposals?.map((p) => p.id === proposalId ? { ...p, status: 'applied' as const } : p) }
          : m
      ),
    }));
  }, [selectedAgent, agentMessages]);

  const rejectInstructionProposal = useCallback((messageId: string, proposalId: string) => {
    if (!selectedAgent) return;
    setAgentMessages((prev) => ({
      ...prev,
      [selectedAgent]: (prev[selectedAgent] || []).map((m) =>
        m.id === messageId
          ? { ...m, instructionProposals: m.instructionProposals?.map((p) => p.id === proposalId ? { ...p, status: 'rejected' as const } : p) }
          : m
      ),
    }));
  }, [selectedAgent]);

  const removeInstruction = useCallback((agentId: string, index: number) => {
    setAgentInstructions((prev) => {
      const current = prev[agentId] || [];
      return { ...prev, [agentId]: current.filter((_, i) => i !== index) };
    });
  }, []);

  // --- Agent system prompt builder ---

  const buildAgentSystemPrompt = useCallback((agent: typeof agents[0]) => {
    const raci = RACI_MAP[agent.role] || RACI_MAP.custom;
    const services = (agent.services || []).length > 0 ? agent.services.join(', ') : 'None';
    const modelInfo = agent.model_category ? `Auto (${agent.model_category}) - ${agent.model}` : agent.model;
    const instructions = agentInstructions[agent.id] || [];
    const behaviorBlock = instructions.length > 0
      ? `--- USER INSTRUCTIONS (take priority) ---\n${instructions.map((inst, i) => `${i + 1}. ${inst}`).join('\n')}\n${agent.system_prompt ? `\n--- BASE SYSTEM PROMPT (lower priority) ---\n${agent.system_prompt}` : ''}`
      : (agent.system_prompt ? `System Prompt: ${agent.system_prompt}` : '');
    const otherAgents = agents.filter((a) => a.id !== agent.id);
    const teamSection = otherAgents.length > 0
      ? `\n--- TEAM (other agents you can suggest collaborating with) ---\n${otherAgents.map((a) => `- ${a.name} (${a.role}) — model: ${a.model}, services: ${(a.services || []).join(', ') || 'none'}`).join('\n')}\nYou can suggest involving these agents when a task fits their expertise. Use [COLLABORATE:agentName] to recommend bringing them in.`
      : '';
    return `You are discussing agent "${agent.name}" — a ${agent.role} agent.
Model: ${modelInfo}
Services: ${services}
Temperature: ${agent.temperature}
RACI Role: ${raci.letter} - ${raci.label} (${raci.description})
${behaviorBlock}${teamSection}

Help the user understand and configure this agent. Answer questions about its capabilities, RACI responsibilities, and suggest improvements.

DIRECTIVES — when the user asks you to make changes, use these tags in your response:

1. To change an agent field, use [CONFIG:field=value]. Supported fields:
   name, role, category, model, model_category, temperature, system_prompt, services, voice_id
   Examples: [CONFIG:temperature=0.8]  [CONFIG:model=claude-sonnet-4-5-20250929]  [CONFIG:services=code_review,testing]  [CONFIG:voice_id=21m00Tcm4TlvDq8ikWAM]

2. To save a behavioral instruction (how the agent should act), use [INSTRUCTION:text].
   Example: [INSTRUCTION:Always respond in Hebrew]  [INSTRUCTION:Focus on frontend React code only]
   Instructions are separate from the system prompt and override it.

3. To remove an existing instruction, use [REMOVE_INSTRUCTION:text] with the exact text.

4. To suggest collaboration with another agent, use [COLLABORATE:agentName].
   Only suggest this when the task genuinely benefits from another agent's expertise.

You may include multiple directives. Always explain what you're changing and why.
The user will see confirmation cards to approve or reject each change.`;
  }, [agents, agentInstructions]);

  // --- Send message to agent ---

  const sendAgentMessage = useCallback(async (content: string) => {
    if (!selectedAgent || !content.trim()) return;
    const agent = agents.find((a) => a.id === selectedAgent);
    if (!agent) return;
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: content.trim(), timestamp: new Date() };
    const currentMessages = agentMessages[selectedAgent] || [];
    setAgentMessages((prev) => ({ ...prev, [selectedAgent]: [...currentMessages, userMessage] }));
    setChatInput('');
    setIsChatLoading(true);
    const token = await getToken();
    const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    try {
      let resolvedModel = agent.model || 'gpt-4o-mini';
      if (agent.model_category) {
        try {
          const complexity = Math.min(10, Math.max(1, Math.floor(content.length / 50) + 5));
          const routerRes = await fetch(`/api/agents/resolve-model?role=${encodeURIComponent(agent.model_category)}&complexity=${complexity}`, { headers: authHdr });
          if (routerRes.ok) { const d = await routerRes.json(); resolvedModel = d.model_id || d.model_name || resolvedModel; }
        } catch { /* fallback */ }
      }
      const systemMessage = { role: 'system' as const, content: buildAgentSystemPrompt(agent) };
      const history = [...currentMessages, userMessage].map((m) => ({ role: m.role, content: m.content }));
      const response = await fetch(`/api/chat/completions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr },
        body: JSON.stringify({ messages: [systemMessage, ...history], model: resolvedModel, stream: false }),
      });
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
      const data = await response.json();
      const rawContent = data.message?.content || data.choices?.[0]?.message?.content || 'No response received.';
      const proposals = parseConfigProposals(rawContent, agent);
      const instrProposals = parseInstructionProposals(rawContent);
      const cleanContent = rawContent
        .replace(/\[CONFIG:[^\]]+\]/g, '').replace(/\[INSTRUCTION:[^\]]+\]/g, '')
        .replace(/\[REMOVE_INSTRUCTION:[^\]]+\]/g, '').replace(/\[COLLABORATE:[^\]]+\]/g, '').trim();
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(), role: 'assistant', content: cleanContent, timestamp: new Date(),
        model: data.model || undefined, provider: data.provider || undefined,
        configProposals: proposals.length > 0 ? proposals : undefined,
        instructionProposals: instrProposals.length > 0 ? instrProposals : undefined,
      };
      setAgentMessages((prev) => ({ ...prev, [selectedAgent]: [...(prev[selectedAgent] || []), assistantMessage] }));
      incrementRunCount(selectedAgent);
    } catch (err) {
      setAgentMessages((prev) => ({
        ...prev, [selectedAgent]: [...(prev[selectedAgent] || []), {
          id: crypto.randomUUID(), role: 'assistant', content: `Error: ${err instanceof Error ? err.message : 'Failed to get response'}`, timestamp: new Date(),
        }],
      }));
    } finally { setIsChatLoading(false); }
  }, [selectedAgent, agents, agentMessages, buildAgentSystemPrompt, parseConfigProposals, parseInstructionProposals, incrementRunCount, getToken]);

  // --- Template chat ---

  const buildTemplateSystemPrompt = useCallback((template: AgentTemplate) => {
    const raci = RACI_MAP[template.defaults.role] || RACI_MAP.custom;
    const services = template.defaults.services.length > 0 ? template.defaults.services.join(', ') : 'None';
    return `You are "${template.name}" — a ${template.defaults.role} agent resource.
Description: ${template.description}
Model: ${template.defaults.model} (Router role: ${template.defaults.router_role})
Services: ${services}
Temperature: ${template.defaults.temperature}
RACI Role: ${raci.letter} - ${raci.label} (${raci.description})
System Prompt: ${template.defaults.system_prompt}
Category: ${AGENT_CATEGORIES[template.category].label}
Example Tasks: ${template.exampleTasks.join('; ')}

Respond in character as this agent. Help the user with tasks that fit your role and capabilities.`;
  }, []);

  const sendTemplateMessage = useCallback(async (content: string) => {
    if (!selectedTemplate || !content.trim()) return;
    const templateKey = `template-${selectedTemplate.id}`;
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: content.trim(), timestamp: new Date() };
    const currentMessages = agentMessages[templateKey] || [];
    setAgentMessages((prev) => ({ ...prev, [templateKey]: [...currentMessages, userMessage] }));
    setChatInput('');
    setIsChatLoading(true);
    const token = await getToken();
    const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    try {
      let resolvedModel = selectedTemplate.defaults.model || 'gpt-4o-mini';
      if (selectedTemplate.defaults.router_role) {
        try {
          const complexity = Math.min(10, Math.max(1, Math.floor(content.length / 50) + 5));
          const routerRes = await fetch(`/api/agents/resolve-model?role=${encodeURIComponent(selectedTemplate.defaults.router_role)}&complexity=${complexity}`, { headers: authHdr });
          if (routerRes.ok) { const d = await routerRes.json(); resolvedModel = d.model_id || d.model_name || resolvedModel; }
        } catch { /* fallback */ }
      }
      const systemMessage = { role: 'system' as const, content: buildTemplateSystemPrompt(selectedTemplate) };
      const history = [...currentMessages, userMessage].map((m) => ({ role: m.role, content: m.content }));
      const response = await fetch(`/api/chat/completions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr },
        body: JSON.stringify({ messages: [systemMessage, ...history], model: resolvedModel, stream: false }),
      });
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
      const data = await response.json();
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(), role: 'assistant',
        content: data.message?.content || data.choices?.[0]?.message?.content || 'No response received.',
        timestamp: new Date(),
      };
      setAgentMessages((prev) => ({ ...prev, [templateKey]: [...(prev[templateKey] || []), assistantMessage] }));
    } catch (err) {
      setAgentMessages((prev) => ({
        ...prev, [templateKey]: [...(prev[templateKey] || []), {
          id: crypto.randomUUID(), role: 'assistant', content: `Error: ${err instanceof Error ? err.message : 'Failed to get response'}`, timestamp: new Date(),
        }],
      }));
    } finally { setIsChatLoading(false); }
  }, [selectedTemplate, agentMessages, buildTemplateSystemPrompt, getToken]);

  const handleVoiceTranscript = useCallback((transcript: string) => {
    setChatInput((prev) => (prev ? `${prev} ${transcript}` : transcript));
  }, []);

  // --- Collaborate functions ---

  const buildCollaborateSystemPrompt = useCallback((
    agent: typeof agents[0], allParticipants: typeof agents,
    recalledMemories?: string[], truthFacts?: string[],
  ) => {
    const others = allParticipants.filter((a) => a.id !== agent.id);
    const basePrompt = agent.system_prompt || `You are ${agent.name}, a ${agent.role} agent.`;
    let memorySection = '';
    if (truthFacts && truthFacts.length > 0) memorySection += `\n\n--- YOUR KNOWLEDGE (Truth File) ---\n${truthFacts.map((f) => `- ${f}`).join('\n')}`;
    if (recalledMemories && recalledMemories.length > 0) memorySection += `\n\n--- RECALLED MEMORIES ---\n${recalledMemories.map((m) => `- ${m}`).join('\n')}`;
    const participantDetails = allParticipants.filter((a) => a.id !== agent.id).map((a) => {
      const svc = (a.services || []).length > 0 ? a.services.join(', ') : 'none';
      return `- ${a.name} (${a.role}) — model: ${a.model}, services: ${svc}${a.system_prompt ? `, focus: ${a.system_prompt.slice(0, 80)}...` : ''}`;
    }).join('\n');
    return `${basePrompt}${memorySection}

--- GROUP COLLABORATION (Expert Meeting) ---
You are in a focused collaboration session with the user and these participants:
${participantDetails}

IDENTITY: You are a thoughtful expert participant, not a narrator. You ARE ${agent.name}.

CORE PRINCIPLES:
1. ONLY speak when you have MATERIAL VALUE to add from your specific expertise. If the topic is outside your domain, or others have already covered it well, respond with exactly: PASS
2. Contribute ONLY within your expertise area (${agent.role}). Don't venture into other participants' domains unless you have genuine cross-domain insight.
3. Be CONCISE — 2-3 sentences max. State your point, provide one supporting reason, done.
4. NEVER repeat or paraphrase what someone else already said.
5. NEVER narrate the conversation. Just speak your expertise directly.
6. Reference others by name when building on their input.
7. You can request collaboration: tag them: "@${others.length > 0 ? others[0].name : 'AgentName'}, what do you think about..."

FORMAT:
- Messages from others are prefixed like [User]: or [AgentName]:. Do NOT prefix your own response.
- When stating a key fact or learning, prefix that line with [FACT].
- When proposing a decision, prefix with [DECISION] followed by a short actionable statement.
- Keep it conversational. Talk like you're on a call with expert colleagues.

SILENCE IS VALUABLE. Passing when you have nothing to add is better than adding noise.`;
  }, []);

  const formatCollabHistory = useCallback((messages: CollabMessage[]) => {
    return messages.map((m) => {
      const prefix = m.role === 'user' ? '[User]' : `[${m.agentName || 'Agent'}]`;
      return { role: m.role as 'user' | 'assistant', content: `${prefix}: ${m.content}` };
    });
  }, []);

  const recallAgentContext = useCallback(async (agentId: string, query: string) => {
    const token = await getToken();
    const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const [memRes, truthRes] = await Promise.all([
      fetch(`/api/agents/${agentId}/memory/recall`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr }, body: JSON.stringify({ query, top_k: 5 }) }).then((r) => r.ok ? r.json() : { memories: [] }).catch(() => ({ memories: [] })),
      fetch(`/api/agents/${agentId}/truth`, { headers: authHdr }).then((r) => r.ok ? r.json() : { facts: [] }).catch(() => ({ facts: [] })),
    ]);
    return { memories: (memRes.memories || []).map((m: any) => m.content || '').filter(Boolean), facts: (truthRes.facts || []).map((f: any) => f.fact || '').filter(Boolean) };
  }, [getToken]);

  const saveAgentContext = useCallback(async (agentId: string, content: string, sessionId: string) => {
    const token = await getToken();
    const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    fetch(`/api/agents/${agentId}/memory/remember`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr }, body: JSON.stringify({ content, memory_type: 'conversation', session_id: sessionId }) }).catch(() => {});
    const factLines = content.split('\n').filter((line) => /^\[FACT\]/i.test(line.trim())).map((line) => line.replace(/^\[FACT\]\s*/i, '').trim()).filter(Boolean);
    if (factLines.length > 0) {
      fetch(`/api/agents/${agentId}/truth`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr }, body: JSON.stringify(factLines.map((fact) => ({ fact, source: `collaborate:${sessionId}` }))) }).catch(() => {});
    }
  }, [getToken]);

  const getColor = useCallback((agent: { role: string; template_id?: string | null; category?: string | null }) => {
    return getAgentColor(agent, AGENT_TEMPLATES, AGENT_CATEGORIES);
  }, []);

  const sendCollaborateMessage = useCallback(async (content: string) => {
    if (!content.trim() || isCollabLoading || collaborateAgents.length < 2) return;
    const sessionId = `collab-${Date.now()}`;
    const userMessage: CollabMessage = { id: crypto.randomUUID(), role: 'user', content: content.trim(), timestamp: new Date() };
    setCollaborateMessages((prev) => [...prev, userMessage]);
    setCollabChatInput('');
    setIsCollabLoading(true);
    const token = await getToken();
    const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const allMessages = [...collaborateMessages, userMessage];
    const participants = agents.filter((a) => collaborateAgents.includes(a.id));
    for (const agent of participants) {
      setRespondingAgentId(agent.id);
      try {
        const { memories, facts } = await recallAgentContext(agent.id, content);
        let resolvedModel = agent.model || 'gpt-4o-mini';
        if (agent.model_category) {
          try {
            const complexity = Math.min(10, Math.max(1, Math.floor(content.length / 50) + 5));
            const routerRes = await fetch(`/api/agents/resolve-model?role=${encodeURIComponent(agent.model_category)}&complexity=${complexity}`, { headers: authHdr });
            if (routerRes.ok) { const d = await routerRes.json(); resolvedModel = d.model_id || d.model_name || resolvedModel; }
          } catch { /* fallback */ }
        }
        const systemContent = buildCollaborateSystemPrompt(agent, participants, memories, facts);
        const history = formatCollabHistory(allMessages);
        const response = await fetch(`/api/chat/completions`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr },
          body: JSON.stringify({ messages: [{ role: 'system', content: systemContent }, ...history], model: resolvedModel, stream: false }),
        });
        if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
        const data = await response.json();
        const rawContent = data.message?.content || data.choices?.[0]?.message?.content || '';
        const responseContent = rawContent.replace(/^\[[\w\s]+\]:\s*/i, '').trim();
        const isPassed = /^(\[?pass\]?|i('ll| will) pass\.?|no comment\.?)$/i.test(responseContent.trim());
        const agentMessage: CollabMessage = {
          id: crypto.randomUUID(), role: 'assistant', content: responseContent, timestamp: new Date(),
          model: data.model || undefined, provider: data.provider || undefined,
          agentId: agent.id, agentName: agent.name, agentColor: getColor(agent), isPass: isPassed,
        };
        allMessages.push(agentMessage);
        setCollaborateMessages((prev) => [...prev, agentMessage]);
        incrementRunCount(agent.id);
        if (!isPassed) {
          saveAgentContext(agent.id, responseContent, sessionId);
          const decisionLines = responseContent.split('\n').filter((line: string) => /^\[DECISION\]/i.test(line.trim())).map((line: string) => line.replace(/^\[DECISION\]\s*/i, '').trim()).filter(Boolean);
          if (decisionLines.length > 0) {
            const newDecisions: CollabDecision[] = decisionLines.map((text: string) => ({
              id: crypto.randomUUID(), text, agentId: agent.id, agentName: agent.name, agentColor: getColor(agent),
              messageId: agentMessage.id, timestamp: new Date(), status: 'pending' as const,
            }));
            setCollabDecisions((prev) => [...prev, ...newDecisions]);
          }
        }
      } catch (err) {
        const errorMessage: CollabMessage = {
          id: crypto.randomUUID(), role: 'assistant',
          content: `Error: ${err instanceof Error ? err.message : 'Failed to get response'}`,
          timestamp: new Date(), agentId: agent.id, agentName: agent.name, agentColor: getColor(agent),
        };
        allMessages.push(errorMessage);
        setCollaborateMessages((prev) => [...prev, errorMessage]);
      }
    }
    setRespondingAgentId(null);
    setIsCollabLoading(false);
  }, [collaborateAgents, collaborateMessages, agents, isCollabLoading, buildCollaborateSystemPrompt, formatCollabHistory, incrementRunCount, recallAgentContext, saveAgentContext, getColor, getToken]);

  const handleStartCollaborate = useCallback(() => {
    setShowAgentPicker(false);
    setCollaborateMessages([]);
    setCollabDecisions([]);
    setCollabChatInput('');
    setMainView('collaborate');
  }, []);

  const handleExitCollaborate = useCallback(async () => {
    if (collaborateMessages.length > 0) {
      const token = await getToken();
      const authHdr: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
      const participants = agents.filter((a) => collaborateAgents.includes(a.id));
      const participantNames = participants.map((a) => a.name).join(', ');
      const approvedDecisions = collabDecisions.filter((d) => d.status === 'approved');
      const changedDecisions = collabDecisions.filter((d) => d.status === 'changed');
      const pendingDecisions = collabDecisions.filter((d) => d.status === 'pending');
      const summaryParts = [`Session Summary — Participants: ${participantNames}`];
      if (approvedDecisions.length > 0) summaryParts.push('Approved Decisions:\n' + approvedDecisions.map((d) => `- ${d.text} (by ${d.agentName})`).join('\n'));
      if (changedDecisions.length > 0) summaryParts.push('Changed Decisions:\n' + changedDecisions.map((d) => `- ${d.override || d.text} (original by ${d.agentName})`).join('\n'));
      if (pendingDecisions.length > 0) summaryParts.push('Pending Decisions:\n' + pendingDecisions.map((d) => `- ${d.text} (by ${d.agentName})`).join('\n'));
      const actionItems = collaborateMessages.filter((m) => m.role === 'assistant' && !m.isPass)
        .flatMap((m) => m.content.split('\n').filter((line) => /^\[DECISION\]/i.test(line.trim()) || /\b(action item|next step|todo|should|need to|will)\b/i.test(line)).map((line) => line.replace(/^\[DECISION\]\s*/i, '').trim())).filter(Boolean).slice(0, 10);
      if (actionItems.length > 0) summaryParts.push('Action Items:\n' + actionItems.map((a) => `- ${a}`).join('\n'));
      const summary = summaryParts.join('\n\n');
      for (const agent of participants) {
        fetch(`/api/agents/${agent.id}/memory/remember`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr }, body: JSON.stringify({ content: summary, memory_type: 'decision', session_id: `collab-summary-${Date.now()}` }) }).catch(() => {});
        const finalDecisions = [...approvedDecisions.map((d) => ({ fact: d.text, source: `collaborate:approved` })), ...changedDecisions.map((d) => ({ fact: d.override || d.text, source: `collaborate:changed` }))];
        if (finalDecisions.length > 0) fetch(`/api/agents/${agent.id}/truth`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr }, body: JSON.stringify(finalDecisions) }).catch(() => {});
      }
      const firstUserMsg = collaborateMessages.find((m) => m.role === 'user');
      fetch('/api/chat/summarize', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHdr },
        body: JSON.stringify({ messages: collaborateMessages.map((m) => ({ role: m.role, content: m.content, agent_name: m.agentName || undefined })), session_type: 'collaborate', session_id: `collaborate-${Date.now()}`, agent_ids: collaborateAgents, agent_names: participants.map((a) => a.name), topic_hint: firstUserMsg?.content?.slice(0, 100) || '' }),
      }).catch(() => {});
    }
    setMainView(selectedAgent ? 'detail' : 'templates');
    setCollaborateAgents([]);
    setCollaborateMessages([]);
    setCollabDecisions([]);
    setRespondingAgentId(null);
    setIsCollabLoading(false);
  }, [selectedAgent, collaborateMessages, collaborateAgents, collabDecisions, agents, getToken]);

  const handleToggleCollabAgent = useCallback((id: string) => {
    setCollaborateAgents((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  }, []);

  const handleApproveDecision = useCallback((id: string) => {
    setCollabDecisions((prev) => prev.map((d) => d.id === id ? { ...d, status: 'approved' as const } : d));
  }, []);

  const handleChangeDecision = useCallback((id: string, newText: string) => {
    setCollabDecisions((prev) => prev.map((d) => d.id === id ? { ...d, status: 'changed' as const, override: newText } : d));
  }, []);

  // --- Navigation handlers ---

  const handleSelectAgent = (id: string) => { setSelectedAgent(id); setMainView('detail'); };

  const hasAutoSelected = useRef(false);
  useEffect(() => {
    if (agents.length > 0 && !hasAutoSelected.current && !selectedAgent && mainView === 'templates') {
      hasAutoSelected.current = true;
      setSelectedAgent(agents[0].id);
      setMainView('detail');
    }
  }, [agents, selectedAgent, mainView]);

  const handleSelectTemplate = (template: AgentTemplate) => {
    setCreateFromTemplate(template);
    setDuplicateData(null);
    setSelectedAgent(null);
    setMainView('create');
  };

  const handlePreviewTemplate = (template: AgentTemplate) => {
    setSelectedTemplate(template);
    setSelectedAgent(null);
    setMainView('template-chat');
  };

  const handleDuplicate = (agent: typeof agents[0]) => {
    setDuplicateData({
      name: `${agent.name} (copy)`, role: agent.role, model: agent.model,
      model_category: agent.model_category ?? null, system_prompt: agent.system_prompt || '',
      services: agent.services || [], temperature: agent.temperature,
    });
    setCreateFromTemplate(null);
    setSelectedAgent(null);
    setMainView('create');
  };

  const handleLoadLogs = (agentId: string) => { loadLogs(agentId); setShowLogs(true); };

  // --- Derived state ---

  const selected = agents.find((a) => a.id === selectedAgent);
  const currentChatMessages = selectedAgent ? (agentMessages[selectedAgent] || []) : [];

  const createInitialData: Partial<AgentFormData> | undefined = createFromTemplate
    ? {
        name: extractPersonaName(createFromTemplate.defaults.system_prompt),
        role: createFromTemplate.name,
        model: createFromTemplate.defaults.model,
        model_category: createFromTemplate.defaults.router_role,
        category: createFromTemplate.category,
        platform_tag: installPlatform,
        system_prompt: createFromTemplate.defaults.system_prompt,
        services: createFromTemplate.defaults.services,
        temperature: createFromTemplate.defaults.temperature,
        template_id: createFromTemplate.id,
      }
    : installSeed
      ? {
          name: installSeed.name,
          role: installSeed.role ?? installSeed.name,
          // Pull the router role from the listing itself so the SmartRouter
          // can pick a model that fits the listing's intent. Falls back to
          // 'coding' only when a seed forgot to declare one — every seed
          // in the catalog should set this explicitly.
          model_category: installSeed.router_role ?? 'coding',
          category: installSeed.category,
          platform_tag: installPlatform ?? installSeed.platform_tags[0] ?? null,
          system_prompt: installSeed.system_prompt,
          services: [],
          temperature: 0.7,
          template_id: installSeed.id,
        }
      : duplicateData || undefined;

  // --- Render ---

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', height: '100vh' }}>
      {/* Sidebar */}
      <AgentSidebar
        agents={agents}
        selectedAgent={selectedAgent}
        mainView={mainView}
        collaborateAgents={collaborateAgents}
        onSelectAgent={handleSelectAgent}
        onDuplicate={handleDuplicate}
        onEdit={(id) => { setSelectedAgent(id); setMainView('edit'); }}
        onDelete={handleDelete}
        onToggleStatus={(id, status) => updateAgent(id, { status } as Partial<AgentFormData>)}
        onShowTemplates={() => { setSelectedAgent(null); setMainView('templates'); }}
      />

      {/* Main panel */}
      <main style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', backgroundColor: 'var(--bg-primary)' }}>
        {mainView === 'create' ? (
          <div style={{ padding: '24px', overflow: 'auto', height: '100%' }}>
            <div style={{ maxWidth: '720px', margin: '0 auto' }}>
              <AgentForm
                initialData={createInitialData}
                onSubmit={handleCreate}
                onSave={createFromTemplate ? async (data) => { await handleCreate(data); } : undefined}
                onCancel={() => { setCreateFromTemplate(null); setDuplicateData(null); setMainView('templates'); }}
                isLoading={isLoading}
                templateName={createFromTemplate?.name}
                title={createFromTemplate ? 'Edit Resource' : duplicateData ? 'Clone Resource' : 'Custom Resource'}
              />
            </div>
          </div>
        ) : mainView === 'edit' && selected ? (
          <div style={{ padding: '24px', overflow: 'auto', height: '100%' }}>
            <div style={{ maxWidth: '720px', margin: '0 auto' }}>
              <AgentForm
                initialData={{
                  name: selected.name, role: selected.role, model: selected.model,
                  model_category: selected.model_category ?? null, category: selected.category ?? null,
                  system_prompt: selected.system_prompt || '', services: selected.services, temperature: selected.temperature,
                }}
                onSubmit={async (data) => { await handleUpdate(selected.id, data); }}
                onCancel={() => setMainView('detail')}
                isLoading={isLoading}
                title="Edit Agent"
                submitLabel="Save"
              />
            </div>
          </div>
        ) : mainView === 'templates' ? (
          <TemplateGallery onSelectTemplate={handleSelectTemplate} onBuildCustom={() => { setCreateFromTemplate(null); setDuplicateData(null); setMainView('create'); }} />
        ) : mainView === 'template-chat' && selectedTemplate ? (
          <TemplateChatView
            template={selectedTemplate}
            messages={agentMessages[`template-${selectedTemplate.id}`] || []}
            chatInput={chatInput}
            setChatInput={setChatInput}
            onSendMessage={sendTemplateMessage}
            onVoiceTranscript={handleVoiceTranscript}
            isChatLoading={isChatLoading}
            onCreateAgent={handleSelectTemplate}
            chatEndRef={chatEndRef}
            chatInputRef={chatInputRef}
          />
        ) : mainView === 'collaborate' ? (
          <>
            <CollaborateChat
              agents={agents.map((a) => ({ id: a.id, name: a.name, role: a.role, color: getColor(a) }))}
              collaborateAgents={collaborateAgents}
              messages={collaborateMessages}
              decisions={collabDecisions}
              isLoading={isCollabLoading}
              respondingAgentId={respondingAgentId}
              onSend={sendCollaborateMessage}
              onExit={handleExitCollaborate}
              onApproveDecision={handleApproveDecision}
              onChangeDecision={handleChangeDecision}
              chatInput={collabChatInput}
              setChatInput={setCollabChatInput}
              onVoiceTranscript={handleVoiceTranscript}
            />
            {showAgentPicker && (
              <AgentPicker
                agents={agents.map((a) => ({ id: a.id, name: a.name, role: a.role, color: getColor(a) }))}
                selected={collaborateAgents}
                onToggle={handleToggleCollabAgent}
                onStart={handleStartCollaborate}
                onClose={() => setShowAgentPicker(false)}
              />
            )}
          </>
        ) : mainView === 'detail' && selected ? (
          <AgentDetailView
            agent={selected}
            messages={currentChatMessages}
            chatInput={chatInput}
            setChatInput={setChatInput}
            onSendMessage={sendAgentMessage}
            onVoiceTranscript={handleVoiceTranscript}
            isChatLoading={isChatLoading}
            onApplyConfigProposal={applyConfigProposal}
            onRejectConfigProposal={rejectConfigProposal}
            onApplyInstructionProposal={applyInstructionProposal}
            onRejectInstructionProposal={rejectInstructionProposal}
            playingMessageId={playingMessageId}
            onSpeakMessage={speakMessage}
            instructions={agentInstructions[selected.id] || []}
            configVersions={configVersions[selected.id] || []}
            logs={logs[selected.id] || []}
            showLogs={showLogs}
            onRemoveInstruction={removeInstruction}
            onRestoreVersion={restoreConfigVersion}
            onLoadLogs={handleLoadLogs}
            onShowTemplates={() => { setSelectedAgent(null); setMainView('templates'); }}
            onStartCollaborate={() => { setCollaborateAgents([selected.id]); setShowAgentPicker(true); }}
            onEdit={() => setMainView('edit')}
            chatEndRef={chatEndRef}
            chatInputRef={chatInputRef}
          />
        ) : (
          <TemplateGallery onSelectTemplate={handleSelectTemplate} onBuildCustom={() => { setCreateFromTemplate(null); setDuplicateData(null); setMainView('create'); }} />
        )}
      </main>

      {/* Agent picker overlay -- shown from detail view */}
      {showAgentPicker && mainView !== 'collaborate' && (
        <AgentPicker
          agents={agents.map((a) => ({ id: a.id, name: a.name, role: a.role, color: getColor(a) }))}
          selected={collaborateAgents}
          onToggle={handleToggleCollabAgent}
          onStart={handleStartCollaborate}
          onClose={() => setShowAgentPicker(false)}
        />
      )}
      <ConfirmMount />
    </div>
  );
}
