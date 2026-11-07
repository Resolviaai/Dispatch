import React, { useMemo, useRef, useCallback } from 'react';
import { FilmstripData } from './filmstrip';

export interface MiniFilmstripProps {
  filmstrip: FilmstripData | null;
  clipDuration: number;
  sourceStart: number;
  sourceEnd: number;
  currentTime: number;
  onSeek?: (sourceTime: number) => void;
  clipStartOffset?: number;
}

function clamp(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, val));
}

function formatBoundaryTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00.00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const cs = Math.floor((seconds % 1) * 100);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`;
}

export const MiniFilmstrip: React.FC<MiniFilmstripProps> = ({
  filmstrip,
  clipDuration,
  sourceStart,
  sourceEnd,
  currentTime,
  onSeek,
  clipStartOffset = 0,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);

  // 1. Calculate an honest context window around the proposed cut
  const cutDuration = Math.max(0.05, sourceEnd - sourceStart);
  const padSec = Math.max(1.0, Math.min(2.5, cutDuration * 0.75));

  const winStart = Math.max(0, sourceStart - padSec);
  const totalBoundary = clipDuration > 0 ? clipDuration : Math.max(sourceEnd + padSec, 60);
  const winEnd = Math.min(totalBoundary, sourceEnd + padSec);
  const winSpan = Math.max(0.1, winEnd - winStart);

  // 2. Proportional cut region percentages within this window
  const cutLeftPct = clamp(((sourceStart - winStart) / winSpan) * 100, 0, 95);
  const rawCutWidthPct = ((sourceEnd - sourceStart) / winSpan) * 100;
  const cutWidthPct = clamp(rawCutWidthPct, 3, 100 - cutLeftPct);

  // 3. Proportional playhead percentage (if currentTime falls within this window)
  const isPlayheadInWindow = currentTime >= winStart && currentTime <= winEnd;
  const playheadPct = isPlayheadInWindow ? clamp(((currentTime - winStart) / winSpan) * 100, 0, 100) : null;

  // 4. Frames within this context window from filmstrip
  const isSingleFallback = useMemo(() => {
    if (!filmstrip || !filmstrip.frames || filmstrip.frames.length === 0) return false;
    if (filmstrip.frames.length === 1) return true;
    const firstSpan = filmstrip.frames[0]?.span || 0;
    return firstSpan > 5.0;
  }, [filmstrip]);

  const fallbackUrl = filmstrip?.frames?.[0]?.url || null;

  const relevantFrames = useMemo(() => {
    if (!filmstrip || !filmstrip.frames || filmstrip.frames.length === 0 || isSingleFallback) {
      return [];
    }

    const interval = filmstrip.interval || 1.0;
    const isSourceScope = filmstrip.scope === 'source';
    const effectiveOffset = isSourceScope ? (filmstrip.sourceStart ?? clipStartOffset) : 0;

    return filmstrip.frames
      .map((f) => {
        const frameSourceTime = isSourceScope ? f.t : f.t + effectiveOffset;
        const frameSpan = f.span || interval;
        return {
          url: f.url,
          sourceTime: frameSourceTime,
          span: frameSpan,
        };
      })
      .filter((f) => f.sourceTime + f.span >= winStart && f.sourceTime <= winEnd)
      .map((f) => {
        const leftPct = clamp(((f.sourceTime - winStart) / winSpan) * 100, -20, 100);
        const widthPct = Math.max(2, (f.span / winSpan) * 100);
        return {
          url: f.url,
          leftPct,
          widthPct,
        };
      });
  }, [filmstrip, winStart, winEnd, winSpan, clipStartOffset, isSingleFallback]);

  // 5. Stylized acoustic waveform bars (representing speech energy vs silence)
  const waveformBars = useMemo(() => {
    const barsCount = 36;
    const bars: Array<{ heightPct: number; isInsideCut: boolean }> = [];
    for (let i = 0; i < barsCount; i++) {
      const barPos = i / (barsCount - 1);
      const barTime = winStart + barPos * winSpan;
      const isInsideCut = barTime >= sourceStart && barTime <= sourceEnd;
      // In dead air / pause cuts, acoustic energy decays to near zero; in speech before/after it modulates
      let heightPct: number;
      if (isInsideCut) {
        // Quiet floor during removal
        heightPct = 12 + Math.sin(i * 1.5) * 8;
      } else {
        // Active speech energy
        heightPct = 35 + Math.abs(Math.sin(i * 0.9) * 45) + (i % 3 === 0 ? 15 : 0);
      }
      bars.push({ heightPct: clamp(heightPct, 8, 92), isInsideCut });
    }
    return bars;
  }, [winStart, winSpan, sourceStart, sourceEnd]);

  // 6. Interactive scrubbing along miniature timeline
  const handleSeekFromPointer = useCallback(
    (clientX: number) => {
      if (!onSeek || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      if (rect.width <= 0) return;
      const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
      const targetSourceTime = winStart + ratio * winSpan;
      onSeek(targetSourceTime);
    },
    [onSeek, winStart, winSpan]
  );

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    isDraggingRef.current = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    handleSeekFromPointer(e.clientX);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDraggingRef.current) return;
    e.stopPropagation();
    handleSeekFromPointer(e.clientX);
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDraggingRef.current) return;
    e.stopPropagation();
    isDraggingRef.current = false;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="w-full flex flex-col gap-1 select-none">
      {/* Time Boundary Labels */}
      <div className="flex items-center justify-between text-[10px] font-mono tabular-nums text-text-muted px-0.5">
        <span>{formatBoundaryTime(winStart)}</span>
        <span className="text-red-400 font-semibold flex items-center gap-1">
          <span>Cut:</span>
          <span>{formatBoundaryTime(sourceStart)}</span>
          <span>&rarr;</span>
          <span>{formatBoundaryTime(sourceEnd)}</span>
        </span>
        <span>{formatBoundaryTime(winEnd)}</span>
      </div>

      {/* Miniature Timeline Track */}
      <div
        ref={containerRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        className="relative w-full h-14 rounded-lg bg-[#141414] border border-border overflow-hidden cursor-pointer touch-none shadow-inner"
        role="slider"
        aria-label="Detection miniature timeline"
        aria-valuemin={winStart}
        aria-valuemax={winEnd}
        aria-valuenow={currentTime}
      >
        {/* Layer 1: Frame Thumbnails (Clean filmstrip reel) */}
        {isSingleFallback && fallbackUrl ? (
          <div className="absolute inset-0 flex items-stretch overflow-hidden pointer-events-none divide-x divide-black/60">
            {Array.from({ length: 6 }).map((_, idx) => (
              <div key={`fallback_tile_${idx}`} className="flex-1 h-full overflow-hidden relative bg-black/40">
                <img
                  src={fallbackUrl}
                  alt=""
                  className="w-full h-full object-cover opacity-80 pointer-events-none"
                  loading="lazy"
                  draggable={false}
                />
              </div>
            ))}
          </div>
        ) : relevantFrames.length > 0 ? (
          <div className="absolute inset-0 flex items-stretch pointer-events-none overflow-hidden">
            {relevantFrames.map((frame, idx) => (
              <img
                key={`mini_frame_${idx}`}
                src={frame.url}
                alt=""
                className="absolute top-0 bottom-0 h-full object-cover opacity-80 pointer-events-none"
                style={{
                  left: `${frame.leftPct}%`,
                  width: `${frame.widthPct}%`,
                }}
                loading="lazy"
                draggable={false}
              />
            ))}
          </div>
        ) : (
          /* Honest fallback pattern when thumbnails are deferred/unavailable */
          <div className="absolute inset-0 flex items-center justify-between px-2 opacity-20 pointer-events-none">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-full w-px bg-white/20" />
            ))}
          </div>
        )}

        {/* Layer 2: Waveform Acoustic Energy Overlay (Refined, subtle needles along bottom) */}
        <div className="absolute inset-x-0 bottom-0 h-4 flex items-end justify-between px-1.5 pointer-events-none z-10">
          {waveformBars.map((bar, i) => (
            <div
              key={`bar_${i}`}
              className={`w-0.5 rounded-t-sm transition-all duration-100 ${
                bar.isInsideCut ? 'bg-red-400/30' : 'bg-white/25'
              }`}
              style={{ height: `${bar.heightPct}%` }}
            />
          ))}
        </div>

        {/* Layer 3: Highlighted Removal Region (Captions-style Red Box) */}
        <div
          className="absolute top-0 bottom-0 z-20 bg-red-500/25 border-x-2 border-red-500 pointer-events-none flex flex-col justify-between items-center py-1 transition-all"
          style={{
            left: `${cutLeftPct}%`,
            width: `${cutWidthPct}%`,
          }}
        >
          {/* Top Duration Chip */}
          <span className="px-1.5 py-0.2 rounded bg-black/85 border border-red-500/40 font-mono tabular-nums text-[10px] font-bold text-red-300 shadow-sm leading-tight">
            &minus;{cutDuration.toFixed(1)}s
          </span>

          {/* Center Cut Indicator */}
          <span className="text-[9px] font-semibold uppercase tracking-wider text-red-300/90 bg-red-950/60 px-1 rounded">
            Cut
          </span>

          {/* Subtle bottom tick */}
          <div className="w-1.5 h-0.5 bg-red-400 rounded-full" />
        </div>

        {/* Start boundary cut marker */}
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 z-20 pointer-events-none"
          style={{ left: `${cutLeftPct}%` }}
        >
          <div className="w-1.5 h-1.5 bg-red-500 rounded-full -translate-x-[2px] -translate-y-0.5 shadow" />
        </div>

        {/* End boundary cut marker */}
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 z-20 pointer-events-none"
          style={{ left: `${cutLeftPct + cutWidthPct}%` }}
        >
          <div className="w-1.5 h-1.5 bg-red-500 rounded-full -translate-x-[3px] -translate-y-0.5 shadow" />
        </div>

        {/* Layer 4: Interactive / Moving Playhead */}
        {playheadPct !== null && (
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white z-30 pointer-events-none shadow-md transition-all duration-75"
            style={{ left: `${playheadPct}%` }}
          >
            {/* Playhead Pin */}
            <div className="w-2.5 h-2.5 bg-white rounded-full -translate-x-1 -top-1 shadow" />
          </div>
        )}
      </div>
    </div>
  );
};
