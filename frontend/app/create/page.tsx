'use client';

import { useRouter } from 'next/navigation';
import { useAuth } from '@clerk/nextjs';
import { motion, AnimatePresence } from 'framer-motion';
import { ArrowLeft, ArrowRight, ChevronLeft } from 'lucide-react';
import { useWizardStore } from '@/lib/store/wizardStore';
import { StepSelector } from '@/components/wizard/StepSelector';
import { DescriptionStep } from '@/components/wizard/DescriptionStep';
import { DesignStep } from '@/components/wizard/DesignStep';
import { TemplateGallery } from '@/components/wizard/TemplateGallery';
import { ReviewStep } from '@/components/wizard/ReviewStep';
import { DeployStep } from '@/components/wizard/DeployStep';
import Link from 'next/link';

const stepLabels = ['Category', 'Description', 'Design', 'Template', 'Review', 'Deploy'];

export default function CreatePage() {
  const router = useRouter();
  const {
    currentStep, nextStep, prevStep, canProceed, setIsBuilding,
    category, description, selectedTemplate, designImage, designStyle, reset,
  } = useWizardStore();
  const { getToken } = useAuth();

  const handleBuild = async () => {
    setIsBuilding(true);
    try {
      // F-M2: Include auth token in API calls
      const token = await getToken();
      const authHeaders: Record<string, string> = token
        ? { 'Authorization': `Bearer ${token}` }
        : {};

      // Dual-path: if design image or style is present, include in FormData
      if (designImage || designStyle) {
        const form = new FormData();
        form.append('category', category || '');
        form.append('description', description);
        if (selectedTemplate?.id) form.append('template_id', selectedTemplate.id);
        if (designImage) form.append('design_image', designImage);
        if (designStyle) form.append('design_style', designStyle);

        const res = await fetch('/api/v1/projects/wizard', {
          method: 'POST',
          headers: authHeaders,
          body: form,
        });
        const data = await res.json();
        if (data.project_id) {
          reset();
          router.push(`/projects/${data.project_id}`);
        }
      } else {
        const res = await fetch('/api/v1/projects/wizard', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          body: JSON.stringify({
            category,
            description,
            template_id: selectedTemplate?.id || null,
          }),
        });
        const data = await res.json();
        if (data.project_id) {
          reset();
          router.push(`/projects/${data.project_id}`);
        }
      }
    } catch (err) {
      // F-M1: Log errors instead of silently swallowing
      console.error("Build failed:", err);
      setIsBuilding(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      {/* Top Bar */}
      <div style={{
        padding: '16px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid var(--border-light)',
      }}>
        <Link
          href="/projects"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '14px',
            color: 'var(--text-secondary)',
          }}
        >
          <ChevronLeft size={18} />
          Back to Projects
        </Link>

        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          fontSize: '13px',
          fontWeight: 500,
        }}>
          {stepLabels.map((label, i) => {
            const step = i + 1;
            const isActive = currentStep === step;
            const isCompleted = currentStep > step;
            return (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {i > 0 && (
                  <div style={{
                    width: '24px',
                    height: '2px',
                    backgroundColor: isCompleted ? 'var(--accent)' : 'var(--border-light)',
                    borderRadius: '1px',
                  }} />
                )}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  color: isActive ? 'var(--accent)' : isCompleted ? 'var(--success)' : 'var(--text-tertiary)',
                }}>
                  <div style={{
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    fontSize: '12px',
                    fontWeight: 600,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: isActive ? 'var(--accent)' : isCompleted ? 'var(--success)' : 'var(--bg-tertiary)',
                    color: (isActive || isCompleted) ? 'white' : 'var(--text-tertiary)',
                  }}>
                    {isCompleted ? '\u2713' : step}
                  </div>
                  <span style={{ display: 'inline' }}>{label}</span>
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ width: '140px' }} /> {/* spacer */}
      </div>

      {/* Content */}
      <div style={{ padding: '48px 24px', maxWidth: '1200px', margin: '0 auto' }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={currentStep}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
          >
            {currentStep === 1 && <StepSelector />}
            {currentStep === 2 && <DescriptionStep />}
            {currentStep === 3 && <DesignStep />}
            {currentStep === 4 && <TemplateGallery />}
            {currentStep === 5 && <ReviewStep onBuild={handleBuild} />}
            {currentStep === 6 && <DeployStep projectId={undefined} />}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Bottom Navigation */}
      {currentStep < 5 && currentStep < 6 && (
        <div style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          padding: '16px 24px',
          backgroundColor: 'var(--bg-primary)',
          borderTop: '1px solid var(--border-light)',
          display: 'flex',
          justifyContent: 'center',
          gap: '12px',
        }}>
          {currentStep > 1 && (
            <button
              onClick={prevStep}
              style={{
                padding: '12px 24px',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
                backgroundColor: 'var(--bg-primary)',
                fontSize: '14px',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: 'var(--text-primary)',
              }}
            >
              <ArrowLeft size={16} />
              Back
            </button>
          )}
          <button
            onClick={nextStep}
            disabled={!canProceed()}
            style={{
              padding: '12px 32px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: canProceed() ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: canProceed() ? 'white' : 'var(--text-tertiary)',
              fontSize: '14px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              cursor: canProceed() ? 'pointer' : 'not-allowed',
              transition: 'all 0.15s',
            }}
          >
            Next
            <ArrowRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
