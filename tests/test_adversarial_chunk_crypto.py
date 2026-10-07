"""Adversarial Stress Test Suite: Chunked Upload and Cryptographic Verification Engine.

Tests:
1. Chunk offset mismatch: Verify PATCH /api/sync/upload/chunk returns 409 Conflict
   when client offset does not match server partial file size across multiple offset drift scenarios.
2. Unauthenticated chunk upload: Verify PATCH /api/sync/upload/chunk strictly rejects
   requests without valid x-auth-token with HTTP 401 Unauthorized (missing, empty, forged, malformed).
3. Final chunk checksum tampering: Verify corrupted chunk bytes at completion trigger
   HTTP 422 Unprocessable Entity and immediately purge/unlink the partial file from disk.
4. Cryptographic proof endpoint /api/sync/verify-chunk: Test valid hash vs tampered hash vs missing file,
   size mismatch, database fallback, unauthenticated access, and directory traversal injection.
5. Post-corruption recovery: Verify client can cleanly resume/re-upload after a 422 corruption rejection.
"""
import os
import unittest
import hashlib
import time
from pathlib import Path
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.db import init_db, get_db_connection

client = TestClient(app)
AUTH_TOKEN = "dispatch_paired_secret_adversarial_test"


class TestAdversarialChunkCrypto(unittest.TestCase):

    def setUp(self):
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()
        self.created_files = []

    def tearDown(self):
        for f in self.created_files:
            if isinstance(f, Path) and f.exists():
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

    # =========================================================================
    # 1. CHUNK OFFSET MISMATCH ADVERSARIAL CHALLENGES (HTTP 409)
    # =========================================================================

    def test_01_offset_mismatch_empty_server_nonzero_client_offset(self):
        """Server has 0 bytes, client sends offset > 0 -> must return HTTP 409 Conflict."""
        seg_id = f"adv_offset_empty_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        part_file.unlink(missing_ok=True)

        payload = b"ADVERSARIAL_INITIAL_CHUNK_DATA"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "100",  # Server has 0, client claims 100
            "x-file-size": "1000",
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_adv_offset",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream",
        }

        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 409, f"Expected 409 Conflict, got {res.status_code}: {res.text}")
        self.assertIn("Offset mismatch", res.json().get("detail", ""))
        self.assertIn("client has 100, server has 0", res.json().get("detail", ""))
        self.assertFalse(part_file.exists(), "Partial file should not be created on 409 rejection")

    def test_02_offset_mismatch_replay_zero_offset_against_existing_partial(self):
        """Server has partial bytes, client replays offset 0 -> must return HTTP 409 Conflict."""
        seg_id = f"adv_offset_replay_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        self.created_files.append(part_file)

        # Pre-seed server with 256 bytes
        initial_bytes = b"X" * 256
        with open(part_file, "wb") as f:
            f.write(initial_bytes)

        payload = b"REPLAYED_PAYLOAD_FROM_ZERO"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "0",  # Client tries to replay from offset 0
            "x-file-size": "1000",
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_adv_replay",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream",
        }

        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Offset mismatch: client has 0, server has 256", res.json().get("detail", ""))
        # Verify existing partial file was NOT modified or corrupted
        self.assertEqual(part_file.stat().st_size, 256)
        with open(part_file, "rb") as f:
            self.assertEqual(f.read(), initial_bytes)

    def test_03_offset_mismatch_skip_ahead_beyond_server_offset(self):
        """Server has 256 bytes, client skips ahead to offset 512 -> must return HTTP 409 Conflict."""
        seg_id = f"adv_offset_skip_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        self.created_files.append(part_file)

        initial_bytes = b"Y" * 256
        with open(part_file, "wb") as f:
            f.write(initial_bytes)

        payload = b"SKIPPED_PAYLOAD_AHEAD"
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "512",  # Client skips gap
            "x-file-size": "1000",
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_adv_skip",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream",
        }

        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Offset mismatch: client has 512, server has 256", res.json().get("detail", ""))
        self.assertEqual(part_file.stat().st_size, 256)

    def test_04_offset_mismatch_off_by_one_boundaries(self):
        """Server has 100 bytes: test offset 99 (-1) and offset 101 (+1) -> both must return HTTP 409."""
        seg_id = f"adv_offset_boundary_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        self.created_files.append(part_file)

        with open(part_file, "wb") as f:
            f.write(b"B" * 100)

        # Off by one: 99
        res_under = client.patch("/api/sync/upload/chunk", content=b"A", headers={
            "x-segment-id": seg_id,
            "x-upload-offset": "99",
            "x-file-size": "500",
            "x-sha256": "fakehash",
            "x-session-id": "sess_boundary",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream",
        })
        self.assertEqual(res_under.status_code, 409)
        self.assertIn("client has 99, server has 100", res_under.json().get("detail", ""))

        # Off by one: 101
        res_over = client.patch("/api/sync/upload/chunk", content=b"A", headers={
            "x-segment-id": seg_id,
            "x-upload-offset": "101",
            "x-file-size": "500",
            "x-sha256": "fakehash",
            "x-session-id": "sess_boundary",
            "x-auth-token": AUTH_TOKEN,
            "content-type": "application/octet-stream",
        })
        self.assertEqual(res_over.status_code, 409)
        self.assertIn("client has 101, server has 100", res_over.json().get("detail", ""))

    # =========================================================================
    # 2. UNAUTHENTICATED CHUNK UPLOAD ADVERSARIAL CHALLENGES (HTTP 401)
    # =========================================================================

    def test_05_unauthenticated_chunk_upload_missing_header(self):
        """Request without x-auth-token header must return HTTP 401 Unauthorized."""
        payload = b"UNAUTHORIZED_ATTEMPT_BYTES"
        headers = {
            "x-segment-id": "adv_unauth_no_header",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_unauth",
            "content-type": "application/octet-stream",
        }
        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json().get("detail"), "Authentication token missing")

    def test_06_unauthenticated_chunk_upload_empty_and_whitespace_token(self):
        """Request with empty or whitespace-only token must return HTTP 401 Unauthorized."""
        payload = b"UNAUTHORIZED_BLANK_BYTES"
        base_headers = {
            "x-segment-id": "adv_unauth_blank",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_unauth",
            "content-type": "application/octet-stream",
        }

        # Empty string
        res_empty = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": ""})
        self.assertEqual(res_empty.status_code, 401)
        self.assertEqual(res_empty.json().get("detail"), "Authentication token missing")

        # Whitespace
        res_ws = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "   "})
        self.assertEqual(res_ws.status_code, 401)
        self.assertEqual(res_ws.json().get("detail"), "Authentication token missing")

    def test_07_unauthenticated_chunk_upload_invalid_and_forged_tokens(self):
        """Request with wrong or forged token must return HTTP 401 Unauthorized."""
        payload = b"FORGED_TOKEN_PAYLOAD"
        base_headers = {
            "x-segment-id": "adv_unauth_forged",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_forged",
            "content-type": "application/octet-stream",
        }

        # Completely wrong token
        res_wrong = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "evil_hacker_token"})
        self.assertEqual(res_wrong.status_code, 401)
        self.assertEqual(res_wrong.json().get("detail"), "Invalid authentication token")

        # Bearer prefix with wrong token
        res_bearer_wrong = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "Bearer evil_token_xyz"})
        self.assertEqual(res_bearer_wrong.status_code, 401)
        self.assertEqual(res_bearer_wrong.json().get("detail"), "Invalid authentication token")

        # Verify no file was written to disk
        part_file = INCOMING_DIR / "adv_unauth_forged.part"
        self.assertFalse(part_file.exists())

    # =========================================================================
    # 3. FINAL CHUNK CHECKSUM TAMPERING & PARTIAL FILE DELETION (HTTP 422)
    # =========================================================================

    def test_08_final_chunk_checksum_tampering_multi_chunk_purges_partial_file(self):
        """Multi-chunk upload with corrupted bytes in the final chunk must trigger HTTP 422
        and immediately delete/unlink the partial file from disk.
        """
        seg_id = f"adv_tamper_multi_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        final_inc = INCOMING_DIR / f"{seg_id}.mp4"
        final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.extend([part_file, final_inc, final_proc])

        part_file.unlink(missing_ok=True)
        final_inc.unlink(missing_ok=True)
        final_proc.unlink(missing_ok=True)

        # 3 chunks of 1000 bytes = 3000 bytes total
        genuine_chunk1 = b"A" * 1000
        genuine_chunk2 = b"B" * 1000
        genuine_chunk3 = b"C" * 1000
        full_genuine_file = genuine_chunk1 + genuine_chunk2 + genuine_chunk3
        genuine_sha256 = hashlib.sha256(full_genuine_file).hexdigest()
        total_size = len(full_genuine_file)

        sess_id = f"sess_multi_{seg_id}"

        # Upload Chunk 1 (0 -> 1000)
        res_c1 = client.patch(
            "/api/sync/upload/chunk",
            content=genuine_chunk1,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(total_size),
                "x-sha256": genuine_sha256,
                "x-session-id": sess_id,
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_c1.status_code, 200)
        self.assertEqual(res_c1.json()["remote_offset"], 1000)
        self.assertFalse(res_c1.json()["verified"])
        self.assertTrue(part_file.exists())
        self.assertEqual(part_file.stat().st_size, 1000)

        # Upload Chunk 2 (1000 -> 2000)
        res_c2 = client.patch(
            "/api/sync/upload/chunk",
            content=genuine_chunk2,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "1000",
                "x-file-size": str(total_size),
                "x-sha256": genuine_sha256,
                "x-session-id": sess_id,
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_c2.status_code, 200)
        self.assertEqual(res_c2.json()["remote_offset"], 2000)
        self.assertFalse(res_c2.json()["verified"])
        self.assertTrue(part_file.exists())
        self.assertEqual(part_file.stat().st_size, 2000)

        # Upload Chunk 3 (2000 -> 3000) TAMPERED: inject corrupted bytes
        tampered_chunk3 = b"X" * 1000  # Corrupted bytes instead of genuine "C" * 1000
        res_c3 = client.patch(
            "/api/sync/upload/chunk",
            content=tampered_chunk3,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "2000",
                "x-file-size": str(total_size),
                "x-sha256": genuine_sha256,  # Claims genuine hash, but bytes are tampered
                "x-session-id": sess_id,
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )

        # Assert: server rejects with HTTP 422
        self.assertEqual(res_c3.status_code, 422, f"Expected 422, got {res_c3.status_code}: {res_c3.text}")
        self.assertIn("Checksum mismatch; partial file purged", res_c3.json().get("detail", ""))

        # CRITICAL ASSERTION: The partial file MUST BE IMMEDIATELY UNLINKED / DELETED
        self.assertFalse(part_file.exists(), f"CRITICAL SECURITY FAILURE: Partial file {part_file} was NOT deleted!")

        # Verify no corrupt file was finalized
        self.assertFalse(final_inc.exists(), "Corrupt file was erroneously saved to INCOMING_DIR")
        self.assertFalse(final_proc.exists(), "Corrupt file was erroneously saved to PROCESSING_DIR")

        # Verify no verified chunk registered in database
        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM chunks WHERE filename = ?", (f"{seg_id}.mp4",)).fetchone()
            self.assertIsNone(row, "Corrupt chunk should not be registered in database")

    def test_09_single_chunk_checksum_tampering_purges_partial_file(self):
        """Single-chunk upload with mismatched checksum header must return HTTP 422 and leave no partial file."""
        seg_id = f"adv_tamper_single_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.extend([part_file, final_proc])

        payload = b"SINGLE_CHUNK_TAMPERED_DATA_BYTES" * 10
        fraudulent_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"

        res = client.patch(
            "/api/sync/upload/chunk",
            content=payload,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(len(payload)),
                "x-sha256": fraudulent_sha256,
                "x-session-id": "sess_tamper_single",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("Checksum mismatch; partial file purged", res.json().get("detail", ""))
        self.assertFalse(part_file.exists(), "Partial file must be purged after single-chunk checksum failure")
        self.assertFalse(final_proc.exists())

    # =========================================================================
    # 4. CRYPTOGRAPHIC PROOF ENDPOINT (/api/sync/verify-chunk) CHALLENGES
    # =========================================================================

    def test_10_verify_chunk_cryptographic_proof_valid_hash(self):
        """Valid hash matching physical file returns HTTP 200, verified=True, status='VERIFIED'."""
        seg_id = f"adv_proof_valid_{int(time.time() * 1000)}"
        test_file = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.append(test_file)

        content = b"PROVEN_CRYPTO_VALID_SEGMENT_CONTENT" * 20
        content_hash = hashlib.sha256(content).hexdigest()
        with open(test_file, "wb") as f:
            f.write(content)

        res = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={content_hash}&file_size={len(content)}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("verified"))
        self.assertEqual(data.get("status"), "VERIFIED")
        self.assertEqual(data.get("segment_id"), seg_id)
        self.assertEqual(data.get("size"), len(content))

    def test_11_verify_chunk_cryptographic_proof_tampered_hash(self):
        """Tampered hash on existing physical file returns HTTP 200, verified=False, status='CORRUPT_RETRY_REQUIRED'."""
        seg_id = f"adv_proof_tampered_{int(time.time() * 1000)}"
        test_file = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.append(test_file)

        content = b"GENUINE_CONTENT_TEST_VERIFY" * 20
        with open(test_file, "wb") as f:
            f.write(content)

        tampered_hash = "deadbeef" * 8

        res = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={tampered_hash}&file_size={len(content)}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data.get("verified"))
        self.assertEqual(data.get("status"), "CORRUPT_RETRY_REQUIRED")
        self.assertEqual(data.get("reason"), "sha256_mismatch")

    def test_12_verify_chunk_cryptographic_proof_missing_file(self):
        """Querying a non-existent segment returns HTTP 200, verified=False, status='MISSING'."""
        seg_id = f"adv_proof_ghost_{int(time.time() * 1000)}"
        dummy_hash = hashlib.sha256(b"nonexistent").hexdigest()

        res = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={dummy_hash}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data.get("verified"))
        self.assertEqual(data.get("status"), "MISSING")
        self.assertEqual(data.get("reason"), "file_not_found")

    def test_13_verify_chunk_size_mismatch_returns_retry_required(self):
        """Querying an existing segment with mismatched file_size returns verified=False, status='RETRY_REQUIRED'."""
        seg_id = f"adv_proof_size_{int(time.time() * 1000)}"
        test_file = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.append(test_file)

        content = b"GENUINE_SIZE_CONTENT" * 10
        content_hash = hashlib.sha256(content).hexdigest()
        with open(test_file, "wb") as f:
            f.write(content)

        # Send wrong file_size (len(content) + 50)
        res = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={content_hash}&file_size={len(content) + 50}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data.get("verified"))
        self.assertEqual(data.get("status"), "RETRY_REQUIRED")
        self.assertEqual(data.get("reason"), "size_mismatch")

    def test_14_verify_chunk_unauthenticated_rejected_with_401(self):
        """Querying /api/sync/verify-chunk without auth must return HTTP 401 Unauthorized."""
        res_no_auth = client.get("/api/sync/verify-chunk?segment_id=test_seg&sha256=abc")
        self.assertEqual(res_no_auth.status_code, 401)

        res_bad_auth = client.get(
            "/api/sync/verify-chunk?segment_id=test_seg&sha256=abc",
            headers={"x-auth-token": "bad_token"}
        )
        self.assertEqual(res_bad_auth.status_code, 401)

    def test_15_verify_chunk_directory_traversal_rejected_with_400(self):
        """Malicious segment_id attempting path traversal must return HTTP 400 Bad Request."""
        traversal_attempts = [
            "../../etc/passwd",
            "../secret",
            "..\\windows\\system32",
            "seg/subpath",
            "seg;drop table",
        ]
        for bad_id in traversal_attempts:
            res = client.get(
                f"/api/sync/verify-chunk?segment_id={bad_id}&sha256=abc",
                headers={"x-auth-token": AUTH_TOKEN}
            )
            self.assertEqual(
                res.status_code, 400,
                f"Directory traversal segment_id '{bad_id}' was not rejected with 400 (got {res.status_code})"
            )

    # =========================================================================
    # 5. POST-CORRUPTION CLEAN RECOVERY WORKFLOW
    # =========================================================================

    def test_16_clean_recovery_after_checksum_corruption_failure(self):
        """Verifies full recovery cycle:
        1. Upload fails due to corrupted chunk bytes -> 422, partial file purged.
        2. Client initializes fresh upload and sends clean genuine bytes.
        3. Upload completes successfully -> 200, verified=True.
        4. Mobile client queries /api/sync/verify-chunk -> confirmed VERIFIED.
        """
        seg_id = f"adv_recovery_{int(time.time() * 1000)}"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.extend([part_file, final_proc])

        genuine_data = b"CLEAN_RECOVERED_MEDIA_STREAM_DATA" * 50
        genuine_hash = hashlib.sha256(genuine_data).hexdigest()
        genuine_size = len(genuine_data)

        # Step 1: Corrupted attempt
        corrupt_data = b"CORRUPTED_STREAM_BYTE_DRIFT" * 50
        res_fail = client.patch(
            "/api/sync/upload/chunk",
            content=corrupt_data,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(genuine_size),
                "x-sha256": genuine_hash,
                "x-session-id": "sess_recovery",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_fail.status_code, 422)
        self.assertFalse(part_file.exists())

        # Step 2: Fresh init
        res_init = client.post("/api/sync/upload/init", json={
            "session_id": "sess_recovery",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": genuine_size,
            "sha256_hash": genuine_hash,
            "auth_token": AUTH_TOKEN
        })
        self.assertEqual(res_init.status_code, 200)
        self.assertEqual(res_init.json()["remote_offset"], 0)

        # Step 3: Clean upload
        res_clean = client.patch(
            "/api/sync/upload/chunk",
            content=genuine_data,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(genuine_size),
                "x-sha256": genuine_hash,
                "x-session-id": "sess_recovery",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_clean.status_code, 200)
        self.assertTrue(res_clean.json()["verified"])
        self.assertEqual(res_clean.json()["status"], "completed")

        # Step 4: Cryptographic verification check
        res_proof = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={genuine_hash}&file_size={genuine_size}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res_proof.status_code, 200)
        self.assertTrue(res_proof.json()["verified"])
        self.assertEqual(res_proof.json()["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
