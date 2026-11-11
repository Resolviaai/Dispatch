"""Pure, deterministic timeline mathematics for Dispatch EDL v1.

This module has no database, renderer, filesystem, or UI dependencies. All ranges
are source-time, half-open intervals [start, end). The exported mapping helpers
handle exact boundaries explicitly so Python and TypeScript can share fixtures.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, Tuple

TimeRange = Tuple[float, float]
MIN_PIECE_DURATION = 0.1
MIN_TOTAL_DURATION = 5.0
MAX_TOTAL_DURATION = 180.0
SOURCE_BOUNDS_EPSILON = 0.01
_TIME_EPSILON = 1e-9
EDL_VERSION = 1
RFC8785_VERSION = "rfc8785-v1"


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
    if isinstance(raw, (dict, Mapping)):
        start_val = raw.get("source_start", raw.get("start"))
        end_val = raw.get("source_end", raw.get("end"))
        if start_val is None or end_val is None:
            raise SegmentError(f"range {index} object must contain source_start/source_end or start/end")
        start = _finite_number(start_val, f"ranges[{index}].source_start")
        end = _finite_number(end_val, f"ranges[{index}].source_end")
        return start, end
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


def round_ms(seconds: Any) -> float:
    """Canonical half-up millisecond rounding: math.floor(x * 1000.0 + 0.5) / 1000.0.

    Guarantees cross-language identical results across Python, TypeScript, Kotlin, and PostgreSQL.
    Normalizes -0.0 to +0.0.
    """
    val = _finite_number(seconds, "seconds")
    rounded = math.floor(val * 1000.0 + 0.5) / 1000.0
    return 0.0 if rounded == 0.0 else rounded


def format_rfc8785_number(value: Any) -> str:
    """Serialize a finite int or float per RFC 8785 §3.2.2.3 / ES6 Number::toString.

    Rejects booleans, NaN, and Infinity, and normalizes -0.0 to "0".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SegmentError(f"JSON number must be a finite int or float, got {type(value).__name__}")
    if isinstance(value, int):
        if -9007199254740991 <= value <= 9007199254740991:
            return "0" if value == 0 else str(value)
        num = float(value)
    else:
        num = float(value)
    if not math.isfinite(num):
        raise SegmentError("non-finite numbers (NaN/Infinity) are not valid in RFC-8785 JSON")
    if num == 0.0:
        return "0"

    sign = "-" if num < 0.0 else ""
    ax = abs(num)
    s_repr = repr(ax)
    if "e" in s_repr or "E" in s_repr:
        mantissa, exp_str = s_repr.lower().split("e")
        exp = int(exp_str)
        digits = mantissa.replace(".", "").rstrip("0")
        n = exp + 1
    elif "." in s_repr:
        int_part, frac_part = s_repr.split(".")
        if int_part != "0":
            digits = (int_part + frac_part).rstrip("0")
            n = len(int_part)
        else:
            stripped = frac_part.lstrip("0")
            leading_zeros = len(frac_part) - len(stripped)
            digits = stripped.rstrip("0")
            n = -leading_zeros
    else:
        digits = s_repr.rstrip("0")
        n = len(s_repr)

    k = len(digits)
    if k <= n <= 21:
        return sign + digits + ("0" * (n - k))
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + ("0" * (-n)) + digits
    exp_val = n - 1
    exp_sign = "+" if exp_val >= 0 else "-"
    mant = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{sign}{mant}e{exp_sign}{abs(exp_val)}"


def canonical_json_dumps(value: Any) -> str:
    """Serialize any JSON value deterministically according to RFC 8785 (JCS).

    - Object keys sorted recursively by UTF-16 code units (matching ECMAScript .sort()).
    - Compact separators (',' and ':') with no extra whitespace.
    - Raw UTF-8 strings (ensure_ascii=False) with standard JSON control escaping.
    - Clean IEEE-754 number formatting (-0.0 -> "0", 1.0 -> "1", ES6 thresholds).
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format_rfc8785_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json_dumps(item) for item in value) + "]"
    if isinstance(value, Mapping):
        pairs = []
        for k, v in value.items():
            key_str = k if isinstance(k, str) else str(k)
            pairs.append((key_str, v))
        pairs.sort(key=lambda item: item[0].encode("utf-16-be"))
        return "{" + ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{canonical_json_dumps(v)}"
            for k, v in pairs
        ) + "}"
    raise SegmentError(f"unsupported type for RFC-8785 canonical JSON: {type(value).__name__}")


def canonical_media_spans(ranges: Any, source_duration: Optional[float] = None) -> List[TimeRange]:
    """Merges contiguous adjacent intervals into continuous source spans at 1ms precision."""
    if source_duration is not None:
        normalized = normalize_ranges(ranges, source_duration)
    else:
        if not isinstance(ranges, Sequence) or isinstance(ranges, (str, bytes)):
            raise SegmentError("ranges must be a list of [start, end] pairs or segment dicts")
        normalized = []
        for item in ranges:
            if isinstance(item, (dict, Mapping)):
                start_val = item.get("source_start", item.get("start"))
                end_val = item.get("source_end", item.get("end"))
                if start_val is None or end_val is None:
                    raise SegmentError("range element dict must contain source_start/source_end or start/end")
                s = round_ms(start_val)
                e = round_ms(end_val)
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
                s = round_ms(item[0])
                e = round_ms(item[1])
            else:
                raise SegmentError("range element must be a [start, end] pair or segment dict")
            if s < 0 or e <= s:
                raise SegmentError(f"invalid segment bounds: [{s}, {e}]")
            normalized.append((s, e))

    if not normalized:
        return []

    sorted_ranges = sorted(normalized, key=lambda x: x[0])
    merged: List[TimeRange] = [sorted_ranges[0]]
    for s, e in sorted_ranges[1:]:
        prev_s, prev_e = merged[-1]
        if s < prev_e - _TIME_EPSILON:
            raise SegmentError(f"overlapping segments detected: [{prev_s}, {prev_e}] and [{s}, {e}]")
        elif abs(s - prev_e) <= 1e-6:
            merged[-1] = (prev_s, e)
        else:
            merged.append((s, e))
    return [(round_ms(s), round_ms(e)) for s, e in merged]


def canonical_spans_str(ranges: Any, source_duration: Optional[float] = None) -> str:
    """Canonical string representation of contiguous spans e.g. [10.000-30.000]."""
    spans = canonical_media_spans(ranges, source_duration)
    return "[" + ",".join(f"{s:.3f}-{e:.3f}" for s, e in spans) + "]"


def compute_media_fingerprint(source_asset_id: str, ranges: Any, source_duration: Optional[float] = None) -> str:
    """Computes canonical media_fingerprint from source asset ID and canonical spans."""
    if not isinstance(source_asset_id, str) or not source_asset_id.strip():
        raise SegmentError("source_asset_id must be a non-empty string")
    spans_str = canonical_spans_str(ranges, source_duration)
    payload = f"v1:media:{source_asset_id.strip()}:{spans_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_render_fingerprint(
    media_fingerprint: str,
    layout_mode: str,
    crop_anchor_x: float,
    layers: Optional[Sequence[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> str:
    """Computes canonical render_fingerprint incorporating media identity, layout, crop, and all layers.

    Uses RFC 8785 (JCS) canonical JSON serialization for layers and settings so Python,
    TypeScript, Cloud Worker, and Kotlin produce byte-identical hashes.
    """
    if not isinstance(media_fingerprint, str) or len(media_fingerprint) != 64:
        raise SegmentError("media_fingerprint must be a valid 64-character SHA-256 hex string")
    layout = str(layout_mode).strip() if layout_mode is not None else ""
    if not layout:
        layout = "portrait_9_16"
    crop_str = f"{round_ms(crop_anchor_x):.3f}"

    layers_clean = list(layers) if layers is not None else []
    layers_str = canonical_json_dumps(layers_clean)
    layers_hash = hashlib.sha256(layers_str.encode("utf-8")).hexdigest()

    settings_clean = dict(settings) if settings is not None else {}
    settings_str = canonical_json_dumps(settings_clean)
    settings_hash = hashlib.sha256(settings_str.encode("utf-8")).hexdigest()

    payload = f"v1:render:{media_fingerprint}:{layout}:{crop_str}:{layers_hash}"
    if settings_str != "{}":
        payload += f":{settings_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fragment_layer_interval(layer_interval: Sequence[float], deleted_gap: Sequence[float]) -> List[TimeRange]:
    """Slices a layer interval across a deleted gap, preserving source timestamps."""
    if len(layer_interval) != 2 or len(deleted_gap) != 2:
        raise SegmentError("layer_interval and deleted_gap must be [start, end] pairs")
    ls, le = round_ms(layer_interval[0]), round_ms(layer_interval[1])
    gs, ge = round_ms(deleted_gap[0]), round_ms(deleted_gap[1])
    if ls >= le or gs >= ge:
        raise SegmentError("intervals must have start < end")

    if le <= gs:
        return [(ls, le)]
    if ls >= ge:
        return [(ls, le)]
    if ls >= gs and le <= ge:
        return []
    if ls < gs and le <= ge:
        return [(ls, gs)]
    if ls >= gs and le > ge:
        return [(ge, le)]
    if ls < gs and le > ge:
        return [(ls, gs), (ge, le)]
    return []


def c2_to_c3_time(
    proxy_time: float,
    window_start: float,
    window_end: Optional[float] = None,
) -> float:
    """Maps C2 proxy playback time to C3 master source seconds with explicit window bounds checks."""
    p = round_ms(_finite_number(proxy_time, "proxy_time"))
    ws = round_ms(_finite_number(window_start, "window_start"))
    if ws < 0.0:
        raise SegmentError("window_start must be non-negative")
    if p < 0.0:
        raise SegmentError(f"proxy_time {p} is before edit window start (0.0)")
    if window_end is not None:
        we = round_ms(_finite_number(window_end, "window_end"))
        if we <= ws:
            raise SegmentError("window_end must be greater than window_start")
        window_dur = round_ms(we - ws)
        if p > window_dur + 1e-6:
            raise SegmentError(f"proxy_time {p} exceeds edit window duration {window_dur}")
        mapped = round_ms(ws + p)
        return we if mapped > we else mapped
    return round_ms(ws + p)


def c3_to_c2_time(
    source_time: float,
    window_start: float,
    window_end: Optional[float] = None,
) -> float:
    """Maps C3 master source seconds to C2 proxy playback time with explicit window bounds checks."""
    s = round_ms(_finite_number(source_time, "source_time"))
    ws = round_ms(_finite_number(window_start, "window_start"))
    if ws < 0.0:
        raise SegmentError("window_start must be non-negative")
    if s < ws - 1e-6:
        raise SegmentError(f"source_time {s} is before edit window start {ws}")
    if window_end is not None:
        we = round_ms(_finite_number(window_end, "window_end"))
        if we <= ws:
            raise SegmentError("window_end must be greater than window_start")
        if s > we + 1e-6:
            raise SegmentError(f"source_time {s} exceeds edit window end {we}")
    mapped = round_ms(s - ws)
    return 0.0 if mapped < 0.0 else mapped
