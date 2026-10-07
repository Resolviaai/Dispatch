"""Destructive Chaos and High-Concurrency Invariant Tests.
Validates the most severe distributed edge cases:
1. Abrupt Process Termination (SIGKILL / taskkill) & Orchestrator Startup Recovery
2. Corrupt Chunk Quarantine & Cryptographic Proof-of-Receipt Refusal
3. Multi-Worker Claim Collision (Atomic CAS) & Split-Brain Lease Fencing (LeaseLostError)
4. Multi-Platform Publishing Idempotency across YouTube, Instagram, LinkedIn, and Twitter
5. Database Hot Backup and SQLite PRAGMA Integrity Verification
"""
import unittest
import tempfile
import shutil
import hashlib
import threading
import subprocess
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient

from dispatch.web.app import app
from dispatch.db import (
    init_db,
    get_db_connection,
    register_chunk,
    create_session,
    verify_db_integrity,
    backup_database,
    approve_clip
)
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.job_queue import (
    init_job_queue_schema,
    enqueue_job,
    claim_job,
    complete_stage_checkpoint,
    LeaseLostError
)
from dispatch.orchestrator.recovery import recover_laptop_orchestrator
from dispatch.publisher.outbox import process_outbox_queue
from dispatch.publisher.linkedin import upload_linkedin_video
from dispatch.publisher.twitter import upload_x_video
from dispatch.config import INCOMING_DIR, PROCESSING_DIR

client = TestClient(app)
AUTH_TOKEN = "dispatch_paired_secret_default"


class TestDestructiveChaos(unittest.TestCase):

    def setUp(self):
        os.environ["DISPATCH_AUTH_TOKEN"] = AUTH_TOKEN
        init_db()
        init_job_queue_schema()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM pipeline_jobs;")
            conn.execute("DELETE FROM publishing_outbox;")
            conn.execute("DELETE FROM clips;")
            conn.execute("DELETE FROM chunks;")
            conn.execute("DELETE FROM sessions;")
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dispatch_destr_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_process_termination_and_startup_recovery(self):
        """Spawns an independent child process that claims a job and is abruptly killed.
        Verifies orchestrator recovery safely reclaims the lease without resetting stage progress.
        """
        chunk_id = register_chunk(
            session_id="sess_kill_test",
            filename="chunk_kill.mp4",
            filepath=str(self.temp_dir / "chunk_kill.mp4"),
            file_hash="hash_kill_test"
        )
        job_id = enqueue_job(chunk_id=chunk_id, session_id="sess_kill_test")

        # Worker advances job to TRANSCRIBE stage
        claimed = claim_job("worker_pre_kill", lease_duration_seconds=30)
        self.assertIsNotNone(claimed)
        complete_stage_checkpoint(job_id, PipelineStage.VERIFY, worker_id="worker_pre_kill")

        # Spawn a subprocess that claims the job with a short lease and holds it
        script = f"""
import sys
from dispatch.db import init_db
from dispatch.orchestrator.job_queue import claim_job
init_db()
claimed = claim_job('worker_doomed_process', lease_duration_seconds=2)
if claimed:
    print('CLAIMED_OK', flush=True)
    import time
    time.sleep(30)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=str(Path.cwd()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Wait until child process reports CLAIMED_OK
        first_line = proc.stdout.readline().strip()
        self.assertEqual(first_line, "CLAIMED_OK")

        # Abruptly kill the worker process (simulating crash / power cut)
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()

        # Artificially expire the lease
        past_stamp = (datetime.now() - timedelta(seconds=10)).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE pipeline_jobs SET lease_expires_at = ? WHERE job_id = ?",
                (past_stamp, job_id)
            )

        # Run orchestrator recovery as if laptop just rebooted
        report = recover_laptop_orchestrator()
        self.assertIn(job_id, report["reclaimed_jobs"])

        # Checkpoint stage must remain TRANSCRIBE (never reset to VERIFY)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT current_stage, status, worker_id FROM pipeline_jobs WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            self.assertEqual(row["current_stage"], "TRANSCRIBE")
            self.assertEqual(row["status"], "RETRY_PENDING")
            self.assertIsNone(row["worker_id"])

    def test_02_corrupt_chunk_quarantine_and_phone_proof_refusal(self):
        """Simulates corrupt or truncated data transmission.
        Ensures receiver quarantines the bad file and verify-chunk refuses cryptographic proof.
        """
        seg_id = f"seg_corrupt_{int(time.time() * 1000)}"
        genuine_bytes = b"GENUINE_VIDEO_STREAM_BYTES_VALID" * 50
        genuine_sha256 = hashlib.sha256(genuine_bytes).hexdigest()
        corrupt_bytes = b"CORRUPTED_STREAM_GARBAGE_PAYLOAD" * 50
        corrupt_sha256 = hashlib.sha256(corrupt_bytes).hexdigest()

        # Step 1: Upload corrupt bytes with genuine SHA256 header expectation
        init_resp = client.post("/api/sync/upload/init", json={
            "session_id": "sess_corrupt",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": len(genuine_bytes),
            "sha256_hash": genuine_sha256,
            "auth_token": AUTH_TOKEN
        })
        self.assertEqual(init_resp.status_code, 200)

        patch_resp = client.patch(
            "/api/sync/upload/chunk",
            content=corrupt_bytes,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(len(genuine_bytes)),
                "x-sha256": genuine_sha256,
                "x-session-id": "sess_corrupt",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream"
            }
        )
        # Server must reject SHA mismatch
        self.assertEqual(patch_resp.status_code, 422)

        # Step 2: Phone requests proof of receipt before deleting local video
        proof_resp = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={genuine_sha256}&file_size={len(genuine_bytes)}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(proof_resp.status_code, 200)
        # Phone MUST be told verified = False so phone NEVER deletes local recording!
        self.assertFalse(proof_resp.json()["verified"])

        # Step 3: Now simulate clean re-upload of genuine bytes
        init_clean = client.post("/api/sync/upload/init", json={
            "session_id": "sess_corrupt",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": len(genuine_bytes),
            "sha256_hash": genuine_sha256,
            "auth_token": AUTH_TOKEN
        })
        self.assertEqual(init_clean.status_code, 200)

        patch_clean = client.patch(
            "/api/sync/upload/chunk",
            content=genuine_bytes,
            headers={
                "x-segment-id": seg_id,
                "x-upload-offset": "0",
                "x-file-size": str(len(genuine_bytes)),
                "x-sha256": genuine_sha256,
                "x-session-id": "sess_corrupt",
                "x-auth-token": AUTH_TOKEN,
                "content-type": "application/octet-stream"
            }
        )
        self.assertEqual(patch_clean.status_code, 200)
        self.assertEqual(patch_clean.json()["status"], "completed")

        # Step 4: Phone re-queries proof of receipt
        proof_verified = client.get(
            f"/api/sync/verify-chunk?segment_id={seg_id}&sha256={genuine_sha256}&file_size={len(genuine_bytes)}",
            headers={"x-auth-token": AUTH_TOKEN}
        )
        self.assertEqual(proof_verified.status_code, 200)
        self.assertTrue(proof_verified.json()["verified"])
        self.assertEqual(proof_verified.json()["status"], "VERIFIED")

    def test_03_concurrent_worker_claim_collision_and_lease_fencing(self):
        """Launches 10 simultaneous threads attempting to claim a single job.
        Verifies atomic CAS allows exactly 1 worker to claim.
        Also verifies lease fencing throws LeaseLostError if an expired worker tries to checkpoint.
        """
        chunk_id = register_chunk(
            session_id="sess_collision",
            filename="chunk_col.mp4",
            filepath=str(self.temp_dir / "chunk_col.mp4"),
            file_hash="hash_col"
        )
        job_id = enqueue_job(chunk_id=chunk_id, session_id="sess_collision")

        claimed_workers = []
        threads = []

        def worker_task(wid: str):
            res = claim_job(worker_id=wid, lease_duration_seconds=30)
            if res:
                claimed_workers.append(wid)

        for i in range(10):
            t = threading.Thread(target=worker_task, args=(f"worker_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Exactly 1 worker should have claimed the job
        self.assertEqual(len(claimed_workers), 1, f"Expected 1 claim, got: {claimed_workers}")
        winner_worker = claimed_workers[0]

        # Now test LEASE FENCING:
        # Simulate winner worker freezing / lease expiring
        past_stamp = (datetime.now() - timedelta(seconds=60)).isoformat()
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE pipeline_jobs SET lease_expires_at = ?, status = 'QUEUED', worker_id = NULL WHERE job_id = ?",
                (past_stamp, job_id)
            )

        # Worker_B claims the newly available job
        claim_b = claim_job(worker_id="worker_B", lease_duration_seconds=30)
        self.assertIsNotNone(claim_b)
        self.assertEqual(claim_b["worker_id"], "worker_B")

        # Original winner_worker wakes up and tries to complete stage
        # Fencing token check must RAISE LeaseLostError!
        with self.assertRaises(LeaseLostError):
            complete_stage_checkpoint(
                job_id=job_id,
                current_stage=PipelineStage.VERIFY,
                worker_id=winner_worker
            )

        # Legitimate worker_B completes stage successfully
        next_stage = complete_stage_checkpoint(
            job_id=job_id,
            current_stage=PipelineStage.VERIFY,
            worker_id="worker_B"
        )
        self.assertEqual(next_stage, PipelineStage.TRANSCRIBE)

    def test_04_multi_platform_publishing_idempotency(self):
        """Validates that publishing to LinkedIn, Twitter/X, Instagram, and YouTube
        handles missing credentials gracefully via simulation, and enforces idempotency.
        """
        clip_id = f"clip_pub_{int(time.time() * 1000)}"
        dummy_video = self.temp_dir / "rendered.mp4"
        dummy_video.write_bytes(b"TEST_VIDEO_BYTES_SIMULATED")

        # 1. Test LinkedIn raises PermissionError when credentials missing
        with self.assertRaises(PermissionError):
            upload_linkedin_video(
                video_path=dummy_video,
                title="Dispatch LinkedIn Test",
                commentary="#buildinpublic #ai",
                access_token=None
            )

        # 2. Test Twitter / X raises PermissionError when credentials missing
        with self.assertRaises(PermissionError):
            upload_x_video(
                video_path=dummy_video,
                text="Autonomous video engine #python",
                bearer_token=None
            )

        # 3. Test queue and dispatch all 4 platforms
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO clips (id, start_time, end_time, duration, title, hook, video_path, status)
                VALUES (?, 0.0, 10.0, 10.0, 'Multi-Platform Test', 'Watch this', ?, 'ready_review')
            """, (clip_id, str(dummy_video)))

        approve_clip(
            clip_id=clip_id,
            custom_platforms="youtube,instagram,linkedin,twitter"
        )

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 4)

        # Without live credentials configured, outbox marks jobs blocked_needs_auth
        with patch("dispatch.publisher.outbox.upload_youtube_short", side_effect=PermissionError("YouTube auth missing")):
            success_count = process_outbox_queue()
            self.assertEqual(success_count, 0)

            # Test Idempotency: re-running process_outbox_queue finds 0 pending
            second_pass = process_outbox_queue()
            self.assertEqual(second_pass, 0)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertEqual(row["status"], "blocked_needs_auth")

    def test_05_database_backup_and_integrity(self):
        """Verifies SQLite hot snapshot backup and PRAGMA integrity check."""
        self.assertTrue(verify_db_integrity(), "Database PRAGMA integrity check must pass")
        backup_path = backup_database()
        self.assertIsNotNone(backup_path)
        self.assertTrue(backup_path.exists())
        self.assertGreater(backup_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
