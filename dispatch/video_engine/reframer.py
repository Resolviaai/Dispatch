"""Adaptive video reframing filter generator.
Converts between portrait (9:16) and landscape (16:9) formats based on
original session input orientation.
"""
from pathlib import Path
from typing import Optional, Tuple
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT

# Output resolution standards
PORTRAIT_WIDTH = TARGET_WIDTH     # 1080
PORTRAIT_HEIGHT = TARGET_HEIGHT   # 1920
WIDE_WIDTH = 1920
WIDE_HEIGHT = 1080


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
    """Build FFmpeg filter_complex string for orientation-aware conversion and subtitle burning.
    
    Args:
        aspect_ratio: Source video aspect ratio: '9:16' / 'portrait' or '16:9' / 'landscape'.
        layout_mode:
            For 9:16 Portrait source:
                - 'fit_black': 16:9 widescreen output with black left/right pillarbox bars.
                - 'fit_blur': 16:9 widescreen output with blurred left/right pillarbox bars.
                - 'native_916' (or 'native_portrait'): 9:16 vertical output, 100% full original video (no bars, no crop).
            For 16:9 Landscape source:
                - 'crop_916' (or 'crop_follow'): 9:16 vertical output, centered vertical crop.
                - 'fit_blur': 9:16 vertical output with blurred top/bottom letterbox bars.
                - 'fit_black': 9:16 vertical output with black top/bottom letterbox bars.
                - 'native_169' (or 'landscape'): 16:9 widescreen output, 100% full original video (no bars, no crop).
        ass_subtitle_path: Optional path to .ass subtitle file.
        
    Returns:
        (filter_complex_string, output_video_label)
    """
    filters = []
    mode = (layout_mode or "").strip().lower()
    is_portrait = str(aspect_ratio).strip().lower() in ("9:16", "portrait", "vertical")

    if is_portrait:
        # ============================================================
        # CASE A — ORIGINAL INPUT IS VERTICAL / 9:16
        # ============================================================
        if mode == "fit_black":
            # OPTION 1 — LONG FORM / 16:9 + BLACK
            # Output: 16:9. Source fully visible in center with black pillarbox bars.
            filters.append(
                f"[0:v]scale={WIDE_WIDTH}:{WIDE_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDE_WIDTH}:{WIDE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[base]"
            )
        elif mode == "fit_blur":
            # OPTION 2 — LONG FORM / 16:9 + BLUR
            # Output: 16:9. Source fully visible in center with blurred pillarbox bars.
            filters.append(
                f"[0:v]split=2[bg_in][fg_in];"
                f"[bg_in]scale={WIDE_WIDTH}:{WIDE_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDE_WIDTH}:{WIDE_HEIGHT},boxblur=25:5[bg];"
                f"[fg_in]scale=-2:{WIDE_HEIGHT}[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
        else:
            # OPTION 3 — NATIVE 9:16 ('native_916', 'native_portrait', default)
            # Output: 9:16. 100% full original vertical video. No crop, no blur, no bars.
            filters.append(
                f"[0:v]scale={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[base]"
            )
    else:
        # ============================================================
        # CASE B — ORIGINAL INPUT IS HORIZONTAL / 16:9
        # ============================================================
        if mode in ("crop_916", "crop_follow"):
            # OPTION 1 — CROP 9:16
            # Output: 9:16. 16:9 source cropped vertically to fill 9:16 frame.
            filters.append(
                f"[0:v]crop=w=trunc(ih*9/16/2)*2:h=ih:x=trunc((iw-ow)/2):y=0,scale={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}[base]"
            )
        elif mode == "fit_blur":
            # OPTION 2 — FIT + BLUR
            # Output: 9:16. 16:9 source in center with blurred top/bottom letterbox.
            filters.append(
                f"[0:v]split=2[bg_in][fg_in];"
                f"[bg_in]scale={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT},boxblur=25:5[bg];"
                f"[fg_in]scale={PORTRAIT_WIDTH}:-2[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
        elif mode == "fit_black":
            # OPTION 3 — FIT + BLACK
            # Output: 9:16. 16:9 source in center with black top/bottom letterbox bars.
            filters.append(
                f"[0:v]scale={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={PORTRAIT_WIDTH}:{PORTRAIT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[base]"
            )
        else:
            # OPTION 4 — NATIVE 16:9 ('native_169', 'landscape', default)
            # Output: 16:9. 100% full original horizontal video. No crop, no blur, no bars.
            filters.append(
                f"[0:v]scale={WIDE_WIDTH}:{WIDE_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDE_WIDTH}:{WIDE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black[base]"
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
