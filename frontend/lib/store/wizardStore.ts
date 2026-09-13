import { create } from 'zustand';

export type ProjectCategory = 'website' | 'ecommerce' | 'dashboard' | 'app' | 'portfolio' | 'blog';

export interface WizardTemplate {
  id: string;
  name: string;
  description: string;
  category: ProjectCategory;
  thumbnail: string;
  difficulty: 'beginner' | 'intermediate' | 'advanced';
}

export interface StreamedInsight {
  phase: string;
  message: string;
  timestamp: number;
}

export type DeployTarget = 'download' | 'vos3-native' | null;
export type DeployStatus = 'idle' | 'packaging' | 'transferring' | 'launching' | 'running' | 'error';

export interface DeployProgress {
  phase: string;
  message: string;
  timestamp: number;
  progress?: number;
}

interface WizardState {
  currentStep: number;
  category: ProjectCategory | null;
  description: string;
  selectedTemplate: WizardTemplate | null;
  isBuilding: boolean;

  // Phase 3.5 — Design step
  designImage: File | null;
  designStyle: string | null;
  streamedInsights: StreamedInsight[];

  // Phase 4.0 — Deploy step
  deployTarget: DeployTarget;
  deployStatus: DeployStatus;
  deployAppId: string | null;
  deployProgress: DeployProgress[];

  setStep: (step: number) => void;
  nextStep: () => void;
  prevStep: () => void;
  setCategory: (category: ProjectCategory) => void;
  setDescription: (description: string) => void;
  setTemplate: (template: WizardTemplate | null) => void;
  setIsBuilding: (building: boolean) => void;
  setDesignImage: (file: File | null) => void;
  setDesignStyle: (style: string | null) => void;
  addInsight: (phase: string, message: string) => void;
  clearInsights: () => void;
  setDeployTarget: (target: DeployTarget) => void;
  setDeployStatus: (status: DeployStatus) => void;
  setDeployAppId: (appId: string | null) => void;
  addDeployProgress: (phase: string, message: string, progress?: number) => void;
  clearDeployProgress: () => void;
  reset: () => void;
  canProceed: () => boolean;
}

export const useWizardStore = create<WizardState>((set, get) => ({
  currentStep: 1,
  category: null,
  description: '',
  selectedTemplate: null,
  isBuilding: false,
  designImage: null,
  designStyle: null,
  streamedInsights: [],
  deployTarget: null,
  deployStatus: 'idle',
  deployAppId: null,
  deployProgress: [],

  setStep: (step) => set({ currentStep: step }),
  nextStep: () => set((s) => ({ currentStep: Math.min(s.currentStep + 1, 6) })),
  prevStep: () => set((s) => ({ currentStep: Math.max(s.currentStep - 1, 1) })),
  setCategory: (category) => set({ category }),
  setDescription: (description) => set({ description }),
  setTemplate: (template) => set({ selectedTemplate: template }),
  setIsBuilding: (building) => set({ isBuilding: building }),
  setDesignImage: (file) => set({ designImage: file, designStyle: file ? null : get().designStyle }),
  setDesignStyle: (style) => set({ designStyle: style, designImage: style ? null : get().designImage }),
  addInsight: (phase, message) => set((s) => ({
    streamedInsights: [...s.streamedInsights, { phase, message, timestamp: Date.now() }],
  })),
  clearInsights: () => set({ streamedInsights: [] }),
  setDeployTarget: (target) => set({ deployTarget: target }),
  setDeployStatus: (status) => set({ deployStatus: status }),
  setDeployAppId: (appId) => set({ deployAppId: appId }),
  addDeployProgress: (phase, message, progress) => set((s) => ({
    deployProgress: [...s.deployProgress, { phase, message, timestamp: Date.now(), progress }],
  })),
  clearDeployProgress: () => set({ deployProgress: [], deployStatus: 'idle', deployAppId: null }),
  reset: () => set({
    currentStep: 1,
    category: null,
    description: '',
    selectedTemplate: null,
    isBuilding: false,
    designImage: null,
    designStyle: null,
    streamedInsights: [],
    deployTarget: null,
    deployStatus: 'idle',
    deployAppId: null,
    deployProgress: [],
  }),
  canProceed: () => {
    const s = get();
    switch (s.currentStep) {
      case 1: return s.category !== null;
      case 2: return s.description.trim().length > 0;
      case 3: return true; // design step is optional
      case 4: return true; // template is optional
      case 5: return true;
      case 6: return true;
      default: return false;
    }
  },
}));
