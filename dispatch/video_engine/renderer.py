"""FFmpeg Video Renderer for Dispatch.
Cuts exact time segments, applies dead-air silence removal with frame-accurate
subtitle synchronization, applies adaptive framing, burns animated subtitles,
normalizes audio, generates preview thumbnails, and enforces strict duration limits.
"""
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dispatch.config import CLIPS_DIR, PROCESSING_DIR, MAX_CLIP_DURATION, MIN_CLIP_DURATION
from dispatch.video_engine.subtitle_generator import generate_ass_subtitles
from dispatch.video_engine.reframer import build_filter_complex
from dispatch.video_engine.silence import (
    analyze_silence_gaps,
    remap_timestamps_for_silence_cuts,
    build_silence_keep_intervals
)
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
    subtitle_options: Optional[Dict[str, Any]] = None,
    expected_range_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Render a single candidate short-form video clip.
    
    Invariants:
      1. Minimum final duration: 5.0 seconds.
      2. Maximum final duration: 180.0 seconds (inclusive).
      3. Automatically removes dead-air silences (>= 3.0s) while preserving conversational
         cadence, thinking pauses, dramatic tension, and comedic timing.
      4. Subtitle timestamps remain frame-accurate and strictly synchronized after silence removal.
      5. Final output file is probed; if duration is outside [5.0s, 180.0s], the clip is
         rejected and unlinked before downstream processing or publishing.
    """
    if not source_video.exists():
        raise FileNotFoundError(f"Source video not found: {source_video}")

    clip_at_start = db.get_clip_by_id(clip_id)
    render_range_revision = expected_range_revision
    if render_range_revision is None and clip_at_start:
        render_range_revision = int(clip_at_start.get("range_revision") or 0)

    from dispatch.ingestion.validator import inspect_video_orientation
    info = inspect_video_orientation(source_video)
    if not aspect_ratio or aspect_ratio == "auto":
        if info.get("valid"):
            aspect_ratio = "9:16" if info.get("orientation") == "portrait" else "16:9"
        else:
            aspect_ratio = "16:9"

    has_audio = info.get("has_audio", True)

    # Candidate/editor renders preserve source timing so the browser caption overlay and
    # approved render share the same clip-relative timeline.
    sub_opts = subtitle_options or {}
    if sub_opts.get("preserve_timing"):
        eff_duration, cuts = max(0.0, end_time - start_time), []
    else:
        eff_duration, cuts = analyze_silence_gaps(start_time, end_time, segments)
    has_silence_cuts = bool(cuts)

    output_video_path = CLIPS_DIR / f"{clip_id}.mp4"
    output_thumb_path = CLIPS_DIR / f"{clip_id}.jpg"
    ass_path = PROCESSING_DIR / f"{clip_id}.ass"
    temp_video_path = CLIPS_DIR / f"{clip_id}.tmp.mp4"
    temp_thumb_path = CLIPS_DIR / f"{clip_id}.tmp.jpg"

    # 2. Subtitle generation with synchronized timeline
    if sub_opts.get("enabled", True):
        captured_track = sub_opts.get("caption_track")
        existing_cap_json = db.get_clip_caption_data(clip_id) if captured_track is None else None
        if captured_track is not None and not has_silence_cuts:
            try:
                from dispatch.captions.model import CaptionTrack
                from dispatch.captions.exporter import export_to_ass
                track = CaptionTrack.from_dict(captured_track)
                export_to_ass(track, ass_path)
            except Exception as e:
                logger.warning("Failed to export captured CaptionTrack for clip %s: %s", clip_id, e)
                ass_path.unlink(missing_ok=True)
                raise RuntimeError(f"Final caption export failed: {e}") from e
        elif existing_cap_json and not has_silence_cuts:
            try:
                import json
                from dispatch.captions.model import CaptionTrack
                from dispatch.captions.exporter import export_to_ass
                track = CaptionTrack.from_dict(json.loads(existing_cap_json))
                export_to_ass(track, ass_path)
            except Exception as e:
                logger.warning("Failed to export existing CaptionTrack for clip %s: %s", clip_id, e)
                ass_path = None
        elif segments:
            # Remap subtitle timestamps if silence cuts occurred
            active_segments = remap_timestamps_for_silence_cuts(segments, start_time, end_time, cuts) if has_silence_cuts else segments
            try:
                import json
                from dispatch.captions import group_words_into_captions, export_to_ass
                from dispatch.captions.presets import get_default_preset_id
                track = group_words_into_captions(
                    raw_segments=active_segments,
                    clip_start=start_time,
                    clip_end=start_time + eff_duration if has_silence_cuts else end_time,
                    clip_id=clip_id,
                    preset_id=sub_opts.get("style_preset") or get_default_preset_id()
                )
                export_to_ass(track, ass_path)
                saved = db.save_clip_caption_data(
                    clip_id, json.dumps(track.to_dict()),
                    expected_range_revision=render_range_revision,
                    source_range_start=start_time,
                    source_range_end=end_time,
                )
                if render_range_revision is not None and not saved:
                    raise RuntimeError("Clip range changed while captions were being rendered")
            except Exception as e:
                logger.warning("Failed to generate subtitles via CaptionTrack: %s", e)
                ass_path = None
        else:
            ass_path = None
    else:
        ass_path = None

    # 3. Construct FFmpeg command
    keep_intervals = build_silence_keep_intervals(start_time, end_time, cuts) if has_silence_cuts else []

    if has_silence_cuts and len(keep_intervals) > 1:
        # Complex filter: trim and concatenate speech blocks, then apply framing and subtitles
        trim_filters = []
        concat_in = []
        for idx, (k_s, k_e) in enumerate(keep_intervals):
            trim_filters.append(f"[0:v]trim=start={k_s:.3f}:end={k_e:.3f},setpts=PTS-STARTPTS[v_trim_{idx}]")
            if has_audio:
                trim_filters.append(f"[0:a]atrim=start={k_s:.3f}:end={k_e:.3f},asetpts=PTS-STARTPTS[a_trim_{idx}]")
                concat_in.append(f"[v_trim_{idx}][a_trim_{idx}]")
            else:
                concat_in.append(f"[v_trim_{idx}]")

        a_cnt = 1 if has_audio else 0
        concat_filter = f"{''.join(concat_in)}concat=n={len(keep_intervals)}:v=1:a={a_cnt}[concat_v]" + ("[concat_a]" if has_audio else "")
        trim_filters.append(concat_filter)

        framing_filter, final_v_label = build_filter_complex(
            aspect_ratio=aspect_ratio,
            layout_mode=layout_mode,
            ass_subtitle_path=ass_path if (ass_path and ass_path.exists()) else None,
            input_v_label="[concat_v]"
        )

        full_filter_complex = ";".join(trim_filters) + ";" + framing_filter

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_video),
            "-filter_complex", full_filter_complex,
            "-map", final_v_label,
        ]
        if has_audio:
            cmd.extend([
                "-map", "[concat_a]",
                "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
                "-c:a", "aac",
                "-b:a", "192k",
            ])
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-threads", "2",
            "-movflags", "+faststart",
            str(temp_video_path)
        ])
    else:
        # Standard contiguous segment render
        filter_complex_str, final_v_label = build_filter_complex(
            aspect_ratio=aspect_ratio,
            layout_mode=layout_mode,
            ass_subtitle_path=ass_path if (ass_path and ass_path.exists()) else None,
            input_v_label="[0:v]"
        )

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

    logger.info("Executing FFmpeg atomic render for clip %s (%.1fs - %.1fs, effective: %.1fs)",
                clip_id, start_time, end_time, eff_duration)
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=300)

    if result.returncode != 0:
        error_msg = result.stderr.strip()
        logger.error("FFmpeg render failed for %s: %s", clip_id, error_msg)
        if temp_video_path.exists():
            temp_video_path.unlink()
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise RuntimeError(f"FFmpeg render failed: {error_msg}")

    # 4. Mandatory Duration Verification on Final Output Video
    from dispatch.ingestion.validator import probe_video
    is_valid, meta, validation_error = probe_video(temp_video_path)
    if not is_valid or not meta or not meta.get("duration"):
        temp_video_path.unlink(missing_ok=True)
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise RuntimeError(f"Rendered clip failed media validation: {validation_error or 'invalid or unreadable output'}")
    actual_duration = float(meta["duration"])

    if actual_duration > MAX_CLIP_DURATION:
        logger.error("Clip %s rendered duration (%.2fs) exceeds MAX_CLIP_DURATION (%.1fs). Rejecting.",
                     clip_id, actual_duration, MAX_CLIP_DURATION)
        if temp_video_path.exists():
            temp_video_path.unlink()
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise ValueError(f"Clip {clip_id} exceeds hard duration limit ({actual_duration:.2f}s > {MAX_CLIP_DURATION}s)")

    if actual_duration < MIN_CLIP_DURATION:
        logger.error("Clip %s rendered duration (%.2fs) below MIN_CLIP_DURATION (%.1fs). Rejecting.",
                     clip_id, actual_duration, MIN_CLIP_DURATION)
        if temp_video_path.exists():
            temp_video_path.unlink()
        if ass_path and ass_path.exists():
            ass_path.unlink()
        raise ValueError(f"Clip {clip_id} below minimum duration limit ({actual_duration:.2f}s < {MIN_CLIP_DURATION}s)")

    # Atomic rename: guarantees system never exposes an unplayable partial video
    temp_video_path.replace(output_video_path)

    # 5. Generate preview thumbnail atomically
    thumb_time = max(0.0, min(1.0, actual_duration / 2.0))
    thumb_cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(thumb_time, 3)),
        "-i", str(output_video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(temp_thumb_path)
    ]
    thumb_res = subprocess.run(thumb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
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

    # 6. Update DB with verified rendered layout
    media_updated = db.update_clip_media(
        clip_id=clip_id,
        video_path=str(output_video_path),
        thumbnail_path=str(output_thumb_path) if output_thumb_path.exists() else None,
        rendered_layout_mode=layout_mode,
        expected_range_revision=render_range_revision,
    )
    if render_range_revision is not None and not media_updated:
        raise RuntimeError("Clip range changed during rendering; obsolete media was not installed")

    logger.info("Successfully rendered clip %s -> %s (duration: %.2fs)", clip_id, output_video_path.name, actual_duration)

    return {
        "clip_id": clip_id,
        "video_path": output_video_path,
        "thumbnail_path": output_thumb_path if output_thumb_path.exists() else None,
        "duration": actual_duration
    }
