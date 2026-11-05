"""Canonical transcription interface for Dispatch (Gemini-only).

Primary: gemini-3.5-transcribe
Fallback: gemini-3.5-flash-lite
Zero local Whisper fallback. Zero YouTube captions fallback.
"""
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from dispatch import db
from dispatch.transcription.gemini_engine import (
    transcribe_media,
    PermanentAuthError,
    RecoverableTranscriptionError,
)

logger = logging.getLogger("dispatch.transcription")


def transcribe_video(
    video_path: Path,
    chunk_id: Optional[str] = None,
    session_id: Optional[str] = None,
    api_key: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    language: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Transcribe video audio using the canonical Gemini engine.
    
    Tries primary (gemini-3.5-transcribe), falling back to gemini-3.5-flash-lite.
    Persists transcript to SQLite database if chunk_id is provided.
    
    Args:
        video_path: Path to the media file (.mp4, .mov, etc.).
        chunk_id: Database chunk ID (if registering to DB).
        session_id: Database session ID.
        api_key: Optional Gemini API key override.
        initial_prompt: Optional custom prompt.
        language: Language hint code.
        
    Returns:
        Dict with transcript_id, full_text, segments (with word timings), source, model.
    """
    logger.info("Starting canonical Gemini transcription on %s", video_path.name)
    result = transcribe_media(
        media_path=video_path,
        api_key=api_key,
        prompt=initial_prompt,
        language=language,
    )

    transcript_id = None
    if chunk_id:
        transcript_id = db.save_transcript(
            chunk_id=chunk_id,
            session_id=session_id,
            full_text=result["full_text"],
            segments=result["segments"]
        )

    return {
        "transcript_id": transcript_id,
        "full_text": result["full_text"],
        "segments": result["segments"],
        "source": result["source"],
        "model": result["model"],
        "language": result.get("language", "auto"),
    }
