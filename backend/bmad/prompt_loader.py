"""
BMAD Prompt Loader
===================

Loads BMAD agent personas from YAML files and workflow templates
to build system prompts for LLM calls.

Agent YAML files at: _bmad/bmm/agents/*.agent.yaml
Workflow templates at: _bmad/bmm/workflows/
"""

import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# Resolve BMAD root (relative to repo root)
_REPO_ROOT = Path(__file__).parent.parent.parent
_BMAD_ROOT = _REPO_ROOT / "_bmad" / "bmm"
_AGENTS_DIR = _BMAD_ROOT / "agents"
_WORKFLOWS_DIR = _BMAD_ROOT / "workflows"

# Map BMAD agent roles to YAML filenames
AGENT_YAML_MAP: Dict[str, str] = {
    "product_manager": "pm.agent.yaml",
    "market_analyst": "analyst.agent.yaml",
    "ux_designer": "ux-designer.agent.yaml",
    "architect": "architect.agent.yaml",
    "scrum_master": "sm.agent.yaml",
    "dev_lead": "dev.agent.yaml",
    "frontend_dev": "dev.agent.yaml",
    "backend_dev": "dev.agent.yaml",
    "devops_engineer": "dev.agent.yaml",
    "qa_engineer": "qa.agent.yaml",
    "security_reviewer": "qa.agent.yaml",
    "code_reviewer": "dev.agent.yaml",
    "deployment_manager": "dev.agent.yaml",
    "release_manager": "sm.agent.yaml",
    "monitoring_agent": "dev.agent.yaml",
    "maintenance_agent": "dev.agent.yaml",
}

# Map phases to template files
PHASE_TEMPLATE_MAP: Dict[str, str] = {
    "ideation": "1-analysis/create-product-brief/product-brief.template.md",
    "discovery": "2-plan-workflows/create-prd/templates/prd-template.md",
    "planning": "3-solutioning/create-architecture/architecture-decision-template.md",
    "design": "2-plan-workflows/create-ux-design/ux-design-template.md",
}

# Cache for loaded personas
_persona_cache: Dict[str, str] = {}


def load_agent_persona(agent_role: str) -> str:
    """
    Load agent persona from YAML file and format as system prompt.

    Args:
        agent_role: Agent role key (e.g. "product_manager", "architect")

    Returns:
        Formatted system prompt string from YAML persona
    """
    if agent_role in _persona_cache:
        return _persona_cache[agent_role]

    yaml_file = AGENT_YAML_MAP.get(agent_role)
    if not yaml_file:
        logger.warning(f"No YAML mapping for agent role: {agent_role}")
        return ""

    yaml_path = _AGENTS_DIR / yaml_file

    if not yaml_path.exists():
        logger.warning(f"Agent YAML not found: {yaml_path}")
        return ""

    if not YAML_AVAILABLE:
        # Fallback: read raw file content
        content = yaml_path.read_text(encoding="utf-8")
        _persona_cache[agent_role] = content
        return content

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        agent_data = data.get("agent", {})
        metadata = agent_data.get("metadata", {})
        persona = agent_data.get("persona", {})

        name = metadata.get("name", "Agent")
        title = metadata.get("title", agent_role)
        role = persona.get("role", "")
        identity = persona.get("identity", "")
        style = persona.get("communication_style", "")
        principles = persona.get("principles", "")

        prompt = f"""You are {name}, a {title}.

Role: {role}

Identity: {identity}

Communication Style: {style}

Principles:
{principles}"""

        _persona_cache[agent_role] = prompt
        return prompt

    except Exception as e:
        logger.error(f"Failed to load agent persona {agent_role}: {e}")
        return ""


def load_phase_template(phase: str) -> str:
    """
    Load the output template for a phase artifact.

    Args:
        phase: Phase name (e.g. "ideation", "discovery")

    Returns:
        Template content string, or empty string if not found
    """
    template_path_rel = PHASE_TEMPLATE_MAP.get(phase)
    if not template_path_rel:
        return ""

    template_path = _WORKFLOWS_DIR / template_path_rel

    if not template_path.exists():
        logger.debug(f"Phase template not found: {template_path}")
        return ""

    try:
        return template_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to load phase template {phase}: {e}")
        return ""


def build_system_prompt(
    agent_role: str,
    phase: str,
    project_context: str = "",
) -> str:
    """
    Build a complete system prompt for an agent in a specific phase.

    Combines:
    1. Agent persona from YAML
    2. Phase-specific template/instructions
    3. Project context (description, previous artifacts)

    Args:
        agent_role: Agent role key
        phase: Current phase name
        project_context: Additional context about the project

    Returns:
        Complete system prompt string
    """
    parts = []

    # 1. Agent persona
    persona = load_agent_persona(agent_role)
    if persona:
        parts.append(persona)

    # 2. Phase template
    template = load_phase_template(phase)
    if template:
        parts.append(
            f"\n## Output Template\n\nUse this structure for your output:\n\n{template}"
        )

    # 3. Phase-specific instructions
    phase_instructions = _get_phase_instructions(phase)
    if phase_instructions:
        parts.append(f"\n## Phase Instructions\n\n{phase_instructions}")

    # 4. Project context
    if project_context:
        parts.append(f"\n## Project Context\n\n{project_context}")

    return (
        "\n\n---\n\n".join(parts)
        if parts
        else f"You are a {agent_role} working on the {phase} phase."
    )


def _get_phase_instructions(phase: str) -> str:
    """Get phase-specific instructions."""
    instructions = {
        "ideation": (
            "Create a product brief with vision, target users, success metrics, and scope. "
            "Focus on the 'why' and 'what', not the 'how'. Ask clarifying questions if the project "
            "description is vague."
        ),
        "discovery": (
            "Create a Product Requirements Document (PRD). Include user stories with acceptance "
            "criteria, feature prioritization (MoSCoW), and success metrics. Ground everything "
            "in user needs."
        ),
        "planning": (
            "Create architecture documentation. Define tech stack, system components, API design, "
            "data models, and key architectural decisions (ADRs). Consider scalability, security, "
            "and developer experience."
        ),
        "design": (
            "Create detailed design specifications. Define UI/UX patterns, component hierarchy, "
            "user flows, and responsive design approach. Include wireframe descriptions."
        ),
        "development": (
            "Implement the code based on approved architecture and design specs. Follow the project's "
            "coding standards. Write clean, tested, documented code."
        ),
        "testing": (
            "Create comprehensive test plan and execute tests. Cover unit, integration, and e2e tests. "
            "Perform security review. Document all findings."
        ),
        "review": (
            "Review code quality, architecture adherence, test coverage, and security. "
            "Provide actionable feedback with specific file/line references."
        ),
        "deployment": (
            "Prepare deployment configuration. Define CI/CD pipeline, environment variables, "
            "infrastructure requirements, and rollback procedures."
        ),
        "operations": (
            "Set up monitoring, alerting, and maintenance procedures. Define SLOs, incident "
            "response playbooks, and regular maintenance tasks."
        ),
    }
    return instructions.get(phase, "")
