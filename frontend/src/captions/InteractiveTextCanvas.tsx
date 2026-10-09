import React, { useState, useRef, useEffect, useCallback } from 'react';
import { 
  Move, 
  Maximize2, 
  RotateCw, 
  Edit3, 
  X, 
  Check 
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig } from './types';

interface InteractiveTextCanvasProps {
  track: CaptionTrack | null;
  currentTime: number;
  selectedGroupId: string | null;
  onSelectGroup: (groupId: string | null) => void;
  onUpdateGroup: (groupId: string, patch: Partial<CaptionGroup>) => void;
  layoutMode?: string;
  isInteractive?: boolean;
}

export const InteractiveTextCanvas: React.FC<InteractiveTextCanvasProps> = ({
  track,
  currentTime,
  selectedGroupId,
  onSelectGroup,
  onUpdateGroup,
  layoutMode = 'fit_black',
  isInteractive = true
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  // Dragging and resizing states
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [isEditingInline, setIsEditingInline] = useState(false);
  const [inlineEditText, setInlineEditText] = useState('');

  const dragStartRef = useRef<{ x: number; y: number; initialX: number; initialY: number }>({ x: 0, y: 0, initialX: 0.5, initialY: 0.78 });
  const resizeStartRef = useRef<{ x: number; initialScale: number }>({ x: 0, initialScale: 1.0 });

  if (!track || !track.groups || track.groups.length === 0) {
    return null;
  }

  // Find currently active group by currentTime, or fallback to selected group
  const activeGroupByTime = track.groups.find(
    grp => currentTime >= grp.start && currentTime <= grp.end
  );

  const currentGroup = activeGroupByTime || 
    (selectedGroupId ? track.groups.find(g => g.id === selectedGroupId) : null) || 
    track.groups[0];

  if (!currentGroup) return null;

  const isSelected = selectedGroupId === currentGroup.id || !selectedGroupId;
  const style: StyleConfig = currentGroup.style || track.default_style;
  const layout: LayoutConfig = currentGroup.layout || track.default_layout;
  const anim = currentGroup.animation || track.default_animation;

  // Normalized position coordinates [0.0 - 1.0]
  let posX = layout.position_x ?? 0.5;
  let posY = layout.position_y ?? 0.78;
  const scale = layout.scale ?? 1.0;

  // Adapt for landscape mode if applicable
  if (layoutMode === 'landscape') {
    posY = Math.max(0.78, posY);
  }

  // Alignment classes
  const justifyClass = 
    layout.alignment === 'left' ? 'justify-start text-left' :
    layout.alignment === 'right' ? 'justify-end text-right' :
    'justify-center text-center';

  // Drag interaction handlers
  const handlePointerDownDrag = (e: React.PointerEvent) => {
    if (!isInteractive || isEditingInline) return;
    e.stopPropagation();
    onSelectGroup(currentGroup.id);

    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      initialX: posX,
      initialY: posY
    };
    setIsDragging(true);
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();

    if (isDragging) {
      const deltaX = (e.clientX - dragStartRef.current.x) / rect.width;
      const deltaY = (e.clientY - dragStartRef.current.y) / rect.height;

      const newX = Math.max(0.12, Math.min(0.88, dragStartRef.current.initialX + deltaX));
      const newY = Math.max(0.15, Math.min(0.90, dragStartRef.current.initialY + deltaY));

      onUpdateGroup(currentGroup.id, {
        layout: {
          ...layout,
          position_x: Number(newX.toFixed(3)),
          position_y: Number(newY.toFixed(3))
        }
      });
    } else if (isResizing) {
      const deltaX = (e.clientX - resizeStartRef.current.x) / (rect.width * 0.4);
      const newScale = Math.max(0.6, Math.min(2.4, resizeStartRef.current.initialScale + deltaX));

      onUpdateGroup(currentGroup.id, {
        layout: {
          ...layout,
          scale: Number(newScale.toFixed(2))
        }
      });
    }
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    if (isDragging) setIsDragging(false);
    if (isResizing) setIsResizing(false);
    try {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {}
  };

  const handleResizePointerDown = (e: React.PointerEvent) => {
    e.stopPropagation();
    resizeStartRef.current = {
      x: e.clientX,
      initialScale: scale
    };
    setIsResizing(true);
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  // Inline text editing commit
  const handleCommitInlineText = () => {
    if (!inlineEditText.trim()) {
      setIsEditingInline(false);
      return;
    }
    const rawWords = inlineEditText.trim().split(/\s+/);
    const duration = currentGroup.end - currentGroup.start;
    const step = duration / Math.max(1, rawWords.length);

    const updatedWords: Word[] = rawWords.map((text, idx) => ({
      id: `${currentGroup.id}-w${idx}`,
      text,
      start: Number((currentGroup.start + idx * step).toFixed(2)),
      end: Number((currentGroup.start + (idx + 1) * step).toFixed(2)),
      confidence: 1.0
    }));

    onUpdateGroup(currentGroup.id, {
      words: updatedWords
    });
    setIsEditingInline(false);
  };

  // Deterministic 3-layer animation evaluation (Enter / Active-Word / Exit)
  const timeSinceStart = Math.max(0, currentTime - currentGroup.start);
  const timeUntilEnd = Math.max(0, currentGroup.end - currentTime);
  
  let enterOpacity = 1.0;
  let enterTranslateY = 0;
  if (anim.enter?.type === 'fade' && anim.enter.duration_ms > 0) {
    const enterSec = anim.enter.duration_ms / 1000;
    if (timeSinceStart < enterSec) enterOpacity = timeSinceStart / enterSec;
  } else if (anim.enter?.type === 'slide_up' && anim.enter.duration_ms > 0) {
    const enterSec = anim.enter.duration_ms / 1000;
    if (timeSinceStart < enterSec) {
      const p = timeSinceStart / enterSec;
      enterOpacity = p;
      enterTranslateY = (1 - p) * 15;
    }
  }

  let exitOpacity = 1.0;
  if (anim.exit?.type === 'fade' && anim.exit.duration_ms > 0) {
    const exitSec = anim.exit.duration_ms / 1000;
    if (timeUntilEnd < exitSec) exitOpacity = timeUntilEnd / exitSec;
  }

  const computedOpacity = (style.opacity ?? 1.0) * enterOpacity * exitOpacity;

  return (
    <div
      ref={containerRef}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      className="absolute inset-0 z-20 overflow-hidden select-none"
      style={{
        paddingLeft: '4%',
        paddingRight: '4%'
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: `${posX * 100}%`,
          top: `${posY * 100}%`,
          transform: `translate(-50%, -50%) scale(${scale}) translateY(${enterTranslateY}px)`,
          maxWidth: `${(layout.max_width ?? 0.88) * 100}%`,
          opacity: computedOpacity,
          cursor: isInteractive ? (isDragging ? 'grabbing' : 'grab') : 'default'
        }}
        onPointerDown={handlePointerDownDrag}
        onDoubleClick={() => {
          if (!isInteractive) return;
          setInlineEditText(currentGroup.words.map(w => w.text).join(' '));
          setIsEditingInline(true);
        }}
        className={`relative transition-shadow group ${
          isSelected && isInteractive ? 'ring-2 ring-primary/80 shadow-[0_0_15px_rgba(37,99,235,0.35)]' : ''
        }`}
      >
        {/* ======================================================== */}
        {/* CAPCUT SELECTION OVERLAY & TRANSFORM HANDLES            */}
        {/* ======================================================== */}
        {isSelected && isInteractive && !isEditingInline && (
          <div className="absolute -inset-2.5 border border-dashed border-primary rounded-lg pointer-events-none z-30">
            {/* Top-Left: Close / Deselect handle (with 44px thumb hit-envelope) */}
            <div 
              onClick={(e) => { e.stopPropagation(); onSelectGroup(null); }}
              className="absolute -top-4 -left-4 w-9 h-9 flex items-center justify-center pointer-events-auto cursor-pointer"
              title="Deselect"
            >
              <div className="w-5 h-5 rounded-full bg-surface-200 border border-border flex items-center justify-center hover:bg-red-500 transition-colors shadow-md">
                <X size={10} className="text-white" />
              </div>
            </div>

            {/* Top-Right: Quick Edit handle (with 44px thumb hit-envelope) */}
            <div 
              onClick={(e) => {
                e.stopPropagation();
                setInlineEditText(currentGroup.words.map(w => w.text).join(' '));
                setIsEditingInline(true);
              }}
              className="absolute -top-4 -right-4 w-9 h-9 flex items-center justify-center pointer-events-auto cursor-pointer"
              title="Double click or tap to edit text"
            >
              <div className="w-5 h-5 rounded-full bg-surface-200 border border-border flex items-center justify-center hover:bg-primary transition-colors shadow-md">
                <Edit3 size={10} className="text-white" />
              </div>
            </div>

            {/* Bottom-Right: Scale / Resize handle (with 44px thumb hit-envelope) */}
            <div 
              onPointerDown={handleResizePointerDown}
              className="absolute -bottom-4 -right-4 w-9 h-9 flex items-center justify-center pointer-events-auto cursor-nwse-resize"
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
          </div>
        )}

        {/* ======================================================== */}
        {/* INLINE TEXT EDITING MODAL / INPUT                        */}
        {/* ======================================================== */}
        {isEditingInline ? (
          <div 
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            className="p-1.5 rounded-xl bg-surface-100 border border-primary shadow-hero-glow flex items-center gap-2 z-40 min-w-[220px]"
          >
            <input
              type="text"
              autoFocus
              value={inlineEditText}
              onChange={(e) => setInlineEditText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCommitInlineText();
                if (e.key === 'Escape') setIsEditingInline(false);
              }}
              className="w-full px-2.5 py-1.5 bg-surface-200 rounded-lg text-xs text-text-main font-semibold border border-border focus:outline-none focus:border-primary"
              placeholder="Edit caption text…"
            />
            <button
              type="button"
              onClick={handleCommitInlineText}
              className="min-w-[36px] min-h-[36px] flex items-center justify-center rounded-lg bg-primary hover:bg-primary-hover text-white shrink-0 active:scale-[0.98] transition-all"
            >
              <Check size={14} />
            </button>
            <button
              type="button"
              onClick={() => setIsEditingInline(false)}
              className="min-w-[36px] min-h-[36px] flex items-center justify-center rounded-lg bg-surface-200 hover:bg-surface-300 text-text-muted hover:text-text-main shrink-0 active:scale-[0.98] transition-all"
            >
              <X size={14} />
            </button>
          </div>
        ) : (
          /* ======================================================== */
          /* RENDERED TEXT AND WORD-LEVEL POP ANIMATION               */
          /* ======================================================== */
          <div
            className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${justifyClass} p-1.5 rounded-lg transition-all`}
            style={{
              ...(style.background_enabled && {
                backgroundColor: style.background_color,
                padding: `${style.background_padding_y || 8}px ${style.background_padding_x || 14}px`,
                borderRadius: `${style.background_corner_radius || 12}px`,
                backdropFilter: 'blur(8px)'
              })
            }}
          >
            {currentGroup.words.map((w, idx) => {
              const isSpoken = currentTime >= w.start && currentTime <= w.end;
              const wordOverride = w.style_override;
              const fontFam = style.font_family || 'Impact';
              const fontSize = Math.max(16, Math.min(42, Math.round((style.font_size || 54) * 0.42)));

              // Tactile scale pop calculation (Spring Punch)
              let wordScale = 1.0;
              const activeAnimType = anim.active_word?.type || anim.type;
              const punchScale = anim.active_word?.active_scale || anim.active_scale || 1.16;
              const animDurationMs = anim.active_word?.duration_ms || anim.duration_ms || 100;

              if (isSpoken && (activeAnimType === 'pop' || activeAnimType === 'bounce')) {
                const durationSec = animDurationMs / 1000;
                const elapsed = Math.max(0, currentTime - w.start);
                if (elapsed < durationSec) {
                  const progress = elapsed / durationSec;
                  const punch = punchScale - 1.0;
                  wordScale = 1.0 + punch * Math.cos(progress * (Math.PI / 2));
                }
              }

              // Color resolution
              let textColor = style.primary_color;
              if (isSpoken) {
                textColor = wordOverride?.highlight_color || style.highlight_color;
              } else if (wordOverride?.color) {
                textColor = wordOverride.color;
              }

              const strokeWidth = style.outline_enabled ? `${Math.max(1.5, (style.outline_width || 4.5) * 0.6)}px` : '0px';

              // Glow and Shadow filters
              const filters: string[] = [];
              if (style.shadow_enabled) {
                filters.push(`drop-shadow(${style.shadow_offset_x || 0}px ${style.shadow_offset_y || 2}px ${style.shadow_blur || 4}px ${style.shadow_color || 'rgba(0,0,0,0.85)'})`);
              }
              if (style.glow_enabled) {
                filters.push(`drop-shadow(0px 0px ${style.glow_blur || 14}px ${style.glow_color || '#00F0FF'})`);
              }

              return (
                <span
                  key={w.id || idx}
                  style={{
                    fontFamily: fontFam,
                    fontSize: `${fontSize}px`,
                    fontWeight: style.font_weight || 'bold',
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
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
