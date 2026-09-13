"""
AIVA - AI Voice Agent Builder

Build agentic AI agents in 6 clicks:
1. Define goal via natural language
2. Select architecture template
3. Choose tools & integrations
4. Test live with the agent
5. Configure security & monitoring
6. Deploy & activate

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
import asyncio
from collections import defaultdict

# ============================================
# Enums
# ============================================


class AgentType(str, Enum):
    """Types of AI agents."""

    SALES_ASSISTANT = "sales_assistant"
    SUPPORT_AGENT = "support_agent"
    SCHEDULER = "scheduler"
    COLLECTIONS = "collections"
    MARKETING = "marketing"
    HR_ASSISTANT = "hr_assistant"
    OPS_COORDINATOR = "ops_coordinator"
    ANALYST = "analyst"
    CONCIERGE = "concierge"
    CUSTOM = "custom"


class AgentArchitecture(str, Enum):
    """Agent architecture patterns."""

    SIMPLE = "simple"  # Single agent, direct response
    REACT = "react"  # Reasoning + Acting loop
    PLAN_EXECUTE = "plan_execute"  # Plan first, then execute
    MULTI_AGENT = "multi_agent"  # Team of specialized agents
    HIERARCHICAL = "hierarchical"  # Manager + workers


class AgentMode(str, Enum):
    """Agent operating modes."""

    SUGGEST = "suggest"  # AI recommends, human decides
    ASSIST = "assist"  # AI drafts, human reviews before sending
    ACT = "act"  # AI executes autonomously, reports after


class AgentStatus(str, Enum):
    """Agent lifecycle status."""

    DRAFT = "draft"
    TESTING = "testing"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ToolCategory(str, Enum):
    """Tool categories."""

    COMMUNICATION = "communication"
    CRM = "crm"
    CALENDAR = "calendar"
    PAYMENTS = "payments"
    DOCUMENTS = "documents"
    DATA = "data"
    CUSTOM = "custom"


# ============================================
# Data Models
# ============================================


@dataclass
class AgentTool:
    """A tool/integration available to agents."""

    id: str
    name: str
    description: str
    category: ToolCategory
    icon: str = "🔧"
    config_schema: dict = field(default_factory=dict)
    required_permissions: list[str] = field(default_factory=list)
    is_enabled: bool = True

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "category": self.category.value,
        }


@dataclass
class EscalationRule:
    """Rule for when to escalate to human."""

    id: str = field(default_factory=lambda: f"rule_{uuid4().hex[:8]}")
    condition: str = ""  # Natural language condition
    action: str = "notify"  # notify, pause, transfer
    target: str = ""  # User/role to escalate to
    priority: str = "medium"  # low, medium, high, urgent

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentPersona:
    """Agent personality and behavior settings."""

    name: str = "AI Assistant"
    role: str = ""
    tone: str = "professional"  # professional, friendly, formal, casual
    language: str = "en"
    greeting: str = "Hello! How can I help you today?"
    fallback_message: str = "I'm not sure about that. Let me connect you with a human."

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentMemory:
    """Agent memory configuration."""

    short_term_enabled: bool = True
    long_term_enabled: bool = True
    context_window: int = 10  # Messages to remember
    persist_conversations: bool = True
    learn_from_feedback: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentSecurity:
    """Security and guardrails configuration."""

    require_approval_for: list[str] = field(
        default_factory=list
    )  # Actions needing approval
    blocked_topics: list[str] = field(default_factory=list)
    pii_redaction: bool = True
    max_actions_per_minute: int = 10
    human_in_loop_threshold: float = 0.7  # Confidence threshold
    audit_all_actions: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentMetrics:
    """Agent performance metrics."""

    total_conversations: int = 0
    total_actions: int = 0
    successful_actions: int = 0
    escalations: int = 0
    avg_response_time_ms: float = 0
    satisfaction_score: float = 0
    last_active: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "last_active": self.last_active.isoformat() if self.last_active else None,
        }


@dataclass
class AIAgent:
    """Complete AI Agent definition."""

    id: str = field(default_factory=lambda: f"agent_{uuid4().hex[:12]}")

    # Basic info
    name: str = ""
    description: str = ""
    agent_type: AgentType = AgentType.CUSTOM

    # User/Org
    user_id: str = ""
    organization_id: Optional[str] = None

    # Architecture
    architecture: AgentArchitecture = AgentArchitecture.REACT
    mode: AgentMode = AgentMode.ASSIST

    # Configuration
    persona: AgentPersona = field(default_factory=AgentPersona)
    memory: AgentMemory = field(default_factory=AgentMemory)
    security: AgentSecurity = field(default_factory=AgentSecurity)

    # Tools & Capabilities
    tools: list[str] = field(default_factory=list)  # Tool IDs
    capabilities: list[str] = field(default_factory=list)

    # Escalation
    escalation_rules: list[EscalationRule] = field(default_factory=list)

    # Model
    model: str = "gpt-4"
    system_prompt: str = ""
    temperature: float = 0.7

    # Status
    status: AgentStatus = AgentStatus.DRAFT

    # Metrics
    metrics: AgentMetrics = field(default_factory=AgentMetrics)

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deployed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_type": self.agent_type.value,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "architecture": self.architecture.value,
            "mode": self.mode.value,
            "persona": self.persona.to_dict(),
            "memory": self.memory.to_dict(),
            "security": self.security.to_dict(),
            "tools": self.tools,
            "capabilities": self.capabilities,
            "escalation_rules": [r.to_dict() for r in self.escalation_rules],
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "status": self.status.value,
            "metrics": self.metrics.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "deployed_at": self.deployed_at.isoformat() if self.deployed_at else None,
        }


# ============================================
# Pre-built Agent Templates
# ============================================

AGENT_TEMPLATES: dict[AgentType, dict] = {
    AgentType.SALES_ASSISTANT: {
        "name": "Sales Assistant",
        "description": "Lead qualification, follow-ups, proposals, and meeting scheduling",
        "icon": "💼",
        "architecture": AgentArchitecture.REACT,
        "mode": AgentMode.ASSIST,
        "tools": ["crm", "email", "calendar", "documents"],
        "capabilities": [
            "Qualify leads based on criteria",
            "Send personalized follow-up emails",
            "Generate proposals and quotes",
            "Schedule meetings and demos",
            "Track deal pipeline",
        ],
        "escalation_rules": [
            {
                "condition": "Deal value > $10,000",
                "action": "notify",
                "priority": "high",
            },
            {
                "condition": "Custom pricing requested",
                "action": "pause",
                "priority": "medium",
            },
            {
                "condition": "Customer complaint",
                "action": "transfer",
                "priority": "urgent",
            },
        ],
        "persona": {
            "tone": "professional",
            "greeting": "Hi! I'm your sales assistant. How can I help you close more deals today?",
        },
    },
    AgentType.SUPPORT_AGENT: {
        "name": "Support Agent",
        "description": "FAQ answers, ticket creation, status updates, troubleshooting",
        "icon": "🎧",
        "architecture": AgentArchitecture.REACT,
        "mode": AgentMode.ASSIST,
        "tools": ["knowledge_base", "tickets", "email", "chat"],
        "capabilities": [
            "Answer frequently asked questions",
            "Create and update support tickets",
            "Provide order/delivery status",
            "Basic troubleshooting guidance",
            "Collect customer feedback",
        ],
        "escalation_rules": [
            {
                "condition": "Angry or frustrated customer",
                "action": "transfer",
                "priority": "urgent",
            },
            {
                "condition": "Refund request > $100",
                "action": "notify",
                "priority": "high",
            },
            {
                "condition": "Technical issue beyond FAQ",
                "action": "transfer",
                "priority": "medium",
            },
        ],
        "persona": {
            "tone": "friendly",
            "greeting": "Hello! I'm here to help. What can I assist you with?",
        },
    },
    AgentType.SCHEDULER: {
        "name": "Scheduler",
        "description": "Booking, rescheduling, reminders, and waitlist management",
        "icon": "📅",
        "architecture": AgentArchitecture.SIMPLE,
        "mode": AgentMode.ACT,
        "tools": ["calendar", "email", "sms", "notifications"],
        "capabilities": [
            "Book appointments and meetings",
            "Reschedule existing bookings",
            "Send reminders and confirmations",
            "Manage waitlist",
            "Handle cancellations",
        ],
        "escalation_rules": [
            {"condition": "VIP customer", "action": "notify", "priority": "high"},
            {
                "condition": "Double-booking conflict",
                "action": "pause",
                "priority": "urgent",
            },
            {
                "condition": "Urgent same-day request",
                "action": "notify",
                "priority": "high",
            },
        ],
        "persona": {
            "tone": "professional",
            "greeting": "Hi! I can help you schedule appointments. What would you like to book?",
        },
    },
    AgentType.COLLECTIONS: {
        "name": "Collections Agent",
        "description": "Payment reminders, invoice sending, and payment plan setup",
        "icon": "💰",
        "architecture": AgentArchitecture.PLAN_EXECUTE,
        "mode": AgentMode.SUGGEST,
        "tools": ["payments", "email", "sms", "documents"],
        "capabilities": [
            "Send payment reminders",
            "Generate and send invoices",
            "Set up payment plans",
            "Track payment status",
            "Record payment promises",
        ],
        "escalation_rules": [
            {"condition": "Payment dispute", "action": "transfer", "priority": "high"},
            {
                "condition": "Account 60+ days overdue",
                "action": "notify",
                "priority": "urgent",
            },
            {
                "condition": "Legal threat mentioned",
                "action": "transfer",
                "priority": "urgent",
            },
        ],
        "persona": {
            "tone": "professional",
            "greeting": "Hello, I'm reaching out regarding your account. How can I assist you today?",
        },
    },
    AgentType.MARKETING: {
        "name": "Marketing Assistant",
        "description": "Campaign drafts, social posts, email sequences, A/B testing",
        "icon": "📢",
        "architecture": AgentArchitecture.REACT,
        "mode": AgentMode.ASSIST,
        "tools": ["email", "social_media", "analytics", "content"],
        "capabilities": [
            "Draft marketing campaigns",
            "Create social media posts",
            "Design email sequences",
            "Analyze campaign performance",
            "Suggest A/B test variations",
        ],
        "escalation_rules": [
            {
                "condition": "Brand-sensitive content",
                "action": "pause",
                "priority": "high",
            },
            {
                "condition": "Budget approval needed",
                "action": "notify",
                "priority": "medium",
            },
            {
                "condition": "Legal/compliance review",
                "action": "pause",
                "priority": "high",
            },
        ],
        "persona": {
            "tone": "creative",
            "greeting": "Hey! Ready to create some amazing content? What's on the agenda?",
        },
    },
    AgentType.HR_ASSISTANT: {
        "name": "HR Assistant",
        "description": "Leave requests, onboarding tasks, policy questions",
        "icon": "👥",
        "architecture": AgentArchitecture.REACT,
        "mode": AgentMode.ASSIST,
        "tools": ["hr_system", "calendar", "documents", "email"],
        "capabilities": [
            "Process leave requests",
            "Guide new employee onboarding",
            "Answer policy questions",
            "Schedule interviews",
            "Collect employee feedback",
        ],
        "escalation_rules": [
            {
                "condition": "Employee complaint",
                "action": "transfer",
                "priority": "urgent",
            },
            {
                "condition": "Termination-related",
                "action": "transfer",
                "priority": "urgent",
            },
            {
                "condition": "Sensitive personal matter",
                "action": "transfer",
                "priority": "high",
            },
        ],
        "persona": {
            "tone": "friendly",
            "greeting": "Hi! I'm your HR assistant. How can I help you today?",
        },
    },
    AgentType.OPS_COORDINATOR: {
        "name": "Operations Coordinator",
        "description": "Task assignment, status tracking, vendor communication",
        "icon": "⚙️",
        "architecture": AgentArchitecture.PLAN_EXECUTE,
        "mode": AgentMode.ASSIST,
        "tools": ["project_management", "email", "inventory", "documents"],
        "capabilities": [
            "Assign and track tasks",
            "Monitor project status",
            "Coordinate with vendors",
            "Generate status reports",
            "Flag blockers and risks",
        ],
        "escalation_rules": [
            {"condition": "Budget overrun", "action": "notify", "priority": "urgent"},
            {"condition": "Missed deadline", "action": "notify", "priority": "high"},
            {
                "condition": "Emergency situation",
                "action": "transfer",
                "priority": "urgent",
            },
        ],
        "persona": {
            "tone": "professional",
            "greeting": "Hello! I'm your ops coordinator. What needs attention today?",
        },
    },
    AgentType.ANALYST: {
        "name": "Data Analyst",
        "description": "Report generation, trend analysis, anomaly detection",
        "icon": "📊",
        "architecture": AgentArchitecture.PLAN_EXECUTE,
        "mode": AgentMode.SUGGEST,
        "tools": ["analytics", "database", "documents", "visualization"],
        "capabilities": [
            "Generate custom reports",
            "Analyze trends and patterns",
            "Detect anomalies in data",
            "Create visualizations",
            "Provide business insights",
        ],
        "escalation_rules": [
            {
                "condition": "Data quality issues",
                "action": "notify",
                "priority": "high",
            },
            {
                "condition": "Unexpected pattern detected",
                "action": "notify",
                "priority": "medium",
            },
            {
                "condition": "Sensitive data access",
                "action": "pause",
                "priority": "high",
            },
        ],
        "persona": {
            "tone": "professional",
            "greeting": "Hi! I can help you analyze your data. What insights are you looking for?",
        },
    },
    AgentType.CONCIERGE: {
        "name": "AI Concierge",
        "description": "24/7 guest services, bookings, recommendations",
        "icon": "🛎️",
        "architecture": AgentArchitecture.REACT,
        "mode": AgentMode.ACT,
        "tools": ["reservations", "recommendations", "chat", "notifications"],
        "capabilities": [
            "Handle guest inquiries 24/7",
            "Make and modify reservations",
            "Provide local recommendations",
            "Process upgrade requests",
            "Resolve service issues",
        ],
        "escalation_rules": [
            {"condition": "VIP guest", "action": "notify", "priority": "high"},
            {
                "condition": "Complaint or issue",
                "action": "transfer",
                "priority": "urgent",
            },
            {"condition": "Special request", "action": "notify", "priority": "medium"},
        ],
        "persona": {
            "tone": "friendly",
            "greeting": "Welcome! I'm your personal concierge. How may I assist you?",
        },
    },
}


# ============================================
# Available Tools Library
# ============================================

AVAILABLE_TOOLS: list[AgentTool] = [
    # Communication
    AgentTool(
        id="email",
        name="Email",
        description="Send and manage emails via Gmail/Outlook",
        category=ToolCategory.COMMUNICATION,
        icon="📧",
        required_permissions=["email:send", "email:read"],
    ),
    AgentTool(
        id="sms",
        name="SMS",
        description="Send text messages via Twilio",
        category=ToolCategory.COMMUNICATION,
        icon="💬",
        required_permissions=["sms:send"],
    ),
    AgentTool(
        id="chat",
        name="Live Chat",
        description="Real-time chat with customers",
        category=ToolCategory.COMMUNICATION,
        icon="💭",
        required_permissions=["chat:access"],
    ),
    AgentTool(
        id="whatsapp",
        name="WhatsApp",
        description="WhatsApp Business messaging",
        category=ToolCategory.COMMUNICATION,
        icon="📱",
        required_permissions=["whatsapp:send"],
    ),
    # CRM
    AgentTool(
        id="crm",
        name="CRM",
        description="Manage contacts, leads, and deals",
        category=ToolCategory.CRM,
        icon="👥",
        required_permissions=["crm:read", "crm:write"],
    ),
    AgentTool(
        id="tickets",
        name="Support Tickets",
        description="Create and manage support tickets",
        category=ToolCategory.CRM,
        icon="🎫",
        required_permissions=["tickets:read", "tickets:write"],
    ),
    # Calendar
    AgentTool(
        id="calendar",
        name="Calendar",
        description="Schedule meetings and appointments",
        category=ToolCategory.CALENDAR,
        icon="📅",
        required_permissions=["calendar:read", "calendar:write"],
    ),
    AgentTool(
        id="reservations",
        name="Reservations",
        description="Manage bookings and reservations",
        category=ToolCategory.CALENDAR,
        icon="🗓️",
        required_permissions=["reservations:manage"],
    ),
    # Payments
    AgentTool(
        id="payments",
        name="Payments",
        description="Process payments via Stripe",
        category=ToolCategory.PAYMENTS,
        icon="💳",
        required_permissions=["payments:read", "payments:charge"],
    ),
    AgentTool(
        id="invoices",
        name="Invoices",
        description="Generate and send invoices",
        category=ToolCategory.PAYMENTS,
        icon="🧾",
        required_permissions=["invoices:create", "invoices:send"],
    ),
    # Documents
    AgentTool(
        id="documents",
        name="Documents",
        description="Create and manage documents",
        category=ToolCategory.DOCUMENTS,
        icon="📄",
        required_permissions=["documents:read", "documents:write"],
    ),
    AgentTool(
        id="knowledge_base",
        name="Knowledge Base",
        description="Search internal documentation",
        category=ToolCategory.DOCUMENTS,
        icon="📚",
        required_permissions=["kb:read"],
    ),
    # Data
    AgentTool(
        id="analytics",
        name="Analytics",
        description="Access business analytics and reports",
        category=ToolCategory.DATA,
        icon="📊",
        required_permissions=["analytics:read"],
    ),
    AgentTool(
        id="database",
        name="Database",
        description="Query business database",
        category=ToolCategory.DATA,
        icon="🗄️",
        required_permissions=["database:read"],
    ),
    AgentTool(
        id="inventory",
        name="Inventory",
        description="Track and manage inventory",
        category=ToolCategory.DATA,
        icon="📦",
        required_permissions=["inventory:read", "inventory:write"],
    ),
    # Other
    AgentTool(
        id="notifications",
        name="Notifications",
        description="Send push notifications",
        category=ToolCategory.CUSTOM,
        icon="🔔",
        required_permissions=["notifications:send"],
    ),
    AgentTool(
        id="social_media",
        name="Social Media",
        description="Post to social media platforms",
        category=ToolCategory.CUSTOM,
        icon="📣",
        required_permissions=["social:post"],
    ),
    AgentTool(
        id="recommendations",
        name="Recommendations",
        description="AI-powered recommendations engine",
        category=ToolCategory.CUSTOM,
        icon="💡",
        required_permissions=["recommendations:access"],
    ),
]


# ============================================
# AIVA Service
# ============================================


class AIVAService:
    """
    AIVA - AI Voice Agent Builder Service

    Manages the full lifecycle of AI agents:
    - Creation from templates or custom
    - Configuration and testing
    - Deployment and monitoring
    """

    def __init__(self):
        self._agents: dict[str, AIAgent] = {}
        self._user_agents: dict[str, list[str]] = defaultdict(list)
        self._conversations: dict[str, list[dict]] = defaultdict(list)

    # ==========================================
    # Agent Creation (Steps 1-2)
    # ==========================================

    def create_from_description(
        self,
        user_id: str,
        description: str,
        organization_id: Optional[str] = None,
    ) -> AIAgent:
        """
        Step 1: Create agent from natural language description.
        AI analyzes the description and suggests configuration.
        """
        # Analyze description to determine best template
        suggested_type = self._analyze_description(description)
        template = AGENT_TEMPLATES.get(suggested_type, {})

        agent = AIAgent(
            user_id=user_id,
            organization_id=organization_id,
            name=template.get("name", "Custom Agent"),
            description=description,
            agent_type=suggested_type,
            architecture=template.get("architecture", AgentArchitecture.REACT),
            mode=template.get("mode", AgentMode.ASSIST),
        )

        # Apply template persona
        if "persona" in template:
            agent.persona = AgentPersona(**template["persona"])

        # Store agent
        self._agents[agent.id] = agent
        self._user_agents[user_id].append(agent.id)

        return agent

    def create_from_template(
        self,
        user_id: str,
        agent_type: AgentType,
        organization_id: Optional[str] = None,
    ) -> AIAgent:
        """
        Step 1 Alternative: Create agent from pre-built template.
        """
        template = AGENT_TEMPLATES.get(agent_type, {})

        agent = AIAgent(
            user_id=user_id,
            organization_id=organization_id,
            name=template.get("name", "Custom Agent"),
            description=template.get("description", ""),
            agent_type=agent_type,
            architecture=template.get("architecture", AgentArchitecture.REACT),
            mode=template.get("mode", AgentMode.ASSIST),
            tools=template.get("tools", []),
            capabilities=template.get("capabilities", []),
        )

        # Apply template persona
        if "persona" in template:
            agent.persona = AgentPersona(**template["persona"])

        # Apply escalation rules
        for rule_data in template.get("escalation_rules", []):
            agent.escalation_rules.append(EscalationRule(**rule_data))

        self._agents[agent.id] = agent
        self._user_agents[user_id].append(agent.id)

        return agent

    def set_architecture(
        self,
        agent_id: str,
        architecture: AgentArchitecture,
    ) -> Optional[AIAgent]:
        """
        Step 2: Set agent architecture.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.architecture = architecture
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    # ==========================================
    # Tools & Integrations (Step 3)
    # ==========================================

    def set_tools(
        self,
        agent_id: str,
        tool_ids: list[str],
    ) -> Optional[AIAgent]:
        """
        Step 3: Configure agent tools and integrations.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        # Validate tools exist
        valid_tools = {t.id for t in AVAILABLE_TOOLS}
        agent.tools = [t for t in tool_ids if t in valid_tools]
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    def get_available_tools(self) -> list[AgentTool]:
        """Get all available tools."""
        return AVAILABLE_TOOLS

    def get_tools_by_category(self, category: ToolCategory) -> list[AgentTool]:
        """Get tools filtered by category."""
        return [t for t in AVAILABLE_TOOLS if t.category == category]

    # ==========================================
    # Testing (Step 4)
    # ==========================================

    async def test_agent(
        self,
        agent_id: str,
        message: str,
    ) -> dict:
        """
        Step 4: Test agent with a live conversation.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": "Agent not found"}

        # Update status to testing
        agent.status = AgentStatus.TESTING

        # Simulate agent response (in production, call actual LLM)
        response = await self._simulate_agent_response(agent, message)

        # Store conversation
        self._conversations[agent_id].append(
            {
                "role": "user",
                "content": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._conversations[agent_id].append(
            {
                "role": "assistant",
                "content": response["message"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        return response

    def get_test_conversation(self, agent_id: str) -> list[dict]:
        """Get test conversation history."""
        return self._conversations.get(agent_id, [])

    def clear_test_conversation(self, agent_id: str) -> bool:
        """Clear test conversation history."""
        if agent_id in self._conversations:
            self._conversations[agent_id] = []
            return True
        return False

    # ==========================================
    # Security & Monitoring (Step 5)
    # ==========================================

    def configure_security(
        self,
        agent_id: str,
        security: AgentSecurity,
    ) -> Optional[AIAgent]:
        """
        Step 5: Configure security and monitoring settings.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.security = security
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    def add_escalation_rule(
        self,
        agent_id: str,
        rule: EscalationRule,
    ) -> Optional[AIAgent]:
        """Add an escalation rule to agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.escalation_rules.append(rule)
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    def set_mode(
        self,
        agent_id: str,
        mode: AgentMode,
    ) -> Optional[AIAgent]:
        """Set agent operating mode."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.mode = mode
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    # ==========================================
    # Deployment (Step 6)
    # ==========================================

    def deploy_agent(
        self,
        agent_id: str,
    ) -> Optional[AIAgent]:
        """
        Step 6: Deploy agent to production.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        # Generate system prompt if not set
        if not agent.system_prompt:
            agent.system_prompt = self._generate_system_prompt(agent)

        agent.status = AgentStatus.ACTIVE
        agent.deployed_at = datetime.now(timezone.utc)
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    def pause_agent(self, agent_id: str) -> Optional[AIAgent]:
        """Pause an active agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.status = AgentStatus.PAUSED
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    def archive_agent(self, agent_id: str) -> Optional[AIAgent]:
        """Archive an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        agent.status = AgentStatus.ARCHIVED
        agent.updated_at = datetime.now(timezone.utc)

        return agent

    # ==========================================
    # CRUD Operations
    # ==========================================

    def get_agent(self, agent_id: str) -> Optional[AIAgent]:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def list_agents(
        self,
        user_id: str,
        status: Optional[AgentStatus] = None,
        agent_type: Optional[AgentType] = None,
    ) -> list[AIAgent]:
        """List user's agents with optional filters."""
        agent_ids = self._user_agents.get(user_id, [])
        agents = [self._agents[aid] for aid in agent_ids if aid in self._agents]

        if status:
            agents = [a for a in agents if a.status == status]
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]

        return agents

    def delete_agent(self, agent_id: str, user_id: str) -> bool:
        """Delete an agent."""
        agent = self._agents.get(agent_id)
        if not agent or agent.user_id != user_id:
            return False

        del self._agents[agent_id]
        self._user_agents[user_id].remove(agent_id)

        if agent_id in self._conversations:
            del self._conversations[agent_id]

        return True

    def update_agent(
        self,
        agent_id: str,
        updates: dict,
    ) -> Optional[AIAgent]:
        """Update agent fields."""
        agent = self._agents.get(agent_id)
        if not agent:
            return None

        for key, value in updates.items():
            if hasattr(agent, key):
                setattr(agent, key, value)

        agent.updated_at = datetime.now(timezone.utc)
        return agent

    # ==========================================
    # Templates
    # ==========================================

    def get_templates(self) -> list[dict]:
        """Get all available agent templates."""
        templates = []
        for agent_type, template in AGENT_TEMPLATES.items():
            templates.append(
                {
                    "type": agent_type.value,
                    "name": template.get("name", ""),
                    "description": template.get("description", ""),
                    "icon": template.get("icon", "🤖"),
                    "architecture": template.get(
                        "architecture", AgentArchitecture.REACT
                    ).value,
                    "mode": template.get("mode", AgentMode.ASSIST).value,
                    "tools": template.get("tools", []),
                    "capabilities": template.get("capabilities", []),
                }
            )
        return templates

    # ==========================================
    # Internal Helpers
    # ==========================================

    def _analyze_description(self, description: str) -> AgentType:
        """Analyze description to suggest agent type."""
        description_lower = description.lower()

        keywords_map = {
            AgentType.SALES_ASSISTANT: [
                "sales",
                "lead",
                "deal",
                "prospect",
                "revenue",
                "quota",
            ],
            AgentType.SUPPORT_AGENT: [
                "support",
                "help",
                "ticket",
                "issue",
                "problem",
                "customer service",
            ],
            AgentType.SCHEDULER: [
                "schedule",
                "book",
                "appointment",
                "meeting",
                "calendar",
            ],
            AgentType.COLLECTIONS: [
                "payment",
                "invoice",
                "collect",
                "overdue",
                "billing",
            ],
            AgentType.MARKETING: [
                "marketing",
                "campaign",
                "social",
                "content",
                "email blast",
            ],
            AgentType.HR_ASSISTANT: ["hr", "employee", "leave", "onboarding", "policy"],
            AgentType.OPS_COORDINATOR: [
                "operations",
                "task",
                "project",
                "coordinate",
                "vendor",
            ],
            AgentType.ANALYST: ["analyze", "report", "data", "insight", "trend"],
            AgentType.CONCIERGE: [
                "concierge",
                "guest",
                "hotel",
                "hospitality",
                "recommendation",
            ],
        }

        for agent_type, keywords in keywords_map.items():
            if any(kw in description_lower for kw in keywords):
                return agent_type

        return AgentType.CUSTOM

    async def _simulate_agent_response(
        self,
        agent: AIAgent,
        message: str,
    ) -> dict:
        """Simulate agent response for testing."""
        # In production, this would call the actual LLM
        await asyncio.sleep(0.5)  # Simulate latency

        response_templates = {
            AgentType.SALES_ASSISTANT: f"I'd be happy to help with that! Based on your message about '{message[:30]}...', let me check our CRM for relevant opportunities.",
            AgentType.SUPPORT_AGENT: f"Thank you for reaching out! I understand you need help with '{message[:30]}...'. Let me look into this for you.",
            AgentType.SCHEDULER: "I can help you schedule that. Looking at your calendar, I see some available slots.",
            AgentType.CONCIERGE: f"Welcome! I'd love to assist you with '{message[:30]}...'. Let me find the best options for you.",
        }

        response_text = response_templates.get(
            agent.agent_type,
            f"I understand. You mentioned: '{message[:50]}...'. How would you like me to help?",
        )

        return {
            "message": response_text,
            "confidence": 0.85,
            "suggested_actions": [
                {
                    "action": "search_knowledge_base",
                    "reason": "Find relevant information",
                },
                {"action": "check_availability", "reason": "Verify options"},
            ],
            "mode": agent.mode.value,
        }

    def _generate_system_prompt(self, agent: AIAgent) -> str:
        """Generate system prompt for agent."""
        tools_list = ", ".join(agent.tools) if agent.tools else "none"

        prompt = f"""You are {agent.persona.name}, a {agent.agent_type.value.replace('_', ' ')}.

Role: {agent.persona.role or agent.description}

Tone: {agent.persona.tone}
Language: {agent.persona.language}

Available Tools: {tools_list}

Operating Mode: {agent.mode.value}
- suggest: Recommend actions, wait for human approval
- assist: Draft responses, human reviews before sending
- act: Execute autonomously for routine tasks

Capabilities:
{chr(10).join(f'- {cap}' for cap in agent.capabilities)}

Escalation Rules:
{chr(10).join(f'- {rule.condition} → {rule.action} ({rule.priority})' for rule in agent.escalation_rules)}

Security Guidelines:
- PII Redaction: {'Enabled' if agent.security.pii_redaction else 'Disabled'}
- Audit: {'All actions logged' if agent.security.audit_all_actions else 'Limited logging'}
- Human-in-loop threshold: {agent.security.human_in_loop_threshold}

Always maintain a {agent.persona.tone} tone and follow the escalation rules strictly.
If unsure, use the fallback: "{agent.persona.fallback_message}"
"""
        return prompt


# ============================================
# Singleton
# ============================================

_aiva_service: Optional[AIVAService] = None


def get_aiva_service() -> AIVAService:
    """Get AIVA service singleton."""
    global _aiva_service
    if _aiva_service is None:
        _aiva_service = AIVAService()
    return _aiva_service


__all__ = [
    "AIVAService",
    "AIAgent",
    "AgentTool",
    "AgentType",
    "AgentArchitecture",
    "AgentMode",
    "AgentStatus",
    "AgentPersona",
    "AgentMemory",
    "AgentSecurity",
    "AgentMetrics",
    "EscalationRule",
    "ToolCategory",
    "AGENT_TEMPLATES",
    "AVAILABLE_TOOLS",
    "get_aiva_service",
]
