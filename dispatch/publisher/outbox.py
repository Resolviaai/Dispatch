"""Publishing Outbox Queue processor.
Ensures durable, idempotent publishing to YouTube, Instagram, LinkedIn, X, and Facebook.
"""
import hashlib
import json
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


def _normalize_platform(platform: str) -> str:
    norm = str(platform or "").strip().lower()
    if norm in ("youtube", "youtube_shorts"):
        return "youtube"
    if norm in ("x", "twitter"):
        return "twitter"
    return norm


def build_publish_idempotency_key(
    clip_id: str,
    platform: str,
    account_id: Optional[str] = None,
    render_fingerprint: Optional[str] = None,
    revision: Optional[int] = None,
) -> str:
    """Derive a deterministic idempotency key from immutable IDs only (never title/description)."""
    norm_plat = _normalize_platform(platform)
    acct_part = str(account_id or "").strip()
    fp_or_rev = str(render_fingerprint or "").strip() or f"rev:{int(revision or 0)}"
    raw = f"v1|{clip_id}|{norm_plat}|{acct_part}|{fp_or_rev}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"idem_{digest[:24]}"


def _resolve_job_account_id(job: Dict[str, Any], platform: str) -> Optional[str]:
    """Resolve platform-specific account_id cleanly from job metadata."""
    raw = job.get("account_id")
    if isinstance(raw, dict):
        norm = _normalize_platform(platform)
        for cand_key in (platform, norm, "youtube_shorts" if norm == "youtube" else "", "x" if norm == "twitter" else ""):
            if cand_key and raw.get(cand_key):
                val = str(raw[cand_key]).strip()
                if val:
                    return val
        return None
    if raw is None:
        return None
    raw_str = str(raw).strip()
    if not raw_str:
        return None
    if raw_str.startswith("{"):
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict):
                norm = _normalize_platform(platform)
                for cand_key in (platform, norm, "youtube_shorts" if norm == "youtube" else "", "x" if norm == "twitter" else ""):
                    if cand_key and parsed.get(cand_key):
                        val = str(parsed[cand_key]).strip()
                        if val:
                            return val
                return None
        except Exception:
            pass
    return raw_str


def _parse_json_dict(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _build_outbox_render_params(claimed_clip: Dict[str, Any]) -> Dict[str, Any]:
    """Build complete render arguments from clip state for stale pre-publish re-renders."""
    clip_id = str(claimed_clip.get("id") or "")
    project_doc = _parse_json_dict(claimed_clip.get("project_document"))
    caption_track = _parse_json_dict(claimed_clip.get("caption_data"))

    segments = None
    if project_doc:
        doc_segs = (project_doc.get("timeline") or {}).get("segments")
        if isinstance(doc_segs, list) and doc_segs:
            segments = doc_segs
    if not segments and caption_track:
        groups = caption_track.get("groups") or []
        if groups:
            segments = [
                {
                    "id": g.get("id", index),
                    "start": g.get("start", 0),
                    "end": g.get("end", 0),
                    "text": g.get("text", ""),
                    "words": g.get("words", []),
                }
                for index, g in enumerate(groups)
            ]
    if not segments and claimed_clip.get("chunk_id"):
        try:
            with db.get_db_connection() as conn:
                t_row = conn.execute(
                    "SELECT segments_json FROM transcripts WHERE chunk_id = ?",
                    (claimed_clip["chunk_id"],),
                ).fetchone()
                if t_row and t_row["segments_json"]:
                    parsed_segs = json.loads(t_row["segments_json"])
                    if isinstance(parsed_segs, list) and parsed_segs:
                        segments = parsed_segs
        except Exception:
            pass

    crop_anchor_x = claimed_clip.get("crop_anchor_x")
    if crop_anchor_x is None and project_doc:
        layout_doc = project_doc.get("layout") if isinstance(project_doc.get("layout"), dict) else {}
        if layout_doc.get("crop_anchor_x") is not None:
            crop_anchor_x = layout_doc.get("crop_anchor_x")
    if crop_anchor_x is not None:
        crop_anchor_x = float(crop_anchor_x)

    user_tpl = None
    style_preset = None
    try:
        user_tpl = db.get_default_template(user_id=claimed_clip.get("user_id"))
        if isinstance(user_tpl, dict):
            tpl_captions = user_tpl.get("captions") if isinstance(user_tpl.get("captions"), dict) else {}
            if tpl_captions.get("preset_id") and tpl_captions.get("preset_id") != "custom":
                style_preset = tpl_captions.get("preset_id")
    except Exception:
        user_tpl = None

    render_fingerprint = claimed_clip.get("render_fingerprint")
    subtitle_options = {
        "enabled": bool(caption_track or segments),
        "caption_track": caption_track,
        "preserve_timing": bool(caption_track or project_doc),
        "crop_anchor_x": crop_anchor_x,
        "style_preset": style_preset,
        "template": user_tpl,
        "project_document": project_doc,
        "render_fingerprint": render_fingerprint,
    }

    return {
        "aspect_ratio": claimed_clip.get("source_aspect_ratio") or None,
        "layout_mode": claimed_clip.get("layout_mode") or "fit_blur",
        "segments": segments,
        "subtitle_options": subtitle_options,
        "expected_range_revision": int(claimed_clip.get("range_revision") or 0),
        "crop_anchor_x": crop_anchor_x,
        "project_document": project_doc,
        "render_fingerprint": render_fingerprint,
    }


def reconcile_youtube_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Query YouTube Data API search for dispatch_idempotency_key to determine if video was uploaded."""
    import os

    service = None
    account_id = _resolve_job_account_id(job, "youtube")
    user_id = job.get("user_id")
    idempotency_key = job.get("idempotency_key") or build_publish_idempotency_key(
        clip_id=str(job.get("clip_id") or ""),
        platform=str(job.get("platform") or "youtube"),
        account_id=account_id,
        render_fingerprint=job.get("render_fingerprint"),
        revision=job.get("range_revision"),
    )
    if user_id and (account_id or os.getenv("DISPATCH_WORKER_URL") or os.getenv("DISPATCH_CLOUD_WORKER_URL")):
        try:
            from dispatch.youtube_connections import reconcile_youtube_via_worker
            worker_res = reconcile_youtube_via_worker(account_id or "", idempotency_key, user_id=user_id)
            if worker_res.get("reconciled"):
                return worker_res
        except PermissionError:
            raise
        except Exception as w_err:
            logger.debug("Worker reconcile fallback for job %s: %s", account_id, w_err)
    if account_id or user_id:
        try:
            from dispatch.youtube_connections import get_account_credentials
            creds = get_account_credentials(account_id or "", user_id=user_id, provider="youtube")
            if creds and creds.get("needs_reauth"):
                raise PermissionError(str(creds.get("error") or "YouTube authorization revoked; reconnect required"))
            if creds:
                from dispatch.publisher.youtube import build_youtube_service_from_credentials
                service = build_youtube_service_from_credentials(creds)
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("Credentials lookup for reconcile job %s: %s", account_id, e)
    if not service and not user_id:
        from dispatch.youtube_inbox.oauth import get_youtube_service
        service = get_youtube_service()
    if not service:
        return {"reconciled": False, "reason": "YouTube service unavailable or unconfigured credentials"}

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


PROCESSING_DEADLINE_HOURS = 2.0


def _processing_deadline_exceeded(job: Dict[str, Any]) -> bool:
    """Start the processing clock once, then fail honestly after the deadline."""
    with db.get_db_connection() as conn:
        conn.execute(
            """UPDATE publishing_outbox
               SET processing_started_at = COALESCE(processing_started_at, CURRENT_TIMESTAMP)
               WHERE id = ? AND status = 'processing'""",
            (job["id"],),
        )
        row = conn.execute(
            """SELECT (julianday('now') - julianday(processing_started_at)) * 24.0 AS age_hours
               FROM publishing_outbox WHERE id = ?""",
            (job["id"],),
        ).fetchone()
        conn.commit()
    if row and row["age_hours"] is not None and float(row["age_hours"]) >= PROCESSING_DEADLINE_HOURS:
        with db.get_db_connection() as conn:
            conn.execute(
                """UPDATE publishing_outbox
                   SET status = 'reconciliation_required',
                       last_error = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'processing'""",
                (f"Provider processing exceeded the {PROCESSING_DEADLINE_HOURS:.0f}h deadline; manual reconciliation required", job["id"]),
            )
            conn.commit()
        logger.warning("Processing job %s exceeded the deadline; moved to reconciliation", job["id"])
        clip_id = job.get("clip_id")
        if not clip_id:
            with db.get_db_connection() as conn:
                r = conn.execute("SELECT clip_id FROM publishing_outbox WHERE id = ?", (job["id"],)).fetchone()
                if r:
                    clip_id = r["clip_id"]
                    
        if clip_id:
            check_and_finalize_clip(clip_id, Path(job.get("video_path") or f"storage/clips/{clip_id}.mp4"))
        return True
    return False


def _schedule_processing_check(job_id: str, attempts: int) -> None:
    """Bounded exponential backoff for provider status checks (30s..30min)."""
    next_attempts = int(attempts or 0) + 1
    delay = min(1800, 30 * (2 ** min(next_attempts - 1, 6)))
    with db.get_db_connection() as conn:
        conn.execute(
            """UPDATE publishing_outbox
               SET processing_attempts = ?,
                   next_retry_at = datetime('now', ?),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'processing'""",
            (next_attempts, f"+{delay} seconds", job_id),
        )
        conn.commit()


def _recover_stale_uploads() -> None:
    """Mark stale 'uploading' rows for reconciliation and finalize their clips.

    The bulk UPDATE cannot return the affected clip_ids, so they are read first;
    without this a clip with one published platform stays 'approved' after a
    daemon restart.
    """
    try:
        stale_clip_ids: list[str] = []
        with db.get_db_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT clip_id FROM publishing_outbox
                WHERE status = 'uploading'
                  AND (julianday('now') - julianday(COALESCE(attempt_started_at, updated_at, created_at))) * 1440.0 > 10.0
            """).fetchall()
            stale_clip_ids = [r["clip_id"] for r in rows if r["clip_id"]]
            conn.execute("""
                UPDATE publishing_outbox 
                SET status = 'reconciliation_required',
                    updated_at = CURRENT_TIMESTAMP,
                    last_error = 'Stale uploading state detected after process interruption; manual or remote reconciliation required'
                WHERE status = 'uploading' 
                  AND (julianday('now') - julianday(COALESCE(attempt_started_at, updated_at, created_at))) * 1440.0 > 10.0
            """)
            conn.commit()
        for stale_clip_id in stale_clip_ids:
            check_and_finalize_clip(stale_clip_id, Path(f"storage/clips/{stale_clip_id}.mp4"))
    except Exception as e:
        logger.debug("Stale outbox recovery check note: %s", e)


def _process_outbox_queue_internal() -> int:
    # 1. Recover stale uploading jobs orphaned by unexpected process restarts (> 10 min)
    # If job has been uploading for > 10 min, do NOT blindly republish: mark
    # 'reconciliation_required' and finalize its clip so a platform that already
    # published is not left stuck in 'approved'.
    _recover_stale_uploads()

    # 2. Check remote status of in-flight processing jobs (e.g. YouTube processing)
    try:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM publishing_outbox
                   WHERE status = 'processing'
                     AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)"""
            )
            processing_jobs = [dict(r) for r in cursor.fetchall()]
        for pjob in processing_jobs:
            if _processing_deadline_exceeded(pjob):
                continue
            if pjob.get("platform") in ("youtube", "youtube_shorts") and pjob.get("remote_id"):
                svc = None
                p_creds = {}
                account_id = _resolve_job_account_id(pjob, "youtube")
                user_id = pjob.get("user_id")
                if account_id or user_id:
                    try:
                        from dispatch.youtube_connections import get_account_credentials
                        p_creds = get_account_credentials(account_id or "", user_id=user_id, provider="youtube")
                    except Exception as ce:
                        logger.debug("Account credentials lookup for processing job %s: %s", account_id, ce)
                if p_creds and p_creds.get("needs_reauth"):
                    with db.get_db_connection() as conn:
                        conn.execute("""
                            UPDATE publishing_outbox
                            SET status = 'blocked_needs_auth', last_error = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (str(p_creds.get("error") or "YouTube authorization revoked"), pjob["id"]))
                        conn.commit()
                    check_and_finalize_clip(pjob["clip_id"], Path(pjob.get("video_path") or f"storage/clips/{pjob['clip_id']}.mp4"))
                    continue
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
                                    conn.commit()
                                check_and_finalize_clip(pjob["clip_id"], Path(pjob.get("video_path") or f"storage/clips/{pjob['clip_id']}.mp4"))
                            elif st in ("failed", "rejected"):
                                with db.get_db_connection() as conn:
                                    conn.execute("""
                                        UPDATE publishing_outbox 
                                        SET status = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
                                        WHERE id = ?
                                    """, (f"YouTube processing {st}: {items[0].get('status', {}).get('rejectionReason', 'unknown')}", pjob["id"]))
                                    conn.commit()
                                check_and_finalize_clip(pjob["clip_id"], Path(pjob.get("video_path") or f"storage/clips/{pjob['clip_id']}.mp4"))
                    except Exception as q_err:
                        logger.warning("Failed checking processing status for job %s: %s", pjob["id"], q_err)
                        with db.get_db_connection() as conn:
                            conn.execute("""
                                UPDATE publishing_outbox SET last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                            """, (f"Status query error: {q_err}", pjob["id"]))
                            conn.commit()
                else:
                    logger.warning("No credentials available to check status of processing job %s", pjob["id"])
            # Always schedule the next check with backoff. The UPDATE is a no-op
            # if this pass already moved the job to published/failed.
            _schedule_processing_check(pjob["id"], int(pjob.get("processing_attempts") or 0))
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
        norm_platform = _normalize_platform(platform)
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
                conn.commit()
            check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
            continue

        if not video_path or not video_path.exists():
            logger.error("Job %s clip video file missing: %s", job_id, video_path)
            with db.get_db_connection() as conn:
                conn.execute(
                    "UPDATE publishing_outbox SET status = 'failed', updated_at = CURRENT_TIMESTAMP, last_error = 'Local video file missing' WHERE id = ?",
                    (job_id,)
                )
                conn.commit()
            check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
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
                conn.commit()
            check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
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
            claimed_rows = cursor.rowcount
            conn.commit()
            if claimed_rows == 0:
                logger.debug("Job %s already claimed by another worker; skipping", job_id)
                continue

        job_settled = False
        upload_started = False
        try:
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
                    conn.commit()
                job_settled = True
                logger.warning("Job %s abandoned: clip %s is not approved", job_id, clip_id)
                check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
                continue

            logger.info("Publishing clip %s to %s (Mode: %s, Attempt: %d)", clip_id, platform, publish_mode, attempt_count + 1)

            # Retrieve bound account credentials per platform (resolving single connected account when omitted)
            account_id = _resolve_job_account_id(job, platform)
            user_id = job.get("user_id")
            account_creds: Dict[str, Any] = {}
            if account_id or user_id:
                try:
                    from dispatch.youtube_connections import get_account_credentials
                    account_creds = get_account_credentials(account_id or "", user_id=user_id, provider=norm_platform)
                except PermissionError:
                    raise
                except Exception as ce:
                    logger.debug("Account credentials lookup note for %s (%s): %s", norm_platform, account_id, ce)

            if account_creds and account_creds.get("needs_reauth"):
                raise PermissionError(
                    str(account_creds.get("error") or f"{platform} authorization expired or revoked; reconnect required.")
                )

            resolved_account_id = (
                str(account_creds.get("account_id") or "").strip() if account_creds else ""
            ) or account_id
            if resolved_account_id and resolved_account_id != job.get("account_id"):
                with db.get_db_connection() as conn:
                    conn.execute(
                        "UPDATE publishing_outbox SET account_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (resolved_account_id, job_id),
                    )
                    conn.commit()

            if user_id and (not resolved_account_id or not account_creds):
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
                    conn.commit()
                job_settled = True
                check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
                continue

            idempotency_key = job.get("idempotency_key") or build_publish_idempotency_key(
                clip_id=clip_id,
                platform=norm_platform,
                account_id=resolved_account_id,
                render_fingerprint=claimed_clip.get("render_fingerprint") or claimed_clip.get("rendered_fingerprint"),
                revision=claimed_clip.get("range_revision"),
            )
            idempotency_marker = f"dispatch_idempotency_key:{idempotency_key}"

            # Canonical Architecture: Ensure final delivery render (target layout, crop_anchor_x,
            # segments, project_document, render_fingerprint, template/captions) is fresh before publish.
            is_explicitly_stale = bool(claimed_clip.get("render_stale")) or bool(
                claimed_clip.get("render_fingerprint")
                and claimed_clip.get("render_fingerprint") != claimed_clip.get("rendered_fingerprint")
            ) or bool(
                claimed_clip.get("rendered_layout_mode")
                and claimed_clip.get("rendered_layout_mode") != claimed_clip.get("layout_mode")
            )
            needs_render = is_explicitly_stale or (claimed_clip.get("rendered_layout_mode") is None)
            if needs_render:
                try:
                    logger.info(
                        "Outbox: Finalizing delivery render for clip %s (layout: %s, stale: %s) before %s upload...",
                        clip_id, claimed_clip.get("layout_mode"), is_explicitly_stale, platform,
                    )
                    from dispatch.web.app import resolve_clip_source_path
                    source_p = resolve_clip_source_path(claimed_clip)
                    if source_p and source_p.exists():
                        from dispatch.video_engine.renderer import render_clip
                        rparams = _build_outbox_render_params(claimed_clip)
                        try:
                            render_res = render_clip(
                                source_video=source_p,
                                clip_id=clip_id,
                                start_time=float(claimed_clip.get("start_time", 0.0)),
                                end_time=float(claimed_clip.get("end_time", 0.0)),
                                aspect_ratio=rparams["aspect_ratio"],
                                layout_mode=rparams["layout_mode"],
                                segments=rparams["segments"],
                                subtitle_options=rparams["subtitle_options"],
                                expected_range_revision=rparams["expected_range_revision"],
                            )
                            video_path = Path(render_res["video_path"])
                        except RuntimeError as rc_err:
                            # render_clip calls update_clip_media() which requires status IN ('ready_review', 'queued_transcription');
                            # for an already-approved clip, record_render_completion() already wrote video_path + cleared render_stale.
                            latest_after = db.get_clip_by_id(clip_id)
                            if (
                                "Clip range changed during rendering" in str(rc_err)
                                and latest_after
                                and latest_after.get("status") == "approved"
                                and int(latest_after.get("range_revision") or 0) == rparams["expected_range_revision"]
                                and not latest_after.get("render_stale")
                                and latest_after.get("video_path")
                                and Path(latest_after["video_path"]).exists()
                            ):
                                with db.get_db_connection() as conn:
                                    conn.execute(
                                        "UPDATE clips SET rendered_layout_mode = ?, render_stale = 0 WHERE id = ? AND status = 'approved'",
                                        (rparams["layout_mode"], clip_id),
                                    )
                                    conn.commit()
                                video_path = Path(latest_after["video_path"])
                            else:
                                raise
                    elif is_explicitly_stale:
                        raise FileNotFoundError(f"Original source video unavailable to rebuild stale render for clip {clip_id}")
                except Exception as rend_err:
                    if is_explicitly_stale or not (video_path and video_path.exists()):
                        raise RuntimeError(f"Stale render rebuild failed before publish: {rend_err}") from rend_err
                    logger.warning("Delivery render check note for %s: %s", clip_id, rend_err)

            upload_started = True
            if norm_platform == "youtube":
                privacy = "public" if publish_mode == "public" else ("unlisted" if publish_mode == "unlisted" else "private")
                # Include idempotency key marker in YouTube description for search reconciliation
                full_desc = f"{description}\n\n{hashtags}\n\n{idempotency_marker}".strip()
                result = upload_youtube_short(
                    video_path=video_path,
                    title=title,
                    description=full_desc,
                    tags=hashtags,
                    privacy_status=privacy,
                    credentials_data=account_creds
                )
            elif norm_platform == "instagram":
                ig_token = account_creds.get("access_token") if account_creds else None
                ig_uid = (
                    (account_creds.get("ig_user_id") if account_creds else None)
                    or (account_creds.get("account_id") if account_creds else None)
                    or resolved_account_id
                )
                result = upload_instagram_reel(
                    video_path=video_path,
                    caption=f"{title}\n\n{description}\n\n{hashtags}".strip(),
                    access_token=ig_token,
                    ig_user_id=ig_uid,
                    api_base=account_creds.get("api_base") if account_creds else None
                )
            elif norm_platform == "linkedin":
                li_token = account_creds.get("access_token") if account_creds else None
                li_author = (
                    (account_creds.get("author_urn") if account_creds else None)
                    or (account_creds.get("account_id") if account_creds else None)
                    or resolved_account_id
                )
                result = upload_linkedin_video(
                    video_path=video_path,
                    commentary=f"{title}\n\n{description}\n\n{hashtags}".strip(),
                    title=title,
                    access_token=li_token,
                    author_urn=li_author
                )
            elif norm_platform == "twitter":
                x_bearer = (
                    (account_creds.get("bearer_token") or account_creds.get("access_token") or account_creds.get("token"))
                    if account_creds else None
                )
                x_key = account_creds.get("api_key") if account_creds else None
                x_secret = account_creds.get("api_secret") if account_creds else None
                x_tok = account_creds.get("oauth_token") if account_creds else None
                x_tok_sec = account_creds.get("oauth_secret") if account_creds else None
                result = upload_x_video(
                    video_path=video_path,
                    text=f"{title}\n\n{hashtags}".strip(),
                    bearer_token=x_bearer,
                    api_key=x_key,
                    api_secret=x_secret,
                    oauth_token=x_tok,
                    oauth_secret=x_tok_sec
                )
            elif norm_platform == "facebook":
                fb_token = account_creds.get("access_token") if account_creds else None
                fb_page = (
                    (account_creds.get("page_id") if account_creds else None)
                    or (account_creds.get("account_id") if account_creds else None)
                    or resolved_account_id
                )
                result = upload_facebook_reel(
                    video_path=video_path,
                    description=f"{title}\n\n{description}\n\n{hashtags}".strip(),
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
                    conn.commit()
                job_settled = True
                check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
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
                conn.commit()
            job_settled = True

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
                conn.commit()
            job_settled = True
            check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))

        except Exception as e:
            err_str = str(e)
            logger.error("Publishing error for job %s (%s): %s", job_id, platform, err_str)
            new_attempts = attempt_count + 1
            # Check for ambiguous timeout or disconnect errors
            is_ambiguous = is_ambiguous_network_error(e)

            with db.get_db_connection() as conn:
                if is_ambiguous and upload_started:
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
                    conn.commit()
                    job_settled = True
                    check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
                elif new_attempts >= 5:
                    conn.execute("""
                        UPDATE publishing_outbox
                        SET status = 'failed',
                            attempt_count = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            last_error = ?
                        WHERE id = ?
                    """, (new_attempts, err_str, job_id))
                    conn.commit()
                    job_settled = True
                    check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
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
                    conn.commit()
                    job_settled = True

        finally:
            if not job_settled:
                fallback_status = "reconciliation_required" if upload_started else "failed"
                try:
                    with db.get_db_connection() as conn:
                        conn.execute(
                            """UPDATE publishing_outbox
                               SET status = ?,
                                   updated_at = CURRENT_TIMESTAMP,
                                   last_error = COALESCE(last_error, 'Worker aborted before outbox job settled')
                               WHERE id = ? AND status = 'uploading'""",
                            (fallback_status, job_id),
                        )
                        conn.commit()
                    check_and_finalize_clip(clip_id, video_path or Path(f"storage/clips/{clip_id}.mp4"))
                except Exception as cleanup_err:
                    logger.error("Failed releasing dangling uploading state for job %s: %s", job_id, cleanup_err)

    return success_count


def check_and_finalize_clip(clip_id: str, video_path: Path):
    """Finalize clip lifecycle when all outbox jobs settle (closing UI publishing spinner cleanly)."""
    with db.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM publishing_outbox WHERE clip_id = ?", (clip_id,))
        statuses = [row["status"] for row in cursor.fetchall()]

        terminal_states = {"published", "failed", "blocked_needs_auth", "reconciliation_required"}
        all_settled = bool(statuses) and all(s in terminal_states for s in statuses)
        any_published = any(s == "published" for s in statuses)
        any_reconciliation = any(s == "reconciliation_required" for s in statuses)

        if all_settled and any_published:
            cursor.execute("""
                UPDATE clips 
                SET status = 'published', published_at = CURRENT_TIMESTAMP 
                WHERE id = ? AND status = 'approved'
            """, (clip_id,))
            if cursor.rowcount == 1:
                conn.commit()
                logger.info("Platform uploads finished for clip %s (with at least 1 published). Clip marked published.", clip_id)
            else:
                conn.commit()
                logger.info(
                    "Platform uploads finished for clip %s, but clip is no longer approved; status left untouched.", clip_id
                )
        elif all_settled and not any_published and not any_reconciliation:
            # All destinations ended in terminal failed / blocked_needs_auth without publishing.
            # Return clip to ready_review so UI closes the publishing spinner and user can fix/retry.
            cursor.execute("""
                UPDATE clips
                SET status = 'ready_review'
                WHERE id = ? AND status = 'approved'
            """, (clip_id,))
            conn.commit()
            if cursor.rowcount == 1:
                logger.warning("All outbox jobs for clip %s failed/blocked; returned clip to ready_review to clear spinner.", clip_id)


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
