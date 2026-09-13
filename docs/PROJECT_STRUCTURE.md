# VOS3 Project Structure

> **Last Updated**: February 2026
> **Version**: 3.0
> **Status**: Active Development

## Overview

VOS3 follows enterprise SaaS best practices with a monorepo structure separating frontend and backend concerns while maintaining shared configuration and documentation at the root level.

```
VOS3/
├── frontend/          # Next.js 14 React Application
├── backend/           # Python FastAPI Server
├── docs/              # Project Documentation (Truth Source)
├── data/              # Runtime Data Storage
├── docker/            # Container Configurations
├── scripts/           # Automation Scripts
├── .env               # Environment Variables
├── CLAUDE.md          # AI Context Guide
└── README.md          # Quick Start Guide
```

---

## Frontend Architecture (`/frontend`)

### Technology Stack
| Technology | Version | Purpose |
|------------|---------|---------|
| Next.js | 14.2.25 | React Framework with SSR |
| React | 18.3.1 | UI Library |
| TypeScript | 5.3.3 | Type Safety |
| CSS Variables | - | Theming System |

### Directory Structure

```
frontend/
├── app/                    # Next.js App Router Pages
│   ├── layout.tsx         # Root Layout (Server Component)
│   ├── globals.css        # Global Styles + CSS Variables
│   ├── page.tsx           # Dashboard Home
│   ├── chat/              # AI Chat Interface
│   ├── builder/           # Code Generation UI
│   ├── agents/            # Agent Management
│   ├── v-core/            # Business OS Dashboard
│   ├── memory/            # Learning Memory UI
│   ├── rag/               # Knowledge Base
│   ├── metrics/           # Router Observability
│   ├── health/            # System Health
│   ├── settings/          # Configuration
│   ├── workflows/         # Workflow Builder
│   ├── analytics/         # Business Analytics
│   ├── github/            # GitHub Integration
│   ├── plugins/           # Plugin Marketplace
│   └── billing/           # Subscription Management
│
├── components/            # React Components
│   ├── shared/           # Reusable Components
│   │   ├── Navigation.tsx
│   │   ├── ClientLayout.tsx
│   │   ├── VoiceInput.tsx
│   │   └── EmptyState.tsx
│   ├── agents/           # Agent UI Components
│   ├── builder/          # Code Builder Components
│   ├── chat/             # Chat Components
│   ├── v-core/           # Business OS Components
│   └── observability/    # Metrics Components
│
├── hooks/                # Custom React Hooks
│   ├── useChat.ts        # Chat API
│   ├── useCodegen.ts     # Code Generation
│   ├── useAgents.ts      # Agent Management
│   ├── useVCore.ts       # Business OS State
│   ├── useMemory.ts      # Learning Memory
│   ├── useSettings.ts    # Configuration
│   └── useVoiceInput.ts  # Voice Recognition
│
├── public/               # Static Assets
├── styles/               # CSS Modules
└── lib/                  # Utility Functions
```

### Path Alias
- `@/*` maps to frontend root
- Example: `import { useChat } from '@/hooks/useChat'`

---

## Backend Architecture (`/backend`)

### Technology Stack
| Technology | Version | Purpose |
|------------|---------|---------|
| FastAPI | 0.109+ | Web Framework |
| Uvicorn | 0.27+ | ASGI Server |
| Python | 3.8+ | Runtime |
| ChromaDB | - | Vector Storage |
| LangGraph | - | Agent Orchestration |

### Directory Structure

```
backend/
├── main.py               # Application Entry Point
├── api/                  # API Route Handlers
│   ├── chat_routes.py           # /api/chat/*
│   ├── codegen_routes.py        # /api/codegen/*
│   ├── agents_routes.py         # /api/agents/*
│   ├── v_core_routes.py         # /api/v-core/*
│   ├── memory_routes.py         # /api/memory/*
│   ├── metrics_routes.py        # /api/metrics/*
│   ├── settings_routes.py       # /api/settings/*
│   ├── voice_routes.py          # /api/voice/*
│   ├── billing_routes.py        # /api/billing/*
│   ├── team_routes.py           # /api/teams/*
│   └── [18 more route files]
│
├── ai/                   # AI Engine
│   ├── llm/             # LLM Providers
│   │   └── providers.py         # Multi-provider support
│   ├── agents/          # Agent Orchestration
│   │   └── multi_agent.py       # LangGraph agents
│   ├── codegen/         # Code Generation
│   │   └── generator.py
│   ├── rag/             # RAG Implementations
│   └── memory/          # AI Memory
│
├── core/                 # V-Core Business OS
│   ├── control_plane.py         # Users, Orgs, RBAC
│   ├── business_core.py         # Entities, Records
│   ├── workflow_engine.py       # Automation
│   └── mission_control.py       # Monitoring
│
├── src/                  # Core Utilities
│   ├── efficiency.py            # Smart Router
│   ├── observability.py         # Metrics Tracking
│   ├── llm.py                   # LLM Abstraction
│   └── cache.py                 # Caching Layer
│
├── memory/               # Persistent Memory
│   └── dev_memory.py            # ChromaDB Storage
│
├── services/             # Service Layer
├── integrations/         # External Services
├── middleware/           # Request Middleware
├── config/               # Configuration Files
│   └── router.yaml              # Smart Router Config
├── db/                   # Database Layer
│   ├── convex.py
│   └── schema.sql
└── tests/                # Test Suite
```

---

## Smart Router System

The system uses intelligent model routing for cost optimization (40-60% savings).

### Model Priority
| Model | Provider | Priority | Use Case |
|-------|----------|----------|----------|
| Claude Opus | Anthropic | 10 | Complex tasks |
| Claude Sonnet | Anthropic | 8 | Balanced tasks |
| GPT | OpenAI | 7 | Architecture |
| Gemini | Google | 6 | Research |

### Role-Based Selection
| Role | Default | High Complexity (>=9) |
|------|---------|----------------------|
| architect | gpt | gpt |
| reviewer | claude-opus | claude-opus |
| researcher | gemini | claude-opus |
| coding | claude-sonnet | claude-opus |

---

## Data Flow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    Frontend     │────▶│    Backend      │────▶│   AI Providers  │
│   (Next.js)     │     │   (FastAPI)     │     │ (OpenAI/Claude) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                        │
        │                       ▼                        │
        │               ┌─────────────────┐              │
        │               │   Smart Router  │◀─────────────┘
        │               │  (Cost Optimize)│
        │               └─────────────────┘
        │                       │
        ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│     Browser     │     │  ChromaDB/      │
│   (User View)   │     │  PostgreSQL     │
└─────────────────┘     └─────────────────┘
```

---

## Environment Configuration

### Required for Production
```env
# AI Providers
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Database
CONVEX_URL=
CONVEX_DEPLOY_KEY=

# Authentication
CLERK_PUBLISHABLE_KEY=
CLERK_SECRET_KEY=

# Payments
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
```

### Optional
```env
GOOGLE_API_KEY=           # Gemini models
OLLAMA_BASE_URL=          # Local models
REDIS_URL=                # Caching
LANGFUSE_*=               # Observability
```

---

## Development Commands

### Backend
```bash
cd backend
pip install -r ../requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Testing
```bash
cd backend && pytest tests/ --cov=src
cd frontend && npm run lint && npm run type-check
```

---

## Best Practices Applied

1. **Separation of Concerns**: Frontend/Backend split
2. **Feature-Based Organization**: Components grouped by feature
3. **Service Layer Pattern**: Abstracted business logic
4. **API Versioning Ready**: Route-based API structure
5. **Environment-Based Config**: Secrets via env variables
6. **Type Safety**: TypeScript frontend, Pydantic backend
7. **Cost Optimization**: Smart model routing
8. **Persistent Learning**: Vector memory system
9. **Observable**: Built-in metrics and health checks
10. **Extensible**: Plugin architecture

---

*This document is the source of truth for VOS3 project structure.*
