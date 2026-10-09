"""Canonical Caption Exporter Engine.
Generates preview/render-parity outputs from the Canonical CaptionTrack:
- ASS (Advanced SubStation Alpha) with frame-accurate active-word pop bounce
- Standard SRT
- WebVTT
- JSON
"""
import json
from pathlib import Path
from typing import Dict, Any, List
from dispatch.captions.model import CaptionTrack, CaptionGroup, Word
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT


def hex_to_ass_color(hex_color: str, alpha: str = "00") -> str:
    """Convert standard Hex color (#RRGGBB) to ASS color format (&HAABBGGRR&)."""
    clean_hex = hex_color.lstrip("#")
    if len(clean_hex) == 6:
        r = clean_hex[0:2]
        g = clean_hex[2:4]
        b = clean_hex[4:6]
        return f"&H{alpha}{b}{g}{r}&"
    return f"&H{alpha}FFFFFF&"


def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp: H:MM:SS.cs"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp: HH:MM:SS,mmm"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def export_to_ass(track: CaptionTrack, output_path: Path) -> Path:
    """Export canonical CaptionTrack to ASS with exact parity for fonts, colors, and pop animations."""
    style = track.default_style
    layout = track.default_layout
    anim = track.default_animation

    primary_ass = hex_to_ass_color(style.primary_color)
    highlight_ass = hex_to_ass_color(style.highlight_color)
    outline_ass = hex_to_ass_color(style.outline_color)

    # Resolution-independent to 1080x1920 vertical coordinate space
    # position_y: 0.78 corresponds to margin_v ~420px from bottom (alignment 2)
    margin_v = int(round((1.0 - layout.position_y) * TARGET_HEIGHT))
    alignment = 2 if layout.alignment == "center" else (1 if layout.alignment == "left" else 3)
    
    border_style = 3 if style.background_enabled else 1
    outline_w = style.outline_width if style.outline_enabled else 0.0
    shadow_val = style.shadow_offset_y if style.shadow_enabled else 0.0

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font_family},{style.font_size},{primary_ass},&H000000FF,{outline_ass},&H80000000,-1,0,0,0,100,100,{style.letter_spacing},0,{border_style},{outline_w},{shadow_val},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for grp in track.groups:
        grp_style = grp.style or style
        grp_anim = grp.animation or anim
        grp_layout = grp.layout or layout
        words = grp.words

        if not words:
            continue

        # Position and transform tags for exact canvas parity
        pos_x = int(round((grp_layout.position_x or 0.5) * TARGET_WIDTH))
        pos_y = int(round((grp_layout.position_y or 0.78) * TARGET_HEIGHT))
        base_scale = int(round((grp_layout.scale or 1.0) * 100))
        an_tag = r"\an5" if grp_layout.alignment == "center" else (r"\an4" if grp_layout.alignment == "left" else r"\an6")
        pos_prefix = f"{{\\pos({pos_x},{pos_y}){an_tag}\\fscx{base_scale}\\fscy{base_scale}}}"

        # Generate word-by-word karaoke illumination within this card
        for j, target_word in enumerate(words):
            w_start = target_word.start
            if j < len(words) - 1:
                w_end = max(w_start + 0.10, words[j + 1].start)
            else:
                w_end = max(w_start + 0.10, target_word.end)

            if w_end <= w_start:
                continue

            rel_start = format_ass_time(w_start)
            rel_end = format_ass_time(w_end)

            card_tokens = []
            for k, w in enumerate(words):
                w_text = w.text.upper() if grp_style.text_transform == "uppercase" else w.text
                if not w_text:
                    continue

                if k == j:
                    # Active spoken word with scale pop bounce
                    if grp_anim.type in ("pop", "bounce") or (hasattr(grp_anim, 'active_type') and grp_anim.active_type in ("pop", "bounce")):
                        active_scale_mult = grp_anim.active_scale or 1.16
                        punch_pct = int(round(base_scale * active_scale_mult))
                        dur = grp_anim.duration_ms or 100
                        anim_tag = f"{{\\c{highlight_ass}\\fscx{punch_pct}\\fscy{punch_pct}\\t(0,{dur},\\fscx{base_scale}\\fscy{base_scale})}}"
                    else:
                        anim_tag = f"{{\\c{highlight_ass}}}"

                    card_tokens.append(f"{anim_tag}{w_text}{{\\c{primary_ass}\\fscx{base_scale}\\fscy{base_scale}}}")
                else:
                    card_tokens.append(w_text)

            line_text = pos_prefix + " ".join(card_tokens)
            events.append(f"Dialogue: 0,{rel_start},{rel_end},Default,,0,0,0,,{line_text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))
        f.write("\n")

    return output_path


def export_to_srt(track: CaptionTrack, output_path: Path) -> Path:
    """Export canonical CaptionTrack to standard SRT."""
    lines = []
    counter = 1
    for grp in track.groups:
        start_ts = format_srt_time(grp.start)
        end_ts = format_srt_time(grp.end)
        text = " ".join(w.text for w in grp.words)
        lines.append(f"{counter}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")
        counter += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path


def export_to_json(track: CaptionTrack, output_path: Path) -> Path:
    """Export canonical CaptionTrack to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(track.to_dict(), f, indent=2)
    return output_path
