import { AnimationConfig, CaptionGroup, CaptionTrack, LayerType, LayoutConfig, StyleConfig, Word } from './types';

function getSavedTemplate(): { style?: Partial<StyleConfig>; layout?: Partial<LayoutConfig>; anim?: Partial<AnimationConfig> } {
  if (typeof localStorage !== 'undefined') {
    try {
      const raw = localStorage.getItem('dispatch_default_template');
      if (raw) {
        const parsed = JSON.parse(raw);
        const c = parsed?.captions || parsed?.template?.captions || parsed?.default_style;
        if (c && typeof c === 'object') {
          return {
            style: {
              font_family: c.font_family,
              font_size: c.font_size,
              font_weight: c.font_weight,
              text_transform: c.all_caps ? 'uppercase' : (c.text_transform || 'none'),
              letter_spacing: c.letter_spacing,
              line_height: c.line_height,
              primary_color: c.primary_color,
              highlight_color: c.highlight_color,
              outline_enabled: c.outline_enabled,
              outline_color: c.outline_color,
              outline_width: c.outline_width,
              shadow_enabled: c.shadow_enabled,
              shadow_color: c.shadow_color,
              shadow_offset_x: c.shadow_offset_x,
              shadow_offset_y: c.shadow_offset_y,
              shadow_blur: c.shadow_blur,
              glow_enabled: c.glow_enabled,
              glow_color: c.glow_color,
              glow_blur: c.glow_blur,
              background_enabled: c.background_enabled,
              background_color: c.background_color,
              background_padding_x: c.background_padding_x,
              background_padding_y: c.background_padding_y,
              background_corner_radius: c.background_corner_radius,
            },
            layout: {
              position_y: typeof c.position_y === 'number' ? c.position_y : undefined,
              position_x: typeof c.position_x === 'number' ? c.position_x : undefined,
              alignment: c.alignment,
              scale: c.scale,
            },
            anim: c.animation_config || (c.animation ? { type: c.animation } : undefined),
          };
        }
      }
    } catch {}
  }
  return {};
}

export function normalizeCaptionTrack(raw: unknown, clipId: string, duration: number): CaptionTrack {
  const source = raw && typeof raw === 'object' ? raw as Record<string, any> : {};
  const rawStyle = source.default_style || {};
  const rawLayout = source.default_layout || {};
  const rawAnimation = source.default_animation || {};

  const savedTpl = getSavedTemplate();
  const tplStyle = savedTpl.style || {};
  const tplLayout = savedTpl.layout || {};
  const tplAnim = savedTpl.anim || {};

  const default_style: StyleConfig = {
    font_family: rawStyle.font_family || tplStyle.font_family || 'Montserrat',
    font_size: rawStyle.font_size || tplStyle.font_size || 44,
    font_weight: rawStyle.font_weight || tplStyle.font_weight || '900',
    text_transform: rawStyle.text_transform || tplStyle.text_transform || 'none',
    letter_spacing: rawStyle.letter_spacing ?? tplStyle.letter_spacing ?? 0,
    line_height: rawStyle.line_height ?? tplStyle.line_height ?? 1.25,
    primary_color: rawStyle.primary_color || rawStyle.color || tplStyle.primary_color || '#FFFF00',
    highlight_color: rawStyle.highlight_color || tplStyle.highlight_color || '#FFFFFF',
    outline_enabled: rawStyle.outline_enabled ?? tplStyle.outline_enabled ?? Boolean(rawStyle.stroke_width ?? tplStyle.outline_width),
    outline_color: rawStyle.outline_color || rawStyle.stroke_color || tplStyle.outline_color || '#000000',
    outline_width: rawStyle.outline_width ?? rawStyle.stroke_width ?? tplStyle.outline_width ?? 3,
    shadow_enabled: rawStyle.shadow_enabled ?? tplStyle.shadow_enabled ?? rawStyle.has_shadow ?? true,
    shadow_color: rawStyle.shadow_color || tplStyle.shadow_color || '#000000',
    shadow_offset_x: rawStyle.shadow_offset_x ?? tplStyle.shadow_offset_x ?? 0,
    shadow_offset_y: rawStyle.shadow_offset_y ?? tplStyle.shadow_offset_y ?? 2,
    shadow_blur: rawStyle.shadow_blur ?? tplStyle.shadow_blur ?? 6,
    glow_enabled: rawStyle.glow_enabled ?? tplStyle.glow_enabled ?? false,
    glow_color: rawStyle.glow_color || tplStyle.glow_color || '#00F0FF',
    glow_blur: rawStyle.glow_blur ?? tplStyle.glow_blur ?? 12,
    background_enabled: rawStyle.background_enabled ?? tplStyle.background_enabled ?? rawStyle.has_background ?? false,
    background_color: rawStyle.background_color || tplStyle.background_color || '#000000',
    background_padding_x: rawStyle.background_padding_x ?? tplStyle.background_padding_x ?? 12,
    background_padding_y: rawStyle.background_padding_y ?? tplStyle.background_padding_y ?? 6,
    background_corner_radius: rawStyle.background_corner_radius ?? tplStyle.background_corner_radius ?? 8,
  };

  const default_layout: LayoutConfig = {
    position_x: rawLayout.position_x ?? tplLayout.position_x ?? 0.5,
    position_y: rawLayout.position_y ?? tplLayout.position_y ?? 0.78,
    scale: rawLayout.scale ?? tplLayout.scale ?? 1.0,
    rotation: rawLayout.rotation ?? 0,
    alignment: rawLayout.alignment || tplLayout.alignment || 'center',
    max_width: rawLayout.max_width ?? 0.88,
    max_lines: rawLayout.max_lines ?? 2,
  };

  const default_animation: AnimationConfig = {
    type: rawAnimation.type || 'pop',
    duration_ms: rawAnimation.duration_ms || 120,
    active_scale: rawAnimation.active_scale || 1.15,
    easing: rawAnimation.easing || 'ease_out',
    enter: {
      type: rawAnimation.entrance_animation || rawAnimation.enter?.type === 'scale_up' ? 'pop' : (rawAnimation.enter?.type || 'pop'),
      duration_ms: 200,
    },
    active_word: {
      type: rawAnimation.active_word_effect || rawAnimation.active_word?.type || 'pop',
      active_scale: 1.15,
      duration_ms: 120,
    },
    exit: {
      type: rawAnimation.exit_animation || rawAnimation.exit?.type || 'none',
      duration_ms: 150,
    },
  };

  const rawGroups = Array.isArray(source.groups) ? source.groups : [];
  const groups: CaptionGroup[] = rawGroups.map((item: any, gIdx: number) => {
    const g = item && typeof item === 'object' ? item : {};
    const rawWords = Array.isArray(g.words) ? g.words : [];
    const words: Word[] = rawWords.map((itemWord: any, wIdx: number) => {
      const w = itemWord && typeof itemWord === 'object' ? itemWord : {};
      return {
        id: w.id || `w_${gIdx}_${wIdx}`,
        text: w.text || w.word || '',
        start: typeof w.start === 'number' ? w.start : g.start,
        end: typeof w.end === 'number' ? w.end : g.end,
        confidence: w.confidence ?? 0.95,
        style_override: w.style_override,
      };
    });

    const rawGroupLayout = g.layout && typeof g.layout === 'object' ? g.layout : {};
    const rawGroupStyle = g.style && typeof g.style === 'object' ? g.style : {};
    const rawPosY = rawGroupLayout.position_y ?? default_layout.position_y;
    const rawPosX = rawGroupLayout.position_x ?? default_layout.position_x;

    return {
      id: g.id || `grp_${gIdx}`,
      start: typeof g.start === 'number' ? g.start : 0,
      end: typeof g.end === 'number' ? g.end : duration,
      words: words.length > 0 ? words : [{
        id: `w_${gIdx}_0`, text: g.text || '', start: g.start || 0, end: g.end || duration,
      }],
      layout: {
        ...default_layout,
        ...rawGroupLayout,
        position_x: rawPosX > 1 ? rawPosX / 100 : rawPosX,
        position_y: rawPosY > 1 ? rawPosY / 100 : rawPosY,
      },
      style: {
        ...default_style,
        ...rawGroupStyle,
        primary_color: rawGroupStyle.primary_color || rawGroupStyle.color || default_style.primary_color,
        highlight_color: rawGroupStyle.highlight_color || default_style.highlight_color,
        highlight_enabled: rawGroupStyle.highlight_enabled !== false,
        outline_color: rawGroupStyle.outline_color || rawGroupStyle.stroke_color || default_style.outline_color,
        outline_width: rawGroupStyle.outline_width || rawGroupStyle.stroke_width || default_style.outline_width,
      },
      animation: { ...default_animation, ...(g.animation || {}) },
      type: (g.type as LayerType) || 'caption',
      trackIndex: typeof g.trackIndex === 'number' && g.trackIndex >= 0 ? g.trackIndex : undefined,
      volume: typeof g.volume === 'number' ? (g.volume <= 2.0 ? Math.round(g.volume * 100) : g.volume) : 100,
      is_muted: Boolean(g.is_muted),
      is_hidden: Boolean(g.is_hidden),
      audio_url: g.audio_url,
      overlay_url: g.overlay_url,
      overlay_type: g.overlay_type,
      opacity: typeof g.opacity === 'number' ? g.opacity : 100,
      label: g.label,
      flip_h: Boolean(g.flip_h),
      flip_v: Boolean(g.flip_v),
      overlay_animation: g.overlay_animation,
      has_word_timing: g.has_word_timing,
      timing_mode: g.timing_mode,
    };
  });

  return {
    id: source.id || `track_${clipId}`,
    clip_id: clipId,
    language: source.language || 'en',
    default_style,
    default_layout,
    default_animation,
    groups,
    range_revision: typeof source.range_revision === 'number' ? source.range_revision : undefined,
    timing_warning: typeof source.timing_warning === 'string' ? source.timing_warning : null,
  };
}

export function resolveStudioClip<T extends { id: string }>(clips: T[], selectedClipId: string | null): T | null {
  return clips.find((clip) => clip.id === selectedClipId) || clips[0] || null;
}

export function trackForClip(clipId: string | null, loadedClipId: string | null, track: CaptionTrack | null): CaptionTrack | null {
  return clipId && clipId === loadedClipId ? track : null;
}
