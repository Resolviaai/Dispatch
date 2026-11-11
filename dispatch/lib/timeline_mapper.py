"""Canonical typed coordinate spaces and timeline mapping contract (EDL v1 / RFC-8785).

Provides strongly typed coordinate spaces (`SourceTime`, `ProxyTime`, `ConcatTime`),
`PlaybackDescriptor`, explicit non-snapping gap / bounds results, and unified RFC-8785
canonical JSON + fingerprinting matching TypeScript, Cloud Worker, and Kotlin.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, List, Literal, NewType, Optional, Union

from dispatch.segments import (
    RFC8785_VERSION,
    SegmentError,
    _TIME_EPSILON,
    c2_to_c3_time,
    c3_to_c2_time,
    canonical_json_dumps,
    canonical_media_spans,
    canonical_spans_str,
    compute_media_fingerprint,
    compute_render_fingerprint,
    round_ms,
)

TIMELINE_CONTRACT_VERSION = 1
CANONICALIZER_VERSION = RFC8785_VERSION
TimelineMapperError = SegmentError

SourceTime = NewType("SourceTime", float)
ProxyTime = NewType("ProxyTime", float)
ConcatTime = NewType("ConcatTime", float)

SnapGapMode = Literal["none", "next", "previous", "nearest"]


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimelineMapperError(f"{field} must be a finite number")
    out = float(value)
    if not math.isfinite(out):
        raise TimelineMapperError(f"{field} must be a finite number")
    return 0.0 if out == 0.0 else out


def as_source_time(seconds: Any) -> SourceTime:
    """Validate and wrap a finite number in the master SourceTime coordinate space."""
    return SourceTime(_finite_float(seconds, "source_time"))


def as_proxy_time(seconds: Any) -> ProxyTime:
    """Validate and wrap a finite number in the edit-window ProxyTime coordinate space."""
    return ProxyTime(_finite_float(seconds, "proxy_time"))


def as_concat_time(seconds: Any) -> ConcatTime:
    """Validate and wrap a finite number in the concatenated ConcatTime coordinate space."""
    return ConcatTime(_finite_float(seconds, "concat_time"))


@dataclass(frozen=True)
class EdlSegment:
    """One active kept segment in source-time coordinates."""

    id: str
    source_start: SourceTime
    source_end: SourceTime


@dataclass(frozen=True)
class SourceVideoDescriptor:
    """Playback directly against the master source recording (SourceTime)."""

    source_duration: Optional[float] = None
    kind: Literal["source_video"] = "source_video"


@dataclass(frozen=True)
class EditWindowDescriptor:
    """Playback against a bounded edit-window proxy (ProxyTime)."""

    window_start: SourceTime
    window_end: SourceTime
    kind: Literal["edit_window"] = "edit_window"


@dataclass(frozen=True)
class RenderedClipDescriptor:
    """Playback against a concatenated rendered EDL output (ConcatTime)."""

    segments: Sequence[Any]
    kind: Literal["rendered_clip"] = "rendered_clip"


PlaybackDescriptor = Union[
    SourceVideoDescriptor,
    EditWindowDescriptor,
    RenderedClipDescriptor,
]


def descriptor_from_dict(raw: Mapping[str, Any]) -> PlaybackDescriptor:
    """Construct a typed PlaybackDescriptor from a JSON-like mapping."""
    if not isinstance(raw, Mapping):
        raise TimelineMapperError("playback descriptor must be a mapping")
    kind = raw.get("kind")
    if kind == "source_video":
        dur = raw.get("sourceDuration", raw.get("source_duration"))
        return SourceVideoDescriptor(
            source_duration=_finite_float(dur, "source_duration") if dur is not None else None
        )
    if kind == "edit_window":
        ws = raw.get("windowStart", raw.get("window_start"))
        we = raw.get("windowEnd", raw.get("window_end"))
        if ws is None or we is None:
            raise TimelineMapperError("edit_window descriptor requires windowStart and windowEnd")
        return EditWindowDescriptor(
            window_start=as_source_time(ws),
            window_end=as_source_time(we),
        )
    if kind == "rendered_clip":
        segs = raw.get("segments")
        if segs is None:
            raise TimelineMapperError("rendered_clip descriptor requires segments")
        return RenderedClipDescriptor(segments=list(segs))
    raise TimelineMapperError(f"unsupported PlaybackDescriptor kind: {kind!r}")


@dataclass(frozen=True)
class SourceToConcatOk:
    """Successful SourceTime -> ConcatTime mapping."""

    time: ConcatTime
    segment_index: int
    snapped: bool = False
    kind: Literal["ok"] = "ok"


@dataclass(frozen=True)
class SourceToConcatGap:
    """Explicit unmappable gap result when SourceTime lies outside kept segments."""

    reason: Literal["deleted_gap", "before_first_segment", "after_last_segment"]
    source_time: SourceTime
    nearest_segment_start: Optional[SourceTime]
    nearest_segment_end: Optional[SourceTime]
    nearest_concat_time: ConcatTime
    kind: Literal["gap"] = "gap"


SourceToConcatResult = Union[SourceToConcatOk, SourceToConcatGap]


@dataclass(frozen=True)
class ConcatToSourceOk:
    """Successful ConcatTime -> SourceTime mapping."""

    time: SourceTime
    segment_index: int
    clamped: bool = False
    kind: Literal["ok"] = "ok"


@dataclass(frozen=True)
class ConcatToSourceOutOfRange:
    """Explicit out-of-range result when ConcatTime lies outside [0, kept_duration]."""

    reason: Literal["before_start", "after_end"]
    concat_time: ConcatTime
    min_concat_time: ConcatTime
    max_concat_time: ConcatTime
    clamped_time: SourceTime
    kind: Literal["out_of_range"] = "out_of_range"


ConcatToSourceResult = Union[ConcatToSourceOk, ConcatToSourceOutOfRange]


@dataclass(frozen=True)
class WindowMapOk:
    """Successful edit-window mapping result."""

    time: float
    clamped: bool = False
    kind: Literal["ok"] = "ok"


@dataclass(frozen=True)
class WindowMapOutOfWindow:
    """Explicit out-of-window result when time lies outside [windowStart, windowEnd]."""

    reason: Literal["before_window", "after_window"]
    window_start: SourceTime
    window_end: SourceTime
    clamped_time: float
    kind: Literal["out_of_window"] = "out_of_window"


WindowMappingResult = Union[WindowMapOk, WindowMapOutOfWindow]


@dataclass(frozen=True)
class PlaybackMapOk:
    """Successful PlaybackDescriptor mapping result."""

    time: float
    clamped: bool = False
    snapped: bool = False
    segment_index: Optional[int] = None
    kind: Literal["ok"] = "ok"


@dataclass(frozen=True)
class PlaybackOutOfRange:
    """Explicit out-of-range result for source_video or rendered_clip bounds."""

    reason: Literal["before_start", "after_end"]
    min_time: float
    max_time: Optional[float]
    clamped_time: float
    kind: Literal["out_of_range"] = "out_of_range"


PlaybackMappingResult = Union[
    PlaybackMapOk,
    SourceToConcatGap,
    WindowMapOutOfWindow,
    PlaybackOutOfRange,
]


def _validate_window(window_start: Any, window_end: Any) -> tuple[SourceTime, SourceTime, float]:
    ws = round_ms(_finite_float(window_start, "window_start"))
    we = round_ms(_finite_float(window_end, "window_end"))
    if ws < 0.0:
        raise TimelineMapperError("window_start must be non-negative")
    if we <= ws:
        raise TimelineMapperError("window_end must be greater than window_start")
    return SourceTime(ws), SourceTime(we), round_ms(we - ws)


def normalize_segments(segments: Any) -> List[EdlSegment]:
    """Validate and canonicalize active EDL segments without merging adjacent blocks."""
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)) or len(segments) == 0:
        raise TimelineMapperError("segments must be a non-empty array")

    normalized: List[EdlSegment] = []
    prev_end: Optional[float] = None

    for idx, raw in enumerate(segments):
        seg_id = f"seg-{idx + 1}"
        if isinstance(raw, EdlSegment):
            seg_id = raw.id.strip() or seg_id
            start = round_ms(raw.source_start)
            end = round_ms(raw.source_end)
        elif isinstance(raw, Mapping):
            start_val = raw.get("source_start", raw.get("start"))
            end_val = raw.get("source_end", raw.get("end"))
            if start_val is None or end_val is None:
                raise TimelineMapperError(f"segment {idx} must contain source_start and source_end")
            raw_id = raw.get("id")
            if isinstance(raw_id, str) and raw_id.strip():
                seg_id = raw_id.strip()
            start = round_ms(_finite_float(start_val, f"segments[{idx}].source_start"))
            end = round_ms(_finite_float(end_val, f"segments[{idx}].source_end"))
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 2:
            start = round_ms(_finite_float(raw[0], f"segments[{idx}][0]"))
            end = round_ms(_finite_float(raw[1], f"segments[{idx}][1]"))
        else:
            raise TimelineMapperError(f"segment {idx} must be {{source_start, source_end}} or [start, end]")

        if start < 0.0 or end <= start:
            raise TimelineMapperError(f"segment {idx} has invalid bounds [{start}, {end}]")
        if prev_end is not None and start < prev_end - _TIME_EPSILON:
            raise TimelineMapperError("segments must be sorted and non-overlapping")

        normalized.append(
            EdlSegment(
                id=seg_id,
                source_start=SourceTime(start),
                source_end=SourceTime(end),
            )
        )
        prev_end = end

    return normalized


def kept_duration(segments: Any) -> float:
    """Return total concatenated kept duration across active EDL segments."""
    canonical = normalize_segments(segments)
    return round_ms(sum(seg.source_end - seg.source_start for seg in canonical))


def source_to_concat(
    source_time: Union[SourceTime, float],
    segments: Any,
    *,
    snap_gap_to: SnapGapMode = "none",
) -> SourceToConcatResult:
    """Map SourceTime to ConcatTime; return an explicit gap result inside deleted gaps unless opted in."""
    if snap_gap_to not in ("none", "next", "previous", "nearest"):
        raise TimelineMapperError(f"invalid snap_gap_to option: {snap_gap_to!r}")

    t = round_ms(_finite_float(source_time, "source_time"))
    canonical = normalize_segments(segments)
    offset = 0.0

    for idx, seg in enumerate(canonical):
        if t < seg.source_start - _TIME_EPSILON:
            prev_end: Optional[SourceTime] = canonical[idx - 1].source_end if idx > 0 else None
            next_start: SourceTime = seg.source_start
            reason: Literal["deleted_gap", "before_first_segment"] = (
                "before_first_segment" if idx == 0 else "deleted_gap"
            )
            nearest_concat = ConcatTime(round_ms(offset))

            if snap_gap_to == "next":
                return SourceToConcatOk(time=nearest_concat, segment_index=idx, snapped=True)
            if snap_gap_to == "previous":
                if prev_end is not None:
                    return SourceToConcatOk(time=nearest_concat, segment_index=idx - 1, snapped=True)
            elif snap_gap_to == "nearest":
                if prev_end is None:
                    return SourceToConcatOk(time=nearest_concat, segment_index=0, snapped=True)
                dist_prev = t - prev_end
                dist_next = next_start - t
                target_idx = idx if dist_next <= dist_prev else idx - 1
                return SourceToConcatOk(time=nearest_concat, segment_index=target_idx, snapped=True)

            return SourceToConcatGap(
                reason=reason,
                source_time=SourceTime(t),
                nearest_segment_start=next_start,
                nearest_segment_end=prev_end,
                nearest_concat_time=nearest_concat,
            )

        if t <= seg.source_end + _TIME_EPSILON:
            local = min(max(t - seg.source_start, 0.0), seg.source_end - seg.source_start)
            return SourceToConcatOk(
                time=ConcatTime(round_ms(offset + local)),
                segment_index=idx,
                snapped=False,
            )

        offset = round_ms(offset + (seg.source_end - seg.source_start))

    last_end = canonical[-1].source_end
    total_concat = ConcatTime(round_ms(offset))
    if snap_gap_to in ("previous", "nearest"):
        return SourceToConcatOk(
            time=total_concat,
            segment_index=len(canonical) - 1,
            snapped=True,
        )

    return SourceToConcatGap(
        reason="after_last_segment",
        source_time=SourceTime(t),
        nearest_segment_start=None,
        nearest_segment_end=last_end,
        nearest_concat_time=total_concat,
    )


def concat_to_source(
    concat_time: Union[ConcatTime, float],
    segments: Any,
    *,
    clamp: bool = False,
) -> ConcatToSourceResult:
    """Map ConcatTime back to SourceTime; return explicit out-of-range unless clamp=True."""
    t = round_ms(_finite_float(concat_time, "concat_time"))
    canonical = normalize_segments(segments)
    total = round_ms(sum(seg.source_end - seg.source_start for seg in canonical))

    if t < 0.0 - _TIME_EPSILON:
        if clamp:
            return ConcatToSourceOk(time=canonical[0].source_start, segment_index=0, clamped=True)
        return ConcatToSourceOutOfRange(
            reason="before_start",
            concat_time=ConcatTime(t),
            min_concat_time=ConcatTime(0.0),
            max_concat_time=ConcatTime(total),
            clamped_time=canonical[0].source_start,
        )

    if t > total + _TIME_EPSILON:
        if clamp:
            return ConcatToSourceOk(
                time=canonical[-1].source_end,
                segment_index=len(canonical) - 1,
                clamped=True,
            )
        return ConcatToSourceOutOfRange(
            reason="after_end",
            concat_time=ConcatTime(t),
            min_concat_time=ConcatTime(0.0),
            max_concat_time=ConcatTime(total),
            clamped_time=canonical[-1].source_end,
        )

    bounded = min(max(t, 0.0), total)
    offset = 0.0
    last_idx = len(canonical) - 1
    for idx, seg in enumerate(canonical):
        dur = round_ms(seg.source_end - seg.source_start)
        next_offset = round_ms(offset + dur)
        if bounded < next_offset - _TIME_EPSILON or idx == last_idx:
            local = min(max(bounded - offset, 0.0), dur)
            src = min(seg.source_end, round_ms(seg.source_start + local))
            return ConcatToSourceOk(
                time=SourceTime(src),
                segment_index=idx,
                clamped=False,
            )
        offset = next_offset

    return ConcatToSourceOk(
        time=canonical[-1].source_end,
        segment_index=last_idx,
        clamped=False,
    )


def source_to_proxy(
    source_time: Union[SourceTime, float],
    window_start: Union[SourceTime, float],
    window_end: Union[SourceTime, float],
    *,
    clamp: bool = False,
) -> WindowMappingResult:
    """Map SourceTime to edit-window ProxyTime with explicit out-of-window bounds handling."""
    s = round_ms(_finite_float(source_time, "source_time"))
    ws, we, dur = _validate_window(window_start, window_end)

    if s < ws - _TIME_EPSILON:
        if clamp:
            return WindowMapOk(time=ProxyTime(0.0), clamped=True)
        return WindowMapOutOfWindow(
            reason="before_window",
            window_start=ws,
            window_end=we,
            clamped_time=ProxyTime(0.0),
        )

    if s > we + _TIME_EPSILON:
        if clamp:
            return WindowMapOk(time=ProxyTime(dur), clamped=True)
        return WindowMapOutOfWindow(
            reason="after_window",
            window_start=ws,
            window_end=we,
            clamped_time=ProxyTime(dur),
        )

    bounded_proxy = min(max(round_ms(s - ws), 0.0), dur)
    return WindowMapOk(time=ProxyTime(bounded_proxy), clamped=False)


def proxy_to_source(
    proxy_time: Union[ProxyTime, float],
    window_start: Union[SourceTime, float],
    window_end: Union[SourceTime, float],
    *,
    clamp: bool = False,
) -> WindowMappingResult:
    """Map edit-window ProxyTime back to SourceTime with explicit out-of-window bounds handling."""
    p = round_ms(_finite_float(proxy_time, "proxy_time"))
    ws, we, dur = _validate_window(window_start, window_end)

    if p < 0.0 - _TIME_EPSILON:
        if clamp:
            return WindowMapOk(time=ws, clamped=True)
        return WindowMapOutOfWindow(
            reason="before_window",
            window_start=ws,
            window_end=we,
            clamped_time=ws,
        )

    if p > dur + _TIME_EPSILON:
        if clamp:
            return WindowMapOk(time=we, clamped=True)
        return WindowMapOutOfWindow(
            reason="after_window",
            window_start=ws,
            window_end=we,
            clamped_time=we,
        )

    bounded_src = min(max(round_ms(ws + p), ws), we)
    return WindowMapOk(time=SourceTime(bounded_src), clamped=False)


def playback_time_to_source(
    playback_time: float,
    descriptor: PlaybackDescriptor,
    *,
    clamp: bool = False,
) -> PlaybackMappingResult:
    """Map a player timestamp (parameterized by PlaybackDescriptor) to master SourceTime."""
    if isinstance(descriptor, SourceVideoDescriptor):
        t = round_ms(_finite_float(playback_time, "playback_time"))
        max_dur = (
            round_ms(_finite_float(descriptor.source_duration, "source_duration"))
            if descriptor.source_duration is not None
            else None
        )
        if max_dur is not None and max_dur <= 0.0:
            raise TimelineMapperError("source_duration must be greater than zero")
        if t < 0.0 - _TIME_EPSILON:
            if clamp:
                return PlaybackMapOk(time=SourceTime(0.0), clamped=True)
            return PlaybackOutOfRange(
                reason="before_start",
                min_time=0.0,
                max_time=max_dur,
                clamped_time=SourceTime(0.0),
            )
        if max_dur is not None and t > max_dur + _TIME_EPSILON:
            if clamp:
                return PlaybackMapOk(time=SourceTime(max_dur), clamped=True)
            return PlaybackOutOfRange(
                reason="after_end",
                min_time=0.0,
                max_time=max_dur,
                clamped_time=SourceTime(max_dur),
            )
        bounded = max(t, 0.0)
        if max_dur is not None:
            bounded = min(bounded, max_dur)
        return PlaybackMapOk(time=SourceTime(round_ms(bounded)), clamped=False)

    if isinstance(descriptor, EditWindowDescriptor):
        res = proxy_to_source(
            as_proxy_time(playback_time),
            descriptor.window_start,
            descriptor.window_end,
            clamp=clamp,
        )
        if isinstance(res, WindowMapOk):
            return PlaybackMapOk(time=res.time, clamped=res.clamped)
        return res

    if isinstance(descriptor, RenderedClipDescriptor):
        res = concat_to_source(
            as_concat_time(playback_time),
            descriptor.segments,
            clamp=clamp,
        )
        if isinstance(res, ConcatToSourceOk):
            return PlaybackMapOk(
                time=res.time,
                segment_index=res.segment_index,
                clamped=res.clamped,
            )
        return PlaybackOutOfRange(
            reason=res.reason,
            min_time=res.min_concat_time,
            max_time=res.max_concat_time,
            clamped_time=res.clamped_time,
        )

    raise TimelineMapperError(f"unsupported PlaybackDescriptor: {descriptor!r}")


def source_to_playback_time(
    source_time: Union[SourceTime, float],
    descriptor: PlaybackDescriptor,
    *,
    clamp: bool = False,
    snap_gap_to: SnapGapMode = "none",
) -> PlaybackMappingResult:
    """Map master SourceTime to a player's native time coordinate (parameterized by PlaybackDescriptor)."""
    if isinstance(descriptor, SourceVideoDescriptor):
        t = round_ms(_finite_float(source_time, "source_time"))
        max_dur = (
            round_ms(_finite_float(descriptor.source_duration, "source_duration"))
            if descriptor.source_duration is not None
            else None
        )
        if max_dur is not None and max_dur <= 0.0:
            raise TimelineMapperError("source_duration must be greater than zero")
        if t < 0.0 - _TIME_EPSILON:
            if clamp:
                return PlaybackMapOk(time=SourceTime(0.0), clamped=True)
            return PlaybackOutOfRange(
                reason="before_start",
                min_time=0.0,
                max_time=max_dur,
                clamped_time=SourceTime(0.0),
            )
        if max_dur is not None and t > max_dur + _TIME_EPSILON:
            if clamp:
                return PlaybackMapOk(time=SourceTime(max_dur), clamped=True)
            return PlaybackOutOfRange(
                reason="after_end",
                min_time=0.0,
                max_time=max_dur,
                clamped_time=SourceTime(max_dur),
            )
        bounded = max(t, 0.0)
        if max_dur is not None:
            bounded = min(bounded, max_dur)
        return PlaybackMapOk(time=SourceTime(round_ms(bounded)), clamped=False)

    if isinstance(descriptor, EditWindowDescriptor):
        res = source_to_proxy(
            as_source_time(source_time),
            descriptor.window_start,
            descriptor.window_end,
            clamp=clamp,
        )
        if isinstance(res, WindowMapOk):
            return PlaybackMapOk(time=res.time, clamped=res.clamped)
        return res

    if isinstance(descriptor, RenderedClipDescriptor):
        res = source_to_concat(
            as_source_time(source_time),
            descriptor.segments,
            snap_gap_to=snap_gap_to,
        )
        if isinstance(res, SourceToConcatOk):
            return PlaybackMapOk(
                time=res.time,
                segment_index=res.segment_index,
                snapped=res.snapped,
            )
        return res

    raise TimelineMapperError(f"unsupported PlaybackDescriptor: {descriptor!r}")


class TimelineMapper:
    """Namespace class exposing canonical cross-runtime timeline mapping & fingerprinting APIs."""

    VERSION = TIMELINE_CONTRACT_VERSION
    CANONICALIZER_VERSION = CANONICALIZER_VERSION

    round_ms = staticmethod(round_ms)
    as_source_time = staticmethod(as_source_time)
    as_proxy_time = staticmethod(as_proxy_time)
    as_concat_time = staticmethod(as_concat_time)
    normalize_segments = staticmethod(normalize_segments)
    kept_duration = staticmethod(kept_duration)
    source_to_concat = staticmethod(source_to_concat)
    concat_to_source = staticmethod(concat_to_source)
    source_to_proxy = staticmethod(source_to_proxy)
    proxy_to_source = staticmethod(proxy_to_source)
    c2_to_c3_time = staticmethod(c2_to_c3_time)
    c3_to_c2_time = staticmethod(c3_to_c2_time)
    playback_time_to_source = staticmethod(playback_time_to_source)
    source_to_playback_time = staticmethod(source_to_playback_time)
    canonical_json_dumps = staticmethod(canonical_json_dumps)
    canonical_media_spans = staticmethod(canonical_media_spans)
    canonical_spans_str = staticmethod(canonical_spans_str)
    compute_media_fingerprint = staticmethod(compute_media_fingerprint)
    compute_render_fingerprint = staticmethod(compute_render_fingerprint)
