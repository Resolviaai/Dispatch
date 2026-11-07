"""Route C: Direct Video Uploads — PC-side pickup (Task 14).

A claimed direct job (processing_jobs.source='direct') references a fully-uploaded,
quota-accounted R2 object (kind 'clip_video', user-owned, staged via the generic
/reserve → PUT → /complete pipeline — the PC may have been offline the whole time).

This module, invoked by RemoteJobPoller via its direct_handler:

1. Requests a short-lived presigned GET URL from the Worker (/media-url, acting
   for the job's owner user — the Worker enforces the PC-impersonation allowlist
   and object ownership, so no privileged R2 credential ever lives on the PC).
2. Streams the object into YOUTUBE_INBOX_DIR as `dir_<object_id>.mp4`, using a
   `.part` temp file during transfer so partial data can never be picked up
   downstream or scanned by the retention janitor.
3. Hands the local file to youtube_inbox.catcher.ingest_direct_video(), which
   ffprobe-validates (hard ≤180.5 s cap) and registers the youtube_inbox row at
   DOWNLOADED — from where the unchanged transcription/highlight/render workers
   produce exactly one full-length captioned clip.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import requests

from dispatch import db
from dispatch.config import YOUTUBE_INBOX_DIR

logger = logging.getLogger("dispatch.direct_ingest")

_WORKER_URL_DEFAULT = "https://dispatch-cloud.resolviaai.workers.dev"

# R2 clip objects are bounded at 512 MiB by the reserve quota contract; the
# download cap adds slack so an anomalous object aborts instead of filling disk.
DIRECT_DOWNLOAD_CAP_BYTES = 536870912 + 16 * 1024 * 1024

_STREAM_TIMEOUT = (15.0, 300.0)


def _worker_base() -> str:
    return os.getenv("DISPATCH_WORKER_URL") or _WORKER_URL_DEFAULT


def get_worker_token() -> str:
    return os.getenv("DISPATCH_WORKER_TOKEN") or ""


def _actor_headers(user_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {get_worker_token()}",
        "X-Dispatch-User-Id": user_id,
        "Content-Type": "application/json",
    }


def _request_media_url(user_id: str, object_id: str, video_id: str) -> str:
    """Fetch a fresh presigned GET URL for the uploaded object (10-minute TTL)."""
    import httpx

    try:
        res = httpx.post(
            f"{_worker_base()}/media-url",
            headers=_actor_headers(user_id),
            json={"media_object_id": object_id},
            timeout=15,
        )
        body = res.json() if res.content else {}
    except Exception as e:
        raise RuntimeError(f"presigned URL request failed for {video_id}: {e}") from e

    url = body.get("url") if isinstance(body, dict) else None
    if res.status_code == 401:
        raise RuntimeError("worker token rejected for direct pickup (check DISPATCH_WORKER_TOKEN/PC allowlist)")
    if res.status_code == 409:
        raise RuntimeError(f"uploaded object for {video_id} is not active ({body.get('error')})")
    if res.status_code == 403:
        raise RuntimeError(f"object for {video_id} is not owned by the job's user")
    if res.status_code != 200 or not isinstance(url, str) or not url:
        raise RuntimeError(f"presigned URL unavailable for {video_id} (HTTP {res.status_code})")
    return url


def _download_direct_video(url: str, video_id: str) -> Path:
    """Stream the object to `dir_<id>.mp4` (`.part` while transferring)."""
    target = Path(YOUTUBE_INBOX_DIR) / f"{video_id}.mp4"
    YOUTUBE_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    received = 0
    try:
        with requests.get(url, stream=True, timeout=_STREAM_TIMEOUT, headers={"User-Agent": "Dispatch-PC/1.0"}) as resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and ctype not in ("video/mp4", "application/octet-stream", "binary/octet-stream"):
                raise RuntimeError(f"unexpected content type {ctype!r} for direct video")
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > DIRECT_DOWNLOAD_CAP_BYTES:
                        raise RuntimeError("direct video exceeds the 512 MiB object cap mid-download")
                    fh.write(chunk)
        if received <= 0:
            raise RuntimeError("direct video download produced an empty file")
        os.replace(part, target)
    except Exception:
        part.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    logger.info("Direct video %s downloaded via presigned URL (%.1f MB)", video_id, received / (1024 * 1024))
    return target


def process_direct_job(job: Any) -> Dict[str, Any]:
    """Direct-route handler: download the uploaded object → validate → inbox row.

    Mirrors the ingest_video() return contract consumed by RemoteJobPoller._process:
    status DOWNLOADED on success (job marked done), any raise marks the job failed.
    """
    object_id = str(getattr(job, "upload_object_id", "") or "").strip()
    user_id = str(getattr(job, "user_id", "") or "").strip()
    if not object_id or not user_id:
        raise RuntimeError("direct job is missing upload_object_id or user_id")
    video_id = f"dir_{object_id}"
    title = str(getattr(job, "source_title", "") or "").strip() or "Direct upload"

    # Idempotent re-claim: a previously validated copy still on disk short-circuits
    # the download (a 45-minute lease reclaimed mid-pipeline must not re-haul 300 MB).
    existing = db.get_youtube_video(video_id)
    if existing and existing.get("status") != "FAILED":
        local_row_path = existing.get("local_video_path")
        if local_row_path and Path(local_row_path).exists() and Path(local_row_path).stat().st_size > 0:
            from dispatch.ingestion.validator import probe_video
            is_valid, _, _ = probe_video(Path(local_row_path))
            if is_valid:
                logger.info("Direct video %s already ingested (status %s). Skipping download.",
                            video_id, existing["status"])
                return {
                    "video_id": video_id,
                    "status": existing["status"],
                    "local_video_path": str(local_row_path),
                    "verified": True,
                }

    # Stage 'downloading' is already reported by RemoteJobPoller._process for both branches.
    url = _request_media_url(user_id, object_id, video_id)
    target = _download_direct_video(url, video_id)

    update_remote_stage(job, "validating")
    from dispatch.youtube_inbox.catcher import YouTubeInboxCatcher
    res = YouTubeInboxCatcher().ingest_direct_video(
        video_id=video_id,
        local_path=target,
        title=title,
        user_id=user_id,
        source_file_size=target.stat().st_size,
    )
    dur = res.get("duration") or (res.get("metadata") or {}).get("duration")
    if dur:
        update_remote_stage(job, "transcribing", video_id=video_id, duration=float(dur))
    return res


def update_remote_stage(job: Any, stage: str, video_id: str = "", duration: Optional[float] = None) -> None:
    """Best-effort stage reporting so the phone sees 'downloading' → 'transcribing'."""
    try:
        from dispatch.youtube_connections import update_remote_job_stage
        update_remote_job_stage(getattr(job, "job_id", ""), stage, video_id=video_id, duration=duration)
    except Exception:
        pass
