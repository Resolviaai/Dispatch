"""ASS Subtitle generator for vertical 9:16 short-form video.
Generates styled subtitles with active word highlights, safe margin placement, and Roman Hinglish formatting.
"""
from pathlib import Path
from typing import List, Dict, Any
from dispatch.config import (
    TARGET_WIDTH,
    TARGET_HEIGHT,
    SUBTITLE_FONT,
    SUBTITLE_FONT_SIZE,
    SUBTITLE_PRIMARY_COLOR,
    SUBTITLE_OUTLINE_COLOR,
    SUBTITLE_HIGHLIGHT_COLOR
)


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cs"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def generate_ass_subtitles(
    segments: List[Dict[str, Any]],
    output_path: Path,
    clip_start_time: float = 0.0,
    clip_end_time: float = float("inf")
) -> Path:
    """Generate an Advanced SubStation Alpha (.ass) subtitle file trimmed to clip boundaries.
    
    Args:
        segments: Transcript segments with word-level timestamps.
        output_path: Path where the .ass file will be saved.
        clip_start_time: Start time of the clip relative to the source video.
        clip_end_time: End time of the clip relative to the source video.
        
    Returns:
        Path to generated .ass file.
    """
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{SUBTITLE_FONT},52,{SUBTITLE_PRIMARY_COLOR},&H000000FF,{SUBTITLE_OUTLINE_COLOR},&H80000000,-1,0,0,0,100,100,0,0,1,4.5,1.5,2,40,40,480,1
Style: Highlight,{SUBTITLE_FONT},52,{SUBTITLE_HIGHLIGHT_COLOR},&H000000FF,{SUBTITLE_OUTLINE_COLOR},&H80000000,-1,0,0,0,100,100,0,0,1,4.5,1.5,2,40,40,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    for seg in segments:
        seg_start = seg["start"]
        seg_end = seg["end"]

        # Only process segments overlapping with this clip
        if seg_end <= clip_start_time or seg_start >= clip_end_time:
            continue

        words = seg.get("words", [])

        if words:
            # Group words into 3-5 word chunks for high-retention rapid subtitle cards
            CHUNK_SIZE = 4
            for i in range(0, len(words), CHUNK_SIZE):
                chunk_words = words[i:i + CHUNK_SIZE]
                chunk_start = max(clip_start_time, chunk_words[0]["start"])
                chunk_end = min(clip_end_time, chunk_words[-1]["end"])

                if chunk_end <= chunk_start or chunk_end <= clip_start_time or chunk_start >= clip_end_time:
                    continue

                # Relative to clip start
                rel_start = format_ass_timestamp(max(0.0, chunk_start - clip_start_time))
                rel_end = format_ass_timestamp(max(0.0, chunk_end - clip_start_time))

                # Display the chunk text
                text_display = " ".join(w["word"] for w in chunk_words).upper()
                events.append(f"Dialogue: 0,{rel_start},{rel_end},Default,,0,0,0,,{text_display}")

        else:
            # Fallback to segment-level dialogue
            rel_start = format_ass_timestamp(max(0.0, seg_start - clip_start_time))
            rel_end = format_ass_timestamp(max(0.0, min(clip_end_time, seg_end) - clip_start_time))
            text_display = seg["text"].strip().upper()
            events.append(f"Dialogue: 0,{rel_start},{rel_end},Default,,0,0,0,,{text_display}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        f.write("\n")

    return output_path
