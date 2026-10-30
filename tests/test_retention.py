"""Unit tests for dispatch.storage.retention multi-tier smart retention policy."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dispatch import db
from dispatch.config import STORAGE_DIR
from dispatch.storage.retention import (
    SEVEN_DAYS_SECONDS,
    SEVENTY_TWO_HOURS_SECONDS,
    get_directory_size,
    get_source_videos_status,
    prune_source_video,
    enforce_retention_policy,
)


class TestStorageRetention(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="dispatch_retention_test_")
        self.storage_path = Path(self.test_dir) / "storage"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.inbox_path = self.storage_path / "youtube_inbox"
        self.inbox_path.mkdir(parents=True, exist_ok=True)

        db.init_db()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_directory_size_calculation(self):
        sub = self.storage_path / "sub"
        sub.mkdir()
        f1 = self.storage_path / "f1.bin"
        f2 = sub / "f2.bin"
        f1.write_bytes(b"a" * 1000)
        f2.write_bytes(b"b" * 2500)

        total = get_directory_size(self.storage_path)
        self.assertEqual(total, 3500)

    def test_7_day_source_retention_policy(self):
        """Source videos >= 7 days old (168h) are pruned even if clips are not published."""
        now = datetime.now(timezone.utc)
        eight_days_ago = now - timedelta(days=8)
        two_days_ago = now - timedelta(days=2)

        # 1. Old source video (8 days old)
        old_file = self.inbox_path / "old_vid.mp4"
        old_file.write_bytes(b"0" * 1024)

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_old', 'completed')")
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_recent', 'completed')")

            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_old', 'sess_old', 'old_vid.mp4', ?, ?, 'transcribed')
            """, (str(old_file), eight_days_ago.strftime("%Y-%m-%d %H:%M:%S")))

            # Clip is still in ready_review (not published)
            cursor.execute("""
                INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at)
                VALUES ('clip_old_1', 'sess_old', 'chunk_old', 'Draft Clip', 10.0, 0, 10, 'ready_review', ?)
            """, (eight_days_ago.strftime("%Y-%m-%d %H:%M:%S"),))

            # 2. Recent source video (2 days old)
            recent_file = self.inbox_path / "recent_vid.mp4"
            recent_file.write_bytes(b"1" * 2048)
            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_recent', 'sess_recent', 'recent_vid.mp4', ?, ?, 'transcribed')
            """, (str(recent_file), two_days_ago.strftime("%Y-%m-%d %H:%M:%S")))

        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=100 * 1024 * 1024,
            now=now,
        )

        self.assertEqual(stats["pruned_7_day"], 1)
        self.assertFalse(old_file.exists())
        self.assertTrue(recent_file.exists())

        # Check DB status updated
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT status, filepath FROM chunks WHERE id = 'chunk_old'").fetchone()
            self.assertEqual(row["status"], "purged")
            self.assertEqual(row["filepath"], "")

    def test_72_hour_post_publish_grace_rule(self):
        """If ALL clips are published/approved, source video is eligible after 72 hours."""
        now = datetime.now(timezone.utc)
        four_days_ago = now - timedelta(days=4)   # 96 hours ago (past 72h)
        one_day_ago = now - timedelta(days=1)     # 24 hours ago (within 72h)

        # Video A: All clips published 96 hours ago -> eligible and pruned
        vid_a = self.inbox_path / "vid_a.mp4"
        vid_a.write_bytes(b"a" * 1024)

        # Video B: Clips published 24 hours ago (< 72h grace) -> preserved
        vid_b = self.inbox_path / "vid_b.mp4"
        vid_b.write_bytes(b"b" * 1024)

        # Video C: Has 2 clips, 1 published 96h ago, 1 still in 'ready_review' -> preserved
        vid_c = self.inbox_path / "vid_c.mp4"
        vid_c.write_bytes(b"c" * 1024)

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_a', 'completed')")
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_b', 'completed')")
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_c', 'completed')")

            # Video A
            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_a', 'sess_a', 'vid_a.mp4', ?, ?, 'transcribed')
            """, (str(vid_a), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))
            cursor.execute("""
                INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at, published_at)
                VALUES ('clip_a', 'sess_a', 'chunk_a', 'Published Clip A', 10.0, 0, 10, 'published', ?, ?)
            """, (four_days_ago.strftime("%Y-%m-%d %H:%M:%S"), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))

            # Video B
            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_b', 'sess_b', 'vid_b.mp4', ?, ?, 'transcribed')
            """, (str(vid_b), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))
            cursor.execute("""
                INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at, published_at)
                VALUES ('clip_b', 'sess_b', 'chunk_b', 'Published Clip B', 10.0, 0, 10, 'published', ?, ?)
            """, (four_days_ago.strftime("%Y-%m-%d %H:%M:%S"), one_day_ago.strftime("%Y-%m-%d %H:%M:%S")))

            # Video C
            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_c', 'sess_c', 'vid_c.mp4', ?, ?, 'transcribed')
            """, (str(vid_c), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))
            cursor.execute("""
                INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at, published_at)
                VALUES ('clip_c_pub', 'sess_c', 'chunk_c', 'Clip C Pub', 10.0, 0, 10, 'published', ?, ?)
            """, (four_days_ago.strftime("%Y-%m-%d %H:%M:%S"), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))
            cursor.execute("""
                INSERT INTO clips (id, session_id, chunk_id, title, duration, start_time, end_time, status, created_at)
                VALUES ('clip_c_draft', 'sess_c', 'chunk_c', 'Clip C Draft', 10.0, 0, 10, 'ready_review', ?)
            """, (four_days_ago.strftime("%Y-%m-%d %H:%M:%S"),))

        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=100 * 1024 * 1024,
            now=now,
        )

        self.assertEqual(stats["pruned_72_hour_published"], 1)
        self.assertFalse(vid_a.exists())
        self.assertTrue(vid_b.exists())
        self.assertTrue(vid_c.exists())

    def test_10_gb_hard_disk_safety_ceiling_fifo(self):
        """When disk exceeds ceiling, oldest source videos past 72 hours are pruned FIFO."""
        now = datetime.now(timezone.utc)
        six_days_ago = now - timedelta(days=6)
        four_days_ago = now - timedelta(days=4)
        one_day_ago = now - timedelta(days=1)

        # 3 videos, each 1 MB. Ceiling set to 1.5 MB.
        v1 = self.inbox_path / "fifo_1_oldest.mp4"  # 6 days old (> 72h)
        v2 = self.inbox_path / "fifo_2_middle.mp4"  # 4 days old (> 72h)
        v3 = self.inbox_path / "fifo_3_newest.mp4"  # 1 day old (< 72h)

        v1.write_bytes(b"1" * (1024 * 1024))
        v2.write_bytes(b"2" * (1024 * 1024))
        v3.write_bytes(b"3" * (1024 * 1024))

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_f1', 'completed')")
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_f2', 'completed')")
            cursor.execute("INSERT INTO sessions (id, status) VALUES ('sess_f3', 'completed')")

            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_f1', 'sess_f1', 'fifo_1_oldest.mp4', ?, ?, 'transcribed')
            """, (str(v1), six_days_ago.strftime("%Y-%m-%d %H:%M:%S")))

            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_f2', 'sess_f2', 'fifo_2_middle.mp4', ?, ?, 'transcribed')
            """, (str(v2), four_days_ago.strftime("%Y-%m-%d %H:%M:%S")))

            cursor.execute("""
                INSERT INTO chunks (id, session_id, filename, filepath, created_at, status)
                VALUES ('chunk_f3', 'sess_f3', 'fifo_3_newest.mp4', ?, ?, 'transcribed')
            """, (str(v3), one_day_ago.strftime("%Y-%m-%d %H:%M:%S")))

        # Storage is ~3 MB. Set ceiling to 1.5 MB.
        ceiling = int(1.5 * 1024 * 1024)
        stats = enforce_retention_policy(
            storage_dir=self.storage_path,
            storage_ceiling_bytes=ceiling,
            now=now,
        )

        self.assertTrue(stats["ceiling_exceeded"])
        # v1 should be pruned first FIFO
        self.assertFalse(v1.exists())
        # Total size drops from 3 MB to ~2 MB (still > 1.5 MB), so v2 is pruned next
        self.assertFalse(v2.exists())
        # Total size is now ~1 MB (<= 1.5 MB), so v3 is preserved (also v3 is < 72h)
        self.assertTrue(v3.exists())
        self.assertEqual(stats["pruned_ceiling_fifo"], 2)


if __name__ == "__main__":
    unittest.main()
