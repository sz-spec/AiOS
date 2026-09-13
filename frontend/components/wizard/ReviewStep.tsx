'use client';

import { motion } from 'framer-motion';
import { Globe, ShoppingCart, BarChart3, Smartphone, Camera, BookOpen, Rocket, ArrowRight } from 'lucide-react';
import { useWizardStore, ProjectCategory } from '@/lib/store/wizardStore';

const categoryIcons: Record<ProjectCategory, typeof Globe> = {
  website: Globe,
  ecommerce: ShoppingCart,
  dashboard: BarChart3,
  app: Smartphone,
  portfolio: Camera,
  blog: BookOpen,
};

const categoryLabels: Record<ProjectCategory, string> = {
  website: 'Website',
  ecommerce: 'Online Store',
  dashboard: 'Dashboard',
  app: 'Web App',
  portfolio: 'Portfolio',
  blog: 'Blog',
};

interface ReviewStepProps {
  onBuild: () => void;
}

export function ReviewStep({ onBuild }: ReviewStepProps) {
  const { category, description, selectedTemplate, isBuilding } = useWizardStore();
  const Icon = category ? categoryIcons[category] : Globe;

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <h2 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 600, textAlign: 'center' }}>
        Ready to build!
      </h2>
      <p style={{ margin: '0 0 40px', fontSize: '16px', color: 'var(--text-secondary)', textAlign: 'center' }}>
        Review your project details and let AI build it for you
      </p>

      <div style={{ maxWidth: '560px', margin: '0 auto' }}>
        {/* Summary Card */}
        <div style={{
          padding: '24px',
          borderRadius: 'var(--radius-lg)',
          border: '1px solid var(--border-light)',
          backgroundColor: 'var(--bg-secondary)',
          marginBottom: '24px',
        }}>
          {/* Category */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px', paddingBottom: '20px', borderBottom: '1px solid var(--border-light)' }}>
            <div style={{
              width: '44px', height: '44px', borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-accent-light)', color: 'var(--accent)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Icon size={22} />
            </div>
            <div>
              <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>Type</div>
              <div style={{ fontWeight: 600 }}>{category ? categoryLabels[category] : 'Not selected'}</div>
            </div>
          </div>

          {/* Description */}
          <div style={{ marginBottom: '20px', paddingBottom: '20px', borderBottom: '1px solid var(--border-light)' }}>
            <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>Description</div>
            <div style={{ fontSize: '14px', lineHeight: 1.6, color: 'var(--text-primary)' }} dir="auto">
              {description || 'No description provided'}
            </div>
          </div>

          {/* Template */}
          <div>
            <div style={{ fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '6px' }}>Template</div>
            <div style={{ fontWeight: 500 }}>
              {selectedTemplate ? selectedTemplate.name : 'Building from scratch'}
            </div>
            {selectedTemplate && (
              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                {selectedTemplate.description}
              </div>
            )}
          </div>
        </div>

        {/* Build Button */}
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={onBuild}
          disabled={isBuilding}
          style={{
            width: '100%',
            padding: '18px 32px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: isBuilding ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: isBuilding ? 'var(--text-secondary)' : 'white',
            fontSize: '16px',
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '10px',
            cursor: isBuilding ? 'not-allowed' : 'pointer',
            transition: 'background-color 0.15s',
          }}
        >
          {isBuilding ? (
            <>Building your app...</>
          ) : (
            <>
              <Rocket size={20} />
              Build My App
              <ArrowRight size={18} />
            </>
          )}
        </motion.button>

        <p style={{ textAlign: 'center', fontSize: '13px', color: 'var(--text-tertiary)', marginTop: '16px' }}>
          AI will design and build your entire application. This usually takes 30-60 seconds.
        </p>
      </div>
    </motion.div>
  );
}
