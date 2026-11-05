"""Transcription package for Dispatch (Gemini-only)."""
from dispatch.transcription.audio import extract_audio
from dispatch.transcription.gemini_engine import (
    transcribe_media,
    PermanentAuthError,
    RecoverableTranscriptionError,
)
from dispatch.transcription.transcriber import transcribe_video

__all__ = [
    "extract_audio",
    "transcribe_media",
    "transcribe_video",
    "PermanentAuthError",
    "RecoverableTranscriptionError",
]
