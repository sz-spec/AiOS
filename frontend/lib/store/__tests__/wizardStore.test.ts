import { describe, it, expect, beforeEach } from 'vitest';
import { useWizardStore, WizardTemplate } from '../wizardStore';


const mockTemplate: WizardTemplate = {
  id: 'tpl-1',
  name: 'Starter',
  description: 'A starter template',
  category: 'website',
  thumbnail: '/thumb.png',
  difficulty: 'beginner',
};

describe('useWizardStore', () => {
  beforeEach(() => {
    // Merge reset — preserves action functions
    useWizardStore.setState({
      currentStep: 1,
      category: null,
      description: '',
      selectedTemplate: null,
      isBuilding: false,
    });
  });

  it('nextStep increments currentStep', () => {
    useWizardStore.getState().nextStep();
    expect(useWizardStore.getState().currentStep).toBe(2);
  });

  it('nextStep clamps at max step 4', () => {
    useWizardStore.setState({ currentStep: 4 });
    useWizardStore.getState().nextStep();
    expect(useWizardStore.getState().currentStep).toBe(4);
  });

  it('prevStep decrements currentStep', () => {
    useWizardStore.setState({ currentStep: 3 });
    useWizardStore.getState().prevStep();
    expect(useWizardStore.getState().currentStep).toBe(2);
  });

  it('prevStep clamps at min step 1', () => {
    useWizardStore.getState().prevStep();
    expect(useWizardStore.getState().currentStep).toBe(1);
  });

  it('setStep sets arbitrary step', () => {
    useWizardStore.getState().setStep(3);
    expect(useWizardStore.getState().currentStep).toBe(3);
  });

  it('canProceed step 1 requires category', () => {
    useWizardStore.setState({ currentStep: 1, category: null });
    expect(useWizardStore.getState().canProceed()).toBe(false);
    useWizardStore.setState({ category: 'website' });
    expect(useWizardStore.getState().canProceed()).toBe(true);
  });

  it('canProceed step 2 requires non-empty description', () => {
    useWizardStore.setState({ currentStep: 2, description: '' });
    expect(useWizardStore.getState().canProceed()).toBe(false);
    useWizardStore.setState({ description: '  ' });
    expect(useWizardStore.getState().canProceed()).toBe(false);
    useWizardStore.setState({ description: 'My project description' });
    expect(useWizardStore.getState().canProceed()).toBe(true);
  });

  it('canProceed step 3 is always true (template optional)', () => {
    useWizardStore.setState({ currentStep: 3, selectedTemplate: null });
    expect(useWizardStore.getState().canProceed()).toBe(true);
  });

  it('canProceed step 4 is always true', () => {
    useWizardStore.setState({ currentStep: 4 });
    expect(useWizardStore.getState().canProceed()).toBe(true);
  });

  it('setTemplate updates selectedTemplate', () => {
    useWizardStore.getState().setTemplate(mockTemplate);
    expect(useWizardStore.getState().selectedTemplate?.id).toBe('tpl-1');
  });

  it('setTemplate accepts null to clear', () => {
    useWizardStore.getState().setTemplate(mockTemplate);
    useWizardStore.getState().setTemplate(null);
    expect(useWizardStore.getState().selectedTemplate).toBeNull();
  });

  it('reset returns store to initial state', () => {
    useWizardStore.setState({
      currentStep: 3,
      category: 'blog',
      description: 'Something',
      selectedTemplate: mockTemplate,
      isBuilding: true,
    });
    useWizardStore.getState().reset();
    const state = useWizardStore.getState();
    expect(state.currentStep).toBe(1);
    expect(state.category).toBeNull();
    expect(state.description).toBe('');
    expect(state.selectedTemplate).toBeNull();
    expect(state.isBuilding).toBe(false);
  });
});
