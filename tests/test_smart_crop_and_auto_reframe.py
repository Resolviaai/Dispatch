"""Unit tests for Smart Crop, Auto-Reframe, and Subject Anchor Tracking.
Tests the fail-soft tracker, mathematical bounds, API persistence, and FFmpeg filter compilation.
"""
import unittest
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient

from dispatch.config import PROCESSING_DIR
from dispatch import db
from dispatch.video_engine.reframer import build_filter_complex
from dispatch.video_engine.tracker import detect_subject_crop_anchor
from dispatch.web.app import app


class TestSmartCropAndAutoReframe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.client = TestClient(app)

        cls.test_video = PROCESSING_DIR / "test_tracker_synth_16x9.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
            "-t", "2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            str(cls.test_video)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        if cls.test_video.exists():
            cls.test_video.unlink()

    def setUp(self):
        self.session_id = f"sess_auto_{Path(self.test_video).stem}"
        self.chunk_id = f"chk_auto_{Path(self.test_video).stem}"
        self.clip_id = f"clip_auto_{Path(self.test_video).stem}"

        with db.get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO sessions (id, status) VALUES (?, 'completed')", (self.session_id,))
            conn.execute("""
                INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, status, duration, width, height, aspect_ratio)
                VALUES (?, ?, ?, ?, 'hash_auto', 'processed', 2.0, 640, 360, '16:9')
            """, (self.chunk_id, self.session_id, self.test_video.name, str(self.test_video)))
            conn.execute("""
                INSERT OR REPLACE INTO clips (
                    id, session_id, chunk_id, start_time, end_time, duration,
                    title, hook, description, hashtags, virality_score,
                    layout_mode, crop_anchor_x, status, publish_mode
                ) VALUES (?, ?, ?, 0.0, 2.0, 2.0,
                    'Auto Reframe Test', 'Hook', 'Desc', '#test', 85,
                    'fit_blur', 0.5, 'ready_review', 'private'
                )
            """, (self.clip_id, self.session_id, self.chunk_id))

    def test_01_filter_complex_compiles_custom_crop_anchor(self):
        """Reframer builds exact mathematical crop expression for custom anchor."""
        # 1. Default center anchor (0.5)
        f_center, _ = build_filter_complex("16:9", "crop_916", crop_anchor_x=0.5)
        self.assertIn("trunc((iw-ow)*0.5000)", f_center)

        # 2. Left anchor (0.25)
        f_left, _ = build_filter_complex("16:9", "crop_916", crop_anchor_x=0.25)
        self.assertIn("trunc((iw-ow)*0.2500)", f_left)

        # 3. Right anchor (0.75)
        f_right, _ = build_filter_complex("16:9", "crop_916", crop_anchor_x=0.75)
        self.assertIn("trunc((iw-ow)*0.7500)", f_right)

        # 4. Out-of-bounds anchor clamped safely to [0.0, 1.0]
        f_clamped, _ = build_filter_complex("16:9", "crop_916", crop_anchor_x=1.85)
        self.assertIn("trunc((iw-ow)*1.0000)", f_clamped)

    def test_02_tracker_fail_soft_on_missing_or_non_face_video(self):
        """Tracker smoothly returns 0.5 when video missing or has no detectable faces."""
        # Missing file
        anchor_missing = detect_subject_crop_anchor("nonexistent_video.mp4")
        self.assertEqual(anchor_missing, 0.5)

        # Synthetic pattern video without human faces
        anchor_synth = detect_subject_crop_anchor(self.test_video)
        self.assertEqual(anchor_synth, 0.5)

    def test_03_api_persists_manual_crop_anchor(self):
        """POST /api/clips/{id}/layout stores and returns custom crop_anchor_x."""
        res = self.client.post(
            f"/api/clips/{self.clip_id}/layout",
            json={"layout_mode": "crop_916", "crop_anchor_x": 0.32}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["layout_mode"], "crop_916")
        self.assertAlmostEqual(data["crop_anchor_x"], 0.32, places=2)

        clip = db.get_clip_by_id(self.clip_id)
        self.assertAlmostEqual(clip["crop_anchor_x"], 0.32, places=2)

    def test_04_api_auto_reframe_endpoint_execution(self):
        """POST /api/clips/{id}/auto_reframe executes face tracker and saves anchor."""
        res = self.client.post(f"/api/clips/{self.clip_id}/auto_reframe")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["layout_mode"], "crop_916")
        # For synthetic video with no face, safe fallback is 0.5
        self.assertEqual(data["crop_anchor_x"], 0.5)

        # Verify DB persisted
        clip = db.get_clip_by_id(self.clip_id)
        self.assertEqual(clip["layout_mode"], "crop_916")
        self.assertEqual(clip["crop_anchor_x"], 0.5)


if __name__ == "__main__":
    unittest.main()
