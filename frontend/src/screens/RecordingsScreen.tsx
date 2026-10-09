import React, { useState } from 'react';
import { 
  Film, 
  RotateCw,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Video,
  Download,
  Maximize2,
  X
} from 'lucide-react';
import { Bridge, RecordingItem, apiUrl } from '../bridge';

interface RecordingsScreenProps {
  recordings: RecordingItem[];
  isYouTubeConnected: boolean;
  onNavigateToSettings: () => void;
  onRetryUpload: (segmentId: string) => void;
}

export const RecordingsScreen: React.FC<RecordingsScreenProps> = ({ 
  recordings, 
  isYouTubeConnected,
  onNavigateToSettings,
  onRetryUpload 
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'queued' | 'uploaded'>('all');
  const [modalVideo, setModalVideo] = useState<{ url: string; title: string } | null>(null);

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0\u00a0B';
    const mb = bytes / (1024 * 1024);
    if (mb < 1) return `${Math.round(bytes / 1024)}\u00a0KB`;
    return `${mb.toFixed(1)}\u00a0MB`;
  };

  const formatDuration = (sec?: number) => {
    if (!sec) return '00:00';
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  };

  const formatTimeAgo = (timestamp: number) => {
    const diff = Math.floor((Date.now() - timestamp) / 1000);
    if (diff < 60) return `${diff}\u00a0s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}\u00a0m ago`;
    return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const filteredRecordings = recordings.filter(item => {
    if (filter === 'uploaded') return item.status === 'UPLOADED_TO_YOUTUBE';
    if (filter === 'queued') return item.status !== 'UPLOADED_TO_YOUTUBE';
    return true;
  });

  const totalBytes = recordings.reduce((acc, r) => acc + (r.fileSizeBytes || 0), 0);

  const renderStatusBadge = (item: RecordingItem) => {
    if (Bridge.isAvailable() && !isYouTubeConnected && item.status === 'QUEUED_FOR_UPLOAD') {
      return (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onNavigateToSettings();
          }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-mono tabular-nums font-medium bg-amber-500/10 border border-amber-500/30 text-amber-300 hover:bg-amber-500/20 active:scale-[0.98] transition-all duration-150"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          Connect YouTube
        </button>
      );
    }

    switch (item.status) {
      case 'UPLOADED_TO_YOUTUBE':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-mono tabular-nums font-medium bg-emerald-500/15 border border-emerald-500/30 text-emerald-300">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            Uploaded
          </span>
        );
      case 'UPLOADING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-mono tabular-nums font-medium bg-blue-500/15 border border-blue-500/30 text-blue-300">
            <RotateCw size={10} className="text-blue-400 animate-spin" />
            Uploading…
          </span>
        );
      case 'QUEUED_FOR_UPLOAD':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-mono tabular-nums font-medium bg-amber-500/15 border border-amber-500/30 text-amber-300">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
            Queued
          </span>
        );
      case 'RECORDED':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-mono tabular-nums font-medium bg-surface-200 border border-border text-text-muted">
            Recorded
          </span>
        );
      case 'FAILED_RECORDING':
      default:
        return (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRetryUpload(item.segmentId);
            }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-mono tabular-nums font-medium bg-red-500/15 border border-red-500/30 text-red-300 hover:bg-red-500/25 active:scale-[0.98] transition-all duration-150"
          >
            Retry Upload
          </button>
        );
    }
  };

  return (
    <div className="w-full h-full flex flex-col bg-studio text-text-main overflow-hidden select-none">
      
      {/* SCREEN HEADER - Layer 0 with bottom border */}
      <div className="shrink-0 px-5 pt-4 pb-3 border-b border-border bg-studio">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-base font-semibold tracking-tight text-text-main flex items-center gap-2">
              <Film size={18} className="text-primary" />
              Sessions &amp; Uploads
            </h1>
            <p className="text-[11px] font-mono tabular-nums text-text-muted mt-0.5">
              {recordings.length}&nbsp;{recordings.length === 1 ? 'segment' : 'segments'} · {formatBytes(totalBytes)}&nbsp;storage
            </p>
          </div>

          {/* YouTube Connection Indicator */}
          <button
            onClick={onNavigateToSettings}
            className="flex items-center gap-2 px-3 py-2 min-h-[44px] rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-[11px] font-mono tabular-nums transition-all duration-150 active:scale-[0.98]"
          >
            <span className={`w-2 h-2 rounded-full ${isYouTubeConnected ? 'bg-emerald-400 shadow-[0_0_8px_#34D399]' : 'bg-red-400'}`} />
            <span className="text-text-muted">{isYouTubeConnected ? 'YouTube' : 'No YouTube'}</span>
          </button>
        </div>

        {/* FILTER TABS - 44px Hitboxes */}
        {recordings.length > 0 && (
          <div className="flex items-center gap-2 mt-3 pt-2 border-t border-border/60">
            <button
              onClick={() => setFilter('all')}
              className={`min-h-[44px] px-3 py-2 rounded-lg text-xs font-mono tabular-nums transition-all duration-150 active:scale-[0.98] ${
                filter === 'all' 
                  ? 'bg-primary text-white font-semibold shadow-hero-glow' 
                  : 'text-text-muted hover:text-text-main bg-surface-100 border border-border'
              }`}
            >
              All ({recordings.length})
            </button>
            <button
              onClick={() => setFilter('queued')}
              className={`min-h-[44px] px-3 py-2 rounded-lg text-xs font-mono tabular-nums transition-all duration-150 active:scale-[0.98] ${
                filter === 'queued' 
                  ? 'bg-primary text-white font-semibold shadow-hero-glow' 
                  : 'text-text-muted hover:text-text-main bg-surface-100 border border-border'
              }`}
            >
              Queued ({recordings.filter(r => r.status !== 'UPLOADED_TO_YOUTUBE').length})
            </button>
            <button
              onClick={() => setFilter('uploaded')}
              className={`min-h-[44px] px-3 py-2 rounded-lg text-xs font-mono tabular-nums transition-all duration-150 active:scale-[0.98] ${
                filter === 'uploaded' 
                  ? 'bg-primary text-white font-semibold shadow-hero-glow' 
                  : 'text-text-muted hover:text-text-main bg-surface-100 border border-border'
              }`}
            >
              Uploaded ({recordings.filter(r => r.status === 'UPLOADED_TO_YOUTUBE').length})
            </button>
          </div>
        )}
      </div>

      {/* YOUTUBE WARNING BANNER IF DISCONNECTED */}
      {!isYouTubeConnected && recordings.length > 0 && (
        <div 
          onClick={onNavigateToSettings}
          className="mx-4 mt-3 p-3.5 min-h-[44px] rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-between cursor-pointer hover:bg-amber-500/15 transition-all duration-150 active:scale-[0.98]"
        >
          <div className="flex items-center gap-2.5 text-amber-300">
            <AlertCircle size={15} className="shrink-0" />
            <span className="text-[11px] font-mono">YouTube not connected. Segments queued on device.</span>
          </div>
          <span className="text-xs font-mono text-amber-400 font-semibold underline shrink-0">Connect</span>
        </div>
      )}

      {/* CONTENT SCROLL AREA - Single-direction vertical flow */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        
        {filteredRecordings.length === 0 ? (
          /* HONEST EMPTY STATE */
          <div className="h-full min-h-[260px] flex flex-col items-center justify-center text-center p-8 rounded-xl bg-surface-100 border border-border">
            <div className="w-14 h-14 rounded-2xl bg-surface-200 border border-border flex items-center justify-center mb-4">
              <Film className="w-7 h-7 text-text-muted" />
            </div>
            <h2 className="text-sm font-semibold text-text-main">No recordings yet</h2>
            <p className="text-xs text-text-muted mt-1 max-w-[240px] leading-relaxed">
              FHD segments recorded on the Record tab will appear here with live playback and upload progress.
            </p>
            <div className="mt-5 px-3 py-1.5 rounded-md bg-surface-200 border border-border text-[11px] font-mono tabular-nums text-text-muted">
              Location: DCIM/Dispatch (1080p · 60&nbsp;s)
            </div>
          </div>
        ) : (
          /* RECORDING CARDS WITH EMBEDDED VIDEO VIEWPORT */
          filteredRecordings.map((item, index) => {
            const isExpanded = expandedId === item.segmentId;
            const hasVideo = !!item.videoUrl;

            return (
              <div
                key={item.segmentId}
                className="rounded-xl bg-surface-100 border border-border overflow-hidden transition-all duration-150 hover:border-border-strong shadow-card"
              >
                {/* VIDEO VIEWPORT HERO */}
                <div className="relative bg-black aspect-video flex items-center justify-center overflow-hidden group">
                  {hasVideo ? (
                    <video
                      src={item.videoUrl!.startsWith('http') ? item.videoUrl : apiUrl(item.videoUrl!)}
                      controls
                      playsInline
                      preload="metadata"
                      className="w-full h-full object-contain"
                    />
                  ) : item.youtubeVideoId ? (
                    <iframe
                      src={`https://www.youtube-nocookie.com/embed/${item.youtubeVideoId}`}
                      className="w-full h-full border-0"
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                      allowFullScreen
                      title={item.filename}
                    />
                  ) : (
                    /* PLACEHOLDER PREVIEW IF HARDWARE FILE */
                    <div className="w-full h-full flex flex-col items-center justify-center bg-surface-200 p-4 text-center">
                      <div className="w-12 h-12 rounded-full bg-surface-300 border border-border flex items-center justify-center text-text-main mb-2">
                        <Video size={20} className="text-primary" />
                      </div>
                      <span className="text-xs font-mono text-text-main font-medium">
                        POCO C65 Hardware Capture
                      </span>
                      <span className="text-[10px] font-mono tabular-nums text-text-muted mt-0.5">
                        FHD 1080p · Saved in DCIM/Dispatch
                      </span>
                    </div>
                  )}

                  {/* OVERLAY BADGES (Duration & Quality) */}
                  <div className="absolute top-2.5 left-2.5 pointer-events-none flex items-center gap-1.5">
                    <span className="px-2 py-0.5 rounded-md bg-black/75 backdrop-blur border border-border text-[10px] font-mono tabular-nums font-semibold text-white">
                      {formatDuration(item.durationSeconds)}
                    </span>
                    <span className="px-1.5 py-0.5 rounded-md bg-black/75 backdrop-blur border border-border text-[9px] font-mono text-blue-300">
                      1080P
                    </span>
                  </div>

                  {/* FULLSCREEN PREVIEW BUTTON */}
                  {hasVideo && (
                    <button
                      onClick={() => setModalVideo({ url: item.videoUrl!.startsWith('http') ? item.videoUrl! : apiUrl(item.videoUrl!), title: item.filename })}
                      className="absolute top-2.5 right-2.5 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg bg-black/70 hover:bg-black/90 border border-border text-white transition-all duration-150 active:scale-[0.98]"
                      title="Fullscreen Preview"
                    >
                      <Maximize2 size={15} />
                    </button>
                  )}
                </div>

                {/* METADATA & ACTIONS PANEL */}
                <div className="p-3.5 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-semibold text-text-main truncate">
                          {item.filename}
                        </span>
                      </div>

                      <div className="flex items-center gap-2 mt-1 text-[11px] font-mono tabular-nums text-text-muted">
                        <span>{formatTimeAgo(item.createdAt)}</span>
                        <span>·</span>
                        <span className="text-text-main font-medium">{formatBytes(item.fileSizeBytes)}</span>
                        <span>·</span>
                        <span>Seq&nbsp;#{item.sequenceNumber || index + 1}</span>
                      </div>
                    </div>

                    <div className="shrink-0 flex flex-col items-end gap-1">
                      {renderStatusBadge(item)}
                    </div>
                  </div>

                  {/* ACTION BAR - 44px Minimum Touch Targets */}
                  <div className="pt-2 border-t border-border flex items-center justify-between text-xs font-mono">
                    <div className="flex items-center gap-2">
                      {hasVideo && (
                        <a
                          href={item.videoUrl!.startsWith('http') ? item.videoUrl : apiUrl(item.videoUrl!)}
                          download={item.filename}
                          className="flex items-center gap-1.5 px-3 py-2 min-h-[44px] rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-xs text-text-muted hover:text-text-main transition-all duration-150 active:scale-[0.98]"
                        >
                          <Download size={13} />
                          <span>Save MP4</span>
                        </a>
                      )}

                      {item.youtubeVideoId && (
                        <a
                          href={`https://youtu.be/${item.youtubeVideoId}`}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1.5 px-3 py-2 min-h-[44px] rounded-lg bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-xs text-red-300 transition-all duration-150 active:scale-[0.98]"
                        >
                          <ExternalLink size={13} />
                          <span>YouTube</span>
                        </a>
                      )}
                    </div>

                    {/* EXPAND METADATA BUTTON */}
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : item.segmentId)}
                      className="min-h-[44px] px-3 py-2 rounded-lg text-text-muted hover:text-text-main flex items-center gap-1.5 text-xs font-mono tabular-nums transition-all duration-150 active:scale-[0.98]"
                    >
                      <span>{isExpanded ? 'Hide info' : 'Details'}</span>
                      {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                    </button>
                  </div>

                  {/* EXPANDED TECHNICAL DETAILS DRAWER - Layer 2 Recessed */}
                  {isExpanded && (
                    <div className="mt-2 p-3 rounded-lg bg-surface-200 border border-border text-[11px] font-mono tabular-nums space-y-1.5 text-text-muted">
                      <div className="flex justify-between">
                        <span>Segment ID:</span>
                        <span className="text-text-main font-medium">{item.segmentId}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Session ID:</span>
                        <span className="text-text-main font-medium">{item.sessionId}</span>
                      </div>
                      <div className="flex justify-between truncate">
                        <span>SHA256:</span>
                        <span className="text-text-muted truncate max-w-[200px]">
                          {item.sha256Hash || 'pending'}
                        </span>
                      </div>
                      <div className="text-text-muted break-all pt-1 border-t border-border/40">
                        Path: {item.filepath}
                      </div>
                    </div>
                  )}
                </div>

              </div>
            );
          })
        )}

      </div>

      {/* FULLSCREEN VIDEO MODAL */}
      {modalVideo && (
        <div 
          onClick={() => setModalVideo(null)}
          className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-md flex flex-col items-center justify-center p-4 animate-in fade-in"
        >
          <div 
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-3xl bg-surface-100 border border-border rounded-2xl overflow-hidden shadow-2xl"
          >
            <div className="flex items-center justify-between p-3.5 border-b border-border">
              <span className="text-xs font-mono font-semibold text-text-main truncate pr-3">
                {modalVideo.title}
              </span>
              <button 
                onClick={() => setModalVideo(null)}
                className="w-11 h-11 flex items-center justify-center rounded-lg text-text-muted hover:text-text-main hover:bg-surface-200 transition-colors duration-150 active:scale-[0.98]"
              >
                <X size={18} />
              </button>
            </div>
            <div className="aspect-video bg-black flex items-center justify-center">
              <video 
                src={modalVideo.url} 
                controls 
                autoPlay 
                className="w-full h-full object-contain"
              />
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
