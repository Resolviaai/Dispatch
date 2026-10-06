"""Persistent Job Queue with Heartbeat Leases and Stage Checkpointing.
Every media processing stage is persisted to SQLite with lease expiration to ensure
automatic crash recovery and prevent zombie processing jobs.
"""
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from dispatch.db import get_db_connection
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus, get_next_stage

logger = logging.getLogger("dispatch.orchestrator.queue")


def init_job_queue_schema():
    """Ensure pipeline_jobs table and indexes exist in SQLite database."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                session_id TEXT,
                current_stage TEXT NOT NULL DEFAULT 'VERIFY',
                status TEXT NOT NULL DEFAULT 'QUEUED',
                worker_id TEXT,
                lease_expires_at TIMESTAMP,
                heartbeat_at TIMESTAMP,
                attempt_count INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                last_error TEXT,
                next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES chunks (id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_claim 
            ON pipeline_jobs (status, next_retry_at, lease_expires_at);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_chunk 
            ON pipeline_jobs (chunk_id, status);
        """)


def enqueue_job(chunk_id: str, session_id: Optional[str] = None) -> str:
    """Enqueue a new chunk processing job starting at VERIFY stage.
    Deduplicates active jobs: if an active job already exists for chunk_id, returns it.
    """
    init_job_queue_schema()
    now_str = datetime.now().isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT job_id FROM pipeline_jobs
            WHERE chunk_id = ? AND status NOT IN ('COMPLETED', 'FAILED_PERMANENT')
            LIMIT 1
        """, (chunk_id,))
        existing = cursor.fetchone()
        if existing:
            logger.info("Active pipeline job %s already exists for chunk %s, reusing.", existing["job_id"], chunk_id)
            return existing["job_id"]

        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        conn.execute("""
            INSERT OR IGNORE INTO pipeline_jobs (
                job_id, chunk_id, session_id, current_stage, status, next_retry_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'VERIFY', 'QUEUED', ?, ?, ?)
        """, (job_id, chunk_id, session_id, now_str, now_str, now_str))

    logger.info("Enqueued pipeline job %s for chunk %s", job_id, chunk_id)
    return job_id


class LeaseLostError(RuntimeError):
    """Raised when a worker attempts to complete a job whose lease expired or was reclaimed."""
    pass


def claim_job(worker_id: str, lease_duration_seconds: int = 300) -> Optional[Dict[str, Any]]:
    """Atomically claim the next eligible job and grant a timed lease.
    Uses atomic UPDATE ... RETURNING * to eliminate any race condition.
    """
    init_job_queue_schema()
    now_dt = datetime.now()
    now_str = now_dt.isoformat()
    lease_until = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pipeline_jobs
            SET status = 'PROCESSING',
                worker_id = ?,
                lease_expires_at = ?,
                heartbeat_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE job_id = (
                SELECT job_id FROM pipeline_jobs
                WHERE status IN ('QUEUED', 'RETRY_PENDING', 'WAITING_FOR_AI', 'WAITING_FOR_RESOURCES')
                  AND datetime(next_retry_at) <= datetime(?)
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *;
        """, (worker_id, lease_until, now_str, now_str, now_str))
        claimed = cursor.fetchone()

        if not claimed:
            return None

        claimed_dict = dict(claimed)
        logger.info("Worker %s claimed job %s at stage %s (Lease: %ds)",
                    worker_id, claimed_dict["job_id"], claimed_dict["current_stage"], lease_duration_seconds)
        return claimed_dict


def renew_heartbeat(job_id: str, worker_id: str, extend_seconds: int = 300) -> bool:
    """Worker renews lease on active job to signal it is alive."""
    now_dt = datetime.now()
    now_str = now_dt.isoformat()
    lease_until = (now_dt + timedelta(seconds=extend_seconds)).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pipeline_jobs
            SET lease_expires_at = ?,
                heartbeat_at = ?,
                updated_at = ?
            WHERE job_id = ? AND worker_id = ? AND status = 'PROCESSING'
        """, (lease_until, now_str, now_str, job_id, worker_id))
        return cursor.rowcount > 0


def complete_stage_checkpoint(
    job_id: str,
    current_stage: PipelineStage,
    worker_id: Optional[str] = None
) -> PipelineStage:
    """Record completed stage checkpoint and transition job to the next stage.
    Fencing token verification: if worker_id is provided, strictly enforces that the
    worker still owns an unexpired active lease, preventing split-brain zombie workers.
    """
    next_stage = get_next_stage(current_stage)
    next_status = JobStatus.COMPLETED.value if next_stage == PipelineStage.COMPLETED else JobStatus.QUEUED.value
    now_str = datetime.now().isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if worker_id:
            cursor.execute("""
                UPDATE pipeline_jobs
                SET current_stage = ?,
                    status = ?,
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE job_id = ? 
                  AND worker_id = ? 
                  AND current_stage = ?
                  AND status = 'PROCESSING'
                  AND datetime(lease_expires_at) >= datetime(?)
            """, (next_stage.value, next_status, now_str, job_id, worker_id, current_stage.value, now_str))

            if cursor.rowcount == 0:
                raise LeaseLostError(
                    f"Fencing check failed for job {job_id}: worker {worker_id} lost lease for stage {current_stage.value}."
                )
        else:
            conn.execute("""
                UPDATE pipeline_jobs
                SET current_stage = ?,
                    status = ?,
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE job_id = ?
            """, (next_stage.value, next_status, now_str, job_id))

    logger.info("Job %s passed checkpoint: %s -> %s (Status: %s)",
                job_id, current_stage.value, next_stage.value, next_status)
    return next_stage


def fail_stage_job(
    job_id: str,
    error_message: str,
    wait_state: Optional[JobStatus] = None,
    delay_seconds: int = 30
):
    """Mark job as failed or waiting (e.g. WAITING_FOR_AI, WAITING_FOR_RESOURCES) with retry delay."""
    now_dt = datetime.now()
    now_str = now_dt.isoformat()
    retry_time = (now_dt + timedelta(seconds=delay_seconds)).isoformat()
    status_to_set = wait_state.value if wait_state else JobStatus.RETRY_PENDING.value

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT attempt_count, max_attempts FROM pipeline_jobs WHERE job_id = ?", (job_id,))
        job = cursor.fetchone()
        attempts = job["attempt_count"] if job else 1
        max_att = job["max_attempts"] if job else 5

        if attempts >= max_att and not wait_state:
            status_to_set = JobStatus.FAILED_PERMANENT.value

        cursor.execute("""
            UPDATE pipeline_jobs
            SET status = ?,
                last_error = ?,
                worker_id = NULL,
                lease_expires_at = NULL,
                next_retry_at = ?,
                updated_at = ?
            WHERE job_id = ?
        """, (status_to_set, error_message, retry_time, now_str, job_id))

    logger.warning("Job %s transitioned to %s (Next retry in %ds): %s",
                   job_id, status_to_set, delay_seconds, error_message)
