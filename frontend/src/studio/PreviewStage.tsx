import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
  Maximize2,
  Minimize2,
  RefreshCw,
  Film,
  Sparkles,
  Layers,
  Crop,
  CheckCircle2,
  Scissors
} from 'lucide-react';
import { ClipItem, LayoutMode, AvailableLayoutMode } from './types';
import { CaptionTrack, CaptionGroup } from '../captions/types';
import { InteractiveTextCanvas } from '../captions/InteractiveTextCanvas';
import { apiUrl } from '../bridge';

interface PreviewStageProps {
  clip: ClipItem | null;
  track: CaptionTrack | null;
  currentTime: number;
  duration: number;
  isPlaying: boolean;
  onTimeUpdate: (time: number) => void;
  onDurationChange: (duration: number) => void;
  onTogglePlay: () => void;
  onSeek: (time: number) => void;
  onUpdateTrackGroup: (groupId: string, patch: Partial<CaptionGroup>) => void;
  selectedGroupId: string | null;
  onSelectGroup: (groupId: string | null) => void;
  onLayoutChange: (mode: LayoutMode) => Promise<void>;
  onTriggerRender: () => Promise<void>;
  isRendering: boolean;
  pcHost: string;
}

function formatTimecode(sec: number): string {
  const safe = Math.max(0, Number.isFinite(sec) ? sec : 0);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  const ms = Math.floor((safe % 1) * 10);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms}`;
}

export const PreviewStage: React.FC<PreviewStageProps> = ({
  clip,
  track,
  currentTime,
  duration,
  isPlaying,
  onTimeUpdate,
  onDurationChange,
  onTogglePlay,
  onSeek,
  onUpdateTrackGroup,
  selectedGroupId,
  onSelectGroup,
  onLayoutChange,
  onTriggerRender,
  isRendering,
  pcHost,
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageContainerRef = useRef<HTMLDivElement>(null);
  const [volume, setVolume] = useState<number>(1.0);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [isScrubbing, setIsScrubbing] = useState<boolean>(false);

  // Synchronize external seek with native video element
  useEffect(() => {
    const vid = videoRef.current;
    if (!vid) return;
    if (Math.abs(vid.currentTime - currentTime) > 0.35 && !isScrubbing) {
      vid.currentTime = currentTime;
    }
  }, [currentTime, isScrubbing]);

  // Synchronize play/pause
  useEffect(() => {
    const vid = videoRef.current;
    if (!vid) return;
    if (isPlaying && vid.paused) {
      vid.play().catch(() => {});
    } else if (!isPlaying && !vid.paused) {
      vid.pause();
    }
  }, [isPlaying]);

  const handleVolumeChange = (newVol: number) => {
    setVolume(newVol);
    if (videoRef.current) {
      videoRef.current.volume = newVol;
    }
    if (newVol > 0 && isMuted) {
      setIsMuted(false);
      if (videoRef.current) videoRef.current.muted = false;
    }
  };

  const toggleMute = () => {
    const next = !isMuted;
    setIsMuted(next);
    if (videoRef.current) {
      videoRef.current.muted = next;
    }
  };

  const toggleFullscreen = () => {
    if (!stageContainerRef.current) return;
    if (!document.fullscreenElement) {
      stageContainerRef.current.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };

  // Resolve video URL: prefer rendered clip MP4, fallback to source video
  const resolvedVideoUrl = clip?.video_url
    ? (clip.video_url.startsWith('http') ? clip.video_url : apiUrl(clip.video_url, pcHost))
    : clip?.source_video_url
    ? (clip.source_video_url.startsWith('http') ? clip.source_video_url : apiUrl(clip.source_video_url, pcHost))
    : null;

  const currentLayoutMode: LayoutMode = clip?.layout_mode || 'fit_black';

  return (
    <div
      ref={stageContainerRef}
      className="flex-1 min-w-0 h-full flex flex-col bg-[#0D0F12] select-none overflow-hidden relative"
    >
      {/* STAGE HEADER BAR */}
      <div className="h-10 px-4 bg-[#16181C] border-b border-[#2E3239] flex items-center justify-between z-20 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Film size={14} className="text-[#2563EB] shrink-0" />
          <span className="text-xs font-semibold text-[#F3F4F6] truncate max-w-[280px]">
            {clip ? (clip.title || clip.hook || clip.id) : 'No Clip Selected'}
          </span>
          {clip?.target_aspect && (
            <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-[#23262C] border border-[#2E3239] text-[#A3AAB5]">
              {clip.target_aspect}
            </span>
          )}
        </div>

        {/* Framing & Render Controls */}
        {clip && (
          <div className="flex items-center gap-2">
            {/* Aspect Ratio Framing Selector */}
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-[11px] text-[#747D8A] hidden md:inline">Framing:</span>
              <select
                value={currentLayoutMode}
                onChange={(e) => onLayoutChange(e.target.value as LayoutMode)}
                className="bg-[#23262C] border border-[#2E3239] rounded px-2 py-1 text-xs text-[#F3F4F6] focus:outline-none focus:border-[#2563EB]"
                title="Select Framing / Crop Mode"
              >
                {(clip.available_layout_modes || [
                  { id: 'fit_blur', label: 'Fit + Blur' },
                  { id: 'crop_916', label: 'Crop 9:16' },
                  { id: 'fit_black', label: 'Fit + Black' },
                  { id: 'native_916', label: 'Native 9:16' },
                  { id: 'native_169', label: 'Native 16:9' }
                ]).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Re-render Button */}
            <button
              type="button"
              onClick={onTriggerRender}
              disabled={isRendering}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium border transition-all active:scale-95 ${
                isRendering
                  ? 'bg-[#23262C] border-[#2E3239] text-[#747D8A] cursor-not-allowed'
                  : 'bg-[#23262C] border-[#2E3239] hover:border-[#2563EB]/50 text-[#F3F4F6] hover:bg-[#2E3239]'
              }`}
              title="Render atomic FFmpeg clip with latest captions & framing"
            >
              <RefreshCw size={12} className={isRendering ? 'animate-spin text-[#2563EB]' : 'text-[#A3AAB5]'} />
              <span>{isRendering ? 'Rendering...' : 'Bake Render'}</span>
            </button>
          </div>
        )}
      </div>

      {/* VIDEO PREVIEW STAGE CANVAS */}
      <div className="flex-1 min-h-0 w-full flex items-center justify-center p-3 relative overflow-hidden bg-[#0D0F12]">
        {clip && resolvedVideoUrl ? (
          <div className="relative h-full aspect-[9/16] max-w-full max-h-full rounded-xl bg-black border border-[#2E3239] shadow-2xl overflow-hidden flex items-center justify-center group">
            {/* HTML5 Video Element with progressive poster */}
            <video
              ref={videoRef}
              src={resolvedVideoUrl}
              poster={clip.thumbnail_url ? (clip.thumbnail_url.startsWith('http') ? clip.thumbnail_url : apiUrl(clip.thumbnail_url, pcHost)) : undefined}
              className="w-full h-full object-contain pointer-events-auto"
              playsInline
              preload="metadata"
              onClick={onTogglePlay}
              onTimeUpdate={() => {
                if (videoRef.current && !isScrubbing) {
                  onTimeUpdate(videoRef.current.currentTime);
                }
              }}
              onLoadedMetadata={() => {
                if (videoRef.current) {
                  onDurationChange(videoRef.current.duration || clip.duration || 1);
                }
              }}
              onEnded={() => onSeek(0)}
            />

            {/* Live Interactive Caption Overlay */}
            <div className="absolute inset-0 pointer-events-none">
              <InteractiveTextCanvas
                track={track}
                currentTime={currentTime}
                selectedGroupId={selectedGroupId}
                onSelectGroup={onSelectGroup}
                onUpdateGroup={onUpdateTrackGroup}
                layoutMode={currentLayoutMode}
                isInteractive={false}
              />
            </div>

            {/* Floating Play / Pause Overlay Indicator */}
            {!isPlaying && (
              <button
                type="button"
                onClick={onTogglePlay}
                className="absolute inset-0 m-auto w-14 h-14 rounded-full bg-black/60 border border-white/20 text-white flex items-center justify-center shadow-hero-glow backdrop-blur-sm transition-all hover:scale-105 active:scale-95"
              >
                <Play size={24} className="ml-1 fill-white" />
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center text-center p-6 text-[#747D8A]">
            <Film size={40} className="mb-3 text-[#2E3239]" />
            <h3 className="text-sm font-semibold text-[#A3AAB5]">No clip selected</h3>
            <p className="text-xs mt-1 max-w-xs">
              Select a clip from the left queue to preview the video and edit captions.
            </p>
          </div>
        )}
      </div>

      {/* PLAYBACK CONTROL DOCK (Layer 1 Surface) */}
      <div className="h-12 px-4 bg-[#16181C] border-t border-[#2E3239] flex items-center justify-between z-20 shrink-0">
        {/* Left: Play/Pause, Step Back/Forward */}
        <div className="flex items-center gap-1.5 sm:gap-2">
          <button
            type="button"
            onClick={onTogglePlay}
            disabled={!clip}
            className="w-8 h-8 rounded-lg bg-[#2563EB] hover:bg-[#1D4ED8] text-white flex items-center justify-center shadow-hero-sm transition-all active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
            title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
          >
            {isPlaying ? <Pause size={14} className="fill-white" /> : <Play size={14} className="ml-0.5 fill-white" />}
          </button>

          <button
            type="button"
            onClick={() => onSeek(Math.max(0, currentTime - 5))}
            disabled={!clip}
            className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all disabled:opacity-40"
            title="Jump back 5s (J)"
          >
            <RotateCcw size={15} />
          </button>

          <button
            type="button"
            onClick={() => onSeek(Math.min(duration || 100, currentTime + 5))}
            disabled={!clip}
            className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all disabled:opacity-40"
            title="Jump forward 5s (L)"
          >
            <RotateCw size={15} />
          </button>

          {/* Timecode Display */}
          <div className="text-xs font-mono tabular-nums text-[#A3AAB5] ml-2 hidden sm:block">
            <span className="text-[#F3F4F6] font-semibold">{formatTimecode(currentTime)}</span>
            <span className="text-[#747D8A]"> / </span>
            <span>{formatTimecode(duration || clip?.duration || 0)}</span>
          </div>
        </div>

        {/* Center: Scrubber Slider */}
        <div className="flex-1 max-w-xl mx-4">
          <input
            type="range"
            min={0}
            max={Math.max(1, duration || clip?.duration || 1)}
            step={0.05}
            value={currentTime}
            onChange={(e) => {
              const val = parseFloat(e.target.value);
              setIsScrubbing(true);
              onSeek(val);
            }}
            onMouseUp={() => setIsScrubbing(false)}
            onTouchEnd={() => setIsScrubbing(false)}
            disabled={!clip}
            className="creator-scrubber w-full"
            style={{
              '--track-bg': '#23262C'
            } as any}
          />
        </div>

        {/* Right: Volume & Fullscreen */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 hidden md:flex">
            <button
              type="button"
              onClick={toggleMute}
              className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted || volume === 0 ? <VolumeX size={15} /> : <Volume2 size={15} />}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={isMuted ? 0 : volume}
              onChange={(e) => handleVolumeChange(parseFloat(e.target.value))}
              className="w-16 h-1 rounded-full accent-[#2563EB] bg-[#23262C] cursor-pointer"
            />
          </div>

          <button
            type="button"
            onClick={toggleFullscreen}
            className="p-1.5 rounded-md text-[#A3AAB5] hover:text-[#F3F4F6] hover:bg-[#23262C] transition-all"
            title="Fullscreen Video"
          >
            {isFullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
        </div>
      </div>
    </div>
  );
};
