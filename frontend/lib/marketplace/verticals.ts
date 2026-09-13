/**
 * VOS3 Marketplace — Vertical Taxonomy
 * =====================================
 *
 * VOS3 is the underlying agentic OS. Distinct **platforms** (Employee
 * Experience, Industrial Automation, Creative Studio, …) run on top of it
 * and share a single agent + app marketplace.
 *
 * A "vertical" is the architectural seam that lets each platform present
 * only the agents and apps relevant to its domain, while the marketplace
 * itself stays category-agnostic. Every agent template and marketplace app
 * declares one or more `platform_tags` from this list. The currently active
 * platform decides which tag(s) to filter on.
 *
 * To add a new vertical: append an entry below. Nothing else needs to
 * change — the sidebar, filter bar, and routing all derive from this list.
 */

export type VerticalId =
  | 'all'
  | 'digital-workforce'
  | 'hr-employees'
  | 'industrial-automation'
  | 'creative-studio'
  | 'finance-ops'
  | 'developer-tools'
  | 'customer-success'
  | 'sales-marketing'
  | 'legal-compliance'
  | 'research-analytics';

export interface Vertical {
  id: VerticalId;
  /** Short label used in sidebars, chips, and routes. */
  label: string;
  /** Single-line description shown when the vertical is selected. */
  tagline: string;
  /** Emoji icon. Picked over lucide-react so this list stays portable. */
  icon: string;
  /** Brand accent for headers, badges, and active state. */
  accent: string;
  /** True for the synthetic "All" entry — never set on real verticals. */
  isAggregate?: boolean;
}

export const VERTICALS: Vertical[] = [
  {
    id: 'all',
    label: 'All Platforms',
    tagline: 'Browse every agent and app across the VOS3 ecosystem.',
    icon: '🌐',
    accent: '#737373',
    isAggregate: true,
  },
  {
    id: 'digital-workforce',
    label: 'Digital Workforce',
    tagline: 'Cross-functional knowledge workers — PMs, leads, analysts.',
    icon: '🧠',
    accent: '#2563eb',
  },
  {
    id: 'hr-employees',
    label: 'HR & Employees',
    tagline: 'Recruiting, onboarding, employee experience automation.',
    icon: '👥',
    accent: '#0891b2',
  },
  {
    id: 'industrial-automation',
    label: 'Industrial Automation',
    tagline: 'Manufacturing, IoT, OT, and supply-chain orchestration.',
    icon: '🏭',
    accent: '#ea580c',
  },
  {
    id: 'creative-studio',
    label: 'Creative Studio',
    tagline: 'Content, design, video, and brand-asset production.',
    icon: '🎨',
    accent: '#db2777',
  },
  {
    id: 'finance-ops',
    label: 'Finance & Ops',
    tagline: 'FP&A, treasury, accounting, and operational reporting.',
    icon: '💼',
    accent: '#16a34a',
  },
  {
    id: 'developer-tools',
    label: 'Developer Tools',
    tagline: 'Code review, infrastructure, DevX, and release engineering.',
    icon: '⚙️',
    accent: '#10b981',
  },
  {
    id: 'customer-success',
    label: 'Customer Success',
    tagline: 'Support, onboarding, retention, and account health.',
    icon: '🎯',
    accent: '#f59e0b',
  },
  {
    id: 'sales-marketing',
    label: 'Sales & Marketing',
    tagline: 'Lead gen, outreach, content marketing, and CRM hygiene.',
    icon: '📈',
    accent: '#a855f7',
  },
  {
    id: 'legal-compliance',
    label: 'Legal & Compliance',
    tagline: 'Contract review, policy drafting, and audit readiness.',
    icon: '⚖️',
    accent: '#475569',
  },
  {
    id: 'research-analytics',
    label: 'Research & Analytics',
    tagline: 'Market research, data analysis, and insight reports.',
    icon: '🔬',
    accent: '#0ea5e9',
  },
];

const VERTICAL_INDEX: Record<VerticalId, Vertical> = Object.fromEntries(
  VERTICALS.map((v) => [v.id, v]),
) as Record<VerticalId, Vertical>;

export function getVertical(id: VerticalId): Vertical {
  return VERTICAL_INDEX[id] ?? VERTICAL_INDEX.all;
}

/**
 * Filter helper. Returns true when an item should be shown for the
 * currently selected vertical. The synthetic "all" vertical matches
 * every item.
 */
export function matchesVertical(itemTags: VerticalId[] | undefined, selected: VerticalId): boolean {
  if (selected === 'all') return true;
  if (!itemTags || itemTags.length === 0) return false;
  return itemTags.includes(selected);
}

// ---------------------------------------------------------------------------
// Listing-level types
// ---------------------------------------------------------------------------

export type ListingKind = 'agent' | 'app';
export type PricingTier = 'free' | 'freemium' | 'paid';

/**
 * SmartRouter role alias used by the backend `/api/agents/resolve-model`
 * endpoint to pick a concrete LLM at install time. Mirrors the
 * `RouterRole` union in `components/agents/templates.ts`; duplicated
 * here so marketplace types stay self-contained.
 */
export type RouterRole = 'architect' | 'reviewer' | 'researcher' | 'coding';

/**
 * Marketplace-shaped projection of an agent template (without the heavy
 * system_prompt). Pages render this; the underlying source can be the
 * static AGENT_TEMPLATES array or, in future, a Convex / API query.
 */
export interface MarketplaceListing {
  id: string;
  kind: ListingKind;
  name: string;
  description: string;
  icon: string;
  accent: string;
  /** One or more verticals this listing serves. */
  platform_tags: VerticalId[];
  /** Sub-category within a vertical (e.g. 'Recruiting', 'Onboarding'). */
  category: string;
  pricing: PricingTier;
  price?: number;
  rating: number;
  installs: number;
  author: string;
  verified: boolean;
  /** Free-form keyword tags used by search. */
  tags: string[];
  /** Routing: agents go to /agents?template=<id>, apps go to /marketplace/<slug>. */
  href: string;
  /**
   * ISO-8601 publish timestamp. Drives the "Recently added" sort in the
   * marketplace. Required so the sort is honest — when this lands on a
   * Convex-backed listing, this field maps to the row's `_creationTime`.
   */
  created_at: string;
  /**
   * Optional agent-side defaults exposed by seed listings (entries that
   * aren't backed by a static AGENT_TEMPLATE). These are consumed by the
   * /agents install flow when present so users see a real role + prompt
   * pre-filled in the create form rather than a placeholder.
   */
  role?: string;
  system_prompt?: string;
  /**
   * SmartRouter role to apply on install. When set, the agents page
   * pre-fills the new agent's `model_category` with this value so the
   * router can pick a model that fits the listing's intent. `null` (or
   * omitted) means "let the form pick its own default" — same behavior
   * as a manual create.
   */
  router_role?: RouterRole | null;
}
