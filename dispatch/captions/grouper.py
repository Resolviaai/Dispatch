"""Deterministic Word-to-Caption Grouping Engine.
Translates raw word-level speech transcripts into short-form, high-retention CaptionGroups.
Enforces monotonic timings, natural pause boundaries, and word limits.
"""
from typing import List, Dict, Any, Optional
from dispatch.captions.model import Word, CaptionGroup, CaptionTrack, StyleConfig, LayoutConfig, AnimationConfig
from dispatch.captions.presets import get_preset


def group_words_into_captions(
    raw_segments: List[Dict[str, Any]],
    clip_start: float = 0.0,
    clip_end: float = float("inf"),
    clip_id: str = "",
    words_per_group: int = 3,
    max_chars_per_line: int = 24,
    max_pause_threshold: float = 0.45,  # Seconds of silence that forces a new card
    preset_id: str = "yellow_pop"
) -> CaptionTrack:
    """Deterministically partition word timestamps into rapid reading CaptionGroups.

    Args:
        raw_segments: List of segments with word timestamps [{"word": "Hi", "start": 0.0, "end": 0.3}, ...]
        clip_start: Start boundary relative to source video in seconds.
        clip_end: End boundary relative to source video in seconds.
        clip_id: ID of the clip.
        words_per_group: Maximum words per visual card (2-4 recommended for mobile).
        max_chars_per_line: Character wrapping budget.
        max_pause_threshold: Silence gap that triggers a new card boundary.
        preset_id: Styling preset to attach by default.

    Returns:
        Canonical CaptionTrack object.
    """
    preset_data = get_preset(preset_id)
    default_style: StyleConfig = preset_data["style"]
    default_layout: LayoutConfig = preset_data["layout"]
    default_anim: AnimationConfig = preset_data["animation"]

    # 1. Process segments: preserve real word timestamps if available; otherwise maintain phrase-level truth
    groups: List[CaptionGroup] = []
    current_words: List[Word] = []
    current_chars = 0
    prev_end = 0.0

    def flush_current_words():
        nonlocal current_words, current_chars, prev_end
        if not current_words:
            return
        grp = CaptionGroup(
            start=current_words[0].start,
            end=current_words[-1].end,
            words=list(current_words),
            layout=default_layout,
            style=default_style,
            animation=default_anim,
            has_word_timing=True,
            timing_mode="word"
        )
        groups.append(grp)
        current_words = []
        current_chars = 0

    for seg in raw_segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))

        if seg_end <= clip_start or seg_start >= clip_end:
            continue

        words = seg.get("words", [])
        has_exact_words = bool(words) and any(w.get("is_exact", True) for w in words)

        if has_exact_words:
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                text = str(w.get("word", "")).strip()
                if not text:
                    continue
                if w_end <= clip_start or w_start >= clip_end:
                    continue

                # Clamp to clip range and normalize relative to clip start exactly once
                rel_start = max(0.0, w_start - clip_start)
                rel_end = max(rel_start + 0.05, min(clip_end - clip_start, w_end - clip_start))

                w_obj = Word(
                    text=text,
                    start=round(rel_start, 3),
                    end=round(rel_end, 3),
                    confidence=float(w.get("confidence", 1.0)),
                    is_exact=bool(w.get("is_exact", True))
                )

                pause = w_obj.start - prev_end if prev_end > 0 else 0.0
                should_split = False

                # Split condition 1: Exceeded words per group (default 2-4 words)
                if len(current_words) >= words_per_group:
                    should_split = True
                # Split condition 2: Previous word ended with punctuation (. ? !)
                elif current_words and current_words[-1].text.endswith((".", "?", "!")):
                    should_split = True
                # Split condition 3: Previous word ended with comma/semicolon and group has >= 2 words
                elif current_words and current_words[-1].text.endswith((",", ";", ":", "—")) and len(current_words) >= 2:
                    should_split = True
                # Split condition 4: Exceeded character budget
                elif current_chars + len(w_obj.text) + 1 > max_chars_per_line and len(current_words) >= 2:
                    should_split = True
                # Split condition 5: Natural pause in speech
                elif pause >= max_pause_threshold and len(current_words) > 0:
                    should_split = True

                if should_split:
                    flush_current_words()

                current_words.append(w_obj)
                current_chars += len(w_obj.text) + 1
                prev_end = w_obj.end
        else:
            # Phrase-level segment (no inline word timestamps):
            # Flush any pending word-timed cards first
            flush_current_words()

            seg_text = seg.get("text", "").strip()
            if not seg_text:
                continue

            rel_seg_start = max(0.0, seg_start - clip_start)
            rel_seg_end = max(rel_seg_start + 0.2, min(clip_end - clip_start, seg_end - clip_start))

            # Render phrase using its authentic start and end times without fabricated linear subdivisions
            tokens = seg_text.split()
            phrase_words = [
                Word(
                    text=tok,
                    start=round(rel_seg_start, 3),
                    end=round(rel_seg_end, 3),
                    confidence=float(seg.get("confidence", 0.95)),
                    is_exact=False
                )
                for tok in tokens
            ]

            grp = CaptionGroup(
                start=round(rel_seg_start, 3),
                end=round(rel_seg_end, 3),
                words=phrase_words,
                layout=default_layout,
                style=default_style,
                animation=default_anim,
                has_word_timing=False,
                timing_mode="phrase"
            )
            groups.append(grp)
            prev_end = rel_seg_end

    # Flush any remaining word-timed group
    flush_current_words()

    # Sort groups by start time
    groups.sort(key=lambda g: g.start)

    # 3. Post-process to eliminate micro-gaps for smooth card transitions
    for idx, grp in enumerate(groups):
        if idx < len(groups) - 1:
            next_start = groups[idx + 1].start
            # If gap between cards is small (<0.25s), stretch card display end so text doesn't flicker out.
            # IMPORTANT: Keep source word timestamps strictly intact! Do NOT alter grp.words[-1].end.
            if next_start - grp.end < 0.25 and next_start > grp.end:
                grp.end = next_start

    return CaptionTrack(
        clip_id=clip_id,
        language="en",
        groups=groups,
        default_style=default_style,
        default_layout=default_layout,
        default_animation=default_anim
    )


def apply_template_to_track(track: CaptionTrack, tpl_captions: Optional[Dict[str, Any]]) -> CaptionTrack:
    """Apply user editing template caption properties over an existing CaptionTrack."""
    if not tpl_captions or not isinstance(tpl_captions, dict):
        return track

    if tpl_captions.get("font_family"):
        track.default_style.font_family = str(tpl_captions["font_family"])
    if tpl_captions.get("font_size"):
        try:
            track.default_style.font_size = int(float(tpl_captions["font_size"]))
        except (ValueError, TypeError):
            pass
    if tpl_captions.get("font_weight"):
        track.default_style.font_weight = str(tpl_captions["font_weight"])
    if tpl_captions.get("primary_color"):
        track.default_style.primary_color = str(tpl_captions["primary_color"])
    if tpl_captions.get("highlight_color"):
        track.default_style.highlight_color = str(tpl_captions["highlight_color"])
    if tpl_captions.get("highlight_enabled") is not None:
        track.default_style.highlight_enabled = bool(tpl_captions["highlight_enabled"])
    if tpl_captions.get("outline_enabled") is not None:
        track.default_style.outline_enabled = bool(tpl_captions["outline_enabled"])
    if tpl_captions.get("outline_color"):
        track.default_style.outline_color = str(tpl_captions["outline_color"])
    if tpl_captions.get("outline_width") is not None:
        try:
            track.default_style.outline_width = float(tpl_captions["outline_width"])
        except (ValueError, TypeError):
            pass
    if tpl_captions.get("shadow_enabled") is not None:
        track.default_style.shadow_enabled = bool(tpl_captions["shadow_enabled"])
    if tpl_captions.get("shadow_color"):
        track.default_style.shadow_color = str(tpl_captions["shadow_color"])
    if tpl_captions.get("background_enabled") is not None:
        track.default_style.background_enabled = bool(tpl_captions["background_enabled"])
    if tpl_captions.get("background_color"):
        track.default_style.background_color = str(tpl_captions["background_color"])
    if tpl_captions.get("letter_spacing") is not None:
        try:
            track.default_style.letter_spacing = float(tpl_captions["letter_spacing"])
        except (ValueError, TypeError):
            pass
    if tpl_captions.get("line_height") is not None:
        try:
            track.default_style.line_height = float(tpl_captions["line_height"])
        except (ValueError, TypeError):
            pass

    if tpl_captions.get("position_y") is not None:
        try:
            track.default_layout.position_y = float(tpl_captions["position_y"])
        except (ValueError, TypeError):
            pass

    if tpl_captions.get("animation"):
        track.default_animation.type = str(tpl_captions["animation"])

    for grp in track.groups:
        grp.style = track.default_style
        grp.layout = track.default_layout
        grp.animation = track.default_animation

    return track
