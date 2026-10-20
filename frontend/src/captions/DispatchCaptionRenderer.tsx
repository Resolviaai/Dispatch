import React, { useMemo } from 'react';
import { CaptionTrack, CaptionGroup, Word, StyleConfig, LayoutConfig, AnimationConfig } from './types';
import { partitionWordsIntoVisualLines } from './linePartition';

interface DispatchCaptionRendererProps {
  track: CaptionTrack | null;
  currentTime: number;          // Current playback seconds with high precision
  containerWidth?: number;      // Optional width for resolution-independent scaling
  containerHeight?: number;     // Optional height for resolution-independent scaling
  layoutMode?: string;          // 'fit_black', 'fit_blur', 'crop_follow', 'landscape'
  showBoundingBox?: boolean;    // CapCut-style active selection box
}

export function applyColorOpacity(color: string, opacity: number = 1.0): string {
  if (!color) return color;
  const clamped = Math.max(0, Math.min(1, opacity));
  if (clamped >= 0.999 && !color.startsWith('#') && !color.startsWith('rgb')) return color;
  if (color.startsWith('#')) {
    let hex = color.slice(1);
    if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
    if (hex.length >= 6) {
      const r = parseInt(hex.substring(0, 2), 16) || 0;
      const g = parseInt(hex.substring(2, 4), 16) || 0;
      const b = parseInt(hex.substring(4, 6), 16) || 0;
      let a = 1.0;
      if (hex.length === 8) a = (parseInt(hex.substring(6, 8), 16) || 255) / 255;
      return `rgba(${r}, ${g}, ${b}, ${Number((a * clamped).toFixed(3))})`;
    }
  }
  const rgbaMatch = color.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)/);
  if (rgbaMatch) {
    const r = rgbaMatch[1];
    const g = rgbaMatch[2];
    const b = rgbaMatch[3];
    const a = rgbaMatch[4] !== undefined ? parseFloat(rgbaMatch[4]) : 1.0;
    return `rgba(${r}, ${g}, ${b}, ${Number((a * clamped).toFixed(3))})`;
  }
  return color;
}

export function applyCaseTransform(text: string, mode?: string): string {
  if (!text) return '';
  if (mode === 'uppercase') return text.toUpperCase();
  if (mode === 'lowercase') return text.toLowerCase();
  if (mode === 'title') {
    return text.split(' ').map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
  }
  return text;
}

interface CaptionGroupItemProps {
  group: CaptionGroup;
  defaultStyle: StyleConfig;
  defaultLayout: LayoutConfig;
  defaultAnimation: AnimationConfig;
  currentTime: number;
  containerWidth?: number;
  layoutMode: string;
  showBoundingBox: boolean;
}

const CaptionGroupItem: React.FC<CaptionGroupItemProps> = React.memo(({
  group,
  defaultStyle,
  defaultLayout,
  defaultAnimation,
  currentTime,
  containerWidth,
  layoutMode,
  showBoundingBox,
}) => {
  const style = group.style || defaultStyle;
  const layout = group.layout || defaultLayout;
  const anim = group.animation || defaultAnimation;

  // Layer type awareness: strictly separate caption from normal text behavior
  const isCaption = (group.type || 'caption') === 'caption';
  const highlightEnabled = isCaption && (style.highlight_enabled !== false);

  // Determine vertical placement percentage based on layout mode
  let posY = layout.position_y ?? 0.78;
  if (isCaption) {
    if (layoutMode === 'fit_black' || layoutMode === 'fit_blur') {
      posY = Math.max(0.74, posY);
    } else if (layoutMode === 'landscape') {
      posY = Math.min(0.86, Math.max(0.70, posY));
    }
  }

  // Entrance, exit, and loop animation calculations
  let enterOpacity = 1.0;
  let enterTransform = '';
  const enterType = anim.enter?.type || (anim as any)?.enter_type || 'none';
  const enterDur = (anim.enter?.duration_ms || (anim as any)?.enter_duration_ms || 200) / 1000;
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
  const exitType = anim.exit?.type || (anim as any)?.exit_type || 'none';
  const exitDur = (anim.exit?.duration_ms || (anim as any)?.exit_duration_ms || 150) / 1000;
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
  const loopType = (anim as any)?.loop?.type || (anim as any)?.loop_type || 'none';
  if (loopType === 'pulse') {
    const speed = ((anim as any)?.loop?.speed_ms || 1000) / 1000;
    const loopTime = (currentTime - group.start) / speed;
    loopTransform = `scale(${1.0 + 0.04 * Math.sin(loopTime * 2 * Math.PI)})`;
  } else if (loopType === 'wiggle') {
    const speed = ((anim as any)?.loop?.speed_ms || 1000) / 1000;
    const loopTime = (currentTime - group.start) / speed;
    loopTransform = `rotate(${2.0 * Math.sin(loopTime * 2 * Math.PI)}deg)`;
  }

  const baseScale = layout.scale ?? 1.0;
  const baseRot = layout.rotation ?? 0;
  const animTransform = [enterTransform, exitTransform, loopTransform].filter(Boolean).join(' ');
  const combinedTransform = `translate(-50%, -50%) scale(${baseScale}) rotate(${baseRot}deg) ${animTransform}`.trim();
  const effectiveOpacity = (style.opacity ?? 1) * Math.min(enterOpacity, exitOpacity);

  // Shadow angle and distance calculations
  let shadowX = style.shadow_offset_x || 0;
  let shadowY = style.shadow_offset_y || 2;
  if (style.shadow_distance !== undefined && style.shadow_angle !== undefined) {
    const rad = (style.shadow_angle * Math.PI) / 180;
    shadowX = Math.round(style.shadow_distance * Math.cos(rad) * 10) / 10;
    shadowY = Math.round(style.shadow_distance * Math.sin(rad) * 10) / 10;
  }
  const shadowColor = applyColorOpacity(style.shadow_color || 'rgba(0,0,0,0.85)', style.shadow_opacity ?? 1.0);
  const glowColor = applyColorOpacity(style.glow_color || '#00F0FF', style.glow_opacity ?? 1.0);

  // Filters for glow and shadow
  const filters: string[] = [];
  if (style.shadow_enabled) {
    filters.push(`drop-shadow(${shadowX}px ${shadowY}px ${style.shadow_blur || 6}px ${shadowColor})`);
  }
  if (style.glow_enabled) {
    filters.push(`drop-shadow(0px 0px ${style.glow_blur || 12}px ${glowColor})`);
  }
  const filterStr = filters.length > 0 ? filters.join(' ') : undefined;

  // Background styling mode: 'none' | 'line' / 'per_line' | 'block' / 'full_box'
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
  const fontWeight = style.font_weight || 'bold';
  const letterSpacing = style.letter_spacing || 0;

  // Derive visual lines deterministically using font metrics and container width
  const visualLines = useMemo(() => {
    const stageWidth = containerWidth || (typeof window !== 'undefined' ? Math.min(window.innerWidth, 480) : 360);
    const maxContentWidth = stageWidth * (layout.max_width ?? 0.88);
    return partitionWordsIntoVisualLines(
      group.words || [],
      fontFam,
      fontSize,
      fontWeight,
      letterSpacing,
      maxContentWidth,
      style.text_transform
    );
  }, [group.words, fontFam, fontSize, fontWeight, letterSpacing, layout.max_width, containerWidth, style.text_transform]);

  return (
    <div
      className={`absolute transition-all duration-75 flex flex-col ${
        layout.alignment === 'left' ? 'items-start text-left' : layout.alignment === 'right' ? 'items-end text-right' : 'items-center text-center'
      } ${showBoundingBox ? 'ring-1 ring-white/30 p-1.5 rounded-lg' : ''}`}
      style={{
        left: `${(layout.position_x ?? 0.5) * 100}%`,
        top: `${posY * 100}%`,
        transform: bgOffsetTransform ? `${combinedTransform} ${bgOffsetTransform}` : combinedTransform,
        maxWidth: `${(layout.max_width ?? 0.88) * 100}%`,
        width: 'max-content',
        transformOrigin: 'center center',
        opacity: effectiveOpacity,
        ...(isBgBlock && {
          backgroundColor: effectiveBgColor,
          padding: `${style.background_padding_y ?? 8}px ${style.background_padding_x ?? 14}px`,
          borderRadius: `${style.background_corner_radius ?? 12}px`,
        })
      }}
    >
      {visualLines.map((lineWords, lIdx) => {
        const lineContent = lineWords.map((w, idx) => {
          // Word timing & spoken active-word only applies to captions with word timing enabled
          const hasWordTiming = isCaption && (group as any).has_word_timing !== false && (group as any).timing_mode !== 'phrase' && (w as any).is_exact !== false;
          const isSpoken = hasWordTiming && currentTime >= w.start && currentTime <= w.end;
          const wordText = applyCaseTransform(w.text || (w as any).word || '', style.text_transform);
          const wordOverride = w.style_override;

          // Tactile scale pop calculation (Spring Punch) ONLY when layer is caption
          let scale = 1.0;
          const animType = anim?.active_word?.type || anim?.type || (anim as any)?.active_word_effect;
          if (isCaption && isSpoken && highlightEnabled && animType && animType !== 'none' && (animType === 'pop' || animType === 'bounce')) {
            const durationSec = (anim?.duration_ms || 100) / 1000;
            const elapsed = Math.max(0, currentTime - w.start);
            if (elapsed < durationSec) {
              const progress = elapsed / durationSec;
              const punch = (anim?.active_scale || 1.16) - 1.0;
              scale = 1.0 + punch * Math.cos(progress * (Math.PI / 2));
            }
          }

          // Dynamic colors: Normal text strictly preserves primary_color; Spoken highlight only applies to captions
          let textColor = style.primary_color || (style as any).color || '#FFFF00';
          if (isCaption && isSpoken && highlightEnabled) {
            textColor = wordOverride?.highlight_color || style.highlight_color || '#FFFFFF';
          } else if (wordOverride?.color) {
            textColor = wordOverride.color;
          }

          const strokeWidth = style.outline_enabled ? `${Math.max(1.5, (style.outline_width || 2) * 0.6)}px` : '0px';
          const strokeColorWithOpacity = style.outline_enabled
            ? applyColorOpacity(style.outline_color || '#000000', style.outline_opacity ?? 1.0)
            : undefined;

          return (
            <React.Fragment key={w.id || idx}>
              <span
                style={{
                  fontFamily: fontFam,
                  fontSize: `${fontSize}px`,
                  fontWeight: wordOverride?.font_weight || style.font_weight || 'bold',
                  fontStyle: style.font_style || 'normal',
                  textDecoration: (wordOverride?.underline ?? style.underline) ? 'underline' : undefined,
                  letterSpacing: `${style.letter_spacing || 0}px`,
                  lineHeight: style.line_height || 1.25,
                  color: applyColorOpacity(textColor, style.text_opacity ?? 1.0),
                  WebkitTextStroke: style.outline_enabled ? `${strokeWidth} ${strokeColorWithOpacity}` : undefined,
                  paintOrder: 'stroke fill',
                  filter: filterStr,
                  transform: `scale(${scale})`,
                  display: 'inline-block',
                  transformOrigin: 'center bottom',
                  transition: 'transform 0.04s cubic-bezier(0.16, 1, 0.3, 1), color 0.05s ease',
                }}
              >
                {wordText}
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
                  padding: `${Math.round((style.background_padding_y ?? 6) * 0.45)}px ${Math.round((style.background_padding_x ?? 12) * 0.45)}px`,
                  borderRadius: `${Math.round((style.background_corner_radius ?? 8) * 0.6)}px`,
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
  );
});

export const DispatchCaptionRenderer: React.FC<DispatchCaptionRendererProps> = ({
  track,
  currentTime,
  containerWidth,
  containerHeight,
  layoutMode = 'fit_black',
  showBoundingBox = true
}) => {
  if (!track || !track.groups || track.groups.length === 0) {
    return null;
  }

  // Find all active visible groups for current playback time (audio/voiceover do not render visual text; hidden layers excluded)
  const activeGroups = track.groups.filter(
    grp => grp.type !== 'audio' && grp.type !== 'voiceover' && !grp.is_hidden && currentTime >= grp.start && currentTime <= grp.end
  );

  if (activeGroups.length === 0) {
    return null;
  }

  return (
    <div
      className="absolute inset-0 pointer-events-none z-20 flex justify-center overflow-hidden select-none"
      style={{
        paddingLeft: '6%',
        paddingRight: '6%'
      }}
    >
      {activeGroups.map((group) => (
        <CaptionGroupItem
          key={group.id}
          group={group}
          defaultStyle={track.default_style}
          defaultLayout={track.default_layout}
          defaultAnimation={track.default_animation}
          currentTime={currentTime}
          containerWidth={containerWidth}
          layoutMode={layoutMode}
          showBoundingBox={showBoundingBox}
        />
      ))}
    </div>
  );
};
