"""Video validation and metadata extraction using ffprobe."""
import json
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


def calculate_file_hash(filepath: Path, block_size: int = 65536) -> str:
    """Calculate MD5 checksum of a file for fast deduplication and integrity check."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def probe_video(filepath: Path) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Probe video file using ffprobe.
    Returns:
        (is_valid, metadata_dict, error_message)
    """
    if not filepath.exists() or filepath.stat().st_size == 0:
        return False, {}, "File does not exist or is empty"

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
            return False, {}, f"ffprobe error: {result.stderr.strip()}"

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            return False, {}, "No video stream found in file"

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        # Check for rotation in metadata (e.g., recorded on mobile phone)
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
                rotation = int(sd["rotation"])

        # If rotated 90 or 270 degrees, swap width and height
        if abs(rotation) in (90, 270):
            effective_width = height
            effective_height = width
        else:
            effective_width = width
            effective_height = height

        duration = float(format_info.get("duration") or video_stream.get("duration") or 0.0)
        if duration <= 0.0:
            return False, {}, f"Invalid video duration: {duration}s (must be > 0)"

        if effective_width <= 0 or effective_height <= 0:
            return False, {}, f"Invalid dimensions: {effective_width}x{effective_height}"

        # Real FFmpeg decode validation: ffprobe inspects containers, but ffmpeg -f null verifies decodable frames
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

        # Detect aspect ratio
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
            "has_audio": audio_stream is not None,
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
            "size_bytes": filepath.stat().st_size
        }

        return True, metadata, None

    except subprocess.TimeoutExpired:
        return False, {}, "ffprobe probe timed out"
    except Exception as e:
        return False, {}, f"Probe exception: {str(e)}"
