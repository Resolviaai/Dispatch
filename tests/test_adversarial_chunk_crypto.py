"""Adversarial Stress Test Suite: Cryptographic Verification Engine.

The LAN byte path (POST /api/sync/upload/init, PATCH /api/sync/upload/chunk,
POST /api/sync/upload/direct) is RETIRED: phone segments arrive via YouTube.
Every byte-transfer adversarial scenario must therefore fail closed with
410 Gone — there is no receiver left to attack, and no partial byte state may
ever be created. The live cryptographic proof endpoint /api/sync/verify-chunk
keeps its full adversarial coverage (tests 10-15): valid hash vs tampered hash
vs missing file, size mismatch, unauthenticated access, and directory
traversal injection.
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
    # 1. RETIRED LAN BYTE PATH: OFFSET DRIFT CHALLENGES (HTTP 410 FAIL-CLOSED)
    # =========================================================================

    def test_01_offset_mismatch_empty_server_nonzero_client_offset(self):
        """Server has 0 bytes, client sends offset > 0 -> retired endpoint returns 410 Gone."""
        seg_id = "adv_retired_offset_1"
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
        self.assertEqual(res.status_code, 410, f"Expected 410 Gone, got {res.status_code}: {res.text}")
        self.assertIn("retired", res.json().get("detail", ""))
        self.assertFalse(part_file.exists(), "Retired endpoint must never create partial bytes")

    def test_02_offset_mismatch_replay_zero_offset_against_existing_partial(self):
        """Replaying offset 0 against a planted partial must be a 410 no-op: no
        receiver exists, so the planted bytes stay exactly as they were."""
        seg_id = "adv_retired_replay_2"
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
        self.assertEqual(res.status_code, 410)
        self.assertIn("retired", res.json().get("detail", ""))
        # The planted partial was untouched: the retirement stub performs no
        # server-side byte operations whatsoever.
        self.assertEqual(part_file.stat().st_size, 256)
        with open(part_file, "rb") as f:
            self.assertEqual(f.read(), initial_bytes)

    def test_03_offset_mismatch_skip_ahead_beyond_server_offset(self):
        """Skip-ahead offset 512 against a planted 256-byte partial -> 410 no-op."""
        seg_id = "adv_retired_skip_3"
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
        self.assertEqual(res.status_code, 410)
        self.assertIn("retired", res.json().get("detail", ""))
        self.assertEqual(part_file.stat().st_size, 256)

    def test_04_offset_mismatch_off_by_one_boundaries(self):
        """Off-by-one offsets (99/101) -> both retired, both 410 Gone."""
        seg_id = "adv_retired_boundary_4"
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
        self.assertEqual(res_under.status_code, 410)
        self.assertIn("retired", res_under.json().get("detail", ""))

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
        self.assertEqual(res_over.status_code, 410)
        self.assertIn("retired", res_over.json().get("detail", ""))

    # =========================================================================
    # 2. RETIRED LAN BYTE PATH: AUTH CHALLENGES ARE MOOT (HTTP 410 FAIL-CLOSED)
    # =========================================================================

    def test_05_unauthenticated_chunk_upload_missing_header(self):
        """The endpoint is gone for ANY caller — a missing token header yields
        410 Gone (no auth oracle, no byte receiver)."""
        payload = b"UNAUTHORIZED_ATTEMPT_BYTES"
        headers = {
            "x-segment-id": "adv_retired_auth_5",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_unauth",
            "content-type": "application/octet-stream",
        }
        res = client.patch("/api/sync/upload/chunk", content=payload, headers=headers)
        self.assertEqual(res.status_code, 410)
        self.assertIn("retired", res.json().get("detail", ""))

    def test_06_unauthenticated_chunk_upload_empty_and_whitespace_token(self):
        """Empty or whitespace-only tokens are also 410 Gone (endpoint is gone)."""
        payload = b"UNAUTHORIZED_BLANK_BYTES"
        base_headers = {
            "x-segment-id": "adv_retired_auth_6",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_unauth",
            "content-type": "application/octet-stream",
        }

        # Empty string
        res_empty = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": ""})
        self.assertEqual(res_empty.status_code, 410)
        self.assertIn("retired", res_empty.json().get("detail", ""))

        # Whitespace
        res_ws = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "   "})
        self.assertEqual(res_ws.status_code, 410)
        self.assertIn("retired", res_ws.json().get("detail", ""))

    def test_07_unauthenticated_chunk_upload_invalid_and_forged_tokens(self):
        """Wrong, forged, and even VALID tokens must all see the same 410 Gone."""
        payload = b"FORGED_TOKEN_PAYLOAD"
        base_headers = {
            "x-segment-id": "adv_retired_auth_7",
            "x-upload-offset": "0",
            "x-file-size": str(len(payload)),
            "x-sha256": hashlib.sha256(payload).hexdigest(),
            "x-session-id": "sess_forged",
            "content-type": "application/octet-stream",
        }

        # Completely wrong token
        res_wrong = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "evil_hacker_token"})
        self.assertEqual(res_wrong.status_code, 410)
        self.assertIn("retired", res_wrong.json().get("detail", ""))

        # Bearer prefix with wrong token
        res_bearer_wrong = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": "Bearer evil_token_xyz"})
        self.assertEqual(res_bearer_wrong.status_code, 410)
        self.assertIn("retired", res_bearer_wrong.json().get("detail", ""))

        # Even the valid token no longer resurrects a byte receiver
        res_valid = client.patch("/api/sync/upload/chunk", content=payload, headers={**base_headers, "x-auth-token": AUTH_TOKEN})
        self.assertEqual(res_valid.status_code, 410)

        # Verify no file was written to disk
        part_file = INCOMING_DIR / "adv_retired_auth_7.part"
        self.assertFalse(part_file.exists())

    # =========================================================================
    # 3. RETIRED LAN BYTE PATH: CHECKSUM TAMPERING IS MOOT (NO BYTE RECEIVER)
    # =========================================================================

    def test_08_final_chunk_checksum_tampering_multi_chunk_purges_partial_file(self):
        """Multi-chunk tampering scenario: the receiver is gone, so every
        chunk PATCH (correct-offset or not) sees 410 Gone, no partial ever
        exists, no final artifact is created, and no chunk row is registered.
        """
        seg_id = "adv_retired_multi_8"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        final_inc = INCOMING_DIR / f"{seg_id}.mp4"
        final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.extend([part_file, final_inc, final_proc])

        part_file.unlink(missing_ok=True)
        final_inc.unlink(missing_ok=True)
        final_proc.unlink(missing_ok=True)

        genuine_chunk1 = b"A" * 1000
        genuine_sha256 = hashlib.sha256(b"A" * 3000).hexdigest()

        for offset in ("0", "1000", "2000"):
            res = client.patch(
                "/api/sync/upload/chunk",
                content=genuine_chunk1,
                headers={
                    "x-segment-id": seg_id,
                    "x-upload-offset": offset,
                    "x-file-size": "3000",
                    "x-sha256": genuine_sha256,
                    "x-session-id": "sess_multi_retired",
                    "x-auth-token": AUTH_TOKEN,
                    "content-type": "application/octet-stream",
                }
            )
            self.assertEqual(res.status_code, 410, f"chunk at offset {offset} must be 410")
            self.assertIn("retired", res.json().get("detail", ""))

        # No partial was ever created, nothing was finalized, nothing registered
        self.assertFalse(part_file.exists(), "Retired endpoint must never write partial bytes")
        self.assertFalse(final_inc.exists(), "Retired endpoint must never finalize media into INCOMING_DIR")
        self.assertFalse(final_proc.exists(), "Retired endpoint must never finalize media into PROCESSING_DIR")

        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM chunks WHERE filename = ?", (f"{seg_id}.mp4",)).fetchone()
            self.assertIsNone(row, "Retired path must not register chunk rows")

    def test_09_single_chunk_checksum_tampering_purges_partial_file(self):
        """Single-chunk attempt with a fraudulent checksum header: 410 Gone and
        no partial file left behind (checksum enforcement is moot post-retirement)."""
        seg_id = "adv_retired_single_9"
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
        self.assertEqual(res.status_code, 410)
        self.assertIn("retired", res.json().get("detail", ""))
        self.assertFalse(part_file.exists(), "Retired endpoint must never leave a partial file")
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
    # 5. RETIRED LAN BYTE PATH: RECOVERY CYCLE AND RESIDUAL ROUTES
    # =========================================================================

    def test_16_clean_recovery_after_checksum_corruption_failure(self):
        """The corruption-recovery cycle is structurally obsolete: init and
        chunk PATCH are 410 Gone so no bytes can ever arrive, and a subsequent
        verify-chunk probe truthfully reports MISSING."""
        seg_id = "adv_retired_recovery_16"
        part_file = INCOMING_DIR / f"{seg_id}.part"
        final_proc = PROCESSING_DIR / f"{seg_id}.mp4"
        self.created_files.extend([part_file, final_proc])
        part_file.unlink(missing_ok=True)
        final_proc.unlink(missing_ok=True)

        genuine_hash = hashlib.sha256(b"CLEAN_RECOVERED_MEDIA_STREAM_DATA" * 50).hexdigest()

        # Step 1: Corrupted attempt on the retired receiver
        res_fail = client.patch(
            "/api/sync/upload/chunk",
            content=b"CORRUPTED_STREAM_BYTE_DRIFT_01234" * 50,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": "1650",
                "x-sha256": genuine_hash,
                "x-session-id": "sess_recovery",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_fail.status_code, 410)
        self.assertFalse(part_file.exists())

        # Step 2: Fresh init on the retired route
        res_init = client.post("/api/sync/upload/init", json={
            "session_id": "sess_recovery",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": 1650,
            "sha256_hash": genuine_hash,
            "auth_token": AUTH_TOKEN
        })
        self.assertEqual(res_init.status_code, 410)
        self.assertIn("retired", res_init.json().get("detail", ""))

        # Step 3: No recovery upload is possible either
        res_clean = client.patch(
            "/api/sync/upload/chunk",
            content=b"CLEAN_RECOVERED_MEDIA_STREAM_DATA" * 50,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": "1650",
                "x-sha256": genuine_hash,
                "x-session-id": "sess_recovery",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream",
            }
        )
        self.assertEqual(res_clean.status_code, 410)

        # Step 4: The proof endpoint truthfully reports the absence of bytes
        res_proof = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={genuine_hash}&file_size=1650",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(res_proof.status_code, 200)
        self.assertFalse(res_proof.json()["verified"])
        self.assertEqual(res_proof.json()["status"], "MISSING")


if __name__ == "__main__":
    unittest.main()
