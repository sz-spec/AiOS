"""
V Core - Business Core

Customizable Data Model + Custom Fields
Based on V PRD Section 4.3

Features:
- Dynamic entity definitions
- Custom fields (text, number, date, select, relation, etc.)
- Entity relationships
- Data validation
- Field-level permissions
- Calculated fields
- Data history/versioning
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from core.base_service import PersistentService

logger = logging.getLogger(__name__)

# Try to import repository factories
try:
    from core.repositories import get_entity_repository, get_record_repository
except ImportError:
    pass


# ============================================
# Enums
# ============================================


class FieldType(str, Enum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    BOOLEAN = "boolean"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    FILE = "file"
    IMAGE = "image"
    RELATION = "relation"
    LOOKUP = "lookup"
    FORMULA = "formula"
    ROLLUP = "rollup"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    CREATED_BY = "created_by"
    UPDATED_BY = "updated_by"


class RelationType(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_MANY = "many_to_many"


class ValidationRule(str, Enum):
    REQUIRED = "required"
    UNIQUE = "unique"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    PATTERN = "pattern"
    CUSTOM = "custom"


# ============================================
# Field Definitions
# ============================================


@dataclass
class SelectOption:
    """Option for select/multi-select fields."""

    value: str
    label: str
    color: Optional[str] = None

    def to_dict(self) -> dict:
        return {"value": self.value, "label": self.label, "color": self.color}


@dataclass
class FieldValidation:
    """Field validation rule."""

    rule: ValidationRule
    value: Any = None
    message: str = ""

    def to_dict(self) -> dict:
        return {"rule": self.rule.value, "value": self.value, "message": self.message}


@dataclass
class FieldDefinition:
    """Custom field definition."""

    id: str = field(default_factory=lambda: f"fld_{uuid4().hex[:12]}")
    name: str = ""
    label: str = ""
    type: FieldType = FieldType.TEXT
    description: str = ""

    # Options for select fields
    options: list[SelectOption] = field(default_factory=list)

    # Relation settings
    related_entity_id: Optional[str] = None
    relation_type: Optional[RelationType] = None

    # Formula/rollup settings
    formula: Optional[str] = None
    rollup_field: Optional[str] = None
    rollup_function: Optional[str] = None  # sum, count, avg, min, max

    # Validation
    validations: list[FieldValidation] = field(default_factory=list)

    # Display
    is_required: bool = False
    is_unique: bool = False
    is_searchable: bool = True
    is_sortable: bool = True
    is_filterable: bool = True
    is_hidden: bool = False

    # Default value
    default_value: Any = None

    # Order
    order: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "type": self.type.value,
            "description": self.description,
            "options": [o.to_dict() for o in self.options],
            "relatedEntityId": self.related_entity_id,
            "relationType": self.relation_type.value if self.relation_type else None,
            "formula": self.formula,
            "rollupField": self.rollup_field,
            "rollupFunction": self.rollup_function,
            "validations": [v.to_dict() for v in self.validations],
            "isRequired": self.is_required,
            "isUnique": self.is_unique,
            "isSearchable": self.is_searchable,
            "isSortable": self.is_sortable,
            "isFilterable": self.is_filterable,
            "isHidden": self.is_hidden,
            "defaultValue": self.default_value,
            "order": self.order,
        }


# ============================================
# Entity Definition
# ============================================


@dataclass
class EntityDefinition:
    """Dynamic entity/table definition."""

    id: str = field(default_factory=lambda: f"ent_{uuid4().hex[:12]}")
    organization_id: str = ""
    name: str = ""  # Internal name (snake_case)
    label: str = ""  # Display name
    label_plural: str = ""
    description: str = ""
    icon: str = "📄"
    color: str = "#6366f1"

    # Fields
    fields: list[FieldDefinition] = field(default_factory=list)

    # Primary field for display
    primary_field_id: Optional[str] = None

    # Features
    enable_comments: bool = True
    enable_attachments: bool = True
    enable_history: bool = True

    # Display settings
    default_view: str = "table"  # table, kanban, calendar, gallery

    # System
    is_system: bool = False  # System entities can't be deleted

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "name": self.name,
            "label": self.label,
            "labelPlural": self.label_plural,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "fields": [f.to_dict() for f in self.fields],
            "primaryFieldId": self.primary_field_id,
            "enableComments": self.enable_comments,
            "enableAttachments": self.enable_attachments,
            "enableHistory": self.enable_history,
            "defaultView": self.default_view,
            "isSystem": self.is_system,
            "createdAt": self.created_at.isoformat(),
        }

    def get_field(self, field_id: str) -> Optional[FieldDefinition]:
        for f in self.fields:
            if f.id == field_id or f.name == field_id:
                return f
        return None


# ============================================
# Record (Data Instance)
# ============================================


@dataclass
class Record:
    """Data record instance."""

    id: str = field(default_factory=lambda: f"rec_{uuid4().hex[:12]}")
    entity_id: str = ""
    organization_id: str = ""

    # Field values
    data: dict[str, Any] = field(default_factory=dict)

    # Metadata
    created_by: str = ""
    updated_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Soft delete
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entityId": self.entity_id,
            "data": self.data,
            "createdBy": self.created_by,
            "updatedBy": self.updated_by,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "isDeleted": self.is_deleted,
        }


@dataclass
class RecordHistory:
    """Record change history."""

    id: str = field(default_factory=lambda: f"hist_{uuid4().hex[:12]}")
    record_id: str = ""
    entity_id: str = ""
    user_id: str = ""
    action: str = ""  # create, update, delete
    changes: dict[str, dict] = field(default_factory=dict)  # {field: {old, new}}
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "recordId": self.record_id,
            "userId": self.user_id,
            "action": self.action,
            "changes": self.changes,
            "timestamp": self.timestamp.isoformat(),
        }


# ============================================
# Task Complexity Calculation
# ============================================


class TaskType(str, Enum):
    """Task types for complexity calculation."""

    SIMPLE = "simple"  # Quick operations, basic CRUD
    GENERAL = "general"  # Standard tasks
    COMPLEX = "complex"  # Multi-step tasks, analysis
    CRITICAL = "critical"  # Architecture, security decisions


@dataclass
class TaskContext:
    """Context for a task with calculated complexity."""

    task_type: TaskType = TaskType.GENERAL
    description: str = ""
    complexity_score: int = 5
    role: str = "coding"

    # Metadata
    entity_id: Optional[str] = None
    record_count: int = 0
    field_count: int = 0

    def to_dict(self) -> dict:
        return {
            "taskType": self.task_type.value,
            "description": self.description,
            "complexityScore": self.complexity_score,
            "role": self.role,
            "entityId": self.entity_id,
            "recordCount": self.record_count,
            "fieldCount": self.field_count,
        }


def calculate_task_complexity(
    description: str,
    task_type: TaskType = TaskType.GENERAL,
    entity: Optional[EntityDefinition] = None,
    record_count: int = 0,
) -> TaskContext:
    """
    Calculate complexity score for a business task.

    This function is used to determine which LLM model should handle
    the task via SmartRouter.

    Args:
        description: Task description or prompt
        task_type: Type of task (affects base complexity)
        entity: Related entity (affects complexity based on schema)
        record_count: Number of records involved

    Returns:
        TaskContext with calculated complexity_score (1-10)
    """
    # Base complexity by task type
    base_scores = {
        TaskType.SIMPLE: 3,
        TaskType.GENERAL: 5,
        TaskType.COMPLEX: 7,
        TaskType.CRITICAL: 9,
    }
    base = base_scores.get(task_type, 5)

    # Adjust for description length
    text_len = len(description) if description else 0
    length_modifier = min(2, text_len // 500)  # +1 per 500 chars, max +2

    # Adjust for entity complexity
    entity_modifier = 0
    field_count = 0
    if entity:
        field_count = len(entity.fields)
        if field_count > 10:
            entity_modifier += 1
        if any(f.type in (FieldType.FORMULA, FieldType.ROLLUP) for f in entity.fields):
            entity_modifier += 1
        if any(f.type == FieldType.RELATION for f in entity.fields):
            entity_modifier += 1

    # Adjust for record count
    record_modifier = 0
    if record_count > 100:
        record_modifier = 1
    elif record_count > 1000:
        record_modifier = 2

    # Complexity keywords in description
    complexity_keywords = [
        "architecture",
        "security",
        "optimize",
        "refactor",
        "integration",
        "migration",
        "scale",
        "performance",
        "workflow",
        "automation",
        "aggregate",
        "analyze",
    ]
    keyword_modifier = sum(1 for kw in complexity_keywords if kw in description.lower())
    keyword_modifier = min(2, keyword_modifier)

    # Calculate final complexity
    complexity = (
        base + length_modifier + entity_modifier + record_modifier + keyword_modifier
    )
    complexity = max(1, min(10, complexity))

    # Determine role based on task type
    role_mapping = {
        TaskType.SIMPLE: "coding",
        TaskType.GENERAL: "coding",
        TaskType.COMPLEX: "architect",
        TaskType.CRITICAL: "architect",
    }
    role = role_mapping.get(task_type, "coding")

    return TaskContext(
        task_type=task_type,
        description=description[:200] if description else "",
        complexity_score=complexity,
        role=role,
        entity_id=entity.id if entity else None,
        record_count=record_count,
        field_count=field_count,
    )


# ============================================
# Built-in Entity Templates
# ============================================


def create_contact_entity(org_id: str) -> EntityDefinition:
    """Create Contact entity template."""
    return EntityDefinition(
        id=f"ent_{uuid4().hex[:12]}",
        organization_id=org_id,
        name="contacts",
        label="Contact",
        label_plural="Contacts",
        description="Customer and lead contact information",
        icon="👤",
        color="#3b82f6",
        fields=[
            FieldDefinition(
                id="fld_name",
                name="name",
                label="Name",
                type=FieldType.TEXT,
                is_required=True,
                order=1,
            ),
            FieldDefinition(
                id="fld_email",
                name="email",
                label="Email",
                type=FieldType.EMAIL,
                is_unique=True,
                order=2,
            ),
            FieldDefinition(
                id="fld_phone",
                name="phone",
                label="Phone",
                type=FieldType.PHONE,
                order=3,
            ),
            FieldDefinition(
                id="fld_company",
                name="company",
                label="Company",
                type=FieldType.TEXT,
                order=4,
            ),
            FieldDefinition(
                id="fld_title",
                name="title",
                label="Job Title",
                type=FieldType.TEXT,
                order=5,
            ),
            FieldDefinition(
                id="fld_status",
                name="status",
                label="Status",
                type=FieldType.SELECT,
                options=[
                    SelectOption("lead", "Lead", "#fbbf24"),
                    SelectOption("prospect", "Prospect", "#60a5fa"),
                    SelectOption("customer", "Customer", "#34d399"),
                    SelectOption("churned", "Churned", "#f87171"),
                ],
                default_value="lead",
                order=6,
            ),
            FieldDefinition(
                id="fld_source",
                name="source",
                label="Source",
                type=FieldType.SELECT,
                options=[
                    SelectOption("website", "Website", "#6366f1"),
                    SelectOption("referral", "Referral", "#8b5cf6"),
                    SelectOption("ads", "Ads", "#ec4899"),
                    SelectOption("other", "Other", "#6b7280"),
                ],
                order=7,
            ),
            FieldDefinition(
                id="fld_notes",
                name="notes",
                label="Notes",
                type=FieldType.LONG_TEXT,
                order=8,
            ),
            FieldDefinition(
                id="fld_tags",
                name="tags",
                label="Tags",
                type=FieldType.MULTI_SELECT,
                options=[
                    SelectOption("vip", "VIP", "#fbbf24"),
                    SelectOption("hot", "Hot Lead", "#ef4444"),
                    SelectOption("nurture", "Nurture", "#22c55e"),
                ],
                order=9,
            ),
        ],
        primary_field_id="fld_name",
    )


def create_deal_entity(org_id: str) -> EntityDefinition:
    """Create Deal/Opportunity entity template."""
    return EntityDefinition(
        id=f"ent_{uuid4().hex[:12]}",
        organization_id=org_id,
        name="deals",
        label="Deal",
        label_plural="Deals",
        description="Sales opportunities and deals",
        icon="💰",
        color="#10b981",
        fields=[
            FieldDefinition(
                id="fld_name",
                name="name",
                label="Deal Name",
                type=FieldType.TEXT,
                is_required=True,
                order=1,
            ),
            FieldDefinition(
                id="fld_value",
                name="value",
                label="Value",
                type=FieldType.CURRENCY,
                order=2,
            ),
            FieldDefinition(
                id="fld_stage",
                name="stage",
                label="Stage",
                type=FieldType.SELECT,
                options=[
                    SelectOption("lead", "Lead", "#94a3b8"),
                    SelectOption("qualified", "Qualified", "#60a5fa"),
                    SelectOption("proposal", "Proposal", "#a78bfa"),
                    SelectOption("negotiation", "Negotiation", "#fbbf24"),
                    SelectOption("won", "Won", "#34d399"),
                    SelectOption("lost", "Lost", "#f87171"),
                ],
                default_value="lead",
                order=3,
            ),
            FieldDefinition(
                id="fld_probability",
                name="probability",
                label="Probability %",
                type=FieldType.NUMBER,
                default_value=0,
                order=4,
            ),
            FieldDefinition(
                id="fld_close_date",
                name="close_date",
                label="Expected Close",
                type=FieldType.DATE,
                order=5,
            ),
            FieldDefinition(
                id="fld_contact",
                name="contact_id",
                label="Contact",
                type=FieldType.RELATION,
                relation_type=RelationType.MANY_TO_MANY,
                order=6,
            ),
            FieldDefinition(
                id="fld_owner",
                name="owner_id",
                label="Owner",
                type=FieldType.RELATION,
                order=7,
            ),
            FieldDefinition(
                id="fld_description",
                name="description",
                label="Description",
                type=FieldType.LONG_TEXT,
                order=8,
            ),
        ],
        primary_field_id="fld_name",
        default_view="kanban",
    )


def create_task_entity(org_id: str) -> EntityDefinition:
    """Create Task entity template."""
    return EntityDefinition(
        id=f"ent_{uuid4().hex[:12]}",
        organization_id=org_id,
        name="tasks",
        label="Task",
        label_plural="Tasks",
        description="Tasks and to-dos",
        icon="✓",
        color="#8b5cf6",
        fields=[
            FieldDefinition(
                id="fld_title",
                name="title",
                label="Title",
                type=FieldType.TEXT,
                is_required=True,
                order=1,
            ),
            FieldDefinition(
                id="fld_description",
                name="description",
                label="Description",
                type=FieldType.LONG_TEXT,
                order=2,
            ),
            FieldDefinition(
                id="fld_status",
                name="status",
                label="Status",
                type=FieldType.SELECT,
                options=[
                    SelectOption("todo", "To Do", "#94a3b8"),
                    SelectOption("in_progress", "In Progress", "#60a5fa"),
                    SelectOption("review", "Review", "#fbbf24"),
                    SelectOption("done", "Done", "#34d399"),
                ],
                default_value="todo",
                order=3,
            ),
            FieldDefinition(
                id="fld_priority",
                name="priority",
                label="Priority",
                type=FieldType.SELECT,
                options=[
                    SelectOption("low", "Low", "#94a3b8"),
                    SelectOption("medium", "Medium", "#fbbf24"),
                    SelectOption("high", "High", "#f97316"),
                    SelectOption("urgent", "Urgent", "#ef4444"),
                ],
                default_value="medium",
                order=4,
            ),
            FieldDefinition(
                id="fld_due_date",
                name="due_date",
                label="Due Date",
                type=FieldType.DATE,
                order=5,
            ),
            FieldDefinition(
                id="fld_assignee",
                name="assignee_id",
                label="Assignee",
                type=FieldType.RELATION,
                order=6,
            ),
        ],
        primary_field_id="fld_title",
        default_view="kanban",
    )


def create_appointment_entity(org_id: str) -> EntityDefinition:
    """Create Appointment entity template."""
    return EntityDefinition(
        id=f"ent_{uuid4().hex[:12]}",
        organization_id=org_id,
        name="appointments",
        label="Appointment",
        label_plural="Appointments",
        description="Scheduled appointments and meetings",
        icon="📅",
        color="#ec4899",
        fields=[
            FieldDefinition(
                id="fld_title",
                name="title",
                label="Title",
                type=FieldType.TEXT,
                is_required=True,
                order=1,
            ),
            FieldDefinition(
                id="fld_start",
                name="start_time",
                label="Start Time",
                type=FieldType.DATETIME,
                is_required=True,
                order=2,
            ),
            FieldDefinition(
                id="fld_end",
                name="end_time",
                label="End Time",
                type=FieldType.DATETIME,
                order=3,
            ),
            FieldDefinition(
                id="fld_contact",
                name="contact_id",
                label="Contact",
                type=FieldType.RELATION,
                order=4,
            ),
            FieldDefinition(
                id="fld_location",
                name="location",
                label="Location",
                type=FieldType.TEXT,
                order=5,
            ),
            FieldDefinition(
                id="fld_status",
                name="status",
                label="Status",
                type=FieldType.SELECT,
                options=[
                    SelectOption("scheduled", "Scheduled", "#60a5fa"),
                    SelectOption("confirmed", "Confirmed", "#34d399"),
                    SelectOption("completed", "Completed", "#6b7280"),
                    SelectOption("cancelled", "Cancelled", "#f87171"),
                    SelectOption("no_show", "No Show", "#fbbf24"),
                ],
                default_value="scheduled",
                order=6,
            ),
            FieldDefinition(
                id="fld_notes",
                name="notes",
                label="Notes",
                type=FieldType.LONG_TEXT,
                order=7,
            ),
            FieldDefinition(
                id="fld_reminder",
                name="reminder_sent",
                label="Reminder Sent",
                type=FieldType.BOOLEAN,
                default_value=False,
                order=8,
            ),
        ],
        primary_field_id="fld_title",
        default_view="calendar",
    )


# ============================================
# Business Core Service
# ============================================


class BusinessCoreService(PersistentService):
    """Business Core - Customizable Data Model."""

    def __init__(self, entity_repo=None, record_repo=None, use_persistence=None):
        """
        Initialize BusinessCoreService.

        Args:
            entity_repo: Optional entity repository (uses factory if None)
            record_repo: Optional record repository (uses factory if None)
            use_persistence: If True, auto-create repositories from environment.
                           If None, check VOS3_STORAGE_BACKEND env var.
        """
        self._using_persistence = self._bootstrap_persistence(use_persistence)

        if self._using_persistence:
            self._entity_repo = entity_repo or get_entity_repository()
            self._record_repo = record_repo or get_record_repository()
        else:
            self._entity_repo = None
            self._record_repo = None

        # In-memory storage (always available as cache/fallback)
        self._entities: dict[str, EntityDefinition] = {}
        self._records: dict[str, Record] = {}
        self._history: list[RecordHistory] = []
        self._last_task_context: Optional[TaskContext] = None

        # Only init sample data for in-memory mode
        if not self._using_persistence:
            self._init_sample_data()

    def get_task_context(self) -> Optional[TaskContext]:
        """Get the last calculated task context for agent state injection."""
        return self._last_task_context

    def _calculate_and_store_context(
        self,
        description: str,
        task_type: TaskType,
        entity: Optional[EntityDefinition] = None,
        record_count: int = 0,
    ) -> TaskContext:
        """Calculate complexity and store context for agent injection."""
        self._last_task_context = calculate_task_complexity(
            description=description,
            task_type=task_type,
            entity=entity,
            record_count=record_count,
        )
        return self._last_task_context

    def _init_sample_data(self):
        """Initialize with sample entities."""
        org_id = "org_demo"

        # Create default entities
        contact_entity = create_contact_entity(org_id)
        deal_entity = create_deal_entity(org_id)
        task_entity = create_task_entity(org_id)
        appointment_entity = create_appointment_entity(org_id)

        for entity in [contact_entity, deal_entity, task_entity, appointment_entity]:
            self._entities[entity.id] = entity

        # Sample records
        sample_contacts = [
            {
                "name": "Sarah Johnson",
                "email": "sarah@acme.com",
                "company": "Acme Corp",
                "status": "customer",
                "phone": "+1-555-0101",
            },
            {
                "name": "Mike Chen",
                "email": "mike@techstart.io",
                "company": "TechStart",
                "status": "prospect",
                "phone": "+1-555-0102",
            },
            {
                "name": "Emily Davis",
                "email": "emily@designco.com",
                "company": "DesignCo",
                "status": "lead",
                "phone": "+1-555-0103",
            },
        ]
        for data in sample_contacts:
            rec = Record(
                entity_id=contact_entity.id,
                organization_id=org_id,
                data=data,
                created_by="dev_seed_user",
                updated_by="dev_seed_user",
            )
            self._records[rec.id] = rec

    # ==========================================
    # Entity Management
    # ==========================================

    def create_entity(
        self, org_id: str, name: str, label: str, fields: list[dict] = None, **kwargs
    ) -> EntityDefinition:
        """Create a new entity definition."""
        entity = EntityDefinition(
            organization_id=org_id,
            name=name,
            label=label,
            label_plural=kwargs.get("label_plural", f"{label}s"),
            description=kwargs.get("description", ""),
            icon=kwargs.get("icon", "📄"),
            color=kwargs.get("color", "#6366f1"),
        )

        # Add fields
        if fields:
            for i, f in enumerate(fields):
                field_def = FieldDefinition(
                    name=f["name"],
                    label=f.get("label", f["name"]),
                    type=FieldType(f["type"]),
                    order=i,
                    is_required=f.get("required", False),
                    is_unique=f.get("unique", False),
                    default_value=f.get("default"),
                )
                if f.get("options"):
                    field_def.options = [SelectOption(**o) for o in f["options"]]
                entity.fields.append(field_def)

        # Add system fields
        entity.fields.extend(
            [
                FieldDefinition(
                    name="created_at",
                    label="Created",
                    type=FieldType.CREATED_AT,
                    is_hidden=True,
                    order=100,
                ),
                FieldDefinition(
                    name="updated_at",
                    label="Updated",
                    type=FieldType.UPDATED_AT,
                    is_hidden=True,
                    order=101,
                ),
            ]
        )

        # Set primary field
        if entity.fields:
            entity.primary_field_id = entity.fields[0].id

        # Cache locally
        self._entities[entity.id] = entity

        # Persist to repository
        if self._using_persistence and self._entity_repo:
            try:
                self._entity_repo.create(
                    {
                        "id": entity.id,
                        "organization_id": org_id,
                        "name": name,
                        "slug": name.lower().replace(" ", "_"),
                        "description": kwargs.get("description", ""),
                        "fields": [f.to_dict() for f in entity.fields],
                        "metadata": entity.to_dict(),
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist entity %s: %s", entity.id, e)

        return entity

    def get_entity(self, entity_id: str) -> Optional[EntityDefinition]:
        return self._entities.get(entity_id)

    def get_entity_by_name(self, org_id: str, name: str) -> Optional[EntityDefinition]:
        for entity in self._entities.values():
            if entity.organization_id == org_id and entity.name == name:
                return entity
        return None

    def list_entities(self, org_id: str) -> list[EntityDefinition]:
        return [e for e in self._entities.values() if e.organization_id == org_id]

    def update_entity(
        self, entity_id: str, updates: dict
    ) -> Optional[EntityDefinition]:
        entity = self._entities.get(entity_id)
        if not entity or entity.is_system:
            return None
        for key, value in updates.items():
            if hasattr(entity, key) and key not in (
                "id",
                "organization_id",
                "is_system",
            ):
                setattr(entity, key, value)
        entity.updated_at = datetime.now(timezone.utc)
        return entity

    def delete_entity(self, entity_id: str) -> bool:
        entity = self._entities.get(entity_id)
        if entity and not entity.is_system:
            del self._entities[entity_id]
            # Delete all records
            self._records = {
                k: v for k, v in self._records.items() if v.entity_id != entity_id
            }
            # Persist deletion
            if self._using_persistence and self._entity_repo:
                try:
                    self._entity_repo.delete(entity_id)
                except Exception as e:
                    logger.warning(
                        "Failed to persist entity deletion %s: %s", entity_id, e
                    )
            return True
        return False

    # ==========================================
    # Field Management
    # ==========================================

    def add_field(self, entity_id: str, field_def: dict) -> Optional[FieldDefinition]:
        """Add field to entity."""
        entity = self._entities.get(entity_id)
        if not entity:
            return None

        field = FieldDefinition(
            name=field_def["name"],
            label=field_def.get("label", field_def["name"]),
            type=FieldType(field_def["type"]),
            is_required=field_def.get("required", False),
            is_unique=field_def.get("unique", False),
            default_value=field_def.get("default"),
            order=len(entity.fields),
        )
        if field_def.get("options"):
            field.options = [SelectOption(**o) for o in field_def["options"]]

        entity.fields.append(field)
        entity.updated_at = datetime.now(timezone.utc)
        return field

    def update_field(
        self, entity_id: str, field_id: str, updates: dict
    ) -> Optional[FieldDefinition]:
        entity = self._entities.get(entity_id)
        if not entity:
            return None
        field = entity.get_field(field_id)
        if not field:
            return None
        for key, value in updates.items():
            if hasattr(field, key):
                setattr(field, key, value)
        entity.updated_at = datetime.now(timezone.utc)
        return field

    def delete_field(self, entity_id: str, field_id: str) -> bool:
        entity = self._entities.get(entity_id)
        if not entity:
            return False
        entity.fields = [f for f in entity.fields if f.id != field_id]
        entity.updated_at = datetime.now(timezone.utc)
        return True

    # ==========================================
    # Record Management
    # ==========================================

    def create_record(
        self, entity_id: str, org_id: str, data: dict, user_id: str
    ) -> Record:
        """Create a new record."""
        entity = self._entities.get(entity_id)

        # Calculate task complexity for SmartRouter
        self._calculate_and_store_context(
            description=f"Create record in {entity.name if entity else 'unknown'}",
            task_type=TaskType.SIMPLE,
            entity=entity,
            record_count=1,
        )

        # Validate and apply defaults
        validated_data = {}
        if entity:
            for field in entity.fields:
                value = data.get(field.name)
                if value is None and field.default_value is not None:
                    value = field.default_value
                if value is not None:
                    validated_data[field.name] = self._validate_field_value(
                        field, value
                    )
                elif field.is_required:
                    raise ValueError(f"Field {field.name} is required")
        else:
            validated_data = data

        record = Record(
            entity_id=entity_id,
            organization_id=org_id,
            data=validated_data,
            created_by=user_id,
            updated_by=user_id,
        )
        self._records[record.id] = record

        # Persist to repository
        if self._using_persistence and self._record_repo:
            try:
                self._record_repo.create(
                    {
                        "id": record.id,
                        "entity_id": entity_id,
                        "organization_id": org_id,
                        "data": validated_data,
                        "created_by": user_id,
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist record %s: %s", record.id, e)

        # Log history
        self._log_history(record.id, entity_id, user_id, "create", {}, validated_data)

        return record

    def get_record(self, record_id: str) -> Optional[Record]:
        record = self._records.get(record_id)
        return record if record and not record.is_deleted else None

    def update_record(
        self, record_id: str, data: dict, user_id: str
    ) -> Optional[Record]:
        record = self._records.get(record_id)
        if not record or record.is_deleted:
            return None

        # Calculate task complexity for SmartRouter
        entity = self._entities.get(record.entity_id)
        self._calculate_and_store_context(
            description=f"Update record in {entity.name if entity else 'unknown'}",
            task_type=TaskType.SIMPLE,
            entity=entity,
            record_count=1,
        )

        old_data = record.data.copy()
        record.data.update(data)
        record.updated_by = user_id
        record.updated_at = datetime.now(timezone.utc)

        # Persist update
        if self._using_persistence and self._record_repo:
            try:
                self._record_repo.update(record_id, {"data": record.data})
            except Exception as e:
                logger.warning("Failed to persist record update %s: %s", record_id, e)

        # Log changes
        changes = {}
        for key, new_val in data.items():
            old_val = old_data.get(key)
            if old_val != new_val:
                changes[key] = {"old": old_val, "new": new_val}
        if changes:
            self._log_history(
                record_id, record.entity_id, user_id, "update", old_data, record.data
            )

        return record

    def delete_record(
        self, record_id: str, user_id: str, hard_delete: bool = False
    ) -> bool:
        record = self._records.get(record_id)
        if not record:
            return False

        # Calculate task complexity for SmartRouter
        entity = self._entities.get(record.entity_id)
        self._calculate_and_store_context(
            description=f"Delete record from {entity.name if entity else 'unknown'}",
            task_type=TaskType.SIMPLE,
            entity=entity,
            record_count=1,
        )

        if hard_delete:
            del self._records[record_id]
        else:
            record.is_deleted = True
            record.deleted_at = datetime.now(timezone.utc)
            record.deleted_by = user_id

        # Persist deletion
        if self._using_persistence and self._record_repo:
            try:
                self._record_repo.delete(record_id)
            except Exception as e:
                logger.warning("Failed to persist record deletion %s: %s", record_id, e)

        self._log_history(
            record_id, record.entity_id, user_id, "delete", record.data, {}
        )
        return True

    def list_records(
        self,
        entity_id: str,
        org_id: str,
        filters: dict = None,
        sort_by: str = None,
        sort_order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Record]:
        """List records with filtering and pagination."""
        records = [
            r
            for r in self._records.values()
            if r.entity_id == entity_id
            and r.organization_id == org_id
            and not r.is_deleted
        ]

        # Calculate task complexity based on query complexity
        entity = self._entities.get(entity_id)
        task_type = TaskType.GENERAL if filters else TaskType.SIMPLE
        self._calculate_and_store_context(
            description=f"List records from {entity.name if entity else 'unknown'} with {'filters' if filters else 'no filters'}",
            task_type=task_type,
            entity=entity,
            record_count=len(records),
        )

        # Apply filters
        if filters:
            for key, value in filters.items():
                if isinstance(value, dict):
                    # Complex filter: {field: {op: value}}
                    op = list(value.keys())[0]
                    val = value[op]
                    if op == "eq":
                        records = [r for r in records if r.data.get(key) == val]
                    elif op == "ne":
                        records = [r for r in records if r.data.get(key) != val]
                    elif op == "gt":
                        records = [r for r in records if (r.data.get(key) or 0) > val]
                    elif op == "lt":
                        records = [r for r in records if (r.data.get(key) or 0) < val]
                    elif op == "contains":
                        records = [
                            r
                            for r in records
                            if val.lower() in str(r.data.get(key, "")).lower()
                        ]
                    elif op == "in":
                        records = [r for r in records if r.data.get(key) in val]
                else:
                    records = [r for r in records if r.data.get(key) == value]

        # Sort
        if sort_by:
            reverse = sort_order == "desc"
            if sort_by == "created_at":
                records.sort(key=lambda r: r.created_at, reverse=reverse)
            elif sort_by == "updated_at":
                records.sort(key=lambda r: r.updated_at, reverse=reverse)
            else:
                records.sort(key=lambda r: r.data.get(sort_by, ""), reverse=reverse)
        else:
            records.sort(key=lambda r: r.created_at, reverse=True)

        return records[offset : offset + limit]

    def count_records(self, entity_id: str, org_id: str, filters: dict = None) -> int:
        records = self.list_records(entity_id, org_id, filters, limit=999999)
        return len(records)

    def search_records(
        self, entity_id: str, org_id: str, query: str, limit: int = 20
    ) -> list[Record]:
        """Full-text search across searchable fields."""
        entity = self._entities.get(entity_id)
        if not entity:
            return []

        # Calculate task complexity - search is more complex
        self._calculate_and_store_context(
            description=f"Search records in {entity.name} for: {query[:50]}",
            task_type=TaskType.GENERAL,
            entity=entity,
            record_count=limit,
        )

        searchable_fields = [f.name for f in entity.fields if f.is_searchable]
        query_lower = query.lower()

        results = []
        for record in self._records.values():
            if (
                record.entity_id != entity_id
                or record.organization_id != org_id
                or record.is_deleted
            ):
                continue
            for field_name in searchable_fields:
                value = record.data.get(field_name, "")
                if query_lower in str(value).lower():
                    results.append(record)
                    break

        return results[:limit]

    # ==========================================
    # History
    # ==========================================

    def _log_history(
        self,
        record_id: str,
        entity_id: str,
        user_id: str,
        action: str,
        old_data: dict,
        new_data: dict,
    ):
        changes = {}
        all_keys = set(old_data.keys()) | set(new_data.keys())
        for key in all_keys:
            old_val = old_data.get(key)
            new_val = new_data.get(key)
            if old_val != new_val:
                changes[key] = {"old": old_val, "new": new_val}

        history = RecordHistory(
            record_id=record_id,
            entity_id=entity_id,
            user_id=user_id,
            action=action,
            changes=changes,
        )
        self._history.append(history)

    def get_record_history(self, record_id: str) -> list[RecordHistory]:
        return [h for h in self._history if h.record_id == record_id]

    def _validate_field_value(self, field: FieldDefinition, value: Any) -> Any:
        """Validate and coerce field value."""
        if value is None:
            return None

        if field.type == FieldType.NUMBER:
            return int(value) if value else 0
        elif field.type in (FieldType.DECIMAL, FieldType.CURRENCY):
            return float(value) if value else 0.0
        elif field.type == FieldType.BOOLEAN:
            return bool(value)
        elif field.type == FieldType.DATE:
            if isinstance(value, str):
                return value  # Keep as ISO string
            return value.isoformat() if isinstance(value, date) else str(value)
        elif field.type == FieldType.SELECT:
            valid_values = [o.value for o in field.options]
            if value not in valid_values:
                return field.default_value
        elif field.type == FieldType.MULTI_SELECT:
            if isinstance(value, list):
                valid_values = [o.value for o in field.options]
                return [v for v in value if v in valid_values]

        return value


# Singleton
_business_core_service: Optional[BusinessCoreService] = None


def get_business_core_service() -> BusinessCoreService:
    global _business_core_service
    if _business_core_service is None:
        _business_core_service = BusinessCoreService()
    return _business_core_service


__all__ = [
    "BusinessCoreService",
    "EntityDefinition",
    "FieldDefinition",
    "Record",
    "RecordHistory",
    "SelectOption",
    "FieldValidation",
    "FieldType",
    "RelationType",
    "ValidationRule",
    "get_business_core_service",
    "create_contact_entity",
    "create_deal_entity",
    "create_task_entity",
    "create_appointment_entity",
    # Task complexity for SmartRouter integration
    "TaskType",
    "TaskContext",
    "calculate_task_complexity",
]
