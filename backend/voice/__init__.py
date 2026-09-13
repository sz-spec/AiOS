"""
Voice Interface Module

Voice-first AI assistant capabilities:
- Speech-to-text processing
- Text-to-speech generation
- Voice command recognition
- Multilingual support
"""

from voice.voice_service import (
    VoiceService,
    VoiceCommand,
    VoiceSettings,
    VoiceLanguage,
    VoiceGender,
    VoiceStatus,
    CommandCategory,
    SUPPORTED_LANGUAGES,
    get_voice_service,
)

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
