import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { 
  Move, 
  Maximize2, 
  RotateCw, 
  Edit3, 
  X, 
  Check,
  CheckCheck,
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig } from './types';
import { preserveWordTimingsOnEdit } from './timingPreservation';
import { partitionWordsIntoVisualLines } from './linePartition';

function applyColorOpacity(color: string, opacity: number = 1.0): string {
  if (opacity >= 1.0) return color;
  if (color.startsWith('#')) {
    const hex = color.slice(1);
    let r = 0, g = 0, b = 0;
    if (hex.length === 3) {
      r = parseInt(hex[0] + hex[0], 16);
      g = parseInt(hex[1] + hex[1], 16);
      b = parseInt(hex[2] + hex[2], 16);
    } else if (hex.length >= 6) {
      r = parseInt(hex.slice(0, 2), 16);
      g = parseInt(hex.slice(2, 4), 16);
      b = parseInt(hex.slice(4, 6), 16);
    }
    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
  }
  return color;
}

interface InteractiveTextCanvasProps {
  track: CaptionTrack | null;
  currentTime: number;
  selectedGroupId: string | null;
  onSelectGroup: (groupId: string | null) => void;
  onOpenTextEditor?: (groupId: string) => void;
  onUpdateGroup: (groupId: string, patch: Partial<CaptionGroup>, commitToHistory?: boolean) => void;
  onApplyLayoutToAll?: (groupId: string) => void;
  layoutMode?: string;
  isInteractive?: boolean;
}

interface CanvasGroupItemProps {
  group: CaptionGroup;
  defaultStyle: StyleConfig;
  defaultLayout: LayoutConfig;
  defaultAnimation: any;
  isSelected: boolean;
  currentTime: number;
  containerRef: React.RefObject<HTMLDivElement>;
  onSelectGroup: (groupId: string | null) => void;
  onOpenTextEditor?: (groupId: string) => void;
  onUpdateGroup: (groupId: string, patch: Partial<CaptionGroup>, commitToHistory?: boolean) => void;
  onApplyLayoutToAll?: (groupId: string) => void;
  layoutMode: string;
  isInteractive: boolean;
}

const CanvasGroupItem: React.FC<CanvasGroupItemProps> = React.memo(({
  group,
  defaultStyle,
  defaultLayout,
  defaultAnimation,
  isSelected,
  currentTime,
  containerRef,
  onSelectGroup,
  onOpenTextEditor,
  onUpdateGroup,
  onApplyLayoutToAll,
  layoutMode,
  isInteractive
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);

  const style: StyleConfig = group.style || defaultStyle;
  const layout: LayoutConfig = group.layout || defaultLayout;
  const anim = group.animation || defaultAnimation;

  // Layer type awareness
  const isCaption = (group.type || 'caption') === 'caption';
  const highlightEnabled = isCaption && (style.highlight_enabled !== false);

  let posX = layout.position_x ?? 0.5;
  let posY = layout.position_y ?? 0.78;
  const scale = layout.scale ?? 1.0;

  if (isCaption && layoutMode === 'landscape') {
    posY = Math.max(0.78, posY);
  }

  const dragStartRef = useRef<{ 
    x: number; 
    y: number; 
    initialX: number; 
    initialY: number; 
    containerWidth: number; 
    containerHeight: number; 
  }>({ x: 0, y: 0, initialX: 0.5, initialY: 0.78, containerWidth: 1, containerHeight: 1 });

  const resizeStartRef = useRef<{ 
    x: number; 
    initialScale: number; 
    containerWidth: number; 
  }>({ x: 0, initialScale: 1.0, containerWidth: 1 });

  const latestPatchRef = useRef<{ groupId: string; patch: Partial<CaptionGroup> } | null>(null);

  const fontFam = style.font_family || 'Montserrat';
  const fontSize = Math.max(10, Math.round((style.font_size || 44) * 0.44));
  const fontWeight = style.font_weight || 'bold';
  const letterSpacing = style.letter_spacing || 0;

  const visualLines = useMemo(() => {
    return partitionWordsIntoVisualLines(
      group.words || [],
      fontFam,
      fontSize,
      fontWeight,
      letterSpacing,
      360 * (layout.max_width || 0.88),
      style.text_transform
    );
  }, [group.words, fontFam, fontSize, fontWeight, letterSpacing, layout.max_width, style.text_transform]);

  const handlePointerDownDrag = (e: React.PointerEvent) => {
    if (!isInteractive) return;
    e.stopPropagation();
    onSelectGroup(group.id);

    const rect = containerRef.current?.getBoundingClientRect() || { width: 393, height: 700 };
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      initialX: posX,
      initialY: posY,
      containerWidth: Math.max(1, rect.width),
      containerHeight: Math.max(1, rect.height),
    };
    latestPatchRef.current = null;
    setIsDragging(true);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (isDragging) {
      const deltaX = (e.clientX - dragStartRef.current.x) / dragStartRef.current.containerWidth;
      const deltaY = (e.clientY - dragStartRef.current.y) / dragStartRef.current.containerHeight;

      const newX = Math.max(0.05, Math.min(0.95, dragStartRef.current.initialX + deltaX));
      const newY = Math.max(0.05, Math.min(0.95, dragStartRef.current.initialY + deltaY));

      const patch: Partial<CaptionGroup> = {
        layout: {
          ...layout,
          position_x: Number(newX.toFixed(3)),
          position_y: Number(newY.toFixed(3))
        }
      };

      latestPatchRef.current = { groupId: group.id, patch };
      onUpdateGroup(group.id, patch, false);
    } else if (isResizing) {
      const deltaX = (e.clientX - resizeStartRef.current.x) / (resizeStartRef.current.containerWidth * 0.4);
      const newScale = Math.max(0.6, Math.min(2.4, resizeStartRef.current.initialScale + deltaX));

      const patch: Partial<CaptionGroup> = {
        layout: {
          ...layout,
          scale: Number(newScale.toFixed(2))
        }
      };

      latestPatchRef.current = { groupId: group.id, patch };
      onUpdateGroup(group.id, patch, false);
    }
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    if (latestPatchRef.current) {
      onUpdateGroup(latestPatchRef.current.groupId, latestPatchRef.current.patch, true);
      latestPatchRef.current = null;
    }
    if (isDragging) setIsDragging(false);
    if (isResizing) setIsResizing(false);
    try {
      (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    } catch {}
  };

  const handleResizePointerDown = (e: React.PointerEvent) => {
    e.stopPropagation();
    const rect = containerRef.current?.getBoundingClientRect() || { width: 393, height: 700 };
    resizeStartRef.current = {
      x: e.clientX,
      initialScale: scale,
      containerWidth: Math.max(1, rect.width),
    };
    latestPatchRef.current = null;
    setIsResizing(true);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  // Deterministic 3-layer animation evaluation (Enter / Exit / Loop)
  const timeSinceStart = Math.max(0, currentTime - group.start);
  const timeUntilEnd = Math.max(0, group.end - currentTime);
  
  let enterOpacity = 1.0;
  let enterTransform = '';
  const enterType = anim.enter?.type || (anim as any)?.enter_type || 'none';
  const enterDur = (anim.enter?.duration_ms || (anim as any)?.enter_duration_ms || 200) / 1000;
  if (enterType !== 'none' && timeSinceStart < enterDur) {
    const p = Math.max(0, Math.min(1, timeSinceStart / enterDur));
    if (enterType === 'fade') enterOpacity = p;
    else if (enterType === 'pop') {
      enterOpacity = Math.min(1, p * 2);
      enterTransform = `scale(${0.5 + 0.5 * Math.sin(p * Math.PI / 2)})`;
    } else if (enterType === 'slide_up') {
      enterOpacity = p;
      enterTransform = `translateY(${(1 - p) * 20}px)`;
    }
  }

  let exitOpacity = 1.0;
  let exitTransform = '';
  const exitType = anim.exit?.type || (anim as any)?.exit_type || 'none';
  const exitDur = (anim.exit?.duration_ms || (anim as any)?.exit_duration_ms || 150) / 1000;
  if (exitType !== 'none' && timeUntilEnd < exitDur) {
    const p = Math.max(0, Math.min(1, timeUntilEnd / exitDur));
    if (exitType === 'fade') exitOpacity = p;
    else if (exitType === 'scale_down') {
      exitOpacity = p;
      exitTransform = `scale(${0.5 + 0.5 * p})`;
    } else if (exitType === 'slide_down') {
      exitOpacity = p;
      exitTransform = `translateY(${(1 - p) * 20}px)`;
    }
  }

  let loopTransform = '';
  const loopType = anim.loop?.type || (anim as any)?.loop_type || 'none';
  if (loopType === 'pulse') {
    const speed = (anim.loop?.speed_ms || (anim as any)?.loop_speed_ms || 1000) / 1000;
    const loopTime = timeSinceStart / speed;
    loopTransform = `scale(${1.0 + 0.04 * Math.sin(loopTime * 2 * Math.PI)})`;
  } else if (loopType === 'wiggle') {
    const speed = (anim.loop?.speed_ms || (anim as any)?.loop_speed_ms || 1000) / 1000;
    const loopTime = timeSinceStart / speed;
    loopTransform = `rotate(${2.0 * Math.sin(loopTime * 2 * Math.PI)}deg)`;
  }

  const computedOpacity = (style.opacity ?? 1.0) * enterOpacity * exitOpacity;
  const extraAnimTransform = [enterTransform, exitTransform, loopTransform].filter(Boolean).join(' ');

  const bgEnabled = style.background_enabled !== false && style.background_mode !== 'none';
  const rawBgMode = bgEnabled ? (style.background_mode || 'block') : 'none';
  const isBgBlock = rawBgMode === 'block' || (rawBgMode as string) === 'full_box';
  const isBgLine = rawBgMode === 'line' || (rawBgMode as string) === 'per_line';
  const effectiveBgColor = applyColorOpacity(style.background_color || '#000000', style.background_opacity ?? 1.0);
  const bgOffsetX = (isBgBlock || isBgLine) ? (style.background_offset_x || 0) : 0;
  const bgOffsetY = (isBgBlock || isBgLine) ? (style.background_offset_y || 0) : 0;
  const bgOffsetTransform = (bgOffsetX !== 0 || bgOffsetY !== 0) ? `translate(${bgOffsetX}px, ${bgOffsetY}px)` : '';

  const rotation = layout.rotation ?? 0;

  return (
    <div
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      style={{
        position: 'absolute',
        left: `${posX * 100}%`,
        top: `${posY * 100}%`,
        transform: `translate(-50%, -50%) rotate(${rotation}deg) scale(${scale}) ${extraAnimTransform}`.trim(),
        width: 'max-content',
        maxWidth: containerRef.current?.clientWidth
          ? `${Math.round(containerRef.current.clientWidth * (layout.max_width ?? 0.88))}px`
          : `${(layout.max_width ?? 0.88) * 100}%`,
        boxSizing: 'border-box',
        opacity: computedOpacity,
        cursor: isInteractive ? (isDragging ? 'grabbing' : 'grab') : 'default',
        touchAction: 'none'
      }}
      onPointerDown={handlePointerDownDrag}
      onClick={(e) => {
        if (!isInteractive) return;
        e.stopPropagation();
        onSelectGroup(group.id);
      }}
      onDoubleClick={(e) => {
        if (!isInteractive) return;
        e.stopPropagation();
        onOpenTextEditor?.(group.id);
      }}
      className={`relative transition-shadow group ${
        isSelected && isInteractive ? 'ring-2 ring-primary/80 shadow-[0_0_15px_rgba(37,99,235,0.35)]' : ''
      }`}
    >
      {/* CAPCUT SELECTION OVERLAY & TRANSFORM HANDLES */}
      {isSelected && isInteractive && (
        <div className="absolute -inset-2.5 border border-dashed border-primary rounded-lg pointer-events-none z-30">
          {/* Top-Left: Close / Deselect handle */}
          <div 
            onClick={(e) => { e.stopPropagation(); onSelectGroup(null); }}
            className="absolute -top-5 -left-5 w-11 h-11 flex items-center justify-center pointer-events-auto cursor-pointer"
            title="Deselect"
          >
            <div className="w-5 h-5 rounded-full bg-surface-200 border border-border flex items-center justify-center hover:bg-red-500 transition-colors shadow-md">
              <X size={10} className="text-white" />
            </div>
          </div>

          {/* Bottom-Right: Scale / Resize handle */}
          <div 
            onPointerDown={handleResizePointerDown}
            className="absolute -bottom-5 -right-5 w-11 h-11 flex items-center justify-center pointer-events-auto cursor-nwse-resize"
            title="Drag to resize text scale"
          >
            <div className="w-5 h-5 rounded-full bg-primary border border-white flex items-center justify-center hover:scale-110 transition-transform shadow-md">
              <Maximize2 size={10} className="text-white" />
            </div>
          </div>

          {/* Bottom-Center: Move Anchor handle */}
          <div 
            className="absolute -bottom-2.5 left-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-surface-200 border border-primary flex items-center justify-center pointer-events-none"
          >
            <Move size={8} className="text-primary" />
          </div>

          {/* Quick Apply to All Captions Action Pill (Captions only) */}
          {onApplyLayoutToAll && isCaption && (
            <div className="absolute -bottom-10 left-1/2 -translate-x-1/2 pointer-events-auto z-40">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onApplyLayoutToAll(group.id);
                }}
                className="h-6 px-2.5 rounded-full bg-[#1C1F26]/95 border border-[#2E3239] hover:border-primary text-text-secondary hover:text-white text-[10px] font-semibold flex items-center gap-1.5 shadow-lg active:scale-95 transition-all whitespace-nowrap"
                title="Apply this caption's position & size to all spoken captions"
              >
                <CheckCheck size={11} className="text-primary" />
                <span>Apply to All Captions</span>
              </button>
            </div>
          )}
        </div>
      )}

      {/* RENDERED TEXT AND WORD-LEVEL POP ANIMATION */}
      <div
        className={`w-full flex flex-col ${
          layout.alignment === 'left' ? 'items-start text-left' : layout.alignment === 'right' ? 'items-end text-right' : 'items-center text-center'
        } p-1.5 rounded-lg transition-all`}
        style={{
          transform: bgOffsetTransform || undefined,
          ...(isBgBlock && {
            backgroundColor: effectiveBgColor,
            padding: `${style.background_padding_y || 8}px ${style.background_padding_x || 14}px`,
            borderRadius: `${style.background_corner_radius || 12}px`,
          })
        }}
      >
        {visualLines.map((lineWords, lIdx) => {
          const lineContent = lineWords.map((w, idx) => {
            const hasWordTiming = isCaption && (group as any).has_word_timing !== false && (group as any).timing_mode !== 'phrase' && w.is_exact !== false;
            const isSpoken = hasWordTiming && currentTime >= w.start && currentTime <= w.end;
            const wordOverride = w.style_override;

            // Tactile scale pop calculation (Spring Punch) ONLY when layer is caption
            let wordScale = 1.0;
            const activeAnimType = anim.active_word?.type || anim.type;
            const punchScale = anim.active_word?.active_scale || anim.active_scale || 1.16;
            const animDurationMs = anim.active_word?.duration_ms || anim.duration_ms || 100;

            if (isCaption && isSpoken && highlightEnabled && activeAnimType && activeAnimType !== 'none' && (activeAnimType === 'pop' || activeAnimType === 'bounce')) {
              const durationSec = animDurationMs / 1000;
              const elapsed = Math.max(0, currentTime - w.start);
              if (elapsed < durationSec) {
                const progress = elapsed / durationSec;
                const punch = punchScale - 1.0;
                wordScale = 1.0 + punch * Math.cos(progress * (Math.PI / 2));
              }
            }

            // Color resolution: Normal text strictly preserves primary_color; Spoken highlight only applies to captions
            let textColor = style.primary_color;
            if (isCaption && isSpoken && highlightEnabled) {
              textColor = wordOverride?.highlight_color || style.highlight_color;
            } else if (wordOverride?.color) {
              textColor = wordOverride.color;
            }

            const strokeWidth = style.outline_enabled ? `${Math.max(1.5, (style.outline_width || 4.5) * 0.6)}px` : '0px';

            const filters: string[] = [];
            if (style.shadow_enabled) {
              filters.push(`drop-shadow(${style.shadow_offset_x || 0}px ${style.shadow_offset_y || 2}px ${style.shadow_blur || 4}px ${style.shadow_color || 'rgba(0,0,0,0.85)'})`);
            }
            if (style.glow_enabled) {
              filters.push(`drop-shadow(0px 0px ${style.glow_blur || 14}px ${style.glow_color || '#00F0FF'})`);
            }

            return (
              <React.Fragment key={w.id || idx}>
                <span
                  style={{
                    fontFamily: fontFam,
                    fontSize: `${fontSize}px`,
                    fontWeight: w.style_override?.font_weight || style.font_weight || 'bold',
                    textTransform: (style.text_transform as any) || 'uppercase',
                    letterSpacing: `${style.letter_spacing || 0}px`,
                    color: textColor,
                    WebkitTextStroke: style.outline_enabled ? `${strokeWidth} ${style.outline_color}` : undefined,
                    paintOrder: 'stroke fill',
                    filter: filters.length > 0 ? filters.join(' ') : undefined,
                    transform: `scale(${wordScale})`,
                    display: 'inline-block',
                    transformOrigin: 'center bottom',
                    transition: 'transform 0.04s cubic-bezier(0.16, 1, 0.3, 1), color 0.05s ease'
                  }}
                >
                  {w.text}
                </span>
                {idx < lineWords.length - 1 ? ' ' : null}
              </React.Fragment>
            );
          });

          if (isBgLine) {
            return (
              <div key={lIdx} className="my-0.5 inline-block text-center w-auto">
                <span
                  style={{
                    display: 'inline-block',
                    backgroundColor: effectiveBgColor,
                    padding: `${Math.round((style.background_padding_y || 6) * 0.45)}px ${Math.round((style.background_padding_x || 12) * 0.45)}px`,
                    borderRadius: `${Math.round((style.background_corner_radius || 8) * 0.6)}px`,
                  }}
                >
                  {lineContent}
                </span>
              </div>
            );
          }

          return (
            <div key={lIdx} className="my-0.5 inline-block text-center w-auto">
              {lineContent}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export const InteractiveTextCanvas: React.FC<InteractiveTextCanvasProps> = ({
  track,
  currentTime,
  selectedGroupId,
  onSelectGroup,
  onOpenTextEditor,
  onUpdateGroup,
  onApplyLayoutToAll,
  layoutMode = 'fit_black',
  isInteractive = true
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  if (!track || !track.groups || track.groups.length === 0) {
    return null;
  }

  // Find all active visible groups for current playback time (timeline-aware visibility)
  const activeGroups = track.groups.filter(
    grp => grp.type !== 'audio' && grp.type !== 'voiceover' && !grp.is_hidden && currentTime >= grp.start && currentTime <= grp.end
  );

  if (activeGroups.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 z-20 overflow-hidden select-none"
      style={{
        paddingLeft: '4%',
        paddingRight: '4%',
      }}
    >
      {activeGroups.map((grp) => (
        <CanvasGroupItem
          key={grp.id}
          group={grp}
          defaultStyle={track.default_style}
          defaultLayout={track.default_layout}
          defaultAnimation={track.default_animation}
          isSelected={selectedGroupId === grp.id}
          currentTime={currentTime}
          containerRef={containerRef}
          onSelectGroup={onSelectGroup}
          onOpenTextEditor={onOpenTextEditor}
          onUpdateGroup={onUpdateGroup}
          onApplyLayoutToAll={onApplyLayoutToAll}
          layoutMode={layoutMode}
          isInteractive={isInteractive}
        />
      ))}
    </div>
  );
};
