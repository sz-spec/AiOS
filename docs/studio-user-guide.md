# Project Studio User Guide

## Overview

Project Studio is VOS3's project development environment powered by the BMAD Method (Breakthrough Method for Agile AI-Driven Development). It guides you through building a full AI-based solution — from initial idea to production-ready code — using specialized AI agents that handle each phase of development.

## Getting Started

1. Open Studio at `/studio`
2. Click **New Project** or select an existing project
3. Provide your project name, description, and type (SaaS, API, Landing Page, Mobile App, etc.)
4. Choose a development mode: **Guided**, **Expert**, or **Party**

## Development Modes

Studio offers three modes for running a BMAD development session. All three operate on the same underlying project and backend workflow — they're different interfaces over the same data. You can switch between modes at any time using the mode selector in the top-right corner.

### Guided Mode

The default, step-by-step experience. Best for users who want structure and clear progression through each development phase.

**How it works:**
- Walks you through the full BMAD workflow one phase at a time
- Each phase produces artifacts (documents, specs, code) that feed into the next
- Approval gates let you review and approve before moving forward
- Progress is shown visually with a phase timeline

**BMAD Phases (in order):**
1. **Product Brief** — Define the project vision, target users, and core value proposition
2. **PRD (Product Requirements Document)** — Detail functional requirements, user stories, and success metrics
3. **UX Design** — Specify user flows, wireframes, and interaction patterns
4. **Architecture** — Design the system architecture, tech stack, data models, and API structure
5. **Epics & Stories** — Break work into prioritized epics and user stories with acceptance criteria
6. **Sprint Planning** — Organize stories into sprints with effort estimates
7. **Development** — AI agents write the actual code following the story specs
8. **Code Review** — Automated quality review with security, performance, and style checks

**Best for:** First-time users, new projects, teams that want a documented development process.

### Expert Mode

A dashboard-style control panel for experienced users who want direct access to all project components without the linear walkthrough.

**Tabs available:**
- **Overview** — Project summary, current phase status, key metrics
- **Agents** — View and interact with individual BMAD agents (PM, Architect, Developer, QA, etc.)
- **Artifacts** — Browse all generated documents, specs, and code files
- **Git** — Manage commits, branches, and version history
- **Deploy** — Trigger deployments and monitor deployment status

**How it works:**
- Navigate freely between tabs — no enforced order
- Directly inspect and edit artifacts
- Trigger individual agents on demand
- Manage git operations manually

**Best for:** Experienced developers, projects that don't need the full linear workflow, debugging and fine-tuning.

### Party Mode

A multi-agent collaboration session where specialized AI agents discuss and solve problems together in real-time.

**Layout:**
- **Left panel** — Agent sidebar showing which agents are active
- **Center panel** — Live chat feed where agents discuss, debate, and propose solutions
- **Right panel** — Decision board tracking conclusions and agreed-upon decisions

**How it works:**
- You pose a question or problem to the team
- Multiple BMAD agents (PM, Architect, Designer, Developer, QA) engage in a collaborative discussion
- Each agent brings their specialized perspective to the conversation
- The decision board captures key conclusions as they emerge
- You can intervene, steer the discussion, or approve decisions

**Best for:** Complex design decisions, architecture debates, brainstorming sessions, getting multiple expert perspectives on a problem.

## AI Agents

Behind the scenes, Studio uses specialized AI agents — each with their own role, expertise, and preferred LLM:

| Agent | Role | Specialty |
|-------|------|-----------|
| Business Analyst | Discovery & briefs | Market research, user needs, value proposition |
| Product Manager | Requirements | PRDs, feature prioritization, success metrics |
| UX Designer | Design | User flows, wireframes, interaction patterns |
| Architect | System design | Tech stack, data models, APIs, infrastructure |
| Scrum Master | Planning | Epics, stories, sprints, effort estimation |
| Developer | Implementation | Writing production-ready code |
| QA Engineer | Quality | Code review, testing, security analysis |

Each agent is routed to the optimal LLM for their task via the Smart Router — research-heavy agents use cost-effective models, while code generation and review agents use more capable models. This keeps quality high and costs low.

## Project Artifacts

As you progress through phases, Studio generates and stores artifacts:

- **Planning artifacts** (`docs/planning-artifacts/`) — Briefs, PRDs, UX specs, architecture docs, epics
- **Implementation artifacts** (`docs/implementation-artifacts/`) — Stories, sprint status, code review reports
- **Source code** (`src/`) — Generated application code

All artifacts are version-controlled with git. Each phase completion triggers an automatic commit, so you always have a full history of how the project evolved.

## Tips

- **Start with Guided Mode** if you're new — it ensures nothing gets skipped
- **Switch to Expert Mode** when you need to drill into a specific area
- **Use Party Mode** when you're stuck on a decision and want multiple perspectives
- Every project gets its own git repo — your work is always saved
- You can return to any project from the Studio dashboard and pick up where you left off
