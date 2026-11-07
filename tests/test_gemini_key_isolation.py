"""Unit tests for Per-User Gemini Key Boundary Isolation.

Verifies:
1. User-owned jobs (user_id non-empty) NEVER fall back to PC host GEMINI_API_KEY.
2. Missing vaulted key fails closed and records status FAILED.
3. Standalone / unauthenticated operator jobs safely use host GEMINI_API_KEY.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch import db
from dispatch.transcription.worker import TranscriptionWorker
from dispatch.ai_clips.worker import HighlightWorker


class TestGeminiKeyIsolation(unittest.TestCase):
    def setUp(self):
        db.init_db()
        os.environ["GEMINI_API_KEY"] = "host_pc_secret_key_12345"

        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-gemini-%'")
            conn.commit()

    def tearDown(self):
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-gemini-%'")
            conn.commit()

    # -------------------------------------------------------------------------
    # 1. Transcription Worker Isolation
    # -------------------------------------------------------------------------

    @patch("dispatch.youtube_connections.get_user_gemini_key", return_value="vaulted_user_key_abc")
    def test_transcription_worker_uses_vaulted_key_for_user_job(self, mock_get_key):
        worker = TranscriptionWorker()
        item = {"video_id": "test-gemini-1", "user_id": "creator_user_1"}
        resolved_key = worker._resolve_api_key_for_item(item)
        self.assertEqual(resolved_key, "vaulted_user_key_abc")
        mock_get_key.assert_called_once_with("creator_user_1")

    @patch("dispatch.youtube_connections.get_user_gemini_key", return_value="")
    def test_transcription_worker_refuses_host_key_fallback_for_user_job(self, mock_get_key):
        worker = TranscriptionWorker()
        item = {"video_id": "test-gemini-2", "user_id": "unvaulted_user"}
        with self.assertRaises(RuntimeError) as ctx:
            worker._resolve_api_key_for_item(item)
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", str(ctx.exception))

    def test_transcription_worker_uses_host_key_for_standalone_operator(self):
        worker = TranscriptionWorker()
        item = {"video_id": "test-gemini-3", "user_id": None}
        resolved_key = worker._resolve_api_key_for_item(item)
        self.assertEqual(resolved_key, "host_pc_secret_key_12345")

    @patch("dispatch.youtube_connections.get_user_gemini_key", return_value="")
    def test_transcription_process_item_fails_closed_on_missing_user_key(self, mock_get_key):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tf.write(b"dummy video data")
            tf_path = tf.name

        try:
            video_id = "test-gemini-4"
            with db.get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO youtube_inbox (video_id, title, status, user_id, local_video_path)
                    VALUES (?, ?, 'DOWNLOADED', ?, ?)
                    """,
                    (video_id, "User job without vaulted key", "tenant_xyz", tf_path),
                )
                conn.commit()

            worker = TranscriptionWorker()
            item = db.get_youtube_video(video_id)
            res = worker.process_downloaded_item(item)
            self.assertIsNone(res)

            updated_row = db.get_youtube_video(video_id)
            self.assertEqual(updated_row["status"], "FAILED")
            self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", updated_row["last_error"])
        finally:
            if os.path.exists(tf_path):
                os.unlink(tf_path)

    # -------------------------------------------------------------------------
    # 2. Highlight Worker Isolation
    # -------------------------------------------------------------------------

    @patch("dispatch.youtube_connections.get_user_gemini_key", return_value="")
    def test_highlight_worker_refuses_host_key_fallback_for_user_job(self, mock_get_key):
        video_id = "test-gemini-5"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, user_id, duration, segments_json)
                VALUES (?, ?, 'TRANSCRIBED', ?, 60.0, ?)
                """,
                (
                    video_id,
                    "User highlight job without vaulted key",
                    "tenant_xyz",
                    '[{"text": "Hello world", "start": 0.0, "end": 5.0}]',
                ),
            )
            conn.commit()

        worker = HighlightWorker()
        with self.assertRaises(RuntimeError) as ctx:
            worker.process_video_highlights(video_id)
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", str(ctx.exception))

        updated_row = db.get_youtube_video(video_id)
        self.assertEqual(updated_row["status"], "FAILED")
        self.assertIn("Refusing fallback to PC host GEMINI_API_KEY", updated_row["last_ai_error"])

    @patch("dispatch.ai_clips.worker.call_gemini_generate_content")
    @patch("dispatch.ai_clips.worker.validate_and_filter_candidates", return_value=[])
    @patch("dispatch.db.save_clip_definitions", return_value=[])
    def test_highlight_worker_uses_host_key_for_standalone_operator(
        self, mock_save_clips, mock_validate, mock_call_gemini
    ):
        mock_call_gemini.return_value = ([], "gemini-2.5-flash")
        video_id = "test-gemini-6"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, user_id, duration, segments_json)
                VALUES (?, ?, 'TRANSCRIBED', NULL, 60.0, ?)
                """,
                (
                    video_id,
                    "Standalone operator highlight job",
                    '[{"text": "Hello world", "start": 0.0, "end": 5.0}]',
                ),
            )
            conn.commit()

        worker = HighlightWorker()
        res = worker.process_video_highlights(video_id)
        mock_call_gemini.assert_called_once()
        # Verify host key was passed
        _, kwargs = mock_call_gemini.call_args
        self.assertEqual(kwargs.get("api_key"), "host_pc_secret_key_12345")


if __name__ == "__main__":
    unittest.main()
