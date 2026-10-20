"""dispatch.video_engine.thumbnail
Production-grade video thumbnail generation, freshness validation, and cache invalidation.

Guarantees:
- Atomicity: Uses temporary files with atomic rename to prevent partially written images.
- Freshness: Compares modification timestamps between source/rendered videos and thumbnails.
- Automatic Regeneration: Automatically regenerates stale or missing thumbnails when videos change.
- Efficient Caching: Skips regeneration when thumbnail is newer than the source video.
- Versioning: Injects version query strings (?v={mtime}) into media URLs to ensure instant cache invalidation on mobile WebViews.
"""
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from dispatch.config import CLIPS_DIR, PROCESSING_DIR, INCOMING_DIR
from dispatch import db

logger = logging.getLogger("dispatch.video_engine.thumbnail")


def resolve_clip_source_path(clip: Dict[str, Any]) -> Optional[Path]:
    """Safely resolve the original raw source video chunk for a clip."""
    # 1. Resolve through chunk_id in chunks table
    chunk_id = clip.get("chunk_id")
    if chunk_id:
        with db.get_db_connection() as conn:
            row = conn.execute("SELECT filepath FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if row and row["filepath"]:
                cand = Path(row["filepath"])
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    return cand

    # 2. Check storage/incoming and storage/processing using session_id or chunk_id
    for search_dir in (PROCESSING_DIR, INCOMING_DIR):
        if clip.get("session_id"):
            sess = clip["session_id"]
            cand = search_dir / f"{sess}.mp4"
            if cand.exists() and cand.stat().st_size > 0:
                return cand
        if chunk_id:
            cand = search_dir / f"{chunk_id}.mp4"
            if cand.exists() and cand.stat().st_size > 0:
                return cand

    # 3. Check YouTube inbox
    clip_id_str = clip.get("id", "")
    if clip_id_str.startswith("yt_") or (clip.get("chunk_id") and str(clip["chunk_id"]).startswith("yt_")):
        yt_id = clip.get("chunk_id") or clip_id_str
        clean_yt_id = str(yt_id).replace("yt_", "").strip()
        yt_row = db.get_youtube_video(clean_yt_id)
        if yt_row and yt_row.get("local_video_path"):
            cand = Path(yt_row["local_video_path"])
            if cand.exists() and cand.stat().st_size > 0:
                return cand

    return None


def resolve_active_clip_video(clip: Dict[str, Any]) -> Optional[Path]:
    """Resolve the active video file currently representing this clip on disk.
    Prioritizes the rendered video MP4, falling back to the raw source video chunk.
    """
    # 1. Rendered video in CLIPS_DIR
    vpath = clip.get("video_path")
    if vpath:
        cand = Path(vpath)
        if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
            return cand

    # Check direct filename in CLIPS_DIR based on clip ID
    clip_id = clip.get("id")
    if clip_id:
        cand = CLIPS_DIR / f"{clip_id}.mp4"
        if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
            return cand

    # 2. Fall back to raw source video chunk
    return resolve_clip_source_path(clip)


def generate_thumbnail_from_video(
    video_path: Path,
    output_thumb_path: Path,
    timestamp_sec: float = 0.5
) -> bool:
    """Atomically extract a single high-quality frame from video using FFmpeg.
    Uses a temporary file to avoid exposing partially written images.
    """
    if not video_path.exists() or video_path.stat().st_size == 0:
        logger.warning("Cannot generate thumbnail: source video does not exist or is empty (%s)", video_path)
        return False

    output_thumb_path.parent.mkdir(parents=True, exist_ok=True)
    temp_thumb_path = output_thumb_path.with_suffix(f".tmp_{uuid.uuid4().hex[:6]}.jpg")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, timestamp_sec):.3f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(temp_thumb_path)
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        if res.returncode == 0 and temp_thumb_path.exists() and temp_thumb_path.stat().st_size > 0:
            temp_thumb_path.replace(output_thumb_path)
            # Ensure thumbnail timestamp is strictly >= video timestamp so freshness checks succeed
            try:
                vid_mtime = video_path.stat().st_mtime
                if output_thumb_path.stat().st_mtime < vid_mtime:
                    os.utime(str(output_thumb_path), (vid_mtime + 1.0, vid_mtime + 1.0))
            except Exception:
                pass
            return True
        else:
            logger.warning("FFmpeg thumbnail extraction returned code %d for %s", res.returncode, video_path)
    except Exception as e:
        logger.error("FFmpeg thumbnail extraction exception for %s: %s", video_path, e)
    finally:
        if temp_thumb_path.exists():
            try:
                temp_thumb_path.unlink()
            except Exception:
                pass

    return False


def is_thumbnail_stale(video_path: Path, thumb_path: Path) -> bool:
    """Return True if the thumbnail is missing, empty, or older than the underlying video."""
    if not thumb_path.exists() or thumb_path.stat().st_size == 0:
        return True
    if not video_path.exists() or video_path.stat().st_size == 0:
        return False  # Cannot regenerate if video is absent; retain existing thumbnail
    return thumb_path.stat().st_mtime < video_path.stat().st_mtime


def ensure_clip_thumbnail(clip_or_id: Any, force: bool = False) -> Optional[Path]:
    """Ensure a clip has a fresh, valid thumbnail on disk.
    If the video on disk was updated, replaced, or modified after the thumbnail was generated,
    this function regenerates the thumbnail immediately and updates database records.
    Returns the Path to the fresh thumbnail, or None if no video or thumbnail is available.
    """
    if isinstance(clip_or_id, dict):
        clip = clip_or_id
    elif isinstance(clip_or_id, str):
        clip = db.get_clip_by_id(clip_or_id)
        if not clip:
            # Check if clip_or_id is a chunk
            chunk = db.get_chunk_by_id(clip_or_id)
            if chunk:
                clip = {
                    "id": clip_or_id,
                    "chunk_id": clip_or_id,
                    "duration": chunk.get("duration", 10.0),
                    "video_path": chunk.get("filepath")
                }
            else:
                return None
    else:
        return None

    clip_id = clip.get("id")
    if not clip_id:
        return None

    # Determine target thumbnail path
    existing_thumb_str = clip.get("thumbnail_path")
    if existing_thumb_str:
        candidate_path = Path(existing_thumb_str)
        if candidate_path.parent == CLIPS_DIR and candidate_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            target_thumb_path = candidate_path
        else:
            target_thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    else:
        target_thumb_path = CLIPS_DIR / f"{clip_id}.jpg"

    active_video = resolve_active_clip_video(clip)

    # If no active video is on disk (e.g. cloud-only retention or deleted source):
    if not active_video or not active_video.exists() or active_video.stat().st_size == 0:
        if target_thumb_path.exists() and target_thumb_path.stat().st_size > 0:
            return target_thumb_path
        if existing_thumb_str and Path(existing_thumb_str).exists() and Path(existing_thumb_str).stat().st_size > 0:
            return Path(existing_thumb_str)
        return None

    # Check freshness
    if not force and not is_thumbnail_stale(active_video, target_thumb_path):
        return target_thumb_path

    # Thumbnail is missing or stale -> regenerate!
    # Pick representative frame: middle of clip duration or safe offset
    duration = float(clip.get("duration") or 2.0)
    is_rendered = bool(clip.get("video_path") and Path(clip["video_path"]).exists() and Path(clip["video_path"]) == active_video)

    if is_rendered:
        thumb_time = max(0.0, min(1.0, duration / 2.0))
    else:
        start_time = float(clip.get("start_time") or 0.0)
        thumb_time = max(0.0, start_time + min(1.0, duration / 2.0))

    logger.info("Regenerating thumbnail for clip %s from %s (time: %.3fs)", clip_id, active_video.name, thumb_time)
    success = generate_thumbnail_from_video(active_video, target_thumb_path, timestamp_sec=thumb_time)
    if success and target_thumb_path.exists():
        # Update database with validated path if not already registered
        if clip.get("thumbnail_path") != str(target_thumb_path):
            try:
                db.update_clip_media(
                    clip_id=clip_id,
                    video_path=clip.get("video_path") or str(active_video),
                    thumbnail_path=str(target_thumb_path)
                )
            except Exception as e:
                logger.warning("Could not update thumbnail_path in DB for %s: %s", clip_id, e)
        return target_thumb_path

    # Fallback to existing if generation failed
    if target_thumb_path.exists() and target_thumb_path.stat().st_size > 0:
        return target_thumb_path

    return None


def get_thumbnail_version(thumb_path: Optional[Path]) -> Optional[int]:
    """Return integer timestamp representing the thumbnail version for cache busting."""
    if thumb_path and thumb_path.exists() and thumb_path.stat().st_size > 0:
        try:
            return int(thumb_path.stat().st_mtime)
        except Exception:
            return None
    return None


def populate_clip_media_urls(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure thumbnail freshness and populate versioned media URLs on clip dictionary.
    
    Guarantees:
    - Freshness: Ensures the thumbnail on disk is up to date with the underlying video.
    - Versioning: Injects ?v={mtime} into thumbnail_url and video_url so browsers/WebViews
      cache aggressively while instantly busting cache when a video or thumbnail changes.
    - Backward-compatibility: Sets standard fields (video_url, thumbnail_url, source_video_url).
    """
    clip_id = clip.get("id")
    if not clip_id:
        return clip

    # 1. Fresh thumbnail validation & generation
    thumb_path = ensure_clip_thumbnail(clip)
    if thumb_path and thumb_path.exists() and thumb_path.stat().st_size > 0:
        thumb_mtime = int(thumb_path.stat().st_mtime)
        clip["thumbnail_path"] = str(thumb_path)
        clip["thumbnail_url"] = f"/clips/{thumb_path.name}?v={thumb_mtime}"
        clip["thumbnail_version"] = thumb_mtime
    else:
        ex_thumb = clip.get("thumbnail_path")
        if ex_thumb and Path(ex_thumb).exists() and Path(ex_thumb).stat().st_size > 0:
            tp = Path(ex_thumb)
            tm = int(tp.stat().st_mtime)
            clip["thumbnail_url"] = f"/clips/{tp.name}?v={tm}"
            clip["thumbnail_version"] = tm
        else:
            clip["thumbnail_url"] = None

    # 2. Rendered video URL
    vpath = clip.get("video_path")
    if vpath:
        vp = Path(vpath)
        if vp.exists() and vp.stat().st_size > 0:
            vm = int(vp.stat().st_mtime)
            clip["video_url"] = f"/clips/{vp.name}?v={vm}"
            clip["video_version"] = vm
            clip["is_cloud_only"] = False
        else:
            clip["video_url"] = None
            clip["is_cloud_only"] = True
    else:
        clip["video_url"] = None

    # 3. Source video URL with version timestamp
    active_source = resolve_clip_source_path(clip)
    if active_source and active_source.exists():
        sm = int(active_source.stat().st_mtime)
        clip["source_video_url"] = f"/api/clips/{clip_id}/source?v={sm}"
    else:
        clip["source_video_url"] = f"/api/clips/{clip_id}/source"

    return clip
