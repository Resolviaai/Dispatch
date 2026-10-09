"""High-production CapCut / MrBeast-grade ASS Subtitle Generator for Dispatch.
Generates word-chunked, karaoke-animated vertical video subtitles with:
- Bouncy scale-pop animation (\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100))
- 2-3 word rapid retention chunking
- Deep contrast 4.5px black outlines and drop shadows
- High-visibility color presets (CapCut Yellow, Hormozi Green, Electric Cyan, Boxed Minimal)
- Shorts/Reels bottom safe-zone placement (above platform UI overlay)
"""
from pathlib import Path
from typing import List, Dict, Any, Optional
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT

# Curated high-impact display font families with system fallbacks
FONT_PRESETS = {
    "impact": "Impact",
    "arial_black": "Arial Black",
    "montserrat": "Montserrat",
    "poppins": "Poppins",
    "trebuchet": "Trebuchet MS",
    "arial": "Arial"
}

# Curated CapCut / viral short color palettes (ASS format: &HAABBGGRR&)
STYLE_PRESETS = {
    "yellow_pop": {
        "primary": "&H00FFFFFF",     # Crisp White
        "highlight": "&H0000FFFF",   # Pure Vibrant Yellow
        "outline": "&H00000000",     # Deep Black
        "border_style": 1,           # Outline + drop shadow
        "outline_width": 4.5,
        "shadow": 2.0,
    },
    "hormozi_green": {
        "primary": "&H00FFFFFF",     # Crisp White
        "highlight": "&H0000FF4D",   # Energetic Neon Emerald
        "outline": "&H00000000",     # Deep Black
        "border_style": 1,
        "outline_width": 5.0,
        "shadow": 2.0,
    },
    "electric_cyan": {
        "primary": "&H00FFFFFF",     # Crisp White
        "highlight": "&H00FFFF00",   # Electric Neon Cyan
        "outline": "&H00000000",     # Deep Black
        "border_style": 1,
        "outline_width": 4.5,
        "shadow": 2.0,
    },
    "boxed_minimal": {
        "primary": "&H00FFFFFF",     # Crisp White
        "highlight": "&H0000C8FF",   # Warm Gold
        "outline": "&H80000000",     # Translucent Black Box
        "border_style": 3,           # Opaque Background Box
        "outline_width": 2.0,
        "shadow": 0.0,
    }
}


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
    clip_end_time: float = float("inf"),
    font_name: str = "Arial Black",
    font_size: int = 54,
    style_preset: str = "yellow_pop",
    position: str = "bottom",
    enable_pop_bounce: bool = True,
    words_per_card: int = 3
) -> Path:
    """Generate high-retention, CapCut-quality ASS subtitles with word-by-word pop bounce.

    Args:
        segments: Transcript segments with word-level timestamps.
        output_path: Destination path for the .ass subtitle file.
        clip_start_time: Clip start relative to source video in seconds.
        clip_end_time: Clip end relative to source video in seconds.
        font_name: Typography font family (e.g. 'Impact', 'Arial Black', 'Montserrat').
        font_size: Subtitle font size in points.
        style_preset: Style preset ('yellow_pop', 'hormozi_green', 'electric_cyan', 'boxed_minimal').
        position: Vertical placement ('bottom' for Shorts safe-zone, 'middle' for center).
        enable_pop_bounce: Whether to apply the tactile scale punch animation.
        words_per_card: How many words appear simultaneously on screen (2-3 recommended).

    Returns:
        Path to generated .ass file.
    """
    preset = STYLE_PRESETS.get(style_preset, STYLE_PRESETS["yellow_pop"])
    primary_col = preset["primary"]
    highlight_col = preset["highlight"]
    outline_col = preset["outline"]
    border_style = preset["border_style"]
    outline_w = preset["outline_width"]
    shadow_val = preset["shadow"]

    # Safe margin calculation
    # Vertical 9:16 (1080x1920) safe zone: bottom margin 420px prevents overlap with Shorts/Reels UI
    if position == "middle":
        margin_v = 960
        alignment = 5  # Center Middle
    else:
        margin_v = 420
        alignment = 2  # Bottom Center

    font_family = FONT_PRESETS.get(font_name.lower().replace(" ", "_"), font_name)

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{font_size},{primary_col},&H000000FF,{outline_col},&H80000000,-1,0,0,0,100,100,0.5,0,{border_style},{outline_w},{shadow_val},{alignment},40,40,{margin_v},1
Style: Highlight,{font_family},{font_size},{highlight_col},&H000000FF,{outline_col},&H80000000,-1,0,0,0,100,100,0.5,0,{border_style},{outline_w},{shadow_val},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    for seg in segments:
        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", 0.0)

        if seg_end <= clip_start_time or seg_start >= clip_end_time:
            continue

        words = seg.get("words", [])

        if words:
            # Chunk words into rapid 2-3 word retention cards
            chunk_size = max(1, min(4, words_per_card))
            for i in range(0, len(words), chunk_size):
                chunk = words[i:i + chunk_size]
                chunk_start = max(clip_start_time, chunk[0]["start"])
                chunk_end = min(clip_end_time, chunk[-1]["end"])

                if chunk_end <= chunk_start or chunk_end <= clip_start_time or chunk_start >= clip_end_time:
                    continue

                # Generate karaoke illumination for each word within this card
                for j, target_word in enumerate(chunk):
                    w_start = max(clip_start_time, target_word["start"])
                    if j < len(chunk) - 1:
                        w_end = min(clip_end_time, max(w_start + 0.12, chunk[j + 1]["start"]))
                    else:
                        w_end = min(clip_end_time, max(w_start + 0.12, target_word["end"]))

                    if w_end <= w_start:
                        continue

                    rel_start = format_ass_timestamp(max(0.0, w_start - clip_start_time))
                    rel_end = format_ass_timestamp(max(0.0, w_end - clip_start_time))

                    card_words = []
                    for k, w in enumerate(chunk):
                        cleaned = w["word"].strip().upper()
                        if not cleaned:
                            continue

                        if k == j:
                            # Active spoken word with tactile pop bounce animation
                            if enable_pop_bounce:
                                # Punch to 118% scale, ease smoothly back to 100% in 90ms
                                anim_tag = f"{{\\c{highlight_col}&\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100)}}"
                            else:
                                anim_tag = f"{{\\c{highlight_col}&}}"

                            card_words.append(f"{anim_tag}{cleaned}{{\\c{primary_col}&\\fscx100\\fscy100}}")
                        else:
                            # Inactive words in the card
                            card_words.append(cleaned)

                    text_display = " ".join(card_words)
                    events.append(f"Dialogue: 0,{rel_start},{rel_end},Default,,0,0,0,,{text_display}")

        else:
            # Fallback for plain transcript segments without word timestamps
            rel_start = format_ass_timestamp(max(0.0, seg_start - clip_start_time))
            rel_end = format_ass_timestamp(max(0.0, min(clip_end_time, seg_end) - clip_start_time))
            cleaned_text = seg.get("text", "").strip().upper()
            if cleaned_text:
                events.append(f"Dialogue: 0,{rel_start},{rel_end},Default,,0,0,0,,{cleaned_text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        f.write("\n")

    return output_path
