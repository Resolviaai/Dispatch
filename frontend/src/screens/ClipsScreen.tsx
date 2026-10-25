import React, { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react';
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
  RotateCw,
  Wifi,
  WifiOff,
  Lock,
  Globe,
  Link as LinkIcon,
  CheckCheck,
  ExternalLink,
  Cloud,
  Plus,
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig } from '../captions/types';
import { normalizeCaptionTrack } from '../captions/normalizeTrack';
import { CaptionTimeline } from '../captions/CaptionTimeline';
import { CaptionStudio, ActivePanel } from '../captions/CaptionStudio';
import { DispatchCaptionRenderer, applyColorOpacity, applyCaseTransform } from '../captions/DispatchCaptionRenderer';
import { useCaptionHistory } from '../captions/useCaptionHistory';
import { preserveWordTimingsOnEdit } from '../captions/timingPreservation';
import { partitionWordsIntoVisualLines } from '../captions/linePartition';
import { applySectionStylesToTrack } from '../captions/applyToAll';
import { apiUrl, getAuthHeaders } from '../bridge';
import { shouldStopSourcePlayback } from '../captions/timelineInteraction';
import { ConnectedAccount, listConnectedAccounts, WORKER_BASE } from '../accounts/accountsService';
import { currentSession } from '../auth/supabase';

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
  padded_start?: number;
  padded_end?: number;
  source_duration?: number | null;
  range_revision?: number;
  ai_suggested_start_time?: number;
  ai_suggested_end_time?: number;
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
  thumbnail_version?: number;
  created_at: string;
  published_at?: string;
  remote_url?: string;
  remote_id?: string;
  is_cloud_only?: boolean;
  source_orientation?: 'portrait' | 'landscape';
  source_aspect_ratio?: '9:16' | '16:9';
  available_layout_modes?: AvailableLayoutMode[];
  target_aspect?: '16:9' | '9:16';
}

export interface QueueStats {
  ready_count: number;
  queued_count: number;
  daily_quota_used: number;
  daily_quota_max: number;
  quota_date: string;
}

interface ClipsScreenProps {
  pcHost: string;
  isMobileFrame?: boolean;
  onEditorOpenChange?: (open: boolean) => void;
  isPcOnline?: boolean;
  sessionFilter?: string | null;
  onClearSessionFilter?: () => void;
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

function formatAge(createdAt?: string): string {
  if (!createdAt) return '';
  const t = new Date(createdAt).getTime();
  if (Number.isNaN(t)) return '';
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  return Math.floor(hrs / 24) + 'd ago';
}

function getRetentionDaysLeft(publishedAt?: string): number {
  if (!publishedAt) return 7;
  const pubDate = new Date(publishedAt).getTime();
  if (isNaN(pubDate)) return 7;
  const diffDays = 7 - (Date.now() - pubDate) / (1000 * 60 * 60 * 24);
  return Math.max(0, Math.ceil(diffDays));
}

// Interactive Direct-Canvas Caption Editor (Used inside Studio Mode)
// Interactive Direct-Canvas Layer & Caption Editor (Used inside Studio Mode)
function InteractiveCanvasItem({
  group,
  isSelected,
  currentTime,
  isPlaying = false,
  containerRef,
  defaultLayout,
  defaultStyle,
  defaultAnimation,
  layoutMode,
  onSelectGroup,
  onOpenTextEditor,
  onUpdateGroup,
  onDeleteGroup,
  onDuplicateGroup,
  onApplyLayoutToAll,
}: {
  group: CaptionGroup;
  isSelected: boolean;
  currentTime: number;
  isPlaying?: boolean;
  containerRef: React.RefObject<HTMLDivElement>;
  defaultLayout: LayoutConfig;
  defaultStyle: StyleConfig;
  defaultAnimation: AnimationConfig;
  layoutMode: LayoutMode;
  onSelectGroup: (id: string | null) => void;
  onOpenTextEditor?: (id: string) => void;
  onUpdateGroup: (id: string, patch: Partial<CaptionGroup>, commitToHistory?: boolean) => void;
  onDeleteGroup?: (id: string) => void;
  onDuplicateGroup?: (id: string) => void;
  onApplyLayoutToAll?: (id: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const overlayVideoRef = useRef<HTMLVideoElement | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());

  useEffect(() => {
    const el = overlayVideoRef.current;
    if (!el || group.overlay_type !== 'video') return;
    if (isPlaying) {
      el.play().catch(() => {});
    } else {
      el.pause();
    }
  }, [isPlaying, group.overlay_type]);

  useEffect(() => {
    const el = overlayVideoRef.current;
    if (!el || group.overlay_type !== 'video') return;
    const relTime = Math.max(0, currentTime - group.start);
    if (Math.abs(el.currentTime - relTime) > 0.25) {
      el.currentTime = relTime;
    }
  }, [currentTime, group.start, group.overlay_type]);
  const dragStart = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const pinchStart = useRef<{ distance: number; scale: number } | null>(null);
  const pendingOverlayPatchRef = useRef<{ groupId: string; patch: Partial<CaptionGroup> } | null>(null);
  const transformState = useRef<{
    pointerId: number;
    centerX: number;
    centerY: number;
    startDist: number;
    startAngle: number;
    startScale: number;
    startRotation: number;
  } | null>(null);

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

  // Memoized visual line partitioning to prevent unneeded DOM Canvas text measurements during playback
  const visualLines = useMemo(() => {
    if (isOverlay) return [];
    const fontFam = style.font_family || 'Montserrat';
    const fontSize = Math.max(10, Math.round((style.font_size || 44) * 0.44));
    const fontWeight = style.font_weight || '700';
    const letterSpacing = style.letter_spacing || 0;
    const displayWords = group.words || [];

    return partitionWordsIntoVisualLines(
      displayWords,
      fontFam,
      fontSize,
      fontWeight,
      letterSpacing,
      360 * (layout.max_width || 0.88),
      style.text_transform
    );
  }, [
    isOverlay,
    group.words,
    style.font_family,
    style.font_size,
    style.font_weight,
    style.letter_spacing,
    layout.max_width,
    style.text_transform,
  ]);

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
      const patch = { layout: { ...layout, scale: Number(nextScale.toFixed(2)) } };
      pendingOverlayPatchRef.current = { groupId: group.id, patch };
      onUpdateGroup(group.id, patch, false);
      return;
    }

    if (dragStart.current && pointers.current.size === 1 && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      const nextX = clamp(dragStart.current.px + (e.clientX - dragStart.current.x) / rect.width, 0.05, 0.95);
      const minY = layoutMode === 'landscape' ? 0.08 : 0.06;
      const maxY = layoutMode === 'landscape' ? 0.92 : 0.94;
      const nextY = clamp(dragStart.current.py + (e.clientY - dragStart.current.y) / rect.height, minY, maxY);
      const patch = { layout: { ...layout, position_x: Number(nextX.toFixed(3)), position_y: Number(nextY.toFixed(3)) } };
      pendingOverlayPatchRef.current = { groupId: group.id, patch };
      onUpdateGroup(group.id, patch, false);
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId);
    try {
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    } catch { }

    if (pointers.current.size === 0) {
      if (pendingOverlayPatchRef.current) {
        onUpdateGroup(pendingOverlayPatchRef.current.groupId, pendingOverlayPatchRef.current.patch, true);
        pendingOverlayPatchRef.current = null;
      }
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

    const patch = {
      layout: {
        ...layout,
        scale: Number(nextScale.toFixed(2)),
        rotation: nextRotation,
      },
    };
    pendingOverlayPatchRef.current = { groupId: group.id, patch };
    onUpdateGroup(group.id, patch, false);
  };

  const handleTransformPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (transformState.current?.pointerId === e.pointerId) {
      if (pendingOverlayPatchRef.current) {
        onUpdateGroup(pendingOverlayPatchRef.current.groupId, pendingOverlayPatchRef.current.patch, true);
        pendingOverlayPatchRef.current = null;
      }
      transformState.current = null;
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch { }
    }
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
        width: isOverlay ? 'auto' : 'max-content',
        maxWidth: isOverlay
          ? '85%'
          : (containerRef.current?.clientWidth
              ? `${Math.round(containerRef.current.clientWidth * clamp(layout.max_width ?? 0.88, 0.35, 0.94))}px`
              : `${clamp(layout.max_width ?? 0.88, 0.35, 0.94) * 100}%`),
        boxSizing: 'border-box',
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
          onSelectGroup(group.id);
          onOpenTextEditor?.(group.id);
        }
      }}
    >
      {/* Bounding Box when Selected */}
      {isSelected && (
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
          {!isOverlay && onOpenTextEditor && (
            <div className="absolute -top-3.5 -right-3.5 w-11 h-11 -m-2 flex items-center justify-center pointer-events-auto">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectGroup(group.id);
                  onOpenTextEditor(group.id);
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

          {/* Quick Apply to All Captions Action Pill */}
          {(!group.type || group.type === 'caption') && onApplyLayoutToAll && (
            <div className="absolute -bottom-10 left-1/2 -translate-x-1/2 pointer-events-auto z-40">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onApplyLayoutToAll(group.id);
                }}
                className="h-6 px-2.5 rounded-full bg-[#1C1F24]/95 border border-[#2E3239] hover:border-primary text-text-secondary hover:text-white text-[10px] font-semibold flex items-center gap-1.5 shadow-lg active:scale-95 transition-all whitespace-nowrap"
                title="Apply this caption's position & size to all spoken captions"
              >
                <CheckCheck size={11} className="text-primary" />
                <span>Apply to All Captions</span>
              </button>
            </div>
          )}
        </div>
      )}

      {/* OVERLAY CONTENT */}
      {isOverlay ? (
        <div className="relative min-w-[100px] min-h-[70px] max-w-[280px] max-h-[220px] flex items-center justify-center pointer-events-none overflow-hidden rounded-lg">
          {group.overlay_type === 'video' ? (
            <video
              ref={overlayVideoRef}
              src={group.overlay_url}
              playsInline
              loop
              muted={group.is_muted ?? true}
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
        (() => {
          // Animation evaluation (entrance, exit, loop)
          let enterOpacity = 1.0;
          let enterTransform = '';
          const enterType = animation?.enter?.type || (animation as any)?.enter_type || 'none';
          const enterDur = (animation?.enter?.duration_ms || (animation as any)?.enter_duration_ms || 200) / 1000;
          const enterElapsed = currentTime - group.start;
          if (enterType !== 'none' && enterElapsed >= 0 && enterElapsed < enterDur) {
            const p = Math.max(0, Math.min(1, enterElapsed / enterDur));
            if (enterType === 'fade') enterOpacity = p;
            else if (enterType === 'pop') {
              enterOpacity = Math.min(1, p * 2);
              enterTransform = `scale(${0.5 + 0.5 * Math.sin(p * Math.PI / 2)})`;
            } else if (enterType === 'slide_up') {
              enterOpacity = p;
              enterTransform = `translateY(${(1 - p) * 25}px)`;
            }
          }

          let exitOpacity = 1.0;
          let exitTransform = '';
          const exitType = animation?.exit?.type || (animation as any)?.exit_type || 'none';
          const exitDur = (animation?.exit?.duration_ms || (animation as any)?.exit_duration_ms || 150) / 1000;
          const exitRemaining = group.end - currentTime;
          if (exitType !== 'none' && exitRemaining >= 0 && exitRemaining < exitDur) {
            const p = Math.max(0, Math.min(1, exitRemaining / exitDur));
            if (exitType === 'fade') exitOpacity = p;
            else if (exitType === 'scale_down') {
              exitOpacity = p;
              exitTransform = `scale(${0.5 + 0.5 * p})`;
            } else if (exitType === 'slide_down') {
              exitOpacity = p;
              exitTransform = `translateY(${(1 - p) * 25}px)`;
            }
          }

          let loopTransform = '';
          const loopType = (animation as any)?.loop?.type || (animation as any)?.loop_type || 'none';
          if (loopType === 'pulse') {
            const speed = ((animation as any)?.loop?.speed_ms || 1000) / 1000;
            const loopTime = (currentTime - group.start) / speed;
            loopTransform = `scale(${1.0 + 0.04 * Math.sin(loopTime * 2 * Math.PI)})`;
          } else if (loopType === 'wiggle') {
            const speed = ((animation as any)?.loop?.speed_ms || 1000) / 1000;
            const loopTime = (currentTime - group.start) / speed;
            loopTransform = `rotate(${2.0 * Math.sin(loopTime * 2 * Math.PI)}deg)`;
          }

          const containerOpacity = (style.opacity ?? 1.0) * Math.min(enterOpacity, exitOpacity);
          const animTransform = [enterTransform, exitTransform, loopTransform].filter(Boolean).join(' ');

          // Shadow distance and angle calculations
          let shadowX = style.shadow_offset_x || 0;
          let shadowY = style.shadow_offset_y || 2;
          if (style.shadow_distance !== undefined && style.shadow_angle !== undefined) {
            const rad = (style.shadow_angle * Math.PI) / 180;
            shadowX = Math.round(style.shadow_distance * Math.cos(rad) * 10) / 10;
            shadowY = Math.round(style.shadow_distance * Math.sin(rad) * 10) / 10;
          }
          const shadowColor = applyColorOpacity(style.shadow_color || 'rgba(0,0,0,0.85)', style.shadow_opacity ?? 1.0);
          const glowColor = applyColorOpacity(style.glow_color || '#00F0FF', style.glow_opacity ?? 1.0);

          // Glow and shadow filters
          const filters: string[] = [];
          if (style.shadow_enabled) {
            filters.push(`drop-shadow(${shadowX}px ${shadowY}px ${style.shadow_blur || 6}px ${shadowColor})`);
          }
          if (style.glow_enabled) {
            filters.push(`drop-shadow(0px 0px ${style.glow_blur || 12}px ${glowColor})`);
          }
          const filterStr = filters.length > 0 ? filters.join(' ') : undefined;

          const bgEnabled = style.background_enabled !== false && style.background_mode !== 'none';
          const rawBgMode = bgEnabled ? (style.background_mode || 'block') : 'none';
          const isBgBlock = rawBgMode === 'block' || (rawBgMode as string) === 'full_box';
          const isBgLine = rawBgMode === 'line' || (rawBgMode as string) === 'per_line';
          const effectiveBgColor = applyColorOpacity(style.background_color || '#000000', style.background_opacity ?? 1.0);

          const bgOffsetX = (isBgBlock || isBgLine) ? (style.background_offset_x || 0) : 0;
          const bgOffsetY = (isBgBlock || isBgLine) ? (style.background_offset_y || 0) : 0;
          const bgOffsetTransform = (bgOffsetX !== 0 || bgOffsetY !== 0) ? `translate(${bgOffsetX}px, ${bgOffsetY}px)` : '';

          const fontFam = style.font_family || 'Montserrat';
          const fontSize = Math.max(10, Math.round((style.font_size || 44) * 0.44));

          return (
            <>
              <div
                className={`w-full flex flex-col ${
                  layout.alignment === 'left' ? 'items-start text-left' : layout.alignment === 'right' ? 'items-end text-right' : 'items-center text-center'
                }`}
                style={{
                  background: isBgBlock ? effectiveBgColor : 'transparent',
                  padding: isBgBlock
                    ? `${style.background_padding_y ?? 8}px ${style.background_padding_x ?? 14}px`
                    : undefined,
                  borderRadius: isBgBlock ? `${style.background_corner_radius ?? 8}px` : undefined,
                  // Implicit blur removed per specification
                  transform: [animTransform, bgOffsetTransform].filter(Boolean).join(' ') || undefined,
                  opacity: containerOpacity,
                  transition: 'opacity 50ms linear',
                }}
              >
                {visualLines.map((lineWords, lIdx) => {
                  const isCaption = (group.type || 'caption') === 'caption';
                  const highlightEnabled = isCaption && (style.highlight_enabled !== false);
                  const lineContent = lineWords.map((word, wIdx) => {
                    const hasWordTiming = isCaption && (group as any).has_word_timing !== false && (group as any).timing_mode !== 'phrase' && (word as any).is_exact !== false;
                    const isSpoken = hasWordTiming && currentTime >= (word.start ?? group.start) && currentTime <= (word.end ?? group.end);
                    const wordText = applyCaseTransform(word.text || (word as any).word || '', style.text_transform);
                    const animType = animation?.active_word?.type || animation?.type;
                    const activeScale = isCaption && isSpoken && animType && animType !== 'none' && (animType === 'pop' || animType === 'bounce')
                      ? (animation?.active_scale || 1.15)
                      : 1.0;
                    const wordColor = (isCaption && isSpoken && highlightEnabled)
                      ? word.style_override?.highlight_color || style.highlight_color || '#FFFFFF'
                      : word.style_override?.color || style.primary_color || '#FFFF00';

                    const strokeWidth = style.outline_enabled
                      ? `${Math.max(1, (style.outline_width || 2) * 0.6)}px`
                      : undefined;
                    const strokeColorWithOpacity = style.outline_enabled
                      ? applyColorOpacity(style.outline_color || '#000000', style.outline_opacity ?? 1.0)
                      : undefined;

                    return (
                      <React.Fragment key={word.id || `w_${wIdx}`}>
                        <span
                          style={{
                            fontFamily: fontFam,
                            fontSize: `${fontSize}px`,
                            fontWeight: word.style_override?.font_weight || style.font_weight || '700',
                            fontStyle: style.font_style || 'normal',
                            textDecoration: (word.style_override?.underline ?? style.underline) ? 'underline' : undefined,
                            letterSpacing: `${style.letter_spacing || 0}px`,
                            lineHeight: style.line_height || 1.25,
                            color: applyColorOpacity(wordColor, style.text_opacity ?? 1.0),
                            WebkitTextStroke: strokeWidth && strokeColorWithOpacity
                              ? `${strokeWidth} ${strokeColorWithOpacity}`
                              : undefined,
                            paintOrder: 'stroke fill',
                            filter: filterStr,
                            transform: isSpoken ? `scale(${activeScale})` : 'scale(1)',
                            display: 'inline-block',
                            transformOrigin: 'center bottom',
                            transition: 'transform 80ms ease-out',
                          }}
                        >
                          {wordText}
                        </span>
                        {wIdx < lineWords.length - 1 ? ' ' : null}
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
                            padding: `${Math.round((style.background_padding_y ?? 6) * 0.45)}px ${Math.round((style.background_padding_x ?? 12) * 0.45)}px`,
                            borderRadius: `${Math.round((style.background_corner_radius ?? 8) * 0.6)}px`,
                            // Implicit blur removed per specification
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
            </>
          );
        })()
      )}
    </div>
  );
}

function InteractiveCaptionCanvas({
  track,
  currentTime,
  isPlaying = false,
  selectedGroupId,
  layoutMode = 'crop_follow',
  onSelectGroup,
  onOpenTextEditor,
  onUpdateGroup,
  onDeleteGroup,
  onDuplicateGroup,
  onApplyLayoutToAll,
}: {
  track: CaptionTrack | null;
  currentTime: number;
  isPlaying?: boolean;
  selectedGroupId: string | null;
  layoutMode?: LayoutMode;
  onSelectGroup: (id: string | null) => void;
  onOpenTextEditor?: (id: string) => void;
  onUpdateGroup: (id: string, patch: Partial<CaptionGroup>) => void;
  onDeleteGroup?: (id: string) => void;
  onDuplicateGroup?: (id: string) => void;
  onApplyLayoutToAll?: (id: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  if (!track || !track.groups.length) return null;

  // Timeline-aware layer visibility:
  // Render text only when currentTime is strictly within that layer's [start, end] interval.
  // Selecting a layer must never force its text to remain visible outside its duration!
  // Hidden layers (is_hidden === true) are never rendered.
  const visibleGroups = track.groups.filter((g) => {
    if (g.type === 'audio' || g.type === 'voiceover' || g.is_hidden) return false;
    return currentTime >= g.start && currentTime <= g.end;
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
          isPlaying={isPlaying}
          containerRef={containerRef}
          defaultLayout={track.default_layout}
          defaultStyle={track.default_style}
          defaultAnimation={track.default_animation}
          layoutMode={layoutMode}
          onSelectGroup={onSelectGroup}
          onOpenTextEditor={onOpenTextEditor}
          onUpdateGroup={onUpdateGroup}
          onDeleteGroup={onDeleteGroup}
          onDuplicateGroup={onDuplicateGroup}
          onApplyLayoutToAll={onApplyLayoutToAll}
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
  onLoadedMetadata?: (isSourceTimebase?: boolean, mediaDuration?: number) => void;
  videoRef: React.RefObject<HTMLVideoElement>;
  bgVideoRef: React.RefObject<HTMLVideoElement>;
  setIsPlaying: (playing: boolean) => void;
  showPlayPauseOverlay?: boolean;
  children?: React.ReactNode;
  poster?: string;
  maxHeight?: string;
  isCompact?: boolean;
  isDetail?: boolean;
  preferRendered?: boolean;
  onVideoClick?: () => void;
}

export const FramedVideoStage: React.FC<FramedVideoStageProps> = ({
  sourceVideoUrl,
  videoUrl: _videoUrl,
  poster,
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
  preferRendered = false,
  onVideoClick,
}) => {
  const [sourceFailed, setSourceFailed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  // High-reliability media resolution:
  // If preferRendered is requested (reviewing pre-rendered clips in detail mode) and _videoUrl exists, use it!
  // Otherwise, attempt sourceVideoUrl first with graceful fallback to _videoUrl.
  const activeVideoUrl = preferRendered
    ? (!videoFailed && _videoUrl ? _videoUrl : (!sourceFailed && sourceVideoUrl ? sourceVideoUrl : null))
    : (!sourceFailed && sourceVideoUrl ? sourceVideoUrl : (!videoFailed && _videoUrl ? _videoUrl : null));

  const isSourceTimebase = activeVideoUrl === sourceVideoUrl;

  // Determine whether the container must be 16:9 widescreen or 9:16 vertical
  const isTarget169 = useMemo(() => {
    if (targetAspect === '16:9') return true;
    if (targetAspect === '9:16') return false;
    if (layoutMode === 'native_169' || layoutMode === 'landscape') return true;
    if (layoutMode === 'native_916' || layoutMode === 'crop_916' || layoutMode === 'crop_follow') return false;
    const isPortrait = (videoDimensions.width > 0 && videoDimensions.height > 0)
      ? videoDimensions.width < videoDimensions.height
      : sourceOrientation === 'portrait';
    if (isPortrait && (layoutMode === 'fit_black' || layoutMode === 'fit_blur')) return true;
    if (!isPortrait && (layoutMode === 'fit_black' || layoutMode === 'fit_blur')) return false;
    return false;
  }, [layoutMode, sourceOrientation, targetAspect, videoDimensions.width, videoDimensions.height]);

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
  }, [sourceVideoUrl, _videoUrl, preferRendered]);

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
              transition: 'width 240ms cubic-bezier(0.2, 0.8, 0.2, 1), height 240ms cubic-bezier(0.2, 0.8, 0.2, 1)',
              willChange: 'width, height',
            }
            : {
              aspectRatio: isTarget169 ? '16 / 9' : '9 / 16',
              maxHeight: computedMaxHeight,
              maxWidth: '100%',
              width: isTarget169 ? '100%' : 'auto',
              height: isTarget169 ? 'auto' : '100%',
              transition: 'max-height 240ms cubic-bezier(0.2, 0.8, 0.2, 1), max-width 240ms cubic-bezier(0.2, 0.8, 0.2, 1)',
              willChange: 'max-width, max-height',
            }
        }
        onClick={(e) => {
          e.stopPropagation();
          if (onVideoClick) {
            onVideoClick();
          } else {
            onTogglePlay?.();
          }
        }}
        className="relative bg-black rounded-xl border border-border overflow-hidden shadow-2xl flex items-center justify-center select-none cursor-pointer"
      >
        {activeVideoUrl ? (
          <>
            {/* Fit + Blur synchronized background video layer: only mount and decode when layoutMode === 'fit_blur' */}
            {layoutMode === 'fit_blur' && (
              <video
                ref={bgVideoRef}
                src={activeVideoUrl}
                muted
                playsInline
                preload="auto"
                aria-hidden="true"
                onLoadedData={() => {
                  if (videoRef.current && bgVideoRef.current) {
                    bgVideoRef.current.currentTime = videoRef.current.currentTime;
                    if (isPlaying) {
                      bgVideoRef.current.play().catch(() => {});
                    }
                  }
                }}
                className="absolute inset-0 w-full h-full object-cover filter blur-2xl brightness-75 scale-125 pointer-events-none select-none z-0 transition-opacity duration-300 ease-out opacity-100"
              />
            )}

            {/* Primary foreground video layer: prioritized loading with instant poster */}
            <video
              ref={videoRef}
              src={activeVideoUrl}
              poster={poster}
              preload="auto"
              playsInline
              onLoadedMetadata={(e) => {
                const v = e.currentTarget;
                if (v.videoWidth > 0 && v.videoHeight > 0) {
                  setVideoDimensions({ width: v.videoWidth, height: v.videoHeight });
                }
                onLoadedMetadata?.(isSourceTimebase, v.duration);
              }}
              onTimeUpdate={onTimeUpdate}
              onError={() => {
                if (isSourceTimebase) {
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
              className="absolute inset-0 z-10 w-full h-full object-contain pointer-events-none will-change-transform"
              style={{
                transform: isCrop ? `scale(${calculatedCropScale}) translateZ(0)` : 'scale(1) translateZ(0)',
                transformOrigin: 'center center',
                transition: 'transform 240ms cubic-bezier(0.2, 0.8, 0.2, 1)',
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
            onClick={(e) => {
              e.stopPropagation();
              onTogglePlay?.();
            }}
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
  isPcOnline,
  sessionFilter,
  onClearSessionFilter,
}) => {
  // Navigation Model: 3-Level Architecture
  // 'library' (landing) -> 'detail' -> 'studio' | 'publish'
  const [viewMode, setViewMode] = useState<ViewMode>('library');
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('ready');
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [isMoreModalOpen, setIsMoreModalOpen] = useState<boolean>(false);
  const [clipToReject, setClipToReject] = useState<ClipItem | null>(null);
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
  const [queueStats, setQueueStats] = useState<QueueStats | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [isCloudMode, setIsCloudMode] = useState<boolean>(false);
  const [remoteJobs, setRemoteJobs] = useState<Array<{ id: string; youtube_url: string; status: string; last_error?: string; created_at?: string }>>([]);
  const [showSubmitModal, setShowSubmitModal] = useState<boolean>(false);
  const [submitUrlInput, setSubmitUrlInput] = useState<string>('');
  const [isSubmittingJob, setIsSubmittingJob] = useState<boolean>(false);

  // Playback & Video Sync
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const bgVideoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const [isSourceTimebase, setIsSourceTimebase] = useState<boolean>(true);
  const [sourceDurationForClip, setSourceDurationForClip] = useState<{ clipId: string; seconds: number } | null>(null);
  const [filmstripFrames, setFilmstripFrames] = useState<string[]>([]);
  const filmstripReqIdRef = useRef<number>(0);
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
  const [publishMode, setPublishMode] = useState<'private' | 'unlisted' | 'public'>('private');
  const [hookOverlayText, setHookOverlayText] = useState<string>('');
  const [showHookBanner, setShowHookBanner] = useState<boolean>(true);
  const [titleDraft, setTitleDraft] = useState<string>('');
  const [captionDraft, setCaptionDraft] = useState<string>('');
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['youtube_shorts']);
  const [connectedAccounts, setConnectedAccounts] = useState<ConnectedAccount[]>([]);
  const [selectedAccountMap, setSelectedAccountMap] = useState<Record<string, string>>({});
  const [saveStatus, setSaveStatus] = useState<'idle' | 'unsaved' | 'saving' | 'saved' | 'error'>('idle');

  useEffect(() => {
    import('../auth/supabase').then(({ currentSession }) =>
      currentSession().then(sess => {
        if (sess?.access_token) {
          listConnectedAccounts(sess.access_token).then(accs => {
            setConnectedAccounts(accs);
            const initialPlatforms: string[] = [];
            const initialMap: Record<string, string> = {};
            const yt = accs.find(a => a.provider === 'youtube');
            if (yt) {
              initialPlatforms.push('youtube_shorts');
              initialMap['youtube_shorts'] = yt.account_id;
            }
            const ig = accs.find(a => a.provider === 'instagram');
            if (ig) {
              initialPlatforms.push('instagram');
              initialMap['instagram'] = ig.account_id;
            }
            const li = accs.find(a => a.provider === 'linkedin');
            if (li) initialMap['linkedin'] = li.account_id;
            const tw = accs.find(a => a.provider === 'twitter');
            if (tw) initialMap['x'] = tw.account_id;

            if (initialPlatforms.length > 0) {
              setSelectedPlatforms(initialPlatforms);
            }
            setSelectedAccountMap(initialMap);
          });
        }
      }));
  }, []);
  const [playbackFeedback, setPlaybackFeedback] = useState<string | null>(null);
  const previewAnimRef = useRef<{ active: boolean; restoreTime: number; endTime: number } | null>(null);
  const contextPreviewRef = useRef<{ sourceEnd: number; restoreTime: number } | null>(null);

  // Canonical Caption Track with Stale Protection
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [captionClipId, setCaptionClipId] = useState<string | null>(null);
  const [captionLoadingState, setCaptionLoadingState] = useState<'idle' | 'loading' | 'success' | 'empty' | 'error'>('idle');
  const [captionErrorMessage, setCaptionErrorMessage] = useState<string | null>(null);
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

  // Canonical selection clearing: deselects text layer, video track, and closes layer-dependent panels
  const handleClearSelection = useCallback(() => {
    setSelectedGroupId(null);
    setIsVideoSelected(false);
    setActivePanel((current) => {
      if ([
        'style',
        'fonts',
        'effects',
        'animation',
        'templates',
        'framing',
        'main_volume',
        'layer_volume',
        'overlay_animation',
        'overlay_transform',
        'layer_opacity',
      ].includes(current)) {
        return 'none';
      }
      return current;
    });
  }, []);

  const handleSelectGroup = useCallback((id: string | null) => {
    if (!id) {
      handleClearSelection();
    } else {
      setSelectedGroupId(id);
      setIsVideoSelected(false);
    }
  }, [handleClearSelection]);

  // 1. Fetch Real Clips from PC Backend or Cloud Worker
  const fetchClips = async () => {
    setIsLoading(true);
    setFeedback(null);
    let loadedFromPc = false;

    if (pcHost && pcHost.trim()) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);
        const res = await fetch(apiUrl('/api/clips', pcHost), { signal: controller.signal });
        clearTimeout(timeoutId);
        if (res.ok) {
          const data = await res.json();
          const rList = Array.isArray(data.ready) ? data.ready : [];
          const aList = Array.isArray(data.recent_approved) ? data.recent_approved : [];
          setReadyClips(rList);
          setApprovedClips(aList);
          if (data.queue_stats) {
            setQueueStats(data.queue_stats);
          }
          setConnectionError(null);
          setIsCloudMode(false);
          loadedFromPc = true;
        }
      } catch {
        // Fall through to Cloud Mode
      }
    }

    if (!loadedFromPc) {
      try {
        const session = await currentSession();
        const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
        if (token) {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 5000);
          const [clipsRes, jobsRes] = await Promise.all([
            fetch(`${WORKER_BASE}/clips`, {
              headers: { 'Authorization': `Bearer ${token}` },
              signal: controller.signal,
            }).catch(() => null),
            fetch(`${WORKER_BASE}/jobs/status`, {
              headers: { 'Authorization': `Bearer ${token}` },
              signal: controller.signal,
            }).catch(() => null),
          ]);
          clearTimeout(timeoutId);

          if (clipsRes && clipsRes.ok) {
            const data = await clipsRes.json();
            const rList = Array.isArray(data.ready) ? data.ready : [];
            const aList = Array.isArray(data.recent_approved) ? data.recent_approved : [];
            setReadyClips(rList);
            setApprovedClips(aList);
            if (data.queue_stats) {
              setQueueStats(data.queue_stats);
            }
          }
          if (jobsRes && jobsRes.ok) {
            const jData = await jobsRes.json();
            if (Array.isArray(jData.jobs)) {
              setRemoteJobs(jData.jobs);
            }
          }
        }
        setIsCloudMode(true);
        setConnectionError(null);
      } catch {
        setIsCloudMode(true);
        setConnectionError(null);
      }
    }
    setIsLoading(false);
  };

  const handleSubmitRemoteJob = async () => {
    if (!submitUrlInput.trim()) return;
    setIsSubmittingJob(true);
    setFeedback(null);
    try {
      const session = await currentSession();
      const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
      if (!token) {
        setFeedback('Please sign in to submit remote processing jobs.');
        return;
      }
      const res = await fetch(`${WORKER_BASE}/jobs/submit`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: submitUrlInput.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setSubmitUrlInput('');
        setShowSubmitModal(false);
        setFeedback('Job queued in Dispatch Cloud. Clips will appear once processed.');
        fetchClips();
      } else {
        setFeedback(data.error || 'Failed to submit job.');
      }
    } catch (e: any) {
      setFeedback(e.message || 'Network error while submitting job.');
    } finally {
      setIsSubmittingJob(false);
    }
  };

  useEffect(() => {
    fetchClips();
    if (viewMode !== 'library') return;
    const interval = setInterval(() => {
      fetchClips();
    }, 6000);
    return () => clearInterval(interval);
  }, [pcHost, viewMode]);

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
    contextPreviewRef.current = null;
    previewAnimRef.current = null;
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
    if (currentClip.publish_mode && ['private', 'unlisted', 'public'].includes(currentClip.publish_mode)) {
      setPublishMode(currentClip.publish_mode as 'private' | 'unlisted' | 'public');
    } else {
      setPublishMode('private');
    }

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
    setCaptionLoadingState('loading');
    setCaptionErrorMessage(null);
    setCaptionClipId(null); // Clear previous track association immediately

    const controller = new AbortController();
    fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), { signal: controller.signal })
      .then(async (res) => {
        if (res.ok) {
          const rawData = await res.json().catch(() => null);
          if (rawData && Array.isArray(rawData.groups)) {
            const norm = normalizeCaptionTrack(rawData, currentClip.id, currentClip.duration || 40);
            resetHistory(norm);
            setCaptionClipId(currentClip.id);
            setCaptionLoadingState(norm.groups.length > 0 ? 'success' : 'empty');
            setSaveStatus('saved');
            if (norm.groups && norm.groups.length > 0) {
              setSelectedGroupId(norm.groups[0].id);
            }
          } else {
            const emptyTrack = normalizeCaptionTrack({ clip_id: currentClip.id, groups: [] }, currentClip.id, currentClip.duration || 40);
            resetHistory(emptyTrack);
            setCaptionClipId(currentClip.id);
            setCaptionLoadingState('empty');
            setSaveStatus('saved');
          }
        } else if (res.status === 404) {
          // Genuinely empty initial track for fresh clip
          const emptyTrack = normalizeCaptionTrack({ clip_id: currentClip.id, groups: [] }, currentClip.id, currentClip.duration || 40);
          resetHistory(emptyTrack);
          setCaptionClipId(currentClip.id);
          setCaptionLoadingState('empty');
          setSaveStatus('saved');
        } else {
          setCaptionLoadingState('error');
          setCaptionErrorMessage(`HTTP ${res.status}: Failed to load captions`);
        }
      })
      .catch((err) => {
        if ((err as DOMException)?.name !== 'AbortError') {
          console.warn('Captions unavailable for clip:', err);
          setCaptionLoadingState('error');
          setCaptionErrorMessage(err.message || 'Network communication failure');
        }
      });

    // Defer heavy filmstrip frame extraction so active video stream and captions have top network priority
    const reqId = ++filmstripReqIdRef.current;
    if (currentClip.thumbnail_url) {
      const tUrl = currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost);
      setFilmstripFrames([tUrl]);
    } else {
      setFilmstripFrames([]);
    }

    const timer = setTimeout(() => {
      fetch(apiUrl(`/api/clips/${currentClip.id}/filmstrip`, pcHost), { signal: controller.signal })
        .then((res) => (res.ok ? res.json() : null))
        .then((fsData) => {
          if (reqId === filmstripReqIdRef.current && fsData && Array.isArray(fsData.frames) && fsData.frames.length > 0) {
            setFilmstripFrames(fsData.frames.map((f: string) => (f.startsWith('http') ? f : apiUrl(f, pcHost))));
          }
        })
        .catch(() => { });
    }, 700);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [currentClip?.id, currentClip?.range_revision, currentClip?.thumbnail_url, currentClip?.thumbnail_version, pcHost]);

  // Source bounds for mapping local clip time (0 -> duration) to source video time
  const clipStart = currentClip ? (currentClip.start_time || 0) : 0;
  const clipEnd = currentClip ? (currentClip.end_time || (clipStart + (currentClip.duration || 40))) : 40;
  const clipDuration = currentClip ? Math.max(0.1, currentClip.duration || (clipEnd - clipStart)) : 40;
  const timelineSourceDuration = currentClip
    ? (sourceDurationForClip?.clipId === currentClip.id
      ? sourceDurationForClip.seconds
      : (currentClip.source_duration || 0))
    : 0;

  // 3. Playback Controls & Dual-Layer Video Sync with Adaptive Timebase Mapping
  const handleTogglePlay = () => {
    const video = videoRef.current;
    if (!video) return;

    if (video.paused) {
      if (isSourceTimebase) {
        if (video.currentTime >= clipEnd - 0.1 || video.currentTime < clipStart - 0.2) {
          video.currentTime = clipStart;
          if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
        }
      } else {
        if (video.currentTime >= clipDuration - 0.1) {
          video.currentTime = 0;
          if (bgVideoRef.current) bgVideoRef.current.currentTime = 0;
        }
      }
      video.play().then(() => {
        setIsPlaying(true);
        if (bgVideoRef.current) bgVideoRef.current.play().catch(() => { });
      }).catch((err) => {
        console.warn('Playback interrupted:', err);
        setIsPlaying(false);
        setPlaybackFeedback('Playback interrupted. Tap play again.');
        setTimeout(() => setPlaybackFeedback(null), 3500);
      });
    } else {
      video.pause();
      if (bgVideoRef.current) bgVideoRef.current.pause();
      contextPreviewRef.current = null;
      setIsPlaying(false);
    }
  };

  const handleSeek = (targetSec: number) => {
    contextPreviewRef.current = null;
    if (previewAnimRef.current?.active) {
      previewAnimRef.current = null;
    }
    const safeTarget = clamp(targetSec, 0, clipDuration);
    lastTimeUpdateRef.current = safeTarget;
    setCurrentTime(safeTarget);
    const actualTarget = isSourceTimebase ? (clipStart + safeTarget) : safeTarget;
    if (videoRef.current) {
      videoRef.current.currentTime = actualTarget;
    }
    if (bgVideoRef.current && layoutMode === 'fit_blur') {
      bgVideoRef.current.currentTime = actualTarget;
    }
  };

  // Synchronize Fit + Blur background layer on mode toggle, seek, or play/pause transition
  useEffect(() => {
    if (layoutMode === 'fit_blur' && bgVideoRef.current && videoRef.current) {
      bgVideoRef.current.currentTime = videoRef.current.currentTime;
      if (isPlaying) {
        bgVideoRef.current.play().catch(() => {});
      } else {
        bgVideoRef.current.pause();
      }
    }
  }, [layoutMode, isPlaying]);

  const handlePreviewAnimation = (start: number, end: number) => {
    const video = videoRef.current;
    if (!video) return;

    const restoreTime = currentTime;
    previewAnimRef.current = {
      active: true,
      restoreTime,
      endTime: Math.min(clipDuration, end + 0.1),
    };

    handleSeek(start);
    video.play().then(() => {
      setIsPlaying(true);
      if (bgVideoRef.current) bgVideoRef.current.play().catch(() => {});
    }).catch((err) => {
      console.warn('Preview playback failed:', err);
      setIsPlaying(false);
      previewAnimRef.current = null;
      setPlaybackFeedback('Playback blocked. Tap play manually.');
      setTimeout(() => setPlaybackFeedback(null), 3500);
    });
  };

  const handlePreviewContext = (range: { start: number; end: number }) => {
    const video = videoRef.current;
    if (!video || !isSourceTimebase || range.end <= range.start) return;
    const restoreTime = clamp(video.currentTime - clipStart, 0, clipDuration);
    previewAnimRef.current = null;
    contextPreviewRef.current = { sourceEnd: range.end, restoreTime };
    video.currentTime = range.start;
    if (bgVideoRef.current) bgVideoRef.current.currentTime = range.start;
    video.play().then(() => {
      setIsPlaying(true);
      if (bgVideoRef.current) bgVideoRef.current.play().catch(() => {});
    }).catch(() => {
      contextPreviewRef.current = null;
      setIsPlaying(false);
      setPlaybackFeedback('Source preview could not start. Tap play manually.');
      setTimeout(() => setPlaybackFeedback(null), 3500);
    });
  };

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video) return;
    const cur = video.currentTime;

    if (isSourceTimebase && contextPreviewRef.current) {
      const context = contextPreviewRef.current;
      if (cur >= context.sourceEnd) {
        contextPreviewRef.current = null;
        video.pause();
        if (bgVideoRef.current) bgVideoRef.current.pause();
        setIsPlaying(false);
        handleSeek(context.restoreTime);
        return;
      }
      setCurrentTime(clamp(cur - clipStart, 0, clipDuration));
      return;
    }

    if (isSourceTimebase) {
      const localTime = Math.max(0, cur - clipStart);

      // Check if preview animation completed
      if (previewAnimRef.current?.active) {
        if (localTime >= previewAnimRef.current.endTime) {
          const restore = previewAnimRef.current.restoreTime;
          previewAnimRef.current = null;
          video.pause();
          if (bgVideoRef.current) bgVideoRef.current.pause();
          setIsPlaying(false);
          handleSeek(restore);
          return;
        }
      }

      // Source bounds: stop when end of clip is reached
      if (shouldStopSourcePlayback(cur, clipEnd)) {
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

      if (cur < clipStart - 0.2) {
        video.currentTime = clipStart;
        if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
        lastTimeUpdateRef.current = 0;
        setCurrentTime(0);
        return;
      }

      if (Math.abs(localTime - lastTimeUpdateRef.current) >= 0.045 || cur <= clipStart + 0.05) {
        lastTimeUpdateRef.current = localTime;
        setCurrentTime(localTime);
      }

      if (bgVideoRef.current && layoutMode === 'fit_blur') {
        const diff = Math.abs(bgVideoRef.current.currentTime - cur);
        if (diff > 0.15) {
          bgVideoRef.current.currentTime = cur;
        }
      }
    } else {
      // Check if preview animation completed
      if (previewAnimRef.current?.active) {
        if (cur >= previewAnimRef.current.endTime) {
          const restore = previewAnimRef.current.restoreTime;
          previewAnimRef.current = null;
          video.pause();
          if (bgVideoRef.current) bgVideoRef.current.pause();
          setIsPlaying(false);
          handleSeek(restore);
          return;
        }
      }

      // 0-based timebase for pre-rendered clip files
      if (cur >= clipDuration) {
        video.pause();
        video.currentTime = 0;
        if (bgVideoRef.current) {
          bgVideoRef.current.pause();
          bgVideoRef.current.currentTime = 0;
        }
        setIsPlaying(false);
        lastTimeUpdateRef.current = 0;
        setCurrentTime(0);
        return;
      }

      if (Math.abs(cur - lastTimeUpdateRef.current) >= 0.045 || cur <= 0.05) {
        lastTimeUpdateRef.current = cur;
        setCurrentTime(cur);
      }

      if (bgVideoRef.current && layoutMode === 'fit_blur') {
        const diff = Math.abs(bgVideoRef.current.currentTime - cur);
        if (diff > 0.15) {
          bgVideoRef.current.currentTime = cur;
        }
      }
    }
  };

  // Playback Time Sync Loop (efficient requestVideoFrameCallback with rAF fallback)
  useEffect(() => {
    if (!isPlaying) return;
    let animId: number;
    let rvfcId: number;
    let cancelled = false;
    const v = videoRef.current;

    const tick = () => {
      if (cancelled) return;
      handleTimeUpdate();
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
  }, [isPlaying, isSourceTimebase, clipStart, clipEnd, clipDuration, layoutMode]);

  const handleLoadedMetadata = (isSource?: boolean, mediaDuration?: number) => {
    const isSourceEffective = (typeof isSource === 'boolean') ? isSource : isSourceTimebase;
    setIsSourceTimebase(isSourceEffective);
    const video = videoRef.current;
    const loadedDuration = Number.isFinite(mediaDuration) ? mediaDuration : video?.duration;
    if (currentClip && isSourceEffective && loadedDuration && Number.isFinite(loadedDuration)) {
      setSourceDurationForClip({ clipId: currentClip.id, seconds: loadedDuration });
    }
    if (video) {
      if (isSourceEffective) {
        if (video.duration >= clipStart && clipStart > 0) {
          video.currentTime = clipStart;
          if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
        } else {
          video.currentTime = 0;
          if (bgVideoRef.current) bgVideoRef.current.currentTime = 0;
        }
      } else {
        video.currentTime = 0;
        if (bgVideoRef.current) bgVideoRef.current.currentTime = 0;
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

    // 1. INSTANT 0ms Optimistic In-Memory Update:
    setReadyClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );
    setApprovedClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );

    // 2. Non-blocking asynchronous persistence to server
    try {
      fetch(apiUrl(`/api/clips/${currentClip.id}/layout`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ layout_mode: newMode }),
      }).then(async (res) => {
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
        }
      }).catch((e) => {
        console.warn('Failed to persist layout framing mode:', e);
      });
    } catch (e) {
      console.warn('Failed to persist layout framing mode:', e);
    }
  };

  // 4. Update Caption Group with Transient Preview vs Commit Support
  const handleUpdateGroup = (
    groupId: string,
    patch: Partial<CaptionGroup>,
    commitToHistory: boolean = true
  ) => {
    if (!captionTrack) return;
    const updatedTrack = {
      ...captionTrack,
      groups: captionTrack.groups.map((grp) => {
        if (grp.id !== groupId) return grp;
        const next: CaptionGroup = { ...grp, ...patch };
        if (patch.layout) next.layout = { ...(grp.layout || captionTrack.default_layout), ...patch.layout };
        if (patch.style) next.style = { ...(grp.style || captionTrack.default_style), ...patch.style };
        if (patch.animation) next.animation = { ...(grp.animation || captionTrack.default_animation), ...patch.animation };
        return next;
      }),
    };
    setCaptionTrack(updatedTrack, commitToHistory);
    setSaveStatus('unsaved');
  };

  // Explicit / Auto Save Captions to Server
  const handleSaveCaptions = async (): Promise<boolean> => {
    if (!currentClip) return false;
    if (!captionTrack) return true;
    if (captionTrack.clip_id && captionTrack.clip_id !== currentClip.id) {
      console.error(`Refusing to save caption track: clip_id mismatch (${captionTrack.clip_id} !== ${currentClip.id})`);
      setSaveStatus('error');
      setFeedback('Error: Caption track mismatch. Refusing to overwrite another clip.');
      return false;
    }
    setSaveStatus('saving');
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(captionTrack),
      });
      if (res.ok) {
        setSaveStatus('saved');
        setFeedback('Captions saved successfully.');
        setTimeout(() => setFeedback(null), 2500);
        return true;
      } else {
        throw new Error(`Server returned HTTP ${res.status}`);
      }
    } catch (e) {
      setSaveStatus('error');
      setFeedback('Failed to save captions to server. Click Retry.');
      return false;
    }
  };

  const handleTrimChange = async (sourceStart: number, sourceEnd: number): Promise<boolean> => {
    if (!currentClip) return false;
    if (!(timelineSourceDuration > 0)) {
      setFeedback('Source duration is not verified yet. Range changes are disabled.');
      return false;
    }
    if (!(await handleSaveCaptions())) return false;
    const newStart = Number(sourceStart.toFixed(2));
    const newEnd = Number(sourceEnd.toFixed(2));
    videoRef.current?.pause();
    bgVideoRef.current?.pause();
    setIsPlaying(false);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/trim`, pcHost), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ start_time: newStart, end_time: newEnd, range_revision: currentClip.range_revision || 0 }),
      });
      if (res.ok) {
        const data = await res.json();
        setReadyClips((prev) =>
          prev.map((c) =>
            c.id === currentClip.id
              ? {
                  ...c,
                  start_time: data.start_time,
                  end_time: data.end_time,
                  duration: data.duration,
                  padded_start: data.padded_start,
                  padded_end: data.padded_end,
                  range_revision: data.range_revision,
                  source_duration: data.source_duration || c.source_duration,
                  is_render_stale: data.is_render_stale,
                }
              : c
          )
        );
        setApprovedClips((prev) =>
          prev.map((c) =>
            c.id === currentClip.id
              ? {
                  ...c,
                  start_time: data.start_time,
                  end_time: data.end_time,
                  duration: data.duration,
                  padded_start: data.padded_start,
                  padded_end: data.padded_end,
                  range_revision: data.range_revision,
                  source_duration: data.source_duration || c.source_duration,
                  is_render_stale: data.is_render_stale,
                }
              : c
          )
        );
        if (isSourceTimebase && videoRef.current) {
          videoRef.current.currentTime = data.start_time;
          if (bgVideoRef.current && layoutMode === 'fit_blur') bgVideoRef.current.currentTime = data.start_time;
        }
        if (isSourceTimebase) {
          lastTimeUpdateRef.current = 0;
          setCurrentTime(0);
          setDuration(data.duration);
        }
        return true;
      } else {
        const err = await res.json().catch(() => null);
        setFeedback(err?.detail || `Failed to save trim (HTTP ${res.status}). The previous range is restored.`);
        return false;
      }
    } catch (e) {
      console.error('Failed to update trim', e);
      setFeedback('Failed to save trim. Check the connection and retry; the previous range is restored.');
      return false;
    }
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

  const handleApplyLayoutToAll = (groupId: string) => {
    if (!visibleCaptionTrack) return;
    const { nextTrack, affectedCount } = applySectionStylesToTrack(
      visibleCaptionTrack,
      groupId,
      'layout'
    );
    // Commit as a single atomic history step for undo/redo
    setCaptionTrack(nextTrack, true);
    setSaveStatus('unsaved');
    setFeedback(`Applied position & size to all captions (${affectedCount})`);
    setTimeout(() => {
      setFeedback(null);
    }, 2200);
  };

  // 5. Approve & Publish Handler
  const handleApprove = async () => {
    if (!currentClip) return;
    if (!titleDraft.trim()) {
      setFeedback('Clip title cannot be empty.');
      return;
    }
    setIsSubmitting(true);
    setFeedback(null);

    try {
      if (captionTrack) {
        if (captionTrack.clip_id && captionTrack.clip_id !== currentClip.id) {
          throw new Error(`Refusing to approve: caption track (${captionTrack.clip_id}) belongs to another clip (${currentClip.id})`);
        }
        const capRes = await fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify(captionTrack),
        });
        if (!capRes.ok) {
          throw new Error('Failed to save caption track before approval');
        }
      }

      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/approve`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          title: titleDraft.trim(),
          hashtags: captionDraft.trim(),
          publish_mode: publishMode,
          platforms: selectedPlatforms.map((p) => p === 'youtube_shorts' ? 'youtube' : p).join(','),
          account_ids: selectedAccountMap,
          layout_mode: layoutMode,
          range_revision: currentClip.range_revision || 0,
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
        publish_mode: publishMode,
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

  // 6. Double Confirmation Reject Handlers
  const handlePromptReject = (clip: ClipItem) => {
    setIsMoreModalOpen(false);
    setClipToReject(clip);
  };

  const handleConfirmReject = async () => {
    if (!clipToReject) return;
    setIsSubmitting(true);
    setFeedback(null);

    try {
      const res = await fetch(apiUrl(`/api/clips/${clipToReject.id}/reject`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ reason: 'Rejected from review studio' }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Reject request failed');
      }

      setReadyClips((prev) => prev.filter((c) => c.id !== clipToReject.id));
      setApprovedClips((prev) => prev.filter((c) => c.id !== clipToReject.id));
      if (selectedClipId === clipToReject.id) {
        setViewMode('library');
        setSelectedClipId(null);
      }
      setClipToReject(null);
      setFeedback('Clip permanently rejected and media deleted.');
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
    let list = allClips;
    if (filterStatus === 'ready') list = readyClips;
    else if (filterStatus === 'approved') list = approvedClips;

    if (sessionFilter) {
      list = list.filter((c) => c.session_id === sessionFilter || c.chunk_id === sessionFilter);
    }
    return list;
  }, [filterStatus, readyClips, approvedClips, allClips, sessionFilter]);

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
    <div className="w-full h-full flex flex-col bg-canvas overflow-hidden">
      {/* HEADER — title + metadata, actions */}
      <header className="shrink-0 px-4 pt-4 pb-3 border-b border-border bg-canvas z-20">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tight text-text-main leading-tight">
              Clips
            </h1>
            <p className="text-xs text-text-muted mt-0.5 tabular-nums truncate flex items-center gap-1.5">
              {isCloudMode ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                  <span>Cloud Mode (Remote)</span>
                </>
              ) : (isPcOnline ?? !connectionError) ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-status-success" />
                  <span>Studio Online (LAN)</span>
                </>
              ) : (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-text-muted" />
                  <span>Offline</span>
                </>
              )}
              <span>&middot;</span>
              <span>{readyClips.length} ready</span>
              <span>&middot;</span>
              <span>{allClips.length} total</span>
            </p>
          </div>
          <div className="flex items-center gap-1 -mr-2">
            <button
              type="button"
              onClick={() => setShowSubmitModal(true)}
              aria-label="Process YouTube Video"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
              title="Process YouTube Video in Cloud"
            >
              <Plus size={19} />
            </button>
            <button
              type="button"
              onClick={fetchClips}
              disabled={isLoading}
              aria-label="Refresh clips"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            >
              <RefreshCw size={18} className={isLoading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {/* FILTER CHIPS — uniform pills, YouTube selected-chip inversion */}
        <div className="mt-3 flex items-center gap-2 overflow-x-auto no-scrollbar">
          {[
            { id: 'all' as FilterStatus, label: 'All', count: allClips.length },
            { id: 'ready' as FilterStatus, label: 'Ready', count: readyClips.length },
            { id: 'approved' as FilterStatus, label: 'Uploaded', count: approvedClips.length },
          ].map((chip) => {
            const isSelected = filterStatus === chip.id;
            return (
              <button
                key={chip.id}
                type="button"
                onClick={() => setFilterStatus(chip.id)}
                aria-pressed={isSelected}
                className={`h-8 shrink-0 px-3 rounded-lg text-[13px] font-medium flex items-center gap-1.5 active:scale-[0.98] transition-all ${
                  isSelected
                    ? 'chip-selected'
                    : 'bg-surface-2 text-text-secondary hover:text-text-main'
                }`}
              >
                <span>{chip.label}</span>
                <span className="tabular-nums text-xs opacity-70">{chip.count}</span>
              </button>
            );
          })}
        </div>
      </header>

      {/* REMOTE CLOUD JOBS IN PROGRESS */}
      {remoteJobs.filter((j) => j.status === 'queued' || j.status === 'claimed').length > 0 && (
        <div className="shrink-0 px-4 py-2 bg-primary-soft border-b border-primary/20 flex items-center justify-between gap-3">
          <p className="text-xs text-primary tabular-nums truncate">
            <span className="font-semibold">{remoteJobs.filter((j) => j.status === 'queued' || j.status === 'claimed').length} cloud job(s) in progress</span>
            {' '}&middot; analyzing highlights and speech
          </p>
          <span className="shrink-0 text-xs font-mono text-primary font-medium">Processing</span>
        </div>
      )}

      {/* BACKLOG — flat notice line, not a card */}
      {queueStats && queueStats.queued_count > 0 && (
        <div className="shrink-0 px-4 py-2 bg-surface-2 border-b border-border flex items-center justify-between gap-3">
          <p className="text-xs text-text-secondary tabular-nums truncate">
            <span className="text-status-warning font-medium">{queueStats.queued_count} queued</span>
            {' '}&middot; daily quota {queueStats.daily_quota_used}/{queueStats.daily_quota_max} &middot; auto-drains at midnight UTC
          </p>
          <span className="shrink-0 text-xs font-medium text-status-warning">Backlog</span>
        </div>
      )}

      {/* SESSION FILTER — flat strip */}
      {sessionFilter && (
        <div className="shrink-0 px-4 py-2 bg-primary-soft border-b border-primary/25 flex items-center justify-between gap-3">
          <span className="text-xs text-primary truncate">
            Session {sessionFilter.replace('sess_', '')} &middot; {filteredClips.length}&nbsp;{filteredClips.length === 1 ? 'clip' : 'clips'}
          </span>
          {onClearSessionFilter && (
            <button
              type="button"
              onClick={onClearSessionFilter}
              className="shrink-0 min-h-[32px] px-3 rounded-lg bg-surface-2 text-xs font-medium text-text-secondary hover:text-text-main flex items-center gap-1 active:scale-[0.98] transition-all"
            >
              <X size={12} />
              Show all
            </button>
          )}
        </div>
      )}

      {/* FEEDBACK — flat strip */}
      {feedback && (
        <div className="shrink-0 px-4 py-2 bg-surface-2 border-b border-border flex items-center justify-between gap-3">
          <span className="text-xs text-text-secondary truncate">{feedback}</span>
          <button type="button" onClick={() => setFeedback(null)} aria-label="Dismiss" className="text-text-muted hover:text-text-main">
            <X size={14} />
          </button>
        </div>
      )}

      {/* CLIP LIST — flat rows on canvas, separated by dividers (no cards) */}
      <main className="flex-1 min-h-0 overflow-y-auto pb-24">
        {isLoading && allClips.length === 0 ? (
          <div className="px-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="py-3 flex gap-3 animate-pulse">
                <div className="w-[132px] aspect-video rounded-lg bg-surface-2 shrink-0" />
                <div className="flex-1 space-y-2 py-0.5">
                  <div className="h-4 bg-surface-2 rounded w-11/12" />
                  <div className="h-3 bg-surface-2 rounded w-1/2" />
                  <div className="h-3 bg-surface-2 rounded w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : filteredClips.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center select-none my-12">
            <div className="w-16 h-16 rounded-full bg-surface-2 flex items-center justify-center mb-4 text-text-muted">
              {isCloudMode ? <Cloud size={24} className="text-primary" /> : <Film size={24} />}
            </div>
            <h2 className="text-base font-semibold tracking-tight text-text-main">
              {isCloudMode ? 'Cloud Mode Active' : 'No clips yet'}
            </h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-[280px]">
              {isCloudMode
                ? 'Your app is connected to Dispatch Cloud. Submit a YouTube video to process clips from anywhere.'
                : 'Dispatch will show generated clips here after processing a recording session.'}
            </p>
            <div className="mt-5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => setShowSubmitModal(true)}
                className="min-h-[44px] px-5 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold active:scale-[0.98] transition-all flex items-center gap-2 shadow-hero-glow"
              >
                <Plus size={15} />
                <span>Process Video</span>
              </button>
              <button
                type="button"
                onClick={fetchClips}
                className="min-h-[44px] px-4 rounded-xl bg-surface-2 text-sm font-medium text-text-main hover:bg-surface-3 active:scale-[0.98] transition-all flex items-center gap-2"
              >
                <RefreshCw size={15} className={isLoading ? 'animate-spin' : ''} />
                <span>Refresh</span>
              </button>
            </div>
          </div>
        ) : (
          groupedSessions.map(([sessionId, clipsInSession]) => (
            <section key={sessionId}>
              {/* Session section header — plain text, no card */}
              <div className="px-4 pt-4 pb-1 flex items-center justify-between">
                <span className="text-xs font-medium uppercase tracking-wider text-text-muted">
                  Session {sessionId.replace('sess_', '')}
                </span>
                <span className="text-xs text-text-muted tabular-nums">
                  {clipsInSession.length}&nbsp;{clipsInSession.length === 1 ? 'clip' : 'clips'}
                </span>
              </div>

              <div className="px-4">
                {clipsInSession.map((clip) => {
                  const isUploaded = clip.status === 'approved' || clip.status === 'published';
                  const youtubeUrl = clip.remote_url || (clip.remote_id ? 'https://youtu.be/' + clip.remote_id : null);
                  const daysLeft = getRetentionDaysLeft(clip.published_at);
                  const age = formatAge(clip.created_at);

                  return (
                    <div
                      key={clip.id}
                      onClick={() => {
                        if (clip.is_cloud_only && youtubeUrl) {
                          window.open(youtubeUrl, '_blank', 'noopener,noreferrer');
                          return;
                        }
                        setSelectedClipId(clip.id);
                        setViewMode('detail');
                      }}
                      className="group w-full py-3 flex items-start gap-3 cursor-pointer active:bg-surface-2 transition-colors"
                    >
                      {/* 16:9 thumbnail — uniform aspect for every clip (Studio pattern) */}
                      <div className="relative w-[132px] aspect-video rounded-lg bg-black overflow-hidden shrink-0 flex items-center justify-center">
                        <Film size={16} className="text-white/40" />
                        {clip.thumbnail_url && (
                          <img
                            src={clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost)}
                            alt=""
                            className="absolute inset-0 w-full h-full object-cover"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = 'none';
                            }}
                          />
                        )}
                        {clip.is_cloud_only && (
                          <span className="absolute top-1 left-1 px-1 py-0.5 rounded bg-black/85 text-white text-[9px] font-medium flex items-center gap-0.5">
                            <Cloud size={8} />
                            Cloud
                          </span>
                        )}
                        {/* Duration badge — black bg, white text, both themes */}
                        <span className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/85 font-mono tabular-nums text-[10px] font-semibold text-white">
                          {formatDuration(clip.duration)}
                        </span>
                      </div>

                      {/* Title + metadata — Studio row anatomy */}
                      <div className="flex-1 min-w-0">
                        <h3 className="text-sm font-semibold text-text-main leading-snug line-clamp-2">
                          {clip.title || clip.hook || 'Untitled Clip'}
                        </h3>
                        <p className="text-xs text-text-muted mt-1 tabular-nums truncate">
                          {typeof clip.virality_score === 'number' && clip.virality_score > 0 && (
                            <>{clip.virality_score}% &middot; </>
                          )}
                          {age || (clip.session_id ? clip.session_id.replace('sess_', '#') : 'Direct')}
                          {isUploaded && !clip.is_cloud_only && <> &middot; {daysLeft}d left</>}
                          {isUploaded && clip.is_cloud_only && <> &middot; Cloud only</>}
                        </p>
                      </div>

                      {/* Kebab — circular ghost, aligned to the 16px margin */}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedClipId(clip.id);
                          setIsMoreModalOpen(true);
                        }}
                        aria-label="More options"
                        className="-mr-2 w-11 h-11 shrink-0 rounded-full flex items-center justify-center text-text-muted hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
                      >
                        <MoreVertical size={18} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          ))
        )}
      </main>

      {/* SUBMIT YOUTUBE VIDEO MODAL (Cloud Processing on any connection) */}
      {showSubmitModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl bg-surface border border-border p-5 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold text-text-main flex items-center gap-2">
                <Cloud size={18} className="text-primary" />
                Process Video in Cloud
              </h3>
              <button
                type="button"
                onClick={() => setShowSubmitModal(false)}
                className="w-8 h-8 rounded-full flex items-center justify-center text-text-muted hover:text-text-main"
              >
                <X size={16} />
              </button>
            </div>
            <p className="text-xs text-text-secondary leading-relaxed">
              Paste a YouTube video link. Dispatch Cloud will extract speech, select best highlights, and render ready-to-publish short clips.
            </p>
            <input
              type="url"
              value={submitUrlInput}
              onChange={(e) => setSubmitUrlInput(e.target.value)}
              placeholder="https://youtube.com/watch?v=..."
              className="w-full min-h-[44px] px-3.5 rounded-xl bg-surface-2 border border-border text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary"
            />
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowSubmitModal(false)}
                className="min-h-[44px] px-4 rounded-xl bg-surface-2 hover:bg-surface-3 text-sm font-medium text-text-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSubmitRemoteJob}
                disabled={isSubmittingJob || !submitUrlInput.trim()}
                className="min-h-[44px] px-5 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold active:scale-[0.98] transition-all flex items-center gap-2 shadow-hero-glow disabled:opacity-50"
              >
                <Plus size={15} />
                <span>{isSubmittingJob ? 'Submitting…' : 'Submit'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  // =========================================================================
  // VIEW 2: CLIP DETAIL SCREEN (Bridge: Player + Actions: [Edit] [Publish])
  // =========================================================================
  const renderDetailView = () => {
    if (!currentClip) return null;

    const progressPct = Math.min(100, Math.max(0, (currentTime / Math.max(1, duration || currentClip.duration || 40)) * 100));

    return (
      <div className="w-full h-full flex flex-col bg-canvas overflow-hidden">
        {/* TOP BAR — 56px, ghost circular buttons */}
        <header className="shrink-0 h-14 px-2 border-b border-border bg-canvas z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1 min-w-0">
            <button
              type="button"
              onClick={() => {
                setViewMode('library');
                setSelectedClipId(null);
              }}
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
              title="Back to Clips Library"
              aria-label="Back"
            >
              <ChevronLeft size={20} />
            </button>
            <div className="min-w-0">
              <h2 className="text-base font-semibold tracking-tight text-text-main truncate leading-tight">
                {currentClip.title || 'Clip Detail'}
              </h2>
              <div className="text-xs text-text-muted truncate tabular-nums">
                {currentClip.session_id ? 'Session ' + currentClip.session_id.replace('sess_', '') : 'Dispatch review'}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setIsMoreModalOpen(true)}
            className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
            title="Options"
            aria-label="Options"
          >
            <MoreVertical size={18} />
          </button>
        </header>

        {/* DETAIL MAIN VIEWPORT: Responsive preview with stable visual center */}
        <main className="flex-1 min-h-0 flex flex-col px-4 pt-2 pb-3 gap-3 overflow-hidden">
          {/* 1. CLEAN VIDEO PLAYER WITH DYNAMIC RESPONSIVE FRAMING: Stable center stage */}
          <div className="flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden py-1">
            <FramedVideoStage
              sourceVideoUrl={apiUrl(currentClip.source_video_url || `/api/clips/${currentClip.id}/source`, pcHost)}
              videoUrl={currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined}
              poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
              preferRendered={false}
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
              {isSourceTimebase && (
                <DispatchCaptionRenderer
                  track={visibleCaptionTrack}
                  currentTime={currentTime}
                  layoutMode={layoutMode}
                  showBoundingBox={false}
                />
              )}
            </FramedVideoStage>
          </div>

          {/* 2. BOTTOM CONTROLS DOCK: Transport + Action Buttons + Framing Options */}
          <div className="w-full shrink-0 space-y-2">
            {/* Cloud Only 7-Day Retention Notice */}
            {currentClip.is_cloud_only && (
              <div className="w-full px-3 py-2 rounded-xl chip-info border flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Cloud size={14} className="shrink-0" />
                  <span className="text-xs truncate font-mono">
                    Local MP4 deleted after 7d retention. Live on YouTube.
                  </span>
                </div>
                {(currentClip.remote_url || currentClip.remote_id) && (
                  <button
                    type="button"
                    onClick={() => {
                      const url = currentClip.remote_url || `https://youtu.be/${currentClip.remote_id}`;
                      window.open(url, '_blank', 'noopener,noreferrer');
                    }}
                    className="min-h-[32px] px-2.5 rounded-lg bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center gap-1 shrink-0 active:scale-95 transition-all"
                  >
                    <ExternalLink size={11} />
                    <span>Watch</span>
                  </button>
                )}
              </div>
            )}

            {/* Playback transport */}
            <section className="w-full h-11 flex items-center gap-3">
              <button
                type="button"
                onClick={handleTogglePlay}
                className="w-10 h-10 rounded-full bg-surface-2 hover:bg-surface-3 text-text-main flex items-center justify-center active:scale-[0.95] shrink-0 transition-colors"
                title={isPlaying ? 'Pause' : 'Play'}
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? <Pause size={16} /> : <Play size={16} fill="currentColor" className="ml-0.5" />}
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
                    '--track-bg': `linear-gradient(to right, var(--ds-accent) ${progressPct}%, var(--ds-surface-3) ${progressPct}%)`,
                  } as React.CSSProperties}
                  aria-label="Seek video playback"
                />
              </div>

              {/* Timecode display */}
              <div className="font-mono tabular-nums text-xs font-semibold text-text-main flex items-center gap-1 shrink-0 select-none">
                <span className="text-text-main">{formatTimeCode(currentTime)}</span>
                <span className="text-text-muted">/</span>
                <span className="text-text-secondary">{formatTimeCode(duration || currentClip.duration || 0)}</span>
              </div>
            </section>

            {/* Primary actions — pill pair, one hero (publish) */}
            <section className="w-full grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setViewMode('studio')}
                className="min-h-[44px] rounded-xl bg-surface-2 hover:bg-surface-3 text-sm font-semibold text-text-main flex items-center justify-center gap-2 active:scale-[0.98] transition-all"
              >
                <Scissors size={15} className="text-primary" />
                <span>Edit</span>
              </button>

              {/* Publish or Watch Live on YouTube */}
              {(currentClip.status === 'approved' || currentClip.status === 'published') && (currentClip.remote_url || currentClip.remote_id) ? (
                <button
                  type="button"
                  onClick={() => {
                    const url = currentClip.remote_url || `https://youtu.be/${currentClip.remote_id}`;
                    window.open(url, '_blank', 'noopener,noreferrer');
                  }}
                  className="min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-1.5 shadow-hero-glow active:scale-[0.98] transition-all"
                >
                  <ExternalLink size={14} />
                  <span>Watch on YouTube</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setViewMode('publish')}
                  className="min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-1.5 shadow-hero-glow active:scale-[0.98] transition-all"
                >
                  <Send size={14} />
                  <span>Publish</span>
                  <span className="text-xs opacity-80 font-mono capitalize">({publishMode})</span>
                </button>
              )}
            </section>

            {/* Layout framing — flat section, single divider (no card) */}
            {(() => {
              const availableModes = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
                ? currentClip.available_layout_modes
                : (currentClip.source_orientation === 'landscape' ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES);

              const activeModeObj = availableModes.find((m) => m.id === layoutMode) || availableModes[0];

              return (
                <section className="w-full pt-3 mt-1 border-t border-border">
                  <div className="flex items-center justify-between text-xs leading-tight mb-2">
                    <span className="font-medium uppercase tracking-wider text-text-muted">Layout framing</span>
                    <span className="font-mono text-xs text-primary font-semibold tabular-nums">
                      {activeModeObj ? `${activeModeObj.target_aspect} ${activeModeObj.label}` : layoutMode}
                    </span>
                  </div>
                  <div className="flex gap-2 overflow-x-auto no-scrollbar">
                    {availableModes.map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => handleLayoutChange(opt.id)}
                        className={`h-8 px-3 rounded-lg text-[13px] font-medium whitespace-nowrap active:scale-[0.98] transition-all flex-1 min-w-[64px] ${layoutMode === opt.id
                          ? 'chip-selected font-semibold'
                          : 'bg-surface-2 text-text-secondary hover:text-text-main'
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
    if (!currentClip) {
      return (
        <div className="w-full h-full flex flex-col items-center justify-center bg-[#0D1014] text-text-main p-6 select-none">
          <div className="w-12 h-12 rounded-2xl bg-surface-200 border border-border flex items-center justify-center mb-3 text-text-muted">
            <Film size={24} />
          </div>
          <h3 className="text-sm font-semibold text-text-main mb-1">Clip Unavailable</h3>
          <p className="text-xs text-text-secondary text-center max-w-xs mb-4">
            The requested clip was not found or has been unloaded from the library.
          </p>
          <button
            type="button"
            onClick={() => {
              setViewMode('library');
              setSelectedClipId(null);
              setSelectedGroupId(null);
            }}
            className="h-8 px-4 rounded-lg bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center gap-1.5 active:scale-95 transition-all shadow-hero-sm"
          >
            <ChevronLeft size={14} />
            <span>Return to Library</span>
          </button>
        </div>
      );
    }

    return (
      <div className="w-full h-full flex flex-col bg-[#0D1014] text-text-main overflow-hidden select-none relative">
        {/* 1. COMPACT TOP TOOLBAR (Back · Title · Done) */}
        <header className="shrink-0 h-10 px-3 border-b border-[#232832] bg-canvas z-20 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={async () => {
                if (saveStatus === 'unsaved') {
                  const saveFirst = window.confirm('You have unsaved changes. Save before exiting?');
                  if (saveFirst) {
                    const saved = await handleSaveCaptions();
                    if (!saved) return;
                  }
                }
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

          <div className="flex items-center gap-1.5 shrink-0">
            {saveStatus === 'error' && (
              <button
                type="button"
                onClick={() => handleSaveCaptions()}
                className="h-6 px-2 rounded-md bg-red-500/20 text-red-300 border border-red-500/40 text-[10px] font-semibold flex items-center gap-1 active:scale-95 hover:bg-red-500/30"
                title="Save failed. Click to retry"
              >
                <RotateCw size={10} />
                <span>Retry Save</span>
              </button>
            )}
            <button
              type="button"
              disabled={saveStatus === 'saving'}
              onClick={async () => {
                if (saveStatus === 'unsaved' || saveStatus === 'error') {
                  const success = await handleSaveCaptions();
                  if (!success) {
                    return; // Retain on screen if save failed
                  }
                }
                setViewMode('detail');
                setSelectedGroupId(null);
                setIsVideoSelected(false);
                setActivePanel('none');
              }}
              className="h-7 px-3 rounded-lg bg-primary text-white text-[11px] font-semibold flex items-center gap-1.5 active:scale-95 transition-all shadow-hero-sm disabled:opacity-75"
              title="Save edits and return to clip detail"
            >
              {saveStatus === 'saving' && <RotateCw size={11} className="animate-spin" />}
              <span>Done</span>
            </button>
          </div>
        </header>

        {/* 2. VIDEO PREVIEW STAGE (Fixed ~38% split, zero resizing during panel changes) */}
        <div
          className="h-[50%] min-h-[200px] max-h-[50%] shrink-0 w-full flex items-center justify-center p-1 bg-black relative overflow-hidden"
          onClick={handleClearSelection}
        >
          <FramedVideoStage
            sourceVideoUrl={apiUrl(currentClip.source_video_url || `/api/clips/${currentClip.id}/source`, pcHost)}
            videoUrl={currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined}
            poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
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
            isDetail={true}
            onVideoClick={() => {
              if (selectedGroupId || isVideoSelected) {
                handleClearSelection();
              } else {
                handleTogglePlay();
              }
            }}
          >
            <InteractiveCaptionCanvas
              track={visibleCaptionTrack}
              currentTime={currentTime}
              isPlaying={isPlaying}
              selectedGroupId={selectedGroupId}
              layoutMode={layoutMode}
              onSelectGroup={handleSelectGroup}
              onOpenTextEditor={(id) => {
                setSelectedGroupId(id);
                setIsVideoSelected(false);
                setActivePanel('style');
              }}
              onUpdateGroup={handleUpdateGroup}
              onDeleteGroup={handleDeleteGroup}
              onDuplicateGroup={handleDuplicateGroup}
              onApplyLayoutToAll={handleApplyLayoutToAll}
            />
          </FramedVideoStage>
        </div>

        {/* LOWER WORKSPACE (~2/3 screen split: Transport + Timeline / Tertiary Panel) */}
        <div
          className="flex-1 min-h-0 relative flex flex-col bg-[#121418] overflow-hidden"
          onClick={(e) => {
            const target = e.target as HTMLElement;
            if (target.closest('button, input, textarea, select, [data-layer-block="true"], [data-video-track="true"], [data-handle]')) {
              return;
            }
            if (selectedGroupId || isVideoSelected) {
              handleClearSelection();
            }
          }}
        >
          {/* Toast feedback for playback issues */}
          {playbackFeedback && (
            <div className="absolute top-2 left-1/2 -translate-x-1/2 z-50 px-3 py-1 bg-red-500/90 text-white text-[11px] font-medium rounded-full shadow-lg backdrop-blur-sm animate-in fade-in flex items-center gap-1.5 pointer-events-none">
              <AlertCircle size={12} />
              <span>{playbackFeedback}</span>
            </div>
          )}

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
                {captionLoadingState === 'loading' && !visibleCaptionTrack ? (
                  <div className="flex-1 min-h-[200px] flex flex-col items-center justify-center text-text-muted gap-2">
                    <RotateCw size={22} className="animate-spin text-primary" />
                    <span className="text-xs">Loading captions…</span>
                  </div>
                ) : captionLoadingState === 'error' && !visibleCaptionTrack ? (
                  <div className="flex-1 min-h-[200px] flex flex-col items-center justify-center p-4 text-center">
                    <div className="w-10 h-10 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 flex items-center justify-center mb-2">
                      <AlertCircle size={20} />
                    </div>
                    <p className="text-xs font-semibold text-text-main mb-1">Failed to load captions</p>
                    <p className="text-[11px] text-text-muted mb-3 max-w-xs">{captionErrorMessage || 'HTTP communication failure'}</p>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setCaptionLoadingState('loading');
                          fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost))
                            .then(async (res) => {
                              if (res.ok) {
                                const raw = await res.json().catch(() => null);
                                const norm = normalizeCaptionTrack(raw || { clip_id: currentClip.id, groups: [] }, currentClip.id, currentClip.duration || 40);
                                resetHistory(norm);
                                setCaptionClipId(currentClip.id);
                                setCaptionLoadingState(norm.groups.length > 0 ? 'success' : 'empty');
                              } else {
                                setCaptionLoadingState('error');
                                setCaptionErrorMessage(`HTTP ${res.status}`);
                              }
                            })
                            .catch((err) => {
                              setCaptionLoadingState('error');
                              setCaptionErrorMessage(err.message || 'Network error');
                            });
                        }}
                        className="h-7 px-3 rounded-md bg-surface-200 hover:bg-surface-300 border border-border text-xs font-medium text-text-main flex items-center gap-1 active:scale-95"
                      >
                        <RotateCw size={12} />
                        <span>Retry</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const emptyTrack = normalizeCaptionTrack({ clip_id: currentClip.id, groups: [] }, currentClip.id, currentClip.duration || 40);
                          resetHistory(emptyTrack);
                          setCaptionClipId(currentClip.id);
                          setCaptionLoadingState('empty');
                        }}
                        className="h-7 px-3 rounded-md bg-primary hover:bg-primary-hover text-xs font-medium text-white flex items-center gap-1 active:scale-95"
                      >
                        <span>Start Fresh</span>
                      </button>
                    </div>
                  </div>
                ) : (
                  <CaptionTimeline
                    track={visibleCaptionTrack}
                    currentTime={currentTime}
                    duration={duration || currentClip.duration || 40}
                    sourceDuration={timelineSourceDuration}
                    sourceTimebase={isSourceTimebase}
                    selectedGroupId={selectedGroupId}
                    isVideoSelected={isVideoSelected}
                    isPlaying={isPlaying}
                    filmstripFrames={filmstripFrames}
                    mediaElementRef={videoRef}
                    clipStart={currentClip.start_time}
                    clipEnd={currentClip.end_time}
                    paddedStart={currentClip.padded_start}
                    paddedEnd={currentClip.padded_end}
                    onTrimChange={handleTrimChange}
                    onPreviewContext={handlePreviewContext}
                    onSeek={handleSeek}
                    onSelectGroup={handleSelectGroup}
                    onSelectVideo={() => {
                      setIsVideoSelected(true);
                      setSelectedGroupId(null);
                    }}
                    onUpdateGroup={handleUpdateGroup}
                  />
                )}
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
                  onSelectGroup={handleSelectGroup}
                  onSelectVideo={(sel) => {
                    setIsVideoSelected(sel);
                    if (sel) setSelectedGroupId(null);
                  }}
                  onUpdateTrack={(updated, commit = true) => {
                    setCaptionTrack(updated, commit);
                    setSaveStatus('unsaved');
                  }}
                  onLayoutChange={handleLayoutChange}
                  onSeek={handleSeek}
                  onPreviewAnimation={handlePreviewAnimation}
                  canUndo={canUndo}
                  canRedo={canRedo}
                  onUndo={undo}
                  onRedo={redo}
                />
              </div>
            </>
          ) : (
            /* Tertiary View: Detailed Settings Panel fills lower workspace, zero preview jumping */
            <div className="flex-1 min-h-0 w-full h-full bg-[#121418] z-30 pb-[env(safe-area-inset-bottom,0px)] overflow-hidden">
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
                onSelectGroup={handleSelectGroup}
                onSelectVideo={(sel) => {
                  setIsVideoSelected(sel);
                  if (sel) setSelectedGroupId(null);
                }}
                onUpdateTrack={(updated, commit = true) => {
                  setCaptionTrack(updated, commit);
                  setSaveStatus('unsaved');
                }}
                onLayoutChange={handleLayoutChange}
                onSeek={handleSeek}
                onPreviewAnimation={handlePreviewAnimation}
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
                poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
                preferRendered={false}
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
                {isSourceTimebase && (
                  <DispatchCaptionRenderer
                    track={visibleCaptionTrack}
                    currentTime={currentTime}
                    layoutMode={layoutMode}
                    showBoundingBox={false}
                  />
                )}
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
      <div className="w-full h-full flex flex-col bg-canvas overflow-hidden">
        {/* PUBLISH TOP BAR */}
        <header className="shrink-0 px-3 pt-2.5 pb-2 border-b border-border bg-canvas z-20 flex items-center justify-between gap-2">
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
              <h2 className="text-base font-semibold tracking-tight text-text-main truncate leading-tight">
                Publish Clip
              </h2>
              <div className="text-xs text-text-muted truncate">
                {currentClip.title}
              </div>
            </div>
          </div>
        </header>

        {/* PUBLISH SCROLL MAIN */}
        <main className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-5 pb-28">
          {/* Platform Selector — destination rows with pill toggles (v3 publish sheet) */}
          <section className="space-y-1.5">
            <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
              <span>Destinations &amp; Accounts</span>
              <span className="font-mono text-text-muted">{selectedPlatforms.length} selected</span>
            </div>
            <div className="rounded-2xl bg-surface-100 border border-border divide-y divide-border/60">
              {[
                { id: 'youtube_shorts', label: 'YouTube Shorts', provider: 'youtube', icon: <YouTubeShortsIcon /> },
                { id: 'instagram', label: 'Instagram Reels', provider: 'instagram', icon: <InstagramIcon /> },
                { id: 'linkedin', label: 'LinkedIn Video', provider: 'linkedin', icon: <LinkedInIcon /> },
                { id: 'x', label: 'X / Twitter', provider: 'twitter', icon: <XIcon /> },
              ].map((p) => {
                const isSelected = selectedPlatforms.includes(p.id);
                const platformAccounts = connectedAccounts.filter(a => a.provider === p.provider);
                const hasAccount = platformAccounts.length > 0;
                const chosenAccountId = selectedAccountMap[p.id] || (hasAccount ? platformAccounts[0].account_id : '');
                const chosenAccount = platformAccounts.find(a => a.account_id === chosenAccountId) || platformAccounts[0];

                return (
                  <div key={p.id} className="p-3 space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <button
                        type="button"
                        onClick={() => togglePlatform(p.id)}
                        aria-pressed={isSelected}
                        className={`flex items-center gap-3 min-w-0 text-left text-sm transition-all active:scale-[0.99] flex-1 ${isSelected ? 'text-text-main' : 'text-text-secondary hover:text-text-main'}`}
                      >
                        {p.icon}
                        <div className="min-w-0">
                          <span className="font-medium block truncate">{p.label}</span>
                          {hasAccount ? (
                            <span className="text-xs text-text-muted font-mono block truncate">
                              {chosenAccount?.account_name || 'Connected'}
                            </span>
                          ) : (
                            <span className="text-xs text-status-warning block">
                              Not connected in Settings
                            </span>
                          )}
                        </div>
                      </button>

                      <button
                        type="button"
                        onClick={() => togglePlatform(p.id)}
                        aria-pressed={isSelected}
                        className="shrink-0 p-1"
                      >
                        <span className={`w-11 h-6 rounded-full p-0.5 flex items-center transition-colors duration-150 ${isSelected ? 'bg-primary justify-end' : 'bg-surface-3 border border-border justify-start'}`}>
                          <span className="w-5 h-5 rounded-full bg-white shadow-sm" />
                        </span>
                      </button>
                    </div>

                    {/* Account picker if multiple accounts connected for this provider */}
                    {isSelected && platformAccounts.length > 1 && (
                      <div className="pt-1.5 pl-7 flex items-center gap-1.5 flex-wrap">
                        <span className="text-xs text-text-muted">Account:</span>
                        {platformAccounts.map((acc) => (
                          <button
                            key={acc.id}
                            type="button"
                            onClick={() => setSelectedAccountMap(prev => ({ ...prev, [p.id]: acc.account_id }))}
                            className={`px-2 py-0.5 rounded-lg text-xs font-medium border transition-all ${
                              chosenAccountId === acc.account_id
                                ? 'bg-primary text-white border-primary font-semibold'
                                : 'bg-surface-2 border-border text-text-secondary hover:text-text-main'
                            }`}
                          >
                            {acc.account_name}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          {/* Visibility / Privacy Selector — segmented, selected inverts via accent */}
          <section className="space-y-1.5">
            <div className="flex items-center justify-between text-xs font-semibold text-text-secondary uppercase tracking-wider">
              <span>Visibility</span>
              <span className="font-mono text-primary normal-case tracking-normal font-medium">
                {publishMode === 'private' && 'Only you can view'}
                {publishMode === 'unlisted' && 'Anyone with link can view'}
                {publishMode === 'public' && 'Publicly visible to everyone'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {[
                { id: 'private', label: 'Private', icon: <Lock size={13} />, desc: 'Review safe' },
                { id: 'unlisted', label: 'Unlisted', icon: <LinkIcon size={13} />, desc: 'Share link' },
                { id: 'public', label: 'Public', icon: <Globe size={13} />, desc: 'Live on feed' },
              ].map((m) => {
                const isSelected = publishMode === m.id;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setPublishMode(m.id as 'private' | 'unlisted' | 'public')}
                    aria-pressed={isSelected}
                    className={`min-h-[52px] p-2 rounded-xl border flex flex-col items-center justify-center gap-1 active:scale-[0.98] transition-all ${isSelected
                      ? 'bg-primary border-primary text-white font-semibold'
                      : 'bg-surface-100 border-border text-text-secondary hover:text-text-main'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 text-sm">
                      {m.icon}
                      <span>{m.label}</span>
                    </div>
                    <span className={`text-xs ${isSelected ? 'text-white/80' : 'text-text-muted'}`}>{m.desc}</span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Content Fields */}
          <section className="space-y-2.5">
            <div>
              <label className="text-xs font-semibold text-text-secondary block mb-1">
                Publishing Title
              </label>
              <input
                type="text"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                className="w-full min-h-[44px] rounded-xl bg-surface-100 border border-border px-3 text-sm text-text-main outline-none focus:border-border-strong"
                placeholder="Title for publishing"
              />
            </div>

            <div>
              <label className="text-xs font-semibold text-text-secondary block mb-1">
                Description &amp; #hashtags
              </label>
              <textarea
                value={captionDraft}
                onChange={(e) => setCaptionDraft(e.target.value)}
                rows={4}
                className="w-full rounded-xl bg-surface-100 border border-border p-3 text-sm text-text-main outline-none focus:border-border-strong resize-none"
                placeholder="Description, credits and hashtags"
              />
            </div>
          </section>
        </main>

        {/* STICKY PUBLISH ACTION FOOTER — hero CTA with destination count inside (v3) */}
        <footer className="shrink-0 border-t border-border bg-canvas px-4 py-2.5 z-30 space-y-2">
          {(() => {
            const missing = selectedPlatforms.filter(p => {
              const prov = p === 'youtube_shorts' ? 'youtube' : (p === 'x' ? 'twitter' : p);
              return !connectedAccounts.some(a => a.provider === prov);
            });
            const hasNonYt = selectedPlatforms.some(p => p !== 'youtube_shorts');
            const isDisabled = isSubmitting || selectedPlatforms.length === 0 || missing.length > 0;

            const ctaText = isSubmitting
              ? 'Publishing…'
              : missing.length > 0
                ? `Connect ${missing[0] === 'youtube_shorts' ? 'YouTube' : missing[0]} in Settings to publish`
                : selectedPlatforms.length === 0
                  ? 'Select at least one destination'
                  : hasNonYt
                    ? `Publish to ${selectedPlatforms.length} destinations (YouTube: ${publishMode} · Socials: Public)`
                    : `Publish to YouTube · ${publishMode}`;

            return (
              <>
                {missing.length > 0 && (
                  <div className="p-2 rounded-lg chip-warning border text-xs text-center font-medium">
                    {missing.map(m => m === 'youtube_shorts' ? 'YouTube' : m).join(', ')} account not connected. Please connect in Settings.
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleApprove}
                  disabled={isDisabled}
                  className="w-full min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all disabled:opacity-40"
                >
                  <Send size={15} />
                  <span>{ctaText}</span>
                </button>
              </>
            );
          })()}
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
          className="w-full max-w-sm rounded-t-3xl sm:rounded-2xl bg-canvas border border-border p-4 pb-8 sm:pb-4 space-y-3.5 shadow-2xl max-h-[85vh] overflow-y-auto animate-in slide-in-from-bottom-4 duration-200"
        >
          {/* Mobile Sheet Handle */}
          <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-1 mb-1 sm:hidden" />

          <div className="flex items-center justify-between pb-2 border-b border-border/70">
            <h3 className="text-base font-semibold tracking-tight text-text-main">Clip Options</h3>
            <button
              type="button"
              onClick={() => setIsMoreModalOpen(false)}
              className="min-h-[40px] min-w-[40px] rounded-lg text-text-muted hover:text-text-main flex items-center justify-center active:scale-[0.95]"
            >
              <X size={16} />
            </button>
          </div>

          {copiedNotice && (
            <div className="px-3 py-1.5 rounded-lg bg-primary-soft border border-primary/40 text-xs text-primary flex items-center gap-1.5">
              <Check size={12} />
              <span>{copiedNotice}</span>
            </div>
          )}

          <div className="space-y-1">
            <button
              type="button"
              onClick={() => handleCopy(currentClip.id, 'Clip ID')}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center justify-between text-sm font-medium text-text-main active:scale-[0.98]"
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
                className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center justify-between text-sm font-medium text-text-main active:scale-[0.98]"
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
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-sm font-medium text-text-main active:scale-[0.98]"
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
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-sm font-medium text-text-main active:scale-[0.98]"
            >
              <Share2 size={14} className="text-text-muted" />
              <span>Publish Destinations</span>
            </button>

            <div className="pt-2">
              <button
                type="button"
                onClick={() => handlePromptReject(currentClip)}
                disabled={isSubmitting}
                className="w-full min-h-[44px] px-3 rounded-xl chip-danger border flex items-center justify-center gap-2 text-sm font-semibold hover:opacity-85 active:scale-[0.98] transition-all"
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

  // =========================================================================
  // VIEW 6: DOUBLE CONFIRMATION MODAL FOR REJECT
  // =========================================================================
  const renderRejectConfirmationModal = () => {
    if (!clipToReject) return null;

    return (
      <div
        onClick={() => setClipToReject(null)}
        className="fixed inset-0 z-[110] flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in"
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-sm rounded-t-3xl sm:rounded-2xl bg-canvas border border-border p-5 pb-8 sm:pb-5 space-y-4 shadow-2xl animate-in slide-in-from-bottom-4 duration-200"
        >
          {/* Mobile Sheet Handle */}
          <div className="w-10 h-1 rounded-full bg-surface-300/60 mx-auto -mt-2 mb-2 sm:hidden" />

          <div className="flex items-start gap-3.5">
            <div className="w-11 h-11 rounded-2xl chip-danger border flex items-center justify-center shrink-0">
              <Trash2 size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-base font-semibold tracking-tight text-text-main leading-tight">
                Reject &amp; Delete Clip?
              </h3>
              <p className="text-sm text-text-secondary mt-1 leading-relaxed">
                Are you sure you want to reject <span className="text-text-main font-semibold">&ldquo;{clipToReject.title || clipToReject.hook || 'this clip'}&rdquo;</span>?
              </p>
              <p className="text-xs text-status-danger mt-1.5 leading-snug">
                This will immediately delete the rendered MP4 and thumbnail files from your storage. This action cannot be undone.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2.5 pt-2">
            <button
              type="button"
              onClick={() => setClipToReject(null)}
              disabled={isSubmitting}
              className="min-h-[44px] rounded-xl bg-surface-200 hover:bg-surface-3 border border-border text-text-main text-sm font-semibold flex items-center justify-center active:scale-[0.98] transition-all"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmReject}
              disabled={isSubmitting}
              className="min-h-[44px] rounded-xl bg-danger hover:opacity-90 text-white text-sm font-semibold flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all disabled:opacity-50"
            >
              {isSubmitting ? (
                <RotateCw size={14} className="animate-spin" />
              ) : (
                <Trash2 size={14} />
              )}
              <span>{isSubmitting ? 'Deleting…' : 'Yes, Delete'}</span>
            </button>
          </div>
        </div>
      </div>
    );
  };

  // Main Render Routing
  return (
    <div className="w-full h-full bg-canvas text-text-main flex flex-col overflow-hidden select-none">
      {viewMode === 'library' && renderLibraryView()}
      {viewMode === 'detail' && renderDetailView()}
      {viewMode === 'studio' && renderStudioView()}
      {viewMode === 'publish' && renderPublishView()}
      {renderMoreModal()}
      {renderRejectConfirmationModal()}
    </div>
  );
};
