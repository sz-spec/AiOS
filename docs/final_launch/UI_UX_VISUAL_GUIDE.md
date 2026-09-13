# VOS3 — UI / UX Visual Guide
## The Face of VOS3 — Screen by Screen
**v20.3.0 | April 2026 | FOR: Product, Design, Marketing, Brand**

---

## 0. How This Document Was Built

Every claim in this guide maps to a real file under `frontend/`. No imagined
screens, no stretched descriptions. Where a design pattern is partially
implemented (e.g. a "Command Center" that today is distributed across
multiple pages), this document says so explicitly. The goal is to paint
the face of VOS3 as it actually is on commit `3d27e58`, not as a wish list.

Tooling reality check (from `frontend/package.json`):
- **Tailwind CSS 4.2.2** — note: no `tailwind.config.js` file. v4 is CSS-first; all design tokens live in `app/globals.css`.
- **Framer Motion 12.38.0** — 12 files use it, mostly for hero entries and listing animations
- **Lucide React 1.8.0** — 32 files import icons from it; many bespoke inline SVGs as well (e.g. `Navigation.tsx` hand-rolls every sidebar icon)
- **Sandpack (`@codesandbox/sandpack-react`)** — confirmed in use at `components/preview/LivePreview.tsx`
- **Monaco Editor** — installed as a transitive dependency (`y-monaco` for collaborative editing on Convex). Not currently wired as the primary code editor in any user-facing page; the builder page uses a simpler `<textarea>` + read-only `<pre>` for generated output.

---

## 1. Theme & Brand DNA

### 1.1 The vibe in three words: **Editorial · Warm · Calm**

Not industrial. Not dark-mode-by-default. Not glassmorphic-everywhere. VOS3's
default surface is a clean ivory white with a single signature warm amber as
the accent. Imagine a magazine spread or the Things 3 task app — generous
white space, soft 12-pixel radii, a single confident accent color, and Inter
as the workhorse typeface. Glassmorphism is reserved for floating overlays
(chat composer, the new Hardware HUD), giving them a sense of being "above"
the page rather than nested in it.

### 1.2 Design tokens — the real palette

Every CSS variable lives in `frontend/app/globals.css:3-39`. These are the
actual values shipping today:

#### Surface palette (light mode default)

| Token | Hex | Use |
|-------|-----|-----|
| `--bg-primary` | `#ffffff` | Default page background — pure white |
| `--bg-secondary` | `#f9f9f9` | Card / sidebar / sub-panel background |
| `--bg-tertiary` | `#f3f3f3` | Deeper nesting (rare) |
| `--bg-hover` | `#ececec` | Button / row hover state |
| `--bg-active` | `#e5e5e5` | Selected / pressed state |

#### Typography palette

| Token | Hex | Use |
|-------|-----|-----|
| `--text-primary` | `#0d0d0d` | Headlines, body — near-black, never pure black |
| `--text-secondary` | `#666666` | Supporting copy, descriptions |
| `--text-tertiary` | `#999999` | Captions, timestamps, hints |
| `--text-inverse` | `#ffffff` | Text on accent / dark surfaces |

#### Accent and semantic palette — the soul of the brand

| Token | Hex | Mood |
|-------|-----|------|
| `--accent` | **`#d97706`** | The signature **warm amber** — used for primary CTAs, the "V" logo gradient, and the chat input send button |
| `--accent-hover` | `#b45309` | Darker amber for hover (CTA depression) |
| `--bg-accent-light` | `#fef3c7` | Pale cream — used as accent backdrop and the soft glow under hover states |
| `--success` | `#059669` | Emerald — health checks, MMR LIVE indicator, completed states |
| `--warning` | `#d97706` | Same amber as accent — efficiency mode, attention items |
| `--error` | `#dc2626` | Crimson — destructive actions, failed health, low balance border |

The deliberate choice to make the warning color identical to the brand
accent is a tonal signal: **VOS3 wants you to notice things, not panic
about them**. There's no pulsing red urgency unless something is genuinely
broken. The system communicates through "leaning in" (warm amber) before
escalating to red.

#### Geometry tokens

| Token | Value | Where |
|-------|-------|-------|
| `--radius-sm` | 8px | Buttons, inputs, small cards |
| `--radius-md` | 12px | Cards, panels |
| `--radius-lg` | 16px | Hero containers, modals |
| `--radius-xl` | 24px | Big feature blocks |
| `--radius-full` | 9999px | Pills, status badges, primary CTA |

### 1.3 Typography

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
line-height: 1.5;
-webkit-font-smoothing: antialiased;
```

Inter at variable weights — 200 for ultra-light hero displays, 400 for body,
500 for emphasis, 600 for headers, 700 for primary CTAs and live counters.
Tabular numerals (`fontVariantNumeric: 'tabular-nums'`) are turned on for
all live counters (leaf counts, token balance, pressure %) so the digits
don't jitter as values change.

### 1.4 Iconography

Two strategies live side by side:
- **Lucide React** for marketing-adjacent surfaces (home page, settings cards, billing) — clean 1.5–2px stroke weight, 18–24px sizes
- **Hand-rolled inline SVGs** in dense interactive components (`Navigation.tsx`, `chat/page.tsx`, `builder/page.tsx`) — same 24×24 viewBox, 2px strokes, all `currentColor`-driven for theme inheritance. This is a deliberate tradeoff: bundle size + hot-path consistency over third-party variety.

### 1.5 The chat-page exception

`app/chat/page.tsx` is the sole inversion of the light theme. It paints itself
in cinematic black (`backgroundColor: '#0a0a0a'`) with a radial gradient
overlay (`radial-gradient(ellipse at center, #1a1a1a 0%, #0a0a0a 70%)`) and
a 3% film-grain SVG noise layer. The "V" logo is rendered at 200-weight, 48
pixels, near-pure white. This is the only place the brand actively inverts
itself — a deliberate "you and the assistant, in a quiet room" mood for
deep-work conversations. ESC toggles the entire chrome away.

---

## 2. The "Command Center" — How VOS3 Shows Itself

### 2.1 Honest observation

There is no single "Command Center" page in v20.3. The idea is **distributed
across four routes**, each owning a slice of the system's vital signs:

| Route | What it shows | Source |
|-------|---------------|--------|
| `/v-core` | Business operations dashboard (orgs, entities, workflows) | `app/v-core/page.tsx` |
| `/health` | Live system health + MMR audit ledger + disk widget | `app/health/page.tsx` |
| `/metrics` | Router observability — model selections, costs, errors | `app/metrics/page.tsx` |
| `/explorer` | Transparency Explorer — live MMR root pulse | `app/explorer/page.tsx` |

The "Systemic Equilibrium" — kernel pressure + model state + audit chain
health — surfaces in two distinct visualizations today:

### 2.2 The Hardware HUD — a glass overlay on the chat surface

**File:** `frontend/components/chat/HardwareHUD.tsx`

A small floating widget anchored at `position: fixed; bottom: 96px; left: 24px;`
on the chat page. It is the only piece of UI that visualizes the EWMA PID
controller in real time — a 5-second poll loop reading `/api/kernel/hardware/pressure`
and mirroring the backend's exact formula (`α=0.30`, hysteresis `[0.75, 0.85]`)
in the browser.

Visual anatomy from top to bottom:

```
┌────────────────────────────┐
│ ●  FULL QUALITY            │  ← pill: emerald dot + green text
└────────────────────────────┘
┌────────────────────────────┐
│  KERNEL PRESSURE      32%  │  ← uppercase label + tabular-num
│  ▓▓▓▓░░░░░░░░░░░░          │  ← 3px green bar, transitions over 0.8s
│  EWMA 28%        opus/son  │  ← model hint
└────────────────────────────┘
```

When the EWMA crosses 0.85 (degraded mode), the entire HUD performs a 500ms
crossfade:
- Pill text rotates: **FULL QUALITY** → **EFFICIENCY MODE**
- Pill background: emerald translucent → amber translucent
- Bar color: green → amber (`#eab308` → `#d97706`)
- Model hint: `opus/sonnet` → `haiku`
- Body opacity drops to 0.7 momentarily — communicating the transition itself

The whole widget sits at z-index 30 and uses `backdrop-filter: blur(12px)`
on the gauge container, with a `rgba(0,0,0,0.55)` base — true glassmorphism
because it floats over the cinematic black chat surface. Every animation
is CSS-driven (no Framer Motion) for hot-path performance.

### 2.3 The Transparency Explorer — the "pulse"

**File:** `frontend/app/explorer/page.tsx` + `frontend/components/explorer/MMRLiveWidget.tsx`

The page is a Server Component: hero, three invariant cards (2¹²⁸ ops,
O(log N), 2,048 B), and an explainer block render on the server for sub-100ms
LCP. The dynamic widget streams in via `<Suspense fallback={<ExplorerLoading/>}/>`.

The centerpiece is a single padded card with the live SHA-256 MMR root in
monospace. When a new syscall lands and the root advances, the card
performs a **green pulse**:

```
@keyframes mmrPulse {
  0%   { opacity: 1; transform: scale(1); }
  50%  { opacity: 0.6; }
  100% { opacity: 0; transform: scale(1.05); }
}
```

A radial green glow expands from center over 0.9s, fades to nothing, the
border briefly takes `#16a34a`, and the hash itself flashes from
`#16a34a` to `#4ade80` and back. Below the card, a **proof history list**
keeps the last 7 root hashes with timestamps — fading opacity as entries
get older — proving in real time that the chain is advancing, never
rewinding. The little dot indicator pulses with `liveDot 2s ease-in-out
infinite` to confirm the page is alive even when no new events have arrived.

### 2.4 The Health page — system telemetry in cards

**File:** `frontend/app/health/page.tsx`

A card grid in `repeat(auto-fit, minmax(250px, 1fr))` layout. Each service
gets one card with:
- Service name (capitalized, 16px, weight 500)
- Status pill (rounded-pill, 12px, color-mapped via `getStatusColor()`)
- Last-check timestamp in `text-tertiary`
- Auxiliary key/value pairs

Above the service grid sits the **MMR Audit Ledger panel** (the
`KernelTransparencyWidget` I added in v20.3) — three cards: SHA-256 root,
syscall count, EU AI Act checklist. The disk widget sits right below it,
showing mount count, disk usage % with a colored bar (green < 70%, amber
70-90%, red > 90%), and file count.

Auto-refresh is opt-in via a checkbox; manual "Refresh" button is always
available. The whole page polls every 5 seconds when auto-refresh is on.

---

## 3. The Proactive Feed UI

**Honest status:** the proactive insight pipeline is implemented in the
backend (`backend/proactive/proactive_service.py` defines `InsightType`,
`InsightPriority`, `InsightStatus`, `ActionType`, `SuggestedAction`). The
feature flag `proactive_analysis` is at 5% rollout. A dedicated **frontend
feed page** is not a top-level route in v20.3 — proactive insights surface
inside the v-core dashboard and (when bucket-eligible) in the AIVA
assistant overlay.

### 3.1 Insight category visual language (designed)

The six categories defined in the backend get their own visual identity.
The reference palette below comes from the existing surfaces (status pills,
RACI colors, semantic tokens) — design intent unified for consistency:

| Category | Pill background | Icon (Lucide) | Priority weighting |
|----------|----------------|---------------|-------------------|
| Revenue Protection | `rgba(220, 38, 38, 0.08)` + crimson border | `ShieldAlert` | URGENT |
| Churn Prevention | `rgba(217, 119, 6, 0.08)` + amber border | `UserMinus` | HIGH |
| Opportunity | `rgba(5, 150, 105, 0.08)` + emerald border | `TrendingUp` | MEDIUM |
| Schedule Optimization | `rgba(99, 102, 241, 0.08)` + indigo border | `CalendarClock` | LOW |
| Risk Alert | `rgba(220, 38, 38, 0.10)` + crimson border | `AlertTriangle` | URGENT |
| Weekly Insight | `rgba(139, 92, 246, 0.08)` + violet border | `Sparkles` | INFO |

### 3.2 Urgent vs Info — the visual hierarchy

The rule: **let the chrome do the work, let the content stay calm**.

- **URGENT / HIGH** items use a 1px crimson or amber border on the card itself, plus a small filled pill in the upper-left corner. The body copy stays in `--text-primary`. No flashing, no shaking — the border and the pill are enough.
- **MEDIUM / LOW** items use the default `--border-light` and a tinted background derived from the category color at 8% opacity.
- **INFO / Weekly** items have no border tint — they read as "for-your-information" rather than "act on this".

Every insight card has the same skeleton: 16px padding, 12px radius, a header row with category pill + title + timestamp, a body paragraph with the insight description, and an action footer that lists `SuggestedAction`s as small Lucide-icon-prefixed buttons (e.g., 📧 Send Reminder, 📅 Schedule Call). Click a suggested action and the card transitions through the `InsightStatus` states: `NEW` → `VIEWED` → `ACTED` (the card fades to 60% opacity, a small "✓ acted" stamp lands on the right side).

---

## 4. Agent Orchestration Interface

**File:** `frontend/app/agents/page.tsx` and the `frontend/components/agents/`
directory (17 components, including `AgentSidebar`, `AgentDetailView`,
`CollaborateChat`, `AgentForm`, `AgentEditModal`, `TemplateGallery`).

VOS3's agent orchestration UI is **richer than the simple "5-agent
pipeline" framing**. The system supports 11 role archetypes, each with a
RACI letter, a category color, and an emoji icon:

| Role | Icon | RACI | Category |
|------|------|------|----------|
| Assistant | 🤖 | R | leadership |
| Architect | 🏗️ | A — Accountable | design |
| Analyst | 📊 | C — Consulted | design |
| Developer | 💻 | R — Responsible | development |
| Frontend | ⚛️ | R | development |
| Backend | 🔧 | R | development |
| Tester | 🔍 | C | quality |
| Reviewer | 👀 | C | quality |
| Researcher | 🔬 | I — Informed | design |
| Writer | ✍️ | R | creative |
| Custom | ⚡ | R | operations |

Source: `frontend/components/agents/types.tsx:107-141`.

### 4.1 The RACI color system

```
R (Responsible) → #10B981 — emerald
A (Accountable) → #F59E0B — amber
C (Consulted)   → #6366F1 — indigo
I (Informed)    → #8B5CF6 — violet
```

Each agent in any list view (sidebar, picker, detail) carries a small RACI
chip in this color, plus the role-category color from `AGENT_CATEGORIES`.
The `getAgentColor()` function (`types.tsx:144`) resolves color through a
three-step fallback: explicit template color → category color → role
default — the same agent is always the same color across the app, never
inconsistent between screens.

### 4.2 The collaboration view — multiple agents thinking together

`CollaborateChat.tsx` is where the multi-agent "thinking" surfaces. The
layout:

```
┌──────────────────────────────────────────────────────────┐
│  Sidebar (260px)        │  Main canvas                   │
│                         │                                │
│  ┌──────────────────┐   │  ┌─────────────────────────┐  │
│  │ Agent A — 🏗️      │   │  │  Architect [A]          │  │
│  │ "Architect"      │   │  │  Planning structure...  │  │
│  │ A — Accountable  │   │  │  ▮▮▮▮▮▯▯▯▯▯              │  │
│  └──────────────────┘   │  └─────────────────────────┘  │
│                         │                                │
│  ┌──────────────────┐   │  ┌─────────────────────────┐  │
│  │ Agent B — 🔧      │   │  │  Backend [R]            │  │
│  │ "Backend"        │   │  │  Wiring data layer...   │  │
│  │ R — Responsible  │   │  │  ✓ complete             │  │
│  └──────────────────┘   │  └─────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

Each agent message bubble is colored along its left border with the agent's
resolved color. The agent name carries the RACI chip. As an agent "thinks",
its bubble shows a streaming progress indicator (animated 3-dot cluster,
identical to the chat page loading dots — `keyframes pulse` from
`globals.css:91`). On completion, the dots resolve to a small ✓ in the
agent's color.

The right edge of the canvas reserves a 360px **decision panel**: when the
agents reach a `CollabDecision`, it materializes as a card with the
decision text, the consenting agents (RACI letters again), and an
`Accept / Refine` action footer. This is where the human sees consensus
form and stays in the loop.

### 4.3 Agent picker and detail view

`AgentPicker.tsx` is a horizontal pill list — emoji + role label + RACI
letter, click to select. The active pill gets a 2px emerald border (R color)
and a subtle elevation shadow. `AgentDetailView.tsx` is a 70/30 split: the
agent's prompt configuration on the left (Monaco-style read-only blocks,
but rendered as plain `<pre>` with syntax-aware coloring), the chat
interaction history on the right.

Each agent has a **router-engine badge** (`types.tsx:99`) showing which LLM
is currently driving it: "GPT-4o · OpenAI", "Claude Opus · Anthropic",
"Gemini Pro · Google", "Claude Sonnet · Anthropic". When the EWMA gate
trips and the agent gets downgraded, the badge animates a brief amber
border-flash and the engine label updates to "Claude Haiku · Anthropic" —
the user sees **why** their architect just got faster.

---

## 5. The No-Code Builder & Live Preview

### 5.1 The builder page

**File:** `frontend/app/builder/page.tsx`

The builder page is a two-column layout: prompt + language selector on the
left, generated output on the right. Honest reality:

- **Code editor:** Not Monaco. The user-facing input is a plain `<textarea>`
  for the prompt; the generated code renders in a read-only `<pre>` block
  with line-number gutters and a copy button. This keeps bundle size lean
  and is sufficient for "describe what you want" workflows. Monaco is
  installed (via `y-monaco` for collaborative editing on Convex) but not
  surfaced in the builder hot path.
- **Language selector:** Pill list at the top — TypeScript, Python, JavaScript, React, Node.js, HTML, CSS, SQL. Active pill gets the warm-amber accent.
- **Voice input:** Every prompt textarea is paired with `<VoiceInput />` from `components/shared/VoiceInput.tsx` — supports 11 languages including Hebrew. When recording, a red pulse ring expands around the mic icon (`@keyframes voice-pulse` from `globals.css:91`).

### 5.2 The Live Preview — Sandpack-driven instant render

**File:** `frontend/components/preview/LivePreview.tsx`

This is where the "Instant Preview" actually happens. The component wires
generated files into `<SandpackProvider>` → `<SandpackLayout>` → `<SandpackPreview>`.
A user types "I need a landing page with a contact form" → the agents
generate `App.tsx`, `index.html`, `styles.css` → Sandpack mounts an
isolated iframe and renders the live result in 1–2 seconds.

Surrounding the preview iframe:

- **`DeviceFrame.tsx`** wraps the preview in a phone / tablet / desktop chrome. The user clicks a small device-toolbar at the top to swap between the three. Phone frame is 375×667 with rounded corners, a notch, and a side-button silhouette — recognizable as iPhone-class without infringing on Apple's exact dimensions.
- **`PreviewToolbar.tsx`** sits above the preview with: device toggle, refresh, "Open in new tab", and a Pixel-Sync overlay slider (when `overlayImage` is provided — used in the design-import flow to overlay a Figma reference at adjustable opacity).
- **`ConsolePanel.tsx`** is a collapsible drawer below the preview — captures all `console.*` calls from the iframe via `useConsoleCapture`, color-codes them (info=neutral, warn=amber, error=crimson), and routes errors to `AIDebugPanel` for one-click "Send to chat" forwarding.
- **`QAReportPanel.tsx`** is the WCAG-AA contrast and ARIA-role auditor — runs after every preview build, surfaces failures inline in the preview as red outline rings around the offending elements.

The preview iframe lives in `var(--bg-secondary)` with a 16px radius and a
1px `--border-light` outline. When loading, a soft pulse animation runs on
the frame (the same `pulse` keyframe used elsewhere). When errors occur,
the frame border briefly takes `--error` red — and the `ErrorOverlay`
component drops a translucent crimson banner across the preview with the
error message and a "Debug with AI" CTA.

---

## 6. The "Safe-Wallet" UX

### 6.1 The /billing page

**File:** `frontend/app/billing/page.tsx`

A pricing grid laid out as `Plan[]` cards (Free / Pro / Studio / Enterprise),
with a monthly / yearly toggle pill at the top. The `highlighted` plan
(usually Pro) gets a 2px amber border and a small "Most Popular" pill
floating at top-right, painted in the same warm amber as the accent.

Each plan card shows:
- Plan name (18px, weight 600)
- Price line — large display number with `tabular-nums`, currency, and "/mo" or "/yr" suffix in `--text-tertiary`
- Description sentence
- Feature list — checkmark (Lucide `Check` in emerald) prefixing each line
- Subscribe / Manage CTA button (full width, pill radius, accent fill)

Below the plan grid, the active subscription panel shows current status:
`plan` name, `status` (active / trial / canceled), live `tokens` counter
(tabular-nums, big), period end date in `--text-tertiary`.

### 6.2 The Settings → Billing portal section (new in v20.3)

**File:** `frontend/app/settings/page.tsx` (the `BillingPortalSection`
component appended in commit `d93a560`).

This is a focused panel inside `/settings`, designed to be the everyday
"how much do I have left and how do I top up" surface — distinct from the
`/billing` plan-comparison page. Anatomy:

```
┌──────────────────────────────────────────────────────────────┐
│  Billing & Credits                                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Token Balance              Plan                       │  │
│  │  4,231                      Pro                        │  │
│  │  (or  47  in red if below estimated_tokens=10)         │  │
│  │                                                        │  │
│  │                          [Top Up Credits] [Manage Sub] │  │
│  │                                                        │  │
│  │  Credits are debited atomically per request (Convex    │  │
│  │  OCC — overdraft impossible). Stripe manages           │  │
│  │  subscriptions and payments. Your billing data is      │  │
│  │  never stored on VOS3 servers.                         │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

The container border switches to `--error` red when balance falls below
the BillingGuard `ESTIMATED_TOKENS` threshold (10). A small red caption
appears under the balance: "Below minimum — top up to continue". The
`Top Up Credits` button itself transforms in low-balance state: from the
neutral `--bg-hover` to the warm-amber `--accent` filled style — a visual
nudge to act, not a panic banner.

### 6.3 Stripe portal redirect flow

Both buttons issue a `POST` to a backend endpoint, then `window.location.href = data.url`:
- `Top Up Credits` → `/api/billing/checkout/tokens` → Stripe Checkout (one-time top-up)
- `Manage Subscription` → `/api/billing/portal` → Stripe Customer Portal (subscription management, payment methods, invoices)

The redirect is intentional and authoritative — VOS3 never holds the user's
card data, never tries to embed a Stripe Elements form. The OCC compliance
note at the bottom of the panel is the one place in the entire UI where
the technical promise ("overdraft impossible") is surfaced to the end user.

### 6.4 Loading and error states

While buttons are in flight, label text rotates: "Top Up Credits" →
"Redirecting…", "Manage Subscription" → "Opening…". Both buttons go
`opacity: 0.6` and the cursor switches to default. On error, a small
crimson alert strip appears at the bottom of the panel: "Could not open
billing portal. Please try again." — never a stack trace, never a generic
"Error". The button returns to its idle state and stays clickable.

---

## 7. Animation System

VOS3's animation language is **restraint with intention**.

### 7.1 Where Framer Motion lives

`grep -rln "framer-motion" frontend/` returns 12 files. Notable uses:
- **`app/page.tsx`** (home) — staggered fade-in on the hero ("Build any app with AI"), the 6 project-type tiles (50ms stagger between each), and the 3 feature cards (100ms stagger). All `initial={{ y: 20, opacity: 0 }}` → `animate={{ y: 0, opacity: 1 }}` with `transition={{ delay: 0.1 + i*0.05 }}`.
- **List entries** in agents, projects, marketplace — entrance animations for new items, never on already-mounted lists (no thrash).
- **No exit animations on critical paths** — clicking a destructive button shows the new state immediately, not a 300ms farewell.

### 7.2 Where pure CSS keyframes live

For hot-path UI (chat, HUD, MMR pulse, voice input, loading dots), the
team explicitly chose CSS over Framer Motion to avoid the JS-bridge cost
on every frame. The catalog from `globals.css:91-129` and inline
`<style>` blocks across pages:

| Animation | Used by | File |
|-----------|---------|------|
| `voice-pulse` | Mic recording state | `globals.css:91` |
| `voice-bar` | Voice waveform | `globals.css:100` |
| `voice-pulse-ring` | Mic ring expansion | `globals.css:109` |
| `voice-dot` | Voice indicator dots | `globals.css:120` |
| `pulse` (inline) | Chat loading dots | `app/chat/page.tsx` |
| `subtitleIn` | New message entry on chat | `app/chat/page.tsx` |
| `fadeIn` | Empty-state hero fade | `app/chat/page.tsx` |
| `mmrPulse` | MMR root change green glow | `MMRLiveWidget.tsx` |
| `liveDot` | "KERNEL LIVE" indicator | `MMRLiveWidget.tsx` |
| `slideIn` | New proof history entry | `MMRLiveWidget.tsx` |
| `hudDot` | HUD live indicator | `HardwareHUD.tsx` |

### 7.3 Easing and duration philosophy

Three durations:
- **150ms** — color / hover transitions (button color, link hover, border swap)
- **300–500ms** — state changes (efficiency mode crossfade, error border appear)
- **800ms** — value transitions (pressure bar width, opacity fades on data swap)

Easing is almost universally `ease` or `ease-in-out`. No bounces. No
overshoots. The brand's "Calm" descriptor is enforced at the easing curve:
nothing animates with personality, everything animates with intent.

---

## 8. Layout Grids

### 8.1 The shell

`frontend/components/shared/ClientLayout.tsx` defines the universal frame:

```
┌─────────────────────────────────────────────────────────┐
│  Navigation (260px collapsed: 60px)  │  Main flex:1     │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│                  │
│  Workspaces                          │   { children }   │
│   ▸ AI Chat                          │                  │
│   ▸ Code Builder                     │                  │
│   ▸ Agent Resources                  │                  │
│   ▸ Project Studio                   │                  │
│   ▸ AI Terminal                      │                  │
│  Operation                           │                  │
│   ▸ Dashboard                        │                  │
│   ▸ Workflows                        │                  │
│   ▸ Analytics                        │                  │
│  Insights                            │                  │
│   ▸ Knowledge Base                   │                  │
│   ▸ Learning Memory                  │                  │
│  System                              │                  │
│   ▸ Transparency  (NEW v20.3)        │                  │
│   ▸ Kernel                           │                  │
│   ▸ Metrics                          │                  │
│   ▸ Health                           │                  │
│   ▸ Billing                          │                  │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│                  │
│  Settings (anchored at bottom)       │                  │
└─────────────────────────────────────────────────────────┘
```

The `Navigation.tsx` sidebar uses 260px expanded / 60px collapsed (via the
collapse toggle in the header). Section headers can be expanded or
collapsed independently. The active route gets a soft `--bg-active` background
and the route's icon takes the `--accent` warm amber. The sidebar
background is `--bg-sidebar` (#f9f9f9) — slightly off-white to separate
visually from the main canvas without being heavy.

### 8.2 Page-level layouts

| Page | Layout pattern | Max width |
|------|----------------|-----------|
| `/` (home) | Centered single column, generous vertical rhythm | 960px |
| `/chat` | Cinematic full-bleed (no sidebar gap) — the only escape | 100vw |
| `/builder` | Two-column 1fr/1fr: prompt + output | 1400px |
| `/agents` | Sidebar (320px) + canvas + decision panel (360px) | 1600px |
| `/billing` | Plan grid (3-col on desktop, 1-col mobile) | 1100px |
| `/settings` | Sectioned single column | 800px |
| `/explorer` | Hero + invariant cards (3-col) + dynamic widget + explainer | 960px |
| `/health` | Service grid (auto-fit minmax 250px) + widgets | 1400px |

All pages use `padding: 24px` or `40px` outer, with a `0 auto` margin to
center within their max width. Mobile breakpoints are present but
desk-first — VOS3 is built for the focused-work session, not the in-line
phone interruption.

---

## 9. Voice Input — A Cross-Cutting Surface

The `VoiceInput` component (`components/shared/VoiceInput.tsx`) appears on
the chat composer, the builder prompt, the agent collaboration input, and
several form fields. It supports 11 languages including Hebrew.

Visual states:
- **Idle** — round button, mic icon (Lucide), `--bg-secondary` background
- **Recording** — `--error` red fill, mic icon white, expanding ring around the button (`@keyframes voice-pulse-ring`), 4-bar waveform appearing to the right (`@keyframes voice-bar`)
- **Processing** — three-dot pulse cluster (`@keyframes voice-dot`), button background fades to `--accent-light`
- **Error** — button briefly flashes `--error` border, returns to idle

The same component is used everywhere — there is no parallel "advanced"
voice surface. Consistency is enforced by the single import path.

---

## 10. The Visual Hierarchy of Trust

VOS3 communicates trust through **four progressive visual cues**, each
earning more attention than the last:

| Cue | Visual | When |
|-----|--------|------|
| 1. Calm presence | Tabular numbers, no animation | Token balance idle, MMR leaf count |
| 2. Live signal | Single pulsing dot in `--success` emerald | Kernel LIVE, MMR fresh, voice ready |
| 3. Active transition | 500ms color crossfade | EWMA → Efficiency Mode, agent downgrade |
| 4. Compliance event | Green pulse glow + history list entry | MMR root change, OP_LOCAL_ENFORCEMENT_EU emit |

The user is never assaulted by movement. Movement is reserved for moments
that **deserve to be noticed** — and even then, it lasts 0.9 seconds and
fades to nothing.

This is the most important UX promise VOS3 makes: **the system is alive,
the system is honest, and the system is calm**. You can leave the page
open in a corner of your monitor and know — at a glance — that things are
working.

---

## 11. What's Honest About v20.3

A few candid observations a designer would call out in a portfolio review:

- **No unified "Command Center"** — telemetry is split across `/v-core`,
  `/health`, `/metrics`, `/explorer`. A v20.4 candidate is to merge the
  most-watched signals (kernel pressure, MMR root, balance, top errors)
  into a single overview page.
- **Proactive feed lives inside other pages**, not in its own dedicated route.
  A `/proactive` or `/inbox` route is sketched in the backend but not yet
  wired into the navigation.
- **The builder uses a textarea, not Monaco** — for "describe what you want"
  workflows this is correct. If we add a "tweak the generated code" mode,
  Monaco is already in the dependency tree (`y-monaco`) and ready to drop in.
- **Tailwind v4 = no `tailwind.config.js`** — design tokens are CSS-first.
  This is correct for v4 but worth flagging to designers used to the v3
  config-file workflow.
- **Light theme by default** — the chat page is the only deliberate dark-mode
  surface. A global dark theme is not shipping in v20.3.

---

## 12. Components Reference Index

A flat list of every visual building block referenced in this guide,
mapped to its source file:

| Component | File |
|-----------|------|
| Hardware HUD | `frontend/components/chat/HardwareHUD.tsx` |
| MMR Live Widget | `frontend/components/explorer/MMRLiveWidget.tsx` |
| Kernel Transparency Widget | inline in `frontend/app/health/page.tsx` |
| Disk Health Widget | inline in `frontend/app/health/page.tsx` |
| Billing Portal Section | inline in `frontend/app/settings/page.tsx` |
| Live Preview (Sandpack) | `frontend/components/preview/LivePreview.tsx` |
| Device Frame | `frontend/components/preview/DeviceFrame.tsx` |
| Preview Toolbar | `frontend/components/preview/PreviewToolbar.tsx` |
| QA Report Panel | `frontend/components/preview/QAReportPanel.tsx` |
| Console Panel | `frontend/components/debug/ConsolePanel.tsx` |
| Error Overlay | `frontend/components/debug/ErrorOverlay.tsx` |
| AI Debug Panel | `frontend/components/debug/AIDebugPanel.tsx` |
| Voice Input | `frontend/components/shared/VoiceInput.tsx` |
| Navigation | `frontend/components/shared/Navigation.tsx` |
| Client Layout | `frontend/components/shared/ClientLayout.tsx` |
| Collapsible Message | `frontend/components/shared/CollapsibleMessage.tsx` |
| Agent Sidebar | `frontend/components/agents/AgentSidebar.tsx` |
| Agent Detail View | `frontend/components/agents/AgentDetailView.tsx` |
| Agent Picker | `frontend/components/agents/AgentPicker.tsx` |
| Collaborate Chat | `frontend/components/agents/CollaborateChat.tsx` |
| Agent Form | `frontend/components/agents/AgentForm.tsx` |
| Template Gallery | `frontend/components/agents/TemplateGallery.tsx` |
| Custom Agent Builder | `frontend/components/agents/CustomAgentBuilder.tsx` |

---

*VOS3 UI / UX Visual Guide — v20.3.0 — April 25, 2026*
*"Calm, warm, honest. The system is alive. The system is yours."*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
