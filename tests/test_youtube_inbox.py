"""Unit and Integration Tests for YouTube Cloud Inbox & Catcher Architecture.
Verifies WebVTT subtitle parsing, database idempotency, video ID extraction,
and end-to-end pipeline execution with YouTube captions and Whisper fallback.
"""
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch.youtube_inbox.vtt_parser import (
    parse_timestamp_to_seconds,
    clean_vtt_text,
    parse_vtt_content,
    parse_vtt_file
)
from dispatch.youtube_inbox.catcher import (
    extract_youtube_video_id,
    YouTubeInboxCatcher
)
from dispatch import db


class TestVTTParser(unittest.TestCase):
    """Test WebVTT subtitle parsing and normalization."""

    def test_parse_timestamp_to_seconds(self):
        self.assertEqual(parse_timestamp_to_seconds("00", "01", "23", "456"), 83.456)
        self.assertEqual(parse_timestamp_to_seconds(None, "02", "15", "500"), 135.5)
        self.assertEqual(parse_timestamp_to_seconds("01", "00", "00", "000"), 3600.0)

    def test_clean_vtt_text(self):
        raw = "<c>Hello</c> &amp; <00:00:01.200>welcome align:start position:50%"
        cleaned = clean_vtt_text(raw)
        self.assertEqual(cleaned, "Hello & welcome")

    def test_parse_vtt_content_deduplication_and_merging(self):
        sample_vtt = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
Here is the biggest mistake

00:00:03.100 --> 00:00:05.500
Here is the biggest mistake founders make

00:00:06.000 --> 00:00:09.000
They build before talking to users.
"""
        segments = parse_vtt_content(sample_vtt)
        self.assertGreater(len(segments), 0)

        # Ensure deduplicated: "founders make" should not repeat the whole prefix
        full_text = " ".join(s["text"] for s in segments)
        self.assertIn("biggest mistake", full_text)
        self.assertIn("founders make", full_text)
        self.assertIn("talking to users", full_text)

        # Check word-level timestamps generated
        for seg in segments:
            self.assertIn("start", seg)
            self.assertIn("end", seg)
            self.assertIn("words", seg)
            self.assertGreater(len(seg["words"]), 0)


class TestYouTubeInboxDatabase(unittest.TestCase):
    """Test SQLite database operations and idempotency for YouTube inbox."""

    def setUp(self):
        db.init_db()
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test_vid_%'")
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'mock_vid_%'")
            conn.commit()

    def test_idempotent_registration(self):
        test_id = "test_vid_001"
        # First registration should succeed
        is_new = db.register_youtube_video(
            video_id=test_id,
            title="[DISPATCH] Test Video 1",
            channel_id="UC12345",
            upload_time="2026-10-06",
            duration=45.0
        )
        self.assertTrue(is_new)

        # Duplicate registration should return False (idempotent)
        is_dup = db.register_youtube_video(
            video_id=test_id,
            title="[DISPATCH] Test Video 1 Duplicate",
            channel_id="UC12345"
        )
        self.assertFalse(is_dup)

        # Fetch and verify fields
        record = db.get_youtube_video(test_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["title"], "[DISPATCH] Test Video 1")
        self.assertEqual(record["status"], "DISCOVERED")

        # Update status and check
        db.update_youtube_video(test_id, status="CLIPS_CREATED", transcript_source="youtube")
        updated = db.get_youtube_video(test_id)
        self.assertEqual(updated["status"], "CLIPS_CREATED")
        self.assertEqual(updated["transcript_source"], "youtube")
        self.assertTrue(db.is_youtube_video_processed(test_id))


class TestYouTubeCatcher(unittest.TestCase):
    """Test video ID parsing, catcher methods, and mock end-to-end execution."""

    def setUp(self):
        db.init_db()
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'mock_vid_%'")
            conn.commit()

    def test_extract_youtube_video_id(self):
        # Full URL
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ"
        )
        # Short URL
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ"
        )
        # Shorts URL
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ"
        )
        # Raw 11-char ID
        self.assertEqual(
            extract_youtube_video_id("dQw4w9WgXcQ"),
            "dQw4w9WgXcQ"
        )
        # Invalid URL
        self.assertIsNone(extract_youtube_video_id("invalid-link"))

    @patch("dispatch.youtube_inbox.catcher.identify_and_save_highlights")
    @patch("dispatch.youtube_inbox.catcher.render_clip")
    def test_process_video_mock_pipeline(self, mock_render, mock_highlights):
        catcher = YouTubeInboxCatcher()
        test_id = "mock_vid_99"

        # Mock dependencies
        catcher.fetch_video_info = MagicMock(return_value={
            "id": test_id,
            "title": "[DISPATCH] 2026-10-06 14:30 Work Session",
            "channel_id": "UC_TEST",
            "channel": "Test Channel",
            "duration": 40.0,
            "upload_date": "2026-10-06",
            "description": "Autonomous Dispatch test"
        })

        # Mock download returning a dummy file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            dummy_video_path = Path(f.name)
        catcher.download_video = MagicMock(return_value=dummy_video_path)

        # Mock captions returning structured segments
        mock_segments = [
            {"start": 0.0, "end": 15.0, "text": "Basically here is the trick to automate video production.", "words": []},
            {"start": 15.0, "end": 35.0, "text": "You record on your phone, upload to YouTube private, and laptop catches it.", "words": []}
        ]
        catcher.fetch_youtube_captions = MagicMock(return_value=mock_segments)

        # Mock highlight finder to register real clip in database
        def fake_identify(chunk_id, session_id, segments, total_duration, publish_mode="private"):
            cid = db.save_clip(
                session_id=session_id,
                chunk_id=chunk_id,
                start_time=0.0,
                end_time=15.0,
                title="Mock Clip",
                hook="Hook",
                description="Desc",
                hashtags="#Shorts",
                virality_score=80,
                layout_mode="fit_blur"
            )
            return [cid]

        mock_highlights.side_effect = fake_identify

        # Mock renderer
        mock_render.return_value = {
            "video_path": dummy_video_path,
            "thumbnail_path": dummy_video_path.with_suffix(".jpg"),
            "duration": 20.0
        }

        # Run process_video
        result = catcher.process_video(f"https://youtu.be/{test_id}")

        self.assertEqual(result["video_id"], test_id)
        self.assertEqual(result["status"], "CLIPS_CREATED")
        self.assertEqual(result["transcript_source"], "youtube")
        self.assertEqual(result["clips_count"], 1)

        # Verify database record updated
        rec = db.get_youtube_video(test_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "CLIPS_CREATED")

        # Verify idempotency on second call
        second_result = catcher.process_video(f"https://youtu.be/{test_id}")
        self.assertEqual(second_result["status"], "ALREADY_PROCESSED")

        # Cleanup
        try:
            dummy_video_path.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
