'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

interface Tool {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  connected: boolean;
  icon: string;
  color: string;
}

const TOOLS: Tool[] = [
  {
    id: '1',
    slug: 'github',
    name: 'GitHub',
    description: 'Sync repositories, enable AI-powered code assistance, and automate workflows',
    category: 'Development',
    connected: true,
    icon: 'GH',
    color: '#24292e',
  },
  {
    id: '2',
    slug: 'google-cloud',
    name: 'Google Cloud',
    description: 'Cloud infrastructure, storage, compute, and AI/ML services',
    category: 'Infrastructure',
    connected: false,
    icon: 'GC',
    color: '#4285F4',
  },
  {
    id: '3',
    slug: 'tavily',
    name: 'Tavily Search',
    description: 'AI-optimized web search API for real-time information retrieval',
    category: 'Search',
    connected: true,
    icon: 'TV',
    color: '#6366F1',
  },
  {
    id: '4',
    slug: 'greenapi',
    name: 'GreenAPI',
    description: 'WhatsApp messaging integration for notifications and customer communication',
    category: 'Communication',
    connected: false,
    icon: 'WA',
    color: '#25D366',
  },
  {
    id: '5',
    slug: 'elevenlabs',
    name: 'ElevenLabs',
    description: 'AI voice synthesis and text-to-speech for natural voice interactions',
    category: 'Voice & Audio',
    connected: false,
    icon: 'EL',
    color: '#000000',
  },
  {
    id: '6',
    slug: 'n8n',
    name: 'n8n / Make',
    description: 'Workflow automation and integration with hundreds of external services',
    category: 'Automation',
    connected: false,
    icon: 'N8',
    color: '#FF6D5A',
  },
  {
    id: '7',
    slug: 'vercel',
    name: 'Vercel',
    description: 'Deploy, preview, and scale frontend applications with zero configuration',
    category: 'Deployment',
    connected: true,
    icon: 'VC',
    color: '#000000',
  },
  {
    id: '8',
    slug: 'stripe',
    name: 'Stripe',
    description: 'Payment processing, subscriptions, and billing management',
    category: 'Payments',
    connected: false,
    icon: 'ST',
    color: '#635BFF',
  },
  {
    id: '9',
    slug: 'twilio',
    name: 'Twilio',
    description: 'SMS, voice calls, and programmable messaging for agent communication',
    category: 'Communication',
    connected: false,
    icon: 'TW',
    color: '#F22F46',
  },
  {
    id: '10',
    slug: 'slack',
    name: 'Slack',
    description: 'Team messaging, notifications, and interactive bot commands',
    category: 'Communication',
    connected: false,
    icon: 'SL',
    color: '#4A154B',
  },
  {
    id: '11',
    slug: 'sendgrid',
    name: 'SendGrid',
    description: 'Transactional and marketing email delivery at scale',
    category: 'Communication',
    connected: false,
    icon: 'SG',
    color: '#1A82E2',
  },
  {
    id: '13',
    slug: 'notion',
    name: 'Notion',
    description: 'Knowledge base, documentation, and structured data for agent context',
    category: 'Knowledge',
    connected: false,
    icon: 'NT',
    color: '#000000',
  },
  {
    id: '14',
    slug: 'linear',
    name: 'Linear',
    description: 'Issue tracking and project management with agent-driven workflows',
    category: 'Project Management',
    connected: false,
    icon: 'LN',
    color: '#5E6AD2',
  },
  {
    id: '15',
    slug: 'pinecone',
    name: 'Pinecone',
    description: 'Vector database for semantic search, RAG, and long-term agent memory',
    category: 'AI Infrastructure',
    connected: false,
    icon: 'PC',
    color: '#000000',
  },
  {
    id: '16',
    slug: 'browserbase',
    name: 'Browserbase',
    description: 'Headless browser infrastructure for web browsing, scraping, and interaction',
    category: 'AI Infrastructure',
    connected: false,
    icon: 'BB',
    color: '#FF6B00',
  },
  {
    id: '17',
    slug: 'e2b',
    name: 'E2B',
    description: 'Secure cloud sandboxes for AI-generated code execution',
    category: 'AI Infrastructure',
    connected: false,
    icon: 'E2',
    color: '#FF8800',
  },
  {
    id: '18',
    slug: 'langfuse',
    name: 'Langfuse',
    description: 'LLM observability — traces, evaluations, prompt management, and cost tracking',
    category: 'Observability',
    connected: false,
    icon: 'LF',
    color: '#6D28D9',
  },
  {
    id: '19',
    slug: 'firecrawl',
    name: 'Firecrawl',
    description: 'Web crawling and scraping API optimized for LLM-ready content extraction',
    category: 'AI Infrastructure',
    connected: false,
    icon: 'FC',
    color: '#FF4F00',
  },
];

export default function ToolsPage() {
  const router = useRouter();
  const [filter, setFilter] = useState<'all' | 'connected' | 'available'>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredTools = TOOLS.filter((t) => {
    if (filter === 'connected' && !t.connected) return false;
    if (filter === 'available' && t.connected) return false;
    if (searchQuery && !t.name.toLowerCase().includes(searchQuery.toLowerCase()) && !t.category.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    return true;
  });

  const handleToolClick = (tool: Tool) => {
    router.push(`/tools/${tool.slug}`);
  };

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px' }}>Tools</h1>
        <p style={{ color: 'var(--text-secondary)' }}>Connect and configure external integrations</p>
      </div>

      {/* Filters */}
      <div style={{ marginBottom: '24px', display: 'flex', gap: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '8px' }}>
          {(['all', 'connected', 'available'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: '8px 16px',
                backgroundColor: filter === f ? 'var(--accent)' : 'var(--bg-secondary)',
                color: filter === f ? 'white' : 'var(--text-primary)',
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${filter === f ? 'var(--accent)' : 'var(--border-light)'}`,
                fontWeight: 500,
                textTransform: 'capitalize',
                cursor: 'pointer',
                fontSize: '13px',
              }}
            >
              {f}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Search tools..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            padding: '8px 16px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)',
            width: '250px',
            fontSize: '13px',
            outline: 'none',
          }}
        />
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: '24px', marginBottom: '24px' }}>
        <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {TOOLS.filter((t) => t.connected).length}
          </span>{' '}
          connected
        </div>
        <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{TOOLS.length}</span> available
        </div>
      </div>

      {/* Tool Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '16px' }}>
        {filteredTools.map((tool) => (
          <div
            key={tool.id}
            onClick={() => handleToolClick(tool)}
            style={{
              padding: '20px',
              backgroundColor: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
              cursor: 'pointer',
              transition: 'border-color 0.15s, box-shadow 0.15s',
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = tool.color;
              (e.currentTarget as HTMLElement).style.boxShadow = `0 0 0 1px ${tool.color}30`;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-light)';
              (e.currentTarget as HTMLElement).style.boxShadow = 'none';
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
              <div
                style={{
                  width: '48px',
                  height: '48px',
                  backgroundColor: tool.color,
                  borderRadius: 'var(--radius-md)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '14px',
                  fontWeight: 700,
                  color: 'white',
                  flexShrink: 0,
                  letterSpacing: '-0.5px',
                }}
              >
                {tool.icon}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: '15px' }}>{tool.name}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '1px' }}>{tool.category}</div>
              </div>
              {tool.connected && (
                <div style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  backgroundColor: '#22c55e',
                  flexShrink: 0,
                  marginTop: '6px',
                }} title="Connected" />
              )}
            </div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '13px', flex: 1, margin: 0, lineHeight: 1.5 }}>
              {tool.description}
            </p>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span
                style={{
                  padding: '3px 8px',
                  backgroundColor: tool.connected ? 'rgba(34, 197, 94, 0.1)' : 'var(--bg-primary)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '11px',
                  fontWeight: 500,
                  color: tool.connected ? '#22c55e' : 'var(--text-tertiary)',
                }}
              >
                {tool.connected ? 'Connected' : 'Not connected'}
              </span>
              <span
                style={{
                  padding: '6px 12px',
                  backgroundColor: tool.connected ? 'var(--bg-primary)' : tool.color,
                  color: tool.connected ? 'var(--text-primary)' : 'white',
                  border: tool.connected ? '1px solid var(--border-light)' : 'none',
                  borderRadius: 'var(--radius-md)',
                  fontSize: '13px',
                  fontWeight: 500,
                }}
              >
                {tool.connected ? 'Configure' : 'Connect'}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
