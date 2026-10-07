"""Tier 1: Feature Coverage — Media Pipeline Resilience & Stage Checkpointing.
Covers:
- Feature 4: Pipeline Checkpointing & Stage Reset (>= 5 tests)
- Feature 5: Raw Source Video Preservation (>= 5 tests)
- Feature 6: Retry Cap & Permanent Failure Classification (>= 5 tests)
"""
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from tests.e2e.e2e_base import DispatchE2EBaseTestCase
from dispatch.db import get_db_connection, register_chunk, save_transcript, get_setting
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.job_queue import (
    enqueue_job,
    claim_job,
    complete_stage_checkpoint,
    fail_stage_job,
    renew_heartbeat,
    LeaseLostError
)
from dispatch.orchestrator.retry_engine import RetryEngine
from dispatch.orchestrator.pipeline_runner import PipelineStageRunner


class TestTier1PipelineResilience(DispatchE2EBaseTestCase):
    """Tier 1 Feature Coverage: Features 4, 5, and 6."""

    # =========================================================================
    # FEATURE 4: Pipeline Checkpointing & Stage Reset
    # =========================================================================

    def test_f04_sequential_checkpoint_progression_verify_to_completed(self):
        """F4.1: Job advances sequentially through all 5 checkpointed pipeline stages."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_seq.mp4")
        job_id = setup_data["job_id"]

        stages = [
            PipelineStage.VERIFY,
            PipelineStage.TRANSCRIBE,
            PipelineStage.ANALYZE,
            PipelineStage.RENDER,
            PipelineStage.FINALIZE
        ]
        expected_next = [
            PipelineStage.TRANSCRIBE,
            PipelineStage.ANALYZE,
            PipelineStage.RENDER,
            PipelineStage.FINALIZE,
            PipelineStage.COMPLETED
        ]

        worker_id = "worker_f04_seq"
        for current_s, next_s in zip(stages, expected_next):
            claimed = claim_job(worker_id=worker_id, lease_duration_seconds=60)
            self.assertIsNotNone(claimed, f"Failed to claim job at stage {current_s.value}")
            self.assertEqual(claimed["job_id"], job_id)
            self.assertEqual(claimed["current_stage"], current_s.value)

            advanced = complete_stage_checkpoint(job_id, current_s, worker_id=worker_id)
            self.assertEqual(advanced, next_s)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, current_stage FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            final_row = cursor.fetchone()
            self.assertEqual(final_row["status"], "COMPLETED")
            self.assertEqual(final_row["current_stage"], "COMPLETED")

    def test_f04_claim_job_atomically_grants_timed_lease(self):
        """F4.2: claim_job atomically updates status to PROCESSING and sets worker lease expiration."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_lease.mp4")
        job_id = setup_data["job_id"]

        claimed = claim_job(worker_id="worker_f04_lease", lease_duration_seconds=120)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["worker_id"], "worker_f04_lease")
        self.assertEqual(claimed["status"], "PROCESSING")
        self.assertIsNotNone(claimed["lease_expires_at"])
        self.assertGreaterEqual(claimed["attempt_count"], 1)

    def test_f04_lease_fencing_prevents_expired_worker_from_checkpointing(self):
        """F4.3: Worker whose lease has expired or been stolen cannot complete checkpoint (LeaseLostError)."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_fence.mp4")
        job_id = setup_data["job_id"]

        claimed_a = claim_job(worker_id="worker_A", lease_duration_seconds=30)
        self.assertIsNotNone(claimed_a)

        # Worker A lease artificially expired
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE pipeline_jobs SET lease_expires_at = datetime('now', '-10 seconds') WHERE job_id = ?",
                (job_id,)
            )

        # Worker A attempts to complete stage after expiration
        with self.assertRaises(LeaseLostError):
            complete_stage_checkpoint(job_id, PipelineStage.VERIFY, worker_id="worker_A")

    def test_f04_worker_heartbeat_lease_renewal(self):
        """F4.4: Active worker extends lease via heartbeat to prevent premature reclamation."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_hb.mp4")
        job_id = setup_data["job_id"]

        claimed = claim_job(worker_id="worker_hb", lease_duration_seconds=30)
        self.assertIsNotNone(claimed)
        initial_lease = claimed["lease_expires_at"]

        time.sleep(0.05)
        renewed = renew_heartbeat(job_id=job_id, worker_id="worker_hb", extend_seconds=120)
        self.assertTrue(renewed)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lease_expires_at FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            updated_lease = cursor.fetchone()["lease_expires_at"]
            self.assertGreater(updated_lease, initial_lease)

    def test_f04_active_job_enqueue_deduplication(self):
        """F4.5: Re-enqueuing an active chunk returns the existing job_id rather than creating a duplicate."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_dedup.mp4")
        chunk_id = setup_data["chunk_id"]
        job_id_1 = setup_data["job_id"]

        job_id_2 = enqueue_job(chunk_id=chunk_id, session_id=setup_data["session_id"])
        self.assertEqual(job_id_1, job_id_2)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM pipeline_jobs WHERE chunk_id = ?", (chunk_id,))
            count = cursor.fetchone()[0]
            self.assertEqual(count, 1)

    def test_f04_stage_checkpoint_persists_in_sqlite(self):
        """F4.6: Database reflects exact stage transition across independent queries."""
        setup_data = self.register_test_chunk_and_job(filename="pipe_f04_db.mp4")
        job_id = setup_data["job_id"]

        claim_job(worker_id="worker_db", lease_duration_seconds=60)
        complete_stage_checkpoint(job_id, PipelineStage.VERIFY, worker_id="worker_db")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_stage, status, worker_id FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            self.assertEqual(row["current_stage"], "TRANSCRIBE")
            self.assertEqual(row["status"], "QUEUED")
            self.assertIsNone(row["worker_id"])

    # =========================================================================
    # FEATURE 5: Raw Source Video Preservation
    # =========================================================================

    def test_f05_finalize_stage_preserves_raw_source_video_on_disk(self):
        """F5.1: Finalize stage marks chunk processed but NEVER deletes raw source video."""
        setup_data = self.register_test_chunk_and_job(filename="raw_f05_pres.mp4")
        job_id = setup_data["job_id"]
        video_path = setup_data["filepath"]
        self.assertTrue(video_path.exists())

        runner = PipelineStageRunner()
        chunk_dict = {
            "id": setup_data["chunk_id"],
            "filename": video_path.name,
            "filepath": str(video_path)
        }
        job_dict = {"job_id": job_id, "chunk_id": setup_data["chunk_id"]}

        runner._run_finalize_stage(job=job_dict, chunk=chunk_dict, filepath=video_path)

        # Raw file MUST still exist!
        self.assertTrue(video_path.exists(), "Raw source video was deleted during finalize stage!")
        self.assertGreater(video_path.stat().st_size, 0)

    def test_f05_finalize_marks_chunk_status_processed(self):
        """F5.2: Finalize stage transitions chunk status in database to 'processed'."""
        setup_data = self.register_test_chunk_and_job(filename="raw_f05_status.mp4")
        runner = PipelineStageRunner()
        chunk_dict = {
            "id": setup_data["chunk_id"],
            "filename": setup_data["filepath"].name,
            "filepath": str(setup_data["filepath"])
        }
        job_dict = {"job_id": setup_data["job_id"], "chunk_id": setup_data["chunk_id"]}

        runner._run_finalize_stage(job=job_dict, chunk=chunk_dict, filepath=setup_data["filepath"])

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM chunks WHERE id = ?", (setup_data["chunk_id"],))
            status = cursor.fetchone()["status"]
            self.assertEqual(status, "processed")

    def test_f05_finalize_preserves_multi_segment_raw_files(self):
        """F5.3: Multiple consecutive raw segment videos are all preserved on disk."""
        files = []
        for i in range(3):
            setup = self.register_test_chunk_and_job(filename=f"raw_multi_seg_{i}.mp4")
            files.append((setup["job_id"], setup["chunk_id"], setup["filepath"]))

        runner = PipelineStageRunner()
        for j_id, c_id, path in files:
            runner._run_finalize_stage(
                job={"job_id": j_id, "chunk_id": c_id},
                chunk={"id": c_id, "filename": path.name, "filepath": str(path)},
                filepath=path
            )

        for _, _, path in files:
            self.assertTrue(path.exists(), f"Multi-segment source {path.name} was deleted!")

    def test_f05_finalize_idempotency_does_not_truncate_file(self):
        """F5.4: Calling finalize multiple times leaves source bytes intact."""
        setup = self.register_test_chunk_and_job(filename="raw_f05_idemp.mp4")
        path = setup["filepath"]
        orig_size = path.stat().st_size

        runner = PipelineStageRunner()
        for _ in range(3):
            runner._run_finalize_stage(
                job={"job_id": setup["job_id"], "chunk_id": setup["chunk_id"]},
                chunk={"id": setup["chunk_id"], "filename": path.name, "filepath": str(path)},
                filepath=path
            )

        self.assertEqual(path.stat().st_size, orig_size)

    def test_f05_pipeline_runner_end_to_end_preserves_source(self):
        """F5.5: Full 5-stage runner execution completes with exit status 0 and source video intact."""
        setup = self.register_test_chunk_and_job(filename="pipe_f05_full.mp4")
        job_id = setup["job_id"]
        source_path = setup["filepath"]

        runner = PipelineStageRunner()
        mock_meta = {"duration": 45.0, "aspect_ratio": "16:9", "resolution": (1280, 720)}

        mock_trans = {
            "text": "Suno aaj hum content creation automate karenge",
            "segments": [{"id": 0, "start": 0.0, "end": 5.0, "text": "Suno aaj hum content creation automate karenge"}]
        }

        with patch("dispatch.orchestrator.pipeline_runner.probe_video", return_value=(True, mock_meta, None)):
            with patch("dispatch.orchestrator.pipeline_runner.transcribe_video", return_value=mock_trans):
                with patch("dispatch.orchestrator.pipeline_runner.identify_and_save_highlights", return_value=["clip_mock_1"]):
                    with patch("dispatch.orchestrator.pipeline_runner.render_clip", return_value={"output": "clip.mp4"}):
                        # Walk all 5 stages
                        for stage in [PipelineStage.VERIFY, PipelineStage.TRANSCRIBE, PipelineStage.ANALYZE, PipelineStage.RENDER, PipelineStage.FINALIZE]:
                            job = claim_job("worker_full_pres")
                            self.assertIsNotNone(job)
                            success = runner.process_job_step(job, "worker_full_pres")
                            self.assertTrue(success, f"Failed at stage {stage.value}")

        self.assertTrue(source_path.exists(), "Source video was deleted during full pipeline run!")

    # =========================================================================
    # FEATURE 6: Retry Cap & Permanent Failure Classification
    # =========================================================================

    def test_f06_retry_cap_terminates_after_max_attempts(self):
        """F6.1: Job failing max_attempts (5) transitions to FAILED_PERMANENT."""
        setup = self.register_test_chunk_and_job(filename="pipe_f06_cap.mp4")
        job_id = setup["job_id"]

        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET attempt_count = 5 WHERE job_id = ?", (job_id,))

        fail_stage_job(job_id=job_id, error_message="Persistent FFmpeg error")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, last_error FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            self.assertEqual(row["status"], "FAILED_PERMANENT")
            self.assertEqual(row["last_error"], "Persistent FFmpeg error")

    def test_f06_fatal_container_corruption_immediately_classified_permanent(self):
        """F6.2: Fatal container corruption (missing moov atom) is immediately classified FAILED_PERMANENT."""
        status, is_perm, reason = RetryEngine.classify_error(ValueError("moov atom not found in video"))
        self.assertEqual(status, JobStatus.FAILED_PERMANENT)
        self.assertTrue(is_perm)
        self.assertIn("moov atom", reason)

    def test_f06_exponential_backoff_delay_calculation(self):
        """F6.3: RetryEngine calculates progressive exponential backoff delays."""
        engine = RetryEngine(base_delay_seconds=10, max_delay_seconds=300)
        delay1 = engine.compute_backoff_seconds(1)
        delay2 = engine.compute_backoff_seconds(2)
        delay3 = engine.compute_backoff_seconds(3)
        self.assertGreaterEqual(delay1, 5)
        self.assertGreaterEqual(delay2, delay1)
        self.assertGreaterEqual(delay3, delay2)

    def test_f06_rate_limit_429_transitions_to_waiting_for_ai(self):
        """F6.4: HTTP 429 rate limit is classified as WAITING_FOR_AI without permanent failure."""
        status, is_perm, reason = RetryEngine.classify_error(Exception("HTTP 429 Too Many Requests: quota exceeded"))
        self.assertEqual(status, JobStatus.WAITING_FOR_AI)
        self.assertFalse(is_perm)

        setup = self.register_test_chunk_and_job(filename="pipe_f06_429.mp4")
        fail_stage_job(setup["job_id"], "Quota exceeded", wait_state=JobStatus.WAITING_FOR_AI)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM pipeline_jobs WHERE job_id = ?", (setup["job_id"],))
            self.assertEqual(cursor.fetchone()["status"], "WAITING_FOR_AI")

    def test_f06_resource_governor_hold_transitions_to_waiting_for_resources(self):
        """F6.5: Low disk space or CPU hold transitions to WAITING_FOR_RESOURCES."""
        status, is_perm, reason = RetryEngine.classify_error(IOError("[Errno 28] No space left on device"))
        self.assertEqual(status, JobStatus.WAITING_FOR_RESOURCES)
        self.assertFalse(is_perm)

        setup = self.register_test_chunk_and_job(filename="pipe_f06_disk.mp4")
        fail_stage_job(setup["job_id"], "Low disk space", wait_state=JobStatus.WAITING_FOR_RESOURCES)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM pipeline_jobs WHERE job_id = ?", (setup["job_id"],))
            self.assertEqual(cursor.fetchone()["status"], "WAITING_FOR_RESOURCES")

    def test_f06_claim_job_ignores_failed_permanent_jobs(self):
        """F6.6: claim_job never allocates jobs marked FAILED_PERMANENT to workers."""
        setup = self.register_test_chunk_and_job(filename="pipe_f06_ignored.mp4")
        job_id = setup["job_id"]

        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET status = 'FAILED_PERMANENT' WHERE job_id = ?", (job_id,))

        claimed = claim_job("worker_should_fail")
        self.assertIsNone(claimed)
