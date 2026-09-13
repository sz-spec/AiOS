# VOS3 - AI Operating System

> **VOS3 Sovereign Infrastructure v19.1 — PRE-CLI GOLD MASTER**

A unified AI-powered operating system that combines intelligent agents, business automation, and natural language interfaces.

## What This Project Does

VOS3 enables non-technical users to build and run full-stack business systems using natural language. It combines:

- **Multi-Agent AI System** - Specialized agents for code generation, architecture, testing, and review
- **Business OS (V-Core)** - Organizations, custom entities, workflows, and monitoring
- **Cost-Optimized LLM Routing** - Smart model selection to minimize API costs
- **Natural Language Interface** - Voice and text input in 11 languages

## Project Structure

```
VOS3/
├── frontend/                 # Next.js Web Application
│   ├── app/                  # App Router pages
│   │   ├── chat/            # AI chat interface
│   │   ├── builder/         # Code generation
│   │   ├── agents/          # Agent management
│   │   ├── v-core/          # Business OS dashboard
│   │   └── settings/        # Configuration
│   ├── components/           # React components
│   │   ├── shared/          # Voice input, navigation
│   │   ├── agents/          # Agent UI components
│   │   └── v-core/          # Business OS components
│   └── hooks/                # Custom React hooks
│
├── backend/                  # Python FastAPI Backend
│   ├── api/                  # REST API endpoints
│   ├── agents/               # Multi-agent system (LangGraph)
│   ├── ai/                   # AI modules
│   │   ├── llm/             # Multi-provider LLM support
│   │   ├── agents/          # Agent implementations
│   │   ├── codegen/         # Code generation
│   │   └── rag/             # RAG implementations
│   ├── core/                 # V-Core Business OS
│   │   ├── control_plane.py # Users, orgs, permissions
│   │   ├── business_core.py # Custom entities & fields
│   │   ├── workflow_engine.py # Automation workflows
│   │   └── mission_control.py # Monitoring & approvals
│   ├── src/                  # Core utilities
│   │   ├── llm.py           # LLM wrapper
│   │   ├── efficiency.py    # Cost-aware routing
│   │   └── cache.py         # Multi-backend caching
│   ├── memory/               # Conversation memory
│   ├── hitl/                 # Human-in-the-loop
│   ├── rag/                  # Document retrieval
│   └── integrations/         # External services
│
├── docs/                     # Documentation
├── docker/                   # Docker configurations
└── scripts/                  # Utility scripts
```

## Key Features

### AI Engine
| Feature | Description |
|---------|-------------|
| Multi-Agent System | Architect, Frontend, Backend, Tester, Reviewer agents |
| Cost-Aware Routing | Smart model selection (40-60% cost savings) |
| Multi-Provider LLM | OpenAI, Anthropic, Google, Ollama |
| RAG | Advanced retrieval with REFRAG, CLaRa, TAO optimizations |
| Memory | Long-term conversation memory |
| HITL | Human approval for critical operations |

### Business OS (V-Core)
| Module | Description |
|--------|-------------|
| Control Plane | Users, organizations, roles, permissions |
| Business Core | Custom entities, dynamic fields, data validation |
| Workflow Engine | Automated processes with triggers and actions |
| Mission Control | AI activity monitoring, approvals, metrics |

### UI Features
| Feature | Description |
|---------|-------------|
| Voice Input | 11 languages supported |
| Dashboard | Clean, non-technical friendly interface |
| Runtime Settings | Configure API keys via UI |
| Dev Mode | Works without API keys (mocked responses) |

## Tech Stack

### Frontend
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: CSS Variables + Tailwind
- **State**: React hooks

### Backend
- **Framework**: FastAPI
- **Language**: Python 3.10+
- **AI**: LangChain + LangGraph
- **LLMs**: OpenAI, Anthropic, Google, Ollama
- **Caching**: Redis / In-memory

## Getting Started

### Prerequisites
- Node.js 18+
- Python 3.10+
- Docker (optional)

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r ../requirements.txt

# Set environment variables
cp ../.env.example ../.env
# Edit .env with your API keys

# Run server
uvicorn main:app --reload --port 8000
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
# Opens at http://localhost:3000
```

### Docker (Full Stack)

```bash
docker-compose up
# Frontend: http://localhost:3000
# Backend: http://localhost:8000
```

## Environment Variables

See `.env.example` for all available options. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes* | OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes* | Anthropic API key |
| `NEXT_PUBLIC_API_URL` | No | Backend URL (default: localhost:8000) |

*At least one LLM API key required for AI features. App runs in dev mode without keys.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         FRONTEND                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │Dashboard│  │  Chat   │  │ Builder │  │ V-Core  │        │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘        │
│       └────────────┴────────────┴────────────┘              │
│                         │ API                                │
└─────────────────────────┼───────────────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────────────┐
│                      BACKEND                                 │
│                         ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │               Cost-Aware Router                      │    │
│  │    Routes to optimal model based on complexity       │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         │                                    │
│  ┌──────────┬───────────┼───────────┬──────────┐           │
│  ▼          ▼           ▼           ▼          ▼           │
│ ┌────┐   ┌────┐     ┌────┐     ┌────┐    ┌────┐           │
│ │Arch│   │Front│    │Back│     │Test│    │Review│          │
│ │itect│   │end │    │end │     │er  │    │er   │          │
│ └────┘   └────┘     └────┘     └────┘    └────┘           │
│                         │                                    │
│  ┌──────────────────────┴──────────────────────┐           │
│  │                 V-Core                       │           │
│  │  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐   │           │
│  │  │Control│ │Business│ │Workflow│ │Mission│   │           │
│  │  │ Plane │ │ Core  │ │Engine │ │Control│   │           │
│  │  └───────┘ └───────┘ └───────┘ └───────┘   │           │
│  └─────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

## VOS3 Kernel — Certified Binary

**v3.2.0 — SEA v19.2 GOLD MASTER CERTIFIED**
- Binary: `kernel/build/vos3.elf`
- SHA-256: `196de8e59a3557bfe4202932b54641d7694b44117e7a0afabf9ddb120b137e30`
- W^X Hard Enforcement: mprotect/mmap reject PROT_WRITE|PROT_EXEC (-EINVAL) + PTE sanitizer
- ELF W^X Forensic: 4 LOAD segments — R E, R E, R, RW — ZERO segments with W+X
- Agent Kill Switch: SYS_AGENT_KILL_ALL (497) — kills all agents, scrubs all model slots
- Kernel Stability: 760K+ console lines, 7.1M+ heartbeat ticks, 0 panics, 0 faults, 0 deadlocks
- Heartbeat VA: `0xFFFFFFFFFFFFF000ULL` -- synced between `ai_guard.h:368` and `vos3_sdk.h:31`
- VBus Bridge: Handshake PASS, SMP-2 live
- Auth Coverage: 42/42 route files, 420 AuthenticatedUser usages
- Sandbox: Semaphore(10), RLIMIT_NPROC=(4,4), 256MB default, 30s timeout
- 5-Layer RCE Defense: COMMAND_ALLOWLIST + BLOCKED_FLAGS + BLOCKED_PATTERNS + SAFE_FLAGS + shell=False
- 0-Hit Purity: 0 user_demo, 0 shell=True, 0 pickle, 0 utcnow in project code
- Stack Canaries: 388 `__stack_chk_fail` call sites
- Certified: 2026-04-06 | Final Sovereign Hardening v19.2 PASSED

## License

MIT License
