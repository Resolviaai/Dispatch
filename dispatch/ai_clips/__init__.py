"""AI Clips package for Dispatch."""
from dispatch.ai_clips.highlight_finder import (
    identify_and_save_highlights,
    extract_clips_gemini,
    extract_clips_local_heuristic,
    snap_to_word_boundary
)
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt

__all__ = [
    "identify_and_save_highlights",
    "extract_clips_gemini",
    "extract_clips_local_heuristic",
    "snap_to_word_boundary",
    "get_negative_feedback_prompt"
]
