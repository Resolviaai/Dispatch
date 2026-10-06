"""Unit test for Phase 2: Resumable phone-to-laptop sync and reconciliation."""
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


def test_resumable_sync_and_reconcile():
    import os
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
        assert final_phone_path.exists()

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
        assert res.status_code == 200
        rec_data = res.json()["reconciled"][0]
        assert rec_data["status"] == "MISSING"
        assert rec_data["remote_offset"] == 0

        # 3. Initialize upload on laptop
        init_res = client.post("/api/sync/upload/init", json={
            "session_id": sess_id,
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": total_size,
            "sha256_hash": sha256,
            "auth_token": AUTH_TOKEN
        })
        assert init_res.status_code == 200
        assert init_res.json()["remote_offset"] == 0

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
        assert res_chunk1.status_code == 200
        assert res_chunk1.json()["remote_offset"] == half_size
        assert res_chunk1.json()["verified"] is False

        # 5. SIMULATE NETWORK INTERUPTION AT 50%
        # Partial file must exist on laptop disk
        part_on_laptop = INCOMING_DIR / f"{seg_id}.part"
        assert part_on_laptop.exists()
        assert part_on_laptop.stat().st_size == half_size

        # Test reconciliation during interruption (should report PARTIAL at exactly half_size)
        res_rec2 = client.post("/api/sync/reconcile", json=reconcile_payload)
        rec2_data = res_rec2.json()["reconciled"][0]
        assert rec2_data["status"] == "PARTIAL"
        assert rec2_data["remote_offset"] == half_size

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
        assert res_chunk2.status_code == 200
        res_chunk2_data = res_chunk2.json()
        assert res_chunk2_data["remote_offset"] == total_size
        assert res_chunk2_data["verified"] is True
        assert not part_on_laptop.exists(), ".part file should be atomically renamed"

        # Final file should now exist on laptop
        final_on_laptop = INCOMING_DIR / f"{seg_id}.mp4"
        assert final_on_laptop.exists()
        assert final_on_laptop.stat().st_size == total_size

        # Check laptop DB has verified chunk
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, file_hash FROM chunks WHERE filename = ?", (f"{seg_id}.mp4",))
            chunk_row = cursor.fetchone()
            assert chunk_row is not None
            assert chunk_row["file_hash"] == sha256

        # 7. Post-upload reconciliation: laptop reports VERIFIED
        res_rec3 = client.post("/api/sync/reconcile", json=reconcile_payload)
        rec3_data = res_rec3.json()["reconciled"][0]
        assert rec3_data["status"] == "VERIFIED"
        assert rec3_data["remote_offset"] == total_size

        # 8. Mark verified on phone and trigger safe retention cleanup
        with get_mobile_db() as conn:
            conn.execute("""
                UPDATE mobile_segments 
                SET status = 'VERIFIED_BY_LAPTOP' 
                WHERE segment_id = ?
            """, (seg_id,))

        cleaned = cleanup_verified_segments()
        assert cleaned == 1
        assert not final_phone_path.exists(), "Phone media file should be safely deleted after verification"

        # Clean up test artifact on laptop
        final_on_laptop.unlink()

        print("ALL RESUMABLE SYNC & RECONCILIATION TESTS (PHASE 2) PASSED 100% SUCCESSFULLY!")

    finally:
        shutil.rmtree(test_storage, ignore_errors=True)


if __name__ == "__main__":
    test_resumable_sync_and_reconcile()
