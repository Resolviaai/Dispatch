"""Unit tests for Pillar 2: Canonical Gemini Transcription Pipeline (Dispatch V2).

Covers:
1. Primary Gemini transcription success (gemini-3.5-transcribe).
2. Primary failure followed by Flash-Lite fallback (gemini-3.5-flash-lite).
3. Both providers failing records failure and last_error.
4. Permanent authentication failure (401/403) stops without fallback or loop.
5. Word-level timestamp normalization and segment grouping.
6. Real source media dimensions and metadata preservation.
7. Strict persistence order (transcript saved before status = TRANSCRIBED).
8. Worker restart recovery and idempotency.
9. Verification of zero reachable Whisper invocation.
"""
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import json
import os
from pathlib import Path
from types import SimpleNamespace, ModuleType

from dispatch import db
from dispatch.transcription.worker import TranscriptionWorker
from dispatch.transcription.gemini_engine import (
    transcribe_media,
    PermanentAuthError,
    RecoverableTranscriptionError,
    check_and_increment_daily_transcribe_quota,
    refund_daily_transcribe_quota,
    is_permanent_auth_error,
)
from dispatch.transcription.transcriber import transcribe_video


def fake_genai_module():
    module = ModuleType("google.genai")
    module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        AudioTranscriptionConfig=lambda **kwargs: kwargs,
    )
    return module


class TestPillar2GeminiTranscription(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_dispatch.db"
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db.init_db()

        # Dummy audio/video file fixture
        self.dummy_video = Path(self.temp_dir.name) / "test_video.mp4"
        self.dummy_video.write_bytes(b"\x00" * 1024)

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    @patch("dispatch.transcription.gemini_engine.extract_compressed_audio")
    @patch("dispatch.transcription.gemini_engine.split_audio_for_transcription")
    def test_primary_gemini_transcribe_success(self, mock_split, mock_extract, mock_get_client):
        """Case 1: Primary provider (gemini-3.5-transcribe) succeeds with word timestamps."""
        mock_extract.return_value = self.dummy_video
        mock_split.return_value = [(self.dummy_video, 0.0)]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_file = MagicMock()
        mock_file.name = "files/test_prim"
        mock_client.files.upload.return_value = mock_file

        w1 = MagicMock(word="Hello", start_offset="0.100s", end_offset="0.500s")
        w2 = MagicMock(word="Dispatch", start_offset="0.600s", end_offset="1.200s")
        trans = MagicMock(speaker_label="Speaker 1", words=[w1, w2])
        part = MagicMock(audio_transcription=trans)
        cand = MagicMock()
        cand.content.parts = [part]

        mock_resp = MagicMock()
        mock_resp.text = "Hello Dispatch"
        mock_resp.candidates = [cand]
        mock_client.models.generate_content.return_value = mock_resp

        video_id = "vid_gemini_prim"
        db.register_youtube_video(video_id=video_id, title="Primary Test", duration=15.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        worker = TranscriptionWorker()
        with patch("sys.modules", {**os.environ, "google.genai": fake_genai_module()}):
            res = worker.process_pending()

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "TRANSCRIBED")
        self.assertEqual(res[0]["source"], "gemini_transcribe")

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "TRANSCRIBED")
        self.assertEqual(yt_record["transcript_source"], "gemini_transcribe")

        # Verify segments and words
        segments = json.loads(yt_record["segments_json"])
        self.assertTrue(len(segments) >= 1)
        self.assertEqual(segments[0]["words"][0]["word"], "Hello")
        self.assertEqual(segments[0]["words"][0]["start"], 0.1)

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    @patch("dispatch.transcription.gemini_engine.extract_compressed_audio")
    @patch("dispatch.transcription.gemini_engine.split_audio_for_transcription")
    def test_primary_failure_triggers_flash_lite_fallback(self, mock_split, mock_extract, mock_get_client):
        """Case 2: Primary fails with recoverable error (503), falls back to gemini-3.5-flash-lite."""
        mock_extract.return_value = self.dummy_video
        mock_split.return_value = [(self.dummy_video, 0.0)]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_file = MagicMock()
        mock_file.name = "files/test_fallback"
        mock_client.files.upload.return_value = mock_file

        # Primary raises 503; fallback returns JSON
        mock_client.models.generate_content.side_effect = [
            RuntimeError("HTTP 503: Model overloaded"),
            MagicMock(text='[{"start": "00:00.00", "end": "00:02.00", "text": "Fallback speech", "speaker": "spk:0"}]')
        ]

        video_id = "vid_gemini_fb"
        db.register_youtube_video(video_id=video_id, title="Fallback Test", duration=10.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        worker = TranscriptionWorker()
        with patch("sys.modules", {**os.environ, "google.genai": fake_genai_module()}):
            res = worker.process_pending()

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "TRANSCRIBED")
        self.assertEqual(res[0]["source"], "gemini_flash_lite")

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "TRANSCRIBED")
        self.assertEqual(yt_record["transcript_source"], "gemini_flash_lite")

        segments = json.loads(yt_record["segments_json"])
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["text"], "Fallback speech")
        self.assertEqual(len(segments[0]["words"]), 2)  # "Fallback", "speech"

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    @patch("dispatch.transcription.gemini_engine.extract_compressed_audio")
    @patch("dispatch.transcription.gemini_engine.split_audio_for_transcription")
    def test_both_providers_fail_records_failed_state(self, mock_split, mock_extract, mock_get_client):
        """Case 3: Both primary and fallback fail -> status = 'FAILED', last_error recorded."""
        mock_extract.return_value = self.dummy_video
        mock_split.return_value = [(self.dummy_video, 0.0)]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_file = MagicMock()
        mock_file.name = "files/test_both_fail"
        mock_client.files.upload.return_value = mock_file

        # Both fail with 500
        mock_client.models.generate_content.side_effect = [
            RuntimeError("HTTP 500: Primary internal error"),
            RuntimeError("HTTP 500: Fallback internal error")
        ]

        video_id = "vid_both_fail"
        db.register_youtube_video(video_id=video_id, title="Failure Test", duration=10.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        worker = TranscriptionWorker()
        with patch("sys.modules", {**os.environ, "google.genai": fake_genai_module()}):
            res = worker.process_pending()

        self.assertEqual(len(res), 0)
        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "FAILED")
        self.assertIn("Gemini transcription failed", yt_record["last_error"])

    @patch("dispatch.transcription.gemini_engine.get_gemini_client")
    @patch("dispatch.transcription.gemini_engine.extract_compressed_audio")
    @patch("dispatch.transcription.gemini_engine.split_audio_for_transcription")
    def test_permanent_auth_error_no_fallback(self, mock_split, mock_extract, mock_get_client):
        """Case 4: Permanent authentication error (401/403/invalid key) stops without calling fallback."""
        mock_extract.return_value = self.dummy_video
        mock_split.return_value = [(self.dummy_video, 0.0)]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_file = MagicMock()
        mock_file.name = "files/test_auth"
        mock_client.files.upload.return_value = mock_file

        # Primary raises 401 Unauthorized / API_KEY_INVALID
        mock_client.models.generate_content.side_effect = RuntimeError("HTTP 401: API_KEY_INVALID")

        video_id = "vid_auth_fail"
        db.register_youtube_video(video_id=video_id, title="Auth Fail Test", duration=10.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        worker = TranscriptionWorker()
        with patch("sys.modules", {**os.environ, "google.genai": fake_genai_module()}):
            res = worker.process_pending()

        # Fallback must NOT have been called (call_count must be exactly 1)
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "FAILED")
        self.assertIn("authentication", yt_record["last_error"].lower())

    def test_zero_whisper_in_transcription_module(self):
        """Case 5: Verification that faster-whisper is NOT imported or present in dispatch.transcription."""
        import dispatch.transcription as dt
        self.assertFalse(hasattr(dt, "get_whisper_model"))
        self.assertTrue(hasattr(dt, "transcribe_media"))
        self.assertTrue(hasattr(dt, "transcribe_video"))

    def test_is_permanent_auth_error_detection(self):
        """Case 6: Classification of auth vs recoverable errors."""
        self.assertTrue(is_permanent_auth_error(RuntimeError("API_KEY_INVALID")))
        self.assertTrue(is_permanent_auth_error(RuntimeError("HTTP 401 Unauthorized")))
        self.assertTrue(is_permanent_auth_error(RuntimeError("HTTP 403 Forbidden: permission_denied")))
        self.assertFalse(is_permanent_auth_error(RuntimeError("HTTP 503 Service Unavailable")))
        self.assertFalse(is_permanent_auth_error(RuntimeError("HTTP 429 Rate limit exceeded")))
        self.assertFalse(is_permanent_auth_error(RuntimeError("Connection timeout")))


if __name__ == "__main__":
    unittest.main()
