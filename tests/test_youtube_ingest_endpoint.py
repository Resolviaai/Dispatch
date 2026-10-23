"""Route B regression tests: manual YouTube link ingestion via the real dashboard endpoint.

Covers:
  1. Invalid URL -> HTTP 400 and no ingestion started.
  2. Valid submission -> HTTP 200 processing_queued and the real ingestion runs with the submitted URL.
  3. Concurrent duplicate submission -> exactly one ingestion run (real single-flight guard,
     exercised at both the catcher level and through the endpoint).
  4. Download failure -> FAILED status with last_error visible via GET /api/youtube/inbox.
  5. Re-submission of a video already processed by downstream workers (COMPLETED) -> no re-download.

Only the network boundary (yt-dlp metadata/download, YouTube Data API status) is mocked;
the endpoint, background threading, single-flight guard, DB state machine, and inbox API are real.
"""
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dispatch import db
from dispatch.web.app import app
from dispatch.youtube_inbox.catcher import (
    YouTubeInboxCatcher,
    _INGEST_INFLIGHT,
    is_ingestion_in_flight,
)

client = TestClient(app)


class TestYouTubeIngestEndpoint(unittest.TestCase):

    def setUp(self):
        db.init_db()
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM youtube_inbox WHERE video_id LIKE 'tt_ep%'")
        with _INGEST_INFLIGHT_LOCK_SAFE():
            _INGEST_INFLIGHT.clear()

    def test_01_invalid_url_rejected_with_400(self):
        """1. An invalid URL is rejected with HTTP 400 and no ingestion is started."""
        with patch.object(YouTubeInboxCatcher, "ingest_video") as mock_ingest:
            res = client.post("/api/youtube/ingest", json={"url": "definitely not a youtube link"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid", res.json()["detail"])
        mock_ingest.assert_not_called()

    def test_02_valid_url_accepted_and_ingestion_started(self):
        """2. A valid submission returns processing_queued and starts the real ingestion thread."""
        vid = "tt_ep_20001"
        started = threading.Event()
        release = threading.Event()
        captured = {}

        def fake_ingest(self, url_or_id, **kwargs):
            captured["url"] = url_or_id
            started.set()
            release.wait(timeout=5)
            return {"video_id": vid, "status": "DOWNLOADED", "verified": True}

        with patch.object(YouTubeInboxCatcher, "ingest_video", fake_ingest):
            res = client.post("/api/youtube/ingest", json={"url": f"https://youtu.be/{vid}"})

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "processing_queued")
        self.assertEqual(data["video_id"], vid)

        self.assertTrue(started.wait(timeout=5), "Background ingestion thread did not start")
        self.assertEqual(captured["url"], f"https://youtu.be/{vid}")
        release.set()

    def test_03_catcher_guard_allows_only_one_concurrent_run(self):
        """3a. Two concurrent ingest_video calls for the same video: the second is refused
        by the real single-flight guard and only one download runs."""
        vid = "tt_ep_30001"
        entered_fetch = threading.Event()
        release_fetch = threading.Event()
        download_calls = []

        def fake_fetch_info(self, video_id):
            entered_fetch.set()
            release_fetch.wait(timeout=5)
            return {"id": vid, "title": "Guard Test", "channel_id": "", "channel": "",
                    "duration": 10.0, "upload_date": "", "description": ""}

        def fake_download(self, video_id):
            download_calls.append(video_id)
            f = self.storage_dir / f"{video_id}.mp4"
            f.write_bytes(b"x" * 2048)
            return f

        catcher = YouTubeInboxCatcher(storage_dir=self.temp_dir)
        results = {}

        def run_once(key):
            results[key] = catcher.ingest_video(vid)

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status",
                       side_effect=lambda ids: {i: {"privacy_status": "unlisted"} for i in ids}), \
             patch.object(YouTubeInboxCatcher, "fetch_video_info", fake_fetch_info), \
             patch.object(YouTubeInboxCatcher, "download_video", fake_download), \
             patch("dispatch.ingestion.validator.probe_video",
                   return_value=(True, {"duration": 10.0, "width": 1080, "height": 1920}, None)):

            t1 = threading.Thread(target=run_once, args=("first",))
            t1.start()
            self.assertTrue(entered_fetch.wait(timeout=5), "First ingestion never reached metadata fetch")
            self.assertTrue(is_ingestion_in_flight(vid), "In-flight flag must be set during a run")

            t2 = threading.Thread(target=run_once, args=("second",))
            t2.start()
            t2.join(timeout=5)
            self.assertFalse(t2.is_alive())

            self.assertEqual(results["second"]["status"], "ALREADY_INGESTING")
            self.assertTrue(results["second"].get("duplicate"))

            release_fetch.set()
            t1.join(timeout=5)

            self.assertEqual(results["first"]["status"], "DOWNLOADED")
            self.assertEqual(download_calls, [vid], "Exactly one download run must occur")

        self.assertFalse(is_ingestion_in_flight(vid), "Guard slot must be released in finally after completion")

    def test_04_endpoint_reports_already_processing_for_in_flight_video(self):
        """3b. A repeat POST while a real ingestion is in flight returns a distinct
        already_processing status, not a second processing_queued run."""
        vid = "tt_ep_40001"
        entered_fetch = threading.Event()
        release_fetch = threading.Event()

        def fake_fetch_info(self, video_id):
            entered_fetch.set()
            release_fetch.wait(timeout=5)
            return {"id": vid, "title": "Endpoint Guard Test", "channel_id": "", "channel": "",
                    "duration": 10.0, "upload_date": "", "description": ""}

        def fake_download(self, video_id):
            f = self.storage_dir / f"{video_id}.mp4"
            f.write_bytes(b"x" * 2048)
            return f

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status",
                       side_effect=lambda ids: {i: {"privacy_status": "unlisted"} for i in ids}), \
             patch.object(YouTubeInboxCatcher, "fetch_video_info", fake_fetch_info), \
             patch.object(YouTubeInboxCatcher, "download_video", fake_download), \
             patch("dispatch.ingestion.validator.probe_video",
                   return_value=(True, {"duration": 10.0, "width": 1080, "height": 1920}, None)):

            first = client.post("/api/youtube/ingest", json={"url": f"https://youtu.be/{vid}"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "processing_queued")

            self.assertTrue(entered_fetch.wait(timeout=5), "First ingestion never started")

            second = client.post("/api/youtube/ingest", json={"url": f"https://youtu.be/{vid}"})
            self.assertEqual(second.status_code, 200)
            body = second.json()
            self.assertEqual(body["status"], "already_processing")
            self.assertTrue(body.get("duplicate"))
            self.assertEqual(body["video_id"], vid)

            release_fetch.set()

            # Let the first ingestion thread finish inside this patch context so it
            # cannot leak its download call into a later test's mocks.
            deadline = time.time() + 5
            while time.time() < deadline and is_ingestion_in_flight(vid):
                time.sleep(0.05)
            self.assertFalse(is_ingestion_in_flight(vid), "First ingestion thread did not finish")

    def test_05_download_failure_recorded_and_visible_in_inbox(self):
        """4. A failed download leaves a visible FAILED record with last_error via the inbox API."""
        vid = "tt_ep_50001"
        done = threading.Event()

        def fake_fetch_info(self, video_id):
            raise RuntimeError("unavailable")

        def failing_download(self, video_id):
            raise RuntimeError("ERROR: requested format not available")

        real_ingest = YouTubeInboxCatcher.ingest_video

        def wrapped_ingest(self, url_or_id, **kwargs):
            try:
                return real_ingest(self, url_or_id, **kwargs)
            finally:
                done.set()

        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status",
                       side_effect=lambda ids: {i: {"privacy_status": "unlisted"} for i in ids}), \
             patch.object(YouTubeInboxCatcher, "fetch_video_info", fake_fetch_info), \
             patch.object(YouTubeInboxCatcher, "download_video", failing_download), \
             patch.object(YouTubeInboxCatcher, "ingest_video", wrapped_ingest):

            res = client.post("/api/youtube/ingest", json={"url": f"https://youtu.be/{vid}"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "processing_queued")

            self.assertTrue(done.wait(timeout=10), "Background ingestion did not finish")

        res = client.get("/api/youtube/inbox")
        self.assertEqual(res.status_code, 200)
        items = {i["video_id"]: i for i in res.json()["items"]}
        self.assertIn(vid, items)
        record = items[vid]
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("Download failed", record["last_error"])
        self.assertIn("requested format not available", record["last_error"])

    def test_06_resubmitting_downstream_completed_video_does_not_redownload(self):
        """5. A video already fully processed by downstream workers (COMPLETED) with a valid
        local file is not downloaded again on re-import."""
        vid = "tt_ep_60001"
        fake_file = self.temp_dir / f"{vid}.mp4"
        fake_file.write_bytes(b"v" * 2048)
        db.register_youtube_video(video_id=vid, title="Downstream Completed")
        db.update_youtube_video(vid, status="COMPLETED", local_video_path=str(fake_file))

        with patch.object(YouTubeInboxCatcher, "download_video") as mock_download, \
             patch("dispatch.ingestion.validator.probe_video",
                   return_value=(True, {"duration": 10.0, "width": 1080, "height": 1920}, None)):
            res = client.post("/api/youtube/ingest", json={"url": f"https://youtu.be/{vid}"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["video_id"], vid)
            # Give the background thread time to run the real ingest_video path.
            deadline = time.time() + 2.0
            while time.time() < deadline and not mock_download.called:
                time.sleep(0.05)
            time.sleep(0.3)
            mock_download.assert_not_called()

        record = db.get_youtube_video(vid)
        self.assertEqual(record["status"], "COMPLETED")


def _INGEST_INFLIGHT_LOCK_SAFE():
    from dispatch.youtube_inbox.catcher import _INGEST_INFLIGHT_LOCK
    return _INGEST_INFLIGHT_LOCK


if __name__ == "__main__":
    unittest.main()
