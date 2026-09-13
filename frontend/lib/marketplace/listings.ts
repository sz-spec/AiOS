/**
 * Adapter that projects in-tree data into the `MarketplaceListing` shape
 * the marketplace UI consumes. Today this maps the static
 * `AGENT_TEMPLATES` (29 entries) onto listings; later it can also pull
 * agents/apps from Convex or the backend without touching the UI.
 *
 * Platform-tag assignment lives here (not on the template itself) so we
 * can update the taxonomy without touching the 1,380-line template
 * source. Tags are best-effort heuristic — refine as the catalog grows.
 */

import { AGENT_TEMPLATES, type AgentTemplate } from '@/components/agents/templates';
import type { MarketplaceListing, VerticalId } from './verticals';

/**
 * Pseudo-deterministic install-count for visual variety. Replace with the
 * real metric once the platform tracks installs in Convex.
 */
function fakeInstalls(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0;
  return 100 + (Math.abs(h) % 9900);
}

function fakeRating(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 17 + seed.charCodeAt(i)) | 0;
  return 3.6 + ((Math.abs(h) % 14) / 10); // 3.6 → 4.9
}

/**
 * Deterministic ISO timestamp in the 1–60 days before the catalog
 * reference date. Same hash-based approach as the install/rating
 * generators — stable across reloads, varied across listings.
 *
 * Replace with each row's real `_creationTime` once listings move
 * to Convex.
 */
const CATALOG_REFERENCE_ISO = '2026-05-06T00:00:00.000Z';
const CATALOG_REFERENCE_MS = Date.parse(CATALOG_REFERENCE_ISO);
const DAY_MS = 86_400_000;

function fakeCreatedAt(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 13 + seed.charCodeAt(i)) | 0;
  const daysAgo = 1 + (Math.abs(h) % 60); // 1 → 60 days ago
  return new Date(CATALOG_REFERENCE_MS - daysAgo * DAY_MS).toISOString();
}

/**
 * Per-template platform-tag overrides. Anything not listed here falls back
 * to the category-based default in `defaultTagsFor()`.
 */
const TAG_OVERRIDES: Record<string, VerticalId[]> = {
  'tmpl-chief-of-staff': ['digital-workforce', 'hr-employees'],
  'tmpl-product-manager': ['digital-workforce'],
  'tmpl-scrum-master': ['digital-workforce', 'developer-tools'],
  'tmpl-dev-lead': ['digital-workforce', 'developer-tools'],
  'tmpl-customer-success': ['customer-success'],
  'tmpl-image-generation': ['creative-studio'],
  'tmpl-copywriter': ['creative-studio', 'sales-marketing'],
  'tmpl-technical-writer': ['creative-studio', 'developer-tools'],
  'tmpl-architect': ['developer-tools', 'digital-workforce'],
  'tmpl-database-architect': ['developer-tools'],
  'tmpl-ux-designer': ['creative-studio'],
  'tmpl-ui-designer': ['creative-studio'],
  'tmpl-market-analyst': ['research-analytics', 'sales-marketing'],
  'tmpl-business-analyst': ['research-analytics', 'finance-ops'],
  'tmpl-frontend-dev': ['developer-tools'],
  'tmpl-backend-dev': ['developer-tools'],
  'tmpl-devops-engineer': ['developer-tools', 'industrial-automation'],
  'tmpl-code-reviewer': ['developer-tools'],
  'tmpl-security-reviewer': ['developer-tools', 'legal-compliance'],
  'tmpl-qa-engineer': ['developer-tools'],
  'tmpl-performance-engineer': ['developer-tools', 'industrial-automation'],
  'tmpl-legal-compliance': ['legal-compliance'],
  'tmpl-release-manager': ['developer-tools'],
  'tmpl-deployment-manager': ['developer-tools', 'industrial-automation'],
  'tmpl-monitoring-agent': ['developer-tools', 'industrial-automation'],
  'tmpl-maintenance-agent': ['developer-tools', 'industrial-automation'],
  'tmpl-it-operations': ['developer-tools', 'hr-employees'],
  'tmpl-finance-controller': ['finance-ops'],
};

function defaultTagsFor(template: AgentTemplate): VerticalId[] {
  switch (template.category) {
    case 'leadership':
      return ['digital-workforce'];
    case 'creative':
      return ['creative-studio'];
    case 'design':
      return ['creative-studio', 'developer-tools'];
    case 'development':
      return ['developer-tools'];
    case 'quality':
      return ['developer-tools'];
    case 'operations':
      return ['developer-tools', 'industrial-automation'];
    default:
      return ['digital-workforce'];
  }
}

export function listingsFromAgentTemplates(): MarketplaceListing[] {
  return AGENT_TEMPLATES.map((t) => {
    const platform_tags = TAG_OVERRIDES[t.id] ?? defaultTagsFor(t);
    return {
      id: t.id,
      kind: 'agent' as const,
      name: t.name,
      description: t.description,
      icon: t.avatar,
      accent: t.color,
      platform_tags,
      category: t.category,
      pricing: 'free' as const,
      rating: Math.round(fakeRating(t.id) * 10) / 10,
      installs: fakeInstalls(t.id),
      author: 'VOS3 Foundation',
      verified: true,
      tags: t.tags,
      href: `/agents?template=${encodeURIComponent(t.id)}`,
      created_at: fakeCreatedAt(t.id),
    } satisfies MarketplaceListing;
  });
}

/**
 * Demo seed listings that exercise verticals not yet covered by the
 * static template catalog. Keeps the UI honest about its multi-platform
 * scope until backend listings arrive.
 */
export const SEED_LISTINGS: MarketplaceListing[] = [
  {
    id: 'seed-shop-floor-supervisor',
    kind: 'agent',
    name: 'Shop Floor Supervisor',
    description: 'Watches OEE, flags downtime patterns, and coordinates maintenance schedules across PLC fleets.',
    icon: '🛠️',
    accent: '#ea580c',
    platform_tags: ['industrial-automation'],
    category: 'operations',
    pricing: 'paid',
    price: 49,
    rating: 4.7,
    installs: 1240,
    author: 'Linde Foundry Labs',
    verified: true,
    tags: ['oee', 'downtime', 'maintenance', 'plc'],
    href: '/agents?seed=shop-floor-supervisor',
    created_at: '2026-03-18T10:00:00.000Z',
    router_role: 'reviewer',
  },
  {
    id: 'seed-recruiter',
    kind: 'agent',
    name: 'Talent Recruiter',
    description: 'Screens applicants against job specs, drafts outreach, and books interviews on your calendar.',
    icon: '🎯',
    accent: '#0891b2',
    platform_tags: ['hr-employees'],
    category: 'leadership',
    pricing: 'freemium',
    rating: 4.6,
    installs: 8400,
    author: 'PeopleOps Co.',
    verified: true,
    tags: ['recruiting', 'screening', 'outreach'],
    href: '/agents?seed=recruiter',
    created_at: '2026-02-09T10:00:00.000Z',
    router_role: 'researcher',
  },
  {
    id: 'seed-brand-stylist',
    kind: 'agent',
    name: 'Brand Stylist',
    description: 'Generates on-brand copy, color palettes, and social-asset variants from a single brief.',
    icon: '🖼️',
    accent: '#db2777',
    platform_tags: ['creative-studio', 'sales-marketing'],
    category: 'creative',
    pricing: 'paid',
    price: 29,
    rating: 4.8,
    installs: 5210,
    author: 'Atelier 11',
    verified: false,
    tags: ['branding', 'social', 'copy'],
    href: '/agents?seed=brand-stylist',
    created_at: '2026-04-02T10:00:00.000Z',
    router_role: 'coding',
  },
  {
    id: 'seed-fpa-analyst',
    kind: 'agent',
    name: 'FP&A Analyst',
    description: 'Produces variance reports, cash-flow forecasts, and board decks from your accounting data.',
    icon: '📊',
    accent: '#16a34a',
    platform_tags: ['finance-ops', 'research-analytics'],
    category: 'leadership',
    pricing: 'paid',
    price: 79,
    rating: 4.5,
    installs: 920,
    author: 'Ledgerline',
    verified: true,
    tags: ['fp&a', 'forecast', 'reporting'],
    href: '/agents?seed=fpa-analyst',
    created_at: '2026-01-22T10:00:00.000Z',
    router_role: 'reviewer',
  },
  {
    id: 'seed-csm',
    kind: 'agent',
    name: 'Customer Health Watcher',
    description: 'Surfaces churn-risk accounts, drafts renewal-ready talking points, and pings your CSM.',
    icon: '💚',
    accent: '#f59e0b',
    platform_tags: ['customer-success'],
    category: 'operations',
    pricing: 'freemium',
    rating: 4.4,
    installs: 3100,
    author: 'Retention.ai',
    verified: true,
    tags: ['churn', 'health-score', 'renewal'],
    href: '/agents?seed=csm',
    created_at: '2026-02-25T10:00:00.000Z',
    router_role: 'researcher',
  },
  {
    id: 'seed-contract-reviewer',
    kind: 'agent',
    name: 'Contract Reviewer',
    description: 'Redlines NDAs, MSAs, and DPAs against your playbook; flags risky clauses and missing terms.',
    icon: '📑',
    accent: '#475569',
    platform_tags: ['legal-compliance'],
    category: 'quality',
    pricing: 'paid',
    price: 99,
    rating: 4.9,
    installs: 410,
    author: 'BlackLetter Legal',
    verified: true,
    tags: ['contracts', 'redlining', 'compliance'],
    href: '/agents?seed=contract-reviewer',
    created_at: '2026-03-30T10:00:00.000Z',
    router_role: 'reviewer',
  },

  // ── HR & Employees ──────────────────────────────────────────────────
  {
    id: 'seed-onboarding-specialist',
    kind: 'agent',
    name: 'Onboarding Specialist',
    description:
      'Guides new hires through company-wide onboarding: paperwork, training plans, and 30/60/90-day check-ins.',
    icon: '🎓',
    accent: '#0891b2',
    platform_tags: ['hr-employees'],
    category: 'operations',
    pricing: 'free',
    rating: 4.7,
    installs: 6200,
    author: 'PeopleOps Co.',
    verified: true,
    tags: ['onboarding', 'training', 'corporate-training', 'employee-experience'],
    href: '/agents?seed=onboarding-specialist',
    created_at: '2026-05-06T08:00:00.000Z',
    role: 'Specialist',
    system_prompt: 'Expert in corporate training',
    router_role: 'researcher',
  },
  {
    id: 'seed-performance-analyst',
    kind: 'agent',
    name: 'Performance Analyst',
    description:
      'Analyzes employee KPIs, surfaces trend deltas, and drafts review-ready summaries for managers.',
    icon: '📈',
    accent: '#0891b2',
    platform_tags: ['hr-employees'],
    category: 'quality',
    pricing: 'paid',
    price: 39,
    rating: 4.5,
    installs: 2150,
    author: 'PeopleOps Co.',
    verified: true,
    tags: ['kpi', 'performance', 'analytics', 'reviews'],
    href: '/agents?seed=performance-analyst',
    created_at: '2026-05-06T08:15:00.000Z',
    role: 'Analyst',
    system_prompt: 'Analyzing KPIs',
    router_role: 'reviewer',
  },

  // ── Industrial Automation ───────────────────────────────────────────
  {
    id: 'seed-plc-logic-architect',
    kind: 'agent',
    name: 'PLC Logic Architect',
    description:
      'Designs and reviews ladder logic, function blocks, and structured text for Siemens and Allen-Bradley PLCs.',
    icon: '🔧',
    accent: '#ea580c',
    platform_tags: ['industrial-automation'],
    category: 'operations',
    pricing: 'paid',
    price: 129,
    rating: 4.9,
    installs: 380,
    author: 'Linde Foundry Labs',
    verified: true,
    tags: ['plc', 'siemens', 'allen-bradley', 'ladder-logic', 'iec-61131'],
    href: '/agents?seed=plc-logic-architect',
    created_at: '2026-05-06T08:30:00.000Z',
    role: 'Architect',
    system_prompt: 'Siemens/Allen-Bradley expert',
    router_role: 'architect',
  },
  {
    id: 'seed-predictive-maintenance',
    kind: 'agent',
    name: 'Predictive Maintenance',
    description:
      'Ingests vibration, temperature, and current telemetry; predicts bearing/motor failure windows before downtime.',
    icon: '🛡️',
    accent: '#ea580c',
    platform_tags: ['industrial-automation'],
    category: 'operations',
    pricing: 'paid',
    price: 89,
    rating: 4.8,
    installs: 720,
    author: 'Linde Foundry Labs',
    verified: true,
    tags: ['predictive-maintenance', 'telemetry', 'vibration', 'failure-prediction'],
    href: '/agents?seed=predictive-maintenance',
    created_at: '2026-05-06T08:45:00.000Z',
    role: 'Monitor',
    system_prompt: 'Telemetry log analyzer',
    router_role: 'reviewer',
  },

  // ── Creative Studio ─────────────────────────────────────────────────
  {
    id: 'seed-asset-librarian',
    kind: 'agent',
    name: 'Asset Librarian',
    description:
      'Indexes /disk creative assets, tags by content, deduplicates near-matches, and surfaces what you used last.',
    icon: '🗂️',
    accent: '#db2777',
    platform_tags: ['creative-studio'],
    category: 'operations',
    pricing: 'free',
    rating: 4.6,
    installs: 4100,
    author: 'Atelier 11',
    verified: false,
    tags: ['asset-management', 'tagging', 'dedup', 'library'],
    href: '/agents?seed=asset-librarian',
    created_at: '2026-05-06T09:00:00.000Z',
    role: 'Manager',
    system_prompt: 'Organizing /disk assets',
    router_role: 'researcher',
  },
];

export function getAllListings(): MarketplaceListing[] {
  return [...listingsFromAgentTemplates(), ...SEED_LISTINGS];
}
