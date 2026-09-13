'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { Check, Layers } from 'lucide-react';
import { useWizardStore, WizardTemplate, ProjectCategory } from '@/lib/store/wizardStore';

const templates: WizardTemplate[] = [
  // Website
  { id: 'startup-landing', name: 'Startup Landing', description: 'Modern landing page with hero, features, pricing, and CTA', category: 'website', thumbnail: '', difficulty: 'beginner' },
  { id: 'agency-site', name: 'Agency Website', description: 'Multi-page agency site with services, portfolio, team, and contact', category: 'website', thumbnail: '', difficulty: 'intermediate' },
  { id: 'restaurant', name: 'Restaurant', description: 'Restaurant with menu, hours, reservations, and photo gallery', category: 'website', thumbnail: '', difficulty: 'beginner' },
  // E-commerce
  { id: 'product-store', name: 'Product Store', description: 'Full e-commerce with catalog, cart, checkout, and account', category: 'ecommerce', thumbnail: '', difficulty: 'advanced' },
  { id: 'digital-downloads', name: 'Digital Products', description: 'Sell digital files with instant download delivery', category: 'ecommerce', thumbnail: '', difficulty: 'intermediate' },
  // Dashboard
  { id: 'analytics-dash', name: 'Analytics Dashboard', description: 'Data visualization with charts, KPIs, and filters', category: 'dashboard', thumbnail: '', difficulty: 'intermediate' },
  { id: 'admin-panel', name: 'Admin Panel', description: 'Content management with users, tables, and settings', category: 'dashboard', thumbnail: '', difficulty: 'advanced' },
  // App
  { id: 'task-manager', name: 'Task Manager', description: 'Kanban boards with drag-and-drop, labels, and due dates', category: 'app', thumbnail: '', difficulty: 'intermediate' },
  { id: 'chat-app', name: 'Chat Application', description: 'Real-time messaging with channels and direct messages', category: 'app', thumbnail: '', difficulty: 'advanced' },
  // Portfolio
  { id: 'minimal-portfolio', name: 'Minimal Portfolio', description: 'Clean portfolio with project grid and detail pages', category: 'portfolio', thumbnail: '', difficulty: 'beginner' },
  { id: 'creative-portfolio', name: 'Creative Portfolio', description: 'Animated portfolio with case studies and transitions', category: 'portfolio', thumbnail: '', difficulty: 'intermediate' },
  // Blog
  { id: 'tech-blog', name: 'Tech Blog', description: 'Blog with categories, search, reading time, and code snippets', category: 'blog', thumbnail: '', difficulty: 'beginner' },
  { id: 'magazine', name: 'Magazine', description: 'Content-rich layout with featured articles and sections', category: 'blog', thumbnail: '', difficulty: 'intermediate' },
];

const tabs: { id: ProjectCategory | 'all'; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'website', label: 'Website' },
  { id: 'ecommerce', label: 'E-Commerce' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'app', label: 'Web App' },
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'blog', label: 'Blog' },
];

const difficultyColors: Record<string, string> = {
  beginner: '#059669',
  intermediate: '#d97706',
  advanced: '#dc2626',
};

export function TemplateGallery() {
  const { selectedTemplate, setTemplate, category } = useWizardStore();
  const [activeTab, setActiveTab] = useState<ProjectCategory | 'all'>(category || 'all');

  const filtered = activeTab === 'all' ? templates : templates.filter((t) => t.category === activeTab);

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <h2 style={{ margin: '0 0 8px', fontSize: '28px', fontWeight: 600, textAlign: 'center' }}>
        Choose a starting template
      </h2>
      <p style={{ margin: '0 0 32px', fontSize: '16px', color: 'var(--text-secondary)', textAlign: 'center' }}>
        Pick a template or build from scratch — you can customize everything later
      </p>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '4px', justifyContent: 'center', marginBottom: '24px', flexWrap: 'wrap' }}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              padding: '8px 16px',
              borderRadius: 'var(--radius-full)',
              fontSize: '13px',
              fontWeight: 500,
              backgroundColor: activeTab === tab.id ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: activeTab === tab.id ? 'white' : 'var(--text-secondary)',
              transition: 'all 0.15s',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Template Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '16px', maxWidth: '960px', margin: '0 auto' }}>
        {/* Build from scratch card */}
        <motion.button
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          onClick={() => setTemplate(null)}
          style={{
            padding: '20px',
            borderRadius: 'var(--radius-md)',
            border: `2px dashed ${!selectedTemplate ? 'var(--accent)' : 'var(--border-light)'}`,
            backgroundColor: !selectedTemplate ? 'var(--bg-accent-light)' : 'var(--bg-secondary)',
            cursor: 'pointer',
            textAlign: 'center',
            minHeight: '180px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '12px',
          }}
        >
          <Layers size={32} style={{ color: !selectedTemplate ? 'var(--accent)' : 'var(--text-tertiary)' }} />
          <div>
            <div style={{ fontWeight: 600, fontSize: '14px' }}>Build from Scratch</div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>AI generates everything based on your description</div>
          </div>
        </motion.button>

        {filtered.map((template, i) => {
          const isSelected = selectedTemplate?.id === template.id;
          return (
            <motion.button
              key={template.id}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: (i + 1) * 0.03 }}
              onClick={() => setTemplate(template)}
              style={{
                padding: '0',
                borderRadius: 'var(--radius-md)',
                border: `2px solid ${isSelected ? 'var(--accent)' : 'var(--border-light)'}`,
                backgroundColor: 'var(--bg-secondary)',
                cursor: 'pointer',
                textAlign: 'left',
                overflow: 'hidden',
                position: 'relative',
              }}
            >
              {/* Thumbnail placeholder */}
              <div
                style={{
                  height: '120px',
                  background: `linear-gradient(135deg, ${isSelected ? 'var(--bg-accent-light)' : 'var(--bg-tertiary)'} 0%, ${isSelected ? '#fde68a' : 'var(--bg-hover)'} 100%)`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {isSelected && (
                  <div style={{
                    width: '32px', height: '32px', borderRadius: '50%',
                    backgroundColor: 'var(--accent)', color: 'white',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <Check size={18} />
                  </div>
                )}
              </div>

              <div style={{ padding: '14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <div style={{ fontWeight: 600, fontSize: '14px' }}>{template.name}</div>
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    color: difficultyColors[template.difficulty],
                    backgroundColor: `${difficultyColors[template.difficulty]}15`,
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-full)',
                  }}>
                    {template.difficulty}
                  </span>
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                  {template.description}
                </div>
              </div>
            </motion.button>
          );
        })}
      </div>
    </motion.div>
  );
}
