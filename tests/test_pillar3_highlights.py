"""Pillar 3 Unit & Integration Tests: Gemini Semantic Highlight Selection.

Tests all required Pillar 3 properties:
  A. Happy-path discovery & processing: TRANSCRIBED -> ANALYZING -> CLIPS_DEFINED.
  B. Valid clip schema: title, hook, description, hashtags, virality_score, layout_mode, duration.
  C. Malformed JSON & schema violations gracefully skipped/filtered.
  D. Word boundary snapping: timestamps snapped to closest spoken word.
  E. Strict 20.0s <= duration <= 90.0s constraint: invalid durations rejected, never manufactured.
  F. Overlap deduplication: overlapping candidates (IoU > 0.5) suppressed, retaining higher virality.
  G. Negative feedback conditioning: rejections injected into prompt.
  H. Rate-limit (429/503/timeout) retry backoff: scheduled via next_ai_attempt_at.
  I. Missing or invalid GEMINI_API_KEY: transitions to permanent failure.
  J. Strict failure: zero fallback to local heuristic.
  K. Restart idempotency: resuming or re-running does not duplicate clips.
  L. Transaction atomicity: partial failures roll back cleanly.
  M. Silence / zero speech: marks CLIPS_DEFINED with 0 clips without calling Gemini.
  N. Invariant check: clips table rows have video_path IS NULL, thumbnail_path IS NULL, status='ready_review'.
  O. Publishing outbox empty: Pillar 3 does not queue or touch social outbox.
"""
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from dispatch import db
from dispatch.config import DB_PATH
from dispatch.ai_clips.highlight_finder import (
    snap_to_word_boundary,
    compute_interval_overlap,
    validate_and_filter_candidates,
    call_gemini_generate_content,
    identify_and_save_highlights,
)
from dispatch.ai_clips.worker import HighlightWorker


class TestPillar3Highlights(unittest.TestCase):
    """Pillar 3 highlight selection test suite."""

    def setUp(self):
        """Set up fresh isolated test SQLite database."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_dispatch.db"
        self._orig_db_path = db.DB_PATH
        db.DB_PATH = self.db_file
        db.init_db()

    def tearDown(self):
        """Restore DB path and cleanup."""
        db.DB_PATH = self._orig_db_path
        self.temp_dir.cleanup()

    def _seed_transcribed_video(
        self,
        video_id: str = "p3_vid_001",
        duration: float = 300.0,
        segments: list = None
    ) -> str:
        """Helper to register chunk and seed youtube_inbox item in TRANSCRIBED status."""
        if segments is None:
            segments = [
                {
                    "start": 10.0,
                    "end": 45.0,
                    "text": "Aaj hum automation setup karenge jo bilkul seamless hai.",
                    "words": [
                        {"word": "Aaj", "start": 10.0, "end": 10.5},
                        {"word": "automation", "start": 10.6, "end": 11.2},
                        {"word": "setup", "start": 11.3, "end": 11.8},
                        {"word": "hai.", "start": 44.5, "end": 45.0}
                    ]
                },
                {
                    "start": 50.0,
                    "end": 95.0,
                    "text": "Second insight yeh hai ki reliable queues hona zaroori hai.",
                    "words": [
                        {"word": "Second", "start": 50.0, "end": 50.6},
                        {"word": "hai.", "start": 94.2, "end": 95.0}
                    ]
                }
            ]

        # Register chunk
        chunk_id = db.register_chunk(
            session_id=None,
            filename=f"{video_id}.mp4",
            filepath=f"/tmp/{video_id}.mp4",
            file_hash=f"yt_{video_id}"
        )
        db.update_chunk_metadata(
            chunk_id=chunk_id,
            duration=duration,
            width=1080,
            height=1920,
            aspect_ratio="9:16",
            status="transcribed"
        )

        full_text = " ".join(s["text"] for s in segments)
        db.save_transcript(
            chunk_id=chunk_id,
            session_id=None,
            full_text=full_text,
            segments=segments
        )

        db.register_youtube_video(
            video_id=video_id,
            title=f"Test Video {video_id}",
            duration=duration
        )
        db.update_youtube_video(
            video_id=video_id,
            status="TRANSCRIBED",
            transcript_source="whisper",
            segments_json=json.dumps(segments)
        )
        return video_id

    # Test A: Happy-path discovery & processing
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_a_happy_path_transcribed_to_clips_defined(self, mock_gemini):
        mock_gemini.return_value = (
            [
                {
                    "start_time": 10.0,
                    "end_time": 45.0,
                    "title": "Automation Setup Guide",
                    "hook": "Aaj hum automation setup karenge",
                    "description": "Seamless setup guide in Hinglish",
                    "hashtags": "#Automation #Hinglish",
                    "virality_score": 88,
                    "layout_recommendation": "fit_blur",
                    "reason": "Complete thought with strong hook"
                }
            ],
            "gemini-3.5-flash-lite"
        )

        video_id = self._seed_transcribed_video("vid_happy_01")
        worker = HighlightWorker(poll_interval_seconds=1)
        res = worker.process_video_highlights(video_id)

        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 1)

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "CLIPS_DEFINED")

        # Verify clips in database
        clips = db.get_clips_for_review()
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["title"], "Automation Setup Guide")
        self.assertEqual(clips[0]["status"], "ready_review")
        self.assertIsNone(clips[0]["video_path"])
        self.assertIsNone(clips[0]["thumbnail_path"])

    # Test B: Valid clip schema validation
    def test_b_candidate_schema_fields(self):
        raw = [
            {
                "start_time": 10.0,
                "end_time": 45.0,
                "title": "Viral Hook Title",
                "hook": "Hook opener",
                "description": "Short summary",
                "hashtags": "#Tech #Viral",
                "virality_score": 92,
                "layout_recommendation": "crop_follow",
                "reason": "High retention"
            }
        ]
        validated = validate_and_filter_candidates(raw, segments=[], total_duration=100.0)
        self.assertEqual(len(validated), 1)
        cand = validated[0]
        self.assertEqual(cand["title"], "Viral Hook Title")
        self.assertEqual(cand["duration"], 35.0)
        self.assertEqual(cand["virality_score"], 92)
        self.assertEqual(cand["layout_recommendation"], "crop_follow")

    # Test C: Malformed JSON and schema violations rejected
    def test_c_malformed_candidates_rejected(self):
        malformed = [
            {"title": "Missing start and end"},  # missing start_time, end_time
            {"start_time": "invalid", "end_time": 40.0, "title": "Bad float"},
            {"start_time": 10.0, "end_time": 50.0, "title": ""},  # empty title
            {"start_time": 10.0, "end_time": 50.0, "title": "Valid Clip", "virality_score": 250}  # virality > 100
        ]
        validated = validate_and_filter_candidates(malformed, segments=[], total_duration=100.0)
        self.assertEqual(len(validated), 0)

    # Test D: Word boundary snapping
    def test_d_word_boundary_snapping(self):
        segments = [
            {
                "start": 5.0,
                "end": 25.0,
                "text": "Hello world test",
                "words": [
                    {"word": "Hello", "start": 5.12, "end": 5.50},
                    {"word": "world", "start": 5.60, "end": 6.10},
                    {"word": "test", "start": 24.20, "end": 24.85}
                ]
            }
        ]
        snapped_start = snap_to_word_boundary(5.0, segments, prefer_start=True)
        self.assertEqual(snapped_start, 5.12)
        snapped_end = snap_to_word_boundary(25.0, segments, prefer_start=False)
        self.assertEqual(snapped_end, 24.85)

    # Test E: Strict 20.0s <= duration <= 90.0s constraint (REJECT, DO NOT FABRICATE)
    def test_e_duration_constraint_rejection(self):
        raw = [
            {  # 15s duration: Too short!
                "start_time": 10.0,
                "end_time": 25.0,
                "title": "Too Short Clip",
                "virality_score": 90
            },
            {  # 120s duration: Too long!
                "start_time": 0.0,
                "end_time": 120.0,
                "title": "Too Long Clip",
                "virality_score": 85
            },
            {  # 30s duration: Valid!
                "start_time": 30.0,
                "end_time": 60.0,
                "title": "Perfect Duration Clip",
                "virality_score": 80
            }
        ]
        validated = validate_and_filter_candidates(raw, segments=[], total_duration=200.0)
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0]["title"], "Perfect Duration Clip")
        self.assertEqual(validated[0]["duration"], 30.0)

    # Test F: Overlap deduplication (suppress IoU > 0.5, prefer higher virality)
    def test_f_overlap_deduplication(self):
        raw = [
            {
                "start_time": 10.0,
                "end_time": 50.0,
                "title": "Clip A - Lower Score",
                "virality_score": 70
            },
            {
                "start_time": 12.0,
                "end_time": 48.0,
                "title": "Clip B - Higher Score Overlapping",
                "virality_score": 95
            },
            {
                "start_time": 60.0,
                "end_time": 90.0,
                "title": "Clip C - Non Overlapping",
                "virality_score": 80
            }
        ]
        validated = validate_and_filter_candidates(raw, segments=[], total_duration=100.0)
        self.assertEqual(len(validated), 2)
        titles = [c["title"] for c in validated]
        self.assertIn("Clip B - Higher Score Overlapping", titles)
        self.assertIn("Clip C - Non Overlapping", titles)
        self.assertNotIn("Clip A - Lower Score", titles)

    # Test G: Negative feedback injection
    def test_g_negative_feedback_conditioning(self):
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=10.0,
            end_time=35.0,
            title="Boring Walkthrough",
            hook="Low energy opening",
            description="Testing rejections",
            hashtags="#Test",
            virality_score=40,
            layout_mode="fit_blur"
        )
        db.reject_clip(clip_id, reason="Boring code walkthrough without energy")
        rejections = db.get_all_rejections()
        self.assertGreater(len(rejections), 0)

        from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
        prompt_frag = get_negative_feedback_prompt()
        self.assertIn("Boring code walkthrough without energy", prompt_frag)
        self.assertIn("NEGATIVE USER PREFERENCES", prompt_frag)

    # Test H: Rate-limit (429/503/timeout) retry backoff
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_h_rate_limit_retry_backoff(self, mock_gemini):
        mock_gemini.side_effect = RuntimeError("Gemini model 'gemini-3.5-flash-lite' rate limited (HTTP 429)")

        video_id = self._seed_transcribed_video("vid_429_01")
        worker = HighlightWorker(poll_interval_seconds=1, base_backoff_seconds=10)

        with self.assertRaises(RuntimeError):
            worker.process_video_highlights(video_id)

        row = db.get_youtube_video(video_id)
        # Must reset to TRANSCRIBED with scheduled next_ai_attempt_at
        self.assertEqual(row["status"], "TRANSCRIBED")
        self.assertEqual(row["ai_attempt_count"], 1)
        self.assertIsNotNone(row["next_ai_attempt_at"])
        self.assertIn("HTTP 429", row["last_ai_error"])

    # Test I: Missing GEMINI_API_KEY transitions to permanent failure
    @patch("dispatch.ai_clips.worker.GEMINI_API_KEY", "")
    @patch("dispatch.ai_clips.highlight_finder.GEMINI_API_KEY", "")
    def test_i_missing_gemini_api_key_fails(self):
        video_id = self._seed_transcribed_video("vid_no_key_01")
        worker = HighlightWorker(poll_interval_seconds=1)

        with self.assertRaises(RuntimeError):
            worker.process_video_highlights(video_id)

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("GEMINI_API_KEY", row["last_ai_error"])

    # Test J: Zero fallback to local heuristic
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    @patch("dispatch.ai_clips.highlight_finder.extract_clips_local_heuristic")
    def test_j_zero_heuristic_fallback(self, mock_heuristic, mock_gemini):
        mock_gemini.side_effect = RuntimeError("All Gemini candidate models failed: HTTP 503")

        video_id = self._seed_transcribed_video("vid_no_heur_01")
        worker = HighlightWorker(poll_interval_seconds=1)

        with self.assertRaises(RuntimeError):
            worker.process_video_highlights(video_id)

        # Confirm heuristic was never called
        mock_heuristic.assert_not_called()

    # Test K: Restart idempotency: no duplicate clips on retry
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_k_restart_idempotency_no_duplicates(self, mock_gemini):
        mock_gemini.return_value = (
            [
                {
                    "start_time": 10.0,
                    "end_time": 45.0,
                    "title": "Idempotent Clip",
                    "virality_score": 85
                }
            ],
            "gemini-3.5-flash-lite"
        )

        video_id = self._seed_transcribed_video("vid_idem_01")
        worker = HighlightWorker(poll_interval_seconds=1)

        # Run 1
        worker.process_video_highlights(video_id)
        clips_1 = db.get_clips_for_review()
        self.assertEqual(len(clips_1), 1)

        # Simulate replay (e.g. video was set back to TRANSCRIBED and re-analyzed)
        db.update_youtube_video(video_id=video_id, status="TRANSCRIBED")
        worker.process_video_highlights(video_id)
        clips_2 = db.get_clips_for_review()
        # Must still be exactly 1 clip, not 2
        self.assertEqual(len(clips_2), 1)

    # Test L: Atomic transaction rollback on partial failure
    def test_l_atomic_transaction_rollback(self):
        clip_defs = [
            {"start_time": 10.0, "end_time": 40.0, "title": "Clip 1"},
            {"start_time": "INVALID_FLOAT", "end_time": 50.0, "title": "Clip 2"}
        ]
        video_id = self._seed_transcribed_video("vid_atomic_01")

        with self.assertRaises(Exception):
            db.save_clip_definitions(
                chunk_id="chk_dummy",
                session_id=None,
                video_id=video_id,
                clip_defs=clip_defs
            )

        # Verify nothing was inserted and status was not changed to CLIPS_DEFINED
        clips = db.get_clips_for_review()
        self.assertEqual(len(clips), 0)
        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "TRANSCRIBED")

    # Test M: Silence / zero speech -> CLIPS_DEFINED with 0 clips
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_m_silence_empty_transcript_defines_zero_clips(self, mock_gemini):
        video_id = self._seed_transcribed_video("vid_silence_01", segments=[])
        worker = HighlightWorker(poll_interval_seconds=1)
        res = worker.process_video_highlights(video_id)

        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "CLIPS_DEFINED")
        self.assertEqual(res["clips_count"], 0)

        # Gemini must not have been called
        mock_gemini.assert_not_called()

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "CLIPS_DEFINED")
        self.assertEqual(len(db.get_clips_for_review()), 0)

    # Test N: Invariants: unrendered media paths and status='ready_review'
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_n_unrendered_media_invariants(self, mock_gemini):
        mock_gemini.return_value = (
            [
                {
                    "start_time": 10.0,
                    "end_time": 40.0,
                    "title": "Unrendered Invariant Test",
                    "virality_score": 90
                }
            ],
            "gemini-3.5-flash-lite"
        )
        video_id = self._seed_transcribed_video("vid_inv_01")
        worker = HighlightWorker()
        worker.process_video_highlights(video_id)

        clips = db.get_clips_for_review()
        self.assertEqual(len(clips), 1)
        c = clips[0]
        self.assertEqual(c["status"], "ready_review")
        self.assertIsNone(c["video_path"])
        self.assertIsNone(c["thumbnail_path"])

    # Test O: Publishing outbox remains empty
    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    def test_o_publishing_outbox_untouched(self, mock_gemini):
        mock_gemini.return_value = (
            [
                {
                    "start_time": 10.0,
                    "end_time": 40.0,
                    "title": "Outbox Untouched Test",
                    "virality_score": 85
                }
            ],
            "gemini-3.5-flash-lite"
        )
        video_id = self._seed_transcribed_video("vid_outbox_01")
        worker = HighlightWorker()
        worker.process_video_highlights(video_id)

        outbox = db.get_outbox_queue()
        self.assertEqual(len(outbox), 0)


if __name__ == "__main__":
    unittest.main()
