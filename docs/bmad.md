# BMAD — Breakthrough Method for Agile AI-Driven Development

## Overview

BMAD is an open-source framework (MIT License) that brings structure to AI-assisted development. Instead of ad-hoc prompting, it uses **specialized AI agents** and **documented workflows** to turn LLM power into predictable, maintainable engineering output.

- **GitHub**: [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD)
- **Website**: [bmadcodes.com](https://bmadcodes.com/)
- **Latest Version**: BMad v6 Alpha

## The Core Problem It Solves

When you vibe-code with AI, you get fast results but lose **governance, traceability, and architectural integrity**. BMAD fixes this by enforcing **Spec-Driven Development (SDD)** — the AI follows documented specs, not your latest chat message. This dramatically reduces hallucinations and creates auditable artifacts at every step.

## The 4-Phase Lifecycle

| Phase | What Happens | Key Artifacts |
|-------|-------------|---------------|
| **Discovery & Planning** | Define the problem, users, MVP scope | Product Brief, PRD |
| **Architecture** | System design, technical decisions | Architecture Doc, Epics & Stories |
| **Implementation** | Build story-by-story with quality gates | Code, Tests, Reviews |
| **Operations** | Deploy, monitor, iterate | Deployment configs, Sprint plans |

## Specialized Agents

Each agent is a **persona with expertise**, defined in markdown files:

- **Product Manager** — Requirements, scope, PRDs
- **Architect** — System design decisions
- **Scrum Master** — Process, sprints, story management
- **Developer** — Implementation per story
- **UX Designer** — User experience strategy
- **QA (Quinn)** — Testing and quality automation
- Plus more specialists depending on modules installed

## How It Works in Practice

1. You run a **slash command** like `/create-prd`
2. The appropriate **agent persona** activates (e.g., Product Manager)
3. The agent **interviews you** or analyzes existing docs
4. It produces a **versioned artifact** (e.g., `docs/prd.md`)
5. The next phase **references that artifact** as its input
6. Everything is **git-tracked** — full traceability

## Key Concepts

### Artifacts as Contracts
PRDs, architecture docs, and stories are versioned markdown files that agents follow — not throwaway chat messages.

### Human-in-the-Loop
You approve at each phase gate before proceeding. Nothing advances without your sign-off.

### Scale-Adaptive Intelligence
Adjusts planning depth based on project complexity — from quick bug fixes to enterprise systems.

### Party Mode
Multiple agents collaborate in a single session for complex tasks.

### IDE-Native
Works as slash commands in Claude Code, Cursor, Windsurf, and other AI IDEs.

## Modular Architecture

| Component | Purpose |
|-----------|---------|
| **BMad Core** | Universal engine — Collaboration Optimized Reflection Engine (CORE) for human-AI interaction |
| **BMad Method** | Agile workflows for software development running on top of Core |
| **BMad Builder** | Create custom agents, workflows, and domain-specific modules |

## Optional Extension Modules

| Module | Purpose |
|--------|---------|
| **BMad Builder (BMB)** | Create custom agents and workflows |
| **Test Architect (TEA)** | Enterprise test strategy, 8 workflows, risk-based gates |
| **Game Dev Studio (BMGD)** | Unity, Unreal, Godot workflows |
| **Creative Intelligence Suite (CIS)** | Innovation, brainstorming, design thinking |

## Slash Commands / Workflows

### Phase 1: Discovery & Planning
- `/product-brief` — Problem definition, target users, MVP scope
- `/create-prd` — Full requirements with personas, metrics, risks
- `/bmad-help` — Intelligent guidance (contextual, module-aware)

### Phase 2: Architecture
- `/create-architecture` — Technical decisions and system design
- `/create-epics-and-stories` — Break work into prioritized stories
- `/sprint-planning` — Sprint initialization and tracking

### Phase 3: Implementation
- `/create-story` — Story specification
- `/dev-story` — Code implementation per story
- `/code-review` — Quality validation and feedback

### Phase 4: Quick Workflows (Bug Fixes / Small Features)
- `/quick-spec` — Codebase analysis producing tech-specs with stories
- `/dev-story` — Implementation
- `/code-review` — Quality gates

## Artifacts Produced

- **Product Briefs** — Problem definition, target users, MVP scope
- **PRDs** — Requirements with personas, metrics, risk assessment
- **Architecture Specifications** — System design, technical decisions
- **User Stories** — Prioritized with acceptance criteria
- **Sprint Plans** — Task breakdown, estimates
- **Test Specifications** — Via QA agent or Test Architect module
- **Code Implementations** — With context from prior artifacts
- **Review Feedback** — Structured quality validation

## Installation (Standard)

```bash
npx bmad-method install
```

For CI/CD automation:
```bash
npx bmad-method install --directory /path/to/project --modules bmm --tools claude-code --yes
```

This creates:
- `.claude/skills/` — Agent definitions and workflow commands
- `docs/` — Generated documentation and artifacts
- Project-specific slash commands accessible in your AI IDE

## Key Differentiators

1. **Reduces hallucinations** — Documentation-first development means AI follows specs, not guesses
2. **Git-based versioning** — Every artifact is a versioned asset with full traceability
3. **Markdown agent definitions** — Each file is a self-contained agent with role, personality, and capabilities
4. **Free & open-source** — MIT License, no paywalls, always free

## VOS3 Custom Implementation

VOS3 has a custom BMAD implementation in `backend/bmad/` with:
- 15 specialized agents across 9 development phases
- LangGraph-based workflow orchestration
- Session state management with approval gates
- Vercel deployment integration
- Project Studio UI at `/studio` with Wizard, Expert, and Party modes

## Sources

- [BMAD-METHOD GitHub](https://github.com/bmad-code-org/BMAD-METHOD)
- [BMad Codes Official Site](https://bmadcodes.com/)
- [Applied BMAD - Reclaiming Control in AI Development](https://bennycheung.github.io/bmad-reclaiming-control-in-ai-dev/)
- [BMAD: The Agile Framework That Makes AI Predictable](https://dev.to/extinctsion/bmad-the-agile-framework-that-makes-ai-actually-predictable-5fe7)
- [What is BMAD-METHOD? - Medium](https://medium.com/@visrow/what-is-bmad-method-a-simple-guide-to-the-future-of-ai-driven-development-412274f91419)
