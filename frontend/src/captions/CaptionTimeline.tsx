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
  Sparkles,
  Magnet,
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
  mediaElementRef?: React.RefObject<HTMLVideoElement>;
  clipStart?: number;
  clipEnd?: number;
  paddedStart?: number;
  paddedEnd?: number;
  onTrimChange?: (newStart: number, newEnd: number) => void;
  onSeek: (seconds: number) => void;
  onTogglePlay?: () => void;
  onSelectGroup: (groupId: string | null) => void;
  onSelectVideo?: () => void;
  onUpdateGroup?: (groupId: string, patch: Partial<CaptionGroup>, commitToHistory?: boolean) => void;
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
  stableAnchors: number[];
}

export const CaptionTimeline: React.FC<CaptionTimelineProps> = ({
  track,
  currentTime,
  duration = 40,
  selectedGroupId,
  isVideoSelected = false,
  isPlaying,
  filmstripFrames = [],
  mediaElementRef,
  clipStart = 0,
  clipEnd,
  paddedStart,
  paddedEnd,
  onTrimChange,
  onSeek,
  onSelectGroup,
  onSelectVideo,
  onUpdateGroup,
}) => {
  const viewportRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const rulerContentRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState<number>(340);
  const [pxPerSec, setPxPerSec] = useState<number>(55);
  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);
  const [isSnapEnabled, setIsSnapEnabled] = useState<boolean>(false);
  const [snapGuideTime, setSnapGuideTime] = useState<number | null>(null);
  const [dragSession, setDragSession] = useState<DragSession | null>(null);
  const [hiddenTracks, setHiddenTracks] = useState<Record<number, boolean>>({});
  const canvasDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const leftRailRef = useRef<HTMLDivElement>(null);
  const latestBlockPatchRef = useRef<{ groupId: string; patch: Partial<CaptionGroup> } | null>(null);
  const isProgrammaticScrollRef = useRef<boolean>(false);
  const pendingSeekTimeRef = useRef<number | null>(null);
  const rafSeekIdRef = useRef<number | null>(null);

  // CapCut-style dual drag trimmer state
  const [activeTrim, setActiveTrim] = useState<{ start: number; end: number }>({
    start: clipStart || 0,
    end: clipEnd !== undefined ? clipEnd : duration,
  });

  const [trimDrag, setTrimDrag] = useState<{
    side: 'start' | 'end';
    startX: number;
    initialStart: number;
    initialEnd: number;
  } | null>(null);

  useEffect(() => {
    setActiveTrim({
      start: clipStart !== undefined ? clipStart : 0,
      end: clipEnd !== undefined ? clipEnd : duration,
    });
  }, [clipStart, clipEnd, duration]);

  const currentTimeRef = useRef(currentTime);
  currentTimeRef.current = currentTime;
  const pxPerSecRef = useRef(pxPerSec);
  pxPerSecRef.current = pxPerSec;

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

  // Coalesced seeking to prevent video playback thrashing and seek queuing
  const scheduleSeek = (timeSec: number) => {
    pendingSeekTimeRef.current = timeSec;
    if (!rafSeekIdRef.current) {
      rafSeekIdRef.current = requestAnimationFrame(() => {
        rafSeekIdRef.current = null;
        if (pendingSeekTimeRef.current !== null) {
          onSeek(pendingSeekTimeRef.current);
        }
      });
    }
  };

  const flushSeek = (timeSec?: number) => {
    if (rafSeekIdRef.current) {
      cancelAnimationFrame(rafSeekIdRef.current);
      rafSeekIdRef.current = null;
    }
    const finalTime = timeSec !== undefined ? timeSec : pendingSeekTimeRef.current;
    if (finalTime !== null && finalTime !== undefined) {
      onSeek(finalTime);
      pendingSeekTimeRef.current = null;
    }
  };

  const cancelSeek = () => {
    if (rafSeekIdRef.current) {
      cancelAnimationFrame(rafSeekIdRef.current);
      rafSeekIdRef.current = null;
    }
    pendingSeekTimeRef.current = null;
  };

  // Zoom adjustments: keep the current time anchored at fixed center playhead
  const handleZoom = (delta: number) => {
    const nextPx = clamp(pxPerSec + delta, 20, 140);
    if (nextPx === pxPerSec) return;
    setPxPerSec(nextPx);
    requestAnimationFrame(() => {
      if (scrollContainerRef.current) {
        isProgrammaticScrollRef.current = true;
        scrollContainerRef.current.scrollLeft = currentTime * nextPx;
        if (rulerContentRef.current) {
          rulerContentRef.current.style.transform = `translateX(${-currentTime * nextPx + halfWidth}px)`;
        }
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
        isProgrammaticScrollRef.current = true;
        scrollContainerRef.current.scrollLeft = currentTime * fitPx;
        if (rulerContentRef.current) {
          rulerContentRef.current.style.transform = `translateX(${-currentTime * fitPx + halfWidth}px)`;
        }
      }
    });
  };

  // Follow playhead smoothly during playback using decoupled media-frame scheduling
  // Drives timeline movement directly from the media clock (requestVideoFrameCallback where supported)
  // Does NOT restart/teardown on every frame update of currentTime
  useEffect(() => {
    if (!isPlaying || isScrubbing || dragSession) return;
    let animId: number;
    let rvfcId: number;
    let cancelled = false;

    const v = mediaElementRef?.current;

    const tick = () => {
      if (cancelled) return;
      const scrollEl = scrollContainerRef.current;
      if (scrollEl) {
        let cur = currentTimeRef.current;
        if (v && !v.paused) {
          const raw = v.currentTime;
          cur = Math.max(0, raw - (clipStart || 0));
        }
        const targetScroll = cur * pxPerSecRef.current;
        if (Math.abs(scrollEl.scrollLeft - targetScroll) > 0.5) {
          isProgrammaticScrollRef.current = true;
          scrollEl.scrollLeft = targetScroll;
          if (rulerContentRef.current) {
            rulerContentRef.current.style.transform = `translateX(${-targetScroll + halfWidth}px)`;
          }
        }
      }

      if (v && 'requestVideoFrameCallback' in v) {
        rvfcId = (v as any).requestVideoFrameCallback(tick);
      } else {
        animId = requestAnimationFrame(tick);
      }
    };

    if (v && 'requestVideoFrameCallback' in v) {
      rvfcId = (v as any).requestVideoFrameCallback(tick);
    } else {
      animId = requestAnimationFrame(tick);
    }

    return () => {
      cancelled = true;
      if (v && 'cancelVideoFrameCallback' in v && rvfcId) {
        (v as any).cancelVideoFrameCallback(rvfcId);
      }
      if (animId) cancelAnimationFrame(animId);
    };
  }, [isPlaying, isScrubbing, !!dragSession, mediaElementRef, clipStart, halfWidth]);

  // When paused and external seek happens (e.g. transport scrub), center timeline
  useEffect(() => {
    if (isPlaying || isScrubbing || dragSession) return;
    const scrollEl = scrollContainerRef.current;
    if (!scrollEl) return;

    const targetScroll = currentTime * pxPerSec;
    if (Math.abs(scrollEl.scrollLeft - targetScroll) > 1) {
      isProgrammaticScrollRef.current = true;
      scrollEl.scrollLeft = targetScroll;
      if (rulerContentRef.current) {
        rulerContentRef.current.style.transform = `translateX(${-targetScroll + halfWidth}px)`;
      }
    }
  }, [currentTime, pxPerSec, isPlaying, isScrubbing, !!dragSession, halfWidth]);

  // Synchronize ruler on mount or viewport resize
  useEffect(() => {
    if (rulerContentRef.current && scrollContainerRef.current) {
      rulerContentRef.current.style.transform = `translateX(${-scrollContainerRef.current.scrollLeft + halfWidth}px)`;
    }
  }, [halfWidth, pxPerSec]);

  // Scroll listener: updates ruler and optionally seeks during manual touch/trackpad scrub
  // Programmatic scroll (playhead follower) never seeks or triggers recursive loops
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const sl = e.currentTarget.scrollLeft;
    if (rulerContentRef.current) {
      rulerContentRef.current.style.transform = `translateX(${-sl + halfWidth}px)`;
    }
    if (leftRailRef.current) {
      leftRailRef.current.scrollTop = e.currentTarget.scrollTop;
    }

    if (isProgrammaticScrollRef.current) {
      isProgrammaticScrollRef.current = false;
      return;
    }

    if (isScrubbing) {
      const scrubTime = clamp(sl / pxPerSec, 0, safeDuration);
      scheduleSeek(Number(scrubTime.toFixed(2)));
    }
  };

  // Direct swipe/scrub on canvas or ruler
  const handleCanvasPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragSession) return;
    if ((e.target as HTMLElement).closest('[data-layer-block="true"], [data-video-track="true"], button, input, [data-handle]')) return;
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
    isProgrammaticScrollRef.current = true;
    scrollContainerRef.current.scrollLeft = targetScroll;
    if (rulerContentRef.current) {
      rulerContentRef.current.style.transform = `translateX(${-targetScroll + halfWidth}px)`;
    }
    const scrubTime = clamp(targetScroll / pxPerSec, 0, safeDuration);
    scheduleSeek(Number(scrubTime.toFixed(2)));
  };

  const handleCanvasPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (canvasDragRef.current) {
      const moved = Math.abs(e.clientX - canvasDragRef.current.startX);
      canvasDragRef.current = null;
      setIsScrubbing(false);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch { }

      flushSeek();

      // Genuinely empty timeline area clicked/tapped (motion < 5px): deselect active layer
      if (moved < 5) {
        const isInteractive = (e.target as HTMLElement).closest('[data-layer-block="true"], [data-video-track="true"], button, input, [data-handle]');
        if (!isInteractive) {
          onSelectGroup(null);
        }
      }
    }
  };

  const handleCanvasPointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    canvasDragRef.current = null;
    setIsScrubbing(false);
    cancelSeek();
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    } catch { }
  };

  // Draggable / Trimmable Layer Block Pointer Handlers
  const handleBlockPointerDown = (
    e: React.PointerEvent,
    grp: CaptionGroup,
    type: 'move' | 'trim-start' | 'trim-end'
  ) => {
    e.stopPropagation();
    onSelectGroup(grp.id);

    // Capture stable anchors for the entire gesture (never move with playhead)
    const otherEdges: number[] = [];
    if (track && track.groups) {
      for (const g of track.groups) {
        if (g.id !== grp.id) {
          otherEdges.push(g.start);
          otherEdges.push(g.end);
        }
      }
    }
    const stableAnchors = [0, safeDuration, currentTimeRef.current, ...otherEdges];

    setDragSession({
      type,
      groupId: grp.id,
      startX: e.clientX,
      origStart: grp.start,
      origEnd: grp.end,
      origWords: grp.words ? [...grp.words] : [],
      stableAnchors,
    });

    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handleBlockPointerMove = (e: React.PointerEvent) => {
    if (!dragSession || !onUpdateGroup) return;
    e.stopPropagation();

    const deltaSec = (e.clientX - dragSession.startX) / pxPerSec;
    const origDuration = Math.max(0.1, dragSession.origEnd - dragSession.origStart);
    let activeSnap: number | null = null;
    const snapThreshold = Math.min(0.12, 10 / pxPerSec);

    if (dragSession.type === 'move') {
      let proposedStart = dragSession.origStart + deltaSec;
      let proposedEnd = proposedStart + origDuration;

      // Bound within media duration
      if (proposedStart < 0) {
        proposedStart = 0;
        proposedEnd = origDuration;
      } else if (proposedEnd > safeDuration) {
        proposedEnd = safeDuration;
        proposedStart = Math.max(0, safeDuration - origDuration);
      }

      let finalStart = proposedStart;
      let finalEnd = proposedEnd;

      // Snapping is only active when explicitly enabled via Magnet toggle
      if (isSnapEnabled) {
        for (const anchor of dragSession.stableAnchors) {
          if (Math.abs(proposedStart - anchor) <= snapThreshold) {
            finalStart = anchor;
            finalEnd = finalStart + origDuration;
            activeSnap = anchor;
            break;
          }
        }
        if (activeSnap === null) {
          for (const anchor of dragSession.stableAnchors) {
            if (Math.abs(proposedEnd - anchor) <= snapThreshold) {
              finalEnd = anchor;
              finalStart = Math.max(0, finalEnd - origDuration);
              activeSnap = anchor;
              break;
            }
          }
        }
      }

      setSnapGuideTime(activeSnap);

      const timeShift = finalStart - dragSession.origStart;
      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((w.start + timeShift).toFixed(3)),
        end: Number((w.end + timeShift).toFixed(3)),
      }));

      const patch: Partial<CaptionGroup> = {
        start: Number(finalStart.toFixed(3)),
        end: Number(finalEnd.toFixed(3)),
        words: nextWords,
      };
      latestBlockPatchRef.current = { groupId: dragSession.groupId, patch };
      onUpdateGroup(dragSession.groupId, patch, false);
      // NOTE: Never call onSeek here. Moving a block must not continuously seek playback or move the snap reference.
    } else if (dragSession.type === 'trim-start') {
      let proposedStart = dragSession.origStart + deltaSec;
      proposedStart = clamp(proposedStart, 0, dragSession.origEnd - 0.2);

      let finalStart = proposedStart;
      if (isSnapEnabled) {
        for (const anchor of dragSession.stableAnchors) {
          if (anchor < dragSession.origEnd - 0.2 && Math.abs(proposedStart - anchor) <= snapThreshold) {
            finalStart = anchor;
            activeSnap = anchor;
            break;
          }
        }
      }
      setSnapGuideTime(activeSnap);

      const newDuration = dragSession.origEnd - finalStart;
      const ratio = newDuration / origDuration;

      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((finalStart + (w.start - dragSession.origStart) * ratio).toFixed(3)),
        end: Number((finalStart + (w.end - dragSession.origStart) * ratio).toFixed(3)),
      }));

      const patch: Partial<CaptionGroup> = {
        start: Number(finalStart.toFixed(3)),
        words: nextWords,
      };
      latestBlockPatchRef.current = { groupId: dragSession.groupId, patch };
      onUpdateGroup(dragSession.groupId, patch, false);
    } else if (dragSession.type === 'trim-end') {
      let proposedEnd = dragSession.origEnd + deltaSec;
      proposedEnd = clamp(proposedEnd, dragSession.origStart + 0.2, safeDuration);

      let finalEnd = proposedEnd;
      if (isSnapEnabled) {
        for (const anchor of dragSession.stableAnchors) {
          if (anchor > dragSession.origStart + 0.2 && Math.abs(proposedEnd - anchor) <= snapThreshold) {
            finalEnd = anchor;
            activeSnap = anchor;
            break;
          }
        }
      }
      setSnapGuideTime(activeSnap);

      const newDuration = finalEnd - dragSession.origStart;
      const ratio = newDuration / origDuration;

      const nextWords: Word[] = dragSession.origWords.map((w) => ({
        ...w,
        start: Number((dragSession.origStart + (w.start - dragSession.origStart) * ratio).toFixed(3)),
        end: Number((dragSession.origStart + (w.end - dragSession.origStart) * ratio).toFixed(3)),
      }));

      const patch: Partial<CaptionGroup> = {
        end: Number(finalEnd.toFixed(3)),
        words: nextWords,
      };
      latestBlockPatchRef.current = { groupId: dragSession.groupId, patch };
      onUpdateGroup(dragSession.groupId, patch, false);
    }
  };

  const handleBlockPointerUp = (e: React.PointerEvent) => {
    setSnapGuideTime(null);
    if (latestBlockPatchRef.current && onUpdateGroup) {
      onUpdateGroup(latestBlockPatchRef.current.groupId, latestBlockPatchRef.current.patch, true);
      latestBlockPatchRef.current = null;
    }
    if (dragSession) {
      setDragSession(null);
      try {
        (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch { }
    }
  };

  const handleBlockPointerCancel = (e: React.PointerEvent) => {
    setSnapGuideTime(null);
    if (dragSession && onUpdateGroup) {
      // Safely revert transient changes on cancellation
      onUpdateGroup(dragSession.groupId, {
        start: dragSession.origStart,
        end: dragSession.origEnd,
        words: dragSession.origWords,
      }, false);
    }
    latestBlockPatchRef.current = null;
    if (dragSession) {
      setDragSession(null);
      try {
        (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
      } catch { }
    }
  };

  const handleTrimPointerDown = (side: 'start' | 'end', e: React.PointerEvent) => {
    e.stopPropagation();
    setTrimDrag({
      side,
      startX: e.clientX,
      initialStart: activeTrim.start,
      initialEnd: activeTrim.end,
    });
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handleTrimPointerMove = (e: React.PointerEvent) => {
    if (!trimDrag) return;
    e.stopPropagation();
    const deltaSec = (e.clientX - trimDrag.startX) / pxPerSec;
    const minPadded = paddedStart !== undefined ? paddedStart : 0;
    const maxPadded = paddedEnd !== undefined ? paddedEnd : safeDuration;

    if (trimDrag.side === 'start') {
      const minBound = minPadded;
      const maxBound = Math.max(minPadded, trimDrag.initialEnd - 1.5);
      const nextStart = clamp(trimDrag.initialStart + deltaSec, minBound, maxBound);
      const rounded = Number(nextStart.toFixed(2));
      setActiveTrim((prev) => ({ ...prev, start: rounded }));
      scheduleSeek(rounded);
    } else {
      const minBound = Math.min(maxPadded, trimDrag.initialStart + 1.5);
      const maxBound = maxPadded;
      const nextEnd = clamp(trimDrag.initialEnd + deltaSec, minBound, maxBound);
      const rounded = Number(nextEnd.toFixed(2));
      setActiveTrim((prev) => ({ ...prev, end: rounded }));
      scheduleSeek(rounded);
    }
  };

  const handleTrimPointerUp = (e: React.PointerEvent) => {
    if (trimDrag) {
      flushSeek(trimDrag.side === 'start' ? activeTrim.start : activeTrim.end);
      if (onTrimChange) {
        onTrimChange(activeTrim.start, activeTrim.end);
      }
      setTrimDrag(null);
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
    if (grp.type === 'text') {
      return {
        icon: <Type size={11} className="mr-1 shrink-0 text-cyan-200" />,
        bg: 'bg-[#0284C7]/90',
        border: 'border-[#38BDF8]',
        text: 'text-sky-100',
        badge: 'Text',
        name: (grp.words || []).map((w) => w.text || '').join(' ') || 'Text',
      };
    }
    // Default caption (type === 'caption' or undefined)
    return {
      icon: <Sparkles size={11} className="mr-1 shrink-0 text-amber-200" />,
      bg: 'bg-[#2563EB]/90',
      border: 'border-[#3B82F6]',
      text: 'text-blue-100',
      badge: 'Caption',
      name: (grp.words || []).map((w) => w.text || '').join(' ') || 'Caption',
    };
  };

  return (
    <section
      className="h-[370px] max-h-[370px] bg-[#13161B] overflow-hidden select-none flex flex-col shrink-0 min-h-0"
    >
      {/* TIMELINE UTILITY BAR: Magnet Snapping + Clean compact zoom controls */}
      <div className="h-6 px-2 bg-[#16191E] border-b border-[#232832] flex items-center justify-between text-text-secondary text-[10px] shrink-0">
        {/* Left side: Snap / Magnet toggle */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setIsSnapEnabled(!isSnapEnabled)}
            className={`px-1.5 h-4.5 rounded flex items-center gap-1 text-[10px] font-semibold transition-all active:scale-95 ${
              isSnapEnabled
                ? 'bg-primary/20 text-primary border border-primary/40 shadow-hero-sm font-bold'
                : 'text-text-muted hover:text-text-secondary hover:bg-[#232832] border border-transparent'
            }`}
            title={isSnapEnabled ? 'Magnetic Snapping: ON (Click to disable)' : 'Magnetic Snapping: OFF (Click to enable)'}
            aria-label="Toggle Magnet Snapping"
          >
            <Magnet size={11} className={isSnapEnabled ? 'text-primary' : 'text-text-muted'} />
            <span>Snap {isSnapEnabled ? 'ON' : 'OFF'}</span>
          </button>
        </div>

        {/* Right side: Zoom controls */}
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
          {/* PINNED TIME RULER: Synchronized horizontally with scrollContainer */}
          <div
            className="h-6 shrink-0 border-b border-[#232832] bg-[#13161B] overflow-hidden relative cursor-ew-resize select-none"
            onPointerDown={handleCanvasPointerDown}
            onPointerMove={handleCanvasPointerMove}
            onPointerUp={handleCanvasPointerUp}
            onPointerCancel={handleCanvasPointerUp}
            onClick={(e) => {
              const isInteractive = (e.target as HTMLElement).closest('[data-layer-block="true"], [data-video-track="true"], button, input, [data-handle]');
              if (!isInteractive) {
                onSelectGroup(null);
              }
            }}
          >
            <div
              ref={rulerContentRef}
              className="relative h-full flex items-center pointer-events-none"
              style={{
                transform: `translateX(${-currentTime * pxPerSec + halfWidth}px)`,
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
            onClick={(e) => {
              const isInteractive = (e.target as HTMLElement).closest('[data-layer-block="true"], [data-video-track="true"], button, input, [data-handle]');
              if (!isInteractive) {
                onSelectGroup(null);
              }
            }}
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

              {/* 1. MAIN VIDEO TRACK WITH CAPCUT-STYLE SAFETY HANDLES & DUAL DRAG TRIMMING */}
              {(() => {
                const trimLeftPx = Math.max(0, activeTrim.start * pxPerSec);
                const trimRightPx = Math.max(trimLeftPx + 20, activeTrim.end * pxPerSec);
                const trimWidthPx = Math.max(20, trimRightPx - trimLeftPx);

                return (
                  <div
                    data-video-track="true"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectVideo?.();
                    }}
                    className={`h-11 border-b border-[#232832] relative p-0.5 overflow-visible cursor-pointer transition-colors ${isVideoSelected ? 'bg-primary/10 ring-1 ring-primary' : ''
                      }`}
                    style={{ width: `${totalTimelineWidth}px` }}
                  >
                    <div className="w-full h-full rounded bg-[#090C0F] border border-[#232832]/60 flex items-center overflow-hidden relative">
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

                      {/* Head Margin Dimmed Overlay (Safety Handle) */}
                      {trimLeftPx > 0 && (
                        <div
                          className="absolute left-0 top-0 bottom-0 bg-black/75 backdrop-blur-[1px] border-r border-dashed border-amber-400/60 pointer-events-none z-10"
                          style={{ width: `${trimLeftPx}px` }}
                        />
                      )}

                      {/* Tail Margin Dimmed Overlay (Safety Handle) */}
                      {trimRightPx < totalTimelineWidth && (
                        <div
                          className="absolute top-0 bottom-0 right-0 bg-black/75 backdrop-blur-[1px] border-l border-dashed border-amber-400/60 pointer-events-none z-10"
                          style={{ left: `${trimRightPx}px` }}
                        />
                      )}

                      {/* Active Video Bounding Box with Dual Drag Handles (CapCut Style) */}
                      <div
                        className="absolute top-0 bottom-0 rounded border-2 border-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.35)] pointer-events-none z-20"
                        style={{ left: `${trimLeftPx}px`, width: `${trimWidthPx}px` }}
                      >
                        {/* Left Trim Handle (Head Padding Expansion) */}
                        <div
                          onPointerDown={(e) => handleTrimPointerDown('start', e)}
                          onPointerMove={handleTrimPointerMove}
                          onPointerUp={handleTrimPointerUp}
                          onPointerCancel={handleTrimPointerUp}
                          className="absolute left-0 top-0 bottom-0 w-3.5 -translate-x-1/2 bg-amber-400 hover:bg-amber-300 rounded-l cursor-ew-resize pointer-events-auto flex items-center justify-center shadow-md active:scale-105 transition-transform"
                          title="Drag to trim start (reveals earlier speech in safety handle)"
                        >
                          <div className="w-[1px] h-3 bg-black/80 rounded-full mx-[0.5px]" />
                          <div className="w-[1px] h-3 bg-black/80 rounded-full mx-[0.5px]" />
                        </div>

                        {/* Right Trim Handle (Tail Padding Expansion) */}
                        <div
                          onPointerDown={(e) => handleTrimPointerDown('end', e)}
                          onPointerMove={handleTrimPointerMove}
                          onPointerUp={handleTrimPointerUp}
                          onPointerCancel={handleTrimPointerUp}
                          className="absolute right-0 top-0 bottom-0 w-3.5 translate-x-1/2 bg-amber-400 hover:bg-amber-300 rounded-r cursor-ew-resize pointer-events-auto flex items-center justify-center shadow-md active:scale-105 transition-transform"
                          title="Drag to trim end (extends ending speech into safety handle)"
                        >
                          <div className="w-[1px] h-3 bg-black/80 rounded-full mx-[0.5px]" />
                          <div className="w-[1px] h-3 bg-black/80 rounded-full mx-[0.5px]" />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })()}

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
                      const rawDur = Math.max(0.04, grp.end - grp.start);
                      const trueWidth = Math.max(4, rawDur * pxPerSec);
                      const isSelected = selectedGroupId === grp.id;
                      const isCaption = !grp.type || grp.type === 'caption';
                      const isSpoken = isCaption && currentTime >= grp.start && currentTime <= grp.end;
                      const meta = getLayerMeta(grp, trackIdx);

                      const isThisBlockDragging = dragSession?.groupId === grp.id;
                      const showHandles = isSelected && trueWidth >= 32;
                      const showDuration = isSelected && trueWidth >= 96;
                      const showLabel = trueWidth >= 40;
                      const showIcon = trueWidth >= 20;

                      return (
                        <div
                          key={grp.id}
                          data-layer-block="true"
                          className={`absolute top-1 bottom-1 rounded-md flex items-center overflow-hidden select-none ${
                            isThisBlockDragging
                              ? 'transition-none z-30 shadow-2xl scale-[1.01]'
                              : isSelected
                              ? 'transition-colors duration-150 bg-[#2563EB] border-2 border-white text-white font-bold shadow-[0_0_12px_rgba(37,99,235,0.6)] z-20'
                              : isSpoken
                              ? `transition-colors duration-150 ${meta.bg} border-2 border-white/80 text-white font-semibold z-10`
                              : `transition-colors duration-150 ${meta.bg} border ${meta.border} ${meta.text} hover:opacity-100 opacity-90`
                          }`}
                          style={{ left: `${left}px`, width: `${trueWidth}px` }}
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectGroup(grp.id);
                          }}
                        >
                          {/* Left Trim Handle (visible only when selected and width permits) */}
                          {showHandles && (
                            <div
                              data-handle="start"
                              onPointerDown={(e) => handleBlockPointerDown(e, grp, 'trim-start')}
                              onPointerMove={handleBlockPointerMove}
                              onPointerUp={handleBlockPointerUp}
                              onPointerCancel={handleBlockPointerCancel}
                              style={{ touchAction: 'none' }}
                              className="w-3 h-full flex items-center justify-center cursor-ew-resize bg-black/40 hover:bg-black/60 active:bg-primary transition-colors shrink-0"
                              title="Trim Start"
                            >
                              <div className="w-[1.5px] h-3 bg-white/90 rounded-full" />
                            </div>
                          )}

                          {/* Draggable Layer Body */}
                          <div
                            onPointerDown={(e) => handleBlockPointerDown(e, grp, 'move')}
                            onPointerMove={handleBlockPointerMove}
                            onPointerUp={handleBlockPointerUp}
                            onPointerCancel={handleBlockPointerCancel}
                            style={{ touchAction: 'none' }}
                            className={`flex-1 h-full flex items-center justify-between ${trueWidth >= 28 ? 'px-1' : 'px-0'} cursor-grab active:cursor-grabbing overflow-hidden min-w-0`}
                          >
                            <div className="flex items-center min-w-0 truncate">
                              {showIcon && (
                                grp.type === 'overlay' && grp.overlay_url && grp.overlay_type === 'image' ? (
                                  <img src={grp.overlay_url} alt="" className="w-3.5 h-3.5 rounded object-cover mr-0.5 shrink-0 border border-white/30" />
                                ) : (
                                  meta.icon
                                )
                              )}
                              {showLabel && (
                                <div className="flex items-center min-w-0 truncate">
                                  {(meta as any).badge && trueWidth >= 48 && (
                                    <span className="text-[8px] font-black uppercase tracking-wider px-1 py-0.2 rounded bg-black/40 text-white/90 mr-1 shrink-0 select-none">
                                      {(meta as any).badge}
                                    </span>
                                  )}
                                  <span className="text-[10px] font-sans truncate whitespace-nowrap leading-none min-w-0">
                                    {meta.name}
                                  </span>
                                </div>
                              )}
                            </div>
                            {showDuration && (
                              <span className="ml-1 font-mono tabular-nums text-[9px] text-white/90 bg-black/40 px-1 py-0.2 rounded shrink-0 font-bold">
                                {rawDur.toFixed(1)}s
                              </span>
                            )}
                          </div>

                          {/* Right Trim Handle (visible only when selected and width permits) */}
                          {showHandles && (
                            <div
                              data-handle="end"
                              onPointerDown={(e) => handleBlockPointerDown(e, grp, 'trim-end')}
                              onPointerMove={handleBlockPointerMove}
                              onPointerUp={handleBlockPointerUp}
                              onPointerCancel={handleBlockPointerCancel}
                              style={{ touchAction: 'none' }}
                              className="w-3 h-full flex items-center justify-center cursor-ew-resize bg-black/40 hover:bg-black/60 active:bg-primary transition-colors shrink-0"
                              title="Trim End"
                            >
                              <div className="w-[1.5px] h-3 bg-white/90 rounded-full" />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}

              {/* DYNAMIC SNAP ALIGNMENT GUIDE LINE */}
              {snapGuideTime !== null && (
                <div
                  className="absolute top-0 bottom-0 pointer-events-none z-30 flex flex-col items-center"
                  style={{ left: `${snapGuideTime * pxPerSec + halfWidth}px` }}
                >
                  <div className="w-[1.5px] h-full bg-primary shadow-[0_0_8px_rgba(37,99,235,0.9)] animate-pulse" />
                  <div className="absolute top-0 px-1 py-0.5 rounded bg-primary text-white text-[8px] font-mono font-bold shadow-md -translate-x-1/2">
                    {snapGuideTime.toFixed(2)}s
                  </div>
                </div>
              )}
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
