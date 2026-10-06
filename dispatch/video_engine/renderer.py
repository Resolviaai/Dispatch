"""FFmpeg Video Renderer for Dispatch.
Cuts exact time segments, applies adaptive framing, burns Roman Hinglish subtitles,
normalizes audio, generates preview thumbnails, and updates DB status.
"""
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dispatch.config import CLIPS_DIR, PROCESSING_DIR
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles
from dispatch.video_engine.reframer import build_filter_complex
from dispatch import db

logger = logging.getLogger("dispatch.renderer")


def render_clip(
    source_video: Path,
    clip_id: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str = "16:9",
    layout_mode: str = "fit_blur",
    segments: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Render a single candidate short-form video clip.
    
    Args:
        source_video: Path to the raw video chunk.
        clip_id: Database clip ID.
        start_time: Clip start in seconds.
        end_time: Clip end in seconds.
        aspect_ratio: Source video aspect ratio ('16:9', '9:16').
        layout_mode: Framing layout ('fit_blur', 'crop_follow').
        segments: Optional transcript segments for subtitle burning.
        
    Returns:
        Dict with paths to rendered video, thumbnail, and duration.
    """
    if not source_video.exists():
        raise FileNotFoundError(f"Source video not found: {source_video}")

    duration = max(0.1, end_time - start_time)
    output_video_path = CLIPS_DIR / f"{clip_id}.mp4"
    output_thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    ass_path = PROCESSING_DIR / f"{clip_id}.ass"

    # 1. Generate ASS Subtitles if transcript segments are available
    if segments:
        try:
            generate_ass_subtitles(
                segments=segments,
                output_path=ass_path,
                clip_start_time=start_time,
                clip_end_time=end_time
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

    # 3. Construct FFmpeg command
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
        "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_video_path)
    ]

    logger.info("Executing FFmpeg render for clip %s (%.1fs - %.1fs)", clip_id, start_time, end_time)
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        error_msg = result.stderr.strip()
        logger.error("FFmpeg render failed for %s: %s", clip_id, error_msg)
        # Clean up temporary ASS file
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise RuntimeError(f"FFmpeg render failed: {error_msg}")

    # 4. Generate preview thumbnail
    thumb_time = min(1.5, duration / 2.0)
    thumb_cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(thumb_time, 3)),
        "-i", str(output_video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(output_thumb_path)
    ]
    subprocess.run(thumb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Clean up temporary ASS file
    if ass_path and ass_path.exists():
        try:
            ass_path.unlink()
        except Exception:
            pass

    # 5. Update DB
    db.update_clip_media(
        clip_id=clip_id,
        video_path=str(output_video_path),
        thumbnail_path=str(output_thumb_path) if output_thumb_path.exists() else None
    )

    logger.info("Successfully rendered clip %s -> %s", clip_id, output_video_path.name)

    return {
        "clip_id": clip_id,
        "video_path": output_video_path,
        "thumbnail_path": output_thumb_path if output_thumb_path.exists() else None,
        "duration": duration
    }
