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

    # 1. Flatten all words within clip boundary and normalize
    raw_words: List[Dict[str, Any]] = []
    for seg in raw_segments:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))

        if seg_end <= clip_start or seg_start >= clip_end:
            continue

        words = seg.get("words", [])
        if words:
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                text = str(w.get("word", "")).strip()
                if not text:
                    continue
                if w_end <= clip_start or w_start >= clip_end:
                    continue

                # Clamp to clip range and normalize relative to clip start
                rel_start = max(0.0, w_start - clip_start)
                rel_end = max(rel_start + 0.05, min(clip_end - clip_start, w_end - clip_start))

                raw_words.append({
                    "text": text,
                    "start": round(rel_start, 3),
                    "end": round(rel_end, 3),
                    "confidence": float(w.get("confidence", 1.0))
                })
        else:
            # When word timestamps are absent (e.g. YouTube segment-level VTT), preserve honest segment-level timing
            # instead of fabricating synthetic per-word timestamps.
            seg_text = seg.get("text", "").strip()
            if seg_text:
                rel_seg_start = max(0.0, seg_start - clip_start)
                rel_seg_end = max(rel_seg_start + 0.2, min(clip_end - clip_start, seg_end - clip_start))
                raw_words.append({
                    "text": seg_text,
                    "start": round(rel_seg_start, 3),
                    "end": round(rel_seg_end, 3),
                    "confidence": 0.95
                })

    # Sort strictly by monotonic start time
    raw_words.sort(key=lambda x: x["start"])

    # 2. Deterministic Grouping
    groups: List[CaptionGroup] = []
    current_words: List[Word] = []
    current_chars = 0
    prev_end = 0.0

    for w_data in raw_words:
        w_obj = Word(
            text=w_data["text"],
            start=w_data["start"],
            end=w_data["end"],
            confidence=w_data["confidence"]
        )

        pause = w_obj.start - prev_end if prev_end > 0 else 0.0
        should_split = False

        # Split condition 1: Exceeded words per group
        if len(current_words) >= words_per_group:
            should_split = True
        # Split condition 2: Exceeded character budget
        elif current_chars + len(w_obj.text) + 1 > max_chars_per_line and len(current_words) >= 2:
            should_split = True
        # Split condition 3: Natural pause in speech
        elif pause >= max_pause_threshold and len(current_words) > 0:
            should_split = True

        if should_split and current_words:
            grp = CaptionGroup(
                start=current_words[0].start,
                end=current_words[-1].end,
                words=current_words,
                layout=default_layout,
                style=default_style,
                animation=default_anim
            )
            groups.append(grp)
            current_words = []
            current_chars = 0

        current_words.append(w_obj)
        current_chars += len(w_obj.text) + 1
        prev_end = w_obj.end

    # Flush remaining words
    if current_words:
        grp = CaptionGroup(
            start=current_words[0].start,
            end=current_words[-1].end,
            words=current_words,
            layout=default_layout,
            style=default_style,
            animation=default_anim
        )
        groups.append(grp)

    # 3. Post-process to eliminate micro-gaps for buttery smooth karaoke playback
    for idx, grp in enumerate(groups):
        if idx < len(groups) - 1:
            next_start = groups[idx + 1].start
            # If gap between cards is small (<0.25s), stretch card end so text doesn't flicker out
            if next_start - grp.end < 0.25 and next_start > grp.end:
                grp.end = next_start
                if grp.words:
                    grp.words[-1].end = next_start

    return CaptionTrack(
        clip_id=clip_id,
        language="en",
        groups=groups,
        default_style=default_style,
        default_layout=default_layout,
        default_animation=default_anim
    )
