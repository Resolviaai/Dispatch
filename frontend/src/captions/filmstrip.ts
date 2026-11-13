/**
 * Filmstrip contract shared between the PC server payload and the timeline.
 *
 * Design Invariants:
 * 1. Strict validation: corrupted or stale payloads (invalid duration, non-positive
 *    intervals, un-ordered timestamps, wrong clip ownership) return null so callers
 *    never render a broken or distorted strip.
 * 2. Mathematically closed slot boundaries: left and right edges are computed
 *    from contiguous time boundaries so that right_i == left_{i+1} at any pxPerSec,
 *    eliminating 1px gaps and overlaps across all zoom levels.
 * 3. Partial slot preservation: the final slot spans min(interval, duration - t),
 *    never overhanging beyond the clip boundary.
 * 4. Coordinate alignment: accounts for filmstrip.scope ('render' vs 'source')
 *    and timeline timeMode ('clip' vs 'source') without drift.
 */

import { clipTimeToSourceTime } from './timelineInteraction';

export type FilmstripScope = 'render' | 'source' | 'none';

export interface FilmstripFrame {
  url: string;
  /** Clip-local slot start in seconds [0, duration). */
  t: number;
  /** Exact slot duration in seconds (including final partial slot). */
  span?: number;
}

export interface FilmstripData {
  clipId: string;
  duration: number;
  interval: number;
  count: number;
  scope: FilmstripScope;
  sourceStart: number;
  frames: FilmstripFrame[];
}

export interface FilmstripValidationOptions {
  expectedClipId?: string;
  expectedDuration?: number;
  expectedClipStart?: number;
}

function toFinite(value: unknown): number | null {
  const n = typeof value === 'number'
    ? value
    : (typeof value === 'string' ? parseFloat(value) : NaN);
  return Number.isFinite(n) ? n : null;
}

/**
 * Parse and strictly validate a filmstrip payload.
 * Returns null if the payload is corrupted, stale, or fails plan consistency.
 */
export function normalizeFilmstripPayload(
  raw: unknown,
  validation?: FilmstripValidationOptions
): FilmstripData | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const data = raw as Record<string, unknown>;

  // 1. Duration must be a strictly positive finite number
  const duration = toFinite(data.duration);
  if (duration === null || duration <= 0) return null;

  // 2. Clip ID ownership validation
  const clipId = typeof data.clip_id === 'string'
    ? data.clip_id
    : (typeof data.clipId === 'string' ? data.clipId : '');

  if (validation?.expectedClipId) {
    if (!clipId || clipId !== validation.expectedClipId) {
      return null;
    }
  }

  // 3. Scope and source start
  const rawScope = data.scope;
  const scope: FilmstripScope = rawScope === 'render' ? 'render' : (rawScope === 'source' ? 'source' : 'none');
  const sourceStart = Math.max(0, toFinite(data.sourceStart ?? data.source_start) ?? 0);

  // Stale payload check: validate COVERAGE instead of exact equality. A strip
  // generated for a wider edit window (before a trim narrowed the request, or a
  // wider window load) still maps correctly onto the requested [clipStart, clipEnd]
  // span; only payloads that fail to cover the requested span are stale.
  if (validation?.expectedDuration !== undefined) {
    const tolerance = 1.0;
    const requestedStart = scope === 'source' ? (validation.expectedClipStart ?? sourceStart) : 0;
    const requestedEnd = requestedStart + validation.expectedDuration;
    const stripStart = scope === 'source' ? sourceStart : 0;
    const stripEnd = stripStart + duration;
    if (stripStart > requestedStart + tolerance || stripEnd < requestedEnd - tolerance) {
      return null;
    }
  }

  // For source-scope strips the strip origin must not start AFTER the requested
  // clip start (it may legitimately start earlier to provide context).
  if (scope === 'source' && validation?.expectedClipStart !== undefined) {
    if (sourceStart > validation.expectedClipStart + 1.0) {
      return null;
    }
  }

  // 4. Frames array validation
  const rawFrames = data.frames;
  if (!Array.isArray(rawFrames) || rawFrames.length === 0) return null;

  const frames: FilmstripFrame[] = [];
  let prevT = -1;

  for (let i = 0; i < rawFrames.length; i++) {
    const item = rawFrames[i];
    if (item === null || typeof item !== 'object' || Array.isArray(item)) {
      continue; // Skip corrupted frame item
    }
    const frame = item as Record<string, unknown>;
    const url = typeof frame.url === 'string' ? frame.url.trim() : '';
    const t = toFinite(frame.t);

    // Frame must have a valid non-empty URL and a valid timestamp within [0, duration)
    // Never silently clamp out-of-bounds timestamps; drop corrupted/invalid frames.
    if (!url || t === null || t < 0 || t >= duration) {
      continue;
    }

    // Frames must be in strictly ascending chronological order (no duplicate timestamps)
    if (t <= prevT) {
      continue;
    }
    prevT = t;

    const rawSpan = toFinite(frame.span);
    const span = rawSpan !== null && rawSpan > 0 ? rawSpan : undefined;
    frames.push(span ? { url, t, span } : { url, t });
  }

  if (frames.length === 0) return null;

  // 5. Interval and count validation
  const intervalRaw = toFinite(data.interval);
  const interval = intervalRaw !== null && intervalRaw > 0
    ? intervalRaw
    : (frames.length === 1 ? duration : duration / frames.length);

  const countRaw = toFinite(data.count);
  const count = countRaw !== null && countRaw > 0 ? countRaw : frames.length;

  return {
    clipId,
    duration,
    interval,
    count,
    scope,
    sourceStart,
    frames,
  };
}

/**
 * Contiguous, mathematically closed placement of one frame on the timeline strip.
 *
 * Uses boundary coordinates:
 *   left  = round(rulerStart * pxPerSec)
 *   right = round(rulerEnd * pxPerSec)
 *   width = max(1, right - left)
 *
 * Because right_i is derived from the same mathematical timestamp as left_{i+1},
 * zero rounding gaps or overlaps can accumulate, across all scale factors.
 * The final slot spans exactly to clip end, honoring partial slots.
 */
export function filmstripFrameBox(
  frame: FilmstripFrame,
  filmstrip: FilmstripData,
  options: { pxPerSec: number; clipStart: number; isSourceMode: boolean },
): { left: number; width: number } | null {
  if (!filmstrip || !frame) return null;
  const pxPerSec = toFinite(options.pxPerSec);
  if (pxPerSec === null || pxPerSec <= 0) return null;
  const t = toFinite(frame.t);
  if (t === null || t < 0 || t >= filmstrip.duration) return null;

  // Actual slot duration: bounded strictly to remaining clip duration
  const maxRemaining = filmstrip.duration - t;
  if (maxRemaining <= 0) return null;

  let slotDuration = filmstrip.interval;
  if (typeof frame.span === 'number' && Number.isFinite(frame.span) && frame.span > 0) {
    slotDuration = Math.min(frame.span, maxRemaining);
  } else if (filmstrip.frames.length === 1) {
    slotDuration = filmstrip.duration;
  } else {
    slotDuration = Math.min(filmstrip.interval, maxRemaining);
  }
  if (slotDuration <= 0) return null;

  const slotStart = t;
  const slotEnd = t + slotDuration;

  // Map to ruler coordinate system, taking into account filmstrip source offset.
  // For scope 'source', the strip's own origin (filmstrip.sourceStart) is
  // authoritative: options.clipStart describes the clip hull, which can differ
  // from the strip origin after trims or wider edit-window loads.
  const effectiveClipStart = filmstrip.scope === 'source'
    ? filmstrip.sourceStart
    : (options.clipStart ?? filmstrip.sourceStart ?? 0);
  const rulerStart = options.isSourceMode
    ? clipTimeToSourceTime(slotStart, effectiveClipStart)
    : slotStart;

  const rulerEnd = options.isSourceMode
    ? clipTimeToSourceTime(slotEnd, effectiveClipStart)
    : slotEnd;

  const left = Math.max(0, Math.round(rulerStart * pxPerSec));
  const right = Math.max(left + 1, Math.round(rulerEnd * pxPerSec));

  return {
    left,
    width: Math.max(1, right - left),
  };
}

/** Build the honest single-thumbnail fallback an open editor shows while the strip is deferred/regenerating. */
export function thumbnailFallbackFilmstrip(
  thumbnailUrl: string,
  durationSeconds: number,
): FilmstripData | null {
  const url = typeof thumbnailUrl === 'string' ? thumbnailUrl.trim() : '';
  const duration = toFinite(durationSeconds) ?? 0;
  if (!url || duration <= 0) return null;
  return {
    clipId: '',
    duration,
    interval: duration,
    count: 1,
    scope: 'none',
    sourceStart: 0,
    frames: [{ url, t: 0 }],
  };
}
