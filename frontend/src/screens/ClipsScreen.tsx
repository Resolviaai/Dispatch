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
  Bookmark,
  Undo2,
  Redo2,
  X,
  Scissors,
  Send,
  MoreVertical,
  Layers,
  Clock,
  Sliders,
  Camera,
  Youtube,
  Check,
  Copy,
  Trash2,
  AlertCircle,
  Eye,
  Heart,
  MessageCircle,
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
  Bell,
} from 'lucide-react';
import { useActivity } from '../context/ActivityContext';
import { NotificationCenterModal } from '../components/NotificationCenterModal';
import { PullToRefresh } from '../components/PullToRefresh';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig } from '../captions/types';
import { normalizeCaptionTrack } from '../captions/normalizeTrack';
import { CaptionTimeline } from '../captions/CaptionTimeline';
import { CaptionStudio, ActivePanel } from '../captions/CaptionStudio';
import { DispatchCaptionRenderer, applyColorOpacity, applyCaseTransform } from '../captions/DispatchCaptionRenderer';
import { useCaptionHistory } from '../captions/useCaptionHistory';
import { preserveWordTimingsOnEdit } from '../captions/timingPreservation';
import { partitionWordsIntoVisualLines } from '../captions/linePartition';
import { applySectionStylesToTrack } from '../captions/applyToAll';
import { apiUrl, Bridge, getAuthHeaders } from '../bridge';
import { shouldStopSourcePlayback } from '../captions/timelineInteraction';
import { ConnectedAccount, listConnectedAccounts, WORKER_BASE } from '../accounts/accountsService';
import { currentSession } from '../auth/supabase';
import {
  cacheClipThumbnails,
  cachedThumbnailUrls,
  readClipsCache,
  writeClipsCache,
} from '../offline/clipsCache';

const CAPTION_LOAD_TIMEOUT_MS = 10_000;

/**
 * Builds caption groups from the cloud transcript seed (raw window-relative
 * words) so offline cloud editing produces the same grouped structure as
 * PC-side transcription: ~10 words / 6 s per caption, breaking on punctuation.
 */
function buildCloudCaptionGroups(
  words: Array<{ w?: string; text?: string; word?: string; start: number; end: number }>,
  duration: number
): Array<{ id: string; start: number; end: number; words: Array<{ id: string; text: string; start: number; end: number }> }> {
  const clean = words
    .map((w, i) => ({
      id: `cw_${i}`,
      text: String(w.word || w.w || w.text || '').trim(),
      start: Number(w.start),
      end: Number(w.end),
    }))
    .filter((w) => w.text.length > 0 && Number.isFinite(w.start) && Number.isFinite(w.end) && w.end > w.start)
    .sort((a, b) => a.start - b.start);
  if (clean.length === 0) return [];

  const MAX_WORDS = 10;
  const MAX_GAP_S = 1.2;
  const MAX_SPAN_S = 6.0;
  const PUNCTUATION_BREAK = /[.!?;,।]$/;

  const chunks: Array<typeof clean> = [];
  let current: typeof clean = [];
  for (const word of clean) {
    if (word.start >= duration) break; // never render beyond the verified proxy span
    const last = current[current.length - 1];
    const gapTooBig = last && word.start - last.end > MAX_GAP_S;
    const spanTooLong = current[0] && word.end - current[0].start > MAX_SPAN_S;
    const tooManyWords = current.length >= MAX_WORDS;
    if (current.length > 0 && (gapTooBig || spanTooLong || tooManyWords)) {
      chunks.push(current);
      current = [];
    }
    current.push(word);
    if (PUNCTUATION_BREAK.test(word.text)) {
      chunks.push(current);
      current = [];
    }
  }
  if (current.length > 0) chunks.push(current);

  return chunks.map((chunk, i) => ({
    id: `grp_${i}`,
    start: Math.max(0, chunk[0].start),
    end: Math.min(duration, chunk[chunk.length - 1].end),
    words: chunk.map((w) => ({ id: w.id, text: w.text, start: w.start, end: w.end })),
  }));
}

const captionTrackCache = new Map<string, { track: CaptionTrack; revision: number; timestamp: number }>();

async function loadClipCaptionTrack(
  clipId: string,
  duration: number,
  pcHost: string,
  parentSignal: AbortSignal,
  expectedRevision?: number,
): Promise<CaptionTrack> {
  const cached = captionTrackCache.get(clipId);
  if (cached && (expectedRevision === undefined || cached.revision === expectedRevision)) {
    return cached.track;
  }
  const controller = new AbortController();
  let timedOut = false;
  const abortFromParent = () => controller.abort();
  if (parentSignal.aborted) controller.abort();
  else parentSignal.addEventListener('abort', abortFromParent, { once: true });
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, CAPTION_LOAD_TIMEOUT_MS);

  try {
    const res = await fetch(apiUrl(`/api/clips/${clipId}/captions`, pcHost), {
      headers: getAuthHeaders(),
      signal: controller.signal,
    });
    if (res.status === 404) {
      return normalizeCaptionTrack({ clip_id: clipId, groups: [] }, clipId, duration);
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load captions`);

    const rawData = await res.json().catch((error) => {
      if (controller.signal.aborted) throw error;
      return null;
    });
    const normalized = (rawData && Array.isArray(rawData.groups))
      ? normalizeCaptionTrack(rawData, clipId, duration)
      : normalizeCaptionTrack({ clip_id: clipId, groups: [] }, clipId, duration);
    captionTrackCache.set(clipId, { track: normalized, revision: expectedRevision ?? 0, timestamp: Date.now() });
    return normalized;
  } catch (error) {
    if (timedOut) throw new Error('Caption loading timed out. Check the PC connection and retry.');
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    parentSignal.removeEventListener('abort', abortFromParent);
  }
}

export type LayoutMode = 'fit_black' | 'fit_blur' | 'native_916' | 'crop_916' | 'crop_follow' | 'native_169' | 'landscape';
export type ViewMode = 'library' | 'detail' | 'studio' | 'publish';
export type FilterStatus = 'all' | 'ready' | 'approved' | 'rejected';

type RejectedCloudClip = {
  id: string;
  cloud_id?: string;
  title?: string;
  rejected_at?: string | null;
  purge_after?: string | null;
  reason?: string | null;
};

export interface AvailableLayoutMode {
  id: LayoutMode;
  label: string;
  target_aspect: '16:9' | '9:16';
  description?: string;
}

export const DEFAULT_PORTRAIT_MODES: AvailableLayoutMode[] = [
  { id: 'fit_black', label: 'Fit + Black', target_aspect: '16:9', description: '16:9 wide frame with black side bars' },
  { id: 'fit_blur', label: 'Fit + Blur', target_aspect: '16:9', description: '16:9 wide frame with blurred background' },
  { id: 'native_916', label: 'Native 9:16', target_aspect: '9:16', description: '100% full original vertical video (no bars, no crop)' },
];

export const DEFAULT_LANDSCAPE_MODES: AvailableLayoutMode[] = [
  { id: 'crop_916', label: 'Crop 9:16', target_aspect: '9:16', description: 'Full vertical crop to fill 9:16' },
  { id: 'fit_blur', label: 'Fit + Blur', target_aspect: '9:16', description: 'Vertical 9:16 with blurred top/bottom bars' },
  { id: 'fit_black', label: 'Fit + Black', target_aspect: '9:16', description: 'Vertical 9:16 with black top/bottom bars' },
  { id: 'native_169', label: 'Native 16:9', target_aspect: '16:9', description: '100% full original horizontal 16:9 (no bars, no crop)' },
];

export interface ClipItem {  id: string;
  cloud_id?: string;
  session_id?: string | null;
  chunk_id?: string | null;
  start_time: number;
  end_time: number;
  padded_start?: number;
  padded_end?: number;
  source_duration?: number | null;
  range_revision?: number;
  captions_revision?: number;
  ai_suggested_start_time?: number;
  ai_suggested_end_time?: number;
  duration: number;
  title: string;
  hook: string;
  description?: string;
  hashtags?: string;
  virality_score: number;
  layout_mode?: LayoutMode;
  crop_anchor_x?: number;
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
  approved_at?: string;
  published_at?: string;
  remote_url?: string;
  remote_id?: string;
  is_cloud_only?: boolean;
  source_orientation?: 'portrait' | 'landscape';
  source_aspect_ratio?: '9:16' | '16:9';
  available_layout_modes?: AvailableLayoutMode[];
  target_aspect?: '16:9' | '9:16';
  origin?: 'phone' | 'youtube';
  youtube_video_id?: string | null;
  source_title?: string | null;
  caption_data?: any;
  /** Cloud caption payload: an edited track (groups) or a transcript seed (words). */
  captions?: {
    words?: Array<{ w?: string; text?: string; word?: string; start: number; end: number }>;
    groups?: unknown[];
    span_duration?: number;
    [key: string]: unknown;
  };
  /** How the captions payload was sourced (worker: track | window). */
  captions_source?: string;
  /** Presigned edit-window proxy URL (worker fallback player source). */
  edit_window_url?: string;
  /** True when the worker could not resolve ANY playable/presentable media. */
  media_missing?: boolean;
  /** One row per platform destination, with the latest cached stats. */
  publications?: PublicationItem[];
}

export interface PublicationItem {
  id: string;
  platform: string;
  remote_id?: string | null;
  remote_url?: string | null;
  published_at?: string | null;
  publish_mode?: string | null;
  stats?: {
    views: number | null;
    likes: number | null;
    comments: number | null;
  };
  stats_updated_at?: string | null;
  stats_stale?: boolean;
}

function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (value < 10_000) return value.toLocaleString();
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function platformLabel(platform: string): string {
  const labels: Record<string, string> = {
    youtube: 'YouTube',
    youtube_shorts: 'YouTube Shorts',
    instagram: 'Instagram',
    linkedin: 'LinkedIn',
    facebook: 'Facebook',
    twitter: 'X',
    x: 'X',
  };
  return labels[platform.toLowerCase()] || platform;
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
  isActive?: boolean;
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

function parseUtcTimestamp(raw?: string): number {
  if (!raw) return NaN;
  const s = raw.trim();
  if (/^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}/.test(s)) {
    return new Date(s.replace(' ', 'T') + 'Z').getTime();
  }
  if (s.includes('T') && !s.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(s)) {
    return new Date(s + 'Z').getTime();
  }
  return new Date(s).getTime();
}

function formatAge(dateStr?: string): string {
  if (!dateStr) return '';
  const t = parseUtcTimestamp(dateStr);
  if (Number.isNaN(t)) return '';
  const diffMs = Date.now() - t;
  if (diffMs < 60000) return 'just now';
  const mins = Math.floor(diffMs / 60000);
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  return Math.floor(hrs / 24) + 'd ago';
}

function getRetentionDaysLeft(publishedAt?: string): number {
  if (!publishedAt) return 7;
  const pubDate = parseUtcTimestamp(publishedAt);
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
                title="Apply this caption's style, position & size to all spoken captions"
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
                      : word.style_override?.color || style.primary_color || '#FFFFFF';

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

export function InteractiveCaptionCanvas({
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
  fallbackVideoUrl?: string;
  clipStart?: number;
  clipEnd?: number;
  clipDuration?: number;
  layoutMode: LayoutMode;
  cropAnchorX?: number;
  onCropAnchorChange?: (newAnchor: number, isFinal: boolean) => void;
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
  renderedLayoutMode?: string;
  onVideoClick?: () => void;
  muted?: boolean;
  /** Worker-reported: no media (rendered clip, window proxy, thumbnail) resolved. */
  mediaMissing?: boolean;
}

export const FramedVideoStage: React.FC<FramedVideoStageProps> = ({
  sourceVideoUrl,
  videoUrl: _videoUrl,
  fallbackVideoUrl,
  poster,
  layoutMode,
  cropAnchorX = 0.5,
  onCropAnchorChange,
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
  renderedLayoutMode,
  onVideoClick,
  muted = false,
  /** Worker-reported: no media (rendered clip, window proxy, thumbnail) resolved. */
  mediaMissing = false,
}) => {
  const [sourceFailed, setSourceFailed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [fallbackFailed, setFallbackFailed] = useState(false);
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  useEffect(() => {
    setSourceFailed(false);
    setVideoFailed(false);
    setFallbackFailed(false);
  }, [sourceVideoUrl, _videoUrl, fallbackVideoUrl, layoutMode]);

  // Canonical Architecture media resolution:
  // _videoUrl is the unbaked clean slice (native 16:9, no baked-in blur, no burned subtitles).
  // It is the primary interactive media. Falls back to sourceVideoUrl or fallbackVideoUrl if missing.
  const candidateVideoUrl = !videoFailed && _videoUrl
    ? _videoUrl
    : (!sourceFailed && sourceVideoUrl ? sourceVideoUrl : null);

  const activeVideoUrl = candidateVideoUrl || (!fallbackFailed && fallbackVideoUrl ? fallbackVideoUrl : null);

  const isFallbackTimebase = Boolean(fallbackVideoUrl && activeVideoUrl === fallbackVideoUrl && activeVideoUrl !== _videoUrl);
  const isSourceTimebase = isFallbackTimebase || Boolean(sourceVideoUrl && activeVideoUrl === sourceVideoUrl && sourceVideoUrl !== _videoUrl);

  // Authoritative aspect ratio determination: governed by layoutMode, targetAspect, and sourceOrientation
  const isTarget169 = useMemo(() => {
    // 1. Explicit native widescreen layouts ALWAYS render in 16:9 frame
    if (layoutMode === 'native_169' || layoutMode === 'landscape') return true;

    // 2. Vertical short-form layouts (Fit + Blur, Fit + Black, Crop 9:16) ALWAYS render in 9:16 frame
    if (layoutMode === 'native_916' || layoutMode === 'crop_916' || layoutMode === 'crop_follow' || layoutMode === 'fit_blur' || layoutMode === 'fit_black') {
      return false;
    }

    if (targetAspect === '16:9') return true;
    if (targetAspect === '9:16') return false;

    if (sourceOrientation === 'landscape') return false;
    if (sourceOrientation === 'portrait') return true;

    return false;
  }, [layoutMode, targetAspect, sourceOrientation]);

  const isCrop = (layoutMode === 'crop_916' || layoutMode === 'crop_follow');

  // Exact mathematical crop scale based on source video aspect ratio and target ratio
  const calculatedCropScale = useMemo(() => {
    if (!isCrop) return 1;

    const sourceAspect = (isSourceTimebase && videoDimensions.width > 0 && videoDimensions.height > 0)
      ? (videoDimensions.width / videoDimensions.height)
      : (sourceOrientation === 'landscape' ? (16 / 9) : (9 / 16));

    const targetRatio = isTarget169 ? (16 / 9) : (9 / 16);

    // If source and target ratios are already equivalent, no zoom needed
    if (Math.abs(sourceAspect - targetRatio) < 0.01) {
      return 1;
    }

    const scale = Math.max(sourceAspect / targetRatio, targetRatio / sourceAspect);
    return Number(scale.toFixed(4));
  }, [isCrop, isSourceTimebase, videoDimensions.width, videoDimensions.height, sourceOrientation, isTarget169]);

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
  }, [sourceVideoUrl, _videoUrl, preferRendered, layoutMode]);

  const isDraggingAnchor = useRef(false);
  const dragStartX = useRef(0);
  const dragStartAnchor = useRef(cropAnchorX);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isCrop) return;
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    isDraggingAnchor.current = true;
    dragStartX.current = e.clientX;
    dragStartAnchor.current = cropAnchorX;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch (_) {}
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDraggingAnchor.current || !isCrop) return;
    const dx = e.clientX - dragStartX.current;
    const containerW = stageBounds.width || 320;
    const deltaAnchor = -dx / (containerW * 1.3);
    const newAnchor = Math.max(0.0, Math.min(1.0, dragStartAnchor.current + deltaAnchor));
    onCropAnchorChange?.(newAnchor, false);
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDraggingAnchor.current) return;
    isDraggingAnchor.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch (_) {}
    const dx = e.clientX - dragStartX.current;
    const containerW = stageBounds.width || 320;
    const deltaAnchor = -dx / (containerW * 1.3);
    const newAnchor = Math.max(0.0, Math.min(1.0, dragStartAnchor.current + deltaAnchor));
    if (Math.abs(dx) > 5) {
      onCropAnchorChange?.(newAnchor, true);
    } else {
      if (onVideoClick) onVideoClick();
      else onTogglePlay?.();
    }
  };

  const handlePointerCancel = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isDraggingAnchor.current) return;
    isDraggingAnchor.current = false;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch (_) {}
    onCropAnchorChange?.(cropAnchorX, true);
  };

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
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        onClick={(e) => {
          e.stopPropagation();
          if (isCrop) return;
          if (onVideoClick) {
            onVideoClick();
          } else {
            onTogglePlay?.();
          }
        }}
        className="relative bg-black rounded-xl border border-border overflow-hidden shadow-2xl flex items-center justify-center select-none cursor-pointer touch-none"
      >
        {activeVideoUrl ? (
          <>
            {/* Fit + Blur synchronized background video layer: dynamically renders blurred background for 9:16 portrait fit */}
            {layoutMode === 'fit_blur' && (
              <video
                ref={bgVideoRef}
                src={activeVideoUrl}
                muted
                playsInline
                preload="none"
                style={{
                  transform: 'translate3d(0, 0, 0)',
                  willChange: 'transform',
                }}
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

            {/* Primary foreground video layer: pure native framing (Crop 9:16 -> object-cover; Fit -> object-contain) */}
            <video
              ref={videoRef}
              src={activeVideoUrl}
              poster={poster}
              muted={muted}
              preload="metadata"
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
                if (activeVideoUrl === fallbackVideoUrl) {
                  setFallbackFailed(true);
                } else if (_videoUrl && activeVideoUrl === _videoUrl) {
                  setVideoFailed(true);
                } else {
                  setSourceFailed(true);
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
              className={`absolute inset-0 z-10 w-full h-full pointer-events-none will-change-transform ${
                isCrop ? 'object-cover' : 'object-contain object-center'
              }`}
              style={{
                objectPosition: isCrop ? `${Math.round(cropAnchorX * 100)}% center` : 'center center'
              }}
            />
          </>
        ) : (
          <div className="flex flex-col items-center justify-center p-6 text-center text-text-muted z-10">
            <Film size={28} className="mb-2 text-text-muted" />
            <div className="text-[12px] font-semibold text-text-secondary">
              {mediaMissing ? 'Media not yet available' : 'Source video unavailable'}
            </div>
            {mediaMissing && (
              <div className="text-[10px] text-text-muted mt-1 max-w-[220px] leading-relaxed">
                This clip is missing its media on Dispatch Cloud. Reconnect the PC so it can re-stage.
              </div>
            )}
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
  isActive = true,
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
  const optimisticRejectIdsRef = useRef<Set<string>>(new Set());
  const [pendingRejectUndo, setPendingRejectUndo] = useState<{ clipId: string; title: string } | null>(null);
  const cloudRejectWorkingRef = useRef(false);
  const [rejectUndoWorking, setRejectUndoWorking] = useState(false);
  const rejectUndoTimerRef = useRef<number | null>(null);
  const [copiedNotice, setCopiedNotice] = useState<string | null>(null);
  const [isVideoSelected, setIsVideoSelected] = useState<boolean>(false);
  const [isFullscreenPreview, setIsFullscreenPreview] = useState<boolean>(false);
  const [activePanel, setActivePanel] = useState<ActivePanel>('none');
  const [showStudioExitConfirm, setShowStudioExitConfirm] = useState<boolean>(false);
  const lastTimeUpdateRef = useRef<number>(0);

  useEffect(() => {
    onEditorOpenChange?.(viewMode === 'studio' || viewMode === 'publish');
  }, [viewMode, onEditorOpenChange]);

  // Clips State
  const [readyClips, setReadyClips] = useState<ClipItem[]>([]);
  const [approvedClips, setApprovedClips] = useState<ClipItem[]>([]);
  const [rejectedClips, setRejectedClips] = useState<RejectedCloudClip[]>([]);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const mountedOnceRef = useRef(false);
  const [queueStats, setQueueStats] = useState<QueueStats | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  // Global Activity & Notification Center
  const {
    hasActiveActivity,
    openNotificationCenter,
    isNotificationCenterOpen,
    closeNotificationCenter,
    addNotification,
  } = useActivity();

  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [isCloudMode, setIsCloudMode] = useState<boolean>(false);
  const [remoteJobs, setRemoteJobs] = useState<Array<{ id: string; youtube_url: string; status: string; last_error?: string; created_at?: string }>>([]);
  const [showSubmitModal, setShowSubmitModal] = useState<boolean>(false);
  const [submitUrlInput, setSubmitUrlInput] = useState<string>('');
  const [isSubmittingJob, setIsSubmittingJob] = useState<boolean>(false);
  const [showingCachedClips, setShowingCachedClips] = useState<boolean>(false);
  const [clipRefreshFailed, setClipRefreshFailed] = useState<boolean>(false);
  const [cachedAt, setCachedAt] = useState<number | null>(null);
  const [publicationLoad, setPublicationLoad] = useState<{
    clipId: string;
    items: PublicationItem[];
    loading: boolean;
    error: boolean;
  } | null>(null);
  const cacheUserIdRef = useRef<string | null>(null);
  const componentMountedRef = useRef<boolean>(true);
  const cacheHydrationRef = useRef<Promise<void> | null>(null);
  const cacheWriteGenerationRef = useRef<number>(0);
  const hasNetworkSnapshotRef = useRef<boolean>(false);
  const cachedThumbnailUrlsRef = useRef<string[]>([]);
  const thumbnailCacheBusyRef = useRef<boolean>(false);
  const thumbnailCacheAttemptedRef = useRef<Map<string, number>>(new Map());
  const offlineReadOnly = clipRefreshFailed;

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
  const [cropAnchorX, setCropAnchorX] = useState<number>(0.5);
  const [isAutoReframing, setIsAutoReframing] = useState<boolean>(false);
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
            if (accs === null) return; // transient fetch failure: skip preselect this render
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
  const captionRequestControllerRef = useRef<AbortController | null>(null);
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

  useEffect(() => {
    if (!currentClip) {
      setPublicationLoad(null);
      return;
    }
    const isPublished = Boolean(
      currentClip.status === 'published' ||
      currentClip.remote_id ||
      currentClip.remote_url ||
      (currentClip.publications && currentClip.publications.length > 0)
    );
    if (!isPublished) {
      setPublicationLoad(null);
      return;
    }

    const cachedItems = currentClip.publications ?? [];
    if (offlineReadOnly || (!isCloudMode && !pcHost.trim()) || (isCloudMode && !currentClip.cloud_id)) {
      setPublicationLoad({
        clipId: currentClip.id,
        items: cachedItems,
        loading: false,
        error: false,
      });
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 20_000);
    setPublicationLoad({
      clipId: currentClip.id,
      items: cachedItems,
      loading: true,
      error: false,
    });

    const loadPublications = async () => {
      if (isCloudMode) {
        const session = await currentSession();
        const token = session?.access_token
          || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
        if (!token) throw new Error('Sign in to load publication stats');
        const response = await fetch(
          `${WORKER_BASE}/clips/${encodeURIComponent(String(currentClip.cloud_id))}/publications`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
            cache: 'no-store',
          }
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        return Array.isArray(data?.publications) ? data.publications as PublicationItem[] : [];
      }
      const response = await fetch(apiUrl(`/api/clips/${currentClip.id}/publications`, pcHost), {
        headers: getAuthHeaders(),
        signal: controller.signal,
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      return Array.isArray(data?.publications) ? data.publications as PublicationItem[] : [];
    };

    void loadPublications()
      .then((items) => {
        if (cancelled) return;
        setPublicationLoad({
          clipId: currentClip.id,
          items,
          loading: false,
          error: false,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setPublicationLoad({
          clipId: currentClip.id,
          items: cachedItems,
          loading: false,
          error: true,
        });
      })
      .finally(() => window.clearTimeout(timeoutId));

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [currentClip?.id, currentClip?.cloud_id, currentClip?.status, isCloudMode, offlineReadOnly, pcHost]);

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

  // ---------------------------------------------------------------------------
  // Media URL stability cache (loading-glitch fix, audit 2026-10-27).
  // Every 4s poll handed <video>/<img> freshly presigned (rotating) R2 URLs,
  // visibly glitching playback as identical content was swapped mid-buffer.
  // One stable URL string is served per (clip, revision, kind) for ~8 min —
  // inside the presign validity window — so poll refreshes no longer flip src
  // props. Rotations only flow through on a content revision or TTL expiry.
  // ---------------------------------------------------------------------------
  const mediaUrlCacheRef = useRef<Map<string, { url: string; expires: number }>>(new Map());
  const stabilizeMediaUrls = useCallback((clip: ClipItem): ClipItem => {
    const cache = mediaUrlCacheRef.current;
    const now = Date.now();
    if (cache.size > 400) {
      for (const [k, v] of cache) if (v.expires <= now) cache.delete(k);
      if (cache.size > 600) cache.clear();
    }
    const stable = (field: 'video_url' | 'thumbnail_url' | 'edit_window_url' | 'remote_url'): string | undefined => {
      const raw = clip[field];
      if (!raw) return undefined;
      const url = raw.startsWith('http') ? raw : apiUrl(raw, pcHost);
      const key = `${clip.id}|${clip.range_revision ?? 0}|${clip.thumbnail_version ?? 0}|${field}`;
      const hit = cache.get(key);
      if (hit && hit.expires > now) return hit.url; // keep the playing src stable
      cache.set(key, { url, expires: now + 480_000 });
      return url;
    };
    return {
      ...clip,
      video_url: stable('video_url'),
      thumbnail_url: stable('thumbnail_url'),
      edit_window_url: stable('edit_window_url'),
      remote_url: stable('remote_url'),
    };
  }, [pcHost]);

  const clipsFetchBusyRef = useRef(false);
  const clipsFetchPendingRef = useRef(false);
  const clipFetchGenerationRef = useRef(0);
  const cloudFailStreakRef = useRef(0);
  // Optimistic-concurrency map (clipId -> last known cloud caption revision).
  // Seeded when a cloud track loads; every save carries it as base_revision so
  // a phone edit made while this editor was open is refused (409), not clobbered.
  const cloudCaptionsRevisionRef = useRef<Map<string, number>>(new Map());
  // PC-mode caption revision per clip; seeded from the server and advanced only
  // by a successful save, so a stale editor conflicts instead of overwriting.
  const localCaptionsRevisionRef = useRef<Map<string, number>>(new Map());

  const revokeCachedThumbnailUrls = useCallback(() => {
    cachedThumbnailUrlsRef.current.forEach((url) => {
      try { URL.revokeObjectURL(url); } catch {}
    });
    cachedThumbnailUrlsRef.current = [];
  }, []);

  useEffect(() => {
    componentMountedRef.current = true;
    return () => {
      componentMountedRef.current = false;
      revokeCachedThumbnailUrls();
    };
  }, [revokeCachedThumbnailUrls]);

  const resolveActiveCacheUserId = async (): Promise<string | null> => {
    const bridged = Bridge.getCurrentUserId?.();
    if (bridged) return bridged;
    try {
      const session = await currentSession();
      return session?.user?.id ?? null;
    } catch {
      return null;
    }
  };

  const resolveCacheUserId = async (): Promise<string | null> => {
    if (cacheUserIdRef.current) return cacheUserIdRef.current;
    const userId = await resolveActiveCacheUserId();
    cacheUserIdRef.current = userId;
    return userId;
  };

  const cachePostersFromSnapshot = async (userId: string, clips: ClipItem[]) => {
    if (thumbnailCacheBusyRef.current || clips.length === 0) return;
    const absoluteClips = clips.map((clip) => ({
      ...clip,
      thumbnail_url: clip.thumbnail_url
        ? (clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost))
        : undefined,
    }));
    thumbnailCacheBusyRef.current = true;
    try {
      await cacheClipThumbnails(userId, absoluteClips, (clip) => {
        const key = `${clip.id}:${clip.thumbnail_version ?? clip.range_revision ?? 0}`;
        const now = Date.now();
        const attemptedAt = thumbnailCacheAttemptedRef.current.get(key);
        // Failed poster fetches retry later, but not on every 10s poll.
        if (attemptedAt && now - attemptedAt < 5 * 60_000) return false;
        thumbnailCacheAttemptedRef.current.set(key, now);
        return true;
      });
    } catch {
      // Offline thumbnails are best-effort; the clip list is not affected.
    } finally {
      thumbnailCacheBusyRef.current = false;
    }
  };

  const persistClipsSnapshot = (
    fetchUserId: string | null,
    source: 'pc' | 'cloud',
    ready: ClipItem[],
    approved: ClipItem[],
    rejected: unknown[],
    stats: QueueStats | null
  ) => {
    if (!fetchUserId) return;
    const generation = ++cacheWriteGenerationRef.current;
    void (async () => {
      const activeUserId = await resolveActiveCacheUserId();
      if (
        !activeUserId
        || activeUserId !== fetchUserId
        || !componentMountedRef.current
        || generation !== cacheWriteGenerationRef.current
      ) {
        return;
      }
      cacheUserIdRef.current = activeUserId;
      await writeClipsCache(fetchUserId, source, {
        ready,
        recent_approved: approved,
        rejected,
        queue_stats: stats,
      }).catch(() => {});
      if (generation !== cacheWriteGenerationRef.current || !componentMountedRef.current) return;
      await cachePostersFromSnapshot(fetchUserId, [...ready, ...approved]);
    })();
  };

  const ensureClipsCacheHydrated = (): Promise<void> => {
    if (cacheHydrationRef.current) return cacheHydrationRef.current;
    cacheHydrationRef.current = (async () => {
      const userId = await resolveCacheUserId();
      if (!userId) return;
      const snapshot = await readClipsCache(userId);
      if (!snapshot || snapshot.userId !== userId || hasNetworkSnapshotRef.current) return;

      const sanitize = (clip: ClipItem): ClipItem => ({
        ...clip,
        // Presigned media URLs expire. Keeping them in a durable cache creates a
        // dead player; offline detail shows metadata and asks for connectivity.
        video_url: undefined,
        source_video_url: undefined,
        edit_window_url: undefined,
        thumbnail_url: undefined,
      });
      const ready = snapshot.payload.ready.map(sanitize);
      const approved = snapshot.payload.recent_approved.map(sanitize);
      setReadyClips(ready);
      setApprovedClips(approved);
      setRejectedClips(
        Array.isArray(snapshot.payload.rejected)
          ? snapshot.payload.rejected.filter((clip): clip is RejectedCloudClip => Boolean(
              clip && typeof clip === 'object' && typeof (clip as RejectedCloudClip).id === 'string'
            ))
          : []
      );
      setQueueStats(
        snapshot.payload.queue_stats && typeof snapshot.payload.queue_stats === 'object'
          ? snapshot.payload.queue_stats as QueueStats
          : null
      );
      setCachedAt(snapshot.savedAt);
      setShowingCachedClips(true);
      setIsLoading(false);

      void cachedThumbnailUrls(userId, [...ready, ...approved]).then((urls) => {
        if (urls.size === 0) return;
        if (hasNetworkSnapshotRef.current || !componentMountedRef.current) {
          urls.forEach((url) => {
            try { URL.revokeObjectURL(url); } catch {}
          });
          return;
        }
        cachedThumbnailUrlsRef.current.push(...urls.values());
        const attach = (clips: ClipItem[]) => clips.map((clip) => {
          const url = urls.get(clip.id);
          return url ? { ...clip, thumbnail_url: url } : clip;
        });
        setReadyClips((previous) => attach(previous));
        setApprovedClips((previous) => attach(previous));
      }).catch(() => {});
    })().catch(() => {
      // IndexedDB failure must never prevent the normal online path.
    });
    return cacheHydrationRef.current;
  };

  // 1. Fetch Real Clips from PC Backend or Cloud Worker
  const fetchClips = async () => {
    await ensureClipsCacheHydrated();
    // In-flight lock: overlapping poll ticks / handler refreshes coalesce, with
    // one trailing refresh so the latest caller's intent is not dropped.
    if (clipsFetchBusyRef.current) {
      clipsFetchPendingRef.current = true;
      return;
    }
    clipsFetchPendingRef.current = false;
    clipsFetchBusyRef.current = true;
    clipFetchGenerationRef.current += 1;

    let loadedFromPc = false;
    let refreshSucceeded = false;
    const generation = clipFetchGenerationRef.current;
    // Capture the account that authorized this request. A response fetched for
    // A must never be written under B after an account switch.
    const fetchUserId = cacheUserIdRef.current;

    if (pcHost && pcHost.trim()) {
      try {
        const controller = new AbortController();
        // A PC already known to be offline gets a short probe, not the full
        // 8 s timeout before cloud fallback.
        const timeoutId = setTimeout(() => controller.abort(), isPcOnline === false ? 2500 : 8000);
        const res = await fetch(apiUrl('/api/clips', pcHost), {
          headers: getAuthHeaders(),
          signal: controller.signal,
          cache: 'no-store',
        });
        clearTimeout(timeoutId);
        if (res.ok) {
          const data = await res.json();
          const rList = Array.isArray(data.ready) ? data.ready : [];
          const aList = Array.isArray(data.recent_approved) ? data.recent_approved : [];
          const hiddenIds = optimisticRejectIdsRef.current;
          if (clipFetchGenerationRef.current === generation) {
            setReadyClips(rList.map((clip: ClipItem) => stabilizeMediaUrls(clip)).filter((clip: ClipItem) => !hiddenIds.has(clip.id)));
            setApprovedClips(aList.map((clip: ClipItem) => stabilizeMediaUrls(clip)).filter((clip: ClipItem) => !hiddenIds.has(clip.id)));
            setRejectedClips([]);
            if (data.queue_stats) {
              setQueueStats(data.queue_stats);
            }
            cloudFailStreakRef.current = 0;
            setConnectionError(null);
            hasNetworkSnapshotRef.current = true;
            setShowingCachedClips(false);
            setClipRefreshFailed(false);
            revokeCachedThumbnailUrls();
            persistClipsSnapshot(fetchUserId, 'pc', rList, aList, [], data.queue_stats ?? null);
            refreshSucceeded = true;
          }
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
        let cloudOk = false;
        if (token) {
          // Separate timeouts: a slow /jobs/status call can no longer push the
          // media list request past its own deadline (the media list is what
          // the page renders; job status is secondary).
          const clipsController = new AbortController();
          const jobsController = new AbortController();
          const clipsTimeout = window.setTimeout(() => clipsController.abort(), 8000);
          const jobsTimeout = window.setTimeout(() => jobsController.abort(), 5000);
          // The clip list must render independently of optional job status.
          // Jobs are fetched after the list response has been applied.
          const clipsRes = await fetch(`${WORKER_BASE}/clips?limit=100&offset=0`, {
            headers: { 'Authorization': `Bearer ${token}` },
            signal: clipsController.signal,
            cache: 'no-store',
          }).catch(() => null);
          window.clearTimeout(clipsTimeout);

          if (clipsRes && clipsRes.ok) {
            const data = await clipsRes.json();
            const rList = Array.isArray(data.ready) ? data.ready : [];
            const aList = Array.isArray(data.recent_approved) ? data.recent_approved : [];
            const rj = Array.isArray(data.rejected) ? data.rejected : [];
            const hiddenIds = optimisticRejectIdsRef.current;
            if (clipFetchGenerationRef.current === generation) {
              setReadyClips(rList.map((clip: ClipItem) => stabilizeMediaUrls(clip)).filter((clip: ClipItem) => !hiddenIds.has(clip.id)));
              setApprovedClips(aList.map((clip: ClipItem) => stabilizeMediaUrls(clip)).filter((clip: ClipItem) => !hiddenIds.has(clip.id)));
              setRejectedClips(rj.filter((clip: RejectedCloudClip) => !(clip as any).retention_expired));
              if (data.queue_stats) {
                setQueueStats(data.queue_stats);
              }
              hasNetworkSnapshotRef.current = true;
              setShowingCachedClips(false);
              setClipRefreshFailed(false);
              revokeCachedThumbnailUrls();
              persistClipsSnapshot(fetchUserId, 'cloud', rList, aList, rj, data.queue_stats ?? null);
              refreshSucceeded = true;
            }
            cloudOk = true;
          }
          const jobsRes = await fetch(`${WORKER_BASE}/jobs/status`, {
            headers: { 'Authorization': `Bearer ${token}` },
            signal: jobsController.signal,
          }).catch(() => null);
          window.clearTimeout(jobsTimeout);
          if (jobsRes && jobsRes.ok) {
            const jData = await jobsRes.json();
            if (Array.isArray(jData.jobs)) {
              if (clipFetchGenerationRef.current === generation) {
                setRemoteJobs(jData.jobs);
              }
            }
          }
        }
        setIsCloudMode(true);
        if (clipFetchGenerationRef.current === generation) {
          if (cloudOk) {
            cloudFailStreakRef.current = 0;
            setConnectionError(null);
          } else {
            // Honest offline signal: one hiccup is tolerated; a second
            // consecutive failure surfaces the connection error.
            cloudFailStreakRef.current += 1;
            if (cloudFailStreakRef.current >= 2) {
              setConnectionError('Dispatch Cloud is unreachable. Retrying every 10s…');
            }
          }
        }
      } catch {
        setIsCloudMode(true);
        cloudFailStreakRef.current += 1;
        if (cloudFailStreakRef.current >= 2) {
          setConnectionError('Dispatch Cloud is unreachable. Retrying every 10s…');
        }
      }
    }
    if (clipFetchGenerationRef.current === generation) {
      setIsLoading(false);
      if (refreshSucceeded) {
        setClipRefreshFailed(false);
      } else if (cloudFailStreakRef.current >= 2) {
        setClipRefreshFailed(true);
      }
    }
    // Release the in-flight lock and honor one queued trailing refresh.
    clipsFetchBusyRef.current = false;
    if (clipsFetchPendingRef.current) {
      clipsFetchPendingRef.current = false;
      void fetchClips();
    }
  };

  const handleSubmitRemoteJob = async () => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to process a new video.');
      return;
    }
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
    if (!currentClip) return;
    localCaptionsRevisionRef.current.set(currentClip.id, Number(currentClip.captions_revision ?? 0));
  }, [currentClip?.id, currentClip?.captions_revision]);

  // Initial load (also warms the cache while the tab is not visible).
  useEffect(() => {
    void fetchClips();
  }, [pcHost]);

  useEffect(() => {
    if (!isActive) {
      // Hidden tabs must not keep decoding video or polling.
      videoRef.current?.pause();
      bgVideoRef.current?.pause();
      setIsPlaying(false);
      return;
    }
    if (mountedOnceRef.current) {
      // Returning to the tab refreshes once instead of refetching every mount.
      void fetchClips();
    } else {
      mountedOnceRef.current = true;
    }
    if (viewMode !== 'library') return;
    // 10s poll cadence; background tabs skip polling entirely (battery + no
    // presigned media churn) and an immediate refresh runs on tab return.
    const interval = setInterval(() => {
      if (document.hidden) return;
      fetchClips();
    }, 10_000);
    const onVisibilityChange = () => {
      if (!document.hidden) fetchClips();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [pcHost, viewMode, isActive]);

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
    setCropAnchorX(typeof currentClip.crop_anchor_x === 'number' ? currentClip.crop_anchor_x : 0.5);

    // Fast-path: Check in-memory cache or pre-fetched caption_data for 0ms instant display
    const currentRev = Number(currentClip.range_revision ?? 0);
    const cachedEntry = captionTrackCache.get(currentClip.id);
    let seededFromLocal = false;

    if (cachedEntry && cachedEntry.revision === currentRev) {
      resetHistory(cachedEntry.track);
      setCaptionClipId(currentClip.id);
      setCaptionLoadingState(cachedEntry.track.groups.length > 0 ? 'success' : 'empty');
      setSaveStatus('saved');
      if (cachedEntry.track.groups.length > 0) setSelectedGroupId(cachedEntry.track.groups[0].id);
      seededFromLocal = true;
    } else if (currentClip.caption_data && typeof currentClip.caption_data === 'string' && currentClip.caption_data.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(currentClip.caption_data);
        if (parsed && Array.isArray(parsed.groups)) {
          const track = normalizeCaptionTrack(parsed, currentClip.id, currentClip.duration || 40);
          captionTrackCache.set(currentClip.id, { track, revision: currentRev, timestamp: Date.now() });
          resetHistory(track);
          setCaptionClipId(currentClip.id);
          setCaptionLoadingState(track.groups.length > 0 ? 'success' : 'empty');
          setSaveStatus('saved');
          if (track.groups.length > 0) setSelectedGroupId(track.groups[0].id);
          seededFromLocal = true;
        }
      } catch (_) {}
    }

    if (!seededFromLocal) {
      setCaptionLoadingState('loading');
      setCaptionErrorMessage(null);
      setCaptionClipId(null);
    }

    captionRequestControllerRef.current?.abort();
    const controller = new AbortController();
    captionRequestControllerRef.current = controller;
    let isCurrentClip = true;
    if (!isCloudMode) {
      loadClipCaptionTrack(currentClip.id, currentClip.duration || 40, pcHost, controller.signal, currentRev)
        .then((track) => {
          if (!isCurrentClip) return;
          resetHistory(track);
          setCaptionClipId(currentClip.id);
          setCaptionLoadingState(track.groups.length > 0 ? 'success' : 'empty');
          setSaveStatus('saved');
          if (track.groups.length > 0) setSelectedGroupId(track.groups[0].id);
        })
        .catch((err) => {
          if (!isCurrentClip) return;
          console.warn('Captions unavailable from PC daemon:', err);

          // Resilient fallback: If the clip already has captions, groups, or word_timings attached, use them
          const clipDuration = Math.max(0.1, currentClip.duration || 10);
          const rawCap = currentClip.captions as { words?: unknown[]; groups?: unknown[] } | undefined;
          const fallbackGroups = rawCap && Array.isArray(rawCap.groups) && rawCap.groups.length > 0 ? rawCap.groups : null;
          const fallbackWords = (rawCap && Array.isArray(rawCap.words) && rawCap.words.length > 0 ? rawCap.words : null)
            || ((currentClip as any).word_timings && Array.isArray((currentClip as any).word_timings) && (currentClip as any).word_timings.length > 0 ? (currentClip as any).word_timings : null);

          if (fallbackGroups) {
            const track = normalizeCaptionTrack({ ...(rawCap as object), clip_id: currentClip.id }, currentClip.id, clipDuration);
            resetHistory(track);
            setCaptionClipId(currentClip.id);
            setCaptionLoadingState('success');
            setSaveStatus('saved');
            setSelectedGroupId((track.groups[0]?.id as string | undefined) ?? null);
            return;
          }

          if (fallbackWords) {
            const seeded = normalizeCaptionTrack(
              { clip_id: currentClip.id, groups: buildCloudCaptionGroups(fallbackWords, clipDuration) },
              currentClip.id,
              clipDuration
            );
            resetHistory(seeded);
            setCaptionClipId(currentClip.id);
            setCaptionLoadingState(seeded.groups.length > 0 ? 'success' : 'empty');
            setSaveStatus('saved');
            setSelectedGroupId((seeded.groups[0]?.id as string | undefined) ?? null);
            return;
          }

          setCaptionLoadingState('error');
          setCaptionErrorMessage(err instanceof Error ? err.message : 'Caption request failed. Retry.');
        });
    } else {
      // Cloud mode loads captions directly from the Dispatch cloud payload so
      // editing works with no PC connection. Two shapes arrive in `captions`:
      // an edited track (groups + default_style/...) or a transcript seed
      // (raw words relative to the edit window).
      const clipDuration = Math.max(0.1, currentClip.duration || 10);
      // Seed the revision this editor session will save against (the worker
      // refuses stale saves with 409 instead of silently overwriting a newer
      // phone edit).
      const seededRevision = Number(currentClip.captions_revision ?? 0);
      cloudCaptionsRevisionRef.current.set(currentClip.id, Number.isFinite(seededRevision) ? Math.floor(seededRevision) : 0);
      const cloudCap = currentClip.captions as { words?: unknown[]; groups?: unknown[] } | undefined;
      const cloudGroups = cloudCap && Array.isArray(cloudCap.groups) ? cloudCap.groups : null;
      const cloudWords = cloudCap && Array.isArray(cloudCap.words) ? (cloudCap.words as Array<{ w?: string; text?: string; start: number; end: number }>) : null;
      if (cloudGroups && cloudGroups.length > 0) {
        const track = normalizeCaptionTrack({ ...(cloudCap as object), clip_id: currentClip.id }, currentClip.id, clipDuration);
        resetHistory(track);
        setCaptionClipId(currentClip.id);
        setCaptionLoadingState('success');
        setSaveStatus('saved');
        setSelectedGroupId((track.groups[0]?.id as string | undefined) ?? null);
      } else if (cloudWords && cloudWords.length > 0) {
        const seeded = normalizeCaptionTrack(
          { clip_id: currentClip.id, groups: buildCloudCaptionGroups(cloudWords, clipDuration) },
          currentClip.id,
          clipDuration
        );
        resetHistory(seeded);
        setCaptionClipId(currentClip.id);
        setCaptionLoadingState(seeded.groups.length > 0 ? 'success' : 'empty');
        setSaveStatus('saved');
        setSelectedGroupId((seeded.groups[0]?.id as string | undefined) ?? null);
      } else {
        // No caption payload yet: either the PC has not written window
        // transcripts yet, or this clip's proxy is still staging. Treat as
        // pending-empty, not a connection error.
        setCaptionLoadingState('empty');
      }
    }

    // Defer heavy filmstrip frame extraction so active video stream and captions have top network priority
    const reqId = ++filmstripReqIdRef.current;
    if (currentClip.thumbnail_url) {
      const tUrl = currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost);
      setFilmstripFrames([tUrl]);
    } else {
      setFilmstripFrames([]);
    }
    if (offlineReadOnly) {
      return () => {
        isCurrentClip = false;
        captionRequestControllerRef.current?.abort();
        captionRequestControllerRef.current = null;
        controller.abort();
      };
    }

    let timer: any = null;
    if (viewMode === 'studio') {
      timer = setTimeout(() => {
        fetch(apiUrl(`/api/clips/${currentClip.id}/filmstrip`, pcHost), { headers: getAuthHeaders(), signal: controller.signal })
          .then((res) => (res.ok ? res.json() : null))
          .then((fsData) => {
            if (reqId === filmstripReqIdRef.current && fsData && Array.isArray(fsData.frames) && fsData.frames.length > 0) {
              setFilmstripFrames(fsData.frames.map((f: string) => (f.startsWith('http') ? f : apiUrl(f, pcHost))));
            }
          })
          .catch(() => { });
      }, 700);
    }

    return () => {
      isCurrentClip = false;
      if (timer) clearTimeout(timer);
      captionRequestControllerRef.current?.abort();
      captionRequestControllerRef.current = null;
      controller.abort();
    };
  }, [currentClip?.id, currentClip?.range_revision, pcHost, isCloudMode, offlineReadOnly, viewMode]);

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

    // Check if the loaded video duration actually supports source timebase
    const effectiveSource = isSourceTimebase && Boolean(video.duration >= clipEnd - 1.0);

    if (video.paused) {
      if (effectiveSource) {
        if (video.currentTime >= clipEnd - 0.1 || video.currentTime < clipStart - 0.2) {
          video.currentTime = clipStart;
          if (bgVideoRef.current) bgVideoRef.current.currentTime = clipStart;
        }
      } else {
        if (video.currentTime >= clipDuration - 0.1 || (video.duration && video.currentTime >= video.duration - 0.1)) {
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
    const video = videoRef.current;
    const effectiveSource = isSourceTimebase && Boolean(video && video.duration >= clipEnd - 1.0);
    const actualTarget = effectiveSource ? (clipStart + safeTarget) : safeTarget;
    if (video) {
      video.currentTime = actualTarget;
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

    const effectiveSourceForContext = isSourceTimebase && Boolean(video.duration >= clipEnd - 1.0);
    if (effectiveSourceForContext && contextPreviewRef.current) {
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

    const effectiveSource = isSourceTimebase && Boolean(video.duration >= clipEnd - 1.0);

    if (effectiveSource) {
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
    const video = videoRef.current;
    const loadedDuration = Number.isFinite(mediaDuration) ? mediaDuration : video?.duration;

    // Determine authoritative timebase from actual media duration vs clip timestamps:
    // If loadedDuration is smaller than clipEnd - 1.0 (when clipEnd > clipDuration + 2.0),
    // this media file cannot be the raw source; it is the rendered clip (0-based timebase).
    let isSourceEffective = (typeof isSource === 'boolean') ? isSource : isSourceTimebase;
    if (loadedDuration && Number.isFinite(loadedDuration)) {
      if (clipEnd > clipDuration + 2.0 && loadedDuration < clipEnd - 1.0) {
        isSourceEffective = false;
      } else if (clipEnd > clipDuration + 2.0 && loadedDuration >= clipEnd - 1.0) {
        isSourceEffective = true;
      }
    }
    setIsSourceTimebase(isSourceEffective);
    if (currentClip && isSourceEffective && loadedDuration && Number.isFinite(loadedDuration)) {
      setSourceDurationForClip({ clipId: currentClip.id, seconds: loadedDuration });
    }
    if (video) {
      if (video.videoWidth > 0 && video.videoHeight > 0 && isSourceEffective) {
        if (currentClip) {
          const isLandscape = video.videoWidth > video.videoHeight * 1.05;
          const newOrientation = isLandscape ? 'landscape' : 'portrait';
          if (currentClip.source_orientation !== newOrientation) {
            currentClip.source_orientation = newOrientation;
            currentClip.available_layout_modes = isLandscape ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES;
            if (isLandscape && !['crop_916', 'crop_follow', 'fit_blur', 'fit_black', 'native_169', 'landscape'].includes(layoutMode)) {
              setLayoutMode('crop_916');
            }
          }
        }
      }
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

  const [templateSaveFeedback, setTemplateSaveFeedback] = useState<string | null>(null);

  const handleSaveAsDefaultTemplate = async () => {
    const repGroup = (captionTrack?.groups && captionTrack.groups.length > 0)
      ? (selectedGroupId ? captionTrack.groups.find(g => g.id === selectedGroupId) || captionTrack.groups[0] : captionTrack.groups[0])
      : null;

    const templateData = {
      name: 'Creator Standard',
      framing: {
        vertical_mode: currentClip?.source_orientation === 'portrait' ? layoutMode : 'native_916',
        horizontal_mode: currentClip?.source_orientation === 'landscape' ? layoutMode : 'crop_916',
      },
      captions: {
        preset_id: 'custom',
        font_family: repGroup?.style?.font_family || 'Impact',
        font_size: repGroup?.style?.font_size || 48,
        font_weight: repGroup?.style?.font_weight || '900',
        letter_spacing: repGroup?.style?.letter_spacing ?? 0,
        line_height: repGroup?.style?.line_height ?? 1.25,
        primary_color: repGroup?.style?.primary_color || '#FFFFFF',
        highlight_color: repGroup?.style?.highlight_color || '#FFD700',
        outline_enabled: repGroup?.style?.outline_enabled ?? true,
        outline_color: repGroup?.style?.outline_color || '#000000',
        outline_width: repGroup?.style?.outline_width ?? 4.5,
        shadow_enabled: repGroup?.style?.shadow_enabled ?? true,
        shadow_color: repGroup?.style?.shadow_color || '#000000',
        shadow_blur: repGroup?.style?.shadow_blur ?? 4,
        shadow_offset_x: repGroup?.style?.shadow_offset_x ?? 2,
        shadow_offset_y: repGroup?.style?.shadow_offset_y ?? 3,
        shadow_distance: repGroup?.style?.shadow_distance,
        shadow_angle: repGroup?.style?.shadow_angle,
        shadow_opacity: repGroup?.style?.shadow_opacity ?? 1.0,
        glow_enabled: repGroup?.style?.glow_enabled ?? false,
        glow_color: repGroup?.style?.glow_color || '#00F0FF',
        glow_blur: repGroup?.style?.glow_blur ?? 12,
        glow_opacity: repGroup?.style?.glow_opacity ?? 1.0,
        background_enabled: repGroup?.style?.background_enabled ?? false,
        background_mode: repGroup?.style?.background_mode || 'none',
        background_color: repGroup?.style?.background_color || '#000000',
        background_opacity: repGroup?.style?.background_opacity ?? 1.0,
        background_padding_x: repGroup?.style?.background_padding_x ?? 12,
        background_padding_y: repGroup?.style?.background_padding_y ?? 6,
        background_corner_radius: repGroup?.style?.background_corner_radius ?? 8,
        background_offset_x: repGroup?.style?.background_offset_x,
        background_offset_y: repGroup?.style?.background_offset_y,
        animation: repGroup?.animation?.type || 'pop',
        animation_config: repGroup?.animation,
        position_y: repGroup?.layout?.position_y ?? 0.78,
        max_words_per_line: 3,
        all_caps: repGroup?.style?.text_transform === 'uppercase',
      },
      hook: {
        enabled: true,
        style: 'pill',
      },
      publish_mode: publishMode || 'private',
      platform_targets: ['youtube', 'instagram'],
    };

    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('dispatch_default_template', JSON.stringify(templateData));
      }
      await fetch(apiUrl('/api/template/default', pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(templateData),
      });
      setTemplateSaveFeedback('Saved as default template!');
      setTimeout(() => setTemplateSaveFeedback(null), 2500);
    } catch {
      setTemplateSaveFeedback('Saved default locally');
      setTimeout(() => setTemplateSaveFeedback(null), 2500);
    }
  };

  const handleLayoutChange = async (newMode: LayoutMode, newAnchor?: number) => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to change clip framing.');
      return;
    }
    setLayoutMode(newMode);
    if (typeof newAnchor === 'number') {
      setCropAnchorX(newAnchor);
    }
    if (!currentClip) return;

    const effAnchor = typeof newAnchor === 'number' ? newAnchor : cropAnchorX;
    const availModes = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
      ? currentClip.available_layout_modes
      : (currentClip.source_orientation === 'landscape' ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES);
    const modeObj = availModes.find((m) => m.id === newMode);
    const immediateTargetAspect = modeObj ? modeObj.target_aspect : (newMode === 'native_169' || newMode === 'landscape' ? '16:9' : '9:16');

    // 1. INSTANT 0ms Optimistic In-Memory Update:
    setReadyClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, crop_anchor_x: effAnchor, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );
    setApprovedClips((prev) =>
      prev.map((c) => (c.id === currentClip.id ? { ...c, layout_mode: newMode, crop_anchor_x: effAnchor, target_aspect: immediateTargetAspect, is_render_stale: true } : c))
    );

    // 2. Non-blocking asynchronous persistence to server
    try {
      fetch(apiUrl(`/api/clips/${currentClip.id}/layout`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ layout_mode: newMode, crop_anchor_x: effAnchor }),
      }).then(async (res) => {
        if (res.ok) {
          const data = await res.json();
          setReadyClips((prev) =>
            prev.map((c) =>
              c.id === currentClip.id
                ? {
                  ...c,
                  layout_mode: data.layout_mode || newMode,
                  crop_anchor_x: typeof data.crop_anchor_x === 'number' ? data.crop_anchor_x : effAnchor,
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

  const handleAutoReframe = async () => {
    if (!currentClip || isAutoReframing) return;
    if (offlineReadOnly) {
      setFeedback('Reconnect to use Auto-Reframe.');
      return;
    }
    setIsAutoReframing(true);
    try {
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/auto_reframe`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        const detectedAnchor = typeof data.crop_anchor_x === 'number' ? data.crop_anchor_x : 0.5;
        setCropAnchorX(detectedAnchor);
        setLayoutMode('crop_916');
        setReadyClips((prev) =>
          prev.map((c) =>
            c.id === currentClip.id
              ? {
                ...c,
                layout_mode: 'crop_916',
                crop_anchor_x: detectedAnchor,
                target_aspect: '9:16',
                is_render_stale: true,
              }
              : c
          )
        );
        setFeedback(`Subject centered (${Math.round(detectedAnchor * 100)}%)`);
      } else {
        setFeedback('Could not center subject automatically.');
      }
    } catch (e) {
      console.warn('Auto-reframe error:', e);
      setFeedback('Auto-reframe failed.');
    } finally {
      setIsAutoReframing(false);
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
    if (offlineReadOnly) {
      setSaveStatus('error');
      setFeedback('Reconnect to save caption edits.');
      return false;
    }
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
      if (isCloudMode) {
        // Cloud save path: persist the edited caption track on the Dispatch
        // cloud so it stays available offline and syncs to the PC later.
        const session = await currentSession();
        const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
        if (!token) {
          throw new Error('Sign in to save cloud captions.');
        }
        const baseRevision = cloudCaptionsRevisionRef.current.get(currentClip.id)
          ?? Number((currentClip as any).captions_revision ?? 0);
        const res = await fetch(`${WORKER_BASE}/clips/${encodeURIComponent(currentClip.id)}/captions`, {
          method: 'PUT',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ track: captionTrack, base_revision: Number.isFinite(baseRevision) ? Math.floor(baseRevision) : 0 }),
        });
        if (res.status === 409) {
          // Another device saved a newer track while this editor was open.
          setSaveStatus('error');
          setFeedback('Captions changed on another device. Reopen this clip to load the latest track.');
          return false;
        }
        if (res.ok) {
          const saveData = await res.json().catch(() => ({} as { revision?: unknown }));
          const nextRevision = Number((saveData as { revision?: unknown })?.revision);
          if (Number.isFinite(nextRevision)) {
            cloudCaptionsRevisionRef.current.set(currentClip.id, Math.floor(nextRevision));
          }
          setSaveStatus('saved');
          setFeedback('Captions saved to Dispatch Cloud.');
          setTimeout(() => setFeedback(null), 2500);
          return true;
        }
        throw new Error(`Cloud returned HTTP ${res.status}`);
      }
      const baseRevision = localCaptionsRevisionRef.current.get(currentClip.id)
        ?? Number(currentClip.captions_revision ?? 0);
      const res = await fetch(apiUrl(`/api/clips/${currentClip.id}/captions`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ ...captionTrack, base_revision: baseRevision }),
      });
      if (res.status === 409) {
        setSaveStatus('error');
        setFeedback('Captions changed on another device. Reopen this clip to load the latest track.');
        return false;
      }
      if (res.ok) {
        const data = await res.json().catch(() => ({} as { captions_revision?: unknown }));
        const nextRevision = Number((data as { captions_revision?: unknown })?.captions_revision);
        if (Number.isFinite(nextRevision)) {
          localCaptionsRevisionRef.current.set(currentClip.id, Math.floor(nextRevision));
        }
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
    if (offlineReadOnly) {
      setFeedback('Reconnect to change clip trim.');
      return false;
    }
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
    setSaveStatus('unsaved');
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
    setSaveStatus('unsaved');
  };

  const handleApplyLayoutToAll = (groupId: string) => {
    if (!visibleCaptionTrack) return;
    const { nextTrack, affectedCount } = applySectionStylesToTrack(
      visibleCaptionTrack,
      groupId,
      'all'
    );
    // Commit as a single atomic history step for undo/redo
    setCaptionTrack(nextTrack, true);
    setSaveStatus('unsaved');
    setFeedback(`Applied style & layout to all captions (${affectedCount})`);
    setTimeout(() => {
      setFeedback(null);
    }, 2200);
  };

  // 5. Approve & Publish Handler (Optimistic UI)
  const handleApprove = () => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to approve and publish.');
      return;
    }
    if (!currentClip) return;
    if (!titleDraft.trim()) {
      setFeedback('Clip title cannot be empty.');
      return;
    }
    if (isCloudMode) {
      // Approval requires the processing PC (render + outbox). Never pretend
      // the clip was approved when the PC is unreachable.
      setFeedback('Approval needs the processing PC. Reconnect Dispatch on your PC, then approve again.');
      return;
    }

    const clipToApprove = currentClip;
    const optimisticTitle = titleDraft.trim();
    const optimisticDescription = captionDraft.trim();
    const optimisticLayout = layoutMode;
    const optimisticMode = publishMode;
    const trackSnapshot = captionTrack;
    const platformsToPublish = selectedPlatforms.map((p) => p === 'youtube_shorts' ? 'youtube' : p).join(',');
    const accountsMap = { ...selectedAccountMap };
    const rev = clipToApprove.range_revision || 0;

    // Snapshot pre-approval view state so a failed approve can restore the user's place
    const previousViewMode = viewMode;
    const previousSelectedClipId = selectedClipId;

    // Show tactile spinner for one render frame before navigating away
    setIsSubmitting(true);

    // Instant Optimistic State Update:
    const approvedItem: ClipItem = {
      ...clipToApprove,
      title: optimisticTitle,
      description: optimisticDescription,
      layout_mode: optimisticLayout,
      publish_mode: optimisticMode,
      status: 'approved',
      approved_at: new Date().toISOString(),
    };

    setReadyClips((prev) => prev.filter((c) => c.id !== clipToApprove.id));
    setApprovedClips((prev) => [approvedItem, ...prev.filter((c) => c.id !== clipToApprove.id)]);
    setFeedback('Queued for rendering & publishing to YouTube…');
    setViewMode('library');
    setSelectedClipId(null);
    setIsSubmitting(false);

    // Restore a failed approve: clip back to Ready, user back to where they were
    const restoreToReady = (message: string) => {
      setApprovedClips((prev) => prev.filter((c) => c.id !== clipToApprove.id));
      setReadyClips((prev) => (prev.some((c) => c.id === clipToApprove.id) ? prev : [clipToApprove, ...prev]));
      setViewMode(previousViewMode);
      setSelectedClipId(previousSelectedClipId);
      setFeedback(message);
    };

    // Asynchronous background call without blocking UI:
    (async () => {
      try {
        if (trackSnapshot) {
          if (trackSnapshot.clip_id && trackSnapshot.clip_id !== clipToApprove.id) {
            console.error('Refusing to approve: caption track belongs to another clip');
            restoreToReady('Caption track mismatch: approval cancelled, clip kept in Ready.');
            return;
          }
          const capBaseRevision = localCaptionsRevisionRef.current.get(clipToApprove.id)
            ?? Number(clipToApprove.captions_revision ?? 0);
          const capRes = await fetch(apiUrl(`/api/clips/${clipToApprove.id}/captions`, pcHost), {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify({ ...trackSnapshot, base_revision: capBaseRevision }),
          });
          if (!capRes.ok) {
            console.error('Caption save failed before approval:', capRes.status);
            restoreToReady(`Caption save failed (HTTP ${capRes.status}) — clip kept in Ready review.`);
            return;
          }
        }

        const res = await fetch(apiUrl(`/api/clips/${clipToApprove.id}/approve`, pcHost), {
          method: 'POST',
          headers: getAuthHeaders(),
          body: JSON.stringify({
            title: optimisticTitle,
            hashtags: optimisticDescription,
            publish_mode: optimisticMode,
            platforms: platformsToPublish,
            account_ids: accountsMap,
            layout_mode: optimisticLayout,
            range_revision: rev,
          }),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => null);
          console.error('Approval failed:', err);
          restoreToReady(err?.detail || 'Approval request failed — clip returned to Ready.');
        } else {
          // Poll server for published status & remote_id
          setTimeout(() => { fetchClips(); }, 2000);
          setTimeout(() => { fetchClips(); }, 5000);
        }
      } catch (err) {
        console.error('Background publish error:', err);
        // Network errors/timeouts never produced an HTTP response. Restore the
        // reviewable clip instead of leaving a false "approved" state.
        restoreToReady('Approval request failed (connection lost) — clip kept in Ready review. Retry when the PC is reachable.');
      }
    })();
  };

  // 6. Double Confirmation Reject Handlers
  const handlePromptReject = (clip: ClipItem) => {
    setIsMoreModalOpen(false);
    setClipToReject(clip);
  };

  /**
   * Cloud-mode reject: hides the clip immediately, queues a durable command on
   * Dispatch Cloud (applied + purged by the PC after the 24h grace window),
   * and offers a 10s Undo that voids the command.
   */
  const performCloudReject = async (clip: ClipItem) => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to reject a clip.');
      return;
    }
    if (cloudRejectWorkingRef.current) return;
    cloudRejectWorkingRef.current = true;
    setIsSubmitting(true);
    setFeedback('Removing clip…');
    optimisticRejectIdsRef.current.add(clip.id);
    setReadyClips((prev) => prev.filter((item) => item.id !== clip.id));
    setApprovedClips((prev) => prev.filter((item) => item.id !== clip.id));
    if (selectedClipId === clip.id) {
      setViewMode('library');
      setSelectedClipId(null);
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 12_000);
    try {
      const session = await currentSession();
      const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
      if (!token) throw new Error('Sign in to manage cloud clips.');

      const res = await fetch(`${WORKER_BASE}/clips/${encodeURIComponent(clip.id)}/reject`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ reason: 'Rejected from mobile review' }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        const detail = (err as any)?.error;
        throw new Error(typeof detail === 'string' && detail ? detail : `Cloud returned HTTP ${res.status}`);
      }

      if (rejectUndoTimerRef.current) window.clearTimeout(rejectUndoTimerRef.current);
      setPendingRejectUndo({ clipId: clip.id, title: clip.title || 'Clip' });
      setFeedback(null);
      const timerId = window.setTimeout(() => {
        setPendingRejectUndo(null);
        setFeedback('Clip removed. Deleted from your PC within 24h.');
        setTimeout(() => setFeedback(null), 2500);
      }, 10_000);
      rejectUndoTimerRef.current = timerId;
    } catch (e) {
      optimisticRejectIdsRef.current.delete(clip.id);
      await fetchClips();
      const message = e instanceof DOMException && e.name === 'AbortError'
        ? 'Couldn’t reach Dispatch Cloud. The clip stays in your list.'
        : e instanceof Error
          ? e.message
          : 'Could not remove clip.';
      setFeedback(message);
    } finally {
      window.clearTimeout(timeoutId);
      optimisticRejectIdsRef.current.delete(clip.id);
      setIsSubmitting(false);
    }
  };

  /** Undo within the 10s window: voids the pending reject command server-side. */
  const handleCloudRejectUndo = async () => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to restore the clip.');
      return;
    }
    const undoState = pendingRejectUndo;
    if (!undoState || rejectUndoWorking) return;
    setRejectUndoWorking(true);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 12_000);
    try {
      const session = await currentSession();
      const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
      if (!token) throw new Error('Sign in to manage cloud clips.');
      const res = await fetch(`${WORKER_BASE}/clips/${encodeURIComponent(undoState.clipId)}/restore`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`Cloud returned HTTP ${res.status}`);
      const undoData = await res.json().catch(() => ({} as { state?: unknown }));
      const state = typeof (undoData as { state?: unknown })?.state === 'string'
        ? (undoData as { state: string }).state
        : 'unknown';
      if (rejectUndoTimerRef.current) {
        window.clearTimeout(rejectUndoTimerRef.current);
        rejectUndoTimerRef.current = null;
      }
      setPendingRejectUndo(null);
      // Honest outcome reporting (audit 2026-10-27): the command is durable —
      // "restored"/"voided" were applied instantly, "pending"/"claimed" are
      // queued for the PC (clip is already back in the cloud list), while
      // "expired" means the 24h grace lapsed and media may be gone.
      if (state === 'restored' || state === 'voided') {
        setFeedback('Clip restored.');
        setTimeout(() => setFeedback(null), 2500);
      } else if (state === 'pending' || state === 'claimed') {
        setFeedback('Restore queued — the clip is back in your cloud list.');
        setTimeout(() => setFeedback(null), 4000);
      } else if (state === 'expired') {
        setFeedback('The 24h undo window expired; the clip may have been removed.');
        setTimeout(() => setFeedback(null), 5000);
      } else if (state === 'not_rejected') {
        setFeedback('Clip was already restored.');
        setTimeout(() => setFeedback(null), 2500);
      } else {
        setFeedback('Restore requested. Refreshing to confirm…');
        setTimeout(() => setFeedback(null), 4000);
      }
      await fetchClips();
    } catch (e) {
      const message = e instanceof DOMException && e.name === 'AbortError'
        ? 'Restore timed out. Try again before the clip rebuilds.'
        : e instanceof Error
          ? e.message
          : 'Restore failed.';
      setFeedback(message);
    } finally {
      window.clearTimeout(timeoutId);
      setRejectUndoWorking(false);
    }
  };

  /** Restore a rejected cloud clip from the Rejected retention list. */
  const handleCloudRestore = async (clipId: string) => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to restore the clip.');
      return;
    }
    if (restoringId) return;
    setRestoringId(clipId);
    try {
      const session = await currentSession();
      const token = session?.access_token || (typeof window !== 'undefined' ? (window as any).DispatchBridge?.getAuthToken?.() : null);
      if (!token) {
        setFeedback('Sign in to restore cloud clips.');
        return;
      }
      const res = await fetch(`${WORKER_BASE}/clips/${encodeURIComponent(clipId)}/restore`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        setFeedback(`Restore failed (HTTP ${res.status}).`);
        return;
      }
      const data = await res.json().catch(() => ({} as { state?: unknown }));
      const state = typeof data?.state === 'string' ? data.state : 'unknown';
      setFeedback(state === 'expired'
        ? 'The 24h restore window expired; media may have been purged.'
        : state === 'not_rejected'
          ? 'Clip was already restored.'
          : 'Restore requested.');
      await fetchClips();
    } catch {
      setFeedback('Restore failed — check the connection and retry.');
    } finally {
      setRestoringId(null);
    }
  };

  const handleConfirmReject = async () => {
    if (offlineReadOnly) {
      setFeedback('Reconnect to reject a clip.');
      return;
    }
    if (!clipToReject || isSubmitting) return;
    const clip = clipToReject;
    if (isCloudMode) {
      setClipToReject(null);
      setIsMoreModalOpen(false);
      await performCloudReject(clip);
      return;
    }

    setIsSubmitting(true);
    setFeedback('Removing clip…');
    optimisticRejectIdsRef.current.add(clip.id);
    setReadyClips((prev) => prev.filter((item) => item.id !== clip.id));
    setApprovedClips((prev) => prev.filter((item) => item.id !== clip.id));
    setClipToReject(null);
    setIsMoreModalOpen(false);
    if (selectedClipId === clip.id) {
      setViewMode('library');
      setSelectedClipId(null);
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 12_000);
    try {
      const res = await fetch(apiUrl(`/api/clips/${clip.id}/reject`, pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ reason: 'Rejected from review studio' }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || 'Reject request failed');
      }

      setReadyClips((prev) => prev.filter((item) => item.id !== clip.id));
      setApprovedClips((prev) => prev.filter((item) => item.id !== clip.id));
      setFeedback('Clip deleted.');
    } catch (e) {
      optimisticRejectIdsRef.current.delete(clip.id);
      await fetchClips();
      const message = e instanceof DOMException && e.name === 'AbortError'
        ? 'Couldn’t confirm deletion. The list was refreshed.'
        : e instanceof Error
          ? e.message
          : 'Could not delete clip.';
      setFeedback(message);
    } finally {
      window.clearTimeout(timeoutId);
      optimisticRejectIdsRef.current.delete(clip.id);
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
      const sessKey = clip.session_id || (clip.youtube_video_id ? `yt_${clip.youtube_video_id}` : 'Imported Video');
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
              {offlineReadOnly ? (
                <>
                  <span className="w-1.5 h-1.5 rounded-full bg-text-muted" />
                  <span>Offline · saved clips</span>
                </>
              ) : isCloudMode ? (
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
              disabled={offlineReadOnly}
              aria-label="Process YouTube Video"
              className="w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all disabled:opacity-40"
              title={offlineReadOnly ? 'Reconnect to process a video' : 'Process YouTube Video in Cloud'}
            >
              <Plus size={19} />
            </button>
            <button
              type="button"
              onClick={openNotificationCenter}
              aria-label="Activity & Notifications"
              className="relative w-11 h-11 rounded-full flex items-center justify-center text-text-secondary hover:text-text-main hover:bg-surface-2 active:scale-[0.95] transition-all"
              title="Activity & Notifications"
            >
              <Bell size={18} />
              {hasActiveActivity && (
                <span className="absolute top-2.5 right-2.5 w-2 h-2 rounded-full bg-primary ring-2 ring-canvas animate-pulse" />
              )}
            </button>
          </div>
        </div>

        {/* FILTER CHIPS — uniform pills, YouTube selected-chip inversion */}
        <div className="mt-3 flex items-center gap-2 overflow-x-auto no-scrollbar">
          {[
            { id: 'all' as FilterStatus, label: 'All', count: allClips.length },
            { id: 'ready' as FilterStatus, label: 'Ready', count: readyClips.length },
            { id: 'approved' as FilterStatus, label: 'Published', count: approvedClips.length },
            { id: 'rejected' as FilterStatus, label: 'Rejected', count: rejectedClips.length },
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

      {/* CLOUD REJECT UNDO — 10s grace bar with real restore */}
      {pendingRejectUndo && (
        <div className="shrink-0 px-4 py-2 bg-surface-2 border-b border-border flex items-center justify-between gap-3">
          <span className="text-xs text-text-secondary truncate">
            '{pendingRejectUndo.title}' will be purged in 24h. Deleted from the list.
          </span>
          <button
            type="button"
            onClick={() => { void handleCloudRejectUndo(); }}
            disabled={rejectUndoWorking}
            className="shrink-0 text-xs font-semibold text-primary active:scale-[0.98] transition-all duration-150 disabled:opacity-50"
          >
            {rejectUndoWorking ? 'Restoring…' : 'Undo'}
          </button>
        </div>
      )}

      {showingCachedClips && (
        <div className="shrink-0 px-4 py-2 bg-surface-2 border-b border-border flex items-center gap-2 text-xs text-text-secondary">
          {offlineReadOnly ? <WifiOff size={13} className="shrink-0" /> : <RefreshCw size={13} className="shrink-0 animate-spin" />}
          <span className="truncate">
            {offlineReadOnly ? 'Offline — showing clips saved' : 'Showing saved clips while refreshing'}
            {cachedAt ? ` ${new Date(cachedAt).toLocaleString()}` : ''}
          </span>
        </div>
      )}

      {/* CLIP LIST — pull down to refresh, flat rows on canvas (no cards) */}
      <PullToRefresh
        onRefresh={fetchClips}
        isRefreshing={isLoading}
        className="flex-1 min-h-0"
      >
        <main className="min-h-full pb-24">
        {clipRefreshFailed && allClips.length === 0 && rejectedClips.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center select-none my-12">
            <div className="w-16 h-16 rounded-full bg-surface-2 flex items-center justify-center mb-4 text-text-muted">
              <WifiOff size={24} />
            </div>
            <h2 className="text-base font-semibold tracking-tight text-text-main">
              No offline clips yet
            </h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-[280px]">
              Connect once to save your clip library for offline viewing.
            </p>
            <button
              type="button"
              onClick={fetchClips}
              className="mt-5 min-h-[44px] px-4 rounded-xl bg-surface-2 text-sm font-medium text-text-main hover:bg-surface-3 active:scale-[0.98] transition-all flex items-center gap-2"
            >
              <RefreshCw size={15} className={isLoading ? 'animate-spin' : ''} />
              <span>Retry</span>
            </button>
          </div>
        ) : isLoading && allClips.length === 0 ? (
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
        ) : filterStatus === 'rejected' ? (
          <div className="px-4 py-4">
            {rejectedClips.length === 0 ? (
              <div className="py-12 text-center text-sm text-text-secondary">
                No rejected clips within the 24-hour retention window.
              </div>
            ) : (
              <div className="divide-y divide-border">
                {rejectedClips.map((clip) => (
                  <div key={clip.id} className="py-3 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-text-main truncate">{clip.title || 'Rejected clip'}</p>
                      <p className="text-xs text-text-muted mt-1">
                        {clip.rejected_at ? `Rejected ${new Date(clip.rejected_at).toLocaleString()}` : 'Rejected'}
                        {clip.purge_after ? ` · media purge ${new Date(clip.purge_after).toLocaleString()}` : ''}
                      </p>
                      {clip.reason && <p className="text-xs text-text-secondary mt-1 truncate">{clip.reason}</p>}
                    </div>
                    <button
                      type="button"
                      onClick={() => { void handleCloudRestore(clip.id); }}
                      disabled={offlineReadOnly || restoringId === clip.id}
                      className="shrink-0 min-h-[44px] px-4 rounded-xl bg-surface-2 text-sm font-medium text-text-main hover:bg-surface-3 active:scale-[0.98] transition-all disabled:opacity-50"
                    >
                      {restoringId === clip.id ? 'Restoring…' : 'Restore'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : filteredClips.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-6 text-center select-none my-12">
            <div className="w-16 h-16 rounded-full bg-surface-2 flex items-center justify-center mb-4 text-text-muted">
              {offlineReadOnly
                ? <WifiOff size={24} />
                : isCloudMode ? <Cloud size={24} className="text-primary" /> : <Film size={24} />}
            </div>
            <h2 className="text-base font-semibold tracking-tight text-text-main">
              {offlineReadOnly
                ? `No ${filterStatus} clips saved`
                : isCloudMode ? 'Cloud Mode Active' : 'No clips yet'}
            </h2>
            <p className="text-sm text-text-secondary mt-1.5 max-w-[280px]">
              {offlineReadOnly
                ? 'Your saved library has no clips in this filter. Reconnect to refresh the full library.'
                : isCloudMode
                  ? 'Your app is connected to Dispatch Cloud. Submit a YouTube video to process clips from anywhere.'
                  : 'Dispatch will show generated clips here after processing a recording session.'}
            </p>
            <div className="mt-5 flex items-center gap-3">
              <button
                type="button"
                onClick={() => setShowSubmitModal(true)}
                disabled={offlineReadOnly}
                className="min-h-[44px] px-5 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold active:scale-[0.98] transition-all flex items-center gap-2 shadow-hero-glow disabled:opacity-40"
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
          groupedSessions.map(([sessionId, clipsInSession]) => {
            const firstClip = clipsInSession[0];
            const isYt = Boolean(
              firstClip?.origin === 'youtube' ||
              firstClip?.youtube_video_id ||
              sessionId.startsWith('yt_') ||
              clipsInSession.some((c) => c.origin === 'youtube' || c.youtube_video_id)
            );
            const isPhone = Boolean(
              firstClip?.origin === 'phone' ||
              (sessionId.startsWith('sess_') && !isYt)
            );
            const sessionOrigin: 'phone' | 'youtube' | 'import' = isYt ? 'youtube' : isPhone ? 'phone' : 'import';
            const sessionLabel = sessionId.startsWith('yt_')
              ? (firstClip?.source_title || `YouTube ${sessionId.replace('yt_', '')}`)
              : sessionId === 'Imported Video'
                ? (sessionOrigin === 'youtube' ? 'YouTube Imports' : 'Imported Videos')
                : `Session ${sessionId.replace('sess_', '')}`;

            const showGroupHeader = groupedSessions.length > 1;

            return (
              <section key={sessionId}>
                {/* Session section header — only shown when multiple distinct sessions exist */}
                {showGroupHeader && (
                  <div className="px-4 pt-3 pb-1 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span
                        className="w-4 h-4 rounded bg-surface-2 flex items-center justify-center shrink-0"
                        title={sessionOrigin === 'phone' ? 'Camera recording' : sessionOrigin === 'youtube' ? 'YouTube video' : 'Imported video'}
                      >
                        {sessionOrigin === 'phone' ? (
                          <Camera size={10} className="text-text-muted" />
                        ) : sessionOrigin === 'youtube' ? (
                          <Youtube size={10} className="text-danger" />
                        ) : (
                          <Film size={10} className="text-primary" />
                        )}
                      </span>
                      <span className="text-xs font-semibold text-text-secondary truncate">
                        {sessionLabel}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono text-text-muted tabular-nums shrink-0">
                      {clipsInSession.length}&nbsp;{clipsInSession.length === 1 ? 'clip' : 'clips'}
                    </span>
                  </div>
                )}

                <div className={`px-4 ${!showGroupHeader ? 'pt-1' : ''}`}>
                  {clipsInSession.map((clip) => {
                    const isUploaded = clip.status === 'approved' || clip.status === 'published';
                    const isPublished = clip.status === 'published' || Boolean(clip.remote_id || clip.remote_url);
                    const youtubeUrl = clip.remote_url || (clip.remote_id ? 'https://youtu.be/' + clip.remote_id : null);
                    const daysLeft = getRetentionDaysLeft(clip.published_at);
                    const age = formatAge(isUploaded ? (clip.published_at || clip.approved_at || clip.created_at) : clip.created_at);
                    const clipOrigin = clip.origin || (clip.youtube_video_id ? 'youtube' : (clip.session_id ? 'phone' : 'youtube'));

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
                        {/* Layout-specific thumbnail: 58px 9:16 portrait for vertical shorts, 124px 16:9 for widescreen */}
                        <div className="relative w-[132px] aspect-video rounded-lg bg-black overflow-hidden shrink-0 flex items-center justify-center">
                          <Film size={16} className="text-white/40" />
                          {clip.thumbnail_url && (
                            <img
                              src={clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost)}
                              alt=""
                              loading="lazy"
                              decoding="async"
                              className="absolute inset-0 w-full h-full object-cover"
                              onError={(e) => {
                                // Hide (not remove) so layout never shifts; the
                                // frame placeholder stays visible behind it.
                                (e.target as HTMLElement).style.visibility = 'hidden';
                              }}
                            />
                          )}

                          {/* Minimal origin badge matching Sessions screen pattern */}
                          <span
                            className="absolute top-1 left-1 w-4 h-4 rounded bg-black/85 text-white flex items-center justify-center shadow-sm"
                            title={clipOrigin === 'phone' ? 'Camera recording' : 'YouTube video'}
                            role="img"
                            aria-label={clipOrigin === 'phone' ? 'Camera recording' : 'YouTube video'}
                          >
                            {clipOrigin === 'phone'
                              ? <Camera size={10} />
                              : <Youtube size={10} className="text-danger" />}
                          </span>

                          {clip.is_cloud_only && (
                            <span className="absolute top-1 left-6 px-1 py-0.5 rounded bg-black/85 text-white text-[9px] font-medium flex items-center gap-0.5">
                              <Cloud size={8} />
                              Cloud
                            </span>
                          )}
                          {/* Uploading shimmer overlay on thumbnail */}
                          {isUploaded && !isPublished && (
                            <div className="absolute inset-0 bg-primary/15 flex items-center justify-center">
                              <span className="w-5 h-5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
                            </div>
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
                            {age || (clip.session_id ? `#${clip.session_id.replace('sess_', '')}` : 'Clip')}
                            {isPublished ? (
                              !clip.is_cloud_only && <> &middot; {daysLeft}d left</>
                            ) : isUploaded ? (
                              <> &middot; <span className="inline-flex items-center gap-1 text-primary font-medium"><span className="w-2.5 h-2.5 rounded-full border border-primary/30 border-t-primary animate-spin inline-block" /><span>Uploading…</span></span></>
                            ) : null}
                            {clip.is_cloud_only && <> &middot; Cloud only</>}
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
            );
          })
        )}
        </main>
      </PullToRefresh>

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
              <div className="text-xs text-text-muted truncate tabular-nums flex items-center gap-1.5">
                <span
                  className="w-3.5 h-3.5 rounded bg-surface-2 flex items-center justify-center shrink-0"
                  title={currentClip.origin === 'phone' ? 'Camera recording' : 'YouTube video'}
                >
                  {(currentClip.origin || (currentClip.youtube_video_id ? 'youtube' : (currentClip.session_id ? 'phone' : 'youtube'))) === 'phone' ? (
                    <Camera size={9} className="text-text-muted" />
                  ) : (
                    <Youtube size={9} className="text-danger" />
                  )}
                </span>
                <span>
                  {currentClip.session_id
                    ? `Session ${currentClip.session_id.replace('sess_', '')}`
                    : (currentClip.youtube_video_id ? `YouTube ${currentClip.youtube_video_id}` : 'Dispatch review')}
                </span>
                <span>&middot;</span>
                <span className="font-mono">{formatDuration(currentClip.duration)}</span>
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
          {offlineReadOnly && (
            <div className="shrink-0 px-3 py-2 rounded-xl bg-surface-2 border border-border flex items-center gap-2 text-xs text-text-secondary">
              <WifiOff size={14} className="shrink-0" />
              <span>Video playback and edits need a connection. Your clip details are saved offline.</span>
            </div>
          )}
          {/* 1. CLEAN VIDEO PLAYER WITH DYNAMIC RESPONSIVE FRAMING: Stable center stage */}
          <div className="flex-1 min-h-0 w-full flex items-center justify-center overflow-hidden py-1">
            <FramedVideoStage
              sourceVideoUrl={offlineReadOnly ? undefined : (currentClip.source_video_url ? apiUrl(currentClip.source_video_url, pcHost) : (pcHost ? apiUrl(`/api/clips/${currentClip.id}/source`, pcHost) : undefined))}
              videoUrl={offlineReadOnly ? undefined : (currentClip.video_url ? (currentClip.video_url.startsWith('http') ? currentClip.video_url : apiUrl(currentClip.video_url, pcHost)) : undefined)}
              fallbackVideoUrl={offlineReadOnly ? undefined : (currentClip.edit_window_url || currentClip.remote_url || (currentClip.video_url && currentClip.video_url.startsWith('http') ? currentClip.video_url : undefined))}
              poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
              preferRendered={true}
              renderedLayoutMode={currentClip.rendered_layout_mode}
              mediaMissing={currentClip.media_missing === true}
              clipStart={clipStart}
              clipEnd={clipEnd}
              clipDuration={clipDuration}
              layoutMode={layoutMode}
              cropAnchorX={cropAnchorX}
              onCropAnchorChange={(newAnchor, isFinal) => {
                setCropAnchorX(newAnchor);
                if (isFinal) {
                  handleLayoutChange(layoutMode, newAnchor);
                }
              }}
              sourceOrientation={(currentClip.source_orientation || (currentClip.origin === 'youtube' || currentClip.youtube_video_id || currentClip.source_aspect_ratio === '16:9' ? 'landscape' : 'portrait'))}
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
              {visibleCaptionTrack && (
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
                    Local MP4 deleted after 7d retention. Published to YouTube.
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

            {/* 3. DYNAMIC ACTIONS & TELEMETRY: Changes depending on clip lifecycle */}
            {(() => {
              const loadedPublications = publicationLoad?.clipId === currentClip.id
                ? publicationLoad.items
                : (currentClip.publications ?? []);
              const fallbackId = currentClip.remote_id || currentClip.remote_url;
              const publications: PublicationItem[] = loadedPublications.length > 0
                ? loadedPublications
                : (fallbackId || currentClip.status === 'published')
                  ? [{
                      id: `legacy:${currentClip.id}`,
                      platform: 'youtube',
                      remote_id: currentClip.remote_id,
                      remote_url: currentClip.remote_url,
                      published_at: currentClip.published_at,
                      publish_mode: currentClip.publish_mode,
                      stats: { views: null, likes: null, comments: null },
                      stats_updated_at: null,
                      stats_stale: true,
                    }]
                  : [];
              const isClipPublished = Boolean(
                currentClip.status === 'published' ||
                publications.length > 0
              );
              const isClipPublishing = Boolean(
                currentClip.status === 'approved' && !isClipPublished
              );
              const primaryPublication = publications.find((p) => p.platform.startsWith('youtube'))
                || publications[0];
              const liveUrl = primaryPublication?.remote_url
                || (primaryPublication?.remote_id ? `https://youtube.com/shorts/${primaryPublication.remote_id}` : '')
                || currentClip.remote_url
                || (currentClip.remote_id ? `https://youtube.com/shorts/${currentClip.remote_id}` : '');
              const statsLoading = Boolean(
                publicationLoad?.clipId === currentClip.id && publicationLoad.loading
              );
              const statsError = Boolean(
                publicationLoad?.clipId === currentClip.id && publicationLoad.error
              );
              const openPlatform = primaryPublication
                ? (primaryPublication.platform.startsWith('youtube') ? 'YouTube' : platformLabel(primaryPublication.platform))
                : 'YouTube';

              if (isClipPublished) {
                return (
                  <section className="w-full space-y-3 pt-1">
                    {publications.map((publication) => {
                      const publicationUrl = publication.remote_url
                        || (publication.remote_id
                          ? (publication.platform.startsWith('youtube')
                            ? `https://youtube.com/shorts/${publication.remote_id}`
                            : '')
                          : '');
                      const isYouTube = publication.platform.startsWith('youtube');
                      const isShorts = Boolean(
                        publication.remote_id ||
                        (publicationUrl && publicationUrl.includes('/shorts/'))
                      );
                      const label = isYouTube
                        ? `YouTube${isShorts ? ' Shorts' : ''}`
                        : platformLabel(publication.platform);
                      const hasStats = Boolean(
                        publication.stats &&
                        [publication.stats.views, publication.stats.likes, publication.stats.comments]
                          .some((value) => value !== null && value !== undefined)
                      );
                      const publishedAge = formatAge(
                        publication.published_at || currentClip.published_at || currentClip.approved_at
                      );
                      return (
                        <div
                          key={publication.id}
                          className="w-full px-3 py-2.5 rounded-2xl bg-surface-100 border border-border space-y-2.5"
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="flex items-center gap-2.5 min-w-0">
                              <span className="w-2 h-2 rounded-full bg-status-success shadow-[0_0_8px_rgba(34,197,94,0.6)] shrink-0" />
                              <div className="flex flex-col min-w-0">
                                <span className="text-xs font-semibold text-text-main">
                                  {label}
                                </span>
                                <span className="text-[11px] text-text-muted font-mono tabular-nums truncate">
                                  {publishedAge ? `Published ${publishedAge}` : 'Published'}
                                  {publication.publish_mode ? ` · ${publication.publish_mode}` : ''}
                                </span>
                              </div>
                            </div>
                            {publicationUrl && (
                              <button
                                type="button"
                                onClick={() => handleCopy(publicationUrl, `${label} link`)}
                                className="shrink-0 w-11 h-11 rounded-xl bg-surface-200 hover:bg-surface-3 text-text-secondary hover:text-text-main flex items-center justify-center active:scale-95 transition-all"
                                aria-label={`Copy ${label} link`}
                                title="Copy link"
                              >
                                <Copy size={15} />
                              </button>
                            )}
                          </div>

                          <div className="grid grid-cols-3 gap-2 border-t border-border pt-2.5">
                            {[
                              { key: 'views', label: 'views', value: publication.stats?.views, Icon: Eye },
                              { key: 'likes', label: 'likes', value: publication.stats?.likes, Icon: Heart },
                              { key: 'comments', label: 'comments', value: publication.stats?.comments, Icon: MessageCircle },
                            ].map(({ key, label: metricLabel, value, Icon }) => (
                              <div key={key} className="min-w-0" aria-label={`${label} ${metricLabel}: ${value ?? 'unavailable'}`}>
                                <span className="flex items-center gap-1 text-[10px] uppercase font-semibold text-text-muted">
                                  <Icon size={11} className="shrink-0" />
                                  <span className="truncate">{metricLabel}</span>
                                </span>
                                <span
                                  className={`block mt-0.5 text-sm font-semibold font-mono tabular-nums text-text-main truncate ${statsLoading && !hasStats ? 'animate-pulse' : ''}`}
                                  aria-label={value === null || value === undefined ? 'Unavailable' : String(value)}
                                >
                                  {formatMetric(value)}
                                </span>
                              </div>
                            ))}
                          </div>

                          <p className="text-[11px] text-text-muted">
                            {statsLoading && !hasStats
                              ? 'Loading stats…'
                              : publication.stats_updated_at
                                ? `${publication.stats_stale ? 'Last updated' : 'Stats updated'} ${formatAge(publication.stats_updated_at)}`
                                : statsError || publication.stats_stale
                                  ? 'Stats unavailable'
                                  : 'Stats not available yet'}
                          </p>
                        </div>
                      );
                    })}

                    {/* Single hero action (opens the primary destination) +
                        secondary edit affordance. */}
                    <div className="w-full grid grid-cols-2 gap-2.5">
                      <button
                        type="button"
                        onClick={() => {
                          if (liveUrl) window.open(liveUrl, '_blank', 'noopener,noreferrer');
                        }}
                        disabled={!liveUrl}
                        className="min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all disabled:opacity-40 disabled:pointer-events-none"
                      >
                        <ExternalLink size={15} />
                        <span>Open on {openPlatform}</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => setViewMode('studio')}
                        disabled={offlineReadOnly}
                        title={offlineReadOnly ? 'Reconnect to edit this clip' : undefined}
                        className="min-h-[44px] rounded-xl bg-surface-2 hover:bg-surface-3 text-xs font-semibold text-text-secondary hover:text-text-main flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all disabled:opacity-40"
                      >
                        <Scissors size={14} className="text-primary" />
                        <span>Edit a Copy</span>
                      </button>
                    </div>
                  </section>
                );
              }

              if (isClipPublishing) {
                return (
                  <section className="w-full space-y-2.5 pt-1">
                    <div className="w-full px-3.5 py-3 rounded-2xl bg-surface-100 border border-primary/30 flex items-center gap-3">
                      <span className="shrink-0 w-5 h-5 rounded-full border-2 border-primary/30 border-t-primary animate-spin" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-primary leading-tight">Uploading to YouTube…</p>
                        <p className="text-[11px] text-text-muted mt-0.5">Rendering captions &amp; sending to your channel</p>
                      </div>
                    </div>
                    <div className="w-full h-1 rounded-full bg-surface-200 overflow-hidden">
                      <div className="h-full w-1/2 bg-primary rounded-full" style={{ animation: 'dispatch-slide 1.4s ease-in-out infinite' }} />
                    </div>
                  </section>
                );
              }

              // Normal Ready-to-Review Mode:
              const isLandscapeReview = currentClip.source_orientation === 'landscape'
                || currentClip.origin === 'youtube'
                || Boolean(currentClip.youtube_video_id)
                || currentClip.source_aspect_ratio === '16:9'
                || Boolean(videoRef.current && isSourceTimebase && videoRef.current.videoWidth > videoRef.current.videoHeight * 1.05);
              const availableModes = (currentClip.available_layout_modes && currentClip.available_layout_modes.length > 0)
                ? currentClip.available_layout_modes
                : (isLandscapeReview ? DEFAULT_LANDSCAPE_MODES : DEFAULT_PORTRAIT_MODES);
              const activeModeObj = availableModes.find((m) => m.id === layoutMode) || availableModes[0];

              return (
                <>
                  {/* Primary actions: Edit + Publish */}
                  <section className="w-full grid grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => setViewMode('studio')}
                      disabled={offlineReadOnly}
                      title={offlineReadOnly ? 'Reconnect to edit this clip' : undefined}
                      className="min-h-[44px] rounded-xl bg-surface-2 hover:bg-surface-3 text-sm font-semibold text-text-main flex items-center justify-center gap-2 active:scale-[0.98] transition-all disabled:opacity-40"
                    >
                      <Scissors size={15} className="text-primary" />
                      <span>Edit</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setViewMode('publish')}
                      disabled={offlineReadOnly}
                      title={offlineReadOnly ? 'Reconnect to publish this clip' : undefined}
                      className="min-h-[44px] rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-1.5 shadow-hero-glow active:scale-[0.98] transition-all disabled:opacity-40"
                    >
                      <Send size={14} />
                      <span>Publish</span>
                    </button>
                  </section>

                  {/* Layout framing */}
                  <section className="w-full pt-3 mt-1 border-t border-border">
                    <div className="flex items-center justify-between text-xs leading-tight mb-2">
                      <span className="font-medium uppercase tracking-wider text-text-muted">Layout framing</span>
                      <span className="font-mono text-xs text-primary font-semibold tabular-nums">
                        {activeModeObj ? `${activeModeObj.target_aspect} ${activeModeObj.label}` : layoutMode}
                      </span>
                    </div>
                    <div
                      className="grid gap-1.5 w-full"
                      style={{ gridTemplateColumns: `repeat(${availableModes.length}, minmax(0, 1fr))` }}
                    >
                      {availableModes.map((opt) => (
                        <button
                          key={opt.id}
                          type="button"
                          onClick={() => handleLayoutChange(opt.id)}
                          className={`h-8 px-1 rounded-lg text-[11px] sm:text-xs font-medium text-center flex items-center justify-center truncate active:scale-[0.98] transition-all ${layoutMode === opt.id
                            ? 'chip-selected font-semibold shadow-sm'
                            : 'bg-surface-2 text-text-secondary hover:text-text-main'
                            }`}
                          title={opt.description}
                        >
                          <span className="truncate">{opt.label}</span>
                        </button>
                      ))}
                    </div>
                    {(layoutMode === 'crop_916' || layoutMode === 'crop_follow') && (
                      <div className="mt-2.5 p-2 rounded-xl bg-surface-2 border border-border flex flex-col gap-2">
                        <div className="flex items-center justify-between text-[11px] leading-tight">
                          <span className="text-text-muted font-medium">Crop Anchor ({Math.round(cropAnchorX * 100)}%)</span>
                          <span className="text-[10px] text-text-muted">Drag preview or tap preset</span>
                        </div>
                        <div className="grid grid-cols-4 gap-1.5 w-full">
                          <button
                            type="button"
                            onClick={() => handleLayoutChange('crop_916', 0.25)}
                            className={`h-7 px-1 rounded-lg text-[11px] font-medium text-center flex items-center justify-center active:scale-[0.98] transition-all ${
                              Math.abs(cropAnchorX - 0.25) < 0.08
                                ? 'chip-selected font-semibold shadow-sm'
                                : 'bg-surface-1 text-text-secondary hover:text-text-main'
                            }`}
                          >
                            Left
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLayoutChange('crop_916', 0.50)}
                            className={`h-7 px-1 rounded-lg text-[11px] font-medium text-center flex items-center justify-center active:scale-[0.98] transition-all ${
                              Math.abs(cropAnchorX - 0.50) < 0.08
                                ? 'chip-selected font-semibold shadow-sm'
                                : 'bg-surface-1 text-text-secondary hover:text-text-main'
                            }`}
                          >
                            Center
                          </button>
                          <button
                            type="button"
                            onClick={() => handleLayoutChange('crop_916', 0.75)}
                            className={`h-7 px-1 rounded-lg text-[11px] font-medium text-center flex items-center justify-center active:scale-[0.98] transition-all ${
                              Math.abs(cropAnchorX - 0.75) < 0.08
                                ? 'chip-selected font-semibold shadow-sm'
                                : 'bg-surface-1 text-text-secondary hover:text-text-main'
                            }`}
                          >
                            Right
                          </button>
                          <button
                            type="button"
                            onClick={handleAutoReframe}
                            disabled={isAutoReframing}
                            className={`h-7 px-1 rounded-lg text-[11px] font-medium text-center flex items-center justify-center gap-1 active:scale-[0.98] transition-all ${
                              isAutoReframing
                                ? 'bg-primary/20 text-primary animate-pulse'
                                : 'bg-surface-1 text-primary hover:bg-surface-2 font-semibold border border-primary/30'
                            }`}
                            title="Auto-detect speaker and center crop"
                          >
                            <Sparkles size={11} className={isAutoReframing ? 'animate-spin' : ''} />
                            <span>{isAutoReframing ? 'Detecting…' : 'Auto'}</span>
                          </button>
                        </div>
                      </div>
                    )}
                  </section>
                </>
              );
            })()}          </div>
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
                  setShowStudioExitConfirm(true);
                  return;
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
            fallbackVideoUrl={currentClip.edit_window_url || currentClip.remote_url || (currentClip.video_url && currentClip.video_url.startsWith('http') ? currentClip.video_url : undefined)}
            poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
            renderedLayoutMode={currentClip.rendered_layout_mode}
            clipStart={clipStart}
            clipEnd={clipEnd}
            clipDuration={clipDuration}
            layoutMode={layoutMode}
            cropAnchorX={cropAnchorX}
            onCropAnchorChange={(newAnchor, isFinal) => {
              setCropAnchorX(newAnchor);
              if (isFinal) {
                handleLayoutChange(layoutMode, newAnchor);
              }
            }}
            sourceOrientation={(currentClip.source_orientation || (currentClip.origin === 'youtube' || currentClip.youtube_video_id || currentClip.source_aspect_ratio === '16:9' ? 'landscape' : 'portrait'))}
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
          {templateSaveFeedback && (
            <div className="absolute top-2 left-1/2 -translate-x-1/2 z-50 px-3 py-1 bg-primary text-white text-[11px] font-medium rounded-full shadow-lg backdrop-blur-sm animate-in fade-in flex items-center gap-1.5 pointer-events-none">
              <Bookmark size={12} />
              <span>{templateSaveFeedback}</span>
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

            {/* Right: Undo & Redo + Set Default Template action buttons */}
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleSaveAsDefaultTemplate}
                className="h-7 px-2 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center gap-1 text-[10px] font-medium text-text-secondary hover:text-text-main active:scale-95 transition-all"
                title="Save current caption style & framing as global default"
                aria-label="Set Default Template"
              >
                <Bookmark size={11} className="text-primary" />
                <span className="hidden sm:inline">Set Default</span>
              </button>
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
                {captionLoadingState === 'loading' && !visibleCaptionTrack && (
                  <div role="status" className="h-8 shrink-0 px-3 flex items-center gap-2 border-b border-border bg-surface text-text-muted">
                    <RotateCw size={13} className="animate-spin text-primary" />
                    <span className="text-xs">Loading captions…</span>
                  </div>
                )}
                {captionLoadingState === 'error' && !visibleCaptionTrack && (
                  <div role="alert" className="min-h-11 shrink-0 px-3 py-1.5 flex items-center justify-between gap-2 border-b border-border bg-surface">
                    <span className="text-xs text-text-secondary min-w-0">{captionErrorMessage || 'Could not load captions.'}</span>
                    {!isCloudMode && (
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => {
                            const clipId = currentClip.id;
                            const clipDuration = currentClip.duration || 40;
                            captionRequestControllerRef.current?.abort();
                            const controller = new AbortController();
                            captionRequestControllerRef.current = controller;
                            setCaptionLoadingState('loading');
                            setCaptionErrorMessage(null);
                            loadClipCaptionTrack(clipId, clipDuration, pcHost, controller.signal)
                              .then((track) => {
                                if (controller.signal.aborted || captionRequestControllerRef.current !== controller) return;
                                resetHistory(track);
                                setCaptionClipId(clipId);
                                setCaptionLoadingState(track.groups.length > 0 ? 'success' : 'empty');
                                setSaveStatus('saved');
                                if (track.groups.length > 0) setSelectedGroupId(track.groups[0].id);
                              })
                              .catch((err) => {
                                if (controller.signal.aborted || captionRequestControllerRef.current !== controller) return;
                                const rawCap = currentClip.captions as { words?: unknown[]; groups?: unknown[] } | undefined;
                                const fallbackGroups = rawCap && Array.isArray(rawCap.groups) && rawCap.groups.length > 0 ? rawCap.groups : null;
                                const fallbackWords = (rawCap && Array.isArray(rawCap.words) && rawCap.words.length > 0 ? rawCap.words : null)
                                  || ((currentClip as any).word_timings && Array.isArray((currentClip as any).word_timings) && (currentClip as any).word_timings.length > 0 ? (currentClip as any).word_timings : null);

                                if (fallbackGroups) {
                                  const track = normalizeCaptionTrack({ ...(rawCap as object), clip_id: clipId }, clipId, clipDuration);
                                  resetHistory(track);
                                  setCaptionClipId(clipId);
                                  setCaptionLoadingState('success');
                                  setSaveStatus('saved');
                                  setSelectedGroupId((track.groups[0]?.id as string | undefined) ?? null);
                                  return;
                                }

                                if (fallbackWords) {
                                  const seeded = normalizeCaptionTrack(
                                    { clip_id: clipId, groups: buildCloudCaptionGroups(fallbackWords, clipDuration) },
                                    clipId,
                                    clipDuration
                                  );
                                  resetHistory(seeded);
                                  setCaptionClipId(clipId);
                                  setCaptionLoadingState(seeded.groups.length > 0 ? 'success' : 'empty');
                                  setSaveStatus('saved');
                                  setSelectedGroupId((seeded.groups[0]?.id as string | undefined) ?? null);
                                  return;
                                }

                                setCaptionLoadingState('error');
                                setCaptionErrorMessage(err instanceof Error ? err.message : 'Caption request failed. Retry.');
                              })
                              .finally(() => {
                                if (captionRequestControllerRef.current === controller) captionRequestControllerRef.current = null;
                              });
                          }}
                          className="min-h-[44px] px-3 rounded-lg bg-surface-2 hover:bg-surface-3 border border-border text-xs font-medium text-text-main flex items-center gap-1 active:scale-[0.98] transition-all"
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
                          className="min-h-[44px] px-3 rounded-lg bg-primary hover:bg-primary-hover text-xs font-medium text-white active:scale-[0.98] transition-all"
                        >
                          Start fresh
                        </button>
                      </div>
                    )}
                  </div>
                )}
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
                fallbackVideoUrl={currentClip.edit_window_url || currentClip.remote_url || (currentClip.video_url && currentClip.video_url.startsWith('http') ? currentClip.video_url : undefined)}
                poster={currentClip.thumbnail_url ? (currentClip.thumbnail_url.startsWith('http') ? currentClip.thumbnail_url : apiUrl(currentClip.thumbnail_url, pcHost)) : undefined}
                preferRendered={false}
                renderedLayoutMode={currentClip.rendered_layout_mode}
                clipStart={clipStart}
                clipEnd={clipEnd}
                clipDuration={clipDuration}
                layoutMode={layoutMode}
                cropAnchorX={cropAnchorX}
                onCropAnchorChange={(newAnchor, isFinal) => {
                  setCropAnchorX(newAnchor);
                  if (isFinal) {
                    handleLayoutChange(layoutMode, newAnchor);
                  }
                }}
                sourceOrientation={(currentClip.source_orientation || (currentClip.origin === 'youtube' || currentClip.youtube_video_id || currentClip.source_aspect_ratio === '16:9' ? 'landscape' : 'portrait'))}
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
                {visibleCaptionTrack && (
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
        {/* Exit Confirmation Modal */}
        {showStudioExitConfirm && (
          <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
            <div className="w-full max-w-xs bg-surface-100 border border-border rounded-2xl p-5 shadow-2xl flex flex-col gap-4">
              <div className="flex flex-col gap-1 text-center">
                <h3 className="text-base font-semibold text-text-main">Save Clip Changes?</h3>
                <p className="text-xs text-text-muted">You have unsaved changes. Save before exiting to Clip Details?</p>
              </div>

              <div className="flex flex-col gap-2 pt-1">
                <button
                  type="button"
                  onClick={async () => {
                    const saved = await handleSaveCaptions();
                    if (saved) {
                      setShowStudioExitConfirm(false);
                      setViewMode('detail');
                      setSelectedGroupId(null);
                      setIsVideoSelected(false);
                      setActivePanel('none');
                    }
                  }}
                  className="w-full h-10 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center justify-center active:scale-98 transition-all shadow-hero-glow"
                >
                  Save & Exit
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowStudioExitConfirm(false);
                    setViewMode('detail');
                    setSelectedGroupId(null);
                    setIsVideoSelected(false);
                    setActivePanel('none');
                  }}
                  className="w-full h-10 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-text-main text-xs font-medium flex items-center justify-center active:scale-98 transition-all"
                >
                  Discard Changes
                </button>
                <button
                  type="button"
                  onClick={() => setShowStudioExitConfirm(false)}
                  className="w-full h-8 text-text-muted hover:text-text-main text-xs font-normal flex items-center justify-center transition-colors"
                >
                  Keep Editing
                </button>
              </div>
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
        <header className="shrink-0 px-4 pt-3 pb-2.5 border-b border-border bg-canvas z-20 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              type="button"
              onClick={() => setViewMode('detail')}
              className="min-h-[44px] min-w-[44px] rounded-xl bg-surface-100 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-[0.95] transition-all"
              title="Back to Clip Detail"
            >
              <ChevronLeft size={18} />
            </button>
            <div className="min-w-0">
              <h2 className="text-base font-semibold tracking-tight text-text-main truncate leading-tight">
                Publish Clip
              </h2>
              <div className="text-xs text-text-muted truncate mt-0.5">
                {currentClip.title}
              </div>
            </div>
          </div>
        </header>

        {/* PUBLISH SCROLL MAIN */}
        <main className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-5 pb-6">
          {/* Platform Selector — clean iOS-style grouped card with inset dividers */}
          <section className="space-y-2">
            <div className="flex items-center justify-between text-xs font-medium text-text-muted">
              <span>Destinations</span>
              <span className="font-mono text-xs">{selectedPlatforms.length} selected</span>
            </div>
            <div className="rounded-2xl bg-surface-100 border border-border overflow-hidden">
              {[
                { id: 'youtube_shorts', label: 'YouTube Shorts', provider: 'youtube', icon: <YouTubeShortsIcon /> },
                { id: 'instagram', label: 'Instagram Reels', provider: 'instagram', icon: <InstagramIcon /> },
                { id: 'linkedin', label: 'LinkedIn Video', provider: 'linkedin', icon: <LinkedInIcon /> },
                { id: 'x', label: 'X / Twitter', provider: 'twitter', icon: <XIcon /> },
              ].map((p, pIdx) => {
                const isSelected = selectedPlatforms.includes(p.id);
                const platformAccounts = connectedAccounts.filter(a => a.provider === p.provider);
                const hasAccount = platformAccounts.length > 0;
                const chosenAccountId = selectedAccountMap[p.id] || (hasAccount ? platformAccounts[0].account_id : '');
                const chosenAccount = platformAccounts.find(a => a.account_id === chosenAccountId) || platformAccounts[0];

                return (
                  <div key={p.id} className="relative">
                    {/* Inset divider removed for clean edge-to-edge row appearance */}
                    <div className="p-3.5 space-y-2">
                      <div className="flex items-center justify-between gap-3">
                        <button
                          type="button"
                          onClick={() => {
                            if (hasAccount) {
                              togglePlatform(p.id);
                            } else {
                              setFeedback(`Connect ${p.label} in Settings`);
                            }
                          }}
                          aria-pressed={isSelected}
                          className="flex items-center gap-3 min-w-0 text-left text-sm transition-all active:scale-[0.99] flex-1"
                        >
                          <div className={hasAccount ? 'shrink-0' : 'shrink-0 opacity-50'}>
                            {p.icon}
                          </div>
                          <div className="min-w-0">
                            <span className={`font-medium block truncate ${hasAccount ? 'text-text-main' : 'text-text-secondary'}`}>
                              {p.label}
                            </span>
                            {hasAccount ? (
                              <span className="text-xs text-text-muted font-mono block truncate">
                                {chosenAccount?.account_name || 'Connected'}
                              </span>
                            ) : (
                              <span className="text-xs text-text-muted block">
                                Not connected
                              </span>
                            )}
                          </div>
                        </button>

                        {hasAccount ? (
                          <button
                            type="button"
                            onClick={() => togglePlatform(p.id)}
                            aria-pressed={isSelected}
                            className="shrink-0 p-1 min-h-[44px] min-w-[44px] flex items-center justify-center active:scale-[0.95] transition-all"
                          >
                            <span className={`w-11 h-6 rounded-full p-0.5 flex items-center transition-colors duration-150 ${isSelected ? 'bg-primary justify-end' : 'bg-surface-3 border border-border justify-start'}`}>
                              <span className="w-5 h-5 rounded-full bg-white shadow-sm" />
                            </span>
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setFeedback(`To connect ${p.label}, open Settings -> Connected Accounts`)}
                            className="shrink-0 px-3.5 py-1.5 rounded-lg bg-surface-2 hover:bg-surface-3 border border-white/[0.08] text-xs font-medium text-text-main active:scale-[0.98] transition-all"
                          >
                            Connect
                          </button>
                        )}
                      </div>

                      {/* Account picker if multiple accounts connected for this provider */}
                      {isSelected && platformAccounts.length > 1 && (
                        <div className="pt-1 pl-11 flex items-center gap-1.5 flex-wrap">
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
                  </div>
                );
              })}
            </div>
          </section>

          {/* Visibility / Privacy Selector — sleek segmented control */}
          <section className="space-y-2">
            <div className="flex items-center justify-between text-xs font-medium text-text-muted">
              <span>Visibility</span>
              <span className="text-[11px] text-text-muted font-normal">
                {publishMode === 'private' && 'Only you can view'}
                {publishMode === 'unlisted' && 'Anyone with link can view'}
                {publishMode === 'public' && 'Publicly visible to everyone'}
              </span>
            </div>
            <div className="h-10 p-1 rounded-xl bg-surface-100 border border-border grid grid-cols-3 gap-1">
              {[
                { id: 'private', label: 'Private', icon: <Lock size={13} /> },
                { id: 'unlisted', label: 'Unlisted', icon: <LinkIcon size={13} /> },
                { id: 'public', label: 'Public', icon: <Globe size={13} /> },
              ].map((m) => {
                const isSelected = publishMode === m.id;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setPublishMode(m.id as 'private' | 'unlisted' | 'public')}
                    aria-pressed={isSelected}
                    className={`h-full rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all active:scale-[0.98] ${
                      isSelected
                        ? 'bg-primary text-white font-semibold shadow-sm'
                        : 'text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {m.icon}
                    <span>{m.label}</span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Content Fields */}
          <section className="space-y-3">
            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                Publishing Title
              </label>
              <input
                type="text"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                className="w-full min-h-[44px] rounded-xl bg-surface-100 border border-border px-3.5 text-sm text-text-main outline-none focus:border-border-strong transition-colors"
                placeholder="Title for publishing"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                Description &amp; #hashtags
              </label>
              <textarea
                value={captionDraft}
                onChange={(e) => setCaptionDraft(e.target.value)}
                rows={3}
                className="w-full rounded-xl bg-surface-100 border border-border p-3.5 text-sm text-text-main outline-none focus:border-border-strong resize-none"
                placeholder="Description, credits and hashtags"
              />
            </div>
          </section>
        </main>

        {/* STICKY PUBLISH ACTION FOOTER — clean hero CTA */}
        <footer className="shrink-0 border-t border-border bg-canvas px-4 pt-3 pb-8 z-30 space-y-2">
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
                ? `Connect ${missing[0] === 'youtube_shorts' ? 'YouTube' : missing[0]} in Settings`
                : selectedPlatforms.length === 0
                  ? 'Select at least one destination'
                  : hasNonYt
                    ? `Publish to ${selectedPlatforms.length} destinations`
                    : `Publish to YouTube · ${publishMode}`;

            return (
              <>
                {missing.length > 0 && (
                  <div className="p-2 rounded-lg bg-surface-2 border border-border text-xs text-text-muted text-center font-medium">
                    {missing.map(m => m === 'youtube_shorts' ? 'YouTube' : m).join(', ')} account not connected.
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleApprove}
                  disabled={isDisabled}
                  className="w-full h-12 rounded-xl bg-primary hover:bg-primary-hover text-white text-sm font-semibold flex items-center justify-center gap-2 shadow-hero-glow active:scale-[0.98] transition-all disabled:opacity-40"
                >
                  {isSubmitting
                    ? <span className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
                    : <Send size={15} />}
                  <span>{ctaText}</span>
                </button>
              </>
            );
          })()}
        </footer>
      </div>
    );
  };

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

            {currentClip.youtube_video_id && (
              <button
                type="button"
                onClick={() => handleCopy(`https://youtu.be/${currentClip.youtube_video_id}`, 'YouTube Link')}
                className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center justify-between text-sm font-medium text-text-main active:scale-[0.98]"
              >
                <div className="flex items-center gap-2">
                  <Youtube size={14} className="text-danger" />
                  <span>YouTube ID</span>
                </div>
                <span className="font-mono text-[10px] text-text-muted truncate max-w-[120px]">
                  {currentClip.youtube_video_id}
                </span>
              </button>
            )}

            <div className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 border border-border flex items-center justify-between text-sm font-medium text-text-main">
              <div className="flex items-center gap-2">
                {((currentClip.origin || (currentClip.youtube_video_id ? 'youtube' : (currentClip.session_id ? 'phone' : 'youtube'))) === 'phone') ? (
                  <Camera size={14} className="text-text-muted" />
                ) : (
                  <Youtube size={14} className="text-danger" />
                )}
                <span>Origin</span>
              </div>
              <span className="text-xs text-text-muted font-medium">
                {((currentClip.origin || (currentClip.youtube_video_id ? 'youtube' : (currentClip.session_id ? 'phone' : 'youtube'))) === 'phone')
                  ? 'Phone Camera'
                  : 'YouTube Ingest'}
              </span>
            </div>

            <div className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 border border-border flex items-center justify-between text-sm font-medium text-text-main">
              <div className="flex items-center gap-2">
                <Clock size={14} className="text-text-muted" />
                <span>Duration</span>
              </div>
              <span className="font-mono text-xs text-text-muted tabular-nums">
                {formatDuration(currentClip.duration)}
              </span>
            </div>

            <button
              type="button"
              onClick={() => {
                setIsMoreModalOpen(false);
                setViewMode('studio');
              }}
              disabled={offlineReadOnly}
              title={offlineReadOnly ? 'Reconnect to edit this clip' : undefined}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-sm font-medium text-text-main active:scale-[0.98] disabled:opacity-40"
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
              disabled={offlineReadOnly}
              title={offlineReadOnly ? 'Reconnect to publish this clip' : undefined}
              className="w-full min-h-[44px] px-3 rounded-xl bg-surface-100 hover:bg-surface-200 border border-border flex items-center gap-2 text-sm font-medium text-text-main active:scale-[0.98] disabled:opacity-40"
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
                Reject clip?
              </h3>
              <p className="text-sm text-text-secondary mt-1 leading-snug line-clamp-2">
                {clipToReject.title || clipToReject.hook || 'Selected clip'}
              </p>
              <p className="text-xs text-status-danger mt-1.5 leading-snug">Permanently deletes its video and thumbnail.</p>
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
              <span>{isSubmitting ? 'Deleting…' : 'Reject'}</span>
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

      {/* ACTIVITY & NOTIFICATION CENTER BOTTOM SHEET */}
      <NotificationCenterModal
        isOpen={isNotificationCenterOpen}
        onClose={closeNotificationCenter}
        onRefreshAll={fetchClips}
      />
    </div>
  );
};
