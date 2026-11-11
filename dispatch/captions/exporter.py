"""Canonical Caption Exporter Engine.
Generates preview/render-parity outputs from the Canonical CaptionTrack:
- ASS (Advanced SubStation Alpha) with frame-accurate active-word pop bounce
- Standard SRT
- WebVTT
- JSON
"""
import json
import math
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dispatch.captions.model import CaptionTrack, CaptionGroup, Word
from dispatch.config import TARGET_WIDTH, TARGET_HEIGHT


def float_to_ass_alpha(opacity: Any) -> str:
    """Convert float opacity [0.0 - 1.0] (1.0=opaque, 0.0=transparent) to ASS 2-digit hex alpha (00=opaque, FF=transparent)."""
    if opacity is None:
        return "00"
    try:
        val = float(opacity)
    except (ValueError, TypeError):
        return "00"
    clamped = max(0.0, min(1.0, val))
    ass_alpha_val = int(round((1.0 - clamped) * 255))
    return f"{ass_alpha_val:02X}"


def apply_case_transform(text: str, mode: str) -> str:
    """Apply non-destructive case transform to text for preview and export parity."""
    if not text:
        return ""
    if mode == "uppercase":
        return text.upper()
    if mode == "lowercase":
        return text.lower()
    if mode == "title":
        return " ".join(part.capitalize() for part in text.split(" "))
    return text


_RGBA_COMMA_RE = re.compile(
    r"^\s*rgba?\(\s*([^,\s]+)\s*,\s*([^,\s]+)\s*,\s*([^,\s)]+)(?:\s*,\s*([^,\s)]+))?\s*\)\s*$",
    re.IGNORECASE,
)
_RGBA_SPACE_RE = re.compile(
    r"^\s*rgba?\(\s*([^\s/]+)\s+([^\s/]+)\s+([^\s/]+)(?:\s*/\s*([^\s)]+))?\s*\)\s*$",
    re.IGNORECASE,
)
_NAMED_COLORS = {
    "black": (0, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "red": (255, 0, 0, 1.0),
    "green": (0, 128, 0, 1.0),
    "blue": (0, 0, 255, 1.0),
    "yellow": (255, 255, 0, 1.0),
    "cyan": (0, 255, 255, 1.0),
    "magenta": (255, 0, 255, 1.0),
    "transparent": (0, 0, 0, 0.0),
}


def _parse_css_channel(raw: str) -> int:
    token = raw.strip()
    if token.endswith("%"):
        val = float(token[:-1]) * 255.0 / 100.0
    else:
        val = float(token)
    return max(0, min(255, int(round(val))))


def _parse_css_alpha(raw: Optional[str]) -> float:
    if raw is None:
        return 1.0
    token = raw.strip()
    if token.endswith("%"):
        val = float(token[:-1]) / 100.0
    else:
        val = float(token)
    return max(0.0, min(1.0, val))


def _combine_ass_alpha(caller_alpha_hex: str, css_opacity: float) -> str:
    try:
        caller_ass = max(0, min(255, int(caller_alpha_hex, 16)))
    except (ValueError, TypeError):
        caller_ass = 0
    caller_opacity = 1.0 - (caller_ass / 255.0)
    effective_opacity = max(0.0, min(1.0, caller_opacity * max(0.0, min(1.0, css_opacity))))
    ass_alpha = int(round((1.0 - effective_opacity) * 255.0))
    return f"{ass_alpha:02X}"


def hex_to_ass_color(hex_color: str, alpha: str = "00") -> str:
    """Convert CSS color (#RRGGBB, #RRGGBBAA, #RGB, rgb(), rgba(), named) to ASS &HAABBGGRR&."""
    norm_alpha = f"{int(alpha, 16):02X}" if isinstance(alpha, str) and len(alpha) == 2 else "00"
    if not hex_color or not isinstance(hex_color, str):
        return f"&H{norm_alpha}FFFFFF&"

    raw = hex_color.strip()
    named = _NAMED_COLORS.get(raw.lower())
    if named is not None:
        r, g, b, css_a = named
        out_alpha = _combine_ass_alpha(norm_alpha, css_a)
        return f"&H{out_alpha}{b:02X}{g:02X}{r:02X}&"

    m = _RGBA_COMMA_RE.match(raw) or _RGBA_SPACE_RE.match(raw)
    if m:
        try:
            r = _parse_css_channel(m.group(1))
            g = _parse_css_channel(m.group(2))
            b = _parse_css_channel(m.group(3))
            css_a = _parse_css_alpha(m.group(4))
            out_alpha = _combine_ass_alpha(norm_alpha, css_a)
            return f"&H{out_alpha}{b:02X}{g:02X}{r:02X}&"
        except (ValueError, TypeError):
            return f"&H{norm_alpha}FFFFFF&"

    clean_hex = raw.lstrip("#")
    if len(clean_hex) == 3 and all(c in "0123456789abcdefABCDEF" for c in clean_hex):
        r = clean_hex[0] * 2
        g = clean_hex[1] * 2
        b = clean_hex[2] * 2
        return f"&H{norm_alpha}{b.upper()}{g.upper()}{r.upper()}&"
    if len(clean_hex) == 4 and all(c in "0123456789abcdefABCDEF" for c in clean_hex):
        r = clean_hex[0] * 2
        g = clean_hex[1] * 2
        b = clean_hex[2] * 2
        css_a = int(clean_hex[3] * 2, 16) / 255.0
        out_alpha = _combine_ass_alpha(norm_alpha, css_a)
        return f"&H{out_alpha}{b.upper()}{g.upper()}{r.upper()}&"
    if len(clean_hex) == 6 and all(c in "0123456789abcdefABCDEF" for c in clean_hex):
        r = clean_hex[0:2].upper()
        g = clean_hex[2:4].upper()
        b = clean_hex[4:6].upper()
        return f"&H{norm_alpha}{b}{g}{r}&"
    if len(clean_hex) == 8 and all(c in "0123456789abcdefABCDEF" for c in clean_hex):
        r = clean_hex[0:2].upper()
        g = clean_hex[2:4].upper()
        b = clean_hex[4:6].upper()
        css_a = int(clean_hex[6:8], 16) / 255.0
        out_alpha = _combine_ass_alpha(norm_alpha, css_a)
        return f"&H{out_alpha}{b}{g}{r}&"
    return f"&H{norm_alpha}FFFFFF&"


def resolve_ass_play_res(
    aspect_ratio: Optional[str] = None,
    play_res_x: Optional[int] = None,
    play_res_y: Optional[int] = None,
    layout_mode: Optional[str] = None,
) -> Tuple[int, int]:
    """Resolve dynamic ASS PlayResX / PlayResY from explicit dimensions, aspect ratio, or layout mode."""
    if play_res_x and play_res_y and int(play_res_x) > 0 and int(play_res_y) > 0:
        return int(play_res_x), int(play_res_y)

    token = str(aspect_ratio or "").strip().lower()
    if not token and layout_mode:
        lm = str(layout_mode).strip().lower()
        if lm in ("native_169", "landscape"):
            token = "16:9"
        elif lm in ("square", "1:1"):
            token = "1:1"
        elif lm in ("portrait_9_16", "native_916", "native_portrait", "crop_916", "crop_follow"):
            token = "9:16"

    if token in ("16:9", "landscape", "native_169", "16/9"):
        return 1920, 1080
    if token in ("1:1", "square", "1/1"):
        return 1080, 1080
    if token in ("4:5", "4/5"):
        return 1080, 1350
    if token in ("9:16", "portrait", "native_916", "9/16"):
        return 1080, 1920

    dim_match = re.match(r"^\s*(\d{3,4})\s*[x:]\s*(\d{3,4})\s*$", token)
    if dim_match:
        return int(dim_match.group(1)), int(dim_match.group(2))

    return TARGET_WIDTH, TARGET_HEIGHT


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


def get_ass_weight_tag(weight: Any) -> str:
    """Map font weight names or numbers to ASS \\b tag."""
    if weight is None:
        return r"\b1"
    s = str(weight).strip().lower()
    if s in ("bold", "700"):
        return r"\b700"
    if s in ("bolder", "800", "heavy", "extrabold"):
        return r"\b800"
    if s in ("black", "900"):
        return r"\b900"
    if s in ("semibold", "600"):
        return r"\b600"
    if s in ("medium", "500"):
        return r"\b500"
    if s in ("normal", "regular", "400"):
        return r"\b400"
    if s in ("light", "300"):
        return r"\b300"
    if s.isdigit():
        return f"\\b{s}"
    return r"\b1"


def export_to_ass(
    track: CaptionTrack,
    output_path: Path,
    *,
    aspect_ratio: Optional[str] = None,
    play_res_x: Optional[int] = None,
    play_res_y: Optional[int] = None,
    layout_mode: Optional[str] = None,
) -> Path:
    """Export canonical CaptionTrack to ASS with exact parity for fonts, colors, and pop animations."""
    style = track.default_style
    layout = track.default_layout
    anim = track.default_animation

    eff_aspect = aspect_ratio or getattr(track, "aspect_ratio", None) or getattr(layout, "aspect_ratio", None)
    eff_layout_mode = layout_mode or getattr(track, "layout_mode", None)
    res_x, res_y = resolve_ass_play_res(
        aspect_ratio=eff_aspect,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        layout_mode=eff_layout_mode,
    )

    primary_alpha = float_to_ass_alpha(getattr(style, "text_opacity", getattr(style, "opacity", 1.0)))
    outline_alpha = float_to_ass_alpha(getattr(style, "outline_opacity", 1.0))
    shadow_alpha = float_to_ass_alpha(getattr(style, "shadow_opacity", 1.0))
    bg_alpha = float_to_ass_alpha(getattr(style, "background_opacity", 1.0))

    primary_ass = hex_to_ass_color(style.primary_color, primary_alpha)
    highlight_ass = hex_to_ass_color(style.highlight_color, primary_alpha)
    outline_ass = hex_to_ass_color(style.outline_color, outline_alpha)
    bg_ass = hex_to_ass_color(getattr(style, "background_color", "#000000") or "#000000", bg_alpha)
    shadow_ass = hex_to_ass_color(getattr(style, "shadow_color", "#000000") or "#000000", shadow_alpha)

    # Resolution-independent coordinate space matched to target aspect ratio
    margin_v = int(round((1.0 - layout.position_y) * res_y))
    alignment = 2 if layout.alignment == "center" else (1 if layout.alignment == "left" else 3)

    outline_w = style.outline_width if style.outline_enabled else 0.0
    shadow_val = style.shadow_offset_y if style.shadow_enabled else 0.0
    bg_pad = max(4.0, float(getattr(style, "background_padding_y", 8.0) or 8.0))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font_family},{style.font_size},{primary_ass},&H000000FF,{outline_ass},{shadow_ass},-1,0,0,0,100,100,{style.letter_spacing},0,1,{outline_w},{shadow_val},{alignment},40,40,{margin_v},1
Style: Boxed,{style.font_family},{style.font_size},{primary_ass},&H000000FF,{bg_ass},{shadow_ass},-1,0,0,0,100,100,{style.letter_spacing},0,3,{bg_pad},{shadow_val},{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for grp in track.groups:
        grp_style = grp.style or style
        grp_anim = grp.animation or anim
        grp_layout = grp.layout or layout
        words = grp.words

        grp_type = getattr(grp, "type", "caption") or "caption"
        if grp_type in ("audio", "voiceover", "overlay"):
            continue
        if getattr(grp, "is_hidden", False):
            continue

        if not words:
            continue

        bg_mode = getattr(grp_style, "background_mode", None)
        if bg_mode is None:
            is_boxed = bool(getattr(grp_style, "background_enabled", False))
            bg_mode = "block" if is_boxed else "none"
        else:
            is_boxed = bg_mode in ("line", "block") and bool(getattr(grp_style, "background_enabled", True))

        style_name = "Boxed" if is_boxed else "Default"

        # Position and transform tags for exact canvas parity
        pos_x = int(round((grp_layout.position_x or 0.5) * res_x))
        pos_y = int(round((grp_layout.position_y or 0.78) * res_y))
        base_scale = int(round((grp_layout.scale or 1.0) * 100))
        an_tag = r"\an5" if grp_layout.alignment == "center" else (r"\an4" if grp_layout.alignment == "left" else r"\an6")

        # Apply background offsets if boxed
        bg_off_x = int(round(float(getattr(grp_style, "background_offset_x", 0) or 0)))
        bg_off_y = int(round(float(getattr(grp_style, "background_offset_y", 0) or 0)))
        if is_boxed and (bg_off_x != 0 or bg_off_y != 0):
            pos_x += bg_off_x
            pos_y += bg_off_y

        grp_text_alpha = float_to_ass_alpha(getattr(grp_style, "text_opacity", getattr(grp_style, "opacity", 1.0)))
        grp_outline_alpha = float_to_ass_alpha(getattr(grp_style, "outline_opacity", 1.0))
        grp_shadow_alpha = float_to_ass_alpha(getattr(grp_style, "shadow_opacity", 1.0))
        grp_bg_alpha = float_to_ass_alpha(getattr(grp_style, "background_opacity", 1.0))

        grp_primary_ass = hex_to_ass_color(grp_style.primary_color, grp_text_alpha)
        grp_highlight_ass = hex_to_ass_color(grp_style.highlight_color, grp_text_alpha)
        grp_outline_ass = hex_to_ass_color(grp_style.outline_color or "#000000", grp_outline_alpha)
        grp_outline_w = grp_style.outline_width if grp_style.outline_enabled else 0.0
        grp_shadow_ass = hex_to_ass_color(grp_style.shadow_color or "#000000", grp_shadow_alpha)

        # Shadow distance and angle calculations
        shadow_dist = getattr(grp_style, "shadow_distance", None)
        shadow_angle = getattr(grp_style, "shadow_angle", None)
        if shadow_dist is not None and shadow_angle is not None:
            rad = math.radians(float(shadow_angle))
            shadow_x = round(float(shadow_dist) * math.cos(rad), 1)
            shadow_y = round(float(shadow_dist) * math.sin(rad), 1)
        else:
            shadow_x = float(getattr(grp_style, "shadow_offset_x", 2.0) or 2.0)
            shadow_y = float(getattr(grp_style, "shadow_offset_y", 2.0) or 2.0)

        shad_tag = f"\\xshad{shadow_x}\\yshad{shadow_y}" if grp_style.shadow_enabled else "\\shad0"

        # Explicit font styling tags matching preview canvas
        font_fn = f"\\fn{grp_style.font_family}" if grp_style.font_family else ""
        font_fs = f"\\fs{int(round(grp_style.font_size or 54))}" if grp_style.font_size else ""
        font_b = get_ass_weight_tag(grp_style.font_weight)
        font_i = r"\i1" if getattr(grp_style, "font_style", "normal") == "italic" else r"\i0"
        font_u = r"\u1" if getattr(grp_style, "underline", False) else r"\u0"
        font_fsp = f"\\fsp{grp_style.letter_spacing}" if grp_style.letter_spacing is not None else ""
        font_tags = f"{font_fn}{font_fs}{font_b}{font_i}{font_u}{font_fsp}"

        # Animation evaluation for ASS tags
        enter_obj = getattr(grp_anim, "enter", None)
        enter_type = enter_obj.get("type", "none") if isinstance(enter_obj, dict) else getattr(enter_obj, "type", getattr(grp_anim, "enter_type", "none"))
        enter_dur_ms = int(enter_obj.get("duration_ms", 200)) if isinstance(enter_obj, dict) else int(getattr(enter_obj, "duration_ms", getattr(grp_anim, "enter_duration_ms", 200)))

        exit_obj = getattr(grp_anim, "exit", None)
        exit_type = exit_obj.get("type", "none") if isinstance(exit_obj, dict) else getattr(exit_obj, "type", getattr(grp_anim, "exit_type", "none"))
        exit_dur_ms = int(exit_obj.get("duration_ms", 150)) if isinstance(exit_obj, dict) else int(getattr(exit_obj, "duration_ms", getattr(grp_anim, "exit_duration_ms", 150)))

        loop_obj = getattr(grp_anim, "loop", None)
        loop_type = loop_obj.get("type", "none") if isinstance(loop_obj, dict) else getattr(loop_obj, "type", "none")
        loop_speed_ms = int(loop_obj.get("speed_ms", 1000)) if isinstance(loop_obj, dict) else int(getattr(loop_obj, "speed_ms", 1000))

        total_card_dur_ms = max(100, int(round((grp.end - grp.start) * 1000)))

        # Build ASS animation tags
        anim_tags = ""
        pos_tag = f"\\pos({pos_x},{pos_y})"

        # 1. Entrance Animation
        fad_in = enter_dur_ms if enter_type == "fade" else 0
        fad_out = exit_dur_ms if exit_type == "fade" else 0
        if fad_in > 0 or fad_out > 0:
            anim_tags += f"\\fad({fad_in},{fad_out})"

        if enter_type == "pop":
            pop_dur = max(30, enter_dur_ms)
            half_in = max(15, pop_dur // 2)
            pop_start_scale = max(10, int(round(base_scale * 0.4)))
            pop_peak_scale = int(round(base_scale * 1.15))
            anim_tags += f"\\fscx{pop_start_scale}\\fscy{pop_start_scale}\\alpha&HFF&\\t(0,{half_in},\\alpha&H00&\\fscx{pop_peak_scale}\\fscy{pop_peak_scale})\\t({half_in},{pop_dur},\\fscx{base_scale}\\fscy{base_scale})"
        elif enter_type == "slide_up":
            slide_dur = max(30, enter_dur_ms)
            slide_offset = 35
            pos_tag = f"\\move({pos_x},{pos_y + slide_offset},{pos_x},{pos_y},0,{slide_dur})"
            if fad_in == 0:
                anim_tags += f"\\fad({slide_dur},0)"

        # 2. Exit Animation
        exit_start_ms = max(0, total_card_dur_ms - exit_dur_ms)
        if exit_type == "scale_down":
            anim_tags += f"\\t({exit_start_ms},{total_card_dur_ms},\\fscx{int(base_scale * 0.4)}\\fscy{int(base_scale * 0.4)}\\alpha&HFF&)"
        elif exit_type == "slide_down":
            if enter_type != "slide_up":
                pos_tag = f"\\move({pos_x},{pos_y},{pos_x},{pos_y + 35},{exit_start_ms},{total_card_dur_ms})"
            if fad_out == 0:
                anim_tags += f"\\fad(0,{exit_dur_ms})"

        # 3. Loop Animation
        if loop_type == "pulse":
            p_scale = int(round(base_scale * 1.15))
            h_ms = max(50, loop_speed_ms // 2)
            anim_tags += f"\\t(0,{h_ms},\\fscx{p_scale}\\fscy{p_scale})\\t({h_ms},{loop_speed_ms},\\fscx{base_scale}\\fscy{base_scale})"
        elif loop_type == "wiggle":
            w_ms = max(30, loop_speed_ms // 4)
            anim_tags += f"\\t(0,{w_ms},\\frz-6)\\t({w_ms},{3 * w_ms},\\frz6)\\t({3 * w_ms},{loop_speed_ms},\\frz0)"

        if is_boxed:
            grp_bg_ass = hex_to_ass_color(getattr(grp_style, "background_color", "#000000") or "#000000", grp_bg_alpha)
            pad_y = float(getattr(grp_style, "background_padding_y", 8.0) or 8.0)
            pad_x = float(getattr(grp_style, "background_padding_x", 12.0) or 12.0)
            grp_bg_pad = max(2.0, max(pad_y, pad_x))
            style_prefix = f"\\3c{grp_bg_ass}\\bord{grp_bg_pad}{shad_tag}\\4c{grp_shadow_ass}"
        else:
            style_prefix = f"\\3c{grp_outline_ass}\\bord{grp_outline_w}{shad_tag}\\4c{grp_shadow_ass}"

        scale_tag = f"\\fscx{base_scale}\\fscy{base_scale}" if enter_type != "pop" else ""
        pos_prefix = "{" + f"{pos_tag}{an_tag}{scale_tag}{font_tags}{anim_tags}{style_prefix}" + "}"

        # Glow layer parameters with matching font tags
        glow_enabled = bool(getattr(grp_style, "glow_enabled", False))
        if getattr(grp_style, "glow_opacity", None) is not None and grp_style.glow_opacity < 0.999:
            glow_alpha = float_to_ass_alpha(float(grp_style.glow_opacity) * (1.0 - 0x35 / 255.0))
        else:
            glow_alpha = "35"
        glow_ass = hex_to_ass_color(getattr(grp_style, "glow_color", "#00F0FF") or "#00F0FF")
        glow_blur = int(round(float(getattr(grp_style, "glow_blur", 12.0) or 12.0)))
        glow_spread = max(3, int(round(glow_blur * 0.45)))
        glow_prefix = "{" + f"{pos_tag}{an_tag}{scale_tag}{font_tags}{anim_tags}\\c{glow_ass}\\3c{glow_ass}\\bord{glow_spread}\\blur{glow_blur}\\shad0\\alpha&H{glow_alpha}&" + "}"

        # Determine visual line breaks matching frontend linePartition.ts
        line_break_indices = set()
        running_w = 0.0
        eff_font_size = float(grp_style.font_size or 54.0)
        eff_letter_spacing = float(grp_style.letter_spacing or 0.0)
        space_w = eff_font_size * 0.35
        max_content_w = float(grp_layout.max_width or 0.88) * res_x

        for w_idx, w_item in enumerate(words):
            raw_w_text = w_item.text or ""
            if "\n" in raw_w_text and w_idx > 0:
                line_break_indices.add(w_idx)
                running_w = 0.0
            
            clean_token = raw_w_text.replace("\n", " ").strip()
            w_w = len(clean_token) * eff_font_size * 0.55 + eff_letter_spacing * len(clean_token)
            if running_w > 0 and (running_w + w_w > max_content_w):
                line_break_indices.add(w_idx)
                running_w = w_w + space_w
            else:
                running_w += w_w + space_w

        grp_type = getattr(grp, "type", "caption") or "caption"
        is_caption = grp_type == "caption"
        highlight_enabled = is_caption and bool(getattr(grp_style, "highlight_enabled", True))

        has_word_timing = is_caption and getattr(grp, "has_word_timing", True) and getattr(grp, "timing_mode", "word") == "word"
        if has_word_timing:
            if not any(getattr(w, "is_exact", True) for w in words):
                has_word_timing = False
            elif len(words) > 1 and all(w.start == words[0].start and w.end == words[0].end for w in words):
                has_word_timing = False

        if not has_word_timing:
            # Phrase-level dialogue: render entire phrase across card's start to end without fake karaoke
            phrase_start = format_ass_time(grp.start)
            phrase_end = format_ass_time(grp.end)
            phrase_tokens = []
            for w_k, w in enumerate(words):
                if w_k in line_break_indices and w_k > 0:
                    phrase_tokens.append(r"\N")
                w_text = apply_case_transform(w.text, getattr(grp_style, "text_transform", "none"))
                if w_text:
                    phrase_tokens.append(w_text)
            card_text = " ".join(phrase_tokens).replace(r" \N ", r"\N").replace(r" \N", r"\N").replace(r"\N ", r"\N")

            if glow_enabled:
                # Layer 0: Background Gaussian Glow
                events.append(f"Dialogue: 0,{phrase_start},{phrase_end},{style_name},,0,0,0,,{glow_prefix}{card_text}")
                # Layer 1: Foreground Crisp Text
                events.append(f"Dialogue: 1,{phrase_start},{phrase_end},{style_name},,0,0,0,,{pos_prefix}{{\\c{grp_primary_ass}}}{card_text}")
            else:
                events.append(f"Dialogue: 0,{phrase_start},{phrase_end},{style_name},,0,0,0,,{pos_prefix}{{\\c{grp_primary_ass}}}{card_text}")
            continue

        # Generate word-by-word karaoke illumination within this card preserving authentic word boundaries
        for j, target_word in enumerate(words):
            w_start = target_word.start
            w_end = target_word.end
            if w_end <= w_start:
                if j < len(words) - 1:
                    w_end = max(w_start + 0.10, words[j + 1].start)
                else:
                    w_end = max(w_start + 0.10, grp.end)

            if w_end <= w_start:
                continue

            rel_start = format_ass_time(w_start)
            rel_end = format_ass_time(w_end)

            card_tokens = []
            glow_tokens = []
            for k, w in enumerate(words):
                if k in line_break_indices and k > 0:
                    card_tokens.append(r"\N")
                    glow_tokens.append(r"\N")
                w_text = apply_case_transform(w.text, getattr(grp_style, "text_transform", "none"))
                if not w_text:
                    continue

                glow_tokens.append(w_text)
                override = getattr(w, "style_override", None)
                w_primary_ass = hex_to_ass_color(override.color, grp_text_alpha) if (override and override.color) else grp_primary_ass
                if highlight_enabled:
                    w_highlight_ass = hex_to_ass_color(override.highlight_color, grp_text_alpha) if (override and override.highlight_color) else grp_highlight_ass
                else:
                    w_highlight_ass = w_primary_ass
                w_scale_mult = float(override.scale) if (override and override.scale) else 1.0
                effective_word_scale = int(round(base_scale * w_scale_mult))
                w_weight_tag = get_ass_weight_tag(override.font_weight) if (override and override.font_weight) else ""
                w_u_tag = (r"\u1" if override.underline else r"\u0") if (override and override.underline is not None) else ""

                word_anim_type = "pop"
                active_scale_mult = 1.16
                active_dur = 100
                if hasattr(grp_anim, "active_word") and grp_anim.active_word:
                    if isinstance(grp_anim.active_word, dict):
                        word_anim_type = grp_anim.active_word.get("type", "pop")
                        active_scale_mult = float(grp_anim.active_word.get("active_scale", 1.16))
                        active_dur = int(grp_anim.active_word.get("duration_ms", 100))
                    else:
                        word_anim_type = getattr(grp_anim.active_word, "type", "pop")
                        active_scale_mult = float(getattr(grp_anim.active_word, "active_scale", 1.16))
                        active_dur = int(getattr(grp_anim.active_word, "duration_ms", 100))
                elif hasattr(grp_anim, "type") and grp_anim.type:
                    word_anim_type = grp_anim.type
                    active_scale_mult = float(getattr(grp_anim, "active_scale", 1.16))
                    active_dur = int(getattr(grp_anim, "duration_ms", 100))

                if k == j:
                    # Active spoken word with scale pop bounce or color highlight
                    if word_anim_type in ("pop", "bounce"):
                        punch_pct = int(round(effective_word_scale * active_scale_mult))
                        anim_tag = f"{{\\c{w_highlight_ass}{w_weight_tag}{w_u_tag}\\fscx{punch_pct}\\fscy{punch_pct}\\t(0,{active_dur},\\fscx{effective_word_scale}\\fscy{effective_word_scale})}}"
                    else:
                        anim_tag = f"{{\\c{w_highlight_ass}{w_weight_tag}{w_u_tag}\\fscx{effective_word_scale}\\fscy{effective_word_scale}}}"

                    card_tokens.append(f"{anim_tag}{w_text}{{\\c{w_primary_ass}{font_b}{font_u}\\fscx{base_scale}\\fscy{base_scale}}}")
                else:
                    if w_primary_ass != grp_primary_ass or effective_word_scale != base_scale or w_weight_tag or w_u_tag:
                        card_tokens.append(f"{{\\c{w_primary_ass}{w_weight_tag}{w_u_tag}\\fscx{effective_word_scale}\\fscy{effective_word_scale}}}{w_text}{{\\c{grp_primary_ass}{font_b}{font_u}\\fscx{base_scale}\\fscy{base_scale}}}")
                    else:
                        card_tokens.append(w_text)

            card_str = " ".join(card_tokens).replace(r" \N ", r"\N").replace(r" \N", r"\N").replace(r"\N ", r"\N")
            glow_str = " ".join(glow_tokens).replace(r" \N ", r"\N").replace(r" \N", r"\N").replace(r"\N ", r"\N")

            if glow_enabled:
                # Layer 0: Background Gaussian Glow
                glow_line = glow_prefix + glow_str
                events.append(f"Dialogue: 0,{rel_start},{rel_end},{style_name},,0,0,0,,{glow_line}")
                # Layer 1: Foreground Illuminated Text
                line_text = pos_prefix + card_str
                events.append(f"Dialogue: 1,{rel_start},{rel_end},{style_name},,0,0,0,,{line_text}")
            else:
                line_text = pos_prefix + card_str
                events.append(f"Dialogue: 0,{rel_start},{rel_end},{style_name},,0,0,0,,{line_text}")

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
        grp_type = getattr(grp, "type", "caption") or "caption"
        if grp_type in ("audio", "voiceover", "overlay") or getattr(grp, "is_hidden", False):
            continue
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


def remap_caption_track_to_timeline(
    track: CaptionTrack,
    spans: Any,
    clip_start: float = 0.0,
    source_duration: Optional[float] = None,
) -> CaptionTrack:
    """Remap a source/clip-relative CaptionTrack to output presentation time for cut EDL clips.

    Words and groups inside removed footage are omitted.
    Word and group boundaries are clipped to segment boundaries.
    """
    if not track or not track.groups or not spans:
        return track

    from dispatch.segments import normalize_ranges, intersect_source_interval

    max_end = 0.0
    for s in spans:
        if isinstance(s, (list, tuple)) and len(s) == 2:
            max_end = max(max_end, float(s[1]))
        elif isinstance(s, dict):
            max_end = max(max_end, float(s.get("source_end", s.get("end", 0.0))))
    effective_dur = float(source_duration) if source_duration else (max_end + 10.0)

    try:
        canonical = normalize_ranges(spans, effective_dur)
    except Exception:
        return track

    if len(canonical) == 0:
        return track

    remapped_groups: List[CaptionGroup] = []

    for grp in track.groups:
        grp_type = getattr(grp, "type", "caption") or "caption"
        if grp_type in ("audio", "voiceover"):
            remapped_groups.append(grp)
            continue

        g_start = float(grp.start or 0.0)
        g_end = float(grp.end or 0.0)

        grp_src_start = clip_start + g_start
        grp_src_end = clip_start + g_end

        if grp_src_end <= grp_src_start:
            continue

        fragments = intersect_source_interval(canonical, grp_src_start, grp_src_end, effective_dur)
        if not fragments:
            continue

        for f_idx, frag in enumerate(fragments):
            frag_src_s = frag["source_start"]
            frag_src_e = frag["source_end"]
            frag_tl_s = frag["timeline_start"]
            frag_tl_e = frag["timeline_end"]

            remapped_words: List[Word] = []
            if grp.words:
                for w in grp.words:
                    w_s = float(w.start or 0.0)
                    w_e = float(w.end or 0.0)
                    w_src_s = clip_start + w_s
                    w_src_e = clip_start + w_e

                    overlap_s = max(w_src_s, frag_src_s)
                    overlap_e = min(w_src_e, frag_src_e)
                    if overlap_e - overlap_s > 0.005:
                        w_tl_s = frag_tl_s + (overlap_s - frag_src_s)
                        w_tl_e = frag_tl_s + (overlap_e - frag_src_s)
                        remapped_words.append(Word(
                            id=w.id,
                            text=w.text,
                            start=round(w_tl_s, 3),
                            end=round(w_tl_e, 3),
                            confidence=w.confidence,
                            is_exact=w.is_exact,
                            style_override=w.style_override,
                        ))

            if not remapped_words and grp.has_word_timing and grp.words:
                continue

            mapped_g_start = remapped_words[0].start if remapped_words else round(frag_tl_s, 3)
            mapped_g_end = remapped_words[-1].end if remapped_words else round(frag_tl_e, 3)

            gid = f"{grp.id}_{f_idx}" if len(fragments) > 1 else grp.id
            remapped_groups.append(CaptionGroup(
                id=gid,
                start=mapped_g_start,
                end=mapped_g_end,
                words=remapped_words if remapped_words else grp.words,
                layout=grp.layout,
                style=grp.style,
                animation=grp.animation,
                trackIndex=grp.trackIndex,
                type=grp.type,
                volume=grp.volume,
                is_muted=grp.is_muted,
                audio_url=grp.audio_url,
                overlay_url=grp.overlay_url,
                overlay_type=grp.overlay_type,
                opacity=grp.opacity,
                label=grp.label,
                flip_h=grp.flip_h,
                flip_v=grp.flip_v,
                overlay_animation=grp.overlay_animation,
                has_word_timing=grp.has_word_timing,
                timing_mode=grp.timing_mode,
            ))

    return CaptionTrack(
        id=track.id,
        clip_id=track.clip_id,
        language=track.language,
        default_style=track.default_style,
        default_layout=track.default_layout,
        default_animation=track.default_animation,
        groups=remapped_groups,
    )
