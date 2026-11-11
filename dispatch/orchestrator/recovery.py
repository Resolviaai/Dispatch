"""Laptop Crash and Power Loss Recovery Engine.
Reclaims expired worker leases after sudden power loss, process crashes, or system restarts.
Ensures jobs resume from the exact last completed stage checkpoint.
"""
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from dispatch.db import get_db_connection
from dispatch.config import PROCESSING_DIR
from dispatch.orchestrator.job_queue import init_job_queue_schema

logger = logging.getLogger("dispatch.orchestrator.recovery")


def sanitize_job_workspace_id(job_id: str) -> str:
    """Sanitize a job or video identifier into a safe single directory component."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(job_id or "").strip()).strip("._")
    return cleaned or "unknown"


def get_job_workspace_dir(job_id: str, base_dir: Optional[Path] = None) -> Path:
    """Return the isolated per-job temp directory at PROCESSING_DIR/jobs/<job_id>/."""
    root = base_dir if base_dir is not None else PROCESSING_DIR
    job_dir = root / "jobs" / sanitize_job_workspace_id(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def _newest_tree_mtime(path: Path) -> float:
    """Return the newest mtime across a directory and its descendants."""
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0
    for root, dirs, files in os.walk(path):
        for name in (*dirs, *files):
            try:
                st = (Path(root) / name).stat()
                if st.st_mtime > newest:
                    newest = st.st_mtime
            except OSError:
                continue
    return newest


def _count_files_in_tree(path: Path) -> int:
    """Count regular files inside a directory tree safely."""
    count = 0
    for _, _, files in os.walk(path):
        count += len(files)
    return count


def _collect_active_workspace_keys(
    conn,
    now_str: str,
    is_boot: bool,
    stale_timeout_seconds: int,
) -> Set[str]:
    """Identify workspace keys belonging to live jobs with valid leases or fresh heartbeats."""
    active_keys: Set[str] = set()
    cursor = conn.cursor()

    # 1. Pipeline jobs holding an active unexpired lease
    cursor.execute("""
        SELECT job_id, chunk_id
        FROM pipeline_jobs
        WHERE status = 'PROCESSING'
          AND (
              (lease_expires_at IS NOT NULL AND datetime(lease_expires_at) >= datetime(?))
              OR (heartbeat_at IS NOT NULL AND datetime(heartbeat_at) >= datetime(?, '-' || ? || ' seconds'))
          )
    """, (now_str, now_str, int(stale_timeout_seconds)))
    active_chunk_ids: Set[str] = set()
    for row in cursor.fetchall():
        if row["job_id"]:
            active_keys.add(sanitize_job_workspace_id(row["job_id"]))
        if row["chunk_id"]:
            active_chunk_ids.add(row["chunk_id"])
            active_keys.add(sanitize_job_workspace_id(row["chunk_id"]))

    # 2. Active youtube_inbox stages (on runtime sweeps, protected if updated_at is fresh)
    if is_boot:
        active_yt_rows = []
    else:
        cursor.execute("""
            SELECT video_id
            FROM youtube_inbox
            WHERE status IN ('TRANSCRIBING', 'ANALYZING', 'DOWNLOADING', 'VALIDATING', 'RENDERING')
              AND datetime(updated_at) >= datetime('now', '-' || ? || ' seconds')
        """, (int(stale_timeout_seconds),))
        active_yt_rows = cursor.fetchall()

    active_video_ids: Set[str] = set()
    for row in active_yt_rows:
        vid = row["video_id"]
        if vid:
            active_video_ids.add(vid)
            active_keys.add(sanitize_job_workspace_id(vid))
            active_keys.add(sanitize_job_workspace_id(f"yt_{vid}"))

    # Map active chunk_ids <-> video_ids and protect their clip workspaces
    if active_video_ids:
        placeholders = ",".join("?" for _ in active_video_ids)
        hashes = [f"yt_{v}" for v in active_video_ids]
        cursor.execute(f"SELECT id FROM chunks WHERE file_hash IN ({placeholders})", tuple(hashes))
        for r in cursor.fetchall():
            active_chunk_ids.add(r["id"])
            active_keys.add(sanitize_job_workspace_id(r["id"]))

    if active_chunk_ids:
        placeholders = ",".join("?" for _ in active_chunk_ids)
        cursor.execute(f"SELECT file_hash FROM chunks WHERE id IN ({placeholders})", tuple(active_chunk_ids))
        for r in cursor.fetchall():
            fh = r["file_hash"] or ""
            if fh.startswith("yt_"):
                active_keys.add(sanitize_job_workspace_id(fh[3:]))
        cursor.execute(f"SELECT id FROM clips WHERE chunk_id IN ({placeholders})", tuple(active_chunk_ids))
        for r in cursor.fetchall():
            cid = r["id"]
            active_keys.add(sanitize_job_workspace_id(cid))
            active_keys.add(sanitize_job_workspace_id(f"clip_{cid}"))
            active_keys.add(sanitize_job_workspace_id(f"drain_{cid}"))

    # 3. Active outbox/rendering clip workspaces
    if not is_boot:
        try:
            cursor.execute("""
                SELECT clip_id FROM publishing_outbox
                WHERE status IN ('uploading', 'processing')
            """)
            for r in cursor.fetchall():
                cid = r["clip_id"]
                if cid:
                    active_keys.add(sanitize_job_workspace_id(cid))
                    active_keys.add(sanitize_job_workspace_id(f"clip_{cid}"))
                    active_keys.add(sanitize_job_workspace_id(f"drain_{cid}"))
        except Exception:
            pass

    return active_keys


def reconcile_interrupted_inbox_stages(
    is_boot: bool = False,
    stale_timeout_seconds: int = 900
) -> List[Dict[str, Any]]:
    """Reconcile youtube_inbox items that were interrupted during active processing stages.

    Covers TRANSCRIBING (transcription worker), ANALYZING (highlight worker),
    DOWNLOADING, VALIDATING (catcher ffprobe check), and RENDERING (clip renderer).
    On boot, all rows in these active processing states are known to be orphaned by
    the previous shutdown/crash and are immediately reclaimed.
    During runtime sweeps, rows are reclaimed if inactive beyond stale_timeout_seconds.
    Fails closed to 'FAILED' after 5 retries to prevent infinite retry loops.
    """
    reconciled = []
    with get_db_connection() as conn:
        cursor = conn.cursor()

        if is_boot:
            cursor.execute("""
                SELECT video_id, status, retry_count, ai_attempt_count, local_video_path, updated_at
                FROM youtube_inbox
                WHERE status IN ('TRANSCRIBING', 'ANALYZING', 'DOWNLOADING', 'VALIDATING', 'RENDERING')
            """)
        else:
            cursor.execute(f"""
                SELECT video_id, status, retry_count, ai_attempt_count, local_video_path, updated_at
                FROM youtube_inbox
                WHERE status IN ('TRANSCRIBING', 'ANALYZING', 'DOWNLOADING', 'VALIDATING', 'RENDERING')
                  AND datetime(updated_at) < datetime('now', '-{int(stale_timeout_seconds)} seconds')
            """)

        stuck_items = cursor.fetchall()
        for item in stuck_items:
            video_id = item["video_id"]
            old_status = item["status"]
            current_retries = item["retry_count"] or 0
            current_ai_attempts = item["ai_attempt_count"] or 0
            local_video_path = item["local_video_path"]

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

            elif old_status == "VALIDATING":
                new_retries = current_retries + 1
                if new_retries > 5:
                    new_status = "FAILED"
                    err = f"Exceeded retry limit (5) during video validation (failed at {new_retries} attempts)"
                else:
                    has_valid_file = False
                    if local_video_path:
                        try:
                            p = Path(local_video_path)
                            if p.exists() and p.stat().st_size > 1024:
                                has_valid_file = True
                        except Exception:
                            has_valid_file = False

                    if has_valid_file:
                        new_status = "DOWNLOADING"
                        err = "Validation interrupted by crash/restart; resetting to DOWNLOADING for re-validation"
                    else:
                        new_status = "DISCOVERED"
                        err = "Validation interrupted with missing or incomplete file; resetting to DISCOVERED for re-download"
                cursor.execute("""
                    UPDATE youtube_inbox
                    SET status = ?, retry_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE video_id = ?
                """, (new_status, new_retries, err, video_id))

            elif old_status == "RENDERING":
                new_retries = max(current_retries, current_ai_attempts) + 1
                if new_retries > 5:
                    new_status = "FAILED"
                    err = f"Exceeded retry limit (5) during clip rendering (failed at {new_retries} attempts)"
                else:
                    new_status = "CLIPS_DEFINED"
                    err = "Clip rendering interrupted by crash/restart; resetting to CLIPS_DEFINED for retry"
                cursor.execute("""
                    UPDATE youtube_inbox
                    SET status = ?, retry_count = ?, ai_attempt_count = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE video_id = ?
                """, (new_status, new_retries, new_retries, err, video_id))
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
    reclaimed_chunk_ids: Set[str] = set()

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
            if job["chunk_id"]:
                reclaimed_chunk_ids.add(job["chunk_id"])

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

    reclaimed_keys: Set[str] = {sanitize_job_workspace_id(jid) for jid in reclaimed_jobs}
    reclaimed_keys.update(sanitize_job_workspace_id(cid) for cid in reclaimed_chunk_ids)
    for rec_item in reclaimed_inbox:
        vid = rec_item.get("video_id")
        if vid:
            reclaimed_keys.add(sanitize_job_workspace_id(vid))
            reclaimed_keys.add(sanitize_job_workspace_id(f"yt_{vid}"))

    with get_db_connection() as conn:
        active_keys = _collect_active_workspace_keys(
            conn=conn,
            now_str=now_str,
            is_boot=is_boot,
            stale_timeout_seconds=stale_timeout_seconds,
        )

    # 3. Clean orphaned temporary artifacts safely without touching live jobs or active renderers
    cleaned_temp_files = 0
    if PROCESSING_DIR.exists():
        if is_boot:
            # Boot-time cleanup retains sweeping orphaned legacy root temp files & temp dirs
            # while leaving finalized source media chunks (.mp4/.mov/.mkv/.webm) untouched.
            for entry in PROCESSING_DIR.iterdir():
                if entry.name == "jobs":
                    continue
                if entry.is_file() and entry.suffix in (".tmp", ".part", ".ass"):
                    try:
                        entry.unlink(missing_ok=True)
                        cleaned_temp_files += 1
                    except Exception as e:
                        logger.warning("Could not clean legacy root temp file %s: %s", entry.name, e)
                elif entry.is_dir() and entry.name.startswith(("dispatch_stage2_", "dispatch_transcribe_", "dispatch_stage1_")):
                    try:
                        file_count = _count_files_in_tree(entry)
                        shutil.rmtree(entry, ignore_errors=False)
                        cleaned_temp_files += max(file_count, 1)
                    except FileNotFoundError:
                        pass
                    except Exception as e:
                        logger.warning("Could not clean legacy temp dir %s: %s", entry.name, e)

        jobs_dir = PROCESSING_DIR / "jobs"
        if jobs_dir.exists() and jobs_dir.is_dir():
            stale_cutoff_epoch = time.time() - max(0, int(stale_timeout_seconds))
            for entry in list(jobs_dir.iterdir()):
                if entry.is_file() and entry.suffix in (".tmp", ".part", ".ass"):
                    try:
                        if is_boot or entry.stat().st_mtime < stale_cutoff_epoch:
                            entry.unlink(missing_ok=True)
                            cleaned_temp_files += 1
                    except FileNotFoundError:
                        pass
                    except Exception as e:
                        logger.warning("Could not clean temp file %s in jobs/: %s", entry.name, e)
                    continue

                if not entry.is_dir():
                    continue

                key = entry.name
                if key in active_keys:
                    logger.debug("Preserving active job workspace %s (valid lease/heartbeat)", key)
                    continue

                if not is_boot:
                    is_reclaimed = key in reclaimed_keys
                    if not is_reclaimed and _newest_tree_mtime(entry) >= stale_cutoff_epoch:
                        logger.debug("Preserving fresh workspace %s during non-boot sweep", key)
                        continue

                try:
                    file_count = _count_files_in_tree(entry)
                    shutil.rmtree(entry, ignore_errors=False)
                    cleaned_temp_files += max(file_count, 1)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logger.warning("Could not clean orphaned job workspace %s: %s", key, e)

    if reclaimed_jobs or reclaimed_inbox or cleaned_temp_files:
        logger.info("Laptop orchestrator recovery complete: %d jobs reclaimed, %d inbox items reconciled, %d temp artifacts cleaned",
                    len(reclaimed_jobs), len(reclaimed_inbox), cleaned_temp_files)

    return {
        "reclaimed_jobs": reclaimed_jobs,
        "reclaimed_inbox_items": reclaimed_inbox,
        "cleaned_temp_files": cleaned_temp_files
    }
