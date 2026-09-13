'use client';

import { motion } from 'framer-motion';
import { Globe, ShoppingCart, BarChart3, Smartphone, Camera, BookOpen } from 'lucide-react';
import { useWizardStore, ProjectCategory } from '@/lib/store/wizardStore';

const categories: { id: ProjectCategory; label: string; icon: typeof Globe; description: string }[] = [
  { id: 'website', label: 'Website', icon: Globe, description: 'Business site, landing page, or personal site' },
  { id: 'ecommerce', label: 'Online Store', icon: ShoppingCart, description: 'Sell products or services online' },
  { id: 'dashboard', label: 'Dashboard', icon: BarChart3, description: 'Analytics, admin panel, or data visualization' },
  { id: 'app', label: 'Web App', icon: Smartphone, description: 'Interactive application with features' },
  { id: 'portfolio', label: 'Portfolio', icon: Camera, description: 'Showcase your work and projects' },
  { id: 'blog', label: 'Blog', icon: BookOpen, description: 'Articles, news, or content platform' },
];

export function StepSelector() {
  const { category, setCategory } = useWizardStore();

  return (
    <div>
      <h2 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 600, textAlign: 'center' }}>
        What do you want to build?
      </h2>
      <p style={{ margin: '0 0 40px', fontSize: '16px', color: 'var(--text-secondary)', textAlign: 'center' }}>
        Choose a category to get started
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px', maxWidth: '720px', margin: '0 auto' }}>
        {categories.map((cat, i) => {
          const Icon = cat.icon;
          const isSelected = category === cat.id;
          return (
            <motion.button
              key={cat.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
              onClick={() => setCategory(cat.id)}
              style={{
                padding: '24px 20px',
                borderRadius: 'var(--radius-lg)',
                border: `2px solid ${isSelected ? 'var(--accent)' : 'var(--border-light)'}`,
                backgroundColor: isSelected ? 'var(--bg-accent-light)' : 'var(--bg-secondary)',
                cursor: 'pointer',
                textAlign: 'center',
                transition: 'all 0.15s',
              }}
            >
              <div
                style={{
                  width: '48px',
                  height: '48px',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: isSelected ? 'var(--accent)' : 'var(--bg-tertiary)',
                  color: isSelected ? 'white' : 'var(--text-secondary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  margin: '0 auto 12px',
                  transition: 'all 0.15s',
                }}
              >
                <Icon size={24} />
              </div>
              <div style={{ fontWeight: 600, fontSize: '15px', marginBottom: '4px' }}>{cat.label}</div>
              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{cat.description}</div>
            </motion.button>
          );
        })}
      </div>
    </div>
  );
}
