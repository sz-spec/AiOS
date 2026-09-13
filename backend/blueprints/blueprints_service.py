"""
Industry Blueprints Service

Pre-built, industry-specific configurations:
- Healthcare (clinics, hospitals, dental)
- Real Estate (agents, property management)
- Retail (stores, e-commerce, restaurants)
- Professional Services (legal, accounting, consulting)
- Fitness & Wellness (gyms, spas, studios)
- Education (schools, tutoring, courses)

Each blueprint includes:
- Pre-configured agents
- Industry workflows
- Compliance settings
- Integration presets
- Sample data

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class Industry(str, Enum):
    HEALTHCARE = "healthcare"
    REAL_ESTATE = "real_estate"
    RETAIL = "retail"
    PROFESSIONAL_SERVICES = "professional_services"
    FITNESS_WELLNESS = "fitness_wellness"
    EDUCATION = "education"
    HOSPITALITY = "hospitality"
    AUTOMOTIVE = "automotive"


class BlueprintStatus(str, Enum):
    AVAILABLE = "available"
    COMING_SOON = "coming_soon"
    BETA = "beta"


class ComplianceLevel(str, Enum):
    NONE = "none"
    BASIC = "basic"
    HIPAA = "hipaa"
    PCI_DSS = "pci_dss"
    GDPR = "gdpr"
    SOC2 = "soc2"


@dataclass
class AgentTemplate:
    id: str
    name: str
    description: str
    role: str
    capabilities: list[str]
    tools: list[str]
    prompts: dict[str, str]
    settings: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "prompts": self.prompts,
            "settings": self.settings,
        }


@dataclass
class WorkflowTemplate:
    id: str
    name: str
    description: str
    trigger: str
    steps: list[dict]
    automations: list[dict]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trigger": self.trigger,
            "steps": self.steps,
            "automations": self.automations,
        }


@dataclass
class IntegrationPreset:
    id: str
    name: str
    type: str
    provider: str
    config: dict[str, Any]
    required: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "provider": self.provider,
            "config": self.config,
            "required": self.required,
        }


@dataclass
class ComplianceConfig:
    level: ComplianceLevel
    requirements: list[str]
    data_retention_days: int
    encryption_required: bool
    audit_logging: bool
    consent_required: bool
    pii_handling: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "requirements": self.requirements,
            "data_retention_days": self.data_retention_days,
            "encryption_required": self.encryption_required,
            "audit_logging": self.audit_logging,
            "consent_required": self.consent_required,
            "pii_handling": self.pii_handling,
        }


@dataclass
class Blueprint:
    id: str = field(default_factory=lambda: f"bp_{uuid4().hex[:12]}")
    industry: Industry = Industry.HEALTHCARE
    name: str = ""
    tagline: str = ""
    description: str = ""
    icon: str = ""
    color: str = "#6366f1"
    status: BlueprintStatus = BlueprintStatus.AVAILABLE
    version: str = "1.0.0"
    agents: list[AgentTemplate] = field(default_factory=list)
    workflows: list[WorkflowTemplate] = field(default_factory=list)
    integrations: list[IntegrationPreset] = field(default_factory=list)
    compliance: Optional[ComplianceConfig] = None
    features: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    sample_data: dict[str, Any] = field(default_factory=dict)
    deployments: int = 0
    rating: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "industry": self.industry.value,
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "status": self.status.value,
            "version": self.version,
            "agents": [a.to_dict() for a in self.agents],
            "workflows": [w.to_dict() for w in self.workflows],
            "integrations": [i.to_dict() for i in self.integrations],
            "compliance": self.compliance.to_dict() if self.compliance else None,
            "features": self.features,
            "use_cases": self.use_cases,
            "sample_data": self.sample_data,
            "deployments": self.deployments,
            "rating": self.rating,
            "created_at": self.created_at.isoformat(),
        }

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "industry": self.industry.value,
            "name": self.name,
            "tagline": self.tagline,
            "icon": self.icon,
            "color": self.color,
            "status": self.status.value,
            "features": self.features[:5],
            "agents_count": len(self.agents),
            "workflows_count": len(self.workflows),
            "deployments": self.deployments,
            "rating": self.rating,
        }


@dataclass
class BlueprintDeployment:
    id: str = field(default_factory=lambda: f"dep_{uuid4().hex[:12]}")
    blueprint_id: str = ""
    user_id: str = ""
    organization_id: Optional[str] = None
    customizations: dict[str, Any] = field(default_factory=dict)
    enabled_agents: list[str] = field(default_factory=list)
    enabled_workflows: list[str] = field(default_factory=list)
    status: str = "active"
    deployed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "blueprint_id": self.blueprint_id,
            "customizations": self.customizations,
            "enabled_agents": self.enabled_agents,
            "enabled_workflows": self.enabled_workflows,
            "status": self.status,
            "deployed_at": self.deployed_at.isoformat(),
        }


# Blueprint Definitions
def create_healthcare_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_healthcare",
        industry=Industry.HEALTHCARE,
        name="Healthcare & Medical",
        tagline="HIPAA-compliant patient management",
        description="Complete solution for clinics, hospitals, and medical practices. Includes patient scheduling, appointment reminders, intake forms, and secure communication.",
        icon="🏥",
        color="#10b981",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_patient_intake",
                name="Patient Intake Agent",
                description="Handles new patient registration and intake forms",
                role="intake_specialist",
                capabilities=[
                    "collect_patient_info",
                    "verify_insurance",
                    "schedule_appointment",
                    "send_forms",
                ],
                tools=["forms", "calendar", "email", "sms"],
                prompts={
                    "greeting": "Welcome to {clinic_name}! I'm here to help you get started.",
                    "insurance": "I'll need to verify your insurance. What is your provider?",
                },
                settings={"language": "en", "hipaa_mode": True},
            ),
            AgentTemplate(
                id="agent_appointment",
                name="Appointment Manager",
                description="Manages scheduling, reminders, and rescheduling",
                role="scheduler",
                capabilities=[
                    "check_availability",
                    "book_appointment",
                    "send_reminder",
                    "handle_cancellation",
                ],
                tools=["calendar", "sms", "email", "voice"],
                prompts={
                    "reminder": "Hi {patient_name}, reminder for your appointment tomorrow at {time} with Dr. {doctor}.",
                    "confirm": "Your appointment is confirmed for {date} at {time}.",
                },
                settings={"reminder_hours": [48, 24, 2], "allow_self_schedule": True},
            ),
            AgentTemplate(
                id="agent_follow_up",
                name="Patient Follow-up Agent",
                description="Handles post-visit follow-ups and care coordination",
                role="care_coordinator",
                capabilities=[
                    "send_follow_up",
                    "collect_feedback",
                    "schedule_follow_up",
                    "medication_reminder",
                ],
                tools=["email", "sms", "forms"],
                prompts={
                    "follow_up": "Hi {patient_name}, how are you feeling after your visit?",
                    "medication": "Reminder: It's time to take your {medication}.",
                },
                settings={"follow_up_days": [1, 7, 30]},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_new_patient",
                name="New Patient Onboarding",
                description="Complete workflow for new patient registration",
                trigger="new_patient_request",
                steps=[
                    {"action": "send_intake_form", "channel": "email"},
                    {"action": "verify_insurance", "auto": True},
                    {"action": "schedule_appointment", "channel": "sms"},
                    {"action": "send_confirmation", "channel": "email"},
                ],
                automations=[
                    {"trigger": "form_completed", "action": "create_patient_record"},
                    {"trigger": "insurance_verified", "action": "notify_billing"},
                ],
            ),
            WorkflowTemplate(
                id="wf_appointment_reminder",
                name="Appointment Reminder Sequence",
                description="Multi-channel appointment reminders",
                trigger="appointment_scheduled",
                steps=[
                    {"action": "send_confirmation", "channel": "email", "delay": "0"},
                    {"action": "send_reminder", "channel": "sms", "delay": "48h"},
                    {"action": "send_reminder", "channel": "sms", "delay": "24h"},
                ],
                automations=[
                    {"trigger": "no_response", "action": "escalate_to_staff"},
                    {"trigger": "cancellation", "action": "offer_reschedule"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_ehr",
                name="EHR System",
                type="ehr",
                provider="epic",
                config={"api_version": "R4"},
                required=True,
            ),
            IntegrationPreset(
                id="int_insurance",
                name="Insurance Verification",
                type="insurance",
                provider="availity",
                config={},
            ),
            IntegrationPreset(
                id="int_telehealth",
                name="Telehealth",
                type="video",
                provider="doxy",
                config={},
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.HIPAA,
            requirements=["BAA", "encryption", "access_controls", "audit_logs"],
            data_retention_days=2555,
            encryption_required=True,
            audit_logging=True,
            consent_required=True,
            pii_handling={
                "ssn": "masked",
                "dob": "encrypted",
                "medical_records": "encrypted",
            },
        ),
        features=[
            "HIPAA-compliant messaging",
            "Patient intake automation",
            "Appointment scheduling & reminders",
            "Insurance verification",
            "Telehealth integration",
            "Post-visit follow-ups",
            "Medication reminders",
            "Secure document sharing",
        ],
        use_cases=[
            "Medical clinics",
            "Dental practices",
            "Mental health providers",
            "Physical therapy",
            "Specialty practices",
        ],
        sample_data={"patients": 50, "appointments": 200, "providers": 5},
        deployments=1247,
        rating=4.8,
    )


def create_real_estate_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_real_estate",
        industry=Industry.REAL_ESTATE,
        name="Real Estate",
        tagline="Close more deals, faster",
        description="Complete solution for real estate agents and property managers. Lead capture, property matching, showing scheduling, and transaction management.",
        icon="🏠",
        color="#8b5cf6",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_lead_capture",
                name="Lead Capture Agent",
                description="Captures and qualifies incoming leads 24/7",
                role="lead_qualifier",
                capabilities=[
                    "capture_lead",
                    "qualify_lead",
                    "collect_preferences",
                    "schedule_call",
                ],
                tools=["chat", "forms", "crm", "calendar"],
                prompts={
                    "greeting": "Hi! I'm here to help you find your perfect property. Are you looking to buy or rent?",
                    "budget": "What's your budget range?",
                },
                settings={"auto_assign": True, "response_time_sla": 60},
            ),
            AgentTemplate(
                id="agent_property_matcher",
                name="Property Matcher",
                description="Matches leads with suitable properties",
                role="property_specialist",
                capabilities=[
                    "search_listings",
                    "match_preferences",
                    "send_recommendations",
                    "track_interest",
                ],
                tools=["mls", "email", "sms"],
                prompts={
                    "match": "Based on your preferences, I found {count} properties!",
                    "new_listing": "New listing alert! A {beds}BR/{baths}BA just hit the market.",
                },
                settings={"match_threshold": 0.7, "max_recommendations": 5},
            ),
            AgentTemplate(
                id="agent_showing",
                name="Showing Coordinator",
                description="Schedules and manages property showings",
                role="showing_coordinator",
                capabilities=[
                    "check_availability",
                    "schedule_showing",
                    "send_reminder",
                    "collect_feedback",
                ],
                tools=["calendar", "sms", "email"],
                prompts={
                    "schedule": "I can schedule a showing for {property}. What times work best?",
                    "feedback": "How was your showing? Would you like to make an offer?",
                },
                settings={"buffer_time": 30, "max_showings_day": 8},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_lead_nurture",
                name="Lead Nurture Sequence",
                description="Automated lead nurturing campaign",
                trigger="new_lead",
                steps=[
                    {"action": "send_welcome", "channel": "email", "delay": "0"},
                    {"action": "send_listings", "channel": "email", "delay": "1d"},
                    {"action": "follow_up_call", "channel": "phone", "delay": "2d"},
                ],
                automations=[
                    {"trigger": "listing_viewed", "action": "send_similar"},
                    {"trigger": "showing_completed", "action": "send_feedback_request"},
                ],
            ),
            WorkflowTemplate(
                id="wf_transaction",
                name="Transaction Management",
                description="End-to-end transaction workflow",
                trigger="offer_accepted",
                steps=[
                    {"action": "notify_parties", "channel": "email"},
                    {"action": "schedule_inspection", "channel": "calendar"},
                    {"action": "track_contingencies", "auto": True},
                ],
                automations=[
                    {"trigger": "document_needed", "action": "request_document"},
                    {"trigger": "deadline_approaching", "action": "send_reminder"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_mls",
                name="MLS Feed",
                type="listings",
                provider="rets",
                config={},
                required=True,
            ),
            IntegrationPreset(
                id="int_crm",
                name="Real Estate CRM",
                type="crm",
                provider="followupboss",
                config={},
            ),
            IntegrationPreset(
                id="int_docusign",
                name="DocuSign",
                type="esign",
                provider="docusign",
                config={},
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.BASIC,
            requirements=["fair_housing", "do_not_call"],
            data_retention_days=365,
            encryption_required=False,
            audit_logging=True,
            consent_required=True,
            pii_handling={"ssn": "not_collected", "financial": "encrypted"},
        ),
        features=[
            "24/7 lead capture",
            "AI property matching",
            "Automated showing scheduling",
            "MLS integration",
            "Transaction management",
            "Market reports",
            "Client portal",
            "E-signature integration",
        ],
        use_cases=[
            "Real estate agents",
            "Property managers",
            "Real estate teams",
            "Brokerages",
            "Rental agencies",
        ],
        sample_data={"leads": 100, "properties": 50, "showings": 200},
        deployments=892,
        rating=4.7,
    )


def create_retail_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_retail",
        industry=Industry.RETAIL,
        name="Retail & E-Commerce",
        tagline="Sell more, support better",
        description="Complete retail solution for online and brick-and-mortar stores. Order support, product recommendations, inventory alerts, and customer engagement.",
        icon="🛍️",
        color="#f59e0b",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_shopping",
                name="Shopping Assistant",
                description="Helps customers find and purchase products",
                role="sales_assistant",
                capabilities=[
                    "product_search",
                    "recommendations",
                    "check_inventory",
                    "process_order",
                ],
                tools=["catalog", "inventory", "cart", "payments"],
                prompts={
                    "greeting": "Welcome! What are you looking for today?",
                    "recommend": "Based on your interests, you might like these:",
                    "upsell": "Customers who bought this also loved {product}!",
                },
                settings={"recommend_count": 4, "show_reviews": True},
            ),
            AgentTemplate(
                id="agent_order_support",
                name="Order Support Agent",
                description="Handles order inquiries, tracking, and issues",
                role="support_agent",
                capabilities=[
                    "track_order",
                    "process_return",
                    "issue_refund",
                    "modify_order",
                ],
                tools=["orders", "shipping", "payments", "email"],
                prompts={
                    "tracking": "Your order #{order_id} is {status}. Expected delivery: {date}.",
                    "return": "I can help with that return. What's the reason?",
                },
                settings={"auto_refund_threshold": 50, "return_window_days": 30},
            ),
            AgentTemplate(
                id="agent_restock",
                name="Restock Alert Agent",
                description="Notifies customers when items are back in stock",
                role="inventory_notifier",
                capabilities=[
                    "track_inventory",
                    "notify_restock",
                    "waitlist_management",
                ],
                tools=["inventory", "email", "sms", "push"],
                prompts={
                    "waitlist": "I'll notify you as soon as {product} is back in stock!",
                    "restock": "Great news! {product} is back in stock!",
                },
                settings={"notify_channels": ["email", "sms"]},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_abandoned_cart",
                name="Abandoned Cart Recovery",
                description="Recover abandoned shopping carts",
                trigger="cart_abandoned",
                steps=[
                    {"action": "send_reminder", "channel": "email", "delay": "1h"},
                    {"action": "send_reminder", "channel": "sms", "delay": "24h"},
                    {"action": "send_discount", "channel": "email", "delay": "48h"},
                ],
                automations=[
                    {"trigger": "cart_recovered", "action": "stop_sequence"},
                    {"trigger": "discount_used", "action": "track_conversion"},
                ],
            ),
            WorkflowTemplate(
                id="wf_post_purchase",
                name="Post-Purchase Engagement",
                description="Engage customers after purchase",
                trigger="order_delivered",
                steps=[
                    {"action": "request_review", "channel": "email", "delay": "3d"},
                    {
                        "action": "send_recommendations",
                        "channel": "email",
                        "delay": "7d",
                    },
                ],
                automations=[
                    {"trigger": "review_submitted", "action": "send_thank_you"},
                    {"trigger": "repeat_purchase", "action": "upgrade_loyalty"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_shopify",
                name="Shopify",
                type="ecommerce",
                provider="shopify",
                config={},
            ),
            IntegrationPreset(
                id="int_stripe",
                name="Stripe Payments",
                type="payments",
                provider="stripe",
                config={},
                required=True,
            ),
            IntegrationPreset(
                id="int_shipping",
                name="Shipping",
                type="logistics",
                provider="shippo",
                config={},
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.PCI_DSS,
            requirements=["pci_compliance", "consumer_protection"],
            data_retention_days=365,
            encryption_required=True,
            audit_logging=True,
            consent_required=True,
            pii_handling={"credit_card": "tokenized", "address": "encrypted"},
        ),
        features=[
            "AI shopping assistant",
            "Product recommendations",
            "Order tracking & support",
            "Abandoned cart recovery",
            "Inventory alerts",
            "Review collection",
            "Loyalty programs",
            "Multi-channel support",
        ],
        use_cases=[
            "E-commerce stores",
            "Retail chains",
            "Boutiques",
            "Restaurants",
            "Subscription boxes",
        ],
        sample_data={"products": 500, "orders": 1000, "customers": 300},
        deployments=2156,
        rating=4.6,
    )


def create_professional_services_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_professional",
        industry=Industry.PROFESSIONAL_SERVICES,
        name="Professional Services",
        tagline="Elevate your client experience",
        description="Solution for law firms, accounting practices, and consulting firms. Client intake, appointment scheduling, document management, and billing.",
        icon="💼",
        color="#3b82f6",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_intake",
                name="Client Intake Agent",
                description="Handles new client inquiries and intake",
                role="intake_coordinator",
                capabilities=[
                    "qualify_lead",
                    "collect_info",
                    "conflict_check",
                    "schedule_consultation",
                ],
                tools=["forms", "calendar", "crm", "email"],
                prompts={
                    "greeting": "Thank you for contacting {firm_name}. How can we assist you today?",
                    "qualification": "Can you briefly describe your situation?",
                },
                settings={"require_conflict_check": True},
            ),
            AgentTemplate(
                id="agent_scheduling",
                name="Appointment Scheduler",
                description="Manages consultations and meetings",
                role="scheduler",
                capabilities=[
                    "check_availability",
                    "book_meeting",
                    "send_reminder",
                    "reschedule",
                ],
                tools=["calendar", "video", "sms", "email"],
                prompts={
                    "availability": "I have openings on {dates}. Which works best?",
                    "confirmation": "Your consultation with {professional} is confirmed for {datetime}.",
                },
                settings={"consultation_duration": 60, "buffer_time": 15},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_client_onboard",
                name="Client Onboarding",
                description="Complete client onboarding workflow",
                trigger="engagement_signed",
                steps=[
                    {"action": "send_welcome_packet", "channel": "email"},
                    {"action": "collect_documents", "channel": "portal"},
                    {"action": "schedule_kickoff", "channel": "calendar"},
                ],
                automations=[
                    {"trigger": "documents_received", "action": "notify_team"},
                    {"trigger": "kickoff_complete", "action": "start_project"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_clio",
                name="Clio (Legal)",
                type="practice_mgmt",
                provider="clio",
                config={},
            ),
            IntegrationPreset(
                id="int_qbo",
                name="QuickBooks",
                type="accounting",
                provider="quickbooks",
                config={},
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.SOC2,
            requirements=["client_confidentiality", "data_security"],
            data_retention_days=2555,
            encryption_required=True,
            audit_logging=True,
            consent_required=True,
            pii_handling={"financial": "encrypted", "legal_docs": "encrypted"},
        ),
        features=[
            "Client intake automation",
            "Conflict checking",
            "Appointment scheduling",
            "Document management",
            "Secure client portal",
            "Time tracking",
            "Billing integration",
            "Matter management",
        ],
        use_cases=[
            "Law firms",
            "Accounting firms",
            "Consulting firms",
            "Financial advisors",
            "Architecture firms",
        ],
        sample_data={"clients": 50, "matters": 100},
        deployments=654,
        rating=4.7,
    )


def create_fitness_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_fitness",
        industry=Industry.FITNESS_WELLNESS,
        name="Fitness & Wellness",
        tagline="Build stronger member relationships",
        description="Complete solution for gyms, studios, and wellness centers. Member management, class booking, trainer scheduling, and engagement.",
        icon="💪",
        color="#ec4899",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_member",
                name="Member Concierge",
                description="Assists members with bookings and inquiries",
                role="concierge",
                capabilities=[
                    "class_booking",
                    "membership_info",
                    "trainer_booking",
                    "facility_info",
                ],
                tools=["calendar", "memberships", "classes", "chat"],
                prompts={
                    "greeting": "Hey! Ready to crush your workout? How can I help?",
                    "class": "I found {count} classes that match. Which one?",
                },
                settings={"casual_tone": True},
            ),
            AgentTemplate(
                id="agent_motivation",
                name="Motivation Coach",
                description="Sends motivational messages and tracks goals",
                role="coach",
                capabilities=[
                    "goal_tracking",
                    "send_motivation",
                    "celebrate_milestones",
                    "workout_reminder",
                ],
                tools=["sms", "push", "goals"],
                prompts={
                    "motivation": "You've got this! Just {sessions} more to hit your goal!",
                    "milestone": "🎉 Amazing! You just hit {milestone}!",
                },
                settings={"message_frequency": "daily"},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_member_journey",
                name="New Member Journey",
                description="Onboard and engage new members",
                trigger="membership_started",
                steps=[
                    {"action": "send_welcome", "channel": "email"},
                    {"action": "schedule_orientation", "channel": "sms"},
                    {"action": "first_class_reminder", "channel": "push"},
                ],
                automations=[
                    {"trigger": "first_class_attended", "action": "celebrate"},
                    {"trigger": "no_visits_7_days", "action": "re_engage"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_mindbody",
                name="Mindbody",
                type="gym_mgmt",
                provider="mindbody",
                config={},
            ),
            IntegrationPreset(
                id="int_stripe",
                name="Payments",
                type="payments",
                provider="stripe",
                config={},
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.BASIC,
            requirements=["health_waiver", "payment_security"],
            data_retention_days=365,
            encryption_required=False,
            audit_logging=False,
            consent_required=True,
            pii_handling={"health_info": "secure", "payment": "tokenized"},
        ),
        features=[
            "Class booking",
            "Trainer scheduling",
            "Member engagement",
            "Goal tracking",
            "Attendance tracking",
            "Payment processing",
            "Push notifications",
            "Referral programs",
        ],
        use_cases=[
            "Gyms",
            "Yoga studios",
            "CrossFit boxes",
            "Pilates studios",
            "Wellness centers",
            "Spas",
        ],
        sample_data={"members": 200, "classes": 50, "trainers": 10},
        deployments=1089,
        rating=4.8,
    )


def create_education_blueprint() -> Blueprint:
    return Blueprint(
        id="bp_education",
        industry=Industry.EDUCATION,
        name="Education",
        tagline="Engage students, empower learning",
        description="Solution for schools, tutoring centers, and online courses. Student enrollment, class scheduling, parent communication, and progress tracking.",
        icon="📚",
        color="#14b8a6",
        status=BlueprintStatus.AVAILABLE,
        agents=[
            AgentTemplate(
                id="agent_enrollment",
                name="Enrollment Agent",
                description="Handles student enrollment inquiries",
                role="admissions",
                capabilities=[
                    "answer_questions",
                    "collect_info",
                    "schedule_tour",
                    "process_application",
                ],
                tools=["forms", "calendar", "email", "chat"],
                prompts={
                    "greeting": "Welcome! I'm here to help you learn more about our programs.",
                    "tour": "Would you like to schedule a campus tour?",
                },
                settings={"follow_up_days": 3},
            ),
            AgentTemplate(
                id="agent_parent",
                name="Parent Communication Agent",
                description="Keeps parents informed and engaged",
                role="communicator",
                capabilities=[
                    "send_updates",
                    "share_progress",
                    "notify_events",
                    "answer_questions",
                ],
                tools=["email", "sms", "portal"],
                prompts={
                    "progress": "Here's {student}'s progress report for this week.",
                    "event": "Reminder: {event} is coming up on {date}!",
                },
                settings={"digest_frequency": "weekly"},
            ),
        ],
        workflows=[
            WorkflowTemplate(
                id="wf_enrollment",
                name="Student Enrollment",
                description="Complete enrollment workflow",
                trigger="application_submitted",
                steps=[
                    {"action": "send_confirmation", "channel": "email"},
                    {"action": "review_application", "auto": True},
                    {"action": "schedule_interview", "channel": "email"},
                ],
                automations=[
                    {"trigger": "accepted", "action": "send_welcome_packet"},
                    {"trigger": "enrolled", "action": "create_student_profile"},
                ],
            ),
        ],
        integrations=[
            IntegrationPreset(
                id="int_canvas",
                name="Canvas LMS",
                type="lms",
                provider="canvas",
                config={},
            ),
            IntegrationPreset(
                id="int_zoom", name="Zoom", type="video", provider="zoom", config={}
            ),
        ],
        compliance=ComplianceConfig(
            level=ComplianceLevel.BASIC,
            requirements=["ferpa", "coppa", "student_privacy"],
            data_retention_days=2555,
            encryption_required=True,
            audit_logging=True,
            consent_required=True,
            pii_handling={"student_records": "encrypted", "grades": "encrypted"},
        ),
        features=[
            "Online enrollment",
            "Class scheduling",
            "Parent portal",
            "Progress tracking",
            "Attendance tracking",
            "Event notifications",
            "LMS integration",
            "Video conferencing",
        ],
        use_cases=[
            "K-12 schools",
            "Tutoring centers",
            "Online courses",
            "Language schools",
            "Test prep",
            "Music schools",
        ],
        sample_data={"students": 100, "classes": 20, "teachers": 15},
        deployments=567,
        rating=4.5,
    )


class BlueprintsService:
    def __init__(self):
        self._blueprints: dict[str, Blueprint] = {}
        self._deployments: dict[str, BlueprintDeployment] = {}
        self._init_blueprints()

    def _init_blueprints(self):
        for bp in [
            create_healthcare_blueprint(),
            create_real_estate_blueprint(),
            create_retail_blueprint(),
            create_professional_services_blueprint(),
            create_fitness_blueprint(),
            create_education_blueprint(),
        ]:
            self._blueprints[bp.id] = bp

    def get_blueprints(
        self,
        industry: Optional[Industry] = None,
        status: Optional[BlueprintStatus] = None,
    ) -> list[Blueprint]:
        blueprints = list(self._blueprints.values())
        if industry:
            blueprints = [bp for bp in blueprints if bp.industry == industry]
        if status:
            blueprints = [bp for bp in blueprints if bp.status == status]
        return sorted(blueprints, key=lambda bp: bp.deployments, reverse=True)

    def get_blueprint(self, blueprint_id: str) -> Optional[Blueprint]:
        return self._blueprints.get(blueprint_id)

    def get_industries(self) -> list[dict]:
        counts = {}
        for bp in self._blueprints.values():
            counts[bp.industry] = counts.get(bp.industry, 0) + 1
        return [
            {
                "industry": ind.value,
                "name": ind.name.replace("_", " ").title(),
                "count": counts.get(ind, 0),
            }
            for ind in Industry
        ]

    def deploy_blueprint(
        self,
        blueprint_id: str,
        user_id: str,
        organization_id: Optional[str] = None,
        customizations: dict = None,
    ) -> Optional[BlueprintDeployment]:
        blueprint = self._blueprints.get(blueprint_id)
        if not blueprint:
            return None
        deployment = BlueprintDeployment(
            blueprint_id=blueprint_id,
            user_id=user_id,
            organization_id=organization_id,
            customizations=customizations or {},
            enabled_agents=[a.id for a in blueprint.agents],
            enabled_workflows=[w.id for w in blueprint.workflows],
        )
        self._deployments[deployment.id] = deployment
        blueprint.deployments += 1
        return deployment

    def get_deployment(self, deployment_id: str) -> Optional[BlueprintDeployment]:
        return self._deployments.get(deployment_id)

    def get_user_deployments(self, user_id: str) -> list[BlueprintDeployment]:
        return [d for d in self._deployments.values() if d.user_id == user_id]


_blueprints_service: Optional[BlueprintsService] = None


def get_blueprints_service() -> BlueprintsService:
    global _blueprints_service
    if _blueprints_service is None:
        _blueprints_service = BlueprintsService()
    return _blueprints_service


__all__ = [
    "BlueprintsService",
    "Blueprint",
    "AgentTemplate",
    "WorkflowTemplate",
    "IntegrationPreset",
    "ComplianceConfig",
    "BlueprintDeployment",
    "Industry",
    "BlueprintStatus",
    "ComplianceLevel",
    "get_blueprints_service",
]
