"""Test suite for Dispatch ingestion and validation."""
import unittest
import subprocess
from pathlib import Path
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.ingestion.validator import probe_video, calculate_file_hash
from dispatch.ingestion.watcher import process_incoming_file
from dispatch.db import init_db, get_db_connection


class TestIngestion(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_ingestion(self):
        test_video = INCOMING_DIR / "unit_test_video.mp4"
        
        # Generate 2-second 1080p landscape test video with sine audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000",
            "-t", "2",
            "-c:v", "libx264",
            "-c:a", "aac",
            str(test_video)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        # 1. Test probe
        is_valid, meta, err = probe_video(test_video)
        self.assertTrue(is_valid, f"Validation failed: {err}")
        self.assertEqual(meta["aspect_ratio"], "16:9", f"Expected 16:9, got {meta['aspect_ratio']}")
        self.assertTrue(meta["has_audio"], "Audio track missing")
        self.assertGreater(meta["duration"], 1.8, f"Duration unexpected: {meta['duration']}")
        
        # 2. Test hash
        f_hash = calculate_file_hash(test_video)
        self.assertEqual(len(f_hash), 32, f"Invalid hash: {f_hash}")
        
        # 3. Test staging via watcher
        result = process_incoming_file(test_video)
        self.assertIsNotNone(result, "Failed to process incoming file")
        self.assertTrue(result["filepath"].exists(), "Staged file not found in processing")
        self.assertEqual(result["filepath"].parent, PROCESSING_DIR, "File was not moved to processing")
        self.assertFalse(test_video.exists(), "Original file should have been moved")
        
        # Clean up test artifact
        result["filepath"].unlink()


if __name__ == "__main__":
    unittest.main()

