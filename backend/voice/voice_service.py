"""
Voice Interface Service

Voice-first AI assistant capabilities:
- Speech-to-text processing
- Text-to-speech generation
- Voice command recognition
- Wake word detection
- Multilingual support

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from uuid import uuid4
import re
from collections import defaultdict

# ============================================
# Enums
# ============================================


class VoiceLanguage(str, Enum):
    """Supported voice languages."""

    ENGLISH = "en"
    HEBREW = "he"
    SPANISH = "es"
    ARABIC = "ar"
    FRENCH = "fr"
    GERMAN = "de"
    ITALIAN = "it"
    PORTUGUESE = "pt"
    RUSSIAN = "ru"
    CHINESE = "zh"
    JAPANESE = "ja"
    KOREAN = "ko"


class VoiceGender(str, Enum):
    """Voice gender options."""

    MALE = "male"
    FEMALE = "female"
    NEUTRAL = "neutral"


class CommandCategory(str, Enum):
    """Voice command categories."""

    NAVIGATION = "navigation"
    ACTION = "action"
    QUERY = "query"
    CONTROL = "control"
    AGENT = "agent"


class VoiceStatus(str, Enum):
    """Voice session status."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


# ============================================
# Data Models
# ============================================


@dataclass
class VoiceCommand:
    """A recognized voice command."""

    id: str = field(default_factory=lambda: f"cmd_{uuid4().hex[:8]}")

    # Raw input
    transcript: str = ""
    confidence: float = 0.0
    language: VoiceLanguage = VoiceLanguage.ENGLISH

    # Parsed command
    category: CommandCategory = CommandCategory.ACTION
    intent: str = ""
    entities: dict = field(default_factory=dict)

    # Execution
    action: Optional[str] = None
    parameters: dict = field(default_factory=dict)

    # Response
    response_text: str = ""
    response_audio_url: Optional[str] = None

    # Metadata
    user_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_time_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "transcript": self.transcript,
            "confidence": self.confidence,
            "language": self.language.value,
            "category": self.category.value,
            "intent": self.intent,
            "entities": self.entities,
            "action": self.action,
            "parameters": self.parameters,
            "response_text": self.response_text,
            "response_audio_url": self.response_audio_url,
            "timestamp": self.timestamp.isoformat(),
            "processing_time_ms": self.processing_time_ms,
        }


@dataclass
class VoiceSettings:
    """User voice settings."""

    user_id: str = ""

    # Input settings
    input_language: VoiceLanguage = VoiceLanguage.ENGLISH
    wake_word_enabled: bool = True
    wake_word: str = "Hey V"
    continuous_listening: bool = False

    # Output settings
    output_language: VoiceLanguage = VoiceLanguage.ENGLISH
    voice_gender: VoiceGender = VoiceGender.FEMALE
    speech_rate: float = 1.0  # 0.5 to 2.0
    pitch: float = 1.0  # 0.5 to 2.0
    volume: float = 1.0  # 0.0 to 1.0

    # Features
    read_notifications: bool = True
    morning_briefing: bool = True
    voice_confirmations: bool = True

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "input_language": self.input_language.value,
            "wake_word_enabled": self.wake_word_enabled,
            "wake_word": self.wake_word,
            "continuous_listening": self.continuous_listening,
            "output_language": self.output_language.value,
            "voice_gender": self.voice_gender.value,
            "speech_rate": self.speech_rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "read_notifications": self.read_notifications,
            "morning_briefing": self.morning_briefing,
            "voice_confirmations": self.voice_confirmations,
        }


@dataclass
class CommandPattern:
    """Pattern for matching voice commands."""

    pattern: str  # Regex pattern
    category: CommandCategory
    intent: str
    action: str
    extract_entities: list[str] = field(default_factory=list)
    response_template: str = ""

    def match(self, text: str) -> Optional[dict]:
        """Try to match text against pattern."""
        match = re.search(self.pattern, text, re.IGNORECASE)
        if match:
            entities = {}
            for entity in self.extract_entities:
                try:
                    entities[entity] = match.group(entity)
                except:
                    pass
            return {
                "intent": self.intent,
                "action": self.action,
                "entities": entities,
                "category": self.category,
            }
        return None


# ============================================
# Command Patterns Library
# ============================================

COMMAND_PATTERNS: list[CommandPattern] = [
    # Navigation
    CommandPattern(
        pattern=r"(go to|open|show|navigate to)\s+(?P<page>\w+)",
        category=CommandCategory.NAVIGATION,
        intent="navigate",
        action="navigate_to_page",
        extract_entities=["page"],
        response_template="Opening {page}",
    ),
    CommandPattern(
        pattern=r"(go back|back|previous)",
        category=CommandCategory.NAVIGATION,
        intent="go_back",
        action="navigate_back",
        response_template="Going back",
    ),
    CommandPattern(
        pattern=r"(go home|home page|main page)",
        category=CommandCategory.NAVIGATION,
        intent="go_home",
        action="navigate_home",
        response_template="Going to home page",
    ),
    # Calendar & Scheduling
    CommandPattern(
        pattern=r"schedule\s+(a\s+)?meeting\s+with\s+(?P<person>\w+)",
        category=CommandCategory.ACTION,
        intent="schedule_meeting",
        action="create_calendar_event",
        extract_entities=["person"],
        response_template="Scheduling a meeting with {person}",
    ),
    CommandPattern(
        pattern=r"(what('s| is) on my|show my)\s+(calendar|schedule)",
        category=CommandCategory.QUERY,
        intent="show_calendar",
        action="get_calendar_events",
        response_template="Here's your schedule",
    ),
    CommandPattern(
        pattern=r"reschedule\s+(?P<event>.+?)\s+to\s+(?P<time>.+)",
        category=CommandCategory.ACTION,
        intent="reschedule",
        action="reschedule_event",
        extract_entities=["event", "time"],
        response_template="Rescheduling {event} to {time}",
    ),
    # Tasks & Projects
    CommandPattern(
        pattern=r"create\s+(a\s+)?(new\s+)?project\s+(called\s+)?(?P<name>.+)",
        category=CommandCategory.ACTION,
        intent="create_project",
        action="create_project",
        extract_entities=["name"],
        response_template="Creating project {name}",
    ),
    CommandPattern(
        pattern=r"add\s+(a\s+)?task\s+(?P<task>.+)",
        category=CommandCategory.ACTION,
        intent="add_task",
        action="create_task",
        extract_entities=["task"],
        response_template="Adding task: {task}",
    ),
    CommandPattern(
        pattern=r"(what are my|show|list)\s+(open\s+)?tasks",
        category=CommandCategory.QUERY,
        intent="list_tasks",
        action="get_tasks",
        response_template="Here are your tasks",
    ),
    # Communication
    CommandPattern(
        pattern=r"send\s+(an?\s+)?email\s+to\s+(?P<recipient>\w+)",
        category=CommandCategory.ACTION,
        intent="send_email",
        action="compose_email",
        extract_entities=["recipient"],
        response_template="Composing email to {recipient}",
    ),
    CommandPattern(
        pattern=r"send\s+(a\s+)?message\s+to\s+(?P<recipient>\w+)",
        category=CommandCategory.ACTION,
        intent="send_message",
        action="compose_message",
        extract_entities=["recipient"],
        response_template="Composing message to {recipient}",
    ),
    CommandPattern(
        pattern=r"(check|read|show)\s+(my\s+)?messages",
        category=CommandCategory.QUERY,
        intent="check_messages",
        action="get_messages",
        response_template="Here are your messages",
    ),
    # Business Queries
    CommandPattern(
        pattern=r"(what('s| is)|show)\s+(my\s+)?revenue\s+(this\s+)?(?P<period>week|month|year)?",
        category=CommandCategory.QUERY,
        intent="revenue_query",
        action="get_revenue",
        extract_entities=["period"],
        response_template="Your revenue this {period}",
    ),
    CommandPattern(
        pattern=r"how many\s+(?P<metric>customers|users|orders|sales)",
        category=CommandCategory.QUERY,
        intent="metric_query",
        action="get_metric",
        extract_entities=["metric"],
        response_template="Fetching {metric} data",
    ),
    # Approvals
    CommandPattern(
        pattern=r"(yes|approve|confirm|ok|do it)",
        category=CommandCategory.CONTROL,
        intent="approve",
        action="confirm_action",
        response_template="Confirmed",
    ),
    CommandPattern(
        pattern=r"(no|cancel|stop|never mind)",
        category=CommandCategory.CONTROL,
        intent="cancel",
        action="cancel_action",
        response_template="Cancelled",
    ),
    # Agent Commands
    CommandPattern(
        pattern=r"(talk to|call|ask)\s+(?P<agent>\w+)\s+agent",
        category=CommandCategory.AGENT,
        intent="invoke_agent",
        action="start_agent_conversation",
        extract_entities=["agent"],
        response_template="Connecting you with {agent} agent",
    ),
    CommandPattern(
        pattern=r"create\s+(an?\s+)?agent\s+(for\s+)?(?P<purpose>.+)",
        category=CommandCategory.AGENT,
        intent="create_agent",
        action="start_agent_builder",
        extract_entities=["purpose"],
        response_template="Let's create an agent for {purpose}",
    ),
    # System Control
    CommandPattern(
        pattern=r"(stop listening|pause|mute)",
        category=CommandCategory.CONTROL,
        intent="stop_listening",
        action="pause_voice",
        response_template="Voice paused",
    ),
    CommandPattern(
        pattern=r"(help|what can you do)",
        category=CommandCategory.CONTROL,
        intent="help",
        action="show_help",
        response_template="Here's what I can do",
    ),
]


# ============================================
# Morning Briefing Templates
# ============================================

BRIEFING_TEMPLATE = """
Good morning! Here's your briefing for {date}:

📅 Calendar: You have {meeting_count} meetings today.
{meetings_summary}

✅ Tasks: {task_count} tasks due today.
{tasks_summary}

📬 Messages: {message_count} unread messages.
{messages_summary}

💰 Business: {revenue_summary}

Have a productive day!
"""


# ============================================
# Voice Service
# ============================================


class VoiceService:
    """
    Voice Interface Service

    Handles voice input/output and command processing.
    Uses Web Speech API on frontend, processes commands on backend.
    """

    def __init__(self):
        self._settings: dict[str, VoiceSettings] = {}
        self._command_history: dict[str, list[VoiceCommand]] = defaultdict(list)
        self._pending_actions: dict[str, dict] = {}
        self._command_handlers: dict[str, Callable] = {}

    # ==========================================
    # Settings
    # ==========================================

    def get_settings(self, user_id: str) -> VoiceSettings:
        """Get user's voice settings."""
        if user_id not in self._settings:
            self._settings[user_id] = VoiceSettings(user_id=user_id)
        return self._settings[user_id]

    def update_settings(self, user_id: str, updates: dict) -> VoiceSettings:
        """Update user's voice settings."""
        settings = self.get_settings(user_id)

        for key, value in updates.items():
            if hasattr(settings, key):
                if key == "input_language" or key == "output_language":
                    value = VoiceLanguage(value)
                elif key == "voice_gender":
                    value = VoiceGender(value)
                setattr(settings, key, value)

        return settings

    # ==========================================
    # Command Processing
    # ==========================================

    async def process_voice_input(
        self,
        user_id: str,
        transcript: str,
        confidence: float = 1.0,
        language: str = "en",
    ) -> VoiceCommand:
        """
        Process voice input and execute command.

        1. Parse transcript to identify intent
        2. Extract entities
        3. Execute action
        4. Generate response
        """
        start_time = datetime.now(timezone.utc)

        command = VoiceCommand(
            user_id=user_id,
            transcript=transcript,
            confidence=confidence,
            language=(
                VoiceLanguage(language)
                if language in [l.value for l in VoiceLanguage]
                else VoiceLanguage.ENGLISH
            ),
        )

        # Check for wake word if enabled
        settings = self.get_settings(user_id)
        if settings.wake_word_enabled:
            transcript = self._remove_wake_word(transcript, settings.wake_word)

        # Parse command
        parsed = self._parse_command(transcript)
        if parsed:
            command.category = parsed["category"]
            command.intent = parsed["intent"]
            command.entities = parsed["entities"]
            command.action = parsed["action"]

            # Generate response
            command.response_text = self._generate_response(parsed)

            # Execute action
            await self._execute_action(command)
        else:
            # Fallback to AI chat
            command.category = CommandCategory.QUERY
            command.intent = "chat"
            command.action = "ai_chat"
            command.response_text = await self._handle_chat(transcript, user_id)

        # Calculate processing time
        end_time = datetime.now(timezone.utc)
        command.processing_time_ms = int((end_time - start_time).total_seconds() * 1000)

        # Store in history
        self._command_history[user_id].append(command)

        return command

    def _remove_wake_word(self, transcript: str, wake_word: str) -> str:
        """Remove wake word from transcript."""
        pattern = rf"^{re.escape(wake_word)}\s*,?\s*"
        return re.sub(pattern, "", transcript, flags=re.IGNORECASE).strip()

    def _parse_command(self, transcript: str) -> Optional[dict]:
        """Parse transcript to identify command."""
        transcript = transcript.strip().lower()

        for pattern in COMMAND_PATTERNS:
            result = pattern.match(transcript)
            if result:
                result["response_template"] = pattern.response_template
                return result

        return None

    def _generate_response(self, parsed: dict) -> str:
        """Generate response text from parsed command."""
        template = parsed.get("response_template", "")
        entities = parsed.get("entities", {})

        try:
            return template.format(**entities)
        except:
            return template

    async def _execute_action(self, command: VoiceCommand) -> None:
        """Execute the parsed action."""
        action = command.action

        # Check for registered handler
        if action in self._command_handlers:
            handler = self._command_handlers[action]
            await handler(command)

        # Built-in actions
        elif action == "confirm_action":
            await self._handle_confirmation(command)
        elif action == "cancel_action":
            await self._handle_cancellation(command)

    async def _handle_chat(self, message: str, user_id: str) -> str:
        """Handle general chat via AI."""
        # In production, this would call the AI service
        return f"I understand you said: '{message}'. How can I help you with that?"

    async def _handle_confirmation(self, command: VoiceCommand) -> None:
        """Handle confirmation of pending action."""
        user_id = command.user_id
        if user_id in self._pending_actions:
            pending = self._pending_actions.pop(user_id)
            command.response_text = (
                f"Confirmed: {pending.get('description', 'Action executed')}"
            )

    async def _handle_cancellation(self, command: VoiceCommand) -> None:
        """Handle cancellation of pending action."""
        user_id = command.user_id
        if user_id in self._pending_actions:
            self._pending_actions.pop(user_id)
            command.response_text = "Action cancelled"

    # ==========================================
    # Command Handlers Registration
    # ==========================================

    def register_handler(self, action: str, handler: Callable) -> None:
        """Register a handler for an action."""
        self._command_handlers[action] = handler

    def unregister_handler(self, action: str) -> None:
        """Unregister a handler."""
        if action in self._command_handlers:
            del self._command_handlers[action]

    # ==========================================
    # Morning Briefing
    # ==========================================

    async def generate_morning_briefing(self, user_id: str) -> str:
        """Generate personalized morning briefing."""
        # In production, these would fetch real data
        date = datetime.now().strftime("%A, %B %d")

        # Mock data
        meetings = [
            {"time": "9:00 AM", "title": "Team Standup"},
            {"time": "2:00 PM", "title": "Client Call"},
        ]

        tasks = [
            {"title": "Review PR #42"},
            {"title": "Update documentation"},
        ]

        meetings_summary = (
            "\n".join([f"  • {m['time']}: {m['title']}" for m in meetings])
            or "  No meetings today."
        )

        tasks_summary = (
            "\n".join([f"  • {t['title']}" for t in tasks]) or "  No tasks due today."
        )

        briefing = BRIEFING_TEMPLATE.format(
            date=date,
            meeting_count=len(meetings),
            meetings_summary=meetings_summary,
            task_count=len(tasks),
            tasks_summary=tasks_summary,
            message_count=3,
            messages_summary="  3 from team members",
            revenue_summary="Revenue is up 12% this week",
        )

        return briefing.strip()

    # ==========================================
    # Text-to-Speech Config
    # ==========================================

    def get_tts_config(self, user_id: str) -> dict:
        """Get TTS configuration for user."""
        settings = self.get_settings(user_id)

        return {
            "lang": settings.output_language.value,
            "rate": settings.speech_rate,
            "pitch": settings.pitch,
            "volume": settings.volume,
            "voice": self._get_voice_name(
                settings.output_language, settings.voice_gender
            ),
        }

    def _get_voice_name(self, language: VoiceLanguage, gender: VoiceGender) -> str:
        """Get voice name for language and gender."""
        # These are common voice names for Web Speech API
        voices = {
            ("en", "female"): "Google US English Female",
            ("en", "male"): "Google US English Male",
            ("he", "female"): "Google עברית",
            ("es", "female"): "Google español",
            ("es", "male"): "Google español",
            ("fr", "female"): "Google français",
            ("de", "female"): "Google Deutsch",
        }

        key = (language.value, gender.value)
        return voices.get(key, "Google US English Female")

    # ==========================================
    # History & Analytics
    # ==========================================

    def get_command_history(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[VoiceCommand]:
        """Get user's command history."""
        history = self._command_history.get(user_id, [])
        return sorted(history, key=lambda c: c.timestamp, reverse=True)[:limit]

    def get_popular_commands(self, user_id: str) -> list[dict]:
        """Get user's most used commands."""
        history = self._command_history.get(user_id, [])

        intent_counts: dict[str, int] = defaultdict(int)
        for cmd in history:
            if cmd.intent:
                intent_counts[cmd.intent] += 1

        return sorted(
            [{"intent": k, "count": v} for k, v in intent_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

    # ==========================================
    # Available Commands
    # ==========================================

    def get_available_commands(self) -> list[dict]:
        """Get list of available voice commands."""
        commands = []

        for pattern in COMMAND_PATTERNS:
            commands.append(
                {
                    "category": pattern.category.value,
                    "intent": pattern.intent,
                    "description": pattern.response_template,
                    "example": self._get_example_for_pattern(pattern),
                }
            )

        return commands

    def _get_example_for_pattern(self, pattern: CommandPattern) -> str:
        """Generate example phrase for pattern."""
        examples = {
            "navigate": "Go to projects",
            "go_back": "Go back",
            "go_home": "Go home",
            "schedule_meeting": "Schedule a meeting with John",
            "show_calendar": "What's on my calendar",
            "create_project": "Create a new project called My App",
            "add_task": "Add a task review the docs",
            "list_tasks": "Show my tasks",
            "send_email": "Send an email to Sarah",
            "revenue_query": "What's my revenue this week",
            "approve": "Yes, do it",
            "cancel": "Cancel",
            "invoke_agent": "Talk to sales agent",
            "help": "What can you do",
        }
        return examples.get(pattern.intent, "")


# ============================================
# Supported Languages Info
# ============================================

SUPPORTED_LANGUAGES = [
    {"code": "en", "name": "English", "flag": "🇺🇸"},
    {"code": "he", "name": "Hebrew", "flag": "🇮🇱"},
    {"code": "es", "name": "Spanish", "flag": "🇪🇸"},
    {"code": "ar", "name": "Arabic", "flag": "🇸🇦"},
    {"code": "fr", "name": "French", "flag": "🇫🇷"},
    {"code": "de", "name": "German", "flag": "🇩🇪"},
    {"code": "it", "name": "Italian", "flag": "🇮🇹"},
    {"code": "pt", "name": "Portuguese", "flag": "🇧🇷"},
    {"code": "ru", "name": "Russian", "flag": "🇷🇺"},
    {"code": "zh", "name": "Chinese", "flag": "🇨🇳"},
    {"code": "ja", "name": "Japanese", "flag": "🇯🇵"},
    {"code": "ko", "name": "Korean", "flag": "🇰🇷"},
]


# ============================================
# Singleton
# ============================================

_voice_service: Optional[VoiceService] = None


def get_voice_service() -> VoiceService:
    """Get voice service singleton."""
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service


__all__ = [
    "VoiceService",
    "VoiceCommand",
    "VoiceSettings",
    "VoiceLanguage",
    "VoiceGender",
    "VoiceStatus",
    "CommandCategory",
    "SUPPORTED_LANGUAGES",
    "get_voice_service",
]
