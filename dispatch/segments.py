"""Pure, deterministic timeline mathematics for Dispatch EDL v1.

This module has no database, renderer, filesystem, or UI dependencies. All ranges
are source-time, half-open intervals [start, end). The exported mapping helpers
handle exact boundaries explicitly so Python and TypeScript can share fixtures.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Dict, List, Optional, Tuple

TimeRange = Tuple[float, float]
MIN_PIECE_DURATION = 0.1
MIN_TOTAL_DURATION = 5.0
MAX_TOTAL_DURATION = 180.0
SOURCE_BOUNDS_EPSILON = 0.01
_TIME_EPSILON = 1e-9
EDL_VERSION = 1


class SegmentError(ValueError):
    """Raised when an EDL range or timeline operation violates an invariant."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SegmentError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SegmentError(f"{field} must be a finite number")
    return result


def _duration(source_duration: Any) -> float:
    value = _finite_number(source_duration, "source_duration")
    if value <= 0:
        raise SegmentError("source_duration must be greater than zero")
    return value


def _read_range(raw: Any, index: int) -> TimeRange:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
        raise SegmentError(f"range {index} must contain exactly [start, end]")
    start = _finite_number(raw[0], f"ranges[{index}][0]")
    end = _finite_number(raw[1], f"ranges[{index}][1]")
    return start, end


def normalize_ranges(
    ranges: Any,
    source_duration: float,
    *,
    enforce_total: bool = True,
) -> List[TimeRange]:
    """Validate and canonicalize source ranges without sorting or merging them.

    Bounds within SOURCE_BOUNDS_EPSILON are clamped to the source boundary.
    Overlapping or unsorted ranges are rejected; adjacency is preserved.
    """
    source_end = _duration(source_duration)
    if not isinstance(ranges, Sequence) or isinstance(ranges, (str, bytes)) or not ranges:
        raise SegmentError("ranges must be a non-empty array")

    normalized: List[TimeRange] = []
    previous_end: Optional[float] = None
    for index, raw in enumerate(ranges):
        start, end = _read_range(raw, index)
        if start < -SOURCE_BOUNDS_EPSILON or end > source_end + SOURCE_BOUNDS_EPSILON:
            raise SegmentError(f"range {index} is outside source bounds")
        if start < 0:
            start = 0.0
        if end > source_end:
            end = source_end
        if end <= start or end - start + _TIME_EPSILON < MIN_PIECE_DURATION:
            raise SegmentError(f"range {index} must be at least {MIN_PIECE_DURATION:.1f}s")
        if previous_end is not None:
            if start < previous_end - _TIME_EPSILON:
                raise SegmentError("ranges must be sorted and non-overlapping")
            if start < previous_end:
                start = previous_end
                if end - start + _TIME_EPSILON < MIN_PIECE_DURATION:
                    raise SegmentError(f"range {index} must be at least {MIN_PIECE_DURATION:.1f}s")
        normalized.append((start, end))
        previous_end = end

    if enforce_total:
        total = kept_duration(normalized)
        if total + _TIME_EPSILON < MIN_TOTAL_DURATION:
            raise SegmentError(f"total kept duration must be at least {MIN_TOTAL_DURATION:.1f}s")
        if total - _TIME_EPSILON > MAX_TOTAL_DURATION:
            raise SegmentError(f"total kept duration must not exceed {MAX_TOTAL_DURATION:.1f}s")
    return normalized


def kept_duration(ranges: Any, source_duration: Optional[float] = None) -> float:
    """Return the sum of kept source durations (never the source-time hull)."""
    if source_duration is not None:
        canonical = normalize_ranges(ranges, source_duration)
    else:
        if not isinstance(ranges, Sequence) or isinstance(ranges, (str, bytes)) or not ranges:
            raise SegmentError("ranges must be a non-empty array")
        canonical = []
        previous_end: Optional[float] = None
        for index, raw in enumerate(ranges):
            start, end = _read_range(raw, index)
            if end <= start or end - start + _TIME_EPSILON < MIN_PIECE_DURATION:
                raise SegmentError(f"range {index} must be at least {MIN_PIECE_DURATION:.1f}s")
            if previous_end is not None and start < previous_end - _TIME_EPSILON:
                raise SegmentError("ranges must be sorted and non-overlapping")
            canonical.append((start, end))
            previous_end = end
    return sum(end - start for start, end in canonical)


def split_at(ranges: Any, source_time: float, source_duration: float) -> List[TimeRange]:
    """Split one kept range at a source timestamp; footage and total duration do not change."""
    canonical = normalize_ranges(ranges, source_duration)
    point = _finite_number(source_time, "source_time")
    result: List[TimeRange] = []
    split_done = False
    for start, end in canonical:
        if start + MIN_PIECE_DURATION - _TIME_EPSILON <= point <= end - MIN_PIECE_DURATION + _TIME_EPSILON:
            if point <= start + _TIME_EPSILON or point >= end - _TIME_EPSILON:
                raise SegmentError("split point must be inside a segment")
            result.extend(((start, point), (point, end)))
            split_done = True
        else:
            result.append((start, end))
    if not split_done:
        raise SegmentError("split point must leave at least 0.1s on both sides of a kept segment")
    validated = normalize_ranges(result, source_duration)
    if not math.isclose(kept_duration(validated), kept_duration(canonical), rel_tol=0.0, abs_tol=1e-8):
        raise SegmentError("split must not change total kept duration")
    return validated


def trim_edge(
    ranges: Any,
    index: int,
    edge: str,
    new_source_time: float,
    source_duration: float,
) -> List[TimeRange]:
    """Move one edge of one segment, allowing expansion into gaps but never overlap."""
    canonical = normalize_ranges(ranges, source_duration)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(canonical):
        raise SegmentError("segment index is out of range")
    if edge not in ("start", "end"):
        raise SegmentError("edge must be 'start' or 'end'")
    point = _finite_number(new_source_time, "new_source_time")
    start, end = canonical[index]
    previous_end = canonical[index - 1][1] if index > 0 else 0.0
    next_start = canonical[index + 1][0] if index + 1 < len(canonical) else _duration(source_duration)

    if edge == "start":
        if point < previous_end - _TIME_EPSILON or point > end - MIN_PIECE_DURATION + _TIME_EPSILON:
            raise SegmentError("trimmed start would overlap a neighbor or make the segment too short")
        canonical[index] = (max(previous_end, point), end)
    else:
        if point > next_start + _TIME_EPSILON or point < start + MIN_PIECE_DURATION - _TIME_EPSILON:
            raise SegmentError("trimmed end would overlap a neighbor or make the segment too short")
        canonical[index] = (start, min(next_start, point))
    return normalize_ranges(canonical, source_duration)


def delete_piece(ranges: Any, index: int, source_duration: float) -> List[TimeRange]:
    """Delete one segment; remaining blocks stay distinct and concatenate on output."""
    canonical = normalize_ranges(ranges, source_duration)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(canonical):
        raise SegmentError("segment index is out of range")
    if len(canonical) == 1:
        raise SegmentError("cannot delete the final kept segment; delete the logical clip instead")
    remaining = canonical[:index] + canonical[index + 1:]
    return normalize_ranges(remaining, source_duration)


def source_to_concat(ranges: Any, source_time: float, source_duration: float) -> Optional[float]:
    """Map a source timestamp to output time; return None inside removed footage.

    Segment intervals are half-open for playback. Exact source endpoints are
    mappable: a kept segment's end maps to its output boundary, and the next
    segment's start maps to the same boundary when separated by a source gap.
    """
    canonical = normalize_ranges(ranges, source_duration)
    point = _finite_number(source_time, "source_time")
    offset = 0.0
    for start, end in canonical:
        if start - _TIME_EPSILON <= point <= end + _TIME_EPSILON:
            return offset + min(max(point - start, 0.0), end - start)
        offset += end - start
    return None


def concat_to_source(ranges: Any, concat_time: float, source_duration: float) -> float:
    """Map output time to source time; at internal boundaries prefer the next segment."""
    canonical = normalize_ranges(ranges, source_duration)
    point = _finite_number(concat_time, "concat_time")
    total = kept_duration(canonical)
    if point < -_TIME_EPSILON or point > total + _TIME_EPSILON:
        raise SegmentError("concat_time is outside the kept timeline")
    point = min(max(point, 0.0), total)
    offset = 0.0
    for start, end in canonical:
        duration = end - start
        if point < offset + duration - _TIME_EPSILON:
            return start + max(0.0, point - offset)
        offset += duration
    return canonical[-1][1]


def intersect_source_interval(
    ranges: Any,
    source_start: float,
    source_end: float,
    source_duration: float,
) -> List[Dict[str, float]]:
    """Fragment a source-anchored layer interval at every kept range.

    A layer crossing removed footage returns multiple fragments. This is the
    primitive caption/overlay/audio layers should use; a single point mapping
    cannot represent an interval crossing a gap.
    """
    canonical = normalize_ranges(ranges, source_duration)
    layer_start = _finite_number(source_start, "source_start")
    layer_end = _finite_number(source_end, "source_end")
    if layer_end <= layer_start:
        raise SegmentError("source interval end must be greater than start")

    fragments: List[Dict[str, float]] = []
    offset = 0.0
    for start, end in canonical:
        overlap_start = max(start, layer_start)
        overlap_end = min(end, layer_end)
        if overlap_end - overlap_start > _TIME_EPSILON:
            fragments.append({
                "source_start": overlap_start,
                "source_end": overlap_end,
                "timeline_start": offset + overlap_start - start,
                "timeline_end": offset + overlap_end - start,
            })
        offset += end - start
    return fragments


def timeline_interval_to_source(
    ranges: Any,
    timeline_start: float,
    timeline_end: float,
    source_duration: float,
) -> List[Dict[str, float]]:
    """Map a continuous output-time interval back to one or more source fragments."""
    canonical = normalize_ranges(ranges, source_duration)
    start = _finite_number(timeline_start, "timeline_start")
    end = _finite_number(timeline_end, "timeline_end")
    total = kept_duration(canonical)
    if end <= start or start < -_TIME_EPSILON or end > total + _TIME_EPSILON:
        raise SegmentError("timeline interval is invalid or outside the kept timeline")

    fragments: List[Dict[str, float]] = []
    offset = 0.0
    for source_start, source_end in canonical:
        duration = source_end - source_start
        overlap_start = max(start, offset)
        overlap_end = min(end, offset + duration)
        if overlap_end - overlap_start > _TIME_EPSILON:
            fragments.append({
                "source_start": source_start + overlap_start - offset,
                "source_end": source_start + overlap_end - offset,
                "timeline_start": overlap_start,
                "timeline_end": overlap_end,
            })
        offset += duration
    return fragments


def normalize_edl_document(document: Any) -> Dict[str, Any]:
    """Validate EDL v1 while preserving optional metadata/settings fields."""
    if not isinstance(document, dict):
        raise SegmentError("EDL document must be an object")
    version = document.get("v")
    if isinstance(version, bool) or version != EDL_VERSION:
        raise SegmentError(f"unsupported EDL version: {version!r}")
    asset_id = document.get("source_asset_id")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise SegmentError("source_asset_id must be a non-empty string")
    source_duration = _duration(document.get("source_duration"))
    ranges = normalize_ranges(document.get("ranges"), source_duration)
    result = dict(document)
    result["v"] = EDL_VERSION
    result["source_asset_id"] = asset_id
    result["source_duration"] = source_duration
    result["ranges"] = [[start, end] for start, end in ranges]
    return result
