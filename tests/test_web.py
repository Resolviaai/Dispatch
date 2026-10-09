import unittest
from fastapi.testclient import TestClient
from dispatch.web.app import app
from dispatch.db import init_db, save_clip, get_clips_for_review

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


if __name__ == "__main__":
    unittest.main()
