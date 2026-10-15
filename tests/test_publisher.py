"""Unit test for publishing outbox and platform adapters."""
import unittest
from unittest.mock import patch
import subprocess
import uuid
from pathlib import Path
from dispatch.config import CLIPS_DIR
from dispatch.publisher.outbox import process_outbox_queue
from dispatch import db
from dispatch.db import init_db, save_clip, update_clip_media, approve_clip, get_db_connection


class TestPublisher(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_publisher(self):
        # 1. Register clip in DB first to get unique ID
        unique_tag = uuid.uuid4().hex[:6]
        clip_id = save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=6.0,
            title=f"Test Publish Short {unique_tag}",
            hook="Opening Hook",
            description="Description",
            hashtags="#Shorts",
            virality_score=92,
            layout_mode="fit_blur",
            platform_targets="youtube,instagram"
        )

        # 2. Create a mock clip video file for this exact clip
        clip_file = CLIPS_DIR / f"{clip_id}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=360x640:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "6",
            "-c:v", "libx264",
            "-c:a", "aac",
            str(clip_file)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        update_clip_media(clip_id, str(clip_file), None)

        # 3. Approve clip (enqueues to publishing outbox for youtube and instagram)
        approve_clip(clip_id, custom_mode="private")

        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 2, f"Expected 2 outbox jobs, got {count}")

        # 4. Process outbox queue with mocked external platform publication
        with patch("dispatch.publisher.outbox.upload_youtube_short", return_value={"remote_id": "mock_yt_123", "remote_url": "https://youtu.be/mock_yt_123"}), \
             patch("dispatch.publisher.outbox.upload_instagram_reel", return_value={"remote_id": "mock_ig_123", "remote_url": "https://instagram.com/reel/mock_ig_123"}):
            success_count = process_outbox_queue()
        self.assertGreaterEqual(success_count, 2, f"Expected at least 2 successful publishes, got {success_count}")

        # 5. Verify this specific clip is marked published
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM clips WHERE id = ?", (clip_id,))
            status = cursor.fetchone()[0]
            self.assertEqual(status, "published", f"Expected clip status 'published', got {status}")

        # 6. Verify local file was preserved on disk for preview and multi-platform publishing
        self.assertTrue(clip_file.exists(), "Local published clip file should remain on disk")
        clip_file.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

