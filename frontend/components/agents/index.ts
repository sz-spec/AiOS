export { AgentForm } from './AgentForm';
export type { AgentFormData } from './AgentForm';
export { AgentEditModal } from './AgentEditModal';
export { CustomAgentBuilder } from './CustomAgentBuilder';
export { TemperatureSlider } from './TemperatureSlider';
export { AgentAvatar } from './AgentAvatar';
export { TemplateGallery } from './TemplateGallery';
export { AGENT_TEMPLATES, AGENT_CATEGORIES, getTemplatesByCategory, searchTemplates } from './templates';
export type { AgentTemplate, AgentCategory, RouterRole } from './templates';
export { CollaborateChat } from './CollaborateChat';
export type { CollabMessage, CollabDecision } from './CollaborateChat';
export { AgentPicker } from './AgentPicker';

// New extracted components
export { AgentSidebar } from './AgentSidebar';
export { AgentChatPanel } from './AgentChatPanel';
export { AgentConfigSidebar } from './AgentConfigSidebar';
export { AgentDetailView } from './AgentDetailView';
export { TemplateChatView } from './TemplateChatView';

// Shared types and utilities
export type {
  Agent,
  AgentLog,
  ChatMessage,
  ConfigProposal,
  InstructionProposal,
  ConfigVersion,
  MainView,
} from './types';
export {
  detectDir,
  ROUTER_ENGINE,
  RACI_MAP,
  RACI_COLORS,
  ROLE_INFO,
  getAgentColor,
  iconBtnStyle,
  getTimeAgo,
  EditIcon,
  DeleteIcon,
  CopyIcon,
} from './types';
