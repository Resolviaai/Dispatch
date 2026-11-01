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
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const obj = raw as Record<string, unknown>;
    const startVal = obj.source_start ?? obj.start;
    const endVal = obj.source_end ?? obj.end;
    if (startVal === undefined || endVal === undefined) {
      throw new TimelineError(`range ${index} object must contain source_start/source_end or start/end`);
    }
    return [
      finiteNumber(startVal, `ranges[${index}].source_start`),
      finiteNumber(endVal, `ranges[${index}].source_end`),
    ];
  }
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


/** Canonical half-up millisecond rounding. Matches Python and PostgreSQL. */
export function roundMs(seconds: unknown): number {
  const val = finiteNumber(seconds, "seconds");
  return Math.floor(val * 1000.0 + 0.5) / 1000.0;
}

/** Merges contiguous adjacent intervals into continuous source spans at 1ms precision. */
export function canonicalMediaSpans(ranges: unknown, sourceDuration?: number): TimeRange[] {
  let normalized: TimeRange[];
  if (sourceDuration !== undefined) {
    normalized = normalizeRanges(ranges, sourceDuration);
  } else {
    if (!Array.isArray(ranges)) throw new TimelineError("ranges must be an array of [start, end] pairs or segment objects");
    normalized = ranges.map((item, idx) => {
      let s: number;
      let e: number;
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        const obj = item as Record<string, unknown>;
        s = roundMs(obj.source_start ?? obj.start);
        e = roundMs(obj.source_end ?? obj.end);
      } else if (Array.isArray(item) && item.length === 2) {
        s = roundMs(item[0]);
        e = roundMs(item[1]);
      } else {
        throw new TimelineError(`range at index ${idx} must be a [start, end] pair or segment object`);
      }
      if (s < 0 || e <= s) {
        throw new TimelineError(`invalid segment bounds: [${s}, ${e}]`);
      }
      return [s, e] as TimeRange;
    });
  }

  if (normalized.length === 0) return [];

  const sorted = [...normalized].sort((a, b) => a[0] - b[0]);
  const merged: TimeRange[] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const [s, e] = sorted[i];
    const prev = merged[merged.length - 1];
    if (s < prev[1] - TIME_EPSILON) {
      throw new TimelineError(`overlapping segments detected: [${prev[0]}, ${prev[1]}] and [${s}, ${e}]`);
    } else if (Math.abs(s - prev[1]) <= 1e-6) {
      prev[1] = e;
    } else {
      merged.push([s, e]);
    }
  }

  return merged.map(([s, e]) => [roundMs(s), roundMs(e)]);
}

/** Canonical string representation of contiguous spans e.g. [10.000-30.000]. */
export function canonicalSpansStr(ranges: unknown, sourceDuration?: number): string {
  const spans = canonicalMediaSpans(ranges, sourceDuration);
  return "[" + spans.map(([s, e]) => `${s.toFixed(3)}-${e.toFixed(3)}`).join(",") + "]";
}

/** Fast, zero-dependency synchronous SHA-256 implementation. */
export function sha256Hex(ascii: string): string {
  function rightRotate(value: number, amount: number): number {
    return (value >>> amount) | (value << (32 - amount));
  }
  const mathPow = Math.pow;
  const maxWord = mathPow(2, 32);
  let i = 0;
  let j = 0;
  let result = "";
  const words: number[] = [];
  const asciiBitLength = ascii.length * 8;
  let hash: number[] = [];
  const k: number[] = [];
  let primeCounter = 0;

  const isComposite: Record<number, number> = {};
  for (let candidate = 2; primeCounter < 64; candidate++) {
    if (!isComposite[candidate]) {
      for (i = 0; i < 313; i += candidate) {
        isComposite[i] = candidate;
      }
      hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
      k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
    }
  }

  ascii += "\x80";
  while ((ascii.length % 64) - 56) ascii += "\x00";
  for (i = 0; i < ascii.length; i++) {
    j = ascii.charCodeAt(i);
    words[i >> 2] |= j << (((3 - i) % 4) * 8);
  }
  words[words.length] = (asciiBitLength / maxWord) | 0;
  words[words.length] = asciiBitLength;

  for (j = 0; j < words.length; ) {
    const w = words.slice(j, (j += 16));
    const oldHash = hash;
    hash = hash.slice(0, 8);

    for (i = 0; i < 64; i++) {
      const w15 = w[i - 15];
      const w2 = w[i - 2];
      const s0 = rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3);
      const s1 = rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10);
      const ch = (hash[4] & hash[5]) ^ (~hash[4] & hash[6]);
      const maj = (hash[0] & hash[1]) ^ (hash[0] & hash[2]) ^ (hash[1] & hash[2]);
      const temp1 =
        hash[7] +
        (rightRotate(hash[4], 6) ^ rightRotate(hash[4], 11) ^ rightRotate(hash[4], 25)) +
        ch +
        k[i] +
        (w[i] =
          i < 16
            ? w[i]
            : (w[i - 16] + s0 + w[i - 7] + s1) | 0);
      const temp2 =
        (rightRotate(hash[0], 2) ^ rightRotate(hash[0], 13) ^ rightRotate(hash[0], 22)) + maj;

      hash = [(temp1 + temp2) | 0, hash[0], hash[1], hash[2], (hash[3] + temp1) | 0, hash[4], hash[5], hash[6]];
    }

    for (i = 0; i < 8; i++) {
      hash[i] = (hash[i] + oldHash[i]) | 0;
    }
  }

  for (i = 0; i < 8; i++) {
    for (j = 3; j + 1; j--) {
      const b = (hash[i] >> (j * 8)) & 255;
      result += (b < 16 ? "0" : "") + b.toString(16);
    }
  }
  return result;
}

/** Computes canonical media_fingerprint from source asset ID and canonical spans. */
export function computeMediaFingerprint(sourceAssetId: string, ranges: unknown, sourceDuration?: number): string {
  if (typeof sourceAssetId !== "string" || !sourceAssetId.trim()) {
    throw new TimelineError("sourceAssetId must be a non-empty string");
  }
  const spansStr = canonicalSpansStr(ranges, sourceDuration);
  const payload = `v1:media:${sourceAssetId.trim()}:${spansStr}`;
  return sha256Hex(payload);
}

/** Computes canonical render_fingerprint incorporating media identity, layout, crop, and all layers. */
export function computeRenderFingerprint(
  mediaFingerprint: string,
  layoutMode: string,
  cropAnchorX: number,
  layers?: unknown[],
  settings?: Record<string, unknown>,
): string {
  if (typeof mediaFingerprint !== "string" || mediaFingerprint.length !== 64) {
    throw new TimelineError("mediaFingerprint must be a valid 64-character SHA-256 hex string");
  }
  const layout = layoutMode ? String(layoutMode).trim() : "portrait_9_16";
  const cropStr = roundMs(cropAnchorX).toFixed(3);

  const layersClean = Array.isArray(layers) ? layers : [];
  // Stable JSON stringification with sorted keys
  function canonicalStringify(obj: unknown): string {
    if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
    if (Array.isArray(obj)) return "[" + obj.map(canonicalStringify).join(",") + "]";
    const keys = Object.keys(obj as Record<string, unknown>).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalStringify((obj as Record<string, unknown>)[k])).join(",") + "}";
  }

  const layersStr = canonicalStringify(layersClean);
  const layersHash = sha256Hex(layersStr);

  const settingsClean = settings && typeof settings === "object" ? settings : {};
  let payload = `v1:render:${mediaFingerprint}:${layout}:${cropStr}:${layersHash}`;
  if (Object.keys(settingsClean).length > 0) {
    const settingsStr = canonicalStringify(settingsClean);
    const settingsHash = sha256Hex(settingsStr);
    payload += `:${settingsHash}`;
  }
  return sha256Hex(payload);
}

/** Slices a layer interval across a deleted gap, preserving source timestamps. */
export function fragmentLayerInterval(layerInterval: [number, number], deletedGap: [number, number]): TimeRange[] {
  if (!Array.isArray(layerInterval) || layerInterval.length !== 2 || !Array.isArray(deletedGap) || deletedGap.length !== 2) {
    throw new TimelineError("layerInterval and deletedGap must be [start, end] pairs");
  }
  const ls = roundMs(layerInterval[0]);
  const le = roundMs(layerInterval[1]);
  const gs = roundMs(deletedGap[0]);
  const ge = roundMs(deletedGap[1]);
  if (ls >= le || gs >= ge) {
    throw new TimelineError("intervals must have start < end");
  }

  if (le <= gs) return [[ls, le]];
  if (ls >= ge) return [[ls, le]];
  if (ls >= gs && le <= ge) return [];
  if (ls < gs && le <= ge) return [[ls, gs]];
  if (ls >= gs && le > ge) return [[ge, le]];
  if (ls < gs && le > ge) return [[ls, gs], [ge, le]];
  return [];
}

/** Maps C2 proxy playback time to C3 master source seconds. */
export function c2ToC3Time(proxyTime: number, windowStart: number): number {
  return roundMs(roundMs(proxyTime) + roundMs(windowStart));
}

/** Maps C3 master source seconds to C2 proxy playback time. */
export function c3ToC2Time(sourceTime: number, windowStart: number): number {
  return roundMs(roundMs(sourceTime) - roundMs(windowStart));
}
