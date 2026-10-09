"""Unit and integration tests for the Layout Framing Architecture.
Tests source media resolution, streaming, stale render detection, and automatic
re-rendering across dynamic framing modes for BOTH Portrait (9:16) and Landscape (16:9) inputs.
"""
import unittest
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient

from dispatch.config import PROCESSING_DIR, CLIPS_DIR
from dispatch import db
from dispatch.video_engine.renderer import render_clip
from dispatch.ingestion.validator import inspect_video_orientation
from dispatch.web.app import app


class TestLayoutFramingArchitecture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.client = TestClient(app)
        
        # 1. Create a 3-second 16:9 landscape test video (1280x720) with audio
        cls.source_video = PROCESSING_DIR / "test_framing_source_16x9.mp4"
        cmd_land = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "3",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            str(cls.source_video)
        ]
        subprocess.run(cmd_land, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Create a 3-second 9:16 portrait test video (720x1280) with audio
        cls.source_video_portrait = PROCESSING_DIR / "test_framing_source_9x16.mp4"
        cmd_port = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=520",
            "-t", "3",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            str(cls.source_video_portrait)
        ]
        subprocess.run(cmd_port, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        if cls.source_video.exists():
            cls.source_video.unlink()
        if cls.source_video_portrait.exists():
            cls.source_video_portrait.unlink()

    def setUp(self):
        self.session_id = f"sess_test_frame_{Path(self.source_video).stem}"
        self.chunk_id = f"chk_test_frame_{Path(self.source_video).stem}"
        self.clip_id = f"clip_test_frame_{Path(self.source_video).stem}"

        self.chunk_id_port = f"chk_test_port_{Path(self.source_video_portrait).stem}"
        self.clip_id_port = f"clip_test_port_{Path(self.source_video_portrait).stem}"
        
        with db.get_db_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions (id, status)
                VALUES (?, 'completed')
            """, (self.session_id,))

            # 1. Register Landscape Chunk & Clip
            conn.execute("""
                INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, status, duration, width, height, aspect_ratio)
                VALUES (?, ?, ?, ?, 'hash_test_frame', 'processed', 3.0, 1280, 720, '16:9')
            """, (self.chunk_id, self.session_id, self.source_video.name, str(self.source_video)))
            
            conn.execute("""
                INSERT OR REPLACE INTO clips (
                    id, session_id, chunk_id, start_time, end_time, duration,
                    title, hook, description, hashtags, virality_score,
                    layout_mode, rendered_layout_mode, status, publish_mode, platform_targets
                ) VALUES (?, ?, ?, 0.5, 2.5, 2.0,
                    'Testing Layout Architecture', 'Hook text', 'Desc', '#test', 90,
                    'fit_blur', NULL, 'ready_review', 'private', 'youtube,instagram'
                )
            """, (self.clip_id, self.session_id, self.chunk_id))

            # 2. Register Portrait Chunk & Clip
            conn.execute("""
                INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, status, duration, width, height, aspect_ratio)
                VALUES (?, ?, ?, ?, 'hash_test_port', 'processed', 3.0, 720, 1280, '9:16')
            """, (self.chunk_id_port, self.session_id, self.source_video_portrait.name, str(self.source_video_portrait)))

            conn.execute("""
                INSERT OR REPLACE INTO clips (
                    id, session_id, chunk_id, start_time, end_time, duration,
                    title, hook, description, hashtags, virality_score,
                    layout_mode, rendered_layout_mode, status, publish_mode, platform_targets
                ) VALUES (?, ?, ?, 0.5, 2.5, 2.0,
                    'Testing Portrait Layout Architecture', 'Hook text port', 'Desc', '#port', 88,
                    'native_916', NULL, 'ready_review', 'private', 'youtube,instagram'
                )
            """, (self.clip_id_port, self.session_id, self.chunk_id_port))
            conn.commit()

    def tearDown(self):
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM clips WHERE id IN (?, ?)", (self.clip_id, self.clip_id_port))
            conn.execute("DELETE FROM chunks WHERE id IN (?, ?)", (self.chunk_id, self.chunk_id_port))
            conn.execute("DELETE FROM sessions WHERE id = ?", (self.session_id,))
            conn.commit()
        for f in CLIPS_DIR.glob(f"{self.clip_id}*"):
            try:
                f.unlink()
            except Exception:
                pass
        for f in CLIPS_DIR.glob(f"{self.clip_id_port}*"):
            try:
                f.unlink()
            except Exception:
                pass

    def test_01_source_media_endpoint_streams_raw_chunk(self):
        """GET /api/clips/{id}/source streams the raw source media with clip headers."""
        res = self.client.get(f"/api/clips/{self.clip_id}/source")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "video/mp4")
        self.assertEqual(res.headers.get("x-clip-start"), "0.5")
        self.assertEqual(res.headers.get("x-clip-end"), "2.5")
        self.assertEqual(res.headers.get("x-clip-duration"), "2.0")
        self.assertGreater(len(res.content), 1000)

    def test_02_layout_change_persists_and_marks_stale(self):
        """POST /api/clips/{id}/layout updates layout_mode and flags is_render_stale."""
        # 1. Initial render with fit_blur
        render_clip(
            source_video=self.source_video,
            clip_id=self.clip_id,
            start_time=0.5,
            end_time=2.5,
            aspect_ratio="16:9",
            layout_mode="fit_blur"
        )
        clip = db.get_clip_by_id(self.clip_id)
        self.assertEqual(clip["rendered_layout_mode"], "fit_blur")
        self.assertEqual(clip["layout_mode"], "fit_blur")

        # 2. Check detail endpoint
        res = self.client.get(f"/api/clips/{self.clip_id}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["is_render_stale"])

        # 3. User switches to fit_black
        res = self.client.post(f"/api/clips/{self.clip_id}/layout", json={"layout_mode": "fit_black"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["layout_mode"], "fit_black")
        self.assertEqual(data["rendered_layout_mode"], "fit_blur")
        self.assertTrue(data["is_render_stale"])

        # 4. Detail endpoint reflects stale state
        res = self.client.get(f"/api/clips/{self.clip_id}")
        data = res.json()
        self.assertTrue(data["is_render_stale"])
        self.assertEqual(data["layout_mode"], "fit_black")

    def test_03_approve_detects_stale_and_rerenders_from_source(self):
        """Approve automatically re-renders from source when rendered_layout_mode != layout_mode."""
        # Initial render as fit_blur
        render_clip(
            source_video=self.source_video,
            clip_id=self.clip_id,
            start_time=0.5,
            end_time=2.5,
            aspect_ratio="16:9",
            layout_mode="fit_blur"
        )
        
        # User changes framing to fit_black
        self.client.post(f"/api/clips/{self.clip_id}/layout", json={"layout_mode": "fit_black"})
        
        # User approves clip
        res = self.client.post(f"/api/clips/{self.clip_id}/approve", json={
            "title": "Approved Title",
            "layout_mode": "fit_black"
        })
        self.assertEqual(res.status_code, 200)

        # Verify DB updated and rendered_layout_mode is now fit_black
        clip = db.get_clip_by_id(self.clip_id)
        self.assertEqual(clip["status"], "approved")
        self.assertEqual(clip["layout_mode"], "fit_black")
        self.assertEqual(clip["rendered_layout_mode"], "fit_black")
        self.assertTrue(Path(clip["video_path"]).exists())
        self.assertGreater(Path(clip["video_path"]).stat().st_size, 1000)

    def test_04_explicit_render_all_four_landscape_modes(self):
        """POST /api/clips/{id}/render re-renders successfully for all 4 landscape modes."""
        modes = ["crop_916", "fit_blur", "fit_black", "native_169"]
        for mode in modes:
            res = self.client.post(f"/api/clips/{self.clip_id}/render", json={"layout_mode": mode})
            self.assertEqual(res.status_code, 200, f"Render failed for {mode}")
            data = res.json()
            self.assertEqual(data["layout_mode"], mode)
            self.assertEqual(data["rendered_layout_mode"], mode)
            self.assertFalse(data["is_render_stale"])

            clip = db.get_clip_by_id(self.clip_id)
            self.assertEqual(clip["rendered_layout_mode"], mode)
            out_path = Path(clip["video_path"])
            self.assertTrue(out_path.exists())
            self.assertGreater(out_path.stat().st_size, 1000)

            # Probe geometry
            info = inspect_video_orientation(out_path)
            self.assertTrue(info["valid"])
            if mode == "native_169":
                self.assertEqual(info["width"], 1920)
                self.assertEqual(info["height"], 1080)
            else:
                self.assertEqual(info["width"], 1080)
                self.assertEqual(info["height"], 1920)

    def test_05_portrait_input_validates_dynamic_three_options(self):
        """Portrait 9:16 source yields EXACTLY 3 valid options and rejects crop options."""
        res = self.client.get(f"/api/clips/{self.clip_id_port}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["source_orientation"], "portrait")
        modes = data["available_layout_modes"]
        self.assertEqual(len(modes), 3)
        mode_ids = [m["id"] for m in modes]
        self.assertEqual(mode_ids, ["fit_black", "fit_blur", "native_916"])
        
        # Verify target aspect ratios
        aspect_by_id = {m["id"]: m["target_aspect"] for m in modes}
        self.assertEqual(aspect_by_id["fit_black"], "16:9")
        self.assertEqual(aspect_by_id["fit_blur"], "16:9")
        self.assertEqual(aspect_by_id["native_916"], "9:16")

    def test_06_portrait_input_rejects_invalid_modes(self):
        """Portrait 9:16 source rejects crop_916 and landscape modes."""
        for invalid_mode in ("crop_916", "crop_follow", "landscape", "native_169"):
            res = self.client.post(f"/api/clips/{self.clip_id_port}/layout", json={"layout_mode": invalid_mode})
            self.assertEqual(res.status_code, 400, f"Mode {invalid_mode} should have been rejected for portrait source")

    def test_07_portrait_input_renders_all_three_modes_with_exact_geometry(self):
        """Portrait 9:16 source renders fit_black (16:9), fit_blur (16:9), and native_916 (9:16)."""
        for mode in ("fit_black", "fit_blur", "native_916"):
            res = self.client.post(f"/api/clips/{self.clip_id_port}/render", json={"layout_mode": mode})
            self.assertEqual(res.status_code, 200, f"Render failed for portrait {mode}")
            clip = db.get_clip_by_id(self.clip_id_port)
            out_path = Path(clip["video_path"])
            self.assertTrue(out_path.exists())
            
            info = inspect_video_orientation(out_path)
            self.assertTrue(info["valid"])
            if mode in ("fit_black", "fit_blur"):
                # Output must be 16:9 widescreen (1920x1080)
                self.assertEqual(info["width"], 1920, f"{mode} width should be 1920")
                self.assertEqual(info["height"], 1080, f"{mode} height should be 1080")
                self.assertEqual(info["orientation"], "landscape")
            else:
                # Output must be 9:16 vertical (1080x1920)
                self.assertEqual(info["width"], 1080, f"{mode} width should be 1080")
                self.assertEqual(info["height"], 1920, f"{mode} height should be 1920")
                self.assertEqual(info["orientation"], "portrait")


if __name__ == "__main__":
    unittest.main()
