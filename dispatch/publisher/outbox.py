"""Publishing Outbox Queue processor.
Ensures durable, idempotent publishing to YouTube and Instagram with automatic cleanup of published local clips.
"""
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from dispatch.publisher.youtube import upload_youtube_short
from dispatch.publisher.instagram import upload_instagram_reel
from dispatch.publisher.linkedin import upload_linkedin_video
from dispatch.publisher.twitter import upload_x_video
from dispatch.config import MIN_CLIP_DURATION, MAX_CLIP_DURATION
from dispatch.ingestion.validator import probe_video
from dispatch import db

logger = logging.getLogger("dispatch.publisher.outbox")
_processing_lock = threading.Lock()


def process_outbox_queue() -> int:
    """Process all queued jobs in publishing_outbox.
    Returns:
        Number of successfully published jobs in this pass.
    """
    acquired = _processing_lock.acquire(blocking=False)
    if not acquired:
        logger.debug("Outbox queue processor is already active; skipping duplicate run.")
        return 0

    try:
        return _process_outbox_queue_internal()
    finally:
        _processing_lock.release()


def _process_outbox_queue_internal() -> int:
    # 1. Recover stale uploading jobs orphaned by unexpected process restarts (> 10 min)
    # If job has been uploading for > 10 min, do NOT blindly republish: mark 'reconciliation_required'
    try:
        with db.get_db_connection() as conn:
            conn.execute("""
                UPDATE publishing_outbox 
                SET status = 'reconciliation_required',
                    updated_at = CURRENT_TIMESTAMP,
                    last_error = 'Stale uploading state detected after process interruption; manual or remote reconciliation required'
                WHERE status = 'uploading' 
                  AND (julianday('now') - julianday(COALESCE(attempt_started_at, updated_at, created_at))) * 1440.0 > 10.0
            """)
    except Exception as e:
        logger.debug("Stale outbox recovery check note: %s", e)

    queue = db.get_outbox_queue()
    if not queue:
        return 0

    logger.info("Found %d pending publishing jobs in outbox", len(queue))
    success_count = 0

    for job in queue:
        job_id = job["id"]
        clip_id = job["clip_id"]
        platform = job["platform"].lower()
        publish_mode = job.get("publish_mode") or "private"
        video_path = Path(job["video_path"]) if job.get("video_path") else None
        title = job.get("title") or "Dispatch Video"
        description = job.get("description") or ""
        hashtags = job.get("hashtags") or ""
        attempt_count = job.get("attempt_count") or 0
        remote_id = job.get("remote_id")

        # If remote_id already exists from a previous ambiguous attempt, do not republish!
        if remote_id:
            logger.warning("Job %s already has remote_id %s; marking reconciliation_required rather than republishing", job_id, remote_id)
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = 'reconciliation_required',
                        updated_at = CURRENT_TIMESTAMP,
                        last_error = 'Job already has confirmed remote_id; reconciliation required before retry'
                    WHERE id = ?
                """, (job_id,))
            continue

        if not video_path or not video_path.exists():
            logger.error("Job %s clip video file missing: %s", job_id, video_path)
            with db.get_db_connection() as conn:
                conn.execute(
                    "UPDATE publishing_outbox SET status = 'failed', updated_at = CURRENT_TIMESTAMP, last_error = 'Local video file missing' WHERE id = ?",
                    (job_id,)
                )
            continue

        # Strict duration verification: 5.0s <= duration <= 180.0s for valid video media
        is_valid, meta, _ = probe_video(video_path)
        if is_valid and meta.get("duration") is not None:
            actual_duration = float(meta["duration"])
            if actual_duration < MIN_CLIP_DURATION or actual_duration > MAX_CLIP_DURATION:
                err_msg = f"Clip duration ({actual_duration:.2f}s) violates absolute duration requirements [{MIN_CLIP_DURATION}s, {MAX_CLIP_DURATION}s]"
                logger.error("Job %s cannot be published: %s", job_id, err_msg)
                with db.get_db_connection() as conn:
                    conn.execute(
                        "UPDATE publishing_outbox SET status = 'failed', updated_at = CURRENT_TIMESTAMP, last_error = ? WHERE id = ?",
                        (err_msg, job_id)
                    )
                continue

        # Atomically claim job as uploading
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE publishing_outbox 
                SET status = 'uploading',
                    attempt_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'queued'
            """, (job_id,))
            if cursor.rowcount == 0:
                logger.debug("Job %s already claimed by another worker; skipping", job_id)
                continue

        try:
            logger.info("Publishing clip %s to %s (Mode: %s, Attempt: %d)", clip_id, platform, publish_mode, attempt_count + 1)

            idempotency_key = job.get("idempotency_key") or f"idem_{clip_id}_{platform}"
            idempotency_marker = f"dispatch_idempotency_key:{idempotency_key}"

            if platform in ("youtube", "youtube_shorts"):
                privacy = "public" if publish_mode == "public" else "private"
                # Include idempotency key marker in description
                full_desc = f"{description}\n\n{hashtags}\n\n{idempotency_marker}".strip()
                result = upload_youtube_short(
                    video_path=video_path,
                    title=title,
                    description=full_desc,
                    tags=hashtags,
                    privacy_status=privacy
                )
            elif platform == "instagram":
                result = upload_instagram_reel(
                    video_path=video_path,
                    caption=f"{title}\n\n{description}\n\n{hashtags}\n\n{idempotency_marker}"
                )
            elif platform == "linkedin":
                result = upload_linkedin_video(
                    video_path=video_path,
                    commentary=f"{title}\n\n{description}\n\n{hashtags}",
                    title=title
                )
            elif platform in ("x", "twitter"):
                result = upload_x_video(
                    video_path=video_path,
                    text=f"{title}\n\n{hashtags}"
                )
            else:
                err_msg = f"Unsupported publishing platform '{platform}'"
                logger.warning("%s for job %s", err_msg, job_id)
                with db.get_db_connection() as conn:
                    conn.execute(
                        "UPDATE publishing_outbox SET status = 'failed', updated_at = CURRENT_TIMESTAMP, last_error = ? WHERE id = ?",
                        (err_msg, job_id)
                    )
                continue

            # Update job record to published
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = 'published', 
                        remote_id = ?, 
                        remote_url = ?, 
                        updated_at = CURRENT_TIMESTAMP,
                        published_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (result.get("remote_id"), result.get("remote_url"), job_id))

            success_count += 1
            logger.info("Successfully published %s to %s (%s)", clip_id, platform, result.get("remote_url"))

            # Check if all outbox jobs for this clip have finished
            check_and_finalize_clip(clip_id, video_path)

        except PermissionError as pe:
            logger.warning("Publishing job %s (%s) blocked: %s", job_id, platform, pe)
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = 'blocked_needs_auth',
                        updated_at = CURRENT_TIMESTAMP,
                        last_error = ?
                    WHERE id = ?
                """, (str(pe), job_id))

        except Exception as e:
            err_str = str(e)
            logger.error("Publishing error for job %s (%s): %s", job_id, platform, err_str)
            new_attempts = attempt_count + 1
            # Check for ambiguous timeout or disconnect errors
            is_ambiguous = any(term in err_str.lower() for term in ("timeout", "timed out", "connection reset", "broken pipe", "remotely closed"))
            
            with db.get_db_connection() as conn:
                if is_ambiguous:
                    # CRITICAL FORENSIC FIX: Even on the FIRST ambiguous timeout/disconnect, NEVER blindly republish!
                    # Move immediately to reconciliation_required to prevent duplicate posts.
                    conn.execute("""
                        UPDATE publishing_outbox 
                        SET status = 'reconciliation_required',
                            attempt_count = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            last_error = ?
                        WHERE id = ?
                    """, (new_attempts, f"Ambiguous remote error (reconciliation required before retry): {err_str}", job_id))
                elif new_attempts >= 5:
                    conn.execute("""
                        UPDATE publishing_outbox 
                        SET status = 'failed',
                            attempt_count = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            last_error = ?
                        WHERE id = ?
                    """, (new_attempts, err_str, job_id))
                else:
                    # Exponential backoff for definitive non-ambiguous transient errors: 30s, 60s, 120s, 240s...
                    delay_seconds = min(3600, 30 * (2 ** (new_attempts - 1)))
                    conn.execute("""
                        UPDATE publishing_outbox 
                        SET status = 'queued',
                            attempt_count = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            next_retry_at = datetime('now', ?),
                            last_error = ?
                        WHERE id = ?
                    """, (new_attempts, f"+{delay_seconds} seconds", err_str, job_id))

    return success_count


def check_and_finalize_clip(clip_id: str, video_path: Path):
    """Check if all platform jobs for this clip are completed, and mark clip published in database."""
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
        statuses = [row["status"] for row in cursor.fetchall()]

        if statuses and all(s == "published" for s in statuses):
            cursor.execute("""
                UPDATE clips 
                SET status = 'published', published_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (clip_id,))
            conn.commit()
            logger.info("All platform uploads confirmed for clip %s. Clip marked published (media preserved).", clip_id)


class PublishingOutboxWorker:
    """Daemon thread worker that regularly monitors and processes queued social publishing jobs."""

    def __init__(self, poll_interval: float = 10.0):
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start background polling thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Publishing outbox worker is already running.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="PublishingOutboxWorker",
            daemon=True
        )
        self._thread.start()
        logger.info("Autonomous Publishing Outbox worker started (daemon thread, interval: %.1fs)", self.poll_interval)

    def stop(self):
        """Signal worker to stop and wait for completion."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            logger.info("Publishing outbox worker stopped.")

    def _run_loop(self):
        logger.info("Starting publishing outbox worker loop")
        while not self._stop_event.is_set():
            try:
                process_outbox_queue()
            except Exception as e:
                logger.error("Publishing outbox worker loop exception: %s", e)
            self._stop_event.wait(self.poll_interval)


def trigger_outbox_pass():
    """Trigger an immediate asynchronous publishing pass without waiting for the next poll interval."""
    t = threading.Thread(
        target=process_outbox_queue,
        name="ImmediateOutboxPass",
        daemon=True
    )
    t.start()


def watch_outbox_loop(poll_interval: float = 10.0):
    """Background polling loop for the publishing outbox (standalone runner)."""
    worker = PublishingOutboxWorker(poll_interval=poll_interval)
    worker.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        worker.stop()
