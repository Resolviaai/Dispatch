import { CaptionTrack, CaptionGroup, StyleConfig, LayoutConfig, AnimationConfig } from './types';

export type StyleSection =
  | 'font'
  | 'color'
  | 'text'
  | 'stroke'
  | 'glow'
  | 'background'
  | 'shadow'
  | 'spacing'
  | 'bold_italic'
  | 'case'
  | 'alignment'
  | 'animation'
  | 'templates'
  | 'layout'
  | 'all';

export interface ApplyToAllResult {
  nextTrack: CaptionTrack;
  affectedCount: number;
}

/**
 * Extracts only the properties belonging to the specified section from a source group.
 */
export function extractSectionProperties(
  sourceGroup: CaptionGroup,
  track: CaptionTrack,
  section: StyleSection
): {
  stylePatch?: Partial<StyleConfig>;
  layoutPatch?: Partial<LayoutConfig>;
  animationPatch?: Partial<AnimationConfig>;
} {
  const targetStyle = sourceGroup.style || track.default_style;
  const targetLayout = sourceGroup.layout || track.default_layout;
  const targetAnimation = sourceGroup.animation || track.default_animation;

  switch (section) {
    case 'font':
      return {
        stylePatch: {
          font_family: targetStyle.font_family,
          font_size: targetStyle.font_size,
          font_weight: targetStyle.font_weight,
          letter_spacing: targetStyle.letter_spacing,
          line_height: targetStyle.line_height,
          text_transform: targetStyle.text_transform,
        },
      };

    case 'color':
      return {
        stylePatch: {
          primary_color: targetStyle.primary_color,
          opacity: targetStyle.opacity,
          text_opacity: targetStyle.text_opacity,
          ...(sourceGroup.type !== 'text' ? {
            highlight_color: targetStyle.highlight_color,
            highlight_enabled: targetStyle.highlight_enabled,
          } : {}),
        },
      };

    case 'text':
      return {
        stylePatch: {
          primary_color: targetStyle.primary_color,
          text_opacity: targetStyle.text_opacity,
          font_size: targetStyle.font_size,
          opacity: targetStyle.opacity,
          font_weight: targetStyle.font_weight,
          font_style: targetStyle.font_style,
          underline: targetStyle.underline,
          text_transform: targetStyle.text_transform,
          ...(sourceGroup.type !== 'text' ? {
            highlight_color: targetStyle.highlight_color,
            highlight_enabled: targetStyle.highlight_enabled,
          } : {}),
        },
      };

    case 'stroke':
      return {
        stylePatch: {
          outline_enabled: targetStyle.outline_enabled,
          outline_color: targetStyle.outline_color,
          outline_width: targetStyle.outline_width,
          outline_opacity: targetStyle.outline_opacity,
        },
      };

    case 'glow':
      return {
        stylePatch: {
          glow_enabled: targetStyle.glow_enabled,
          glow_color: targetStyle.glow_color,
          glow_blur: targetStyle.glow_blur,
          glow_opacity: targetStyle.glow_opacity,
        },
      };

    case 'background':
      return {
        stylePatch: {
          background_enabled: targetStyle.background_enabled,
          background_mode: targetStyle.background_mode,
          background_color: targetStyle.background_color,
          background_opacity: targetStyle.background_opacity,
          background_padding_x: targetStyle.background_padding_x,
          background_padding_y: targetStyle.background_padding_y,
          background_corner_radius: targetStyle.background_corner_radius,
          background_offset_x: targetStyle.background_offset_x,
          background_offset_y: targetStyle.background_offset_y,
          background_width: targetStyle.background_width,
          background_height: targetStyle.background_height,
        },
      };

    case 'shadow':
      return {
        stylePatch: {
          shadow_enabled: targetStyle.shadow_enabled,
          shadow_color: targetStyle.shadow_color,
          shadow_offset_x: targetStyle.shadow_offset_x,
          shadow_offset_y: targetStyle.shadow_offset_y,
          shadow_blur: targetStyle.shadow_blur,
          shadow_opacity: targetStyle.shadow_opacity,
          shadow_distance: targetStyle.shadow_distance,
          shadow_angle: targetStyle.shadow_angle,
        },
      };

    case 'spacing':
      return {
        stylePatch: {
          letter_spacing: targetStyle.letter_spacing,
          line_height: targetStyle.line_height,
        },
        layoutPatch: {
          alignment: targetLayout.alignment,
          scale: targetLayout.scale,
        },
      };

    case 'bold_italic':
      return {
        stylePatch: {
          font_weight: targetStyle.font_weight,
          font_style: targetStyle.font_style,
          underline: targetStyle.underline,
        },
      };

    case 'case':
      return {
        stylePatch: {
          text_transform: targetStyle.text_transform,
        },
      };

    case 'alignment':
      return {
        layoutPatch: {
          alignment: targetLayout.alignment,
          max_width: targetLayout.max_width,
          max_lines: targetLayout.max_lines,
        },
      };

    case 'layout':
      return {
        layoutPatch: {
          position_x: targetLayout.position_x,
          position_y: targetLayout.position_y,
          scale: targetLayout.scale,
          ...(targetLayout.rotation !== undefined ? { rotation: targetLayout.rotation } : {}),
          ...(targetLayout.alignment !== undefined ? { alignment: targetLayout.alignment } : {}),
          ...(targetLayout.max_width !== undefined ? { max_width: targetLayout.max_width } : {}),
          ...(targetLayout.max_lines !== undefined ? { max_lines: targetLayout.max_lines } : {}),
        },
      };

    case 'animation':
      return {
        animationPatch: {
          type: targetAnimation.type,
          duration_ms: targetAnimation.duration_ms,
          active_scale: targetAnimation.active_scale,
          easing: targetAnimation.easing,
          enter: targetAnimation.enter ? { ...targetAnimation.enter } : undefined,
          active_word: targetAnimation.active_word ? { ...targetAnimation.active_word } : undefined,
          exit: targetAnimation.exit ? { ...targetAnimation.exit } : undefined,
          loop: targetAnimation.loop ? { ...targetAnimation.loop } : undefined,
        },
      };

    case 'templates':
      return {
        stylePatch: { ...targetStyle },
        animationPatch: { ...targetAnimation },
      };

    case 'all':
      return {
        stylePatch: { ...targetStyle },
        layoutPatch: {
          position_x: targetLayout.position_x,
          position_y: targetLayout.position_y,
          scale: targetLayout.scale,
          ...(targetLayout.rotation !== undefined ? { rotation: targetLayout.rotation } : {}),
          ...(targetLayout.alignment !== undefined ? { alignment: targetLayout.alignment } : {}),
          ...(targetLayout.max_width !== undefined ? { max_width: targetLayout.max_width } : {}),
          ...(targetLayout.max_lines !== undefined ? { max_lines: targetLayout.max_lines } : {}),
        },
        animationPatch: {
          type: targetAnimation.type,
          duration_ms: targetAnimation.duration_ms,
          active_scale: targetAnimation.active_scale,
          easing: targetAnimation.easing,
          enter: targetAnimation.enter ? { ...targetAnimation.enter } : undefined,
          active_word: targetAnimation.active_word ? { ...targetAnimation.active_word } : undefined,
          exit: targetAnimation.exit ? { ...targetAnimation.exit } : undefined,
          loop: targetAnimation.loop ? { ...targetAnimation.loop } : undefined,
        },
      };

    default:
      return {};
  }
}

/**
 * One-time action to apply ONLY the properties of the given section
 * from the target group to every eligible text/caption element in the clip.
 * 
 * Invariants:
 * 1. Only changed properties of the current section propagate.
 * 2. Unrelated section properties (colors, strokes, shadows, animations, positions) remain intact.
 * 3. Never applies to audio, video overlays, or voiceovers.
 * 4. Word-level overrides and timestamps are 100% preserved.
 * 5. Not a persistent mode — future edits remain strictly local.
 */
export function applySectionStylesToTrack(
  track: CaptionTrack | null,
  targetGroupId: string | null,
  section: StyleSection
): ApplyToAllResult {
  if (!track || !track.groups || track.groups.length === 0) {
    return { nextTrack: track as CaptionTrack, affectedCount: 0 };
  }

  const targetGroup = (targetGroupId ? track.groups.find((g) => g.id === targetGroupId) : null) ||
    track.groups.find((g) => !g.type || g.type === 'caption') ||
    track.groups.find((g) => g.type === 'text') ||
    track.groups[0];

  if (!targetGroup) {
    return { nextTrack: track, affectedCount: 0 };
  }

  const { stylePatch, layoutPatch, animationPatch } = extractSectionProperties(
    targetGroup,
    track,
    section
  );

  let affectedCount = 0;

  const updatedGroups = track.groups.map((grp) => {
    // Restrict strictly based on target layer type:
    // Text layers apply ONLY to other text layers.
    // Captions apply ONLY to other captions.
    // Audio and overlay layers are NEVER affected.
    const isTargetText = targetGroup.type === 'text';
    const isEligible = isTargetText
      ? grp.type === 'text'
      : (!grp.type || grp.type === 'caption');

    if (!isEligible) {
      return grp;
    }

    affectedCount++;
    return {
      ...grp,
      style: stylePatch ? { ...(grp.style || track.default_style), ...stylePatch } : grp.style,
      layout: layoutPatch ? { ...(grp.layout || track.default_layout), ...layoutPatch } : grp.layout,
      animation: animationPatch ? { ...(grp.animation || track.default_animation), ...animationPatch } : grp.animation,
    };
  });

  const nextTrack: CaptionTrack = {
    ...track,
    default_style: stylePatch ? { ...track.default_style, ...stylePatch } : track.default_style,
    default_layout: layoutPatch ? { ...track.default_layout, ...layoutPatch } : track.default_layout,
    default_animation: animationPatch ? { ...track.default_animation, ...animationPatch } : track.default_animation,
    groups: updatedGroups,
  };

  return { nextTrack, affectedCount };
}
