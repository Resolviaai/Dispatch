import React from 'react';
import { CaptionTrack, CaptionGroup, Word } from './types';

interface DispatchCaptionRendererProps {
  track: CaptionTrack | null;
  currentTime: number;          // Current playback seconds with high precision
  containerWidth?: number;      // Optional width for resolution-independent scaling
  containerHeight?: number;     // Optional height for resolution-independent scaling
  layoutMode?: string;          // 'fit_black', 'fit_blur', 'crop_follow', 'landscape'
  showBoundingBox?: boolean;    // CapCut-style active selection box
}

export const DispatchCaptionRenderer: React.FC<DispatchCaptionRendererProps> = ({
  track,
  currentTime,
  layoutMode = 'fit_black',
  showBoundingBox = true
}) => {
  if (!track || !track.groups || track.groups.length === 0) {
    return null;
  }

  // Find active group for current time
  const activeGroup = track.groups.find(
    grp => currentTime >= grp.start && currentTime <= grp.end
  );

  if (!activeGroup) {
    return null;
  }

  const style = activeGroup.style || track.default_style;
  const layout = activeGroup.layout || track.default_layout;
  const anim = activeGroup.animation || track.default_animation;

  // Determine vertical placement percentage based on layout mode
  let posY = layout.position_y ?? 0.78;
  if (layoutMode === 'fit_black' || layoutMode === 'fit_blur') {
    // In fit_black / fit_blur, keep in the lower margin area
    posY = Math.max(0.74, posY);
  } else if (layoutMode === 'landscape') {
    // In 16:9 landscape, clamp within title safe margins
    posY = Math.min(0.86, Math.max(0.70, posY));
  }

  const justifyClass = 
    layout.alignment === 'left' ? 'justify-start text-left' :
    layout.alignment === 'right' ? 'justify-end text-right' :
    'justify-center text-center';

  return (
    <div
      className="absolute inset-0 pointer-events-none z-20 flex justify-center overflow-hidden select-none"
      style={{
        paddingLeft: '6%',
        paddingRight: '6%'
      }}
    >
      <div
        className={`absolute transition-all duration-75 flex flex-wrap items-center gap-x-2 gap-y-1 ${justifyClass} ${
          showBoundingBox ? 'ring-1 ring-white/30 p-1.5 rounded-lg bg-black/10' : ''
        }`}
        style={{
          left: `${(layout.position_x ?? 0.5) * 100}%`,
          top: `${posY * 100}%`,
          transform: `translate(-50%, -50%) scale(${layout.scale ?? 1.0}) rotate(${layout.rotation ?? 0}deg)`,
          maxWidth: `${(layout.max_width ?? 0.88) * 100}%`,
          transformOrigin: 'center center',
          opacity: style.opacity ?? 1,
          ...(style.background_enabled && {
            backgroundColor: style.background_color,
            padding: `${style.background_padding_y || 8}px ${style.background_padding_x || 14}px`,
            borderRadius: `${style.background_corner_radius || 12}px`,
            backdropFilter: 'blur(8px)'
          })
        }}
      >
        {(activeGroup.words || []).map((w, idx) => {
          const isSpoken = currentTime >= w.start && currentTime <= w.end;
          const wordText = w.text || (w as any).word || '';

          // Word style override or group style
          const wordOverride = w.style_override;
          const fontFam = style.font_family || 'Montserrat';
          const fontSize = Math.max(16, Math.min(38, Math.round((style.font_size || 42) * 0.42)));

          // Tactile scale pop calculation (CapCut Spring Pop)
          let scale = 1.0;
          const animType = anim?.active_word?.type || anim?.type || (anim as any)?.active_word_effect;
          if (isSpoken && (animType === 'pop' || animType === 'bounce')) {
            const durationSec = (anim?.duration_ms || 100) / 1000;
            const elapsed = Math.max(0, currentTime - w.start);
            if (elapsed < durationSec) {
              const progress = elapsed / durationSec;
              const punch = (anim?.active_scale || 1.16) - 1.0;
              scale = 1.0 + punch * Math.cos(progress * (Math.PI / 2));
            }
          }

          // Dynamic colors
          let textColor = style.primary_color || (style as any).color || '#FFFF00';
          if (isSpoken) {
            textColor = wordOverride?.highlight_color || style.highlight_color || '#FFFFFF';
          } else if (wordOverride?.color) {
            textColor = wordOverride.color;
          }

          const strokeWidth = style.outline_enabled ? `${Math.max(1.5, (style.outline_width || 2) * 0.6)}px` : '0px';

          return (
            <span
              key={w.id || idx}
              style={{
                fontFamily: fontFam,
                fontSize: `${fontSize}px`,
                fontWeight: style.font_weight || 'bold',
                textTransform: (style.text_transform as any) || 'none',
                letterSpacing: `${style.letter_spacing || 0}px`,
                color: textColor,
                WebkitTextStroke: style.outline_enabled ? `${strokeWidth} ${style.outline_color || '#000000'}` : undefined,
                paintOrder: 'stroke fill',
                filter: style.shadow_enabled 
                  ? `drop-shadow(${style.shadow_offset_x || 0}px ${style.shadow_offset_y || 2}px ${style.shadow_blur || 10}px ${style.shadow_color || 'rgba(0,0,0,0.8)'})`
                  : undefined,
                transform: `scale(${scale})`,
                display: 'inline-block',
                transformOrigin: 'center bottom',
                transition: 'transform 0.04s cubic-bezier(0.16, 1, 0.3, 1), color 0.05s ease'
              }}
            >
              {wordText}
            </span>
          );
        })}
      </div>
    </div>
  );
};
