# VOS3 — UI Visual Walkthrough
## A User's Journey, Pixel by Pixel
**v20.3.0 | April 2026 | FOR: Design Review, Investor Demo, Onboarding**

---

> **Companion document:** `UI_UX_VISUAL_GUIDE.md` is the screen-by-screen
> reference manual. **This** document is the journey — a narrative
> walkthrough of what a user actually sees, in the order they see it,
> from cold-start to closing the laptop.

---

## Part I — The Five Faces of VOS3

Five visual elements define the product's identity. Each is grounded in
a real file under `frontend/`.

### Face 1 — The Brand DNA: "Editorial · Warm · Calm"

Not industrial. Not dark-mode-by-default. Not glassmorphic-everywhere.
VOS3's default surface is **ivory white** (`--bg-primary: #ffffff`,
`--bg-secondary: #f9f9f9`) — the same restraint you find in a magazine
spread or in Things 3. The single confident accent is a **warm amber**
(`--accent: #d97706`) that reads as competence rather than urgency. Inter
at 200 weight handles the hero displays; tabular numerals lock all live
counters so digits never jitter when values change.

Source: `frontend/app/globals.css:3-39`. There is no `tailwind.config.js`
file — Tailwind v4 is CSS-first and every token lives in that one stylesheet.

The brand makes one deliberate inversion: **the chat page is cinematic
dark** (`#0a0a0a` with a radial gradient and 3% film-grain SVG noise). It's
the only place the product asks the user to be quiet with it. Every other
surface is the bright, calm, editorial canvas.

### Face 2 — The Hardware HUD: A glass overlay that watches the kernel

**File:** `frontend/components/chat/HardwareHUD.tsx`

A small floating widget anchored at `position: fixed; bottom: 96px;
left: 24px;` on the chat page. It polls `/api/kernel/hardware/pressure`
every 5 seconds and **mirrors the backend EWMA formula in the browser**
(`α = 0.30`, hysteresis `[0.75, 0.85]`). The user sees, in real time, the
exact same number the router uses to decide which model to wake up.

```
┌─────────────────────────────┐
│  ●  FULL QUALITY            │  ← emerald dot · uppercase · letter-space 0.06
└─────────────────────────────┘
┌─────────────────────────────┐
│   KERNEL PRESSURE      32%  │  ← tabular-nums, label 9px / value 11px / 700
│   ▓▓▓▓░░░░░░░░░░░░          │  ← 3px bar, transitions over 0.8s ease
│   EWMA 28%        opus/son  │  ← hint text in rgba(255,255,255,0.3)
└─────────────────────────────┘
```

The gauge container uses true glassmorphism: `backgroundColor: rgba(0,0,0,0.55)`
+ `backdropFilter: blur(12px)`. It floats over the cinematic black chat
canvas with a 1px subtle border (`rgba(255,255,255,0.08)`). When the EWMA
crosses 0.85, a 500ms crossfade rolls through the whole widget:

- The pill text rotates: **FULL QUALITY** → **EFFICIENCY MODE**
- The dot and text shift: emerald → warm amber
- The bar color transitions: `#16a34a` (green) → `#eab308` (yellow) → `#d97706` (amber) over 0.5s
- The model hint label updates: `opus/sonnet` → `haiku`
- The whole widget dips to 0.7 opacity for 600ms — the transition itself becomes a moment

No flicker. The user reads "Efficiency Mode" and understands implicitly:
the system noticed the world is busy, and chose speed.

### Face 3 — Agent Orchestration: Color-coded RACI in motion

**File:** `frontend/components/agents/types.tsx:107-141`

Every agent in the pipeline carries two visual signals — its **role color**
(from category mapping) and its **RACI letter chip**. The RACI palette is
hard-coded:

| Letter | Meaning | Color |
|--------|---------|-------|
| **R** | Responsible — does the work | `#10B981` emerald |
| **A** | Accountable — owns the outcome | `#F59E0B` amber |
| **C** | Consulted — provides expert input | `#6366F1` indigo |
| **I** | Informed — kept in the loop | `#8B5CF6` violet |

The Architect is **A** (amber). The Frontend, Backend, Developer, Writer
agents are **R** (emerald). The Reviewer, Tester, Analyst are **C** (indigo).
The Researcher is **I** (violet). When the Architect speaks first in a
collaboration, you see amber. When the engineers respond, you see green.
When the reviewer weighs in, you see indigo. The user reads the RACI flow
**at a glance** — no labels needed once they've learned the four colors.

The `getAgentColor()` function resolves color through a three-step fallback:
explicit template color → category color → role default. The same agent is
**always the same color across every screen** — sidebar, picker, detail,
chat bubble, decision panel. Color is identity.

When an agent is "thinking", its bubble shows a 3-dot pulsing cluster
animated with `@keyframes pulse` — identical to the chat loading dots,
deliberately consistent. When the agent finishes, the dots resolve into a
small ✓ in the agent's color.

### Face 4 — The Transparency Explorer: Pulsing green proof

**Files:** `frontend/app/explorer/page.tsx` + `frontend/components/explorer/MMRLiveWidget.tsx`

The page is a Server Component for sub-100ms LCP — hero, three invariant
cards (2¹²⁸ ops, O(log N), 2,048 B), explainer block — render synchronously.
The dynamic widget streams in via `<Suspense>`. The centerpiece is the
live SHA-256 MMR root, rendered in monospace at 15px, letter-spacing 0.04em.

When a new syscall lands and the root advances:

```
@keyframes mmrPulse {
  0%   { opacity: 1; transform: scale(1); }
  50%  { opacity: 0.6; }
  100% { opacity: 0; transform: scale(1.05); }
}
```

A radial green glow expands from the card center over 0.9s, fades to
nothing, the card border briefly takes `#16a34a`, and the hash itself
flashes from `#16a34a` → `#4ade80` → `#16a34a`. Below the card, a
**proof history list** keeps the last 7 root hashes with timestamps,
opacity fading by 12% per step into the past — the user sees the chain
advancing in real time, never rewinding. A tiny dot indicator pulses
infinitely with `liveDot 2s ease-in-out` so the page feels alive even
when no events are arriving.

This is the most visually emotional moment in the entire product. It's
the only animation that explicitly conveys: **something just happened,
and it cannot be undone**.

### Face 5 — The Safe-Wallet UX: Calm in the wallet, sharp at the limit

**Files:** `frontend/app/billing/page.tsx` + the `BillingPortalSection` in `frontend/app/settings/page.tsx`

Two surfaces serve different jobs:

- `/billing` is the **plan-comparison page** — three plan cards (Free / Pro / Studio / Enterprise), monthly/yearly toggle pill at the top. The "highlighted" plan gets a 2px amber border and a "Most Popular" pill at top-right. Each plan lists features with emerald `Check` icons (Lucide). Clean, restrained, magazine-like.
- `/settings → Billing & Credits panel` is the **everyday "how much do I have, how do I top up" surface** — different job, different design.

In the settings panel, the token balance is the hero number — 28px,
weight 700, tabular-nums. The plan name sits next to it in a smaller
column. When balance falls **below the BillingGuard threshold** of 10
estimated tokens, the entire container flips into low-balance state:

- Container border: `--border-light` (#e5e5e5) → `--error` (#dc2626)
- Balance number color: `--text-primary` (#0d0d0d) → `--error` red
- A small caption appears: "Below minimum — top up to continue" in red, weight 500
- The "Top Up Credits" button transforms: from neutral `--bg-hover` filling to **warm amber** `--accent` filling — a nudge, not a panic banner

The button-transformation is the clever part. Most products turn the
warning red and the action red. VOS3 turns the warning red and the
**solution amber** — guiding the eye from "there's a problem" to "here's
how you fix it" in the same glance. No modal, no dialog, no pop-up. Just
the panel itself, calmly suggesting the next step.

Both buttons (`Top Up Credits` and `Manage Subscription`) issue a POST and
redirect via `window.location.href` to Stripe — VOS3 never holds card data.
While buttons are in flight, label text rotates: "Top Up Credits" →
"Redirecting…", and opacity drops to 0.6. On error, a small crimson alert
strip appears at the bottom of the panel — never a stack trace, never a
generic "Error". The button returns to its idle state and stays clickable.

---

## Part II — A Day in the Pixels: The User Journey

This is what a real session looks like, from cold-start to laptop close.
Every screen referenced is shipping today.

### Frame 1 — The Cold Start (08:42)

The user opens `vos3.app/` in a fresh tab. The page renders the **home
hero** (`frontend/app/page.tsx`):

- A 64×64 amber gradient square — the "V" mark — fades in via Framer Motion `initial={{ scale: 0.8, opacity: 0 }}` → `animate={{ scale: 1, opacity: 1 }}`
- The 44px headline lands 100ms later: "Build any app with AI" (`y: 20 → 0`, opacity)
- The subhead, two CTAs, and the 6 project-type cards stagger in at +100ms intervals
- The 3 feature cards (`Sparkles`, `Zap`, `Shield` from Lucide) follow at +500ms
- Total time to fully settled: ~700ms

The hero feels like a magazine opening, not a SaaS landing page.
There's no carousel, no parallax, no video background. Just generous
white space, the amber V, and confident typography. The user clicks
**Start Building**.

### Frame 2 — The Login (08:42:15)

Clerk takes over with its native modal — same Inter typeface (Clerk
defaults match VOS3's brand voice closely enough that there's no jarring
brand handoff). The user signs in with Google. Clerk returns. Cookies set.
The page now has an authenticated session.

### Frame 3 — The First Glance (08:42:25)

The user lands on the home page again, but now the chrome appears: the
**260px Navigation sidebar** slides in from the left (no animation —
instant render) carrying five grouped sections:

```
Workspaces              ← expanded by default
  ▸ AI Chat             ← icon: ChatIcon (hand-rolled SVG)
  ▸ AI Terminal
  ▸ Code Builder
  ▸ Agent Resources
  ▸ Project Studio

Operation               ← expanded
  ▸ Dashboard
  ▸ Workflows
  ▸ Analytics

Insights                ← expanded
  ▸ Knowledge Base
  ▸ Learning Memory

Tools
  ▸ Tools

System                  ← collapsed by default
  ▸ Transparency  ← NEW v20.3 (shield + checkmark icon)
  ▸ Kernel
  ▸ Metrics
  ▸ Health
  ▸ Billing

[Settings]              ← anchored at the bottom
```

Source: `frontend/components/shared/Navigation.tsx:269-310`. Active route
gets a soft `--bg-active` fill and the icon takes the warm amber `--accent`.
Hover gets `--bg-hover`. The sidebar can collapse to 60px via the toggle —
still showing icons, hiding labels. Section headers fold/unfold
independently. The "System" section is collapsed because most users don't
need to look at kernel telemetry every day.

### Frame 4 — Opening the Chat (08:43)

The user clicks "AI Chat" in the sidebar. Routing transitions in <100ms
(Next.js prefetch). And — **the world goes dark**.

The chat page is the brand's cinematic exception:
- Background: `#0a0a0a` with a radial gradient overlay (`radial-gradient(ellipse at center, #1a1a1a 0%, #0a0a0a 70%)`)
- Film-grain SVG noise at 3% opacity layers on top
- The "V" logo renders at 200-weight, 48 pixels, near-pure white, centered
- Below the V, in `rgba(255,255,255,0.4)`: "Start a conversation"
- The model selector pill sits in the top-left at 11px in `rgba(255,255,255,0.6)`
- "ESC to toggle UI" hint in the top-right at `rgba(255,255,255,0.3)`
- The composer at the bottom has `backdropFilter: blur(20px)` over a 5%-white background — true glass

And — quietly — **the Hardware HUD has just appeared at bottom-left**.
The user might not notice it at first. That's deliberate. It's there if
they look, ignorable if they don't.

### Frame 5 — The First Conversation (08:43:20)

The user types "Help me draft a proposal for a new client". They press Enter.

- The user message appears centered at the bottom of the canvas, padded `12px 24px`, `rgba(0,0,0,0.7)` background, 4px radius, animated in via `subtitleIn` keyframe (`opacity: 0; transform: translateY(20px) → 0`)
- 200ms later, the assistant slot appears with the 3-dot pulsing loader (`@keyframes pulse` from `globals.css:91`) — three 6px dots, each delayed by 200ms, fading between 0.3 and 1.0 opacity
- The token stream begins. Words materialize letter-by-letter in the assistant bubble, weight 300, 20px Inter, `rgba(255,255,255,1)` text on `rgba(0,0,0,0.7)` background

In the bottom-left corner, the HUD is alive: the bar shows 38%, the EWMA
hovers around 35%, the pill says **FULL QUALITY** in emerald. The user is
getting the full Sonnet model. The world is calm.

### Frame 6 — The Hidden Workforce (08:43:45)

While the user is reading the assistant's response, **the Hardware HUD
quietly transforms**. The system has detected a pressure spike — maybe a
batch of users hit refresh at the top of the hour. Over 500ms:

- Pill: emerald → amber, **FULL QUALITY** → **EFFICIENCY MODE**
- Bar: green fills to 87%, transitions amber
- Hint: `opus/sonnet` → `haiku`

The user's next message is faster. Slightly. They might not notice. The
HUD did notice — and quietly told them the truth. This is VOS3's central
design promise: **the system is honest about its state, but never panics
about it**.

### Frame 7 — The Background Hum (09:15)

The user is back in their browser, working in another tab. In a third
tab, VOS3's tab title pulses: "(1) VOS3" — a proactive insight has
arrived. They click back.

A small notification card slides into view from the right (Framer Motion
`initial={{ x: 20, opacity: 0 }}` → `animate={{ x: 0, opacity: 1 }}`). It
sits in the upper-right of the v-core dashboard. Its anatomy:

```
┌─────────────────────────────────────────────────────┐
│  [ShieldAlert]  Revenue Protection · URGENT         │  ← red-bordered pill
│  3 clients have unpaid invoices >30 days.           │  ← body
│  Total: 8,400 ₪                                     │
│                                                     │
│  [📧 Send Reminder]  [📅 Schedule Call]  [Dismiss]  │  ← Lucide-icon CTAs
└─────────────────────────────────────────────────────┘
```

The card's left border carries the urgency color — crimson at 1px for
**URGENT/HIGH**, amber for **MEDIUM**, no border tint for **INFO**. The
category pill in the upper-left uses a category-derived background at 8%
opacity. No flashing. No shaking. The chrome carries the weight; the
content stays calm.

### Frame 8 — The Decision Moment (09:16)

The user clicks **📧 Send Reminder**. Behind the scenes:
- The agent collaboration view briefly opens in a sliding side panel (300px from the right)
- The Architect (amber RACI chip — Accountable) speaks first: "I'll structure a polite-but-firm reminder…"
- The Writer agent (emerald — Responsible) drafts: "Hi [Name], I hope you're well…"
- The Reviewer agent (indigo — Consulted) checks tone, offers two changes
- A **decision card** materializes at the bottom: "Send to all 3 clients? [Approve] [Refine]"
- The user clicks Approve. The card's button label rotates to "Sending…" with opacity 0.6, then "✓ Sent to 3 recipients" with a 500ms emerald flash

The insight card itself transitions: `InsightStatus.NEW` → `VIEWED` → `ACTED`.
Visually, the card fades to 60% opacity and a small "✓ acted" stamp lands
on the right side in emerald. It stays on screen, slowly drifting toward
the bottom of the feed as newer items arrive.

### Frame 9 — The Trust Check (10:30)

Out of curiosity, the user clicks **System → Transparency** in the
sidebar. The Explorer page renders:

- Static hero ("Transparency Explorer · MMR AUDIT CHAIN" pill) lands instantly — Server Component, < 80ms LCP
- Three invariant cards in a `repeat(3, 1fr)` grid below: **Collision Resistance · 2¹²⁸ ops** (emerald), **Append Complexity · O(log N)** (blue), **BSS Footprint · 2,048 B** (violet) — each with the value at 28px / 700 / tabular-nums
- The dynamic MMR widget streams in 200ms later via Suspense
- The centerpiece card shows the live SHA-256 root in monospace, 15px, with a small emerald dot pulsing infinitely next to "KERNEL LIVE"

The user watches for a few seconds. Suddenly — **green pulse**. The root
hash flashes from `#16a34a` to `#4ade80` and back over 0.9s. A radial
glow expands and fades. A new entry slides in at the top of the proof
history list with the new root, current timestamp, leaf count incremented.
The user just witnessed a syscall being recorded into the audit chain in
real time, cryptographically tamper-proof.

This is the moment that cannot be faked. It's the visual proof the brand
promises.

### Frame 10 — Checking the Books (11:45)

The user is about to leave for lunch. They glance at **Settings → Billing & Credits**:

- Token Balance: 4,231 (tabular-nums, weight 700)
- Plan: Pro
- Two buttons: `[Top Up Credits]` (neutral) and `[Manage Subscription]` (neutral)
- The OCC compliance footer line: "Credits are debited atomically per request (Convex OCC — overdraft impossible)."

Healthy state. Container border is the standard `--border-light` (#e5e5e5).
The user closes the tab.

### Frame 11 — The Settle (17:00)

End of day. The user pops back to the dashboard for one last check. The
proactive feed has accumulated: the morning's URGENT insight is now ACTED
and grayed at 60% opacity. Two new MEDIUM insights have arrived. One
INFO weekly summary card sits at the bottom — purple-pilled, calm, ready
to be read tomorrow.

The user closes the laptop.

The VOS3 backend continues. The kernel is still recording syscalls into
the MMR. The proactive engine is still scanning data. The next morning,
when they open the laptop, the green pulse will be waiting — proving that
the audit chain advanced overnight, that nothing was lost, that everything
they did yesterday is mathematically locked in.

---

## Part III — The Animation Library

Animation is rationed. The brand's **"Calm"** descriptor is enforced at
the easing curve: nothing animates with personality, everything animates
with intent.

### Three durations, three purposes

| Duration | Use | Where |
|----------|-----|-------|
| **150ms** | Hover, focus, color swap | Button hover, link color, RACI chip activate |
| **300–500ms** | State change | Efficiency Mode crossfade, error border appear, card status transition |
| **800ms** | Value transition | Pressure bar width, opacity fades on data swap |

All easing is `ease` or `ease-in-out`. **No bounces. No overshoots. No
spring physics.**

### Where Framer Motion lives (12 files)

`grep -rln "framer-motion" frontend/` returns 12 files. The pattern:
- **Page-level entries:** `app/page.tsx`, `app/projects/page.tsx`, `app/marketplace/page.tsx` — staggered fade-in for hero + tile grids
- **List staggers:** New items in agents, projects, marketplace use `transition={{ delay: 0.1 + i * 0.05 }}`
- **Notification entries:** Insight cards use `initial={{ x: 20, opacity: 0 }}` → `animate={{ x: 0, opacity: 1 }}`
- **No exit animations on critical paths** — clicking a destructive button shows the new state immediately, not a 300ms farewell

### Where pure CSS keyframes live

For hot-path UI, the team explicitly chose CSS over Framer Motion to
avoid the JS-bridge cost on every frame:

| Animation | Trigger | File |
|-----------|---------|------|
| `voice-pulse` | Mic active | `globals.css:91` |
| `voice-bar` | Voice waveform | `globals.css:100` |
| `voice-pulse-ring` | Mic outer ring | `globals.css:109` |
| `voice-dot` | Voice indicator | `globals.css:120` |
| `pulse` (chat) | Loading dots | inline `app/chat/page.tsx` |
| `subtitleIn` | New chat message | inline `app/chat/page.tsx` |
| `fadeIn` | Empty-state hero | inline `app/chat/page.tsx` |
| `mmrPulse` | MMR root advance | inline `MMRLiveWidget.tsx` |
| `liveDot` | Kernel alive indicator | inline `MMRLiveWidget.tsx` |
| `slideIn` | Proof history entry | inline `MMRLiveWidget.tsx` |
| `hudDot` | HUD live indicator | inline `HardwareHUD.tsx` |

---

## Part IV — The Grid System

### The shell

```
┌────────────────────────────────────────────────────────────┐
│  Navigation (260px / collapsed: 60px) │  <main flex: 1>    │
│   - Workspaces                        │                    │
│   - Operation                         │   { children }     │
│   - Insights                          │                    │
│   - Tools                             │                    │
│   - System (collapsed by default)     │                    │
│  ─────────────────────────────────────│                    │
│   Settings (anchored bottom)          │                    │
└────────────────────────────────────────────────────────────┘
```

Source: `frontend/components/shared/ClientLayout.tsx`. Sidebar background
is `--bg-sidebar` (#f9f9f9). Active route gets `--bg-active` plus the
amber-tinted icon. Border-right is `--border-light`.

### Page max-widths

| Page | Layout | Max width |
|------|--------|-----------|
| `/` (home) | Centered single column | 960px |
| `/chat` | Cinematic full-bleed (no sidebar gap) | 100vw |
| `/explorer` | Hero + 3-card invariant grid + dynamic widget | 960px |
| `/builder` | Two-column (prompt / output), 1fr/1fr | 1400px |
| `/agents` | Sidebar 320px + canvas + decision panel 360px | 1600px |
| `/billing` | Plan grid (3-col desktop, 1-col mobile) | 1100px |
| `/settings` | Sectioned single column | 800px |
| `/health` | Service grid `auto-fit minmax(250px, 1fr)` + widgets | 1400px |

All pages use `padding: 24px` or `40px` outer margin and `0 auto` to
center within max-width. Mobile breakpoints exist but VOS3 is desk-first —
built for the focused-work session, not the in-line phone interruption.

### The card pattern (used everywhere)

```css
{
  padding: 16px or 20px or 24px;
  background: var(--bg-secondary);     /* #f9f9f9 */
  border: 1px solid var(--border-light); /* #e5e5e5 */
  border-radius: var(--radius-md);      /* 12px */
}
```

When a card carries urgency or status, the **border color** changes —
not the background. `--error` red border for low-balance, `#bbf7d0` (a
pale green) for live MMR, `--accent` amber for highlighted plans. The
content inside stays calm; the chrome carries the weight.

---

## V — The Visual Hierarchy of Trust

VOS3 communicates trust through **four progressive visual cues**, each
earning more attention than the last:

| Cue | Visual | When |
|-----|--------|------|
| **1. Calm presence** | Tabular numbers, no animation | Token balance, MMR leaf count, plan name |
| **2. Live signal** | Single pulsing dot in `--success` emerald | Kernel LIVE, MMR fresh, voice ready, HUD alive |
| **3. Active transition** | 500ms color crossfade | EWMA → Efficiency Mode, RACI chip activate, button "Redirecting…" |
| **4. Compliance event** | Green pulse glow + history list entry | MMR root change, OP_LOCAL_ENFORCEMENT_EU emit |

The user is never assaulted by movement. Movement is reserved for moments
that **deserve to be noticed** — and even then, it lasts 0.9 seconds and
fades to nothing.

This is the most important UX promise VOS3 makes: **the system is alive,
the system is honest, and the system is calm.** You can leave the page
open in a corner of your monitor and know — at a glance — that things are
working.

---

## VI — The Closing Frame

A user who's been with VOS3 for a month no longer notices the brand. They
notice the green pulse when their request is recorded. They notice the
HUD pill go amber and the response come back faster. They notice the red
border when their balance drops, and the amber Top-Up button right below
it. They notice the agent colors — A for Architect, R for Engineer, C for
Reviewer — and they read the conversation by its color flow without
reading the labels.

This is what the brand is for: **so that, eventually, the user stops seeing
the brand and starts trusting the system.**

---

*VOS3 UI Visual Walkthrough — v20.3.0 — April 25, 2026*
*"The system is alive. The system is honest. The system is calm."*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
