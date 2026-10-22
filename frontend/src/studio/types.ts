import { CaptionTrack, Word, CaptionGroup, StyleConfig, AnimationConfig } from '../captions/types';

export type LayoutMode =
  | 'fit_black'
  | 'fit_blur'
  | 'native_916'
  | 'crop_916'
  | 'crop_follow'
  | 'native_169'
  | 'landscape';

export interface AvailableLayoutMode {
  id: LayoutMode;
  label: string;
  target_aspect: '16:9' | '9:16';
  description?: string;
}

export interface ClipItem {
  id: string;
  session_id?: string | null;
  chunk_id?: string | null;
  start_time: number;
  end_time: number;
  duration: number;
  source_duration?: number | null;
  range_revision?: number;
  ai_suggested_start_time?: number;
  ai_suggested_end_time?: number;
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
  created_at: string;
  source_orientation?: 'portrait' | 'landscape';
  source_aspect_ratio?: '9:16' | '16:9';
  available_layout_modes?: AvailableLayoutMode[];
  target_aspect?: '16:9' | '9:16';
}

export type QueueFilter = 'all' | 'ready' | 'approved' | 'published' | 'rejected';
export type QueueSort = 'score' | 'newest' | 'oldest' | 'duration';

export interface SystemStatus {
  disk_free_gb: number;
  disk_total_gb: number;
  disk_percent_used: number;
  ready_clips: number;
  approved_clips?: number;
  published_clips?: number;
  publish_mode?: 'private' | 'public';
}

export interface IntegrationsStatus {
  youtube: {
    connected: boolean;
    channel_title: string;
    channel_id: string;
    has_client_secrets: boolean;
    has_token: boolean;
    error: string;
  };
  gemini: {
    configured: boolean;
    masked_key: string;
  };
}

export interface PublishingPlatform {
  id: 'youtube' | 'instagram' | 'linkedin' | 'x';
  name: string;
  maxTitle?: number;
  maxPost: number;
  requiresConnected: boolean;
  isConnected: boolean;
  connectionLabel?: string;
  helpUrl: string;
}

export interface SessionGroupData {
  sessionId: string;
  title: string;
  createdAt: string;
  clips: ClipItem[];
}
