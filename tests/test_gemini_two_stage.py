"""Unit tests for Gemini Two-Stage Transcription Engine."""
import os
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from dispatch.transcription.gemini_engine import (
    _parse_offset_to_seconds,
    check_and_increment_daily_transcribe_quota,
    transcribe_stage1_flash_lite,
    transcribe_stage2_precision_words,
)
from dispatch import db


class TestGeminiTwoStage(unittest.TestCase):

    def test_parse_offset_to_seconds(self):
        self.assertEqual(_parse_offset_to_seconds("0.100s"), 0.1)
        self.assertEqual(_parse_offset_to_seconds("2.450s"), 2.45)
        self.assertEqual(_parse_offset_to_seconds("15s"), 15.0)
        self.assertEqual(_parse_offset_to_seconds(3.5), 3.5)
        self.assertEqual(_parse_offset_to_seconds(None), 0.0)

    def test_daily_transcribe_quota_tracking(self):
        # Reset test state
        db.set_setting("transcribe_quota_date", "2026-01-01")
        db.set_setting("transcribe_quota_count", "0")

        # First 5 calls pass with limit=5
        for i in range(1, 6):
            self.assertTrue(check_and_increment_daily_transcribe_quota(max_daily_requests=5))

        # 6th call exceeds limit
        self.assertFalse(check_and_increment_daily_transcribe_quota(max_daily_requests=5))

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    def test_transcribe_stage1_flash_lite(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_file = MagicMock()
        mock_file.name = "files/test1234"
        mock_client.files.upload.return_value = mock_file

        mock_resp = MagicMock()
        mock_resp.text = '[{"start": "00:01.00", "end": "00:04.50", "text": "Testing hook", "speaker": "Speaker 1"}]'
        mock_client.models.generate_content.return_value = mock_resp

        # Create dummy audio file
        test_file = Path("storage/test_dummy.mp3")
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"dummy")

        try:
            segments = transcribe_stage1_flash_lite(test_file)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["start"], 1.0)
            self.assertEqual(segments[0]["end"], 4.5)
            self.assertEqual(segments[0]["text"], "Testing hook")
        finally:
            if test_file.exists():
                test_file.unlink()

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    def test_transcribe_stage2_precision_words(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_file = MagicMock()
        mock_file.name = "files/test5678"
        mock_client.files.upload.return_value = mock_file

        # Mock WordInfo
        w1 = MagicMock()
        w1.word = "Hello"
        w1.start_offset = "0.200s"
        w1.end_offset = "0.500s"

        w2 = MagicMock()
        w2.word = "world"
        w2.start_offset = "0.600s"
        w2.end_offset = "1.100s"

        part = MagicMock()
        trans = MagicMock()
        trans.speaker_label = "spk:0"
        trans.words = [w1, w2]
        part.audio_transcription = trans

        cand = MagicMock()
        cand.content.parts = [part]

        mock_resp = MagicMock()
        mock_resp.text = "Hello world"
        mock_resp.candidates = [cand]
        mock_client.models.generate_content.return_value = mock_resp

        test_file = Path("storage/test_dummy_clip.mp3")
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_bytes(b"dummy")

        try:
            result = transcribe_stage2_precision_words(test_file)
            self.assertEqual(result["full_text"], "Hello world")
            self.assertEqual(len(result["words"]), 2)
            self.assertEqual(result["words"][0]["word"], "Hello")
            self.assertEqual(result["words"][0]["start"], 0.2)
            self.assertEqual(result["speech_start"], 0.2)
            self.assertEqual(result["speech_end"], 1.1)
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_transcription_queue_stats_and_backlog(self):
        # Insert a queued_transcription clip directly into SQLite
        clip_id = "test_queued_clip_999"
        with db.get_db_connection() as conn:
            conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            conn.cursor().execute("""
                INSERT INTO clips (id, start_time, end_time, padded_start, padded_end, duration, title, hook, virality_score, status)
                VALUES (?, 10.0, 40.0, 6.0, 44.0, 30.0, 'Test Clip', 'Test Hook', 85, 'queued_transcription')
            """, (clip_id,))
            conn.commit()

        try:
            queued = db.get_queued_transcription_clips(limit=10)
            self.assertTrue(any(c["id"] == clip_id for c in queued))

            stats = db.get_transcription_queue_stats()
            self.assertGreaterEqual(stats["queued_count"], 1)
            self.assertEqual(stats["daily_quota_max"], 25)
        finally:
            with db.get_db_connection() as conn:
                conn.cursor().execute("DELETE FROM clips WHERE id = ?", (clip_id,))
                conn.commit()

    @patch("dispatch.transcription.gemini_engine.check_and_increment_daily_transcribe_quota")
    def test_drain_queued_transcriptions_respects_quota(self, mock_quota):
        # When quota is exhausted, drain stops immediately
        mock_quota.return_value = False
        from dispatch.transcription.gemini_engine import drain_queued_transcriptions
        drained = drain_queued_transcriptions(max_batch=5)
        self.assertEqual(drained, 0)


if __name__ == "__main__":
    unittest.main()
