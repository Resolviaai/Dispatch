/**
 * Cloud Worker canonical timeline contract, typed coordinate mapper, and RFC-8785 fingerprinting.
 *
 * Pure TypeScript compatible with Cloudflare Workers, Wrangler, and Node --experimental-strip-types.
 * Mirrored identically by:
 *   - frontend/src/lib/timelineMapper.ts
 *   - dispatch/segments.py & dispatch/lib/timeline_mapper.py
 *   - android/.../timeline/TimelineMapper.kt
 */

export const TIMELINE_CONTRACT_VERSION = 1 as const;
export const CANONICALIZER_VERSION = "rfc8785-v1" as const;
export const MIN_PIECE_DURATION = 0.1;
export const MIN_TOTAL_DURATION = 5.0;
export const MAX_TOTAL_DURATION = 180.0;
const TIME_EPSILON = 1e-9;

declare const __coordinateBrand: unique symbol;
export type Brand<T, B extends string> = T & { readonly [__coordinateBrand]: B };

export type SourceTime = Brand<number, "SourceTime">;
export type ProxyTime = Brand<number, "ProxyTime">;
export type ConcatTime = Brand<number, "ConcatTime">;

export class TimelineMapperError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TimelineMapperError";
  }
}

export interface EdlSegment {
  id: string;
  source_start: SourceTime;
  source_end: SourceTime;
}

export type EdlSegmentInput =
  | { id?: string; source_start?: number; source_end?: number; start?: number; end?: number }
  | readonly [number, number];

export type PlaybackDescriptor =
  | { kind: "source_video"; sourceDuration?: number }
  | { kind: "edit_window"; windowStart: SourceTime; windowEnd: SourceTime }
  | { kind: "rendered_clip"; segments: ReadonlyArray<EdlSegmentInput> };

export type SnapGapMode = "none" | "next" | "previous" | "nearest";

export type SourceToConcatResult =
  | { kind: "ok"; time: ConcatTime; segmentIndex: number; snapped: boolean }
  | {
      kind: "gap";
      reason: "deleted_gap" | "before_first_segment" | "after_last_segment";
      sourceTime: SourceTime;
      nearestSegmentStart: SourceTime | null;
      nearestSegmentEnd: SourceTime | null;
      nearestConcatTime: ConcatTime;
    };

export type ConcatToSourceResult =
  | { kind: "ok"; time: SourceTime; segmentIndex: number; clamped: boolean }
  | {
      kind: "out_of_range";
      reason: "before_start" | "after_end";
      concatTime: ConcatTime;
      minConcatTime: ConcatTime;
      maxConcatTime: ConcatTime;
      clampedTime: SourceTime;
    };

export type WindowMappingResult<T extends number> =
  | { kind: "ok"; time: T; clamped: boolean }
  | {
      kind: "out_of_window";
      reason: "before_window" | "after_window";
      windowStart: SourceTime;
      windowEnd: SourceTime;
      clampedTime: T;
    };

export type PlaybackMappingResult<T extends number = number> =
  | { kind: "ok"; time: T; clamped?: boolean; snapped?: boolean; segmentIndex?: number }
  | {
      kind: "gap";
      reason: "deleted_gap" | "before_first_segment" | "after_last_segment";
      sourceTime: SourceTime;
      nearestSegmentStart: SourceTime | null;
      nearestSegmentEnd: SourceTime | null;
      nearestConcatTime: ConcatTime;
    }
  | {
      kind: "out_of_window";
      reason: "before_window" | "after_window";
      windowStart: SourceTime;
      windowEnd: SourceTime;
      clampedTime: T;
    }
  | {
      kind: "out_of_range";
      reason: "before_start" | "after_end";
      minTime: number;
      maxTime: number | null;
      clampedTime: T;
    };

function finiteNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TimelineMapperError(`${field} must be a finite number`);
  }
  return value === 0 ? 0 : value;
}

export function asSourceTime(seconds: number): SourceTime {
  return finiteNumber(seconds, "sourceTime") as SourceTime;
}

export function asProxyTime(seconds: number): ProxyTime {
  return finiteNumber(seconds, "proxyTime") as ProxyTime;
}

export function asConcatTime(seconds: number): ConcatTime {
  return finiteNumber(seconds, "concatTime") as ConcatTime;
}

export const sourceTime = asSourceTime;
export const proxyTime = asProxyTime;
export const concatTime = asConcatTime;

/** Canonical half-up millisecond rounding matching Python, Frontend TS, Kotlin, and PostgreSQL. */
export function roundMs(seconds: unknown): number {
  const val = finiteNumber(seconds, "seconds");
  const rounded = Math.floor(val * 1000.0 + 0.5) / 1000.0;
  return rounded === 0 ? 0 : rounded;
}

/** Validate and normalize active EDL segments without merging adjacent blocks. */
export function normalizeSegments(segments: ReadonlyArray<EdlSegmentInput>): EdlSegment[] {
  if (!Array.isArray(segments) || segments.length === 0) {
    throw new TimelineMapperError("segments must be a non-empty array");
  }

  const normalized: EdlSegment[] = [];
  let prevEnd: number | null = null;

  for (let i = 0; i < segments.length; i++) {
    const raw = segments[i];
    let segId = `seg-${i + 1}`;
    let s: number;
    let e: number;

    if (Array.isArray(raw) && raw.length === 2) {
      s = roundMs(raw[0]);
      e = roundMs(raw[1]);
    } else if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const obj = raw as Record<string, unknown>;
      const startVal = obj.source_start ?? obj.start;
      const endVal = obj.source_end ?? obj.end;
      if (startVal === undefined || endVal === undefined) {
        throw new TimelineMapperError(`segment ${i} must contain source_start and source_end`);
      }
      if (typeof obj.id === "string" && obj.id.trim()) {
        segId = obj.id.trim();
      }
      s = roundMs(startVal);
      e = roundMs(endVal);
    } else {
      throw new TimelineMapperError(`segment ${i} must be { source_start, source_end } or [start, end]`);
    }

    if (s < 0 || e <= s) {
      throw new TimelineMapperError(`segment ${i} has invalid bounds [${s}, ${e}]`);
    }
    if (prevEnd !== null && s < prevEnd - TIME_EPSILON) {
      throw new TimelineMapperError("segments must be sorted and non-overlapping");
    }

    normalized.push({
      id: segId,
      source_start: s as SourceTime,
      source_end: e as SourceTime,
    });
    prevEnd = e;
  }

  return normalized;
}

/** Return total concatenated kept duration across active EDL segments. */
export function keptDuration(segments: ReadonlyArray<EdlSegmentInput>): number {
  const canonical = normalizeSegments(segments);
  const sum = canonical.reduce((acc, seg) => acc + (seg.source_end - seg.source_start), 0);
  return roundMs(sum);
}

/** Map SourceTime to ConcatTime; return an explicit gap result inside deleted gaps unless opted in. */
export function sourceToConcat(
  time: SourceTime,
  segments: ReadonlyArray<EdlSegmentInput>,
  options: { snapGapTo?: SnapGapMode } = {},
): SourceToConcatResult {
  const snapGapTo = options.snapGapTo ?? "none";
  if (snapGapTo !== "none" && snapGapTo !== "next" && snapGapTo !== "previous" && snapGapTo !== "nearest") {
    throw new TimelineMapperError(`invalid snapGapTo option: ${String(snapGapTo)}`);
  }

  const t = roundMs(finiteNumber(time, "sourceTime"));
  const canonical = normalizeSegments(segments);
  let offset = 0;

  for (let i = 0; i < canonical.length; i++) {
    const seg = canonical[i];
    if (t < seg.source_start - TIME_EPSILON) {
      const prevEnd = i > 0 ? canonical[i - 1].source_end : null;
      const nextStart = seg.source_start;
      const reason = i === 0 ? "before_first_segment" : "deleted_gap";
      const nearestConcat = roundMs(offset) as ConcatTime;

      if (snapGapTo === "next") {
        return { kind: "ok", time: nearestConcat, segmentIndex: i, snapped: true };
      }
      if (snapGapTo === "previous") {
        if (prevEnd !== null) {
          return { kind: "ok", time: nearestConcat, segmentIndex: i - 1, snapped: true };
        }
      } else if (snapGapTo === "nearest") {
        if (prevEnd === null) {
          return { kind: "ok", time: nearestConcat, segmentIndex: 0, snapped: true };
        }
        const distPrev = t - prevEnd;
        const distNext = nextStart - t;
        const targetIndex = distNext <= distPrev ? i : i - 1;
        return { kind: "ok", time: nearestConcat, segmentIndex: targetIndex, snapped: true };
      }

      return {
        kind: "gap",
        reason,
        sourceTime: t as SourceTime,
        nearestSegmentStart: nextStart,
        nearestSegmentEnd: prevEnd,
        nearestConcatTime: nearestConcat,
      };
    }

    if (t <= seg.source_end + TIME_EPSILON) {
      const local = Math.min(Math.max(t - seg.source_start, 0), seg.source_end - seg.source_start);
      return {
        kind: "ok",
        time: roundMs(offset + local) as ConcatTime,
        segmentIndex: i,
        snapped: false,
      };
    }

    offset = roundMs(offset + (seg.source_end - seg.source_start));
  }

  const lastEnd = canonical[canonical.length - 1].source_end;
  const totalConcat = roundMs(offset) as ConcatTime;
  if (snapGapTo === "previous" || snapGapTo === "nearest") {
    return {
      kind: "ok",
      time: totalConcat,
      segmentIndex: canonical.length - 1,
      snapped: true,
    };
  }

  return {
    kind: "gap",
    reason: "after_last_segment",
    sourceTime: t as SourceTime,
    nearestSegmentStart: null,
    nearestSegmentEnd: lastEnd,
    nearestConcatTime: totalConcat,
  };
}

/** Map ConcatTime back to SourceTime; return explicit out_of_range unless clamp=true. */
export function concatToSource(
  time: ConcatTime,
  segments: ReadonlyArray<EdlSegmentInput>,
  options: { clamp?: boolean } = {},
): ConcatToSourceResult {
  const clamp = options.clamp === true;
  const t = roundMs(finiteNumber(time, "concatTime"));
  const canonical = normalizeSegments(segments);
  const total = roundMs(canonical.reduce((sum, seg) => sum + (seg.source_end - seg.source_start), 0));

  if (t < -TIME_EPSILON) {
    if (clamp) {
      return { kind: "ok", time: canonical[0].source_start, segmentIndex: 0, clamped: true };
    }
    return {
      kind: "out_of_range",
      reason: "before_start",
      concatTime: t as ConcatTime,
      minConcatTime: 0 as ConcatTime,
      maxConcatTime: total as ConcatTime,
      clampedTime: canonical[0].source_start,
    };
  }

  if (t > total + TIME_EPSILON) {
    if (clamp) {
      return {
        kind: "ok",
        time: canonical[canonical.length - 1].source_end,
        segmentIndex: canonical.length - 1,
        clamped: true,
      };
    }
    return {
      kind: "out_of_range",
      reason: "after_end",
      concatTime: t as ConcatTime,
      minConcatTime: 0 as ConcatTime,
      maxConcatTime: total as ConcatTime,
      clampedTime: canonical[canonical.length - 1].source_end,
    };
  }

  const bounded = Math.min(Math.max(t, 0), total);
  let offset = 0;
  const lastIdx = canonical.length - 1;

  for (let i = 0; i < canonical.length; i++) {
    const seg = canonical[i];
    const dur = roundMs(seg.source_end - seg.source_start);
    const nextOffset = roundMs(offset + dur);
    if (bounded < nextOffset - TIME_EPSILON || i === lastIdx) {
      const local = Math.min(Math.max(bounded - offset, 0), dur);
      const src = Math.min(seg.source_end, roundMs(seg.source_start + local));
      return {
        kind: "ok",
        time: src as SourceTime,
        segmentIndex: i,
        clamped: false,
      };
    }
    offset = nextOffset;
  }

  return {
    kind: "ok",
    time: canonical[lastIdx].source_end,
    segmentIndex: lastIdx,
    clamped: false,
  };
}

function validateWindow(windowStart: SourceTime, windowEnd: SourceTime): { ws: SourceTime; we: SourceTime; dur: number } {
  const ws = roundMs(finiteNumber(windowStart, "windowStart")) as SourceTime;
  const we = roundMs(finiteNumber(windowEnd, "windowEnd")) as SourceTime;
  if (ws < 0) {
    throw new TimelineMapperError("windowStart must be non-negative");
  }
  if (we <= ws) {
    throw new TimelineMapperError("windowEnd must be greater than windowStart");
  }
  return { ws, we, dur: roundMs(we - ws) };
}

/** Map SourceTime to edit-window ProxyTime with explicit out-of-window handling. */
export function sourceToProxy(
  time: SourceTime,
  windowStart: SourceTime,
  windowEnd: SourceTime,
  options: { clamp?: boolean } = {},
): WindowMappingResult<ProxyTime> {
  const clamp = options.clamp === true;
  const s = roundMs(finiteNumber(time, "sourceTime"));
  const { ws, we, dur } = validateWindow(windowStart, windowEnd);

  if (s < ws - TIME_EPSILON) {
    if (clamp) {
      return { kind: "ok", time: 0 as ProxyTime, clamped: true };
    }
    return {
      kind: "out_of_window",
      reason: "before_window",
      windowStart: ws,
      windowEnd: we,
      clampedTime: 0 as ProxyTime,
    };
  }

  if (s > we + TIME_EPSILON) {
    if (clamp) {
      return { kind: "ok", time: dur as ProxyTime, clamped: true };
    }
    return {
      kind: "out_of_window",
      reason: "after_window",
      windowStart: ws,
      windowEnd: we,
      clampedTime: dur as ProxyTime,
    };
  }

  const mapped = Math.min(Math.max(roundMs(s - ws), 0), dur);
  return { kind: "ok", time: mapped as ProxyTime, clamped: false };
}

/** Map edit-window ProxyTime back to SourceTime with explicit out-of-window handling. */
export function proxyToSource(
  time: ProxyTime,
  windowStart: SourceTime,
  windowEnd: SourceTime,
  options: { clamp?: boolean } = {},
): WindowMappingResult<SourceTime> {
  const clamp = options.clamp === true;
  const p = roundMs(finiteNumber(time, "proxyTime"));
  const { ws, we, dur } = validateWindow(windowStart, windowEnd);

  if (p < -TIME_EPSILON) {
    if (clamp) {
      return { kind: "ok", time: ws, clamped: true };
    }
    return {
      kind: "out_of_window",
      reason: "before_window",
      windowStart: ws,
      windowEnd: we,
      clampedTime: ws,
    };
  }

  if (p > dur + TIME_EPSILON) {
    if (clamp) {
      return { kind: "ok", time: we, clamped: true };
    }
    return {
      kind: "out_of_window",
      reason: "after_window",
      windowStart: ws,
      windowEnd: we,
      clampedTime: we,
    };
  }

  const mapped = Math.min(Math.max(roundMs(ws + p), ws), we);
  return { kind: "ok", time: mapped as SourceTime, clamped: false };
}

/** Map C2 edit-window ProxyTime to C3 master SourceTime with strict window bounds checks. */
export function c2ToC3Time(proxyTimeVal: ProxyTime, windowStart: SourceTime, windowEnd?: SourceTime): SourceTime {
  const p = roundMs(finiteNumber(proxyTimeVal, "proxyTime"));
  const ws = roundMs(finiteNumber(windowStart, "windowStart"));
  if (ws < 0) {
    throw new TimelineMapperError("windowStart must be non-negative");
  }
  if (p < 0) {
    throw new TimelineMapperError(`proxyTime ${p} is before edit window start (0.0)`);
  }
  if (windowEnd !== undefined) {
    const we = roundMs(finiteNumber(windowEnd, "windowEnd"));
    if (we <= ws) {
      throw new TimelineMapperError("windowEnd must be greater than windowStart");
    }
    const windowDur = roundMs(we - ws);
    if (p > windowDur + 1e-6) {
      throw new TimelineMapperError(`proxyTime ${p} exceeds edit window duration ${windowDur}`);
    }
    return Math.min(we, roundMs(ws + p)) as SourceTime;
  }
  return roundMs(ws + p) as SourceTime;
}

/** Map C3 master SourceTime to C2 edit-window ProxyTime with strict window bounds checks. */
export function c3ToC2Time(sourceTimeVal: SourceTime, windowStart: SourceTime, windowEnd?: SourceTime): ProxyTime {
  const s = roundMs(finiteNumber(sourceTimeVal, "sourceTime"));
  const ws = roundMs(finiteNumber(windowStart, "windowStart"));
  if (ws < 0) {
    throw new TimelineMapperError("windowStart must be non-negative");
  }
  if (s < ws - 1e-6) {
    throw new TimelineMapperError(`sourceTime ${s} is before edit window start ${ws}`);
  }
  if (windowEnd !== undefined) {
    const we = roundMs(finiteNumber(windowEnd, "windowEnd"));
    if (we <= ws) {
      throw new TimelineMapperError("windowEnd must be greater than windowStart");
    }
    if (s > we + 1e-6) {
      throw new TimelineMapperError(`sourceTime ${s} exceeds edit window end ${we}`);
    }
  }
  return Math.max(0, roundMs(s - ws)) as ProxyTime;
}

/** Map a player timestamp parameterized by PlaybackDescriptor to master SourceTime. */
export function playbackTimeToSource(
  playbackTime: number,
  descriptor: PlaybackDescriptor,
  options: { clamp?: boolean } = {},
): PlaybackMappingResult<SourceTime> {
  const clamp = options.clamp === true;
  if (descriptor.kind === "source_video") {
    const t = roundMs(finiteNumber(playbackTime, "playbackTime"));
    const maxDur = descriptor.sourceDuration !== undefined ? roundMs(finiteNumber(descriptor.sourceDuration, "sourceDuration")) : null;
    if (maxDur !== null && maxDur <= 0) {
      throw new TimelineMapperError("sourceDuration must be greater than zero");
    }
    if (t < -TIME_EPSILON) {
      if (clamp) return { kind: "ok", time: 0 as SourceTime, clamped: true };
      return { kind: "out_of_range", reason: "before_start", minTime: 0, maxTime: maxDur, clampedTime: 0 as SourceTime };
    }
    if (maxDur !== null && t > maxDur + TIME_EPSILON) {
      if (clamp) return { kind: "ok", time: maxDur as SourceTime, clamped: true };
      return { kind: "out_of_range", reason: "after_end", minTime: 0, maxTime: maxDur, clampedTime: maxDur as SourceTime };
    }
    const bounded = maxDur !== null ? Math.min(Math.max(t, 0), maxDur) : Math.max(t, 0);
    return { kind: "ok", time: roundMs(bounded) as SourceTime, clamped: false };
  }

  if (descriptor.kind === "edit_window") {
    return proxyToSource(asProxyTime(playbackTime), descriptor.windowStart, descriptor.windowEnd, options);
  }

  if (descriptor.kind === "rendered_clip") {
    const res = concatToSource(asConcatTime(playbackTime), descriptor.segments, options);
    if (res.kind === "ok") {
      return res;
    }
    return {
      kind: "out_of_range",
      reason: res.reason,
      minTime: res.minConcatTime,
      maxTime: res.maxConcatTime,
      clampedTime: res.clampedTime,
    };
  }

  throw new TimelineMapperError(`unsupported PlaybackDescriptor kind: ${String((descriptor as Record<string, unknown>)?.kind)}`);
}

/** Map master SourceTime to a player's native time coordinate parameterized by PlaybackDescriptor. */
export function sourceToPlaybackTime(
  time: SourceTime,
  descriptor: PlaybackDescriptor,
  options: { clamp?: boolean; snapGapTo?: SnapGapMode } = {},
): PlaybackMappingResult<number> {
  const clamp = options.clamp === true;
  if (descriptor.kind === "source_video") {
    const t = roundMs(finiteNumber(time, "sourceTime"));
    const maxDur = descriptor.sourceDuration !== undefined ? roundMs(finiteNumber(descriptor.sourceDuration, "sourceDuration")) : null;
    if (maxDur !== null && maxDur <= 0) {
      throw new TimelineMapperError("sourceDuration must be greater than zero");
    }
    if (t < -TIME_EPSILON) {
      if (clamp) return { kind: "ok", time: 0, clamped: true };
      return { kind: "out_of_range", reason: "before_start", minTime: 0, maxTime: maxDur, clampedTime: 0 };
    }
    if (maxDur !== null && t > maxDur + TIME_EPSILON) {
      if (clamp) return { kind: "ok", time: maxDur, clamped: true };
      return { kind: "out_of_range", reason: "after_end", minTime: 0, maxTime: maxDur, clampedTime: maxDur };
    }
    const bounded = maxDur !== null ? Math.min(Math.max(t, 0), maxDur) : Math.max(t, 0);
    return { kind: "ok", time: roundMs(bounded), clamped: false };
  }

  if (descriptor.kind === "edit_window") {
    return sourceToProxy(time, descriptor.windowStart, descriptor.windowEnd, { clamp });
  }

  if (descriptor.kind === "rendered_clip") {
    return sourceToConcat(time, descriptor.segments, { snapGapTo: options.snapGapTo });
  }

  throw new TimelineMapperError(`unsupported PlaybackDescriptor kind: ${String((descriptor as Record<string, unknown>)?.kind)}`);
}

/** Merge contiguous adjacent intervals into continuous source spans at 1ms precision. */
export function canonicalMediaSpans(ranges: unknown): [number, number][] {
  if (!Array.isArray(ranges)) {
    throw new TimelineMapperError("ranges must be an array of [start, end] pairs or segment objects");
  }
  const normalized: [number, number][] = ranges.map((item, idx) => {
    let s: number;
    let e: number;
    if (item && typeof item === "object" && !Array.isArray(item)) {
      const obj = item as Record<string, unknown>;
      const startVal = obj.source_start ?? obj.start;
      const endVal = obj.source_end ?? obj.end;
      s = roundMs(startVal);
      e = roundMs(endVal);
    } else if (Array.isArray(item) && item.length === 2) {
      s = roundMs(item[0]);
      e = roundMs(item[1]);
    } else {
      throw new TimelineMapperError(`range at index ${idx} must be a [start, end] pair or segment object`);
    }
    if (s < 0 || e <= s) {
      throw new TimelineMapperError(`invalid segment bounds: [${s}, ${e}]`);
    }
    return [s, e];
  });

  if (normalized.length === 0) return [];
  const sorted = [...normalized].sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const [s, e] = sorted[i];
    const prev = merged[merged.length - 1];
    if (s < prev[1] - TIME_EPSILON) {
      throw new TimelineMapperError(`overlapping segments detected: [${prev[0]}, ${prev[1]}] and [${s}, ${e}]`);
    } else if (Math.abs(s - prev[1]) <= 1e-6) {
      prev[1] = e;
    } else {
      merged.push([s, e]);
    }
  }
  return merged.map(([s, e]) => [roundMs(s), roundMs(e)]);
}

/** Canonical string representation of contiguous spans e.g. [10.000-30.000]. */
export function canonicalSpansStr(ranges: unknown): string {
  const spans = canonicalMediaSpans(ranges);
  return "[" + spans.map(([s, e]) => `${s.toFixed(3)}-${e.toFixed(3)}`).join(",") + "]";
}

/**
 * RFC 8785 (JCS) canonical JSON stringifier:
 * - Object keys sorted recursively by UTF-16 code units (.sort())
 * - Undefined object properties omitted; undefined array items serialized as null
 * - Compact separators with no extra whitespace
 * - Clean IEEE-754 number serialization (-0 -> "0", ES6 formatting); NaN/Infinity rejected
 */
export function canonicalJsonStringify(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new TimelineMapperError("non-finite numbers (NaN/Infinity) are not valid in RFC-8785 JSON");
    }
    if (Object.is(value, -0) || value === 0) return "0";
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "object") {
    const maybeToJson = (value as { toJSON?: () => unknown }).toJSON;
    if (typeof maybeToJson === "function") {
      return canonicalJsonStringify(maybeToJson.call(value));
    }
    if (Array.isArray(value)) {
      const items = value.map((item) => {
        if (item === undefined || typeof item === "function" || typeof item === "symbol") {
          return "null";
        }
        return canonicalJsonStringify(item);
      });
      return `[${items.join(",")}]`;
    }
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    const parts: string[] = [];
    for (const k of keys) {
      const v = obj[k];
      if (v === undefined || typeof v === "function" || typeof v === "symbol") {
        continue;
      }
      parts.push(`${JSON.stringify(k)}:${canonicalJsonStringify(v)}`);
    }
    return `{${parts.join(",")}}`;
  }
  throw new TimelineMapperError(`unsupported type for RFC-8785 canonical JSON: ${typeof value}`);
}

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const SHA256_H0 = new Uint32Array([
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
  0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
]);

function rotr(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits));
}

/** Synchronous UTF-8 SHA-256 hex digest (works identically in Browser, Node, and Cloudflare Workers). */
export function sha256Hex(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const bitLen = bytes.length * 8;
  const paddedLen = Math.ceil((bytes.length + 9) / 64) * 64;
  const padded = new Uint8Array(paddedLen);
  padded.set(bytes);
  padded[bytes.length] = 0x80;

  const view = new DataView(padded.buffer);
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);
  view.setUint32(paddedLen - 4, bitLen >>> 0, false);

  const hash = new Uint32Array(SHA256_H0);
  const w = new Uint32Array(64);

  for (let offset = 0; offset < paddedLen; offset += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = view.getUint32(offset + i * 4, false);
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }

    let a = hash[0];
    let b = hash[1];
    let c = hash[2];
    let d = hash[3];
    let e = hash[4];
    let f = hash[5];
    let g = hash[6];
    let h = hash[7];

    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }

  let hex = "";
  for (let i = 0; i < 8; i++) {
    hex += hash[i].toString(16).padStart(8, "0");
  }
  return hex;
}

/** Compute canonical media_fingerprint from sourceAssetId and canonical spans. */
export function computeMediaFingerprint(sourceAssetId: string, ranges: unknown): string {
  if (typeof sourceAssetId !== "string" || !sourceAssetId.trim()) {
    throw new TimelineMapperError("sourceAssetId must be a non-empty string");
  }
  const spansStr = canonicalSpansStr(ranges);
  return sha256Hex(`v1:media:${sourceAssetId.trim()}:${spansStr}`);
}

/** Compute canonical render_fingerprint using RFC-8785 canonical JSON for layers and settings. */
export function computeRenderFingerprint(
  mediaFingerprint: string,
  layoutMode: string,
  cropAnchorX: number,
  layers?: unknown[],
  settings?: Record<string, unknown>,
): string {
  if (typeof mediaFingerprint !== "string" || mediaFingerprint.length !== 64) {
    throw new TimelineMapperError("mediaFingerprint must be a valid 64-character SHA-256 hex string");
  }
  const trimmedLayout = typeof layoutMode === "string" ? layoutMode.trim() : String(layoutMode ?? "").trim();
  const layout = trimmedLayout || "portrait_9_16";
  const cropStr = roundMs(cropAnchorX).toFixed(3);

  const layersClean = Array.isArray(layers) ? layers : [];
  const layersStr = canonicalJsonStringify(layersClean);
  const layersHash = sha256Hex(layersStr);

  const settingsClean = settings && typeof settings === "object" && !Array.isArray(settings) ? settings : {};
  const settingsStr = canonicalJsonStringify(settingsClean);

  let payload = `v1:render:${mediaFingerprint}:${layout}:${cropStr}:${layersHash}`;
  if (settingsStr !== "{}") {
    const settingsHash = sha256Hex(settingsStr);
    payload += `:${settingsHash}`;
  }
  return sha256Hex(payload);
}

/** Validate segment array against product bounds (5s..180s, sorted, non-overlapping). */
export function validateTimelineSegments(segments: unknown): {
  valid: boolean;
  error?: string;
  keptDuration: number;
  canonicalSegments?: Array<{ id: string; source_start: number; source_end: number }>;
} {
  if (!Array.isArray(segments) || segments.length === 0) {
    return { valid: false, error: "segments must be a non-empty array", keptDuration: 0 };
  }
  let total = 0;
  let prevEnd = -1;
  const canonicalSegments: Array<{ id: string; source_start: number; source_end: number }> = [];

  for (let i = 0; i < segments.length; i++) {
    const item = segments[i];
    let s: number;
    let e: number;
    let id = `seg-${i + 1}`;

    if (item && typeof item === "object" && !Array.isArray(item)) {
      const obj = item as Record<string, unknown>;
      const startVal = obj.source_start ?? obj.start;
      const endVal = obj.source_end ?? obj.end;
      if (typeof obj.id === "string" && obj.id.trim()) {
        id = obj.id.trim();
      }
      if (typeof startVal !== "number" || typeof endVal !== "number") {
        return { valid: false, error: `segment at index ${i} missing numeric source_start/source_end`, keptDuration: 0 };
      }
      s = roundMs(startVal);
      e = roundMs(endVal);
    } else if (Array.isArray(item) && item.length === 2) {
      s = roundMs(Number(item[0]));
      e = roundMs(Number(item[1]));
    } else {
      return { valid: false, error: `segment at index ${i} must be [start, end] or { source_start, source_end }`, keptDuration: 0 };
    }

    if (s < 0 || e <= s || !Number.isFinite(s) || !Number.isFinite(e)) {
      return { valid: false, error: `invalid segment bounds [${s}, ${e}] at index ${i}`, keptDuration: 0 };
    }
    if (s < prevEnd - TIME_EPSILON) {
      return { valid: false, error: `overlapping segment at index ${i}`, keptDuration: 0 };
    }
    prevEnd = e;
    total += e - s;
    canonicalSegments.push({ id, source_start: s, source_end: e });
  }

  total = roundMs(total);
  if (total < MIN_TOTAL_DURATION) {
    return { valid: false, error: `total kept duration (${total}s) must be at least 5.0s`, keptDuration: total };
  }
  if (total > MAX_TOTAL_DURATION) {
    return { valid: false, error: `total kept duration (${total}s) exceeds 180.0s maximum`, keptDuration: total };
  }
  return { valid: true, keptDuration: total, canonicalSegments };
}

export const TimelineMapper = {
  VERSION: TIMELINE_CONTRACT_VERSION,
  CANONICALIZER_VERSION,
  roundMs,
  asSourceTime,
  asProxyTime,
  asConcatTime,
  normalizeSegments,
  keptDuration,
  sourceToConcat,
  concatToSource,
  sourceToProxy,
  proxyToSource,
  c2ToC3Time,
  c3ToC2Time,
  playbackTimeToSource,
  sourceToPlaybackTime,
  canonicalMediaSpans,
  canonicalSpansStr,
  canonicalJsonStringify,
  sha256Hex,
  computeMediaFingerprint,
  computeRenderFingerprint,
  validateTimelineSegments,
} as const;
