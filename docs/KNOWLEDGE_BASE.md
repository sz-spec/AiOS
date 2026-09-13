# VOS3 Knowledge Base - Project Truth

> **Purpose**: This document serves as the canonical source of truth for VOS3.
> **Usage**: Load into Learning Memory on project initialization.
> **Update Policy**: Must be updated when architecture or key decisions change.

---

## Project Identity

```yaml
name: VOS3
type: AI Operating System
version: 3.0
status: Active Development
updated: February 2026
```

---

## Core Architecture

### System Components
1. **Frontend** - Next.js 14 React application at `/frontend`
2. **Backend** - FastAPI Python server at `/backend`
3. **Memory** - ChromaDB vector storage for persistent learning
4. **Router** - Smart model selection for cost optimization

### Key Directories
- `/frontend/app` - 16 page routes
- `/frontend/hooks` - 8 API hooks
- `/frontend/components` - Feature-organized components
- `/backend/api` - 29 route files
- `/backend/ai` - AI engine (LLM, agents, RAG)
- `/backend/core` - V-Core business OS
- `/backend/memory` - Persistent memory system
- `/docs` - Project documentation (truth source)

---

## Navigation Structure

```
Workspaces:
  - AI Chat (/chat)
  - Code Builder (/builder)
  - Agents (/agents)

Operation:
  - Dashboard (/v-core)
  - Workflows (/workflows)
  - Analytics (/analytics)

Insights:
  - Knowledge Base (/rag)
  - Learning Memory (/memory)

Integrations:
  - GitHub (/github)
  - Plugins (/plugins)

System:
  - Metrics (/metrics)
  - Health (/health)
  - Billing (/billing)
  - Settings (/settings)
```

---

## API Endpoints

### Primary (Active)
| Prefix | Purpose |
|--------|---------|
| `/api/chat` | AI conversations |
| `/api/codegen` | Code generation |
| `/api/agents` | Agent management |
| `/api/v-core` | Business OS |
| `/api/memory` | Learning Memory |
| `/api/metrics` | Observability |
| `/api/settings` | Configuration |
| `/api/voice` | Voice commands |

### Secondary (Available)
| Prefix | Purpose |
|--------|---------|
| `/api/billing` | Payments |
| `/api/teams` | Collaboration |
| `/api/analytics` | Analytics |
| `/api/github` | GitHub sync |
| `/api/inbox` | Notifications |
| `/api/templates` | Templates |
| `/api/plugins` | Plugins |

---

## Smart Router Configuration

### Models (Priority Order)
1. Claude Opus (Anthropic) - Priority 10
2. Claude Sonnet (Anthropic) - Priority 8
3. GPT (OpenAI) - Priority 7
4. Gemini (Google) - Priority 6

### Role Assignments
| Role | Default Model | Complex Tasks |
|------|---------------|---------------|
| architect | gpt | gpt |
| reviewer | claude-opus | claude-opus |
| researcher | gemini | claude-opus |
| coding | claude-sonnet | claude-opus |

### Cost Optimization
- Automatic model selection based on complexity
- 40-60% cost reduction achieved
- Fallback chain: Claude Opus → GPT → Gemini

---

## V-Core Business OS

### Modules
1. **Control Plane** - Users, organizations, RBAC
2. **Business Core** - Custom entities and records
3. **Workflow Engine** - Automation with triggers/actions
4. **Mission Control** - Monitoring and approvals

### Entity Types
- Organizations
- Users
- Roles
- Custom Entities
- Records
- Workflows

---

## Memory System

### Technology
- ChromaDB vector database
- Sentence transformers (all-MiniLM-L6-v2)
- Persistent storage in `/data/memory`

### Memory Types
| Type | Purpose |
|------|---------|
| conversation | Chat messages |
| decision | Technical decisions |
| code_change | Code modifications |
| learning | Patterns discovered |
| error | Problems encountered |
| solution | Fixes implemented |
| context | Project background |

### Operations
- `add` - Store new memory
- `query` - Semantic search
- `update` - Modify existing
- `delete` - Remove single
- `bulk-delete` - Remove multiple
- `export` - Download all
- `import` - Upload memories
- `clear` - Remove all

---

## Development Commands

### Backend
```bash
cd backend
uvicorn main:app --reload --port 8000
pytest tests/ --cov=src
```

### Frontend
```bash
cd frontend
npm run dev
npm run build
npm run lint
```

---

## Environment Variables

### Required
```
OPENAI_API_KEY
ANTHROPIC_API_KEY
CONVEX_URL
CONVEX_DEPLOY_KEY
CLERK_PUBLISHABLE_KEY
CLERK_SECRET_KEY
STRIPE_SECRET_KEY
```

### Optional
```
GOOGLE_API_KEY
OLLAMA_BASE_URL
REDIS_URL
LANGFUSE_*
```

---

## Technical Decisions

### Why Next.js 14?
- Server-side rendering for SEO
- App Router for modern routing
- Built-in API routes
- TypeScript support

### Why FastAPI?
- High performance async
- Automatic OpenAPI docs
- Pydantic validation
- Easy dependency injection

### Why ChromaDB?
- Simple vector storage
- Embedded mode (no server needed)
- Good Python integration
- Persistent by default

### Why Multi-Model?
- Cost optimization
- Capability matching
- Redundancy/failover
- Best tool for each job

---

## Known Technical Debt

1. **Duplicate Directories**
   - `backend/rag/` duplicates `backend/ai/rag/`
   - `backend/agents/` duplicates `backend/ai/agents/`
   - Solution: Keep `ai/*` versions, update imports

2. **In-Memory V-Core**
   - Services use dicts not database
   - Convex integration ready and wired
   - Solution: Set `VOS3_STORAGE_BACKEND=convex` for persistence

---

## Naming Conventions

### Frontend
- Pages: `page.tsx` (Next.js convention)
- Components: PascalCase (`Navigation.tsx`)
- Hooks: camelCase with `use` prefix (`useChat.ts`)
- Styles: CSS modules (`*.module.css`)

### Backend
- Routes: snake_case (`chat_routes.py`)
- Modules: snake_case (`dev_memory.py`)
- Classes: PascalCase (`DevMemory`)
- Functions: snake_case (`get_recent`)

---

## File Locations Quick Reference

| Need | Location |
|------|----------|
| Add new page | `/frontend/app/[name]/page.tsx` |
| Add API hook | `/frontend/hooks/use[Name].ts` |
| Add component | `/frontend/components/[feature]/[Name].tsx` |
| Add API route | `/backend/api/[name]_routes.py` |
| Add service | `/backend/services/[name].py` |
| Add AI feature | `/backend/ai/[module]/` |
| Update smart router | `/backend/config/router.yaml` |
| Update memory | `/backend/memory/dev_memory.py` |

---

## Deployment

### Local Development
- Backend: `localhost:8000`
- Frontend: `localhost:3000`
- API proxy configured in `next.config.js`

### Production
- Docker containers available
- Railway.app configs included
- Environment-based configuration

---

## Update Log

| Date | Change |
|------|--------|
| Feb 2026 | Initial knowledge base created |
| Feb 2026 | Navigation renamed (Intelligence→Insights, Memory→Learning Memory) |
| Feb 2026 | Learning Memory CRUD features added |

---

*This knowledge base should be loaded into Learning Memory to provide project context.*

---

## Loading Instructions

To load this knowledge base into VOS3 Learning Memory:

1. Navigate to `/memory` (Learning Memory page)
2. Use the Import function
3. Or add key sections as individual memories with type `context`

Key entries to add:
- Project identity and version
- Navigation structure
- API endpoint summary
- Smart router configuration
- Development commands
- File locations reference

---

*Last verified: February 2026*
