import React, { useMemo, useState, useRef } from 'react';
import {
  Scissors,
  Type,
  Sparkles,
  Trash2,
  Clock,
  Copy,
  Layers,
  Wand2,
  ChevronLeft,
  Plus,
  Volume2,
  VolumeX,
  Crop,
  Music,
  Check,
  RotateCcw,
  Image as ImageIcon,
  Sliders,
  RotateCw,
  FlipHorizontal,
  FlipVertical,
  Mic,
} from 'lucide-react';
import { CaptionTrack, CaptionGroup, StyleConfig, AnimationConfig, LayoutConfig, OverlayAnimationConfig } from './types';
import { LayoutMode, AvailableLayoutMode } from '../screens/ClipsScreen';

export type ActivePanel =
  | 'none'
  | 'style'
  | 'animation'
  | 'templates'
  | 'layers'
  | 'framing'
  | 'main_volume'
  | 'layer_volume'
  | 'overlay_animation'
  | 'overlay_transform'
  | 'layer_opacity';

export interface CaptionStudioProps {
  track: CaptionTrack | null;
  currentTime: number;
  duration?: number;
  selectedGroupId: string | null;
  isVideoSelected?: boolean;
  layoutMode?: LayoutMode;
  availableLayoutModes?: AvailableLayoutMode[];
  mainVideoVolume?: number;
  isMainVideoMuted?: boolean;
  activePanel?: ActivePanel;
  onActivePanelChange?: (panel: ActivePanel) => void;
  onUpdateMainVideoAudio?: (volume: number, isMuted: boolean) => void;
  onSelectGroup?: (groupId: string | null) => void;
  onSelectVideo?: (selected: boolean) => void;
  onUpdateTrack: (updated: CaptionTrack) => void;
  onLayoutChange?: (mode: LayoutMode) => void;
  onSeek: (seconds: number) => void;
  canUndo?: boolean;
  canRedo?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
}

export type ToolbarCategory = 'none' | 'edit' | 'audio' | 'text' | 'overlay';
type StyleTab = 'font' | 'color' | 'stroke' | 'glow' | 'background' | 'shadow' | 'alignment';

const FONT_OPTIONS = [
  'Montserrat',
  'Poppins',
  'Impact',
  'Arial Black',
  'Inter',
  'Georgia',
  'Courier New',
  'Trebuchet MS',
  'Oswald'
];

const COLOR_SWATCHES = [
  '#FFFFFF',
  '#000000',
  '#FFE600',
  '#2563EB',
  '#22C55E',
  '#EF4444',
  '#EC4899',
  '#8B5CF6',
  '#06B6D4',
  '#F97316'
];

interface CaptionTemplate {
  id: string;
  name: string;
  category: string;
  style: Partial<StyleConfig>;
  animation?: Partial<AnimationConfig>;
}

const CAPTION_TEMPLATES: CaptionTemplate[] = [
  {
    id: 'viral_yellow',
    name: 'Viral Bold',
    category: 'High Retention',
    style: {
      font_family: 'Impact',
      font_size: 48,
      font_weight: '900',
      text_transform: 'uppercase',
      primary_color: '#FFE600',
      highlight_color: '#FFFFFF',
      outline_enabled: true,
      outline_color: '#000000',
      outline_width: 3,
      shadow_enabled: true,
      shadow_color: '#000000',
      shadow_offset_y: 3,
      shadow_blur: 6,
      background_enabled: false,
    },
    animation: {
      type: 'pop',
      duration_ms: 120,
      active_scale: 1.18,
      easing: 'bounce',
      active_word: { type: 'pop', active_scale: 1.2, duration_ms: 120 },
    },
  },
  {
    id: 'clean_minimal',
    name: 'Clean Pill',
    category: 'Minimalist',
    style: {
      font_family: 'Montserrat',
      font_size: 38,
      font_weight: '700',
      text_transform: 'none',
      primary_color: '#FFFFFF',
      highlight_color: '#60A5FA',
      outline_enabled: false,
      shadow_enabled: false,
      background_enabled: true,
      background_color: 'rgba(0, 0, 0, 0.7)',
      background_padding_x: 14,
      background_padding_y: 8,
      background_corner_radius: 12,
    },
    animation: {
      type: 'fade',
      duration_ms: 140,
      active_scale: 1.05,
      easing: 'ease_out',
      active_word: { type: 'highlight', active_scale: 1.08, duration_ms: 140 },
    },
  },
  {
    id: 'hormozi_punch',
    name: 'Hormozi Green',
    category: 'Creator High-Energy',
    style: {
      font_family: 'Impact',
      font_size: 50,
      font_weight: '900',
      text_transform: 'uppercase',
      primary_color: '#22C55E',
      highlight_color: '#FFE600',
      outline_enabled: true,
      outline_color: '#000000',
      outline_width: 4,
      shadow_enabled: true,
      shadow_color: 'rgba(0,0,0,0.85)',
      shadow_offset_y: 4,
      shadow_blur: 8,
      background_enabled: false,
    },
    animation: {
      type: 'bounce',
      duration_ms: 130,
      active_scale: 1.22,
      easing: 'bounce',
      active_word: { type: 'bounce', active_scale: 1.25, duration_ms: 130 },
    },
  },
  {
    id: 'cyberpunk_glow',
    name: 'Cyberpunk Glow',
    category: 'Stylized',
    style: {
      font_family: 'Impact',
      font_size: 46,
      font_weight: '900',
      text_transform: 'uppercase',
      primary_color: '#06B6D4',
      highlight_color: '#F43F5E',
      outline_enabled: true,
      outline_color: '#000000',
      outline_width: 2,
      glow_enabled: true,
      glow_color: '#06B6D4',
      glow_blur: 16,
      shadow_enabled: true,
      shadow_color: '#0891B2',
      shadow_offset_y: 2,
      shadow_blur: 10,
      background_enabled: false,
    },
    animation: {
      type: 'pop',
      duration_ms: 110,
      active_scale: 1.15,
      easing: 'ease_out',
      active_word: { type: 'karaoke', active_scale: 1.18, duration_ms: 110 },
    },
  },
];

function ValueSlider({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit = '',
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  unit?: string;
}) {
  return (
    <div className="space-y-1 py-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-text-secondary font-medium">{label}</span>
        <span className="font-mono tabular-nums text-text-main font-semibold">
          {Number(value.toFixed(1))}{unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full min-h-[32px] accent-primary cursor-pointer"
        aria-label={label}
      />
    </div>
  );
}

// Flat, cardless CapCut tool button
function ToolButton({
  icon,
  label,
  onClick,
  active = false,
  disabled = false,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  active?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      className={`flex flex-col items-center justify-center min-w-[54px] h-12 px-1 text-center select-none transition-colors active:scale-95 shrink-0 ${
        disabled
          ? 'opacity-30 cursor-not-allowed pointer-events-none'
          : active
          ? 'text-primary font-semibold'
          : 'text-text-secondary hover:text-text-main'
      }`}
    >
      <div className="h-5 flex items-center justify-center mb-0.5">
        {icon}
      </div>
      <span className="text-[10px] leading-tight font-medium truncate max-w-[52px]">
        {label}
      </span>
    </button>
  );
}

export const CaptionStudio: React.FC<CaptionStudioProps> = ({
  track,
  currentTime,
  duration = 40,
  selectedGroupId,
  isVideoSelected = false,
  layoutMode = 'crop_916',
  availableLayoutModes = [],
  mainVideoVolume = 100,
  isMainVideoMuted = false,
  activePanel: propActivePanel,
  onActivePanelChange,
  onUpdateMainVideoAudio,
  onSelectGroup,
  onSelectVideo,
  onUpdateTrack,
  onLayoutChange,
  onSeek: _onSeek,
  canUndo: _canUndo = false,
  canRedo: _canRedo = false,
  onUndo: _onUndo,
  onRedo: _onRedo,
}) => {
  const [activeCategory, setActiveCategory] = useState<ToolbarCategory>('none');
  const [internalActivePanel, setInternalActivePanel] = useState<ActivePanel>('none');
  const activePanel = propActivePanel !== undefined ? propActivePanel : internalActivePanel;
  const setActivePanel = (panel: ActivePanel) => {
    setInternalActivePanel(panel);
    onActivePanelChange?.(panel);
  };
  const [styleTab, setStyleTab] = useState<StyleTab>('font');
  const [applyToAll, setApplyToAll] = useState<boolean>(false);
  const [overlayAnimTab, setOverlayAnimTab] = useState<'enter' | 'exit'>('enter');
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Active selected group
  const currentGroup = useMemo(() => {
    if (!track || !track.groups || track.groups.length === 0) return null;
    if (selectedGroupId) {
      return track.groups.find((g) => g.id === selectedGroupId) || null;
    }
    return null;
  }, [track, selectedGroupId]);

  // Derive current effective category:
  // If layer selected, it automatically determines category (overlay / audio / text)
  // If video selected, it's 'edit'
  // Else uses activeCategory
  const effectiveCategory: ToolbarCategory = useMemo(() => {
    if (currentGroup) {
      if (currentGroup.type === 'overlay') return 'overlay';
      if (currentGroup.type === 'audio' || currentGroup.type === 'voiceover') return 'audio';
      return 'text';
    }
    if (isVideoSelected) return 'edit';
    return activeCategory;
  }, [currentGroup, isVideoSelected, activeCategory]);

  const currentStyle = currentGroup?.style || track?.default_style;
  const currentLayout = currentGroup?.layout || track?.default_layout;
  const currentAnimation = currentGroup?.animation || track?.default_animation;

  // Patch function for captions/text
  const patch = (
    stylePatch?: Partial<StyleConfig>,
    layoutPatch?: Partial<LayoutConfig>,
    animationPatch?: Partial<AnimationConfig>
  ) => {
    if (!track) return;
    const targetGroup = currentGroup || track.groups[0];
    if (!targetGroup) return;

    const nextTrack: CaptionTrack = {
      ...track,
      default_style: stylePatch ? { ...track.default_style, ...stylePatch } : track.default_style,
      default_layout: layoutPatch ? { ...track.default_layout, ...layoutPatch } : track.default_layout,
      default_animation: animationPatch ? { ...track.default_animation, ...animationPatch } : track.default_animation,
      groups: track.groups.map((grp) => {
        if (!applyToAll && grp.id !== targetGroup.id) return grp;
        return {
          ...grp,
          style: stylePatch ? { ...(grp.style || track.default_style), ...stylePatch } : grp.style,
          layout: layoutPatch ? { ...(grp.layout || track.default_layout), ...layoutPatch } : grp.layout,
          animation: animationPatch
            ? { ...(grp.animation || track.default_animation), ...animationPatch }
            : grp.animation,
        };
      }),
    };

    onUpdateTrack(nextTrack);
  };

  // Return to primary toolbar: deselects layer/video and restores primary toolbar
  const handleBackToPrimary = () => {
    setActiveCategory('none');
    setActivePanel('none');
    onSelectGroup?.(null);
    onSelectVideo?.(false);
  };

  // Add new text layer
  const handleAddText = () => {
    if (!track) return;
    const newId = `grp_${Date.now()}`;
    const start = Number(currentTime.toFixed(2));
    const end = Number(Math.min(duration, start + 3.0).toFixed(2));
    const newGroup: CaptionGroup = {
      id: newId,
      start,
      end,
      type: 'text',
      words: [
        { id: `w_${Date.now()}_0`, text: 'New', start, end: start + 1.0 },
        { id: `w_${Date.now()}_1`, text: 'Text', start: start + 1.0, end },
      ],
      layout: { ...(track.default_layout || { position_x: 0.5, position_y: 0.5, alignment: 'center', max_width: 0.88, max_lines: 2 }) },
      style: { ...(track.default_style || { font_family: 'Montserrat', font_size: 44, font_weight: '900', text_transform: 'none', letter_spacing: 0, line_height: 1.2, primary_color: '#FFFFFF', highlight_color: '#FFE600', outline_enabled: true, outline_color: '#000000', outline_width: 2, shadow_enabled: false, background_enabled: false }) },
      animation: { ...(track.default_animation || { type: 'pop', duration_ms: 100, active_scale: 1.15, easing: 'ease_out' }) },
    };

    onUpdateTrack({
      ...track,
      groups: [...track.groups, newGroup],
    });
    onSelectGroup?.(newId);
    setActiveCategory('text');
  };

  // Native file picker selection for Add Overlay
  const handleOverlayFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !track) return;
    const isVideo = file.type.startsWith('video');
    const objectUrl = URL.createObjectURL(file);
    const newId = `ovl_${Date.now()}`;
    const start = Number(currentTime.toFixed(2));
    const end = Number(Math.min(duration, start + (isVideo ? 8.0 : 4.0)).toFixed(2));
    const cleanName = file.name.replace(/\.[^/.]+$/, '').slice(0, 16);

    const newOverlay: CaptionGroup = {
      id: newId,
      start,
      end,
      type: 'overlay',
      overlay_type: isVideo ? 'video' : 'image',
      overlay_url: objectUrl,
      label: cleanName || (isVideo ? 'Video Clip' : 'Image Overlay'),
      opacity: 100,
      volume: isVideo ? 100 : 0,
      is_muted: false,
      layout: {
        position_x: 0.5,
        position_y: 0.5,
        scale: 0.8,
        rotation: 0,
        alignment: 'center',
        max_width: 0.8,
        max_lines: 1,
      },
      words: [{ id: `w_${Date.now()}`, text: cleanName || 'Overlay', start, end }],
    };

    onUpdateTrack({
      ...track,
      groups: [...track.groups, newOverlay],
    });
    onSelectGroup?.(newId);
    setActiveCategory('overlay');
    // Clear value so user can pick same file again if desired
    e.target.value = '';
  };

  // Add BGM audio layer
  const handleImportAudio = (label = 'BGM Track') => {
    if (!track) return;
    const newId = `aud_${Date.now()}`;
    const start = Number(currentTime.toFixed(2));
    const end = Number(Math.min(duration, start + 15.0).toFixed(2));
    const newAudio: CaptionGroup = {
      id: newId,
      start,
      end,
      type: 'audio',
      label,
      volume: 100,
      is_muted: false,
      words: [{ id: `w_${Date.now()}`, text: label, start, end }],
    };
    onUpdateTrack({
      ...track,
      groups: [...track.groups, newAudio],
    });
    onSelectGroup?.(newId);
    setActiveCategory('audio');
  };

  // Add Voiceover audio layer
  const handleRecordVoiceover = () => {
    if (!track) return;
    const newId = `voc_${Date.now()}`;
    const start = Number(currentTime.toFixed(2));
    const end = Number(Math.min(duration, start + 5.0).toFixed(2));
    const newVoice: CaptionGroup = {
      id: newId,
      start,
      end,
      type: 'voiceover',
      label: 'Voiceover',
      volume: 100,
      is_muted: false,
      words: [{ id: `w_${Date.now()}`, text: 'Voiceover', start, end }],
    };
    onUpdateTrack({
      ...track,
      groups: [...track.groups, newVoice],
    });
    onSelectGroup?.(newId);
    setActiveCategory('audio');
  };

  // Split layer at playhead
  const handleSplit = () => {
    if (!track || !currentGroup) return;
    if (currentTime <= currentGroup.start + 0.1 || currentTime >= currentGroup.end - 0.1) return;

    const splitTime = Number(currentTime.toFixed(2));
    const isOverlay = currentGroup.type === 'overlay';
    const firstGroup: CaptionGroup = {
      ...currentGroup,
      end: splitTime,
      words: (currentGroup.words || []).filter((w) => w.start < splitTime),
    };
    if (!firstGroup.words || firstGroup.words.length === 0) {
      firstGroup.words = [{ id: `w_${Date.now()}_a`, text: currentGroup.label || 'Segment 1', start: currentGroup.start, end: splitTime }];
    }

    const secondId = `${isOverlay ? 'ovl' : 'grp'}_${Date.now()}`;
    const secondGroup: CaptionGroup = {
      ...currentGroup,
      id: secondId,
      start: splitTime,
      words: (currentGroup.words || []).filter((w) => w.start >= splitTime).map((w, idx) => ({ ...w, id: `w_${Date.now()}_b_${idx}` })),
    };
    if (!secondGroup.words || secondGroup.words.length === 0) {
      secondGroup.words = [{ id: `w_${Date.now()}_b`, text: currentGroup.label || 'Segment 2', start: splitTime, end: currentGroup.end }];
    }

    onUpdateTrack({
      ...track,
      groups: track.groups.flatMap((g) => (g.id === currentGroup.id ? [firstGroup, secondGroup] : [g])),
    });
    onSelectGroup?.(secondId);
  };

  // Duplicate layer
  const handleDuplicate = () => {
    if (!track || !currentGroup) return;
    const isOverlay = currentGroup.type === 'overlay';
    const newId = `${isOverlay ? 'ovl' : 'grp'}_${Date.now()}`;
    const nextStart = Number(Math.min(duration - 0.5, currentGroup.start + 0.3).toFixed(2));
    const nextEnd = Number(Math.min(duration, currentGroup.end + 0.3).toFixed(2));

    const dup: CaptionGroup = {
      ...currentGroup,
      id: newId,
      start: nextStart,
      end: nextEnd,
      words: (currentGroup.words || []).map((w, idx) => ({ ...w, id: `w_${Date.now()}_${idx}` })),
    };

    onUpdateTrack({
      ...track,
      groups: [...track.groups, dup],
    });
    onSelectGroup?.(newId);
  };

  // Delete layer
  const handleDelete = () => {
    if (!track || !currentGroup) return;
    onUpdateTrack({
      ...track,
      groups: track.groups.filter((g) => g.id !== currentGroup.id),
    });
    onSelectGroup?.(null);
  };

  // Lasting text across duration
  const handleLastingText = () => {
    if (!track || !currentGroup) return;
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) => (g.id === currentGroup.id ? { ...g, start: 0, end: Number(duration.toFixed(2)) } : g)),
    });
  };

  // Reorder stacking layers
  const handleMoveLayer = (direction: 'up' | 'down') => {
    if (!track || !currentGroup) return;
    const idx = track.groups.findIndex((g) => g.id === currentGroup.id);
    if (idx === -1) return;
    const targetIdx = direction === 'up' ? idx + 1 : idx - 1;
    if (targetIdx < 0 || targetIdx >= track.groups.length) return;

    const copy = [...track.groups];
    const [removed] = copy.splice(idx, 1);
    copy.splice(targetIdx, 0, removed);
    onUpdateTrack({ ...track, groups: copy });
  };

  // Overlay Transform Actions
  const handleRotate90 = () => {
    if (!currentGroup || !track) return;
    const curRot = currentGroup.layout?.rotation || 0;
    const nextRot = (curRot + 90) % 360;
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) =>
        g.id === currentGroup.id
          ? { ...g, layout: { ...(g.layout || track.default_layout), rotation: nextRot } }
          : g
      ),
    });
  };

  const handleToggleFlipH = () => {
    if (!currentGroup || !track) return;
    const nextFlipH = !currentGroup.flip_h;
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) => (g.id === currentGroup.id ? { ...g, flip_h: nextFlipH } : g)),
    });
  };

  const handleToggleFlipV = () => {
    if (!currentGroup || !track) return;
    const nextFlipV = !currentGroup.flip_v;
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) => (g.id === currentGroup.id ? { ...g, flip_v: nextFlipV } : g)),
    });
  };

  const handleResetTransform = () => {
    if (!currentGroup || !track) return;
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) =>
        g.id === currentGroup.id
          ? {
              ...g,
              flip_h: false,
              flip_v: false,
              layout: {
                ...(g.layout || track.default_layout),
                scale: 1.0,
                rotation: 0,
                position_x: 0.5,
                position_y: 0.5,
              },
            }
          : g
      ),
    });
  };

  const handleUpdateOverlayAnimation = (patchAnim: Partial<OverlayAnimationConfig>) => {
    if (!currentGroup || !track) return;
    const curAnim = currentGroup.overlay_animation || {};
    onUpdateTrack({
      ...track,
      groups: track.groups.map((g) =>
        g.id === currentGroup.id ? { ...g, overlay_animation: { ...curAnim, ...patchAnim } } : g
      ),
    });
  };

  // =========================================================================
  // EXPANDED WORKSPACE PANELS (Edge-to-Edge, Continuous Dark Surface)
  // =========================================================================

  // 1. OVERLAY TRANSFORM PANEL
  if (activePanel === 'overlay_transform' && currentGroup) {
    const curLayout = currentGroup.layout || track?.default_layout || { scale: 1.0, rotation: 0 };
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Overlay Transform</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2.5 py-1">
          {/* Quick Buttons: Rotate 90, Flip H, Flip V, Reset */}
          <div className="grid grid-cols-4 gap-1.5">
            <button
              type="button"
              onClick={handleRotate90}
              className="h-9 rounded-lg bg-surface-200 border border-border flex flex-col items-center justify-center text-[11px] font-medium text-text-main hover:bg-surface-300 active:scale-95"
            >
              <RotateCw size={13} className="text-primary mb-0.5" />
              <span>Rotate 90°</span>
            </button>
            <button
              type="button"
              onClick={handleToggleFlipH}
              className={`h-9 rounded-lg border flex flex-col items-center justify-center text-[11px] font-medium transition-colors active:scale-95 ${
                currentGroup.flip_h ? 'bg-primary/20 border-primary text-white' : 'bg-surface-200 border-border text-text-main'
              }`}
            >
              <FlipHorizontal size={13} className="mb-0.5" />
              <span>Flip H</span>
            </button>
            <button
              type="button"
              onClick={handleToggleFlipV}
              className={`h-9 rounded-lg border flex flex-col items-center justify-center text-[11px] font-medium transition-colors active:scale-95 ${
                currentGroup.flip_v ? 'bg-primary/20 border-primary text-white' : 'bg-surface-200 border-border text-text-main'
              }`}
            >
              <FlipVertical size={13} className="mb-0.5" />
              <span>Flip V</span>
            </button>
            <button
              type="button"
              onClick={handleResetTransform}
              className="h-9 rounded-lg bg-surface-200 border border-border flex flex-col items-center justify-center text-[11px] font-medium text-text-secondary hover:text-text-main active:scale-95"
            >
              <RotateCcw size={13} className="mb-0.5" />
              <span>Reset</span>
            </button>
          </div>

          <ValueSlider
            label="Scale / Size"
            value={curLayout.scale ?? 1.0}
            min={0.2}
            max={2.5}
            step={0.05}
            unit="x"
            onChange={(val) => {
              if (!track) return;
              onUpdateTrack({
                ...track,
                groups: track.groups.map((g) =>
                  g.id === currentGroup.id ? { ...g, layout: { ...(g.layout || track.default_layout), scale: val } } : g
                ),
              });
            }}
          />

          <ValueSlider
            label="Rotation"
            value={curLayout.rotation ?? 0}
            min={-180}
            max={180}
            step={1}
            unit="°"
            onChange={(val) => {
              if (!track) return;
              onUpdateTrack({
                ...track,
                groups: track.groups.map((g) =>
                  g.id === currentGroup.id ? { ...g, layout: { ...(g.layout || track.default_layout), rotation: val } } : g
                ),
              });
            }}
          />
        </div>
      </div>
    );
  }

  // 2. OVERLAY ANIMATION PANEL
  if (activePanel === 'overlay_animation' && currentGroup) {
    const curAnim = currentGroup.overlay_animation || {};
    const enterChoices = [
      { id: 'none', label: 'None' },
      { id: 'fade', label: 'Fade In' },
      { id: 'pop', label: 'Pop / Zoom' },
      { id: 'scale', label: 'Scale Up' },
      { id: 'slide_up', label: 'Slide Up' },
      { id: 'slide_down', label: 'Slide Down' },
      { id: 'slide_left', label: 'Slide Left' },
      { id: 'slide_right', label: 'Slide Right' },
    ];
    const exitChoices = [
      { id: 'none', label: 'None' },
      { id: 'fade', label: 'Fade Out' },
      { id: 'scale_down', label: 'Scale Down' },
      { id: 'slide_up', label: 'Slide Up' },
      { id: 'slide_down', label: 'Slide Down' },
      { id: 'slide_left', label: 'Slide Left' },
      { id: 'slide_right', label: 'Slide Right' },
    ];

    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setOverlayAnimTab('enter')}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-colors ${
                overlayAnimTab === 'enter' ? 'bg-primary text-white' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              In (Entrance)
            </button>
            <button
              type="button"
              onClick={() => setOverlayAnimTab('exit')}
              className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-colors ${
                overlayAnimTab === 'exit' ? 'bg-primary text-white' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              Out (Exit)
            </button>
          </div>

          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2 py-1">
          {overlayAnimTab === 'enter' ? (
            <>
              <div className="grid grid-cols-4 gap-1.5">
                {enterChoices.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => handleUpdateOverlayAnimation({ enter: c.id as any })}
                    className={`h-9 rounded-lg border text-[11px] font-medium transition-colors ${
                      (curAnim.enter || 'none') === c.id
                        ? 'bg-primary/20 border-primary text-white font-bold'
                        : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              <ValueSlider
                label="Entrance Duration"
                value={(curAnim.enter_duration_ms || 400) / 1000}
                min={0.1}
                max={2.0}
                step={0.1}
                unit="s"
                onChange={(val) => handleUpdateOverlayAnimation({ enter_duration_ms: Math.round(val * 1000) })}
              />
            </>
          ) : (
            <>
              <div className="grid grid-cols-4 gap-1.5">
                {exitChoices.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => handleUpdateOverlayAnimation({ exit: c.id as any })}
                    className={`h-9 rounded-lg border text-[11px] font-medium transition-colors ${
                      (curAnim.exit || 'none') === c.id
                        ? 'bg-primary/20 border-primary text-white font-bold'
                        : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                    }`}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              <ValueSlider
                label="Exit Duration"
                value={(curAnim.exit_duration_ms || 400) / 1000}
                min={0.1}
                max={2.0}
                step={0.1}
                unit="s"
                onChange={(val) => handleUpdateOverlayAnimation({ exit_duration_ms: Math.round(val * 1000) })}
              />
            </>
          )}
        </div>
      </div>
    );
  }

  // 3. LAYER OPACITY PANEL
  if (activePanel === 'layer_opacity' && currentGroup) {
    const curOpacity = currentGroup.opacity ?? 100;
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Overlay Opacity</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 flex flex-col justify-center py-4">
          <ValueSlider
            label="Opacity"
            value={curOpacity}
            min={0}
            max={100}
            step={1}
            unit="%"
            onChange={(val) => {
              if (!track) return;
              onUpdateTrack({
                ...track,
                groups: track.groups.map((g) => (g.id === currentGroup.id ? { ...g, opacity: val } : g)),
              });
            }}
          />
        </div>
      </div>
    );
  }

  // 4. LAYER VOLUME PANEL (For Video Overlay or Audio Layers)
  if (activePanel === 'layer_volume' && currentGroup) {
    const layerVol = currentGroup.volume ?? 100;
    const layerMuted = currentGroup.is_muted ?? false;

    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Layer Audio</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2 py-1">
          <div className="flex items-center justify-between py-1">
            <button
              type="button"
              onClick={() => {
                if (!track) return;
                onUpdateTrack({
                  ...track,
                  groups: track.groups.map((g) =>
                    g.id === currentGroup.id ? { ...g, is_muted: !layerMuted } : g
                  ),
                });
              }}
              className={`p-2 rounded-lg border flex items-center gap-1.5 text-[11px] font-semibold ${
                layerMuted ? 'bg-red-500/20 border-red-500 text-red-300' : 'bg-surface-200 border-border text-text-main'
              }`}
            >
              {layerMuted ? <VolumeX size={15} /> : <Volume2 size={15} />}
              <span>{layerMuted ? 'Muted' : 'Unmuted'}</span>
            </button>
          </div>

          <ValueSlider
            label="Layer Volume"
            value={layerVol}
            min={0}
            max={200}
            unit="%"
            onChange={(val) => {
              if (!track) return;
              onUpdateTrack({
                ...track,
                groups: track.groups.map((g) =>
                  g.id === currentGroup.id ? { ...g, volume: val } : g
                ),
              });
            }}
          />
        </div>
      </div>
    );
  }

  // 5. MAIN VIDEO VOLUME PANEL
  if (activePanel === 'main_volume') {
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Main Video Audio</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2 py-1">
          <div className="flex items-center justify-between py-1">
            <button
              type="button"
              onClick={() => onUpdateMainVideoAudio?.(mainVideoVolume, !isMainVideoMuted)}
              className={`p-2 rounded-lg border flex items-center gap-1.5 text-[11px] font-semibold ${
                isMainVideoMuted ? 'bg-red-500/20 border-red-500 text-red-300' : 'bg-surface-200 border-border text-text-main'
              }`}
            >
              {isMainVideoMuted ? <VolumeX size={15} /> : <Volume2 size={15} />}
              <span>{isMainVideoMuted ? 'Muted' : 'Unmuted'}</span>
            </button>
          </div>

          <ValueSlider
            label="Video Volume"
            value={mainVideoVolume}
            min={0}
            max={200}
            unit="%"
            onChange={(val) => onUpdateMainVideoAudio?.(val, isMainVideoMuted)}
          />
        </div>
      </div>
    );
  }

  // 6. STYLE PANEL
  if (activePanel === 'style') {
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>

          <div className="flex items-center gap-1 overflow-x-auto no-scrollbar">
            {(['font', 'color', 'stroke', 'glow', 'background', 'shadow', 'alignment'] as StyleTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setStyleTab(tab)}
                className={`px-2 py-1 rounded text-[11px] font-semibold capitalize transition-all ${
                  styleTab === tab ? 'bg-primary text-white' : 'text-text-muted hover:text-text-secondary'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar">
          {styleTab === 'font' && (
            <div className="grid grid-cols-4 gap-1.5 py-1">
              {FONT_OPTIONS.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => patch({ font_family: f })}
                  className={`h-9 rounded-lg border text-[11px] font-medium flex items-center justify-center truncate px-1 transition-all ${
                    currentStyle?.font_family === f
                      ? 'border-primary bg-primary/20 text-white font-bold'
                      : 'border-border bg-surface-200 text-text-secondary hover:text-text-main'
                  }`}
                  style={{ fontFamily: f }}
                >
                  {f}
                </button>
              ))}
            </div>
          )}

          {styleTab === 'color' && (
            <div className="space-y-2 py-1">
              <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-1">
                {COLOR_SWATCHES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => patch({ primary_color: c })}
                    className={`w-7 h-7 rounded-full shrink-0 border-2 transition-transform active:scale-90 ${
                      currentStyle?.primary_color === c ? 'border-white scale-110 shadow-lg' : 'border-transparent'
                    }`}
                    style={{ backgroundColor: c }}
                    title={c}
                  />
                ))}
              </div>
              <ValueSlider
                label="Font Size"
                value={currentStyle?.font_size || 44}
                min={20}
                max={80}
                unit="px"
                onChange={(val) => patch({ font_size: val })}
              />
            </div>
          )}

          {styleTab === 'stroke' && (
            <div className="space-y-2 py-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-text-secondary">Enable Text Outline</span>
                <input
                  type="checkbox"
                  checked={currentStyle?.outline_enabled ?? true}
                  onChange={(e) => patch({ outline_enabled: e.target.checked })}
                  className="w-4 h-4 accent-primary cursor-pointer"
                />
              </div>
              {currentStyle?.outline_enabled && (
                <>
                  <ValueSlider
                    label="Stroke Width"
                    value={currentStyle?.outline_width || 3}
                    min={1}
                    max={10}
                    unit="px"
                    onChange={(val) => patch({ outline_width: val })}
                  />
                  <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-1">
                    {COLOR_SWATCHES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => patch({ outline_color: c })}
                        className={`w-6 h-6 rounded-full shrink-0 border-2 ${
                          currentStyle?.outline_color === c ? 'border-white scale-110' : 'border-transparent'
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {styleTab === 'glow' && (
            <div className="space-y-2 py-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-text-secondary">Enable Neon Glow</span>
                <input
                  type="checkbox"
                  checked={currentStyle?.glow_enabled ?? false}
                  onChange={(e) => patch({ glow_enabled: e.target.checked })}
                  className="w-4 h-4 accent-primary cursor-pointer"
                />
              </div>
              {currentStyle?.glow_enabled && (
                <>
                  <ValueSlider
                    label="Glow Radius"
                    value={currentStyle?.glow_blur || 12}
                    min={4}
                    max={30}
                    unit="px"
                    onChange={(val) => patch({ glow_blur: val })}
                  />
                  <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-1">
                    {COLOR_SWATCHES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => patch({ glow_color: c })}
                        className={`w-6 h-6 rounded-full shrink-0 border-2 ${
                          currentStyle?.glow_color === c ? 'border-white scale-110' : 'border-transparent'
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {styleTab === 'background' && (
            <div className="space-y-2 py-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-text-secondary">Background Pill</span>
                <input
                  type="checkbox"
                  checked={currentStyle?.background_enabled ?? false}
                  onChange={(e) => patch({ background_enabled: e.target.checked })}
                  className="w-4 h-4 accent-primary cursor-pointer"
                />
              </div>
              {currentStyle?.background_enabled && (
                <>
                  <ValueSlider
                    label="Corner Radius"
                    value={currentStyle?.background_corner_radius || 8}
                    min={0}
                    max={24}
                    unit="px"
                    onChange={(val) => patch({ background_corner_radius: val })}
                  />
                  <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-1">
                    {COLOR_SWATCHES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => patch({ background_color: c })}
                        className={`w-6 h-6 rounded-full shrink-0 border-2 ${
                          currentStyle?.background_color === c ? 'border-white scale-110' : 'border-transparent'
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {styleTab === 'shadow' && (
            <div className="space-y-2 py-1">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-text-secondary">Drop Shadow</span>
                <input
                  type="checkbox"
                  checked={currentStyle?.shadow_enabled ?? false}
                  onChange={(e) => patch({ shadow_enabled: e.target.checked })}
                  className="w-4 h-4 accent-primary cursor-pointer"
                />
              </div>
              {currentStyle?.shadow_enabled && (
                <ValueSlider
                  label="Shadow Blur"
                  value={currentStyle?.shadow_blur || 6}
                  min={1}
                  max={20}
                  unit="px"
                  onChange={(val) => patch({ shadow_blur: val })}
                />
              )}
            </div>
          )}

          {styleTab === 'alignment' && (
            <div className="grid grid-cols-3 gap-2 py-1">
              {(['left', 'center', 'right'] as const).map((align) => (
                <button
                  key={align}
                  type="button"
                  onClick={() => patch(undefined, { alignment: align })}
                  className={`h-9 rounded-lg border text-[11px] font-medium capitalize transition-colors ${
                    currentLayout?.alignment === align
                      ? 'bg-primary/20 border-primary text-white font-bold'
                      : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                  }`}
                >
                  {align}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // 7. ANIMATION PANEL (Captions / Text)
  if (activePanel === 'animation') {
    const animTypes = [
      { id: 'pop', label: 'Pop & Bounce' },
      { id: 'karaoke', label: 'Word Karaoke' },
      { id: 'bounce', label: 'Playful Jump' },
      { id: 'fade', label: 'Smooth Fade' },
      { id: 'none', label: 'Static' },
    ];

    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Text Animation</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2 py-1">
          <div className="grid grid-cols-3 gap-1.5">
            {animTypes.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => patch(undefined, undefined, { type: a.id as any })}
                className={`h-9 rounded-lg border text-[11px] font-medium transition-colors ${
                  currentAnimation?.type === a.id
                    ? 'bg-primary/20 border-primary text-white font-bold'
                    : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>

          <ValueSlider
            label="Duration"
            value={currentAnimation?.duration_ms || 120}
            min={50}
            max={350}
            step={10}
            unit="ms"
            onChange={(val) => patch(undefined, undefined, { duration_ms: val })}
          />

          <ValueSlider
            label="Scale Impact"
            value={currentAnimation?.active_scale || 1.15}
            min={1.05}
            max={1.4}
            step={0.02}
            unit="x"
            onChange={(val) => patch(undefined, undefined, { active_scale: val })}
          />
        </div>
      </div>
    );
  }

  // 8. TEMPLATES / PRESETS PANEL
  if (activePanel === 'templates') {
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Caption Presets</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar py-1">
          <div className="grid grid-cols-2 gap-2">
            {CAPTION_TEMPLATES.map((tmpl) => (
              <button
                key={tmpl.id}
                type="button"
                onClick={() => {
                  patch(tmpl.style, undefined, tmpl.animation);
                }}
                className="p-2.5 rounded-xl border border-border bg-surface-200 hover:bg-surface-300 flex flex-col items-center justify-center gap-1 transition-all active:scale-95"
              >
                <span
                  className="text-[13px] font-bold"
                  style={{
                    fontFamily: tmpl.style.font_family || 'Impact',
                    color: tmpl.style.primary_color || '#FFF',
                  }}
                >
                  {tmpl.name}
                </span>
                <span className="text-[10px] text-text-muted">{tmpl.category}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // 9. LAYERS REORDER PANEL
  if (activePanel === 'layers') {
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Layer Stacking Order</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar space-y-2 py-1">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => handleMoveLayer('up')}
              className="h-10 rounded-xl bg-surface-200 border border-border flex items-center justify-center gap-1.5 text-[11px] font-medium text-text-main hover:bg-surface-300 active:scale-95"
            >
              <span>Bring Forward</span>
            </button>
            <button
              type="button"
              onClick={() => handleMoveLayer('down')}
              className="h-10 rounded-xl bg-surface-200 border border-border flex items-center justify-center gap-1.5 text-[11px] font-medium text-text-main hover:bg-surface-300 active:scale-95"
            >
              <span>Send Backward</span>
            </button>
          </div>

          <div className="space-y-1 pt-1">
            {track?.groups.map((grp, idx) => {
              const isSelected = grp.id === currentGroup?.id;
              const label = grp.label || (grp.words || []).map((w) => w.text).join(' ') || `Layer ${idx + 1}`;
              return (
                <div
                  key={grp.id}
                  onClick={() => onSelectGroup?.(grp.id)}
                  className={`p-2 rounded-lg flex items-center justify-between text-[11px] cursor-pointer transition-colors ${
                    isSelected ? 'bg-primary/20 border border-primary text-white font-bold' : 'bg-surface-200 text-text-secondary'
                  }`}
                >
                  <span className="truncate max-w-[200px]">{label}</span>
                  <span className="text-[10px] text-text-muted">
                    {(grp.end - grp.start).toFixed(1)}s
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // 10. FRAMING PANEL
  if (activePanel === 'framing') {
    return (
      <div className="w-full h-full bg-[#16191E] flex flex-col p-3 space-y-2 select-none overflow-hidden animate-in fade-in duration-150">
        <div className="flex items-center justify-between pb-1.5 border-b border-[#232832] shrink-0">
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="flex items-center gap-1 text-[12px] font-semibold text-text-secondary hover:text-text-main"
          >
            <ChevronLeft size={16} />
            <span>Tools</span>
          </button>
          <span className="text-[12px] font-semibold text-text-main">Video Framing & Crop</span>
          <button
            type="button"
            onClick={() => setActivePanel('none')}
            className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-white active:scale-95"
            title="Done"
          >
            <Check size={14} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto no-scrollbar">
          <div className="grid grid-cols-2 gap-2 py-1">
            {availableLayoutModes.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => onLayoutChange?.(m.id)}
                className={`p-2.5 rounded-xl border flex flex-col items-center justify-center gap-1 transition-all ${
                  layoutMode === m.id
                    ? 'bg-primary/20 border-primary text-white font-bold'
                    : 'bg-surface-200 border-border text-text-secondary hover:text-text-main'
                }`}
              >
                <span className="text-[12px] font-semibold">{m.label}</span>
                <span className="text-[10px] text-text-muted">{m.target_aspect}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // =========================================================================
  // SINGLE-ROW CONTEXTUAL TOOLBARS (Continuous Dark Surface, CapCut Model)
  // Exactly ONE row visible at a time. Zero stacked toolbars.
  // =========================================================================

  return (
    <div className="w-full bg-[#121418] relative select-none">
      {/* Hidden file input for native image/video overlay picker */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,video/*"
        className="hidden"
        onChange={handleOverlayFileSelect}
      />

      {/* CONTEXT A: PRIMARY TOOLBAR (No category / layer open) - Exactly 4 Tools */}
      {effectiveCategory === 'none' && (
        <div className="w-full h-12 flex items-center justify-around px-1">
          <ToolButton
            icon={<Scissors size={18} />}
            label="Edit"
            onClick={() => {
              onSelectVideo?.(true);
              setActiveCategory('edit');
            }}
          />
          <ToolButton
            icon={<Music size={18} />}
            label="Audio"
            onClick={() => setActiveCategory('audio')}
          />
          <ToolButton
            icon={<Type size={18} />}
            label="Text"
            onClick={() => setActiveCategory('text')}
          />
          <ToolButton
            icon={<ImageIcon size={18} />}
            label="Overlay"
            onClick={() => setActiveCategory('overlay')}
          />
        </div>
      )}

      {/* CONTEXT B: OVERLAY TOOLS */}
      {effectiveCategory === 'overlay' && (
        <div className="w-full h-12 flex items-center px-1.5 overflow-x-auto no-scrollbar">
          {/* Integrated bottom-left back button */}
          <button
            type="button"
            onClick={handleBackToPrimary}
            className="w-9 h-12 flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-colors shrink-0 border-r border-[#232832]/80 pr-1 mr-1"
            title="Back to main tools"
            aria-label="Back"
          >
            <ChevronLeft size={20} />
          </button>

          {/* When NO overlay is selected: Add Overlay */}
          {!currentGroup || currentGroup.type !== 'overlay' ? (
            <ToolButton
              icon={<Plus size={18} />}
              label="Add Overlay"
              onClick={() => fileInputRef.current?.click()}
            />
          ) : (
            /* When an overlay IS selected: Split · Volume · Animation · Delete · Transform · Layers · Duplicate · Opacity */
            <>
              <ToolButton
                icon={<Scissors size={18} />}
                label="Split"
                onClick={handleSplit}
              />
              <ToolButton
                icon={currentGroup.is_muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
                label="Volume"
                disabled={currentGroup.overlay_type === 'image'}
                onClick={() => setActivePanel('layer_volume')}
                title={
                  currentGroup.overlay_type === 'image'
                    ? 'Audio not available for image overlays'
                    : 'Adjust overlay volume'
                }
              />
              <ToolButton
                icon={<Sparkles size={18} />}
                label="Animation"
                onClick={() => setActivePanel('overlay_animation')}
              />
              <ToolButton
                icon={<Trash2 size={18} className="text-red-400" />}
                label="Delete"
                onClick={handleDelete}
              />
              <ToolButton
                icon={<Crop size={18} />}
                label="Transform"
                onClick={() => setActivePanel('overlay_transform')}
              />
              <ToolButton
                icon={<Layers size={18} />}
                label="Layers"
                onClick={() => setActivePanel('layers')}
              />
              <ToolButton
                icon={<Copy size={18} />}
                label="Duplicate"
                onClick={handleDuplicate}
              />
              <ToolButton
                icon={<Sliders size={18} />}
                label="Opacity"
                onClick={() => setActivePanel('layer_opacity')}
              />
            </>
          )}
        </div>
      )}

      {/* CONTEXT C: TEXT TOOLS (Unified text & caption editing) */}
      {effectiveCategory === 'text' && (
        <div className="w-full h-12 flex items-center px-1.5 overflow-x-auto no-scrollbar">
          <button
            type="button"
            onClick={handleBackToPrimary}
            className="w-9 h-12 flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-colors shrink-0 border-r border-[#232832]/80 pr-1 mr-1"
            title="Back to main tools"
            aria-label="Back"
          >
            <ChevronLeft size={20} />
          </button>

          {/* Add Text · Presets · Style · Animation · Split · Lasting · Duplicate · Layers · Delete */}
          <ToolButton
            icon={<Plus size={18} />}
            label="Add Text"
            onClick={handleAddText}
          />
          <ToolButton
            icon={<Wand2 size={18} />}
            label="Presets"
            onClick={() => setActivePanel('templates')}
          />
          <ToolButton
            icon={<Type size={18} />}
            label="Style"
            onClick={() => setActivePanel('style')}
          />
          <ToolButton
            icon={<Sparkles size={18} />}
            label="Animation"
            onClick={() => setActivePanel('animation')}
          />
          <ToolButton
            icon={<Scissors size={18} />}
            label="Split"
            disabled={!currentGroup}
            onClick={handleSplit}
          />
          <ToolButton
            icon={<Clock size={18} />}
            label="Lasting"
            disabled={!currentGroup}
            onClick={handleLastingText}
          />
          <ToolButton
            icon={<Copy size={18} />}
            label="Duplicate"
            disabled={!currentGroup}
            onClick={handleDuplicate}
          />
          <ToolButton
            icon={<Layers size={18} />}
            label="Layers"
            onClick={() => setActivePanel('layers')}
          />
          <ToolButton
            icon={<Trash2 size={18} className="text-red-400" />}
            label="Delete"
            disabled={!currentGroup}
            onClick={handleDelete}
          />
        </div>
      )}

      {/* CONTEXT D: AUDIO TOOLS */}
      {effectiveCategory === 'audio' && (
        <div className="w-full h-12 flex items-center px-1.5 overflow-x-auto no-scrollbar">
          <button
            type="button"
            onClick={handleBackToPrimary}
            className="w-9 h-12 flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-colors shrink-0 border-r border-[#232832]/80 pr-1 mr-1"
            title="Back to main tools"
            aria-label="Back"
          >
            <ChevronLeft size={20} />
          </button>

          {currentGroup?.type === 'audio' || currentGroup?.type === 'voiceover' ? (
            <>
              <ToolButton
                icon={currentGroup.is_muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
                label="Volume"
                onClick={() => setActivePanel('layer_volume')}
              />
              <ToolButton
                icon={currentGroup.is_muted ? <Volume2 size={18} /> : <VolumeX size={18} />}
                label={currentGroup.is_muted ? 'Unmute' : 'Mute'}
                onClick={() => {
                  if (!track) return;
                  onUpdateTrack({
                    ...track,
                    groups: track.groups.map((g) =>
                      g.id === currentGroup.id ? { ...g, is_muted: !currentGroup.is_muted } : g
                    ),
                  });
                }}
              />
              <ToolButton icon={<Scissors size={18} />} label="Split" onClick={handleSplit} />
              <ToolButton icon={<Copy size={18} />} label="Duplicate" onClick={handleDuplicate} />
              <ToolButton icon={<Trash2 size={18} className="text-red-400" />} label="Delete" onClick={handleDelete} />
            </>
          ) : (
            <>
              <ToolButton
                icon={isMainVideoMuted ? <VolumeX size={18} /> : <Volume2 size={18} />}
                label="Volume"
                onClick={() => setActivePanel('main_volume')}
              />
              <ToolButton
                icon={isMainVideoMuted ? <Volume2 size={18} /> : <VolumeX size={18} />}
                label={isMainVideoMuted ? 'Unmute' : 'Mute'}
                onClick={() => onUpdateMainVideoAudio?.(mainVideoVolume, !isMainVideoMuted)}
              />
              <ToolButton
                icon={<Music size={18} />}
                label="Import Audio"
                onClick={() => handleImportAudio()}
              />
              <ToolButton
                icon={<Mic size={18} />}
                label="Voiceover"
                onClick={() => handleRecordVoiceover()}
              />
            </>
          )}
        </div>
      )}

      {/* CONTEXT E: EDIT TOOLS (Main Video Selected) */}
      {effectiveCategory === 'edit' && (
        <div className="w-full h-12 flex items-center px-1.5 overflow-x-auto no-scrollbar">
          <button
            type="button"
            onClick={handleBackToPrimary}
            className="w-9 h-12 flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-colors shrink-0 border-r border-[#232832]/80 pr-1 mr-1"
            title="Back to main tools"
            aria-label="Back"
          >
            <ChevronLeft size={20} />
          </button>

          <ToolButton icon={<Crop size={18} />} label="Framing" onClick={() => setActivePanel('framing')} />
          <ToolButton
            icon={isMainVideoMuted ? <VolumeX size={18} /> : <Volume2 size={18} />}
            label="Volume"
            onClick={() => setActivePanel('main_volume')}
          />
          <ToolButton
            icon={<RotateCcw size={18} />}
            label="Reset Crop"
            onClick={() => onLayoutChange?.('crop_916')}
          />
        </div>
      )}
    </div>
  );
};
