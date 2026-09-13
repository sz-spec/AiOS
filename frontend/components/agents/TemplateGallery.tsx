'use client';

import React, { useState, useMemo } from 'react';
import {
  AgentTemplate,
  AgentCategory,
  AGENT_CATEGORIES,
  AGENT_TEMPLATES,
  getTemplatesByCategory,
  searchTemplates,
} from './templates';
import { AgentAvatar } from './AgentAvatar';

interface TemplateGalleryProps {
  onSelectTemplate: (template: AgentTemplate) => void;
  onBuildCustom?: () => void;
}

export function TemplateGallery({ onSelectTemplate, onBuildCustom }: TemplateGalleryProps) {
  const [search, setSearch] = useState('');

  const filteredByCategory = useMemo(() => {
    const filtered = searchTemplates(search);
    const grouped: Record<AgentCategory, AgentTemplate[]> = {
      leadership: [],
      creative: [],
      design: [],
      development: [],
      quality: [],
      operations: [],
    };
    for (const t of filtered) {
      grouped[t.category].push(t);
    }
    return grouped;
  }, [search]);

  const categoryOrder: AgentCategory[] = ['leadership', 'creative', 'design', 'development', 'quality', 'operations'];

  return (
    <div style={{ padding: '24px', overflow: 'auto', height: '100%' }}>
      <div style={{ maxWidth: '960px', margin: '0 auto' }}>
        <div style={{ marginBottom: '24px' }}>
          <h2 style={{ margin: '0 0 4px', fontSize: '20px', fontWeight: 600 }}>Agent Resources</h2>
          <p style={{ margin: '0 0 16px', fontSize: '14px', color: 'var(--text-secondary)' }}>
            Pre-built agents for every stage of the development lifecycle. Pick a template, configure its model and tools, then give it tasks to execute.
          </p>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search resources by name, category, or tag..."
            style={{
              width: '100%',
              maxWidth: '400px',
              padding: '10px 14px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '14px',
              outline: 'none',
            }}
          />

          {/* Quick peek — dropdown sorted A-Z */}
          <select
            defaultValue=""
            onChange={(e) => {
              const tmpl = AGENT_TEMPLATES.find((t) => t.id === e.target.value);
              if (tmpl) onSelectTemplate(tmpl);
              e.target.value = '';
            }}
            style={{
              marginTop: '10px',
              padding: '8px 12px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              outline: 'none',
              cursor: 'pointer',
              maxWidth: '300px',
            }}
          >
            <option value="" disabled>Quick jump to resource...</option>
            {[...AGENT_TEMPLATES].sort((a, b) => a.name.localeCompare(b.name)).map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>

        {/* Build Custom card */}
        {onBuildCustom && (
          <>
            <button
              onClick={onBuildCustom}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '14px',
                width: '100%',
                padding: '16px 20px',
                marginBottom: '20px',
                backgroundColor: 'var(--bg-secondary)',
                border: '2px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'border-color 0.15s',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = '#d97706'; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)'; }}
            >
              <div style={{
                width: '44px',
                height: '44px',
                borderRadius: 'var(--radius-md)',
                background: 'linear-gradient(135deg, #d97706, #b45309)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
              }}>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </div>
              <div>
                <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '2px' }}>
                  Custom Resource
                </div>
                <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                  Design your own agent
                </div>
              </div>
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', margin: '0 0 16px' }}>
              <div style={{ flex: 1, height: '1px', backgroundColor: 'var(--border-light)' }} />
              <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontWeight: 500 }}>OR USE A RESOURCE</span>
              <div style={{ flex: 1, height: '1px', backgroundColor: 'var(--border-light)' }} />
            </div>
          </>
        )}

        {categoryOrder.map((cat) => {
          const templates = filteredByCategory[cat];
          if (templates.length === 0) return null;
          const catInfo = AGENT_CATEGORIES[cat];

          return (
            <div key={cat} style={{ marginBottom: '32px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <div
                  style={{
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    backgroundColor: catInfo.color,
                  }}
                />
                <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>{catInfo.label}</h3>
                <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                  {catInfo.description}
                </span>
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                  gap: '12px',
                }}
              >
                {templates.map((template) => (
                  <TemplateCard
                    key={template.id}
                    template={template}
                    onSelect={() => onSelectTemplate(template)}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {categoryOrder.every((cat) => filteredByCategory[cat].length === 0) && (
          <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
            No templates match &quot;{search}&quot;
          </div>
        )}
      </div>
    </div>
  );
}

function TemplateCard({
  template,
  onSelect,
}: {
  template: AgentTemplate;
  onSelect: () => void;
}) {
  const catInfo = AGENT_CATEGORIES[template.category];

  return (
    <div
      style={{
        padding: '16px',
        backgroundColor: 'var(--bg-secondary)',
        border: '1px solid var(--border-light)',
        borderRadius: 'var(--radius-md)',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        transition: 'border-color 0.15s ease',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = template.color;
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)';
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <AgentAvatar name={template.name} color={template.color} size="md" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: '14px' }}>{template.name}</div>
          <div
            style={{
              display: 'inline-block',
              padding: '1px 6px',
              borderRadius: 'var(--radius-full)',
              fontSize: '10px',
              fontWeight: 500,
              backgroundColor: `${catInfo.color}18`,
              color: catInfo.color,
            }}
          >
            {catInfo.label}
          </div>
        </div>
      </div>

      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
        {template.description}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
        {template.tags.slice(0, 4).map((tag) => (
          <span
            key={tag}
            style={{
              padding: '2px 6px',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: 'var(--radius-full)',
              fontSize: '11px',
              color: 'var(--text-tertiary)',
            }}
          >
            {tag}
          </span>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 'auto' }}>
        <span style={{ fontSize: '11px', color: 'var(--text-tertiary)' }}>
          {template.defaults.model} &bull; T={template.defaults.temperature}
        </span>
        <button
          onClick={onSelect}
          style={{
            padding: '6px 14px',
            backgroundColor: template.color,
            border: 'none',
            borderRadius: 'var(--radius-md)',
            color: 'white',
            fontSize: '12px',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Use Resource
        </button>
      </div>
    </div>
  );
}

export default TemplateGallery;
