"""Unit test for Phase 3: Persistent job queue, leases, checkpoints, and crash recovery."""
from datetime import datetime, timedelta
from dispatch.db import init_db, register_chunk, get_db_connection
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus
from dispatch.orchestrator.job_queue import (
    init_job_queue_schema,
    enqueue_job,
    claim_job,
    renew_heartbeat,
    complete_stage_checkpoint,
    fail_stage_job
)
from dispatch.orchestrator.recovery import recover_laptop_orchestrator


def test_job_queue_and_recovery():
    init_db()
    init_job_queue_schema()

    # 1. Register a test chunk
    chunk_id = register_chunk(
        session_id="test_sess_p3",
        filename="test_chunk_p3.mp4",
        filepath="storage/processing/test_chunk_p3.mp4",
        file_hash="dummy_hash_p3"
    )

    # 2. Enqueue job
    job_id = enqueue_job(chunk_id=chunk_id, session_id="test_sess_p3")
    assert job_id is not None

    # 3. Worker 1 claims job
    claimed = claim_job(worker_id="worker_alpha", lease_duration_seconds=30)
    assert claimed is not None
    assert claimed["job_id"] == job_id
    assert claimed["current_stage"] == "VERIFY"
    assert claimed["status"] == "PROCESSING"

    # 4. Advance checkpoint: VERIFY -> TRANSCRIBE
    next_stg = complete_stage_checkpoint(job_id, PipelineStage.VERIFY)
    assert next_stg == PipelineStage.TRANSCRIBE

    # Worker claims job at TRANSCRIBE stage
    claimed2 = claim_job(worker_id="worker_alpha", lease_duration_seconds=30)
    assert claimed2["job_id"] == job_id
    assert claimed2["current_stage"] == "TRANSCRIBE"

    # 5. SIMULATE POWER LOSS / WORKER CRASH:
    # Expire the lease in the past
    past_time = (datetime.now() - timedelta(minutes=5)).isoformat()
    with get_db_connection() as conn:
        conn.execute("UPDATE pipeline_jobs SET lease_expires_at = ? WHERE job_id = ?", (past_time, job_id))

    # Run boot recovery
    recovery = recover_laptop_orchestrator()
    assert job_id in recovery["reclaimed_jobs"]

    # Verify job is RETRY_PENDING and retained stage TRANSCRIBE (checkpoint preserved!)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, current_stage, worker_id FROM pipeline_jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        assert row["status"] == "RETRY_PENDING"
        assert row["current_stage"] == "TRANSCRIBE", "Job should have resumed from TRANSCRIBE checkpoint!"
        assert row["worker_id"] is None

    # 6. Worker 2 claims and finishes through all stages
    claimed3 = claim_job(worker_id="worker_beta", lease_duration_seconds=30)
    assert claimed3["job_id"] == job_id
    assert claimed3["current_stage"] == "TRANSCRIBE"

    # Advance TRANSCRIBE -> ANALYZE
    complete_stage_checkpoint(job_id, PipelineStage.TRANSCRIBE)
    claimed4 = claim_job(worker_id="worker_beta")
    assert claimed4["current_stage"] == "ANALYZE"

    # Advance ANALYZE -> RENDER
    complete_stage_checkpoint(job_id, PipelineStage.ANALYZE)
    claimed5 = claim_job(worker_id="worker_beta")
    assert claimed5["current_stage"] == "RENDER"

    # Advance RENDER -> FINALIZE
    complete_stage_checkpoint(job_id, PipelineStage.RENDER)
    claimed6 = claim_job(worker_id="worker_beta")
    assert claimed6["current_stage"] == "FINALIZE"

    # Advance FINALIZE -> COMPLETED
    final_stg = complete_stage_checkpoint(job_id, PipelineStage.FINALIZE)
    assert final_stg == PipelineStage.COMPLETED

    # Verify final status in DB
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, current_stage FROM pipeline_jobs WHERE job_id = ?", (job_id,))
        final_row = cursor.fetchone()
        assert final_row["status"] == "COMPLETED"
        assert final_row["current_stage"] == "COMPLETED"

    print("ALL JOB QUEUE & RECOVERY TESTS (PHASE 3) PASSED 100% SUCCESSFULLY!")


if __name__ == "__main__":
    test_job_queue_and_recovery()
