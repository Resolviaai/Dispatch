"""Unit test for AI highlight identification and packaging."""
from dispatch.ai_clips.highlight_finder import (
    snap_to_word_boundary,
    extract_clips_local_heuristic,
    identify_and_save_highlights
)
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch.db import init_db, reject_clip, save_clip, get_clips_for_review

def test_ai_clips():
    init_db()

    # Synthetic transcript segments
    mock_segments = [
        {
            "id": 0, "start": 0.0, "end": 10.5,
            "text": "Suno aaj hum discuss karenge how Dispatch automates content creation.",
            "words": [
                {"word": "Suno", "start": 0.0, "end": 0.8},
                {"word": "aaj", "start": 0.9, "end": 1.4},
                {"word": "hum", "start": 1.5, "end": 1.9},
                {"word": "discuss", "start": 2.0, "end": 2.6},
                {"word": "karenge", "start": 2.7, "end": 3.4},
            ]
        },
        {
            "id": 1, "start": 10.5, "end": 25.0,
            "text": "Basically you just hit record on your phone and forget everything else.",
            "words": [
                {"word": "Basically", "start": 10.5, "end": 11.2},
                {"word": "you", "start": 11.3, "end": 11.6},
                {"word": "just", "start": 11.7, "end": 12.0},
                {"word": "hit", "start": 12.1, "end": 12.5},
            ]
        },
        {
            "id": 2, "start": 25.0, "end": 45.0,
            "text": "The entire pipeline runs locally on your PC and generates polished shorts automatically.",
            "words": [
                {"word": "The", "start": 25.0, "end": 25.3},
                {"word": "entire", "start": 25.4, "end": 26.0},
                {"word": "pipeline", "start": 26.1, "end": 26.8},
            ]
        }
    ]

    # 1. Test word boundary snapping
    snapped = snap_to_word_boundary(11.0, mock_segments, prefer_start=True)
    assert snapped in (10.5, 11.3), f"Unexpected snapped value: {snapped}"

    # 2. Test local heuristic clip extraction
    clips = extract_clips_local_heuristic(mock_segments, total_duration=45.0)
    assert len(clips) >= 1, "Should have extracted at least one candidate clip"
    clip = clips[0]
    assert clip["start_time"] >= 0.0
    assert clip["end_time"] <= 45.0
    assert clip["virality_score"] >= 60

    # 3. Test saving to DB
    clip_ids = identify_and_save_highlights(
        chunk_id=None,
        session_id=None,
        segments=mock_segments,
        total_duration=45.0
    )
    assert len(clip_ids) >= 1, "Expected saved clips in DB"

    # 4. Test negative preference feedback
    reject_clip(clip_ids[0], reason="Too fast talking")
    pref_prompt = get_negative_feedback_prompt()
    assert "Too fast talking" in pref_prompt, "Negative preference not reflected in prompt"

    print("ALL AI CLIPS HIGHLIGHT TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_ai_clips()
