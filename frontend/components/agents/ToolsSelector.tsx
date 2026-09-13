'use client';

import React from 'react';

// ── Default capabilities every agent gets automatically ──
export interface DefaultCapability {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
}

export const DEFAULT_CAPABILITIES: DefaultCapability[] = [
  {
    id: 'email',
    name: 'Email',
    description: 'Send & receive emails',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
        <polyline points="22,6 12,13 2,6" />
      </svg>
    ),
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp',
    description: 'Messaging via WhatsApp',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      </svg>
    ),
  },
  {
    id: 'telephone',
    name: 'Telephone',
    description: 'Voice calls & SMS',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
      </svg>
    ),
  },
  {
    id: 'storage',
    name: 'Private Storage',
    description: 'Files & documents',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    id: 'vector_memory',
    name: 'Vector Memory',
    description: 'Long-term recall',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
        <line x1="10" y1="22" x2="14" y2="22" />
      </svg>
    ),
  },
  {
    id: 'database',
    name: 'Database',
    description: 'Structured data store',
    icon: (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
      </svg>
    ),
  },
];

// ── Third-party services an agent can connect to ──
export interface Service {
  id: string;
  name: string;
  provider: string;
  description: string;
  icon: React.ReactNode;
}

export interface ServiceCategory {
  label: string;
  services: Service[];
}

export const SERVICE_CATEGORIES: ServiceCategory[] = [
  {
    label: 'Search & AI',
    services: [
      {
        id: 'tavily',
        name: 'Tavily',
        provider: 'Tavily AI',
        description: 'Real-time web search and research',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        ),
      },
      {
        id: 'eleven_labs',
        name: 'ElevenLabs',
        provider: 'ElevenLabs',
        description: 'AI voice synthesis, text-to-speech, voice cloning',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Cloud & Infrastructure',
    services: [
      {
        id: 'google_cloud',
        name: 'Google Cloud',
        provider: 'Google',
        description: 'Cloud compute, storage, BigQuery, Vertex AI',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
          </svg>
        ),
      },
      {
        id: 'aws',
        name: 'AWS',
        provider: 'Amazon',
        description: 'S3, Lambda, DynamoDB, SES, and more',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Development & Code',
    services: [
      {
        id: 'github',
        name: 'GitHub',
        provider: 'GitHub',
        description: 'Repos, issues, PRs, Actions, code search',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
          </svg>
        ),
      },
      {
        id: 'vercel',
        name: 'Vercel',
        provider: 'Vercel',
        description: 'Deploy, preview, and host web applications',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 22 22 2 22" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Automation & Workflows',
    services: [
      {
        id: 'n8n',
        name: 'n8n',
        provider: 'n8n',
        description: 'Open-source workflow automation with 400+ integrations',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
        ),
      },
      {
        id: 'make',
        name: 'Make',
        provider: 'Make (Integromat)',
        description: 'Visual automation platform for complex scenarios',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        ),
      },
      {
        id: 'zapier',
        name: 'Zapier',
        provider: 'Zapier',
        description: 'Connect 6,000+ apps with no-code automations',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Communication',
    services: [
      {
        id: 'green_api',
        name: 'GreenAPI',
        provider: 'Green API',
        description: 'WhatsApp Business automation and chatbots',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
        ),
      },
      {
        id: 'twilio',
        name: 'Twilio',
        provider: 'Twilio',
        description: 'Voice calls, SMS, phone numbers, IVR',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
          </svg>
        ),
      },
      {
        id: 'sendgrid',
        name: 'SendGrid',
        provider: 'Twilio SendGrid',
        description: 'Transactional & marketing email delivery',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
            <polyline points="22,6 12,13 2,6" />
          </svg>
        ),
      },
      {
        id: 'slack',
        name: 'Slack',
        provider: 'Slack',
        description: 'Team messaging, channels, and notifications',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="13" y="2" width="3" height="8" rx="1.5" />
            <path d="M19 8.5V10h1.5A1.5 1.5 0 1 0 19 8.5" />
            <rect x="8" y="14" width="3" height="8" rx="1.5" />
            <path d="M5 15.5V14H3.5A1.5 1.5 0 1 0 5 15.5" />
            <rect x="14" y="13" width="8" height="3" rx="1.5" />
            <path d="M15.5 19H14v1.5a1.5 1.5 0 1 0 1.5-1.5" />
            <rect x="2" y="8" width="8" height="3" rx="1.5" />
            <path d="M8.5 5H10V3.5A1.5 1.5 0 1 0 8.5 5" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'CRM & Business',
    services: [
      {
        id: 'hubspot',
        name: 'HubSpot',
        provider: 'HubSpot',
        description: 'CRM, contacts, deals, marketing automation',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        ),
      },
      {
        id: 'salesforce',
        name: 'Salesforce',
        provider: 'Salesforce',
        description: 'Enterprise CRM, leads, opportunities, reports',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
          </svg>
        ),
      },
      {
        id: 'stripe',
        name: 'Stripe',
        provider: 'Stripe',
        description: 'Payments, subscriptions, invoices, billing',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="1" x2="12" y2="23" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
          </svg>
        ),
      },
    ],
  },
  {
    label: 'Productivity & Knowledge',
    services: [
      {
        id: 'notion',
        name: 'Notion',
        provider: 'Notion',
        description: 'Docs, wikis, databases, and project management',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
        ),
      },
      {
        id: 'linear',
        name: 'Linear',
        provider: 'Linear',
        description: 'Issue tracking, sprints, project management',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
          </svg>
        ),
      },
      {
        id: 'jira',
        name: 'Jira',
        provider: 'Atlassian',
        description: 'Issue tracking, agile boards, roadmaps',
        icon: (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" />
          </svg>
        ),
      },
    ],
  },
];

// Flat list of all services
export const AVAILABLE_SERVICES: Service[] = SERVICE_CATEGORIES.flatMap((cat) => cat.services);

// ── Services Selector Component ──
interface ServicesSelectorProps {
  selectedServices: string[];
  onChange: (services: string[]) => void;
  disabled?: boolean;
}

export function ServicesSelector({
  selectedServices,
  onChange,
  disabled = false,
}: ServicesSelectorProps) {
  const handleToggle = (serviceId: string) => {
    if (disabled) return;
    if (selectedServices.includes(serviceId)) {
      onChange(selectedServices.filter((id) => id !== serviceId));
    } else {
      onChange([...selectedServices, serviceId]);
    }
  };


  return (
    <div>
      {/* Default capabilities — always included */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{
          fontSize: '11px',
          fontWeight: 600,
          color: 'var(--text-tertiary)',
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          marginBottom: '8px',
        }}>
          Included by Default
        </div>
        <div style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '6px',
          padding: '12px',
          backgroundColor: 'rgba(16, 185, 129, 0.04)',
          border: '1px solid rgba(16, 185, 129, 0.15)',
          borderRadius: 'var(--radius-md)',
        }}>
          {DEFAULT_CAPABILITIES.map((cap) => (
            <div
              key={cap.id}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '5px',
                padding: '4px 10px',
                backgroundColor: 'rgba(16, 185, 129, 0.08)',
                borderRadius: 'var(--radius-full)',
                fontSize: '11px',
                fontWeight: 500,
                color: '#059669',
              }}
              title={cap.description}
            >
              <span style={{ display: 'flex', color: '#059669' }}>{cap.icon}</span>
              {cap.name}
            </div>
          ))}
        </div>
        <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-tertiary)' }}>
          Every agent automatically gets these capabilities
        </div>
      </div>

      {/* Service selection */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {SERVICE_CATEGORIES.map((category) => (
          <div key={category.label}>
            <div style={{
              fontSize: '11px',
              fontWeight: 600,
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              marginBottom: '8px',
            }}>
              {category.label}
            </div>
            <div style={{
              display: 'grid',
              gridTemplateColumns: `repeat(${Math.min(category.services.length, 4)}, 1fr)`,
              gap: '8px',
            }}>
              {category.services.map((service) => {
                const isSelected = selectedServices.includes(service.id);
                return (
                  <button
                    key={service.id}
                    type="button"
                    onClick={() => handleToggle(service.id)}
                    disabled={disabled}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      gap: '4px',
                      padding: '14px 8px 12px',
                      backgroundColor: isSelected ? 'rgba(217, 119, 6, 0.08)' : 'var(--bg-secondary)',
                      border: `1px solid ${isSelected ? 'var(--accent)' : 'var(--border-light)'}`,
                      borderRadius: 'var(--radius-md)',
                      textAlign: 'center',
                      cursor: disabled ? 'not-allowed' : 'pointer',
                      opacity: disabled ? 0.5 : 1,
                      transition: 'all 0.15s ease',
                      position: 'relative',
                    }}
                  >
                    {isSelected && (
                      <div style={{
                        position: 'absolute', top: '6px', right: '6px',
                        width: '16px', height: '16px', borderRadius: '50%',
                        backgroundColor: 'var(--accent)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      </div>
                    )}
                    <span style={{ color: isSelected ? 'var(--accent)' : 'var(--text-secondary)' }}>
                      {service.icon}
                    </span>
                    <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)', lineHeight: 1.3 }}>
                      {service.name}
                    </span>
                    <span style={{ fontSize: '10px', color: isSelected ? 'var(--accent)' : 'var(--text-tertiary)', opacity: 0.8 }}>
                      {service.provider}
                    </span>
                    <span style={{ fontSize: '11px', color: 'var(--text-tertiary)', lineHeight: 1.3, marginTop: '2px' }}>
                      {service.description}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Backward compat exports
export { ServicesSelector as ToolsSelector };
export type Tool = Service;
export type ToolCategory = ServiceCategory;
export const TOOL_CATEGORIES = SERVICE_CATEGORIES;
export const AVAILABLE_TOOLS = AVAILABLE_SERVICES;

export default ServicesSelector;
