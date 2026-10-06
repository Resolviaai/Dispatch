"""Transcription package for Dispatch."""
from dispatch.transcription.audio import extract_audio
from dispatch.transcription.transcriber import transcribe_video, get_whisper_model

__all__ = ["extract_audio", "transcribe_video", "get_whisper_model"]
