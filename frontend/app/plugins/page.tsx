'use client';

import { useState } from 'react';

interface Plugin {
  id: string;
  name: string;
  description: string;
  author: string;
  category: string;
  installed: boolean;
  icon: string;
}

export default function PluginsPage() {
  const [filter, setFilter] = useState<'all' | 'installed' | 'available'>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const plugins: Plugin[] = [
    {
      id: '1',
      name: 'Slack Integration',
      description: 'Send notifications and receive commands from Slack',
      author: 'VOS3 Team',
      category: 'Communication',
      installed: true,
      icon: '💬',
    },
    {
      id: '2',
      name: 'Zapier Connect',
      description: 'Connect with 5000+ apps via Zapier',
      author: 'VOS3 Team',
      category: 'Automation',
      installed: true,
      icon: '⚡',
    },
    {
      id: '3',
      name: 'Google Drive',
      description: 'Sync files and documents with Google Drive',
      author: 'VOS3 Team',
      category: 'Storage',
      installed: false,
      icon: '📁',
    },
    {
      id: '4',
      name: 'Notion Sync',
      description: 'Two-way sync with Notion databases',
      author: 'Community',
      category: 'Productivity',
      installed: false,
      icon: '📝',
    },
    {
      id: '5',
      name: 'OpenAI GPT-4',
      description: 'Enhanced AI capabilities with GPT-4',
      author: 'VOS3 Team',
      category: 'AI',
      installed: true,
      icon: '🤖',
    },
    {
      id: '6',
      name: 'Stripe Payments',
      description: 'Accept payments directly in your workflows',
      author: 'VOS3 Team',
      category: 'Payments',
      installed: true,
      icon: '💳',
    },
    {
      id: '7',
      name: 'Twilio SMS',
      description: 'Send SMS notifications via Twilio',
      author: 'VOS3 Team',
      category: 'Communication',
      installed: false,
      icon: '📱',
    },
    {
      id: '8',
      name: 'Airtable Sync',
      description: 'Sync data with Airtable bases',
      author: 'Community',
      category: 'Productivity',
      installed: false,
      icon: '📊',
    },
  ];

  const filteredPlugins = plugins.filter((p) => {
    if (filter === 'installed' && !p.installed) return false;
    if (filter === 'available' && p.installed) return false;
    if (searchQuery && !p.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    return true;
  });

  const categories = Array.from(new Set(plugins.map((p) => p.category)));

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px' }}>Plugins</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Extend VOS3 with powerful integrations</p>
      </div>

      {/* Filters */}
      <div
        style={{ marginBottom: '24px', display: 'flex', gap: '16px', alignItems: 'center', flexWrap: 'wrap' }}
      >
        <div style={{ display: 'flex', gap: '8px' }}>
          {(['all', 'installed', 'available'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: '8px 16px',
                backgroundColor: filter === f ? 'var(--accent-primary)' : 'var(--bg-secondary)',
                color: filter === f ? 'white' : 'var(--text-primary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
                fontWeight: 500,
                textTransform: 'capitalize',
              }}
            >
              {f}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Search plugins..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            padding: '8px 16px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            width: '250px',
          }}
        />
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '24px', marginBottom: '24px' }}>
        <div style={{ color: 'var(--text-secondary)' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {plugins.filter((p) => p.installed).length}
          </span>{' '}
          installed
        </div>
        <div style={{ color: 'var(--text-secondary)' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{plugins.length}</span> available
        </div>
      </div>

      {/* Plugin Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '16px' }}>
        {filteredPlugins.map((plugin) => (
          <div
            key={plugin.id}
            style={{
              padding: '20px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
              <div
                style={{
                  width: '48px',
                  height: '48px',
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '24px',
                }}
              >
                {plugin.icon}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500 }}>{plugin.name}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>by {plugin.author}</div>
              </div>
            </div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '14px', flex: 1 }}>{plugin.description}</p>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span
                style={{
                  padding: '4px 8px',
                  backgroundColor: 'var(--bg-primary)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                }}
              >
                {plugin.category}
              </span>
              <button
                style={{
                  padding: '6px 12px',
                  backgroundColor: plugin.installed ? 'var(--bg-primary)' : 'var(--accent-primary)',
                  color: plugin.installed ? 'var(--text-primary)' : 'white',
                  border: plugin.installed ? '1px solid var(--border-light)' : 'none',
                  borderRadius: 'var(--radius-md)',
                  fontSize: '14px',
                  fontWeight: 500,
                }}
              >
                {plugin.installed ? 'Configure' : 'Install'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
