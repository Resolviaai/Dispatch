"""Unit test for Phase 5: Pipeline Checkpointing & Centralized Retry Engine."""
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from dispatch import db
from dispatch.db import init_db, register_chunk, get_db_connection
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.job_queue import init_job_queue_schema, enqueue_job, claim_job
from dispatch.orchestrator.retry_engine import RetryEngine
from dispatch.orchestrator.pipeline_runner import PipelineStageRunner
from dispatch.governor.resource_governor import ResourceGovernor, GovernorPolicy


class TestPipelineCheckpointingAndRetry(unittest.TestCase):
    def setUp(self):
        init_db()
        init_job_queue_schema()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM pipeline_jobs")
        self.retry_engine = RetryEngine(base_delay_seconds=10, max_delay_seconds=60)

    def test_retry_engine_backoff_and_classification(self):
        # 1. Backoff delays
        delay1 = self.retry_engine.compute_backoff_seconds(1)
        delay3 = self.retry_engine.compute_backoff_seconds(3)
        self.assertGreaterEqual(delay1, 5)
        self.assertGreaterEqual(delay3, delay1)

        # 2. AI 429 Rate limit classification
        status, perm, reason = RetryEngine.classify_error(Exception("HTTP 429 Too Many Requests: quota exceeded"))
        self.assertEqual(status, JobStatus.WAITING_FOR_AI)
        self.assertFalse(perm)

        # 3. Disk space error classification
        status, perm, reason = RetryEngine.classify_error(IOError("[Errno 28] No space left on device"))
        self.assertEqual(status, JobStatus.WAITING_FOR_RESOURCES)
        self.assertFalse(perm)

        # 4. Permanent corrupt file classification
        status, perm, reason = RetryEngine.classify_error(ValueError("moov atom not found in video"))
        self.assertEqual(status, JobStatus.FAILED_PERMANENT)
        self.assertTrue(perm)

    def test_checkpointed_stage_execution_and_resumption(self):
        # Create a mock video file in temp
        temp_dir = Path(tempfile.mkdtemp(prefix="dispatch_pipe_test_"))
        dummy_video = temp_dir / "sample_pipe.mp4"
        with open(dummy_video, "wb") as f:
            f.write(b"DUMMY_MP4_HEADER_CONTENT_BYTES" * 100)

        chunk_id = register_chunk(
            session_id="pipe_sess_01",
            filename="sample_pipe.mp4",
            filepath=str(dummy_video),
            file_hash="mock_hash_pipe"
        )
        job_id = enqueue_job(chunk_id=chunk_id, session_id="pipe_sess_01")

        runner = PipelineStageRunner()

        # Step 1: VERIFY stage
        job = claim_job("test_worker_1")
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["current_stage"], "VERIFY")

        # Mock probe_video to pass
        mock_meta = {"duration": 45.0, "aspect_ratio": "16:9", "resolution": (1920, 1080)}
        with patch("dispatch.orchestrator.pipeline_runner.probe_video", return_value=(True, mock_meta, None)):
            success = runner.process_job_step(job, "test_worker_1")
            self.assertTrue(success)

        # Step 2: Job should now be at TRANSCRIBE stage
        job_t = claim_job("test_worker_1")
        self.assertEqual(job_t["current_stage"], "TRANSCRIBE")

        # Mock transcription
        mock_trans = {
            "text": "Yeh pehla segment hai jo automate ho chuka hai",
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.5, "text": "Yeh pehla segment hai"},
                {"id": 1, "start": 2.5, "end": 5.0, "text": "jo automate ho chuka hai"}
            ]
        }
        def fake_transcribe(**kwargs):
            cid = kwargs.get("chunk_id")
            db.save_transcript(
                chunk_id=cid,
                session_id=kwargs.get("session_id"),
                full_text=mock_trans["text"],
                segments=mock_trans["segments"]
            )
            return mock_trans

        with patch("dispatch.orchestrator.pipeline_runner.transcribe_video", side_effect=fake_transcribe):
            success = runner.process_job_step(job_t, "test_worker_1")
            self.assertTrue(success)

        # Verify transcript was stored in database
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_text FROM transcripts WHERE chunk_id = ?", (chunk_id,))
            t_row = cursor.fetchone()
            self.assertIsNotNone(t_row)
            self.assertIn("Yeh pehla segment", t_row["full_text"])

        # Step 3: Job should now be at ANALYZE stage
        job_a = claim_job("test_worker_1")
        self.assertEqual(job_a["current_stage"], "ANALYZE")

        # Mock AI highlight finder returning 1 clip
        clip_mock_id = f"clip_test_{chunk_id}_01"
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO clips (id, chunk_id, session_id, start_time, end_time, duration, title, hook, layout_mode, status)
                VALUES (?, ?, ?, 0.0, 5.0, 5.0, 'Auto Title', 'Hook Test', 'fit_blur', 'review_pending')
            """, (clip_mock_id, chunk_id, "pipe_sess_01"))

        with patch("dispatch.orchestrator.pipeline_runner.identify_and_save_highlights", return_value=[clip_mock_id]):
            success = runner.process_job_step(job_a, "test_worker_1")
            self.assertTrue(success)

        # Step 4: Job should now be at RENDER stage
        job_r = claim_job("test_worker_1")
        self.assertEqual(job_r["current_stage"], "RENDER")

        # Render clip mock
        rendered_mp4 = temp_dir / "rendered_output.mp4"
        with open(rendered_mp4, "wb") as f:
            f.write(b"RENDERED_VIDEO_DATA")

        def fake_render(**kwargs):
            with get_db_connection() as conn:
                conn.execute("UPDATE clips SET video_path = ? WHERE id = ?", (str(rendered_mp4), clip_mock_id))

        with patch("dispatch.orchestrator.pipeline_runner.render_clip", side_effect=fake_render):
            with patch("dispatch.orchestrator.pipeline_runner.probe_video", return_value=(True, mock_meta, None)):
                success = runner.process_job_step(job_r, "test_worker_1")
                self.assertTrue(success)

        # Step 5: Job should now be at FINALIZE stage
        job_f = claim_job("test_worker_1")
        self.assertEqual(job_f["current_stage"], "FINALIZE")

        success = runner.process_job_step(job_f, "test_worker_1")
        self.assertTrue(success)

        # Verify job is now COMPLETED
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, current_stage FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            final_job = cursor.fetchone()
            self.assertEqual(final_job["status"], "COMPLETED")
            self.assertEqual(final_job["current_stage"], "COMPLETED")

        # Verify raw file was safely cleaned up
        self.assertFalse(dummy_video.exists())

    def test_governor_hold_transitions_to_waiting_for_resources(self):
        # Register a chunk and job at TRANSCRIBE stage
        temp_dir = Path(tempfile.mkdtemp(prefix="dispatch_gov_hold_"))
        dummy_video = temp_dir / "sample_hold.mp4"
        with open(dummy_video, "wb") as f:
            f.write(b"SAMPLE_VIDEO_DATA")

        chunk_id = register_chunk(
            session_id="pipe_hold_01",
            filename="sample_hold.mp4",
            filepath=str(dummy_video),
            file_hash="mock_hash_hold"
        )
        job_id = enqueue_job(chunk_id=chunk_id, session_id="pipe_hold_01")

        # Advance to TRANSCRIBE stage
        with get_db_connection() as conn:
            conn.execute("UPDATE pipeline_jobs SET current_stage = 'TRANSCRIBE' WHERE job_id = ?", (job_id,))

        job = claim_job("test_worker_gov")
        self.assertEqual(job["current_stage"], "TRANSCRIBE")

        # Mock governor reporting low disk space
        mock_gov = MagicMock()
        mock_gov.can_process_heavy_task.return_value = (False, "Low disk space (2.1 GB free < 5.0 GB minimum)")

        runner = PipelineStageRunner(governor=mock_gov)
        result = runner.process_job_step(job, "test_worker_gov")
        self.assertFalse(result)

        # Verify job transitioned to WAITING_FOR_RESOURCES
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, last_error FROM pipeline_jobs WHERE job_id = ?", (job_id,))
            j_row = cursor.fetchone()
            self.assertEqual(j_row["status"], "WAITING_FOR_RESOURCES")
            self.assertIn("Resource governor hold", j_row["last_error"])


if __name__ == "__main__":
    unittest.main()

