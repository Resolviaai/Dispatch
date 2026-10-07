"""Unit tests for Pillar 2: Transcription Worker, VTT Parsing, and State Transitions.
Tests:
Case A: YouTube Captions available immediately.
Case B: YouTube Captions unavailable, within wait window -> remains WAITING_FOR_TRANSCRIPT.
Case C: Wait deadline expires -> triggers Whisper fallback -> TRANSCRIBED (source: 'whisper').
Case D: Whisper engine fails -> status = 'FAILED', last_error populated, retry_count incremented.
Case E: Word timestamp exactness flag (is_exact True vs False).
Case F: Worker restart recovery.
Case G: Zero speech / empty transcript handling.
Case H: Database transcript idempotency per chunk_id.
Case I: Strict ordering (transcript saved before TRANSCRIBED status, failure leaves status unchanged).
Case J: Real source media dimensions preserved in chunk (no invented 1080x1920).
Case K: Caption reprobe backoff rate limiting & next probe scheduling surviving restart.
Case L: 4-hour (14400s) default caption wait window & environment variable override.
Case M: Caption language restriction (subtitleslangs with en/hi preference).
"""
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import json
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dispatch import db
from dispatch.transcription.worker import TranscriptionWorker
from dispatch.youtube_inbox.vtt_parser import parse_vtt_content, parse_inline_words
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher


class TestPillar2Transcription(unittest.TestCase):

    def setUp(self):
        # Use an isolated temporary database for every test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_dispatch.db"
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db.init_db()

        # Dummy video file
        self.dummy_video = Path(self.temp_dir.name) / "test_video.mp4"
        self.dummy_video.write_bytes(b"\x00" * 1024)

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_case_a_youtube_captions_immediately_available(self):
        """Case A: YouTube captions available immediately -> TRANSCRIBED (source='youtube')."""
        video_id = "vid_test_a"
        db.register_youtube_video(video_id=video_id, title="Test A", duration=60.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = [
            {
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world from captions",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 1.0, "probability": 1.0, "is_exact": True},
                    {"word": "world", "start": 1.0, "end": 1.8, "probability": 1.0, "is_exact": True},
                    {"word": "from", "start": 1.8, "end": 2.1, "probability": 1.0, "is_exact": True},
                    {"word": "captions", "start": 2.1, "end": 2.5, "probability": 1.0, "is_exact": True}
                ]
            }
        ]

        worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
        res = worker.process_pending()

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "TRANSCRIBED")
        self.assertEqual(res[0]["source"], "youtube")
        self.assertEqual(res[0]["segments_count"], 1)

        # Verify DB state
        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "TRANSCRIBED")
        self.assertEqual(yt_record["transcript_source"], "youtube")
        self.assertIsNotNone(yt_record["transcript_wait_deadline"])

        # Verify chunk and transcript table
        chunk = db.get_chunk_by_file_hash(f"yt_{video_id}")
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk["status"], "transcribed")

    def test_case_b_captions_unavailable_within_wait_window(self):
        """Case B: Captions unavailable, within wait window -> remains WAITING_FOR_TRANSCRIPT."""
        video_id = "vid_test_b"
        db.register_youtube_video(video_id=video_id, title="Test B", duration=60.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None  # Not yet ready

        worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
        res = worker.process_pending()

        # Should NOT be finalized yet
        self.assertEqual(len(res), 0)

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "WAITING_FOR_TRANSCRIPT")
        self.assertIsNone(yt_record["transcript_source"])

    def test_case_c_wait_deadline_expires_whisper_fallback(self):
        """Case C: Wait deadline expires -> triggers Whisper fallback -> TRANSCRIBED (source='whisper')."""
        video_id = "vid_test_c"
        past_deadline = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        db.register_youtube_video(video_id=video_id, title="Test C", duration=60.0)
        db.update_youtube_video(
            video_id=video_id,
            status="WAITING_FOR_TRANSCRIPT",
            local_video_path=str(self.dummy_video),
            transcript_wait_deadline=past_deadline
        )

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None  # Still unavailable

        fake_whisper_res = {
            "transcript_id": "tx_test_123",
            "full_text": "This is local whisper speaking",
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "text": "This is local whisper speaking",
                    "words": [{"word": "This", "start": 0.0, "end": 0.5, "probability": 0.95}]
                }
            ],
            "language": "en",
            "language_probability": 0.99
        }

        with patch("dispatch.transcription.worker.transcribe_video", return_value=fake_whisper_res) as mock_whisper:
            worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
            res = worker.process_pending()

            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["status"], "TRANSCRIBED")
            self.assertEqual(res[0]["source"], "whisper")
            mock_whisper.assert_called_once_with(video_path=self.dummy_video)

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "TRANSCRIBED")
        self.assertEqual(yt_record["transcript_source"], "whisper")

    def test_case_d_whisper_engine_failure_marks_failed(self):
        """Case D: Whisper engine fails -> status = 'FAILED', last_error recorded, retry_count incremented."""
        video_id = "vid_test_d"
        past_deadline = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        db.register_youtube_video(video_id=video_id, title="Test D", duration=60.0)
        db.update_youtube_video(
            video_id=video_id,
            status="WAITING_FOR_TRANSCRIPT",
            local_video_path=str(self.dummy_video),
            transcript_wait_deadline=past_deadline
        )

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None

        with patch("dispatch.transcription.worker.transcribe_video", side_effect=RuntimeError("Whisper OOM")):
            worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
            try:
                worker.process_pending()
            except RuntimeError:
                pass

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "FAILED")
        self.assertIn("Whisper OOM", yt_record["last_error"])
        self.assertEqual(yt_record["retry_count"], 1)

    def test_case_e_word_timestamp_exactness_flags(self):
        """Case E: Word timestamp exactness flag (is_exact True for inline VTT, False for synthesized)."""
        # 1. Exact inline VTT tags
        vtt_exact = """WEBVTT

00:00:00.000 --> 00:00:02.000
First <00:00:01.000><c> second</c>
"""
        segs_exact = parse_vtt_content(vtt_exact)
        self.assertEqual(len(segs_exact), 1)
        words_exact = segs_exact[0]["words"]
        self.assertEqual(len(words_exact), 2)
        self.assertTrue(words_exact[0]["is_exact"])
        self.assertTrue(words_exact[1]["is_exact"])

        # 2. Plain VTT without inline tags (synthesized)
        vtt_plain = """WEBVTT

00:00:00.000 --> 00:00:02.000
Plain words here
"""
        segs_plain = parse_vtt_content(vtt_plain)
        self.assertEqual(len(segs_plain), 1)
        words_plain = segs_plain[0]["words"]
        self.assertEqual(len(words_plain), 3)
        self.assertFalse(words_plain[0]["is_exact"])
        self.assertFalse(words_plain[1]["is_exact"])
        self.assertFalse(words_plain[2]["is_exact"])

    def test_case_f_worker_restart_recovery(self):
        """Case F: Worker restart picks up existing WAITING_FOR_TRANSCRIPT and DOWNLOADED records."""
        # Record 1 in DOWNLOADED
        db.register_youtube_video(video_id="vid_f1", title="F1", duration=30.0)
        db.update_youtube_video(video_id="vid_f1", status="DOWNLOADED", local_video_path=str(self.dummy_video))

        # Record 2 in WAITING_FOR_TRANSCRIPT with deadline passed
        past_deadline = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.register_youtube_video(video_id="vid_f2", title="F2", duration=45.0)
        db.update_youtube_video(
            video_id="vid_f2",
            status="WAITING_FOR_TRANSCRIPT",
            local_video_path=str(self.dummy_video),
            transcript_wait_deadline=past_deadline
        )

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None

        fake_whisper = {
            "full_text": "whisper output",
            "segments": [{"start": 0.0, "end": 2.0, "text": "whisper output", "words": []}]
        }

        with patch("dispatch.transcription.worker.transcribe_video", return_value=fake_whisper):
            worker = TranscriptionWorker(caption_wait_seconds=600, catcher=mock_catcher)
            res = worker.process_pending()

            # f1 should have entered WAITING_FOR_TRANSCRIPT (not finished)
            # f2 should have finished via Whisper fallback
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["video_id"], "vid_f2")
            self.assertEqual(res[0]["source"], "whisper")

        self.assertEqual(db.get_youtube_video("vid_f1")["status"], "WAITING_FOR_TRANSCRIPT")
        self.assertEqual(db.get_youtube_video("vid_f2")["status"], "TRANSCRIBED")

    def test_case_g_genuine_silence_zero_speech(self):
        """Case G: Genuine silence produces TRANSCRIBED with 0 segments and does NOT fail."""
        video_id = "vid_silence"
        past_deadline = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        db.register_youtube_video(video_id=video_id, title="Silence Video", duration=60.0)
        db.update_youtube_video(
            video_id=video_id,
            status="WAITING_FOR_TRANSCRIPT",
            local_video_path=str(self.dummy_video),
            transcript_wait_deadline=past_deadline
        )

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None

        fake_whisper_empty = {
            "transcript_id": "tx_empty",
            "full_text": "",
            "segments": [],
            "language": "unknown",
            "language_probability": 0.0
        }

        with patch("dispatch.transcription.worker.transcribe_video", return_value=fake_whisper_empty):
            worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
            res = worker.process_pending()

            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["status"], "TRANSCRIBED")
            self.assertEqual(res[0]["segments_count"], 0)
            self.assertEqual(res[0]["words_count"], 0)

        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "TRANSCRIBED")
        self.assertEqual(yt_record["transcript_source"], "whisper")

    def test_case_h_idempotent_save_transcript(self):
        """Case H: save_transcript replaces existing records for chunk_id without duplicate rows."""
        chunk_id = db.register_chunk(session_id=None, filename="dummy.mp4", filepath=str(self.dummy_video), file_hash="hash_h")
        tx1 = db.save_transcript(chunk_id=chunk_id, session_id=None, full_text="First", segments=[])
        tx2 = db.save_transcript(chunk_id=chunk_id, session_id=None, full_text="Second", segments=[])

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            cnt = cursor.fetchone()["cnt"]
            self.assertEqual(cnt, 1)

            cursor.execute("SELECT full_text FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            row = cursor.fetchone()
            self.assertEqual(row["full_text"], "Second")

    def test_case_i_strict_transcription_persistence_order(self):
        """Case I: Transcript is persisted before status=TRANSCRIBED. If save fails, status MUST NOT be TRANSCRIBED."""
        video_id = "vid_test_i"
        db.register_youtube_video(video_id=video_id, title="Test I", duration=60.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = [
            {"start": 0.0, "end": 2.0, "text": "Valid speech", "words": []}
        ]

        # Simulate database failure during save_transcript
        with patch("dispatch.db.save_transcript", side_effect=RuntimeError("Disk full")):
            worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
            try:
                worker.process_pending()
            except RuntimeError:
                pass

        # Video must NOT be TRANSCRIBED
        yt_record = db.get_youtube_video(video_id)
        self.assertEqual(yt_record["status"], "FAILED")
        self.assertIn("Disk full", yt_record["last_error"])

    def test_case_j_preserves_real_source_media_metadata(self):
        """Case J: Pillar 2 preserves real source dimensions (e.g. 360x640) and never invents 1080x1920."""
        video_id = "vid_test_j"
        db.register_youtube_video(video_id=video_id, title="Test J", duration=600.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = [
            {"start": 0.0, "end": 2.0, "text": "Valid speech", "words": []}
        ]

        # Mock probe_video returning real 360x640 mobile dimensions
        real_meta = {
            "duration": 600.05,
            "width": 360,
            "height": 640,
            "aspect_ratio": "9:16",
            "has_audio": True
        }
        with patch("dispatch.ingestion.validator.probe_video", return_value=(True, real_meta, None)):
            worker = TranscriptionWorker(caption_wait_seconds=900, catcher=mock_catcher)
            res = worker.process_pending()
            self.assertEqual(len(res), 1)

        chunk = db.get_chunk_by_file_hash(f"yt_{video_id}")
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk["width"], 360)
        self.assertEqual(chunk["height"], 640)
        self.assertEqual(chunk["aspect_ratio"], "9:16")
        self.assertEqual(chunk["status"], "transcribed")

    def test_case_k_caption_reprobe_backoff_and_restart(self):
        """Case K: 15s worker loop respects 300s reprobe backoff; persisted next_probe timestamp survives restart."""
        video_id = "vid_test_k"
        db.register_youtube_video(video_id=video_id, title="Test K", duration=60.0)
        db.update_youtube_video(video_id=video_id, status="DOWNLOADED", local_video_path=str(self.dummy_video))

        mock_catcher = MagicMock()
        mock_catcher.fetch_youtube_captions.return_value = None  # unavailable

        # Cycle 1: Enters WAITING_FOR_TRANSCRIPT and probes once
        worker = TranscriptionWorker(caption_wait_seconds=14400, caption_reprobe_seconds=300, catcher=mock_catcher)
        worker.process_pending()
        self.assertEqual(mock_catcher.fetch_youtube_captions.call_count, 1)

        rec = db.get_youtube_video(video_id)
        self.assertEqual(rec["status"], "WAITING_FOR_TRANSCRIPT")
        next_probe_1 = rec["next_caption_probe_at"]
        self.assertIsNotNone(next_probe_1)

        # Cycle 2: Immediate follow-up cycle within 300s backoff must NOT call fetch_youtube_captions again
        worker.process_pending()
        self.assertEqual(mock_catcher.fetch_youtube_captions.call_count, 1)

        # Simulate daemon restart by instantiating new worker instance
        worker_restarted = TranscriptionWorker(caption_wait_seconds=14400, caption_reprobe_seconds=300, catcher=mock_catcher)
        worker_restarted.process_pending()
        # Still within 300s window -> no new YouTube probe
        self.assertEqual(mock_catcher.fetch_youtube_captions.call_count, 1)

        # Fast forward next_caption_probe_at into the past
        past_probe = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        db.update_youtube_video(video_id=video_id, status="WAITING_FOR_TRANSCRIPT", next_caption_probe_at=past_probe)

        # Cycle 3: Now probe is due -> must call fetch_youtube_captions
        worker_restarted.process_pending()
        self.assertEqual(mock_catcher.fetch_youtube_captions.call_count, 2)

    def test_case_l_caption_wait_defaults_and_env_override(self):
        """Case L: Default wait window is 4 hours (14400s), overridable via DISPATCH_TRANSCRIPT_WAIT_SECONDS."""
        w_default = TranscriptionWorker()
        self.assertEqual(w_default.caption_wait_seconds, 14400)
        self.assertEqual(w_default.caption_reprobe_seconds, 300)

        with patch.dict(os.environ, {"DISPATCH_TRANSCRIPT_WAIT_SECONDS": "7200", "DISPATCH_CAPTION_REPROBE_SECONDS": "180"}):
            w_env = TranscriptionWorker()
            self.assertEqual(w_env.caption_wait_seconds, 7200)
            self.assertEqual(w_env.caption_reprobe_seconds, 180)

    def test_case_m_caption_language_restriction(self):
        """Case M: fetch_youtube_captions restricts subtitleslangs to en/hi variants."""
        catcher = YouTubeInboxCatcher()
        captured_opts = {}

        def mock_ydl_init(opts):
            nonlocal captured_opts
            captured_opts = opts
            mock_inst = MagicMock()
            mock_inst.__enter__.return_value = mock_inst
            mock_inst.download.return_value = 0
            return mock_inst

        with patch("yt_dlp.YoutubeDL", side_effect=mock_ydl_init):
            catcher.fetch_youtube_captions("mock_vid_123")

        self.assertIn("subtitleslangs", captured_opts)
        langs = captured_opts["subtitleslangs"]
        self.assertTrue(any("en" in l for l in langs))
        self.assertTrue(any("hi" in l for l in langs))


if __name__ == "__main__":
    unittest.main()
