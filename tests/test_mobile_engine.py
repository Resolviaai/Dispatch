"""Unit test for Phase 1: Mobile recording, segmentation, crash recovery, and retention."""
import unittest
import tempfile
import shutil
from pathlib import Path

from dispatch_mobile.db import init_mobile_db, get_mobile_db, calculate_sha256
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.recovery import recover_mobile_state
from dispatch_mobile.retention import cleanup_verified_segments


class TestMobileEngine(unittest.TestCase):
    def setUp(self):
        init_mobile_db()

    def test_mobile_engine(self):
        test_storage = Path(tempfile.mkdtemp(prefix="dispatch_mob_test_"))
        manager = RecordingSessionManager(storage_dir=test_storage)

        try:
            # 2. Start session and record segment 1
            sess_id = manager.start_session(notes="Test work session")
            self.assertEqual(manager.current_session_id, sess_id)

            # Write simulated bytes to segment 1 .tmp
            seg1_tmp = manager.current_tmp_path
            with open(seg1_tmp, "wb") as f:
                f.write(b"SIMULATED_VIDEO_DATA_SEGMENT_1_AABBCCDD")

            # 3. Roll to segment 2 (atomically finalizes segment 1)
            seg2_info = manager.start_next_segment()
            seg1_final = test_storage / f"{sess_id}_seg_0001.mp4"

            self.assertTrue(seg1_final.exists(), "Segment 1 was not finalized to .mp4")
            self.assertFalse(seg1_tmp.exists(), "Segment 1 .tmp should have been renamed")
            self.assertGreater(seg1_final.stat().st_size, 0)

            # Check DB state for segment 1
            with get_mobile_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status, sha256_hash FROM mobile_segments WHERE segment_id = ?",
                               (f"{sess_id}_seg_0001",))
                row = cursor.fetchone()
                self.assertEqual(row["status"], "QUEUED_FOR_UPLOAD")
                self.assertIsNotNone(row["sha256_hash"])

            # Write simulated bytes to segment 2 .tmp
            seg2_tmp = manager.current_tmp_path
            with open(seg2_tmp, "wb") as f:
                f.write(b"SIMULATED_VIDEO_DATA_SEGMENT_2_EEFFGGHH")

            # 4. SIMULATE PHONE BATTERY DEATH / CRASH:
            # Instead of calling manager.stop_session(), simulate the process dying mid-recording of segment 2!
            del manager

            # 5. TEST CRASH RECOVERY ON BOOT
            recovery_report = recover_mobile_state(storage_dir=test_storage)
            self.assertIn(f"{sess_id}_seg_0002", recovery_report["recovered_segments"])
            self.assertIn(sess_id, recovery_report["sealed_sessions"])

            seg2_final = test_storage / f"{sess_id}_seg_0002.mp4"
            self.assertTrue(seg2_final.exists(), "Interrupted segment 2 was not recovered on boot")
            self.assertFalse(seg2_tmp.exists(), "Interrupted segment 2 .tmp should be finalized to .mp4")

            # Check outbox has both segments
            with get_mobile_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT segment_id FROM mobile_outbox WHERE session_id = ?", (sess_id,))
                outbox_segs = [r["segment_id"] for r in cursor.fetchall()]
                self.assertIn(f"{sess_id}_seg_0001", outbox_segs)
                self.assertIn(f"{sess_id}_seg_0002", outbox_segs)

            # 6. TEST RETENTION CLEANUP
            # Segment 1 is not verified yet -> must NOT be deleted
            deleted = cleanup_verified_segments()
            self.assertEqual(deleted, 0)
            self.assertTrue(seg1_final.exists(), "Unverified segment was prematurely deleted!")

            # Mark Segment 1 as VERIFIED_BY_LAPTOP
            with get_mobile_db() as conn:
                conn.execute("""
                    UPDATE mobile_segments 
                    SET status = 'VERIFIED_BY_LAPTOP' 
                    WHERE segment_id = ?
                """, (f"{sess_id}_seg_0001",))

            # Now cleanup should safely delete only Segment 1
            deleted = cleanup_verified_segments()
            self.assertEqual(deleted, 1)
            self.assertFalse(seg1_final.exists(), "Verified segment was not cleaned up")
            self.assertTrue(seg2_final.exists(), "Unverified segment 2 must still exist!")

        finally:
            shutil.rmtree(test_storage, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

