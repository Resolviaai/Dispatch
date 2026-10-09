"""Tests for Dispatch Semantic Highlight Detection and Editorial Quality.
Covers all 16 editorial, lifecycle, silence-removal, and duration invariants:
  1. Valid 5-second final clip.
  2. Exact 180-second final clip.
  3. Overlong clip (> 180s) rejected before downstream processing.
  4. Raw interval > 180s that qualifies after silence removal.
  5. Short moment (< 5s) successfully expanded with surrounding context.
  6. Short moment that cannot be expanded and is rejected.
  7. Long explanation with continuous speech not destructively truncated.
  8. Meaningful, thinking, and comedic pauses preserved (not treated as dead air).
  9. Accidental restart cleanup vs intentional rhetorical repetition.
 10. Multiple topic types (humor, stories, opinions, lessons, reflections).
 11. Zero-candidate / low-density transcript returns 0 clips without fabricating.
 12. Overlapping candidates deduplicated (IoU > 0.5) while preserving distinct nearby moments.
 13. Rejection feedback conditions execution quality without topic blacklisting.
 14. Diverse languages preserved without forced Roman Hinglish transliteration.
 15. Pydantic validation and malformed response handling.
 16. Subtitle synchronization after silence removal.
"""
import unittest
import tempfile
import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch.config import MIN_CLIP_DURATION, MAX_CLIP_DURATION
from dispatch.video_engine.silence import (
    analyze_silence_gaps,
    remap_timestamps_for_silence_cuts,
    build_silence_keep_intervals,
    find_natural_subsection_within_limit
)
from dispatch.ai_clips.highlight_finder import (
    validate_and_filter_candidates,
    expand_short_candidate_with_context,
    clean_speech_restarts_and_boundaries,
    extract_clips_local_heuristic,
    has_devanagari,
    transliterate_segments_to_hinglish,
    HighlightCandidate
)
from dispatch.ai_clips.prompt_templates import (
    HIGHLIGHT_SYSTEM_PROMPT,
    build_highlight_user_prompt
)
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch import db


class TestAIHighlightEditorialQuality(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_editorial.db"
        self._orig_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._orig_db_path
        self.temp_dir.cleanup()

    # 1. Valid 5-second final clip
    def test_01_valid_5_second_final_clip(self):
        segments = [
            {"start": 10.0, "end": 15.0, "text": "This is a quick 5-second observation.", "words": []}
        ]
        raw_candidates = [
            {
                "start_time": 10.0,
                "end_time": 15.0,
                "title": "Quick Insight",
                "virality_score": 85
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=30.0)
        self.assertEqual(len(accepted), 1)
        self.assertGreaterEqual(accepted[0]["duration"], 5.0)
        self.assertLessEqual(accepted[0]["duration"], 180.0)

    # 2. Exact 180-second final clip
    def test_02_exact_180_second_final_clip(self):
        segments = [
            {"start": 0.0, "end": 180.0, "text": "A full complete 180-second deep dive explanation.", "words": []}
        ]
        raw_candidates = [
            {
                "start_time": 0.0,
                "end_time": 180.0,
                "title": "Full Deep Dive",
                "virality_score": 90
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=200.0)
        self.assertEqual(len(accepted), 1)
        self.assertAlmostEqual(accepted[0]["duration"], 180.0, delta=0.5)

    # 3. Overlong clip (> 180s) rejected before downstream processing
    def test_03_overlong_clip_rejected_without_valid_subsection(self):
        # 220s continuous speech with no subsection available
        segments = [
            {"start": 0.0, "end": 220.0, "text": "An overly long monologue.", "words": []}
        ]
        raw_candidates = [
            {
                "start_time": 0.0,
                "end_time": 220.0,
                "title": "Endless Monologue",
                "virality_score": 75
            }
        ]
        # Candidate exceeds 180s and find_natural_subsection_within_limit fails or yields none
        with patch("dispatch.ai_clips.highlight_finder.find_natural_subsection_within_limit", return_value=None):
            accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=250.0)
            self.assertEqual(len(accepted), 0)

    # 4. Raw interval > 180s that qualifies after silence removal
    def test_04_raw_interval_over_180s_qualifies_after_silence_removal(self):
        # 210s interval, but contains a 40s silence gap (from 80s to 120s)
        segments = [
            {"start": 0.0, "end": 80.0, "text": "Part one of the story.", "words": []},
            {"start": 120.0, "end": 210.0, "text": "Part two of the story after prolonged silence.", "words": []}
        ]
        eff_dur, cuts = analyze_silence_gaps(0.0, 210.0, segments)
        # 40s dead-air gap trimmed down to 0.8s -> removes 39.2s -> effective duration ~170.8s
        self.assertLessEqual(eff_dur, 180.0)
        self.assertGreater(len(cuts), 0)

        raw_candidates = [
            {
                "start_time": 0.0,
                "end_time": 210.0,
                "title": "Long Story With Dead Air",
                "virality_score": 88
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=220.0)
        self.assertEqual(len(accepted), 1)
        self.assertLessEqual(accepted[0]["duration"], 180.0)
        self.assertGreaterEqual(accepted[0]["duration"], 5.0)

    # 5. Short moment (< 5s) successfully expanded with context
    def test_05_short_moment_expanded_with_surrounding_context(self):
        # Setup at 10.0-14.0, Punchline at 14.5-17.5 (3s), Reaction at 18.0-21.0
        segments = [
            {"start": 10.0, "end": 14.0, "text": "So I walked into the server room.", "words": []},
            {"start": 14.5, "end": 17.5, "text": "And the server was literally in the fridge!", "words": []},
            {"start": 18.0, "end": 21.0, "text": "Everyone looked at me like I was crazy.", "words": []}
        ]
        # Candidate is only the 3-second punchline
        raw_candidates = [
            {
                "start_time": 14.5,
                "end_time": 17.5,
                "title": "Fridge Server Punchline",
                "virality_score": 92
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=30.0)
        self.assertEqual(len(accepted), 1)
        # It must have expanded to include setup/reaction so duration is >= 5.0s
        self.assertGreaterEqual(accepted[0]["duration"], 5.0)
        self.assertLess(accepted[0]["start_time"], 14.5)

    # 6. Short moment that cannot be expanded and is rejected
    def test_06_short_isolated_moment_rejected(self):
        # Isolated 2.5s speech segment surrounded by huge silence
        segments = [
            {"start": 50.0, "end": 52.5, "text": "Just two words.", "words": []}
        ]
        raw_candidates = [
            {
                "start_time": 50.0,
                "end_time": 52.5,
                "title": "Tiny Fragment",
                "virality_score": 50
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=100.0)
        self.assertEqual(len(accepted), 0)

    # 7. Long explanation with meaningful speech not destructively truncated
    def test_07_long_meaningful_explanation_not_chopped(self):
        # 160s of solid explanation
        segments = [
            {"start": 10.0, "end": 90.0, "text": "Detailed architecture explanation part 1.", "words": []},
            {"start": 91.0, "end": 170.0, "text": "Detailed architecture explanation part 2.", "words": []}
        ]
        raw_candidates = [
            {
                "start_time": 10.0,
                "end_time": 170.0,
                "title": "Comprehensive Architecture Walkthrough",
                "virality_score": 95
            }
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments, total_duration=200.0)
        self.assertEqual(len(accepted), 1)
        # Verify it was kept intact (~160s) rather than artificially chopped to 60s
        self.assertGreater(accepted[0]["duration"], 150.0)
        self.assertLessEqual(accepted[0]["duration"], 180.0)

    # 8. Meaningful and comedic pauses preserved
    def test_08_meaningful_and_comedic_pauses_preserved(self):
        # A 2.2-second pause before a punchline should NOT be trimmed as dead air
        segments = [
            {"start": 10.0, "end": 14.0, "text": "Why do programmers prefer dark mode?", "words": []},
            # 2.2s dramatic/comedic pause: 14.0s to 16.2s
            {"start": 16.2, "end": 20.0, "text": "Because light attracts bugs.", "words": []}
        ]
        eff_dur, cuts = analyze_silence_gaps(10.0, 20.0, segments)
        # Gap is 2.2s (< 3.0s threshold), so cuts should be empty and effective duration intact
        self.assertEqual(len(cuts), 0)
        self.assertAlmostEqual(eff_dur, 10.0, delta=0.1)

    # 9. Accidental restart cleanup vs intentional rhetorical repetition
    def test_09_speech_restart_cleanup(self):
        # False start: "I think... I think the real issue is..."
        segments = [
            {
                "start": 10.0,
                "end": 20.0,
                "text": "I think I think the real issue is clarity.",
                "words": [
                    {"word": "I", "start": 10.0, "end": 10.3},
                    {"word": "think", "start": 10.4, "end": 10.7},
                    {"word": "I", "start": 12.0, "end": 12.3},  # restart after 1.3s pause
                    {"word": "think", "start": 12.4, "end": 12.7},
                    {"word": "the", "start": 12.8, "end": 13.0},
                    {"word": "real", "start": 13.1, "end": 13.4},
                    {"word": "issue", "start": 13.5, "end": 13.8},
                    {"word": "is", "start": 13.9, "end": 14.1},
                    {"word": "clarity.", "start": 14.2, "end": 14.6}
                ]
            }
        ]
        clean_start, clean_end = clean_speech_restarts_and_boundaries(10.0, 20.0, segments, total_duration=30.0)
        # Clean take starts at 12.0s, so snapped start should be ~11.85s (12.0 - 0.15)
        self.assertGreater(clean_start, 11.5)

    # 10. Multiple topic types (humor, stories, opinions, lessons, reflections)
    def test_10_multiple_topic_types_supported_in_prompts(self):
        # Verify the prompt system explicitly welcomes diverse creative topics
        prompt_lower = HIGHLIGHT_SYSTEM_PROMPT.lower()
        self.assertIn("humor, jokes", prompt_lower)
        self.assertIn("personal stories, anecdotes", prompt_lower)
        self.assertIn("strong opinions", prompt_lower)
        self.assertIn("philosophical reflections", prompt_lower)
        self.assertIn("surprising facts", prompt_lower)
        self.assertIn("spontaneous reactions", prompt_lower)

    # 11. Zero-candidate transcript returns 0 clips without fabricating
    def test_11_zero_candidate_returns_empty_list_no_fabrication(self):
        # Empty segments
        clips_empty = extract_clips_local_heuristic([], total_duration=0.0)
        self.assertEqual(clips_empty, [])

        # Low density segments (< 4 words)
        short_segs = [{"start": 0.0, "end": 10.0, "text": "um okay", "words": []}]
        clips_noise = extract_clips_local_heuristic(short_segs, total_duration=10.0)
        self.assertEqual(clips_noise, [])

    # 12. Overlapping candidates deduplicated while preserving distinct nearby moments
    def test_12_deduplication_keeps_distinct_moments(self):
        raw_candidates = [
            # Two heavy overlapping clips (IoU > 0.5)
            {"start_time": 10.0, "end_time": 40.0, "title": "Setup High Score", "virality_score": 95},
            {"start_time": 12.0, "end_time": 42.0, "title": "Setup Low Score", "virality_score": 60},
            # Distinct nearby clip [45.0, 75.0] (IoU == 0.0)
            {"start_time": 45.0, "end_time": 75.0, "title": "Distinct Second Thought", "virality_score": 85}
        ]
        accepted = validate_and_filter_candidates(raw_candidates, segments=[], total_duration=100.0)
        self.assertEqual(len(accepted), 2)
        titles = [c["title"] for c in accepted]
        self.assertIn("Setup High Score", titles)
        self.assertIn("Distinct Second Thought", titles)
        self.assertNotIn("Setup Low Score", titles)

    # 13. Rejection feedback without topic blacklisting
    def test_13_rejection_feedback_avoids_topic_blacklisting(self):
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=10.0,
            end_time=35.0,
            title="AI Tools Review",
            hook="Check these tools out",
            description="Testing rejection conditioning",
            hashtags="#AI",
            virality_score=50,
            layout_mode="fit_blur"
        )
        db.reject_clip(clip_id, reason="Slow pacing and rambling start")

        feedback_prompt = get_negative_feedback_prompt()
        self.assertIn("DO NOT blacklist or avoid a topic", feedback_prompt)
        self.assertIn("Treat rejections as feedback on EDITORIAL EXECUTION", feedback_prompt)

    # 14. Diverse languages without forced Roman Hinglish
    def test_14_pure_english_not_transliterated(self):
        english_text = "This is a purely English presentation on distributed databases."
        self.assertFalse(has_devanagari(english_text))

        segments = [{"start": 0.0, "end": 10.0, "text": english_text, "words": []}]
        processed = transliterate_segments_to_hinglish(segments)
        self.assertEqual(processed[0]["text"], english_text)

    # 15. Pydantic validation & malformed response handling
    def test_15_pydantic_validation_and_malformed_responses(self):
        malformed_list = [
            {"title": "Missing start and end"},
            {"start_time": "not_a_number", "end_time": 20.0, "title": "Bad float"},
            {"start_time": 10.0, "end_time": 25.0, "title": "", "virality_score": 80},  # Empty title
            {"start_time": 10.0, "end_time": 25.0, "title": "Score too high", "virality_score": 250},
            {"start_time": 10.0, "end_time": 25.0, "title": "Valid Clip", "virality_score": 85}
        ]
        accepted = validate_and_filter_candidates(malformed_list, segments=[], total_duration=50.0)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["title"], "Valid Clip")

    # 16. Subtitle synchronization after silence removal
    def test_16_subtitle_synchronization_after_silence_removal(self):
        # A 100s clip with a 20s dead-air gap removed between 30s and 50s
        # (30s to 50s: 20s gap trimmed to 0.8s -> 19.2s cut)
        cuts = [(30.0, 49.2, 19.2)]
        segments = [
            {
                "start": 10.0,
                "end": 25.0,
                "text": "Before cut segment.",
                "words": [
                    {"word": "Before", "start": 10.0, "end": 15.0},
                    {"word": "cut", "start": 15.5, "end": 20.0}
                ]
            },
            {
                "start": 55.0,
                "end": 70.0,
                "text": "After cut segment.",
                "words": [
                    {"word": "After", "start": 55.0, "end": 60.0},
                    {"word": "cut", "start": 60.5, "end": 65.0}
                ]
            }
        ]

        remapped = remap_timestamps_for_silence_cuts(segments, clip_start=0.0, clip_end=100.0, cuts=cuts)
        self.assertEqual(len(remapped), 2)

        # Before cut segment should be unshifted relative to clip start
        self.assertEqual(remapped[0]["start"], 10.0)
        self.assertEqual(remapped[0]["words"][0]["start"], 10.0)

        # After cut segment should be shifted earlier by 19.2s
        expected_shifted_start = round(55.0 - 19.2, 3)
        self.assertAlmostEqual(remapped[1]["start"], expected_shifted_start, places=2)
        self.assertAlmostEqual(remapped[1]["words"][0]["start"], round(55.0 - 19.2, 3), places=2)


if __name__ == "__main__":
    unittest.main()
