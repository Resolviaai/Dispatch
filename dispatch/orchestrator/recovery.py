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


def reconcile_interrupted_inbox_stages(
    is_boot: bool = False,
    stale_timeout_seconds: int = 900
) -> List[Dict[str, Any]]:
    """Reconcile youtube_inbox items that were interrupted during active processing stages.

    Covers TRANSCRIBING (transcription worker) and ANALYZING (highlight worker),
    plus DOWNLOADING. On boot, all rows in these active processing states are known
    to be orphaned by the previous shutdown/crash and are immediately reclaimed.
    During runtime sweeps, rows are reclaimed if inactive beyond stale_timeout_seconds.
    Fails closed to 'FAILED' after 5 retries to prevent infinite retry loops.
    """
    reconciled = []
    with get_db_connection() as conn:
        cursor = conn.cursor()

        if is_boot:
            cursor.execute("""
                SELECT video_id, status, retry_count, ai_attempt_count, updated_at
                FROM youtube_inbox
                WHERE status IN ('TRANSCRIBING', 'ANALYZING', 'DOWNLOADING')
            """)
        else:
            cursor.execute(f"""
                SELECT video_id, status, retry_count, ai_attempt_count, updated_at
                FROM youtube_inbox
                WHERE status IN ('TRANSCRIBING', 'ANALYZING', 'DOWNLOADING')
                  AND datetime(updated_at) < datetime('now', '-{int(stale_timeout_seconds)} seconds')
            """)

        stuck_items = cursor.fetchall()
        for item in stuck_items:
            video_id = item["video_id"]
            old_status = item["status"]
            current_retries = item["retry_count"] or 0
            current_ai_attempts = item["ai_attempt_count"] or 0

            if old_status == "TRANSCRIBING":
                new_retries = current_retries + 1
                if new_retries > 5:
                    new_status = "FAILED"
                    err = f"Exceeded retry limit (5) during transcription (failed at {new_retries} attempts)"
                else:
                    new_status = "DOWNLOADED"
                    err = "Transcription interrupted by crash/restart; resetting to DOWNLOADED for retry"
                cursor.execute("""
                    UPDATE youtube_inbox
                    SET status = ?, retry_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE video_id = ?
                """, (new_status, new_retries, err, video_id))

            elif old_status == "ANALYZING":
                new_retries = max(current_retries, current_ai_attempts) + 1
                if new_retries > 5:
                    new_status = "FAILED"
                    err = f"Exceeded retry limit (5) during highlight analysis (failed at {new_retries} attempts)"
                else:
                    new_status = "TRANSCRIBED"
                    err = "Highlight analysis interrupted by crash/restart; resetting to TRANSCRIBED for retry"
                cursor.execute("""
                    UPDATE youtube_inbox
                    SET status = ?, retry_count = ?, ai_attempt_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE video_id = ?
                """, (new_status, new_retries, new_retries, err, video_id))

            elif old_status == "DOWNLOADING":
                new_retries = current_retries + 1
                if new_retries > 5:
                    new_status = "FAILED"
                    err = f"Exceeded retry limit (5) during video download (failed at {new_retries} attempts)"
                else:
                    new_status = "DISCOVERED"
                    err = "Download interrupted by crash/restart; resetting to DISCOVERED for retry"
                cursor.execute("""
                    UPDATE youtube_inbox
                    SET status = ?, retry_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE video_id = ?
                """, (new_status, new_retries, err, video_id))
            else:
                continue

            reconciled.append({
                "video_id": video_id,
                "old_status": old_status,
                "new_status": new_status,
                "retry_count": new_retries,
            })
            logger.info("Reconciled interrupted inbox item %s: %s -> %s (attempt %d)",
                        video_id, old_status, new_status, new_retries)

        conn.commit()

    return reconciled


def recover_laptop_orchestrator(is_boot: bool = True, stale_timeout_seconds: int = 900) -> Dict[str, Any]:
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

    # 2. Reconcile interrupted active processing stages in youtube_inbox
    reclaimed_inbox = reconcile_interrupted_inbox_stages(
        is_boot=is_boot,
        stale_timeout_seconds=stale_timeout_seconds
    )

    # 3. Clean temporary files in processing folder without touching source chunks
    cleaned_temp_files = 0
    if PROCESSING_DIR.exists():
        for f in PROCESSING_DIR.iterdir():
            if f.is_file() and f.suffix in (".tmp", ".part", ".ass"):
                try:
                    f.unlink()
                    cleaned_temp_files += 1
                except Exception as e:
                    logger.debug("Could not clean temp file %s: %s", f.name, e)

    if reclaimed_jobs or reclaimed_inbox:
        logger.info("Laptop orchestrator recovery complete: %d jobs reclaimed, %d inbox items reconciled, %d temp files cleaned",
                    len(reclaimed_jobs), len(reclaimed_inbox), cleaned_temp_files)

    return {
        "reclaimed_jobs": reclaimed_jobs,
        "reclaimed_inbox_items": reclaimed_inbox,
        "cleaned_temp_files": cleaned_temp_files
    }
