"""Silence Detection, Removal, and Timeline Synchronization Module.
Provides intelligent silence analysis for candidate clips, allowing longer source intervals
to qualify if genuine dead-air silences are removed, while strictly preserving meaningful pauses,
comedic timing, dramatic tension, and subtitle synchronization.
"""
import copy
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("dispatch.video_engine.silence")

# Threshold above which a pause is considered dead-air / prolonged silence (seconds)
DEFAULT_SILENCE_THRESHOLD = 3.0

# Natural pause duration to retain across a trimmed dead-air gap (seconds)
DEFAULT_TARGET_PAUSE = 0.8


def analyze_silence_gaps(
    start_time: float,
    end_time: float,
    segments: Optional[List[Dict[str, Any]]],
    silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
    target_pause: float = DEFAULT_TARGET_PAUSE
) -> Tuple[float, List[Tuple[float, float, float]]]:
    """Analyze silence gaps within [start_time, end_time] and compute effective duration.

    Invariants:
      1. Conversational pauses (< 2.0s) and thinking/dramatic pauses (< silence_threshold)
         are fully preserved to respect speaker cadence and intentional delivery.
      2. Dead air gaps (>= silence_threshold) are trimmed down to target_pause, never
         cut abruptly to 0.0s, to prevent jarring audio jumps.
      3. Returns (effective_duration, cuts) where each cut is:
         (cut_start, cut_end, removed_duration).
    """
    raw_duration = max(0.0, end_time - start_time)
    if not segments or raw_duration <= 0.0:
        return raw_duration, []

    # Extract all active speech intervals within [start_time, end_time]
    speech_intervals: List[Tuple[float, float]] = []
    for s in segments:
        s_start = float(s.get("start", 0.0))
        s_end = float(s.get("end", 0.0))
        if s_end <= start_time or s_start >= end_time:
            continue

        words = s.get("words", [])
        text_words = s.get("text", "").split()
        if words and len(words) >= max(1, int(len(text_words) * 0.75)):
            for w in words:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                if w_end > start_time and w_start < end_time:
                    clamped_s = max(start_time, w_start)
                    clamped_e = min(end_time, w_end)
                    if clamped_e > clamped_s:
                        speech_intervals.append((clamped_s, clamped_e))
        else:
            clamped_s = max(start_time, s_start)
            clamped_e = min(end_time, s_end)
            if clamped_e > clamped_s:
                speech_intervals.append((clamped_s, clamped_e))

    if not speech_intervals:
        return raw_duration, []

    # Sort and merge overlapping or tightly adjacent speech intervals (< 0.2s)
    speech_intervals.sort(key=lambda x: x[0])
    merged_speech: List[Tuple[float, float]] = []
    cur_s, cur_e = speech_intervals[0]
    for nxt_s, nxt_e in speech_intervals[1:]:
        if nxt_s <= cur_e + 0.20:
            cur_e = max(cur_e, nxt_e)
        else:
            merged_speech.append((cur_s, cur_e))
            cur_s, cur_e = nxt_s, nxt_e
    merged_speech.append((cur_s, cur_e))

    cuts: List[Tuple[float, float, float]] = []
    total_removed = 0.0

    # 1. Check leading silence before the first speech
    first_speech_start = merged_speech[0][0]
    leading_gap = first_speech_start - start_time
    if leading_gap >= silence_threshold:
        rem_dur = leading_gap - target_pause
        cut_start = start_time
        cut_end = start_time + rem_dur
        cuts.append((cut_start, cut_end, rem_dur))
        total_removed += rem_dur

    # 2. Check gaps between consecutive speech blocks
    for i in range(len(merged_speech) - 1):
        gap_start = merged_speech[i][1]
        gap_end = merged_speech[i + 1][0]
        gap_duration = gap_end - gap_start
        if gap_duration >= silence_threshold:
            rem_dur = gap_duration - target_pause
            # Leave target_pause / 2 after speech and target_pause / 2 before next speech
            margin = target_pause / 2.0
            cut_start = gap_start + margin
            cut_end = gap_end - margin
            if cut_end > cut_start:
                actual_cut_dur = cut_end - cut_start
                cuts.append((cut_start, cut_end, actual_cut_dur))
                total_removed += actual_cut_dur

    # 3. Check trailing silence after the last speech
    last_speech_end = merged_speech[-1][1]
    trailing_gap = end_time - last_speech_end
    if trailing_gap >= silence_threshold:
        rem_dur = trailing_gap - target_pause
        cut_start = end_time - rem_dur
        cut_end = end_time
        cuts.append((cut_start, cut_end, rem_dur))
        total_removed += rem_dur

    effective_duration = max(0.0, round(raw_duration - total_removed, 2))
    return effective_duration, cuts


def remap_timestamps_for_silence_cuts(
    segments: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    cuts: List[Tuple[float, float, float]]
) -> List[Dict[str, Any]]:
    """Remap transcript segment and word timestamps to match the silence-trimmed timeline.

    Ensures subtitles remain frame-accurate and strictly synchronized after dead-air cuts.
    """
    if not segments:
        return []

    if not cuts:
        return copy.deepcopy(segments)

    def _remap_point(t: float) -> float:
        shift = 0.0
        for cut_start, cut_end, cut_dur in cuts:
            if t >= cut_end:
                shift += cut_dur
            elif t > cut_start:
                # Inside the cut: smoothly compress
                offset_in_cut = t - cut_start
                cut_span = cut_end - cut_start
                ratio = offset_in_cut / cut_span if cut_span > 0 else 0.0
                shift += (cut_dur * ratio)
                break
            else:
                break
        return max(0.0, t - shift)

    remapped_segments = []
    for s in segments:
        s_start = float(s.get("start", 0.0))
        s_end = float(s.get("end", 0.0))
        if s_end <= clip_start or s_start >= clip_end:
            continue

        new_s = copy.deepcopy(s)
        new_s["start"] = _remap_point(s_start)
        new_s["end"] = _remap_point(s_end)

        if "words" in new_s and new_s["words"]:
            for w in new_s["words"]:
                w_start = float(w.get("start", 0.0))
                w_end = float(w.get("end", 0.0))
                w["start"] = _remap_point(w_start)
                w["end"] = _remap_point(w_end)

        remapped_segments.append(new_s)

    return remapped_segments


def build_silence_keep_intervals(
    start_time: float,
    end_time: float,
    cuts: List[Tuple[float, float, float]]
) -> List[Tuple[float, float]]:
    """Convert silence cuts into a list of keep intervals [(k_start, k_end), ...] for FFmpeg."""
    if not cuts:
        return [(start_time, end_time)]

    keep_intervals: List[Tuple[float, float]] = []
    cur_pos = start_time

    for cut_start, cut_end, _ in sorted(cuts, key=lambda c: c[0]):
        if cut_start > cur_pos:
            keep_intervals.append((cur_pos, cut_start))
        cur_pos = max(cur_pos, cut_end)

    if cur_pos < end_time:
        keep_intervals.append((cur_pos, end_time))

    return [k for k in keep_intervals if k[1] > k[0] + 0.05]


def find_natural_subsection_within_limit(
    start_time: float,
    end_time: float,
    segments: List[Dict[str, Any]],
    max_duration: float = 180.0,
    min_duration: float = 5.0
) -> Optional[Tuple[float, float]]:
    """Find a natural, complete thought subsection that fits within [min_duration, max_duration].

    Used when a candidate thought exceeds 180s even after legitimate silence removal.
    Looks for complete sentence boundaries within the candidate to find a standalone section.
    """
    if not segments:
        return None

    # Filter segments within candidate range
    candidate_segs = [
        s for s in segments
        if float(s.get("end", 0.0)) > start_time and float(s.get("start", 0.0)) < end_time
    ]
    if len(candidate_segs) < 2:
        return None

    # Try natural sentence/segment endings from start_time
    base_start = start_time
    for s in reversed(candidate_segs):
        cand_end = float(s.get("end", 0.0))
        eff_dur, _ = analyze_silence_gaps(base_start, cand_end, candidate_segs)
        if min_duration <= eff_dur <= max_duration:
            # Check punctuation indicating complete sentence (. ! ? or pause)
            text = s.get("text", "").strip()
            if text and text[-1] in (".", "!", "?") or (s is not candidate_segs[-1]):
                return base_start, cand_end

    return None
