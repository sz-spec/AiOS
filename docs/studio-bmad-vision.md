# VOS3 Project Studio + BMAD Integration Vision

## Target User Flow

1. **User opens Studio** (`/studio`)
2. **Creates a project** — provides project name and description
3. **System creates project structure:**
   - `projects/{user_id}/` folder (currently user "1")
   - `projects/{user_id}/{unique_project_name}/` folder
   - Initializes a local git repo (`git init`, origin main branch)
   - Creates a remote git repo (GitHub)
4. **User selects project type** (SaaS, API, Landing Page, Mobile App, etc.)
5. **BMAD process begins** — guided workflow for developing an AI-based fullstack solution tailored to user needs:
   - Product Brief → PRD → UX Design → Architecture → Epics & Stories → Sprint → Dev → Review
6. **Smart Router integration:**
   - Routes between user prompts and BMAD agents
   - Utilizes cache when agent is not needed (simple responses)
   - Selects each agent's preferred LLM based on role and complexity
   - Cost-optimized model selection throughout the entire process

## System Architecture

### Project Structure on Disk
```
projects/
  1/                          # user_id folder
    my-saas-app/              # unique project name
      .git/                   # initialized repo
      _bmad/                  # BMAD config for this project
        bmm/config.yaml       # project-specific BMAD config
      docs/
        planning-artifacts/   # briefs, PRDs, architecture
        implementation-artifacts/  # stories, sprint status
      src/                    # generated code
```

### Smart Router Integration
- User prompt → SmartRouter → decides if agent needed or cache hit
- If agent needed → SmartRouter selects optimal LLM for that agent's role
- Agent roles map to LLM preferences (from `config/router.yaml`):
  - Analyst/PM → gemini (research-heavy, cost-effective)
  - Architect → gpt (complex reasoning)
  - Developer → claude-sonnet (code generation)
  - QA/Reviewer → claude-opus (thorough analysis)
  - High complexity (>=9) → always routes to top-tier model

### Git Integration
- Local repo created on project init
- Remote repo created via GitHub API
- Auto-commit at BMAD phase completions
- Branch per sprint/story for implementation phase

## Key Requirements

- Each project is isolated in its own folder with its own git repo
- BMAD config is per-project (different projects can be at different phases)
- Smart Router handles all LLM calls — no direct API calls from agents
- Cache layer reduces costs for repetitive or simple interactions
- User sees real-time progress through Studio UI (WebSocket events)
