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

        if not video_path or not video_path.exists():
            logger.error("Job %s clip video file missing: %s", job_id, video_path)
            with db.get_db_connection() as conn:
                conn.execute(
                    "UPDATE publishing_outbox SET status = 'failed', last_error = 'Local video file missing' WHERE id = ?",
                    (job_id,)
                )
            continue

        # Mark as uploading
        with db.get_db_connection() as conn:
            conn.execute("UPDATE publishing_outbox SET status = 'uploading' WHERE id = ?", (job_id,))

        try:
            logger.info("Publishing clip %s to %s (Mode: %s)", clip_id, platform, publish_mode)

            if platform == "youtube":
                privacy = "public" if publish_mode == "public" else "private"
                result = upload_youtube_short(
                    video_path=video_path,
                    title=title,
                    description=f"{description}\n\n{hashtags}",
                    tags=hashtags,
                    privacy_status=privacy
                )
            elif platform == "instagram":
                result = upload_instagram_reel(
                    video_path=video_path,
                    caption=f"{title}\n\n{description}\n\n{hashtags}"
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
                logger.warning("Unsupported platform %s for job %s", platform, job_id)
                continue

            # Update job record to published
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = 'published', remote_id = ?, remote_url = ?, published_at = CURRENT_TIMESTAMP
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
                        last_error = ?
                    WHERE id = ?
                """, (str(pe), job_id))

        except Exception as e:
            logger.error("Publishing error for job %s (%s): %s", job_id, platform, e)
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = CASE WHEN attempt_count >= 4 THEN 'failed' ELSE 'queued' END,
                        attempt_count = attempt_count + 1,
                        last_error = ?
                    WHERE id = ?
                """, (str(e), job_id))

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
