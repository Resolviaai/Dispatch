"""Adaptive video reframing filter generator.
Converts 16:9 landscape to vertical 9:16 using either Fit-with-Blur or Crop-Follow,
and handles native 9:16 portrait orientation.
"""
from pathlib import Path
from typing import Optional, Tuple
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT


def escape_ffmpeg_filter_path(filepath: Path) -> str:
    """Escape Windows file paths for FFmpeg filter arguments (subtitles filter)."""
    # Use forward slashes and escape the colon in Windows drive letter (e.g. C\: -> C\:/)
    path_str = str(filepath.resolve()).replace("\\", "/")
    if len(path_str) > 1 and path_str[1] == ":":
        path_str = path_str[0] + "\\:" + path_str[2:]
    return path_str


def build_filter_complex(
    aspect_ratio: str,
    layout_mode: str = "fit_blur",
    ass_subtitle_path: Optional[Path] = None
) -> Tuple[str, str]:
    """Build FFmpeg filter_complex string for adaptive 9:16 vertical conversion and subtitle burning.
    
    Args:
        aspect_ratio: '16:9', '9:16', etc.
        layout_mode: 'fit_blur', 'crop_follow'
        ass_subtitle_path: Optional path to .ass subtitle file.
        
    Returns:
        (filter_complex_string, output_video_label)
    """
    filters = []

    if aspect_ratio == "9:16":
        # Native portrait: scale to 1080x1920 keeping aspect ratio
        filters.append(
            f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2[base]"
        )
    elif layout_mode == "crop_follow":
        # Center-crop 9:16 from 16:9
        filters.append(
            f"[0:v]crop=ih*9/16:ih:(iw-ow)/2:0,scale={TARGET_WIDTH}:{TARGET_HEIGHT}[base]"
        )
    else:
        # Default: Fit with high-quality blurred background
        filters.append(
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},boxblur=25:5[bg];"
            f"[fg_in]scale={TARGET_WIDTH}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )

    current_label = "[base]"

    if ass_subtitle_path and ass_subtitle_path.exists():
        escaped_sub = escape_ffmpeg_filter_path(ass_subtitle_path)
        filters.append(f"{current_label}subtitles='{escaped_sub}'[outv]")
        final_label = "[outv]"
    else:
        final_label = current_label

    filter_complex_str = ";".join(filters)
    return filter_complex_str, final_label
