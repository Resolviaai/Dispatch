import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from dispatch.web.app import app
from dispatch.db import init_db, save_clip, update_clip_media

client = TestClient(app)

class TestWebDashboard(unittest.TestCase):
    def test_web_dashboard(self):
        init_db()

        # 1. Test homepage
        res = client.get("/")
        self.assertEqual(res.status_code, 200, f"Expected 200, got {res.status_code}")
        self.assertIn("Dispatch", res.text)

        # 2. Test status endpoint
        res = client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("disk_free_gb", data)
        self.assertIn("ready_clips", data)

        # 3. Create a test clip and verify list endpoint
        clip_id = save_clip(
            session_id=None,
            chunk_id=None,
            start_time=1.0,
            end_time=25.0,
            title="Web test clip title",
            hook="Opening test",
            description="Description",
            hashtags="#Test",
            virality_score=85,
            layout_mode="fit_blur"
        )

        res = client.get("/api/clips")
        self.assertEqual(res.status_code, 200)
        clips_data = res.json()
        clip_ids = [c["id"] for c in clips_data["ready"]]
        self.assertIn(clip_id, clip_ids, f"Clip {clip_id} not found in ready list")

        # 4. Test approval
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            output = Path(tmp) / "rendered.mp4"
            source.write_bytes(b"test source")

            def render_for_test(**kwargs):
                output.write_bytes(b"test rendered media")
                update_clip_media(kwargs["clip_id"], str(output), None, kwargs["layout_mode"])
                return {"video_path": str(output)}

            with patch("dispatch.web.app.resolve_clip_source_path", return_value=source), \
                 patch("dispatch.web.app.render_clip", side_effect=render_for_test), \
                 patch("dispatch.ingestion.validator.probe_video", return_value=(True, {"duration": 24.0}, None)):
                res = client.post(f"/api/clips/{clip_id}/approve", json={
                    "title": "Approved Title",
                    "hashtags": "#Approved",
                    "publish_mode": "private",
                    "platforms": "youtube,instagram"
                })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "success")

        # 5. Test pipeline jobs endpoint
        res = client.get("/api/pipeline/jobs")
        self.assertEqual(res.status_code, 200)
        self.assertIn("jobs", res.json())

        # 6. Test integrations status endpoint
        res = client.get("/api/integrations/status")
        self.assertEqual(res.status_code, 200)
        self.assertIn("youtube", res.json())

    def test_lan_peer_media_access(self):
        """Verify that LAN peers can access clip media and thumbnails without Authorization headers."""
        from fastapi.testclient import TestClient
        from dispatch.web.app import app, CLIPS_DIR

        test_file = CLIPS_DIR / "test_thumb_auth.jpg"
        test_file.write_bytes(b"dummy jpeg content")
        try:
            # 1. LAN client without auth headers should receive 200 OK (not 401)
            lan_client = TestClient(app, client=("192.168.0.105", 54321))
            res = lan_client.get(f"/clips/{test_file.name}")
            self.assertEqual(res.status_code, 200)

            # 2. External untrusted IP without auth headers should receive 401
            ext_client = TestClient(app, client=("8.8.8.8", 54321))
            res = ext_client.get(f"/clips/{test_file.name}")
            self.assertEqual(res.status_code, 401)
        finally:
            if test_file.exists():
                test_file.unlink()


if __name__ == "__main__":
    unittest.main()
