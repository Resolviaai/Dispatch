"""Laptop Crash and Power Loss Recovery Engine.
Reclaims expired worker leases after sudden power loss, process crashes, or system restarts.
Ensures jobs resume from the exact last completed stage checkpoint.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from dispatch.db import get_db_connection
from dispatch.config import PROCESSING_DIR
from dispatch.orchestrator.job_queue import init_job_queue_schema

logger = logging.getLogger("dispatch.orchestrator.recovery")


def recover_laptop_orchestrator() -> Dict[str, Any]:
    """Scan and recover orphaned jobs and clean temporary artifacts on boot or heartbeat tick."""
    init_job_queue_schema()
    now_str = datetime.now().isoformat()
    reclaimed_jobs = []

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Identify jobs whose worker lease has expired (stale / crashed worker)
        cursor.execute("""
            SELECT job_id, chunk_id, current_stage, worker_id, attempt_count
            FROM pipeline_jobs
            WHERE status = 'PROCESSING'
              AND datetime(lease_expires_at) < datetime(?)
        """, (now_str,))
        expired = cursor.fetchall()

        for job in expired:
            job_id = job["job_id"]
            stage = job["current_stage"]

            cursor.execute("""
                UPDATE pipeline_jobs
                SET status = 'RETRY_PENDING',
                    worker_id = NULL,
                    lease_expires_at = NULL,
                    last_error = 'Worker lease expired; recovered on restart',
                    next_retry_at = ?,
                    updated_at = ?
                WHERE job_id = ?
            """, (now_str, now_str, job_id))

            reclaimed_jobs.append(job_id)
            logger.info("Reclaimed stale job %s from failed worker (Resuming at stage %s)", job_id, stage)

    # 2. Clean temporary files in processing folder without touching source chunks
    cleaned_temp_files = 0
    if PROCESSING_DIR.exists():
        for f in PROCESSING_DIR.iterdir():
            if f.is_file() and f.suffix in (".tmp", ".part", ".ass"):
                try:
                    f.unlink()
                    cleaned_temp_files += 1
                except Exception as e:
                    logger.debug("Could not clean temp file %s: %s", f.name, e)

    if reclaimed_jobs:
        logger.info("Laptop orchestrator recovery complete: %d jobs reclaimed, %d temp files cleaned",
                    len(reclaimed_jobs), cleaned_temp_files)

    return {
        "reclaimed_jobs": reclaimed_jobs,
        "cleaned_temp_files": cleaned_temp_files
    }
