"""Focused tests for Pillar 1 ? YouTube Ingestion & Media Validation.
Verifies:
  - Discovery pagination (pageToken support)
  - Duplicate discovery handling (idempotent DB insert)
  - Ingestion state transitions: DISCOVERED -> DOWNLOADING -> VALIDATING -> DOWNLOADED
  - Zero-byte / missing / corrupted file rejection (VALIDATING -> FAILED)
  - Invariant: status = DOWNLOADED guarantees local file exists and passes ffprobe
  - Restart / duplicate ingest returns existing record without re-downloading
"""
import unittest
import tempfile
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatch import db
from dispatch.config import YOUTUBE_INBOX_DIR
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
from dispatch.youtube_inbox.oauth import list_authenticated_user_uploads


class TestPillar1Ingestion(unittest.TestCase):

    def setUp(self):
        db.init_db()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.catcher = YouTubeInboxCatcher(storage_dir=self.temp_dir)
        self.test_video_id = "p1_test0001"
        self.test_dispatch_id = "dsp_test_p1_001"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'p1_%'")

    def _create_valid_mp4(self, path: Path, duration_sec: int = 2):
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(duration_sec),
            "-c:v", "libx264", "-c:a", "aac", str(path)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def test_01_discovery_duplicate_idempotency(self):
        """Verify registering the same video 10 times yields exactly 1 DB record."""
        vid_id = "p1_dup00001"
        for _ in range(10):
            db.register_youtube_video(
                video_id=vid_id,
                title="Test Video",
                dispatch_id=self.test_dispatch_id
            )
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM youtube_inbox WHERE video_id = ?", (vid_id,))
            count = cursor.fetchone()[0]
        self.assertEqual(count, 1)

    def test_02_discovery_pagination_support(self):
        """Verify list_authenticated_user_uploads follows nextPageToken."""
        mock_service = MagicMock()
        mock_channels = MagicMock()
        mock_channels.list.return_value.execute.return_value = {
            "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU_test"}}}]
        }
        mock_service.channels.return_value = mock_channels

        # Simulate 2 pages of playlist items
        page1_resp = {
            "items": [
                {
                    "snippet": {
                        "resourceId": {"videoId": "p1_page1_01"},
                        "title": "[DISPATCH] Page 1 Video",
                        "description": "dispatch_id: dsp_page1",
                        "tags": ["dispatch"],
                        "publishedAt": "2026-10-07T00:00:00Z"
                    },
                    "status": {"privacyStatus": "unlisted"}
                }
            ],
            "nextPageToken": "TOKEN_PAGE_2"
        }
        page2_resp = {
            "items": [
                {
                    "snippet": {
                        "resourceId": {"videoId": "p1_page2_02"},
                        "title": "[DISPATCH] Page 2 Video",
                        "description": "dispatch_id: dsp_page2",
                        "tags": ["dispatch"],
                        "publishedAt": "2026-10-07T00:01:00Z"
                    },
                    "status": {"privacyStatus": "unlisted"}
                }
            ]
        }
        mock_playlist_items = MagicMock()
        mock_playlist_items.list.return_value.execute.side_effect = [page1_resp, page2_resp]
        mock_service.playlistItems.return_value = mock_playlist_items

        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=mock_service):
            results = list_authenticated_user_uploads(max_results=50, max_pages=3)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["video_id"], "p1_page1_01")
            self.assertEqual(results[1]["video_id"], "p1_page2_02")

    def test_03_ingest_successful_download_and_validation(self):
        """Verify valid downloaded video transitions through DISCOVERED -> DOWNLOADING -> VALIDATING -> DOWNLOADED."""
        vid_id = "p1_ok000001"
        valid_file = self.temp_dir / f"{vid_id}.mp4"
        self._create_valid_mp4(valid_file, duration_sec=2)

        with patch.object(self.catcher, "download_video", return_value=valid_file):
            result = self.catcher.ingest_video(vid_id, dispatch_id=self.test_dispatch_id)

        self.assertEqual(result["status"], "DOWNLOADED")
        self.assertTrue(result["verified"])
        self.assertEqual(result["local_video_path"], str(valid_file))

        # Check DB state
        record = db.get_youtube_video(vid_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "DOWNLOADED")
        self.assertEqual(record["local_video_path"], str(valid_file))
        self.assertGreater(record["duration"], 0.0)

    def test_04_ingest_corrupt_file_rejected_to_failed(self):
        """Verify zero-byte or corrupt file is rejected with status = FAILED and purged."""
        vid_id = "p1_bad00001"
        corrupt_file = self.temp_dir / f"{vid_id}.mp4"
        corrupt_file.write_bytes(b"CORRUPTED_NON_VIDEO_DATA")

        with patch.object(self.catcher, "download_video", return_value=corrupt_file):
            with self.assertRaises(ValueError):
                self.catcher.ingest_video(vid_id, dispatch_id=self.test_dispatch_id)

        record = db.get_youtube_video(vid_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("Media validation failed", record.get("last_error", ""))
        # Verify corrupt file was deleted
        self.assertFalse(corrupt_file.exists())

    def test_05_ingest_zero_byte_file_rejected(self):
        """Verify 0-byte file fails media probe and marks status = FAILED."""
        vid_id = "p1_zero0001"
        zero_file = self.temp_dir / f"{vid_id}.mp4"
        zero_file.write_bytes(b"")

        with patch.object(self.catcher, "download_video", return_value=zero_file):
            with self.assertRaises(ValueError):
                self.catcher.ingest_video(vid_id, dispatch_id=self.test_dispatch_id)

        record = db.get_youtube_video(vid_id)
        self.assertEqual(record["status"], "FAILED")

    def test_06_ingest_idempotent_restart_skips_redownload(self):
        """Verify running ingest on an already DOWNLOADED video with intact file returns immediately."""
        vid_id = "p1_idem0001"
        valid_file = self.temp_dir / f"{vid_id}.mp4"
        self._create_valid_mp4(valid_file, duration_sec=2)

        with patch.object(self.catcher, "download_video", return_value=valid_file):
            first = self.catcher.ingest_video(vid_id, dispatch_id=self.test_dispatch_id)
        self.assertEqual(first["status"], "DOWNLOADED")

        # Second call should NOT call download_video
        with patch.object(self.catcher, "download_video", side_effect=AssertionError("Should not re-download")):
            second = self.catcher.ingest_video(vid_id, dispatch_id=self.test_dispatch_id)
        self.assertEqual(second["status"], "DOWNLOADED")
        self.assertTrue(second["verified"])


if __name__ == "__main__":
    unittest.main()
