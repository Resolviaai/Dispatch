"""Focused tests for YouTube Processing Status Tracking, Deferred Ingestion, and Stall Detection.
Verifies:
  1. A video reported as processing is deferred without repeated media downloads.
  2. A video whose processing later succeeds is automatically ingested.
  3. A permanent processing failure is recorded with useful diagnostic information.
  4. Transient API or download errors are handled without corrupting video state.
  5. A video waiting for processing is surfaced as stalled when exceeding timeout without false completion.
  6. Batch status query handles multiple video IDs accurately.
"""
import unittest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from dispatch import db
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
from dispatch.youtube_inbox.poller import YouTubeInboxPoller
from dispatch.youtube_inbox.oauth import get_youtube_processing_status


class TestYouTubeProcessingTracker(unittest.TestCase):

    def setUp(self):
        db.init_db()
        self.temp_dir = Path(tempfile.mkdtemp())
        self.catcher = YouTubeInboxCatcher(storage_dir=self.temp_dir)
        self.poller = YouTubeInboxPoller(catcher=self.catcher)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'tt_%'")

    def test_01_processing_video_is_deferred_without_download(self):
        """1. A video reported as processing is deferred without calling download_video."""
        vid = "tt_proc0001"
        mock_status = {
            vid: {
                "video_id": vid,
                "upload_status": "uploaded",
                "processing_status": "processing",
                "failure_reason": None,
                "duration": "P0D",
                "title": "[DISPATCH] Processing Test",
                "published_at": "2026-10-09T18:00:00Z"
            }
        }

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=mock_status), \
             patch.object(self.catcher, "download_video") as mock_download:

            result = self.catcher.ingest_video(url_or_id=vid)

            # Assert download was NEVER called
            mock_download.assert_not_called()

            # Assert returned deferred status
            self.assertEqual(result["status"], "WAITING_FOR_YOUTUBE_PROCESSING")
            self.assertTrue(result["processing"])
            self.assertFalse(result["verified"])

            # Assert database state
            record = db.get_youtube_video(vid)
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "WAITING_FOR_YOUTUBE_PROCESSING")

    def test_02_processing_succeeded_advances_to_download_and_ingestion(self):
        """2. A video whose processing succeeds proceeds to download and completes ingestion."""
        vid = "tt_succ0002"
        mock_status = {
            vid: {
                "video_id": vid,
                "upload_status": "processed",
                "processing_status": "succeeded",
                "failure_reason": None,
                "duration": "PT45S",
                "title": "[DISPATCH] Succeeded Test",
                "published_at": "2026-10-09T18:00:00Z"
            }
        }

        fake_video_file = self.temp_dir / f"{vid}.mp4"
        fake_video_file.write_bytes(b"dummy_content")

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=mock_status), \
             patch.object(self.catcher, "download_video", return_value=fake_video_file) as mock_download, \
             patch("dispatch.ingestion.validator.probe_video", return_value=(True, {"duration": 45.0, "width": 1080, "height": 1920}, None)):

            result = self.catcher.ingest_video(url_or_id=vid)

            mock_download.assert_called_once_with(vid)
            self.assertEqual(result["status"], "DOWNLOADED")
            self.assertTrue(result["verified"])

            record = db.get_youtube_video(vid)
            self.assertEqual(record["status"], "DOWNLOADED")
            self.assertEqual(record["duration"], 45.0)

    def test_03_processing_failed_records_diagnostic_information(self):
        """3. A permanent processing failure records failure reason and transitions to FAILED."""
        vid = "tt_fail0003"
        mock_status = {
            vid: {
                "video_id": vid,
                "upload_status": "failed",
                "processing_status": "failed",
                "failure_reason": "transcodingFailed",
                "duration": "P0D",
                "title": "[DISPATCH] Failed Video",
                "published_at": "2026-10-09T18:00:00Z"
            }
        }

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=mock_status), \
             patch.object(self.catcher, "download_video") as mock_download:

            result = self.catcher.ingest_video(url_or_id=vid)

            mock_download.assert_not_called()
            self.assertEqual(result["status"], "FAILED")
            self.assertIn("transcodingFailed", result["error"])

            record = db.get_youtube_video(vid)
            self.assertEqual(record["status"], "FAILED")
            self.assertIn("transcodingFailed", record["last_error"])

    def test_04_poller_waiting_video_staleness_detection(self):
        """4. Poller marks video stalled when elapsed processing time exceeds threshold without false completion."""
        vid = "tt_stal0004"
        # 2 hours before utcnow
        old_pub_time = "2026-10-09T18:00:00Z"

        db.register_youtube_video(
            video_id=vid,
            title="[DISPATCH] Stalled Video",
            upload_time=old_pub_time,
            dispatch_id="dsp_test_stall"
        )
        db.update_youtube_video(vid, status="WAITING_FOR_YOUTUBE_PROCESSING")

        mock_auth_uploads = [
            {
                "video_id": vid,
                "title": "[DISPATCH] Stalled Video",
                "published_at": old_pub_time,
                "dispatch_id": "dsp_test_stall"
            }
        ]

        mock_status = {
            vid: {
                "video_id": vid,
                "upload_status": "uploaded",
                "processing_status": "processing",
                "failure_reason": None,
                "duration": "P0D",
                "title": "[DISPATCH] Stalled Video",
                "published_at": old_pub_time
            }
        }

        with patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads", return_value=mock_auth_uploads), \
             patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=mock_status), \
             patch.object(self.catcher, "ingest_video") as mock_ingest:

            # Set timeout threshold to 60 minutes
            with patch.dict(os.environ, {"DISPATCH_YOUTUBE_PROCESSING_TIMEOUT_MINUTES": "60"}):
                self.poller.poll_once()

            # Assert ingest / download was NOT called
            mock_ingest.assert_not_called()

            # Mark it actionable without treating YouTube's delay as a permanent failure.
            record = db.get_youtube_video(vid)
            self.assertEqual(record["status"], "STALLED_YOUTUBE_PROCESSING")
            self.assertIn("[STALLED_YOUTUBE_PROCESSING]", record["last_error"])

            # A later successful YouTube result must revisit this same video ID and resume ingestion.
            mock_status[vid]["processing_status"] = "succeeded"
            mock_status[vid]["upload_status"] = "processed"
            self.poller.poll_once()
            mock_ingest.assert_called_once_with(
                url_or_id=vid,
                dispatch_id="dsp_test_stall",
                session_id=None,
                sequence_number=None
            )

    def test_05_batch_status_query_parses_responses_correctly(self):
        """5. get_youtube_processing_status parses multiple items from service."""
        mock_service = MagicMock()
        mock_videos = MagicMock()
        mock_service.videos.return_value = mock_videos

        mock_resp = {
            "items": [
                {
                    "id": "vid_1",
                    "status": {"uploadStatus": "uploaded", "privacyStatus": "unlisted"},
                    "processingDetails": {"processingStatus": "processing"},
                    "contentDetails": {"duration": "P0D"},
                    "snippet": {"title": "Vid 1", "publishedAt": "2026-10-10T01:00:00Z"}
                },
                {
                    "id": "vid_2",
                    "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                    "processingDetails": {"processingStatus": "succeeded"},
                    "contentDetails": {"duration": "PT1M"},
                    "snippet": {"title": "Vid 2", "publishedAt": "2026-10-10T01:05:00Z"}
                }
            ]
        }
        mock_videos.list.return_value.execute.return_value = mock_resp

        with patch("dispatch.youtube_inbox.oauth.get_youtube_service", return_value=mock_service):
            res = get_youtube_processing_status(["vid_1", "vid_2"])

            self.assertEqual(len(res), 2)
            self.assertEqual(res["vid_1"]["processing_status"], "processing")
            self.assertEqual(res["vid_1"]["upload_status"], "uploaded")
            self.assertEqual(res["vid_2"]["processing_status"], "succeeded")
            self.assertEqual(res["vid_2"]["duration"], "PT1M")

    def test_06_transient_api_error_does_not_corrupt_state(self):
        """6. Transient API errors fall back safely and retain video in database."""
        vid = "tt_tran0006"
        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", side_effect=RuntimeError("Network timeout")), \
             patch.object(self.catcher, "download_video", side_effect=Exception("ERROR: [youtube] We're processing this video. Check back later.")):

            result = self.catcher.ingest_video(url_or_id=vid)

            self.assertEqual(result["status"], "WAITING_FOR_YOUTUBE_PROCESSING")
            record = db.get_youtube_video(vid)
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "WAITING_FOR_YOUTUBE_PROCESSING")

    def test_07_already_downloaded_video_skipped_idempotently(self):
        """7. An already-downloaded valid video returns immediately without re-downloading."""
        vid = "tt_done0007"
        fake_file = self.temp_dir / f"{vid}.mp4"
        fake_file.write_bytes(b"existing_video_bytes")

        db.register_youtube_video(video_id=vid, title="Already Downloaded")
        db.update_youtube_video(vid, status="DOWNLOADED", local_video_path=str(fake_file))

        with patch.object(self.catcher, "download_video") as mock_download, \
             patch("dispatch.ingestion.validator.probe_video", return_value=(True, {"duration": 30.0}, None)):

            result = self.catcher.ingest_video(url_or_id=vid)

            mock_download.assert_not_called()
            self.assertEqual(result["status"], "DOWNLOADED")
            self.assertTrue(result["verified"])

    def test_08_terminal_failure_wins_over_uploaded_status(self):
        """A failed processing state must not be mislabeled as still processing."""
        vid = "tt_failupl08"
        db.register_youtube_video(video_id=vid, title="Failed upload-status fixture")
        db.update_youtube_video(vid, status="WAITING_FOR_YOUTUBE_PROCESSING")
        status = {
            vid: {
                "video_id": vid,
                "upload_status": "uploaded",
                "processing_status": "failed",
                "failure_reason": "transcodingFailed",
                "duration": "P0D",
            }
        }

        with patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads", return_value=[{"video_id": vid}]), \
             patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=status), \
             patch.object(self.catcher, "ingest_video") as mock_ingest:
            self.poller.poll_once()

        mock_ingest.assert_not_called()
        record = db.get_youtube_video(vid)
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("transcodingFailed", record["last_error"])
        self.assertIn("upload_status=uploaded", record["last_error"])

        terminal_error = record["last_error"]
        status[vid]["processing_status"] = None
        with patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads", return_value=[{"video_id": vid}]), \
             patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", return_value=status), \
             patch.object(self.catcher, "ingest_video") as mock_ingest:
            self.poller.poll_once()
        mock_ingest.assert_not_called()
        record = db.get_youtube_video(vid)
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["last_error"], terminal_error)

    def test_09_status_api_error_preserves_existing_state_and_does_not_download(self):
        """A status API outage is visible and cannot trigger an unverified media download."""
        vid = "tt_apierr09"
        db.register_youtube_video(video_id=vid, title="Status API outage fixture")
        db.update_youtube_video(vid, status="STALLED_YOUTUBE_PROCESSING", last_error="previous status")

        with patch("dispatch.youtube_inbox.poller.list_authenticated_user_uploads", return_value=[{"video_id": vid}]), \
             patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status", side_effect=RuntimeError("network timeout")), \
             patch.object(self.catcher, "ingest_video") as mock_ingest:
            self.poller.poll_once()

        mock_ingest.assert_not_called()
        record = db.get_youtube_video(vid)
        self.assertEqual(record["status"], "STALLED_YOUTUBE_PROCESSING")
        self.assertIn("YOUTUBE_STATUS_UNAVAILABLE", record["last_error"])
        self.assertIn("network timeout", record["last_error"])


if __name__ == "__main__":
    unittest.main()
