export const MAX_CLIP_SELECTION_SECONDS = 180;
export const MIN_CLIP_SELECTION_SECONDS = 5;

export interface SourceRange {
  start: number;
  end: number;
}

export interface TrimPointerState {
  side: 'start' | 'end';
  pointerId: number;
  startX: number;
  initialRange: SourceRange;
}

export function clipTimeToSourceTime(clipTime: number, clipStart: number): number {
  return Math.max(0, clipStart + (Number.isFinite(clipTime) ? clipTime : 0));
}

export function sourceTimeToClipTime(sourceTime: number, clipStart: number, clipDuration: number): number {
  const local = (Number.isFinite(sourceTime) ? sourceTime : clipStart) - clipStart;
  return Math.min(Math.max(0, clipDuration), Math.max(0, local));
}

export function captionTimeIsActive(time: number, start: number, end: number): boolean {
  return Number.isFinite(time) && time >= start && time <= end;
}

export function beginTrimPointer(
  side: 'start' | 'end',
  pointerId: number,
  clientX: number,
  range: SourceRange,
): TrimPointerState {
  return { side, pointerId, startX: clientX, initialRange: { ...range } };
}

/** Apply a real pointer delta to the active source-time selection. */
export function moveTrimPointer(
  pointer: TrimPointerState,
  pointerId: number,
  clientX: number,
  pixelsPerSecond: number,
  sourceDuration: number,
): SourceRange {
  if (pointer.pointerId !== pointerId || pixelsPerSecond <= 0 || !Number.isFinite(clientX)) {
    return pointer.initialRange;
  }
  const sourceEnd = Number.isFinite(sourceDuration) && sourceDuration > 0 ? sourceDuration : 0;
  const delta = (clientX - pointer.startX) / pixelsPerSecond;
  const { start, end } = pointer.initialRange;
  const minLength = Math.min(MIN_CLIP_SELECTION_SECONDS, sourceEnd);
  const maxLength = Math.min(MAX_CLIP_SELECTION_SECONDS, sourceEnd);

  if (pointer.side === 'start') {
    const minStart = Math.max(0, end - maxLength);
    const maxStart = Math.max(minStart, end - minLength);
    return { start: clamp(start + delta, minStart, maxStart), end };
  }

  const minEnd = Math.min(sourceEnd, start + minLength);
  const maxEnd = Math.min(sourceEnd, start + maxLength);
  return { start, end: clamp(end + delta, minEnd, maxEnd) };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
