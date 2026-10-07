"""Tier 3: Cross-Feature Combinations & Pairwise Interaction Tests.
Covers:
- Pairwise 1: LAN Chunked Resumable Sync + 5-Stage Checkpointed Media Pipeline
- Pairwise 2: YouTube Cloud Inbox Catcher + 5-Stage Checkpointed Media Pipeline
- Pairwise 3: Web Dashboard Approval Flow + Multi-Platform Publishing Outbox + blocked_needs_auth
- Pairwise 4: Pipeline Stage Failure + Retry Engine Exponential Backoff + Retry Cap
- Pairwise 5: Mobile Non-Blocking Segment Roll + Resumable Sync + Cryptographic Pruning
- Pairwise 6: Resource Governor Hold (Low Disk) + Checkpointed Stage Resumption
- Pairwise 7: Discovery Beacon / Pairing Configuration + Direct File Upload
- Pairwise 8: Negative Preference Feedback Loop + Highlight Prompt Adaptation
- Pairwise 9: Multi-Worker Concurrent Lease Claim Collision + Split-Brain Lease Fencing
- Pairwise 10: Ingestion Deduplication & Idempotent Processing
"""
import time
import hashlib
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.config import INCOMING_DIR, PROCESSING_DIR
from dispatch.db import get_db_connection, register_chunk, save_clip, approve_clip, reject_clip
from dispatch.sync.receiver import get_pairing_pin, get_auth_token
from dispatch.orchestrator.job_queue import (
    enqueue_job,
    claim_job,
    complete_stage_checkpoint,
    fail_stage_job,
    LeaseLostError
)
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.pipeline_runner import PipelineStageRunner
from dispatch.publisher.outbox import process_outbox_queue
from dispatch_mobile.segmenter import RecordingSessionManager
from dispatch_mobile.retention import cleanup_verified_segments
from dispatch_mobile.db import get_mobile_db
from dispatch.ai_clips.preference_learner import get_negative_feedback_prompt
from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher


class TestTier3CrossFeatureCombinations(DispatchE2EBaseTestCase):
    """Tier 3: Cross-Feature Pairwise Interactions."""

    def test_p01_lan_sync_plus_5_stage_checkpointed_pipeline(self):
        """Pairwise 1: LAN chunked upload -> verify -> pipeline job -> 5-stage sequential completion."""
        from dispatch.db import create_session
        create_session(session_id="sess_p01")

        seg_id = f"seg_p01_{int(time.time() * 1000)}"
        video_payload = b"GENUINE_CHUNKED_VIDEO_BYTES_STREAM_" * 300  # ~10,800 bytes
        file_size = len(video_payload)
        sha256_hash = hashlib.sha256(video_payload).hexdigest()

        # Step 1: Init upload
        init_res = self.client.post("/api/sync/upload/init", json={
            "session_id": "sess_p01",
            "segment_id": seg_id,
            "filename": f"{seg_id}.mp4",
            "file_size_bytes": file_size,
            "sha256_hash": sha256_hash,
            "auth_token": self.auth_token
        })
        self.assertEqual(init_res.status_code, 200)

        # Step 2: Upload chunk to completion
        headers = {
            "x-segment-id": seg_id,
            "x-upload-offset": "0",
            "x-file-size": str(file_size),
            "x-sha256": sha256_hash,
            "x-session-id": "sess_p01",
            "x-auth-token": self.auth_token,
            "content-type": "application/octet-stream"
        }
        upload_res = self.client.patch("/api/sync/upload/chunk", content=video_payload, headers=headers)
        self.assertEqual(upload_res.status_code, 200)
        self.assertEqual(upload_res.json()["status"], "completed")

        # Step 3: Verify pipeline job was automatically created in database
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chunks WHERE filename = ?", (f"{seg_id}.mp4",))
            chunk_row = cursor.fetchone()
            self.assertIsNotNone(chunk_row)
            chunk_id = chunk_row["id"]

            cursor.execute("SELECT job_id, current_stage, status FROM pipeline_jobs WHERE chunk_id = ?", (chunk_id,))
            job_row = cursor.fetchone()
            self.assertIsNotNone(job_row)
            job_id = job_row["job_id"]
            self.assertEqual(job_row["current_stage"], "VERIFY")
            self.assertEqual(job_row["status"], "QUEUED")

        # Step 4: Advance job through all stages via worker claims
        stages = [PipelineStage.VERIFY, PipelineStage.TRANSCRIBE, PipelineStage.ANALYZE, PipelineStage.RENDER, PipelineStage.FINALIZE]
        for st in stages:
            claimed = claim_job("worker_p01", lease_duration_seconds=60)
            self.assertIsNotNone(claimed)
            complete_stage_checkpoint(job_id, st, worker_id="worker_p01")

        # Step 5: Final state verification
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, current_stage FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            final_job = cursor.fetchone()
            self.assertEqual(final_job["status"], "COMPLETED")
            self.assertEqual(final_job["current_stage"], "COMPLETED")

            # Raw video preserved
            cursor.execute("SELECT filepath FROM chunks WHERE id = ?", (chunk_id,))
            saved_path = Path(cursor.fetchone()["filepath"])
            self.assertTrue(saved_path.exists())

        # Cleanup
        saved_path.unlink(missing_ok=True)

    def test_p02_youtube_inbox_plus_5_stage_pipeline_checkpoints(self):
        """Pairwise 2: YouTube catcher discovery -> download -> transcript -> highlight clips creation."""
        catcher = YouTubeInboxCatcher(storage_dir=self.temp_dir)
        vid = "dQw4w9WgXcQ"  # Valid 11-char ID
        dummy_v = self.temp_dir / f"{vid}.mp4"
        self.create_synthetic_mp4(dummy_v, duration_seconds=1.0)

        mock_captions = [
            {"id": 0, "start": 0.0, "end": 15.0, "text": "Dispatch automates your workflow.", "words": []},
            {"id": 1, "start": 15.0, "end": 35.0, "text": "It cuts highlights and publishes everywhere.", "words": []}
        ]

        def fake_identify(**kwargs):
            cid = save_clip(
                session_id=kwargs.get("session_id"),
                chunk_id=kwargs.get("chunk_id"),
                start_time=0.0,
                end_time=15.0,
                title="yt_clip_1",
                hook="Hook",
                description="Desc",
                hashtags="",
                virality_score=85,
                layout_mode="fit_blur"
            )
            return [cid]

        with patch.object(catcher, "download_video", return_value=dummy_v):
            with patch.object(catcher, "fetch_youtube_captions", return_value=mock_captions):
                with patch("dispatch.youtube_inbox.catcher.identify_and_save_highlights", side_effect=fake_identify):
                    with patch("dispatch.youtube_inbox.catcher.render_clip", return_value={"video_path": "c.mp4", "thumbnail_path": "t.jpg"}):
                        res = catcher.process_video(vid)
                        self.assertEqual(res["status"], "CLIPS_CREATED")
                        self.assertEqual(res["clips_count"], 1)

                        # Verify idempotency: calling process_video again immediately returns duplicate notice
                        res_dup = catcher.process_video(vid)
                        self.assertEqual(res_dup["status"], "ALREADY_PROCESSED")

    def test_p03_approval_flow_plus_multi_platform_outbox_blocked_auth(self):
        """Pairwise 3: Dashboard clip approval for 4 platforms -> outbox marks unauthenticated blocked_needs_auth."""
        dummy_video = self.temp_dir / "p03_clip.mp4"
        dummy_video.write_bytes(b"VIDEO_CLIP_P03")

        clip_id = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=15.0,
            title="P03 Pairwise Clip", hook="High retention", description="Description",
            hashtags="#Shorts", virality_score=88, layout_mode="fit_blur"
        )
        with get_db_connection() as conn:
            conn.execute("UPDATE clips SET video_path = ? WHERE id = ?", (str(dummy_video), clip_id))

        # Creator 1-click approves via dashboard API
        res = self.client.post(f"/api/clips/{clip_id}/approve", json={
            "title": "Pairwise Approved Title",
            "hashtags": "#Shorts #IG #LinkedIn #X",
            "publish_mode": "private",
            "platforms": "youtube,instagram,linkedin,x"
        })
        self.assertEqual(res.status_code, 200)

        # Worker processes outbox queue with missing credentials
        with patch("dispatch.publisher.outbox.upload_youtube_short", side_effect=PermissionError("Missing YouTube OAuth")):
            success_count = process_outbox_queue()
            self.assertEqual(success_count, 0)

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT platform, status, last_error FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
                rows = cursor.fetchall()
                self.assertEqual(len(rows), 4)
                for r in rows:
                    self.assertEqual(r["status"], "blocked_needs_auth")
                    self.assertIsNotNone(r["last_error"])

    def test_p04_pipeline_failure_plus_retry_engine_cap(self):
        """Pairwise 4: Failing stage job claims, backs off, hits attempt cap (5), and terminates permanently."""
        setup = self.register_test_chunk_and_job(filename="p04_fail.mp4")
        job_id = setup["job_id"]

        for attempt in range(1, 6):
            claimed = claim_job("worker_fail_p04")
            if not claimed:
                # Force eligibility for test progression
                with get_db_connection() as conn:
                    conn.execute("UPDATE pipeline_jobs SET next_retry_at = datetime('now', '-10 seconds') WHERE job_id = ?", (job_id,))
                claimed = claim_job("worker_fail_p04")
            self.assertIsNotNone(claimed)

            fail_stage_job(job_id=job_id, error_message=f"Synthetic stage crash on attempt {attempt}")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, attempt_count FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            self.assertEqual(row["status"], "FAILED_PERMANENT")
            self.assertGreaterEqual(row["attempt_count"], 5)

        # Job is never claimed again
        unclaimed = claim_job("worker_after_permanent")
        self.assertIsNone(unclaimed)

    def test_p05_mobile_segment_roll_plus_sync_plus_cryptographic_pruning(self):
        """Pairwise 5: Mobile rolls 2 segments -> bad checksum refused -> clean upload prunes safely."""
        manager = RecordingSessionManager(storage_dir=self.temp_dir)
        sess_id = manager.start_session(notes="Pairwise Roll Sync")

        # Segment 1
        seg1_tmp = manager.current_tmp_path
        seg1_tmp.write_bytes(b"VALID_SEG_1_DATA")
        manager.start_next_segment()

        seg1_id = f"{sess_id}_seg_0001"
        seg1_path = self.temp_dir / f"{seg1_id}.mp4"
        self.assertTrue(seg1_path.exists())

        # Bad hash check refused
        proof_res = self.client.get(
            f"/api/sync/verify-chunk?segment_id={seg1_id}&sha256=tampered_hash_123&file_size=16",
            headers={"x-auth-token": self.auth_token}
        )
        self.assertFalse(proof_res.json()["verified"])

        # Pruning refuses to delete segment 1
        deleted = cleanup_verified_segments()
        self.assertEqual(deleted, 0)
        self.assertTrue(seg1_path.exists())

        # Mark verified by laptop
        with get_mobile_db() as conn:
            conn.execute("UPDATE mobile_segments SET status = 'VERIFIED_BY_LAPTOP' WHERE segment_id = ?", (seg1_id,))

        deleted = cleanup_verified_segments()
        self.assertEqual(deleted, 1)
        self.assertFalse(seg1_path.exists(), "Verified segment was not pruned after proof confirmation")

    def test_p06_resource_governor_hold_plus_stage_resumption(self):
        """Pairwise 6: Low disk space hold transitions to WAITING_FOR_RESOURCES, then resumes cleanly."""
        setup = self.register_test_chunk_and_job(filename="p06_gov.mp4")
        job_id = setup["job_id"]

        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET current_stage = 'TRANSCRIBE' WHERE job_id = ?", (job_id,))

        job = claim_job("worker_gov_hold")
        self.assertEqual(job["current_stage"], "TRANSCRIBE")

        # Mock governor reporting low disk space
        mock_gov = MagicMock()
        mock_gov.can_process_heavy_task.return_value = (False, "Low disk space (2.5 GB free < 5.0 GB minimum)")

        runner = PipelineStageRunner(governor=mock_gov)
        result = runner.process_job_step(job, "worker_gov_hold")
        self.assertFalse(result)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, last_error FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            j_row = cursor.fetchone()
            self.assertEqual(j_row["status"], "WAITING_FOR_RESOURCES")
            self.assertIn("Resource governor hold", j_row["last_error"])

        # Disk space restored -> governor allows processing
        mock_gov.can_process_heavy_task.return_value = (True, "")
        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET next_retry_at = datetime('now', '-5 seconds') WHERE job_id = ?", (job_id,))

        job_resumed = claim_job("worker_gov_resume")
        self.assertIsNotNone(job_resumed)
        self.assertEqual(job_resumed["current_stage"], "TRANSCRIBE")

    def test_p07_discovery_pairing_plus_direct_file_upload(self):
        """Pairwise 7: Discovered pairing credentials used to perform direct file upload."""
        pair_res = self.client.get("/api/sync/pairing/config")
        self.assertEqual(pair_res.status_code, 200)
        token = pair_res.json()["auth_token"]

        dummy_video = self.temp_dir / "direct_upload.mp4"
        self.create_synthetic_mp4(dummy_video, duration_seconds=1.0)
        seg_id = f"seg_direct_{int(time.time() * 1000)}"

        with open(dummy_video, "rb") as f:
            res_up = self.client.post(
                "/api/sync/upload/direct",
                data={"segment_id": seg_id, "session_id": "sess_direct", "auth_token": token},
                files={"file": (f"{seg_id}.mp4", f, "video/mp4")}
            )
        self.assertEqual(res_up.status_code, 200)
        self.assertEqual(res_up.json()["status"], "completed")
        self.assertTrue(res_up.json()["verified"])

        # Check job enqueued in queue
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT job_id FROM pipeline_jobs WHERE chunk_id = ?", (res_up.json()["chunk_id"],))
            self.assertIsNotNone(cursor.fetchone())

    def test_p08_negative_preference_feedback_plus_prompt_adaptation(self):
        """Pairwise 8: Rejected clip reasons are reflected in subsequent AI highlight prompts."""
        clip_id = save_clip(
            session_id=None, chunk_id=None, start_time=0.0, end_time=15.0,
            title="Weak Hook Clip", hook="Slow pacing", description="", hashtags="",
            virality_score=50, layout_mode="fit_blur"
        )
        reject_clip(clip_id, reason="Too slow in first 3 seconds, needs immediate action")

        prompt = get_negative_feedback_prompt()
        self.assertIn("Too slow in first 3 seconds", prompt)

    def test_p09_multi_worker_claim_collision_plus_lease_fencing(self):
        """Pairwise 9: 5 concurrent workers race to claim job -> exactly 1 wins; loser cannot checkpoint."""
        setup = self.register_test_chunk_and_job(filename="p09_race.mp4")
        job_id = setup["job_id"]

        claimed_workers = []
        threads = []

        def worker_task(wid: str):
            res = claim_job(wid, lease_duration_seconds=30)
            if res:
                claimed_workers.append(wid)

        for i in range(5):
            t = threading.Thread(target=worker_task, args=(f"worker_race_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(claimed_workers), 1)
        winner = claimed_workers[0]

        # Artificially expire winner's lease and re-claim by worker_B
        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET lease_expires_at = datetime('now', '-30 seconds'), status = 'QUEUED', worker_id = NULL WHERE job_id = ?", (job_id,))

        claim_b = claim_job("worker_B", lease_duration_seconds=30)
        self.assertIsNotNone(claim_b)

        # Winner worker tries to checkpoint -> raises LeaseLostError
        with self.assertRaises(LeaseLostError):
            complete_stage_checkpoint(job_id, PipelineStage.VERIFY, worker_id=winner)

        # Legitimate worker_B checkpoints successfully
        next_s = complete_stage_checkpoint(job_id, PipelineStage.VERIFY, worker_id="worker_B")
        self.assertEqual(next_s, PipelineStage.TRANSCRIBE)

    def test_p10_ingestion_deduplication_and_idempotent_processing(self):
        """Pairwise 10: Ingestion receiver detects duplicate hash and reuses existing chunk/job."""
        setup = self.register_test_chunk_and_job(filename="p10_dedup.mp4")
        chunk_id = setup["chunk_id"]
        sha256 = setup["sha256"]

        # Duplicate init with same hash returns already_completed
        init_dup = self.client.post("/api/sync/upload/init", json={
            "session_id": "sess_p10_dup",
            "segment_id": "p10_dedup",
            "filename": "p10_dedup.mp4",
            "file_size_bytes": setup["filepath"].stat().st_size,
            "sha256_hash": sha256,
            "auth_token": self.auth_token
        })
        self.assertEqual(init_dup.status_code, 200)
        self.assertEqual(init_dup.json()["status"], "already_completed")
        self.assertTrue(init_dup.json()["verified"])
