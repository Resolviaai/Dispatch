"""FFmpeg Video Renderer for Dispatch.
Cuts exact time segments, applies adaptive framing, burns Roman Hinglish subtitles,
normalizes audio, generates preview thumbnails, and updates DB status.
"""
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dispatch.config import CLIPS_DIR, PROCESSING_DIR, MAX_CLIP_DURATION, MIN_CLIP_DURATION
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles
from dispatch.video_engine.reframer import build_filter_complex
from dispatch import db

logger = logging.getLogger("dispatch.renderer")


def render_clip(
    source_video: Path,
    clip_id: str,
    start_time: float,
    end_time: float,
    aspect_ratio: Optional[str] = None,
    layout_mode: str = "fit_blur",
    segments: Optional[List[Dict[str, Any]]] = None,
    subtitle_options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Render a single candidate short-form video clip.
    
    Args:
        source_video: Path to the raw video chunk.
        clip_id: Database clip ID.
        start_time: Clip start in seconds.
        end_time: Clip end in seconds.
        aspect_ratio: Source video aspect ratio ('16:9', '9:16', 'portrait', 'landscape').
        layout_mode: Framing layout.
        segments: Optional transcript segments for subtitle burning.
        subtitle_options: Optional custom typography & styling options.
        
    Returns:
        Dict with paths to rendered video, thumbnail, and duration.
    """
    if not source_video.exists():
        raise FileNotFoundError(f"Source video not found: {source_video}")

    if not aspect_ratio or aspect_ratio == "auto":
        from dispatch.ingestion.validator import inspect_video_orientation
        info = inspect_video_orientation(source_video)
        if info.get("valid"):
            aspect_ratio = "9:16" if info.get("orientation") == "portrait" else "16:9"
        else:
            aspect_ratio = "16:9"

    # Enforce duration constraints (hard cap 180s, min technical interval)
    if (end_time - start_time) > MAX_CLIP_DURATION:
        logger.warning("Clamping clip %s duration from %.2fs to MAX_CLIP_DURATION (%.1fs)",
                       clip_id, end_time - start_time, MAX_CLIP_DURATION)
        end_time = start_time + MAX_CLIP_DURATION
    duration = max(0.1, round(end_time - start_time, 3))
    output_video_path = CLIPS_DIR / f"{clip_id}.mp4"
    output_thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    ass_path = PROCESSING_DIR / f"{clip_id}.ass"

    # 1. Generate CapCut-grade ASS Subtitles if transcript segments are available
    sub_opts = subtitle_options or {}
    if segments and sub_opts.get("enabled", True):
        try:
            generate_ass_subtitles(
                segments=segments,
                output_path=ass_path,
                clip_start_time=start_time,
                clip_end_time=end_time,
                font_name=sub_opts.get("font_name", "Arial Black"),
                font_size=sub_opts.get("font_size", 54),
                style_preset=sub_opts.get("style_preset", "yellow_pop"),
                position=sub_opts.get("position", "bottom"),
                enable_pop_bounce=sub_opts.get("enable_pop_bounce", True),
                words_per_card=sub_opts.get("words_per_card", 3)
            )
        except Exception as e:
            logger.warning("Failed to generate subtitles: %s", e)
            ass_path = None
    else:
        ass_path = None

    # 2. Build filter complex
    filter_complex_str, final_v_label = build_filter_complex(
        aspect_ratio=aspect_ratio,
        layout_mode=layout_mode,
        ass_subtitle_path=ass_path if (ass_path and ass_path.exists()) else None
    )

    temp_video_path = CLIPS_DIR / f"{clip_id}.tmp.mp4"
    temp_thumb_path = CLIPS_DIR / f"{clip_id}.tmp.jpg"

    # 3. Construct FFmpeg command with restricted threads to preserve desktop responsiveness
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(start_time, 3)),
        "-to", str(round(end_time, 3)),
        "-i", str(source_video),
        "-filter_complex", filter_complex_str,
        "-map", final_v_label,
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-threads", "2",
        "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(temp_video_path)
    ]

    logger.info("Executing FFmpeg atomic render for clip %s (%.1fs - %.1fs)", clip_id, start_time, end_time)
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        error_msg = result.stderr.strip()
        logger.error("FFmpeg render failed for %s: %s", clip_id, error_msg)
        if temp_video_path.exists():
            temp_video_path.unlink()
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise RuntimeError(f"FFmpeg render failed: {error_msg}")

    # Atomic rename: guarantees system never exposes an unplayable partial video
    temp_video_path.replace(output_video_path)

    # 4. Generate preview thumbnail atomically
    thumb_time = max(0.0, min(1.0, duration / 2.0))
    thumb_cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(thumb_time, 3)),
        "-i", str(output_video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(temp_thumb_path)
    ]
    thumb_res = subprocess.run(thumb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if thumb_res.returncode == 0 and temp_thumb_path.exists():
        temp_thumb_path.replace(output_thumb_path)
    elif temp_thumb_path.exists():
        temp_thumb_path.unlink()

    # Clean up temporary ASS file
    if ass_path and ass_path.exists():
        try:
            ass_path.unlink()
        except Exception:
            pass

    # 5. Update DB with verified rendered layout
    db.update_clip_media(
        clip_id=clip_id,
        video_path=str(output_video_path),
        thumbnail_path=str(output_thumb_path) if output_thumb_path.exists() else None,
        rendered_layout_mode=layout_mode
    )

    logger.info("Successfully rendered clip %s -> %s", clip_id, output_video_path.name)

    return {
        "clip_id": clip_id,
        "video_path": output_video_path,
        "thumbnail_path": output_thumb_path if output_thumb_path.exists() else None,
        "duration": duration
    }
