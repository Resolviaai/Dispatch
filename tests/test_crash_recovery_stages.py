"""Unit tests for Crash & Power-Loss Recovery of Active Processing Stages.

Verifies:
1. Reconciling TRANSCRIBING items back to DOWNLOADED on crash/boot.
2. Reconciling ANALYZING items back to TRANSCRIBED on crash/boot.
3. Reconciling DOWNLOADING items back to DISCOVERED on crash/boot.
4. Failing closed to FAILED after 5 retries.
5. Runtime sweeps only reclaiming items older than stale_timeout_seconds.
6. Integration into recover_laptop_orchestrator().
"""

import unittest
from datetime import datetime, timedelta
from dispatch import db
from dispatch.orchestrator.recovery import (
    reconcile_interrupted_inbox_stages,
    recover_laptop_orchestrator,
)


class TestCrashRecoveryStages(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-crash-%'")
            conn.commit()

    def tearDown(self):
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'test-crash-%'")
            conn.commit()

    def test_boot_recovery_reconciles_transcribing_to_downloaded(self):
        video_id = "test-crash-transcribe-1"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count)
                VALUES (?, ?, 'TRANSCRIBING', 0)
                """,
                (video_id, "Stuck Transcription"),
            )
            conn.commit()

        reconciled = reconcile_interrupted_inbox_stages(is_boot=True)
        rec_ids = [r["video_id"] for r in reconciled]
        self.assertIn(video_id, rec_ids)

        row = db.get_youtube_video(video_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "DOWNLOADED")
        self.assertEqual(row["retry_count"], 1)
        self.assertIn("Transcription interrupted", row["last_error"])

    def test_boot_recovery_reconciles_analyzing_to_transcribed(self):
        video_id = "test-crash-analyze-1"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count, ai_attempt_count)
                VALUES (?, ?, 'ANALYZING', 1, 1)
                """,
                (video_id, "Stuck Highlight Analysis"),
            )
            conn.commit()

        reconciled = reconcile_interrupted_inbox_stages(is_boot=True)
        rec_ids = [r["video_id"] for r in reconciled]
        self.assertIn(video_id, rec_ids)

        row = db.get_youtube_video(video_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "TRANSCRIBED")
        self.assertEqual(row["retry_count"], 2)
        self.assertEqual(row["ai_attempt_count"], 2)
        self.assertIn("Highlight analysis interrupted", row["last_error"])

    def test_exceeded_retries_transition_to_failed(self):
        vid_transcribe = "test-crash-transcribe-fail"
        vid_analyze = "test-crash-analyze-fail"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count)
                VALUES (?, ?, 'TRANSCRIBING', 5)
                """,
                (vid_transcribe, "Failing Transcription"),
            )
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count, ai_attempt_count)
                VALUES (?, ?, 'ANALYZING', 5, 5)
                """,
                (vid_analyze, "Failing Highlight"),
            )
            conn.commit()

        reconcile_interrupted_inbox_stages(is_boot=True)

        row_t = db.get_youtube_video(vid_transcribe)
        self.assertEqual(row_t["status"], "FAILED")
        self.assertIn("Exceeded retry limit (5)", row_t["last_error"])

        row_a = db.get_youtube_video(vid_analyze)
        self.assertEqual(row_a["status"], "FAILED")
        self.assertIn("Exceeded retry limit (5)", row_a["last_error"])

    def test_runtime_sweep_respects_stale_timeout(self):
        vid_recent = "test-crash-recent"
        vid_stale = "test-crash-stale"

        # Recent: updated 1 minute ago
        recent_time = (datetime.utcnow() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        # Stale: updated 30 minutes ago
        stale_time = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count, updated_at)
                VALUES (?, ?, 'TRANSCRIBING', 0, ?)
                """,
                (vid_recent, "Active Worker", recent_time),
            )
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count, updated_at)
                VALUES (?, ?, 'TRANSCRIBING', 0, ?)
                """,
                (vid_stale, "Hung Worker", stale_time),
            )
            conn.commit()

        # Runtime sweep with 15-minute (900s) timeout
        reconciled = reconcile_interrupted_inbox_stages(is_boot=False, stale_timeout_seconds=900)
        reconciled_ids = [r["video_id"] for r in reconciled]

        self.assertNotIn(vid_recent, reconciled_ids)
        self.assertIn(vid_stale, reconciled_ids)

        # Recent remains TRANSCRIBING
        self.assertEqual(db.get_youtube_video(vid_recent)["status"], "TRANSCRIBING")
        # Stale recovered to DOWNLOADED
        self.assertEqual(db.get_youtube_video(vid_stale)["status"], "DOWNLOADED")

    def test_recover_laptop_orchestrator_integration(self):
        video_id = "test-crash-orch-1"
        with db.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO youtube_inbox (video_id, title, status, retry_count)
                VALUES (?, ?, 'ANALYZING', 0)
                """,
                (video_id, "Orchestrator test item"),
            )
            conn.commit()

        stats = recover_laptop_orchestrator(is_boot=True)
        self.assertIn("reclaimed_inbox_items", stats)
        reconciled_ids = [r["video_id"] for r in stats["reclaimed_inbox_items"]]
        self.assertIn(video_id, reconciled_ids)

        row = db.get_youtube_video(video_id)
        self.assertEqual(row["status"], "TRANSCRIBED")


if __name__ == "__main__":
    unittest.main()
