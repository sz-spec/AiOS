"""
Voice API Routes

REST API for voice interface:
- Process voice commands
- Get/update voice settings
- Morning briefing
- Command history
"""

from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from voice.voice_service import (
    VoiceService,
    SUPPORTED_LANGUAGES,
    get_voice_service,
)

router = APIRouter(prefix="/voice", tags=["voice"])


# ============================================
# Request/Response Models
# ============================================


class ProcessVoiceRequest(BaseModel):
    """Process voice input request."""

    transcript: str
    confidence: float = 1.0
    language: str = "en"


class UpdateSettingsRequest(BaseModel):
    """Update voice settings request."""

    input_language: Optional[str] = None
    output_language: Optional[str] = None
    wake_word_enabled: Optional[bool] = None
    wake_word: Optional[str] = None
    continuous_listening: Optional[bool] = None
    voice_gender: Optional[str] = None
    speech_rate: Optional[float] = None
    pitch: Optional[float] = None
    volume: Optional[float] = None
    read_notifications: Optional[bool] = None
    morning_briefing: Optional[bool] = None
    voice_confirmations: Optional[bool] = None


class VoiceCommandResponse(BaseModel):
    """Voice command response."""

    id: str
    transcript: str
    confidence: float
    language: str
    category: str
    intent: str
    entities: dict
    action: Optional[str]
    response_text: str
    processing_time_ms: int


# ============================================
# Voice Processing
# ============================================


@router.post(
    "/process",
    response_model=VoiceCommandResponse,
    summary="Process voice input",
    description="Accept a speech-to-text transcript and execute the recognized voice command",
    responses={503: {"description": "Voice processing service unavailable"}},
)
async def process_voice_input(
    request: ProcessVoiceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """
    Process voice input and execute command.

    This is called after speech-to-text on the frontend.
    """
    command = await service.process_voice_input(
        user_id=user.id,
        transcript=request.transcript,
        confidence=request.confidence,
        language=request.language,
    )

    return VoiceCommandResponse(
        id=command.id,
        transcript=command.transcript,
        confidence=command.confidence,
        language=command.language.value,
        category=command.category.value,
        intent=command.intent,
        entities=command.entities,
        action=command.action,
        response_text=command.response_text,
        processing_time_ms=command.processing_time_ms,
    )


# ============================================
# Settings
# ============================================


@router.get(
    "/settings",
    summary="Get voice settings",
    description="Return the authenticated user's voice configuration including language, wake word, and TTS preferences",
)
async def get_settings(
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Get user's voice settings."""
    settings = service.get_settings(user.id)
    return settings.to_dict()


@router.patch(
    "/settings",
    summary="Update voice settings",
    description="Partially update voice configuration settings for the authenticated user",
)
async def update_settings(
    request: UpdateSettingsRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Update user's voice settings."""
    updates = {k: v for k, v in request.dict().items() if v is not None}
    settings = service.update_settings(user.id, updates)
    return settings.to_dict()


# ============================================
# Morning Briefing
# ============================================


@router.get(
    "/briefing",
    summary="Get morning briefing",
    description="Generate a personalized morning briefing with TTS configuration for the authenticated user",
    responses={503: {"description": "Briefing generation service unavailable"}},
)
async def get_morning_briefing(
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Generate morning briefing."""
    briefing = await service.generate_morning_briefing(user.id)

    return {
        "briefing": briefing,
        "tts_config": service.get_tts_config(user.id),
    }


# ============================================
# TTS Configuration
# ============================================


@router.get(
    "/tts-config",
    summary="Get TTS configuration",
    description="Return text-to-speech engine settings for the authenticated user",
)
async def get_tts_config(
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Get text-to-speech configuration."""
    return service.get_tts_config(user.id)


# ============================================
# Commands
# ============================================


@router.get(
    "/commands",
    summary="Get available voice commands",
    description="Return all recognized voice commands and their descriptions",
)
async def get_available_commands(
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Get list of available voice commands."""
    return {
        "commands": service.get_available_commands(),
    }


@router.get(
    "/history",
    summary="Get command history",
    description="Return the user's recent voice command history",
)
async def get_command_history(
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Get user's command history."""
    history = service.get_command_history(user.id, limit)
    return {
        "history": [cmd.to_dict() for cmd in history],
    }


@router.get(
    "/popular",
    summary="Get popular commands",
    description="Return the user's most frequently used voice commands",
)
async def get_popular_commands(
    user: AuthenticatedUser = Depends(get_current_user),
    service: VoiceService = Depends(get_voice_service),
):
    """Get user's most used commands."""
    return {
        "popular": service.get_popular_commands(user.id),
    }


# ============================================
# Languages
# ============================================


@router.get(
    "/languages",
    summary="Get supported languages",
    description="Return all languages supported by the voice recognition and TTS systems",
)
async def get_supported_languages(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get list of supported languages."""
    return {
        "languages": SUPPORTED_LANGUAGES,
    }


__all__ = ["router"]
