"""Unit tests for automatic video orientation normalization and structured logging."""
import unittest
import subprocess
import logging
from pathlib import Path
from dispatch.config import PROCESSING_DIR
from dispatch.ingestion.validator import inspect_video_orientation, normalize_video_orientation, probe_video


class TestOrientationNormalization(unittest.TestCase):
    def setUp(self):
        PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
        self.test_upright = PROCESSING_DIR / "test_upright.mp4"
        self.test_rotated = PROCESSING_DIR / "test_rotated_90.mp4"

        # 1. Generate 1.0s 1920x1080 video with rotate=0
        cmd_upright = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000",
            "-t", "1.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(self.test_upright)
        ]
        subprocess.run(cmd_upright, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Generate 1.0s video with display matrix rotation=90 (simulating phone recording)
        cmd_rotated = [
            "ffmpeg", "-y",
            "-display_rotation", "90",
            "-i", str(self.test_upright),
            "-c", "copy",
            str(self.test_rotated)
        ]
        subprocess.run(cmd_rotated, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def tearDown(self):
        if self.test_upright.exists():
            self.test_upright.unlink(missing_ok=True)
        if self.test_rotated.exists():
            self.test_rotated.unlink(missing_ok=True)

    def test_inspect_upright_video(self):
        info = inspect_video_orientation(self.test_upright)
        self.assertTrue(info["valid"])
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["height"], 1080)
        self.assertEqual(info["norm_rotation"], 0)
        self.assertEqual(info["orientation"], "landscape")

    def test_inspect_rotated_video(self):
        info = inspect_video_orientation(self.test_rotated)
        self.assertTrue(info["valid"])
        self.assertEqual(info["width"], 1920)
        self.assertEqual(info["height"], 1080)
        self.assertEqual(info["norm_rotation"], 90)
        # In a 90-degree rotated video, effective width/height are swapped -> portrait
        self.assertEqual(info["effective_width"], 1080)
        self.assertEqual(info["effective_height"], 1920)
        self.assertEqual(info["orientation"], "portrait")

    def test_normalize_rotated_video_transforms_to_physically_upright(self):
        with self.assertLogs("dispatch.ingestion.validator", level=logging.INFO) as log_ctx:
            success, norm_path, info = normalize_video_orientation(self.test_rotated, chunk_id="chk_orient_test")
            self.assertTrue(success)
            self.assertTrue(norm_path.exists())

            # Check that structured logs were emitted
            log_output = "\n".join(log_ctx.output)
            self.assertIn("MEDIA_ORIENTATION_DETECTED", log_output)
            self.assertIn("MEDIA_ORIENTATION_NORMALIZED", log_output)
            self.assertIn("chk_orient_test", log_output)

            # Check that resulting video is physically 1080x1920 with 0 rotation
            final_info = inspect_video_orientation(norm_path)
            self.assertEqual(final_info["width"], 1080)
            self.assertEqual(final_info["height"], 1920)
            self.assertEqual(final_info["norm_rotation"], 0)
            self.assertEqual(final_info["orientation"], "portrait")

    def test_probe_video_auto_normalizes(self):
        is_valid, meta, err = probe_video(self.test_rotated, auto_normalize=True, chunk_id="chk_probe_test")
        self.assertTrue(is_valid, f"Probe error: {err}")
        self.assertEqual(meta["width"], 1080)
        self.assertEqual(meta["height"], 1920)
        self.assertEqual(meta["aspect_ratio"], "9:16")


if __name__ == "__main__":
    unittest.main()
