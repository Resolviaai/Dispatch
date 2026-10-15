"""Tests for Phase 4: Publishing Outbox, FastAPI Unblocking & Studio UI."""
import unittest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch import db
from dispatch.config import STORAGE_DIR
from dispatch.main import acquire_single_instance_lock, release_single_instance_lock, LOCK_FILE


class TestPhase4Publishing(unittest.TestCase):

    def setUp(self):
        db.init_db()

    def test_single_instance_lock_lifecycle(self):
        """Verify lock file creation and release."""
        release_single_instance_lock()
        self.assertFalse(LOCK_FILE.exists())

        acquired = acquire_single_instance_lock()
        self.assertTrue(acquired)
        self.assertTrue(LOCK_FILE.exists())
        self.assertEqual(LOCK_FILE.read_text().strip(), str(os.getpid()))

        release_single_instance_lock()
        self.assertFalse(LOCK_FILE.exists())

    def test_unsupported_platform_marks_failed(self):
        """Verify unsupported platforms transition to 'failed' rather than remaining in 'uploading'."""
        clip_id = "test_clip_unsupported_plat"
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'Sample Clip', 0.0, 15.0, 15.0, 'ready_review', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key)
                VALUES ('job_unsupported_1', ?, 'tiktok', 'queued', 'idem_unsupported_1')
            """, (clip_id,))
            conn.commit()

        # Mock probe_video to pass
        with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 15.0}, None)):
            with patch("pathlib.Path.exists", return_value=True):
                from dispatch.publisher.outbox import process_outbox_queue
                process_outbox_queue()

        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = 'job_unsupported_1'").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIn("Unsupported", row["last_error"])

    def test_existing_remote_id_prevents_duplicate_publish(self):
        """Verify that a job with an existing remote_id transitions to reconciliation_required and does not re-upload."""
        clip_id = "test_clip_remote_id_dup"
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'Remote Clip', 0.0, 15.0, 15.0, 'ready_review', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key, remote_id)
                VALUES ('job_remote_dup', ?, 'youtube', 'queued', 'idem_remote_dup', 'yt_existing_video_123')
            """, (clip_id,))
            conn.commit()

        with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 15.0}, None)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("dispatch.publisher.outbox.upload_youtube_short") as mock_upload:
                    from dispatch.publisher.outbox import process_outbox_queue
                    process_outbox_queue()
                    mock_upload.assert_not_called()

        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = 'job_remote_dup'").fetchone()
            self.assertEqual(row["status"], "reconciliation_required")
            self.assertIn("confirmed remote_id", row["last_error"])

    def test_ambiguous_error_transitions_to_reconciliation_required(self):
        """Verify that ambiguous timeouts on retry transition to reconciliation_required."""
        clip_id = "test_clip_timeout_recon"
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'Timeout Clip', 0.0, 15.0, 15.0, 'ready_review', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key, attempt_count)
                VALUES ('job_timeout_recon', ?, 'youtube', 'queued', 'idem_timeout_recon', 1)
            """, (clip_id,))
            conn.commit()

        with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 15.0}, None)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("dispatch.publisher.outbox.upload_youtube_short", side_effect=TimeoutError("Connection timed out waiting for remote response")):
                    from dispatch.publisher.outbox import process_outbox_queue
                    process_outbox_queue()

        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = 'job_timeout_recon'").fetchone()
            self.assertEqual(row["status"], "reconciliation_required")
            self.assertIn("Ambiguous remote error", row["last_error"])


if __name__ == "__main__":
    unittest.main()
