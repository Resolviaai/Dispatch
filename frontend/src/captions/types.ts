export interface WordStyleOverride {
  color?: string;
  highlight_color?: string;
  scale?: number;
  font_weight?: string;
  underline?: boolean;
  background_color?: string;
}

export interface Word {
  id: string;
  text: string;
  start: number;
  end: number;
  confidence?: number;
  style_override?: WordStyleOverride;
}

export interface StyleConfig {
  font_family: string;
  font_size: number;
  font_weight: string;
  text_transform: 'uppercase' | 'none' | 'lowercase';
  letter_spacing: number;
  line_height: number;
  opacity?: number;
  
  // Colors
  primary_color: string;
  highlight_color: string;
  
  // Stroke / Outline
  outline_enabled: boolean;
  outline_color: string;
  outline_width: number;
  
  // Drop Shadow
  shadow_enabled: boolean;
  shadow_color: string;
  shadow_offset_x: number;
  shadow_offset_y: number;
  shadow_blur: number;
  
  // Glow Aura
  glow_enabled?: boolean;
  glow_color?: string;
  glow_blur?: number;
  
  // Background Box / Pill
  background_enabled: boolean;
  background_color: string;
  background_padding_x: number;
  background_padding_y: number;
  background_corner_radius: number;
}

export interface LayoutConfig {
  // Normalized 0.0 - 1.0
  position_x: number;
  position_y: number;
  scale?: number;
  rotation?: number;
  alignment: 'left' | 'center' | 'right';
  max_width: number;
  max_lines: number;
}

export interface AnimationConfig {
  type: 'pop' | 'karaoke' | 'bounce' | 'fade' | 'none';
  duration_ms: number;
  active_scale: number;
  easing: 'ease_out' | 'linear' | 'bounce';
  
  // 3-layer animation model
  enter?: {
    type: 'fade' | 'pop' | 'slide_up' | 'none';
    duration_ms: number;
  };
  active_word?: {
    type: 'pop' | 'karaoke' | 'bounce' | 'highlight' | 'none';
    active_scale: number;
    duration_ms: number;
  };
  exit?: {
    type: 'fade' | 'scale_down' | 'none';
    duration_ms: number;
  };
}

export interface OverlayAnimationConfig {
  enter?: 'none' | 'fade' | 'pop' | 'slide_up' | 'slide_down' | 'slide_left' | 'slide_right' | 'scale';
  enter_duration_ms?: number;
  exit?: 'none' | 'fade' | 'scale_down' | 'slide_up' | 'slide_down' | 'slide_left' | 'slide_right';
  exit_duration_ms?: number;
}

export type LayerType = 'text' | 'caption' | 'video' | 'audio' | 'voiceover' | 'overlay';

export interface CaptionGroup {
  id: string;
  start: number;
  end: number;
  words: Word[];
  layout?: LayoutConfig;
  style?: StyleConfig;
  animation?: AnimationConfig;
  trackIndex?: number;
  type?: LayerType;
  // Independent layer audio controls
  volume?: number;      // 0 - 200%
  is_muted?: boolean;
  audio_url?: string;
  // Independent layer overlay controls
  overlay_url?: string;
  overlay_type?: 'image' | 'video';
  opacity?: number;
  label?: string;
  flip_h?: boolean;
  flip_v?: boolean;
  overlay_animation?: OverlayAnimationConfig;
}

export interface CaptionTrack {
  id: string;
  clip_id: string;
  language: string;
  default_style: StyleConfig;
  default_layout: LayoutConfig;
  default_animation: AnimationConfig;
  groups: CaptionGroup[];
}
