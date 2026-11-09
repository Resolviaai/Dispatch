from pathlib import Path
"""Exhaustive tests for Dispatch Highlight Pipeline Enhancements:
1. Duration-proportional continuous yield equation N_target(D).
2. Bug #2 Fix: Stage 2 Word-Snap does not expand into +-4s padded slice.
3. Bug #3 Fix: False-start speech restart detection does not prematurely break.
4. Quote Anchor matching (start_words & end_words) for millisecond alignment.
5. Opening filler token stripping (so, basically, um, toh, matlab).
6. Trailing conjunction trimming (because, and, so, kyunki).
7. Containment deduplication (overlap / min(dur1, dur2) > 0.45).
8. Tier 'A' vs Tier 'B' editorial ranking priority.
9. Windowed extraction for long videos (>12m).
"""
import math
import unittest
from unittest.mock import patch, MagicMock

from dispatch.ai_clips.prompt_templates import (
    compute_optimal_clip_quota,
    build_highlight_user_prompt,
    HIGHLIGHT_SYSTEM_PROMPT
)
from dispatch.ai_clips.highlight_finder import (
    HighlightCandidate,
    compute_containment_ratio,
    compute_interval_overlap,
    clean_speech_restarts_and_boundaries,
    snap_clip_to_stage2_words,
    match_quote_anchor,
    strip_leading_filler_words,
    trim_trailing_conjunctions,
    validate_and_filter_candidates,
    extract_highlights_windowed,
)


class TestHighlightEnhancements(unittest.TestCase):

    def test_compute_optimal_clip_quota_piecewise_formula(self):
        """Verify the exact piecewise formula from the user's specification image:
        - 5 <= D < 60: 1
        - 60 <= D < 300: max(2, floor(D / 45))
        - D >= 300: max(5, min(24, floor(D / 75) + floor(sqrt(D / 180))))
        """
        # Under 60s
        self.assertEqual(compute_optimal_clip_quota(10.0), 1)
        self.assertEqual(compute_optimal_clip_quota(45.0), 1)
        self.assertEqual(compute_optimal_clip_quota(59.9), 1)

        # 60s to 300s: max(2, floor(D / 45))
        self.assertEqual(compute_optimal_clip_quota(60.0), 2)   # max(2, 1) = 2
        self.assertEqual(compute_optimal_clip_quota(90.0), 2)   # max(2, 2) = 2
        self.assertEqual(compute_optimal_clip_quota(135.0), 3)  # floor(135/45) = 3
        self.assertEqual(compute_optimal_clip_quota(180.0), 4)  # floor(180/45) = 4
        self.assertEqual(compute_optimal_clip_quota(270.0), 6)  # floor(270/45) = 6
        self.assertEqual(compute_optimal_clip_quota(299.0), 6)

        # D >= 300s: max(5, min(24, floor(D/75) + floor(sqrt(D/180))))
        # 300s (5m): floor(4) + floor(sqrt(1.666)) = 4 + 1 = 5
        self.assertEqual(compute_optimal_clip_quota(300.0), 5)

        # 601.7s (~10m, like video cUvsEGNruoY):
        # floor(601.7/75) = 8, floor(sqrt(601.7/180)) = floor(1.828) = 1 -> 8 + 1 = 9
        self.assertEqual(compute_optimal_clip_quota(601.7), 9)

        # 900s (15m): floor(12) + floor(sqrt(5)) = 12 + 2 = 14
        self.assertEqual(compute_optimal_clip_quota(900.0), 14)

        # 1800s (30m): floor(24) + floor(sqrt(10)) = 24 + 3 = 27 -> capped at 24
        self.assertEqual(compute_optimal_clip_quota(1800.0), 24)

        # 3600s (60m): capped at 24
        self.assertEqual(compute_optimal_clip_quota(3600.0), 24)

    def test_prompt_includes_duration_and_target_yield(self):
        """Verify build_highlight_user_prompt passes duration guidance and target quota."""
        prompt = build_highlight_user_prompt("Mock transcript text", total_duration=601.7)
        self.assertIn("601 seconds (~10 minutes)", prompt)
        self.assertIn("Extract approximately 9 distinct", prompt)
        self.assertIn("SWEET-SPOT DURATION: Target 30 to 75 seconds", prompt)
        self.assertIn("QUOTE ANCHORS", prompt)

    def test_stage2_word_snapping_does_not_expand_to_padding(self):
        """Bug #2 Fix: Word snap must snap to the word closest to target cut, NOT words[0] and words[-1] of padded slice."""
        target_start = 12.50
        target_end = 45.00
        padded_start = 8.50  # 4s lead-in
        padded_end = 49.00   # 4s tail

        # Words relative to padded_start (8.50):
        # - Word 0 at 0.3s (absolute 8.80s) in the lead-in padding (unrelated sentence)
        # - Word 1 at 3.9s (absolute 12.40s) where the target clip actually starts
        # - Word 2 at 36.4s (absolute 44.90s) where the target clip ends
        # - Word 3 at 40.2s (absolute 48.70s) in the trailing padding (next conversation)
        stage2_words = [
            {"word": "Previous", "start": 0.30, "end": 0.70},
            {"word": "sentence", "start": 0.80, "end": 1.20},
            {"word": "Actually", "start": 3.90, "end": 4.30},  # abs start: 8.5 + 3.9 = 12.40s
            {"word": "this", "start": 4.40, "end": 4.60},
            {"word": "is", "start": 4.70, "end": 4.80},
            {"word": "valuable", "start": 36.00, "end": 36.40},  # abs end: 8.5 + 36.4 = 44.90s
            {"word": "Next", "start": 39.80, "end": 40.10},
            {"word": "unrelated", "start": 40.20, "end": 40.60},  # abs end: 8.5 + 40.6 = 49.10s
        ]

        snapped_s, snapped_e = snap_clip_to_stage2_words(
            target_start=target_start,
            target_end=target_end,
            stage2_words=stage2_words,
            padded_start=padded_start,
            padded_end=padded_end,
            max_snap_tolerance=2.0
        )

        # Snapped start must be near 12.40s (target 12.5s), NOT 8.60s (Word 0)!
        self.assertAlmostEqual(snapped_s, 12.25, delta=0.1)
        self.assertGreater(snapped_s, 11.5)  # strictly excluded the padded lead-in!

        # Snapped end must be near 45.15s (target 45.0s), NOT 49.0s (Word 3)!
        self.assertAlmostEqual(snapped_e, 45.15, delta=0.2)
        self.assertLess(snapped_e, 46.5)  # strictly excluded the padded lead-out!

    def test_speech_restart_detection_not_prematurely_aborted(self):
        """Bug #3 Fix: False-start speech restart detection must not break on first word margin."""
        # Candidate runs from 10.0 to 40.0.
        # Speaker stutters: "humne yeh kiya" at 10.2s, pauses 1.5s, then repeats cleanly: "humne yeh kiya dispatch mein" at 12.5s.
        # clip_words[0] starts at 9.9s (less than start_t = 10.0s).
        mock_segments = [
            {
                "words": [
                    {"word": "before", "start": 9.9, "end": 10.1},
                    {"word": "humne", "start": 10.2, "end": 10.5},
                    {"word": "yeh", "start": 10.6, "end": 10.8},
                    {"word": "kiya", "start": 10.9, "end": 11.2},
                    # Pause: 11.2 to 12.7 (1.5s gap)
                    {"word": "humne", "start": 12.7, "end": 13.0},
                    {"word": "yeh", "start": 13.1, "end": 13.3},
                    {"word": "kiya", "start": 13.4, "end": 13.7},
                    {"word": "dispatch", "start": 13.8, "end": 14.2},
                    {"word": "mein", "start": 14.3, "end": 14.6},
                    {"word": "complete", "start": 20.0, "end": 20.5},
                    {"word": "thought", "start": 30.0, "end": 30.5},
                ]
            }
        ]

        cleaned_s, cleaned_e = clean_speech_restarts_and_boundaries(
            start_t=10.0,
            end_t=35.0,
            segments=mock_segments,
            total_duration=60.0
        )

        # The restart was at 12.7s; cleaned_s should snap forward to ~12.55s!
        self.assertGreater(cleaned_s, 12.0, "Failed to detect speech restart due to premature break")
        self.assertAlmostEqual(cleaned_s, 12.55, delta=0.2)

    def test_quote_anchor_matching(self):
        """Verify match_quote_anchor locates exact spoken phrases within search window."""
        mock_segments = [
            {
                "words": [
                    {"word": "Nobody", "start": 25.4, "end": 25.8},
                    {"word": "tells", "start": 25.9, "end": 26.2},
                    {"word": "you", "start": 26.3, "end": 26.5},
                    {"word": "raising", "start": 26.6, "end": 27.0},
                    {"word": "prices", "start": 27.1, "end": 27.5},
                    {"word": "is", "start": 27.6, "end": 27.8},
                    {"word": "easy", "start": 27.9, "end": 28.2},
                ]
            }
        ]

        # Gemini returns approx start 24.0, but quote anchor is "Nobody tells you raising prices"
        anchor_start = match_quote_anchor(
            quote="Nobody tells you raising prices",
            segments=mock_segments,
            approx_time=24.0,
            prefer_start=True
        )
        self.assertIsNotNone(anchor_start)
        self.assertAlmostEqual(anchor_start, 25.4, delta=0.05)

        # End quote anchor: "prices is easy"
        anchor_end = match_quote_anchor(
            quote="prices is easy",
            segments=mock_segments,
            approx_time=29.0,
            prefer_start=False
        )
        self.assertIsNotNone(anchor_end)
        self.assertAlmostEqual(anchor_end, 28.2, delta=0.05)

    def test_opening_filler_stripping(self):
        """Verify strip_leading_filler_words advances past 'so basically toh' to core statement."""
        mock_segments = [
            {
                "words": [
                    {"word": "Basically", "start": 10.0, "end": 10.4},
                    {"word": "toh", "start": 10.5, "end": 10.7},
                    {"word": "pricing", "start": 10.9, "end": 11.4},
                    {"word": "strategy", "start": 11.5, "end": 12.0},
                    {"word": "decides", "start": 12.1, "end": 12.5},
                    {"word": "everything", "start": 12.6, "end": 13.0},
                ]
            }
        ]

        stripped_start = strip_leading_filler_words(
            start_t=10.0,
            end_t=40.0,
            segments=mock_segments
        )
        # Should skip 'Basically' (ends 10.4) and 'toh' (ends 10.7) and start near 10.8s
        self.assertGreater(stripped_start, 10.7)
        self.assertLessEqual(stripped_start, 10.9)

    def test_trailing_conjunction_trimming(self):
        """Verify trim_trailing_conjunctions removes dangling 'because' / 'kyunki'."""
        mock_segments = [
            {
                "words": [
                    {"word": "we", "start": 20.0, "end": 20.3},
                    {"word": "achieved", "start": 20.4, "end": 20.8},
                    {"word": "record", "start": 20.9, "end": 21.3},
                    {"word": "growth", "start": 21.4, "end": 21.9},
                    {"word": "because", "start": 22.1, "end": 22.5},  # cut off mid-thought!
                ]
            }
        ]

        trimmed_end = trim_trailing_conjunctions(
            start_t=10.0,
            end_t=22.6,
            segments=mock_segments
        )
        # Should trim back to 'growth' (ends 21.9 + 0.15 = 22.05)
        self.assertAlmostEqual(trimmed_end, 22.05, delta=0.1)

    def test_containment_deduplication(self):
        """Verify containment ratio catches a 20s clip 100% inside a 50s clip where IoU is only 0.40."""
        # Clip 1: [10.0, 30.0] (20s)
        # Clip 2: [0.0, 50.0] (50s)
        iou = compute_interval_overlap(10.0, 30.0, 0.0, 50.0)
        containment = compute_containment_ratio(10.0, 30.0, 0.0, 50.0)

        self.assertAlmostEqual(iou, 20.0 / 50.0, delta=0.01)  # 0.40 <= 0.50 (missed by standard IoU!)
        self.assertAlmostEqual(containment, 1.0, delta=0.01)   # 100% contained!

        raw_candidates = [
            {
                "start_time": 0.0,
                "end_time": 50.0,
                "title": "Master clip with full context",
                "virality_score": 90,
                "tier": "A"
            },
            {
                "start_time": 10.0,
                "end_time": 30.0,
                "title": "Nested sub-clip",
                "virality_score": 75,
                "tier": "B"
            }
        ]

        deduped = validate_and_filter_candidates(
            raw_candidates=raw_candidates,
            segments=[],
            total_duration=100.0,
            max_clips=10
        )

        # Only the Master clip should survive
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "Master clip with full context")

    def test_tier_a_priority_over_virality_score(self):
        """Verify Tier 'A' clip is prioritized over Tier 'B' even if Tier 'B' had higher nominal score."""
        raw_candidates = [
            {
                "start_time": 10.0,
                "end_time": 40.0,
                "title": "Tier B clip with inflated score",
                "virality_score": 95,
                "tier": "B"
            },
            {
                "start_time": 12.0,
                "end_time": 42.0,
                "title": "Tier A pristine clip",
                "virality_score": 85,
                "tier": "A"
            }
        ]

        deduped = validate_and_filter_candidates(
            raw_candidates=raw_candidates,
            segments=[],
            total_duration=100.0,
            max_clips=10
        )

        # Tier A pristine clip must be kept, overlapping Tier B suppressed
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "Tier A pristine clip")
        self.assertEqual(deduped[0]["tier"], "A")

    @patch("dispatch.ai_clips.highlight_finder.call_gemini_generate_content")
    def test_windowed_extraction_splits_and_merges_candidates(self, mock_gemini):
        """Verify long video (>720s) splits into overlapping 12-minute windows and merges candidates."""
        total_duration = 1500.0  # 25 minutes -> 3 windows ([0..720], [630..1350], [1260..1500])

        mock_gemini.side_effect = [
            ([{"start_time": 30.0, "end_time": 75.0, "title": "Win 1 Clip", "tier": "A"}], "gemini-flash"),
            ([{"start_time": 700.0, "end_time": 745.0, "title": "Win 2 Clip", "tier": "A"}], "gemini-flash"),
            ([{"start_time": 1300.0, "end_time": 1345.0, "title": "Win 3 Clip", "tier": "A"}], "gemini-flash"),
        ]

        # Generate synthetic segments spanning 0 to 1500s
        synthetic_segments = [
            {"start": 10.0, "end": 100.0, "text": "Beginning of long video."},
            {"start": 650.0, "end": 750.0, "text": "Middle turning point."},
            {"start": 1300.0, "end": 1400.0, "text": "Concluding remarks."}
        ]

        raw_clips, model_used = extract_highlights_windowed(
            segments=synthetic_segments,
            total_duration=total_duration
        )

        self.assertEqual(len(raw_clips), 3)
        self.assertEqual(mock_gemini.call_count, 3)
        self.assertEqual(raw_clips[0]["title"], "Win 1 Clip")
        self.assertEqual(raw_clips[1]["title"], "Win 2 Clip")
        self.assertEqual(raw_clips[2]["title"], "Win 3 Clip")



    def test_sarcasm_and_irony_lookahead_extends_retraction(self):
        """Verify apply_sarcasm_and_irony_lookahead detects retraction markers and extends candidate."""
        from dispatch.ai_clips.highlight_finder import apply_sarcasm_and_irony_lookahead
        mock_segments = [
            {
                "words": [
                    {"word": "We", "start": 10.0, "end": 10.3},
                    {"word": "should", "start": 10.4, "end": 10.6},
                    {"word": "delete", "start": 10.7, "end": 11.0},
                    {"word": "all", "start": 11.1, "end": 11.3},
                    {"word": "code.", "start": 11.4, "end": 11.8},
                    # Sarcasm retraction within 4s of 11.8s
                    {"word": "Obviously", "start": 12.2, "end": 12.6},
                    {"word": "just", "start": 12.7, "end": 12.9},
                    {"word": "kidding", "start": 13.0, "end": 13.4},
                    {"word": "guys.", "start": 13.5, "end": 13.8},
                ]
            }
        ]

        # Candidate ends at 11.8s (before retraction)
        new_s, new_e, is_safe = apply_sarcasm_and_irony_lookahead(
            start_t=10.0,
            end_t=11.8,
            segments=mock_segments,
            total_duration=60.0
        )
        self.assertTrue(is_safe)
        self.assertGreater(new_e, 13.5)
        self.assertAlmostEqual(new_e, 14.05, delta=0.2)

    def test_sarcasm_and_irony_lookahead_rejects_overlong(self):
        """Verify apply_sarcasm_and_irony_lookahead rejects candidate when retraction pushes beyond MAX_CLIP_DURATION."""
        from dispatch.ai_clips.highlight_finder import apply_sarcasm_and_irony_lookahead
        mock_segments = [
            {
                "words": [
                    {"word": "Obviously", "start": 182.0, "end": 182.5},
                    {"word": "just", "start": 182.6, "end": 182.8},
                    {"word": "kidding.", "start": 182.9, "end": 183.4},
                ]
            }
        ]
        # Candidate starts at 0.0s, ends at 178.0s. Retraction at 183.4s pushes duration > 180.0s.
        new_s, new_e, is_safe = apply_sarcasm_and_irony_lookahead(
            start_t=0.0,
            end_t=178.0,
            segments=mock_segments,
            total_duration=200.0
        )
        self.assertFalse(is_safe, "Must reject clip if retraction cannot fit safely within MAX_CLIP_DURATION")

    def test_detect_sponsor_promo_contamination_trims_edge(self):
        """Verify detect_sponsor_promo_contamination trims trailing subscribe asks."""
        from dispatch.ai_clips.highlight_finder import detect_sponsor_promo_contamination
        mock_segments = [
            {
                "words": [
                    {"word": "Great", "start": 10.0, "end": 10.5},
                    {"word": "lesson", "start": 10.6, "end": 11.0},
                    {"word": "here.", "start": 11.1, "end": 11.5},
                    # Trailing subscribe ask at 30.0s to 32.5s
                    {"word": "Subscribe", "start": 30.0, "end": 30.6},
                    {"word": "to", "start": 30.7, "end": 30.8},
                    {"word": "the", "start": 30.9, "end": 31.0},
                    {"word": "channel.", "start": 31.1, "end": 31.6},
                ]
            }
        ]
        new_s, new_e, is_clean = detect_sponsor_promo_contamination(
            start_t=10.0,
            end_t=32.0,
            segments=mock_segments
        )
        self.assertTrue(is_clean)
        self.assertLess(new_e, 30.0)

    def test_laughter_release_window_extends_end(self):
        """Verify apply_laughter_release_window preserves natural laugh decay after punchline."""
        from dispatch.ai_clips.highlight_finder import apply_laughter_release_window
        mock_segments = [
            {
                "words": [
                    {"word": "Best", "start": 10.0, "end": 10.4},
                    {"word": "joke", "start": 10.5, "end": 10.8},
                    {"word": "ever.", "start": 10.9, "end": 11.3},
                    {"word": "[laughter]", "start": 11.5, "end": 12.8},
                ]
            }
        ]
        extended_e = apply_laughter_release_window(
            end_t=11.3,
            segments=mock_segments,
            total_duration=30.0
        )
        self.assertGreater(extended_e, 12.5)

    def test_positive_style_anchors_and_editorial_guidance(self):
        """Verify positive style anchors are retrieved from database and formatted into prompt."""
        import tempfile
        from dispatch import db
        from dispatch.ai_clips.preference_learner import get_positive_style_anchors, get_editorial_guidance_prompt

        temp_dir = tempfile.TemporaryDirectory()
        orig_db = db.DB_PATH
        try:
            db.DB_PATH = Path(temp_dir.name) / "test_pref.db"
            db.init_db()

            # Insert an approved clip
            cid = db.save_clip(
                session_id=None,
                chunk_id=None,
                start_time=15.0,
                end_time=45.0,
                duration=30.0,
                title="Surgical Scaling Secrets",
                hook="Never optimize databases before you have users.",
                description="Why early optimization is a trap.",
                hashtags="#Tech",
                virality_score=92,
                layout_mode="fit_blur",
                user_id="user_gold_01"
            )
            db.approve_clip(cid)

            anchors = get_positive_style_anchors(user_id="user_gold_01")
            self.assertIn("USER-APPROVED HIGH-PERFORMANCE STYLE ANCHORS", anchors)
            self.assertIn("Surgical Scaling Secrets", anchors)
            self.assertIn("Never optimize databases before you have users", anchors)

            guidance = get_editorial_guidance_prompt(user_id="user_gold_01")
            self.assertIn("Surgical Scaling Secrets", guidance)

            # Different user sees 0 anchors (strict tenant isolation)
            other_anchors = get_positive_style_anchors(user_id="user_gold_02")
            self.assertEqual(other_anchors, "")
        finally:
            db.DB_PATH = orig_db
            temp_dir.cleanup()

if __name__ == "__main__":
    unittest.main()
