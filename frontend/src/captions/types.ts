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
  is_exact?: boolean;
  style_override?: WordStyleOverride;
}

export interface StyleConfig {
  font_family: string;
  font_size: number;
  font_weight: string;
  font_style?: 'normal' | 'italic';
  underline?: boolean;
  text_transform: 'uppercase' | 'none' | 'lowercase' | 'title';
  letter_spacing: number;
  line_height: number;
  opacity?: number;
  text_opacity?: number;
  
  // Colors
  primary_color: string;
  highlight_color: string;
  highlight_enabled?: boolean; // Captions active/spoken-word highlight toggle
  
  // Stroke / Outline
  outline_enabled: boolean;
  outline_color: string;
  outline_width: number;
  outline_opacity?: number;
  
  // Drop Shadow
  shadow_enabled: boolean;
  shadow_color: string;
  shadow_offset_x: number;
  shadow_offset_y: number;
  shadow_blur: number;
  shadow_opacity?: number;
  shadow_distance?: number;
  shadow_angle?: number;
  
  // Glow Aura
  glow_enabled?: boolean;
  glow_color?: string;
  glow_blur?: number;
  glow_opacity?: number;
  
  // Background Box / Pill
  background_enabled: boolean;
  background_mode?: 'none' | 'line' | 'block';
  background_color: string;
  background_opacity?: number;
  background_padding_x: number;
  background_padding_y: number;
  background_corner_radius: number;
  background_offset_x?: number;
  background_offset_y?: number;
  background_width?: number;
  background_height?: number;
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
  type: 'pop' | 'karaoke' | 'bounce' | 'slide' | 'fade' | 'none';
  duration_ms: number;
  active_scale: number;
  easing: 'ease_out' | 'linear' | 'bounce';
  
  // 3-layer animation model
  enter?: {
    type: 'fade' | 'pop' | 'scale_up' | 'slide_up' | 'none';
    duration_ms: number;
  };
  active_word?: {
    type: 'pop' | 'karaoke' | 'bounce' | 'slide' | 'highlight' | 'none';
    active_scale: number;
    duration_ms: number;
  };
  exit?: {
    type: 'fade' | 'scale_down' | 'slide_down' | 'none';
    duration_ms: number;
  };
  loop?: {
    type: 'pulse' | 'wiggle' | 'none';
    speed_ms: number;
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
  is_hidden?: boolean;
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
  has_word_timing?: boolean;
  timing_mode?: 'word' | 'phrase';
}

export interface CaptionTrack {
  id: string;
  clip_id: string;
  language: string;
  default_style: StyleConfig;
  default_layout: LayoutConfig;
  default_animation: AnimationConfig;
  groups: CaptionGroup[];
  range_revision?: number;
  timing_warning?: string | null;
}
