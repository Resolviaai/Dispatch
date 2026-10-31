"""dispatch.video_engine.filmstrip
Time-anchored filmstrip generation for the clip-editor timeline.

Design invariants (see also dispatch.video_engine.thumbnail):
- The filmstrip is a CACHE ARTIFACT: one directory per clip that either fully
  matches the current video or does not exist at all. Consumers never read a
  partially built or internally inconsistent directory.
- A single sampling plan derived from the clip's duration defines the slot
  geometry on BOTH the backend (where frames are sampled) and the frontend
  (where slots are placed). The API payload is self-describing: every frame
  carries its clip-local slot start time `t`, and `interval` carries the slot
  span, so no consumer ever guesses.
- Frame sampling always targets the ACTIVE video in ITS OWN coordinate
  system: a rendered video is sampled in local time [0, duration]; a raw
  source video is sampled at [start_time, start_time + duration] exactly like
  ensure_clip_thumbnail() does for single thumbnails.
- Freshness is defined by a manifest (content fingerprint + nanosecond mtime
  + duration + plan). Any drift (video replaced, duration changed, plan
  changed, stray files, missing frames) forces a full regeneration into a
  staging directory followed by an atomic swap. Incremental mixing of old and
  new frames is impossible by construction.
"""
import json
import logging
import math
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dispatch import db
from dispatch.config import CLIPS_DIR
from dispatch.video_engine.thumbnail import (
    ensure_clip_thumbnail,
    get_file_fingerprint,
    resolve_active_clip_video,
)

logger = logging.getLogger("dispatch.video_engine.filmstrip")

# The sampling plan: one frame slot per TARGET_INTERVAL seconds, clamped so a
# long clip never explodes the per-open extraction cost and a short clip never
# collapses into a single unreadable sliver.
FILMSTRIP_TARGET_INTERVAL = 2.5
FILMSTRIP_MIN_COUNT = 8
FILMSTRIP_MAX_COUNT = 16
FILMSTRIP_EXTRACT_WIDTH = 480
FILMSTRIP_MANIFEST_VERSION = 2

# Concurrency locks per clip_id (threading, mirroring thumbnail.py; the web
# layer additionally holds its own asyncio lock around the to_thread call).
_filmstrip_locks: Dict[str, threading.Lock] = {}
_filmstrip_locks_guard = threading.Lock()


def get_filmstrip_thread_lock(clip_id: str) -> threading.Lock:
    """Retrieve or create a thread lock dedicated to a specific clip ID."""
    with _filmstrip_locks_guard:
        if clip_id not in _filmstrip_locks:
            _filmstrip_locks[clip_id] = threading.Lock()
        return _filmstrip_locks[clip_id]


def plan_filmstrip(duration: float) -> Tuple[float, int]:
    """Derive the definitive slot geometry for a clip duration.

    Returns (interval, count): count slots span exactly [0, duration] with
    interval = duration / count, so the frontend can place slot i at
    t = i * interval and width = interval without any shared rounding.
    """
    dur = float(duration or 0.0)
    if dur <= 0 or not math.isfinite(dur):
        raise ValueError("duration must be a positive finite number")
    count = max(
        FILMSTRIP_MIN_COUNT,
        min(FILMSTRIP_MAX_COUNT, int(math.ceil(dur / FILMSTRIP_TARGET_INTERVAL))),
    )
    interval = dur / count
    return interval, count


def sample_axis_times(scope: str, source_start: float, interval: float, count: int) -> List[float]:
    """Mid-slot times in the ACTIVE video's own coordinate system.

    scope "render"  -> the active video is the rendered clip: local [0, dur].
    scope "source"  -> the active video is the raw source: the clip window is
                       [source_start, source_start + duration] exactly as the
                       thumbnail engine treats it.
    """
    offset = float(source_start or 0.0)
    if scope != "source":
        offset = 0.0
    return [offset + (i + 0.5) * interval for i in range(max(0, int(count)))]


def filmstrip_dir(clip_id: str) -> Path:
    return CLIPS_DIR / "filmstrip_cache" / str(clip_id)


def read_manifest_directory(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = (path / "manifest.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def is_manifest_current(
    manifest: Optional[Dict[str, Any]],
    *,
    cache_dir: Path,
    video_fingerprint: str,
    video_mtime_ns: int,
    duration: float,
    interval: float,
    count: int,
    scope: str = "none",
    source_start: float = 0.0,
) -> bool:
    """True only when the cache artifact is byte-for-byte plan-consistent.

    Any drift invalidates the whole artifact: the manifest must declare the
    same plan (duration/interval/count), the same video content (fingerprint
    AND mtime), every declared frame must exist on disk, and no frame file
    outside the manifest may exist (kills leftover/s_partial generations).
    """
    if not isinstance(manifest, dict):
        return False
    if manifest.get("version") != FILMSTRIP_MANIFEST_VERSION:
        return False
    if manifest.get("video_fingerprint") != video_fingerprint:
        return False
    try:
        if int(manifest.get("video_mtime_ns", -1)) != int(video_mtime_ns):
            return False
        if abs(float(manifest.get("duration", -1.0)) - duration) > 1e-6:
            return False
        if abs(float(manifest.get("interval", -1.0)) - interval) > 1e-6:
            return False
        if int(manifest.get("count", -1)) != int(count):
            return False
        if manifest.get("scope") != scope:
            return False
        if scope == "source":
            if abs(float(manifest.get("source_start", -999.0)) - source_start) > 1e-4:
                return False
    except (TypeError, ValueError):
        return False

    frames = manifest.get("frames")
    if not isinstance(frames, list):
        return False

    try:
        declared: Dict[str, Any] = {}
        for frame in frames:
            if not isinstance(frame, dict):
                return False
            name = frame.get("name")
            slot_t = float(frame.get("t", -1.0))
            slot_index = frame.get("index")
            if not isinstance(name, str) or not name:
                return False
            if not 0.0 <= slot_t < duration + 1e-9:
                return False
            if not isinstance(slot_index, int) or not 0 <= slot_index < count:
                return False
            declared[name] = frame.copy()
            frame_file = cache_dir / name
            if not frame_file.exists() or frame_file.stat().st_size <= 0:
                return False
            # Slot time must equal index * interval (plan equality).
            if abs(slot_t - slot_index * interval) > 1e-6:
                return False

        for disk_file in cache_dir.glob("f*.jpg"):
            if disk_file.name not in declared:
                return False
    except Exception:
        return False

    return True


def _atomic_swap_directory(staging: Path, destination: Path) -> bool:
    """Replace `destination` directory with `staging` as near-atomically as
    directory renames allow. Tombstoning keeps readers from ever seeing a
    half-populated tree; failures clean up both sides."""
    tombstone: Optional[Path] = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            tombstone = destination.parent / f".{destination.name}.tomb.{uuid.uuid4().hex[:8]}"
            destination.rename(tombstone)
        os.replace(str(staging), str(destination))
        return True
    except Exception as e:
        logger.debug("Filmstrip swap failed for %s: %s", destination, e)
        # Restore in reverse order so a pre-existing cache is not lost.
        if tombstone is not None and tombstone.exists():
            try:
                if destination.exists():
                    _remove_tree(destination)
                tombstone.rename(destination)
            except Exception:
                pass
        _remove_tree(staging)
        return False
    finally:
        if tombstone is not None and tombstone.exists():
            _remove_tree(tombstone)


def _remove_tree(path: Path) -> None:
    try:
        import shutil

        if path.exists():
            shutil.rmtree(str(path), ignore_errors=True)
    except Exception:
        pass


def _extract_slot(
    active_video: Path,
    axis_time: float,
    out_path: Path,
) -> bool:
    """Extract exactly one frame at `axis_time` into `out_path` atomically."""
    import subprocess

    staging_name = f".staging_{uuid.uuid4().hex[:8]}_{out_path.name}"
    tmp_path = out_path.parent / staging_name
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{max(0.0, axis_time):.4f}",
            "-i", str(active_video),
            "-frames:v", "1",
            "-q:v", "3",
            "-vf", f"scale={FILMSTRIP_EXTRACT_WIDTH}:-2",
            str(tmp_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
            os.replace(str(tmp_path), str(out_path))
            return True
        return False
    except Exception as e:
        logger.debug("Filmstrip slot extraction failed at %.3fs for %s: %s", axis_time, active_video, e)
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def sweep_legacy_filmstrip_files(clip_id: str) -> None:
    """Delete the pre-manifest flat-frame artifacts ({clip_id}_frame_*.jpg*).

    The flat layout could store an uneven index set (generation once wrote 8,
    early scans looked for 12), which is how mixed-era frames leaked into
    responses. All history now lives in filmstrip_cache/<clip_id>/.
    """
    for legacy in CLIPS_DIR.glob(f"{clip_id}_frame_*.jpg*"):
        try:
            legacy.unlink()
        except Exception as e:
            logger.debug("Could not remove legacy filmstrip file %s: %s", legacy, e)


def generate_filmstrip(
    *,
    clip_id: str,
    clip: Dict[str, Any],
    active_video: Path,
    video_fingerprint: str,
    video_mtime_ns: int,
) -> Optional[Dict[str, Any]]:
    """Build the filmstrip artifact for the current video and return its manifest.

    Never returns a partially-built state: either the final directory exists
    with a manifest that satisfies is_manifest_current(), or None.
    """
    duration = float(clip.get("duration") or 0.0)
    if duration <= 0 or not math.isfinite(duration):
        return None

    is_rendered = bool(
        clip.get("video_path")
        and Path(clip["video_path"]).exists()
        and Path(clip["video_path"]) == active_video
    ) or (duration > 0 and active_video == CLIPS_DIR / f"{clip_id}.mp4")
    scope = "render" if is_rendered else "source"
    source_start = 0.0 if is_rendered else float(clip.get("start_time") or 0.0)

    interval, count = plan_filmstrip(duration)
    axis_times = sample_axis_times(scope, source_start, interval, count)

    cache_dir = filmstrip_dir(clip_id)
    staging = cache_dir.parent / f"{cache_dir.name}.staging.{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True, exist_ok=True)

        manifest_frames: List[Dict[str, Any]] = []
        for index, axis_time in enumerate(axis_times):
            slot_t = round(index * interval, 6)
            frame_name = f"f{index:02d}.jpg"
            out_path = staging / frame_name
            if not _extract_slot(active_video, axis_time, out_path):
                continue  # gap tolerated; the manifest records only real frames
            manifest_frames.append({
                "name": frame_name,
                "index": index,
                "t": slot_t,
            })

        manifest = {
            "version": FILMSTRIP_MANIFEST_VERSION,
            "clip_id": clip_id,
            "video_path": str(active_video.resolve()),
            "video_fingerprint": video_fingerprint,
            "video_mtime_ns": video_mtime_ns,
            "duration": duration,
            "interval": interval,
            "count": count,
            "scope": scope,
            "source_start": source_start,
            "frames": manifest_frames,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        if not manifest_frames:
            # Nothing extracted: no artifact, no cache, honest failure.
            _remove_tree(staging)
            return None

        if not _atomic_swap_directory(staging, cache_dir):
            return None

        return manifest
    except Exception as e:
        logger.debug("Filmstrip generation failed for %s: %s", clip_id, e)
        _remove_tree(staging)
        return None
    finally:
        _remove_tree(staging)


def load_filmstrip_payload(clip: Dict[str, Any], clip_id: str) -> Dict[str, Any]:
    """Return the definitive, self-describing filmstrip payload for a clip.

    Guarantees under the hit/miss logic:
    - Fresh, plan-consistent cache  -> served directly.
    - Drift or missing artifact     -> regenerated ONCE (thread-safe, atomic),
      then served.
    - No video / failed generation  -> single-thumbnail slot with the clip's
      real duration, so the timeline always has *some* honest render.
    """
    duration = float(clip.get("duration") or 0.0)
    duration = duration if (duration > 0 and math.isfinite(duration)) else 0.0

    # Legacy hygiene on EVERY load (hit or miss): flat pre-manifest artifacts
    # are never authoritative, so they must not survive even a cache hit.
    sweep_legacy_filmstrip_files(clip_id)

    def _empty_response() -> Dict[str, Any]:
        interval, count = (0.0, 0) if duration <= 0 else plan_filmstrip(duration)
        if duration <= 0:
            return {
                "clip_id": clip_id,
                "duration": duration,
                "interval": 0.0,
                "count": 0,
                "scope": "none",
                "source_start": 0.0,
                "frames": [],
            }
        return {
            "clip_id": clip_id,
            "duration": duration,
            "interval": interval,
            "count": count,
            "scope": "none",
            "source_start": 0.0,
            "frames": [],
        }

    def _thumbnail_response(thumb: Optional[Path]) -> Dict[str, Any]:
        if not thumb or not thumb.exists() or thumb.stat().st_size <= 0 or duration <= 0:
            return _empty_response()
        thumb_mtime_ns = getattr(thumb.stat(), "st_mtime_ns", int(thumb.stat().st_mtime * 1e9))
        return {
            "clip_id": clip_id,
            "duration": duration,
            "interval": duration,  # a single frame legitimately represents the whole run
            "count": 1,
            "scope": "render",
            "source_start": 0.0,
            "frames": [{
                "url": f"/clips/{thumb.name}?v={thumb_mtime_ns}",
                "t": 0.0,
            }],
        }

    if duration <= 0:
        return _empty_response()

    active_video = resolve_active_clip_video(clip)
    if not active_video or not active_video.exists() or active_video.stat().st_size == 0:
        try:
            return _thumbnail_response(ensure_clip_thumbnail(clip))
        except Exception as e:
            logger.debug("Thumbnail fallback failed for filmstrip of %s: %s", clip_id, e)
            return _empty_response()

    video_mtime_ns = getattr(active_video.stat(), "st_mtime_ns", int(active_video.stat().st_mtime * 1e9))
    video_fingerprint = get_file_fingerprint(active_video)
    interval, count = plan_filmstrip(duration)
    cache_dir = filmstrip_dir(clip_id)

    is_rendered = bool(
        clip.get("video_path")
        and Path(clip["video_path"]).exists()
        and Path(clip["video_path"]) == active_video
    ) or (duration > 0 and active_video == CLIPS_DIR / f"{clip_id}.mp4")
    scope = "render" if is_rendered else "source"
    source_start = 0.0 if is_rendered else float(clip.get("start_time") or 0.0)

    lock = get_filmstrip_thread_lock(clip_id)
    with lock:
        manifest = read_manifest_directory(cache_dir)
        if is_manifest_current(
            manifest,
            cache_dir=cache_dir,
            video_fingerprint=video_fingerprint,
            video_mtime_ns=video_mtime_ns,
            duration=duration,
            interval=interval,
            count=count,
            scope=scope,
            source_start=source_start,
        ):
            regenerated = False
        else:
            regenerated = True
            try_result = generate_filmstrip(
                clip_id=clip_id,
                clip=clip,
                active_video=active_video,
                video_fingerprint=video_fingerprint,
                video_mtime_ns=video_mtime_ns,
            )
            if try_result is None:
                # Keep a previously valid manifest if a full rebuild failed but
                # the old artifact was internally consistent (never impossible
                # here since we already judged it stale, so degrade to thumb).
                try:
                    return _thumbnail_response(ensure_clip_thumbnail(clip))
                except Exception as e:
                    logger.debug("Thumbnail fallback after failed filmstrip regeneration for %s: %s", clip_id, e)
                return _empty_response()
            manifest = try_result

        frames_payload: List[Dict[str, Any]] = []
        for frame in manifest.get("frames", []):
            name = frame.get("name")
            slot_index = int(frame.get("index", -1))
            slot_t = float(frame.get("t", -1.0))
            frame_file = cache_dir / str(name)
            if not frame_file.exists() or frame_file.stat().st_size <= 0:
                continue
            file_mtime_ns = getattr(frame_file.stat(), "st_mtime_ns", int(frame_file.stat().st_mtime * 1e9))
            slot_span = max(1e-6, min(float(manifest["interval"]), max(0.0, float(manifest["duration"]) - slot_t)))
            frames_payload.append({
                "url": f"/clips/filmstrip_cache/{clip_id}/{name}?v={file_mtime_ns}_{slot_index}",
                "t": slot_t,
                # span is informational; interval remains the canonical width
                "span": round(slot_span, 6),
            })

        return {
            "clip_id": clip_id,
            "duration": duration,
            "interval": float(manifest["interval"]),
            "count": int(manifest["count"]),
            "scope": manifest.get("scope", "none"),
            "source_start": float(manifest.get("source_start", 0.0) or 0.0),
            "regenerated": regenerated,
            "frames": frames_payload,
        }
