"""Video validation, metadata extraction, and automatic orientation normalization using ffprobe and ffmpeg."""
import json
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("dispatch.ingestion.validator")


def calculate_file_hash(filepath: Path, block_size: int = 65536) -> str:
    """Calculate MD5 checksum of a file for fast deduplication and integrity check."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def inspect_video_orientation(filepath: Path) -> Dict[str, Any]:
    """Inspect raw video dimensions and container rotation metadata via ffprobe."""
    if not filepath.exists() or filepath.stat().st_size == 0:
        return {"valid": False, "error": "File does not exist or is empty"}

    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(filepath)
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode != 0:
            return {"valid": False, "error": f"ffprobe error: {result.stderr.strip()}"}

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            return {"valid": False, "error": "No video stream found in file"}

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        # Check for rotation in metadata (tags and side_data_list)
        tags = video_stream.get("tags", {})
        side_data = video_stream.get("side_data_list", [])
        rotation = 0
        if "rotate" in tags:
            try:
                rotation = int(tags["rotate"])
            except ValueError:
                pass
        for sd in side_data:
            if "rotation" in sd:
                try:
                    rotation = int(sd["rotation"])
                except ValueError:
                    pass

        norm_rotation = rotation % 360
        if norm_rotation < 0:
            norm_rotation += 360

        if norm_rotation in (90, 270):
            effective_width = height
            effective_height = width
        else:
            effective_width = width
            effective_height = height

        orientation = "portrait" if effective_height >= effective_width else "landscape"
        duration = float(format_info.get("duration") or video_stream.get("duration") or 0.0)

        return {
            "valid": True,
            "width": width,
            "height": height,
            "rotation": rotation,
            "norm_rotation": norm_rotation,
            "effective_width": effective_width,
            "effective_height": effective_height,
            "orientation": orientation,
            "duration": duration,
            "has_audio": audio_stream is not None,
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
            "size_bytes": filepath.stat().st_size
        }

    except Exception as e:
        return {"valid": False, "error": str(e)}


def normalize_video_orientation(
    filepath: Path,
    chunk_id: Optional[str] = None
) -> Tuple[bool, Path, Dict[str, Any]]:
    """Deterministically normalize physical pixel orientation.
    If the video contains rotation metadata (e.g. 90, 180, 270 degrees from mobile recording),
    transcodes once to physically upright orientation with rotate=0 metadata, so all downstream
    processors (Whisper, highlight finder, ffmpeg filter complex, web video players) receive
    physically upright frames without orientation drift.

    Logs:
        MEDIA_ORIENTATION_DETECTED
        MEDIA_ORIENTATION_NORMALIZED
    """
    cid = chunk_id or filepath.stem
    info = inspect_video_orientation(filepath)
    if not info.get("valid"):
        logger.warning("Cannot normalize orientation for %s: %s", cid, info.get("error"))
        return False, filepath, info

    width = info["width"]
    height = info["height"]
    rotation = info["rotation"]
    norm_rotation = info["norm_rotation"]
    orientation = info["orientation"]

    logger.info(
        "MEDIA_ORIENTATION_DETECTED: chunk_id=%s, encoded=%dx%d, rotation=%d, orientation=%s",
        cid, width, height, rotation, orientation
    )

    if norm_rotation == 0:
        # Already physically upright, no transcode needed
        logger.info(
            "MEDIA_ORIENTATION_NORMALIZED: chunk_id=%s, input=%dx%d, output=%dx%d (already upright, skipped)",
            cid, width, height, width, height
        )
        return True, filepath, info

    # Transcode to physically upright video with rotate=0
    temp_norm_path = filepath.with_name(f"{filepath.stem}_norm_temp.mp4")
    norm_cmd = [
        "ffmpeg", "-y",
        "-i", str(filepath),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-metadata:s:v:0", "rotate=0",
        "-c:a", "copy",
        str(temp_norm_path)
    ]

    try:
        res = subprocess.run(norm_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        if res.returncode != 0 or not temp_norm_path.exists() or temp_norm_path.stat().st_size == 0:
            logger.error("FFmpeg orientation normalization failed for %s: %s", cid, res.stderr.strip()[:300])
            if temp_norm_path.exists():
                temp_norm_path.unlink(missing_ok=True)
            return False, filepath, info

        # Replace original file atomically
        temp_norm_path.replace(filepath)

        # Re-inspect to confirm normalized dimensions
        new_info = inspect_video_orientation(filepath)
        new_width = new_info.get("width", info["effective_width"])
        new_height = new_info.get("height", info["effective_height"])

        logger.info(
            "MEDIA_ORIENTATION_NORMALIZED: chunk_id=%s, input=%dx%d, output=%dx%d",
            cid, width, height, new_width, new_height
        )
        return True, filepath, new_info

    except Exception as e:
        logger.error("Exception during orientation normalization for %s: %s", cid, e)
        if temp_norm_path.exists():
            temp_norm_path.unlink(missing_ok=True)
        return False, filepath, info


def probe_video(
    filepath: Path,
    auto_normalize: bool = True,
    chunk_id: Optional[str] = None
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Probe video file using ffprobe and normalize physical orientation if needed.
    Returns:
        (is_valid, metadata_dict, error_message)
    """
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False, {}, "File does not exist or is empty"

    # 1. Orientation normalization if video has non-zero rotation metadata
    if auto_normalize:
        norm_success, filepath, _ = normalize_video_orientation(filepath, chunk_id=chunk_id)

    # 2. Extract final probed metadata
    info = inspect_video_orientation(filepath)
    if not info.get("valid"):
        return False, {}, info.get("error", "Failed to probe video")

    duration = info["duration"]
    effective_width = info["effective_width"]
    effective_height = info["effective_height"]

    if duration <= 0.0:
        return False, {}, f"Invalid video duration: {duration}s (must be > 0)"

    if effective_width <= 0 or effective_height <= 0:
        return False, {}, f"Invalid dimensions: {effective_width}x{effective_height}"

    # 3. Real FFmpeg decode validation: verify decodable frames
    decode_cmd = [
        "ffmpeg",
        "-v", "error",
        "-i", str(filepath),
        "-f", "null",
        "-"
    ]
    try:
        decode_res = subprocess.run(decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if decode_res.returncode != 0:
            return False, {}, f"FFmpeg decode verification failed: {decode_res.stderr.strip()[:200]}"
    except subprocess.TimeoutExpired:
        return False, {}, "FFmpeg decode verification timed out"

    # 4. Detect canonical aspect ratio
    ratio_val = effective_width / effective_height
    if 0.5 <= ratio_val <= 0.65:
        aspect_ratio = "9:16"  # Portrait
    elif 1.6 <= ratio_val <= 1.85:
        aspect_ratio = "16:9"  # Landscape
    elif ratio_val < 1.0:
        aspect_ratio = "vertical_other"
    else:
        aspect_ratio = "horizontal_other"

    metadata = {
        "duration": duration,
        "width": effective_width,
        "height": effective_height,
        "aspect_ratio": aspect_ratio,
        "has_audio": info["has_audio"],
        "video_codec": info["video_codec"],
        "audio_codec": info["audio_codec"],
        "size_bytes": filepath.stat().st_size
    }

    return True, metadata, None
