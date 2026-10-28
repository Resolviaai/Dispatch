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
                VALUES (?, 'Sample Clip', 0.0, 15.0, 15.0, 'approved', 'tests/fixtures/sample.mp4')
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
                VALUES (?, 'Remote Clip', 0.0, 15.0, 15.0, 'approved', 'tests/fixtures/sample.mp4')
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
        """Verify that ambiguous timeouts even on the FIRST attempt transition to reconciliation_required."""
        clip_id = "test_clip_timeout_recon"
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'Timeout Clip', 0.0, 15.0, 15.0, 'approved', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key, attempt_count)
                VALUES ('job_timeout_recon', ?, 'youtube', 'queued', 'idem_timeout_recon', 0)
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

    def test_is_ambiguous_network_error(self):
        """Test classification of ambiguous vs deterministic errors in is_ambiguous_network_error."""
        from dispatch.publisher.outbox import is_ambiguous_network_error
        import socket

        # 1. Native exception types
        self.assertTrue(is_ambiguous_network_error(TimeoutError("timed out")))
        self.assertTrue(is_ambiguous_network_error(ConnectionResetError("connection reset")))
        self.assertTrue(is_ambiguous_network_error(ConnectionAbortedError("connection aborted")))
        self.assertTrue(is_ambiguous_network_error(socket.timeout("timed out")))

        # 2. Windows socket errnos
        err10053 = OSError("An established connection was aborted by the software in your host machine")
        err10053.winerror = 10053
        self.assertTrue(is_ambiguous_network_error(err10053))

        err10054 = OSError("An existing connection was forcibly closed by the remote host")
        err10054.winerror = 10054
        self.assertTrue(is_ambiguous_network_error(err10054))

        err10060 = OSError("A connection attempt failed because the connected party did not properly respond")
        err10060.winerror = 10060
        self.assertTrue(is_ambiguous_network_error(err10060))

        # Linux / POSIX errnos
        err104 = OSError(104, "Connection reset by peer")
        self.assertTrue(is_ambiguous_network_error(err104))

        err32 = OSError(32, "Broken pipe")
        self.assertTrue(is_ambiguous_network_error(err32))

        # 3. Non-ambiguous errors must return False
        self.assertFalse(is_ambiguous_network_error(ValueError("Invalid argument: duration too short")))
        self.assertFalse(is_ambiguous_network_error(FileNotFoundError("Local video file missing")))
        self.assertFalse(is_ambiguous_network_error(KeyError("missing_field")))

    def test_first_attempt_winerror_10053_transitions_to_reconciliation_required(self):
        """Verify that a WinError 10053 socket abortion transitions to reconciliation_required on the first attempt."""
        clip_id = "test_clip_winerror_10053"
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'WinError Clip', 0.0, 15.0, 15.0, 'approved', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key, attempt_count)
                VALUES ('job_winerror_10053', ?, 'youtube', 'queued', 'idem_winerror_10053', 0)
            """, (clip_id,))
            conn.commit()

        win_err = OSError("Connection aborted")
        win_err.winerror = 10053

        with patch("dispatch.publisher.outbox.probe_video", return_value=(True, {"duration": 15.0}, None)):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("dispatch.publisher.outbox.upload_youtube_short", side_effect=win_err):
                    from dispatch.publisher.outbox import process_outbox_queue
                    process_outbox_queue()

        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, last_error FROM publishing_outbox WHERE id = 'job_winerror_10053'").fetchone()
            self.assertEqual(row["status"], "reconciliation_required")
            self.assertIn("Ambiguous remote error", row["last_error"])

    def test_reconciliation_endpoints_and_resolution(self):
        """Test GET and POST reconciliation management APIs in web dashboard."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app
        client = TestClient(app)

        clip_id = "test_clip_recon_api"
        job_id = "job_recon_api_123"

        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO clips (id, title, start_time, end_time, duration, status, video_path)
                VALUES (?, 'Recon API Clip', 0.0, 15.0, 15.0, 'ready_review', 'tests/fixtures/sample.mp4')
            """, (clip_id,))
            conn.execute("""
                INSERT OR REPLACE INTO publishing_outbox 
                (id, clip_id, platform, status, idempotency_key, attempt_count)
                VALUES (?, ?, 'youtube', 'reconciliation_required', 'idem_recon_api', 1)
            """, (job_id, clip_id))
            conn.commit()

        # 1. GET /api/publisher/outbox
        res_outbox = client.get("/api/publisher/outbox")
        self.assertEqual(res_outbox.status_code, 200)
        jobs = res_outbox.json()["jobs"]
        self.assertTrue(any(j["id"] == job_id for j in jobs))

        # 2. GET /api/publisher/reconcile
        res_recon = client.get("/api/publisher/reconcile")
        self.assertEqual(res_recon.status_code, 200)
        recon_jobs = res_recon.json()["jobs"]
        self.assertTrue(any(j["id"] == job_id for j in recon_jobs))

        # 3. POST /api/publisher/reconcile/{job_id}/resolve - Action: retry
        res_retry = client.post(
            f"/api/publisher/reconcile/{job_id}/resolve",
            json={"action": "retry", "note": "Verified not published on channel; safe to retry"},
            headers={"host": "127.0.0.1:8000"}
        )
        self.assertEqual(res_retry.status_code, 200)
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status FROM publishing_outbox WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["status"], "queued")

        # 4. POST /api/publisher/reconcile/{job_id}/resolve - Action: mark_published
        res_pub = client.post(
            f"/api/publisher/reconcile/{job_id}/resolve",
            json={"action": "mark_published", "remote_id": "yt_resolved_777", "remote_url": "https://youtube.com/watch?v=yt_resolved_777"},
            headers={"host": "127.0.0.1:8000"}
        )
        self.assertEqual(res_pub.status_code, 200)
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, remote_id FROM publishing_outbox WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["status"], "published")
            self.assertEqual(row["remote_id"], "yt_resolved_777")


if __name__ == "__main__":
    unittest.main()
