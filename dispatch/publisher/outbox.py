"""Publishing Outbox Queue processor.
Ensures durable, idempotent publishing to YouTube and Instagram with automatic cleanup of published local clips.
"""
import time
import logging
import threading
import socket
import http.client
from pathlib import Path
from typing import Dict, Any, List, Optional
try:
    import requests
except ImportError:
    requests = None
try:
    import httpx
except ImportError:
    httpx = None

from dispatch.publisher.youtube import upload_youtube_short
from dispatch.publisher.instagram import upload_instagram_reel
from dispatch.publisher.linkedin import upload_linkedin_video
from dispatch.publisher.twitter import upload_x_video
from dispatch.publisher.facebook import upload_facebook_reel
from dispatch.config import MIN_CLIP_DURATION, MAX_CLIP_DURATION
from dispatch.ingestion.validator import probe_video
from dispatch import db

logger = logging.getLogger("dispatch.publisher.outbox")
_processing_lock = threading.Lock()


def is_ambiguous_network_error(exc: Exception) -> bool:
    """Classify whether an exception represents an ambiguous outcome where bytes may have
    reached the remote platform, but the response was lost, dropped, or timed out.
    Enforces that ambiguous errors are NEVER blindly retried.
    """
    if exc is None:
        return False

    # 1. Native Python network & socket timeout/disconnect types
    ambiguous_types = [
        TimeoutError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        http.client.RemoteDisconnected,
        http.client.IncompleteRead,
    ]
    if hasattr(socket, "timeout"):
        ambiguous_types.append(socket.timeout)

    # 2. Third-party HTTP client libraries
    if requests is not None:
        ambiguous_types.extend([
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ])
    if httpx is not None:
        ambiguous_types.extend([
            httpx.TimeoutException,
            httpx.NetworkError,
        ])

    for t in ambiguous_types:
        if isinstance(exc, t):
            return True

    # 3. OSError / Windows Socket error codes (e.g. WSAECONNABORTED 10053, WSAECONNRESET 10054, WSAETIMEDOUT 10060)
    if isinstance(exc, OSError):
        ambiguous_errnos = {
            10053,  # WSAECONNABORTED
            10054,  # WSAECONNRESET
            10060,  # WSAETIMEDOUT
            54,     # ECONNRESET (BSD/macOS)
            104,    # ECONNRESET (Linux)
            32,     # EPIPE
            110,    # ETIMEDOUT (Linux)
        }
        if getattr(exc, "winerror", None) in ambiguous_errnos or getattr(exc, "errno", None) in ambiguous_errnos:
            return True

    # 4. Check nested __cause__ or __context__
    if getattr(exc, "__cause__", None) and is_ambiguous_network_error(exc.__cause__):
        return True
    if getattr(exc, "__context__", None) and is_ambiguous_network_error(exc.__context__):
        return True

    # 5. String signature fallback
    err_str = str(exc).lower()
    ambiguous_patterns = (
        "timeout", "timed out", "connection reset", "connection aborted",
        "broken pipe", "remotely closed", "remote disconnected",
        "winerror 10053", "winerror 10054", "forcibly closed"
    )
    return any(p in err_str for p in ambiguous_patterns)


def reconcile_youtube_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Query YouTube Data API search for dispatch_idempotency_key to determine if video was uploaded."""
    service = None
    account_id = job.get("account_id")
    user_id = job.get("user_id")
    if account_id:
        try:
            from dispatch.youtube_connections import get_account_credentials
            creds = get_account_credentials(account_id, user_id=user_id)
            if creds:
                from dispatch.publisher.youtube import build_youtube_service_from_credentials
                service = build_youtube_service_from_credentials(creds)
        except Exception as e:
            logger.debug("Credentials lookup for reconcile job %s: %s", account_id, e)
    if not service and not user_id:
        from dispatch.youtube_inbox.oauth import get_youtube_service
        service = get_youtube_service()
    if not service:
        return {"reconciled": False, "reason": "YouTube service unavailable or unconfigured credentials"}

    idempotency_key = job.get("idempotency_key") or f"idem_{job['clip_id']}_{job['platform']}"
    marker = f"dispatch_idempotency_key:{idempotency_key}"
    try:
        req = service.search().list(part="snippet", forMine=True, q=marker, type="video", maxResults=5)
        resp = req.execute()
        items = resp.get("items", [])
        if items:
            vid = items[0]["id"]["videoId"]
            vurl = f"https://www.youtube.com/watch?v={vid}"
            return {"reconciled": True, "published": True, "remote_id": vid, "remote_url": vurl}
        return {"reconciled": True, "published": False}
    except Exception as e:
        return {"reconciled": False, "reason": str(e)}


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

    # 2. Check remote status of in-flight processing jobs (e.g. YouTube processing)
    try:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM publishing_outbox WHERE status = 'processing'")
            processing_jobs = [dict(r) for r in cursor.fetchall()]
        for pjob in processing_jobs:
            if pjob.get("platform") in ("youtube", "youtube_shorts") and pjob.get("remote_id"):
                svc = None
                p_creds = {}
                account_id = pjob.get("account_id")
                user_id = pjob.get("user_id")
                if account_id:
                    try:
                        from dispatch.youtube_connections import get_account_credentials
                        p_creds = get_account_credentials(account_id, user_id=user_id)
                    except Exception as ce:
                        logger.debug("Account credentials lookup for processing job %s: %s", account_id, ce)
                if p_creds:
                    from dispatch.publisher.youtube import build_youtube_service_from_credentials
                    svc = build_youtube_service_from_credentials(p_creds)
                elif not user_id:
                    from dispatch.youtube_inbox.oauth import get_youtube_service
                    svc = get_youtube_service()

                if svc:
                    try:
                        s_resp = svc.videos().list(part="status", id=pjob["remote_id"]).execute()
                        items = s_resp.get("items", [])
                        if items:
                            st = items[0].get("status", {}).get("uploadStatus")
                            if st == "processed":
                                with db.get_db_connection() as conn:
                                    conn.execute("""
                                        UPDATE publishing_outbox 
                                        SET status = 'published', updated_at = CURRENT_TIMESTAMP, published_at = CURRENT_TIMESTAMP
                                        WHERE id = ?
                                    """, (pjob["id"],))
                                check_and_finalize_clip(pjob["clip_id"], Path(pjob.get("video_path") or f"storage/clips/{pjob['clip_id']}.mp4"))
                            elif st in ("failed", "rejected"):
                                with db.get_db_connection() as conn:
                                    conn.execute("""
                                        UPDATE publishing_outbox 
                                        SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                                        WHERE id = ?
                                    """, (f"YouTube processing {st}: {items[0].get('status', {}).get('rejectionReason', 'unknown')}", pjob["id"]))
                    except Exception as q_err:
                        logger.warning("Failed checking processing status for job %s: %s", pjob["id"], q_err)
                        with db.get_db_connection() as conn:
                            conn.execute("""
                                UPDATE publishing_outbox SET last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                            """, (f"Status query error: {q_err}", pjob["id"]))
                else:
                    logger.warning("No credentials available to check status of processing job %s", pjob["id"])
    except Exception as e:
        logger.debug("Processing status check note: %s", e)

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

        # Reject corrupt or unsupported media before any publisher can perform a real upload.
        is_valid, meta, validation_error = probe_video(video_path)
        meta = meta or {}
        if not is_valid or meta.get("duration") is None:
            err_msg = f"Video validation failed: {validation_error or 'unsupported or corrupt media'}"
        else:
            actual_duration = float(meta["duration"])
            err_msg = None
            if actual_duration < MIN_CLIP_DURATION or actual_duration > MAX_CLIP_DURATION:
                err_msg = f"Clip duration ({actual_duration:.2f}s) violates absolute duration requirements [{MIN_CLIP_DURATION}s, {MAX_CLIP_DURATION}s]"

        if err_msg:
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

        # Ground-truth re-check AFTER claiming: the queue JOIN may have been minutes
        # old, and the user could have rejected the clip or moved its status since.
        # Publishing a rejected clip from a stale queue row must fail closed.
        claimed_clip = db.get_clip_by_id(clip_id)
        if not claimed_clip or claimed_clip.get("status") != "approved":
            with db.get_db_connection() as conn:
                conn.execute(
                    "UPDATE publishing_outbox SET status = 'failed', updated_at = CURRENT_TIMESTAMP, last_error = ? WHERE id = ?",
                    (f"Clip no longer approved (status: {claimed_clip.get('status') if claimed_clip else 'missing'})", job_id)
                )
            logger.warning("Job %s abandoned: clip %s is not approved", job_id, clip_id)
            continue

        try:
            logger.info("Publishing clip %s to %s (Mode: %s, Attempt: %d)", clip_id, platform, publish_mode, attempt_count + 1)

            idempotency_key = job.get("idempotency_key") or f"idem_{clip_id}_{platform}"
            idempotency_marker = f"dispatch_idempotency_key:{idempotency_key}"

            # Retrieve bound account credentials if account_id is present
            account_id = job.get("account_id")
            user_id = job.get("user_id")
            account_creds = {}
            if account_id:
                try:
                    from dispatch.youtube_connections import get_account_credentials
                    account_creds = get_account_credentials(account_id, user_id=user_id)
                except Exception as ce:
                    logger.debug("Account credentials lookup note for %s: %s", account_id, ce)

            if user_id and (not account_id or not account_creds):
                err_msg = f"User-owned job {job_id} requires a connected and verified {platform} account. No valid credentials found."
                logger.warning("%s (status -> blocked_needs_auth)", err_msg)
                with db.get_db_connection() as conn:
                    conn.execute("""
                        UPDATE publishing_outbox 
                        SET status = 'blocked_needs_auth',
                            updated_at = CURRENT_TIMESTAMP,
                            last_error = ?
                        WHERE id = ?
                    """, (err_msg, job_id))
                continue

            if platform in ("youtube", "youtube_shorts"):
                privacy = "public" if publish_mode == "public" else ("unlisted" if publish_mode == "unlisted" else "private")
                # Include idempotency key marker in description
                full_desc = f"{description}\n\n{hashtags}\n\n{idempotency_marker}".strip()
                result = upload_youtube_short(
                    video_path=video_path,
                    title=title,
                    description=full_desc,
                    tags=hashtags,
                    privacy_status=privacy,
                    credentials_data=account_creds
                )
            elif platform == "instagram":
                ig_token = account_creds.get("access_token") if account_creds else None
                ig_uid = account_id or (account_creds.get("account_id") if account_creds else None)
                result = upload_instagram_reel(
                    video_path=video_path,
                    caption=f"{title}\n\n{description}\n\n{hashtags}\n\n{idempotency_marker}",
                    access_token=ig_token,
                    ig_user_id=ig_uid
                )
            elif platform == "linkedin":
                li_token = account_creds.get("access_token") if account_creds else None
                li_author = account_id or (account_creds.get("author_urn") if account_creds else None)
                result = upload_linkedin_video(
                    video_path=video_path,
                    commentary=f"{title}\n\n{description}\n\n{hashtags}",
                    title=title,
                    access_token=li_token,
                    author_urn=li_author
                )
            elif platform in ("x", "twitter"):
                x_bearer = account_creds.get("bearer_token") if account_creds else None
                x_key = account_creds.get("api_key") if account_creds else None
                x_secret = account_creds.get("api_secret") if account_creds else None
                x_tok = account_creds.get("oauth_token") if account_creds else None
                x_tok_sec = account_creds.get("oauth_secret") if account_creds else None
                result = upload_x_video(
                    video_path=video_path,
                    text=f"{title}\n\n{hashtags}",
                    bearer_token=x_bearer,
                    api_key=x_key,
                    api_secret=x_secret,
                    oauth_token=x_tok,
                    oauth_secret=x_tok_sec
                )
            elif platform == "facebook":
                fb_token = account_creds.get("access_token") if account_creds else None
                fb_page = account_id or (account_creds.get("page_id") if account_creds else None)
                result = upload_facebook_reel(
                    video_path=video_path,
                    description=f"{title}\n\n{description}\n\n{hashtags}\n\n{idempotency_marker}",
                    access_token=fb_token,
                    page_id=fb_page
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

            # Update job record with actual provider status
            final_status = result.get("status", "published")
            with db.get_db_connection() as conn:
                conn.execute("""
                    UPDATE publishing_outbox 
                    SET status = ?, 
                        remote_id = ?, 
                        remote_url = ?, 
                        updated_at = CURRENT_TIMESTAMP,
                        published_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (final_status, result.get("remote_id"), result.get("remote_url"), job_id))

            if final_status == "published":
                success_count += 1
                logger.info("Successfully published %s to %s (%s)", clip_id, platform, result.get("remote_url"))
                # Check if all outbox jobs for this clip have finished
                check_and_finalize_clip(clip_id, video_path)
            else:
                logger.info("Uploaded %s to %s with in-flight remote status '%s' (%s)", clip_id, platform, final_status, result.get("remote_url"))

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
            is_ambiguous = is_ambiguous_network_error(e)
            
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
                WHERE id = ? AND status = 'approved'
            """, (clip_id,))
            if cursor.rowcount == 1:
                conn.commit()
                logger.info("All platform uploads confirmed for clip %s. Clip marked published (media preserved).", clip_id)
            else:
                conn.commit()
                logger.info(
                    "All platform uploads confirmed for clip %s, but clip is no longer approved "
                    "(rejected or finalized already); status left untouched.", clip_id
                )


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
