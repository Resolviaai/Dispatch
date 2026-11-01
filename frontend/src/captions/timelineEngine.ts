/** Pure, deterministic timeline mathematics for Dispatch EDL v1.
 *
 * All ranges are source-time half-open intervals [start, end). Exact mapping
 * boundary behavior is explicit and mirrored by dispatch/segments.py.
 */

export type TimeRange = [number, number];

export interface TimelineFragment {
  source_start: number;
  source_end: number;
  timeline_start: number;
  timeline_end: number;
}

export interface EdlV1 {
  v: 1;
  source_asset_id: string;
  source_duration: number;
  ranges: TimeRange[];
  [key: string]: unknown;
}

export const MIN_PIECE_DURATION = 0.1;
export const MIN_TOTAL_DURATION = 5.0;
export const MAX_TOTAL_DURATION = 180.0;
export const SOURCE_BOUNDS_EPSILON = 0.01;
const TIME_EPSILON = 1e-9;
const EDL_VERSION = 1 as const;

export class TimelineError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TimelineError';
  }
}

function finiteNumber(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TimelineError(`${field} must be a finite number`);
  }
  return value;
}

function validSourceDuration(value: unknown): number {
  const duration = finiteNumber(value, 'source_duration');
  if (duration <= 0) throw new TimelineError('source_duration must be greater than zero');
  return duration;
}

function readRange(raw: unknown, index: number): TimeRange {
  if (!Array.isArray(raw) || raw.length !== 2) {
    throw new TimelineError(`range ${index} must contain exactly [start, end]`);
  }
  return [
    finiteNumber(raw[0], `ranges[${index}][0]`),
    finiteNumber(raw[1], `ranges[${index}][1]`),
  ];
}

/** Validate without sorting or merging. Adjacent source ranges remain distinct. */
export function normalizeRanges(
  ranges: unknown,
  sourceDuration: number,
  options: { enforceTotal?: boolean } = {},
): TimeRange[] {
  const sourceEnd = validSourceDuration(sourceDuration);
  if (!Array.isArray(ranges) || ranges.length === 0) {
    throw new TimelineError('ranges must be a non-empty array');
  }

  const normalized: TimeRange[] = [];
  let previousEnd: number | null = null;

  ranges.forEach((raw, index) => {
    let [start, end] = readRange(raw, index);
    if (start < -SOURCE_BOUNDS_EPSILON || end > sourceEnd + SOURCE_BOUNDS_EPSILON) {
      throw new TimelineError(`range ${index} is outside source bounds`);
    }
    if (start < 0) start = 0;
    if (end > sourceEnd) end = sourceEnd;
    if (end <= start || end - start + TIME_EPSILON < MIN_PIECE_DURATION) {
      throw new TimelineError(`range ${index} must be at least ${MIN_PIECE_DURATION.toFixed(1)}s`);
    }
    if (previousEnd !== null) {
      if (start < previousEnd - TIME_EPSILON) {
        throw new TimelineError('ranges must be sorted and non-overlapping');
      }
      if (start < previousEnd) {
        start = previousEnd;
        if (end - start + TIME_EPSILON < MIN_PIECE_DURATION) {
          throw new TimelineError(`range ${index} must be at least ${MIN_PIECE_DURATION.toFixed(1)}s`);
        }
      }
    }
    normalized.push([start, end]);
    previousEnd = end;
  });

  if (options.enforceTotal !== false) {
    const total = keptDuration(normalized);
    if (total + TIME_EPSILON < MIN_TOTAL_DURATION) {
      throw new TimelineError(`total kept duration must be at least ${MIN_TOTAL_DURATION.toFixed(1)}s`);
    }
    if (total - TIME_EPSILON > MAX_TOTAL_DURATION) {
      throw new TimelineError(`total kept duration must not exceed ${MAX_TOTAL_DURATION.toFixed(1)}s`);
    }
  }
  return normalized;
}

/** Sum kept footage; never use the outer source-time hull as duration. */
export function keptDuration(ranges: unknown, sourceDuration?: number): number {
  const canonical: TimeRange[] = sourceDuration === undefined
    ? validateRangesWithoutSource(ranges)
    : normalizeRanges(ranges, sourceDuration);
  return canonical.reduce((sum, [start, end]) => sum + end - start, 0);
}

function validateRangesWithoutSource(ranges: unknown): TimeRange[] {
  if (!Array.isArray(ranges) || ranges.length === 0) {
    throw new TimelineError('ranges must be a non-empty array');
  }
  const result: TimeRange[] = [];
  let previousEnd: number | null = null;
  ranges.forEach((raw, index) => {
    const [start, end] = readRange(raw, index);
    if (end <= start || end - start + TIME_EPSILON < MIN_PIECE_DURATION) {
      throw new TimelineError(`range ${index} must be at least ${MIN_PIECE_DURATION.toFixed(1)}s`);
    }
    if (previousEnd !== null && start < previousEnd - TIME_EPSILON) {
      throw new TimelineError('ranges must be sorted and non-overlapping');
    }
    result.push([start, end]);
    previousEnd = end;
  });
  return result;
}

/** Split one kept segment without changing the kept footage or output duration. */
export function splitAt(ranges: unknown, sourceTime: number, sourceDuration: number): TimeRange[] {
  const canonical = normalizeRanges(ranges, sourceDuration);
  const point = finiteNumber(sourceTime, 'source_time');
  const result: TimeRange[] = [];
  let splitDone = false;

  canonical.forEach(([start, end]) => {
    if (
      point >= start + MIN_PIECE_DURATION - TIME_EPSILON
      && point <= end - MIN_PIECE_DURATION + TIME_EPSILON
    ) {
      if (point <= start + TIME_EPSILON || point >= end - TIME_EPSILON) {
        throw new TimelineError('split point must be inside a segment');
      }
      result.push([start, point], [point, end]);
      splitDone = true;
    } else {
      result.push([start, end]);
    }
  });

  if (!splitDone) {
    throw new TimelineError('split point must leave at least 0.1s on both sides of a kept segment');
  }
  const validated = normalizeRanges(result, sourceDuration);
  if (Math.abs(keptDuration(validated) - keptDuration(canonical)) > 1e-8) {
    throw new TimelineError('split must not change total kept duration');
  }
  return validated;
}

/** Move one selected edge; expansion into gaps is allowed, overlap is not. */
export function trimEdge(
  ranges: unknown,
  index: number,
  edge: 'start' | 'end',
  newSourceTime: number,
  sourceDuration: number,
): TimeRange[] {
  const canonical = normalizeRanges(ranges, sourceDuration);
  if (!Number.isInteger(index) || index < 0 || index >= canonical.length) {
    throw new TimelineError('segment index is out of range');
  }
  if (edge !== 'start' && edge !== 'end') throw new TimelineError("edge must be 'start' or 'end'");
  const point = finiteNumber(newSourceTime, 'new_source_time');
  const [start, end] = canonical[index];
  const previousEnd = index > 0 ? canonical[index - 1][1] : 0;
  const nextStart = index + 1 < canonical.length
    ? canonical[index + 1][0]
    : validSourceDuration(sourceDuration);

  if (edge === 'start') {
    if (
      point < previousEnd - TIME_EPSILON
      || point > end - MIN_PIECE_DURATION + TIME_EPSILON
    ) {
      throw new TimelineError('trimmed start would overlap a neighbor or make the segment too short');
    }
    canonical[index] = [Math.max(previousEnd, point), end];
  } else {
    if (
      point > nextStart + TIME_EPSILON
      || point < start + MIN_PIECE_DURATION - TIME_EPSILON
    ) {
      throw new TimelineError('trimmed end would overlap a neighbor or make the segment too short');
    }
    canonical[index] = [start, Math.min(nextStart, point)];
  }
  return normalizeRanges(canonical, sourceDuration);
}

/** Delete one piece, preserving the remaining blocks as separate source ranges. */
export function deletePiece(ranges: unknown, index: number, sourceDuration: number): TimeRange[] {
  const canonical = normalizeRanges(ranges, sourceDuration);
  if (!Number.isInteger(index) || index < 0 || index >= canonical.length) {
    throw new TimelineError('segment index is out of range');
  }
  if (canonical.length === 1) {
    throw new TimelineError('cannot delete the final kept segment; delete the logical clip instead');
  }
  return normalizeRanges(canonical.filter((_, current) => current !== index), sourceDuration);
}

/** Source point -> concatenated output time; removed-gap points return null. */
export function sourceToConcat(
  ranges: unknown,
  sourceTime: number,
  sourceDuration: number,
): number | null {
  const canonical = normalizeRanges(ranges, sourceDuration);
  const point = finiteNumber(sourceTime, 'source_time');
  let offset = 0;
  for (const [start, end] of canonical) {
    if (point >= start - TIME_EPSILON && point <= end + TIME_EPSILON) {
      return offset + Math.min(Math.max(point - start, 0), end - start);
    }
    offset += end - start;
  }
  return null;
}

/** Output point -> source time. At internal boundaries, prefer the next segment. */
export function concatToSource(ranges: unknown, concatTime: number, sourceDuration: number): number {
  const canonical = normalizeRanges(ranges, sourceDuration);
  let point = finiteNumber(concatTime, 'concat_time');
  const total = keptDuration(canonical);
  if (point < -TIME_EPSILON || point > total + TIME_EPSILON) {
    throw new TimelineError('concat_time is outside the kept timeline');
  }
  point = Math.min(Math.max(point, 0), total);
  let offset = 0;
  for (const [start, end] of canonical) {
    const duration = end - start;
    if (point < offset + duration - TIME_EPSILON) {
      return start + Math.max(0, point - offset);
    }
    offset += duration;
  }
  return canonical[canonical.length - 1][1];
}

/** Intersect a source-anchored layer with kept ranges; gaps produce multiple fragments. */
export function intersectSourceInterval(
  ranges: unknown,
  sourceStart: number,
  sourceEnd: number,
  sourceDuration: number,
): TimelineFragment[] {
  const canonical = normalizeRanges(ranges, sourceDuration);
  const layerStart = finiteNumber(sourceStart, 'source_start');
  const layerEnd = finiteNumber(sourceEnd, 'source_end');
  if (layerEnd <= layerStart) throw new TimelineError('source interval end must be greater than start');

  const fragments: TimelineFragment[] = [];
  let offset = 0;
  for (const [start, end] of canonical) {
    const overlapStart = Math.max(start, layerStart);
    const overlapEnd = Math.min(end, layerEnd);
    if (overlapEnd - overlapStart > TIME_EPSILON) {
      fragments.push({
        source_start: overlapStart,
        source_end: overlapEnd,
        timeline_start: offset + overlapStart - start,
        timeline_end: offset + overlapEnd - start,
      });
    }
    offset += end - start;
  }
  return fragments;
}

/** Intersect an output-time interval with each segment and return source fragments. */
export function timelineIntervalToSource(
  ranges: unknown,
  timelineStart: number,
  timelineEnd: number,
  sourceDuration: number,
): TimelineFragment[] {
  const canonical = normalizeRanges(ranges, sourceDuration);
  const start = finiteNumber(timelineStart, 'timeline_start');
  const end = finiteNumber(timelineEnd, 'timeline_end');
  const total = keptDuration(canonical);
  if (end <= start || start < -TIME_EPSILON || end > total + TIME_EPSILON) {
    throw new TimelineError('timeline interval is invalid or outside the kept timeline');
  }

  const fragments: TimelineFragment[] = [];
  let offset = 0;
  for (const [sourceStart, sourceEnd] of canonical) {
    const duration = sourceEnd - sourceStart;
    const overlapStart = Math.max(start, offset);
    const overlapEnd = Math.min(end, offset + duration);
    if (overlapEnd - overlapStart > TIME_EPSILON) {
      fragments.push({
        source_start: sourceStart + overlapStart - offset,
        source_end: sourceStart + overlapEnd - offset,
        timeline_start: overlapStart,
        timeline_end: overlapEnd,
      });
    }
    offset += duration;
  }
  return fragments;
}

/** Validate EDL v1 and preserve optional settings/provenance fields. */
export function normalizeEdlDocument(document: unknown): EdlV1 {
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new TimelineError('EDL document must be an object');
  }
  const input = document as Record<string, unknown>;
  if (input.v !== EDL_VERSION) throw new TimelineError(`unsupported EDL version: ${String(input.v)}`);
  if (typeof input.source_asset_id !== 'string' || !input.source_asset_id.trim()) {
    throw new TimelineError('source_asset_id must be a non-empty string');
  }
  const sourceDuration = validSourceDuration(input.source_duration);
  const ranges = normalizeRanges(input.ranges, sourceDuration);
  return {
    ...input,
    v: EDL_VERSION,
    source_asset_id: input.source_asset_id,
    source_duration: sourceDuration,
    ranges,
  } as EdlV1;
}
