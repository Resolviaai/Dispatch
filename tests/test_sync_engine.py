"""Unit test for Phase 2: Resumable phone-to-laptop sync and reconciliation."""
import os
import unittest
import tempfile
import shutil
import hashlib
from pathlib import Path
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.db import init_db, get_db_connection
from dispatch_mobile.db import init_mobile_db, get_mobile_db, calculate_sha256
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.retention import cleanup_verified_segments

client = TestClient(app)

AUTH_TOKEN = "dispatch_paired_secret_default"


class TestSyncEngine(unittest.TestCase):

    def test_resumable_sync_and_reconcile(self):
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()
        init_mobile_db()

        test_storage = Path(tempfile.mkdtemp(prefix="dispatch_sync_test_"))
        manager = RecordingSessionManager(storage_dir=test_storage)

        try:
            # 1. Mobile starts session and generates a 40 KB test segment
            sess_id = manager.start_session(notes="Sync Test Session")
            seg_tmp = manager.current_tmp_path

            test_payload = b"DISPATCH_RELIABLE_PAYLOAD_CHUNK_" * 1280  # ~40,960 bytes
            with open(seg_tmp, "wb") as f:
                f.write(test_payload)

            # Finalize segment on phone
            seg_info = manager.finalize_current_segment()
            seg_id = seg_info["segment_id"]
            total_size = seg_info["size_bytes"]
            sha256 = seg_info["sha256"]

            final_phone_path = test_storage / f"{seg_id}.mp4"
            self.assertTrue(final_phone_path.exists())

            # 2. Test Reconciliation BEFORE upload (should report MISSING)
            reconcile_payload = {
                "auth_token": AUTH_TOKEN,
                "manifest": [
                    {
                        "segment_id": seg_id,
                        "session_id": sess_id,
                        "file_size_bytes": total_size,
                        "sha256_hash": sha256
                    }
                ]
            }
            res = client.post("/api/sync/reconcile", json=reconcile_payload)
            self.assertEqual(res.status_code, 200)
            rec_data = res.json()["reconciled"][0]
            self.assertEqual(rec_data["status"], "MISSING")
            self.assertEqual(rec_data["remote_offset"], 0)

            # 3. Initialize upload on laptop
            init_res = client.post("/api/sync/upload/init", json={
                "session_id": sess_id,
                "segment_id": seg_id,
                "filename": f"{seg_id}.mp4",
                "file_size_bytes": total_size,
                "sha256_hash": sha256,
                "auth_token": AUTH_TOKEN
            })
            self.assertEqual(init_res.status_code, 200)
            self.assertEqual(init_res.json()["remote_offset"], 0)

            # 4. Upload FIRST CHUNK (approx 50% of the file: 20,480 bytes)
            half_size = total_size // 2
            chunk1 = test_payload[:half_size]

            headers1 = {
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(total_size),
                "x-sha256": sha256,
                "x-session-id": sess_id,
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream"
            }
            res_chunk1 = client.patch("/api/sync/upload/chunk", content=chunk1, headers=headers1)
            self.assertEqual(res_chunk1.status_code, 200)
            self.assertEqual(res_chunk1.json()["remote_offset"], half_size)
            self.assertFalse(res_chunk1.json()["verified"])

            # 5. SIMULATE NETWORK INTERRUPTION AT 50%
            # Partial file must exist on laptop disk
            part_on_laptop = INCOMING_DIR / f"{seg_id}.part"
            self.assertTrue(part_on_laptop.exists())
            self.assertEqual(part_on_laptop.stat().st_size, half_size)

            # Test reconciliation during interruption (should report PARTIAL at exactly half_size)
            res_rec2 = client.post("/api/sync/reconcile", json=reconcile_payload)
            rec2_data = res_rec2.json()["reconciled"][0]
            self.assertEqual(rec2_data["status"], "PARTIAL")
            self.assertEqual(rec2_data["remote_offset"], half_size)

            # 6. RESUME UPLOAD: Upload SECOND CHUNK from byte offset half_size
            chunk2 = test_payload[half_size:]
            headers2 = {
                "x-segment-id": seg_id,
                "x-upload-offset": str(half_size),
                "x-file-size": str(total_size),
                "x-sha256": sha256,
                "x-session-id": sess_id,
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream"
            }
            res_chunk2 = client.patch("/api/sync/upload/chunk", content=chunk2, headers=headers2)
            self.assertEqual(res_chunk2.status_code, 200)
            res_chunk2_data = res_chunk2.json()
            self.assertEqual(res_chunk2_data["remote_offset"], total_size)
            self.assertTrue(res_chunk2_data["verified"])
            self.assertFalse(part_on_laptop.exists(), ".part file should be atomically renamed")

            # Final file should now exist on laptop (in PROCESSING_DIR or INCOMING_DIR)
            final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
            final_inc = INCOMING_DIR / f"{seg_id}.mp4"
            final_on_laptop = final_proc if final_proc.exists() else final_inc
            self.assertTrue(final_on_laptop.exists())
            self.assertEqual(final_on_laptop.stat().st_size, total_size)

            # Check laptop DB has verified chunk
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status, file_hash FROM chunks WHERE filename = ?", (f"{seg_id}.mp4",))
                chunk_row = cursor.fetchone()
                self.assertIsNotNone(chunk_row)
                self.assertEqual(chunk_row["file_hash"], sha256)

            # 7. Post-upload reconciliation: laptop reports VERIFIED
            res_rec3 = client.post("/api/sync/reconcile", json=reconcile_payload)
            rec3_data = res_rec3.json()["reconciled"][0]
            self.assertEqual(rec3_data["status"], "VERIFIED")
            self.assertEqual(rec3_data["remote_offset"], total_size)

            # 8. Mark verified on phone and trigger safe retention cleanup
            with get_mobile_db() as conn:
                conn.execute("""
                    UPDATE mobile_segments 
                    SET status = 'VERIFIED_BY_LAPTOP' 
                    WHERE segment_id = ?
                """, (seg_id,))

            cleaned = cleanup_verified_segments()
            self.assertEqual(cleaned, 1)
            self.assertFalse(final_phone_path.exists(), "Phone media file should be safely deleted after verification")

            # Clean up test artifact on laptop
            final_on_laptop.unlink(missing_ok=True)

        finally:
            shutil.rmtree(test_storage, ignore_errors=True)

    def test_verify_chunk_cryptographic_receipt(self):
        """Explicit cryptographic proof-of-receipt endpoint (/api/sync/verify-chunk)."""
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()

        # 1. Unauthenticated request must return 401
        res_unauth = client.get("/api/sync/verify-chunk?segment_id=test_dummy&sha256=abcdef")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Non-existent segment returns status: MISSING
        res_missing = client.get(
            "/api/sync/verify-chunk?segment_id=non_existent_seg&sha256=abcdef",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res_missing.status_code, 200)
        self.assertFalse(res_missing.json()["verified"])
        self.assertEqual(res_missing.json()["status"], "MISSING")

        # 3. Create test file in PROCESSING_DIR to test verified receipt
        seg_id = "test_verified_receipt_seg"
        test_file = PROCESSING_DIR / f"{seg_id}.mp4"
        test_content = b"TEST_VERIFIED_RECEIPT_CONTENT_DISPATCH_12345"
        hasher = hashlib.sha256(test_content)
        expected_hash = hasher.hexdigest()
        with open(test_file, "wb") as f:
            f.write(test_content)

        try:
            # Hash matches -> VERIFIED
            res_verified = client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={expected_hash}&file_size={len(test_content)}",
                headers={"x-auth-token": AUTH_TOKEN}
            )
            self.assertEqual(res_verified.status_code, 200)
            self.assertTrue(res_verified.json()["verified"])
            self.assertEqual(res_verified.json()["status"], "VERIFIED")

            # Hash mismatch -> CORRUPT_RETRY_REQUIRED
            res_corrupt = client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256=0000000000000000000000000000000000000000000000000000000000000000&file_size={len(test_content)}",
                headers={"x-auth-token": AUTH_TOKEN}
            )
            self.assertEqual(res_corrupt.status_code, 200)
            self.assertFalse(res_corrupt.json()["verified"])
            self.assertEqual(res_corrupt.json()["status"], "CORRUPT_RETRY_REQUIRED")
        finally:
            test_file.unlink(missing_ok=True)

    def test_chunk_upload_offset_mismatch_returns_409(self):
        """Chunk upload with mismatched offset must return HTTP 409 Conflict."""
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()

        seg_id = "seg_offset_mismatch_test"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        part_file.unlink(missing_ok=True)

        payload = b"TEST_CHUNK_PAYLOAD_OFFSET"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "100",  # Incorrect offset (server has 0 bytes)
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_offset_test",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream"
        }
        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Offset mismatch", res.json()["detail"])

    def test_chunk_upload_unauthenticated_returns_401(self):
        """Chunk upload without auth token returns HTTP 401 Unauthorized."""
        payload = b"TEST_PAYLOAD"
        headers = {
            "x-segment-id": "seg_unauth_test",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_unauth",
            "content-type": "application/octet-stream"
        }
        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
