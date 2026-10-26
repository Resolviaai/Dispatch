import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  ChevronLeft,
  RotateCcw,
  Play,
  Pause,
  Save,
  Maximize2,
  Undo2,
  Redo2,
  RotateCw,
  Smartphone,
  Monitor,
} from 'lucide-react';
import {
  CaptionTrack,
  CaptionGroup,
  StyleConfig,
  LayoutConfig,
  AnimationConfig,
} from './types';
import { CaptionStudio, ActivePanel } from './CaptionStudio';
import { CaptionTimeline } from './CaptionTimeline';
import { useCaptionHistory } from './useCaptionHistory';
import { applySectionStylesToTrack } from './applyToAll';
import {
  FramedVideoStage,
  InteractiveCaptionCanvas,
  LayoutMode,
  AvailableLayoutMode,
  DEFAULT_PORTRAIT_MODES,
  DEFAULT_LANDSCAPE_MODES,
} from '../screens/ClipsScreen';
import { apiUrl, getAuthHeaders } from '../bridge';

export interface TemplateStudioProps {
  onClose: () => void;
  pcHost?: string;
  onSaved?: () => void;
}

const FACTORY_DEFAULT_STYLE: StyleConfig = {
  font_family: 'Impact',
  font_size: 48,
  font_weight: '900',
  text_transform: 'uppercase',
  letter_spacing: 0,
  line_height: 1.25,
  primary_color: '#FFFFFF',
  highlight_color: '#FFD700',
  highlight_enabled: true,
  outline_enabled: true,
  outline_color: '#000000',
  outline_width: 4.5,
  shadow_enabled: true,
  shadow_color: '#000000',
  shadow_offset_x: 2,
  shadow_offset_y: 3,
  shadow_blur: 4,
  background_enabled: false,
  background_color: '#000000',
  background_padding_x: 12,
  background_padding_y: 6,
  background_corner_radius: 8,
};

const FACTORY_DEFAULT_LAYOUT: LayoutConfig = {
  position_x: 0.5,
  position_y: 0.78,
  scale: 1,
  alignment: 'center',
  max_width: 0.88,
  max_lines: 2,
};

const FACTORY_DEFAULT_ANIMATION: AnimationConfig = {
  type: 'pop',
  duration_ms: 180,
  active_scale: 1.15,
  easing: 'ease_out',
  active_word: {
    type: 'pop',
    active_scale: 1.15,
    duration_ms: 180,
  },
};

function createSampleTemplateTrack(
  style = FACTORY_DEFAULT_STYLE,
  layout = FACTORY_DEFAULT_LAYOUT,
  anim = FACTORY_DEFAULT_ANIMATION
): CaptionTrack {
  return {
    id: 'track_template_sample',
    clip_id: 'template_preview',
    language: 'en',
    default_style: style,
    default_layout: layout,
    default_animation: anim,
    groups: [
      {
        id: 'grp_tpl_1',
        start: 0.0,
        end: 2.6,
        type: 'caption',
        style: { ...style },
        layout: { ...layout },
        animation: { ...anim },
        words: [
          { id: 'w1_1', text: 'This', start: 0.0, end: 0.6 },
          { id: 'w1_2', text: 'is', start: 0.6, end: 1.0 },
          { id: 'w1_3', text: 'how', start: 1.0, end: 1.5 },
          { id: 'w1_4', text: 'Dispatch', start: 1.5, end: 2.5 },
        ],
      },
      {
        id: 'grp_tpl_2',
        start: 2.6,
        end: 5.2,
        type: 'caption',
        style: { ...style },
        layout: { ...layout },
        animation: { ...anim },
        words: [
          { id: 'w2_1', text: 'automatically', start: 2.6, end: 3.4 },
          { id: 'w2_2', text: 'styles', start: 3.4, end: 4.0 },
          { id: 'w2_3', text: 'your', start: 4.0, end: 4.5 },
          { id: 'w2_4', text: 'clips', start: 4.5, end: 5.1 },
        ],
      },
      {
        id: 'grp_tpl_3',
        start: 5.2,
        end: 8.0,
        type: 'caption',
        style: { ...style },
        layout: { ...layout },
        animation: { ...anim },
        words: [
          { id: 'w3_1', text: 'ready', start: 5.2, end: 5.7 },
          { id: 'w3_2', text: 'for', start: 5.7, end: 6.2 },
          { id: 'w3_3', text: 'Shorts', start: 6.2, end: 6.8 },
          { id: 'w3_4', text: '&', start: 6.8, end: 7.2 },
          { id: 'w3_5', text: 'Reels', start: 7.2, end: 7.9 },
        ],
      },
    ],
  };
}

function formatTime(seconds: number): string {
  const s = Math.max(0, seconds || 0);
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  const ms = Math.floor((s % 1) * 10);
  return `${m}:${sec.toString().padStart(2, '0')}.${ms}`;
}

export const TemplateStudio: React.FC<TemplateStudioProps> = ({
  onClose,
  pcHost,
  onSaved,
}) => {
  // --- Workspace & Playback States ---
  const [orientation, setOrientation] = useState<'portrait' | 'landscape'>('portrait');
  const [savedVerticalMode, setSavedVerticalMode] = useState<LayoutMode>('native_916');
  const [savedHorizontalMode, setSavedHorizontalMode] = useState<LayoutMode>('crop_916');
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('native_916');

  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [isVideoSelected, setIsVideoSelected] = useState(false);
  const [activePanel, setActivePanel] = useState<ActivePanel>('none');

  const [isSaving, setIsSaving] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [isFullscreenPreview, setIsFullscreenPreview] = useState(false);
  const [showExitConfirm, setShowExitConfirm] = useState(false);

  // Audio mock states for CaptionStudio props
  const [mainVideoVolume, setMainVideoVolume] = useState(100);
  const [isMainVideoMuted, setIsMainVideoMuted] = useState(true);

  const videoRef = useRef<HTMLVideoElement>(null);
  const bgVideoRef = useRef<HTMLVideoElement>(null);

  // --- Caption History Management ---
  const initialTrack = useMemo(() => createSampleTemplateTrack(), []);
  const { track, setTrack: setCaptionTrack, canUndo, canRedo, undo, redo, resetHistory } = useCaptionHistory(initialTrack);

  const activeGroupId = selectedGroupId || track?.groups[0]?.id || 'grp_tpl_1';

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  };

  // --- Load Initial Saved Template (Cache-First + Network Fallback) ---
  useEffect(() => {
    if (typeof localStorage !== 'undefined') {
      try {
        const cached = localStorage.getItem('dispatch_default_template');
        if (cached) {
          const parsed = JSON.parse(cached);
          applyLoadedTemplate(parsed);
        }
      } catch {}
    }

    const loadBackendTemplate = async () => {
      try {
        const res = await fetch(apiUrl('/api/template/default', pcHost), {
          headers: getAuthHeaders(),
        });
        if (res.ok) {
          const data = await res.json();
          if (data && typeof data === 'object') {
            applyLoadedTemplate(data);
          }
        }
      } catch (err) {
        console.warn('Could not load backend template, relying on cached/factory defaults', err);
      }
    };
    loadBackendTemplate();
  }, [pcHost]);

  const applyLoadedTemplate = (data: any) => {
    if (!data) return;
    const vMode = data.framing?.vertical_mode || 'native_916';
    const hMode = data.framing?.horizontal_mode || 'crop_916';
    setSavedVerticalMode(vMode);
    setSavedHorizontalMode(hMode);
    setLayoutMode(orientation === 'portrait' ? vMode : hMode);

    if (data.captions) {
      const c = data.captions;
      const loadedStyle: StyleConfig = {
        ...FACTORY_DEFAULT_STYLE,
        font_family: c.font_family || 'Impact',
        font_size: Number(c.font_size || 48),
        font_weight: c.font_weight || FACTORY_DEFAULT_STYLE.font_weight,
        letter_spacing: c.letter_spacing ?? FACTORY_DEFAULT_STYLE.letter_spacing,
        line_height: c.line_height ?? FACTORY_DEFAULT_STYLE.line_height,
        primary_color: c.primary_color || '#FFFFFF',
        highlight_color: c.highlight_color || '#FFD700',
        outline_enabled: c.outline_enabled ?? true,
        outline_color: c.outline_color || '#000000',
        outline_width: Number(c.outline_width ?? 4.5),
        shadow_enabled: c.shadow_enabled ?? true,
        shadow_color: c.shadow_color || '#000000',
        shadow_blur: c.shadow_blur ?? 4,
        shadow_offset_x: c.shadow_offset_x ?? 2,
        shadow_offset_y: c.shadow_offset_y ?? 3,
        shadow_distance: c.shadow_distance,
        shadow_angle: c.shadow_angle,
        shadow_opacity: c.shadow_opacity ?? 1.0,
        glow_enabled: c.glow_enabled ?? false,
        glow_color: c.glow_color || '#00F0FF',
        glow_blur: c.glow_blur ?? 12,
        glow_opacity: c.glow_opacity ?? 1.0,
        background_enabled: c.background_enabled ?? false,
        background_mode: c.background_mode || (c.background_enabled ? 'line' : 'none'),
        background_color: c.background_color || '#000000',
        background_opacity: c.background_opacity ?? 1.0,
        background_padding_x: c.background_padding_x ?? 12,
        background_padding_y: c.background_padding_y ?? 6,
        background_corner_radius: c.background_corner_radius ?? 8,
        background_offset_x: c.background_offset_x,
        background_offset_y: c.background_offset_y,
        text_transform: c.all_caps ? 'uppercase' : (c.text_transform || 'none'),
      };
      const loadedLayout: LayoutConfig = {
        ...FACTORY_DEFAULT_LAYOUT,
        position_y: Number(c.position_y ?? 0.78),
      };
      const loadedAnim: AnimationConfig = c.animation_config ? {
        ...FACTORY_DEFAULT_ANIMATION,
        ...c.animation_config,
      } : {
        ...FACTORY_DEFAULT_ANIMATION,
        type: c.animation || 'pop',
      };

      const newTrack = createSampleTemplateTrack(loadedStyle, loadedLayout, loadedAnim);
      resetHistory(newTrack);
    }
  };

  // --- Available Layout Modes based on Orientation ---
  const availableModes: AvailableLayoutMode[] = useMemo(() => {
    return orientation === 'portrait' ? DEFAULT_PORTRAIT_MODES : DEFAULT_LANDSCAPE_MODES;
  }, [orientation]);

  // Handle Orientation Switch
  const handleToggleOrientation = (newOrient: 'portrait' | 'landscape') => {
    setOrientation(newOrient);
    const targetMode = newOrient === 'portrait' ? savedVerticalMode : savedHorizontalMode;
    setLayoutMode(targetMode);
  };

  // Handle Layout Framing Change
  const handleLayoutChange = (newMode: LayoutMode) => {
    setLayoutMode(newMode);
    if (orientation === 'portrait') {
      setSavedVerticalMode(newMode);
    } else {
      setSavedHorizontalMode(newMode);
    }
  };

  // --- Reset to Factory Default ---
  const handleResetToFactory = () => {
    const factory = createSampleTemplateTrack(FACTORY_DEFAULT_STYLE, FACTORY_DEFAULT_LAYOUT, FACTORY_DEFAULT_ANIMATION);
    setCaptionTrack(factory, true);
    setSavedVerticalMode('native_916');
    setSavedHorizontalMode('crop_916');
    setLayoutMode(orientation === 'portrait' ? 'native_916' : 'crop_916');
    showToast('Reset template to factory defaults');
  };

  // --- Save Template Handler ---
  const handleSaveTemplate = async () => {
    if (!track) return;
    setIsSaving(true);
    try {
      const activeGroup = track.groups.find((g) => g.id === activeGroupId) || track.groups[0];
      const style = activeGroup?.style || track.default_style;
      const layout = activeGroup?.layout || track.default_layout;
      const anim = activeGroup?.animation || track.default_animation;

      const payload = {
        name: 'Creator Standard',
        framing: {
          vertical_mode: savedVerticalMode,
          horizontal_mode: savedHorizontalMode,
        },
        captions: {
          preset_id: 'custom',
          font_family: style.font_family || 'Impact',
          font_size: style.font_size || 48,
          font_weight: style.font_weight || '900',
          letter_spacing: style.letter_spacing ?? 0,
          line_height: style.line_height ?? 1.25,
          primary_color: style.primary_color || '#FFFFFF',
          highlight_color: style.highlight_color || '#FFD700',
          outline_enabled: style.outline_enabled ?? true,
          outline_color: style.outline_color || '#000000',
          outline_width: style.outline_width ?? 4.5,
          shadow_enabled: style.shadow_enabled ?? true,
          shadow_color: style.shadow_color || '#000000',
          shadow_blur: style.shadow_blur ?? 4,
          shadow_offset_x: style.shadow_offset_x ?? 2,
          shadow_offset_y: style.shadow_offset_y ?? 3,
          shadow_distance: style.shadow_distance,
          shadow_angle: style.shadow_angle,
          shadow_opacity: style.shadow_opacity ?? 1.0,
          glow_enabled: style.glow_enabled ?? false,
          glow_color: style.glow_color || '#00F0FF',
          glow_blur: style.glow_blur ?? 12,
          glow_opacity: style.glow_opacity ?? 1.0,
          background_enabled: style.background_enabled ?? false,
          background_mode: style.background_mode || 'none',
          background_color: style.background_color || '#000000',
          background_opacity: style.background_opacity ?? 1.0,
          background_padding_x: style.background_padding_x ?? 12,
          background_padding_y: style.background_padding_y ?? 6,
          background_corner_radius: style.background_corner_radius ?? 8,
          background_offset_x: style.background_offset_x,
          background_offset_y: style.background_offset_y,
          animation: anim.type || 'pop',
          animation_config: anim,
          position_y: layout.position_y ?? 0.78,
          max_words_per_line: 3,
          all_caps: style.text_transform === 'uppercase',
        },
        hook: {
          enabled: true,
          style: 'pill',
        },
        publish_mode: 'private',
        platform_targets: ['youtube', 'instagram'],
      };

      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('dispatch_default_template', JSON.stringify(payload));
      }

      await fetch(apiUrl('/api/template/default', pcHost), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(payload),
      });

      showToast('Default template saved');
      onSaved?.();

      setTimeout(() => {
        onClose();
      }, 400);
    } catch (err) {
      console.warn('Failed to save default template:', err);
      showToast('Template saved locally (offline)');
      setTimeout(() => onClose(), 600);
    } finally {
      setIsSaving(false);
    }
  };

  // --- Seamless Continuous Playback Loop & Video Sync ---
  // Offline-first bundled sample video paths
  const sampleVideoUrl = orientation === 'portrait' ? './sample_portrait.mp4' : './sample_landscape.mp4';

  const handleTogglePlay = () => {
    const nextPlay = !isPlaying;
    setIsPlaying(nextPlay);
    if (videoRef.current) {
      if (nextPlay) {
        videoRef.current.play().catch(() => {});
      } else {
        videoRef.current.pause();
      }
    }
  };

  // Video timeupdate handler
  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const t = videoRef.current.currentTime;
    if (t >= 8.0) {
      videoRef.current.currentTime = 0;
      setCurrentTime(0);
      if (isPlaying) {
        videoRef.current.play().catch(() => {});
      }
    } else {
      setCurrentTime(t);
    }
  };

  const handleSeek = (seconds: number) => {
    const clamped = Math.max(0, Math.min(8.0, seconds));
    setCurrentTime(clamped);
    if (videoRef.current) {
      videoRef.current.currentTime = clamped;
    }
  };

  // Synchronized playback engine: triggers play and keeps time smoothly running
  useEffect(() => {
    const v = videoRef.current;
    if (v) {
      v.muted = true;
      if (isPlaying) {
        v.play().catch(() => {});
      } else {
        v.pause();
      }
    }
  }, [sampleVideoUrl, isPlaying]);

  // Robust animation frame timer fallback: guarantees time progression even if video stalls
  useEffect(() => {
    if (!isPlaying) return;
    let lastTime = performance.now();
    let animId: number;

    const tick = (now: number) => {
      const dt = (now - lastTime) / 1000;
      lastTime = now;

      if (videoRef.current && !videoRef.current.paused && videoRef.current.readyState >= 2) {
        const vt = videoRef.current.currentTime;
        if (vt >= 8.0) {
          videoRef.current.currentTime = 0;
          setCurrentTime(0);
        } else {
          setCurrentTime(vt);
        }
      } else {
        // Fallback software timer
        setCurrentTime((prev) => {
          const next = prev + dt;
          if (next >= 8.0) {
            if (videoRef.current) videoRef.current.currentTime = 0;
            return 0;
          }
          return next;
        });
      }

      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animId);
  }, [isPlaying]);

  // --- Group Selection & Group Update ---
  const handleSelectGroup = (id: string | null) => {
    setSelectedGroupId(id);
    if (id) {
      setIsVideoSelected(false);
    }
  };

  const handleUpdateGroup = (
    groupId: string,
    patch: Partial<CaptionGroup>,
    commitToHistory = true
  ) => {
    if (!track) return;
    const nextGroups = track.groups.map((grp) => {
      if (grp.id !== groupId) return grp;
      const next = { ...grp, ...patch };
      if (patch.layout) next.layout = { ...(grp.layout || track.default_layout), ...patch.layout };
      if (patch.style) next.style = { ...(grp.style || track.default_style), ...patch.style };
      if (patch.animation) next.animation = { ...(grp.animation || track.default_animation), ...patch.animation };
      return next;
    });

    const nextTrack: CaptionTrack = {
      ...track,
      groups: nextGroups,
    };
    setCaptionTrack(nextTrack, commitToHistory);
  };

  const handlePreviewAnimation = (start: number) => {
    handleSeek(start);
    if (!isPlaying) {
      setIsPlaying(true);
      if (videoRef.current) {
        videoRef.current.play().catch(() => {});
      }
    }
  };

  const handleApplyLayoutToAll = (sourceGroupId: string) => {
    if (!track) return;
    const src = track.groups.find((g) => g.id === sourceGroupId);
    if (!src || !src.layout) return;
    const { nextTrack } = applySectionStylesToTrack(track, src.id, 'layout');
    setCaptionTrack(nextTrack, true);
    showToast('Applied position & size to all template phrases');
  };

  return (
    <div className="w-full h-full flex flex-col bg-[#0D1014] text-text-main overflow-hidden select-none relative animate-in fade-in duration-200">
      {/* 1. TOP TOOLBAR HEADER */}
      <header className="shrink-0 h-11 px-3 border-b border-[#232832] bg-canvas z-20 flex items-center justify-between gap-2">
        {/* Left: Back button + Plain Title (NO pill/card, clean typography) */}
        <div className="flex items-center gap-2.5 min-w-0">
          <button
            type="button"
            onClick={() => setShowExitConfirm(true)}
            className="w-8 h-8 rounded-lg bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-all"
            title="Back to Settings"
          >
            <ChevronLeft size={18} />
          </button>
          <span className="text-sm font-semibold text-text-main tracking-tight select-none">
            Default Template
          </span>
        </div>

        {/* Right Controls: Reset + Save Template */}
        <div className="flex items-center gap-2 shrink-0">
          {/* Reset to Factory Default */}
          <button
            type="button"
            onClick={handleResetToFactory}
            className="h-7 px-2.5 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-text-muted hover:text-text-main text-[11px] font-medium flex items-center gap-1 active:scale-95 transition-all"
            title="Reset template to factory default"
          >
            <RotateCcw size={11} />
            <span className="hidden sm:inline">Reset</span>
          </button>

          {/* Glowing Hero Save CTA */}
          <button
            type="button"
            disabled={isSaving}
            onClick={handleSaveTemplate}
            className="h-7 px-3 rounded-lg bg-primary hover:bg-primary-hover text-white text-[11px] font-semibold flex items-center gap-1.5 active:scale-95 shadow-hero-glow transition-all disabled:opacity-75"
            title="Save as default template for all newly generated clips"
          >
            {isSaving ? <RotateCw size={11} className="animate-spin" /> : <Save size={11} />}
            <span>Save Template</span>
          </button>
        </div>
      </header>

      {/* Toast Feedback */}
      {toastMessage && (
        <div className="absolute top-12 left-1/2 -translate-x-1/2 z-50 px-3.5 py-1.5 bg-primary/95 text-white text-xs font-medium rounded-full shadow-lg backdrop-blur-sm animate-in fade-in flex items-center gap-1.5 pointer-events-none">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* 2. VIDEO PREVIEW STAGE (Fixed 50% split) */}
      <div
        className="h-[50%] min-h-[200px] max-h-[50%] shrink-0 w-full flex items-center justify-center p-1 bg-black relative overflow-hidden"
        onClick={() => {
          setIsVideoSelected(false);
        }}
      >
        <FramedVideoStage
          sourceVideoUrl={sampleVideoUrl}
          videoUrl={sampleVideoUrl}
          clipStart={0}
          clipEnd={8.0}
          clipDuration={8.0}
          layoutMode={layoutMode}
          sourceOrientation={orientation}
          targetAspect={orientation === 'portrait' ? '9:16' : '16:9'}
          isPlaying={isPlaying}
          onTogglePlay={handleTogglePlay}
          onTimeUpdate={handleTimeUpdate}
          videoRef={videoRef}
          bgVideoRef={bgVideoRef}
          setIsPlaying={setIsPlaying}
          showPlayPauseOverlay={false}
          isCompact={false}
          isDetail={true}
          muted={true}
          onVideoClick={handleTogglePlay}
        >
          <InteractiveCaptionCanvas
            track={track}
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
            onApplyLayoutToAll={handleApplyLayoutToAll}
          />
        </FramedVideoStage>

        {/* Fullscreen Preview Trigger (Shifted to video stage bottom-left per user direction) */}
        <button
          type="button"
          onClick={() => setIsFullscreenPreview(true)}
          className="absolute bottom-3 left-3 z-30 w-8 h-8 rounded-lg bg-black/80 backdrop-blur-md border border-white/20 flex items-center justify-center text-white/80 hover:text-white active:scale-95 transition-all shadow-2xl"
          title="Expand Fullscreen Preview"
          aria-label="Expand Fullscreen Preview"
        >
          <Maximize2 size={13} />
        </button>

        {/* 9:16 vs 16:9 Orientation Switcher (Shifted to video stage bottom-right per user direction) */}
        <div
          className="absolute bottom-3 right-3 z-30 flex p-0.5 rounded-lg bg-black/80 backdrop-blur-md border border-white/20 text-[11px] font-semibold shadow-2xl"
          role="group"
          aria-label="Preview Orientation"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => handleToggleOrientation('portrait')}
            className={`px-2.5 py-1 rounded transition-all flex items-center gap-1.5 ${
              orientation === 'portrait'
                ? 'bg-primary text-white shadow-sm font-bold'
                : 'text-white/70 hover:text-white'
            }`}
            title="Test framing with mobile 9:16 vertical video"
          >
            <Smartphone size={12} />
            <span>9:16</span>
          </button>
          <button
            type="button"
            onClick={() => handleToggleOrientation('landscape')}
            className={`px-2.5 py-1 rounded transition-all flex items-center gap-1.5 ${
              orientation === 'landscape'
                ? 'bg-primary text-white shadow-sm font-bold'
                : 'text-white/70 hover:text-white'
            }`}
            title="Test framing with horizontal 16:9 video"
          >
            <Monitor size={12} />
            <span>16:9</span>
          </button>
        </div>
      </div>

      {/* 3. LOWER WORKSPACE (~50% screen split: Transport + Framing + Timeline + CaptionStudio) */}
      <div className="flex-1 min-h-0 relative flex flex-col bg-[#121418] overflow-hidden">
        {/* CapCut Transport-Control Row */}
        <div className="shrink-0 h-9 px-3 bg-[#13161B] border-y border-[#232832] flex items-center justify-between text-[11px] z-20">
          {/* Leftmost: Timecode */}
          <div className="font-mono tabular-nums text-[10px] sm:text-[11px] font-semibold text-text-main flex items-center gap-1 pl-0.5">
            <span>{formatTime(currentTime)}</span>
            <span className="text-text-muted">/</span>
            <span className="text-text-secondary">0:08.0</span>
          </div>

          {/* Center: Play / Pause */}
          <button
            type="button"
            onClick={handleTogglePlay}
            className="w-7 h-7 rounded-full bg-primary hover:bg-primary-hover text-white flex items-center justify-center shadow-hero-glow active:scale-95 transition-all"
            aria-label={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? <Pause size={12} /> : <Play size={12} fill="currentColor" className="ml-0.5" />}
          </button>

          {/* Right: Undo & Redo buttons */}
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={!canUndo}
              onClick={undo}
              className="w-7 h-7 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center justify-center text-text-secondary hover:text-text-main disabled:opacity-25 active:scale-95 transition-all"
              title="Undo"
            >
              <Undo2 size={12} />
            </button>
            <button
              type="button"
              disabled={!canRedo}
              onClick={redo}
              className="w-7 h-7 rounded-lg bg-surface-200 hover:bg-surface-300 border border-border flex items-center justify-center text-text-secondary hover:text-text-main disabled:opacity-25 active:scale-95 transition-all"
              title="Redo"
            >
              <Redo2 size={12} />
            </button>
          </div>
        </div>

        {activePanel === 'none' ? (
          <>
            {/* Layout Selector Bar (Options centered, no text label per user direction) */}
            <div className="shrink-0 px-3 py-1.5 bg-[#121418] border-b border-[#232832] flex items-center justify-center text-xs overflow-x-auto no-scrollbar gap-1.5">
              {availableModes.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => handleLayoutChange(opt.id)}
                  className={`h-6 px-3 rounded text-[11px] font-medium whitespace-nowrap active:scale-95 transition-all ${
                    layoutMode === opt.id
                      ? 'chip-selected font-semibold'
                      : 'bg-surface-2 text-text-secondary hover:text-text-main'
                  }`}
                  title={opt.description}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            {/* Timeline View (with hideTrimControls=true to eliminate visual clutter) */}
            <div className="flex-1 min-h-[90px] relative overflow-hidden">
              <CaptionTimeline
                track={track}
                currentTime={currentTime}
                duration={8.0}
                isPlaying={isPlaying}
                selectedGroupId={selectedGroupId}
                isVideoSelected={isVideoSelected}
                clipStart={0}
                clipEnd={8.0}
                hideTrimControls={true}
                onSeek={handleSeek}
                onSelectGroup={handleSelectGroup}
                onSelectVideo={() => {
                  setIsVideoSelected(true);
                }}
                onUpdateGroup={handleUpdateGroup}
              />
            </div>

            {/* Docked Contextual Tool Panel / CaptionStudio */}
            <div className="h-12 shrink-0 bg-[#121418] border-t border-[#232832] z-30 pb-[env(safe-area-inset-bottom,0px)]">
              <CaptionStudio
                track={track}
                currentTime={currentTime}
                duration={8.0}
                selectedGroupId={selectedGroupId}
                isVideoSelected={isVideoSelected}
                layoutMode={layoutMode}
                availableLayoutModes={availableModes}
                mainVideoVolume={mainVideoVolume}
                isMainVideoMuted={isMainVideoMuted}
                activePanel={activePanel}
                onActivePanelChange={setActivePanel}
                onUpdateMainVideoAudio={(vol, muted) => {
                  setMainVideoVolume(vol);
                  setIsMainVideoMuted(muted);
                }}
                onSelectGroup={handleSelectGroup}
                onSelectVideo={(sel) => {
                  setIsVideoSelected(sel);
                }}
                onUpdateTrack={(updated, commit = true) => {
                  setCaptionTrack(updated, commit);
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
          <div className="flex-1 min-h-0 w-full h-full bg-[#121418] z-30 pb-[env(safe-area-inset-bottom,0px)] overflow-hidden flex flex-col">
            <CaptionStudio
              track={track}
              currentTime={currentTime}
              duration={8.0}
              selectedGroupId={selectedGroupId || activeGroupId}
              isVideoSelected={isVideoSelected}
              layoutMode={layoutMode}
              availableLayoutModes={availableModes}
              mainVideoVolume={mainVideoVolume}
              isMainVideoMuted={isMainVideoMuted}
              activePanel={activePanel}
              onActivePanelChange={setActivePanel}
              onUpdateMainVideoAudio={(vol, muted) => {
                setMainVideoVolume(vol);
                setIsMainVideoMuted(muted);
              }}
              onSelectGroup={handleSelectGroup}
              onSelectVideo={(sel) => {
                setIsVideoSelected(sel);
              }}
              onUpdateTrack={(updated, commit = true) => {
                setCaptionTrack(updated, commit);
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

      {/* Exit Confirmation Prompt (Save or Discard) */}
      {showExitConfirm && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150">
          <div className="w-full max-w-xs bg-surface-100 border border-border rounded-2xl p-5 shadow-2xl flex flex-col gap-4">
            <div className="flex flex-col gap-1 text-center">
              <h3 className="text-base font-semibold text-text-main">Save Template Changes?</h3>
              <p className="text-xs text-text-muted">Save your styling as default before exiting to Settings.</p>
            </div>

            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={async () => {
                  setShowExitConfirm(false);
                  await handleSaveTemplate();
                }}
                className="w-full h-10 rounded-xl bg-primary hover:bg-primary-hover text-white text-xs font-semibold flex items-center justify-center active:scale-98 transition-all shadow-hero-glow"
              >
                Save & Exit
              </button>

              <button
                type="button"
                onClick={() => {
                  setShowExitConfirm(false);
                  onClose();
                }}
                className="w-full h-10 rounded-xl bg-surface-200 hover:bg-surface-300 border border-border text-text-main text-xs font-medium flex items-center justify-center active:scale-98 transition-all"
              >
                Discard Changes
              </button>

              <button
                type="button"
                onClick={() => setShowExitConfirm(false)}
                className="w-full h-8 text-text-muted hover:text-text-main text-xs font-normal flex items-center justify-center transition-colors"
              >
                Keep Editing
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Fullscreen Preview Overlay */}
      {isFullscreenPreview && (
        <div className="fixed inset-0 z-50 bg-black flex flex-col justify-between p-3 select-none animate-in fade-in duration-150">
          <div className="flex items-center justify-between pb-2">
            <span className="text-[13px] font-semibold text-white">Default Template Preview</span>
            <button
              type="button"
              onClick={() => setIsFullscreenPreview(false)}
              className="w-8 h-8 rounded-lg bg-surface-200 border border-border flex items-center justify-center text-text-secondary hover:text-text-main"
            >
              <ChevronLeft size={16} />
            </button>
          </div>
          <div className="flex-1 flex items-center justify-center overflow-hidden">
            <FramedVideoStage
              sourceVideoUrl={sampleVideoUrl}
              videoUrl={sampleVideoUrl}
              clipStart={0}
              clipEnd={8.0}
              clipDuration={8.0}
              layoutMode={layoutMode}
              sourceOrientation={orientation}
              targetAspect={orientation === 'portrait' ? '9:16' : '16:9'}
              isPlaying={isPlaying}
              onTogglePlay={handleTogglePlay}
              onTimeUpdate={handleTimeUpdate}
              videoRef={videoRef}
              bgVideoRef={bgVideoRef}
              setIsPlaying={setIsPlaying}
              showPlayPauseOverlay={false}
              isDetail={true}
              muted={true}
            >
              <InteractiveCaptionCanvas
                track={track}
                currentTime={currentTime}
                isPlaying={isPlaying}
                selectedGroupId={selectedGroupId}
                layoutMode={layoutMode}
                onSelectGroup={handleSelectGroup}
                onUpdateGroup={handleUpdateGroup}
              />
            </FramedVideoStage>
          </div>
        </div>
      )}
    </div>
  );
};
