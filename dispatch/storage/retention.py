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
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from dispatch import db
from dispatch.config import STORAGE_DIR

logger = logging.getLogger("dispatch.storage.retention")

SEVEN_DAYS_SECONDS = 7 * 24 * 3600       # 168 hours
SEVENTY_TWO_HOURS_SECONDS = 72 * 3600    # 72 hours
DEFAULT_STORAGE_CEILING_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


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

    For each source video, inspect associated clips to evaluate:
    - total clips count
    - published/approved clips count
    - all clips published/approved boolean
    - age in seconds
    - post-publish grace age (time since latest clip published/approved)
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
            SELECT id, session_id, chunk_id, status, created_at, approved_at, published_at
            FROM clips
        """)
        clips = cursor.fetchall()

    # Index clips by chunk_id and session_id
    clips_by_chunk: Dict[str, List[Any]] = {}
    clips_by_session: Dict[str, List[Any]] = {}
    for clip in clips:
        cid = clip["chunk_id"]
        sid = clip["session_id"]
        if cid:
            clips_by_chunk.setdefault(cid, []).append(clip)
        if sid:
            clips_by_session.setdefault(sid, []).append(clip)

    # Process chunks
    for ch in chunks:
        cid = ch["chunk_id"]
        fpath = ch["filepath"]
        if not fpath:
            continue
        p = Path(fpath)
        if not p.exists() or not p.is_file():
            continue

        created_dt = parse_timestamp(ch["created_at"]) or datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        file_size = p.stat().st_size
        mtime_dt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)

        # Match clips either by chunk_id or session_id
        matched_clips = clips_by_chunk.get(cid, [])
        if not matched_clips and ch["session_id"]:
            matched_clips = clips_by_session.get(ch["session_id"], [])

        source_items[str(p.resolve())] = {
            "type": "chunk",
            "id": cid,
            "session_id": ch["session_id"],
            "path": p,
            "created_at": created_dt,
            "mtime": mtime_dt,
            "file_size": file_size,
            "clips": matched_clips,
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

        matched_clips = []
        if yt["session_id"] and yt["session_id"] in clips_by_session:
            matched_clips = clips_by_session[yt["session_id"]]

        if key in source_items:
            # Combine or merge clips
            existing_clips = source_items[key]["clips"]
            all_c = {c["id"]: c for c in (existing_clips + matched_clips)}
            source_items[key]["clips"] = list(all_c.values())
            source_items[key]["youtube_video_id"] = vid
        else:
            source_items[key] = {
                "type": "youtube_inbox",
                "id": vid,
                "session_id": yt["session_id"],
                "path": p,
                "created_at": created_dt,
                "mtime": mtime_dt,
                "file_size": file_size,
                "clips": matched_clips,
                "youtube_video_id": vid,
            }

    results = []
    for item in source_items.values():
        p = item["path"]
        created_at = item["created_at"]
        age_seconds = (now - created_at).total_seconds() if created_at else 0.0

        item_clips = item["clips"]
        total_clips = len(item_clips)
        
        # Check publish/approval status of clips
        published_or_approved_clips = [
            c for c in item_clips
            if c["status"] in ("published", "approved")
        ]
        
        all_clips_published_or_approved = (
            total_clips > 0 and len(published_or_approved_clips) == total_clips
        )

        latest_publish_dt: Optional[datetime] = None
        if all_clips_published_or_approved:
            for c in published_or_approved_clips:
                pub_ts = parse_timestamp(c["published_at"]) or parse_timestamp(c["approved_at"]) or parse_timestamp(c["created_at"])
                if pub_ts:
                    if latest_publish_dt is None or pub_ts > latest_publish_dt:
                        latest_publish_dt = pub_ts

        time_since_all_published = (
            (now - latest_publish_dt).total_seconds() if latest_publish_dt else 0.0
        )

        # Tier 1: 7-Day Source Retention (168 hours)
        eligible_7_day = age_seconds >= SEVEN_DAYS_SECONDS

        # Tier 2: 72-Hour Post-Publish Grace
        eligible_72_hour_post_publish = (
            all_clips_published_or_approved
            and latest_publish_dt is not None
            and time_since_all_published >= SEVENTY_TWO_HOURS_SECONDS
        )

        # Candidate for cleanup under normal (non-ceiling) policy
        eligible_for_cleanup = eligible_7_day or eligible_72_hour_post_publish

        results.append({
            **item,
            "age_seconds": age_seconds,
            "total_clips": total_clips,
            "approved_or_published_clips": len(published_or_approved_clips),
            "all_clips_published_or_approved": all_clips_published_or_approved,
            "latest_publish_dt": latest_publish_dt,
            "time_since_all_published": time_since_all_published,
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
            path_str = str(p)

            # Update chunks
            cursor.execute("""
                UPDATE chunks 
                SET filepath = '', status = 'purged' 
                WHERE filepath = ? OR id = ?
            """, (path_str, item.get("id")))

            # Update youtube_inbox
            cursor.execute("""
                UPDATE youtube_inbox 
                SET local_video_path = '' 
                WHERE local_video_path = ? OR video_id = ?
            """, (path_str, item.get("youtube_video_id", item.get("id"))))

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
       If total storage exceeds the ceiling, prune oldest source videos past 72 hours FIFO
       until below ceiling.
    
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

    # Phase 2: Tier 3 Hard Disk Safety Ceiling (FIFO eviction for videos older than 72 hours)
    if current_storage_size > storage_ceiling_bytes:
        stats["ceiling_exceeded"] = True
        logger.warning(
            "Storage directory %s exceeds safety ceiling (%d > %d bytes). Enforcing Tier 3 FIFO prune.",
            storage_dir, current_storage_size, storage_ceiling_bytes
        )

        # Refresh sources that still exist on disk
        remaining_sources = get_source_videos_status(now=now)
        # Filter for videos older than 72 hours (age_seconds >= 72h)
        fifo_candidates = [
            s for s in remaining_sources
            if s["age_seconds"] >= SEVENTY_TWO_HOURS_SECONDS
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
