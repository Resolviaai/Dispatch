import React, { useState, useEffect, useLayoutEffect, useRef, useMemo } from 'react';
import {
  Film,
  Play,
  Pause,
  Maximize2,
  Minimize2,
  ChevronLeft,
  RefreshCw,
  Sparkles,
  Undo2,
  Redo2,
  X,
  Scissors,
  Send,
  MoreVertical,
  Layers,
  Clock,
  Sliders,
  Check,
  Copy,
  Trash2,
  AlertCircle,
  Eye,
  CheckCircle2,
  Share2,
  Pencil,
  RotateCw
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig } from '../captions/types';
import { CaptionTimeline } from '../captions/CaptionTimeline';
import { CaptionStudio, ActivePanel } from '../captions/CaptionStudio';
import { DispatchCaptionRenderer } from '../captions/DispatchCaptionRenderer';
import { useCaptionHistory } from '../captions/useCaptionHistory';
import { apiUrl } from '../bridge';

export type LayoutMode = 'fit_black' | 'fit_blur' | 'native_916' | 'crop_916' | 'crop_follow' | 'native_169' | 'landscape';
export type ViewMode = 'library' | 'detail' | 'studio' | 'publish';
export type FilterStatus = 'all' | 'ready' | 'approved';

export interface AvailableLayoutMode {
  id: LayoutMode;
  label: string;
  target_aspect: '16:9' | '9:16';
  description?: string;
}

const DEFAULT_PORTRAIT_MODES: AvailableLayoutMode[] = [
  { id: 'fit_black', label: 'Fit + Black', target_aspect: '16:9', description: '16:9 wide frame with black side bars' },
  { id: 'fit_blur', label: 'Fit + Blur', target_aspect: '16:9', description: '16:9 wide frame with blurred background' },
  { id: 'native_916', label: 'Native 9:16', target_aspect: '9:16', description: '100% full original vertical video (no bars, no crop)' },
];

const DEFAULT_LANDSCAPE_MODES: AvailableLayoutMode[] = [
  { id: 'crop_916', label: 'Crop 9:16', target_aspect: '9:16', description: 'Full vertical crop to fill 9:16' },
  { id: 'fit_blur', label: 'Fit + Blur', target_aspect: '9:16', description: 'Vertical 9:16 with blurred top/bottom bars' },
  { id: 'fit_black', label: 'Fit + Black', target_aspect: '9:16', description: 'Vertical 9:16 with black top/bottom bars' },
  { id: 'native_169', label: 'Native 16:9', target_aspect: '16:9', description: '100% full original horizontal 16:9 (no bars, no crop)' },
];

export interface ClipItem {
  id: string;
  session_id?: string | null;
  chunk_id?: string | null;
  start_time: number;
  end_time: number;
  duration: number;
  title: string;
  hook: string;
  description?: string;
  hashtags?: string;
  virality_score: number;
  layout_mode?: LayoutMode;
  rendered_layout_mode?: string;
  is_render_stale?: boolean;
  status: string;
  publish_mode?: string;
  platform_targets?: string;
  video_url?: string;
  source_video_url?: string;
  thumbnail_url?: string;
  created_at: string;
  source_orientation?: 'portrait' | 'landscape';
  source_aspect_ratio?: '9:16' | '16:9';
  available_layout_modes?: AvailableLayoutMode[];
  target_aspect?: '16:9' | '9:16';
}

interface ClipsScreenProps {
  pcHost: string;
  isMobileFrame?: boolean;
  onEditorOpenChange?: (open: boolean) => void;
}

// Brand Social Media SVGs
const YouTubeShortsIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#FF0000" />
    <path d="M10 8.5L16 12L10 15.5V8.5Z" fill="white" />
  </svg>
);

const InstagramIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <defs>
      <linearGradient id="ig-grad-clips" x1="0%" y1="100%" x2="100%" y2="0%">
        <stop offset="0%" stopColor="#FED373" />
        <stop offset="25%" stopColor="#F15245" />
        <stop offset="60%" stopColor="#D92E7F" />
        <stop offset="100%" stopColor="#9B36B7" />
      </linearGradient>
    </defs>
    <rect width="24" height="24" rx="6" fill="url(#ig-grad-clips)" />
    <rect x="5.5" y="5.5" width="13" height="13" rx="3.5" stroke="white" strokeWidth="1.5" />
    <circle cx="12" cy="12" r="3" stroke="white" strokeWidth="1.5" />
    <circle cx="15.8" cy="8.2" r="0.8" fill="white" />
  </svg>
);

const LinkedInIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#0A66C2" />
    <path
      d="M7.5 9.5H5V17H7.5V9.5ZM6.25 8.2C7.05 8.2 7.7 7.55 7.7 6.75C7.7 5.95 7.05 5.3 6.25 5.3C5.45 5.3 4.8 5.95 4.8 6.75C4.8 7.55 5.45 8.2 6.25 8.2ZM19 17H16.5V13.1C16.5 12.15 16.48 10.95 15.15 10.95C13.8 10.95 13.6 12.02 13.6 13.03V17H11.1V9.5H13.5V10.55H13.53C13.87 9.9 14.7 9.25 15.9 9.25C18.45 9.25 18.95 10.9 18.95 13.05V17H19Z"
      fill="white"
    />
  </svg>
);

const XIcon = () => (
  <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none">
    <rect width="24" height="24" rx="6" fill="#18181B" stroke="#27272A" strokeWidth="1" />
    <path
      d="M14.7 6.5H16.8L12.2 11.75L17.6 18.5H13.35L10.02 14.15L6.2 18.5H4.1L8.98 12.92L3.8 6.5H8.16L11.16 10.47L14.7 6.5ZM13.96 17.22H15.12L7.5 7.72H6.25L13.96 17.22Z"
      fill="white"
    />
  </svg>
);

const clamp = (val: number, min: number, max: number) => Math.min(max, Math.max(min, val));

function formatDuration(sec: number): string {
  const safe = Math.max(0, Number.isFinite(sec) ? sec : 0);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  if (m === 0) return `${s}s`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatTimeCode(sec: number): string {
  const safe = Math.max(0, Number.isFinite(sec) ? sec : 0);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function normalizeCaptionTrack(raw: any, clipId: string, duration: number): CaptionTrack {
  const default_style: StyleConfig = {
    font_family: raw?.default_style?.font_family || 'Montserrat',
    font_size: raw?.default_style?.font_size || 44,
    font_weight: raw?.default_style?.font_weight || '900',
    text_transform: raw?.default_style?.text_transform || 'none',
    letter_spacing: raw?.default_style?.letter_spacing || 0,
    line_height: raw?.default_style?.line_height || 1.25,
    primary_color: raw?.default_style?.primary_color || raw?.default_style?.color || '#FFFF00',
    highlight_color: raw?.default_style?.highlight_color || '#FFFFFF',
    outline_enabled: raw?.default_style?.outline_enabled ?? (raw?.default_style?.stroke_width ? true : false),
    outline_color: raw?.default_style?.outline_color || raw?.default_style?.stroke_color || '#000000',
    outline_width: raw?.default_style?.outline_width || raw?.default_style?.stroke_width || 3,
    shadow_enabled: raw?.default_style?.shadow_enabled ?? raw?.default_style?.has_shadow ?? true,
    shadow_color: raw?.default_style?.shadow_color || '#000000',
    shadow_offset_x: raw?.default_style?.shadow_offset_x || 0,
    shadow_offset_y: raw?.default_style?.shadow_offset_y || 2,
    shadow_blur: raw?.default_style?.shadow_blur || 6,
    background_enabled: raw?.default_style?.background_enabled ?? raw?.default_style?.has_background ?? false,
    background_color: raw?.default_style?.background_color || '#000000',
    background_padding_x: raw?.default_style?.background_padding_x || 12,
    background_padding_y: raw?.default_style?.background_padding_y || 6,
    background_corner_radius: raw?.default_style?.background_corner_radius || 8,
  };

  const default_layout: LayoutConfig = {
    position_x: raw?.default_layout?.position_x ?? 0.5,
    position_y: raw?.default_layout?.position_y ?? 0.78,
    scale: raw?.default_layout?.scale ?? 1.0,
    rotation: raw?.default_layout?.rotation ?? 0,
    alignment: raw?.default_layout?.alignment || 'center',
    max_width: raw?.default_layout?.max_width ?? 0.88,
    max_lines: raw?.default_layout?.max_lines ?? 2,
  };

  const default_animation: AnimationConfig = {
    type: raw?.default_animation?.type || 'pop',
    duration_ms: raw?.default_animation?.duration_ms || 120,
    active_scale: raw?.default_animation?.active_scale || 1.15,
    easing: raw?.default_animation?.easing || 'ease_out',
    enter: {
      type: raw?.default_animation?.entrance_animation || raw?.default_animation?.enter?.type || 'scale_up',
      duration_ms: 200,
    },
    active_word: {
      type: raw?.default_animation?.active_word_effect || raw?.default_animation?.active_word?.type || 'pop',
      active_scale: 1.15,
      duration_ms: 120,
    },
    exit: {
      type: raw?.default_animation?.exit_animation || raw?.default_animation?.exit?.type || 'none',
      duration_ms: 150,
    },
  };

  const rawGroups = Array.isArray(raw?.groups) ? raw.groups : [];
  const groups: CaptionGroup[] = rawGroups.map((g: any, gIdx: number) => {
    const rawWords = Array.isArray(g.words) ? g.words : [];
    const words: Word[] = rawWords.map((w: any, wIdx: number) => ({
      id: w.id || `w_${gIdx}_${wIdx}`,
      text: w.text || w.word || '',
      start: typeof w.start === 'number' ? w.start : g.start,
      end: typeof w.end === 'number' ? w.end : g.end,
      confidence: w.confidence ?? 0.95,
      style_override: w.style_override,
    }));

    const rawPosY = g.layout?.position_y ?? default_layout.position_y;
    const rawPosX = g.layout?.position_x ?? default_layout.position_x;

    return {
      id: g.id || `grp_${gIdx}`,
      start: typeof g.start === 'number' ? g.start : 0,
      end: typeof g.end === 'number' ? g.end : duration,
      words: words.length > 0 ? words : [{
        id: `w_${gIdx}_0`,
        text: g.text || '',
        start: g.start || 0,
        end: g.end || duration,
      }],
      layout: {
        ...default_layout,
        ...(g.layout || {}),
        position_x: rawPosX > 1 ? rawPosX / 100 : rawPosX,
        position_y: rawPosY > 1 ? rawPosY / 100 : rawPosY,
      },
      style: {
        ...default_style,
        ...(g.style || {}),
        primary_color: g.style?.primary_color || g.style?.color || default_style.primary_color,
        outline_color: g.style?.outline_color || g.style?.stroke_color || default_style.outline_color,
        outline_width: g.style?.outline_width || g.style?.stroke_width || default_style.outline_width,
      },
      animation: {
        ...default_animation,
        ...(g.animation || {}),
      },
    };
  });

  return {
    id: raw?.id || `track_${clipId}`,
    clip_id: clipId,
    language: raw?.language || 'en',
    default_style,
    default_layout,
    default_animation,
    groups,
  };
}

// Interactive Direct-Canvas Caption Editor (Used inside Studio Mode)
// Interactive Direct-Canvas Layer & Caption Editor (Used inside Studio Mode)
function InteractiveCanvasItem({
  group,
  isSelected,
  currentTime,
  containerRef,
  defaultLayout,
  defaultStyle,
  defaultAnimation,
  layoutMode,
  onSelectGroup,
  onUpdateGroup,
  onDeleteGroup,
  onDuplicateGroup,
}: {
  group: CaptionGroup;
  isSelected: boolean;
  currentTime: number;
  containerRef: React.RefObject<HTMLDivElement>;
  defaultLayout: LayoutConfig;
  defaultStyle: StyleConfig;
  defaultAnimation: AnimationConfig;
  layoutMode: LayoutMode;
  onSelectGroup: (id: string | null) => void;
  onUpdateGroup: (id: string, patch: Partial<CaptionGroup>) => void;
  onDeleteGroup?: (id: string) => void;
  onDuplicateGroup?: (id: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const dragStart = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const pinchStart = useRef<{ distance: number; scale: number } | null>(null);
  const transformState = useRef<{
    pointerId: number;
    centerX: number;
    centerY: number;
    startDist: number;
    startAngle: number;
    startScale: number;
    startRotation: number;
  } | null>(null);

  const [isEditingText, setIsEditingText] = useState(false);
  const [editText, setEditText] = useState('');

  const isOverlay = group.type === 'overlay';
  const layout = group.layout || defaultLayout;
  const style = group.style || defaultStyle;
  const animation = group.animation || defaultAnimation;

  const posX = layout.position_x ?? (isOverlay ? 0.5 : defaultLayout.position_x ?? 0.5);
  const posY = layout.position_y ?? (isOverlay ? 0.5 : defaultLayout.position_y ?? 0.78);
  const baseScale = layout.scale ?? 1.0;
  const rotation = layout.rotation ?? 0;
  const flipH = group.flip_h ? -1 : 1;
  const flipV = group.flip_v ? -1 : 1;

  // Base opacity
  let computedOpacity = isOverlay ? ((group.opacity ?? 100) / 100) : (style.opacity ?? 1);
  let computedScale = baseScale;
  let offsetX = 0;
  let offsetY = 0;

  // Compute overlay entrance & exit animations
  if (isOverlay && group.overlay_animation) {
    const anim = group.overlay_animation;
    const enterDur = (anim.enter_duration_ms || 400) / 1000;
    const exitDur = (anim.exit_duration_ms || 400) / 1000;
    const tIn = currentTime - group.start;
    const tOut = group.end - currentTime;

    if (anim.enter && anim.enter !== 'none' && tIn >= 0 && tIn < enterDur) {
      const p = Math.max(0, Math.min(1, tIn / enterDur));
      if (anim.enter === 'fade') computedOpacity *= p;
      else if (anim.enter === 'scale' || anim.enter === 'pop') computedScale *= Math.min(1.2, p * 1.1);
      else if (anim.enter === 'slide_up') offsetY += (1 - p) * 35;
      else if (anim.enter === 'slide_down') offsetY -= (1 - p) * 35;
      else if (anim.enter === 'slide_left') offsetX += (1 - p) * 35;
      else if (anim.enter === 'slide_right') offsetX -= (1 - p) * 35;
    } else if (anim.exit && anim.exit !== 'none' && tOut >= 0 && tOut < exitDur) {
      const p = Math.max(0, Math.min(1, tOut / exitDur));
      if (anim.exit === 'fade') computedOpacity *= p;
      else if (anim.exit === 'scale_down') computedScale *= p;
      else if (anim.exit === 'slide_up') offsetY -= (1 - p) * 35;
      else if (anim.exit === 'slide_down') offsetY += (1 - p) * 35;
      else if (anim.exit === 'slide_left') offsetX -= (1 - p) * 35;
      else if (anim.exit === 'slide_right') offsetX += (1 - p) * 35;
    }
  }

  const currentFullText = (group.words || []).map((w) => w.text || (w as any).word || '').join(' ');

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    if (isEditingText) return;
    e.stopPropagation();
    onSelectGroup(group.id);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    e.currentTarget.setPointerCapture?.(e.pointerId);

    if (pointers.current.size === 1) {
      dragStart.current = { x: e.clientX, y: e.clientY, px: posX, py: posY };
      pinchStart.current = null;
    } else if (pointers.current.size === 2) {
      const pts = [...pointers.current.values()];
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      pinchStart.current = { distance: Math.max(1, dist), scale: baseScale };
      dragStart.current = null;
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.current.size >= 2 && pinchStart.current) {
      const pts = [...pointers.current.values()];
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      const nextScale = clamp(pinchStart.current.scale * (dist / pinchStart.current.distance), 0.3, 3.0);
      onUpdateGroup(group.id, {
        layout: { ...layout, scale: Number(nextScale.toFixed(2)) },
      });
      return;
    }

    if (dragStart.current && pointers.current.size === 1 && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      const nextX = clamp(dragStart.current.px + (e.clientX - dragStart.current.x) / rect.width, 0.05, 0.95);
      const minY = layoutMode === 'landscape' ? 0.08 : 0.06;
      const maxY = layoutMode === 'landscape' ? 0.92 : 0.94;
      const nextY = clamp(dragStart.current.py + (e.clientY - dragStart.current.y) / rect.height, minY, maxY);
      onUpdateGroup(group.id, {
        layout: { ...layout, position_x: Number(nextX.toFixed(3)), position_y: Number(nextY.toFixed(3)) },
      });
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId);
    try {
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    } catch { }

    if (pointers.current.size === 0) {
      dragStart.current = null;
      pinchStart.current = null;
    }
  };

  // Transform Corner Drag Handler
  const handleTransformPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (!boxRef.current) return;
    const rect = boxRef.current.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const startDist = Math.max(10, Math.hypot(e.clientX - centerX, e.clientY - centerY));
    const startAngle = Math.atan2(e.clientY - centerY, e.clientX - centerX) * (180 / Math.PI);
    const startScale = layout.scale ?? 1.0;
    const startRotation = layout.rotation ?? 0;

    transformState.current = {
      pointerId: e.pointerId,
      centerX,
      centerY,
      startDist,
      startAngle,
      startScale,
      startRotation,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const handleTransformPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!transformState.current || transformState.current.pointerId !== e.pointerId) return;
    e.stopPropagation();
    const { centerX, centerY, startDist, startAngle, startScale, startRotation } = transformState.current;
    const currentDist = Math.hypot(e.clientX - centerX, e.clientY - centerY);
    const currentAngle = Math.atan2(e.clientY - centerY, e.clientX - centerX) * (180 / Math.PI);

    const scaleFactor = currentDist / startDist;
    const nextScale = clamp(startScale * scaleFactor, 0.2, 3.0);
    const angleDiff = currentAngle - startAngle;
    const nextRotation = Math.round((startRotation + angleDiff) % 360);

    onUpdateGroup(group.id, {
      layout: {
        ...layout,
        scale: Number(nextScale.toFixed(2)),
        rotation: nextRotation,
      },
    });
  };

  const handleTransformPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (transformState.current?.pointerId === e.pointerId) {
      transformState.current = null;
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch { }
    }
  };

  const handleSaveInlineText = () => {
    const trimmed = editText.trim();
    if (trimmed) {
      const wordsArr = trimmed.split(/\s+/).filter(Boolean);
      const totalDuration = Math.max(0.2, group.end - group.start);
      const wordDuration = totalDuration / Math.max(1, wordsArr.length);
      const nextWords: Word[] = wordsArr.map((text, idx) => ({
        id: group.words?.[idx]?.id || `w_${Date.now()}_${idx}`,
        text,
        start: Number((group.start + idx * wordDuration).toFixed(2)),
        end: Number((group.start + (idx + 1) * wordDuration).toFixed(2)),
        style_override: group.words?.[idx]?.style_override,
      }));
      onUpdateGroup(group.id, { words: nextWords });
    }
    setIsEditingText(false);
  };

  const justifyClass =
    layout.alignment === 'left' ? 'justify-start text-left' :
      layout.alignment === 'right' ? 'justify-end text-right' :
        'justify-center text-center';

  return (
    <div
      ref={boxRef}
      className="absolute pointer-events-auto select-none cursor-move"
      style={{
        left: `${posX * 100}%`,
        top: `${posY * 100}%`,
        transform: `translate(calc(-50% + ${offsetX}px), calc(-50% + ${offsetY}px)) scale(${computedScale}) rotate(${rotation}deg) scaleX(${flipH}) scaleY(${flipV})`,
        maxWidth: isOverlay ? '85%' : `${clamp(layout.max_width ?? 0.88, 0.35, 0.94) * 100}%`,
        opacity: Math.max(0, Math.min(1, computedOpacity)),
        transformOrigin: 'center center',
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      onClick={(e) => {
        e.stopPropagation();
        onSelectGroup(group.id);
      }}
      onDoubleClick={(e) => {
        if (!isOverlay) {
          e.stopPropagation();
          setEditText(currentFullText);
          setIsEditingText(true);
        }
      }}
    >
      {/* Bounding Box when Selected */}
      {isSelected && !isEditingText && (
        <div className="absolute -inset-2.5 border-2 border-primary bg-primary/[0.04] shadow-[0_0_12px_rgba(37,99,235,0.25)] pointer-events-none rounded-xl">
          {/* Top-Left Corner: Delete */}
          <div className="absolute -top-3.5 -left-3.5 w-11 h-11 -m-2 flex items-center justify-center pointer-events-auto">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteGroup?.(group.id);
              }}
              className="w-7 h-7 rounded-full bg-[#EF4444] hover:bg-red-500 text-white flex items-center justify-center shadow-lg active:scale-90 transition-transform cursor-pointer border border-white/40"
              title="Delete layer"
              aria-label="Delete"
            >
              <X size={13} strokeWidth={2.5} />
            </button>
          </div>

          {/* Top-Right Corner: Edit text (text layers only) */}
          {!isOverlay && (
            <div className="absolute -top-3.5 -right-3.5 w-11 h-11 -m-2 flex items-center justify-center pointer-events-auto">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditText(currentFullText);
                  setIsEditingText(true);
                }}
                className="w-7 h-7 rounded-full bg-[#2563EB] hover:bg-blue-500 text-white flex items-center justify-center shadow-lg active:scale-90 transition-transform cursor-pointer border border-white/40"
                title="Edit text content"
                aria-label="Edit text"
              >
                <Pencil size={12} strokeWidth={2} />
              </button>
            </div>
          )}

          {/* Bottom-Left Corner: Duplicate */}
          <div className="absolute -bottom-3.5 -left-3.5 w-11 h-11 -m-2 flex items-center justify-center pointer-events-auto">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onDuplicateGroup?.(group.id);
              }}
              className="w-7 h-7 rounded-full bg-[#1F2937] hover:bg-gray-700 text-white flex items-center justify-center shadow-lg active:scale-90 transition-transform cursor-pointer border border-white/40"
              title="Duplicate layer"
              aria-label="Duplicate"
            >
              <Copy size={12} strokeWidth={2} />
            </button>
          </div>

          {/* Bottom-Right Corner: Transform Scale & Rotate Handle */}
          <div className="absolute -bottom-3.5 -right-3.5 w-11 h-11 -m-2 flex items-center justify-center pointer-events-auto">
            <button
              type="button"
              onPointerDown={handleTransformPointerDown}
              onPointerMove={handleTransformPointerMove}
              onPointerUp={handleTransformPointerUp}
              onPointerCancel={handleTransformPointerUp}
              className="w-7 h-7 rounded-full bg-[#2563EB] hover:bg-blue-500 text-white flex items-center justify-center shadow-lg active:scale-90 transition-transform cursor-nwse-resize border border-white/40"
              title="Drag to scale and rotate"
              aria-label="Transform"
            >
              <RotateCw size={12} strokeWidth={2} />
            </button>
          </div>
        </div>
      )}

      {/* OVERLAY CONTENT */}
      {isOverlay ? (
        <div className="relative min-w-[100px] min-h-[70px] max-w-[280px] max-h-[220px] flex items-center justify-center pointer-events-none overflow-hidden rounded-lg">
          {group.overlay_type === 'video' ? (
            <video
              src={group.overlay_url}
              playsInline
              autoPlay
              loop
              muted={group.is_muted}
              className="w-full h-full object-contain rounded-lg"
            />
          ) : (
            <img
              src={group.overlay_url}
              alt={group.label || 'Overlay'}
              className="w-full h-full object-contain rounded-lg shadow-lg"
            />
          )}
        </div>
      ) : (
        /* TEXT CONTENT */
        isEditingText ? (
          <div
            className="flex flex-col items-center gap-2 p-2.5 bg-black/85 rounded-xl border border-primary shadow-2xl pointer-events-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <textarea
              autoFocus
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSaveInlineText();
                } else if (e.key === 'Escape') {
                  setIsEditingText(false);
                }
              }}
              onBlur={handleSaveInlineText}
              rows={2}
              className="w-full min-w-[200px] max-w-[300px] bg-transparent text-white text-center font-bold text-[16px] outline-none resize-none border-b border-primary/50 pb-1 font-sans"
              style={{
                fontFamily: style.font_family || 'Montserrat',
                color: style.primary_color || '#FFFF00',
              }}
              placeholder="Enter caption text…"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSaveInlineText}
                className="min-h-[32px] px-3 rounded-lg bg-primary hover:bg-primary-hover text-white text-[11px] font-semibold flex items-center gap-1 active:scale-95 shadow-hero-glow"
              >
                <Check size={12} /> Done
              </button>
            </div>
          </div>
        ) : (
          <div
            className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${justifyClass}`}
            style={{
              background: style.background_enabled ? style.background_color : 'transparent',
              padding: style.background_enabled
                ? `${style.background_padding_y || 6}px ${style.background_padding_x || 12}px`
                : undefined,
              borderRadius: style.background_enabled ? `${style.background_corner_radius || 8}px` : undefined,
              backdropFilter: style.background_enabled ? 'blur(8px)' : undefined,
            }}
          >
            {(group.words || []).map((word, wIdx) => {
              const isSpoken = currentTime >= word.start && currentTime <= word.end;
              const wordText = word.text || (word as any).word || '';
              const animType = animation?.active_word?.type || animation?.type;
              const activeScale = animType === 'pop' || animType === 'bounce' ? (animation?.active_scale || 1.15) : 1.0;
              const wordColor = isSpoken
                ? word.style_override?.highlight_color || style.highlight_color || '#FFFFFF'
                : word.style_override?.color || style.primary_color || '#FFFF00';

              return (
                <span
                  key={word.id || `w_${wIdx}`}
                  className="inline-block whitespace-pre"
                  style={{
                    fontFamily: style.font_family || 'Montserrat',
                    fontSize: `${Math.max(16, (style.font_size || 44) * 0.44)}px`,
                    fontWeight: style.font_weight || '900',
                    textTransform: style.text_transform || 'none',
                    letterSpacing: `${style.letter_spacing || 0}px`,
                    lineHeight: style.line_height || 1.25,
                    color: wordColor,
                    WebkitTextStroke: style.outline_enabled
                      ? `${Math.max(1, (style.outline_width || 2) * 0.6)}px ${style.outline_color || '#000000'}`
                      : undefined,
                    paintOrder: 'stroke fill',
                    transform: isSpoken ? `scale(${activeScale})` : 'scale(1)',
                    transformOrigin: 'center bottom',
                    transition: 'transform 80ms ease-out',
                  }}
                >
                  {wordText}
                </span>
              );
            })}
          </div>
        )
      )}
    </div>
  );
}

function InteractiveCaptionCanvas({
  track,
  currentTime,
  selectedGroupId,
  layoutMode = 'crop_follow',
  onSelectGroup,
  onUpdateGroup,
  onDeleteGroup,
  onDuplicateGroup,
}: {
  track: CaptionTrack | null;
  currentTime: number;
  selectedGroupId: string | null;
  layoutMode?: LayoutMode;
  onSelectGroup: (id: string | null) => void;
  onUpdateGroup: (id: string, patch: Partial<CaptionGroup>) => void;
  onDeleteGroup?: (id: string) => void;
  onDuplicateGroup?: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  if (!track || !track.groups.length) return null;

  // Filter all layers that are active at currentTime OR currently selected
  const visibleGroups = track.groups.filter((g) => {
    if (g.type === 'audio' || g.type === 'voiceover') return false;
    const isTimeActive = currentTime >= g.start && currentTime <= g.end;
    const isSelected = selectedGroupId === g.id;
    return isTimeActive || isSelected;
  });

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 z-20 overflow-hidden pointer-events-none"
      style={{ touchAction: 'none' }}
    >
      {visibleGroups.map((group) => (
        <InteractiveCanvasItem
          key={group.id}
          group={group}
          isSelected={selectedGroupId === group.id}
          currentTime={currentTime}
          containerRef={containerRef}
          defaultLayout={track.default_layout}
          defaultStyle={track.default_style}
          defaultAnimation={track.default_animation}
          layoutMode={layoutMode}
          onSelectGroup={onSelectGroup}
          onUpdateGroup={onUpdateGroup}
          onDeleteGroup={onDeleteGroup}
          onDuplicateGroup={onDuplicateGroup}
        />
      ))}
    </div>
  );
}

interface FramedVideoStageProps {
  sourceVideoUrl?: string;
  videoUrl?: string;
  clipStart?: number;
  clipEnd?: number;
  clipDuration?: number;
  layoutMode: LayoutMode;
  sourceOrientation?: 'portrait' | 'landscape';
  targetAspect?: '16:9' | '9:16';
  isPlaying: boolean;
  onTogglePlay: () => void;
  onTimeUpdate?: () => void;
  onLoadedMetadata?: () => void;
  videoRef: React.RefObject<HTMLVideoElement>;
  bgVideoRef: React.RefObject<HTMLVideoElement>;
  setIsPlaying: (playing: boolean) => void;
  showPlayPauseOverlay?: boolean;
  children?: React.ReactNode;
  maxHeight?: string;
  isCompact?: boolean;
  isDetail?: boolean;
}

export const FramedVideoStage: React.FC<FramedVideoStageProps> = ({
  sourceVideoUrl,
  videoUrl: _videoUrl,
  layoutMode,
  sourceOrientation = 'portrait',
  targetAspect,
  isPlaying,
  onTogglePlay,
  onTimeUpdate,
  onLoadedMetadata,
  videoRef,
  bgVideoRef,
  setIsPlaying,
  showPlayPauseOverlay = true,
  children,
  maxHeight,
  isCompact = false,
  isDetail = false,
}) => {
  const [sourceFailed, setSourceFailed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  // High-reliability media resolution: prefer raw source chunk for real-time framing,
  // fall back gracefully to rendered clip MP4 so real creator footage never shows "Source video unavailable".
  const activeVideoUrl = (!sourceFailed && sourceVideoUrl)
    ? sourceVideoUrl
    : (!videoFailed && _videoUrl ? _videoUrl : null);

  // Determine whether the container must be 16:9 widescreen or 9:16 vertical
  const isTarget169 = useMemo(() => {
    if (layoutMode === 'native_169' || layoutMode === 'landscape') return true;
    if (sourceOrientation === 'portrait' && (layoutMode === 'fit_black' || layoutMode === 'fit_blur')) return true;
    if (layoutMode === 'native_916' || layoutMode === 'crop_916' || layoutMode === 'crop_follow') return false;
    if (sourceOrientation === 'landscape' && (layoutMode === 'fit_black' || layoutMode === 'fit_blur')) return false;
    return targetAspect === '16:9';
  }, [layoutMode, sourceOrientation, targetAspect]);

  const isCrop = (layoutMode === 'crop_916' || layoutMode === 'crop_follow');

  // Exact mathematical crop scale based on measured video dimensions and target ratio
  const calculatedCropScale = useMemo(() => {
    if (!isCrop) return 1;

    const sourceAspect = (videoDimensions.width > 0 && videoDimensions.height > 0)
      ? (videoDimensions.width / videoDimensions.height)
      : (sourceOrientation === 'landscape' ? (16 / 9) : (9 / 16));

    const targetRatio = isTarget169 ? (16 / 9) : (9 / 16);

    // If source and target ratios are already equivalent, no zoom needed
    if (Math.abs(sourceAspect - targetRatio) < 0.01) {
      return 1;
    }

    // Exact scale factor to fill object-contain frame completely without artificial over-zoom
    const scale = Math.max(sourceAspect / targetRatio, targetRatio / sourceAspect);
    return Number(scale.toFixed(4));
  }, [isCrop, videoDimensions, sourceOrientation, isTarget169]);

  const stageContainerRef = useRef<HTMLDivElement>(null);
  const [stageBounds, setStageBounds] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  useLayoutEffect(() => {
    if (!isDetail) return;
    const el = stageContainerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setStageBounds({ width: Math.round(rect.width), height: Math.round(rect.height) });
      }
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [isDetail]);

  // Synchronize actual video dimensions when ref is populated
  useEffect(() => {
    const v = videoRef.current;
    if (v && v.videoWidth > 0 && v.videoHeight > 0) {
      setVideoDimensions({ width: v.videoWidth, height: v.videoHeight });
    }
  }, [videoRef, activeVideoUrl]);

  const detailDimensions = useMemo(() => {
    if (!isDetail || stageBounds.width === 0 || stageBounds.height === 0) {
      return null;
    }

    const availW = stageBounds.width;
    const availH = stageBounds.height;

    if (isTarget169) {
      // 16:9 widescreen: grow horizontally to fill width, height = width * 9/16 (bounded by availH)
      let w = availW;
      let h = Math.round(w * (9 / 16));
      if (h > availH) {
        h = availH;
        w = Math.round(h * (16 / 9));
      }
      return { width: w, height: h };
    } else {
      // 9:16 portrait: expand upward and sideways while preserving 9:16
      let h = availH;
      let w = Math.round(h * (9 / 16));
      if (w > availW) {
        w = availW;
        h = Math.round(w * (16 / 9));
      }
      return { width: w, height: h };
    }
  }, [isDetail, isTarget169, stageBounds.width, stageBounds.height]);

  useEffect(() => {
    setSourceFailed(false);
    setVideoFailed(false);
  }, [sourceVideoUrl, _videoUrl]);

  const computedMaxHeight = maxHeight || (isCompact ? '220px' : '100%');

  return (
    <div
      ref={isDetail ? stageContainerRef : undefined}
      className="w-full h-full flex items-center justify-center overflow-hidden"
    >
      <section
        style={
          isDetail
            ? {
              aspectRatio: isTarget169 ? '16 / 9' : '9 / 16',
              width: detailDimensions ? `${detailDimensions.width}px` : (isTarget169 ? '100%' : 'auto'),
              height: detailDimensions ? `${detailDimensions.height}px` : (isTarget169 ? 'auto' : '100%'),
              maxWidth: '100%',
              maxHeight: '100%',
              transition: 'width 280ms cubic-bezier(0.4, 0, 0.2, 1), height 280ms cubic-bezier(0.4, 0, 0.2, 1)',
            }
            : {
              aspectRatio: isTarget169 ? '16 / 9' : '9 / 16',
              maxHeight: computedMaxHeight,
              maxWidth: '100%',
              width: isTarget169 ? '100%' : 'auto',
              height: isTarget169 ? 'auto' : '100%',
              transition: 'max-height 200ms ease, max-width 200ms ease, aspect-ratio 200ms ease',
            }
        }
        className="relative bg-black rounded-xl border border-border overflow-hidden shadow-2xl flex items-center justify-center select-none"
      >
        {activeVideoUrl ? (
          <>
            {/* Fit + Blur synchronized background video layer: smooth opacity crossfade */}
            <video
              ref={bgVideoRef}
              src={activeVideoUrl}
              muted
              playsInline
              aria-hidden="true"
              className={`absolute inset-0 w-full h-full object-cover filter blur-2xl brightness-75 scale-110 pointer-events-none select-none z-0 transition-opacity duration-300 ease-out ${layoutMode === 'fit_blur' ? 'opacity-100' : 'opacity-0'
                }`}
            />

            {/* Primary foreground video layer: smooth scale zoom between crop and fit */}
            <video
              ref={videoRef}
              src={activeVideoUrl}
              playsInline
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                if (v.videoWidth > 0 && v.videoHeight > 0) {
                  setVideoDimensions({ width: v.videoWidth, height: v.videoHeight });
                }
                onLoadedMetadata?.();
              }}
              onTimeUpdate={onTimeUpdate}
              onError={() => {
                if (!sourceFailed && sourceVideoUrl) {
                  setSourceFailed(true);
                } else {
                  setVideoFailed(true);
                }
              }}
              onPlay={() => {
                setIsPlaying(true);
                if (layoutMode === 'fit_blur') {
                  bgVideoRef.current?.play().catch(() => { });
                }
              }}
              onPause={() => {
                setIsPlaying(false);
                bgVideoRef.current?.pause();
              }}
              onEnded={() => {
                setIsPlaying(false);
                bgVideoRef.current?.pause();
              }}
              className="absolute inset-0 z-10 w-full h-full object-contain pointer-events-none"
              style={{
                transform: isCrop ? `scale(${calculatedCropScale})` : 'scale(1)',
                transformOrigin: 'center center',
                transition: 'transform 280ms cubic-bezier(0.4, 0, 0.2, 1)',
              }}
            />
          </>
        ) : (
          <div className="flex flex-col items-center justify-center p-6 text-center text-text-muted z-10">
            <Film size={28} className="mb-2 text-text-muted" />
            <div className="text-[12px] font-semibold text-text-secondary">Source video unavailable</div>
          </div>
        )}

        {/* Caption renderer or Interactive canvas */}
        <div className="absolute inset-0 z-20 pointer-events-none">
          {children}
        </div>

        {/* Play/Pause Touch Overlay (Detail view only) */}
        {showPlayPauseOverlay && (
          <button
            type="button"
            onClick={onTogglePlay}
            className="absolute inset-0 z-15 flex items-center justify-center bg-black/10 hover:bg-black/25 active:bg-black/40 transition-colors pointer-events-auto"
            aria-label={isPlaying ? 'Pause video' : 'Play video'}
          >
            {!isPlaying && (
              <div className="w-12 h-12 rounded-full bg-black/60 border border-white/25 flex items-center justify-center text-white backdrop-blur shadow-xl active:scale-[0.95] transition-all">
                <Play size={20} fill="currentColor" className="ml-0.5" />
              </div>
            )}
          </button>
        )}
      </section>
    </div>
  );
};

export const ClipsScreen: React.FC<ClipsScreenProps> = ({
  pcHost,
  isMobileFrame = true,
  onEditorOpenChange,
}) => {
  // Navigation Model: 3-Level Architecture
  // 'library' (landing) -> 'detail' -> 'studio' | 'publish'
  const [viewMode, setViewMode] = useState<ViewMode>('library');
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('all');
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [isMoreModalOpen, setIsMoreModalOpen] = useState<boolean>(false);
  const [copiedNotice, setCopiedNotice] = useState<string | null>(null);
  const [isVideoSelected, setIsVideoSelected] = useState<boolean>(false);
  const [isFullscreenPreview, setIsFullscreenPreview] = useState<boolean>(false);
  const [activePanel, setActivePanel] = useState<ActivePanel>('none');
  const lastTimeUpdateRef = useRef<number>(0);

  useEffect(() => {
    onEditorOpenChange?.(viewMode === 'studio');
  }, [viewMode, onEditorOpenChange]);

  // Clips State
  const [readyClips, setReadyClips] = useState<ClipItem[]>([]);
  const [approvedClips, setApprovedClips] = useState<ClipItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  // Playback & Video Sync
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const bgVideoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const [filmstripFrames, setFilmstripFrames] = useState<string[]>([]);
  const [mainVideoVolume, setMainVideoVolume] = useState<number>(100);
  const [isMainVideoMuted, setIsMainVideoMuted] = useState<boolean>(false);

  const handleUpdateMainVideoAudio = (vol: number, muted: boolean) => {
    setMainVideoVolume(vol);
    setIsMainVideoMuted(muted);
    if (videoRef.current) {
      videoRef.current.volume = muted ? 0 : Math.min(1, Math.max(0, vol / 100));
      videoRef.current.muted = muted;
    }
  };

  // Active Clip Customization Fields
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('crop_follow');
  const [hookOverlayText, setHookOverlayText] = useState<string>('');
  const [showHookBanner, setShowHookBanner] = useState<boolean>(true);
  const [titleDraft, setTitleDraft] = useState<string>('');
  const [captionDraft, setCaptionDraft] = useState<string>('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['youtube_shorts', 'instagram']);

  // Canonical Caption Track with Stale Protection
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [captionClipId, setCaptionClipId] = useState<string | null>(null);
  const {
    track: captionTrack,
    setTrack: setCaptionTrack,
    undo,
    redo,
    canUndo,
    canRedo,
    resetHistory,
  } = useCaptionHistory(null);

  // Combined clip list and currently selected clip
  const allClips = useMemo(() => [...readyClips, ...approvedClips], [readyClips, approvedClips]);
  const currentClip = useMemo(
    () => allClips.find((c) => c.id === selectedClipId) || null,
    [allClips, selectedClipId]
  );

  const visibleCaptionTrack = useMemo(
    () => (currentClip && captionClipId === currentClip.id ? captionTrack : null),
    [currentClip, captionClipId, captionTrack]
  );

  // 1. Fetch Real Clips from Backend
  const fetchClips = async () => {
    setIsLoading(true);
    setFeedback(null);
    try {
      const res = await fetch(apiUrl('/api/clips', pcHost));
      if (res.ok) {
        const data = await res.json();
        const rList = Array.isArray(data.ready) ? data.ready : [];
        const aList = Array.isArray(data.recent_approved) ? data.recent_approved : [];
        setReadyClips(rList);
        setApprovedClips(aList);
      } else {
        throw new Error(`Server returned status ${res.status}`);
      }
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : 'Failed to connect to backend.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchClips();
  }, [pcHost]);

  // 2. Synchronize Selected Clip State & Defensively Clear Stale Captions
  useEffect(() => {
    if (!currentClip) {
      setCaptionClipId(null);
      setSelectedGroupId(null);
      setDuration(0);
      setCurrentTime(0);
      setIsPlaying(false);
      setFilmstripFrames([]);
      return;
    }

    // Clear stale caption track and reset playback
    setCaptionClipId(null);
    setSelectedGroupId(null);
    setCurrentTime(0);
    setIsPlaying(false);
    setFilmstripFrames([]);

    // Populate active clip draft metadata
    setTitleDraft(currentClip.title || '');
    setHookOverlayText(currentClip.hook || '');
    setCaptionDraft(currentClip.description || currentClip.hashtags || '');
    setDuration(Math.max(0, currentClip.duration || 0));

    const validModeIds = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
      ? currentClip.available_layout_modes.map((m) => m.id)
      : (currentClip.source_orientation === 'landscape'
        ? ['crop_916', 'crop_follow', 'fit_blur', 'fit_black', 'native_169', 'landscape']
        : ['fit_black', 'fit_blur', 'native_916', 'native_portrait']);

    if (currentClip.layout_mode && (validModeIds as string[]).includes(currentClip.layout_mode)) {
      setLayoutMode(currentClip.layout_mode as LayoutMode);
    } else {
      const defaultMode = currentClip.source_orientation === 'landscape' ? 'crop_916' : 'native_916';
      setLayoutMode(defaultMode as LayoutMode);
    }

    // Fetch canonical caption track for current clip
    const controller = new AbortController();
    fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((rawData) => {
        if (rawData) {
          const norm = normalizeCaptionTrack(rawData, currentClip.id, currentClip.duration || 40);
          resetHistory(norm);
          setCaptionClipId(currentClip.id);
          if (norm.groups && norm.groups.length > 0) {
            setSelectedGroupId(norm.groups[0].id);
          }
        }
      })
      .catch((err) => {
        if ((err as DOMException)?.name !== 'AbortError') {
          console.warn('Captions unavailable for clip:', err);
        }
      });

    // Fetch filmstrip frames for timeline
    fetch(apiUrl(`/api/clips/${currentClip.id}/filmstrip`, pcHost), { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((fsData) => {
        if (fsData && Array.isArray(fsData.frames) && fsData.frames.length > 0) {
          setFilmstripFrames(fsData.frames.map((f: string) => (f.startsWith('http') ? f : apiUrl(f, pcHost))));
        } else if (currentClip.thumbnail_url) {
          const tUrl = currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost);
          setFilmstripFrames([tUrl]);
        }
      })
      .catch(() => {
        if (currentClip.thumbnail_url) {
          const tUrl = currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost);
          setFilmstripFrames([tUrl]);
        }
      });

    return () => controller.abort();
  }, [currentClip?.id, pcHost]);

  // Source bounds for mapping local clip time (0 -> duration) to source video time
  const clipStart = currentClip ? (currentClip.start_time || 0) : 0;
  const clipEnd = currentClip ? (currentClip.end_time || (clipStart + (currentClip.duration || 40))) : 40;
  const clipDuration = currentClip ? Math.max(0.1, currentClip.duration || (clipEnd - clipStart)) : 40;

  // 3. Playback Controls & Dual-Layer Video Sync with Source Time Mapping
  const handleTogglePlay = () => {
    const video = videoRef.current;
    if (!video) return;

    if (video.paused) {
      if (video.currentTime >= clipEnd - 0.1 || video.currentTime < clipStart - 0.2) {
        video.currentTime = clipStart;
        if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
      }
      video.play().then(() => {
        setIsPlaying(true);
        if (bgVideoRef.current) bgVideoRef.current.play().catch(() => { });
      }).catch(() => { });
    } else {
      video.pause();
      if (bgVideoRef.current) bgVideoRef.current.pause();
      setIsPlaying(false);
    }
  };

  const handleSeek = (targetSec: number) => {
    const safeTarget = clamp(targetSec, 0, clipDuration);
    lastTimeUpdateRef.current = safeTarget;
    setCurrentTime(safeTarget);
    const sourceTarget = clipStart + safeTarget;
    if (videoRef.current) {
      videoRef.current.currentTime = sourceTarget;
    }
    if (bgVideoRef.current && layoutMode === 'fit_blur') {
      bgVideoRef.current.currentTime = sourceTarget;
    }
  };

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video) return;
    const sourceCur = video.currentTime;

    // Enforce clip bounds: stop when end of clip is reached
    if (sourceCur >= clipEnd) {
      video.pause();
      video.currentTime = clipStart;
      if (bgVideoRef.current) {
        bgVideoRef.current.pause();
        bgVideoRef.current.currentTime = clipStart;
      }
      setIsPlaying(false);
      lastTimeUpdateRef.current = 0;
      setCurrentTime(0);
      return;
    }

    if (sourceCur < clipStart - 0.2) {
      video.currentTime = clipStart;
      if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
      lastTimeUpdateRef.current = 0;
      setCurrentTime(0);
      return;
    }

    const localTime = Math.max(0, sourceCur - clipStart);
    if (Math.abs(localTime - lastTimeUpdateRef.current) >= 0.04 || sourceCur <= clipStart + 0.05) {
      lastTimeUpdateRef.current = localTime;
      setCurrentTime(localTime);
    }

    if (bgVideoRef.current && layoutMode === 'fit_blur') {
      const diff = Math.abs(bgVideoRef.current.currentTime - sourceCur);
      if (diff > 0.4) {
        bgVideoRef.current.currentTime = sourceCur;
      }
    }
  };

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (video) {
      video.currentTime = clipStart;
      if (bgVideoRef.current) {
        bgVideoRef.current.currentTime = clipStart;
      }
    }
    setCurrentTime(0);
    setDuration(clipDuration);
  };

  const handleLayoutChange = async (newMode: LayoutMode) => {
    setLayoutMode(newMode);
    if (!currentClip) return;

    const availModes = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
      ? currentClip.available_layout_modes
      : (currentClip.source_orientation === 'landscape' ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES);
    const modeObj = availModes.find((m) => m.id === newMode);
    const immediateTargetAspect = modeObj ? modeObj.target_aspect : (newMode === 'native_169' || newMode === 'landscape' ? '16:9' : '9:16');

    // Immediately reflect in in-memory clip lists so back/forth navigation retains it
    setReadyClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );
    setApprovedClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );

    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/layout`, pcHost), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_mode: newMode }),
      });
      if (res.ok) {
        const data = await res.json();
        setReadyClips((prev) =>
          prev.map((c) =>
            c.id === currentClip.id
              ? {
                ...c,
                layout_mode: data.layout_mode || newMode,
                target_aspect: data.target_aspect,
                source_orientation: data.source_orientation,
                available_layout_modes: data.available_layout_modes,
                rendered_layout_mode: data.rendered_layout_mode,
                is_render_stale: data.is_render_stale,
              }
              : c
          )
        );
        const activeObj = data.available_layout_modes?.find((m: any) => m.id === data.layout_mode);
        const modeLabel = activeObj ? activeObj.label : newMode;
        setFeedback(`Framing set to ${modeLabel}. Live preview updated.`);
        setTimeout(() => setFeedback(null), 2500);
      }
    } catch (e) {
      console.warn('Failed to persist layout framing mode:', e);
    }
  };

  // 4. Update Caption Group
  const handleUpdateGroup = (groupId: string, patch: Partial<CaptionGroup>) => {
    if (!captionTrack) return;
    setCaptionTrack({
      ...captionTrack,
      groups: captionTrack.groups.map((grp) => {
        if (grp.id !== groupId) return grp;
        const next: CaptionGroup = { ...grp, ...patch };
        if (patch.layout) next.layout = { ...(grp.layout || captionTrack.default_layout), ...patch.layout };
        if (patch.style) next.style = { ...(grp.style || captionTrack.default_style), ...patch.style };
        if (patch.animation) next.animation = { ...(grp.animation || captionTrack.default_animation), ...patch.animation };
        return next;
      }),
    });
  };

  const handleDeleteGroup = (groupId: string) => {
    if (!captionTrack) return;
    setCaptionTrack({
      ...captionTrack,
      groups: captionTrack.groups.filter((grp) => grp.id !== groupId),
    });
    setSelectedGroupId(null);
  };

  const handleDuplicateGroup = (groupId: string) => {
    if (!captionTrack) return;
    const target = captionTrack.groups.find((grp) => grp.id === groupId);
    if (!target) return;
    const newId = `grp_${Date.now()}`;
    const nextY = Math.min(0.88, (target.layout?.position_y || 0.78) + 0.06);
    const duplicated: CaptionGroup = {
      ...target,
      id: newId,
      layout: {
        ...(target.layout || captionTrack.default_layout),
        position_y: nextY,
      },
      words: (target.words || []).map((w, idx) => ({
        ...w,
        id: `w_${Date.now()}_${idx}`,
      })),
    };
    setCaptionTrack({
      ...captionTrack,
      groups: [...captionTrack.groups, duplicated],
    });
    setSelectedGroupId(newId);
  };

  // 5. Approve & Publish Handler
  const handleApprove = async () => {
    if (!currentClip) return;
    setIsSubmitting(true);
    setFeedback(null);

    try {
      if (captionTrack) {
        await fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(captionTrack),
        });
      }

      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/approve`, pcHost), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: titleDraft.trim(),
          hashtags: captionDraft.trim(),
          publish_mode: 'public',
          platforms: selectedPlatforms.join(','),
          layout_mode: layoutMode,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Approval request failed');
      }

      const approvedItem: ClipItem = {
        ...currentClip,
        title: titleDraft.trim(),
        description: captionDraft.trim(),
        layout_mode: layoutMode,
        status: 'approved',
      };

      setReadyClips((prev) => prev.filter((c) => c.id !== currentClip.id));
      setApprovedClips((prev) => [approvedItem, ...prev]);
      setFeedback('Clip approved and queued to publishing outbox.');
      setViewMode('library');
      setSelectedClipId(null);
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : 'Could not approve clip.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // 6. Reject Handler
  const handleReject = async () => {
    if (!currentClip) return;
    setIsSubmitting(true);
    setFeedback(null);

    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/reject`, pcHost), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Rejected from review studio' }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Reject request failed');
      }

      setReadyClips((prev) => prev.filter((c) => c.id !== currentClip.id));
      setApprovedClips((prev) => prev.filter((c) => c.id !== currentClip.id));
      setViewMode('library');
      setSelectedClipId(null);
      setIsMoreModalOpen(false);
      setFeedback('Clip rejected.');
    } catch (e) {
      setFeedback(e instanceof Error ? e.message : 'Could not reject clip.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCopy = (text: string, label: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedNotice(`${label} copied!`);
      setTimeout(() => setCopiedNotice(null), 2200);
    }
  };

  const togglePlatform = (pId: string) => {
    setSelectedPlatforms((prev) => (prev.includes(pId) ? prev.filter((p) => p !== pId) : [...prev, pId]));
  };

  // Filtered clip list for Library View
  const filteredClips = useMemo(() => {
    if (filterStatus === 'ready') return readyClips;
    if (filterStatus === 'approved') return approvedClips;
    return allClips;
  }, [filterStatus, readyClips, approvedClips, allClips]);

  // Group clips by source session for clean structure
  const groupedSessions = useMemo(() => {
    const map = new Map<string, ClipItem[]>();
    filteredClips.forEach((clip) => {
      const sessKey = clip.session_id || 'Direct Ingestion';
      if (!map.has(sessKey)) {
        map.set(sessKey, []);
      }
      map.get(sessKey)!.push(clip);
    });
    return Array.from(map.entries());
  }, [filteredClips]);

  // Unique session count for header subtitle
  const uniqueSessionCount = useMemo(() => {
    const s = new Set<string>();
    allClips.forEach((c) => {
      if (c.session_id) s.add(c.session_id);
    });
    return Math.max(s.size, 1);
  }, [allClips]);

  // =========================================================================
  // VIEW 1: CLIPS LIBRARY (Landing View of Clips Tab)
  // =========================================================================
  const renderLibraryView = () => (
    <div className="w-full h-full flex flex-col bg-studio overflow-hidden">
      {/* 1. COMPACT HEADER */}
      <header className="shrink-0 px-3.5 pt-3 pb-2 border-b border-border bg-[#16191E] z-20">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-primary/15 border border-primary/30 flex items-center justify-center shrink-0">
              <Film size={15} className="text-primary" />
            </div>
            <div className="min-w-0">
              <h1 className="text-[15px] font-semibold tracking-tight text-text-main truncate leading-tight">
                Clips
              </h1>
              <p className="text-[11px] text-text-secondary truncate">
                {readyClips.length} ready to review &bull; {allClips.length} total from {uniqueSessionCount} session{uniqueSessionCount === 1 ? '' : 's'}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={fetchClips}
            disabled={isLoading}
            className="min-h-[44px] min-w-[44px] rounded-xl bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95] transition-all"
            title="Refresh clips"
          >
            <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* 2. STATUS / FILTER TABS */}
        <div className="mt-2.5 flex items-center gap-1.5">
          {[
            { id: 'all' as FilterStatus, label: 'All', count: allClips.length },
            { id: 'ready' as FilterStatus, label: 'Ready', count: readyClips.length },
            { id: 'approved' as FilterStatus, label: 'Approved', count: approvedClips.length },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setFilterStatus(tab.id)}
              className={`min-h-[34px] px-3 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 active:scale-[0.98] transition-all ${filterStatus === tab.id
                ? 'bg-primary/20 border-primary text-text-main shadow-hero-sm'
                : 'bg-surface-200 border-border text-text-muted hover:text-text-secondary'
                }`}
            >
              <span>{tab.label}</span>
              <span className="font-mono tabular-nums text-[10px] px-1.5 py-0.2 rounded-full bg-surface-300">
                {tab.count}
              </span>
            </button>
          ))}
        </div>
      </header>

      {/* FEEDBACK BANNER */}
      {feedback && (
        <div className="mx-3 mt-2 shrink-0 px-3 py-2 rounded-xl bg-surface-100 border border-border flex items-center justify-between text-[11px] text-text-secondary">
          <div className="flex items-center gap-2 truncate">
            <CheckCircle2 size={14} className="text-primary shrink-0" />
            <span className="truncate">{feedback}</span>
          </div>
          <button type="button" onClick={() => setFeedback(null)} className="text-text-muted hover:text-text-main">
            <X size={12} />
          </button>
        </div>
      )}

      {/* 3. VERTICALLY SCROLLABLE CLIPS LIST */}
      <main className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-4 pb-28">
        {isLoading && allClips.length === 0 ? (
          /* Loading Skeletons */
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-28 rounded-2xl bg-surface-100 border border-border animate-pulse p-3 flex gap-3">
                <div className="w-16 h-full rounded-xl bg-surface-200" />
                <div className="flex-1 space-y-2 py-1">
                  <div className="h-4 bg-surface-200 rounded w-3/4" />
                  <div className="h-3 bg-surface-200 rounded w-1/2" />
                  <div className="h-3 bg-surface-200 rounded w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : filteredClips.length === 0 ? (
          /* Empty State */
          <div className="h-full flex flex-col items-center justify-center p-6 text-center select-none my-12">
            <div className="w-16 h-16 rounded-2xl bg-surface-100 border border-border flex items-center justify-center mb-4 text-text-muted">
              <Film size={26} />
            </div>
            <h2 className="text-[17px] font-semibold tracking-tight text-text-main">No clips yet</h2>
            <p className="text-[12px] text-text-secondary mt-1.5 max-w-[280px]">
              Dispatch will show generated clips here after processing a recording session.
            </p>
            <button
              type="button"
              onClick={fetchClips}
              className="mt-5 min-h-[44px] px-5 rounded-xl bg-surface-200 border border-border text-[13px] font-semibold text-text-main active:scale-[0.98] transition-all flex items-center gap-2"
            >
              <RefreshCw size={15} />
              <span>Refresh Clips</span>
            </button>
          </div>
        ) : (
          /* Grouped Sessions */
          groupedSessions.map(([sessionId, clipsInSession]) => (
            <div key={sessionId} className="space-y-2">
              {/* Session Group Header */}
              <div className="flex items-center justify-between px-1">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-text-secondary">
                  <Layers size={13} className="text-primary shrink-0" />
                  <span className="font-mono">{sessionId.replace('sess_', 'Session ')}</span>
                </div>
                <span className="font-mono tabular-nums text-[10px] text-text-muted">
                  {clipsInSession.length}&nbsp;clip{clipsInSession.length === 1 ? '' : 's'}
                </span>
              </div>

              {/* Clip Cards */}
              <div className="space-y-2">
                {clipsInSession.map((clip) => {
                  const isReady = clip.status === 'ready_review';
                  return (
                    <div
                      key={clip.id}
                      onClick={() => {
                        setSelectedClipId(clip.id);
                        setViewMode('detail');
                      }}
                      className="group w-full rounded-2xl bg-surface-100 hover:bg-surface-200/60 border border-border hover:border-border-strong p-2.5 flex items-stretch gap-3 cursor-pointer active:scale-[0.99] transition-all shadow-sm"
                    >
                      {/* Left: 9:16 Thumbnail */}
                      <div className="relative w-16 h-28 rounded-xl bg-black border border-border/70 overflow-hidden shrink-0 flex items-center justify-center">
                        {clip.thumbnail_url ? (
                          <img
                            src={clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost)}
                            alt=""
                            className="w-full h-full object-cover"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = 'none';
                            }}
                          />
                        ) : (
                          <Film size={18} className="text-text-muted" />
                        )}

                        {/* Duration Badge */}
                        <div className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/80 font-mono tabular-nums text-[9px] font-semibold text-white">
                          {formatDuration(clip.duration)}
                        </div>
                      </div>

                      {/* Center: Metadata */}
                      <div className="flex-1 min-w-0 flex flex-col justify-between py-0.5">
                        <div>
                          <h3 className="text-[13px] font-semibold text-text-main leading-snug line-clamp-2">
                            {clip.title || clip.hook || 'Untitled Clip'}
                          </h3>
                          {clip.hook && clip.title !== clip.hook && (
                            <p className="text-[11px] text-text-secondary mt-0.5 line-clamp-1 italic">
                              &ldquo;{clip.hook}&rdquo;
                            </p>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-1.5 mt-2">
                          {/* Virality score if present */}
                          {typeof clip.virality_score === 'number' && clip.virality_score > 0 && (
                            <span className="font-mono tabular-nums text-[10px] px-1.5 py-0.5 rounded bg-primary/10 border border-primary/25 text-[#60A5FA] font-semibold flex items-center gap-1">
                              <Sparkles size={10} />
                              {clip.virality_score}%
                            </span>
                          )}

                          {/* Source session label */}
                          <span className="font-mono text-[10px] text-text-muted">
                            {clip.session_id ? clip.session_id.replace('sess_', '#') : 'Direct'}
                          </span>
                        </div>
                      </div>

                      {/* Right: Status & Action */}
                      <div className="shrink-0 flex flex-col justify-between items-end pl-1">
                        <span
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${isReady
                            ? 'bg-primary/15 text-primary border-primary/30'
                            : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                            }`}
                        >
                          {isReady ? 'Ready' : 'Approved'}
                        </span>

                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedClipId(clip.id);
                            setIsMoreModalOpen(true);
                          }}
                          className="min-h-[44px] min-w-[44px] rounded-lg text-text-muted hover:text-text-main flex items-center justify-center active:scale-[0.95]"
                          title="More options"
                        >
                          <MoreVertical size={16} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </main>
    </div>
  );

  // =========================================================================
  // VIEW 2: CLIP DETAIL SCREEN (Bridge: Player + Actions: [Edit] [Publish])
  // =========================================================================
  const renderDetailView = () => {
    if (!currentClip) return null;

    const progressPct = Math.min(100, Math.max(0, (currentTime / Math.max(1, duration || currentClip.duration || 40)) * 100));

    return (
      <div className="w-full h-full flex flex-col bg-studio overflow-hidden">
        {/* TOP BAR (Target: 48–52px high, 36–40px touch buttons) */}
        <header className="shrink-0 h-[50px] px-3 border-b border-border bg-[#16191E] z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => {
                setViewMode('library');
                setSelectedClipId(null);
              }}
              className="w-[38px] h-[38px] rounded-xl bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95] transition-all"
              title="Back to Clips Library"
              aria-label="Back"
            >
              <ChevronLeft size={18} />
            </button>
            <div className="min-w-0">
              <h2 className="text-[14px] font-semibold tracking-tight text-text-main truncate leading-tight">
                {currentClip.title || 'Clip Detail'}
              </h2>
              <div className="text-[10px] text-text-secondary truncate font-mono">
                {currentClip.session_id ? `Source: ${currentClip.session_id}` : 'Dispatch Review'}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setIsMoreModalOpen(true)}
            className="w-[38px] h-[38px] rounded-xl bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95] transition-all"
            title="Options"
            aria-label="Options"
          >
            <MoreVertical size={16} />
          </button>
        </header>

        {/* DETAIL MAIN VIEWPORT: Responsive preview with stable visual center */}
        <main className="flex-1 min-h-0 flex flex-col px-3 pt-1 pb-2 gap-2 overflow-hidden">
          {/* 1. CLEAN VIDEO PLAYER WITH DYNAMIC RESPONSIVE FRAMING: Stable center stage */}
          <div className="flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden py-1">
            <FramedVideoStage
              sourceVideoUrl={apiUrl(currentClip.source_video_url || `/api/clips/${currentClip.id}/source`, pcHost)}
              videoUrl={currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined}
              clipStart={clipStart}
              clipEnd={clipEnd}
              clipDuration={clipDuration}
              layoutMode={layoutMode}
              sourceOrientation={currentClip.source_orientation || 'portrait'}
              targetAspect={currentClip.target_aspect}
              isPlaying={isPlaying}
              onTogglePlay={handleTogglePlay}
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={handleLoadedMetadata}
              videoRef={videoRef}
              bgVideoRef={bgVideoRef}
              setIsPlaying={setIsPlaying}
              showPlayPauseOverlay={true}
              isDetail={true}
            >
              <DispatchCaptionRenderer
                track={visibleCaptionTrack}
                currentTime={currentTime}
                layoutMode={layoutMode}
                showBoundingBox={false}
              />
            </FramedVideoStage>
          </div>

          {/* 2. BOTTOM CONTROLS DOCK: Transport + Action Buttons + Framing Options */}
          <div className="w-full shrink-0 space-y-2">
            {/* Playback Transport Row (Target: 40–44px high) */}
            <section className="w-full h-11 flex items-center gap-3 px-1">
              {/* Play / Pause button (Target: 32–36px) */}
              <button
                type="button"
                onClick={handleTogglePlay}
                className="w-[34px] h-[34px] rounded-full bg-surface-200 hover:bg-surface-300 border border-border text-text-main flex items-center justify-center active:scale-[0.95] shrink-0 transition-colors shadow-sm"
                title={isPlaying ? 'Pause' : 'Play'}
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? <Pause size={14} /> : <Play size={14} fill="currentColor" className="ml-0.5" />}
              </button>

              {/* Creator App Scrubber */}
              <div
                className="flex-1 relative flex items-center h-8"
                onClick={(e) => e.stopPropagation()}
                onPointerDown={(e) => e.stopPropagation()}
              >
                <input
                  type="range"
                  min={0}
                  max={Math.max(1, duration || currentClip.duration || 40)}
                  step={0.05}
                  value={currentTime}
                  onChange={(e) => handleSeek(parseFloat(e.target.value))}
                  onPointerDown={(e) => e.stopPropagation()}
                  className="creator-scrubber w-full"
                  style={{
                    '--track-bg': `linear-gradient(to right, #2563EB ${progressPct}%, #282828 ${progressPct}%)`,
                  } as React.CSSProperties}
                  aria-label="Seek video playback"
                />
              </div>

              {/* Timecode display */}
              <div className="font-mono tabular-nums text-[11px] font-semibold text-text-main flex items-center gap-1 shrink-0 select-none">
                <span className="text-text-main">{formatTimeCode(currentTime)}</span>
                <span className="text-text-muted">/</span>
                <span className="text-text-secondary">{formatTimeCode(duration || currentClip.duration || 0)}</span>
              </div>
            </section>

            {/* Core Action Buttons: [Edit] and [Publish] (Target: 42–44px high, equal widths, 8–10px gap) */}
            <section className="w-full grid grid-cols-2 gap-2.5">
              {/* Edit: Opens Full Caption Studio */}
              <button
                type="button"
                onClick={() => setViewMode('studio')}
                className="h-[42px] rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-[12px] font-semibold text-text-main flex items-center justify-center gap-2 active:scale-[0.98] transition-all"
              >
                <Scissors size={15} className="text-primary" />
                <span>Edit</span>
              </button>

              {/* Publish: Opens Publishing Selector */}
              <button
                type="button"
                onClick={() => setViewMode('publish')}
                className="h-[42px] rounded-xl bg-primary hover:bg-primary-hover text-white text-[12px] font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all"
              >
                <Send size={14} />
                <span>Publish</span>
              </button>
            </section>

            {/* Layout Framing Quick Select (Target: ~68–76px total, buttons 32–36px high) */}
            {(() => {
              const availableModes = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
                ? currentClip.available_layout_modes
                : (currentClip.source_orientation === 'landscape' ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES);

              const activeModeObj = availableModes.find((m) => m.id === layoutMode) || availableModes[0];

              return (
                <section className="w-full h-[72px] bg-surface-100 border border-border rounded-xl px-2.5 py-2 flex flex-col justify-between">
                  <div className="flex items-center justify-between text-[11px] leading-tight">
                    <span className="font-semibold text-text-main">Layout Framing</span>
                    <span className="font-mono text-[10px] text-primary font-semibold">
                      {activeModeObj ? `${activeModeObj.target_aspect} ${activeModeObj.label}` : layoutMode}
                    </span>
                  </div>
                  <div className="flex gap-2 overflow-x-auto no-scrollbar">
                    {availableModes.map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => handleLayoutChange(opt.id)}
                        className={`h-[34px] px-2.5 rounded-lg border text-[11px] font-medium whitespace-nowrap active:scale-[0.98] transition-all flex-1 min-w-[64px] ${layoutMode === opt.id
                          ? 'bg-primary/20 border-primary text-text-main font-semibold shadow-hero-sm'
                          : 'bg-surface-200 border-border text-text-secondary hover:bg-surface-300'
                          }`}
                        title={opt.description}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </section>
              );
            })()}
          </div>
        </main>
      </div>
    );
  };

  // =========================================================================
  // VIEW 3: FULL CAPTION STUDIO (Deep Edit Workspace)
  // =========================================================================
  const renderStudioView = () => {
    if (!currentClip) return null;

    return (
      <div className="w-full h-full flex flex-col bg-[#0D1014] text-text-main overflow-hidden select-none relative">
        {/* 1. COMPACT TOP TOOLBAR (Back · Title · Done) */}
        <header className="shrink-0 h-10 px-3 border-b border-[#232832] bg-[#16191E] z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => {
                setViewMode('detail');
                setSelectedGroupId(null);
                setIsVideoSelected(false);
              }}
              className="w-7 h-7 rounded-lg bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95"
              title="Back to Clip Detail"
            >
              <ChevronLeft size={16} />
            </button>
            <div className="min-w-0">
              <h2 className="text-[12px] sm:text-[13px] font-semibold text-text-main truncate leading-tight">
                {currentClip.title || 'Clip Editor'}
              </h2>
            </div>
          </div>

          <button
            type="button"
            onClick={() => {
              setViewMode('detail');
              setSelectedGroupId(null);
              setIsVideoSelected(false);
              setActivePanel('none');
            }}
            className="h-7 px-3 rounded-lg bg-primary text-white text-[11px] font-semibold active:scale-95 transition-all shadow-hero-sm"
          >
            Done
          </button>
        </header>

        {/* 2. VIDEO PREVIEW STAGE (Fixed ~38% split, zero resizing during panel changes) */}
        <div
          className="h-[50%] min-h-[200px] max-h-[50%] shrink-0 w-full flex items-center justify-center p-1 bg-black relative overflow-hidden"
          onClick={() => {
            // Clicking canvas background deselects layer and video
            setSelectedGroupId(null);
            setIsVideoSelected(false);
            setActivePanel('none');
          }}
        >
          <FramedVideoStage
            sourceVideoUrl={apiUrl(currentClip.source_video_url || `/api/clips/${currentClip.id}/source`, pcHost)}
            videoUrl={currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined}
            clipStart={clipStart}
            clipEnd={clipEnd}
            clipDuration={clipDuration}
            layoutMode={layoutMode}
            sourceOrientation={currentClip.source_orientation || 'portrait'}
            targetAspect={currentClip.target_aspect}
            isPlaying={isPlaying}
            onTogglePlay={handleTogglePlay}
            onTimeUpdate={handleTimeUpdate}
            onLoadedMetadata={handleLoadedMetadata}
            videoRef={videoRef}
            bgVideoRef={bgVideoRef}
            setIsPlaying={setIsPlaying}
            showPlayPauseOverlay={false}
            isCompact={false}
          >
            <InteractiveCaptionCanvas
              track={visibleCaptionTrack}
              currentTime={currentTime}
              selectedGroupId={selectedGroupId}
              layoutMode={layoutMode}
              onSelectGroup={(id) => {
                setSelectedGroupId(id);
                if (id) setIsVideoSelected(false);
              }}
              onUpdateGroup={handleUpdateGroup}
              onDeleteGroup={handleDeleteGroup}
              onDuplicateGroup={handleDuplicateGroup}
            />
          </FramedVideoStage>
        </div>

        {/* LOWER WORKSPACE (~2/3 screen split: Transport + Timeline / Tertiary Panel) */}
        <div className="flex-1 min-h-0 relative flex flex-col bg-[#121418] overflow-hidden">
          {/* 3. CAPCUT TRANSPORT-CONTROL ROW (Fullscreen · Play/Pause · Undo/Redo) */}
          <div className="shrink-0 h-9 px-3 bg-[#13161B] border-y border-[#232832] flex items-center justify-between text-[11px] z-20">
            {/* Left: Fullscreen / Expand preview icon + Timecode */}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setIsFullscreenPreview(true)}
                className="w-7 h-7 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-all"
                title="Expand Fullscreen Preview"
                aria-label="Expand Preview"
              >
                <Maximize2 size={13} />
              </button>
              <div className="font-mono tabular-nums text-[10px] sm:text-[11px] font-semibold text-text-main flex items-center gap-1">
                <span>{formatTimeCode(currentTime)}</span>
                <span className="text-text-muted">/</span>
                <span className="text-text-secondary">{formatTimeCode(duration || currentClip.duration || 0)}</span>
              </div>
            </div>

            {/* Center: Prominent Play/Pause button */}
            <button
              type="button"
              onClick={handleTogglePlay}
              className="w-7 h-7 rounded-full bg-primary hover:bg-primary-hover text-white flex items-center justify-center shadow-hero-glow active:scale-95 transition-all"
              aria-label={isPlaying ? 'Pause' : 'Play'}
            >
              {isPlaying ? <Pause size={12} /> : <Play size={12} fill="currentColor" className="ml-0.5" />}
            </button>

            {/* Right: Undo & Redo genuine action icon buttons */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={!canUndo}
                onClick={undo}
                className="w-7 h-7 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center justify-center text-text-secondary hover:text-text-main disabled:opacity-25 active:scale-95 transition-all"
                title="Undo"
                aria-label="Undo"
              >
                <Undo2 size={12} />
              </button>
              <button
                type="button"
                disabled={!canRedo}
                onClick={redo}
                className="w-7 h-7 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center justify-center text-text-secondary hover:text-text-main disabled:opacity-25 active:scale-95 transition-all"
                title="Redo"
                aria-label="Redo"
              >
                <Redo2 size={12} />
              </button>
            </div>
          </div>

          {activePanel === 'none' ? (
            <>
              {/* 4. MULTI-TRACK TIMELINE (Seamless edge-to-edge surface filling available workspace) */}
              <div className="flex-1 min-h-0 overflow-hidden flex flex-col bg-[#13161B]">
                <CaptionTimeline
                  track={visibleCaptionTrack}
                  currentTime={currentTime}
                  duration={duration || currentClip.duration || 40}
                  selectedGroupId={selectedGroupId}
                  isVideoSelected={isVideoSelected}
                  isPlaying={isPlaying}
                  filmstripFrames={filmstripFrames}
                  onSeek={handleSeek}
                  onSelectGroup={(id) => {
                    setSelectedGroupId(id);
                    if (id) setIsVideoSelected(false);
                  }}
                  onSelectVideo={() => {
                    setIsVideoSelected(true);
                    setSelectedGroupId(null);
                  }}
                  onUpdateGroup={handleUpdateGroup}
                />
              </div>

              {/* 5. CONTEXTUAL BOTTOM WORKSPACE / EXPANDED TOOL PANEL (Docked flush edge-to-edge) */}
              <div className="h-12 shrink-0 bg-[#121418] border-t border-[#232832] z-30 pb-[env(safe-area-inset-bottom,0px)]">
                <CaptionStudio
                  track={visibleCaptionTrack}
                  currentTime={currentTime}
                  duration={duration || currentClip.duration || 40}
                  selectedGroupId={selectedGroupId}
                  isVideoSelected={isVideoSelected}
                  layoutMode={layoutMode}
                  availableLayoutModes={currentClip.available_layout_modes || []}
                  mainVideoVolume={mainVideoVolume}
                  isMainVideoMuted={isMainVideoMuted}
                  activePanel={activePanel}
                  onActivePanelChange={setActivePanel}
                  onUpdateMainVideoAudio={handleUpdateMainVideoAudio}
                  onSelectGroup={(id) => {
                    setSelectedGroupId(id);
                    if (id) setIsVideoSelected(false);
                  }}
                  onSelectVideo={(sel) => {
                    setIsVideoSelected(sel);
                    if (sel) setSelectedGroupId(null);
                  }}
                  onUpdateTrack={setCaptionTrack}
                  onLayoutChange={handleLayoutChange}
                  onSeek={handleSeek}
                  canUndo={canUndo}
                  canRedo={canRedo}
                  onUndo={undo}
                  onRedo={redo}
                />
              </div>
            </>
          ) : (
            /* Tertiary View: Detailed Settings Panel fills lower workspace, zero preview jumping */
            <div className="flex-1 min-h-0 w-full h-full bg-[#16191E] z-30 pb-[env(safe-area-inset-bottom,0px)] overflow-hidden">
              <CaptionStudio
                track={visibleCaptionTrack}
                currentTime={currentTime}
                duration={duration || currentClip.duration || 40}
                selectedGroupId={selectedGroupId}
                isVideoSelected={isVideoSelected}
                layoutMode={layoutMode}
                availableLayoutModes={currentClip.available_layout_modes || []}
                mainVideoVolume={mainVideoVolume}
                isMainVideoMuted={isMainVideoMuted}
                activePanel={activePanel}
                onActivePanelChange={setActivePanel}
                onUpdateMainVideoAudio={handleUpdateMainVideoAudio}
                onSelectGroup={(id) => {
                  setSelectedGroupId(id);
                  if (id) setIsVideoSelected(false);
                }}
                onSelectVideo={(sel) => {
                  setIsVideoSelected(sel);
                  if (sel) setSelectedGroupId(null);
                }}
                onUpdateTrack={setCaptionTrack}
                onLayoutChange={handleLayoutChange}
                onSeek={handleSeek}
                canUndo={canUndo}
                canRedo={canRedo}
                onUndo={undo}
                onRedo={redo}
              />
            </div>
          )}
        </div>

        {/* 6. FULLSCREEN / THEATER PREVIEW MODAL OVERLAY */}
        {isFullscreenPreview && (
          <div className="fixed inset-0 z-50 bg-black flex flex-col justify-between p-3 select-none animate-in fade-in duration-150">
            <div className="flex items-center justify-between pb-2">
              <span className="text-[13px] font-semibold text-white truncate">{currentClip.title || 'Preview'}</span>
              <button
                type="button"
                onClick={() => setIsFullscreenPreview(false)}
                className="w-8 h-8 rounded-full bg-surface-200/80 border border-white/20 text-white flex items-center justify-center active:scale-95"
                title="Exit Fullscreen"
              >
                <Minimize2 size={16} />
              </button>
            </div>

            <div className="flex-1 flex items-center justify-center overflow-hidden">
              <FramedVideoStage
                sourceVideoUrl={apiUrl(currentClip.source_video_url || `/api/clips/${currentClip.id}/source`, pcHost)}
                videoUrl={currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined}
                clipStart={clipStart}
                clipEnd={clipEnd}
                clipDuration={clipDuration}
                layoutMode={layoutMode}
                sourceOrientation={currentClip.source_orientation || 'portrait'}
                targetAspect={currentClip.target_aspect}
                isPlaying={isPlaying}
                onTogglePlay={handleTogglePlay}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={handleLoadedMetadata}
                videoRef={videoRef}
                bgVideoRef={bgVideoRef}
                setIsPlaying={setIsPlaying}
                showPlayPauseOverlay={true}
                maxHeight="75vh"
              >
                <DispatchCaptionRenderer
                  track={visibleCaptionTrack}
                  currentTime={currentTime}
                  layoutMode={layoutMode}
                  showBoundingBox={false}
                />
              </FramedVideoStage>
            </div>

            <div className="flex items-center gap-2 pt-2 px-2">
              <button
                type="button"
                onClick={handleTogglePlay}
                className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center active:scale-95"
              >
                {isPlaying ? <Pause size={14} /> : <Play size={14} fill="currentColor" />}
              </button>
              <input
                type="range"
                min={0}
                max={Math.max(1, duration || currentClip.duration || 40)}
                step={0.1}
                value={currentTime}
                onChange={(e) => handleSeek(parseFloat(e.target.value))}
                className="flex-1 accent-primary h-1.5 bg-surface-300 rounded-lg cursor-pointer"
              />
              <span className="font-mono text-[11px] text-white tabular-nums">
                {formatTimeCode(currentTime)}
              </span>
            </div>
          </div>
        )}
      </div>
    );
  };

  // =========================================================================
  // VIEW 4: PUBLISH CLIP SCREEN (Platform-Aware Publishing Flow)
  // =========================================================================
  const renderPublishView = () => {
    if (!currentClip) return null;

    return (
      <div className="w-full h-full flex flex-col bg-studio overflow-hidden">
        {/* PUBLISH TOP BAR */}
        <header className="shrink-0 px-3 pt-2.5 pb-2 border-b border-border bg-[#16191E] z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => setViewMode('detail')}
              className="min-h-[44px] min-w-[44px] rounded-xl bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95]"
              title="Back to Clip Detail"
            >
              <ChevronLeft size={18} />
            </button>
            <div className="min-w-0">
              <h2 className="text-[14px] font-semibold tracking-tight text-text-main truncate leading-tight">
                Publish Clip
              </h2>
              <div className="text-[10px] text-text-secondary truncate font-mono">
                {currentClip.title}
              </div>
            </div>
          </div>
        </header>

        {/* PUBLISH SCROLL MAIN */}
        <main className="flex-1 min-h-0 overflow-y-auto px-3.5 py-3 space-y-3.5 pb-28">
          {/* Platform Selector */}
          <section className="space-y-1.5">
            <div className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider">
              Destinations
            </div>
            <div className="grid grid-cols-2 gap-2">
              {[
                { id: 'youtube_shorts', label: 'YouTube Shorts', icon: <YouTubeShortsIcon />, badge: 'thumbnails.set' },
                { id: 'instagram', label: 'Instagram Reels', icon: <InstagramIcon />, badge: 'cover_url' },
                { id: 'linkedin', label: 'LinkedIn Video', icon: <LinkedInIcon />, badge: 'video_thumb' },
                { id: 'x', label: 'X / Twitter', icon: <XIcon />, badge: 'native' },
              ].map((p) => {
                const isSelected = selectedPlatforms.includes(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => togglePlatform(p.id)}
                    className={`min-h-[44px] p-2.5 rounded-xl border text-[11px] font-medium flex items-center justify-between active:scale-[0.98] transition-all ${isSelected
                      ? 'bg-primary/15 border-primary text-text-main shadow-hero-sm font-semibold'
                      : 'bg-surface-100 border-border text-text-muted hover:text-text-secondary'
                      }`}
                  >
                    <div className="flex items-center gap-2">
                      {p.icon}
                      <span>{p.label}</span>
                    </div>
                    {isSelected && <Check size={14} className="text-primary" />}
                  </button>
                );
              })}
            </div>
          </section>



          {/* Content Fields */}
          <section className="space-y-2.5">
            <div>
              <label className="text-[11px] font-semibold text-text-secondary block mb-1">
                Publishing Title
              </label>
              <input
                type="text"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                className="w-full min-h-[42px] rounded-xl bg-surface-100 border border-border px-3 text-[12px] text-text-main outline-none focus:border-primary font-sans"
                placeholder="Title for publishing"
              />
            </div>

            <div>
              <label className="text-[11px] font-semibold text-text-secondary block mb-1">
                Description & #hashtags
              </label>
              <textarea
                value={captionDraft}
                onChange={(e) => setCaptionDraft(e.target.value)}
                rows={4}
                className="w-full rounded-xl bg-surface-100 border border-border p-3 text-[12px] text-text-main outline-none focus:border-primary resize-none font-sans"
                placeholder="Description, credits and hashtags"
              />
            </div>
          </section>
        </main>

        {/* STICKY PUBLISH ACTION FOOTER */}
        <footer className="shrink-0 border-t border-border bg-[#16191E] px-3.5 py-2.5 z-30">
          <button
            type="button"
            onClick={handleApprove}
            disabled={isSubmitting || selectedPlatforms.length === 0}
            className="w-full min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-[13px] font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all disabled:opacity-40"
          >
            <Send size={15} />
            <span>{isSubmitting ? 'Publishing…' : 'Approve & publish'}</span>
          </button>
        </footer>
      </div>
    );
  };

  // =========================================================================
  // VIEW 5: MORE ACTIONS MODAL / OVERFLOW
  // =========================================================================
  const renderMoreModal = () => {
    if (!isMoreModalOpen || !currentClip) return null;

    return (
      <div
        onClick={() => setIsMoreModalOpen(false)}
        className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/75 backdrop-blur-sm animate-in fade-in"
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-sm rounded-t-3xl sm:rounded-2xl bg-[#16191E] border border-border p-4 pb-8 sm:pb-4 space-y-3.5 shadow-2xl max-h-[85vh] overflow-y-auto animate-in slide-in-from-bottom-4 duration-200"
        >
          {/* Mobile Sheet Handle */}
          <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-1 mb-1 sm:hidden" />

          <div className="flex items-center justify-between pb-2 border-b border-border/70">
            <h3 className="text-[14px] font-semibold text-text-main">Clip Options</h3>
            <button
              type="button"
              onClick={() => setIsMoreModalOpen(false)}
              className="min-h-[40px] min-w-[40px] rounded-lg text-text-muted hover:text-text-main flex items-center justify-center active:scale-[0.95]"
            >
              <X size={16} />
            </button>
          </div>

          {copiedNotice && (
            <div className="px-3 py-1.5 rounded-lg bg-primary/20 border border-primary/40 text-[11px] text-primary flex items-center gap-1.5">
              <Check size={12} />
              <span>{copiedNotice}</span>
            </div>
          )}

          <div className="space-y-1">
            <button
              type="button"
              onClick={() => handleCopy(currentClip.id, 'Clip ID')}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center justify-between text-[12px] font-medium text-text-main active:scale-[0.98]"
            >
              <div className="flex items-center gap-2">
                <Copy size={14} className="text-text-muted" />
                <span>Copy Clip ID</span>
              </div>
              <span className="font-mono text-[10px] text-text-muted truncate max-w-[120px]">
                {currentClip.id}
              </span>
            </button>

            {currentClip.session_id && (
              <button
                type="button"
                onClick={() => handleCopy(currentClip.session_id!, 'Session ID')}
                className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center justify-between text-[12px] font-medium text-text-main active:scale-[0.98]"
              >
                <div className="flex items-center gap-2">
                  <Layers size={14} className="text-text-muted" />
                  <span>Source Session</span>
                </div>
                <span className="font-mono text-[10px] text-text-muted truncate max-w-[120px]">
                  {currentClip.session_id}
                </span>
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                setIsMoreModalOpen(false);
                setViewMode('studio');
              }}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-[12px] font-medium text-text-main active:scale-[0.98]"
            >
              <Scissors size={14} className="text-text-muted" />
              <span>Open in Caption Studio</span>
            </button>

            <button
              type="button"
              onClick={() => {
                setIsMoreModalOpen(false);
                setViewMode('publish');
              }}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-[12px] font-medium text-text-main active:scale-[0.98]"
            >
              <Share2 size={14} className="text-text-muted" />
              <span>Publish Destinations</span>
            </button>

            <div className="pt-2">
              <button
                type="button"
                onClick={handleReject}
                disabled={isSubmitting}
                className="w-full min-h-[44px] px-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 flex items-center justify-center gap-2 text-[12px] font-semibold text-red-400 active:scale-[0.98] transition-all"
              >
                <Trash2 size={14} />
                <span>Reject Clip</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  };

  // Main Render Routing
  return (
    <div className="w-full h-full bg-studio text-text-main flex flex-col overflow-hidden select-none">
      {viewMode === 'library' && renderLibraryView()}
      {viewMode === 'detail' && renderDetailView()}
      {viewMode === 'studio' && renderStudioView()}
      {viewMode === 'publish' && renderPublishView()}
      {renderMoreModal()}
    </div>
  );
};
