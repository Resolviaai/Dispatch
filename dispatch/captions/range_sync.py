"""Keep saved clip-relative captions aligned when a source selection changes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple


def rebase_caption_track(
    saved_track: Dict[str, Any],
    saved_source_start: float,
    selected_start: float,
    selected_end: float,
    transcript_track: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Re-anchor saved clip-relative words once and fill newly selected source time.

    Existing caption content and styling are retained. New transcript words are
    added only when their exact text/time identity is not already in the saved
    track. Phrase-level groups are preserved only when fully inside the range.
    """
    result = deepcopy(saved_track)
    groups: List[Dict[str, Any]] = []
    exact_keys = set()
    timing_warning = False

    def word_key(word: Dict[str, Any], source_start: float) -> tuple:
        return (
            round(source_start + float(word.get("start", 0.0)), 3),
            round(source_start + float(word.get("end", 0.0)), 3),
        )

    for original in saved_track.get("groups", []):
        group = deepcopy(original)
        phrase_timing = not original.get("has_word_timing", True) or original.get("timing_mode") == "phrase"
        words = []
        for word in original.get("words", []):
            start = saved_source_start + float(word.get("start", 0.0))
            end = saved_source_start + float(word.get("end", 0.0))
            if end <= selected_start or start >= selected_end:
                continue
            adjusted = deepcopy(word)
            adjusted["start"] = round(max(start, selected_start) - selected_start, 3)
            adjusted["end"] = round(min(end, selected_end) - selected_start, 3)
            words.append(adjusted)
            exact_keys.add(word_key(adjusted, selected_start))

        if words:
            if phrase_timing and original.get("type", "caption") == "caption":
                timing_warning = True
            group["words"] = words
            group["start"] = min(word["start"] for word in words)
            group["end"] = max(word["end"] for word in words)
            groups.append(group)
        elif not original.get("words"):
            start = saved_source_start + float(original.get("start", 0.0))
            end = saved_source_start + float(original.get("end", 0.0))
            # Non-word layers can be clipped to selection bounds; phrase captions
            # cannot be subdivided without inventing word timings.
            if start >= selected_start and end <= selected_end:
                group["start"] = round(start - selected_start, 3)
                group["end"] = round(end - selected_start, 3)
                groups.append(group)
            elif original.get("type", "caption") == "caption":
                timing_warning = True

    if transcript_track:
        for original in transcript_track.get("groups", []):
            if not original.get("has_word_timing", True) or original.get("timing_mode") == "phrase":
                missing_phrase_words = [
                    word for word in original.get("words", [])
                    if word_key(word, selected_start) not in exact_keys
                ]
                if missing_phrase_words:
                    timing_warning = True
                continue
            new_word_runs: List[List[Dict[str, Any]]] = []
            current_run: List[Dict[str, Any]] = []
            for word in original.get("words", []):
                key = word_key(word, selected_start)
                if key in exact_keys:
                    if current_run:
                        new_word_runs.append(current_run)
                        current_run = []
                    continue
                exact_keys.add(key)
                current_run.append(deepcopy(word))
            if current_run:
                new_word_runs.append(current_run)
            for run_index, new_words in enumerate(new_word_runs):
                group = deepcopy(original)
                if len(new_word_runs) > 1:
                    group["id"] = f"{original.get('id', 'transcript')}-range-{run_index + 1}"
                group["words"] = new_words
                group["start"] = min(word["start"] for word in new_words)
                group["end"] = max(word["end"] for word in new_words)
                groups.append(group)

    groups.sort(key=lambda group: float(group.get("start", 0.0)))
    result["groups"] = groups
    return result, timing_warning
