import React, { useState, useEffect } from 'react';
import { 
  Sparkles, 
  CheckCircle2, 
  Server, 
  Film, 
  RefreshCw,
  Eye,
  Cpu
} from 'lucide-react';

interface ProcessingScreenProps {
  pcHost: string;
}

interface PipelineJob {
  segmentId: string;
  youtubeVideoId: string;
  currentStage: 'RECORDED' | 'YOUTUBE_UPLOADED' | 'PC_DISCOVERED' | 'TRANSCRIBING' | 'ANALYZING' | 'CLIPS_READY';
  title?: string;
  clipsCount?: number;
  duration?: number;
  updatedAt: number;
}

export const ProcessingScreen: React.FC<ProcessingScreenProps> = ({ pcHost }) => {
  const [showSamplePreview, setShowSamplePreview] = useState<boolean>(false);
  const [serverHealth, setServerHealth] = useState<{ online: boolean; uptime?: number; pendingJobs?: number }>({
    online: false
  });
  const [isCheckingServer, setIsCheckingServer] = useState(false);

  // Sample job for UI inspection
  const sampleJob: PipelineJob = {
    segmentId: 'seg_20261008_001',
    youtubeVideoId: 'dQw4w9WgXcQ',
    currentStage: 'CLIPS_READY',
    title: 'How to build autonomous AI systems without breaking production',
    clipsCount: 3,
    duration: 60,
    updatedAt: Date.now() - 1000 * 60 * 3
  };

  const activeJobs: PipelineJob[] = showSamplePreview ? [sampleJob] : [];

  const checkDaemonHealth = async () => {
    setIsCheckingServer(true);
    try {
      const res = await fetch(`http://${pcHost}/api/status`, { mode: 'cors' });
      if (res.ok) {
        const data = await res.json();
        setServerHealth({
          online: true,
          uptime: data.uptime_seconds,
          pendingJobs: data.pending_jobs || 0
        });
      } else {
        setServerHealth({ online: false });
      }
    } catch {
      setServerHealth({ online: false });
    } finally {
      setIsCheckingServer(false);
    }
  };

  useEffect(() => {
    checkDaemonHealth();
  }, [pcHost]);

  const stages = [
    { key: 'RECORDED', label: 'Phone Capture', desc: '1080p FHD segment finalized on POCO C65' },
    { key: 'YOUTUBE_UPLOADED', label: 'YouTube Cloud Inbox', desc: 'Uploaded as private [DISPATCH] stream' },
    { key: 'PC_DISCOVERED', label: 'PC Ingestion', desc: 'Discovered & downloaded by PC daemon' },
    { key: 'TRANSCRIBING', label: 'Whisper Transcription', desc: 'Word-level timestamps & Roman Hinglish STT' },
    { key: 'ANALYZING', label: 'Gemini Analysis', desc: 'Hook detection & viral segment isolation' },
    { key: 'CLIPS_READY', label: 'Clips Defined', desc: '9:16 vertical shorts with animated subtitles' },
  ];

  return (
    <div className="w-full h-full flex flex-col bg-studio text-text-main overflow-hidden select-none">
      
      {/* SCREEN HEADER */}
      <div className="shrink-0 px-5 pt-4 pb-3 border-b border-border bg-studio flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold tracking-tight text-text-main flex items-center gap-2">
            <Sparkles size={18} className="text-primary" />
            AI Processing
          </h1>
          <p className="text-[11px] font-mono text-text-muted mt-0.5">
            Cloud inbox &amp; PC highlight pipeline
          </p>
        </div>

        {/* Web Iteration Preview Toggle - 44px Hitbox */}
        <button
          onClick={() => setShowSamplePreview(!showSamplePreview)}
          className={`flex items-center gap-2 px-3 py-2 min-h-[44px] rounded-lg text-xs font-mono tabular-nums transition-all duration-150 active:scale-[0.98] border ${
            showSamplePreview 
              ? 'bg-primary/15 border-primary/40 text-blue-300 font-semibold shadow-hero-glow' 
              : 'bg-surface-200 border-border text-text-muted hover:text-text-main'
          }`}
          title="Toggle sample pipeline execution for UI review on PC"
        >
          <Eye size={14} />
          <span>{showSamplePreview ? 'Sample View' : 'Live State'}</span>
        </button>
      </div>

      {/* CONTENT SCROLL AREA */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        
        {/* PC DAEMON CONNECTION BADGE (Layer 1 Surface) */}
        <div className="rounded-xl bg-surface-100 border border-border p-3.5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center border ${
              serverHealth.online 
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' 
                : 'bg-red-500/10 border-red-500/30 text-red-400'
            }`}>
              <Server size={18} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-text-main">Dispatch PC Daemon</span>
                <span className={`w-2 h-2 rounded-full ${
                  serverHealth.online ? 'bg-emerald-400 shadow-[0_0_8px_#34D399]' : 'bg-red-500'
                }`} />
              </div>
              <span className="text-[11px] font-mono tabular-nums text-text-muted block mt-0.5">
                {serverHealth.online 
                  ? `Host: ${pcHost} · Online` 
                  : `Host: ${pcHost} · Offline / Polling`}
              </span>
            </div>
          </div>

          <button
            onClick={checkDaemonHealth}
            disabled={isCheckingServer}
            className="w-11 h-11 flex items-center justify-center rounded-lg bg-surface-200 hover:bg-surface-300 border border-border text-text-muted hover:text-text-main transition-all duration-150 active:scale-[0.98]"
            title="Refresh status"
          >
            <RefreshCw size={15} className={isCheckingServer ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* ACTIVE PIPELINE JOBS */}
        {activeJobs.length === 0 ? (
          /* EMPTY STATE */
          <div className="h-[280px] flex flex-col items-center justify-center text-center p-6 rounded-xl bg-surface-100 border border-border">
            <div className="w-12 h-12 rounded-2xl bg-surface-200 border border-border flex items-center justify-center mb-3">
              <Cpu className="w-6 h-6 text-text-muted" />
            </div>
            <h2 className="text-sm font-semibold text-text-main">No active processing</h2>
            <p className="text-xs text-text-muted mt-1 max-w-[240px] leading-relaxed">
              When segments finish uploading to YouTube, the PC daemon ingests them to generate transcripts and clips.
            </p>
          </div>
        ) : (
          /* PIPELINE ACTIVE FLOW */
          activeJobs.map((job) => (
            <div 
              key={job.segmentId} 
              className="rounded-xl bg-surface-100 border border-border p-4 space-y-4 shadow-card"
            >
              {/* Job Header */}
              <div className="border-b border-border pb-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono tabular-nums font-bold text-primary">
                    {job.segmentId}
                  </span>
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono tabular-nums bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 font-semibold">
                    CLIPS READY
                  </span>
                </div>
                {job.title && (
                  <p className="text-xs font-medium text-text-main mt-1 line-clamp-1">
                    {job.title}
                  </p>
                )}
                <div className="flex items-center gap-2 mt-1.5 text-[11px] font-mono tabular-nums text-text-muted">
                  <span>YouTube ID: {job.youtubeVideoId}</span>
                  <span>·</span>
                  <span>Duration: {job.duration}&nbsp;s</span>
                </div>
              </div>

              {/* Stage Progress Stepper with Continuity Rail */}
              <div className="space-y-3">
                {stages.map((st, idx) => {
                  return (
                    <div key={st.key} className="flex items-start gap-3">
                      <div className="flex flex-col items-center">
                        <div className="w-5 h-5 rounded-full bg-emerald-500/20 border border-emerald-500/50 flex items-center justify-center text-emerald-400">
                          <CheckCircle2 size={12} />
                        </div>
                        {idx < stages.length - 1 && (
                          <div className="w-[1.5px] h-6 bg-emerald-500/30 my-0.5" />
                        )}
                      </div>

                      <div className="flex-1 pb-1">
                        <span className="text-xs font-mono font-medium text-text-main block">
                          {st.label}
                        </span>
                        <span className="text-[10px] font-mono text-text-muted block mt-0.5">
                          {st.desc}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Generated Clips Card - Layer 2 Recessed */}
              <div className="p-3.5 rounded-lg bg-surface-200 border border-border space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono font-semibold text-text-main flex items-center gap-2">
                    <Film size={14} className="text-primary" />
                    AI Highlights Generated (3)
                  </span>
                  <span className="text-[11px] font-mono tabular-nums text-emerald-400 font-medium">9:16 Shorts Ready</span>
                </div>

                <div className="space-y-1.5 pt-1">
                  <div className="p-2.5 rounded-lg bg-surface-100/60 border border-border/40 flex items-center justify-between text-xs font-mono tabular-nums text-text-main">
                    <span className="truncate pr-2">Clip 1: Hook on autonomous loops</span>
                    <span className="text-text-muted shrink-0">0:24</span>
                  </div>
                  <div className="p-2.5 rounded-lg bg-surface-100/60 border border-border/40 flex items-center justify-between text-xs font-mono tabular-nums text-text-main">
                    <span className="truncate pr-2">Clip 2: Why raw data matters</span>
                    <span className="text-text-muted shrink-0">0:18</span>
                  </div>
                </div>
              </div>

            </div>
          ))
        )}

      </div>
    </div>
  );
};
