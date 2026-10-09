import React, { useRef, useState, useCallback, useMemo, useEffect } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  Film,
  Type,
  Music,
  Mic,
  Image as ImageIcon,
  Eye,
  EyeOff,
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word } from './types';

interface CaptionTimelineProps {
  track: CaptionTrack | null;
  currentTime: number;
  duration: number;
  selectedGroupId: string | null;
  isVideoSelected?: boolean;
  isPlaying: boolean;
  filmstripFrames?: string[];
  onSeek: (seconds: number) => void;
  onTogglePlay?: () => void;
  onSelectGroup: (groupId: string | null) => void;
  onSelectVideo?: () => void;
  onUpdateGroup?: (groupId: string, patch: Partial<CaptionGroup>) => void;
  onSplitGroup?: (groupId: string, splitTime: number) => void;
}

const clamp = (val: number, min: number, max: number) => Math.min(max, Math.max(min, val));

function formatTimeRuler(sec: number): string {
  const safe = Math.max(0, Number.isFinite(sec) ? sec : 0);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

interface DragSession {
  type: 'move' | 'trim-start' | 'trim-end';
  groupId: string;
  startX: number;
  origStart: number;
  origEnd: number;
  origWords: Word[];
}

export const CaptionTimeline: React.FC<CaptionTimelineProps> = ({
  track,
  currentTime,
  duration = 40,
  selectedGroupId,
  isVideoSelected = false,
  isPlaying,
  filmstripFrames = [],
  onSeek,
  onSelectGroup,
  onSelectVideo,
  onUpdateGroup,
}) => {
  const viewportRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState<number>(340);
  const [pxPerSec, setPxPerSec] = useState<number>(55);
  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);
  const [dragSession, setDragSession] = useState<DragSession | null>(null);
  const [hiddenTracks, setHiddenTracks] = useState<Record<number, boolean>>({});
  const [scrollLeft, setScrollLeft] = useState<number>(0);
  const canvasDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const leftRailRef = useRef<HTMLDivElement>(null);

  const safeDuration = Math.max(1, duration);
  const totalTimelineWidth = Math.max(340, Math.round(safeDuration * pxPerSec));

  // Measure timeline viewport width to anchor fixed center playhead (50% horizontal center)
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const updateWidth = () => {
      if (el.clientWidth > 0) {
        setViewportWidth(el.clientWidth);
      }
    };
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const halfWidth = Math.max(80, Math.round(viewportWidth / 2));

  // =========================================================================
  // UNIFORM DYNAMIC MULTI-TRACK ALLOCATION
  // Maps any media type to uniform tracks: Main, L1, L2, L3, L4...
  // =========================================================================
  const dynamicTracks = useMemo(() => {
    if (!track || !track.groups || track.groups.length === 0) {
      return [];
    }

    const sorted = [...track.groups].sort((a, b) => a.start - b.start);
    const buckets: CaptionGroup[][] = [];

    sorted.forEach((grp) => {
      if (grp.trackIndex !== undefined && grp.trackIndex >= 0) {
        while (buckets.length <= grp.trackIndex) {
          buckets.push([]);
        }
        buckets[grp.trackIndex].push(grp);
        return;
      }

      let placed = false;
      for (let i = 0; i < buckets.length; i++) {
        const bucket = buckets[i];
        const hasOverlap = bucket.some(
          (existing) => Math.max(grp.start, existing.start) < Math.min(grp.end, existing.end)
        );
        if (!hasOverlap) {
          bucket.push(grp);
          placed = true;
          break;
        }
      }

      if (!placed) {
        buckets.push([grp]);
      }
    });

    return buckets;
  }, [track]);

  // Dynamic ruler ticks based on zoom level
  const rulerTicks = useMemo(() => {
    let step = 5;
    if (pxPerSec >= 80) step = 1;
    else if (pxPerSec >= 45) step = 2;
    else if (pxPerSec >= 25) step = 5;
    else step = 10;

    const count = Math.ceil(safeDuration / step);
    const ticks: number[] = [];
    for (let i = 0; i <= count; i++) {
      const t = i * step;
      if (t <= safeDuration) ticks.push(t);
    }
    return ticks;
  }, [safeDuration, pxPerSec]);

  // Zoom adjustments: keep the current time anchored at fixed center playhead
  const handleZoom = (delta: number) => {
    const nextPx = clamp(pxPerSec + delta, 20, 140);
    if (nextPx === pxPerSec) return;
    setPxPerSec(nextPx);
    requestAnimationFrame(() => {
      if (scrollContainerRef.current) {
        scrollContainerRef.current.scrollLeft = currentTime * nextPx;
      }
    });
  };

  const handleZoomFit = () => {
    const scrollEl = scrollContainerRef.current;
    if (!scrollEl) return;
    const availableWidth = Math.max(160, viewportWidth - 40);
    const fitPx = clamp(Math.floor(availableWidth / safeDuration), 15, 60);
    setPxPerSec(fitPx);
    requestAnimationFrame(() => {
      if (scrollContainerRef.current) {
        scrollContainerRef.current.scrollLeft = currentTime * fitPx;
      }
    });
  };

  // Follow playhead smoothly during playback using animation-frame scheduling
  useEffect(() => {
    if (!isPlaying || isScrubbing || dragSession) return;
    let animId: number;

    const tick = () => {
      const scrollEl = scrollContainerRef.current;
      if (scrollEl) {
        const targetScroll = currentTime * pxPerSec;
        if (Math.abs(scrollEl.scrollLeft - targetScroll) > 0.5) {
          scrollEl.scrollLeft = targetScroll;
        }
      }
      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [isPlaying, isScrubbing, dragSession, currentTime, pxPerSec]);

  // When paused and external seek happens (e.g. transport scrub), center timeline
  useEffect(() => {
    if (isPlaying || isScrubbing || dragSession) return;
    const scrollEl = scrollContainerRef.current;
    if (!scrollEl) return;

    const targetScroll = currentTime * pxPerSec;
    if (Math.abs(scrollEl.scrollLeft - targetScroll) > 1) {
      scrollEl.scrollLeft = targetScroll;
    }
  }, [currentTime]);

  // Scroll listener: updates playback time beneath the fixed center playhead
  // CRITICAL: Only call onSeek when manually scrubbing or paused!
  // During playback, programmatic auto-scroll must NOT trigger onSeek/video.currentTime!
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const sl = e.currentTarget.scrollLeft;
    setScrollLeft(sl);
    if (leftRailRef.current) {
      leftRailRef.current.scrollTop = e.currentTarget.scrollTop;
    }
    if (isScrubbing || !isPlaying) {
      const scrubTime = clamp(sl / pxPerSec, 0, safeDuration);
      onSeek(Number(scrubTime.toFixed(2)));
    }
  };

  // Mouse drag on empty timeline canvas or ruler (desktop horizontal swipe/scrub)
  const handleCanvasPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragSession) return;
    if ((e.target as HTMLElement).closest('[data-layer-block="true"]')) return;
    setIsScrubbing(true);
    canvasDragRef.current = {
      startX: e.clientX,
      startScrollLeft: scrollContainerRef.current?.scrollLeft || 0,
    };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handleCanvasPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!canvasDragRef.current || !scrollContainerRef.current) return;
    const deltaX = e.clientX - canvasDragRef.current.startX;
    const targetScroll = Math.max(0, canvasDragRef.current.startScrollLeft - deltaX);
    scrollContainerRef.current.scrollLeft = targetScroll;
    const scrubTime = clamp(targetScroll / pxPerSec, 0, safeDuration);
    onSeek(Number(scrubTime.toFixed(2)));
  };

  const handleCanvasPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (canvasDragRef.current) {
      canvasDragRef.current = null;
      setIsScrubbing(false);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch { }
    }
  };

  // Draggable / Trimmable Layer Block Pointer Handlers
  const handleBlockPointerDown = (
    e: React.PointerEvent,
    grp: CaptionGroup,
    type: 'move' | 'trim-start' | 'trim-end'
  ) => {
    e.stopPropagation();
    onSelectGroup(grp.id);

    setDragSession({
      type,
      groupId: grp.id,
      startX: e.clientX,
      origStart: grp.start,
      origEnd: grp.end,
      origWords: grp.words ? [...grp.words] : [],
    });

    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handleBlockPointerMove = (e: React.PointerEvent) => {
    if (!dragSession || !onUpdateGroup) return;
    e.stopPropagation();

    const deltaSec = (e.clientX - dragSession.startX) / pxPerSec;
    const origDuration = Math.max(0.2, dragSession.origEnd - dragSession.origStart);

    if (dragSession.type === 'move') {
      let nextStart = dragSession.origStart + deltaSec;
      let nextEnd = nextStart + origDuration;

      // Snapping to playhead (within ±0.15s)
      if (Math.abs(nextStart - currentTime) < 0.15) {
        nextStart = currentTime;
        nextEnd = nextStart + origDuration;
      } else if (Math.abs(nextEnd - currentTime) < 0.15) {
        nextEnd = currentTime;
        nextStart = nextEnd - origDuration;
      }

      // Snapping to boundaries
      if (nextStart < 0) {
        nextStart = 0;
        nextEnd = origDuration;
      } else if (nextEnd > safeDuration) {
        nextEnd = safeDuration;
        nextStart = Math.max(0, safeDuration - origDuration);
      }

      const timeShift = nextStart - dragSession.origStart;
      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((w.start + timeShift).toFixed(2)),
        end: Number((w.end + timeShift).toFixed(2)),
      }));

      onUpdateGroup(dragSession.groupId, {
        start: Number(nextStart.toFixed(2)),
        end: Number(nextEnd.toFixed(2)),
        words: nextWords,
      });

      onSeek(Number(nextStart.toFixed(2)));
    } else if (dragSession.type === 'trim-start') {
      let nextStart = dragSession.origStart + deltaSec;
      if (Math.abs(nextStart - currentTime) < 0.15) {
        nextStart = currentTime;
      }

      nextStart = clamp(nextStart, 0, dragSession.origEnd - 0.2);
      const newDuration = dragSession.origEnd - nextStart;
      const ratio = newDuration / origDuration;

      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((nextStart + (w.start - dragSession.origStart) * ratio).toFixed(2)),
        end: Number((nextStart + (w.end - dragSession.origStart) * ratio).toFixed(2)),
      }));

      onUpdateGroup(dragSession.groupId, {
        start: Number(nextStart.toFixed(2)),
        words: nextWords,
      });

      onSeek(Number(nextStart.toFixed(2)));
    } else if (dragSession.type === 'trim-end') {
      let nextEnd = dragSession.origEnd + deltaSec;
      if (Math.abs(nextEnd - currentTime) < 0.15) {
        nextEnd = currentTime;
      }

      nextEnd = clamp(nextEnd, dragSession.origStart + 0.2, safeDuration);
      const newDuration = nextEnd - dragSession.origStart;
      const ratio = newDuration / origDuration;

      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((dragSession.origStart + (w.start - dragSession.origStart) * ratio).toFixed(2)),
        end: Number((dragSession.origStart + (w.end - dragSession.origStart) * ratio).toFixed(2)),
      }));

      onUpdateGroup(dragSession.groupId, {
        end: Number(nextEnd.toFixed(2)),
        words: nextWords,
      });

      onSeek(Number(nextEnd.toFixed(2)));
    }
  };

  const handleBlockPointerUp = (e: React.PointerEvent) => {
    if (dragSession) {
      setDragSession(null);
      try {
        (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch { }
    }
  };

  const toggleTrackVisibility = (idx: number) => {
    setHiddenTracks((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  // Palette and Icon based on layer type
  const getLayerMeta = (grp: CaptionGroup, idx: number) => {
    if (grp.type === 'audio') {
      return {
        icon: <Music size={11} className="mr-1 shrink-0" />,
        bg: 'bg-[#059669]/90',
        border: 'border-[#10B981]',
        text: 'text-emerald-100',
        name: grp.label || 'Audio',
      };
    }
    if (grp.type === 'voiceover') {
      return {
        icon: <Mic size={11} className="mr-1 shrink-0" />,
        bg: 'bg-[#D97706]/90',
        border: 'border-[#F59E0B]',
        text: 'text-amber-100',
        name: grp.label || 'Voiceover',
      };
    }
    if (grp.type === 'overlay') {
      const isVideo = grp.overlay_type === 'video';
      return {
        icon: isVideo ? <Film size={11} className="mr-1 shrink-0" /> : <ImageIcon size={11} className="mr-1 shrink-0" />,
        bg: isVideo ? 'bg-[#4338CA]/90' : 'bg-[#7C3AED]/90',
        border: isVideo ? 'border-[#6366F1]' : 'border-[#8B5CF6]',
        text: 'text-purple-100',
        name: grp.label || (isVideo ? 'Video' : 'Image'),
      };
    }
    // Default text / caption
    return {
      icon: <Type size={11} className="mr-1 shrink-0" />,
      bg: 'bg-[#2563EB]/90',
      border: 'border-[#3B82F6]',
      text: 'text-blue-100',
      name: (grp.words || []).map((w) => w.text || '').join(' ') || 'Text',
    };
  };

  return (
    <section
      className="h-[370px] max-h-[370px] bg-[#13161B] overflow-hidden select-none flex flex-col shrink-0 min-h-0"
    >
      {/* TIMELINE UTILITY BAR: Clean compact zoom controls */}
      <div className="h-6 px-2 bg-[#16191E] border-b border-[#232832] flex items-center justify-end text-text-secondary text-[10px] shrink-0">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => handleZoom(-15)}
            className="w-5 h-5 rounded flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-[#232832] active:scale-95 transition-all"
            title="Zoom Out"
            aria-label="Zoom Out"
          >
            <ZoomOut size={12} />
          </button>

          <button
            type="button"
            onClick={() => handleZoom(15)}
            className="w-5 h-5 rounded flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-[#232832] active:scale-95 transition-all"
            title="Zoom In"
            aria-label="Zoom In"
          >
            <ZoomIn size={12} />
          </button>

          <button
            type="button"
            onClick={handleZoomFit}
            className="w-5 h-5 rounded flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-[#232832] active:scale-95 transition-all"
            title="Fit to Timeline"
            aria-label="Fit to Timeline"
          >
            <Maximize2 size={11} />
          </button>
        </div>
      </div>

      {/* TIMELINE MAIN BODY: Sticky Left Rail + Canvas Viewport with Fixed Center Playhead */}
      <div className="flex-1 min-h-0 flex relative overflow-hidden">
        {/* STICKY LEFT RAIL: Main + L1, L2, L3... */}
        <div className="w-12 shrink-0 bg-[#16191E] border-r border-[#232832] flex flex-col z-20">
          {/* Seamless Ruler Header Cell */}
          <div className="h-6 border-b border-[#232832] bg-[#16191E]" />

          {/* Track Headers (scrolls vertically with tracks) */}
          <div ref={leftRailRef} className="flex-1 min-h-0 overflow-hidden flex flex-col">
            {/* Header: Main Video Track */}
            <button
              type="button"
              onClick={onSelectVideo}
              className={`h-10 border-b border-[#232832] flex items-center justify-center gap-1 transition-colors ${isVideoSelected ? 'bg-primary/20 text-primary font-bold' : 'text-text-muted hover:text-text-secondary'
                }`}
              title="Select Main Video Track"
            >
              <Film size={11} />
              <span className="text-[9px] font-mono font-bold">Main</span>
            </button>

            {/* Headers: Uniform Layers (L1, L2, L3...) */}
            {dynamicTracks.map((_, idx) => (
              <div
                key={`hdr_${idx}`}
                className="h-10 border-b border-[#232832] flex items-center justify-between px-1.5 bg-[#16191E]"
              >
                <span className="text-[10px] font-bold text-text-secondary font-mono">
                  L{idx + 1}
                </span>
                <button
                  type="button"
                  onClick={() => toggleTrackVisibility(idx)}
                  className="text-text-muted hover:text-text-secondary active:scale-95"
                  title={`Toggle Track L${idx + 1}`}
                >
                  {!hiddenTracks[idx] ? <Eye size={10} /> : <EyeOff size={10} className="text-red-400" />}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* TIMELINE CANVAS VIEWPORT: Fixed Playhead + Ruler + Synchronized Scroll Canvas */}
        <div
          ref={viewportRef}
          className="flex-1 min-h-0 flex flex-col relative overflow-hidden"
        >
          {/* PINNED TIME RULER: Synchronized horizontally with scrollLeft */}
          <div
            className="h-6 shrink-0 border-b border-[#232832] bg-[#13161B] overflow-hidden relative cursor-ew-resize select-none"
            onPointerDown={handleCanvasPointerDown}
            onPointerMove={handleCanvasPointerMove}
            onPointerUp={handleCanvasPointerUp}
            onPointerCancel={handleCanvasPointerUp}
          >
            <div
              className="relative h-full flex items-center pointer-events-none"
              style={{
                transform: `translateX(${-scrollLeft + halfWidth}px)`,
                width: `${totalTimelineWidth}px`,
                willChange: 'transform',
              }}
            >
              {rulerTicks.map((t) => (
                <div
                  key={t}
                  className="absolute top-0 bottom-0 flex flex-col items-center -translate-x-1/2"
                  style={{ left: `${t * pxPerSec}px` }}
                >
                  <span className="text-[9px] font-mono tabular-nums text-text-secondary font-medium mt-0.5">
                    {formatTimeRuler(t)}
                  </span>
                  <div className="w-[1px] h-1.5 bg-[#3A4350]" />
                </div>
              ))}
            </div>
          </div>

          {/* HORIZONTALLY & VERTICALLY SCROLLABLE TRACK CANVAS */}
          <div
            ref={scrollContainerRef}
            onScroll={handleScroll}
            onTouchStart={() => setIsScrubbing(true)}
            onTouchEnd={() => setIsScrubbing(false)}
            onPointerDown={handleCanvasPointerDown}
            onPointerMove={handleCanvasPointerMove}
            onPointerUp={handleCanvasPointerUp}
            onPointerCancel={handleCanvasPointerUp}
            className="flex-1 overflow-x-auto overflow-y-auto relative bg-[#0D1014] no-scrollbar select-none cursor-ew-resize"
            style={{ touchAction: 'pan-x' }}
          >
            <div
              className="relative min-h-full"
              style={{
                width: `${totalTimelineWidth + halfWidth * 2}px`,
                paddingLeft: `${halfWidth}px`,
                paddingRight: `${halfWidth}px`,
              }}
            >
              {/* BACKGROUND VERTICAL GRID LINES */}
              <div
                className="absolute inset-y-0 pointer-events-none"
                style={{
                  left: `${halfWidth}px`,
                  width: `${totalTimelineWidth}px`,
                }}
              >
                {rulerTicks.map((t) => (
                  <div
                    key={`grid_${t}`}
                    className="absolute top-0 bottom-0 w-[1px] bg-[#1A1F26]"
                    style={{ left: `${t * pxPerSec}px` }}
                  />
                ))}
              </div>

              {/* 1. MAIN VIDEO TRACK */}
              <div
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectVideo?.();
                }}
                className={`h-10 border-b border-[#232832] relative p-0.5 overflow-hidden cursor-pointer transition-colors ${isVideoSelected ? 'bg-primary/10 ring-1 ring-primary inset-0' : ''
                  }`}
                style={{ width: `${totalTimelineWidth}px` }}
              >
                <div className="w-full h-full rounded bg-[#090C0F] border border-[#232832]/60 flex items-center overflow-hidden">
                  {filmstripFrames.length > 0 ? (
                    filmstripFrames.map((frameUrl, idx) => (
                      <div
                        key={idx}
                        className="flex-1 h-full border-r border-black/50 overflow-hidden bg-cover bg-center"
                        style={{ backgroundImage: `url(${frameUrl})` }}
                      />
                    ))
                  ) : (
                    <div className="w-full h-full flex items-center justify-around opacity-30 px-2">
                      {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="w-10 h-7 rounded bg-[#2A313C] border border-[#3A4350]" />
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* 2. UNIFORM LAYER TRACKS (L1, L2, L3...) */}
              {dynamicTracks.map((rowGroups, trackIdx) => {
                if (hiddenTracks[trackIdx]) {
                  return (
                    <div
                      key={`track_${trackIdx}`}
                      className="h-10 border-b border-[#232832] relative bg-black/20"
                      style={{ width: `${totalTimelineWidth}px` }}
                    />
                  );
                }

                return (
                  <div
                    key={`track_${trackIdx}`}
                    className="h-10 border-b border-[#232832] relative p-1"
                    style={{ width: `${totalTimelineWidth}px` }}
                  >
                    {rowGroups.map((grp) => {
                      const left = grp.start * pxPerSec;
                      const width = Math.max(32, (grp.end - grp.start) * pxPerSec);
                      const isSelected = selectedGroupId === grp.id;
                      const isSpoken = currentTime >= grp.start && currentTime <= grp.end;
                      const meta = getLayerMeta(grp, trackIdx);

                      return (
                        <div
                          key={grp.id}
                          data-layer-block="true"
                          className={`absolute top-1 bottom-1 rounded-lg flex items-center overflow-hidden transition-shadow select-none ${isSelected
                            ? 'bg-[#2563EB] border-2 border-white text-white font-bold shadow-[0_0_12px_rgba(37,99,235,0.6)] z-10'
                            : isSpoken
                              ? `${meta.bg} border-2 border-white/80 text-white font-semibold`
                              : `${meta.bg} border ${meta.border} ${meta.text} hover:opacity-95`
                            }`}
                          style={{ left: `${left}px`, width: `${width}px` }}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectGroup(grp.id);
                          }}
                        >
                          {/* Left Trim Handle */}
                          <div
                            data-handle="start"
                            onPointerDown={(e) => handleBlockPointerDown(e, grp, 'trim-start')}
                            onPointerMove={handleBlockPointerMove}
                            onPointerUp={handleBlockPointerUp}
                            onPointerCancel={handleBlockPointerUp}
                            className="w-3.5 h-full flex items-center justify-center cursor-ew-resize bg-black/30 hover:bg-black/50 active:bg-primary transition-colors shrink-0"
                            title="Trim Start"
                          >
                            <div className="w-[2px] h-3 bg-white/70 rounded-full" />
                          </div>

                          {/* Draggable Layer Body */}
                          <div
                            onPointerDown={(e) => handleBlockPointerDown(e, grp, 'move')}
                            onPointerMove={handleBlockPointerMove}
                            onPointerUp={handleBlockPointerUp}
                            onPointerCancel={handleBlockPointerUp}
                            className="flex-1 h-full flex items-center justify-between px-1.5 cursor-grab active:cursor-grabbing overflow-hidden min-w-0"
                          >
                            <div className="flex items-center min-w-0 truncate">
                              {grp.type === 'overlay' && grp.overlay_url && grp.overlay_type === 'image' ? (
                                <img src={grp.overlay_url} alt="" className="w-3.5 h-3.5 rounded object-cover mr-1 shrink-0 border border-white/30" />
                              ) : (
                                meta.icon
                              )}
                              <span className="text-[10px] font-sans truncate whitespace-nowrap leading-none min-w-0">
                                {meta.name}
                              </span>
                            </div>
                            {isSelected && (
                              <span className="ml-1 font-mono tabular-nums text-[9px] text-white/90 bg-black/40 px-1 py-0.5 rounded shrink-0 font-bold">
                                {(grp.end - grp.start).toFixed(1)}s
                              </span>
                            )}
                          </div>

                          {/* Right Trim Handle */}
                          <div
                            data-handle="end"
                            onPointerDown={(e) => handleBlockPointerDown(e, grp, 'trim-end')}
                            onPointerMove={handleBlockPointerMove}
                            onPointerUp={handleBlockPointerUp}
                            onPointerCancel={handleBlockPointerUp}
                            className="w-3.5 h-full flex items-center justify-center cursor-ew-resize bg-black/30 hover:bg-black/50 active:bg-primary transition-colors shrink-0"
                            title="Trim End"
                          >
                            <div className="w-[2px] h-3 bg-white/70 rounded-full" />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>

          {/* FIXED CENTER PLAYHEAD: Stays permanently at horizontal center of viewport */}
          <div className="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 pointer-events-none z-30 flex flex-col items-center">
            {/* Top Playhead Notch on Ruler */}
            <div className="w-3 h-3 bg-white rounded-sm shadow-md border border-black/30 flex items-center justify-center">
              <div className="w-[1px] h-2 bg-[#16191E]" />
            </div>
            {/* Solid crisp vertical white line dropping through all tracks */}
            <div className="w-[1.5px] flex-1 bg-white shadow-[0_0_6px_rgba(255,255,255,0.9)]" />
          </div>
        </div>
      </div>
    </section>
  );
};
