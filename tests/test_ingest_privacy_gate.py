"""Privacy gate regressions: private and unverifiable sources are rejected before
any download; only public/unlisted may proceed (Friends-ready Alpha contract)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatch import db
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher


class TestIngestPrivacyGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="privacy_gate_")
        from dispatch.config import STORAGE_DIR
        self._orig_storage = STORAGE_DIR
        import dispatch.config as cfg
        cfg.STORAGE_DIR = Path(self.tmp.name)
        cfg.DATABASE_DIR = Path(self.tmp.name) / "database"
        cfg.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        db.init_db()

    def tearDown(self):
        import dispatch.config as cfg
        cfg.STORAGE_DIR = self._orig_storage
        self.tmp.cleanup()

    def _ingest(self, verdict):
        downloads = []
        with patch("dispatch.youtube_inbox.oauth.get_youtube_processing_status",
                   side_effect=lambda ids: {i: {"privacy_status": verdict} for i in ids}), \
             patch("dispatch.ingestion.validator.probe_video",
                   lambda f: (True, {"duration": 10.0, "width": 720, "height": 1280}, None)), \
             patch.object(YouTubeInboxCatcher, "fetch_video_info",
                          lambda self, vid: {"id": vid, "title": "t", "duration": 10}), \
             patch.object(YouTubeInboxCatcher, "download_video", self._fake_download(downloads)):
            return YouTubeInboxCatcher().ingest_video("priv_gate_1"), downloads

    def _fake_download(self, downloads):
        tmp = Path(self.tmp.name)
        def fake(self_, vid):
            f = tmp / f"{vid}.mp4"
            f.write_bytes(b"x")
            downloads.append(vid)
            return f
        return fake

    def test_private_source_rejected_before_download(self):
        result, downloads = self._ingest("private")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("Private", result["error"])
        self.assertEqual(downloads, [])  # never touched yt-dlp

    def test_unverifiable_source_rejected(self):
        result, downloads = self._ingest("")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("could not be verified", result["error"])
        self.assertEqual(downloads, [])

    def test_public_and_unlisted_pass_gate(self):
        for verdict in ("public", "unlisted"):
            result, _ = self._ingest(verdict)
            self.assertNotEqual(result.get("status"), "FAILED", msg=verdict)


    def test_configured_cookies_cannot_bypass_private_rejection(self):
        """B1 #7: even with authenticated yt-dlp cookies configured, a PRIVATE verdict
        is rejected before download - yt-dlp access is never treated as visibility."""
        import os
        os.environ["DISPATCH_YTDLP_COOKIES_FILE"] = "cookies.txt"
        try:
            result, downloads = self._ingest("private")
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(downloads, [])
        finally:
            del os.environ["DISPATCH_YTDLP_COOKIES_FILE"]


if __name__ == "__main__":
    unittest.main()
