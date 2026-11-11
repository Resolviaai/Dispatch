"""Keep saved clip-relative captions aligned when a source selection changes."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from dispatch.edit_windows import uncovered_ranges as _edit_window_uncovered_ranges


def compute_word_id(word: Dict[str, Any], source_origin: float = 0.0) -> str:
    """Return existing stable word_id or a deterministic source-anchored word_id."""
    existing = str(word.get("word_id") or "").strip()
    if existing:
        return existing
    raw_id = str(word.get("id") or "").strip()
    start_ms = int(round((source_origin + float(word.get("start", 0.0))) * 1000.0))
    end_ms = int(round((source_origin + float(word.get("end", 0.0))) * 1000.0))
    token = str(word.get("text") or word.get("word") or "").strip().casefold()
    digest = hashlib.sha1(f"{start_ms}:{end_ms}:{token}".encode("utf-8")).hexdigest()[:12]
    return raw_id or f"w_{start_ms}_{digest}"


def _extract_covered_source_spans(
    track: Optional[Dict[str, Any]],
    source_origin: float,
    explicit_start: Optional[float] = None,
    explicit_end: Optional[float] = None,
) -> List[Tuple[float, float]]:
    spans: List[Tuple[float, float]] = []
    if explicit_end is not None and explicit_end > source_origin:
        lo = explicit_start if explicit_start is not None else source_origin
        if explicit_end > lo:
            spans.append((float(lo), float(explicit_end)))
    if not isinstance(track, dict):
        return spans
    for group in track.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        words = group.get("words") or []
        if words:
            for w in words:
                if not isinstance(w, dict):
                    continue
                ws = source_origin + float(w.get("start", 0.0))
                we = source_origin + float(w.get("end", 0.0))
                if we > ws:
                    spans.append((round(ws, 3), round(we, 3)))
        else:
            gs = source_origin + float(group.get("start", 0.0))
            ge = source_origin + float(group.get("end", 0.0))
            if ge > gs:
                spans.append((round(gs, 3), round(ge, 3)))
    return spans


def find_uncovered_caption_ranges(
    saved_track: Optional[Dict[str, Any]],
    saved_source_start: float,
    saved_source_end: Optional[float],
    selected_start: float,
    selected_end: float,
    transcript_track: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Compute source-time gaps inside [selected_start, selected_end] not covered by
    either the saved caption track or the source transcript track."""
    if selected_end <= selected_start:
        return []
    covered: List[Tuple[float, float]] = []
    covered.extend(
        _extract_covered_source_spans(
            saved_track,
            saved_source_start,
            explicit_start=saved_source_start if saved_source_end is not None else None,
            explicit_end=saved_source_end,
        )
    )
    covered.extend(_extract_covered_source_spans(transcript_track, selected_start))
    return _edit_window_uncovered_ranges((float(selected_start), float(selected_end)), covered)


def _normalize_gap_transcription_result(
    raw_gap: Any,
    gap_start: float,
    gap_end: float,
    selected_start: float,
) -> Optional[Dict[str, Any]]:
    """Normalize a Stage-2 gap transcription callback output into a clip-relative group."""
    if not raw_gap:
        return None
    if isinstance(raw_gap, dict) and isinstance(raw_gap.get("groups"), list):
        return raw_gap
    words_in = raw_gap.get("words", []) if isinstance(raw_gap, dict) else (raw_gap if isinstance(raw_gap, list) else [])
    norm_words: List[Dict[str, Any]] = []
    for idx, w in enumerate(words_in):
        if not isinstance(w, dict):
            continue
        text = str(w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        raw_s = float(w.get("start", 0.0))
        raw_e = float(w.get("end", 0.0))
        # Treat timings inside [0, gap_span] as gap-local unless already source-anchored >= gap_start.
        if gap_start > 0.5 and raw_e <= (gap_end - gap_start) + 1.0 and raw_s < gap_start - 0.25:
            src_s = gap_start + raw_s
            src_e = gap_start + raw_e
        else:
            src_s = raw_s
            src_e = raw_e
        if src_e <= selected_start or src_s >= gap_end + 0.05:
            continue
        rel_s = round(max(0.0, src_s - selected_start), 3)
        rel_e = round(max(rel_s + 0.05, src_e - selected_start), 3)
        w_dict: Dict[str, Any] = {
            "id": str(w.get("id") or f"gap_w_{int(round(src_s * 1000))}_{idx}"),
            "word_id": str(w.get("word_id") or compute_word_id({"start": src_s, "end": src_e, "text": text}, 0.0)),
            "text": text,
            "start": rel_s,
            "end": rel_e,
            "is_exact": bool(w.get("is_exact", True)),
        }
        norm_words.append(w_dict)
    if not norm_words:
        return None
    return {
        "groups": [{
            "id": f"gap_{int(round(gap_start * 1000))}_{int(round(gap_end * 1000))}",
            "type": "caption",
            "has_word_timing": all(w.get("is_exact", True) for w in norm_words),
            "timing_mode": "word" if all(w.get("is_exact", True) for w in norm_words) else "phrase",
            "start": norm_words[0]["start"],
            "end": norm_words[-1]["end"],
            "words": norm_words,
        }]
    }


def rebase_caption_track(
    saved_track: Dict[str, Any],
    saved_source_start: float,
    selected_start: float,
    selected_end: float,
    transcript_track: Optional[Dict[str, Any]] = None,
    *,
    saved_source_end: Optional[float] = None,
    gap_transcribe_fn: Optional[Callable[[float, float], Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Re-anchor saved clip-relative words once and fill newly selected source time.

    Existing caption content and styling are retained. Saved words are matched
    by persistent `word_id`/`id` first and by source timestamp fallback second,
    so edited or boundary-clamped words never duplicate or resurrect. Uncovered
    source gaps inside [selected_start, selected_end] are detected via
    `edit_windows.uncovered_ranges()` and optionally filled via `gap_transcribe_fn`.
    """
    active_transcript = deepcopy(transcript_track) if transcript_track else None
    gaps = find_uncovered_caption_ranges(
        saved_track,
        saved_source_start,
        saved_source_end,
        selected_start,
        selected_end,
        active_transcript,
    )
    if gap_transcribe_fn is not None and gaps:
        for gap_start, gap_end in gaps:
            try:
                gap_res = gap_transcribe_fn(gap_start, gap_end)
            except Exception:
                gap_res = None
            normalized_gap = _normalize_gap_transcription_result(gap_res, gap_start, gap_end, selected_start)
            if normalized_gap and normalized_gap.get("groups"):
                if active_transcript is None:
                    active_transcript = {"groups": []}
                active_transcript.setdefault("groups", []).extend(normalized_gap["groups"])
        gaps = find_uncovered_caption_ranges(
            saved_track,
            saved_source_start,
            saved_source_end,
            selected_start,
            selected_end,
            active_transcript,
        )

    result = deepcopy(saved_track)
    groups: List[Dict[str, Any]] = []
    exact_keys = set()
    seen_word_ids = set()
    seen_explicit_ids = set()
    deleted_ids = {str(x) for x in (saved_track.get("deleted_word_ids") or []) if x}
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
            wid = str(adjusted.get("word_id") or "").strip()
            if not wid:
                wid = compute_word_id(word, saved_source_start)
                adjusted["word_id"] = wid
            explicit_id = str(adjusted.get("id") or "").strip()
            if wid in deleted_ids or (explicit_id and explicit_id in deleted_ids):
                continue
            seen_word_ids.add(wid)
            if explicit_id:
                seen_explicit_ids.add(explicit_id)
            exact_keys.add((round(start, 3), round(end, 3)))
            exact_keys.add(word_key(adjusted, selected_start))
            words.append(adjusted)

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

    if active_transcript:
        for original in active_transcript.get("groups", []):
            if not original.get("has_word_timing", True) or original.get("timing_mode") == "phrase":
                missing_phrase_words = [
                    word for word in original.get("words", [])
                    if (
                        word_key(word, selected_start) not in exact_keys
                        and str(word.get("word_id") or "") not in seen_word_ids
                        and str(word.get("id") or "") not in seen_explicit_ids
                    )
                ]
                if missing_phrase_words:
                    timing_warning = True
                continue
            new_word_runs: List[List[Dict[str, Any]]] = []
            current_run: List[Dict[str, Any]] = []
            for word in original.get("words", []):
                key = word_key(word, selected_start)
                t_wid = str(word.get("word_id") or "").strip()
                t_id = str(word.get("id") or "").strip()
                if (
                    (t_wid and (t_wid in seen_word_ids or t_wid in deleted_ids))
                    or (t_id and (t_id in seen_explicit_ids or t_id in deleted_ids))
                    or key in exact_keys
                ):
                    if current_run:
                        new_word_runs.append(current_run)
                        current_run = []
                    continue
                exact_keys.add(key)
                word_copy = deepcopy(word)
                if not t_wid:
                    t_wid = compute_word_id(word_copy, selected_start)
                    word_copy["word_id"] = t_wid
                seen_word_ids.add(t_wid)
                if t_id:
                    seen_explicit_ids.add(t_id)
                current_run.append(word_copy)
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
    result["uncovered_ranges"] = [[round(a, 3), round(b, 3)] for a, b in gaps]
    return result, timing_warning


def sync_caption_range_with_gap_transcription(
    saved_track: Dict[str, Any],
    saved_source_start: float,
    saved_source_end: Optional[float],
    selected_start: float,
    selected_end: float,
    transcript_track: Optional[Dict[str, Any]] = None,
    *,
    gap_transcribe_fn: Optional[Callable[[float, float], Any]] = None,
) -> Tuple[Dict[str, Any], bool, List[Tuple[float, float]]]:
    """Rebase captions and return `(rebased_track, timing_warning, remaining_uncovered_ranges)`."""
    rebased, warning = rebase_caption_track(
        saved_track,
        saved_source_start,
        selected_start,
        selected_end,
        transcript_track,
        saved_source_end=saved_source_end,
        gap_transcribe_fn=gap_transcribe_fn,
    )
    remaining = [(float(pair[0]), float(pair[1])) for pair in rebased.get("uncovered_ranges", [])]
    return rebased, warning, remaining
