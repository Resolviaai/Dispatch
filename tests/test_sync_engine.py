"""Unit tests for the sync module: reconciliation states, verify-chunk
receipts, YouTube ingestion acknowledgement, and the retired LAN byte path."""
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

    def test_reconcile_reports_missing_then_verified_for_seeded_file(self):
        """Reconciliation stays live after the LAN byte path retirement: it must
        report MISSING until the PC holds verified bytes, and VERIFIED only
        against a size+SHA-256-checked physical file."""
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()
        init_mobile_db()

        test_storage = Path(tempfile.mkdtemp(prefix="dispatch_sync_test_"))
        manager = RecordingSessionManager(storage_dir=test_storage)

        try:
            # 1. Phone-side segment exists (mobile engine stand-in), the laptop
            #    holds nothing yet.
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

            # 2. Reconciliation BEFORE any PC-side state (should report MISSING)
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

            # 3. Seed the PC side directly (LAN transfer retired): a real file on
            #    disk plus a matching verified chunks row.
            final_on_laptop = PROCESSING_DIR / f"{seg_id}.mp4"
            shutil.copyfile(final_phone_path, final_on_laptop)
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions (id, status, notes) VALUES (?, ?, ?)",
                    (sess_id, "processing", "Sync Test Session")
                )
                conn.execute(
                    "INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (seg_id, sess_id, f"{seg_id}.mp4", str(final_on_laptop), sha256, "verified")
                )
                conn.commit()

            # 4. Post-seed reconciliation: laptop reports VERIFIED
            res_rec3 = client.post("/api/sync/reconcile", json=reconcile_payload)
            rec3_data = res_rec3.json()["reconciled"][0]
            self.assertEqual(rec3_data["status"], "VERIFIED")
            self.assertEqual(rec3_data["remote_offset"], total_size)

            # 5. Mark verified on phone and trigger safe retention cleanup
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

    def test_retired_lan_upload_endpoints_return_410(self):
        """The LAN byte path (/upload/init, /upload/chunk) is RETIRED: phone
        segments arrive via YouTube. Every request — valid token, forged token,
        wrong offset, or none — must fail closed with 410 Gone, never a live
        byte receiver."""
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()

        payload = b"TEST_CHUNK_PAYLOAD_OFFSET"
        base_headers = {
            "x-segment-id": "seg_retired_test",
            "x-upload-offset": "100",  # incorrect offset anyway
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_retired_test",
            "content-type": "application/octet-stream"
        }

        for token_header in (
            {"x-auth-token": AUTH_TOKEN},
            {"x-auth-token": "forged_evil_token"},
            {},
        ):
            headers = {**base_headers, **token_header}
            res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
            self.assertEqual(res.status_code, 410, f"chunk must be gone for {token_header}")
            if res.status_code == 410:
                self.assertIn("retired", res.json()["detail"])

        init_res = client.post("/api/sync/upload/init", json={
            "session_id": "sess_retired_test",
            "segment_id": "seg_retired_test",
            "filename": "seg_retired_test.mp4",
            "file_size_bytes": len(payload),
            "sha256_hash": hashlib.sha256(payload).hexdigest(),
            "auth_token": AUTH_TOKEN
        })
        self.assertEqual(init_res.status_code, 410)
        self.assertIn("retired", init_res.json()["detail"])


    def test_verify_chunk_missing_file_with_db_row_returns_missing(self):
        """P0 Regression: Ensure verify-chunk and reconcile never report VERIFIED when file is missing from disk."""
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()

        seg_id = "seg_ghost_file_test"
        dummy_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        nonexistent_path = PROCESSING_DIR / f"{seg_id}.mp4"
        nonexistent_path.unlink(missing_ok=True)

        # Register in database with matching hash, pointing to a nonexistent file
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, status, notes) VALUES (?, ?, ?)",
                ("sess_ghost", "recording", "Ghost Session")
            )
            conn.execute(
                "INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, status) VALUES (?, ?, ?, ?, ?, ?)",
                (seg_id, "sess_ghost", f"{seg_id}.mp4", str(nonexistent_path), dummy_hash, "processed")
            )

        try:
            # 1. verify-chunk must return verified: False, status: MISSING
            res = client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={dummy_hash}&file_size=100",
                headers={"x-auth-token": AUTH_TOKEN}
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertFalse(data["verified"], "Must not verify chunk when physical file is missing from disk")
            self.assertEqual(data["status"], "MISSING")

            # 2. reconcile must not return VERIFIED
            rec_res = client.post(
                "/api/sync/reconcile",
                json={
                    "auth_token": AUTH_TOKEN,
                    "manifest": [{
                        "segment_id": seg_id,
                        "session_id": "sess_ghost",
                        "file_size_bytes": 100,
                        "sha256_hash": dummy_hash
                    }]
                }
            )
            rec_data = rec_res.json()["reconciled"]
            self.assertEqual(len(rec_data), 1)
            self.assertNotEqual(rec_data[0]["status"], "VERIFIED", "Reconcile must not report VERIFIED without physical file")
            self.assertEqual(rec_data[0]["status"], "MISSING")

            # 3. The LAN upload path is retired (covered by
            #    test_retired_lan_upload_endpoints_return_410). The ghost-row
            #    protection lives in verify-chunk (1) and reconcile (2) above,
            #    which must never report VERIFIED without physical bytes.
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM chunks WHERE id = ?", (seg_id,))
                conn.execute("DELETE FROM sessions WHERE id = 'sess_ghost'")

    def test_verify_chunk_modified_bytes_same_size_fails_hash_check(self):
        """Verify that verify-chunk strictly verifies physical file bytes, failing modified files of same size."""
        seg_id = "test_tampered_seg_123"
        correct_hash = hashlib.sha256(b"original clean audio content").hexdigest()
        tampered_bytes = b"tampered evil audio content!"  # exactly same length: 28 bytes
        self.assertEqual(len(b"original clean audio content"), len(tampered_bytes))

        tampered_file = Path("storage/processing") / f"{seg_id}.mp4"
        tampered_file.parent.mkdir(parents=True, exist_ok=True)
        tampered_file.write_bytes(tampered_bytes)

        try:
            with get_db_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO sessions (id, status) VALUES ('sess_tamper', 'active')")
                conn.execute("""
                    INSERT OR REPLACE INTO chunks (id, session_id, filename, filepath, file_hash, duration, status)
                    VALUES (?, 'sess_tamper', ?, ?, ?, 10.0, 'verified')
                """, (seg_id, f"{seg_id}.mp4", str(tampered_file), correct_hash))
                conn.commit()

            # Client queries verify-chunk with expected hash
            res = client.get(
                f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={correct_hash}&file_size={len(tampered_bytes)}",
                headers={"x-auth-token": AUTH_TOKEN}
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertFalse(data["verified"], "Tampered file on disk with identical size must not be verified")
            self.assertEqual(data["status"], "CORRUPT_RETRY_REQUIRED")
        finally:
            tampered_file.unlink(missing_ok=True)
            with get_db_connection() as conn:
                conn.execute("DELETE FROM chunks WHERE id = ?", (seg_id,))

    def test_youtube_ingestion_acknowledgement_requires_disk_file(self):
        """Test that /api/sync/youtube-ack strictly enforces auth, file existence, and HMAC receipt."""
        import hmac
        from dispatch.sync.receiver import get_auth_token
        auth_token = get_auth_token()
        headers = {"x-auth-token": auth_token}

        video_id = "test_yt_vid_98765"
        dispatch_id = "dsp_123456789_seg99"
        dummy_file = Path(tempfile.gettempdir()) / f"dispatch_test_yt_{video_id}.mp4"
        dummy_bytes = b"YOUTUBE_VERIFIED_PC_INGESTED_BYTES"
        dummy_sha256 = hashlib.sha256(dummy_bytes).hexdigest().lower()

        try:
            # 1. Unauthenticated request returns 401
            unauth_res = client.get(f"/api/sync/youtube-ack?video_id={video_id}")
            self.assertEqual(unauth_res.status_code, 401)

            bad_auth_res = client.get(f"/api/sync/youtube-ack?video_id={video_id}", headers={"x-auth-token": "wrong_secret"})
            self.assertEqual(bad_auth_res.status_code, 401)

            # 2. Unregistered video returns acknowledged: False
            res = client.get(f"/api/sync/youtube-ack?video_id={video_id}", headers=headers)
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["acknowledged"])
            self.assertFalse(res.json()["ingested"])

            # 3. Registered in youtube_inbox with status='WAITING' returns acknowledged: False
            with get_db_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO youtube_inbox (video_id, dispatch_id, status, title, source_sha256)
                    VALUES (?, ?, 'WAITING', 'Test YouTube Video', ?)
                """, (video_id, dispatch_id, dummy_sha256))
                conn.commit()

            res = client.get(f"/api/sync/youtube-ack?video_id={video_id}&sha256={dummy_sha256}", headers=headers)
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["acknowledged"])
            self.assertFalse(res.json()["ingested"])

            # 4. Status='DOWNLOADED' but physical file missing returns acknowledged: False
            with get_db_connection() as conn:
                conn.execute("""
                    UPDATE youtube_inbox 
                    SET status = 'DOWNLOADED', local_video_path = ?
                    WHERE video_id = ?
                """, (str(dummy_file), video_id))
                conn.commit()

            dummy_file.unlink(missing_ok=True)
            res = client.get(f"/api/sync/youtube-ack?video_id={video_id}&sha256={dummy_sha256}", headers=headers)
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["acknowledged"], "Must not acknowledge without physical file on PC disk")
            self.assertFalse(res.json()["ingested"])

            # 5. File exists, but with wrong SHA-256 returns acknowledged: False
            dummy_file.write_bytes(dummy_bytes)
            res_wrong_hash = client.get(f"/api/sync/youtube-ack?video_id={video_id}&sha256={'0'*64}", headers=headers)
            self.assertEqual(res_wrong_hash.status_code, 200)
            self.assertFalse(res_wrong_hash.json()["acknowledged"])
            self.assertEqual(res_wrong_hash.json().get("error"), "hash_mismatch")

            # 6. Physical file exists on PC disk with matching hash returns acknowledged: True with HMAC receipt
            res = client.get(f"/api/sync/youtube-ack?video_id={video_id}&sha256={dummy_sha256}", headers=headers)
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.json()["acknowledged"])
            self.assertTrue(res.json()["ingested"])
            self.assertIn("receipt", res.json())

            # Verify the HMAC receipt format and signature. The PC signs with
            # the domain-separated ack key (mirrors Android's
            # deriveAckReceiptKey); the phone additionally accepts legacy
            # token-keyed receipts during the transition.
            receipt = res.json()["receipt"]
            canonical_id = res.json().get("canonical_id")
            expected_msg = f"{canonical_id}:{dummy_sha256}:DOWNLOADED:VERIFIED".encode("utf-8")
            ack_key = hmac.new(auth_token.encode("utf-8"), b"dispatch-ack-receipt-v1", hashlib.sha256).digest()
            expected_receipt = hmac.new(ack_key, expected_msg, hashlib.sha256).hexdigest()
            self.assertEqual(receipt, expected_receipt)

            # 7. Query by dispatch_id also succeeds with verified receipt
            res_disp = client.get(f"/api/sync/youtube-ack?dispatch_id={dispatch_id}&sha256={dummy_sha256}", headers=headers)
            self.assertEqual(res_disp.status_code, 200)
            self.assertTrue(res_disp.json()["acknowledged"])
            self.assertTrue(res_disp.json()["ingested"])
            self.assertIn("receipt", res_disp.json())

        finally:
            dummy_file.unlink(missing_ok=True)
            with get_db_connection() as conn:
                conn.execute("DELETE FROM youtube_inbox WHERE video_id = ?", (video_id,))


if __name__ == "__main__":
    unittest.main()
