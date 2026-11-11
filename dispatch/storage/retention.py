"""Multi-Tier Smart Storage Retention Policy Manager for Dispatch PC.

Implements the 3-Tier Storage Retention Law:
1. 7-Day Source Retention (168 hours):
   Full source videos remain on PC for at least 7 days for re-clipping or trim expansion.
2. 72-Hour Post-Publish Grace:
   If ALL clips extracted from that source video are published/approved (and at least one clip
   exists), source video is eligible for cleanup after 72 hours from the latest publication/approval.
3. 10 GB Hard Disk Safety Ceiling:
   If total storage/ directory size exceeds 10 GB (or custom threshold), oldest source videos
   past 72 hours are pruned FIFO until total storage drops below the threshold.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from dispatch import db
from dispatch.config import STORAGE_DIR

logger = logging.getLogger("dispatch.storage.retention")

SEVEN_DAYS_SECONDS = 7 * 24 * 3600       # 168 hours
SEVENTY_TWO_HOURS_SECONDS = 72 * 3600    # 72 hours
DEFAULT_STORAGE_CEILING_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

ACTIVE_CHUNK_STATUSES = {
    "incoming", "queued", "processing", "transcribing",
    "analyzing", "rendering", "in_progress", "retrying",
}
ACTIVE_YOUTUBE_STATUSES = {
    "discovered",
    "waiting_for_youtube_processing",
    "stalled_youtube_processing",
    "waiting_for_visibility_check",
    "downloading",
    "validating",
    "downloaded",
    "waiting_for_transcript",
    "captions_unavailable",
    "transcribing",
    "transcript_fetched",
    "transcribed",
    "analyzing",
    "clips_defined",
    "rendering",
    "queued",
    "processing",
    "in_progress",
    "retrying",
    "retry_pending",
}
TERMINAL_SOURCE_STATUSES = {
    "completed", "failed", "rejected", "purged", "clips_created",
    "transcribed", "verified", "processed",
}
ACTIVE_PIPELINE_JOB_STATUSES = {
    "QUEUED", "PROCESSING", "WAITING_FOR_AI", "WAITING_FOR_RESOURCES", "RETRY_PENDING",
}
PENDING_CLIP_STATUSES = {"queued_transcription", "approved"}
TERMINAL_OUTBOX_STATUSES = {"published", "cancelled"}


def get_directory_size(path: Path) -> int:
    """Calculate the total size of all files in a directory in bytes."""
    total = 0
    if not path.exists():
        return 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += get_directory_size(Path(entry.path))
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return total


def parse_timestamp(ts: Any) -> Optional[datetime]:
    """Parse SQLite timestamp string or datetime into UTC datetime."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        # Clean common sqlite formats: '2026-10-30 02:25:54' or ISO
        clean_ts = ts.strip().replace(" ", "T")
        try:
            dt = datetime.fromisoformat(clean_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(ts.strip(), fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
    return None


def get_source_videos_status(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Scan database for all known source video records from chunks and youtube_inbox.

    For each source video, inspect associated clips, pipeline jobs, and outbox rows to evaluate:
    - total clips count
    - published clips count (terminal 'published' only; never 'approved')
    - all clips published boolean
    - age in seconds
    - post-publish grace age (time since latest clip published)
    - active/pending/retrying/rendering/outbox protection state
    - file size on disk
    - eligibility under 7-day, 72-hour, or ceiling rules
    """
    if now is None:
        now = datetime.now(timezone.utc)

    source_items: Dict[str, Dict[str, Any]] = {}

    with db.get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Query chunks table
        cursor.execute("""
            SELECT id as chunk_id, session_id, filename, filepath, file_hash, created_at, status
            FROM chunks
        """)
        chunks = cursor.fetchall()

        # 2. Query youtube_inbox table
        cursor.execute("""
            SELECT video_id, dispatch_id, session_id, title, local_video_path, discovered_at, updated_at, status
            FROM youtube_inbox
        """)
        yt_rows = cursor.fetchall()

        # 3. Query all clips for analysis
        cursor.execute("""
            SELECT id, session_id, chunk_id, status, created_at, approved_at, published_at, video_path
            FROM clips
        """)
        clips = cursor.fetchall()

        # 4. Query active/pending/retrying pipeline_jobs
        active_jobs: List[Any] = []
        try:
            cursor.execute("""
                SELECT job_id, chunk_id, session_id, current_stage, status, lease_expires_at, heartbeat_at
                FROM pipeline_jobs
                WHERE status NOT IN ('COMPLETED', 'FAILED_PERMANENT')
            """)
            active_jobs = cursor.fetchall()
        except sqlite3.OperationalError:
            active_jobs = []

        # 5. Query non-terminal publishing_outbox jobs
        pending_outbox_rows: List[Any] = []
        try:
            cursor.execute("""
                SELECT id, clip_id, status
                FROM publishing_outbox
                WHERE LOWER(status) NOT IN ('published', 'cancelled')
            """)
            pending_outbox_rows = cursor.fetchall()
        except sqlite3.OperationalError:
            pending_outbox_rows = []

    # Index clips by chunk_id and session_id
    clips_by_chunk: Dict[str, List[Any]] = {}
    clips_by_session: Dict[str, List[Any]] = {}
    clip_by_id: Dict[str, Any] = {}
    for clip in clips:
        clip_by_id[clip["id"]] = clip
        cid = clip["chunk_id"]
        sid = clip["session_id"]
        if cid:
            clips_by_chunk.setdefault(cid, []).append(clip)
        if sid:
            clips_by_session.setdefault(sid, []).append(clip)

    # Index active pipeline jobs by chunk_id and session_id
    active_jobs_by_chunk: Dict[str, List[Any]] = {}
    active_jobs_by_session: Dict[str, List[Any]] = {}
    for job in active_jobs:
        if str(job["status"] or "") not in ACTIVE_PIPELINE_JOB_STATUSES:
            continue
        j_cid = job["chunk_id"]
        j_sid = job["session_id"]
        if j_cid:
            active_jobs_by_chunk.setdefault(j_cid, []).append(job)
        if j_sid:
            active_jobs_by_session.setdefault(j_sid, []).append(job)

    # Index non-terminal outbox clip_ids and any resolved media paths tied to pending clips/outbox
    pending_outbox_clip_ids: Set[str] = {
        row["clip_id"] for row in pending_outbox_rows if row["clip_id"]
    }
    protected_media_paths: Set[str] = set()
    for clip in clips:
        vp = clip["video_path"]
        if vp and (
            clip["id"] in pending_outbox_clip_ids
            or str(clip["status"] or "").lower() in PENDING_CLIP_STATUSES
        ):
            try:
                protected_media_paths.add(str(Path(vp).resolve()))
            except Exception:
                pass

    # Index chunk IDs by file_hash and resolved path (even if chunk filepath is empty/moved)
    chunk_ids_by_file_hash: Dict[str, Set[str]] = {}
    chunk_ids_by_resolved_path: Dict[str, Set[str]] = {}
    for ch in chunks:
        cid = ch["chunk_id"]
        fhash = ch["file_hash"]
        if fhash:
            chunk_ids_by_file_hash.setdefault(fhash, set()).add(cid)
        fpath = ch["filepath"]
        if fpath:
            try:
                chunk_ids_by_resolved_path.setdefault(str(Path(fpath).resolve()), set()).add(cid)
            except Exception:
                pass

    # Process chunks
    for ch in chunks:
        cid = ch["chunk_id"]
        fpath = ch["filepath"]
        if not fpath:
            continue
        p = Path(fpath)
        if not p.exists() or not p.is_file():
            continue

        key = str(p.resolve())
        created_dt = parse_timestamp(ch["created_at"]) or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        file_size = p.stat().st_size
        mtime_dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)

        # Match clips by both chunk_id and session_id
        matched_map: Dict[str, Any] = {c["id"]: c for c in clips_by_chunk.get(cid, [])}
        if ch["session_id"]:
            for c in clips_by_session.get(ch["session_id"], []):
                matched_map.setdefault(c["id"], c)

        matched_jobs = list(active_jobs_by_chunk.get(cid, []))
        if ch["session_id"]:
            matched_jobs.extend(active_jobs_by_session.get(ch["session_id"], []))

        source_items[key] = {
            "type": "chunk",
            "id": cid,
            "chunk_ids": {cid},
            "session_id": ch["session_id"],
            "path": p,
            "raw_paths": {fpath, str(p), key},
            "created_at": created_dt,
            "mtime": mtime_dt,
            "file_size": file_size,
            "clips": list(matched_map.values()),
            "active_jobs": matched_jobs,
            "status": str(ch["status"] or "").lower(),
            "chunk_status": str(ch["status"] or "").lower(),
            "youtube_status": "",
        }

    # Process youtube_inbox items
    for yt in yt_rows:
        vid = yt["video_id"]
        fpath = yt["local_video_path"]
        if not fpath:
            continue
        p = Path(fpath)
        if not p.exists() or not p.is_file():
            continue

        key = str(p.resolve())
        created_dt = parse_timestamp(yt["discovered_at"]) or parse_timestamp(yt["updated_at"]) or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        file_size = p.stat().st_size
        mtime_dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)

        candidate_chunk_ids: Set[str] = {vid, f"chk_yt_{vid}"}
        candidate_chunk_ids.update(chunk_ids_by_file_hash.get(f"yt_{vid}", set()))
        candidate_chunk_ids.update(chunk_ids_by_resolved_path.get(key, set()))

        matched_map = {}
        if yt["session_id"]:
            for c in clips_by_session.get(yt["session_id"], []):
                matched_map[c["id"]] = c
        for cid in candidate_chunk_ids:
            for c in clips_by_chunk.get(cid, []):
                matched_map[c["id"]] = c

        matched_jobs = []
        if yt["session_id"]:
            matched_jobs.extend(active_jobs_by_session.get(yt["session_id"], []))
        for cid in candidate_chunk_ids:
            matched_jobs.extend(active_jobs_by_chunk.get(cid, []))

        yt_status = str(yt["status"] or "").lower()

        if key in source_items:
            # Combine clips, chunk_ids, jobs, and preserve youtube_inbox status alongside chunk status
            existing = source_items[key]
            all_c = {c["id"]: c for c in existing["clips"]}
            all_c.update(matched_map)
            existing["clips"] = list(all_c.values())
            existing["chunk_ids"].update(candidate_chunk_ids)
            existing["raw_paths"].update({fpath, str(p), key})
            existing["active_jobs"] = list(existing["active_jobs"]) + matched_jobs
            existing["youtube_video_id"] = vid
            existing["youtube_status"] = yt_status
            if not existing.get("session_id") and yt["session_id"]:
                existing["session_id"] = yt["session_id"]
        else:
            source_items[key] = {
                "type": "youtube_inbox",
                "id": vid,
                "chunk_ids": set(candidate_chunk_ids),
                "session_id": yt["session_id"],
                "path": p,
                "raw_paths": {fpath, str(p), key},
                "created_at": created_dt,
                "mtime": mtime_dt,
                "file_size": file_size,
                "clips": list(matched_map.values()),
                "active_jobs": matched_jobs,
                "status": yt_status,
                "chunk_status": "",
                "youtube_status": yt_status,
                "youtube_video_id": vid,
            }

    results = []
    for key, item in source_items.items():
        p = item["path"]
        created_at = item["created_at"]
        age_seconds = (now - created_at).total_seconds() if created_at else 0.0

        item_clips = item["clips"]
        total_clips = len(item_clips)

        # Tier 2 requires real terminal 'published' status on every clip (NEVER 'approved')
        published_clips = [
            c for c in item_clips
            if str(c["status"] or "").lower() == "published"
        ]
        all_clips_published = (
            total_clips > 0 and len(published_clips) == total_clips
        )

        latest_publish_dt: Optional[datetime] = None
        if all_clips_published:
            for c in published_clips:
                pub_ts = parse_timestamp(c["published_at"]) or parse_timestamp(c["approved_at"]) or parse_timestamp(c["created_at"])
                if pub_ts:
                    if latest_publish_dt is None or pub_ts > latest_publish_dt:
                        latest_publish_dt = pub_ts

        time_since_all_published = (
            (now - latest_publish_dt).total_seconds() if latest_publish_dt else 0.0
        )

        chunk_status = str(item.get("chunk_status") or "").lower()
        youtube_status = str(item.get("youtube_status") or "").lower()
        has_active_chunk = chunk_status in ACTIVE_CHUNK_STATUSES
        has_active_yt = youtube_status in ACTIVE_YOUTUBE_STATUSES
        has_active_job = bool(item.get("active_jobs"))
        has_pending_clips = any(
            str(c["status"] or "").lower() in PENDING_CLIP_STATUSES
            for c in item_clips
        )
        has_pending_outbox = (
            any(c["id"] in pending_outbox_clip_ids for c in item_clips)
            or (key in protected_media_paths)
        )

        is_active_pipeline = has_active_chunk or has_active_yt or has_active_job
        is_protected = is_active_pipeline or has_pending_clips or has_pending_outbox
        is_terminal = (
            not is_protected
            and (
                (youtube_status in TERMINAL_SOURCE_STATUSES)
                if youtube_status
                else (chunk_status in TERMINAL_SOURCE_STATUSES)
            )
        )

        # Tier 1: 7-Day Source Retention (168 hours)
        # Safety invariant: never prune protected/active/outbox/pending items;
        # if 0 clips were generated, ONLY prune after 7 days if status is terminal.
        eligible_7_day = (
            age_seconds >= SEVEN_DAYS_SECONDS
            and not is_protected
            and (total_clips > 0 or is_terminal)
        )

        # Tier 2: 72-Hour Post-Publish Grace (strictly requires terminal 'published')
        eligible_72_hour_post_publish = (
            all_clips_published
            and latest_publish_dt is not None
            and time_since_all_published >= SEVENTY_TWO_HOURS_SECONDS
            and not is_protected
        )

        # Candidate for cleanup under normal (non-ceiling) policy
        eligible_for_cleanup = (eligible_7_day or eligible_72_hour_post_publish) and not is_protected

        results.append({
            **item,
            "age_seconds": age_seconds,
            "total_clips": total_clips,
            "published_clips": len(published_clips),
            "all_clips_published": all_clips_published,
            "approved_or_published_clips": len(published_clips),
            "all_clips_published_or_approved": all_clips_published,
            "latest_publish_dt": latest_publish_dt,
            "time_since_all_published": time_since_all_published,
            "is_active_pipeline": is_active_pipeline,
            "has_pending_clips": has_pending_clips,
            "has_pending_outbox": has_pending_outbox,
            "is_protected": is_protected,
            "eligible_7_day": eligible_7_day,
            "eligible_72_hour_post_publish": eligible_72_hour_post_publish,
            "eligible_for_cleanup": eligible_for_cleanup,
        })

    return results


def prune_source_video(item: Dict[str, Any]) -> bool:
    """Safely unlink a source video file and update the database references."""
    p: Path = item["path"]
    try:
        if p.exists() and p.is_file():
            p.unlink()
            logger.info("Purged source video %s (Size: %d bytes, Type: %s)", p.name, item.get("file_size", 0), item.get("type"))
    except Exception as e:
        logger.error("Failed to delete source video file %s: %s", p, e)
        return False

    # Update database records so UI reflects purged status
    try:
        with db.get_db_connection() as conn:
            cursor = conn.cursor()
            path_candidates = list(item.get("raw_paths") or {str(p)})
            chunk_ids = list(item.get("chunk_ids") or ([item["id"]] if item.get("id") else []))

            if path_candidates or chunk_ids:
                p_ph = ",".join("?" for _ in path_candidates) if path_candidates else "''"
                c_ph = ",".join("?" for _ in chunk_ids) if chunk_ids else "''"
                if path_candidates and chunk_ids:
                    cursor.execute(f"""
                        UPDATE chunks
                        SET filepath = '', status = 'purged'
                        WHERE filepath IN ({p_ph}) OR id IN ({c_ph})
                    """, tuple(path_candidates) + tuple(chunk_ids))
                elif path_candidates:
                    cursor.execute(f"""
                        UPDATE chunks
                        SET filepath = '', status = 'purged'
                        WHERE filepath IN ({p_ph})
                    """, tuple(path_candidates))

            yt_vid = item.get("youtube_video_id") or (item.get("id") if item.get("type") == "youtube_inbox" else None)
            if path_candidates and yt_vid:
                p_ph = ",".join("?" for _ in path_candidates)
                cursor.execute(f"""
                    UPDATE youtube_inbox
                    SET local_video_path = ''
                    WHERE local_video_path IN ({p_ph}) OR video_id = ?
                """, tuple(path_candidates) + (yt_vid,))
            elif yt_vid:
                cursor.execute("UPDATE youtube_inbox SET local_video_path = '' WHERE video_id = ?", (yt_vid,))
            elif path_candidates:
                p_ph = ",".join("?" for _ in path_candidates)
                cursor.execute(f"UPDATE youtube_inbox SET local_video_path = '' WHERE local_video_path IN ({p_ph})", tuple(path_candidates))

            conn.commit()
    except Exception as e:
        logger.warning("Failed to update database for purged source video %s: %s", p.name, e)

    return True


def enforce_retention_policy(
    storage_dir: Optional[Path] = None,
    storage_ceiling_bytes: int = DEFAULT_STORAGE_CEILING_BYTES,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Execute the 3-Tier Storage Retention Policy:
    
    1. Tier 1 (7-Day Source Retention) & Tier 2 (72-Hour Post-Publish Grace):
       Prune source videos meeting either 7-day age or 72-hour all-clips published condition.
    2. Tier 3 (10 GB Hard Disk Safety Ceiling):
       If total storage exceeds the ceiling, prune oldest eligible/unprotected source videos
       past 72 hours FIFO until below ceiling.
    
    Returns a summary dict with action counts and byte amounts.
    """
    if storage_dir is None:
        storage_dir = STORAGE_DIR
    if now is None:
        now = datetime.now(timezone.utc)

    stats = {
        "pruned_7_day": 0,
        "pruned_72_hour_published": 0,
        "pruned_ceiling_fifo": 0,
        "freed_bytes": 0,
        "initial_storage_bytes": 0,
        "final_storage_bytes": 0,
        "storage_ceiling_bytes": storage_ceiling_bytes,
        "ceiling_exceeded": False,
    }

    initial_size = get_directory_size(storage_dir)
    stats["initial_storage_bytes"] = initial_size
    current_storage_size = initial_size

    # Phase 1: Normal Time & Post-Publish Grace Sweep (Tier 1 & Tier 2)
    sources = get_source_videos_status(now=now)
    
    for item in sources:
        if item["eligible_7_day"]:
            if prune_source_video(item):
                stats["pruned_7_day"] += 1
                freed = item.get("file_size", 0)
                stats["freed_bytes"] += freed
                current_storage_size -= freed
        elif item["eligible_72_hour_post_publish"]:
            if prune_source_video(item):
                stats["pruned_72_hour_published"] += 1
                freed = item.get("file_size", 0)
                stats["freed_bytes"] += freed
                current_storage_size -= freed

    # Phase 2: Tier 3 Hard Disk Safety Ceiling (FIFO eviction for unprotected videos older than 72 hours)
    if current_storage_size > storage_ceiling_bytes:
        stats["ceiling_exceeded"] = True
        logger.warning(
            "Storage directory %s exceeds safety ceiling (%d > %d bytes). Enforcing Tier 3 FIFO prune.",
            storage_dir, current_storage_size, storage_ceiling_bytes
        )

        # Refresh sources that still exist on disk
        remaining_sources = get_source_videos_status(now=now)
        # Filter for unprotected videos older than 72 hours (never evict active/pending/retrying/rendering/outbox)
        fifo_candidates = [
            s for s in remaining_sources
            if s["age_seconds"] >= SEVENTY_TWO_HOURS_SECONDS and not s["is_protected"]
        ]
        # Sort FIFO: oldest created_at first
        fifo_candidates.sort(key=lambda x: x["created_at"] or datetime.min.replace(tzinfo=timezone.utc))

        for cand in fifo_candidates:
            if current_storage_size <= storage_ceiling_bytes:
                break
            if prune_source_video(cand):
                stats["pruned_ceiling_fifo"] += 1
                freed = cand.get("file_size", 0)
                stats["freed_bytes"] += freed
                current_storage_size -= freed

    stats["final_storage_bytes"] = get_directory_size(storage_dir)
    return stats
