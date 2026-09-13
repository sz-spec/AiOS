"""
AIVA - AI Voice Agent Builder

Build agentic AI agents in 6 clicks:
1. Define goal via natural language
2. Select architecture template
3. Choose tools & integrations
4. Test live with the agent
5. Configure security & monitoring
6. Deploy & activate
"""

from aiva.agent_builder import (
    # Service
    AIVAService,
    get_aiva_service,
    # Models
    AIAgent,
    AgentTool,
    AgentPersona,
    AgentMemory,
    AgentSecurity,
    AgentMetrics,
    EscalationRule,
    # Enums
    AgentType,
    AgentArchitecture,
    AgentMode,
    AgentStatus,
    ToolCategory,
    # Templates & Tools
    AGENT_TEMPLATES,
    AVAILABLE_TOOLS,
)

__all__ = [
    # Service
    "AIVAService",
    "get_aiva_service",
    # Models
    "AIAgent",
    "AgentTool",
    "AgentPersona",
    "AgentMemory",
    "AgentSecurity",
    "AgentMetrics",
    "EscalationRule",
    # Enums
    "AgentType",
    "AgentArchitecture",
    "AgentMode",
    "AgentStatus",
    "ToolCategory",
    # Templates & Tools
    "AGENT_TEMPLATES",
    "AVAILABLE_TOOLS",
]
